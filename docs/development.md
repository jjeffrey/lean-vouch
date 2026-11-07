# Developing lean-vouch

To run it from a clone, use `python3 path/to/lean-vouch/review.py`, which takes the same options as
`lean-vouch`.

## Example

`example/` is a small Lean project to try lean-vouch on and to develop it against: Euclid's
algorithm in `Divides` (a definition of divisibility and its lemmas), `Zero` (zero divides only
zero) and `Gcd` (`gcd`, proofs that it divides both numbers and that every common divisor divides it,
and a deprecated old theorem name), then a `Multiples` folder: `Lcm`, whose key theorem is a `sorry`,
and `Coprime`, with one theorem that relies on it and one that doesn't. Last, `Prime`, a
definition that lets 1 count as prime, for the [walkthrough](usage.md). `example/review/seed.py`
writes example marks that exercise each feature: a conversation, a library definition marked as
understood, a rename, a mark to re-check, an orphan, a question from an assistant, and a rejected
definition.

```bash
cd example
./review.sh            # build the docs and graph, serve http://127.0.0.1:8798/ReviewExample.html
./review.sh --reset    # start again from the example marks
./screenshots.sh       # regenerate docs/images (needs Google Chrome)
```

The first docs build compiles doc-gen4 and takes several minutes. Marks made while trying it go to
`example/review/marks/`, which is not committed.

## Releasing

Check that CI passes, set `VERSION` in `lean_vouch/server.py`, date the [changelog](../CHANGELOG.md),
and push a tag on that commit, such as `v0.1.0`. `.github/workflows/publish.yml` then runs the tests,
checks that the tag matches `VERSION`, and publishes to PyPI through Trusted Publishing, so there's no
token and nothing to upload by hand. A version can't be replaced once it's on PyPI.

## Tests

```bash
(cd example && ./review.sh)          # once, to build the docs and graph; stop it with Ctrl-C
python3 -m unittest discover -s tests
```

| File | Needs | Checks |
| --- | --- | --- |
| `tests/test_server.py` | Python | Marks and notes are saved and read back, each in its owner's file; renames and moves never overwrite a mark; a broken or newer marks file is refused and left alone; only the tool's own pages are answered. |
| `tests/test_review.py` | Python | What the command checks and how it fails, with a stand-in for `lake`; who you are, which modules are reviewed, and what git ignores. |
| `tests/test_graph.py` | Lean | On edited copies of the example: changing a proof or a docstring changes no fingerprint, and changing a statement or definition changes the right ones. The graph is compared with `tests/golden/`. |
| `tests/test_pages.py` | Example docs, Chrome, network | The controls attach to doc-gen4's pages, marks are flagged exactly when something changed, the diagram draws, and clicking the buttons does what it should, from inside the page. |

Tests skip what they can't run. If the golden graph differs after a Lean upgrade, read the difference
before recording it (`python3 tests/test_graph.py --record`): fingerprints that change on their own would
flag every mark in every project.
