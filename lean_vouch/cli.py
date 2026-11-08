"""Set up lean-vouch for a Lake project, build what it needs, and serve it. From the project's root:

    lean-vouch
    lean-vouch MyLibrary My.Module

It reviews every library in the project, or just the libraries and modules you name. Safe to rerun: it refreshes the
docs and graph after you change the code.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unicodedata
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from lean_vouch import challenge
from lean_vouch.server import ID, VERSION, Graph, agreed_now

HERE = Path(__file__).resolve().parent
MINIMUM = (4, 30)  # the oldest Lean this has been run on, end to end; see docs/internals.md
TESTED = (4, 35)   # the newest
HOME = Path(".lean-vouch")

DOCBUILD = """\
# Builds this project's API documentation with doc-gen4, for lean-vouch.
name = "docbuild"
reservoir = false
version = "0.1.0"
packagesDir = "../../.lake/packages"

[[require]]
scope = "leanprover"
name = "doc-gen4"
rev = "{version}"

[[require]]
name = "{package}"
path = "../../"
"""

# Everything here can be rebuilt but the marks and the theorems chosen for the challenge, and quiz
# answers stay with the person.
GITIGNORE = """\
*
!.gitignore
!marks/
!marks/**
!challenge.json
"""


def fail(message):
    sys.exit(f"lean-vouch: {message}")


def toolchain():
    """The project's Lean version, as its tag and (major, minor), which is None if it isn't a release."""
    if not Path("lean-toolchain").exists():
        fail("this project has no lean-toolchain file.")
    tag = Path("lean-toolchain").read_text(encoding="utf-8").strip().rsplit(":", 1)[-1]
    found = re.fullmatch(r"v(\d+)\.(\d+)\.\d+.*", tag)
    return tag, (int(found[1]), int(found[2])) if found else None


def check_version(tag, version):
    """Say so now, before a long doc-gen4 build that can't succeed."""
    if version is None:
        print(f"lean-vouch: {tag} isn't a Lean release, so this may not work. "
              f"Tested on v{MINIMUM[0]}.{MINIMUM[1]} to v{TESTED[0]}.{TESTED[1]}.", flush=True)
    elif version < MINIMUM:
        fail(f"this project is on Lean {tag}; lean-vouch needs v{MINIMUM[0]}.{MINIMUM[1]} or newer.")
    elif version > TESTED:
        print(f"lean-vouch: Lean {tag} is newer than it's been tested on (v{TESTED[0]}.{TESTED[1]}). "
              "If the graph step fails, please open an issue.", flush=True)


def lakefile_toml():
    try:
        return tomllib.loads(Path("lakefile.toml").read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        fail(f"couldn't read lakefile.toml: {error}")


def package_name():
    if Path("lakefile.toml").exists():
        found = lakefile_toml().get("name")
    elif Path("lakefile.lean").exists():
        found = re.search(r'^package\s+[«"]?([^\s»"]+)', Path("lakefile.lean").read_text(encoding="utf-8"), re.M)
        found = found and found[1]
    else:
        fail("run this from the root of a Lake project: there's no lakefile here.")
    if not found or not isinstance(found, str):
        fail("couldn't read the package name from the lakefile.")
    return found.strip("«»")


def library_names():
    """The project's libraries, as its lakefile declares them."""
    if Path("lakefile.toml").exists():
        return [library["name"].strip("«»") for library in lakefile_toml().get("lean_lib", [])]
    found = re.findall(r"^(?:@\[.*?\]\s*)?lean_lib\s+(\S+)", Path("lakefile.lean").read_text(encoding="utf-8"), re.M)
    return [name.strip('«»"') for name in found]


def modules_of(names):
    """What to review: the modules of the libraries named, or of them all, and any modules named.
    Lake lists a library's modules, since its globs decide them. The first named comes first, as the
    project's name; then the shortest names, which is what the graph keeps as its roots."""
    libraries = library_names()
    chosen = names or libraries
    modules = []
    for name in chosen:
        if name in libraries:
            listed = subprocess.run(["lake", "query", f"{name}:modules"], stdout=subprocess.PIPE, text=True)
            if listed.returncode:
                fail(f"Lake couldn't list the modules of {name}; see above.")
            modules += listed.stdout.split()
        else:
            modules.append(name)
    if not modules:
        fail("found no modules to review. Name them, as in `lean-vouch MyProject`.")
    return sorted(set(modules), key=lambda m: (m != chosen[0], m.count("."), m))


def tops(imports):
    """The modules no other module imports. doc-gen4 documents what a module imports, once per
    module named, so these are enough, and a library with one root takes one pass."""
    imported = {module for names in imports.values() for module in names}
    return [module for module in imports if module not in imported]


def packages_in(manifest):
    return {p["name"].strip("«»") for p in json.loads(manifest.read_text(encoding="utf-8")).get("packages", [])} if manifest.exists() else set()


def clear_doc_packages(manifest):
    """Delete the builds of the packages only doc-gen4 needs. Lake keeps a package's compiled C when
    the Lean version changes, and it won't link against another Lean. The folder is shared, so what
    the project itself requires stays: that may be all of Mathlib."""
    for name in packages_in(manifest) - packages_in(Path("lake-manifest.json")):
        shutil.rmtree(Path(".lake/packages") / name / ".lake/build", ignore_errors=True)


def set_up_folders(tag, package):
    """Docs live in their own folder, so the project gains no dependency. doc-gen4 tags match Lean's
    versions, and it must match the project's: its main branch needs the newest Lean."""
    docbuild = HOME / "docbuild"
    docbuild.mkdir(parents=True, exist_ok=True)
    wanted, written = Path("lean-toolchain").read_text(encoding="utf-8"), docbuild / "lean-toolchain"
    new = not (docbuild / "lakefile.toml").exists()
    changed = new or not written.exists() or written.read_text(encoding="utf-8") != wanted
    if changed:
        (docbuild / "lakefile.toml").write_text(DOCBUILD.format(version=tag, package=package), encoding="utf-8")
        written.write_text(wanted, encoding="utf-8")
        # docbuild shares the project's .lake, so on another Lean it would overwrite the project's build.
        clear_doc_packages(docbuild / "lake-manifest.json")
        (docbuild / "lake-manifest.json").unlink(missing_ok=True)
    if not (HOME / ".gitignore").exists():   # it's yours to edit, to keep marks out of git
        (HOME / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    if new:
        print(f"lean-vouch: created {HOME}/ (doc-gen4 {tag}).", flush=True)
    elif changed:
        print(f"lean-vouch: moved to doc-gen4 {tag}, for the project's Lean.", flush=True)


def source_folders():
    """Where the project's modules are, as a library's `srcDir` can move them: Lake's source path,
    without its dependencies' folders and Lean's own."""
    path = subprocess.run(["lake", "env", "printenv", "LEAN_SRC_PATH"], capture_output=True, text=True).stdout
    root = Path.cwd().resolve()
    folders = dict.fromkeys(Path(folder).resolve() for folder in path.strip().split(os.pathsep) if folder)
    return [folder for folder in folders
            if folder.is_relative_to(root) and ".lake" not in folder.relative_to(root).parts] or [root]


# A project's own warnings repeat at every build, twice over, and say nothing about reviewing it. `lake
# build` still shows them, and a failing build shows its whole log.
QUIET = "--log-level=error"


LOG = HOME / "build.log"
JOB = re.compile(r"^\S+ \[(\d+)/(\d+)\] ")   # Lake's line for each job it finishes: "✔ [3/10] Built M"


STEPS = {"project": "building the project", "graph": "building the declaration graph", "docs": "building the docs"}
DONE = {"project": "built the project", "graph": "built the declaration graph", "docs": "built the docs"}
# The graph program's phases, as it reports them.
PHASES = {"loading": "loading the project", "fingerprinting": "fingerprinting declarations",
          "library": "following library definitions", "proofs": "tracing sorry through proofs",
          "statements": "printing statements", "writing": "writing the graph"}
# And what each says once it's done, to leave a breakdown of the step's time.
PHASES_DONE = {"loading": "loaded the project", "fingerprinting": "fingerprinted {n} declarations",
               "library": "followed {n} library definitions", "proofs": "traced sorry through {n} declarations' proofs",
               "statements": "printed {n} statements", "writing": "wrote the graph"}
PROGRESS = re.compile(r"^lean-vouch-progress (\S+) (\d+) (\d+)$")


def bar(done, total, width=12):
    filled = round(width * done / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def elapsed(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}m {seconds:02}s" if minutes else f"{seconds}s"


class Terminal:
    """What a step shows. In a terminal, a line counts up from the step's start until there's output.
    Each finished job then replaces the one before, as Lake's own display does, and everything else
    stays. Lake says nothing while a job runs, and one can take many minutes, so after ten quiet
    seconds the count comes back. When the step is done, a line says so. Elsewhere, as in a log, the
    step is announced once."""

    def __init__(self, step, label=None, done=None):
        self.live = sys.stdout.isatty()
        self.width = shutil.get_terminal_size().columns - 1
        self.label = label or STEPS.get(step, "building")
        self.finished = done or DONE.get(step, "done")
        self.began, self.heard, self.shown, self.ok = time.monotonic(), None, False, True
        self.status = None   # a phase and how far along it is, when the step reports one
        self.phase = None    # the phase under way: its name, when it began, and how many it has
        self.lock, self.done = threading.Lock(), threading.Event()

    def __enter__(self):
        if self.live:
            with self.lock:
                self.tick()
            threading.Thread(target=self.wait, daemon=True).start()
        else:
            print(f"lean-vouch: {self.label}.", flush=True)
        return self

    def __exit__(self, error, *_):
        self.done.set()
        with self.lock:
            if self.ok and error is None:
                self.finish_phase()
            self.clear()
            if self.live and self.ok and error is None:
                print(f"lean-vouch: {self.finished} in {elapsed(time.monotonic() - self.began)}.", flush=True)

    def clear(self):
        if self.shown:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
        self.shown = False

    def tick(self):
        so_far = elapsed(time.monotonic() - self.began)
        if self.status:   # what matters most first, since a narrow terminal cuts the end
            text = self.status.replace("{time}", so_far)
        elif self.heard is None:
            text = f"lean-vouch: {self.label}... {so_far}"
        elif time.monotonic() - self.heard >= 10:
            text = f"lean-vouch: still {self.label}, {so_far} so far"
        else:
            return
        self.clear()
        sys.stdout.write(text[:self.width])
        sys.stdout.flush()
        self.shown = True

    def wait(self):
        while not self.done.wait(1):
            with self.lock:
                if not self.done.is_set():
                    self.tick()

    def progress(self, phase, done, total):
        """A step's phase and how far along it is: a bar in a terminal. Each phase leaves a line saying
        how long it took, in a terminal or not."""
        name = PHASES.get(phase, phase)
        with self.lock:
            if self.phase is None or self.phase[0] != phase:
                self.finish_phase()
                self.phase = (phase, time.monotonic(), total)
            if self.live:
                self.status = (f"lean-vouch: {bar(done, total)} {done:,}/{total:,} {{time}} {name}" if total
                               else f"lean-vouch: {name}... {{time}}")
                self.tick()

    def finish_phase(self):
        """The line a finished phase leaves. Called holding the lock."""
        if self.phase is None:
            return
        phase, began, total = self.phase
        self.clear()
        said = PHASES_DONE.get(phase, phase).replace("{n}", f"{total:,}")
        print(f"lean-vouch:   {said} in {elapsed(time.monotonic() - began)}.", flush=True)
        self.phase = self.status = None

    def line(self, line, passing=False):
        """Show a line of output; `passing` lines are replaced by the next."""
        with self.lock:
            self.clear()
            self.heard = time.monotonic()
            self.shown = self.live and passing
            sys.stdout.write(line.rstrip("\n")[:self.width] if self.shown else line)
            sys.stdout.flush()


def run(command, cwd=".", env=None, step=None, label=None, done=None):
    """Run a build command, its output going to the terminal and to the build log. Lake's count of jobs
    goes to open pages."""
    reported = 0
    with LOG.open("a", encoding="utf-8") as log, Terminal(step, label, done) as terminal, subprocess.Popen(
            command, cwd=cwd, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace") as process:
        log.write(f"$ {' '.join(command)}\n")
        for line in process.stdout:
            log.write(line)
            job = JOB.match(line)
            terminal.line(line, passing=job is not None and line.startswith("✔"))
            if job and step and time.monotonic() - reported > 0.5:
                reported = time.monotonic()
                report("building", step=step, done=int(job[1]), total=int(job[2]))
        terminal.ok = process.wait() == 0
    if process.returncode:
        fail(f"`{' '.join(command)}` failed. Its output is in {LOG}.")


def follow_project_pins(docbuild):
    """docbuild's manifest pins the project's dependencies, and they share a folder, so an old one
    would check them out again at their old revisions. After the project's build, which may be what
    writes the project's manifest."""
    project = Path("lake-manifest.json")
    pins = hashlib.sha256(project.read_bytes() if project.exists() else b"").hexdigest()
    seen = docbuild / "project-manifest.sha256"
    if not seen.exists() or seen.read_text(encoding="utf-8") != pins:
        (docbuild / "lake-manifest.json").unlink(missing_ok=True)
        seen.write_text(pins, encoding="utf-8")


def build_docs(modules):
    docbuild = HOME / "docbuild"
    follow_project_pins(docbuild)
    if not (docbuild / "lake-manifest.json").exists():
        # Keeps Mathlib's update hook from fetching its cache again.
        run(["lake", "update", "doc-gen4"], docbuild, {"MATHLIB_NO_CACHE_ON_UPDATE": "1"}, step="docs",
            label="fetching doc-gen4", done="fetched doc-gen4")
    if not (docbuild / ".lake/build/doc").exists():
        uses_mathlib = '"mathlib"' in Path("lake-manifest.json").read_text(encoding="utf-8") if Path("lake-manifest.json").exists() else False
        print("lean-vouch: first docs build. doc-gen4 documents everything the project imports" +
              (", including Mathlib: expect about half an hour and 2 GB. It doesn't recompile Mathlib."
               if uses_mathlib else ": expect several minutes."), flush=True)
    report("building", step="docs")
    run(["lake", "build", QUIET, *[f"+{module}:docs" for module in modules]], docbuild,
        {"DOCGEN_SRC": os.environ.get("DOCGEN_SRC", "file")}, step="docs")


GRAPH_TOOL = HOME / "graph-tool"
GRAPH_LAKEFILE = """\
# Builds lean-vouch's graph program as an executable, for this project's Lean.
name = "lean-vouch-graph"

[[lean_exe]]
name = "lean-vouch-graph"
root = "LeanVouchGraph"
supportInterpreter = true
"""


def graph_program():
    """How to run the graph program. Compiled once for the project's Lean, it takes a fraction of a
    second on a small project, against seconds to compile it each time with `lean --run`, and works
    faster on a large one. If it won't compile, `lean --run` still works."""
    files = {"LeanVouchGraph.lean": (HERE / "graph/DeclGraph.lean").read_text(encoding="utf-8"),
             "lean-toolchain": Path("lean-toolchain").read_text(encoding="utf-8"), "lakefile.toml": GRAPH_LAKEFILE}
    GRAPH_TOOL.mkdir(exist_ok=True)
    for name, text in files.items():   # only when different, so Lake sees nothing new
        path = GRAPH_TOOL / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    executable = GRAPH_TOOL / ".lake/build/bin/lean-vouch-graph"
    compiling = Terminal(None, "compiling the graph program for this Lean, once", "compiled the graph program")
    with compiling if not executable.exists() else nullcontext():
        built = subprocess.run(["lake", "build", "lean-vouch-graph"], cwd=GRAPH_TOOL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    with LOG.open("a", encoding="utf-8") as log:
        log.write("$ lake build lean-vouch-graph\n" + built.stdout)
    if built.returncode == 0 and executable.exists():
        return [str(executable.resolve())]
    print(f"lean-vouch: couldn't compile the graph program, so it runs more slowly. See {LOG}.", flush=True)
    return ["lean", "--run", str(HERE / "graph/DeclGraph.lean")]


GRAPH_KEY = HOME / "graph.key"


def graph_inputs(modules):
    """Everything the graph is made from: the graph program, Lean, the dependencies' pins, the modules
    asked for, and the project's compiled files, by size and time, since Lake rewrites one only when
    it recompiles it. If none of this changed, neither did the graph."""
    key = hashlib.sha256()
    key.update((HERE / "graph/DeclGraph.lean").read_bytes())
    for name in ("lean-toolchain", "lake-manifest.json"):
        key.update(Path(name).read_bytes() if Path(name).exists() else b"")
    key.update("\n".join(sorted(modules)).encode())
    for path in sorted(Path(".lake/build/lib").rglob("*.olean")):
        stat = path.stat()
        key.update(f"{path}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return key.hexdigest()


def build_graph(modules):
    """Build the graph beside the current one, and return it, unless nothing it's made from has
    changed since the last good build. It goes to a file, since a project's initializers can print to
    stdout, where Lean also puts compile errors: show those on failure."""
    new = HOME / "graph.json.new"
    report("building", step="graph")
    current = HOME / "graph.json"
    if current.exists() and GRAPH_KEY.exists() and GRAPH_KEY.read_text(encoding="utf-8") == graph_inputs(modules):
        try:
            graph = json.loads(current.read_text(encoding="utf-8"))
            print("lean-vouch: the declaration graph is unchanged since the last build.", flush=True)
            return graph
        except (OSError, ValueError):
            pass
    program = graph_program()
    # The graph program reports its progress on stderr, and anything the project prints goes to
    # stdout, kept for when it fails.
    reported = 0
    with Terminal("graph") as terminal, tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as printed:
        with subprocess.Popen(["lake", "env", *program, *modules], stdout=printed, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace",
                              env={**os.environ, "LEAN_VOUCH_GRAPH": str(new.resolve()),
                                   "LEAN_VOUCH_PROGRESS": "1"}) as process:
            for line in process.stderr:
                if found := PROGRESS.match(line.strip()):
                    phase, count, total = found[1], int(found[2]), int(found[3])
                    terminal.progress(phase, count, total)
                    if time.monotonic() - reported > 0.5 or count == total:
                        reported = time.monotonic()
                        report("building", step="graph", phase=phase, done=count, total=total)
                else:
                    terminal.line(line)
        terminal.ok = process.returncode == 0
        printed.seek(0)
        output = printed.read()
    if process.returncode:
        new.unlink(missing_ok=True)
        fail(f"building the declaration graph failed:\n{output[-4000:]}\n"
             "If two libraries define the same name, they can't be imported together: review one at a "
             "time, as in `lean-vouch MyLibrary`.\n"
             "If this is about Lean itself, not your project, please open an issue.")
    try:
        return json.loads(new.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        new.unlink(missing_ok=True)
        fail("the declaration graph came out unreadable. Please open an issue.")


def slug(name):
    """A name as plain lowercase words, which every filesystem and git agree on."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def git_config(key):
    try:
        return subprocess.run(["git", "config", key], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def identity(chosen_id, chosen_name):
    """Who's reviewing. The id tells two people of the same name apart by their email, without
    publishing it, and comes out the same on each of their machines."""
    git_name = git_config("user.name")
    name = chosen_name or git_name or chosen_id or "reviewer"
    if chosen_id:
        if not ID.fullmatch(chosen_id):
            fail(f"{chosen_id} can't be an id: use lowercase letters, digits and hyphens.")
        return chosen_id, name
    # Not from --name if there's a git name: that can change without making you someone else.
    base = slug(git_name) or slug(name)
    if not base:
        fail(f"can't make a file name from {name!r}. Choose one with --id, such as --id my-name.")
    return f"{base}-{hashlib.sha256(git_config('user.email').encode()).hexdigest()[:4]}", name


def clean_docs(docs, root, modules):
    """Delete the project's pages for modules that no longer exist, and say which. doc-gen4 only
    writes pages, so a renamed module's old page stays, and can still be reached from an old link."""
    folder = docs / root.replace(".", "/")
    stale = [page for page in folder.glob("**/*.html")
             if ".".join(page.relative_to(docs).with_suffix("").parts) not in modules]
    for page in stale:
        page.unlink()
    for empty in sorted((p for p in folder.glob("**/*") if p.is_dir()), reverse=True):
        if not any(empty.iterdir()):
            empty.rmdir()
    if stale:
        print(f"lean-vouch: removed {len(stale)} old pages: " + ", ".join(sorted(p.stem for p in stale)[:8]) +
              (", ..." if len(stale) > 8 else ""), flush=True)


