"""A small mutation check: does the test suite notice when the code is wrong?

Each mutant plants one realistic bug in the code, runs the tests (all but the
slow app tests, stopping at the first failure) and restores the file. A mutant
is *killed* when some test fails, and *survives* when every test still
passes: a gap in what the tests check, whatever the coverage says.

    python tests/mutation_check.py            # every mutant
    python tests/mutation_check.py 3 7        # only these
    python tests/mutation_check.py --without-golden   # without the byte-for-byte test
    python tests/mutation_check.py --json docs/mutation_results.json   # keep the results

``--without-golden`` leaves out the test that regenerates ``out/`` byte for
byte. That test notices any change, so a bug it alone catches is caught for
no stated reason; without it, each bug must break a test of a stated rule.

Not collected by pytest (no ``test_`` prefix). mutmut does not run on
Windows, hence this hand-written list.
"""
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (file, original text, mutated text, what the bug would be)
MUTANTS = [
    ("generate.py", '"&": "&amp;"', '"&": "&"', "XES attributes no longer escape &"),
    ("generate.py", 'text = _XML_INVALID.sub("", str(value))', "text = str(value)",
     "control characters reach the XES"),
    ("generate.py", "if not number(value) or not low <= value <= high:",
     "if not number(value) or not low <= value:", "range check forgets the upper bound"),
    ("generate.py", "if not number(value) or not low <= value <= high:",
     "if not low <= value <= high:", "a value of the wrong type is not reported"),
    ("generate.py", "if a not in ids:", "if a in ids:", "waits for a missing procedure accepted"),
    ("generate.py", 't - inst["end"]).total_seconds() <= valid:',
     't - inst["end"]).total_seconds() <= 10 * valid:', "expired certificates reused"),
    ("generate.py", 'if prior and rng.random() < b["reuse_evidence_probability"]:',
     'if prior and rng.random() > b["reuse_evidence_probability"]:', "reuse chance inverted"),
    ("generate.py", 'for attempt in range(1, b["max_attempts"] + 1):',
     'for attempt in range(1, b["max_attempts"] + 2):', "one retry too many"),
    ("generate.py", "dur = rng.uniform(min(lo, hi), max(lo, hi))",
     "dur = rng.uniform(min(lo, hi), 2 * max(lo, hi))", "durations exceed the MITOS maximum"),
    ("generate.py", "if t.weekday() < 5 and 8 <= t.hour < 15:",
     "if t.weekday() <= 5 and 8 <= t.hour < 15:", "counter open on Saturdays"),
    ("generate.py", "cur = rng.choices(kids, [negative_weight if b else 1.0 for b in bad])[0]",
     "cur = rng.choices(kids, [1.0 for b in bad])[0]", "failure branches as likely as others"),
    ("bundles.py", "m = m[size >= min_size]", "m = m[size > min_size]",
     "journeys of exactly min_size dropped"),
    ("bundles.py", "out = out[out.journeys >= min_journeys]", "out = out[out.journeys >= 0]",
     "candidate pairs not filtered"),
    ("bundles.py", "if (r.both < min_journeys or r.lift < min_lift",
     "if (r.both < min_journeys or r.lift < 0", "low-lift pairs recommended"),
    ("build_catalogue.py", "if not url.startswith(API):", "if False:",
     "downloader fetches from anywhere"),
    ("build_catalogue.py", 'r"[0-9]{1,12}"', 'r".+"', "any procedure id names a file"),
    ("build_catalogue.py", 'if rel["id"] == pid:', 'if rel["id"] == "never":',
     "a procedure requires itself"),
    ("build_catalogue.py", 'requires.get(rel["id"], True) and alt',
     'requires.get(rel["id"], True) or alt', "an alternative overrides a mandatory link"),
    ("catalogue_changes.py", 'return len(r["withdrawn"]) > 0.05 * len(old["procedures"])',
     'return len(r["withdrawn"]) > 0.5 * len(old["procedures"])',
     "a failed download is accepted as a refresh"),
    ("catalogue_changes.py", 'if a["validity"] != b["validity"]:', "if False:",
     "a change of validity goes unreported"),
    ("prototype/score.py", '"F1": 2 * prec * rec / max(prec + rec, 1e-9),',
     '"F1": prec * rec / max(prec + rec, 1e-9),', "F1 computed wrongly"),
    ("prototype/score.py", "prec, rec = hit / max(len(f_pairs), 1), hit / max(len(t_pairs), 1)",
     "prec, rec = hit / max(len(t_pairs), 1), hit / max(len(f_pairs), 1)",
     "precision and recall swapped"),
]

TESTS = ["tests", "-x", "-q", "-p", "no:cacheprovider", "--ignore=tests/test_app.py"]


def main(picked, tests=TESTS, json_path=None):
    results = []
    for n, (path, old, new, what) in enumerate(MUTANTS, 1):
        if picked and n not in picked:
            continue
        full = os.path.join(ROOT, path)
        source = io.open(full, encoding="utf-8", newline="").read()
        if source.count(old) != 1:
            results.append((n, "NOT APPLIED", what, path))
            print(f"{n:2} NOT APPLIED  {what}", flush=True)
            continue
        began = time.perf_counter()
        try:
            io.open(full, "w", encoding="utf-8", newline="").write(source.replace(old, new))
            run = subprocess.run([sys.executable, "-m", "pytest", *tests], cwd=ROOT,
                                 capture_output=True, text=True)
        finally:
            io.open(full, "w", encoding="utf-8", newline="").write(source)
        failed = [line.split(" - ")[0] for line in run.stdout.splitlines()
                  if line.startswith(("FAILED", "ERROR"))]
        verdict = "killed" if run.returncode else "SURVIVED"
        results.append((n, verdict, what, failed[0] if failed else ""))
        print(f"{n:2} {verdict:8} {time.perf_counter() - began:5.0f}s  {what}"
              + (f"  <- {failed[0]}" if failed else ""), flush=True)
    killed = sum(1 for r in results if r[1] == "killed")
    print(f"\n{killed} of {len(results)} mutants killed")
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"without_golden": "--ignore=tests/test_default_dataset.py" in tests,
                       "date": time.strftime("%Y-%m-%d"),
                       "mutants": [{"n": n, "file": MUTANTS[n - 1][0], "bug": what,
                                    "verdict": verdict, "caught_by": by}
                                   for n, verdict, what, by in results]}, f, indent=1)
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    tests = TESTS + (["--ignore=tests/test_default_dataset.py"]
                     if "--without-golden" in args else [])
    out = args[args.index("--json") + 1] if "--json" in args else None
    sys.exit(main({int(a) for a in args if a.isdigit()}, tests, out))
