import logging
from types import SimpleNamespace

from google.genai import types

from lookout_wire.adapters.gemini import GeminiExplainer, NullExplainer, build_explainer
from lookout_wire.core.ports import Explanation

MODEL = "gemini-3.8-flash"


def chunk(title=None, domain=None):
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    return SimpleNamespace(web=SimpleNamespace(uri=redirect, title=title, domain=domain))


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


def test_text_and_first_three_source_names():
    titles = ["reuters.com", "cnbc.com", "wsj.com", "bloomberg.com"]
    explainer, _ = explainer_returning(
        response("  Shares fell on guidance.  ", [chunk(title=t) for t in titles])
    )

    assert explainer.explain("why?") == Explanation(
        text="Shares fell on guidance.", sources=("reuters.com", "cnbc.com", "wsj.com")
    )


def test_no_grounding_metadata_gives_no_sources():
    explainer, _ = explainer_returning(response("Shares fell."))

    assert explainer.explain("why?") == Explanation(text="Shares fell.", sources=())


def test_timeout_returns_none_and_logs_warning(caplog):
    explainer, _ = explainer_returning(TimeoutError("read timed out"))

    with caplog.at_level(logging.WARNING):
        assert explainer.explain("why?") is None

    assert "TimeoutError" in caplog.text
    assert "secret-key" not in caplog.text


def test_source_name_falls_back_to_the_domain():
    explainer, _ = explainer_returning(response("Shares fell.", [chunk(domain="barrons.com")]))

    assert explainer.explain("why?").sources == ("barrons.com",)


def test_duplicate_source_names_are_kept_once():
    chunks = [chunk(title="reuters.com"), chunk(title="reuters.com"), chunk(title="cnbc.com")]
    explainer, _ = explainer_returning(response("Shares fell.", chunks))

    assert explainer.explain("why?").sources == ("reuters.com", "cnbc.com")


def test_empty_text_returns_none():
    explainer, _ = explainer_returning(response("   ", [chunk(title="a.com")]))

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
