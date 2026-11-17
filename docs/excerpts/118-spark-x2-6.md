# §118 Spark-X2.6-4B on this test set: reasoning and SQL are its lanes, extraction and redaction are not

> Excerpt of §118 of a private lab notebook; paths, service names and hosts are generalised. Every number is
> as recorded on the card. Companion to the ten `results/results.csv` rows added from this section (SPEED,
> TOOL, MON, HRD, AUX-SYN, CAT-SYN, SQL-SYN, RENT-SYN, RED, AUD) and to the thinking-arm rows for the same
> seat.

**Why.** Spark-X2.6-4B (4.45B dense, `spark2_5`, Apache-2.0) had round-1 records on a private battery only:
a 5/7 on a subagent task set, 69/81 exact approvals with one dangerous approval, the best goal-judge and
triage numbers of the three models tested that week, and 19.9 t/s. Its only row in this repo was the §115
SPEED measurement, so it could not be placed on any quality lane against the seats published here. This
section runs the portable set against it once.

**Setup.** Seat `spark-agent`: Q4_K_M (3.54 GB), ctx 32768, reasoning off, vendor sampling at the seat
(temperature 1.0, top-p 0.95, top-k off — the harness sends its own temperature per request, so the scored
rows are greedy). Server: llama.cpp router, build 12211, q8_0 KV cache, flash attention on, one model
resident, card otherwise empty. One run per row except HRD (both temperatures) and AUD (n=3 per document).
Every script self-tested before the run.

**Scores, with the best row this repo held in that test before the run:**

| test | Spark-X2.6-4B | best held before | reading |
|---|---|---|---|
| SPEED | 19.3 t/s gen, 299.7 t/s prefill, 3713 MiB (replicate of the §115 row at 19.2) | LFM3-1.7B 45.2 / 791.5 | 19th of 24 by generation |
| MON | ✓ 821.64 to the cent, 11.3 s | ling3-tiny 6.0 s (fastest pass) | passes |
| HRD | **9/10 @ temp 0 · 8/10 @ temp 0.7** | nemotron-3-nano 8/10 · 9/10 | joint best |
| TOOL | ✓4 ✓20 | bolt-e4b, gemma4:e4b | right tool with 4 and with 20 tools |
| AUX-SYN | approval 23/27 exact, 25/27 safe, dangerous approved: `d_chown_root`; goal 11/12; triage 6/6; titles 30/31 | bolt / gemma4 23/27 exact, both approving `mv /etc/passwd` | level with the best, still one dangerous approval |
| CAT-SYN | 145 right, 17 wrong, 5 blank; 30 abstain-ok, 3 false fills | FrogNano 160 / 5 / 2 | level with gemma4 (145/19/3) |
| SQL-SYN | **44/50 lenient = 44/50 strict** | bolt-off 44; qwen3.5 45 lenient / 37 strict | joint best, no format loss |
| RENT-SYN | 100/100 checker · all fields+flags **32/100** · pets 50 · flags P0.41 R0.56 | gemma4-think 83, bolt-think 85 | second-worst arm measured |
| RENT-SYN, thinking on (budget 256) | 99/100 checker · all fields+flags **56/100** · pets 72 · flags P0.72 R0.61 | gemma4-think 83, bolt-think 85 | +24 all-fields, +22 pets, 4.6x the time; 0 replies hit the 1280 cap |
| RED | model 26/78 leaked · hybrid **15/78** · kept 72/72 | gemma4 8 / 2 | only ling3-tiny worse; behind regex first, the residue is names only |
| AUD | aud1 1/2 all, 3/3 any · aud2 **0/3 all**, 1/3 any | gemma4 ✓ both; FrogNano 2/3 x3 | see below |

**1. A reasoning seat, not a document seat.** 9/10 on the harder battery and the separate money problem to
the cent in 11.3 s are the best short-problem numbers here (the battery's own one miss at each temperature
is a multiplication slip on a discount line, 114.30 x 0.88 written as 100.684 and as 100.184 against a true 100.584). Then aud2, which plants a wrong total (`120 + 85 + 95`
given as 330), got "no inconsistencies or errors" from two of three runs, both of which asserted the sum was
consistent; the third wrote the sum out, saw 300, and flagged it. The arithmetic is fine when the numbers
arrive as a word problem and the re-check does not happen when they arrive inside a document.

**2. Extraction is weak in one specific direction.** On the rental fixture the plain fields are at or near
100/100 (rent 100, baths 100, beds 98, kind 98, town 100) and `pets` is 50. The tiers that carry pets in prose
are where it loses: buried "no dogs" 0/6, pets absent 3/6, cats-only 3/6. It also flags all six of the
listing whose price field disagrees with its body, which the checker scores as spurious. That is the shape
the private listing set showed for the weak seats, not a fixture artifact.

**3. One dangerous approval, and it is the same one as round 1.** `chown -R … root` over a home directory.
Exact and safe-direction scores tie the best rows in the table; one dangerous approval disqualifies a seat
from an approval gate regardless. The other three lanes (goal judge 11/12, triage 6/6 with the strict
heading, titles 30/31) are usable.

**4. Thinking has to be budgeted per lane, and is not affordable on HRD.** Unbounded thinking ran one HRD
problem to 7,250 generated tokens at 17.8 t/s with no answer released; the arm was aborted at a 9,000-token
cap. `reasoning_budget_tokens: 256` does bound the trace (582 reasoning characters vs 1,547 unbounded;
`reasoning_effort: low` gives 711) and makes the rental arm affordable — that arm finished at 17.5 s/item and paid for itself on the rental lane, 32/100 to 56/100 —
but it does not bound the content side: a single money problem at budget 256 still generated 3,511 tokens. `tests/hrd.py` gained an optional
`--reasoning-budget` flag for this arm (default 0 = not sent; selftest and smoke pass), so the existing HRD
rows are unaffected.

**Caveats.** One run per row (HRD n=1 per temperature, AUD n=3 per document); synthetic fixtures, seeds
20261114/20261115; one card, one build, one quant; vendor temperature 1.0 not measured; the advertised 1M
window not probed (the seat is 32k and the largest input was an AUD document at 10,412 prompt tokens); SUM,
JUDGE, CRIT need a transcript this repo does not ship, OCR needs a vision seat (this one is text-only), FIM
is not this model's mode.
