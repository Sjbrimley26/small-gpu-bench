#!/usr/bin/env python3
"""A mock OpenAI-compatible server for smoke-testing the scripts in this repo (stdlib only).

It answers POST /v1/chat/completions (and GET /v1/models) with canned, prompt-shaped replies so that every
script can run end to end WITHOUT a model or a GPU. It is not a model: the replies are chosen by looking at
the prompt, and they are mostly right so the graders have something to grade. Scores against it mean
nothing; a run that completes means the plumbing works.

Behaviour worth knowing:
  * a model name containing "small" gets deliberately WRONG answers to the escalate example task, so the
    retry and escalation path is exercised (model "mock-big" answers correctly);
  * `tools` in the request: the reply is a native tool_calls entry (`terminal`, or `tool_call` with --meta
    to imitate the wide-toolset failure);
  * a reply that contains `<think>` is never produced.

  python3 mock/mock_server.py [--port 8099] [--host 127.0.0.1] [--meta]
"""
import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

META = False
_SQL_GOLD = None


def _sql_gold():
    """question text -> gold SQL, read from the SQL-SYN fixture (the mock answers by lookup; it is not a model)."""
    global _SQL_GOLD
    if _SQL_GOLD is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures", "sql", "questions.json")
        with open(path, encoding="utf-8") as f:
            _SQL_GOLD = {q["question"]: q["gold"] for q in json.load(f)["questions"]}
    return _SQL_GOLD


def _redact(text):
    t = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "[EMAIL]", text)
    t = re.sub(r"(?:\(\d{3}\)\s?|\b\d{3}[\s.-])\d{3}[\s.-]\d{4}\b", "[PHONE]", t)
    t = re.sub(r"\b\d{2}-\d{7}\b", "[TAXID]", t)
    t = re.sub(r"\b\d{4}(?: \d{4}){1,3}(?: \d{1,4})?\b|\b\d{9,17}\b", "[ACCOUNT]", t)
    return t


def _source_sentences(user, n=3):
    """First n sentences with a digit from the SOURCE text in the prompt, so canned output is verifiable."""
    body = re.split(r"SOURCE[^\n]*:\n|SECTION \d+ of \d+:\n\n", user, maxsplit=1)[-1]
    body = body.split("CLAIMS:", 1)[0]
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", re.sub(r"---[^\n]*---|##[^\n]*", " ", body)) if x.strip()]
    pick = [x for x in sents if re.search(r"\d", x) and len(x.split()) >= 5][:n]
    return pick or sents[:n]


def _claims(user):
    return re.findall(r"^(\d+)\. (.+)$", user.split("CLAIMS:", 1)[-1], re.M)


