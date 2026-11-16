#!/usr/bin/env python3
"""EMBED: what an embedding server costs, and whether moving it off the card changes the vectors.

Two questions, one run. (1) How long does indexing take (milliseconds per chunk) and how fast is a
warm single-chunk query? (2) If you serve the same weights on two devices (GPU and CPU), are the
vectors still the same? The second one decides whether the split is safe: identical vectors mean a
query can hit the CPU copy and still match an index the GPU built.

The lab measured exactly this pair (664 MB of a 4 GB card against 4.5x slower indexing) and shipped
the CPU copy as the default. This script is the portable version: it takes two OpenAI-compatible
endpoints, uses synthetic text generated from a seed, and prints the three numbers that decided it.

  python3 tests/embed_timing.py --selftest
  BASE_URL=http://localhost:8092 MODEL=bge-m3-cpu python3 tests/embed_timing.py
  BASE_URL=http://localhost:8092 MODEL=bge-m3 \\          # device A
    BASE_URL_B=http://localhost:8092 MODEL_B=bge-m3-cpu \\  # device B, same server is fine
    python3 tests/embed_timing.py --chunks 8 --queries 10

Scoring is code-side: ms/chunk from the batched call, median/p95 from the query loop, and the minimum
cosine between the two endpoints' vectors for the same text. No model judges anything.

Cold load is NOT measured here (a fresh server's first call includes loading the weights, which is
50-150 s on this card and would swamp the sample). Restart the server yourself if you want that number.
"""
import argparse
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

WORDS = ("order invoice freight ledger payee receipt balance column subtotal vendor remit taxable "
         "quantity discount catalog item price basis terms net shipped carrier pallet crate bundle "
         "warehouse counter pickup delivery schedule batch ticket credit memo return damaged").split()


def make_chunk(seed, target_chars=3200):
    """One synthetic prose chunk of about target_chars, deterministic for a given seed."""
    rng = random.Random(seed)
    out = []
    while sum(len(w) + 1 for w in out) < target_chars:
        n = rng.randint(6, 14)
        out.extend(rng.choice(WORDS) for _ in range(n))
        out[-1] += "."
    return " ".join(out)[:target_chars]


def make_chunks(n, target_chars=3200, seed=20261116):
    return [make_chunk(seed + i, target_chars) for i in range(n)]


def median_p95(values):
    if not values:
        return None, None
    vals = sorted(values)
    p95 = vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]
    return statistics.median(vals), p95


def measure(args, chunks, queries, query_texts=None, base_url=None, model=None, label="A"):
    """-> dict with ms_per_chunk, query median/p95, vectors, and any error."""
    base = base_url or args.base_url
    mdl = model or args.model or "local"
    qtexts = query_texts or chunks
    out = {"label": label, "model": mdl, "n_chunks": len(chunks), "chunk_chars": len(chunks[0]) if chunks else 0}
    batch = common.embed(args, chunks, model=mdl, base_url=base)
    if "error" in batch:
        out["error"] = batch["error"]
        return out
    total_ms = batch["s"] * 1000.0
    out["batch_s"] = round(batch["s"], 3)
    out["ms_per_chunk"] = round(total_ms / max(1, len(chunks)), 1)
    out["vectors"] = batch["vectors"]
    out["dim"] = len(batch["vectors"][0]) if batch["vectors"] else 0
    lat = []
    for i in range(queries):
        r = common.embed(args, [qtexts[i % len(qtexts)]], model=mdl, base_url=base)
        if "error" in r:
            out["error"] = r["error"]
            return out
        lat.append(r["s"])
    med, p95 = median_p95(lat)
    out.update(query_median_s=med, query_p95_s=p95, query_n=len(lat), query_chars=len(qtexts[0]) if qtexts else 0)
    return out


def compare(a, b):
    """Min cosine between two endpoints' vectors for the same texts (1.0 = identical)."""
    va, vb = a.get("vectors") or [], b.get("vectors") or []
    if not va or not vb or len(va) != len(vb):
        return None
    return min(common.cosine(x, y) for x, y in zip(va, vb))


