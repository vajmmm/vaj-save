"""Optional LLM disambiguation for ambiguous box-art candidates.

The deterministic :func:`~vajsave.artwork.boxart_index.unique_boxart_match`
already resolves the common cases: a single match, or several region/language
variants of one title (USA wins). Only when *genuinely different* titles match
the same query is the listing ambiguous. This module is the optional last
resort: it asks a language model to pick exactly one file name from that list.

Design constraints:

* **Off by default.** The service only receives a chooser when the user opted in
  and supplied an API key, so a disabled app never performs an LLM request.
* **Never invents a name.** The model's answer is accepted only when it is one
  of the offered file names; anything else collapses to ``None``.
* **Never raises.** A transport error, a non-200 response, malformed JSON or a
  missing choice all degrade to ``None`` so the caller keeps the placeholder.
* **Two wire protocols.** The chooser speaks either the OpenAI
  chat-completions shape or the Anthropic messages shape, selected by
  ``protocol`` (default OpenAI). Gemini is deliberately not implemented.
* **The API key never reaches a log.** It travels only in the request headers
  (``Authorization: Bearer`` for OpenAI, ``x-api-key`` for Anthropic), and
  :meth:`LLMCoverChooser.__repr__` omits it.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence, Tuple

# OpenAI-compatible chat-completions endpoint; overridable for tests and for
# self-hosted gateways.
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_TIMEOUT = 15.0
MAX_LLM_RESPONSE_BYTES = 256 * 1024

# Selectable wire protocols. Gemini is deliberately not implemented: only the
# OpenAI chat-completions shape and the Anthropic messages shape are supported.
PROTOCOL_OPENAI = "openai"
PROTOCOL_ANTHROPIC = "anthropic"
DEFAULT_LLM_PROTOCOL = PROTOCOL_OPENAI
LLM_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

# Anthropic messages endpoint defaults (used only when that protocol is chosen).
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-latest"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 1024

# Provider presets bundle the wire protocol with a sensible endpoint and model
# so the common gateways can be configured with one choice. ``custom`` is the
# escape hatch: it keeps whatever the user has typed (see ``fill_from_preset``)
# so a private/self-hosted endpoint is never clobbered. Gemini is deliberately
# absent -- only the OpenAI and Anthropic wire shapes are implemented.
PRESET_OPENAI = "openai"
PRESET_ANTHROPIC = "anthropic"
PRESET_DEEPSEEK = "deepseek"
PRESET_OPENROUTER = "openrouter"
PRESET_CUSTOM = "custom"
DEFAULT_LLM_PRESET = PRESET_OPENAI


@dataclass(frozen=True)
class LLMPreset:
    """One provider preset: a wire protocol plus its default endpoint/model.

    ``key`` is the persisted/serialised identity (``openai``/``anthropic``/
    ``deepseek``/``openrouter``/``custom``); ``protocol`` is the wire shape the
    chooser speaks; ``base_url``/``model`` are the values auto-filled into the
    settings fields when the preset is chosen.
    """

    key: str
    label: str
    protocol: str
    base_url: str
    model: str
    description: str = ""


_LLM_PRESETS: Tuple[LLMPreset, ...] = (
    LLMPreset(
        key=PRESET_OPENAI,
        label="OpenAI",
        protocol=PROTOCOL_OPENAI,
        base_url=DEFAULT_LLM_BASE_URL,
        model=DEFAULT_LLM_MODEL,
        description="OpenAI 官方接口",
    ),
    LLMPreset(
        key=PRESET_ANTHROPIC,
        label="Anthropic",
        protocol=PROTOCOL_ANTHROPIC,
        base_url=DEFAULT_ANTHROPIC_BASE_URL,
        model=DEFAULT_ANTHROPIC_MODEL,
        description="Anthropic Claude 接口",
    ),
    LLMPreset(
        key=PRESET_DEEPSEEK,
        label="DeepSeek",
        protocol=PROTOCOL_OPENAI,
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        description="DeepSeek（OpenAI 兼容）",
    ),
    LLMPreset(
        key=PRESET_OPENROUTER,
        label="OpenRouter",
        protocol=PROTOCOL_OPENAI,
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-4o-mini",
        description="OpenRouter 聚合网关（OpenAI 兼容）",
    ),
    LLMPreset(
        key=PRESET_CUSTOM,
        label="自定义",
        protocol=PROTOCOL_OPENAI,
        base_url=DEFAULT_LLM_BASE_URL,
        model=DEFAULT_LLM_MODEL,
        description="自定义协议 / 地址 / 模型（保留已填内容）",
    ),
)


def llm_presets() -> Tuple[LLMPreset, ...]:
    """All built-in provider presets, in dropdown order."""
    return _LLM_PRESETS


def llm_preset_keys() -> Tuple[str, ...]:
    return tuple(preset.key for preset in _LLM_PRESETS)


def normalize_preset(value: object) -> str:
    """A known preset key; anything else (including Gemini) -> OpenAI."""
    text = str(value or "").strip().lower()
    return text if text in llm_preset_keys() else DEFAULT_LLM_PRESET


def get_llm_preset(key: object) -> LLMPreset:
    """The preset named by ``key``, falling back to the default on a miss."""
    text = normalize_preset(key)
    for preset in _LLM_PRESETS:
        if preset.key == text:
            return preset
    return _LLM_PRESETS[0]


def default_llm_preset() -> LLMPreset:
    return _LLM_PRESETS[0]


def fill_from_preset(
    target: LLMPreset,
    source: LLMPreset,
    protocol: object,
    base_url: object,
    model: object,
) -> Tuple[str, str, str]:
    """``(protocol, base_url, model)`` after choosing the ``target`` preset.

    A field is filled from ``target`` only when the current value is blank or
    still equals ``source``'s declared value for that field -- i.e. the user has
    not customised it. A value the user typed is preserved. The ``custom``
    preset is the escape hatch: it keeps every current value untouched so a
    private endpoint survives the selection. Non-string/``None`` values are
    treated as blank.
    """
    if target.key == PRESET_CUSTOM:
        return (
            normalize_protocol(protocol),
            "" if base_url is None else str(base_url),
            "" if model is None else str(model),
        )

    def _pick(target_value: str, source_value: str, current: object) -> str:
        text = "" if current is None else str(current)
        if not target_value:
            return text
        if not text.strip() or text.rstrip("/") == source_value.rstrip("/"):
            return target_value
        return text

    return (
        _pick(target.protocol, source.protocol, protocol),
        _pick(target.base_url, source.base_url, base_url),
        _pick(target.model, source.model, model),
    )

_SYSTEM_PROMPT = (
    "You choose the single best box-art file for a video game from a candidate "
    "list. Reply with exactly one file name copied verbatim from the list, or "
    "NONE when none of them fit. Never explain or add text."
)


def normalize_protocol(value: object) -> str:
    """A supported protocol key; anything else (including Gemini) -> OpenAI."""
    text = str(value or "").strip().lower()
    return text if text in LLM_PROTOCOLS else DEFAULT_LLM_PROTOCOL


def default_base_url(protocol: object) -> str:
    """The built-in endpoint for ``protocol`` (OpenAI by default)."""
    if normalize_protocol(protocol) == PROTOCOL_ANTHROPIC:
        return DEFAULT_ANTHROPIC_BASE_URL
    return DEFAULT_LLM_BASE_URL


def default_model(protocol: object) -> str:
    """The built-in model for ``protocol`` (OpenAI by default)."""
    if normalize_protocol(protocol) == PROTOCOL_ANTHROPIC:
        return DEFAULT_ANTHROPIC_MODEL
    return DEFAULT_LLM_MODEL


def _match_choice(text: Optional[str], candidates: Sequence[str]) -> Optional[str]:
    """The one candidate named by ``text``, else ``None``.

    A reply may wrap the name in prose/quotes, so a candidate is accepted when
    it appears as a substring -- but only when exactly one candidate does, so an
    answer mentioning several (or none) is rejected rather than guessed.
    """
    if not text:
        return None
    found = [name for name in candidates if name and name in text]
    if len(found) == 1:
        return found[0]
    return None


def _extract_anthropic_content(data: dict) -> Optional[str]:
    """Join the text blocks of an Anthropic ``messages`` response."""
    blocks = data.get("content")
    if not isinstance(blocks, list):
        return None
    parts = [
        block.get("text")
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    if not parts:
        return None
    return "\n".join(parts)


def _extract_content(
    raw: bytes, protocol: object = DEFAULT_LLM_PROTOCOL
) -> Optional[str]:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if normalize_protocol(protocol) == PROTOCOL_ANTHROPIC:
        return _extract_anthropic_content(data)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


class LLMCoverChooser:
    """Ask a chat model (OpenAI or Anthropic shape) to pick one file name."""

    def __init__(
        self,
        *,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_LLM_TIMEOUT,
        protocol: object = DEFAULT_LLM_PROTOCOL,
        urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self.protocol = normalize_protocol(protocol)
        self.model = model or default_model(self.protocol)
        self.base_url = (base_url or default_base_url(self.protocol)).rstrip("/")
        self.timeout = float(timeout)
        self._urlopen = urlopen or urllib.request.urlopen

    def __repr__(self) -> str:  # pragma: no cover - trivial formatting
        return (
            f"LLMCoverChooser(protocol={self.protocol!r}, model={self.model!r}, "
            f"base_url={self.base_url!r}, api_key=***)"
        )

    def __call__(self, candidates: Sequence[str], query: str) -> Optional[str]:
        return self.choose(candidates, query)

    def choose(self, candidates: Sequence[str], query: str) -> Optional[str]:
        """The chosen candidate name, or ``None`` on any failure/nonsense."""
        names = [str(name) for name in (candidates or ()) if str(name or "").strip()]
        if not self._api_key or not names:
            return None
        request = self._build_request(names, query)
        try:
            response = self._urlopen(request, timeout=self.timeout)
        except Exception:  # noqa: BLE001 - offline/timeout/HTTP all mean "no choice"
            return None
        try:
            raw = self._read_response(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        if raw is None:
            return None
        return _match_choice(_extract_content(raw, self.protocol), names)

    def _build_request(self, names: Sequence[str], query: str):
        listing = "\n".join(f"- {name}" for name in names)
        prompt = (
            f"Game title: {str(query or '').strip()}\n"
            f"Candidate box-art files:\n{listing}\n"
            "Answer with one exact file name from the list, or NONE."
        )
        if self.protocol == PROTOCOL_ANTHROPIC:
            return self._build_anthropic_request(prompt)
        return self._build_openai_request(prompt)

    def _build_openai_request(self, prompt: str):
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
        return urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

    def _build_anthropic_request(self, prompt: str):
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": ANTHROPIC_MAX_TOKENS,
                "temperature": 0,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")
        return urllib.request.Request(
            self.base_url + "/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            },
            method="POST",
        )

    @staticmethod
    def _read_response(response: Any) -> Optional[bytes]:
        status = getattr(response, "status", None)
        if status is None:
            status = getattr(response, "code", None)
        if status is not None and int(status) != 200:
            return None
        try:
            raw = response.read(MAX_LLM_RESPONSE_BYTES + 1)
        except Exception:  # noqa: BLE001 - a broken connection is just "no choice"
            return None
        if not raw or len(raw) > MAX_LLM_RESPONSE_BYTES:
            return None
        return raw


def choose_cover_filename(
    candidates: Sequence[str],
    query: str,
    *,
    api_key: str,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = DEFAULT_LLM_TIMEOUT,
    protocol: object = DEFAULT_LLM_PROTOCOL,
    urlopen: Optional[Callable[..., Any]] = None,
) -> Optional[str]:
    """Convenience wrapper around :class:`LLMCoverChooser`.

    ``model``/``base_url`` fall back to the chosen protocol's built-in default,
    so ``protocol="anthropic"`` without either yields the Anthropic endpoint.
    """
    proto = normalize_protocol(protocol)
    chooser = LLMCoverChooser(
        api_key=api_key,
        model=model or default_model(proto),
        base_url=base_url or default_base_url(proto),
        timeout=timeout,
        protocol=proto,
        urlopen=urlopen,
    )
    return chooser.choose(candidates, query)


__all__ = [
    "ANTHROPIC_API_VERSION",
    "ANTHROPIC_MAX_TOKENS",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_LLM_BASE_URL",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_LLM_PRESET",
    "DEFAULT_LLM_PROTOCOL",
    "DEFAULT_LLM_TIMEOUT",
    "LLM_PROTOCOLS",
    "LLMPreset",
    "LLMCoverChooser",
    "PRESET_ANTHROPIC",
    "PRESET_CUSTOM",
    "PRESET_DEEPSEEK",
    "PRESET_OPENAI",
    "PRESET_OPENROUTER",
    "PROTOCOL_ANTHROPIC",
    "PROTOCOL_OPENAI",
    "choose_cover_filename",
    "default_base_url",
    "default_llm_preset",
    "default_model",
    "fill_from_preset",
    "get_llm_preset",
    "llm_preset_keys",
    "llm_presets",
    "normalize_preset",
    "normalize_protocol",
]
