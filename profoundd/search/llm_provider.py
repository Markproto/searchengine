"""Centralized LLM construction for Profoundd.

All AI modules pull their LLM handle from build_llm(role) instead of
constructing their own SDK clients. Role-specific config lives in
SiteSetting (writable from /admin/ai-provider) with env-var fallbacks.

Returns LangChain `BaseChatModel` so every caller uses a single
invoke API regardless of which provider is chosen. invoke_text()
is a convenience that strips Qwen3-style <think> blocks before
returning.

Roles currently registered:
  - "explain"  — AI Explain panels (Epstein/Climate/WEF/Archive/FWP)
  - "summary"  — Search-results AI summary
  - "analyzer" — /admin/analyze-url URL analyzer + revise loop
  - "article"  — Per-article sentiment/bias analysis cards
  - "the_man"  — Editorial ranking decisions
  - "bob"      — NewsRoom Bob story drafting
  - "verify"   — Verify Bot dual-perspective claim analysis

Each role can be configured independently from /admin/ai-provider:
  ai_<role>_provider     — "anthropic" | "ollama" | "xai"
  ai_<role>_ollama_model — model name for ollama
  ai_<role>_ollama_url   — base URL for ollama (defaults to apollo9)
  ai_anthropic_model     — model name shared across anthropic-configured roles
  ai_xai_model           — model name shared across xai-configured roles
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Optional

logger = logging.getLogger(__name__)


# Default model per role per provider. Used when no SiteSetting / env is set.
DEFAULTS = {
    "explain":  {"anthropic": "claude-sonnet-4-6",          "ollama": "qwen3:8b", "xai": "grok-2-latest"},
    "summary":  {"anthropic": "claude-haiku-4-5-20251001",  "ollama": "qwen3:8b", "xai": "grok-2-latest"},
    "analyzer": {"anthropic": "claude-sonnet-4-6",          "ollama": "qwen3:8b", "xai": "grok-2-latest"},
    "article":  {"anthropic": "claude-sonnet-4-6",          "ollama": "qwen3:8b", "xai": "grok-2-latest"},
    "the_man":  {"anthropic": "claude-sonnet-4-6",          "ollama": "qwen3:8b", "xai": "grok-2-latest"},
    "bob":      {"anthropic": "claude-sonnet-4-6",          "ollama": "qwen3:8b", "xai": "grok-2-latest"},
    "verify":   {"anthropic": "claude-sonnet-4-6",          "ollama": "qwen3:8b", "xai": "grok-2-latest"},
}

ROLES = list(DEFAULTS.keys())
VALID_PROVIDERS = ("anthropic", "ollama", "xai")
DEFAULT_PROVIDER = "anthropic"

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_cache_lock = threading.Lock()
_cache: dict = {}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolve_provider(role: str) -> str:
    from profoundd.utils.models import SiteSetting
    s = SiteSetting.get(f"ai_{role}_provider", "") or ""
    if s:
        return s.lower()
    # Back-compat: explain was the only role originally; honor its env var
    # for everything when no per-role setting exists.
    s = os.environ.get(f"AI_{role.upper()}_PROVIDER", "")
    if s:
        return s.lower()
    s = os.environ.get("AI_EXPLAIN_PROVIDER", "")
    if s:
        return s.lower()
    return DEFAULT_PROVIDER


def _resolve_model(role: str, provider: str) -> str:
    from profoundd.utils.models import SiteSetting
    # Most specific key first
    s = SiteSetting.get(f"ai_{role}_{provider}_model", "")
    if s:
        return s
    # Shared per-provider keys (preserves backward compat)
    if provider == "anthropic":
        s = SiteSetting.get("ai_anthropic_model", "")
        if s:
            return s
    elif provider == "xai":
        s = SiteSetting.get("ai_xai_model", "")
        if s:
            return s
    elif provider == "ollama":
        s = os.environ.get("OLLAMA_MODEL", "")
        if s:
            return s
    return DEFAULTS.get(role, {}).get(provider, "")


def _resolve_ollama_url(role: str) -> str:
    from profoundd.utils.models import SiteSetting
    return (SiteSetting.get(f"ai_{role}_ollama_url", "")
            or os.environ.get("OLLAMA_URL", "http://10.0.6.1:11434"))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_llm(role: str = "explain",
              *,
              max_tokens: int = 1500,
              temperature: float = 0.2,
              provider_override: Optional[str] = None,
              model_override: Optional[str] = None):
    """Return a LangChain ChatModel for the given role.

    Caches the handle by (role, provider, model, url, max_tokens, temperature)
    so repeated calls reuse the same client and connection pool.
    """
    provider = (provider_override or _resolve_provider(role)).lower()
    if provider not in VALID_PROVIDERS:
        logger.warning("Unknown provider %r for role %s; falling back to anthropic.", provider, role)
        provider = "anthropic"
    model = model_override or _resolve_model(role, provider)
    url = _resolve_ollama_url(role) if provider == "ollama" else ""

    key = (role, provider, model, url, max_tokens, temperature)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached

        try:
            if provider == "ollama":
                from langchain_ollama import ChatOllama
                llm = ChatOllama(
                    model=model,
                    base_url=url,
                    temperature=temperature,
                    num_predict=max_tokens,
                )
            elif provider == "xai":
                from langchain_openai import ChatOpenAI
                from profoundd.utils.models import SiteSetting
                xai_key = SiteSetting.get("ai_xai_key", "") or os.environ.get("XAI_API_KEY", "")
                if not xai_key:
                    raise RuntimeError(f"xAI API key not configured (role={role})")
                llm = ChatOpenAI(
                    model=model,
                    api_key=xai_key,
                    base_url="https://api.x.ai/v1",
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=60,
                    max_retries=3,
                )
            else:  # anthropic
                from langchain_anthropic import ChatAnthropic
                from profoundd.admin.routes import get_anthropic_key
                api_key = get_anthropic_key()
                if not api_key:
                    raise RuntimeError(f"Anthropic API key not configured (role={role})")
                llm = ChatAnthropic(
                    model=model,
                    api_key=api_key,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    max_retries=4,
                    timeout=60,
                )
        except Exception:
            logger.exception("build_llm failed: role=%s provider=%s model=%s", role, provider, model)
            raise

        _cache[key] = llm
        logger.info("build_llm: role=%s provider=%s model=%s url=%s",
                    role, provider, model, url or "—")
        return llm


def reset_cache():
    """Force all cached LLM handles to rebuild on next call. Call after
    /admin/ai-provider saves so flips take effect immediately."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Invoke helpers
# ---------------------------------------------------------------------------

def strip_think(text: str) -> str:
    """Remove Qwen3-style <think>...</think> reasoning blocks from a response."""
    if not text or "<think>" not in text.lower():
        return text
    return _THINK_BLOCK_RE.sub("", text).strip()


def invoke_text(llm, prompt: str) -> str:
    """Single-shot user-message call. Returns stripped text content.

    The constitution preamble should be applied by the caller (use
    profoundd.utils.editorial_constitution.prepend) BEFORE passing the
    prompt here.
    """
    from langchain_core.messages import HumanMessage
    result = llm.invoke([HumanMessage(content=prompt)])
    text = result.content if hasattr(result, "content") else str(result)
    if isinstance(text, list):
        # Anthropic returns list of content blocks in some configs
        text = "".join(getattr(b, "text", str(b)) for b in text)
    return strip_think(text or "")


def get_provider_for(role: str) -> str:
    """What provider would build_llm(role) use right now? (for telemetry / UI)."""
    return _resolve_provider(role)
