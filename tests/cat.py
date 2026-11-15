#!/usr/bin/env python3
"""CAT (synthetic): categorize bank-statement payees, with abstention.

The lab's original CAT test used one person's real bank statements, so it could not be published. This is a
rebuild on invented data. Every payee is generated in code from a seed (`--gen`), the ground truth is known
by construction, and nothing in tests/fixtures/cat/ comes from any real statement.

What the model sees (like the original): only the cleaned payee text and "money in" / "money out", plus a
category list and a short policy. It must answer with one category or UNSURE. Scored in code:

  category rows (gold is a category):  right / wrong / blank (blank = UNSURE or no usable reply)
  unknowable rows (gold is UNSURE):    abstained (good) / answered (a false fill)

Rows come in tiers by construction. "rule" tiers (clear, income, transfer) are what a keyword rule list would
absorb; the "tail" tiers (weak, misleading, dual, opaque, brand) are the hard residue. The report gives the whole
fixture and the tail alone, because the lab found those two slices rank models differently.

  python3 tests/cat.py --selftest
  python3 tests/cat.py --gen                      # rewrite tests/fixtures/cat/payees.json from the seed
  BASE_URL=... MODEL=... python3 tests/cat.py [--limit N] [--no-schema] [--max-tokens 256]

The record in results/raw/ keeps every answer, so tests/arena.py can vote across several runs.
"""
import argparse
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SEED = 20261114
UNSURE = "UNSURE"
FIXTURE = common.fixture_path("cat", "payees.json")

CATEGORIES = {
    "Groceries": "supermarkets, food markets, grocery stores",
    "Dining Out": "cafes, restaurants, bakeries, takeaway",
    "Fuel": "fuel for vehicles",
    "Utilities": "electric, water, natural gas service, phone and internet providers, waste",
    "Software & Streaming": "software, apps, cloud and streaming subscriptions",
    "Insurance": "insurance companies and premiums",
    "Health & Pharmacy": "pharmacies, dental, clinics, therapy",
    "Home & Hardware": "hardware, lumber, garden and paint supply",
    "Clothing": "apparel, shoes, outfitters",
    "Travel": "airlines, hotels, inns, car rental",
    "Entertainment": "cinemas, theatres, bowling, arcades",
    "Auto Service": "repair, tires, oil changes, body shops, car washes",
    "Transfers & Cash": "ATM withdrawals and transfers between own accounts",
    "Income": "payroll deposits and interest received",
}

# type words per category: (full form, abbreviation used by the "weak" tier)
TYPES = {
    "Groceries": [("MARKET", "MKT"), ("GROCERY", "GROC"), ("SUPERMARKET", "SPRMKT"), ("FOODS", "FDS")],
    "Dining Out": [("CAFE", "CAF"), ("GRILL", "GRL"), ("PIZZA", "PZA"), ("BAKERY", "BKRY"), ("TAQUERIA", "TAQ"), ("DINER", "DNR")],
    "Fuel": [("FUEL", "FL"), ("FUEL STOP", "FL STP"), ("PETROLEUM", "PETRO")],
    "Utilities": [("ELECTRIC", "ELEC"), ("WATER DISTRICT", "WTR DIST"), ("TELECOM", "TLCM"), ("BROADBAND", "BRDBND"), ("WASTE SERVICES", "WSTE SVC")],
    "Software & Streaming": [("SOFTWARE", "SOFTWR"), ("STREAMING", "STRMNG"), ("CLOUD", "CLD"), ("APPS", "APP")],
    "Insurance": [("INSURANCE", "INS"), ("ASSURANCE", "ASSUR"), ("MUTUAL INSURANCE", "MUT INS")],
    "Health & Pharmacy": [("PHARMACY", "PHARM"), ("DENTAL", "DNTL"), ("CLINIC", "CLNC"), ("PHYSICAL THERAPY", "PHYS THER")],
    "Home & Hardware": [("HARDWARE", "HDWR"), ("LUMBER", "LBR"), ("GARDEN CENTER", "GRDN CTR"), ("PAINT SUPPLY", "PNT SUPP")],
    "Clothing": [("APPAREL", "APPRL"), ("OUTFITTERS", "OUTFTR"), ("SHOES", "SHS")],
    "Travel": [("AIRLINES", "AIRLN"), ("HOTEL", "HTL"), ("INN", "INN"), ("CAR RENTAL", "CAR RNTL")],
    "Entertainment": [("CINEMA", "CINE"), ("THEATRE", "THTR"), ("BOWLING", "BWL"), ("ARCADE", "ARCD")],
    "Auto Service": [("AUTO REPAIR", "AUTO RPR"), ("TIRE CENTER", "TIRE CTR"), ("OIL CHANGE", "OIL CHG"), ("CAR WASH", "CAR WSH")],
}
# a business-type word from another category, used as a decoy in the "misleading" tier
DECOYS = ["BAKERY", "PHARMACY", "FUEL", "GROCERY", "CINEMA", "HARDWARE", "ELECTRIC", "MARKET", "CAFE", "LUMBER"]
HEADS = ["Insurance", "Software & Streaming", "Clothing", "Travel", "Utilities", "Health & Pharmacy", "Auto Service", "Entertainment"]

