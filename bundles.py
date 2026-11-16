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

* ``integration_report.md`` and ``integration_opportunities.csv``: what could
  be integrated, as recommendations with the evidence for each, scored, and
  the ten best trade-offs first;
* ``journey_log.xes.gz`` and ``journey_log.csv``: the service-level log, each
  journey carrying the best-ranked opportunity it shows;
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

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def journey_log(inst: pd.DataFrame, members: pd.DataFrame, organisation: dict | None = None,
                min_size: int = 2) -> pd.DataFrame:
    """One row per service instance per journey, in PM4Py's column names.

    ``inst`` has ``case_id, citizen_id, service_id, service, start, end`` and
    optionally ``channel`` and ``outcome`` (``completed`` or not: the status a
    real system records for each request). ``members`` has ``case_id, journey_id`` and
    optionally ``kind`` (the kind of life event, when it is known). Journeys
    of fewer than ``min_size`` instances are left out: a single instance is
    not a bundle.
    """
    m = members.astype({"case_id": str}).drop_duplicates(["case_id", "journey_id"])
    size = m.groupby("journey_id").case_id.transform("size")
    m = m[size >= min_size]
    cols = ["case_id", "citizen_id", "service_id", "service", "start", "end"] + [
        c for c in ("channel", "outcome") if c in inst.columns]
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
    for extra in ("channel", "outcome"):
        if extra in df.columns:
            out[extra] = df[extra]
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


# The kinds of recommendation, numbered by how much each typically changes for
# citizens: 1 reshapes a whole journey, 6 spares a few citizens one request.
# The order is a judgement, not a measurement.
TYPES = {
    "life event": "1. One entry point for the life event",
    "one step": "2. Offer as one step",
    "fetch": "3. Fetch automatically",
    "together": "4. One application for both",
    "reuse": "5. Reuse instead of asking again",
    "start": "6. Fetch when needed",
}
EFFORT = {1: "1 low", 2: "2 moderate", 3: "3 high", 4: "4 very high"}


