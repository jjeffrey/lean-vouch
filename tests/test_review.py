"""The lean-vouch command's checks and failures, with a stand-in for lake. Needs only Python."""

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
from lean_vouch import cli as review  # noqa: E402


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


class LakefileTest(unittest.TestCase):
    def in_project(self, lakefile):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(folder.name)
        Path("lakefile.toml").write_text(lakefile)

    def test_any_valid_toml_is_read(self):
        self.in_project("name = 'proj'\n\n[[lean_lib]]\n  name = 'Lib'\n\n[[lean_lib]]\n\"name\" = \"Other\"\n")
        self.assertEqual(review.package_name(), "proj")
        self.assertEqual(review.library_names(), ["Lib", "Other"])

    def test_a_broken_lakefile_says_so(self):
        self.in_project("name = \n")
        with self.assertRaises(SystemExit) as caught:
            review.package_name()
        self.assertIn("lakefile.toml", str(caught.exception.code))


class DocbuildManifestTest(unittest.TestCase):
    def test_a_change_in_the_projects_pins_refreshes_docbuilds(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(folder.name)
        Path("lean-toolchain").write_text("leanprover/lean4:v4.30.0\n")
        Path("lake-manifest.json").write_text('{"packages": []}')
        review.set_up_folders("v4.30.0", "p")
        docbuild = review.HOME / "docbuild"
        pinned = docbuild / "lake-manifest.json"
        review.follow_project_pins(docbuild)
        pinned.write_text('{"packages": []}')
        review.follow_project_pins(docbuild)
        self.assertTrue(pinned.exists())
        Path("lake-manifest.json").write_text('{"packages": ["bumped"]}')
        review.follow_project_pins(docbuild)
        self.assertFalse(pinned.exists())


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

    def test_a_build_reports_lakes_progress_and_keeps_a_log(self):
        review.run(["sh", "-c", "echo 'warning: ignored'; echo '✔ [3/10] Built M'"], step="docs")
        self.assertEqual({key: self.status()[key] for key in ("state", "step", "done", "total")},
                         {"state": "building", "step": "docs", "done": 3, "total": 10})
        self.assertIn("✔ [3/10] Built M", review.LOG.read_text())

    def test_a_failed_command_points_to_the_log(self):
        with self.assertRaises(SystemExit) as stopped:
            review.run(["sh", "-c", "echo 'error: it broke'; exit 1"])
        self.assertIn("build.log", str(stopped.exception))
        self.assertIn("error: it broke", review.LOG.read_text())

    def test_a_step_keeps_its_start_while_it_reports(self):
        review.report("building", step="docs")
        since = self.status()["step_since"]
        time.sleep(0.01)
        review.report("building", step="docs", done=3, total=10)
        self.assertEqual(self.status()["step_since"], since)
        review.report("building", step="graph")
        self.assertGreater(self.status()["step_since"], since)

    def test_elapsed_time_reads_as_minutes_and_seconds(self):
        self.assertEqual([review.elapsed(s) for s in (5, 65, 3600)], ["5s", "1m 05s", "60m 00s"])

    def test_what_the_run_reviewed_carries_over(self):
        review.report("building", names=["Lib"])
        review.report("done", built="now", since=5.0)
        review.report("building", step="project")
        review.report("failed", message="It broke.")
        self.assertEqual({key: self.status()[key] for key in ("names", "built", "since")},
                         {"names": ["Lib"], "built": "now", "since": 5.0})
        self.assertNotIn("step", self.status())

    def test_a_garbled_status_file_doesnt_stop_a_build(self):
        (review.HOME / "build.json").write_text("{ partial")
        review.report("building")
        self.assertEqual(self.status()["state"], "building")

    def test_an_interrupted_build_has_a_reason(self):
        self.assertEqual(review.reason(KeyboardInterrupt()), "The build was interrupted.")
        self.assertEqual(review.reason(SystemExit("lean-vouch: first\nsecond")), "lean-vouch: first")

    def test_a_challenge_lists_what_you_havent_agreed_with_as_it_is_now(self):
        graph = review.HOME / "graph.json"
        graph.write_text(json.dumps({"library": {}, "declarations": {
            name: {"own": own, "deps": [], "uses": [], "module": "M", "kind": "def"} for name, own in
            (("M.agreed", "1"), ("M.changed", "2"), ("M.asked", "3"), ("M.unmarked", "4"))}}))
        (review.HOME / "marks").mkdir()
        mark = lambda status, own: {"status": status, "fingerprint": {"own": own, "deps": {}}, "notes": []}
        (review.HOME / "marks" / "ada.json").write_text(json.dumps({"format": 3, "name": "Ada", "marks": {
            "M.agreed": mark("agree", "1"), "M.changed": mark("agree", "old"), "M.asked": mark("question", "3")}}))
        self.assertEqual(review.unagreed(graph, ["M.agreed", "M.changed", "M.asked", "M.unmarked"], "ada"),
                         ["M.changed", "M.asked", "M.unmarked"])

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

    def run_review(self, toolchain, *options):
        (self.root / "lean-toolchain").write_text(f"leanprover/lean4:{toolchain}\n")
        env = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"}
        return subprocess.run([sys.executable, str(REVIEW), "MyProject", "--rebuild", *options], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def test_an_old_lean_is_refused_before_anything_is_built(self):
        result = self.run_review("v4.24.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs v4.30 or newer", result.stderr)
        self.assertFalse((self.root / ".lean-vouch").exists())

    def test_a_newer_lean_is_warned_about(self):
        self.assertIn("newer than it's been tested on", self.run_review("v4.99.0").stdout)

    def test_a_failed_graph_shows_leans_errors_and_leaves_no_file(self):
        result = self.run_review("v4.35.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid field", result.stderr)
        self.assertFalse((self.root / ".lean-vouch" / "graph.json.new").exists())

    def test_docbuild_matches_the_project(self):
        self.run_review("v4.35.0")
        lakefile = (self.root / ".lean-vouch" / "docbuild" / "lakefile.toml").read_text()
        self.assertIn('rev = "v4.35.0"', lakefile)
        self.assertIn('name = "my-project"', lakefile)
        self.assertIn('path = "../../"', lakefile)
        self.assertIn('packagesDir = "../../.lake/packages"', lakefile)

    def test_docbuild_follows_the_projects_lean(self):
        self.run_review("v4.34.0")
        docbuild = self.root / ".lean-vouch" / "docbuild"
        (docbuild / "lake-manifest.json").write_text("{}")
        self.assertIn("moved to doc-gen4 v4.35.0", self.run_review("v4.35.0").stdout)
        self.assertIn('rev = "v4.35.0"', (docbuild / "lakefile.toml").read_text())
        self.assertEqual((docbuild / "lean-toolchain").read_text(), "leanprover/lean4:v4.35.0\n")
        self.assertFalse((docbuild / "lake-manifest.json").exists())

    def test_a_new_lean_clears_only_doc_gen4s_packages(self):
        self.run_review("v4.34.0")
        manifest = '{"packages": [{"name": "%s"}, {"name": "Cli"}]}'
        (self.root / ".lean-vouch" / "docbuild" / "lake-manifest.json").write_text(manifest % "«doc-gen4»")
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
        manifest = self.root / ".lean-vouch" / "docbuild" / "lake-manifest.json"
        manifest.write_text("{}")
        self.run_review("v4.35.0")
        self.assertTrue(manifest.exists())

    def test_git_keeps_only_the_marks_and_the_challenges_theorems(self):
        self.run_review("v4.35.0")
        home = self.root / ".lean-vouch"
        for name in ("marks/ada.json", "quiz/ada.json", "graph.json", "docbuild/.lake/build/doc/index.html", "links.local.json",
                     "challenge.json", "challenge/Challenge.lean"):
            (home / name).parent.mkdir(parents=True, exist_ok=True)
            (home / name).write_text("{}")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        listed = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", ".lean-vouch"], cwd=self.root,
                                capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(sorted(listed), [".lean-vouch/.gitignore", ".lean-vouch/challenge.json", ".lean-vouch/marks/ada.json"])

    def challenge_run(self, *names):
        """`lean-vouch --challenge` with these names, on a project whose graph has a theorem and a definition."""
        (self.root / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
        home = self.root / ".lean-vouch"
        home.mkdir(exist_ok=True)
        decl = lambda kind, deps=(): {"kind": kind, "module": "M", "lines": [1, 1], "own": "1", "deps": list(deps), "uses": []}
        (home / "graph.json").write_text(json.dumps({"modules": {"M": []}, "library": {}, "declarations": {
            "M.d": decl("def"), "M.t": decl("theorem", ["M.d"])}}))
        env = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"}
        return subprocess.run([sys.executable, str(REVIEW), "--challenge", *names], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def test_naming_theorems_chooses_them_for_the_project(self):
        result = self.challenge_run("M.t")
        self.assertIn("chose 1 theorem", result.stdout)
        self.assertEqual(json.loads((self.root / ".lean-vouch" / "challenge.json").read_text())["theorems"], ["M.t"])

    def test_only_theorems_are_chosen(self):
        result = self.challenge_run("M.d")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("aren't: M.d", result.stderr)
        self.assertFalse((self.root / ".lean-vouch" / "challenge.json").exists())

    def test_with_nothing_chosen_it_says_how_to_choose(self):
        result = self.challenge_run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no theorems are chosen", result.stderr)

    def test_with_none_named_it_uses_the_chosen_ones(self):
        (self.root / ".lean-vouch").mkdir()
        (self.root / ".lean-vouch" / "challenge.json").write_text('{"format": 1, "theorems": ["M.t"]}')
        result = self.challenge_run()
        self.assertNotIn("no theorems are chosen", result.stderr)
        self.assertNotIn("chose", result.stdout, "nothing new was chosen")

    def test_a_failed_build_says_so_for_open_pages(self):
        self.run_review("v4.35.0")
        status = json.loads((self.root / ".lean-vouch" / "build.json").read_text())
        self.assertEqual(status["state"], "failed")
        self.assertIn("declaration graph", status["message"])

    def test_ctrl_c_during_a_build_stops_quietly_and_says_so_for_open_pages(self):
        (self.root / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
        (self.root / "bin" / "lake").write_text("#!/bin/sh\nexec sleep 20\n")
        env = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"}
        process = subprocess.Popen([sys.executable, str(REVIEW), "MyProject", "--rebuild"], cwd=self.root, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(process.kill)
        status = self.root / ".lean-vouch" / "build.json"
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
        (self.root / ".lean-vouch").mkdir(exist_ok=True)
        (self.root / ".lean-vouch" / "graph.json").write_text('{"good": true}')

    def test_builds_leave_out_the_projects_warnings(self):
        calls = self.root / "calls"
        (self.root / "lake-manifest.json").write_text("{}")
        self.stub_graph(f'echo "$*" >> "{calls}"\n'
                        'case "$*" in env*) echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_VOUCH_GRAPH" ;; esac\n'
                        "mkdir -p .lake/build/doc\n")
        self.run_review("v4.35.0", "--docs")
        builds = [line for line in calls.read_text().splitlines() if line.startswith("build") and "lean-vouch-graph" not in line]
        self.assertEqual(len(builds), 2, "the project, then its docs")
        for build in builds:
            self.assertIn("--log-level=error", build)

    def test_the_graph_programs_progress_is_shown_and_reported(self):
        status = self.root / "status-during"
        self.stub_graph('case "$*" in env*)\n'
                        '  echo "lean-vouch-progress loading 0 0" >&2\n'
                        '  echo "lean-vouch-progress fingerprinting 1 2" >&2\n'
                        '  echo "lean-vouch-progress fingerprinting 2 2" >&2\n'
                        f'  cp .lean-vouch/build.json "{status}"\n'
                        '  echo "a warning of its own" >&2\n'
                        '  echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_VOUCH_GRAPH" ;;\n'
                        "esac\n")
        result = self.run_review("v4.35.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("loaded the project in", result.stdout)
        self.assertIn("fingerprinted 2 declarations in", result.stdout)
        self.assertEqual(result.stdout.count("fingerprint"), 1, "a line a phase, when it's done")
        self.assertIn("a warning of its own", result.stdout)
        self.assertNotIn("lean-vouch-progress", result.stdout + result.stderr)
        during = json.loads(status.read_text())
        self.assertEqual((during["step"], during["phase"], during["done"], during["total"]), ("graph", "fingerprinting", 2, 2))

    def test_an_unchanged_graph_isnt_built_again(self):
        calls = self.root / "calls"
        self.stub_graph(f'echo "$*" >> "{calls}"\n'
                        'case "$*" in env*) echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_VOUCH_GRAPH" ;; esac\n'
                        "mkdir -p .lake/build/doc\n")
        graph_runs = lambda: sum(line.startswith("env") for line in calls.read_text().splitlines())
        self.assertEqual(self.run_review("v4.35.0").returncode, 0)
        self.assertEqual(graph_runs(), 1)
        result = self.run_review("v4.35.0")
        self.assertIn("unchanged since the last build", result.stdout)
        self.assertEqual(graph_runs(), 1)
        compiled = self.root / ".lake" / "build" / "lib" / "lean" / "MyProject.olean"
        compiled.parent.mkdir(parents=True)
        compiled.write_bytes(b"recompiled")
        self.run_review("v4.35.0")
        self.assertEqual(graph_runs(), 2, "a recompiled module means a new graph")

    def test_a_busy_port_stops_the_run_before_it_builds(self):
        calls = self.root / "calls"
        self.stub_graph(f'echo "$*" >> "{calls}"\n')
        (self.root / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
        env = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"}
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            result = subprocess.run([sys.executable, str(REVIEW), "MyProject", "--port", str(busy.getsockname()[1])],
                                    cwd=self.root, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Choose another with --port", result.stderr)
        self.assertNotIn("build", calls.read_text() if calls.exists() else "")

    def test_without_docs_only_the_project_is_built(self):
        calls = self.root / "calls"
        self.stub_graph(f'echo "$*" >> "{calls}"\n'
                        'case "$*" in env*) echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_VOUCH_GRAPH" ;; esac\n')
        (self.root / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
        env = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"}
        result = subprocess.run([sys.executable, str(REVIEW), "MyProject", "--rebuild", "--no-docs"], cwd=self.root, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(":docs", calls.read_text())
        self.assertNotIn("update", calls.read_text(), "doc-gen4 isn't fetched either")
        self.assertIn("no doc pages", result.stdout)
        self.assertIs(json.loads((self.root / ".lean-vouch" / "build.json").read_text())["docs"], False)

    def docs_built(self, *options):
        calls = self.root / "calls"
        calls.unlink(missing_ok=True)
        (self.root / ".lean-vouch" / "graph.key").unlink(missing_ok=True)   # the stub's graph is a placeholder
        self.stub_graph(f'echo "$*" >> "{calls}"\n'
                        'case "$*" in env*) echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_VOUCH_GRAPH" ;; esac\n'
                        "mkdir -p .lake/build/doc\n")
        self.assertEqual(self.run_review("v4.35.0", *options).returncode, 0)
        return ":docs" in calls.read_text()

    def test_a_new_project_starts_without_docs_and_remembers_the_choice(self):
        self.assertFalse(self.docs_built())
        self.assertTrue(self.docs_built("--docs"))
        self.assertTrue(self.docs_built(), "kept from the last run")
        self.assertFalse(self.docs_built("--no-docs"))
        self.assertFalse(self.docs_built(), "kept from the last run")

    def test_a_project_from_before_the_choice_keeps_its_docs(self):
        (self.root / ".lean-vouch").mkdir()
        (self.root / ".lean-vouch" / "build.json").write_text('{"state": "done", "built": "then"}')
        self.assertTrue(self.docs_built())

    def graph_runs(self, script=""):
        calls = self.root / "calls"
        self.stub_graph(f'echo "$*" >> "{calls}"\n' + script +
                        'case "$*" in env*) echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_VOUCH_GRAPH" ;; esac\n'
                        "mkdir -p .lake/build/doc\n")
        result = self.run_review("v4.35.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result, [line for line in calls.read_text().splitlines() if line.startswith("env")]

    def test_the_graph_program_is_compiled_once_for_the_projects_lean(self):
        result, runs = self.graph_runs('case "$*" in "build lean-vouch-graph") mkdir -p .lake/build/bin && '
                                       'touch .lake/build/bin/lean-vouch-graph ;; esac\n')
        self.assertTrue(runs[0].startswith("env /") and runs[0].endswith("lean-vouch-graph MyProject"), runs)
        tool = self.root / ".lean-vouch" / "graph-tool"
        self.assertEqual((tool / "lean-toolchain").read_text(), (self.root / "lean-toolchain").read_text())
        self.assertIn("compiling the graph program", result.stdout)

    def test_a_graph_program_that_wont_compile_still_runs(self):
        result, runs = self.graph_runs()
        self.assertTrue(runs[0].startswith("env lean --run ") and runs[0].endswith("DeclGraph.lean MyProject"), runs)
        self.assertIn("runs more slowly", result.stdout)

    def test_a_good_build_says_when_it_started_and_what_it_reviewed(self):
        self.stub_graph('case "$*" in env*) echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_VOUCH_GRAPH" ;; esac\n'
                        "echo '✔ [1/1] Built M'\nmkdir -p .lake/build/doc\n")
        before = time.time()
        result = self.run_review("v4.35.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("✔ [1/1] Built M", result.stdout, "every line, when the output isn't a terminal")
        status = json.loads((self.root / ".lean-vouch" / "build.json").read_text())
        self.assertEqual((status["state"], status["names"]), ("done", ["MyProject"]))
        self.assertGreaterEqual(status["since"], before - 1)

    def test_an_unreadable_new_graph_leaves_the_old_one(self):
        self.stub_graph('case "$*" in env*) echo garbage > "$LEAN_VOUCH_GRAPH" ;; esac\n')
        result = self.run_review("v4.35.0")
        self.assertIn("unreadable", result.stderr)
        self.assertEqual((self.root / ".lean-vouch" / "graph.json").read_text(), '{"good": true}')

    def test_what_a_project_prints_cant_reach_the_graph(self):
        self.stub_graph('case "$*" in env*) echo noise; echo \'{"modules": {}, "roots": []}\' > "$LEAN_VOUCH_GRAPH" ;; esac\n')
        self.assertEqual(self.run_review("v4.35.0").returncode, 0)
        self.assertEqual(json.loads((self.root / ".lean-vouch" / "graph.json").read_text())["roots"], [])

    def test_a_failed_docs_build_leaves_the_old_graph(self):
        self.stub_graph('case "$*" in env*) echo \'{"modules": {"M": []}, "roots": ["M"]}\' > "$LEAN_VOUCH_GRAPH" ;;\n'
                        '  *docs*) exit 1 ;; esac\n')
        self.assertNotEqual(self.run_review("v4.35.0", "--docs").returncode, 0)
        self.assertEqual((self.root / ".lean-vouch" / "graph.json").read_text(), '{"good": true}')

    def test_an_edited_gitignore_survives_losing_docbuild(self):
        self.run_review("v4.35.0")
        ignore = self.root / ".lean-vouch" / ".gitignore"
        ignore.write_text("*\n")
        shutil.rmtree(self.root / ".lean-vouch" / "docbuild")
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
