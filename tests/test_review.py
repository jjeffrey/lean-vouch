"""review.py's checks and failures, with a stand-in for lake. Needs only Python."""

import os
import stat
import subprocess
import sys
import tempfile
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
import review  # noqa: E402


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
        return subprocess.run([sys.executable, str(REVIEW), "MyProject", "--no-serve"], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def test_an_old_lean_is_refused_before_anything_is_built(self):
        result = self.run_review("v4.24.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs v4.30 or newer", result.stderr)
        self.assertFalse((self.root / "docbuild").exists())

    def test_a_newer_lean_is_warned_about(self):
        self.assertIn("newer than it's been tested on", self.run_review("v4.99.0").stdout)

    def test_a_failed_graph_shows_leans_errors_and_leaves_no_file(self):
        result = self.run_review("v4.35.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid field", result.stderr)
        self.assertFalse((self.root / "review" / "graph.json.new").exists())

    def test_docbuild_matches_the_project(self):
        self.run_review("v4.35.0")
        lakefile = (self.root / "docbuild" / "lakefile.toml").read_text()
        self.assertIn('rev = "v4.35.0"', lakefile)
        self.assertIn('name = "my-project"', lakefile)
        self.assertEqual((self.root / "docbuild" / ".gitignore").read_text(), ".lake/\n")

    def test_docbuild_follows_the_projects_lean(self):
        self.run_review("v4.34.0")
        docbuild = self.root / "docbuild"
        (docbuild / "lake-manifest.json").write_text("{}")
        self.assertIn("moved docbuild/ to doc-gen4 v4.35.0", self.run_review("v4.35.0").stdout)
        self.assertIn('rev = "v4.35.0"', (docbuild / "lakefile.toml").read_text())
        self.assertEqual((docbuild / "lean-toolchain").read_text(), "leanprover/lean4:v4.35.0\n")
        self.assertFalse((docbuild / "lake-manifest.json").exists())

    def test_a_new_lean_clears_only_doc_gen4s_packages(self):
        self.run_review("v4.34.0")
        manifest = '{"packages": [{"name": "%s"}, {"name": "Cli"}]}'
        (self.root / "docbuild" / "lake-manifest.json").write_text(manifest % "«doc-gen4»")
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
        manifest = self.root / "docbuild" / "lake-manifest.json"
        manifest.write_text("{}")
        self.run_review("v4.35.0")
        self.assertTrue(manifest.exists())

    def test_outside_a_lake_project_it_says_so(self):
        (self.root / "lakefile.toml").unlink()
        self.assertIn("no lakefile", self.run_review("v4.35.0").stderr)


if __name__ == "__main__":
    unittest.main()
