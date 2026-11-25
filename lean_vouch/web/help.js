// The tutorial: everything the pages would otherwise explain in place, in one dialog, opened from
// the ? in the header. It opens by itself the first time someone visits, once per browser.
// reviewHelp.button(where, section) adds the ?; reviewHelp.open(section) shows it.
window.reviewHelp = (() => {
  const SEEN = "lean-vouch.help-seen";
  const swatch = (fill, border = "#888", style = "solid") =>
    `<i class="help-swatch" style="background:${fill};border:2px ${style} ${border}"></i>`;
  const SECTIONS = [
    ["start", "What this is", `
      <p>lean-vouch helps you check that a Lean project's definitions and theorem statements mean
      what they should. Lean checks the proofs; whether a statement says what you meant is up to
      people.</p>
      <p>The diagram shows the project's declarations, grouped in boxes by folder and module. Each
      arrow points from a declaration to one it depends on, so what everything rests on is at the
      bottom. For a theorem only its statement counts, since Lean checks the proof; for a
      definition, its statement and body.</p>
      <p>Mark each one as you read it. Your marks are saved to a file of your own, so a team can
      share them, see where they disagree, and talk it over in notes.</p>`],
    ["marks", "Marking", `
      <p>Click a declaration to see its statement, code and notes beside the diagram, and mark it:</p>
      <ul class="help-list">
        <li>${swatch("#c8ecc8", "#33aa33")}<b>✓</b> I agree with this</li>
        <li>${swatch("#e4f2c4", "#7fae2e")}<b>~</b> looks right at a glance, not checked closely</li>
        <li>${swatch("#fbe9b0", "#cc9900")}<b>?</b> I have a question</li>
        <li>${swatch("#f6c4c4", "#cc3333")}<b>✗</b> this seems wrong</li>
        <li><span class="help-star">★</span> especially useful, apart from the mark</li>
      </ul>
      <p>Notes go under the mark, for everyone. <span class="help-attention">!</span> means someone has
      written where you asked or were asked; reply, mark, or <b>Dismiss</b> it. "Marks of" shows
      someone else's marks, or where people disagree.</p>`],
    ["moving", "Getting around", `
      <ul class="help-list keys">
        <li><kbd>click</kbd> a box to open or close it; opening a module shows all its code</li>
        <li><kbd>click</kbd> a declaration to see it, with <span class="help-down">what it depends on</span>
          and <span class="help-up">what depends on it</span> highlighted</li>
        <li><kbd>shift</kbd>-click anything to color everything it rests on and everything that uses
          it, however far away</li>
        <li><kbd>cmd</kbd> or <kbd>ctrl</kbd>-click boxes, here or in the list, to show only those and
          the arrows between them</li>
        <li><kbd>scroll</kbd> to zoom and drag to move around; click empty space to clear</li>
      </ul>
      <p>The list on the left finds folders and modules by name. <b>View</b> chooses what the diagram
      draws: boxes, arrows between folders, the library definitions the project uses. Drag the lines
      beside the panels to resize them, or hide them with « and ».</p>`],
    ["changes", "Changes and sorry", `
      <ul class="help-list">
        <li>${swatch("#fff", "#f0a020")}changed since your mark: look again</li>
        <li>${swatch("#fff", "#f0a020", "dashed")}something it rests on changed; click it to follow
          the way down to the change</li>
        <li>${swatch("#fff", "#c62828")}contains <code>sorry</code></li>
        <li>${swatch("#fff", "#c62828", "dashed")}relies on a <code>sorry</code>, if only through a
          proof; dashed red arrows lead to it</li>
      </ul>
      <p><b>What changed</b> lists the changes your marks rest on, lowest first. After you edit the
      project, the page offers to rebuild it.</p>`],
    ["quiz", "The quiz", `
      <p>The quiz shows a statement without its name or docstring. Write what you think it says,
      then compare: a mismatch means you misread it, or the name or docstring is wrong. Answers
      stay with you.</p>
      <p><kbd>cmd</kbd>-<kbd>enter</kbd> saves or moves on, and <kbd>1</kbd> <kbd>2</kbd>
      <kbd>3</kbd> say how it compared. Names you've marked ✓ or ? can be looked up as you go.</p>`],
    ["challenge", "Challenges", `
      <p>A challenge is a few theorems whose statements, with every definition beneath them, go in
      one file for <code>lake comparator</code>, which checks that the project proves exactly those
      statements. Choose its theorems from a theorem's details; <b>Challenge</b> shows the choice,
      what it rests on, and how much of that you've agreed with as it is now.</p>
      <p>Writing it builds the project first if the code has changed, and puts
      <code>Challenge.lean</code> and <code>comparator.json</code> in the project. Running
      <code>lake comparator</code> stays yours.</p>`],
  ];

  let dialog = null;
  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "help";
    dialog.innerHTML = `<nav></nav><div class="help-body"><div class="help-page"></div>
      <div class="help-foot"><button type="button" class="help-back">Back</button>
      <button type="button" class="help-next">Next</button><button type="button" class="help-close">Done</button></div></div>`;
    const nav = dialog.querySelector("nav"), page = dialog.querySelector(".help-page");
    let at = 0;
    const show = i => {
      at = i;
      const [, title, html] = SECTIONS[i];
      page.innerHTML = `<h2>${title}</h2>${html}`;
      for (const [k, b] of [...nav.children].entries()) b.classList.toggle("current", k === i);
      dialog.querySelector(".help-back").disabled = i === 0;
      dialog.querySelector(".help-next").hidden = i === SECTIONS.length - 1;
      dialog.querySelector(".help-close").classList.toggle("primary", i === SECTIONS.length - 1);
    };
    SECTIONS.forEach(([, title], i) => {
      const b = Object.assign(document.createElement("button"), { type: "button", textContent: title });
      b.onclick = () => show(i);
      nav.append(b);
    });
    dialog.querySelector(".help-back").onclick = () => show(Math.max(0, at - 1));
    dialog.querySelector(".help-next").onclick = () => show(Math.min(SECTIONS.length - 1, at + 1));
    dialog.querySelector(".help-close").onclick = () => dialog.close();
    // A click on the backdrop closes it too.
    dialog.addEventListener("click", e => { if (e.target === dialog) dialog.close(); });
    dialog.goTo = show;
    document.body.append(dialog);
  }
  // The styles, for the ? as well as the dialog.
  let styled = false;
  function style() {
    if (styled) return;
    styled = true;
    document.head.append(Object.assign(document.createElement("style"), { textContent: CSS }));
  }

  function open(section = "start") {
    style();
    if (!dialog) build();
    dialog.goTo(Math.max(0, SECTIONS.findIndex(([key]) => key === section)));
    if (!dialog.open) dialog.showModal();
    try { localStorage.setItem(SEEN, "1"); } catch {}
  }

  // The ? in a header, opening at `section`; the first visit opens it once by itself.
  function button(where, section) {
    style();
    const b = Object.assign(document.createElement("button"), { type: "button", className: "help-button", textContent: "?" });
    b.title = "How this works";
    b.setAttribute("aria-label", "How this works");
    b.onclick = () => open(section);
    where.append(b);
    let seen = true;
    try { seen = localStorage.getItem(SEEN) === "1"; } catch {}
    if (!seen) open(section);
  }

  const CSS = `
    dialog.help { border: none; border-radius: 10px; padding: 0; width: min(46em, calc(100vw - 2em));
      max-height: min(36em, calc(100vh - 2em)); box-shadow: 0 12px 40px #0004; color: #222;
      font: 15px/1.5 system-ui, sans-serif; display: none; }
    dialog.help[open] { display: flex; }
    dialog.help::backdrop { background: #0003; }
    dialog.help nav { flex: none; width: 11em; background: #f6f7f9; padding: 1em 0.5em; display: flex;
      flex-direction: column; gap: 0.15em; border-right: 1px solid #e3e5e8; }
    dialog.help nav button { all: unset; cursor: pointer; padding: 0.35em 0.7em; border-radius: 6px; color: #444; }
    dialog.help nav button:hover { background: #eceef1; }
    dialog.help nav button.current { background: #fff; color: #111; font-weight: 600; box-shadow: 0 1px 2px #0001; }
    dialog.help .help-body { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    dialog.help .help-page { flex: 1; overflow: auto; padding: 1.2em 1.6em 0.4em; }
    dialog.help h2 { margin: 0 0 0.6em; font-size: 1.25em; }
    dialog.help p { margin: 0 0 0.8em; }
    dialog.help .help-list { list-style: none; padding: 0; margin: 0 0 0.8em; }
    dialog.help .help-list li { margin: 0.35em 0; }
    dialog.help .help-swatch { display: inline-block; width: 1em; height: 1em; border-radius: 3px;
      vertical-align: -0.15em; margin-right: 0.6em; box-sizing: border-box; }
    dialog.help .help-star { color: #d4a017; margin: 0 0.6em 0 0.1em; }
    dialog.help .help-attention { display: inline-block; width: 1.2em; height: 1.2em; line-height: 1.1em;
      text-align: center; border: 1.5px solid #e07000; border-radius: 50%; color: #e07000; font-weight: 700;
      font-size: 0.85em; }
    dialog.help .help-down { color: #2a7de1; font-weight: 600; }
    dialog.help .help-up { color: #8e44ad; font-weight: 600; }
    dialog.help kbd { font: 0.85em ui-monospace, monospace; border: 1px solid #ccd; border-bottom-width: 2px;
      border-radius: 4px; padding: 0 0.35em; background: #fafbfc; }
    dialog.help .help-foot { display: flex; justify-content: flex-end; gap: 0.5em; padding: 0.8em 1.6em 1em; }
    dialog.help .help-foot button { font: inherit; font-size: 0.9em; padding: 0.3em 0.9em; border-radius: 6px;
      border: 1px solid transparent; background: #eceef1; cursor: pointer; }
    dialog.help .help-foot button:disabled { opacity: 0.4; cursor: default; }
    dialog.help .help-foot button.primary, dialog.help .help-foot button.help-next { background: #1a5fb4; border-color: #1a5fb4; color: #fff; }
    @media (max-width: 40em) { dialog.help nav { display: none; } }
    .help-button { font: 600 0.9em system-ui, sans-serif; width: 1.8em; height: 1.8em; border-radius: 50%; margin: 0;
      border: none; background: #eceef1; color: #555; cursor: pointer; padding: 0; }
    .help-button:hover { background: #e1e4e8; color: #111; }`;

  return { open, button };
})();
