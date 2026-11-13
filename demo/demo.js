// The web demo's runtime. lean-vouch's server is plain Python, so this runs it in the page with
// Pyodide and answers the page's requests to /review/... with it, as the server on your computer
// would. What you mark is kept in this browser, apart from the example marks it starts with.
(() => {
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
  const realFetch = window.fetch.bind(window);
  // Writing a challenge, and rebuilding, need Lean, which the demo hasn't got.
  const NEEDS_LEAN = new Set(["/review/challenge/write", "/review/rebuild"]);

  const BOOT = `
import http.client, io, shutil, sys, traceback
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "/demo")
from lean_vouch import server

saved = Path("/demo/saved")
if not (saved / "marks").exists():
    shutil.copytree("/demo/seed", saved / "marks")
server.args = SimpleNamespace(
    docs=Path("/demo/nodocs"), marks=saved / "marks", quiz=saved / "quiz", id="ada", name="Ada",
    graph=Path("/demo/project/graph.json"), prefix="ReviewExample", source=[Path("/demo/project")],
    build=Path("/demo/build.json"), links=saved / "links.local.json", port=0)
# Unpacked just now, in no particular order: the graph is the latest build of these files.
server.args.graph.touch()
server.graph = server.Graph(server.args.graph)

def handle(method, url, body):
    """A request, as the server would answer it over HTTP: its status, type and body."""
    body = bytes(body)
    h = server.Handler.__new__(server.Handler)
    head = f"Host: 127.0.0.1:0\\r\\nContent-Type: application/json\\r\\nContent-Length: {len(body)}\\r\\n\\r\\n"
    h.headers = http.client.parse_headers(io.BytesIO(head.encode()))
    h.command, h.path, h.request_version = method, url, "HTTP/1.1"
    h.requestline = f"{method} {url} HTTP/1.1"
    h.server, h.client_address, h.close_connection = SimpleNamespace(server_port=0), ("127.0.0.1", 0), True
    h.directory = str(server.args.docs)
    h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
    try:
        (h.do_GET if method == "GET" else h.do_POST)()
    except Exception:
        return 500, "text/plain", traceback.format_exc()
    head, _, rest = h.wfile.getvalue().partition(b"\\r\\n\\r\\n")
    lines = head.decode("latin-1").split("\\r\\n")
    kind = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("content-type:")), "text/plain")
    return int(lines[0].split()[1]), kind, rest.decode("utf-8")
`;

  let py = null;
  const ready = (async () => {
    await new Promise((resolve, reject) => {
      const script = Object.assign(document.createElement("script"), { src: PYODIDE + "pyodide.js", onload: resolve, onerror: reject });
      document.head.append(script);
    });
    py = await loadPyodide({ indexURL: PYODIDE });
    py.unpackArchive(await realFetch("demo.zip").then(r => r.arrayBuffer()), "zip", { extractDir: "/demo" });
    py.FS.mkdirTree("/demo/saved");
    py.FS.mount(py.FS.filesystems.IDBFS, {}, "/demo/saved");
    await sync(true);
    py.runPython(BOOT);
    await sync(false);
    say("");
  })();
  ready.catch(error => say(`The demo couldn't start: ${error}`));

  // Copies the saved files between Pyodide and the browser's IndexedDB: loading, or saving.
  const sync = loading => new Promise((resolve, reject) => py.FS.syncfs(loading, e => e ? reject(e) : resolve()));

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const at = url.pathname.indexOf("/review/");
    if (at < 0) return realFetch(input, init);
    const path = url.pathname.slice(at);
    const method = (init.method ?? "GET").toUpperCase();
    if (NEEDS_LEAN.has(path))
      return Response.json({ error: "This demo can't run Lean. On your own computer, lean-vouch does this for you." }, { status: 400 });
    await ready;
    const body = typeof init.body === "string" ? new TextEncoder().encode(init.body) : new Uint8Array();
    const [status, kind, text] = py.globals.get("handle")(method, path + url.search, body).toJs();
    if (method !== "GET") await sync(false);
    return new Response(text, { status, headers: { "Content-Type": kind } });
  };

  // A line above the page: what this is, and a way back to the example marks.
  let line = null;
  function say(text) {
    if (!line) return;
    line.querySelector(".demo-status").textContent = text;
  }
  document.addEventListener("DOMContentLoaded", () => {
    line = document.createElement("div");
    line.className = "demo-line";
    line.innerHTML = `<b>lean-vouch demo</b>: a small example project, with lean-vouch running in your browser.
      Your marks and notes stay in this browser. <span class="demo-status">Starting Python…</span>
      <button type="button">Start again</button>
      <a href="https://github.com/jjeffrey/lean-vouch">lean-vouch on GitHub</a>`;
    line.querySelector("button").onclick = async () => {
      await ready;
      py.runPython(`import shutil; shutil.rmtree("/demo/saved/marks", ignore_errors=True); shutil.rmtree("/demo/saved/quiz", ignore_errors=True)
from pathlib import Path; Path("/demo/saved/challenge.json").unlink(missing_ok=True)`);
      await sync(false);
      location.reload();
    };
    const style = document.createElement("style");
    style.textContent = `.demo-line { background: #fff8db; border: 1px solid #e6d27a; border-radius: 4px;
        padding: 0.4em 0.8em; margin-bottom: 0.6em; font-size: 0.9em; }
      .demo-line button { margin-left: 0.6em; } .demo-line a { margin-left: 0.6em; }
      .demo-line .demo-status { color: #8a6d00; }`;
    document.head.append(style);
    document.body.prepend(line);
    ready.then(() => say("")).catch(error => say(`The demo couldn't start: ${error}`));
  });
})();
