"""Serve doc-gen4 pages with review controls under each declaration.

Marks and note threads are saved to a JSON file, one entry per declaration name. With a declaration
graph from graph/DeclGraph.lean, marks are flagged when the declaration or anything it depends on
changes, renames recorded as deprecated aliases move marks automatically, and marks whose
declaration no longer exists are listed as orphans. Local use only: it binds to 127.0.0.1.

    python3 server/server.py --docs .lake/build/doc --marks review/marks.json \
        --graph review/graph.json --prefix MyProject
"""

import argparse
import json
import os
import re
import sys
import threading
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "0.1.0"
# Bump a format when old readers would misread the new one; see docs/internals.md.
# marks.json is {"__format__": 2, "marks": {name: entry}}, so that no declaration can collide with
# the format: `__format__` is a legal Lean name. Format 1 was the bare {name: entry}.
MARKS_FORMAT = 2
GRAPH_FORMAT = 1  # stored in the graph as "format"
FORMAT_KEY, MARKS_KEY = "__format__", "marks"

WEB = Path(__file__).resolve().parent.parent / "web"
STATUSES = {"agree", "question", "reject", None}
VERDICTS = {"match", "partly", "miss"}   # how a quiz answer compared with the real name and docstring
GENERATED = re.compile(r"\.inst(Repr|DecidableEq|BEq|Hashable|Inhabited|Nonempty)")  # as in web/review.js
LARGE = 5000  # declarations; above this, pages get the parts of the graph they need, not all of it
INJECT = (b'<link rel="stylesheet" href="/__review__/review.css"><script src="/__review__/links.js"></script>'
          b'<script src="/__review__/review.js"></script>')

# The tool's own URLs. doc-gen4 ends every module's page in .html, and a Lean module can have almost
# any name, so these must not end in .html: then none can be a module's page, even in a project with
# a module called __review__.
PAGES = {"/__review__/diagram": "graph.html", "/__review__/quiz": "quiz.html", "/__review__/review.js": "review.js",
         "/__review__/review.css": "review.css", "/__review__/links.js": "links.js", "/__review__/math.js": "math.js"}

lock = threading.Lock()
args = None
graph = None


def today():
    return date.today().isoformat()


