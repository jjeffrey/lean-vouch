#!/usr/bin/env python3
"""Set up lean-review for a Lake project, build what it needs, and serve it. From the project's root:

    python3 path/to/lean-review/review.py MyProject
    python3 path/to/lean-review/review.py MyProject MyProject.Examples

Name the project's root module, then any modules it doesn't import. Safe to rerun: it refreshes the
docs and graph after you change the code.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MINIMUM = (4, 30)  # the oldest Lean this has been run on, end to end; see docs/internals.md
TESTED = (4, 35)   # the newest

DOCBUILD = """\
# Builds this project's API documentation with doc-gen4, for lean-review.
name = "docbuild"
reservoir = false
version = "0.1.0"
packagesDir = "../.lake/packages"

[[require]]
scope = "leanprover"
name = "doc-gen4"
rev = "{version}"

[[require]]
name = "{package}"
path = "../"
"""


def fail(message):
    sys.exit(f"lean-review: {message}")


def toolchain():
    """The project's Lean version, as its tag and (major, minor), which is None if it isn't a release."""
    if not Path("lean-toolchain").exists():
        fail("this project has no lean-toolchain file.")
    tag = Path("lean-toolchain").read_text().strip().rsplit(":", 1)[-1]
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
        found = re.search(r'^name\s*=\s*"(.*?)"', Path("lakefile.toml").read_text(), re.M)
    elif Path("lakefile.lean").exists():
        found = re.search(r'^package\s+[«"]?([^\s»"]+)', Path("lakefile.lean").read_text(), re.M)
    else:
        fail("run this from the root of a Lake project: there's no lakefile here.")
    if not found:
        fail("couldn't read the package name from the lakefile.")
    return found[1].strip("«»")


def packages_in(manifest):
    return {p["name"].strip("«»") for p in json.loads(manifest.read_text()).get("packages", [])} if manifest.exists() else set()


def clear_doc_packages(manifest):
    """Delete the builds of the packages only doc-gen4 needs. Lake keeps a package's compiled C when
    the Lean version changes, and it won't link against another Lean. The folder is shared, so what
    the project itself requires stays: that may be all of Mathlib."""
    for name in packages_in(manifest) - packages_in(Path("lake-manifest.json")):
        shutil.rmtree(Path(".lake/packages") / name / ".lake/build", ignore_errors=True)


def set_up_folders(tag, package):
    """Docs live in their own folder, so the project gains no dependency. doc-gen4 tags match Lean's
    versions, and it must match the project's: its main branch needs the newest Lean."""
    docbuild, review = Path("docbuild"), Path("review")
    docbuild.mkdir(exist_ok=True)
    review.mkdir(exist_ok=True)
    wanted, written = Path("lean-toolchain").read_text(), docbuild / "lean-toolchain"
    new = not (docbuild / "lakefile.toml").exists()
    changed = new or not written.exists() or written.read_text() != wanted
    if changed:
        (docbuild / "lakefile.toml").write_text(DOCBUILD.format(version=tag, package=package))
        written.write_text(wanted)
        # docbuild shares the project's .lake, so on another Lean it would overwrite the project's build.
        clear_doc_packages(docbuild / "lake-manifest.json")
        (docbuild / "lake-manifest.json").unlink(missing_ok=True)
    if new:
        (docbuild / ".gitignore").write_text(".lake/\n")
        (review / ".gitignore").write_text("graph.json\ngraph.json.new\nlinks.local.json\n")
        print(f"lean-review: created docbuild/ (doc-gen4 {tag}) and review/.", flush=True)
    elif changed:
        print(f"lean-review: moved docbuild/ to doc-gen4 {tag}, for the project's Lean.", flush=True)


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
    docbuild = Path("docbuild")
    if not (docbuild / "lake-manifest.json").exists():
        # Keeps Mathlib's update hook from fetching its cache again.
        run(["lake", "update", "doc-gen4"], docbuild, {"MATHLIB_NO_CACHE_ON_UPDATE": "1"})
    if not (docbuild / ".lake/build/doc").exists():
        uses_mathlib = '"mathlib"' in Path("lake-manifest.json").read_text() if Path("lake-manifest.json").exists() else False
        print("lean-review: first docs build. doc-gen4 documents everything the project imports" +
              (", including Mathlib: expect about half an hour and 2 GB. It doesn't recompile Mathlib."
               if uses_mathlib else ": expect several minutes."), flush=True)
    run(["lake", "build", *[f"+{module}:docs" for module in modules]], docbuild,
        {"DOCGEN_SRC": os.environ.get("DOCGEN_SRC", "file")})


def build_graph(modules):
    """Lean prints compile errors to stdout, so a failure leaves them in the output file: show them."""
    new = Path("review/graph.json.new")
    with new.open("w") as output:
        done = subprocess.run(["lake", "env", "lean", "--run", str(HERE / "graph/DeclGraph.lean"), *modules], stdout=output)
    if done.returncode:
        errors = new.read_text()[-4000:]
        new.unlink()
        fail(f"building the declaration graph failed:\n{errors}\n"
             "If this is about Lean itself, not your project, please open an issue.")
    new.replace("review/graph.json")


def clean_docs(docs, root, modules):
    """Delete the project's pages for modules that no longer exist, and say which. doc-gen4 only
    writes pages, so a renamed module's old page stays, and can still be reached from an old link."""
    stale = [page for page in (docs / root).glob("**/*.html")
             if ".".join(page.relative_to(docs).with_suffix("").parts) not in modules]
    for page in stale:
        page.unlink()
    for folder in sorted((p for p in (docs / root).glob("**/*") if p.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    if stale:
        print(f"lean-review: removed {len(stale)} old pages: " + ", ".join(sorted(p.stem for p in stale)[:8]) +
              (", ..." if len(stale) > 8 else ""), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("modules", nargs="+", metavar="MODULE", help="the root module, then any it doesn't import")
    parser.add_argument("--port", type=int, default=8797)
    parser.add_argument("--reviewer", help="the name on your notes (default: your git name)")
    parser.add_argument("--no-serve", action="store_true", help="only rebuild the docs and graph")
    parser.add_argument("--clean", action="store_true", help="delete docs pages left by renamed or removed modules")
    args = parser.parse_args()
    package = package_name()
    tag, version = toolchain()
    check_version(tag, version)
    set_up_folders(tag, package)
    run(["lake", "build"])
    build_docs(args.modules)
    build_graph(args.modules)
    if args.clean:
        clean_docs(Path("docbuild/.lake/build/doc"), args.modules[0], json.loads(Path("review/graph.json").read_text())["modules"])
    if args.no_serve:
        return
    reviewer = args.reviewer or subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
    command = [sys.executable, str(HERE / "server/server.py"), "--docs", "docbuild/.lake/build/doc",
               "--marks", "review/marks.json", "--graph", "review/graph.json",
               *[option for folder in source_folders() for option in ("--source", str(folder))],
               "--prefix", args.modules[0], "--reviewer", reviewer or "reviewer", "--port", str(args.port)]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
