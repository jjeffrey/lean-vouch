# Changelog

## Unreleased

- Challenges for `lake comparator`: choose theorems in the pages, shared through `.lean-vouch/challenge.json`, and write their statements with the project's definitions beneath them, checked by fingerprint to say what the project says. From the page or with `lean-vouch --challenge`. An existing project's `.lean-vouch/.gitignore` needs the line `!challenge.json` to share the choice.
- Pages say when the code has changed since the last build, with a button to rebuild, and show how far a rebuild has got and how long each step has taken. A build's output also goes to `.lean-vouch/build.log`.
- The graph program is compiled once for each Lean version, so a rebuild with nothing new takes a couple of seconds instead of about ten. Each step says what it's doing and counts up while it runs.
- A run no longer shows the project's own build warnings, which `lake build` shows. A failing build still shows everything.
- The diagram's side panels can be hidden, for a larger diagram.
- The diagram takes `?open=` with a list of modules, opening only the folders on the way to them.
- `--no-docs` skips the docs, for a diagram in a minute or two on a project that imports Mathlib. Marks, notes and challenges work without them; doc pages and quiz mode don't.
- Fixes: a project of one module shows it in the diagram's list, and a `partial def` is called that, not "opaque".
- Docs: a walkthrough of finding and fixing a definition that doesn't mean what it should, and sharper screenshots.

## 0.1.0 (2026-11-06)

First public version. Works with Lean v4.30 to v4.35, with the matching doc-gen4.

- Marks and note threads on every declaration, in the project's doc-gen4 pages, and marks on library definitions.
- Change tracking: marks are flagged when anything below them changes, with what changed traced and listed, and Accept change to keep a mark. Renames move marks, and marks left behind are listed as orphaned notes.
- A dependency diagram colored by your marks, which opens large projects as folders; tested on all of Mathlib.
- `sorry` and non-standard axioms shown wherever they reach, even through proofs.
- Quiz mode: read a statement with its name hidden, say what it means, then compare.
- Each person has their own marks file. The diagram shows yours, anyone's or everyone's, with disagreements marked, and someone's question needs your attention until you mark or reply.
- Marks and notes appear on open pages by themselves, and the server can stay up through a rebuild.
- `lean-vouch` sets a project up and serves it in one command, reviewing every library or those you name. It installs with pipx, pip or uv.
- Everything lives in `.lean-vouch/`, which git ignores but for the marks.
