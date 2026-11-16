"""From journeys to an answer: which services are used together, in what
order, and which pairs would gain most from being integrated.

A journey groups the service instances of one citizen that belong together:
the true episodes of a generated log, or what a method found. This module
changes the level of the log. In ``events.csv`` a case is one service
instance and an activity is a step. Here a **case is a journey and an
activity is a service**, with the start and the end of each instance. That
log can be mined like any other; in PM4Py-UCM it gives a map of the services
citizens chain together, with the organisations as components.

    python bundles.py out/                               # from the true episodes
    python bundles.py out/ --journeys out/journeys_hawkes.csv

Writes, next to the log:

* ``journey_log.xes.gz`` and ``journey_log.csv``: the service-level log;
* ``integration_candidates.csv``: ordered pairs of services used together,
  with how often, how much more than chance, the wait between them, and
  whether they belong to different organisations;
* ``journey_variants.csv``: the most common sequences of services;
* ``repeated_requests.csv``: services requested more than once in a journey.

Needs pandas.
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
from xml.sax.saxutils import quoteattr

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def journey_log(inst: pd.DataFrame, members: pd.DataFrame, organisation: dict | None = None,
                min_size: int = 2) -> pd.DataFrame:
    """One row per service instance per journey, in PM4Py's column names.

    ``inst`` has ``case_id, citizen_id, service_id, service, start, end`` and
    optionally ``channel``. ``members`` has ``case_id, journey_id`` and
    optionally ``kind`` (the kind of life event, when it is known). Journeys
    of fewer than ``min_size`` instances are left out: a single instance is
    not a bundle.
    """
    m = members.astype({"case_id": str}).drop_duplicates(["case_id", "journey_id"])
    size = m.groupby("journey_id").case_id.transform("size")
    m = m[size >= min_size]
    cols = ["case_id", "citizen_id", "service_id", "service", "start", "end"] + (
        ["channel"] if "channel" in inst.columns else [])
    df = m.merge(inst[cols].astype({"case_id": str}), on="case_id")
    df["start"], df["end"] = pd.to_datetime(df.start), pd.to_datetime(df.end)
    # journey ids as short names, numbered by when the journey begins
    first = df.groupby("journey_id").start.min().sort_values(kind="stable")
    name = {j: f"J{n:06d}" for n, j in enumerate(first.index, 1)}
    out = pd.DataFrame({
        "case:concept:name": df.journey_id.map(name),
        "concept:name": df.service,
        "start_timestamp": df.start,
        "time:timestamp": df.end,
        "org:role": df.service_id.astype(str).map(organisation or {}).fillna(""),
        "service_id": df.service_id.astype(str),
        "service_instance": df.case_id,
        "case:citizen_id": df.citizen_id,
        "case:kind": df.kind.astype(str) if "kind" in df.columns else "journey",
    })
    if "channel" in df.columns:
        out["channel"] = df.channel
    return out.sort_values(["case:concept:name", "start_timestamp", "time:timestamp"],
                           kind="stable").reset_index(drop=True)


def candidates(log: pd.DataFrame, min_journeys: int = 10) -> pd.DataFrame:
    """Ordered pairs of services used in the same journey, A before B.

    * ``journeys``: journeys in which an A is followed, at any distance, by a B;
    * ``b_follows_a``: of the journeys with an A, the share where a B follows;
    * ``lift``: over all journeys of every kind, the share of journeys with an
      A where a B follows, against the share of journeys that hold a B. Above
      1, the two go together more than B's popularity explains;
    * ``median_wait_days``: from the end of A to the start of B;
    * ``citizen_days_waiting``: that wait summed over the journeys;
    * ``different_organisations``: the two belong to different bodies.
    """
    a = log[["case:concept:name", "case:kind", "concept:name", "org:role", "start_timestamp",
             "time:timestamp"]]
    a.columns = ["journey", "kind", "service", "org", "start", "end"]
    pairs = a.merge(a, on=["journey", "kind"], suffixes=("_a", "_b"))
    pairs = pairs[(pairs.service_a != pairs.service_b) & (pairs.start_a < pairs.start_b)]
    pairs = pairs.assign(wait=((pairs.start_b - pairs.end_a).dt.total_seconds() / 86400)
                         .clip(lower=0))
    # the closest A before each B in a journey: one wait per journey and pair
    pairs = pairs.sort_values("wait").drop_duplicates(["journey", "service_a", "service_b"])
    once = a.drop_duplicates(["journey", "service"])
    has = once.groupby(["kind", "service"]).size()
    has_any = once.groupby("service").size()
    follows_any = pairs.groupby(["service_a", "service_b"]).journey.nunique()
    all_journeys = a.journey.nunique()
    g = pairs.groupby(["kind", "service_a", "service_b"])
    out = g.agg(journeys=("journey", "size"), median_wait_days=("wait", "median"),
                citizen_days_waiting=("wait", "sum"), org_a=("org_a", "first"),
                org_b=("org_b", "first")).reset_index()
    out = out[out.journeys >= min_journeys]
    with_a = [has[(k, s)] for k, s in zip(out.kind, out.service_a)]
    out["b_follows_a"] = out.journeys / with_a
    out["lift"] = [follows_any[(x, y)] / has_any[x] / (has_any[y] / all_journeys)
                   for x, y in zip(out.service_a, out.service_b)]
    out["different_organisations"] = (out.org_a != out.org_b) & (out.org_a != "") & (
        out.org_b != "")
    out = out.rename(columns={"service_a": "first", "service_b": "then",
                              "org_a": "organisation_first", "org_b": "organisation_then"})
    out = out.round({"median_wait_days": 1, "citizen_days_waiting": 0, "b_follows_a": 3,
                     "lift": 2})
    cols = ["kind", "first", "then", "journeys", "b_follows_a", "lift", "median_wait_days",
            "citizen_days_waiting", "different_organisations", "organisation_first",
            "organisation_then"]
    return out[cols].sort_values(["journeys", "lift"], ascending=False).reset_index(drop=True)


def variants(log: pd.DataFrame, top: int = 20) -> pd.DataFrame:
    """The most common sequences of services, per kind of journey."""
    seq = log.groupby(["case:kind", "case:concept:name"])["concept:name"].agg(" > ".join)
    span = log.groupby("case:concept:name").agg(a=("start_timestamp", "min"),
                                                b=("time:timestamp", "max"))
    days = ((span.b - span.a).dt.total_seconds() / 86400)
    t = seq.reset_index(name="sequence")
    t["days"] = t["case:concept:name"].map(days)
    out = t.groupby(["case:kind", "sequence"]).agg(journeys=("days", "size"),
                                                   median_days=("days", "median")).reset_index()
    out["share_of_kind"] = (out.journeys / out.groupby("case:kind").journeys.transform("sum")
                            ).round(3)
    out["services"] = out.sequence.str.count(" > ") + 1
    out = out.sort_values(["case:kind", "journeys"], ascending=[True, False])
    out = out.groupby("case:kind").head(top).rename(columns={"case:kind": "kind"})
    return out[["kind", "journeys", "share_of_kind", "services", "median_days", "sequence"]
               ].round({"median_days": 1}).reset_index(drop=True)


def repeated(log: pd.DataFrame, min_journeys: int = 5) -> pd.DataFrame:
    """Services requested more than once in the same journey: a citizen asking
    again for something they already asked for."""
    n = log.groupby(["case:kind", "case:concept:name", "concept:name"]).size()
    n = n.reset_index(name="times")
    g = n.groupby(["case:kind", "concept:name"])
    out = g.agg(journeys_with_it=("times", "size"),
                journeys_repeating_it=("times", lambda s: int((s > 1).sum())),
                extra_requests=("times", lambda s: int((s - 1).sum()))).reset_index()
    out = out[out.journeys_repeating_it >= min_journeys]
    out["share_repeating"] = (out.journeys_repeating_it / out.journeys_with_it).round(3)
    return out.rename(columns={"case:kind": "kind", "concept:name": "service"}).sort_values(
        "extra_requests", ascending=False).reset_index(drop=True)


def to_csv(log: pd.DataFrame) -> bytes:
    out = log.copy()
    for col in ("start_timestamp", "time:timestamp"):
        out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out.to_csv(index=False, lineterminator="\n").encode("utf-8")


def to_xes(log: pd.DataFrame, compress: bool = True) -> bytes:
    """XES with a trace per journey and, on every event, the end of the
    service instance in ``time:timestamp`` and its start in ``start_timestamp``."""
    attr = lambda kind, key, value: f'<{kind} key={quoteattr(key)} value={quoteattr(str(value))}/>'  # noqa: E731,E501
    stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"  # noqa: E731
    buf = io.StringIO()
    buf.write('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<log xes.version="1.0" xes.features="" xmlns="http://www.xes-standard.org/">\n'
              '<extension name="Concept" prefix="concept" '
              'uri="http://www.xes-standard.org/concept.xesext"/>\n'
              '<extension name="Time" prefix="time" '
              'uri="http://www.xes-standard.org/time.xesext"/>\n'
              '<extension name="Organizational" prefix="org" '
              'uri="http://www.xes-standard.org/org.xesext"/>\n'
              '<classifier name="Activity" keys="concept:name"/>\n')
    channel = log["channel"].to_numpy() if "channel" in log.columns else None
    journey = log["case:concept:name"].to_numpy()
    cols = [log[c].to_numpy() for c in ("concept:name", "org:role", "service_id",
                                        "service_instance", "case:citizen_id", "case:kind")]
    starts, ends = list(log["start_timestamp"]), list(log["time:timestamp"])
    for i in range(len(log)):
        if i == 0 or journey[i] != journey[i - 1]:
            buf.write(("</trace>\n" if i else "") + "<trace>"
                      + attr("string", "concept:name", journey[i])
                      + attr("string", "citizen_id", cols[4][i])
                      + attr("string", "kind", cols[5][i]))
        buf.write("<event>" + attr("string", "concept:name", cols[0][i])
                  + attr("date", "time:timestamp", stamp(ends[i]))
                  + attr("date", "start_timestamp", stamp(starts[i]))
                  + attr("string", "org:role", cols[1][i])
                  + attr("string", "service_id", cols[2][i])
                  + attr("string", "service_instance", cols[3][i])
                  + (attr("string", "channel", channel[i]) if channel is not None else "")
                  + "</event>")
    buf.write(("</trace>\n" if len(log) else "") + "</log>\n")
    raw = buf.getvalue().encode("utf-8")
    return gzip.compress(raw, mtime=0) if compress else raw


def organisations(service_ids, catalogue_path: str | None = None) -> dict:
    """Service id -> the body that owns the procedure, from the catalogue."""
    import json
    path = catalogue_path or os.path.join(HERE, "catalogue.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        procs = json.load(f)["procedures"]
    return {str(i): (procs[str(i)]["org_en"] or procs[str(i)]["org"] or "")
            for i in set(map(str, service_ids)) if str(i) in procs}


def true_journeys(truth: pd.DataFrame) -> pd.DataFrame:
    """The true episodes as journeys, from the rows of ``truth_instances.csv``;
    an instance shared by two episodes is in both."""
    t = truth.fillna("")
    eps = (t.episode_id + ";" + t.shared_with).str.strip(";").str.split(";")
    m = pd.DataFrame({"case_id": t.case_id.astype(str), "journey_id": eps}).explode("journey_id")
    m = m[m.journey_id != ""]
    kind = t[t.episode_id != ""].drop_duplicates("episode_id").set_index("episode_id").scenario
    return m.assign(kind=m.journey_id.map(kind).fillna("unknown"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder holding truth_instances.csv")
    ap.add_argument("--journeys", help="a journeys CSV (case_id, journey_id[, kind]) from a "
                                       "method; without it, the true episodes are used")
    ap.add_argument("--min-size", type=int, default=2)
    args = ap.parse_args()
    truth = pd.read_csv(os.path.join(args.folder, "truth_instances.csv"), dtype=str).fillna("")
    members = (pd.read_csv(args.journeys, dtype={"case_id": str}) if args.journeys
               else true_journeys(truth))
    log = journey_log(truth, members, organisations(truth.service_id), args.min_size)
    files = {"journey_log.xes.gz": to_xes(log), "journey_log.csv": to_csv(log)}
    for name, table in (("integration_candidates.csv", candidates(log)),
                        ("journey_variants.csv", variants(log)),
                        ("repeated_requests.csv", repeated(log))):
        files[name] = table.to_csv(index=False, lineterminator="\n").encode("utf-8")
    for name, data in files.items():
        with open(os.path.join(args.folder, name), "wb") as f:
            f.write(data)
    print(f"{log['case:concept:name'].nunique():,} journeys of {args.min_size} or more "
          f"instances, {len(log):,} service instances, {log['concept:name'].nunique()} "
          f"services, {log['case:kind'].nunique()} kinds; wrote {', '.join(files)}")


if __name__ == "__main__":
    main()
