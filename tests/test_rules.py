from fieldbook.assistant import rules


def test_stations():
    assert rules.station_value("12+00") == 1200
    assert rules.station_value("15+20") == 1520
    assert rules.station_label(1520) == "15+20"
    assert rules.find_station_range("Laid pipe Sta 12+00 to 15+20 today") == ("12+00", "15+20")
    assert rules.find_station_range("from 9+40 thru 12+00") == ("9+40", "12+00")
    assert rules.find_station_range("no stations here") is None


def test_quantities_prefer_the_jobs_unit():
    assert rules.find_quantity("Laid 320 feet today, 12+00 to 15+20", "LF") == (320, "LF")
    assert rules.find_quantity("4 services done on Elm Ct, 206 to 212", "EA") == (4, "EA")
    assert rules.find_quantity("Sheets W-38 to W-41 drafted", "sheets") == (4, "sheets")
    assert rules.find_quantity("CCTV step is 20%", "%") == (20, "%")
    assert rules.find_quantity("Roller ready", "SY") is None


def test_draft_computes_from_stations_when_no_quantity_is_stated():
    d = rules.draft_from_inputs(
        job_name="Line A",
        job_unit="LF",
        job_word=None,
        checklist=["Photos"],
        texts=["Sta 12+00 to 15+20 done"],
        photos=3,
        voice=0,
        docs=[],
    )
    assert d.quantity == 320 and d.question is None


def test_draft_asks_which_stations():
    d = rules.draft_from_inputs(
        job_name="Line A",
        job_unit="LF",
        job_word=None,
        checklist=[],
        texts=["Laid 300 feet"],
        photos=1,
        voice=0,
        docs=[],
    )
    assert d.question == "Which stations?"


def test_checklist_evidence():
    kw = {"photos": 2, "texts": ["Test crew tomorrow"]}
    assert not rules.evidence_present("Pressure test report", docs=[("ticket.pdf", "Delivery ticket")], **kw)
    assert rules.evidence_present("Pressure test report", docs=[("Pressure test Line A.pdf", "")], **kw)
    assert rules.evidence_present("Pressure test report", photos=0, docs=[], texts=["Pressure test passed at 150 psi"])
    assert rules.evidence_present("Photos", photos=1, docs=[], texts=[])
    assert not rules.evidence_present("Photos", photos=0, docs=[], texts=[])
