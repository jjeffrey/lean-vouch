# Changelog

## 0.1.0 (unreleased)

First public version. Works with Lean v4.30 to v4.35, with the matching doc-gen4.

- Marks, note threads, change tracking, library marks, orphaned notes, the dependency diagram and quiz mode.
- The diagram opens large projects as folders, click to expand; tested on all of Mathlib.
- Quiz mode can be limited to a folder or module, or asked in random order, and records whether your reading matched.
- `sorry` and non-standard axioms are shown wherever they reach, even through proofs.
- `lean-review` sets a project up and serves it in one command. It installs with pip or pipx.
- The graph lists exactly what the docs list, using doc-gen4's rule for what a person wrote.
- A list of folders and modules to open, search and pick from; shift-click for everything something depends on and that uses it.
- `lean-review` keeps its docs build on the project's Lean version when that changes.
- The server answers only its own pages, so other sites open in the browser can't read or change a review.
- `lean-review` reviews every library in the project by default, or the libraries and modules you name.
- Projects whose libraries set `srcDir` show their declarations' code.
- Each person has their own marks file, shown together: your marks by default, anyone else's or everyone's in the diagram, with disagreements marked. Someone's `?` needs your attention until you mark or reply. An older `review/marks.json` is imported.
- Marks and notes appear on open pages by themselves. The server can stay up through a rebuild, and pages offer to reload when it's done, or say so if it failed.
- Everything lives in `.lean-review/`, which git ignores but for the marks. Quiz answers stay local.
- Marks files and the graph record a format; the server refuses a format it doesn't read, and a marks file it can't parse, instead of overwriting it.
