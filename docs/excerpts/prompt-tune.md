# The prompt-tune loop: a cloud model improves a local model's prompt while seeing counts only

> Excerpt of the `prompt-tune` run records of 2026-11-12, not of the lab notebook. Aggregates only:
> the run records hold the cloud's prompts and count reports (the gateway passes no task data), and
> only these score numbers are reproduced here. The loop's code and its synthetic-data tests are in
> github.com/Sjbrimley26/prompt-tune.

**Why.** The payee categorizer (a small local model labeling private bank-transaction categories) had a
fixed prompt written by hand. The question: can a bigger cloud model write a better prompt, given only
what a privacy gateway passes on — counts per category per candidate prompt, never a single payee string,
amount or label? The cloud sees the counts, its own previous prompts and its own scores, and writes the
next candidate. The local model scores every candidate; a candidate is kept only if it beats the best so
far.

**Harness.** Local seat = `gemma4:e4b` (vision-text preset, reasoning off, ctx 16384) on the llama.cpp
router. The task is the payee categorizer's own request code on the tune set: 130 labeled keys for
tuning; the last 91 keys (40%) are held out and scored once, only at the end. Score per key set =
right − 2×wrong (unsure = 0), so a wrong label is worse than a blank. A paired sign test across holdout
keys guards against calling noise a gain. The approval to run was a reviewed hash of the gateway
(`approved-gateway.sha256`); anything the cloud request file contained besides category/field names
would have aborted it.

| run | writer (cloud) | rounds | tune score | holdout baseline → best | right | wrong | unsure | failures | paired (base/best) | sign test p | writer tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 20261112-222017 | glm-5.3-flash | 6 | 34 → 44 | 11 → **26** | 51 → 50 | 20 → **12** | 20 → 29 | 0 → 3 | 4 / 3 | 1.00 | 38,635 / 148,100 |
| 20261112-231147 | deepseek-v4.1-flash | 6 | 34 → 40 | 11 → **20** | 51 → 48 | 20 → **14** | 20 → 29 | 0 → **33** | 7 / 4 | 0.55 | 30,893 / 27,836 |

(A third run, glm-5.3 itself at the then 8,000-token completion cap, produced no usable reply — both
calls hit the cap on reasoning tokens — and the loop stopped before round 1. The cap was raised to
32,000 afterwards and unusable replies are now logged; that arm was not rerun for these numbers.)

## Verdict

- **Neither gain is established.** Both tuned prompts scored higher on the holdout (11 → 26 and 11 → 20),
  but paired sign tests give p = 1.00 and p = 0.55. The gain in both runs exists because the tuned
  prompts **abstain more** (wrong 20 → 12 and 20 → 14, right flat or down), which the scoring rewards
  (right − 2×wrong) but that pattern cannot distinguish better judgement from simply refusing more.
- **The deeper finding is the failure pattern.** deepseek's best prompt scored worse on tuning (40 vs 44)
  yet the local seat **failed to answer on 33 of 91 holdout keys** with it (vs 3 with glm's), and per-round
  failures grew with prompt length (0, 10, 18, 0, 24, 30, 42). The tuned prompts are longer and more
  conditional; the small seat then times out or mis-parses and the row costs −2 or 0. Longer prompts are
  not free on a 4 B model even when the text is better. Open question, tracked in the prompt-tune repo.
- **The loop design held one thing stable: the cloud never saw the data.** Every round's only inputs were
  counts and category names; the run records and prompts were checked for task strings before publishing
  these aggregates.

Caveats: one holdout pass per arm (n = 1 arm-scoring), one local model, one person's transaction history,
and the cloud writers are frontier models whose token counts differ by an order of magnitude
(148k vs 28k completion tokens — the cheaper writer produced the flatter gain).
