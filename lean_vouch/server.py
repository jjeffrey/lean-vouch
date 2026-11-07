"""Serve doc-gen4 pages with review controls under each declaration.

Each person's marks and notes are saved to a file of their own in a folder, and their quiz answers to
one in another, so that what's shared and what isn't can be committed separately. Pages get one view:
the marks of whoever runs the server, everyone's notes, and everyone else's marks beside them. With
a declaration graph from DeclGraph.lean, marks are flagged when the declaration or anything it
depends on changes, renames recorded as deprecated aliases move marks automatically, and marks whose
declaration no longer exists are listed as orphans. Local use only: it binds to 127.0.0.1.

    python3 lean_vouch/server.py --docs .lean-vouch/docbuild/.lake/build/doc --marks .lean-vouch/marks \
        --graph .lean-vouch/graph.json --prefix MyProject --id ada --name Ada
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "0.1.0"
# Bump a format when old readers would misread the new one; see docs/internals.md.
# A person's file is {"format": 3, "name": display name, "marks": {declaration: entry}}, and their
# quiz file {"format": 3, "answers": {declaration: [answer]}}. The file's name is their id.
MARKS_FORMAT = 3
GRAPH_FORMAT = 1  # stored in the graph as "format"

WEB = Path(__file__).resolve().parent / "web"
STATUSES = {"agree", "question", "reject", None}
ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")   # plain, so that a file can't differ between machines
VERDICTS = {"match", "partly", "miss"}   # how a quiz answer compared with the real name and docstring
LARGE = 5000  # declarations; above this, pages get the parts of the graph they need, not all of it
INJECT = (b'<link rel="stylesheet" href="/review/review.css"><script src="/review/links.js"></script>'
          b'<script src="/review/review.js"></script>')

# The tool's own URLs. doc-gen4 ends every module's page in .html, and a Lean module can have almost
# any name, so these must not end in .html: then none can be a module's page, even in a project with
# a module called review.
PAGES = {"/review/diagram": "graph.html", "/review/quiz": "quiz.html", "/review/review.js": "review.js",
         "/review/review.css": "review.css", "/review/links.js": "links.js", "/review/math.js": "math.js"}

lock = threading.Lock()
args = None
graph = None
unreadable = []  # others' marks files that the latest view had to leave out


def now():
    """In UTC, so that times from different machines sort, and compare, correctly."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    def fingerprint(self, name):
        """What a mark records: `name` and everything below it, as they are now. Worked out here, so
        a mark is right however it was made, and whatever part of the graph a page has."""
        graph = self.current()
        decls, library = graph.get("declarations", {}), graph.get("library", {})
        lean = {"lean": graph["lean"]} if graph.get("lean") else {}
        if name.startswith("module "):
            own = graph.get("moduleDocs", {}).get(name[len("module "):])
            return {"deps": {}, **({"own": own} if own else {}), **lean}
        if name not in decls and name not in library:
            return None
        own = lambda n: (decls.get(n) or library.get(n) or {}).get("own")
        below, stack = set(), [name]
        while stack:
            decl = decls.get(stack.pop())
            for dep in [*decl["deps"], *decl["uses"]] if decl else []:
                if dep not in below:
                    below.add(dep)
                    stack.append(dep)
        return {"own": own(name), "deps": {dep: own(dep) for dep in below}, **lean}

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
                            if (count := sum(not decls[n].get("derived") for n in names))},
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
                "moduleNames": sorted(graph.get("modules") or by_module),   # so a page can tell a removed module from an unloaded one
                "declarations": {n: decls[n] for n in chosen},
                "library": {n: e for n, e in library.items() if n in wanted or e.get("module") in modules}}


def load_json(path, default):
    # A file being written (the graph, while DeclGraph runs) reads as empty until it's done.
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else default
    except ValueError:
        return default


class MarksError(Exception):
    """A marks file can't be used. Never treat it as empty: the next save would erase the review."""


