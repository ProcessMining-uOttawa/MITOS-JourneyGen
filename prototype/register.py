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

The generator builds its bundles from these same links, so on its logs the
register looks better than it would on real ones.
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
    """An ISO 8601 duration in days, or None when there is none."""
    m = _ISO.match(iso or "")
    if not m or not any(m.groups()):
        return None
    y, mo, w, d = (float(x) if x else 0.0 for x in m.groups())
    return y * 365 + mo * 30 + w * 7 + d


class Register:
    """What MITOS states: which procedures each requires as evidence, and how
    long a result stays valid (``default_validity`` days where MITOS gives
    no validity)."""
    def __init__(self, catalogue: str, default_validity: float = 90.0):
        with gzip.open(catalogue, "rt", encoding="utf-8") as f:
            procs = json.load(f)["procedures"]
        self.requires = {pid: [r["id"] for r in p["requires"]] for pid, p in procs.items()}
        self.valid = {pid: validity_days(p["validity"]) or default_validity
                      for pid, p in procs.items()}


class Log:
    """The instances of a log, as plain arrays, and what each citizen holds:
    per citizen and service, the instances in order of their end."""

    def __init__(self, inst: pd.DataFrame):
        self.case = inst.case_id.astype(str).to_numpy()
        self.citizen = inst.citizen_id.to_numpy()
        self.service = inst.service_id.astype(str).to_numpy()
        t0 = inst.start.min()
        self.start = ((inst.start - t0).dt.total_seconds() / 86400).to_numpy()
        self.end = ((inst.end - t0).dt.total_seconds() / 86400).to_numpy()
        self.row = {c: i for i, c in enumerate(self.case)}
        self.by: dict = {}
        for i in np.argsort(self.end, kind="stable"):
            ends, idx = self.by.setdefault((self.citizen[i], self.service[i]), ([], []))
            ends.append(self.end[i])
            idx.append(int(i))

    def __len__(self) -> int:
        return len(self.case)

    def latest(self, citizen, service: str, before: float, valid: float):
        """The latest instance of ``service`` for ``citizen`` that ended before
        ``before`` and is still ``valid`` (days) then, or None."""
        got = self.by.get((citizen, service))
        if not got:
            return None
        k = bisect.bisect_right(got[0], before) - 1
        if k < 0 or before - got[0][k] > valid:
            return None
        return got[1][k]


def uses(log: Log, reg: Register) -> list:
    """(index of A, index of B) for every "B uses A" the rule gives."""
    out = []
    for b in range(len(log)):
        for a_service in reg.requires.get(log.service[b], ()):
            a = log.latest(log.citizen[b], a_service, log.start[b], reg.valid.get(a_service, 0))
            if a is not None and a != b:
                out.append((a, b))
    return out


def register_only(log: Log, links: list):
    """Journeys from the register's links alone: each instance that nothing
    else uses heads a journey of everything it used, directly or not.
    Returns the overlapping journeys and a partition."""
    n = len(log)
    partition = pd.DataFrame({"case_id": log.case, "journey_id": components(n, links)})
    used_by: dict = {}
    is_used = np.zeros(n, dtype=bool)
    for a, b in links:
        used_by.setdefault(b, []).append(a)
        is_used[a] = True
    rows = []
    for top in np.flatnonzero(~is_used):   # an instance nothing else uses
        seen, stack = {int(top)}, [int(top)]
        while stack:
            for a in used_by.get(stack.pop(), ()):
                if a not in seen:
                    seen.add(a)
                    stack.append(a)
        rows += [(x, int(top)) for x in seen]
    journeys = pd.DataFrame({"case_id": log.case[[x for x, _ in rows]],
                             "journey_id": [t for _, t in rows]})
    return journeys, partition


def add_register(journeys: pd.DataFrame, log: Log, reg: Register):
    """Add to each journey the valid earlier evidence its instances lack.
    Returns the enlarged journeys and how many memberships were added."""
    groups: dict = {}
    for case, jid in zip(journeys.case_id.astype(str), journeys.journey_id):
        if case in log.row:
            groups.setdefault(jid, []).append(log.row[case])
    added = []
    for jid, idx in groups.items():
        have = {log.service[i] for i in idx}
        inside = set(idx)
        for b in idx:
            for a_service in reg.requires.get(log.service[b], ()):
                if a_service in have:
                    continue  # the journey has its own
                a = log.latest(log.citizen[b], a_service, log.start[b],
                               reg.valid.get(a_service, 0))
                if a is not None and a not in inside:
                    inside.add(a)
                    added.append((log.case[a], jid))
    extra = pd.DataFrame(added, columns=["case_id", "journey_id"])
    both = pd.concat([journeys[["case_id", "journey_id"]].astype({"case_id": str}), extra])
    return both.drop_duplicates(), len(extra)


def main() -> None:
    """The command line: ``python prototype/register.py --help``."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder holding events.csv")
    ap.add_argument("--catalogue", default=os.path.join(HERE, "..", "catalogue.json.gz"),
                    help="MITOS catalogue snapshot (default ../catalogue.json.gz)")
    ap.add_argument("--validity-days", type=float, default=90.0,
                    help="validity assumed where the register gives none")
    args = ap.parse_args()
    log = Log(instances(os.path.join(args.folder, "events.csv")))
    reg = Register(args.catalogue, args.validity_days)

    links = uses(log, reg)
    journeys, partition = register_only(log, links)
    journeys.to_csv(os.path.join(args.folder, "journeys_register.csv"), index=False)
    partition.to_csv(os.path.join(args.folder, "journeys_register_partition.csv"), index=False)
    print(f"register only: {len(links)} 'uses' links among {len(log)} instances")

    for path in sorted(glob.glob(os.path.join(args.folder, "journeys*.csv"))):
        name = os.path.basename(path)[:-4]
        if "partition" in name or "register" in name:
            continue
        merged, n = add_register(pd.read_csv(path, dtype={"case_id": str}), log, reg)
        merged.to_csv(os.path.join(args.folder, name + "+register.csv"), index=False)
        print(f"{name}: {n} instances added to journeys that lacked them")


if __name__ == "__main__":
    main()
