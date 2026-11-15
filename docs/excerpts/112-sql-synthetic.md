# §112 SQL-SYN: text-to-SQL on an invented hardware store, ten seats, and how the ranking compares with a private run

> Excerpt of §112 of a private lab notebook; paths, seat names and hosts are generalised. The database is invented
> (`tests/sql.py`, seed 20261115) and built in memory at run time; no real catalog or vendor data is in this repo.

**Why.** The lab's 50-question text-to-SQL test ran against a real purchasing database and cannot be published. `tests/sql.py` rebuilds it on an invented
hardware-store schema (10 tables, 120 parts, 40 orders) with the same shape: one SQLite schema, a prompt of rules + schema DDL + two sample rows per table + a hint
+ the question, gold SQL verified by executing it, model SQL executed and its rows compared with the gold rows (order-insensitive unless the question asks for an
order). 50 questions in 11 kinds. Traps taken from what split the private set: quoted column names with spaces and punctuation, near-duplicate column names, a join
the model should not make, NULL handling, tied maxima, and text dates. llama.cpp router (build 12211), GTX 1050 Ti 4 GB, one model resident, temp 0, output cap 700, no thinking switch.

**Two ways of getting the SQL out of the reply, scored side by side.** *Lenient* cuts at the first fence, prose, blank line or semicolon; *strict* requires the raw reply to be the
query. Nine of ten seats emit a bare query; qwen3.5:4b wraps 8 replies in a fence (45 lenient, 37 strict) and granite3.3:2b loses 2 points the same way.

| model (seat) | lenient | strict | errors (lenient) | wall |
|---|---|---|---|---|
| qwen3.5:4b (text, thinking on) | **45** | 37 | wrong rows 2, **empty 3 (hit the 700-token cap)** | 1220 s |
| bolt-e4b, thinking off | 44 | 44 | wrong rows 6 | 78 s |
| ling3-tiny-64k | 43 | 43 | wrong rows 7 | 81 s |
| bolt-e4b, thinking on | 43 | 43 | wrong rows 5, empty 2 | 285 s |
| granite4:micro | 42 | 42 | wrong rows 7, hallucinated column 1 | 55 s |
| gemma4:e4b (text only) | 42 | 42 | wrong rows 7, hallucinated column 1 | 81 s |
| FrogNano-4B | 41 | 41 | wrong rows 9 | 135 s |
| nemotron-3-nano:4b, thinking on | 35 | 35 | wrong rows 15 | 343 s |
| granite4:3b-h | 34 | 34 | wrong rows 13, hallucinated column 3 | 126 s |
| granite3.3:2b | 24 | 22 | wrong 12, hallucinated column 9, syntax 4, empty 1 | 72 s |

qwen3.5:4b is accurate and unusable as a seat: 1220 s for 50 questions (granite4:micro: 55 s), and three replies were lost to the token cap.

**Where the points go.** Across the ten models: count 93%, quoting 91%, no-join 88%, group-by 87%, aggregate 86%, ordered 83%, subquery 82%, join 77%, **NULL
handling 58%, dates 58%, ties 50%**. Only 9 of 50 questions are answered correctly by every model. The two questions about a NULL reorder flag were missed by 9 of 10
models, partly because the prompt's sample rows print NULL as an empty cell, so the model cannot see it is NULL. A question about the tied most-expensive and tied
cheapest parts was missed by 7 of 10 (`LIMIT 1`), and a March-2026 date match on MM/DD/YYYY text by 8 of 10 (the year was ignored).

**How it differs from the private run.** The private run used the author's real catalog (17 original and 33 added questions), so scores are not comparable and the
schema is larger. On the seven models in both: private 44 / 44 / 40 / 35 / 35 / 32 / 23 for granite4:micro / bolt-e4b / gemma4:e4b / ling3 / granite4:3b-h / nemotron / granite3.3, here
42 / 44 / 42 / 43 / 34 / 35 / 24. Spearman 0.74; 15 of 21 pairs are ordered the same, 3 flip, 3 tie. The **rankings broadly agree**: the top (granite4:micro and
bolt, within two points of each other) and the bottom (granite3.3:2b, 23 vs 24) hold, and so does the weak middle. The one real disagreement is ling3-tiny (43 here, 35 private),
and four models sit inside a two-point band at the top here, so the order inside that band is not a result. A first run of this sweep, on a fixture that differed by a single
word in one sample row, moved three models by 2 to 4 points (granite4:micro 44 -> 42, gemma4:e4b 38 -> 42, granite4:3b-h 32 -> 34): a score on 50 questions from a
small model is sensitive to the prompt at that scale. Only the re-run on the committed fixture is reported.

**n-gram speculative decoding on SQL ([§107](107-ngram-spec-and-reasoning-budget.md) lever).** bolt-e4b, same weights and context, `ngram-mod` with match 8 / n-min 4 / n-max 16 against the
plain seat, two passes per load: decode 16.13 vs 16.23 tokens/s, wall time 79.0 vs 78.2 s, 512 tokens drafted of which 51 accepted (10%). Replies were byte-identical on 49 of 50
questions (the one difference was right, giving 45 vs 44: one question, not a result). The replies are about 23 tokens, too short for the lookup to pay; the
speed-up seen on longer outputs does not carry over to SQL.

One run per model at temperature 0; treat one- or two-question gaps as noise.
