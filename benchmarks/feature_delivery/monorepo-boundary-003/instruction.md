# Impact Analysis: Decorator Metadata + TS Tuple Label Changes for v7.23.0

## Context

We have two features converging in the v7.23.0 release:

1. **Decorator metadata** — adds `Symbol.metadata` support to the decorator
   implementation.

2. **TS tuple label relaxation** — TypeScript now allows tuples with a mix of
   labeled and unlabeled elements (previously all-or-nothing). The grammar change
   is in `@babel/parser`'s TypeScript plugin.

These are independent features, but they're both landing in the same v7.23.0
release. I need a combined impact analysis.

Our release notes say the decorator work lands in
`@babel/helper-create-class-features-plugin` and cascades out through a
helper → plugin → preset chain. Do not take that as given: work out from the
checkout which packages each change actually touches, and cite the file that
establishes it. If a package the notes call affected turns out not to be, say so.

## What I Need

1. **Affected packages**: Which packages are affected by change (1), which by
   change (2), and are any hit by both?

2. **Impact classification**: Per package — none/patch/minor/major. State the
   bump for each affected package and say what the classification rests on.

3. **Boundary violations**: The specific files where these features cross
   package boundaries.

## Output

Write findings to `/workspace/agent_output/IMPACT_REPORT.md`.
