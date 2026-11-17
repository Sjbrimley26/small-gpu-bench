#!/usr/bin/env python3
"""PO-SYN: purchase-order extraction where "did it return parseable JSON" is scored apart from "were the
fields right".

The lab card that produced this test asked for one PO/invoice extraction with JSON validity counted
separately, because on a small model those two come apart: a reply can be a perfect reading of the page and
still arrive wrapped in prose, fenced, truncated at the token cap, or empty because a reasoning trace spent
the budget. One correct/incorrect column hides which of those happened, so this test reports five numbers:

  parse     strict (the reply IS the JSON) | fenced | wrapped (JSON embedded in prose) | invalid | empty
  schema    of 10 required top-level keys, how many are present
  fields    5 header fields exact (vendor, po number, date, currency, ship-to)
  money     subtotal, freight, tax and total each within 0.01 of the document
  items     line items matched by SKU with qty, unit price and amount all exact (8 in the fixture)
  arith     do the reply's OWN numbers add up (sum of line amounts = subtotal, subtotal + freight + tax =
            total)? A model that invents a total is internally inconsistent even when it copied every other
            number, and that is the useful signal for a purchasing workflow.

**Read this as the clean baseline, not the hard case.** Every seat measured on it (13 of them) extracted all
eight items and every header field, so what it separates is formatting (fenced vs strict) and budget (a
reasoning trace eating the cap). For a document whose layout fights back, see `tests/po_real.py`, built from a
real published purchase order.

The document is invented: a fastener supplier, eight line items, a flat freight charge and 8.1 % tax, all
generated here from the gold, so the text and the expected values cannot disagree
(`--gen` writes tests/fixtures/po/document.txt and gold.json; `--selftest` fails if the committed copies
drift from the code). No real vendor, customer, address or price from any record is used.

  python3 tests/po.py --selftest
  python3 tests/po.py --gen
  BASE_URL=... MODEL=... python3 tests/po.py                  # thinking left to the server (THINK=auto)
  BASE_URL=... MODEL=... THINK=off python3 tests/po.py        # the arm that produced the lab result
  BASE_URL=... MODEL=... THINK=on  python3 tests/po.py --max-tokens 6000   # the trace needs a real budget
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

VENDOR = "Northwind Fastener Supply"
PO_NUMBER = "PO-2026-1187"
ORDER_DATE = "2026-11-03"
SHIP_TO = "Riverbend Makerspace, 214 Mill St, Phoenix AZ 85004"
CURRENCY = "USD"
ITEMS = [
    ("NF-1042", "3 in structural screw, exterior, 5 lb box", 4, 38.75),
    ("NF-2210", "1/2 in galvanized carriage bolt, 25 pack", 6, 21.40),
    ("NF-3308", "joist hanger, 2x8, G185", 24, 2.65),
    ("NF-4417", "construction adhesive, 28 oz tube", 12, 8.95),
    ("NF-5520", "post base, 6x6, powder coated", 8, 17.30),
    ("NF-6631", "stainless washer, 3/8 in, 100 count", 3, 12.10),
    ("NF-7702", "hurricane tie, 18 gauge, 12 in", 40, 1.85),
    ("NF-8814", "deck screw, 2-1/2 in, 5 lb box", 10, 31.20),
]
FREIGHT = 84.50
TAX_RATE = 0.081
TAX_LABEL = "8.1%"

REQUIRED = ("vendor", "po_number", "order_date", "ship_to", "currency", "line_items",
            "subtotal", "freight", "tax", "total")

SCHEMA = """{
  "vendor": string, "po_number": string, "order_date": string (YYYY-MM-DD),
  "ship_to": string, "currency": string,
  "line_items": [{"sku": string, "description": string, "qty": integer,
                  "unit_price": number, "amount": number}],
  "subtotal": number, "freight": number, "tax": number, "total": number
}"""

PROMPT = """Extract the purchase order below into one JSON object with exactly this shape:

{schema}

Reply with the JSON object and nothing else. Copy values from the document; do not compute any you
cannot read. Numbers are plain JSON numbers (no currency symbols, no thousands separators).

