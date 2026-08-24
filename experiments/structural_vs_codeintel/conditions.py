"""The five tool conditions, and the parity check that keeps them comparable.

A condition is a tool surface plus the backend that answers it. Everything else
-- model, sampling, system prompt, task text, repo snapshot, round budget,
retry policy, scorer -- comes from ``toast_retrieval.run_claude_policy`` and is
identical across all five, so a between-condition delta is attributable to the
retrieval primitive on offer.

``read_file``, ``list_files`` and ``submit_files`` appear in every condition,
reusing the shared harness's own schemas verbatim. Only the search/navigation
tools vary.

The descriptions are written to one template: what the tool matches, then what
it returns. None of them says when to prefer it, which is the outcome the
experiment measures and therefore the one thing a description must not supply.
``check_description_parity`` enforces that mechanically, because "we were
careful" is not a result anyone can check.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "toast_retrieval"))

from run_claude_policy import TOOLS  # noqa: E402

from backends import CodeIntelSeam, ConditionBackend, StructuralSeam, ast_grep_binary  # noqa: E402

_SHARED = {t["function"]["name"]: t for t in TOOLS}
_TOP_K = {"type": "integer", "default": 25}

#: Present in every condition; taken from the shared harness unchanged.
COMMON_TOOLS = [_SHARED["read_file"], _SHARED["list_files"], _SHARED["submit_files"]]

# A local copy of the shared grep tool, differing only in that its `pattern`
# argument documents its own syntax -- as ast_search's does. Query-language
# syntax belongs on the argument, not in the claim about what the tool does,
# which is what keeps the two claims comparable in `check_description_parity`.
# The shared TOOLS entry is left untouched so the existing arms are unaffected.
GREP_TOOL = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": _SHARED["grep"]["function"]["description"],
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression, POSIX extended syntax.",
                },
                "case_sensitive": {"type": "boolean", "default": False},
                "top_k": _TOP_K,
            },
            "required": ["pattern"],
        },
    },
}

AST_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "ast_search",
        "description": (
            "Syntax-tree pattern search over file contents of the in-scope "
            "repositories."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Source code in which `$NAME` matches one node and "
                        "`$$$NAME` matches a sequence of nodes."
                    ),
                },
                "language": {
                    "type": "string",
                    "description": (
                        "Source language of the pattern, e.g. python, go, "
                        "rust, java, typescript, c, cpp. Inferred from the "
                        "repository if omitted."
                    ),
                },
                "top_k": _TOP_K,
            },
            "required": ["pattern"],
        },
    },
}

#: The ablation variant. Identical to AST_SEARCH_TOOL except that its `pattern`
#: argument shows two worked patterns instead of only naming the metavariable
#: forms. The tool, backend and top-level claim are unchanged, so a difference
#: between an arm holding this and an arm holding AST_SEARCH_TOOL is a
#: difference in how the query language was explained, not in what the tool can
#: do.
#:
#: This is the ablation `DECLARED_ASYMMETRIES` says to run rather than take on
#: faith: the pilot found ast_search unused in 52 of 54 runs that also held
#: grep, and avoiding a capability and avoiding a calling convention look
#: identical in that number.
AST_SEARCH_TOOL_WITH_EXAMPLE = {
    "type": "function",
    "function": {
        "name": AST_SEARCH_TOOL["function"]["name"],
        "description": AST_SEARCH_TOOL["function"]["description"],
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Source code in which `$NAME` matches one node and "
                        "`$$$NAME` matches a sequence of nodes. In python, "
                        "`def $F($$$ARGS)` matches any function definition, "
                        "and `$OBJ.get($KEY)` matches any call to a `get` "
                        "method."
                    ),
                },
                "language": AST_SEARCH_TOOL["function"]["parameters"]["properties"][
                    "language"
                ],
                "top_k": _TOP_K,
            },
            "required": ["pattern"],
        },
    },
}

DEFINITION_TOOL = {
    "type": "function",
    "function": {
        "name": "definition",
        "description": (
            "Resolve a symbol occurring at a known location to its definition, "
            "using the index's symbol resolution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "repository identifier"},
                "path": {
                    "type": "string",
                    "description": "repo-relative path where the symbol occurs",
                },
                "symbol": {"type": "string"},
                "top_k": _TOP_K,
            },
            "required": ["repo", "path", "symbol"],
        },
    },
}

REFERENCES_TOOL = {
    "type": "function",
    "function": {
        "name": "references",
        "description": (
            "Find the references to a symbol occurring at a known location, "
            "using the index's symbol resolution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "repository identifier"},
                "path": {
                    "type": "string",
                    "description": "repo-relative path where the symbol occurs",
                },
                "symbol": {"type": "string"},
                "top_k": _TOP_K,
            },
            "required": ["repo", "path", "symbol"],
        },
    },
}

#: Tools whose descriptions the parity check governs. read/list/submit are
#: shared verbatim and carry no retrieval claim.
RETRIEVAL_TOOLS = (GREP_TOOL, AST_SEARCH_TOOL, DEFINITION_TOOL, REFERENCES_TOOL)

#: The same registry with the exampled ast_search substituted in. Ablation arms
#: draw from here; every other arm draws from RETRIEVAL_TOOLS.
ABLATION_TOOLS = (
    GREP_TOOL,
    AST_SEARCH_TOOL_WITH_EXAMPLE,
    DEFINITION_TOOL,
    REFERENCES_TOOL,
)


@dataclass(frozen=True)
class Condition:
    name: str
    tool_names: tuple[str, ...]
    #: What the condition's search/navigation tools actually know. Carried into
    #: every report so the analysis cannot quietly upgrade ast_search to
    #: "semantic code intelligence".
    knows: str
    #: Set only on an arm that deliberately departs from the shared tool
    #: schemas, and says how. Carried into every report, so an ablation arm
    #: cannot be read later as another sample of the arm it departs from.
    description_ablation: str | None = None

    @property
    def tools(self) -> list[dict]:
        registry = (
            ABLATION_TOOLS if self.description_ablation else RETRIEVAL_TOOLS
        )
        by_name = {t["function"]["name"]: t for t in (*registry, *COMMON_TOOLS)}
        return [by_name[n] for n in self.tool_names] + list(COMMON_TOOLS)

    def backend(self) -> ConditionBackend:
        return ConditionBackend(
            lexical="grep" in self.tool_names,
            structural=StructuralSeam(ast_grep_binary())
            if "ast_search" in self.tool_names
            else None,
            code_intel=CodeIntelSeam() if "definition" in self.tool_names else None,
        )


CONDITIONS: dict[str, Condition] = {
    c.name: c
    for c in (
        Condition("lexical", ("grep",), "text"),
        Condition("structural", ("ast_search",), "syntax"),
        Condition(
            "codeintel",
            ("definition", "references"),
            "resolved symbol identity",
        ),
        Condition(
            "structural_lexical",
            ("grep", "ast_search"),
            "text + syntax",
        ),
        Condition(
            "combined",
            ("grep", "ast_search", "definition", "references"),
            "text + syntax + resolved symbol identity",
        ),
        # The ablation pair. `structural_lexical_syntax` is the preference test
        # -- does the agent reach for ast_search once its syntax is shown? --
        # against `structural_lexical` as its control. `structural_syntax` is
        # the capability test: with no grep to fall back on, does the example
        # change how well the arm retrieves? Without the second arm, a null
        # result on the first cannot distinguish "the example did not persuade
        # the agent" from "the example was not usable".
        Condition(
            "structural_lexical_syntax",
            ("grep", "ast_search"),
            "text + syntax",
            description_ablation="ast_search's `pattern` argument shows two worked patterns instead of only naming the metavariable forms. Everything else, including the backend and the tool's own description, is unchanged.",
        ),
        Condition(
            "structural_syntax",
            ("ast_search",),
            "syntax",
            description_ablation="ast_search's `pattern` argument shows two worked patterns instead of only naming the metavariable forms. Everything else, including the backend and the tool's own description, is unchanged.",
        ),
    )
}

#: The default sweep: the arms whose tool schemas are the shared ones. Ablation
#: arms are opt-in by name, so a re-run of the pilot cannot silently grow arms
#: (and cost) it was not asked for.
PILOT_CONDITIONS = tuple(
    name for name, condition in CONDITIONS.items() if not condition.description_ablation
)

#: Arms that depart from the shared schemas, and the arm each one is read
#: against. A contrast that crosses this mapping is an ablation result; any
#: other use of these arms is a category error.
ABLATION_CONTROLS = {
    "structural_lexical_syntax": "structural_lexical",
    "structural_syntax": "structural",
}

# Language that tells the model when to reach for a tool. Any of these in a
# retrieval tool's description means the experiment is measuring the
# description rather than the primitive.
_PREFERENCE_LANGUAGE = re.compile(
    r"\b(prefer|preferred|best|better|faster|slower|cheaper|powerful|precise|"
    r"accurate|reliable|use this when|start with|first choice|ideal|"
    r"more (?:targeted|effective)|instead of|rather than)\b",
    re.IGNORECASE,
)
#: Descriptions must be the same order of magnitude in length. The band is
#: generous on purpose: the point is to catch one arm being handed a paragraph,
#: not to force identical prose onto tools that take different arguments.
_LENGTH_BAND = 1.8

#: The argument that carries each tool's query language, and so is the argument
#: that must document its own syntax if any of them does.
_QUERY_ARGUMENT = {"grep": "pattern", "ast_search": "pattern"}


def check_description_parity(tools=RETRIEVAL_TOOLS) -> list[str]:
    """Reasons the retrieval tool descriptions are not comparably written."""
    problems: list[str] = []
    lengths: dict[str, int] = {}
    for tool in tools:
        function = tool["function"]
        name = function["name"]
        lengths[name] = len(function["description"])
        # Preference language is checked over the whole schema: an argument
        # description is just as capable of telling the model when to reach for
        # a tool as the claim is.
        blob = function["description"] + " " + " ".join(
            prop.get("description", "")
            for prop in function["parameters"]["properties"].values()
        )
        found = _PREFERENCE_LANGUAGE.findall(blob)
        if found:
            problems.append(f"{name}: preference language {sorted(set(found))}")
        if not function["parameters"]["properties"].get(
            _QUERY_ARGUMENT.get(name, ""), {}
        ).get("description") and name in _QUERY_ARGUMENT:
            problems.append(f"{name}: query argument has no syntax note")
    if lengths:
        longest, shortest = max(lengths.values()), min(lengths.values())
        if longest > shortest * _LENGTH_BAND:
            problems.append(
                f"description lengths span {shortest}-{longest} chars "
                f"(> {_LENGTH_BAND}x): {lengths}"
            )
    return problems


#: A declared, deliberate asymmetry. ast_search's description spends one
#: sentence on metavariable syntax; grep's does not explain regexes. Regex is
#: near-universal and ast-grep's metavariables are not, so omitting the syntax
#: would handicap the structural arm on unfamiliarity rather than on the
#: primitive. It is still an asymmetry, and the next experiment should ablate
#: it (structural arm with and without the syntax sentence) rather than take
#: this reasoning on faith.
DECLARED_ASYMMETRIES = (
    "ast_search and grep each document their query syntax on the `pattern` "
    "argument. Regex is near-universal and ast-grep metavariables are not, so "
    "the structural arm may still gain more from its note than the lexical arm "
    "does from its own. The next experiment should ablate the note rather than "
    "take this reasoning on faith.",
    "definition/references require a starting location (repo, path, symbol); "
    "grep and ast_search require only a query. This is a property of precise "
    "navigation, not a handicap imposed by the harness.",
)


def check_ablation_declarations() -> list[str]:
    """Reasons an ablation arm is not a usable ablation of its control.

    Parity governs how the shared descriptions are written. This governs the
    ablation pairs: that each one names a control, that the control is an
    ordinary arm, that the pair differs in exactly one schema, and that it
    differs at all. A no-op ablation is the dangerous case, because it reports
    as an ablation and reads as a second sample of its control.
    """
    problems: list[str] = []
    for name, condition in CONDITIONS.items():
        if condition.description_ablation and name not in ABLATION_CONTROLS:
            problems.append(f"{name}: ablation arm names no control arm")
    for arm, control in ABLATION_CONTROLS.items():
        if arm not in CONDITIONS:
            problems.append(f"{arm}: ablation arm does not exist")
            continue
        if not CONDITIONS[arm].description_ablation:
            problems.append(f"{arm}: listed as an ablation but declares nothing")
        if control not in CONDITIONS:
            problems.append(f"{arm}: control arm {control!r} does not exist")
            continue
        if CONDITIONS[control].description_ablation:
            problems.append(f"{arm}: control arm {control!r} is itself an ablation")
        if CONDITIONS[arm].tool_names != CONDITIONS[control].tool_names:
            problems.append(
                f"{arm}: holds {CONDITIONS[arm].tool_names} against control "
                f"{CONDITIONS[control].tool_names}; the pair would differ in "
                "more than the description"
            )
            continue
        differing = [
            a["function"]["name"]
            for a, b in zip(CONDITIONS[arm].tools, CONDITIONS[control].tools)
            if a != b
        ]
        if not differing:
            problems.append(
                f"{arm}: identical to control {control!r}, so the ablation "
                "would record as a second sample of it"
            )
        elif len(differing) > 1:
            problems.append(f"{arm}: differs from {control!r} in {differing}")
    return problems
