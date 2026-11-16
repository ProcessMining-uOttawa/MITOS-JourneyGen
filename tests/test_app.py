"""Drive the app without a browser: every view renders, a log is generated in
both formats, and the views that need a log work on it.

    python -m pytest tests -q
"""
import io
import json
import os
import zipfile

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWS = ["Start", "Scenarios", "Population", "Generate", "Inspect", "Score a method",
         "Bundles to integrate", "Catalogue"]


def start() -> AppTest:
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    return at


def go(at: AppTest, view: str) -> AppTest:
    at.sidebar.radio[0].set_value(view).run()
    assert not at.exception, (view, at.exception)
    return at


def button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def test_every_view_renders_before_a_log_exists():
    at = start()
    assert at.session_state["view"] == "Start"       # newcomers land on the guide
    for view in VIEWS:
        go(at, view)
    assert not at.error


def test_the_start_page_leads_to_generate():
    at = start()
    labels = {b.label for b in at.button}
    for view in VIEWS[1:]:                 # every entry of the menu is introduced
        assert any(l.startswith("Go to " + view.split()[0]) for l in labels), view
    button(at, "Go to Generate").click().run()
    assert not at.exception, at.exception
    assert at.session_state["view"] == "Generate"
    assert any(b.label == "Generate" for b in at.button)


def test_a_scenario_is_a_story_first_and_an_editor_on_request():
    at = go(start(), "Scenarios")
    text = " ".join(m.value for m in at.markdown)
    assert "Declaration of a death that occurred in Greece" in text and "always" in text
    assert not any(n.label == "Name" for n in at.text_input)
    next(t for t in at.toggle if t.label == "Edit this scenario").set_value(True).run()
    assert not at.exception, at.exception
    assert any(n.label == "Name" for n in at.text_input)
    assert any("Step 2" in h.value for h in at.subheader)


def test_picking_a_life_event_once_is_enough():
    at = go(start(), "Scenarios")
    pick = next(s for s in at.selectbox if s.label.startswith("Pick a life event"))
    pick.set_value(1).run()
    assert not at.exception, at.exception
    assert any("#### Birth of a child" in m.value for m in at.markdown)
    # and the choice is still there after another view was visited
    go(at, "Population")
    go(at, "Scenarios")
    assert any("#### Birth of a child" in m.value for m in at.markdown)


def test_the_method_guide_opens_as_a_page_of_its_own():
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300)
    at.query_params["guide"] = "methods"
    at.run()
    assert not at.exception, at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Methods for finding journeys" in text and "Hawkes process + register" in text
    assert not at.sidebar.radio


def test_unrealistic_values_are_pointed_out_where_they_are_set():
    at = start()
    cfg = at.session_state["cfg"]
    cfg["scenarios"][0]["rate_per_year"] = {"45-59": 0.6}
    cfg["background"]["rate_per_year"] = 0
    go(at, "Scenarios")
    assert any("twice or more" in w.value for w in at.warning)
    go(at, "Population")
    assert any("no background use" in w.value for w in at.warning)
    go(at, "Generate")
    assert any("twice or more" in w.value for w in at.warning)


@pytest.mark.parametrize("fmt, log_file", [("CSV", "events.csv"),
                                           ("XES, gzipped", "events.xes.gz")])
