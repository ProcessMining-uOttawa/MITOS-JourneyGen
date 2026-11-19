# How MITOS-JourneyGen is tested

This page explains what the tests are meant to guarantee, how they are
organised, and how three complementary checks (security, coverage and
mutation) are used to judge them. The latest results, test case by test case,
are in [test_results.md](test_results.md).

## What must hold

The tool makes data that others use to evaluate methods, so the tests protect
four promises:

1. **Faithful to MITOS.** Every service follows the published step tree of its
   procedure, durations stay within the bounds MITOS gives, and the evidence
   links come from the register.
2. **Reproducible.** The same seed, scenarios and catalogue give the same log,
   byte for byte, on Linux and on Windows.
3. **Correct answers.** The ground truth, the scores of methods and the ranked
   recommendations are computed as documented.
4. **Safe with what it is given.** Answers from the MITOS API, project files
   loaded into the app and uploaded journeys cannot make the tool write where
   it should not, emit invalid files, or run for ever.

## Kinds of tests

| Kind | What it checks | How the expected result is obtained | Files |
|---|---|---|---|
| Worked examples | Functions on small inputs whose answer can be computed by hand | Worked out by hand from the input, independently of the code (e.g. 3 right pairs out of 6 found and 4 true gives precision 0.5, recall 0.75, F1 0.6) | `test_bundles.py`, `test_score.py`, `test_catalogue.py` |
| Rules of a generated log | The properties the generator promises, on freshly generated data | Stated rules: durations within the MITOS bounds, counter steps in office hours, at most `max_attempts` tries, no failure branch when its weight is 0, no reuse when its probability is 0, reuse only while a result is valid | `test_generator_rules.py` |
| Remarks and checks | Unrealistic or impossible configurations are reported or refused | One configuration per remark or limit | `test_review.py`, `test_security.py` |
| Command lines | Each command end to end on a small dataset, and its `--help` | Files written, columns present, key lines printed; every option explained | `test_command_line.py` |
| Catalogue | Download, build and comparison | A made-up MITOS served from memory or from a fake cache: **no test touches the network** | `test_catalogue.py`, `test_command_line.py` |
| Golden dataset | The whole generator at once | `out/` regenerated from seed 1 must equal the committed files byte for byte | `test_default_dataset.py` |
| App | Every view of the Streamlit app, driven without a browser | Views render without error; the Start page leads to Generate; the app's log equals the command line's for the same seed | `test_app.py` |
| Release and deployment | What a release relies on | The version and the changelog agree; the configuration fixes no port; the release workflow moves the branch the hosted app watches | `test_release.py`, `test_assets.py` |
| Security | The fixes the security scan led to | See below | `test_security.py` |

The golden test and the rule tests complement each other. The golden test
notices *any* change to the generator, including an intended one, but cannot
say what went wrong. The rule tests state each promise separately, so a
failure names the broken rule, and an intended change of behaviour only
requires regenerating `out/`.

## Security

