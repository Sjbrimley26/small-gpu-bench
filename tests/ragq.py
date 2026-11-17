#!/usr/bin/env python3
"""RAGQ-SYN: grounded answering from a fixed, supplied context — the writer half of RAG, on invented documents.

The lab measures a RAG writer by holding retrieval still and varying the writer (one saved hit set per
question, every model answered from it). That cannot be published: the hit sets are the lab's own notes, full
of host paths and personal infrastructure. This is the portable rebuild — a small invented corpus for a
fictional makerspace, with the same five question shapes the lab set uses, and the same four failure modes
planted deliberately:

  exact       one document holds the fact
  freshness   a stale document and a live one disagree; repeating the stale value is wrong
  negative    the fact is NOT in the corpus; the only correct answer is "not recorded"
  synthesis   two documents must be combined, one question needs arithmetic
  vague       colloquial phrasing that points at a document without naming it

**Read this as the clean baseline.** Ten seats scored 6-9/10 on it, so the ceiling is high; the hard version
is `tests/ragq_real.py`, which uses real public documents arranged as two revision pairs (a superseded source
that disagrees with the current one).

Every question ships the documents it "retrieved" (a fixed list of ids), so this test measures the writer and
the instruction only — never a retrieval system. Nothing is judged by a model: each question carries a key of
regexes that must match (`need`) and must not match (`must_not`), and a freshness question accepts the stale
number only when the answer marks it as old. That keeps the score countable and the failure explainable.

What is reported per question: correct/wrong, which key checks failed, whether any document id was cited
(counted, not scored — a citation format is a contract decision, not a capability), chars, completion tokens,
finish reason and seconds. A transport error is its own bucket; a run with more than a quarter of them is
`invalid` and exits 1.

  python3 tests/ragq.py --selftest
  sed -i 's/x/x/' /dev/null  # (no fixture to generate: corpus and key live in this file, on purpose)
  BASE_URL=... MODEL=... python3 tests/ragq.py [--limit N] [--max-tokens 900]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

DOCS = {
    "D1": """[D1] FIRING SCHEDULE - KILN 1 (revised 2026-09)
Cone 04 bisque: ramp 150 C per hour to 1000 C, hold 20 minutes, cool naturally.
Cone 6 glaze: ramp 120 C per hour to 1200 C, hold 12 minutes, cool 80 C per hour to 800 C,
lids shut after 600 C. Do not open the lid above 300 C.""",
    "D2": """[D2] FIRING SCHEDULE - KILN 1 (printed 2019, kept in the glaze room binder)
Cone 6 glaze: ramp 120 C per hour to 1200 C, hold 25 minutes, lids shut at 900 C.
Cone 04 bisque: hold 20 minutes.""",
    "D3": """[D3] GLAZE BATCH LOG
Batch 214 stoneware white: 40 kg dry, 5 kg frit, ball-milled 3 hours.
Batch 215 celadon: 40 kg dry, ball-milled 4 hours.
Batch 216 tenmoku: under test, do not use on food surfaces yet.""",
    "D4": """[D4] SUPPLIER TERMS
Ridgeway Clay: net 30. 2% discount if the invoice is paid within 10 days.
Freight prepaid on orders over 400, otherwise 35 flat. Backorders held 14 days.""",
    "D5": """[D5] MEMBERSHIP AND PASSES
Studio dues 45 per month, including two shelf-feet and firing up to 4 cu ft.
Day pass 12 per visit; firing charged by the cubic foot on top.
Student rate 30 per month with a current student id.""",
    "D6": """[D6] SAFETY
Respirator P100 required for dry glaze mixing. Eyewash station at the sink.
No spraying outside the loading dock.""",
    "D7": """[D7] 3D PRINTER (card updated 2026-06)
Nozzle 0.4 mm hardened steel. Max hotend 300 C, bed 110 C.
Filament kept in the dry box below 20% relative humidity. Firmware 2.7.""",
    "D8": """[D8] 3D PRINTER (card taped to the wall in 2021)
Nozzle 0.6 mm brass. Max hotend 260 C. Firmware 1.9.""",
    "D9": """[D9] SHIPPING
