"""Discover journeys with a multivariate Hawkes process.

Reads only ``events.csv``. Each citizen is one realisation of a point process
whose events are service instances. An instance of service B is either
*spontaneous* (background rate of B, which may vary over the calendar and
with how active the citizen is) or *triggered* by one earlier instance of the
same citizen. An instance of A triggers on average ``alpha[A, B]`` instances
of B, after a delay with a log-normal density of its own.

Fitted by expectation-maximisation over the branching structure: the E-step
gives, for every instance, the probability of each possible parent and of
being spontaneous; the M-step re-estimates the rates and delay densities.
A journey is a tree of that structure: an instance joins its most likely
parent unless it is more likely spontaneous than triggered.

    python hawkes.py ../out

Writes ``hawkes_pairs.csv``, ``journeys_hawkes.csv`` (overlapping: an instance
with a second likely parent in another journey belongs to both) and
``journeys_hawkes_partition.csv`` next to the log.
Needs numpy, pandas and scipy.
"""
from __future__ import annotations

import argparse
import math
import os

import numpy as np
import pandas as pd
from scipy.special import erf

from discover import candidate_pairs, components, instances, lognormal

BIN = 30.0  # days, resolution of the background rate over the calendar


def fit(inst: pd.DataFrame, min_pairs: int = 15, min_triggered: float = 5.0,
        iters: int = 150, also_parent: float = 0.25, verbose: bool = False):
    inst = inst.reset_index(drop=True)
    t0 = inst.start.min()
    day = ((inst.start - t0).dt.total_seconds() / 86400).to_numpy()
    horizon = day.max()
    services = np.sort(inst.service_id.unique())
    S = len(services)
    code = pd.Series(np.arange(S), index=services)
    svc = code[inst.service_id].to_numpy()
    citizens, cit = np.unique(inst.citizen_id, return_inverse=True)
    n_cit = len(citizens)
    n_bins = int(horizon // BIN) + 1
    tbin = np.minimum((day // BIN).astype(int), n_bins - 1)
    row = pd.Series(np.arange(len(inst)), index=inst.case_id)

    cand = candidate_pairs(inst)
    parent = row[cand.case_id_a].to_numpy()
    child = row[cand.case_id_b].to_numpy()
    delay = cand.delay.to_numpy()
    logd = np.log(delay)
    pair = svc[parent] * S + svc[child]
    support = np.bincount(pair, minlength=S * S)
    ok = support[pair] >= min_pairs  # too few co-occurrences to learn a delay
    parent, child, delay, logd, pair = parent[ok], child[ok], delay[ok], logd[ok], pair[ok]

    alpha = np.where(support >= min_pairs, 0.05, 0.0)
    mu = np.full(S * S, math.log(14.0))
    sigma = np.full(S * S, 1.5)
    n_by_service = np.bincount(svc, minlength=S).astype(float)
    # background rate of service b in calendar bin k, per citizen and day
    base = np.zeros((S, n_bins))
    np.add.at(base, (svc, tbin), 0.7)
    base /= n_cit * BIN
    activity = np.ones(n_cit)  # how much more than average a citizen uses services
    remaining = horizon - day[parent]  # time left to observe a triggered instance

    for it in range(iters):
        # E-step
        w = alpha[pair] * lognormal(delay, mu[pair], sigma[pair])
        w0 = base[svc, tbin] * activity[cit] + 1e-300
        total = w0 + np.bincount(child, w, minlength=len(inst))
        p = w / total[child]
        p0 = w0 / total
        # M-step: triggering
        trig = np.bincount(pair, p, minlength=S * S)
        seen = np.bincount(pair, 0.5 * (1 + erf((np.log(np.maximum(remaining, 1e-9))
                                                - mu[pair]) / (sigma[pair] * math.sqrt(2)))),
                           minlength=S * S)
        # share of each A's delay density that falls inside the observed
        # period, averaged over the A's (those with no later B count fully)
        n_parent = np.repeat(n_by_service, S)
        covered = np.clip((seen + np.maximum(n_parent - support, 0)) / np.maximum(n_parent, 1),
                          0.05, 1.0)
        new_alpha = np.where(trig > 1e-9, trig / (np.maximum(n_parent, 1) * covered), 0.0)
        safe = np.maximum(trig, 1e-12)
        mu = np.where(trig > 1e-9, np.bincount(pair, p * logd, minlength=S * S) / safe, mu)
        var = np.bincount(pair, p * (logd - mu[pair]) ** 2, minlength=S * S) / safe
        sigma = np.where(trig > 1e-9, np.clip(np.sqrt(var), 0.3, 2.0), sigma)
        if it >= iters // 3:  # drop pairs that explain next to nothing
            new_alpha[trig < min_triggered] = 0.0
        change = np.abs(new_alpha - alpha).max()
        alpha = new_alpha
        # M-step: background, over the calendar and per citizen
        base = np.zeros((S, n_bins))
        np.add.at(base, (svc, tbin), p0)
        base /= n_cit * BIN
        spont = np.bincount(cit, p0, minlength=n_cit)
        activity = (spont + 2.0) / (spont.mean() + 2.0)
        activity /= activity.mean()
        if verbose and it % 10 == 0:
            print(f"  iteration {it}: {int((alpha > 0).sum())} pairs, "
                  f"{p0.sum():.0f} spontaneous, change {change:.4f}")
        if it > iters // 3 + 5 and change < 1e-5:
            break

    w = alpha[pair] * lognormal(delay, mu[pair], sigma[pair])
    w0 = base[svc, tbin] * activity[cit] + 1e-300
    total = w0 + np.bincount(child, w, minlength=len(inst))
    p, p0 = w / total[child], w0 / total

    # most likely parent of each instance, unless the instance is more likely
    # spontaneous than triggered by any of its possible parents taken together
    order = np.lexsort((p, child))
    last = np.r_[child[order][1:] != child[order][:-1], True]
    best_child, best_parent, best_p = child[order][last], parent[order][last], p[order][last]
    linked = p0[best_child] < 0.5
    edges = list(zip(best_parent[linked], best_child[linked]))
    partition = pd.DataFrame({"case_id": inst.case_id,
                              "journey_id": components(len(inst), edges),
                              "p_spontaneous": p0.round(3)})

    # Overlapping journeys: a triggered instance also keeps every other parent
    # that is likely enough. A journey is everything that descends from one
    # spontaneous instance, so an instance with parents in two journeys, and
    # whatever it triggers, belongs to both.
    also = (p >= also_parent) & (p0[child] < 0.5)
    kids: dict = {}
    has_parent = np.zeros(len(inst), dtype=bool)
    for a, b in set(edges) | set(zip(parent[also], child[also])):
        kids.setdefault(a, []).append(b)
        has_parent[b] = True
    rows = []
    for root in np.flatnonzero(~has_parent):
        seen, stack = {root}, [root]
        while stack:
            for b in kids.get(stack.pop(), ()):
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        rows += [(x, root) for x in seen]
    cases = inst.case_id.to_numpy()
    journeys = pd.DataFrame({"case_id": cases[[x for x, _ in rows]],
                             "journey_id": [r for _, r in rows]})

    names = inst.drop_duplicates("service_id").set_index("service_id").service
    trig = np.bincount(pair, p, minlength=S * S)
    rows = [{"service_a": services[k // S], "service_b": services[k % S],
             "name_a": names[services[k // S]], "name_b": names[services[k % S]],
             "triggered_per_a": round(alpha[k], 3), "triggered": round(trig[k], 1),
             "median_delay_days": round(math.exp(mu[k]), 2),
             "window_from_days": round(math.exp(mu[k] - 1.645 * sigma[k]), 2),
             "window_to_days": round(math.exp(mu[k] + 1.645 * sigma[k]), 2)}
            for k in np.flatnonzero(alpha > 0)]
    pairs = pd.DataFrame(rows).sort_values("triggered", ascending=False)
    return pairs, journeys, partition, p0.sum()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder holding events.csv")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    inst = instances(os.path.join(args.folder, "events.csv"))
    pairs, journeys, partition, spontaneous = fit(inst, verbose=args.verbose)
    pairs.to_csv(os.path.join(args.folder, "hawkes_pairs.csv"), index=False)
    journeys.to_csv(os.path.join(args.folder, "journeys_hawkes.csv"), index=False)
    partition.to_csv(os.path.join(args.folder, "journeys_hawkes_partition.csv"), index=False)
    sizes = journeys.groupby("journey_id").size()
    several = (journeys.groupby("case_id").size() > 1).sum()
    print(f"{len(inst)} instances; {len(pairs)} triggering pairs; "
          f"{spontaneous:.0f} instances expected spontaneous; {(sizes > 1).sum()} journeys "
          f"of two or more instances, {(sizes == 1).sum()} single instances; {several} "
          f"instances in more than one journey")


if __name__ == "__main__":
    main()
