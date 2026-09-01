# LLM client logic
"""
PANDORA LLM Client
==================
Multi-provider LLM abstraction layer.
Receives TRANSCRIPT events from Node, sends LLM_RESPONSE back.

Supported providers (config-selectable, with automatic fallback):
  openrouter | gemini | gpt | mistral | mock

Inbound event:
  { "event": "LLM_QUERY", "text": "...", "history": [...], "provider": "openrouter" }

Outbound event:
  { "event": "LLM_RESPONSE", "text": "...", "provider": "openrouter" }
  { "event": "LLM_ERROR",    "reason": "..." }

Reference: Jarvis chat_with_gpt/gemini/openrouter/mistral in Jarvis_v1_7r_core.py
  - Retains: system persona prompt, history trimming to last 8 messages
  - Retains: provider selection logic, fallback chaining
  - Retains: OpenRouter model + temperature config
  - Rewrites: monolithic per-provider functions → unified provider class hierarchy
  - Adds: automatic provider fallback on failure
  - Removes: hard-coded speak() calls inside LLM functions (voice is Node's job)
"""

import asyncio
import json
import logging
import time

import websockets

logger = logging.getLogger("pandora.llm")


# ---------------------------------------------------------------------------
# Optional provider imports
# ---------------------------------------------------------------------------
try:
    import requests as _requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from openai import OpenAI as _OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import google.generativeai as _genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


# ---------------------------------------------------------------------------
# System persona — rewritten from Jarvis system_message content
# Name/honorific injected at runtime from shared memory profile
# ---------------------------------------------------------------------------
PANDORA_SYSTEM_PROMPT = (
    "You are PANDORA — a Personalized Autonomous Native Desktop Operator and Real-time Assistant. "
    "Your persona is precise, composed, and intelligent with a formal but warm tone. "
    "You address the user by their preferred honorific. "
    "Keep answers concise and practical. Never use emoji. "
    "You are proactive, efficient, and occasionally dry-witted."
)

DEFAULT_CFG = {
    "provider": "openrouter",
    "openrouter_api_key": "",
    "openrouter_model": "nvidia/nemotron-nano-9b-v2:free",
    "openrouter_temperature": 0.9,
    "openrouter_max_tokens": 160,
    "openai_api_key": "",
    "gemini_api_key": "",
    "history_trim": 8,          # keep last N messages in context
    "node_ws_uri": "ws://localhost:8765",
}


# ---------------------------------------------------------------------------
# Provider base class
# ---------------------------------------------------------------------------
class BaseProvider:
    name: str = "base"

    def chat(self, query: str, history: list[dict], honorific: str = "Sir") -> str:
        raise NotImplementedError

    def _system_prompt(self, honorific: str) -> str:
        return PANDORA_SYSTEM_PROMPT.replace("their preferred honorific", honorific)

    def _trim_history(self, history: list[dict], n: int) -> list[dict]:
        """Keep only the last n messages. Mirrors Jarvis trimmed_history logic."""
        return history[-n:] if len(history) > n else history


# ---------------------------------------------------------------------------
# OpenRouter provider (default, free-tier models)
# Reference: Jarvis chat_with_openrouter()
# ---------------------------------------------------------------------------
class OpenRouterProvider(BaseProvider):
    name = "openrouter"

    def __init__(self, cfg: dict):
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests package not installed")
        self._api_key = cfg.get("openrouter_api_key", "")
        self._model = cfg.get("openrouter_model", DEFAULT_CFG["openrouter_model"])
        self._temperature = cfg.get("openrouter_temperature", DEFAULT_CFG["openrouter_temperature"])
        self._max_tokens = cfg.get("openrouter_max_tokens", DEFAULT_CFG["openrouter_max_tokens"])
        self._trim = cfg.get("history_trim", DEFAULT_CFG["history_trim"])

    def chat(self, query: str, history: list[dict], honorific: str = "Sir") -> str:
        system_msg = {"role": "system", "content": self._system_prompt(honorific)}
        trimmed = self._trim_history(history, self._trim)
        messages = [system_msg] + trimmed + [{"role": "user", "content": query}]

        response = _requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": messages,
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
                "top_p": 1,
            },
            timeout=40,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# OpenAI / GPT provider
# Reference: Jarvis chat_with_gpt()
# ---------------------------------------------------------------------------
class OpenAIProvider(BaseProvider):
    name = "gpt"

    def __init__(self, cfg: dict):
        if not OPENAI_AVAILABLE:
            raise RuntimeError("openai package not installed")
        self._client = _OpenAI(api_key=cfg.get("openai_api_key", ""))
        self._trim = cfg.get("history_trim", DEFAULT_CFG["history_trim"])

    def chat(self, query: str, history: list[dict], honorific: str = "Sir") -> str:
        system_msg = {"role": "system", "content": self._system_prompt(honorific)}
        trimmed = self._trim_history(history, self._trim)
        messages = [system_msg] + trimmed + [{"role": "user", "content": query}]
        completion = self._client.chat.completions.create(
            model="gpt-3.5-turbo", messages=messages
        )
        return completion.choices[0].message.content


