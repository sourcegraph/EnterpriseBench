#!/usr/bin/env python3
"""Generate MCP instruction files and .mcp.json configs for EnterpriseBench tasks.

For each task, produces:
  1. instruction_mcp.md -- Sourcegraph MCP usage guide with repo filter syntax
  2. .mcp.json          -- Claude Code MCP discovery config

Adapted from CodeScaleBench's MCP agent configuration patterns.

Usage:
    python3 scripts/infra/generate_mcp_instructions.py benchmarks/EXAMPLE_TASK.toml
    python3 scripts/infra/generate_mcp_instructions.py benchmarks/ --all
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(REPO_ROOT / "scripts" / "infra"))
from create_sg_mirrors import parse_toml, find_task_files, ORG

sys.path.insert(0, str(REPO_ROOT / "scripts" / "sandbox"))
from dockerfile_generator import mirror_name_for_repo

sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
from validation import validate_repo_entry


# ═══════════════════════════════════════════════════════════════
#  MCP INSTRUCTION GENERATION
# ═══════════════════════════════════════════════════════════════

TOOL_GUIDE = """\
### Available Sourcegraph MCP Tools

| Tool | Purpose | Best For |
|------|---------|----------|
| `sg_keyword_search` | Fast exact string matching | Function names, error messages, imports |
| `sg_nls_search` | Natural language semantic search | "How does X work?", architecture questions |
| `sg_read_file` | Read file from indexed repo | Examining specific files found via search |
| `sg_list_files` | Find files by pattern | Discovering file/directory structure |
| `sg_go_to_definition` | Navigate to symbol definitions | Tracing function/type definitions |
| `sg_find_references` | Find all references to a symbol | Understanding usage patterns |
| `sg_commit_search` | Search git commit history | Understanding code evolution |

### Search Strategy

