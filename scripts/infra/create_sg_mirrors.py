#!/usr/bin/env python3
"""Create sg-evals mirrors on GitHub for reproducible Sourcegraph indexing.

Reads task.toml files (EnterpriseBench format) and either:
  1. Generates a mirror_creation_manifest.json (--manifest-only)
  2. Creates GitHub repos under the sg-evals org, pinned to specific commits

Adapted from CodeScaleBench's create_sg_mirrors.py for EnterpriseBench's
multi-repo task.toml format.

Usage:
    # Generate manifest from a single task
    python3 scripts/infra/create_sg_mirrors.py benchmarks/EXAMPLE_TASK.toml

    # Generate manifest from all tasks
    python3 scripts/infra/create_sg_mirrors.py benchmarks/

    # Actually create mirrors (requires gh CLI + sg-evals org access)
    python3 scripts/infra/create_sg_mirrors.py benchmarks/ --execute

    # Dry run of mirror creation
    python3 scripts/infra/create_sg_mirrors.py benchmarks/ --execute --dry-run
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

from mirror_naming import GITHUB_REPO_NAME_RE, ORG, derive_mirror_name, is_hex_rev

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_DIR = REPO_ROOT / "configs" / "runs"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
from validation import validate_repo_entry  # noqa: E402


# ═══════════════════════════════════════════════════════════════
#  TASK.TOML PARSING
# ═══════════════════════════════════════════════════════════════

def parse_toml(path: Path) -> dict:
    """Parse a TOML file, falling back to a simple parser if tomllib unavailable."""
    if tomllib:
        with open(path, "rb") as f:
            return tomllib.load(f)
    # Minimal fallback for environments without tomllib/tomli
    return _parse_toml_fallback(path)


def _parse_toml_fallback(path: Path) -> dict:
    """Bare-minimum TOML parser for task.toml files.

    Handles the subset we need: [section], key = "value", [[array]], and
    multi-line strings. NOT a full TOML parser.
    """
    import re

    text = path.read_text()
    result = {}
    current_section = None
    current_array_key = None
    in_multiline = False
    multiline_key = None
    multiline_val = ""

    for line in text.splitlines():
        stripped = line.strip()

        # Multi-line string handling
        if in_multiline:
            if '"""' in stripped:
                multiline_val += stripped.split('"""')[0]
                target = result
                if current_section:
                    for part in current_section.split("."):
                        target = target.setdefault(part, {})
                if current_array_key and isinstance(target, list):
                    target[-1][multiline_key] = multiline_val
                else:
                    target[multiline_key] = multiline_val
                in_multiline = False
            else:
                multiline_val += line + "\n"
            continue

        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            continue

        # Array of tables: [[repos]]
        m = re.match(r'^\[\[(\w+)\]\]$', stripped)
        if m:
            key = m.group(1)
            result.setdefault(key, [])
            result[key].append({})
            current_section = None
            current_array_key = key
            continue

        # Table: [task]
        m = re.match(r'^\[([^\]]+)\]$', stripped)
        if m:
            current_section = m.group(1)
            current_array_key = None
            parts = current_section.split(".")
            target = result
            for part in parts:
                target = target.setdefault(part, {})
            continue

        # Key-value pair
        m = re.match(r'^(\w+)\s*=\s*(.+)$', stripped)
        if m:
            key, val_str = m.group(1), m.group(2).strip()

            # Multi-line string start
            if val_str.startswith('"""'):
                in_multiline = True
                multiline_key = key
                multiline_val = val_str[3:] + "\n" if len(val_str) > 3 else ""
                continue

            # Parse value
            val = _parse_toml_value(val_str)

            # Place in correct location
            if current_array_key and result.get(current_array_key):
                result[current_array_key][-1][key] = val
            elif current_section:
                parts = current_section.split(".")
                target = result
                for part in parts:
                    target = target.setdefault(part, {})
                target[key] = val
            else:
                result[key] = val

    return result


def _parse_toml_value(val_str: str):
    """Parse a TOML value string."""
    val_str = val_str.strip()
    # String
    if val_str.startswith('"') and val_str.endswith('"'):
        return val_str[1:-1]
    # Array
    if val_str.startswith("[") and val_str.endswith("]"):
        inner = val_str[1:-1].strip()
        if not inner:
            return []
        items = []
        for item in inner.split(","):
            item = item.strip().strip('"')
            if item:
                items.append(item)
        return items
    # Integer (handle underscores like 1_400_000)
    try:
        return int(val_str.replace("_", ""))
    except ValueError:
        pass
    # Float
    try:
        return float(val_str)
    except ValueError:
        pass
    # Boolean
    if val_str.lower() == "true":
        return True
    if val_str.lower() == "false":
        return False
    return val_str


