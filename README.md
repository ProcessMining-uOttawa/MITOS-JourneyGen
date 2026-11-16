# MITOS-JourneyGen: synthetic logs of citizens using public services

A generator of event logs in which citizens use several Greek public services,
some of them together because of a life event (a death in the family, a birth,
a job loss) and some of them for unrelated reasons. It exists to test methods
that look for services commonly used together, so that those services can be
integrated or bundled.

**The data is synthetic.** No citizen, case or timestamp is real. What is real
is the structure: every service follows the published step tree of a procedure
in [MITOS](https://mitos.gov.gr), the Greek National Register of Administrative
Procedures, and the evidence one procedure needs from another comes from MITOS
too.

## Quick start

Python 3.9 or later. The generator uses the standard library only; the methods
in `prototype/` need `pip install -r prototype/requirements.txt`.

```bash
python generate.py --citizens 2000 --seed 1 --out out/
```

```bash
python generate.py --describe
```

The first command writes the dataset. The second prints each scenario with the
evidence MITOS attaches to its procedures, and generates nothing.

With the defaults (2,000 citizens, seed 1) the log has 77,551 events in 10,568
service instances of 55 services, used by 1,777 citizens (the others used no
service in the window). 4,725 instances belong to 953 life-event episodes and
5,843 are background. 24 instances serve more than one purpose: 8 are shared
by two episodes and 16 are background uses that an episode later reused.

`--rate-scale 4` multiplies every life-event rate. It is a stress test: the
episodes of one citizen overlap far more often, and about 1 instance in 135 is
shared.

## What is written

| File | Content |
|---|---|
| `events.csv` | The log. One row per step of a service instance. |
| `truth_instances.csv` | Ground truth per instance: which episode it belongs to, or background. |
| `truth_episodes.csv` | Ground truth per episode: scenario, life-event time, span, services in order. |
| `services.csv` | The services that occur, with their MITOS ids, Greek titles and links. |
| `manifest.json` | Arguments, seed, counts and the date of the MITOS snapshot. |

The default dataset is included in `out/`, so it can be used without running
anything.

`events.csv` carries no hint of the bundles. Keep the `truth_*` files away from
the method under test and use them only to score it.

### Columns of `events.csv`

| Column | Meaning |
|---|---|
| `case_id` | One service instance (one citizen using one service once). |
| `citizen_id` | The citizen. The key that links instances across services. |
| `service_id`, `service` | MITOS procedure id and title. |
| `activity` | The step, as named in MITOS. |
| `start_timestamp`, `time:timestamp` | Start and completion of the step. |
| `role`, `method` | Who performs the step and how (manual action, software, signature ...). |
| `channel` | `digital` or `counter`. |
| `organisation` | The body that owns the procedure. |
| `citizen_region`, `citizen_age_group` | Citizen attributes. |

`--lang el` writes Greek labels; `--lang both` adds `service_el` and
`activity_el` to the English ones.

### Reading the ground truth

In `truth_instances.csv`, `origin` is `scenario` or `background`. A scenario
instance has an `episode_id`, and a `role`: `goal` for a procedure the scenario
lists, `evidence` for one obtained because another procedure requires it (then
`supports_case` names the instance it was obtained for). `outcome` is
`completed`, `unsuccessful` (the instance took a failure branch) or
`abandoned` (it stops part-way). `attempt` counts retries.

An instance can belong to more than one episode. `shared_with` lists the other
episodes that reused its result; a background instance with a `shared_with`
belongs to that episode too. So the true grouping is not a partition.

## How the log is made

1. **Citizens.** Each has an age group, a region and a propensity to use the
   digital channel.
2. **Life events.** For each scenario the citizen draws a number of life events
   over the window, at a rate that depends on the age group.
3. **Bundles.** A life event starts the scenario's procedures, each with its own
   probability and delay. A procedure listed `after` another waits for it to
   succeed, and is dropped if it never does.
4. **Evidence.** Before a procedure starts, the citizen obtains the procedures
   that MITOS names as its required evidence. A citizen who already holds a
   valid result (for the validity MITOS gives, else 60 days) sometimes reuses
   it and sometimes requests it again, which is the duplication a better
   integration would remove. The result reused may come from the same episode,
   from another episode or from an unrelated use; that instance then belongs
   to both. Solemn declarations, authorisations and fees are never reused.
5. **Instances.** An instance walks the procedure's step tree. Where the tree
   branches, a branch whose name signals failure (rejection, incomplete
   documents, failed authentication) is taken rarely. Step durations are drawn
   between the minimum and maximum that MITOS gives. Counter steps happen in
   office hours. An unsuccessful or abandoned instance is usually retried.
6. **Background.** Each citizen also uses popular services on their own, at
   random times. These are often the same services that scenarios pull in as
   evidence (solemn declaration, tax clearance, family-status certificate), so
   they co-occur with everything by chance.

Two properties are deliberate, because they are what makes the problem hard:

- **No single time window fits.** A passport episode spans about 9 days at the
  median, a death in the family about 90, and a job loss can reach two years
  because the long-term benefit starts after the regular one ends.
- **Episodes overlap** with each other and with background use, for the same
  citizen.

## Scenarios

`scenarios.json` defines eight: death of a close relative, birth of a child,
getting married, retiring, losing a job, moving to another municipality,
starting university, getting a passport. Each names real MITOS procedures by id.

**The procedures, their steps, their durations and their evidence links are
MITOS's. Everything else is an assumption of this dataset**: which procedures
form a scenario, how often a life event happens, the probability that a citizen
starts each procedure, the delays, the reuse and retry behaviour, the
background rates. None of it is measured. Change it in `scenarios.json`; the
file explains each key.

To add a scenario, look up the procedure ids on mitos.gov.gr, add a block, and
run `--describe` to see the evidence that comes with it. MITOS sometimes lists
evidence that applies only in special cases (a death certificate for a birth
allowance); set its chance under `evidence` to lower or remove it.

## The catalogue

`catalogue.json.gz` is a snapshot of all 4,416 published MITOS procedures,
reduced to what the generator needs: titles, owner, life events, the two step
trees (counter and digital) and the evidence links. Any of them can be used in
a scenario. To refresh it:

```bash
python build_catalogue.py --cache raw/
```

This downloads about 5,000 small JSON documents from the public API and takes
a quarter of an hour.

English labels come from MITOS where it publishes them (594 procedures, and
any Greek step name that one of those translates). `translations_en.json`
holds unofficial English renderings, written for this dataset, of the
remaining titles and steps used by the bundled scenarios. A label with no
English stays in Greek; `--describe` lists those.

## Limitations

- Alternatives inside one instance are chosen independently, so an instance can
  be filed at a Citizen Service Centre and delivered by the municipality.
- A branch is treated as a failure from its Greek name, by a keyword list in
  `generate.py`. It is a heuristic.
- Many MITOS steps are back-office steps. The log therefore mixes what a
  citizen does with what a clerk does, as an agency's own system would.
- Durations are uniform between MITOS's minimum and maximum. Queues, seasons
  and workload are not modelled.
- Life events start inside the window, but their instances run to completion,
  so the log extends past the window's end.
- One citizen acts alone: a marriage or a birth involves one applicant.

## A baseline method

`prototype/` holds first methods for finding journeys, and their scorer. They
need numpy, pandas and scipy, and write their results next to the log.

```bash
python prototype/discover.py out/
```

```bash
python prototype/score.py out/
```

`discover.py` reads only `events.csv`. For each ordered pair of services it
fits a mixture to the delays between them for the same citizen: related uses
with a log-normal delay, and chance uses whose delay is measured from the same
two services used by different citizens. It writes the learnt window of every
related pair (`pairs.csv`), the likely links between instances (`links.csv`)
and the journeys. `score.py` compares the journeys with the ground truth and
with fixed-gap baselines.

`prototype/hawkes.py` (also needs scipy) is a second method, run and scored the
same way. It fits a multivariate Hawkes process: an instance is either
spontaneous or triggered by one earlier instance of the same citizen, with a
log-normal delay per service pair, a background rate that follows the calendar,
and an activity level per citizen.

`prototype/latent.py` is a third method: a latent life-event model. A hidden
life event of some kind shows up as its first instance and brings further
instances, each service at a rate and after a delay that depend on the kind.
Instances attach to the event, not to each other, and the kinds are learnt.
It is the only one of the three that answers the original question directly:
`latent_kinds.csv` lists each discovered bundle, with the services it brings
and their windows. The number of kinds is an upper bound (`--kinds`, 12 by
default); unused kinds stay empty.

None of the three uses the register: they read `events.csv` and nothing else.

Each method writes its journeys twice. As a partition, an instance is in one
journey: connected components of the links for the mixture, the tree of most
likely parents for Hawkes and the latent model. Overlapping, an instance can be
in several: groups of links that share triangles for the mixture, so that an
instance which only bridges two groups joins both; every likely parent for the
other two.

| Default dataset | Precision | Recall | F1 | Episodes whole | Journeys clean |
|---|---|---|---|---|---|
| Mixture, overlapping | 82% | 95% | 88% | 84% | 79% |
| Mixture, partition | 75% | 98% | 85% | 92% | 79% |
| Hawkes, overlapping | 94% | 86% | 90% | 82% | 92% |
| Hawkes, partition | 94% | 85% | 89% | 82% | 93% |
| Latent, overlapping | 92% | 87% | 89% | 80% | 87% |
| Latent, partition | 92% | 85% | 88% | 80% | 88% |
| Best fixed gap (30 days) | 74% | 68% | 71% | 73% | 69% |

On a 10,000-citizen log the latent model's 12 kinds include one for each of the
eight scenarios, and 90% of its journeys fall in the kind of their scenario.
On the default log, with 52 university episodes, that scenario gets no kind of
its own.

Precision and recall are over pairs of instances: "share a journey" against
"share a true episode". These are prototypes, with hand-set thresholds and no
control for the number of service pairs tested.

**No log-only method finds the truly shared instances.** With `--rate-scale 4`,
918 instances belong to two episodes; the overlapping mixture puts 6% of them
in two journeys, Hawkes 2% and the latent model 1%. Overlap helps by not
merging two episodes through a bridge, not by recognising a reused certificate.
A reuse leaves no event: the second episode simply lacks a request.

### With the register

`prototype/register.py` brings in what MITOS states: which procedures a
procedure requires as evidence, and how long a result stays valid (90 days
assumed where MITOS gives none). An instance of B *uses* the citizen's latest
valid instance of A when the register says B requires A.

```bash
python prototype/register.py out/
```

It is applied in two ways. **Register only** builds journeys from those links
and nothing learnt. **Register added** takes the journeys of a log-only method
and, where a journey holds a B but none of an A it requires, adds the valid
earlier A the citizen holds; that A stays in its own journey too.

| Default dataset | Precision | Recall | F1 | Episodes whole | Shared found |
|---|---|---|---|---|---|
| Hawkes | 94% | 86% | 90% | 82% | 0% |
| Hawkes + register | 92% | 90% | 91% | 88% | 91% |
| Mixture + register | 81% | 98% | 89% | 91% | 78% |
| Latent + register | 90% | 90% | 90% | 85% | 83% |
| Register only | 95% | 28% | 43% | 38% | 44% |

The register alone is precise and badly incomplete, worse than a fixed gap:
it knows that a pension needs a certificate, not that a move brings both a
residence certificate and a change of address. Added to a learnt method it
does the one thing learning cannot, and little else.

Read these numbers with care. The generator built its bundles from the same
evidence links, so the register fits this data better than it would fit real
logs, where not every requirement is exercised and not every dependency is
written down. The default dataset has only 23 shared instances; with
`--rate-scale 4` (918 of them) Hawkes + register finds 73%.

## Open issues

- **The subject of a journey.** Every episode has one applicant. In reality
  several people act after one life event: relatives after a death, two parents
  after a birth. Their instances share the person the procedures are *about*
  (the deceased, the child), not the applicant. A method that links instances
  by `citizen_id` alone would split such bundles. To study this the generator
  needs households, episodes spread over several applicants, and a subject
  identifier on each instance (present in some services, missing in others, as
  it would be in real logs). Deferred on purpose (2026-11-16) to keep the first
  version simple.

## Licence and attribution

Procedure data: MITOS, National Register of Administrative Procedures, Hellenic
Republic, licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
`catalogue.json.gz` is adapted from it and is under the same licence, as are
the logs generated from it (including `out/`), since they reproduce MITOS's
titles and step names. `translations_en.json` adds unofficial English
renderings of MITOS text and is under CC BY-SA 4.0 as well.

The code (`*.py`, `scenarios.json`) is under the MIT licence; see `LICENSE`.
