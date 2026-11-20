# small-gpu-bench

Real tasks on a 4 GB Pascal card: what works, what doesn't, and the patterns that make small models usable.

This repo holds the portable parts of a lab's small-GPU LLM benchmark work:

- **`tests/`**: 21 test scripts — 19 on synthetic fixtures, 2 on real public documents (redaction, planted-error audit, arithmetic, tool selection,
  LLM-as-judge, OCR) that run against any OpenAI-compatible endpoint — 16 of them call it, and `tests/arena.py`
  votes offline over other models' saved records.
- **`patterns/`**: two stdlib-only building blocks: a **checker-gated escalation loop** and a **summarize-verify
  pipeline**.
- **`results/results.csv`**: the lab's recorded scores for exactly those tests (194 rows; the composition is
  broken down under [`results/results.csv`](#resultsresultscsv), including the two `PTUNE` rows from the
  prompt-tune runs, see [prompt-tune.md](docs/excerpts/prompt-tune.md)).
- **`mock/`** and **`tests/smoke.sh`**: a fake server so everything can be exercised without a model.

Python 3 standard library only. `tests/smoke.sh` passes on Python 3.14 and 3.11; older versions are untested.

> **Where the numbers come from.** `§N` below refers to a section of the author's lab notebook. The cited sections
> are shipped as sanitised excerpts in `docs/excerpts/`, one file per section, linked at every claim below; the
> PTUNE row links point instead at [prompt-tune.md](docs/excerpts/prompt-tune.md), excerpted from the prompt-tune
> run records. Each
> figure in this README is copied from one of those excerpts or from `results/results.csv`, and none is from memory.
> Where a sample is small, it says so. `docs/RESULTS-NOTES.md` collects the sample-size, fixture and
> sanitisation notes in one place.

## Hardware

All published results come from one machine.

| | |
|---|---|
| GPU | NVIDIA GTX 1050 Ti, 4 GB (Pascal, compute 6.1, no tensor cores) |
| CPU | Intel i5-6500 (4 cores, AVX2) |
| RAM | 12 GB |
| Server | llama.cpp `server-cuda`, build 12211 (router image as of 2026-11-09; build and commit in [§107.1](docs/excerpts/107-ngram-spec-and-reasoning-budget.md)) |
| KV cache | `q8_0` for K and V, flash attention on (machine-wide for every published row) |

The build is not the same in every section: [§88](docs/excerpts/088-dflash-no-go.md) ran build 11889, [§52](docs/excerpts/052-vulkan-vs-ollama.md) compared Ollama 0.34.2 (CUDA 12) with llama.cpp
`2851b3b` (Vulkan), and the older rows in `results.csv` were measured through Ollama before the move to llama.cpp.
Only one model is resident at a time on this card; a second one does not fit, which is why the drafter test in [§88](docs/excerpts/088-dflash-no-go.md)
had to reserve VRAM by hand (`fit-target = 2200`) before it would load.

## Run the tests against your endpoint

```sh
export BASE_URL=http://localhost:8080     # any OpenAI-compatible server root (/v1 optional)
export MODEL=my-model                     # name sent in the request
export API_KEY=                           # only if your server wants a bearer token

python3 tests/mon.py                      # one test; each has --help and --selftest
bash tests/smoke.sh                       # every test and pattern against the mock server, no GPU needed
```

A llama.cpp example (adjust the model, context and flags for your build): `llama-server -m model.gguf -c 16384 --port 8080
-ctk q8_0 -ctv q8_0 -fa on`. For reasoning models, `--think on|off` sends `chat_template_kwargs.enable_thinking`.

Every script reads `BASE_URL`, `MODEL`, `API_KEY` from the environment (flags override), uses fixtures relative to the
repo, runs with `--selftest` (grader checks, no model), and writes one JSON record per run to `./results/raw/`
(`--out-dir` to change; credentials and hostnames are not written into the record).