def find_task_files(path: Path) -> list[Path]:
    """Find all task.toml files given a file or directory path."""
    if path.is_file():
        return [path]
    task_files = sorted(path.rglob("*.toml"))
    if not task_files:
        # Check if we were given a directory with task.toml inside
        candidate = path / "task.toml"
        if candidate.exists():
            return [candidate]
    return task_files


def extract_mirrors_from_task(task_data: dict, task_file: Path) -> list[dict]:
    """Extract mirror entries from a parsed task.toml."""
    task_info = task_data.get("task", {})
    task_id = task_info.get("id", task_file.stem)
    repos = task_data.get("repos", [])

    mirrors = []
    for repo in repos:
        url = repo.get("url", "")
        rev = repo.get("rev", "")
        if not url or not rev:
            continue
        validate_repo_entry(repo)

        # Normalize URL: strip https://, trailing .git
        upstream = url.replace("https://", "").replace("http://", "").rstrip("/")
        if upstream.endswith(".git"):
            upstream = upstream[:-4]

        mirrors.append({
            "upstream": upstream,
            "commit": rev,
            "mirror": derive_mirror_name(url, rev),
            "pin_source": f"task.toml repos[].rev",
            "tasks": [task_id],
        })

    return mirrors


def merge_mirrors(all_mirrors: list[dict]) -> list[dict]:
    """Deduplicate mirrors, merging task lists for identical mirrors."""
    by_mirror = {}
    for m in all_mirrors:
        key = m["mirror"]
        if key in by_mirror:
            existing = by_mirror[key]
            for task in m["tasks"]:
                if task not in existing["tasks"]:
                    existing["tasks"].append(task)
        else:
            by_mirror[key] = dict(m)
    return sorted(by_mirror.values(), key=lambda m: m["mirror"])


# ═══════════════════════════════════════════════════════════════
#  MANIFEST GENERATION
# ═══════════════════════════════════════════════════════════════

def generate_manifest(task_files: list[Path]) -> dict:
    """Generate a mirror creation manifest from task files."""
    all_mirrors = []

    for task_file in task_files:
        try:
            task_data = parse_toml(task_file)
            mirrors = extract_mirrors_from_task(task_data, task_file)
            all_mirrors.extend(mirrors)
        except Exception as e:
            print(f"WARNING: Failed to parse {task_file}: {e}", file=sys.stderr)

    merged = merge_mirrors(all_mirrors)

    return {
        "_description": "Mirrors needed for reproducible Sourcegraph indexing (EnterpriseBench)",
        "_generated": str(date.today()),
        "_status": f"{len(merged)} mirrors from {len(task_files)} task files",
        "mirrors": merged,
    }


# ═══════════════════════════════════════════════════════════════
#  MIRROR CREATION (adapted from CodeScaleBench)
# ═══════════════════════════════════════════════════════════════

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, returning CompletedProcess."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            cmd, returncode=124,
            stdout=e.stdout or "",
            stderr=f"timed out after {e.timeout}s",
        )


def repo_exists(mirror_name: str) -> bool:
    """Check if a GitHub repo already exists."""
    r = run(["gh", "repo", "view", f"{ORG}/{mirror_name}", "--json", "name"])
    return r.returncode == 0


def resolve_commit(upstream: str, ref: str) -> str | None:
    """Resolve a short hash or tag to a full commit SHA via GitHub API."""
    org_repo = upstream.replace("github.com/", "")
    r = run(["gh", "api", f"repos/{org_repo}/commits/{ref}", "--jq", ".sha"])
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return None


