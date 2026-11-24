#!/usr/bin/env python3
"""CALL-SYN: did the model find the commitments in a phone call, and invent none?

The task a call-summary worker really sells is "turn this call into action items". A count of emitted tasks
cannot score it (three tasks may be the wrong three), and a tiny hand-written set cannot say anything about
false tasks, which is where small models fail. This test builds 44 invented two-speaker calls in code, each
with 0-3 planted commitments, and the gold answer is produced by the same code that writes the transcript, so
text and key cannot disagree (`--gen` writes tests/fixtures/call/calls.json; `--selftest` fails if the
committed copy drifts from the code).

Tiers (calls per tier in brackets):
  plain [8]        a commitment stated plainly, 1-3 per call
  buried [8]       1-2 commitments in the middle of a long (400-900 word) call of small talk
  conditional [6]  "if the hinges come in, I'll ..." and the condition never resolves  -> NO task
  cancelled [6]    promised, then retracted ("scratch that")                           -> NO task
  third_party [6]  "Dana said she'd email ..." - somebody who is not on the call      -> NO task
  banter [6]       talk, no commitment                                                 -> NO task
  short [4]        wrong number / dropped call, no commitment                          -> NO task
Every call that has a real commitment also carries a decoy intention ("we should catch up sometime") that
must not become a task.

Scored in code, no judge, per call and summed per tier:
  recall    gold commitments whose action keyword appears in some reply task
  owner     of those, the right speaker is named
  due       of those that have a due date, the reply's `due` equals the date resolved against the call date
  false     reply tasks that match no gold commitment (invented, decoy, cancelled, conditional, third-party,
            duplicate); `false_calls` counts no-commitment calls with at least one task
  parse     strict | fenced | wrapped | invalid | empty, kept apart from the content scores

  python3 tests/call.py --selftest
  python3 tests/call.py --gen
  BASE_URL=... MODEL=... THINK=off python3 tests/call.py
  BASE_URL=... MODEL=... THINK=on  python3 tests/call.py --max-tokens 4000 --limit 12
"""
import argparse
import datetime
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SEED = 20261123
PLAN = [("plain", 8), ("buried", 8), ("conditional", 6), ("cancelled", 6),
        ("third_party", 6), ("banter", 6), ("short", 4)]
