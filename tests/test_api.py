"""End-to-end through HTTP: the flows the office and the field app depend on."""

from fastapi.testclient import TestClient

from fieldbook.sample import make_pdf, make_photo


def _jobs(snapshot):
    return {j["tag"]: j for j in snapshot["jobs"]}


def _people(snapshot):
    return {p["name"]: p for p in snapshot["people"]}


# ---------------------------------------------------------------- the seeded office matches the brief


def test_seeded_office_matches_the_brief(owner):
    snap = owner.get("/api/office").json()
    jobs = _jobs(snap)
    assert (jobs["Line A"]["approved_pct"], jobs["Line A"]["pending_pct"]) == (62, 71)
    assert jobs["Line A"]["qty_label"] == "2,230 of 3,600 LF"
    assert jobs["Line A"]["status"] == ["warn", "Waiting on pressure test report"]
    assert jobs["Services"]["qty_label"] == "46 of 72 services"
    assert (jobs["90% set"]["approved_pct"], jobs["90% set"]["pending_pct"]) == (70, 77)
    assert jobs["Sewer"]["status"][0] == "bad"
    assert jobs["SW3P"]["status"] == ["none", "Next one due Thursday"]

    inbox = {r["person_id"]: r for r in snap["inbox"] if r["state"] == "submitted"}
    people = _people(snap)
    ortiz = inbox[people["J. Ortiz"]["id"]]
    assert ortiz["change"] == "+320 LF" and ortiz["where"] == "Sta 12+00 to 15+20"
    assert ortiz["group"] == "attention" and ortiz["missing"] == ["Pressure test report"]
    assert ortiz["usual"] == "320 LF today vs. this crew's usual 200 LF"
    lee = inbox[people["K. Lee"]["id"]]
    assert lee["group"] == "flagged" and lee["verdict"].startswith("2 of 9 photos match ones submitted")
    chen = inbox[people["D. Chen"]["id"]]
    assert chen["group"] == "routine" and chen["usual"] == "4 sheets today vs. this crew's usual 3 sheets"
    assert people["S. Patel"]["link"] == "offline" and people["S. Patel"]["queued"] == 12
    assert snap["assistant"] == {"mode": "rules", "label": "Rules engine"}


def test_office_needs_a_manager_login(app, seeded, field):
    anon = TestClient(app)
    assert anon.get("/api/office").status_code == 401
    assert anon.get("/").status_code == 401
    # A field link is not an office login, even for the PM's own field link.
    assert field("Dana Brooks").get("/api/office").status_code == 401
    assert field("J. Ortiz").post("/api/reports/1/approve").status_code == 401


# ---------------------------------------------------------------- the core loop


def test_field_report_to_locked_approval(owner, field):
    ortiz = field("J. Ortiz")
    me = ortiz.get("/api/field/me").json()
    line_a = next(j for j in me["jobs"] if j["tag"] == "Line A")
    before = line_a["done"]

    # Approve today's seeded report first so the new one starts from 15+20.
    snap = owner.get("/api/office").json()
    seeded_report = next(r for r in snap["inbox"] if r["state"] == "submitted" and r["job_id"] == line_a["id"])
    assert owner.post(f"/api/reports/{seeded_report['id']}/approve").status_code == 200

    r = ortiz.post(
        "/api/field/inputs",
        data={"kind": "photo", "text": "16+00", "client_id": "c-1", "job_id": line_a["id"]},
        files={"file": ("a.jpg", make_photo(901, "16+00"), "image/jpeg")},
    )
    assert r.status_code == 201
    report_id = r.json()["report_id"]
    # The offline queue may send the same item twice; the second is recognised.
    again = ortiz.post(
        "/api/field/inputs",
        data={"kind": "photo", "client_id": "c-1"},
        files={"file": ("a.jpg", make_photo(901, "16+00"), "image/jpeg")},
    )
    assert again.json()["duplicate"] is True
    ortiz.post(
        "/api/field/inputs",
        data={"kind": "text", "text": "Laid 80 feet, 15+20 to 16+00. Pressure test passed.", "job_id": line_a["id"]},
    )
    draft = next(rep for rep in ortiz.get("/api/field/me").json()["reports"] if rep["id"] == report_id)
    assert draft["state"] == "draft" and draft["delta"] == 80 and draft["missing"] == []

    sent = ortiz.post(f"/api/field/reports/{report_id}/submit").json()
    assert sent["state"] == "submitted" and sent["where"] == "Sta 15+20 to 16+00"

    ok = owner.post(f"/api/reports/{report_id}/approve")
    assert ok.status_code == 200 and ok.json()["fingerprint"].startswith("#")
    after = _jobs(owner.get("/api/office").json())["Line A"]
    assert after["done"] == before + 320 + 80
    assert owner.get("/api/ledger/verify").json()["ok"] is True
    # Approving twice is refused; the ledger only moves once.
    assert owner.post(f"/api/reports/{report_id}/approve").status_code == 400


