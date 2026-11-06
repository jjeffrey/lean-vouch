"""DeclGraph's fingerprints: proofs don't matter, statements and definitions do. Needs Lean (elan)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden" / "example-graph.json"
GCD = "ReviewExample/Gcd.lean"
DIVIDES = "ReviewExample/Divides.lean"


def graph_of(project, *roots):
    roots = roots or ("ReviewExample",)
    subprocess.run(["lake", "build", "-q", *roots], cwd=project, check=True, capture_output=True, text=True)
    result = subprocess.run(["lake", "env", "lean", "--run", str(ROOT / "lean_vouch" / "graph" / "DeclGraph.lean"), *roots],
                            cwd=project, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("lake"), "needs Lean")
class GraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.original = Path(cls.folder.name) / "original"
        shutil.copytree(ROOT / "example", cls.original,
                        ignore=shutil.ignore_patterns(".lake", "docbuild", "review"))
        cls.before = graph_of(cls.original)

    def edited(self, file, old, new):
        """The graph after replacing `old` with `new`, in a copy of the project."""
        copy = Path(tempfile.mkdtemp(dir=self.folder.name))
        shutil.rmtree(copy)
        shutil.copytree(self.original, copy)
        path = copy / file
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))
        return graph_of(copy)

    def changed(self, after, field):
        before = self.before["declarations"]
        return {n for n, d in after["declarations"].items() if n in before and d[field] != before[n][field]}

    def flagged(self, after):
        """What a mark would show as out of date: anything that changed, or that has it below."""
        before, now = self.before["declarations"], after["declarations"]
        changed = {n for n in now if n in before and now[n]["own"] != before[n]["own"]}
        changed |= {n for n, e in after["library"].items() if e["own"] != self.before["library"].get(n, e)["own"]}

        def below(name, seen):
            for dep in [*now[name]["deps"], *now[name]["uses"]] if name in now else []:
                if dep not in seen:
                    seen.add(dep)
                    below(dep, seen)
            return seen
        return {n for n in now if n in before and changed & below(n, {n})}

    def test_records_its_format_and_lean_version(self):
        self.assertEqual(self.before["format"], 1)
        self.assertRegex(self.before["lean"], r"^\d+\.\d+\.\d+")

    def test_every_module_named_is_reviewed(self):
        copy = Path(tempfile.mkdtemp(dir=self.folder.name)) / "project"
        shutil.copytree(self.original, copy)
        with (copy / "lakefile.toml").open("a") as lakefile:
            lakefile.write('\n[[lean_lib]]\nname = "Extra"\n')
        (copy / "Extra.lean").write_text("import ReviewExample.Gcd\n\n"
                                         "theorem Extra.gcd_self : ReviewExample.gcd 0 1 = 1 := by simp [ReviewExample.gcd]\n")
        both = graph_of(copy, "ReviewExample", "Extra")
        self.assertEqual(both["root"], "ReviewExample")
        self.assertEqual(both["roots"], ["ReviewExample", "Extra"])
        self.assertEqual(graph_of(copy, "ReviewExample", "ReviewExample.Gcd", "Extra")["roots"], ["ReviewExample", "Extra"],
                         "only the outermost are kept")
        self.assertIn("Extra", both["modules"])
        self.assertIn("ReviewExample.gcd", both["declarations"]["Extra.gcd_self"]["deps"])
        self.assertEqual({n: d["own"] for n, d in both["declarations"].items() if n != "Extra.gcd_self"},
                         {n: d["own"] for n, d in self.before["declarations"].items()},
                         "another root changes no fingerprint in the first")

    def extra(self, text):
        """The graph of a small second library holding `text`."""
        copy = Path(tempfile.mkdtemp(dir=self.folder.name)) / "project"
        shutil.copytree(self.original, copy)
        with (copy / "lakefile.toml").open("a") as lakefile:
            lakefile.write('\n[[lean_lib]]\nname = "Extra"\n')
        (copy / "Extra.lean").write_text(text)
        return graph_of(copy, "Extra")

    def two_libraries(self, lower):
        """The graph of `Upper`, for which `Lower` is a library."""
        copy = Path(tempfile.mkdtemp(dir=self.folder.name)) / "project"
        shutil.copytree(self.original, copy)
        with (copy / "lakefile.toml").open("a") as lakefile:
            lakefile.write('\n[[lean_lib]]\nname = "Lower"\n\n[[lean_lib]]\nname = "Upper"\n')
        (copy / "Lower.lean").write_text(lower)
        (copy / "Upper.lean").write_text("import Lower\n\ndef use : " + ("bar = bar" if "theorem foo" in lower else "foo = foo") + " := "
                                         + ("foo\n" if "theorem foo" in lower else "rfl\n"))
        return graph_of(copy, "Upper")

    def test_a_change_below_a_library_definition_is_seen(self):
        lower = "def bar : Nat := {}\ndef middle : Nat := bar\ndef foo : Nat := middle\n"
        one, two = self.two_libraries(lower.format(1)), self.two_libraries(lower.format(2))
        self.assertIn("foo", one["declarations"]["use"]["uses"])
        self.assertNotEqual(one["library"]["foo"]["own"], two["library"]["foo"]["own"])
        self.assertEqual(one["declarations"]["use"]["own"], two["declarations"]["use"]["own"], "only the mark's record notices")

    def test_a_library_proof_doesnt_count(self):
        lower = "theorem t : 1 = 1 := {}\ndef foo : Nat := have _ := t; 1\n"
        one, two = self.two_libraries(lower.format("rfl")), self.two_libraries(lower.format("by simp"))
        self.assertEqual(one["library"]["foo"]["own"], two["library"]["foo"]["own"])

    def test_a_theorem_cited_in_a_library_body_doesnt_count(self):
        # `Q` is reached only through `t`, whose statement isn't copied into `foo`.
        lower = "def Q : Prop := {}\ntheorem t : Q := by unfold Q; decide\ndef foo : Fin 2 := ⟨0, t⟩\n"
        one, two = self.two_libraries(lower.format("0 < 2")), self.two_libraries(lower.format("0 < 1 + 1"))
        self.assertEqual(one["library"]["foo"]["own"], two["library"]["foo"]["own"])

    def test_a_definition_in_a_library_theorems_statement_does_count(self):
        lower = "def bar : Nat := {}\ntheorem foo : bar = bar := rfl\n"
        one, two = self.two_libraries(lower.format(1)), self.two_libraries(lower.format(2))
        self.assertNotEqual(one["library"]["foo"]["own"], two["library"]["foo"]["own"])

    def test_a_partial_defs_body_counts(self):
        one, two = (self.extra(f"partial def spin (n : Nat) : Nat := spin (n + {k})\n") for k in (1, 2))
        self.assertNotEqual(one["declarations"]["spin"]["own"], two["declarations"]["spin"]["own"])

    def test_what_a_partial_def_calls_is_a_dependency(self):
        graph = self.extra("def helper (n : Nat) : Nat := n\npartial def spin (n : Nat) : Nat := spin (helper n)\n")
        self.assertIn("helper", graph["declarations"]["spin"]["deps"])

    def test_a_field_default_counts(self):
        one, two = (self.extra(f"structure S where\n  x : Nat := {k}\n") for k in (1, 2))
        self.assertNotEqual(one["declarations"]["S"]["own"], two["declarations"]["S"]["own"])

    def test_a_deprecated_declaration_counts_as_part_of_what_uses_it(self):
        source = ("def new (n : Nat) : Nat := n + 1\n"
                  '@[deprecated new (since := "2026-01-01")] def old (n : Nat) : Nat := n + {k}\n'
                  "set_option linter.deprecated false in\ndef user : Nat := old 1\n")
        one, two = (self.extra(source.replace("{k}", str(k))) for k in (2, 3))
        self.assertNotEqual(one["declarations"]["user"]["own"], two["declarations"]["user"]["own"])
        self.assertNotIn("old", one["declarations"]["user"]["deps"])

    def test_what_a_deprecated_alias_stands_for_is_a_dependency(self):
        graph = self.extra('def new : Nat := 1\n@[deprecated new (since := "2026-01-01")] abbrev old := new\n'
                           "set_option linter.deprecated false in\ndef user : Nat := old\n")
        self.assertEqual(graph["declarations"]["user"]["deps"], ["new"])

    def test_only_what_deriving_wrote_is_marked_derived(self):
        declarations = self.extra("structure A where\n  x : Nat\n  deriving BEq, Repr\n\n"
                                  "structure B where\n  x : Nat\n\n"
                                  "instance : BEq B := ⟨fun a b => a.x != b.x⟩\n\n"
                                  "structure C where\n  x : Nat\n\n"
                                  "deriving instance BEq for C\n")["declarations"]
        self.assertEqual({n for n, d in declarations.items() if d.get("derived")},
                         {"instBEqA", "instBEqA.beq", "instReprA", "instReprA.repr", "instBEqC", "instBEqC.beq"})
        self.assertIn("instBEqB", declarations)

    def test_swapping_fields_or_constructors_counts(self):
        one = self.extra("structure I where\n  lo : Nat\n  hi : Nat\ninductive C where\n  | a | b\n")
        two = self.extra("structure I where\n  hi : Nat\n  lo : Nat\ninductive C where\n  | b | a\n")
        self.assertNotEqual(one["declarations"]["I"]["own"], two["declarations"]["I"]["own"])
        self.assertNotEqual(one["declarations"]["C"]["own"], two["declarations"]["C"]["own"])

    def test_renaming_a_field_counts(self):
        one, two = (self.extra(f"structure I where\n  {name} : Nat\n") for name in ("size", "width"))
        self.assertNotEqual(one["declarations"]["I"]["own"], two["declarations"]["I"]["own"])

    def test_a_shared_helper_changing_name_flags_nothing_else(self):
        source = ("def {} (l : List Nat) : Nat := match l with\n  | [] => 0\n  | x :: _ => x\n\n"
                  "def second (l : List Nat) : Nat := match l with\n  | [] => 0\n  | x :: _ => x\n")
        one, two = (self.extra(source.format(name)) for name in ("first", "renamed"))
        self.assertEqual(one["declarations"]["second"]["own"], two["declarations"]["second"]["own"])

    def test_what_a_shared_helper_does_still_counts(self):
        one, two = (self.extra(f"def f (l : List Nat) : Nat := match l with\n  | [] => {k}\n  | x :: _ => x\n") for k in (0, 1))
        self.assertNotEqual(one["declarations"]["f"]["own"], two["declarations"]["f"]["own"])

    def test_which_helper_is_used_where_still_counts(self):
        source = ("def h (n : Nat) : Bool := match n with\n  | 0 => true\n  | _ => false\n"
                  "def i (n : Nat) : Nat := match n with\n  | 0 => 1\n  | _ => 2\n")
        one, two = (self.extra(source + f"def f (n : Nat) : Bool × Nat × Bool := {body}\n")
                    for body in ("((match n with | 0 => true | _ => false), (match n with | 0 => 1 | _ => 2), (match n with | 0 => true | _ => false))",
                                 "((match n with | 0 => true | _ => false), (match n with | 0 => 1 | _ => 2), (match n with | 0 => false | _ => true))"))
        self.assertNotEqual(one["declarations"]["f"]["own"], two["declarations"]["f"]["own"])

    def test_a_module_doc_has_a_fingerprint(self):
        one, two = (self.extra(f"/-! {text} -/\n\ndef x : Nat := 1\n") for text in ("One.", "Two."))
        self.assertNotEqual(one["moduleDocs"]["Extra"], two["moduleDocs"]["Extra"])
        self.assertEqual(one["declarations"]["x"]["own"], two["declarations"]["x"]["own"])

    def test_the_graph_can_go_to_a_file_away_from_what_a_project_prints(self):
        copy = Path(tempfile.mkdtemp(dir=self.folder.name)) / "project"
        shutil.copytree(self.original, copy)
        out = copy / "out.json"
        subprocess.run(["lake", "env", "lean", "--run", str(ROOT / "lean_vouch" / "graph" / "DeclGraph.lean"), "ReviewExample"], cwd=copy,
                       check=True, capture_output=True, text=True, env={**os.environ, "LEAN_VOUCH_GRAPH": str(out)})
        self.assertEqual(json.loads(out.read_text())["root"], "ReviewExample")

    def test_changing_a_proof_changes_no_fingerprint(self):
        after = self.edited(DIVIDES, "⟨1, (Nat.mul_one n).symm⟩", "⟨1, by simp⟩")
        self.assertEqual(self.changed(after, "own"), set())
        self.assertEqual(self.flagged(after), set())

    def test_changing_a_theorem_statement_flags_only_that_theorem(self):
        # Proofs that use it aren't flagged: Lean rechecks them, and their statements are unchanged.
        after = self.edited(
            DIVIDES, "theorem Divides.refl (n : Nat) : Divides n n :=",
            "theorem Divides.refl (n : Nat) : Divides n (n + 0) :=")
        self.assertEqual(self.changed(after, "own"), {"ReviewExample.Divides.refl"})
        self.assertEqual(self.flagged(after), {"ReviewExample.Divides.refl"})

    def test_changing_a_definition_reaches_everything_above_it(self):
        after = self.edited(GCD, "| 0, b => b", "| 0, b => b + 0")
        self.assertEqual(self.changed(after, "own"), {"ReviewExample.gcd"})
        flagged = self.flagged(after)
        self.assertLessEqual({"ReviewExample.gcd", "ReviewExample.gcd_divides", "ReviewExample.divides_gcd"}, flagged)
        self.assertNotIn("ReviewExample.Divides", flagged)

    def test_a_docstring_changes_no_fingerprint(self):
        after = self.edited(DIVIDES, "/-- Every number divides itself. -/", "/-- Reworded. -/")
        self.assertEqual(self.changed(after, "own"), set())

    PLACED = """def usesPanic (n : Nat) : Nat := if n = 0 then panic! "no" else n
