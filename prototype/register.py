"""Use the register's evidence links to find journeys, alone or on top of a
method that learnt them from the log.

MITOS says which procedures a procedure requires as evidence, and for some how
long their result stays valid. That gives one rule, with nothing learnt:

    an instance of B *uses* the citizen's latest instance of A when the
    register says B requires A, A ended before B started, and A was still
    valid then.

Two ways to apply it:

* **Register only.** Journeys are built from "uses" links and nothing else.
* **Register added.** Journeys come from a log-only method. Where a journey
  holds a B but no A that B requires, and the citizen holds a valid earlier A,
  that A is added to the journey. It stays in its own journey as well: this
  is how an instance comes to be shared.

    python register.py ../out

Reads ``events.csv``, the catalogue, and every overlapping ``journeys*.csv``
in the folder. Writes ``journeys_register.csv`` (overlapping: each instance
that nothing uses, with everything it uses directly or indirectly),
``journeys_register_partition.csv`` (connected groups of "uses" links) and
one ``journeys_<method>+register.csv`` per method. Needs numpy and pandas.
"""
from __future__ import annotations

import argparse
import bisect
import glob
import gzip
import json
import os
import re

import numpy as np
import pandas as pd

from discover import components, instances

HERE = os.path.dirname(os.path.abspath(__file__))
_ISO = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?")


def validity_days(iso: str | None) -> float | None:
    m = _ISO.match(iso or "")
    if not m or not any(m.groups()):
        return None
    y, mo, w, d = (float(x) if x else 0.0 for x in m.groups())
    return y * 365 + mo * 30 + w * 7 + d


class Register:
    def __init__(self, catalogue: str, default_validity: float):
        with gzip.open(catalogue, "rt", encoding="utf-8") as f:
            procs = json.load(f)["procedures"]
        self.requires = {pid: [r["id"] for r in p["requires"]] for pid, p in procs.items()}
        self.valid = {pid: validity_days(p["validity"]) or default_validity
                      for pid, p in procs.items()}


class Held:
    """What each citizen holds: per citizen and service, instances by end time."""

    def __init__(self, inst: pd.DataFrame):
        self.by: dict = {}
        order = np.argsort(inst.end.to_numpy(), kind="stable")
        for i in order:
            ends, idx = self.by.setdefault((inst.citizen_id[i], inst.service_id[i]), ([], []))
            ends.append(inst.end_day[i])
            idx.append(i)

    def latest(self, citizen, service, before: float, valid: float):
        got = self.by.get((citizen, service))
        if not got:
            return None
        k = bisect.bisect_right(got[0], before) - 1
        if k < 0 or before - got[0][k] > valid:
            return None
        return got[1][k]


def uses(inst: pd.DataFrame, reg: Register, held: Held) -> list:
    """(index of A, index of B) for every "B uses A" the rule gives."""
    out = []
    for b in range(len(inst)):
        for a_service in reg.requires.get(inst.service_id[b], ()):
            a = held.latest(inst.citizen_id[b], a_service, inst.start_day[b],
                            reg.valid.get(a_service, 0))
            if a is not None and a != b:
                out.append((a, b))
    return out


def register_only(inst: pd.DataFrame, links: list):
    n = len(inst)
    cases = inst.case_id.to_numpy()
    partition = pd.DataFrame({"case_id": cases, "journey_id": components(n, links)})
    used_by: dict = {}
    is_used = np.zeros(n, dtype=bool)
    for a, b in links:
        used_by.setdefault(b, []).append(a)
        is_used[a] = True
    rows = []
    for top in np.flatnonzero(~is_used):   # an instance nothing else uses
        seen, stack = {top}, [top]
        while stack:
            for a in used_by.get(stack.pop(), ()):
                if a not in seen:
                    seen.add(a)
                    stack.append(a)
        rows += [(x, top) for x in seen]
    journeys = pd.DataFrame({"case_id": cases[[x for x, _ in rows]],
                             "journey_id": [t for _, t in rows]})
    return journeys, partition


def add_register(journeys: pd.DataFrame, inst: pd.DataFrame, reg: Register, held: Held):
    """Add to each journey the valid earlier evidence its instances lack."""
    row = pd.Series(np.arange(len(inst)), index=inst.case_id)
    cases = inst.case_id.to_numpy()
    added = []
    for jid, members in journeys.groupby("journey_id").case_id:
        idx = row[members].to_numpy()
        have = set(inst.service_id[idx])
        inside = set(idx)
        for b in idx:
            for a_service in reg.requires.get(inst.service_id[b], ()):
                if a_service in have:
                    continue  # the journey has its own
                a = held.latest(inst.citizen_id[b], a_service, inst.start_day[b],
                                reg.valid.get(a_service, 0))
                if a is not None and a not in inside:
                    inside.add(a)
                    added.append((cases[a], jid))
    extra = pd.DataFrame(added, columns=["case_id", "journey_id"])
    return pd.concat([journeys[["case_id", "journey_id"]], extra]).drop_duplicates(), len(extra)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder holding events.csv")
    ap.add_argument("--catalogue", default=os.path.join(HERE, "..", "catalogue.json.gz"))
    ap.add_argument("--validity-days", type=float, default=90.0,
                    help="validity assumed where the register gives none")
    args = ap.parse_args()
    inst = instances(os.path.join(args.folder, "events.csv"))
    inst["case_id"] = inst.case_id.astype(str)
    inst["service_id"] = inst.service_id.astype(str)
    t0 = inst.start.min()
    inst["start_day"] = (inst.start - t0).dt.total_seconds() / 86400
    inst["end_day"] = (inst.end - t0).dt.total_seconds() / 86400
    inst = inst.reset_index(drop=True)
    reg = Register(args.catalogue, args.validity_days)
    held = Held(inst)

    links = uses(inst, reg, held)
    journeys, partition = register_only(inst, links)
    journeys.to_csv(os.path.join(args.folder, "journeys_register.csv"), index=False)
    partition.to_csv(os.path.join(args.folder, "journeys_register_partition.csv"), index=False)
    print(f"register only: {len(links)} 'uses' links among {len(inst)} instances")

    for path in sorted(glob.glob(os.path.join(args.folder, "journeys*.csv"))):
        name = os.path.basename(path)[:-4]
        if "partition" in name or "register" in name:
            continue
        found = pd.read_csv(path, dtype={"case_id": str})
        merged, n = add_register(found, inst, reg, held)
        merged.to_csv(os.path.join(args.folder, name + "+register.csv"), index=False)
        print(f"{name}: {n} instances added to journeys that lacked them")


if __name__ == "__main__":
    main()
