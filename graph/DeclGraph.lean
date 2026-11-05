import Lean

/-!
# Declaration graph for review

Run from inside a Lake project, after `lake build`:

    lake env lean --run path/to/DeclGraph.lean MyProject > graph.json

It imports the module `MyProject`, and for every declaration a reviewer reads in the modules under
it, records which other such declarations it depends on, and two fingerprints:

* `own`: a hash of the declaration itself. A theorem's statement only, since its proof is checked
  by Lean and never reviewed; a definition's statement and body; a structure's or inductive type's
  fields and constructors.
* `deep`: `own` combined with the `deep` fingerprints of everything it depends on. It changes when
  the declaration or anything below it changes.

Compiler-generated helpers (`match_1`, `rec`, …) are not listed; a declaration that uses one
depends on whatever the helper uses. Library constants that are Core Lean plumbing are listed with
a `group` (see `plumbingGroup?`), so a viewer can hide them. Constructors and structure fields count as their type.
Deprecated aliases (`@[deprecated] alias Old := New`) are listed under `renames`.

Projects using the module system work the same way: the private level is loaded, so a definition's
body counts, and a change to it is noticed, whether or not it is exposed to other modules.
-/

open Lean

/-- `prefix` followed by digits only, as in `match_1`: how Lean numbers what it generates. A person
can write `eq_spec` or `proof_irrel`, so a prefix alone isn't enough. -/
def isNumbered (s pre : String) : Bool :=
  let rest := s.drop pre.length
  s.startsWith pre && !rest.isEmpty && rest.all Char.isDigit

/-- One component of a generated name: the helpers of matching and of recursion, which can have
children of their own (`f.match_1.congr_eq_1`, `T.brecOn_1.go`), and what Lean adds per definition. -/
def isGeneratedPart (s : String) : Bool :=
  s == "brecOn" || s == "binductionOn" ||
  ["match_", "proof_", "eq_", "rec_", "brecOn_", "below_", "binductionOn_", "congr_eq_", "hcongr_"].any (isNumbered s)

/-- Where a declaration is in its source file. Lean records this for what a person wrote, and
doc-gen4 documents only what has one. -/
def sourceRange? (env : Environment) (n : Name) :=
  declRangeExt.find? (level := .exported) env n <|> declRangeExt.find? (level := .server) env n

/-- A structure's projection to a parent it extends. A person wrote `extends`, so it has a source
range, but doc-gen4 shows it with the structure, not as a declaration of its own. -/
def isParentProjection (env : Environment) (n : Name) : Bool :=
  match n with
  | .str parent _ => (getStructureInfo? env parent).any (·.parentInfo.any (·.projFn == n))
  | _ => false

/-- Compiler-generated or otherwise unreviewed names: looked through, never listed. These follow
doc-gen4's rule (it has a source range, and isn't a recursor or matcher) so that the graph and the
docs agree, plus Lean's generated lemmas, which do have ranges. Names alone can't decide: `Sym2.rec`
and `Bool.ofNat` are written by people, and each has the name of something Lean generates. -/
def isDetail (env : Environment) (n : Name) : Bool :=
  n.isInternalDetail || isAuxRecursor env n || isNoConfusion env n ||
  (sourceRange? env n).isNone ||
  (env.find? n matches some (.recInfo _)) || isParentProjection env n ||
  n.components.any (fun c => match c with | .str _ s => isGeneratedPart s | _ => false) ||
  match n with
  | .str parent s =>
    (s == "inj" || s == "injEq" || s == "sizeOf_spec") && (env.find? parent matches some (.ctorInfo _)) ||
    s == "ctorIdx" || s == "toCtorIdx" || s == "ofNat_ctorIdx" || s == "ctorElim" ||
    s == "ctorElimType" || s == "noConfusionType" ||
    -- Generated for definitions by recursion: induction principles and unfolding equations. Only on
    -- a declaration: `Polynomial.Chebyshev.induct` is a person's theorem in a namespace.
    (env.contains parent && (s == "induct" || s == "induct_unfolding" || s == "fun_cases" ||
      s == "fun_cases_unfolding" || s == "eq_def" || s == "eq_unfold" || s == "congr_simp")) ||
    s.startsWith "_"
  | _ => false

