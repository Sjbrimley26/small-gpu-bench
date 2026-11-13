"""Shared helpers for the scripts in tests/ (stdlib only).

Every test reads BASE_URL / MODEL / API_KEY from the environment (flags override), talks to an
OpenAI-compatible /v1/chat/completions endpoint, and writes a JSON record to ./results/raw/
(relative to the current directory; change with --out-dir).
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://localhost:8080"   # llama-server's default port; override with BASE_URL


def add_common_args(ap):
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", DEFAULT_BASE_URL),
                    help="OpenAI-compatible server root (env BASE_URL)")
    ap.add_argument("--model", default=os.environ.get("MODEL", ""),
                    help="model name sent in the request (env MODEL)")
    ap.add_argument("--api-key", default=os.environ.get("API_KEY", ""),
                    help="bearer token, if the server wants one (env API_KEY)")
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("TIMEOUT", "1200")),
                    help="seconds per request (env TIMEOUT, default 1200)")
    ap.add_argument("--think", choices=["auto", "on", "off"], default=os.environ.get("THINK", "auto"),
                    help="send chat_template_kwargs.enable_thinking (env THINK); auto = do not send")
    ap.add_argument("--out-dir", default="results/raw", help="where the JSON record goes")
    return ap


def _url(base):
    base = base.rstrip("/")
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


def chat(args, messages, *, max_tokens, temperature=0.0, top_p=None, seed=None, tools=None):
    """One non-streaming chat call. Never raises: errors come back as {'error': ...}."""
    body = {"model": args.model or "local", "messages": messages, "stream": False,
            "max_tokens": max_tokens, "temperature": temperature}
    if top_p is not None:
        body["top_p"] = top_p
    if seed is not None:
        body["seed"] = seed
    if tools:
        body["tools"] = tools
    if args.think != "auto":
        body["chat_template_kwargs"] = {"enable_thinking": args.think == "on"}
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = "Bearer " + args.api_key
    req = urllib.request.Request(_url(args.base_url), data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        return {"s": round(time.time() - t0, 1), "content": "", "finish": "error",
                "error": "HTTP %d: %s" % (e.code, e.read()[:300].decode("utf8", "replace"))}
    except Exception as e:  # noqa: BLE001 - any transport problem is a recorded failure, not a crash
        return {"s": round(time.time() - t0, 1), "content": "", "finish": "error",
                "error": "%s: %s" % (type(e).__name__, e)}
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content") or ""
    if "</think>" in content:                      # some servers leave the trace inline
        content = content.split("</think>", 1)[1]
    usage = data.get("usage") or {}
    return {"s": round(time.time() - t0, 1), "content": content.strip(),
            "reasoning_chars": len(msg.get("reasoning_content") or ""),
            "finish": ch.get("finish_reason"), "tool_calls": msg.get("tool_calls") or [],
            "completion_tokens": usage.get("completion_tokens"),
            "prompt_tokens": usage.get("prompt_tokens")}


def save_results(args, test, payload):
    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", args.model or "local")
    path = os.path.join(args.out_dir, "%s-%s-%s.json" % (test, safe, stamp))
    skip = {"api_key", "base_url", "out_dir"}       # keep credentials and hostnames out of result files
    rec = {"test": test, "model": args.model or "local", "date": stamp,
           "args": {k: v for k, v in vars(args).items() if k not in skip}, **payload}
    with open(path, "w") as f:
        json.dump(rec, f, indent=1)
    print("wrote", path)
    return path


class Checks:
    """Tiny assertion collector for --selftest modes (no model involved)."""

    def __init__(self):
        self.ok = True

    def __call__(self, name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        self.ok &= bool(cond)

    def finish(self, label="SELFTEST"):
        print(label, "OK" if self.ok else "FAILED")
        return 0 if self.ok else 1


def fixture_path(*parts):
    """Fixture files live next to the scripts; resolved from this file, never from a fixed prefix."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", *parts)


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def say(*a):
    print(*a, flush=True)


def die(msg):
    print("error:", msg, file=sys.stderr)
    sys.exit(2)