def load_file(path, key):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise MarksError(f"{path} is not valid JSON ({error}); fix it before reviewing") from error
    if not isinstance(data, dict):
        raise MarksError(f"{path} is not a JSON object")
    if data.get("format") != MARKS_FORMAT or not isinstance(data.get(key), dict):
        raise MarksError(f"{path} has format {data.get('format')}; this lean-vouch reads format {MARKS_FORMAT}. "
                         "Use a matching version.")
    return data


def save_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"format": MARKS_FORMAT, **data}, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    # Write beside the file, then swap, so a crash can't leave half a review.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def is_note(note):
    return isinstance(note, dict) and isinstance(note.get("date"), str) and isinstance(note.get("text"), str)


def is_entry(entry):
    return (isinstance(entry, dict) and isinstance(entry.get("date", ""), str)
            and isinstance(entry.get("status"), (str, type(None))) and entry.get("status") in STATUSES
            and isinstance(entry.get("fingerprint") or {}, dict)
            and isinstance(entry.get("notes", []), list) and all(is_note(note) for note in entry.get("notes", [])))


def person(who, name=None):
    path = args.marks / f"{who}.json"
    data = load_file(path, "marks")
    for declaration, entry in data.get("marks", {}).items():
        if not is_entry(entry):
            raise MarksError(f"{path} has a malformed entry for {declaration}")
    return {"name": name or data.get("name") or who, "marks": data.get("marks", {})}


def save_person(who, data):
    save_file(args.marks / f"{who}.json", data)


def quiz_answers():
    path = args.quiz / f"{args.id}.json"
    answers = load_file(path, "answers").get("answers", {})
    if not all(isinstance(kept, list) and all(is_note(answer) and isinstance(answer.get("quiz"), dict) for answer in kept)
               for kept in answers.values()):
        raise MarksError(f"{path} has a malformed answer")
    return answers


def save_quiz(answers):
    save_file(args.quiz / f"{args.id}.json", {"answers": answers})


def followed(renames, name):
    """Where a name ended up, through any chain of renames."""
    seen = {name}
    while name in renames and renames[name] not in seen:
        name = renames[name]
        seen.add(name)
    return name


def apply_renames(marks, graph):
    """Move marks from an old name to its new one, for renames recorded as deprecated aliases."""
    moved = False
    renames = graph.get("renames", {})
    for old in renames:
        new = followed(renames, old)
        if old in marks and new not in marks:
            entry = marks.pop(old)
            entry.setdefault("notes", []).append(
                {"by": "lean-vouch", "date": now(), "text": f"Moved automatically: {old} was renamed to {new}."})
            marks[new] = entry
            moved = True
    return moved


def current(who=None, name=None):
    """A person's marks, with any renames applied."""
    who = who or args.id
    data = person(who, name or (args.name if who == args.id else None))
    if apply_renames(data["marks"], graph.current()):
        save_person(who, data)
    if who == args.id:
        answers, renames, moved = quiz_answers(), graph.current().get("renames", {}), False
        for old in renames:   # one at a time, so that a second old name can't overwrite the first
            new = followed(renames, old)
            if old in answers and new not in answers:
                answers[new] = answers.pop(old)
                moved = True
        if moved:
            save_quiz(answers)
    return data


