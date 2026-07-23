#!/usr/bin/env python3
"""Build a self-contained HTML root-causing console over sweep result cells.

Walks results/runs/<task>/<mode>/, extracts per-cell metadata, the harness run
steps (environment/build, injected preamble, exposed tools = MCP/sgx config,
mode-gate outcome), and a compact tool-call trace with error signatures, then
emits one self-contained HTML with the data embedded inline (file:// blocks
fetch, so nothing is loaded at runtime).

Usage:
    python3 scripts/analysis/build_rootcause_view.py \
        --results results/runs --out /tmp/rootcause.html
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

MODES = ("baseline", "mcp_only", "cli", "hybrid")
RESULT_SNIPPET = 500
INSTR_CAP = 12000
OUTPUT_CAP = 9000
ORACLE_CAP = 9000
_DELIVERABLE_HINTS = ("answer", "report", "drift", ".md", ".json", "output", "analysis", "plan")


def build_taskdir_map(bench: Path) -> dict:
    """result-dir-name (== [task].id) -> task.toml directory. The results tree
    is keyed by task_id, which frequently differs from the benchmark dir name."""
    import tomllib

    out = {}
    for tp in bench.glob("*/*/task.toml"):
        if "_archived" in tp.parts or "mined" in tp.parts:
            continue
        try:
            tid = tomllib.load(tp.open("rb")).get("task", {}).get("id")
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if tid:
            out[tid] = tp.parent
    return out


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _iter_trace(path: Path):
    if not path.exists():
        return
    for line in path.open(errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    out.append(c.get("text", ""))
                elif c.get("type") == "tool_result":
                    out.append(_text_of(c.get("content", "")))
        return "\n".join(out)
    return json.dumps(content)[:RESULT_SNIPPET]


def _result_status(txt: str) -> str:
    low = txt.lower()
    if "eacces" in low or "permission denied" in low:
        return "denied"
    if "<tool_use_error>" in low or "error:" in low[:40]:
        return "error"
    if "not found" in low or "no such file" in low:
        return "missing"
    return "ok"


def _init_event(stdout_path: Path) -> dict:
    """The stream-json `system/init` event carries the tool list and MCP server
    handshake status — the ground truth of what tool configuration the agent
    actually booted with. It lives in agent_stdout.log, not agent_trace.jsonl."""
    for ev in _iter_trace(stdout_path):
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            return ev
    return {}


def extract_trace(trace_path: Path, stdout_path: Path) -> dict:
    """Return {tools_exposed, mcp_servers, instruction, calls[], terminal}."""
    init = _init_event(stdout_path)
    tools_exposed: list[str] = init.get("tools", []) or []
    mcp_servers: list[dict] = init.get("mcp_servers", []) or []
    instruction = ""
    calls: list[dict] = []
    writes: dict[str, dict] = {}  # deliverable path -> {content, status} (last write wins)
    terminal = {}
    pending: dict[str, dict] = {}
    write_pending: dict[str, str] = {}  # tool_use_id -> path

    for ev in _iter_trace(trace_path):
        t = ev.get("type")
        if t == "user" and not instruction:
            msg = ev.get("message") or {}
            # first user turn that is not a tool_result is the injected prompt
            content = msg.get("content")
            if isinstance(content, str):
                instruction = content
            elif isinstance(content, list) and not any(
                isinstance(c, dict) and c.get("type") == "tool_result"
                for c in content
            ):
                instruction = _text_of(content)
        if t == "assistant":
            for c in (ev.get("message") or {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    inp = c.get("input", {})
                    summ = inp.get("command") or inp.get("file_path") or ""
                    if not summ and inp:
                        summ = json.dumps(inp)[:160]
                    rec = {
                        "name": c.get("name", "?"),
                        "input": str(summ)[:200],
                        "status": "pending",
                        "result": "",
                    }
                    calls.append(rec)
                    pending[c.get("id", "")] = rec
                    # Capture deliverable Write attempts (content survives even
                    # when the write later EACCES-fails in a gated arm).
                    if c.get("name") in ("Write", "Edit"):
                        fp = (inp.get("file_path") or "").lower()
                        if any(h in fp for h in _DELIVERABLE_HINTS):
                            content = inp.get("content") or inp.get("new_string") or ""
                            writes[inp.get("file_path")] = {
                                "content": content[:OUTPUT_CAP],
                                "status": "pending",
                            }
                            write_pending[c.get("id", "")] = inp.get("file_path")
        if t == "user":
            for c in (ev.get("message") or {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    tuid = c.get("tool_use_id", "")
                    rec = pending.pop(tuid, None)
                    if rec is not None:
                        txt = _text_of(c.get("content", ""))
                        rec["status"] = _result_status(txt)
                        rec["result"] = txt[:RESULT_SNIPPET]
                        wp = write_pending.pop(tuid, None)
                        if wp and wp in writes:
                            writes[wp]["status"] = rec["status"]
        if t == "result":
            terminal = {
                "subtype": ev.get("subtype"),
                "num_turns": ev.get("num_turns"),
                "terminal_reason": ev.get("terminal_reason"),
                "is_error": ev.get("is_error"),
            }
    return {
        "tools_exposed": tools_exposed,
        "mcp_servers": mcp_servers,
        "instruction": instruction[:INSTR_CAP],
        "calls": calls,
        "writes": [{"path": p, **v} for p, v in writes.items()],
        "terminal": terminal,
    }


def flags_for(cell: dict) -> list[str]:
    f = []
    fc = cell.get("failure_class") or ""
    ph = cell.get("phase") or ""
    if any(c["status"] == "denied" for c in cell["calls"]):
        f.append("EACCES")
    if fc == "infra_sgx_unused":
        f.append("zero-sgx")
    if ph == "integrity_violation":
        f.append("integrity")
    if "seal_check_unreachable" in json.dumps(cell.get("infra_detail", "")):
        f.append("container-gone")
    if fc == "agent_max_turns" or (cell["terminal"].get("subtype") == "error_max_turns"):
        f.append("max-turns")
    if any("not found" in (cp.get("detail", "") or "").lower() for cp in cell["checkpoints"]):
        f.append("deliverable-missing")
    if ph == "verifier_infra_error":
        f.append("verifier-infra")
    # An MCP arm that ended with 0 MCP calls, or an sgx arm with 0 sgx calls,
    # never exercised its retrieval channel — the arm measured nothing.
    if cell["mode"] in ("mcp_only", "hybrid") and (cell.get("mcp_calls") or 0) == 0:
        f.append("no-mcp-calls")
    if cell["mode"] == "cli" and (cell.get("sgx_calls") or 0) == 0 and "zero-sgx" not in f:
        f.append("no-sgx-calls")
    return f


def build_cells(results_dir: Path, taskdirs: dict) -> list[dict]:
    cells = []
    for rj in sorted(results_dir.glob("*/*/results.json")):
        mode = rj.parent.name
        if mode not in MODES:
            continue
        task = rj.parent.parent.name
        r = _load_json(rj)
        cfg = _load_json(rj.parent / "config.json")
        tu = r.get("tool_usage") or {}
        tm = r.get("timing") or {}
        scores = r.get("scores") or {}
        meta = r.get("task_metadata") or {}
        runmeta = r.get("run_metadata") or {}
        tr = extract_trace(
            rj.parent / "agent_trace.jsonl", rj.parent / "agent_stdout.log"
        )
        tdir = taskdirs.get(task)
        ground_truth = ""
        expected_solution = ""
        if tdir:
            gt = tdir / "ground_truth.json"
            es = tdir / "expected_solution.json"
            if gt.exists():
                ground_truth = gt.read_text(errors="replace")[:ORACLE_CAP]
            if es.exists():
                expected_solution = es.read_text(errors="replace")[:ORACLE_CAP]
        infra = (
            (r.get("verifier_infra_error") or {}).get("detail")
            or (r.get("integrity_violation") or {}).get("detail")
            or (scores.get("verifier_infra_error") or {}).get("detail")
            or (scores.get("integrity_violation") or {}).get("detail")
            or r.get("error")
            or ""
        )
        cell = {
            "task": task,
            "mode": mode,
            "suite": meta.get("suite", ""),
            "type": meta.get("task_type", ""),
            "difficulty": meta.get("difficulty", ""),
            "phase": r.get("phase", ""),
            "success": r.get("success"),
            "score": scores.get("task_score"),
            "failure_class": r.get("failure_class") or "",
            "infra_detail": infra,
            "cost": tu.get("cost_usd"),
            "in_tok": tu.get("total_input_tokens"),
            "out_tok": tu.get("total_output_tokens"),
            "turns": tu.get("num_turns"),
            "mcp_calls": tu.get("mcp_tool_calls"),
            "sgx_calls": tu.get("sgx_tool_calls"),
            "model": runmeta.get("model") or cfg.get("model"),
            "cli_version": runmeta.get("claude_cli_version"),
            "image_tag": r.get("image_tag", ""),
            "source": cfg.get("source"),
            "timeout": cfg.get("timeout"),
            "verifier_timeout": cfg.get("verifier_timeout"),
            "memory_mb": cfg.get("memory_mb"),
            "timing": {
                k: tm.get(k) for k in ("parse", "build", "setup", "agent", "scoring")
            },
            "checkpoints": [
                {
                    "name": cp.get("name"),
                    "weight": cp.get("weight"),
                    "score": cp.get("score"),
                    "passed": cp.get("passed"),
                    "detail": cp.get("detail", ""),
                    "verifier_ran": cp.get("verifier_ran"),
                }
                for cp in (scores.get("checkpoints") or [])
            ],
            "tools_exposed": tr["tools_exposed"],
            "mcp_servers": tr["mcp_servers"],
            "instruction": tr["instruction"],
            "calls": tr["calls"],
            "writes": tr["writes"],
            "terminal": tr["terminal"],
            "ground_truth": ground_truth,
            "expected_solution": expected_solution,
        }
        cell["flags"] = flags_for(cell)
        cells.append(cell)
    return cells


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EnterpriseBench — Run Root-Cause Console</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2430;--bd:#30363d;--fg:#e6edf3;--dim:#8b949e;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--acc:#58a6ff;--mcp:#bc8cff;--sgx:#39c5cf;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 var(--mono)}
header{position:sticky;top:0;z-index:10;background:var(--panel);border-bottom:1px solid var(--bd);padding:8px 12px}
h1{font-size:15px;margin:0 0 6px}
.filters{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}
.filters label{color:var(--dim);font-size:11px;display:flex;gap:4px;align-items:center;cursor:pointer}
input,select{background:var(--panel2);color:var(--fg);border:1px solid var(--bd);border-radius:4px;padding:3px 6px;font:12px var(--mono)}
.chip{padding:1px 6px;border-radius:9px;font-size:10px;border:1px solid var(--bd);white-space:nowrap}
.chip.EACCES,.chip.integrity{background:#3d1418;color:var(--bad);border-color:#6e1f24}
.chip.zero-sgx,.chip.deliverable-missing,.chip.no-mcp-calls,.chip.no-sgx-calls{background:#3a2c10;color:var(--warn);border-color:#6b4d15}
.chip.max-turns,.chip.verifier-infra,.chip.container-gone{background:#132a3d;color:var(--acc);border-color:#1c3d5a}
.stats{display:flex;gap:16px;font-size:11px;color:var(--dim);margin-top:6px;flex-wrap:wrap}
.stats b{color:var(--fg)}
.wrap{display:flex;height:calc(100vh - 96px)}
.tablewrap{flex:1;overflow:auto;border-right:1px solid var(--bd)}
table{border-collapse:collapse;width:100%;font-size:12px}
th{position:sticky;top:0;background:var(--panel2);text-align:left;padding:5px 7px;border-bottom:1px solid var(--bd);cursor:pointer;white-space:nowrap;user-select:none}
td{padding:4px 7px;border-bottom:1px solid #21262d;white-space:nowrap}
tr.row{cursor:pointer}tr.row:hover{background:var(--panel2)}tr.row.sel{background:#1f6feb33}
.mode-baseline{color:var(--dim)}.mode-mcp_only{color:var(--mcp)}.mode-cli{color:var(--sgx)}.mode-hybrid{color:var(--warn)}
.sc-hi{color:var(--ok)}.sc-mid{color:var(--warn)}.sc-lo{color:var(--bad)}
.detail{width:46%;min-width:420px;overflow:auto;padding:12px 16px;background:var(--panel)}
.detail h2{font-size:14px;margin:2px 0 8px;border-bottom:1px solid var(--bd);padding-bottom:4px}
.detail h3{font-size:12px;color:var(--acc);margin:14px 0 5px;text-transform:uppercase;letter-spacing:.04em}
.kv{display:grid;grid-template-columns:130px 1fr;gap:2px 10px;font-size:12px}
.kv .k{color:var(--dim)}
.step{border-left:2px solid var(--bd);padding:2px 0 2px 10px;margin:4px 0}
.step .t{color:var(--dim);font-size:11px}
pre{background:var(--panel2);border:1px solid var(--bd);border-radius:5px;padding:8px;overflow:auto;max-height:340px;white-space:pre-wrap;word-break:break-word;font-size:11px}
.call{display:grid;grid-template-columns:16px 62px 1fr;gap:6px;padding:3px 0;border-bottom:1px solid #21262d;align-items:start}
.call .st{font-weight:bold;text-align:center}
.st.denied,.st.error{color:var(--bad)}.st.missing{color:var(--warn)}.st.ok{color:var(--ok)}.st.pending{color:var(--dim)}
.call .nm{color:var(--acc)}
.call .io{color:var(--fg);word-break:break-word}
.call .io .res{color:var(--dim);display:block;margin-top:1px}
.res.denied,.res.error{color:var(--bad)!important}.res.missing{color:var(--warn)!important}
.tool-badges span{font-size:10px;padding:1px 5px;margin:1px;border-radius:3px;display:inline-block;background:var(--panel2);border:1px solid var(--bd)}
.tool-badges .mcp{color:var(--mcp);border-color:#3d2d5a}.tool-badges .sgx{color:var(--sgx)}
.cp{padding:3px 0;border-bottom:1px solid #21262d}.cp .d{color:var(--dim);font-size:11px}
.wr{margin:6px 0}.wr .wrh{font-size:11px;margin-bottom:2px}.wr code,.orc code{color:var(--acc)}
.orc{margin:6px 0}.orc b{font-size:11px;color:var(--dim)}
.empty{color:var(--dim);padding:40px;text-align:center}
.count{color:var(--dim);font-size:11px}
</style></head><body>
<header>
<h1>EnterpriseBench — Run Root-Cause Console <span class="count" id="count"></span></h1>
<div class="filters">
<input id="q" placeholder="search task/detail…" style="width:180px">
<label>arm <select id="fmode"><option value="">all</option><option>baseline</option><option>mcp_only</option><option>cli</option><option>hybrid</option></select></label>
<label>suite <select id="fsuite"></select></label>
<label>phase <select id="fphase"></select></label>
<label>flag <select id="fflag"></select></label>
<label>score ≤ <input id="fscore" type="number" step="0.05" min="0" max="1" style="width:60px"></label>
<label><input type="checkbox" id="ffail"> failures only</label>
</div>
<div class="stats" id="stats"></div>
</header>
<div class="wrap">
<div class="tablewrap"><table id="tbl"><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table></div>
<div class="detail" id="detail"><div class="empty">Select a row to inspect its run steps, injected preamble, exposed tools (MCP/sgx config), trace, and scoring.</div></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const CELLS = JSON.parse(document.getElementById('data').textContent);
const COLS = [
  ['task','task'],['mode','arm'],['suite','suite'],['type','type'],['phase','phase'],
  ['score','score'],['cost','$'],['out_tok','out_tok'],['turns','turns'],
  ['mcp_calls','mcp'],['sgx_calls','sgx'],['agent_s','lat_s'],['flags','flags']
];
CELLS.forEach(c=>{c.agent_s = c.timing && c.timing.agent!=null ? Math.round(+c.timing.agent) : null;});
const esc = s => (s==null?'':(''+s)).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
const scoreCls = v => v==null?'':(v>=0.6?'sc-hi':v>=0.3?'sc-mid':'sc-lo');
let sortKey='task', sortDir=1, selIdx=-1;

function uniq(k){return [...new Set(CELLS.map(c=>c[k]).filter(Boolean))].sort();}
function fill(sel,vals){const e=document.getElementById(sel);e.innerHTML='<option value="">all</option>'+vals.map(v=>`<option>${esc(v)}</option>`).join('');}
fill('fsuite',uniq('suite'));fill('fphase',uniq('phase'));
const allFlags=[...new Set(CELLS.flatMap(c=>c.flags))].sort();
document.getElementById('fflag').innerHTML='<option value="">all</option>'+allFlags.map(f=>`<option>${esc(f)}</option>`).join('');

document.getElementById('head').innerHTML = COLS.map(([k,l])=>`<th data-k="${k}">${l}</th>`).join('');
document.querySelectorAll('#head th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;sortDir=(sortKey===k)?-sortDir:1;sortKey=k;render();});

function passes(c){
  const q=document.getElementById('q').value.toLowerCase();
  if(q && !(c.task.toLowerCase().includes(q)||(c.infra_detail||'').toLowerCase().includes(q)||(c.instruction||'').toLowerCase().includes(q))) return false;
  const fm=document.getElementById('fmode').value; if(fm && c.mode!==fm) return false;
  const fs=document.getElementById('fsuite').value; if(fs && c.suite!==fs) return false;
  const fp=document.getElementById('fphase').value; if(fp && c.phase!==fp) return false;
  const ff=document.getElementById('fflag').value; if(ff && !c.flags.includes(ff)) return false;
  const fsc=document.getElementById('fscore').value; if(fsc!=='' && !(c.score!=null && c.score<=+fsc)) return false;
  if(document.getElementById('ffail').checked && c.success===true) return false;
  return true;
}
function cmp(a,b){let x=a[sortKey],y=b[sortKey];
  if(sortKey==='flags'){x=a.flags.length;y=b.flags.length;}
  if(x==null)return 1;if(y==null)return -1;
  if(typeof x==='number'&&typeof y==='number')return (x-y)*sortDir;
  return (''+x).localeCompare(''+y)*sortDir;}

function render(){
  const rows=CELLS.filter(passes).sort(cmp);
  document.getElementById('count').textContent=`— ${rows.length}/${CELLS.length} cells`;
  const body=document.getElementById('body');
  body.innerHTML=rows.map(c=>{
    const gi=CELLS.indexOf(c);
    const tds=COLS.map(([k])=>{
      if(k==='score')return `<td class="${scoreCls(c.score)}">${c.score==null?'—':c.score.toFixed(2)}</td>`;
      if(k==='mode')return `<td class="mode-${c.mode}">${c.mode}</td>`;
      if(k==='cost')return `<td>${c.cost==null?'—':'$'+(+c.cost).toFixed(2)}</td>`;
      if(k==='flags')return `<td>${c.flags.map(f=>`<span class="chip ${f}">${f}</span>`).join(' ')}</td>`;
      return `<td>${esc(c[k])}</td>`;
    }).join('');
    return `<tr class="row ${gi===selIdx?'sel':''}" data-i="${gi}">${tds}</tr>`;
  }).join('') || '<tr><td colspan="13" class="empty">no cells match filters</td></tr>';
  body.querySelectorAll('tr.row').forEach(tr=>tr.onclick=()=>{selIdx=+tr.dataset.i;render();detail(CELLS[selIdx]);});
  stats(rows);
}
function stats(rows){
  const by={};['baseline','mcp_only','cli','hybrid'].forEach(m=>{
    const r=rows.filter(c=>c.mode===m&&c.score!=null);
    if(r.length)by[m]=(r.reduce((s,c)=>s+c.score,0)/r.length);
  });
  const fails=rows.filter(c=>c.success!==true).length;
  const flagct={};rows.forEach(c=>c.flags.forEach(f=>flagct[f]=(flagct[f]||0)+1));
  document.getElementById('stats').innerHTML=
    Object.entries(by).map(([m,v])=>`<span>${m} mean <b>${v.toFixed(3)}</b></span>`).join('')+
    `<span>failures <b>${fails}</b></span>`+
    Object.entries(flagct).sort((a,b)=>b[1]-a[1]).map(([f,n])=>`<span>${f} <b>${n}</b></span>`).join('');
}
function stepRow(t,label,body){return `<div class="step"><div class="t">${t}</div><b>${label}</b>${body?('<div>'+body+'</div>'):''}</div>`;}
function detail(c){
  const tools=c.tools_exposed||[];
  const mcpTools=tools.filter(t=>/mcp|sourcegraph/i.test(t));
  const sgxTools=tools.filter(t=>/sgx|sg_/i.test(t));
  const tm=c.timing||{};
  const badge=t=>`<span class="${/mcp|sourcegraph/i.test(t)?'mcp':(/sgx|sg_/i.test(t)?'sgx':'')}">${esc(t)}</span>`;
  const calls=(c.calls||[]).map(cl=>`<div class="call"><div class="st ${cl.status}">${cl.status==='denied'?'✗':cl.status==='error'?'!':cl.status==='missing'?'?':cl.status==='ok'?'✓':'·'}</div><div class="nm">${esc(cl.name)}</div><div class="io">${esc(cl.input)}${cl.result?`<span class="res ${cl.status}">→ ${esc(cl.result)}</span>`:''}</div></div>`).join('');
  const cps=(c.checkpoints||[]).map(cp=>`<div class="cp"><b class="${scoreCls(cp.score)}">${cp.score==null?'—':cp.score.toFixed(2)}</b> ${esc(cp.name)} <span class="d">w=${cp.weight} ran=${cp.verifier_ran} — ${esc(cp.detail)}</span></div>`).join('');
  document.getElementById('detail').innerHTML=`
  <h2><span class="mode-${c.mode}">${esc(c.mode)}</span> / ${esc(c.task)} ${c.flags.map(f=>`<span class="chip ${f}">${f}</span>`).join(' ')}</h2>
  <div class="kv">
    <span class="k">score</span><span class="${scoreCls(c.score)}">${c.score==null?'INVALID':c.score.toFixed(3)}</span>
    <span class="k">phase</span><span>${esc(c.phase)} ${c.failure_class?('/ '+esc(c.failure_class)):''}</span>
    <span class="k">suite/type</span><span>${esc(c.suite)} / ${esc(c.type)} (${esc(c.difficulty)})</span>
    <span class="k">cost / tokens</span><span>${c.cost==null?'—':'$'+(+c.cost).toFixed(3)} · in ${c.in_tok||0} / out ${c.out_tok||0}</span>
    <span class="k">turns</span><span>${c.turns} · mcp ${c.mcp_calls} · sgx ${c.sgx_calls}</span>
    ${c.infra_detail?`<span class="k">infra/error</span><span class="sc-lo">${esc(c.infra_detail)}</span>`:''}
  </div>
  <h3>Run steps</h3>
  ${stepRow('env','Environment build',`image <code>${esc(c.image_tag)}</code> · source ${esc(c.source)} · mem ${c.memory_mb}MB · build ${tm.build||'?'} · setup ${tm.setup||'?'}`)}
  ${stepRow('model','Agent pin',`model <b>${esc(c.model)}</b> · CLI ${esc(c.cli_version||'—')} · agent-timeout ${c.timeout}s · verifier-timeout ${c.verifier_timeout}s`)}
  ${stepRow('gate','Mode gate',`arm=${esc(c.mode)} — ${c.mode==='baseline'||c.mode==='hybrid'?'ungated (local source readable)':'gated (local source denied; remote tool is the only path to code)'}${c.flags.includes('EACCES')?' <span class="sc-lo">— in-repo write blocked (EACCES)</span>':''}`)}
  ${stepRow('mcp','Tool configuration (MCP/sgx exposed at session init)',`${tools.length} tools at init · <b class="mode-mcp_only">${mcpTools.length} MCP</b> · <b class="mode-cli">${sgxTools.length} sgx</b> · used: mcp ${c.mcp_calls} / sgx ${c.sgx_calls}`+
    ((c.mcp_servers&&c.mcp_servers.length)?` · servers: `+c.mcp_servers.map(s=>`${esc(s.name)}=<b class="${s.status==='connected'?'sc-hi':'sc-mid'}">${esc(s.status)}</b>`).join(', '):''))}
  <div class="tool-badges">${(mcpTools.concat(sgxTools).length?mcpTools.concat(sgxTools):tools).map(badge).join('')||'<span>none exposed at init</span>'}</div>
  ${stepRow('agent','Agent execution',`${c.turns} turns · terminal ${esc((c.terminal||{}).subtype||'?')} ${esc((c.terminal||{}).terminal_reason||'')} · agent ${tm.agent||'?'} · scoring ${tm.scoring||'?'}`)}
  <h3>Injected preamble (exact text the agent received)</h3>
  <pre>${esc(c.instruction||'(not captured)')}</pre>
  <h3>Trace — ${c.calls.length} tool calls</h3>
  <div>${calls||'<div class="empty">no tool calls</div>'}</div>
  <h3>Agent output produced — ${(c.writes||[]).length} deliverable write(s)</h3>
  ${(c.writes&&c.writes.length)?c.writes.map(w=>`<div class="wr"><div class="wrh"><span class="st ${w.status}">${w.status==='denied'?'✗ WRITE DENIED':w.status==='ok'?'✓ written':w.status}</span> <code>${esc(w.path)}</code></div><pre>${esc(w.content||'(empty)')}</pre></div>`).join(''):'<div class="empty">agent wrote no deliverable file</div>'}
  <h3>Oracle — what the checker graded against</h3>
  ${c.ground_truth?`<div class="orc"><b>ground_truth.json</b><pre>${esc(c.ground_truth)}</pre></div>`:''}
  ${c.expected_solution?`<div class="orc"><b>expected_solution.json (curated)</b><pre>${esc(c.expected_solution)}</pre></div>`:''}
  ${(!c.ground_truth&&!c.expected_solution)?'<div class="empty">no ground_truth/expected_solution on disk for this task</div>':''}
  <h3>Scoring — ${c.checkpoints.length} checkpoints</h3>
  <div>${cps||'<div class="empty">no checkpoints</div>'}</div>`;
  document.getElementById('detail').scrollTop=0;
}
['q','fmode','fsuite','fphase','fflag','fscore','ffail'].forEach(id=>{
  const e=document.getElementById(id);e.addEventListener(e.tagName==='SELECT'||e.type==='checkbox'?'change':'input',render);
});
render();
</script></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results/runs")
    ap.add_argument("--out", default="/tmp/rootcause.html")
    args = ap.parse_args()
    taskdirs = build_taskdir_map(Path("benchmarks"))
    cells = build_cells(Path(args.results), taskdirs)
    data = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
    out = Path(args.out)
    out.write_text(HTML_TEMPLATE.replace("__DATA__", data), encoding="utf-8")
    size_mb = out.stat().st_size / 1e6
    print(f"wrote {out} — {len(cells)} cells, {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
