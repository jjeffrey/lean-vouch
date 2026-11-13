# How it works

- **Dependencies** come from the compiled declarations (`Expr.getUsedConstants`), not from the
  source text. Compiler-generated helpers (`match_1`, `brecOn`, `ctorIdx`, …) are looked through:
  a declaration depends on whatever its helpers use, and their content counts as part of its own.
  Constructors and structure fields count as their type.
- **Library uses:** a class method like `LE.le` (behind `≤`) stands for nothing by itself; the
  instance it is used with stands for the operations it provides, so `≤` on floats is recorded as
  `Float.le`. Library uses that are Core Lean plumbing are kept but tagged, in groups (`plumbingGroup?` in `DeclGraph.lean`): `compiler`,
  what Lean compiles recursion, matching and `deriving` into (`PProd`, `PUnit`, `Function.comp`,
  `HEq`, …); `notation`, what `if`, `match`, `do`, `throw` and `try` mean (`ite`, `Except.bind`,
  …); `internals`, formatting and string-slice internals; and `theorems`, library theorems used
  inside a definition's body. Each group is hidden by default and can be switched on under "show
  plumbing" in the pages and on the diagram page (remembered per browser). A hidden group is
  neither listed nor counted when deciding whether a mark needs re-checking.
- **What a reviewer reads:** each project declaration's signature, printed by Lean from inside the
  declaration's own namespace, so it shows every argument, including those a `variable` adds, and
  its docstring. Pages without doc-gen4 show these, and quiz mode asks from them. For a structure or
  class the graph also lists its fields, and for an inductive type its constructors, each with its
  type and docstring, which those pages show under the statement; a parent it extends is one field,
  `toParent`.
- **Gaps:** what a declaration's proof rests on is worked out as `#print axioms` does, through proofs
  and all, and each `sorry` is named by the declaration it's written in. Each step toward a `sorry`
  is kept, and no other proof dependency, so a project with none has nothing extra in its graph. This
  is the one thing that looks inside proofs, so it never affects fingerprints.
- **Fingerprints** are hashes of Lean's expressions. A mark stores the fingerprint of the
  declaration and of everything below it when it was made, worked out by the server however the
  mark was made; the page compares them with the current graph. A `partial def` counts its body, a
  structure its default field values and field names, an inductive type its constructors' names, and
  a module doc its text. Names of helpers Lean generates don't count, only what's in them, and nor
  does where a `panic!`, `assert!` or `sorry` was written, though Lean records it in the expression. A mark also
  records the Lean version it was made on. What a declaration uses through a
  deprecated name counts as part of it. A library definition's fingerprint takes in everything it's built on, down
  through the library, leaving out proofs, so upgrading Lean or a dependency flags marks when something
  beneath what they use really changed, even if the change is harmless.

## Limits

- Fingerprints use Lean's expression hash, which is 32 bits and not cryptographic. An accidental
  collision is very unlikely, but someone could craft one, so don't rely on them against an adversary.
- A fingerprint depends on what was reviewed. A declaration outside the modules named counts as a
  library one, which is fingerprinted with everything below it, so a run over less of a project than
  before flags marks that rest on what it left out, though nothing changed. A run over the same part
  clears them.
- Binder names outside a type's fields don't count, so renaming an argument flags nothing.
- On a large project, a page loads everything below every mark, to compare it. With many marks
  high up in a project this can approach the size of the whole graph.
- Instances that `deriving` writes for `Repr`, `DecidableEq`, `BEq`, `Hashable`, `Inhabited` and
  `Nonempty` are hidden, and left out of the counts, the diagram and the quiz. Lean records them
  differently from written code, but an instance a project's own macro makes looks the same, so
  it's hidden too. "hide generated" on a docs page shows them.
- Library tracking covers the library definitions listed under a declaration. Type classes and instances
  stand for the operations they provide, so a change to a class itself isn't followed.
