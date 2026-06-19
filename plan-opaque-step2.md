# Plan: Opaque step 2 — "an opaque value can never be formed"

Status: design agreed 2026-06-18; implementation in landable slices.
Progress: Slice 1 (struct recursion) landed `2e979554`; Slice 1b
(slice-of-opaque, cycle-aware) landed `1c40ba52`; Slice 2 (strengthen
make/sizeof/etc. gates to embedsOpaqueByValue — the dedicated generic gate was
found redundant) landed `b7cbedaa`; Slice 3 (generic func/iface instantiation
gates, forward-ref-safe) landed `40924b14`; Slice 4 (composite-literal +
inferred-var gates) landed `6d541973`; Slice 4b (deref-as-rvalue assignment
gate — `_ = *p` / `*dst = *src` / `x := *p`) landed `fe048395`. The checker now
fully enforces "an opaque value can never be formed" on the whole-program path.
Slice 5 (REPL CheckDeclInScope hook) landed `5968e1e2`. Slice 6 (reject a
pointer/handle to an opaque-embedding non-bare type, e.g. `*Box[Opaque]`) landed
`d00fcd81` — done CHECKER-SIDE (not the IR-gen guard the design floated: IR-gen's
precondition is valid input, so the fix belongs in the checker). **STEP 2
COMPLETE**: the checker fully prevents an opaque value from being formed (batch +
REPL), and IR-gen never receives an opaque instantiation. Tiny benign residual:
a pointer nested under further pointers (`**Box[Opaque]`, `@(*Box[Opaque])`) —
the one-level pointee check doesn't reach it; closing it needs the
cycle-detection a recursive peel would require for `type P *P`. Tracked in
claude-todo.md.
Prereq: step 1 landed (`f3807ed2` panic removal + checker gates; `e887543e`
foldConstNum gate). See the opaque-layout MAJOR in `claude-todo.md`.

## Goal

An opaque type (`type T` with no body — `TYP_NAMED`, `Underlying == nil`,
layout external) must never have a **value** formed for it. Once a value is
formed, IR-gen silently lays it out as `i64` (the `ptrSize()` fabrication that
`SizeOf()` returns) — wrong code, and for generics a mangled-symbol collision.
Holding opaque behind `*T`/`@T` is legal and MUST stay legal. A generic type
param is `TYP_TYPE_PARAM` (not opaque). A forward-REFERENCED concrete type is a
nil-`Underlying` placeholder byte-identical to opaque during pass-1 collection;
the park/pending machinery resolves it before post-collection checks — every
gate below runs post-collection, so a nil-`Underlying` `TYP_NAMED` it sees is a
genuine opaque, never a forward-ref.

Governing principle: the compiler must NEVER panic on a code path reachable
from user input. Panic (or a fatal internal diagnostic) is only for internal
invariants unreachable once the checker has done its job.

## Verified mechanism (the generic collision — the priority)

`type Box[T any] struct { v T }; var b Box[Opaque]`:

1. Checker resolves `Box[Opaque]` via `instantiateGenericDeclWithArgs` →
   `populateInstantiatedStruct` (`check_generic_type.bn:301-319`). Field `v`
   resolves (under `T→Opaque`) to the opaque `@Type`. **No gate** — the
   instantiation is accepted. (`checkValueEmbedding` explicitly skips generic
   decls, and the instantiation is never checked.)
2. The checker mangles the instantiation via `QualifiedTypeName`
   (`type_name.bn:typeNameImpl`), which returns the opaque's `Name` ("Opaque")
   verbatim — so the **checker** name is distinct (`…__bn_inst__Opaque`).
   **Verified:** the `__int` collapse is purely an IR-gen artifact.
3. IR-gen `ensureInstantiatedStruct` (`gen_generic.bn:475`) resolves field `v`
   via `resolveTypeExpr`, which finds no match for unqualified `Opaque` and
   falls through to `types.TypInt()` (`gen_type_resolve.bn:119`). The struct
   becomes `<{ i64 }>` and mangles as `…__bn_inst__int` — identical to a real
   `Box[int]`: wrong layout + symbol collision.

**Consequence:** a checker gate that rejects `Box[Opaque]` removes the
collision entirely, before IR-gen runs. The whole fix lives in the checker;
IR-gen (Part B) is defense-in-depth.

## Decisions (agreed)

- **By-value only.** Every generic gate fires only when an instantiation forms
  a `T` *value* (field/param/return of type `T`, or embedding it). Pointer uses
  (`*T`/`@T`) stay legal — `requireSizedType`/`embedsOpaqueByValue` already stop
  at pointers.
- **Generic structs, functions, AND interfaces all in scope** (deferring only
  defers work; interfaces are lower-risk but still a by-value formation in the
  ABI).
- **Slice-of-opaque rejected wholesale**, reusing the existing "cannot use an
  opaque type by value" diagnostic (consistency over a dedicated message). Note:
  a slice of opaques is only rejected where the element is *genuinely* opaque
  (no layout visible); in a context where the type is concrete, `isOpaqueType`
  is false and it compiles — so cross-package "function with layout knowledge
  returns the slice" still works there.
- **Part B narrow IR-gen guard: include it, as a build-failing internal
  diagnostic (not `panic`)** — safer if a Part-A hole turns out source-reachable.

## Part A — checker gates (landable slices, each green)

**Slice 1 — `embedsOpaqueByValue` struct recursion** (LANDED `2e979554`).
Added `TYP_STRUCT` (walk resolved `.Fields[i].Type`) to the existing `TYP_ARRAY`
recursion. Array/struct value-containment is acyclic, so it terminates. Closes
the anon-struct-value-field gap.

