#!/bin/sh
# Regenerate the screenshots in ../docs/images from the example, with fresh example marks, at twice
# the pixel density so they stay sharp when zoomed in.
# Needs the example's docs and graph built (./review.sh does both) and Google Chrome.
set -e
cd "$(dirname "$0")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
TOOL=$(cd .. && pwd)/lean_vouch
OUT=$(cd .. && pwd)/docs/images
mkdir -p "$OUT"
WORK=$(mktemp -d -t lean-vouch-screenshots)
SERVER=
trap '[ -n "$SERVER" ] && kill $SERVER; rm -rf "$WORK"' EXIT

serve() {  # serve EXAMPLE: serve that copy of the example, with fresh example marks
  [ -n "$SERVER" ] && kill $SERVER
  PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
  rm -rf "$WORK/marks"
  cp -R review/seed "$WORK/marks"
  (cd "$1" && exec python3 "$TOOL/server.py" --docs docbuild/.lake/build/doc --marks "$WORK/marks" \
    --graph review/graph.json --source . --prefix ReviewExample --id ada --name Ada --port $PORT) > /dev/null &
  SERVER=$!
  sleep 1
  kill -0 $SERVER  # stop here if the server didn't start
}

shot() {  # shot NAME WIDTH HEIGHT PATH [SCHEME]: SCHEME 1 is light (the default), 0 dark
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --window-size="$2,$3" --force-device-scale-factor=2 \
    --blink-settings=preferredColorScheme="${5:-1}" --virtual-time-budget=20000 \
    --screenshot="$OUT/$1.png" "http://127.0.0.1:$PORT/$4" 2> /dev/null
  echo "docs/images/$1.png"
}

serve .
shot review-page 1400 1000 "ReviewExample/Gcd.html?only&focus=ReviewExample.gcd"
shot changes 1400 1000 "ReviewExample/Gcd.html?only&focus=ReviewExample.gcd_divides,ReviewExample.divides_gcd"
shot library-mark 1400 1350 "Init/Prelude.html?only&focus=Nat.mod"
shot orphans 1400 800 "ReviewExample/Gcd.html#review-orphans"
shot diagram 1600 1000 "review/diagram#ReviewExample.gcd"
shot sorry 1400 1000 "ReviewExample/Multiples/Lcm.html?only&focus=ReviewExample.gcd_mul_lcm,ReviewExample.gcd_divides_mul"
shot sorry-diagram 1400 900 "review/diagram?open=ReviewExample.Multiples.Lcm,ReviewExample.Gcd#ReviewExample.gcd_divides_mul"
shot review-page-dark 1400 1000 "ReviewExample/Gcd.html?only&focus=ReviewExample.gcd" 0
shot quiz 1400 1000 "review/quiz?name=ReviewExample.divides_gcd"
shot prime 1400 900 "ReviewExample/Prime.html?only&focus=ReviewExample.Prime"
shot prime-diagram 1400 900 "review/diagram?open=ReviewExample.Prime#ReviewExample.Prime"

# The walkthrough's fix, applied to a copy of the example (a clone, so it takes no extra disk on
# APFS), and the same marks flagged against it.
cp -cR . "$WORK/fixed" 2> /dev/null || cp -R . "$WORK/fixed"
python3 - "$WORK/fixed/ReviewExample/Prime.lean" << 'EOF'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
for old, new in [("`n` is prime: its", "`n` is prime: it is at least 2, and its"),
                 (":=\n  ∀ d,", ":=\n  2 ≤ n ∧ ∀ d,"), ("hp _ (gcd", "hp.2 _ (gcd")]:
    assert old in text, old
    text = text.replace(old, new)
path.write_text(text)
EOF
(cd "$WORK/fixed" && lake build -q > /dev/null && (cd docbuild && DOCGEN_SRC=file lake build -q ReviewExample:docs > /dev/null) \
  && lake env lean --run "$TOOL/graph/DeclGraph.lean" ReviewExample > review/graph.json)
serve "$WORK/fixed"
shot prime-fixed 1400 1000 "ReviewExample/Prime.html?only&focus=ReviewExample.Prime,ReviewExample.Prime.coprime"
