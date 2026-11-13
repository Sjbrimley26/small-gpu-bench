#!/usr/bin/env python3
"""TOOL: does the model pick the right tool from 4 tools, and from 20 tools that include a meta-dispatcher?

One task ("run `df -h /data` and report the Avail value") is offered with the same 4 real tools
(terminal, read_file, write_file, search_files), then with a 20-tool set: those 4 plus 16 filler tools, one of
which is a `tool_call` meta-dispatcher ("invoke deferred tools with a `calls` array"). The lab saw 3 of 4
small models pick the dispatcher over `terminal` once the set was wide (notebook 23.4); this script lets you
check your model. The score is the first tool call's name; a call-shaped JSON in the message text instead of a
native tool_calls entry is recorded as `textual_call` (a parser/template problem, not a selection problem).

Only the OpenAI-compatible /v1/chat/completions path is used. (The lab's original probe also ran ollama's
native /api/chat; that comparison is not reproduced here.)

  python3 tests/tool_probe.py --selftest
  BASE_URL=... MODEL=... python3 tests/tool_probe.py [--runs 3] [--sets 4,20]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def _tool(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


S = {"type": "string"}
TOOLS = [
    _tool("terminal", "Run a shell command and return its output.",
          {"command": {"type": "string", "description": "The shell command to execute."}}, ["command"]),
    _tool("read_file", "Read a text file and return its contents.", {"path": S}, ["path"]),
    _tool("write_file", "Write content to a file, replacing any existing content.", {"path": S, "content": S},
          ["path", "content"]),
    _tool("search_files", "Search file contents by regular expression.", {"pattern": S, "path": S}, ["pattern"]),
]
FILLER = [("tool_search", "Search deferred tools by keyword query."),
          ("tool_describe", "Load the full JSON schema for deferred tools."),
          ("tool_call", "Invoke deferred tools with a `calls` array of {name, arguments}."),
          ("web_search", "Search the web for a query."),
          ("web_extract", "Extract markdown from a URL."),
          ("skill_view", "Load a skill by name."),
          ("skills_list", "List installed skills."),
          ("vision_analyze", "Load an image into the conversation."),
          ("text_to_speech", "Convert text to speech."),
          ("browser_exec", "Drive the browser with python code."),
          ("computer_use", "Drive the desktop."),
          ("session_search", "Recall past conversations."),
          ("todo_list", "Track a task list."),
          ("cronjob_manage", "Manage scheduled jobs."),
          ("process_manage", "Poll or kill background processes."),
          ("delegate_task", "Spawn subagents.")]
WIDE = TOOLS + [_tool(n, d, {"query": S}, []) for n, d in FILLER]

PROMPT = "Run the shell command `df -h /data` and report the Avail value for /data."
SYSTEM = ("You are a subagent. Complete exactly one task, then report it. "
          "Use the tools provided; do not describe what you would do, call the tool.")
SETS = {"4": TOOLS, "20": WIDE}


def classify(reply):
    calls = reply.get("tool_calls") or []
    first = ((calls[0].get("function") or {}).get("name") if calls else None)
    text = reply.get("content") or ""
    textual = (not calls) and '"name"' in text and ('"command"' in text or '"arguments"' in text)
    args = (calls[0].get("function") or {}).get("arguments") if calls else None
    return {"n_calls": len(calls), "first_tool": first, "right_tool": first == "terminal",
            "arguments": args if isinstance(args, str) else json.dumps(args), "textual_call": bool(textual),
            "content_head": text[:200]}


def selftest():
    c = common.Checks()
    c("4-tool set has 4 tools, wide set has 20", (len(TOOLS), len(WIDE)) == (4, 20))
    c("tool names are unique", len({t["function"]["name"] for t in WIDE}) == 20)
    c("exactly one meta-dispatcher named tool_call", sum(t["function"]["name"] == "tool_call" for t in WIDE) == 1)
    good = {"tool_calls": [{"function": {"name": "terminal", "arguments": '{"command":"df -h /data"}'}}]}
    meta = {"tool_calls": [{"function": {"name": "tool_call", "arguments": "{}"}}]}
    text = {"content": '{"name": "terminal", "arguments": {"command": "df -h /data"}}'}
    c("native terminal call is right", classify(good)["right_tool"])
    c("meta-dispatcher pick is wrong", not classify(meta)["right_tool"])
    t = classify(text)
    c("call-shaped text is flagged textual, not right", t["textual_call"] and not t["right_tool"])
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--runs", type=int, default=1, help="repeats per tool set")
    ap.add_argument("--sets", default="4,20", help="comma list from 4,20")
    ap.add_argument("--max-tokens", type=int, default=300)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    rows = []
    for key in [x for x in a.sets.split(",") if x]:
        for i in range(a.runs):
            r = common.chat(a, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": PROMPT}],
                            max_tokens=a.max_tokens, temperature=a.temperature, seed=100 + i, tools=SETS[key])
            g = classify(r)
            rows.append({"tools": int(key), "run": i, "s": r["s"], "error": r.get("error"), **g})
            common.say("tools=%-2s run%d first_tool=%s right=%s textual=%s %.1fs %s" % (
                key, i, g["first_tool"], g["right_tool"], g["textual_call"], r["s"], r.get("error") or ""))
    summary = {k: "%d/%d" % (sum(x["right_tool"] for x in rows if x["tools"] == int(k)),
                             sum(1 for x in rows if x["tools"] == int(k)))
               for k in a.sets.split(",") if k}
    common.say("TOOL right tool: %s" % ", ".join("%s tools %s" % (k, v) for k, v in summary.items()))
    common.save_results(a, "TOOL", {"summary": summary, "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
