# §104 Summarize on the Bonsai 27B transcript: single pass beats the pipeline again; the checker preset is a poor writer

> Excerpt of §104 of a private lab notebook; paths, seat names and hosts are generalised.

**Why.** Summarizing this 35k-char transcript (a review video of the Bonsai 27B model, ~9k tokens) with the
checker preset as both writer and checker scored badly (13/31 facts, 25 `[unverified]`). Re-ran with the shipped
seats and the cheap local candidates, to see which arm to trust on a transcript that is longer than the 27.7k one
in §97.

**Harness.** A 31-fact regex list (19 must-have) written by hand from the transcript, numbers absent from the
source, no judge model. One run per cell. The source is 35k chars, so `single` uses the 16k seat for the writer
preset (the whole text fits, ~9k tokens).

| arm | configuration | cov /31 | must /19 | numbers not in source | unverified | words | s |
|---|---|---|---|---|---|---|---|
| single | writer preset, gemma4:e4b at 40k (no projector) | **25** | **19** | 0 | 0 | 673 | 124 |
| single | writer preset, gemma4:e4b at 16k | 24 | 18 | 1 (`5`) | 0 | 607 | 122 |
| single | summary preset, granite4.2:3b at 16k, reasoning on | 21 | 15 | 1 | 0 | 588 | 115 |
| single | summary preset, minicpm5-2b at 64k | 20 | 16 | 2 (`1.5`, `5`) | 0 | 470 | **71** |
| pipeline | writer preset + checker preset | 18 | 9 | 0 | 17 | 698 | 546 |
| `--loaded`, checker preset writes and checks | checker preset | 13 | 8 | 0 | 25 | 673 | 435 |

**Verdict.**

- Same as §97: a single pass beats the pipeline on a document that fits (24-25/31 vs 18/31 at ~1/4.5 of the
  time). The pipeline lost 10 must-haves; it drops whole topics (the 3-hour phone run, the OpenCode fix loop, the
  keyboard diffusion questions) because the extract-then-write path compresses 8 chunks into notes before writing.
- The checker preset is fine as a *checker of planted errors* (§96.1) but flags ~half of a clean summary (17
  flagged of ~48 claims here, 19 of 43 in the checker-only run). The headline 13/31 was that preset writing; it
  was never the intended writer.
- gemma4 at 40k covers one more fact than at 16k (19/19 must vs 18/19) at the same 124 s. One run each, so the
  difference is one fact and not a verdict; but no cost showed up, so 40k is free for single-pass on long inputs.
- granite4.2 (21) and minicpm5 (20) are the fast fallbacks: minicpm 71 s but 2 invented numbers; neither beats
  gemma4.
- Missed by every arm: method-closed, spec-decoding 1.34x, binary ammo/no-game detail, the "full precision was
  suspiciously short" aside. Those are small asides in a long video, not must-haves.

**Action.** For transcripts, use the single pass (the tool falls back to the pipeline above 30k chars, which is
why this 35k file took the bad path). Raise the single-pass character limit and run it on a 40k seat, or open a
card for flag precision.
