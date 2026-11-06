"""The rendered pages, end to end: doc-gen4's HTML still takes the review controls, and marks are
flagged exactly when they should be. Needs the example's docs and graph (example/review.sh builds
them) and Google Chrome; set CHROME to use another binary."""

import html
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


# Clicks through a page from inside it: with an iframe in the page, its document, or else the page's own.
DRIVER = """<script>
(async () => {
  const results = [], errors = [];
  const wait = ms => new Promise(done => setTimeout(done, ms));
  const until = async test => { for (let i = 0; i < 200 && !test(); i++) await wait(50); return test(); };
  const frame = document.getElementById("frame");
  if (frame && !frame.contentDocument?.body?.childElementCount) await new Promise(done => frame.addEventListener("load", done));
  const view = frame ? frame.contentWindow : window, doc = view.document;
  view.addEventListener("error", e => errors.push(e.message));
  view.addEventListener("unhandledrejection", e => errors.push(String(e.reason)));
  const ready = selector => until(() => doc.querySelector(selector));
  try {
SCRIPT
    await wait(300);
  } catch (error) { errors.push(String(error)); }
  const out = document.createElement("pre");
  out.id = "driven";
  out.textContent = JSON.stringify({ results, errors });
  document.body.append(out);
})();
</script>"""


@unittest.skipUnless(DOCS.exists() and GRAPH.exists() and Path(CHROME).exists(), "needs example docs and Chrome")
class PagesTest(unittest.TestCase):
    def serve(self, marks, graph, docs=DOCS, people={}, build=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        marks_folder, graph_file = Path(folder.name, "marks"), Path(folder.name, "graph.json")
        marks_folder.mkdir()
        self.port = free_port()
        (marks_folder / "ada.json").write_text(json.dumps({"format": 3, "name": "Ada", "marks": marks}))
        graph_file.write_text(json.dumps(graph))
        if build:
            Path(folder.name, "build.json").write_text(json.dumps(build))
        for who, (name, entries) in people.items():
            (marks_folder / f"{who}.json").write_text(json.dumps({"format": 3, "name": name, "marks": entries}))
        server = subprocess.Popen([sys.executable, str(ROOT / "lean_review" / "server.py"), "--docs", str(docs),
                                   "--marks", str(marks_folder), "--graph", str(graph_file), "--source", str(EXAMPLE),
                                   "--prefix", "ReviewExample", "--id", "ada", "--name", "Ada", "--port", str(self.port)], stdout=subprocess.DEVNULL)
        self.addCleanup(server.wait)
        self.addCleanup(server.terminate)
        for _ in range(50):
            try:
                urlopen(f"http://127.0.0.1:{self.port}/index.html").read()
                return
            except OSError:
                time.sleep(0.1)
        self.fail("server didn't start")

    def page(self, path, budget=8000):
        with tempfile.TemporaryDirectory() as profile:
            result = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", f"--user-data-dir={profile}", f"--virtual-time-budget={budget}",
                 *(["--no-sandbox"] if os.environ.get("CI") else []),  # GitHub's runners don't allow Chrome's sandbox
                 "--dump-dom", f"http://127.0.0.1:{self.port}/{path}"], capture_output=True, text=True, timeout=120)
        return result.stdout

    def docs_with(self, path, text):
        """The example's docs with one page more or changed, linking the rest: they're large."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        docs, parts = Path(folder.name), Path(path).parts
        for depth in range(len(parts)):
            source, target = DOCS.joinpath(*parts[:depth]), docs.joinpath(*parts[:depth])
            target.mkdir(exist_ok=True)
            for entry in source.iterdir():
                if entry.name != parts[depth]:
                    (target / entry.name).symlink_to(entry)
        docs.joinpath(*parts).write_text(text)
        return docs

    def click_through(self, marks, script, *, frame=None, page="ReviewExample/Gcd.html", people={}, build=None):
        """Run `script` in a real page, which can't be clicked from outside headless Chrome, and return
        what it adds to `results`. It sees `doc`, the page's document, and any uncaught error fails."""
        driver = DRIVER.replace("SCRIPT", script)
        if frame:
            docs = self.docs_with("drive.html", f'<html><body><iframe id="frame" src="/{frame}" width="1400" height="900">'
                                                f'</iframe>{driver}</body></html>')
            path = "drive.html"
        else:
            docs = self.docs_with(page, (DOCS / page).read_text().replace("</body>", driver + "</body>", 1))
            path = page
        self.serve(marks, self.graph, docs, people, build)
        found = re.search(r'<pre id="driven">(.*?)</pre>', self.page(path, 30000), re.S)
        self.assertIsNotNone(found, "the page's script didn't finish")
        done = json.loads(html.unescape(found[1]))
        self.assertEqual(done["errors"], [])
        return done["results"]

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

    def test_the_diagrams_buttons_change_at_once(self):
        results = self.click_through({}, """
            await ready("#sidebar .marks button");
            const active = () => [...doc.querySelectorAll("#sidebar .marks button.active")].map(b => b.dataset.status).join();
            for (const status of ["agree", "reject", "reject", "question"]) {
              const before = active();
              doc.querySelector(`#sidebar .marks button[data-status=${status}]`).click();
              await until(() => active() !== before);
              results.push(active());
            }
            await ready("#sidebar .prompt");
            results.push(doc.activeElement === doc.querySelector("#sidebar textarea"));
        """, frame=f"review/diagram#{GCD}")
        self.assertEqual(results, ["agree", "reject", "", "question", True])

    def test_a_note_answers_the_diagrams_prompt(self):
        results = self.click_through({}, """
            await ready("#sidebar .marks button");
            doc.querySelector("#sidebar .marks button[data-status=question]").click();
            await ready("#sidebar .prompt");
            doc.querySelector("#sidebar textarea").value = "Why?";
            [...doc.querySelectorAll("#sidebar button")].find(b => b.textContent === "Add note").click();
            await until(() => doc.querySelector("#sidebar .review-notes li"));
            results.push(!!doc.querySelector("#sidebar .prompt"), doc.querySelector("#sidebar .marks button.active")?.dataset.status);
        """, frame=f"review/diagram#{GCD}")
        self.assertEqual(results, [False, "question"])

    def test_a_reply_clears_the_diagrams_attention(self):
        asked = {GCD: {"status": "question", "date": "2026-01-01T00:00:00",
                       "notes": [{"date": "2026-01-01T00:00:00", "text": "Is this right?"}]}}
        results = self.click_through({}, """
            await ready(".attention-badge");
            results.push(doc.querySelectorAll(".attention-badge").length, doc.querySelectorAll("#tree-rows .waiting").length > 0);
            await ready("#sidebar textarea");
            doc.querySelector("#sidebar textarea").value = "It is.";
            [...doc.querySelectorAll("#sidebar button")].find(b => b.textContent === "Add note").click();
            await until(() => !doc.querySelector(".attention-badge"));
            results.push(doc.querySelectorAll(".attention-badge").length, doc.querySelectorAll("#tree-rows .waiting").length);
        """, frame=f"review/diagram#{GCD}", people={"claude": ("Claude", asked)})
        self.assertEqual(results, [1, True, 0, 0])

    def test_the_diagram_shows_whose_marks_you_choose(self):
        others = {GCD: {"status": "reject", "date": "2026-01-01T00:00:00", "notes": [], "fingerprint": fingerprint(self.graph, GCD)}}
        results = self.click_through(self.marked(), """
            await ready("#diagram g.node");
            const viewer = doc.getElementById("viewer");
            results.push([...viewer.options].map(o => o.text));
            viewer.value = "all";
            viewer.dispatchEvent(new Event("change"));
            await until(() => doc.querySelector('#diagram g.node [fill="#e6dcf5"]'));
            results.push(!!doc.querySelector('#diagram g.node [fill="#e6dcf5"]'), doc.getElementById("disagree-key").hidden);
        """, frame=f"review/diagram#{GCD}", people={"bo": ("Bo", others)})
        self.assertEqual(results, [["mine", "Bo", "everyone"], True, False])

    def test_a_docs_pages_question_saves_at_once_and_asks_for_a_note(self):
        results = self.click_through({}, """
            await ready(".decl .review-row button");
            const decl = doc.getElementById("ReviewExample.gcd");
            decl.querySelector("button[data-status=question]").click();
            await until(() => decl.dataset.reviewStatus === "question");
            const prompt = decl.querySelector(".review-prompt");
            results.push(decl.dataset.reviewStatus, prompt.hidden, doc.activeElement === decl.querySelector("textarea"));
            decl.querySelector("textarea").value = "Why?";
            decl.querySelector(".review-add").click();
            await until(() => prompt.hidden);
            results.push(prompt.hidden);
            decl.querySelector("button[data-status=question]").click();
            await until(() => decl.dataset.reviewStatus === "");
            results.push(decl.querySelectorAll(".review-notes li").length);
        """)
        self.assertEqual(results, ["question", False, True, True, 1])

    def test_a_docs_page_shows_others_and_clears_attention(self):
        asked = {GCD: {"status": "question", "date": "2026-02-01T00:00:00", "fingerprint": fingerprint(self.graph, GCD),
                       "notes": [{"date": "2026-02-01T00:00:00", "text": "Is this right?"}]}}
        results = self.click_through(self.marked(), """
            await ready(".decl[data-review-attention=yes]");
            const decl = doc.getElementById("ReviewExample.gcd");
            results.push(decl.dataset.reviewDisagree, decl.querySelector(".review-others").textContent);
            results.push([...doc.querySelectorAll("#review-summary button")].some(b => b.textContent.includes("needs attention")));
            decl.querySelector("textarea").value = "It is.";
            decl.querySelector(".review-add").click();
            await until(() => !decl.dataset.reviewAttention);
            results.push(decl.dataset.reviewAttention);
        """, people={"claude": ("Claude", asked)})
        self.assertEqual(results[0], "yes")
        self.assertIn("Claude: ?", results[1])
        self.assertEqual(results[2:], [True, ""])

    LIVE_NOTE = """fetch("/review/data", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: "claude", author: "Claude", name: "ReviewExample.gcd", status: "question", note: "Live." }) });"""

    def test_a_docs_page_shows_someone_elses_note_while_open(self):
        results = self.click_through({}, """
            await ready(".decl .review-row button");
            const decl = doc.getElementById("ReviewExample.gcd");
            const box = decl.querySelector("textarea");
            box.value = "half written";
            """ + self.LIVE_NOTE + """
            await until(() => decl.querySelector(".review-notes li"));
            results.push(decl.querySelector(".review-notes li").textContent.includes("Live."), box.value, decl.dataset.reviewAttention);
            await until(() => [...doc.querySelectorAll("#review-summary button")].some(b => b.textContent.includes("needs attention (1)") && b.style.display !== "none"));
            results.push([...doc.querySelectorAll("#review-summary button")].find(b => b.textContent.includes("attention")).style.display);
        """)
        self.assertEqual(results, [True, "half written", "yes", ""])

    def test_the_diagram_shows_someone_elses_note_while_open(self):
        results = self.click_through(self.marked(), """
            await ready("#sidebar .marks button");
            results.push(doc.getElementById("viewer-label").hidden, doc.querySelectorAll(".attention-badge").length);
            """ + self.LIVE_NOTE + """
            await ready(".attention-badge");
            await ready("#sidebar .others .attention");
            results.push(doc.getElementById("viewer-label").hidden, doc.querySelectorAll(".attention-badge").length);
            results.push([...doc.getElementById("viewer").options].map(o => o.text));
        """, frame=f"review/diagram#{GCD}")
        self.assertEqual(results, [True, 0, False, 1, ["mine", "Claude", "everyone"]])

    def test_a_running_build_is_announced(self):
        results = self.click_through({}, """
            await ready("#review-build:not([hidden])");
            results.push(doc.getElementById("review-build").textContent);
        """, build={"state": "building", "pid": os.getpid()})
        self.assertEqual(results, ["Rebuilding…"])

    def test_a_failed_build_is_announced_and_the_page_still_works(self):
        results = self.click_through({}, """
            await ready("#review-build:not([hidden])");
            results.push(doc.getElementById("review-build").textContent, doc.getElementById("review-build").title);
            results.push(doc.querySelectorAll(".decl .review-row").length > 0);
        """, build={"state": "failed", "message": "It broke.", "pid": 1})
        self.assertEqual(results, ["Build failed; showing the last good version.", "It broke.", True])

    def test_nothing_is_announced_after_a_good_build(self):
        results = self.click_through({}, """
            await ready(".decl .review-row button");
            await wait(2500);
            results.push(doc.getElementById("review-build").hidden);
        """, build={"state": "done", "built": "then", "pid": 1})
        self.assertEqual(results, [True])

    def test_the_quiz_hides_a_name_even_in_a_recursive_call(self):
        self.serve({}, self.graph)
        html = self.page("review/quiz?name=ReviewExample.Steps.count")
        self.assertIn('rest.<span class="hidden-name">▢</span>', html)

    def test_the_quiz_fills_in_the_namespace(self):
        self.serve({}, self.graph)
        self.assertIn('<span class="namespace">Steps.</span>', self.page("review/quiz?name=ReviewExample.Steps.count"))

    def test_the_quiz_is_titled_with_the_project(self):
        self.serve({}, self.graph)
        self.assertIn(">ReviewExample: quiz<", self.page("review/quiz"))

    def test_the_diagram_is_titled_with_the_project(self):
        self.serve({}, self.graph)
        self.assertIn('<h1 id="project">ReviewExample</h1>', self.page("review/diagram"))

    def test_the_diagram_draws(self):
        self.serve(self.marked(), self.graph)
        html = self.page("review/diagram")
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
        html = self.page("review/diagram")
        self.assertIn("contains sorry", html)
        self.assertIn('stroke="#c62828"', html)
        self.assertIn("relies on", html, "the dashed arrows toward the sorry")

    def test_a_project_without_sorry_shows_no_sorry_controls(self):
        graph = json.loads(json.dumps(self.graph))
        for decl in graph["declarations"].values():
            decl.pop("sorry", None)
            decl.pop("sorryFrom", None)
        self.serve({}, graph)
        self.assertRegex(self.page("review/diagram"), r'id="gaps-label" hidden')

    def test_a_large_project_opens_as_folders(self):
        self.serve({}, self.large_graph())
        html = self.page("review/diagram")
        self.assertIn("0 of 5200 marked", html)
        self.assertEqual(html.count('class="node'), 3, "one box each for Big, Small/Inner and Other")

    def test_hovering_shows_names_not_graphviz_ids(self):
        self.serve({}, self.large_graph())
        html = self.page("review/diagram#P.Small.Inner.b")
        self.assertIn("<title>P.Small.Inner.b</title>", html)
        self.assertIn("<title>Big</title>", html)
        self.assertIn("<title>Small/Inner: 2 declarations</title>", html)
        self.assertNotIn("<title>cluster_", html)

    def test_a_project_too_large_for_open_all_offers_folders(self):
        graph = self.large_graph()
        for name in list(graph["declarations"])[300:]:
            del graph["declarations"][name]
        self.serve({}, graph)
        self.assertIn(">open folders<", self.page("review/diagram"))

    def test_the_list_shows_every_top_level_folder(self):
        self.serve({}, self.large_graph())
        html = self.page("review/diagram")
        self.assertEqual(html.count('class="row'), 3)

    def test_a_link_to_a_declaration_opens_the_boxes_around_it(self):
        self.serve({}, self.large_graph())
        html = self.page("review/diagram#P.Small.Inner.b")
        self.assertIn("Inner.b", html)
        self.assertIn(">Big<", html, "the other folders stay closed")


if __name__ == "__main__":
    unittest.main()
