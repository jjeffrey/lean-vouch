import Lean

/-!
# Declaration graph for review

Run from inside a Lake project, after `lake build`:

    lake env lean --run path/to/DeclGraph.lean MyProject [MyProjectTests ...] > graph.json

It imports the modules named, and for every declaration a reviewer reads in the modules under
them, records which other such declarations it depends on, and a fingerprint, `own`: a hash of the
declaration itself. A theorem's statement only, since its proof is checked by Lean and never
reviewed; a definition's statement and body; a structure's or inductive type's fields and
constructors.

Compiler-generated helpers (`match_1`, `rec`, …) are not listed; a declaration that uses one
depends on whatever the helper uses, and the helper counts as part of it. So does a deprecated
declaration, which isn't reviewed either. Library constants that are Core Lean plumbing are listed with
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

/-- Whether a module above `n` is one of `names`. -/
def isUnder (names : NameSet) : Name → Bool
  | .str parent _ | .num parent _ => names.contains parent || isUnder names parent
  | .anonymous => false

/-- Where a declaration is in its source file. Lean records this for what a person wrote, and
doc-gen4 documents only what has one. -/
def sourceRange? (env : Environment) (n : Name) :=
  declRangeExt.find? (level := .exported) env n <|> declRangeExt.find? (level := .server) env n

/-- Classes whose `deriving` output there's nothing to review in. -/
def derivedClasses : List String := ["Repr", "DecidableEq", "BEq", "Hashable", "Inhabited", "Nonempty"]

/-- Whether `deriving` wrote this, rather than a person. Names can't say: `instance : BEq Foo` gets
the same one. But a written declaration's range covers all of it and its selection only part, while
what `deriving` makes has both at the class's name in the clause. So does what a macro makes. -/
def isDerived (env : Environment) (n : Name) : Bool :=
  let generated := match sourceRange? env n with
    | some r => r.range.pos == r.selectionRange.pos && r.range.endPos == r.selectionRange.endPos
    | none => false
  generated && n.components.any fun part => match part with
    | .str _ s => derivedClasses.any fun c => ("inst" ++ c).isPrefixOf s
    | _ => false

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

/-- How far along the graph is, for lean-vouch to show: `phase`, and `done` of `total`, about two hundred
times a phase at most, when `LEAN_VOUCH_PROGRESS` asks for it. On stderr, since the graph goes to a
file and a project's own output to stdout. -/
def progress (phase : String) (done total : Nat) : IO Unit := do
  if (← IO.getEnv "LEAN_VOUCH_PROGRESS").isNone then return
  if total == 0 || done == total || done % (max 1 (total / 200)) == 0 then
    IO.eprintln s!"lean-vouch-progress {phase} {done} {total}"

/-- What each constant's proof rests on beyond Lean's three standard axioms: other axioms, and the
`sorry`s it depends on, named by where they were written. It looks through proofs, unlike
everything else here, since that's where a `sorry` usually is. It keeps its own stack, since chains
of proofs are too deep to recurse along. -/
def collectGaps (env : Environment) (roots : Array Name) : IO (Std.HashMap Name NameSet) := do
  let standard : NameSet := NameSet.empty |>.insert ``propext |>.insert ``Classical.choice |>.insert ``Quot.sound
  let mut done : Std.HashMap Name NameSet := {}
  let mut count := 0
  for root in roots do
    count := count + 1
    progress "proofs" count roots.size
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

