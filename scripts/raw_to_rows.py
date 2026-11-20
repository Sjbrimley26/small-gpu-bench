#!/usr/bin/env python3
"""Turn results/raw/CMP-SYN-*.json and CAL-*.json records into results.csv rows (CMP-SYN and CAL tests).

Rebuilt for tests that post-date the lab's private export source: raw records are the primary evidence, this
script only summarises them. Prints rows (CSV) to stdout; with --append, adds them to results/results.csv
unless a row with the same model+test already exists.

  python3 scripts/raw_to_rows.py [--raw results/raw] [--append] [--section 125]

Model names are the published model names; MODELS maps the router's seat name to it. A record that has no
`chain` (load failure) is not turned into a row: the README/excerpt state load failures in prose.
"""
import argparse
import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = {
    "frog-agent": ("FrogNano-4B-2609-IQ4_XS", "IQ4_XS"),
    "granite4-7b-40k": ("granite4:7b-a1b-h (40k ctx)", ""),
    "lfm2-40k": ("lfm2-8b-a1b (40k ctx)", ""),
    "aux": ("ling3-tiny (64k ctx)", ""),
}
COLS = ["model", "test", "score", "passed", "total", "metric_value", "metric_unit", "date", "section", "notes",
        "build", "quant", "n_runs", "n_items"]


def row(rec, section):
    seat = rec["model"]
    name, quant = MODELS.get(seat, (seat, ""))
    chain = rec.get("chain") or []
    if not chain or chain[0]["status"] == "error":
        return None  # load failure (CUDA OOM): stated in prose, not a scored row
    per = []
    for c in chain:
        if c["status"] == "ok":
            per.append(f"{c['kept']}/{c['planted']}")
        else:
            per.append(c["status"])
    resume = rec.get("resume") or {}
    n_exact = sum(1 for v in resume.values() if v.get("exact"))
    invented = sum(c.get("invented", 0) for c in chain)
    final = rec.get("final_kept")
    if final is None:
        score = "rounds " + " / ".join(per) + (f"; resume {n_exact}/{len(resume)}" if resume else "; resume not run")
        passed = ""
    else:
        score = f"{final}/7 kept after round {len(chain)} ({' / '.join(per)}); resume {n_exact}/{len(resume)}"
        passed = str(final)
    secs = sum(c.get("s", 0) for c in chain)
    notes = (f"Synthetic agent-session transcript (small-gpu-bench tests/compress.py, seed {rec['seed']}, about "
             f"{rec['size_tok_est']} est. tokens, invented), {rec['rounds']} compaction rounds, 7 planted needles, "
             f"output ceiling {rec['ceiling']} tokens (`overflow` = ran past the ceiling, recorded apart from wrong), "
             f"one run (n=1). Per round kept/planted: {', '.join(per)}; {invented} invented; chain status "
             f"{rec.get('chain_status')}. Resume questions need an exact value: {n_exact}/{len(resume)}. "
             f"Seat context 40k-64k; larger contexts CUDA-OOM at load on this card. Section {section}.")
    d = rec["date"]
    date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return [name, "CMP-SYN", score, passed, "7" if final is not None else "", f"{secs:.0f}", "s wall (rounds)", date,
            section, notes, "", quant, "1", "7"]


CAL_MODELS = {"aux": "ling3-tiny"}

# VISION seats -> (published model, whole-card MiB after load, from nvidia-smi in the sweep log)
VIS_MODELS = {
    "vision": ("gemma4:e4b (vision)", 3155),
    "vision-text": ("gemma4:e4b (text-only control, no image)", 3965),
    "unc-vision": ("e4b-e2b-aggressive (vision)", 3977),
    "think-vision": ("qwen3.5-4b (vision)", 3931),
    "ocr-gemma3": ("gemma3-4b (ggml-org GGUF + mmproj)", 3517),
    "ocr-qwen25vl": ("qwen2.5vl-3b (ggml-org GGUF + mmproj)", 3765),
}


N_IMG_PER_SET = 4
N_Q_PER_SET = 8


