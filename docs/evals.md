# How the marketing compliance gate is evaluated

Read this page if you decide what this service is allowed to publish. The metrics, the bars, the
corpora and the narrative floor below are generated from the artifacts that actually gate the
build, so they cannot drift from what runs: `make evals-doc-check` fails the build when this page
and those artifacts disagree.

## How to run it

```sh
make eval              # the deterministic half, offline, no credentials
make eval-narrative    # the judged half, offline by default, no model server
make evals-doc-check   # this page is still true
```

`make gate` runs all three on every change.

## The falsification runs first, and that is the design

`assert_metrics_not_falsely_green` is the FIRST statement of the scored run, before a single
golden score is trusted. Run only in a test suite, a proof says the metric could have gone red on
some machine at some point; run there, it says the metric about to score this corpus can go red,
in this process, against the thresholds this process just loaded from the rubrics. A metric that
became tautological because a scorer was refactored, a threshold moved, or a rule pack was
emptied is caught there and nowhere else.

## What is measured, and against what bar

Every bar below lives in `eval/rubrics/*.yaml` next to the argument for it, and the
runner reads it from there. There is no dict of thresholds in the runner any more: a
metric scored with no reviewed bar fails the build, and so does a bar that names no
metric, which is the direction that rots quietly because it rots toward looking well
governed.

The third column is the denominator rule, and it applies only where a score is a
FRACTION over scored positives: such a threshold `t` tolerates a single miss only over
at least `1/(1-t)` of them. `all or nothing` marks a bar that already asks for no
headroom, so a bigger corpus would not change what it means. Each rubric declares which
it is rather than the rule being guessed from the number.

| Metric | Bar | Denominator | What it measures |
|---|---|---|---|
| `citation_accuracy` | 0.99 | a rate; needs 100 positives | Every finding cites a rule in the active rule set (no foreign citations). |
| `consent_decision_accuracy` | 1 | a rate; needs 0 positives | Fraction of golden consent questions whose outcome matches the independent golden outcome exactly, in both directions, so an always-deny store cannot pass. |
| `consent_fail_closed` | 1 | a rate; needs 0 positives | Fraction of golden consent questions where the store did NOT allow contact that the independent golden state refuses. Any allow-when-denied scores 0.0. |
| `consent_pii_safety` | 1 | a rate; needs 0 positives | Fraction of consent decisions whose newly written durable audit surfaces contain no APAC national identifier. The scorer combines the selected shared pack with a literal planted identifier that remains independent of that pack. |
| `finding_accuracy` | 0.9 | a rate; needs 10 positives | Jaccard overlap of expected vs actual failing rule ids. |
| `review_safety` | 0.99 | a rate; needs 100 positives | Every non-compliant review must require human review (maker-checker). |
| `rule_coverage` | 0.95 | a rate; needs 20 positives | Fraction of applicable rules that produced a finding. |
| `substantiation_accuracy` | 0.99 | a rate; needs 100 positives | Fraction of golden green-claim assets whose substantiation verdict matches the independent golden verdict. |

Scored over 8 golden marketing assets plus the green-claim and consent families.

## What is exercised

Three families, in one run and one table.

- **8 golden marketing assets** in `eval/datasets/golden_reviews.jsonl`,
  carrying 13 expected failing rule ids between them. That is the denominator
  `finding_accuracy` is measured over, not the asset count.
- **8 golden green claims** in `eval/datasets/golden_green_claims.jsonl`,
  scored through the same SubstantiationService the API and the CLI use, over an
  evidence store seeded from the dataset.
- **17 golden consent subjects** in `eval/datasets/golden_consent.jsonl`.
- **2 judged substantiation narratives** in
  `eval/datasets/narrative_golden.jsonl`, each written once per profile with the band it
  is expected to land in. A profile that quietly got BETTER fails too.

## Where the narrative floor comes from

`config/quality-floors.toml` is owned by model risk. A **floor** refuses: below it a
profile must not serve this vertical, which is not the same as serving it worse. A
**target** is full quality. Between the two is DEGRADED, the band a portability claim
describes in adjectives and which nothing measured until there was a floor.

| Vertical | Floor | Target | Why |
|---|---|---|---|
| `mkt6-compliance` | 0.65 | 0.88 | The narrative a marketing reviewer reads before an environmental claim is published. The verdict is code and the narrative is the model; a weak narrative is what a reviewer overrides the verdict on. |

## The one generative output, and why it now has a metric

This service is deliberately almost all deterministic code. The verdict, the coverage and every
rule finding are computed. The substantiation narrative is the one thing a model contributes, and
nothing measured it, which is an odd shape for a service whose whole purpose is to stop an
unsubstantiated claim reaching the public: the single generative output was the one thing the
gate did not score.

A narrative can sit under a correct verdict and still assert an absolute the evidence does not
carry, read as a publishing decision rather than an assessment, or claim completeness over
evidence that has an age. Those are the three things a marketing reviewer overrides a verdict on,
and they are what the judged half grades.

## What is NOT measured here

Naming this is part of the page, because an unmeasured claim that goes unmentioned reads as a
measured one.

- **A real model's words.** The deterministic metrics score a deterministic core, and the judged
  half grades written-down narratives rather than ones a model produced in this run.
- **Production traffic.** Everything here is a golden set. Nothing samples live requests.
