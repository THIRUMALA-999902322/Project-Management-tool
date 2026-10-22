"""Content-addressed file storage plus the little bit of file understanding code can do on its own.

- Same bytes are stored once (sha256), which is also exact-duplicate detection.
- Photos get a 64-bit difference hash, so a re-saved or re-compressed copy of an old photo is
  still recognised (Hamming distance <= DUP_DISTANCE).
- Photo EXIF gives capture time and GPS; PDFs give page count and text.
Local disk for the MVP; swap `save_blob` / `read_blob` for S3 later.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.config import Settings
from fieldbook.models import Blob

DUP_DISTANCE = 6


def save_blob(db: Session, settings: Settings, data: bytes, mime: str) -> Blob:
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(Blob).where(Blob.sha256 == digest))
    if existing:
        return existing
    rel = Path(digest[:2]) / digest
    path = settings.blob_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    blob = Blob(sha256=digest, size=len(data), mime=mime or "application/octet-stream", path=str(rel))
    if blob.mime.startswith("image/"):
        blob.dhash = image_dhash(data)
    elif blob.mime == "application/pdf":
        blob.pages = pdf_pages(data)
    db.add(blob)
    db.flush()
    return blob


def read_blob(settings: Settings, blob: Blob) -> bytes:
    return (settings.blob_dir / blob.path).read_bytes()


def blob_file(settings: Settings, blob: Blob) -> Path:
    return settings.blob_dir / blob.path


def image_dhash(data: bytes) -> str | None:
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    except Exception:
        return None
    px = list(getattr(img, "get_flattened_data", img.getdata)())
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = px[row * 9 + col], px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _gps_to_deg(values, ref) -> float | None:
    try:
        d, m, s = (float(v) for v in values)
    except Exception:
        return None
    deg = d + m / 60 + s / 3600
    return -deg if ref in ("S", "W") else deg


def photo_meta(data: bytes) -> tuple[datetime | None, float | None, float | None]:
    """Capture time and GPS from EXIF, when the phone recorded them."""
    try:
        exif = Image.open(io.BytesIO(data)).getexif()
    except Exception:
        return None, None, None
    taken = None
    raw = exif.get_ifd(ExifTags.IFD.Exif).get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
    if raw:
        try:
            taken = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            taken = None
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    lat = lon = None
    if gps:
        lat = _gps_to_deg(gps.get(2), gps.get(1)) if gps.get(2) else None
        lon = _gps_to_deg(gps.get(4), gps.get(3)) if gps.get(4) else None
    return taken, lat, lon


def downscaled_jpeg(data: bytes, max_side: int = 1280) -> bytes | None:
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:
        return None
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82)
    return out.getvalue()


def pdf_pages(data: bytes) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return None


def pdf_text_pages(data: bytes) -> list[str]:
    """Text per page. Scanned sheets come back empty; the Assistant then reads the PDF itself."""
    try:
        from pypdf import PdfReader

        return [(page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages]
    except Exception:
        return []