/-- What each constant's proof rests on beyond Lean's three standard axioms: other axioms, and the
`sorry`s it depends on, named by where they were written. It looks through proofs, unlike
everything else here, since that's where a `sorry` usually is. It keeps its own stack, since chains
of proofs are too deep to recurse along. -/
def collectGaps (env : Environment) (roots : Array Name) : Std.HashMap Name NameSet := Id.run do
  let standard : NameSet := NameSet.empty |>.insert ``propext |>.insert ``Classical.choice |>.insert ``Quot.sound
  let mut done : Std.HashMap Name NameSet := {}
  for root in roots do
    if done.contains root then continue
    -- Each frame: a constant, what it uses, how far along that we are, and what's been found.
    let mut stack : Array (Name × Array Name × Nat × NameSet) := #[]
    let uses (n : Name) : Array Name :=
      match env.find? n with
      | some info => info.getUsedConstantsAsSet.toArray
      | none => #[]
    stack := stack.push (root, uses root, 0, {})
    let mut seen : NameSet := NameSet.empty.insert root
    while !stack.isEmpty do
      let (n, used, i, found) := stack.back!
      stack := stack.pop
      if h : i < used.size then
        let next := used[i]
        if let some gaps := done.get? next then
          stack := stack.push (n, used, i + 1, if gaps.isEmpty then found else found ++ gaps)
        else if seen.contains next then
          stack := stack.push (n, used, i + 1, found)         -- an inductive and its constructors
        else
          seen := seen.insert next
          stack := stack.push (n, used, i, found)             -- back here once `next` is done
          stack := stack.push (next, uses next, 0, {})
      else
        let mut gaps := found
        if (env.find? n matches some (.axiomInfo _)) && !standard.contains n then
          gaps := gaps.insert n
        -- A `sorry` belongs to the first declaration out of the generated helpers around it.
        if gaps.contains ``sorryAx && n != ``sorryAx && !isDetail env n then
          gaps := (gaps.erase ``sorryAx).insert n
        done := done.insert n gaps
  return done

/-- The next steps from `n` toward a `sorry`: the declarations `n` uses, proofs included, that have
one or rely on one, looking through generated helpers. Following these gets to the source. -/
def sorryHops (env : Environment) (gaps : Std.HashMap Name NameSet) (n : Name) : NameSet := Id.run do
  let uses (c : Name) : Array Name := (env.find? c).map (·.getUsedConstantsAsSet.toArray) |>.getD #[]
  let isAxiom (c : Name) := env.find? c matches some (.axiomInfo _)
  let leadsToSorry (c : Name) := !isAxiom c && ((gaps.get? c).getD {}).toArray.any (fun g => !isAxiom g || g == ``sorryAx)
  let mut hops : NameSet := {}
  let mut seen : NameSet := {}
  let mut todo := uses n
  while !todo.isEmpty do
    let c := todo.back!
    todo := todo.pop
    if seen.contains c || !leadsToSorry c then continue
    seen := seen.insert c
    if isDetail env c then todo := todo ++ uses c else hops := hops.insert c
  return hops

structure Graph where
  /-- Modules whose declarations are reviewed. -/
  inScope : Name → Bool
  env : Environment
  /-- `env.header.moduleNames`, computed once: in Lean 4.35 it rebuilds the array on every call. -/
  moduleNames : Array Name := env.header.moduleNames

namespace Graph

/-- The declaration a reviewer sees for `n`: its type, for a constructor or structure field. -/
def owner (g : Graph) (n : Name) : Name :=
  match g.env.find? n with
  | some (.ctorInfo c) => c.induct
  | _ =>
    match g.env.getProjectionFnInfo? n with
    | some p => match g.env.find? p.ctorName with
      | some (.ctorInfo c) => c.induct
      | _ => n
    | none => n

def inProject (g : Graph) (n : Name) : Bool :=
  match g.env.getModuleIdxFor? n with
  | some i => g.inScope g.moduleNames[i.toNat]!
  | none => false

