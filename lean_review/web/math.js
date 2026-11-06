// Math in declarations embedded in the graph and quiz pages. Those sit in shadow roots, which
// doc-gen4's MathJax doesn't reach and whose page-level styles can't enter, so this uses MathJax's
// SVG output, which needs none. The delimiters and skipped tags are doc-gen4's (mathjax-config.js).
window.reviewMath = (() => {
  let started;
  const ready = new Promise(resolve => { started = resolve; });
  window.MathJax = {
    tex: { inlineMath: [["$", "$"]], displayMath: [["$$", "$$"]] },
    svg: { fontCache: "none" },
    options: {
      skipHtmlTags: ["script", "noscript", "style", "textarea", "pre", "code", "annotation",
        "annotation-xml", "decl", "decl_meta", "attributes", "decl_args", "decl_header", "decl_name",
        "decl_type", "equation", "equations", "structure_field", "structure_fields", "constructor",
        "constructors", "instances"],
    },
    startup: {
      typeset: false,
      pageReady: () => MathJax.startup.defaultPageReady().then(started),
    },
  };
  document.head.append(Object.assign(document.createElement("script"), {
    src: "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js", async: true,
  }));
  // MathJax sizes math from the surrounding text, so a panel has to be on the page first. One that
  // is replaced before it gets there is skipped.
  const attached = async root => {
    for (let i = 0; i < 100 && !root.isConnected; i++) await new Promise(r => setTimeout(r, 20));
    return root.isConnected;
  };
  // MathJax typesets one request at a time.
  let queue = ready;
  const typeset = root => queue = queue
    .then(() => attached(root))
    .then(on => on && MathJax.typesetPromise([root]))
    .catch(console.error);
  const css = ' mjx-container[display="true"] { display: block; text-align: center; margin: 0.8em 0; ' +
    "overflow-x: auto; } mjx-assistive-mml { position: absolute !important; width: 1px; height: 1px; " +
    "overflow: hidden; clip: rect(1px, 1px, 1px, 1px); }";
  return { typeset, css };
})();