class Graph:
    """The declaration graph, parsed once per version of the file, with what slices need."""

    def __init__(self, path):
        self.path, self.stamp, self.data, self.indexes = path, None, {}, None

    def current(self):
        stat = self.path.stat() if self.path and self.path.exists() else None
        stamp = stat and (stat.st_mtime_ns, stat.st_size)
        if stamp != self.stamp:
            self.data, self.stamp = load_json(self.path, {}), stamp
            self.indexes = None
        return self.data

    def index(self):
        """Declarations by module, direct users of each declaration, dependency counts between
        modules, and what each `sorry` reaches. Built on first use: it takes seconds for Mathlib, and
        small projects never need it."""
        graph = self.current()
        if self.indexes is None:
            decls = graph.get("declarations", {})
            by_module, users, edges, reached, hops = {}, {}, {}, {}, []
            for name, decl in decls.items():
                by_module.setdefault(decl["module"], []).append(name)
                for source in decl.get("sorryFrom", []):
                    reached.setdefault(source, []).append(name)
                hops.extend([name, decl["module"], hop, decls[hop]["module"]]
                            for hop in decl.get("sorryVia", []) if hop in decls)
                for dep in decl["deps"]:
                    users.setdefault(dep, []).append(name)
                    target = decls[dep]["module"] if dep in decls else None
                    if target and target != decl["module"]:
                        key = (decl["module"], target)
                        edges[key] = edges.get(key, 0) + 1
            self.indexes = by_module, users, edges, reached, hops
        return self.indexes

    def tree(self):
        """What the diagram draws folders from, however large the project is."""
        graph = self.current()
        by_module, _, edges, _, hops = self.index()
        decls = graph.get("declarations", {})
        gaps = {}   # module: [declarations containing `sorry`, others relying on one]
        for module, names in by_module.items():
            contains = sum("sorry" in decls[n] for n in names)
            relies = sum("sorry" not in decls[n] and "sorryFrom" in decls[n] for n in names)
            if contains or relies:
                gaps[module] = [contains, relies]
        return {"root": graph.get("root", ""), "lean": graph.get("lean"),
                "complete": len(graph.get("declarations", {})) <= LARGE,
                # Only what a reviewer can mark: `deriving` output isn't shown.
                "modules": {m: count for m, names in sorted(by_module.items())
                            if (count := sum(not GENERATED.search(n) for n in names))},
                "edges": [[a, b, n] for (a, b), n in sorted(edges.items())], "gaps": gaps,
                "sorryHops": hops}   # [from, its module, to, its module]: few, whatever the project's size

    def reach(self, modules, names, shown):
        """Everything the given modules or declarations depend on and everything that uses them,
        all the way down and up, not counting themselves: how much of it lies in each module, and
        by name for the `shown` modules, whose declarations are on screen. Dependencies follow
        statements, as everywhere, except that what relies on a `sorry` through a proof counts as
        using it, since that's the point of asking."""
        decls = self.current().get("declarations", {})
        by_module, users, _, reached, _ = self.index()
        seeds = {n for m in modules for n in by_module.get(m, [])} | {n for n in names if n in decls}
        shown = set(shown)

        def spread(step, start=()):
            seen, stack = set(), list(seeds)
            seen.update(n for n in start if n not in seeds)
            stack.extend(seen)
            while stack:
                for other in step(stack.pop()):
                    if other in decls and other not in seen and other not in seeds:
                        seen.add(other)
                        stack.append(other)
            counts = {}
            for name in seen:
                counts[decls[name]["module"]] = counts.get(decls[name]["module"], 0) + 1
            return {"modules": counts, "names": sorted(n for n in seen if decls[n]["module"] in shown)}

        return {"below": spread(lambda n: decls[n]["deps"] if n in decls else []),
                "above": spread(lambda n: users.get(n, []), [u for n in seeds for u in reached.get(n, [])])}

    def slice(self, modules, names, marks):
        """Enough of the graph to review `modules` and `names` and to check `marks`: them, everything
        below them, and whatever directly uses the modules' declarations. Same shape as the graph.
        A project of at most LARGE declarations gets the whole graph."""
        graph = self.current()
        decls, library = graph.get("declarations", {}), graph.get("library", {})
        if len(decls) <= LARGE:
            return {**graph, "complete": True}
        by_module, users, _, _, _ = self.index()
        own = [n for m in modules for n in by_module.get(m, [])]
        seeds = [n for n in [*own, *names, *marks] if n in decls]
        chosen, stack = set(), list(seeds)
        while stack:
            name = stack.pop()
            if name not in chosen:
                chosen.add(name)
                stack.extend(d for d in decls[name]["deps"] if d in decls and d not in chosen)
        chosen.update(u for n in own for u in users.get(n, []))
        wanted = {u for n in chosen for u in decls[n]["uses"]} | set(marks) | set(names)
        return {**{k: v for k, v in graph.items() if k not in ("declarations", "library", "modules")},
                "moduleNames": sorted(by_module),   # so a page can tell a removed module from an unloaded one
                "declarations": {n: decls[n] for n in chosen},
                "library": {n: e for n, e in library.items() if n in wanted or e.get("module") in modules}}


def load_json(path, default):
    # A file being written (the graph, while DeclGraph runs) reads as empty until it's done.
    try:
        return json.loads(path.read_text()) if path and path.exists() else default
    except ValueError:
        return default


class MarksError(Exception):
    """marks.json can't be used. Never treat it as empty: the next save would erase the review."""


def load_marks():
    if not args.marks.exists():
        return {}
    try:
        marks = json.loads(args.marks.read_text())
    except ValueError as error:
        raise MarksError(f"{args.marks} is not valid JSON ({error}); fix it before reviewing") from error
    if not isinstance(marks, dict):
        raise MarksError(f"{args.marks} is not a JSON object")
    # In format 1 this key, if present, was a version number or a declaration's mark.
    format = marks.get(FORMAT_KEY)
    if not isinstance(format, int):
        return marks   # format 1; the next save writes format 2
    if format == 1:
        del marks[FORMAT_KEY]
        return marks
    if format != MARKS_FORMAT or not isinstance(marks.get(MARKS_KEY), dict):
        raise MarksError(f"{args.marks} has format {format}; this lean-review reads format {MARKS_FORMAT}. "
                         "Use a matching version.")
    return marks[MARKS_KEY]


