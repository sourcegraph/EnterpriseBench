# CVE Blast Radius Analysis: golang.org/x/text Accept-Language DoS

## Context

There is a known denial-of-service vulnerability in `golang.org/x/text` triggered
by parsing a crafted `Accept-Language` header, which can consume excessive CPU.
Not every project that pulls in `golang.org/x/text` is actually exposed — several
of its sub-packages have nothing to do with header parsing. I need you to look at
each workspace repo's own source to determine whether it actually exercises the
vulnerable code path, not just whether the module shows up in a manifest.

## What I Need

1. **CVE Identification**: look up the CVE ID, the affected module, and the
   vulnerable version range.

2. **Module Graph**: which workspace repos list golang.org/x/text in their go.mod?

3. **Function-Level Analysis**: this is the critical step. For each repo that
   imports `golang.org/x/text`, find the exact sub-package(s) and call sites it
   actually uses, and determine whether any of them exercise the vulnerable
   parsing path. Don't stop at "it's in go.mod" — cite the file(s) and the
   function(s) actually called.

4. **Version + Usage Classification**: combine the resolved/pinned version from
   each repo's own lockfile with the usage analysis above to classify each
   consumer as affected or not, and say why.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