1. **Start broad**: Use `sg_nls_search` to understand the problem domain
2. **Get specific**: Use `sg_keyword_search` for exact function/class names
3. **Trace definitions**: Use `sg_go_to_definition` for symbol navigation
4. **Check usage**: Use `sg_find_references` to understand impact
5. **Read files**: Use `sg_read_file` for detailed examination\
"""


def generate_instruction_mcp(task_data: dict) -> str:
    """Generate instruction_mcp.md content for a task."""
    task_info = task_data.get("task", {})
    repos = task_data.get("repos", [])
    metadata = task_data.get("metadata", {})

    task_id = task_info.get("id", "unknown")
    description = task_info.get("description", "")
    pattern = metadata.get("multi_repo_pattern", "")

    lines = [
        f"# Sourcegraph MCP Guide: {task_id}",
        "",
        f"> {description}",
        "",
        "## Repository Mirrors",
        "",
        "The following repositories are indexed in Sourcegraph as pinned mirrors.",
        "Use the repo filter syntax to scope searches to specific repos.",
        "",
    ]

    # List each mirror with its filter syntax
    full_mirrors = []
    for repo in repos:
        url = repo.get("url", "")
        rev = repo.get("rev", "")
        path = repo.get("path", "")
        role = repo.get("role", "unknown")

        if not url or not rev:
            continue

        validate_repo_entry(repo)
        full_mirror = f"{ORG}/{mirror_name_for_repo(url, rev)}"
        full_mirrors.append(full_mirror)

        lines.append(f"### {path} ({role})")
        lines.append(f"- **Upstream**: {url} @ `{rev}`")
        lines.append(f"- **Mirror**: `{full_mirror}`")
        lines.append(f"- **Filter**: `repo:^github.com/{full_mirror}$`")
        lines.append("")

    # Multi-repo search example
    if len(repos) > 1:
        all_filters = [f"github.com/{m}" for m in full_mirrors]

        lines.append("### Searching Across All Repos")
        lines.append("")
        lines.append("To search all task repos at once, use an OR pattern:")
        lines.append("```")
        or_pattern = "|".join(all_filters)
        lines.append(f"repo:^({or_pattern})$")
        lines.append("```")
        lines.append("")

    # Multi-repo pattern guidance
    if pattern:
        lines.append(f"## Multi-Repo Pattern: {pattern}")
        lines.append("")
        pattern_guides = {
            "propagate": (
                "This task follows the **propagate** pattern: changes in a dependency "
                "must be propagated to consumers. Start by understanding the API change "
                "in the dependency repo, then trace all consumer usages."
            ),
            "investigate": (
                "This task follows the **investigate** pattern: a problem manifests in "
                "one repo but the root cause may be in another. Use cross-repo search "
                "to trace the issue across service boundaries."
            ),
            "enforce": (
                "This task follows the **enforce** pattern: a policy or standard must "
                "be applied consistently across repos. Search for violations across all "
                "repos simultaneously."
            ),
            "orchestrate": (
                "This task follows the **orchestrate** pattern: changes must be "
                "coordinated across repos in a specific order. Map the dependency graph "
                "first, then make changes in dependency order."
            ),
        }
        lines.append(pattern_guides.get(pattern, ""))
        lines.append("")

    # Tool guide
    lines.append(TOOL_GUIDE)
    lines.append("")

    # Workflow recommendation
    lines.append("## Recommended Workflow")
    lines.append("")
    lines.append("1. Use Sourcegraph MCP tools to explore and understand the codebase")
    lines.append("2. Identify all relevant files and code paths")
    lines.append("3. Read specific files locally for detailed examination")
    lines.append("4. Make targeted code changes")
    lines.append("5. Verify changes with tests")
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  MCP CONFIG GENERATION
# ═══════════════════════════════════════════════════════════════

SOURCEGRAPH_MCP_URL = "https://demo.sourcegraph.com/.api/mcp/v1"


def generate_mcp_config() -> dict:
    """Generate .mcp.json config for Sourcegraph MCP."""
    return {
        "mcpServers": {
            "sourcegraph": {
                "type": "http",
                "url": SOURCEGRAPH_MCP_URL,
                "headers": {
                    "Authorization": "token ${SOURCEGRAPH_ACCESS_TOKEN}"
                },
            }
        }
    }


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

def generate_for_task(task_file: Path, output_dir: Path | None = None) -> dict[str, Path]:
    """Generate MCP instructions and config for a single task."""
    task_data = parse_toml(task_file)

    if output_dir is None:
        output_dir = task_file.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # instruction_mcp.md
    content = generate_instruction_mcp(task_data)
    path = output_dir / "instruction_mcp.md"
    path.write_text(content)
    results["instruction_mcp"] = path

    # .mcp.json
    config = generate_mcp_config()
    path = output_dir / ".mcp.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    results["mcp_config"] = path

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Generate MCP instruction files and configs for EnterpriseBench tasks"
    )
    parser.add_argument("path", type=Path,
                        help="Path to a task.toml file or directory containing tasks")
    parser.add_argument("--output-dir", "-o", type=Path, default=None,
                        help="Output directory (default: alongside task.toml)")
    parser.add_argument("--all", action="store_true",
                        help="Process all task files in directory")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"ERROR: {args.path} does not exist", file=sys.stderr)
        sys.exit(1)

    if args.all or args.path.is_dir():
        task_files = find_task_files(args.path)
    else:
        task_files = [args.path]

    if not task_files:
        print(f"ERROR: No .toml files found in {args.path}", file=sys.stderr)
        sys.exit(1)

    total_generated = 0

    for task_file in task_files:
        print(f"\nProcessing: {task_file}")
        try:
            results = generate_for_task(task_file, args.output_dir)
            for name, path in results.items():
                print(f"  {name:20s} -> {path}")
            total_generated += len(results)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)

    print(f"\nGenerated {total_generated} files from {len(task_files)} task(s)")


if __name__ == "__main__":
    main()
