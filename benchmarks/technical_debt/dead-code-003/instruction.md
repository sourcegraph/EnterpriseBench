# Dead Code Necropsy: React Compiler Pipeline

## Context

The React Compiler (`babel-plugin-react-compiler`) is a compilation pipeline that transforms React components. Several features were recently removed (`retryCompileFunction`, `enableFire`, `inferEffectDependencies`), but dead code was left behind. Your task is to find that dead code.

## What to Look For

1. **Dead validation passes** — passes that are always no-ops after feature removal
2. **Dead types** — type definitions that are no longer used anywhere
3. **Dead output modes** — compilation output modes that are no longer reachable
4. **Overly complex return types** — functions returning union types where one branch is now impossible
5. **Dead imports** — imports of removed features

## Output

Write your findings to `/workspace/agent_output/dead_code_report.json` as a JSON array where each entry has:
- `file`: relative path from repo root
- `symbol`: function/type/export name
- `kind`: one of `function`, `class`, `method`, `file`, `export`, `type`
- `confidence`: `high`, `medium`, or `low`
- `evidence`: brief explanation of why this code is dead

## Scoring

- **Precision matters most**: incorrectly flagging live code as dead is heavily penalized
- Must identify the dead validation pass by name
