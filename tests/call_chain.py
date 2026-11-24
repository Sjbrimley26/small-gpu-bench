#!/usr/bin/env python3
"""CALL-CHAIN: does a second model that audits the first one's action items cut false tasks without losing recall?

Pass 1 is a saved CALL-SYN record (tests/call.py writes the full reply per call). Pass 2 sees the transcript plus
the numbered candidate tasks and answers keep/drop per candidate. It never rewrites a task: the survivors keep
pass 1's action, owner and due date, so recall can only fall and false tasks can only fall. The grader is the
same code grader as CALL-SYN (tests/call.py grade/summarise); seconds are pass 1 + pass 2 per call.

Audit-specific counts, from the same gold matching:
  true_dropped   tasks that matched a gold commitment in pass 1 but did not survive (recall lost to the audit)
  false_dropped  tasks that matched nothing in pass 1 and did not survive (the audit doing its job)
  false_kept     tasks that matched nothing and survived
A call whose pass 1 produced no task is not sent to pass 2 (nothing to audit, 0 s added).

Prompt variants (--variant):
  reject  keep a candidate only if a speaker explicitly and unconditionally committed to it
  quote   same rule, and the reply must copy the committing sentence verbatim; code drops the candidate when
          the quote is absent from the transcript (a check a model cannot fake by saying "keep")

An unparseable audit reply keeps every candidate (fail open) and is counted in audit_unparsed.

  python3 tests/call_chain.py --selftest
  BASE_URL=... MODEL=frog-agent-think python3 tests/call_chain.py --pass1 results/raw/CALL-bolt-...json \\
      --think on --reasoning-budget 256 --max-tokens 2000 --variant reject
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call  # noqa: E402
import common  # noqa: E402

AUDIT = """Below is a phone-call transcript and a list of candidate action items that someone else extracted from it. \
Check each candidate against the transcript.

Keep a candidate only if a speaker on the call explicitly and unconditionally committed to do it. Drop it if it was \
only an idea or a maybe ("we should", "somebody ought to"), if it depends on a condition that has not happened, if \
the person who will do it is not on the call, or if the commitment was cancelled or taken back later in the call.

The call took place on {date}.

{reply_format}

TRANSCRIPT
----------
{transcript}

CANDIDATES
----------
{candidates}
"""

FORMAT = {
    "reject": 'Reply with one JSON object and nothing else:\n'
              '{{"verdicts": [{{"id": number, "keep": true or false}}]}}\nOne entry per candidate.',
    "quote": 'Reply with one JSON object and nothing else:\n'
             '{{"verdicts": [{{"id": number, "keep": true or false, "quote": string or null}}]}}\n'
             'One entry per candidate. For a candidate you keep, "quote" is the exact sentence from the transcript '
             'where the speaker commits to it, copied word for word; use null for one you drop.',
}


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


def pass1_tasks(row):
    """Candidate task dicts from a saved CALL row (the full reply, else an intact reply_head)."""
    obj, mode = call.parse_reply(row.get("reply") or row.get("reply_head") or "")
    tasks = obj.get("tasks") if isinstance(obj, dict) else None
    if not isinstance(tasks, list):
        return [], mode
    return [t for t in tasks if isinstance(t, dict)], mode


def label_tasks(tasks, c):
    """True where the task matches a gold commitment, using call.grade's greedy matching."""
    used, lab = set(), [False] * len(tasks)
    for gold in c["gold"]:
        for i, t in enumerate(tasks):
            if i not in used and any(k in str(t.get("action") or "").lower() for k in gold["kw"]):
                used.add(i)
                lab[i] = True
                break
    return lab


def build_prompt(c, tasks, variant):
    cand = "\n".join("%d. %s (owner: %s, due: %s)" % (i + 1, t.get("action"), t.get("owner"), t.get("due") or "none")
                     for i, t in enumerate(tasks))
    return AUDIT.format(date=c["date"], reply_format=FORMAT[variant], transcript=c["transcript"], candidates=cand)


