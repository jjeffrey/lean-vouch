# Changelog

## 0.1.2 (2026-11-12)

A first run builds only what `lake build` does, and reviewing reads more easily: a ~ mark for "looks right at a glance", stars, a module's whole code beside the diagram, and statements laid out as in doc-gen4. Works with Lean v4.30 to v4.35. Everyone sharing marks should upgrade together, since 0.1.1 can't read a ~.

- With no libraries named, lean-vouch reviews what `lake build` builds: the project's default targets, or every library if it sets none. Before, it reviewed and built every library, tests and examples included, which could take much longer than `lake build` or fail where it doesn't. It says which it chose, and how to name others. If you've been reviewing a library that isn't a default target, name it, as in `lean-vouch MyLib Tests`, or its marks will show as orphans.
- A fourth mark, ~, for "looks right at a glance" but not checked closely. It doesn't disagree with a ✓, asks nothing of anyone, and doesn't count as agreeing in a challenge. lean-vouch 0.1.1 can't read a marks file containing one and leaves that person out, so everyone sharing marks should upgrade together. From now on, a mark a later version adds shows as unmarked instead.
- A star, apart from the mark, for declarations that are especially useful. Everyone sees who starred what, and the diagram shows a ★ on each, and on a closed folder or module with how many are inside. Assistants send `"star": true`.
- Without doc pages, a declaration's statement is laid out as doc-gen4 does: its name, then its arguments, indented and broken only between them, then its type on a line of its own. A short definition's body follows, as written, since that is what it means.
- Opening or Cmd-clicking a module in the diagram shows all its code in the sidebar, as in its file, with each declaration's mark down its side; its name where it's declared opens it. Theorems' proofs are folded away. A declaration's module, under its name, goes back to it.
- "Needs your attention" can be dismissed without a mark or a note, and comes back when someone writes there again. Assistants send `"seen": true`.
- The diagram's folders, diagram and details are divided by a line instead of each having a border, and dragging a line makes the panel beside it wider or narrower. The width is remembered per browser; double-click the line to go back to the default.
- Without doc pages, the diagram's sidebar shows a structure's or class's fields, and an inductive type's constructors, each with its type and docstring, under its statement. Before, they were only in its code. A class is now called a class, not a structure.
- An assistant can ask what's waiting on it: `/review/data?id=ID` gives the view from that person's side, with who is waiting on them. The setup guide has a section for assistants: asking, replying, finding what's waiting, and the file format for when the server isn't running.
- Only the person the server runs as can mark ✓ or ✗ through it. Others, such as assistants, leave notes and `?`s.
- Searching the diagram's list of folders and modules filters the tree, matching each by its own name, with its folders above it once, and marks the match. It no longer lists every path that contains the text.

## 0.1.1 (2026-11-07)

The diagram is now the starting point: doc pages are optional, so a first run takes minutes, and challenges for `lake comparator` can be chosen and written from it. Works with Lean v4.30 to v4.35.

- Doc pages are now optional, and a new project starts without them: the diagram is ready in a minute or two, or a few more with Mathlib, instead of about 35. `--docs` adds them and `--no-docs` stops them, and the project remembers the choice; a project set up before 0.1.1 keeps its docs.
- Without doc pages, the diagram's sidebar shows a declaration's docstring, its statement as Lean printed it (with what `variable`s add), and its code, colored, with names linked. Quiz mode works from the same.
- Challenges for `lake comparator`: choose theorems in the pages, shared through `.lean-vouch/challenge.json`, and write their statements with the project's definitions beneath them, checked by fingerprint to say what the project says. Writing one builds first if the code changed, and puts `Challenge.lean`, `comparator.json` and a `Challenge` library in the project, ready for `lake comparator`, which stays yours to run. From the page or with `lean-vouch --challenge`. The challenge, as named in `comparator.json`, is left out of review. The diagram's challenge panel shows the challenge as it is in the project, and says when it's out of date. An existing project's `.lean-vouch/.gitignore` needs the line `!challenge.json` to share the choice.
- Pages say when the code has changed since the last build, with a button to rebuild, and show how far a rebuild has got and how long each step has taken. A build's output also goes to `.lean-vouch/build.log`.
- A rebuild skips the declaration graph when nothing it's made from has changed: on a project that imports Mathlib, seconds instead of a minute or two. A busy port is reported before building, not after.
- The graph program is compiled once for each Lean version, so a rebuild with nothing new takes a couple of seconds instead of about ten. Each step says what it's doing and counts up while it runs.
- Building the declaration graph shows a progress bar through its phases, in the terminal and on open pages, and leaves a line with each phase's time.
- A run no longer shows the project's own build warnings, which `lake build` shows. A failing build still shows everything.
- The diagram's side panels can be hidden, for a larger diagram.
- The diagram takes `?open=` with a list of modules, opening only the folders on the way to them.
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
