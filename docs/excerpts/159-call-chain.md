# §159 CALL-CHAIN: a second seat that only prunes the first seat's tasks cuts false tasks without losing promises

> Excerpt of a private lab notebook entry; paths, service names and hosts are generalised. The numbers are as
> measured by `tests/call_chain.py` in this repo on the same 44 invented calls as [§158](158-call-syn.md) (gold built in
> code, no judge), one run per call, temperature 0, one 4 GB card with a llama.cpp router. Read §158 first: it carries the
> single-seat rows used as baselines here.

**Setup.** Pass 1 is a saved CALL-SYN reply (thinking on, budget 256). Pass 2 sees the transcript plus the numbered
candidate tasks and answers keep or drop for each one; it cannot edit or add a task, so survivors keep pass 1's owner and
due date and recall can only fall. Both passes use thinking on, budget 256, max_tokens 2000. A call with no pass-1 task is
not audited; an unparseable audit reply keeps every candidate. The `quote` variant asks the auditor to copy the committing
sentence verbatim and drops the candidate in code when the quote is not in the transcript. Pass 1 was regenerated for this
run (the §158 records keep only the first 300 characters of each reply) and reproduced the §158 rows exactly.

| pass 1 → audit | recall /27 | due /26 | false tasks (pass 1 alone) | true tasks dropped | false tasks dropped |
|---|---|---|---|---|---|
| bolt → frog | 27 | 22 | **4** (9) | 0 | 5 |
| bolt → frog, quote variant | 27 | 22 | 4 (9) | 0 | 5 |
| gemma4 → frog | 26 | 17 | 6 (13) | 1 | 7 |
| spark → frog | 26 | 19 | 6 (16) | 0 | 10 |
| bolt → bolt (self-audit control) | 27 | 22 | 6 (9) | 0 | 3 |
| frog → frog (self-audit control) | 25 | 16 | 3 (3) | 0 | 0 |
| frog → bolt | 25 | 16 | 2 (3) | 0 | 1 |
| baselines, §158: bolt / frog alone | 27 / 25 | 22 / 16 | 9 / 3 | | |

**Reading (n=1, one fixture).**
- bolt proposing and frog auditing keeps bolt's 27/27 recall and 22/26 due dates and cuts its 9 false tasks to 4 with no
  true task dropped. Against frog alone it trades one more false task (4 vs 3) for two more promises and six more correct due dates.
- It is the chain, not a second look: bolt auditing itself reaches 6, frog auditing frog changes nothing, frog auditing
  bolt's list reaches 4.
- By tier (bolt → frog): conditional 3 → 0 false tasks, third-party 3 → 1, cancelled and banter 0 → 0; the three
  wrong-number calls still produce one task each, unchanged from pass 1.
- Requiring a verbatim quote, with a code check, changed no outcome against the plain keep-or-drop rule.
- The chain costs both passes' time: about 2x frog alone. A second sweep run at the same time on the same router
  reproduced every arm but one self-audit (one task apart), so the counts hold; its seconds, like these, were inflated by sharing the card's router and are not a speed figure.
- Not run: n=3 repeats (pass 1 is exactly repeatable at temperature 0, so they would add nothing; variance needs
  sampling and a second fixture seed), and any held-out set. The fixture was built before this test and seat order was chosen from §158, so this is a tuned-on result.