def test_request_changes_then_resubmit(owner, field):
    snap = owner.get("/api/office").json()
    lee_id = _people(snap)["K. Lee"]["id"]
    report = next(r for r in snap["inbox"] if r["person_id"] == lee_id and r["state"] == "submitted")
    r = owner.post(f"/api/reports/{report['id']}/changes", json={"items": ["2 new photos"], "note": "Retake MH-5"})
    assert r.json()["state"] == "changes_requested"
    lee = field("K. Lee")
    mine = next(rep for rep in lee.get("/api/field/me").json()["reports"] if rep["id"] == report["id"])
    assert "2 new photos" in mine["change_note"]
    lee.post(
        "/api/field/inputs",
        data={"kind": "photo", "text": "MH-5"},
        files={"file": ("n.jpg", make_photo(950, "MH-5"), "image/jpeg")},
    )
    assert lee.post(f"/api/field/reports/{report['id']}/submit").json()["state"] == "submitted"


def test_questions_reach_the_person(owner, field):
    snap = owner.get("/api/office").json()
    ortiz_id = _people(snap)["J. Ortiz"]["id"]
    report = next(r for r in snap["inbox"] if r["person_id"] == ortiz_id and r["state"] == "submitted")
    owner.post(f"/api/reports/{report['id']}/questions", json={"text": "When is the pressure test?"})
    mine = field("J. Ortiz").get("/api/field/me").json()
    assert mine["person"]["activity"]["text"].endswith("asked 1 question")
    assert "When is the pressure test?" in next(r for r in mine["reports"] if r["id"] == report["id"])["asked"]


# ---------------------------------------------------------------- people


def test_hire_link_role_change_and_removal(app, owner):
    snap = owner.get("/api/office").json()
    paving = _jobs(snap)["Paving"]
    r = owner.post(
        "/api/people",
        json={"name": "Priya Raman", "role": "inspector", "contact": "(555) 201-3344", "job_ids": [paving["id"]]},
    )
    assert r.status_code == 201
    priya = r.json()["person"]
    assert priya["team"] == "Field"
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + r.json()["field_link"].rsplit("/", 1)[-1]
    assert [j["tag"] for j in c.get("/api/field/me").json()["jobs"]] == ["Paving"]

    moved = owner.patch(f"/api/people/{priya['id']}", json={"role": "cad"}).json()
    assert moved["team"] == "Design"

    assert owner.delete(f"/api/people/{priya['id']}").json()["jobs_needing_someone"] == [paving["id"]]
    assert c.get("/api/field/me").status_code == 401  # link stops working
    jobs = _jobs(owner.get("/api/office").json())
    assert jobs["Paving"]["status"] == ["warn", "Needs someone"]
    assert owner.delete(f"/api/people/{snap['me']['id']}").status_code == 400  # the owner stays


def test_removing_someone_keeps_their_approved_history(owner):
    snap = owner.get("/api/office").json()
    ortiz_id = _people(snap)["J. Ortiz"]["id"]
    before = _jobs(snap)["Line A"]["done"]
    owner.delete(f"/api/people/{ortiz_id}")
    after = _jobs(owner.get("/api/office").json())["Line A"]
    assert after["done"] == before
    assert owner.get("/api/ledger/verify").json()["ok"]


def test_contractor_sees_only_own_scope(field):
    me = field("T. Ward").get("/api/field/me").json()
    assert [j["tag"] for j in me["jobs"]] == ["Paving"]
    assert field("T. Ward").get("/api/field/files/1").status_code == 404


# ---------------------------------------------------------------- projects, plans, amendments


