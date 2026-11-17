#!/usr/bin/env python3
"""HRD: a harder 10-problem reasoning battery whose ground truth is computed in code.

Money with distractors, saw-kerf yield, linear feet, concrete bags, business-day date arithmetic (twice), a
brute-force-verified logic puzzle, a code trace, and two UNDERDETERMINED questions where the right answer is
"CANNOT BE DETERMINED" (one numeric, one logic; the logic one is asserted underdetermined by brute force).
Each reply is graded by code on its last 'ANSWER: <value>' line; a reply that hits the output cap is a fail.

The lab ran it with thinking ON, 16k context, a 6000-token cap, at temperature 0 and 0.7 (so 20 graded
replies per model). Defaults here match the cap and temperatures (--max-tokens 6000 --temps 0,0.7); pass --think on to
ask for reasoning (the default sends nothing and leaves it to the server).

  python3 tests/hrd.py --check                  print the problems' computed answers (no model)
  python3 tests/hrd.py --selftest               grader checks, no model
  BASE_URL=... MODEL=... python3 tests/hrd.py [--temps 0,0.7] [--only money,kerf] [--think on]
"""
import argparse
import datetime as dt
import itertools
import math
import os
import re
import sys
from decimal import Decimal as D, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

FMT = "\n\nWork it out, then end with exactly one final line in the form 'ANSWER: <value>'."
UNDET = " If the information given is not enough to determine the answer, the final line must be 'ANSWER: CANNOT BE DETERMINED'."

P = []  # (id, prompt, kind, expected)

# 1 money with distractors
c = lambda x: D(x)
bal, post, rail = 18 * c("6.35"), 6 * c("48.90"), 3 * c("72.00")
disc = (bal + rail) * c("0.12")
sub = bal + post + rail - disc
tot = (sub * c("1.0825") + c("55")).quantize(D("0.01"), ROUND_HALF_UP)
P.append(("money", "A contractor with 4 employees and a 2019 pickup orders 18 stair balusters at $6.35 each, 6 newel "
  "posts at $48.90 each and 3 handrails at $72.00 each. A 12% discount applies to the balusters and handrails only, "
  "not to the newel posts. Sales tax of 8.25% applies to the whole order after the discount. A flat delivery fee of "
  "$55.00 is added after tax and is not taxed. What is the final amount owed, in dollars to the cent?",
  "num", float(tot)))

# 2 kerf
L, k, W, need = D("21.75"), D("0.125"), D("96"), 31
n = max(i for i in range(1, 20) if i * L + (i - 1) * k <= W)
P.append(("kerf", "Stock boards are 96 inches long. You need 31 pieces, each 21.75 inches long. Each saw cut removes "
  "0.125 inch (kerf); the factory ends of a board are square so no cut is needed at the ends, which means a board "
  "yielding n pieces takes n-1 cuts. The shop's saw is a 10-inch blade. Pieces cannot be joined. How many stock "
  "boards must you buy?", "num", math.ceil(need / n)))

# 3 linear feet
perim = 2 * (14 + 11.5)
net = perim - 3 - 3 - 6
sticks = math.ceil(net * 1.10 / 8)
cost = sticks * 8 * 1.85
P.append(("linear_ft", "A room is 14 ft by 11.5 ft with 9 ft ceilings. Baseboard runs along the full perimeter except "
  "two 3-foot doorways and one 6-foot wide opening. Add 10% waste to the required length, then buy whole 8-foot "
  "sticks. The baseboard costs $1.85 per linear foot (you pay for the full length of each stick). What is the total "
  "cost in dollars?", "num", round(cost, 2)))

# 4 concrete
cf = 12 * 20 * (4 / 12) * 1.05
bags = math.ceil(cf / 0.6)
P.append(("concrete", "A slab is 12 feet by 20 feet and 4 inches thick. Order 5% extra volume. Each 80 lb bag of mix "
  "yields 0.6 cubic feet. The slab will use rebar on a 16-inch grid, which does not change the volume needed. How "
  "many whole bags are needed?", "num", bags))

