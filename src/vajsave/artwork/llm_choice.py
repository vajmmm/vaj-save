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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Tuple, Union

# OpenAI-compatible chat-completions endpoint; overridable for tests and for
# self-hosted gateways.
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_TIMEOUT = 15.0
MAX_LLM_RESPONSE_BYTES = 256 * 1024

# Debug log written next to the library root when a chooser is built with a
# ``log_path``. Every request/result line is appended here so the user can see
# what was sent and what came back. The API key is never written.
LLM_LOG_NAME = "llm-cover.log"

# Explicit User-Agent for the HTTP request. urllib's default
# (``Python-urllib/3.x``) is banned by the Cloudflare fronting some gateways
# (error 1010 / 403), so a normal token is sent instead.
LLM_USER_AGENT = "vaj-save/0.1.0"

# Selectable wire protocols. Gemini is deliberately not implemented: only the
# OpenAI chat-completions shape and the Anthropic messages shape are supported.
PROTOCOL_OPENAI = "openai-completions"
PROTOCOL_ANTHROPIC = "anthropic-messages"
DEFAULT_LLM_PROTOCOL = PROTOCOL_OPENAI
LLM_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

# Wire-shape names accepted from an older config, so a stored ``openai`` /
# ``anthropic`` keeps working after the rename to the explicit shape names.
_LEGACY_PROTOCOL_ALIASES = {
    "openai": PROTOCOL_OPENAI,
    "anthropic": PROTOCOL_ANTHROPIC,
}

# Anthropic messages endpoint defaults (used only when that protocol is chosen).
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-latest"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "You choose the single best box-art file for a video game from a candidate "
    "list. Reply with exactly one file name copied verbatim from the list, or "
    "NONE when none of them fit. Never explain or add text."
)

# Used by :meth:`LLMCoverChooser.probe` so the connectivity test returns a
# short, readable acknowledgement instead of the chooser's "NONE".
_PROBE_SYSTEM_PROMPT = (
    "You are a connectivity check. Reply with the single word OK and nothing else."
)


def normalize_protocol(value: object) -> str:
    """A supported wire-shape key; legacy keys map across, unknown -> default."""
    text = str(value or "").strip().lower()
    if text in LLM_PROTOCOLS:
        return text
    return _LEGACY_PROTOCOL_ALIASES.get(text, DEFAULT_LLM_PROTOCOL)


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


def normalize_base_url(
    value: object, protocol: object = DEFAULT_LLM_PROTOCOL
) -> str:
    """A usable endpoint: blank -> the protocol default; a missing ``/v1`` gains it.

    An endpoint that already carries a ``/v1`` segment is returned untouched
    (trailing slash and all), so the version prefix is never duplicated. This
    makes a bare host such as ``https://api.deepseek.com`` usable while leaving
    the built-in defaults byte-for-byte unchanged.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return default_base_url(protocol)
    if "/v1" not in text:
        return text.rstrip("/") + "/v1"
    return text


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
        log_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self.protocol = normalize_protocol(protocol)
        self.model = model or default_model(self.protocol)
        self.base_url = normalize_base_url(base_url, self.protocol).rstrip("/")
        self.timeout = float(timeout)
        self._urlopen = urlopen or urllib.request.urlopen
        # ``None`` keeps the chooser silent (used by unit tests); the app passes
        # a real path so the exchange can be inspected after the fact.
        self._log_path = Path(log_path) if log_path else None

    def _log(self, message: str) -> None:
        """Append one timestamped debug line; never raises, never logs the key."""
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {message}\n")
        except Exception:  # noqa: BLE001 - a debug log must never break a lookup
            pass

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
        endpoint = getattr(request, "full_url", "") or ""
        self._log(
            f"request protocol={self.protocol} model={self.model} "
            f"endpoint={endpoint} query={str(query or '').strip()!r} "
            f"candidates={len(names)}"
        )
        for name in names:
            self._log(f"  candidate: {name}")
        try:
            response = self._urlopen(request, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - offline/timeout/HTTP all mean "no choice"
            self._log(f"result error={type(exc).__name__}: {exc}")
            return None
        try:
            status = getattr(response, "status", None)
            if status is None:
                status = getattr(response, "code", None)
            raw = self._read_response(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        if raw is None:
            self._log(f"result status={status} -> no usable response")
            return None
        choice = _match_choice(_extract_content(raw, self.protocol), names)
        self._log(f"result status={status} choice={choice!r}")
        return choice

    def probe(self) -> Tuple[bool, str]:
        """Send one tiny message to check the endpoint/model/key actually work.

        Returns ``(ok, detail)``: ``detail`` is the model's trimmed reply on
        success, or a short reason on failure. Never raises and never includes
        the API key, so it is safe to show in the status bar.
        """
        if not self._api_key:
            return False, "请先填写 API 密钥"
        if not str(self.model or "").strip():
            return False, "请先填写模型 ID"
        prompt = "ping"
        if self.protocol == PROTOCOL_ANTHROPIC:
            request = self._build_anthropic_request(prompt, system=_PROBE_SYSTEM_PROMPT)
        else:
            request = self._build_openai_request(prompt, system=_PROBE_SYSTEM_PROMPT)
        endpoint = getattr(request, "full_url", "") or ""
        self._log(f"probe protocol={self.protocol} model={self.model} endpoint={endpoint}")
        try:
            response = self._urlopen(request, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - report, never raise
            self._log(f"probe error={type(exc).__name__}: {exc}")
            return False, f"请求失败: {type(exc).__name__}: {exc}"
        try:
            status = getattr(response, "status", None)
            if status is None:
                status = getattr(response, "code", None)
            raw = self._read_response(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        if raw is None:
            self._log(f"probe status={status} -> no usable response")
            return False, f"模型返回异常（HTTP {status}）"
        reply = (_extract_content(raw, self.protocol) or "").strip()
        text = reply or "(空回复)"
        self._log(f"probe status={status} reply={text!r}")
        return True, text

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

    def _build_openai_request(self, prompt: str, system: str = _SYSTEM_PROMPT):
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
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
                "User-Agent": LLM_USER_AGENT,
            },
            method="POST",
        )

    def _build_anthropic_request(self, prompt: str, system: str = _SYSTEM_PROMPT):
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": ANTHROPIC_MAX_TOKENS,
                "temperature": 0,
                "system": system,
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
                "User-Agent": LLM_USER_AGENT,
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
    log_path: Optional[Union[str, Path]] = None,
) -> Optional[str]:
    """Convenience wrapper around :class:`LLMCoverChooser`.

    ``model``/``base_url`` fall back to the chosen protocol's built-in default,
    so ``protocol="anthropic-messages"`` without either yields the Anthropic
    endpoint.
    """
    proto = normalize_protocol(protocol)
    chooser = LLMCoverChooser(
        api_key=api_key,
        model=model or default_model(proto),
        base_url=base_url or default_base_url(proto),
        timeout=timeout,
        protocol=proto,
        urlopen=urlopen,
        log_path=log_path,
    )
    return chooser.choose(candidates, query)


__all__ = [
    "ANTHROPIC_API_VERSION",
    "ANTHROPIC_MAX_TOKENS",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_LLM_BASE_URL",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_LLM_PROTOCOL",
    "DEFAULT_LLM_TIMEOUT",
    "LLM_PROTOCOLS",
    "LLM_LOG_NAME",
    "LLM_USER_AGENT",
    "LLMCoverChooser",
    "PROTOCOL_ANTHROPIC",
    "PROTOCOL_OPENAI",
    "choose_cover_filename",
    "default_base_url",
    "default_model",
    "normalize_base_url",
    "normalize_protocol",
]
