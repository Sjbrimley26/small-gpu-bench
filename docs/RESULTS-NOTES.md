# Notes on the published results

Two things in this repo need explaining: how the lab's internal names were taken out of `results/results.csv`,
and how to read the columns (especially `section`) without over-reading them. The cited notebook sections
themselves are in `docs/excerpts/`.

## Where a row comes from

`results/results.csv` here is the **published copy**. The lab keeps its own `results/results.csv` as the primary
record for every test it runs — including the runs made with this repo's harnesses — and a row is copied into
this repo only after it exists in the lab file, and only when it can be published. The ported matrix rows were
produced by `scripts/export_results.py` from that lab file and rewritten by `scripts/sanitize_results.py`; rows
measured after the port (EMBED, SPEED, FIM, the §117, §118 and §120 runs — the §120 runs include the two
tests added with them, PO-SYN and RAGQ-SYN) were added by hand under the same rule.
A row that appears here with no lab-side counterpart is a bug in the record, not a variant of it.

## Before a push: commit first, then scan

`scripts/check_public.sh` reads `git ls-files`, so files that are still untracked (new `results/raw/` records,
a fresh fixture) are not scanned. Running it before `git add` reports a clean tree that a fresh clone will
fail on. The order is: `git add` the new files, run the scanner, then commit. Two §114 `EMBED` records did
exactly this in reverse and shipped a `base_url_b` field naming the lab's router endpoint until a clone caught
it (fixed in 77f3cd3); records written by `common.save_results` never carry it, because it skips `base_url`.

## Sanitising the seat names

The lab calls its server presets "seats". The exported CSV's `notes` column carried those names, which are
internal shorthand and meaningless outside the lab. `scripts/sanitize_results.py` rewrites them to role words:

| in the lab's CSV | in this repo | occurrences in this export |
|---|---|---|
| `frog-agent`, `frog-agent-16k/32k/40k/64k` | `checker preset` (the context suffix is kept: `checker preset (64k)`) | 5 |
| `vision-text`, `vision-text seat` | `writer preset` | 2 |
| `doc-audit` | `audit preset` | 2 |
| `bolt-agent`, `bolt-agent-16k/64k` | `audit preset (no-think arm)` | 1 |
| `bolt-ng-*` | `audit preset (no-think arm)` | 0 (rule kept for future exports) |
| `bolt-df-*` | `drafter arm` (+ its `n-max`) | 0 in the CSV; used in `docs/excerpts/088-dflash-no-go.md` |
| `gemma26` | `26B remote seat` | 0 — the 26B rows carry the model name `gemma-4-26B-A4B-it-ARA-heresy`, which is public and stayed |
| `sum-granite42`, `sum-granite7b`, `sum-granitemicro`, `sum-lfm2`, `sum-llama32`, `router seat sum-*` | `summary preset` | 5 |
| `aux seat`, `aux seat ling3-tiny-64k` | `aux preset`, `aux preset (64k)` | 3 |
| `Ex-aux seat` | `Former aux preset` | 1 |
| `router :<port>` | `local router` | 1 |
| `doc-seat-bakeoff-lc protocol`, `doc-seat-bakeoff-grade` | `document-audit protocol`, `the document-audit grader` | 2 |

What the script does **not** touch:

- **Model names.** `gemma4:e4b`, `bolt-e4b`, `FrogNano-4B-2609-IQ4_XS`, `granite4:3b-h` and the rest are public
  and are the substance of the results. The `model` column is unchanged.
- **Numbers.** No score, count, timing, percentage, temperature or context size changed. The `passed`, `total`, `metric_value`,
  `n_runs` and `n_items` columns are byte-identical. The constant hardware columns (`gpu`, `vram_gb`, `cpu`), the
  all-`installed` `status` column and the all-blank `kv_type` column were folded out of the export (the machine is
  the hardware block in the README); where a row's serving backend was not already evident from its notes, the
  export prepends `backend: llama.cpp.` to the notes.
- **Shape.** 82 rows, 14 columns, same order, same quoting style. (The original export had 80 rows and 20 columns; the
  two hand-added PTUNE rows came later, and the constant hardware/`status` and blank `kv_type` columns were folded
  out of the file — see the numbers note above.)

What the role words mean in practice — what a "seat" is, what each role's job is, and how the card is
leased — see [SEATS.md](SEATS.md).

The script is idempotent: it matches seat names, not their replacements, so a second run reports
`nothing to do`. `python3 scripts/sanitize_results.py --check` exits non-zero if the published file ever needs
sanitising again (for instance after regenerating it with `scripts/export_results.py`); `--diff` shows the change
without writing.