/-- What runs in place of an opaque declaration's value, which is only a placeholder: a `partial
def`'s body, or what `@[implemented_by]` gives. A recursive definition has its own value to review,
and Lean's compiled copy of it mustn't count, or its fingerprint would move for no reason. -/
def implementation (g : Graph) (n : Name) : Array Expr :=
  (#[Compiler.mkUnsafeRecName n] ++ (Compiler.getImplementedBy? g.env n).toArray).filterMap fun c =>
    match g.env.find? c with
    | some (.defnInfo d) => some d.value
    | some (.opaqueInfo o) => some o.value
    | _ => none

/-- A structure's default field values, which its constructor's type doesn't mention. -/
def fieldDefaults (g : Graph) (n : Name) : Array Expr :=
  if !isStructure g.env n then #[] else
    (getStructureFieldsFlattened g.env n (includeSubobjectFields := false)).filterMap fun field => do
      match g.env.find? (← getDefaultFnForField? g.env n field) with
      | some (.defnInfo d) => some d.value
      | _ => none

/-- A type's constructors and fields by name, in order. Lean's expression hash ignores binder names,
so swapping two fields of one type would otherwise change nothing, though it changes what every
`⟨a, b⟩` means. As string literals, since a name here isn't a dependency. -/
def memberNames (g : Graph) (i : InductiveVal) : Array Expr :=
  let fields := if isStructure g.env i.name
    then getStructureFieldsFlattened g.env i.name (includeSubobjectFields := false) else #[]
  (i.ctors.toArray ++ fields).map (mkStrLit ·.toString)

/-- The expressions that are reviewed for a declaration. -/
def reviewedExprs (g : Graph) (n : Name) : Array Expr :=
  match g.env.find? n with
  | some (.thmInfo t) => #[t.type]
  | some (.defnInfo d) => #[d.type, d.value]
  | some (.opaqueInfo o) => #[o.type, o.value] ++ g.implementation n
  | some (.inductInfo i) =>
    #[i.type] ++ (i.ctors.toArray.filterMap fun c => (g.env.find? c).map (·.type)) ++ g.fieldDefaults n ++ g.memberNames i
  | some info => #[info.type]
  | none => #[]

/-- Which arguments of a call say where it was written: the module, declaration, line and column of
a `panic!`, `unreachable!` or `assert!`, and the label of a `sorry`, which holds its position. -/
def placeArgs (c : Name) (args : Array Expr) : Array Nat :=
  if c == ``panicWithPosWithDecl && args.size ≥ 6 then #[2, 3, 4, 5]
  else if c == ``panicWithPos && args.size ≥ 5 then #[2, 3, 4]
  else if c == ``sorryAx && args.size ≥ 3 && (args[0]!).isForall &&
      (args[0]!).bindingDomain! == mkConst ``Lean.Name then #[2]
  else #[]

/-- `e` without what says where it was written, so that moving code changes nothing. -/
partial def withoutPlace (e : Expr) : Expr :=
  e.replace fun x =>
    if !x.isApp then none else
    match x.getAppFn with
    | fn@(.const c _) =>
      let args := x.getAppArgs
      let place := placeArgs c args
      if place.isEmpty then none else
        some (mkAppN fn (args.mapIdx fun i a => if place.contains i then mkStrLit "" else withoutPlace a))
    | _ => none

/-- An expression's hash, without where it was written or the names of the project's helpers. Lean
shares a `match` helper between declarations and names it after the first, so that name can change
while nothing here does. What a helper contains counts anyway, through `collect`. Each is numbered
by first use, so which helper is used where still counts. -/
def contentHash (g : Graph) (e : Expr) : UInt64 :=
  let used := e.getUsedConstants
  let placed := used.any fun c => c == ``panicWithPosWithDecl || c == ``panicWithPos || c == ``sorryAx
  let helpers := used.filter fun c => g.inProject c && isDetail g.env c
  if helpers.isEmpty && !placed then e.hash else
    let number : Std.HashMap Name Nat := helpers.foldl (fun found c => found.insert c found.size) {}
    let e := if placed then withoutPlace e else e
    (e.replace fun
      | .const c levels => (number.get? c).map fun i => .const (.num `_helper i) levels
      | _ => none).hash

/-- What a library constant's meaning rests on: the constants its reviewed expressions mention, so
a theorem's statement and a definition's body, but nothing of a `partial def`'s compiled code, whose
recursion isn't a dependency. Theorems cited inside a body are left out: they're proofs, and which
proof it uses can't change what it means. -/
def libraryBelow (g : Graph) (n : Name) : NameSet :=
  let exprs := match g.env.find? n with
    | some (.opaqueInfo o) => #[o.type, o.value]
    | _ => g.reviewedExprs n
  exprs.foldl (init := {}) fun found e =>
    e.getUsedConstants.foldl (init := found) fun found c =>
      if g.env.find? c matches some (.thmInfo _) then found else found.insert c

/-- Fingerprints of library constants that take in everything reachable from them, so that a change
to something a used definition is built on is seen. Each is a sum over the set of what's reachable,
which depends on that set alone: not on the order of the walk, or on how many paths lead to a
constant, which vary between Lean versions. `flat` is a constant's fingerprint by itself. -/
def libraryDepths (g : Graph) (roots : Array Name) (flat : Name → UInt64) : IO (Std.HashMap Name UInt64) := do
  let mut done : Std.HashMap Name UInt64 := {}
  let mut known : Std.HashMap Name (UInt64 × NameSet) := {}
  let mut count := 0
  for root in roots do
    count := count + 1
    progress "library" count roots.size
    let mut seen : NameSet := {}
    let mut todo := #[root]
    let mut sum : UInt64 := 0
    while !todo.isEmpty do
      let n := todo.back!
      todo := todo.pop
      if seen.contains n then continue
      seen := seen.insert n
      let (own, below) ← match known.get? n with
        | some entry => pure entry
        | none =>
          let entry := (flat n, g.libraryBelow n)
          known := known.insert n entry
          pure entry
      sum := sum + own
      todo := below.foldl Array.push todo
    done := done.insert root sum
  return done

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

