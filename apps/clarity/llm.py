"""Chat client for Azure AI Foundry and Azure OpenAI.

Handles the three things that break naive OpenAI-style clients on Azure:

1. Route shapes differ. Foundry exposes ``/models/chat/completions`` while
   Azure OpenAI exposes ``/openai/v1/chat/completions`` (and the older
   ``/openai/deployments/{name}/chat/completions?api-version=``). The route is
   derived from the configured URL so operators only paste what the portal
   gives them.

2. Payload shapes differ per model family. GPT-5 and the o-series reject
   ``max_tokens`` and reject any ``temperature`` other than the default, and
   they accept ``reasoning_effort`` and ``verbosity`` that older models reject.

3. Azure changes the rules without warning. When the service rejects a
   parameter, the client reads the rejection, drops or renames the offending
   field, remembers the correction for the rest of the process, and retries.
   A deployment upgrade therefore costs one wasted request, not an outage.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

__all__ = [
    "LLMError",
    "NoProvidersConfigured",
    "Provider",
    "ChatClient",
    "load_providers",
]


class LLMError(RuntimeError):
    """Raised when every configured provider fails."""


class NoProvidersConfigured(LLMError):
    """Raised when no provider has both a URL and a key."""


# --------------------------------------------------------------------------
# Model parameter profiles
# --------------------------------------------------------------------------

# Reasoning models: no temperature, max_completion_tokens, reasoning_effort.
_REASONING = re.compile(
    r"(?:^|[-_/])(?:gpt-?5(?!-chat)|o[1345](?:-|$)|o4-mini)", re.IGNORECASE
)
# GPT-5-chat and GPT-4.1+ take max_completion_tokens but keep temperature.
_NEW_TOKEN_PARAM = re.compile(r"(?:^|[-_/])(?:gpt-?5|gpt-?4\.1|gpt-?4o)", re.IGNORECASE)


@dataclass(frozen=True)
class ModelProfile:
    """Which payload fields a given deployment will accept."""

    token_param: str = "max_tokens"
    responses_token_param: str = "max_output_tokens"
    supports_temperature: bool = True
    supports_top_p: bool = True
    supports_reasoning_effort: bool = False
    supports_verbosity: bool = False

    @classmethod
    def for_model(cls, model: str) -> "ModelProfile":
        name = model or ""
        if _REASONING.search(name):
            return cls(
                token_param="max_completion_tokens",
                supports_temperature=False,
                supports_top_p=False,
                supports_reasoning_effort=True,
                supports_verbosity=True,
            )
        if _NEW_TOKEN_PARAM.search(name):
            return cls(
                token_param="max_completion_tokens",
                supports_verbosity=True,
            )
        return cls()


# Fields the client will surrender when Azure objects to them.
_DROPPABLE = {
    "temperature": "supports_temperature",
    "top_p": "supports_top_p",
    "reasoning_effort": "supports_reasoning_effort",
    "verbosity": "supports_verbosity",
}


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


@dataclass
class Provider:
    """One configured endpoint."""

    name: str
    api_url: str
    api_key: str
    model: str = ""
    auth_type: str = ""          # bearer | api-key | header:X-Name
    api_version: str = ""
    deployment: str = ""
    extra_headers: Dict[str, str] = None  # type: ignore[assignment]
    profile: ModelProfile = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extra_headers is None:
            self.extra_headers = {}
        if self.profile is None:
            self.profile = ModelProfile.for_model(self.model)
        if not self.auth_type:
            host = self.api_url.lower()
            self.auth_type = "api-key" if ".azure.com" in host else "bearer"

    # -- routing ---------------------------------------------------------
    @property
    def is_azure(self) -> bool:
        return ".azure.com" in self.api_url.lower()

    @property
    def protocol(self) -> str:
        """Which wire format this endpoint speaks.

        Azure exposes GPT-5 on two incompatible APIs. Chat Completions takes
        ``messages`` and streams ``choices[].delta.content``. The Responses
        API takes ``input`` and streams typed ``response.*`` events. The
        portal hands out whichever it prefers for a given model, so the URL
        decides rather than the operator.
        """
        return "responses" if "/responses" in self.api_url.lower() else "chat"

    def endpoint(self) -> str:
        """Resolve the configured URL into a full chat-completions URL."""
        url = self.api_url.strip()
        low = url.lower()

        if low.rstrip("/").endswith("/responses"):
            return url.rstrip("/")

        if "/chat/completions" not in low:
            base = url.rstrip("/")
            low = base.lower()
            if "/openai/v1" in low or "/openai/deployments/" in low:
                # Already pointed at a specific API surface.
                url = f"{base}/chat/completions"
            elif self.deployment:
                # Explicit deployment wins: use the classic Azure OpenAI route.
                root = re.sub(r"/openai/?$", "", base, flags=re.IGNORECASE)
                url = (
                    f"{root}/openai/deployments/{self.deployment}"
                    "/chat/completions"
                )
            elif low.endswith("/openai"):
                url = f"{base}/v1/chat/completions"
            elif "services.ai.azure.com" in low:
                # Foundry model inference route.
                url = f"{base}/models/chat/completions"
            elif "openai.azure.com" in low:
                url = f"{base}/openai/v1/chat/completions"
            else:
                url = f"{base}/chat/completions"
            low = url.lower()

        # Azure's older routes require api-version; the v1 route does not.
        needs_version = (
            self.is_azure
            and self.api_version
            and "api-version=" not in low
            and "/openai/v1/" not in low
        )
        if needs_version:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api-version={self.api_version}"
        return url

    def headers(self) -> Dict[str, str]:
        head = {"Content-Type": "application/json"}
        auth = (self.auth_type or "bearer").lower()
        if auth == "api-key":
            head["api-key"] = self.api_key
        elif auth.startswith("header:"):
            head[auth.split(":", 1)[1].strip() or "Authorization"] = self.api_key
        else:
            head["Authorization"] = f"Bearer {self.api_key}"
        head.update(self.extra_headers)
        return head

    def redacted(self) -> Dict[str, str]:
        """Safe for logs and the health endpoint."""
        responses = self.protocol == "responses"
        return {
            "name": self.name,
            "endpoint": self.endpoint(),
            "model": self.model,
            "auth_type": self.auth_type,
            "protocol": self.protocol,
            # Report the field actually sent, which differs by protocol.
            "token_param": (
                self.profile.responses_token_param if responses
                else self.profile.token_param
            ),
            "reasoning": self.profile.supports_reasoning_effort,
        }


def _split_headers(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if ":" in part:
            key, value = part.split(":", 1)
            out[key.strip()] = value.strip()
    return out


def load_providers(env: Optional[Dict[str, str]] = None) -> List[Provider]:
    """Build the provider chain from environment variables.

    Primary provider uses unprefixed names (``AZURE_API_URL`` or ``API_URL``).
    Fallbacks use ``LLM_1_``, ``LLM_2_`` and so on, tried in numeric order.
    A provider missing either URL or key is skipped silently so half-filled
    placeholder blocks in an env file cost nothing.
    """
    env = dict(os.environ if env is None else env)
    indices = {
        int(m.group(1))
        for key in env
        for m in [re.match(r"^LLM_(\d+)_API_URL$", key, re.IGNORECASE)]
        if m
    }

    def pick(prefix: str, *names: str) -> str:
        for name in names:
            value = env.get(f"{prefix}{name}")
            if value and value.strip():
                return value.strip()
        return ""

    providers: List[Provider] = []
    shared_version = pick("", "AZURE_API_VERSION", "API_VERSION")

    for idx in [0] + sorted(i for i in indices if i > 0):
        prefix = "" if idx == 0 else f"LLM_{idx}_"
        url = pick(prefix, "AZURE_API_URL", "API_URL", "LLM_API_URL")
        key = pick(prefix, "AZURE_API_KEY", "API_KEY", "LLM_API_KEY")
        if not (url and key):
            continue
        model = pick(prefix, "AZURE_DEPLOYMENT", "MODEL", "LLM_MODEL")
        # Skip placeholder blocks carried over from the example file.
        if key.lower().startswith(("your_", "changeme", "<", "sk-xxx")):
            continue
        providers.append(
            Provider(
                name=f"LLM_{idx}" if idx else "primary",
                api_url=url,
                api_key=key,
                model=model,
                auth_type=pick(prefix, "AUTH_TYPE", "LLM_AUTH_TYPE"),
                api_version=pick(prefix, "AZURE_API_VERSION", "API_VERSION")
                or shared_version,
                deployment=pick(prefix, "AZURE_DEPLOYMENT_PATH", "DEPLOYMENT"),
                extra_headers=_split_headers(pick(prefix, "HEADERS", "LLM_HEADERS")),
            )
        )
    return providers


# --------------------------------------------------------------------------
# Responses API translation
# --------------------------------------------------------------------------


def to_responses_input(
    messages: List[Dict[str, Any]]
) -> Tuple[str, List[Dict[str, Any]]]:
    """Convert Chat Completions messages into Responses instructions + input.

    System messages become top-level instructions. Tool calls and their
    results become ``function_call`` and ``function_call_output`` items rather
    than message roles.
    """
    instructions: List[str] = []
    items: List[Dict[str, Any]] = []

    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if role == "system":
            if content:
                instructions.append(str(content))
        elif role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": message.get("tool_call_id", ""),
                "output": str(content or ""),
            })
        elif role == "assistant" and message.get("tool_calls"):
            if content:
                items.append({"role": "assistant", "content": str(content)})
            for call in message["tool_calls"]:
                fn = call.get("function") or {}
                items.append({
                    "type": "function_call",
                    "call_id": call.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })
        else:
            items.append({"role": role or "user", "content": str(content or "")})

    return "\n\n".join(instructions), items


def to_responses_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten Chat Completions tool schemas into Responses tool schemas."""
    out: List[Dict[str, Any]] = []
    for tool in tools or []:
        fn = tool.get("function") or tool
        out.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


