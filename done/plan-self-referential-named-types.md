# Plan: self-referential named non-struct types in IR-gen

Todo entry: "A named non-struct type that names itself through an indirection is mis-typed in IR-gen"
(claimed 2026-09-30, work-7).  Recon: a read-only two-agent sweep of irgen / IR layers and types /
codegen / VM / interp / native, 2026-09-30, over a draft (work-7) that registers a named entry before
resolving its underlying.  Nothing below is implemented yet.

## The bug

`type StateFn @func(int) StateFn`, `type Tree @[]Tree`, `type P *P`, `type L @[]@L`, `type Visitor
@func(Visitor) int`, and mutual groups (`type A @[]B; type B @func(A) B`) are accepted by the checker (it
pre-registers the placeholder).  IR-gen resolves a declaration's underlying before registering its
Module.TypeAliases entry, and the dependency walk skips the in-progress name, so the inner reference falls
to resolveTypeExpr's `int` fallback: StateFn's result is one word where a two-word func value belongs
(clang: "extractvalue operand must be aggregate type"; natives: silent), Tree is `@[]int`.

## 1. Registration (IR-gen)

- Pre-register the named entry (MakeNamedType, Underlying nil), then resolve the underlying and set it —
  the draft's registerTypeDeclEntry, replacing the five entry sites (registerModuleTypeDecl,
  registerPkgTypeDecl, registerImportFieldsAndFuncs, RegisterImport, genReplTypeDecl).
- The draft covers only DIRECT self-reference.  Mutual recursion needs two phases per package: (1)
  pre-register every distinct (non-alias) named entry of the package; (2) resolve aliases in dependency
  order (they need only the named entries to exist), then set each named type's Underlying.  Also covers a
  cycle through an alias (`type T = @[]N; type N @[]T`) if the checker accepts it.
- registerImportFieldsAndFuncs (gen_import.bn ~251), RegisterImport (gen_register_import.bn ~95, test-only)
  and the REPL's GenTypeDecls → genReplTypeDecl resolve in textual order, so a non-struct type naming a
  LATER one gets `int` today even without recursion; they also append a second TypeAliases entry / a
  second TYP_NAMED object for an already-registered type (lookupTypeAlias returns the first).  So one named
  type can have several objects: every recursion guard must key on the QUALIFIED NAME, not the object.
- During the window between registering an entry and setting its Underlying only resolveTypeExpr runs; its
  one read of a nil Underlying (findAnonStruct → types.Identical → NamedIdentityName) is nil-guarded.  Keep
  it that way: SizeOf / AlignOf / NeedsDestruction / StripWrappers-based predicates all answer WRONG (not
  crash) on a nil Underlying.

## 2. Walkers that loop once the type is recursive

- **irutil.dtorTypeSuffixRec** (nameutil.bn ~150; with anonStructSuffix; entry points DtorTypeSuffix,
  DtorNameForType, MsElemsDtorName; copy names share it): peels NAMED to Underlying and descends through
  mp_ / ms_ / arrN_ / nameless-struct fields.  Loops for Tree, `@[]@L`, `type M @M`, `type A [2]@A`,
  `@[]B / @[]A` groups; NOT for StateFn / Visitor (a managed func value is the fixed `func` token) or `*P`
  (never destructible).  Identity contract: definitions and references name DIFFERENT spellings of one type
  (boxSlot0DtorName names the unpeeled `Tree`; RegisterModulePendingDtor generates for the peeled
  `@[]Tree`; elemDtorName strips wrappers first), so the name must be a pure function of the named-type
  graph — a "token on second encounter" stack scheme breaks it (`Tree` → ms_<ref> but `@[]Tree` →
  ms_ms_<ref>).  Proposed: in NESTED position only, a TYP_NAMED that can reach itself (DFS over exactly
  this walker's edges, keyed on qualified name, stopping at named structs / func / iface values /
  primitives / opaque types) is written nominally as mangle.LpTypeArgNamedRaw(qualified name) — the token
  nested named structs already use; every other named type keeps the structural peel (`@[]Celsius` still
  shares `__dtor_ms_int`).  Normalize the qualified name as MangleTypeArg does (unquote the checker's Pkg).
- **irgen ensureMsDtor / ensureMpDtor / ensureArrayDtor** (gen_dtor_emit.bn ~238 / ~287 / ~364; closed
  through ensureNestedMptrPointeeDtor ~309): record the name in `generated` only AFTER recursing into the
  element, so a recursive type recurses forever even once naming terminates.  Fix: record the name right
  after the HasName check, then recurse, then generate and AddFunc (the pre-register pattern
  ensureInstantiatedStruct uses); the helper body then calls itself by name (LLVM / native resolve it; the
  REPL VM backfills via backfillExternCachesForName).
- **codegen discoverStructFromType** (emit_types.bn ~216, from collectStructTypes): NAMED → Underlying →
  pointer / slice / array / readonly Elem → the same NAMED.  Loops for Tree, `*P`, `@[]@L`, `readonly
  Tree`, `*[4]S`; not through a func value (no FUNC_VALUE arm).  Fix: a module-level visited list of named
  types (by qualified name), reset beside moduleStructDefs; the walk only discovers struct defs, so
  skipping a repeat changes nothing.
- Everything else was checked and terminates (types layout / ABI / identity / naming, MangleTypeArg,
  ReceiverBaseTypeName, the by-value-only walkers, the dtor / copy body generators, the VM and natives).

## 3. Independent, found by the same sweep

- **interp marshalableType** (runfunc_typed.bn ~230; from RunFuncTyped / CallIfaceMethod) runs on CHECKER
  types, which are already recursive on main: loops on `type Tree @[]Tree` and on an ordinary recursive
  struct `type Node struct { kids @[]Node }`.  Separate todo entry.
- The type graph of a recursive group is a refcount cycle (named.Underlying → wrapper, wrapper.Elem →
  named), leaked once per compilation in multi-compilation processes (REPL, multi-root lint) —
  compiler-internal memory, the same cycle recursive structs already have.

## Tests

Conformance (drafted as 1460 — renumber): StateFn and Tree, same package and through a `.bni`, with a
live-block balance after a warm-up.  Add: `@[]@L`, `type M @M`, `type A [2]@A`, a mutual group through a
func value and one through slices only, `*P`, `readonly Tree`, a Tree element boxed into `@any`, and the
REPL (declared at the prompt).  Every backend.
