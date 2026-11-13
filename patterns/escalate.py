#!/usr/bin/env python3
"""escalate: a checker-gated escalation loop for small local models (stdlib only).

The pattern (from the lab's relay job queue):

  * The model only EXTRACTS. Code decides what to do with the result.
  * A task is two functions: build_messages(item) and check(item, reply) -> (ok, parsed, why).
  * check() rejects bad output. On rejection the same endpoint is retried once with the rejection reason
    appended to the conversation; if it still fails, the loop escalates to the next endpoint in the list
    (a bigger model, a thinking-on seat, a slower box).
  * The best checks cross-check the model's answer against fields the source already carries in structured
    form (a price field, a count, an ID). Those are things the model cannot argue with.
  * If every rung fails, the result is "failed" with the last reason. Nothing is silently accepted.

Endpoints come from the environment so nothing about your setup is baked in:

  BASE_URL, MODEL, API_KEY          the default (first) rung
  BASE_URL_<ROLE>, MODEL_<ROLE>,    optional per-role overrides, e.g. BASE_URL_ESCALATE / MODEL_ESCALATE
  API_KEY_<ROLE>                    for a second rung; ROLES lists the rungs in order (default "DEFAULT,ESCALATE")

Run the example task on its synthetic data:

  python3 escalate.py --selftest                 no model, scripted replies
  python3 escalate.py                            real endpoint(s) from the environment
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------- endpoints

@dataclass
class Endpoint:
    name: str
    base_url: str
    model: str
    api_key: str = ""
    max_tokens: int = 1500
    temperature: float = 0.0
    think: bool | None = None          # None = do not send enable_thinking
    timeout: float = 600.0

    def chat(self, messages: list[dict]) -> str:
        base = self.base_url.rstrip("/")
        url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
        body = {"model": self.model or "local", "messages": messages, "stream": False,
                "max_tokens": self.max_tokens, "temperature": self.temperature}
        if self.think is not None:
            body["chat_template_kwargs"] = {"enable_thinking": self.think}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        return data["choices"][0]["message"].get("content") or ""


def endpoints_from_env(env=os.environ) -> list[Endpoint]:
    """Build the rung list from BASE_URL/MODEL/API_KEY plus per-role overrides.

    ROLES (comma list, default "DEFAULT,ESCALATE") names the rungs in order. DEFAULT reads the plain
    BASE_URL/MODEL/API_KEY; any other role X reads BASE_URL_X/MODEL_X/API_KEY_X and falls back to the
    plain value for whatever it does not set. A role with no override of its own is skipped, so with
    only BASE_URL set you get one rung."""
    base, model, key = env.get("BASE_URL", "http://localhost:8080"), env.get("MODEL", ""), env.get("API_KEY", "")
    out: list[Endpoint] = []
    for role in [r.strip().upper() for r in env.get("ROLES", "DEFAULT,ESCALATE").split(",") if r.strip()]:
        if role == "DEFAULT":
            out.append(Endpoint("default", base, model, key))
            continue
        b, m, k = env.get(f"BASE_URL_{role}"), env.get(f"MODEL_{role}"), env.get(f"API_KEY_{role}")
        if not (b or m):
            continue
        think = env.get(f"THINK_{role}")
        out.append(Endpoint(role.lower(), b or base, m or model, k if k is not None else key,
                            think=None if think is None else think.lower() in ("1", "true", "on")))
    return out


# ---------------------------------------------------------------- the loop

# A task is any object (a module works) with:
#   NAME: str
#   build_messages(item: dict) -> list[dict]
#   check(item: dict, reply: str) -> (ok: bool, parsed, why: str)
Task = Any


@dataclass
class Result:
    status: str                         # "done" | "failed"
    parsed: object = None
    rung: str = ""                      # name of the endpoint that produced the accepted answer
    attempts: list = field(default_factory=list)   # one dict per model call: rung, ok, why, seconds
    why: str = ""


def run_item(task: Task, item: dict, rungs: list[Endpoint], retries: int = 1,
             chat: Callable[[Endpoint, list[dict]], str] | None = None) -> Result:
    """Try each rung in order. Within a rung: ask, check; on rejection retry up to `retries` times with the
    rejection reason fed back. An endpoint that cannot be reached counts as a failed rung, not a crash."""
    chat = chat or (lambda ep, msgs: ep.chat(msgs))
    attempts: list[dict] = []
    last_why = "no endpoints configured"
    for ep in rungs:
        messages = task.build_messages(item)
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                reply = chat(ep, messages)
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as e:
                last_why = f"{ep.name}: endpoint error: {e}"
                attempts.append({"rung": ep.name, "ok": False, "why": last_why, "s": round(time.time() - t0, 1)})
                break                                   # no point retrying a dead endpoint; escalate
            ok, parsed, why = task.check(item, reply)
            attempts.append({"rung": ep.name, "ok": ok, "why": why, "s": round(time.time() - t0, 1)})
            if ok:
                return Result("done", parsed, ep.name, attempts, "ok")
            last_why = f"{ep.name}: {why}"
            messages = messages + [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"That answer was rejected: {why}. Reply again with ONLY the corrected JSON object."},
            ]
    return Result("failed", None, "", attempts, last_why)


# ---------------------------------------------------------------- example task (synthetic data)
# "The model only extracts, code decides, and the checker cross-checks the answer against the item's own
# structured fields" - here, a made-up equipment-listing record with a price field and a bedroom-style count.

NAME = "listing_extract"

KINDS = ["sofa", "bike", "laptop", "bookshelf", "other"]
CONDITIONS = ["new", "good", "worn", "unclear"]

SYSTEM = f"""You extract facts from one classified ad. Reply with ONE JSON object and nothing else:
{{"price": int USD or null, "item": one of {KINDS}, "condition": one of {CONDITIONS},
 "pickup_only": true or false, "red_flags": list of short strings (scam signs: wire transfer, gift cards, seller abroad,
 price far below normal); [] if none}}
