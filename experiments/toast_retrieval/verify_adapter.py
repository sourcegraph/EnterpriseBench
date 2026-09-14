"""Conformance-test the Sourcegraph adapter against a live instance.

Drives one real Toast rollout (scripted generation — no model call, no
MXBAI_API_KEY needed) through `agent_harness.testing.verify_retrieval_client`
against `SourcegraphRetrievalClient`. This is the checkpoint that proves the
adapter satisfies Toast's retrieval contract before any Toast-1 model call
is attempted.

Usage:
    SOURCEGRAPH_ACCESS_TOKEN=... python3 verify_adapter.py \
        --store github.com/sg-evals/git--v2.41.0 \
        --grep-pattern sslCAInfo
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agent_harness.testing import verify_retrieval_client

from graphql import SourcegraphConfig
from sourcegraph_client import SourcegraphRetrievalClient


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        action="append",
        required=True,
        dest="stores",
        help="sg-evals mirror repo name, e.g. github.com/sg-evals/git--v2.41.0 "
        "(repeatable for a multi-repo store set)",
    )
    parser.add_argument("--query", default="retrieval seam verification probe")
    parser.add_argument("--grep-pattern", default="the")
    args = parser.parse_args()

    config = SourcegraphConfig.from_env()
    if not config.token:
        print("SOURCEGRAPH_ACCESS_TOKEN is not set.", file=sys.stderr)
        return 2

    client = SourcegraphRetrievalClient(config)
    result = await verify_retrieval_client(
        client,
        store_identifiers=args.stores,
        query=args.query,
        grep_pattern=args.grep_pattern,
    )
    print(f"OK: {len(result.chunks)} chunks in final ranking, "
          f"{len(result.queries_made)} queries made, "
          f"{len(result.tool_trace)} tool calls")
    for record in result.queries_made:
        print(f"  {record.get('tool')}: {record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
