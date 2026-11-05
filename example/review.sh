#!/bin/sh
# Build the example's docs and declaration graph, then serve them with review controls.
#   ./review.sh           serve http://127.0.0.1:8798/ReviewExample.html
#   ./review.sh --reset   start again from the example marks (review/marks.seed.json)
set -e
cd "$(dirname "$0")"
lake build -q
(cd docbuild && DOCGEN_SRC=file lake build -q ReviewExample:docs)
lake env lean --run ../graph/DeclGraph.lean ReviewExample > review/graph.json
if [ "$1" = "--reset" ] || [ ! -f review/marks.json ]; then cp review/marks.seed.json review/marks.json; fi
exec python3 ../server/server.py --docs docbuild/.lake/build/doc --marks review/marks.json \
  --graph review/graph.json --source . --prefix ReviewExample --reviewer "${REVIEWER:-Ada}" \
  --port "${PORT:-8798}"
