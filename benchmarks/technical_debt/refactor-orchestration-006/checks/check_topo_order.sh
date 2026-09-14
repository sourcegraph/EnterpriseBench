#!/usr/bin/env bash
# check_topo_order.sh — checkpoint "topological_order"
#
# Validates the proposed order against ground_truth.json:dependency_graph via
# the shared eb_verify topological_order plugin (plugin unchanged; three sibling
# tasks use it). What changed is the graph, and a gate in front of it.
#
# The graph was a four-deep staging cascade (apimachinery -> api -> client-go ->
# apiserver) rooted at a "build-infra" node, with "distroless-images" and
# "e2e-infra" hanging off it. Three of those seven nodes DO NOT EXIST — no such
# repo, module or directory is in the checkout — and the four that do need no
# work at all: a Go toolchain bump touches nothing under staging/src/. The graph
# is now the GOVERNS graph the repo declares about itself: build/dependencies.yaml
# carries a version plus a refPaths list per entry, so it lands first and its six
# refPath targets follow. See ground_truth.json:_premise_correction.
#
# Fixing the graph alone would leave free credit here, and this is the subtle
# part: resolve_tokens_to_graph drops tokens that are not graph nodes, so the old
# cascade resolves to nothing and scores 0.0 rather than being credited. But the
# asserted staging edges are REAL as Go module facts — client-go does require
# apimachinery — so an agent can "verify" the chain in a go.mod and still be
# wrong, having never checked whether a toolchain bump traverses it. The sequence
# alone cannot separate a derived plan from a recited one.
#
# The order is therefore gated on evidence it was DERIVED: the plan must name the
# manifest that declares the propagation set ('dependencies.yaml', ZERO hits in
# instruction.md). That is what makes the set knowable rather than guessable, and
# it is readable only in the checkout (EnterpriseBench-jn73.2.7.3.1.2).
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

# Fixed-string containment, never a regex.
lowered = plan_text.lower()
missing = [t for t in tokens if t.lower() not in lowered]
if missing:
    verdict(0.0,
            "Order not derived: plan never names the manifest that declares "
            "the propagation set (%s), so its sequence is an assumption about "
            "Kubernetes staging rather than a reading of this checkout"
            % (", ".join(missing),))

try:
    from eb_verify.plugins.topological_order import validate_refactor_plan_markdown
except Exception as exc:  # import miss => our infra, not the agent's fault
    verdict(0.0, f"VERIFIER_INFRA_ERROR: cannot import topological_order: {exc}")

result = validate_refactor_plan_markdown(plan_text, dep_graph)
verdict(result["score"], str(result["detail"]).replace('"', "'"))
PYEOF
