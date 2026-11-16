# §115 SPEED: 24 models re-measured on one card, one build, one method

> Excerpt of §115 of a private lab notebook; paths, service names and hosts are generalised. Every model
> name and every number is as recorded. This sweep supersedes the older speed columns in the lab's matrix,
> which were measured through a retired serving stack on several builds; those figures are not repeated
> here except where they are explicitly compared.

**Why.** The lab's speed table mixed two backends: most rows came from the retired stack, and three models
were added later on the current one. A mixed table is not comparable cell by cell, so every installed model
was re-measured on the current build.

**Method.** One model at a time on a 4 GB GTX 1050 Ti, with the card emptied between models (the serving
router unloads, then the sweep waits for `nvidia-smi` to fall below 400 MiB), then the model is loaded and
measured. Per model: a 444-token prompt (1,776 characters of fixed filler), 200 tokens generated,
temperature 0, one warm-up request discarded, two measured reps, both with `cache_prompt: false` and
`ignore_eos: true`. Rates are read from the server's own timing block; VRAM is `nvidia-smi` after the run.
Build 12211, q8_0 KV cache, flash attention on, one model resident.

**Two knobs decide whether the number is real.** With prompt caching left on, the second rep re-uses the
first rep's KV and the reported prefill rate covers only the 1-4 tokens that were *not* cached:
`granite3.3:2b` then reads **33.4 t/s** where the honest figure is **529.7**. With `ignore_eos` off, a model
that stops after 38 tokens measures decode over 38 tokens. Both are now fixed in the test, and the record
flags `short_reply` if a server ignores `ignore_eos`.

**24 of 26 models measured.** `gemma3:4b` and `ministral-3:3b` fail to load:
`error loading model hyperparameters: key not found in model: gemma3.attention.layer_norm_rms_epsilon`.
Their weights are packaged in a format this build cannot read, and they are the same two the FIM work
already lists as unusable. They cannot be re-measured without a downloadable GGUF.

| model | ctx | VRAM MiB | prefill t/s | gen t/s (min-max) |
|---|---|---|---|---|
| LFM3-1.7B | 32768 | 1441 | 791.5 | **45.2** (45.2-45.3) |
| Moon-Beam-8B-A1B | 32768 | 3975 | 530.2 | **37.4** (36.6-38.1) |
| lfm2-8b-a1b | 65536 | 3985 | 539.0 | **36.5** (33.8-39.1) |
| MiniCPM5-2B | 32768 | 2215 | 643.5 | **35.9** (35.7-36.1) |
| MiniCPM5-2B (thinking on) | 32768 | 2215 | 636.5 | **35.9** (35.9-35.9) |
| ling3-tiny-64k (1.3B active) | 65536 | 4007 | 542.1 | **34.2** (27.0-41.4) |
| granite3.3:2b | 8192 | 1957 | 529.7 | **34.0** (34.0-34.0) |
| llama3.2:3b | 12288 | 2799 | 439.9 | **28.9** (28.8-28.9) |
| granite4:3b-h | 32768 | 2249 | 333.9 | **26.9** (26.9-27.0) |
| granite4:micro | 8192 | 2489 | 377.8 | **25.6** (25.6-25.6) |
| granite4.2:3b | 16384 | 2835 | 419.9 | **25.6** (25.5-25.6) |
| nemotron-3-nano:4b (thinking on) | 16384 | 2861 | 284.9 | **24.2** (24.2-24.3) |
| granite4:7b-a1b-h | 65536 | 3993 | 369.9 | **24.2** (19.7-28.7) |
| phi4-mini | 8192 | 3069 | 401.4 | **23.4** (23.3-23.4) |
| FrogNano-4B | 32768 | 3223 | 350.3 | **22.8** (22.8-22.8) |
| qwen3-abliterated:4b-instruct-2507 | 8192 | 3149 | 344.9 | **21.6** (21.5-21.6) |
| qwen3:4b | 8192 | 3149 | 345.9 | **21.6** (21.6-21.7) |
| e4b-e2b-aggressive (q2_k) | 32768 | 3907 | 426.1 | **20.6** (20.6-20.6) |
| Spark-X2.6-4B | 32768 | 3721 | 299.9 | **19.2** (19.2-19.2) |
| Qwen3.5-4B (text) | 32768 | 3497 | 324.8 | **18.3** (18.3-18.3) |
| Qwen3.5-4B + projector | 12288 | 3923 | 324.1 | **18.3** (18.3-18.3) |
| gemma4:e4b (text) | 16384 | 3551 | 463.2 | **17.4** (17.4-17.5) |
| bolt-e4b (thinking on) | 24576 | 3939 | 435.4 | **16.7** (16.7-16.7) |
| gemma4:e4b + projector | 16384 | 3647 | 195.6 | **1.5** (1.2-1.8) |

**What the table says.**

- **A projector can cost 11x, or nothing at all.** `gemma4:e4b` reads **17.4 t/s** as a text model and
  **1.5 t/s** with its projector loaded, same prompt, same 16k window; prefill falls from 463 to 196.
  `Qwen3.5-4B` reads **18.3 t/s** either way. So the cost is not "vision on a small card", it is "this
  model's projector does not fit beside its weights here" — gemma4:e4b is 6.58 GB on disk and does not fit
  the card even alone. Any vision comparison on a 4 GB card should publish the text-mode control beside it.
- **MoE wins on this card while spilling.** Moon-Beam-8B-A1B (**37.4 t/s** at 3,975 MiB) and lfm2-8b-a1b
  (**36.5** at 3,985) are the fastest 8B-class seats, above every dense 3-4B in the table, and both are
  holding nearly the whole card with the remainder in host RAM.
- **The fastest seat is the one with the smallest active set.** LFM3-1.7B: **45.2 t/s** in 1,441 MiB, with
  791 t/s prefill — about 2.6x a dense 4B's generation rate for a quarter of the memory.
- **One rep is not a measurement.** `ling3-tiny-64k` reads 27.0 on one rep and 41.4 on the other (median
  34.2); `granite4:7b-a1b-h` ranges 19.7 to 28.7. The test requires two reps and publishes the min and max
  with every row for this reason.
- **Against the retired stack where models overlap**, generation is 3-8 % higher on this build
  (granite3.3:2b 32.1 -> 34.0, granite4:3b-h 24.6 -> 26.9, MiniCPM5 33.2-34.9 -> 35.9) and prefill is
  within a few percent. The current build is not slower than the old one on this card.

**What this does not cover.** One prompt size (444 tokens) and one output length (200 tokens); a long-context
prefill is a different measurement and is not what these rows report. Thinking-on models were measured with
their serving preset's reasoning setting, so their generation rate includes thinking time. Context size is
the seat's fixed window and is not varied per row. Two models unloadable (above). Two reps per model.
