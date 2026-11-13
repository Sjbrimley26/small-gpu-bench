# §96 Judge calibration and redaction recall (§96, §96.1, §96.2)

> Excerpt of §96, §96.1 and §96.2 of a private lab notebook; paths, seat names and hosts are generalised.

**Why.** A public "AI agents from scratch" tutorial runs generate -> same-model validator ("rate 1-5") and a
PHI sanitizer, all on llama3.2:3b. The lab's test matrix covered the generate steps but not the two pieces that
design leans on: whether a local judge's score moves when the output is wrong, and whether a local sanitizer
leaks. Two tests, JUDGE and RED, on the three plausible single-seat pipelines: the writer preset
(gemma4:e4b), the aux preset (ling3-tiny-64k), and the checker preset (FrogNano-4B, 32k).

**Harness.** A bench script with a `selftest`, `run <seat>` and `report`, run under a GPU lease (the other
local services stopped, restored afterwards). The selftest passed before any model call: no plant pattern
matches an empty problem list, planted fabrications are absent from the transcript, the no-op leaks all 78,
an oracle leaks 0 and keeps 72/72, an over-redactor leaks 0 and keeps 0. Reasoning off on every seat.

- **JUDGE**: the notebook's standard SUM transcript (~6.9k tokens) plus six summaries: gold, F1 (wrong
  parameter counts), F3 (F1 + a company swap + an invented RTX 4090 run), CONTRA (a C++ test flipped to a
  success and the verdict reversed), OMIT (results section truncated to two items), OFF (a sourdough summary).
  The judge returns `{problems, score}`. A planted error counts as named when the problem list matches a keyword
  pattern. Run 0 is greedy. Runs 1-2 use vendor sampling (FrogNano: generic 0.7/0.95). Cap 1200 tokens.
- **RED**: 12 synthetic docs (email, invoice, bank statement, phone note x3, seeded), 78 identifiers (names,
  emails, phones in 4 formats, addresses, account numbers incl. one split in groups, routing, card last-4, tax
  IDs, an obfuscated "x at y dot com" email) and 72 keep-items (companies, SKUs, amounts, dates). A digit
  identifier counts as leaked if its digits survive in any punctuation. Arms: regex only (no model), model only,
  hybrid (regex first, then model). Greedy.

## Results

| seat | JUDGE: defects below same-run gold | unparsed | gold scores | RED model leaked /78 | RED hybrid leaked /78 | kept /72 | s/doc |
|---|---|---|---|---|---|---|---|
| regex only | — | — | — | — | 30 (names, obfuscated email) | 72 | 0 |
| writer preset, gemma4:e4b | **15/15** | 0/18 | 4, 4, 4 | 8 (digits) | **2** (a bare surname) | 72 | 9 |
| aux preset, ling3-tiny-64k | 10/15 | 3/18 (greedy run) | 5, 5, 4 | 27 | 14 (all names) | 69 | 4.5 |
| checker preset, FrogNano-4B | 9/10 scored pairs | 5/18 | 4, —, 4 | 15 | 12 (all names) | 72 | 7.5 |

gemma4 per variant (3 runs): F1 3,3,3 · F3 2,2,2 · CONTRA 3,2,3 · OMIT 3,3,3 · OFF 1,1,1. It named 20 of 24
planted errors. Its misses: the omission (named in 1 of 3 runs) and one half of CONTRA in 2 runs.

## What it shows

- **gemma4:e4b can serve as a validator, as a ranking signal.** Every defect scored below gold in every run,
  and it named almost every planted error. Its scale is compressed: gold never got a 5, and a summary with three
  fabrications got 2, not 1. Use a threshold relative to the run (or "problems list non-empty"), not the
  absolute number.