def usesUnreachable (n : Nat) : Nat := if n = 0 then unreachable! else n
def usesAssert (n : Nat) : Nat := assert! n > 0; n
def usesSorry : Nat := sorry
def usesTactic : Nat := by sorry
def nested (n : Nat) : Nat := if n = 0 then panic! (toString (sorry : Nat)) else n
theorem stmtSorry : (sorry : Nat) = 1 := sorry

end ReviewExample"""

    def placed(self, code):
        declarations = self.edited(GCD, "end ReviewExample", code)["declarations"]
        return {n.rsplit(".")[-1]: declarations[n]["own"] for n in declarations
                if n.rsplit(".")[-1] in ("usesPanic", "usesUnreachable", "usesAssert", "usesSorry", "usesTactic", "nested", "stmtSorry")}

    def test_where_code_was_written_changes_no_fingerprint(self):
        here = self.placed(self.PLACED)
        self.assertEqual(len(here), 7)
        self.assertEqual(here, self.placed("-- A line, and each column moved.\n" + self.PLACED.replace(" :=", "  :=")))

    def test_what_a_panic_says_still_changes_its_fingerprint(self):
        here = self.placed(self.PLACED)
        there = self.placed(self.PLACED.replace('panic! "no"', 'panic! "yes"'))
        self.assertEqual({n for n in here if here[n] != there[n]}, {"usesPanic"})

    def test_a_name_with_a_space_cant_look_like_a_module_mark(self):
        after = self.edited(GCD, "namespace ReviewExample", "def «module Foo» : Nat := 1\n\nnamespace ReviewExample")
        self.assertIn("«module Foo»", after["declarations"], "marks on modules are keyed `module Foo`, bare")

    def test_a_name_that_only_looks_generated_is_kept(self):
        after = self.edited(GCD, "end ReviewExample", "theorem eq_spec : 1 = 1 := rfl\ntheorem proof_irrel : 1 = 1 := rfl\n"
                            "theorem match_up : 1 = 1 := rfl\ntheorem Foo.rec : 1 = 1 := rfl\ntheorem Foo.inj : 1 = 1 := rfl\n"
                            "theorem Foo.below : 1 = 1 := rfl\ntheorem Bar.induct : 1 = 1 := rfl\n\nend ReviewExample")
        names = ("eq_spec", "proof_irrel", "match_up", "Foo.rec", "Foo.inj", "Foo.below", "Bar.induct")
        self.assertLessEqual({f"ReviewExample.{n}" for n in names}, set(after["declarations"]))

    def test_generated_lemmas_are_left_out(self):
        after = self.edited(GCD, "end ReviewExample", "def probe (n : Nat) : Nat := match n with | 0 => 1 | k + 1 => k\n"
                            "theorem probe_self (n : Nat) : probe n = probe n := by simp\n\nend ReviewExample")
        made = [n for n in after["declarations"] if n.startswith("ReviewExample.probe")]
        self.assertEqual(sorted(made), ["ReviewExample.probe", "ReviewExample.probe_self"])

    def test_a_parent_projection_is_part_of_its_structure(self):
        after = self.edited(GCD, "end ReviewExample", "structure Base where\n  a : Nat\nstructure Other where\n  a : Nat\n  b : Nat\n"
                            "structure Both extends Base, Other\n\nend ReviewExample")
        made = [n for n in after["declarations"] if n.split(".")[1] in ("Base", "Other", "Both")]
        self.assertEqual(sorted(made), ["ReviewExample.Base", "ReviewExample.Both", "ReviewExample.Other"])

    def test_a_sorry_is_traced_through_proofs_only_to_what_relies_on_it(self):
        decls = self.before["declarations"]
        source = "ReviewExample.gcd_mul_lcm"
        self.assertTrue(decls[source]["sorry"])
        # Its statement doesn't mention the source: only following the proof finds this one.
        self.assertEqual(decls["ReviewExample.gcd_divides_mul"]["sorryFrom"], [source])
        self.assertNotIn(source, decls["ReviewExample.gcd_divides_mul"]["deps"])
        self.assertEqual(decls["ReviewExample.Coprime.lcm_eq"]["sorryFrom"], [source])
        # Each step of the way: a chain of two, and no step for what has no `sorry` behind it.
        self.assertEqual(decls["ReviewExample.gcd_divides_mul"]["sorryVia"], [source])
        self.assertEqual(decls["ReviewExample.gcd_divides_mul_comm"]["sorryFrom"], [source])
        self.assertEqual(decls["ReviewExample.gcd_divides_mul_comm"]["sorryVia"], ["ReviewExample.gcd_divides_mul"])
        self.assertNotIn("sorryVia", decls[source])
        for clean in ("ReviewExample.Coprime.divides_one", "ReviewExample.gcd", "ReviewExample.lcm"):
            self.assertFalse({"sorry", "sorryFrom"} & decls[clean].keys(), clean)

    def test_a_sorry_in_a_proof_doesnt_change_a_fingerprint(self):
        after = self.edited("ReviewExample/Multiples/Lcm.lean", "theorem gcd_divides_mul (a b : Nat) : Divides (gcd a b) (a * b) :=\n  ⟨lcm a b, (gcd_mul_lcm a b).symm⟩",
                            "theorem gcd_divides_mul (a b : Nat) : Divides (gcd a b) (a * b) :=\n  sorry")
        self.assertEqual(self.changed(after, "own"), set())
        self.assertTrue(after["declarations"]["ReviewExample.gcd_divides_mul"]["sorry"])

    def test_native_decide_is_reported_as_an_axiom(self):
        after = self.edited(GCD, "end ReviewExample", "theorem big : 2 ^ 10 = 1024 := by native_decide\n\nend ReviewExample")
        self.assertEqual(after["declarations"]["ReviewExample.big"]["axioms"], ["native_decide"])

    def test_a_deprecated_alias_is_listed_as_a_rename(self):
        self.assertEqual(self.before["renames"], {"ReviewExample.gcd_dvd": "ReviewExample.gcd_divides"})

    def test_matches_the_recorded_graph(self):
        """A failure on a new Lean version matters: if only fingerprints differ, upgrading Lean
        would flag every mark in every project. Review the difference, then record it with
        `python3 tests/test_graph.py --record`."""
        golden = json.loads(GOLDEN.read_text())
        self.assertEqual(self.before.keys(), golden.keys())
        for key in golden:
            if key != "lean":
                self.assertEqual(self.before[key], golden[key], key)


if __name__ == "__main__":
    import sys
    if "--record" in sys.argv:
        GraphTest.setUpClass()
        GOLDEN.write_text(json.dumps(GraphTest.before, indent=1, sort_keys=True) + "\n")
        print(f"recorded {GOLDEN}")
    else:
        unittest.main()