def parse_verdicts(text, n, transcript, variant):
    """-> (keep: list[bool] of length n, ok). Fail open when the reply does not parse."""
    obj, mode = call.parse_reply(text)
    vs = obj.get("verdicts") if isinstance(obj, dict) else None
    if not isinstance(vs, list):
        return [True] * n, False
    keep = [True] * n
    tn = norm(transcript)
    for v in vs:
        if not isinstance(v, dict):
            continue
        try:
            i = int(v.get("id")) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= i < n:
            continue
        k = v.get("keep") is True
        if k and variant == "quote":
            q = norm(v.get("quote") if isinstance(v.get("quote"), str) else "")
            k = len(q) >= 8 and q in tn
        keep[i] = k
    return keep, True


def seed_mismatch(rec, seed):
    """The pass-1 record's fixture and the grader's fixture must be the same set of calls."""
    s = rec.get("seed")
    if s in (None, seed):
        return None
    return "pass-1 record is seed %s, not %s: the audit grader would score against another fixture" % (s, seed)


def run(args):
    rec = json.load(open(args.pass1))
    bad = seed_mismatch(rec, args.seed)
    if bad:
        common.die(bad)
    calls = {c["id"]: c for c in call.build(args.seed)}
    rows, audit = [], {"audited_calls": 0, "tasks_in": 0, "true_dropped": 0, "false_dropped": 0, "false_kept": 0,
                       "audit_unparsed": 0, "audit_secs": 0.0, "quote_rejected": 0}
    for p1 in rec["rows"][:args.limit] if args.limit else rec["rows"]:
        c = calls[p1["id"]]
        tasks, mode = pass1_tasks(p1)
        lab = label_tasks(tasks, c)
        kept, secs = tasks, 0.0
        if tasks:
            r = common.chat(args, [{"role": "user", "content": build_prompt(c, tasks, args.variant)}],
                            max_tokens=args.max_tokens, temperature=args.temperature,
                            extra=({"reasoning_budget_tokens": args.reasoning_budget} if args.reasoning_budget else None))
            secs = r.get("s") or 0.0
            keep, ok = parse_verdicts("" if r.get("error") else r["content"], len(tasks), c["transcript"], args.variant)
            audit["audited_calls"] += 1
            audit["audit_unparsed"] += 0 if ok else 1
            audit["tasks_in"] += len(tasks)
            audit["true_dropped"] += sum(1 for k, t in zip(keep, lab) if t and not k)
            audit["false_dropped"] += sum(1 for k, t in zip(keep, lab) if not t and not k)
            audit["false_kept"] += sum(1 for k, t in zip(keep, lab) if not t and k)
            kept = [t for t, k in zip(tasks, keep) if k]
        audit["audit_secs"] += secs
        row = {"id": c["id"], "tier": c["tier"], "parse": mode, "s": round((p1.get("s") or 0) + secs, 1),
               "pass1_s": p1.get("s"), "audit_s": secs, "tasks_pass1": len(tasks), "tasks_kept": len(kept)}
        row.update(call.grade({"summary": "", "tasks": kept}, c))
        rows.append(row)
        common.say("%-14s pass1=%d kept=%d recall=%d false=%d %ss" % (
            c["id"], len(tasks), len(kept), row["recall"], row["false"], row["s"]))
    summ = call.summarise(rows)
    base = call.summarise([dict(r, **call.grade({"summary": "", "tasks": pass1_tasks(p)[0]}, calls[r["id"]]))
                           for r, p in zip(rows, rec["rows"])])
    a, b = summ["ALL"], base["ALL"]
    audit["audit_secs"] = round(audit["audit_secs"], 1)
    common.say("CHAIN %s -> %s [%s]  recall %s/%s (pass1 %s)  false tasks %s (pass1 %s)  due %s/%s  "
               "true dropped %s  false dropped %s  audit unparsed %s  %ss" % (
                   rec["model"], args.model, args.variant, a["recall"], a["gold"], b["recall"], a["false_tasks"],
                   b["false_tasks"], a["due_ok"], a["due_scored"], audit["true_dropped"], audit["false_dropped"],
                   audit["audit_unparsed"], a["secs"]))
    args.pass1 = os.path.basename(args.pass1)        # the record must not carry a host path
    common.save_results(args, "CALLCHAIN", {
        "seed": args.seed, "pass1_model": rec["model"], "pass1_file": args.pass1,
        "pass1_thinking": rec.get("thinking"), "pass1_reasoning_budget": rec.get("reasoning_budget"),
        "variant": args.variant, "thinking": args.think, "reasoning_budget": args.reasoning_budget,
        "max_tokens": args.max_tokens, "summary": summ, "pass1_summary": base, "audit": audit, "rows": rows})
    return 0


