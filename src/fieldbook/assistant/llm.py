"""The only module that talks to a language model.

Fieldbook runs on open-weight models you host yourself. Any server that speaks the
OpenAI-compatible Chat Completions API works: Ollama (the default, http://localhost:11434/v1),
vLLM, LM Studio, llama.cpp's llama-server, LocalAI. There is no per-token bill.

Every call:
- asks for one JSON object matching a pydantic model. The server enforces the schema when it
  can (`response_format: json_schema`); servers that reject that get plain JSON mode, and the
  schema is always in the prompt as well, because small models follow it better that way;
- validates the reply, and on a bad reply asks once more with the validation error;
- copes with reasoning models (strips <think>…</think>) and fenced ```json``` replies;
- drops the photos and retries if the model turns out not to support images;
- logs tokens and failures to `usage_log`;
- returns None on any failure, so the caller falls back to the rules engine. A slow or
  missing model never blocks a report.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from fieldbook.config import Settings
from fieldbook.models import UsageLog

log = logging.getLogger("fieldbook.assistant")

_json_mode_override: dict[str, str] = {}  # base_url -> "object" once a server rejected "schema"
_no_vision: set[str] = set()  # models that rejected image input
_transport: httpx.BaseTransport | None = None  # tests swap in a fake server


def _client(settings: Settings) -> httpx.Client:
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    return httpx.Client(transport=_transport, headers=headers, timeout=settings.llm_timeout)


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema, inlined and closed: no $refs, every key required, no extra keys."""
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


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def image_part(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Cut text to a budget. Callers say so when something was cut; nothing is dropped silently."""
    if len(text) <= limit:
        return text, False
    return text[:limit].rsplit("\n", 1)[0] + "\n[…cut to fit the model's context]", True


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def extract_json(text: str) -> str:
    text = _THINK.sub("", text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return text


def _log(db: Session, purpose: str, model: str, *, ok: bool, data: dict | None = None, note: str | None = None):
    usage = (data or {}).get("usage") or {}
    db.add(
        UsageLog(
            purpose=purpose,
            model=model,
            ok=ok,
            stop_reason=((data or {}).get("choices") or [{}])[0].get("finish_reason"),
            input_tokens=usage.get("prompt_tokens") or 0,
            output_tokens=usage.get("completion_tokens") or 0,
            request_id=(data or {}).get("id"),
            note=note,
        )
    )
    db.flush()


def _user_content(parts: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    # Text-only requests go as a plain string: every server and model accepts that.
    if all(p["type"] == "text" for p in parts):
        return "\n\n".join(p["text"] for p in parts)
    return parts


def structured[T: BaseModel](
    db: Session,
    settings: Settings,
    *,
    purpose: str,
    system: str,
    content: list[dict[str, Any]],
    output: type[T],
) -> T | None:
    """One model call that must come back as `output`. None means: use the rules engine."""
    if not settings.use_llm:
        return None
    has_images = any(p["type"] == "image_url" for p in content)
    model = settings.vision_model if has_images else settings.llm_model
    if has_images and model in _no_vision:
        content, has_images, model = [p for p in content if p["type"] == "text"], False, settings.llm_model
    schema = strict_schema(output)
    mode = _json_mode_override.get(settings.llm_base_url, settings.llm_json_mode)
    instructions = (
        f"{system}\n\nReply with one JSON object and nothing else. It must match this JSON schema:\n"
        f"{json.dumps(schema, separators=(',', ':'))}"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": _user_content(content)},
    ]
    url = f"{settings.llm_base_url}/chat/completions"
    retried_bad_json = False
    with _client(settings) as http:
        for _ in range(4):
            payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0, "stream": False}
            payload["response_format"] = (
                {"type": "json_schema", "json_schema": {"name": output.__name__, "schema": schema, "strict": True}}
                if mode == "schema"
                else {"type": "json_object"}
            )
            try:
                resp = http.post(url, json=payload)
            except httpx.TimeoutException:
                _log(db, purpose, model, ok=False, note=f"timed out after {settings.llm_timeout:.0f}s")
                log.warning("The model at %s timed out on %s", settings.llm_base_url, purpose)
                return None
            except httpx.HTTPError as e:
                _log(db, purpose, model, ok=False, note=f"not reachable: {type(e).__name__}")
                log.warning("No model server at %s (%s); using the rules engine", settings.llm_base_url, e)
                return None

            if resp.status_code >= 400:
                body = resp.text[:400]
                if (
                    mode == "schema"
                    and resp.status_code in (400, 422)
                    and re.search(r"response_format|json_schema|schema", body, re.I)
                ):
                    mode = _json_mode_override[settings.llm_base_url] = "object"
                    continue
                if has_images and re.search(r"image|vision|multimodal|multi-modal", body, re.I):
                    _no_vision.add(model)
                    log.warning("%s does not take images; continuing without photos", model)
                    messages[1]["content"] = _user_content([p for p in content if p["type"] == "text"])
                    has_images, model = False, settings.llm_model
                    continue
                note = f"HTTP {resp.status_code}: {body[:160]}"
                if resp.status_code == 404:
                    note = f"model {model!r} not found on the server (with Ollama: `ollama pull {model}`)"
                _log(db, purpose, model, ok=False, note=note)
                log.warning("Model call for %s failed: %s", purpose, note)
                return None

            try:
                data = resp.json()
                choice = data["choices"][0]
                text = choice["message"].get("content") or ""
            except (ValueError, KeyError, IndexError):
                _log(db, purpose, model, ok=False, note="reply was not a chat completion")
                return None
            if choice.get("finish_reason") == "length":
                _log(db, purpose, model, ok=False, data=data, note="reply cut off by the token limit")
                return None
            try:
                parsed = output.model_validate_json(extract_json(text))
            except ValidationError as e:
                _log(db, purpose, model, ok=False, data=data, note=f"invalid JSON: {e.error_count()} errors")
                if retried_bad_json:
                    return None
                retried_bad_json = True
                first = e.errors()[0]
                messages += [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"That reply did not match the schema ({first['loc']}: {first['msg']}). "
                        "Reply again with only the JSON object.",
                    },
                ]
                continue
            _log(db, purpose, model, ok=True, data=data)
            return parsed
    return None


def check(settings: Settings) -> tuple[bool, str]:
    """Is the model server up, and does it have the configured models? For `fieldbook doctor`."""
    if not settings.llm_model:
        return False, "No model set (FIELDBOOK_LLM_MODEL). The Assistant uses the rules engine."
    try:
        with _client(settings) as http:
            resp = http.get(f"{settings.llm_base_url}/models", timeout=5)
    except httpx.HTTPError as e:
        return (
            False,
            f"No model server at {settings.llm_base_url} ({type(e).__name__}). Start Ollama with `ollama serve`.",
        )
    if resp.status_code >= 400:
        return False, f"{settings.llm_base_url} answered HTTP {resp.status_code}."
    names = {m.get("id", "") for m in resp.json().get("data", [])}
    missing = [m for m in {settings.llm_model, settings.vision_model} if m and m not in names]
    if missing:
        return (
            False,
            f"Server is up, but {', '.join(missing)} is not available. With Ollama: `ollama pull {missing[0]}`.",
        )
    return True, f"{settings.llm_base_url} is serving {settings.llm_model}" + (
        f" (photos: {settings.vision_model})" if settings.vision_model != settings.llm_model else ""
    )