Use only what the ad says."""

# Synthetic records. `fields` plays the role of the source's own structured data.
ITEMS = [
    {"id": "ad-001", "title": "Grey 3-seat sofa, good condition",
     "fields": {"price": 180, "count": 3},
     "body": "Selling our grey sofa, 3 seats, some wear on the arms but sturdy. $180 firm. Pickup only, no delivery."},
    {"id": "ad-002", "title": "Road bike, barely used",
     "fields": {"price": 425, "count": 1},
     "body": "Aluminium road bike, ridden maybe ten times. Asking 425. Can meet halfway, happy to ship at buyer's cost."},
    {"id": "ad-003", "title": "Laptop 14in NEW sealed box",
     "fields": {"price": 90, "count": 1},
     "body": "Brand new laptop, sealed. I am overseas for work so please pay by gift card and my cousin will send it. $90."},
    {"id": "ad-004", "title": "Bookshelf, solid wood",
     "fields": {"price": 60, "count": 5},
     "body": "Five-shelf bookshelf, solid wood, a few scratches. $60, you collect."},
]


def build_messages(item: dict) -> list[dict]:
    user = f"Title: {item['title']}\nPrice field: {item['fields']['price']}\n\n{item['body'][:4000]}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def check(item: dict, text: str) -> tuple[bool, object, str]:
    """Reject anything malformed or that contradicts the ad's own structured fields."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return False, None, "no JSON object in reply"
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError as e:
        return False, None, f"invalid JSON: {e}"
    for k in ("price", "item", "condition", "pickup_only", "red_flags"):
        if k not in d:
            return False, None, f"missing key {k!r}"
    if d["item"] not in KINDS:
        return False, None, f"item must be one of {KINDS}"
    if d["condition"] not in CONDITIONS:
        return False, None, f"condition must be one of {CONDITIONS}"
    if not isinstance(d["pickup_only"], bool):
        return False, None, "pickup_only must be true or false"
    if not isinstance(d["red_flags"], list):
        return False, None, "red_flags must be a list"
    # cross-check against the record's own structured price: a mismatch is allowed only if the number
    # the model gave actually appears in the text
    want = item["fields"]["price"]
    if d["price"] is not None and abs(float(d["price"]) - want) > max(5, 0.05 * want):
        # (whole-number match: a bare substring test would let 18 pass because "$180" contains it)
        if not re.search(r"(?<![\d.])%d(?![\d.]|,\d)" % int(float(d["price"])), item["body"].replace(",", "")):
            return False, None, f"price {d['price']} disagrees with the price field {want} and is not in the text"
    return True, d, "ok"


def decide(parsed: Any) -> str:
    """Code, not the model, decides what the extraction means."""
    if parsed["red_flags"]:
        return "skip: " + "; ".join(parsed["red_flags"])
    return "keep"


