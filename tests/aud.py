#!/usr/bin/env python3
"""AUD: planted-error document audit.

Two documents (7.7k and 9.0k words) of unique administrative sentences are generated in code from a seed
(fixtures/aud_text.json holds the sentence pool and the planted passages). The only figures in a document
are the planted ones, so any other number a model cites is unambiguously a false positive.

  aud1  price conflict (12.40 vs 14.10 for the same item) and a freight arithmetic error (4 x 285.00 invoiced
        as 1,420.00 instead of 1,140.00)
  aud2  contract-term conflict (net 30 vs net 45), a summation error (120 + 85 + 95 stated as 330) and an
        impossible chronology (ordered 22 July, arrived 14 July)

A planted error counts as FOUND only if the ANSWER text names both of its figures (a reasoning trace is
ignored). Reported per document: errors found, "says none" (claims the document is clean), empty replies,
cap hits (finish_reason=length), and runs that cite a number not in the document.

The sentence pool and planted passages come from the lab's own audit fixtures, with two business names
replaced by fictional ones; the documents are therefore not byte-identical to the published runs.

  python3 tests/aud.py --selftest
  BASE_URL=... MODEL=... python3 tests/aud.py [--docs aud1,aud2] [--runs 3] [--max-tokens 3000]
"""
import argparse
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

QUOTED = ("Answer about the document above. Do not comment on its formatting, padding, repetition or "
          "structure; only report findings about the content.")
Q_INC = (QUOTED + " Are there any inconsistencies or errors in the figures or facts in this document? "
         "List each one briefly with the figures involved. If there are none, say so.")
NEG = re.compile(r"\b(no|not|without|none|zero)\b(\s+\w+){0,3}\s+(inconsistenc|error|discrepanc|issue|mismatch)", re.I)


def build_doc(spec):
    rng = random.Random(spec["seed"])
    combos = [(b, t) for b in spec["bases"] for t in spec["tails"]]
    rng.shuffle(combos)
    sents = ["%s, %s." % (b, t) for b, t in combos[:spec["n_sentences"]]]
    assert len(set(sents)) == len(sents), "duplicate sentences generated"
    paras, i = [], 0
    while i < len(sents):
        n = rng.choice((3, 4, 5, 6))
        paras.append(" ".join(sents[i:i + n]))
        i += n
    plants = spec["plants"]
    n_slots = len(paras) + len(plants)
    marks = {int(round(f * n_slots)): body for f, (_, body) in zip(spec["fracs"], plants)}
    assert len(marks) == len(plants)
    return "\n\n".join(marks[i] if i in marks else paras.pop(0) for i in range(n_slots))


def load_docs():
    spec = json.load(open(common.fixture_path("aud_text.json")))
    return {"aud1": build_doc(spec["a1"]), "aud2": build_doc(spec["a2"])}


def _nums(s):
    """Numbers as floats, thousands separators removed, so '1,420' and '1420.00' are the same figure.
    (The lab's original grader split '1,420' into 1 and 420 and flagged 420 as invented.)"""
    return {float(x) for x in re.findall(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3})", "", s))}


def has(c, *alts):
    return any(a in c for a in alts)


def grade_aud(task, answer, doc):
    c = answer or ""
    low = c.lower()
    if task == "aud1":
        f = {"price": "12.40" in c and "14.10" in c,
             "freight": has(c, "1,420", "1420") and has(c, "1,140", "1140", "285")}
    else:
        f = {"terms": bool(re.search(r"net\s*30", low) and re.search(r"net\s*45", low)) or
                      (("30" in c and "45" in c) and "day" in low and "millworks" in low),
             "sum": "330" in c and has(c, "300"),
             "chrono": ("22 july" in low or "july 22" in low) and ("14 july" in low or "july 14" in low)}
    f = {k: bool(v) for k, v in f.items()}
    n_found = sum(f.values())
    allowed_derived = {1140.0, 300.0, 14.1}      # figures a correct answer derives or restates
    doc_nums = _nums(doc)
    unknown = sorted(n for n in _nums(c) if n not in doc_nums and n >= 10 and n not in allowed_derived)[:8]
    return {"found": f, "n_found": n_found, "n_planted": len(f), "says_none": bool(NEG.search(c)) and n_found == 0,
            "unknown_numbers": [("%g" % n) for n in unknown], "empty": not c.strip()}


