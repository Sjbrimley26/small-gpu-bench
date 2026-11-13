#!/usr/bin/env python3
"""summarize_verify: a local summarize-verify pipeline (stdlib only).

extract -> write -> per-claim check -> repair once.  Adapted from the `summarize` tool whose design and
measurements are in the lab notebook (sections 96, 96.1, 96.2, 97 and 104).

Code drives the loop; the model only extracts, writes and answers closed questions:
  1. chunk the source on structure and extract a checklist of facts per chunk (coverage floor);
  2. write the summary once, with the checklist in view;
  3. check every claim in batches: the checker answers S/U/C and must give a quote, and CODE verifies the
     quote is (mostly) a verbatim substring of the source and that every number in the claim occurs in it;
  4. test every checklist point against the summary (omissions are what judges miss);
  5. repair once, re-check, and keep whatever still fails, marked [unverified].

When to use it: the measured result (notebook 97, 104) is that on a document that fits one context window a
plain single pass covered MORE facts and took ~1/5 of the time. The pipeline earns its cost on documents
too big for one pass, and as a source of code-side checks (unsupported numbers, quote presence).
The claim checker also flagged about half of a clean summary, so treat [unverified] as "look here",
not as "wrong".

Configuration (environment, all optional; flags override):
  BASE_URL        OpenAI-compatible server root         default http://localhost:8080
  MODEL           model for every role unless overridden
  WRITER_MODEL    model for extract + write + repair    default $MODEL
  CHECK_MODEL     model for the claim checker           default $MODEL
  API_KEY         bearer token if the server needs one

  python3 summarize_verify.py --selftest             code-side checks, no model
  python3 summarize_verify.py FILE [--single]        summarize a text file
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
from pathlib import Path

# Sizes were tuned for a 16k-token seat on a 4 GB card; raise them for a bigger context.
CHUNK_CHARS = 7000          # ~1.8k tokens of source per chunk: leaves room for the checklist in a 16k seat
OVERLAP_CHARS = 400
MAX_WRITE_WORDS = 800       # ceiling for the rendered summary; 10% of the source is the target (floor 60)
WRITE_FULL_CHARS = 40000    # send the whole document to the writer while it fits a 16k seat (16k ctx ~ 40k chars)
VERIFY_MAX_CHARS = 24000    # past this, claims are checked against the few sections they match, not the whole doc
MIN_WORDS = 60
SINGLE_MAX_CHARS = 60000    # --single: what the 40k-token seat takes after the prompt and 2.4k output (35k chars measured at ~9k tokens)

SINGLE_PROMPT = ("Below is a document. Write a faithful summary of the whole thing for someone who has not read it: "
                 "what it is, each main point or event and how it turned out (say plainly when something failed), "
                 "and any conclusion or verdict. Use short sections or bullets, and list action items or deadlines if "
                 "there are any. Keep every name, number and date exactly as written. Use only what the document says; "
                 "do not add facts or numbers that are not in it, and do not repeat yourself.\n\nDOCUMENT:\n")

# ---------------------------------------------------------------- text in

TS = re.compile(r"^\s*(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{3})?\s*-->.*$|^\s*\d+:\d{2}\s")
VTTCUE = re.compile(r"^(?:WEBVTT.*|NOTE .*|Kind:.*|Language:.*|\d+|[\d:.,]+\s*-->.*)$")


def clean_srt(text: str) -> str:
    """Cue timing/indices out, consecutive duplicate lines collapsed, one paragraph per ~12 lines."""
    out, prev, buf = [], None, []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or VTTCUE.match(s) or TS.match(ln):
            continue
        s = re.sub(r"<[^>]+>", "", s)                      # inline karaoke tags
        if s == prev:
            continue
        prev = s
        buf.append(s)
        if len(buf) >= 12:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return "\n\n".join(out)


# ---------------------------------------------------------------- chunking

def chunk_text(text: str, max_chars: int = CHUNK_CHARS) -> list[dict]:
    """Split on headings first, then blank-line blocks, then hard-wrap. Small overlap between chunks.
    Returns [{"text":..., "n":i}] with 1-based n."""
    text = text.replace("\r\n", "\n").strip()
    blocks, buf = [], []
    for ln in text.split("\n"):
        is_head = bool(re.match(r"^\s{0,3}(#{1,6}\s|={3,}$|-{3,}$)", ln))
        if is_head and len("\n".join(buf)) > 200:
            blocks.append("\n".join(buf)); buf = []
        buf.append(ln)
        if not ln.strip() and len("\n".join(buf)) > max_chars // 2:
            blocks.append("\n".join(buf)); buf = []
    if "\n".join(buf).strip():
        blocks.append("\n".join(buf))

    def _overlap(cur: str) -> str:
        """Tail of the previous chunk for context, trimmed forward to a word/sentence boundary."""
        tail = cur[-OVERLAP_CHARS:]
        for sep in (". ", "\n", " "):
            i = tail.find(sep)
            if 0 <= i < len(tail) - 1:
                return tail[i + len(sep):]
        return ""

    chunks, cur = [], ""
    for b in blocks:
        is_head = bool(re.match(r"^\s{0,3}#{1,6}\s", b))
        while len(b) > max_chars:                        # a single huge block: hard-wrap on sentence ends
            cut = b.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 2 else max_chars
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(b[:cut]); b = b[cut:]
        # a heading starts a new chunk unless the current one is still short (keeps sections intact)
        if cur and (len(cur) + len(b) > max_chars or (is_head and len(cur) > max_chars // 3)):
            chunks.append(cur)
            cur = _overlap(cur)
        cur = (cur + "\n" + b).strip() if cur else b
    if cur.strip():
        chunks.append(cur)
    out = [{"n": i + 1, "text": c.strip()} for i, c in enumerate(chunks) if c.strip()]
    return out or [{"n": 1, "text": text}]


# ---------------------------------------------------------------- code-side checks

def split_claims(text: str) -> list[str]:
    """Deterministic claim splitter (same rule the lab’s claim splitter uses)."""
    out = []
    for line in text.splitlines():
        s = re.sub(r"^\s*(?:[*\-\u2022]+|\d+[.)])\s+", "", line).strip()
        s = re.sub(r"\*\*|__|`", "", s)
        if not s:
            continue
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z\"])", s):
            part = part.strip()
            if len(part.split()) >= 5 and not part.endswith(":"):
                out.append(part)
    return out


def norm_words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower().replace("&gt;", " ")).split()


def quote_ok(quote: str, source_norm: str) -> bool:
    """A quote counts only if >=50% of its word 4-grams occur verbatim in the source (paraphrase tolerance
    identical to the faithfulness harness that produced the SUM numbers)."""
    w = norm_words(quote)
    g = [" ".join(w[i:i + 4]) for i in range(len(w) - 3)]
    return bool(g) and sum((" " + x + " ") in source_norm for x in g) / len(g) >= 0.5


STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "was", "were", "it",
        "its", "as", "at", "by", "that", "this", "from", "be", "been", "has", "have", "had", "not", "but", "his",
        "her", "their", "they", "he", "she", "we", "you", "i", "s", "t", "which", "who", "when", "then", "than"}


def content_tokens(s: str) -> set[str]:
    return {w for w in norm_words(s) if len(w) > 2 and w not in STOP}


def covered(point: str, summary_text: str) -> bool:
    """A checklist point counts as covered when half its content words appear in the summary."""
    p = content_tokens(point)
    if not p:
        return True
    s = content_tokens(summary_text)
    return len(p & s) / len(p) >= 0.5


def dedupe_points(groups: list[dict]) -> list[dict]:
    """Drop a point whose content words are >=60% shared with one already kept (Jaccard), across sections."""
    kept: list[set] = []
    for g in groups:
        out = []
        for p in g["points"]:
            t = content_tokens(p)
            if not t:
                continue
            if any(len(t & k) / max(1, len(t | k)) >= 0.6 for k in kept):
                continue
            kept.append(t)
            out.append(p)
        g["points"] = out
    return groups


def unsupported_numbers(summary: str, source: str) -> list[str]:
    def nums(t):
        return set(re.findall(r"\d+(?:[.,]\d+)?", t))
    return sorted(nums(summary) - nums(source), key=lambda x: float(x.replace(",", "")) if x.replace(",", "").replace(".", "").isdigit() else 0)


# ---------------------------------------------------------------- prompts

EXTRACT_SYS = ("""You read ONE section of a document and list the facts a summary must not lose. Reply with ONE JSON
object and nothing else: {"points": ["...", "..."]}
Rules: 6-9 points for a long section, 3-4 for a short one. One fact per point: do not merge two facts
(numbers, names, verdicts) into one point. Each point is one short sentence (max 25 words) stating
what the section actually says: names, numbers, prices, dates, decisions, failures, verdicts. Copy numbers exactly
as written. If the section is boilerplate (headers, page furniture, ads) reply {"points": []}.
Use ONLY the section text; never add a fact it does not contain.""")

WRITE_SYS = ("""You write a summary of a document for someone who has not read it, using only the source. Reply with
ONE JSON object and nothing else:
{"overview": "3-5 sentences saying what this document is and its bottom line",
 "bullets": ["key point", "key point", ...],
 "actions": [{"what": "the action", "who": "who (or \\"\\" if unstated)", "when": "date or deadline (or \\"\\")"}]}
