#!/usr/bin/env python3
"""RENT (synthetic): extract the facts of one rental listing as strict JSON, then let a checker veto the reply.

The lab's original rental test used 23 live, hand-labelled classified listings (real text, real addresses), so
it cannot be published. This is a rebuild on invented data: every listing is generated in code from a seed
(`--gen`), the gold answer is known by construction, and nothing in tests/fixtures/rent/ comes from a real ad.

The task (same contract as the lab's production extractor): given one listing (title, price field, attributes
line, location, body cut to 4000 characters) the model returns ONE JSON object with
  rent (monthly USD), beds, baths, kind, pets, town, red_flags
and only extracts. Whether the listing is a deal is decided elsewhere, in code. A checker (`check()` below, a
verbatim port of the production one) rejects malformed JSON, off-list `kind` / `pets` values, and any reply
that contradicts the listing's own structured fields (price field, "NBR" attribute, the dogs/cats attributes).
A rejected reply is what would send the item to a stronger model, so the checker-fail rate is the
"escalation rate".

Two scores, because they disagree:
  checker pass   shape + consistency with the structured fields (what the pipeline can see by itself)
  field accuracy rent, beds, baths, kind, pets, town exact against gold; red_flags as precision / recall,
                 because small models invent flags. A flag string is mapped to a canonical category by regex;
                 a string that maps to no category, or to a category the gold lacks, is a spurious flag.
Listings come in tiers by construction (plain, pets stated in the attributes / in passing / mixed / absent,
"no dogs" buried mid-body, cats-only, 55+ and section-8 restrictions, room for rent, studio, half baths,
price field vs body, utilities wording, scam signs, non-housing kinds, very long bodies).

  python3 tests/rent.py --selftest
  python3 tests/rent.py --gen                       # rewrite tests/fixtures/rent/listings.json from the seed
  BASE_URL=... MODEL=... python3 tests/rent.py [--limit N] [--think off|on] [--reasoning-budget 256]

Arms: `--think off` (thinking disabled, the plain arm) and `--think on --reasoning-budget 256` (thinking on,
capped by llama.cpp's per-request `reasoning_budget_tokens`). Temperature 0, one reply per listing.
"""
import argparse
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SEED = 20261115
FIXTURE = common.fixture_path("rent", "listings.json")

# ---------------------------------------------------------------- prompt + checker (ported from the production task)
KINDS = ["house", "duplex", "apartment", "condo", "townhouse", "mobile home", "room", "other"]
PETS = ["dogs ok", "cats only", "no pets", "unclear"]

SYSTEM = f"""You extract facts from one rental listing. Reply with ONE JSON object and nothing else:
{{"rent": int monthly USD or null,
 "beds": number or null,
 "baths": number or null,
 "kind": one of {KINDS},
 "pets": one of {PETS},
 "town": string,
 "red_flags": list of short strings (scam signs: wire/gift-card payment, owner abroad, rent far below area, no viewing; or deal-breakers hidden in the text such as "no dogs", "55+ only", "room for rent", "section 8 only"); [] if none}}
Use only what the listing says. "pets" is "dogs ok" only if dogs are explicitly allowed."""


