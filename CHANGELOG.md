# Changelog

What changed in each release of MITOS-JourneyGen, newest first. Changes to the
MITOS catalogue itself are in [CATALOGUE_CHANGES.md](CATALOGUE_CHANGES.md) once
a monthly refresh has found any.

## Unreleased

**Security** (from a bandit scan, now clean and run on every pull request)

- The catalogue download only fetches from the MITOS API over https, and
  refuses a procedure id that is not a number before it names a file.
- XES attributes are written with an escaper that also drops the control
  characters XML 1.0 forbids, so free text from MITOS cannot make a log
  invalid.
- A run refuses values far outside what it can handle (a window of more than
  ten years, life-event rates above two a year, chances outside 0 to 1,
  delays of more than ten years ...), so a project file loaded into the hosted
  app cannot make it run for ever.

**Tests and documentation**

- Coverage rose from 81% to about 90%, now measured in CI with a floor of 85%:
  the command lines, the catalogue build and comparison, the scoring of
  methods and the rules a generated log promises (durations within the MITOS
  bounds, counter steps in office hours, the retry limit, reuse only while a
  result is valid) have their own tests.
- `tests/mutation_check.py` plants 22 realistic bugs and checks that the
  tests catch each of them.
- API documentation from the docstrings, built with pdoc and published on
  GitHub Pages for every release; the public functions that had no docstring
  have one.
- `docs/testing.md` describes the test strategy (kinds of tests, security,
  coverage, mutation, CI), and `docs/test_results.md`, written by
  `docs/build_test_report.py`, lists every test case with its result.
- The README summarises every command line.

**Fixes**

- `catalogue_changes.py --help` crashed on a `%` in a help text, and
  `prototype/score.py` had no `--help`. Every option of every command now has
  a help text, and a test checks it.

## 0.1.1 - 2026-11-17

The catalogue follows MITOS monthly, and the documentation shows the tool.

- The catalogue is refreshed from MITOS on the first of every month
  instead of every Monday. The app says so on its Start page and in the
  Catalogue view.
- The README opens with the logo and links to the app, the slides, the
  method guide and this changelog, and shows the app and PM4Py-UCM in
  eight screenshots.
- Introduction slides in `docs/presentation/` (PDF and PPTX, with speaker
  notes) walk through the default dataset from the question to a
  recommendation.
- `assets/social/` holds the repository's social preview.

## 0.1.0 - 2026-11-16

The first release.

**Generating logs**

- `generate.py` writes a synthetic event log of citizens using Greek public
  services, as CSV or XES, with the ground truth in separate files. Every
  service follows the step tree of a procedure published in MITOS; life events
  start bundles of procedures together with the evidence they require.
- Eight bundled life-event scenarios, defined in `scenarios.json`.
- Instances can belong to two episodes, when a citizen reuses a certificate.
- Remarks on values that are allowed but unrealistic, and checks that stop a
  run that cannot work.
- A default dataset of 2,000 citizens in `out/`, reproduced byte for byte by
  seed 1 on Linux and Windows.

**The catalogue**

- A snapshot of the 4,416 procedures published in MITOS, with the script that
  builds it and one that says what changed between two snapshots.
- A weekly job that refreshes it and records what changed.

**Finding journeys**

- Four methods and a scorer in `prototype/`: a delay mixture per pair of
  services, a Hawkes process, a latent life-event model, and the register's
  evidence links alone or added to any method. `docs/methods.md` describes and
  compares them.

**From journeys to recommendations**

- `bundles.py` turns journeys into a journey-level log that PM4Py-UCM mines,
  and into ranked integration recommendations of six kinds, each with its
  evidence, effort, failed requests and whether MITOS confirms it.

**The app**

- `app.py`, for Streamlit: a Start page, scenario editing that reads as a story
  first, generation with an estimate and progress, inspection, scoring of
  methods, the recommendations, and the catalogue. Light and dark themes.