- **ling3 and FrogNano are unreliable as judges, and the failure is format, not blindness.** When they return
  parseable JSON they name the plants. But at greedy temperature both loop, repeating the same problem until the
  cap. That cost 3 of 18 replies on ling3 and 5 of 18 on FrogNano (one was FrogNano's gold). ling3 also scored
  F3 (three fabrications) at 4 next to gold at 4 in run 2. A harness using either one needs a repeat-guard and a
  parse-or-retry.
- **A same-model 1-5 score that nothing acts on would mostly work on gemma4 and would not work on the other
  two.** Either way the score has to drive something (retry, escalate) to matter.
- **Redaction: hybrid beats both pure arms, and every remaining leak is a name.** Regex owns structured
  identifiers (0 digit leaks in any hybrid arm). The model owns names, and gemma4 gets all but 2. Both of those
  were a surname standing alone ("[Surname] in receiving will sign for it"). A small model alone leaks account
  digits (gemma4 8, ling3 and FrogNano more). **No arm is leak-free, so a masker for private data still needs a
  final code check against a known-name list (payees, vendor contacts) and must halt on any hit.**
- ling3 dropped 3 keep-items while redacting. The other two kept all 72.

Caveats: one transcript and five hand-built defects (n=3 runs each, so 15 pairs per seat). The keyword grader can
credit a problem that mentions the right word for the wrong reason (it did not in the replies read). RED is
synthetic, with one format family and n=1 at greedy. The real-data recall of the hybrid is unmeasured. FrogNano
ran at 32k, not its 64k configuration. Not run: thinking-on judges, and bolt-e4b as judge (the audit preset, best
planted-error recall in the notebook's doc-seat section).

## §96.1 Cross-model critic: writer x judge on real model-written summaries

Six real SUM summaries (FrogNano r1-r3, gemma4:e4b r1-r3). Each gets a planted twin with three asserted edits:
active params 1.3 -> 3, a company swap (one company name replaced by another), and an inserted line "ran it
locally on an RTX 4090 at 200 tokens/s". Judges: the writer preset (gemma4) and the checker preset (FrogNano,
32k). Run 0 greedy, run 1 sampled. Same JUDGE prompt, parser and grader as §96. Paired metric: planted twin
scored below its own clean original by the same judge in the same run.

| judge | writer | paired | planted named | unparsed |
|---|---|---|---|---|
| gemma4 | FrogNano (cross) | **6/6** | **14/18** | 0/12 |
| gemma4 | gemma4 (self) | 6/6 | **7/18** | 0/12 |
| FrogNano | FrogNano (self) | 2/4 | 13/18 | 2/12 |
| FrogNano | gemma4 (cross) | **5/5** | **17/18** | 1/12 |

Plants named per kind, gemma4 judge: on FrogNano's text params 6, company 2, rtx 6; on its own text params 2,
company 1, rtx 4. FrogNano judge on gemma4's text: 6, 5, 6.

- **Both judges name errors in the other model's writing far better than in their own** (gemma4 14 vs 7 of 18,
  FrogNano 17 vs 13). The score still drops on self-judging for gemma4 (6/6), but the problem list, which is what
  a repair step needs, is thin. Self-critique is the weak arrangement; cross-model is the strong one.
- The best cell is FrogNano judging gemma4: 17/18 named and every planted twin scored 2. But FrogNano still
  loses replies to format (3 of 24 unparsed) and twice scored its own planted twin equal to the clean one.
- Pipeline design that follows: gemma4 writes, FrogNano verifies (or the reverse, since gemma4 judges with 0
  unparsed replies). Batch by stage on the single-model router (generate all, then swap once and judge all), and
  give the FrogNano judge a repeat-guard plus a parse retry.
- Caveats: 6 summaries x 2 runs per cell, three plant types, one source transcript; the company swap is the
  hardest plant (the clean summaries do not all name the company, so its insertion reads as a plain fact). The
  14-vs-7 gap is suggestive, not proven. Self-blindness might be a style effect (the judge reads its own phrasing
  as fluent) rather than a model family effect; not separated here.

## §96.2 bolt-e4b as critic, thinking off and on

Same 12 items and harness as §96.1. The audit preset with thinking off (32k): run 0 greedy, run 1 vendor
1.0/0.95. The audit preset with thinking on (32k): 1 run at vendor think 1.0/0.95, output cap raised to 8000 so
the trace cannot starve the JSON.

| judge | on FrogNano text: paired, named | on gemma4 text: paired, named | unparsed | s/call |
|---|---|---|---|---|
| bolt, thinking off | 4/4, 12/18 | 3/3, **6/18** | 5/24 | 44 |
| bolt, thinking on | 3/3, **9/9** | 3/3, **9/9** | 0/12 | 122 |
| gemma4 (§96.1) | 6/6, 14/18 | 6/6, 7/18 (self) | 0/24 | ~40 |
| FrogNano (§96.1) | 2/4, 13/18 (self) | 5/5, 17/18 | 3/24 | ~35 |

- Thinking off, bolt behaves like a Gemma self-judge on gemma4's text: it named the inserted RTX line 6/6 but the
  edited parameter count and the company swap 0/6 each (on FrogNano's text 4 and 2). Every planted twin still
  scored 2, so the score is a clean signal; the problem list is not. All 5 unparsed replies were malformed JSON
  on clean items (`[...], "[]", 5}` with the "score" key dropped), recoverable with a lenient parse or a retry.
- Thinking on is the only judge so far that is perfect on both writers: 18/18 planted errors named, every twin
  scored 1-2, clean summaries 4-5, no unparsed replies. It costs ~3x the wall time per call. n=1 run (6 pairs),
  so "perfect" is a small sample.
- Shared lineage is not decisive: with reasoning on, the Gemma-derived bolt reads gemma4's text as well as
  FrogNano's. Reasoning, not model family, separates it from the thinking-off result.
- For the summary-verify pipeline: gemma4 writes; verifier is the audit preset (bolt, thinking on) when time
  allows, the checker preset (FrogNano, thinking off, with repeat-guard and parse retry) when it does not.
  Per-claim closed checks are shorter than this whole-summary judge prompt, so the thinking cost per claim is
  unmeasured.
