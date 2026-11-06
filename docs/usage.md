# Using lean-review

The first run adds two folders, each with its own `.gitignore`, and doc-gen4's packages to
`.lake/packages`:

- `docbuild/`, which builds the docs with the [doc-gen4](https://github.com/leanprover/doc-gen4) version
  that matches your `lean-toolchain`, so your project gains no dependency.
- `review/`, with the declaration graph and `marks.json`. Commit `review/marks.json`: it is the review
  record.

The first docs build takes time, because doc-gen4 documents everything the project imports:

| Project | First run | Disk |
| --- | --- | --- |
| No dependencies | about 7 minutes, mostly compiling doc-gen4 | about 1 GB |
| Using Mathlib | about 35 minutes | 2 GB |

It doesn't recompile Mathlib. The graph step needs about 4 GB of memory for a Mathlib project. Lake's `[n/m]` total grows as it finds more to build, so the progress can seem to go backwards.

When the project moves to another Lean version, the next run moves `docbuild/` to the matching doc-gen4 and builds the docs again, which takes about as long as the first run.

| Option | Meaning |
| --- | --- |
| `review.py MyProject MyProjectTests` | Also review another root and the modules under it, such as a second library or modules the first doesn't import. Without it they aren't covered. Each must be a module Lake can build. |
| `--port N` | Serve on another port, for reviewing two projects at once (default 8797) |
| `--reviewer NAME` | The name on your notes (default your git name) |
| `--no-serve` | Only rebuild the docs and graph |
| `--clean` | Delete docs pages left by renamed or removed modules, which doc-gen4 keeps |

Source links open in VS Code, at the declaration's line. Set `DOCGEN_SRC=github` before the first
run for GitHub links instead.

To open links in docstrings from your own copies, add `review/links.local.json`, as
`{"https://example.org/paper.pdf": "your copy's URL"}`. The page rewrites them when you view the
docs, adding `?page=N` if the link text ends in `, p. N`.