/-- The expressions that are reviewed for a declaration. -/
def reviewedExprs (g : Graph) (n : Name) : Array Expr :=
  match g.env.find? n with
  | some (.thmInfo t) => #[t.type]
  | some (.defnInfo d) => #[d.type, d.value]
  | some (.opaqueInfo o) => #[o.type, o.value]
  | some (.inductInfo i) =>
    #[i.type] ++ i.ctors.toArray.filterMap fun c => (g.env.find? c).map (·.type)
  | some info => #[info.type]
  | none => #[]

/-- Whether `c` is a method of a type class, such as `LE.le` behind `≤`. -/
def isClassMethod (g : Graph) (c : Name) : Bool :=
  match g.env.getProjectionFnInfo? c with
  | some p => p.fromClass
  | none => false

/-! ## Core Lean plumbing

Library constants that appear in compiled definitions but are usually not part of what they mean to
a reader. They are kept in the graph, tagged with a group, so the pages can show or hide each
group. -/

/-- How Lean compiles recursion, pattern matching and `do` notation, and how `deriving` prints. -/
def compilerPlumbing : List Name :=
  [`PProd, `PProd.mk, `PProd.fst, `PProd.snd, `PUnit, `PUnit.unit, `Unit, `Unit.unit,
   `Function.comp, `Function.const, `id, `inferInstance, `inferInstanceAs,
   `outParam, `optParam, `autoParam, `namedPattern, `HEq, `cast, `Eq.mpr, `Eq.mp,
   `Eq.ndrec, `ForInStep, `List.forIn', `Repr.addAppParen,
   `Nat.hasNotBit, `Nat.land, `Nat.shiftRight,
   -- Well-founded recursion, as `gcd` is compiled.
   `InvImage, `invImage, `PSigma, `PSigma.mk, `measure, `sizeOf, `SizeOf]

/-- What `if`, `match`, `do`, `throw` and `try` mean. These are notation a reader already reads
as such; the conditions and steps inside them are listed separately. -/
def notationPlumbing : List Name :=
  [`ite, `dite, `Decidable, `Decidable.decide, `Decidable.isTrue, `Decidable.isFalse,
   `Except.bind, `Except.pure, `Except.map, `Except.tryCatch, `throwThe, `tryCatchThe,
   `Bool.decEq, `Nat.decEq, `String.decEq]

/-- Namespaces that are internal machinery: text formatting for `Repr`, and string slices behind
`String.startsWith`. -/
def plumbingNamespaces : List Name :=
  [`Std.Format, `String.Slice, `WellFounded]

/-- The plumbing group of a library constant, if any: `compiler`, `notation`, `internals`, or
`theorems` for library theorems, which appear inside definitions only as proof plumbing. -/
def plumbingGroup? (env : Environment) (n : Name) : Option String :=
  if compilerPlumbing.contains n then some "compiler"
  else if notationPlumbing.contains n then some "notation"
  else if plumbingNamespaces.any (·.isPrefixOf n) then some "internals"
  else if env.find? n matches some (.thmInfo _) then some "theorems"
  else none

/-- Logic and basic types that an instance's definition is phrased in, not what it provides. -/
def basicLogic : List Name :=
  [`Eq, `Bool, `True, `False, `And, `Or, `Not, `Iff, `Exists, `HEq]

/-- What a library constant stands for, for a reader. A class method (`LE.le`) stands for
nothing by itself: its instance says what it means. An instance (`instLEFloat`) stands for the
operations it provides (`Float.le`). A constructor or field stands for its type. Generated helpers
stand for nothing. Anything else stands for itself. -/
def libraryMeaning (g : Graph) (c : Name) : Array Name :=
  -- A type class (`Mod`, behind `%`) stands for nothing by itself either: its instance says what
  -- `%` means (`Nat.mod`).
  if g.isClassMethod c || isDetail g.env c || isClass g.env c then #[]
  else if Meta.isInstanceCore g.env c then
    match g.env.find? c with
    | some (.defnInfo d) =>
      d.value.getUsedConstants.filter fun d =>
        !g.inProject d && !isDetail g.env d && !g.isClassMethod d &&
        !Meta.isInstanceCore g.env d && g.owner d == d &&
        -- Definitions, and inductive types such as `Nat.le`, which is what `≤` means on `Nat`,
        -- but not the basic logic an instance is phrased in (`Float.le a b = true`).
        !basicLogic.contains d && !isClass g.env d &&
        (g.env.find? d matches some (.defnInfo _) | some (.opaqueInfo _) | some (.thmInfo _) |
          some (.inductInfo _))
    | _ => #[]
  else
    -- A class's constructor (`Mod.mk`) also stands for nothing.
    let o := g.owner c
    if isClass g.env o then #[] else #[o]

