// The links in doc-gen4's declarations, on every page that shows them.
//
// Links in docstrings are public URLs. A local links file, {"https://...": "your copy's URL"}, sends
// them to your own copy instead, at the page ending the link's text ("Smith et al., Eq. 5 (p. 3)" or "(page 3)").
//
// With DOCGEN_SRC=file the source links are file:// URLs, which browsers won't follow from a web
// page. They open in VS Code instead, at the line `lineOf(a)` gives, if any.
// Who disagrees with whom, among marks that are current: `mine` is your status, if any.
window.reviewDisagreement = (mine, others) => {
  const differ = others.filter(o => o.status !== mine);
  if (mine && differ.length) return `${differ.map(o => o.name).join(", ")} ${differ.length === 1 ? "disagrees" : "disagree"} with you`;
  return new Set(others.map(o => o.status)).size > 1 ? "others disagree" : null;
};

// Reads a reply. An error says so and throws, so that a page never takes it for what it asked for and
// keeps what was being written.
window.reviewChecked = async response => {
  const value = await response.json();
  if (response.status >= 500 || response.status === 409) { alert(value.error); throw new Error(value.error); }
  return value;
};

// `built` says which build this page shows, for a mark to be refused if the docs have moved on.
window.reviewPost = (path, body) => fetch(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ ...body, built: window.reviewBuilt ?? undefined }),
}).catch(error => { alert("Couldn't reach the lean-vouch server, so this wasn't saved."); throw error; })
  .then(window.reviewChecked);

// Reloads, unless that would lose a note being written.
window.reviewReload = () => {
  const writing = [...document.querySelectorAll("textarea")].some(box => box.value.trim());
  if (!writing || confirm("Reloading will lose the note you're writing. Reload anyway?")) location.reload();
};

// On a large project a page holds part of the graph. Of `names`, those whose marks, or others', rest
// on something it hasn't got, which they'd show as changed. `has` says what it has.
window.reviewMissing = (names, marks, has) => names.filter(name =>
  [marks[name], ...(marks[name]?.others ?? [])].some(mark =>
    Object.entries(mark?.fingerprint?.deps ?? {}).some(([dep, own]) => own !== null && !has(dep))));

// What lies below `names`. Past what a URL can hold, what lies below every mark.
window.reviewBelow = names => fetch(`/review/graph?${names.length > 100 ? "marked"
  : names.map(name => `name=${encodeURIComponent(name)}`).join("&")}`).then(r => r.json());

// Where a theorem's proof starts in its source: the first `:=` outside any brackets, or a `|` case
// starting a line. -1 if it has none.
window.reviewProofStart = text => {
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const two = text.slice(i, i + 2);
    if (two === "--") { i = text.indexOf("\n", i); if (i < 0) return -1; }
    else if (two === "/-") { i = text.indexOf("-/", i) + 1; if (i <= 0) return -1; }
    else if ("([{⟨⦃".includes(text[i])) depth++;
    else if (")]}⟩⦄".includes(text[i])) depth--;
    else if (depth === 0 && two === ":=") return i;
    else if (depth === 0 && text[i] === "|") {
      const line = text.lastIndexOf("\n", i) + 1;
      if (i > line - 1 && !text.slice(line, i).trim()) return line;
    }
  }
  return -1;
};

// A person's color in note threads, the same wherever their name appears.
window.reviewHue = name => [...name].reduce((hue, c) => (hue * 31 + c.codePointAt(0)) % 360, 7);