| script | test | what it measures | scored by |
|---|---|---|---|
| `tests/red.py` | RED | 12 synthetic documents, 78 planted identifiers, 72 keep-items; model-only vs regex+model | identifiers leaked /78 (lower is better); keep-items kept /72 |
| `tests/aud.py` | AUD | find planted errors in two documents of 7.7k and 9.0k words (price conflict, arithmetic, term conflict, impossible date) | errors named in the answer; invented numbers |
| `tests/mon.py` | MON | a money problem under a token cap (answer 821.64, computed in code) | right to the cent; a cap hit is a fail |
| `tests/hrd.py` | HRD | 10 harder problems, truth computed in code, two of them "cannot be determined" | correct /10 per temperature |
| `tests/tool_probe.py` | TOOL | first tool picked with 4 tools vs 20 tools incl. a `tool_call` meta-dispatcher | right tool |
| `tests/judge.py` | JUDGE | does a 1-5 rating drop for a broken summary; are planted errors named | defects scored below gold; plants named; unparsed |
| `tests/crit.py` | CRIT | JUDGE on model-written summaries and a planted twin of each; self vs cross writer | paired drop; plants named |
| `tests/sum_test.py` | SUM | single-pass summary scored in code against a fact list; `--pipeline` adds the verify pipeline | facts hit; numbers not in source |
| `tests/ocr.py` | OCR | read 5 planted values from an invoice page drawn in code (vision model needed) | fields right /5 |
| `tests/cat.py` | CAT (synthetic) | categorize 200 invented bank-statement payees (descriptor noise, abbreviations, misleading and two-business names, unknowable ones) into 14 categories or UNSURE; fixture generated from a seed in code | right / wrong / blank on category rows; abstained vs false fill on unknowable rows; whole fixture and the hard tail separately |
| `tests/sql.py` | SQL-SYN (synthetic) | 50 text-to-SQL questions on an invented hardware-store SQLite schema (quoted column names, near-duplicate names, a join not to make, NULLs, ties, text dates); gold verified by executing it; schema built from a seed in code | executed rows equal gold rows; errors by class (syntax, hallucinated column/table, other, wrong rows); lenient vs strict SQL extraction; per-kind accuracy |
| `tests/arena.py` | ARENA (synthetic) | offline: several models' CAT records vote on the same payees | each model alone vs weighted vote (quorum 1, 2) vs full agreement; agreement-tier precision; oracle ceiling |
| `tests/rent.py` | RENT-SYN (synthetic) | extract one invented rental listing as strict JSON (rent, beds, baths, kind, pets, town, red_flags) with a checker that rejects replies contradicting the listing's own price and BR attributes; 100 listings from a seed in 16 tiers (pets stated plainly / in passing / mixed / absent, buried "no dogs", cats-only, 55+, room for rent, studio, half baths, price field vs body, scam signs, non-housing, 4000+ character bodies); thinking-off and thinking-on (256-token reasoning budget) arms | checker pass (= escalation rate); rent, beds, baths, kind, pets exact; red_flags precision / recall and spurious flags; per tier |
| `tests/aux_lanes.py` | AUX (synthetic) | the four helper-model jobs of an agent on Hermes' verbatim prompts (MIT, attributed): approve/deny/escalate 27 flagged shell commands, a done/blocked/continue/wait goal judge (12), a JSON task-spec contract (6), session titles (31 openers) | exact and safe-direction per lane; **dangerous approved** (a destructive command approved); empty replies; title and triage contract passes |
| `tests/embed_timing.py` | EMBED | what an embedding server costs, and whether two devices serving the same weights return the same vectors: 8 batched chunks of 3,200 chars, then 10 warm 120-char queries, on one or two endpoints | ms/chunk (indexing); query median and p95; minimum cosine between the two endpoints' vectors |
| `tests/speed.py` | SPEED | prefill and generation throughput for one model on one endpoint, measured the same way every time: 444-token prompt, 200 tokens generated, `cache_prompt` off, `ignore_eos` on, one warm-up discarded | prefill t/s and gen t/s from the server's own timings (median of the reps), plus VRAM in use where `nvidia-smi` exists |
| `tests/po.py` | PO-SYN (synthetic) | extract one invented purchase order (vendor, PO number, date, ship-to, currency, 8 line items with qty/unit price/amount, subtotal, freight, 8.1 % tax, total) into a fixed JSON shape; document and gold are generated together in code so they cannot disagree | **parse** (strict / fenced / wrapped / invalid / empty) reported apart from schema 10 keys, 5 header fields, 4 money fields to 0.01, items exact /8, and whether the reply's OWN numbers add up |
| `tests/ragq.py` | RAGQ-SYN (synthetic) | grounded answering from a supplied context: 10 questions over an invented 11-document makerspace corpus in the five shapes that matter — exact lookup, freshness (a stale sheet and a live one disagree), negative (the fact is absent), synthesis (two documents, one needs arithmetic), vague phrasing; each question ships only the documents it "retrieved" | correct /10 per question from a key of regexes that must / must not match (no model judges); a stale value counts only when the answer labels it old; per-shape counts; citations counted, not scored |
| `tests/po_real.py` | PO-REAL (real document) | extract a real published purchase order - a Texas HHS Commission PO (public record, personal contact details redacted) as `pdftotext -layout` renders it: interleaved columns, descriptions wrapped onto their own lines, repeated page headers, one stated "Not to exceed" total, 12 line items that each multiply out | **parse** apart from 8 schema keys, 4 header fields, line rows exact /12 (line label, class code, qty, UOM, unit price, amount), the schedule total, and the reply's own arithmetic (qty x price per line, amounts summing to the total) |
| `tests/ragq_real.py` | RAGQ-REAL (real documents) | grounded answering over four real public documents that form **two revision pairs**: 13 CFR 125.6 (CFR 2026 vs 2023 editions) and NIST SP 800-63B-4 vs -3 password verifiers; 15 questions in six shapes, three of them arithmetic over the rule (apply the 50%/85% limit to a contract value rather than quote the percentage), each shipping the retrieved chunk(s) labelled only with title and edition date - resolving which source is current is the model's job | correct /15 by a per-question key of regexes that must / must not match (no model judges); a superseded value counts only when labelled as old or tied to the case it still applies to; per-shape counts |
| `tests/cal/run.py` | CAL | code generation: the model writes 9 small JavaScript calendar helpers (`--extended`: 13), `node --test` grades each, a same-model critic diagnoses failures, up to 4 attempts; needs Node 20+; the answer key (`reference/*.js`) is not shipped (`--reference DIR` borrows failed dependencies and runs the gold selftest). Protocol in the script docstring: thinking off at 4500 tokens is the headline, `--think on --max-tokens 11000` the think arm | first-attempt pass per task (critic-independent, the clean number) and final pass after repairs; blocked tasks counted as fails; request errors reported, a quarter or more makes the run invalid |
| `tests/fim.py` | FIM | inline shell-command completion: 58 cases cut from 29 shell lines, two cuts per line (mid-word and three characters from the end), run through `/infill` (fim) and through `/v1/completions` (plain, the control) | first token must equal the missing remainder exactly; failures classified wrong / overlong / fenced / empty / echoed prefix; latency mean and p95 |

