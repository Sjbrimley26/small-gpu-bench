# §120 Real documents on the portable set: a real purchase order separates the seats, and reading is not the same as reasoning

> Excerpt of §120 of a private lab notebook; paths, service names and hosts are generalised. The documents
> published with this excerpt are real, and so are every one of the numbers. Companion to the 26
> `results/results.csv` rows added from this section (PO-REAL 13, RAGQ-REAL 13) and to the two tests they were
> measured with, `tests/po_real.py` and `tests/ragq_real.py`.

**Why.** Two portable tests had just been built on generated fixtures — an invented purchase order and an
invented makerspace corpus — and measured across thirteen seats. The order was passed on every field by every
seat (8/8 line items, 5/5 header fields, only the formatting varying), and the corpus scored 6-9 out of 10 with
every seat at 60 % or better. Both were therefore measuring formatting and budget, not reading. The obvious
suspicion was that generated documents are too tidy: one value per line, columns in order, nothing wrapped,
nothing repeated, no second source contradicting the first. So both tests were rebuilt on real documents, and
the sweep was repeated on the same thirteen seats.

**The documents, and why they can be published.**

| id | document | licence |
|---|---|---|
| PO | Texas HHS Commission purchase order HHSTX-7-0000417784, 2026-11-02, 12 line items | public record published by the agency |
| A / B | 13 CFR 125.6 (limitations on subcontracting), CFR **2026** and **2023** annual editions | U.S. Government work, not subject to copyright (17 U.S.C. 105) |
| C / D | NIST SP 800-63B-**4** §3.1.1.2 (2025) and SP 800-63B-**3** §5.1.1.2 (2017) | U.S. Government work, not subject to copyright |

The purchase order ships as its `pdftotext -layout` rendering — what a real pipeline sees, not a curated table:
interleaved columns, descriptions wrapped onto their own lines, the page header repeated on every page, the
vendor's tax ID printed above its name, and no subtotal, freight or tax line to invent. All personal contact
details (agency and vendor staff names, e-mail addresses, telephone numbers) are replaced with
`[name redacted]` / `[email redacted]` / `[phone redacted]`; the vendor company, the agency addresses, the line
items and every price are unchanged. Provenance, retrieval date and the redaction list are in
`tests/fixtures/real/SOURCES.json`, and a self-test fails if any e-mail address or telephone number ever
appears in a fixture again.

The two revision pairs are what make the reading test hard rather than clerical. Between the CFR editions the
2026 text **adds** a rule the 2023 text does not contain: for a multi-agency set-aside contract where more than
one agency can issue orders, the ordering agency must use the *period of performance for each order* to judge
compliance, and a new past-performance section governs what happens when a contractor misses the limit. Between
the NIST revisions the single-factor password minimum went **8 → 15** characters (`8` survives only for
multi-factor use) and composition rules went from "not required" to "SHALL NOT impose". A writer that reads the
older sheet and answers confidently is wrong in exactly the way a real RAG stack is wrong every day.

**Method.** Thirteen seats on one 4 GB card, one at a time, one run each, temperature 0, thinking off, 10-58 s
per purchase order and ~1.9k-token prompts for the reading test. Each reading question ships the retrieved
chunk(s) it needs — a fixed window of the document, labelled only with title and edition date, with no source
marked "current". Nothing is judged by a model: both tests score with code. The PO test reports parse mode
(strict / fenced / wrapped / invalid / empty) separately from 8 schema keys, 4 header fields, 12 line rows
(label, class code, quantity, unit, unit price, amount), the stated total, and whether the reply's own numbers
add up (`qty × price = amount` per line, amounts summing to the total). The reading test uses a per-question key
of regexes that must / must not match; a superseded value is accepted only when the answer labels it as old or
ties it to the case where it still applies.

**PO-REAL: five seats read it perfectly, three misread every column.**

