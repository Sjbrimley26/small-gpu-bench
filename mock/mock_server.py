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
import hashlib
import json
import math
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

META = False
_SQL_GOLD = None
EMBED_DIM = 1024


def fake_vector(text, dim=EMBED_DIM):
    """Deterministic unit vector from the text's sha256. Same text -> identical vector."""
    out, counter = [], 0
    while len(out) < dim:
        d = hashlib.sha256(("%d:%s" % (counter, text)).encode("utf-8", "replace")).digest()
        for i in range(0, len(d), 2):
            if len(out) == dim:
                break
            out.append((int.from_bytes(d[i:i + 2], "big") / 32767.5) - 1.0)
        counter += 1
    norm = math.sqrt(sum(v * v for v in out)) or 1.0
    return [round(v / norm, 6) for v in out]


def _sql_gold():
    """question text -> gold SQL, read from the SQL-SYN fixture (the mock answers by lookup; it is not a model)."""
    global _SQL_GOLD
    if _SQL_GOLD is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures", "sql", "questions.json")
        with open(path, encoding="utf-8") as f:
            _SQL_GOLD = {q["question"]: q["gold"] for q in json.load(f)["questions"]}
    return _SQL_GOLD


def _aux():
    """tests/aux_lanes.py, so the mock can answer the AUX cases by lookup (it is not a model)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
    import aux_lanes
    return aux_lanes


def _aux_reply(model, system, user):
    """Canned AUX answers: right by lookup; a model named \"small\" approves everything, never gets the
    judge/triage contract right, and answers instead of titling."""
    al = _aux()
    small = "small" in model
    if system.startswith("You are a security reviewer"):
        cmd = re.search(r"<command>\n(.*)\n</command>", user, re.S).group(1)
        gold = next((g for _, _, c, g, _ in al.APPROVAL if al.strip_shell_comments(c) == cmd), "escalate")
        return "APPROVE" if small else gold.upper()
    if system.startswith("You are a strict judge"):
        goal = re.search(r"Goal:\n(.*?)\n\nAgent's most recent", user, re.S).group(1)
        resp = re.search(r"most recent response:\n(.*?)\n\n(?:Background|Current time)", user, re.S).group(1)
        gold = next((g for _, gl, r, _, g in al.GOAL if gl == goal and r == resp), "continue")
        if small:
            return "I think the agent is done."
        extra = ', "wait_for_seconds": 60' if gold == "wait" else ""
        return '{"verdict": "%s"%s, "reason": "canned"}' % (gold, extra)
    if system.startswith("You are the Kanban triage specifier"):
        if small:
            return '{"title": "x", "body": ["not", "a", "string"]}'
        return json.dumps({"title": "Do the task", "body": "**Goal** done\n**Approach** - steps\n**Acceptance criteria** - [ ] ok"})
    if system.startswith("You name chat sessions"):
        if small:
            return "Sure, here is how you can do that."
        words = re.sub(r"[^\w\s-]", " ", user).split()[:5]
        return json.dumps({"title": " ".join(["Help"] + words[:4])})
    return None


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
    aux = _aux_reply(model, system, user)
    if aux is not None:
        return aux, None
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
    if first_user.startswith("Extract the purchase order below") and '"class_code"' in first_user:
        # PO-REAL: gold by construction from the committed fixture; "small" drops lines and rounds the total
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
        import po_real
        _, gold = po_real.load()
        if "small" in model:
            gold = json.loads(json.dumps(gold))
            gold["line_items"] = gold["line_items"][:9]
            gold["total"] = round(gold["total"] - 3, 2)
        return json.dumps(gold), None
    if first_user.startswith("Extract the purchase order below"):    # PO-SYN: gold by construction; "small" is wrong
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
        import po
        _, gold = po.build()
        if "small" in model:                     # drop three items, inflate the total, wrap it in prose
            gold = json.loads(json.dumps(gold))
            gold["line_items"] = gold["line_items"][:5]
            gold["total"] = round(gold["total"] + 40, 2)
            return "Here is the data: " + json.dumps(gold), None
        return json.dumps(gold), None
    if system.startswith("You answer questions using only the NOTE CONTEXT") and "13 CFR 125.6" in user or (
            system.startswith("You answer questions using only the NOTE CONTEXT") and "NIST SP 800-63B" in user):
        # RAGQ-REAL: canned answers by question; "small" repeats the superseded value where there is a pair
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
        import ragq_real
        q = next((x for x in ragq_real.QUESTIONS if x["question"] in user), None)
        if q is None:
            return "The context does not record that.", None
        small = "small" in model
        good = {
            "E1": "Services contracts may pay at most 50% to firms that are not similarly situated.",
            "E2": "Verifiers and CSPs should permit at least 64 characters.",
            "F1": "For a multi-agency set-aside, the ordering agency uses the period of performance for each order.",
            "F2": "15 characters when the password is a single factor; 8 is still permitted for multi-factor use.",
            "N1": "The documents do not state a size standard for NAICS 236220.",
            "N2": "No password manager product is recommended; the documents only require that managers be allowed.",
            "S1": "Services 50%, general construction 85%, special trade construction 75%.",
            "S2": "Single-factor passwords went from 8 to 15 characters and composition rules are not imposed.",
            "V1": "At most 50% of a services contract may go to non-similarly-situated firms (85% general construction, 75% special trade).",
            "V2": "15 characters for a single-factor password, 8 when it is part of multi-factor authentication.",
        }
        stale = {
            "E1": "Services contracts may pay at most 50% to firms that are not similarly situated.",
            "E2": "Verifiers should permit at least 64 characters.",
            "F1": "Compliance is measured over the base term and then each subsequent option period.",
            "F2": "8 characters is the minimum password length.",
            "N1": "The size standard is $30 million in average annual receipts.",
            "N2": "NIST recommends Bitwarden.",
            "S1": "Services 50% and general construction 85%.",
            "S2": "The minimum password length is 8 characters.",
            "V1": "The prime must keep at least half the work.",
            "V2": "8 characters.",
        }
        return (stale if small else good).get(q["qid"], "The context does not record that."), None
    if system.startswith("You answer questions using only the NOTE CONTEXT"):
        # RAGQ-SYN: canned answers by question; a model named "small" repeats stale values and invents
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
        import ragq
        q = next((x for x in ragq.QUESTIONS if x["question"] in user), None)
        if q is None:
            return "The context does not record that.", None
        small = "small" in model
        canned = {
            "E1": "2% discount if the invoice is paid within 10 days.",
            "E2": "45 per month.",
            "F1": "It holds 25 minutes at cone 6.",
            "F2": "0.6 mm brass.",
            "N1": "Kiln rental is $80 a day with a $200 deposit.",
            "N2": "The second kiln was bought in 2021.",
            "S1": "Element 3 measured 9.8 ohms and cone 6 holds 12 minutes.",
            "S2": "A day pass is 12, so four visits cost 48, more than the 45 membership.",
            "V1": "12 per day pass.",
            "V2": "12 minutes.",
        }
        good = {
            "E1": "A 2% discount applies if paid within 10 days.",
            "E2": "A student pays 30 per month.",
            "F1": "The current schedule holds 12 minutes at cone 6.",
            "F2": "0.4 mm hardened steel.",
            "N1": "The documents do not mention kiln rental or a deposit.",
            "N2": "The documents do not record a second kiln.",
            "S1": "Element 3 measured 9.8 ohms; cone 6 holds 12 minutes.",
            "S2": "A day pass is 12, so four visits cost 48, more than the 45 membership.",
            "V1": "12 per day pass.",
            "V2": "12 minutes.",
        }
        return (canned if small else good).get(q["qid"], "The context does not record that."), None
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

    def _fake_timings(self, prompt_tokens, predicted):
        """llama.cpp-shaped timings so a client that reads them has something consistent to read."""
        return {"prompt_n": prompt_tokens, "prompt_ms": prompt_tokens * 2.0,
                "predicted_n": predicted, "predicted_ms": predicted * 25.0,
                "predicted_per_second": 1000.0 / 25.0,
                "prompt_per_second": 1000.0 / 2.0}

    def _embeddings(self, body):
        """Deterministic pseudo-vector per text: same text -> same vector, different text -> different."""
        inp = body.get("input")
        items = inp if isinstance(inp, list) else [inp]
        out, toks = [], 0
        for i, text in enumerate(items):
            text = text if isinstance(text, str) else json.dumps(text)
            toks += max(1, len(text) // 4)
            out.append({"object": "embedding", "index": i, "embedding": fake_vector(text)})
        self._send({"object": "list", "model": body.get("model") or "mock-embed", "data": out,
                    "usage": {"prompt_tokens": toks, "total_tokens": toks}})

    def _completion(self, body):
        prompt = body.get("prompt") or ""
        if isinstance(prompt, list):
            prompt = "".join(prompt)
        text = " mock completion"
        n = max(1, len(text) // 4)
        self._send({"id": "mock", "object": "text_completion", "model": body.get("model"),
                    "choices": [{"index": 0, "text": text, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": n},
                    "timings": self._fake_timings(max(1, len(prompt) // 4), n)})

    def _infill(self, body):
        """Answers with the rest of the first line of the prefix, which is enough to exercise the plumbing."""
        prefix = body.get("input_prefix") or body.get("prompt") or ""
        last = prefix.split("\n")[-1]
        text = "mock-" + (last.strip().split()[-1][:6] if last.strip() else "infill")
        n = max(1, len(text) // 4)
        self._send({"content": text, "model": body.get("model"),
                    "tokens_predicted": n, "tokens_evaluated": max(1, len(prefix) // 4),
                    "stop": True, "timings": self._fake_timings(max(1, len(prefix) // 4), n)})

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
        path = self.path.rstrip("/")
        if path.endswith("/embeddings"):
            return self._embeddings(body)
        if path.endswith("/completions") and not path.endswith("/chat/completions"):
            return self._completion(body)
        if path.endswith("/infill"):
            return self._infill(body)
        if not path.endswith("/chat/completions"):
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
