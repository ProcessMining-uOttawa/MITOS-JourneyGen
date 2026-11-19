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
import re
import time  # noqa: F401 - also used by the command line's progress
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://api.digigov.grnet.gr/v1/"
_ID = re.compile(r"[0-9]{1,12}")


def _get(url: str) -> dict:
    """GET a JSON document from the MITOS API, and from nowhere else."""
    if not url.startswith(API):
        raise ValueError(f"not a MITOS API address: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "MITOS-JourneyGen"})
    # The address is checked above: https, on the MITOS API host only.
    with urllib.request.urlopen(req, timeout=60) as r:  # nosec B310
        return json.loads(r.read().decode("utf-8"))


def _procedure_id(value) -> str:
    """A procedure id from the API, checked before it names a file or a URL."""
    pid = str(value)
    if not _ID.fullmatch(pid):
        raise ValueError(f"unexpected procedure id from MITOS: {pid!r}")
    return pid


def _paged(endpoint: str, limit: int) -> list:
    rows, page = [], 1
    while page:
        r = _get(f"{API}{endpoint}?page={page}&limit={limit}")
        rows += r["data"]
        page = r.get("next_page")
        time.sleep(0.2)
    return rows


def download(cache: str, progress=None) -> list:
    """Fill ``cache`` with the raw API answers; files already there are kept.
    Returns what could not be fetched.

    ``progress(done, total, failed)`` is called from the calling thread after
    every document, and once with ``total`` 0 while the lists are being read.
    It may raise to stop the download; what was fetched stays in the cache.

    The cache is for resuming an interrupted download. Start a refresh from
    an empty one, or documents fetched earlier are taken as current.
    """
    failed = []
    if progress:
        progress(0, 0, 0)
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
        failed.append((sub, pid))

    jobs = [(_procedure_id(r["id"]), "proc", "") for r in rows]
    jobs += [(_procedure_id(r["id"]), "proc_en", "?lang=en") for r in rows
             if r["title"].get("en")]
    pool = ThreadPoolExecutor(3)
    try:
        for done, _ in enumerate(pool.map(one, jobs), 1):
            if progress:
                progress(done, len(jobs), len(failed))
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)  # stopped: do not finish the queue
        raise
    pool.shutdown()
    return failed


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
    """The catalogue built from a cache filled by :func:`download`: for every
    procedure its titles, owner, life events, validity, counter and digital
    step trees and the procedures it requires as evidence, plus the official
    Greek-to-English strings learnt from the procedures MITOS publishes in both
    languages."""
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
    """The command line: ``python build_catalogue.py --help``."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", required=True, help="directory for the raw API answers")
    ap.add_argument("--offline", action="store_true", help="do not download, only rebuild")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "catalogue.json.gz"))
    args = ap.parse_args()
    if not args.offline:
        began, shown = time.perf_counter(), [-10.0]

        def progress(done, total, failed):
            now = time.perf_counter() - began
            if not total:
                print("Reading the lists of procedures and organisations", flush=True)
            elif (now - shown[0] >= 10 and done >= 25) or done == total:
                shown[0] = now
                left = now * (total - done) / max(done, 1)
                print(f"  {done:,} of {total:,} documents, {now / 60:.1f} min elapsed, "
                      f"about {left / 60:.1f} min left"
                      + (f", {failed} failed" if failed else ""), flush=True)

        failed = download(args.cache, progress)
        if failed:
            # A procedure missing from the cache would look withdrawn.
            raise SystemExit(f"{len(failed)} documents could not be fetched; the catalogue "
                             "was not rebuilt. Run again: what was fetched is kept.")
    cat = build(args.cache)
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, separators=(",", ":"))
    n = cat["procedures"]
    print(f"{len(n)} procedures, {sum(1 for p in n.values() if p['requires'])} with "
          f"evidence links, {len(cat['official_en'])} official English strings "
          f"-> {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
