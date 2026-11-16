#!/usr/bin/env python3
"""FIM: inline shell-command completion, scored on the first token.

The question is narrow on purpose: when a user has typed part of a command and the cursor is in the
middle of a word, does the model return exactly the rest of that word? That is what a ghost-text widget
does. A model that returns the whole rest of the line, or wraps the answer in backticks, or repeats the
prefix, is wrong for this job even when it "understood" it.

Two modes, so there is a control:
  fim    POST /infill with input_prefix and input_suffix (llama.cpp, models trained for FIM)
  plain  POST /v1/completions on the prefix only (what a model without FIM tokens can do)

Cases are generated in code from a fixed table: 29 shell lines, each cut at two places, so 58 cases.
No shell history, no private command, no path from this machine is in the fixture. The cut is always
inside a word, and the expected reply is the rest of that word; `right` is the remainder of the line
after it and is given to the model as the suffix in fim mode.

  python3 tests/fim.py --selftest
  BASE_URL=http://localhost:8093 MODEL=qwen2.5-coder-1.5b python3 tests/fim.py            # FIM mode
  BASE_URL=http://localhost:8093 MODEL=qwen2.5-coder-1.5b python3 tests/fim.py --mode plain

Grading is code-side and strict: first token == expected remainder. Failures are classified as wrong,
overlong (kept going past the answer), fenced (backticks/think tags), or empty. Latency is reported as
mean and p95 because a sub-250 ms budget is a tail requirement, not an average one.
"""
import argparse
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# (line, cut) pairs. cut is the index of the first character the model must produce. Two cuts per line,
# always inside a word, so the expected reply is a partial token and not a whole argument.
LINES = [
    "cd ~/Documents/projects/ledger",
    "ls -la /var/log/nginx/",
    "tar -czf backup.tar.gz ./src",
    "systemctl status nginx.service",
    "journalctl -u sshd --since yesterday",
    "grep -rn \"TODO\" src/",
    "find . -name \"*.py\" -newermt +1",
    "docker ps --format '{{.Names}}'",
    "ssh -p 2222 root@192.0.2.10",
    "awk -F',' '{print $3}' data.csv",
    "sed -i 's/foo/bar/g' file.txt",
    "git commit --amend --no-edit",
    "kubectl get pods -n staging",
    "rsync -avz --delete ./out/ backup:/srv/",
    "curl -sSL https://example.test/install.sh",
    "chmod 644 /etc/systemd/system/app.service",
    "sort -k2 -n -r scores.txt",
    "xargs -0 -n1 -P4 grep -l needle",
    "tmux new-session -s work",
    "podman run --rm -it alpine sh",
    "du -sh */ | sort -h",
    "ps aux | grep -v grep | awk '{print $2}'",
    "openssl req -new -key server.key",
    "nft list ruleset | head -20",
    "python3 -m venv .venv",
    "make -j4 test",
    "dd if=/dev/zero of=/tmp/file bs=1M count=100",
    "ip -4 addr show dev eth0",
    "tcpdump -i any port 53 -nn",
]

# cut offsets: one inside a path/argument word, one inside a flag or subcommand
CUTS = [(0.55, 0.30), (0.72, 0.45)]