def selftest():
    c = common.Checks()
    ch = make_chunks(3, 400)
    c("chunks are deterministic", ch == make_chunks(3, 400))
    c("different seeds give different text", ch[0] != ch[1])
    c("chunk length is close to target", all(380 <= len(x) <= 400 for x in ch))
    c("cosine of a vector with itself is 1.0", abs(common.cosine([1, 2, 3], [1, 2, 3]) - 1.0) < 1e-9)
    c("cosine of opposite vectors is -1.0", abs(common.cosine([1, 0], [-1, 0]) + 1.0) < 1e-9)
    c("cosine of a length mismatch is 0.0", common.cosine([1, 2], [1, 2, 3]) == 0.0)
    med, p95 = median_p95([0.1, 0.2, 0.3, 0.4, 1.0])
    c("median/p95 of five values", med == 0.3 and p95 == 1.0)
    c("median/p95 of an empty list is (None, None)", median_p95([]) == (None, None))
    a = {"vectors": [[1.0, 0.0], [0.0, 1.0]]}
    b = {"vectors": [[1.0, 0.0], [1.0, 0.0]]}
    worst = compare(a, b)
    c("compare() takes the worst pair, not the best", worst is not None and abs(worst) < 1e-9)
    c("compare() is None when the counts differ", compare(a, {"vectors": [[1.0, 0.0]]}) is None)
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--base-url-b", default=os.environ.get("BASE_URL_B", ""),
                    help="second endpoint to compare against (env BASE_URL_B); optional")
    ap.add_argument("--model-b", default=os.environ.get("MODEL_B", ""),
                    help="model name on the second endpoint (env MODEL_B)")
    ap.add_argument("--chunks", type=int, default=8, help="batched chunks in the indexing call")
    ap.add_argument("--queries", type=int, default=10, help="single-chunk queries for warm latency")
    ap.add_argument("--chunk-chars", type=int, default=3200, help="characters per chunk (~800 tokens)")
    ap.add_argument("--query-chars", type=int, default=120, help="characters per query (a real search query, not a chunk)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    chunks = make_chunks(max(1, a.chunks), a.chunk_chars)
    qtexts = make_chunks(max(1, a.queries), a.query_chars, seed=777)
    A = measure(a, chunks, a.queries, qtexts, label="A")
    if "error" in A:
        common.say("A failed:", A["error"])
    else:
        common.say("A %-22s %d chunks x %d chars: %.1f ms/chunk (%.2f s total), %d-dim"
                   % (A["model"], A["n_chunks"], A["chunk_chars"], A["ms_per_chunk"], A["batch_s"], A["dim"]))
        common.say("A warm query (%d chars): median %.3f s, p95 %.3f s (n=%d)"
                   % (A["query_chars"], A["query_median_s"], A["query_p95_s"], A["query_n"]))

    B, cos = None, None
    if a.base_url_b:
        B = measure(a, chunks, a.queries, qtexts, base_url=a.base_url_b, model=a.model_b, label="B")
        if "error" in B:
            common.say("B failed:", B["error"])
        else:
            common.say("B %-22s %d chunks x %d chars: %.1f ms/chunk (%.2f s total), %d-dim"
                       % (B["model"], B["n_chunks"], B["chunk_chars"], B["ms_per_chunk"], B["batch_s"], B["dim"]))
            common.say("B warm query (%d chars): median %.3f s, p95 %.3f s (n=%d)"
                       % (B["query_chars"], B["query_median_s"], B["query_p95_s"], B["query_n"]))
            cos = compare(A, B)
            if cos is None:
                common.say("vectors: cannot compare (one side returned none, or dimensions differ)")
            else:
                common.say("vectors: min cosine %.6f over %d chunks (%.6f = same vectors)"
                           % (cos, len(A["vectors"]), 1.0))
            if A.get("ms_per_chunk") and B.get("ms_per_chunk"):
                common.say("indexing speed ratio A/B: %.2fx" % (A["ms_per_chunk"] / B["ms_per_chunk"],))

    rec = {"chunks": A["n_chunks"], "chunk_chars": A["chunk_chars"], "A": A, "B": B,
           "min_cosine": cos,
           "speed_ratio": (A["ms_per_chunk"] / B["ms_per_chunk"]) if (A.get("ms_per_chunk") and B and B.get("ms_per_chunk")) else None}
    for side in (rec["A"], rec.get("B")):
        if side:
            side.pop("vectors", None)          # keep the record small; the numbers are the result
    common.save_results(a, "EMBED", rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
