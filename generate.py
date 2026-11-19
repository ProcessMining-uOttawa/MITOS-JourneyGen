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
    python generate.py --format xes --gzip # an XES log instead of CSV
    python generate.py --describe          # show the scenarios, generate nothing

Standard library only. See README.md.
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import csv
import datetime as dt
import gzip
import json
import math
import os
import random
import re
import sys
import time

__version__ = "1.0.0"

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


# Characters XML 1.0 does not allow, even escaped: most C0 controls, lone
# surrogates and two non-characters. Text from MITOS is free text typed by
# civil servants, so a stray control character must not make the XES invalid.
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
                "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"}


def xml_attr(value) -> str:
    """``value`` as a double-quoted XML attribute: escaped, and with the
    characters XML 1.0 forbids removed. For writing XES; nothing here parses
    XML."""
    text = _XML_INVALID.sub("", str(value))
    return '"' + "".join(_XML_ESCAPES.get(ch, ch) for ch in text) + '"'


def poisson(rng: random.Random, lam: float) -> int:
    """A Poisson draw with mean ``lam`` (Knuth's method; 0 when ``lam`` <= 0)."""
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def weighted(rng: random.Random, table: dict):
    """A key of ``table`` drawn with probability proportional to its value."""
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
        """The title of procedure ``pid``: Greek for ``lang`` "el", else the
        English from MITOS or from the translations, else the Greek (and the
        title is noted in ``untranslated``)."""
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
    """Simulates citizens through their life events and background use.

    ``run`` fills ``instances`` (every service instance with its events and
    its ground truth) and ``episodes``; ``write`` writes the log, the truth
    and the manifest. The same ``seed`` and configuration give the same log."""
    def __init__(self, cat: Catalogue, cfg: dict, seed: int, rate_scale: float = 1.0):
        # A seeded simulation, not a secret: the same seed must give the same log.
        self.cat, self.cfg, self.rng = cat, cfg, random.Random(seed)  # nosec B311
        self.rate_scale = rate_scale
        self.b = cfg["behaviour"]
        self.instances: list = []
        self.episodes: list = []
        self.n_events = 0

    # -- one service instance ------------------------------------------------
    def instance(self, citizen: dict, pid: str, t: dt.datetime, **truth) -> dict:
        """One service instance of procedure ``pid`` started at ``t``: a path
        through its counter or digital step tree, with durations drawn
        between the MITOS bounds. ``truth`` (episode, role, attempt, ...)
        is stored with it. A completed instance is held by the citizen."""
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
        self.n_events += len(events)
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
        """One life event of ``citizen`` at ``trigger``: starts the procedures of
        scenario ``scen`` with their chances, delays and prerequisites, each
        with the evidence it requires, and records the episode."""
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
    def run(self, n_citizens: int, progress=None) -> None:
        """Simulate the population.

        ``progress(done, total, instances, episodes, events)`` is called about
        two hundred times along the way; it may raise to stop the run.
        """
        rng, cfg = self.rng, self.cfg
        every = max(1, n_citizens // 200)
        pop, win = cfg["population"], cfg["window"]
        t0 = dt.datetime.fromisoformat(win["start"])
        span = win["years"] * 365 * DAY
        ids = rng.sample(range(10 ** 7, 10 ** 8), n_citizens)
        for done_count, cid in enumerate(ids):
            if progress and done_count % every == 0:
                progress(done_count, n_citizens, len(self.instances), len(self.episodes),
                         self.n_events)
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
        if progress:
            progress(n_citizens, n_citizens, len(self.instances), len(self.episodes),
                     self.n_events)

    def write_xes(self, path: str, lang: str, compress: bool, progress=None) -> None:
        """The log as XES: a trace per service instance, and on every event its
        completion in ``time:timestamp`` and its start in ``start_timestamp``."""
        cat = self.cat
        both = lang == "both"
        name = (lambda el: el or "") if lang == "el" else cat.en
        stamp = lambda t: t.isoformat(timespec="seconds") + "+00:00"  # noqa: E731
        attr = lambda kind, key, value: f'<{kind} key={xml_attr(key)} value={xml_attr(value)}/>'  # noqa: E731,E501
        opener = gzip.open if compress else open
        with opener(path, "wt", encoding="utf-8", newline="\n") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<log xes.version="1.0" xes.features="" '
                    'xmlns="http://www.xes-standard.org/">\n'
                    '<extension name="Concept" prefix="concept" '
                    'uri="http://www.xes-standard.org/concept.xesext"/>\n'
                    '<extension name="Time" prefix="time" '
                    'uri="http://www.xes-standard.org/time.xesext"/>\n'
                    '<extension name="Organizational" prefix="org" '
                    'uri="http://www.xes-standard.org/org.xesext"/>\n'
                    '<global scope="trace"><string key="concept:name" value=""/></global>\n'
                    '<global scope="event"><string key="concept:name" value=""/>'
                    '<date key="time:timestamp" value="1970-01-01T00:00:00+00:00"/>'
                    '<date key="start_timestamp" value="1970-01-01T00:00:00+00:00"/>'
                    '</global>\n'
                    '<classifier name="Activity" keys="concept:name"/>\n')
            total = len(self.instances)
            every = max(1, total // 100)
            for n, inst in enumerate(self.instances):
                if progress and n % every == 0:
                    progress(n / total)
                p, c = cat.procedures[inst["pid"]], inst["citizen"]
                org = (p["org"] if lang == "el" else p["org_en"] or p["org"]) or ""
                out = ["<trace>", attr("string", "concept:name", inst["case"]),
                       attr("string", "citizen_id", c["id"]),
                       attr("string", "service_id", inst["pid"]),
                       attr("string", "service",
                            cat.title(inst["pid"], "el" if lang == "el" else "en")),
                       attr("string", "channel", inst["channel"]),
                       attr("string", "organisation", org),
                       attr("string", "citizen_region", c["region"]),
                       attr("string", "citizen_age_group", c["age"])]
                if both:
                    out.append(attr("string", "service_el", p["title"]))
                for s, start, end in inst["events"]:
                    out += ["<event>", attr("string", "concept:name", name(s["title"])),
                            attr("date", "time:timestamp", stamp(end)),
                            attr("date", "start_timestamp", stamp(start)),
                            attr("string", "org:role", name(s["role"])),
                            attr("string", "method", name(s["how"]))]
                    if both:
                        out.append(attr("string", "activity_el", s["title"]))
                    out.append("</event>")
                out.append("</trace>\n")
                f.write("".join(out))
            f.write("</log>\n")

    # -- output -------------------------------------------------------------------
    def write(self, out: str, lang: str, args: dict, fmt: str = "csv",
              compress: bool = False, progress=None) -> dict:
        """Write the log (``fmt``: csv, xes or both), the ground truth and the
        manifest. ``progress(fraction)`` follows the writing of the log."""
        os.makedirs(out, exist_ok=True)
        cat, stamp = self.cat, lambda t: t.isoformat(sep=" ", timespec="seconds")
        both = lang == "both"
        name = (lambda el: el or "") if lang == "el" else cat.en
        want_csv = fmt in ("csv", "both")
        share = 0.5 if fmt == "both" else 1.0   # of the progress bar, for the CSV
        report = (lambda x: progress(x * share)) if progress and want_csv else None
        every = max(1, len(self.instances) // 50)
        rows = []
        for n, inst in enumerate(self.instances if want_csv else ()):
            if report and n % every == 0:
                report(0.4 * n / len(self.instances))
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

        def dump(fname, head, lines, report=None, total=0):
            with open(os.path.join(out, fname), "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, lineterminator="\n")  # same bytes on every platform
                w.writerow(head)
                if not report:
                    w.writerows(lines)
                    return
                for n, line in enumerate(lines):
                    if n % 20000 == 0:
                        report(0.4 + 0.6 * n / max(total, 1))
                    w.writerow(line)

        log_files = []
        if want_csv:
            dump("events.csv", header, (r for _, r in rows), report, len(rows))
            log_files.append("events.csv")
        if progress:
            progress(share if want_csv else 0.0)
        if fmt in ("xes", "both"):
            xes = "events.xes.gz" if compress else "events.xes"
            lo = 0.5 if fmt == "both" else 0.0
            self.write_xes(os.path.join(out, xes), lang, compress,
                           (lambda x: progress(lo + (1 - lo) * x)) if progress else None)
            log_files.append(xes)
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
            "generator": "MITOS-JourneyGen generate.py", "version": __version__,
            "arguments": args,
            "log_files": log_files,
            "catalogue": cat.meta, "citizens": len({i["citizen"]["id"] for i in self.instances}),
            "instances": len(self.instances), "events": self.n_events,
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


def check(cat: Catalogue, cfg: dict) -> list:
    """Problems that would stop or distort a run, as sentences. A scenario
    written against an older catalogue can name a procedure that MITOS has
    since withdrawn or emptied."""
    out = []

    def usable(pid):
        p = cat.procedures.get(pid)
        if p is None:
            return "is not in the catalogue"
        if not (p["steps"] or p["digital_steps"]):
            return "has no steps in the catalogue"
        return None

    for scen in cfg["scenarios"]:
        ids = [item["id"] for item in scen["procedures"]]
        for item in scen["procedures"]:
            why = usable(item["id"])
            if why:
                out.append(f"Scenario '{scen['label']}': procedure {item['id']} {why}.")
            for a in item.get("after", []):
                if a not in ids:
                    out.append(f"Scenario '{scen['label']}': procedure {item['id']} waits for "
                               f"{a}, which the scenario does not list.")
    for pid in cfg["background"]["services"]:
        why = usable(pid)
        if why:
            out.append(f"Background service {pid} {why}.")
    if cfg["background"]["rate_per_year"] > 0 and not cfg["background"]["services"]:
        out.append("Background use has a rate but no service to draw from.")
    out += _out_of_range(cfg)
    if sum(cfg["population"]["age_groups"].values()) <= 0:
        out.append("Every age group has a share of zero, so there are no citizens.")
    if not cfg["population"]["regions"]:
        out.append("The population has no region.")
    return out


# The largest values a run accepts. Far beyond anything realistic, they only
# stop a configuration, typed or loaded from a file, that would make a run
# take forever or exhaust memory.
LIMITS = {"years": 10, "life_event_rate": 2.0, "background_rate": 50.0,
          "delay_days": 3650, "max_attempts": 10, "evidence_depth": 5,
          "evidence_validity_days": 36500}


def _out_of_range(cfg: dict) -> list:
    """Values outside what a run can handle, as sentences."""
    out = []

    def number(x):
        # NaN and infinity fail the range comparison below
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    def within(value, low, high, what):
        if not number(value) or not low <= value <= high:
            out.append(f"{what} is {value!r}; it must be between {low} and {high}.")

    within(cfg["window"]["years"], 0.1, LIMITS["years"], "The window in years")
    b = cfg["behaviour"]
    for key in ("evidence_probability", "alternative_evidence_probability",
                "reuse_evidence_probability", "abandon_probability",
                "negative_branch_weight", "retry_probability"):
        if key in b:
            within(b[key], 0, 1, f"Behaviour '{key}'")
    if "max_attempts" in b:
        within(b["max_attempts"], 1, LIMITS["max_attempts"], "Behaviour 'max_attempts'")
    if "evidence_depth" in b:
        within(b["evidence_depth"], 0, LIMITS["evidence_depth"], "Behaviour 'evidence_depth'")
    if "evidence_validity_days" in b:
        within(b["evidence_validity_days"], 0, LIMITS["evidence_validity_days"],
               "Behaviour 'evidence_validity_days'")
    for d in b.get("retry_delay_days", []):
        within(d, 0, LIMITS["delay_days"], "A retry delay in days")
    within(cfg["background"]["rate_per_year"], 0, LIMITS["background_rate"],
           "The background rate per year")
    for scen in cfg["scenarios"]:
        name = f"Scenario '{scen.get('label', scen.get('id'))}'"
        for group, rate in scen["rate_per_year"].items():
            within(rate, 0, LIMITS["life_event_rate"], f"{name}: the rate for {group}")
        for item in scen["procedures"]:
            if "p" in item:
                within(item["p"], 0, 1, f"{name}: the chance of {item['id']}")
            for d in item.get("delay_days", []):
                within(d, 0, LIMITS["delay_days"], f"{name}: a delay of {item['id']}")
    return out


def review(cat: Catalogue, cfg: dict, n_citizens: int | None = None,
           rate_scale: float = 1.0) -> list:
    """Values that are allowed but probably not meant, or not realistic.

    Returns dicts with ``where`` ("scenario:<position>", "population",
    "behaviour", "background" or "run"), ``level`` ("warning" for something
    that distorts the log, "note" for something to be aware of) and ``text``.
    Nothing here stops a run; :func:`check` does that.
    """
    out = []

    def say(where, level, text):
        out.append({"where": where, "level": level, "text": text})

    pop, b, bg = cfg["population"], cfg["behaviour"], cfg["background"]
    years = cfg["window"]["years"]
    total = sum(pop["age_groups"].values()) or 1.0
    share = {a: v / total for a, v in pop["age_groups"].items()}

    for pos, scen in enumerate(cfg["scenarios"]):
        where = f"scenario:{pos}"
        rates = {a: r * rate_scale for a, r in scen["rate_per_year"].items()
                 if r > 0 and share.get(a, 0) > 0}
        if not rates:
            say(where, "warning", "This life event never happens: its rate is zero for every "
                "age group that has citizens.")
        else:
            age, top = max(rates.items(), key=lambda kv: kv[1])
            if top > 0.5:
                say(where, "warning", f"Citizens aged {age} go through this {top:.2f} times a "
                    "year on average. Few life events are that frequent.")
            lam = top * years
            twice = 1 - math.exp(-lam) * (1 + lam)
            if twice > 0.05:
                say(where, "warning", f"About {twice:.0%} of citizens aged {age} will go "
                    f"through this twice or more in the {years}-year window. For an event "
                    "such as a death in the family or a retirement that is not plausible: "
                    "lower the rate or shorten the window.")
            if n_citizens:
                expected = n_citizens * sum(share[a] * r * years for a, r in rates.items())
                if expected < 30:
                    say(where, "note", f"Only about {expected:.0f} episodes are expected with "
                        f"{n_citizens:,} citizens. That is too few for a method to learn this "
                        "bundle's timing: use more citizens.")
        items = scen["procedures"]
        ids = [x["id"] for x in items]
        if not items:
            say(where, "warning", "This life event starts no procedure, so it leaves no trace "
                "in the log.")
            continue
        effective: dict = {}
        for n, item in enumerate(items):
            name = cat.title(item["id"]) if item["id"] in cat.procedures else item["id"]
            chance = item["p"]
            for a in item.get("after", []):
                if a in ids and ids.index(a) > n:
                    say(where, "warning", f"'{name}' waits for {a}, which is listed after it. "
                        "Procedures are taken in the order listed, so it never starts. Move "
                        "it below the one it waits for.")
                    chance = 0.0
                chance *= effective.get(a, 0.0 if a in ids else 1.0)
            effective[item["id"]] = chance
            if item["p"] <= 0:
                say(where, "warning", f"'{name}' has a chance of 0, so it never starts.")
            elif 0 < chance < 0.02:
                say(where, "note", f"'{name}' starts in only about {chance:.1%} of episodes, "
                    "once the chances of what it waits for are counted.")
            hi = item["delay_days"][1]
            if hi > years * 365:
                say(where, "warning", f"'{name}' can wait {hi:.0f} days, longer than the "
                    f"{years}-year window. Much of it will fall long after everything else.")
        # what an episode holds on average: its procedures and their evidence
        size = 0.0
        over = scen.get("evidence", {})
        for item in items:
            evidence = 0.0
            for r in cat.procedures.get(item["id"], {}).get("requires", []):
                q = cat.procedures.get(r["id"])
                if q and (q["steps"] or q["digital_steps"]):
                    evidence += over.get(r["id"], b["alternative_evidence_probability"]
                                         if r["alternative"] else b["evidence_probability"])
            size += effective[item["id"]] * (1 + evidence)
        if size < 1.5:
            say(where, "warning", f"An episode holds {size:.1f} instances on average, evidence "
                "included. Many episodes will be a single instance, which leaves nothing to "
                "find. Raise some chances or add procedures.")

    if all(v >= 0.95 for v in pop["digital_propensity"].values()) or all(
            v <= 0.05 for v in pop["digital_propensity"].values()):
        say("population", "note", "Nearly everyone uses the same channel, so the log shows "
            "only one of the two step trees of each procedure.")

    if b["evidence_probability"] <= 0 and b["alternative_evidence_probability"] <= 0:
        say("behaviour", "warning", "No evidence is ever requested. Bundles shrink to the "
            "procedures a scenario lists, and the register's links leave no trace.")
    if b["reuse_evidence_probability"] <= 0:
        say("behaviour", "warning", "No result is ever reused, so no instance is shared "
            "between episodes and nothing can be learnt about finding shared instances.")
    elif b["reuse_evidence_probability"] >= 1:
        say("behaviour", "note", "A valid result is always reused. Real citizens often request "
            "a certificate again, which is the duplication integration would remove.")
    if b["abandon_probability"] > 0.25:
        say("behaviour", "warning", f"{b['abandon_probability']:.0%} of instances are abandoned "
            "part-way. That is a very unreliable service landscape.")
    if b["negative_branch_weight"] > 0.5:
        say("behaviour", "warning", "A failure branch is almost as likely as a normal one, so "
            "most instances of a branching procedure fail.")
    if b["max_attempts"] <= 1 or b["retry_probability"] < 0.1:
        say("behaviour", "note", "Citizens hardly ever retry after a failure, so a failed "
            "procedure usually ends the part of the episode that depends on it.")
    if not 7 <= b["evidence_validity_days"] <= 730:
        say("behaviour", "note", f"A result is valid for {b['evidence_validity_days']:.0f} days "
            "where MITOS gives no validity. Certificates are usually valid for one to six "
            "months.")

    if bg["rate_per_year"] <= 0:
        say("background", "warning", "There is no background use: every instance belongs to an "
            "episode. Real logs are mostly unrelated use, so this is a far easier problem.")
    elif bg["rate_per_year"] > 12:
        say("background", "warning", f"A citizen makes {bg['rate_per_year']:.0f} unrelated uses "
            "a year, more than one a month. Bundles will be hard to see under that.")

    if rate_scale > 1:
        say("run", "note" if rate_scale <= 3 else "warning",
            f"Life-event rates are multiplied by {rate_scale:g}. That is a stress test: "
            "citizens' lives are busier than real ones.")
    if n_citizens is not None and n_citizens < 500:
        say("run", "note", f"{n_citizens:,} citizens give a small log. It is fine for a first "
            "look, too small to compare methods.")
    return out


def estimate(cat: Catalogue, cfg: dict, n_citizens: int, rate_scale: float = 1.0,
             sample: int = 400) -> dict:
    """Roughly what a run will produce and how long it will take, from a small
    trial run on this machine."""
    trial = Generator(cat, cfg, 12345, rate_scale)
    began = time.perf_counter()
    trial.run(sample)
    took = time.perf_counter() - began
    scale = n_citizens / sample
    events = trial.n_events * scale
    background = sum(1 for i in trial.instances if i["origin"] == "background")
    shared = sum(1 for i in trial.instances
                 if len(i["shared"]) + bool(i["episode"]) > 1 or (i["shared"]
                                                                   and not i["episode"]))
    return {"instances": int(len(trial.instances) * scale), "events": int(events),
            "episodes": int(len(trial.episodes) * scale),
            "background_instances": int(background * scale),
            "shared_instances": int(shared * scale),
            # simulating is about half of a run; sorting and writing are the rest
            "seconds": took * scale * 2.2,
            "csv_mb": events * 240 / 1e6, "xes_mb": events * 375 / 1e6,
            "xes_gz_mb": events * 28 / 1e6}


def describe(cat: Catalogue, cfg: dict) -> None:
    """Print each scenario's procedures with the evidence MITOS attaches, the
    labels left in Greek, and the remarks of :func:`review`."""
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
    remarks = review(cat, cfg)
    print(f"\n{len(remarks)} remarks on the values chosen:")
    for r in remarks:
        where = r["where"]
        if where.startswith("scenario:"):
            where = cfg["scenarios"][int(where.split(":")[1])]["label"]
        print(f"   [{r['level']}] {where}: {r['text']}")


def main() -> None:
    """The command line: ``python generate.py --help``."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--citizens", type=int, default=2000,
                    help="size of the population (default 2000)")
    ap.add_argument("--seed", type=int, default=1,
                    help="random seed; the same seed gives the same log (default 1)")
    ap.add_argument("--out", default=os.path.join(HERE, "out"),
                    help="folder to write the log, the ground truth and the manifest "
                         "(default out/)")
    ap.add_argument("--lang", choices=("en", "el", "both"), default="en",
                    help="labels in English (Greek where MITOS has no English), "
                         "Greek, or English plus Greek columns")
    ap.add_argument("--format", choices=("csv", "xes", "both"), default="csv",
                    help="the log as CSV, as XES (start in start_timestamp), or both")
    ap.add_argument("--gzip", action="store_true", help="write the XES log gzipped")
    ap.add_argument("--rate-scale", type=float, default=1.0,
                    help="multiply every life-event rate, to make episodes of one "
                         "citizen overlap more often (a stress test)")
    ap.add_argument("--scenarios", default=os.path.join(HERE, "scenarios.json"),
                    help="life-event scenarios, population and behaviour "
                         "(default scenarios.json)")
    ap.add_argument("--catalogue", default=os.path.join(HERE, "catalogue.json.gz"),
                    help="MITOS catalogue snapshot (default catalogue.json.gz)")
    ap.add_argument("--translations", default=os.path.join(HERE, "translations_en.json"),
                    help="English renderings of labels MITOS has only in Greek "
                         "(default translations_en.json)")
    ap.add_argument("--describe", action="store_true",
                    help="print the scenarios with their evidence and exit")
    args = ap.parse_args()
    # Greek labels on a Windows console; a stream that cannot be
    # reconfigured (redirected, or not a text stream) is left as it is.
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")
    cat = Catalogue(args.catalogue, args.translations)
    with open(args.scenarios, encoding="utf-8") as f:
        cfg = json.load(f)
    if args.describe:
        describe(cat, cfg)
        return
    problems = check(cat, cfg)
    if problems:
        raise SystemExit("Cannot generate:\n  " + "\n  ".join(problems))
    warnings = [r for r in review(cat, cfg, args.citizens, args.rate_scale)
                if r["level"] == "warning"]
    if warnings:
        print(f"{len(warnings)} values look unrealistic; run with --describe to see them.")
    gen = Generator(cat, cfg, args.seed, args.rate_scale)
    began, shown = time.perf_counter(), [0.0]

    def progress(done, total, instances, episodes, events):
        now = time.perf_counter() - began
        if now - shown[0] >= 5 and done < total:  # only a long run reports
            shown[0] = now
            left = now * (total - done) / max(done, 1)
            print(f"  {done:,} of {total:,} citizens, {events:,} events, "
                  f"about {left:.0f} s of simulation left", flush=True)

    gen.run(args.citizens, progress)
    manifest = gen.write(args.out, args.lang, {
        "citizens": args.citizens, "seed": args.seed, "lang": args.lang,
        "rate_scale": args.rate_scale, "format": args.format,
        "scenarios": os.path.basename(args.scenarios)}, args.format, args.gzip)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("arguments", "catalogue")},
                     indent=1))
    print("written to", args.out)


if __name__ == "__main__":
    main()
