# §113 RENT-SYN: rental-listing extraction on 100 invented listings, eight arms, and how the ranking compares with a private run

> Excerpt of §113 of a private lab notebook; paths, seat names and hosts are generalised. Every listing is invented
> (`tests/rent.py`, seed 20261115, `tests/fixtures/rent/listings.json`); no real listing, address, phone number or URL is in this repo.

**Why.** The lab's rental-listing test ran on 23 live listings that were hand-labelled and cannot be published. `tests/rent.py` rebuilds the task on 100
invented listings with gold known by construction. The model reads one listing (title, price field, attribute line such as "3BR / 2Ba 1200ft2", location,
body cut at 4000 characters) and must reply with strict JSON: `rent`, `beds`, `baths`, `kind`, `pets`, `town`, `red_flags`. The prompt, allowed values and
**checker** are the production ones, generalised. The checker rejects malformed JSON or anything that contradicts the listing's own structured fields; a
rejection is what would escalate the listing to a stronger model. Code, not the model, decides whether a listing is a deal.

**Fixture (16 tiers).** plain 12, pets stated in the attributes 6, in passing 6, mixed 6, absent 6, "no dogs" buried mid-body 6, cats-only 6, restricted
(55+) 5, room in a shared house 5, studio 4, half baths 4, price field vs body disagree 6, utilities-included wording 4, scam signs 12 (wire or gift-card
payment, owner abroad, no viewing, price far below area), non-rental kinds 6, very long bodies 6. llama.cpp router (build 12211), GTX 1050 Ti 4 GB, one model
resident, temperature 0, one pass. Thinking-off arms cap the reply at 512 tokens; thinking-on arms send a per-request reasoning budget of 256 tokens and cap
at 1280.

**Scored three ways.** *Checker pass* (shape plus consistency with the price and BR/Ba fields), *field accuracy* (rent, beds, baths, kind, pets exact), and
*red_flags* as precision and recall over flag categories, because several small models invent flags. "All fields+flags" means every field exact and exactly
the gold flag set.

| model (arm) | checker | all fields+flags | pets | flag precision / recall | spurious / missed | s/item |
|---|---|---|---|---|---|---|
| bolt-e4b, thinking on (budget 256) | 100 | **85** | 97 | **0.81** / 0.77 | 8 / 10 | 19.6 |
| gemma4:e4b, thinking on (budget 256) | 100 | 83 | 97 | 0.73 / 0.77 | 12 / 10 | 20.1 |
| gemma4:e4b, thinking off | 100 | 76 | 94 | 0.64 / **0.84** | 20 / 7 | 3.7 |
| bolt-e4b, thinking off | 100 | 67 | 94 | 0.53 / 0.77 | 29 / 10 | 3.8 |
| FrogNano-4B | 97 | 48 | 75 | 0.49 / 0.70 | 31 / 13 | 3.2 |
| granite4:3b-h | 85 | 45 | 66 | 0.37 / 0.54 | 39 / 20 | 3.0 |
| granite3.3:2b | 80 | 22 | 35 | 0.44 / 0.65 | 36 / 15 | 2.6 |
| ling3-tiny | 92 | 16 | 68 | **0.13** / 0.84 | **235** / 7 | 2.5 |

Rent and town are solved by every model; pets, flags and (for the weak models) kind carry the ranking. **The checker is near-saturated** (100/100 for the
three models that matter), so it cannot rank the top of the table the way it did on the private set.

**What the numbers say.**
* **Thinking buys precision, not recall.** bolt-e4b goes from 29 to 8 spurious flags and from 67 to 85 on all fields; gemma4:e4b from 20 to 12 and 76 to 83.
  The missed flags do not move (10 and 10; 7 and 10). The price is about 5x the time and 6x the tokens. The per-request switch took effect on both models
  (33785 tokens at 20 s/item against 5450 at 3.7 s on the same model); no reply hit the 1280 cap.
* **A prompt-echo habit explains most of the thinking-off precision loss.** 26 of bolt-e4b's 29 spurious flags are the literal string "no viewing" on listings
  that invite a viewing: it copies the example list in the prompt. gemma4:e4b does it 14 times, FrogNano 11, ling3-tiny 31; with thinking on it is 1 and 0.
* **Every bolt/gemma arm misses the same flags:** 2 "gift card" and 2 "price far below area" (all in the scam tier) and 3 to 6 buried "no dogs" (three of those
  lie past the 4000-character cut and cannot be found).
* **ling3-tiny invents flags** (235 spurious, 2.35 per listing, 127 of them strings that map to no category): high recall because it flags everything, and
  not usable here. **FrogNano** is fast but weak on `pets` (75) and on beds and baths. The two granite models fail the checker mostly by writing a `pets`
  value outside the allowed list (17 of 20 and 12 of 15 failures).

**How the synthetic set differs from the private one.** The private set is 23 live listings, hand-labelled, run three times at temperature 0 (checker /
pets / red_flags out of 23 or 21): bolt-e4b thinking off 21 / 18 / 15, gemma4:e4b 21 / 18 / 14, FrogNano 21 / 12 / 12, ling3-tiny 15 / 9 / invented flags,
bolt-e4b thinking on with a 256-token budget 23 / 14 / 19, plus a 26B model on a second machine at 21. The synthetic set is invented, bigger (100), has an
easier `pets` wording, and was run once per arm.
*The rankings agree on the order and on what thinking buys:* bolt and gemma4:e4b tie on pets (94 each; 18 each private), FrogNano's weakness is pets (75;
12/21 private), ling3-tiny is last of the common models and invents flags, thinking-on bolt is the top arm, and thinking costs about 5x. *They disagree on
difficulty:* the checker saturates (100 against 91% private), ling3-tiny passes it 92% here against 65% private, pets is easier for the top models (94-97%
against 86%), and the private finding that thinking lowers `pets` (14/21 against 18/21) while raising flag recall (15 to 19 of 23) is **not reproduced**: here
thinking raises pets (94 to 97) and flag precision (0.53 to 0.81) and leaves recall unchanged. One pass per arm at temperature 0: read one to three listings
as noise, but the spurious-flag counts (8, 12, 20, 29) are large enough to read.

**Not run.** granite4.2:3b (both arms) and LFM3-1.7B were queued but not measured: the GPU fell off the PCIe bus (Xid 79, "GPU has fallen off the bus")
during the first request to granite4.2, so their runs failed on transport errors. Those records are discarded, not scored. A 26B remote model was also
not reachable. They are the next runs once the card is reset.

**Reproduce.** `python3 tests/rent.py --selftest`, then `MODEL=<model> python3 tests/rent.py --think off` or `--think on --reasoning-budget 256` against an
OpenAI-compatible endpoint (`BASE_URL`, `API_KEY`). `--abort-after 15 --abort-bad 4` (default) stops an arm early if 4 of its first 15 replies hit the token
cap or a transport error and records it as aborted. `tests/smoke.sh` runs the whole thing against the mock server.
