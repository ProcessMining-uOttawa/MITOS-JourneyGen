"""The rules a generated log promises, checked one by one on generated data.

The byte-for-byte test of ``out/`` notices any change to the generator, but
cannot say what went wrong, and an intended change breaks it too. These tests
state the rules themselves.
"""
import copy
import datetime as dt
import json
import os

import pytest

import generate as g

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = g.Catalogue(os.path.join(ROOT, "catalogue.json.gz"),
                  os.path.join(ROOT, "translations_en.json"))
with open(os.path.join(ROOT, "scenarios.json"), encoding="utf-8") as f:
    BUNDLED = json.load(f)


def generated(citizens=600, seed=11, **behaviour):
    cfg = copy.deepcopy(BUNDLED)
    cfg["behaviour"].update(behaviour)
    gen = g.Generator(CAT, cfg, seed)
    gen.run(citizens)
    return gen


@pytest.fixture(scope="module")
def default_run():
    return generated()


def test_every_step_lasts_between_the_mitos_minimum_and_maximum(default_run):
    checked = 0
    for inst in default_run.instances:
        for step, start, end in inst["events"]:
            lo, hi = g.seconds(step["min"]), g.seconds(step["max"])
            if lo is None or hi is None:
                continue
            took = (end - start).total_seconds()
            assert min(lo, hi) - 1e-6 <= took <= max(max(lo, hi), 1.0) + 1e-6, (step, took)
            checked += 1
    assert checked > 1000


def test_counter_steps_happen_in_office_hours(default_run):
    checked = 0
    for inst in default_run.instances:
        if inst["channel"] != "counter":
            continue
        for step, start, end in inst["events"]:
            if (end - start).total_seconds() < g.DAY:
                assert start.weekday() < 5 and 8 <= start.hour < 15, (step["title"], start)
                checked += 1
    assert checked > 100


def test_no_instance_is_attempted_more_often_than_allowed(default_run):
    most = default_run.b["max_attempts"]
    attempts = [inst["attempt"] for inst in default_run.instances]
    assert max(attempts) <= most
    assert max(attempts) > 1                         # retries do happen
    # failures nearly always and retries always: the limit is what stops them
    stubborn = generated(300, negative_branch_weight=1.0, retry_probability=1.0,
                         max_attempts=2)
    assert max(i["attempt"] for i in stubborn.instances) == 2


def test_failure_branches_follow_their_weight():
    never = generated(negative_branch_weight=0.0, abandon_probability=0.0)
    assert not any(i["outcome"] == "unsuccessful" for i in never.instances)
    always = generated(negative_branch_weight=1.0, abandon_probability=0.0)
    assert any(i["outcome"] == "unsuccessful" for i in always.instances)


def test_reuse_follows_its_probability():
    def shared(gen):
        return sum(1 for i in gen.instances if i["shared"])
    assert shared(generated(1500, reuse_evidence_probability=0.0)) == 0
    assert shared(generated(1500, reuse_evidence_probability=1.0)) > 0


def test_a_certificate_is_reused_only_while_it_is_valid():
    gen = generated(citizens=1)
    pid = next(p for p, v in CAT.procedures.items()
               if v["validity"] is None and p not in gen.b["single_use_evidence"])
    end = dt.datetime(2024, 1, 1)
    held = {"pid": pid, "end": end}
    citizen = {"held": [held]}
    valid_days = gen.b["evidence_validity_days"]
    assert gen.held(citizen, pid, end + dt.timedelta(days=valid_days - 1)) is held
    assert gen.held(citizen, pid, end + dt.timedelta(days=valid_days + 1)) is None
    assert gen.held(citizen, pid, end - dt.timedelta(days=1)) is None   # not yet issued
