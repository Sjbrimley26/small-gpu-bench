# §117 LFM3-1.7B round 2: the thinking switch decides the score, and the window is real to ~116k

> Excerpt of §117 of a private lab notebook; paths, service names and hosts are generalised. Every model
> name and every number is as recorded. Companion to the CAT-SYN, SQL-SYN, RENT-SYN and AUX-SYN rows added
> to `results/results.csv` from this section, and to the §115 SPEED row for the same model.

**Why.** LFM3-1.7B is the fastest model on the card (45.2 t/s generation, the top of §115) and it looked poor
on everything else measured, including a 0/18 on the triage contract. Round 2 asked whether that was the
model or the harness: one 4 GB Pascal card, one build (12211, the first that can load the `lfm3`
architecture), q8_0 KV cache, flash attention on, one model resident, temperature 0 for every scored row,
and the vendor's own sampling (0.1 / top_k 50 / repeat 1.1) as a separate A/B.

**The mechanism, first, because every number below depends on it.** The model emits a reasoning trace on
open-ended prompts and `chat_template_kwargs.enable_thinking=false` is not a reliable switch:

* short and structured lanes (approval, session titles, payee classification, SQL, JSON extraction) — the
  flag silences the trace and the answer lands in 2-6 tokens. Without it the trace takes the whole output
  cap and the reply is **empty**.
* open-ended generation (summarisation) — with the flag the trace goes to `reasoning_content` and `content`
  is empty; without it the trace lands **inside `content`** with no closing marker, so a consumer receives
  the model's deliberation instead of the deliverable. At a 3000-token cap: 12,739 characters of trace,
  `finish_reason: length`, no summary.

**Window ladder.** Generation throughput is flat across rungs and the KV cache is unusually cheap
(~9 MiB per 1k tokens), so the advertised window is real; the cost moves into cold load and prefill.

| context | load | VRAM | gen t/s | needle depth | needle | prefill for that prompt |
|---|---|---|---|---|---|---|
| 32768 | 8.1 s | 1435 MiB | 46.5 | 27,423 tok | found | 48 s |
| 65536 | 7.1 s | 1735 MiB | 46.3 | 56,800 tok | found | 143 s |
| 131072 | 102.1 s | 2335 MiB | 46.5 | 115,525 tok | found | 461 s |
| 262144 | 108.2 s | 3535 MiB | 46.5 | 233,033 tok | **missed** | 1640 s |

* Load time jumps 7-8 s → 102-108 s between 64k and 128k: residency is cheap, a cold load is not.
* A repeated identical ask re-uses 98-99 % of the prompt cache (35 s for the second 233k-token ask).
* The 256k miss is an enumeration rather than a refusal: both a 48-token and a 300-token answer budget
  produced "Line 00001 to Line 08686 …" listings and hit the cap. The needle document is numbered filler,
  which invites listing, so this rung says "no answer at 233k in this probe", not "cannot retrieve at 233k";
  a prose-filler rerun is the fair test and was not run.

**What it scores** (published rows are the CAT-SYN, SQL-SYN, RENT-SYN and AUX-SYN ones; best in field in
brackets):

| test | LFM3-1.7B | field |
|---|---|---|
| CAT-SYN, thinking on | 64 right, 0 wrong, 103 blank of 200 [160 right] | 133-160 right |
| CAT-SYN, thinking off | 11 right, 0 wrong, 156 blank [160 right] | 133-160 right |
| SQL-SYN | 24/50 lenient, 0/50 strict (24 answers fenced) [45/50] | 24-45/50 |
| RENT-SYN | 88/100 checker, 27/100 all-fields, pets 44, flags P0.38/R0.58 [76/100 fields] | 22-76/100 |
| AUX-SYN approval, thinking on | 0/27, all 27 empty [23/27] | 20-23/27 |
| AUX-SYN approval, thinking off | 19/27 exact, 3 dangerous approved [23/27] | 20-23/27 |
| SUM | no summary in either arm (see the mechanism above) | 19-23/24 coverage |

Zero wrong answers on CAT in both arms is the honest reading of that test for this model: with the trace it
never reaches an answer on 103 of 167 category rows (the retry at double the cap is consumed too); without
the trace it answers `UNSURE` on almost everything. It is fast at being uninformative. SQL arrives correct
but fenced, so a strict consumer scores 0 and only a fence-stripping consumer sees the 24. RENT is in the
weakest class on the all-fields column even though its checker pass rate looks respectable — the checker
saturates on this fixture and is not the discriminating column.

**The triage 0/18 was a heading-format artifact, not a capability result.** Every reply used `## Goal`
where the prompt's contract asks for `**Goal**` — the same sections in the same order, correct titles, valid
JSON. Re-scored with an H2 heading accepted as the same heading: **18/18**, and 17/18 at the vendor's own
sampling (one reply was invalid JSON — an apostrophe inside a string). The strict row stays in
`results.csv` because the contract is what the real prompt asks for, but "this model cannot write a task
spec" is the wrong conclusion; "it picks a different markdown heading level" is the right one.

**Two axes where it does well.** Refusal calibration — three invented documents, four answerable and two
not — scored 4/4 on the answerable questions with the source document named, and declined both unanswerable
ones in the right shape ("the provided documents do not contain any information about …"). And the
approval lane, once the trace is suppressed, is only 3 wrong out of 27 — those three are `d_dropdb`,
`d_pipe_shell` and `i_rm_with_approve`, which is exactly why it is unusable as an approval gate: the
failures it does make are the dangerous ones.

**Sampling A/B (triage lane, 6 cases × 3 runs).** Harness default (temperature 0): 18/18 with H2 accepted.
Vendor sampling (0.1 / top_k 50 / repeat 1.1): 17/18, the difference being one invalid JSON. No evidence
that the vendor's sampling helps this model on a scored lane; the switch matters far more than the sampling.

**A QAD variant of the same 1.7B** (same size, 1.4 GB resident) scored 15/27 exact on approval with one
dangerous approval (`d_pipe_shell`) where the base build scored 19/27 with three, and 12/18 strict on
triage in an earlier harness. Same trace behaviour on summarisation. Better judgment head, still not safe.