# What carries from one report to the next: when the last good build finished, and started, as
# the pages still show it after a failure, and what the latest run reviewed and whether it built the
# docs, for the pages' rebuild button and for what the server offers.
KEPT = ("built", "since", "names", "docs")


def report(state, **details):
    """Say how a build is going, for a server and the pages open on it."""
    status = HOME / "build.json"
    try:
        before = json.loads(status.read_text(encoding="utf-8"))
        kept = {key: before[key] for key in KEPT if key in before}
    except (OSError, ValueError, TypeError, AttributeError):
        before, kept = {}, {"built": None}
    # When this step started, for pages to say how long it has taken.
    if state == "building" and details.get("step"):
        same = before.get("state") == "building" and before.get("step") == details["step"] and before.get("pid") == os.getpid()
        details = {**details, "step_since": before.get("step_since") if same else time.time()}
    # Swapped in whole, since the server reads it while this runs.
    temporary = status.with_name(status.name + ".tmp")
    temporary.write_text(json.dumps({**kept, **details, "state": state, "pid": os.getpid()}), encoding="utf-8")
    os.replace(temporary, status)


def reason(error):
    """The first line of what stopped a build. Ctrl-C has nothing to say."""
    return (str(getattr(error, "code", error)).splitlines() or ["The build was interrupted."])[0]


