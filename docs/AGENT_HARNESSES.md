# Codex and OpenCode benchmark harnesses

EnterpriseBench can run single-session tasks with three coding-agent CLIs:
Claude Code, Codex, and OpenCode. Codex and OpenCode support baseline,
Sourcegraph `mcp_only`, forced Code Finder (`mcp_code_finder`), assisted Code
Finder (`mcp_assisted`), and Sourcegraph `sgx` CLI runs. The hybrid arm remains
Claude-specific. Selecting an unsupported mode fails before a task is built or
billed.

The generated harnesses use pinned CLI packages:

- Codex: `@openai/codex@0.145.0`
- OpenCode: `opencode-ai@1.18.4`

If a task image does not already contain the selected CLI, the runner installs
that exact package in the task container and verifies the binary before starting
the agent. Result metadata records `harness`, `model`, and the effective command.
JSON event logs remain under `agent/`; Codex `turn.completed` events and OpenCode
`step_finish` events feed the shared token/cost fields. These events are not the
same behavioral unit: one Codex turn can contain many completed work items,
while OpenCode emits a step after each model/tool loop.

## Codex

Authenticate the host CLI first:

```bash
codex login
codex login status
```

The runner uses the login stored at `${CODEX_HOME:-$HOME/.codex}/auth.json`.
The file is copied into the task container as mode `0600`, removed immediately
after Codex exits, and never written to run artifacts. If removal fails, the
runner will not retain the container even when `--keep-container` was requested.

Run one task with the current Codex flagship:

```bash
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/calibration-001/task.toml \
  --harness codex \
  --model gpt-5.6-sol \
  --mode baseline
```

Codex runs non-interactively with JSON events, ephemeral session state, ignored
user configuration, and its internal sandbox disabled because the CLI is already
inside EnterpriseBench's Docker boundary. The selected account must have access
to the requested model; the harness does not silently substitute another one.

To run the same task with Sourcegraph MCP:

```bash
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/calibration-001/task.toml \
  --harness codex \
  --model gpt-5.6-sol \
  --mode mcp_only
```

The MCP arm creates an isolated `/home/agent/.codex/config.toml` inside the
container. It contains the Sourcegraph endpoint and the name of
`SOURCEGRAPH_ACCESS_TOKEN`, never the token value. The token is forwarded only
through the subprocess environment. Sourcegraph is marked as required, so Codex
fails the run if the server cannot initialize, and the benchmark invalidates a
completed MCP-only run that records zero Sourcegraph calls. Completed Codex
`mcp_tool_call` events feed both the MCP call gate and the normalized trace.

## Code Finder arms and telemetry

The two Code Finder arms deny local repository reads at the filesystem:

- `mcp_code_finder` requires exactly one `code_finder` call per task repository
  and rejects any direct Sourcegraph retrieval call. Each Finder task must name
  exactly one expected `github.com/sg-evals/<mirror>` repository; duplicate,
  unscoped, or cross-repository calls invalidate the run even when the total
  call count matches.
- `mcp_assisted` requires a `code_finder` bootstrap and permits targeted direct
  Sourcegraph follow-up.

Both arms route the harness's MCP traffic through a root-owned proxy bound to
`127.0.0.1` inside the task container. The proxy forwards authorization but
never records headers. It writes `mcp_trace.jsonl` as mode `0600`, capturing
tool call arguments and the raw `_meta.sourcegraphToolTelemetry` returned by
Sourcegraph. `results.json` keeps provider-reported outer usage, Finder inner
turn/tool/token aggregates, and a combined token total separate. Combined
dollar cost remains null because Sourcegraph does not report Finder subagent
cost in MCP metadata. Retrieval provenance also records the MCP protocol and
server identity, trace start and finish timestamps, the sorted tool names, a
canonical SHA-256 hash of the advertised tool inventory, and a separate
canonical SHA-256 hash of the Code Finder input/output schema. The root-cause
console displays these fields with the per-repository scope verdict so results
can be compared against the exact server contract that produced them.