def selftest():
    c = common.Checks()
    docs = load_docs()
    for k, d in docs.items():
        c("%s: %d words (build_doc asserts no duplicate sentences)" % (k, len(d.split())), len(d.split()) > 5000)
    c("aud1 planted figures present", all(x in docs["aud1"] for x in ("12.40", "14.10", "285.00", "1,420.00")))
    c("aud2 planted figures present", all(x in docs["aud2"] for x in ("net 30", "net 45", "330 units", "22 July", "14 July")))
    c("aud1 carries only the planted figures", _nums(docs["aud1"]) == {3.0, 4.0, 5.0, 8.0, 12.4, 14.1, 285.0, 1420.0})
    c("aud2 carries only the planted figures", _nums(docs["aud2"]) == {14.0, 22.0, 30.0, 45.0, 85.0, 95.0, 120.0, 330.0})
    full1 = "Price: 12.40 vs 14.10. Freight: 4 loads at 285.00 = 1,140 but invoiced 1,420."
    g = grade_aud("aud1", full1, docs["aud1"])
    c("grader: aud1 both found", g["n_found"] == 2 and not g["unknown_numbers"])
    g = grade_aud("aud1", "Nothing wrong. There are no inconsistencies.", docs["aud1"])
    c("grader: says-none detected, 0 found", g["n_found"] == 0 and g["says_none"])
    full2 = "Northgate Millworks net 30 vs net 45; hinges 120+85+95=300 not 330; ordered 22 July, arrived 14 July."
    g = grade_aud("aud2", full2, docs["aud2"])
    c("grader: aud2 all three found", g["n_found"] == 3)
    g = grade_aud("aud2", "It says 7777 units somewhere.", docs["aud2"])
    c("grader: invented number flagged", g["unknown_numbers"] == ["7777"])
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--docs", default="aud1,aud2")
    ap.add_argument("--runs", type=int, default=1, help="runs per document")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    docs = load_docs()
    rows = []
    for name in [x for x in a.docs.split(",") if x]:
        for run in range(a.runs):
            msgs = [{"role": "user", "content": Q_INC + "\n\nDOCUMENT:\n" + docs[name]}]
            r = common.chat(a, msgs, max_tokens=a.max_tokens, temperature=a.temperature, seed=100 + run)
            g = grade_aud(name, r["content"], docs[name]) if not r.get("error") else {"error": True}
            rows.append({"doc": name, "run": run, "s": r["s"], "finish": r.get("finish"), "error": r.get("error"),
                         "prompt_tokens": r.get("prompt_tokens"), "reply": r["content"], **g})
            common.say("%s run%d found %s/%s %s cap=%s %.0fs %s" % (
                name, run, g.get("n_found"), g.get("n_planted"), g.get("found"), r.get("finish") == "length", r["s"],
                r.get("error") or ""))
    summary = {}
    for name in sorted({x["doc"] for x in rows}):
        rs = [x for x in rows if x["doc"] == name and not x.get("error")]
        summary[name] = {"runs": len(rs), "all_found": sum(x["n_found"] == x["n_planted"] for x in rs),
                         "any_found": sum(x["n_found"] > 0 for x in rs), "says_none": sum(x["says_none"] for x in rs),
                         "empty": sum(x["empty"] for x in rs), "cap_hit": sum(x["finish"] == "length" for x in rs),
                         "runs_with_unknown_numbers": sum(bool(x["unknown_numbers"]) for x in rs)}
        common.say("AUD %s %s" % (name, summary[name]))
    common.save_results(a, "AUD", {"summary": summary, "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
