#!/usr/bin/env bash
# test_runner.sh — Cross-repo test runner for EnterpriseBench tasks.
# Executes checkpoint verifiers and produces structured JSON output.
#
# Usage: /workspace/test.sh [checkpoint_name]
#
# Output: JSON object with per-checkpoint results and aggregate score.
# Exit code: 0 if all checkpoints pass, 1 otherwise.
set -uo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
VERIFIER_DIR="$WORKSPACE/.verifiers"
# Scoring closes $WORKSPACE to everyone but root (the grader must not be able to
# plant an artifact for a later checkpoint), so the orchestrator redirects this
# file to a scorer-writable path. The default stays put for standalone runs.
RESULTS_FILE="${EB_RESULTS_FILE:-$WORKSPACE/.results.json}"

# --- helpers ---

json_escape() {
    # Escape a string for safe JSON embedding (all RFC 7159 control chars)
    printf '%s' "$1" \
      | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g; s/\r/\\r/g; s/\x0c/\\f/g; s/\x08/\\b/g' \
      | tr '\n' ' '
}

now_ms() {
    date +%s%3N 2>/dev/null || date +%s000
}

# Read the value of KEY from the root of a verifier payload, or print nothing if
# the payload does not state one this can trust:
#
#   parse_json_root_value <key> <payload> <dup_anywhere_fails 0|1> <number|literal|string>
#
# Every field the result document is built from comes through here. A regex cannot
# see nesting, and each field this reads sits beside agent-echoed text and verifier
# diagnostics that carry the same key names, so "find the key" is never the same
# question as "find the value the verifier attested".
#
# A single LC_ALL=C awk pass walks the payload byte by byte, skipping string
# literals so a brace — or an escaped \"score\" — inside a string cannot move the
# nesting depth or forge a key. A value is read only when the payload is one closed
# root OBJECT carrying exactly one such key, at the level of the root itself:
#
#   nested-only key   {"detail": {"score": 1.0}}   a regex cannot see nesting, and
#                                                  reads the 1.0 as the verdict
#   second root       {"detail": "x"} {"score": 1}  a diagnostic object printed
#                                                  beside the verdict is not it
#   noise before it   INFO ok\n{"score": 1.0}       and neither is one printed
#                                                  ahead of it — the rule is the
#                                                  same in both directions
#   unclosed root     {"score": 1.0, "passed": tr   a verifier killed mid-print
#                                                  (OOM, SIGKILL, full disk) never
#                                                  finished saying what it meant
#
# All four are payloads no verifier that reached a verdict can emit, so each fails
# CLOSED. What that COSTS differs per key, so how much MORE to refuse is the
# callers to decide: dup_anywhere_fails=1 additionally poisons the read on a
# duplicate of the key at ANY depth. See the three wrappers below, which are the
# only callers.
#
# The value is then held to the grammar named by kind — all three anchored, so
# "1.0abc" cannot coerce to 1.0 and a quoted "true" is not the literal. Matched
# tokens are printed verbatim, never coerced through "+ 0", so a number stays a
# byte-faithful JSON number and a string keeps its escapes.
#
# What this is NOT is a full JSON validator, and it does not claim to be one: it is
# a structural scanner looking for one key. It can still credit a payload
# json.loads would reject — {"detail": "gap is 3" x 5" wide", "score": 0.3} has an
# unescaped quote in detail, yet the score key after the comma is real and is
# credited as 0.3. That is fine: the malformation is confined to a value the score
# does not depend on, and the credited number is the one the verifier actually
# wrote. (The string kind is held to more than this — see its grammar below.)
#
# What it must NOT do is read a key in a position JSON would not allow, because
# that is where a score gets forged. {"detail": "x""score": 1.0} is a scoreless
# verdict with the agent text run straight into a fake key; the two strings abut
# with no comma, which valid JSON never does. The key-position check in the walker
# (a key follows only "{" or ",") rejects it, so an agent cannot inject a score the
# verifier never attested. The line to hold is not "well-formed JSON" but "the key
# sits where a key may sit" — enough that the value read is one the verifier wrote,
# never one smuggled beside it.
#
# LC_ALL=C is load-bearing twice. mawk (Debian's default, and the task images') is
# byte-based, but gawk under a UTF-8 locale makes substr()/length()
# character-based, and one multibyte byte in a detail string would then desync the
# walk against the byte stream and void the string-skipping. It also fixes strtod's
# separator: under a comma locale "1.5" + 0 is 1, so the range check would wave a
# 1.5 through. JSON's separator is always a period.
#
# Nothing below this line may carry a literal apostrophe, comment included: the
# whole awk program is one single-quoted argument, and an apostrophe closes it.
parse_json_root_value() {
    printf '%s' "$2" | LC_ALL=C awk -v key="$1" -v dup_anywhere_fails="$3" -v kind="$4" '
    # The index of the quote closing the string opened at p, or past n if the
    # payload ends first (a trailing backslash makes p += 2 overshoot by one; both
    # callers only ask whether p > n, so the size of the overshoot never matters).
    #
    # Walks by INDEX, never accumulating the bytes: a verifier that prints a
    # megabyte of detail would make a char-by-char concat quadratic. A backslash
    # escapes whatever byte follows, so \" does not close the string, \\ leaves the
    # next quote free to, and { stays six bytes of text rather than a brace that
    # could move the depth.
    #
    # Both callers have to skip a string by the SAME rule, which is why the rule
    # lives here once: the walk relies on it to keep a brace inside a string out of
    # the depth count, and value_at relies on it to hand back the very span the
    # walk agreed was one string. Two copies left to drift apart would be a forged
    # score.
    function string_end(p,   d) {
        p++
        while (p <= n) {
            d = substr(buf, p, 1)
            if (d == "\\") { p += 2; continue }
            if (d == "\"") break
            p++
        }
        return p
    }
    # The value of the key found at depth 1, or "" if it has none this can read.
    # Both branches only have to FIND the token; the grammar in END decides
    # whether it is one. Uses buf/n as globals rather than parameters — awk copies
    # a scalar argument, and copying the whole payload per key would undo the care
    # the walk above takes to stay linear on a megabyte of detail.
    function value_at(v,   start, w) {
        while (v <= n && substr(buf, v, 1) ~ /[ \t\r\n]/) v++
        if (substr(buf, v, 1) != "\"") {
            # Flat scan to the next terminator, deliberately NOT re-walking
            # strings or containers. Anything that is not a bare token — [0.5],
            # {"a":1} — comes back garbled and the grammar throws it away, so the
            # sloppier scan can only narrow what is credited, never widen it.
            #
            # The quote is deliberately NOT in the stop set, and it is
            # load-bearing in the opposite direction from how it reads.
            # {"score":1.0"passed":true} is a verifier that dropped a comma;
            # scanning THROUGH the quote yields 1.0"passed":true, which the number
            # grammar refuses, and the malformed payload fails closed. Add the
            # quote here and the token truncates to a clean 1.0 — the same payload
            # is then awarded FULL MARKS. Tightening the scan loosens the verdict,
            # so it stays loose on purpose; NO_READABLE_SCORE["glued_token"] pins it.
            start = v
            while (v <= n && substr(buf, v, 1) !~ /[]},\t\r\n ]/) v++
            return substr(buf, start, v - start)
        }
        # A quote-led value is taken as a quoted span under the same escape rule
        # the walk uses to skip strings, keeping its own quotes. That is what makes
        # detail readable at all — the flat scan stops at the first comma or space,
        # which most real detail strings contain. It is provably neutral for the
        # number and literal grammars: both are anchored, and a token that still
        # carries its quotes matches neither.
        w = string_end(v)
        if (w > n) return ""   # unterminated; the root cannot have closed either
        return substr(buf, v, w - v + 1)
    }
    { buf = buf $0 "\n" }
    END {
        n = length(buf)
        klen = length(key)
        depth = 0; rootClosed = 0; ok = 1
        totalCount = 0; rootCount = 0; keyVal = ""
        prevSig = ""
        i = 1
        while (i <= n) {
            c = substr(buf, i, 1)
            # One root, and nothing beside it: at the payload own level only
            # whitespace may sit next to the root value. A byte after it (a
            # second object, a stray diagnostic) and a byte before it (a log
            # line, the tail of a partial print) are the same class — a payload
            # no verifier that reached a verdict emits — so both fail closed
            # rather than crediting whichever score token they happen to carry.
            # This is also what keeps depth from going negative on a stray
            # closer: at the root level a "}" is not an opener, so it stops here.
            #
            # The root must be an OBJECT. Allowing "[" bought nothing: valid JSON
            # cannot put a bare key directly in an array, so a real score key can
            # never sit at depth 1 of one. It did admit ["score": 1.0] — not JSON
            # at all — which this function scored 1.0 standalone. run_verifier
            # never handed it that payload (the ^{ shape gate above rejects a
            # non-object root first), so this was defense in depth, not a live
            # hole. It is still the right rule: the two gates now agree instead of
            # one relying on the other to have already run.
            if (depth == 0 && c !~ /[ \t\r\n]/ &&
                (rootClosed || c != "{")) { ok = 0; break }

            if (c == "\"") {
                # Skipping the string is what keeps a brace inside it — or an
                # escaped \"score\" — from moving the depth or forging a key.
                j = string_end(i)
                # A string is a key iff the next non-space byte is a colon. It is
                # THE key iff its bytes are exactly the one asked for — which an
                # escape can only lengthen, so the span check alone rejects every
                # forgery.
                k = j + 1
                while (k <= n && substr(buf, k, 1) ~ /[ \t\r\n]/) k++
                isKey = (k <= n && substr(buf, k, 1) == ":")

                # A key may sit only where JSON puts one: first thing after "{"
                # or after a ",". Any other predecessor means two values abut
                # with no separator, which no JSON emitter produces. Without this
                # the walker credits {"detail": "x""score": 1.0} — a scoreless
                # verdict a verifier that failed to escape agent text into detail
                # would print, letting the agent inject a "score" the verifier
                # never wrote. json.loads refuses those bytes; so does this now.
                # (A comma-separated ,"score":1.0 the agent smuggles in IS valid
                # JSON that every parser reads as a score — a verifier escaping
                # bug, not one this layer can or should second-guess.)
                if (isKey && prevSig != "{" && prevSig != ",") { ok = 0; break }

                if (isKey && j - i == klen + 1 && substr(buf, i + 1, klen) == key) {
                    totalCount++
                    if (depth == 1) {
                        rootCount++
                        if (rootCount == 1) keyVal = value_at(k + 1)
                    }
                }
                prevSig = "\""
                i = j + 1; continue
            }

            if (c == "{" || c == "[") { depth++; prevSig = c; i++; continue }
            if (c == "}" || c == "]") { depth--; if (depth == 0) rootClosed = 1; prevSig = c; i++; continue }
            # Track the last significant byte for the key-position check above;
            # whitespace is not significant, so "," then a newline then a key
            # still reads the key as legally placed.
            if (c !~ /[ \t\r\n]/) prevSig = c
            i++
        }
        # The key must sit at the level of the root itself, exactly once. A payload
        # carrying two of them at depth 1 has not said it once, and one that has
        # the key only inside a nested object has not said it at all.
        if (!(ok && rootClosed && rootCount == 1)) exit
        # See the wrappers for why only score can afford this.
        if (dup_anywhere_fails == 1 && totalCount != 1) exit

        if (kind == "number") {
            # Range-checked to [0, 1], which also catches 1e400 (strtod takes it to
            # +inf). The exponent branch is not decoration: json.dumps emits 1e-05
            # for any small float. The 1e-6 tolerance mirrors _SCORE_EPSILON in
            # eb_verify.scorer_guard, so the shell and the guard admit exactly the
            # same values.
            if (keyVal ~ /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$/) {
                x = keyVal + 0
                if (x >= -1e-6 && x <= 1 + 1e-6) print keyVal
            }
        } else if (kind == "literal") {
            if (keyVal ~ /^(true|false)$/) print keyVal
        } else if (kind == "string") {
            # The JSON string grammar, and this is the one place the walker owes
            # more than "find the token": a string is printed back into the result
            # document verbatim, so a token that is not a legal JSON string takes
            # the whole document down with it — the same corruption as emitting two
            # tokens, just from a different cause. Raw control bytes are what a
            # verifier building detail by hand actually produces, and json.loads
            # rejects them inside a string (strict=True). Callers default an
            # unreadable string to "", which costs a debugging aid, not a grade.
            if (keyVal ~ /^"([^"\\[:cntrl:]]|\\(["\\\/bfnrt]|u[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]))*"$/) print keyVal
        }
    }'
}

