"""Discover journeys and their kinds with a latent life-event model.

Reads only ``events.csv``. The model says a citizen's instances come from
hidden *life events*. A life event has a kind (one of ``K``, unknown in
advance), shows up in the log as its first instance, and then brings further
instances: on average ``alpha[kind, service]`` of each service, after a
log-normal delay from that first instance. Whatever is not brought by a life
event is spontaneous.

It differs from the Hawkes model in two ways. Instances attach to the life
event, not to each other, so two procedures that follow the same event in
either order are siblings. And the kinds are learnt: each is a bundle of
services with their typical timing, which is the thing we are looking for.

Fitted by a mean-field EM. For each instance it keeps the probability of being
the first of a life event (against being brought by an earlier one) and, if
first, of each kind. Kind 0 means "no life event". Whether an instance was
brought by an earlier one is judged with that earlier one's kind inferred
from everything *else* it brought.

    python latent.py ../out --kinds 12

Writes ``latent_kinds.csv`` (the bundles), ``journeys_latent.csv``
(overlapping) and ``journeys_latent_partition.csv`` next to the log. Both
journey files carry the kind of each journey: a number that ``latent_kinds.csv``
describes, 0 for "no life event" and -1 for a single instance. Needs numpy and pandas.
"""
from __future__ import annotations

import argparse
import math
import os

import numpy as np
import pandas as pd

from discover import candidate_pairs, components, instances

BIN = 30.0
SQRT2PI = math.sqrt(2 * math.pi)


