"""The only module that calls the Claude API.

Model id, effort and max tokens come from Settings, so they change without touching the
pipeline. Every call:
- asks for structured JSON (output_config.format) validated against a pydantic model;
- opts into the server-side refusal fallback (`fallbacks: "default"`), so a declined request
  is re-run on Anthropic's recommended fallback model instead of failing;
- logs token usage to `usage_log`;
- returns None on any failure, so the caller falls back to the rules engine. The Assistant
  degrading to "rules" is always better than a stuck report.
"""

from __future__ import annotations

import base64
import copy
import logging
from functools import lru_cache
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from fieldbook.config import Settings
from fieldbook.models import UsageLog

log = logging.getLogger("fieldbook.assistant")

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@lru_cache(maxsize=1)
def _sdk() -> anthropic.Anthropic:
    # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
    return anthropic.Anthropic(max_retries=2, timeout=120.0)


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema -> the strict subset structured outputs accept.

    Inlines $refs, drops titles/defaults, and marks every object closed with all keys required
    (optional values are expressed as `anyOf [..., null]`).
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def fix(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return fix(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]))
            out = {k: fix(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [fix(x) for x in node]
        return node

    return fix(schema)


def image_block(jpeg: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(jpeg).decode()},
    }


def pdf_block(data: bytes, title: str | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(data).decode()},
    }
    if title:
        block["title"] = title
    return block


def _log_usage(db: Session, settings: Settings, purpose: str, *, ok: bool, resp: Any = None, note: str | None = None):
    usage = getattr(resp, "usage", None)
    db.add(
        UsageLog(
            purpose=purpose,
            model=getattr(resp, "model", None) or settings.claude_model,
            ok=ok,
            stop_reason=getattr(resp, "stop_reason", None),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            request_id=getattr(resp, "_request_id", None),
            note=note,
        )
    )
    db.flush()


def structured[T: BaseModel](
    db: Session,
    settings: Settings,
    *,
    purpose: str,
    system: str,
    content: list[dict[str, Any]],
    output: type[T],
    effort: str | None = None,
) -> T | None:
    """One Claude call that must come back as `output`. None means: use the rules engine."""
    if not settings.use_claude:
        return None
    try:
        resp = _sdk().beta.messages.create(
            model=settings.claude_model,
            max_tokens=settings.claude_max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=system,
            output_config={
                "effort": effort or settings.claude_effort,
                "format": {"type": "json_schema", "schema": strict_schema(output)},
            },
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.BadRequestError as e:
        _log_usage(db, settings, purpose, ok=False, note=f"bad request: {e.message}")
        log.warning("Claude rejected the %s request: %s", purpose, e.message)
        return None
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        _log_usage(db, settings, purpose, ok=False, note=f"auth: {e.message}")
        log.error("Claude credentials were refused; using the rules engine. %s", e.message)
        return None
    except anthropic.RateLimitError as e:
        _log_usage(db, settings, purpose, ok=False, note="rate limited")
        log.warning("Claude rate limit on %s after SDK retries: %s", purpose, e.message)
        return None
    except anthropic.APIStatusError as e:
        _log_usage(db, settings, purpose, ok=False, note=f"status {e.status_code}")
        log.warning("Claude API error %s on %s", e.status_code, purpose)
        return None
    except anthropic.APIConnectionError:
        _log_usage(db, settings, purpose, ok=False, note="connection error")
        log.warning("Could not reach the Claude API for %s", purpose)
        return None

    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        _log_usage(db, settings, purpose, ok=False, resp=resp, note=f"refusal: {getattr(details, 'category', None)}")
        return None
    if resp.stop_reason == "max_tokens":
        _log_usage(db, settings, purpose, ok=False, resp=resp, note="hit max_tokens")
        return None
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    try:
        parsed = output.model_validate_json(text or "")
    except ValidationError as e:
        _log_usage(db, settings, purpose, ok=False, resp=resp, note=f"invalid JSON: {e.error_count()} errors")
        return None
    _log_usage(db, settings, purpose, ok=True, resp=resp)
    return parsed
