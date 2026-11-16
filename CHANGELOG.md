# Changelog

What changed in each release of MITOS-JourneyGen, newest first. Changes to the
MITOS catalogue itself are in [CATALOGUE_CHANGES.md](CATALOGUE_CHANGES.md) once
a weekly refresh has found any.

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
