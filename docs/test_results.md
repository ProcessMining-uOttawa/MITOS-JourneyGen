# Test results

MITOS-JourneyGen 0.1.1, run on 2026-11-19 with Python 3.13.2 on Windows. Written by `docs/build_test_report.py`; the strategy behind these tests is in [testing.md](testing.md).

| | |
|---|---|
| Test cases | **104 of 104 passed** in 212 s |
| Statement coverage | **92%** (2,553 of 2,769 statements) |
| Security scan (bandit) | **0 issues** in 4,659 lines; 7 reviewed exceptions marked `# nosec` |
| Mutation check | **22 of 22 planted bugs caught** without the byte-for-byte test (2026-11-19) |

## Coverage by module

| Module | Statements | Covered |
|---|---:|---:|
| `app.py` | 955 | 82% |
| `build_catalogue.py` | 150 | 93% |
| `bundles.py` | 364 | 100% |
| `catalogue_changes.py` | 136 | 97% |
| `generate.py` | 542 | 95% |
| `prototype/discover.py` | 161 | 99% |
| `prototype/hawkes.py` | 115 | 99% |
| `prototype/latent.py` | 153 | 99% |
| `prototype/register.py` | 116 | 100% |
| `prototype/score.py` | 77 | 100% |

## Mutation check

Each row is a bug planted in the code, and a test that fails because of it (with or without the byte-for-byte test of out/).

| # | File | Planted bug | Result | Caught by |
|---:|---|---|---|---|
| 1 | `generate.py` | XES attributes no longer escape & | killed | test_security.py::test_xml_attributes_are_escaped_and_cleaned |
| 2 | `generate.py` | control characters reach the XES | killed | test_security.py::test_xml_attributes_are_escaped_and_cleaned |
| 3 | `generate.py` | range check forgets the upper bound | killed | test_security.py::test_values_that_would_run_away_stop_a_run[<lambda>-window] |
| 4 | `generate.py` | a value of the wrong type is not reported | killed | test_security.py::test_values_that_would_run_away_stop_a_run[<lambda>-retry_probability] |
| 5 | `generate.py` | waits for a missing procedure accepted | killed | test_review.py::test_a_procedure_waiting_for_one_the_scenario_does_not_list_stops_a_run |
| 6 | `generate.py` | expired certificates reused | killed | test_generator_rules.py::test_a_certificate_is_reused_only_while_it_is_valid |
| 7 | `generate.py` | reuse chance inverted | killed | test_generator_rules.py::test_reuse_follows_its_probability |
| 8 | `generate.py` | one retry too many | killed | test_generator_rules.py::test_no_instance_is_attempted_more_often_than_allowed |
| 9 | `generate.py` | durations exceed the MITOS maximum | killed | test_generator_rules.py::test_every_step_lasts_between_the_mitos_minimum_and_maximum |
| 10 | `generate.py` | counter open on Saturdays | killed | test_generator_rules.py::test_counter_steps_happen_in_office_hours |
| 11 | `generate.py` | failure branches as likely as others | killed | test_generator_rules.py::test_failure_branches_follow_their_weight |
| 12 | `bundles.py` | journeys of exactly min_size dropped | killed | test_bundles.py::test_a_journey_becomes_a_case_and_a_service_an_activity |
| 13 | `bundles.py` | candidate pairs not filtered | killed | test_bundles.py::test_rare_pairs_are_not_candidates |
| 14 | `bundles.py` | low-lift pairs recommended | killed | test_bundles.py::test_a_pair_explained_by_a_popular_service_is_not_recommended |
| 15 | `build_catalogue.py` | downloader fetches from anywhere | killed | test_security.py::test_the_downloader_only_fetches_from_the_mitos_api |
| 16 | `build_catalogue.py` | any procedure id names a file | killed | test_security.py::test_a_procedure_id_cannot_name_a_file_outside_the_cache |
| 17 | `build_catalogue.py` | a procedure requires itself | killed | test_command_line.py::test_build_catalogue_offline_from_a_cache |
| 18 | `build_catalogue.py` | an alternative overrides a mandatory link | killed | test_command_line.py::test_build_catalogue_offline_from_a_cache |
| 19 | `catalogue_changes.py` | a failed download is accepted as a refresh | killed | test_command_line.py::test_catalogue_changes_refuses_a_download_that_lost_procedures |
| 20 | `catalogue_changes.py` | a change of validity goes unreported | killed | test_command_line.py::test_each_kind_of_change_in_a_procedure_is_named[change8-validity: P1Y to none] |
| 21 | `prototype/score.py` | F1 computed wrongly | killed | test_score.py::test_pair_scores_on_a_case_worked_out_by_hand |
| 22 | `prototype/score.py` | precision and recall swapped | killed | test_score.py::test_pair_scores_on_a_case_worked_out_by_hand |

