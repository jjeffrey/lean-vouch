# lean-review

Lean checks proofs. lean-review helps a person check the rest: that definitions and theorem
statements mean what they should. It adds review tools to a project's doc-gen4 pages.

![a dependency diagram colored by review marks, with a sorry and what relies on it](docs/images/sorry-diagram.png)

## Try it

You need Lean v4.30 or newer through elan, and Python 3. From the root of a Lake project:

```bash
git clone https://github.com/jjeffrey/lean-review ../lean-review
python3 ../lean-review/review.py
```

Then open <http://127.0.0.1:8797/review/diagram>. The first run takes about 7 minutes, or about 35
with Mathlib. Later runs take seconds. Your marks and notes are in `.lean-review/marks/`, which git
tracks unless you opt out; quiz answers stay local.

> [!WARNING]
> Only run this on code you trust. Building a Lean project runs its macros and build scripts, and the
> graph step runs its initializers, so reviewing a stranger's project runs their code on your machine.

## What you get

- **Marks and notes** on every declaration, each person's in their own file.
- **Flags** when something you marked, or anything it depends on, changes.
- **A dependency diagram** colored by your marks, which handles projects as large as Mathlib.
- **`sorry` tracing:** everything that relies on one, even through proofs.
- **A quiz:** read a statement with its name hidden, say what it means, then compare.

It's a review aid, not a checker: for soundness, use
[lean4checker](https://github.com/leanprover/lean4checker) or Comparator. It's early, and has been used
on small projects.

## More

[Features](docs/features.md) · [Usage](docs/usage.md) · [The quiz](docs/quiz.md) ·
[How it works](docs/internals.md) · [Developing](docs/development.md) ·
[Changelog](CHANGELOG.md)

Apache License 2.0. Copyright 2026 Jonathan Jeffrey.
