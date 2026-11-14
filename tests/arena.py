#!/usr/bin/env python3
"""ARENA (synthetic): several models vote on the same payees, scored by agreement tier.

Offline scorer. It reads the CAT records that `tests/cat.py` wrote for two or more models (same fixture seed)
and asks the question the lab's payee arena asked: when several small models answer the same payee, does
agreement tell you which answers to trust? Nothing here calls a model, so it runs on records from any
server, and the fixture is the synthetic one (see tests/cat.py), not anyone's bank data.

Methods scored against the fixture's ground truth, on the rows that have a category in the ground truth:
  each model alone | weighted vote with quorum 1 | weighted vote with quorum 2 | full agreement
Agreement tiers per row: all agree (every model answered, same category) / quorum-2 only / lone answer /
no agreement / all unsure, each with the precision of filling that row with the vote's answer.
Also: the oracle ceiling (rows where any model was right) and rows where the models agree on a wrong
category, split by fixture tier (the shared blind spots).
Weights: each model's precision on what it answered (right / answered) in the same records.

  python3 tests/arena.py --selftest
  python3 tests/arena.py results/raw/CAT-a.json results/raw/CAT-b.json [results/raw/CAT-c.json ...] [--out-dir results/raw]
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import cat  # noqa: E402


def load_votes(paths):
    """-> ({model: {id: answer}}, n_rows_limit) from CAT records; refuses mixed fixture seeds."""
    votes, seeds = {}, set()
    for p in paths:
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("test") != "CAT":
            common.die("%s is not a CAT record" % p)
        seeds.add(rec.get("fixture_seed"))
        name = rec["model"]
        while name in votes:
            name += "'"
        votes[name] = {d["id"]: d["answer"] for d in rec["answers"]}
    if len(seeds) != 1:
        common.die("records come from different fixture seeds: %s" % sorted(map(str, seeds)))
    return votes


def _answer(v):
    return None if v in (None, cat.UNSURE) else v


def weights(rows, votes):
    w = {}
    for m, ans in votes.items():
        right = wrong = 0
        for r in rows:
            o = cat.outcome(ans.get(r["id"]), r["gold"])
            right += o == "right"
            wrong += o in ("wrong", "false_fill")
        w[m] = right / (right + wrong) if right + wrong else 0.0
    return w


def tally(row, votes, w):
    """-> (tier, {category: (weight, nvotes)}) for one row."""
    per = collections.defaultdict(lambda: [0.0, 0])
    answered = 0
    for m, ans in votes.items():
        a = _answer(ans.get(row["id"]))
        if a is None:
            continue
        answered += 1
        per[a][0] += w[m]
        per[a][1] += 1
    top = max((v[1] for v in per.values()), default=0)
    n = len(votes)
    if answered == 0:
        tier = "all unsure"
    elif answered == 1:
        tier = "lone answer"
    elif len(per) == 1 and answered == n:
        tier = "all agree"
    elif top >= 2:
        tier = "quorum-2 only"
    else:
        tier = "no agreement"
    return tier, per


def vote(per, quorum):
    """Weighted winner among categories with >= quorum votes; a tie in weight is no answer."""
    ok = [(wt, c) for c, (wt, nv) in per.items() if nv >= quorum]
    if not ok:
        return None
    ok.sort(reverse=True)
    if len(ok) > 1 and abs(ok[0][0] - ok[1][0]) < 1e-9:
        return None
    return ok[0][1]


def analyse(rows, votes):
    w = weights(rows, votes)
    n = len(votes)
    res = {"models": list(votes), "weights": {m: round(x, 3) for m, x in w.items()}, "methods": {}, "tiers": {}, "oracle": 0,
           "shared_wrong_by_fixture_tier": {}}
    for m, ans in votes.items():
        res["methods"][m] = collections.Counter(cat.outcome(ans.get(r["id"]), r["gold"]) for r in rows)
    meth = {"vote quorum 1": collections.Counter(), "vote quorum 2": collections.Counter(), "full agreement": collections.Counter()}
    tiers = collections.defaultdict(collections.Counter)
    for r in rows:
        tier, per = tally(r, votes, w)
        fills = {"vote quorum 1": vote(per, 1), "vote quorum 2": vote(per, 2),
                 "full agreement": (next(iter(per)) if tier == "all agree" else None)}
        for k, v in fills.items():
            meth[k][cat.outcome(v, r["gold"])] += 1
        tiers[tier][cat.outcome(fills["vote quorum 1"] if tier != "no agreement" else None, r["gold"])] += 1
        if r["gold"] != cat.UNSURE and any(_answer(a.get(r["id"])) == r["gold"] for a in votes.values()):
            res["oracle"] += 1
        if tier == "all agree" and next(iter(per)) != r["gold"]:
            res["shared_wrong_by_fixture_tier"][r["tier"]] = res["shared_wrong_by_fixture_tier"].get(r["tier"], 0) + 1
    res["methods"].update(meth)
    res["tiers"] = tiers
    res["n_models"] = n
    return res


def fmt_counts(c):
    return "right %3d  wrong %3d  blank %3d | unknowable: abstained %3d, false fill %3d" % (
        c["right"], c["wrong"], c["blank"], c["abstain_ok"], c["false_fill"])


def report(res, n_cat):
    out = ["models: %s   weights (precision on answered): %s" % (", ".join(res["models"]), res["weights"]),
           "category rows in ground truth: %d, oracle ceiling (any model right): %d" % (n_cat, res["oracle"]), ""]
    for k, c in res["methods"].items():
        out.append("%-16s %s" % (k, fmt_counts(c)))
    out += ["", "agreement tier   rows  filled-right filled-wrong (a false fill on an unknowable row counts as wrong)"]
    for t in ("all agree", "quorum-2 only", "lone answer", "no agreement", "all unsure"):
        c = res["tiers"].get(t, collections.Counter())
        n = sum(c.values())
        out.append("%-16s %4d  %12d %12d" % (t, n, c["right"], c["wrong"] + c["false_fill"]))
    if res["shared_wrong_by_fixture_tier"]:
        out += ["", "all models agree on a wrong answer, by fixture tier: %s" % res["shared_wrong_by_fixture_tier"]]
    return "\n".join(out)


def selftest():
    c = common.Checks()
    rows = [{"id": "a", "gold": "Fuel", "tier": "clear"}, {"id": "b", "gold": "Fuel", "tier": "weak"},
            {"id": "c", "gold": "Groceries", "tier": "dual"}, {"id": "d", "gold": cat.UNSURE, "tier": "opaque"},
            {"id": "e", "gold": "Dining Out", "tier": "weak"}, {"id": "f", "gold": "Fuel", "tier": "misleading"}]
    v = {"m1": {"a": "Fuel", "b": "Fuel", "c": "Dining Out", "d": cat.UNSURE, "e": None, "f": "Utilities"},
         "m2": {"a": "Fuel", "b": "Groceries", "c": "Dining Out", "d": "Fuel", "e": "Dining Out", "f": "Utilities"},
         "m3": {"a": "Fuel", "b": None, "c": "Groceries", "d": None, "e": None, "f": None}}
    w = weights(rows, v)
    c("weights are right/(right+wrong)", abs(w["m1"] - 2 / 4) < 1e-9 and abs(w["m3"] - 1.0) < 1e-9)
    t = {r["id"]: tally(r, v, w)[0] for r in rows}
    c("tiers", t == {"a": "all agree", "b": "no agreement", "c": "quorum-2 only",
                      "d": "lone answer", "e": "lone answer", "f": "quorum-2 only"})
    res = analyse(rows, v)
    c("oracle ceiling = rows where any model is right (a, b, c, e)", res["oracle"] == 4)
    fa = res["methods"]["full agreement"]
    c("full agreement fills only row a (right) and nothing else", fa["right"] == 1 and fa["wrong"] == 0 and fa["false_fill"] == 0)
    q2 = res["methods"]["vote quorum 2"]
    c("quorum 2 fills a, c(wrong), f(wrong); d is a lone answer so blank/abstain", q2["right"] == 1 and q2["wrong"] == 2 and q2["abstain_ok"] == 1)
    q1 = res["methods"]["vote quorum 1"]
    c("quorum 1 on an unknowable row with a lone answer is a false fill", q1["false_fill"] == 1)
    c("shared wrong answers are attributed to their fixture tier", res["shared_wrong_by_fixture_tier"] == {})
    c("a tie in weight gives no answer", vote({"X": [1.0, 1], "Y": [1.0, 1]}, 1) is None)
    c("report renders", "oracle ceiling" in report(res, 5))
    return c.finish()


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("records", nargs="*", help="CAT result JSON files, one per model")
    ap.add_argument("--out-dir", default="results/raw")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if len(a.records) < 2:
        common.die("need at least two CAT records")
    votes = load_votes(a.records)
    fx = cat.load_fixture()
    ids = set.intersection(*(set(v) for v in votes.values()))     # rows every model was asked
    rows = [r for r in fx["rows"] if r["id"] in ids]
    res = analyse(rows, votes)
    n_cat = sum(r["gold"] != cat.UNSURE for r in rows)
    common.say("%d rows asked of every model" % len(rows))
    common.say(report(res, n_cat))
    ser = json.loads(json.dumps(res, default=lambda o: dict(o)))
    args = argparse.Namespace(model="+".join(votes), out_dir=a.out_dir, records=[os.path.basename(p) for p in a.records])
    common.save_results(args, "ARENA", {"synthetic": True, "n_rows": len(rows), "n_category_rows": n_cat, "result": ser})
    return 0


if __name__ == "__main__":
    sys.exit(main())
