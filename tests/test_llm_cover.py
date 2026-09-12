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
    DEFAULT_LLM_PRESET,
    DEFAULT_LLM_PROTOCOL,
    LLM_PROTOCOLS,
    MAX_LLM_RESPONSE_BYTES,
    PRESET_ANTHROPIC,
    PRESET_CUSTOM,
    PRESET_DEEPSEEK,
    PRESET_OPENAI,
    PRESET_OPENROUTER,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    LLMCoverChooser,
    choose_cover_filename,
    fill_from_preset,
    get_llm_preset,
    llm_preset_keys,
    llm_presets,
    normalize_preset,
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


def test_default_protocol_is_openai_and_openai_request_is_unchanged():
    assert DEFAULT_LLM_PROTOCOL == PROTOCOL_OPENAI
    assert normalize_protocol(None) == PROTOCOL_OPENAI
    assert normalize_protocol("") == PROTOCOL_OPENAI
    assert normalize_protocol("gemini") == PROTOCOL_OPENAI  # unsupported -> default
    assert "gemini" not in LLM_PROTOCOLS
    assert LLM_PROTOCOLS == (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

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


# --- provider presets: protocol / base URL / model combinations --------------


def test_llm_preset_catalogue_has_the_expected_keys_and_no_gemini():
    assert llm_preset_keys() == (
        PRESET_OPENAI,
        PRESET_ANTHROPIC,
        PRESET_DEEPSEEK,
        PRESET_OPENROUTER,
        PRESET_CUSTOM,
    )
    assert DEFAULT_LLM_PRESET == PRESET_OPENAI
    assert "gemini" not in llm_preset_keys()


def test_llm_presets_declare_correct_protocol_and_base():
    presets = {preset.key: preset for preset in llm_presets()}

    assert presets[PRESET_OPENAI].protocol == PROTOCOL_OPENAI
    assert presets[PRESET_OPENAI].base_url == DEFAULT_LLM_BASE_URL
    assert presets[PRESET_OPENAI].model == DEFAULT_LLM_MODEL

    assert presets[PRESET_ANTHROPIC].protocol == PROTOCOL_ANTHROPIC
    assert presets[PRESET_ANTHROPIC].base_url == DEFAULT_ANTHROPIC_BASE_URL
    assert presets[PRESET_ANTHROPIC].model == DEFAULT_ANTHROPIC_MODEL

    assert presets[PRESET_DEEPSEEK].protocol == PROTOCOL_OPENAI
    assert presets[PRESET_DEEPSEEK].base_url == "https://api.deepseek.com/v1"
    assert presets[PRESET_DEEPSEEK].model == "deepseek-chat"

    assert presets[PRESET_OPENROUTER].protocol == PROTOCOL_OPENAI
    assert presets[PRESET_OPENROUTER].base_url == "https://openrouter.ai/api/v1"

    assert presets[PRESET_CUSTOM].protocol == PROTOCOL_OPENAI


def test_normalize_preset_falls_back_to_openai():
    assert normalize_preset(None) == PRESET_OPENAI
    assert normalize_preset("") == PRESET_OPENAI
    assert normalize_preset("gemini") == PRESET_OPENAI
    assert normalize_preset("DeepSeek") == PRESET_DEEPSEEK
    assert normalize_preset(PRESET_CUSTOM) == PRESET_CUSTOM


def test_get_llm_preset_falls_back_to_default_on_unknown_key():
    assert get_llm_preset("nope").key == PRESET_OPENAI
    assert get_llm_preset(PRESET_DEEPSEEK).key == PRESET_DEEPSEEK
    assert get_llm_preset(PRESET_ANTHROPIC).protocol == PROTOCOL_ANTHROPIC


def test_fill_from_preset_fills_defaults_and_spares_customised_values():
    openai = get_llm_preset(PRESET_OPENAI)
    deepseek = get_llm_preset(PRESET_DEEPSEEK)

    # Fields still holding the source preset's values follow the new preset.
    assert fill_from_preset(
        deepseek, openai, PROTOCOL_OPENAI, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL
    ) == (PROTOCOL_OPENAI, "https://api.deepseek.com/v1", "deepseek-chat")

    # A customised endpoint/model is never clobbered by a preset switch.
    assert fill_from_preset(
        deepseek, openai, PROTOCOL_OPENAI, "https://my.gateway/v1", "my-model"
    ) == (PROTOCOL_OPENAI, "https://my.gateway/v1", "my-model")


def test_fill_from_preset_custom_keeps_every_current_value():
    custom = get_llm_preset(PRESET_CUSTOM)
    openai = get_llm_preset(PRESET_OPENAI)

    assert fill_from_preset(
        custom, openai, PROTOCOL_ANTHROPIC, "https://my.gateway/v1", "my-model"
    ) == (PROTOCOL_ANTHROPIC, "https://my.gateway/v1", "my-model")


def test_fill_from_preset_fills_blank_fields():
    anthropic = get_llm_preset(PRESET_ANTHROPIC)
    openai = get_llm_preset(PRESET_OPENAI)

    assert fill_from_preset(anthropic, openai, "", "", "") == (
        PROTOCOL_ANTHROPIC,
        DEFAULT_ANTHROPIC_BASE_URL,
        DEFAULT_ANTHROPIC_MODEL,
    )


def test_fill_from_preset_switches_protocol_when_still_the_source_value():
    anthropic = get_llm_preset(PRESET_ANTHROPIC)
    openai = get_llm_preset(PRESET_OPENAI)

    filled_protocol, _, _ = fill_from_preset(
        anthropic, openai, PROTOCOL_OPENAI, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL
    )
    assert filled_protocol == PROTOCOL_ANTHROPIC

    # A protocol the user already changed stays put even when switching preset.
    deepseek = get_llm_preset(PRESET_DEEPSEEK)
    filled_protocol, _, _ = fill_from_preset(
        deepseek, openai, PROTOCOL_ANTHROPIC, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL
    )
    assert filled_protocol == PROTOCOL_ANTHROPIC