def view():
    """What pages read: for each declaration, your mark as its status, date and fingerprint, everyone's
    notes in order with your quiz answers among them, and what others marked under `others`. Others'
    files are only read, so a declaration they marked before a rename shows under its new name.
    `attention` names whoever is waiting on you: someone with a `?` and a note saying what it asks,
    or who answered your `?`, since you last marked or wrote here. A bare `?` is a person's own reminder."""
    renames = graph.current().get("renames", {})
    # Your own file has to be right, or a save could lose it. Someone else's only gets read here,
    # and arrives through git, so a conflict or a newer version of theirs needn't stop you.
    people, unreadable[:] = {args.id: current()}, []
    for path in sorted(args.marks.glob("*.json")):
        if path.stem != args.id:
            try:
                people[path.stem] = person(path.stem)
            except MarksError as error:
                unreadable.append(path.stem)
                print(f"lean-vouch: leaving out {error}", file=sys.stderr, flush=True)
    shown = {}

    def at(name):
        return shown.setdefault(name, {"status": None, "notes": [], "others": []})

    for who, data in people.items():
        mine = who == args.id
        for name, entry in data["marks"].items():
            item = at(name if mine else followed(renames, name))
            item["notes"] += [{"by": data["name"], **note, "who": who, "mine": mine} for note in entry.get("notes", [])]
            if mine:
                item.update({key: value for key, value in entry.items() if key != "notes"})
            elif entry.get("status"):
                item["others"].append({"id": who, "name": data["name"], **{
                    key: entry[key] for key in ("status", "date", "fingerprint") if key in entry}})
    for name, answers in quiz_answers().items():
        at(name)["notes"] += [{"by": people[args.id]["name"], **answer, "who": args.id, "mine": True} for answer in answers]
    for item in shown.values():
        item["notes"].sort(key=lambda note: note["date"])
        seen = max([item.get("date", ""), *(n["date"] for n in item["notes"]
                                            if n["mine"] and "quiz" not in n and n["by"] != "lean-vouch")])
        new = [note for note in item["notes"] if note["date"] > seen and not note["mine"] and note["by"] != "lean-vouch"]
        wrote = {note["who"] for note in item["notes"] if note["by"] != "lean-vouch"}
        asking = [other for other in item["others"] if other["status"] == "question" and other["id"] in wrote]
        waiting = {other["name"] for other in asking if other.get("date", "") > seen}
        waiting |= {note["by"] for note in new if item["status"] == "question" or note["who"] in {o["id"] for o in asking}}
        item["attention"] = sorted(waiting)
    return shown


def source_of(name):
    """A declaration's source text, with the doc comment just above it, from the graph's line
    numbers and the project's files."""
    decl = graph.current().get("declarations", {}).get(name)
    if not decl or not decl.get("lines") or not args.source:
        return {"error": "no source"}
    module = Path(decl["module"].replace(".", "/") + ".lean")
    folder = next((folder for folder in args.source if (folder / module).exists()), None)
    if folder is None:
        return {"error": f"{module} not found"}
    # Not splitlines(): Lean counts only \n, and that also splits on form feeds and the like.
    lines = (folder / module).read_text(encoding="utf-8").split("\n")
    start, end = decl["lines"][0] - 1, decl["lines"][1]
    if end > len(lines):
        return {"error": f"{module} has changed since the last build. Rebuild to see this declaration's code."}
    # Lines come from the last build, so an edit since then can put them in the wrong place.
    edited = args.graph and args.graph.exists() and (folder / module).stat().st_mtime > args.graph.stat().st_mtime
    return {"file": str(module), "start": start + 1, "end": end, "edited": bool(edited),
            "text": "\n".join(lines[start:end]), "kind": decl.get("kind")}


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def project():
    """The Lake project this server is for, when lean-vouch started it: then pages can rebuild it."""
    home = args.marks.resolve().parent
    return home.parent if home.name == ".lean-vouch" else None


rebuilding = None   # a rebuild started from a page, while it runs


def building():
    """Whether a build is running, whoever started it, and what its file says."""
    global rebuilding
    build = load_json(args.build, {})
    build = build if isinstance(build, dict) else {}
    if rebuilding and rebuilding.poll() is not None:
        rebuilding = None
    return bool(rebuilding) or (build.get("state") == "building" and alive(build.get("pid"))), build


newest = {"checked": 0, "time": 0}


