#!/usr/bin/env python3
"""Pre-fetch the pinned sentence-transformers model into the local HF cache.

Downloads eb_verify.fact_coverage.SentenceTransformerEmbedder's pinned
(model_name, revision) once, so a later run with HF_HUB_OFFLINE=1 set can
load it with no network access. Intended to run at image-build time for
whatever environment executes fact_coverage/fact_triples scoring — no such
build step exists yet in scripts/sandbox/dockerfile_generator.py or any task
Dockerfile; this script is the piece that step would call.

Usage:
    python3 scripts/infra/prefetch_embedding_model.py
    python3 scripts/infra/prefetch_embedding_model.py --model sentence-transformers/all-MiniLM-L6-v2 --revision <sha>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))


def main() -> None:
    from eb_verify.fact_coverage import SentenceTransformerEmbedder

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=SentenceTransformerEmbedder.DEFAULT_MODEL_NAME)
    parser.add_argument("--revision", default=SentenceTransformerEmbedder.DEFAULT_MODEL_REVISION)
    args = parser.parse_args()

    print(f"Fetching {args.model}@{args.revision} into the local Hugging Face cache...")
    embedder = SentenceTransformerEmbedder(model_name=args.model, revision=args.revision)
    # Exercise encode() once so all lazily-loaded weights are actually pulled,
    # not just the config/tokenizer files SentenceTransformer() reads eagerly.
    embedder.embed(["cache warmup"])
    print("Done. Set HF_HUB_OFFLINE=1 to load this model with no network access.")


if __name__ == "__main__":
    main()
