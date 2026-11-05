# Changelog

## 0.1.0 (unreleased)

First public version. Works with Lean v4.30 to v4.35, with the matching doc-gen4.

- Marks, note threads, change tracking, library marks, orphaned notes, the dependency diagram and the quiz.
- The diagram opens large projects as folders, click to expand; tested on all of Mathlib.
- The quiz can be limited to a folder or module, or asked in random order, and records whether your reading matched.
- `sorry` and non-standard axioms are shown wherever they reach, even through proofs.
- `review.py` sets a project up and serves it in one command.
- The graph lists exactly what the docs list, using doc-gen4's rule for what a person wrote.
- A list of folders and modules to open, search and pick from; shift-click for everything something depends on and that uses it.
- `review.py` keeps `docbuild/` on the project's Lean version when that changes.
- The server answers only its own pages, so other sites open in the browser can't read or change a review.
- `marks.json` and the graph record a format; the server refuses a format it doesn't read, and a `marks.json` it can't parse, instead of overwriting it.
