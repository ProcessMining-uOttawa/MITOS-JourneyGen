# Documentation

Everything here complements the [main README](../README.md), which explains
what MITOS-JourneyGen does and how to run it.

## To read

| Page | What it is for |
|---|---|
| [Introduction slides](presentation/MITOS-JourneyGen-introduction.pdf) ([PPTX](presentation/MITOS-JourneyGen-introduction.pptx)) | A walk through the default dataset, from the question to a recommendation, including the journey log opened in PM4Py-UCM. Start here if you are new. The PPTX has speaker notes. |
| [methods.md](methods.md) | The journey-discovery methods in `prototype/`: what each assumes, how it decides, and how they compare. |
| [API documentation](https://processmining-uottawa.github.io/MITOS-JourneyGen/) | Every module, class and function, from the docstrings. Published for each release; build it locally with `build_api_docs.py`. |
| [testing.md](testing.md) | How the tool is tested: what the tests guarantee, the kinds of tests, the security scan, coverage and the mutation check. |
| [test_results.md](test_results.md) | The latest results: every test case, coverage by module, the security scan and the planted bugs caught. |

## Files that support those pages

| File | What it is |
|---|---|
| `images/` | Screenshots of the app and of PM4Py-UCM shown in the main README. |
| `presentation/` | The introduction slides, as PDF (readable on GitHub) and PPTX. They are copies: edit the source deck, then replace both files. |
| `build_api_docs.py` | Builds the API documentation into `api/` (not committed) with pdoc: `pip install pdoc`, then `python docs/build_api_docs.py`. |
| `build_test_report.py` | Runs the tests with coverage and bandit and rewrites `test_results.md`: `python docs/build_test_report.py`. |
| `mutation_results.json` | The last results of the mutation check, read by `build_test_report.py`. Refresh it with `python tests/mutation_check.py --without-golden --json docs/mutation_results.json` (about fifteen minutes). |

## Keeping them current

- After a change to the code or the tests, run `build_test_report.py` and
  commit `test_results.md` with the change.
- After a change that a planted bug in `tests/mutation_check.py` could hide,
  refresh `mutation_results.json` first, then the report.
- The API documentation updates itself at each release (the *API
  documentation* workflow); nothing to commit.
- Screenshots and slides are made by hand and show version 0.1.x; replace them
  when the app changes visibly.
