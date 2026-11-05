"""The rendered pages, end to end: doc-gen4's HTML still takes the review controls, and marks are
flagged exactly when they should be. Needs the example's docs and graph (example/review.sh builds
them) and Google Chrome; set CHROME to use another binary."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example"
DOCS = EXAMPLE / "docbuild" / ".lake" / "build" / "doc"
GRAPH = EXAMPLE / "review" / "graph.json"
CHROME = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium") or \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
GCD = "ReviewExample.gcd"


def free_port():
    """A port nothing is using, so these pages can't come from another lean-review server."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def closure(graph, name):
    """What review.js's `closure` computes: everything below a declaration."""
    decls, seen, stack = graph["declarations"], set(), [name]
    while stack:
        entry = decls.get(stack.pop())
        for dep in (entry["deps"] + entry["uses"]) if entry else []:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def fingerprint(graph, name):
    own = lambda n: (graph["declarations"].get(n) or graph["library"].get(n) or {}).get("own")
    return {"own": own(name), "deps": {d: own(d) for d in closure(graph, name)}}


@unittest.skipUnless(DOCS.exists() and GRAPH.exists() and Path(CHROME).exists(), "needs example docs and Chrome")
class PagesTest(unittest.TestCase):
    def serve(self, marks, graph):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        marks_file, graph_file = Path(folder.name, "marks.json"), Path(folder.name, "graph.json")
        self.port = free_port()
        marks_file.write_text(json.dumps(marks))
        graph_file.write_text(json.dumps(graph))
        server = subprocess.Popen([sys.executable, str(ROOT / "server" / "server.py"), "--docs", str(DOCS),
                                   "--marks", str(marks_file), "--graph", str(graph_file), "--source", str(EXAMPLE),
                                   "--prefix", "ReviewExample", "--port", str(self.port)], stdout=subprocess.DEVNULL)
        self.addCleanup(server.wait)
        self.addCleanup(server.terminate)
        for _ in range(50):
            try:
                urlopen(f"http://127.0.0.1:{self.port}/index.html").read()
                return
            except OSError:
                time.sleep(0.1)
        self.fail("server didn't start")

    def page(self, path):
        with tempfile.TemporaryDirectory() as profile:
            result = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", f"--user-data-dir={profile}", "--virtual-time-budget=8000",
                 *(["--no-sandbox"] if os.environ.get("CI") else []),  # GitHub's runners don't allow Chrome's sandbox
                 "--dump-dom", f"http://127.0.0.1:{self.port}/{path}"], capture_output=True, text=True, timeout=60)
        return result.stdout

    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads(GRAPH.read_text())

    def marked(self):
        return {GCD: {"status": "agree", "notes": [], "date": "2026-01-01", "fingerprint": fingerprint(self.graph, GCD)}}

    def test_every_project_declaration_gets_controls(self):
        self.serve({}, self.graph)
        html = self.page("ReviewExample/Gcd.html")
        decls = [n for n, d in self.graph["declarations"].items() if d["module"] == "ReviewExample.Gcd"]
        self.assertTrue(decls)
        self.assertGreaterEqual(html.count("review-row"), len(decls),
                                "doc-gen4's HTML may have changed; see review.js")

    def test_graph_and_docs_list_the_same_declarations(self):
        pages = "".join(p.read_text() for p in (DOCS / "ReviewExample").rglob("*.html"))
        documented = set(re.findall(r'class="decl[^"]*" id="([^"]+)"', pages))
        # A deprecated alias is on its page, but the graph lists it as a rename, for its mark to move.
        self.assertEqual(set(self.graph["declarations"]) | set(self.graph["renames"]), documented)

    def test_an_unchanged_declaration_is_not_flagged(self):
        self.serve(self.marked(), self.graph)
        self.assertNotIn("changed since you marked it", self.page("ReviewExample/Gcd.html"))

    def test_a_changed_declaration_is_flagged(self):
        graph = json.loads(json.dumps(self.graph))
        graph["declarations"][GCD]["own"] = "0000000000000000"
        self.serve(self.marked(), graph)
        self.assertIn("changed since you marked it", self.page("ReviewExample/Gcd.html"))

    def test_a_change_below_flags_what_depends_on_it(self):
        name = "ReviewExample.gcd_divides"
        below = "ReviewExample.gcd"
        marks = {name: {"status": "agree", "notes": [], "fingerprint": fingerprint(self.graph, name)}}
        graph = json.loads(json.dumps(self.graph))
        graph["declarations"][below]["own"] = "0000000000000000"
        self.serve(marks, graph)
        html = self.page("ReviewExample/Gcd.html")
        self.assertIn("which changed since you marked it", html)

    def test_the_quiz_hides_a_name_even_in_a_recursive_call(self):
        self.serve({}, self.graph)
        html = self.page("__review__/quiz?name=ReviewExample.Steps.count")
        self.assertIn('rest.<span class="hidden-name">▢</span>', html)

    def test_the_quiz_fills_in_the_namespace(self):
        self.serve({}, self.graph)
        self.assertIn('<span class="namespace">Steps.</span>', self.page("__review__/quiz?name=ReviewExample.Steps.count"))

    def test_the_quiz_is_titled_with_the_project(self):
        self.serve({}, self.graph)
        self.assertIn(">ReviewExample: quiz<", self.page("__review__/quiz"))

    def test_the_diagram_is_titled_with_the_project(self):
        self.serve({}, self.graph)
        self.assertIn('<h1 id="project">ReviewExample</h1>', self.page("__review__/diagram"))

    def test_the_diagram_draws(self):
        self.serve(self.marked(), self.graph)
        html = self.page("__review__/diagram")
        self.assertGreater(html.count('class="node'), 0, "the diagram needs the CDN scripts, so this needs a network")


    def large_graph(self):
        """A project too large to send whole: three folders, one module of which we open."""
        entry = lambda module, deps=(): {"kind": "def", "module": module, "lines": None, "own": "0", "deep": "0",
                                         "deps": list(deps), "uses": []}
        decls = {f"P.Big.M{i % 40}.x{i}": entry(f"P.Big.M{i % 40}") for i in range(5200)}
        decls["P.Small.Inner.a"] = entry("P.Small.Inner")
        decls["P.Small.Inner.b"] = entry("P.Small.Inner", ["P.Small.Inner.a", "P.Big.M0.x0"])
        decls["P.Other.c"] = entry("P.Other", ["P.Small.Inner.b"])
        return {"format": 1, "root": "P", "declarations": decls, "library": {}, "renames": {}, "modules": {}}

    def test_a_sorry_shows_on_its_page_and_on_what_relies_on_it(self):
        self.serve({}, self.graph)
        html = self.page("ReviewExample/Multiples/Lcm.html")
        self.assertIn("Contains sorry.", html)
        self.assertIn("Proved only if these are:", html)
        self.assertIn("sorry, 3 relying on one", html)
        self.assertIn("by way of", html)

    def test_a_sorry_shows_in_the_diagram(self):
        self.serve({}, self.graph)
        html = self.page("__review__/diagram")
        self.assertIn("contains sorry", html)
        self.assertIn('stroke="#c62828"', html)
        self.assertIn("relies on", html, "the dashed arrows toward the sorry")

    def test_a_project_without_sorry_shows_no_sorry_controls(self):
        graph = json.loads(json.dumps(self.graph))
        for decl in graph["declarations"].values():
            decl.pop("sorry", None)
            decl.pop("sorryFrom", None)
        self.serve({}, graph)
        self.assertRegex(self.page("__review__/diagram"), r'id="gaps-label" hidden')

    def test_a_large_project_opens_as_folders(self):
        self.serve({}, self.large_graph())
        html = self.page("__review__/diagram")
        self.assertIn("0 of 5200 marked", html)
        self.assertEqual(html.count('class="node'), 3, "one box each for Big, Small/Inner and Other")

    def test_hovering_shows_names_not_graphviz_ids(self):
        self.serve({}, self.large_graph())
        html = self.page("__review__/diagram#P.Small.Inner.b")
        self.assertIn("<title>P.Small.Inner.b</title>", html)
        self.assertIn("<title>Big</title>", html)
        self.assertIn("<title>Small/Inner: 2 declarations</title>", html)
        self.assertNotIn("<title>cluster_", html)

    def test_a_project_too_large_for_open_all_offers_folders(self):
        graph = self.large_graph()
        for name in list(graph["declarations"])[300:]:
            del graph["declarations"][name]
        self.serve({}, graph)
        self.assertIn(">open folders<", self.page("__review__/diagram"))

    def test_the_list_shows_every_top_level_folder(self):
        self.serve({}, self.large_graph())
        html = self.page("__review__/diagram")
        self.assertEqual(html.count('class="row'), 3)

    def test_a_link_to_a_declaration_opens_the_boxes_around_it(self):
        self.serve({}, self.large_graph())
        html = self.page("__review__/diagram#P.Small.Inner.b")
        self.assertIn("Inner.b", html)
        self.assertIn(">Big<", html, "the other folders stay closed")


if __name__ == "__main__":
    unittest.main()