/-- Reviewed declarations and library constants used by `n`, looking through helpers; also the
hashes of the helpers looked through, which are part of `n`'s own content. -/
partial def collect (g : Graph) (self : Name) (exprs : Array Expr)
    (seen : NameSet := {}) : NameSet × NameSet × Array UInt64 × NameSet := Id.run do
  let mut deps : NameSet := {}
  let mut library : NameSet := {}
  let mut hashes : Array UInt64 := #[]
  let mut seen := seen
  for e in exprs do
    for c in e.getUsedConstants do
      if seen.contains c then continue
      seen := seen.insert c
      if !g.inProject c then
        for m in g.libraryMeaning c do library := library.insert m
        continue
      let o := g.owner c
      if o != c || !isDetail g.env c then
        if o != self then deps := deps.insert o
      else
        let inner := g.reviewedExprs c
        hashes := hashes ++ inner.map (·.hash)
        let (d, l, h, s) := g.collect self inner seen
        deps := deps.union d
        library := library.union l
        hashes := hashes ++ h
        seen := s
  return (deps, library, hashes, seen)

end Graph

def hex (h : UInt64) : String :=
  let s := String.ofList (Nat.toDigits 16 h.toNat)
  "".pushn '0' (16 - s.length) ++ s

/-- `unsafe` only because loading the imported modules' extensions (which record instances and
deprecations) requires running their initializers. -/
unsafe def main (args : List String) : IO UInt32 := do
  let root :: extra := args | do
    IO.eprintln "usage: lake env lean --run DeclGraph.lean <RootModule> [<Module>...]"
    return 1
  let rootName := root.toName
  initSearchPath (← findSysroot)
  enableInitializersExecution
  -- Extra modules are imported too, for a project whose root module doesn't import everything
  -- (a separate examples library); the root module's name still sets what is in the project.
  let env ← importModules ((root :: extra).toArray.map fun m => { module := m.toName }) {} (loadExts := true) (level := .private)
  let g : Graph := { env, inScope := fun m => rootName.isPrefixOf m || m == rootName }
  -- Reviewed declarations: in the project, not helpers, not constructors or fields.
  let names := env.constants.fold (init := #[]) fun acc n _ =>
    if g.inProject n && !isDetail env n && g.owner n == n &&
       (Linter.getDeprecatedNewName env n).isNone then acc.push n else acc
  let mut own : Std.HashMap Name UInt64 := {}
  let mut deps : Std.HashMap Name NameSet := {}
  let mut uses : Std.HashMap Name NameSet := {}
  let mut library : NameSet := {}
  for n in names do
    let exprs := g.reviewedExprs n
    let (d, l, inner, _) := g.collect n exprs
    own := own.insert n (mixHash (hash n) ((exprs.map (·.hash) ++ inner).foldl mixHash 7))
    deps := deps.insert n d
    uses := uses.insert n l
    library := library.union l
  -- A library constant's own fingerprint: its statement and, for a definition, its body.
  let libraryOwn (n : Name) : UInt64 :=
    mixHash (hash n) ((g.reviewedExprs n).map (·.hash) |>.foldl mixHash 7)
  -- `deep` fingerprints, depth first over the dependency graph (it has no cycles).
  let mut deep : Std.HashMap Name UInt64 := {}
  let rec visit (n : Name) (deep : Std.HashMap Name UInt64) (fuel : Nat) :
      Std.HashMap Name UInt64 :=
    match fuel with
    | 0 => deep
    | fuel + 1 =>
      if deep.contains n then deep else
      let ds := ((deps.get? n).getD {}).toArray.qsort Name.lt
      let deep := ds.foldl (fun acc d => visit d acc fuel) deep
      let ls := ((uses.get? n).getD {}).toArray.qsort Name.lt
      let h := ds.foldl (fun acc d => mixHash acc ((deep.get? d).getD 0)) ((own.get? n).getD 0)
      let h := ls.foldl (fun acc l => mixHash acc (libraryOwn l)) h
      deep.insert n h
  for n in names do
    deep := visit n deep (names.size + 1)
  let kind (n : Name) : String :=
    match env.find? n with
    | some (.thmInfo _) => "theorem"
    | some (.inductInfo _) => if isStructure env n then "structure" else "inductive"
    | some (.axiomInfo _) => "axiom"
    | some (.opaqueInfo _) => "opaque"
    | _ => if Meta.isInstanceCore env n then "instance" else "def"
  let module (n : Name) : String :=
    match env.getModuleIdxFor? n with
    | some i => g.moduleNames[i.toNat]!.toString
    | none => ""
  -- Source lines, for showing a declaration's code: Lean records them with each declaration.
  let lines (n : Name) : Json :=
    match sourceRange? env n with
    | some r => toJson #[r.range.pos.line, r.range.endPos.line]
    | none => Json.null
  let gaps := collectGaps env names
  -- Written only where there's something to say, so a clean project's graph is unchanged.
  let gapFields (n : Name) : List (String × Json) :=
    let found := ((gaps.get? n).getD {}).toArray.qsort Name.lt
    let isAxiom (a : Name) := env.find? a matches some (.axiomInfo _)
    let sources := found.filter fun a => !isAxiom a && a != n
    let axioms := found.filter isAxiom
    (if found.contains n then [("sorry", toJson true)] else []) ++
    (if sources.isEmpty then [] else [("sorryFrom", toJson (sources.map (·.toString)))]) ++
    (if sources.isEmpty then [] else
      [("sorryVia", toJson ((sorryHops env gaps n).toArray.qsort Name.lt |>.map (fun h => (g.owner h).toString)))]) ++
    -- `native_decide` adds an axiom per use, or `Lean.ofReduceBool` in older Lean: the same trust.
    let shown (a : Name) : String :=
      if a == `Lean.ofReduceBool || a.components.contains `_native then "native_decide" else a.toString
    (if axioms.isEmpty then [] else [("axioms", toJson (axioms.map shown).toList.eraseDups.toArray)])
  let decls := (names.qsort Name.lt).map fun n =>
    (n.toString, Json.mkObj <| List.append (gapFields n) <| [
      ("kind", kind n),
      ("module", module n),
      ("lines", lines n),
      ("own", hex ((own.get? n).getD 0)),
      ("deep", hex ((deep.get? n).getD 0)),
      ("deps", toJson (((deps.get? n).getD {}).toArray.qsort Name.lt |>.map (·.toString))),
      ("uses", toJson (((uses.get? n).getD {}).toArray.qsort Name.lt |>.map (·.toString)))])
  let libraryDecls := (library.toArray.qsort Name.lt).map fun n =>
    (n.toString, Json.mkObj [
      ("kind", kind n),
      ("module", module n),
      ("own", hex (libraryOwn n)),
      ("group", match Graph.plumbingGroup? env n with
        | some grp => Json.str grp
        | none => Json.null)])
  let renames := env.constants.fold (init := #[]) fun acc n _ =>
    if g.inProject n then
      match Linter.getDeprecatedNewName env n with
      | some new => acc.push (n.toString, Json.str new.toString)
      | none => acc
    else acc
  -- The project's modules and which project modules each imports directly, for the module diagram.
  let modules := (g.moduleNames.zip env.header.moduleData).filterMap fun (m, data) =>
    if g.inScope m then
      some (m.toString, toJson ((data.imports.filterMap fun i =>
        if g.inScope i.module then some i.module.toString else none).qsort (· < ·)))
    else none
  IO.println (Json.mkObj [
    ("format", (1 : Nat)),  -- GRAPH_FORMAT in server/server.py
    ("lean", Lean.versionString),
    ("root", root),
    ("modules", Json.mkObj (modules.qsort (·.1 < ·.1)).toList),
    ("declarations", Json.mkObj decls.toList),
    ("library", Json.mkObj libraryDecls.toList),
    ("renames", Json.mkObj renames.toList)]).pretty
  return 0
