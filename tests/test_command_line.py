"""The command lines, end to end on a small dataset, and the catalogue build
from a made-up MITOS cache."""
import gzip
import json
import os
import sys

import pandas as pd
import pytest

import build_catalogue
import bundles
import catalogue_changes
import generate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "prototype"))

import discover  # noqa: E402
import score  # noqa: E402


def run(monkeypatch, main, *argv):
    monkeypatch.setattr(sys, "argv", ["prog", *map(str, argv)])
    main()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    """A small log written by the command line, in CSV and gzipped XES."""
    out = tmp_path_factory.mktemp("small")
    mp = pytest.MonkeyPatch()
    try:
        run(mp, generate.main, "--citizens", 400, "--seed", 5, "--out", out,
            "--format", "both", "--gzip", "--lang", "both")
    finally:
        mp.undo()
    return out


def test_generate_writes_the_log_the_truth_and_a_manifest(dataset):
    names = set(os.listdir(dataset))
    assert {"events.csv", "events.xes.gz", "truth_instances.csv", "truth_episodes.csv",
            "services.csv", "manifest.json"} <= names
    m = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    assert m["arguments"]["citizens"] == 400 and m["events"] > 0
    events = pd.read_csv(dataset / "events.csv", dtype=str)
    assert {"service_el", "activity_el"} <= set(events.columns)   # --lang both


def test_generate_describe_lists_scenarios_and_remarks(monkeypatch, capsys):
    run(monkeypatch, generate.main, "--describe")
    text = capsys.readouterr().out
    assert "death_of_relative" in text and "needs" in text and "remarks" in text


