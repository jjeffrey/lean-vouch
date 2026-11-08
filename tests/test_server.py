"""The server's promises about marks.json: nothing is lost, whatever happens. Needs only Python."""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from argparse import Namespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lean_vouch import server  # noqa: E402


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

    def test_a_garbled_build_file_is_no_build(self):
        server.args.build.write_text("[1, 2]")
        self.assertIsNone(self.status()["state"])

    def test_a_build_that_died_shows_as_failed(self):
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()
        server.args.build.write_text(json.dumps({"state": "building", "pid": dead.pid}))
        self.assertEqual(self.status()["state"], "failed")

    def test_status_says_how_far_a_build_has_got(self):
        server.args.build.write_text(json.dumps({"state": "building", "pid": os.getpid(), "step": "docs", "step_since": 5.0,
                                                 "done": 3, "total": 10}))
        self.assertEqual({key: self.status()[key] for key in ("step", "step_since", "done", "total")},
                         {"step": "docs", "step_since": 5.0, "done": 3, "total": 10})
        server.args.build.write_text(json.dumps({"state": "done", "step": "docs", "done": 3, "total": 10}))
        self.assertNotIn("step", self.status())

    def test_code_changed_since_the_last_good_build_is_stale(self):
        (self.root / "src" / "A").mkdir(parents=True)
        (self.root / "src" / ".lake").mkdir()
        file = self.root / "src" / "A" / "M.lean"
        file.write_text("")
        (self.root / "src" / ".lake" / "Hidden.lean").write_text("")
        os.utime(file, (1_000, 1_000))
        os.utime(self.root / "src" / ".lake" / "Hidden.lean", (9_000, 9_000))   # Lake's own files don't count
        server.args.source = [self.root / "src"]
        for since, stale in ((None, False), (2_000, False), (500, True)):
            server.newest["checked"] = 0
            server.args.build.write_text(json.dumps({"state": "done", **({"since": since} if since else {})}))
            self.assertEqual(self.status()["stale"], stale, since)
        server.args.build.write_text(json.dumps({"state": "building", "pid": os.getpid(), "since": 500}))
        self.assertFalse(self.status()["stale"], "a build is already on its way")

    def test_a_server_not_started_by_lean_vouch_cant_rebuild(self):
        self.assertFalse(self.status()["can_rebuild"])
        self.assertEqual(self.post("/review/rebuild", {})[0], 409)

    def test_a_page_can_start_one_rebuild_at_a_time(self):
        home = self.root / ".lean-vouch"
        server.args.marks, server.args.build = home / "marks", home / "build.json"
        home.mkdir()
        server.args.build.write_text(json.dumps({"state": "done", "names": ["Lib", "My.Module"]}))
        started = []

        class Running:
            def __init__(self, command, **options):
                started.append((command, options["cwd"]))

            def poll(self):
                return None

        self.addCleanup(setattr, server, "rebuilding", None)
        with unittest.mock.patch.object(server.subprocess, "Popen", Running):
            self.assertTrue(self.status()["can_rebuild"])
            self.assertEqual(self.post("/review/rebuild", {}), (200, {"started": True}))
            self.assertEqual(self.status()["state"], "building", "before the build has said so itself")
            self.assertEqual(self.post("/review/rebuild", {})[0], 409)
        command, cwd = started[0]
        self.assertEqual(command[-3:], ["--rebuild", "Lib", "My.Module"])
        self.assertEqual(cwd, self.root.resolve())
        self.assertEqual(len(started), 1)

    def test_a_build_from_the_terminal_blocks_one_from_a_page(self):
        home = self.root / ".lean-vouch"
        server.args.marks, server.args.build = home / "marks", home / "build.json"
        home.mkdir()
        server.args.build.write_text(json.dumps({"state": "building", "pid": os.getpid()}))
        self.assertEqual(self.post("/review/rebuild", {}), (409, {"error": "A build is already running."}))

    def test_without_docs_the_server_offers_the_diagram(self):
        (self.root / "docs" / "index.html").write_text("<html><body>index</body></html>")
        self.assertTrue(self.status()["docs"])
        server.args.build.write_text(json.dumps({"state": "done", "docs": False}))
        self.assertFalse(self.status()["docs"])
        status, headers = self.raw_with_headers("GET", "/")
        self.assertEqual((status, headers.get("Location")), (302, "/review/diagram"))
        status, body = self.raw("GET", "/Page.html")
        self.assertEqual(status, 404)
        self.assertIn(b"--no-docs", body)
        self.assertIn(b"window.reviewDocs = false", self.get("/review/diagram"))

    def test_a_rebuild_from_a_page_skips_the_docs_if_the_last_run_did(self):
        home = self.root / ".lean-vouch"
        server.args.marks, server.args.build = home / "marks", home / "build.json"
        home.mkdir()
        server.args.build.write_text(json.dumps({"state": "done", "names": ["Lib"], "docs": False}))
        started = []

        class Running:
            def __init__(self, command, **options):
                started.append(command)

            def poll(self):
                return None

        self.addCleanup(setattr, server, "rebuilding", None)
        with unittest.mock.patch.object(server.subprocess, "Popen", Running):
            self.post("/review/rebuild", {})
        self.assertEqual(started[0][-3:], ["--rebuild", "--no-docs", "Lib"])

    def raw_with_headers(self, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
        self.addCleanup(connection.close)
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.headers

    def test_the_build_log_is_served(self):
        self.assertIn(b"No build", self.get("/review/log"))
        server.args.build.with_name("build.log").write_text("error: it broke\n")
        self.assertEqual(self.get("/review/log"), b"error: it broke\n")

    def challenge_graph(self):
        decl = lambda kind, deps=(): {"kind": kind, "module": "M", "lines": [1, 1], "own": kind[0], "deps": list(deps), "uses": []}
        self.graph.write_text(json.dumps({"declarations": {
            "M.d": decl("def"), "M.t": decl("theorem", ["M.d"]), "M.other": decl("theorem")}, "library": {}}))

    def test_theorems_are_chosen_for_the_challenge(self):
        self.challenge_graph()
        state = self.post("/review/challenge", {"names": ["M.t"]})[1]
        self.assertEqual((state["theorems"], state["closure"], state["modules"]), (["M.t"], ["M.d", "M.t"], {"M": 2}))
        stored = json.loads((self.root / "challenge.json").read_text())
        self.assertEqual(stored, {"format": 1, "theorems": ["M.t"]})
        self.assertEqual(json.loads(self.get("/review/challenge"))["theorems"], ["M.t"])

    def test_only_theorems_can_be_chosen(self):
        self.challenge_graph()
        self.assertEqual(self.post("/review/challenge", {"names": ["M.d"]})[0], 400)
        self.assertEqual(self.post("/review/challenge", {"names": ["M.nope"]})[0], 400)
        self.assertFalse((self.root / "challenge.json").exists())

    def test_the_challenge_says_what_youve_agreed_with_as_it_is_now(self):
        self.challenge_graph()
        self.post("/review/challenge", {"names": ["M.t"]})
        self.post("/review/data", {"name": "M.d", "status": "agree"})
        self.post("/review/data", {"name": "M.t", "status": "question"})
        self.assertEqual(json.loads(self.get("/review/challenge"))["agreed"], ["M.d"])
        decls = json.loads(self.graph.read_text())
        decls["declarations"]["M.d"]["own"] = "changed"
        self.graph.write_text(json.dumps(decls))
        self.assertEqual(json.loads(self.get("/review/challenge"))["agreed"], [], "a change below undoes the agreement")

    def test_a_removed_theorem_is_listed_as_missing(self):
        self.challenge_graph()
        self.post("/review/challenge", {"names": ["M.t", "M.other"]})
        decls = json.loads(self.graph.read_text())
        del decls["declarations"]["M.other"]
        self.graph.write_text(json.dumps(decls))
        state = json.loads(self.get("/review/challenge"))
        self.assertEqual((state["theorems"], state["missing"]), (["M.t"], ["M.other"]))

    def test_choosing_changes_the_status_for_open_pages(self):
        self.challenge_graph()
        before = self.status()["marks"]
        self.post("/review/challenge", {"names": ["M.t"]})
        self.assertNotEqual(before, self.status()["marks"])

    def test_a_server_not_started_by_lean_vouch_cant_write_the_challenge(self):
        self.assertFalse(json.loads(self.get("/review/challenge"))["can_write"])
        self.assertEqual(self.post("/review/challenge/write", {})[0], 409)

    def test_writing_the_challenge_runs_the_command(self):
        home = self.root / ".lean-vouch"
        server.args.marks, server.args.build = home / "marks", home / "build.json"
        home.mkdir()
        ran = []

        def run(command, **options):
            ran.append((command, options["cwd"]))
            return subprocess.CompletedProcess(command, 0, stdout="lean-vouch: it matches the project.\n", stderr="")

        with unittest.mock.patch.object(server.subprocess, "run", run):
            self.assertEqual(self.post("/review/challenge/write", {}),
                             (200, {"ok": True, "output": "lean-vouch: it matches the project."}))
        command, cwd = ran[0]
        self.assertEqual(command[-5:], ["--challenge", "--id", "ada", "--name", "Ada"])
        self.assertEqual(cwd, self.root.resolve())

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

    def test_a_broken_marks_file_doesnt_break_status_or_says_why_elsewhere(self):
        self.folder_marks.mkdir()
        self.marks.write_text("{ not json")
        self.assertEqual(self.status()["me"]["id"], "ada")
        status, body = self.raw("GET", "/review/graph?marked")
        self.assertEqual(status, 500)
        self.assertIn("not valid JSON", json.loads(body)["error"])

    def test_someone_elses_unreadable_file_is_left_out(self):
        self.write({"A.b": {"status": "agree", "date": "2026-01-01T00:00:00", "notes": []}}, "cy", "Cy")
        (self.folder_marks / "bo.json").write_text("<<<<<<< HEAD")
        (self.folder_marks / "di.json").write_text(json.dumps({"format": 99, "marks": {}}))
        self.post("/review/data", {"name": "A.b", "status": "agree"})
        self.assertEqual([o["id"] for o in self.shown("A.b")["others"]], ["cy"])
        self.assertEqual(self.status()["unreadable"], ["bo", "di"])
        self.assertEqual((self.folder_marks / "bo.json").read_text(), "<<<<<<< HEAD")

    def test_someone_elses_malformed_entries_are_left_out(self):
        self.folder_marks.mkdir()
        for who, entries in (("bo", {"A.b": "agree"}), ("cy", {"A.b": {"status": "agree", "notes": [{"text": "no date"}]}}),
                             ("di", {"A.b": {"status": ["agree"]}})):
            (self.folder_marks / f"{who}.json").write_text(json.dumps({"format": 3, "marks": entries}))
        self.assertEqual(self.post("/review/data", {"name": "A.b", "status": "agree"})[0], 200)
        self.assertEqual(self.status()["unreadable"], ["bo", "cy", "di"])

    def test_times_are_in_utc(self):
        self.post("/review/data", {"name": "A.b", "status": "agree", "note": "Hm."})
        self.assertRegex(self.shown("A.b")["date"], r"Z$")
        self.assertRegex(self.shown("A.b")["notes"][0]["date"], r"Z$")

    def test_writing_on_a_renamed_name_lands_on_the_new_one(self):
        self.graph.write_text(json.dumps({"renames": {"Old.name": "New.name"}}))
        code, entry = self.post("/review/data", {"name": "Old.name", "note": "Hm."})
        self.assertEqual((code, entry["notes"][0]["text"]), (200, "Hm."))
        self.assertIn("New.name", self.stored())
        self.assertNotIn("Old.name", self.stored())

    def test_a_tools_note_isnt_you_responding(self):
        self.write({"Old.name": {"status": None, "notes": []}})
        self.write({"New.name": {"status": "question", "date": "2026-01-01T00:00:00",
                                 "notes": [{"date": "2026-01-01T00:00:00", "text": "Why?"}]}}, "claude", "Claude")
        self.graph.write_text(json.dumps({"renames": {"Old.name": "New.name"}}))
        self.assertEqual(self.shown("New.name")["attention"], ["Claude"])

    def test_non_ascii_survives_saving(self):
        self.post("/review/data", {"name": "A.b", "note": "∀ x"})
        self.assertIn("∀ x", self.marks.read_bytes().decode("utf-8"))

    def test_head_is_refused_from_another_site_too(self):
        self.assertEqual(self.raw("HEAD", "/Page.html", headers={"Host": "example.com"})[0], 403)
        self.assertEqual(self.raw("HEAD", "/Page.html", headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.raw("HEAD", "/Page.html")[0], 200)

    def test_another_site_cant_load_it_as_a_resource_but_can_link_to_it(self):
        cross = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}
        self.assertEqual(self.raw("GET", "/review/diagram", headers=cross)[0], 403)
        self.assertEqual(self.raw("GET", "/review/diagram", headers={**cross, "Sec-Fetch-Mode": "navigate"})[0], 200)

    def test_pages_cant_be_framed_by_another_site(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
        self.addCleanup(connection.close)
        connection.request("GET", "/review/diagram")
        self.assertEqual(connection.getresponse().getheader("Content-Security-Policy"), "frame-ancestors 'self'")

    def test_source_starts_where_lean_says_past_a_comment_above(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "M.lean").write_text("def a : Nat := 1\n\n/- a plain comment -/\ndef b : Nat := 2\n")
        self.graph.write_text(json.dumps({"declarations": {"b": {"module": "M", "lines": [4, 4], "kind": "def"}}}))
        server.args.source = [self.root / "src"]
        self.assertEqual(json.loads(self.get("/review/source?name=b"))["text"], "def b : Nat := 2")

    def test_your_own_unreadable_file_still_stops_you(self):
        self.folder_marks.mkdir()
        self.marks.write_text("<<<<<<< HEAD")
        self.assertEqual(self.post("/review/data", {"name": "A.b", "status": "agree"})[0], 500)

    def test_a_quiz_answer_alone_can_be_moved_and_deleted(self):
        self.post("/review/data", {"name": "A.b", "note": "one", "quiz": {"name": "x", "description": "y"}})
        self.assertEqual(self.post("/review/move", {"from": "A.b", "to": "A.c"})[0], 200)
        self.assertIn("A.c", json.loads(self.quiz.read_text())["answers"])
        self.assertEqual(self.post("/review/delete", {"name": "A.c"})[0], 200)
        self.assertEqual(json.loads(self.quiz.read_text())["answers"], {})

    def test_a_rename_moves_quiz_answers(self):
        self.post("/review/data", {"name": "Old.name", "note": "one", "quiz": {"name": "x", "description": "y"}})
        self.graph.write_text(json.dumps({"renames": {"Old.name": "New.name"}}))
        self.shown()
        self.assertEqual(list(json.loads(self.quiz.read_text())["answers"]), ["New.name"])

    def test_source_from_a_file_that_got_shorter_says_so(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "M.lean").write_text("theorem t : True := trivial\n")
        self.graph.write_text(json.dumps({"declarations": {"t": {"module": "M", "lines": [40, 41], "kind": "theorem"}}}))
        server.args.source = [self.root / "src"]
        self.assertIn("Rebuild", json.loads(self.get("/review/source?name=t"))["error"])

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


    FINGERPRINTED = {"declarations": {"t": {"module": "M", "own": "t1", "deps": ["d"], "uses": ["l"]},
                                      "d": {"module": "M", "own": "d1", "deps": ["e"], "uses": []},
                                      "e": {"module": "N", "own": "e1", "deps": [], "uses": []}},
                     "library": {"l": {"module": "L", "own": "l1"}}, "moduleDocs": {"M": "m1"}, "modules": {"M": [], "N": [], "E": []}}

    def stored_mark(self, name):
        return json.loads(self.marks.read_text())["marks"][name]

    def test_a_mark_records_everything_below_it_without_being_told(self):
        self.graph.write_text(json.dumps(self.FINGERPRINTED))
        status, _ = self.post("/review/data", {"name": "t", "status": "question"})
        self.assertEqual(status, 200)
        self.assertEqual(self.stored_mark("t")["fingerprint"], {"own": "t1", "deps": {"d": "d1", "e": "e1", "l": "l1"}})

    def test_a_mark_records_the_lean_it_was_made_on(self):
        self.graph.write_text(json.dumps({**self.FINGERPRINTED, "lean": "4.35.0"}))
        self.post("/review/data", {"name": "t", "status": "agree"})
        self.post("/review/data", {"name": "module M", "status": "agree"})
        self.assertEqual(self.stored_mark("t")["fingerprint"]["lean"], "4.35.0")
        self.assertEqual(self.stored_mark("module M")["fingerprint"]["lean"], "4.35.0")

    def test_what_a_request_says_about_a_fingerprint_is_ignored(self):
        self.graph.write_text(json.dumps(self.FINGERPRINTED))
        for sent in ("abc", {"own": "wrong", "deps": {}}):
            self.assertEqual(self.post("/review/data", {"name": "t", "status": "agree", "fingerprint": sent})[0], 200)
            self.assertEqual(self.stored_mark("t")["fingerprint"]["own"], "t1")
        self.assertEqual(self.post("/review/data", {"name": "t", "note": "still working"})[0], 200)

    def test_module_and_library_marks(self):
        self.graph.write_text(json.dumps(self.FINGERPRINTED))
        self.post("/review/data", {"name": "module M", "status": "agree"})
        self.post("/review/data", {"name": "module E", "status": "agree"})
        self.post("/review/data", {"name": "l", "status": "agree"})
        self.assertEqual(self.stored_mark("module M")["fingerprint"], {"own": "m1", "deps": {}})
        self.assertEqual(self.stored_mark("module E")["fingerprint"], {"deps": {}})   # no own, as on a page
        self.assertEqual(self.stored_mark("l")["fingerprint"], {"own": "l1", "deps": {}})

    def test_a_mark_from_a_page_older_than_the_build_is_refused(self):
        self.graph.write_text(json.dumps(self.FINGERPRINTED))
        (self.root / "build.json").write_text(json.dumps({"built": "new"}))
        status, reply = self.post("/review/data", {"name": "t", "status": "agree", "built": "old"})
        self.assertEqual(status, 409)
        self.assertIn("Reload", reply["error"])
        self.assertFalse(self.marks.exists())
        self.assertEqual(self.post("/review/data", {"name": "t", "note": "a note can wait", "built": "old"})[0], 200)
        self.assertEqual(self.post("/review/data", {"name": "t", "status": "agree", "built": "new"})[0], 200)
        self.assertEqual(self.post("/review/data", {"name": "t", "status": "agree"})[0], 200)


    def test_deleting_an_orphan_leaves_the_mark_it_was_renamed_to(self):
        self.graph.write_text(json.dumps({"renames": {"P.dup": "P.keep"}}))
        self.marks.parent.mkdir(exist_ok=True)
        self.marks.write_text(json.dumps({"format": server.MARKS_FORMAT, "name": "Ada", "marks": {
            "P.dup": {"status": "reject", "notes": []}, "P.keep": {"status": "agree", "notes": []}}}))
        self.assertEqual(self.post("/review/delete", {"name": "P.dup"})[0], 200)
        self.assertEqual(json.loads(self.get("/review/data"))["P.keep"]["status"], "agree")
        self.assertNotIn("P.dup", json.loads(self.marks.read_text())["marks"])

    def test_a_page_says_which_build_it_is_from(self):
        (self.root / "build.json").write_text(json.dumps({"built": "the <build>"}))
        (self.root / "docs" / "Linked.html").write_text('<html><body><script src="/review/links.js"></script></body></html>')
        self.assertIn(b'window.reviewBuilt = "the \\u003cbuild>"', self.get("/Linked.html"))
        self.assertIn(b"window.reviewBuilt", self.get("/Page.html"))
        self.assertIn(b"window.reviewBuilt", self.get("/review/diagram"))
        self.assertIn(b"window.reviewBuilt", self.get("/review/quiz"))

    def write_quiz(self, answers):
        self.quiz.parent.mkdir(exist_ok=True)
        answer = lambda text: [{"date": "2026-01-01", "text": text, "quiz": {}}]
        self.quiz.write_text(json.dumps({"format": server.MARKS_FORMAT, "answers": {k: answer(k) for k in answers}}))

    def test_two_old_names_keep_both_quiz_answers(self):
        self.graph.write_text(json.dumps({"renames": {"Old.a": "New.c", "Old.b": "New.c"}}))
        self.write_quiz(["Old.a", "Old.b"])
        self.get("/review/data")
        kept = json.loads(self.quiz.read_text())["answers"]
        self.assertEqual(sorted(a["text"] for answers in kept.values() for a in answers), ["Old.a", "Old.b"])
        self.assertIn("New.c", kept)

    def test_a_chain_of_renames_is_followed(self):
        self.graph.write_text(json.dumps({"renames": {"A": "B", "B": "C"}}))
        status, reply = self.post("/review/data", {"name": "A", "note": "hello"})
        self.assertEqual(status, 200)
        self.assertEqual(reply["notes"][0]["text"], "hello")
        (self.folder_marks / "bob.json").write_text(json.dumps({
            "format": server.MARKS_FORMAT, "name": "Bob", "marks": {"A": {"status": "agree", "notes": []}}}))
        self.assertEqual(json.loads(self.get("/review/data"))["C"]["others"][0]["id"], "bob")

    def test_a_loop_of_renames_ends(self):
        self.assertEqual(server.followed({"A": "B", "B": "A"}, "A"), "B")


    def test_only_newlines_end_a_line(self):
        (self.root / "M.lean").write_text("-- a\x0c comment\ntheorem t : True := trivial\n", encoding="utf-8")
        self.graph.write_text(json.dumps({"declarations": {"t": {"module": "M", "lines": [2, 2], "own": "1", "deps": [], "uses": []}}}))
        server.args.source = [self.root]
        self.assertTrue(json.loads(self.get("/review/source?name=t"))["text"].startswith("theorem t"))


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
        decls = {"Q.a": {"module": "Q", "deps": []}, "Q.instReprFoo": {"module": "Q", "deps": [], "derived": True},
                 "R.instBEqBar": {"module": "R", "deps": [], "derived": True},
                 "S.instBEqBaz": {"module": "S", "deps": []}}   # written by hand, whatever its name
        self.graph.path.write_text(json.dumps({"root": "Q", "declarations": decls}))
        self.assertEqual(self.graph.tree()["modules"], {"Q": 1, "S": 1})

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

    def test_a_page_of_no_module_gets_only_what_marks_need(self):
        self.assertEqual(set(self.graph.slice([], [], {"a": {}})["declarations"]), {"a"})
        self.assertEqual(self.graph.slice([], [], {})["declarations"], {})

    def test_a_small_project_gets_the_whole_graph(self):
        self.graph.path.write_text(json.dumps({"root": "Q", "declarations": {}, "library": {"Int": {}}}))
        part = self.graph.slice(["Q.A"], [], {})
        self.assertTrue(part["complete"])
        self.assertIn("Int", part["library"])


class StartupTest(unittest.TestCase):
    def run_server(self, *extra):
        return subprocess.run([sys.executable, str(ROOT / "lean_vouch" / "server.py"), *extra],
                              capture_output=True, text=True, timeout=20)

    def test_ctrl_c_stops_it_quietly(self):
        with tempfile.TemporaryDirectory() as folder, socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            process = subprocess.Popen([sys.executable, str(ROOT / "lean_vouch" / "server.py"), "--docs", folder, "--marks", str(Path(folder) / "marks"),
                                        "--prefix", "P", "--id", "ada", "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(process.kill)
            process.stdout.readline()
            for _ in range(100):
                try:
                    urlopen(f"http://127.0.0.1:{port}/review/status").read()
                    break
                except OSError:
                    time.sleep(0.05)
            process.send_signal(signal.SIGINT)
            _, errors = process.communicate(timeout=15)
            self.assertNotIn("Traceback", errors)

    def test_refuses_an_unsafe_id(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_server("--docs", folder, "--marks", folder, "--prefix", "P", "--id", "Ada Lovelace")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("can't be an id", result.stderr)

    def test_version(self):
        self.assertEqual(self.run_server("--version").stdout.strip(), f"lean-vouch {server.VERSION}")

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
