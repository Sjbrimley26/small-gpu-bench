# §125 Compression on a synthetic transcript: on a 4 GB card, no seat kept more than 1 of 7 planted facts

> Excerpt of a private lab notebook; seat names and paths generalised. The transcript is generated from templates
> in `tests/compress.py` (seed 7, about 23.5k estimated tokens, no real session text). One run per seat, n=1.

**Setup.** Seven needles (a path, an in-flight job and pid, a numeric result, a pending question, a never-do rule,
a version string, a name) are planted at fixed depths among decoys of the same kinds. The transcript is compacted
over three rounds, each round summarising the previous summary plus the next third of the transcript. The output
ceiling is 3000 tokens and is part of the test: a model that will not stop is recorded as `overflow`, not as a wrong
summary. After round 3 each needle is asked as an exact-match resume question; an abbreviated path fails.

| seat (4 GB card) | round 1 | round 2 | round 3 | resume exact |
|---|---|---|---|---|
| small helper seat (ling3-tiny class) | overflow | not run | not run | n/a |
| FrogNano-4B (IQ4_XS) | 0/3 kept | 1/5 | 1/7 | 1/7 |
| granite4 7B at 40k | 0/3 | 0/5 | overflow | 0/7 |
| lfm2 at 40k | 0/3 | 0/5 | overflow | 0/7 |
| 4B document-audit seat | CUDA out-of-memory at load | | | |
| gemma4 at 40k | CUDA out-of-memory at load | | | |

No seat invented a path or number (0 invented everywhere it produced a summary): the failure mode is dropping,
not fabricating.

**Reading it.** The best arm keeps one needle (the never-do rule) of seven after three rounds. Two seats overflow
the 3000-token ceiling by round 3 (their summaries grow instead of compressing), one overflows in round 1, and two
cannot load at 40k context on this card at all. That 40k is a wall on 4 GB is consistent with the lab's earlier
(private-transcript) ladder, which is not republished here.

**Caveats.**
- n=1 per seat; one seed. Needles planted in thirds, so "kept" per round is out of needles planted so far.
- `overflow` and OOM rows are results, not harness faults; the OOMs are in the server log (CUDA out of memory at load).
- Round times (40-190 s) include prompt processing of about 14k tokens on a 4 GB GPU.
