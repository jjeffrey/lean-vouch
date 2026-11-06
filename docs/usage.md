# Using lean-review

The first run adds doc-gen4's packages to `.lake/packages`, and a `.lean-review` folder:

```text
.lean-review/
├── .gitignore        tracked
├── marks/            tracked: each person's marks and notes
│   └── ada-lovelace-3f9a.json
├── quiz/             ignored: each person's quiz answers
├── graph.json        ignored
├── links.local.json  ignored, optional
└── docbuild/         ignored
    └── .lake/build/doc/   the HTML
```

`docbuild` builds the docs with the [doc-gen4](https://github.com/leanprover/doc-gen4) version that
matches your `lean-toolchain`, so your project gains no dependency. Everything but the marks can be
rebuilt or is yours alone, so `.lean-review/.gitignore` ignores it. To keep marks out of git too, edit
that file: it's only written on the first run. In a public repository, committed notes are public.

The first docs build takes time, because doc-gen4 documents everything the project imports:

| Project | First run | Disk |
| --- | --- | --- |
| No dependencies | about 7 minutes, mostly compiling doc-gen4 | about 1 GB |
| Using Mathlib | about 35 minutes | 2 GB |

It doesn't recompile Mathlib. The graph step needs about 4 GB of memory for a Mathlib project. Lake's `[n/m]` total grows as it finds more to build, so the progress can seem to go backwards.

When the project moves to another Lean version, the next run moves `docbuild/` to the matching doc-gen4 and builds the docs again, which takes about as long as the first run.

| Option | Meaning |
| --- | --- |
| `lean-review` | Review every library in the project, tests included |
| `lean-review MyLibrary My.Module` | Review only these libraries and modules. Use this to leave out a library that doesn't build, or a project's large test libraries. |
| `--port N` | Serve on another port, for reviewing two projects at once (default 8797) |
| `--name NAME` | The name shown on your marks and notes, which can change (default your git name) |
| `--id ID` | Who you are, as the name of your file: lowercase letters, digits and hyphens. It should never change, and comes by default from your git name and email, which tells two people of the same name apart. A name with no Latin letters needs one chosen. If your git email changes, pass your old id to stay the same person. |
| `--rebuild` | Rebuild the docs and graph without starting a server |
| `--clean` | Delete docs pages left by renamed or removed modules, which doc-gen4 keeps |

Source links open in VS Code, at the declaration's line. Set `DOCGEN_SRC=github` before the first
run for GitHub links instead.

To open links in docstrings from your own copies, add `review/links.local.json`, as
`{"https://example.org/paper.pdf": "your copy's URL"}`. The page rewrites them when you view the
docs, adding `?page=N` if the link text ends in `(p. N)` or `(page N)`.

## Rebuilding

Leave the server running. After changing the code, run `lean-review` again, or `lean-review --rebuild`,
in another terminal. An agent can do the same when it has finished its changes. It builds once, when
you ask, and never on every save. Open pages say when a rebuild is running, and offer to reload when
it's done, so a note you're writing isn't lost. If a build fails, they keep showing the last good version.

Marks and notes need no rebuild. They appear on open pages by themselves, from anyone, within a few seconds.

## Working with others

Each person's marks are their own, and the diagram shows yours unless you choose someone else's or
everyone's. Commit `.lean-review/marks/` to share them. A mark that's out of date is flagged for its
owner alone, and differing current marks show as a disagreement.

A `?` alone is your own reminder. With a note, it asks the others. A declaration needs your attention,
shown by a `!`, when someone is waiting on you: they asked, wrote more under their `?`, or answered
yours. It clears when you mark the declaration or reply.

Marks are shared with plain git, and lean-review never commits or pushes for you. Commit your file
with your work, and pull to get everyone else's, which appear on open pages by themselves. Each person writes
only their own file, so marks don't conflict.

An assistant, human or not, is another person with its own file. By convention it leaves notes and `?`s,
and doesn't mark ✓ or ✗: the record says what a person has checked. While the server runs, it can write
as itself:

```bash
curl -X POST http://127.0.0.1:8797/review/data -H "Content-Type: application/json" \
  -d '{"id": "claude", "author": "Claude", "name": "MyProject.foo", "status": "question", "note": "Is 0 meant to be included?"}'
```
