"""The Claude path, with the SDK replaced by a fake (no network, no key needed)."""

import json
from types import SimpleNamespace

import pytest

from fieldbook import services
from fieldbook.assistant import client
from fieldbook.config import load_settings
from fieldbook.models import Job, Person, UsageLog
from fieldbook.sample import make_photo


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        stop, payload = reply
        content = [SimpleNamespace(type="text", text=json.dumps(payload))] if payload is not None else []
        return SimpleNamespace(
            content=content,
            stop_reason=stop,
            stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None,
            model=kwargs["model"],
            usage=SimpleNamespace(
                input_tokens=1200, output_tokens=150, cache_read_input_tokens=0, cache_creation_input_tokens=0
            ),
            _request_id="req_test",
        )


@pytest.fixture
def fake(monkeypatch):
    def install(*replies):
        messages = FakeMessages(replies)
        sdk = SimpleNamespace(beta=SimpleNamespace(messages=messages))
        monkeypatch.setattr(client, "_sdk", lambda: sdk)
        return messages

    return install


@pytest.fixture
def claude_settings(settings):
    return load_settings(data_dir=settings.data_dir, assistant_mode="claude", public_url="http://test")


def _ortiz(db):
    person = db.query(Person).filter_by(name="J. Ortiz").one()
    job = db.query(Job).filter_by(tag="Line A").one()
    return person, job


def test_request_shape(seeded, db, claude_settings, fake):
    msgs = fake(
        (
            "end_turn",
            {
                "quantity": 40,
                "unit": "LF",
                "station_from": "15+20",
                "station_to": "15+60",
                "claims": ["40 LF laid"],
                "summary": "40 LF",
                "missing": [],
                "question": None,
            },
        )
    )
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db,
        claude_settings,
        person,
        kind="photo",
        text="15+60",
        job_id=job.id,
        upload=services.Upload("p.jpg", "image/jpeg", make_photo(77, "15+60")),
    )
    services.redraft(db, claude_settings, report)
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == set(fmt["schema"]["properties"])
    assert call["output_config"]["effort"] == "medium"
    assert any(block["type"] == "image" for block in call["messages"][0]["content"])
    assert report.drafted_by == "claude" and report.delta == 40
    log = db.query(UsageLog).one()
    assert log.ok and log.input_tokens == 1200 and log.request_id == "req_test"


def test_code_computes_delta_not_the_model(seeded, db, claude_settings, fake):
    # The model reports stations but no quantity; code turns the span into 40 LF.
    fake(
        (
            "end_turn",
            {
                "quantity": None,
                "unit": None,
                "station_from": "15+20",
                "station_to": "15+60",
                "claims": [],
                "summary": "Sta 15+20 to 15+60",
                "missing": [],
                "question": None,
            },
        )
    )
    person, job = _ortiz(db)
    _, report, _ = services.add_input(db, claude_settings, person, kind="text", text="did some pipe", job_id=job.id)
    services.redraft(db, claude_settings, report)
    assert report.delta == 40


def test_refusal_falls_back_to_rules(seeded, db, claude_settings, fake):
    fake(("refusal", None))
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db, claude_settings, person, kind="text", text="Laid 60 feet, 15+20 to 15+80", job_id=job.id
    )
    services.redraft(db, claude_settings, report)
    assert report.drafted_by == "rules" and report.delta == 60
    log = db.query(UsageLog).one()
    assert not log.ok and log.note == "refusal: cyber"


def test_bad_json_falls_back_to_rules(seeded, db, claude_settings, fake):
    fake(("end_turn", {"quantity": "lots"}))
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db, claude_settings, person, kind="text", text="Laid 60 feet, 15+20 to 15+80", job_id=job.id
    )
    services.redraft(db, claude_settings, report)
    assert report.drafted_by == "rules" and report.delta == 60


def test_evidence_the_rules_can_see_is_never_reported_missing(seeded, db, claude_settings, fake):
    # The model lists the pressure test as missing, but the message says it passed; code wins.
    fake(
        (
            "end_turn",
            {
                "quantity": 60,
                "unit": "LF",
                "station_from": "15+20",
                "station_to": "15+80",
                "claims": [],
                "summary": "",
                "missing": ["Pressure test report"],
                "question": None,
            },
        )
    )
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db,
        claude_settings,
        person,
        kind="text",
        text="Laid 60 feet, 15+20 to 15+80. Pressure test passed, sheet attached.",
        job_id=job.id,
    )
    services.redraft(db, claude_settings, report)
    assert report.missing == []


def test_strict_schema_inlines_refs():
    from fieldbook.assistant.planning import PlanOut

    schema = client.strict_schema(PlanOut)
    assert "$defs" not in json.dumps(schema)
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False and "quantity" in item["required"]
