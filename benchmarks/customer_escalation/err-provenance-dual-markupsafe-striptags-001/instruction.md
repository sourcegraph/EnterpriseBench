# Escalation: "plain-text" previews still contain raw HTML tags

## Reported issue

A content platform lets users submit rich text (HTML). To show a short plain-text
preview in listings and notification emails, the app renders the submitted body
through the templating library's built-in tag-removal filter, which is supposed to
strip all HTML tags and leave only the visible text:

    {{ body | <the built-in tag-removal filter> }}

For almost all content this works. But moderators have flagged previews where raw
tag text leaks straight into the "plain-text" output, for example a preview that
still literally contains:

    <a
       href="http://example.com">

instead of just the link's visible text.

The team pins down the trigger precisely:

- If the submitted HTML has an element whose **opening tag is written on a single
  line**, that tag is stripped correctly.
- If the submitted HTML has an element whose **opening tag is split across
  multiple lines** (a newline sits between the `<` and the closing `>`, which is
  perfectly valid HTML — common with many attributes or pasted from an editor),
  the tag is **not** removed and its raw text survives into the preview.

So two pieces of content that look identical when rendered can produce different
previews: the one with a multi-line tag leaks markup, the single-line one does
not. The app only calls the template filter; it never runs its own regex over the
body.

## Your task

1. Determine why a tag whose markup spans multiple lines is not stripped. The
   template filter is only the entry point: the actual tag removal is delegated to
   a lower-level string/markup-escaping library, whose tag-stripping routine
   compiles a small set of module-level regular expressions to recognize tags and
   comments. Identify the exact repository, file, and the regex compilation(s)
   where a tag is matched in a way that cannot cross a newline, so multi-line tags
   slip through.

2. Trace the full path across the repository boundary: the templating library's
   filter function that implements the tag-removal filter and the registry that
   exposes it to every template, and — in the escaping library — the method that
   performs the strip and the regexes it uses. Name the file and the
   function/class responsible at each hop, and explain why the defect lives in the
   escaping library rather than in the templating library whose filter the app
   invokes.

3. Fix the defect at its real origin so the tag-stripping regexes match a tag (and
   a comment) even when its markup contains newlines, so multi-line tags are
   removed exactly like single-line ones. Make the change in the workspace and
   leave it as an uncommitted diff; do not add a pre-processing step in the app or
   swap the template filter.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories.
- An uncommitted code change at the true origin that makes the strip regexes match
  tags across newlines.