def newest_change():
    """When the project's code last changed: its modules, its Lake configuration and its pins. At most
    every ten seconds, since a large project has thousands of files. By the time, so saving a file
    unchanged counts too, but a rebuild that changes nothing takes seconds."""
    if time.monotonic() - newest["checked"] < 10:
        return newest["time"]
    times = []
    def walk(folder):
        try:
            for entry in os.scandir(folder):
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                    walk(entry.path)
                elif entry.name.endswith(".lean") and entry.is_file():
                    times.append(entry.stat().st_mtime)
        except OSError:
            pass
    for folder in args.source or []:
        walk(folder)
    if root := project():
        for name in ("lakefile.lean", "lakefile.toml", "lake-manifest.json", "lean-toolchain"):
            if (root / name).exists():
                times.append((root / name).stat().st_mtime)
    newest.update(checked=time.monotonic(), time=max(times, default=0))
    return newest["time"]


def status():
    """What open pages poll: when anyone's marks last changed, and how the latest build went, which
    a build reports in a file. A build that died can't say so itself. `stale` is whether the code
    changed after the last good build started. `marks_folder` tells lean-vouch which project this
    server is for, and `me` shows who's reviewing."""
    files = [path for folder in (args.marks, args.quiz) for path in sorted(folder.glob("*.json"))]
    running, build = building()
    state, message = build.get("state"), build.get("message")
    if running:
        state = "building"
    elif state == "building":
        state, message = "failed", "The build stopped before it finished."
    since = build.get("since")
    stale = state != "building" and isinstance(since, (int, float)) and newest_change() > since
    progress = {key: build.get(key) for key in ("step", "step_since", "done", "total")} if state == "building" else {}
    return {"marks": ",".join(f"{path.stem}:{path.stat().st_mtime_ns}" for path in files), "state": state,
            "message": message, "built": build.get("built"), "stale": stale, "can_rebuild": project() is not None,
            **progress, "marks_folder": str(args.marks.resolve()),
            "me": {"id": args.id, "name": args.name or args.id}, "unreadable": unreadable}


def rebuild():
    """Start `lean-vouch --rebuild` for the project, as the last run reviewed it. Its output goes where
    this server's does, and to the build log."""
    global rebuilding
    running, build = building()
    if running:
        return "A build is already running."
    names = build.get("names", [])
    names = names if isinstance(names, list) and all(isinstance(n, str) and not n.startswith("-") for n in names) else []
    package = Path(__file__).resolve().parent.parent   # where lean_vouch is, installed or not
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(package), os.environ.get("PYTHONPATH")]))}
    rebuilding = subprocess.Popen([sys.executable, "-c", "from lean_vouch.cli import main; main()", "--rebuild", *names],
                                  cwd=project(), env=env, stdin=subprocess.DEVNULL)
    return None


def last_built():
    build = load_json(args.build, {})
    return build.get("built") if isinstance(build, dict) else None


def stamped(page):
    """A page, saying which build it's from. The page's first look at the status can't say: a build
    may finish while a large page loads."""
    links = b'<script src="/review/links.js">'
    stamp = json.dumps(last_built()).replace("<", "\\u003c")
    return page.replace(links, f"<script>window.reviewBuilt = {stamp};</script>".encode() + links, 1)