# 5 date add (business days)
hol = {dt.date(2026, 11, 26), dt.date(2026, 12, 25)}
d = dt.date(2026, 11, 23); left = 37
while left:
    d += dt.timedelta(days=1)
    if d.weekday() < 5 and d not in hol:
        left -= 1
P.append(("date_add", "A job starts on Monday 2026-11-23 (day zero). Work happens only on weekdays (Mon-Fri) and "
  "not on 2026-11-26 or 2026-12-25 (holidays). On what date does the 37th working day after the start date fall? "
  "(The start date itself is not counted; the first working day is the next eligible day.) Give the date as "
  "YYYY-MM-DD.", "str", d.isoformat()))

# 6 logic puzzle, unique
people = ["Ana", "Ben", "Cy", "Dee"]; tools = ["saw", "drill", "plane", "level"]
def sols(clues):
    out = []
    for perm in itertools.permutations(tools):
        a = dict(zip(people, perm))
        if all(f(a) for f in clues):
            out.append(a)
    return out
txt_u = ["Ana has neither the saw nor the drill.", "Ben does not have the plane.",
         "Cy has the drill or the level.", "Dee does not have the level.", "Ben has the saw.", "Ana does not have the level."]
cl_u = [lambda a: a["Ana"] not in ("saw", "drill"), lambda a: a["Ben"] != "plane",
        lambda a: a["Cy"] in ("drill", "level"), lambda a: a["Dee"] != "level", lambda a: a["Ben"] == "saw", lambda a: a["Ana"] != "level"]
su = sols(cl_u); assert len(su) == 1, su
P.append(("logic_unique", "Four people - Ana, Ben, Cy and Dee - each have exactly one different tool from: saw, drill, "
  "plane, level. Clues: " + " ".join(txt_u) + " Who has the drill? Answer with the name only.", "str", [p for p in people if su[0][p] == "drill"][0]))

# 7 code trace
def f(n):
    a, b = 0, 1; cc = 0
    for i in range(n):
        a, b = b, (a + b) % 10
        if a % 2 == 0:
            cc += b
    return cc
P.append(("code_trace", "What does f(15) return?\n\ndef f(n):\n    a, b = 0, 1\n    c = 0\n    for i in range(n):\n"
  "        a, b = b, (a + b) % 10\n        if a % 2 == 0:\n            c += b\n    return c\n", "num", f(15)))

# 8 date diff
hol2 = {dt.date(2026, 11, 26), dt.date(2026, 12, 25), dt.date(2027, 1, 1)}
d0, d1 = dt.date(2026, 10, 19), dt.date(2027, 1, 15)
cnt = 0; x = d0
while x <= d1:
    if x.weekday() < 5 and x not in hol2: cnt += 1
    x += dt.timedelta(days=1)
P.append(("date_diff", "How many working days are there from 2026-10-19 through 2027-01-15, counting both of those "
  "days if they qualify? Working days are Monday to Friday excluding these holidays: 2026-11-26, 2026-12-25, "
  "2027-01-01. (2026-10-19 is a Monday.)", "num", cnt))

# 9 undetermined (numeric)
P.append(("undet_screws", "A box holds only #8 and #10 screws, 150 screws in total. 60% of all the screws are "
  "zinc-plated, and the box weighs 2.4 kg including its 300 g cardboard shell. How many of the screws are #10?" + UNDET,
  "undet", "CANNOT BE DETERMINED"))

# 10 undetermined (logic, verified underdetermined by brute force)
txt_a = ["Ana has neither the saw nor the drill.", "Ben does not have the plane.", "Dee does not have the level."]
cl_a = [cl_u[0], cl_u[1], cl_u[3]]
sa = sols(cl_a); assert len({a["Cy"] for a in sa}) > 1
P.append(("undet_logic", "Four people - Ana, Ben, Cy and Dee - each have exactly one different tool from: saw, drill, "
  "plane, level. Clues: " + " ".join(txt_a) + " Which tool does Cy have? Answer with the tool name only." + UNDET,
  "undet", "CANNOT BE DETERMINED"))


