# Working on lean-review

lean-review is a tool for human review of Lean definitions and statements in doc-gen4 pages.
[docs/development.md](docs/development.md) covers the example project and the tests, and
[docs/internals.md](docs/internals.md) covers how it works.

- Run the tests before committing, and don't commit on a failing run.
- Read any change to `tests/golden/` before recording it.
- If a change affects the screenshots, regenerate them with `example/screenshots.sh`.
- Take examples from `example/` or a public project.
- Keep machine details out of the repository: no absolute paths or usernames. Take a location from an
  argument or environment variable.