/-- Reviewed declarations and library constants used by `n`, looking through helpers and deprecated
declarations; also the hashes of those looked through, which are part of `n`'s own content. -/
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
      -- A deprecated declaration isn't reviewed, so nothing would notice it change: it's looked
      -- through like a helper.
      let retired := (Linter.getDeprecatedNewName g.env o).isSome
      if !retired && (o != c || !isDetail g.env c) then
        if o != self then deps := deps.insert o
      else
        let inner := g.reviewedExprs (if retired then o else c)
        hashes := hashes ++ inner.map g.contentHash
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

/-- A declaration's signature as Lean prints it: every argument, including those that came from a
`variable` and so aren't in its source text, and its type. Empty if it can't be printed. -/
def signature (env : Environment) (n : Name) : IO String := do
  -- From inside its own namespace, as its source is written, so neighbours' names print short.
  let context : Core.Context := { fileName := "", fileMap := default, maxHeartbeats := 0, currNamespace := n.getPrefix }
  try
    let (sig, _) ← (Meta.MetaM.run' (PrettyPrinter.ppSignature n)).toIO context { env }
    return toString sig.fmt
  catch _ => return ""

/-- A structure's fields, or an inductive type's constructors, as doc-gen4 lists them: each with its
type as Lean prints it, and its docstring. A field's type mentions earlier fields by name. A parent a
structure extends is one entry, `toParent`, marked `parent`, rather than each of its fields. -/
def members (env : Environment) (n : Name) : IO (Array Json) := do
  let some (.inductInfo i) := env.find? n | return #[]
  let context : Core.Context := { fileName := "", fileMap := default, maxHeartbeats := 0, currNamespace := n }
  let entry (name : Name) (type : Expr) (doc : Option String) (extra : List (String × Json) := []) : MetaM Json := do
    let shown := toString (← PrettyPrinter.ppExpr type)
    return Json.mkObj <| [("name", Json.str name.toString), ("type", Json.str shown)] ++
      (doc.map fun d => [("doc", Json.str d)]).getD [] ++ extra
  let run : MetaM (Array Json) := do
    if isStructure env n then
      let some ctor := i.ctors.head? | return #[]
      let some (.ctorInfo c) := env.find? ctor | return #[]
      Meta.forallTelescope c.type fun xs _ => do
        let mut out := #[]
        for x in xs[i.numParams:] do
          let field := (← x.fvarId!.getDecl).userName
          let type ← Meta.inferType x
          let parent := (isSubobjectField? env n field).isSome
          out := out.push (← entry field type (← findDocString? env (n ++ field))
            (if parent then [("parent", Json.bool true)] else []))
        return out
    else
      i.ctors.toArray.mapM fun ctor => do
        let some (.ctorInfo c) := env.find? ctor | return Json.null
        Meta.forallBoundedTelescope c.type i.numParams fun _ type => do
          entry (ctor.replacePrefix n .anonymous) type (← findDocString? env ctor)
  try
    let (out, _) ← (Meta.MetaM.run' run).toIO context { env }
    return out.filter (· != Json.null)
  catch _ => return #[]

/-- `unsafe` only because loading the imported modules' extensions (which record instances and
deprecations) requires running their initializers. -/
unsafe def main (args : List String) : IO UInt32 := do
  let root :: extra := args | do
    IO.eprintln "usage: lake env lean --run DeclGraph.lean <RootModule> [<Module>...]"
    return 1
  let roots := (root :: extra).map String.toName
  initSearchPath (← findSysroot)
  enableInitializersExecution
  progress "loading" 0 0
  -- More than one module, for a project whose root module doesn't import everything, or whose
  -- libraries don't share a prefix. Each is reviewed, with the modules under it.
  let env ← importModules ((root :: extra).toArray.map fun m => { module := m.toName }) {} (loadExts := true) (level := .private)
  -- Only the outermost, since this is asked for every constant, and a project can name every module.
  let named := roots.foldl NameSet.insert {}
  let outermost := roots.filter (!isUnder named ·)
  let g : Graph := { env, inScope := fun m => outermost.any (·.isPrefixOf m) }
  -- Reviewed declarations: in the project, not helpers, not constructors or fields.
  let names := env.constants.fold (init := #[]) fun acc n _ =>
    if g.inProject n && !isDetail env n && g.owner n == n &&
       (Linter.getDeprecatedNewName env n).isNone then acc.push n else acc
  let mut own : Std.HashMap Name UInt64 := {}
  let mut deps : Std.HashMap Name NameSet := {}
  let mut uses : Std.HashMap Name NameSet := {}
  let mut library : NameSet := {}
  let mut count := 0
  for n in names do
    count := count + 1
    progress "fingerprinting" count names.size
    let exprs := g.reviewedExprs n
    let (d, l, inner, _) := g.collect n exprs
    own := own.insert n (mixHash (hash n) ((exprs.map g.contentHash ++ inner).foldl mixHash 7))
    deps := deps.insert n d
    uses := uses.insert n l
    library := library.union l
  -- A library constant's fingerprint by itself: its statement and, for a definition, its body.
  let libraryFlat (n : Name) : UInt64 :=
    mixHash (hash n) ((g.reviewedExprs n).map g.contentHash |>.foldl mixHash 7)
  -- And with everything it rests on, down through the library.
  let depths ← g.libraryDepths library.toArray libraryFlat
  let libraryOwn (n : Name) : UInt64 := (depths.get? n).getD 0
  let kind (n : Name) : String :=
    match env.find? n with
    | some (.thmInfo _) => "theorem"
    | some (.inductInfo _) => if isClass env n then "class" else if isStructure env n then "structure" else "inductive"
    | some (.axiomInfo _) => "axiom"
    -- Lean keeps a `partial def` as an opaque constant, with its body beside it.
    | some (.opaqueInfo _) => if (env.find? (Compiler.mkUnsafeRecName n)).isSome then "partial def" else "opaque"
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
  let gaps ← collectGaps env names
  -- Written only where there's something to say, so a clean project's graph is unchanged.
  let gapFields (n : Name) : List (String × Json) :=
    let found := ((gaps.get? n).getD {}).toArray.qsort Name.lt
    let isAxiom (a : Name) := env.find? a matches some (.axiomInfo _)
    let sources := found.filter fun a => !isAxiom a && a != n
    let axioms := found.filter isAxiom
    (if isDerived env n then [("derived", toJson true)] else []) ++
    (if found.contains n then [("sorry", toJson true)] else []) ++
    (if sources.isEmpty then [] else [("sorryFrom", toJson (sources.map (·.toString)))]) ++
    (if sources.isEmpty then [] else
      [("sorryVia", toJson ((sorryHops env gaps n).toArray.qsort Name.lt |>.map (fun h => (g.owner h).toString)))]) ++
    -- `native_decide` adds an axiom per use, or `Lean.ofReduceBool` in older Lean: the same trust.
    let shown (a : Name) : String :=
      if a == `Lean.ofReduceBool || a.components.contains `_native then "native_decide" else a.toString
    (if axioms.isEmpty then [] else [("axioms", toJson (axioms.map shown).toList.eraseDups.toArray)])
  -- What a reviewer reads without the docs: the signature as Lean understood it, and the docstring.
  let mut readable : Std.HashMap Name (List (String × Json)) := {}
  count := 0
  for n in names do
    count := count + 1
    progress "statements" count names.size
    let sig ← signature env n
    let doc ← findDocString? env n
    let parts ← members env n
    readable := readable.insert n <|
      (if sig.isEmpty then [] else [("signature", Json.str sig)]) ++ (doc.map fun d => [("doc", Json.str d)]).getD [] ++
      (if parts.isEmpty then [] else [("members", Json.arr parts)])
  progress "writing" 0 0
  let decls := (names.qsort Name.lt).map fun n =>
    (n.toString, Json.mkObj <| List.append (gapFields n) <| List.append ((readable.get? n).getD []) <| [
      ("kind", kind n),
      ("module", module n),
      ("lines", lines n),
      ("own", hex ((own.get? n).getD 0)),
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
  -- Each module doc's own fingerprint, so a mark on one can go stale too.
  let moduleDocs := (g.moduleNames.filter g.inScope).filterMap fun m =>
    (getModuleDoc? env m).map fun docs =>
      (m.toString, Json.str (hex (docs.foldl (fun h d => mixHash h (hash d.doc)) 7)))
  let graph := (Json.mkObj [
    ("format", (1 : Nat)),  -- GRAPH_FORMAT in server.py
    ("lean", Lean.versionString),
    ("root", root),
    ("roots", toJson (outermost.map (·.toString))),
    ("modules", Json.mkObj (modules.qsort (·.1 < ·.1)).toList),
    ("moduleDocs", Json.mkObj (moduleDocs.qsort (·.1 < ·.1)).toList),
    ("declarations", Json.mkObj decls.toList),
    ("library", Json.mkObj libraryDecls.toList),
    ("renames", Json.mkObj renames.toList)]).compress
  -- A file if asked for one: a project's initializers can print to stdout.
  match ← IO.getEnv "LEAN_VOUCH_GRAPH" with
  | some path => IO.FS.writeFile path (graph ++ "\n")
  | none => IO.println graph
  return 0