## Test cases

### `tests/test_app.py` (11 of 11 passed)

Drive the app without a browser: every view renders, a log is generated in both formats, and the views that need a log work on it.

| Test case | Result | Seconds |
|---|---|---:|
| Every view renders before a log exists | passed | 14.6 |
| The start page leads to generate | passed | 1.7 |
| A scenario is a story first and an editor on request | passed | 1.4 |
| Picking a life event once is enough | passed | 2.4 |
| The method guide opens as a page of its own | passed | 0.4 |
| Unrealistic values are pointed out where they are set | passed | 2.4 |
| Generate inspect and score (CSV-events.csv) | passed | 17.1 |
| Generate inspect and score (XES, gzipped-events.xes.gz) | passed | 17.9 |
| The latent models kinds reach the bundles view | passed | 40.5 |
| The same seed gives the same log as the command line | passed | 5.2 |
| A scenario naming a withdrawn procedure is reported | passed | 0.8 |

### `tests/test_assets.py` (3 of 3 passed)

The logo files are what assets/make_logo.py writes, and the app uses them.

| Test case | Result | Seconds |
|---|---|---:|
| The svg files match their script | passed | 0.1 |
| The browser icon is a square png | passed | 0.0 |
| The app shows the logo and offers both themes | passed | 0.0 |

### `tests/test_bundles.py` (16 of 16 passed)

From journeys to a service-level log and integration candidates.

| Test case | Result | Seconds |
|---|---|---:|
| A journey becomes a case and a service an activity | passed | 0.0 |
| Candidates say what follows what and how long citizens wait | passed | 0.1 |
| Sequences and repeated requests | passed | 0.1 |
| The xes holds a trace per journey with both timestamps | passed | 0.1 |
| True episodes put a shared instance in both journeys | passed | 0.0 |
| Each kind of recommendation is recognised | passed | 0.4 |
| Evidence effort and the register are reported | passed | 0.7 |
| The kinds and the effort are numbered in order | passed | 0.0 |
| The score rewards reach and certainty and charges effort | passed | 0.0 |
| The top list is ranked and varied | passed | 0.4 |
| The ranking travels with the journey log | passed | 0.7 |
| A pair the entry point covers is not said twice | passed | 0.3 |
| Rare and weakly tied pairs are left out | passed | 0.9 |
| Failed requests are counted for each recommendation | passed | 0.9 |
| Rare pairs are not candidates | passed | 0.3 |
| A pair explained by a popular service is not recommended | passed | 0.5 |

### `tests/test_catalogue.py` (4 of 4 passed)

The catalogue download and comparison, without the network.

| Test case | Result | Seconds |
|---|---|---:|
| Download reports progress and resumes | passed | 0.0 |
| A document that cannot be fetched is reported | passed | 0.0 |
| Progress can stop the download | passed | 0.0 |
| Comparison finds what changed and which scenarios it touches | passed | 26.2 |

### `tests/test_command_line.py` (31 of 31 passed)

The command lines, end to end on a small dataset, and the catalogue build from a made-up MITOS cache.

| Test case | Result | Seconds |
|---|---|---:|
| Generate writes the log the truth and a manifest | passed | 10.3 |
| Generate describe lists scenarios and remarks | passed | 0.4 |
| Generate refuses a scenario it cannot run | passed | 0.3 |
| Generate says when values look unrealistic | passed | 2.4 |
| Bundles writes the answer files | passed | 3.5 |
| Prototype discovery and scoring | passed | 5.6 |
| Build catalogue offline from a cache | passed | 0.0 |
| Build catalogue stops when documents are missing | passed | 0.0 |
| Build catalogue downloads then builds | passed | 0.0 |
| Catalogue changes command line | passed | 0.0 |
| Catalogue changes refuses a download that lost procedures | passed | 0.0 |
| The other prototype methods write journeys | passed | 4.0 |
| Each kind of change in a procedure is named (change0-title) | passed | 0.0 |
| Each kind of change in a procedure is named (change1-steps: 2 to 1) | passed | 0.0 |
| Each kind of change in a procedure is named (change2-steps: branching) | passed | 0.0 |
| Each kind of change in a procedure is named (change3-steps: durations) | passed | 0.0 |
| Each kind of change in a procedure is named (change4-steps: names or roles) | passed | 0.0 |
| Each kind of change in a procedure is named (change5-evidence: +6) | passed | 0.0 |
| Each kind of change in a procedure is named (change6-evidence: -5) | passed | 0.0 |
| Each kind of change in a procedure is named (change7-evidence: alternative flag) | passed | 0.0 |
| Each kind of change in a procedure is named (change8-validity: P1Y to none) | passed | 0.0 |
| Each kind of change in a procedure is named (change9-owner) | passed | 0.0 |
| Every command line explains every option (generate.py) | passed | 0.2 |
| Every command line explains every option (bundles.py) | passed | 1.7 |
| Every command line explains every option (build_catalogue.py) | passed | 0.3 |
| Every command line explains every option (catalogue_changes.py) | passed | 0.2 |
| Every command line explains every option (prototype/discover.py) | passed | 1.5 |
| Every command line explains every option (prototype/hawkes.py) | passed | 1.9 |
| Every command line explains every option (prototype/latent.py) | passed | 1.5 |
| Every command line explains every option (prototype/register.py) | passed | 1.5 |
| Every command line explains every option (prototype/score.py) | passed | 1.5 |

