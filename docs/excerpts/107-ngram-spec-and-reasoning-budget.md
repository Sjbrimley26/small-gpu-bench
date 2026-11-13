# §107 n-gram speculative decoding and `--reasoning-budget` on the 4 GB card

> Excerpt of §107 of a private lab notebook; paths, seat names and hosts are generalised.

**Why.** Two llama.cpp levers had not been measured here. (1) n-gram spec decoding: still speculative decoding,
but the drafter is a hash table over the prompt + generated text instead of a second model, so on Pascal — where
the DFlash drafter's own compute ate the gain (§88: 16.9 -> 3.1 t/s) — it costs almost nothing. (2)
`--reasoning-budget`: a cap on thinking tokens, i.e. the knob that decides whether the audit preset's 36 s/item is
inherent or just talking. Both were run on the batch queue's own rental-extraction task and prompt, not a synthetic
bench. Every run went through one GPU window (lease taken once, the other local services stopped and the aux lane
moved to the cloud, restored afterwards).

## §107.1 What the server actually accepts (build 12211, commit 7d7f02875)

`llama-server --help` has five n-gram types: `ngram-simple`, `ngram-map-k`, `ngram-map-k4v`, `ngram-mod`,
`ngram-cache`, plus `--spec-ngram-{simple,map-k,map-k4v}-size-n/-size-m/-min-hits` and
`--spec-ngram-mod-n-min/-n-max/-n-match`.

**Per request.** Probe 1 sent `speculative.types`, `speculative.type` and `speculative:{types:[…]}` in the
`/v1/chat/completions` body: HTTP 200, no error, and `timings.draft_n` stayed null. Probe 2 repeated a long prompt
twice per variant on a configuration with **no** spec flag at all: 15.31 / 15.32 t/s control, 15.31 / 15.22 with
`speculative.types=[ngram-mod]`, 15.31 / 15.32 with `[ngram-simple]`, draft counters null every time. So **spec
type is a server/load-time flag only; the body fields are accepted and ignored.** That forced presets (added to the
preset generator, generated, then a server restart *inside* the window so the router would parse them at startup).

**`--reasoning-budget` IS per request**, as `reasoning_budget_tokens` (probe 1: audit preset thinking on, one
listing, unlimited 656 tokens / 42.4 s vs budget 64 -> 118 tokens / 7.3 s). `thinking_budget_tokens` is also
accepted (118 tokens, 7.2 s) and `reasoning_budget_message` is accepted alongside it (128 tokens, 7.7 s). Two names
for one knob; the harness uses `reasoning_budget_tokens`.

## §107.2 Rental extraction: n-gram spec, temp 0, 23 gold listings

The control is the production-equivalent configuration with the same weights and context. Decode t/s is the
server's own `timings.predicted_per_second`, median over the 23 items; acceptance is
`draft_n_accepted/draft_n` summed per pass. "fields" = items with every non-lenient gold field right
(rent/beds/baths/kind/pets/red_flags).

| arm | configuration | decode t/s, median (item range) | accept % | checker | fields | s/item | n |
|---|---|---|---|---|---|---|---|
| bolt control | audit preset (no-think arm), 16k | 16.70 (16.3-16.9) | — | 21/23 | 10/23 | 4.02 | 2 |
| bolt `ngram-simple` default (n=12,m=48) | n-gram simple, defaults | 16.59 (16.2-16.7) | 0 (0 drafts) | 21/23 | 10/23 | 4.08 | 1 |
| bolt `ngram-mod` default (match 24) | n-gram mod, defaults | 16.56 (16.2-16.7) | 0 (0 drafts) | 21/23 | 10/23 | 4.09 | 1 |
| bolt `ngram-simple` n=4,m=8 | n-gram simple, 4/8 | 15.83 (15.2-16.1) | 5.3 | 21/23 | 9/23 | 4.24 | 1 |
| **bolt `ngram-mod` match 8 / n-min 4 / n-max 16** | n-gram mod, 8/4/16 | **22.16 (16.3-41.6)** | 33.1 | 21/23 | 10/23 | 3.30 | 2 |
| vision control | writer preset, 16k | 17.27 (16.9-17.5) | — | 21/23 | 10/23 | 3.90 | 2 |
| **vision `ngram-mod` 8/4/16** | n-gram mod, 8/4/16 | **30.82 (16.8-67.7)** | 32.0 / 44.3 | 21/23 | 10/23 | 2.88 | 2 |
| frog control | checker preset | 22.69 (22.3-22.8) | — | 21/23 | 7/23 | 3.70 | 2 |
| **frog `ngram-mod` 8/4/16** | n-gram mod, 8/4/16 | **32.71 (22.1-45.7)** | 35.4 / 47.6 | 20/23 | 7/23 | 3.02 | 2 |

Per-pass decode t/s (aggregate over the 23 items), because the spread between passes is bigger than the spread
between seats: bolt control 17.03 / 16.96; bolt 8/4/16 22.78 / 22.78; vision control 17.60 / 17.62; vision 8/4/16
23.30 / **41.56**; frog control 23.06 / 23.07; frog 8/4/16 28.09 / **38.04**. Pass 2 is faster because the n-gram
table already holds the earlier pass's text (the slot keeps its state across requests on the same load); a fresh
seat load starts cold, which is exactly what a batch window does per seat.

**Default n-gram settings do nothing on this workload.** With `ngram-simple` (12-token lookup, 48-token draft) and
`ngram-mod` (24-token match), zero drafts were generated on all 23 items — these JSON replies are ~50 tokens and
never repeat a 12-token run, and the defaults want a 48-64-token draft. Shrinking the window to 8/4/16
(`spec-ngram-mod-n-match = 8`, `-n-min 4`, `-n-max 16`) is what turns it on: 33% acceptance, ~6 accepted tokens per
draft, +30-34% decode on bolt and the writer preset. `ngram-simple` shrunk to 4/8 accepts only 5.3% and *loses* 6%
speed (the 4-token lookup matches junk); the map/mod lookup is the one to use.

