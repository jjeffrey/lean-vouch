"""Set up lean-review for a Lake project, build what it needs, and serve it. From the project's root:

    lean-review
    lean-review MyLibrary My.Module

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
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from lean_review.server import ID, VERSION

HERE = Path(__file__).resolve().parent
MINIMUM = (4, 30)  # the oldest Lean this has been run on, end to end; see docs/internals.md
TESTED = (4, 35)   # the newest
HOME = Path(".lean-review")

DOCBUILD = """\
# Builds this project's API documentation with doc-gen4, for lean-review.
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
    sys.exit(f"lean-review: {message}")


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
        print(f"lean-review: {tag} isn't a Lean release, so this may not work. "
              f"Tested on v{MINIMUM[0]}.{MINIMUM[1]} to v{TESTED[0]}.{TESTED[1]}.", flush=True)
    elif version < MINIMUM:
        fail(f"this project is on Lean {tag}; lean-review needs v{MINIMUM[0]}.{MINIMUM[1]} or newer.")
    elif version > TESTED:
        print(f"lean-review: Lean {tag} is newer than it's been tested on (v{TESTED[0]}.{TESTED[1]}). "
              "If the graph step fails, please open an issue.", flush=True)


def package_name():
    if Path("lakefile.toml").exists():
        found = re.search(r'^name\s*=\s*"(.*?)"', Path("lakefile.toml").read_text(encoding="utf-8"), re.M)
    elif Path("lakefile.lean").exists():
        found = re.search(r'^package\s+[«"]?([^\s»"]+)', Path("lakefile.lean").read_text(encoding="utf-8"), re.M)
    else:
        fail("run this from the root of a Lake project: there's no lakefile here.")
    if not found:
        fail("couldn't read the package name from the lakefile.")
    return found[1].strip("«»")


def library_names():
    """The project's libraries, as its lakefile declares them."""
    if Path("lakefile.toml").exists():
        blocks = Path("lakefile.toml").read_text(encoding="utf-8").split("[[")
        return [found[1] for block in blocks if block.startswith("lean_lib]]")
                if (found := re.search(r'^name\s*=\s*"(.*?)"', block, re.M))]
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
        fail("found no modules to review. Name them, as in `lean-review MyProject`.")
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
        print(f"lean-review: created {HOME}/ (doc-gen4 {tag}).", flush=True)
    elif changed:
        print(f"lean-review: moved to doc-gen4 {tag}, for the project's Lean.", flush=True)


def source_folders():
    """Where the project's modules are, as a library's `srcDir` can move them: Lake's source path,
    without its dependencies' folders and Lean's own."""
    path = subprocess.run(["lake", "env", "printenv", "LEAN_SRC_PATH"], capture_output=True, text=True).stdout
    root = Path.cwd().resolve()
    folders = dict.fromkeys(Path(folder).resolve() for folder in path.strip().split(os.pathsep) if folder)
    return [folder for folder in folders
            if folder.is_relative_to(root) and ".lake" not in folder.relative_to(root).parts] or [root]


def run(command, cwd=".", env=None):
    if subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}).returncode:
        fail(f"`{' '.join(command)}` failed; see above.")


def build_docs(modules):
    docbuild = HOME / "docbuild"
    if not (docbuild / "lake-manifest.json").exists():
        # Keeps Mathlib's update hook from fetching its cache again.
        run(["lake", "update", "doc-gen4"], docbuild, {"MATHLIB_NO_CACHE_ON_UPDATE": "1"})
    if not (docbuild / ".lake/build/doc").exists():
        uses_mathlib = '"mathlib"' in Path("lake-manifest.json").read_text(encoding="utf-8") if Path("lake-manifest.json").exists() else False
        print("lean-review: first docs build. doc-gen4 documents everything the project imports" +
              (", including Mathlib: expect about half an hour and 2 GB. It doesn't recompile Mathlib."
               if uses_mathlib else ": expect several minutes."), flush=True)
    run(["lake", "build", *[f"+{module}:docs" for module in modules]], docbuild,
        {"DOCGEN_SRC": os.environ.get("DOCGEN_SRC", "file")})


def build_graph(modules):
    """Build the graph beside the current one, and return it. It goes to a file, since a project's
    initializers can print to stdout, where Lean also puts compile errors: show those on failure."""
    new = HOME / "graph.json.new"
    done = subprocess.run(["lake", "env", "lean", "--run", str(HERE / "graph/DeclGraph.lean"), *modules],
                          stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "LEAN_REVIEW_GRAPH": str(new.resolve())})
    if done.returncode:
        new.unlink(missing_ok=True)
        fail(f"building the declaration graph failed:\n{done.stdout[-4000:]}\n"
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
    # Not from --name, which can change without making you someone else.
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
        print(f"lean-review: removed {len(stale)} old pages: " + ", ".join(sorted(p.stem for p in stale)[:8]) +
              (", ..." if len(stale) > 8 else ""), flush=True)


def report(state, **details):
    """Say how a build is going, for a server and the pages open on it. The time of the last good
    build carries over a failure, since those pages still show it."""
    status = HOME / "build.json"
    try:
        built = json.loads(status.read_text(encoding="utf-8")).get("built")
    except (OSError, ValueError, AttributeError):
        built = None
    # Swapped in whole, since the server reads it while this runs.
    temporary = status.with_name(status.name + ".tmp")
    temporary.write_text(json.dumps({"built": built, **details, "state": state, "pid": os.getpid()}), encoding="utf-8")
    os.replace(temporary, status)


def reason(error):
    """The first line of what stopped a build. Ctrl-C has nothing to say."""
    return (str(getattr(error, "code", error)).splitlines() or ["The build was interrupted."])[0]


def running_server(port):
    """The marks folder of the lean-review server on this port, if there is one."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/review/status", timeout=1) as response:
            found = json.load(response)
    except (OSError, ValueError):
        return None
    return found.get("marks_folder") if isinstance(found, dict) else None


def main():
    parser = argparse.ArgumentParser(prog="lean-review", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"lean-review {VERSION}")
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
    serving = None if args.rebuild else running_server(args.port)
    if serving and Path(serving) != (HOME / "marks").resolve():
        fail(f"port {args.port} is serving another project. Choose another with --port.")
    if serving:
        print(f"lean-review: already serving on port {args.port}, so this only rebuilds; open pages will offer to reload.", flush=True)
    set_up_folders(tag, package)
    report("building")
    try:
        modules = modules_of(args.names)
        print(f"lean-review: reviewing {len(modules)} modules.", flush=True)
        run(["lake", "build", *[f"+{module}" for module in modules]])
        built = build_graph(modules)
        build_docs(tops(built["modules"]))
        if args.clean:
            for root in built["roots"]:
                clean_docs(HOME / "docbuild/.lake/build/doc", root, built["modules"])
        # Last, so a failure leaves the graph that matches the docs.
        (HOME / "graph.json.new").replace(HOME / "graph.json")
    except KeyboardInterrupt as error:
        report("failed", message=reason(error))
        print("\nlean-review: stopped.", file=sys.stderr)
        sys.exit(130)
    except (SystemExit, Exception) as error:
        report("failed", message=reason(error))
        raise
    report("done", built=datetime.now().isoformat(timespec="seconds"))
    if args.rebuild or serving:
        return
    command = [sys.executable, str(HERE / "server.py"), "--docs", str(HOME / "docbuild/.lake/build/doc"),
               "--marks", str(HOME / "marks"), "--graph", str(HOME / "graph.json"),
               *[option for folder in source_folders() for option in ("--source", str(folder))],
               "--prefix", modules[0], "--id", who, "--name", name, "--port", str(args.port)]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