def well_formed(body):
    """Whether a POST has the types the handlers rely on; they decide whether its values make sense."""
    return (isinstance(body, dict)
            and all(isinstance(body.get(key, ""), str) for key in ("name", "from", "to", "verdict", "note", "id", "author", "built"))
            and isinstance(body.get("status"), (str, type(None)))
            and all(isinstance(body.get(key, []), list) and all(isinstance(item, str) for item in body.get(key, []))
                    for key in ("modules", "names", "shown")))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(args.docs), **kw)

    def send_body(self, body, kind, code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")   # no clicks through another site's frame
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
        # A link from elsewhere is fine; a page elsewhere loading this as a resource isn't.
        foreign = self.headers.get("Sec-Fetch-Site") == "cross-site" and self.headers.get("Sec-Fetch-Mode") != "navigate"
        return self.headers.get("Host") in hosts and not foreign and (origin is None or origin in {f"http://{h}" for h in hosts})

    def do_HEAD(self):
        if not self.from_this_server():
            return self.send_error(403)
        super().do_HEAD()

    def do_GET(self):
        if not self.from_this_server():
            return self.send_json({"error": "request from another site"}, 403)
        path = self.path.split("?")[0]
        if path == "/review/status":
            return self.send_json(status())
        if path == "/review/data":
            with lock:
                try:
                    return self.send_json(view())
                except MarksError as error:
                    return self.send_json({"error": str(error)}, 500)
        if path == "/review/tree":
            with lock:
                return self.send_json(graph.tree())
        if path == "/review/graph":
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if not query:
                return self.send_json(graph.current())
            with lock:
                try:
                    marks = view() if "marked" in query else {}
                except MarksError as error:
                    return self.send_json({"error": str(error)}, 500)
                return self.send_json(graph.slice(query.get("module", []), query.get("name", []), marks))
        if path == "/review/links":
            return self.send_json(load_json(args.links, {}))
        if path == "/review/log":
            log = args.build.with_name("build.log")
            text = log.read_bytes()[-500_000:] if log.exists() else b"No build has been logged yet."
            return self.send_body(text, "text/plain; charset=utf-8")
        if path == "/review/source":
            name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
            return self.send_json(source_of(name))
        if path in PAGES:
            name = PAGES[path]
            kind = {"js": "text/javascript", "css": "text/css", "html": "text/html; charset=utf-8"}[name.rsplit(".", 1)[1]]
            return self.send_body(stamped((WEB / name).read_bytes()), kind)
        # Every page gets the script: project pages for review, library pages so the library
        # definitions the project uses can be marked as understood.
        # translate_path decodes the URL, since a module's name needn't be ASCII, and stays in the docs.
        file = Path(self.translate_path(self.path))
        file = file / "index.html" if path == "/" else file
        if file.suffix == ".html" and file.is_file():
            return self.send_body(stamped(file.read_bytes().replace(b"</body>", INJECT + b"</body>", 1)),
                                  "text/html; charset=utf-8")
        return super().do_GET()

    def do_POST(self):
        if not self.from_this_server():
            return self.send_json({"error": "request from another site"}, 403)
        # Another site can't send this type without asking first, and the server never agrees.
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "expected JSON"}, 415)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", ""))))
        except ValueError:
            return self.send_json({"error": "bad request"}, 400)
        if not well_formed(body):
            return self.send_json({"error": "bad request"}, 400)
        if self.path == "/review/reach":   # a POST, since a folder can hold thousands of modules
            with lock:
                return self.send_json(graph.reach(body.get("modules", []), body.get("names", []), body.get("shown", [])))
        if self.path == "/review/rebuild":
            if project() is None:
                return self.send_json({"error": "This server can't rebuild: run lean-vouch from the project."}, 409)
            with lock:
                refused = rebuild()
            return self.send_json({"error": refused}, 409) if refused else self.send_json({"started": True})
        with lock:
            try:
                return self.change(body)
            except MarksError as error:
                return self.send_json({"error": str(error)}, 500)

    def change(self, body):
        # Anyone local can write as themselves: an assistant has no server of its own.
        who = body.get("id", args.id)
        if not ID.fullmatch(who):
            return self.send_json({"error": "an id is lowercase letters, digits and hyphens"}, 400)
        data = current(who, body.get("author"))
        marks = data["marks"]
        answers = quiz_answers() if who == args.id else {}
        renames = graph.current().get("renames", {})
        name = followed(renames, body.get("name"))
        if self.path == "/review/verdict":
            if not answers.get(name) or body.get("verdict") not in VERDICTS:
                return self.send_json({"error": "bad request"}, 400)
            answers[name][-1]["quiz"]["verdict"] = body["verdict"]
            save_quiz(answers)
            return self.send_json(view()[name])
        if self.path == "/review/data":
            if not isinstance(name, str) or body.get("status") not in STATUSES:
                return self.send_json({"error": "bad request"}, 400)
            if isinstance(body.get("quiz"), dict) and body.get("note") and who == args.id:
                quiz = body["quiz"]
                answers.setdefault(name, []).append({"date": now(), "text": body["note"], "quiz": {
                    "name": str(quiz.get("name", "")), "description": str(quiz.get("description", ""))}})
                save_quiz(answers)
                return self.send_json(view()[name])
            # A page from before a rebuild would mark what's there now without having shown it.
            if "status" in body and body.get("built", last_built()) != last_built():
                return self.send_json({"error": "The docs have been rebuilt. Reload this page, then mark again."}, 409)
            entry = marks.setdefault(name, {"status": None, "notes": []})
            if "status" in body:
                entry["status"] = body["status"]
                if fingerprint := graph.fingerprint(name):
                    entry["fingerprint"] = fingerprint
                entry["date"] = now()
            if body.get("note"):
                entry["notes"].append({"date": now(), "text": body["note"]})
            save_person(who, data)
            return self.send_json(view()[name])
        if self.path == "/review/move":
            old, new = body.get("from"), followed(renames, body.get("to"))
            if (old not in marks and old not in answers) or not new or new in marks or new in answers:
                return self.send_json({"error": "cannot move"}, 400)
            if old in marks:
                entry = marks.pop(old)
                entry["notes"].append({"by": "lean-vouch", "date": now(), "text": f"Moved from {old}."})
                marks[new] = entry
                save_person(who, data)
            if old in answers:
                answers[new] = answers.pop(old)
                save_quiz(answers)
            return self.send_json(view()[new])
        if self.path == "/review/delete":
            # An orphan by its own name, even if that was renamed to something marked already.
            name = next((n for n in (body.get("name"), name) if n in marks or n in answers), None)
            if name is None:
                return self.send_json({"error": "no such mark"}, 400)
            if marks.pop(name, None) is not None:
                save_person(who, data)
            if answers.pop(name, None) is not None:
                save_quiz(answers)
            return self.send_json({})
        return self.send_json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