```bash
# Forced Finder
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/calibration-001/task.toml \
  --harness codex \
  --model gpt-5.6-sol \
  --mode mcp_code_finder

# Finder-assisted OpenCode
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/calibration-001/task.toml \
  --harness opencode \
  --model openrouter/moonshotai/kimi-k3 \
  --mode mcp_assisted
```

## OpenCode with open-weight models

The initial provider is OpenRouter because it exposes several useful
open-weight coding models through one credential and one model-ID namespace.
Set the key in the environment or `.env.local`:

```bash
export OPENROUTER_API_KEY=...
```

Refresh OpenCode's provider catalog when checking model availability:

```bash
npm exec --yes --package=opencode-ai@1.18.4 -- \
  opencode models openrouter --refresh
```

Start with this matrix:

| Role | OpenCode model ID | Why include it |
|---|---|---|
| Capability ceiling | `openrouter/deepseek/deepseek-v4-pro` | Long-context, high-capability coding and agentic tool use |
| Cost/latency control | `openrouter/deepseek/deepseek-v4-flash` | Same family, much cheaper and faster |
| Different model family | `openrouter/qwen/qwen3.6-27b` | Apache-licensed dense model sized for practical self-hosting |
| Stable open baseline | `openrouter/openai/gpt-oss-120b` | Widely available open-weight reasoning and tool-use baseline |

For example:

```bash
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/ \
  --all \
  --session-type single \
  --limit 10 \
  --parallel 2 \
  --harness opencode \
  --model openrouter/deepseek/deepseek-v4-pro \
  --mode baseline
```

Repeat the same locked task set for each model. Do not mix task filters, task
revisions, timeouts, or concurrency policies within a comparison.

OpenCode receives the instruction through stdin and runs with `--pure`,
`--format json`, and `--auto`. `--pure` excludes external plugins; `--auto` is
required for unattended file and shell actions inside the Docker sandbox.

Run the same model and task through Sourcegraph MCP by changing only the mode:

```bash
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/calibration-001/task.toml \
  --harness opencode \
  --model openrouter/moonshotai/kimi-k3 \
  --mode mcp_only
```

The runner writes a private, container-local
`/home/agent/.config/opencode/opencode.jsonc`. Its authorization header uses
OpenCode's `{env:SOURCEGRAPH_ACCESS_TOKEN}` interpolation, so the persisted
configuration contains only the environment-variable name. The Sourcegraph
server is enabled with OAuth discovery disabled, and a completed OpenCode tool
named `sourcegraph_*` counts toward the MCP-only validity gate.

## Sourcegraph CLI arm

The `cli` arm keeps local source available and adds the plain `sgx` shell
command. It is therefore a local-plus-remote retrieval arm, distinct from the
no-local-source `mcp_only` ablation. The runner installs `sgx`, validates its
Sourcegraph token before the model call, and invalidates a completed run that
records zero real `sgx` invocations.

```bash
# Codex
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/calibration-001/task.toml \
  --harness codex \
  --model gpt-5.6-sol \
  --mode cli

# OpenCode
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/calibration-001/task.toml \
  --harness opencode \
  --model openrouter/moonshotai/kimi-k3 \
  --mode cli
```

Codex `command_execution` and OpenCode `bash` events feed the same `sgx` usage
gate as Claude Bash events. Mentions in messages, file paths, and quoted text do
not count.

## Tier-2 judge routing

The agent harness and the LLM judge are configured independently. A Codex or
OpenCode run can use an account-specific Claude Code judge without giving that
Claude credential to the agent container:

```bash
python3 scripts/run_benchmark.py \
  benchmarks/customer_escalation/ \
  --harness opencode \
  --model openrouter/moonshotai/kimi-k3 \
  --judge-model cc:haiku \
  --judge-account 3
```

`--judge-account 3` resolves the host wrapper `claude-3`; omitting it retains
the generic `claude` profile. Result scores persist the judge backend, provider,
model, account, executable, and CLI version. A judge failure remains a
`verifier_infra_error` and does not erase the agent artifact or retrieval
telemetry.