# The three keys the result document is built from, and the only callers of the
# walker. Each fixes its key, its grammar, and how much duplication it refuses.
#
# Only score refuses a duplicate anywhere, because only score can afford to: an
# unreadable score routes to the infra chain, which is a re-run — a safe escape.
# The other two have no such escape. An unreadable passed becomes a recorded false,
# indistinguishable from a real agent failure by the time it reaches
# checkpoints_passed / all_passed and the pass-rate reporting; an unreadable detail
# only loses a debugging aid, which is what lets it fail closed to "" at all.
#
# Depth-1 scoping, not duplicate-refusal, is what closes the bug behind this: a
# diagnostics.passed flipping a 0.0 checkpoint, which is the ONLY passed a
# depth-blind read finds on the 22 corpus verifiers that state no top-level one
# (scorer_guard.py:335). Refusing duplicates on top of it would just point the same
# fabrication the other way, recording passed=false at score=1.0 for the legitimate
# {"score":1.0,"passed":true,"diagnostics":{"passed":true}}.
#
# detail keeps its quotes: it is the canonical string of verifier_output.schema.json
# and is embedded verbatim into the result document below.
parse_score()  { parse_json_root_value score  "$1" 1 number; }
parse_passed() { parse_json_root_value passed "$1" 0 literal; }
parse_detail() { parse_json_root_value detail "$1" 0 string; }

