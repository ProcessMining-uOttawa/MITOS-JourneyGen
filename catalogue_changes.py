"""Say what changed between two MITOS catalogue snapshots.

    python catalogue_changes.py OLD.json.gz NEW.json.gz

Compares what the generator uses of each procedure: its titles, owner,
validity, the two step trees and the evidence links. MITOS's own update
dates are no guide, because automated bulk edits touch most procedures every
week; only a difference in content counts here.

Writes ``catalogue_changes.json`` (the latest comparison, read by the app)
and puts a section at the top of ``CATALOGUE_CHANGES.md`` (the history).
Reports which bundled scenarios are affected. Standard library only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HEADER = ("# Catalogue changes\n\n"
          "What changed in MITOS between two refreshes of `catalogue.json.gz`, newest first.\n"
          "Each refresh is tagged `catalogue-<date>`, so an older catalogue can be checked out.\n")


def load(path: str) -> dict:
    """A catalogue (``catalogue.json.gz``) as a dict."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _tree(steps: list) -> list:
    return [(s["n"], s["parent"], s["exit"]) for s in steps]


def differences(a: dict, b: dict) -> list:
    """Why procedure ``b`` is not ``a``, as short phrases; empty when equal."""
    out = []
    if (a["title"], a["title_en"]) != (b["title"], b["title_en"]):
        out.append("title")
    for key, label in (("steps", "steps"), ("digital_steps", "digital steps")):
        x, y = a[key], b[key]
        if len(x) != len(y):
            out.append(f"{label}: {len(x)} to {len(y)}")
        elif _tree(x) != _tree(y):
            out.append(f"{label}: branching")
        else:
            if [(s["min"], s["max"]) for s in x] != [(s["min"], s["max"]) for s in y]:
                out.append(f"{label}: durations")
            if [(s["title"], s["role"], s["how"]) for s in x] != [
                    (s["title"], s["role"], s["how"]) for s in y]:
                out.append(f"{label}: names or roles")
    ra = {r["id"]: r["alternative"] for r in a["requires"]}
    rb = {r["id"]: r["alternative"] for r in b["requires"]}
    if ra != rb:
        added, dropped = sorted(set(rb) - set(ra)), sorted(set(ra) - set(rb))
        bits = ([f"+{', +'.join(added)}"] if added else []) + (
            [f"-{', -'.join(dropped)}"] if dropped else [])
        out.append("evidence: " + (" ".join(bits) if bits else "alternative flag"))
    if a["validity"] != b["validity"]:
        out.append(f"validity: {a['validity'] or 'none'} to {b['validity'] or 'none'}")
    if (a["org"], a["org_en"]) != (b["org"], b["org_en"]):
        out.append("owner")
    return out


def compare(old: dict, new: dict, scenarios: dict | None = None) -> dict:
    """What changed from catalogue ``old`` to ``new``: dates, new, withdrawn and
    changed procedures (with :func:`differences`), and, given ``scenarios``,
    the points in them that the changes touch. ``any`` says whether anything
    changed."""
    po, pn = old["procedures"], new["procedures"]
    title = lambda p: p["title_en"] or p["title"]  # noqa: E731
    added = sorted(set(pn) - set(po))
    withdrawn = sorted(set(po) - set(pn))
    changed = {}
    for pid in sorted(set(po) & set(pn)):
        why = differences(po[pid], pn[pid])
        if why:
            changed[pid] = why
    result = {
        "from": old["retrieved"], "to": new["retrieved"],
        "procedures": len(pn),
        "new": [{"id": i, "title": title(pn[i])} for i in added],
        "withdrawn": [{"id": i, "title": title(po[i])} for i in withdrawn],
        "changed": [{"id": i, "title": title(pn[i]), "what": changed[i]} for i in changed],
        "scenarios": [],
    }
    if scenarios:
        english = set(new["official_en"])
        try:
            with open(os.path.join(HERE, "translations_en.json"), encoding="utf-8") as f:
                english |= set(json.load(f).get("strings", {}))
        except OSError:
            pass
        groups = [(s["label"], [x["id"] for x in s["procedures"]]) for s in scenarios["scenarios"]]
        groups.append(("Background services", list(scenarios["background"]["services"])))
        for label, ids in groups:
            for pid in ids:
                # the procedure itself, and the evidence the register attaches to it
                around = [(pid, "")] + [(r["id"], f" (evidence of {pid})")
                                        for r in (pn.get(pid) or po.get(pid, {})).get(
                                            "requires", [])]
                for qid, role in around:
                    if qid in withdrawn:
                        result["scenarios"].append({
                            "scenario": label, "id": qid, "title": title(po[qid]) + role,
                            "what": ["withdrawn from MITOS"], "action": bool(not role)})
                    elif qid in changed and any(
                            w.startswith(("steps", "digital steps", "evidence", "validity"))
                            for w in changed[qid]):
                        missing = sum(1 for s in pn[qid]["steps"] + pn[qid]["digital_steps"]
                                      if s["title"] and s["title"].strip() not in english
                                      and not pn[qid]["title_en"])
                        what = list(changed[qid])
                        if missing:
                            what.append(f"{missing} step names without English")
                        result["scenarios"].append({
                            "scenario": label, "id": qid, "title": title(pn[qid]) + role,
                            "what": what, "action": False})
        seen, unique = set(), []
        for row in result["scenarios"]:
            key = (row["scenario"], row["id"])
            if key not in seen:
                seen.add(key)
                unique.append(row)
        result["scenarios"] = unique
    result["any"] = bool(added or withdrawn or changed)
    return result


