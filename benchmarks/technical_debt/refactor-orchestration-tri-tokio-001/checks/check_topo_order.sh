#!/usr/bin/env bash
# check_topo_order.sh — checkpoint "topological_order"
#
# Validates the proposed order against ground_truth.json:dependency_graph via
# the shared eb_verify topological_order plugin (plugin unchanged; three sibling
# tasks use it). What changed is the graph it validates against, and a gate in
# front of it.
#
# The graph was wrong. It asserted axum -> hyper, but axum 0.6.19 requires
# hyper ^0.14.24 while the workspace vendors hyper 1.0.0-rc.4 — semver
# incompatible, so that edge does not exist here. The real shape is a fan-out
# from tokio. See ground_truth.json:_premise_correction.
#
# Fixing the graph alone would leave this checkpoint as free credit, and this is
# the subtle part: under the CORRECTED fan-out graph, [tokio, hyper, axum] is
# still a valid topological order. It is also exactly what the old prompt stated
# outright, and what any model that knows the Rust async ecosystem guesses
# without opening a file. So the order alone cannot separate a derived plan from
# a recited one, and crediting it would hand 0.45 to a plan that never read the
# repos.
#
# The order is therefore gated on evidence that the graph was DERIVED: the plan
# must cite axum's actual hyper requirement (0.14.x), the fact that kills the
# assumed chain and is readable only in axum/Cargo.toml. "0.14" appears ZERO
# times in instruction.md, which now states no versions at all. Ungated, this
# check credits the ecosystem's reputation instead of the workspace's manifests
# (EnterpriseBench-jn73.2.7.3.1.2).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
ANSWER="$WORKSPACE/REFACTOR_PLAN.md"
GT="${TASK_DIR:-$(dirname "$(dirname "$0")")}/ground_truth.json"
MAX_ANSWER_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$ANSWER" ]]; then
  verdict 0.0 false "REFACTOR_PLAN.md is a symlink, not a regular file"
fi
if [[ ! -f "$ANSWER" ]]; then
  verdict 0.0 false "REFACTOR_PLAN.md not found"
fi
if [[ "$(wc -c <"$ANSWER")" -gt "$MAX_ANSWER_BYTES" ]]; then
  verdict 0.0 false "REFACTOR_PLAN.md exceeds ${MAX_ANSWER_BYTES} bytes"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../../../../lib" 2>/dev/null && pwd || echo "")"
# Trust the fallback only if it carries the module we import: a stale
# eb_verify four levels up (a -d check would wave through) must not shadow
# the PYTHONPATH the harness exports. See EnterpriseBench-4du6t.
[[ -f "$LIB_DIR/eb_verify/plugins/topological_order.py" ]] || LIB_DIR=""
# ...and only when the harness told us where lib/ is at all. The -f guard above
# authenticates PRESENCE, not CONTENT: with PYTHONPATH set, the guessed dir is
# appended BEHIND the authoritative lib and cannot win. With PYTHONPATH empty it
# IS the import source, so an eb_verify planted four levels up would be imported
# unchallenged and could forge a passing verdict. Fail closed there; a human
# running a check by hand outside the harness opts back in explicitly.
# See EnterpriseBench-0idmk.
if [[ -z "${PYTHONPATH:-}" && "${EB_ALLOW_GUESSED_LIB:-}" != "1" ]]; then
  LIB_DIR=""
fi

export GT_FILE="$GT"
export ANSWER_FILE="$ANSWER"

python3 - "$LIB_DIR" <<'PYEOF'
import json, os, sys

def verdict(score, detail):
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

lib_dir = sys.argv[1]
if lib_dir:
    sys.path.append(lib_dir)

with open(os.environ["GT_FILE"]) as fh:
    gt = json.load(fh)

with open(os.environ["ANSWER_FILE"], encoding="utf-8", errors="replace") as fh:
    plan_text = fh.read()

dep_graph = gt.get("dependency_graph") or {}
if not dep_graph:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no dependency_graph in ground_truth.json")

tokens = (gt.get("scoring_evidence") or {}).get("topological_order") or []
if not tokens:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no topological_order evidence in ground_truth.json")

# Fixed-string containment, never a regex: "." in "0.14" would match "0x14".
lowered = plan_text.lower()
missing = [t for t in tokens if t.lower() not in lowered]
if missing:
    verdict(0.0,
            "Order not derived: plan never cites axum's actual hyper requirement "
            "%s, so its ordering is an assumption about the ecosystem rather than "
            "a reading of these manifests" % (", ".join(missing),))

try:
    from eb_verify.plugins.topological_order import validate_refactor_plan_markdown
except Exception as exc:  # import miss => our infra, not the agent's fault
    verdict(0.0, f"VERIFIER_INFRA_ERROR: cannot import topological_order: {exc}")

result = validate_refactor_plan_markdown(plan_text, dep_graph)
verdict(result["score"], str(result["detail"]).replace('"', "'"))
PYEOF