Rules: write AT LEAST one bullet for every checklist point - a point left out is a failure - then add any
other important detail the source has. Use 5-9 words for numbers, names and verdicts exactly as the source has them. Say plainly when something failed,
was rejected, or is uncertain. Copy every number, price, date and name exactly as the source writes it. Never add a
fact, number or name that is not in the source. actions lists only work the document asks a person to do, or a
deadline someone was given; a date that is merely mentioned is NOT an action. Use [] when there is none. Keep bullets to one line each, no sub-bullets, no repeated points.""")

CHECK_SYS = ("""You are a strict fact-checker. Below are the SOURCE sections of a document and a numbered list of
CLAIMS taken from a summary of it. For each claim decide:
S = supported by the source, U = unsupported (not stated or implied), C = contradicted (the source says otherwise).
Judge the claim's content, not its wording; a faithful paraphrase is S. A claim that merges facts from two places
is S only if both are supported.
For every claim give a short VERBATIM quote copied from the source that supports or refutes it (use "..." to join
fragments). For U with no relevant text use an empty quote.
Reply with ONLY a JSON array: [{"n":1,"label":"S","quote":"..."}, ...] covering every claim.""")

NOTES_SYS = ("""You condense a GROUP of sections of a long document into notes another writer will summarize. Reply
with ONE JSON object: {"notes": ["...", "..."]}
Rules: 6-12 notes, one fact each, 15 words maximum. Keep every number, name, date, price and verdict exactly as
written, say plainly what failed or was rejected, and drop page furniture, references and repetition.
Use only the text; never add a fact it does not contain.""")

REPAIR_SYS = ("""You fix a summary that failed fact-checking. Reply with the SAME JSON shape as before:
{"overview": "...", "bullets": ["..."], "actions": [{"what": "...", "who": "...", "when": "..."}]}
Rules: rewrite or delete every claim listed as unsupported or contradicted, and add every missing point, using only
the source. Keep everything that already passed, unchanged where possible. Copy numbers and names exactly as the
source writes them. Never add a fact that is not in the source.""")


def _json_obj(text: str) -> dict | None:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0), strict=False)
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _json_list(text: str) -> list | None:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0), strict=False)
    except Exception:
        return None
    return d if isinstance(d, list) else None


def parse_summary(reply: str) -> dict | None:
    d = _json_obj(reply)
    if not d:
        return None
    ov = d.get("overview")
    bl = d.get("bullets")
    ac = d.get("actions")
    if not isinstance(ov, str) or not isinstance(bl, list):
        return None
    ac = ac if isinstance(ac, list) else []
    clean = []
    for a in ac:
        if isinstance(a, str) and a.strip():
            clean.append({"what": a.strip(), "who": "", "when": ""})
        elif isinstance(a, dict) and str(a.get("what", "")).strip():
            clean.append({"what": str(a["what"]).strip(), "who": str(a.get("who") or "").strip(),
                          "when": str(a.get("when") or "").strip()})
    return {"overview": ov.strip(), "bullets": [str(b).strip() for b in bl if str(b).strip()], "actions": clean}


def render(s: dict, flags: dict[int, str]) -> str:
    """Markdown: overview, bullets, actions, then the count footer. flags maps claim number -> tag."""
    lines = [s["overview"].strip(), ""]
    for b in s["bullets"]:
        lines.append(f"- {b}")
    if s["actions"]:
        lines += ["", "## Action items", ""]
        for a in s["actions"]:
            bits = [a["what"]]
            if a["who"]:
                bits.append(f"({a['who']})")
            if a["when"]:
                bits.append(f"- due {a['when']}")
            lines.append("- " + " ".join(bits))
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- the pipeline

class Pipeline:
    def __init__(self, chat, seat_names: dict | None = None, verbose: bool = True):
        """chat(role, messages) -> reply text. role is "writer" or "check"; the callable maps a role to a model
        (see make_chat). Pass seat_names={"writer": ..., "check": ...} to label roles in the call log."""
        self.chat = chat
        self.seats = {"writer": "writer", "check": "check", **(seat_names or {})}
        self.verbose = verbose
        self.calls = []
        self.t0 = time.time()

    def say(self, *a, **kw):
        if self.verbose:
            print(*a, file=sys.stderr, flush=True, **kw)

    def _call(self, role: str, system: str, user: str, cap: int = 1600) -> str:   # cap: informational; make_chat sets max_tokens
        seat = self.seats[role]
        t = time.time()
        self.say(f"  [{role}:{seat}] {len(user)} chars ...", end=" ")
        out = self.chat(role, [{"role": "system", "content": system}, {"role": "user", "content": user}])
        self.calls.append({"role": role, "seat": seat, "s": round(time.time() - t, 1), "out": len(out)})
        self.say(f"{round(time.time() - t, 1)}s")
        return out

    # 1. extraction: the coverage checklist
    def checklist(self, chunks: list[dict]) -> list[dict]:
        out = []
        for c in chunks:
            r = self._call("writer", EXTRACT_SYS, f"SECTION {c['n']} of {len(chunks)}:\n\n{c['text']}", 700)
            d = _json_obj(r) or {}
            pts = [str(p).strip() for p in (d.get("points") or []) if str(p).strip()]
            out.append({"n": c["n"], "points": pts})
            self.say(f"    section {c['n']}: {len(pts)} points")
        out = dedupe_points(out)
        n = sum(len(g["points"]) for g in out)
        self.say(f"  checklist: {n} distinct point(s) over {len(out)} section(s)")
        return out

    def group_notes(self, chunks: list[dict], checklists: list[dict], size: int = 4) -> list[str]:
        """Long documents: condense every `size` sections with their text in view, then the writer summarises the
        notes. A flat 100-point checklist is not a summary input (measured: 114/119 points lost)."""
        by_n = {c["n"]: c for c in chunks}
        pts = {c["n"]: c["points"] for c in checklists}
        out = []
        for i in range(0, len(chunks), size):
            group = [c["n"] for c in chunks[i:i + size]]
            text = "\n\n".join(f"--- SECTION {n} ---\n{by_n[n]['text']}" for n in group if n in by_n)
            points = "\n".join(f"- {p}" for n in group for p in pts.get(n, []))
            user = f"SECTIONS {group[0]}-{group[-1]} of {len(chunks)}\nPOINTS ALREADY EXTRACTED:\n{points}\n\n{text}"
            r = self._call("writer", NOTES_SYS, user, 900)
            d = _json_obj(r) or {}
            ns = [str(x).strip() for x in (d.get("notes") or []) if str(x).strip()]
            if not ns:      # the model ignored the shape: fall back to this group's checklist points
                ns = [p for n in group for p in pts.get(n, [])]
            out += ns[:14]
            self.say(f"    group {i // size + 1}: {len(ns)} notes")
        return out

    def write(self, chunks: list[dict], checklists: list[dict], notes: str = "") -> dict | None:
        target = max(MIN_WORDS, min(MAX_WRITE_WORDS, len(" ".join(c["text"] for c in chunks)) // 10 // 6))
        pts = "\n".join(f"- [s{c['n']}] {p}" for c in checklists for p in c["points"]) or "- (none extracted)"
        total = sum(len(c["text"]) for c in chunks)
        if total <= WRITE_FULL_CHARS:      # the whole document fits: give the writer everything it has
            body = "\n\n".join(f"--- SECTION {c['n']} ---\n{c['text']}" for c in chunks)
            src_label = "SOURCE (the whole document)"
        else:                              # long document: two-level, section notes first, then write from those
            notes_tree = self.group_notes(chunks, checklists)
            body = "\n".join(f"- {n}" for n in notes_tree) or "(none)"
            src_label = ("SOURCE: notes taken group by group through the whole document (it is too long to send "
                         "whole). Write only from these notes; do not guess at anything they do not say.")
        user = (f"CHECKLIST (coverage floor, from all {len(chunks)} sections):\n{pts}\n\n"
                f"TARGET LENGTH: {target} words (do not go below {int(target * 0.8)}).\n{notes}\n\n"
                f"{src_label}:\n{body}")
        r = self._call("writer", WRITE_SYS, user, 2000)
        s = parse_summary(r)
        if s is None:      # one retry with the shape spelled out again: small models drop the wrapper
            r = self._call("writer", WRITE_SYS + "\nYour last reply was not the required JSON object.",
                           user, 2000)
            s = parse_summary(r)
        return s

    # 2. verification: one batched closed question per group of claims, quote checked in code
    def verify(self, claims: list[str], chunks: list[dict], src_norm: str, src_text: str = "") -> list[dict]:
        if not claims:
            return []
        body = "\n\n".join(f"--- SECTION {c['n']} ---\n{c['text']}" for c in chunks)
        if len(body) > VERIFY_MAX_CHARS:   # long document: send only the sections these claims actually touch
            want = content_tokens(" ".join(claims))
            scored = sorted(chunks, key=lambda c: -len(content_tokens(c["text"]) & want))[:4]
            scored.sort(key=lambda c: c["n"])
            body = "\n\n".join(f"--- SECTION {c['n']} ---\n{c['text']}" for c in scored)
            self.say(f"    (claims checked against sections {[c['n'] for c in scored]})")
        if len(claims) > 40:               # keep one prompt to a sane size; results are renumbered below
            out = []
            for i in range(0, len(claims), 40):
                part = claims[i:i + 40]
                out += [{**v, "n": v["n"] + i} for v in self.verify(part, chunks, src_norm, src_text)]
            return out
        listing = "\n".join(f"{i+1}. {c}" for i, c in enumerate(claims))
        r = self._call("check", CHECK_SYS, f"SOURCE:\n{body}\n\nCLAIMS:\n{listing}", 2400)
        arr = _json_list(r)
        if arr is None:
            r = self._call("check", CHECK_SYS + "\nReply with only the JSON array, nothing else.",
                           f"SOURCE:\n{body}\n\nCLAIMS:\n{listing}", 2400)
            arr = _json_list(r) or []
        got = {}
        for a in arr:
            if not isinstance(a, dict):
                continue
            try:
                n = int(a.get("n"))
            except Exception:
                continue
            lab = str(a.get("label", "")).strip().upper()[:1]
            got[n] = {"n": n, "label": lab if lab in ("S", "U", "C") else "U",
                      "quote": str(a.get("quote") or "").strip()}
        out = []
        for i, c in enumerate(claims, 1):
            g = got.get(i) or {"n": i, "label": "U", "quote": ""}
            q = g["quote"]
            ok = g["label"] == "S" and quote_ok(q, src_norm)
            why = "supported (quote verified)" if ok else (
                "quote not found in source" if g["label"] == "S" else
                "contradicted" if g["label"] == "C" else "unsupported")
            if ok:                                   # a number the source never contains cannot be supported
                bad_nums = unsupported_numbers(c, src_text)
                if bad_nums:
                    ok, why = False, f"number not in source: {', '.join(bad_nums[:3])}"
            out.append({**g, "claim": c, "ok": ok, "why": why})
        return out

    def run(self, text: str) -> dict:
        chunks = chunk_text(text)
        src_norm = " " + " ".join(norm_words(text)) + " "
        self.say(f"source: {len(text)} chars, {len(chunks)} chunk(s)")
        checklists = self.checklist(chunks)
        s = self.write(chunks, checklists)
        if s is None:
            return {"ok": False, "why": "writer never returned the required JSON", "calls": self.calls}
        rounds, verdicts, missing = [], [], []
        for rnd in (1, 2):
            claims = split_claims(render(s, {}))
            verdicts = self.verify(claims, chunks, src_norm, text)
            all_pts = [p for c in checklists for p in c["points"]]
            missing = [p for p in all_pts if not covered(p, render(s, {}))]
            bad = [v for v in verdicts if not v["ok"]]
            rounds.append({"round": rnd, "claims": len(claims), "flagged": len(bad), "missing": len(missing)})
            self.say(f"  round {rnd}: {len(claims) - len(bad)}/{len(claims)} claims verified, "
                     f"{len(missing)} checklist point(s) missing")
            if (not bad and not missing) or rnd == 2:
                break
            fix = (f"YOUR PREVIOUS SUMMARY WAS {len(render(s, {}).split())} WORDS. Keep at least that length and keep "
                   "every claim that passed; change only what failed.\n\n"
                   "CLAIMS THAT FAILED:\n" + ("\n".join(f"- {v['claim']}  [{v['why']}]" for v in bad) or "- (none)")
                   + "\n\nCHECKLIST POINTS THE SUMMARY LEFT OUT (add each one in a bullet):\n"
                   + ("\n".join(f"- {m}" for m in missing) or "- (none)"))
            s2 = self.write(chunks, checklists, notes="\n\n" + fix)
            if s2:
                s = s2
        # final verdicts for the text we actually ship
        final_claims = split_claims(render(s, {}))
        if [v["claim"] for v in verdicts] != final_claims:
            verdicts = self.verify(final_claims, chunks, src_norm, text)
        all_pts = [p for c in checklists for p in c["points"]]
        missing = [p for p in all_pts if not covered(p, render(s, {}))]
        flagged = [v for v in verdicts if not v["ok"]]
        # keep failing claims, marked [unverified] rather than silently dropping them
        marked = list(s["bullets"])
        for v in flagged:
            for i, b in enumerate(marked):
                if b.strip().rstrip(".") and content_tokens(b) and \
                        len(content_tokens(b) & content_tokens(v["claim"])) / max(1, len(content_tokens(b))) >= 0.6:
                    marked[i] = b.rstrip() + " **[unverified]**"
        body = render({**s, "bullets": marked}, {})
        words = len(body.split())
        footer = (f"_summarize-verify · {len(chunks)} chunk(s) · claims {len(verdicts) - len(flagged)}"
                  f"/{len(verdicts)} verified, {len(flagged)} flagged · checklist {len(all_pts) - len(missing)}"
                  f"/{len(all_pts)} covered · {words} words · {round(time.time() - self.t0)}s_")
        return {"ok": True, "body": body, "footer": footer, "markdown": body + "\n\n" + footer + "\n",
                "rounds": rounds, "claims": len(verdicts), "flagged": len(flagged),
                "flagged_claims": [v["claim"] for v in flagged], "missing_points": missing,
                "points": len(all_pts), "covered": len(all_pts) - len(missing),
                "words": words, "chunks": len(chunks), "calls": self.calls,
                "unsupported_numbers": unsupported_numbers(body, text)}


def single_pass(chat, text: str) -> dict:
    """One writer call, no extract/check/repair. Same result shape as Pipeline.run for the parts main() uses."""
    t0 = time.time()
    body = chat("writer", [{"role": "user", "content": SINGLE_PROMPT + text}]).strip()
    if not body:
        return {"ok": False, "why": "single pass returned nothing"}
    words = len(body.split())
    bad = unsupported_numbers(body, text)
    footer = (f"_single pass · unchecked · {words} words · "
              f"{len(bad)} number(s) not in source · {round(time.time() - t0)}s_")
    return {"ok": True, "body": body, "footer": footer, "markdown": body + "\n\n" + footer + "\n",
            "words": words, "unsupported_numbers": bad}



# ---------------------------------------------------------------- model plumbing (stdlib HTTP)

def make_chat(base_url: str | None = None, writer_model: str | None = None, check_model: str | None = None,
              api_key: str | None = None, timeout: float = 1800, max_tokens: int = 2400, think: bool | None = None):
    """-> chat(role, messages) -> reply text, for an OpenAI-compatible /v1/chat/completions server.

    role "writer" uses WRITER_MODEL (else MODEL); role "check" uses CHECK_MODEL (else MODEL). Reasoning is left
    to the server unless think=True/False is given (sent as chat_template_kwargs.enable_thinking)."""
    base = (base_url or os.environ.get("BASE_URL", "http://localhost:8080")).rstrip("/")
    url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    model = os.environ.get("MODEL", "")
    models = {"writer": writer_model or os.environ.get("WRITER_MODEL") or model,
              "check": check_model or os.environ.get("CHECK_MODEL") or model}
    key = api_key if api_key is not None else os.environ.get("API_KEY", "")

    def chat(role: str, messages: list[dict]) -> str:
        body = {"model": models[role] or "local", "messages": messages, "stream": False,
                "max_tokens": max_tokens, "temperature": 0.2}
        if think is not None:
            body["chat_template_kwargs"] = {"enable_thinking": think}
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SystemExit(f"cannot reach {base} as role {role!r}: {e}")
        content = (data["choices"][0]["message"].get("content") or "")
        return content.split("</think>", 1)[1].strip() if "</think>" in content else content.strip()

    return chat


# ---------------------------------------------------------------- selftest (no model)

def selftest() -> bool:
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    t = ("Title slide\n\nThis deck is about the Q3 widget programme. Northwind shipped 412 units in Q3, up from 380.\n\n"
         "## Pricing\n\nPart WX-100 moved to $18.40 per unit on 3/4/2026. Contoso declined to match.\n\n"
         "## Issues\n\nThe freight claim on invoice 44821 was rejected by the carrier.\n")
    ch = chunk_text(t)
    chk("small doc stays one chunk", len(ch) == 1 and "Pricing" in ch[0]["text"])
    big_doc = "".join(f"## Section {i}\n\n" + ("Detail about widgets and fittings. " * 60) + "\n\n" for i in range(8))
    chb = chunk_text(big_doc)
    chk("long doc splits into several chunks", len(chb) >= 3)
    chk("every section heading survives", all(f"## Section {i}" in "".join(c["text"] for c in chb) for i in range(8)))
    big = "".join("Sentence number %d about widgets. " % i for i in range(400))
    cbig = chunk_text(big)
    chk("hard-wrap respects the budget", all(len(c["text"]) <= CHUNK_CHARS + OVERLAP_CHARS for c in cbig))
    srt = ("1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:03,000 --> 00:00:05,000\n"
           "<i>Hello there.</i>\n\n3\n00:00:05,000 --> 00:00:07,000\nSecond line.\n")
    chk("srt cleaning drops cues and repeats", clean_srt(srt).count("Hello") == 1 and "00:00" not in clean_srt(srt))
    good = {"overview": "A deck about the Q3 widget programme.", "bullets": ["Northwind shipped 412 units."],
            "actions": [{"what": "Call the carrier about the freight claim", "who": "Ops", "when": "3/5"}]}
    chk("summary parses", parse_summary(json.dumps(good)) == good)
    chk("bad summary rejected", parse_summary('{"overview": 3}') is None)
    chk("fenced summary parses", parse_summary("```json\n" + json.dumps(good) + "\n```") is not None)
    src = " " + " ".join(norm_words(t)) + " "
    chk("verbatim quote passes", quote_ok("Northwind shipped 412 units in Q3, up from 380", src))
    chk("invented quote fails", not quote_ok("Northwind shipped 900 units in Q3 with record margin", src))
    chk("claim split drops headings", split_claims("## Pricing\n- Northwind shipped 412 units in Q3.\n") ==
        ["Northwind shipped 412 units in Q3."])
    chk("coverage: point found", covered("Northwind shipped 412 units in Q3", "Northwind shipped 412 units in Q3."))
    chk("coverage: point missing", not covered("Contoso declined to match the new price", "Northwind shipped 412 units."))
    chk("unsupported number caught", unsupported_numbers("Northwind shipped 900 units.", t) == ["900"])

    # scripted model: the writer plants one wrong price; the checker contradicts it; the repair fixes it
    def fake_chat(role, messages):
        sysm, user = messages[0]["content"], messages[1]["content"]
        if sysm == EXTRACT_SYS:
            return json.dumps({"points": ["Northwind shipped 412 units in Q3", "WX-100 is $18.40 per unit",
                                          "The freight claim on invoice 44821 was rejected"]})
        if "CLAIMS THAT FAILED" in user:
            return json.dumps({"overview": "A deck on the Q3 widget programme.", "bullets": [
                "Northwind shipped 412 units in Q3, up from 380.",
                "WX-100 moved to $18.40 per unit on 3/4/2026.",
                "The freight claim on invoice 44821 was rejected."],
                "actions": [{"what": "Follow up on the rejected freight claim", "who": "", "when": ""}]})
        if sysm.startswith(WRITE_SYS):
            return json.dumps({"overview": "A deck on the Q3 widget programme.", "bullets": [
                "Northwind shipped 412 units in Q3, up from 380.",
                "WX-100 moved to $21.75 per unit on 3/4/2026.",
                "The freight claim was rejected."], "actions": []})
        return json.dumps([{"n": 1, "label": "S", "quote": "Northwind shipped 412 units in Q3"},
                           {"n": 2, "label": "C", "quote": "Part WX-100 moved to $18.40 per unit on 3/4/2026"},
                           {"n": 3, "label": "S", "quote": "freight claim on invoice 44821 was rejected"}])

    res = Pipeline(fake_chat, verbose=False).run(t)
    chk("pipeline completes", res["ok"])
    chk("planted bad claim removed after repair", "$21.75" not in res["markdown"])
    chk("repair ran one round then stopped", len(res["rounds"]) == 2)
    chk("footer reports counts", "verified" in res["footer"] and "checklist" in res["footer"])

    def stubborn(role, messages):          # never fixes anything: the bad claim must survive MARKED
        sysm = messages[0]["content"]
        if sysm == EXTRACT_SYS:
            return json.dumps({"points": []})
        if sysm.startswith(CHECK_SYS):
            return json.dumps([{"n": i + 1, "label": "S" if i else "U", "quote": "freight claim on invoice 44821"}
                               for i in range(2)])
        return json.dumps({"overview": "Some deck.", "bullets": [
            "WX-100 moved to $21.75 per unit on 3/4/2026.", "The freight claim was rejected."], "actions": []})

    r2 = Pipeline(stubborn, verbose=False).run(t)
    chk("unfixable claim is kept and marked", "[unverified]" in r2["markdown"] and "21.75" in r2["markdown"])

    seen = {"notes": 0, "write_user": ""}      # a long document goes through the notes tree

    def tree_chat(role, messages):
        sysm, user = messages[0]["content"], messages[1]["content"]
        if sysm == EXTRACT_SYS:
            return json.dumps({"points": ["Part A is 18.40 per unit", "Contoso declined to match"]})
        if sysm == NOTES_SYS:
            seen["notes"] += 1
            return json.dumps({"notes": ["WX-100 moved to $18.40 per unit", "Contoso declined to match"]})
        if sysm.startswith(CHECK_SYS):
            return json.dumps([{"n": i + 1, "label": "S", "quote": "Contoso declined to match"} for i in range(40)])
        seen["write_user"] = user
        return json.dumps({"overview": "A long document about widgets.", "bullets": ["Contoso declined to match."],
                           "actions": []})

    big2 = "".join("## Part %d\n\n" % i + ("Contoso declined to match and WX-100 moved to $18.40 per unit. " * 110) + "\n\n"
                   for i in range(12))
    r3 = Pipeline(tree_chat, verbose=False).run(big2)
    chk("long document uses the notes tree", seen["notes"] >= 2)
    chk("long-document writer is told it has notes, not the document", "notes taken group by group" in seen["write_user"])
    chk("long document still completes", r3["ok"])
    print("SELFTEST", "OK" if ok else "FAILED")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Local summarize-verify pipeline (stdlib).")
    ap.add_argument("source", nargs="?", help="a .txt/.md/.srt/.vtt file")
    ap.add_argument("--single", action="store_true", help="one model call, no checks (documents under ~60k chars)")
    ap.add_argument("--out", help="write the markdown here (default: print only)")
    ap.add_argument("--base-url"), ap.add_argument("--writer-model"), ap.add_argument("--check-model")
    ap.add_argument("--think", choices=["on", "off"], help="send chat_template_kwargs.enable_thinking")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="code-side checks, no model")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    if not a.source:
        ap.error("give a text file (or --selftest)")
    p = Path(a.source)
    raw = p.read_text(encoding="utf-8", errors="replace")
    text = clean_srt(raw) if p.suffix.lower() in (".srt", ".vtt") else raw
    chat = make_chat(a.base_url, a.writer_model, a.check_model, think=None if not a.think else a.think == "on")
    if a.single and len(text) <= SINGLE_MAX_CHARS:
        res = single_pass(chat, text)
    else:
        res = Pipeline(chat, verbose=not a.quiet).run(text)
    if not res["ok"]:
        print("FAILED:", res.get("why"), file=sys.stderr)
        return 1
    print(res["markdown"])
    if a.out:
        Path(a.out).write_text(res["markdown"], encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
