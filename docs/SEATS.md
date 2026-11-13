# What a "seat" is

The results and the excerpts talk about **seats** (also called presets in the notebook). A seat is a
named serving configuration, not a model file:

- one model blob, at a chosen quantisation (`model` column carries it, for example `gemma4:e4b`);
- a chosen context window (a seat name may carry a suffix, for example `checker preset (64k)`);
- fixed defaults: sampling, a `think`/reasoning switch (`--think on|off` on llama.cpp, which sends
  `chat_template_kwargs.enable_thinking`), and the request mode for the task (tool calling, JSON
  schema, vision off, and so on).

These are registered by name on the lab's router — a llama.cpp server that holds one model resident at
a time and swaps on request. The card in the lab has 4 GB of VRAM, so only one seat can be resident;
swapping the model is the expensive step, which is why the lab leases the card for a run and restores
the previous seat afterwards, and why a run that loses the card mid-way reports itself "evicted" rather
than limping on.

Why this matters for the numbers: the same model behaves differently through different seats — reasoning
on or off, a 16k vs a 64k context, a JSON-schema request versus free text — so the row's seat is part of
the result, which is why the CSV's `notes` carry it. The published rows use role words instead of the
lab's internal seat names; the mapping is in [RESULTS-NOTES.md](RESULTS-NOTES.md). In the results the
roles mean roughly:

| Role word in the CSV | What it does |
|---|---|
| `writer preset` | the default assistant/summarising seat used for prose tasks |
| `audit preset` | the document-audit seat (planted-error hunting; a `(no-think arm)` variant runs it with reasoning off) |
| `checker preset` | the verdict/verify seat behind the claim checker (context suffixes like `(64k)` belong to this seat) |
| `summary preset` | the SUM/CRIT seats of the summarize-verify experiments |
| `aux preset` | small utility seats (extraction, monitoring) that run when the big seat isn't needed |
| `26B remote seat` | an outsized model reached over the local router, not resident on the GPU card |

To reproduce a row's shape you don't need any of this: point the test scripts at any
OpenAI-compatible endpoint and set the equivalent options yourself (`BASE_URL`, `MODEL`, and for
reasoning models the `--think` flag shown in the README). Seats are the lab's way of naming that
combination so results stay comparable.