def test_generate_refuses_a_scenario_it_cannot_run(tmp_path, monkeypatch):
    with open(os.path.join(ROOT, "scenarios.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["scenarios"][0]["procedures"][0]["id"] = "999999999"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(SystemExit, match="Cannot generate"):
        run(monkeypatch, generate.main, "--scenarios", bad, "--out", tmp_path / "o")


def test_generate_says_when_values_look_unrealistic(tmp_path, monkeypatch, capsys):
    run(monkeypatch, generate.main, "--citizens", 60, "--out", tmp_path, "--rate-scale", 20)
    assert "look unrealistic" in capsys.readouterr().out


def test_bundles_writes_the_answer_files(dataset, monkeypatch, capsys):
    run(monkeypatch, bundles.main, dataset)
    for name in ("journey_log.xes.gz", "journey_log.csv", "integration_report.md",
                 "integration_opportunities.csv", "integration_candidates.csv",
                 "journey_variants.csv", "repeated_requests.csv"):
        assert (dataset / name).stat().st_size > 0, name
    assert "integration opportunities" in capsys.readouterr().out


def test_prototype_discovery_and_scoring(dataset, monkeypatch, capsys):
    run(monkeypatch, discover.main, dataset)
    assert (dataset / "journeys.csv").exists() and (dataset / "pairs.csv").exists()
    # bundles on a method's journeys rather than the truth
    run(monkeypatch, bundles.main, dataset, "--journeys", dataset / "journeys.csv",
        "--no-register")
    capsys.readouterr()
    run(monkeypatch, score.main, dataset)
    text = capsys.readouterr().out
    assert "fixed gap 30 d" in text and "episodes kept whole" in text


# ------------------------------------------------------------ the catalogue
def fake_cache(folder, with_english=True):
    """Two procedures; 2 requires 1 as evidence; 1 is published in English too."""
    def doc(pid, lang):
        el = lang == "el"
        # MITOS gives the English title in both documents of a translated procedure
        return {"id": pid, "title": {"el": f"Διαδικασία {pid}",
                                     **({"en": f"Procedure {pid}"} if pid == "1" else {})},
                "metadata": {
                    "process": {"org_owner": 7, "life_events": ["Θάνατος" if el else "Death"],
                                "provided_to": ["Πολίτες" if el else "Citizens"],
                                "validity_duration": "P1Y"},
                    "process_steps": [
                        {"step_num_id": 1, "step_title": "Αίτηση" if el else "Application",
                         "step_official": "Υπάλληλος" if el else "Employee",
                         "step_implementation": "Χειροκίνητη" if el else "Manual",
                         "step_duration_min": "PT1M", "step_duration_max": "PT5M",
                         "step_exit": False, "step_child": False},
                        {"step_num_id": 2, "step_title": "Έκδοση" if el else "Issue",
                         "step_exit": True, "step_child": False}],
                    "process_steps_digital": [
                        {"step_digital_num_id": 1,
                         "step_digital_title": "Ψηφιακή αίτηση" if el else "Online form",
                         "step_digital_exit": True, "step_digital_child": False}],
                    "process_evidences": [] if pid == "1" else [
                        {"evidence_alternative": False,
                         "evidence_related_process": [{"id": "1"}, {"id": pid}]}]}}

    for sub in ("proc", "proc_en"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    for pid in ("1", "2"):
        (folder / "proc" / f"{pid}.json").write_text(json.dumps(doc(pid, "el")), "utf-8")
    if with_english:
        (folder / "proc_en" / "1.json").write_text(json.dumps(doc("1", "en")), "utf-8")
    (folder / "orgs.json").write_text(json.dumps(
        [{"id": 7, "title": {"el": "ΥΠΟΥΡΓΕΙΟ", "en": "MINISTRY"}}]), "utf-8")


def test_build_catalogue_offline_from_a_cache(tmp_path, monkeypatch, capsys):
    fake_cache(tmp_path / "raw")
    out = tmp_path / "catalogue.json.gz"
    run(monkeypatch, build_catalogue.main, "--cache", tmp_path / "raw", "--offline",
        "--out", out)
    with gzip.open(out, "rt", encoding="utf-8") as f:
        cat = json.load(f)
    p1, p2 = cat["procedures"]["1"], cat["procedures"]["2"]
    assert p2["requires"] == [{"id": "1", "alternative": False}]   # not itself
    assert p1["org"] == "ΥΠΟΥΡΓΕΙΟ" and p1["title_en"] == "Procedure 1"
    assert len(p1["steps"]) == 2 and len(p1["digital_steps"]) == 1
    assert cat["official_en"]["Αίτηση"] == "Application"           # learnt from the English
    assert "2 procedures, 1 with evidence links" in capsys.readouterr().out


def test_build_catalogue_stops_when_documents_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(build_catalogue, "download", lambda cache, progress=None: [("proc", "1")])
    with pytest.raises(SystemExit, match="could not be fetched"):
        run(monkeypatch, build_catalogue.main, "--cache", tmp_path, "--out", tmp_path / "c.gz")


def test_build_catalogue_downloads_then_builds(tmp_path, monkeypatch, capsys):
    def download(cache, progress=None):
        progress(0, 0, 0)
        fake_cache(tmp_path / "raw")
        progress(30, 30, 0)
        return []
    monkeypatch.setattr(build_catalogue, "download", download)
    run(monkeypatch, build_catalogue.main, "--cache", tmp_path / "raw",
        "--out", tmp_path / "c.json.gz")
    text = capsys.readouterr().out
    assert "Reading the lists" in text and "30 of 30 documents" in text


def built(tmp_path, name, **changes):
    fake_cache(tmp_path / name)
    for pid, title in changes.items():
        path = tmp_path / name / "proc" / f"{pid}.json"
        if title is None:
            path.unlink()
            continue
        d = json.loads(path.read_text("utf-8"))
        d["title"]["el"] = title
        path.write_text(json.dumps(d), "utf-8")
    cat = build_catalogue.build(str(tmp_path / name))
    cat["retrieved"] = name
    out = tmp_path / f"{name}.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(cat, f)
    return out


def test_catalogue_changes_command_line(tmp_path, monkeypatch, capsys):
    old = built(tmp_path, "2026-11-01")
    new = built(tmp_path, "2026-12-01", **{"2": "Νέος τίτλος"})
    github = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github))
    run(monkeypatch, catalogue_changes.main, old, new,
        "--json", tmp_path / "changes.json", "--log", tmp_path / "CHANGES.md")
    assert "1 changed" in capsys.readouterr().out
    assert "changed=true" in github.read_text("utf-8")
    assert "2026-12-01" in (tmp_path / "CHANGES.md").read_text("utf-8")


def test_catalogue_changes_refuses_a_download_that_lost_procedures(tmp_path, monkeypatch):
    old = built(tmp_path, "2026-11-01")
    new = built(tmp_path, "2026-12-01", **{"2": None})
    with pytest.raises(SystemExit, match="missing from the new catalogue"):
        run(monkeypatch, catalogue_changes.main, old, new,
            "--json", tmp_path / "c.json", "--log", tmp_path / "c.md")
    run(monkeypatch, catalogue_changes.main, old, new, "--force",
        "--json", tmp_path / "c.json", "--log", tmp_path / "c.md")
    assert "withdrawn" in (tmp_path / "c.md").read_text("utf-8")


def test_the_other_prototype_methods_write_journeys(dataset, monkeypatch, capsys):
    import hawkes
    import latent
    import register
    run(monkeypatch, hawkes.main, dataset)
    run(monkeypatch, latent.main, dataset, "--kinds", 6, "--restarts", 1)
    run(monkeypatch, register.main, dataset)
    for name in ("journeys_hawkes.csv", "journeys_register.csv"):
        found = pd.read_csv(dataset / name, dtype={"case_id": str})
        assert {"case_id", "journey_id"} <= set(found.columns) and len(found), name
    assert any(n.startswith("journeys_latent") for n in os.listdir(dataset))
    capsys.readouterr()
    run(monkeypatch, score.main, dataset)
    text = capsys.readouterr().out
    assert "hawkes" in text and "register" in text and "latent" in text


def procedure(**changes):
    p = {"title": "Τίτλος", "title_en": "Title", "org": "ΟΡΓ", "org_en": "ORG",
         "validity": "P1Y", "requires": [{"id": "5", "alternative": False}],
         "steps": [{"n": 1, "parent": None, "exit": False, "min": "PT1M", "max": "PT5M",
                    "title": "Α", "role": "R", "how": "H"},
                   {"n": 2, "parent": None, "exit": True, "min": None, "max": None,
                    "title": "Β", "role": "R", "how": "H"}],
         "digital_steps": []}
    p.update(changes)
    return p


@pytest.mark.parametrize("change, expected", [
    ({"title_en": "Other"}, "title"),
    ({"steps": procedure()["steps"][:1]}, "steps: 2 to 1"),
    ({"steps": [procedure()["steps"][0], {**procedure()["steps"][1], "parent": 1}]},
     "steps: branching"),
    ({"steps": [{**procedure()["steps"][0], "max": "PT9M"}, procedure()["steps"][1]]},
     "steps: durations"),
    ({"steps": [{**procedure()["steps"][0], "role": "Other"}, procedure()["steps"][1]]},
     "steps: names or roles"),
    ({"requires": [{"id": "5", "alternative": False}, {"id": "6", "alternative": False}]},
     "evidence: +6"),
    ({"requires": []}, "evidence: -5"),
    ({"requires": [{"id": "5", "alternative": True}]}, "evidence: alternative flag"),
    ({"validity": None}, "validity: P1Y to none"),
    ({"org_en": "OTHER"}, "owner"),
])
def test_each_kind_of_change_in_a_procedure_is_named(change, expected):
    assert catalogue_changes.differences(procedure(), procedure()) == []
    assert catalogue_changes.differences(procedure(), procedure(**change)) == [expected]


SCRIPTS = ["generate.py", "bundles.py", "build_catalogue.py", "catalogue_changes.py",
           "prototype/discover.py", "prototype/hawkes.py", "prototype/latent.py",
           "prototype/register.py", "prototype/score.py"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_command_line_explains_every_option(script):
    import subprocess
    run = subprocess.run([sys.executable, os.path.join(ROOT, script), "--help"],
                         capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr[-500:]
    lines = run.stdout.splitlines()
    # an option line with nothing after it, and no indented line below, has no help
    for n, line in enumerate(lines):
        if line.startswith("  -") and not line.startswith("  -h, --help"):
            text = line.strip().split("  ", 1)
            has_text = len(text) > 1 and text[1].strip()
            continued = n + 1 < len(lines) and lines[n + 1].startswith(" " * 6)
            assert has_text or continued or "--version" in line, (script, line)
