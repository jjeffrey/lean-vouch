#!/bin/sh
# Build the example's docs and declaration graph, then serve them with review controls.
#   ./review.sh           serve http://127.0.0.1:8798/ReviewExample.html
#   ./review.sh --reset   start again from the example marks (review/seed)
set -e
cd "$(dirname "$0")"
lake build -q
(cd docbuild && DOCGEN_SRC=file lake build -q ReviewExample:docs)
lake env lean --run ../lean_vouch/graph/DeclGraph.lean ReviewExample > review/graph.json
if [ "$1" = "--reset" ] || [ ! -d review/marks ]; then rm -rf review/marks review/quiz; cp -R review/seed review/marks; fi
exec python3 ../lean_vouch/server.py --docs docbuild/.lake/build/doc --marks review/marks \
  --graph review/graph.json --source . --prefix ReviewExample --id ada --name Ada \
  --port "${PORT:-8798}"
