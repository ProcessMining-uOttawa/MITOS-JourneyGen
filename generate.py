"""Generate a synthetic event log of citizens using Greek public services.

Every service instance follows the real step tree of a procedure published in
MITOS, the Greek National Register of Administrative Procedures. Citizens
live through life events (a death in the family, a birth, a job loss ...);
each event makes them start a bundle of related procedures, together with
the evidence those procedures require. Unrelated one-off uses of popular
services are mixed in as background.

The log itself says nothing about bundles. The ground truth is written to
separate files, so a method that looks for bundles can be scored.

    python generate.py --citizens 2000 --seed 1 --out out/
    python generate.py --describe          # show the scenarios, generate nothing

Standard library only. See README.md.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import gzip
import json
import math
import os
import random
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86400.0

_ISO = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
                  r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")
# Greek step titles that mark the unhappy side of a branch: rejection,
# incomplete documents, failure, "not ...", negative, cancellation.
_NEGATIVE = re.compile(r"^Μη |^Δεν |απόρριψ|απορριπτ|ελλιπ|ελλειπ|αποτυχ|ανεπιτυχ|"
                       r"αρνητικ|ακύρωσ|μη έκδοση|μη ταυτοποίηση|λανθασμέν|"
                       r"αδυναμία|δεν πληρο", re.IGNORECASE)


def seconds(iso: str | None) -> float | None:
    """ISO 8601 duration -> seconds (a month is 30 days, a year 365)."""
    m = _ISO.match(iso or "")
    if not m or not any(m.groups()):
        return None
    y, mo, w, d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return ((y * 365 + mo * 30 + w * 7 + d) * 24 + h) * 3600 + mi * 60 + s


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def weighted(rng: random.Random, table: dict):
    keys = list(table)
    cum, total = [], 0.0
    for k in keys:
        total += table[k]
        cum.append(total)
    return keys[bisect.bisect_left(cum, rng.random() * total)]


class Catalogue:
    """The MITOS snapshot plus the English labels used in the log."""

    def __init__(self, path: str, translations: str | None):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        self.meta = {k: data[k] for k in ("source", "licence", "retrieved")}
        self.procedures = data["procedures"]
        self.official = data["official_en"]
        extra = {}
        if translations and os.path.exists(translations):
            with open(translations, encoding="utf-8") as f:
                extra = json.load(f)
        self.extra_titles = extra.get("titles", {})
        self.extra_strings = extra.get("strings", {})
        self.untranslated: set = set()

    def en(self, text: str | None) -> str:
        """English for a Greek step name, role or method; Greek when unknown."""
        if not text:
            return ""
        text = text.strip()
        out = self.official.get(text) or self.extra_strings.get(text)
        if out is None:
            self.untranslated.add(text)
            return text
        return out

    def title(self, pid: str, lang: str = "en") -> str:
        p = self.procedures[pid]
        if lang == "el":
            return p["title"]
        out = p["title_en"] or self.extra_titles.get(pid)
        if out is None:
            self.untranslated.add(p["title"])
            return p["title"]
        return out


def walk(steps: list, rng: random.Random, negative_weight: float) -> tuple[list, bool]:
    """One path through a MITOS step tree; returns (steps, went_negative).

    Steps without a parent form the main line. A step's children are the
    alternatives that follow it; a branch that ends without an exit step
    rejoins the main line at the next main-line step.
    """
    by_n = {s["n"]: s for s in steps}
    children: dict = {}
    for s in steps:
        if s["parent"] is not None and s["parent"] in by_n and s["parent"] != s["n"]:
            children.setdefault(s["parent"], []).append(s)
    main = [s for s in steps if s["n"] not in {c["n"] for cs in children.values() for c in cs}]
    path, negative, seen = [], False, set()
    cur = main[0] if main else None
    while cur is not None and cur["n"] not in seen:
        seen.add(cur["n"])
        path.append(cur)
        if cur["exit"]:
            break
        kids = children.get(cur["n"])
        if kids:
            # Only a choice between alternatives can turn an instance sour: a
            # main-line step such as "Delivery of passport or rejection
            # letter" says nothing about this instance.
            bad = [len(kids) > 1 and bool(_NEGATIVE.search(k["title"])) for k in kids]
            cur = rng.choices(kids, [negative_weight if b else 1.0 for b in bad])[0]
            negative = negative or bad[kids.index(cur)]
        else:
            cur = next((s for s in main if s["n"] > cur["n"]), None)
    return path, negative


def office_hours(t: dt.datetime, rng: random.Random) -> dt.datetime:
    """Move a counter step to Monday-Friday, 08:00-15:00."""
    if t.weekday() < 5 and 8 <= t.hour < 15:
        return t
    if t.weekday() < 5 and t.hour < 8:
        day = t.date()
    else:
        day = t.date() + dt.timedelta(days=1)
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(8)) + dt.timedelta(seconds=rng.uniform(0, 5 * 3600))


class Generator:
    def __init__(self, cat: Catalogue, cfg: dict, seed: int, rate_scale: float = 1.0):
        self.cat, self.cfg, self.rng = cat, cfg, random.Random(seed)
        self.rate_scale = rate_scale
        self.b = cfg["behaviour"]
        self.instances: list = []
        self.episodes: list = []

    # -- one service instance ------------------------------------------------
    def instance(self, citizen: dict, pid: str, t: dt.datetime, **truth) -> dict:
        rng, proc = self.rng, self.cat.procedures[pid]
        digital = bool(proc["digital_steps"]) and (
            not proc["steps"] or rng.random() < citizen["digital"])
        path, negative = walk(proc["digital_steps"] if digital else proc["steps"],
                              rng, self.b["negative_branch_weight"])
        outcome = "unsuccessful" if negative else "completed"
        if len(path) > 1 and rng.random() < self.b["abandon_probability"]:
            path, outcome = path[:rng.randint(1, len(path) - 1)], "abandoned"
        events, clock = [], t
        for s in path:
            lo = seconds(s["min"])
            hi = seconds(s["max"])
            if lo is None and hi is None:
                lo, hi = (5, 60) if digital else (300, 1800)
            lo = hi if lo is None else lo
            hi = lo if hi is None else hi
            dur = rng.uniform(min(lo, hi), max(lo, hi))
            start = clock + dt.timedelta(seconds=rng.uniform(1, 8) if digital
                                         else rng.uniform(30, 600))
            if not digital and dur < DAY:
                start = office_hours(start, rng)
            clock = start + dt.timedelta(seconds=max(dur, 1.0))
            events.append((s, start, clock))
        inst = {"citizen": citizen, "pid": pid, "events": events, "outcome": outcome,
                "channel": "digital" if digital else "counter", "shared": [],
                "start": events[0][1], "end": events[-1][2], **truth}
        self.instances.append(inst)
        if outcome == "completed":
            citizen["held"].append(inst)
        return inst

    def held(self, citizen: dict, pid: str, t: dt.datetime):
        """The citizen's latest still-valid result of ``pid`` at time ``t``."""
        if pid in self.b["single_use_evidence"]:
            return None
        valid = seconds(self.cat.procedures[pid]["validity"]) or (
            self.b["evidence_validity_days"] * DAY)
        best = None
        for inst in citizen["held"]:
            if inst["pid"] == pid and inst["end"] <= t and (
                    t - inst["end"]).total_seconds() <= valid:
                if best is None or inst["end"] > best["end"]:
                    best = inst
        return best

    # -- obtain a service, with its evidence and retries ----------------------
    def obtain(self, citizen: dict, pid: str, t: dt.datetime, done: dict,
               overrides: dict, depth: int, **truth):
        """Run ``pid`` until it succeeds or the citizen gives up.

        Returns the successful instance, or None. ``done`` maps the services
        already obtained in this episode to their completion time.
        """
        rng, b = self.rng, self.b
        gathered = []
        if depth < b["evidence_depth"]:
            for req in self.cat.procedures[pid]["requires"]:
                rid = req["id"]
                proc = self.cat.procedures.get(rid)
                if not proc or not (proc["steps"] or proc["digital_steps"]):
                    continue
                chance = overrides.get(rid, b["alternative_evidence_probability"]
                                       if req["alternative"] else b["evidence_probability"])
                if rng.random() >= chance:
                    continue
                prior = self.held(citizen, rid, t)
                if prior and rng.random() < b["reuse_evidence_probability"]:
                    # The citizen still holds a valid copy. When it came from
                    # another episode, or from an unrelated use, that
                    # instance now belongs to this episode as well.
                    ep = truth["episode"]
                    if prior["episode"] is not ep and ep not in prior["shared"]:
                        prior["shared"].append(ep)
                    continue
                ev = self.obtain(citizen, rid, t + dt.timedelta(days=rng.uniform(0, 2)),
                                 done, overrides, depth + 1,
                                 **{**truth, "role": "evidence"})
                if ev:
                    gathered.append(ev)
        if gathered:
            t = max(t, max(e["end"] for e in gathered)) + dt.timedelta(
                seconds=rng.uniform(600, 3 * DAY))
        for attempt in range(1, b["max_attempts"] + 1):
            inst = self.instance(citizen, pid, t, attempt=attempt, **truth)
            for e in gathered:
                e["supports"] = inst
            if inst["outcome"] == "completed":
                done[pid] = inst["end"]
                return inst
            if rng.random() >= b["retry_probability"]:
                return None
            t = inst["end"] + dt.timedelta(days=rng.uniform(*b["retry_delay_days"]))
        return None

    # -- one life event --------------------------------------------------------
    def episode(self, citizen: dict, scen: dict, trigger: dt.datetime) -> None:
        rng = self.rng
        ep = {"id": len(self.episodes) + 1, "citizen": citizen, "scenario": scen["id"],
              "trigger": trigger}
        before = len(self.instances)
        done: dict = {}
        for item in scen["procedures"]:
            if rng.random() >= item["p"]:
                continue
            after = item.get("after", [])
            if any(a not in done for a in after):
                continue  # a prerequisite was skipped, unsuccessful or abandoned
            lo, hi = item["delay_days"]
            ready = max([trigger] + [done[a] for a in after])
            ready += dt.timedelta(days=lo + (hi - lo) * rng.random() ** 2)
            self.obtain(citizen, item["id"], ready, done, scen.get("evidence", {}), 0,
                        origin="scenario", episode=ep, role="goal")
        if len(self.instances) > before:
            self.episodes.append(ep)

    # -- the whole population ---------------------------------------------------
    def run(self, n_citizens: int) -> None:
        rng, cfg = self.rng, self.cfg
        pop, win = cfg["population"], cfg["window"]
        t0 = dt.datetime.fromisoformat(win["start"])
        span = win["years"] * 365 * DAY
        ids = rng.sample(range(10 ** 7, 10 ** 8), n_citizens)
        for cid in ids:
            age = weighted(rng, pop["age_groups"])
            citizen = {"id": f"C{cid}", "age": age, "region": weighted(rng, pop["regions"]),
                       "held": [],
                       "digital": min(0.98, max(0.02, rng.gauss(
                           pop["digital_propensity"][age], 0.15)))}
            # Background first, then life events in the order they happen, so
            # that an episode can reuse what the citizen already holds.
            bg = cfg["background"]
            activity = rng.gammavariate(2.0, 0.5)  # some citizens use far more services
            for _ in range(poisson(rng, bg["rate_per_year"] * win["years"] * activity)):
                self.instance(citizen, weighted(rng, bg["services"]),
                              t0 + dt.timedelta(seconds=rng.uniform(0, span)),
                              origin="background", episode=None, role="background",
                              attempt=1)
            life = []
            for scen in cfg["scenarios"]:
                rate = scen["rate_per_year"].get(age, 0.0) * self.rate_scale
                for _ in range(poisson(rng, rate * win["years"])):
                    life.append((t0 + dt.timedelta(seconds=rng.uniform(0, span)), scen))
            for trigger, scen in sorted(life, key=lambda x: x[0]):
                self.episode(citizen, scen, trigger)
        self.instances.sort(key=lambda i: (i["start"], i["citizen"]["id"]))
        for n, inst in enumerate(self.instances, 1):
            inst["case"] = f"S{n:07d}"

    # -- output -------------------------------------------------------------------
    def write(self, out: str, lang: str, args: dict) -> dict:
        os.makedirs(out, exist_ok=True)
        cat, stamp = self.cat, lambda t: t.isoformat(sep=" ", timespec="seconds")
        both = lang == "both"
        name = (lambda el: el or "") if lang == "el" else cat.en
        rows = []
        for inst in self.instances:
            p, c = cat.procedures[inst["pid"]], inst["citizen"]
            org = (p["org"] if lang == "el" else p["org_en"] or p["org"]) or ""
            for s, start, end in inst["events"]:
                row = [inst["case"], c["id"], inst["pid"],
                       cat.title(inst["pid"], "el" if lang == "el" else "en"),
                       name(s["title"]), stamp(start), stamp(end), name(s["role"]),
                       name(s["how"]), inst["channel"], org, c["region"], c["age"]]
                if both:
                    row += [p["title"], s["title"]]
                rows.append((end, row))
        rows.sort(key=lambda r: r[0])
        header = ["case_id", "citizen_id", "service_id", "service", "activity",
                  "start_timestamp", "time:timestamp", "role", "method", "channel",
                  "organisation", "citizen_region", "citizen_age_group"]
        if both:
            header += ["service_el", "activity_el"]

        def dump(fname, head, lines):
            with open(os.path.join(out, fname), "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, lineterminator="\n")  # same bytes on every platform
                w.writerow(head)
                w.writerows(lines)

        dump("events.csv", header, (r for _, r in rows))
        dump("truth_instances.csv",
             ["case_id", "citizen_id", "service_id", "service", "start", "end", "events",
              "channel", "outcome", "origin", "episode_id", "scenario", "role", "attempt",
              "supports_case", "shared_with"],
             ([i["case"], i["citizen"]["id"], i["pid"], cat.title(i["pid"]),
               stamp(i["start"]), stamp(i["end"]), len(i["events"]), i["channel"],
               i["outcome"], i["origin"],
               f"E{i['episode']['id']:06d}" if i["episode"] else "",
               i["episode"]["scenario"] if i["episode"] else "", i["role"], i["attempt"],
               i["supports"]["case"] if i.get("supports") else "",
               ";".join(f"E{e['id']:06d}" for e in i["shared"])]
              for i in self.instances))
        by_ep: dict = {}
        for i in self.instances:
            for e in ([i["episode"]] if i["episode"] else []) + i["shared"]:
                by_ep.setdefault(e["id"], []).append(i)
        dump("truth_episodes.csv",
             ["episode_id", "citizen_id", "scenario", "life_event", "first_start", "last_end",
              "span_days", "instances", "goal_services", "all_services"],
             ([f"E{e['id']:06d}", e["citizen"]["id"], e["scenario"], stamp(e["trigger"]),
               stamp(min(i["start"] for i in by_ep[e["id"]])),
               stamp(max(i["end"] for i in by_ep[e["id"]])),
               round((max(i["end"] for i in by_ep[e["id"]])
                      - min(i["start"] for i in by_ep[e["id"]])).total_seconds() / DAY, 1),
               len(by_ep[e["id"]]),
               " > ".join(i["pid"] for i in by_ep[e["id"]]
                          if i["role"] == "goal" and i["episode"] is e),
               " > ".join(i["pid"] for i in by_ep[e["id"]])]
              for e in self.episodes))
        used = sorted({i["pid"] for i in self.instances})
        dump("services.csv",
             ["service_id", "service", "service_el", "organisation", "mitos_url", "instances"],
             ([pid, cat.title(pid), cat.procedures[pid]["title"],
               cat.procedures[pid]["org_en"] or cat.procedures[pid]["org"] or "",
               f"https://id.mitos.gov.gr/{pid}",
               sum(1 for i in self.instances if i["pid"] == pid)] for pid in used))
        outcomes: dict = {}
        for i in self.instances:
            outcomes[i["outcome"]] = outcomes.get(i["outcome"], 0) + 1
        manifest = {
            "generator": "MITOS-JourneyGen generate.py", "arguments": args,
            "catalogue": cat.meta, "citizens": len({i["citizen"]["id"] for i in self.instances}),
            "instances": len(self.instances), "events": len(rows),
            "services": len(used), "episodes": len(self.episodes),
            "episodes_by_scenario": {s["id"]: sum(1 for e in self.episodes
                                                  if e["scenario"] == s["id"])
                                     for s in self.cfg["scenarios"]},
            "instances_by_origin": {o: sum(1 for i in self.instances if i["origin"] == o)
                                    for o in ("scenario", "background")},
            "outcomes": outcomes,
            "instances_shared_between_episodes": sum(
                1 for i in self.instances if len(i["shared"]) + bool(i["episode"]) > 1),
            "background_instances_reused_by_an_episode": sum(
                1 for i in self.instances if i["shared"] and not i["episode"]),
            "labels_left_in_greek": len(cat.untranslated),
        }
        with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        return manifest


def describe(cat: Catalogue, cfg: dict) -> None:
    b = cfg["behaviour"]
    for scen in cfg["scenarios"]:
        print(f"\n{scen['id']}: {scen['label']}")
        for item in scen["procedures"]:
            p = cat.procedures[item["id"]]
            print(f"  {item['id']}  p={item['p']:<4}  {cat.title(item['id'])}"
                  f"  [{len(p['steps'])} steps, {len(p['digital_steps'])} digital]")
            for req in p["requires"]:
                chance = scen.get("evidence", {}).get(
                    req["id"], b["alternative_evidence_probability"] if req["alternative"]
                    else b["evidence_probability"])
                if chance and req["id"] in cat.procedures:
                    print(f"            needs {req['id']} ({chance:.2f})  {cat.title(req['id'])}")
            for s in p["steps"] + p["digital_steps"]:
                cat.en(s["title"])
    for pid in cfg["background"]["services"]:
        cat.title(pid)
        for s in cat.procedures[pid]["steps"] + cat.procedures[pid]["digital_steps"]:
            cat.en(s["title"])
    print(f"\n{len(cat.untranslated)} labels have no English and stay in Greek:")
    for text in sorted(cat.untranslated):
        print("  ", text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--citizens", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--lang", choices=("en", "el", "both"), default="en",
                    help="labels in English (Greek where MITOS has no English), "
                         "Greek, or English plus Greek columns")
    ap.add_argument("--rate-scale", type=float, default=1.0,
                    help="multiply every life-event rate, to make episodes of one "
                         "citizen overlap more often (a stress test)")
    ap.add_argument("--scenarios", default=os.path.join(HERE, "scenarios.json"))
    ap.add_argument("--catalogue", default=os.path.join(HERE, "catalogue.json.gz"))
    ap.add_argument("--translations", default=os.path.join(HERE, "translations_en.json"))
    ap.add_argument("--describe", action="store_true",
                    help="print the scenarios with their evidence and exit")
    args = ap.parse_args()
    try:  # Greek labels on a Windows console
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    cat = Catalogue(args.catalogue, args.translations)
    with open(args.scenarios, encoding="utf-8") as f:
        cfg = json.load(f)
    if args.describe:
        describe(cat, cfg)
        return
    gen = Generator(cat, cfg, args.seed, args.rate_scale)
    gen.run(args.citizens)
    manifest = gen.write(args.out, args.lang, {
        "citizens": args.citizens, "seed": args.seed, "lang": args.lang,
        "rate_scale": args.rate_scale,
        "scenarios": os.path.basename(args.scenarios)})
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("arguments", "catalogue")},
                     indent=1))
    print("written to", args.out)


if __name__ == "__main__":
    main()
