# §126 VISION: drawn-image test on six 4 GB-class vision seats; a 4B Gemma 3 and a 3B Qwen2.5-VL clear it, the 4B Qwen3.5 does not

> Excerpt of a private lab notebook entry; paths, service names and hosts are generalised. The numbers are as
> measured by `tests/vision.py` in this repo (images drawn in code, nothing shipped), one run per seat (n=1), on one
> 4 GB card with a llama.cpp router. This replaces the lab's older vision scores, which were logged but never archived.

**Setup.** Four images drawn in code as RGB PNGs (a poster, a logo, a five-figure scene, an arrow icon), eight questions
weighted 2/1 for 10 points on one denominator, graded in code with accept/reject words (so "left or right" and "15" do
not score). Thinking left on its default, 200 output tokens, one run. Each seat was loaded through the router, then
asked the eight questions; whole-card VRAM comes from `nvidia-smi` after load (it includes the small resting
code-completion server; the older `/api/ps` figure leaves the projector out and under-reports it).

| seat (model) | score | missed | VRAM after load | wall, 8 questions |
|---|---|---|---|---|
| gemma3-4b (GGUF + mmproj) | **10/10** | none | 3517 MiB | 75 s |
| qwen2.5vl-3b (GGUF + mmproj) | 9/10 | icon digit (read "1" for "7") | 3765 MiB | 38 s |
| e4b-e2b-aggressive (Gemma-4 e4b variant) | 9/10 | arrow direction (said "Up") | 3977 MiB | 658 s |
| gemma4:e4b | 8/10 | figure count (said 6, drawn 5; weight 2) | 3155 MiB | 161 s |
| qwen3.5-4b | 6/10 | logo word, figure count, icon digit (empty replies) | 3931 MiB | 966 s |
| gemma4:e4b text-only control | not scored | every request refused with HTTP 500 | 3965 MiB | n/a |

**Reading it.**

- The text-only control is the plumbing check: a seat with no projector does not guess, it errors on every image
  request, so the other rows are image reads and not language priors.
- The two fastest seats are the two that were converted to a standard GGUF plus a separate projector; the two slowest
  (qwen3.5-4b, the Gemma-4 variant) spend most of their wall time on thinking tokens, not on the image. The empty
  qwen3.5-4b replies are the thinking budget running out at 200 tokens, not a wrong answer; a larger budget was not run.
- Nine of ten points is one question. With n=1 and eight questions, 10/10 versus 9/10 is not a ranking.
- Not run: ministral-3:3b and qwen3-vl:2b (no router seat with a projector yet), repeats, larger output budgets.
