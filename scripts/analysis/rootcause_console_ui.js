const CELLS = JSON.parse(document.getElementById("data").textContent);
const COLS = [
  ["task", "task"],
  ["harness", "harness"],
  ["model", "model"],
  ["mode", "arm"],
  ["suite", "suite"],
  ["score", "score"],
  ["cost", "$"],
  ["in_tok", "in_tok"],
  ["out_tok", "out_tok"],
  ["activity_sort", "native activity"],
  ["agent_s", "lat_s"],
  ["flags", "flags"],
];

const esc = (value) =>
  (value == null ? "" : String(value)).replace(
    /[&<>]/g,
    (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[char],
  );
const scoreClass = (value) =>
  value == null ? "" : value >= 0.6 ? "sc-hi" : value >= 0.3 ? "sc-mid" : "sc-lo";
const safeStatus = (status) =>
  ["ok", "error", "denied", "missing", "pending"].includes(status)
    ? status
    : "pending";
const safeClass = (value) => String(value || "").replace(/[^a-zA-Z0-9_-]/g, "-");
const providerLabel = (cell) =>
  cell.harness || cell.activity?.provider || "claude";
const modeGateLabel = (cell) =>
  cell.mode === "cli"
    ? "ungated (local source + sgx)"
    : cell.mode === "baseline" || cell.mode === "hybrid"
      ? "ungated (local source readable)"
      : "gated (remote retrieval required)";
const activityLabel = (cell) => {
  if (cell.activity?.label) return cell.activity.label;
  const count = Number(cell.turns || 0);
  return `${count} ${providerLabel(cell) === "opencode" ? "OpenCode step" : `${providerLabel(cell)} turn`}${count === 1 ? "" : "s"}`;
};

CELLS.forEach((cell) => {
  cell.harness = providerLabel(cell);
  cell.model = cell.model || "unknown";
  cell.flags = Array.isArray(cell.flags) ? cell.flags : [];
  cell.calls = Array.isArray(cell.calls) ? cell.calls : [];
  cell.trace = Array.isArray(cell.trace) ? cell.trace : cell.calls;
  cell.checkpoints = Array.isArray(cell.checkpoints) ? cell.checkpoints : [];
  cell.agent_s =
    cell.timing && cell.timing.agent != null
      ? Math.round(Number(cell.timing.agent))
      : null;
  cell.activity_sort = cell.activity?.primary_count ?? cell.turns ?? 0;
});

const armFilter = document.getElementById("fmode").closest("label");
document.getElementById("q").setAttribute("aria-label", "Search run details");
armFilter.insertAdjacentHTML(
  "afterend",
  '<label>harness <select id="fharness"></select></label>',
);

let sortKey = "task";
let sortDirection = 1;
let selectedIndex = -1;

function uniqueValues(key) {
  return [...new Set(CELLS.map((cell) => cell[key]).filter(Boolean))].sort();
}

function fillFilter(id, values) {
  const select = document.getElementById(id);
  select.innerHTML =
    '<option value="">all</option>' +
    values.map((value) => `<option>${esc(value)}</option>`).join("");
}

fillFilter("fharness", uniqueValues("harness"));
fillFilter("fsuite", uniqueValues("suite"));
fillFilter("fphase", uniqueValues("phase"));
fillFilter(
  "fflag",
  [...new Set(CELLS.flatMap((cell) => cell.flags))].sort(),
);

document.getElementById("head").innerHTML = COLS.map(
  ([key, label]) => `<th data-k="${key}">${label}</th>`,
).join("");
document.querySelectorAll("#head th").forEach((heading) => {
  const updateSort = () => {
    const key = heading.dataset.k;
    sortDirection = sortKey === key ? -sortDirection : 1;
    sortKey = key;
    render();
  };
  heading.tabIndex = 0;
  heading.setAttribute("role", "button");
  heading.onclick = updateSort;
  heading.onkeydown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      updateSort();
    }
  };
});

