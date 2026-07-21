"""Preamble variants for Experiment P1: Tool selection guidance.

Each variant modifies the _TOOL_SELECTION and related sections
of the MCP preamble to test different approaches to getting agents
to use find_references and go_to_definition.

Based on findings from Experiment H1:
- Pre-loading tool schemas does NOT change tool selection
- Agents never mention find_references in thinking blocks (0/1705)
- The current decision-list format is ignored during tool selection
- go_to_definition was used in 1 trial where the task naturally required it

Import these and pass to build_system_prompt() to override default sections.
"""

# ---------------------------------------------------------------------------
# Variant P0: Control — current preamble (unchanged)
# ---------------------------------------------------------------------------
# Uses the default _TOOL_SELECTION, _EFFICIENCY_RULES, _IF_STUCK from sourcegraph.py

# ---------------------------------------------------------------------------
# Variant P1: Procedural tool selection with explicit deliberation prompt
# ---------------------------------------------------------------------------
# Hypothesis: Agents don't deliberate about tools because the preamble doesn't
# ask them to. Adding "Before each search, think about which tool fits" may
# trigger reasoning about find_references.

P1_TOOL_SELECTION = """\
## Tool Selection — Think Before You Search

**Before each code search, briefly consider which tool fits your need:**

| I need to... | Use this tool |
|---|---|
| Find exact string/symbol matches | `keyword_search` |
| Explore a concept broadly | `nls_search` |
| Find every caller/usage of a function or type | `find_references` |
| Jump to where a symbol is defined | `go_to_definition` |
| Read a specific file | `read_file` |
| Browse directory structure | `list_files` |

**`find_references` is the fastest way to trace callers.** It returns every file \
and line that calls a given symbol, with surrounding context — the equivalent of \
"Find All References" in an IDE. One call replaces the typical pattern of \
keyword_search → read_file → keyword_search → read_file.

**`go_to_definition` jumps directly to a symbol's implementation.** Faster than \
searching for the symbol name and then reading the file.

Both require: `repo`, `path` (the file where you saw the symbol), and `symbol`. \
If you don't know the file path yet, do one keyword_search first to find it, \
then use find_references or go_to_definition for the rest."""

P1_EFFICIENCY_RULES = """\
## Efficiency Rules

- **For tracing callers:** keyword_search to locate the symbol once, then \
`find_references` for all usages — NOT repeated keyword_search + read_file loops
- **For understanding implementations:** `go_to_definition` to jump to the source — \
NOT keyword_search + read_file
- Chain searches logically: search → references → definition → read
- Don't re-search for the same pattern; use results from prior calls
- Prefer `keyword_search` over `nls_search` when you have exact terms
- Read 2-3 related files before synthesising, rather than one at a time"""

P1_IF_STUCK = """\
## If Stuck

If MCP search returns no results:
1. Reduce to 1-2 search terms (keyword_search uses AND logic — every term must match)
2. Try `nls_search` for broader semantic matching (uses OR logic with stemming)
3. Use `list_files` to browse the directory structure
4. Use `list_repos` to verify the repository name
5. Check if the symbol exists under a different module or package name"""

# ---------------------------------------------------------------------------
# Variant P2: Workflow-based with concrete recipes
# ---------------------------------------------------------------------------
# Hypothesis: The current preamble is declarative ("use X for Y") but agents
# need procedural recipes. Providing step-by-step workflows for common tasks
# may increase code intelligence tool usage.

P2_TOOL_SELECTION = """\
## Search Workflows

### Finding all callers of a function (impact analysis)
```
Step 1: keyword_search("FunctionName repo:^github.com/org/repo$")
        → note the file path where the function is defined
Step 2: find_references(repo: "github.com/org/repo", path: "<file from step 1>", symbol: "FunctionName")
        → returns every file and line that calls FunctionName
```
This 2-step workflow replaces the usual 10+ call pattern of \
keyword_search → read_file → keyword_search → read_file.

### Tracing a dependency chain
```
Step 1: keyword_search("SymbolA repo:^github.com/org/repo$")
        → find where SymbolA is defined
Step 2: go_to_definition(repo: "...", path: "<file>", symbol: "SymbolA")
        → see the full implementation
Step 3: find_references(repo: "...", path: "<file>", symbol: "SymbolA")
        → see everything that depends on SymbolA
Step 4: Repeat steps 2-3 for each downstream symbol
```

### Exploring unfamiliar code
```
Step 1: nls_search("how authentication works repo:^github.com/org/repo$")
        → broad semantic search for the concept
Step 2: read_file for the most relevant results
Step 3: keyword_search for specific symbols found in step 2
```

### Quick lookup
| I need... | Tool |
|---|---|
| Exact string match | `keyword_search` |
| Concept search | `nls_search` |
| All callers of X | `find_references` |
| Definition of X | `go_to_definition` |
| File contents | `read_file` |
| Directory listing | `list_files` |"""

P2_EFFICIENCY_RULES = """\
## Efficiency Rules

- **Use the right workflow above** — don't default to keyword_search for everything
- When tracing callers: 2 calls (keyword_search + find_references) > 10 calls (search + read loops)
- Don't re-search for the same pattern; use results from prior calls
- Read 2-3 related files before synthesising, rather than one at a time
- Use `nls_search` for exploration, `keyword_search` for precision"""

