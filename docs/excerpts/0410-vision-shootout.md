# §4.10 Vision shootout (tests 10–11)

> Excerpt of §4.10 of a private lab notebook; paths, seat names and hosts are generalised.

Seven questions over four images (a film poster, a wordmark logo, a film backdrop, an icon):

| model | score | wall | residency | img-tokens |
|---|---|---|---|---|
| `gemma3:4b` | **16** | **99.7 s** | 3.82 GB, 55 % | 344 |
| `qwen2.5vl:3b` | 15 | 555.6 s | 3.67 GB, 29 % | 2,852 |
| `moondream:latest` | 14 | 27.4 s | 1.24 GB, 100 % | 811 |
| `qwen3.5:4b` | 14 | 483.0 s | 4.17 GB, 48 % | 2,224 |
| `granite3.2-vision:2b` | 13 | 219.0 s | 3.05 GB, 71 % | 7,402 |

Synthetic invoice page, drawn in code. Planted values: invoice 88213, PO 4471, date 2026-09-14, total
1345.99, and a fictional vendor name.

| model | score | wall | note |
|---|---|---|---|
| `gemma3:4b` | 4/5 | **25.9 s** | returned `1345.9` — dropped the last digit |
| `qwen2.5vl:3b` | 4/5 | 95.4 s | `1345.90` |
| `qwen3.5:4b` | 4/5 | 122.8 s | `1345.9` |
| `granite3.2-vision:2b` | 4/5 | 147.9 s | OCR dump; garbled words ("DOOR SLAD") |
| `moondream:latest` | **0/5** | 56.0 s | "Invoices are printed on white paper." |

Pointing (returning coordinates for an object): only `qwen2.5vl:3b` can — it returned
`[{"point_2d": [720, 640], "label": "car"}]`; `gemma3:4b` answered `(14, 78)`; `moondream` returned nothing.

Hard limits found while testing: images smaller than ~64 px are rejected outright (`Failed to load image`),
and sending an image to a text-only model is a hard 400 (`does not support multimodal requests`).

## Addendum (2026-11-12): the "dropped the last digit" column is a fixture bug, not a model error

The page was drawn on a 1240 px canvas, and each text row is `len(text) * 6 * scale` px wide starting at
x0 = 60. The 41-char amount rows therefore end at x = 60 + 41*30 = 1290, and the 40-char subtotal/tax/total
rows at 1260 — every character right of x = 1240 was cut off the image. So `1345.99` was rendered as
`1345.9`, and `gemma3:4b`'s `1345.9`, `qwen2.5vl:3b`'s `1345.90` and `qwen3.5:4b`'s `1345.9` were all
**correct reads of what the page actually showed**; the "4/5, missed the total" scores measure the canvas,
not the models. The fixture-drawing script is now 1360 px wide, so the rows above remain an artifact of the
old fixture and a re-run is owed. The drawn-invoice fixture lives only in that one script; the lab's other
rasterising script used a different corpus and is unaffected.
