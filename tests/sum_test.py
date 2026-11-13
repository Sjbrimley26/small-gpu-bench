#!/usr/bin/env python3
"""SUM: summarize one document in a single pass and score it in code against a fact list.

No judge model. The score is (a) how many facts from your fact list appear in the summary (each fact is a
regex; some are marked `must`), (b) numbers in the summary that do not occur in the source (a code-side check
for invented figures; any thousands separator is normalised), (c) word count and seconds. Optionally the same
source goes through the summarize-verify pipeline (patterns/summarize_verify.py) with --pipeline, so you can
repeat the single-pass vs pipeline comparison of notebook 97 / 104 on your own document.

THIS REPO SHIPS NO REAL TRANSCRIPT. The defaults are a tiny invented demo. Bring your own:

  --transcript FILE    the source text (a CC-licensed talk, an article you may use)
  --facts FILE         JSON {"facts": [{"id": ..., "must": true|false, "pattern": "<regex>"}, ...]} written by
                       hand from the source (tests/fixtures/demo/facts.json is the format)

  python3 tests/sum_test.py --selftest
  BASE_URL=... MODEL=... python3 tests/sum_test.py [--runs 3] [--pipeline]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = common.fixture_path("demo")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "patterns"))

PROMPT = ("Below is a document. Write a faithful summary of the whole thing for someone who has not read it: "
          "what it is, each main point or event and how it turned out (say plainly when something failed), "
          "and any conclusion or verdict. Use short sections or bullets. Keep every name, number and date exactly "
          "as written. Use only what the document says; do not add facts or numbers that are not in it, and do "
          "not repeat yourself.\n\nDOCUMENT:\n")


def _nums(s):
    return {x for x in re.findall(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3})", "", s))}


def score(summary, source, facts):
    low = summary.lower()
    hit = [f["id"] for f in facts if re.search(f["pattern"], low)]
    must = [f["id"] for f in facts if f.get("must")]
    return {"facts_hit": len(hit), "facts_total": len(facts), "must_hit": len([m for m in must if m in hit]),
            "must_total": len(must), "missed": [f["id"] for f in facts if f["id"] not in hit],
            "numbers_not_in_source": sorted(_nums(summary) - _nums(source)), "words": len(summary.split())}


def selftest():
    c = common.Checks()
    src = common.read_text(os.path.join(DEMO, "transcript.txt"))
    facts = json.load(open(os.path.join(DEMO, "facts.json")))["facts"]
    ref = common.read_text(os.path.join(DEMO, "reference_summary.md"))
    g = score(ref, src, facts)
    c("the demo reference summary covers all demo facts (%d/%d)" % (g["facts_hit"], g["facts_total"]),
      g["facts_hit"] == g["facts_total"])
    c("reference has no numbers absent from the source (%s)" % g["numbers_not_in_source"], not g["numbers_not_in_source"])
    g0 = score("Nothing relevant here.", src, facts)
    c("an empty summary scores 0 facts", g0["facts_hit"] == 0)
    g1 = score("It ran at 999 tokens per second.", src, facts)
    c("an invented number is caught", g1["numbers_not_in_source"] == ["999"])
    c("1,420 and 1420 are the same figure", _nums("1,420") == _nums("1420"))
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--transcript", default=os.path.join(DEMO, "transcript.txt"))
    ap.add_argument("--facts", default=os.path.join(DEMO, "facts.json"))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--pipeline", action="store_true", help="also run patterns/summarize_verify.py's Pipeline")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    src = common.read_text(a.transcript)
    facts = json.load(open(a.facts))["facts"]
    rows = []
    for run in range(a.runs):
        r = common.chat(a, [{"role": "user", "content": PROMPT + src}], max_tokens=a.max_tokens,
                        temperature=a.temperature, seed=100 + run)
        g = score(r["content"], src, facts)
        rows.append({"arm": "single", "run": run, "s": r["s"], "finish": r.get("finish"), "error": r.get("error"),
                     "reply": r["content"], **g})
        common.say("single r%d facts %d/%d must %d/%d words %d numbers-not-in-source %s %.0fs %s" % (
            run, g["facts_hit"], g["facts_total"], g["must_hit"], g["must_total"], g["words"],
            g["numbers_not_in_source"], r["s"], r.get("error") or ""))
    if a.pipeline:
        import time
        import summarize_verify as sv
        chat = sv.make_chat(a.base_url, a.model, a.model, a.api_key, timeout=a.timeout)
        t0 = time.time()
        res = sv.Pipeline(chat, verbose=False).run(src)
        if res["ok"]:
            g = score(res["body"], src, facts)
            rows.append({"arm": "pipeline", "run": 0, "s": round(time.time() - t0, 1), "reply": res["markdown"],
                         "claims": res["claims"], "flagged": res["flagged"], **g})
            common.say("pipeline facts %d/%d must %d/%d words %d flagged %d/%d %.0fs" % (
                g["facts_hit"], g["facts_total"], g["must_hit"], g["must_total"], g["words"], res["flagged"],
                res["claims"], time.time() - t0))
        else:
            rows.append({"arm": "pipeline", "run": 0, "error": res.get("why")})
            common.say("pipeline FAILED:", res.get("why"))
    common.save_results(a, "SUM", {"rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