# ---------------------------------------------------------------------------
# Google Gemini provider
# Reference: Jarvis chat_with_gemini()
# ---------------------------------------------------------------------------
class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self, cfg: dict):
        if not GEMINI_AVAILABLE:
            raise RuntimeError("google-generativeai package not installed")
        _genai.configure(api_key=cfg.get("gemini_api_key", ""))
        self._model = _genai.GenerativeModel("gemini-pro")

    def chat(self, query: str, history: list[dict], honorific: str = "Sir") -> str:
        # Gemini API does not accept system messages in the same way;
        # prepend persona as first user turn for simplicity.
        full_query = f"{self._system_prompt(honorific)}\n\nUser: {query}"
        response = self._model.generate_content(full_query)
        return response.text


# ---------------------------------------------------------------------------
# Mock provider — for development / testing without API keys
# ---------------------------------------------------------------------------
class MockProvider(BaseProvider):
    name = "mock"

    def chat(self, query: str, history: list[dict], honorific: str = "Sir") -> str:
        return f"[MOCK] Received: '{query}'. This is a development stub response, {honorific}."


# ---------------------------------------------------------------------------
# Provider registry + factory
# ---------------------------------------------------------------------------
PROVIDER_MAP: dict[str, type[BaseProvider]] = {
    "openrouter": OpenRouterProvider,
    "gpt":        OpenAIProvider,
    "gemini":     GeminiProvider,
    "mock":       MockProvider,
}

FALLBACK_ORDER = ["openrouter", "gpt", "gemini", "mock"]


def build_provider(name: str, cfg: dict) -> BaseProvider:
    cls = PROVIDER_MAP.get(name)
    if cls is None:
        raise ValueError(f"Unknown provider: '{name}'")
    return cls(cfg)


# ---------------------------------------------------------------------------
# LLM Service
# ---------------------------------------------------------------------------
class LLMService:
    """
    Connects to Node orchestrator, handles LLM_QUERY events, returns responses.
    Supports automatic provider fallback if primary fails.
    """

    def __init__(self, cfg: dict):
        self.cfg = {**DEFAULT_CFG, **cfg}
        self._providers: dict[str, BaseProvider] = {}
        self._ws = None

    def _get_provider(self, name: str) -> BaseProvider:
        if name not in self._providers:
            try:
                self._providers[name] = build_provider(name, self.cfg)
                logger.info("Loaded LLM provider: %s", name)
            except Exception as exc:
                logger.warning("Failed to load provider '%s': %s", name, exc)
                raise
        return self._providers[name]

    def _chat_with_fallback(
        self,
        query: str,
        history: list[dict],
        preferred: str,
        honorific: str,
    ) -> tuple[str, str]:
        """
        Try preferred provider, then fall back through FALLBACK_ORDER.
        Returns (response_text, provider_used).
        """
        order = [preferred] + [p for p in FALLBACK_ORDER if p != preferred]

        for provider_name in order:
            try:
                provider = self._get_provider(provider_name)
                t0 = time.monotonic()
                text = provider.chat(query, history, honorific)
                logger.info("LLM response from %s in %.2fs", provider_name, time.monotonic() - t0)
                return text, provider_name
            except Exception as exc:
                logger.warning("Provider '%s' failed: %s — trying next", provider_name, exc)

        return "I am unable to generate a response at this time.", "none"

    async def _emit(self, payload: dict) -> None:
        if self._ws:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as exc:
                logger.warning("WS send failed: %s", exc)

    async def _handle_messages(self, ws) -> None:
        self._ws = ws
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("event") != "LLM_QUERY":
                continue

            query = msg.get("text", "")
            history = msg.get("history", [])
            preferred = msg.get("provider", self.cfg["provider"])
            honorific = msg.get("honorific", "Sir")

            if not query:
                continue

            try:
                response_text, provider_used = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._chat_with_fallback,
                    query, history, preferred, honorific,
                )
                await self._emit({
                    "event": "LLM_RESPONSE",
                    "text": response_text,
                    "provider": provider_used,
                })
            except Exception as exc:
                logger.error("Unhandled LLM error: %s", exc)
                await self._emit({"event": "LLM_ERROR", "reason": str(exc)})

    async def _connect_and_run(self) -> None:
        uri = self.cfg["node_ws_uri"]
        reconnect_delay = 2
        while True:
            try:
                async with websockets.connect(uri) as ws:
                    logger.info("LLM client connected to Node at %s", uri)
                    reconnect_delay = 2
                    await self._handle_messages(ws)
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                logger.warning("WS lost: %s — retrying in %ds", exc, reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    def start(self) -> None:
        asyncio.run(self._connect_and_run())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def load_config(path: str = "shared/config/configuration.json") -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            full = json.load(f)
        keys = full.get("api_keys", {})
        settings = full.get("settings", {})
        return {
            "provider": settings.get("preferred_ai_model", DEFAULT_CFG["provider"]),
            "openrouter_api_key": keys.get("open_router", ""),
            "openai_api_key": keys.get("open_ai", ""),
            "gemini_api_key": keys.get("gemini", ""),
        }
    except Exception as exc:
        logger.warning("Config load failed (%s) — using defaults", exc)
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    cfg = load_config()
    service = LLMService(cfg)
    service.start()