**Bring-your-own data for JUDGE, CRIT and SUM.** The lab ran these on a copyrighted YouTube transcript, which is not
included. `tests/fixtures/demo/` holds a short **invented** transcript, summary, fact list and plant specs so the
scripts run end to end; scores on it mean little. To measure something real, supply a transcript you may use (a
CC-licensed talk, your own notes), a fact list and a good summary: `--transcript`, `--gold`, `--plants`, `--facts`
(formats in each script's docstring). The scripts refuse to run if a planted edit's text isn't in your gold summary.

**Fixtures are synthetic but not byte-identical to the published runs.** RED and AUD were rebuilt with fictional company,
SKU, street and city names (the random draw and the graders are unchanged, so the counts of 78/72 planted items hold),
and the AUD grader now treats `1,420` and `1420.00` as the same number (the lab's grader split `1,420` into `1` and `420`). HRD
and the MON prompt are verbatim; the MON grader is stricter than the lab's (the lab also accepted `821.6`). The JUDGE/CRIT/SUM
demo data are invented stand-ins, not the lab's inputs. The OCR page was
widened (below). A model's score here can therefore differ from its row in `results.csv`.

## Patterns

`patterns/escalate.py`, a checker-gated escalation loop. A task is two functions: `build_messages(item)` and
`check(item, reply) -> (ok, parsed, why)`. The model only extracts; code decides. If `check` rejects the reply, the same
endpoint is retried with the rejection reason appended; after that the loop escalates to the next endpoint
(`ROLES=DEFAULT,ESCALATE`, with `BASE_URL_ESCALATE`, `MODEL_ESCALATE`, `API_KEY_ESCALATE`). The example task extracts
fields from synthetic classified ads and cross-checks the model's price against the record's own price field, the same
shape as the lab's rental-listing task, where the checker compares the model's answer to the listing's structured fields.
`python3 patterns/escalate.py --selftest` covers retry, escalation, total failure, a dead endpoint, and the env wiring.

`patterns/summarize_verify.py`, a summarize-verify pipeline adapted from the author's `summarize` tool: chunk, extract a
checklist per chunk, write once, check every claim in batches (the checker answers supported/unsupported/contradicted
**and must give a quote that code verifies is a substring of the source**; every number in a claim must occur in the
source), test each checklist point against the summary, repair once, and mark whatever still fails `[unverified]`.
Roles: `WRITER_MODEL` and `CHECK_MODEL` (both default to `MODEL`). **Read the findings before using it**: on a document
that fits one context window it did worse than a plain single pass ([§97](docs/excerpts/097-single-pass-vs-pipeline.md), [§104](docs/excerpts/104-summarize-bonsai.md)); `--single` runs the single pass.

## Findings

Sample sizes are stated per item. In general: one card, one build per section, mostly 1-3 runs per cell, and models of
4B parameters or less except where named. Treat these as measurements on this setup, not as rankings.

### Code generation (CAL): a local 35B MoE and a hosted model tie on nine tasks; four harder ones split them ([§124](docs/excerpts/124-cal.md))

13 calendar-helper tasks, thinking off, up to 4 attempts with a same-model critic (`tests/cal/run.py`). Qwen3.6-35B-A3B
(local) **9/13**, DeepSeek-V4.1-flash (hosted) **12/13**; both pass the nine originals on attempt 1. Sample: n=1 per
model; the local throughput for the last four tasks is withheld (unexplained slowdown), and the hosted
expandRecurrence failure shows zero critic calls, so it is not a clean read ([§124](docs/excerpts/124-cal.md)).

On the nine originals, a 4 GB-class local seat (ling3-tiny, nine tasks, 4500 tokens, one run) scored **3/9 final, 1/9 on
attempt 1**. n=1 with large variance: an earlier partial run of the same configuration passed two tasks this one
failed, and an older run on a previous harness revision scored 6/9 (see `docs/RESULTS-NOTES.md`).

### Context compression (synthetic transcript): best 4 GB seat keeps 1 of 7 facts after three rounds ([§125](docs/excerpts/125-compress-synthetic.md))

Seeded ~23.5k-token generated transcript, 7 planted needles, three compaction rounds, 3000-token output ceiling
(`tests/compress.py`). FrogNano-4B 1/7 kept; granite4-7B and lfm2 0/7 and overflow the ceiling in round 3; a small
helper seat overflows in round 1; two seats CUDA-OOM at load at 40k. 0 invented facts anywhere. Sample: n=1 per seat, one seed.

### Speculative decoding (DFlash drafter) is slower here: no-go ([§88](docs/excerpts/088-dflash-no-go.md))

`bolt-e4b` (UD-Q4_K_XL) with its DFlash drafter (BF16, 736,812,096 B) on the llama.cpp router, build 11889. Every drafter
cell was slower than its control. On the audit workload (thinking on, 32k): **3.2 t/s vs 13.8 t/s = 0.23x**, against a
decision rule of at least 1.3x. Chat at temperature 1.0: control 16.9 t/s (n=3) vs 3.1 t/s with draft n-max 7 (n=3,
acceptance 21-22%). JSON output reached 86-94% acceptance yet still decoded at 0.45-0.75x of control, so low acceptance is
not the whole story; the cause was not isolated. The memory fitter could not size the drafter, and loading needed
`fit-target = 2200` ([§88](docs/excerpts/088-dflash-no-go.md)). Sample: control n=3 per workload; drafter cells n=1 to 3 (the audit cell is **n=1**); one model,
one card. The vendor's 2.74x on an RTX 5090 did not transfer.

### Vulkan vs Ollama (CUDA) on Pascal ([§52](docs/excerpts/052-vulkan-vs-ollama.md))

Same GGUF (granite4 7B-A1B-H Q4_K_M), q8_0 KV, flash attention, ~1.28k-token prompt. Ollama 0.34.2 (CUDA 12): prefill
**479-499 t/s**, generation **30.9-35.8 t/s**, 90% of the model on the GPU. llama.cpp `2851b3b` on Vulkan at its best expert
split (`-ncmoe 4`): prefill 117.5 t/s, generation 28.7 t/s; with everything on the GPU it failed to load (1.05 GB buffer
allocation). So on this card Vulkan was about 4x slower at prefill. Sample: one model; `llama-bench` r=2; Ollama 3 runs.
Not tested: a llama.cpp **CUDA 12** build, which that section expected to at best tie ([§52](docs/excerpts/052-vulkan-vs-ollama.md)). The later move to llama.cpp CUDA in the
other sections is outside this comparison.

### A judge can see that a summary got worse; it names omissions poorly ([§96–§96.2](docs/excerpts/096-judge-calibration.md))

Judging a transcript summary (JUDGE: gold plus five broken copies: wrong parameter counts, three fabrications, a flipped
verdict, a truncated results section, an off-topic summary; 1 transcript, 3 runs, so 15 defect/gold pairs per model):

| judge (reasoning off) | defects scored below same-run gold | unparsed replies |
|---|---|---|
| gemma4 e4b | **15/15** (planted errors named: 20/24) | 0/18 |
| ling3-tiny | 10/15 | 3/18 (loops at temperature 0) |
| FrogNano-4B | 9/10 scored pairs | 5/18 |

(`results.csv`, JUDGE rows; [§96](docs/excerpts/096-judge-calibration.md).) For gemma4 the **omission** copy scored 3 in all three runs, the same as the wrong-numbers
copy, but its problem list named the omission in only **1 of 3** runs ([§96](docs/excerpts/096-judge-calibration.md)). With reasoning off, bolt-e4b named the inserted
fabricated line 6/6 times but the edited parameter count and the swapped company 0/6 each on gemma4's text ([§96.2](docs/excerpts/096-judge-calibration.md)). A score
that drops is a usable signal; the problem list is not reliable for what is missing. That is why `summarize_verify.py` checks
coverage in code instead of asking a model.

Self vs cross (CRIT, 6 real summaries x 2 runs, 3 plant types): gemma4 named **14/18** planted errors in FrogNano's
writing but **7/18** in its own; FrogNano named 17/18 in gemma4's writing vs 13/18 in its own ([§96.1](docs/excerpts/096-judge-calibration.md)). Thinking on changed
the picture: bolt-e4b with reasoning on named **18/18** with 0 unparsed replies, but at roughly 3x the time per call and
with **n=1 run (6 pairs)**, so "perfect" is a small sample ([§96.2](docs/excerpts/096-judge-calibration.md)). Whether the self-blindness is model family or writing
style was not separated. RED ([§96](docs/excerpts/096-judge-calibration.md), 12 synthetic documents, n=1 greedy): regex alone leaked 30/78 identifiers (all names and
an obfuscated email); gemma4 alone leaked 8/78 (digits) and regex+gemma4 leaked **2/78** with 72/72 keep-items kept; ling3
and FrogNano hybrids leaked 14 and 12, all names.

### Single pass vs the verify pipeline: single pass wins when the document fits ([§97](docs/excerpts/097-single-pass-vs-pipeline.md), [§104](docs/excerpts/104-summarize-bonsai.md))

| source | arm | facts covered | time | other |
|---|---|---|---|---|
| 27.7k-char transcript, 24-fact list ([§97](docs/excerpts/097-single-pass-vs-pipeline.md)) | single pass | **22/24** (10/10 must-have) | 89 s | 0 numbers absent from the source |
| | pipeline | 18/24 (9/10) | 713 s | 17 of 24 claims flagged |
| 35k-char transcript, 31-fact list ([§104](docs/excerpts/104-summarize-bonsai.md)) | single pass, 40k seat | **25/31** (19/19) | 124 s | |
| | pipeline | 18/31 (9/19) | 546 s | 17 flagged |
| 143.9k-char PDF ([§97](docs/excerpts/097-single-pass-vs-pipeline.md)) | single pass | truncated to 30k chars, 282 words | 65 s | no fact list |
| | pipeline | 1142 words over 35 chunks, 177/230 checklist points | 2127 s | 43 of 84 claims flagged |

The claim checker flags about half of a clean summary (17 of 24 here), so `[unverified]` means "look", not "wrong"; it is a good
detector of planted errors ([§96.1](docs/excerpts/096-judge-calibration.md)) with a high false-positive rate on clean text ([§97](docs/excerpts/097-single-pass-vs-pipeline.md)). The pipeline is the only arm that
summarised the PDF the single pass could not hold. **n is small:** one run per cell, one source per scoring regime, and the PDF
single-pass arm is a truncation, not a fair fight ([§97](docs/excerpts/097-single-pass-vs-pipeline.md), [§104](docs/excerpts/104-summarize-bonsai.md)). The transcripts behind the fact lists are not in this repo.

### Tool count: 4 tools vs 20 with a meta-dispatcher (TOOL, [§23.4](docs/excerpts/0234-tool-count.md))

A task as simple as "run `df -h` and report a value" was offered with 4 tools and then 20 tools that included a `tool_call`
meta-dispatcher. In `results.csv`, the "4 tools / 20 tools" format has 12 rows (11 distinct models; one model appears at 1 and 2 loops). **5 models
picked the right tool with 4 tools but not with 20** (granite3.3:2b, granite4.2:3b, granite4:3b-h, granite4:7b-a1b-h,
granite4:micro) and 6 passed both. In
the [§23.4](docs/excerpts/0234-tool-count.md) probe the failure was specifically choosing the meta-tool over `terminal` (3 of 4 models tested there), and the same
models called `terminal` correctly over both the native and the OpenAI-compatible API with 4 tools, so the wire path was not the
cause. Caveats from [§23.4](docs/excerpts/0234-tool-count.md): the section reports one result per model per path, so treat n as 1 per cell; the 20-tool column was run on
Ollama's native API only, so the 20-tool result over `/v1` (what `tests/tool_probe.py` measures) is new, not a replication; the
probe explains the wrong-tool failures but not the "emitted the call as plain text" ones. Other TOOL rows in the CSV (e.g. `12/12`) use a different
scoring and are not comparable. `tests/tool_probe.py` runs the 4-vs-20 comparison over `/v1` only.

### Reranker: the micro-batch size is a hard input ceiling ([§40.1c](docs/excerpts/0401c-reranker-ubatch.md))

On the CPU (not the GPU) with `llama-server --rerank` and `bge-reranker-v2-m3-Q8_0` (636 MB): the server clamps `n_batch =
n_ubatch`, and any single document longer than `ubatch` returns HTTP 500. Measured: ubatch 768 failed on both a ~937-token
document and a real ~2,171-token chunk; 1024 and 2048 passed the short document but failed the 2,171-token chunk; **4096 passed
both**. The shipped value was `-b 4096 -ub 4096` for about +250 MB. Size ubatch from your largest chunk, not from the flag
default ([§40.1c](docs/excerpts/0401c-reranker-ubatch.md)). Sample: one model, two document sizes; repeat counts per cell are not stated in the section.

### A cloud model can improve a local model's prompt while seeing counts only (PTUNE, [prompt-tune](docs/excerpts/prompt-tune.md))

Two runs of the prompt-tune loop on the payee-categorisation task: cloud writers (glm-5.3-flash,
deepseek-v4.1-flash) read a gateway report of counts only and rewrote the categoriser's prompt; the local
seat (gemma4:e4b) scored every candidate against 130 tune keys, and the last 91 keys were held out and
scored once. Holdout score (right − 2×wrong): baseline 11 → **26** (glm writer) and 11 → **20**
(deepseek writer), but the paired sign tests give p = 1.00 and p = 0.55 — the gain is abstention, not
better answers (wrong 20 → 12 and 20 → 14, right flat or down). The one finding that transferred: the
deepseek-written prompt made the local seat fail to answer on **33 of 91** holdout keys (vs 3), because
it is longer and the small seat fails or mis-parses more on longer prompts — prompt length has a cost on
a 4 B model even when the text is better. Author's task set, one holdout pass per arm, one local model;
code, synthetic-data tests and run records: github.com/Sjbrimley26/prompt-tune.

### Payee categorization on an invented fixture: abstaining matters more than the right-count (CAT, [§110](docs/excerpts/110-cat-synthetic.md))

200 invented payees (seed 20261114; **synthetic, not the author's bank data**), 14 categories or UNSURE, 33 rows with no knowable answer. One run per model, temp 0 where the
preset sets it, output cap 256 then 512, no schema. FrogNano-4B is the best single model (160 right, 5 wrong, 2 false fills); ling3-tiny answers everything and
makes 30 false fills; the thinking models (bolt-e4b with thinking on, nemotron-3-nano) abstain on all 33 but one of them needs 21 minutes; granite4.2:3b and qwen3.5:4b spiral to the cap
and are unusable here (12 and 5 right). A json_schema arm (two models, first 100 rows) removed the false fills but ran about 50x slower. A quorum-2 vote of nine models (150 right,
8 false fills) did not beat the best single model. **How it differs from the private run:** that run used about a hundred real rows scored against the author's own key, so counts are
not comparable; the rankings do **not** agree (FrogNano mid-pack there, first here; granite4-micro near the top there, last here) but the failure-mode shape does (the answer-everything
model pays in wrong answers or false fills in both; the abstaining models have the low wrong shares; full agreement is precise but narrow). Treat it as a method check, not a leaderboard
([§110](docs/excerpts/110-cat-synthetic.md); `results.csv` CAT-SYN and CAT-SYN-SCHEMA rows).

### Helper-model jobs of an agent on generic cases: the safety check and the real-data check disagree (AUX, [§111](docs/excerpts/111-aux.md))

27 flagged shell commands, 12 goal verdicts, 6 task specs and 31 session titles, all invented, with Hermes' own prompts verbatim (MIT, attributed). One run per seat, temp 0, reasoning off.
ling3-tiny approves no destructive command and passes triage 6/6 but is mid-pack on titles (26/31); granite3.3:2b approves a disk format and a recursive `chown` to root on a home directory and returns invalid JSON on all 6 triage
specs; bolt-e4b and gemma4:e4b (text only) lead the approval lane on exact matches but both approve `mv /etc/passwd /etc/passwd.bak`. **How it differs from the private run:** the author also ran the approval
and title lanes on 400 real flagged commands and 150 real session openers (aggregates only, nothing published). Those approval labels were mostly proposed by an earlier model-assisted review and contain no
deny rows, so the real set measures over-denial, not safety; the title score is stricter than the agent's own acceptance rule. The rankings **do not agree**: on real commands bolt-e4b (337/400) and gemma4:e4b (327)
lead and ling3 (243) and FrogNano (234) trail by denying a third of harmless commands, while here ling3 and FrogNano are the ones that never approve a destructive command. ling3 and granite3.3 do reproduce their
hand-written-case results exactly. No seat passed both checks, so none is shown fit to replace a strong model for approvals; for titles, triage and the goal judge a small model is adequate. One run per seat:
treat one- or two-case gaps as noise ([§111](docs/excerpts/111-aux.md); `results.csv` AUX-SYN rows).

### Text-to-SQL on an invented hardware store: the lab's ranking mostly holds, and the reply format matters (SQL-SYN, [§112](docs/excerpts/112-sql-synthetic.md))

50 questions on an invented 10-table SQLite database (seed 20261115; **synthetic, not the author's purchasing database**), gold verified by execution, one run per model at temp 0, cap 700.
Scored twice: *lenient* (cut the query out of the reply) and *strict* (the reply must be the query). bolt-e4b with thinking off scores 44/50 in 78 s; granite4:micro, gemma4:e4b, ling3-tiny and bolt with thinking
on are at 42-43; granite3.3:2b is last at 24. qwen3.5:4b is 45 lenient but 37 strict (8 fenced replies), takes 1220 s, and loses three answers to the token cap. The hard kinds are NULL handling (58%), dates
(58%) and ties (50%), not quoted column names (91%). **How it differs from the private run:** that run used the author's real catalog and a larger schema; on the seven models in both, Spearman is 0.74 (15 of 21 pairs ordered
the same): the top and the bottom hold, ling3-tiny is the one big disagreement (43 here, 35 private), and the top four are inside two points. A one-word change in a sample row moved three models by 2-4 points between two
runs, so read the order inside that band as noise. n-gram speculative decoding (`ngram-mod` 8/4/16) gave bolt no speed-up on SQL (16.13 vs 16.23 tokens/s; replies about 23 tokens)
([§112](docs/excerpts/112-sql-synthetic.md); `results.csv` SQL-SYN rows).

### Rental-listing extraction on 100 invented listings: the order holds, the checker saturates, thinking buys precision (RENT-SYN, [§113](docs/excerpts/113-rent-synthetic.md))

100 invented rental listings in 16 tiers (seed 20261115; **synthetic, not the author's private set of 23 live, hand-labelled listings**), gold known by construction. The model returns strict
JSON (rent, beds, baths, kind, pets, town, red_flags); the production checker rejects malformed or self-contradicting replies. Eight arms, one pass each at temp 0. bolt-e4b with a
256-token thinking budget is best (85/100 on all fields plus exact flags, flag precision 0.81, 19.6 s/item); gemma4:e4b with thinking is 83; with thinking off both run at ~3.7 s/item and
score 76 (gemma4:e4b) and 67 (bolt-e4b). Thinking removes spurious flags (bolt 29 to 8) but not missed ones, and 26 of bolt's 29 thinking-off false flags are one prompt-echo ("no viewing").
FrogNano (48, pets 75), the two granite models (45, 22) and ling3-tiny (16; 235 spurious flags) trail. **How it differs from the private run:** on the five models in both, the order
agrees (bolt and gemma4:e4b tie on pets, FrogNano weak on pets, ling3-tiny last and inventing flags, thinking-on bolt on top), but the synthetic checker is saturated (100/100 for three models;
the private one separated them at 21/23 vs 15/23), pets is easier (94-97% vs 86%), and the private finding that thinking lowers pets is not reproduced. granite4.2 and LFM3 are not measured
(the GPU dropped off the bus mid-sweep). One pass per arm: read one to three listings as noise ([§113](docs/excerpts/113-rent-synthetic.md); `results.csv` RENT-SYN rows).

### An embedding server off the card: 7.4x slower to index, the same for a query (EMBED, [§114](docs/excerpts/114-embed-device.md))

Same weights (`bge-m3`, 1024-dim) served twice on one machine: once with no GPU device, 3 threads, and
once all-layers on the 4 GB card. Eight synthetic chunks of 3,200 characters in one batched call, then
ten warm 120-character queries:

| device | indexing | ms/chunk | warm query median / p95 | VRAM |
|---|---|---|---|---|
| CPU | 27.83 s for 8 chunks | **3,478.4** | 0.136 s / 0.154 s | 0 |
| GPU | 3.74 s for 8 chunks | **467.4** | **0.049 s** / 0.059 s | 633 MiB |

The vectors are the same: minimum cosine **0.999990** across the eight chunks, so an index built by one
device is queryable by the other and the split is reversible. Indexing is **7.44x** slower on the CPU; an
earlier measurement on the previous serving stack read 4.5x, and the difference is the CPU thread count
and the batched path, not the weights. The decision it supports is unchanged (a batch import is minutes
either way, a query is milliseconds either way), but the cost of a full re-index on the CPU is larger
than was previously stated. One run per device, one batch size, one card; cold load and co-residency with
a generation model are not measured ([§114](docs/excerpts/114-embed-device.md); `results.csv` EMBED rows).

### Speed on one card: MoE beats dense while spilling, and one projector costs 11x (SPEED, [§115](docs/excerpts/115-speed.md))

Every installed model was re-measured on the current build with one method: a 444-token prompt, 200 tokens
generated, temperature 0, prompt cache **off**, `ignore_eos` **on**, one warm-up discarded, two reps, the
card emptied between models and VRAM read from `nvidia-smi`. Prompt caching is the trap that matters: left
on, the second rep reports prefill over the 1-4 tokens that were not cached, and `granite3.3:2b` reads
33.4 t/s where the honest figure is 529.7.

| model | ctx | VRAM MiB | prefill t/s | gen t/s |
|---|---|---|---|---|
| LFM3-1.7B | 32768 | 1441 | 791.5 | **45.2** |
| Moon-Beam-8B-A1B | 32768 | 3975 | 530.2 | **37.4** |
| lfm2-8b-a1b | 65536 | 3985 | 539.0 | **36.5** |
| MiniCPM5-2B | 32768 | 2215 | 643.5 | **35.9** |
| ling3-tiny-64k | 65536 | 4007 | 542.1 | **34.2** (27.0-41.4) |
| granite3.3:2b | 8192 | 1957 | 529.7 | **34.0** |
| granite4:3b-h | 32768 | 2249 | 333.9 | **26.9** |
| granite4:7b-a1b-h | 65536 | 3993 | 369.9 | **24.2** (19.7-28.7) |
| gemma4:e4b (text) | 16384 | 3551 | 463.2 | **17.4** |
| gemma4:e4b + projector | 16384 | 3647 | 195.6 | **1.5** (1.2-1.8) |

Three things worth taking from it. **MoE offload is cheap in the direction people fear:** an 8B-A1B at 97 %
of a 4 GB card (Moon-Beam 37.4 t/s, lfm2 36.5) beats every dense 3-4B in the table. **The fastest seat has
the smallest active set:** LFM3-1.7B does 45.2 t/s in 1.4 GB. And **a projector is not a fixed tax:**
`gemma4:e4b` reads 17.4 t/s as text and 1.5 t/s with its projector, while `Qwen3.5-4B` reads 18.3 either
way, so the cost is that model's projector not fitting beside its weights, not vision as such. The full
24-row table, with min/max per model, is in [§115](docs/excerpts/115-speed.md); `gemma3:4b` and
`ministral-3:3b` could not be re-measured (their weights will not load on this build) and are listed as not
measured rather than carried over.

### Inline shell completion: native FIM is worth 0.12-0.31, and the seat is the 1.5B (FIM, [§116](docs/excerpts/116-fim.md))

58 synthetic cases cut from 29 shell lines, two cuts per line, one of them three characters from the end of a
word. The model must return exactly the rest of that word; the first token is compared, so `oads/` for `oads`
fails. Two arms: `/infill` (fill-in-the-middle) and plain `/v1/completions` as the control.

| model | fim | plain | fim latency mean / p95 |
|---|---|---|---|
| qwen2.5-coder-1.5b Q8_0 | **27/58 (0.466)** | 12/58 (0.207) | **114 ms / 142 ms** |
| granite4:3b-h | 24/58 (0.414) | 6/58 (0.103) | 272 ms / 529 ms |
| qwen2.5-coder-0.5b Q8_0 | 17/58 (0.293) | 10/58 (0.172) | **66 ms / 63 ms** |

Every model gains from native FIM (+0.26, +0.31, +0.12), which is the finding: whether the weights were
trained for `/infill` matters more than the parameter count. The 1.5B is the seat at 27/58 in 1.65 GB with a
p95 of 142 ms. The 3B matches it within three cases and blows the latency budget at a 529 ms p95, and the
0.5B halves the latency for 17 points of accuracy. An earlier run of this test used a fixture that was 31 %
the owner's shell history, which no model could complete and which flattened the ranking (every model scored
0.20-0.27 there); this one is all synthetic and the order is cleaner, so the two runs are not comparable and
the older numbers are not republished ([§116](docs/excerpts/116-fim.md); `results.csv` FIM rows).

### The fastest model on the card is the weakest on every scored lane, and the output cap explains most of it (LFM3-1.7B, [§117](docs/excerpts/117-lfm3-round-2.md))

LFM3-1.7B leads the speed table (45.2 t/s generation) and holds that rate to a 262k window for ~2.1 GB of KV, but it scores last or near-last everywhere else:
CAT-SYN 64 right / 0 wrong / 103 blank with thinking on and 11 / 0 / 156 with it off (best in field 160); SQL-SYN 24/50 lenient and 0/50 strict, because every usable
answer arrives inside a `sql` code fence; RENT-SYN 27/100 on all-fields (gemma4:e4b is 76); approval 0/27 — all 27 replies empty — with thinking on, and 19/27 with three
dangerous commands approved with it off; triage 0/6 strictly and 18/18 once `## Goal` is accepted as the heading the prompt asks for as `**Goal**`. The mechanism is the
model's reasoning trace: on short lanes it fills the output cap and the answer never arrives, and on a long summarisation it either fills `content` with an unterminated
trace or leaves `content` empty, so no deliverable comes back in either arm at 1500 or 3000 tokens. `enable_thinking:false` silences it on structured lanes only.
It does well on one axis: asked four answerable and two unanswerable questions over three invented documents it answered 4/4 and declined both that were absent.
**How it differs from the private run:** the private SQL50 pass (16/50 and 26/50 for this model) is the same verdict, and round 1's 0/18 on the triage lane is the
same heading-format artifact seen here, which is why that score is published with the strict contract and the H2 note in the same row. The window ladder and the
refusal probe are in the excerpt and the notebook, not in `results.csv` — they are not ported tests. One run per row, temp 0, vendor sampling scored separately
(17/18 vs 18/18 on the triage cases): ([§117](docs/excerpts/117-lfm3-round-2.md); `results.csv` CAT-SYN, SQL-SYN, RENT-SYN and AUX-SYN rows).

### Spark-X2.6-4B: the best short-problem scores here, and the weakest audit (SPARK, [§118](docs/excerpts/118-spark-x2-6.md))

Spark-X2.6-4B (4.45B dense, Apache-2.0) had private round-1 records and only the §115 speed row here, so
this pass ran the portable set once: **HRD 9/10 at temperature 0 and 8/10 at 0.7** (joint best in the table), the money problem to the cent
in **11.3 s**, **44/50 on SQL-SYN lenient and strict** (no fenced replies, joint best), the right tool with 4
and with 20 tools, CAT-SYN **145 right / 17 wrong / 5 blank** with 3 false fills, and AUX-SYN **23/27 exact
approvals and 25/27 safe-direction — with one dangerous approval (`chown` to root)**, which disqualifies it as
a gate however good the rest of the lane is. It is slow at **19.3 t/s generation / 299.7 t/s prefill in
3,713 MiB** and weak in two places: rental extraction at **32/100 all-fields-plus-flags** (`pets` 50; buried
"no dogs" 0/6), and redaction at **26/78 model-only, 15/78 hybrid**. The audit result is the one worth
reading: on the document that plants a wrong total, two of three runs answered "no inconsistencies" and both
asserted that `120 + 85 + 95 = 330`, while the third wrote the sum out, saw 300, and flagged it. The same
model computes a word problem correctly to the cent; it does not re-check a figure that arrives inside a
document. Thinking is budgetable (`reasoning_budget_tokens: 256` gives 582 reasoning characters against 1,547
unbounded) and it paid on the rental lane — **56/100 all-fields and pets 72 at 17.5 s/item**, against 32
and 50 thinking off — but unbounded thinking ran a single HRD problem to 7,250 generated tokens with no
answer at a 9,000-token cap, so the HRD arm was aborted rather than scored. One run per row (HRD per temperature, AUD
n=3), temp 0 rather than the vendor's temp 1.0, one card, one build, one quant
([§118](docs/excerpts/118-spark-x2-6.md); `results.csv` rows with §118 in their notes).

### A real purchase order: five seats read it perfectly, three misread every column (PO-REAL, [§120](docs/excerpts/120-real-documents.md))

The synthetic PO is passed by every seat, so it measures formatting, not extraction. This one is a real Texas
HHS Commission purchase order — public record, personal contact details redacted — read from its
`pdftotext -layout` rendering: columns interleave, descriptions wrap onto their own lines, page headers repeat,
the vendor's tax ID is printed above its name, and the only total on the page is a single "Not to exceed" line.
Twelve line items, each of which multiplies out exactly, and the schedule sums to that total, so the gold is
arithmetic rather than opinion. Thirteen seats, one run each, temp 0, thinking off.

| what happened | seats |
|---|---|
| **12/12 items, all header fields, the total and the arithmetic** | granite4:3b-h, minicpm5-2b, gemma4:e4b, bolt-e4b, Spark-X2.6-4B |
| 12/12 items, but a header field wrong | ling3-tiny-64k (took the vendor's **tax ID** as the vendor), granite4:micro, Moon-Beam-8B-A1B |
| 11/12 items | LFM3-1.7B-QAD |
| **0/12 items** — every price column read one place to the left | granite3.3:2b, FrogNano-4B, granite4.2:3b |
| no JSON at all (`finish=length`) | LFM3-1.7B-Q4_K_M |

The failure is not arithmetic and not omission: the three 0/12 seats produced twelve rows with the right line
labels, descriptions and class codes, and put the quantity where the unit price goes (25.34 vs 102.00), so
`qty x price = amount` fails on every line while the stated total still comes out right. That is what a
real interleaved table does to a small model, and it is invisible on a generated one. 10-58 s per document.

### Real documents, two revision pairs: lookups are free, applying a rule is not (RAGQ-REAL, [§120](docs/excerpts/120-real-documents.md))

Four real public documents, arranged as two revision pairs — 13 CFR 125.6 from the **2026** and **2023** CFR
editions (the 2026 text adds a per-order compliance rule for multi-agency set-asides, and a past-performance
section the 2023 text lacks), and NIST SP 800-63B-**4** (2025) against -**3** (2017), where the single-factor
password minimum went from 8 to 15 characters. Fifteen questions in six shapes; each ships the retrieved
chunk(s) labelled only with title and edition date, so working out which source is current is the test. One
run per seat, temp 0, thinking off, ~1.9k-token prompts.

| score | seats |
|---|---|
| **15/15** | FrogNano-4B-2609-IQ4_XS, LFM3-1.7B-QAD |
| 14/15 | granite4:micro, gemma4:e4b, granite4.2:3b |
| 13/15 | minicpm5-2b, granite4:3b-h, LFM3-1.7B-Q4_K_M, Spark-X2.6-4B |
| 11/15 | ling3-tiny-64k, granite3.3:2b, bolt-e4b |
| 10/15 | Moon-Beam-8B-A1B |

Eight of the fifteen questions are answered by **all thirteen** seats: the three exact lookups, the blocklist
question, both negatives, the three-percentage enumeration, the 2017-vs-2025 freshness pair and the
full-and-open-order exemption. Everything that separates the seats is one of two things:

* **applying a rule to a number** — "a $4,000,000 general-construction contract pays $3,200,000 to
  non-similarly-situated firms: compliant?" needs the 85 % cap computed and compared, and **6 of 13** get it
  wrong (three by answering "not compliant" after computing 80 % < 85 %);
* **multi-clause synthesis** — the CFR past-performance paragraph (5 of 13 fail) and the NIST composition-rule
  change (4 of 13), both of which need two statements from the same passage combined.

The ranking is not by size: the two perfect scores are a 4B (FrogNano) and a 1.7B (LFM3-QAD), and the 8B
Moon-Beam is last. Read the gaps of one or two questions as noise (15 items, one run, one card, regex keys).

## `results/results.csv`

194 rows. 192 of them are regenerated by `scripts/export_results.py` from the lab's source CSV (which is not
in this repo) and then rewritten by `scripts/sanitize_results.py`:

| test | rows | | test | rows |
|---|---|---|---|---|
| RED | 4 | | CAT-SYN-SCHEMA | 2 |
| AUD | 16 | | RENT-SYN | 11 |
| MON | 12 | | SQL-SYN | 12 |
| HRD | 5 | | AUX-SYN | 8 |
| TOOL | 21 | | PO-SYN | 2 |
| JUDGE | 3 | | PO-REAL | 13 |
| CRIT | 4 | | RAGQ-REAL | 13 |
| SUM | 16 | | | |
| OCR | 4 | | | |
| CAT-SYN | 14 | | | |

The two remaining rows are hand-added `PTUNE` rows from the prompt-tune runs (see
[prompt-tune.md](docs/excerpts/prompt-tune.md)). The `EMBED`, `SPEED`, `FIM` and `FIM-PLAIN` rows
([§114](docs/excerpts/114-embed-device.md), [§115](docs/excerpts/115-speed.md),
[§116](docs/excerpts/116-fim.md)) and the `Spark-X2.6-4B` set
([§118](docs/excerpts/118-spark-x2-6.md)) are inside that export now: the lab CSV carries them, so a fresh
export reproduces them rather than losing them.

**Where a row comes from.** The lab's own `results/results.csv` is the primary record for every test the lab
runs, including the ones executed with this repo's harnesses (`tests/cat.py`, `tests/rent.py`, …). This file is
the published copy: a row is copied here only after the lab row exists, and only if it can be published. A row
here with no lab-side counterpart is a bug, not a variant — say so instead of editing around it.

Added columns: `build`, `quant`, `n_runs`, `n_items`; each is filled only where the source row
or its section states it, otherwise blank. **A blank means "not stated in the source", not 1.** `n_runs` is blank on
57 of the 80 matrix rows and `build` on all of them, because the per-row notes do not state them.
The machine (GPU, VRAM, CPU, KV cache) is in the hardware block above and is not repeated per row; where serving
backend is not already evident from a row's notes, the notes say so (for example `backend: llama.cpp.`).
Scores keep the source's markup (`**bold**`, footnote letters).
`section` is the lab's **test-matrix** section the row came from (`2`, `2.2`, `3`) — not a notebook `§`; blank on the two
PTUNE rows, which were not run through that matrix. Notebook
references appear inside `notes` as `(§96)` and point at the excerpts in `docs/excerpts/`. The `notes` column's lab seat
names were rewritten to role words by `scripts/sanitize_results.py`; the mapping, the sample sizes and the fixture caveats
are in `docs/RESULTS-NOTES.md`.
Rows were measured at different dates, on different builds and quantisations, through Ollama or llama.cpp; read the `notes`.
The OCR rows carry a caveat (below); one of the four is a pointing/coordinates result that this repo does not port.

**OCR page, a fixture bug found while porting.** The lab's original invoice page was 1240 px wide and its long lines ran off
the right edge, clipping the amount column and the last digit of the planted total; the lab's own addendum of 2026-11-12
confirms the "missed the total" column measured the canvas, not the models ([§4.10](docs/excerpts/0410-vision-shootout.md)).
The lab's log shows 4 of 5 models missed exactly `total` (the fifth missed everything). `tests/ocr.py` draws a wider page
(1400 px; the lab redrew its own at 1360). The 4/5 rows in `results.csv` appear to come from the clipped-page run (the CSV
does not link a row to a script), so read them as unconfirmed.

## Not ported

Only tests whose fixtures are synthetic or computed in code were ported. The rest of the lab's tests were left out because their
fixtures are private, because they have not been audited for publication, or because they need infrastructure that is not in this
repo (an agent harness, a knowledge base, a catalogue). Their rows are not in `results/results.csv`.

## Layout

```
patterns/   escalate.py, summarize_verify.py
tests/      red.py aud.py mon.py hrd.py tool_probe.py judge.py crit.py sum_test.py ocr.py cat.py arena.py, common.py, smoke.sh, fixtures/
mock/       mock_server.py (stdlib OpenAI-compatible stand-in)
results/    results.csv, raw/ (your run records land here)
scripts/    export_results.py, sanitize_results.py
docs/       RESULTS-NOTES.md, SEATS.md, excerpts/ (one sanitised excerpt per cited notebook section)
```

## License

MIT, see `LICENSE`.
