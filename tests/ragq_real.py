#!/usr/bin/env python3
"""RAGQ-REAL: grounded answering over real public documents, including two revision pairs.

RAGQ-SYN ships an invented makerspace corpus and every seat scored 6-9/10 on it, so it measures little more
than "the model can read". This one is built from documents that are real, published and free to redistribute
(see `tests/fixtures/real/SOURCES.json`) and that carry the failure modes a real corpus has:

  A  13 CFR 125.6, limitations on subcontracting - CFR 2026 annual edition
  B  13 CFR 125.6, same section - CFR 2023 annual edition (superseded; the rules differ)
  C  NIST SP 800-63B-4 section 3.1.1.2, password verifiers (July 2025)
  D  NIST SP 800-63B-3 section 5.1.1.2, memorized secret verifiers (June 2017; superseded)

Two real revision pairs, so "the source says X" and "the current rule is Y" genuinely differ: single-factor
passwords went from 8 to 15 characters between the NIST revisions, and the 2026 CFR adds a per-order compliance
rule for multi-agency set-asides that the 2023 text does not contain. Documents are shipped as retrieved
chunks (what a RAG pipeline actually hands a writer), each labelled with its title and edition date and nothing
else - no source is marked "current", so resolving a conflict is the model's job. The whole 125.6 section for
both editions also ships in `tests/fixtures/real/` so a reader can see the chunks in their context; the
questions use the chunks, and every chunk is a fixed window, not a per-run cut.

Fifteen questions in six shapes: exact, freshness (a superseded source disagrees), negative (absent from the
corpus), synthesis (two facts, one across a revision pair), arithmetic (apply the rule to a contract value -
quoting the percentage is not the answer), and vague. Scored by a per-question key of regexes that
must / must not match plus, where a stale value is legitimate, a rule that accepts it only when the answer
labels it as old or attaches it to the case it still applies to. No model judges anything.

  python3 tests/ragq_real.py --selftest
  BASE_URL=... MODEL=... python3 tests/ragq_real.py [--limit N] [--max-tokens 1200]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import ragq  # noqa: E402  (REFUSAL and the stale-value rule are shared with RAGQ-SYN)

DOCS = {
    "A": ("[A] 13 CFR 125.6 - Limitations on subcontracting (Code of Federal Regulations, 2026 annual edition)",
          "cfr125_6_2026_pct.txt"),
    "B": ("[B] 13 CFR 125.6 - Limitations on subcontracting (Code of Federal Regulations, 2023 annual edition)",
          "cfr125_6_2023_pct.txt"),
    "A2": ("[A2] 13 CFR 125.6 - period used to determine compliance (Code of Federal Regulations, 2026 annual edition)",
           "cfr125_6_2026_period.txt"),
    "B2": ("[B2] 13 CFR 125.6 - period used to determine compliance (Code of Federal Regulations, 2023 annual edition)",
           "cfr125_6_2023_period.txt"),
    "A3": ("[A3] 13 CFR 125.6 - compliance and past performance (Code of Federal Regulations, 2026 annual edition)",
           "cfr125_6_2026_pastperf.txt"),
    "B3": ("[B3] 13 CFR 125.6 - compliance and past performance (Code of Federal Regulations, 2023 annual edition)",
           "cfr125_6_2023_pastperf.txt"),
    "C": ("[C] NIST SP 800-63B-4, section 3.1.1.2 Password Verifiers (July 2025)", "nist63b4_3_1_1_2.txt"),
    "D": ("[D] NIST SP 800-63B-3, section 5.1.1.2 Memorized Secret Verifiers (June 2017)", "nist63b3_5_1_1_2.txt"),
}

MFA = re.compile(r"multi[\s-]?factor|\bMFA\b|second\s+factor|as\s+part\s+of\s+(an\s+)?(MFA|multi)", re.I)
COMPOSITION = re.compile(
    r"(no|not|never|dropped|removed)\b[^.\n]{0,60}(composition|character\s+type|mixture)|"
    r"composition[^.\n]{0,40}(rules?\s+)?(are|were|is|was)?\s*(not|no\s+longer)\b", re.I)


def near(text, a_rx, b_rx, window=220):
    """True if some match of a_rx has a match of b_rx within `window` characters."""
    for m in a_rx.finditer(text):
        seg = text[max(0, m.start() - window):m.end() + window]
        if b_rx.search(seg):
            return True
    return False


OLDNESS = re.compile(r"\b(old|older|previous|previously|prior|stale|superseded|no longer|used to|"
                     r"from|was|were|2017|rev\s?3|earlier|800-63B-3|63B-3)\b", re.I)
OTHER_CASE = re.compile(r"memorized\s+secrets?|\bPINs?\b", re.I)


def f2_rule(text):
    """15 is required; an 8 is acceptable only when tied to multi-factor use, the older revision, or PINs."""
    eight = re.compile(r"\b8\b|\beight\b")
    if not eight.search(text):
        return True
    for m in eight.finditer(text):
        seg = text[max(0, m.start() - 200):m.end() + 200]
        if not (MFA.search(seg) or OLDNESS.search(seg) or OTHER_CASE.search(seg)):
            return False
    return True


def f1_rule(text):
    """The multi-agency answer: the period of performance for each order."""
    order = re.compile(r"each\s+order|per\s+order|order'?s?\s+period", re.I)
    multi = re.compile(r"multi[\s-]?agency|more\s+than\s+one\s+agency|several\s+agencies", re.I)
    return near(text, order, multi, 260)


QUESTIONS = [
    dict(qid="E1", type="exact", docs=["A"],
         question="for a contract for services, what share of what the government pays may go to firms that are not similarly situated?",
         need=[("50%", re.compile(r"\b50\s?%|\b50\s*percent|half", re.I))]),
    dict(qid="E2", type="exact", docs=["C"],
         question="what maximum password length should verifiers and CSPs permit?",
         need=[("64", re.compile(r"\b64\b"))]),
    dict(qid="F1", type="freshness", docs=["A2", "B2"],
         question="under a multi-agency set-aside contract where more than one agency can issue orders, what period does the ordering agency use to determine compliance with the limitations on subcontracting?",
         custom=[("per-order rule tied to the multi-agency case", f1_rule)]),
    dict(qid="F2", type="freshness", docs=["C", "D"],
         question="what is the minimum length of a password used as a single-factor authentication mechanism?",
         need=[("15", re.compile(r"\b15\b"))],
         custom=[("an 8-char minimum only where it is tied to multi-factor use", f2_rule)]),
    dict(qid="N1", type="negative", docs=["A"],
         question="what is the small business size standard, in dollars, for NAICS 236220?",
         need=[("states it is not recorded", ragq.REFUSAL)],
         must_not=[("invents a dollar size standard", re.compile(r"(\$\s?\d|\b\d+(\.\d+)?\s*million)[^.\n]{0,40}(size|standard|average|receipts)", re.I))]),
    dict(qid="N2", type="negative", docs=["C", "D"],
         question="which password manager product does NIST recommend using?",
         need=[("says no product is named (or only the generic requirement)",
                re.compile(ragq.REFUSAL.pattern + r"|password\s+managers?", re.I))],
         must_not=[("names a product", re.compile(r"\b(1Password|Bitwarden|LastPass|KeePass|Dashlane|NordPass)\b", re.I))]),
    dict(qid="S1", type="synthesis", docs=["A"],
         question="list the subcontracting limits as percentages for services, general construction and special trade construction",
         need=[("services 50%", re.compile(r"\b50\s?%|\b50\s*percent", re.I)),
               ("general construction 85%", re.compile(r"\b85\s?%|\b85\s*percent", re.I)),
               ("special trade 75%", re.compile(r"\b75\s?%|\b75\s*percent", re.I))]),
    dict(qid="S2", type="synthesis", docs=["C", "D"],
         question="what changed between the two versions about password composition rules and the minimum length of a single-factor password?",
         need=[("15 for single factor", re.compile(r"\b15\b")),
               ("composition rules dropped", COMPOSITION)],
         custom=[("an 8-char minimum only where it is tied to multi-factor use", f2_rule)]),
    dict(qid="V1", type="vague", docs=["A"],
         question="how much of the work does the prime contractor have to keep in house?",
         need=[("50% for services", re.compile(r"\b50\s?%|\b50\s*percent|half", re.I))]),
    dict(qid="E3", type="exact", docs=["C"],
         question="what must a verifier do when a password a subscriber chooses appears on the blocklist?",
         need=[("rejects it and makes them choose another",
                re.compile(r"(select|choose|pick)\s+a\s+different|different\s+(secret|password)|reject", re.I)),
               ("gives the reason", re.compile(r"reason|why|explain", re.I))]),
    dict(qid="S3", type="synthesis", docs=["A3", "B3"],
         question="if a contractor finished a contract but did not meet the limitations on subcontracting, how is that handled in the past performance evaluation, and does the older text say the same?",
         need=[("the contractor gets an opportunity to explain extenuating circumstances",
                re.compile(r"opportunity\s+to\s+explain|extenuating\s+or\s+mitigating", re.I)),
               ("higher-level concurrence is needed for a satisfactory rating",
                re.compile(r"one\s+level\s+above", re.I))],
         must_not=[("claims the older text has the same rule",
                    re.compile(r"(older|2023|previous)[^.\n]{0,80}(same|identical)[^.\n]{0,40}(rule|text)", re.I))]),
    dict(qid="A1", type="arithmetic", docs=["A"],
         question="a services contract is worth 2,000,000 dollars. what is the most it may pay to firms that are not similarly situated?",
         need=[("1,000,000 (the 50% cap applied)", re.compile(r"1[,.]?000[,.]?000|\bone\s+million\b", re.I))]),
    dict(qid="A2", type="arithmetic", docs=["A"],
         question="a general construction contract is worth 4,000,000 dollars and 3,200,000 dollars of it goes to firms that are not similarly situated. is that compliant?",
         need=[("compliant, with the 85% cap stated",
                re.compile(r"85\s?%|3[,.]?400[,.]?000|3\.4\s*million", re.I)),
               ("says it is within the limit", re.compile(r"\b(compliant|complies|within|meets|allowed|satisfies)\b", re.I))],
         must_not=[("calls it non-compliant",
                    re.compile(r"\b(not|non)[-\s]?compliant|\bviolat|\bexceeds?\s+the\s+(85|limit|cap)", re.I))]),
    dict(qid="A3", type="arithmetic", docs=["A2"],
         question="an order is set aside under a full and open contract and was competed among small and other-than-small businesses. do the subcontracting limitations apply to that order?",
         need=[("says they do not apply",
                re.compile(r"(do(es)?|will)\s+not\s+apply|not\s+applicable|no\s+limitation|limitations?\s+(do\s+)?not\s+apply", re.I))]),
    dict(qid="V2", type="vague", docs=["C", "D"],
         question="how long should a password be these days?",
         need=[("15", re.compile(r"\b15\b"))],
         custom=[("an 8-char minimum only where it is tied to multi-factor use", f2_rule)]),
]

SYSTEM = ("You answer questions using only the NOTE CONTEXT below. If the context does not answer the "
          "question, say plainly that it is not recorded in the documents. Never invent values.")
PROMPT = """{system}

