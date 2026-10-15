"""Explainer backed by Gemini with Google Search grounding."""

import logging
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import errors, types

from lookout_wire.core.ports import Explainer, Explanation

logger = logging.getLogger(__name__)

MAX_SOURCES = 3


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
        return Explanation(text=text, sources=_sources(response))

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


def _sources(response: Any) -> tuple[str, ...]:
    """Short source names, usually the site's domain. Gemini's links are long Google redirects."""
    names: list[str] = []
    for name in _chunk_names(response):
        if name not in names:
            names.append(name)
    return tuple(names[:MAX_SOURCES])


def _chunk_names(response: Any) -> list[str]:
    if not response.candidates:
        return []
    metadata = response.candidates[0].grounding_metadata
    if metadata is None:
        return []
    webs = [chunk.web for chunk in metadata.grounding_chunks or [] if chunk.web is not None]
    return [name for web in webs if (name := web.title or web.domain)]


def describe(error: Exception) -> str:
    """Google's status and first sentence for an API error, else the error type. Never the key."""
    if not isinstance(error, errors.APIError):
        return type(error).__name__
    first_sentence = (error.message or "").split(". ")[0].rstrip(".")
    return f"HTTP {error.code} {error.status}: {first_sentence}."