function passesFilters(cell) {
  const query = document.getElementById("q").value.toLowerCase();
  const searchable = [
    cell.task,
    cell.run_label,
    cell.harness,
    cell.model,
    cell.infra_detail,
    cell.instruction,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (query && !searchable.includes(query)) return false;
  const exactFilters = {
    fmode: "mode",
    fharness: "harness",
    fsuite: "suite",
    fphase: "phase",
  };
  for (const [id, key] of Object.entries(exactFilters)) {
    const value = document.getElementById(id).value;
    if (value && cell[key] !== value) return false;
  }
  const flag = document.getElementById("fflag").value;
  if (flag && !cell.flags.includes(flag)) return false;
  const score = document.getElementById("fscore").value;
  if (score !== "" && !(cell.score != null && cell.score <= Number(score))) {
    return false;
  }
  if (document.getElementById("ffail").checked && cell.success === true) {
    return false;
  }
  return true;
}

function compareCells(left, right) {
  let a = left[sortKey];
  let b = right[sortKey];
  if (sortKey === "flags") {
    a = left.flags.length;
    b = right.flags.length;
  }
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") {
    return (a - b) * sortDirection;
  }
  return String(a).localeCompare(String(b)) * sortDirection;
}

function renderCell(cell, key) {
  if (key === "score") {
    return `<td class="${scoreClass(cell.score)}">${cell.score == null ? "—" : Number(cell.score).toFixed(3)}</td>`;
  }
  if (key === "mode") {
    return `<td class="mode-${safeClass(cell.mode)}">${esc(cell.mode)}</td>`;
  }
  if (key === "cost") {
    return `<td>${cell.cost == null ? "—" : `$${Number(cell.cost).toFixed(3)}`}</td>`;
  }
  if (key === "activity_sort") {
    return `<td>${esc(activityLabel(cell))}</td>`;
  }
  if (key === "flags") {
    return `<td>${cell.flags
      .map((flag) => `<span class="chip ${safeClass(flag)}">${esc(flag)}</span>`)
      .join(" ")}</td>`;
  }
  return `<td>${esc(cell[key])}</td>`;
}

function render() {
  const rows = CELLS.filter(passesFilters).sort(compareCells);
  document.querySelectorAll("#head th").forEach((heading) => {
    const direction =
      heading.dataset.k === sortKey
        ? sortDirection === 1
          ? "ascending"
          : "descending"
        : "none";
    heading.setAttribute("aria-sort", direction);
  });
  document.getElementById("count").textContent =
    `— ${rows.length}/${CELLS.length} runs`;
  const body = document.getElementById("body");
  body.innerHTML =
    rows
      .map((cell) => {
        const globalIndex = CELLS.indexOf(cell);
        const cells = COLS.map(([key]) => renderCell(cell, key)).join("");
        return `<tr class="row ${globalIndex === selectedIndex ? "sel" : ""}" tabindex="0" data-i="${globalIndex}">${cells}</tr>`;
      })
      .join("") ||
    `<tr><td colspan="${COLS.length}" class="empty">no runs match filters</td></tr>`;
  body.querySelectorAll("tr.row").forEach((row) => {
    const selectRow = () => {
      selectedIndex = Number(row.dataset.i);
      render();
      renderDetail(CELLS[selectedIndex]);
    };
    row.onclick = selectRow;
    row.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectRow();
      }
    };
  });
  renderStats(rows);
}

function renderStats(rows) {
  const byHarness = {};
  rows.forEach((cell) => {
    if (cell.score == null) return;
    const bucket = (byHarness[cell.harness] ||= []);
    bucket.push(Number(cell.score));
  });
  const means = Object.entries(byHarness)
    .map(([harness, values]) => {
      const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
      return `<span>${esc(harness)} mean <b>${mean.toFixed(3)}</b></span>`;
    })
    .join("");
  const failures = rows.filter((cell) => cell.success !== true).length;
  document.getElementById("stats").innerHTML =
    '<span><b>Comparison contract:</b> score/time are comparable; tokens and cost are provider-reported; activity uses native units and is not normalized.</span>' +
    means +
    `<span>failures <b>${failures}</b></span>`;
}