def fit(inst: pd.DataFrame, kinds: int = 12, min_pairs: int = 15, min_brought: float = 4.0,
        iters: int = 120, seed: int = 0, also_parent: float = 0.25, verbose: bool = False):
    """Journeys found by the latent life-event model, from one random start.

    ``kinds`` hidden kinds of event (plus a no-event kind) each bring a set
    of services after a first instance; the model is fitted by mean-field EM.
    Returns the kinds found, the overlapping journeys, a partition, and the
    log-likelihood (to pick the best of several starts)."""
    rng = np.random.default_rng(seed)
    inst = inst.reset_index(drop=True)
    day = ((inst.start - inst.start.min()).dt.total_seconds() / 86400).to_numpy()
    services = np.sort(inst.service_id.unique())
    S, K, N = len(services), kinds, len(inst)
    svc = pd.Series(np.arange(S), index=services)[inst.service_id].to_numpy()
    citizens, cit = np.unique(inst.citizen_id, return_inverse=True)
    n_bins = int(day.max() // BIN) + 1
    tbin = np.minimum((day // BIN).astype(int), n_bins - 1)
    row = pd.Series(np.arange(N), index=inst.case_id)

    cand = candidate_pairs(inst)
    first = row[cand.case_id_a].to_numpy()   # the possible first instance of the event
    later = row[cand.case_id_b].to_numpy()   # the instance it may have brought
    pair = svc[first] * S + svc[later]
    ok = np.bincount(pair, minlength=S * S)[pair] >= min_pairs
    first, later = first[ok], later[ok]
    delay = cand.delay.to_numpy()[ok]
    logd = np.log(delay).astype(np.float32)[:, None]
    s_later = svc[later]

    alpha = (0.05 * rng.uniform(0.5, 1.5, (K, S))).astype(np.float32)
    alpha[0] = 0.0  # kind 0 is "no life event": an instance that brings nothing
    mu = (math.log(14.0) + rng.normal(0, 0.5, (K, S))).astype(np.float32)
    sigma = np.full((K, S), 1.5, dtype=np.float32)
    q = rng.dirichlet(np.full(K, 5.0), N).astype(np.float32)  # kind, if first of an event
    logq = np.log(q)
    contrib = np.zeros((len(first), K), dtype=np.float32)
    kind_given_service = np.full((K, S), 1.0 / K, dtype=np.float32)
    p0 = np.ones(N, dtype=np.float32)        # probability of not being brought
    base = np.zeros((S, n_bins))
    np.add.at(base, (svc, tbin), 0.7)
    base /= len(citizens) * BIN
    activity = np.ones(len(citizens))

    def density():
        a, m, sg = alpha[:, s_later].T, mu[:, s_later].T, sigma[:, s_later].T
        return a * np.exp(-(logd - m) ** 2 / (2 * sg ** 2)) / (delay[:, None] * sg * SQRT2PI)

    def kind_without(logq, contrib):
        """The kind of each row's first instance, judged without that row.

        Using the kind as already inferred *from* the later instance would
        let any chance follower confirm the life event it was used to posit.
        """
        cav = logq[first] - contrib
        cav -= cav.max(1, keepdims=True)
        cav = np.exp(cav)
        return cav / cav.sum(1, keepdims=True)

    score = -np.inf
    for it in range(iters):
        # E-step: who brought whom
        F = density()                                       # rows x kinds
        W = p0[first][:, None] * kind_without(logq, contrib) * F
        w0 = base[svc, tbin] * activity[cit] + 1e-300
        total = w0 + np.bincount(later, W.sum(1), minlength=N)
        R = W / total[later][:, None]
        p0 = (w0 / total).astype(np.float32)
        score = float(np.log(total).sum())
        # E-step: the kind of each possible life event, from what it brought
        r = R.sum(1)
        logq = np.log(kind_given_service[:, svc].T + 1e-12) - alpha.sum(1)[None, :]
        contrib = (r[:, None] * np.log(F + 1e-30)).astype(np.float32)
        for k in range(K):
            logq[:, k] += np.bincount(first, contrib[:, k], minlength=N)
        q = np.exp(logq - logq.max(1, keepdims=True))
        q = (q / q.sum(1, keepdims=True)).astype(np.float32)
        # M-step
        events = p0[:, None] * q                            # expected life events, by kind
        n_events = events.sum(0) + 1e-9
        brought = np.stack([np.bincount(s_later, R[:, k], minlength=S) for k in range(K)])
        m1 = np.stack([np.bincount(s_later, R[:, k] * logd[:, 0], minlength=S)
                       for k in range(K)])
        safe = np.maximum(brought, 1e-9)
        new_mu = np.where(brought > 1e-6, m1 / safe, mu)
        dev = (logd - new_mu[:, s_later].T) ** 2
        m2 = np.stack([np.bincount(s_later, R[:, k] * dev[:, k], minlength=S)
                       for k in range(K)])
        mu = new_mu.astype(np.float32)
        sigma = np.where(brought > 1e-6, np.clip(np.sqrt(m2 / safe), 0.3, 2.0),
                         sigma).astype(np.float32)
        alpha = (brought / n_events[:, None]).astype(np.float32)
        alpha[0] = 0.0
        if it >= iters // 3:
            alpha[brought < min_brought] = 0.0
        ks = np.stack([np.bincount(svc, events[:, k], minlength=S) for k in range(K)]) + 0.01
        kind_given_service = (ks / ks.sum(0, keepdims=True)).astype(np.float32)
        base = np.zeros((S, n_bins))
        np.add.at(base, (svc, tbin), p0)
        base /= len(citizens) * BIN
        spont = np.bincount(cit, p0, minlength=len(citizens))
        activity = (spont + 2.0) / (spont.mean() + 2.0)
        activity /= activity.mean()
        if verbose and it % 10 == 0:
            print(f"  iteration {it}: log-likelihood {score:.0f}, "
                  f"{p0.sum():.0f} not brought, {(alpha > 0).sum()} kind-service rates")

    F = density()
    W = p0[first][:, None] * kind_without(logq, contrib) * F
    w0 = base[svc, tbin] * activity[cit] + 1e-300
    total = w0 + np.bincount(later, W.sum(1), minlength=N)
    r = W.sum(1) / total[later]
    p0 = w0 / total

    order = np.lexsort((r, later))
    last = np.r_[later[order][1:] != later[order][:-1], True]
    b_child, b_parent = later[order][last], first[order][last]
    linked = p0[b_child] < 0.5
    edges = list(zip(b_parent[linked], b_child[linked]))
    comp = components(N, edges)
    # the kind of a journey is the kind of its first instance
    root = pd.Series(day).groupby(comp).idxmin()
    kind_of = q.argmax(1)
    size = np.bincount(comp, minlength=N)
    partition = pd.DataFrame({"case_id": inst.case_id, "journey_id": comp,
                              "kind": np.where(size[comp] > 1, kind_of[root[comp].to_numpy()],
                                               -1)})
    also = (r >= also_parent) & (p0[later] < 0.5)
    kids: dict = {}
    has_parent = np.zeros(N, dtype=bool)
    for a, b in set(edges) | set(zip(first[also], later[also])):
        kids.setdefault(a, []).append(b)
        has_parent[b] = True
    rows = []
    for start in np.flatnonzero(~has_parent):
        seen, stack = {start}, [start]
        while stack:
            for b in kids.get(stack.pop(), ()):
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        # the kind of a journey is the kind of the instance it starts from;
        # a single instance is no life event
        rows += [(x, start, kind_of[start] if len(seen) > 1 else -1) for x in seen]
    cases = inst.case_id.to_numpy()
    journeys = pd.DataFrame({"case_id": cases[[x for x, _, _ in rows]],
                             "journey_id": [g for _, g, _ in rows],
                             "kind": [int(k) for _, _, k in rows]})

    names = inst.drop_duplicates("service_id").set_index("service_id").service
    found = partition[partition.kind >= 0].drop_duplicates("journey_id").kind.value_counts()
    out = []
    for k in range(1, K):
        starts = np.bincount(svc[root.to_numpy()][(kind_of[root.to_numpy()] == k)
                                                  & (size[root.index.to_numpy()] > 1)],
                             minlength=S)
        for s in np.flatnonzero(alpha[k] > 0):
            out.append({"kind": k, "journeys": int(found.get(k, 0)), "service_id": services[s],
                        "service": names[services[s]], "per_event": round(float(alpha[k, s]), 3),
                        "median_delay_days": round(math.exp(mu[k, s]), 1),
                        "window_from_days": round(math.exp(mu[k, s] - 1.645 * sigma[k, s]), 1),
                        "window_to_days": round(math.exp(mu[k, s] + 1.645 * sigma[k, s]), 1),
                        "starts_here": int(starts[s])})
    kinds_table = pd.DataFrame(out).sort_values(["journeys", "kind", "per_event"],
                                                ascending=[False, True, False])
    return kinds_table, journeys, partition, score


def main() -> None:
    """The command line: ``python prototype/latent.py --help``."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder holding events.csv")
    ap.add_argument("--kinds", type=int, default=12)
    ap.add_argument("--restarts", type=int, default=3)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    inst = instances(os.path.join(args.folder, "events.csv"))
    best = None
    for seed in range(args.restarts):
        result = fit(inst, kinds=args.kinds, seed=seed, verbose=args.verbose)
        print(f"start {seed}: log-likelihood {result[3]:.0f}")
        if best is None or result[3] > best[3]:
            best = result
    kinds_table, journeys, partition, _ = best
    kinds_table.to_csv(os.path.join(args.folder, "latent_kinds.csv"), index=False)
    journeys.to_csv(os.path.join(args.folder, "journeys_latent.csv"), index=False)
    partition.to_csv(os.path.join(args.folder, "journeys_latent_partition.csv"), index=False)
    sizes = partition.groupby("journey_id").size()
    used = partition[partition.kind > 0].kind.nunique()
    print(f"{len(inst)} instances; {(sizes > 1).sum()} journeys of two or more instances "
          f"in {used} kinds; {(sizes == 1).sum()} single instances")


if __name__ == "__main__":
    main()
