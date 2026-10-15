import logging
from types import SimpleNamespace

from google.genai import types

from lookout_wire.adapters.gemini import GeminiExplainer, NullExplainer, build_explainer
from lookout_wire.core.ports import Explanation

MODEL = "gemini-3.5-flash-lite"


def chunk(uri):
    return SimpleNamespace(web=SimpleNamespace(uri=uri))


def response(text, chunks=None):
    metadata = None if chunks is None else SimpleNamespace(grounding_chunks=chunks)
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(grounding_metadata=metadata)])


class StubModels:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def explainer_returning(result):
    models = StubModels(result)
    client = SimpleNamespace(models=models)
    explainer = GeminiExplainer(
        api_key="secret-key", model=MODEL, timeout_seconds=15, client_factory=lambda **_: client
    )
    return explainer, models


def test_text_and_first_three_https_links():
    uris = [f"https://example.com/{n}" for n in range(4)]
    explainer, _ = explainer_returning(
        response("  Shares fell on guidance.  ", [chunk(u) for u in uris])
    )

    assert explainer.explain("why?") == Explanation(
        text="Shares fell on guidance.", links=tuple(uris[:3])
    )


def test_no_grounding_metadata_gives_no_links():
    explainer, _ = explainer_returning(response("Shares fell."))

    assert explainer.explain("why?") == Explanation(text="Shares fell.", links=())


def test_timeout_returns_none_and_logs_warning(caplog):
    explainer, _ = explainer_returning(TimeoutError("read timed out"))

    with caplog.at_level(logging.WARNING):
        assert explainer.explain("why?") is None

    assert "TimeoutError" in caplog.text
    assert "secret-key" not in caplog.text


def test_non_https_uri_is_dropped():
    chunks = [chunk("http://plain.example.com"), chunk("https://secure.example.com")]
    explainer, _ = explainer_returning(response("Shares fell.", chunks))

    assert explainer.explain("why?").links == ("https://secure.example.com",)


def test_duplicate_links_are_kept_once():
    chunks = [
        chunk("https://a.example.com"),
        chunk("https://a.example.com"),
        chunk("https://b.example.com"),
    ]
    explainer, _ = explainer_returning(response("Shares fell.", chunks))

    assert explainer.explain("why?").links == ("https://a.example.com", "https://b.example.com")


def test_empty_text_returns_none():
    explainer, _ = explainer_returning(response("   ", [chunk("https://a.example.com")]))

    assert explainer.explain("why?") is None


def test_passes_model_question_and_google_search_tool():
    explainer, models = explainer_returning(response("Shares fell."))

    explainer.explain("Why is ACME down today?")

    (call,) = models.calls
    assert call["model"] == MODEL
    assert call["contents"] == "Why is ACME down today?"
    assert call["config"].tools == [types.Tool(google_search=types.GoogleSearch())]


def test_default_factory_receives_key_and_timeout_in_milliseconds():
    received = {}

    def factory(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(models=StubModels(response("x")))

    GeminiExplainer(api_key="secret-key", model=MODEL, timeout_seconds=15, client_factory=factory)

    assert received["api_key"] == "secret-key"
    assert received["http_options"].timeout == 15_000


def test_null_explainer_returns_none():
    assert NullExplainer().explain("why?") is None


def test_build_explainer_without_key_constructs_no_client(monkeypatch):
    def fail(**_):
        raise AssertionError("client constructed")

    monkeypatch.setattr("lookout_wire.adapters.gemini.genai.Client", fail)

    assert isinstance(build_explainer(None, MODEL, 15), NullExplainer)
    assert isinstance(build_explainer("", MODEL, 15), NullExplainer)


def test_build_explainer_with_key_returns_gemini(monkeypatch):
    monkeypatch.setattr("lookout_wire.adapters.gemini.genai.Client", lambda **_: SimpleNamespace())

    assert isinstance(build_explainer("secret-key", MODEL, 15), GeminiExplainer)


def test_api_error_logs_model_status_and_google_message(caplog):
    from google.genai import errors

    quota = errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "You exceeded your current quota, please check your plan and billing "
                "details. For more information on this error, head to: https://ai.google.dev/x.",
            }
        },
    )
    explainer, _ = explainer_returning(quota)

    with caplog.at_level(logging.WARNING):
        assert explainer.explain("why?") is None

    assert (
        f"Gemini explanation failed with model {MODEL}: HTTP 429 RESOURCE_EXHAUSTED: "
        "You exceeded your current quota, please check your plan and billing details."
    ) in caplog.text
    assert "For more information" not in caplog.text
    assert "secret-key" not in caplog.text
