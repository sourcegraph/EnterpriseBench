"""Offline test for the usage-capture seam in toast_generate.

Toast-1 is served only by Mixedbread's API -- no OpenRouter, no proxy that
could meter it for us. The response's `usage` block is therefore the single
source of exact token counts, and the first sweep threw it away (reports
carried only the harness's chars/4 estimate). This pins that the sink
receives what the API reported.
"""

from types import SimpleNamespace

import pytest

import toast_generate


class _StubCompletions:
    def create(self, **kwargs):
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=393, completion_tokens=16),
            choices=[],
        )


class _StubClient:
    def __init__(self, *, base_url, api_key):
        self.chat = SimpleNamespace(completions=_StubCompletions())


def test_generate_appends_api_usage_to_sink(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "OpenAI", _StubClient)
    monkeypatch.setenv("MXBAI_API_KEY", "test-key")

    sink: list[dict] = []
    generate = toast_generate.build_generate_fn(usage_sink=sink)
    for _ in range(2):
        generate(
            [{"role": "user", "content": "q"}],
            tools=[],
            completion_config={"tool_choice": "auto", "parallel_tool_calls": True},
        )

    assert sink == [
        {"prompt_tokens": 393, "completion_tokens": 16},
        {"prompt_tokens": 393, "completion_tokens": 16},
    ]


def test_generate_without_sink_still_returns_response(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "OpenAI", _StubClient)
    monkeypatch.setenv("MXBAI_API_KEY", "test-key")

    generate = toast_generate.build_generate_fn()
    response = generate(
        [{"role": "user", "content": "q"}],
        tools=[],
        completion_config={"tool_choice": "auto", "parallel_tool_calls": True},
    )
    assert response.usage.prompt_tokens == 393
