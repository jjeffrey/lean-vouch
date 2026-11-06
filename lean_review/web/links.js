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

// A person's color in note threads, the same wherever their name appears.
window.reviewHue = name => [...name].reduce((hue, c) => (hue * 31 + c.codePointAt(0)) % 360, 7);

// Notices what changes while a page is open: anyone's marks, handed to `onMarks`, which updates the
// page in place, and rebuilds, which only offer a reload, so as not to lose a note being written.
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
  reload.onclick = () => location.reload();
  const say = (text, title, ...extra) => {
    banner.hidden = !text;
    banner.title = title ?? "";
    banner.replaceChildren(text ?? "", ...extra);
  };
  let marks, built, first = true;
  const tick = async () => {
    const now = await fetch("/review/status").then(r => r.json()).catch(() => null);
    if (!now) return;
    for (const me of document.querySelectorAll(".review-me")) {
      me.textContent = `reviewing as ${now.me.name}`;
      me.title = `Your id is ${now.me.id}: the name of your marks file. --name and --id change them.`;
    }
    if (first) built = now.built;
    // Also on the first look: marks can change while a large page loads.
    if (first || now.marks !== marks) await onMarks();
    first = false;
    marks = now.marks;
    if (now.state === "building") say("Rebuilding…");
    else if (now.state === "failed") say("Build failed; showing the last good version.", now.message);
    else if (now.built !== built) say("Docs updated.", null, reload);
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
