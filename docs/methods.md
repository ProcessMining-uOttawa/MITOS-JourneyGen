# Methods for finding journeys

A **journey** is a group of service instances of one citizen that belong
together. A method reads a log and proposes journeys; scoring compares them
with the true episodes. This guide describes the methods that come with
MITOS-JourneyGen: what each assumes, how it decides, what it is good and bad
at, and how they compare.

They are prototypes. Their thresholds were set by hand, and none controls for
the number of service pairs it tests. They exist to show what is feasible and
to give a new method something to be measured against.

## At a glance

| Method | Reads | Learns | An instance can be in two journeys | Finds shared instances | Says what the bundles are |
|---|---|---|---|---|---|
| Fixed gap | log | nothing | no | no | no |
| Pair mixture | log | a time window per pair of services | yes | no | pairs only |
| Hawkes process | log | who triggers whom, and when | yes | no | pairs only |
| Latent life events | log | the kinds of life event | yes | no | yes |
| Register only | log and MITOS | nothing | yes | partly | no |
| Any method + register | log and MITOS | as the method | yes | yes | as the method |

"Shared instances" are instances that truly belong to two episodes, because a
citizen reused a certificate they already held.

## Scores on the default dataset

2,000 citizens, seed 1, catalogue of 2026-11-16: 10,568 service instances, 953
episodes. Overlapping variants where a method has one.

| Method | Precision | Recall | F1 | Episodes whole | Journeys clean | Shared found | Time |
|---|---|---|---|---|---|---|---|
| Fixed gap, 7 days | 83% | 40% | 54% | 51% | 87% | 0% | under 1 s |
| Fixed gap, 30 days | 74% | 68% | 71% | 73% | 69% | 0% | under 1 s |
| Fixed gap, 90 days | 56% | 93% | 70% | 91% | 47% | 0% | under 1 s |
| Pair mixture | 82% | 95% | 88% | 84% | 79% | 0% | 11 s |
| Hawkes process | 94% | 86% | 90% | 82% | 92% | 0% | 4 s |
| Latent life events | 92% | 87% | 89% | 80% | 87% | 0% | 10 s |
| Register only | 95% | 28% | 43% | 38% | 96% | 44% | 1 s |
| Pair mixture + register | 81% | 98% | 89% | 91% | 76% | 78% | 12 s |
| Hawkes process + register | 92% | 90% | 91% | 88% | 88% | 91% | 5 s |
| Latent life events + register | 90% | 90% | 90% | 85% | 83% | 83% | 11 s |

How to read the columns:

- **Precision**: of the pairs of instances a method put in the same journey,
  the share that truly share an episode. Low means it glues unrelated things
  together.
- **Recall**: of the pairs that truly share an episode, the share the method
  put together. Low means it splits episodes.
- **F1**: one number combining the two.
- **Episodes whole**: true episodes that fit entirely inside one journey.
- **Journeys clean**: journeys that hold nothing from outside one episode.
- **Shared found**: of the truly shared instances, those placed in two or more
  journeys. This dataset has only 23 of them, so that column moves a lot by
  chance; on a log with 918 of them, Hawkes + register found 73%.

Two cautions. The numbers are for one generated log, and the generator's
delays are smoother than real ones. And the generator builds its bundles from
MITOS's evidence links, so the register rows are flattered: on real logs not
every requirement is exercised and not every dependency is written down.

## The methods that read only the log

### Fixed gap

**Idea.** A citizen's instances form one journey until the citizen has been
inactive for longer than a chosen number of days.

**Assumes.** That one time window suits every bundle.

**Good at.** Being simple, instant and explainable. It is the baseline.

**Bad at.** Everything else. A short gap cuts long bundles apart (a death in
the family spans months); a long gap glues unrelated use onto short ones (a
passport takes days). No single value works, which is the reason the other
methods exist.

### Pair mixture (`prototype/discover.py`)

**Idea.** For every ordered pair of services (A, B), look at the delays from
an A to a later B of the same citizen. Those delays are a blend of two
populations: related uses, whose delay is concentrated, and chance uses, whose
delay is spread out. Fit that blend.

**How.** The related part is a log-normal. The chance part is *measured*: it is
the delay between the same two services used by *different* citizens, so it
follows whatever calendar the log has. A pair is kept when the blend explains
the delays clearly better than chance alone (a BIC gain) and has enough
support. Each pair of instances then gets a probability of being linked.

**Journeys.** As a partition, the connected groups of likely links.
Overlapping, groups of links that share triangles, so that an instance which
only bridges two dense groups belongs to both instead of merging them.

**Output.** `pairs.csv`: for every related pair, the share that is related,
the median delay and a 90% window. This is the "time window per pair" in
readable form.

