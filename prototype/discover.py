"""Discover journeys: groups of service instances that belong together.

Reads only ``events.csv``. For every ordered pair of services (A, B) it takes
the delays from an A to a later B of the same citizen and fits a two-part
mixture: *related* uses, with a log-normal delay, and *chance* uses, whose
delay is measured from the same two services used by different citizens. A pair is kept when the mixture explains the delays clearly better
than chance alone. Each pair of instances then gets a probability of being
linked. A journey is a densely linked group of instances; an instance that
only bridges two groups belongs to both.

    python discover.py ../out

Writes ``pairs.csv`` (the learnt windows), ``links.csv``, ``journeys.csv``
(overlapping, one row per instance and journey) and ``journeys_partition.csv``
(connected components, one journey per instance) next to the log. Needs numpy and pandas.
"""
from __future__ import annotations

import argparse
import math
import os

import numpy as np
import pandas as pd

SQRT2PI = math.sqrt(2 * math.pi)


def instances(events_csv: str) -> pd.DataFrame:
    """One row per service instance of ``events.csv``: citizen, service, and the
    start of its first step and the end of its last."""
    ev = pd.read_csv(events_csv, usecols=["case_id", "citizen_id", "service_id", "service",
                                           "start_timestamp", "time:timestamp"],
                     parse_dates=["start_timestamp", "time:timestamp"])
    g = ev.groupby("case_id", sort=False)
    return pd.DataFrame({
        "citizen_id": g.citizen_id.first(), "service_id": g.service_id.first(),
        "service": g.service.first(), "start": g.start_timestamp.min(),
        "end": g["time:timestamp"].max()}).reset_index()


def candidate_pairs(inst: pd.DataFrame) -> pd.DataFrame:
    """Every ordered pair of instances of one citizen, with its delay in days."""
    cols = ["case_id", "citizen_id", "service_id", "start"]
    m = inst[cols].merge(inst[cols], on="citizen_id", suffixes=("_a", "_b"))
    m["delay"] = (m.start_b - m.start_a).dt.total_seconds() / 86400
    m = m[(m.delay > 0) & (m.case_id_a != m.case_id_b)].copy()
    m["delay"] = m.delay.clip(lower=1 / 1440)  # one minute
    return m


def lognormal(d, mu, sigma):
    """The log-normal density at ``d`` (days) with parameters ``mu`` and ``sigma``."""
    return np.exp(-(np.log(d) - mu) ** 2 / (2 * sigma ** 2)) / (d * sigma * SQRT2PI)


class Chance:
    """Delay from an A to a later B when the two have nothing to do with
    each other, measured rather than assumed: A's of one citizen are paired
    with B's of *other* citizens. This follows whatever the log's calendar
    looks like (growth, seasons, a ragged end)."""

    BIN = 30.0  # days

    def __init__(self, starts_a, starts_b, window, rng, samples=40000):
        d = rng.choice(starts_b, samples) - rng.choice(starts_a, samples)
        d = d[d > 0]
        edges = np.arange(0, window + self.BIN, self.BIN)
        counts = np.histogram(d, edges)[0] + 0.5
        self.density = counts / (counts.sum() * self.BIN)

    def __call__(self, d):
        i = np.minimum((d / self.BIN).astype(int), len(self.density) - 1)
        return self.density[i]


def fit(d: np.ndarray, chance, iters: int = 300):
    """EM for pi * lognormal + (1 - pi) * chance. Returns pi, mu, sigma, gain."""
    f0 = chance(d)
    ll0 = np.log(f0).sum()
    logd = np.log(d)
    best = None
    # several starts: the related part may sit at hours, days or months
    for q in (0.15, 0.35, 0.6):
        low = logd[logd <= np.quantile(logd, q)]
        mu, sigma, pi = low.mean(), max(low.std(), 0.5), q
        for _ in range(iters):
            f1 = lognormal(d, mu, sigma)
            r = pi * f1 / (pi * f1 + (1 - pi) * f0)
            w = r.sum()
            if w < 1e-6:
                break
            new = (w / len(d), (r * logd).sum() / w)
            sigma = max(math.sqrt((r * (logd - new[1]) ** 2).sum() / w), 0.2)
            done = abs(new[0] - pi) < 1e-6 and abs(new[1] - mu) < 1e-6
            pi, mu = new
            if done:
                break
        ll1 = np.log(pi * lognormal(d, mu, sigma) + (1 - pi) * f0).sum()
        if best is None or ll1 > best[3]:
            best = (pi, mu, sigma, ll1)
    pi, mu, sigma, ll1 = best
    # BIC gain of the mixture (3 more parameters) over chance alone
    return pi, mu, sigma, 2 * (ll1 - ll0) - 3 * math.log(len(d))


def components(n: int, edges) -> np.ndarray:
    """The connected components of ``n`` nodes joined by ``edges`` (union-find):
    an array giving each node the id of its component."""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return np.array([find(i) for i in range(n)])