### `tests/test_default_dataset.py` (1 of 1 passed)

The dataset in ``out/`` is what the command line gives for seed 1, on every platform, as long as the catalogue is the one it was made with.

| Test case | Result | Seconds |
|---|---|---:|
| Out is reproduced by seed 1 | passed | 7.7 |

### `tests/test_generator_rules.py` (6 of 6 passed)

The rules a generated log promises, checked one by one on generated data.

| Test case | Result | Seconds |
|---|---|---:|
| Every step lasts between the mitos minimum and maximum | passed | 2.2 |
| Counter steps happen in office hours | passed | 0.0 |
| No instance is attempted more often than allowed | passed | 0.5 |
| Failure branches follow their weight | passed | 2.8 |
| Reuse follows its probability | passed | 6.7 |
| A certificate is reused only while it is valid | passed | 0.0 |

### `tests/test_release.py` (4 of 4 passed)

What a release and a hosted deployment rely on.

| Test case | Result | Seconds |
|---|---|---:|
| The changelog opens with the version the code states | passed | 0.0 |
| The committed config fixes no port | passed | 0.0 |
| A release moves the branch the deployed app watches | passed | 0.0 |
| What streamlit cloud installs is listed | passed | 0.0 |

### `tests/test_review.py` (8 of 8 passed)

The remarks on unrealistic values, and the checks that stop a run.

| Test case | Result | Seconds |
|---|---|---:|
| The bundled scenarios draw no remark | passed | 0.0 |
| A procedure waiting for a later one is caught | passed | 0.0 |
| Rates that repeat a life event are caught | passed | 0.0 |
| Thin scenarios and long waits are caught | passed | 0.0 |
| Behaviour and background extremes are caught | passed | 0.0 |
| A small population is noted per scenario | passed | 0.0 |
| Check stops what cannot run | passed | 0.0 |
| A procedure waiting for one the scenario does not list stops a run | passed | 0.0 |

### `tests/test_score.py` (4 of 4 passed)

The scores of a journey-discovery method, worked out by hand.

| Test case | Result | Seconds |
|---|---|---:|
| Pair scores on a case worked out by hand | passed | 0.1 |
| A shared instance is found only when it is in two journeys | passed | 0.1 |
| A perfect and an empty answer | passed | 0.1 |
| A fixed gap starts a new journey after a quiet spell | passed | 0.0 |

### `tests/test_security.py` (16 of 16 passed)

What the security assessment (bandit) led to: safe XES attributes, a downloader that only talks to MITOS, and limits a loaded project cannot exceed.

| Test case | Result | Seconds |
|---|---|---:|
| Xml attributes are escaped and cleaned | passed | 0.0 |
| An xes log with awkward labels is well formed | passed | 3.4 |
| The journey log xes is well formed | passed | 0.0 |
| The downloader only fetches from the mitos api | passed | 0.0 |
| A procedure id cannot name a file outside the cache | passed | 0.0 |
| A download refuses a list with a strange id | passed | 0.0 |
| The bundled scenarios are within the limits | passed | 0.3 |
| Values that would run away stop a run (<lambda>-window) | passed | 0.3 |
| Values that would run away stop a run (<lambda>-rate for 30-44) | passed | 0.3 |
| Values that would run away stop a run (<lambda>-rate) | passed | 0.3 |
| Values that would run away stop a run (<lambda>-chance) | passed | 0.3 |
| Values that would run away stop a run (<lambda>-delay) | passed | 0.3 |
| Values that would run away stop a run (<lambda>-background) | passed | 0.3 |
| Values that would run away stop a run (<lambda>-max_attempts) | passed | 0.3 |
| Values that would run away stop a run (<lambda>-retry_probability) | passed | 0.2 |
| Values that would run away stop a run (<lambda>-evidence_depth) | passed | 0.3 |
