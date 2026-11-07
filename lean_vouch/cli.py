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
import subprocess
import sys
import time
import tomllib
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from lean_vouch.server import ID, VERSION

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

# Everything here can be rebuilt but the marks, and quiz answers stay with the person.
GITIGNORE = """\
*
!.gitignore
!marks/
!marks/**
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


def run(command, cwd=".", env=None, step=None):
    """Run a build command, its output going to the terminal and to the build log. Lake's count of jobs
    goes to open pages. In a terminal, each finished job replaces the one before, as Lake's own display
    does, and everything else stays."""
    terminal = sys.stdout.isatty()
    width = shutil.get_terminal_size().columns - 1
    shown = reported = 0
    with LOG.open("a", encoding="utf-8") as log, subprocess.Popen(
            command, cwd=cwd, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace") as process:
        log.write(f"$ {' '.join(command)}\n")
        for line in process.stdout:
            log.write(line)
            job = JOB.match(line)
            if shown:
                sys.stdout.write("\r\033[K")
            shown = terminal and job is not None and line.startswith("✔")
            sys.stdout.write(line.rstrip("\n")[:width] if shown else line)
            sys.stdout.flush()
            if job and step and time.monotonic() - reported > 0.5:
                reported = time.monotonic()
                report("building", step=step, done=int(job[1]), total=int(job[2]))
        if shown:
            sys.stdout.write("\r\033[K")
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
        run(["lake", "update", "doc-gen4"], docbuild, {"MATHLIB_NO_CACHE_ON_UPDATE": "1"}, step="docs")
    if not (docbuild / ".lake/build/doc").exists():
        uses_mathlib = '"mathlib"' in Path("lake-manifest.json").read_text(encoding="utf-8") if Path("lake-manifest.json").exists() else False
        print("lean-vouch: first docs build. doc-gen4 documents everything the project imports" +
              (", including Mathlib: expect about half an hour and 2 GB. It doesn't recompile Mathlib."
               if uses_mathlib else ": expect several minutes."), flush=True)
    report("building", step="docs")
    run(["lake", "build", QUIET, *[f"+{module}:docs" for module in modules]], docbuild,
        {"DOCGEN_SRC": os.environ.get("DOCGEN_SRC", "file")}, step="docs")


def build_graph(modules):
    """Build the graph beside the current one, and return it. It goes to a file, since a project's
    initializers can print to stdout, where Lean also puts compile errors: show those on failure."""
    new = HOME / "graph.json.new"
    report("building", step="graph")
    done = subprocess.run(["lake", "env", "lean", "--run", str(HERE / "graph/DeclGraph.lean"), *modules],
                          stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "LEAN_VOUCH_GRAPH": str(new.resolve())})
    if done.returncode:
        new.unlink(missing_ok=True)
        fail(f"building the declaration graph failed:\n{done.stdout[-4000:]}\n"
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
# the pages still show it after a failure, and what the latest run reviewed, for the pages' rebuild button.
KEPT = ("built", "since", "names")


def report(state, **details):
    """Say how a build is going, for a server and the pages open on it."""
    status = HOME / "build.json"
    try:
        before = json.loads(status.read_text(encoding="utf-8"))
        kept = {key: before[key] for key in KEPT if key in before}
    except (OSError, ValueError, TypeError, AttributeError):
        kept = {"built": None}
    # Swapped in whole, since the server reads it while this runs.
    temporary = status.with_name(status.name + ".tmp")
    temporary.write_text(json.dumps({**kept, **details, "state": state, "pid": os.getpid()}), encoding="utf-8")
    os.replace(temporary, status)


def reason(error):
    """The first line of what stopped a build. Ctrl-C has nothing to say."""
    return (str(getattr(error, "code", error)).splitlines() or ["The build was interrupted."])[0]


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
    args = parser.parse_args()
    package = package_name()
    tag, version = toolchain()
    check_version(tag, version)
    if not shutil.which("lake"):
        fail("Lake isn't on your PATH. Install Lean through elan: https://github.com/leanprover/elan")
    who, name = identity(args.id, args.name)
    others = sorted(path.stem for path in (HOME / "marks").glob("*.json") if path.stem != who)
    if others and not (HOME / "marks" / f"{who}.json").exists():
        print(f"lean-vouch: there's no marks file for {who} yet, only for {', '.join(others)}. "
              "If one of those is you, use --id.", flush=True)
    serving = None if args.rebuild else running_server(args.port)
    if serving and Path(serving) != (HOME / "marks").resolve():
        fail(f"port {args.port} is serving another project. Choose another with --port.")
    if serving:
        print(f"lean-vouch: already serving on port {args.port}, so this only rebuilds; open pages will offer to reload.", flush=True)
    set_up_folders(tag, package)
    started = time.time()
    LOG.write_text("", encoding="utf-8")
    report("building", step="project", names=args.names)
    try:
        modules = modules_of(args.names)
        print(f"lean-vouch: reviewing {len(modules)} modules.", flush=True)
        run(["lake", "build", QUIET, *[f"+{module}" for module in modules]], step="project")
        built = build_graph(modules)
        build_docs(tops(built["modules"]))
        if args.clean:
            for root in built["roots"]:
                clean_docs(HOME / "docbuild/.lake/build/doc", root, built["modules"])
        # Last, so a failure leaves the graph that matches the docs.
        (HOME / "graph.json.new").replace(HOME / "graph.json")
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
