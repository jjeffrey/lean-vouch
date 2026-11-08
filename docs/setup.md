# Setup and options

The first run adds doc-gen4's packages to `.lake/packages`, and a `.lean-vouch` folder:

```text
.lean-vouch/
├── .gitignore        tracked
├── marks/            tracked: each person's marks and notes
│   └── ada-lovelace-3f9a.json
├── quiz/             ignored: each person's quiz answers
├── graph.json        ignored
├── build.json        ignored: how the latest build went, for open pages
├── build.log         ignored: the latest build's output
├── graph-tool/       ignored: the graph program, compiled for the project's Lean
├── challenge/        ignored: the latest challenge, from --challenge
├── links.local.json  ignored, optional
└── docbuild/         ignored
    └── .lake/build/doc/   the HTML
```

`docbuild` builds the docs with the [doc-gen4](https://github.com/leanprover/doc-gen4) version that
matches your `lean-toolchain`, so your project gains no dependency. Everything but the marks can be
rebuilt or is yours alone, so `.lean-vouch/.gitignore` ignores it. To keep marks out of git too, edit
that file: it's only written on the first run. In a public repository, committed notes are public.

The first docs build takes time, because doc-gen4 documents everything the project imports:

| Project | First run | Disk |
| --- | --- | --- |
| No dependencies | about 7 minutes, mostly compiling doc-gen4 | about 1 GB |
| Using Mathlib | about 35 minutes | 2 GB |
| All of Mathlib | about 70 minutes | 4 GB, and about 6 GB of memory |

Warnings from the project's own build are left out, since `lake build` shows them, though a failing build shows everything. It doesn't recompile Mathlib. The graph step needs up to about 6 GB of memory for a project on the scale of Mathlib. Lake's `[n/m]` total grows as it finds more to build, so the progress can seem to go backwards.

When the project moves to another Lean version, the next run moves `docbuild/` to the matching doc-gen4 and builds the docs again, which takes about as long as the first run. When the project's dependencies change on the same Lean, the next run's docs follow them.

| Option | Meaning |
| --- | --- |
| `lean-vouch` | Review every library in the project, tests included |
| `lean-vouch MyLibrary My.Module` | Review only these libraries and modules. Use this to leave out a library that doesn't build, or a project's large test libraries. Libraries are imported together for the graph, so also leave out one whose names clash with another's, such as exercises and their solutions. Reviewing a smaller part of a project than before can flag marks that haven't really changed: see [the limits](internals.md#limits). |
| `--port N` | Serve on another port, for reviewing two projects at once (default 8797) |
| `--name NAME` | The name shown on your marks and notes, which can change (default your git name) |
| `--id ID` | Who you are, as the name of your file: lowercase letters, digits and hyphens. It should never change, and comes by default from your git name and email, which tells two people of the same name apart. A name with no Latin letters needs one chosen. If your git name or email changes, pass your old id to stay the same person; your marks and quiz answers are under it. |
| `--rebuild` | Rebuild the docs and graph without starting a server |
| `--clean` | Delete docs pages left by renamed or removed modules, which doc-gen4 keeps |
| `--challenge THEOREM` | Write a [challenge for `lake comparator`](challenge.md) with this theorem, and what its statement rests on; can be repeated |

Source links open in VS Code, at the declaration's line. Set `DOCGEN_SRC=github` before the first
run for GitHub links instead.

To open links in docstrings from your own copies, add `.lean-vouch/links.local.json`, as
`{"https://example.org/paper.pdf": "your copy's URL"}`. The page rewrites them when you view the
docs, adding `?page=N` if the link text ends in `(p. N)` or `(page N)`.

## Rebuilding

Leave the server running. When the code has changed since the last build, open pages say so, with
a **Rebuild** button, which reviews the same libraries and modules as the last run. Or run
`lean-vouch` again, or `lean-vouch --rebuild`, in another terminal. An agent can do either when it
has finished its changes, the button by:

```bash
curl -X POST http://127.0.0.1:8797/review/rebuild -H "Content-Type: application/json" -d '{}'
```

It builds once, when asked, and never on every save. Open pages say how far a rebuild has got, and
offer to reload when it's done, so a note you're writing isn't lost, and ask first if reloading would
lose one. A page from before a rebuild can't be used to mark until it's reloaded, since it may not
show what's there now, though notes still save. A build's output also goes to
`.lean-vouch/build.log`, which pages link to. If a build fails, changes are still tracked from the
last good build, though a failure during the docs step can leave some pages newer than that.

Marks and notes need no rebuild. They appear on open pages by themselves, from anyone, within a few seconds.

## Working with others

Each person's marks are their own, and the diagram shows yours unless you choose someone else's or
everyone's. Commit `.lean-vouch/marks/` to share them. A mark that's out of date is flagged for its
owner alone, and differing current marks show as a disagreement.

A `?` alone is your own reminder. With a note, it asks the others. A declaration needs your attention,
shown by a `!`, when someone is waiting on you: they asked, wrote more under their `?`, or answered
yours. It clears when you mark the declaration or reply.

Marks are shared with plain git, and lean-vouch never commits or pushes for you. Commit your file
with your work, and pull to get everyone else's, which appear on open pages by themselves. Each person writes
only their own file, so marks don't conflict. If someone else's file can't be read, such as during a
merge conflict, pages say so and carry on without it.

An assistant, human or not, is another person with its own file. By convention it leaves notes and `?`s,
and doesn't mark ✓ or ✗: the record says what a person has checked. While the server runs, it can write
as itself:

```bash
curl -X POST http://127.0.0.1:8797/review/data -H "Content-Type: application/json" \
  -d '{"id": "claude", "author": "Claude", "name": "MyProject.foo", "status": "question", "note": "Is 0 meant to be included?"}'
```
