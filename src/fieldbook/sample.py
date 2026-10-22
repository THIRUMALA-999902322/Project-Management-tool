"""Synthetic sample files for the demo seed and the tests: job-site photos and small PDFs.

Photos are drawn from a seeded random layout so each one is visually distinct (the
duplicate-photo check compares perceptual hashes, so near-identical drawings would trip it).
"""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw


def make_photo(seed: int, label: str = "", *, quality: int = 84, size: tuple[int, int] = (640, 480)) -> bytes:
    rng = random.Random(seed)
    w, h = size
    img = Image.new("RGB", size, (150 + rng.randint(0, 60), 190 + rng.randint(0, 40), 225))
    d = ImageDraw.Draw(img)
    horizon = rng.randint(120, 300)
    d.rectangle([0, horizon, w, h], fill=(rng.randint(140, 190), rng.randint(110, 150), rng.randint(70, 100)))
    for _ in range(rng.randint(6, 11)):  # spoil piles, cones, trucks, shadows
        x, y = rng.randint(-60, w), rng.randint(horizon - 80, h)
        bw, bh = rng.randint(30, 220), rng.randint(20, 160)
        color = tuple(rng.randint(20, 240) for _ in range(3))
        (d.ellipse if rng.random() < 0.5 else d.rectangle)([x, y, x + bw, y + bh], fill=color)
    tx = rng.randint(40, w - 260)
    d.polygon([(tx, h), (tx + 60, horizon + 40), (tx + 200, horizon + 40), (tx + 260, h)], fill=(90, 70, 50))
    d.rectangle([tx + 40, h - 70, tx + 220, h - 46], fill=(58, 115, 181))
    if label:
        sx = rng.randint(60, w - 140)
        d.rectangle([sx + 36, horizon - 10, sx + 42, horizon + 90], fill=(201, 161, 91))
        d.rectangle([sx, horizon - 40, sx + 90, horizon - 10], fill=(255, 255, 255), outline=(20, 33, 61), width=2)
        d.text((sx + 10, horizon - 34), label, fill=(20, 33, 61))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def reencode(jpeg: bytes, quality: int = 70) -> bytes:
    """Same picture, different bytes: what a re-sent or re-saved old photo looks like."""
    out = io.BytesIO()
    Image.open(io.BytesIO(jpeg)).save(out, format="JPEG", quality=quality)
    return out.getvalue()


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal text PDF (Helvetica), enough for page counts and text extraction."""
    out = io.BytesIO()
    offsets: list[tuple[int, int]] = []

    def obj(n: int, body: bytes) -> None:
        offsets.append((n, out.tell()))
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")

    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    page_ids = [4 + i * 2 for i in range(len(pages))]
    obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(pages)} >>".encode())
    obj(3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for pid, lines in zip(page_ids, pages, strict=True):
        body = "BT /F1 11 Tf 50 750 Td 15 TL " + " ".join(f"({_pdf_escape(line)}) Tj T*" for line in lines) + " ET"
        data = body.encode("latin-1", "replace")
        obj(
            pid,
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {pid + 1} 0 R >>".encode(),
        )
        obj(pid + 1, f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")
    xref = out.tell()
    count = 3 + 2 * len(pages)
    out.write(f"xref\n0 {count + 1}\n0000000000 65535 f \n".encode())
    for _n, off in sorted(offsets):
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {count + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
