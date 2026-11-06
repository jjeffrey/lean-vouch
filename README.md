# lean-review

Lean checks proofs. lean-review helps a person check the rest: that definitions and theorem
statements mean what they should. It adds review tools to a project's doc-gen4 pages.

![a dependency diagram colored by review marks, with a sorry and what relies on it](https://raw.githubusercontent.com/jjeffrey/lean-review/main/docs/images/sorry-diagram.png)

## Try it

You need Lean v4.30 or newer through elan, and Python 3.11 or newer. From the root of a Lake project:

```bash
pipx install lean-review
lean-review
```

With uv, install it with `uv tool install lean-review`.

Then open <http://127.0.0.1:8797/review/diagram>. The first run takes about 7 minutes, or about 35
with Mathlib. Later runs take seconds. Your marks and notes are in `.lean-review/marks/`, which git
tracks unless you opt out; quiz answers stay local.

> [!WARNING]
> Only run this on code you trust. Building a Lean project runs its macros and build scripts, and the
> graph step runs its initializers, so reviewing a stranger's project runs their code on your machine.

## What you get

- **Marks and notes** on every declaration, each person's in their own file.
- **Change tracking:** flags when something you marked, or anything below it, changes, and shows the
  way down to what changed.
- **A dependency diagram** colored by your marks.
- **`sorry` tracing:** everything that relies on one, even through proofs.
- **Quiz mode:** read a statement with its name hidden, say what it means, then compare.
- **Working together:** others' marks and questions beside yours, from people or assistants, live on
  open pages.

lean-review is a review aid, not a checker: for soundness, use `lake check` or `lake comparator`, in
Lean v4.35 or newer. lean-review is still early: it handles projects as large as Mathlib, but has
mostly been used to review small ones.

## More

[Features](https://github.com/jjeffrey/lean-review/blob/main/docs/features.md) · [Usage](https://github.com/jjeffrey/lean-review/blob/main/docs/usage.md) · [Quiz mode](https://github.com/jjeffrey/lean-review/blob/main/docs/quiz.md) ·
[How it works](https://github.com/jjeffrey/lean-review/blob/main/docs/internals.md) · [Developing](https://github.com/jjeffrey/lean-review/blob/main/docs/development.md) ·
[Changelog](https://github.com/jjeffrey/lean-review/blob/main/CHANGELOG.md)

Apache License 2.0. Copyright 2026 Jonathan Jeffrey.
