"""Minimal synchronous Sourcegraph GraphQL client.

Mirrors the auth/header pattern already proven in
``scripts/infra/verify_sg_indexing.py`` (User-Agent override, `token`
Authorization scheme) so this experiment talks to the instance the same way
the rest of EnterpriseBench does. Kept dependency-free (stdlib `urllib`) —
the async adapter wraps calls in `asyncio.to_thread`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_ENDPOINT = "https://demo.sourcegraph.com"
USER_AGENT = "EnterpriseBench-toast-retrieval/0.1"
REQUEST_TIMEOUT = 30


class GraphQLError(RuntimeError):
    """A Sourcegraph GraphQL call failed transport-side or returned errors."""


@dataclass(frozen=True)
class SourcegraphConfig:
    endpoint: str = DEFAULT_ENDPOINT
    token: str | None = None

    @classmethod
    def from_env(cls) -> "SourcegraphConfig":
        return cls(
            endpoint=os.environ.get("SOURCEGRAPH_ENDPOINT", DEFAULT_ENDPOINT),
            token=os.environ.get("SOURCEGRAPH_ACCESS_TOKEN"),
        )


def graphql(config: SourcegraphConfig, query: str, variables: dict) -> dict:
    """Run one GraphQL query synchronously; returns the ``data`` payload.

    Raises ``GraphQLError`` on transport failure or a GraphQL-level error —
    callers decide how that maps onto the Toast harness's provider-error
    classification, not this module.
    """
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if config.token:
        headers["Authorization"] = f"token {config.token}"

    body = json.dumps({"query": query, "variables": variables}).encode()
    url = f"{config.endpoint.rstrip('/')}/.api/graphql"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise GraphQLError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise GraphQLError(f"transport: {exc.reason}") from exc
    except OSError as exc:
        raise GraphQLError(f"transport: {exc}") from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GraphQLError("malformed response body") from exc

    if payload.get("errors"):
        raise GraphQLError(str(payload["errors"]))
    return payload.get("data") or {}