Flat 18 per box, free over 150. Local pickup Tuesday to Saturday.""",
    "D10": """[D10] OPEN HOURS
Tuesday to Saturday 10 to 6. Closed Sunday and Monday. The kiln room closes at 5.""",
    "D11": """[D11] KILN 1 SERVICE NOTE (2026-03)
Element 3 replaced. Resistance measured 9.8 ohms per element pair. Thermocouple recalibrated.
Next service due 2027-03.""",
}

REFUSAL = re.compile(
    r"not\s+(recorded|mentioned|stated|listed|given|specified|provided|available|covered)|"
    r"(does\s+not|doesn't|do\s+not|don't)\s+(mention|contain|include|record|say|state|provide|give|"
    r"specify|cover|address)|"
    r"no\s+(information|mention|record|policy|indication)|"
    r"isn't\s+(recorded|mentioned|stated|in\s+the)|"
    r"(context|documents?|notes?)[^.\n]{0,40}(do(es)?\s+not|don't|doesn't)\s+(mention|contain|include|record|provide|say|state)",
    re.I)
ATTRIBUTED = re.compile(r"\b(old|older|previous|previously|prior|stale|superseded|no\s+longer|used\s+to|2019|outdated)\b", re.I)

# qid -> dict(question, docs, type, need=[(label, regex)], must_not=[(label, regex)],
#             stale=[(label, regex)] for freshness: matched only if ATTRIBUTED is near it)
QUESTIONS = [
    dict(qid="E1", type="exact", docs=["D4"],
         question="what are the terms from Ridgeway Clay for paying an invoice early?",
         need=[("2% discount", re.compile(r"\b2\s?%|\b2\s*percent|two\s*percent", re.I)),
               ("10 days", re.compile(r"\b10\s*days?\b|\bten\s*days?\b", re.I))],
         must_not=[]),
    dict(qid="E2", type="exact", docs=["D5"],
         question="what does a student pay per month for studio access?",
         need=[("30", re.compile(r"\b30\b"))],
         must_not=[("45 quoted as the student rate", re.compile(r"student[^.\n]{0,40}\b45\b|\b45\b[^.\n]{0,30}student", re.I))]),
    dict(qid="F1", type="freshness", docs=["D1", "D2"],
         question="how long does the kiln hold at cone 6 these days?",
         need=[("12 minutes", re.compile(r"\b12\s*(min|minutes?)\b|\btwelve\s*minutes?\b", re.I))],
         must_not=[],
         stale=[("25 minutes", re.compile(r"\b25\s*(min|minutes?)\b|\btwenty[- ]five\s*minutes?\b", re.I))]),
    dict(qid="F2", type="freshness", docs=["D7", "D8"],
         question="what nozzle size is the 3D printer running?",
         need=[("0.4 mm", re.compile(r"\b0\.4\s*mm|\b0\.4\b"))],
         must_not=[],
         stale=[("0.6 mm", re.compile(r"\b0\.6\s*mm|\b0\.6\b"))]),
    dict(qid="N1", type="negative", docs=["D1", "D5", "D9"],
         question="what does it cost to rent the kiln out to an outside group, and what deposit is required?",
         need=[("states it is not recorded", REFUSAL)],
         must_not=[("invents a rental price",
                    re.compile(r"\b(rental|rent|hire)[^.\n]{0,40}?\$\s?\d|\$\s?\d+[^.\n]{0,20}?(day|hour|session)", re.I)),
                   ("invents a deposit", re.compile(r"deposit[^.\n]{0,30}?\d", re.I))]),
    dict(qid="N2", type="negative", docs=["D1", "D11"],
         question="when did the shop buy its second kiln?",
         need=[("states it is not recorded", REFUSAL)],
         must_not=[("asserts a purchase", re.compile(r"(second|2nd|new)\s+kiln[^.\n]{0,60}(bought|purchased|installed|arrived|20\d\d)", re.I))]),
    dict(qid="S1", type="synthesis", docs=["D1", "D11"],
         question="what was measured when the kiln element was replaced, and what does the firing schedule ask for at cone 6 now?",
         need=[("9.8 ohms", re.compile(r"\b9\.8\b")),
               ("12 minutes hold", re.compile(r"\b12\s*(min|minutes?)\b|\btwelve\s*minutes?\b", re.I))],
         must_not=[],
         stale=[("25 minutes", re.compile(r"\b25\s*(min|minutes?)\b", re.I))]),
    dict(qid="S2", type="synthesis", docs=["D5"],
         question="someone visits once a week - is a membership or day passes cheaper for them in a month, and by how much?",
         need=[("45 dues", re.compile(r"\b45\b")),
               ("12 per pass", re.compile(r"\b12\b")),
               ("the comparison (48 / 3.75 visits / four visits)", re.compile(r"\b48\b|\b3\.7\d?\b|\bfour\s+visits?\b|\b4\s+visits?\b", re.I))],
         must_not=[]),
    dict(qid="V1", type="vague", docs=["D5", "D9"],
         question="remind me what we charge the drop-in crowd",
         need=[("12 per day pass", re.compile(r"\b12\b"))],
         must_not=[]),
    dict(qid="V2", type="vague", docs=["D1", "D2"],
         question="how long do we hold the top temperature?",
         need=[("12 minutes", re.compile(r"\b12\s*(min|minutes?)\b|\btwelve\s*minutes?\b", re.I))],
         must_not=[],
         stale=[("25 minutes", re.compile(r"\b25\s*(min|minutes?)\b|\btwenty[- ]five\s*minutes?\b", re.I))]),
]

SYSTEM = ("You answer questions using only the NOTE CONTEXT below. If the context does not answer the "
          "question, say plainly that it is not recorded in the documents. Never invent values.")
PROMPT = """{system}