**Outputs at temp 0 are unchanged** where it matters: bolt control vs bolt 8/4/16 **23/23 replies byte-identical**
(both passes), vision control vs vision 8/4/16 **23/23 identical** (both passes). Two exceptions, both small:
`ngram-simple` 4/8 changed item 0 (it added a bogus `"no viewing"` red flag, fields 10 -> 9), and frog changed 2-3
items — one of them a real regression (item 5: it filled `beds: 1` against a 2BR attribute and the checker
rejected it, 20/23 vs 21/23). So spec-at-temp-0 is not a guarantee of identical text on these models, just very
close; the bolt/gemma pairs reproduce exactly and frog does not.

**Temperature.** Re-running bolt control and bolt 8/4/16 at the seat's vendor sampling (temp 1.0, top_p 0.95,
seeded 1000·rep+i) keeps all 23 replies byte-identical between control and spec — but costs 2 items on the strict
field score (10/23 -> 8/23) at the same 21/23 checker and the same speed (16.92 vs 22.16 t/s: spec still +31%).
Vendor temperature costs accuracy, not speed, on this task; spec preserves whatever the temperature produces.

## §107.3 One long generation: summarize the 27.7 KB transcript on the writer preset

The summarization harness's own prompt and scorer; 1500 max tokens, temp 0, 2 reps.

| arm | rep | decode t/s | drafts (accepted) | COV /24 | MUST /10 | dup 6-grams | words | wall s |
|---|---|---|---|---|---|---|---|---|
| control | 1 | 15.18 | — | 23 | 10 | 0% | 648 | 80 |
| control | 2 | 15.11 | — | 23 | 10 | 0% | 648 | 80 |
| ngram-mod 8/4/16 | 1 | 15.13 | 18/112 (16%) | 23 | 10 | 0% | 654 | 81 |
| ngram-mod 8/4/16 | 2 | **46.79** | 763/960 (79%) | 23 | 10 | 0% | 647 | **37** |

Long prose is where n-gram spec should shine, and on the second pass it does: 3.1x. The first pass after a load
gets almost nothing (112 drafted tokens, 16% accepted) — cold table. Coverage did not collapse (23/24 and MUST
10/10 on every run, same as control; dup 0%). The texts are not byte-identical to control here, which is expected:
at 964 tokens a single divergence compounds, and the scorer's 23/24 + 10/10 is the check that nothing was lost.

Production shape matters: the batch queue loads a seat, runs the window's jobs, unloads. For a summarize job the writer
seat would be cold, so the realistic gain is the 15.1 -> 15.1 cold pass, not 46.8. n-gram spec pays off on a seat
that stays loaded across requests (interactive use, or 2+ jobs per seat per window), not on a one-shot swap.

## §107.4 `--reasoning-budget` on the audit preset (thinking on), 23 listings

Audit preset = bolt-e4b, 24k, reasoning on; `reasoning_budget_tokens` per request, temp 0, n=1 per arm. "think
tokens" = `timings.predicted_n` minus that item's answer length measured on the thinking-off pass (the router had
no `/tokenize` endpoint, so this is a subtraction, not a tokenizer count; the 128/256 arms land at 131/259, i.e.
the budget plus the injected end-of-thinking text).

| budget | checker | fields | think tokens median (mean, max) | s/item | decode t/s |
|---|---|---|---|---|---|
| unlimited (-1) | 23/23 | 12/23 | 517 (553, 777) | 36.67 | 16.43 |
| 512 | 23/23 | 12/23 | 513 (495, 517) | 35.04 | 16.46 |
| 256 | **23/23** | **14/23** | 259 (259, 267) | **19.58** | 16.62 |
| 128 | **23/23** | 12/23 | 131 (131, 139) | **11.98** | 16.65 |

Decode speed is flat (~16.5 t/s) across every budget: the only thing the budget changes is how many tokens get
generated, so wall time is almost linear in it. Unlimited thinking naturally stops at ~517 tokens median on this
task, which is why budget 512 is a no-op and 256 is the first budget that bites — and it bites for free: still
23/23 on the task's checker, 14/23 on strict fields (2 better than unlimited, noise at n=1), 19.6 s/item instead
of 36.7. Budget 128 gives 12 s/item, still 23/23. Against thinking off (4.0 s/item, 21/23), the ladder is now
4 s -> 12 s -> 20 s -> 37 s for 21 -> 23 -> 23 -> 23 checker passes, so 128 is the value point if a second seat
failure costs more than 8 s.

## §107.5 Verdict

- **n-gram spec is a real, model-free win, but only with a small lookup window.** Defaults: 0 drafts, 0 gain.
  `ngram-mod` with match 8 / n-min 4 / n-max 16: +30-34% decode on bolt and the writer preset with byte-identical
  temp-0 output, ~3x on a warm long generation, and it costs VRAM nothing (no drafter).
- **`--reasoning-budget` is the bigger operational lever.** 256 (or 128) on the audit preset keeps 23/23 and cuts
  thinking-on cost from 37 s/item to 20 (or 12), and it is per request, so no preset change is needed.
- Cold vs warm: the first request after a load gets almost no n-gram benefit. Any future measurement of n-gram
  spec must say which pass it is.

**Not done.** n=2 only on the spec arms, n=1 on every budget arm; no `ngram-cache`/`ngram-map-k4v`; no sweep of
match 6/10/12; no per-request `spec-type` (impossible on this build).
