#!/usr/bin/env bash
# Checkpoint 2: Verify topological ordering of proposed refactor plan
# Uses bash + jq + grep (no python3 in container — task images ship bash/grep/jq
# but not python/python3, so the previous `python3 -c` body exited 127). Scoring
# semantics are identical to lib/eb_verify/plugins/topological_order.py
# (validate_topological_order), including its cycle check, pairwise constraint
# scoring, coverage penalty, round(score,4), and detail-string format.
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
GT="${TASK_DIR:-$(dirname "$(dirname "$0")")}/ground_truth.json"

if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

if [[ ! -f "$GT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "ground_truth.json not found"}\n'
  exit 0
fi

# Basename-normalize the ground_truth dependency_graph: ground_truth keys repos
# by slug ("spf13/cobra") while agents name them by workspace basename ("cobra").
# Strip everything up to and including the last "/" on both keys and dep values
# so name-matching is form-agnostic; topological correctness is unaffected.
NORM_GT=$(mktemp)
trap 'rm -f "$NORM_GT"' EXIT
jq '.dependency_graph |= (
  to_entries
  | map({key: (.key | sub("^.*/"; "")), value: (.value | map(sub("^.*/"; "")))})
  | from_entries
)' "$GT" > "$NORM_GT"

# Extract ordered repo list from agent's plan (numbered list items)
PROPOSED_FILE=$(mktemp)
grep -oE '^\s*[0-9]+\.\s+\S+' "$ANSWER" | sed 's/^[[:space:]]*[0-9]*\.\s*//' | head -20 > "$PROPOSED_FILE" || true

if [[ ! -s "$PROPOSED_FILE" ]]; then
  rm -f "$PROPOSED_FILE"
  printf '{"score": 0.0, "passed": false, "reason": "No numbered ordering found in plan"}\n'
  exit 0
fi

# proposed[] = non-blank stripped lines (matches python [r.strip() for r in f if r.strip()])
mapfile -t proposed < <(sed 's/^[[:space:]]*//; s/[[:space:]]*$//' "$PROPOSED_FILE" | grep -v '^$' || true)
rm -f "$PROPOSED_FILE"

# Basename-normalize proposed repo tokens to match the normalized graph above.
if [[ ${#proposed[@]} -gt 0 ]]; then
  proposed=("${proposed[@]##*/}")
fi

# Format a number the way python json.dumps prints a float: whole numbers keep a
# trailing ".0" (jq emits bare integers; python emits 1.0 / 0.0).
pyfloat() {
  local v="$1"
  if [[ "$v" != *.* && "$v" != *e* && "$v" != *E* ]]; then
    printf '%s.0' "$v"
  else
    printf '%s' "$v"
  fi
}

emit() { # score passed detail
  printf '{"score": %s, "passed": %s, "reason": "%s"}\n' "$1" "$2" "$3"
}

# Empty proposal -> {"score": 0.0, "detail": "Empty proposal"}; passed = 0.0>=0.5 = false
if [[ ${#proposed[@]} -eq 0 ]]; then
  emit "0.0" "false" "Empty proposal"
  exit 0
fi

# ---- cycle detection (Kahn's algorithm over dependency_graph) ----
# A cycle exists iff fewer than N nodes get visited.
has_cycle=$(jq -r '
  .dependency_graph as $g
  | ($g | keys) as $nodes
  | ($nodes | length) as $n
  # in-degree counts only deps that are themselves graph nodes
  | reduce ($g | to_entries[]) as $e ({};
      .[$e.key] = ([$e.value[] | select(. as $d | $nodes | index($d))] | length))
  | . as $indeg
  # adjacency: dep -> [dependents]
  | (reduce ($g | to_entries[]) as $e ({};
      reduce ($e.value[] | select(. as $d | $nodes | index($d))) as $dep (.;
        .[$dep] = ((.[$dep] // []) + [$e.key]))) ) as $adj
  | {indeg: $indeg, queue: [$nodes[] | select($indeg[.] == 0)], visited: 0}
  | until(.queue | length == 0;
      .queue[0] as $cur
      | .queue = .queue[1:]
      | .visited += 1
      | reduce ($adj[$cur] // [])[] as $dep (.;
          .indeg[$dep] -= 1
          | if .indeg[$dep] == 0 then .queue += [$dep] else . end))
  | (.visited < $n)
' "$NORM_GT")

if [[ "$has_cycle" == "true" ]]; then
  emit "0.0" "false" "Dependency graph contains a cycle"
  exit 0
fi

# graph_nodes
mapfile -t graph_nodes < <(jq -r '.dependency_graph | keys[]' "$NORM_GT")
n_graph=${#graph_nodes[@]}

# is_node helper via associative array
declare -A NODE_SET=()
for gn in "${graph_nodes[@]}"; do NODE_SET["$gn"]=1; done

# known_proposed = proposed filtered to graph nodes, preserving order
known_proposed=()
for r in "${proposed[@]}"; do
  if [[ -n "${NODE_SET[$r]+x}" ]]; then known_proposed+=("$r"); fi
done

if [[ ${#known_proposed[@]} -eq 0 ]]; then
  emit "0.0" "false" "No recognized repos in proposal"
  exit 0
fi

# position map (first occurrence wins — matches python dict comprehension? python
# {repo:i for i,repo in enumerate(known_proposed)} keeps LAST occurrence). Replicate
# python: last write wins.
declare -A POS=()
idx=0
for r in "${known_proposed[@]}"; do
  POS["$r"]=$idx
  idx=$((idx + 1))
done
n_known=${#known_proposed[@]}

# Count pairwise constraints. Iterate dependency_graph entries as "node\tdep".
total_constraints=0
satisfied_constraints=0
while IFS=$'\t' read -r node dep; do
  [[ -z "$node" ]] && continue
  # dep must be a graph node
  [[ -n "${NODE_SET[$dep]+x}" ]] || continue
  total_constraints=$((total_constraints + 1))
  if [[ -n "${POS[$node]+x}" && -n "${POS[$dep]+x}" ]]; then
    if [[ ${POS[$dep]} -lt ${POS[$node]} ]]; then
      satisfied_constraints=$((satisfied_constraints + 1))
    fi
  fi
done < <(jq -r '.dependency_graph | to_entries[] | .key as $k | .value[] | [$k, .] | @tsv' "$NORM_GT")

if [[ $total_constraints -eq 0 ]]; then
  # coverage = n_known / n_graph (n_graph > 0 here since known_proposed nonempty)
  score=$(jq -n --argjson k "$n_known" --argjson g "$n_graph" '[1.0, ($k/$g)] | min')
  passed=$(jq -n --argjson s "$score" 'if $s >= 0.5 then true else false end')
  emit "$(pyfloat "$score")" "$passed" "No dependency constraints to validate"
  exit 0
fi

# constraint_score = satisfied/total ; coverage = n_known/n_graph ; score = product, round 4
score=$(jq -n --argjson sat "$satisfied_constraints" --argjson tot "$total_constraints" \
  --argjson k "$n_known" --argjson g "$n_graph" \
  '((($sat/$tot) * ($k/$g)) * 10000 | round) / 10000')

# missing = sorted(graph_nodes - known_proposed)
declare -A KNOWN_SET=()
for r in "${known_proposed[@]}"; do KNOWN_SET["$r"]=1; done
missing=()
while IFS= read -r gn; do
  if [[ -z "${KNOWN_SET[$gn]+x}" ]]; then missing+=("$gn"); fi
done < <(printf '%s\n' "${graph_nodes[@]}" | sort)

# Build detail string
detail="${satisfied_constraints}/${total_constraints} dependency constraints satisfied; ${n_known}/${n_graph} repos covered"
if [[ ${#missing[@]} -gt 0 ]]; then
  missing_joined=$(printf '%s, ' "${missing[@]}"); missing_joined="${missing_joined%, }"
  detail="${detail}; missing: ${missing_joined}"
fi

# Prefix based on score thresholds (jq numeric compare to avoid locale/float issues)
ge85=$(jq -n --argjson s "$score" 'if $s >= 0.85 then 1 else 0 end')
gt0=$(jq -n --argjson s "$score" 'if $s > 0.0 then 1 else 0 end')
if [[ "$ge85" -eq 1 ]]; then
  detail="Valid topological ordering; ${detail}"
elif [[ "$gt0" -eq 1 ]]; then
  detail="Partially valid ordering; ${detail}"
fi

passed=$(jq -n --argjson s "$score" 'if $s >= 0.5 then true else false end')
emit "$(pyfloat "$score")" "$passed" "$detail"
