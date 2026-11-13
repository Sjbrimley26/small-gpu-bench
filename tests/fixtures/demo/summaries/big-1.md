The host spends a weekend testing Kestrel-9B, an open-weight mixture-of-experts model from Lumen Labs (9B total, 2.1B active parameters), run through llama.cpp with thinking on.

- Python snake game with curses: about 120 lines, works on the first run; a wall-collision bug was fixed in one follow-up.
- CSV deduplication of a 5,000-row file: after dropping pandas it kept 4,212 unique rows, verified by the host.
- Multi-file C++ text adventure: failed (missing header, eleven errors, no progress after three rounds).
- Long context (14,000 tokens): correct, with a quote; about 40 s prompt processing, about 31 tokens/s generation.
- Verdict: use it for small scripts and document reading, with a larger model for multi-file work. The license is a plus.