# two businesses joined by "&": the policy says the first-named one decides; gas & electric is the stated exception
DUAL = [("MARKET", "PHARMACY", "Groceries"), ("PHARMACY", "MARKET", "Health & Pharmacy"),
        ("FUEL", "MARKET", "Fuel"), ("MARKET", "FUEL", "Groceries"),
        ("HARDWARE", "LUMBER", "Home & Hardware"), ("LUMBER", "HARDWARE", "Home & Hardware"),
        ("CAFE", "BAKERY", "Dining Out"), ("BAKERY", "CAFE", "Dining Out"),
        ("AUTO REPAIR", "TIRE CENTER", "Auto Service"), ("TIRE CENTER", "AUTO REPAIR", "Auto Service"),
        ("INN", "GRILL", "Travel"), ("GRILL", "INN", "Dining Out"),
        ("CINEMA", "GRILL", "Entertainment"), ("GRILL", "CINEMA", "Dining Out"),
        ("GAS", "ELECTRIC", "Utilities"), ("NATURAL GAS", "COMPANY", "Utilities")]

PREFIXES = ["POS DEBIT", "DEBIT CARD PURCHASE", "PURCHASE AUTHORIZED ON", "CHECKCARD", "RECURRING PAYMENT", "POS PURCHASE"]
PROCESSORS = ["SQ *", "TST* ", "PP*", "", "", ""]
CITIES = ["PHOENIX", "TUCSON", "MESA", "DENVER", "AUSTIN", "BOISE", "RENO", "TACOMA", "OMAHA", "TULSA"]
STATES = ["AZ", "CO", "TX", "ID", "NV", "WA", "NE", "OK"]
SYL_A = ["Vel", "Kor", "Tam", "Bran", "Mos", "Qui", "Dra", "Ful", "Zan", "Per", "Lor", "Hesk", "Yar", "Nol", "Ost", "Cav"]
SYL_B = ["tran", "vora", "ley", "kry", "dell", "mont", "sen", "wick", "ari", "bost", "lin", "gard", "pell", "ton"]
FIRST = ["Mara", "Joss", "Teo", "Ines", "Dov", "Lena", "Rafe", "Sunni", "Orla", "Pavel"]
LAST = ["Torven", "Halloran", "Vasko", "Brenner", "Okafor", "Lindqvist", "Marsh", "Delacroix", "Petrov", "Quill"]
POLICY_EXAMPLE_NAME = "Rivermont"          # appears in the prompt's example only; the generator never emits it

TIER_ORDER = ["clear", "income", "transfer", "weak", "misleading", "dual", "opaque", "brand"]
RULE_TIERS = {"clear", "income", "transfer"}
TIER_COUNTS = {"clear": 70, "income": 10, "transfer": 12, "weak": 30, "misleading": 25, "dual": 20, "opaque": 18, "brand": 15}


# ---------------------------------------------------------------- cleaning (what the model sees)
_PREFIX_RE = re.compile(r"^(?:%s)\s+" % "|".join(re.escape(p) for p in PREFIXES))


