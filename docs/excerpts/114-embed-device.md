# §114 EMBED on one card: CPU vs GPU bge-m3, re-measured

> Excerpt of §114 of a private lab notebook; paths, service names and hosts are generalised. The text is
> synthetic and generated in code (`tests/embed_timing.py`, seed 20261116); no document from the lab is in
> this repo, and no vector is published, only the timings and the cosine between the two devices.

**Why.** The earlier decision (notebook §63) was to take the embedder off the 4 GB card: 664 MB of VRAM
for a query path that measured the same speed either way, against a slower batch import on the CPU. That
was measured through a retired serving stack. Both devices now run llama.cpp, so the pair was measured
again before publishing any of it.

**Method.** 8 synthetic chunks of 3,200 characters (about 800 tokens each) sent as one batched
`/v1/embeddings` call, then 10 warm single-query calls of 120 characters. Both sides read the same text
from the same seed. Scoring is code-side: milliseconds per chunk from the batch, median and p95 from the
query loop, and the minimum cosine between the two devices' vectors for the same input. Warm only; a cold
load is not part of this measurement.

| device | serving | 8 x 3,200 chars | ms/chunk | warm query (120 chars) median / p95 | VRAM |
|---|---|---|---|---|---|
| CPU | its own server, no GPU device, 3 threads, ctx 8192, batch 2048 | 27.83 s | **3,478.4** | 0.136 s / 0.154 s | 0 |
| GPU | a seat on the model router, all layers on the card, ctx 8192, batch 2048 | 3.74 s | **467.4** | **0.049 s** / 0.059 s | 633 MiB |

**Vectors are the same, and that is the point.** Minimum cosine **0.999990** across the 8 chunks, 1024
dimensions on both sides. The earlier measurement recorded 1.000000 through the previous stack; the last
five decimals are the CPU and GPU accumulation orders disagreeing, not a different model. Same weights
file, so an index built by one device is queryable by the other.

**Indexing is 7.44x slower on the CPU**, against 4.5x in the earlier measurement. Two changes explain the
gap: the CPU copy runs 3 threads in its own server (the old stack picked its own thread count), and the
GPU path here is a router seat rather than a batched call through the old API. The direction is
unchanged and so is the decision (a batch import is minutes either way, a query is milliseconds either
way), but the penalty for a full re-index on the CPU is larger than the earlier figure.

**What this keeps and what it changes.** The CPU copy stays the default because the reason was never
query speed, it was 664 MB of a card that holds one small model at a time. The GPU copy stays a
batch-import seat. What changes is the number to quote when the question is what a full re-index costs on
the CPU: 7.4x, not 4.5x. For scale, 10,000 chunks is about 9.7 hours on the CPU against 1.3 hours on the
card, and the resulting index is the same either way.

**n and limits.** One run per device, one batch size, one text source, one card. The two runs of the
query loop (n=10 each) are reported as median and p95 rather than a mean, because the distribution is
one slow call away from being skewed. Not measured: cold load, and what the GPU copy does when a
generation model is already resident (on this card it evicts the seat, which is why it is not the
default).