def save_marks(marks):
    args.marks.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({FORMAT_KEY: MARKS_FORMAT, MARKS_KEY: marks}, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    # Write beside the file, then swap, so a crash can't leave half a review.
    temporary = args.marks.with_name(args.marks.name + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, args.marks)


def apply_renames(marks, graph):
    """Move marks from an old name to its new one, for renames recorded as deprecated aliases."""
    moved = False
    for old, new in graph.get("renames", {}).items():
        if old in marks and new not in marks:
            entry = marks.pop(old)
            entry.setdefault("notes", []).append(
                {"by": "lean-review", "date": today(), "text": f"Moved automatically: {old} was renamed to {new}."})
            marks[new] = entry
            moved = True
    return moved


def source_of(name):
    """A declaration's source text, with the doc comment just above it, from the graph's line
    numbers and the project's files."""
    decl = graph.current().get("declarations", {}).get(name)
    if not decl or not decl.get("lines") or not args.source:
        return {"error": "no source"}
    file = args.source / (decl["module"].replace(".", "/") + ".lean")
    if not file.exists():
        return {"error": f"{file} not found"}
    lines = file.read_text().splitlines()
    start, end = decl["lines"][0] - 1, decl["lines"][1]
    # Include a doc comment (and attributes) directly above the declaration.
    if start > 0 and lines[start - 1].rstrip().endswith("-/"):
        i = start - 1
        while i > 0 and "/--" not in lines[i]:
            i -= 1
        start = i
    while start > 0 and lines[start - 1].lstrip().startswith("@["):
        start -= 1
    return {"file": str(file.relative_to(args.source)), "start": start + 1, "end": end,
            "text": "\n".join(lines[start:end]), "kind": decl.get("kind")}


def current_marks():
    marks = load_marks()
    if apply_renames(marks, graph.current()):
        save_marks(marks)
    return marks


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(args.docs), **kw)

    def send_body(self, body, kind, code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, code=200):
        self.send_body(json.dumps(value, ensure_ascii=False).encode(), "application/json", code)

    def from_this_server(self):
        """Whether a request came from these pages. Binding to 127.0.0.1 doesn't stop a site open in
        the same browser from reaching the server, by a form or fetch, or by pointing its own domain
        at 127.0.0.1. A browser can't forge these headers."""
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (origin is None or origin in {f"http://{h}" for h in hosts})

    def do_GET(self):
        if not self.from_this_server():
            return self.send_json({"error": "request from another site"}, 403)
        path = self.path.split("?")[0]
        if path == "/__review__/data":
            with lock:
                try:
                    return self.send_json(current_marks())
                except MarksError as error:
                    return self.send_json({"error": str(error)}, 500)
        if path == "/__review__/tree":
            with lock:
                return self.send_json(graph.tree())
        if path == "/__review__/graph":
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if not query:
                return self.send_json(graph.current())
            with lock:
                marks = load_marks() if "marked" in query else {}
                return self.send_json(graph.slice(query.get("module", []), query.get("name", []), marks))
        if path == "/__review__/links":
            return self.send_json(load_json(args.links, {}))
        if path == "/__review__/source":
            name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
            return self.send_json(source_of(name))
        if path in PAGES:
            name = PAGES[path]
            kind = {"js": "text/javascript", "css": "text/css", "html": "text/html; charset=utf-8"}[name.rsplit(".", 1)[1]]
            return self.send_body((WEB / name).read_bytes(), kind)
        # Every page gets the script: project pages for review, library pages so the library
        # definitions the project uses can be marked as understood.
        if path.endswith(".html") or path == "/":
            file = args.docs / ("index.html" if path == "/" else path.lstrip("/"))
            if file.exists():
                return self.send_body(file.read_bytes().replace(b"</body>", INJECT + b"</body>", 1),
                                      "text/html; charset=utf-8")
        return super().do_GET()

    def do_POST(self):
        if not self.from_this_server():
            return self.send_json({"error": "request from another site"}, 403)
        # Another site can't send this type without asking first, and the server never agrees.
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "expected JSON"}, 415)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/__review__/reach":   # a POST, since a folder can hold thousands of modules
            with lock:
                return self.send_json(graph.reach(body.get("modules", []), body.get("names", []), body.get("shown", [])))
        with lock:
            try:
                marks = current_marks()
            except MarksError as error:
                return self.send_json({"error": str(error)}, 500)
            if self.path == "/__review__/verdict":
                quizzed = [n for n in marks.get(body.get("name"), {}).get("notes", []) if "quiz" in n]
                if not quizzed or body.get("verdict") not in VERDICTS:
                    return self.send_json({"error": "bad request"}, 400)
                quizzed[-1]["quiz"]["verdict"] = body["verdict"]
                save_marks(marks)
                return self.send_json(marks[body["name"]])
            if self.path == "/__review__/data":
                name = body.get("name")
                if not isinstance(name, str) or body.get("status") not in STATUSES:
                    return self.send_json({"error": "bad request"}, 400)
                entry = marks.setdefault(name, {"status": None, "notes": []})
                if "status" in body:
                    entry["status"] = body["status"]
                    # A graph fingerprint replaces the older text signature; keep whichever was sent.
                    if body.get("fingerprint"):
                        entry["fingerprint"] = body["fingerprint"]
                        entry.pop("signature", None)
                    elif body.get("signature"):
                        entry["signature"] = body["signature"]
                    entry["date"] = today()
                if body.get("note"):
                    note = {"by": args.reviewer, "date": today(), "text": body["note"]}
                    # A quiz answer also keeps the name and description the reviewer wrote blind.
                    quiz = body.get("quiz")
                    if isinstance(quiz, dict):
                        note["quiz"] = {"name": str(quiz.get("name", "")),
                                        "description": str(quiz.get("description", ""))}
                    entry["notes"].append(note)
                save_marks(marks)
                return self.send_json(entry)
            if self.path == "/__review__/move":
                old, new = body.get("from"), body.get("to")
                if old not in marks or not new or new in marks:
                    return self.send_json({"error": "cannot move"}, 400)
                entry = marks.pop(old)
                entry["notes"].append({"by": args.reviewer, "date": today(), "text": f"Moved from {old}."})
                marks[new] = entry
                save_marks(marks)
                return self.send_json(entry)
            if self.path == "/__review__/delete":
                if marks.pop(body.get("name"), None) is None:
                    return self.send_json({"error": "no such mark"}, 400)
                save_marks(marks)
                return self.send_json({})
        return self.send_json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


