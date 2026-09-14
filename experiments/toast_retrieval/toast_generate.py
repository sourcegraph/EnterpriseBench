"""Toast-1 generation_fn: the model-turn seam Toast's harness calls.

Retrieval (agent_harness.RetrievalClient) and generation (this module) are
independent seams — the adapter in sourcegraph_client.py is fully verified
and needs nothing from here. This module is the one piece genuinely blocked
on an MXBAI_API_KEY: Toast-1 itself is API-only (no released weights), served
through Mixedbread's OpenAI-compatible Chat Completions endpoint.

The `model=` string and base URL below are confirmed against
https://www.mixedbread.com/docs/agent/chat-completions and
https://www.mixedbread.com/docs/agent/models (checked 2026-08-17): base URL
`https://api.mixedbread.com/v1`, model id `toast-1`, key env `MXBAI_API_KEY`.
Toast-1 there is a 131K-context model with a 4K max output.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# This repo keeps its secrets in a gitignored .env.local at the root, not a
# .env (see scripts/orchestration/run_task.py's own loader). load_dotenv's
# default search only finds .env, so name the file.
_REPO_ENV_LOCAL = Path(__file__).resolve().parents[2] / ".env.local"

_DEFAULT_BASE_URL = "https://api.mixedbread.com/v1"
_DEFAULT_MODEL = "toast-1"


def _resolve_api_key() -> str:
    # agent_harness.tools.functions.resolve_mixedbread_api_key does the same
    # load_dotenv() before reading these vars; match it so a .env-based key
    # resolves identically on both seams. Neither call overrides an ambient
    # value, matching run_task.py's setdefault semantics for .env.local.
    load_dotenv()
    load_dotenv(_REPO_ENV_LOCAL)
    for env_var in ("MXBAI_API_KEY", "MBREAD_API_KEY", "MIXEDBREAD_API_KEY"):
        value = os.environ.get(env_var)
        if value:
            return value
    raise RuntimeError(
        "No Mixedbread API key found (checked MXBAI_API_KEY, MBREAD_API_KEY, "
        "MIXEDBREAD_API_KEY). Toast-1 generation cannot run without one — the "
        "retrieval adapter (sourcegraph_client.py) works standalone and does "
        "not need this key; only an actual Toast-1 model call does."
    )


def build_generate_fn(
    *,
    base_url: str = _DEFAULT_BASE_URL,
    model: str = _DEFAULT_MODEL,
    usage_sink: list[dict] | None = None,
):
    """Build a sync `generation_fn` for `agent_harness.run_searcher`/`aio.*`.

    Raises immediately if no API key is available, rather than returning a
    closure that fails on first use deep inside a rollout.

    `usage_sink`, when given, accumulates the API-reported `usage` block from
    every completion. The harness's own input/output token numbers come from
    its internal budgeter (chars/4 when no exact tokenizer is installed, which
    is the canonical setup here); the API response carries the served model's
    real counts, and discarding them is why the first sweep could only report
    estimates. Toast-1 is not on OpenRouter or any other metering proxy --
    this response field is the only exact count available.
    """
    import openai
    from agent_harness import apply_force_submit

    api_key = _resolve_api_key()
    client = openai.OpenAI(base_url=base_url, api_key=api_key)

    def generate(
        messages,
        *,
        tools,
        completion_config,
        force_submit: bool = False,
        forced_tool_name: str = "submit_ranking",
    ):
        config = dict(completion_config)
        if force_submit:
            apply_force_submit(config, forced_tool_name)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=config["tool_choice"],
            parallel_tool_calls=config["parallel_tool_calls"],
            temperature=0.7,
            top_p=0.95,
            max_tokens=4096,
        )
        usage = getattr(response, "usage", None)
        if usage_sink is not None and usage is not None:
            usage_sink.append(
                {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                }
            )
        return response

    return generate
