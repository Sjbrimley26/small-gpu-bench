# §23.4 Why the tool-call failures happened: the wire path is innocent, the tool COUNT is the culprit

> Excerpt of §23.4 of a private lab notebook; paths, seat names and hosts are generalised.

Two explanations were worth separating: (A) the client reaches a local endpoint over `/v1/chat/completions`,
while the harness used Ollama's native `/api/chat` — if these models emit tool calls that only the native path
parses, the path is at fault. (B) A real child advertises ~20 tools including the `tool_*` meta-dispatchers,
versus 4 in the harness — if a model can pick one of four tools but not one of twenty, selection is at fault.

A probe isolates both with the same 4-tool schema on each path, then the same models against a 20-tool set
including a `tool_call` meta-tool shaped like the real one:

| model | native `/api/chat`, 4 tools | `/v1` 4 tools | native, **20 tools** |
|---|---|---|---|
| `granite4:micro` | `terminal` ✓ | `terminal` ✓ | **`tool_call`** ✗ |
| `granite4:7b-a1b-h` | `terminal` ✓ | `terminal` ✓ | **`tool_call`** ✗ |
| `granite4:3b-h` | `terminal` ✓ | `terminal` ✓ | **`tool_call`** ✗ |
| `llama3.2:3b` | `terminal` ✓ | `terminal` ✓ | **`terminal` ✓** |

- **A is refuted.** Every model calls `terminal` correctly on *both* paths with 4 tools. `/v1` even parsed the
  arguments properly (`{"command":"df -h <path>"}`), so the OpenAI-compatible path is not the problem and the
  client's transport choice is fine.
- **B is confirmed — and it has a name.** With the wide set, **3 of 4 models pick the meta-tool `tool_call` over
  `terminal`**, which is exactly what the live children did. The meta-dispatcher is a trap for small models: its
  name and one-line description make it look like "the way to call tools", and it takes precedence over the real
  ones. `llama3.2:3b` is the only model that resists, which fits its tool-use training.

**Caveat, stated plainly:** the probe explains the *wrong-tool* failures (runs 1 and 4), not the *textual-call*
failures (runs 2 and 3) — those two models emit native calls correctly in the probe and did not reproduce live.
So the live child environment is harsher than the mock in a way the mock does not capture: the real system prompt
measured **4,098 tokens** against a mock prompt of ~600, inside an 8192 window that the server's log shows
context-shifting (`n_ctx_slot = 8192`, `restored context checkpoint … pos_min = 4093`). The remaining untested
lever is therefore the **window**: raising the context length to 32768 and re-running `llama3.2:3b` (the only
model that selects correctly) is the cheap decisive follow-up. Not run at time of writing.

**Conclusion for the architecture question:** a local subagent is mechanically possible on this box — routing
verified, GPU-resident, no new credentials — but every installed small model fails the actual job, in three
different ways, while reporting success. The gap is not context and not the transport; it is *tool selection under
a wide, meta-tool-laden toolset*.