def markdown(r: dict, limit: int = 40) -> str:
    """The comparison ``r`` as a Markdown section, at most ``limit`` rows per list."""
    lines = [f"## {r['to']}", "",
             f"Compared with {r['from']}: {len(r['new'])} new, {len(r['withdrawn'])} withdrawn, "
             f"{len(r['changed'])} changed, of {r['procedures']} procedures.", ""]
    if r["scenarios"]:
        lines += ["**Bundled scenarios affected**", ""]
        for row in r["scenarios"]:
            flag = " **Needs attention.**" if row["action"] else ""
            lines.append(f"- {row['scenario']}: {row['id']} {row['title']}: "
                         f"{'; '.join(row['what'])}.{flag}")
        lines.append("")
    else:
        lines += ["No bundled scenario is affected.", ""]
    for key, head in (("new", "New"), ("withdrawn", "Withdrawn"), ("changed", "Changed")):
        rows = r[key]
        if not rows:
            continue
        lines += [f"**{head}**", ""]
        for row in rows[:limit]:
            extra = f": {'; '.join(row['what'])}" if "what" in row else ""
            lines.append(f"- {row['id']} {row['title']}{extra}")
        if len(rows) > limit:
            lines.append(f"- and {len(rows) - limit} more, in the commit's `catalogue_changes.json`")
        lines.append("")
    return "\n".join(lines)


def too_many_withdrawn(r: dict, old: dict) -> bool:
    """More than 5% gone looks like a failed download, not like MITOS."""
    return len(r["withdrawn"]) > 0.05 * len(old["procedures"])


def record(r: dict, json_path: str, log_path: str) -> None:
    """Write the latest comparison and put it at the top of the history."""
    with open(json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    history = ""
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            history = f.read().replace(HEADER, "", 1).lstrip("\n")
    with open(log_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + "\n" + markdown(r) + ("\n" + history if history else ""))


def main() -> None:
    """The command line: ``python catalogue_changes.py --help``."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("old", help="the previous catalogue (.json.gz)")
    ap.add_argument("new", help="the new catalogue (.json.gz)")
    ap.add_argument("--scenarios", default=os.path.join(HERE, "scenarios.json"),
                    help="scenarios to check against the changes (default scenarios.json)")
    ap.add_argument("--json", default=os.path.join(HERE, "catalogue_changes.json"),
                    help="where to write the latest comparison (default "
                         "catalogue_changes.json)")
    ap.add_argument("--log", default=os.path.join(HERE, "CATALOGUE_CHANGES.md"),
                    help="change log to prepend to (default CATALOGUE_CHANGES.md)")
    ap.add_argument("--force", action="store_true",
                    help="accept a refresh that withdraws more than 5%% of the procedures")
    args = ap.parse_args()
    old, new = load(args.old), load(args.new)
    with open(args.scenarios, encoding="utf-8") as f:
        scenarios = json.load(f)
    r = compare(old, new, scenarios)
    if too_many_withdrawn(r, old) and not args.force:
        sys.exit(f"{len(r['withdrawn'])} procedures are missing from the new catalogue. That "
                 "looks like a failed download rather than MITOS withdrawing them; nothing "
                 "was written. Use --force if it is real.")
    summary = (f"{len(r['new'])} new, {len(r['withdrawn'])} withdrawn, "
               f"{len(r['changed'])} changed")
    print(f"{r['from']} to {r['to']}: {summary}; "
          f"{len(r['scenarios'])} points in the bundled scenarios")
    if r["any"]:
        record(r, args.json, args.log)
    github = os.environ.get("GITHUB_OUTPUT")
    if github:
        with open(github, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if r['any'] else 'false'}\n")
            f.write(f"summary={summary}\n")
            f.write(f"date={r['to']}\n")


if __name__ == "__main__":
    main()
