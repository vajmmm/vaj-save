"""Optional LLM cover disambiguation.

When the deterministic box-art matcher leaves several genuinely different
titles, the optional LLM is asked to pick exactly one file name from that list.
The client is disabled by default, never guesses outside the offered list, and
every transport/parse/nonsense answer collapses to ``None`` so the caller keeps
the placeholder and never writes the cache.

The API key is sent in the request header but must never be echoed by ``repr``
(or any log line built from the chooser).
"""

from __future__ import annotations

import json
import urllib.error

from vajsave.artwork.llm_choice import (
    ANTHROPIC_API_VERSION,
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROTOCOL,
    LLM_PROTOCOLS,
    MAX_LLM_RESPONSE_BYTES,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    LLMCoverChooser,
    choose_cover_filename,
    normalize_base_url,
    normalize_protocol,
)

_CANDIDATES = ("Mario Kart 7 (USA).png", "Mario Party (USA).png")


class FakeResponse:
    def __init__(self, data=b"", status=200):
        self._data = data
        self.status = status
        self.headers = {}
        self.closed = False

    def read(self, n=-1):
        if n is None or n < 0:
            return self._data
        return self._data[:n]

    def close(self):
        self.closed = True


def _chat_response(content) -> FakeResponse:
    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    return FakeResponse(json.dumps(body).encode("utf-8"))


def _chooser(content=None, *, status=200, raw=None):
    if raw is not None:
        response = FakeResponse(raw, status=status)
    else:
        response = _chat_response(content)
        response.status = status
    return LLMCoverChooser(
        api_key="sk-test", urlopen=lambda request, timeout=None: response
    )