## What the `section` column means

`section` is the **test-matrix section** of the lab's results matrix (values here: `2`, `2.2`, `3`), i.e. which
block of the matrix build the row came from. It is **not** a notebook `§` number and must not be read as one.
Notebook section references appear inside `notes`, in the form `(§96)`, and point at the private notebook — the
ones the README quotes are excerpted in `docs/excerpts/`.

## Where n is small

Stated plainly because several cells are one run:

- **RED** — 12 synthetic documents, 78 identifiers, 72 keep-items, **n=1 at greedy** per model. One format family;
  real statements carry payee strings these fixtures do not.
- **AUD** — the FrogNano row is n=3 per document; other AUD rows are single scores, and the section behind them
  reports one result per model per document.
- **JUDGE** — one transcript and five hand-built broken copies, 3 runs each, so **15 defect/gold pairs per model**
  (FrogNano: 10 scorable pairs after 5 unparsed replies).
- **CRIT** — 6 real summaries × 2 runs = 12 items per cell, three plant types, one source transcript. The bolt
  thinking-on cell is **n=1 run (6 pairs)**.
- **SUM** — the §97/§104 A/Bs are **one run per cell**; several CSV rows are explicitly 1-run anecdotes (the
  `1 run (anecdote)` notes), and one `ling3-tiny:q4_K_M` row is byte-identical to `ling3-tiny-64k` (same text, not
  a second measurement).
- **TOOL** — the `§23.4` probe reports one result per model per path; the 20-tool column was run on Ollama's
  native API only, so `tests/tool_probe.py`'s `/v1` 20-tool result is new, not a replication.
- **OCR** — 5 planted fields, single run per model; see the fixture caveat below.
- **Reranker (§40.1c)** — one model, two document sizes; repeat counts per cell are not stated.
- **EMBED** — one run per device, one batch size (8 x 3,200 chars), one text source generated from a
  seed, one card. The query figure is the median of 10 warm calls with a p95 beside it; a cold load and
  what the GPU copy does when a generation model is already resident are not measured.
- **SPEED** — two reps per model after one discarded warm-up, on an emptied card. The min and max of the
  two reps travel with every row because the spread is real (`ling3-tiny-64k` ranged 27.0 to 41.4 t/s).
  444-token prompt, 200-token output: a long-context prefill is a different measurement and is not
  reported. Two models could not be re-measured at all (their weights fail to load on this build) and
  are marked not measured rather than carried over from the older table.
- **CAL** — one run per cell. The `ling3-tiny` row (nine tasks, 3/9 final, 1/9 on attempt 1) is a single run on the
  portable `tests/cal/run.py`; an earlier unrecorded partial run of the same configuration passed two tasks this one
  failed, and the lab's ollama-era run of the same model scored 6/9 on the same nine (earlier harness revision). Read
  it as "somewhere between 3/9 and 6/9", not a point estimate. The first-attempt column is the clean one.
- **VISION** — one run per image set, three sets of four images, 8 questions / 10 points each, 30 points in all,
  200 output tokens. A one-point difference is not a ranking, and on the first set alone the same field read
  10/9/9/8 while on 30 points it is 29/28/28/28/17: each seat's single miss is a different question, so the order
  inside the leading group is unresolved at n=1. Empty replies from the thinking seat are budget exhaustion
  (13 of its 13 lost points), not misreads; re-asking just those 11 questions with a 600-token budget recovered 7
  of their 13 points and still produced no output at all on 5 of them, so the budget is only half the story. That
  seat is also the slowest. Wall time is dominated by thinking
  tokens on two seats, so it is not an image-encode cost. Whole-card VRAM includes the resting FIM server.
  The text-only control (HTTP 500 on every image) is shown in the excerpt, not as a CSV row.
- **FIM** — 58 cases cut from 29 lines (two cuts each), one pass per arm, one card. Two cases is noise.
  The plain arm cannot see the suffix, so it is a floor, not a fair fight; the containers start
  immediately before the test, so the first cases carry cold-load latency.
- **CMP-SYN** — one seed (7), one run per seat, three rounds. "Kept" per round is out of the needles planted so
  far (3, 5, 7), so the headline is round 3 out of 7. `overflow` (summary ran past the 3000-token ceiling) is a
  result, not a harness fault; two seats CUDA-OOM at 40k on this card and have no row (stated in the excerpt).
  Rows come from `scripts/raw_to_rows.py` over `results/raw/CMP-SYN-*.json`, not from the lab export. Not
  comparable to the lab's private-transcript ladder (§85, §89).

## Fixture reproducibility

