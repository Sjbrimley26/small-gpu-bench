#!/usr/bin/env python3
"""Turn results/raw/CMP-SYN-*.json records into results.csv rows (CMP-SYN test).

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=os.path.join(HERE, "..", "results", "raw"))
    ap.add_argument("--csv", default=os.path.join(HERE, "..", "results", "results.csv"))
    ap.add_argument("--section", default="125")
    ap.add_argument("--append", action="store_true")
    a = ap.parse_args()
    rows = []
    for p in sorted(glob.glob(os.path.join(a.raw, "CMP-SYN-*.json"))):
        with open(p) as f:
            r = row(json.load(f), a.section)
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
