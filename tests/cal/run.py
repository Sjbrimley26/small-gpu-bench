#!/usr/bin/env python3
"""CAL: code generation with a test-and-critic loop (ported from the lab's calagent harness).

The model writes one small JavaScript module at a time (calendar helpers: week start, month grid, event
layout, rendering). `node --test` grades each module against a fixed test file. On failure a critic (the
SAME model) diagnoses the failing output in a few bullets and the coder retries, up to 4 attempts.
Needs Node 20+ and python3; no pip packages. Works against any OpenAI-compatible endpoint.

Fixed protocol (what the published rows use):
  * 9 tasks: startOfWeek, monthGrid, rowsToEvents, eventsForDay, layoutDay, eventToRow, renderMonth,
    renderAgenda, renderWeek.  --extended adds expandRecurrence, buildIcs, layoutWeekSpans, findFreeSlots.
  * 4 attempts per task, same-model critic (400 tokens, clipped to 1200 chars), n=1 per cell.
  * thinking off is the headline (--max-tokens 4500); the thinking-on arm is --think on --max-tokens 11000
    with sampling 0.6/0.95.  Server context >= 16384.
  * two numbers per task: FIRST-ATTEMPT pass (does not depend on the critic; the clean number) and FINAL pass.
  * a task whose dependency module did not pass is BLOCKED, unless --reference DIR is given: then the
    dependency is borrowed from DIR (--gold-deps, what the published rows used). The reference solutions are
    the answer key and are not shipped; the published rows were produced with the lab's private copy.

  python3 tests/cal/run.py --selftest [--reference DIR]
  BASE_URL=... MODEL=... python3 tests/cal/run.py [--extended] [--only layoutDay] [--think on --max-tokens 11000]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import common  # noqa: E402
import prompts  # noqa: E402

NINE = ["startOfWeek", "monthGrid", "rowsToEvents", "eventsForDay", "layoutDay", "eventToRow",
        "renderMonth", "renderAgenda", "renderWeek"]
ANSI = re.compile(r"\x1b\[[0-9;]*m")
MAX_ATTEMPTS = 4
FEEDBACK_CHARS = 2500
CRITIC_CHARS = 1200
CRITIC_TOKENS = 400
TEST_TIMEOUT = 30


def load_tasks():
    with open(os.path.join(HERE, "tasks.toml"), "rb") as f:
        return tomllib.load(f)["task"]


def extract_code(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    blocks = re.findall(r"```(?:javascript|js|mjs|jsx)?[ \t]*\n(.*?)```", text, flags=re.S)
    blocks = [b for b in blocks if "export" in b] or blocks
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    if "export function" in text:                      # model forgot the fence
        return text.strip() + "\n"
    return None


def run_tests(work, tid, node="node"):
    env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0", "NODE_DISABLE_COLORS": "1"}
    cmd = [node, "--test", "--test-reporter=spec", os.path.join(work, "tests", tid + ".test.js")]
    try:
        p = subprocess.run(cmd, cwd=work, capture_output=True, timeout=TEST_TIMEOUT, env=env)
        out = ANSI.sub("", (p.stdout + p.stderr).decode("utf-8", errors="replace"))
        code = p.returncode
    except subprocess.TimeoutExpired:
        out, code = "Tests timed out after %ds (infinite loop?)" % TEST_TIMEOUT, 1
    counts = {k: int(v) for k, v in re.findall(r"^[ℹ#] (tests|pass|fail) (\d+)", out, flags=re.M)}
    total, passed = counts.get("tests", 0), counts.get("pass", 0)
    return {"ok": code == 0 and total > 0, "passed": passed, "total": total, "output": out}


def trim_failure(out, limit):
    """Shrink node's test output to what a small model needs: each failing test once with its assertion
    message, no stack traces, identical errors collapsed, crashes (syntax errors) kept."""
    noise = lambda l: (re.match(r"^\s+at ", l) or "node:internal" in l or re.match(r"^\s*test at ", l)  # noqa: E731
                       or re.match(r"^\s*(generatedMessage|code|operator|diff|actual|expected):", l)
                       or l.strip() in ("}", "{", "'test failed'") or not l.strip())
    pre, _, recap = out.partition("✖ failing tests:")
    pre = re.split(r"^[ℹ#] tests \d+", pre, maxsplit=1, flags=re.M)[0]
    counts = " ".join(re.findall(r"^[ℹ#] (?:pass|fail) \d+", out, flags=re.M))
    crash = [l.rstrip() for l in pre.splitlines()
             if not noise(l) and not l.lstrip().startswith(("✔", "✖", "▶", "Node.js v"))]
    blocks, cur = [], None
    for line in recap.splitlines():
        if line.lstrip().startswith("✖"):
            cur = [re.sub(r" \([\d.]+ms\)$", "", line.strip())]
            blocks.append(cur)
        elif cur is not None and not noise(line):
            cur.append(line.rstrip())
    parts, seen = [], {}
    if crash:
        parts.append("Your module crashed before the tests could run:\n" + "\n".join(crash[:15]))
    for b in blocks:
        err = "\n".join(b[1:]).strip()
        if not err:
            continue
        if err in seen:
            seen[err] += 1
            continue
        seen[err] = 0
        parts.append("\n".join(b))
    text = "\n\n".join(parts) or out[-limit:]
    dupes = sum(seen.values())
    if dupes:
        text += "\n\n(%d more failing test(s) had an identical error message)" % dupes
    text += "\n\nSummary: " + counts
    return text[:limit] + ("\n... (truncated)" if len(text) > limit else "")


def make_workdir(reference=None):
    """A scratch copy of the test tree; generated modules go into <work>/app/src/gen, never into the repo."""
    work = tempfile.mkdtemp(prefix="cal-")
    for d in ("tests", "app"):
        shutil.copytree(os.path.join(HERE, d), os.path.join(work, d))
    os.makedirs(os.path.join(work, "app", "src", "gen"), exist_ok=True)
    return work


def gen_path(work, tid):
    return os.path.join(work, "app", "src", "gen", tid + ".js")


def solve(args, task, by_id, work, util_src):
    tid = task["id"]
    test_src = common.read_text(os.path.join(work, "tests", tid + ".test.js"))
    deps = [by_id[d] for d in task["deps"]]
    first = prompts.coder_first(task, util_src, deps, test_src)
    res = {"id": tid, "status": "failed", "first_attempt_pass": False, "attempts": 0, "best_passed": 0,
           "total": 0, "coder_tokens": 0, "coder_seconds": 0.0, "critic_calls": 0, "errors": 0, "history": []}
    messages, last_reply, failure, diagnosis, best_code = first, None, None, None, None
    extra = {"top_k": 20}
    sampling = dict(temperature=0.6, top_p=0.95) if args.think == "on" else dict(temperature=0.7, top_p=0.8)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1:
            messages = first if last_reply is None else prompts.coder_retry(first, last_reply, failure, diagnosis)
        common.say("  [%s] attempt %d" % (tid, attempt))
        r = common.chat(args, messages, max_tokens=args.max_tokens, extra=extra, **sampling)
        res["attempts"] = attempt
        step = {"attempt": attempt, "completion_tokens": r.get("completion_tokens"), "s": r.get("s"),
                "finish": r.get("finish")}
        if r.get("error"):
            res["errors"] += 1
            step.update(passed=0, total=0, note="error")
            res["history"].append(step)
            failure, diagnosis = failure or "The request failed. Output the module.", None
            continue
        res["coder_tokens"] += r.get("completion_tokens") or 0
        res["coder_seconds"] += r.get("s") or 0.0
        last_reply = r["content"]
        code = extract_code(last_reply)
        if code is None or ("function " + tid) not in code:
            failure = ("Your answer did not contain a ```js code block exporting `function %s`. "
                       "Reply with the full module inside one ```js block." % tid)
            if r.get("finish") == "length":
                failure = "Your answer was cut off before the code block was finished. " + failure
            step.update(passed=0, total=0, note="truncated" if r.get("finish") == "length" else "no code")
        else:
            with open(gen_path(work, tid), "w", encoding="utf-8") as f:
                f.write(code)
            t = run_tests(work, tid)
            step.update(passed=t["passed"], total=t["total"])
            res["total"] = t["total"] or res["total"]
            common.say("  [%s]   tests %d/%d" % (tid, t["passed"], t["total"]))
            if t["passed"] >= res["best_passed"]:
                res["best_passed"], best_code = t["passed"], code
            if t["ok"]:
                res["status"] = "passed"
                res["first_attempt_pass"] = attempt == 1
                res["history"].append(step)
                break
            failure = trim_failure(t["output"], FEEDBACK_CHARS)
        diagnosis = None
        if attempt < MAX_ATTEMPTS and code:
            c = common.chat(args, prompts.critic(task, code, failure), max_tokens=CRITIC_TOKENS,
                            temperature=0.3, top_p=0.8, extra=extra)
            if not c.get("error"):
                diagnosis = prompts.clip(c["content"], CRITIC_CHARS)
                res["critic_calls"] += 1
        res["history"].append(step)
    if res["status"] != "passed" and best_code:
        with open(gen_path(work, tid), "w", encoding="utf-8") as f:
            f.write(best_code)
    res["tok_s"] = round(res["coder_tokens"] / res["coder_seconds"], 1) if res["coder_seconds"] else None
    return res


def borrow(reference, work, tid):
    shutil.copy(os.path.join(reference, tid + ".js"), gen_path(work, tid))


def run_model(args, tasks):
    by_id = {t["id"]: t for t in tasks}
    ids = args.only.split(",") if args.only else (NINE + (["expandRecurrence", "buildIcs", "layoutWeekSpans",
                                                          "findFreeSlots"] if args.extended else []))
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        common.die("unknown task id(s): " + ", ".join(unknown))
    if shutil.which("node") is None:
        common.die("Node.js not found (need Node 20+ on PATH)")
    work = make_workdir()
    util_src = common.read_text(os.path.join(work, "app", "src", "lib", "util.js"))
    results, passing, borrowed = [], set(), []
    try:
        for tid in ids:
            task = by_id[tid]
            missing = [d for d in task["deps"] if d not in passing]
            if missing and args.reference:
                for d in missing:
                    borrow(args.reference, work, d)
                    borrowed.append(d)
                missing = []
            if missing:
                common.say("- %s: blocked (needs %s)" % (tid, ", ".join(missing)))
                results.append({"id": tid, "status": "blocked by " + ", ".join(missing), "first_attempt_pass": False,
                                "attempts": 0, "best_passed": 0, "total": 0, "errors": 0})
                continue
            common.say("- %s" % tid)
            r = solve(args, task, by_id, work, util_src)
            results.append(r)
            if r["status"] == "passed":
                passing.add(tid)
            common.say("  [%s] => %s after %d attempt(s)" % (tid, r["status"].upper(), r["attempts"]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    n = len(results)
    final = sum(r["status"] == "passed" for r in results)
    first_n = sum(bool(r["first_attempt_pass"]) for r in results)
    attempts = sum(r["attempts"] for r in results)
    errors = sum(r.get("errors", 0) for r in results)
    common.say("CAL: first-attempt pass %d/%d   final pass %d/%d   (%d request errors / %d attempts)"
               % (first_n, n, final, n, errors, attempts))
    payload = {"tasks": n, "first_attempt_pass": first_n, "final_pass": final, "request_errors": errors,
               "attempts_total": attempts, "max_attempts": MAX_ATTEMPTS, "critic": "same model",
               "gold_deps_borrowed": sorted(set(borrowed)), "results": results}
    rec_args = argparse.Namespace(**{**vars(args), "reference": "<private reference dir>" if args.reference else None})
    common.save_results(rec_args, "CAL", payload)
    if attempts and errors * 4 > attempts:
        print("INVALID: more than a quarter of the requests failed", file=sys.stderr)
        return 1
    return 0


def selftest(args, tasks):
    ck = common.Checks()
    by_id = {t["id"]: t for t in tasks}
    ck("tasks.toml has 13 tasks", len(tasks) == 13)
    ck("the nine headline tasks exist", all(i in by_id for i in NINE))
    ck("every task has a test file", all(os.path.exists(os.path.join(HERE, "tests", t["id"] + ".test.js")) for t in tasks))
    ck("deps point at earlier tasks", all(d in [x["id"] for x in tasks[:i]] for i, t in enumerate(tasks) for d in t["deps"]))
    ck("answer key is not shipped", not os.path.isdir(os.path.join(HERE, "reference")))
    ck("extract_code: fenced js", extract_code("hi\n```js\nexport function a(){}\n```\nbye") == "export function a(){}\n")
    ck("extract_code: unfenced", extract_code("export function a(){}") == "export function a(){}\n")
    ck("extract_code: none", extract_code("no code here") is None)
    ck("extract_code: think block ignored", extract_code("<think>```js\nexport function x(){}\n```</think>\n```js\nexport function y(){}\n```") == "export function y(){}\n")
    ck("node present", shutil.which("node") is not None)
    work = make_workdir()
    try:
        ck("stub fails its tests (grader is not vacuous)", not _stub_ok(work, "startOfWeek"))
        out = run_tests(work, "startOfWeek")["output"]
        ck("trim_failure keeps a summary line", "Summary:" in trim_failure(out, FEEDBACK_CHARS))
        if args.reference:
            bad = []
            for t in tasks:
                if not os.path.exists(os.path.join(args.reference, t["id"] + ".js")):
                    bad.append(t["id"] + " (missing)")
                    continue
                borrow(args.reference, work, t["id"])
                r = run_tests(work, t["id"])
                common.say("  gold %-16s %d/%d" % (t["id"], r["passed"], r["total"]))
                if not r["ok"]:
                    bad.append(t["id"])
            ck("gold reference passes every task", not bad)
        else:
            common.say("note: gold run skipped (no --reference DIR with the private answer key)")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return ck.finish()


def _stub_ok(work, tid):
    with open(gen_path(work, tid), "w", encoding="utf-8") as f:
        f.write("export function %s() { throw new Error('stub'); }\n" % tid)
    return run_tests(work, tid)["ok"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_common_args(ap)
    ap.add_argument("--max-tokens", type=int, default=4500, help="coder cap, thinking + answer together (11000 for the think arm)")
    ap.add_argument("--only", help="comma-separated task ids")
    ap.add_argument("--extended", action="store_true", help="add the four extension tasks (13 total)")
    ap.add_argument("--reference", help="dir of reference <task>.js files (private answer key): borrow failed deps, run the gold selftest")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    tasks = load_tasks()
    if args.selftest:
        sys.exit(selftest(args, tasks))
    sys.exit(run_model(args, tasks))


if __name__ == "__main__":
    main()
