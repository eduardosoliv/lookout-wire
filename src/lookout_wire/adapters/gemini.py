"""Explainer backed by Gemini with Google Search grounding."""

import logging
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import errors, types

from lookout_wire.core.ports import Explainer, Explanation

logger = logging.getLogger(__name__)

MAX_LINKS = 3


class GeminiExplainer:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        factory = client_factory or genai.Client
        timeout_ms = int(timeout_seconds * 1000)
        self._client = factory(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
        self._model = model

    def explain(self, question: str) -> Explanation | None:
        try:
            response = self._generate(question)
        except Exception as error:
            logger.warning(
                "Gemini explanation failed with model %s: %s", self._model, describe(error)
            )
            return None
        text = (response.text or "").strip()
        if not text:
            return None
        return Explanation(text=text, links=_links(response))

    def _generate(self, question: str) -> Any:
        return self._client.models.generate_content(
            model=self._model,
            contents=question,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )


class NullExplainer:
    def explain(self, question: str) -> Explanation | None:
        return None


def build_explainer(api_key: str | None, model: str, timeout_seconds: float) -> Explainer:
    if not api_key:
        return NullExplainer()
    return GeminiExplainer(api_key=api_key, model=model, timeout_seconds=timeout_seconds)


def _links(response: Any) -> tuple[str, ...]:
    links: list[str] = []
    for uri in _chunk_uris(response):
        if uri.startswith("https://") and uri not in links:
            links.append(uri)
    return tuple(links[:MAX_LINKS])


def _chunk_uris(response: Any) -> list[str]:
    if not response.candidates:
        return []
    metadata = response.candidates[0].grounding_metadata
    if metadata is None:
        return []
    chunks = metadata.grounding_chunks or []
    return [chunk.web.uri for chunk in chunks if chunk.web is not None and chunk.web.uri]


def describe(error: Exception) -> str:
    """Google's status and first sentence for an API error, else the error type. Never the key."""
    if not isinstance(error, errors.APIError):
        return type(error).__name__
    first_sentence = (error.message or "").split(". ")[0].rstrip(".")
    return f"HTTP {error.code} {error.status}: {first_sentence}."
