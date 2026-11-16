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


def chain(n, steps, kind, start_day=1, org=None):
    """n journeys, each going through ``steps`` (service, day offset) in order."""
    rows, members = [], []
    for j in range(n):
        for k, (service, day) in enumerate(steps):
            case = f"{kind}-{j}-{k}"
            t = pd.Timestamp("2024-01-01") + pd.Timedelta(days=start_day + j + day)
            rows.append({"case_id": case, "citizen_id": f"{kind}{j}",
                         "service_id": service, "service": service,
                         "start": t, "end": t + pd.Timedelta(hours=1), "channel": "digital"})
            members.append({"case_id": case, "journey_id": f"{kind}{j}", "kind": kind})
    return rows, members


def opportunity_log():
    rows, members = [], []
    for part in (
        chain(20, [("Birth act", 0), ("Allowance", 5)], "birth"),          # always chained
        chain(14, [("Certificate", 0), ("Pension", 3)], "pension"),        # an input ...
        chain(12, [("Certificate", 0), ("Other use", 2)], "other"),        # ... used elsewhere
        chain(4, [("Rare paper", 0), ("Licence", 2)], "licence"),          # occasional input
        chain(12, [("Licence", 0)], "licence2"),
        chain(8, [("Address", 0), ("Residence", 4)], "move"),              # either order
        chain(8, [("Residence", 0), ("Address", 4)], "moveb"),
        chain(10, [("Death act", 0), ("Death act", 6), ("Funeral", 9)], "death"),
    ):
        rows += part[0]
        members += part[1]
    m = pd.DataFrame(members)
    m.loc[m.kind == "other", "kind"] = "pension"       # the certificate serves two purposes
    m.loc[m.kind == "licence2", "kind"] = "licence"
    m.loc[m.kind == "moveb", "kind"] = "move"
    # journeys of one service are bundles of nothing; give the lone licences a companion
    extra = chain(12, [("Licence", 0), ("Stamp", 1)], "licence3")
    rows = [r for r in rows if not r["case_id"].startswith("licence2")] + extra[0]
    m = pd.concat([m[~m.case_id.str.startswith("licence2")],
                   pd.DataFrame(extra[1]).assign(kind="licence")])
    orgs = {"Birth act": "Interior", "Allowance": "Welfare"}
    return bd.journey_log(pd.DataFrame(rows), m, orgs)


def test_each_kind_of_recommendation_is_recognised():
    opp = bd.opportunities(opportunity_log(), min_journeys=4)
    found = {(r.recommendation, r.first, r.then) for r in opp.itertuples()}
    assert ("2. Offer as one step", "Birth act", "Allowance") in found
    assert ("3. Fetch automatically", "Certificate", "Pension") in found
    assert ("6. Fetch when needed", "Rare paper", "Licence") in found
    assert ("5. Reuse instead of asking again", "Death act", "Death act") in found
    together = {frozenset((r.first, r.then)) for r in opp.itertuples()
                if r.recommendation == "4. One application for both"}
    assert frozenset(("Address", "Residence")) in together
    assert sum(1 for r in opp.itertuples() if r.recommendation == "4. One application for both"
               and {r.first, r.then} == {"Address", "Residence"}) == 1
    entry = opp[(opp.recommendation == "1. One entry point for the life event")
                & (opp.kind == "birth")].iloc[0]
    assert "Birth act" in entry.what and "Allowance" in entry.what


def test_evidence_effort_and_the_register_are_reported():
    log = opportunity_log()
    opp = bd.opportunities(log, {"Allowance": {"Birth act"}}, min_journeys=4)
    pairs = opp[~opp.recommendation.isin([bd.TYPES["life event"], bd.TYPES["reuse"]])]
    birth = pairs[(pairs["first"] == "Birth act") & (pairs.then == "Allowance")].iloc[0]
    assert birth.journeys == 20 and birth.requests_saved == 20
    assert birth.register == "confirmed by MITOS"
    assert birth.difficulty == "3 high: across organisations (Interior to Welfare)"
    assert birth.effort_level == 3
    assert "20 of 20" in birth.evidence and round(birth.median_wait_days) == 5
    pension = pairs[(pairs["first"] == "Certificate") & (pairs.then == "Pension")].iloc[0]
    assert pension.register == "not in MITOS"
    again = opp[opp.recommendation == "5. Reuse instead of asking again"].iloc[0]
    assert again.requests_saved == 10 and again.difficulty.startswith("1 low")
    text = bd.report(opp, log, "a test")
    assert text.startswith("# Integration opportunities") and "synthetic" in text
    assert "### 3. Fetch automatically" in text and "confirmed by MITOS" in text
    assert "best trade-offs" in text
    assert bd.opportunities(log, min_journeys=1000).empty


