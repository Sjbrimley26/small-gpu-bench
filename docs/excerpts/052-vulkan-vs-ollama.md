# §52 llama.cpp (Vulkan) vs Ollama head-to-head on the MoE — Ollama wins

> Excerpt of §52 of a private lab notebook; paths, seat names and hosts are generalised.

Same GGUF (granite4 7B-A1B-H, Q4_K_M), `q8_0` KV, flash attention, ~1.28k-token prompt. Card emptied first
(`/api/ps` empty, nvidia-smi 7 MiB). Load average 1.3-2.4 on 4 vCPU.

| runtime | config | prefill t/s | gen t/s | VRAM |
|---|---|---|---|---|
| **Ollama 0.34.2 (CUDA 12)** | ctx 16384, FIT_TARGET=64 | **479-499** | **30.9-35.8** | 3837 of 4277 MiB resident (**90 % GPU**), nvidia-smi 3921 |
| llama.cpp `2851b3b`, Vulkan | `-ncmoe 0` (all on GPU) | — | — | **fails to load** (1.05 GB buffer alloc) |
| llama.cpp Vulkan | `-ncmoe 4` | 117.5 | 28.7 | — |
| llama.cpp Vulkan | `-ncmoe 8` | 114.6 | 27.7 | — |
| llama.cpp Vulkan | `-ncmoe 14` | 99.3 ± 9.7 | 18.2 ± 3.4 | — |
| llama.cpp Vulkan | `-ncmoe 22` | 107.3 | 21.1 | — |

(llama-bench pp1280/tg128, r=2; Ollama 3 runs, 256-token cap, temp 0.)

**Two findings, one of them a correction:**

1. **Ollama already does the MoE expert-offload trick.** At FIT_TARGET=64 the embedded llama-server's fit
   planner places the model at 90 % GPU — the "67 %" quoted in the notebook's earlier sections is the
   pre-policy stock-margin (1050 MiB) figure and is stale. The standalone `--n-cpu-moe` knob had nothing
   left to win.
2. **On Pascal, Vulkan is ~4x slower at prefill than Ollama's CUDA backend** and slightly slower at
   generation even at the best offload split. A fair standalone test would need a **CUDA 12** build
   (CUDA 13 drops compute 6.1) — which is exactly what Ollama ships, so it would at best tie.

**Verdict: no migration.** On this card standalone llama.cpp's remaining edges are narrow — speculative
decoding (n-gram, for copy-heavy extraction), `--swa-full` for Gemma prefix reuse, and new architectures
before Ollama supports them. Revisit only for one of those, and only with a CUDA-12 build.