**Good at.** Keeping episodes whole (highest recall). It links anything
plausibly related, and it needs little data per pair.

**Bad at.** Precision. One bridging instance can chain two episodes together,
and popular services used shortly before a bundle look exactly like its
evidence.

### Hawkes process (`prototype/hawkes.py`)

**Idea.** Each instance is either spontaneous or triggered by one earlier
instance of the same citizen. An instance of A triggers, on average, a learnt
number of instances of B, after a learnt delay.

**How.** A multivariate Hawkes process fitted by expectation-maximisation, with
three choices that matter here: a log-normal delay per pair rather than the
usual exponential decay (an inheritance tax declaration peaks months after its
trigger, not at zero); a spontaneous rate that follows the calendar; and an
activity level per citizen, so that heavy users of popular services are not
mistaken for bundles.

**Journeys.** As a partition, the tree formed by each instance's most likely
parent. Overlapping, an instance also keeps any other parent that is likely
enough, so it and what it triggers can sit in two journeys.

**Output.** `hawkes_pairs.csv`: for every triggering pair, instances triggered
per A, the median delay and a window.

**Good at.** Clean journeys (highest precision). Every instance has at most
one parent and parents compete to explain it, so it rarely merges episodes. It
also degrades least when citizens' episodes overlap.

**Bad at.** Siblings. A life event is a hidden common cause, not one service
triggering another. Two procedures that follow the same event in no fixed
order are tied by nothing in this model, so loosely ordered bundles are split.
Its numbers should not be read causally either: "a death declaration triggers
1.4 death registration acts" is bookkeeping for retries and repeated requests.

### Latent life events (`prototype/latent.py`)

**Idea.** Model the hidden cause itself. A life event of some kind shows up as
its first instance and then brings further instances: each service at a rate
and after a delay that depend on the kind. The kinds are not given; they are
learnt.

**How.** A mean-field expectation-maximisation. For each instance it keeps the
probability of being the first of a life event and, if so, of each kind. Two
things make it work: an explicit "no life event" kind, and judging each link
with the kind inferred from everything *else* the first instance brought.
Without the second, any chance follower confirms the life event it was used
to posit, and the model invents kinds such as "a tax certificate, then a
family-status certificate within 20 to 300 days".

**Journeys.** As for Hawkes, with instances attached to the life event rather
than to each other.

**Output.** `latent_kinds.csv`: each discovered kind, the services it brings,
how many per event, and when. This is the only output that states the bundles
themselves, which is the original question.

**Good at.** Naming bundles. On a 10,000-citizen log its 12 kinds included one
for each of the eight true scenarios, with 90% of journeys in the right kind.

**Bad at.** Small logs: with 52 episodes of a scenario it finds no kind for it.
Loosely tied bundles, where the first instance predicts the rest poorly. It is
the slowest, and the number of kinds must be chosen: 8, 12 and 16 give nearly
the same journeys but different kinds.

## The methods that use the register

MITOS states which procedures a procedure requires as evidence, and for some
how long a result stays valid. That gives one rule, with nothing learnt:

> An instance of B *uses* the citizen's latest instance of A when the register
> says B requires A, A ended before B started, and A was still valid then.

Where MITOS gives no validity, 90 days are assumed; 30 to 180 days change the
results little.

### Register only (`prototype/register.py`)

**Journeys.** As a partition, the connected groups of "uses" links.
Overlapping, each instance that nothing uses, together with everything it uses
directly or indirectly.

**Good at.** Precision: a link the register confirms is nearly always right.
It needs no data volume at all.

**Bad at.** Completeness. The register knows that a pension needs a
family-status certificate. It does not know that a move brings both a
residence certificate and a change of address, because neither requires the
other. It finds under a third of what belongs together, which is worse than a
fixed gap.

### Any method + register

**Idea.** Take the journeys of any method. Where a journey holds a B but none
of an A that B requires, and the citizen holds a valid earlier A, add that A
to the journey. The A stays in its own journey as well.

**Why it matters.** This is the one thing no log-only method can do. When a
citizen reuses a certificate, the second episode contains no request for it:
there is no event to learn from, only an absence. The register turns that
absence into a link. It lifts shared instances found from about 0% to between
70% and 90%.

**Cost.** A little precision: it sometimes attaches a certificate the citizen
held but did not in fact reuse. Overall scores move by about one point.

## Which to use

- To **group instances** as cleanly as possible: Hawkes.
- To **miss as little as possible**: pair mixture.
- To **learn what the bundles are**: latent life events, on a log large enough.
- To **find reused certificates**: add the register to any of them.
- As a **baseline** for a new method: a fixed gap of 30 days, then Hawkes.

Learning finds the bundles; the register explains the reuse. They complement
each other rather than compete.
