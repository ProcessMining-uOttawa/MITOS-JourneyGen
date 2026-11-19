"""What the security assessment (bandit) led to: safe XES attributes, a
downloader that only talks to MITOS, and limits a loaded project cannot
exceed."""
import copy
import gzip
import json
import os
import xml.etree.ElementTree as ET  # nosec B405 - parses only what the test itself wrote

import pytest

import build_catalogue
import bundles
import generate as g

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def catalogue():
    return g.Catalogue(os.path.join(ROOT, "catalogue.json.gz"),
                       os.path.join(ROOT, "translations_en.json"))


def bundled():
    with open(os.path.join(ROOT, "scenarios.json"), encoding="utf-8") as f:
        return json.load(f)


def test_xml_attributes_are_escaped_and_cleaned():
    assert g.xml_attr('a "b" <c> & d') == '"a &quot;b&quot; &lt;c&gt; &amp; d"'
    assert g.xml_attr("line\nbreak\ttab") == '"line&#10;break&#9;tab"'
    # characters XML 1.0 forbids are dropped rather than written
    assert g.xml_attr("bell\x07 null\x00 form\x0c") == '"bell null form"'
    assert g.xml_attr(42) == '"42"'
    assert g.xml_attr("Ελληνικά") == '"Ελληνικά"'


def test_an_xes_log_with_awkward_labels_is_well_formed(tmp_path):
    cat = catalogue()
    pid = next(iter(bundled()["scenarios"][0]["procedures"]))["id"]
    cat.procedures[pid]["steps"][0]["title"] = 'Step "one" <a&b>\x07'
    gen = g.Generator(cat, bundled(), 3)
    gen.run(150)
    gen.write(str(tmp_path), "el", {}, "xes", True)
    with gzip.open(tmp_path / "events.xes.gz", "rt", encoding="utf-8") as f:
        root = ET.parse(f).getroot()  # nosec B314 - our own output
    names = {e.get("value") for e in root.iter("{http://www.xes-standard.org/}string")
             if e.get("key") == "concept:name"}
    assert 'Step "one" <a&b>' in names


def test_the_journey_log_xes_is_well_formed():
    import pandas as pd
    log = pd.DataFrame({
        "case:concept:name": ["J1", "J1"], "case:kind": ['a "kind" <x>', 'a "kind" <x>'],
        "concept:name": ["A & B", "C\x01"], "org:role": ["Org", "Org"],
        "start_timestamp": pd.to_datetime(["2024-01-01 10:00", "2024-01-02 10:00"]),
        "time:timestamp": pd.to_datetime(["2024-01-01 11:00", "2024-01-02 11:00"]),
        "case:requests": [2, 2]})
    root = ET.fromstring(gzip.decompress(bundles.to_xes(log)))  # nosec B314 - our own output
    values = {e.get("value") for e in root.iter() if e.get("key") == "concept:name"}
    assert {"A & B", "C"} <= values


def test_the_downloader_only_fetches_from_the_mitos_api():
    for url in ("file:///etc/passwd", "http://api.digigov.grnet.gr/v1/services",
                "https://example.org/v1/services", "ftp://api.digigov.grnet.gr/v1/"):
        with pytest.raises(ValueError):
            build_catalogue._get(url)


def test_a_procedure_id_cannot_name_a_file_outside_the_cache():
    assert build_catalogue._procedure_id(406191) == "406191"
    for bad in ("../../etc/passwd", "12/34", "", "1" * 13, "12a"):
        with pytest.raises(ValueError):
            build_catalogue._procedure_id(bad)


def test_a_download_refuses_a_list_with_a_strange_id(tmp_path, monkeypatch):
    monkeypatch.setattr(build_catalogue, "_paged",
                        lambda endpoint, limit: [{"id": "../x", "title": {"el": "x"}}]
                        if endpoint == "services" else [])
    with pytest.raises(ValueError):
        build_catalogue.download(str(tmp_path))
    assert not (tmp_path / "x.json").exists()


def test_the_bundled_scenarios_are_within_the_limits():
    cat = catalogue()
    assert g.check(cat, bundled()) == []


@pytest.mark.parametrize("change, words", [
    (lambda c: c["window"].update(years=1000), "window"),
    (lambda c: c["scenarios"][0]["rate_per_year"].update({"30-44": 500.0}), "rate for 30-44"),
    (lambda c: c["scenarios"][0]["rate_per_year"].update({"30-44": float("nan")}), "rate"),
    (lambda c: c["scenarios"][0]["procedures"][0].update(p=7), "chance"),
    (lambda c: c["scenarios"][0]["procedures"][0].update(delay_days=[0, 10 ** 6]), "delay"),
    (lambda c: c["background"].update(rate_per_year=10 ** 6), "background"),
    (lambda c: c["behaviour"].update(max_attempts=10 ** 6), "max_attempts"),
    (lambda c: c["behaviour"].update(retry_probability="high"), "retry_probability"),
    (lambda c: c["behaviour"].update(evidence_depth=-1), "evidence_depth"),
])
def test_values_that_would_run_away_stop_a_run(change, words):
    cat = catalogue()
    cfg = copy.deepcopy(bundled())
    change(cfg)
    problems = g.check(cat, cfg)
    assert any(words in p for p in problems), problems
