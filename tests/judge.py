#!/usr/bin/env python3
"""JUDGE: is a local model's 1-5 rating of a summary lower when the summary is wrong?

The model is shown a transcript and a summary and returns {"problems": [...], "score": 1-5}. The gold summary
and five deliberately broken copies (built from the gold by asserted, deterministic edits) are judged; the
metrics are (a) how many defective copies score strictly BELOW the gold in the same run, and (b) how many of
the planted errors the problem list names (a keyword pattern per plant). Unparseable replies are counted:
small models loop or break the JSON, and a harness needs a parse-or-retry.

THIS REPO SHIPS NO REAL TRANSCRIPT. The default inputs under tests/fixtures/demo/ are a tiny invented
stand-in so the script runs end to end. To reproduce the lab's kind of measurement, bring your own:

  --transcript FILE    a transcript or article you may use (e.g. a CC-licensed talk), plain text
  --gold FILE          a good summary of it, written or checked by you
  --plants FILE        JSON describing the broken copies (see tests/fixtures/demo/judge_plants.json): each
                       variant is a list of edits (replace / insert_after / truncate_after / replace_all) on
                       the gold text and a keyword pattern per planted error. Every edit's `old`/`anchor`
                       text must occur in your gold, or the script stops (edits are asserted to apply).

The 'what the published numbers mean' caveats: the lab's run used one transcript, five hand-built defects,
3 runs per variant (run 0 greedy, runs 1-2 sampled), so 15 defect/gold pairs per model.

  python3 tests/judge.py --selftest
  BASE_URL=... MODEL=... python3 tests/judge.py [--runs 3] [--transcript T --gold G --plants P]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

DEMO = common.fixture_path("demo")
JUDGE_SYS = "You are an AI assistant that validates summaries of video transcripts."
JUDGE_USER = ("Given the original transcript and its summary, assess whether the summary accurately and completely "
              "captures the key points of the transcript. Check every claim in the summary against the transcript.\n"
              "Return ONLY a JSON object: {\"problems\": [\"...\"], \"score\": N} where problems lists each statement "
              "in the summary that is wrong or unsupported by the transcript, and each important point it leaves out "
              "(empty list if none), and score is 1-5 (5 = accurate and complete, 1 = wrong or unrelated).\n\n"
              "TRANSCRIPT:\n{t}\n\nSUMMARY:\n{s}\n")


def add_input_args(ap):
    ap.add_argument("--transcript", default=os.path.join(DEMO, "transcript.txt"),
                    help="source text the summaries are about (bring your own; default is a tiny invented demo)")
    ap.add_argument("--gold", default=os.path.join(DEMO, "reference_summary.md"), help="a good summary of it")
    ap.add_argument("--plants", default=os.path.join(DEMO, "judge_plants.json"),
                    help="JSON spec of the broken copies and their keyword patterns")
    return ap


def apply_edits(text, edits):
    for e in edits:
        op = e["op"]
        if op == "replace":
            assert e["old"] in text, "edit did not apply (text not in gold): %r" % e["old"][:60]
            text = text.replace(e["old"], e["new"])
        elif op == "insert_after":
            assert e["anchor"] in text, "anchor not in gold: %r" % e["anchor"][:60]
            text = text.replace(e["anchor"], e["anchor"] + "\n" + e["text"], 1)
        elif op == "truncate_after":
            lines = text.split("\n")
            assert e["line"] in lines, "line not in gold: %r" % e["line"]
            i = lines.index(e["line"])
            text = "\n".join(lines[:i + 1 + e["keep_lines"]]) + e.get("append", "")
        elif op == "replace_all":
            text = e["text"]
        else:
            raise ValueError("unknown edit op %r" % op)
    return text


def build_variants(gold, spec):
    out = [{"id": "G", "text": gold, "plants": {}}]
    by_id = {}
    for v in spec["variants"]:
        base = by_id[v["from"]]["text"] if v.get("from") else gold
        text = apply_edits(base, v["edits"])
        rec = {"id": v["id"], "text": text, "plants": dict(v["plants"])}
        by_id[v["id"]] = rec
        out.append(rec)
    return out


def parse_judge(txt):
    t = re.sub(r"^```(?:json)?|```$", "", (txt or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0), strict=False)
    except Exception:  # noqa: BLE001
        sm = re.search(r'"score"\s*:\s*([1-5])', t)
        return {"score": int(sm.group(1)), "problems": [t], "lenient": True} if sm else None
    try:
        sc = int(d.get("score"))
    except Exception:  # noqa: BLE001
        return None
    pr = d.get("problems") or []
    if isinstance(pr, str):
        pr = [pr]
    return {"score": sc, "problems": [str(p) for p in pr]}


def grade_judge(variant, parsed):
    if not parsed:
        return {"parsed": False}
    blob = " ".join(parsed["problems"]).lower()
    found = {k: bool(re.search(p, blob)) for k, p in variant["plants"].items()}
    return {"parsed": True, "score": parsed["score"], "found": found, "n_problems": len(parsed["problems"])}


def judge_once(args, transcript, summary, max_tokens, temperature, seed):
    user = JUDGE_USER.replace("{t}", transcript).replace("{s}", summary)
    return common.chat(args, [{"role": "system", "content": JUDGE_SYS}, {"role": "user", "content": user}],
                       max_tokens=max_tokens, temperature=temperature, seed=seed)


def selftest():
    c = common.Checks()
    transcript = common.read_text(os.path.join(DEMO, "transcript.txt"))
    gold = common.read_text(os.path.join(DEMO, "reference_summary.md")).strip()
    spec = json.load(open(os.path.join(DEMO, "judge_plants.json")))
    vs = build_variants(gold, spec)
    c("6 variants, all distinct", len(vs) == 6 and len({v["id"] for v in vs}) == 6 and len({v["text"] for v in vs}) == 6)
    c("planted fabrications are absent from the transcript",
      not re.search(r"5090|northfield|19b total|tokens per second at about 300", transcript, re.I))
    c("no plant pattern matches an empty problem list", all(not re.search(p, "") for v in vs for p in v["plants"].values()))
    c("parse fenced json", (parse_judge('```json\n{"problems": ["says 19B"], "score": 2}\n```') or {}).get("score") == 2)
    c("parse junk -> None", parse_judge("great summary!") is None)
    f3 = next(v for v in vs if v["id"] == "F3")
    g = grade_judge(f3, {"score": 2, "problems": ["It says Northfield; the transcript says Lumen Labs", "19B is wrong"]})
    c("F3 grading finds 2 of 3", sum(g["found"].values()) == 2)
    g0 = grade_judge(f3, {"score": 5, "problems": []})
    c("rubber-stamp finds 0 of 3", sum(g0["found"].values()) == 0)
    try:
        apply_edits("abc", [{"op": "replace", "old": "zzz", "new": "y"}])
        c("an edit that does not apply stops the run", False)
    except AssertionError:
        c("an edit that does not apply stops the run", True)
    return c.finish()


def main():
    ap = add_input_args(common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])))
    ap.add_argument("--runs", type=int, default=3, help="runs per variant; run 0 greedy, later runs sampled")
    ap.add_argument("--sample-temp", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=1200, help="raise to ~8000 for thinking-on models")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    transcript = common.read_text(a.transcript)
    gold = common.read_text(a.gold).strip()
    variants = build_variants(gold, json.load(open(a.plants)))
    rows = []
    for run in range(a.runs):
        for v in variants:
            r = judge_once(a, transcript, v["text"], a.max_tokens, 0.0 if run == 0 else a.sample_temp, 100 + run)
            g = grade_judge(v, parse_judge(r["content"])) if not r.get("error") else {"parsed": False}
            if not g.get("parsed"):
                raw = (r["content"] or "").lower()
                g["found_raw"] = {k: bool(re.search(p, raw)) for k, p in v["plants"].items()}
            rows.append({"run": run, "variant": v["id"], "s": r["s"], "finish": r.get("finish"),
                         "error": r.get("error"), "reply": r["content"], **g})
            common.say("judge r%d %-6s score=%s found=%s %.0fs %s" % (run, v["id"], g.get("score"), g.get("found"),
                                                                       r["s"], r.get("error") or ""))
    below = pairs = unparsed = named = planted = 0
    for run in range(a.runs):
        gs = [x.get("score") for x in rows if x["run"] == run and x["variant"] == "G"]
        for x in rows:
            if x["run"] != run:
                continue
            unparsed += not x.get("parsed")
            if x["variant"] == "G":
                continue
            planted += len(variants[[v["id"] for v in variants].index(x["variant"])]["plants"])
            named += sum(x["found"].values()) if x.get("parsed") else 0
            if gs and gs[0] is not None and x.get("score") is not None:
                pairs += 1
                below += x["score"] < gs[0]
    summary = {"defects_below_gold": "%d/%d" % (below, pairs), "planted_named": "%d/%d" % (named, planted),
               "unparsed": "%d/%d" % (unparsed, len(rows))}
    common.say("JUDGE %s" % summary)
    common.save_results(a, "JUDGE", {"summary": summary, "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
