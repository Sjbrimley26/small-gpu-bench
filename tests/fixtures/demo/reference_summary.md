Summary of the video.

- Kestrel-9B is a new open-weight model from Lumen Labs: 9B total / 2.1B active parameters (mixture of experts), permissive license, run locally through a llama.cpp server with thinking enabled.
- These are first impressions after a weekend, not a benchmark; four tests were run.
- Snake game (Python, curses): about 120 lines, ran on the first try; the wall-collision bug was fixed in the second version. Pass.
- CSV dedupe script: the first attempt imported pandas (not installed); rewritten with the csv module it kept 4,212 unique rows of 5,000, which was correct. Pass after one nudge.
- Multi-file C++ text adventure: failed. Missing header file, eleven build errors, still going in circles after three rounds; stopped after about forty minutes.
- Long-context test: a 14,000-token meeting transcript; answered correctly (Dana from finance) and quoted its source. About 40 seconds of prompt processing and about 31 tokens per second generation.

RESULTS
- Good: Python scripting, long-context retrieval, speed for a 9B model, the license.
- Bad: multi-file C++.

OVERALL
A decent small model. Use it for small scripts and reading documents; keep a bigger model for work that spans many files.
