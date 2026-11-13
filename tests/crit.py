#!/usr/bin/env python3
"""CRIT: cross-model critic. Judge real model-written summaries and a planted-defect twin of each.

Give it a directory of summaries written by one or more writers (`--summaries DIR`, files named
`<writer>-<n>.md`, e.g. `small-1.md`, `big-2.md`). Each summary is judged as-is (clean) and again after three
asserted edits (a changed number, a swapped company name, an inserted fabricated line), using the same prompt,
parser and grader as JUDGE. Because clean summaries are real model output and may carry real errors, the metric
is PAIRED: the twin scored below its own clean original by the same judge in the same run, plus how many of the
3 planted errors the problem list names.

Self vs cross: pass --judge-writer NAME to label summaries whose writer is the model under test as "self"; the
rest are "cross". The lab's finding (notebook 96.1) was that judges named planted errors in another model's
writing more often than in their own (gemma4 14/18 cross vs 7/18 self; FrogNano 17/18 vs 13/18; 6 summaries x
2 runs, so small samples) - whether that is model family or writing style was not separated.

The planted edits are described by tests/fixtures/demo/crit_plants.json; with your own transcript, point
--plants at a file whose `old` strings occur in your summaries (otherwise the edit falls back to inserting a
sentence, which is logged).

  python3 tests/crit.py --selftest
  BASE_URL=... MODEL=... python3 tests/crit.py --summaries tests/fixtures/demo/summaries [--runs 2]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import judge  # noqa: E402

DEMO = judge.DEMO


def plant(text, spec):
    t, log = text, []
    plants = {}
    for p in spec["plants"]:
        plants[p["id"]] = p["pattern"]
        if "old" in p:
            if p["old"] in t:
                t = t.replace(p["old"], p["new"])
                log.append(p["id"] + ":replace")
            else:
                t += "\n" + p["insert_if_absent"]
                log.append(p["id"] + ":insert")
        else:
            lines = t.split("\n")
            k = next((i for i, l in enumerate(lines) if l.strip().startswith(("*", "-"))), 1)
            lines.insert(k + 1, "- " + p["insert_after_first_bullet"])
            t = "\n".join(lines)
            log.append(p["id"] + ":insert")
    return t, log, plants


def items(summaries_dir, spec):
    out = []
    for f in sorted(os.listdir(summaries_dir)):
        if not f.endswith(".md"):
            continue
        stem = f[:-3]
        writer = re.sub(r"[-_]?\d+$", "", stem) or stem
        txt = common.read_text(os.path.join(summaries_dir, f)).strip()
        twin, log, plants = plant(txt, spec)
        out.append({"id": stem + "-clean", "writer": writer, "kind": "clean", "text": txt, "plants": {}})
        out.append({"id": stem + "-plant", "writer": writer, "kind": "plant", "text": twin, "plants": plants, "edits": log})
    return out


def selftest():
    c = common.Checks()
    spec = json.load(open(os.path.join(DEMO, "crit_plants.json")))
    its = items(os.path.join(DEMO, "summaries"), spec)
    transcript = common.read_text(os.path.join(DEMO, "transcript.txt"))
    c("demo summaries found (>= 4 items)", len(its) >= 4)
    for it in its:
        if it["kind"] == "plant":
            clean = next(x for x in its if x["id"] == it["id"].replace("plant", "clean"))
            c("%s differs from its clean original %s" % (it["id"], it["edits"]),
              it["text"] != clean["text"] and "Northfield" in it["text"] and "5090" in it["text"]
              and not re.search(r"5090|northfield", clean["text"], re.I))
    c("planted fabrications absent from the transcript", not re.search(r"5090|northfield", transcript, re.I))
    g = judge.grade_judge(next(x for x in its if x["kind"] == "plant"), {"score": 5, "problems": []})
    c("rubber-stamp names 0 planted errors", sum(g["found"].values()) == 0)
    return c.finish()


def main():
    ap = judge.add_input_args(common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])))
    ap.add_argument("--summaries", default=os.path.join(DEMO, "summaries"), help="directory of <writer>-<n>.md files")
    ap.add_argument("--crit-plants", default=os.path.join(DEMO, "crit_plants.json"), help="planted-edit spec")
    ap.add_argument("--judge-writer", default="", help="writer name that counts as 'self' (the judge's own writing)")
    ap.add_argument("--runs", type=int, default=2, help="run 0 greedy, later runs sampled")
    ap.add_argument("--sample-temp", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    transcript = common.read_text(a.transcript)
    its = items(a.summaries, json.load(open(a.crit_plants)))
    if not its:
        common.die("no .md summaries in %s" % a.summaries)
    rows = []
    for run in range(a.runs):
        for it in its:
            r = judge.judge_once(a, transcript, it["text"], a.max_tokens, 0.0 if run == 0 else a.sample_temp, 100 + run)
            g = judge.grade_judge(it, judge.parse_judge(r["content"])) if not r.get("error") else {"parsed": False}
            rows.append({"run": run, "item": it["id"], "writer": it["writer"], "kind": it["kind"], "s": r["s"],
                         "error": r.get("error"), "finish": r.get("finish"), "reply": r["content"], **g})
            common.say("r%d %-18s score=%s found=%s %.0fs %s" % (run, it["id"], g.get("score"), g.get("found"), r["s"],
                                                                   r.get("error") or ""))
    summary = {}
    for w in sorted({x["writer"] for x in rows}):
        R = [x for x in rows if x["writer"] == w]
        pair = tot = named = nplant = 0
        for x in R:
            if x["kind"] != "plant":
                continue
            cl = next(y for y in R if y["run"] == x["run"] and y["item"] == x["item"].replace("plant", "clean"))
            if cl.get("score") is not None and x.get("score") is not None:
                tot += 1
                pair += x["score"] < cl["score"]
            nplant += len(next(i for i in its if i["id"] == x["item"])["plants"])
            named += sum(x.get("found", {}).values()) if x.get("parsed") else 0
        summary[w] = {"role": "self" if w == a.judge_writer else "cross", "paired": "%d/%d" % (pair, tot),
                      "planted_named": "%d/%d" % (named, nplant),
                      "unparsed": "%d/%d" % (sum(1 for x in R if not x.get("parsed")), len(R))}
        common.say("CRIT writer=%s %s" % (w, summary[w]))
    common.save_results(a, "CRIT", {"summary": summary, "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
