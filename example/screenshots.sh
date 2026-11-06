#!/bin/sh
# Regenerate the screenshots in ../docs/images from the example, with fresh example marks.
# Needs the example's docs and graph built (./review.sh does both) and Google Chrome.
set -e
cd "$(dirname "$0")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
OUT=../docs/images
mkdir -p "$OUT"
REVIEW=$(mktemp -d -t lean-vouch-marks)
cp -R review/seed "$REVIEW/marks"
python3 ../lean_vouch/server.py --docs docbuild/.lake/build/doc --marks "$REVIEW/marks" --graph review/graph.json \
  --source . --prefix ReviewExample --id ada --name Ada --port $PORT > /dev/null &
SERVER=$!
trap 'kill $SERVER; rm -rf "$REVIEW"' EXIT
sleep 1
kill -0 $SERVER  # stop here if the server didn't start

shot() {  # shot NAME WIDTH HEIGHT PATH [SCHEME]: SCHEME 1 is light (the default), 0 dark
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --window-size="$2,$3" \
    --blink-settings=preferredColorScheme="${5:-1}" --virtual-time-budget=20000 \
    --screenshot="$OUT/$1.png" "http://127.0.0.1:$PORT/$4" 2> /dev/null
  echo "$OUT/$1.png"
}
shot review-page 1400 1000 "ReviewExample/Gcd.html?only&focus=ReviewExample.gcd"
shot changes 1400 1000 "ReviewExample/Gcd.html?only&focus=ReviewExample.gcd_divides,ReviewExample.divides_gcd"
shot library-mark 1400 1350 "Init/Prelude.html?only&focus=Nat.mod"
shot orphans 1400 800 "ReviewExample/Gcd.html#review-orphans"
shot diagram 1600 1000 "review/diagram#ReviewExample.gcd"
shot sorry 1400 1000 "ReviewExample/Multiples/Lcm.html?only&focus=ReviewExample.gcd_mul_lcm,ReviewExample.gcd_divides_mul"
shot sorry-diagram 1600 1000 "review/diagram#ReviewExample.gcd_divides_mul"
shot review-page-dark 1400 1000 "ReviewExample/Gcd.html?only&focus=ReviewExample.gcd" 0
shot quiz 1400 1000 "review/quiz?name=ReviewExample.divides_gcd"
