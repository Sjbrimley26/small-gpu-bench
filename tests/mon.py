#!/usr/bin/env python3
"""MON: the money problem under a token cap.

One multi-step arithmetic word problem (quantity x price, then sales tax). The answer is computed in code
with Decimal, so there is nothing to look up. A reply passes if it contains the right amount to the cent;
a reply that hits the output cap (finish_reason=length) is a FAIL even if the number appears in a trace,
because the lab's finding was that thinking models fail this test by running out of budget, not by being
unable to do the sum.

  python3 tests/mon.py --selftest
  BASE_URL=... MODEL=... python3 tests/mon.py [--max-tokens 900] [--runs 3] [--think on]

The original problem has fixed numbers (14 slabs at $46.75, 9 boards at $12.40, 7.25% tax; answer 821.64);
this script keeps them.
"""
import argparse
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal as D

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

QTY_A, PRICE_A, QTY_B, PRICE_B, TAX = 14, D("46.75"), 9, D("12.40"), D("0.0725")
PROMPT = ("A contractor buys %d door slabs at $%s each and %d trim boards at $%s each. "
          "Sales tax is 7.25%%. What is the total cost? End your answer with the line "
          "'TOTAL: <amount>'." % (QTY_A, PRICE_A, QTY_B, PRICE_B))
EXACT = (QTY_A * PRICE_A + QTY_B * PRICE_B) * (1 + TAX)           # 821.6422...
ANSWER = EXACT.quantize(D("0.01"), ROUND_HALF_UP)                 # 821.64


def grade(content, finish):
    """-> (ok, amount_found). Looks at the TOTAL: line first, else any number equal to the answer."""
    if finish == "length":
        return False, None
    m = re.findall(r"TOTAL:\s*\$?\s*([\d,]+(?:\.\d+)?)", content or "", re.I)
    cands = m[-1:] or re.findall(r"\d[\d,]*\.\d+", content or "")
    for c in cands:
        try:
            if abs(D(c.replace(",", "")) - ANSWER) < D("0.005"):
                return True, c
        except Exception:  # noqa: BLE001
            pass
    return False, (cands[-1] if cands else None)


def selftest():
    c = common.Checks()
    c("answer computed in code is 821.64", str(ANSWER) == "821.64")
    c("exact value is 821.6422...", str(EXACT).startswith("821.6422"))
    c("right total passes", grade("work...\nTOTAL: $821.64", "stop")[0])
    c("comma-free amount passes", grade("TOTAL: 821.64", "stop")[0])
    c("rounding error (821.65) fails", not grade("TOTAL: 821.65", "stop")[0])
    c("untaxed total fails", not grade("TOTAL: 766.10", "stop")[0])
    c("cap hit fails even with the number present", not grade("TOTAL: 821.64", "length")[0])
    c("no number fails", not grade("I am not sure.", "stop")[0])
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--max-tokens", type=int, default=900, help="output cap; thinking models need several thousand")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    rows = []
    for i in range(a.runs):
        r = common.chat(a, [{"role": "user", "content": PROMPT}], max_tokens=a.max_tokens,
                        temperature=a.temperature, seed=100 + i)
        ok, found = grade(r["content"], r.get("finish"))
        rows.append({"run": i, "ok": ok, "found": found, "expected": str(ANSWER), "s": r["s"],
                     "finish": r.get("finish"), "completion_tokens": r.get("completion_tokens"),
                     "reasoning_chars": r.get("reasoning_chars"), "error": r.get("error"), "reply": r["content"]})
        common.say("run%d %s found=%s expected=%s finish=%s %.1fs %s" % (
            i, "PASS" if ok else "FAIL", found, ANSWER, r.get("finish"), r["s"], r.get("error") or ""))
    passed = sum(x["ok"] for x in rows)
    common.say("MON %d/%d" % (passed, len(rows)))
    common.save_results(a, "MON", {"passed": passed, "total": len(rows), "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
