"""Writing a challenge for `lake comparator`: what goes in, and that the check notices when the copy
says something else. The last tests need Lean and the example's graph."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lean_vouch import challenge  # noqa: E402

EXAMPLE = ROOT / "example"
GRAPH = EXAMPLE / "review" / "graph.json"
P = "ReviewExample."


def decl(module, lines, kind="def", deps=()):
    return {"module": module, "lines": lines, "kind": kind, "deps": list(deps), "uses": [], "own": "0"}


class ChallengeTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "P").mkdir()
        (self.root / "P" / "A.lean").write_text(
            "import Mathlib.Logic.Basic\n\nopen Nat\n\nnamespace P\n\n/-- Small. -/\ndef small (n : Nat) : Prop := n < 3\n\n"
            "theorem small_zero : small 0 := by\n  decide\n\nend P\n")
        (self.root / "P" / "B.lean").write_text(
            "import P.A\n\nnamespace P.Sub\n\ntheorem uses (n : Nat) (h : small n) :\n    n < 3 := h\n\n"
            "def unrelated : Nat := 0\n\nend P.Sub\n")
        self.graph = {"root": "P", "modules": {"P.A": [], "P.B": ["P.A"], "P": ["P.A", "P.B"]}, "declarations": {
            "P.small": decl("P.A", [7, 8]),
            "P.small_zero": decl("P.A", [10, 11], "theorem", ["P.small"]),
            "P.Sub.uses": decl("P.B", [5, 6], "theorem", ["P.small"]),
            "P.Sub.unrelated": decl("P.B", [8, 8]),
        }}

    def write(self, targets):
        names, warnings = challenge.write(self.graph, [self.root], targets, self.root / "out")
        return names, warnings, (self.root / "out" / "Challenge.lean").read_text()

    def test_only_the_targets_and_what_their_statements_rest_on(self):
        self.assertEqual(challenge.closure(self.graph, ["P.Sub.uses"]), {"P.Sub.uses", "P.small"})

    def test_a_target_must_be_a_theorem_in_the_project(self):
        with self.assertRaisesRegex(challenge.ChallengeError, "aren't: P.small"):
            challenge.closure(self.graph, ["P.small"])
        with self.assertRaisesRegex(challenge.ChallengeError, "not in the project's graph: P.nope"):
            challenge.closure(self.graph, ["P.nope"])

    def test_definitions_come_before_what_uses_them(self):
        self.assertEqual(challenge.ordered(self.graph, {"P.Sub.uses", "P.small", "P.small_zero"}),
                         ["P.small", "P.small_zero", "P.Sub.uses"])

    def test_proofs_become_sorry_and_definitions_stay_whole(self):
        _, _, text = self.write(["P.Sub.uses", "P.small_zero"])
        self.assertIn("def small (n : Nat) : Prop := n < 3", text)
        self.assertIn("theorem small_zero : small 0 := by\n  sorry", text)
        self.assertIn("theorem uses (n : Nat) (h : small n) :\n    n < 3 := by\n  sorry", text)
        self.assertNotIn("decide", text)
        self.assertNotIn("unrelated", text)

    def test_namespaces_opens_and_imports_carry_over(self):
        _, _, text = self.write(["P.Sub.uses", "P.small_zero"])
        self.assertIn("import Mathlib.Logic.Basic\n", text)
        self.assertNotIn("import P.A", text, "the project's own modules are what the challenge replaces")
        self.assertIn("namespace P\n\nopen Nat\n/-- Small. -/", text)
        self.assertEqual(text.count("namespace P\n"), 1, "neighbors in one namespace share it")
        self.assertIn("namespace P.Sub\n", text)

    def test_the_configuration_names_the_targets(self):
        self.write(["P.Sub.uses"])
        config = json.loads((self.root / "out" / "comparator.json").read_text())
        self.assertEqual((config["challenge_module"], config["solution_module"], config["theorem_names"]),
                         ("Challenge", "P", ["P.Sub.uses"]))
        self.assertEqual(config["permitted_axioms"], ["propext", "Quot.sound", "Classical.choice"])

    def test_variables_above_a_declaration_are_noted(self):
        path = self.root / "P" / "B.lean"
        path.write_text(path.read_text().replace("namespace P.Sub\n", "namespace P.Sub\nvariable (m : Nat)\n", 1)
                        .replace("\n\ntheorem uses", "\ntheorem uses", 1))
        _, warnings, _ = self.write(["P.Sub.uses"])
        self.assertTrue(any("variables" in warning for warning in warnings), warnings)

    def test_private_declarations_are_refused(self):
        self.graph["declarations"]["_private.P.A.0.P.hidden"] = decl("P.A", [7, 8])
        self.graph["declarations"]["P.Sub.uses"]["deps"].append("_private.P.A.0.P.hidden")
        with self.assertRaisesRegex(challenge.ChallengeError, "private"):
            self.write(["P.Sub.uses"])

    def test_where_a_proof_starts(self):
        self.assertEqual(challenge.proof_start("theorem t (h : a := b) : c := by"), 27)
        self.assertEqual(challenge.proof_start("theorem t : c -- := not this\n  := rfl"), 31)
        self.assertEqual(challenge.proof_start("def f : Nat → Nat\n  | 0 => 1\n  | _ => 2"), 18)


@unittest.skipUnless(GRAPH.exists() and shutil.which("lake"), "needs Lean and the example's graph")
class CheckTest(unittest.TestCase):
    """Against the example: a challenge for two theorems matches it, and one edited afterwards doesn't."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.out = Path(cls.folder.name)
        cls.graph = json.loads(GRAPH.read_text())
        cls.targets = [P + "divides_gcd", P + "Prime.coprime"]
        challenge.write(cls.graph, [EXAMPLE], cls.targets, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def check(self):
        here = os.getcwd()
        os.chdir(EXAMPLE)
        try:
            return challenge.check(self.graph, self.out, ["lean", "--run", str(ROOT / "lean_vouch" / "graph" / "DeclGraph.lean")])
        finally:
            os.chdir(here)

    def test_a_generated_challenge_matches_the_project(self):
        self.assertEqual(self.check(), [])

    def test_a_challenge_that_says_something_else_is_caught(self):
        path = self.out / "Challenge.lean"
        original = path.read_text()
        self.addCleanup(path.write_text, original)
        path.write_text(original.replace("∀ d, Divides d n → d = 1 ∨ d = n", "2 ≤ n ∧ ∀ d, Divides d n → d = 1 ∨ d = n")
                        .replace("(a b : Nat) (ha : Divides d a)", "(a b : Nat) (hb0 : 0 < b) (ha : Divides d a)"))
        self.assertEqual(sorted(self.check()), [P + "Prime", P + "divides_gcd"])


if __name__ == "__main__":
    unittest.main()
