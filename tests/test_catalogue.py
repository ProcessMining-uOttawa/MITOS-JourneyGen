"""The catalogue download and comparison, without the network."""
import copy
import gzip
import json
import os

import pytest

import build_catalogue
import catalogue_changes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fake_mitos(monkeypatch, broken=()):
    """Three procedures, served from memory; ``broken`` ids always fail."""
    rows = [{"id": str(i), "title": {"el": f"Διαδικασία {i}"}} for i in (1, 2, 3)]
    calls = []

    def paged(endpoint, limit):
        return rows if endpoint == "services" else []

    def get(url):
        pid = url.rsplit("/", 1)[1]
        calls.append(pid)
        if pid in broken:
            raise OSError("no answer")
        return {"data": {"id": pid, "title": {"el": f"Διαδικασία {pid}"}, "metadata": {
            "process": {}, "process_steps": [{"step_num_id": 1, "step_title": "Βήμα",
                                              "step_exit": True, "step_child": False}]}}}

    monkeypatch.setattr(build_catalogue, "_paged", paged)
    monkeypatch.setattr(build_catalogue, "_get", get)
    monkeypatch.setattr(build_catalogue.time, "sleep", lambda s: None)
    return calls


def test_download_reports_progress_and_resumes(tmp_path, monkeypatch):
    calls = fake_mitos(monkeypatch)
    seen = []
    failed = build_catalogue.download(str(tmp_path), lambda *a: seen.append(a))
    assert failed == []
    assert seen[0] == (0, 0, 0)                      # reading the lists
    assert seen[1:] == [(1, 3, 0), (2, 3, 0), (3, 3, 0)]
    assert sorted(calls) == ["1", "2", "3"]
    assert len(build_catalogue.build(str(tmp_path))["procedures"]) == 3

    calls.clear()                                     # a second run fetches nothing
    assert build_catalogue.download(str(tmp_path)) == []
    assert calls == []


def test_a_document_that_cannot_be_fetched_is_reported(tmp_path, monkeypatch):
    fake_mitos(monkeypatch, broken={"2"})
    seen = []
    failed = build_catalogue.download(str(tmp_path), lambda *a: seen.append(a))
    assert failed == [("proc", "2")]
    assert seen[-1] == (3, 3, 1)


def test_progress_can_stop_the_download(tmp_path, monkeypatch):
    fake_mitos(monkeypatch)

    def stop(done, total, failed):
        if done == 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        build_catalogue.download(str(tmp_path), stop)


def test_comparison_finds_what_changed_and_which_scenarios_it_touches(tmp_path):
    old = catalogue_changes.load(os.path.join(ROOT, "catalogue.json.gz"))
    with open(os.path.join(ROOT, "scenarios.json"), encoding="utf-8") as f:
        scenarios = json.load(f)
    assert not catalogue_changes.compare(old, old, scenarios)["any"]

    new = copy.deepcopy(old)
    new["retrieved"] = "2099-01-01"
    used = scenarios["scenarios"][0]["procedures"][1]["id"]
    gone = scenarios["scenarios"][0]["procedures"][0]["id"]
    new["procedures"][used]["steps"].append(dict(new["procedures"][used]["steps"][-1], n=999))
    del new["procedures"][gone]
    new["procedures"]["0"] = copy.deepcopy(new["procedures"][used])
    r = catalogue_changes.compare(old, new, scenarios)
    assert [x["id"] for x in r["new"]] == ["0"]
    assert [x["id"] for x in r["withdrawn"]] == [gone]
    assert [x["id"] for x in r["changed"]] == [used]
    hit = {(x["id"], x["action"]) for x in r["scenarios"]
           if x["scenario"] == scenarios["scenarios"][0]["label"]}
    assert (gone, True) in hit and (used, False) in hit

    catalogue_changes.record(r, str(tmp_path / "c.json"), str(tmp_path / "C.md"))
    catalogue_changes.record(r, str(tmp_path / "c.json"), str(tmp_path / "C.md"))
    text = (tmp_path / "C.md").read_text(encoding="utf-8")
    assert text.count("# Catalogue changes") == 1 and text.count("## 2099-01-01") == 2
    with gzip.open(tmp_path / "x.gz", "wt", encoding="utf-8") as f:
        json.dump(new, f)
    assert catalogue_changes.load(str(tmp_path / "x.gz"))["retrieved"] == "2099-01-01"
