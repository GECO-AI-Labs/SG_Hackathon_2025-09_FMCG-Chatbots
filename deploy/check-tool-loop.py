#!/usr/bin/env python3
"""Offline checks for Clarity's tool-calling loop.

No network, no dataset, no container. Runs in about a second and covers the
failure that put raw transport markers into a user's answer:

  round 3 dropped the tool schemas while the conversation still held
  function_call items, so the model had calls it could not express and wrote
  the call syntax into the answer as plain text.

Run it from the repo root or inside a built image:

    python3 deploy/check-tool-loop.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / "apps" / "clarity", Path("/app"), HERE.parent):
    if (candidate / "llm.py").is_file():
        sys.path.insert(0, str(candidate))
        break
else:
    sys.exit("Could not find llm.py. Run from the repo root or inside the image.")

from llm import (  # noqa: E402
    ChatClient, LLMError, Provider, _LeakGuard, to_responses_input,
)

FAILS: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ok   " if condition else "  FAIL ") + name + (f"  {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


class FakeResponse:
    """Just enough of requests.Response for the SSE readers."""

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def close(self):
        pass


def sse_lines(events):
    out = []
    for ev in events:
        out.append("data: " + json.dumps(ev))
        out.append("")
    out.append("data: [DONE]")
    return out


# --------------------------------------------------------------------------
print("\nleak guard")

# Lifted verbatim from the answer that shipped to the commercial team.
LEAKED = (
    "I am pulling the Q1 2026 category mix so I can turn this into a "
    "practical campaign recommendation rather than a generic one."
    "to=multi_tool_use.parallel json"
)
guard = _LeakGuard()
try:
    for piece in [LEAKED[i:i + 7] for i in range(0, len(LEAKED), 7)]:
        guard.feed(piece)
    check("stops the leaked answer from the incident", False, "nothing raised")
except LLMError:
    check("stops the leaked answer from the incident", True)

# A marker split across two deltas must still be caught.
guard = _LeakGuard()
try:
    guard.feed("x" * 200 + "to=funct")
    guard.feed("ions.breakdown")
    check("catches a marker split across deltas", False, "nothing raised")
except LLMError:
    check("catches a marker split across deltas", True)

# A real answer must pass through byte for byte.
CLEAN = (
    "Cashews led Q1 2026 at SGD 13,377.30, 24.8% of net sales. Macadamia "
    "carries the best margin at 46.3%. Put the CNY spend behind honey "
    "cashews and use macadamia gift packs to lift basket value."
)
guard = _LeakGuard()
got = "".join(guard.feed(c) for c in CLEAN) + guard.flush()
check("passes a real answer through unchanged", got == CLEAN, repr(got[:60]))


# --------------------------------------------------------------------------
print("\npayload")

provider = Provider(
    name="primary",
    api_url="https://example.services.ai.azure.com/openai/v1/responses",
    api_key="x",
    model="SG-Geco-AI-General-Resources-Foundry-gpt-5.4",
)
client = ChatClient(providers=[provider], log=False)

check("URL selects the Responses protocol", provider.protocol == "responses")
check("deployment name is read as a reasoning model",
      provider.profile.supports_reasoning_effort)

TOOLS = [{"type": "function", "function": {
    "name": "breakdown", "description": "d",
    "parameters": {"type": "object", "properties": {}}}}]

HISTORY = [
    {"role": "system", "content": "s"},
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "breakdown", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "name": "breakdown",
     "content": "{}"},
]

final = client._payload(
    provider, HISTORY, stream=True, temperature=None, max_tokens=100,
    tools=TOOLS, tool_choice="none", reasoning_effort="medium",
    verbosity="medium", parallel_tool_calls=False,
)

# This is the regression test. The old final round sent tools=None.
has_calls = any(i.get("type") == "function_call" for i in final["input"])
check("history still carries a function_call item", has_calls)
check("final round keeps the tool schemas", bool(final.get("tools")))
check("final round forbids new calls", final.get("tool_choice") == "none")
check("parallel calls are off", final.get("parallel_tool_calls") is False)
check("reasoning is requested back encrypted",
      final.get("store") is False
      and "reasoning.encrypted_content" in (final.get("include") or []))


# --------------------------------------------------------------------------
print("\nreasoning replay")

WITH_REASONING = [
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": None,
     "reasoning_items": [
         {"type": "reasoning", "id": "rs_1", "encrypted_content": "abc"},
         {"type": "reasoning", "id": "rs_bare"},
     ],
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "breakdown", "arguments": "{}"}}]},
]
_, items = to_responses_input(WITH_REASONING)
kinds = [i.get("type") or i.get("role") for i in items]
ids = [i.get("id") for i in items if i.get("type") == "reasoning"]

check("encrypted reasoning is replayed", "rs_1" in ids)
check("a bare reasoning id is skipped", "rs_bare" not in ids)
check("reasoning comes before the call it produced",
      kinds.index("reasoning") < kinds.index("function_call"), str(kinds))


# --------------------------------------------------------------------------
print("\nstream reader")

events = list(ChatClient._read_responses_stream(FakeResponse(sse_lines([
    {"type": "response.output_item.done",
     "item": {"type": "reasoning", "id": "rs_1", "encrypted_content": "abc"}},
    {"type": "response.output_item.added",
     "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1",
              "name": "breakdown"}},
    {"type": "response.function_call_arguments.delta",
     "item_id": "fc_1", "delta": '{"dimension":"category"}'},
    {"type": "response.output_item.done",
     "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1",
              "name": "breakdown", "arguments": '{"dimension":"category"}'}},
    {"type": "response.completed", "response": {"status": "completed"}},
]))))

tool_events = [e for e in events if e["type"] == "tool"]
check("the tool event is emitted", len(tool_events) == 1)
if tool_events:
    ev = tool_events[0]
    check("reasoning items ride along with it",
          [i.get("id") for i in ev.get("reasoning_items", [])] == ["rs_1"],
          str(ev.get("reasoning_items")))
    check("the call survives intact",
          ev["calls"][0]["function"]["name"] == "breakdown"
          and ev["calls"][0]["id"] == "call_1")

# Text that carries a marker must stop the turn, not render.
try:
    list(ChatClient._read_responses_stream(FakeResponse(sse_lines([
        {"type": "response.output_text.delta",
         "delta": "Here is the mix." + "to=functions.sales_trend json"},
        {"type": "response.completed", "response": {"status": "completed"}},
    ]))))
    check("a leaking stream is stopped", False, "nothing raised")
except LLMError:
    check("a leaking stream is stopped", True)


# --------------------------------------------------------------------------
print("\nduplicate detection")

sys.path.insert(0, str(HERE.parent / "apps" / "clarity"))
try:
    os.environ.setdefault("DATA_DIR", "data/Team_Cashew_Synthetic_Data")
    from app import canonical_args  # noqa: E402
except Exception as exc:                                        # noqa: BLE001
    print(f"  skip  app.py did not import ({exc.__class__.__name__}); "
          "canonical_args not checked")
else:
    a = '{"dimension":"category","start":"2026-01-01"}'
    b = '{"start":"2026-01-01", "dimension":"category"}'
    c = '{"dimension":"channel","start":"2026-01-01"}'
    check("key order does not hide a repeat", canonical_args(a) == canonical_args(b))
    check("a different query is not a repeat", canonical_args(a) != canonical_args(c))


# --------------------------------------------------------------------------
print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for name in FAILS:
        print(f"  - {name}")
    sys.exit(1)
print("All checks passed.")
