#!/usr/bin/env python3
"""Regenerate results/results.csv from the lab's source results CSV.

Keeps ONLY the rows for tests that are ported to this repo, drops machine-specific text from the notes, and
adds provenance columns. A column is filled only where the source states it; otherwise it is blank.
Rows in the output file whose test is not ported (today: the hand-added PTUNE rows) are preserved as-is.

  python3 scripts/export_results.py --source PATH/TO/results.csv [--out results/results.csv]
  (or SOURCE_CSV=... in the environment)

The source CSV lives in the author's private lab repo, so this script is for the maintainer; the exported
file is what is published. Run `scripts/sanitize_results.py` after exporting: the source's `notes` carry the
lab's seat names, and that script rewrites them to the published role words.

Source columns : model,test,score,passed,total,metric_value,metric_unit,status,date,section,notes
Dropped        : status (all `installed`); the machine's gpu/vram_gb/cpu and kv_type are constants of the
                 hardware block in the README and are not repeated per row.
Added columns  : build, quant, n_runs, n_items
  build              only when the notes give a build number (none of the exported rows do).
  quant              parsed from the model string (IQ4_XS, Q4_K_M, ...) when it carries one.
  n_runs             parsed from the notes ("3 runs", "n=3", "1 run", "run0 greedy + 2 runs") or set in RUNS below
                     where a notebook section states it.
  n_items            items per run as the test is defined (TESTS below, from tests.csv / the notebook).

Serving backend is not a column: the backend named in a row's notes (`llama.cpp`, `ollama`) is the row's own
statement. Where the source's backend column was set but the published notes no longer evidence it, the
export prepends `backend: <backend>. ` to the notes.
"""
import argparse
import csv
import os
import re
import sys

PORTED = ["RED", "AUD", "MON", "HRD", "TOOL", "JUDGE", "CRIT", "SUM", "OCR", "CAT-SYN", "CAT-SYN-SCHEMA", "RENT-SYN", "SQL-SYN", "AUX-SYN",
          "PO-SYN", "RAGQ-SYN", "PO-REAL", "RAGQ-REAL"]
# OCR rows are exported with a caveat: they were measured on a page whose right-hand columns were clipped
# (tests/ocr.py explains); the lab's log shows the miss was `total` for 4 of 5 models.
CAVEAT = {"OCR": "CAVEAT: the 4/5 rows appear to come from a run on a page whose amount column was clipped at the right "
                 "edge (see tests/ocr.py; the lab log shows 4 of 5 models missing only the total). The ported test uses a "
                 "wider page, so scores are not comparable. A 'coordinates' row is a pointing test, not ported."}

HW = {"gpu": "GTX 1050 Ti", "vram_gb": "4", "cpu": "i5-6500"}

# items per run, from the test definitions (tests.csv, notebook sections 96, 96.1)
N_ITEMS = {"RED": "12", "HRD": "10", "MON": "1", "JUDGE": "6", "CRIT": "12", "SUM": "24", "OCR": "5", "CAT-SYN": "200", "CAT-SYN-SCHEMA": "100", "RENT-SYN": "100", "SQL-SYN": "50", "AUX-SYN": "27+12+6+31",
           "PO-SYN": "8", "RAGQ-SYN": "10", "PO-REAL": "12", "RAGQ-REAL": "12"}

# n_runs that the notebook states but the row's notes do not (section cited in the comment)
RUNS = {
    ("RED", "*"): "1",      # section 96: "n=1 at greedy"
    ("JUDGE", "*"): "3",    # section 96: run 0 greedy + 2 sampled runs
    ("CAT-SYN", "*"): "1",  # section 110: one pass, temp 0 where the preset sets it
    ("CAT-SYN-SCHEMA", "*"): "1",
    ("SQL-SYN", "*"): "1",
}

PATH_REFS = re.compile(r"\s*`?(?:[\w.-]+/)*raw/[\w./*-]*`?")   # pointers into the private lab repo's raw/ tree
QUANT = re.compile(r"\b(IQ\d_[A-Z]+|[Qq]\d_[A-Z0-9_]+|MXFP4_MoE|UD-Q\d_K_XL)\b")


def backend_of(row):
    text = (row["model"] + " " + row["notes"]).lower()
    if "llama.cpp" in text or "router" in text:
        return "llama.cpp"
    if "ollama" in text:
        return "ollama"
    return ""


def n_runs_of(row):
    key = (row["test"], row["model"])
    for k in (key, (row["test"], "*")):
        if k in RUNS:
            return RUNS[k]
    n = row["notes"]
    m = re.search(r"\b(\d+)\s+runs?\b", n) or re.search(r"\bn=(\d+)\b", n)
    if m:
        return m.group(1)
    m = re.search(r"run0 greedy \+ (\d+) runs", n)
    if m:
        return str(int(m.group(1)) + 1)
    if re.search(r"\b1 run\b", n):
        return "1"
    return ""


def clean_notes(notes):
    """Drop pointers into the private lab repo (raw/... paths) and collapse whitespace."""
    notes = PATH_REFS.sub("", notes)
    notes = re.sub(r"\(notebook ([\d.]+)\)", r"(§\1)", notes)
    notes = re.sub(r"\bnotebook ([\d.]+)", r"§\1", notes)
    return re.sub(r"\s+", " ", notes).strip(" .") + ("." if notes.strip() else "")


def backend_note(row):
    """If the source sets a backend the published notes no longer evidence, say it in the notes."""
    bk = backend_of(row)
    if not bk:
        return ""
    if any(w in row["notes"].lower() for w in ("llama.cpp", "ollama", "router")):
        return ""
    return "backend: %s. " % bk


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=os.environ.get("SOURCE_CSV", ""), help="the lab's results.csv")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "results.csv"))
    a = ap.parse_args()
    if not a.source:
        ap.error("give --source (or set SOURCE_CSV)")
    with open(a.source, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["test"] in PORTED]
    fields = ["model", "test", "score", "passed", "total", "metric_value", "metric_unit", "date",
              "section", "notes", "build", "quant", "n_runs", "n_items"]
    out = []
    for r in rows:
        q = QUANT.search(r["model"] + " " + r["notes"])
        extra = " ".join(x for x in (backend_note(r), CAVEAT.get(r["test"], ""),
                                     clean_notes(r["notes"])) if x)
        out.append({**{k: r[k] for k in ("model", "test", "score", "passed", "total",
                                         "metric_value", "metric_unit", "date", "section")},
                    "notes": extra, "build": "", "quant": q.group(1) if q else "",
                    "n_runs": n_runs_of(r), "n_items": N_ITEMS.get(r["test"], "")})
    order = {t: i for i, t in enumerate(PORTED)}
    out.sort(key=lambda r: (order[r["test"]], r["model"]))
    # preserve rows of the existing output whose test is not exported (hand-added rows, e.g. PTUNE)
    try:
        with open(a.out, newline="", encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if r["test"] not in PORTED]
    except FileNotFoundError:
        keep = []
    if keep:
        out.extend(keep)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    by = {t: sum(1 for r in out if r["test"] == t) for t in PORTED}
    print("wrote %s: %d rows %s" % (os.path.relpath(a.out), len(out), by))
    return 0


if __name__ == "__main__":
    sys.exit(main())
