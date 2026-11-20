# §126 VISION: drawn-image test on six 4 GB-class vision seats, three image sets; four seats read 28-29 of 30, a 4B Qwen3.5 with thinking does not

> Excerpt of a private lab notebook entry; paths, service names and hosts are generalised. The numbers are as
> measured by `tests/vision.py` in this repo (images drawn in code, nothing shipped), one run per image set, on one
> 4 GB card with a llama.cpp router. This replaces the lab's older vision scores, which were logged but never archived.

**Setup.** Twelve images drawn in code as RGB PNGs, in three sets of four kinds: a film poster, a logo, a scene with a
countable number of stick figures, and an arrow icon. Each set uses different answers — titles `NIGHT HARBOR` /
`IRON TIDE` / `SALT ROAD`, years 2026/1987/2031, circle and sun colours, wordmarks `KESTREL` / `MARTEN` / `HERON`,
5 / 3 / 7 figures, a left / right / up arrow, digits 7 / 4 / 3 — so a score cannot be one lucky guess at one picture.
Eight questions per set, weighted 2/1, 10 points per set, 30 points in all, one denominator, graded in code with
accept/reject words (so "left or right" and "15" do not score). Thinking left on its default, 200 output tokens, one
run per set. Each seat was loaded through the router, then asked the 24 questions; whole-card VRAM comes from
`nvidia-smi` after load (it includes the small resting code-completion server; the older `/api/ps` figure leaves the
projector out and under-reports it). Set 0 is the original four images, byte-identical PNGs; sets 1 and 2 are the
redraw.

| seat (model) | score, 3 sets | per set | missed | VRAM after load | wall, 24 questions |
|---|---|---|---|---|---|
| e4b-e2b-aggressive (Gemma-4 e4b variant) | **29/30** | 9 / 10 / 10 | set 0 arrow direction ("Up" for a left arrow) | 3977 MiB | 690 s |
| gemma3-4b (GGUF + mmproj) | 28/30 | 10 / 10 / 8 | set 2 figure count (said 8, drawn 7; weight 2) | 3517 MiB | 196 s |
| qwen2.5vl-3b (GGUF + mmproj) | 28/30 | 9 / 10 / 9 | icon digit twice (read "1" for 7, then "1" for 3) | 3765 MiB | 111 s |
| gemma4:e4b | 28/30 | 8 / 10 / 10 | set 0 figure count (said 6, drawn 5; weight 2) | 3155 MiB | 274 s |
| qwen3.5-4b | 17/30 | 6 / 7 / 4 | 13 points, every one an empty reply at the 200-token cap | 3931 MiB | 1161 s |
| gemma4:e4b text-only control | not scored | — | every image request refused, HTTP 500, in all three sets | 3965 MiB | n/a |

**What the extra image sets changed.**

- **The one-set ranking was luck.** On the first set alone the field read 10/9/9/8 and looked like a hierarchy. On
  thirty points it is 29/28/28/28/17: three seats tie, one leads by a single question, and each seat's miss falls on a
  different question (count, digit, arrow, count). Ten of ten versus nine of ten was one question; so is twenty-nine
  versus twenty-eight. The defensible claim is that four seats clear 28 of 30 and none of them is reliably perfect,
  not that one reads images better than the others. Breaking the tie needs a repeat (n=2); with n=1 per set it is not
  resolved.
- **The failures are the same kinds of question as before**, and they are the hard ones for small projectors: exact
  counting of touching figures, reading one large isolated digit, and an arrow whose direction is a few pixels of
  head geometry. The wordmark and the poster text — the parts that are ordinary text — were read correctly by every
  seat that answered at all, in every set.
- **qwen3.5-4b is not a reading result, it is a budget result.** All 13 of its lost points are empty replies with
  `finish_reason: length` at the 200-token cap: the seat spends the budget thinking and never emits an answer. A
  follow-up arm re-asked only those 11 questions with a **600-token** budget: it scored 7 of their 13 points, and it
  still returned **nothing at all on 5 of the 11** (the wordmark in all three sets, one colour, two digits) — a sixth
  answer was a near-read (`MARTEL` for `MARTEN`). When it does stop it is right (5 of 6 answers, including both exact
  counts). So the failure is runaway thinking, not perception — and tripling the budget only half fixes it, at
  4-10x the wall time of the fast seats. Read the row as "unusable at this budget", not as "cannot read".
- **The text-only control is the plumbing check**, unchanged: a seat with no projector does not guess, it errors on
  every image request (HTTP 500, `image input is not supported ... you may need to provide the mmproj`), so the other
  rows are image reads and not language priors.
- **Speed:** the two seats converted to a standard GGUF plus a separate projector are still the fastest (196 s and
  111 s for 24 questions); the two that think by default pay for it in wall time (690 s and 1161 s) without a better
  score.
- Not run: ministral-3:3b and qwen3-vl:2b (no router seat with a projector), repeats (n=2), and a full second pass at
  a larger output budget for the thinking seats.