def pair_evidence(log: pd.DataFrame) -> pd.DataFrame:
    """Everything the log says about each pair of services, per kind.

    For services A and B of a kind: ``with_a``, ``with_b``, ``both`` (journeys
    holding them), ``a_first`` (journeys where the first A comes before the
    first B), ``next_to`` (journeys where B is the next service after A),
    the wait from the end of the first A to the start of the first B, their
    organisations and channels, and ``lift`` over all journeys of every kind.
    One row per ordered pair that occurs. When the log carries the outcome of
    each request, ``requests`` counts the requests for the two services in the
    journeys where A comes first, and ``failed`` those that did not complete.
    """
    firsts = log.sort_values(["case:concept:name", "start_timestamp"], kind="stable").groupby(
        ["case:kind", "case:concept:name", "concept:name"], sort=False).agg(
        start=("start_timestamp", "first"), end=("time:timestamp", "first"),
        org=("org:role", "first"), sid=("service_id", "first"),
        channel=("channel", "first") if "channel" in log.columns else ("org:role", "first")
    ).reset_index()
    firsts.columns = ["kind", "journey", "service", "start", "end", "org", "sid", "channel"]
    p = firsts.merge(firsts, on=["kind", "journey"], suffixes=("_a", "_b"))
    p = p[(p.service_a != p.service_b) & (p.start_a < p.start_b)]
    p = p.assign(wait=((p.start_b - p.end_a).dt.total_seconds() / 86400).clip(lower=0),
                 channel_break=p.channel_a != p.channel_b)
    # B is the next different service after A
    order = log.sort_values(["case:concept:name", "start_timestamp"], kind="stable")
    nxt = order.assign(then=order.groupby("case:concept:name")["concept:name"].shift(-1))
    nxt = nxt[nxt.then.notna() & (nxt.then != nxt["concept:name"])]
    adjacent = nxt.drop_duplicates(["case:concept:name", "concept:name", "then"]).groupby(
        ["case:kind", "concept:name", "then"]).size()

    has = firsts.groupby(["kind", "service"]).size()
    has_all = firsts.drop_duplicates(["journey", "service"]).groupby("service").size()
    journeys_all = firsts.journey.nunique()
    follows_all = p.groupby(["service_a", "service_b"]).journey.nunique()
    out = p.groupby(["kind", "service_a", "service_b"]).agg(
        a_first=("journey", "size"), median_wait_days=("wait", "median"),
        citizen_days=("wait", "sum"), org_a=("org_a", "first"), org_b=("org_b", "first"),
        sid_a=("sid_a", "first"), sid_b=("sid_b", "first"),
        channel_breaks=("channel_break", "mean")).reset_index()
    key_ab = list(zip(out.kind, out.service_a, out.service_b))
    b_first = p.groupby(["kind", "service_a", "service_b"]).size()
    out["b_first"] = [int(b_first.get((k, b, a), 0)) for k, a, b in key_ab]
    out["with_a"] = [int(has[(k, a)]) for k, a, _ in key_ab]
    out["with_b"] = [int(has[(k, b)]) for k, _, b in key_ab]
    out["both"] = out.a_first + out.b_first
    out["next_to"] = [int(adjacent.get(x, 0)) for x in key_ab]
    out["lift"] = [follows_all[(a, b)] / has_all[a] / (has_all[b] / journeys_all)
                   for _, a, b in key_ab]
    # requests for the two services in the journeys that hold A before B, and
    # how many of them failed or were abandoned
    out["requests"] = out["failed"] = 0
    if "outcome" in log.columns:
        n = log.assign(bad=(log.outcome != "completed").astype(int)).groupby(
            ["case:concept:name", "concept:name"]).agg(n=("bad", "size"), bad=("bad", "sum"))
        ka, kb = list(zip(p.journey, p.service_a)), list(zip(p.journey, p.service_b))
        counts = p[["kind", "service_a", "service_b"]].assign(
            requests=n.n.reindex(ka).to_numpy() + n.n.reindex(kb).to_numpy(),
            failed=n.bad.reindex(ka).to_numpy() + n.bad.reindex(kb).to_numpy()).groupby(
            ["kind", "service_a", "service_b"])[["requests", "failed"]].sum()
        out["requests"] = [int(counts.requests[k]) for k in key_ab]
        out["failed"] = [int(counts.failed[k]) for k in key_ab]
    return out