def vision_row(recs, section):
    """One published row per seat from every VISION record of that seat.

    A record is one harness invocation and carries one run per image set (`--variants`); a seat read on several
    image sets is one row scored over all its sets, with the per-set breakdown in the notes. Records whose every
    run errored (the text-only control, HTTP 500 per image) produce no row, as before.
    """
    model = recs[0]["model"]
    base = [r for r in recs if (r["args"].get("max_tokens") or 200) == 200] or recs
    alt = [r for r in recs if r not in base]
    alt_scores = []
    for rec in alt:
        rs = [r for r in rec.get("runs") or [] if not any(q.get("error") for q in r["questions"])]
        if not rs:
            continue
        qs = [q for r in rs for q in r["questions"]]
        asked = len(qs)
        if asked < 5:
            continue  # a smoke probe, not an arm
        got_w = sum(q["weight"] for q in qs if q["ok"])
        tot_w = sum(q["weight"] for q in qs)
        empty = [q["q"] for q in qs if not q["ok"] and not (q.get("reply") or "").strip()]
        alt_scores.append("A separate arm re-asked %d of them with a %d-token budget and scored %d of %d points on "
                          "just those questions, still emitting nothing at all on %d of the %d"
                          % (asked, rec["args"]["max_tokens"], got_w, tot_w, len(empty), asked))
    scored, errored, per_set, sets = [], 0, [], []
    for rec in sorted(base, key=lambda r: r["date"]):
        for run in rec.get("runs") or []:
            qs = run["questions"]
            if any(q.get("error") for q in qs):
                errored += 1
                continue
            sets.append(run.get("variant", 0))
            scored.append(run)
    if not scored:
        return None
    got = sum(sum(q["weight"] for q in r["questions"] if q["ok"]) for r in scored)
    total = sum(r["total"] for r in scored)
    failed = ["v%d %s" % (r.get("variant", 0), q["q"]) for r in scored for q in r["questions"] if not q["ok"]]
    empty = sum(1 for r in scored for q in r["questions"]
                if not q["ok"] and not (q.get("reply") or "").strip())
    for k in sorted(set(sets)):
        rs = [r for r in scored if r.get("variant", 0) == k]
        per_set.append("set %d %d/%d" % (k, sum(sum(q["weight"] for q in r["questions"] if q["ok"]) for r in rs),
                                         sum(r["total"] for r in rs)))
    secs = sum(q["s"] for r in scored for q in r["questions"])
    nq = sum(len(r["questions"]) for r in scored)
    name, mib = VIS_MODELS.get(model, (model, None))
    notes = (f"Drawn-image vision test (small-gpu-bench tests/vision.py): "
             f"{len(set(sets))} image sets ({len(set(sets)) * N_IMG_PER_SET} images drawn in code: poster, logo, "
             f"countable-figure scene, arrow icon; each set has different titles, years, colours, wordmark, "
             f"figure count, arrow direction and digit), {nq} questions weighted 2/1, {total} points, code-side "
             f"grading, one run per image set (n=1). {', '.join(per_set)}. Missed: {', '.join(failed) or 'none'}"
             f"{'; %d of those were empty replies' % empty if empty else ''}. Wall {secs:.0f} s for the "
             f"{nq} questions. Whole-card VRAM after load {mib} MiB from nvidia-smi (includes the resting fim "
             f"server; /api/ps would under-report the projector)."
             f"{' ' + '; '.join(alt_scores) + ' (not part of this score).' if alt_scores else ''}"
             f" Section {section}.")
    d = base[0]["date"]
    date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return [name, "VISION", f"{got}/{total}", str(got), str(total), f"{secs:.0f}", f"s wall ({nq} questions)", date,
            section, notes, "", "", "1", str(nq)]


def cal_row(rec, section):
    if rec.get("request_errors") or not rec.get("results"):
        return None  # INVALID / endpoint trouble: not a scored row
    name = CAL_MODELS.get(rec["model"], rec["model"])
    n = rec["tasks"]
    fa, fin = rec["first_attempt_pass"], rec["final_pass"]
    a = rec["args"]
    passed = [r["id"] for r in rec["results"] if r["status"] == "passed"]
    tok = sum(r["coder_tokens"] for r in rec["results"])
    secs = sum(r["coder_seconds"] for r in rec["results"])
    score = f"{fin}/{n} final, {fa}/{n} on attempt 1"
    notes = (f"Code generation (small-gpu-bench tests/cal/run.py): {n} original calendar-helper tasks, up to "
             f"{rec['max_attempts']} attempts, critic = {rec['critic']}, borrowed (gold) dependencies for tasks "
             f"that import another module, thinking {a['think']}, {a['max_tokens']} output tokens, one run (n=1). "
             f"Passed: {', '.join(passed) or 'none'}. CAVEAT: n=1 variance is large: an earlier unrecorded "
             f"partial run of this same configuration passed rowsToEvents and eventsForDay, which this run "
             f"failed, and the lab's older ollama-era run of the same model scored 6/9 on these nine "
             f"(earlier harness revision, not comparable row for row). First-attempt is the clean number; "
             f"final depends on the critic. Section {section}.")
    d = rec["date"]
    date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return [name, "CAL", score, str(fin), str(n), f"{tok / secs:.1f}" if secs else "", "tok/s (coder)", date,
            section, notes, "", "", "1", str(n)]


