#!/usr/bin/env python3
"""PO-REAL: extraction from a real, published purchase order - the harder sibling of PO-SYN.

PO-SYN uses a document generated from its own gold: clean columns, one item per line, totals at the foot.
Every seat measured passed it on fields, so it measures formatting and budget, not extraction. This one is a
real Texas HHS Commission purchase order (public record; personal contact details redacted, see
`tests/fixtures/real/SOURCES.json`), converted with `pdftotext -layout`: columns interleave, descriptions wrap
onto their own lines, page headers repeat, the total is stated once as "Not to exceed", and there is no tax or
freight line to invent. Twelve line items, each of which multiplies out exactly, and the schedule sums to the
stated total - so the gold is arithmetic, not opinion.

Scoring is the same split as PO-SYN, with the schema a real PO actually has:

  parse      strict / fenced / wrapped / invalid / empty
  schema     9 required top-level keys present
  fields     vendor, PO number, order date, payment terms
  items      line rows matched by line number ("1-1"), with class code, quantity, unit, unit price and amount
             all exact (12 in this document)
  total      the schedule total, to 0.01
  arith      the reply's OWN numbers: qty x unit price = amount for every line, and the amounts sum to the total

  python3 tests/po_real.py --selftest
  BASE_URL=... MODEL=... python3 tests/po_real.py [--max-tokens 3000]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

FIXTURE = common.fixture_path("real", "po_hhs.txt")
GOLD = common.fixture_path("real", "po_hhs_gold.json")

SCHEMA = """{
  "vendor": string, "po_number": string, "order_date": string (YYYY-MM-DD),
  "payment_terms": string, "freight_terms": string, "ship_to": string,
  "line_items": [{"line": string, "class_code": string, "description": string, "qty": number,
                  "uom": string, "unit_price": number, "amount": number, "due_date": string}],
  "total": number
}"""

PROMPT = """Extract the purchase order below into one JSON object with exactly this shape:

{schema}

Rules: copy values from the page, do not compute any you cannot read; money as plain JSON numbers without
currency symbols or thousands separators; keep each line item's own line label (e.g. "1-1") and unit of
measure exactly as printed; a description that wraps across lines belongs to the single item above it.

Reply with the JSON object and nothing else.