def unagreed(graph_file, names, who):
    """Of `names`, those you haven't agreed with as they are now: unmarked, marked otherwise, or
    changed since."""
    try:
        marks = json.loads((HOME / "marks" / f"{who}.json").read_text(encoding="utf-8")).get("marks", {})
    except (OSError, ValueError, AttributeError):
        marks = {}
    graph = Graph(graph_file)
    return [name for name in names if not agreed_now(marks.get(name), graph.fingerprint(name))]


TARGETS = HOME / "challenge.json"


def write_challenge(targets, who):
    """Write the project's challenge for `lake comparator` from the last build, and check it says what
    the project does. Theorems named here become the project's chosen ones; otherwise the chosen ones
    are used."""
    graph_file, out = HOME / "graph.json", HOME / "challenge"
    if not graph_file.exists():
        fail("there's no graph yet: run lean-vouch once first.")
    if targets:
        try:
            challenge.closure(json.loads(graph_file.read_text(encoding="utf-8")), targets)
        except challenge.ChallengeError as error:
            fail(str(error))
        targets = challenge.choose(TARGETS, targets)
        print(f"lean-vouch: chose {len(targets)} theorem{'' if len(targets) == 1 else 's'} for the challenge, in {TARGETS}.",
              flush=True)
        ignore = HOME / ".gitignore"
        if ignore.exists() and "!challenge.json" not in ignore.read_text(encoding="utf-8"):
            print(f"lean-vouch: to share them, add the line `!challenge.json` to {ignore}.", flush=True)
    else:
        targets = challenge.chosen(TARGETS)
        if not targets:
            fail("no theorems are chosen for the challenge yet. Choose them in the pages, or name them: "
                 "lean-vouch --challenge My.theorem Other.theorem")
    built = graph_file.stat().st_mtime
    edited = [path for folder in source_folders() for path in folder.rglob("*.lean")
              if ".lake" not in path.parts and ".lean-vouch" not in path.parts and path.stat().st_mtime > built]
    if edited:
        fail(f"{edited[0]} has changed since the last build, and a challenge is copied from the code that was built. "
             "Run lean-vouch --rebuild first.")
    graph = json.loads(graph_file.read_text(encoding="utf-8"))
    try:
        names, warnings = challenge.write(graph, source_folders(), targets, out)
        for warning in warnings:
            print(f"lean-vouch: note: {warning}.", flush=True)
        plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"
        print(f"lean-vouch: wrote {out / 'Challenge.lean'}: {plural(len(targets), 'theorem')}, and "
              f"{plural(len(names) - len(targets), 'declaration')} their statements rest on. Checking that it says what "
              "the project says...", flush=True)
        differ = challenge.check(graph, out, graph_program())
    except challenge.ChallengeError as error:
        fail(str(error))
    if differ:
        fail(f"the challenge doesn't say what the project says, in {', '.join(differ)}. Copied out of its file, a "
             "declaration can mean something else, through an `open` or `variable` it relied on. "
             f"Edit {out / 'Challenge.lean'} and rerun, or choose other theorems.")
    print("lean-vouch: it matches the project, declaration by declaration.", flush=True)
    waiting = unagreed(graph_file, names, who)
    if waiting:
        print(f"lean-vouch: you haven't agreed with {len(waiting)} of them as they are now: {', '.join(waiting)}.", flush=True)
    print(f"lean-vouch: {out / 'comparator.json'} is its configuration for `lake comparator`.", flush=True)