def percolate(n: int, edges) -> list:
    """Overlapping groups of a graph, by clique percolation with triangles.

    Two links belong to the same group when they are sides of one triangle,
    and groups grow through triangles that share a side. A node that only
    bridges two dense groups therefore ends up in both, instead of merging
    them the way connected components do. A link in no triangle is a group
    of two; a node with no link is a group of one.

    Returns (node, group) pairs.
    """
    adj: dict = {}
    for a, b in edges:
        if a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    edge_id = {}
    for a, nbrs in adj.items():
        for b in nbrs:
            if a < b:
                edge_id[(a, b)] = len(edge_id)
    parent = list(range(len(edge_id)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    eid = lambda a, b: edge_id[(a, b) if a < b else (b, a)]  # noqa: E731
    for (a, b), e in edge_id.items():
        for c in adj[a] & adj[b]:
            for other in (eid(a, c), eid(b, c)):
                ra, rb = find(e), find(other)
                if ra != rb:
                    parent[ra] = rb
    groups: dict = {}
    for (a, b), e in edge_id.items():
        groups.setdefault(find(e), set()).update((a, b))
    member: dict = {}
    for g, nodes in groups.items():
        for x in nodes:
            member.setdefault(x, set()).add(g)
    out = []
    for g, nodes in groups.items():
        if len(nodes) == 2:  # a lone link inside a larger group adds nothing
            a, b = nodes
            if (member[a] & member[b]) - {g}:
                continue
        out += [(x, g) for x in nodes]
    out += [(x, len(edge_id) + x) for x in range(n) if x not in adj]
    return out


def discover(inst: pd.DataFrame, min_pairs: int = 15, min_gain: float = 10.0,
             min_related: float = 8.0, link_at: float = 0.5):
    """Journeys found by the delay-mixture method.

    For each pair of services with at least ``min_pairs`` occurrences, fits
    the delays of one citizen as a mixture of a related (log-normal) part and
    the chance curve measured across citizens; pairs whose fit gains enough
    are related, and instances are linked when they are more likely related
    than not (``link_at``). Returns the related pairs, the links, the
    overlapping journeys, a partition, and the window in days."""
    window = (inst.start.max() - inst.start.min()).total_seconds() / 86400
    cand = candidate_pairs(inst)
    rng = np.random.default_rng(0)
    day = (inst.start - inst.start.min()).dt.total_seconds() / 86400
    starts = {s: v.to_numpy() for s, v in day.groupby(inst.service_id)}
    names = inst.drop_duplicates("service_id").set_index("service_id").service
    rows, params = [], {}
    for (a, b), grp in cand.groupby(["service_id_a", "service_id_b"], sort=False):
        if len(grp) < min_pairs:
            continue
        d = grp.delay.to_numpy()
        null = Chance(starts[a], starts[b], window, rng)
        pi, mu, sigma, gain = fit(d, null)
        if gain < min_gain or pi * len(d) < min_related:
            continue
        params[(a, b)] = (pi, mu, sigma, null)
        rows.append({"service_a": a, "service_b": b, "name_a": names[a], "name_b": names[b],
                     "pairs": len(d), "related_share": round(pi, 3),
                     "related_pairs": round(pi * len(d), 1),
                     "median_delay_days": round(math.exp(mu), 2),
                     "window_from_days": round(math.exp(mu - 1.645 * sigma), 2),
                     "window_to_days": round(math.exp(mu + 1.645 * sigma), 2),
                     "bic_gain": round(gain, 1)})
    pairs = pd.DataFrame(rows).sort_values("related_pairs", ascending=False)

    key = list(zip(cand.service_id_a, cand.service_id_b))
    keep = np.array([k in params for k in key])
    cand = cand[keep].copy()
    kept = [k for k, ok in zip(key, keep) if ok]
    par = np.array([params[k][:3] for k in kept], dtype=float)
    d = cand.delay.to_numpy()
    f0 = np.empty(len(d))
    codes = pd.Series(kept)
    for k, idx in codes.groupby(codes).groups.items():
        f0[idx] = params[k][3](d[idx])
    f1 = par[:, 0] * lognormal(d, par[:, 1], par[:, 2])
    cand["p_link"] = f1 / (f1 + (1 - par[:, 0]) * f0)
    links = cand[cand.p_link >= link_at][["case_id_a", "case_id_b", "citizen_id",
                                          "delay", "p_link"]]

    index = {c: i for i, c in enumerate(inst.case_id)}
    edges = list(zip(links.case_id_a.map(index), links.case_id_b.map(index)))
    cases = inst.case_id.to_numpy()
    partition = pd.DataFrame({"case_id": cases,
                              "journey_id": components(len(inst), edges)})
    over = percolate(len(inst), edges)
    journeys = pd.DataFrame({"case_id": cases[[x for x, _ in over]],
                             "journey_id": [g for _, g in over]})
    return pairs, links, journeys, partition, window


def main() -> None:
    """The command line: ``python prototype/discover.py --help``."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder holding events.csv")
    ap.add_argument("--link-at", type=float, default=0.5)
    args = ap.parse_args()
    inst = instances(os.path.join(args.folder, "events.csv"))
    pairs, links, journeys, partition, window = discover(inst, link_at=args.link_at)
    pairs.to_csv(os.path.join(args.folder, "pairs.csv"), index=False)
    links.to_csv(os.path.join(args.folder, "links.csv"), index=False)
    journeys.to_csv(os.path.join(args.folder, "journeys.csv"), index=False)
    partition.to_csv(os.path.join(args.folder, "journeys_partition.csv"), index=False)
    sizes = journeys.groupby("journey_id").size()
    several = (journeys.groupby("case_id").size() > 1).sum()
    print(f"{len(inst)} instances over {window:.0f} days; {len(pairs)} related service "
          f"pairs; {len(links)} links; {(sizes > 1).sum()} journeys of two or more "
          f"instances, {(sizes == 1).sum()} single instances; {several} instances in "
          f"more than one journey")


if __name__ == "__main__":
    main()