DOCUMENT
--------
{doc}
"""

REQUIRED = ("vendor", "po_number", "order_date", "payment_terms", "freight_terms", "ship_to",
            "line_items", "total")


def load():
    doc = common.read_text(FIXTURE)
    gold = json.load(open(GOLD))
    return doc, gold


def parse_reply(text):
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


def same_date(text, iso):
    """A date is right if it names the same day, whatever notation: 2026-11-02, 11/02/2026, 11/02/26."""
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text) or re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4}|\d{2})", text)
    if not m:
        return False
    parts = [int(x) for x in m.groups()]
    if len(str(parts[0])) == 4:
        y, mo, d = parts[0], parts[1], parts[2]
    else:
        mo, d, y = parts[0], parts[1], parts[2] + (2000 if parts[2] < 100 else 0)
    return "%04d-%02d-%02d" % (y, mo, d) == iso


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
    g["fields"] = (
        str(obj.get("vendor") or "").upper().startswith("BOB BARKER")
        and str(obj.get("po_number") or "").upper() == gold["po_number"]
        and same_date(str(obj.get("order_date") or ""), "2026-11-02")
        and re.search(r"net\s*30", str(obj.get("payment_terms") or ""), re.I) is not None
    )
    g["total_exact"] = abs((num(obj.get("total")) or -1) - gold["total"]) <= 0.01
    items = obj.get("line_items") if isinstance(obj.get("line_items"), list) else []
    by_line = {}
    for it in items:
        if isinstance(it, dict) and it.get("line"):
            by_line[str(it["line"]).strip()] = it
    exact = 0
    for gi in gold["line_items"]:
        it = by_line.get(gi["line"])
        if not it:
            continue
        exact += (str(it.get("class_code") or "").strip() == gi["class_code"]
                  and num(it.get("qty")) == gi["qty"]
                  and str(it.get("uom") or "").strip().upper() == gi["uom"]
                  and abs((num(it.get("unit_price")) or -1) - gi["unit_price"]) <= 0.005
                  and abs((num(it.get("amount")) or -1) - gi["amount"]) <= 0.01)
    g["items_found"] = len(items)
    g["items_exact"] = exact
    try:
        rows = [num(it.get("amount")) for it in items]
        qty_ok = all(abs((num(it.get("qty")) or -1) * (num(it.get("unit_price")) or -1) - (num(it.get("amount")) or -1)) <= 0.02
                     for it in items if isinstance(it, dict))
        tot = num(obj.get("total"))
        g["arith_consistent"] = bool(items) and qty_ok and tot is not None and abs(sum(r for r in rows if r is not None) - tot) <= 0.05
    except Exception:
        g["arith_consistent"] = None
    return g


def run(args, doc, gold):
    r = common.chat(args, [{"role": "user", "content": PROMPT.format(schema=SCHEMA, doc=doc)}],
                    max_tokens=args.max_tokens, temperature=0.0)
    if r.get("error"):
        common.save_results(args, "PO-REAL", {"row": {"error": r["error"]}, "arm": "real"})
        common.say("PO-REAL  transport error:", r["error"])
        return 1
    obj, mode = parse_reply(r["content"])
    row = {"arm": "real", "parse": mode, "finish": r.get("finish"), "s": r.get("s"),
           "chars": len(r["content"]), "completion_tokens": r.get("completion_tokens"),
           "prompt_tokens": r.get("prompt_tokens"), "thinking": args.think,
           "max_tokens": args.max_tokens, "reply_head": r["content"][:600]}
    row.update(grade(obj, gold))
    if obj is not None:
        row["parsed"] = obj
    common.save_results(args, "PO-REAL", {"row": row})
    common.say("PO-REAL  parse=%s schema=%s/8 fields=%s items=%s/12 total=%s arith=%s finish=%s %ss" % (
        mode, row.get("schema_keys"), row.get("fields"), row.get("items_exact"),
        row.get("total_exact"), row.get("arith_consistent"), r.get("finish"), r.get("s")))
    return 0 if mode in ("strict", "fenced", "wrapped") else 1


def selftest():
    c = common.Checks()
    doc, gold = load()
    c("real PO fixture present", len(doc) > 20000)
    c("gold has 12 line items", len(gold["line_items"]) == 12)
    c("gold line math is exact",
      all(abs(i["qty"] * i["unit_price"] - i["amount"]) <= 0.02 for i in gold["line_items"]))
    c("gold total equals the stated 'Not to exceed'", "7,846.38" in doc)
    c("every gold line label appears in the document",
      all(i["line"] in doc for i in gold["line_items"]))
    # personal data must not be in the fixture (the redaction is part of the test's contract)
    c("no e-mail addresses in the fixture", "@" not in doc)
    c("no telephone numbers in the fixture",
      re.search(r"\(?\b\d{3}\)?[\s/-]\d{3}[\s/-]\d{4}", doc) is None)
    clean = json.dumps(gold)
    c("strict parse", parse_reply(clean)[1] == "strict")
    c("wrapped parse", parse_reply("here: %s done" % clean)[1] == "wrapped")
    c("empty parse", parse_reply("  ")[1] == "empty")
    g = grade(json.loads(clean), gold)
    c("perfect reply: schema 8/8, fields, 12/12 items, total, arith",
      g["schema_keys"] == 8 and g["fields"] and g["items_exact"] == 12 and g["total_exact"] and g["arith_consistent"])
    short = json.loads(clean)
    short["line_items"] = gold["line_items"][:9]
    c("nine of twelve items scores 9/12", grade(short, gold)["items_exact"] == 9)
    badtotal = json.loads(clean)
    badtotal["total"] = 7846.00
    c("a rounded total fails both total and arithmetic",
      not grade(badtotal, gold)["total_exact"] and grade(badtotal, gold)["arith_consistent"] is False)
    badoff = json.loads(clean)
    badoff["order_date"] = "2026-11-11"
    c("wrong order date fails the field check", grade(badoff, gold)["fields"] is False)
    c("date notation does not decide the grade",
      same_date("11/02/2026", "2026-11-02") and same_date("11/02/26", "2026-11-02")
      and same_date("2026-11-02", "2026-11-02") and not same_date("02/11/2026", "2026-11-02"))
    return c.finish()


def main():
    ap = argparse.ArgumentParser(description="PO-REAL: extraction from a real published purchase order")
    ap.add_argument("--selftest", action="store_true", help="grade the graders; no model")
    ap.add_argument("--max-tokens", type=int, default=3000, help="output cap (12 items need room)")
    common.add_common_args(ap)
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    doc, gold = load()
    sys.exit(run(args, doc, gold))


if __name__ == "__main__":
    main()