QUESTION: {q}

NOTE CONTEXT:
{ctx}
"""


def context_for(q):
    return "\n\n".join(DOCS[d] for d in q["docs"])


def build_prompt(q):
    return PROMPT.format(system=SYSTEM, q=q["question"], ctx=context_for(q))


def stale_attributed(text, stale):
    """A stale value is acceptable only when the answer marks it as old, within a short window."""
    for m in stale.finditer(text):
        window = text[max(0, m.start() - 80):m.end() + 80]
        if not ATTRIBUTED.search(window):
            return False
    return True


def grade(q, text):
    """-> dict(checks, ok). Code-side only: every check is a regex decision, not an opinion."""
    text = text or ""
    checks, ok = {}, True
    for label, rx in q.get("need", []):
        checks[label] = bool(rx.search(text))
        ok &= checks[label]
    for label, rx in q.get("must_not", []):
        checks["not " + label] = not rx.search(text)
        ok &= checks["not " + label]
    for label, rx in q.get("stale", []):
        allowed = stale_attributed(text, rx)
        checks["stale value only if labelled old (%s)" % label] = allowed
        ok &= allowed
    return {"checks": checks, "ok": ok}


def cite_ids(text):
    """Document ids the answer names. Counted, never scored: a citation contract is not a capability."""
    return sorted({"D" + n for n in set(re.findall(r"\bD(\d{1,2})\b", text or ""))})


def run(args):
    qs = QUESTIONS[: args.limit] if args.limit else QUESTIONS
    rows, transport = [], 0
    for q in qs:
        r = common.chat(args, [{"role": "user", "content": build_prompt(q)}],
                        max_tokens=args.max_tokens, temperature=0.0)
        if r.get("error"):
            transport += 1
            rows.append({"qid": q["qid"], "type": q["type"], "error": r["error"], "ok": False})
            common.say("%-3s %-9s ERROR %s" % (q["qid"], q["type"], r["error"]))
            continue
        g = grade(q, r["content"])
        row = {"qid": q["qid"], "type": q["type"], "question": q["question"], "docs": q["docs"],
               "answer": r["content"], "correct": g["ok"], "checks": g["checks"],
               "cited": cite_ids(r["content"]), "chars": len(r["content"]),
               "finish": r.get("finish"), "s": r.get("s"),
               "completion_tokens": r.get("completion_tokens"),
               "reasoning_chars": r.get("reasoning_chars")}
        rows.append(row)
        common.say("%-3s %-9s %-5s %5ss %4s tok  %s" % (
            q["qid"], q["type"], "ok" if g["ok"] else "FAIL", r.get("s"),
            r.get("completion_tokens"), r["content"][:150].replace("\n", " ")))
    scored = [r for r in rows if "error" not in r]
    right = sum(1 for r in scored if r["correct"])
    by_type = {}
    for r in scored:
        t = by_type.setdefault(r["type"], [0, 0])
        t[0] += int(r["correct"]); t[1] += 1
    summary = {"correct": right, "scored": len(scored), "transport_errors": transport,
               "by_type": {k: "%d/%d" % (v[0], v[1]) for k, v in by_type.items()},
               "cited_something": sum(1 for r in scored if r["cited"]),
               "mean_s": round(sum(r["s"] or 0 for r in scored) / len(scored), 1) if scored else None,
               "mean_completion_tokens": round(sum(r["completion_tokens"] or 0 for r in scored) / len(scored)) if scored else None}
    invalid = bool(scored) and transport > 0.25 * len(rows)
    common.say("\nRAGQ-SYN: %d/%d correct  %s  cited %d/%d  mean %ss  %s tok/answer%s" % (
        right, len(scored), summary["by_type"], summary["cited_something"], len(scored),
        summary["mean_s"], summary["mean_completion_tokens"],
        "  INVALID (>25% transport errors)" if invalid else ""))
    common.save_results(args, "RAGQ", {"summary": summary, "rows": rows, "invalid": invalid,
                                       "arm": "ragq-syn", "thinking": args.think})
    return 1 if invalid else 0


def selftest():
    c = common.Checks()
    c("11 invented documents", len(DOCS) == 11)
    c("10 questions, five shapes", len(QUESTIONS) == 10
      and {q["type"] for q in QUESTIONS} == {"exact", "freshness", "negative", "synthesis", "vague"})
    c("every question's docs exist", all(d in DOCS for q in QUESTIONS for d in q["docs"]))
    c("no question ships the whole corpus", all(len(q["docs"]) <= 3 for q in QUESTIONS))
    q = {x["qid"]: x for x in QUESTIONS}
    c("freshness answers the live value", grade(q["F1"], "It holds 12 minutes at cone 6.")["ok"])
    c("freshness rejects the stale value", not grade(q["F1"], "It holds 25 minutes at cone 6.")["ok"])
    c("freshness accepts the stale value when labelled old",
      grade(q["F1"], "The 2019 sheet said 25 minutes, but the current schedule holds 12 minutes.")["ok"])
    c("negative needs a refusal", grade(q["N1"], "The documents do not mention a rental or deposit.")["ok"])
    c("negative fails on an invented fee", not grade(q["N1"], "Kiln rental is $80 a day with a $200 deposit.")["ok"])
    c("negative fails on an invented purchase", not grade(q["N2"], "The second kiln was bought in 2021.")["ok"])
    c("exact needs the number", grade(q["E2"], "Students pay 30 per month.")["ok"])
    c("exact fails when the wrong rate is attached to students",
      not grade(q["E2"], "Students pay the standard 45 per month.")["ok"])
    c("synthesis needs both facts", grade(q["S1"], "Element 3 measured 9.8 ohms; cone 6 holds 12 minutes.")["ok"])
    c("synthesis fails on one fact", not grade(q["S1"], "Element 3 was replaced and cone 6 holds 12 minutes.")["ok"])
    c("arithmetic synthesis accepts 48",
      grade(q["S2"], "A day pass is 12, so four visits cost 48, which is more than the 45 membership.")["ok"])
    c("citations are extracted", cite_ids("see D4 and D11") == ["D11", "D4"])
    c("prompt carries only the question's documents", "D3" not in build_prompt(q["E1"]))
    return c.finish()


def main():
    ap = argparse.ArgumentParser(description="RAGQ-SYN: grounded answering from a supplied context (invented corpus)")
    ap.add_argument("--selftest", action="store_true", help="grade the graders; no model")
    ap.add_argument("--limit", type=int, default=0, help="first N questions only")
    ap.add_argument("--max-tokens", type=int, default=900, help="output cap (a reasoning trace needs more)")
    common.add_common_args(ap)
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    sys.exit(run(args))


if __name__ == "__main__":
    main()