def clean(raw):
    """Strip descriptor noise the way a categorizer front end would: payment prefixes, processor stars,
    dates, store numbers, a trailing 'CITY ST'. Truncated words are left as they are."""
    t = raw.upper().strip()
    t = _PREFIX_RE.sub("", t)
    t = re.sub(r"\b\d{1,2}/\d{1,2}\b", " ", t)
    t = t.replace("*", " ")
    t = re.sub(r"^(?:SQ|TST|PP)\s+", "", t)
    t = re.sub(r"#\s*\d+", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    toks = t.split()
    if len(toks) >= 2 and toks[-1] in STATES and toks[-2] in CITIES:
        toks = toks[:-2]
    elif toks and toks[-1] in CITIES:
        toks = toks[:-1]
    return " ".join(toks)


# ---------------------------------------------------------------- generator
def _name(rng, used):
    for _ in range(1000):
        n = rng.choice(SYL_A) + rng.choice(SYL_B)
        if n.lower() != POLICY_EXAMPLE_NAME.lower() and n not in used:
            used.add(n)
            return n
    raise RuntimeError("name space exhausted")


def _person(rng):
    return "%s %s" % (rng.choice(FIRST), rng.choice(LAST))


def _noise(rng, core, *, store=True, truncate=None):
    """Wrap a merchant core in bank-descriptor noise. truncate=N cuts the merchant field to N characters."""
    body = core.upper()
    if truncate and len(body) > truncate:
        body = body[:truncate].rstrip()
    parts = [rng.choice(PREFIXES), rng.choice(PROCESSORS) + body]
    s = " ".join(parts) if parts[0] else parts[1]
    if store and rng.random() < 0.7:
        s += " #%04d" % rng.randint(1, 9999)
    if rng.random() < 0.6:
        s += " %s %s" % (rng.choice(CITIES), rng.choice(STATES))
    if rng.random() < 0.5:
        s += " %02d/%02d" % (rng.randint(1, 12), rng.randint(1, 28))
    return s.replace("  ", " ").strip()


def generate(seed=SEED):
    rng = random.Random(seed)
    used, rows, seen_clean = set(), [], set()
    cats = list(TYPES)

    def add(tier, raw, gold, direction="out"):
        c = clean(raw)
        if c in seen_clean:
            return False
        seen_clean.add(c)
        rows.append({"tier": tier, "raw": raw, "payee": c, "direction": direction, "gold": gold,
                     "rule_covered": tier in RULE_TIERS})
        return True

    def fill(tier, make):
        n = 0
        while n < TIER_COUNTS[tier]:
            n += add(*make(n))

    def clear(i):
        cat = cats[i % len(cats)]
        full = rng.choice(TYPES[cat])[0]
        return "clear", _noise(rng, "%s %s" % (_name(rng, used), full)), cat

    def weak(i):
        cat = cats[(i * 5 + 1) % len(cats)]
        full, abbr = rng.choice(TYPES[cat])
        name = _name(rng, used)
        if rng.random() < 0.5:                       # abbreviated type word
            core = "%s %s" % (name, abbr)
        else:                                        # cut mid-word by the field width, hint kept (>= 4 letters)
            core = "%s %s" % (name, full)
            core = core[:max(len(name) + 5, len(core) - rng.randint(2, 4))]
        return "weak", _noise(rng, core), cat

    def misleading(i):
        head = HEADS[i % len(HEADS)]
        decoy = rng.choice(DECOYS)
        return "misleading", _noise(rng, "%s %s %s" % (_name(rng, used), decoy, rng.choice(TYPES[head])[0])), head

    def dual(i):
        a, b, cat = DUAL[i % len(DUAL)]
        name = _name(rng, used)
        core = "%s %s COMPANY" % (name, a) if b == "COMPANY" else "%s %s & %s" % (name, a, b)
        return "dual", _noise(rng, core), cat

    def opaque(i):
        who = _person(rng)
        kind = i % 6
        raw = [lambda: "ZELLE PAYMENT TO %s" % who,
               lambda: "PAYPAL *%s" % who.replace(" ", ""),
               lambda: "SQ *%s" % who.upper(),
               lambda: "VENMO PAYMENT %s" % who,
               lambda: "CHECK # %d" % rng.randint(1000, 9999),
               lambda: "CKO*%s" % "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(6))][kind]()
        if kind == 4:                                # make the cleaned text unique: checks carry the payee line
            raw += " " + _person(rng).upper()
        return "opaque", raw, UNSURE

    def brand(i):
        return "brand", _noise(rng, _name(rng, used).upper(), store=True), UNSURE

    def income(i):
        if i % 2 == 0:
            return "income", "PAYROLL DEPOSIT %s %s" % (_name(rng, used).upper(), rng.choice(["LLC", "INC", "LTD"])), "Income", "in"
        return "income", "INTEREST PAID %s BANK %02d/%02d" % (_name(rng, used).upper(), rng.randint(1, 12), rng.randint(1, 28)), "Income", "in"

    def transfer(i):
        kind = ["ATM WITHDRAWAL", "ONLINE TRANSFER TO SAVINGS"][i % 2]
        return "transfer", "%s %s BANK %s %s" % (kind, _name(rng, used).upper(), rng.choice(CITIES), rng.choice(STATES)), "Transfers & Cash"

    for tier, mk in [("clear", clear), ("income", income), ("transfer", transfer), ("weak", weak),
                     ("misleading", misleading), ("dual", dual), ("opaque", opaque), ("brand", brand)]:
        fill(tier, mk)
    rng.shuffle(rows)
    for i, r in enumerate(rows):
        r["id"] = "p%03d" % i
    return {"seed": seed, "synthetic": True, "categories": list(CATEGORIES), "rows": rows}


# ---------------------------------------------------------------- prompt, reply parsing, scoring
def system_prompt():
    cats = "\n".join("- %s: %s" % (k, v) for k, v in CATEGORIES.items())
    return (
        "You assign one bank-statement payee to a budget category. Reply with exactly one category from the list, "
        "or UNSURE when the text does not tell you what the business is. A wrong category is worse than UNSURE.\n\n"
        "Categories:\n%s\n\n"
        "Policy:\n"
        "- Classify by what the business is, which is its LAST business word: '%s Dental Software' is "
        "Software & Streaming, not Health & Pharmacy.\n"
        "- A name that lists two businesses joined by '&' takes the category of the business named first, except "
        "'Gas & Electric' and 'Natural Gas' (a utility), which are Utilities.\n"
        "- Abbreviated or cut-off words count if you can tell what they stand for.\n"
        "- A person's name, a bare brand with no business word, a check, or a payment-app handle is UNSURE."
        % (cats, POLICY_EXAMPLE_NAME))


def user_prompt(row):
    return "PAYEE: %s\nDIRECTION: money %s" % (row["payee"], "in" if row["direction"] == "in" else "out")


def schema_format():
    return {"type": "json_schema", "json_schema": {"name": "category", "strict": True, "schema": {
        "type": "object", "properties": {"category": {"enum": list(CATEGORIES) + [UNSURE]}},
        "required": ["category"], "additionalProperties": False}}}


def parse_reply(text):
    """-> category string, UNSURE, or None when the reply holds no usable answer (empty / off-list)."""
    t = (text or "").strip()
    if not t:
        return None
    try:
        v = json.loads(t)
        if isinstance(v, dict) and isinstance(v.get("category"), str):
            t = v["category"]
    except ValueError:
        pass
    t = t.strip().strip("\"'` .").strip()
    for c in list(CATEGORIES) + [UNSURE]:
        if t.lower() == c.lower():
            return c
    hits = [c for c in sorted(CATEGORIES, key=len, reverse=True) if re.search(r"\b%s\b" % re.escape(c), t, re.I)]
    if len(hits) == 1 and not re.search(r"\bunsure\b", t, re.I):
        return hits[0]
    if re.search(r"\bunsure\b", t, re.I) and not hits:
        return UNSURE
    return None


def outcome(answer, gold, errored=False):
    """answer: category / UNSURE / None.  -> right | wrong | blank (category rows), abstain_ok | false_fill (UNSURE rows)."""
    if errored:                     # a transport/HTTP failure is not an answer and earns no credit either way
        return "error"
    ans = None if answer in (None, UNSURE) else answer
    if gold == UNSURE:
        return "abstain_ok" if ans is None else "false_fill"
    if ans is None:
        return "blank"
    return "right" if ans == gold else "wrong"


def summarize(rows, answers):
    """rows: fixture rows; answers: {id: answer} or {id: (answer, errored)}. -> {slice: counts}."""
    out = {}
    for name, pick in (("all", lambda r: True), ("tail", lambda r: not r["rule_covered"])):
        c = dict(right=0, wrong=0, blank=0, abstain_ok=0, false_fill=0, error=0)
        for r in rows:
            if pick(r):
                v = answers.get(r["id"])
                ans, err = (v if isinstance(v, tuple) else (v, False))
                c[outcome(ans, r["gold"], err)] += 1
        ans = c["right"] + c["wrong"]
        c["wrong_share"] = round(c["wrong"] / ans, 3) if ans else None
        out[name] = c
    by_tier = {}
    for t in TIER_ORDER:
        tr = [r for r in rows if r["tier"] == t]
        if tr:
            ok = 0
            for r in tr:
                v = answers.get(r["id"])
                ans, err = v if isinstance(v, tuple) else (v, False)
                ok += outcome(ans, r["gold"], err) in ("right", "abstain_ok")
            by_tier[t] = [ok, len(tr)]
    out["by_tier"] = by_tier
    return out


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def selftest():
    c = common.Checks()
    fx = generate()
    rows = fx["rows"]
    c("generation is deterministic", generate() == fx)
    if os.path.exists(FIXTURE):
        c("committed fixture equals the seed's output", load_fixture() == fx)
    c("row count is the tier total", len(rows) == sum(TIER_COUNTS.values()))
    c("every tier has its planned count", all(sum(r["tier"] == t for r in rows) == n for t, n in TIER_COUNTS.items()))
    c("cleaned payees are unique", len({r["payee"] for r in rows}) == len(rows))
    c("every gold is a category or UNSURE", all(r["gold"] in CATEGORIES or r["gold"] == UNSURE for r in rows))
    c("UNSURE gold only in the opaque/brand tiers", {r["tier"] for r in rows if r["gold"] == UNSURE} == {"opaque", "brand"})
    c("no cleaned payee keeps a digit-only token, a store number or a state tail",
      not any(re.search(r"(^| )\d+( |$)|#|\*", r["payee"]) or r["payee"].split()[-1:] and r["payee"].split()[-1] in STATES
              for r in rows))
    c("noise is really in the raw text", sum(bool(re.search(r"#\d{4}|SQ \*|PP\*|TST\*|POS|CHECKCARD", r["raw"])) for r in rows) > len(rows) // 2)
    c("the prompt's example name is never generated", not any(POLICY_EXAMPLE_NAME.upper() in r["raw"] for r in rows))
    c("cleaning a known descriptor", clean("POS DEBIT SQ *KELVORA MKT #0412 PHOENIX AZ 05/12") == "KELVORA MKT")
    c("cleaning keeps a cut word", clean("CHECKCARD TAMARIND ELEC 05/02") == "TAMARIND ELEC")
    c("dual gold follows the first-named rule", all(
        r["gold"] in ("Utilities",) if ("NATURAL GAS" in r["payee"] or "GAS & ELECTRIC" in r["payee"]) else True
        for r in rows if r["tier"] == "dual"))
    c("parse: json", parse_reply('{"category": "Fuel"}') == "Fuel")
    c("parse: plain text, case", parse_reply("groceries.") == "Groceries")
    c("parse: UNSURE", parse_reply('{"category":"UNSURE"}') == UNSURE)
    c("parse: empty is None", parse_reply("") is None)
    c("parse: two categories is None", parse_reply("Fuel or Groceries") is None)
    c("parse: off-list is None", parse_reply("Pets") is None)
    c("score: right / wrong / blank", [outcome("Fuel", "Fuel"), outcome("Fuel", "Groceries"), outcome(UNSURE, "Fuel"),
                                       outcome(None, "Fuel")] == ["right", "wrong", "blank", "blank"])
    c("score: unknowable rows", [outcome(UNSURE, UNSURE), outcome("Fuel", UNSURE)] == ["abstain_ok", "false_fill"])
    c("score: an errored row is neither right, blank nor an abstention", outcome(None, UNSURE, True) == "error")
    perfect = {r["id"]: (None if r["gold"] == UNSURE else r["gold"]) for r in rows}
    s = summarize(rows, perfect)
    cat_rows = sum(r["gold"] != UNSURE for r in rows)
    c("perfect answers score all right", s["all"]["right"] == cat_rows and s["all"]["wrong"] == 0
      and s["all"]["abstain_ok"] == len(rows) - cat_rows)
    c("perfect answers score every tier fully ok", all(a == b for a, b in s["by_tier"].values()))
    tup = summarize(rows, {r["id"]: (a, False) for r, a in ((r, perfect[r["id"]]) for r in rows)})
    c("by_tier reads (answer, errored) tuples like all/tail do", tup["by_tier"] == s["by_tier"])
    sure = summarize(rows, {r["id"]: "Fuel" for r in rows})
    c("always-Fuel scores a false fill on every unknowable row", sure["all"]["false_fill"] == len(rows) - cat_rows)
    c("tail slice excludes rule tiers", s["tail"]["right"] + s["tail"]["abstain_ok"] ==
      sum(not r["rule_covered"] for r in rows))
    return c.finish()


def ask(a, row, sysmsg):
    """Two attempts like the lab harness: cap 256, then 512 when the first reply is empty or cut off."""
    fmt = None if a.no_schema else schema_format()
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": user_prompt(row)}]
    tries, total_s = 0, 0.0
    for cap in (a.max_tokens, a.max_tokens * 2):
        tries += 1
        r = common.chat(a, msgs, max_tokens=cap, temperature=0.0, response_format=fmt)
        total_s += r["s"]
        ans = parse_reply(r["content"])
        if ans is not None and r.get("finish") != "length":
            return ans, r, tries, total_s
        if r.get("error"):
            break
    return None, r, tries, total_s


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--max-tokens", type=int, default=256, help="first-try output cap (retry at twice this)")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N rows of the fixture")
    ap.add_argument("--no-schema", action="store_true", help="do not send a json_schema response_format")
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
    sysmsg = system_prompt()
    answers, detail, wall = {}, [], 0.0
    toks = 0
    for i, row in enumerate(rows):
        ans, r, tries, secs = ask(a, row, sysmsg)
        wall += secs
        toks += r.get("completion_tokens") or 0
        answers[row["id"]] = (ans, bool(r.get("error")))
        detail.append({"id": row["id"], "answer": ans, "outcome": outcome(ans, row["gold"], bool(r.get("error"))), "tries": tries,
                       "finish": r.get("finish"), "error": r.get("error"), "s": round(secs, 1)})
        if (i + 1) % 25 == 0:
            common.say("  %d/%d rows, %.0fs" % (i + 1, len(rows), wall))
    s = summarize(rows, answers)
    for name in ("all", "tail"):
        x = s[name]
        common.say("%-4s right %d  wrong %d  blank %d  | unknowable: abstained %d, false fill %d  | errors %d | wrong share of answered %s"
                   % (name, x["right"], x["wrong"], x["blank"], x["abstain_ok"], x["false_fill"], x["error"], x["wrong_share"]))
    common.say("by tier (ok/n): " + "  ".join("%s %d/%d" % (t, v[0], v[1]) for t, v in s["by_tier"].items()))
    errs = sum(bool(d["error"]) for d in detail)
    common.say("wall %.0fs, %d completion tokens, %d errors" % (wall, toks, errs))
    if errs > len(rows) // 4:
        common.say("INVALID RUN: %d of %d rows hit a transport/HTTP error and are counted blank; do not publish" % (errs, len(rows)))
    common.save_results(a, "CAT", {"fixture_seed": fx["seed"], "synthetic": True, "n_rows": len(rows),
                                   "summary": s, "wall_s": round(wall, 1), "completion_tokens": toks,
                                   "answers": detail,
                                   "invalid": errs > len(rows) // 4})
    return 1 if errs > len(rows) // 4 else 0


if __name__ == "__main__":
    sys.exit(main())
