#!/usr/bin/env python3
"""Check that a project's graph and docs list the same declarations. From a project set up by
review.py: python3 path/to/check_docs_match.py"""
import html
import json
import re
import sys
from pathlib import Path

graph = json.loads(Path(".lean-review/graph.json").read_text())
docs = Path(".lean-review/docbuild/.lake/build/doc")
folders = [docs / root.replace(".", "/") for root in graph["roots"]]
pages = [page for folder in folders for page in (folder.with_name(folder.name + ".html"), *folder.glob("**/*.html"))]
documented = {html.unescape(name) for page in pages
              for name in re.findall(r'class="decl[^"]*" id="([^"]+)"', page.read_text())}
listed = set(graph["declarations"]) | set(graph["renames"])
print(f"Lean {graph['lean']}: {len(listed)} in the graph, {len(documented)} in the docs")
if listed != documented:
    sys.exit(f"only in the graph: {sorted(listed - documented)[:20]}\nonly in the docs: {sorted(documented - listed)[:20]}")
