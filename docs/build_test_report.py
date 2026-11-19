"""Run the tests and write docs/test_results.md: every test case with its
result, coverage by module, the security scan and the mutation check.

    pip install pytest pytest-cov bandit
    python docs/build_test_report.py

The mutation check takes about fifteen minutes, so it is not run here: its
last results are read from docs/mutation_results.json, written by
``python tests/mutation_check.py --without-golden --json docs/mutation_results.json``.
"""
import ast
import json
import os
import platform
import re
# Runs this repository's own tools (pytest, bandit) with fixed arguments and no shell.
import subprocess  # nosec B404
import sys
import tempfile
import time
# Parses only the JUnit report pytest has just written.
import xml.etree.ElementTree as ET  # nosec B405

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = re.compile(r"#\s*nosec\s+B\d+")
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "test_results.md")


def sentence(name: str) -> str:
    """``test_a_rare_pair_is_not_kept[x]`` -> ``A rare pair is not kept (x)``."""
    base, _, param = name.partition("[")
    words = base.removeprefix("test_").replace("_", " ")
    text = words[:1].upper() + words[1:]
    return text + (f" ({param.rstrip(']')})" if param else "")


def module_summary(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        doc = ast.get_docstring(ast.parse(f.read())) or ""
    return " ".join(doc.split("\n\n")[0].split())


def run_tests(tmp: str):
    junit, cov = os.path.join(tmp, "junit.xml"), os.path.join(tmp, "coverage.json")
    began = time.perf_counter()
    subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
                    f"--junitxml={junit}", "--cov", f"--cov-report=json:{cov}"],
                   cwd=ROOT, capture_output=True, text=True)  # nosec B603
    took = time.perf_counter() - began
    cases = []
    for case in ET.parse(junit).getroot().iter("testcase"):  # nosec B314
        outcome = "passed"
        for tag in ("failure", "error", "skipped"):
            if case.find(tag) is not None:
                outcome = {"failure": "FAILED", "error": "ERROR", "skipped": "skipped"}[tag]
        cases.append({"file": case.get("classname").split(".")[-1] + ".py",
                      "name": case.get("name"), "seconds": float(case.get("time") or 0),
                      "outcome": outcome})
    with open(cov, encoding="utf-8") as f:
        coverage = json.load(f)
    return cases, coverage, took


def run_bandit() -> dict:
    out = subprocess.run([sys.executable, "-m", "bandit", "-r", ".", "-c", "bandit.yaml",
                          "-f", "json", "-q"], cwd=ROOT, capture_output=True,
                         text=True)  # nosec B603
    data = json.loads(out.stdout)
    return {"issues": data["results"], "loc": data["metrics"]["_totals"]["loc"],
            "nosec": nosec_lines()}


def nosec_lines() -> int:
    """Lines where a bandit finding was reviewed and accepted (``# nosec``)."""
    count = 0
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "tests", "out", "paper", "zenodo")]
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    count += sum(1 for line in f if MARK.search(line))
    return count


def main() -> None:
    sys.path.insert(0, ROOT)
    import generate  # noqa: E402
    with tempfile.TemporaryDirectory() as tmp:
        cases, coverage, took = run_tests(tmp)
    sec = run_bandit()
    mut_path = os.path.join(HERE, "mutation_results.json")
    mutation = json.load(open(mut_path, encoding="utf-8")) if os.path.exists(mut_path) else None

    passed = sum(1 for c in cases if c["outcome"] == "passed")
    total = coverage["totals"]
    lines = [
        "# Test results",
        "",
        f"MITOS-JourneyGen {generate.__version__}, run on {time.strftime('%Y-%m-%d')} with "
        f"Python {platform.python_version()} on {platform.system()}. Written by "
        "`docs/build_test_report.py`; the strategy behind these tests is in "
        "[testing.md](testing.md).",
        "",
        "| | |",
        "|---|---|",
        f"| Test cases | **{passed} of {len(cases)} passed** in {took:.0f} s |",
        f"| Statement coverage | **{total['percent_covered']:.0f}%** "
        f"({total['covered_lines']:,} of {total['num_statements']:,} statements) |",
        f"| Security scan (bandit) | **{len(sec['issues'])} issues** in "
        f"{sec['loc']:,} lines; {sec['nosec']} reviewed exceptions marked `# nosec` |",
    ]
    if mutation:
        killed = sum(1 for m in mutation["mutants"] if m["verdict"] == "killed")
        lines.append(f"| Mutation check | **{killed} of {len(mutation['mutants'])} planted "
                     f"bugs caught**" + (" without the byte-for-byte test" if
                                          mutation["without_golden"] else "")
                     + f" ({mutation['date']}) |")
    lines += ["", "## Coverage by module", "", "| Module | Statements | Covered |",
              "|---|---:|---:|"]
    for path, data in sorted(coverage["files"].items()):
        s = data["summary"]
        lines.append(f"| `{path.replace(os.sep, '/')}` | {s['num_statements']} | "
                     f"{s['percent_covered']:.0f}% |")
    if mutation:
        lines += ["", "## Mutation check", "",
                  "Each row is a bug planted in the code, and a test that fails because of "
                  "it (with or without the byte-for-byte test of out/).", "",
                  "| # | File | Planted bug | Result | Caught by |", "|---:|---|---|---|---|"]
        for m in mutation["mutants"]:
            by = m["caught_by"].replace("FAILED ", "").replace("tests/", "")
            lines.append(f"| {m['n']} | `{m['file']}` | {m['bug']} | {m['verdict']} | "
                         f"{by.replace('|', '/')} |")
    lines += ["", "## Test cases", ""]
    for file in sorted({c["file"] for c in cases}):
        mine = [c for c in cases if c["file"] == file]
        ok = sum(1 for c in mine if c["outcome"] == "passed")
        lines += [f"### `tests/{file}` ({ok} of {len(mine)} passed)", "",
                  module_summary(os.path.join(ROOT, "tests", file)), "",
                  "| Test case | Result | Seconds |", "|---|---|---:|"]
        for c in mine:
            lines.append(f"| {sentence(c['name']).replace('|', '/')} | {c['outcome']} | "
                         f"{c['seconds']:.1f} |")
        lines.append("")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    print(f"{passed} of {len(cases)} passed, {total['percent_covered']:.0f}% coverage; "
          f"wrote {OUT}")


if __name__ == "__main__":
    main()