def agent_row(recs, section):
    """One row per seat from its latest AGENT record that is not all-error (an all-error record is a seat that
    never answered, e.g. HTTP 500; it is not a score)."""
    good = [r for r in sorted(recs, key=lambda r: r["date"]) if r["total"]["error"] < r["total"]["n"]]
    if not good:
        return None
    rec = good[-1]
    t = rec["total"]
    failed = [k for k, v in rec["tasks"].items() if not v[0]["passed"]]
    fs = [k for k, v in rec["tasks"].items() if v[0]["false_success"]]
    dm = [k for k, v in rec["tasks"].items() if v[0]["damage"]]
    secs = ""
    notes = (f"Agent battery (small-gpu-bench tests/agent_tasks.py): own tool loop (read_file, write_file, list_dir, "
             f"run) over native tool calls, ten tasks each in a fresh sandbox, code-side grading, one run per task "
             f"(n=1), max {rec['args']['max_turns']} turns. Passed {t['passed']}/{t['n']}; false success (failed "
             f"but answer claims done) {t['false_success']}{' on ' + ', '.join(fs) if fs else ''}; damage (changed "
             f"a file outside the allowed outputs) {t['damage']}{' on ' + ', '.join(dm) if dm else ''}; runaway "
             f"{t['runaway']}; errors {t['error']}. Failed: {', '.join(failed) or 'none'}. Section {section}.")
    d = rec["date"]
    date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return [rec["model"] + " (agent loop)", "AGENT", f"{t['passed']}/{t['n']}", str(t["passed"]), str(t["n"]),
            str(t["false_success"]), "false successes", date, section, notes, "", "", "1", str(t["n"])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent-section", default="127")
    ap.add_argument("--raw", default=os.path.join(HERE, "..", "results", "raw"))
    ap.add_argument("--csv", default=os.path.join(HERE, "..", "results", "results.csv"))
    ap.add_argument("--section", default="125")
    ap.add_argument("--cal-section", default="124")
    ap.add_argument("--vision-section", default="126")
    ap.add_argument("--append", action="store_true")
    a = ap.parse_args()
    rows = []
    for p in sorted(glob.glob(os.path.join(a.raw, "CMP-SYN-*.json"))):
        with open(p) as f:
            r = row(json.load(f), a.section)
        if r:
            rows.append(r)
    for p in sorted(glob.glob(os.path.join(a.raw, "CAL-*.json"))):
        with open(p) as f:
            r = cal_row(json.load(f), a.cal_section)
        if r:
            rows.append(r)
    vis = {}
    for p in sorted(glob.glob(os.path.join(a.raw, "VISION-*.json"))):
        with open(p) as f:
            rec = json.load(f)
        vis.setdefault(rec["model"], []).append(rec)
    for key, recs in sorted(vis.items()):
        r = vision_row(recs, a.vision_section)
        if r:
            rows.append(r)
    ag = {}
    for p in sorted(glob.glob(os.path.join(a.raw, "AGENT-*.json"))):
        with open(p) as f:
            rec = json.load(f)
        ag.setdefault(rec["model"], []).append(rec)
    for key, recs in sorted(ag.items()):
        r = agent_row(recs, a.agent_section)
        if r:
            rows.append(r)
    w = csv.writer(sys.stdout, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    if a.append:
        with open(a.csv, newline="") as f:
            have = {(x[0], x[1]) for x in csv.reader(f) if len(x) > 1}
        new = [r for r in rows if (r[0], r[1]) not in have]
        with open(a.csv, "a", newline="") as f:
            cw = csv.writer(f, lineterminator="\n")
            for r in new:
                cw.writerow(r)
        print(f"appended {len(new)} of {len(rows)} rows", file=sys.stderr)


if __name__ == "__main__":
    main()
