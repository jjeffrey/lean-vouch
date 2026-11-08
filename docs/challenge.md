# Challenges for comparator

A theorem means what its statement says, given what each definition in it means. A *challenge* holds
just that: the statements of the theorems you care about, and the project's definitions they rest on,
with no proofs. `lake comparator`, built into Lake, then checks that a project proves exactly those
statements, using only the axioms you allow, so the rest of the project, its proofs and helper lemmas,
can change without anyone reviewing it again.

lean-vouch helps build one. A project has one challenge, shared by everyone reviewing it:

1. **Choose its theorems.** Tick "in the challenge" under a theorem on its docs page, or in its sidebar
   in the diagram. The choice is saved in `.lean-vouch/challenge.json`, which git tracks, so it's the
   project's, not one person's.
2. **Review what's beneath them.** The diagram's **challenge** button lists the chosen theorems, how
   many declarations their statements rest on, and which of those you haven't marked ✓ as they are
   now. "Show in diagram" colors them all.
3. **Write it**, with "Write challenge" there, or from the terminal:

```bash
lean-vouch --challenge
```

4. **Check the project against it** with comparator, which is Lean's checker and so yours to run:

```bash
lake comparator
```

To choose theorems from the terminal instead, name them: `lean-vouch --challenge MyProject.main_theorem
MyProject.other_theorem` makes those the chosen theorems, then writes the challenge.

In a project set up before lean-vouch 0.1.1, git ignores `.lean-vouch/challenge.json` until you add the
line `!challenge.json` to `.lean-vouch/.gitignore`. lean-vouch says so when you choose from the terminal.

Writing the challenge does everything comparator needs:

- **It builds first** if the code has changed since the last build: the project and the graph, not
  the docs.
- **What goes in:** each theorem's statement, with `sorry` for its proof, and every project
  declaration those statements use, recursively, copied whole from the source, doc comments
  included. Library imports carry over; the project's own modules don't, since the challenge
  replaces them.
- **The check:** a declaration copied out of its file can mean something else, through an `open`
  or a `variable` it relied on. So lean-vouch compiles the challenge and fingerprints it as it does
  the project, and stops, leaving the project alone, if any declaration differs.
- **Into the project:** `Challenge.lean` beside the project's modules, `comparator.json` at its root,
  and a `Challenge` library in its lakefile, if it isn't there. A `Challenge.lean` lean-vouch didn't
  write is left alone, and anything you add to `comparator.json`, such as more permitted axioms or
  external kernels, is kept. Commit all three, so anyone, and CI, can run `lake comparator`.
- **Your review:** it lists the declarations in the challenge you haven't marked ✓ as they are now.
  The challenge is only as good as that review.

The challenge repeats the project's names, so lean-vouch leaves out the library that
`comparator.json` names as the challenge, unless you name it.

`lake comparator` needs Lean v4.35 or newer. It builds the project inside a `bwrap` sandbox, which
needs Linux. On macOS, checking your own project needs `lake comparator --inadvisably-no-sandbox`,
and lean-vouch says so: the sandbox protects you from a solution you don't trust, and your own
project's build already runs with your permissions. Check anyone else's work on Linux, such as in CI,
where every push can be checked against the challenge.

Not yet supported: private declarations, mutually recursive ones, and projects that use the module
system (`module`).