def test_choose_returns_candidate_and_sends_openai_compatible_request():
    captured = {}

    def opener(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        return _chat_response("Mario Party (USA).png")

    choice = choose_cover_filename(
        _CANDIDATES, "Mario", api_key="sk-test", urlopen=opener
    )

    assert choice == "Mario Party (USA).png"
    request = captured["request"]
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer sk-test"
    assert request.full_url == DEFAULT_LLM_BASE_URL + "/chat/completions"
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == DEFAULT_LLM_MODEL
    assert "Mario Party (USA).png" in json.dumps(body)


def test_choose_accepts_a_filename_wrapped_in_prose():
    chooser = _chooser("The best match is Mario Kart 7 (USA).png")
    assert chooser.choose(_CANDIDATES, "Mario") == "Mario Kart 7 (USA).png"


def test_choose_rejects_nonsense_none_and_ambiguous_replies():
    for content in (
        "NONE",
        "I am not sure",
        "",
        "Mario",
        "Mario Kart 7 (USA).png and Mario Party (USA).png",
    ):
        chooser = _chooser(content)
        assert chooser.choose(_CANDIDATES, "Mario") is None


def test_choose_degrades_on_transport_failure():
    def boom(request, timeout=None):
        raise urllib.error.URLError("offline")

    assert (
        choose_cover_filename(_CANDIDATES, "Mario", api_key="sk", urlopen=boom) is None
    )

    class Broken(FakeResponse):
        def read(self, n=-1):
            raise OSError("connection reset")

    chooser = LLMCoverChooser(
        api_key="sk", urlopen=lambda request, timeout=None: Broken()
    )
    assert chooser.choose(_CANDIDATES, "Mario") is None


def test_choose_without_key_or_candidates_never_hits_the_network():
    def boom(request, timeout=None):
        raise AssertionError("network must not be used without a key/candidates")

    assert choose_cover_filename(_CANDIDATES, "Mario", api_key="", urlopen=boom) is None
    assert choose_cover_filename((), "Mario", api_key="sk", urlopen=boom) is None
    assert choose_cover_filename(_CANDIDATES, "", api_key="sk", urlopen=boom) is None


def test_choose_degrades_on_http_and_parse_failures():
    assert _chooser(status=500).choose(_CANDIDATES, "Mario") is None
    assert _chooser(raw=b"{not json").choose(_CANDIDATES, "Mario") is None
    assert _chooser(raw=b"").choose(_CANDIDATES, "Mario") is None
    assert _chooser(raw=json.dumps({"choices": []}).encode()).choose(
        _CANDIDATES, "Mario"
    ) is None
    assert _chooser(raw=json.dumps("[]").encode()).choose(_CANDIDATES, "Mario") is None
    # Malformed / unexpected message shapes must never raise.
    for body in ({"choices": "nope"}, {"choices": [{}]}, {"choices": ["x"]},
                 {"choices": [{"message": {"content": 5}}]}):
        assert _chooser(raw=json.dumps(body).encode()).choose(
            _CANDIDATES, "Mario"
        ) is None


def test_choose_is_callable_and_closes_an_erroring_response():
    closed = []

    class WeirdResponse(FakeResponse):
        # Only a ``code`` attribute (no ``status``) and a close that raises.
        def __init__(self, data=b""):
            super().__init__(data)
            del self.status
            self.code = 200

        def close(self):
            closed.append(True)
            raise OSError("close blew up")

    chooser = LLMCoverChooser(
        api_key="sk",
        urlopen=lambda request, timeout=None: WeirdResponse(
            json.dumps(
                {"choices": [{"message": {"content": "Mario Party (USA).png"}}]}
            ).encode()
        ),
    )
    # The chooser instance is callable, matching the service's usage.
    assert chooser(_CANDIDATES, "Mario") == "Mario Party (USA).png"
    assert closed == [True]


def test_choose_rejects_an_oversized_response():
    chooser = _chooser(raw=b"x" * (MAX_LLM_RESPONSE_BYTES + 1))
    assert chooser.choose(_CANDIDATES, "Mario") is None


def test_chooser_repr_never_leaks_the_api_key():
    chooser = LLMCoverChooser(api_key="sk-super-secret")
    assert "sk-super-secret" not in repr(chooser)


# --- selectable OpenAI / Anthropic protocol ----------------------------------


def test_protocols_use_wire_shape_names_and_accept_legacy_values():
    assert PROTOCOL_OPENAI == "openai-completions"
    assert PROTOCOL_ANTHROPIC == "anthropic-messages"
    assert DEFAULT_LLM_PROTOCOL == PROTOCOL_OPENAI
    assert "gemini" not in LLM_PROTOCOLS
    assert LLM_PROTOCOLS == (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

    assert normalize_protocol(None) == PROTOCOL_OPENAI
    assert normalize_protocol("") == PROTOCOL_OPENAI
    assert normalize_protocol("gemini") == PROTOCOL_OPENAI  # unsupported -> default
    # Legacy keys from an older config keep working.
    assert normalize_protocol("openai") == PROTOCOL_OPENAI
    assert normalize_protocol("anthropic") == PROTOCOL_ANTHROPIC
    assert normalize_protocol("OPENAI") == PROTOCOL_OPENAI


def test_default_protocol_is_openai_and_openai_request_is_unchanged():
    captured = {}

    def opener(request, timeout=None):
        captured["request"] = request
        return _chat_response("Mario Party (USA).png")

    choice = choose_cover_filename(
        _CANDIDATES, "Mario", api_key="sk-test", urlopen=opener
    )
    assert choice == "Mario Party (USA).png"
    assert captured["request"].full_url == DEFAULT_LLM_BASE_URL + "/chat/completions"
    assert captured["request"].get_header("Authorization") == "Bearer sk-test"


def test_anthropic_choose_sends_messages_request():
    captured = {}

    def opener(request, timeout=None):
        captured["request"] = request
        return FakeResponse(
            json.dumps(
                {"content": [{"type": "text", "text": "Mario Party (USA).png"}]}
            ).encode("utf-8")
        )

    choice = choose_cover_filename(
        _CANDIDATES,
        "Mario",
        api_key="sk-ant",
        protocol=PROTOCOL_ANTHROPIC,
        urlopen=opener,
    )

    assert choice == "Mario Party (USA).png"
    request = captured["request"]
    assert request.method == "POST"
    assert request.full_url == DEFAULT_ANTHROPIC_BASE_URL + "/messages"
    # The Anthropic key travels in x-api-key, never as a Bearer token.
    assert request.get_header("X-api-key") == "sk-ant"
    assert request.get_header("Authorization") is None
    assert request.get_header("Anthropic-version") == ANTHROPIC_API_VERSION
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == DEFAULT_ANTHROPIC_MODEL
    assert body["max_tokens"] > 0
    assert body["temperature"] == 0
    assert isinstance(body["system"], str) and body["system"]
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert "Mario Party (USA).png" in body["messages"][0]["content"]


def test_anthropic_choose_parses_content_blocks_and_ignores_prose():
    chooser = LLMCoverChooser(
        api_key="sk-ant",
        protocol=PROTOCOL_ANTHROPIC,
        urlopen=lambda request, timeout=None: FakeResponse(
            json.dumps(
                {
                    "content": [
                        {"type": "text", "text": "Best match: Mario Kart 7 (USA).png"},
                        {"type": "text", "text": ""},
                    ]
                }
            ).encode("utf-8")
        ),
    )
    assert chooser.choose(_CANDIDATES, "Mario") == "Mario Kart 7 (USA).png"
    assert chooser.protocol == PROTOCOL_ANTHROPIC


def test_anthropic_choose_degrades_on_malformed_content():
    for body in (
        {"content": []},
        {"content": "nope"},
        {"content": [{}]},
        {"content": [{"type": "text"}]},
        {},
    ):
        chooser = LLMCoverChooser(
            api_key="sk-ant",
            protocol=PROTOCOL_ANTHROPIC,
            urlopen=lambda request, timeout=None, body=body: FakeResponse(
                json.dumps(body).encode("utf-8")
            ),
        )
        assert chooser.choose(_CANDIDATES, "Mario") is None


def test_chooser_repr_includes_protocol_but_never_the_key():
    chooser = LLMCoverChooser(api_key="sk-super-secret", protocol=PROTOCOL_ANTHROPIC)
    text = repr(chooser)
    assert "sk-super-secret" not in text
    assert PROTOCOL_ANTHROPIC in text


# --- base URL completion ------------------------------------------------------


def test_normalize_base_url_appends_v1_only_when_missing():
    assert normalize_base_url("https://api.deepseek.com", PROTOCOL_OPENAI) == (
        "https://api.deepseek.com/v1"
    )
    assert normalize_base_url("https://api.deepseek.com/", PROTOCOL_OPENAI) == (
        "https://api.deepseek.com/v1"
    )
    assert normalize_base_url("https://api.deepseek.com/v1", PROTOCOL_OPENAI) == (
        "https://api.deepseek.com/v1"
    )
    # An existing /v1 is never duplicated (trailing slash and all).
    assert normalize_base_url("https://api.deepseek.com/v1/", PROTOCOL_OPENAI) == (
        "https://api.deepseek.com/v1/"
    )
    assert normalize_base_url("https://openrouter.ai/api/v1", PROTOCOL_OPENAI) == (
        "https://openrouter.ai/api/v1"
    )


def test_normalize_base_url_blank_uses_the_protocol_default():
    assert normalize_base_url(None, PROTOCOL_OPENAI) == DEFAULT_LLM_BASE_URL
    assert normalize_base_url("", PROTOCOL_OPENAI) == DEFAULT_LLM_BASE_URL
    assert normalize_base_url("   ", PROTOCOL_ANTHROPIC) == DEFAULT_ANTHROPIC_BASE_URL
    assert normalize_base_url(None, PROTOCOL_ANTHROPIC) == DEFAULT_ANTHROPIC_BASE_URL


def test_chooser_completes_a_missing_v1_endpoint():
    chooser = LLMCoverChooser(
        api_key="sk", base_url="https://api.deepseek.com", protocol=PROTOCOL_OPENAI
    )
    assert chooser.base_url == "https://api.deepseek.com/v1"


# --- debug log + connectivity probe ------------------------------------------


def test_choose_writes_debug_log_without_the_key(tmp_path):
    log = tmp_path / "llm-cover.log"
    choice = choose_cover_filename(
        _CANDIDATES,
        "Mario",
        api_key="sk-secret-value",
        urlopen=lambda request, timeout=None: _chat_response("Mario Party (USA).png"),
        log_path=log,
    )
    assert choice == "Mario Party (USA).png"
    text = log.read_text(encoding="utf-8")
    assert "request" in text and "result" in text
    assert "Mario" in text
    assert "Mario Party (USA).png" in text
    assert "sk-secret-value" not in text


def test_choose_logs_failure_reason(tmp_path):
    log = tmp_path / "llm-cover.log"

    def boom(request, timeout=None):
        raise urllib.error.URLError("offline")

    assert (
        choose_cover_filename(
            _CANDIDATES, "Mario", api_key="sk", urlopen=boom, log_path=log
        )
        is None
    )
    text = log.read_text(encoding="utf-8")
    assert "error" in text and "URLError" in text


def test_probe_reports_the_reply_and_logs(tmp_path):
    log = tmp_path / "llm-cover.log"
    chooser = LLMCoverChooser(
        api_key="sk-test",
        urlopen=lambda request, timeout=None: _chat_response("OK"),
        log_path=log,
    )
    ok, detail = chooser.probe()
    assert ok is True
    assert detail == "OK"
    text = log.read_text(encoding="utf-8")
    assert "probe" in text and "OK" in text
    assert "sk-test" not in text


def test_probe_reports_http_failure_and_accepts_anthropic(tmp_path):
    failing = LLMCoverChooser(
        api_key="sk-test",
        urlopen=lambda request, timeout=None: FakeResponse(b"{}", status=401),
    )
    ok, detail = failing.probe()
    assert ok is False
    assert "401" in detail

    body = {"content": [{"type": "text", "text": "OK"}]}
    anthropic = LLMCoverChooser(
        api_key="sk",
        protocol=PROTOCOL_ANTHROPIC,
        urlopen=lambda request, timeout=None: FakeResponse(
            json.dumps(body).encode("utf-8"), status=200
        ),
    )
    assert anthropic.probe() == (True, "OK")


def test_probe_refuses_without_a_key_and_never_calls_the_network():
    def boom(request, timeout=None):
        raise AssertionError("probe must not hit the network without a key")

    ok, detail = LLMCoverChooser(api_key="", urlopen=boom).probe()
    assert ok is False
    assert "密钥" in detail


def test_debug_log_is_optional(tmp_path):
    """No ``log_path`` means no file is written anywhere."""
    LLMCoverChooser(
        api_key="sk",
        urlopen=lambda request, timeout=None: _chat_response("OK"),
    ).probe()
    assert list(tmp_path.iterdir()) == []


# --- User-Agent (Cloudflare-fronted gateways ban Python-urllib, cf. error 1010) --


def test_openai_request_sends_a_real_user_agent():
    from vajsave.artwork.llm_choice import LLM_USER_AGENT

    captured = {}

    def opener(request, timeout=None):
        captured["ua"] = request.get_header("User-agent")
        return _chat_response("OK")

    choose_cover_filename(_CANDIDATES, "Mario", api_key="sk", urlopen=opener)
    assert captured["ua"] == LLM_USER_AGENT
    assert "urllib" not in captured["ua"].lower()


def test_anthropic_request_sends_a_real_user_agent():
    from vajsave.artwork.llm_choice import LLM_USER_AGENT

    captured = {}

    def opener(request, timeout=None):
        captured["ua"] = request.get_header("User-agent")
        return FakeResponse(
            json.dumps({"content": [{"type": "text", "text": "OK"}]}).encode(),
            status=200,
        )

    chooser = LLMCoverChooser(
        api_key="sk", protocol=PROTOCOL_ANTHROPIC, urlopen=opener
    )
    chooser.probe()
    assert captured["ua"] == LLM_USER_AGENT
    assert "urllib" not in captured["ua"].lower()


def test_probe_uses_a_neutral_system_prompt_not_the_chooser_one():
    captured = {}

    def opener(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _chat_response("OK")

    LLMCoverChooser(api_key="sk", urlopen=opener).probe()
    system = captured["body"]["messages"][0]["content"]
    assert "connectivity check" in system.lower()
    assert "box-art" not in system.lower()