# Discover repos in /workspace/ (directories with .git)
discover_repos() {
    local repos=()
    for dir in "$WORKSPACE"/*/; do
        [ -d "$dir/.git" ] && repos+=("$(basename "$dir")")
    done
    printf '%s\n' "${repos[@]}"
}

# Declares a verifier-harness failure rather than an agent failure. Must stay in
# lockstep with eb_verify.scorer_guard.INFRA_SENTINEL, which greps for it.
INFRA_SENTINEL="VERIFIER_INFRA_ERROR"

# Sourced (via BASH_ENV) by every verifier shell. Bash calls
# command_not_found_handle for any command it cannot find in ANY context —
# including inside `if python3 ... 2>/dev/null`, where the verifier's own
# control flow would otherwise swallow the failure. EB_INFRA_LOG is a side
# channel the verifier cannot redirect away. Returning 127 reproduces bash's
# native exit status for an unfindable command, so `set -e` and every exit-code
# test behave as they would without the handler.
INFRA_PREAMBLE=$(mktemp)
INFRA_LOG=$(mktemp)
trap 'rm -f "$INFRA_PREAMBLE" "$INFRA_LOG"' EXIT
cat >"$INFRA_PREAMBLE" <<'PREAMBLE'
command_not_found_handle() {
    printf '%s\n' "$1" >>"${EB_INFRA_LOG:-/dev/null}"
    printf '%s: command not found\n' "$1" >&2
    return 127
}
PREAMBLE

# Run a single verifier, capture JSON output and exit code.
# Returns: sets VERIFIER_EXIT, VERIFIER_JSON, VERIFIER_DURATION_MS, VERIFIER_RAN,
# VERIFIER_SCORE
#
# Never fabricates a score — not from an exit code, and not from output it could
# not parse. Absence of a verdict is not a verdict of 0.0, nor of 1.0. A verdict
# requires a score the schema would accept, so printing a JSON object is not on
# its own enough to have reached one. VERIFIER_RAN attests whether the verifier
# did; the scorer refuses to score any checkpoint that did not.
run_verifier() {
    local verifier_path="$1"
    local timeout_sec="${2:-120}"
    local start end raw_stdout raw_stderr raw_stdout_file

    start=$(now_ms)
    raw_stderr=$(mktemp)
    raw_stdout_file=$(mktemp)
    : >"$INFRA_LOG"

    # Pass the workspace explicitly as $1: several checks resolve
    # WORKSPACE="${1:-.}", which shadows the exported env var. Scoring runs from
    # a cwd outside the workspace (the agent must not control the checks'
    # sys.path[0]), so the "." fallback would silently zero every such check.
    if command -v timeout >/dev/null 2>&1; then
        BASH_ENV="$INFRA_PREAMBLE" EB_INFRA_LOG="$INFRA_LOG" \
            timeout "$timeout_sec" bash "$verifier_path" "$WORKSPACE" >"$raw_stdout_file" 2>"$raw_stderr"
        VERIFIER_EXIT=$?
    else
        BASH_ENV="$INFRA_PREAMBLE" EB_INFRA_LOG="$INFRA_LOG" \
            bash "$verifier_path" "$WORKSPACE" >"$raw_stdout_file" 2>"$raw_stderr"
        VERIFIER_EXIT=$?
    fi

    raw_stdout=$(cat "$raw_stdout_file")
    rm -f "$raw_stdout_file"

    # awk, not `sort -u`: awk is already a hard dependency of this script (the
    # weighted-score math below), and a missing-command detector must not itself
    # depend on a command the image may lack.
    local missing_cmds=""
    [ -s "$INFRA_LOG" ] && missing_cmds=$(awk '!seen[$0]++ { printf "%s%s", (c++ ? " " : ""), $0 }' "$INFRA_LOG")

    end=$(now_ms)
    VERIFIER_DURATION_MS=$(( end - start ))
    VERIFIER_RAN="true"

    # Default for the branches below that return early; every JSON they fabricate
    # carries a literal 0.0. The good path overwrites it with the verifier's score.
    VERIFIER_SCORE="0.0"

    local verdict_score
    verdict_score=$(parse_score "$raw_stdout")

    local infra_detail=""
    if [ -n "$missing_cmds" ]; then
        # Ahead of the timeout check: a command that does not exist is an infra
        # failure whether or not the verifier also ran out the clock. Fires even
        # when the verifier swallowed the failure and went on to print a
        # legitimate-looking score.
        infra_detail="$INFRA_SENTINEL: verifier shelled a command that does not exist: $missing_cmds"
    elif [ "$VERIFIER_EXIT" -eq 124 ]; then
        # A timeout means the verifier ran but did not finish, so it stays a
        # scored 0.0: a hang is as often the subject code's fault as the harness's.
        VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"detail\": \"Timed out after ${timeout_sec}s\"}"
        rm -f "$raw_stderr"
        return
    elif [ "$VERIFIER_EXIT" -eq 127 ]; then
        # A not-found command raised by something other than the handler, e.g. a
        # non-bash interpreter in the chain.
        infra_detail="$INFRA_SENTINEL: verifier exited 127 (command not found)"
    elif ! printf '%s' "$raw_stdout" | grep -q '^{'; then
        local stderr_content
        stderr_content=$(cat "$raw_stderr" 2>/dev/null || true)

        if [ -n "$AGENT_OUTPUT_INVALID" ] && [ "$VERIFIER_EXIT" -ne 0 ]; then
            # The verifier RAN; the agent's own malformed artifact killed it
            # mid-flight (see AGENT_OUTPUT_INVALID). A bad answer is the agent's
            # failure, so it is scored 0.0 — routing it to the infra re-run
            # channel would let an agent escape a deserved 0.0 by emitting
            # garbage. Requires a nonzero exit: a verifier that exits 0 without
            # printing anything is broken irrespective of the answer.
            VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"detail\": \"$(json_escape "$AGENT_OUTPUT_INVALID (exit $VERIFIER_EXIT): ${stderr_content:-no output}")\"}"
            rm -f "$raw_stderr"
            return
        fi

        # No JSON, and nothing about the agent's answer explains it: the verifier
        # died before reaching its verdict.
        infra_detail="$INFRA_SENTINEL: verifier produced no JSON verdict (exit $VERIFIER_EXIT): ${stderr_content:-no output}"
    elif [ -z "$verdict_score" ]; then
        if [ -n "$AGENT_OUTPUT_INVALID" ] && [ "$VERIFIER_EXIT" -ne 0 ]; then
            # Same attribution as the no-JSON branch above: the agent's own
            # malformed artifact killed the verifier, and a leaked JSON skeleton
            # on the way down ({"score": null}) is no more a verdict than no
            # output at all. Scored 0.0, not an infra re-run — otherwise the two
            # structurally identical failures (garbage answer, verifier dead
            # without a score) would score oppositely on whether a skeleton
            # happened to print, and an agent could buy a re-run by emitting one.
            VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"detail\": \"$(json_escape "$AGENT_OUTPUT_INVALID (exit $VERIFIER_EXIT): ${raw_stdout:0:200}")\"}"
            rm -f "$raw_stderr"
            return
        fi

        # JSON, but no score the schema would accept, and nothing about the
        # agent's answer explains it. This belongs in the chain that decides
        # whether a verdict was REACHED: an object whose score is a string has
        # judged nothing, however well-formed the braces around it.
        infra_detail="$INFRA_SENTINEL: verifier emitted no score in [0, 1] (exit $VERIFIER_EXIT): ${raw_stdout:0:200}"
    fi

    if [ -n "$infra_detail" ]; then
        VERIFIER_RAN="false"
        VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"verifier_ran\": false, \"detail\": \"$(json_escape "$infra_detail")\"}"
    else
        VERIFIER_JSON="$raw_stdout"
        VERIFIER_SCORE="$verdict_score"
    fi

    rm -f "$raw_stderr"
}

# --- is the agent's own answer artifact structurally unusable? ----------------
#
# A check script that dies parsing a malformed answer.json produced no verdict,
# but it did not fail to RUN: it ran, and the agent's output killed it. Without
# this distinction the runner reads every garbage answer as "the verifier never
# ran" and routes a deserved 0.0 into the infra re-run channel — an agent could
# evade its score by emitting garbage. 104 of the 132 corpus check scripts that
# read answer.json abort under `set -e` on an answer that is not a JSON object,
# so this is the common case, not an edge one.
#
# Structural only, and deliberately narrow: an answer that IS a JSON object never
# trips this, however wrong its contents — wrongness is what the checks are for.
# Unparseable, and parseable-but-not-an-object, are the only two shapes the corpus
# cannot survive (measured: {}, wrong value types, and hostile nesting all still
# reach a verdict).
#
# Ahead of the mode split: run_verifier reads this, so under `set -u` every mode
# must cross the assignment, and both modes owe the agent the same attribution.
AGENT_OUTPUT_INVALID=""
AGENT_ANSWER="$WORKSPACE/agent_output/answer.json"
if [ -f "$AGENT_ANSWER" ] && command -v python3 >/dev/null 2>&1; then
    if ! python3 -c "
import json, sys
with open(sys.argv[1]) as fh:
    sys.exit(0 if isinstance(json.load(fh), dict) else 1)
" "$AGENT_ANSWER" >/dev/null 2>&1; then
        AGENT_OUTPUT_INVALID="agent_output/answer.json is not a JSON object, so no check could read it"
    fi
fi

# --- single checkpoint mode ---

if [ -n "${1:-}" ]; then
    CHECKPOINT="$1"
    # Validate checkpoint name (alphanumeric, hyphens, underscores only)
    if [[ "$CHECKPOINT" =~ [^a-zA-Z0-9_-] ]]; then
        printf '{"score": 0.0, "passed": false, "detail": "Invalid checkpoint name"}\n'
        exit 1
    fi
    VERIFIER="$VERIFIER_DIR/${CHECKPOINT}.sh"
    if [ ! -f "$VERIFIER" ]; then
        printf '{"score": 0.0, "passed": false, "detail": "No verifier found for checkpoint: %s"}\n' "$CHECKPOINT"
        exit 1
    fi
    run_verifier "$VERIFIER" 120
    printf '%s\n' "$VERIFIER_JSON"
    exit "$VERIFIER_EXIT"
fi

# --- full run: all checkpoints ---

echo "=== EnterpriseBench Cross-Repo Test Runner ===" >&2
echo "Workspace: $WORKSPACE" >&2

# List repos for diagnostics (stderr so it doesn't pollute JSON stdout)
mapfile -t REPOS < <(discover_repos)
echo "Repos:" >&2
for repo in "${REPOS[@]}"; do
    echo "  - $repo" >&2
done
echo "" >&2

# Verify cross-repo access: confirm we can cd into each repo
for repo in "${REPOS[@]}"; do
    if ! (cd "$WORKSPACE/$repo" && pwd >/dev/null); then
        printf '{"task_score": 0.0, "all_passed": false, "error": "Cannot access repo: %s"}\n' "$repo"
        exit 1
    fi
done

# Run all verifiers
if [ ! -d "$VERIFIER_DIR" ]; then
    printf '{"task_score": 0.0, "all_passed": false, "checkpoints": [], "error": "No .verifiers/ directory found"}\n'
    exit 1
fi

# --- preflight: an interpreter the check scripts need must actually exist -----
#
# command_not_found_handle only fires when BASH performs the PATH lookup itself,
# so it is blind to `env python3` and absolute-path invocations. No in-bash
# signal can see those, so detection alone cannot close them — but the
# precondition can: if a check script needs an interpreter the image lacks, no
# verifier depending on it can reach a verdict, whatever syntax it uses. Refuse
# to score the task at all rather than emit numbers that only look like
# measurements.
#
# Gated on the interpreter actually being referenced, so a task whose checks are
# pure bash is never failed for lacking one it does not use.
REQUIRED_INTERPRETERS="python3"
MISSING_INTERPRETERS=""
for interp in $REQUIRED_INTERPRETERS; do
    # `command -v` (a builtin) first: it short-circuits the grep, which forks and
    # reads every check script, in the common case where the interpreter is present.
    if ! command -v "$interp" >/dev/null 2>&1 \
       && grep -qF -- "$interp" "$VERIFIER_DIR"/*.sh 2>/dev/null; then
        MISSING_INTERPRETERS="$MISSING_INTERPRETERS $interp"
    fi
done
if [ -n "$MISSING_INTERPRETERS" ]; then
    printf '{"task_score": 0.0, "all_passed": false, "checkpoints": [], "error": "%s: check scripts require interpreter(s) not installed in this image:%s"}\n' \
        "$INFRA_SENTINEL" "$(json_escape "$MISSING_INTERPRETERS")"
    exit 1
fi

TOTAL=0
PASSED=0
CHECKPOINT_RESULTS=""
WEIGHTED_SCORE="0"

for verifier in "$VERIFIER_DIR"/*.sh; do
    [ -f "$verifier" ] || continue
    name=$(basename "$verifier" .sh)
    TOTAL=$((TOTAL + 1))

    # Read weight from companion .meta file if present, else default 1.0.
    # Validate it is a plain number: it is interpolated unquoted into both the
    # result JSON and an awk expression below, so a malformed weight is an
    # injection point. .verifiers/ is sealed root-only, but this check is the
    # layer that does not depend on the seal holding.
    weight="1.0"
    meta_file="$VERIFIER_DIR/${name}.meta"
    if [ -f "$meta_file" ]; then
        w=$(grep -oP '(?<=weight=)\S+' "$meta_file" 2>/dev/null || true)
        [[ "$w" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && weight="$w"
    fi

    # Read timeout from .meta file if present
    checkpoint_timeout=120
    if [ -f "$meta_file" ]; then
        t=$(grep -oP '(?<=timeout=)\S+' "$meta_file" 2>/dev/null || true)
        [ -n "$t" ] && checkpoint_timeout="$t"
    fi

    echo "--- Checkpoint: $name (weight=$weight, timeout=${checkpoint_timeout}s) ---" >&2
    run_verifier "$verifier" "$checkpoint_timeout"

    # run_verifier BRANCHED on the score (the infra chain reads it to decide whether
    # the verifier reached a verdict at all), so the document has to carry the very
    # value that decision was made from — hence the variable, not a re-read of
    # VERIFIER_JSON. passed and detail decided nothing upstream, so reading them off
    # the payload here is not a second opinion on a settled question.
    checkpoint_score="$VERIFIER_SCORE"

    # Defaults are the shell's, not the walker's: it prints nothing when the payload
    # states no root value it can trust.
    checkpoint_passed=$(parse_passed "$VERIFIER_JSON")
    [ -n "$checkpoint_passed" ] || checkpoint_passed="false"

    checkpoint_detail=$(parse_detail "$VERIFIER_JSON")
    [ -n "$checkpoint_detail" ] || checkpoint_detail='""'

    if [ "$VERIFIER_RAN" != "true" ]; then
        echo "  INFRA ERROR (verifier never reached a verdict — not scored)" >&2
    elif [ "$checkpoint_passed" = "true" ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS (score=$checkpoint_score)" >&2
    else
        echo "  FAIL (score=$checkpoint_score)" >&2
    fi

    # awk for float math; LC_ALL=C for the same mawk locale reason as parse_score
    # (a comma locale prints "0,5000" and task_score stops being a JSON number).
    WEIGHTED_SCORE=$(LC_ALL=C awk "BEGIN { printf \"%.4f\", $WEIGHTED_SCORE + ($checkpoint_score * $weight) }")

    # Build checkpoint result JSON entry (detail is already a quoted JSON
    # string). name comes from an agent-writable filename in .verifiers/ —
    # escape it the same way repo basenames are escaped above.
    #
    # verifier_ran is the attestation the scorer gates on. It is emitted here
    # because this is the only component that can observe whether the verifier
    # reached a verdict.
    entry=$(printf '{"name": "%s", "weight": %s, "score": %s, "passed": %s, "verifier_ran": %s, "detail": %s, "duration_ms": %d, "exit_code": %d}' \
        "$(json_escape "$name")" "$weight" "$checkpoint_score" "$checkpoint_passed" "$VERIFIER_RAN" "$checkpoint_detail" "$VERIFIER_DURATION_MS" "$VERIFIER_EXIT")

    if [ -z "$CHECKPOINT_RESULTS" ]; then
        CHECKPOINT_RESULTS="$entry"
    else
        CHECKPOINT_RESULTS="$CHECKPOINT_RESULTS, $entry"
    fi
    echo "" >&2
done

echo "Results: $PASSED/$TOTAL checkpoints passed" >&2

ALL_PASSED="false"
EXIT_CODE=1
if [ "$PASSED" -eq "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
    ALL_PASSED="true"
    EXIT_CODE=0
fi

# Render the repos array as a JSON list. Must expand the whole array —
# `echo "$REPOS"` would emit only ${REPOS[0]} and silently drop every other
# repo from the output. The ${arr[@]+...} guard keeps this safe under set -u
# when no repos were discovered. Repo names come from discover_repos(), which
# globs agent-owned /workspace/*/ — escape each one via json_escape() before
# embedding, the same way verifier detail strings already are, so an agent
# cannot break out of the JSON array via a crafted directory name.
REPOS_JSON=$(
    for repo in ${REPOS[@]+"${REPOS[@]}"}; do
        json_escape "$repo"
        printf '\n'
    done | awk 'NF{printf "%s\"%s\"", (c++?", ":""), $0}'
)

# Build the result JSON once, then emit to both stdout and the results file.
RESULT_JSON=$(cat <<RESULT_JSON
{
  "task_score": $WEIGHTED_SCORE,
  "all_passed": $ALL_PASSED,
  "checkpoints_passed": $PASSED,
  "checkpoints_total": $TOTAL,
  "repos": [$REPOS_JSON],
  "checkpoints": [$CHECKPOINT_RESULTS]
}
RESULT_JSON
)

printf '%s\n' "$RESULT_JSON"
printf '%s\n' "$RESULT_JSON" > "$RESULTS_FILE"

exit $EXIT_CODE
