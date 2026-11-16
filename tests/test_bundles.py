"""From journeys to a service-level log and integration candidates."""
import gzip
import xml.etree.ElementTree as ET

import pandas as pd

import bundles as bd


def instances():
    rows = []

    def add(case, citizen, sid, service, start, end):
        rows.append({"case_id": case, "citizen_id": citizen, "service_id": sid,
                     "service": service, "start": start, "end": end, "channel": "digital"})

    for n in range(12):                      # twelve citizens: photo, then passport
        day = f"2024-01-{n + 1:02d}"
        add(f"a{n}", f"c{n}", "1", "Photo", f"{day} 09:00:00", f"{day} 09:10:00")
        add(f"b{n}", f"c{n}", "2", "Passport", f"2024-02-{n + 1:02d} 09:00:00",
            f"2024-02-{n + 1:02d} 10:00:00")
    add("x", "c0", "1", "Photo", "2024-01-20 09:00:00", "2024-01-20 09:05:00")   # asked again
    add("lone", "c99", "3", "Tax clearance", "2024-03-01 09:00:00", "2024-03-01 09:01:00")
    inst = pd.DataFrame(rows)
    members = pd.DataFrame(
        [{"case_id": f"{p}{n}", "journey_id": n, "kind": "passport"} for n in range(12)
         for p in "ab"] + [{"case_id": "x", "journey_id": 0, "kind": "passport"},
                           {"case_id": "lone", "journey_id": 50, "kind": "other"}])
    return inst, members


def test_a_journey_becomes_a_case_and_a_service_an_activity():
    inst, members = instances()
    log = bd.journey_log(inst, members, {"1": "Ministry A", "2": "Ministry B"})
    assert log["case:concept:name"].nunique() == 12           # the lone instance is no bundle
    first = log[log["case:concept:name"] == "J000001"]
    assert list(first["concept:name"]) == ["Photo", "Photo", "Passport"]
    assert list(first["org:role"]) == ["Ministry A", "Ministry A", "Ministry B"]
    assert (log.start_timestamp <= log["time:timestamp"]).all()
    assert set(log["case:kind"]) == {"passport"}


def test_candidates_say_what_follows_what_and_how_long_citizens_wait():
    inst, members = instances()
    log = bd.journey_log(inst, members, {"1": "Ministry A", "2": "Ministry B"})
    cand = bd.candidates(log, min_journeys=5)
    assert len(cand) == 1
    row = cand.iloc[0]
    assert (row["first"], row["then"], row["journeys"]) == ("Photo", "Passport", 12)
    assert row["b_follows_a"] == 1.0 and bool(row["different_organisations"])
    # the wait runs from the closest photo: 11 journeys wait 31 days, one waits 12
    assert round(row["median_wait_days"]) == 31


def test_sequences_and_repeated_requests():
    inst, members = instances()
    log = bd.journey_log(inst, members)
    v = bd.variants(log)
    assert v.iloc[0]["sequence"] == "Photo > Passport" and v.iloc[0]["journeys"] == 11
    again = bd.repeated(log, min_journeys=1)
    assert list(again.service) == ["Photo"] and again.iloc[0]["extra_requests"] == 1


def test_the_xes_holds_a_trace_per_journey_with_both_timestamps():
    inst, members = instances()
    log = bd.journey_log(inst, members, {"1": "Ministry A", "2": "Ministry B"})
    root = ET.fromstring(gzip.decompress(bd.to_xes(log)))
    ns = {"x": "http://www.xes-standard.org/"}
    traces = root.findall("x:trace", ns)
    assert len(traces) == 12
    event = traces[0].find("x:event", ns)
    keys = {a.get("key"): a.get("value") for a in event}
    assert keys["concept:name"] == "Photo" and keys["org:role"] == "Ministry A"
    assert keys["start_timestamp"] < keys["time:timestamp"]
    assert {a.get("key") for a in traces[0] if a.tag.endswith("string")} == {
        "concept:name", "citizen_id", "kind"}
    assert bd.to_csv(log).decode().splitlines()[0].startswith("case:concept:name,concept:name")


def test_true_episodes_put_a_shared_instance_in_both_journeys():
    truth = pd.DataFrame({
        "case_id": ["s1", "s2", "s3"], "episode_id": ["E1", "E2", ""],
        "shared_with": ["E2", "", "E1"], "scenario": ["birth", "marriage", ""]})
    m = bd.true_journeys(truth)
    assert sorted(zip(m.case_id, m.journey_id)) == [("s1", "E1"), ("s1", "E2"), ("s2", "E2"),
                                                    ("s3", "E1")]
    assert dict(zip(m.journey_id, m.kind))["E2"] == "marriage"
