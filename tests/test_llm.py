"""The local-model path, against a simulated OpenAI-compatible server (the API Ollama, vLLM,
LM Studio and llama.cpp serve). No model weights or network are needed."""

import json

import httpx
import pytest

from fieldbook import services
from fieldbook.assistant import ask, llm, planning
from fieldbook.config import load_settings
from fieldbook.models import Job, Org, Person, UsageLog
from fieldbook.sample import make_photo


class FakeServer:
    """Answers /v1/chat/completions from a queue of scripted replies and records every request."""

    def __init__(self):
        self.queue = []
        self.requests = []

    def json(self, payload, finish="stop", wrap=None):
        text = json.dumps(payload)
        self.queue.append(("ok", wrap(text) if wrap else text, finish))

    def text(self, text):
        self.queue.append(("ok", text, "stop"))

    def error(self, status, body):
        self.queue.append(("error", status, body))

    def refuse_connection(self):
        self.queue.append(("down",))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append({"url": str(request.url), "body": body})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": "qwen2.5vl:7b"}]})
        kind, *rest = self.queue.pop(0)
        if kind == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if kind == "error":
            return httpx.Response(rest[0], text=rest[1])
        content, finish = rest
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020},
            },
        )


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(llm, "_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(llm, "_json_mode_override", {})
    monkeypatch.setattr(llm, "_no_vision", set())
    return fake


@pytest.fixture
def local(settings):
    return load_settings(
        data_dir=settings.data_dir, assistant_mode="auto", llm_model="qwen2.5vl:7b", public_url="http://test"
    )


DRAFT = {
    "quantity": 40,
    "unit": "LF",
    "station_from": "15+20",
    "station_to": "15+60",
    "claims": ["40 LF of 8-in PVC laid"],
    "summary": "40 LF",
    "missing": [],
    "question": None,
}


def _ortiz(db):
    return db.query(Person).filter_by(name="J. Ortiz").one(), db.query(Job).filter_by(tag="Line A").one()


def _photo_input(db, local, text="15+60"):
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db,
        local,
        person,
        kind="photo",
        text=text,
        job_id=job.id,
        upload=services.Upload("p.jpg", "image/jpeg", make_photo(77, "15+60")),
    )
    return report


def test_request_is_openai_compatible_with_schema_and_images(seeded, db, local, server):
    server.json(DRAFT)
    report = _photo_input(db, local)
    services.redraft(db, local, report)
    req = server.requests[0]
    assert req["url"] == "http://localhost:11434/v1/chat/completions"
    body = req["body"]
    assert body["model"] == "qwen2.5vl:7b" and body["temperature"] == 0 and body["stream"] is False
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    assert "JSON schema" in body["messages"][0]["content"]  # schema also in the prompt for small models
    parts = body["messages"][1]["content"]
    assert any(p["type"] == "image_url" and p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in parts)
    assert report.drafted_by == "llm" and report.delta == 40
    log = db.query(UsageLog).one()
    assert log.ok and log.input_tokens == 900 and log.model == "qwen2.5vl:7b"


def test_text_only_requests_send_a_plain_string(seeded, db, local, server):
    server.json(DRAFT)
    person, job = _ortiz(db)
    _, report, _ = services.add_input(db, local, person, kind="text", text="pipe went in", job_id=job.id)
    services.redraft(db, local, report)
    assert isinstance(server.requests[0]["body"]["messages"][1]["content"], str)


def test_reasoning_tags_and_code_fences_are_stripped(seeded, db, local, server):
    server.json(DRAFT, wrap=lambda t: f"<think>stations 15+20 to 15+60 is 40 ft</think>\n```json\n{t}\n```")
    report = _photo_input(db, local)
    services.redraft(db, local, report)
    assert report.drafted_by == "llm" and report.sta_to == "15+60"


def test_server_without_json_schema_support_falls_back_to_json_mode(seeded, db, local, server):
    server.error(400, '{"error":"unknown response_format type json_schema"}')
    server.json(DRAFT)
    server.json(DRAFT)
    report = _photo_input(db, local)
    services.redraft(db, local, report)
    assert [r["body"]["response_format"]["type"] for r in server.requests] == ["json_schema", "json_object"]
    services.redraft(db, local, report)  # remembered for the next call
    assert server.requests[-1]["body"]["response_format"]["type"] == "json_object"


def test_text_only_model_gets_the_request_without_photos(seeded, db, local, server):
    server.error(400, '{"error":"model does not support images"}')
    server.json(DRAFT)
    report = _photo_input(db, local)
    services.redraft(db, local, report)
    assert isinstance(server.requests[1]["body"]["messages"][1]["content"], str)
    assert report.drafted_by == "llm"