OpenCode results also include `opencode_lifecycle`: event timestamps, started
and finished step counts, unfinished-step status, completed artifact writes,
and the configured graded artifact path and write timestamp. The runner derives
that path from checkpoint scripts, so bespoke Markdown and JSON deliverables are
tracked instead of assuming every task grades `agent_output/answer.json`. This
separates MCP latency, model synthesis latency, artifact completion, and a CLI
that continues reasoning after producing the graded deliverable.

## Result isolation

Generated harnesses derive a lowercase variant label from harness and model, so
they cannot overwrite Claude or one another:

```text
results/runs/<task>/baseline--codex-gpt-5-6-sol-2507d95681/
results/runs/<task>/baseline--opencode-openrouter-deepseek-deepseek-v4-pro-600d7d9311/
```

The suffix is a hash of the unsanitized harness/model identity, preventing
distinct provider paths from collapsing to the same directory after path-safe
normalization. An explicit `--variant-label` overrides the derived label. Use
that only when a separate experiment identity is intentional.

Include these isolated arms in score analysis with:

```bash
python3 scripts/analyze_scores.py --include-variant-labeled
```

The regular `by_mode` and MCP headline fields remain limited to unlabeled runs.
Each Codex/OpenCode arm is reported independently under `by_variant`; variants
are never averaged into the Claude baseline.

## No-spend readiness gate

Before consuming a Claude, Codex, or OpenRouter run:

1. Run the harness, output-contract, telemetry, and judge-routing tests.
2. Run `python3 scripts/validate_tasks_preflight.py --json`; every selected task
   must be ready.
3. Check the selected agent and judge accounts with
   `scripts/accounts/account-health --no-probe`.
4. Exercise each selected harness/mode cell with `run_task.py --dry-run
   --no-build`.

Dry runs build the real instruction, start the task container, validate cloned
repositories, install the requested harness, and preflight Sourcegraph MCP/CLI
access. They do not invoke an agent or judge model. Repository-health, MCP
handshake, missing graded-artifact contracts, and incomplete Tier-2 ground truth
all fail closed as invalid infrastructure runs with no numeric task reward.

## Comparison semantics

Use task score, checkpoint score, and elapsed wall time for direct cross-harness
comparisons. Token counts and cost are provider-reported: accounting can differ,
and Codex subscription runs do not report a dollar cost.

`tool_usage.num_turns` remains for compatibility with historical results, but it
contains a provider-native count. Read `tool_usage.provider_activity` before
interpreting it:

- Codex reports `primary_unit = "turn"` plus completed work-item, command,
  message, and file-change counts.
- OpenCode reports `primary_unit = "step"` plus tool-use and message counts.
- Claude reports its native turn count.

Do not calculate cross-harness efficiency per “turn.” The root-cause console
labels native units and exposes the underlying trace structure instead.

## Current limitations

- Codex and OpenCode support `session_type = "single"` only. Chain and event
  replay tasks fail at dispatch instead of running without an agent.
- Codex supports Sourcegraph `mcp_only`, both Code Finder arms, and `cli`, but
  not `hybrid`.
- Codex subscription runs report token usage but no synthetic dollar cost.
- OpenCode supports baseline, Sourcegraph `mcp_only`, both Code Finder arms, and
  Sourcegraph `cli` runs for `openrouter/...` model IDs.
- A dry run validates routing and configuration but does not spend a model call
  or prove model-provider credentials; use one calibration task as the live
  smoke test.

References:

- [Codex non-interactive CLI](https://developers.openai.com/codex/noninteractive)
- [Codex MCP configuration](https://developers.openai.com/codex/mcp)
- [Sourcegraph MCP](https://sourcegraph.com/docs/api/mcp)
- [Sourcegraph OpenCode integration](https://sourcegraph.com/docs/api/mcp/client-integrations#opencode)
- [OpenCode MCP servers](https://opencode.ai/docs/mcp-servers/)
- [OpenCode `run` command](https://opencode.ai/docs/cli/#run)
- [OpenCode providers](https://opencode.ai/docs/providers/#openrouter)
- [OpenRouter programming models](https://openrouter.ai/collections/programming)
