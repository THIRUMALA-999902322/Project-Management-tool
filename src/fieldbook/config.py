"""Runtime settings, read once from the environment.

Every knob that touches the Claude API (model id, effort, max tokens) lives here so it can
change without touching the pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    # "auto": use Claude when credentials are present, else the rules engine.
    # "claude": always use Claude. "rules": never call Claude (tests, offline demos).
    assistant_mode: str = "auto"
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"
    claude_max_tokens: int = 16000
    # Max photos sent to Claude per drafting call; each is downscaled first.
    claude_max_images: int = 6
    # Treat every browser without a session as the owner. Local demos only.
    dev_autologin: bool = False
    public_url: str = "http://localhost:8000"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def use_claude(self) -> bool:
        if self.assistant_mode == "claude":
            return True
        if self.assistant_mode == "rules":
            return False
        return bool((os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip())


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def load_settings(**overrides) -> Settings:
    data_dir = Path(overrides.pop("data_dir", None) or os.environ.get("FIELDBOOK_DATA_DIR", "data")).resolve()
    database_url = overrides.pop("database_url", None) or os.environ.get(
        "FIELDBOOK_DATABASE_URL", f"sqlite:///{data_dir / 'fieldbook.db'}"
    )
    values = {
        "data_dir": data_dir,
        "database_url": database_url,
        "assistant_mode": _env("FIELDBOOK_ASSISTANT", "auto").lower(),
        "claude_model": _env("FIELDBOOK_CLAUDE_MODEL", "claude-opus-5-5"),
        "claude_effort": _env("FIELDBOOK_CLAUDE_EFFORT", "medium").lower(),
        "claude_max_tokens": int(_env("FIELDBOOK_CLAUDE_MAX_TOKENS", "16000")),
        "dev_autologin": _bool("FIELDBOOK_DEV_AUTOLOGIN"),
        "public_url": _env("FIELDBOOK_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
    }
    values.update(overrides)
    settings = Settings(**values)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.blob_dir.mkdir(parents=True, exist_ok=True)
    return settings
