# §40.1c The reranker's micro-batch size is a hard input ceiling — size it from the chunk, not the flag default

> Excerpt of §40.1c of a private lab notebook; paths, seat names and hosts are generalised.

With `--rerank`, `llama-server` clamps `n_batch = n_ubatch`, and any single document above that size returns
**HTTP 500 `input (N tokens) is too large to process. increase the physical batch size`**. Since
`chunk_size=2000` means ~2,200-token chunks, the value shipped first (`-ub 2048`) **failed on the live
store** — 2,171 tokens > 2,048. Measured ceiling:

| ubatch | ~937-token doc | ~2,171-token chunk (real) |
|---|---|---|
| 768 | 500 | 500 |
| 1024 | ok | 500 |
| 2048 | ok | **500 (this was shipped — wrong)** |
| 4096 | ok | ok |

`-b 4096 -ub 4096` is the shipped value: it covers a 4,000-token chunk (2x the current setting) for about
+250 MB. Startup RSS is flat at **755 MB** for every ubatch (the model is only 636 MB; the compute buffers
are small) — but a long-lived process that has served a large batch grows far beyond that (measured
3.3-4.3 GB after 15 real chunks). Restarting returns it to 755 MB. `Nice=10` keeps it yielding to the other
services on the host.

Measured on the CPU, not the GPU, with `llama-server --rerank` and `bge-reranker-v2-m3-Q8_0` (636 MB).
One model, two document sizes; the section does not state repeat counts per cell.