def test_project_setup_reads_documents_and_needs_quantities(owner):
    snap = owner.get("/api/office").json()
    people = _people(snap)
    p = owner.post(
        "/api/projects",
        json={
            "name": "Elm Street Drainage Improvements",
            "client": "City of Oak Hollow",
            "work_types": ["Drainage"],
            "team": [people["J. Ortiz"]["id"], people["S. Patel"]["id"]],
        },
    )
    pid = p.json()["id"]
    bid = make_pdf([["BID FORM", "Item 3  18-in RCP storm drain  1,240 LF", "Item 4  Curb inlet, Type A  9 EA"]])
    up = owner.post(
        f"/api/projects/{pid}/documents",
        files=[
            ("files", ("Bid form.pdf", bid, "application/pdf")),
            ("files", ("Bid form (1).pdf", bid, "application/pdf")),
        ],
    )
    statuses = [d["status"] for d in up.json()["documents"]]
    assert statuses == ["ready", "duplicate"]
    assert owner.post(f"/api/projects/{pid}/plan").status_code == 202
    plan = owner.get(f"/api/projects/{pid}/plan").json()
    assert plan["project"]["plan_status"] == "review"
    items = {i["tag"]: i for i in plan["items"]}
    assert items["RCP"]["quantity"] == 1240 and items["RCP"]["source"] == "Bid form.pdf p.1"
    assert items["Inlets"]["quantity"] == 9
    assert (
        items["SW3P"]["quantity"] is None and not items["SW3P"]["found"]
    )  # not in the documents: blank, never invented
    assert items["RCP"]["person_id"] == people["J. Ortiz"]["id"]
    assert items["SW3P"]["person_id"] == people["S. Patel"]["id"]
    # No CAD tech on the team: office work goes to the PM, not the owner.
    assert items["IFC sheets"]["person_id"] == people["Dana Brooks"]["id"]

    blank = owner.post(f"/api/projects/{pid}/plan/approve", json={"items": []})
    assert blank.status_code == 400 and "Add a quantity" in blank.json()["detail"]
    fill = [{"id": i["id"], "quantity": 12} for i in plan["items"] if i["quantity"] is None]
    ok = owner.post(f"/api/projects/{pid}/plan/approve", json={"items": fill})
    assert ok.status_code == 200
    proj = owner.get(f"/api/projects/{pid}").json()
    assert proj["plan_status"] == "approved" and len(proj["jobs"]) == len(plan["items"])
    assert proj["history"][0]["text"].startswith("Elm Street plan approved")


def test_amendment_keeps_the_original_readable(owner):
    snap = owner.get("/api/office").json()
    oak = snap["projects"][0]
    history = owner.get(f"/api/projects/{oak['id']}").json()["history"]
    amended = [h for h in history if h["amendment"]]
    assert (
        amended
        and amended[0]["text"].endswith("Sta 7+60 to 9+40")
        and amended[0]["amendment"]["text"].endswith("7+80 to 9+40")
    )
    target = next(h for h in history if h["kind"] == "report_approved" and not h["amendment"])
    assert owner.post(f"/api/ledger/{target['id']}/amend", json={"text": "fixed", "reason": " "}).status_code == 400
    r = owner.post(
        f"/api/ledger/{target['id']}/amend", json={"text": target["text"], "reason": "Wrong crew on the entry"}
    )
    assert r.status_code == 201
    again = {h["id"]: h for h in owner.get(f"/api/projects/{oak['id']}").json()["history"]}
    assert again[target["id"]]["amendment"]["reason"] == "Wrong crew on the entry"
    assert owner.get("/api/ledger/verify").json()["ok"]


# ---------------------------------------------------------------- ask, files, stream


def test_ask_answers_from_data(owner):
    r = owner.post("/api/ask", json={"question": "Which reports need a decision?"}).json()
    assert r["answered_by"] == "rules" and r["answer"].startswith("4 waiting for approval") and r["sources"]
    line_a = owner.post("/api/ask", json={"question": "What's holding up Line A?"}).json()
    assert "pressure test" in line_a["answer"].lower()


def test_files_are_scoped(owner, field):
    snap = owner.get("/api/office").json()
    chip = next(c for r in snap["inbox"] for c in r["chips"] if c["kind"] == "photo")
    assert owner.get(f"/api/files/{chip['blob_id']}").headers["content-type"] == "image/jpeg"
    owner_of_photo = next(r for r in snap["inbox"] if chip in r["chips"])["person_id"]
    name = next(p["name"] for p in snap["people"] if p["id"] == owner_of_photo)
    assert field(name).get(f"/api/field/files/{chip['blob_id']}").status_code == 200
    other = "D. Chen" if name != "D. Chen" else "J. Ortiz"
    assert field(other).get(f"/api/field/files/{chip['blob_id']}").status_code == 404


def test_event_stream_replays_after_an_id(app, owner):
    app.state.stream_once = True
    with owner.stream("GET", "/api/events/stream?after=0") as resp:
        body = "".join(resp.iter_text())
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "report.submitted" in body and "id: 1" in body
