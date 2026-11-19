"""The remarks on unrealistic values, and the checks that stop a run."""
import copy
import json
import os

import generate as g

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = g.Catalogue(os.path.join(ROOT, "catalogue.json.gz"),
                  os.path.join(ROOT, "translations_en.json"))
with open(os.path.join(ROOT, "scenarios.json"), encoding="utf-8") as f:
    BUNDLED = json.load(f)


def texts(cfg, where, **kw):
    return " | ".join(r["text"] for r in g.review(CAT, cfg, **kw) if r["where"] == where)


def test_the_bundled_scenarios_draw_no_remark():
    assert g.review(CAT, BUNDLED) == []
    assert g.check(CAT, BUNDLED) == []


def test_a_procedure_waiting_for_a_later_one_is_caught():
    cfg = copy.deepcopy(BUNDLED)
    first, second = cfg["scenarios"][0]["procedures"][:2]
    first["after"] = [second["id"]]
    assert "listed after it" in texts(cfg, "scenario:0")


def test_rates_that_repeat_a_life_event_are_caught():
    cfg = copy.deepcopy(BUNDLED)
    cfg["scenarios"][0]["rate_per_year"] = {"45-59": 0.4}
    assert "twice or more" in texts(cfg, "scenario:0")
    # the multiplier counts too
    assert "twice or more" in texts(BUNDLED, "scenario:0", rate_scale=10)
    cfg["scenarios"][0]["rate_per_year"] = {}
    assert "never happens" in texts(cfg, "scenario:0")


def test_thin_scenarios_and_long_waits_are_caught():
    cfg = copy.deepcopy(BUNDLED)
    cfg["scenarios"][0]["procedures"] = [{"id": "356865", "p": 0.4, "delay_days": [0, 2000]}]
    said = texts(cfg, "scenario:0")
    assert "single instance" in said and "longer than the" in said
    cfg["scenarios"][0]["procedures"][0]["p"] = 0
    assert "never starts" in texts(cfg, "scenario:0")


def test_behaviour_and_background_extremes_are_caught():
    cfg = copy.deepcopy(BUNDLED)
    cfg["behaviour"].update(reuse_evidence_probability=0, abandon_probability=0.5,
                            evidence_probability=0, alternative_evidence_probability=0)
    cfg["background"]["rate_per_year"] = 0
    said = texts(cfg, "behaviour")
    assert "No result is ever reused" in said and "abandoned" in said
    assert "No evidence is ever requested" in said
    assert "no background use" in texts(cfg, "background")


def test_a_small_population_is_noted_per_scenario():
    said = texts(BUNDLED, "scenario:6", n_citizens=300)
    assert "too few" in said
    assert "small log" in texts(BUNDLED, "run", n_citizens=300)


def test_check_stops_what_cannot_run():
    cfg = copy.deepcopy(BUNDLED)
    cfg["background"]["services"] = {}
    cfg["population"]["age_groups"] = {a: 0 for a in cfg["population"]["age_groups"]}
    problems = " ".join(g.check(CAT, cfg))
    assert "no service to draw from" in problems and "no citizens" in problems


def test_a_procedure_waiting_for_one_the_scenario_does_not_list_stops_a_run():
    cfg = copy.deepcopy(BUNDLED)
    first = cfg["scenarios"][0]["procedures"][0]
    first["after"] = ["123456789"]
    problems = g.check(CAT, cfg)
    assert len(problems) == 1
    assert f"procedure {first['id']} waits for 123456789" in problems[0]
    assert "does not list" in problems[0]