P2_IF_STUCK = P1_IF_STUCK  # Same improved version

# ---------------------------------------------------------------------------
# Variant P3: Minimal — just improve find_references framing
# ---------------------------------------------------------------------------
# Hypothesis: The current "Need all callers/references? -> find_references"
# doesn't convey enough value. Reframing it as the IDE equivalent with
# concrete output description may be sufficient.

P3_TOOL_SELECTION = """\
## Tool Selection

**Decision logic:**
1. Know the exact symbol? → `keyword_search`
2. Know the concept, not the name? → `nls_search`
3. Need to jump to a symbol's source code? → `go_to_definition`
4. Need to find every file and line that calls a symbol? → `find_references`
   (Returns structured results with file paths, line numbers, and ±3 lines context. \
The IDE "Find All References" action, but across the entire codebase.)
5. Need full file content? → `read_file`
6. Need deep cross-repo analysis? → `deepsearch` (then `deepsearch_read` after 60s)

`find_references` requires: `repo`, `path` (file where you saw the symbol), `symbol`. \
If you don't know the path, do one `keyword_search` first to find it."""

# Use same efficiency and if-stuck as P1
P3_EFFICIENCY_RULES = P1_EFFICIENCY_RULES
P3_IF_STUCK = P1_IF_STUCK


# ---------------------------------------------------------------------------
# Variant P4: Value description only — isolates the assertive framing
# ---------------------------------------------------------------------------
# Hypothesis: The p1 deliberation prompt and prerequisite recipe were NOT
# what drove find_references usage (thinking blocks never reference them).
# The assertive value description ("fastest way to trace callers") was the
# active ingredient. This variant tests that by adding ONLY the value
# description to the existing flat list format — no "Think Before You Search",
# no prerequisite recipe, no workflow changes.

P4_TOOL_SELECTION = """\
## Tool Selection

**Decision logic:**
1. Know the exact symbol? → `keyword_search`
2. Know the concept, not the name? → `nls_search`
3. Need definition of a symbol? → `go_to_definition`
4. Need to find every file and line that calls a symbol? → `find_references` \
— the fastest way to trace callers. One call returns all references with \
file paths, line numbers, and surrounding context, replacing \
keyword_search → read_file loops.
5. Need full file content? → `read_file`
6. Need deep cross-repo analysis? → `deepsearch` (then `deepsearch_read` after 60s)"""

# Use the ORIGINAL efficiency and if-stuck (no changes from control)
# This isolates the value description as the only variable.

# ---------------------------------------------------------------------------
# Variant P5: Value descriptions for ALL underused tools
# ---------------------------------------------------------------------------
# Tests whether assertive value framing works for go_to_definition,
# nls_search, commit_search, and diff_search too — not just find_references.

P5_TOOL_SELECTION = """\
## Tool Selection

**Decision logic:**
1. Know the exact symbol? → `keyword_search`
2. Know the concept, not the name? → `nls_search` — uses OR logic with \
stemming, so partial matches and synonyms work. 5% empty rate vs 30% for \
keyword_search. Try this first for exploration.
3. Need definition of a symbol? → `go_to_definition` — jumps directly to \
the source implementation. Faster than searching for the symbol and then \
reading the file.
4. Need to find every file and line that calls a symbol? → `find_references` \
— the fastest way to trace callers. One call returns all references with \
file paths, line numbers, and surrounding context, replacing \
keyword_search → read_file loops.
5. Need full file content? → `read_file`
6. Need to know what changed recently? → `commit_search` — search by author, \
message, or code content to find relevant commits. Use `diff_search` to \
find specific lines that were added or removed.
7. Need deep cross-repo analysis? → `deepsearch` (then `deepsearch_read` after 60s)"""


# ---------------------------------------------------------------------------
# Helper: get variant sections by name
# ---------------------------------------------------------------------------
VARIANTS = {
    "p0_control": {
        "tool_selection": None,  # use default
        "efficiency_rules": None,
        "if_stuck": None,
    },
    "p1_deliberate": {
        "tool_selection": P1_TOOL_SELECTION,
        "efficiency_rules": P1_EFFICIENCY_RULES,
        "if_stuck": P1_IF_STUCK,
    },
    "p2_workflows": {
        "tool_selection": P2_TOOL_SELECTION,
        "efficiency_rules": P2_EFFICIENCY_RULES,
        "if_stuck": P2_IF_STUCK,
    },
    "p3_minimal": {
        "tool_selection": P3_TOOL_SELECTION,
        "efficiency_rules": P3_EFFICIENCY_RULES,
        "if_stuck": P3_IF_STUCK,
    },
    "p4_value_only": {
        "tool_selection": P4_TOOL_SELECTION,
        "efficiency_rules": None,  # unchanged from control
        "if_stuck": None,  # unchanged from control
    },
    "p5_all_tools_value": {
        "tool_selection": P5_TOOL_SELECTION,
        "efficiency_rules": None,  # unchanged from control
        "if_stuck": None,  # unchanged from control
    },
}


def get_variant(name: str) -> dict[str, str | None]:
    """Return preamble sections for a named variant."""
    if name not in VARIANTS:
        raise ValueError(f"Unknown variant: {name!r}. Available: {list(VARIANTS.keys())}")
    return VARIANTS[name]