def _status_ranges(spec: str) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.append((int(lo), int(hi)))
        else:
            out.append((int(part), int(part)))
    return out


class ChatClient:
    """Chat completions with failover, streaming and parameter repair."""

    def __init__(
        self,
        providers: Optional[List[Provider]] = None,
        timeout: float = None,
        retries: int = None,
        backoff: float = None,
        failover_on: str = None,
        log: bool = None,
    ) -> None:
        self.providers = providers if providers is not None else load_providers()
        self.timeout = float(
            timeout if timeout is not None else os.getenv("LLM_TIMEOUT_SECONDS", "120")
        )
        self.retries = int(
            retries if retries is not None else os.getenv("LLM_RETRIES", "1")
        )
        self.backoff = float(
            backoff if backoff is not None else os.getenv("LLM_BACKOFF_SECONDS", "1.2")
        )
        self.failover_on = _status_ranges(
            failover_on
            if failover_on is not None
            else os.getenv("LLM_FAILOVER_ON_STATUS", "401,403,404,408,429,500-599")
        )
        self.log = (
            log if log is not None else os.getenv("LLM_DEBUG", "1").strip() == "1"
        )
        self._session = requests.Session()

    # -- plumbing --------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def describe(self) -> List[Dict[str, str]]:
        return [p.redacted() for p in self.providers]

    def _say(self, message: str) -> None:
        if self.log:
            print(f"[llm] {message}", flush=True)

    def _payload(
        self,
        provider: Provider,
        messages: List[Dict[str, Any]],
        *,
        stream: bool,
        temperature: Optional[float],
        max_tokens: Optional[int],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[str],
        reasoning_effort: Optional[str],
        verbosity: Optional[str],
    ) -> Dict[str, Any]:
        if provider.protocol == "responses":
            return self._responses_payload(
                provider, messages, stream=stream, temperature=temperature,
                max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
                reasoning_effort=reasoning_effort, verbosity=verbosity,
            )

        profile = provider.profile
        payload: Dict[str, Any] = {"messages": messages, "stream": stream}
        if provider.model:
            payload["model"] = provider.model
        if max_tokens is not None:
            payload[profile.token_param] = int(max_tokens)
        if temperature is not None and profile.supports_temperature:
            payload["temperature"] = float(temperature)
        if reasoning_effort and profile.supports_reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if verbosity and profile.supports_verbosity:
            payload["verbosity"] = verbosity
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        return payload

    def _responses_payload(
        self,
        provider: Provider,
        messages: List[Dict[str, Any]],
        *,
        stream: bool,
        temperature: Optional[float],
        max_tokens: Optional[int],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[str],
        reasoning_effort: Optional[str],
        verbosity: Optional[str],
    ) -> Dict[str, Any]:
        """Same request, expressed the way the Responses API wants it."""
        profile = provider.profile
        instructions, items = to_responses_input(messages)

        payload: Dict[str, Any] = {"input": items, "stream": stream}
        if provider.model:
            payload["model"] = provider.model
        if instructions:
            payload["instructions"] = instructions
        if max_tokens is not None:
            payload[profile.responses_token_param] = int(max_tokens)
        if temperature is not None and profile.supports_temperature:
            payload["temperature"] = float(temperature)
        # Reasoning effort and verbosity are nested objects here, not flat keys.
        if reasoning_effort and profile.supports_reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if verbosity and profile.supports_verbosity:
            payload["text"] = {"verbosity": verbosity}
        if tools:
            payload["tools"] = to_responses_tools(tools)
            payload["tool_choice"] = tool_choice or "auto"
        return payload

    def _repair(self, provider: Provider, body: str) -> bool:
        """Adapt the profile to a rejection. True when worth retrying."""
        try:
            err = json.loads(body).get("error") or {}
        except Exception:
            err = {}
        param = (err.get("param") or "").strip()
        message = f"{err.get('message', '')} {body[:400]}".lower()

        if param in ("max_output_tokens", "max_completion_tokens"):
            return False        # already using the name the service asked for

        if param == "max_tokens" or (
            not param and "max_completion_tokens" in message
        ):
            if provider.profile.token_param != "max_completion_tokens":
                provider.profile = replace(
                    provider.profile, token_param="max_completion_tokens"
                )
                self._say(f"{provider.name}: switched to max_completion_tokens")
                return True
            return False

        for field, flag in _DROPPABLE.items():
            hit = param == field or (not param and f"'{field}'" in message)
            if hit and getattr(provider.profile, flag):
                provider.profile = replace(provider.profile, **{flag: False})
                self._say(f"{provider.name}: dropped unsupported '{field}'")
                return True
        return False

    def _post(self, provider: Provider, payload: Dict[str, Any], stream: bool):
        return self._session.post(
            provider.endpoint(),
            json=payload,
            headers=provider.headers(),
            timeout=self.timeout,
            stream=stream,
        )

    # -- public API ------------------------------------------------------
    def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        verbosity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Non-streaming call. Returns the assistant message dict."""
        last: Optional[str] = None
        if not self.providers:
            raise NoProvidersConfigured("No LLM provider is configured.")

        for provider in self.providers:
            for attempt in range(self.retries + 1):
                payload = self._payload(
                    provider,
                    messages,
                    stream=False,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                    reasoning_effort=reasoning_effort,
                    verbosity=verbosity,
                )
                try:
                    resp = self._post(provider, payload, stream=False)
                except requests.RequestException as exc:
                    last = f"{provider.name}: {exc}"
                    self._say(last)
                    time.sleep(self.backoff * (attempt + 1))
                    continue

                if resp.status_code == 200:
                    body = resp.json()
                    if provider.protocol == "responses":
                        return self._responses_message(body)
                    choices = body.get("choices") or []
                    if not choices:
                        last = f"{provider.name}: empty choices"
                        break
                    return choices[0].get("message") or {}

                body = resp.text
                last = f"{provider.name}: HTTP {resp.status_code} {body[:300]}"
                self._say(last)

                if resp.status_code == 400 and self._repair(provider, body):
                    continue
                if any(lo <= resp.status_code <= hi for lo, hi in self.failover_on):
                    break
                time.sleep(self.backoff * (attempt + 1))

        raise LLMError(last or "All providers failed.")

    def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        verbosity: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield events as the model produces them.

        Event shapes:
          {"type": "text",  "text": str}          incremental answer text
          {"type": "tool",  "calls": [...]}       assembled tool calls
          {"type": "usage", "usage": {...}}       token counts when reported
          {"type": "end",   "reason": str}        finish reason
        """
        last: Optional[str] = None
        if not self.providers:
            raise NoProvidersConfigured("No LLM provider is configured.")

        for provider in self.providers:
            for attempt in range(self.retries + 1):
                payload = self._payload(
                    provider,
                    messages,
                    stream=True,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                    reasoning_effort=reasoning_effort,
                    verbosity=verbosity,
                )
                try:
                    resp = self._post(provider, payload, stream=True)
                except requests.RequestException as exc:
                    last = f"{provider.name}: {exc}"
                    self._say(last)
                    time.sleep(self.backoff * (attempt + 1))
                    continue

                if resp.status_code != 200:
                    body = resp.text
                    last = f"{provider.name}: HTTP {resp.status_code} {body[:300]}"
                    self._say(last)
                    resp.close()
                    if resp.status_code == 400 and self._repair(provider, body):
                        continue
                    if any(
                        lo <= resp.status_code <= hi for lo, hi in self.failover_on
                    ):
                        break
                    time.sleep(self.backoff * (attempt + 1))
                    continue

                self._say(
                    f"{provider.name}: streaming {provider.protocol} "
                    f"from {provider.endpoint()}"
                )
                reader = (self._read_responses_stream
                          if provider.protocol == "responses"
                          else self._read_stream)
                try:
                    yield from reader(resp)
                finally:
                    resp.close()
                return

        raise LLMError(last or "All providers failed.")

    # -- SSE parsing -----------------------------------------------------
    @staticmethod
    def _read_stream(resp: requests.Response) -> Iterator[Dict[str, Any]]:
        pending: Dict[int, Dict[str, Any]] = {}
        reason = ""

        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if line.startswith(":"):          # SSE comment / keep-alive
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if chunk.get("usage"):
                yield {"type": "usage", "usage": chunk["usage"]}

            # Azure sends a leading chunk carrying only content filter results.
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}

            text = delta.get("content")
            if text:
                yield {"type": "text", "text": text}

            for call in delta.get("tool_calls") or []:
                slot = pending.setdefault(
                    call.get("index", 0),
                    {"id": "", "type": "function",
                     "function": {"name": "", "arguments": ""}},
                )
                if call.get("id"):
                    slot["id"] = call["id"]
                fn = call.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]

            if choice.get("finish_reason"):
                reason = choice["finish_reason"]

        if pending:
            yield {"type": "tool", "calls": [pending[k] for k in sorted(pending)]}
        yield {"type": "end", "reason": reason}

    # -- Responses API parsing -------------------------------------------
    @staticmethod
    def _responses_message(body: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten a non-streamed Responses body into a Chat-style message."""
        text: List[str] = []
        calls: List[Dict[str, Any]] = []
        for item in body.get("output") or []:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") in ("output_text", "text"):
                        text.append(part.get("text", ""))
            elif kind == "function_call":
                calls.append({
                    "id": item.get("call_id") or item.get("id", ""),
                    "type": "function",
                    "function": {"name": item.get("name", ""),
                                 "arguments": item.get("arguments", "{}")},
                })
        message: Dict[str, Any] = {"role": "assistant", "content": "".join(text)}
        if calls:
            message["tool_calls"] = calls
        return message

    @staticmethod
    def _read_responses_stream(resp: requests.Response) -> Iterator[Dict[str, Any]]:
        """Normalise Responses SSE into the same events the chat reader emits.

        The wire format is entirely different: typed ``response.*`` events
        carrying deltas, rather than a choices array. Callers never see the
        difference.
        """
        pending: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        reason = ""

        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            # Responses sends "event:" lines too; the JSON carries its own type.
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            kind = chunk.get("type", "")

            if kind == "response.output_text.delta":
                text = chunk.get("delta") or ""
                if text:
                    yield {"type": "text", "text": text}

            elif kind == "response.output_item.added":
                item = chunk.get("item") or {}
                if item.get("type") == "function_call":
                    key = item.get("id") or item.get("call_id") or str(len(order))
                    if key not in pending:
                        order.append(key)
                    pending[key] = {
                        "id": item.get("call_id") or item.get("id", ""),
                        "type": "function",
                        "function": {"name": item.get("name", ""), "arguments": ""},
                    }

            elif kind == "response.function_call_arguments.delta":
                key = chunk.get("item_id") or (order[-1] if order else None)
                if key in pending:
                    pending[key]["function"]["arguments"] += chunk.get("delta") or ""

            elif kind == "response.function_call_arguments.done":
                key = chunk.get("item_id") or (order[-1] if order else None)
                if key in pending and chunk.get("arguments"):
                    pending[key]["function"]["arguments"] = chunk["arguments"]

            elif kind == "response.output_item.done":
                item = chunk.get("item") or {}
                if item.get("type") == "function_call":
                    key = item.get("id") or item.get("call_id")
                    if key in pending:
                        pending[key]["id"] = item.get("call_id") or pending[key]["id"]
                        pending[key]["function"]["name"] = (
                            item.get("name") or pending[key]["function"]["name"])
                        if item.get("arguments"):
                            pending[key]["function"]["arguments"] = item["arguments"]

            elif kind in ("response.completed", "response.incomplete"):
                body = chunk.get("response") or {}
                usage = body.get("usage")
                if usage:
                    yield {"type": "usage", "usage": usage}
                reason = body.get("status") or "completed"

            elif kind in ("response.failed", "error"):
                body = chunk.get("response") or chunk
                detail = (body.get("error") or {}).get("message") or str(body)[:300]
                raise LLMError(f"Responses API error: {detail}")

        if pending:
            yield {"type": "tool", "calls": [pending[k] for k in order if k in pending]}
        yield {"type": "end", "reason": reason}
