"""Build the web demo: lean-vouch's own pages and server, on the example project, as a static site.

The server is plain Python, so the demo runs it in the visitor's browser with Pyodide: demo.js
starts it and answers the pages' requests with it, instead of a server on their computer. What
they mark is kept in their browser. Nothing here can run Lean, so building and writing a challenge
are left out; the example's graph is built beforehand.

    example/review.sh           # once, to build example/review/graph.json
    python3 demo/build.py       # writes demo/site/
    python3 -m http.server -d demo/site 8800
"""

import argparse
import json
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "lean_vouch" / "web"
EXAMPLE = ROOT / "example"
HERE = Path(__file__).resolve().parent


def page(text, built):
    """A page as the server sends it, with its links made relative, since the demo may be served
    from a folder of a site, and the demo's runtime loaded first."""
    for old, new in [('"/review/diagram?challenge"', '"index.html?challenge"'), ('"/review/diagram"', '"index.html"'),
                     ("`/review/quiz?", "`quiz.html?"), ('"/review/quiz"', '"quiz.html"')]:
        text = text.replace(old, new)
    text = re.sub(r'(src|href)="/review/([a-z]+\.(?:js|css))"', r'\1="\2"', text)
    # As the server stamps pages: which build they're from, and that there are no doc pages.
    known = f"<script>window.reviewBuilt = {json.dumps(built)}; window.reviewDocs = false;</script>"
    text = text.replace('<script src="links.js">', known + '<script src="links.js">', 1)
    return text.replace("<head>", '<head>\n<script src="demo.js"></script>', 1)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, default=HERE / "site")
    out = p.parse_args().out
    graph = EXAMPLE / "review" / "graph.json"
    if not graph.exists():
        raise SystemExit("demo: build the example's graph first: example/review.sh")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    built = json.loads(graph.read_text(encoding="utf-8")).get("built")

    for name, target in [("graph.html", "index.html"), ("quiz.html", "quiz.html")]:
        (out / target).write_text(page((WEB / name).read_text(encoding="utf-8"), built), encoding="utf-8")
    for name in ["links.js", "math.js", "lean.js", "review.js", "review.css"]:
        shutil.copy(WEB / name, out / name)
    shutil.copy(HERE / "demo.js", out / "demo.js")

    # What the server reads, unpacked in the browser under /demo.
    with zipfile.ZipFile(out / "demo.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in ["__init__.py", "server.py", "challenge.py"]:
            bundle.write(ROOT / "lean_vouch" / name, f"lean_vouch/{name}")
        bundle.write(graph, "project/graph.json")
        for source in sorted((EXAMPLE / "ReviewExample").rglob("*.lean")) + [EXAMPLE / "ReviewExample.lean"]:
            bundle.write(source, f"project/{source.relative_to(EXAMPLE)}")
        for seed in sorted((EXAMPLE / "review" / "seed").glob("*.json")):
            bundle.write(seed, f"seed/{seed.name}")
        bundle.writestr("build.json", json.dumps({"state": "done", "docs": False, "built": built}))
    print(f"demo: wrote {out}")


if __name__ == "__main__":
    main()
