"""Clarity: the internal analytics assistant.

Answers are streamed over server-sent events. A turn runs as a loop: the model
streams, and if it asks for data instead of answering, the requested queries
run against pandas and the results go back in. Once it stops asking, the answer
streams straight through to the browser.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Dict, Iterator, List

import numpy as np
import pandas as pd
from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

import config
from analytics import TOOL_NAMES, TOOL_SCHEMAS, Analytics
from datastore import DataStore
from llm import ChatClient, LLMError, NoProvidersConfigured
from prompts import build_system_prompt

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app, supports_credentials=True)

STORE = DataStore(config.DATA_DIR)
ANALYTICS = Analytics(STORE)
CLIENT = ChatClient()
SYSTEM_PROMPT = build_system_prompt(config.COMPANY_NAME, STORE.schema_note())

# Conversation history per browser session. Swap for Redis to run more than
# one worker process without losing context between requests.
SESSIONS: Dict[str, List[Dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def to_jsonable(value: Any) -> Any:
    """Make pandas and numpy scalars safe for json.dumps."""
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if value is pd.NaT or (isinstance(value, float) and value != value):
        return None
    return value


def sse(kind: str, **payload: Any) -> str:
    return f"data: {json.dumps({'type': kind, **payload}, default=str)}\n\n"


def run_tool(name: str, arguments: str) -> Dict[str, Any]:
    """Execute one analytics tool. Failures come back as data, not exceptions,
    so the model can correct its own arguments and try again."""
    if name not in TOOL_NAMES:
        return {"error": f"Unknown tool '{name}'.", "available": TOOL_NAMES}
    try:
        kwargs = json.loads(arguments) if arguments and arguments.strip() else {}
    except json.JSONDecodeError as exc:
        return {"error": f"Could not parse arguments: {exc}"}
    if not isinstance(kwargs, dict):
        return {"error": "Arguments must be a JSON object."}

    try:
        return to_jsonable(getattr(ANALYTICS, name)(**kwargs))
    except TypeError as exc:
        return {"error": f"Bad arguments for {name}: {exc}"}
    except Exception as exc:                                   # noqa: BLE001
        return {"error": f"{name} failed: {exc}"}


def canonical_args(arguments: str) -> str:
    """Key an argument string by meaning, so key order or spacing cannot hide
    a repeat of a query that already ran this turn."""
    try:
        parsed = json.loads(arguments) if arguments and arguments.strip() else {}
    except json.JSONDecodeError:
        return (arguments or "").strip()
    return json.dumps(parsed, sort_keys=True, default=str)


def session_id() -> str:
    return request.cookies.get("clarity_sid") or secrets.token_hex(16)


# ---------------------------------------------------------------------------
# the turn
# ---------------------------------------------------------------------------

def run_turn(user_message: str, history: List[Dict[str, Any]]) -> Iterator[str]:
    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    answer: List[str] = []
    # Every query already run this turn, so a repeat costs nothing and the
    # model gets told to use the result it already has.
    seen_calls: set = set()
    started = time.time()

    try:
        for round_no in range(config.MAX_TOOL_ROUNDS + 1):
            # The tool schemas stay in the payload on every round, including
            # the last. Dropping them while the conversation still carries
            # function_call items leaves the model holding calls it has no way
            # to express, and it writes the call syntax into the answer as
            # plain text. tool_choice="none" forbids new calls and keeps the
            # function namespace defined.
            final_round = round_no == config.MAX_TOOL_ROUNDS
            tool_choice = "none" if final_round else "auto"

            chunk: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            reasoning_items: List[Dict[str, Any]] = []

            for event in CLIENT.stream(
                messages,
                tools=TOOL_SCHEMAS,
                tool_choice=tool_choice,
                parallel_tool_calls=config.PARALLEL_TOOL_CALLS,
                temperature=config.TEMPERATURE,
                max_tokens=config.MAX_TOKENS,
                reasoning_effort=config.REASONING_EFFORT,
                verbosity=config.VERBOSITY,
            ):
                if event["type"] == "text":
                    chunk.append(event["text"])
                    yield sse("delta", text=event["text"])
                elif event["type"] == "tool":
                    tool_calls = event["calls"]
                    reasoning_items = event.get("reasoning_items") or []
                elif event["type"] == "usage":
                    yield sse("usage", usage=event["usage"])

            if not tool_calls:
                answer.extend(chunk)
                break

            # The model asked for data. Anything it said first was preamble,
            # so clear it from the bubble and show the queries instead.
            if chunk:
                yield sse("retract")

            messages.append({
                "role": "assistant",
                "content": "".join(chunk) or None,
                "tool_calls": tool_calls,
                # Handed back next round so the model keeps the plan it just
                # made instead of re-deriving it and reissuing the same
                # queries until the rounds run out.
                "reasoning_items": reasoning_items,
            })

            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                yield sse("tool", name=name, arguments=raw_args)

                key = (name, canonical_args(raw_args))
                if key in seen_calls:
                    result = {"error": (
                        "Duplicate call. This exact query already ran earlier "
                        "in this turn and its result is above. Use that "
                        "result, or change the arguments."
                    )}
                else:
                    seen_calls.add(key)
                    result = run_tool(name, raw_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": json.dumps(result, default=str),
                })
                yield sse("tool_done", name=name, ok="error" not in result)

        text = "".join(answer).strip()
        if not text:
            text = ("I could not produce an answer from the data for that one. "
                    "Try narrowing the question to a period or a channel.")
            yield sse("delta", text=text)

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": text})
        del history[: max(0, len(history) - config.HISTORY_TURNS * 2)]

        yield sse("done", elapsed_s=round(time.time() - started, 2))

    except NoProvidersConfigured:
        yield sse("error", message=(
            "No language model is configured. Set AZURE_API_URL, AZURE_API_KEY "
            "and AZURE_DEPLOYMENT in your .env file."
        ))
    except LLMError as exc:
        yield sse("retract")
        yield sse("error", message=f"The model call failed: {exc}")
    except Exception as exc:                                   # noqa: BLE001
        yield sse("error", message=f"Unexpected failure: {exc}")


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@app.get("/")
def index() -> str:
    return render_template("clarity.html", company=config.COMPANY_NAME)


@app.post("/chat")
def chat() -> Response:
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    if not STORE.ready:
        return jsonify({"error": f"Dataset not loaded from {config.DATA_DIR}"}), 503

    sid = session_id()
    history = SESSIONS.setdefault(sid, [])

    response = Response(
        run_turn(message, history),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx and similar proxies buffering the stream into one blob.
            "X-Accel-Buffering": "no",
        },
    )
    response.set_cookie("clarity_sid", sid, httponly=True, samesite="Lax",
                        secure=config.COOKIE_SECURE)
    return response


@app.post("/reset")
def reset() -> Response:
    SESSIONS.pop(session_id(), None)
    return jsonify({"ok": True})


@app.get("/healthz")
def healthz() -> Response:
    """Liveness only.

    This endpoint is reachable from the public internet, so it says whether
    the service is up and nothing else. Deployment names, the Azure endpoint,
    table names and paths are all reconnaissance material. Set HEALTH_DETAIL=1
    temporarily when diagnosing, and turn it off again.
    """
    ready = STORE.ready and CLIENT.enabled
    if not config.HEALTH_DETAIL:
        return jsonify({"ok": ready})

    first, last = STORE.date_range()
    return jsonify({
        "ok": ready,
        "app": config.APP_NAME,
        "data_dir": str(config.DATA_DIR),
        "tables": {name: len(frame) for name, frame in STORE.tables.items()},
        "date_range": [str(first), str(last)] if first is not None else None,
        "load_errors": STORE.errors,
        "llm_enabled": CLIENT.enabled,
        "providers": CLIENT.describe(),
        "tools": TOOL_NAMES,
    })


if __name__ == "__main__":
    print(f"[{config.APP_NAME}] data: {config.DATA_DIR}")
    print(f"[{config.APP_NAME}] tables: {sorted(STORE.tables)}")
    print(f"[{config.APP_NAME}] providers: {[p['name'] for p in CLIENT.describe()]}")
    print(f"[{config.APP_NAME}] http://127.0.0.1:{config.PORT}")
    app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG, threaded=True)