def test_generate_inspect_and_score(fmt, log_file):
    at = go(start(), "Generate")
    next(n for n in at.number_input if n.label == "Citizens").set_value(300)
    next(r for r in at.radio if r.label == "Log format").set_value(fmt)
    at.run()
    button(at, "Generate").click().run()
    assert not at.exception, at.exception
    result = at.session_state["result"]
    assert result["manifest"]["arguments"]["citizens"] == 300
    names = zipfile.ZipFile(io.BytesIO(result["zip"])).namelist()
    assert log_file in names and "truth_instances.csv" in names and "scenarios.json" in names
    assert ("events.csv" in names) == (fmt == "CSV")

    go(at, "Inspect")
    assert any(m.label == "Episodes" for m in at.metric)

    go(at, "Score a method")
    button(at, "Hawkes process").click().run()
    assert not at.exception, at.exception
    button(at, "Register only").click().run()
    assert not at.exception, at.exception
    next(t for t in at.toggle if t.label.startswith("Also show every method")).set_value(
        True).run()
    assert not at.exception, at.exception
    scores = at.session_state["scores"]
    assert "Hawkes, overlapping" in scores and "fixed gap, 30 days" in scores
    assert any(m.label == "Baseline to beat" for m in at.metric)
    best = next(m for m in at.metric if m.label == "Best method so far")
    assert "points on the baseline" in best.delta
    table = " ".join(h.body for h in at.get("html"))
    assert "Hawkes, overlapping + register" in table and "&#9733;" in table
    assert "#16a34a" in table and "#dc2626" in table     # good and poor scores are coloured
    assert 0 < scores["Hawkes, overlapping"]["F1"] <= 1
    assert "register only, overlapping" in scores
    plus = scores["Hawkes, overlapping + register"]
    assert plus["recall"] >= scores["Hawkes, overlapping"]["recall"]
    assert "register only, overlapping + register" not in scores
    # with so few shared instances the app says the percentage cannot be trusted
    assert any("shared instances" in w.value for w in at.warning)

    go(at, "Bundles to integrate")
    assert any(m.label == "Journeys" for m in at.metric)
    assert any(h.value == "Integration opportunities" for h in at.subheader)
    tables = " ".join(h.body for h in at.get("html"))
    assert "best trade-offs" in " ".join(m.value for m in at.markdown)
    assert 'title="' in tables and "2. Offer as one step" in tables and "1 low" in tables
    assert any(d.label == "Report (Markdown)" for d in at.get("download_button"))
    source = next(s for s in at.selectbox if s.label == "Journeys to use")
    assert "Hawkes, overlapping" in source.options
    source.set_value("Hawkes, overlapping").run()
    assert not at.exception, at.exception
    assert any(m.label == "Journeys" for m in at.metric)
    kinds = next(m for m in at.metric if m.label == "Kinds")
    assert kinds.value == "1"                      # Hawkes does not name life events


def test_the_latent_models_kinds_reach_the_bundles_view():
    at = go(start(), "Generate")
    button(at, "Generate").click().run()
    go(at, "Score a method")
    button(at, "Latent life events").click().run()
    assert not at.exception, at.exception
    found = at.session_state["found"]["latent life events, overlapping"]
    assert "kind" in found.columns and (found.kind > 0).any()

    go(at, "Bundles to integrate")
    next(s for s in at.selectbox if s.label == "Journeys to use").set_value(
        "latent life events, overlapping").run()
    assert not at.exception, at.exception
    assert int(next(m for m in at.metric if m.label == "Kinds").value) >= 5
    logs = at.session_state["result"]["journey_logs"]
    log = next(v for k, v in logs.items() if k[0].startswith("latent"))
    names = set(log["case:kind"])
    assert all(n.startswith("Kind ") or n == "No life event found" for n in names)
    assert any("passport" in n.lower() for n in names)


def test_the_same_seed_gives_the_same_log_as_the_command_line(tmp_path):
    import generate as g

    at = go(start(), "Generate")
    next(n for n in at.number_input if n.label == "Citizens").set_value(200)
    at.run()
    button(at, "Generate").click().run()
    from_app = zipfile.ZipFile(io.BytesIO(at.session_state["result"]["zip"])).read("events.csv")

    cat = g.Catalogue(os.path.join(ROOT, "catalogue.json.gz"),
                      os.path.join(ROOT, "translations_en.json"))
    with open(os.path.join(ROOT, "scenarios.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    run = g.Generator(cat, cfg, 1)
    run.run(200)
    run.write(str(tmp_path), "en", {})
    assert from_app == (tmp_path / "events.csv").read_bytes()


def test_a_scenario_naming_a_withdrawn_procedure_is_reported():
    at = start()
    at.session_state["cfg"]["scenarios"][0]["procedures"][0]["id"] = "000000"
    at.run()
    assert any("000000" in e.value for e in at.error)
