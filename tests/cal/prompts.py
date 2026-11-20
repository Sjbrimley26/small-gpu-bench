"""Prompt builders. Kept short and concrete because small models copy what they see."""
from __future__ import annotations

CODER_SYSTEM = """You are a careful JavaScript developer. You write ONE small ES module at a time.
Rules:
- Output exactly one ```js code block and nothing else after it.
- The module must `export function NAME(...)` with the exact signature given.
- Plain modern JavaScript (ES2022). No dependencies, no TypeScript, no console.log, no top-level code that runs.
- You may import ONLY from the paths listed under "Allowed imports". Use those helpers instead of rewriting them.
- Never mutate input arguments.
- Make every test in the test file pass. Read the tests carefully: they define the exact output format."""

CRITIC_SYSTEM = """You review a failed attempt at a small JavaScript function.
Given the spec, the code and the failing test output, explain what is wrong.
Answer with at most 5 short bullet points, under 150 words total. Do not think out loud. Name the exact line or expression to change and what it should be.
Do not write the whole function. Do not repeat the spec."""

REVIEWER_SYSTEM = """You check a JavaScript function that already passes its tests.
List spec requirements the code does NOT follow (for example mutating inputs, wrong rounding, unescaped HTML).
Reply with "OK" if you find nothing. Otherwise at most 5 short bullet points."""


def _imports_block(task: dict, util_src: str, dep_tasks: list[dict]) -> str:
    lines = ["## Allowed imports", "", "From '../lib/util.js' (already written, do not copy it):", "```js", util_src.strip(), "```"]
    for d in dep_tasks:
        lines += ["", f"From './{d['id']}.js' (already written and tested):", f"`{d['signature']}`"]
        first = d["spec"].strip().splitlines()[0]
        lines.append(f"// {first}")
    return "\n".join(lines)


def coder_first(task: dict, util_src: str, dep_tasks: list[dict], test_src: str) -> list[dict]:
    user = f"""# Task: write app/src/gen/{task['id']}.js

Signature:
```js
{task['signature']}
```

## Spec
{task['spec'].strip()}

{_imports_block(task, util_src, dep_tasks)}

## Tests your module must pass (tests/{task['id']}.test.js)
```js
{test_src.strip()}
```

Write the complete module now, starting with its import lines (if any)."""
    return [{"role": "system", "content": CODER_SYSTEM}, {"role": "user", "content": user}]


def coder_retry(history: list[dict], last_reply: str, failure: str, diagnosis: str | None) -> list[dict]:
    fix = "Your module failed. Test output:\n```\n" + failure.strip() + "\n```\n"
    if diagnosis:
        fix += "\nA reviewer looked at the failure and says:\n" + diagnosis.strip() + "\n"
    fix += "\nReturn the corrected complete module in one ```js block."
    return history + [{"role": "assistant", "content": last_reply}, {"role": "user", "content": fix}]


def critic(task: dict, code: str, failure: str) -> list[dict]:
    user = f"""## Spec for `{task['signature']}`
{task['spec'].strip()}

## Code
```js
{code.strip()}
```

## Failing test output
```
{failure.strip()}
```"""
    return [{"role": "system", "content": CRITIC_SYSTEM}, {"role": "user", "content": user}]


def reviewer(task: dict, code: str) -> list[dict]:
    user = f"""## Spec for `{task['signature']}`
{task['spec'].strip()}

## Code
```js
{code.strip()}
```"""
    return [{"role": "system", "content": REVIEWER_SYSTEM}, {"role": "user", "content": user}]


def clip(text: str, limit: int) -> str:
    """Hard cap on text pasted into another model's prompt (a rambling critic can't eat the context)."""
    text = text.strip()
    return text if limit <= 0 or len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n(... cut)"


def coder_repair(first: list[dict], code: str, failure: str, diagnosis: str | None,
                 passed_names: list[str], failed_names: list[str], label: str) -> list[dict]:
    """Repair prompt v2: the code to fix (the best attempt so far, not necessarily the latest),
    which tests pass (keep them) and which fail, then the trimmed failure output."""
    fix = f"Here is {label}. Fix it.\n```js\n{code.strip()}\n```\n"
    if passed_names or failed_names:
        n = len(passed_names) + len(failed_names)
        fix += f"\nIt passes {len(passed_names)}/{n} tests.\n"
        if failed_names:
            fix += "Failing:\n" + "\n".join(f"- {t}" for t in failed_names[:20]) + "\n"
        if passed_names:
            fix += "Passing (keep these passing):\n" + "\n".join(f"- {t}" for t in passed_names[:20]) + "\n"
    fix += "\nTest output:\n```\n" + failure.strip() + "\n```\n"
    if diagnosis:
        fix += "\nA reviewer looked at the failure and says:\n" + diagnosis.strip() + "\n"
    fix += "\nReturn the corrected complete module in one ```js block. Declare each variable only once."
    return first + [{"role": "user", "content": fix}]
