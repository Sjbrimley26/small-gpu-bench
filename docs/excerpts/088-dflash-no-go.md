# §88 DFlash drafter on bolt-e4b, llama.cpp router: measured no-go

> Excerpt of §88 of a private lab notebook; paths, seat names and hosts are generalised.

Router build 11889, GTX 1050 Ti (4 GB). Main model bolt-e4b = UD-Q4_K_XL; drafter
`Nemotron-3.5-Bolt-E4B-DFlash-BF16.gguf` (736,812,096 B). The GPU was leased for the test: the other local
services were stopped and restored afterwards.

**Load.** The preset flags (`spec-draft-model`, `spec-type = draft-dflash`, `spec-draft-n-max`,
`spec-draft-ngl`) are accepted. First load died with CUDA OOM: the memory fitter cannot measure the drafter
("failed to measure the memory of the extra model, fitting without it"), so `fit-target = 64` leaves no room
for its weights (687 MiB) or its compute buffer (1049 MiB). `fit-target = 1200` still OOMed at the compute
buffer; **2200 loads** (all six arms; the CPU-drafter arm OOMed too at 64, because the DFlash compute buffer
stays on the GPU with ngl 0). `timings` does carry `draft_n` / `draft_n_accepted`.

**Results** (decode t/s median, range; acceptance = `draft_n_accepted/draft_n`; n as shown). The control is
the audit preset with thinking off at 16k, except on the audit workload, where it is the audit preset with
thinking on at 32k.

| workload, temp | arm | n | decode t/s | acceptance |
|---|---|---|---|---|
| chat 1.0 | control (audit preset, no-think arm, 16k) | 3 | 16.9 (16.7-17.0) | - |
| chat 1.0 | drafter arm, n-max 7 | 3 | 3.1 (2.5-3.4) | 21-22% |
| chat 1.0 | drafter arm, n-max 5 | 2 | 4.5 (3.5-5.4) | 28-30% |
| chat 1.0 | drafter arm, n-max 3 | 2 | 6.0 (5.7-6.3) | 37-40% |
| chat 1.0 | drafter arm, drafter on CPU | 2 | 3.6 (3.2-4.0) | 21-22% |
| chat 0.0 | control / drafter n-max 7 | 3 / 3 | 16.9 / 2.6 (2.1-3.0) | - / 20% |
| json 1.0 | control / drafter n-max 7 | 3 / 3 | 16.9 / 9.2 (7.6-12.1) | - / 86-94% |
| json 0.0 | control / drafter n-max 7 | 3 / 3 | 16.8 / 10.2 (6.3-12.7) | - / 94% |
| audit 1.0 (aud2, thinking on, 32k) | control (audit preset, thinking on) | 3 | 13.8 (13.8-13.8) | - |
| audit 1.0 | drafter arm, thinking on | 1 | 3.2 | 33% |

Every drafter cell is slower than its control. Audit workload: 3.2 / 13.8 = **0.23x** (rule needs
>= 1.3x). Even json, at 86-94% acceptance, decodes at 0.45-0.75x of control: the slowdown is per-step cost in
this build on this card, not only low acceptance. Cause not isolated (the log prints no layer-offload line to
show whether the fit pushed main-model layers to CPU). The CPU drafter decodes the same as the GPU drafter,
so drafter placement is not it. VRAM max: audit control 4015 MiB, drafter audit arm 3777 MiB (it fits at
32k, it is just slow).

**Temp-0 identity.** json: control == drafter, 3/3 identical each. chat: each arm is self-identical across
repeats but control and drafter differ (1158 vs 1250 chars). Unresolved: a mismatch is the bug signal the
plan named; the texts were not diffed and it was not tested whether it is batch-shape numerics or a real
defect.

**Quality gate (aud2 grader, answer text only).** Control r1/r2/r3 found 3/1/3 of 3; the drafter audit arm r1
found 2/3 (missed terms). With n=1 this is not a recall verdict; it does not contradict the speed no-go.

**Not run.** aud1, CAL 8/9 with the drafter, the drafter audit arm at n=3, n3/n5 on audit and json, the 32k
best-arm cell, the NVIDIA Q4_K_M control arm. Skipped because the decision rule fails on speed by 5x; none
could change it.

**Decision: no-go.** Reasons: decode 0.23x on the audit workload; acceptance 20-40% on prose (chat, audit),
high only on rigid JSON; the fitter cannot size the drafter, so a 2200 MiB reserve is needed. Vendor 2.74x
(RTX 5090, Q4_K_M) does not transfer. The audit presets were left unchanged.
