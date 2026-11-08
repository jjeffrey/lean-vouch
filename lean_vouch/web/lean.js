// Lean code and docstrings, shown without doc-gen4: for a run with --no-docs, and quiz mode.
// Everything is built as page elements from text, never as HTML, since a docstring may come from
// someone else's project.
window.reviewLean = (() => {
  const KEYWORDS = new Set(("theorem lemma def abbrev instance structure class inductive where extends deriving " +
    "fun λ let have show from by at with match if then else do return for in open namespace section end " +
    "variable universe calc example axiom opaque noncomputable partial private protected mutual " +
    "termination_by decreasing_by local scoped attribute macro syntax notation infix infixl infixr prefix postfix " +
    "Type Prop Sort").split(" "));
  // One token at a time: comments, strings, numbers, names, and anything else as it is.
  const TOKEN = new RegExp([
    String.raw`(?<comment>--[^\n]*|\/-[\s\S]*?-\/)`,
    String.raw`(?<string>"(?:[^"\\]|\\.)*")`,
    String.raw`(?<number>\b\d+(?:\.\d+)?\b)`,
    String.raw`(?<name>«[^»]*»|[\p{L}_][\p{L}\p{N}_'!?₀-₉ₐ-ₜᵢ-ᵪ]*(?:\.(?:«[^»]*»|[\p{L}_][\p{L}\p{N}_'!?₀-₉ₐ-ₜᵢ-ᵪ]*))*)`,
  ].join("|"), "gu");

  // `text` as colored Lean. `link(name)` returns an element for a name worth linking, or null.
  function highlight(text, link = () => null) {
    const out = document.createDocumentFragment();
    let at = 0;
    const span = (cls, s) => Object.assign(document.createElement("span"), { className: cls, textContent: s });
    for (const m of text.matchAll(TOKEN)) {
      if (m.index > at) out.append(text.slice(at, m.index));
      const { comment, string, number, name } = m.groups;
      if (comment) out.append(span("lean-comment", comment));
      else if (string) out.append(span("lean-string", string));
      else if (number) out.append(span("lean-number", number));
      else if (KEYWORDS.has(name)) out.append(span("lean-keyword", name));
      else out.append(link(name) ?? name);
      at = m.index + m[0].length;
    }
    out.append(text.slice(at));
    return out;
  }

  // Of the names a declaration's code could mean by `written`, the first the graph knows: written in
  // full, or under one of the namespaces the declaration is in, innermost first.
  function resolve(written, within, known) {
    const parts = within.split(".");
    for (let i = parts.length; i >= 0; i--) {
      const candidate = [...parts.slice(0, i), written].join(".");
      if (known(candidate)) return candidate;
    }
    return null;
  }

  // A docstring's Markdown: paragraphs, headings, lists, code blocks, `code`, **bold**, *emphasis*
  // and [links](https://...). Math is left as text, for MathJax.
  function markdown(source) {
    const root = document.createElement("div");
    root.className = "lean-doc";
    const inline = (text, into) => {
      const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
      let at = 0;
      for (const m of text.matchAll(pattern)) {
        into.append(text.slice(at, m.index));
        if (m[1] !== undefined) into.append(Object.assign(document.createElement("code"), { textContent: m[1] }));
        else if (m[2] !== undefined) into.append(inline(m[2], document.createElement("strong")));
        else if (m[3] !== undefined) into.append(inline(m[3], document.createElement("em")));
        else into.append(Object.assign(document.createElement("a"), { textContent: m[4], href: m[5], target: "_blank", rel: "noopener" }));
        at = m.index + m[0].length;
      }
      into.append(text.slice(at));
      return into;
    };
    const lines = source.replace(/\s+$/, "").split("\n");
    for (let i = 0; i < lines.length;) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        const code = [];
        for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
        i++;
        root.append(Object.assign(document.createElement("pre"), { textContent: code.join("\n") }));
      } else if (/^#{1,6}\s/.test(line)) {
        root.append(inline(line.replace(/^#+\s*/, ""), document.createElement("h4")));
        i++;
      } else if (/^\s*([-*+]|\d+\.)\s/.test(line)) {
        const list = document.createElement(/^\s*\d/.test(line) ? "ol" : "ul");
        for (; i < lines.length && /^\s*([-*+]|\d+\.)\s/.test(lines[i]); i++)
          list.append(inline(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""), document.createElement("li")));
        root.append(list);
      } else if (!line.trim()) i++;
      else {
        const para = [];
        for (; i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s|[-*+]\s|\d+\.\s)/.test(lines[i]); i++) para.push(lines[i]);
        root.append(inline(para.join(" "), document.createElement("p")));
      }
    }
    return root;
  }

  const css = ".lean-keyword { color: #8b2fc9; font-weight: 600; } .lean-comment { color: #6a737d; font-style: italic; } " +
    ".lean-string { color: #22863a; } .lean-number { color: #005cc5; } .lean-doc p { margin: 0.3em 0; } " +
    ".lean-doc h4 { margin: 0.5em 0 0.2em; } .lean-doc pre { background: #f6f8fa; padding: 0.4em; } " +
    ".lean-doc ul, .lean-doc ol { margin: 0.2em 0; padding-left: 1.4em; }";
  document.head.append(Object.assign(document.createElement("style"), { textContent: css }));
  return { highlight, resolve, markdown };
})();
