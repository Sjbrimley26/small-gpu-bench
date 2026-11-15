# §110 CAT on a synthetic fixture: eleven models, the schema arm, the arena, and how it compares with a private run

> Excerpt of §110 of a private lab notebook; paths, seat names and hosts are generalised. The fixture is invented
> (`tests/cat.py`, seed 20261114); no real bank data is in this repo.

**Why.** The lab's payee-categorization test was run on a real bank statement and cannot be published. `tests/cat.py` rebuilds the question on
200 invented payees so anyone can re-run it. 70 rows are clear, 10 income and 12 transfer (rule-able). The other 108 are the hard tail: 30 weak, 25
misleading, 20 two-business ("dual"), 18 opaque and 15 brand-only. The opaque and brand rows (33) have gold `UNSURE`: a model that names a category
for them has made something up (a "false fill"). Scoring: right / wrong / blank on the 167 category rows, abstained / false fill on the 33 unknowable rows.
Two attempts per row: output cap 256 tokens, then 512 if the reply was empty or cut off. llama.cpp router (build 12211), GTX 1050 Ti 4 GB, one model resident,
**no-schema arm** (no `response_format`).

**No-schema arm, all 200 rows**

| model | right | wrong | blank | abstained of 33 | false fill | wrong share of answered (all / tail) | wall |
|---|---|---|---|---|---|---|---|
| FrogNano-4B | **160** | **5** | 2 | 31 | 2 | 3.0% / 6.8% | 65 s |
| bolt-e4b, thinking on | 148 | 16 | 3 | 33 | 0 | 9.8% / 22.2% | 496 s |
| ling3-tiny-64k | 147 | 20 | 0 | 3 | **30** | 12.0% / 25.3% | 55 s |
| gemma4:e4b | 145 | 19 | 3 | 25 | 8 | 11.6% / 26.4% | 60 s |
| bolt-e4b, thinking off (ctx 24576) | 143 | 21 | 3 | 30 | 3 | 12.8% / 29.2% | 69 s |
| granite4:3b-h | 133 | 12 | 22 | 33 | 0 | 8.3% / 17.5% | 62 s |
| granite3.3:2b | 115 | 38 | 14 | 28 | 5 | 24.8% / 36.4% | 36 s |
| nemotron-3-nano:4b, thinking on | 114 | 7 | 46 | 33 | 0 | 5.8% / 13.5% | 1291 s |
| granite4-micro | 99 | 50 | 18 | 33 | 0 | 33.6% / 47.0% | 49 s |
| granite4.2:3b | 12 | 0 | 155 | 33 | 0 | n/a | 6127 s |
| qwen3.5:4b (text) | 5 | 0 | 162 | 33 | 0 | n/a | 8598 s |

granite4.2:3b and qwen3.5:4b spiral to the token cap on almost every category row (about 100k completion tokens each); their blanks are the cap, not
abstention, and they "abstain" on the unknowable rows only because they answer nothing. Treat them as unusable at this cap, not as careful.

**By fixture tier** (right, or abstained on opaque/brand): every model that works at all is near-perfect on clear, income and transfer. The spread is in
`misleading` (FrogNano 22/25, bolt-e4b no-think 7/25, gemma4:e4b 10/25, ling3-tiny 15/25) and the unknowable tiers (ling3-tiny abstained on 3 of 33).

**Schema arm** (json_schema strict, first 100 rows, 18 of them unknowable; same 100 rows without a schema in brackets):

| model | right | wrong | blank | abstained of 18 | false fill | wall |
|---|---|---|---|---|---|---|
| gemma4:e4b | 59 (72) | 6 (9) | 17 (1) | 18 | 0 (4) | 1483 s (29 s) |
| bolt-e4b, thinking off | 54 (72) | 3 (9) | 25 (1) | 18 | 0 (2) | 2055 s (34 s) |

A strict schema removes the false fills and some wrong answers, but costs about 50x wall and turns them into blanks (21 and 27 of 100 rows hit the length cap).
Fewer errors, fewer answers, far slower: not a free upgrade on a small card at this output cap. Only two models were run on this arm.

**Arena** (`tests/arena.py`, the nine models that answer): oracle ceiling 165 of 167. Weighted vote with quorum 1: 150 right, 17 wrong, 30 false fills
(it inherits ling3-tiny's habit of answering everything). Quorum 2: 150 right, 15 wrong, 8 false fills, 25 of 33 abstained. Full agreement: 47 right, 1 wrong,
119 blank. Rows where every model agrees on a wrong answer: 1. The best single model (160) beats both votes.

**How this differs from the private run, and whether the rankings agree.** The private run used one month of a real checking statement (about a hundred
scored rows, scored against the author's own answer key) and, separately, a hard-tail extension and a three-model arena. Counts are not comparable: different rows, different
mix (here 46% of rows are easy rule-tier payees), and the synthetic gold has a built-in "unknowable" slice that the real key does not reward abstaining on.
- **Rankings do not agree on right-count.** FrogNano-4B is first here and mid-pack privately; granite4-micro is near the top privately and last here; qwen3.5:4b
  is upper-middle privately and unusable here.
- **The shape does agree.** The model that answers everything (ling3-tiny) pays in wrong answers privately and in false fills here; the thinking and document
  models abstain on unknowable rows and have the low wrong shares; granite4-micro has one of the worst wrong shares in both.
- **The arena finding agrees.** Full agreement is precise but covers little, quorum 1 fills the most with the most false fills, and no vote beats the best single
  model by much.
- So the synthetic fixture is a method check and a ranking of failure modes, not a leaderboard. FrogNano's lead here is untested against the private key.

**Fault found while running it.** bolt-e4b failed with proxy errors and zero tokens at ctx 32768: the load succeeds and the first CAT-sized prompt then dies with
`CUDA error: out of memory` in the compute-buffer allocation; an 8-token probe passes and hides it. At ctx 24576 the same model ran all 200 rows with no errors.
