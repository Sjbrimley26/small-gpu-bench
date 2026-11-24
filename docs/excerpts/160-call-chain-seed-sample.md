# §160 CALL-CHAIN at n=3: three further fixtures, and the pair's gain is narrower than one fixture said

> Excerpt of a private lab notebook entry; paths, service names and hosts are generalised. Numbers measured by
> `tests/call.py --seed` and `tests/call_chain.py --seed` (both in this repo) on one 4 GB card with a llama.cpp router,
> thinking on with a 256-token budget on both passes, temperature 0, one run per call. Read [§159](159-call-chain.md)
> first: it carries the arms on the original fixture and the single-seat baselines.

**Why the sample is fixtures, not repeats.** At temperature 0 a second run of the same fixture is bit-identical (pass 1
of §159 reproduced §158's rows to the digit), so repeating it measures nothing. The sample is three further 44-call
fixtures — `--seed` redraws the whole set, with its own gold built in code with the transcript. The default seed is
unchanged and the committed fixture still matches in `--selftest`, so this is an out-of-sample test of an order chosen
on the first fixture.

**Arms per fixture:** the proposer alone (pass 1), the cleaner seat alone (the incumbent on false tasks), then the
cleaner seat auditing the proposer's saved list (keep/drop only, never rewriting a task).

| fixture | arm | recall | due | false tasks | true tasks dropped | false tasks dropped | audit unparsed |
|---|---|---|---|---|---|---|---|
| A | proposer alone | 26/27 | 20/24 | 9 | – | – | 0 |
| A | cleaner alone | 25/27 | 13/24 | 7 | – | – | 0 |
| A | proposer → cleaner | 24/27 | 19/24 | 7 | 2 | 2 | 0 |
| B | proposer alone | 27/27 | 17/23 | 8 | – | – | 0 |
| B | cleaner alone | 24/27 | 11/23 | 4 | – | – | 0 |
| B | proposer → cleaner | 27/27 | 17/23 | 3 | 0 | 5 | 0 |
| C | proposer alone | 27/27 | 15/19 | 12 | – | – | 0 |
| C | cleaner alone | 24/27 | 7/19 | 7 | – | – | 0 |
| C | proposer → cleaner | 27/27 | 15/19 | 6 | 0 | 6 | 2 |

Pooled over these three fixtures, with the §159 fixture as a fourth:

| arm | recall | due | false tasks | true dropped | false dropped |
|---|---|---|---|---|---|
| proposer alone, 4 fixtures | 107/108 | 74/92 | 38 | – | – |
| cleaner alone, 4 fixtures | 98/108 | 47/92 | 21 | – | – |
| proposer → cleaner, 4 fixtures | 105/108 | 73/92 | **20** | 2 | 18 |

**Reading.**
- The chain still works, but the first fixture was its optimistic end: 9 → 7, 8 → 3, 12 → 6 here against 9 → 4 there,
  for 2 promises out of 81 and one due date. Pooled, it removes about half the proposer's false tasks.
- Against a single seat it does not win on false tasks — 20 vs 21 for the cleaner seat alone over four fixtures is a
  tie. What it buys is the proposer's recall and dates: 105/108 vs 98/108 promises, 73/92 vs 47/92 due dates. Against
  the proposer alone it buys 18 false tasks for 2 promises and 1 date.
- Where pruning hurts is not spread evenly. The only fixture that lost promises lost both in long transcripts whose
  commitment sits mid-call (3 of 12 such promises dropped there, 0 of 12 on the other two).
- What the audit never removes: the three wrong-number calls (nobody on the call commits to anything) produce one
  invented task each on every fixture and the auditor keeps all three every time. Across the four fixtures those three
  calls supply 12 of the 20 surviving false tasks — a rule or prompt fix, not a model one.
- One seat's own spread is large: the proposer invents 9, 8, 12 false tasks and the cleaner 7, 4, 7 on the same three
  fixtures, and the due-date denominator moves too (24, 23, 19 of 27 commitments carry a date). Pooled counts are the
  only sound comparison; a single-fixture line is a claim with a ±40% error bar.
- Timings with the card to itself (the §159 run shared it): proposer ~21 s/call, cleaner ~17 s/call, the pair ~1.45x
  the proposer alone. The earlier 1684 s figure was inflated by the concurrent sweep and is not a speed.

**Not measured:** a temperature above 0 (this measures fixtures, not the sampler), the auditor's drop rule on real
calls, and any auditor other than the cleaner seat on these three fixtures.
