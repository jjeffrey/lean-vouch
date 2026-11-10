# Features

For every declaration, in the diagram's sidebar, and under it on its doc page if you build them
(`--docs`):

- **A mark:** ✓ agree, ~ looks right at a glance but not checked closely, ? question, ✗ reject how
  it's defined. Click it again to clear it. Only ✓ counts as agreeing in a [challenge](challenge.md).
- **A note thread,** for questions and answers, from everyone reviewing.
- **Change tracking:** the mark is flagged when the declaration has changed since it was made, or
  when anything below it has, however far, in libraries too ("depends on `Divides`, which changed since
  you marked it").
  For a theorem only its statement counts, since Lean checks the proof; for a definition, its
  statement and body; for a type, its fields and constructors, names and order included. A change
  only to how an argument is passed, such as `(n : Nat)` to `{n : Nat}`, or to an argument's name,
  isn't flagged: it changes how the declaration is called, not what it says.
- **Accept change** keeps a flagged mark for the current version. It covers that one mark: accepting a
  change below doesn't accept what's built on it.
- **The library definitions it uses** ("Uses from libraries: `Nat`, `Nat.mod`").
- **What it says.** The sidebar shows its docstring, its statement as Lean understood it, with every
  argument, including those a `variable` adds that its source text doesn't show, and its code as
  written, colored, with names linked to their own entries and a theorem's proof folded. With doc
  pages, the sidebar shows doc-gen4's rendering instead.
- **Its equations, open,** on doc pages. doc-gen4 folds a definition's equations; for review they are
  what you check, so project declarations show them open. "open equations" at the bottom of the page
  switches this off (remembered per browser). Library pages keep doc-gen4's folding.

Across the project:

- **What changed,** on the pages and in the diagram: every change, lowest first, with how many marks
  rely on it. In the diagram a solid orange border is what changed and a dashed one what relies on it,
  and selecting a flagged declaration lights the way down to the change.
- **Library definitions can be marked too,** meaning "I understand this", so you record what `%` on
  `Nat` means to you once, for every use: in the diagram, with "show library definitions" on, or on
  their own doc pages.
- **A challenge for comparator:** choose the theorems that matter, and lean-vouch writes their
  statements and everything beneath them as a challenge for `lake comparator`, checked to say what
  the project says. See [challenges](challenge.md).
- **Refactors:** renames recorded as deprecated aliases (`@[deprecated] alias Old := New`) move
  marks automatically. Marks whose declaration isn't in the last build are listed as orphaned notes, to
  move or delete; none disappear silently.
- **A dependency diagram,** on its own page: a Hasse diagram of the project's declarations (and
  optionally the library definitions they use), colored by your marks. Clicking a node highlights
  what it depends on and what depends on it, and opens a sidebar with what it says, its mark and
  notes, and its direct dependencies and dependents. "hide folders" and "hide details" hide the
  panels on either side, for a larger diagram, and dragging the line beside one resizes it.
- **What a `sorry` reaches,** on by default if the project has one. A declaration that contains
  `sorry` gets a red border, and whatever relies on it a dashed one, even if only through a proof,
  with dashed red arrows leading to it step by step. Folders count both, and the pages name the `sorry`s each declaration depends on. A mark is about the
  statement, so it stands: a theorem can be agreed and still unproved. `native_decide` and any other
  axiom beyond Lean's standard three are listed too. Filling in a proof changes no fingerprint and no
  mark.
- **Folders,** in the same diagram. A project of more than 60 declarations opens as boxes, one per
  folder or module, each saying how much of it you have marked; click one to open it, and click an
  open box's label to close it. A thicker arrow between boxes stands for more dependencies, and by
  default arrows are drawn only between neighbors in one folder. All of Mathlib opens this way: 32
  boxes, with a module's declarations fetched when you open it.
- **The list of folders and modules,** beside the diagram. Click one to open or close it, search by
  name, and Cmd- or Ctrl-click one here or its box in the diagram to pick it, opening it, however
  distant: the diagram then shows only what's picked and the arrows between them.
- **Shift-click** a declaration, a box or a row in the list to color everything it depends on and
  everything that uses it, all the way down and up, and list how much of each is in each folder.

With others ([how to share marks](setup.md#working-with-others)), and with [assistants](setup.md#assistants):

- **Their marks beside yours,** and their notes in the same thread, each person in their own color.
  The diagram shows your marks, or anyone's, or everyone's, with disagreements in purple.
- **Needs your attention:** a `!` where someone is waiting on you, because they asked a question or
  answered yours. Listed at the bottom of the page, and counted in the diagram's folders. Reply,
  mark it, or dismiss it until they write again.
- **Live updates:** marks and notes appear on open pages by themselves. Pages say when the code has
  changed since the last build, with a button to [rebuild](setup.md#rebuilding), show how far a
  rebuild has got, and offer to reload when it's done.
- **Doc pages,** with `--docs`: every module's doc-gen4 page, with the review controls under each
  declaration, to read a module from top to bottom.

## Screenshots

From the [example](development.md#example), with its example marks.

**The dependency diagram:** `gcd` selected, with what depends on it highlighted, modules boxed,
and the sidebar showing its docstring, statement, review and code.

![the dependency diagram with gcd selected](images/diagram.png)

In the diagram, solid red borders mark a `sorry`, dashed ones what relies on it, and dashed red arrows
how. Shift-click one to color all it reaches.

![the diagram with a sorry](images/sorry-diagram.png)

The rest are doc pages, from a run with `--docs`.

**A declaration under review:** its mark, the library definitions it uses, and a note thread.

![gcd with a question and a reply](images/review-page.png)

**Change tracking:** a mark moved automatically by a rename (`gcd_dvd` → `gcd_divides`), and a
mark flagged because something below it changed.

![a moved mark and a mark to re-check](images/changes.png)

**A library definition marked as understood,** with the project declarations that use it.

![Nat.mod marked as understood](images/library-mark.png)

**A `sorry` and what relies on it:** `gcd_mul_lcm` isn't proved, so `gcd_divides_mul` holds only if it
is, which a reviewer has asked about. Its statement never mentions `gcd_mul_lcm`; the proof does.

![a sorry and a theorem that relies on it](images/sorry.png)

**Orphaned notes,** left behind when a declaration is removed, or when a run leaves out its library.

![the orphaned notes list](images/orphans.png)

**Dark theme:** the controls follow doc-gen4's light, dark and system themes.

![a declaration under review in the dark theme](images/review-page-dark.png)
