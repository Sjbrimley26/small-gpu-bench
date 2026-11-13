# §97 The summarize-verify pipeline, built and measured: single pass wins on documents that fit

> Excerpt of §97 of a private lab notebook; paths, seat names and hosts are generalised.

**Why.** §96/96.1/96.2 showed a local model can *judge* a summary (bolt thinking-on named 18/18 planted
errors) but also that single-pass coverage was already 20-23/24 facts. The open question was whether wrapping the
writer in checks (extract -> write -> per-claim check -> repair) actually produces a better summary, and whether
the long-document path works at all.

**Harness.** Same source, two arms, identical code-side scoring.
- single pass = the notebook's standard SUM prompt, one call, gemma4 (the writer preset), no checks. Truncated to
  the first 30k chars when the source does not fit the seat, and the truncation is reported (that IS the
  single-pass limit).
- pipeline = extract (4 chunks or more, 6-9 points each, deduped by Jaccard >= 0.6) -> write (whole document up
  to 40k chars; past that a two-level notes tree, groups of 4 sections) -> batched claim check (the checker
  preset, `S`/`U`/`C` plus a verbatim quote verified in code, numbers checked against the source) -> coverage
  check -> one repair -> re-check. Claims still failing are kept and marked `[unverified]`.
- Scoring: the SUM harness fact list on the transcript (24 facts, 10 must-have) + numbers absent from the source
  (footer stripped first: an earlier pass counted the pipeline's own footer and produced phantom numbers).

| source | chars | arm | words | cov | must | numbers not in source | seconds |
|---|---|---|---|---|---|---|---|
| transcript | 27.7k | single | 636 | **22/24** | **10/10** | 0 | 89 |
| | | pipeline | 413 | 18/24 | 9/10 | 0 | **713** |
| arXiv PDF (DeepSeek-V2) | 143.9k | single | 282 | — | — | 0 | 65 (**truncated to 30k**) |
| | | pipeline | **1142** | — | — | 0 | 2127 |
| handbook (3 pages) | 21.0k | single | 538 | — | — | 0 | 107 |
| | | pipeline | 499 | — | — | 0 | **591** |

Pipeline's own counts: transcript 24 claims / **17 flagged** / checklist 24/24; PDF 84 claims / 43 flagged /
177/230 points; handbook 34 claims / 0 flagged / 39/44 points.

## Verdict

- **On a document that fits one seat, the single pass is better and 7-8x cheaper.** 22/24 vs 18/24 facts and
  10/10 vs 9/10 must-haves, at 89 s vs 713 s. No arm invented a number (0 across the board once the metric was
  fixed). The checks do not add accuracy here: they mostly add runtime.
- **The pipeline is the only arm that summarises a document too big for one seat.** The single pass died outright
  at 41k tokens on the 143.9k-char paper and had to be truncated to 30k chars (282 words, the first ~5 pages); the
  pipeline produced a 1142-word summary over all 35 chunks in 35 min. Quality is moderate, not good: 43 of 84
  claims flagged, 23% of checklist points uncovered.
- **The claim flags are too noisy to ship as-is.** 17 of 24 claims flagged on the transcript for a summary whose
  numbers all check out. The checker is a good *detector of planted* errors (§96.1: 17/18) but its false-positive
  rate on clean text is high, which in this design means half a summary carrying `[unverified]`. Fix: verify with
  the audit preset (bolt, thinking on) for anything the user will read, or drop to a code-only check (numbers +
  quote presence) and skip the model verdict.
- **Long-document design, corrected twice.** First version gave a multi-chunk writer only its opening and closing
  sections (13 checklist points lost, one invented claim through). Second gave it a flat checklist: 114 of 119
  points lost on the paper, and 5/17 claims verified. The notes tree fixed the structure (groups of 4 sections,
  then write from the notes) and coverage rose to 177/230, but 35 minutes per paper means this belongs in an
  overnight queue, not on demand.
- **What to use, then.** Emails, notes and transcripts: single pass, with the pipeline's code-side checks bolted
  on only if a claim list is wanted. Long PDFs and anything over ~30k chars: the pipeline in batch mode. The RAG
  service still owns "answer a question about a document".

Caveats: one run per cell, one source per scoring regime (only the transcript has a fact list), gemma4 + the
checker preset only (§96.2's bolt verifier was not run in this A/B), and the PDF's single-pass arm is a
truncation, not a fair fight. The 17-flag figure is one summary; its false-positive rate needs a cleaner
measurement (planted-error recall and false-flag rate on a hand-labelled set).

## §97.1 The batched queue path, end to end for real

One batch-mode run through the queue's GPU window, on a clean written summary rather than a video transcript:
**10 of 10 claims verified, 0 flagged, checklist 7/8**. Two things that says: the integration is real (queue ->
window -> seat switching by the task), and the checker's behaviour is source-dependent, not uniformly harsh —
17/24 flagged on the video transcript, 0/10 on a clean written summary. The flag rate needs a hand-labelled set
before it can be tuned (open question).
