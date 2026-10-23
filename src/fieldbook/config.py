"""Runtime settings, read once from the environment.

Every knob that touches the language model (server, model names, timeouts, how much text to
send) lives here, so it can change without touching the pipeline.
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


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    # "auto": use the model when FIELDBOOK_LLM_MODEL is set, else the rules engine.
    # "llm": always try the model. "rules": never call a model (tests, offline demos).
    assistant_mode: str = "auto"
    # Any OpenAI-compatible server: Ollama (default), vLLM, LM Studio, llama.cpp, LocalAI.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = ""
    # Model used when photos are attached; defaults to llm_model. Needs a vision model.
    llm_vision_model: str = ""
    llm_api_key: str = ""
    llm_timeout: float = 180.0
    # Text budget per request. Local models often run with 8k context; ~4 characters per token.
    llm_max_input_chars: int = 12000
    llm_max_images: int = 4
    # "schema" asks the server to enforce the JSON schema; "object" only asks for JSON.
    # Servers that reject "schema" are switched to "object" automatically.
    llm_json_mode: str = "schema"
    # Treat every browser without a session as the owner. Local demos only.
    dev_autologin: bool = False
    public_url: str = "http://localhost:8000"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def use_llm(self) -> bool:
        return self.assistant_mode != "rules" and bool(self.llm_model)

    @property
    def vision_model(self) -> str:
        return self.llm_vision_model or self.llm_model

    @property
    def assistant_label(self) -> str:
        return f"Local model · {self.llm_model}" if self.use_llm else "Rules engine"


def load_settings(**overrides) -> Settings:
    data_dir = Path(overrides.pop("data_dir", None) or _env("FIELDBOOK_DATA_DIR", "data")).resolve()
    database_url = overrides.pop("database_url", None) or _env(
        "FIELDBOOK_DATABASE_URL", f"sqlite:///{data_dir / 'fieldbook.db'}"
    )
    values = {
        "data_dir": data_dir,
        "database_url": database_url,
        "assistant_mode": _env("FIELDBOOK_ASSISTANT", "auto").lower(),
        "llm_base_url": _env("FIELDBOOK_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
        "llm_model": _env("FIELDBOOK_LLM_MODEL", ""),
        "llm_vision_model": _env("FIELDBOOK_LLM_VISION_MODEL", ""),
        "llm_api_key": _env("FIELDBOOK_LLM_API_KEY", ""),
        "llm_timeout": float(_env("FIELDBOOK_LLM_TIMEOUT", "180")),
        "llm_max_input_chars": int(_env("FIELDBOOK_LLM_MAX_INPUT_CHARS", "12000")),
        "llm_max_images": int(_env("FIELDBOOK_LLM_MAX_IMAGES", "4")),
        "llm_json_mode": _env("FIELDBOOK_LLM_JSON_MODE", "schema").lower(),
        "dev_autologin": _bool("FIELDBOOK_DEV_AUTOLOGIN"),
        "public_url": _env("FIELDBOOK_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
    }
    values.update(overrides)
    settings = Settings(**values)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.blob_dir.mkdir(parents=True, exist_ok=True)
    return settings
