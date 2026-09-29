"""Nibbles: the customer-facing assistant.

A turn runs catalogue matching first and pushes the matched products to the
browser immediately, so the shopper sees real items within milliseconds. The
written reply then streams in beside them.
"""

from __future__ import annotations

import csv
import json
import secrets
import time
from typing import Any, Dict, Iterator, List

from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

import config
import matching
import prompts
from catalog import Catalog
from llm import ChatClient, LLMError, NoProvidersConfigured
from sessions import SessionStore

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app, supports_credentials=True)

CATALOG = Catalog(config.DATA_DIR, config.CURRENCY)
CLIENT = ChatClient()
SESSIONS = SessionStore(config.HISTORY_TURNS)
SYSTEM_PROMPT = prompts.build_system_prompt(config.COMPANY_NAME)

FALLBACK_NO_MATCH = (
    "I could not find that one in our range. Could you tell me the product "
    "name or the pack size you are after?"
)
FALLBACK_OFFLINE = (
    "Here is what I found in our range. Ask me about any of these and I will "
    "help you pick."
)


def sse(kind: str, **payload: Any) -> str:
    return f"data: {json.dumps({'type': kind, **payload}, default=str)}\n\n"


def session_id() -> str:
    supplied = (request.get_json(silent=True) or {}).get("sid")
    return str(supplied) if supplied else request.cookies.get("nibbles_sid") \
        or secrets.token_hex(12)


def build_messages(sid: str, message: str, items: List[Dict],
                   faqs: List[Dict]) -> List[Dict[str, str]]:
    entry = SESSIONS.get(sid)
    messages: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    interest = prompts.interest_block(entry["families"], entry["styles"],
                                      entry["sizes"])
    if interest:
        messages.append({"role": "system", "content": interest})
    if items:
        messages.append({"role": "system",
                         "content": prompts.catalogue_block(items, CATALOG.price)})
    else:
        messages.append({"role": "system", "content": prompts.NO_MATCH_NOTE})
    if faqs:
        messages.append({"role": "system", "content": prompts.policy_block(faqs)})

    messages.extend(SESSIONS.history(sid))
    messages.append({"role": "user", "content": message})
    return messages


def run_turn(sid: str, message: str) -> Iterator[str]:
    started = time.time()
    families, styles, intents = matching.extract(message)
    grams = matching.parse_grams(matching.to_english(message)) or \
        matching.parse_grams(message)

    items = CATALOG.search(message, limit=config.MAX_ITEMS) \
        if matching.looks_like_product_question(message) else []
    faqs = CATALOG.faq_matches(message)

    # Show the products before the model has written a word.
    if items:
        yield sse("items", items=items)
        SESSIONS.remember_interest(sid, families, styles, grams, items)

    SESSIONS.add_turn(sid, "user", message)
    reply: List[str] = []

    try:
        for event in CLIENT.stream(
            build_messages(sid, message, items, faqs),
            temperature=config.TEMPERATURE,
            max_tokens=config.MAX_TOKENS,
            reasoning_effort=config.REASONING_EFFORT,
            verbosity=config.VERBOSITY,
        ):
            if event["type"] == "text":
                reply.append(event["text"])
                yield sse("delta", text=event["text"])
    except (LLMError, NoProvidersConfigured) as exc:
        app.logger.warning("Nibbles fell back to catalogue only: %s", exc)
        # A shopper should still get the products even when the model is down.
        text = FALLBACK_OFFLINE if items else FALLBACK_NO_MATCH
        reply = [text]
        yield sse("delta", text=text)

    text = "".join(reply).strip() or (FALLBACK_OFFLINE if items else FALLBACK_NO_MATCH)
    SESSIONS.add_turn(sid, "assistant", text)

    entry = SESSIONS.get(sid)
    suggestions = CATALOG.suggest(
        entry["families"], entry["styles"],
        exclude={item["name"] for item in items}, limit=3,
    ) if (entry["families"] or entry["styles"]) else []
    if suggestions:
        yield sse("suggestions", items=suggestions)

    yield sse("done", elapsed_s=round(time.time() - started, 2))


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@app.get("/")
def index() -> str:
    return render_template("nibbles.html", company=config.COMPANY_NAME)


@app.post("/chat")
def chat() -> Response:
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400

    sid = session_id()
    response = Response(
        run_turn(sid, message),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )
    response.set_cookie("nibbles_sid", sid, httponly=True, samesite="Lax",
                        secure=config.COOKIE_SECURE)
    return response


@app.post("/lead")
def lead() -> Response:
    """Capture a callback request. Written to a file the team can export."""
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    contact = str(payload.get("email") or payload.get("phone") or "").strip()
    if not name or not contact:
        return jsonify({"ok": False, "error": "Name and a contact are required."}), 400

    config.LEADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    is_new = not config.LEADS_FILE.exists()
    with config.LEADS_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(["received_at", "name", "email", "phone", "message"])
        writer.writerow([
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            name,
            str(payload.get("email", "")).strip(),
            str(payload.get("phone", "")).strip(),
            str(payload.get("message", "")).strip()[:500],
        ])
    return jsonify({"ok": True})


@app.post("/reset")
def reset() -> Response:
    SESSIONS.clear(session_id())
    return jsonify({"ok": True})


@app.get("/healthz")
def healthz() -> Response:
    return jsonify({
        "ok": bool(len(CATALOG.df)) and CLIENT.enabled,
        "app": config.APP_NAME,
        "company": config.COMPANY_NAME,
        "data_dir": str(config.DATA_DIR),
        "products": int(len(CATALOG.df)),
        "faqs": int(len(CATALOG.faqs)),
        "confidential_columns_dropped": CATALOG.dropped_columns,
        "active_sessions": SESSIONS.active,
        "llm_enabled": CLIENT.enabled,
        "providers": CLIENT.describe(),
    })


if __name__ == "__main__":
    print(f"[{config.APP_NAME}] {len(CATALOG.df)} products, {len(CATALOG.faqs)} FAQs")
    print(f"[{config.APP_NAME}] withheld from customers: {CATALOG.dropped_columns}")
    print(f"[{config.APP_NAME}] http://127.0.0.1:{config.PORT}")
    app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG, threaded=True)