// Notices what changes while a page is open: anyone's marks, handed to `onMarks`, which updates the
// page in place, and rebuilds, which only offer a reload, so as not to lose a note being written.
// It says how far a rebuild has got, and when the code has changed since the last one, with a
// button to start one when the server can.
window.reviewWatch = onMarks => {
  const banner = document.createElement("div");
  banner.id = "review-build";
  banner.style.cssText = "position: fixed; top: 0.6em; right: 0.6em; z-index: 1000; padding: 0.3em 0.7em; " +
    "border: 1px solid #e07000; border-radius: 6px; background: #fff8ef; color: #333; font-size: 0.9em;";
  banner.hidden = true;
  document.body.append(banner);
  const reload = document.createElement("button");
  reload.textContent = "Reload";
  reload.style.marginLeft = "0.6em";
  reload.onclick = () => reviewReload();
  const button = (text, title, onclick) => {
    const b = document.createElement("button");
    b.textContent = text;
    b.title = title;
    b.style.marginLeft = "0.6em";
    b.onclick = onclick;
    return b;
  };
  const rebuild = button("Rebuild", "Run lean-vouch --rebuild for this project, as it was last run. Open pages offer to reload when it's done.",
    async () => { rebuild.disabled = true; await reviewPost("/review/rebuild", {}).finally(() => { rebuild.disabled = false; }); tick(); });
  const log = document.createElement("a");
  log.textContent = "log";
  log.href = "/review/log";
  log.target = "_blank";
  log.style.marginLeft = "0.6em";
  // Lake finds more to build as it goes, so its total can grow.
  const STEPS = { project: "building the project", graph: "building the declaration graph", docs: "building the docs" };
  // How long the step has taken, since Lake says nothing while a job runs, and one can take many minutes.
  const elapsed = since => {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - since));
    return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s` : `${seconds}s`;
  };
  const progress = now => `Rebuilding: ${STEPS[now.step] ?? "starting"}${now.total ? `, ${now.done} of ${now.total} so far` : ""}` +
    `${typeof now.step_since === "number" ? `, ${elapsed(now.step_since)}` : ""}…`;
  const say = (text, title, ...extra) => {
    banner.hidden = !text;
    banner.title = title ?? "";
    banner.replaceChildren(text ?? "", ...extra);
  };
  let marks, first = true;
  const tick = async () => {
    const now = await fetch("/review/status").then(r => r.json()).catch(() => null);
    if (!now) return;
    for (const me of document.querySelectorAll(".review-me")) {
      me.textContent = `reviewing as ${now.me.name}`;
      me.title = `Your id is ${now.me.id}: the name of your marks file. --name and --id change them.`;
    }
    // Also on the first look: marks can change while a large page loads. Noted first, so a failure
    // isn't repeated on every look.
    const changed = first || now.marks !== marks;
    first = false;
    marks = now.marks;
    if (changed) await onMarks();
    if (now.state === "building") say(progress(now), "Lake finds more to build as it goes, so its total can grow.", log);
    else if (now.state === "failed") say("Build failed; changes are tracked from the last good build.", now.message, log,
      ...(now.can_rebuild ? [rebuild] : []));
    else if (now.built !== (window.reviewBuilt ?? null)) say("Docs updated.", null, reload);
    else if (now.stale) say("The code has changed since the last build.", "Marks still work, against what was last built.",
      now.can_rebuild ? rebuild : " Run lean-vouch to rebuild.");
    else if (now.unreadable.length) say(`Left out marks that can't be read: ${now.unreadable.join(", ")}.`);
    else say(null);
  };
  tick();
  setInterval(tick, 2000);
};

window.reviewLinks = (() => {
  const yourCopies = fetch("/review/links").then(r => r.json());
  async function rewrite(root, lineOf = () => undefined) {
    const copies = await yourCopies;
    for (const a of root.querySelectorAll("a[href]")) {
      const href = a.getAttribute("href");
      if (href.startsWith("file://")) {
        const line = lineOf(a);
        a.href = `vscode://file${href.slice("file://".length)}${line ? `:${line}` : ""}`;
      } else if (copies[href]) {
        const page = a.textContent.match(/\((?:p\.|page)\s*(\d+)\)\s*$/)?.[1];
        a.href = page ? `${copies[href]}?page=${page}` : copies[href];
        a.title = `Your copy${page ? `, page ${page}` : ""}`;
      }
    }
  }
  return { rewrite };
})();
