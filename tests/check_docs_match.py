#!/usr/bin/env python3
"""Check that a project's graph and docs list the same declarations. From a project set up by
review.py: python3 path/to/check_docs_match.py MyProject"""
import html
import json
import re
import sys
from pathlib import Path

root = sys.argv[1]
graph = json.loads(Path("review/graph.json").read_text())
docs = Path("docbuild/.lake/build/doc")
pages = [docs / f"{root}.html", *(docs / root).glob("**/*.html")]
documented = {html.unescape(name) for page in pages
              for name in re.findall(r'class="decl[^"]*" id="([^"]+)"', page.read_text())}
listed = set(graph["declarations"]) | set(graph["renames"])
print(f"Lean {graph['lean']}: {len(listed)} in the graph, {len(documented)} in the docs")
if listed != documented:
    sys.exit(f"only in the graph: {sorted(listed - documented)[:20]}\nonly in the docs: {sorted(documented - listed)[:20]}")
