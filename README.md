# small-gpu-bench

Real tasks on a 4 GB Pascal card: what works, what doesn't, and the patterns that make small models usable.

This repo holds the portable parts of a lab's small-GPU LLM benchmark work:

- **`tests/`**: a set of tests with synthetic fixtures (redaction, planted-error audit, arithmetic, tool selection,
  LLM-as-judge, OCR) that run against any OpenAI-compatible endpoint.
- **`patterns/`**: two stdlib-only building blocks: a **checker-gated escalation loop** and a **summarize-verify
  pipeline**.
- **`results/results.csv`**: the lab's recorded scores for exactly those tests (80 rows), plus two
  `PTUNE` rows from the prompt-tune runs (82 total; see
  [prompt-tune.md](docs/excerpts/prompt-tune.md)).
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

## `results/results.csv`

110 rows: for the ported tests only (RED 3, AUD 15, MON 11, HRD 4, TOOL 20, JUDGE 3, CRIT 4, SUM 16,
OCR 4, CAT-SYN 11, CAT-SYN-SCHEMA 2, AUX-SYN 5, SQL-SYN 10 — 110 rows, regenerated by `scripts/export_results.py`
from the lab's source CSV (which is not in this repo), plus two hand-added `PTUNE` rows from the prompt-tune
runs (see [Findings](#findings) and
[prompt-tune.md](docs/excerpts/prompt-tune.md)); `export_results.py` does not regenerate those two.
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