function stepRow(marker, label, body) {
  return `<div class="step"><div class="t">${marker}</div><b>${label}</b>${body ? `<div>${body}</div>` : ""}</div>`;
}

function activityDetails(cell) {
  const activity = cell.activity;
  if (!activity) return `${activityLabel(cell)} · legacy trace`;
  const secondary = [
    activity.work_items ? `${activity.work_items} work items` : "",
    `${activity.tool_uses || 0} tool uses`,
    `${activity.agent_messages || 0} agent messages`,
    activity.file_changes ? `${activity.file_changes} file changes` : "",
  ].filter(Boolean);
  return `${esc(activity.label)} · ${secondary.map(esc).join(" · ")}`;
}

function retrievalDetails(cell) {
  const retrieval = cell.retrieval;
  if (!retrieval || !Object.keys(retrieval).length) return "";
  const inner = retrieval.inner || {};
  const combined = retrieval.combined || {};
  const scope = retrieval.repository_scope || {};
  const expected = scope.expected || [];
  const scopedCalls = scope.finder_calls_by_repo || {};
  const scoped = expected.length
    ? ` · scoped ${expected.filter((repo) => scopedCalls[repo] === 1).length}/${expected.length} repos · ambiguous ${scope.ambiguous_or_unscoped_calls || 0}`
    : "";
  const validity = retrieval.valid ? "valid" : `INVALID: ${retrieval.invalid_reason || "contract failed"}`;
  return `${validity} · Finder ${retrieval.code_finder_calls || 0} · direct ${retrieval.direct_retrieval_calls || 0}${scoped} · inner ${inner.turns || 0} turns / ${inner.tool_calls || 0} tools / ${inner.total_tokens || 0} tok · combined ${combined.total_tokens || 0} tok`;
}

function provenanceDetails(cell) {
  const provenance = (cell.retrieval || {}).provenance || {};
  if (!Object.keys(provenance).length) return "";
  const server = provenance.server_info || {};
  const inventory = provenance.tool_inventory_sha256 || "unavailable";
  const finderSchema = provenance.code_finder_schema_sha256 || "unavailable";
  return `trace v${provenance.trace_version || "?"} · ${provenance.trace_started_at || "?"} → ${provenance.trace_finished_at || "?"} · protocol ${provenance.protocol_version || "?"} · server ${server.name || "?"}@${server.version || "?"} · tools ${inventory} · code_finder schema ${finderSchema}`;
}

function judgeDetails(cell) {
  const judge = cell.judge || {};
  const requested = judge.requested || {};
  const provenance = judge.provenance || {};
  if (!requested.model && !Object.keys(provenance).length) return "";
  const requestedAccount = requested.account == null ? "default" : requested.account;
  if (!Object.keys(provenance).length) {
    return `requested ${requested.model || "?"} · account ${requestedAccount} · no completed judge provenance`;
  }
  const actualAccount = provenance.account == null ? "default" : provenance.account;
  return `requested ${requested.model || "?"} · ${provenance.backend || "?"} · ${provenance.model || "?"} · account ${actualAccount} · ${provenance.executable || "?"} · ${provenance.cli_version || "version unavailable"}`;
}

function lifecycleDetails(cell) {
  const lifecycle = cell.lifecycle || {};
  if (!Object.keys(lifecycle).length) return "";
  const duration = lifecycle.observed_duration_ms == null
    ? "duration unavailable"
    : `${(Number(lifecycle.observed_duration_ms) / 1000).toFixed(1)}s observed`;
  const state = lifecycle.unfinished_step ? "UNFINISHED step" : "finished";
  const gradedPath = lifecycle.graded_artifact_path || "/workspace/agent_output/answer.json";
  const gradedWritten = lifecycle.graded_artifact_written
    ?? lifecycle.canonical_answer_written
    ?? false;
  const graded = gradedWritten
    ? `graded artifact written (${gradedPath})`
    : `graded artifact missing (${gradedPath})`;
  const writes = Array.isArray(lifecycle.artifact_writes)
    ? lifecycle.artifact_writes.join(", ")
    : "";
  return `${duration} · starts ${lifecycle.step_starts || 0} / finishes ${lifecycle.step_finishes || 0} · ${state} · last ${lifecycle.last_event_type || "?"} · ${graded}${writes ? ` · writes ${writes}` : ""}`;
}