def build_messages(item: dict) -> list[dict]:
    body = (f"Title: {item['title']}\nPrice field: {item.get('price')}\n"
            f"Attributes: {item.get('attrs')}\nLocation: {item.get('location')}\n\n{item['body'][:4000]}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}]


def _num(s):
    m = re.search(r"\d+(?:\.\d+)?", str(s or "").replace(",", ""))
    return float(m.group()) if m else None


def check(item: dict, text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return False, None, "no JSON object in reply"
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError as e:
        return False, None, f"invalid JSON: {e}"
    for k in ("rent", "beds", "baths", "kind", "pets", "town", "red_flags"):
        if k not in d:
            return False, None, f"missing key {k!r}"
    if d["kind"] not in KINDS:
        return False, None, f"kind must be one of {KINDS}"
    if d["pets"] not in PETS:
        return False, None, f"pets must be one of {PETS}"
    if not isinstance(d["red_flags"], list):
        return False, None, "red_flags must be a list"
    # cross-check against craigslist's own structured fields
    price = _num(item.get("price"))
    if price and d["rent"] is not None and abs(float(d["rent"]) - price) > max(25, 0.05 * price):
        if not re.search(re.escape(str(int(float(d["rent"])))), item["body"].replace(",", "")):
            return False, None, f"rent {d['rent']} disagrees with the price field {price} and is not in the text"
    br = re.search(r"(\d+)BR", item.get("attrs", ""), re.I)
    if br and d["beds"] is not None and float(d["beds"]) != float(br.group(1)):
        return False, None, f"beds {d['beds']} disagrees with listing attribute {br.group(1)}BR"
    attrs = item.get("attrs", "").lower()
    if "dogs are ok" in attrs and d["pets"] in ("no pets", "cats only"):
        return False, None, "listing attributes say dogs are OK"
    if "dogs are ok" not in attrs and "cats are ok" in attrs and d["pets"] == "dogs ok" \
            and not re.search(r"\bdogs?\b", item["body"], re.I):
        return False, None, "attributes allow cats only and the text never mentions dogs"
    return True, d, "ok"


# ---------------------------------------------------------------- red flags: canonical categories
FLAGS = ["wire payment", "gift card", "owner abroad", "no viewing", "price far below area", "no dogs",
         "55+ only", "room for rent", "section 8 only"]
# how a MODEL's free-text flag is mapped to a category (deliberately generous)
FLAG_RX = [
    ("wire payment", re.compile(r"wire|western union|money ?gram", re.I)),
    ("gift card", re.compile(r"gift ?card|prepaid card", re.I)),
    ("owner abroad", re.compile(r"abroad|overseas|out of (the )?(country|state)|outside (of )?(the )?(us|country)|oil rig|"
                                r"not (local|in (town|the country))|owner.{0,15}(away|elsewhere)", re.I)),
    ("no viewing", re.compile(r"no (viewings?|showings?|tours?|visits?|inspections?)|(cannot|can't|unable to) "
                              r"(view|show|tour|visit)|sight[- ]unseen|without (viewing|seeing)|unseen|keys? (by |via )?mail|"
                              r"mail(ed|ing)? (the )?keys?", re.I)),
    ("price far below area", re.compile(r"below (the )?(market|area|typical|average)|too good|far below|suspiciously|"
                                        r"unusually (low|cheap)|under-?priced|(low|cheap) (rent|price)|price.{0,20}(low|cheap)", re.I)),
    ("no dogs", re.compile(r"no dogs?|dogs? (are |is )?(not|n't) (allowed|permitted|ok|accepted)|no pets|no animals|pets? not", re.I)),
    ("55+ only", re.compile(r"\b55\b|55\+|senior|age[- ]restrict|retirement|adults? (only|communit)|active adult", re.I)),
    ("room for rent", re.compile(r"room (for rent|rental)|rent(ing)? (a|one) room|room only|shared (house|home)|roommate|"
                                 r"single room|private room", re.I)),
    ("section 8 only", re.compile(r"section ?8|voucher|housing choice", re.I)),
]


def categorize_flags(model_flags):
    """-> (set of categories hit, number of strings that map to no category)."""
    hit, unmatched = set(), 0
    for f in model_flags:
        s = str(f)
        cs = [c for c, rx in FLAG_RX if rx.search(s)]
        if cs:
            hit.update(cs)
        else:
            unmatched += 1
    return hit, unmatched


def flag_score(model_flags, gold_flags, lenient=()):
    """-> (tp, fp, fn). `lenient` categories (a plain 'no pets' or 'cats only' text makes 'no dogs' optional)
    count neither for nor against. Duplicates of a category collapse to one."""
    hit, unmatched = categorize_flags(model_flags if isinstance(model_flags, list) else [])
    gold = set(gold_flags)
    tp = len(hit & gold)
    fp = unmatched + len(hit - gold - set(lenient))
    fn = len(gold - hit)
    return tp, fp, fn


# ---------------------------------------------------------------- generator (all names and text are invented)
SYL_A = ["Vel", "Kor", "Tam", "Quin", "Dra", "Zan", "Yar", "Hesk", "Nol", "Ost", "Cav", "Brev", "Ixen", "Mav", "Ulm", "Jorv"]
SYL_B = ["tran", "vora", "kry", "dell", "sen", "wick", "ari", "bost", "lin", "gard", "pell", "mere"]
TOWN_SUF = ["ville", "dale", "ford", "haven", "mont", "field", "burg", "ridge"]
STREET_TYPE = ["St", "Ave", "Ln", "Dr", "Ct", "Way", "Rd"]
LANDMARKS = ["the old mill", "the community college", "the rail trail", "the farmers market", "the hospital", "Main Street shops", "the lake"]
# real places must never appear: the selftest scans for these
REAL_PLACE_DENYLIST = ["Phoenix", "Tucson", "Mesa", "Denver", "Austin", "Boise", "Reno", "Tacoma", "Omaha", "Tulsa", "Seattle",
                       "Portland", "Dallas", "Houston", "Chicago", "Atlanta", "Miami", "Boston", "Sacramento", "Spokane",
                       "Flagstaff", "Prescott", "Yuma", "Albuquerque", "Las Vegas", "Salt Lake", "Craigslist", "craigslist"]
BED_WORD = {1: "one", 2: "two", 3: "three", 4: "four"}
BATH_WORD = {1: "one", 1.5: "one and a half", 2: "two", 2.5: "two and a half", 3: "three"}
KIND_PHRASE = {"house": ["house", "single-family home", "home"], "duplex": ["duplex", "half of a duplex"],
               "apartment": ["apartment", "apartment"], "condo": ["condo", "condominium"],
               "townhouse": ["townhouse", "townhome"], "mobile home": ["mobile home", "manufactured home"]}
KIND_ATTR = {"house": "house", "duplex": "duplex", "apartment": "apartment", "condo": "condo", "townhouse": "townhouse",
             "mobile home": "manufactured", "room": "room", "other": ""}
TYPICAL_RENT = {"house": 1000, "duplex": 800, "apartment": 700, "condo": 850, "townhouse": 900, "mobile home": 600}  # + per bed below
PER_BED = {"house": 330, "duplex": 280, "apartment": 260, "condo": 290, "townhouse": 300, "mobile home": 220}

FEATURES = ["Washer and dryer hookups.", "New carpet and fresh paint throughout.", "Central air and heat.",
            "Off-street parking for two cars.", "Granite counters in the kitchen.", "A covered patio out back.",
            "Dishwasher and gas range included.", "Close to the elementary school.", "Ceiling fans in every room.",
            "Updated bathroom with a tub and shower.", "Lots of closet space.", "Quiet street with little traffic.",
            "Water, sewer and trash are paid by the owner.", "Lawn care is handled for you.", "Brand new water heater.",
            "Laminate floors, no carpet in the main rooms.", "Bright kitchen with a pantry.", "Storage shed on the lot."]
FILLER = ["The area has an easy commute to the main highway.", "Neighbors here have been in place for years and keep to themselves.",
          "Trash pickup is on Tuesdays.", "Renters insurance is required before move-in.", "Smoking is not allowed inside.",
          "We run a credit and background check on every adult applicant.", "The lease is twelve months with a option to renew.",
          "Utilities not listed here are paid by the tenant.", "The owner has kept this place maintained for a long time.",
          "Repairs are handled within a few days of a request.", "There is a grocery store a short drive away.",
          "The lot is on a gentle slope with good drainage.", "Schools in the district have a good reputation locally.",
          "Rent is due on the first and late after the fifth.", "Please read the whole ad before you reach out.",
          "Income should be about three times the rent.", "We prefer tenants who plan to stay at least a year.",
          "Snow and ice removal on the driveway is up to the tenant.", "The furnace was serviced this fall.",
          "Photos were taken last month."]
STATE_CODES = ["AZ", "CO", "TX", "ID", "NV", "WA"]

TIER_COUNTS = {"plain": 12, "pets_attrs": 6, "pets_passing": 6, "pets_mixed": 6, "pets_absent": 6, "nodogs_buried": 6,
               "cats_only": 6, "restricted": 5, "room": 5, "studio": 4, "half_baths": 4, "price_disagree": 6,
               "utilities": 4, "scam": 12, "other_kind": 6, "long_body": 6}
TIER_ORDER = list(TIER_COUNTS)
SCAM_COMBOS = [["wire payment"], ["wire payment", "owner abroad"], ["wire payment", "no viewing"], ["gift card"],
               ["gift card", "owner abroad"], ["owner abroad"], ["owner abroad", "no viewing"], ["owner abroad", "wire payment"],
               ["no viewing"], ["no viewing", "wire payment"], ["price far below area"], ["price far below area", "no viewing"]]
SCAM_TEXT = {"wire payment": ["The deposit has to be sent by wire transfer before anything else happens.",
                              "Payment of the first month goes by Western Union wire, and the keys follow once it is received."],
             "gift card": ["We only take the deposit as gift cards, and will tell you which ones when you write."],
             "owner abroad": ["The owner is working overseas on an oil rig for the year, so a property manager handles the paperwork by email.",
                              "I am out of the country for work right now and everything is done by email."],
             "no viewing": ["No viewings or showings until the deposit has cleared; the photos speak for themselves."]}
# a regex per category that fires on the BODY text of a gold flag (used by the selftest, not by scoring)
TRIGGER = {"wire payment": re.compile(r"wire transfer|western union", re.I), "gift card": re.compile(r"gift cards?", re.I),
           "owner abroad": re.compile(r"overseas|out of the country", re.I), "no viewing": re.compile(r"no viewings", re.I),
           "no dogs": re.compile(r"\bno dogs\b", re.I), "55+ only": re.compile(r"55 or older|55\+", re.I),
           "room for rent": re.compile(r"room for rent", re.I), "section 8 only": re.compile(r"section 8", re.I)}


class Gen:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.used_towns, self.used_streets = set(), set()

    def name(self):
        r = self.rng
        return r.choice(SYL_A) + r.choice(SYL_B)

    def town(self):
        for _ in range(500):
            t = self.name() + self.rng.choice(TOWN_SUF)
            if t not in self.used_towns:
                self.used_towns.add(t)
                return t
        raise RuntimeError("town space exhausted")

    def street(self):
        for _ in range(500):
            s = "%d %s %s" % (self.rng.randint(100, 9899), self.name(), self.rng.choice(STREET_TYPE))
            if s not in self.used_streets:
                self.used_streets.add(s)
                return s
        raise RuntimeError("street space exhausted")

    def phone(self):
        return "555-01%02d" % self.rng.randint(0, 99)        # 555-0100..0199 is the reserved fictional range

    def money(self, n):
        return "${:,}".format(int(n))

    def rent_for(self, kind, beds):
        base = TYPICAL_RENT[kind] + PER_BED[kind] * (beds or 1)
        return int(round(base * self.rng.uniform(0.9, 1.2) / 25)) * 25

    def spec(self, kind, beds=None, baths=None):
        r = self.rng
        if kind in ("house", "townhouse"):
            beds = beds or r.choice([2, 3, 3, 4])
            baths = baths or r.choice([1, 1.5, 2, 2, 2.5])
        elif kind == "mobile home":
            beds = beds or r.choice([2, 3])
            baths = baths or r.choice([1, 2])
        elif kind in ("duplex", "condo"):
            beds = beds or r.choice([2, 2, 3])
            baths = baths or r.choice([1, 1.5, 2])
        else:   # apartment
            beds = beds or r.choice([1, 2, 2])
            baths = baths or r.choice([1, 1, 2])
        sqft = int(round((350 + beds * 330 * r.uniform(0.8, 1.3)) / 10)) * 10
        return {"kind": kind, "beds": beds, "baths": baths, "sqft": sqft, "town": self.town(), "street": self.street(),
                "rent": self.rent_for(kind, beds)}

    # ---- text pieces
    def attrs(self, s, pets_tokens=(), show_kind=True, no_br=False):
        r = self.rng
        parts = []
        if not no_br:
            parts.append("%dBR / %sBa %dft2" % (s["beds"], ("%g" % s["baths"]), s["sqft"]))
        else:
            parts.append("%sBa %dft2" % (("%g" % s["baths"]), s["sqft"]))
        if show_kind and KIND_ATTR.get(s["kind"]):
            parts.append(KIND_ATTR[s["kind"]])
        parts.extend(pets_tokens)
        extra = r.sample(["w/d hookups", "laundry in bldg", "off-street parking", "carport", "no smoking", "air conditioning"], r.randint(0, 2))
        parts.extend(extra)
        return " · ".join(parts)

    def title(self, s, flavor=""):
        r = self.rng
        adj = r.choice(["Updated", "Spacious", "Cozy", "Bright", "Quiet", "Clean", "Charming", "Roomy"])
        ph = r.choice(KIND_PHRASE.get(s["kind"], ["place"]))
        t = "%s %dBR %s near %s" % (adj, s["beds"], ph, r.choice(LANDMARKS))
        return t + flavor

    def intro(self, s):
        r = self.rng
        bw, kw = BED_WORD.get(s["beds"], str(s["beds"])), r.choice(KIND_PHRASE.get(s["kind"], ["place"]))
        bathw = BATH_WORD.get(s["baths"], str(s["baths"]))
        return r.choice(["%s %s bedroom, %s bath %s on %s." % ("A", bw, bathw, kw, s["street"]),
                         "This %s-bedroom %s has %s bath%s and about %d square feet." % (bw, kw, bathw, "" if s["baths"] == 1 else "s", s["sqft"]),
                         "Available now: %s bedrooms, %s baths, %s on %s." % (bw, bathw, kw, s["street"])])

    def rent_line(self, rent):
        return self.rng.choice(["Rent is %s per month." % self.money(rent), "%s a month, first month and deposit due at signing." % self.money(rent),
                                "Asking %s/mo on a twelve month lease." % self.money(rent)])

    def tail(self):
        return self.rng.choice(["Text %s to schedule a viewing." % self.phone(), "Call or text %s to see it this week." % self.phone(),
                                "To see it, text %s." % self.phone()])

    def build_body(self, s, rent_sentence, pets_sentence="", extra=(), n_feat=3, tail=True, filler=0):
        r = self.rng
        parts = [self.intro(s)] + r.sample(FEATURES, n_feat)
        parts.append(rent_sentence)
        parts.append("Application fee is $%d, deposit is one month's rent." % r.choice([35, 45, 50]))
        if pets_sentence:
            parts.append(pets_sentence)
        parts.extend(extra)
        parts.extend(r.sample(FILLER, filler))
        if tail:
            parts.append(self.tail())
        return " ".join(parts)

    def pad_to(self, n_chars, avoid=()):
        """Neutral filler prose until the text is at least n_chars long."""
        out, size = [], 0
        pool = list(FILLER) + list(FEATURES)
        while size < n_chars:
            s = self.rng.choice(pool)
            out.append(s)
            size += len(s) + 1
        return " ".join(out)


def generate(seed=SEED):
    g = Gen(seed)
    r = g.rng
    rows = []

    def row(tier, s, title, price, attrs, body, gold_pets, flags, lenient=(), rent=None, beds=None, baths=None, kind=None, location=None):
        gold = {"rent": s["rent"] if rent is None else (None if rent == "null" else rent),
                "beds": s["beds"] if beds is None else (None if beds == "null" else beds),
                "baths": s["baths"] if baths is None else (None if baths == "null" else baths),
                "kind": kind or s["kind"], "pets": gold_pets, "town": s["town"], "red_flags": sorted(flags)}
        rows.append({"tier": tier, "title": title, "price": price, "attrs": attrs, "location": location or s["town"],
                     "body": body, "gold": gold, "lenient_flags": sorted(lenient)})

    plain_kinds = ["house", "duplex", "apartment", "condo", "townhouse", "mobile home"]
    DOGS_PLAIN = ["Dogs welcome with a $300 pet deposit.", "Pet friendly: dogs and cats are both fine.", "Dogs are allowed, two maximum."]
    CATS_PLAIN = ["Cats are welcome, one or two. Cats only.", "Sorry, cats only (one cat, $200 deposit)."]
    NOPETS_PLAIN = ["No pets.", "No pets allowed, no exceptions.", "Pets are not permitted in this building."]

    # plain: all fields stated, pets stated plainly in the body (4 dogs ok / 4 cats only / 4 no pets)
    for i in range(TIER_COUNTS["plain"]):
        kind = plain_kinds[i % len(plain_kinds)]
        s = g.spec(kind)
        mode = i % 3
        if mode == 0:
            pets, sent, lenient = "dogs ok", r.choice(DOGS_PLAIN), ()
        elif mode == 1:
            pets, sent, lenient = "cats only", r.choice(CATS_PLAIN), ("no dogs",)
        else:
            pets, sent, lenient = "no pets", r.choice(NOPETS_PLAIN), ("no dogs",)
        body = g.build_body(s, g.rent_line(s["rent"]), sent)
        row("plain", s, g.title(s), g.money(s["rent"]), g.attrs(s, show_kind=r.random() < 0.6), body, pets, [], lenient)

    # pets_attrs: the body is silent; the attributes line carries it
    for i in range(TIER_COUNTS["pets_attrs"]):
        s = g.spec(plain_kinds[(i * 2 + 1) % len(plain_kinds)])
        if i % 2 == 0:
            tok, pets = ("cats are OK - purrr", "dogs are OK - wooof"), "dogs ok"
        else:
            tok, pets = ("cats are OK - purrr",), "cats only"
        body = g.build_body(s, g.rent_line(s["rent"]))
        row("pets_attrs", s, g.title(s), g.money(s["rent"]), g.attrs(s, pets_tokens=tok), body, pets, [], ("no dogs",) if pets == "cats only" else ())

    # pets_passing: dogs allowed, said in passing among other sentences
    PASSING = ["The back lot is great for the one dog you may bring ($300 deposit).", "There is a dog run out back and we welcome dogs; ask about the deposit.",
               "Walk to the dog park; dogs under 50 lb are welcome here too."]
    for i in range(TIER_COUNTS["pets_passing"]):
        s = g.spec(["house", "townhouse", "duplex"][i % 3])
        body = g.build_body(s, g.rent_line(s["rent"]), r.choice(PASSING), filler=2)
        row("pets_passing", s, g.title(s), g.money(s["rent"]), g.attrs(s, show_kind=False), body, "dogs ok", [])

    # pets_mixed: attributes and body each say part of it
    for i in range(TIER_COUNTS["pets_mixed"]):
        s = g.spec(plain_kinds[(i + 2) % len(plain_kinds)])
        mode = i % 3
        if mode == 0:     # attrs cats only, body says dogs welcome too
            tok, sent, pets, flags = ("cats are OK - purrr",), "Dogs are welcome too, one dog under 40 lb.", "dogs ok", []
        elif mode == 1:   # attrs dogs ok, body says small dogs only, no cats
            tok, sent, pets, flags = ("dogs are OK - wooof",), "Small dogs only, 25 lb limit.", "dogs ok", []
        else:             # attrs cats ok, body says no dogs
            tok, sent, pets, flags = ("cats are OK - purrr",), "Cats are fine. Dogs: sorry, no dogs.", "cats only", ["no dogs"]
        body = g.build_body(s, g.rent_line(s["rent"]), sent)
        row("pets_mixed", s, g.title(s), g.money(s["rent"]), g.attrs(s, pets_tokens=tok), body, pets, flags)

    # pets_absent: nothing said, or only non-answers ("ask", "case by case")
    VAGUE = ["", "", "Ask us about pets.", "Pets are considered case by case.", "", "Pet policy: contact the office."]
    for i in range(TIER_COUNTS["pets_absent"]):
        s = g.spec(plain_kinds[(i * 5) % len(plain_kinds)])
        body = g.build_body(s, g.rent_line(s["rent"]), VAGUE[i % len(VAGUE)], filler=2)
        row("pets_absent", s, g.title(s), g.money(s["rent"]), g.attrs(s), body, "unclear", [])

    # nodogs_buried: long body, "No dogs." in the middle, cats said to be fine
    for i in range(TIER_COUNTS["nodogs_buried"]):
        s = g.spec(["house", "duplex", "townhouse"][i % 3])
        mid = "Cats are okay with a deposit. No dogs, no exceptions."
        parts = [g.intro(s), " ".join(r.sample(FEATURES, 4)), g.rent_line(s["rent"]), " ".join(r.sample(FILLER, 6)), mid,
                 " ".join(r.sample(FILLER, 5)), g.tail()]
        body = " ".join(parts)
        row("nodogs_buried", s, g.title(s), g.money(s["rent"]), g.attrs(s, show_kind=False), body, "cats only", ["no dogs"])

    # cats_only: three say "no dogs" outright, three just "cats only" (the dog flag is then optional)
    for i in range(TIER_COUNTS["cats_only"]):
        s = g.spec(plain_kinds[(i * 3) % len(plain_kinds)])
        if i < 3:
            sent, flags, lenient = r.choice(["Cats are welcome. No dogs.", "One cat is fine; no dogs allowed."]), ["no dogs"], ()
        else:
            sent, flags, lenient = r.choice(["Cats only please.", "We allow cats only."]), [], ("no dogs",)
        body = g.build_body(s, g.rent_line(s["rent"]), sent)
        row("cats_only", s, g.title(s), g.money(s["rent"]), g.attrs(s), body, "cats only", flags, lenient)

    # restricted: 55+ community (3), section 8 only (2)
    for i in range(TIER_COUNTS["restricted"]):
        s = g.spec(["mobile home", "condo", "apartment", "townhouse", "duplex"][i])
        if i < 3:
            extra, flags = ["This is an active adult community; residents must be 55 or older."], ["55+ only"]
        else:
            extra, flags = ["Section 8 vouchers only, please do not apply otherwise."], ["section 8 only"]
        body = g.build_body(s, g.rent_line(s["rent"]), "", extra)
        row("restricted", s, g.title(s), g.money(s["rent"]), g.attrs(s), body, "unclear", flags)

    # room: a room in a shared house (beds 1, kind room)
    for i in range(TIER_COUNTS["room"]):
        s = g.spec("house", beds=3, baths=2)
        s.update({"kind": "room", "beds": 1, "baths": 1, "sqft": 140 + 10 * i, "rent": int(round(r.uniform(450, 800) / 25)) * 25})
        body = " ".join(["Room for rent in a shared house on %s." % s["street"], "Furnished room, shared kitchen and bath.",
                         "%s a month, utilities split between the housemates." % g.money(s["rent"])] + r.sample(FILLER, 2) +
                        [["", "Cats ok.", "", "No pets.", ""][i], g.tail()])
        pets = {1: "cats only", 3: "no pets"}.get(i, "unclear")
        lenient = ("no dogs",) if pets != "unclear" else ()
        row("room", s, "Room for rent in %s" % s["town"], g.money(s["rent"]), "1BR / 1Ba %dft2 · room" % s["sqft"], body.replace("  ", " "),
            pets, ["room for rent"], lenient)

    # studio: beds 0
    for i in range(TIER_COUNTS["studio"]):
        s = g.spec("apartment", beds=1, baths=1)
        s.update({"beds": 0, "sqft": 380 + 15 * i, "rent": int(round(r.uniform(620, 900) / 25)) * 25})
        intro = r.choice(["Studio apartment, one bath, about %d square feet on %s." % (s["sqft"], s["street"]),
                          "A bright studio with one bathroom at %s." % s["street"]])
        body = " ".join([intro] + r.sample(FEATURES, 3) + [g.rent_line(s["rent"]), g.tail()])
        attrs = ("studio / 1Ba %dft2 · apartment" if i < 2 else "0BR / 1Ba %dft2 · apartment") % s["sqft"]
        row("studio", s, "Studio apartment near %s" % r.choice(LANDMARKS), g.money(s["rent"]), attrs, body, "unclear", [])

    # half_baths
    for i in range(TIER_COUNTS["half_baths"]):
        s = g.spec(["house", "townhouse", "condo", "duplex"][i], baths=[1.5, 2.5, 1.5, 2.5][i])
        body = g.build_body(s, g.rent_line(s["rent"]))
        row("half_baths", s, g.title(s), g.money(s["rent"]), g.attrs(s), body, "unclear", [])

    # price_disagree: the price field is stale or a placeholder; the body states the real rent
    for i in range(TIER_COUNTS["price_disagree"]):
        s = g.spec(["house", "duplex", "apartment"][i % 3])
        real = s["rent"]
        if i < 3:
            price = "$1"
            line = "Rent is %s per month (the price field is only a placeholder)." % g.money(real)
        else:
            stale = real + 25 * r.randint(8, 14)
            price = g.money(stale)
            line = "Reduced! Now %s a month; the posted price is out of date." % g.money(real)
        body = g.build_body(s, line)
        row("price_disagree", s, g.title(s), price, g.attrs(s), body, "unclear", [])

    # utilities wording: rent is the stated base number
    for i in range(TIER_COUNTS["utilities"]):
        s = g.spec(["apartment", "duplex", "condo", "house"][i])
        line = [("%s per month, water, sewer and trash included." % g.money(s["rent"])),
                ("%s/mo plus about $%d a month in utilities." % (g.money(s["rent"]), r.choice([120, 150, 180]))),
                ("%s a month with all utilities included." % g.money(s["rent"])),
                ("Rent %s. Tenant pays electric and gas, estimated $%d." % (g.money(s["rent"]), r.choice([90, 140])))][i]
        body = g.build_body(s, line)
        row("utilities", s, g.title(s), g.money(s["rent"]), g.attrs(s), body, "unclear", [])

    # scam: gold flags by construction
    for i, combo in enumerate(SCAM_COMBOS):
        s = g.spec(["house", "apartment", "duplex", "condo"][i % 4])
        rent = s["rent"]
        if "price far below area" in combo:
            rent = int(round(rent * r.uniform(0.28, 0.4) / 25)) * 25
            s["rent"] = rent
        extra = [r.choice(SCAM_TEXT[c]) for c in combo if c in SCAM_TEXT]
        body = g.build_body(s, g.rent_line(rent), "", extra, tail="no viewing" not in combo)
        if "no viewing" not in combo:
            pass
        row("scam", s, g.title(s), g.money(rent), g.attrs(s), body, "unclear", combo)

    # other_kind: parking, storage, office, workshop, RV pad, garage
    OTHERS = [("Covered parking space", "parking space", 85, 0), ("Storage unit, ground floor", "storage unit", 110, 50),
              ("Office suite for lease", "office suite", 650, 400), ("Workshop bay with roll-up door", "workshop bay", 475, 600),
              ("RV pad with hookups", "RV pad", 240, 0), ("Detached garage for rent", "detached garage", 160, 280)]
    for i in range(TIER_COUNTS["other_kind"]):
        title, thing, rent, sqft = OTHERS[i]
        s = {"kind": "other", "beds": None, "baths": None, "sqft": sqft, "town": g.town(), "street": g.street(), "rent": rent}
        body = " ".join(["%s at %s." % (title, s["street"]), "It is not a living space and is not for sleeping in.",
                         "%s a month, one month deposit." % g.money(rent)] + r.sample(FILLER[:8], 2) + [g.tail()])
        attrs = ("%dft2" % sqft) if sqft else "outdoor"
        row("other_kind", s, "%s in %s" % (title, s["town"]), g.money(rent), attrs, body, "unclear", [],
            rent=rent, beds="null", baths="null", kind="other")

    # long_body: three keep a "no dogs" sentence inside the first 4000 characters, three push it past the cut
    for i in range(TIER_COUNTS["long_body"]):
        s = g.spec(["house", "townhouse"][i % 2])
        head = " ".join([g.intro(s)] + r.sample(FEATURES, 3) + [g.rent_line(s["rent"])])
        if i < 3:   # in window
            body = " ".join([head, g.pad_to(2200 + 250 * i), "Cats are okay with a deposit. No dogs.", g.pad_to(1600), g.tail()])
            row("long_body", s, g.title(s), g.money(s["rent"]), g.attrs(s), body, "cats only", ["no dogs"])
        else:       # beyond the 4000-character cut: the model never sees it, so gold does not include it
            body = " ".join([head, g.pad_to(4300 + 250 * i), "Pets: no dogs.", g.tail()])
            row("long_body", s, g.title(s), g.money(s["rent"]), g.attrs(s), body, "unclear", [])

    r.shuffle(rows)
    for i, x in enumerate(rows):
        x["id"] = "r%03d" % i
    return {"seed": seed, "synthetic": True, "n_rows": len(rows), "rows": rows}


# ---------------------------------------------------------------- scoring
def _teq(a, b):
    return a is None and b is None or (a is not None and b is not None and abs(float(a) - float(b)) < 1e-6)


def parse_reply(text):
    """-> dict (keys as the model gave them) or None when no JSON object can be read."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def score_item(row, text, errored=False):
    """-> dict with checker pass/message, per-field right/wrong, and flag tp/fp/fn."""
    gold = row["gold"]
    if errored:
        return {"error": True, "checker": None, "check_msg": "transport error", "fields": {}, "all_fields": False,
                "tp": 0, "fp": 0, "fn": len(gold["red_flags"])}
    ok, _d, msg = check(row, text)
    d = parse_reply(text) or {}
    f = {"rent": _teq(d.get("rent"), gold["rent"]) if "rent" in d else False,
         "beds": _teq(d.get("beds"), gold["beds"]) if "beds" in d else False,
         "baths": _teq(d.get("baths"), gold["baths"]) if "baths" in d else False,
         "kind": d.get("kind") == gold["kind"], "pets": d.get("pets") == gold["pets"],
         "town": str(d.get("town", "")).strip().lower() == gold["town"].lower()}
    for k in ("rent", "beds", "baths"):
        if k in d and d[k] is not None and not isinstance(d[k], (int, float)):
            try:
                f[k] = _teq(float(str(d[k]).replace(",", "").replace("$", "")), gold[k])
            except ValueError:
                f[k] = False
    flags = d.get("red_flags")
    tp, fp, fn = flag_score(flags if isinstance(flags, list) else [], gold["red_flags"], row.get("lenient_flags", ()))
    flags_exact = isinstance(flags, list) and fp == 0 and fn == 0
    five = all(f[k] for k in ("rent", "beds", "baths", "kind", "pets"))
    return {"error": False, "checker": bool(ok), "check_msg": msg, "fields": f, "flags_exact": flags_exact,
            "all_fields": bool(five and flags_exact), "tp": tp, "fp": fp, "fn": fn}


def summarize(rows, scored):
    """scored: {id: score_item(...)} -> overall and per-tier aggregates."""
    def agg(pick):
        rs = [r for r in rows if pick(r)]
        sc = [scored[r["id"]] for r in rs if r["id"] in scored]
        n = len(sc)
        a = {"n": n, "errors": sum(s["error"] for s in sc), "checker_pass": sum(bool(s["checker"]) for s in sc),
             "checker_fail": sum(s["checker"] is False for s in sc), "all_fields": sum(s["all_fields"] for s in sc),
             "tp": sum(s["tp"] for s in sc), "fp": sum(s["fp"] for s in sc), "fn": sum(s["fn"] for s in sc)}
        for k in ("rent", "beds", "baths", "kind", "pets", "town"):
            a[k] = sum(bool(s["fields"].get(k)) for s in sc)
        a["flag_precision"] = round(a["tp"] / (a["tp"] + a["fp"]), 3) if a["tp"] + a["fp"] else None
        a["flag_recall"] = round(a["tp"] / (a["tp"] + a["fn"]), 3) if a["tp"] + a["fn"] else None
        a["escalation_rate"] = round(a["checker_fail"] / n, 3) if n else None
        return a
    out = {"all": agg(lambda r: True), "by_tier": {}}
    for t in TIER_ORDER:
        if any(r["tier"] == t for r in rows):
            out["by_tier"][t] = agg(lambda r, t=t: r["tier"] == t)
    return out


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- selftest
def selftest():
    c = common.Checks()
    fx = generate()
    rows = fx["rows"]
    c("generation is deterministic", generate() == fx)
    if os.path.exists(FIXTURE):
        c("committed fixture equals the seed's output", load_fixture() == fx)
    c("row count is the tier total", len(rows) == sum(TIER_COUNTS.values()))
    c("every tier has its planned count", all(sum(r["tier"] == t for r in rows) == n for t, n in TIER_COUNTS.items()))
    c("ids are unique", len({r["id"] for r in rows}) == len(rows))
    c("every kind appears in the gold", set(KINDS) <= {r["gold"]["kind"] for r in rows})
    c("every pets value appears in the gold", set(PETS) <= {r["gold"]["pets"] for r in rows})
    c("every red-flag category appears in the gold", set(FLAGS) <= {f for r in rows for f in r["gold"]["red_flags"]})
    c("gold kind and pets are on the allowed lists", all(r["gold"]["kind"] in KINDS and r["gold"]["pets"] in PETS for r in rows))

    # structured fields agree with the gold (except where a tier disagrees on purpose)
    def price_ok(r):
        if r["tier"] == "price_disagree":
            return _num(r["price"]) != r["gold"]["rent"] and ("$" + "{:,}".format(r["gold"]["rent"])) in r["body"]
        return _num(r["price"]) == r["gold"]["rent"]
    c("price field agrees with gold rent (stated in the body where the tier disagrees)", all(price_ok(r) for r in rows))
    c("the price_disagree tier differs from the price field in every row", all(
        _num(r["price"]) != r["gold"]["rent"] for r in rows if r["tier"] == "price_disagree"))

    def attrs_ok(r):
        br = re.search(r"(\d+)BR", r["attrs"], re.I)
        ba = re.search(r"([\d.]+)Ba", r["attrs"])
        return (not br or float(br.group(1)) == r["gold"]["beds"]) and (not ba or float(ba.group(1)) == r["gold"]["baths"])
    c("BR / Ba attributes agree with gold beds and baths", all(attrs_ok(r) for r in rows))
    c("studio rows have gold beds 0", all(r["gold"]["beds"] == 0 for r in rows if r["tier"] == "studio"))
    c("non-housing rows have null beds, baths", all(r["gold"]["beds"] is None and r["gold"]["baths"] is None
                                                       for r in rows if r["tier"] == "other_kind"))
    c("half baths are in the gold", any(r["gold"]["baths"] not in (None,) and r["gold"]["baths"] % 1 for r in rows))

    # the production checker must accept the gold answer for every listing
    def gold_json(r):
        g = r["gold"]
        return json.dumps({"rent": g["rent"], "beds": g["beds"], "baths": g["baths"], "kind": g["kind"], "pets": g["pets"],
                           "town": g["town"], "red_flags": g["red_flags"]})
    c("the checker accepts the gold answer on every listing", all(check(r, gold_json(r))[0] for r in rows))
    c("a perfect reply scores every field and every flag", all(score_item(r, gold_json(r))["all_fields"] for r in rows))

    # a gold flag is triggered by the (truncated) text it is supposed to come from, and only then
    def text_of(r):
        return r["title"] + " " + r["body"][:4000]
    bad = []
    for r in rows:
        t, flags = text_of(r), r["gold"]["red_flags"]
        for cat, rx in TRIGGER.items():
            if bool(rx.search(t)) != (cat in flags):
                bad.append((r["id"], cat))
    c("each text trigger fires exactly when its gold flag is set (within the 4000-character window)", not bad)
    c("price far below area appears only on rows priced under 45% of the kind's typical rent", all(
        (r["gold"]["rent"] <= 0.45 * (TYPICAL_RENT[r["gold"]["kind"]] + PER_BED[r["gold"]["kind"]] * (r["gold"]["beds"] or 1)))
        == ("price far below area" in r["gold"]["red_flags"]) for r in rows if r["gold"]["kind"] in TYPICAL_RENT))
    lb = [r for r in rows if r["tier"] == "long_body"]
    c("long_body rows are over 4000 characters", all(len(r["body"]) > 4000 for r in lb))
    c("long_body: 'no dogs' is inside the window in three rows and past the cut in three",
      sum(bool(re.search(r"no dogs", r["body"][:4000], re.I)) for r in lb) == 3 and
      sum(bool(re.search(r"no dogs", r["body"], re.I)) for r in lb) == 6)
    un = [r for r in rows if r["gold"]["pets"] == "unclear"]
    c("an 'unclear' gold never has an explicit pet statement in view", not any(
        re.search(r"dogs? (are |is )?(welcome|ok|allowed|fine)|no dogs|no pets|cats|dog run|dog park|are ok", text_of(r) + " " + r["attrs"], re.I)
        for r in un))
    c("'dogs ok' gold always has dogs said in the attributes or the visible text", all(
        re.search(r"dogs? (are |is )?(welcome|ok|allowed|fine)|dog run|dog park|one dog|small dogs|dogs are ok", text_of(r) + " " + r["attrs"], re.I)
        for r in rows if r["gold"]["pets"] == "dogs ok"))

    # privacy / leakage
    blob = json.dumps(rows)
    c("no real place name from the denylist", not any(re.search(r"\b%s\b" % re.escape(p), blob) for p in REAL_PLACE_DENYLIST))
    c("no email, URL or domain", not re.search(r"@|https?:|www\.|\.com|\.net|\.org", blob))
    phones = re.findall(r"\b\d{3}[-. ]\d{4}\b|\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b", blob)
    c("every phone number is in the reserved 555-01xx range", all(re.fullmatch(r"555-01\d\d", p) for p in phones) and len(phones) > 50)
    syl = re.compile(r"^(?:%s)(?:%s)(?:%s)$" % ("|".join(SYL_A), "|".join(SYL_B), "|".join(TOWN_SUF)))
    c("every town is built from the invented syllables", all(syl.match(r["gold"]["town"]) for r in rows))
    c("no two listings share a town", len({r["gold"]["town"] for r in rows}) == len(rows))

    # checker behaviour (a port: these pin what it does)
    g = rows[0]
    base = json.loads(gold_json(g))
    c("checker: bad JSON fails", not check(g, "no json here")[0] and not check(g, "{not json}")[0])
    c("checker: missing key fails", not check(g, json.dumps({k: v for k, v in base.items() if k != "town"}))[0])
    c("checker: off-list kind fails", not check(g, json.dumps({**base, "kind": "flat"}))[0])
    c("checker: off-list pets fails", not check(g, json.dumps({**base, "pets": "yes"}))[0])
    c("checker: red_flags must be a list", not check(g, json.dumps({**base, "red_flags": "none"}))[0])
    pd = next(r for r in rows if r["tier"] == "price_disagree")
    c("checker: a rent that is not the price field but is in the text passes", check(pd, gold_json(pd))[0])
    c("checker: a rent far from the price field and absent from the text fails", not check(g, json.dumps({**base, "rent": 99999}))[0])
    withbr = next(r for r in rows if re.search(r"\dBR", r["attrs"]) and r["gold"]["beds"])
    wb = json.loads(gold_json(withbr))
    c("checker: beds that contradict the BR attribute fail", not check(withbr, json.dumps({**wb, "beds": wb["beds"] + 1}))[0])
    c("checker: null beds pass", check(withbr, json.dumps({**wb, "beds": None}))[0])
    ca = next(r for r in rows if "dogs are OK" in r["attrs"])
    c("checker: attributes say dogs OK but reply says no pets fails", not check(ca, json.dumps({**json.loads(gold_json(ca)), "pets": "no pets"}))[0])
    c("checker: a <think> block is ignored", check(g, "<think>hm</think>" + gold_json(g))[0])

    # scoring helpers
    c("flags: spurious and missed are counted",
      flag_score(["wire transfer", "bad vibes"], ["wire payment", "no viewing"]) == (1, 1, 1))
    c("flags: duplicates of a category collapse", flag_score(["no dogs", "dogs not allowed"], ["no dogs"]) == (1, 0, 0))
    c("flags: a lenient category counts neither way", flag_score(["no pets"], [], ("no dogs",)) == (0, 0, 0))
    c("flags: an invented flag outside the lenient set is spurious", flag_score(["no pets"], []) == (0, 1, 0))
    c("flags: 55+ phrasing maps", flag_score(["age 55+ community"], ["55+ only"]) == (1, 0, 0))
    c("flags: non-list is no flags", flag_score("none", ["no dogs"]) == (0, 0, 1))
    c("parse: bare JSON, fenced JSON, think block, garbage",
      parse_reply('{"a":1}') == {"a": 1} and parse_reply('```json\n{"a":1}\n```') == {"a": 1} and
      parse_reply('<think>x</think>{"a":1}') == {"a": 1} and parse_reply("sorry") is None)
    wrong = json.loads(gold_json(g))
    wrong.update({"kind": "other", "red_flags": ["gift card"]})
    sc = score_item(g, json.dumps(wrong))
    c("score: a wrong kind and an invented flag are both seen", not sc["fields"]["kind"] and sc["fp"] == 1 and not sc["all_fields"])
    c("score: a transport error earns nothing and is not a checker failure", score_item(g, "", True)["checker"] is None)
    perfect = {r["id"]: score_item(r, gold_json(r)) for r in rows}
    s = summarize(rows, perfect)
    c("summary of perfect replies: checker, fields, no spurious flags", s["all"]["checker_pass"] == len(rows) and s["all"]["all_fields"] == len(rows)
      and s["all"]["fp"] == 0 and s["all"]["escalation_rate"] == 0.0)
    c("summary has a row per tier", list(s["by_tier"]) == TIER_ORDER)
    # the user message is built exactly as the production one is (body cut at 4000)
    m = build_messages(next(r for r in rows if r["tier"] == "long_body"))
    c("prompt: system + user, body cut at 4000 characters",
      [x["role"] for x in m] == ["system", "user"] and m[1]["content"].count("\n") >= 4 and len(m[1]["content"]) < 4000 + 400)
    return c.finish()


# ---------------------------------------------------------------- run
def default_cap(a):
    return a.max_tokens or (1280 if a.think == "on" else 512)


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--max-tokens", type=int, default=0, help="output cap (default 512, or 1280 with --think on)")
    ap.add_argument("--reasoning-budget", type=int, default=0,
                    help="send reasoning_budget_tokens (llama.cpp per-request cap on thinking); 0 = not sent")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N listings of the fixture")
    ap.add_argument("--abort-after", type=int, default=15,
                    help="spiral guard: look at the first N listings (0 = off)")
    ap.add_argument("--abort-bad", type=int, default=4,
                    help="spiral guard: stop the arm once this many of those listings hit the token cap or an error")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--gen", action="store_true", help="rewrite the committed fixture from the seed")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.gen:
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        with open(FIXTURE, "w", encoding="utf-8") as f:
            json.dump(generate(), f, indent=1)
            f.write("\n")
        print("wrote", FIXTURE)
        return 0
    fx = load_fixture()
    rows = fx["rows"][:a.limit] if a.limit else fx["rows"]
    extra = {"reasoning_budget_tokens": a.reasoning_budget} if a.reasoning_budget else None
    arm = {"off": "think-off", "on": "think-on" + ("-%d" % a.reasoning_budget if a.reasoning_budget else ""), "auto": "think-auto"}[a.think]
    scored, detail, wall, toks, capped = {}, [], 0.0, 0, 0
    for i, row in enumerate(rows):
        r = common.chat(a, build_messages(row), max_tokens=default_cap(a), temperature=0.0, extra=extra)
        err = bool(r.get("error"))
        sc = score_item(row, r["content"], err)
        scored[row["id"]] = sc
        wall += r["s"]
        toks += r.get("completion_tokens") or 0
        capped += r.get("finish") == "length"
        bad = sum(1 for d in detail if d["finish"] == "length" or d["error"]) + (1 if (r.get("finish") == "length" or err) else 0)
        if a.abort_after and i < a.abort_after and bad >= a.abort_bad:
            nerr = sum(1 for d in detail if d["error"]) + (1 if err else 0)
            msg = ("ABORTED: %d of the first %d listings hit the token cap or an error (%d transport errors: check the server before blaming the model)"
                   % (bad, i + 1, nerr)) if nerr else (
                   "ABORTED: %d of the first %d listings hit the token cap (spiral guard); seat unusable on this arm" % (bad, i + 1))
            common.say(msg)
            common.save_results(a, "RENT", {"fixture_seed": fx["seed"], "synthetic": True, "n_rows": i + 1, "arm": arm,
                                            "reasoning_budget": a.reasoning_budget, "max_tokens": default_cap(a),
                                            "aborted": msg, "capped": bad, "wall_s": round(wall + r["s"], 1), "invalid": True})
            return 1
        detail.append({"id": row["id"], "tier": row["tier"], "reply": r["content"], "finish": r.get("finish"), "error": r.get("error"),
                       "s": r["s"], "completion_tokens": r.get("completion_tokens"), "checker": sc["checker"], "check_msg": sc["check_msg"],
                       "fields": sc["fields"], "flags_exact": sc.get("flags_exact"), "tp": sc["tp"], "fp": sc["fp"], "fn": sc["fn"]})
        if (i + 1) % 25 == 0:
            common.say("  %d/%d listings, %.0fs" % (i + 1, len(rows), wall))
    s = summarize(rows, scored)
    x = s["all"]
    common.say("arm %s: checker pass %d/%d (escalation %s) | all fields+flags %d/%d" % (
        arm, x["checker_pass"], x["n"], x["escalation_rate"], x["all_fields"], x["n"]))
    common.say("fields right: rent %d beds %d baths %d kind %d pets %d town %d (of %d)" % (
        x["rent"], x["beds"], x["baths"], x["kind"], x["pets"], x["town"], x["n"]))
    common.say("red_flags: precision %s recall %s (%d spurious, %d missed)" % (x["flag_precision"], x["flag_recall"], x["fp"], x["fn"]))
    common.say("by tier (checker/all-fields/n): " + "  ".join("%s %d/%d/%d" % (t, v["checker_pass"], v["all_fields"], v["n"])
                                                              for t, v in s["by_tier"].items()))
    errs = x["errors"]
    n = max(1, len(rows))
    common.say("wall %.0fs (%.1f s/item), %d completion tokens, %d capped at max_tokens, %d errors" % (wall, wall / n, toks, capped, errs))
    invalid = errs > len(rows) // 4
    if invalid:
        common.say("INVALID RUN: %d of %d listings hit a transport/HTTP error; do not publish" % (errs, len(rows)))
    common.save_results(a, "RENT", {"fixture_seed": fx["seed"], "synthetic": True, "n_rows": len(rows), "arm": arm,
                                    "reasoning_budget": a.reasoning_budget, "max_tokens": default_cap(a),
                                    "summary": s, "wall_s": round(wall, 1), "s_per_item": round(wall / n, 2),
                                    "completion_tokens": toks, "capped": capped, "answers": detail, "invalid": invalid})
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
