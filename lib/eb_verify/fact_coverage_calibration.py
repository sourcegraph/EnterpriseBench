"""Calibration experiment for fact_coverage embedders.

Labeled set: paraphrase pairs (same code-fact stated differently — SHOULD
match) and near-miss pairs (different symbol / different repo / negation /
right entities, wrong relation — should NOT match). Sweeps the match
threshold and reports precision/recall/F1 per threshold plus the best-F1
operating point.

Pair provenance: every statement is grounded in an actual
``ground_truth.json`` narrative field (``error_chain`` / ``trigger_conditions``
/ ``root_cause``) from a real task under ``benchmarks/`` — see each pair's
``source_task``. Paraphrases are hand-written rewordings of that real
statement; negation/wrong-relation pairs are hand-crafted polarity/direction
flips of it (real GT data does not spontaneously contain a negated copy of
itself, so these two categories cannot be "mined" — they are adversarial
probes construed from real material).

Run:

    PYTHONPATH=lib python3 -m eb_verify.fact_coverage_calibration
    PYTHONPATH=lib python3 -m eb_verify.fact_coverage_calibration --embedder sentence-transformer
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np

from eb_verify.fact_coverage import (
    Embedder,
    SentenceTransformerEmbedder,
    TfidfCharNgramEmbedder,
)


@dataclass(frozen=True)
class LabeledPair:
    a: str
    b: str
    should_match: bool
    kind: str  # paraphrase | different-symbol | different-repo | negation | wrong-relation
    source_task: str  # benchmarks/ task id the statement was drawn from


PARAPHRASE_PAIRS = [
    LabeledPair(
        "Kombu connection pool in kombu/connection.py exhausts available connections to RabbitMQ broker",
        "the connection pool inside kombu/connection.py runs out of connections available to the RabbitMQ broker",
        True, "paraphrase", "err-provenance-dual-celery-001"),
    LabeledPair(
        "autoretry_for=(Exception,) is too broad and catches broker-level ConnectionResetError alongside task exceptions",
        "because autoretry_for is configured to catch the bare Exception class, it ends up catching transport-level ConnectionResetError as well as genuine task errors",
        True, "paraphrase", "err-provenance-dual-celery-001"),
    LabeledPair(
        "urllib3 HTTPSConnectionPool in src/urllib3/connectionpool.py catches SSLError during connection",
        "when establishing a connection, HTTPSConnectionPool in src/urllib3/connectionpool.py catches SSLError",
        True, "paraphrase", "err-provenance-dual-requests-001"),
    LabeledPair(
        "requests wraps the exception in requests.exceptions.SSLError with nested exception context",
        "the requests library re-raises the error as requests.exceptions.SSLError, preserving the original exception as context",
        True, "paraphrase", "err-provenance-dual-requests-001"),
    LabeledPair(
        "terraform-provider-aws receives the plan request via gRPC",
        "the plan request is delivered to terraform-provider-aws over gRPC",
        True, "paraphrase", "err-provenance-dual-terraform-001"),
    LabeledPair(
        "Provider validates resource attributes in internal/service validation",
        "resource attribute validation for the provider happens in the internal/service validation code",
        True, "paraphrase", "err-provenance-dual-terraform-001"),
    LabeledPair(
        "Grafana datasource proxy receives HTTP 503 from upstream Prometheus in pkg/api/datasource_proxy.go",
        "in pkg/api/datasource_proxy.go, Grafana's datasource proxy gets back an HTTP 503 response from the upstream Prometheus server",
        True, "paraphrase", "err-provenance-dual-grafana-001"),
    LabeledPair(
        "Grafana proxy discards upstream error response body when translating to frontend error format",
        "when the proxy converts the upstream error into the frontend's error format, it drops the original response body",
        True, "paraphrase", "err-provenance-dual-grafana-001"),
    LabeledPair(
        "pkg/apis/batch/validation/validation.go checks startTime field",
        "the startTime field is checked by validation logic in pkg/apis/batch/validation/validation.go",
        True, "paraphrase", "err-provenance-01"),
    LabeledPair(
        "knftables library invokes nft CLI which segfaults on sets with udata",
        "invoking the nft CLI from the knftables library causes a segfault when sets carry udata",
        True, "paraphrase", "err-provenance-02"),
    LabeledPair(
        "the watch cache incorrectly allows establishing a watch as if it can deliver events for its currently synced resourceVersion",
        "the watch cache mistakenly permits a watch to start at its current resourceVersion even though it cannot actually deliver events from that point",
        True, "paraphrase", "incident-investigation-001"),
    LabeledPair(
        "the case for DELETE events uses event.PrevObject.DeepCopyObject() which retains the previous resourceVersion",
        "for DELETE events, the code copies event.PrevObject, which keeps the old resourceVersion attached",
        True, "paraphrase", "incident-investigation-002"),
    LabeledPair(
        "Blueprint.register_blueprint and the register method that handles URL prefix merging for nested blueprints",
        "the register method, invoked via Blueprint.register_blueprint, is what merges the URL prefix for nested blueprints",
        True, "paraphrase", "calibration-001"),
    LabeledPair(
        "PostStartHook fails, readyz check fails",
        "the failure of the PostStartHook causes the readyz check to fail as well",
        True, "paraphrase", "err-provenance-03"),
]

NEAR_MISS_PAIRS = [
    # different-symbol: same task/repo family, different actual subject
    LabeledPair(
        "Kombu connection pool in kombu/connection.py exhausts available connections to RabbitMQ broker",
        "Celery worker catches the ConnectionResetError as a generic Exception in celery/worker/request.py",
        False, "different-symbol", "err-provenance-dual-celery-001"),
    LabeledPair(
        "urllib3 HTTPSConnectionPool in src/urllib3/connectionpool.py catches SSLError during connection",
        "requests HTTPAdapter in src/requests/adapters.py catches urllib3 MaxRetryError",
        False, "different-symbol", "err-provenance-dual-requests-001"),
    LabeledPair(
        "pkg/registry/batch/job/strategy.go validates the update",
        "pkg/apis/batch/validation/validation.go checks startTime field",
        False, "different-symbol", "err-provenance-01"),
    LabeledPair(
        "Prometheus PromQL engine evaluates query and hits context deadline timeout in promql/engine.go",
        "Grafana proxy discards upstream error response body when translating to frontend error format",
        False, "different-symbol", "err-provenance-dual-grafana-001"),
    # different-repo: structurally similar wording, unrelated systems
    LabeledPair(
        "Kombu connection pool in kombu/connection.py exhausts available connections to RabbitMQ broker",
        "urllib3 HTTPSConnectionPool in src/urllib3/connectionpool.py catches SSLError during connection",
        False, "different-repo", "err-provenance-dual-celery-001 vs err-provenance-dual-requests-001"),
    LabeledPair(
        "terraform-provider-aws receives the plan request via gRPC",
        "Grafana datasource proxy receives HTTP 503 from upstream Prometheus in pkg/api/datasource_proxy.go",
        False, "different-repo", "err-provenance-dual-terraform-001 vs err-provenance-dual-grafana-001"),
    LabeledPair(
        "knftables library invokes nft CLI which segfaults on sets with udata",
        "Provider validates resource attributes in internal/service validation",
        False, "different-repo", "err-provenance-02 vs err-provenance-dual-terraform-001"),
    # negation: hand-crafted polarity flip of a real statement
    LabeledPair(
        "the connection pool in httpcore is thread-safe",
        "the connection pool in httpcore is not thread-safe",
        False, "negation", "err-provenance-tri-httpx-proxy-001"),
    LabeledPair(
        "Kombu connection pool in kombu/connection.py exhausts available connections to RabbitMQ broker",
        "Kombu connection pool in kombu/connection.py does not exhaust available connections to RabbitMQ broker even under heavy load",
        False, "negation", "err-provenance-dual-celery-001"),
    LabeledPair(
        "PostStartHook fails, readyz check fails",
        "PostStartHook succeeds and the readyz check passes without issue",
        False, "negation", "err-provenance-03"),
    # wrong-relation: hand-crafted subject/object reversal of a real statement
    LabeledPair(
        "Grafana datasource proxy receives HTTP 503 from upstream Prometheus in pkg/api/datasource_proxy.go",
        "Prometheus receives HTTP 503 from the Grafana datasource proxy in pkg/api/datasource_proxy.go",
        False, "wrong-relation", "err-provenance-dual-grafana-001"),
    LabeledPair(
        "requests HTTPAdapter in src/requests/adapters.py catches urllib3 MaxRetryError",
        "urllib3 catches a MaxRetryError raised by the requests HTTPAdapter in src/requests/adapters.py",
        False, "wrong-relation", "err-provenance-dual-requests-001"),
    LabeledPair(
        "terraform-provider-aws receives the plan request via gRPC",
        "Terraform core receives the plan request from terraform-provider-aws via gRPC",
        False, "wrong-relation", "err-provenance-dual-terraform-001"),
    LabeledPair(
        "httpx depends on httpcore for its transport layer",
        "httpcore depends on httpx for its transport layer",
        False, "wrong-relation", "err-provenance-tri-httpx-proxy-001"),
]

ALL_PAIRS: list[LabeledPair] = PARAPHRASE_PAIRS + NEAR_MISS_PAIRS

EMBEDDER_FACTORIES = {
    "tfidf": TfidfCharNgramEmbedder,
    "sentence-transformer": SentenceTransformerEmbedder,
}


def pair_similarities(
    pairs: list[LabeledPair] | None = None, embedder: Embedder | None = None
) -> list[tuple[LabeledPair, float]]:
    """Cosine similarity per labeled pair, embedded in one shared vector space."""
    pairs = ALL_PAIRS if pairs is None else pairs
    if not pairs:
        raise ValueError("pairs must be non-empty")
    embedder = embedder if embedder is not None else TfidfCharNgramEmbedder()
    texts = [t for p in pairs for t in (p.a, p.b)]
    emb = embedder.embed(texts)
    return [
        (p, float(np.dot(emb[2 * i], emb[2 * i + 1])))
        for i, p in enumerate(pairs)
    ]


def sweep(
    thresholds: list[float] | None = None,
    embedder: Embedder | None = None,
    sims: list[tuple[LabeledPair, float]] | None = None,
) -> tuple[list[dict], dict]:
    """Sweep thresholds; return (rows, best_row) with precision/recall/F1.

    Pass a precomputed ``sims`` (from ``pair_similarities``) to skip
    re-embedding the pair set when the caller already has it.
    """
    if thresholds is None:
        thresholds = [round(0.05 * i, 2) for i in range(1, 20)]  # 0.05..0.95
    if sims is None:
        sims = pair_similarities(embedder=embedder)
    rows = []
    for t in thresholds:
        tp = sum(1 for p, s in sims if p.should_match and s >= t)
        fp = sum(1 for p, s in sims if not p.should_match and s >= t)
        fn = sum(1 for p, s in sims if p.should_match and s < t)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        rows.append({"threshold": t, "tp": tp, "fp": fp, "fn": fn,
                     "precision": prec, "recall": rec, "f1": f1})
    best = max(rows, key=lambda r: (r["f1"], r["threshold"]))
    return rows, best


def _run(embedder_name: str) -> None:
    embedder = EMBEDDER_FACTORIES[embedder_name]()
    sims = pair_similarities(embedder=embedder)
    print(f"embedder: {embedder_name}")
    print(f"{len(ALL_PAIRS)} labeled pairs "
          f"({sum(p.should_match for p in ALL_PAIRS)} match / "
          f"{sum(not p.should_match for p in ALL_PAIRS)} no-match)\n")

    print("per-pair similarities:")
    for p, s in sorted(sims, key=lambda x: -x[1]):
        label = "MATCH " if p.should_match else "nomatch"
        print(f"  {s:.3f}  {label}  [{p.kind}]  {p.a[:52]!r} vs {p.b[:52]!r}")

    rows, best = sweep(sims=sims)
    print("\nthreshold sweep:")
    print(f"  {'thr':>5} {'tp':>3} {'fp':>3} {'fn':>3} {'prec':>6} {'rec':>6} {'f1':>6}")
    for r in rows:
        marker = "  <-- best" if r is best else ""
        print(f"  {r['threshold']:>5.2f} {r['tp']:>3} {r['fp']:>3} {r['fn']:>3} "
              f"{r['precision']:>6.3f} {r['recall']:>6.3f} {r['f1']:>6.3f}{marker}")
    print(f"\nbest F1 = {best['f1']:.3f} at threshold {best['threshold']:.2f}")
    if best["f1"] < 0.75:
        print(f"VERDICT: {embedder_name} is NOT sufficient (F1 < 0.75) on this set.")
    else:
        print(f"VERDICT: {embedder_name} clears the F1 >= 0.75 bar on this set.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--embedder", choices=sorted(EMBEDDER_FACTORIES), default="tfidf",
    )
    args = parser.parse_args()
    _run(args.embedder)


if __name__ == "__main__":
    main()
