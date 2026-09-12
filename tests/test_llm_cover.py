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
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    MAX_LLM_RESPONSE_BYTES,
    LLMCoverChooser,
    choose_cover_filename,
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