**Tool.** [bandit](https://bandit.readthedocs.io), configured in `bandit.yaml`:

```bash
python -m bandit -r . -c bandit.yaml
```

It scans the code and the prototypes. The tests are left out: they use
`assert` by design (B101) and parse only XML they wrote themselves.

**First scan (bandit 1.9.4)**: one medium and four low findings, all resolved.

| Finding | Where | Resolution |
|---|---|---|
| B310 `urlopen` accepts any scheme (medium) | `build_catalogue._get` | Only `https://api.digigov.grnet.gr/v1/` is fetched; anything else raises. |
| B406 `xml.sax.saxutils.quoteattr` (low) | XES writers | Replaced by `generate.xml_attr`, which also drops the characters XML 1.0 forbids. Nothing in the tool parses XML. |
| B311 `random` (low) | `Generator` | Kept, marked `# nosec B311`: a seeded simulation must be reproducible and holds no secret. |
| B110 `try`/`except`/`pass` (low) | `generate.main` | Replaced by `contextlib.suppress` of the three errors that call can raise. |

Later additions are held to the same rule. `docs/build_test_report.py` runs
pytest and bandit as subprocesses (B404, B603) and reads the JUnit XML pytest
has just written (B405, B314): fixed arguments, no shell, no outside input,
so these are accepted and marked `# nosec` with the reason on the line above.
Every accepted finding is marked this way; the results page counts them.

**Manual review** of what bandit cannot see:

| Risk | Resolution |
|---|---|
| A procedure id from the API names a file in the cache (path traversal, e.g. `../../x`) | Ids must be 1 to 12 digits, checked before any file or URL is built. |
| A project file loaded into the hosted app bypasses the widgets' limits (a rate of 500 a year, a 1,000-year window) and keeps a shared server busy | `check()` refuses values a run cannot handle; `generate.LIMITS` lists them. |
| Values shown in the app's HTML tables | Already escaped with `html.escape`; no change needed. |
| Free text from MITOS in the XES (control characters) | Dropped by `xml_attr`, so the log stays well-formed. |

Each fix has a test in `test_security.py`, and CI runs bandit on every pull
request: any finding fails the job.

## Coverage

Measured with [coverage.py](https://coverage.readthedocs.io) through
pytest-cov; `.coveragerc` leaves out the tests and the logo script:

```bash
python -m pytest tests --cov --cov-report=term-missing
```

Coverage rose from **81%** to about **92%** with this round of tests. CI fails
below 85%. The least covered part is `app.py` (about 82%): Streamlit's test
harness cannot drive file uploads or the catalogue refresh, which are covered
only through the functions they call.

Coverage says which lines ran, not whether a wrong result would be noticed. A
test that runs a function and checks nothing raises coverage without testing
anything. That is what the mutation check is for.

## Mutation

**Method.** `tests/mutation_check.py` plants realistic bugs in the code, one
at a time: an unescaped character in the XES, an expired certificate reused,
a retry too many, a failed download accepted as a refresh, precision and
recall swapped, and so on (22 in all, across the generator, the catalogue,
the recommendations and the scoring). For each, it runs the tests (all but
the slow app tests) and restores the file. A bug is *caught* when some test
fails, and *survives* when every test still passes. Tools such as mutmut do
not run on Windows, hence a hand-written list of bugs chosen to be the
mistakes most likely to matter.

```bash
python tests/mutation_check.py --without-golden --json docs/mutation_results.json
```

`--without-golden` leaves out the golden dataset test, so every bug must be
caught by a test that states the rule it breaks.

**What it found.** The first run caught 16 of 22. The survivors were real
gaps: no test checked the precision, recall and F1 formulas against a known
answer, two filters of the recommendations (minimum number of journeys,
minimum lift) could be removed unnoticed, and the retry limit was never
reached in the test data. One survivor was an *equivalent* mutant: the
`isfinite` test it removed was redundant, because NaN and infinity already
fail the range comparison, so that check was removed from the code. Tests
were added for the others; six generator bugs that only the golden test
caught now each break a rule test. Every planted bug is now caught, with or
without the golden test.

**A test that targets a bug is run against the code with that bug.** Besides
the planted bugs above, the test that every command line explains its
options was run on the code before their help texts were written: it failed
on all nine command lines (and showed that `catalogue_changes.py --help`
crashed, and that `prototype/score.py` had no `--help` at all).

## Continuous integration

`.github/workflows/tests.yml` runs on every push to `main` and every pull
request:

| Job | Runs |
|---|---|
| Linux, Python 3.10 and 3.13; Windows, Python 3.12 | A command-line smoke run, then all tests with coverage (floor 85%). Windows matters: the golden test checks the same bytes on both systems. |
| Security scan | bandit with `bandit.yaml`; any finding fails. |

The mutation check is not in CI (about fifteen minutes); run it before a
release, or after changing a test.

## Running everything

```bash
pip install -r requirements.txt pytest pytest-cov bandit
```

```bash
python -m pytest tests -q
```

```bash
python docs/build_test_report.py
```

The last command runs the tests with coverage and bandit, reads the latest
mutation results, and rewrites [test_results.md](test_results.md).

## Adding a test

- Prefer a small input whose answer you can work out by hand, and write that
  answer in the test, not one computed by the code under test.
- For a generator change, add or adapt a rule in `test_generator_rules.py`,
  then regenerate `out/` (`python generate.py --citizens 2000 --seed 1`).
- When a test is meant to catch a bug, check that it does: break the code on
  purpose, or add the bug to `tests/mutation_check.py`.
- Nothing may use the network; serve MITOS from memory as
  `test_catalogue.py` does.
