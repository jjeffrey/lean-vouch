"""The server's promises about marks.json: nothing is lost, whatever happens. Needs only Python."""

import http.client
import json
import os
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
sys.path.insert(0, str(ROOT))
from lean_review import server  # noqa: E402


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        (root / "docs").mkdir()
        (root / "docs" / "Page.html").write_text("<html><body>page</body></html>")
        (root / "docs" / "Bär.html").write_text("<html><body>page</body></html>")
        (root / "outside.html").write_text("<html><body>outside</body></html>")
        self.root = root
        self.folder_marks = root / "marks"
        self.marks = self.folder_marks / "ada.json"
        self.quiz = root / "quiz" / "ada.json"
        self.graph = root / "graph.json"
        server.args = Namespace(docs=root / "docs", marks=self.folder_marks, quiz=root / "quiz", graph=self.graph,
                                links=root / "links.json", source=None, id="ada", name="Ada", build=root / "build.json")
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

    def write(self, marks, who="ada", name="Ada"):
        self.folder_marks.mkdir(exist_ok=True)
        (self.folder_marks / f"{who}.json").write_text(json.dumps({"format": 3, "name": name, "marks": marks}))

    def shown(self, name=None):
        data = json.loads(self.get("/review/data"))
        return data[name] if name else data

    def raw(self, method, path, body=None, headers={}):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
        self.addCleanup(connection.close)
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        return response.status, response.read()

    def test_pages_outside_the_docs_arent_served(self):
        for path in ("/../outside.html", "/%2e%2e/outside.html", "/..%2Foutside.html"):
            status, body = self.raw("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"outside", body)

    def test_a_page_with_a_non_ascii_name_gets_the_controls(self):
        self.assertIn(b"review.js", self.get("/B%C3%A4r.html"))
        self.assertIn(b"review.js", self.get("/Page.html?focus=a"))

    def test_a_malformed_post_is_a_bad_request(self):
        json_type = {"Content-Type": "application/json"}
        for path, body in (("/review/data", "not json"), ("/review/data", "[]"), ("/review/data", ""),
                           ("/review/data", '{"name": "A.b", "status": []}'), ("/review/data", '{"name": "A.b", "note": {}}'),
                           ("/review/delete", '{"name": []}'), ("/review/move", '{"from": [], "to": "B"}'),
                           ("/review/verdict", '{"name": {}, "verdict": "match"}'), ("/review/reach", '{"modules": [[]]}')):
            self.assertEqual(self.raw("POST", path, body, json_type)[0], 400, body)
        self.assertFalse(self.marks.exists())
        self.assertEqual(self.post("/review/data", {"name": "A.b", "status": "agree"})[0], 200)

    def status(self):
        return json.loads(self.get("/review/status"))

    def test_status_changes_when_anyones_marks_do(self):
        before = self.status()["marks"]
        self.post("/review/data", {"name": "A.b", "status": "agree"})
        after = self.status()["marks"]
        self.assertNotEqual(before, after)
        self.post("/review/data", {"name": "A.b", "id": "claude", "note": "Hm."})
        self.assertNotEqual(after, self.status()["marks"])

    def test_status_says_how_the_build_went(self):
        self.assertIsNone(self.status()["state"])
        server.args.build.write_text(json.dumps({"state": "building", "pid": os.getpid(), "built": "then"}))
        self.assertEqual((self.status()["state"], self.status()["built"]), ("building", "then"))
        server.args.build.write_text(json.dumps({"state": "failed", "message": "It broke.", "pid": 1}))
        self.assertEqual((self.status()["state"], self.status()["message"]), ("failed", "It broke."))

    def test_a_build_that_died_shows_as_failed(self):
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()
        server.args.build.write_text(json.dumps({"state": "building", "pid": dead.pid}))
        self.assertEqual(self.status()["state"], "failed")

    def test_status_names_the_marks_folder_and_who_this_is(self):
        self.assertEqual(self.status()["marks_folder"], str(self.folder_marks.resolve()))
        self.assertEqual(self.status()["me"], {"id": "ada", "name": "Ada"})

    def test_source_edited_since_the_build_says_so(self):
        (self.root / "src").mkdir()
        file = self.root / "src" / "M.lean"
        file.write_text("theorem t : True := trivial\n")
        self.graph.write_text(json.dumps({"declarations": {"t": {"module": "M", "lines": [1, 1], "kind": "theorem"}}}))
        os.utime(self.graph, (2_000_000_000, 2_000_000_000))
        os.utime(file, (1_000_000_000, 1_000_000_000))
        server.args.source = [self.root / "src"]
        self.assertFalse(json.loads(self.get("/review/source?name=t"))["edited"])
        os.utime(file, (2_100_000_000, 2_100_000_000))
        self.assertTrue(json.loads(self.get("/review/source?name=t"))["edited"])

    def test_a_declarations_source_is_found_in_any_source_folder(self):
        (self.root / "src" / "P").mkdir(parents=True)
        (self.root / "src" / "P" / "M.lean").write_text("theorem t : True := trivial\n")
        self.graph.write_text(json.dumps({"declarations": {"P.t": {"module": "P.M", "lines": [1, 1], "kind": "theorem"}}}))
        server.args.source = [self.root / "lib", self.root / "src"]
        found = json.loads(self.get("/review/source?name=P.t"))
        self.assertEqual((found["file"], found["text"]), (str(Path("P/M.lean")), "theorem t : True := trivial"))
        server.args.source = [self.root / "lib"]
        self.assertIn("not found", json.loads(self.get("/review/source?name=P.t"))["error"])

    def test_another_site_cant_change_marks(self):
        json_type = {"Content-Type": "application/json"}
        for headers in ({"Content-Type": "text/plain"}, {**json_type, "Origin": "https://example.com"},
                        {**json_type, "Host": "example.com"}):
            code, _ = self.post("/review/data", {"name": "A.b", "status": "agree"}, headers)
            self.assertIn(code, (403, 415), headers)
        self.assertFalse(self.marks.exists())

    def test_another_domain_cant_read(self):
        request = Request(self.url("/review/data"), headers={"Host": "example.com"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_the_pages_own_origin_is_accepted(self):
        port = self.httpd.server_port
        for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            code, _ = self.post("/review/data", {"name": "A.b", "status": "agree"},
                                {"Content-Type": "application/json", "Origin": origin})
            self.assertEqual(code, 200)

    def test_a_mark_and_note_are_saved_and_read_back(self):
        self.post("/review/data", {"name": "A.b", "status": "agree"})
        self.post("/review/data", {"name": "A.b", "note": "Looks right."})
        entry = self.shown("A.b")
        self.assertEqual(entry["status"], "agree")
        self.assertEqual([(n["by"], n["text"], n["mine"]) for n in entry["notes"]], [("Ada", "Looks right.", True)])
        stored = json.loads(self.marks.read_text())
        self.assertEqual((stored["format"], stored["name"]), (3, "Ada"))
        self.assertNotIn("by", stored["marks"]["A.b"]["notes"][0], "the file says whose it is")

    def test_a_declaration_named_like_a_key_is_an_ordinary_mark(self):
        self.post("/review/data", {"name": "format", "status": "agree"})
        self.assertEqual(self.shown("format")["status"], "agree")

    def test_a_mark_can_be_cleared(self):
        self.post("/review/data", {"name": "A.b", "status": "agree"})
        self.post("/review/data", {"name": "A.b", "status": None})
        self.assertIsNone(self.shown("A.b")["status"])

    def test_a_question_saves_without_a_note(self):
        self.assertEqual(self.post("/review/data", {"name": "A.b", "status": "question"})[0], 200)
        self.assertEqual(self.shown("A.b")["status"], "question")

    def test_a_bare_question_is_your_own_reminder(self):
        self.write({"A.b": {"status": "question", "date": "2026-01-01T00:00:00", "notes": []}}, "claude", "Claude")
        self.assertEqual(self.shown("A.b")["attention"], [])

    def test_a_note_turns_a_bare_question_into_asking(self):
        self.write({"A.b": {"status": "agree", "date": "2026-01-15T00:00:00", "notes": []}})
        self.write({"A.b": {"status": "question", "date": "2026-01-01T00:00:00",
                            "notes": [{"date": "2026-02-01T00:00:00", "text": "Why?"}]}}, "claude", "Claude")
        self.assertEqual(self.shown("A.b")["attention"], ["Claude"])

    def test_a_note_alone_doesnt_ask_anything(self):
        self.write({"A.b": {"status": None, "notes": [{"date": "2026-02-01T00:00:00", "text": "FYI."}]}}, "claude", "Claude")
        self.assertEqual(self.shown("A.b")["attention"], [])

    def test_two_people_keep_their_own_marks(self):
        self.write({"A.b": {"status": "reject", "date": "2026-01-01T00:00:00", "notes": [
            {"date": "2026-01-01T00:00:00", "text": "No."}]}}, "bo", "Bo")
        self.post("/review/data", {"name": "A.b", "status": "agree", "note": "Yes."})
        entry = self.shown("A.b")
        self.assertEqual(entry["status"], "agree")
        self.assertEqual([(o["id"], o["name"], o["status"]) for o in entry["others"]], [("bo", "Bo", "reject")])
        self.assertEqual([(n["by"], n["mine"]) for n in entry["notes"]], [("Bo", False), ("Ada", True)])
        self.assertEqual(json.loads((self.folder_marks / "bo.json").read_text())["marks"]["A.b"]["status"], "reject")

    def test_only_others_marks_show_for_a_declaration_you_havent_touched(self):
        self.write({"A.b": {"status": "agree", "date": "2026-01-01T00:00:00", "notes": []}}, "bo", "Bo")
        self.assertIsNone(self.shown("A.b")["status"])

    def test_two_people_of_one_name_stay_apart(self):
        self.write({"A.b": {"status": "agree", "date": "2026-01-01T00:00:00", "notes": []}}, "ada-1234", "Ada")
        self.post("/review/data", {"name": "A.b", "status": "reject"})
        self.assertEqual([(o["id"], o["status"]) for o in self.shown("A.b")["others"]], [("ada-1234", "agree")])

    def test_someone_elses_question_needs_attention_until_you_mark_or_reply(self):
        self.write({"A.b": {"status": "question", "date": "2026-01-01T00:00:00", "notes": [{"date": "2026-01-01T00:00:00", "text": "Why?"}]}}, "claude", "Claude")
        self.assertEqual(self.shown("A.b")["attention"], ["Claude"])
        self.post("/review/data", {"name": "A.b", "note": "Not a concern."})
        self.assertEqual(self.shown("A.b")["attention"], [])
        self.write({"A.c": {"status": "question", "date": "2026-01-01T00:00:00", "notes": [{"date": "2026-01-01T00:00:00", "text": "Why?"}]}}, "claude", "Claude")
        self.post("/review/data", {"name": "A.c", "status": "agree"})
        self.assertEqual(self.shown("A.c")["attention"], [])

    def test_a_question_asked_after_your_mark_needs_attention(self):
        self.write({"A.b": {"status": "agree", "date": "2026-01-01T00:00:00", "notes": []}})
        self.write({"A.b": {"status": "question", "date": "2026-02-01T00:00:00", "notes": [{"date": "2026-02-01T00:00:00", "text": "Why?"}]}}, "claude", "Claude")
        self.assertEqual(self.shown("A.b")["attention"], ["Claude"])

    def test_an_answer_to_your_question_needs_attention(self):
        self.post("/review/data", {"name": "A.b", "status": "question", "note": "Why?"})
        self.assertEqual(self.shown("A.b")["attention"], [])
        self.write({"A.b": {"status": None, "notes": [{"date": "2999-01-01T00:00:00", "text": "Because."}]}}, "claude", "Claude")
        self.assertEqual(self.shown("A.b")["attention"], ["Claude"])

    def test_a_new_note_on_a_raised_question_needs_attention_again(self):
        self.write({"A.b": {"status": "agree", "date": "2026-03-01T00:00:00", "notes": []}})
        self.write({"A.b": {"status": "question", "date": "2026-01-01T00:00:00",
                            "notes": [{"date": "2026-02-01T00:00:00", "text": "First."}]}}, "claude", "Claude")
        self.assertEqual(self.shown("A.b")["attention"], [], "you've seen it")
        self.write({"A.b": {"status": "question", "date": "2026-01-01T00:00:00",
                            "notes": [{"date": "2026-04-01T00:00:00", "text": "One more thing."}]}}, "claude", "Claude")
        self.assertEqual(self.shown("A.b")["attention"], ["Claude"])

    def test_a_plain_note_needs_no_attention(self):
        self.write({"A.b": {"status": "agree", "date": "2026-01-01T00:00:00", "notes": []}})
        self.write({"A.b": {"status": "reject", "date": "2026-02-01T00:00:00",
                            "notes": [{"date": "2026-02-01T00:00:00", "text": "No."}]}}, "bo", "Bo")
        self.assertEqual(self.shown("A.b")["attention"], [])

    def test_your_own_question_never_needs_your_attention(self):
        self.post("/review/data", {"name": "A.b", "status": "question", "note": "Why?"})
        self.assertEqual(self.shown("A.b")["attention"], [])

    def test_an_assistant_can_write_as_itself(self):
        self.post("/review/data", {"name": "A.b", "id": "claude", "author": "Claude", "status": "question", "note": "Is this right?"})
        self.assertFalse(self.marks.exists())
        entry = self.shown("A.b")
        self.assertEqual(entry["attention"], ["Claude"])
        self.assertEqual([(n["by"], n["mine"]) for n in entry["notes"]], [("Claude", False)])

    def test_an_unsafe_id_is_refused(self):
        code, _ = self.post("/review/data", {"name": "A.b", "id": "../evil", "note": "x"})
        self.assertEqual(code, 400)

    def test_invalid_json_is_refused_and_left_alone(self):
        self.folder_marks.mkdir()
        self.marks.write_text("{ not json")
        code, body = self.post("/review/data", {"name": "A.b", "status": "agree"})
        self.assertEqual(code, 500)
        self.assertIn("not valid JSON", body["error"])
        self.assertEqual(self.marks.read_text(), "{ not json")

    def test_a_newer_format_is_refused_and_left_alone(self):
        self.folder_marks.mkdir()
        text = json.dumps({"format": 99, "marks": {}})
        self.marks.write_text(text)
        code, body = self.post("/review/data", {"name": "A.b", "status": "agree"})
        self.assertEqual(code, 500)
        self.assertIn("format 99", body["error"])
        self.assertEqual(self.marks.read_text(), text)

    def test_a_save_leaves_no_temporary_file(self):
        self.post("/review/data", {"name": "A.b", "status": "agree"})
        self.assertEqual([p.name for p in self.folder_marks.iterdir()], ["ada.json"])

    def test_a_rename_moves_the_mark_and_says_so(self):
        self.write({"Old.name": {"status": "agree", "notes": []}})
        self.graph.write_text(json.dumps({"renames": {"Old.name": "New.name"}}))
        marks = self.shown()
        self.assertNotIn("Old.name", marks)
        self.assertEqual(marks["New.name"]["status"], "agree")
        self.assertIn("renamed", marks["New.name"]["notes"][-1]["text"])

    def test_a_rename_never_overwrites_an_existing_mark(self):
        self.write({"Old.name": {"status": "agree", "notes": []}, "New.name": {"status": "reject", "notes": []}})
        self.graph.write_text(json.dumps({"renames": {"Old.name": "New.name"}}))
        marks = self.shown()
        self.assertEqual(marks["Old.name"]["status"], "agree")
        self.assertEqual(marks["New.name"]["status"], "reject")

    def test_someone_elses_mark_follows_a_rename_without_touching_their_file(self):
        self.write({"Old.name": {"status": "agree", "notes": []}}, "bo", "Bo")
        before = (self.folder_marks / "bo.json").read_text()
        self.graph.write_text(json.dumps({"renames": {"Old.name": "New.name"}}))
        self.assertEqual(self.shown("New.name")["others"][0]["id"], "bo")
        self.assertEqual((self.folder_marks / "bo.json").read_text(), before)

    def test_moving_a_mark_onto_another_is_refused(self):
        self.write({"A": {"status": "agree", "notes": []}, "B": {"status": "reject", "notes": []}})
        code, _ = self.post("/review/move", {"from": "A", "to": "B"})
        self.assertEqual(code, 400)
        self.assertEqual(self.stored()["B"]["status"], "reject")

    def test_an_invalid_status_is_refused(self):
        code, _ = self.post("/review/data", {"name": "A.b", "status": "maybe"})
        self.assertEqual(code, 400)
        self.assertFalse(self.marks.exists())

    def test_asking_for_what_the_marks_need_returns_only_that(self):
        decls = {f"x{i}": {"module": "P.X", "deps": [], "uses": []} for i in range(server.LARGE + 1)}
        self.graph.write_text(json.dumps({"root": "P", "declarations": decls}))
        self.write({"x1": {"status": "agree", "notes": []}})
        self.assertEqual(list(json.loads(self.get("/review/graph?marked"))["declarations"]), ["x1"])

    def test_a_module_named_like_the_tool_keeps_its_page(self):
        folder = server.args.docs / "review"
        folder.mkdir()
        (folder / "graph.html").write_text("<html><body>module page</body></html>")
        self.assertIn(b"module page", self.get("/review/graph.html"))
        self.assertNotIn(b"module page", self.get("/review/diagram"))

    def test_no_url_of_the_tool_can_be_a_module_page(self):
        self.assertFalse([path for path in server.PAGES if path.endswith(".html")])

    def test_the_tools_pages_are_served(self):
        for path in server.PAGES:
            self.assertTrue(self.get(path))

    def test_a_verdict_goes_on_the_latest_quiz_answer(self):
        self.post("/review/data", {"name": "A.b", "note": "one", "quiz": {"name": "x", "description": "y"}})
        self.post("/review/data", {"name": "A.b", "note": "two", "quiz": {"name": "x", "description": "z"}})
        code, _ = self.post("/review/verdict", {"name": "A.b", "verdict": "partly"})
        self.assertEqual(code, 200)
        answers = json.loads(self.quiz.read_text())["answers"]["A.b"]
        self.assertEqual(["verdict" in a["quiz"] for a in answers], [False, True])

    def test_quiz_answers_stay_out_of_the_marks_file_but_show_on_pages(self):
        self.post("/review/data", {"name": "A.b", "note": "one", "quiz": {"name": "x", "description": "y"}})
        self.assertFalse(self.marks.exists())
        note = self.shown("A.b")["notes"][0]
        self.assertEqual((note["text"], note["quiz"]["name"], note["mine"]), ("one", "x", True))

    def test_another_persons_quiz_answers_are_not_shown(self):
        other = self.quiz.with_name("bo.json")
        other.parent.mkdir()
        other.write_text(json.dumps({"format": 3, "answers": {"A.b": [{"date": "2026-01-01", "text": "secret", "quiz": {}}]}}))
        self.assertNotIn("A.b", self.shown())

    def test_a_verdict_needs_a_quiz_answer_and_a_known_value(self):
        self.post("/review/data", {"name": "A.b", "note": "plain"})
        self.assertEqual(self.post("/review/verdict", {"name": "A.b", "verdict": "match"})[0], 400)
        self.post("/review/data", {"name": "A.b", "note": "q", "quiz": {"name": "x", "description": "y"}})
        self.assertEqual(self.post("/review/verdict", {"name": "A.b", "verdict": "maybe"})[0], 400)

    def test_pages_get_the_review_script(self):
        self.assertIn(b"/review/review.js", self.get("/Page.html"))


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
        return subprocess.run([sys.executable, str(ROOT / "lean_review" / "server.py"), *extra],
                              capture_output=True, text=True, timeout=20)

    def test_refuses_an_unsafe_id(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_server("--docs", folder, "--marks", folder, "--prefix", "P", "--id", "Ada Lovelace")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("can't be an id", result.stderr)

    def test_version(self):
        self.assertEqual(self.run_server("--version").stdout.strip(), f"lean-review {server.VERSION}")

    def test_refuses_to_start_on_a_broken_marks_file(self):
        with tempfile.TemporaryDirectory() as folder:
            marks = Path(folder) / "marks"
            marks.mkdir()
            (marks / "ada.json").write_text("{")
            result = self.run_server("--docs", folder, "--marks", str(marks), "--prefix", "P", "--id", "ada")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not valid JSON", result.stderr)

    def test_refuses_a_graph_of_another_format(self):
        with tempfile.TemporaryDirectory() as folder:
            graph = Path(folder) / "graph.json"
            graph.write_text(json.dumps({"format": server.GRAPH_FORMAT + 1}))
            result = self.run_server("--docs", folder, "--marks", str(Path(folder) / "marks"),
                                     "--graph", str(graph), "--prefix", "P", "--id", "ada")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("has format", result.stderr)


if __name__ == "__main__":
    unittest.main()