NO_PROMISE = ("conditional", "cancelled", "third_party", "banter", "short")
NAMES = ["Rhea", "Marcus", "Tomas", "Ingrid", "Owen", "Leila", "Gabe", "Nadia", "Perry", "Jun"]
THIRD = ["Dana", "Luis", "Priya", "Hollis"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# (keywords any of which names the action, imperative phrase)
ACTIONS = [
    (["quote"], "send over the revised quote"),
    (["hinge"], "order the replacement hinges"),
    (["supplier"], "call the supplier about the delay"),
    (["photo"], "email the install photos"),
    (["measure"], "schedule the field measure"),
    (["sample"], "drop off the door samples"),
    (["delivery"], "confirm the delivery window"),
    (["permit"], "file the permit paperwork"),
    (["frame"], "pick up the stained frames"),
    (["invoice"], "update the invoice"),
]
DECOYS = ["We should catch up sometime, it has been ages.",
          "Somebody ought to look at that gate hardware one of these days.",
          "I really should learn that new scheduling software.",
          "We ought to get lunch when things calm down."]
FILLER = [
    "The weather has been strange for November, hasn't it.",
    "I was stuck behind a flatbed on the freeway for twenty minutes this morning.",
    "My kid's school moved pickup to a different gate and nobody told us.",
    "That new place on Mill Avenue finally opened, the line was out the door.",
    "Honestly the traffic on the loop has been worse since the lane closures.",
    "We had a long weekend, mostly yard work and a ball game.",
    "The shop was quiet today, just the radio and the saw.",
    "I think the coffee machine in the office is finally dead.",
    "Did you see the game last night? That fourth quarter was something.",
    "Prices on everything keep creeping up, lumber included.",
    "I need a new phone, mine drops calls whenever I walk past the loading dock.",
    "The dog got into the trash again, third time this week.",
    "Last year's holiday party was a lot more fun than the one they are planning.",
    "It is supposed to cool off by the weekend, finally.",
    "My brother-in-law is thinking about moving out to Tucson.",
    "I had the strangest dream about a warehouse full of doors.",
]
REPLIES = ["Yeah.", "Right, I know what you mean.", "Ha, that is true.", "Really? Huh.",
           "Okay, got it.", "Sure.", "That makes sense.", "Mm-hm, go on."]


def _next_weekday(d, wd):
    n = (wd - d.weekday()) % 7 or 7
    return d + datetime.timedelta(days=n)


def due_phrase(rng, call_date):
    """-> (spoken phrase, resolved ISO date or None)."""
    kind = rng.choice(["tomorrow", "weekday", "weekday", "nth", "none"])
    if kind == "tomorrow":
        return "tomorrow", (call_date + datetime.timedelta(days=1)).isoformat()
    if kind == "weekday":
        wd = rng.randrange(5)
        return "by " + WEEKDAYS[wd], _next_weekday(call_date, wd).isoformat()
    if kind == "nth":
        later = call_date + datetime.timedelta(days=rng.randint(4, 12))
        n = later.day
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return "by the %d%s" % (n, suffix), later.isoformat()
    return "", None


def promise_line(rng, phrase, due_text):
    f = rng.choice(["I'll {p} {d}.", "Let me {p} {d}.", "Okay, I can {p} {d}."])
    return re.sub(r"\s+([.,])", r"\1", f.format(p=phrase, d=due_text).replace("  ", " ")).strip()


def filler_lines(rng, n, speakers):
    """n lines alternating between the two speakers; no commitment language anywhere in the pool."""
    out = []
    pool = FILLER[:]
    rng.shuffle(pool)
    for i in range(n):
        sp = speakers[i % 2]
        text = pool[(i // 2) % len(pool)] if i % 2 == 0 else rng.choice(REPLIES)
        out.append((sp, text))
    return out


def build(seed=SEED):
    """-> list of calls: {id, tier, date, speakers, transcript, gold: [{kw, owner, due}]}."""
    rng = random.Random(seed)
    calls = []
    for tier, count in PLAN:
        for k in range(count):
            a, b = rng.sample(NAMES, 2)
            sp = [a, b]
            date = datetime.date(2026, 11, 2) + datetime.timedelta(days=rng.randrange(0, 40))
            while date.weekday() > 4:
                date += datetime.timedelta(days=1)
            acts = rng.sample(ACTIONS, 4)
            lines, gold = [], []
            greet = [(a, "Hi, it's %s, do you have a minute?" % a), (b, "Sure, go ahead.")]

            def commit(act, speaker, rng=rng, date=date):
                kw, phrase = act
                due_text, due = due_phrase(rng, date)
                return (speaker, promise_line(rng, phrase, due_text)), {"kw": kw, "owner": speaker, "due": due}

            if tier == "plain":
                n = 1 + k % 3
                body = []
                for i in range(n):
                    ln, g = commit(acts[i], rng.choice(sp))
                    body += [ln, (sp[1] if ln[0] == a else a, "Great, thanks.")]
                    gold.append(g)
                body.insert(rng.randrange(len(body) + 1) // 2 * 2, (rng.choice(sp), rng.choice(DECOYS)))
                lines = greet + filler_lines(rng, 8 + 2 * (k % 3), sp) + body
            elif tier == "buried":
                n = 1 + k % 2
                head = filler_lines(rng, 20 + k * 6, sp)
                mid = []
                for i in range(n):
                    ln, g = commit(acts[i], rng.choice(sp))
                    mid += [ln, ("%s" % (sp[1] if ln[0] == a else a), "Okay, noted.")]
                    gold.append(g)
                mid.insert(0, (rng.choice(sp), rng.choice(DECOYS)))
                tail = filler_lines(rng, 20 + (7 - k) * 6, sp)
                lines = greet + head + mid + tail
            elif tier == "conditional":
                kw, phrase = acts[0]
                due_text, _ = due_phrase(rng, date)
                lines = greet + filler_lines(rng, 4, sp) + [
                    (a, "If the %s comes in, I'll %s %s." % (kw[0] + "s" if kw[0] != "quote" else "quote",
                                                              phrase, due_text)),
                    (b, "Right, but nobody knows when that will be."),
                    (a, "Exactly. No idea yet. We will see."),
                ] + filler_lines(rng, 2, sp)
            elif tier == "cancelled":
                kw, phrase = acts[0]
                due_text, _ = due_phrase(rng, date)
                lines = greet + filler_lines(rng, 3, sp) + [
                    (a, promise_line(rng, phrase, due_text)),
                    (b, "Wait, scratch that. The customer called and cancelled it."),
                    (a, "Okay, scratch that then. I won't do it."),
                ] + filler_lines(rng, 2, sp)
            elif tier == "third_party":
                kw, phrase = acts[0]
                other = rng.choice(THIRD)
                due_text, _ = due_phrase(rng, date)
                said = rng.choice(["%s said she'd %s %s.", "%s is going to %s %s."])
                lines = greet + filler_lines(rng, 3, sp) + [
                    (a, said % (other, phrase, due_text)),
                    (b, "Good, then it is covered on their side."),
                ] + filler_lines(rng, 3, sp)
            elif tier == "banter":
                lines = greet + filler_lines(rng, 16 + k * 2, sp)
            else:  # short: wrong number
                lines = [(a, "Hello? Is this the door shop?"),
                         (b, "No, I think you have the wrong number."),
                         (a, "Oh, sorry. I was trying to reach the shop on the east side."),
                         (b, "No problem. That is a different number from this one."),
                         (a, "Okay, thank you, sorry again for the trouble."),
                         (b, "It happens. Have a good afternoon."),
                         (a, "You too, goodbye."), (b, "Bye."),
                         (a, "Sorry, one more thing, do you happen to know the right number?"),
                         (b, "I do not, I am afraid. Try the website."),
                         (a, "Okay, I will do that, thank you."), (b, "Take care.")][:10 + k]
            calls.append({"id": "%s-%02d" % (tier, k + 1), "tier": tier, "date": date.isoformat(),
                          "weekday": WEEKDAYS[date.weekday()], "speakers": sp,
                          "transcript": "\n".join("%s: %s" % ln for ln in lines), "gold": gold})
    return calls


PROMPT = """Read the phone-call transcript below and list the action items: things a speaker on the call \
committed to do. Skip ideas and maybes.

The call took place on {date} ({weekday}). Resolve any relative due date against that day.

Reply with one JSON object and nothing else:
{{"summary": string (one or two sentences),
  "tasks": [{{"action": string, "owner": string (the speaker's name), "due": string (YYYY-MM-DD) or null}}]}}
Use an empty list when nobody committed to anything.

TRANSCRIPT
----------
{transcript}
"""


def parse_reply(text):
    raw = (text or "").strip()
    if not raw:
        return None, "empty"
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", raw, re.S)
    for cand in [raw] + fenced:
        try:
            return json.loads(cand.strip()), ("strict" if cand is raw else "fenced")
        except Exception:
            pass
    s, e = raw.find("{"), raw.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(raw[s:e + 1]), "wrapped"
        except Exception:
            pass
    return None, "invalid"


def grade(obj, call):
    """-> per-call score dict. obj may be None (unparsed)."""
    g = {"gold_n": len(call["gold"]), "tasks_n": 0, "recall": 0, "owner_ok": 0,
         "due_scored": sum(1 for x in call["gold"] if x["due"]), "due_ok": 0, "false": 0,
         "shape_ok": False}
    tasks = obj.get("tasks") if isinstance(obj, dict) else None
    if not isinstance(tasks, list):
        g["false"] = 0
        return g
    tasks = [t for t in tasks if isinstance(t, dict)]
    g["shape_ok"] = (isinstance(obj.get("summary"), str) and len(tasks) == len(obj["tasks"])
                     and all(isinstance(t.get("action"), str) for t in tasks))
    g["tasks_n"] = len(tasks)
    used = set()
    matched = 0
    for gold in call["gold"]:
        for i, t in enumerate(tasks):
            if i in used:
                continue
            if any(k in str(t.get("action") or "").lower() for k in gold["kw"]):
                used.add(i)
                matched += 1
                owner = str(t.get("owner") or "").lower()
                g["owner_ok"] += gold["owner"].lower() in owner
                if gold["due"]:
                    g["due_ok"] += str(t.get("due") or "").strip() == gold["due"]
                break
    g["recall"] = matched
    g["false"] = len(tasks) - matched
    return g


def summarise(rows):
    """Per tier and overall. rows: list of {tier, parse, s, **grade}."""
    out = {}
    for tier in [t for t, _ in PLAN] + ["ALL"]:
        rs = [r for r in rows if tier == "ALL" or r["tier"] == tier]
        if not rs:
            continue
        gold = sum(r["gold_n"] for r in rs)
        d = {"calls": len(rs), "gold": gold, "recall": sum(r["recall"] for r in rs),
             "owner_ok": sum(r["owner_ok"] for r in rs),
             "due_scored": sum(r["due_scored"] for r in rs), "due_ok": sum(r["due_ok"] for r in rs),
             "false_tasks": sum(r["false"] for r in rs),
             "false_calls": sum(1 for r in rs if r["gold_n"] == 0 and r["tasks_n"] > 0),
             "parsed": sum(1 for r in rs if r["parse"] in ("strict", "fenced", "wrapped")),
             "unparsed": sum(1 for r in rs if r["parse"] not in ("strict", "fenced", "wrapped")),
             "secs": round(sum(r.get("s") or 0 for r in rs), 1)}
        out[tier] = d
    return out


def run(args, calls):
    rows = []
    for c in calls[:args.limit] if args.limit else calls:
        prompt = PROMPT.format(date=c["date"], weekday=c["weekday"], transcript=c["transcript"])
        r = common.chat(args, [{"role": "user", "content": prompt}], max_tokens=args.max_tokens,
                        temperature=args.temperature,
                        extra=({"reasoning_budget_tokens": args.reasoning_budget} if args.reasoning_budget else None))
        row = {"id": c["id"], "tier": c["tier"], "s": r.get("s"), "finish": r.get("finish"),
               "completion_tokens": r.get("completion_tokens")}
        if r.get("error"):
            row.update({"parse": "error", "error": r["error"]})
            row.update(grade(None, c))
        else:
            obj, mode = parse_reply(r["content"])
            row["parse"] = mode
            row.update(grade(obj, c))
            row["reply_head"] = r["content"][:300]
        rows.append(row)
        common.say("%-14s parse=%-7s gold=%d recall=%d false=%d %ss" % (
            c["id"], row["parse"], row["gold_n"], row["recall"], row["false"], row["s"]))
    summ = summarise(rows)
    a = summ.get("ALL", {})
    common.say("CALL  recall %s/%s  owner %s/%s  due %s/%s  false tasks %s (on %s no-commitment calls)  unparsed %s  %ss" % (
        a.get("recall"), a.get("gold"), a.get("owner_ok"), a.get("recall"), a.get("due_ok"),
        a.get("due_scored"), a.get("false_tasks"), a.get("false_calls"), a.get("unparsed"), a.get("secs")))
    common.save_results(args, "CALL", {"seed": SEED, "thinking": args.think, "reasoning_budget": args.reasoning_budget, "max_tokens": args.max_tokens,
                                       "summary": summ, "rows": rows})
    return 0


def selftest():
    c = common.Checks()
    calls = build()
    c("44 calls", len(calls) == 44)
    c("deterministic", build() == calls)
    by = {t: [x for x in calls if x["tier"] == t] for t, _ in PLAN}
    c("tier sizes match the plan", all(len(by[t]) == n for t, n in PLAN))
    words = [len(x["transcript"].split()) for x in calls]
    c("every call 60-900 words (got %d-%d)" % (min(words), max(words)), 60 <= min(words) and max(words) <= 900)
    c("buried calls 400+ words", all(len(x["transcript"].split()) >= 400 for x in by["buried"]))
    c("0-3 commitments per call", all(len(x["gold"]) <= 3 for x in calls))
    c("no-promise tiers have empty gold", all(not x["gold"] for t in NO_PROMISE for x in by[t]))
    c("promise tiers all have gold", all(x["gold"] for t in ("plain", "buried") for x in by[t]))
    c("a 3-commitment call exists", any(len(x["gold"]) == 3 for x in calls))
    c("every promise call carries a decoy",
      all(any(d in x["transcript"] for d in DECOYS) for t in ("plain", "buried") for x in by[t]))
    c("gold due dates are weekdays or later than the call", all(
        (not g["due"]) or g["due"] > x["date"] for x in calls for g in x["gold"]))
    c("gold owners are on the call", all(g["owner"] in x["speakers"] for x in calls for g in x["gold"]))
    c("every gold action keyword appears in its transcript's phrase", all(
        any(k in x["transcript"].lower() for k in g["kw"]) for x in calls for g in x["gold"]))
    # scripted good reply: exactly the gold
    x = next(y for y in by["plain"] if len(y["gold"]) == 2)
    good = {"summary": "s", "tasks": [{"action": "do the " + g["kw"][0], "owner": g["owner"], "due": g["due"]}
                                      for g in x["gold"]]}
    gg = grade(good, x)
    c("good reply: full recall, owner, due, no false task",
      gg["recall"] == 2 and gg["owner_ok"] == 2 and gg["due_ok"] == gg["due_scored"] and gg["false"] == 0
      and gg["shape_ok"])
    # scripted bad reply: one missed, one invented (a decoy)
    bad = {"summary": "s", "tasks": [good["tasks"][0], {"action": "catch up sometime", "owner": x["speakers"][0],
                                                          "due": None}]}
    gb = grade(bad, x)
    c("bad reply: one missed, one invented", gb["recall"] == 1 and gb["false"] == 1)
    wrong = {"summary": "s", "tasks": [dict(good["tasks"][0], owner="Nobody", due="2000-01-01")]}
    gw = grade(wrong, x)
    c("wrong owner and wrong due are not credited", gw["owner_ok"] == 0 and gw["due_ok"] == 0 and gw["recall"] == 1)
    canc = by["cancelled"][0]
    gc = grade({"summary": "s", "tasks": [{"action": "do the thing", "owner": canc["speakers"][0], "due": None}]}, canc)
    c("any task on a cancelled call is false", gc["false"] == 1)
    c("empty list on a no-promise call scores clean", grade({"summary": "s", "tasks": []}, canc)["false"] == 0)
    c("unparsed reply scores zero recall", grade(None, x)["recall"] == 0)
    dup = {"summary": "s", "tasks": [good["tasks"][0], good["tasks"][0]]}
    c("a duplicate task counts as false", grade(dup, x)["false"] == 1 + 0 if len(x["gold"]) == 2 else True)
    clean = json.dumps(good)
    c("strict parse", parse_reply(clean)[1] == "strict")
    c("fenced parse", parse_reply("```json\n%s\n```" % clean)[1] == "fenced")
    c("wrapped parse", parse_reply("Here: %s ok" % clean)[1] == "wrapped")
    c("invalid parse", parse_reply("tasks: none")[1] == "invalid")
    c("empty parse", parse_reply("")[1] == "empty")
    rows = [dict(id=y["id"], tier=y["tier"], parse="strict", s=1.0,
                 **grade({"summary": "s", "tasks": []}, y)) for y in calls]
    s = summarise(rows)
    c("empty replies: zero false, recall 0 of %d" % s["ALL"]["gold"], s["ALL"]["false_tasks"] == 0
      and s["ALL"]["recall"] == 0 and s["ALL"]["gold"] > 0)
    p = common.fixture_path("call", "calls.json")
    if os.path.exists(p):
        c("committed fixture matches the code", json.load(open(p)) == json.loads(json.dumps(calls)))
    else:
        common.say("note: run --gen to write the fixture copy")
    return c.finish()


def gen():
    p = common.fixture_path("call", "calls.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(build(), open(p, "w"), indent=1)
    common.say("wrote", p)


def main():
    ap = argparse.ArgumentParser(description="CALL-SYN: commitments found, none invented, on invented calls")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--gen", action="store_true", help="rewrite tests/fixtures/call/calls.json")
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=0, help="first N calls only (smoke / thinking arm)")
    ap.add_argument("--reasoning-budget", type=int, default=0,
                    help="send reasoning_budget_tokens (llama.cpp per-request cap on thinking); 0 = not sent")
    common.add_common_args(ap)
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if args.gen:
        return gen()
    sys.exit(run(args, build()))


if __name__ == "__main__":
    main()