def selftest():
    c = common.Checks()
    calls = call.build()
    by = {t: [x for x in calls if x["tier"] == t] for t, _ in call.PLAN}
    x = next(y for y in by["plain"] if len(y["gold"]) == 2)
    good = [{"action": "do the " + g["kw"][0], "owner": g["owner"], "due": g["due"]} for g in x["gold"]]
    decoy = {"action": "catch up sometime", "owner": x["speakers"][0], "due": None}
    tasks = good + [decoy]
    c("labels: gold true, decoy false", label_tasks(tasks, x) == [True, True, False])
    p = build_prompt(x, tasks, "quote")
    c("prompt carries transcript and 3 numbered candidates", x["transcript"] in p and "3. catch up sometime" in p)
    line = next(l for l in x["transcript"].split("\n") if call.DECOYS[0] in l or any(d in l for d in call.DECOYS))
    sent = line.split(": ", 1)[1]
    k, ok = parse_verdicts(json.dumps({"verdicts": [{"id": 1, "keep": True}, {"id": 2, "keep": True},
                                                    {"id": 3, "keep": False}]}), 3, x["transcript"], "reject")
    c("reject: verdicts applied", ok and k == [True, True, False])
    k, ok = parse_verdicts("not json", 3, x["transcript"], "reject")
    c("unparseable audit fails open", (not ok) and k == [True, True, True])
    k, ok = parse_verdicts(json.dumps({"verdicts": [{"id": 3, "keep": True, "quote": sent}]}), 3, x["transcript"], "quote")
    c("quote: a verbatim quote from the transcript is accepted", ok and k[2] is True)
    k, ok = parse_verdicts(json.dumps({"verdicts": [{"id": 3, "keep": True, "quote": "I solemnly promise to climb a mountain"}]}),
                           3, x["transcript"], "quote")
    c("quote: a made-up quote is rejected", ok and k[2] is False)
    k, ok = parse_verdicts(json.dumps({"verdicts": [{"id": 1, "keep": True}]}), 3, x["transcript"], "quote")
    c("quote: keep without a quote is rejected", k[0] is False)
    k, ok = parse_verdicts(json.dumps({"verdicts": [{"id": 9, "keep": False}, {"id": "x"}, "junk"]}), 3, x["transcript"], "reject")
    c("out-of-range, malformed ids ignored; unmentioned candidates kept", ok and k == [True, True, True])
    kept = [t for t, kk in zip(tasks, [True, False, False]) if kk]
    g = call.grade({"summary": "", "tasks": kept}, x)
    c("dropping a true task lowers recall and does not create a false task", g["recall"] == 1 and g["false"] == 0)
    c("pass1_tasks reads a full reply", pass1_tasks({"reply": json.dumps({"summary": "s", "tasks": tasks})})[0] == tasks)
    c("pass1_tasks on garbage is empty", pass1_tasks({"reply": "nope"})[0] == [])
    c("a foreign-seed pass-1 record is refused",
      seed_mismatch({"seed": call.SEED + 1}, call.SEED) is not None)
    c("a matching or seedless pass-1 record is accepted",
      seed_mismatch({"seed": call.SEED}, call.SEED) is None and seed_mismatch({}, call.SEED) is None)
    other = call.build(call.SEED + 1)
    x2 = next(y for y in other if y["tier"] == "plain")
    perfect = {"summary": "s", "tasks": [{"action": "do the " + g["kw"][0], "owner": g["owner"], "due": g["due"]}
                                       for g in x2["gold"]]}
    g2 = call.grade(perfect, x2)
    c("seed+1: 44 calls, and its own gold grades a perfect reply clean",
      len(other) == 44 and g2["recall"] == len(x2["gold"]) and g2["false"] == 0)
    return c.finish()


def main():
    ap = argparse.ArgumentParser(description="CALL-CHAIN: audit a saved CALL-SYN reply with a second pass")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pass1", help="CALL-SYN JSON record that carries full replies")
    ap.add_argument("--variant", choices=sorted(FORMAT), default="reject")
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reasoning-budget", type=int, default=0)
    ap.add_argument("--seed", type=int, default=call.SEED,
                    help="fixture seed; must match the pass-1 record's seed")
    common.add_common_args(ap)
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if not args.pass1:
        common.die("--pass1 is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
