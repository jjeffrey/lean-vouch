"""The server's promises about marks.json: nothing is lost, whatever happens. Needs only Python."""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from argparse import Namespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import server  # noqa: E402


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        (root / "docs").mkdir()
        (root / "docs" / "Page.html").write_text("<html><body>page</body></html>")
        self.marks = root / "marks.json"
        self.graph = root / "graph.json"
        server.args = Namespace(docs=root / "docs", marks=self.marks, graph=self.graph, links=root / "links.json",
                                source=None, reviewer="Ada")
        server.graph = server.Graph(self.graph)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def url(self, path):
        return f"http://127.0.0.1:{self.httpd.server_port}{path}"

    def post(self, path, body, headers={"Content-Type": "application/json"}):
        request = Request(self.url(path), data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def get(self, path):
        with urlopen(self.url(path)) as response:
            return response.read()

    def stored(self):
        return json.loads(self.marks.read_text())["marks"]

    def test_another_site_cant_change_marks(self):
        json_type = {"Content-Type": "application/json"}
        for headers in ({"Content-Type": "text/plain"}, {**json_type, "Origin": "https://example.com"},
                        {**json_type, "Host": "example.com"}):
            code, _ = self.post("/__review__/data", {"name": "A.b", "status": "agree"}, headers)
            self.assertIn(code, (403, 415), headers)
        self.assertFalse(self.marks.exists())

    def test_another_domain_cant_read(self):
        request = Request(self.url("/__review__/data"), headers={"Host": "example.com"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_the_pages_own_origin_is_accepted(self):
        port = self.httpd.server_port
        for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            code, _ = self.post("/__review__/data", {"name": "A.b", "status": "agree"},
                                {"Content-Type": "application/json", "Origin": origin})
            self.assertEqual(code, 200)

    def test_a_mark_and_note_are_saved_and_read_back(self):
        self.post("/__review__/data", {"name": "A.b", "status": "agree"})
        self.post("/__review__/data", {"name": "A.b", "note": "Looks right."})
        entry = json.loads(self.get("/__review__/data"))["A.b"]
        self.assertEqual(entry["status"], "agree")
        self.assertEqual([(n["by"], n["text"]) for n in entry["notes"]], [("Ada", "Looks right.")])

    def test_saves_record_the_format_and_pages_never_see_it(self):
        self.post("/__review__/data", {"name": "A.b", "status": "agree"})
        self.assertEqual(json.loads(self.marks.read_text())[server.FORMAT_KEY], server.MARKS_FORMAT)
        self.assertEqual(list(json.loads(self.get("/__review__/data"))), ["A.b"])

    def test_a_declaration_named_like_the_format_is_an_ordinary_mark(self):
        self.post("/__review__/data", {"name": "__format__", "status": "agree"})
        self.post("/__review__/data", {"name": "marks", "status": "question"})
        self.assertEqual(json.loads(self.get("/__review__/data"))["__format__"]["status"], "agree")
        self.assertEqual(json.loads(self.get("/__review__/data"))["marks"]["status"], "question")

    def test_a_format_1_file_with_that_declaration_keeps_it(self):
        self.marks.write_text(json.dumps({"__format__": {"status": "agree", "notes": []}}))
        self.post("/__review__/data", {"name": "A.b", "status": "agree"})
        self.assertEqual(self.stored()["__format__"]["status"], "agree")

    def test_a_format_1_file_with_a_version_number_drops_it(self):
        self.marks.write_text(json.dumps({"__format__": 1, "A.b": {"status": "agree", "notes": []}}))
        self.assertEqual(list(json.loads(self.get("/__review__/data"))), ["A.b"])

    def test_a_file_from_before_formats_is_read_and_kept(self):
        self.marks.write_text(json.dumps({"Old.mark": {"status": "agree", "notes": []}}))
        self.post("/__review__/data", {"name": "New.mark", "status": "question"})
        self.assertEqual(self.stored()["Old.mark"]["status"], "agree")
        self.assertEqual(self.stored()["New.mark"]["status"], "question")

    def test_invalid_json_is_refused_and_left_alone(self):
        broken = '{"A.b": {"status": "agree",'
        self.marks.write_text(broken)
        code, reply = self.post("/__review__/data", {"name": "C.d", "status": "agree"})
        self.assertEqual(code, 500)
        self.assertIn("not valid JSON", reply["error"])
        self.assertEqual(self.marks.read_text(), broken)

    def test_a_newer_format_is_refused_and_left_alone(self):
        newer = json.dumps({server.FORMAT_KEY: server.MARKS_FORMAT + 1, "marks": {}})
        self.marks.write_text(newer)
        code, _ = self.post("/__review__/data", {"name": "C.d", "status": "agree"})
        self.assertEqual(code, 500)
        self.assertEqual(self.marks.read_text(), newer)

    def test_a_save_leaves_no_temporary_file(self):
        self.post("/__review__/data", {"name": "A.b", "status": "agree"})
        self.assertEqual([p.name for p in self.marks.parent.glob("marks.json*")], ["marks.json"])

    def test_a_rename_moves_the_mark_and_says_so(self):
        self.marks.write_text(json.dumps({"Old.name": {"status": "agree", "notes": []}}))
        self.graph.write_text(json.dumps({"renames": {"Old.name": "New.name"}}))
        marks = json.loads(self.get("/__review__/data"))
        self.assertNotIn("Old.name", marks)
        self.assertEqual(marks["New.name"]["status"], "agree")
        self.assertIn("renamed", marks["New.name"]["notes"][-1]["text"])

    def test_a_rename_never_overwrites_an_existing_mark(self):
        self.marks.write_text(json.dumps({"Old.name": {"status": "agree", "notes": []},
                                          "New.name": {"status": "reject", "notes": []}}))
        self.graph.write_text(json.dumps({"renames": {"Old.name": "New.name"}}))
        marks = json.loads(self.get("/__review__/data"))
        self.assertEqual(marks["Old.name"]["status"], "agree")
        self.assertEqual(marks["New.name"]["status"], "reject")

    def test_moving_a_mark_onto_another_is_refused(self):
        self.marks.write_text(json.dumps({"A": {"status": "agree", "notes": []},
                                          "B": {"status": "reject", "notes": []}}))
        code, _ = self.post("/__review__/move", {"from": "A", "to": "B"})
        self.assertEqual(code, 400)
        self.assertEqual(json.loads(self.marks.read_text())["B"]["status"], "reject")

    def test_an_invalid_status_is_refused(self):
        code, _ = self.post("/__review__/data", {"name": "A.b", "status": "maybe"})
        self.assertEqual(code, 400)
        self.assertFalse(self.marks.exists())

    def test_asking_for_what_the_marks_need_returns_only_that(self):
        decls = {f"x{i}": {"module": "P.X", "deps": [], "uses": []} for i in range(server.LARGE + 1)}
        self.graph.write_text(json.dumps({"root": "P", "declarations": decls}))
        self.marks.write_text(json.dumps({"x1": {"status": "agree", "notes": []}}))
        self.assertEqual(list(json.loads(self.get("/__review__/graph?marked"))["declarations"]), ["x1"])

    def test_a_module_named_like_the_tool_keeps_its_page(self):
        folder = server.args.docs / "__review__"
        folder.mkdir()
        (folder / "graph.html").write_text("<html><body>module page</body></html>")
        self.assertIn(b"module page", self.get("/__review__/graph.html"))
        self.assertNotIn(b"module page", self.get("/__review__/diagram"))

    def test_no_url_of_the_tool_can_be_a_module_page(self):
        self.assertFalse([path for path in server.PAGES if path.endswith(".html")])

    def test_the_tools_pages_are_served(self):
        for path in server.PAGES:
            self.assertTrue(self.get(path))

    def test_a_verdict_goes_on_the_latest_quiz_answer(self):
        self.post("/__review__/data", {"name": "A.b", "note": "one", "quiz": {"name": "x", "description": "y"}})
        self.post("/__review__/data", {"name": "A.b", "note": "two", "quiz": {"name": "x", "description": "z"}})
        code, _ = self.post("/__review__/verdict", {"name": "A.b", "verdict": "partly"})
        self.assertEqual(code, 200)
        notes = self.stored()["A.b"]["notes"]
        self.assertEqual(["verdict" in n["quiz"] for n in notes], [False, True])

    def test_a_verdict_needs_a_quiz_answer_and_a_known_value(self):
        self.post("/__review__/data", {"name": "A.b", "note": "plain"})
        self.assertEqual(self.post("/__review__/verdict", {"name": "A.b", "verdict": "match"})[0], 400)
        self.post("/__review__/data", {"name": "A.b", "note": "q", "quiz": {"name": "x", "description": "y"}})
        self.assertEqual(self.post("/__review__/verdict", {"name": "A.b", "verdict": "maybe"})[0], 400)

    def test_pages_get_the_review_script(self):
        self.assertIn(b"/__review__/review.js", self.get("/Page.html"))


class SliceTest(unittest.TestCase):
    """Large projects: pages get what they need, with everything below it for change tracking."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "graph.json"
        entry = lambda module, deps=(), uses=(): {"module": module, "deps": list(deps), "uses": list(uses), "own": "0"}
        decls = {f"P.Pad.x{i}": entry("P.Pad") for i in range(server.LARGE)}
        decls |= {"a": entry("P.A"), "b": entry("P.B", ["a"], ["Nat"]), "c": entry("P.C", ["b"]),
                  "d": entry("P.D", ["c"]), "e": entry("P.E", ["outside"])}
        path.write_text(json.dumps({"root": "P", "declarations": decls, "renames": {"o": "n"},
                                    "library": {"Nat": {"module": "Init", "own": "1"}, "Int": {"module": "Init", "own": "2"},
                                                "Z": {"module": "Other", "own": "3"}}}))
        self.graph = server.Graph(path)

    def test_a_module_brings_everything_below_and_its_direct_users(self):
        part = self.graph.slice(["P.B"], [], {})
        self.assertEqual(set(part["declarations"]), {"a", "b", "c"})
        self.assertEqual(set(part["library"]), {"Nat"})
        self.assertEqual(part["renames"], {"o": "n"})
        self.assertNotIn("complete", part)

    def test_marks_bring_what_checking_them_needs(self):
        part = self.graph.slice([], [], {"c": {}, "Int": {}})
        self.assertEqual(set(part["declarations"]), {"a", "b", "c"})
        self.assertEqual(set(part["library"]), {"Nat", "Int"})

    def test_a_slice_names_every_module(self):
        self.assertIn("P.D", self.graph.slice(["P.A"], [], {})["moduleNames"])

    def test_a_library_page_gets_its_own_definitions(self):
        self.assertIn("Z", self.graph.slice(["Other"], [], {})["library"])

    def test_the_tree_counts_declarations_and_dependencies_between_modules(self):
        tree = self.graph.tree()
        self.assertFalse(tree["complete"])
        self.assertEqual(tree["modules"]["P.Pad"], server.LARGE)
        self.assertEqual(tree["edges"], [["P.B", "P.A", 1], ["P.C", "P.B", 1], ["P.D", "P.C", 1]])

    def test_reach_goes_all_the_way_down_and_up(self):
        reach = self.graph.reach([], ["b"], ["P.A"])
        self.assertEqual(reach["below"], {"modules": {"P.A": 1}, "names": ["a"]})
        self.assertEqual(reach["above"], {"modules": {"P.C": 1, "P.D": 1}, "names": []})

    def test_a_dependency_outside_the_declarations_is_ignored(self):
        self.assertEqual(self.graph.reach(["P.E"], [], [])["below"]["modules"], {})
        self.assertEqual(set(self.graph.slice(["P.E"], [], {})["declarations"]), {"e"})

    def test_reach_of_a_module_leaves_out_the_module(self):
        reach = self.graph.reach(["P.C"], [], [])
        self.assertEqual(reach["below"]["modules"], {"P.A": 1, "P.B": 1})
        self.assertEqual(reach["above"]["modules"], {"P.D": 1})

    def test_counts_leave_out_what_deriving_generates(self):
        decls = {"Q.a": {"module": "Q", "deps": []}, "Q.instReprFoo": {"module": "Q", "deps": []},
                 "R.instBEqBar": {"module": "R", "deps": []}}
        self.graph.path.write_text(json.dumps({"root": "Q", "declarations": decls}))
        self.assertEqual(self.graph.tree()["modules"], {"Q": 1})

    def test_a_sorry_is_counted_and_reaches_through_proofs(self):
        entry = lambda module, **fields: {"module": module, "deps": [], "uses": [], **fields}
        decls = {"gap": entry("Q.A", sorry=True), "proof": entry("Q.B", sorryFrom=["gap"]),
                 "next": entry("Q.B", deps=["proof"], sorryFrom=["gap"]), "clean": entry("Q.C")}
        self.graph.path.write_text(json.dumps({"root": "Q", "declarations": decls}))
        self.assertEqual(self.graph.tree()["gaps"], {"Q.A": [1, 0], "Q.B": [0, 2]})
        self.assertEqual(self.graph.tree()["sorryHops"], [])
        decls["proof"]["sorryVia"] = ["gap"]
        self.graph.path.write_text(json.dumps({"root": "Q", "declarations": decls, "note": "changed"}))
        self.assertEqual(self.graph.tree()["sorryHops"], [["proof", "Q.B", "gap", "Q.A"]])
        self.assertEqual(self.graph.reach([], ["gap"], ["Q.B"])["above"]["names"], ["next", "proof"])

    def test_a_missing_graph_gives_an_empty_tree(self):
        graph = server.Graph(self.graph.path.parent / "absent.json")
        self.assertEqual(graph.tree()["modules"], {})

    def test_a_changed_file_is_read_again(self):
        self.graph.current()
        self.graph.path.write_text(json.dumps({"root": "Q", "declarations": {}}))
        self.assertEqual(self.graph.tree()["root"], "Q")

    def test_a_small_project_gets_the_whole_graph(self):
        self.graph.path.write_text(json.dumps({"root": "Q", "declarations": {}, "library": {"Int": {}}}))
        part = self.graph.slice(["Q.A"], [], {})
        self.assertTrue(part["complete"])
        self.assertIn("Int", part["library"])


class StartupTest(unittest.TestCase):
    def run_server(self, *extra):
        return subprocess.run([sys.executable, str(ROOT / "server" / "server.py"), *extra],
                              capture_output=True, text=True, timeout=20)

    def test_version(self):
        self.assertEqual(self.run_server("--version").stdout.strip(), f"lean-review {server.VERSION}")

    def test_refuses_to_start_on_a_broken_marks_file(self):
        with tempfile.TemporaryDirectory() as folder:
            marks = Path(folder) / "marks.json"
            marks.write_text("{")
            result = self.run_server("--docs", folder, "--marks", str(marks), "--prefix", "P")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not valid JSON", result.stderr)

    def test_refuses_a_graph_of_another_format(self):
        with tempfile.TemporaryDirectory() as folder:
            graph = Path(folder) / "graph.json"
            graph.write_text(json.dumps({"format": server.GRAPH_FORMAT + 1}))
            result = self.run_server("--docs", folder, "--marks", str(Path(folder) / "marks.json"),
                                     "--graph", str(graph), "--prefix", "P")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("has format", result.stderr)


if __name__ == "__main__":
    unittest.main()