# ---------------------------------------------------------------- selftest and CLI

def selftest() -> bool:
    from types import SimpleNamespace
    task = SimpleNamespace(NAME=NAME, build_messages=build_messages, check=check)
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    item = ITEMS[0]
    good = json.dumps({"price": 180, "item": "sofa", "condition": "good", "pickup_only": True, "red_flags": []})
    bad_price = json.dumps({"price": 18, "item": "sofa", "condition": "good", "pickup_only": True, "red_flags": []})
    chk("check accepts a good reply", check(item, good)[0])
    chk("check rejects prose", not check(item, "It is a sofa.")[0])
    chk("check rejects a bad enum", not check(item, good.replace('"sofa"', '"couch"'))[0])
    chk("check cross-checks the price field", not check(item, bad_price)[0])
    chk("price 18 is not 'found' inside $180", "disagrees" in check(item, bad_price)[2])
    chk("check accepts a price that is in the text", check(ITEMS[1], json.dumps(
        {"price": 425, "item": "bike", "condition": "good", "pickup_only": False, "red_flags": []}))[0])

    rungs = [Endpoint("small", "x", "m1"), Endpoint("big", "x", "m2")]
    # 1. first reply bad, retry with the reason succeeds on the same rung
    calls = []
    def script_retry(ep, msgs):
        calls.append((ep.name, msgs[-1]["content"][:30]))
        return bad_price if len(calls) == 1 else good
    r = run_item(task, item, rungs, chat=script_retry)
    chk("retry on same rung with the reason", r.status == "done" and r.rung == "small" and len(calls) == 2
        and "rejected" in calls[1][1])
    # 2. small rung always bad -> escalates to the next endpoint
    def script_escalate(ep, msgs):
        return "no idea" if ep.name == "small" else good
    r = run_item(task, item, rungs, chat=script_escalate)
    chk("escalates after retries are spent", r.status == "done" and r.rung == "big" and len(r.attempts) == 3)
    # 3. everything bad -> failed, never silently accepted
    r = run_item(task, item, rungs, chat=lambda ep, m: "nope")
    chk("fails when every rung fails", r.status == "failed" and r.parsed is None and len(r.attempts) == 4)
    # 4. dead endpoint -> escalate, not crash
    def script_dead(ep, msgs):
        if ep.name == "small":
            raise urllib.error.URLError("connection refused")
        return good
    r = run_item(task, item, rungs, chat=script_dead)
    chk("unreachable endpoint escalates", r.status == "done" and r.rung == "big")
    # 5. code decides
    scam = json.dumps({"price": 90, "item": "laptop", "condition": "new", "pickup_only": False,
                       "red_flags": ["gift card payment", "seller overseas"]})
    r = run_item(task, ITEMS[2], rungs[:1], chat=lambda ep, m: scam)
    chk("code decision uses the flags", r.status == "done" and decide(r.parsed).startswith("skip"))
    env = {"BASE_URL": "http://a", "MODEL": "m", "BASE_URL_ESCALATE": "http://b", "MODEL_ESCALATE": "m2"}
    eps = endpoints_from_env(env)
    chk("env builds two rungs", [e.name for e in eps] == ["default", "escalate"] and eps[1].base_url == "http://b")
    chk("one rung when no override", len(endpoints_from_env({"BASE_URL": "http://a"})) == 1)
    print("SELFTEST", "OK" if ok else "FAILED")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Checker-gated escalation loop, example task on synthetic ads.")
    ap.add_argument("--selftest", action="store_true", help="scripted replies, no model")
    ap.add_argument("--retries", type=int, default=1, help="retries per rung with the rejection reason (default 1)")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    rungs = endpoints_from_env()
    from types import SimpleNamespace
    task = SimpleNamespace(NAME=NAME, build_messages=build_messages, check=check)
    done = 0
    for item in ITEMS:
        r = run_item(task, item, rungs, retries=a.retries)
        done += r.status == "done"
        print(f"{item['id']}: {r.status:6s} rung={r.rung or '-':9s} calls={len(r.attempts)} "
              f"{decide(r.parsed) if r.parsed else r.why}")
    print(f"{done}/{len(ITEMS)} accepted")
    return 0 if done == len(ITEMS) else 1


if __name__ == "__main__":
    sys.exit(main())