- Lean v4.30 to v4.35 (see [Versions](#versions)). `DeclGraph.lean` uses Lean's internals, which change between versions. It works with and without the module system (`module`); it reads private data, so unexposed bodies are covered.
- `DeclGraph.lean` is `unsafe` only to load the imported modules' environment extensions (which
  record instances and deprecations); it reads, never writes.
- The diagram loads d3 and d3-graphviz from a CDN, and so needs an internet connection.
- Above 5,000 declarations the server sends each page what it needs: a module's declarations, what's
  below them, and what directly uses them. So on a library page "Used by the project in" lists only
  what's loaded, and quiz mode still downloads the whole graph. For all of Mathlib the graph takes under
  ten minutes and about 6 GB to build and is about 190 MB; the server needs a few seconds and about 1 GB
  to read it.
- The server runs on your own machine: it binds to 127.0.0.1 and refuses requests from other sites, but has no login, so anyone logged in to the machine can reach it. People working together each run their own, and share marks with git.
- Splits and merges can't be remapped automatically; their marks show up as orphans.

## Versions

Three things are versioned separately:

- **lean-vouch** has a version (`lean-vouch --version`) and a [changelog](../CHANGELOG.md). Until 1.0, a minor
  version may need a new toolchain or a regenerated graph.
- **Lean and doc-gen4.** `DeclGraph.lean` uses Lean's internals and `review.js` uses doc-gen4's HTML, so
  each release of lean-vouch is tested on a range of toolchains, with the oldest and newest in CI:

  | lean-vouch | Lean and doc-gen4 |
  | --- | --- |
  | 0.1.0 | v4.30.0 to v4.35.0 |
  | 0.1.1 | v4.30.0 to v4.35.0 |
  | 0.1.2 | v4.30.0 to v4.35.0 |

  `lean-vouch` stops on an older Lean, since the graph needs newer library functions, and warns on a newer one.

- **Marks files** are the review record, so their format changes only with a migration. `marks/ID.json` is
  `{"format": 3, "name": "...", "marks": {declaration: entry}}`, where the filename is the person's id and the
  name is only for display. Quiz answers are in `quiz/ID.json`, as `{"format": 3, "answers": {...}}`.
  The theorems chosen for the challenge are in `challenge.json`, as `{"format": 1, "theorems": [...]}`.
  The graph stores `format` and the Lean version that made it; regenerate it freely. The server refuses a format
  it doesn't read rather than guessing.

## Pieces

| Path | What it does |
| --- | --- |
| `lean_vouch/cli.py` | The `lean-vouch` command: sets up `.lean-vouch/`, builds the graph and, if asked, the docs, writes challenges, and starts the server. `review.py` runs it from a clone. |
| `lean_vouch/graph/DeclGraph.lean` | Run inside a Lake project, compiled once per Lean version; writes the declaration graph as JSON: the project's modules and their imports, each declaration's dependencies, library uses, signature, docstring and fingerprint, `own`. |
| `lean_vouch/challenge.py` | Writes a challenge for `lake comparator` from the graph and the source, and checks it by fingerprint. |
| `lean_vouch/server.py` | Serves the diagram and quiz pages, and doc-gen4's output with the review script added; keeps each person's marks and notes in their own file and gives pages one view of them; applies renames; starts rebuilds and challenges for pages. Python standard library only. |
| `lean_vouch/web/review.js`, `review.css` | The controls in the doc pages. |
| `lean_vouch/web/links.js`, `math.js`, `lean.js` | What the pages share: rewriting links, noticing changes and rebuilds while a page is open, typesetting math, and coloring Lean code and rendering docstrings without doc-gen4. |
| `lean_vouch/web/quiz.html` | Quiz mode: blind descriptions of declarations at the edge of what you have marked. |
| `lean_vouch/web/graph.html` | The dependency diagram, laid out by Graphviz (via d3-graphviz, as on Lean blueprint pages). It zooms with the scroll wheel and pans by dragging. |