The ported tests are not byte-identical to the lab's fixtures, so **a run of this repo will not reproduce
`results/results.csv`**. `results.csv` is the lab's record of what those models did on that machine; the scripts
here are the same tests rebuilt to be publishable.

- RED and AUD were rebuilt with fictional company, SKU, street and city names. The random draw and the graders are
  unchanged, so the counts (78 planted identifiers, 72 keep-items, the four planted error types) hold.
- The AUD grader now treats `1,420` and `1420.00` as the same number; the lab's grader split `1,420` into `1` and
  `420`.
- HRD and the MON prompt are verbatim. The MON grader is stricter than the lab's (the lab also accepted `821.6`).
- JUDGE, CRIT and SUM run on an **invented** demo transcript with invented plants. Scores on it mean little.
- The OCR page is drawn in code at **1400 px** wide. The lab's original page was 1240 px and clipped the amount
  column (see `docs/excerpts/0410-vision-shootout.md`); the lab later redrew its own fixture at 1360 px. Three
  widths, one test: the four `4/5` rows in `results.csv` are not comparable with this repo's `tests/ocr.py` output.

## Why `docs/excerpts/` exists

The README makes claims about numbers measured in a private notebook. Each cited section is shipped as a
sanitised excerpt so the claim can be checked against its source:

| file | covers |
|---|---|
| `docs/excerpts/0234-tool-count.md` | §23.4, 4 tools vs 20 with a meta-dispatcher |
| `docs/excerpts/0410-vision-shootout.md` | §4.10, vision shootout, incl. the 2026-11-12 fixture-bug addendum |
| `docs/excerpts/0401c-reranker-ubatch.md` | §40.1c, the reranker's ubatch ceiling |
| `docs/excerpts/052-vulkan-vs-ollama.md` | §52, Vulkan vs Ollama on Pascal |
| `docs/excerpts/088-dflash-no-go.md` | §88, DFlash drafter no-go |
| `docs/excerpts/096-judge-calibration.md` | §96, §96.1, §96.2, judge calibration and redaction recall |
| `docs/excerpts/097-single-pass-vs-pipeline.md` | §97 (and §97.1), single pass vs the verify pipeline |
| `docs/excerpts/104-summarize-bonsai.md` | §104, summarize on the 35k-char transcript |
| `docs/excerpts/107-ngram-spec-and-reasoning-budget.md` | §107, n-gram spec decoding and reasoning budgets |
| `docs/excerpts/110-cat-synthetic.md` | §110, payee categorization on a synthetic fixture, the schema arm and the arena |
| `docs/excerpts/111-aux.md` | §111, the agent's helper-model lanes on generic cases |
| `docs/excerpts/112-sql-synthetic.md` | §112, text-to-SQL on an invented hardware store |
| `docs/excerpts/113-rent-synthetic.md` | §113, rental-listing extraction on 100 invented listings |
| `docs/excerpts/114-embed-device.md` | §114, the same embedding weights on the CPU and on the card |
| `docs/excerpts/126-vision-synthetic.md` | §126, drawn-image vision test on six seats |
| `docs/excerpts/115-speed.md` | §115, the 24-model speed and residency sweep on one build |
| `docs/excerpts/116-fim.md` | §116, inline shell completion on a synthetic case set |

Every table and number in an excerpt is copied verbatim from the notebook section; prose is trimmed to what a
reader needs to follow the numbers. What was generalised:

- **Paths** — absolute paths, `raw/...` run directories and script filenames became descriptions ("the
  fixture-drawing script", "the preset generator", "the bench harness").
- **Seats** — the role words above, in both prose and table labels.
- **Hosts and services** — machine names and the lab's other local services became "the other local services",
  "the RAG service", "the batch queue". No IPs, no ports, no hostnames.
- **People and businesses** — personal names were dropped; the invoice fixture's vendor string became "a
  fictional vendor name"; the two company names used as planted edits became "a company swap". Public model names,
  product names and paper titles were kept, because the numbers are about those models.
- **Internal bookkeeping** — backlog card names, queue job ids and source line numbers inside the lab's code were
  dropped where they carried no finding.

Nothing was cut because it could not be generalised: every number the README uses survived the rewrite. Two
sections the README cites are folded in rather than given their own file: §96.2 lives in the §96 excerpt (it is
the same harness and the same table family), and §97.1 lives in the §97 excerpt. §23.4 gets its own excerpt even
though the notebook list of "cited sections" did not name it, because the README's TOOL paragraph quotes its
result directly.

The excerpts carry no licence of their own: they are part of this MIT-licensed repository, and they contain no
third-party text beyond public model names and the titles of public documents (a paper, a tutorial, a model release).