def opportunities(log: pd.DataFrame, requires: dict | None = None, share: float = 0.6,
                  ordered: float = 0.9, min_journeys: int = 10, min_lift: float = 1.5,
                  direct: float = 0.25, kind_share: float = 0.1) -> pd.DataFrame:
    """What could be integrated, read off a journey log.

    Each row is one recommendation with the evidence for it. The kinds of
    recommendation, and the rule behind each:

    * **One entry point for the life event**: for each kind, the services
      present in at least half of its journeys.
    * **Offer as one step**: A comes before B in at least ``ordered`` of the
      journeys with both, B follows in at least ``share`` of the journeys
      with A, and A comes first in at least ``share`` of the journeys with B.
      The two always go together, in that order.
    * **Fetch automatically**: same order, and A comes first in at least
      ``share`` of the journeys with B, but A is often used without B. A is
      an input of B: B could obtain it by itself.
    * **One application for both**: no fixed order, yet at least ``share`` of
      the journeys holding the rarer one hold both.
    * **Reuse instead of asking again**: a service requested more than once
      in at least a fifth of the journeys that hold it.
    * **Fetch when needed**: same order, and B follows in at least ``share``
      of the journeys with A, but B often happens without A. Citizens request
      A almost only for B, and only some of them need it: an occasional input.
      (A log cannot tell this from B being a consequence of A; where B always
      follows and always has A before it, the pair is "one step".)

    Pairs need ``min_journeys`` journeys, at least ``kind_share`` of the
    journeys of their kind, and a ``lift`` of ``min_lift``. For the ordered
    kinds, B must be the next service after A in at least ``direct`` of the
    journeys, which leaves out pairs that only go together through a third
    service. An unordered pair is left out when both services are already
    part of the entry point for the life event: the entry point says it once,
    where the pairs would say it once for every two of its services.

    With the outcome of each request in the log, every recommendation also
    counts the requests concerned that failed or were abandoned. ``requires`` maps a service id to the ids MITOS
    says it requires; with it, each pair says whether the register confirms
    the dependency.

    ``effort_level`` runs from 1 (one organisation) to 4 (across organisations
    with a change of channel). ``score`` weighs reach, savings, certainty and
    effort; see :func:`scored`.
    """
    ev = pair_evidence(log)
    rows = []
    has_outcome = "outcome" in log.columns
    counts = {(r.kind, r.service_a, r.service_b): (r.requests, r.failed)
              for r in ev.itertuples(index=False)}
    by_service = log.assign(bad=(log.outcome != "completed").astype(int)).groupby(
        ["case:kind", "concept:name"]).agg(n=("bad", "size"), bad=("bad", "sum")
                                           ) if has_outcome else None

    def failures(requests: int, failed: int) -> dict:
        return {"failed_requests": int(failed),
                "failure_rate": round(failed / requests, 3) if requests else float("nan")}

    def failure_text(requests: int, failed: int) -> str:
        if not has_outcome or not requests:
            return ""
        return (f" Of the {requests:,} requests concerned, {failed:,} failed or were "
                f"abandoned ({failed / requests:.0%}).")

    # the services at the heart of each kind of journey: its entry point
    per_journey = log.drop_duplicates(["case:concept:name", "concept:name"])
    in_kind = per_journey.groupby("case:kind")["case:concept:name"].nunique()
    core_of = {}
    for kind, grp in per_journey.groupby("case:kind"):
        share_of = grp.groupby("concept:name").size() / in_kind[kind]
        core = share_of[share_of >= 0.5]
        core_of[kind] = set(core.index) if len(core) >= 2 and in_kind[
            kind] >= min_journeys else set()

    def pair_effort(a, b, breaks):
        across = bool(a and b and a != b)
        level = (3 if across else 1) + (1 if breaks >= 0.5 else 0)
        text = f"across organisations ({a} to {b})" if across else "one organisation"
        return level, text + (", with a change of channel" if breaks >= 0.5 else "")

    def register(sid_a, sid_b):
        if requires is None:
            return "not checked"
        return "confirmed by MITOS" if str(sid_a) in requires.get(str(sid_b), ()) else (
            "not in MITOS")

    seen = set()
    for r in ev.itertuples(index=False):
        if (r.both < min_journeys or r.lift < min_lift
                or r.both < kind_share * in_kind[r.kind]):
            continue
        a_before = r.a_first / r.both
        follows, preceded = r.a_first / r.with_a, r.a_first / r.with_b
        kind = None
        if a_before >= ordered:
            if r.next_to / r.a_first < direct:
                continue
            if follows >= share and preceded >= share:
                kind = "one step"
            elif preceded >= share:
                kind = "fetch"
            elif follows >= share:
                kind = "start"
        elif r.both / min(r.with_a, r.with_b) >= share and (1 - ordered) < a_before:
            if (r.kind, r.service_b, r.service_a) in seen:
                continue            # an unordered pair is reported once
            if {r.service_a, r.service_b} <= core_of[r.kind]:
                continue            # the entry point of the life event covers it
            kind = "together"
        if kind is None:
            continue
        seen.add((r.kind, r.service_a, r.service_b))
        a, b = r.service_a, r.service_b
        text = {
            "one step": f"Offer '{a}' and '{b}' as one step: citizens who do the first "
                        f"nearly always go on to the second.",
            "fetch": f"Let '{b}' obtain the result of '{a}' by itself, so the citizen does "
                     f"not have to request it first.",
            "start": f"When '{b}' needs the result of '{a}', let it obtain it by itself: "
                     f"citizens who request '{a}' nearly always do so for '{b}'.",
            "together": f"Offer '{a}' and '{b}' in one application: citizens need both, in "
                        f"either order.",
        }[kind]
        evidence = (f"In {r.a_first:,} of {r.both:,} journeys with both, '{a}' came first "
                    f"({a_before:.0%}). '{b}' followed in {follows:.0%} of the journeys with "
                    f"'{a}'; '{a}' came before in {preceded:.0%} of the journeys with '{b}'. "
                    f"Median wait between them: {r.median_wait_days:.1f} days.")
        level, where = pair_effort(r.org_a, r.org_b, r.channel_breaks)
        requests, failed = r.requests, r.failed
        if kind == "together":      # either order counts
            back = counts.get((r.kind, r.service_b, r.service_a), (0, 0))
            requests, failed = requests + back[0], failed + back[1]
        evidence += failure_text(requests, failed)
        if kind == "together" and requires is not None:
            known = "confirmed" in register(r.sid_a, r.sid_b) + register(r.sid_b, r.sid_a)
            reg = "confirmed by MITOS" if known else "not in MITOS"
        else:
            reg = register(r.sid_a, r.sid_b)
        rows.append({
            "kind": r.kind, "recommendation": TYPES[kind], "what": text,
            "first": a, "then": b, "journeys": int(r.both if kind == "together" else r.a_first),
            "requests_saved": int(r.a_first) if kind != "together" else 0,
            "citizen_days_between": round(float(r.citizen_days)),
            "median_wait_days": round(float(r.median_wait_days), 1),
            "lift": round(float(r.lift), 1), "effort_level": level,
            "difficulty": f"{EFFORT[level]}: {where}", "register": reg, "evidence": evidence,
            **failures(requests, failed)})

    again = repeated(log, min_journeys=max(3, min_journeys // 2))
    for r in again[again.share_repeating >= 0.2].itertuples(index=False):
        rows.append({
            "kind": r.kind, "recommendation": TYPES["reuse"],
            "what": f"Keep the result of '{r.service}' for the rest of the journey: citizens "
                    "request it again although they already have it.",
            "first": r.service, "then": r.service, "journeys": int(r.journeys_repeating_it),
            "requests_saved": int(r.extra_requests), "citizen_days_between": 0,
            "median_wait_days": 0.0, "lift": float("nan"), "effort_level": 1,
            "difficulty": f"{EFFORT[1]}: one organisation",
            "register": "not checked" if requires is None else "does not apply",
            "evidence": f"Requested more than once in {r.journeys_repeating_it:,} of "
                        f"{r.journeys_with_it:,} journeys that hold it "
                        f"({r.share_repeating:.0%}): {r.extra_requests:,} extra requests."
                        + (failure_text(*by_service.loc[(r.kind, r.service)])
                           if has_outcome else ""),
            **(failures(*by_service.loc[(r.kind, r.service)]) if has_outcome
               else failures(0, 0))})

    spans = log.groupby("case:concept:name").agg(a=("start_timestamp", "min"),
                                                 b=("time:timestamp", "max"),
                                                 n=("concept:name", "size"))
    spans["days"] = (spans.b - spans.a).dt.total_seconds() / 86400
    kind_of = log.drop_duplicates("case:concept:name").set_index("case:concept:name")["case:kind"]
    for kind, grp in per_journey.groupby("case:kind"):
        total = grp["case:concept:name"].nunique()
        if total < min_journeys:
            continue
        share_of = grp.groupby("concept:name").size() / total
        core = share_of[share_of >= 0.5].sort_values(ascending=False)
        if len(core) < 2:
            continue
        orgs = sorted({o for o in grp[grp["concept:name"].isin(core.index)]["org:role"] if o})
        mine = spans[kind_of.reindex(spans.index) == kind]
        level = 2 if len(orgs) <= 1 else 3 if len(orgs) <= 3 else 4
        rows.append({
            "kind": kind, "recommendation": TYPES["life event"],
            "what": "Offer one entry point covering " + ", ".join(
                f"'{s}'" for s in core.index) + ".",
            "first": core.index[0], "then": core.index[-1], "journeys": int(total),
            # every journey would make one request instead of one per core service
            "requests_saved": int(round(total * (core.sum() - 1))),
            "citizen_days_between": 0,
            "median_wait_days": round(float(mine.days.median()), 1), "lift": float("nan"),
            "effort_level": level,
            "difficulty": f"{EFFORT[level]}: " + (
                f"{len(orgs)} organisations ({', '.join(orgs)})" if len(orgs) > 1
                else "several services of one organisation"),
            "register": "not checked" if requires is None else "does not apply",
            "evidence": f"{total:,} journeys; each of these services is in at least half of "
                        f"them ({', '.join(f'{v:.0%}' for v in core)}). A journey takes a "
                        f"median {mine.days.median():.0f} days and "
                        f"{mine.n.median():.0f} requests."
                        + (failure_text(*by_service.loc[kind].loc[list(core.index)].sum())
                           if has_outcome else ""),
            **(failures(*by_service.loc[kind].loc[list(core.index)].sum()) if has_outcome
               else failures(0, 0))})
    cols = ["kind", "recommendation", "what", "first", "then", "journeys", "requests_saved",
            "citizen_days_between", "median_wait_days", "lift", "effort_level", "difficulty",
            "register", "evidence", "failed_requests", "failure_rate"]
    out = scored(pd.DataFrame(rows, columns=cols))
    return out.sort_values(["recommendation", "journeys"], ascending=[True, False]
                           ).reset_index(drop=True)


def scored(opp: pd.DataFrame) -> pd.DataFrame:
    """Add a ``score`` from 0 to 100 that trades benefit against effort.

    * benefit: half of it is reach (journeys concerned, against the largest in
      the table), 35% the requests saved, 15% the citizen-days between the two
      services. When the log says which requests failed, 15% goes to the
      failed requests concerned instead, taken evenly from the other three:
      an integration removes the occasions on which those failures happen;
    * certainty: 1 when MITOS confirms the dependency, 0.85 when the register
      was not consulted or does not apply, 0.65 when the pair is not in MITOS;
      and for pairs, less when the lift is below 2;
    * effort: 1 at level 1, then 0.85, 0.7 and 0.55.

    The weights are choices. They make the ranking reproducible and open to
    argument, not right.
    """
    if opp.empty:
        return opp.assign(score=pd.Series(dtype=int))

    def share(col):
        top = opp[col].max()
        return opp[col] / top if top else opp[col] * 0.0

    benefit = (0.5 * share("journeys") + 0.35 * share("requests_saved")
               + 0.15 * share("citizen_days_between"))
    if "failed_requests" in opp.columns and opp.failed_requests.max() > 0:
        benefit = 0.85 * benefit + 0.15 * share("failed_requests")
    certainty = opp.register.map({"confirmed by MITOS": 1.0, "not in MITOS": 0.65}
                                 ).fillna(0.85)
    certainty = certainty * (0.5 + opp.lift.clip(upper=2) / 4).fillna(1.0)
    effort = opp.effort_level.map({1: 1.0, 2: 0.85, 3: 0.7, 4: 0.55})
    return opp.assign(score=(100 * benefit * certainty * effort).round().astype(int))


def top(opp: pd.DataFrame, n: int = 10, per_kind: int = 3, per_type: int = 4) -> pd.DataFrame:
    """The ``n`` best opportunities by score, with at most ``per_kind`` for one
    kind of journey, ``per_type`` of one kind of recommendation and one entry
    for a pair of services, so that the list does not fill up with variations
    of one finding."""
    chosen, per, types, pairs = [], {}, {}, set()
    for r in opp.sort_values(["score", "journeys"], ascending=False).itertuples():
        pair = (r.kind, frozenset((r.first, r.then)))
        if (per.get(r.kind, 0) >= per_kind or pair in pairs
                or types.get(r.recommendation, 0) >= per_type):
            continue
        per[r.kind] = per.get(r.kind, 0) + 1
        types[r.recommendation] = types.get(r.recommendation, 0) + 1
        pairs.add(pair)
        chosen.append(r.Index)
        if len(chosen) == n:
            break
    out = opp.loc[chosen].copy()
    out.insert(0, "rank", range(1, len(out) + 1))
    return out.reset_index(drop=True)


def annotate(log: pd.DataFrame, best: pd.DataFrame) -> pd.DataFrame:
    """Carry the ranking into the journey log, as attributes a process-mining
    tool can filter, partition and count on.

    On every journey (case attributes):

    * ``case:top_opportunity``: the best-ranked opportunity of ``best`` that
      this journey shows, as text, or "none";
    * ``case:opportunity_rank``: its rank, 0 for none;
    * ``case:requests``, ``case:organisations``: how many requests the journey
      took and how many bodies it involved;
    * ``case:repeated_requests``: requests for a service the journey had
      already requested;
    * ``case:failed_requests``: requests that failed or were abandoned, when
      the log carries the outcome of each request (also on every event, as
      ``outcome``).

    On every event:

    * ``repeated_request``: "yes" for a second or later request of a service
      in the journey;
    * ``avoidable_request``: "yes" when a ranked recommendation would make
      this request unnecessary (the input the next service could fetch, or a
      repeat).

    Life-event entry points are left out: every journey of the kind shows one.
    """
    out = log.copy()
    j, s = out["case:concept:name"], out["concept:name"]
    nth = out.groupby([j, s]).cumcount()
    out["repeated_request"] = ["yes" if x else "no" for x in nth]
    first = out[nth == 0].set_index(["case:concept:name", "concept:name"]).start_timestamp
    best_rank = pd.Series(0, index=pd.Index(j.unique(), name="journey"))
    label = pd.Series("none", index=best_rank.index)
    avoidable = pd.Series(False, index=out.index)
    kind = out["case:kind"]
    for r in best.sort_values("rank").itertuples(index=False):
        name = r.recommendation.split(". ", 1)[-1]
        if r.recommendation == TYPES["life event"]:
            continue
        if r.recommendation == TYPES["reuse"]:
            hit = (kind == r.kind) & (s == r.first) & (nth > 0)
            shows = set(j[hit])
            avoidable |= hit
            text = f"#{r.rank} {name}: {r.first}"
        else:
            has_a = first.xs(r.first, level=1) if r.first in first.index.get_level_values(
                1) else pd.Series(dtype="datetime64[ns]")
            has_b = first.xs(r.then, level=1) if r.then in first.index.get_level_values(
                1) else pd.Series(dtype="datetime64[ns]")
            both = has_a.index.intersection(has_b.index)
            if r.recommendation == TYPES["together"]:
                shows = set(both)
            else:
                shows = set(both[(has_a[both] < has_b[both]).to_numpy()])
            shows &= set(j[kind == r.kind])
            if r.recommendation != TYPES["together"]:
                avoidable |= j.isin(shows) & (s == r.first)
            text = f"#{r.rank} {name}: {r.first} + {r.then}"
        fresh = [x for x in shows if best_rank[x] == 0]
        best_rank[fresh] = r.rank
        label[fresh] = text
    out["avoidable_request"] = ["yes" if x else "no" for x in avoidable]
    out["case:top_opportunity"] = j.map(label)
    out["case:opportunity_rank"] = j.map(best_rank).astype(int)
    out["case:requests"] = out.groupby(j)["concept:name"].transform("size").astype(int)
    out["case:organisations"] = out.groupby(j)["org:role"].transform(
        lambda x: x[x != ""].nunique()).astype(int)
    out["case:repeated_requests"] = (nth > 0).groupby(j).transform("sum").astype(int)
    if "outcome" in out.columns:
        out["case:failed_requests"] = (out.outcome != "completed").groupby(j).transform(
            "sum").astype(int)
    return out


def report(opp: pd.DataFrame, log: pd.DataFrame, source: str, per_type: int = 8) -> str:
    """The opportunities as a document an analyst can hand on."""
    lines = ["# Integration opportunities", "",
             f"From {log['case:concept:name'].nunique():,} journeys of "
             f"{log['case:kind'].nunique()} kinds, holding {len(log):,} service instances of "
             f"{log['concept:name'].nunique()} services. Journeys: {source}.", "",
             "> This log is synthetic. The report shows how such findings are reached and "
             "what they would look like; it says nothing about real citizens.", ""]
    if opp.empty:
        return "\n".join(lines + ["No opportunity meets the thresholds. Use a larger log."])

    def entry(n, r):
        text = [f"**{n}. {r.what}**", "",
                f"- Recommendation: {r.recommendation} (score {r.score})",
                f"- Kind of journey: {r.kind}",
                f"- Evidence: {r.evidence}"]
        if r.requests_saved:
            text.append(f"- Requests a citizen would no longer make: {r.requests_saved:,}")
        if r.citizen_days_between:
            text.append(f"- Citizen-days between the two today: {r.citizen_days_between:,}")
        if getattr(r, "failed_requests", 0):
            text.append(f"- Requests concerned that failed or were abandoned: "
                        f"{r.failed_requests:,} ({r.failure_rate:.0%})")
        text.append(f"- Effort: {r.difficulty}")
        if r.register not in ("not checked", "does not apply"):
            text.append(f"- Register: {r.register}"
                        + (" (an undocumented dependency, or a coincidence)"
                           if r.register == "not in MITOS" else ""))
        return text + [""]

    best = top(opp)
    lines += [f"## The {len(best)} best trade-offs", "",
              "Ranked by a score that weighs how many journeys are concerned and what "
              "citizens would save against how sure the finding is and how much effort it "
              "takes. At most three per kind of journey and four of one kind of "
              "recommendation.", ""]
    for r in best.itertuples(index=False):
        lines += entry(r.rank, r)
    counts = opp.recommendation.value_counts()
    meaning = {
        TYPES["life event"]: "The services most journeys of a kind hold.",
        TYPES["one step"]: "Two services citizens always chain, in the same order: the "
                           "second could start by itself when the first ends.",
        TYPES["fetch"]: "One service is an input of another, which could obtain it itself.",
        TYPES["together"]: "Two services citizens need together, in either order.",
        TYPES["reuse"]: "A result the citizen already has is requested again.",
        TYPES["start"]: "A service requested almost only as an occasional input of "
                        "another.",
    }
    lines += ["## All opportunities, by kind of recommendation", "",
              "The kinds are numbered by how much each typically changes for citizens, 1 "
              "the most.", "",
              "| Recommendation | Found | What it means |", "|---|---|---|"]
    for name in TYPES.values():
        if name in counts:
            lines.append(f"| {name} | {counts[name]} | {meaning[name]} |")
    lines.append("")
    for name in TYPES.values():
        part = opp[opp.recommendation == name].sort_values("score", ascending=False)
        if part.empty:
            continue
        lines += [f"### {name}", ""]
        for n, r in enumerate(part.head(per_type).itertuples(index=False), 1):
            lines += entry(n, r)
        if len(part) > per_type:
            lines += [f"And {len(part) - per_type} more in `integration_opportunities.csv`.",
                      ""]
    lines += ["## How to read this", "",
              "- **Score** (0 to 100): half of the benefit is reach, the rest is requests "
              "saved and waiting; it is reduced when the register does not confirm the "
              "pair, when the lift is low, and as effort rises. The weights are choices.",
              "- **Requests saved** counts the requests citizens made that the integration "
              "would make unnecessary.",
              "- **Failed or abandoned** counts, among the requests concerned, those that "
              "did not complete. Each one sent a citizen back to try again.",
              "- **Citizen-days between** adds up the time from the end of the first service "
              "to the start of the second. It is an upper bound on the time saved, not an "
              "estimate of it.",
              "- **Effort** runs from 1 (one organisation) to 4 (across organisations, with "
              "a change of channel).",
              "- A pair **confirmed by MITOS** is one where the register says the second "
              "procedure requires the first. The log shows that it happens and what it "
              "costs; the register says why. A pair **not in MITOS** deserves a look: it may "
              "be a dependency nobody wrote down.",
              "- The thresholds behind each kind of recommendation are choices, documented in "
              "`bundles.py`. Someone who knows the services should set them.", ""]
    return "\n".join(lines)


def requirements(catalogue_path: str | None = None) -> dict:
    """Service id -> ids of the procedures MITOS says it requires."""
    import json
    path = catalogue_path or os.path.join(HERE, "catalogue.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        procs = json.load(f)["procedures"]
    return {pid: {r["id"] for r in p["requires"]} for pid, p in procs.items() if p["requires"]}


def to_csv(log: pd.DataFrame) -> bytes:
    out = log.copy()
    for col in ("start_timestamp", "time:timestamp"):
        out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out.to_csv(index=False, lineterminator="\n").encode("utf-8")


def to_xes(log: pd.DataFrame, compress: bool = True) -> bytes:
    """XES with a trace per journey and, on every event, the end of the
    service instance in ``time:timestamp`` and its start in ``start_timestamp``.
    Columns named ``case:...`` become attributes of the trace, the others
    attributes of the event; whole numbers are written as numbers."""
    def attr(key, value):
        kind = "int" if isinstance(value, (int, np.integer)) and not isinstance(
            value, bool) else "string"
        return f'<{kind} key={quoteattr(key)} value={quoteattr(str(value))}/>'

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
    case_cols = [c for c in log.columns if c.startswith("case:") and c != "case:concept:name"]
    event_cols = [c for c in log.columns if not c.startswith("case:") and c not in (
        "concept:name", "start_timestamp", "time:timestamp")]
    journey = log["case:concept:name"].to_numpy()
    names = log["concept:name"].to_numpy()
    cases = [(c[5:], log[c].to_numpy()) for c in case_cols]
    events = [(c, log[c].to_numpy()) for c in event_cols]
    starts, ends = list(log["start_timestamp"]), list(log["time:timestamp"])
    for i in range(len(log)):
        if i == 0 or journey[i] != journey[i - 1]:
            buf.write(("</trace>\n" if i else "") + "<trace>" + attr("concept:name", journey[i])
                      + "".join(attr(k, v[i]) for k, v in cases))
        buf.write("<event>" + attr("concept:name", names[i])
                  + f'<date key="time:timestamp" value="{stamp(ends[i])}"/>'
                  + f'<date key="start_timestamp" value="{stamp(starts[i])}"/>'
                  + "".join(attr(k, v[i]) for k, v in events) + "</event>")
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
    ap.add_argument("--no-register", action="store_true",
                    help="do not say whether MITOS confirms each dependency")
    args = ap.parse_args()
    truth = pd.read_csv(os.path.join(args.folder, "truth_instances.csv"), dtype=str).fillna("")
    members = (pd.read_csv(args.journeys, dtype={"case_id": str}) if args.journeys
               else true_journeys(truth))
    log = journey_log(truth, members, organisations(truth.service_id), args.min_size)
    opp = opportunities(log, None if args.no_register else requirements())
    best = top(opp)
    source = f"from {os.path.basename(args.journeys)}" if args.journeys else "the true episodes"
    log = annotate(log, best)       # the ranking travels with the log
    files = {"journey_log.xes.gz": to_xes(log), "journey_log.csv": to_csv(log)}
    files["integration_report.md"] = report(opp, log, source).encode("utf-8")
    ranked = opp.merge(best[["kind", "recommendation", "first", "then", "rank"]],
                       how="left", on=["kind", "recommendation", "first", "then"])
    ranked = ranked.rename(columns={"rank": "top_rank"}).sort_values(
        ["top_rank", "score"], ascending=[True, False], na_position="last")
    files["integration_opportunities.csv"] = ranked.to_csv(
        index=False, lineterminator="\n").encode("utf-8")
    for name, table in (("integration_candidates.csv", candidates(log)),
                        ("journey_variants.csv", variants(log)),
                        ("repeated_requests.csv", repeated(log))):
        files[name] = table.to_csv(index=False, lineterminator="\n").encode("utf-8")
    for name, data in files.items():
        with open(os.path.join(args.folder, name), "wb") as f:
            f.write(data)
    print(f"{len(opp)} integration opportunities; "
          f"{log['case:concept:name'].nunique():,} journeys of {args.min_size} or more "
          f"instances, {len(log):,} service instances, {log['concept:name'].nunique()} "
          f"services, {log['case:kind'].nunique()} kinds; wrote {', '.join(files)}")


if __name__ == "__main__":
    main()