def docs_wanted(chosen):
    """Whether this run builds the docs: as asked, or else as this project's last run did. A project
    first run before the choice was remembered had its docs built, so it keeps them; a new one starts
    without, since they take most of a first run."""
    if chosen is not None:
        return chosen
    try:
        last = json.loads((HOME / "build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return last.get("docs", True) is not False if isinstance(last, dict) else False


def running_server(port):
    """The marks folder of the lean-vouch server on this port, if there is one."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/review/status", timeout=1) as response:
            found = json.load(response)
    except (OSError, ValueError):
        return None
    return found.get("marks_folder") if isinstance(found, dict) else None


def main():
    parser = argparse.ArgumentParser(prog="lean-vouch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"lean-vouch {VERSION}")
    parser.add_argument("names", nargs="*", metavar="NAME", help="libraries or modules to review (default: every library)")
    parser.add_argument("--port", type=int, default=8797)
    parser.add_argument("--name", help="the name to show on your marks and notes (default: your git name)")
    parser.add_argument("--id", help="who you are, as the name of your marks file (default: from your git name and email)")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the docs and graph, without starting a server")
    parser.add_argument("--clean", action="store_true", help="delete docs pages left by renamed or removed modules")
    docs = parser.add_mutually_exclusive_group()
    docs.add_argument("--docs", dest="docs", action="store_true", default=None,
                      help="also build doc pages, which take most of a first run; the project remembers the choice")
    docs.add_argument("--no-docs", dest="docs", action="store_false",
                      help="stop building doc pages; the project remembers the choice")
    parser.add_argument("--challenge", nargs="*", metavar="THEOREM",
                        help="write the project's challenge for lake comparator, from the last build; theorems named "
                             "here become the chosen ones")
    args = parser.parse_args()
    package = package_name()
    tag, version = toolchain()
    check_version(tag, version)
    if not shutil.which("lake"):
        fail("Lake isn't on your PATH. Install Lean through elan: https://github.com/leanprover/elan")
    who, name = identity(args.id, args.name)
    if args.challenge is not None:
        return write_challenge(args.challenge, who)
    others = sorted(path.stem for path in (HOME / "marks").glob("*.json") if path.stem != who)
    if others and not (HOME / "marks" / f"{who}.json").exists():
        print(f"lean-vouch: there's no marks file for {who} yet, only for {', '.join(others)}. "
              "If one of those is you, use --id.", flush=True)
    serving = None if args.rebuild else running_server(args.port)
    if serving and Path(serving) != (HOME / "marks").resolve():
        fail(f"port {args.port} is serving another project. Choose another with --port.")
    if serving:
        print(f"lean-vouch: already serving on port {args.port}, so this only rebuilds; open pages will offer to reload.", flush=True)
    elif not args.rebuild and args.challenge is None:
        # Before building, which can take minutes, rather than after.
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", args.port))
            except OSError as error:
                fail(f"can't use port {args.port} ({error.strerror}). Choose another with --port.")
    with_docs = docs_wanted(args.docs)
    set_up_folders(tag, package)
    started = time.time()
    LOG.write_text("", encoding="utf-8")
    report("building", step="project", names=args.names, docs=with_docs)
    try:
        modules = modules_of(args.names)
        print(f"lean-vouch: reviewing {len(modules)} module{'' if len(modules) == 1 else 's'}.", flush=True)
        run(["lake", "build", QUIET, *[f"+{module}" for module in modules]], step="project")
        built = build_graph(modules)
        if with_docs:
            build_docs(tops(built["modules"]))
        else:
            print("lean-vouch: no doc pages: everything else works without them. `lean-vouch --docs` adds them, "
                  "and this project remembers it.", flush=True)
        if args.clean and with_docs:
            for root in built["roots"]:
                clean_docs(HOME / "docbuild/.lake/build/doc", root, built["modules"])
        # Last, so a failure leaves the graph that matches the docs.
        if (HOME / "graph.json.new").exists():
            (HOME / "graph.json.new").replace(HOME / "graph.json")
        GRAPH_KEY.write_text(graph_inputs(modules), encoding="utf-8")
    except KeyboardInterrupt as error:
        report("failed", message=reason(error))
        print("\nlean-vouch: stopped.", file=sys.stderr)
        sys.exit(130)
    except (SystemExit, Exception) as error:
        report("failed", message=reason(error))
        raise
    report("done", built=datetime.now().isoformat(timespec="seconds"), since=started)
    if args.rebuild or serving:
        return
    command = [sys.executable, str(HERE / "server.py"), "--docs", str(HOME / "docbuild/.lake/build/doc"),
               "--marks", str(HOME / "marks"), "--graph", str(HOME / "graph.json"),
               *[option for folder in source_folders() for option in ("--source", str(folder))],
               "--prefix", modules[0], "--id", who, "--name", name, "--port", str(args.port)]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
