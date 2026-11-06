"""The lean-review command's checks and failures, with a stand-in for lake. Needs only Python."""

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REVIEW = Path(__file__).resolve().parent.parent / "review.py"
LAKE = """#!/bin/sh
case "$*" in
  env*) echo "DeclGraph.lean:197:16: error: Invalid field" ; exit 1 ;;
esac
mkdir -p .lake/build/doc
"""


sys.path.insert(0, str(REVIEW.parent))
from lean_review import cli as review  # noqa: E402


class CleanTest(unittest.TestCase):
    def test_only_pages_of_missing_modules_go(self):
        with tempfile.TemporaryDirectory() as folder:
            docs = Path(folder)
            for page in ("P.html", "P/Kept.html", "P/Sub/Kept.html", "P/Gone.html", "P/Old/Gone.html", "Init/Prelude.html"):
                (docs / page).parent.mkdir(parents=True, exist_ok=True)
                (docs / page).write_text("page")
            review.clean_docs(docs, "P", {"P": [], "P.Kept": [], "P.Sub.Kept": []})
            left = sorted(str(p.relative_to(docs)) for p in docs.glob("**/*.html"))
            self.assertEqual(left, ["Init/Prelude.html", "P.html", "P/Kept.html", "P/Sub/Kept.html"])
            self.assertFalse((docs / "P" / "Old").exists())


class SourceFoldersTest(unittest.TestCase):
    def folders(self, root, path):
        found = subprocess.CompletedProcess([], 0, stdout=path + "\n")
        with mock.patch.object(review.subprocess, "run", return_value=found), mock.patch.object(review.Path, "cwd", return_value=root):
            return review.source_folders()

    def test_only_the_projects_own_folders_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            path = os.pathsep.join([str(root / "src"), str(root / ".lake/packages/mathlib"), "/elsewhere/lean/lake"])
            self.assertEqual(self.folders(root, path), [root / "src"])

    def test_without_an_answer_it_is_the_project_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            self.assertEqual(self.folders(root, ""), [root])


class ModulesTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(folder.name)

    def modules(self, names, listed):
        found = lambda command, **_: subprocess.CompletedProcess(command, 0, stdout=listed[command[2].split(":")[0]])
        with mock.patch.object(review.subprocess, "run", side_effect=found):
            return review.modules_of(names)

    def test_every_library_by_default(self):
        Path("lakefile.toml").write_text('name = "p"\n\n[[lean_lib]]\nname = "Lib"\n\n[[lean_exe]]\nname = "run"\n\n'
                                         '[[lean_lib]]\nglobs = ["Tests.+"]\nname = "Tests"\n')
        self.assertEqual(review.library_names(), ["Lib", "Tests"])
        self.assertEqual(self.modules([], {"Lib": "Lib.A\nLib\n", "Tests": "Tests.B\n"}), ["Lib", "Lib.A", "Tests.B"])

    def test_a_lean_lakefile(self):
        Path("lakefile.lean").write_text('package p\n\n@[default_target]\nlean_lib Lib where\n\nlean_lib «Tests»\n')
        self.assertEqual(review.library_names(), ["Lib", "Tests"])

    def test_libraries_and_modules_can_be_named(self):
        Path("lakefile.toml").write_text('name = "p"\n\n[[lean_lib]]\nname = "Lib"\n')
        self.assertEqual(self.modules(["Other.M", "Lib"], {"Lib": "Lib\n"}), ["Other.M", "Lib"])

    def test_nothing_to_review_says_so(self):
        Path("lakefile.toml").write_text('name = "p"\n')
        with self.assertRaises(SystemExit) as caught:
            self.modules([], {})
        self.assertIn("no modules", str(caught.exception))

    def test_docs_need_only_what_nothing_imports(self):
        self.assertEqual(review.tops({"Lib": ["Lib.A"], "Lib.A": [], "Tests.B": ["Lib.A"], "Tests.C": []}),
                         ["Lib", "Tests.B", "Tests.C"])


