"""Render the run browser: one self-contained HTML file, no external requests.

The page ships in the repository, so it cannot fetch its data at load time.
Substitution is @@NAME@@ rather than str.format because the template is mostly
CSS and JavaScript, and every brace in it would otherwise need escaping.

The template holds body content only. A file committed to the repository needs
a real document around it; a hosted publish supplies its own. --body-only picks
the second, so the two outputs cannot drift apart.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from collect import collect  # noqa: E402

TEMPLATE = os.path.join(HERE, "template.html")
DEFAULT_OUT = os.path.join(HERE, "index.html")

DOCUMENT = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '{head}</head>\n<body>\n{body}</body>\n</html>\n')


def _wrap(page):
    """Lift <title> and <style> into a real <head>, leave the rest in <body>."""
    head, body, rest = [], [], page
    for tag in ("title", "style"):
        open_tag, close_tag = f"<{tag}", f"</{tag}>"
        while open_tag in rest:
            start = rest.index(open_tag)
            end = rest.index(close_tag, start) + len(close_tag)
            head.append(rest[start:end])
            rest = rest[:start] + rest[end:]
    body.append(rest.strip())
    return DOCUMENT.format(head="\n".join(head) + "\n", body="\n".join(body) + "\n")


def build(runs_root, out_path, body_only=False):
    runs = collect(runs_root)
    if not runs:
        raise SystemExit(f"no scored runs under {runs_root}")
    with open(TEMPLATE) as fh:
        page = fh.read()
    fields = {
        "DATA": json.dumps(runs, separators=(",", ":")),
        "N_RUNS": f"{len(runs):,}",
        "N_TASKS": str(len({r['task'] for r in runs})),
        "N_MODES": str(len({r['mode'] for r in runs})),
    }
    for name, value in fields.items():
        page = page.replace(f"@@{name}@@", value)
    if "@@" in page:
        leftover = page[page.index("@@"):page.index("@@") + 40]
        raise SystemExit(f"unfilled placeholder in template: {leftover!r}")
    if not body_only:
        page = _wrap(page)
    with open(out_path, "w") as fh:
        fh.write(page)
    return runs, out_path


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--body-only"]
    body_only = "--body-only" in sys.argv
    root = argv[0] if argv else os.path.join(
        os.path.dirname(os.path.dirname(HERE)), "results/runs")
    out = argv[1] if len(argv) > 1 else DEFAULT_OUT
    rows, path = build(root, out, body_only=body_only)
    print(f"wrote {path}  ({os.path.getsize(path):,} bytes, {len(rows):,} runs)")
