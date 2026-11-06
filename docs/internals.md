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
- **Gaps:** what a declaration's proof rests on is worked out as `#print axioms` does, through proofs
  and all, and each `sorry` is named by the declaration it's written in. Each step toward a `sorry`
  is kept, and no other proof dependency, so a project with none has nothing extra in its graph. This
  is the one thing that looks inside proofs, so it never affects fingerprints.
- **Fingerprints** are hashes of Lean's expressions. A mark stores the fingerprint of the
  declaration and of everything below it when it was made; the page compares them with the
  current graph.

## Limits

- Lean v4.30 to v4.35 (see [Versions](#versions)). `DeclGraph.lean` uses Lean's internals, which change between versions. It works with and without the module system (`module`); it reads private data, so unexposed bodies are covered.
- `DeclGraph.lean` is `unsafe` only to load the imported modules' environment extensions (which
  record instances and deprecations); it reads, never writes.
- The diagram loads d3 and d3-graphviz from a CDN, and so needs an internet connection.
- Above 5,000 declarations the server sends each page what it needs: a module's declarations, what's
  below them, and what directly uses them. So on a library page "Used by the project in" lists only
  what's loaded, and quiz mode still downloads the whole graph. For all of Mathlib the graph takes under
  ten minutes and about 4 GB to build and is about 190 MB; the server needs a few seconds and about 1 GB
  to read it.
- The server runs on your own machine: it binds to 127.0.0.1 and refuses requests from other sites, but has no login, so anyone logged in to the machine can reach it. People working together each run their own, and share marks with git.
- Splits and merges can't be remapped automatically; their marks show up as orphans.

## Versions

Three things are versioned separately:

- **lean-review** has a version (`lean-review --version`) and a [changelog](../CHANGELOG.md). Until 1.0, a minor
  version may need a new toolchain or a regenerated graph.
- **Lean and doc-gen4.** `DeclGraph.lean` uses Lean's internals and `review.js` uses doc-gen4's HTML, so
  each release of lean-review is tested on a range of toolchains, with the oldest and newest in CI:

  | lean-review | Lean and doc-gen4 |
  | --- | --- |
  | 0.1.0 | v4.30.0 to v4.35.0 |

  `lean-review` stops on an older Lean, since the graph needs newer library functions, and warns on a newer one.

- **Marks files** are the review record, so their format changes only with a migration. `marks/ID.json` is
  `{"format": 3, "name": "...", "marks": {declaration: entry}}`, where the filename is the person's id and the
  name is only for display. Quiz answers are in `quiz/ID.json`, as `{"format": 3, "answers": {...}}`.
  The graph stores `format` and the Lean version that made it; regenerate it freely. The server refuses a format
  it doesn't read rather than guessing.

## Pieces

| Path | What it does |
| --- | --- |
| `lean_review/cli.py` | The `lean-review` command: sets up `.lean-review/`, builds the docs and graph, and starts the server. `review.py` runs it from a clone. |
| `lean_review/graph/DeclGraph.lean` | Run inside a Lake project; writes the declaration graph as JSON: the project's modules and their imports, each declaration's dependencies, library uses, and fingerprints (`own`, and `deep`, which includes everything below it). |
| `lean_review/server.py` | Serves the doc-gen4 output with the review script added; keeps each person's marks and notes in their own file and gives pages one view of them; applies renames. Python standard library only. |
| `lean_review/web/review.js`, `review.css` | The controls in the pages. |
| `lean_review/web/links.js`, `math.js` | What the pages share: rewriting links, noticing changes while a page is open, and typesetting math. |
| `lean_review/web/quiz.html` | Quiz mode: blind descriptions of declarations at the edge of what you have marked. |
| `lean_review/web/graph.html` | The dependency diagram, laid out by Graphviz (via d3-graphviz, as on Lean blueprint pages). It zooms with the scroll wheel and pans by dragging. |
