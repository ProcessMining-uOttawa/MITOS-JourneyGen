"""The scores of a journey-discovery method, worked out by hand."""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "prototype"))

import score  # noqa: E402


def members(groups: dict, key: str) -> pd.DataFrame:
    return pd.DataFrame([{"case_id": c, key: g} for g, cases in groups.items() for c in cases])


def test_pair_scores_on_a_case_worked_out_by_hand():
    # Truth: E1 = {a, b, c}, E2 = {d, e}: 4 pairs (ab, ac, bc, de).
    # Found: J1 = {a, b, c, d}, J2 = {e}: 6 pairs (ab, ac, ad, bc, bd, cd),
    # 3 of them right (ab, ac, bc). Precision 3/6, recall 3/4, F1 0.6.
    truth = members({"E1": "abc", "E2": "de"}, "episode")
    found = members({"J1": "abcd", "J2": "e"}, "journey_id")
    s = score.score(truth, found, shared=set())
    assert s["precision"] == pytest.approx(0.5)
    assert s["recall"] == pytest.approx(0.75)
    assert s["F1"] == pytest.approx(0.6)
    assert s["whole"] == pytest.approx(0.5)       # E1 lies inside J1; E2 is split
    assert s["clean"] == pytest.approx(0.5)       # J2 lies inside E2; J1 mixes two
    assert s["in 2+ journeys"] == 0
    assert pd.isna(s["shared found"])


def test_a_shared_instance_is_found_only_when_it_is_in_two_journeys():
    truth = members({"E1": "abc", "E2": "cd"}, "episode")      # c serves both
    once = members({"J1": "abc", "J2": "d"}, "journey_id")
    twice = members({"J1": "abc", "J2": "cd"}, "journey_id")
    assert score.score(truth, once, {"c"})["shared found"] == 0.0
    s = score.score(truth, twice, {"c"})
    assert s["shared found"] == 1.0 and s["in 2+ journeys"] == 1
    assert s["precision"] == s["recall"] == 1.0


def test_a_perfect_and_an_empty_answer():
    truth = members({"E1": "abc", "E2": "de"}, "episode")
    perfect = score.score(truth, members({"X": "abc", "Y": "de"}, "journey_id"), set())
    assert perfect["precision"] == perfect["recall"] == perfect["F1"] == 1.0
    singles = score.score(truth, members({i: c for i, c in enumerate("abcde")}, "journey_id"),
                          set())
    assert singles["recall"] == 0.0 and singles["F1"] == 0.0


def test_a_fixed_gap_starts_a_new_journey_after_a_quiet_spell():
    t = pd.Timestamp("2024-01-01")
    inst = pd.DataFrame({
        "case_id": ["a", "b", "c", "d"], "citizen_id": ["x", "x", "x", "y"],
        "start": [t, t + pd.Timedelta(days=3), t + pd.Timedelta(days=40), t],
        "end": [t + pd.Timedelta(hours=1), t + pd.Timedelta(days=3, hours=1),
                t + pd.Timedelta(days=40, hours=1), t + pd.Timedelta(hours=1)]})
    j = dict(zip(*score.fixed_gap(inst, 30).T.to_numpy()))
    assert j["a"] == j["b"] != j["c"]             # 3 days apart, then 37 days
    assert j["d"] not in (j["a"], j["c"])         # another citizen, another journey