DOCUMENT
--------
{doc}
"""


def money(x):
    return round(x + 1e-9, 2)


def build():
    """-> (document text, gold dict). The document is rendered from the gold, so the two cannot disagree."""
    lines, subtotal = [], 0.0
    for sku, desc, qty, price in ITEMS:
        amt = money(qty * price)
        subtotal += amt
        lines.append("%-9s %-46s %3d  $%8.2f  $%9.2f" % (sku, desc, qty, price, amt))
    subtotal = money(subtotal)
    tax = money(subtotal * TAX_RATE)
    total = money(subtotal + FREIGHT + tax)
    doc = """From: orders@northwindfasteners.example
To: purchasing@riverbendmakers.example
Subject: Purchase order {po} confirmed

Northwind Fastener Supply confirms the order below. Prices are per unit in {cur}, net 30.
Ship to: {ship}
Order date: {date}
Purchase order: {po}

SKU       Description                                        Qty   Unit price      Amount
{dash}
{lines}
{dash}
                                                   Subtotal:          ${subtotal:>10,.2f}
                                          Freight (flat rate):        ${freight:>10,.2f}
                                              Sales tax ({taxlabel}):       ${tax:>10,.2f}
                                                      TOTAL:         ${total:>10,.2f}

Please quote the purchase order number on the remittance advice.
""".format(po=PO_NUMBER, cur=CURRENCY, ship=SHIP_TO, date=ORDER_DATE, dash="-" * 96,
           lines="\n".join(lines), subtotal=subtotal, freight=FREIGHT, tax=tax, total=total,
           taxlabel=TAX_LABEL)
    gold = {
        "vendor": VENDOR, "po_number": PO_NUMBER, "order_date": ORDER_DATE, "ship_to": SHIP_TO,
        "currency": CURRENCY,
        "line_items": [{"sku": s, "description": d, "qty": q, "unit_price": money(p),
                        "amount": money(q * p)} for s, d, q, p in ITEMS],
        "subtotal": subtotal, "freight": FREIGHT, "tax": tax, "total": total,
    }
    return doc, gold


def parse_reply(text):
    """-> (object or None, mode). mode says how the JSON was found, which is a separate result."""
    raw = (text or "").strip()
    if not raw:
        return None, "empty"
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", raw, re.S)
    for cand in [raw] + fenced:
        try:
            return json.loads(cand.strip()), ("strict" if cand is raw else "fenced")
        except Exception:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1]), "wrapped"
        except Exception:
            pass
    return None, "invalid"


def num(x):
    try:
        return float(str(x).replace("$", "").replace(",", "").strip())
    except Exception:
        return None


def grade(obj, gold):
    g = {"json_valid": obj is not None}
    if obj is None:
        return g
    g["schema_keys"] = sum(1 for k in REQUIRED if k in obj)
    hit = 0
    for k in ("vendor", "po_number", "order_date", "currency"):
        hit += str(obj.get(k) or "").strip().lower() == gold[k].lower()
    ship = str(obj.get("ship_to") or "").replace(" ", "").lower()
    hit += gold["ship_to"].replace(" ", "").lower() in ship.replace(" ", "")
    g["fields"] = hit
    for k in ("subtotal", "freight", "tax", "total"):
        g["field_" + k] = num(obj.get(k)) is not None and abs(num(obj.get(k)) - gold[k]) <= 0.01
    items = obj.get("line_items") if isinstance(obj.get("line_items"), list) else []
    by_sku = {}
    for it in items:
        if isinstance(it, dict) and it.get("sku"):
            by_sku[str(it["sku"]).strip().upper()] = it
    exact = 0
    for gi in gold["line_items"]:
        it = by_sku.get(gi["sku"])
        if not it:
            continue
        exact += (num(it.get("qty")) == gi["qty"]
                  and abs((num(it.get("unit_price")) or -1) - gi["unit_price"]) <= 0.01
                  and abs((num(it.get("amount")) or -1) - gi["amount"]) <= 0.01)
    g["items_found"] = len(items)
    g["items_exact"] = exact
    try:
        rows = [num(it.get("amount")) for it in items]
        s = sum(r for r in rows if r is not None)
        sub, fr, tx, tot = (num(obj.get(k)) for k in ("subtotal", "freight", "tax", "total"))
        g["arith_consistent"] = (None not in (sub, fr, tx, tot)
                                 and abs(s - sub) <= 0.05 and abs(sub + fr + tx - tot) <= 0.05)
    except Exception:
        g["arith_consistent"] = None
    return g


def run(args, doc, gold):
    prompt = PROMPT.format(schema=SCHEMA, doc=doc)
    r = common.chat(args, [{"role": "user", "content": prompt}],
                    max_tokens=args.max_tokens, temperature=args.temperature)
    if r.get("error"):
        payload = {"arm": "po", "error": r["error"], "finish": r.get("finish"), "s": r.get("s")}
        common.save_results(args, "PO", payload)
        common.say("PO  transport error:", r["error"])
        return 1
    obj, mode = parse_reply(r["content"])
    row = {"arm": "po", "parse": mode, "finish": r.get("finish"), "s": r.get("s"),
           "chars": len(r["content"]), "completion_tokens": r.get("completion_tokens"),
           "prompt_tokens": r.get("prompt_tokens"), "thinking": args.think,
           "max_tokens": args.max_tokens, "reply_head": r["content"][:600]}
    row.update(grade(obj, gold))
    if obj is not None:
        row["parsed"] = obj
    common.save_results(args, "PO", {"row": row})
    common.say("PO  parse=%s schema=%s/10 fields=%s/5 items=%s/8 arith=%s finish=%s %ss" % (
        mode, row.get("schema_keys"), row.get("fields"), row.get("items_exact"),
        row.get("arith_consistent"), r.get("finish"), r.get("s")))
    return 0 if mode in ("strict", "fenced", "wrapped") else 1


def selftest():
    c = common.Checks()
    doc, gold = build()
    c("fixture renders 8 line items", doc.count("NF-") == 8)
    c("gold totals add up", abs(gold["subtotal"] + gold["freight"] + gold["tax"] - gold["total"]) <= 0.01
      and abs(sum(i["amount"] for i in gold["line_items"]) - gold["subtotal"]) <= 0.01)
    clean = json.dumps(gold)
    c("strict parse", parse_reply(clean)[1] == "strict")
    c("fenced parse", parse_reply("```json\n%s\n```" % clean)[1] == "fenced")
    c("wrapped parse", parse_reply("Sure: %s thanks" % clean)[1] == "wrapped")
    c("invalid parse", parse_reply("vendor: Northwind, total: lots")[1] == "invalid")
    c("empty parse", parse_reply("   ")[1] == "empty")
    g = grade(json.loads(clean), gold)
    c("perfect reply scores 10/10 schema, 5/5 fields, 8/8 items, arith",
      g["schema_keys"] == 10 and g["fields"] == 5 and g["items_exact"] == 8 and g["arith_consistent"])
    bad = dict(gold)
    bad["total"] = gold["total"] + 10
    gb = grade(bad, gold)
    c("invented total fails the money check and the arithmetic check",
      not gb["field_total"] and gb["arith_consistent"] is False)
    short = dict(gold)
    short["line_items"] = gold["line_items"][:5]
    c("five of eight items scores 5/8", grade(short, gold)["items_exact"] == 5)
    # the committed fixture copies must not drift from the code
    docp, goldp = common.fixture_path("po", "document.txt"), common.fixture_path("po", "gold.json")
    if os.path.exists(docp) and os.path.exists(goldp):
        c("committed document matches the code", common.read_text(docp) == doc)
        c("committed gold matches the code", json.load(open(goldp)) == gold)
    else:
        common.say("note: run --gen to write the fixture copies")
    return c.finish()


def gen():
    d = os.path.dirname(common.fixture_path("po", "x"))
    os.makedirs(d, exist_ok=True)
    doc, gold = build()
    open(common.fixture_path("po", "document.txt"), "w").write(doc)
    json.dump(gold, open(common.fixture_path("po", "gold.json"), "w"), indent=1)
    common.say("wrote", common.fixture_path("po", "document.txt"), "and gold.json")


def main():
    ap = argparse.ArgumentParser(description="PO-SYN: purchase-order extraction, JSON validity scored apart from fields")
    ap.add_argument("--selftest", action="store_true", help="grade the graders; no model")
    ap.add_argument("--gen", action="store_true", help="rewrite tests/fixtures/po/ from the code")
    ap.add_argument("--max-tokens", type=int, default=1400, help="output cap (a trace needs several thousand)")
    ap.add_argument("--temperature", type=float, default=0.0)
    common.add_common_args(ap)
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if args.gen:
        gen()
        return
    doc, gold = build()
    sys.exit(run(args, doc, gold))


if __name__ == "__main__":
    main()