**Slice 1b — slice-of-opaque, cycle-aware** (LANDED `1c40ba52`, decision A).
DISCOVERY: naively recursing into slice elements does NOT terminate — a
recursive managed type (`struct { kids @[]Node }`, very common) loops
`Node → @[]Node → Node → …` and hung the checker (broke conformance 252/253).
Fix: recurse `TYP_SLICE`/`TYP_MANAGED_SLICE` elements WITH a per-branch visited
set of named types (mirrors `dfsCycleSearch` in `check_pending_cycles.bn`); a
back-edge to a name already on the branch is a cycle that adds no new opaque.
Slice-of-pointer (`@[]@Opaque` / `*[]*Opaque`) stays legal. The opaque helpers
moved to `check_opaque.bn` (kept `check_builtin.bn` under the soft limit).

**Slice 2 — strengthen make/make_slice/sizeof/alignof gates** (LANDED `b7cbedaa`).
DISCOVERY: a dedicated generic-struct gate (the original plan) is REDUNDANT and
double-errors — Slice 1's `embedsOpaqueByValue` struct recursion ALREADY rejects
`Box[Opaque]` as a var / field / param / return (the instantiated struct's
opaque field is reached through `requireSizedType`). The real gap was the
builtin gates: `make`/`make_slice`/`sizeof`/`alignof` (and the const-fold /
bit_cast variants in `eval_const_int.bn`, `check_cast_fits.bn`) checked
`isOpaqueType` (bare opaque only), so `make(Box[Opaque])`, `make(struct{o
Opaque})`, `sizeof(Box[Opaque])` silently fabricated `i64`. Fix: switch all of
them to `embedsOpaqueByValue`. With Slice 1 + Slice 2, a `Box[Opaque]` VALUE can
never be formed → the collision is closed for value forms. The pointer-only
escapee `*Box[Opaque]` (a pointer is sized, so the checker can't reject it) still
reaches IR-gen and fabricates the colliding `i64` Box — left for Part B (Slice 6,
now confirmed LOAD-BEARING, not just defense-in-depth). conformance/838 +
unit lock-in.

**Slice 3 — Generic function + interface instantiation gates.** (LANDED `40924b14`.)
The discard form `_ = mk[Opaque]()` was the key gap (no var to catch it; silently
monomorphized to an i64-returning `mk__bn_inst__int`). Both gates verified
forward-ref-safe.
- Function: `instantiateGenericFunc` (`check_generic.bn:62,68`) — after
  `substituteTypeParams` builds `newParams`/`newResults`, `requireSizedType` each.
  `id[Opaque]` (`x T`, returns `T`) → rejected; `f[Opaque]` with `x *T` → fine.
- Interface: `populateInstantiatedInterface` (`check_generic_type.bn:195`) — after
  `resolveFuncDeclType(m)` for each method, `requireSizedType` each param/result.
  `I[Opaque]{ get() T }` → rejected.
Conformance + unit for each; positive companions (pointer-T instantiations compile).

**Slice 4 — Composite-literal + inferred-var gates.**
- Composite literal: `check_expr_composite.bn` (after `capturePendingIfSized`) —
  `requireSizedType(c, typ, e.Pos)`. The source choke point: rejects `Opaque{}`
  in any expression context.
- Inferred var: `check_decl.bn` inferred-`var x = …` branch (no gate today) —
  `requireSizedType(c, valType, d.Pos)` after `defaultTypeForExpr`. Catches opaque
  values arriving from other expressions (`var x = *opaquePtr`, `var x = f()`).
Conformance (`var x = Opaque{}`, anon-struct param exercising Slice 1+the param gate).

**Slice 5 — REPL `CheckDeclInScope` hook** (`checker.bn:~352` + the `DECL_TYPE`
early-return). Call `checkValueEmbedding` after `collectDecls`, mirroring the
batch paths. Generics are covered for free (same `resolveTypeInstantiation`
path). Unit test feeding `type Opaque` then `var x Opaque` / `type S struct{o
Opaque}` through the REPL path.

## Part B — IR-gen narrow guard (defense-in-depth)

**Slice 6** — `gen_generic.bn:475-476`, `ensureInstantiatedStruct`. After
`f.Type = resolveTypeExpr(fd.Type)`: if `f.Type` is `TypInt()` AND `fd.Type` is
`TEXPR_NAMED` whose name is not a builtin-int spelling, the name failed to
resolve (the only way a named-non-int becomes `int` is the fabrication
fallback) → emit a **build-failing internal diagnostic** (not `panic`). Not
source-reachable once Part A is complete (the checker rejected the input), so
it's a genuine internal-invariant check. Catches any future Part-A hole loudly
at struct registration, before a wrong symbol is emitted. Pointer/slice args
arrive as `TYP_POINTER`/`TYP_SLICE`, never bare `int`, so it only fires on the
genuine fabrication. Do NOT build a general `isInternalFallback` predicate
(dual-use line; `type Alias int` also resolves to `TypInt`).

## Sequencing & tests

Order: 1 → 2 (priority) → 3 → 4 → 5 → 6. Each lands green with default
conformance modes + unit coverage in the package it edits (Slices 1/4 →
`pkg/binate/types`; 2/3/5 → `types`; 6 → `pkg/binate/ir`). Per the Bug Discovery
Protocol, positive tests (pointer-held opaque through generics/slices still
compiles) pin the "must stay legal" boundary and matter as much as the err
tests. Err-conformance follows the `8xx_err_*` `.bn`+`.error` pattern.