QUESTION: {q}

NOTE CONTEXT:
{ctx}
"""


def context_for(q):
    blocks = []
    for d in q["docs"]:
        head, path = DOCS[d]
        body = common.read_text(common.fixture_path("real", path))
        blocks.append(head + "\n" + body)
    return "\n\n".join(blocks)


def build_prompt(q):
    return PROMPT.format(system=SYSTEM, q=q["question"], ctx=context_for(q))


def grade(q, text):
    text = text or ""
    checks, ok = {}, True
    for label, rx in q.get("need", []):
        checks[label] = bool(rx.search(text))
        ok &= checks[label]
    for label, rx in q.get("must_not", []):
        checks["not " + label] = not rx.search(text)
        ok &= checks["not " + label]
    for label, fn in q.get("custom", []):
        checks[label] = bool(fn(text))
        ok &= checks[label]
    return {"checks": checks, "ok": ok}


def run(args):
    qs = QUESTIONS[: args.limit] if args.limit else QUESTIONS
    rows, transport = [], 0
    for q in qs:
        prompt = build_prompt(q)
        r = common.chat(args, [{"role": "user", "content": prompt}],
                        max_tokens=args.max_tokens, temperature=0.0)
        if r.get("error"):
            transport += 1
            rows.append({"qid": q["qid"], "type": q["type"], "error": r["error"], "ok": False})
            common.say("%-3s %-9s ERROR %s" % (q["qid"], q["type"], r["error"]))
            continue
        g = grade(q, r["content"])
        row = {"qid": q["qid"], "type": q["type"], "question": q["question"], "docs": q["docs"],
               "answer": r["content"], "correct": g["ok"], "checks": g["checks"],
               "chars": len(r["content"]), "prompt_tokens": r.get("prompt_tokens"),
               "completion_tokens": r.get("completion_tokens"), "finish": r.get("finish"), "s": r.get("s")}
        rows.append(row)
        common.say("%-3s %-9s %-5s %6ss %5s tok  %s" % (
            q["qid"], q["type"], "ok" if g["ok"] else "FAIL", r.get("s"),
            r.get("completion_tokens"), r["content"][:140].replace("\n", " ")))
    scored = [r for r in rows if "error" not in r]
    right = sum(1 for r in scored if r["correct"])
    by_type = {}
    for r in scored:
        t = by_type.setdefault(r["type"], [0, 0])
        t[0] += int(r["correct"])
        t[1] += 1
    summary = {"correct": right, "scored": len(scored), "transport_errors": transport,
               "by_type": {k: "%d/%d" % (v[0], v[1]) for k, v in by_type.items()},
               "mean_s": round(sum(r["s"] or 0 for r in scored) / len(scored), 1) if scored else None,
               "mean_completion_tokens": round(sum(r["completion_tokens"] or 0 for r in scored) / len(scored)) if scored else None,
               "mean_prompt_tokens": round(sum(r["prompt_tokens"] or 0 for r in scored) / len(scored)) if scored else None}
    common.say("\nRAGQ-REAL: %d/%d correct  %s  mean %ss  prompt %s tok  %s tok/answer%s" % (
        right, len(scored), summary["by_type"], summary["mean_s"], summary["mean_prompt_tokens"],
        summary["mean_completion_tokens"],
        "  INVALID (>25% transport errors)" if scored and transport > 0.25 * len(rows) else ""))
    common.save_results(args, "RAGQ-REAL", {"summary": summary, "rows": rows,
                                            "invalid": scored and transport > 0.25 * len(rows),
                                            "arm": "ragq-real", "thinking": args.think})
    return 1 if scored and transport > 0.25 * len(rows) else 0


def selftest():
    c = common.Checks()
    c("15 questions, six shapes", len(QUESTIONS) == 15
      and {q["type"] for q in QUESTIONS} == {"exact", "freshness", "negative", "synthesis", "arithmetic", "vague"})
    missing = [f for _, f in DOCS.values() if not os.path.exists(common.fixture_path("real", f))]
    c("every fixture the questions use is on disk", not missing)
    c("no fixture contains an e-mail address or phone number",
      all("@" not in common.read_text(common.fixture_path("real", f))
          and re.search(r"\(?\b\d{3}\)?[\s/-]\d{3}[\s/-]\d{4}", common.read_text(common.fixture_path("real", f))) is None
          for _, f in DOCS.values()))
    q = {x["qid"]: x for x in QUESTIONS}
    # every key that a source can satisfy must actually match that source, or the question is unanswerable
    a_pct = common.read_text(common.fixture_path("real", "cfr125_6_2026_pct.txt"))
    c_nist = common.read_text(common.fixture_path("real", "nist63b4_3_1_1_2.txt"))
    a_per = common.read_text(common.fixture_path("real", "cfr125_6_2026_period.txt"))
    b_per = common.read_text(common.fixture_path("real", "cfr125_6_2023_period.txt"))
    c("the 50/85/75 limits are all in the 2026 text",
      all(rx.search(a_pct) for _, rx in q["S1"]["need"]))
    c("64 is in the NIST 2025 text", q["E2"]["need"][0][1].search(c_nist) is not None)
    c("15 is in the NIST 2025 text", q["F2"]["need"][0][1].search(c_nist) is not None)
    c("the per-order multi-agency rule is only in the 2026 period text",
      f1_rule(a_per) and not f1_rule(b_per))
    c("the 2023 text does not carry the multi-agency rule", "multi-agency" not in b_per.lower())
    # positive and negative graders
    c("freshness: live rule passes",
      grade(q["F1"], "For a multi-agency set-aside the ordering agency uses the period of performance for each order.")["ok"])
    c("freshness: the 2023-only answer fails", not grade(q["F1"], "It is the base term and then each subsequent option period.")["ok"])
    c("freshness: 15 passes", grade(q["F2"], "15 characters for single-factor use.")["ok"])
    c("freshness: bare 8 fails", not grade(q["F2"], "8 characters.")["ok"])
    c("freshness: 8 tied to MFA passes",
      grade(q["F2"], "15 characters as a single factor; 8 is still allowed for multi-factor use.")["ok"])
    c("negative: refusal passes", grade(q["N1"], "The documents do not state a size standard for that NAICS code.")["ok"])
    c("negative: an invented figure fails", not grade(q["N1"], "The size standard is $30 million in average receipts.")["ok"])
    c("negative: naming a product fails", not grade(q["N2"], "NIST recommends Bitwarden.")["ok"])
    c("synthesis: all three percentages pass",
      grade(q["S1"], "Services 50%, general construction 85%, special trade construction 75%.")["ok"])
    c("synthesis: two of three fails",
      not grade(q["S1"], "Services 50% and general construction 85%.")["ok"])
    c("synthesis S2 needs the 15 and the no-composition rule",
      grade(q["S2"], "It went from 8 to 15 characters for single-factor use, and composition rules are not imposed.")["ok"])
    c("arithmetic: the 50% cap on $2M is $1M",
      grade(q["A1"], "It may pay at most 1,000,000 dollars, half the contract value.")["ok"])
    c("arithmetic: quoting only the percentage fails the $2M question",
      not grade(q["A1"], "Firms that are not similarly situated may receive no more than 50%.")["ok"])
    c("arithmetic: $3.2M of $4M construction is compliant",
      grade(q["A2"], "Yes - the 85% cap is 3,400,000, so 3,200,000 is within the limit.")["ok"])
    c("arithmetic: calling it non-compliant fails",
      not grade(q["A2"], "No, that is not compliant: 80% exceeds the 85% limit.")["ok"])
    c("arithmetic: the full-and-open mixed order is exempt",
      grade(q["A3"], "No - when the order is competed among small and other-than-small businesses the limitations do not apply.")["ok"])
    c("prompt for E1 ships only the 2026 percentage chunk",
      "2023 annual edition" not in build_prompt(q["E1"]))
    a_full = common.read_text(common.fixture_path("real", "cfr125_6_2026_pastperf.txt"))
    b_full = common.read_text(common.fixture_path("real", "cfr125_6_2023_pastperf.txt"))
    c("the past-performance rule is in the 2026 chunk", all(rx.search(a_full) for _, rx in q["S3"]["need"]))
    c("the past-performance rule is not in the 2023 chunk",
      not all(rx.search(b_full) for _, rx in q["S3"]["need"]))
    c("the blocklist rule is in the NIST 2025 text", all(rx.search(c_nist) for _, rx in q["E3"]["need"]))
    c("blocklist answer passes",
      grade(q["E3"], "If it is on the blocklist the subscriber must choose a different secret and is told the reason.")["ok"])
    c("past-performance answer passes",
      grade(q["S3"], "The agency notifies the contractor and gives an opportunity to explain extenuating circumstances; "
                     "a satisfactory rating needs concurrence from one level above the contracting officer.")["ok"])
    return c.finish()


def regrade(repo):
    """Re-score every stored RAGQ-REAL record with the current keys. Answers are kept in the record, so this is
    a pure re-grade: no model call, and the file is rewritten in place (the published rows are built from it)."""
    import glob
    import json
    files = sorted(glob.glob(os.path.join(repo, "results", "raw", "RAGQ-REAL-*.json")))
    for f in files:
        rec = json.load(open(f))
        qs = {q["qid"]: q for q in QUESTIONS}
        changed = 0
        for row in rec.get("rows", []):
            q = qs.get(row.get("qid"))
            if not q or "answer" not in row:
                continue
            g = grade(q, row["answer"])
            if g["ok"] != row.get("correct"):
                changed += 1
            row["correct"], row["checks"] = g["ok"], g["checks"]
        scored = [r for r in rec.get("rows", []) if "error" not in r]
        by_type = {}
        for r in scored:
            t = by_type.setdefault(r["type"], [0, 0])
            t[0] += int(r["correct"])
            t[1] += 1
        rec["summary"].update({"correct": sum(1 for r in scored if r["correct"]), "scored": len(scored),
                               "by_type": {k: "%d/%d" % (v[0], v[1]) for k, v in by_type.items()}})
        json.dump(rec, open(f, "w"), indent=1)
        print("%-40s %2d/%d  changed %d" % (os.path.basename(f), rec["summary"]["correct"],
                                            rec["summary"]["scored"], changed))
    return 0 if files else 1


def main():
    ap = argparse.ArgumentParser(description="RAGQ-REAL: grounded answering over real public documents")
    ap.add_argument("--regrade", metavar="REPO", default="", help="re-score stored records in REPO/results/raw; no model")
    ap.add_argument("--selftest", action="store_true", help="grade the graders; no model")
    ap.add_argument("--limit", type=int, default=0, help="first N questions only")
    ap.add_argument("--max-tokens", type=int, default=1200, help="output cap")
    common.add_common_args(ap)
    args = ap.parse_args()
    if args.regrade:
        sys.exit(regrade(args.regrade))
    if args.selftest:
        sys.exit(selftest())
    sys.exit(run(args))


if __name__ == "__main__":
    main()