| outcome | seats | reading |
|---|---|---|
| 12/12 items, all header fields, total, arithmetic | granite4:3b-h, minicpm5-2b, gemma4:e4b, bolt-e4b, Spark-X2.6-4B | a real interleaved table is inside a 3-4B seat's reach |
| 12/12 items, one header field wrong | ling3-tiny-64k, granite4:micro, Moon-Beam-8B-A1B | ling3-tiny took the **tax ID** printed above the vendor name as the vendor |
| 11/12 items | LFM3-1.7B-QAD | one line lost to a wrapped description |
| **0/12 items** | granite3.3:2b, FrogNano-4B, granite4.2:3b | every row right except the columns: quantity where the unit price goes (25.34 vs 102.00) |
| no JSON (`finish=length`) | LFM3-1.7B-Q4_K_M | the trace spent the cap |

The 0/12 group is the interesting one. They emitted twelve rows with the correct line labels, class codes and
descriptions, and put the unit price in the quantity field and the quantity in the price field, so the
arithmetic check fails on every line while the *stated* total still comes out right — a failure mode that
cannot occur on a generated table, because a generated table has one value per line in reading order.

**RAGQ-REAL: lookups are free, applying a rule is not.**

| score | seats |
|---|---|
| 15/15 | FrogNano-4B-2609-IQ4_XS, LFM3-1.7B-QAD |
| 14/15 | granite4:micro, gemma4:e4b, granite4.2:3b |
| 13/15 | minicpm5-2b, granite4:3b-h, LFM3-1.7B-Q4_K_M, Spark-X2.6-4B |
| 11/15 | ling3-tiny-64k, granite3.3:2b, bolt-e4b |
| 10/15 | Moon-Beam-8B-A1B |

Eight of the fifteen questions are answered correctly by **all thirteen** seats: the three exact lookups (the
50 % services cap, the 64-character maximum, the blocklist behaviour), both negatives (a NAICS size standard and
a named password-manager product are both absent and every seat declined), the three-percentage enumeration,
the 2017-vs-2025 length pair, and the full-and-open-order exemption. What separates seats is narrower and more
interesting:

| question | shape | seats failing (of 13) |
|---|---|---|
| A2 — `$4,000,000` general construction, `$3,200,000` to non-similarly-situated firms: compliant? | apply the 85 % cap to a number | **6** |
| S3 — how a missed subcontracting limit is handled in the past-performance evaluation | two clauses from the 2026 text, absent from 2023 | **5** |
| S2 — what changed between the NIST revisions | compare two sources, two facts | 4 |
| A1 — the most a `$2,000,000` services contract may pay non-similarly-situated firms | apply the 50 % cap (answer `$1,000,000`) | 4 |
| F1 — the multi-agency per-order rule | the 2026-only rule | 3 |
| V1 — "how much of the work does the prime keep in house" | vague phrasing | 3 |

Three of the six A2 failures say "not compliant" and then compute 80 % < 85 % in the same answer — the
arithmetic is right, the verdict is inverted. That is a comprehension failure, not a lookup failure, and it is
where a 1.7B and an 8B separate from each other.

**What this changes.** The synthetic versions stay in the set as the clean baseline (they are cheap and they do
measure formatting and budget: the fence, the trace eating the cap, the `##` vs `**` heading), but the
finding-grade numbers are these. On a real document the spread is 0/12 to 12/12 on extraction, and 10/15 to
15/15 on reading; on the generated ones it was 8/8 to 8/8 and 6/10 to 9/10.

**Caveats, in full.** One run per cell, one card, one build, thirteen seats of which four are quant variants of
three models; fifteen reading questions and one purchase order, so read gaps of one or two as noise; the keys
are regexes written after reading the documents, which is the standard for this repo but not a substitute for a
second reviewer; the model ranking is **not** by size (the 8B Moon-Beam is last, the 1.7B LFM3-QAD joint first),
so treat it as a per-task result rather than a capability ladder.

**Raw.** `tests/fixtures/real/` (documents, gold, `SOURCES.json`), the records in `results/raw/PO-REAL-*.json`
and `RAGQ-REAL-*.json`, and the lab-side harness that ran the sweep (one seat at a time, GPU lease, records
written straight into this repo).