function traceRow(event) {
  const status = safeStatus(event.status);
  const symbol =
    status === "denied"
      ? "✗"
      : status === "error"
        ? "!"
        : status === "missing"
          ? "?"
          : status === "ok"
            ? "✓"
            : "·";
  return `<div class="call"><div class="st ${status}">${symbol}</div><div class="nm">${esc(event.name)}</div><div class="io">${esc(event.input)}${event.result ? `<span class="res ${status}">→ ${esc(event.result)}</span>` : ""}</div></div>`;
}

function renderDetail(cell) {
  const tools = cell.tools_exposed || [];
  const mcpTools = tools.filter((tool) => /mcp|sourcegraph/i.test(tool));
  const sgxTools = tools.filter((tool) => /sgx|sg_/i.test(tool));
  const timing = cell.timing || {};
  const trace = cell.trace || [];
  const retrieval = retrievalDetails(cell);
  const provenance = provenanceDetails(cell);
  const judge = judgeDetails(cell);
  const lifecycle = lifecycleDetails(cell);
  const traceRows = trace.map(traceRow).join("");
  const checkpoints = cell.checkpoints
    .map(
      (checkpoint) =>
        `<div class="cp"><b class="${scoreClass(checkpoint.score)}">${checkpoint.score == null ? "—" : Number(checkpoint.score).toFixed(2)}</b> ${esc(checkpoint.name)} <span class="d">w=${esc(checkpoint.weight)} ran=${esc(checkpoint.verifier_ran)} — ${esc(checkpoint.detail)}</span></div>`,
    )
    .join("");
  const flags = cell.flags
    .map((flag) => `<span class="chip ${safeClass(flag)}">${esc(flag)}</span>`)
    .join(" ");
  const toolBadge = (tool) =>
    `<span class="${/mcp|sourcegraph/i.test(tool) ? "mcp" : /sgx|sg_/i.test(tool) ? "sgx" : ""}">${esc(tool)}</span>`;
  document.getElementById("detail").innerHTML = `
  <h2>${esc(cell.harness)} / <span class="mode-${safeClass(cell.mode)}">${esc(cell.mode)}</span> / ${esc(cell.task)} ${flags}</h2>
  <div class="kv">
    <span class="k">run</span><span>${esc(cell.run_label || cell.run_id || "legacy")}</span>
    <span class="k">model</span><span>${esc(cell.model)}</span>
    <span class="k">score</span><span class="${scoreClass(cell.score)}">${cell.score == null ? "INVALID" : Number(cell.score).toFixed(3)}</span>
    <span class="k">phase</span><span>${esc(cell.phase)} ${cell.failure_class ? `/ ${esc(cell.failure_class)}` : ""}</span>
    <span class="k">suite/type</span><span>${esc(cell.suite)} / ${esc(cell.type)} (${esc(cell.difficulty)})</span>
    <span class="k">cost / tokens</span><span>${cell.cost == null ? "cost unavailable" : `$${Number(cell.cost).toFixed(3)}`} · provider-reported in ${cell.in_tok || 0} / out ${cell.out_tok || 0}</span>
    <span class="k">native activity</span><span>${activityDetails(cell)}</span>
    ${retrieval ? `<span class="k">Code Finder retrieval</span><span>${esc(retrieval)}</span>` : ""}
    ${provenance ? `<span class="k">MCP provenance</span><span>${esc(provenance)}</span>` : ""}
    ${judge ? `<span class="k">Judge provenance</span><span>${esc(judge)}</span>` : ""}
    ${lifecycle ? `<span class="k">OpenCode lifecycle</span><span>${esc(lifecycle)}</span>` : ""}
    ${cell.arm_gate_proof ? `<span class="k">Arm gate proof</span><span>${esc(cell.arm_gate_proof)}</span>` : ""}
    ${cell.infra_detail ? `<span class="k">infra/error</span><span class="sc-lo">${esc(cell.infra_detail)}</span>` : ""}
  </div>
  <h3>Comparison contract</h3>
  ${stepRow("=", "Comparable", "task score, checkpoint score, elapsed wall time")}
  ${stepRow("≈", "Provider-reported", "input/output tokens and cost; accounting and availability vary by provider")}
  ${stepRow("≠", "Provider-native activity", "a Claude/Codex turn is not an OpenCode step; use the labeled trace structure, not the raw count, for behavioral comparison")}
  <h3>Run steps</h3>
  ${stepRow("env", "Environment build", `image <code>${esc(cell.image_tag)}</code> · source ${esc(cell.source)} · mem ${esc(cell.memory_mb || "—")}MB · build ${esc(timing.build ?? "?")} · setup ${esc(timing.setup ?? "?")}`)}
  ${stepRow("model", "Agent pin", `harness <b>${esc(cell.harness)}</b> · model <b>${esc(cell.model)}</b> · agent-timeout ${esc(cell.timeout || "—")}s · verifier-timeout ${esc(cell.verifier_timeout || "—")}s`)}
  ${stepRow("gate", "Mode gate", `arm=${esc(cell.mode)} — ${modeGateLabel(cell)}`)}
  ${stepRow("agent", "Agent execution", `${activityDetails(cell)} · agent ${esc(timing.agent ?? "?")}s · scoring ${esc(timing.scoring ?? "?")}s`)}
  <div class="tool-badges">${(mcpTools.concat(sgxTools).length ? mcpTools.concat(sgxTools) : tools).map(toolBadge).join("") || "<span>no named tools captured</span>"}</div>
  <h3>Injected prompt</h3>
  <pre>${esc(cell.instruction || "(not captured)")}</pre>
  <h3>Trace — ${trace.length} events</h3>
  ${(cell.trace_sources || (cell.trace_source ? [cell.trace_source] : [])).length ? `<div class="count">sources: ${(cell.trace_sources || [cell.trace_source]).map(esc).join(" · ")}</div>` : ""}
  <div>${traceRows || '<div class="empty">no trace events</div>'}</div>
  <h3>Agent output produced — ${(cell.writes || []).length} deliverable write(s)</h3>
  ${(cell.writes || []).length ? cell.writes.map((write) => `<div class="wr"><div class="wrh"><span class="st ${safeStatus(write.status)}">${write.status === "denied" ? "✗ WRITE DENIED" : write.status === "ok" ? "✓ written" : esc(write.status)}</span> <code>${esc(write.path)}</code></div><pre>${esc(write.content || "(empty)")}</pre></div>`).join("") : '<div class="empty">agent wrote no captured deliverable file</div>'}
  <h3>Oracle — what the checker graded against</h3>
  ${cell.ground_truth ? `<div class="orc"><b>ground_truth.json</b><pre>${esc(cell.ground_truth)}</pre></div>` : ""}
  ${cell.expected_solution ? `<div class="orc"><b>expected_solution.json (curated)</b><pre>${esc(cell.expected_solution)}</pre></div>` : ""}
  ${!cell.ground_truth && !cell.expected_solution ? '<div class="empty">no ground_truth/expected_solution on disk for this task</div>' : ""}
  <h3>Scoring — ${cell.checkpoints.length} checkpoints</h3>
  <div>${checkpoints || '<div class="empty">no checkpoints</div>'}</div>`;
  document.getElementById("detail").scrollTop = 0;
}

["q", "fmode", "fharness", "fsuite", "fphase", "fflag", "fscore", "ffail"].forEach(
  (id) => {
    const element = document.getElementById(id);
    const event =
      element.tagName === "SELECT" || element.type === "checkbox"
        ? "change"
        : "input";
    element.addEventListener(event, render);
  },
);

render();