def create_mirror(entry: dict, dry_run: bool = False) -> tuple[bool, str]:
    """Create a single sg-evals mirror. Returns (success, message)."""
    mirror_name = entry["mirror"].replace(f"{ORG}/", "")
    if not GITHUB_REPO_NAME_RE.match(mirror_name):
        raise ValueError(f"Invalid mirror name: {mirror_name}")
    upstream = entry["upstream"]
    commit = entry["commit"]
    org_repo = upstream.replace("github.com/", "")

    # Check if already exists with content
    if repo_exists(mirror_name):
        r = run(["gh", "api", f"repos/{ORG}/{mirror_name}/commits",
                 "--jq", "length"])
        if r.returncode == 0 and r.stdout.strip() not in ("0", ""):
            return True, "already exists"
        # Repo exists but is empty — delete so we can recreate cleanly
        if not dry_run:
            r = run(["gh", "repo", "delete", f"{ORG}/{mirror_name}", "--yes"])
            if r.returncode != 0:
                return False, f"could not delete empty repo: {r.stderr.strip()}"
            time.sleep(2)

    # Resolve short hashes to full
    archive_ref = commit

    if is_hex_rev(commit) and len(commit) < 40:
        full_sha = resolve_commit(upstream, commit)
        if full_sha:
            archive_ref = full_sha
        else:
            return False, f"could not resolve short hash {commit}"

    if dry_run:
        return True, f"would create from {org_repo}@{archive_ref[:12]}"

    # Create the GitHub repo
    desc = f"Mirror of {org_repo} at {commit}"
    r = run(["gh", "repo", "create", f"{ORG}/{mirror_name}",
             "--public", "--description", desc])
    if r.returncode != 0:
        return False, f"gh repo create failed: {r.stderr.strip()}"

    time.sleep(2)

    # Disable secret scanning and push protection so OSS repos with test
    # tokens/fixtures (vault, grafana, zulip, etc.) can be pushed
    run(["gh", "api", f"repos/{ORG}/{mirror_name}",
         "-X", "PATCH",
         "-f", "security_and_analysis.secret_scanning.status=disabled",
         "-f", "security_and_analysis.secret_scanning_push_protection.status=disabled",
         "--silent"])
    time.sleep(1)

    # Download archive to temp dir
    workdir = tempfile.mkdtemp(prefix=f"sgmirror-{mirror_name}-")
    try:
        archive_url = f"https://github.com/{org_repo}/archive/{archive_ref}.tar.gz"
        r = run(["curl", "-sSL", "--fail", "-o", f"{workdir}/archive.tar.gz", archive_url],
                timeout=1800)
        if r.returncode != 0:
            return False, f"archive download failed: {r.stderr.strip()}"

        r = run(["tar", "xzf", f"{workdir}/archive.tar.gz", "-C", workdir], timeout=600)
        if r.returncode != 0:
            return False, f"tar extract failed: {r.stderr.strip()}"

        os.remove(f"{workdir}/archive.tar.gz")
        extracted = [d for d in os.listdir(workdir) if os.path.isdir(f"{workdir}/{d}")]
        if not extracted:
            return False, "no directory found after extraction"
        src_dir = f"{workdir}/{extracted[0]}"

        # Init git repo and commit
        env = {**os.environ, "GIT_AUTHOR_NAME": "sg-evals",
               "GIT_AUTHOR_EMAIL": "benchmarks@sourcegraph.com",
               "GIT_COMMITTER_NAME": "sg-evals",
               "GIT_COMMITTER_EMAIL": "benchmarks@sourcegraph.com"}

        r = run(["git", "init", "-b", "main"], cwd=src_dir, env=env)
        if r.returncode != 0:
            return False, f"git init failed: {r.stderr.strip()}"

        r = run(["git", "add", "-A"], cwd=src_dir, env=env, timeout=300)
        if r.returncode != 0:
            return False, f"git add failed: {r.stderr.strip()}"

        commit_msg = (f"Mirror of {org_repo} at {commit}\n\n"
                      f"Upstream: https://github.com/{org_repo}\nRef: {archive_ref}")
        r = run(["git", "commit", "-m", commit_msg], cwd=src_dir, env=env, timeout=120)
        if r.returncode != 0:
            return False, f"git commit failed: {r.stderr.strip()}"

        remote_url = f"https://github.com/{ORG}/{mirror_name}.git"
        r = run(["git", "remote", "add", "origin", remote_url], cwd=src_dir, env=env)
        if r.returncode != 0:
            return False, f"git remote add failed: {r.stderr.strip()}"

        r = run(["git", "push", "-u", "origin", "main"], cwd=src_dir, env=env, timeout=1800)
        if r.returncode != 0:
            push_err = r.stderr.strip()
            # GitHub push protection blocks public repos containing test
            # tokens/API keys.  Workaround: make private → push → make public.
            if "push declined due to repository rule violations" in push_err or "GH013" in push_err:
                run(["gh", "api", f"repos/{ORG}/{mirror_name}",
                     "-X", "PATCH", "-f", "private=true", "--silent"])
                time.sleep(3)
                r2 = run(["git", "push", "-u", "origin", "main"], cwd=src_dir, env=env, timeout=1800)
                # Restore public visibility regardless of push outcome
                run(["gh", "api", f"repos/{ORG}/{mirror_name}",
                     "-X", "PATCH", "-f", "private=false", "--silent"])
                if r2.returncode != 0:
                    return False, f"git push failed (even after private-toggle): {r2.stderr.strip()}"
                return True, "created successfully (private-toggle workaround)"
            return False, f"git push failed: {push_err}"

        return True, "created successfully"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def execute_mirrors(manifest: dict, dry_run: bool = False, skip_existing: bool = True):
    """Create all mirrors from a manifest."""
    entries = manifest["mirrors"]

    if skip_existing and not dry_run:
        print("Checking which mirrors already exist...")
        r = run(["gh", "repo", "list", ORG, "--limit", "500", "--json", "name", "--jq", ".[].name"])
        existing = set(r.stdout.strip().split("\n")) if r.returncode == 0 else set()
        to_create = [e for e in entries if e["mirror"].replace(f"{ORG}/", "") not in existing]
        skipped = len(entries) - len(to_create)
        print(f"  {len(existing)} repos exist, {skipped} already done, {len(to_create)} to create\n")
        entries = to_create

    created = 0
    failed = []
    mode = "DRY RUN" if dry_run else "CREATING"
    print(f"=== {mode}: {len(entries)} mirrors ===\n")

    for i, entry in enumerate(entries, 1):
        mirror = entry["mirror"]
        upstream_short = entry["upstream"].replace("github.com/", "")
        ref_short = entry["commit"][:12] if len(entry["commit"]) > 12 else entry["commit"]

        print(f"[{i}/{len(entries)}] {mirror} <- {upstream_short}@{ref_short} ... ", end="", flush=True)

        ok, msg = create_mirror(entry, dry_run=dry_run)
        if ok:
            if "already exists" in msg:
                print(f"SKIP ({msg})")
            else:
                print(f"OK ({msg})")
                created += 1
                # Rate-limit to avoid "created too many repositories" errors
                if not dry_run:
                    time.sleep(5)
        else:
            if "too quickly" in msg:
                print(f"RATE LIMITED — waiting 60s")
                time.sleep(60)
                # Retry once
                ok2, msg2 = create_mirror(entry, dry_run=dry_run)
                if ok2:
                    print(f"  RETRY OK ({msg2})")
                    created += 1
                    time.sleep(5)
                else:
                    print(f"  RETRY FAIL ({msg2})")
                    failed.append((mirror, msg2))
            else:
                print(f"FAIL ({msg})")
                failed.append((mirror, msg))

    print(f"\n=== Summary: {created} created, {len(failed)} failed ===")
    if failed:
        print("\nFailed mirrors:")
        for mirror, msg in failed:
            print(f"  {mirror}: {msg}")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate mirror manifest and optionally create sg-evals mirrors"
    )
    parser.add_argument("path", type=Path,
                        help="Path to a task.toml file or directory containing tasks")
    parser.add_argument("--execute", action="store_true",
                        help="Actually create mirrors (default: just generate manifest)")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --execute, show what would be done without creating")
    parser.add_argument("--no-skip", action="store_true",
                        help="Don't skip repos that already exist (retry empty/failed ones)")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Output path for manifest JSON (default: configs/runs/mirror_creation_manifest.json)")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"ERROR: {args.path} does not exist", file=sys.stderr)
        sys.exit(1)

    # Find and parse task files
    task_files = find_task_files(args.path)
    if not task_files:
        print(f"ERROR: No .toml files found in {args.path}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(task_files)} task file(s)")

    # Generate manifest
    manifest = generate_manifest(task_files)
    print(f"Generated manifest with {len(manifest['mirrors'])} mirrors")

    # Write manifest
    output_path = args.output or (MANIFEST_DIR / "mirror_creation_manifest.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote manifest to {output_path}")

    # Print summary
    print("\nMirrors:")
    for m in manifest["mirrors"]:
        tasks = ", ".join(m["tasks"])
        print(f"  {m['mirror']}  <-  {m['upstream']}@{m['commit'][:12]}")
        print(f"    tasks: {tasks}")

    # Optionally create mirrors
    if args.execute:
        print("\n" + "=" * 60)
        execute_mirrors(manifest, dry_run=args.dry_run,
                        skip_existing=not args.no_skip)


if __name__ == "__main__":
    main()
