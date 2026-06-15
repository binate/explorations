# Plan: reject type redeclaration across `.bni`/`.bn`

Status: IMPLEMENTED on worktree (2026-06-15), pending land. Three commits:
`rt` baremetal cleanup, conformance fixture migration, and the enforcement
(a dedicated `checkTypeRedeclaration` pass — see the Design correction below;
the approved in-`collectTypeDecl` placement was the wrong layer). Full unit
suite 45/0; conformance builder-comp 1460/0, builder-comp-int 1445/0,
builder-comp-comp 1460/0; hygiene 14/14. The grammar doc fix (§"Doc fix") is
moot — `explorations/grammar.ebnf` was retired the same day and the canonical
`docs/spec/binate.ebnf` already carries the forward-decl `TypeDef` alternative.

## Goal

A type may be declared **full at most once per package**. The only legal shapes
for a given type name in a package are:

1. **Transparent** — full decl (`type T struct{…}` / alias / distinct) in the
   `.bni` only; the `.bn` impl uses it but does not redeclare it.
2. **Opaque** — forward decl (`type T`, no body) in the `.bni` + exactly one
   full decl in a `.bn` (the layout the importer can't see). This is the
   conformance/512 pattern.
3. **Package-private** — one full decl in a `.bn`, absent from any `.bni`.

**Illegal** (the thing this plan adds enforcement for):

- **Full-in-both** — full decl in both the `.bni` and a `.bn` (the same name,
  whether or not the bodies match).
- **`.bn`-dup** — two full decls of the same name across the package's `.bn`
  files (within a single build — build-gated variants don't count, only one
  compiles).

The rule is already *documented* by the checker's own `dupTypeMsg`:
"define in .bni OR .bn, not both; use `type <name>` forward-decl in .bni for
opaque export." It is only half-*enforced* (see Mechanism).

## Current state (repo-wide sweep, grouped by declared package path)

| Shape | Count | |
|---|---|---|
| transparent (full `.bni` only) | 112 | already correct |
| package-private (full one `.bn`) | 124 | already correct |
| opaque (forward `.bni` + full `.bn`) | 1 | conformance/512 |
| **full-in-both (REDECL)** | **12** | **1 production + 11 conformance fixtures** |
| dangling forward (forward, no full) | 0 | — |

The production tree is essentially clean. The **one** real production violation:

- **`pkg/builtins/rt` `ManagedSlice`** — full `type ManagedSlice struct{…}` in
  both `ifaces/core/pkg/builtins/rt.bni:200` and
  `impls/core/common/pkg/builtins/rt/rt_baremetal.bn:326` (byte-identical).
  The **hosted** `rt.bn` variant already does it right (transparent — relies on
  the `.bni`); only the **baremetal** variant redeclares. It compiles today
  purely because of the same-shape escape hatch below.

The 11 conformance fixtures with full-in-both: 062 (geom.Point), 110
(mylib.Result), 694 (dep.Box), 675/676 (shape.Circle), 678+ (store.Node), 785
(aa/thing + bb/thing). Plus the cross-test-reused fixture package names
`pkg/hello` / `pkg/things` / `pkg/wk` need a per-test recheck (the sweep merged
them across test dirs).

(Sweep note: an early pass mis-grouped every `.bn` opening with a `#[build(…)]`
annotation — the package-extraction broke on the first non-comment line before
reaching `package` — dropping them into a `<nopkg>` bucket. Fixing that is what
surfaced the `rt`/`ManagedSlice` violation. Lesson: scan all lines for
`package`, and grep the pattern repo-wide, not a guessed subtree.)

## Mechanism — why full-in-both compiles today

Confirmed by code-read and an empirical test (deleting `geom.bn`'s `type Point`
→ 062 still passes):

- A package's own `.bni` **is merged into the same compilation**: the loader
  *prepends* the `.bni`'s type/const/iface/impl/extern decls to `pkg.Merged`
  (`loader.bn:288-339`), and the `.bni` is *also* loaded into scope via
  `LoadPackageInterface` → `buildScopeFromFile`. So each `.bni` type decl is
  seen by the checker **twice** (once into scope, once as the prepended decl in
  the merged AST).
- `collectTypeDecl` (`check_decl.bn:249-335`) already errors via `dupTypeMsg`
  when two full decls of a name **disagree** in shape. But when they have the
  **same shape** it silently returns (lines 285-289, 298-302, 320-324) — a
  deliberate escape hatch so the benign "`.bni` decl seen twice" reprocessing
  doesn't error. That same lenience lets a genuine `.bni`+`.bn` full-in-both
  through (their shapes match, so it's silently deduped).

So the fix is to close the same-shape escape hatch **only for genuine `.bn`
redecls**, while keeping the benign `.bni`-reprocessing path silent. That
requires knowing each decl's source file.

## Design — tag decls at load, enforce in a dedicated decl-list pass

> **Design correction (implementation).** The original plan put the check
> *inside* `collectTypeDecl`, gating the same-shape silent-return on `FromBNI`.
> That is the **wrong layer** and was abandoned: the same-shape escape hatch
> absorbs *several* benign re-processings of the **same** decl object, not just
> the `.bni` merge-prepend. In particular `resolveBuiltinScalarTypeDecls`
> pre-fills a distinct-scalar placeholder (`type Celsius int`) *before*
> `collectTypeDecl` runs, so a **single** `.bn` decl is re-seen with its
> underlying already set — and a `FromBNI`-gated error then false-fires on a
> lone, legitimate declaration. `collectTypeDecl` is resolution machinery, not
> policy. The rule instead lives in a **dedicated pass that counts source decl
> objects** (one `ast.Decl` per `type T`), which no amount of re-processing can
> fool. `FromBNI` is still set at load (the approved tag) and still used — but
> only to phrase the diagnostic and point at the `.bn` decl, not for detection.

### 1. AST: source origin on `ast.Decl`

Add `FromBNI bool` to `ast.Decl` (`ast.bni`). Default `false` = came from a
`.bn`. BUILDER-safe (a plain bool field; no new language feature).

### 2. Loader: set the tag

Tag `FromBNI = true` on every `bniFile.Decls[i]` right after the `.bni` is
gated in `loadPackage` — *before* the merge block, so it covers **both** the
with-impl path (the prepend carries the same decl pointers into `merged.Decls`)
**and** the `.bni`-only fallback (`merged = bniFile`, no `.bn` files). `.bn`
decls keep the default `false`. (Placing it only inside the merge block — which
requires `merged != nil` — misses `.bni`-only packages like `pkg/builtins/reflect`
and false-fires; this bit during bring-up.)

### 3. Checker: a dedicated `checkTypeRedeclaration` pass

`collectDecls` (the pass-1 entry routed through by every package-check entry —
loader-merged `checkPackageImpl`, the single-file `Check`, and the REPL) calls
`checkTypeRedeclaration(c, decls)` *before* `preRegisterTypeNames`. It walks the
decl list and, for each full type decl, reports an error if an earlier full
decl of the same name exists:

- A **full** type decl = `DECL_TYPE`, `!IsForward`, non-generic (`TypeParams`
  empty). Forward decls never count (so `forward .bni + full .bn` = one full →
  OK); generic type decls are out of scope (their monomorphization keys aren't
  path-qualified yet — deferred, conformance/792).
- The merged decl list holds each *source* decl exactly once (the prepend adds
  each `.bni` decl once; `.bn` decls are distinct files). So **transparent** = 1
  full decl → OK; **full-in-both** = 2 full (one `.bni`-prepended + one `.bn`) →
  error; **`.bn`-dup** = 2 full `.bn` → error; **opaque/private** = 1 full → OK.
- `FromBNI` (plus a propagated group flag, for the `type (...)` grouped form,
  though none exist today) selects the message and points the error at the `.bn`
  decl (the one to remove) rather than the authoritative `.bni`.

`collectTypeDecl` is left as-is (its existing same-shape silent-return /
mismatch-error behavior is unchanged): it stays pure resolution, and the
mismatch-error is harmless defense-in-depth alongside the new pass.

Build-gated variants don't false-trigger: only the active variant's `.bn` is in
the merged compilation, so the pass sees at most one variant's decl.

### 4. (Secondary, separable) dangling forward

A `.bni` forward decl that no `.bn` ever fills (`TYP_NAMED`, `Underlying==nil`
after all decls collected) is an opaque type with no backing — uninstantiable.
The sweep found **0**. Adding a post-`collectDecls` pass to reject it is
optional and separable from the main rule; flagged for a decision, not bundled.

## Migration

1. **`rt` baremetal** — delete `type ManagedSlice struct{…}` from
   `rt_baremetal.bn`; it inherits the decl from `rt.bni` transparently, like the
   hosted `rt.bn`. **Must verify the `builder-comp_arm32_baremetal` mode** (this
   is core-runtime, build-gated code).
2. **11 conformance fixtures** — delete the redundant `.bn` full decl in each
   (→ transparent); they keep testing their original feature (cross-pkg struct /
   iface / alias / same-segment mangle). Recheck the `hello`/`things`/`wk`
   per-test cases. 785 stays valid (same-segment mangling is about the package
   path, not where the struct is declared).
3. **New rejection test** — `conformance/NNN_err_type_redecl` (or a unit test):
   a package with a full `.bni` decl + a full `.bn` decl of the same type →
   expect the duplicate-type error. Plus positive coverage that all three legal
   shapes still compile.

## Tests

- **Unit** (`check_decl_test.bn`): `TestCheckDuplicateStructSameShapeNoWarning`
  asserts same-shape silent collapse — its semantics change. Same-shape `.bn`
  dup (both `FromBNI=false`) now **errors**; the benign case now requires
  `FromBNI=true`. Update it + add a case per row of the matrix above.
- **Conformance**: the rejection test in Migration §3.

## Doc fix (independent, do alongside)

`explorations/grammar.ebnf` is **stale**: its `TypeDef` rule lacks the empty /
forward alternative that the parser (`parse_decl.bn:269-289`) and the spec
grammar (`docs/spec/binate.ebnf:155-160`) both have. Add the
`| (* empty *)  (* opaque / forward-declared *)` alternative to match.

## Sequencing (each commit green)

1. `rt` baremetal migration (transparent) — independent correctness fix; verify
   arm32_baremetal. *(Compiles today; still compiles after — no rule yet.)*
2. Migrate the 11 conformance fixtures to transparent. *(Still pass; no rule.)*
3. AST `FromBNI` + loader tagging + `collectTypeDecl` rule + unit-test update +
   the new rejection conformance test. *(Enforcement lands with zero existing
   violations left to trip on.)*
4. `explorations/grammar.ebnf` fix (commit to explorations separately).

## BUILDER compatibility

`ast`, `loader`, `types` are all in cmd/bnc's BUILDER-compiled tree. A `bool`
struct field + its reads/writes are well within the BUILDER subset — safe. The
`rt` baremetal change is core-runtime; gate verification on the
`builder-comp_arm32_baremetal` conformance mode.