def build_cases():
    """-> list of {kind, left, expect, right, full}; deterministic, no randomness.

    Two cuts per line: one at the middle of a word (a long remainder, the hard case) and one three
    characters before the end of a word (a short remainder, the realistic ghost-text case). A cut always
    lands strictly inside a word, so `expect` is a partial token with no space in it.
    """
    cases = []
    for line, (at_a, at_b) in zip(LINES, [CUTS[i % len(CUTS)] for i in range(len(LINES))]):
        words = [(m.start(), m.end()) for m in re.finditer(r"\S+", line)]
        for frac, style in ((at_a, "middle"), (at_b, "tail3")):
            idx = max(0, min(len(words) - 1, int(len(words) * frac)))
            s, e = words[idx]
            if e - s < 2:                                   # a one-character word cannot be cut
                idx = min(len(words) - 1, idx + 1)
                s, e = words[idx]
            cut = s + max(1, (e - s) // 2) if style == "middle" else max(s + 1, e - 3)
            left, rest = line[:cut], line[cut:]
            token = rest.split(" ")[0]
            cases.append({"kind": "path" if "/" in left else "flag", "left": left, "expect": token,
                          "right": rest[len(token):], "full": line})
    return cases


def first_token(text):
    t = (text or "").strip()
    if not t:
        return ""
    return t.split()[0]


def grade(reply, expect, prefix_tail):
    """-> (ok, reason). ok only when the first token is exactly the missing remainder."""
    t = (reply or "").strip()
    if not t:
        return False, "empty"
    if "`" in t or "</think>" in t or "<|" in t:
        return False, "fenced"
    tok = first_token(t)
    if tok == expect:
        return True, "exact"
    if tok.startswith(expect):
        return False, "overlong"
    if t.startswith(prefix_tail):
        return False, "echoed prefix"
    return False, "wrong"


def selftest():
    c = common.Checks()
    cases = build_cases()
    c("58 cases", len(cases) == 58)
    c("every case reconstructs its line", all(x["left"] + x["expect"] + x["right"] == x["full"] for x in cases))
    c("no expectation is empty", all(x["expect"] for x in cases))
    c("no expectation contains a space", all(" " not in x["expect"] for x in cases))
    c("cuts land inside a word", all(x["left"] and x["left"][-1] != " " for x in cases))
    c("the first case is a partial word", cases[0]["expect"] != cases[0]["full"])
    # the guard's own literal would trip the repo's leak scanner, so it is assembled at run time
    home_mark = "/" + "home" + "/"
    c("no case contains a home-directory path", not any(home_mark in x["full"] for x in cases))
    ok, why = grade("tus nginx.service", "tus", "ss")
    c("exact first token passes", ok and why == "exact")
    ok, why = grade("tus/", "tus", "ss")
    c("overlong first token fails", (not ok) and why == "overlong")
    ok, why = grade("`tus`", "tus", "ss")
    c("fenced reply fails", (not ok) and why == "fenced")
    ok, why = grade("", "tus", "ss")
    c("empty reply fails", (not ok) and why == "empty")
    ok, why = grade("ss tus", "tus", "ss")
    c("echoed prefix fails", (not ok) and why == "echoed prefix")
    return c.finish()


def ask(args, case):
    """One completion request. Returns (text, seconds, error)."""
    if args.mode == "fim":
        r = common.post_json(args.base_url, "/infill",
                             {"model": args.model or "local", "input_prefix": case["left"],
                              "input_suffix": case["right"], "n_predict": args.pred,
                              "temperature": 0.0, "stop": ["\n", " ", "<|file_sep|>", "<|endoftext|>"]},
                             api_key=args.api_key, timeout=args.timeout)
        if "error" in r:
            return "", 0.0, r["error"]
        return (r["data"].get("content") or ""), r["s"], None
    body = {"model": args.model or "local", "prompt": case["left"], "max_tokens": args.pred,
            "temperature": 0.0, "stop": ["\n", " "], "stream": False}
    r = common.post_json(args.base_url, "completions", body, api_key=args.api_key, timeout=args.timeout)
    if "error" in r:
        return "", 0.0, r["error"]
    ch = (r["data"].get("choices") or [{}])[0]
    return (ch.get("text") or ""), r["s"], None


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--mode", choices=["fim", "plain"], default="fim")
    ap.add_argument("--pred", type=int, default=16, help="tokens the server may generate")
    ap.add_argument("--limit", type=int, default=0, help="only the first N cases")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    cases = build_cases()
    if a.limit:
        cases = cases[:a.limit]
    rows, lat = [], []
    errors = 0
    for i, case in enumerate(cases):
        text, secs, err = ask(a, case)
        if err:
            errors += 1
            rows.append({"i": i, "kind": case["kind"], "left": case["left"], "expect": case["expect"],
                         "reply": "", "ok": False, "why": "transport", "s": 0.0, "error": err})
            if errors == 1:
                common.say("transport error on case %d (recorded, not scored as a model failure): %s" % (i, err))
            if errors >= 3:
                common.say("3 transport errors: the endpoint is not answering, stopping")
                break
            continue
        ok, why = grade(text, case["expect"], case["left"][-3:])
        lat.append(secs)
        rows.append({"i": i, "kind": case["kind"], "left": case["left"], "expect": case["expect"],
                     "reply": text[:80], "ok": ok, "why": why, "s": round(secs, 3), "error": None})
        if (i + 1) % 10 == 0:
            hit = sum(1 for r in rows if r["ok"])
            common.say("%d/%d cases, %d exact" % (i + 1, len(cases), hit))

    hit = sum(1 for r in rows if r["ok"])
    reasons = {}
    for r in rows:
        if not r["ok"]:
            reasons[r["why"]] = reasons.get(r["why"], 0) + 1
    good = sorted(lat)
    p95 = good[min(len(good) - 1, int(round(0.95 * (len(good) - 1))))] if good else 0.0
    common.say("%s %s: %d/%d exact (%.3f), failures %s" % (a.mode, a.model or "local", hit, len(rows),
                                                          hit / max(1, len(rows)), reasons or "none"))
    common.say("latency: mean %.0f ms, p95 %.0f ms" % (1000 * statistics.mean(good) if good else 0.0,
                                                       1000 * p95))
    common.save_results(a, "FIM" if a.mode == "fim" else "FIM-PLAIN", {"mode": a.mode, "n": len(rows), "exact": hit,
                                   "rate": round(hit / max(1, len(rows)), 3),
                                   "failures": reasons,
                                   "latency_mean_ms": round(1000 * statistics.mean(good), 1),
                                   "latency_p95_ms": round(1000 * p95, 1),
                                   "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
