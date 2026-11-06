// The links in doc-gen4's declarations, on every page that shows them.
//
// Links in docstrings are public URLs. A local links file, {"https://...": "your copy's URL"}, sends
// them to your own copy instead, at the page ending the link's text ("Smith et al., Eq. 5 (p. 3)" or "(page 3)").
//
// With DOCGEN_SRC=file the source links are file:// URLs, which browsers won't follow from a web
// page. They open in VS Code instead, at the line `lineOf(a)` gives, if any.
// A person's color in note threads, the same wherever their name appears.
window.reviewHue = name => [...name].reduce((hue, c) => (hue * 31 + c.codePointAt(0)) % 360, 7);

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
