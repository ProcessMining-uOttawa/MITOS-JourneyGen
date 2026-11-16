"""Build ``catalogue.json.gz`` from the MITOS public API.

MITOS is the Greek National Register of Administrative Procedures
(https://mitos.gov.gr, content under CC BY-SA 4.0). This script downloads
every published procedure, keeps the fields the generator needs, and writes
one compact, offline snapshot.

    python build_catalogue.py --cache raw/            # download + build
    python build_catalogue.py --cache raw/ --offline  # rebuild from the cache

Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://api.digigov.grnet.gr/v1/"


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "MITOS-JourneyGen"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _paged(endpoint: str, limit: int) -> list:
    rows, page = [], 1
    while page:
        r = _get(f"{API}{endpoint}?page={page}&limit={limit}")
        rows += r["data"]
        page = r.get("next_page")
        time.sleep(0.2)
    return rows


def download(cache: str) -> None:
    """Fill ``cache`` with the raw API answers; files already there are kept."""
    for sub in ("proc", "proc_en"):
        os.makedirs(os.path.join(cache, sub), exist_ok=True)
    for name, endpoint, limit in (("list.json", "services", 100),
                                  ("orgs.json", "organizations", 500)):
        path = os.path.join(cache, name)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(_paged(endpoint, limit), f, ensure_ascii=False)
    with open(os.path.join(cache, "list.json"), encoding="utf-8") as f:
        rows = json.load(f)

    def one(job):
        pid, sub, query = job
        path = os.path.join(cache, sub, pid + ".json")
        if os.path.exists(path):
            return
        for attempt in range(3):
            try:
                data = _get(f"{API}services/{pid}{query}")["data"]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                time.sleep(0.15)
                return
            except Exception as exc:  # noqa: BLE001 - reported, then retried
                err = exc
                time.sleep(2 * (attempt + 1))
        print("could not fetch", sub, pid, repr(err)[:80])

    jobs = [(r["id"], "proc", "") for r in rows]
    jobs += [(r["id"], "proc_en", "?lang=en") for r in rows if r["title"].get("en")]
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(one, jobs))


def _load_dir(path: str) -> dict:
    out = {}
    for name in os.listdir(path):
        with open(os.path.join(path, name), encoding="utf-8") as f:
            d = json.load(f)
        out[d["id"]] = d
    return out


def _steps(raw: list, prefix: str) -> list:
    steps = []
    for s in raw or []:
        g = lambda k: s.get(prefix + k)  # noqa: E731
        steps.append({
            "n": g("num_id"),
            "title": (g("title") or "").strip(),
            "role": g("official"),
            "how": g("implementation"),
            "min": g("duration_min"),
            "max": g("duration_max"),
            "parent": g("previous_child") if g("child") else None,
            "exit": bool(g("exit")),
        })
    return steps


def build(cache: str) -> dict:
    el = _load_dir(os.path.join(cache, "proc"))
    en = _load_dir(os.path.join(cache, "proc_en"))
    with open(os.path.join(cache, "orgs.json"), encoding="utf-8") as f:
        orgs = {str(o["id"]): o["title"] for o in json.load(f)}

    # Official Greek -> English strings, learnt from the procedures that
    # MITOS publishes in both languages (aligned field by field).
    official: dict = {}

    def learn(a, b):
        if isinstance(a, str) and isinstance(b, str) and a.strip() != b.strip():
            official.setdefault(a.strip(), b.strip())

    for pid, e in en.items():
        g = el.get(pid)
        if not g:
            continue
        mg, me = g["metadata"], e["metadata"]
        for key, pre in (("process_steps", "step_"),
                         ("process_steps_digital", "step_digital_")):
            sg, se = mg.get(key) or [], me.get(key) or []
            if len(sg) == len(se):
                for a, b in zip(sg, se):
                    for field in ("title", "official", "implementation"):
                        learn(a.get(pre + field), b.get(pre + field))
        for field in ("life_events", "provided_to"):
            a = mg["process"].get(field) or []
            b = me["process"].get(field) or []
            if len(a) == len(b):
                for x, y in zip(a, b):
                    learn(x, y)

    procedures = {}
    for pid, d in sorted(el.items()):
        m = d["metadata"]
        p = m.get("process", {})
        requires = {}
        for ev in m.get("process_evidences") or []:
            for rel in ev.get("evidence_related_process") or []:
                if rel["id"] == pid:
                    continue
                alt = bool(ev.get("evidence_alternative"))
                # mandatory wins when the same procedure is named twice
                requires[rel["id"]] = requires.get(rel["id"], True) and alt
        org = str(p.get("org_owner") or "")
        procedures[pid] = {
            "title": d["title"].get("el"),
            "title_en": d["title"].get("en"),
            "org": orgs.get(org, {}).get("el"),
            "org_en": orgs.get(org, {}).get("en"),
            "life_events": p.get("life_events") or [],
            "applicants": p.get("application_owner") or [],
            "validity": p.get("validity_duration"),
            "estimated_max": p.get("estimated_implementation_duration_max"),
            "steps": _steps(m.get("process_steps"), "step_"),
            "digital_steps": _steps(m.get("process_steps_digital"), "step_digital_"),
            "requires": [{"id": k, "alternative": v} for k, v in sorted(requires.items())],
        }
    return {
        "source": "MITOS, National Register of Administrative Procedures "
                  "(https://mitos.gov.gr), via " + API,
        "licence": "CC BY-SA 4.0",
        "retrieved": dt.date.today().isoformat(),
        "official_en": official,
        "procedures": procedures,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", required=True, help="directory for the raw API answers")
    ap.add_argument("--offline", action="store_true", help="do not download, only rebuild")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "catalogue.json.gz"))
    args = ap.parse_args()
    if not args.offline:
        download(args.cache)
    cat = build(args.cache)
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, separators=(",", ":"))
    n = cat["procedures"]
    print(f"{len(n)} procedures, {sum(1 for p in n.values() if p['requires'])} with "
          f"evidence links, {len(cat['official_en'])} official English strings "
          f"-> {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
