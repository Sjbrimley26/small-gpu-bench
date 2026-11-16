#!/usr/bin/env python3
"""SPEED: prefill and generation throughput for one model on one endpoint, measured the same way every time.

The method is fixed on purpose, because a tokens/s number means nothing without it: a 444-token prompt
(about 1,776 characters), 200 tokens generated, temperature 0, one warm-up request that is discarded, then
N measured requests. Server-side timings are used when the server reports them (llama.cpp does, in a
`timings` block); otherwise the wall clock and the token counts are used and the record says so.

Two things this deliberately does NOT do. It does not load or unload the model, so run it against an
endpoint you control the residency of. And it does not report a number for a card that is already holding
another model: on this 4 GB card a second resident model reads 2-4x slow with no error, which is the
single easiest way to publish a wrong speed figure. The lab's rule is "empty card, one model resident".

  python3 tests/speed.py --selftest
  BASE_URL=... MODEL=... python3 tests/speed.py --reps 2
  BASE_URL=... MODEL=... python3 tests/speed.py --nvidia-smi     # record VRAM used, NVIDIA only

Writes one JSON record per run; --reps values are all kept so you can see the spread.
"""
import argparse
import os
import statistics
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

PROMPT_TOKENS = 444          # the lab's fixed size; ~4 chars per token
GEN_TOKENS = 200
PROMPT_CHARS = PROMPT_TOKENS * 4

FILLER = ("The workshop keeps a ledger for every job it runs, and the ledger records the order, the "
          "materials drawn, the hours booked against it, and the balance still owed by the customer. ")

PROMPT = (FILLER * 40)[:PROMPT_CHARS]