def test_bad_json_gets_one_retry_with_the_error(seeded, db, local, server):
    server.text('{"quantity": "about forty"}')
    server.json(DRAFT)
    report = _photo_input(db, local)
    services.redraft(db, local, report)
    retry = server.requests[1]["body"]["messages"]
    assert retry[-1]["role"] == "user" and "did not match the schema" in retry[-1]["content"]
    assert report.drafted_by == "llm"


def test_persistent_bad_json_falls_back_to_rules(seeded, db, local, server):
    server.text("Sure! Here is the report.")
    server.text("I think about 60 feet.")
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db, local, person, kind="text", text="Laid 60 feet, 15+20 to 15+80", job_id=job.id
    )
    services.redraft(db, local, report)
    assert report.drafted_by == "rules" and report.delta == 60


def test_no_server_running_falls_back_to_rules(seeded, db, local, server):
    server.refuse_connection()
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db, local, person, kind="text", text="Laid 60 feet, 15+20 to 15+80", job_id=job.id
    )
    services.redraft(db, local, report)
    assert report.drafted_by == "rules" and report.delta == 60
    assert "not reachable" in db.query(UsageLog).one().note


def test_missing_model_says_how_to_pull_it(seeded, db, local, server):
    server.error(404, '{"error":"model \\"qwen2.5vl:7b\\" not found, try pulling it first"}')
    person, job = _ortiz(db)
    _, report, _ = services.add_input(db, local, person, kind="text", text="Laid 60 feet", job_id=job.id)
    services.redraft(db, local, report)
    assert "ollama pull qwen2.5vl:7b" in db.query(UsageLog).one().note


def test_numbers_the_person_stated_beat_the_model(seeded, db, local, server):
    server.json({**DRAFT, "quantity": 400, "station_from": "15+00", "station_to": "19+00"})  # the model misreads
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db, local, person, kind="text", text="Laid 60 feet, 15+20 to 15+80", job_id=job.id
    )
    services.redraft(db, local, report)
    assert (report.quantity, report.sta_from, report.sta_to, report.delta) == (60, "15+20", "15+80", 60)


def test_plan_figures_the_documents_do_not_print_are_blanked(seeded, db, local, server):
    from fieldbook.sample import make_pdf

    server.json(
        {
            "items": [
                {
                    "name": "18-in RCP storm drain",
                    "tag": "RCP",
                    "method": "qty",
                    "quantity": 1240,
                    "unit": "LF",
                    "source": "Bid.pdf p.1",
                    "role": "crew",
                },
                {
                    "name": "Curb inlets",
                    "tag": "Inlets",
                    "method": "qty",
                    "quantity": 14,
                    "unit": "EA",
                    "source": "Bid.pdf p.1",
                    "role": "crew",
                },
            ],
            "notes": [],
        }
    )
    owner = db.query(Person).filter_by(role="owner").one()
    project = services.create_project(
        db, db.query(Org).one(), owner, name="Elm Street Drainage", client=None, work_types=["Drainage"], team_ids=[]
    )
    pdf = make_pdf([["BID FORM", "18-in RCP storm drain  1,240 LF", "Curb inlet, Type A  9 EA"]])
    services.add_documents(db, local, project, owner, [services.Upload("Bid.pdf", "application/pdf", pdf)])
    items, notes, by = planning.propose(db, local, project)
    assert by == "llm"
    got = {i.tag: i for i in items}
    assert got["RCP"].quantity == 1240 and got["RCP"].found
    assert got["Inlets"].quantity is None and "not in the documents" in got["Inlets"].source  # 14 was invented
    assert "=== Bid.pdf p.1 ===" in server.requests[0]["body"]["messages"][1]["content"]


def test_ask_drops_sources_that_do_not_exist(seeded, db, local, server):
    server.json(
        {
            "answer": "Line A is waiting on the pressure test.",
            "sources": [{"label": "Job · Line A"}, {"label": "Made-up memo"}],
        }
    )
    out = ask.answer(db, local, db.query(Person).filter_by(role="owner").one().org_id, "What's holding up Line A?")
    assert out["answered_by"] == "llm" and out["sources"] == ["Job · Line A"]


def test_check_reports_server_and_model(local, server):
    ok, detail = llm.check(local)
    assert ok and "qwen2.5vl:7b" in detail
    missing = load_settings(data_dir=local.data_dir, llm_model="llama3.1:8b", public_url="http://test")
    ok, detail = llm.check(missing)
    assert not ok and "ollama pull llama3.1:8b" in detail


def test_without_a_model_nothing_is_called(seeded, db, settings, server):
    person, job = _ortiz(db)
    _, report, _ = services.add_input(
        db, settings, person, kind="text", text="Laid 60 feet, 15+20 to 15+80", job_id=job.id
    )
    services.redraft(db, settings, report)
    assert server.requests == [] and report.drafted_by == "rules"