def test_the_kinds_and_the_effort_are_numbered_in_order():
    names = list(bd.TYPES.values())
    assert [n.split(". ")[0] for n in names] == ["1", "2", "3", "4", "5", "6"]
    assert names[0].endswith("One entry point for the life event")
    assert [bd.EFFORT[k].split()[0] for k in (1, 2, 3, 4)] == ["1", "2", "3", "4"]


def test_the_score_rewards_reach_and_certainty_and_charges_effort():
    base = {"kind": "k", "recommendation": bd.TYPES["fetch"], "what": "", "first": "A",
            "then": "B", "journeys": 100, "requests_saved": 100, "citizen_days_between": 0,
            "median_wait_days": 0.0, "lift": 3.0, "effort_level": 1, "difficulty": "",
            "register": "confirmed by MITOS", "evidence": ""}
    rows = [base, {**base, "then": "C", "effort_level": 4},
            {**base, "then": "D", "register": "not in MITOS"},
            {**base, "then": "E", "journeys": 20, "requests_saved": 20}]
    scores = dict(zip("BCDE", bd.scored(pd.DataFrame(rows)).score))
    assert scores["B"] > scores["D"] > scores["C"] and scores["B"] > scores["E"]
    assert scores["B"] == 85 and scores["C"] == round(85 * 0.55)


def test_the_top_list_is_ranked_and_varied():
    opp = bd.opportunities(opportunity_log(), min_journeys=4)
    best = bd.top(opp, n=5, per_kind=1)
    assert list(best["rank"]) == list(range(1, len(best) + 1))
    assert list(best.score) == sorted(best.score, reverse=True)
    assert best.kind.is_unique                       # one per kind of journey here
    capped = bd.top(opp, n=10, per_type=1)
    assert capped.recommendation.is_unique


def test_the_ranking_travels_with_the_journey_log():
    log = opportunity_log()
    opp = bd.opportunities(log, min_journeys=4)
    best = bd.top(opp[opp.recommendation != bd.TYPES["life event"]], n=10)
    out = bd.annotate(log, best)
    cases = out.drop_duplicates("case:concept:name").set_index("case:concept:name")
    birth = cases[cases["case:kind"] == "birth"]
    rank = int(best[(best["first"] == "Birth act") & (best.then == "Allowance")]["rank"].iloc[0])
    assert (birth["case:opportunity_rank"] == rank).all()
    assert birth["case:top_opportunity"].iloc[0] == (
        f"#{rank} Offer as one step: Birth act + Allowance")
    assert (birth["case:requests"] == 2).all() and (birth["case:organisations"] == 2).all()
    death = out[out["case:kind"] == "death"]
    assert (death.groupby("case:concept:name").repeated_request.apply(
        lambda s: (s == "yes").sum()) == 1).all()
    assert (cases[cases["case:kind"] == "death"]["case:repeated_requests"] == 1).all()
    # the request a recommendation removes is marked; the one it keeps is not
    assert set(out[(out["case:kind"] == "birth") & (out["concept:name"] == "Birth act")]
               .avoidable_request) == {"yes"}
    assert set(out[(out["case:kind"] == "birth") & (out["concept:name"] == "Allowance")]
               .avoidable_request) == {"no"}

    root = ET.fromstring(gzip.decompress(bd.to_xes(out)))
    ns = {"x": "http://www.xes-standard.org/"}
    trace = root.find("x:trace", ns)
    kinds = {a.get("key"): a.tag.split("}")[1] for a in trace if "event" not in a.tag}
    assert kinds["opportunity_rank"] == "int" and kinds["requests"] == "int"
    assert kinds["top_opportunity"] == "string"
    event = {a.get("key") for a in trace.find("x:event", ns)}
    assert {"avoidable_request", "repeated_request", "org:role"} <= event