def build_prompt(chars=PROMPT_CHARS):
    """A fixed-length, content-free prompt. Deterministic, so two runs measure the same input."""
    return (FILLER * (chars // len(FILLER) + 2))[:chars]


def vram_used_mib():
    """VRAM in use, or None when there is no NVIDIA device. Best-effort: never raises."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        used, total = out.stdout.strip().split("\n")[0].split(",")
        return {"used_mib": int(used), "total_mib": int(total)}
    except Exception:  # noqa: BLE001 - a missing nvidia-smi is not an error on another machine
        return None


def rates(reply, prompt_tokens, gen_tokens):
    """Prefill and generation t/s from llama.cpp's timings when present, else from the wall clock."""
    t = reply.get("timings") or {}
    if t.get("prompt_per_second") and t.get("predicted_per_second"):
        return {"prefill_tps": round(float(t["prompt_per_second"]), 1),
                "gen_tps": round(float(t["predicted_per_second"]), 1),
                "prompt_tokens": t.get("prompt_n") or prompt_tokens,
                "completion_tokens": t.get("predicted_n") or gen_tokens,
                "source": "server timings"}
    seconds = reply.get("s") or 0.0
    if not seconds or not gen_tokens:
        return {"prefill_tps": None, "gen_tps": None, "prompt_tokens": prompt_tokens,
                "completion_tokens": gen_tokens, "source": "wall clock", "note": "no timing information"}
    # wall clock cannot separate prefill from decode; a 200-token reply is dominated by decode, so the
    # whole request is reported as a lower bound on generation and prefill is left unknown.
    return {"prefill_tps": None, "gen_tps": round(gen_tokens / seconds, 1),
            "prompt_tokens": prompt_tokens, "completion_tokens": gen_tokens,
            "source": "wall clock", "note": "prefill not separable without server timings"}


def selftest():
    c = common.Checks()
    c("prompt is the advertised length", len(build_prompt()) == PROMPT_CHARS)
    c("prompt is deterministic", build_prompt() == build_prompt())
    srv = {"s": 9.9, "timings": {"prompt_per_second": 518.0, "predicted_per_second": 32.1,
                                 "prompt_n": 444, "predicted_n": 200}}
    r = rates(srv, 444, 200)
    c("server timings are preferred", r["source"] == "server timings" and r["prefill_tps"] == 518.0 and r["gen_tps"] == 32.1)
    wall = {"s": 10.0, "timings": {}}
    r = rates(wall, 444, 200)
    c("wall-clock fallback reports generation only",
      r["source"] == "wall clock" and r["gen_tps"] == 20.0 and r["prefill_tps"] is None)
    c("a reply with no timing and no seconds returns nulls, not a division error",
      rates({"s": 0, "timings": {}}, 444, 200)["gen_tps"] is None)
    v = vram_used_mib()
    c("vram probe returns a dict or None", v is None or "used_mib" in v)
    c("median of an odd list", statistics.median([1, 2, 9]) == 2)
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--reps", type=int, default=2, help="measured requests after the warm-up")
    ap.add_argument("--gen", type=int, default=GEN_TOKENS, help="tokens to generate")
    ap.add_argument("--prompt-chars", type=int, default=PROMPT_CHARS, help="prompt size in characters")
    ap.add_argument("--nvidia-smi", action="store_true", help="record VRAM used before and after (NVIDIA only)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    prompt = build_prompt(a.prompt_chars)
    msgs = [{"role": "user", "content": prompt}]
    before = vram_used_mib() if a.nvidia_smi else None
    # cache_prompt off: with it on, rep 2 re-uses rep 1's KV and prompt_per_second reports the tokens
    # that were NOT cached (1-4), which is how a "prefill" number ends up 15x too low.
    # ignore_eos on: a model that stops at 38 tokens measures decode on 38 tokens. Fixed length or nothing.
    knobs = {"cache_prompt": False, "ignore_eos": True}

    warm = common.chat(a, msgs, max_tokens=8, temperature=0.0, extra=knobs)   # discarded: first call carries any load cost
    if warm.get("error"):
        common.say("warm-up failed:", warm["error"])
        common.save_results(a, "SPEED", {"error": warm["error"], "prompt_chars": len(prompt)})
        return 1

    rows = []
    for i in range(max(1, a.reps)):
        r = common.chat(a, msgs, max_tokens=a.gen, temperature=0.0, extra=knobs)
        if r.get("error"):
            rows.append({"rep": i, "error": r["error"]})
            common.say("rep%d failed: %s" % (i, r["error"]))
            continue
        g = rates(r, r.get("prompt_tokens"), r.get("completion_tokens") or a.gen)
        g.update(rep=i, wall_s=r["s"], finish=r.get("finish"))
        short = (g.get("completion_tokens") or 0) < 0.9 * a.gen
        if short:
            g["short_reply"] = True
        rows.append(g)
        common.say("rep%d prefill %s t/s, gen %s t/s, %s tokens%s, %.1fs wall"
                   % (i, g["prefill_tps"], g["gen_tps"], g["completion_tokens"],
                      " (SHORT - server ignored ignore_eos or capped output)" if short else "", r["s"]))

    ok = [x for x in rows if x.get("gen_tps")]
    summary = {}
    if ok:
        summary = {"prefill_tps_median": statistics.median([x["prefill_tps"] for x in ok]) if ok[0].get("prefill_tps") else None,
                   "gen_tps_median": statistics.median([x["gen_tps"] for x in ok]),
                   "gen_tps_min": min(x["gen_tps"] for x in ok),
                   "gen_tps_max": max(x["gen_tps"] for x in ok),
                   "n_ok": len(ok)}
        common.say("median: prefill %s t/s, gen %.1f t/s (n=%d)"
                   % (summary["prefill_tps_median"], summary["gen_tps_median"], summary["n_ok"]))
    after = vram_used_mib() if a.nvidia_smi else None
    common.save_results(a, "SPEED", {"prompt_chars": len(prompt), "gen_tokens": a.gen,
                                     "rows": rows, "summary": summary,
                                     "vram_before": before, "vram_after": after})
    return 0


if __name__ == "__main__":
    sys.exit(main())