def reply_for(model, messages, tools):
    def text_of(c):                      # content may be a string or a list of typed parts (vision requests)
        return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    system = next((text_of(m["content"]) for m in messages if m["role"] == "system"), "")
    user = text_of(messages[-1]["content"]) if messages else ""
    first_user = next((text_of(m["content"]) for m in messages if m["role"] == "user"), "")
    if tools:
        names = [t["function"]["name"] for t in tools]
        pick = "tool_call" if (META and "tool_call" in names) else "terminal"
        return None, [{"id": "call_1", "type": "function",
                       "function": {"name": pick, "arguments": json.dumps({"command": "df -h /data"})}}]
    # --- summarize_verify roles
    if system.startswith("You read ONE section"):
        return json.dumps({"points": _source_sentences(user, 2)}), None
    if system.startswith("You condense a GROUP"):
        return json.dumps({"notes": _source_sentences(user, 2)}), None
    if system.startswith("You write a summary of a document"):
        return json.dumps({"overview": (_source_sentences(user, 1) or ["No source."])[0],
                           "bullets": _source_sentences(user, 3), "actions": []}), None
    if system.startswith("You fix a summary"):
        return json.dumps({"overview": (_source_sentences(user, 1) or ["No source."])[0],
                           "bullets": _source_sentences(user, 3), "actions": []}), None
    if system.startswith("You are a strict fact-checker"):
        cl = _claims(user)
        # a checker that supports a claim only if the claim's own words appear in the source it was shown
        src = re.sub(r"\s+", " ", user.split("CLAIMS:", 1)[0]).lower()
        out = []
        for n, c in cl:
            ok = re.sub(r"\s+", " ", c).lower().rstrip(".") in src
            out.append({"n": int(n), "label": "S" if ok else "U", "quote": c if ok else ""})
        return json.dumps(out), None
    # --- escalate example task (synthetic ads)
    if "You extract facts from one classified ad" in system:
        m = re.search(r"Price field: (\d+)", user)
        price = int(m.group(1)) if m else None
        if "small" in model:                                   # wrong on purpose: breaks the price cross-check
            price = (price or 0) * 10 + 7
        kind = "laptop" if "aptop" in user else "bike" if "bike" in user.lower() else "sofa" if "sofa" in user.lower() else "other"
        flags = ["gift card payment"] if "gift card" in user else []
        return json.dumps({"price": price, "item": kind, "condition": "good", "pickup_only": "ickup" in user,
                           "red_flags": flags}), None
    # --- tests
    if system.startswith("You are a SQLite expert"):      # SQL-SYN: gold by lookup; "small" is wrong; every 5th reply adds prose, every 7th a fence
        qtext = re.search(r"QUESTION: (.*)\nSQL:", user, re.S).group(1)
        if "small" in model:
            return "SELECT 1", None
        sqltext = _sql_gold().get(qtext, "SELECT 1")
        n = sum(map(ord, qtext))
        if n % 5 == 0:
            return sqltext + "\n\nThis query answers the question.", None
        if n % 7 == 0:
            return "```sql\n" + sqltext + "\n```", None
        return sqltext, None
    if system.startswith("You extract facts from one rental listing"):   # RENT: read the structured fields, guess the rest
        price = re.search(r"Price field: \$?([\d,]+)", user)
        attrs = re.search(r"Attributes: (.*)", user).group(1)
        br, ba = re.search(r"(\d+)BR", attrs), re.search(r"([\d.]+)Ba", attrs)
        loc = re.search(r"Location: (.*)", user).group(1)
        low = user.lower()
        kind = next((k for k in ("mobile home", "townhouse", "duplex", "condo", "apartment", "room") if k in low), "house")
        pets = "dogs ok" if "dogs are ok" in attrs.lower() else "cats only" if "cats are ok" in attrs.lower() else "unclear"
        if "small" in model:                                   # wrong on purpose: breaks the BR cross-check
            br = type("M", (), {"group": lambda self, i: "9"})()
        flags = [f for k, f in (("gift card", "gift card"), ("wire transfer", "wire payment"), ("overseas", "owner abroad"),
                                ("no dogs", "no dogs"), ("no viewings", "no viewing")) if k in low]
        return json.dumps({"rent": int(price.group(1).replace(",", "")) if price else None, "beds": float(br.group(1)) if br else None,
                           "baths": float(ba.group(1)) if ba else None, "kind": kind, "pets": pets, "town": loc, "red_flags": flags}), None
    if system.startswith("You assign one bank-statement payee"):   # CAT: keyword guess, last business word wins
        payee = re.search(r"PAYEE: (.*)", user).group(1)
        kw = [("PAYROLL", "Income"), ("INTEREST PAID", "Income"), ("ATM", "Transfers & Cash"), ("TRANSFER", "Transfers & Cash"),
              ("MARKET", "Groceries"), ("GROC", "Groceries"), ("CAFE", "Dining Out"), ("BAKERY", "Dining Out"), ("GRILL", "Dining Out"),
              ("FUEL", "Fuel"), ("ELEC", "Utilities"), ("INSURANCE", "Insurance"), ("PHARMACY", "Health & Pharmacy"),
              ("DENTAL", "Health & Pharmacy"), ("HARDWARE", "Home & Hardware"), ("LUMBER", "Home & Hardware"),
              ("STREAMING", "Software & Streaming"), ("SOFTWARE", "Software & Streaming"), ("CINEMA", "Entertainment"),
              ("AUTO REPAIR", "Auto Service"), ("INN", "Travel")]
        hit = min(((payee.rfind(k), c) for k, c in kw if k in payee), default=None, key=lambda x: -x[0])
        ans = "Groceries" if "small" in model else (hit[1] if hit else "UNSURE")
        return json.dumps({"category": ans}), None
    if "Read this document page" in first_user:           # OCR (the mock cannot see; it returns the planted values)
        return "TITLE: 88213\nPO: 4471\nDATE: 2026-09-14\nTOTAL: 1345.99\nVENDOR: ACME DOOR AND TRIM SUPPLY", None
    if "sanitizes business documents" in system:           # RED
        return _redact(user.split("DOCUMENT:\n", 1)[-1]), None
    if "validates summaries of video transcripts" in system:   # JUDGE / CRIT
        s = user.split("SUMMARY:\n", 1)[-1]
        bad = re.search(r"Northfield|5090|19B|sourdough|best result", s)
        return json.dumps({"problems": ["Northfield, 5090 and 19B do not match the transcript"] if bad else [],
                           "score": 2 if bad else 4}), None
    if "inconsistencies or errors in the figures" in first_user:   # AUD
        if "Harlan" in first_user or "Northgate" in first_user:
            return ("1. Northgate Millworks is paid at net 30 in April and net 45 in September.\n"
                    "2. The hinge pallets 120 + 85 + 95 = 300, not 330.\n3. Ordered 22 July but arrived 14 July."), None
        return ("1. TMB-8FT is 12.40 in March but 14.10 in June.\n"
                "2. Freight: 4 x 285.00 = 1,140, but invoiced at 1,420."), None
    if "contractor buys 14 door slabs" in first_user:      # MON
        return "14 x 46.75 = 654.50; 9 x 12.40 = 111.60; subtotal 766.10; tax 55.54.\nTOTAL: $821.64", None
    if "'ANSWER: <value>'" in first_user:                   # HRD (not correct on purpose: mock is not a model)
        return "Working...\nANSWER: CANNOT BE DETERMINED", None
    if first_user.startswith("Below is a document."):       # SUM single pass
        return ("Kestrel-9B (Lumen Labs) is a 9B total / 2.1B active model. Snake game: passed on the first try. "
                "CSV dedupe kept 4,212 rows. The multi-file C++ project failed. The long-context test (14,000 "
                "tokens) was answered correctly by Dana. Good for small scripts and reading, with a license plus."), None
    return "OK", None


class H(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - silence request logging
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send({"object": "list", "data": [{"id": "mock-small"}, {"id": "mock-big"}]})
        else:
            self._send({"status": "ok"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send({"error": "bad json"}, 400)
        if not self.path.endswith("/chat/completions"):
            return self._send({"error": "not found"}, 404)
        content, tool_calls = reply_for(body.get("model", ""), body.get("messages") or [], body.get("tools"))
        msg = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        chars = sum(len(m["content"]) if isinstance(m.get("content"), str) else 100 for m in body.get("messages") or [])
        self._send({"id": "mock", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
                    "usage": {"prompt_tokens": chars // 4, "completion_tokens": max(1, len(content or "") // 4)}})


def main():
    global META
    ap = argparse.ArgumentParser(description="Mock OpenAI-compatible server for smoke tests.")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--meta", action="store_true", help="pick the tool_call meta-dispatcher when it is offered")
    a = ap.parse_args()
    META = a.meta
    srv = ThreadingHTTPServer((a.host, a.port), H)
    print("mock server on %s:%d" % (a.host, srv.server_address[1]), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
