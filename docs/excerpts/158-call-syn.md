# §158 CALL-SYN: every seat finds the promises; every seat also invents tasks, and budgeted thinking cuts that 2-3x

> Excerpt of a private lab notebook entry; paths, service names and hosts are generalised. The numbers are as
> measured by `tests/call.py` in this repo (44 invented calls, gold built in code, no judge), one run per call per
> seat, temperature 0, on one 4 GB card with a llama.cpp router.
>
> **Origin and caveat.** The lab's first call-summary bench (§155) scored six transcripts (1,391 characters in all, four
> of them private chatter snippets, n=2) by counting how many tasks the model emitted. "3/3" there meant three
> tasks, not the right three, and no due date was scored. It saw every seat at 0 false tasks, which this test shows
> was an artefact of four short snippets. Its numbers are not comparable with the table below and none are reused
> here; the fixture here is invented.

**Setup.** `tests/call.py` builds 44 two-speaker calls from a seed, 51 to about 740 words, with 27 planted commitments
over 16 calls (the other 28 hold none). Seven tiers: `plain` (8 calls), `buried` (8, a commitment inside 400-900 words of
small talk), `conditional` (6, "if the hinges come in I'll ..." and the condition never resolves), `cancelled` (6,
promised then retracted), `third_party` (6, "Dana said she'd email"), `banter` (6), `short` (4, wrong number or dropped
call). The last five tiers have **no** commitment. Every call with a real commitment also carries a decoy intention
("we should catch up sometime"). The gold answer comes from the same code that writes the transcript. Scored in code per
call: **recall** (the commitment's action keyword appears in some reply task), **owner** (right speaker), **due** (the
reply's date equals the date resolved against the call date), **false tasks** (every task on a call with no commitment,
plus any task matching no gold item), **false calls** (no-commitment calls that got at least one task), JSON shape,
seconds. `--selftest` runs a scripted good reply and a scripted bad reply (one missed, one invented) through the
grader. Think arm: thinking on, reasoning budget 256 tokens, max_tokens 2000.

| seat | arm | recall /27 | due /26 | false tasks | no-commitment calls with a false task | secs (44 calls) |
|---|---|---|---|---|---|---|
| spark-agent-16k | off | 27 | 16 | 27 | 18 | 266 |
| frog-agent-16k | off | 24 | 16 | 7 | 6 | 181 |
| gemma4-agent-16k | off | 27 | 20 | 18 | 18 | 293 |
| bolt-agent-16k | off | 27 | 16 | 16 | 16 | 237 |
| aux (ling3-tiny-64k) | off | 27 | 18 | 49 | 23 | 288 |
| aux-granite | off | 21 | 11 | 35 | 17 | 182 |
| moonbeam-agent | off | 26 | 17 | 47 | 20 | 275 |
| spark-think | budget 256 | 26 | 19 | 16 | 16 | 847 |
| gemma4-agent-think | budget 256 | 27 | 17 | 13 | 13 | 951 |
| frog-agent-think | budget 256 | 25 | 16 | 3 | 3 | 776 |
| bolt-agent-think | budget 256 | 27 | 22 | 9 | 9 | 929 |

**Findings.**

- **Recall saturates; false tasks separate the seats.** Seven of eleven rows find 26 or 27 of 27. False tasks run from
  3 to 49 on the same calls. No seat reached zero; the §155 "0 false tasks" was a four-snippet artefact.
- **The false tasks sit on three tiers.** On the thinking-off arms, `conditional`, `cancelled` and `third_party` calls are
  where tasks get invented (a hedged or retracted or someone-else's promise becomes the speaker's task); `banter` is
  clean for four of the seven (spark, gemma4, bolt, frog at 0; aux 12, moonbeam 8, granite 1). `short` calls (wrong number)
  draw 1-3 false tasks on every thinking-off seat, and the think arms keep 2-3 of them (three of four).
- **Budgeted thinking cuts false tasks 2-3x** (spark 27 to 16, bolt 16 to 9, frog 7 to 3, gemma4 18 to 13) for
  about 3.5-4x the wall time (181-293 s becomes 776-951 s for 44 calls). Recall does not move more than one promise.
- **Due dates are the weak spot everywhere**: 11 to 22 of 26 right on every row. Date arithmetic against the call date
  is not solved by any seat; bolt with thinking is best at 22/26.
- **Sample size:** n=1 per call per seat, one fixture, one seed. A difference of one or two tasks is not a ranking.