def main():
    global args, graph
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--docs", type=Path, required=True, help="doc-gen4 output folder")
    p.add_argument("--marks", type=Path, required=True, help="folder for each person's marks and notes")
    p.add_argument("--quiz", type=Path, help="folder for quiz answers (default: quiz beside the marks)")
    p.add_argument("--id", required=True, help="who this is: the name of their file, which never changes")
    p.add_argument("--name", help="the name to show for them (default: the id)")
    p.add_argument("--graph", type=Path, help="declaration graph from DeclGraph.lean")
    p.add_argument("--prefix", required=True, help="root module, e.g. MyProject")
    p.add_argument("--source", type=Path, action="append",
                   help="a folder holding the project's modules, for showing declarations' code; can be repeated")
    p.add_argument("--build", type=Path, help="file where builds report their state (default: build.json beside the marks)")
    p.add_argument("--links", type=Path,
                   help="JSON file linking references to your own copies (default: links.local.json beside the marks)")
    p.add_argument("--port", type=int, default=8797)
    p.add_argument("--version", action="version", version=f"lean-vouch {VERSION}")
    args = p.parse_args()
    args.links = args.links or args.marks.parent / "links.local.json"
    args.quiz = args.quiz or args.marks.parent / "quiz"
    args.build = args.build or args.marks.parent / "build.json"
    if not ID.fullmatch(args.id):
        sys.exit(f"lean-vouch: {args.id} can't be an id: use lowercase letters, digits and hyphens.")
    graph = Graph(args.graph)
    try:
        view()
    except MarksError as error:
        sys.exit(f"lean-vouch: {error}")
    graph_format = graph.current().get("format", GRAPH_FORMAT)
    if graph_format != GRAPH_FORMAT:
        sys.exit(f"lean-vouch: {args.graph} has format {graph_format}; this server reads format "
                 f"{GRAPH_FORMAT}. Rerun lean-vouch to rebuild it.")
    print(f"lean-vouch {VERSION}: http://127.0.0.1:{args.port}/review/diagram\n"
          f"Docs: http://127.0.0.1:{args.port}/{args.prefix.replace('.', '/')}.html", flush=True)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        sys.exit(f"lean-vouch: can't use port {args.port} ({error.strerror}). Choose another with --port.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