def final(text):
    m = re.findall(r"ANSWER:\s*(.+)", text, re.I)
    return m[-1].strip().strip("*` ") if m else None

def grade(kind, exp, text):
    a = final(text)
    if a is None: return False, None
    if kind == "num":
        m = re.search(r"-?\d[\d,]*\.?\d*", a.replace("$", ""))
        if not m: return False, a
        try: return abs(float(m.group().replace(",", "")) - exp) < 0.006, a
        except ValueError: return False, a
    if kind == "str": return exp.lower() in a.lower(), a
    if kind == "undet": return "cannot be determined" in a.lower(), a


def selftest():
    c = common.Checks()
    ids = [p[0] for p in P]
    c("10 problems with unique ids", len(P) == 10 and len(set(ids)) == 10)
    exp = {p[0]: p[3] for p in P}
    indep = round(((18 * 6.35 + 3 * 72.0) * 0.88 + 6 * 48.90) * 1.0825 + 55, 2)   # same problem, written separately
    c("money answer matches an independent float computation (%.2f)" % indep, abs(exp["money"] - indep) < 0.006)
    c("business-day answers are weekdays", dt.date.fromisoformat(exp["date_add"]).weekday() < 5)
    c("undetermined problems expect CANNOT BE DETERMINED", exp["undet_screws"] == exp["undet_logic"] == "CANNOT BE DETERMINED")
    for pid, prompt, kind, e in P:
        right = "ANSWER: %s" % ("CANNOT BE DETERMINED" if kind == "undet" else e)
        c("grader accepts the right answer: " + pid, grade(kind, e, "reasoning\n" + right)[0])
        c("grader rejects a missing ANSWER line: " + pid, not grade(kind, e, "I think it is " + str(e))[0])
    c("numeric grader rejects a wrong number", not grade("num", 10, "ANSWER: 11")[0])
    c("comma/dollar tolerant", grade("num", 1263.03, "ANSWER: $1,263.03")[0])
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--temps", default="0,0.7", help="comma list of temperatures")
    ap.add_argument("--reasoning-budget", type=int, default=0,
                    help="send reasoning_budget_tokens (llama.cpp per-request cap on thinking); 0 = not sent")
    ap.add_argument("--only", default="", help="comma list of problem ids")
    ap.add_argument("--check", action="store_true", help="print computed answers and exit")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.check:
        for i, k, kind, e in P:
            print(i, kind, e)
        return 0
    if a.selftest:
        return selftest()
    only = {x for x in a.only.split(",") if x}
    temps = [float(t) for t in a.temps.split(",") if t]
    rows = []
    for temp in temps:
        for pid, prompt, kind, exp in P:
            if only and pid not in only:
                continue
            r = common.chat(a, [{"role": "user", "content": prompt + FMT}], max_tokens=a.max_tokens, temperature=temp,
                            extra={"reasoning_budget_tokens": a.reasoning_budget} if a.reasoning_budget else None)
            ok, ans = grade(kind, exp, r["content"])
            if r.get("finish") == "length":
                ok = False
            rows.append({"id": pid, "temp": temp, "ok": ok, "answer": ans, "expected": exp, "kind": kind, "s": r["s"],
                         "finish": r.get("finish"), "completion_tokens": r.get("completion_tokens"),
                         "reasoning_chars": r.get("reasoning_chars"), "error": r.get("error"), "reply": r["content"]})
            common.say("%s@%s: %s ans=%r exp=%r %.0fs finish=%s %s" % (pid, temp, "PASS" if ok else "FAIL", ans, exp,
                                                                        r["s"], r.get("finish"), r.get("error") or ""))
    summary = {str(t): "%d/%d" % (sum(x["ok"] for x in rows if x["temp"] == t), sum(1 for x in rows if x["temp"] == t))
               for t in temps}
    common.say("HRD correct per temperature: %s" % summary)
    common.save_results(a, "HRD", {"summary": summary, "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