class IdentityTest(unittest.TestCase):
    def identity(self, name, email, *chosen):
        config = {"user.name": name, "user.email": email}
        with mock.patch.object(review, "git_config", config.get):
            return review.identity(*(chosen or (None, None)))

    def test_two_people_of_one_name_get_different_ids(self):
        first, second = self.identity("Ada Lovelace", "a@example.com"), self.identity("Ada Lovelace", "b@example.com")
        self.assertRegex(first[0], r"^ada-lovelace-[0-9a-f]{4}$")
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1], "Ada Lovelace")

    def test_the_id_doesnt_depend_on_the_machine(self):
        self.assertEqual(self.identity("Ada", "a@example.com"), self.identity("Ada", "a@example.com"))

    def test_accents_become_plain_letters_and_the_name_keeps_them(self):
        who, name = self.identity("José García", "a@example.com")
        self.assertTrue(who.startswith("jose-garcia-"))
        self.assertEqual(name, "José García")

    def test_a_name_with_no_latin_letters_asks_for_an_id(self):
        with self.assertRaises(SystemExit) as caught:
            self.identity("李明", "a@example.com")
        self.assertIn("--id", str(caught.exception))
        self.assertEqual(self.identity("李明", "a@example.com", "li-ming", None), ("li-ming", "李明"))

    def test_a_new_name_is_still_the_same_person(self):
        self.assertEqual(self.identity("Ada Lovelace", "a@example.com", None, "Ada")[0],
                         self.identity("Ada Lovelace", "a@example.com")[0])
        self.assertEqual(self.identity("Ada Lovelace", "a@example.com", None, "Ada")[1], "Ada")

    def test_a_bad_id_stops_before_any_build(self):
        with self.assertRaises(SystemExit) as caught:
            self.identity("Ada", "a@example.com", "Ada L", None)
        self.assertIn("can't be an id", str(caught.exception))

    def test_both_can_be_chosen(self):
        self.assertEqual(self.identity("Ada", "a@example.com", "claude", "Claude"), ("claude", "Claude"))


class ReportTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(folder.name)
        review.HOME.mkdir()

    def status(self):
        return json.loads((review.HOME / "build.json").read_text())

    def test_a_failure_keeps_the_last_good_builds_time(self):
        review.report("done", built="then")
        review.report("building")
        self.assertEqual((self.status()["state"], self.status()["built"]), ("building", "then"))
        review.report("failed", message="It broke.")
        self.assertEqual((self.status()["built"], self.status()["message"]), ("then", "It broke."))

    def test_a_garbled_status_file_doesnt_stop_a_build(self):
        (review.HOME / "build.json").write_text("{ partial")
        review.report("building")
        self.assertEqual(self.status()["state"], "building")

    def test_an_interrupted_build_has_a_reason(self):
        self.assertEqual(review.reason(KeyboardInterrupt()), "The build was interrupted.")
        self.assertEqual(review.reason(SystemExit("lean-review: first\nsecond")), "lean-review: first")

    def test_no_server_is_none(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertIsNone(review.running_server(port))


class CleanSecondRootTest(unittest.TestCase):
    def test_a_dotted_root_finds_its_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            docs = Path(folder)
            (docs / "P" / "Sub").mkdir(parents=True)
            (docs / "P" / "Sub" / "Gone.html").write_text("page")
            review.clean_docs(docs, "P.Sub", {"P.Sub": []})
            self.assertFalse((docs / "P" / "Sub" / "Gone.html").exists())


class ReviewTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "lakefile.toml").write_text('name = "my-project"\n')
        (self.root / "bin").mkdir()
        lake = self.root / "bin" / "lake"
        lake.write_text(LAKE)
        lake.chmod(lake.stat().st_mode | stat.S_IEXEC)

    def run_review(self, toolchain):
        (self.root / "lean-toolchain").write_text(f"leanprover/lean4:{toolchain}\n")
        env = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"}
        return subprocess.run([sys.executable, str(REVIEW), "MyProject", "--rebuild"], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def test_an_old_lean_is_refused_before_anything_is_built(self):
        result = self.run_review("v4.24.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs v4.30 or newer", result.stderr)
        self.assertFalse((self.root / ".lean-review").exists())

    def test_a_newer_lean_is_warned_about(self):
        self.assertIn("newer than it's been tested on", self.run_review("v4.99.0").stdout)

    def test_a_failed_graph_shows_leans_errors_and_leaves_no_file(self):
        result = self.run_review("v4.35.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid field", result.stderr)
        self.assertFalse((self.root / ".lean-review" / "graph.json.new").exists())

    def test_docbuild_matches_the_project(self):
        self.run_review("v4.35.0")
        lakefile = (self.root / ".lean-review" / "docbuild" / "lakefile.toml").read_text()
        self.assertIn('rev = "v4.35.0"', lakefile)
        self.assertIn('name = "my-project"', lakefile)
        self.assertIn('path = "../../"', lakefile)
        self.assertIn('packagesDir = "../../.lake/packages"', lakefile)

    def test_docbuild_follows_the_projects_lean(self):
        self.run_review("v4.34.0")
        docbuild = self.root / ".lean-review" / "docbuild"
        (docbuild / "lake-manifest.json").write_text("{}")
        self.assertIn("moved to doc-gen4 v4.35.0", self.run_review("v4.35.0").stdout)
        self.assertIn('rev = "v4.35.0"', (docbuild / "lakefile.toml").read_text())
        self.assertEqual((docbuild / "lean-toolchain").read_text(), "leanprover/lean4:v4.35.0\n")
        self.assertFalse((docbuild / "lake-manifest.json").exists())

    def test_a_new_lean_clears_only_doc_gen4s_packages(self):
        self.run_review("v4.34.0")
        manifest = '{"packages": [{"name": "%s"}, {"name": "Cli"}]}'
        (self.root / ".lean-review" / "docbuild" / "lake-manifest.json").write_text(manifest % "«doc-gen4»")
        (self.root / "lake-manifest.json").write_text(manifest % "mathlib")
        builds = {name: self.root / ".lake" / "packages" / name / ".lake" / "build"
                  for name in ("doc-gen4", "Cli", "mathlib")}
        for build in builds.values():
            build.mkdir(parents=True)
        self.run_review("v4.35.0")
        self.assertFalse(builds["doc-gen4"].exists())
        self.assertTrue(builds["Cli"].exists(), "the project requires it too")
        self.assertTrue(builds["mathlib"].exists())

    def test_the_same_lean_keeps_the_manifest(self):
        self.run_review("v4.35.0")
        manifest = self.root / ".lean-review" / "docbuild" / "lake-manifest.json"
        manifest.write_text("{}")
        self.run_review("v4.35.0")
        self.assertTrue(manifest.exists())

    def test_git_keeps_only_the_marks(self):
        self.run_review("v4.35.0")
        home = self.root / ".lean-review"
        for name in ("marks/ada.json", "quiz/ada.json", "graph.json", "docbuild/.lake/build/doc/index.html", "links.local.json"):
            (home / name).parent.mkdir(parents=True, exist_ok=True)
            (home / name).write_text("{}")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        listed = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", ".lean-review"], cwd=self.root,
                                capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(sorted(listed), [".lean-review/.gitignore", ".lean-review/marks/ada.json"])

    def test_a_failed_build_says_so_for_open_pages(self):
        self.run_review("v4.35.0")
        status = json.loads((self.root / ".lean-review" / "build.json").read_text())
        self.assertEqual(status["state"], "failed")
        self.assertIn("declaration graph", status["message"])

    def test_ctrl_c_during_a_build_stops_quietly_and_says_so_for_open_pages(self):
        (self.root / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
        (self.root / "bin" / "lake").write_text("#!/bin/sh\nexec sleep 20\n")
        env = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"}
        process = subprocess.Popen([sys.executable, str(REVIEW), "MyProject", "--rebuild"], cwd=self.root, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(process.kill)
        status = self.root / ".lean-review" / "build.json"
        for _ in range(100):
            if status.exists():
                break
            time.sleep(0.05)
        time.sleep(0.3)
        process.send_signal(signal.SIGINT)
        _, errors = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 130)
        self.assertNotIn("Traceback", errors)
        self.assertIn("stopped", errors)
        self.assertEqual(json.loads(status.read_text())["message"], "The build was interrupted.")

    def stub_graph(self, script):
        (self.root / "bin" / "lake").write_text("#!/bin/sh\n" + script)
        (self.root / ".lean-review").mkdir(exist_ok=True)
        (self.root / ".lean-review" / "graph.json").write_text('{"good": true}')

    def test_an_unreadable_new_graph_leaves_the_old_one(self):
        self.stub_graph('case "$*" in env*) echo garbage > "$LEAN_REVIEW_GRAPH" ;; esac\n')
        result = self.run_review("v4.35.0")
        self.assertIn("unreadable", result.stderr)
        self.assertEqual((self.root / ".lean-review" / "graph.json").read_text(), '{"good": true}')

    def test_what_a_project_prints_cant_reach_the_graph(self):
        self.stub_graph('case "$*" in env*) echo noise; echo \'{"modules": {}, "roots": []}\' > "$LEAN_REVIEW_GRAPH" ;; esac\n')
        self.assertEqual(self.run_review("v4.35.0").returncode, 0)
        self.assertEqual(json.loads((self.root / ".lean-review" / "graph.json").read_text())["roots"], [])

    def test_a_failed_docs_build_leaves_the_old_graph(self):
        self.stub_graph('case "$*" in env*) echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_REVIEW_GRAPH" ;;\n'
                        '  *docs*) exit 1 ;; esac\n')
        self.assertNotEqual(self.run_review("v4.35.0").returncode, 0)
        self.assertEqual((self.root / ".lean-review" / "graph.json").read_text(), '{"good": true}')

    def test_an_edited_gitignore_survives_losing_docbuild(self):
        self.run_review("v4.35.0")
        ignore = self.root / ".lean-review" / ".gitignore"
        ignore.write_text("*\n")
        shutil.rmtree(self.root / ".lean-review" / "docbuild")
        self.run_review("v4.35.0")
        self.assertEqual(ignore.read_text(), "*\n")

    def test_no_lake_says_so(self):
        (self.root / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
        result = subprocess.run([sys.executable, str(REVIEW), "--rebuild"], cwd=self.root, env={"PATH": str(self.root)},
                                capture_output=True, text=True)
        self.assertIn("Lake isn't on your PATH", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_outside_a_lake_project_it_says_so(self):
        (self.root / "lakefile.toml").unlink()
        self.assertIn("no lakefile", self.run_review("v4.35.0").stderr)


if __name__ == "__main__":
    unittest.main()