def main():
    global args, graph
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--docs", type=Path, required=True, help="doc-gen4 output folder")
    p.add_argument("--marks", type=Path, required=True, help="JSON file for marks and notes")
    p.add_argument("--graph", type=Path, help="declaration graph from graph/DeclGraph.lean")
    p.add_argument("--prefix", required=True, help="root module, e.g. MyProject")
    p.add_argument("--source", type=Path, help="project root, for showing declarations' code")
    p.add_argument("--reviewer", default="reviewer", help="name shown on notes added in the page")
    p.add_argument("--links", type=Path,
                   help="JSON file linking references to your own copies (default: links.local.json beside the marks)")
    p.add_argument("--port", type=int, default=8797)
    p.add_argument("--version", action="version", version=f"lean-review {VERSION}")
    args = p.parse_args()
    args.links = args.links or args.marks.parent / "links.local.json"
    try:
        load_marks()
    except MarksError as error:
        sys.exit(f"lean-review: {error}")
    graph = Graph(args.graph)
    graph_format = graph.current().get("format", GRAPH_FORMAT)
    if graph_format != GRAPH_FORMAT:
        sys.exit(f"lean-review: {args.graph} has format {graph_format}; this server reads format "
                 f"{GRAPH_FORMAT}. Rerun this version's graph/DeclGraph.lean.")
    print(f"lean-review {VERSION}: http://127.0.0.1:{args.port}/__review__/diagram\n"
          f"Docs: http://127.0.0.1:{args.port}/{args.prefix.replace('.', '/')}.html", flush=True)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        sys.exit(f"lean-review: can't use port {args.port} ({error.strerror}). Choose another with --port.")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
