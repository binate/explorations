# Execution Plan: Type Assertions, Type Switches, and RTTI

**Status:** detailed, edit-site-level execution plan (2026-07-03). This expands
the high-level `plan-type-assertions.md` into ordered steps anchored to concrete
files, functions, and constants. The **design is settled and specified** — see
the cross-references in the high-level plan (§11.12, §7.13.8, §7.13.14, §13.8,
§14.10, §17.5, and `claude-notes.md`). This document is the *implementation*
roadmap; it does **not** re-litigate design. Anything here that goes beyond the
spec (record field order, table search structure, symbol names, linkage) is
**informative** (Annex-B-class) and is called out as an implementation choice.

Companion: `plan-type-assertions.md` (the high-level phase overview). Read that
first for the "what" and "why"; this file is the "where" and "in what order."

> **Adversarially reviewed 2026-07-03** (three independent code-grounded
> reviewers: ABI/dispatch, RTTI/cross-mode, frontend/refcount/spec). The core
> Phase-1 ABI-shift inventory and the transitive-closure/leaf-TypeInfo reuse
> **survived** scrutiny. Five MAJOR and several MINOR findings were folded back
> in and are marked inline with **⚠ Review** and consolidated in **§6** with the
> updated risk register (R4–R11, §3) and open decisions (§4). Read §6 before
> starting — two claims the first draft called "zero-cost reuse" (comma-ok
> wiring; the cross-mode/VM story) are the ones that actually carry the most work.

---

## 0. Ground truth from reconnaissance (read this before touching code)

These are the load-bearing facts the plan is built on. Each was verified against
the tree at plan-authoring time; **re-verify before editing** (line numbers
drift).

### 0.1 The vtable any-block is exactly ONE word today

Slot 0 = the destructor handle (or null); method slots start at slot 1. Adding a
`*TypeInfo` grows the any-block to **two** words (dtor at 0, TypeInfo at 1,
methods start at 2). Every place that encodes "one word precedes the methods"
must move in lockstep. The canonical constant is:

- **`pkg/binate/ir/gen_iface_extends.bn` → `IfaceFullVtableSize`**: `var n int = 1`
  (the any-block word count). **This is the single most load-bearing constant.**

### 0.2 The dispatch slot index is produced in ONE place, consumed raw everywhere

- **Producer:** `pkg/binate/ir/gen_iface_dispatch.bn` → `findInterfaceMethodFromBase`
  (`var cursor int = base + 1`, returns `cursor + j`). This stamps `instr.Index`.
- **Parent sub-vtable offset producer:** `pkg/binate/ir/gen_iface_extends.bn` →
  `parentSlotOffsetFromBase` (`var cursor int = base + 1`), feeding
  `IfaceParentSlotOffset` → `instr.IfaceUpcastSlotOffset`.
- Every backend (LLVM `emit_iface_call.bn`, x64 `x64_iface.bn`, aarch64
  `aarch64_iface.bn`) and the VM (`vm_exec.bn`, `vm_exec_iface.bn`) consume
  `instr.Index` / `instr.IfaceUpcastSlotOffset` **raw** — they do no `+1` of
  their own. So fixing the two producers above fixes every dispatch reader.

### 0.3 But the vtable EMITTERS re-derive the layout independently (4 copies)

This is the trap. Four separate functions build the physical vtable and each
independently writes "dtor slot, then methods":

1. **LLVM:** `pkg/binate/codegen/emit_impls.bn` → `collectImplVtableSlots`
2. **x64:** `pkg/binate/native/x64/x64_iface.bn` → `collectImplVtableSlots_x64`
3. **aarch64:** `pkg/binate/native/aarch64/aarch64_iface.bn` → `collectImplVtableSlotsNative`
4. **VM:** `pkg/binate/vm/lower.bn` → `fillVtableLayout` (`slots[base] = dtorEntry`,
   `cursor := base + 1`)

A missed emitter puts methods at the wrong slot while dispatch reads the shifted
index → **silent misdispatch**. These four + the two producers in 0.2 + the size
formula in 0.1 + the VM guard in 0.4 must all change **atomically in one commit**.

(arm32: `pkg/binate/native/arm32/arm32_iface.bn` → `emitImplVtables` is a
panic-stub — no slot math today; it only matters when arm32 iface support lands,
but the new layout must be mirrored there when it does.)

### 0.4 VM has a hardcoded "methods start at slot 1" guard

- `pkg/binate/vm/vm_exec.bn` (BC_CALL_IFACE_METHOD): `slot := instr.Aux & 65535`
  then a bounds check `slot < 1` — **must become the new method base (2)** or
  the guard rejects valid dispatch.

### 0.5 The dtor stays at slot 0 — the `*any` upcast depends on it

- `pkg/binate/types/types_assignable.bn` `*any` upcast reuses the source vtable
  because "slot 0 is T's dtor, which doubles as any's whole vtable since any has
  no methods." **Keep the dtor at offset 0.** After the change, `any`'s vtable
  becomes a 2-word `[dtor, TypeInfo]` block; the `*any` upcast still points at
  offset 0 and now legitimately spans both any-block words. Verify the upcast
  range/copy still works (it points at the block start, so it does).
- Hardcoded dtor-load-at-0 sites to audit (should stay 0): `emit_iface_call.bn`
  (`getelementptr ... i64 0`), `x64_dispatch.bn` (`Mem(rd, 0)`),
  `aarch64_dispatch.bn` (`MemImm(rd, 0)`), `vm_exec_iface.bn` (`natVt[0]` /
  `Methods[0]`).

### 0.6 Do NOT confuse iface-VALUE offsets with vtable-INTERNAL slots

- `pkg/binate/types/layout_offsets.bn` `IfaceValueDataIndex()==0` /
  `IfaceValueVtableIndex()==1` are the offsets of `data`/`vtable` **within the
  2-word `{data, vtable}` interface value struct** — NOT within the vtable block.
  They are **unaffected** by the any-block growth. Do not touch them.

### 0.7 The reflect/descriptor machinery is the emission template

- `DataGlobal` (`pkg/binate/ir.bni`, ctors `DataBytes`/`DataInt`/`DataSymref`/
  `DataZero`/`NewDataGlobal`) is the backend-neutral static-blob primitive.
  `DG_WEAK` linkage (used by `BuildImplVtable`) coalesces duplicate definitions
  across TUs — exactly what "one TypeInfo per type program-wide" needs.
- `pkg/binate/ir/data_pkg_descriptor.bn` → `BuildPackageDescriptor` is the
  closest existing "static record with identity + name-rodata + pointer-table"
  and is the structural template for the `TypeInfo` record.
- Cross-backend descriptor emission already exists in all four backends
  (`emit_pkg_descriptor.bn`, `x64_pkg_descriptor.bn`, `aarch64_pkg_descriptor.bn`,
  `arm32_pkg_descriptor.bn`) and is ingested cross-mode by the VM
  (`vtable_inject.bn` `registerVtableAddr`, keyed by mangled name → native
  address). TypeInfo follows this exact pattern.

### 0.8 The impl registry already gives each impl site its ancestor closure

- `pkg/binate/ir/gen_impl.bn` → `collectImplsFromDecl` already registers, for
  each `impl T : Child`, one `ImplInfo` per `(T, Child)` **and** one per
  `(T, ancestor)` for every transitive ancestor (via `IfaceAncestorClosure`).
  So each `impl` site already carries the `(T, iface)` closure it needs — the
  **distributed satisfaction model** (§2.2b ✅ DECISION) emits one `SatEntry` per
  `m.Impls` row **at that site**, with **no per-type aggregation**. The
  per-`(T,J)` sub-vtable is the standalone `__ivt.<T>__<J>` symbol at offset 0
  (`findImplVtableName` / `mangle.ImplVtableName`), **not** `&(@__ivt) +
  IfaceParentSlotOffset*W` (per the §2.2b RESOLVED note).
  > **⚠ SUPERSEDED framing.** An earlier draft grouped `Module.Impls` by
  > `(RecvPkg, RecvTypeName)` to build a *per-type* satisfaction table. That is
  > **not** used: `m.Impls` is per-TU, so the grouping is **incomplete under
  > cross-package impls** — no single TU sees T's full impl set (no orphan rule,
  > `iface.crosspkg.no-orphan`; §2.2b BLOCKER). Satisfaction is distributed
  > per-`(T,J)`, not per-type. Spec: §7.13.14 `type.layout.satisfaction`.

### 0.9 BUILDER compatibility

- The any-block growth touches `pkg/binate/ir` files that ARE in `cmd/bnc`'s
  BUILDER-compiled tree (`gen_iface_extends.bn`, `gen_iface_dispatch.bn`). The
  *source edits* are trivial (`1`→`2`, `+1`→`+2`, add a slot) and stay well
  within the BUILDER subset. **No new language feature is introduced into the
  BUILDER tree.**
- There is **no cross-artifact ABI hazard**: every build stage compiles the whole
  program (including `pkg/rt`) from source, so the layout is internally
  consistent within each compile. The frozen BUILDER `bnc` emits the *old*
  1-word layout, but it only produces gen1's binary from gen1's source — it never
  emits vtables that gen1-compiled code consumes at runtime. Confirm by running
  the full self-compile chain (`builder-comp-comp`) after the layout change.
- The assertion **syntax** (`.(...)`, `switch x.(type)`) is new parser surface,
  but it appears only in new test/user code — never in `cmd/bnc`'s own source —
  so the BUILDER never has to parse it. No BUILDER bump needed. (Verify: grep
  `cmd/bnc`'s tree for any `.(` usage after implementing; there should be none.)

---

## 1. RTTI record shape (implementation choice — informative)

The spec (§7.13.14) fixes the *contents* and cross-mode result-agreement but
leaves field order and search structure informative. Per the §2.2b ✅ DECISION,
satisfaction is a **distributed `(T, J)` registry**, NOT a per-type table in
`TypeInfo` — so the record carries only its 5 real fields (the `sat_len`/
`sat_table` words were **✅ dropped**, `89ad8b18`), and `SatEntry` records are
**standalone weak globals** keyed on `(TypeInfo, IfaceId)`, emitted at each
`impl` site, not owned by `TypeInfo`. Shapes:

```
TypeInfo {                      // static, one per concrete type, weak linkage — 5 words
    identity:  *TypeInfo        // = the record's own ADDRESS (no stored word; §1 note)
    dtor:      handle           // same handle as the vtable any-block slot 0
    size:      int              // t.SizeOf()  (target's value, baked at emit)
    align:     int              // t.AlignOf()
    name:      *[]readonly char // t.QualifiedTypeName() into rodata
    // satisfaction is NOT here — distributed SatEntry globals (spec type.layout.satisfaction)
}
SatEntry {                      // standalone weak_odr global, one per (T, J) m.Impls row;
                                //   keyed on (TypeInfo, IfaceId); NOT pointed to by TypeInfo
    type_id:   *TypeInfo        // &TypeInfo(T) — the registry key's first half
    iface_id:  *IfaceId         // &IfaceId(J) — per-interface identity token (see §1.1)
    subvtable: *void            // &__ivt.<T>__<J> at offset 0 (standalone symbol,
                                //   NOT &(@__ivt)+IfaceParentSlotOffset*W; §2.2b RESOLVED)
}
```

Notes:
- **`identity` as a self-pointer** means a concrete assertion is `scrutinee's
  TypeInfo* == target type's TypeInfo*` — a single pointer compare. The target's
  `TypeInfo*` is a static symbol reference known at the assertion site.
  **⚠ Review-flagged (MINOR):** a self-referential `DataGlobal` (a `DataSymref`
  to its own symbol) has **no in-tree precedent** — every existing weak
  DataGlobal (`@__ivt`, dtors, func-value handles, descriptor nodes) references
  *other* symbols, never itself, so the "coalesces the same as `@__ivt`/dtors"
  defense is *false by analogy* (those never self-reference). Self-relocation
  under `weak_odr` (LLVM COMDAT) / `N_WEAK_DEF` (Mach-O, linker-coalesced) is
  standard and almost certainly benign, but is **untested here**. Two mitigations,
  pick one: (a) add an explicit Phase-2 link-smoke that a generic type
  instantiated in two TUs coalesces to one `TypeInfo` **and** both TUs' `identity`
  self-refs resolve to the survivor; or (b) drop the self-pointer entirely and use
  the **`TypeInfo` symbol's own address** as the identity (the assertion already
  references `&bn_TypeInfo.<T>` — comparing the record *addresses* needs no
  interior `identity` field at all). **(b) is simpler and precedent-free-risk-free
  — recommend it**, making `identity` an implementation non-field.
- **`size`/`align`/`name`** are not needed by assertions per se (the concrete
  test is pure identity); they are included because §7.13.14 mandates them and
  reflection (§20.3) will need them. The `name` also feeds the failed-assertion
  panic diagnostic (`<dyn> is not <T>`) — the runtime reads `dyn` from the
  scrutinee's `TypeInfo.name`.
- **Satisfaction** is the **distributed `(TypeInfo, IfaceId) → subvtable`
  registry** of standalone `SatEntry` globals (§2.2b ✅ DECISION), not a per-type
  array. The Phase-5 reader (`pkg/rt`) scans it (linear or hashed; entry counts are
  small); the search structure is informative.

### 1.1 Per-interface identity token (`IfaceId`) — the key sub-decision

An **interface** assertion `x.(*J)` must, at runtime, find "does T satisfy J?"
by looking up `(dynamic-type, J)` in the global `SatEntry` registry (§2.2b).
That requires a stable token identifying `J` that **both** the registry entries
(emitted at each `impl` site) **and** the
assertion site (which knows J statically) can reference.

**Proposal:** emit one static **`IfaceId`** symbol per interface program-wide
(weak linkage, deterministic mangled name, e.g. `bn_IfaceId.<mangled J>`). It can
be a zero-content 1-byte marker — only its *address* matters. The `SatEntry` for
`(T, J)` stores `&IfaceId(J)`; the assertion site references `&IfaceId(J)`
statically; the scan compares pointers. Cross-mode: the VM injects the native
`IfaceId` addresses by mangled name (same mechanism as vtable injection, §0.7),
so pointer-equality agrees. **⚠ Review-flagged (MAJOR) — there is no injection
channel today.** `registerVtableAddr` (`vtable_inject.bn`) is fed **exclusively**
from the reflect package descriptor's `Vtables` table (`p.Vtables`, via
`RegisterPackageVtables`). To inject `IfaceId`/`TypeInfo` addresses the **reflect
descriptor itself must gain TypeInfo + IfaceId tables** — a concrete, previously
unlisted work item spanning `reflect.bni`, all four `*_pkg_descriptor.bn` writers,
`BuildPackageDescriptor`, and the VM ingestion (`extern_register.bn` /
`vtable_inject.bn`). This is folded into the revised Phase 2 (§2f) and the risk
register (R4/R9).

Alternative considered and rejected: keying the table by the interface's mangled
*name string* and comparing by content — works but adds a strcmp per scan step
and a rodata blob per interface; the address-token is cheaper and matches the
self-describing-handle model the spec already uses for vtables/func-values. **Flag
for reviewer:** is a dedicated `IfaceId` symbol warranted, or should we reuse an
existing per-interface artifact (does one exist? — recon found none; interfaces
have no static descriptor today)?

---

## 2. Phasing (each phase leaves the tree green and is independently landable)

The phases are ordered so the highest-risk, most-mechanical change (any-block
growth) lands first with a **null** TypeInfo slot — provably inert — before any
code reads the slot. Then TypeInfo is populated, then the frontend/lowering for
assertions, then type switches.

| Phase | What | Reads the new slot? | Risk |
|-------|------|---------------------|------|
| 1 | Grow any-block to 2 words; TypeInfo slot = **null**; re-base methods — **✅ LANDED `0734beaa`** | no | HIGH (ABI) |
| 2 | Emit real `TypeInfo` records; fill the slot; `IfaceId` symbols | no (assert not built yet) | med |
| 3 | Parser + AST for `x.(K T)` and `switch x.(type)` | — | low |
| 4 | Checker: assertion + comma-ok + type-switch typing | — | med |
| 5 | IR-gen + backends + VM: assertion lowering, satisfaction lookup, panic | yes | med-high |
| 6 | IR-gen + backends + VM: type-switch lowering | yes | med |
| 7 | Full test matrix, spec status flip, docs | — | low |

---

## Phase 1 — Grow the vtable any-block to two words (null TypeInfo)

> **✅ LANDED 2026-07-04** — main `0734beaa`. Implemented exactly as planned (the
> 8 lockstep sites, atomic). Adversarially reviewed (four lenses): no correctness
> defect; the review's MINOR findings (stale layout doc-comments; x64 had no
> vtable byte-size unit golden, and no positional slot-1 null-TypeInfo golden for
> the managed-receiver case) were all folded into the landed commit. Verified:
> full unit suite + full conformance on builder-comp / -int / -comp / native-aa64
> (all 0 failed), hygiene 15/15. TypeInfo slot ships as a null placeholder;
> Phase 2 populates it.

**Goal:** every vtable becomes `[dtor, null, method0, method1, …]`; all dispatch
still works; nothing reads slot 1 yet. This is a pure ABI-shift commit. It is the
single riskiest change and must be atomic.

**Edit sites (all in one commit):**

1. `pkg/binate/ir/gen_iface_extends.bn` `IfaceFullVtableSize`: `var n int = 1`
   → `2` (0.1). Also update the doc comment's size formula (`1 (any-block: dtor
   slot)` → `2 (any-block: dtor + TypeInfo slots)`).
2. `pkg/binate/ir/gen_iface_dispatch.bn` `findInterfaceMethodFromBase`:
   `cursor := base + 1` → `base + 2` (0.2); update the layout doc comment ("slot
   0 = dtor; slots 1.." → "slot 0 = dtor, slot 1 = TypeInfo; slots 2..").
3. `pkg/binate/ir/gen_iface_extends.bn` `parentSlotOffsetFromBase`:
   `cursor := base + 1` → `base + 2` (0.2).
4. **Emitter LLVM** `pkg/binate/codegen/emit_impls.bn` `collectImplVtableSlots`:
   after the slot-0 dtor `appendSlotSym`, append a **null** slot for TypeInfo
   before parents/own-methods (0.3). Use the existing null-slot convention (the
   "defensive missing-method slot" already appends nulls — reuse it).
5. **Emitter x64** `pkg/binate/native/x64/x64_iface.bn`
   `collectImplVtableSlots_x64`: same null insert after the dtor slot.
6. **Emitter aarch64** `pkg/binate/native/aarch64/aarch64_iface.bn`
   `collectImplVtableSlotsNative`: same.
7. **Emitter VM** `pkg/binate/vm/lower.bn` `fillVtableLayout`: `slots[base] =
   dtorEntry`; add `slots[base+1] = nullEntry`; `cursor := base + 1` → `base + 2`.
8. **VM guard** `pkg/binate/vm/vm_exec.bn` BC_CALL_IFACE_METHOD: `slot < 1` →
   `slot < 2` (0.4).
9. `pkg/binate/ir/gen_iface.bn` `ensureAnyImplInfo` / `wrapAsIfaceValue`:
   **comment-only** (review-corrected — there is *no* `[1 x i8*]` literal to
   change). `any` is a real registered `ModuleInterface` (`registerUniverseAny`),
   so its vtable size flows through `IfaceFullVtableSize` and auto-grows `1`→`2`
   with step 1. The `[1 x i8*]` strings at `gen_iface.bn` are **doc comments**;
   update them to `[2 x i8*]` (dtor + null TypeInfo) but there is no code/layout
   edit here.
10. **Checker method-order doc** `pkg/binate/types/check_iface_extends.bn`
    `ifaceFullMethods`: update the layout comment `[any-block]…` to note the
    2-word any-block. (No logic change — it only orders methods, which are still
    contiguous after the any-block; the *slot math* is all IR-side.)
11. Audit slot-count consumers that auto-track `IfaceFullVtableSize`
    (`emit_impls.bn` `vtableSlotCount`/`vtableSlotCountForInfo`, `emit_instr.bn`
    OP_IFACE_VALUE `[N x i8*]` bitcast, the four `*_pkg_descriptor.bn` writers,
    `data_pkg_vtables.bn` SlotCount, `vm/vtable_inject.bn` slotCount). These
    should need **no manual edit** (they read the formula) — but confirm each
    still produces the right N after the `1→2` change.

**Verification for Phase 1 (must be exhaustive — this is the ABI-risk gate):**
- Unit tests for **every** changed package: `pkg/binate/ir`,
  `pkg/binate/codegen`, `pkg/binate/native/x64`, `pkg/binate/native/aarch64`,
  `pkg/binate/vm`, `pkg/binate/types` (smoke-test-every-package rule).
- **Enumerate the hardcoded-slot/size/byte test assertions from a repo-wide grep,
  not "any test"** (review-flagged MAJOR — under-scoping here lands "done-but-red"
  on the very packages Phase 1 must keep green). Build the list with
  `grep -rn 'IfaceFullVtableSize\|IfaceParentSlotOffset\|\[[0-9].* x i8\*\]\|relroSectionBytes\|ins.Index =\|Index ==' pkg/binate/**/*_test.bn`.
  At authoring time that set is **at least** these ~10 files, each with baked
  expected values that shift by +1 slot / +8 bytes:
  - `codegen/emit_impls_test.bn` (`[2 x i8*]`→`[3 x i8*]` etc.; RC vtable
    `{ptr,ptr,ptr,ptr}`→6 slots; `Closer {ptr,ptr}`→3).
  - `codegen/emit_iface_upcast_test.bn` (GEP `i64 1`→`2`, `i64 2`→`3`).
  - `codegen/emit_iface_call_test.bn` (method-dispatch offsets; dtor GEP `i64 0`
    stays 0).
  - `native/aarch64/aarch64_iface_vtable_test.bn` (`relroSectionBytes != 32`→48,
    `!= 96`→144 — hard byte counts).
  - `native/x64/x64_iface_test.bn` (`ins.Index = 1` + `8*Index` byte patterns).
  - `ir/gen_iface_dispatch_test.bn` (`Index == 1/2`, `closerSlot != 1`,
    `inheritedSlot != 2`, `ownSlot != 3`).
  - `ir/gen_iface_extends_test.bn` (`IfaceFullVtableSize != 2/2/6`→3/3/9;
    `IfaceParentSlotOffset` expectations per nesting level).
  All must be updated **in the same atomic Phase-1 commit**. Re-run the grep at
  implementation time (line-drift; new tests may have landed).
- Conformance across **all** backends and modes: `builder-comp` (native LLVM),
  `builder-comp-int` (VM), `builder-comp-comp` (gen2 self-compile), and the
  native-aarch64 cross mode. Any interface-dispatch conformance test exercises
  the re-based slots; a misdispatch shows as wrong output.
- Explicitly run an interface-**extension** conformance test (nested sub-vtables,
  upcast) — the `parentSlotOffsetFromBase` change is exercised only there.

**Green criterion:** all of the above pass with the TypeInfo slot present but
null. No behavior change is observable; the only diff is 8 bytes of null per
vtable and shifted method indices.

---

## Phase 2 — Emit `TypeInfo` records and populate the slot

**Goal:** every concrete type that can be boxed gets a static `TypeInfo`; the
vtable any-block slot 1 points at it; `IfaceId` symbols exist; satisfaction
tables are populated. Still nothing *reads* these (assertions not built), so the
tree stays green — this phase is validated by inspecting emitted data and by the
self-compile continuing to pass.

> **Implementation notes (2026-07-04, grounded recon) — architecture decisions
> for Phase 2, adopted:**
> - **Collect from the CHECKER's `c.Impls`, not IR-gen's flat `ImplInfo`.** The
>   `ir.ImplInfo` registry carries only name *strings* (`RecvPkg`/`RecvTypeName`/
>   `DtorFuncName`) — no `types.Type` — so `SizeOf`/`AlignOf`/`QualifiedTypeName`/
>   `NeedsDestruction` aren't computable there. The checker's `Impl` struct DOES
>   carry `RecvType @Type`. So collect one `TypeInfoDesc` per distinct receiver
>   type in `GenModule` (which receives the checker), store on `Module.TypeInfos`,
>   and have each backend emit them (mirrors how the reflect Vtables table is
>   collected once and emitted per-backend). Respects the IR/backend boundary:
>   layout (size/align) computed in the shared types layer, backends emit bytes.
> - **Identity = the record's OWN address** (no interior `identity` field) — a
>   concrete assertion compares `&bn_TypeInfo.<T>` pointers. Dodges the
>   unprecedented self-referential-weak-symbol hazard (§1 review finding).
> - **Symbol:** new `mangle.TypeInfoName(pkg, name)` mirroring `ImplVtableName`
>   (`__ivt.` → a `__typeinfo.`-style prefix over the lp-mangled per-type body);
>   generic instantiations get distinct symbols automatically (via `StructName`'s
>   `bn_T` path). Weak linkage (`DG_WEAK`) coalesces cross-module duplicates.
> - **`BuildTypeInfo` in `pkg/ir`** (new `data_typeinfo.bn`) mirrors
>   `BuildPackageDescriptor` (node global + name-rodata global; `DataSymref`/
>   `DataInt`/`DataBytes` terms in a fixed append order = byte order).
> - **`emitDataGlobal` does NOT auto-propagate:** each backend (LLVM/x64/aarch64)
>   has its own vtable driver + its own slot-1 placeholder; each must get the
>   emit pass + the slot-1 wire. arm32 is a no-op skeleton (skip). The VM consumes
>   the *native* vtable (reads the TypeInfo pointer through the native
>   relocation), so it needs NO change to carry the slot — only to *read* it,
>   which is Phase 5.
>
> **Increment breakdown (each self-contained + green; nothing reads the slot yet):**
> - **2.1 — ✅ LANDED 2026-07-04, main `041a6954`.** Scoped tighter than the
>   original bullet: emit the fixed 7-word record **all-zero/null** (identity =
>   the record's address), so it's fully **flat-registry / codegen-side** — no
>   `TypeInfoDesc`, no `Module.TypeInfos`, no `GenModule`/checker collection yet
>   (that was unnecessary for an all-zero record and would have risked a
>   symbol-vs-slot key mismatch; deferred to 2.2 where the checker fields are
>   filled). Delivered: `mangle.TypeInfoName`; `ir.BuildTypeInfo` +
>   `ir.CollectTypeInfoSyms` (new `data_typeinfo.bn`); emit + slot-1 wire in
>   LLVM/x64/aarch64 (native via `symPrefixed`); VM unchanged. Adversarially
>   reviewed (4 lenses, no defects; NIT + Phase-5 weak-def hazard folded in).
>   Verified: full unit + full conformance builder-comp/native-aa64 (2650 each) +
>   iface VM/gen2, hygiene 15/15.
> - **2.2** (split into 2.2a payload, 2.2b satisfaction — see grounding). Fill the
>   record from the **checker**, then emit the **distributed** satisfaction entries
>   (§2.2b ✅ DECISION — per-`(T,J)` globals, not a per-type table).
>
> **2.2 grounding (2026-07-04):**
> - **Resolution path.** `ir.Module.Checker` (`@types.Checker`, set in
>   `GeneratePackage`/`GenModule`) exposes `PackageType(pkgPath, name) @Type` and
>   `c.Impls @[]@Impl` (each `Impl` has `RecvType @Type`). So the payload
>   (`SizeOf`/`AlignOf`/`QualifiedTypeName`) is computable from a `types.Type`.
> - **THE symbol-match constraint (do NOT get wrong — a mismatch = link error, a
>   wrong size = SILENT miscompile).** The 2.1 record symbol + vtable slot ref
>   both key on the FLAT `(RecvPkg, RecvTypeName)` strings. The payload must be
>   attached to that SAME key, not to a separately-derived checker key (`t.Pkg`
>   may be a path where `RecvPkg` is a short name — they can differ). **Approach:**
>   enrich in IR-gen where BOTH are in hand — in/after `collectImplsFromDecl`,
>   resolve the flat receiver to its base value type (peel the receiver shape) and
>   record `{SizeOf, AlignOf, QualifiedTypeName, dtorSym}` onto a `Module`-side
>   `TypeInfoDesc` keyed by `mangle.TypeInfoName(RecvPkg, RecvTypeName)` — the
>   exact string the slot references. Backends look up the desc by that symbol
>   (respects the IR/backend boundary: size/align computed in the IR/types layer,
>   backends only emit bytes — do NOT call the checker from a backend).
> - **`dtor` is free of the resolution risk:** `ImplInfo.DtorFuncName` already
>   holds it (the same handle slot 0 uses) — TU-invariant, no `PackageType` call.
> - **TU-invariance (the Phase-5 weak-def hazard, now due):** `SizeOf`/`AlignOf`
>   are layout facts (target-parameterized, identical within a link);
>   `QualifiedTypeName` is canonical; the dtor symbol is deterministic. So the
>   fields ARE TU-invariant **iff computed on the canonical, alias-peeled base
>   type**. Compute them that way and weak-from-every-TU coalescing stays correct
>   (no need to switch to one-canonical-TU emission). Verify with a multi-TU test
>   (e.g. `378_iface_impl_dup`) that the filled records are byte-identical.
> - **2.2a — ✅ LANDED `8047a72c`.** Scoped to **size/align only** (words 1–2) —
>   pure ints, no cross-backend symbol plumbing, the fields most prone to silent
>   miscompile, done first via design A (`ImplInfo.RecvTyp` held, `SizeOf` read at
>   codegen — the fix for the size-0 blocker below). dtor/name (words 0, 3–4) ride
>   with 2.2b (they need cross-backend symbol handling like the name rodata). The
>   record stays reloc-free (no name/dtor pointers yet) → stays in `rodata`, not
>   `rodata_relro`. Adversarially reviewed (impl); byte-identical cross-TU verified.
>   **Remaining for 2.2b:** name rodata (word 3–4) + dtor (word 0) + the sat table.
>
> **⚠ 2.2a BLOCKER (2026-07-04) — the flat↔checker bridge is not resolvable by the
> two obvious routes; needs a design call.** Attempting to fill size/align, BOTH
> failed with `size=0` (a silent miscompile — reverted rather than landed):
> 1. **Compute at IR-gen impl-collection** (`collectImplsFromDecl` via
>    `resolveTypeExpr(gc, d.TypeRef)`, peel to base): runs during impl collection,
>    **before struct field layouts are populated**, so `SizeOf` reads an empty
>    struct → 0.
> 2. **Compute at codegen via `m.Checker.PackageType(RecvPkg, RecvTypeName)`**:
>    returns **nil for the current module's own types**. `Check(file)` (single-file
>    mode, used for `main`) `pushScope`/`popScope`s and **never `registerPackage`s
>    the current package into `c.Packages`**, so `lookupPackage` misses it.
>    (Multi-file `CheckPackage` does register — line 206-207 — so this is
>    inconsistent, and relying on it is fragile.)
> The tension: the vtable slot + record symbol key on the **flat** IR-gen strings
> `(RecvPkg = unquoted pkg path, RecvTypeName)`, but the fully-laid-out type lives
> on the **checker** side (`c.Impls[i].RecvType`, or the popped package scope), and
> there is no clean, always-available bridge between them at a point where the
> layout is final.
> **Candidate resolutions (user's design call — needs the checker's identity
> model):** (a) compute size/align in the **checker** from `c.Impls[i].RecvType`
> (native, laid-out) and stash it on the `Impl`/thread it to IR-gen, keyed to match
> the flat `ImplInfo` — the cleanest if the keying is clear; (b) make the current
> package resolvable (persist its scope / register it) so `PackageType` works
> uniformly — a checker change; (c) capture at a resolved IR-gen point that has the
> flat key (method-gen `genMethod`, or the box site `wrapAsIfaceValue` which has
> `val.Typ.Elem` resolved) and update the `ImplInfo` — covers boxed/method'd types,
> not never-boxed-in-module explicit impls. Recommend (a). **2.1 stays landed and
> correct; 2.2a is parked on this decision.**
> - **2.2b** = the remaining record fields (dtor + name + satisfaction table),
>   landing as sub-increments:
>   - **2.2b-1 — ✅ LANDED `9eba70eb`.** Word 0 destructor handle, filled from the
>     SAME helper the vtable any-block slot 0 uses (LLVM `implDtorSlotSym`;
>     newly-extracted native `dtorSlotSym_x64` / `dtorSlotSymNative`) so the
>     record's dtor word is byte-identical to that slot by construction.
>     `TypeInfoDesc` carries neutral `DtorFuncName` (per-type, from
>     `CollectTypeInfoDescs`) → each backend resolves the prefixed `DtorSym`. A
>     no-dtor type keeps a null word (reloc-free → `rodata`); a dtor type's
>     relocation moves the record to `rodata_relro`. Also split the native
>     TypeInfo-emission driver into new `<arch>_typeinfo.bn` (+ tests) — a home
>     for 2.2b-2/2.2b-3's growth, keeps `aarch64_iface.bn` under the length cap.
>     TU-invariance holds (the record is emitted only from TUs with a LOCAL impl
>     of T, where the dtor is a local def). Adversarially reviewed (correctness +
>     refactor-safety, each built + emitted-LLVM + mutation-tested; no defects).
>   - **2.2b-2 — ✅ LANDED `88e913af`.** Name (words 3–4): a TU-local rodata name
>     blob holding `RecvTyp.QualifiedTypeName()` (canonical/path-dotted, e.g.
>     `main.T`) + word-3 symref + word-4 length. `BuildTypeInfo → @[]@DataGlobal`
>     (`[record, name-blob]`, mirrors BuildPackageDescriptors); word 3 gated on
>     name presence (no dangling ref). `TypeInfoDesc` carries neutral `Name`/`NameSym`
>     (NameSym handled like Sym — native-prefixed); exported `types.QualifiedTypeName`;
>     added `mangle.TypeInfoNameBlobName`. **Consequence:** the name pointer is a
>     relocation, so EVERY named record now lands in `rodata_relro` (not just
>     dtor-bearing ones) — native section tests + the vtable-shape tests (their
>     `emitImplVtables` also emits the record) updated (+56 record). Adversarially
>     reviewed (6 lenses; clean, one accepted test-naming NIT).
>   - **2.2b-3** = satisfaction (the `IfaceId` weak symbols + entries). **⚠ The
>     per-type "words 5–6 table" framing below is SUPERSEDED by the ✅ DECISION at
>     the end of this block — satisfaction is DISTRIBUTED per-`(T,J)`, not a
>     per-type table. Read the recon as the trail to that decision.** (Original
>     framing: `IfaceId` weak symbols via `mangle.IfaceIdName`, one
>     `{iface_id, sub-vtable-ptr}` per interface in T's transitive set from the
>     `m.Impls` grouping, filling `sat_len`/`sat_table`.)
>
>     **⚠ 2.2b-3 RECON (2026-07-05, 5-investigator + synthesis workflow) — a
>     BLOCKER + resolved facts. AWAITING USER DECISION on the blocker.**
>     - **⚠ BLOCKER — the sat SET is not TU-invariant (silent wrong-code hazard).**
>       Every prior word (size/align/dtor/name) is a per-type TU-invariant fact, so
>       the weak `__typeinfo.<T>` records coalesce byte-identically. The sat table is
>       NOT: (1) `CollectTypeInfoDescs` walks `m.Impls` only, never `m.ImportedImpls`;
>       (2) `ensureAnyImplInfo` appends `(T, any)` lazily into the *boxing* module's
>       `m.Impls`. So module A (`impl T:Dog`, Dog:Animal) emits sat={Dog,Animal},
>       while module B (only `@any(t)`) emits sat={any} — two weak defs of
>       `__typeinfo.<T>`, linker picks one arbitrarily → a valid `t.(*Dog)` can fail.
>       **Root cause is fundamental:** Binate allows **cross-package impls** (no
>       orphan rule — plan-cross-package-interfaces.md §2), so NO single TU (not even
>       T's defining package) sees T's complete impl set. `weak_odr` duplicate-OK
>       fixes per-`(T,J)` *vtables* (byte-identical), but not a per-*type* aggregate.
>       **The completeness-contract fork (USER'S CALL — changes the record shape
>       and/or the Phase-5 reader and/or the language):**
>       (a) **orphan rule** for boxable/assertable types (all impls of T in T's pkg)
>       — but §2 explicitly rejected an orphan rule;
>       (b) **distributed per-`(T,J)` satisfaction entries** in a global collection,
>       each riding weak_odr with its `(T,J)` vtable (complete under cross-package
>       impls; the assertion scans (TypeInfo,IfaceId)→subvtable globally instead of a
>       per-type table) — arguably cleanest long-term, but drops the per-type
>       words-5/6 table shape;
>       (c) **per-TU-partial** per-type table + a Phase-5 slow-path fallback on miss;
>       (d) **canonical-TU** emission (Option A) — complete only for same-package
>       impls; silently incomplete for cross-package ones.
>     - **RESOLVED (approach-independent): sub-vtable pointer = the standalone
>       `__ivt.<T>__<J>` symbol at offset 0** — NOT the plan's earlier
>       `&(T's @__ivt) + IfaceParentSlotOffset*W`. `emitImplVtables` emits a distinct
>       `__ivt.<T>__<J>` for every row in `m.Impls` (incl. every transitive ancestor),
>       co-located with the record, and each begins with J's any-block at offset 0
>       (byte-identical to the offset target). The offset form is under-specified
>       across multiple hierarchies (no single top-level `@__ivt`; `IfaceParentSlotOffset`
>       returns −1 for a non-ancestor) and `IfaceParentSlotOffset` is really only the
>       `OP_IFACE_UPCAST` tool. This also kills the weak+nonzero-addend Mach-O concern.
>     - **RESOLVED: `mangle.IfaceIdName(pkg,name)` = `buf.Concat("__ifaceid.",
>       StructName(pkg,name))`** (mirrors `TypeInfoName`; reject the doc's illustrative
>       `bn_IfaceId.…` — not a real lp kind letter). One weak 1-byte rodata marker per
>       interface, enumerated from `m.Interfaces` (alias-filtered, deduped), emitted by
>       a module-level pass mirroring `emitTypeInfos` in all 3 backends (arm32 iface is
>       a stub — no change). Address-only identity; the Phase-5 assertion site references
>       the same symbol.
>     - **RESOLVED: transitive set** — `m.Impls` already holds one deduped, canonical,
>       alias-resolved row per `(T, listed-iface)` AND per `(T, transitive-ancestor)`
>       (via `IfaceAncestorClosure` + `moduleHasImpl`). Just partition `m.Impls` by
>       `(RecvPkg, RecvTypeName)`; extend the existing `CollectTypeInfoDescs` per-type
>       walk (don't add a parallel pass). Sort entries by IfaceIdName for byte-stable
>       weak coalescing (the `(T,any)` row appends in nondeterministic order).
>     - **RESOLVED: len-0 sat tables are possible** (the "≥1 row" floor is incidental)
>       — gate word 6 on `len>0` (null slot otherwise), mirroring the name-ptr gate.
>     - **Minor decision:** does `any` get an IfaceId + sat entry, or does Phase-5
>       `x.(*any)` special-case (trivially true)? Simplest: emit an `any` IfaceId
>       (harmless, weak, address-only). USER'S CALL, low-stakes.
>     - **Landable split:** Commit 1 = IfaceId symbols (inert markers, no readers —
>       UNBLOCKED); Commit 2 = sat array + words 5-6 (BLOCKED on the fork above).
>
>     **✅ DECISION (2026-07-05, user): PLAIN DISTRIBUTED — no per-type sat table.**
>     Satisfaction is represented by **distributed per-`(T,J)` `SatEntry` globals**,
>     NOT a per-type table in words 5-6. Rationale (spec-grounded): the spec allows
>     third-party impls (`iface.crosspkg.no-orphan`) and requires the assertion result
>     to reflect *every* interface T satisfies (`iface.rtti`, result normative /
>     layout informative); a per-type table can't be complete under separate
>     compilation AND needs the coalescing-union fix even for home impls. A per-`(T,J)`
>     entry is a per-pair fact — byte-identical weak_odr, exactly like `__ivt.<T>__<J>`
>     — so it captures third-party + `any` with NO TU-invariance blocker. This is Go's
>     itab model; one uniform mechanism, complete.
>     - **Each `impl T:J` (any package) emits `SatEntry{&TypeInfo(T), &IfaceId(J),
>       &__ivt.<T>__<J>}`** — weak_odr, keyed on `(T,J)`, one per `m.Impls` row
>       (incl. transitive ancestors + `(T,any)`), emitted alongside the vtables by
>       every TU with the impl visible; the linker keeps one. No canonical-emission
>       change; no coalescing surgery.
>     - **Record words 5-6 (sat_len/sat_table) — ✅ DROPPED `89ad8b18`.** The record
>       is now the fixed 5-word `[dtor, size, align, name-ptr, name-len]` (40 bytes at
>       LP64), matching the already-updated spec `type.layout.typeinfo` exactly. No
>       spec change needed (the spec was updated to the distributed model).
>     - **Retention (so the weak entries survive dead-strip) — OPEN, settle before the
>       retention slice:** a dedicated linker section (`__start_/__stop_` bounds;
>       cross-backend section work) vs. **extending the per-package reflect descriptor**
>       (reuses existing aggregation, is already the §2f cross-mode path). Leaning
>       reflect-descriptor. Emit-only slices (IfaceId, SatEntry) can land + be
>       emit-tested before this is decided (dead-strip is harmless while inert).
>     - **Phase-5 reader:** a global `(TypeInfo, IfaceId) → subvtable` lookup
>       (itab-like; linear or hashed in `pkg/rt`) + the assertion/type-switch lowering.
>     - **Re-scoped landable slices:** (3a) IfaceId symbols — **✅ LANDED
>       `a04ae1b8`** (`mangle.IfaceIdName`; `ir.BuildIfaceId`/`CollectIfaceIdSyms` in
>       data_ifaceid.bn; module-level emit pass in LLVM/x64/aarch64; weak 1-byte
>       rodata markers, `any` included, aliases skipped; adversarially reviewed —
>       identity-consistency verified across cross-pkg/alias/generic/any); (3b)
>       per-`(T,J)` SatEntry globals — **✅ LANDED `e12a0a0d`** (`mangle.SatEntryName`
>       reusing ImplVtableName's (T,J) core; `ir.BuildSatEntry`/`CollectSatEntries`
>       in data_satentry.bn; emit pass in LLVM/x64/aarch64; one weak
>       `{&TypeInfo,&IfaceId,&__ivt.<T,J>}` per m.Impls row incl. transitive
>       ancestors + `(T,any)`; also decoupled the native vtable-shape tests from the
>       RTTI satellites; adversarially reviewed — identity/completeness/sub-vtable/
>       TU-invariance verified across cross-pkg/alias/generic/deep-chain/multi-parent/
>       any/third-party, 0 dangling refs); (3c) retention = **✅ DECIDED (2026-07-05,
>       user): extend the
>       per-package REFLECT DESCRIPTOR** with a satisfaction-entries table (the
>       runtime aggregates across packages like it does vtables) — one mechanism for
>       native AND VM (the VM already ingests descriptors; sections don't exist
>       there), reusing the §2f/R9 cross-mode path; 3c does the descriptor writers
>       (reflect.bni + 4 `*_pkg_descriptor.bn` + BuildPackageDescriptor) + VM
>       ingestion. (Phase 5) reader = global `(TypeInfo,IfaceId)→subvtable` lookup +
>       assertion/type-switch lowering.
>       **3c recon + shape DECIDED (2026-07-06, user): managed `@SatEntryInfo` nodes,
>       land 3c-1 (emission) + 3c-2 (VM) both this pass.** The `__satentry.<T,J>`
>       global becomes the managed node itself: `{2-word static-managed header,
>       &TypeInfo, &IfaceId, &subvtable}` (5 words, still weak_odr / SatEntryName-keyed),
>       so it IS a `reflect.SatEntryInfo` (payload `{Type,Iface,Vtable *uint8}`) — data
>       inline, usable without copying, self-contained so the VM can lower its own nodes
>       (immortal-static compiled, regular-managed interpreted). Descriptor gains
>       `Package.SatEntries *[]@SatEntryInfo` (after Vtables) — a backing ptr array to
>       each `__satentry`+hdr, threaded through `BuildPackageDescriptors`; the 3 emitting
>       writers (LLVM/x64/aarch64) gather `CollectSatEntries`, arm32+VM pass empty.
>       Retention: `__Package` root → node → SatEntries array → `__satentry` nodes →
>       their referents. `BuildSatEntry` gains the header (STATIC_MANAGED_REFCOUNT).
>       3c-2: the VM's `RegisterPackageSatEntries` ingests `p.SatEntries` (inert until
>       the Phase-5 reader), mirroring RegisterPackageVtables.
> - **Deferred to Phase 5** (where the VM must *read* TypeInfo): the reflect-
>   descriptor extension + VM-side per-type identity materialization (revised
>   §2f).
> - **`any`-only-boxing: VERIFIED COVERED (not a gap).** The 2.1 adversarial
>   review (2026-07-04) confirmed the lazy `ensureAnyImplInfo` append lands in
>   `m.Impls` during IR-gen, which completes before codegen's `CollectTypeInfoSyms`
>   runs — and reproduced it by compiling+linking+running programs that box a
>   cross-package type and a primitive into `*any` with no explicit `impl`: each
>   emits a local weak `__typeinfo.<T>` for its slot-1 reference. No dangling ref.
> - **⚠ Phase-5 HAZARD (review-flagged, MINOR) — conflicting weak defs.** The
>   `__typeinfo.<T>` record is emitted weak from *every TU with a local impl of
>   `T`* (and every generic-inst site), relying on the linker to coalesce them to
>   one identity. Today that is safe because the record is **all-zero** — byte-
>   identical from every TU. But when 2.2/Phase 5 fills `size`/`align`/`name`/`dtor`
>   from each TU's **own checker**, two TUs that see `T` differently (via an alias
>   or a divergent import path) would emit **conflicting** weak defs; the linker
>   silently picks one, no diagnostic. Before filling the payload: either prove the
>   checker-derived fields are TU-invariant, or emit the record from exactly one
>   canonical TU (e.g. the type's defining package, like dtors). This is
>   real-but-latent — the all-zero record masks it now (multi-TU coalescing is
>   exercised green by `conformance/378_iface_impl_dup`).

**Step 2a — TypeInfo layout helpers in `pkg/binate/types`.**
Per the ir/backend guidelines, the record's *layout* is a language-level ABI
contract → add named field-offset helpers alongside `layout_offsets.bn`
(`TypeInfoIdentityOffset()`, `…DtorOffset()`, `…SizeOffset()`, etc.) and mirror
them in `types.bni`. **Decision to flag:** the record's *emission* (building the
`DataGlobal`) belongs in `pkg/binate/ir` (that is where `DataGlobal`,
`BuildImplVtable`, `BuildPackageDescriptor`, and dtor-symbol naming all live —
`pkg/binate/types` has no emission concept). So: **layout/offsets in
`pkg/types`, builder in `pkg/ir`.** This mildly stretches the high-level plan's
"define the TypeInfo record in pkg/types" — the *record contract* is in
pkg/types; the *DataGlobal builder* is in ir. (Reviewer: confirm this split is
acceptable, or argue for a builder in pkg/types with a callback for the
ir-resident dtor-symbol string.)

**Step 2b — `BuildTypeInfo` in `pkg/binate/ir` (new file `data_typeinfo.bn`).**
Model on `data_pkg_descriptor.bn` `BuildPackageDescriptor`. For a concrete type
`T`, emit a `DataGlobal` (weak linkage, deterministic symbol
`bn_TypeInfo.<mangle.StructName / instantiationMangledName>`):
- identity: `DataSymref(<self symbol>, 0)` (self-pointer).
- dtor: `DataSymref(dtorNameForType(T), 0)` if `T.NeedsDestruction()`, else
  `DataZero`.
- size/align: `DataInt(T.SizeOf())` / `DataInt(T.AlignOf())`.
- name: `DataSymref(<rodata blob of T.QualifiedTypeName()>, 0)` + a rodata
  builder (reuse `buildRodata` from `data_pkg_descriptor.bn`).
- sat table: build `SatEntry[]` as a sibling `DataGlobal` (backing array, like
  `buildPtrArray`), one entry per interface in T's transitive set (§0.8), each
  `{DataSymref(IfaceId(J)), DataSymref(<T's @__ivt symbol>, IfaceParentSlotOffset*W)}`.

**Step 2c — enumerate the types to emit.**
In the module-emission driver (where `emitImplVtables` walks `m.Impls`), derive
the **distinct `(RecvPkg, RecvTypeName)`** set from `m.Impls` (0.8). For each,
call `BuildTypeInfo`. Emit `TypeInfo` in the module where the type's dtor is
emitted (co-location); weak linkage handles generic instantiations emitted in
multiple modules (same coalescing as `@__ivt`/dtors already use — 0.7). Types
boxed only into `any` also appear via `ensureAnyImplInfo`'s lazy `ImplInfo`.
**⚠ Review caveat:** `ensureAnyImplInfo` appends the `(T, any)` `ImplInfo` to the
**boxing** module's `m.Impls` (lazily, during IR-gen at the box site) — which may
**not** be the module that emits T's dtor. On the native path this is benign
because enumeration runs in codegen (`EmitModule`), a strictly *later* pass than
IR-gen, so each module's `m.Impls` is complete before enumeration and the
weak-coalesced `TypeInfo` is emitted by *every* module that boxes T (linker keeps
one). But do **not** rely on strict "co-locate with the dtor" as the emission
rule — use **"emit `TypeInfo` from every module whose `m.Impls` names `(T, *)`,
weak"**, so an `any`-only box in a module that doesn't own T's dtor still emits a
(coalesced) `TypeInfo`. Explicitly confirm the **VM lower path** performs the same
`m.Impls` walk *after* `ensureAnyImplInfo` has run (ordering parity with codegen).

**Step 2d — `IfaceId` symbols.**
Emit one weak `IfaceId` `DataGlobal` per interface referenced (from
`m.Interfaces`). Deterministic symbol via a new `mangle.IfaceIdName(pkg, name)`
(add to `pkg/binate/mangle`). **BUILDER check:** `mangle` is in cmd/bnc's tree —
adding a plain function is fine (no new language feature), but confirm the
function is simple string-building within the subset.

**Step 2e — wire the slot.**
In each of the four vtable emitters (0.3), replace the **null** TypeInfo slot
from Phase 1 with `DataSymref(bn_TypeInfo.<T>, 0)`. Every **nested** sub-vtable's
any-block must carry the **leaf** type's TypeInfo (§7.13.8 / high-level risk
#3) — since `collectImplVtableSlots*` recurse per parent and each recursion emits
the any-block for the *same receiver `T`*, the leaf TypeInfo propagates
automatically as long as the emitter uses the top-level receiver's TypeInfo at
every nesting level (verify it doesn't accidentally use the parent interface's
identity).

**Step 2f — cross-mode: TypeInfo/IfaceId in the VM. ⚠ SUBSTANTIALLY REVISED per
review (MAJOR) — this was the plan's biggest hole.** The original "just inject the
native addresses" story covers **only** native-injected vtables and **fails for
the default `builder-comp-int` (bytecode) workload**, where the user's own program
is VM-lowered. Two facts the first draft missed:

1. **No injection channel exists.** `registerVtableAddr` is fed *exclusively* from
   the reflect package descriptor's `Vtables` table. Injecting TypeInfo/IfaceId
   addresses **requires extending the reflect descriptor** — a new work item
   (see §1.1) touching `reflect.bni`, all four `*_pkg_descriptor.bn` writers,
   `BuildPackageDescriptor`, and VM ingestion (`extern_register.bn` /
   `vtable_inject.bn`). This is not optional plumbing; without it the VM cannot
   resolve an assertion-site `&bn_TypeInfo.<T>` / `&IfaceId(J)` to the same token
   the injected `SatEntry` globals hold.
2. **VM-lowered impls store func *indices*, not addresses.** `fillVtableLayout`
   writes 1-based VM func indices into `IfaceVtable.Methods[]`; the VM has **no**
   mechanism today to materialize a static `TypeInfo` *record* into a stable
   address, and **no `BC_ADDR`-of-rodata path** for the assertion site to take
   `&bn_TypeInfo.<T>`. So "mirror `fillVtableLayout`" is not directly applicable —
   `fillVtableLayout` allocates no record objects.

**The spec (§7.13.14) actually permits the cleaner model the first draft
avoided:** "each engine may use its **own** native `TypeInfo` for a type and
compare by pointer-equality *within* its mode ... it is the boolean *result* that
must coincide, not a shared address." So the VM does **not** need the native
address — it needs *its own* per-type identity object plus a satisfaction lookup
that agree on the *result*. Concretely, the VM path is:
- At load/intern, build a VM-side per-type identity handle (an interned object,
  keyed by the type's `QualifiedTypeName()` — the VM already interns strings and
  builds per-impl `IfaceVtable`s in `lowerImplVtables`) and a VM-side satisfaction
  map (interface-identity → sub-vtable) derived from the **same** `m.Impls`
  grouping used natively (§0.8). The vtable any-block slot 1 (VM `IfaceVtable`)
  holds this handle.
- The assertion's concrete compare and `SatLookup` run against these VM handles;
  pointer-eq *within the VM* yields the same boolean as the native compare does
  natively. Cross-mode agreement is on the **result**, per spec — no shared
  address needed, so the injection channel (fact 1) is needed **only** for values
  that cross the boundary as native-injected vtables (native code handing an iface
  value to the VM), where the VM must map the native TypeInfo address back to its
  own handle. Design that boundary mapping explicitly.

**This elevates the cross-mode work from "open decision #5" to a first-class
Phase-2 deliverable with its own subtasks** (reflect-descriptor extension; VM
per-type identity + satisfaction map; the native↔VM boundary mapping). It is the
single largest correction from the adversarial review. Do **not** treat the VM
side as "injection suffices" — it does not for bytecode mode.

**Verification for Phase 2:**
- Self-compile chain green (`builder-comp`, `-int`, `-comp`): the records are
  emitted but unread, so any breakage means a malformed `DataGlobal` (bad symref,
  wrong linkage causing a duplicate-symbol link error, etc.).
- Add an IR-level unit test asserting `BuildTypeInfo` produces the expected
  DataTerm shape for a sample type (identity self-ref, dtor symref present/absent
  by `NeedsDestruction`, sat-table entry count = transitive interface count).
- A link smoke: build a program with a generic type instantiated in two modules;
  confirm no duplicate-`TypeInfo`-symbol link error (weak coalescing works).

---

## ⬛ UPDATE (2026-07-06) — Phases 3–7 re-grounded; gather/reader decisions made + adversarially reviewed

**This section SUPERSEDES the Phase 3–7 detail below** (dated 2026-07-03/04,
written before 3a/3b/3c landed and before the reflect-descriptor injection
channel existed). Where the older text conflicts (line-refs, R9 "no injection
channel", "`rt.SatLookup` is a mode-uniform pure-Binate helper", "concrete
assertion needs no helper"), **this update wins**. The data plane is fully
landed: 2-word any-block (Phase 1 `0734beaa`), `__typeinfo`/`__ifaceid`/
`__satentry` emission (2.x; 3a `a04ae1b8`; 3b `e12a0a0d`), reflect-descriptor
`SatEntries` retention (3c-1 `e14407dc`), VM ingestion (3c-2 `89108b34`). Only
the **reader + front-end** remain. Grounded by a 6-investigator recon and a
3-lens adversarial review (both 2026-07-06).

### U.1 What the interface lookup actually is

`x.(*J)` is Go's **itab lookup**: `(dynamic TypeInfo*, IfaceId(J)*) → subvtable`
or null. Two consequences: (1) the performant end-state is a **hash cache built
once at startup**, so the *gather* mechanism only decides how entries reach that
build — the perf ceiling is the same either way; (2) only **interface** targets
need the registry — a **concrete** `x.(T)` is a pointer compare of `vtable[1]`
against the static `&__typeinfo.<T>`, no enumeration.

### U.2 Resolved decisions (user, 2026-07-06)

- **(a) Native gather = compiler-synthesized root array** over each package's
  `_pkg_satentries` backing array (the 3c retention array), gathered from
  **`(ldr.Order deps) ∪ main`** (see must-do M1), emitted on the native main
  module and referenced from `__entry` so it is a live root. **Justify the array
  on SCAN-BOUNDING, not retention** — rooting `__Package` already gives dead-strip
  retention; the array's real job is to hand the reader a *bounded enumerable
  set*. Rejected: linker `__start_/__stop_` section (genuinely unavailable —
  `DataGlobal` has no `Section` field, `ir.bni:~854`; greenfield per-object-format
  boundary-symbol work). **Caveat (documented, not a blocker):** the root is
  *whole-program-at-invocation* — precompiled `--link-after-objs` objects carrying
  their own `__satentry`/`_pkg_satentries` are invisible to it. Latent today
  (`--link-after-objs` is only arm32-baremetal crt0, no impls); separate-
  compilation / library archives would need recompile-through-bnc or the section
  fallback.
- **(reader) = runtime-owned registry object** (native: an `rt` global built at
  startup into an itab-style hash from the root; VM: the existing per-`@VM`
  registry from 3c-2). The two readers are **symmetric only for native-carried /
  injected types**; VM-lowered user types need **d-i** (below). This is orthogonal
  to (a): it consumes whatever (a) produces at startup.
- **(b) Interface-value assembly = EXTEND `OP_IFACE_VALUE`** with a dynamic-vtable
  branch (empty static name ⇒ take the vtable from a register operand); VM gets a
  sibling `BC_IFACE_VALUE_DYN`. arm32 iface is stubbed → untouched. (Also add one
  small `OP_DATA_SYM_ADDR` to materialize `&__typeinfo.<T>` / `&__ifaceid.<J>`
  into a register — no existing primitive does this; `IsGlobalRef` re-mangles.)
- **(c) Cadence = MERGE Phase 4 checker + Phase 5 concrete lowering.** The
  `genExprInner`→`EmitConstInt(0)` / `genStmt`→drop fallbacks are *silent*
  (verified `gen_expr.bn:~164`, `gen_stmt.bn:~141`), so a checker that accepts
  assertions without lowering is a silent miscompile. Merge closes the window.
- **(d) VM identity = d-i, now FORCED by the review (must-do M2), not optional.**
  `lowerImplVtables` (`vm/lower.bn:~248`) must mint a per-VM interned type handle
  per impl receiver, write it into the null any-block slot `base+1`
  (`lower.bn:~270-277`), and `registerSatEntry` the VM-lowered `(T,J)` so
  `lookupSatEntry` resolves VM-lowered types through the *same* registry. The
  concrete-compare identity token then branches on `ifaceVtIsNative`.
- **(e) Reader split; `rt` stays reflect-free.** `SatLookup` is NOT one uniform
  function: native = the root scan/hash; VM = `lookupSatEntry(vm, typeAddr,
  ifaceAddr)` over the `@VM` slices (mirrors `lookupVtableAddr`). `rt` takes raw
  `*uint8`/`int` (matching `SatEntryInfo`'s `*uint8` fields) — no `reflect`
  dependency in Tier-0 `rt`. `rt` only needs `AssertFail`.
- **(f)** `AssertFail(dyn *[]readonly char, target *[]readonly char)`; the
  null-vtable / typed-nil miss uses the literal `"<unset>"` for `<dyn>`, pinned in
  the golden. Panic text (verbatim, `spec §17.5`): `runtime error: type assertion
  failed: <dyn> is not <T>`.

### U.3 Adversarial must-dos (GO-WITH-CAVEATS; fold into the slices)

- **M1 [CRITICAL] Gather = `ldr.Order ∪ main`, not `ldr.Order` alone.**
  `ldr.Order` is deps-only; the main module is built *after* the loop
  (`cmd/bnc/main.bn:~155-224`; `loader.bn:~426`), so as-written every `package
  main` impl / `@any` box is missed → spurious MISS (349/354/356/357 …). Mirror
  the existing `initPkgNames` precedent (`main.bn:~188` already appends main's
  `__init` separately). Applies at **all three driver sites** (main build,
  `test.bn` runner's synthetic main, and — see M5 — NOT interp).
- **M2 [CRITICAL, rides with decision d-i] VM-lowered user types are unresolvable
  without d-i.** Null slot-1 key + zero registered satentries → `builder-comp-int`
  says MISS where native says HIT (cross-mode violation). d-i is a hard
  prerequisite of the reader (see decision d). Do not call the readers "symmetric"
  until d-i lands.
- **M3 [CRITICAL] Registry fill = the FIRST statement of `__entry`, strictly
  before `__init_all()`.** Assertions can run in top-level var initializers, which
  execute *inside* `__init_all` (`gen_init.bn:~181-199,279-280`). `EmitMainEntry`
  must prepend the rt registry-builder ahead of the `__init_all()` call. Pin with
  a conformance test that asserts in a top-level var initializer.
- **M4 [MAJOR] Extern-DATA-declaration step (the `EmitInitDispatcher` precedent
  does NOT fully transfer).** `EmitInitDispatcher` declares extern *functions*
  (`NewExternFunc`); the root references cross-TU *data* symbols and **no existing
  pass auto-declares an undefined data symref** → "undefined value" (LLVM,
  `emit_data_global.bn:~134`) / "undefined symbol" at `Finalize` (native,
  `asm.bn:~385`). Add a distinct step: per gathered dep, emit an `IsExtern`
  `ir.Global` (drives LLVM `external global`) + seed the native symbol via
  `SetWeak`/`SetGlobal` (`asm.bn:~309`) before the root symrefs it.
- **M5 [MAJOR] Native-codegen-only gating.** `EmitMainEntry`/`EmitInitDispatcher`
  are called from `main.bn`, `test.bn`, AND `interp.bn:~175` (which then lowers
  main to bytecode). A native `{ptr,len}[]` root symref-ing native
  `_pkg_satentries` is meaningless/erroring in the VM path. Scope the gather+emit
  to the native-codegen drivers only; do NOT bury it in the shared
  `EmitMainEntry`.
- **Caveat C1 (minor):** make the root reference **unconditional** (never gated on
  "this TU has local impls"), and add a hygiene/unit assertion that every
  `ldr.Order∪main` package with non-empty `CollectSatEntries` appears in the root
  list — guards a future "skip when empty" shortcut from silently dropping
  cross-package impls.

**Survived clean (load-bearing claims that held):** registry lifetime/refcount is
zero-obligation (`__satentry` nodes carry `STATIC_MANAGED_REFCOUNT`; refcount ops
gate on `slt 0`, `emit_refcount.bn:~36`, so copying the immortal pointers into a
hash cannot leak/dangle); `(T,any)` lazy-append, cross-package impls, transitive-
only leaf deps, and generic-instantiation weak coalescing all gather ≥1×, never 0;
dead-strip retention holds via the live root; cross-mode agreement is sound for
injected/native-carried types (`ifaceVtIsNative` discriminates by pointer range).

### U.4 Slice breakdown (ordered, independently landable; dependency-correct)

Each slice leaves the tree green and follows the standard landing procedure
(per-round approval). **BUILDER: GO, no bump** — `cmd/bnc`'s own source has zero
`.(` assert syntax (only comment prose); re-run that grep before each
BUILDER-sensitive land.

- **Slice 1 — Parser + AST (Phase 3) — ✅ LANDED (2026-07-06, main
  `ebddfc38`..`46448e0c`, a 5-commit stack).** *BUILDER-sensitive.*
  `EXPR_TYPE_ASSERT`, `STMT_TYPE_SWITCH`, a dedicated `parseAssertTarget` (NOT
  `parseType` — the leading `*`/`@` is a recovery kind, not a constructor;
  produces `*T`=TEXPR_POINTER(named), `@T`=TEXPR_MANAGED(named), value=named,
  `readonly`=TEXPR_CONST), a restructured `.(` disambiguation across BOTH the
  primary parser (parseIdentOrCompositeLit no longer eats `.` before `(`) and the
  postfix loop (`.(type)` terminates the loop as a type-switch head;
  `.(AssertTarget)` builds the assertion), new `Stmt.Binder` + `CaseClause.Types`
  fields, and a new `parse_assert.bn`. Conforms to `binate.ebnf` (`AssertTarget`/
  `AssertTargetList`/type-switch alt + D13). PARSE-ONLY: the checker rejects both
  new nodes with an interim "not yet supported" diagnostic (removed when Slice
  4/6 add real checking) — added because `checkStmt` has no default arm and a
  `STMT_TYPE_SWITCH` would otherwise silently no-op. Adversarially reviewed (GO;
  27 accept/reject/disambiguation probes, 0 regressions). Two length-driven
  extractions rode along (composite-lit helpers → `parse_composite.bn`;
  pending-decl error helpers → `check_pending.bn`) to keep `parse_expr.bn`/
  `check_expr.bn` under the soft cap. Deps: none.
- **Slice 2 — New IR ops — ✅ LANDED (2026-07-06, main `8db770c6` (2a) +
  `1685d590` (2b)).** `OP_DATA_SYM_ADDR` (materialize a LOCAL weak data-global's
  address — `&__typeinfo.<T>`/`&__ifaceid.<J>` — into a register; models the
  `OP_IFACE_VALUE` vtable-address arm with plain local addressing, NOT
  OP_C_GLOBAL's GOT-indirect load) + `OP_IFACE_VALUE` dynamic-vtable extension
  (empty `StrVal` ⇒ vtable from `Args[1]` register; new `EmitIfaceValueDyn`).
  Mechanism only — no lowering emits either yet. **Scope corrections (user calls,
  2026-07-06):** (1) arm32 is NOT a stub — its iface lowering is fully
  implemented, so it is a **FOURTH** native site (LLVM/x64/aarch64/arm32), and
  both ops were wired there. (2) **VM deferred to Slice 5** (native-only): the VM
  resolves vtables by name→index and has no data-symbol address model, so
  OP_DATA_SYM_ADDR rides the VM lowerer's loud-fail default and dynamic
  OP_IFACE_VALUE gets an explicit VM loud-fail (`BC_IFACE_VALUE_DYN` + the VM
  identity model land in Slice 5, per decision d-i). (3) **M4 extern-data-decl
  deferred to Slice 3** — bare references to the weak-emitted symbols link fine
  (the existing OP_IFACE_VALUE vtable path proves it), so M4 belongs where the
  cross-TU root actually needs it. The LLVM OP_IFACE_VALUE lowering was extracted
  to `emitIfaceValueLLVM` in `emit_iface_call.bn` (matching the sibling iface-op
  delegation; keeps `emit_instr.bn` under cap). Adversarially reviewed — the
  landed static OP_IFACE_VALUE path (every interface value) is proven functionally
  unchanged (iface conformance builder-comp 46/46, VM 11/11, native-aa64 11/11).
  Follow-up (✅ LANDED `207d0410`): the native dynamic iface tests now decode the
  data-vs-vtable store OFFSET and pin data→`IfaceValueDataOffset()`,
  vtable→`IfaceValueVtableOffset()` (source-correlated by emission order,
  fault-injection-validated on x64/aarch64/arm32) — closing a pre-existing
  test-rigor gap where byte/store counts couldn't catch a slot swap. Deps: none
  (∥ Slice 1).
- **Slice 3 — Native SatEntry root (inert) — ✅ LANDED (2026-07-07, main
  `5cfc6dee`).** `ir.BuildSatEntryRoot` (a `pairs` array of
  `{&_pkg_satentries,count}` + a `{&pairs,N}` raw-slice header) + Module method
  `EmitSatEntryRoot` (stashes the gather; injects an OP_DATA_SYM_ADDR reference
  to the root into `__entry`). Gather **`ldr.Order ∪ main`** (**M1**) at the
  native drivers only (**M5** — `cmd/bnc` main + `--test`, never `interp.bn`);
  extern-data decls (**M4** — LLVM `external global` per cross-object dependency
  `_pkg_satentries`; native `SetGlobal`-seed); **unconditional** root (**C1** —
  empty `{null,0}` when no impls). Retention is *dual*: the LLVM `__entry`
  OP_DATA_SYM_ADDR lowers to a no-op `bitcast i8* @root to i8*` that LLVM folds
  away, so LLVM pins the root in **`@llvm.used`** (the portable retain
  primitive); the native `__entry` reference is a real LEA/ADRP reloc that
  retains directly. Adversarial review (self-run + isolation, after the
  independent agents were rate-limited) caught a **latent native-arm32 bug this
  exposed**: `arm32_pkg_descriptor.bn` passed `noSatEntries` on a stale premise
  ("arm32 emits no `__satentry` / panics on impls") — both false; it emits the
  nodes but never defined the `_pkg_satentries` array, so the root's cross-object
  reference to a native-arm32 program's own `_pkg_satentries` was an undefined
  symbol at emit. Fixed with `collectSatEntriesArm32` mirroring x64/aa64 (folded
  into this commit). Verified: `builder-comp-comp` 2689/0, native-aa64 214/0,
  LLVM-arm32 213/0, native-arm32 177 pass (36 remaining fails are pre-existing P4
  backend gaps, confirmed via the green LLVM-arm32 run), `e2e/satentry-retention.sh`
  (nm-asserts root + own + cross-package-dependency `__satentry` survive
  dead-strip on both backends), separate-compilation e2e green. Deps: Slice 2.
  BUILDER-GO. **Note:** retention is now an unconditional per-binary cost (every
  program retains its dependency closure's RTTI, e.g. `builtins/lang`'s ~52
  satentry+typeinfo+ivt chains) — acceptable per (a)+C1, but a future pass could
  gate the root on actual assertion use once Slice 4/6 land.
- **Slice 4 — Checker + concrete-assertion lowering (Phases 4+5 merged, M-c).**
  `EXPR_TYPE_ASSERT` typing (interface operand check via the `present()` pattern;
  target via `resolveTypeExprAllowInterface` — plain `resolveTypeExpr` rejects a
  bare interface name; the §11.12 kind table — reject `@T` from raw `*I`, enforce
  readonly add-not-drop) + concrete lowering (`vtable[1]` load,
  `OP_DATA_SYM_ADDR(&__typeinfo.<T>)`, `OP_EQ`, branch, recovery refcount) +
  `AssertFail` in **both** `rt.bn` and `rt_baremetal.bn` + expression-form panic +
  comma-ok (new checker wiring — `hasExpandableResults` accepts only func kinds;
  synthesize a `{recovered,bool}` struct shaped for `genMultiAssign`'s
  `TYP_STRUCT` path). Green: checker accept/reject unit tests; panic golden;
  refcount goldens (@-hit=+1 then release, *-borrow=no churn) under `-comp` AND
  `-int`. Deps: 1, 2, 3. BUILDER-GO.
  **Scope narrowed (user, 2026-07-09, after recon):** Slice 4 is **compiled-mode
  concrete assertions only.** Two boundaries, both because they genuinely belong
  to Slice 5's d-i work: (1) **VM path deferred to Slice 5** — the concrete
  compare is `iface.vtable[1] == &__typeinfo.<T>`, but VM `vtable[1]` is *null*
  until Slice 5's d-i mints interned type handles there (decision d-i / M2), so
  the `-int` refcount/panic goldens move to Slice 5. `OP_DATA_SYM_ADDR` is
  already VM-loud-fail (Slice 2), so no silent miscompile in the interim. (2)
  **Interface targets `x.(*J)` interim-rejected** (checker types concrete targets
  fully; a bare-interface target gets a clear "not yet supported (Slice 5)"
  diagnostic — the Slice-1 pattern), since interface-target recovery IS the
  Slice-5 reader. Sub-slices: **4a** expression-form concrete assertion (checker
  + lowering + `AssertFail` + panic); **4b** comma-ok. The `vtable[1]` (`*TypeInfo`)
  load reuses `EmitGetElemPtr`+`EmitLoad` (no new IR op — the vtable ptr GEP'd by
  word index 1); `AssertFail` is a plain `EmitCall("pkg/builtins/rt.AssertFail")`
  (no new op / backend arm).
  - **4a — ✅ LANDED (2026-07-10, main `6c512002`).** Expression-form concrete
    assertions `x.(*T)` / `x.(@T)`, compiled-mode. Checker (`check_assert.bn`):
    operand must be an interface value (`comparabilityKind`), peels the `*T`/`@T`
    (+ readonly) target wrapper, resolves via `resolveTypeExprAllowInterface`,
    interim-rejects interface targets ("interface-target type assertion not yet
    supported") and value-recovery `x.(T)` ("value-recovery … not yet supported"),
    rejects `@T` from a raw `*I`. Lowering (`gen_assert.bn`): extract `vtable`
    (iface idx 1), null-check → unset panic; else load `vtable[1]` (`*TypeInfo`),
    `OP_DATA_SYM_ADDR(&__typeinfo.<T>)` (its first real consumer), `OP_NE` →
    wrong-type panic vs hit; hit recovers the pointer (`@T` RefInc+registerTemp,
    `*T` borrow — no churn). Panic text per §17.5. `rt.AssertFail` added to
    `rt.bn` + `rt_baremetal.bn` + `rt.bni`. **Cross-package fix (MAJOR, caught in
    self-review):** `emitDataSymAddrDeclares` emits `external global i8` for
    OP_DATA_SYM_ADDR symbols not defined in-module (`collectDefinedDataSyms`),
    else clang rejects the bitcast on a cross-TU `&__typeinfo.<T>`. Conformance
    998 (HIT `*T`/`@T`), 999 (miss), 1000 (unset), 1001 (cross-pkg HIT); all
    xfail'd on the three `-int` modes (VM lacks `OP_DATA_SYM_ADDR` until Slice 5).
    Unit tests: checker accept/reject, refcount golden, cross-TU/in-module
    declare split.
  - **4b — ✅ LANDED (2026-07-10, main `81e2104e`).** Comma-ok form
    `v, ok := x.(*T)` / `x.(@T)` (and the `=` form): a HIT yields (recovered,
    true), a wrong-type or unset-vtable MISS yields (nil, false) without
    aborting. Checker (`check_assign.bn`, split out of `check_stmt.bn`): a
    2-target assign/short-var with a type-assert RHS binds (recovered, bool),
    reusing `checkTypeAssert`'s validation. Lowering (`gen_assert_commaok.bn`,
    `genTypeAssertCommaOk`): synthesizes the `{recovered, ok}`
    makeMultiReturnStructType via the **alloca-merge idiom** (branch on
    null-vtable + type-compare, store {recovered,true} on hit / {nil,false} on
    both miss paths, load at the merge) — NOT `EmitStructLit`, which is
    VM-only (OP_STRUCT_LIT/OP_PHI are unlowered in the LLVM/native backends).
    **Borrow model**: no RefInc in the hit block; the existing multi-return
    destructure (genShortVar / genMultiAssign, routed via `genMultiValueSource`)
    copy-RefInc's the target — registering an owned temp would RefDec a garbage
    pointer on the miss path. Conformance 1002 (HIT *T/@T, wrong + unset MISS,
    `=`/`:=`, blank targets), same three `-int` xfails. Unit tests: checker
    accept/reject + refcount golden (borrow: *T churn-free, @T acquires only
    via the destructure). **Follow-up filed:** `bnfmt` drops type-assertion
    expressions (MAJOR, `claude-todo.md`) — print_expr.bn has no
    EXPR_TYPE_ASSERT case; to fix next.
- **Slice 5 — Interface-target reader (SatLookup split; decisions d-i + e).**
  Native: build the startup registry/hash (**M3** — fill first in `__entry`) +
  the hash reader over the Slice-3 root; VM: `lookupSatEntry` over the `@VM`
  registry + **d-i** (**M2** — `lowerImplVtables` mints interned handles + registers
  VM-lowered satEntries into the null slot); assemble the result iface value via
  the dynamic `OP_IFACE_VALUE`; assertion site branches on `ifaceVtIsNative`.
  Green: interface-target hit/miss over injected AND user-defined types, `-comp`
  vs `-int` identical boolean. Deps: 4.
  - **5a — ✅ LANDED (2026-07-10, main `2e566227`).** NATIVE/compiled-mode
    interface-target assertions (expr + comma-ok). rt itab-hash reader
    (`rt_satregistry.bn`, a new UNCONDITIONAL rt file — reflect-free raw
    `*uint8`; `BuildSatRegistry` walks `_satentry_root`, `SatLookup` queries the
    open-addressing hash). M3 fill prepended to `__entry` before `__init_all`
    (data_satroot.bn, native-gated). **rt registry globals MUST be static-zero
    (no `= nil`)** — else rt's package `__init` re-zeros the just-built table
    (found the hard way). Checker un-rejects interface targets (`*J`→
    MakeInterfaceValueType, `@J`→MakeManagedInterfaceValueType; reject
    `readonly J`). Lowering `gen_assert_iface.bn` (genInterfaceAssert +
    genInterfaceAssertCommaOk): `loadVtableSlot(1)`→dyn `*TypeInfo`,
    `EmitDataSymAddr(&__ifaceid.<J>)`, plain `EmitCall(rt.SatLookup)` (NOT a
    lowered op — the VM mechanism is a 5b decision), branch, `EmitIfaceValueDyn`,
    `@J` RefInc / `*J` borrow. **`collectDefinedDataSyms` += `__ifaceid` syms**
    (first `OP_DATA_SYM_ADDR(&__ifaceid)` consumer; else LLVM redefinition).
    Conformance 1013 (hit *J/@J, comma-ok hit/miss/@J-managed/unset), 1014 (miss
    panic), 1015 (ancestor/transitive), xfail'd on 3 -int modes. Unit tests:
    checker, itab-hash (empty/hit/miss), refcount golden.
    - **5a follow-ups — ✅ LANDED (2026-07-10, main `2b7a8146`).** 1024
      (cross-package interface target `s.(*shp.Sized)` — validates the short-name
      `__ifaceid` symbol agreement across packages), 1025 (`@J` retain
      refcount-balance via rt.Refcount). Both xfail'd on -int.
    - **5a leak fix — ✅ LANDED (2026-07-10, main `2f88b262`).** MAJOR (found by
      adversarial review): `recoverInterfaceValue` registered the raw `*uint8`
      `data` as the end-of-statement temp, but cleanup dispatches by type and a
      raw pointer matches no managed predicate — a silent no-op, so a DISCARDED
      `@J` recovery (`_ = x.(@J)`) leaked one ref (assign/return/arg balanced, so
      the assign-only tests missed it). Fix: register the fresh `@J`-typed result
      iface value instead. Regression test 1026 (discard refcount-balance).
  - **5b-1 (concrete VM) — ✅ LANDED (2026-07-10, main `380e40f5`).** Concrete
    `x.(*T)`/`x.(@T)` (expr + comma-ok) run in the bytecode VM; removes the -int
    xfails on 998-1002. The slot-1 read fix is the recon-recommended **op**
    (user-confirmed over the layout change AND over a call+intercept — the latter
    rejected as a hack): new `OP_IFACE_TYPEINFO` reads the dynamic `*TypeInfo`
    from vtable slot 1, replacing the `loadVtableSlot` GEP+LOAD in
    `gen_assert*.bn`. It takes the iface VALUE (extracts the vtable word like
    `OP_IFACE_DTOR`); native (LLVM/x64/aa64/arm32) lower it to the inline slot-1
    GEP+LOAD they already emitted (arm32's `emitIfaceTypeInfo` lives in
    `arm32_dispatch.bn`); the VM lowers it to `BC_IFACE_TYPEINFO` branching on
    `ifaceVtIsNative` (VM word → `IfaceVtables[idx-1].Methods[1]`). d-i is REAL
    records, not tokens: `materializeTypeInfos` (`lower_typeinfo.bn`) lays the
    `irdata.BuildTypeInfo` blobs via the existing `lowerDataGlobals` into a
    session-lifetime data-symbol table (`vm.dataSymNames`/`dataSymAddrs`), run in
    `LowerModule` before `lowerImplVtables`; `fillVtableLayout` writes each
    receiver's record addr into slot `base+1`; `BC_DATA_SYM_ADDR` resolves
    `&__typeinfo.<T>` against the table at exec. Real records so a wrong-type miss
    reads the dynamic type's NAME (test 999). Rationale for the op (not the layout
    change): the fnptr migration DELIBERATELY kept `IfaceVtable.Methods`=idx+1
    ("per-vm dispatch tables; the indices never leave the vm",
    plan-uniform-native-fnptrs L120), so the index is by-design and the op reuses
    the same `ifaceVtIsNative` discrimination method dispatch already does.
    Adversarially reviewed (native/IR clean; VM d-i found 1 MAJOR: a
    native-INJECTED iface value concrete-asserted in the VM would silently MISS —
    native `__typeinfo` addr ≠ VM record — now **loud-fails** at
    `BC_IFACE_TYPEINFO`, the real cross-mode mapping deferred to 5b-2). Unit
    tests: `lookupDataSymAddr`, `materializeTypeInfos`, `TestLowerEmitsBc
    IfaceTypeInfo`. Follow-up owed: `vm_exec_iface.bn` split (grew over the soft
    length limit).
  - **5b-2 (interface VM) — PENDING.** VM interface-target path: `lookupSatEntry`
    (re-add — DROPPED from the 5a increment, ~15 lines mirroring
    `lookupVtableAddr`); register VM-lowered `(T,J)` in `lowerImplVtables`;
    `BC_IFACE_VALUE_DYN`; the VM SatLookup mechanism (route the `rt.SatLookup`
    call → `lookupSatEntry`, OR introduce `OP_SAT_LOOKUP` — decide in 5b-2); and
    the **cross-mode boundary mapping** (feed a native-injected package's
    `__typeinfo`/`__ifaceid` addresses into the VM data-symbol table under their
    symbols, so both the injected slot-1 read and `BC_DATA_SYM_ADDR` resolve to
    one address — this also lifts 5b-1's native-injected loud-fail). Removes the
    remaining 18 -int xfails (1013-1015 + 1024-1026 × 3 modes).
- **Slice 6 — Type-switch (Phase 6).** `checkTypeSwitchStmt` (modeled on
  `checkSwitchStmt`; per-case narrowing; multi-target/`default` bind scrutinee
  type; no exhaustiveness/dup/fallthrough) + `genTypeSwitch` (first-match chain
  over the Slice-5 primitives; **push each `@`-binder into `ctx.Vars` as a typed
  managed slot** so case-scope cleanup RefDecs it — the subtlest leak risk).
  Green: type-switch goldens incl. a `@`-binder leak test, both modes. Deps: 4, 5.
- **Slice 7 — Spec flips + docs (Phase 7).** *Explorations/docs; commit promptly.*
  Flip the `§11.12`/`§17.5`/`§7.13.14`/`§13.8`/`§14.10` Draft banners + `00-index`
  rows once conformance is green; keep the panic-text byte-identical to
  `AssertFail`. Move the item to the done log. Deps: 4–6 landed.

### U.5 Still-open (small spikes during implementation, not blockers)

- Native reader shape once the root exists: a pure-`rt` scan over the root vs. a
  lowered op (like `OP_BOUNDS_CHECK`) — settle in Slice 5 (couples to whether any
  single call path must be VM-aware; recon leans split-reader).
- VM-lowered interned-handle key stability across load order / generic
  instantiations (`List[int]` vs `List[float]` per-instantiation identity) —
  confirm in Slice 5.
- The comma-ok synthesized `{recovered,bool}` must match the exact `_0/_1`
  field naming `genMultiAssign` expects (`makeMultiReturnStructType`,
  `gen_func.bn:~17`) — a targeted unit test, not just conformance.

### U.6 Slice 5 approach — settled (2026-07-10, user-confirmed; 6-investigator recon)

**Scope reducer confirmed by recon:** the dynamic-vtable IR construct
(`EmitIfaceValueDyn`) + `OP_DATA_SYM_ADDR` already landed in Slice 2 across all
four native backends; the impl registry already carries the transitive-ancestor
closure (so `x.(*Ancestor)` HITs for free); `recoverPointer` is reused verbatim
for `@J`/`*J`. Slice 5 is the READER + checker + VM-identity, not new IR
primitives.

**User decisions (2026-07-10):**
- **Sequencing = 5a native, then 5b VM d-i.** 5a lands the native reader with
  new interface tests xfail'd on the three `-int` modes (like 4a/4b); 5b's d-i
  sweep clears ALL `-int` xfails (998–1002 concrete + the new interface tests).
- **Native reader = a startup-built itab hash (M3 now)**, not a deferred linear
  scan. Built from `_satentry_root` as `__entry`'s first statement, before
  `__init_all` (assertions can run in top-level var inits).
- **`readonly *J` / `readonly @J` = reject at check time** (an interface value has
  no inner readonly slot, spec §11.12 iface.value.no-readonly-slot). Concrete
  targets keep their existing element-readonly handling.

**Reader mechanism = a split lowered op `OP_SAT_LOOKUP(ti, ifaceid) → subvtable`,
modeled on `OP_BOUNDS_CHECK`.** Native lowers it to `call rt.SatLookup(ti,
ifaceid)` (queries the native itab-hash global). VM (5b) lowers it to
`BC_SAT_LOOKUP` whose exec calls the VM's own `lookupSatEntry(vm, ti, ifaceid)`
over `@VM` state — NOT `rt.SatLookup` (the two registries differ by mode; the
op is the mode-dispatch point). In 5a the VM loud-fails `OP_SAT_LOOKUP` (like it
loud-fails `OP_DATA_SYM_ADDR`), so interface conformance tests xfail on `-int`
until 5b.

**5a work items:** (1) rt itab-hash global + builder (walks `_satentry_root`) +
`rt.SatLookup` reader (reflect-free, raw `*uint8`/int) in rt.bn + rt_baremetal.bn
+ rt.bni; (2) M3 wiring — prepend the hash-build into `__entry` ahead of
`__init_all` (in `EmitSatEntryRoot`/data_satroot.bn, native-gated, NOT the shared
`EmitMainEntry` — M5); (3) `OP_SAT_LOOKUP` op (iropcode + `EmitSatLookup`) +
native/LLVM lowering to `call rt.SatLookup` + VM loud-fail; (4) checker un-reject
in check_assert.bn (`*J`→`MakeInterfaceValueType`, `@J`→`MakeManagedInterface
ValueType`, mirror resolve_type.bn; reject `readonly J`; @J-from-*I already
covered); (5) `gen_assert_iface.bn` interface-target lowering (expr + comma-ok):
vtable-null check → `loadVtableSlot(vtable,1)`=dynamic `*TypeInfo` → `EmitData
SymAddr(IfaceIdName(J))` → `OP_SAT_LOOKUP` → null?→miss : `EmitIfaceValueDyn(data,
subvtable)` + `recoverPointer`; (6) conformance tests (iface HIT/MISS/xpkg/
ancestor/comma-ok), xfail'd on `-int`.

**5b work items (VM d-i, M2):** intern per-VM type handles into the null vtable
slot-1 (`fillVtableLayout`); `BC_DATA_SYM_ADDR` (OP_DATA_SYM_ADDR → interned
handle); the slot-1 read fix — the VM `iv[1]` is a 1-based INDEX not a pointer,
so the shared assertion IR's GEP+LOAD derefs a small int → garbage; recon
recommends a `BC_IFACE_TYPEINFO` op branching on `ifaceVtIsNative` (VM iv →
`IfaceVtables[iv[1]-1].Methods[1]`; native iv → native `@__ivt` slot-1) over
changing the iface-value memory layout; `lookupSatEntry`; register VM-lowered
`(T,J)`; `BC_IFACE_VALUE_DYN`; `BC_SAT_LOOKUP`. **Cross-mode boundary trap
(likely MAJOR-bug point):** a native-INJECTED iface value in the VM carries a
native `&__typeinfo` in slot-1 that won't equal the VM synthetic handle unless
the interner is fed the native address under its `__typeinfo.<T>` symbol — design
explicitly. Remove all 15 `-int` xfails (998–1002 × 3 modes) once green.

---

## Phase 3 — Parser + AST for assertions and type switches

**Goal:** parse `x.(K T)` (expression) and `switch [v :=] x.(type) { case K T: }`
(statement) into new AST nodes. No checking/lowering yet — parse-only, validated
by parser unit tests.

**Step 3a — AST nodes** (`pkg/binate/ast.bni` + `pkg/binate/ast/ast.bn`):
- Add `EXPR_TYPE_ASSERT` before `NUM_EXPR_KINDS`; reuse `Expr.X` (operand) +
  `Expr.TypeRef` (the `@TypeExpr` target `K T`). No new fields.
- Add `STMT_TYPE_SWITCH` before `NUM_STMT_KINDS`; reuse `Stmt.X` (scrutinee) +
  `Stmt.Cases`. For the bound `v` name, reuse an existing string field (e.g.
  `Stmt`'s binder field used by short-var-decl) or add one small field — decide
  during impl. For per-case target *types*, extend `CaseClause`
  (`pkg/binate/ast.bni`): either overload `Exprs` with `EXPR_TYPE`-wrapped
  targets, or add `Types @[]@TypeExpr`. **Prefer a dedicated `Types` field** —
  overloading `Exprs` risks the expression-switch checker mis-handling them.
- Update the stringers `ExprKindName` / `StmtKindName` (`ast/ast.bn`) and the
  field-usage header comment.

**Step 3b — expression postfix `.(`** (`pkg/binate/parser/parse_expr.bn`
`continuePostfix`): **⚠ Review-corrected — the DOT arm must be *restructured*, not
merely "add an arm."** The current arm (parse_expr.bn ~L246) *unconditionally*
reads `nameTok := p.tok` and advances to build an `EXPR_SELECTOR`; for `.(` that
would wrongly produce a selector with `Name = "("`. Rewrite the arm to **branch on
`token.LPAREN` before consuming a name token**: if `.` is followed by `(`, consume
`(` and parse an assert/type-switch-head; else fall through to the existing
selector path. When it is `(`: if the next token is `token.TYPE`, this is a
**type-switch head** in expression position — parse it into a marker the
switch-parser recognizes and reject a bare `x.(type)` outside a switch (see 3c);
otherwise parse an **AssertTarget** (step 3d — *not* `parseType`), consume `)`,
build `EXPR_TYPE_ASSERT`.
- Lexer: **no change** — `.(` is `DOT`+`LPAREN`, `type` is `token.TYPE` already
  (§ recon; confirmed `token.TYPE` reserved). Two-token lookahead exists
  (`peekTok`, `peekTok2`).

**Step 3d — a dedicated `AssertTarget` parser (NEW — do NOT reuse `parseType`).**
**⚠ Review-flagged (MINOR):** `parseType` treats a leading `*`/`@` as a
pointer/slice **type constructor**, but per the grammar (`binate.ebnf`) the
leading `*`/`@` in an `AssertTarget` is **always the recovery kind**, never a
constructor. `parseType` on `*T` yields `TEXPR_POINTER(T)` (wrong AST shape) and
would *accept* `x.(*[]T)`, `x.(**T)`, etc. — which must be non-nameable-target
**compile errors**. Write a small `parseAssertTarget`: optional single `*`|`@`
(record as the recovery kind), optional `readonly`, then a **`TypeName`** only
(`parseNamedType` / a bare interface name), rejecting further `*`/`[]`/`func`/
composite constructors at parse time. Store `{kind, readonly, TypeName}` in the
node (extend `TypeExpr` or add a small AssertTarget carrier). This is the clean
place to enforce "nameable target" syntactically; the checker (§4a) then only has
to resolve the `TypeName` and apply the kind-legality table.

**Step 3c — type-switch statement** (`pkg/binate/parser/parse_stmt.bn`
`parseSwitchStmt` + `parseCaseClause`):
- `parseSwitchStmt` currently parses an optional tag via
  `parseExprNoCompositeLit` then `{ cases }`. Add a head fork: detect
  `[ident :=] PostfixExpr . ( type )`. Cleanest detection: parse the head
  expression; if it comes back as `EXPR_TYPE_ASSERT` whose `TypeRef` is the
  special `type` marker (or a dedicated flag), switch to type-switch shape
  (`STMT_TYPE_SWITCH`). Handle the optional `v :=` binder (the current switch has
  no init/binder head — add a minimal `ident :=` parse before the scrutinee).
- `parseCaseClause`: for a type switch, parse `case AssertTargetList:` where each
  target is an **AssertTarget** (step 3d), not an expression and not a general
  `parseType`. `default:` unchanged. **⚠ Review-corrected:** `startsType` does
  **not** exist in `parse_type.bn` (grep-confirmed) — there is nothing to reuse to
  detect "a case begins a type." Since a type-switch case is *known* to be in type
  position from the `x.(type)` head, `parseCaseClause` doesn't need a
  begins-a-type predicate at all — it dispatches on the already-decided
  type-switch shape and calls `parseAssertTarget` directly. (If a shared predicate
  is later wanted, write it; do not cite a nonexistent one.)

**Verification for Phase 3:**
- Parser unit tests (`pkg/binate/parser`): round-trip `x.(*T)`, `x.(@T)`, `x.(T)`,
  `x.(readonly T)`, `v, ok := x.(*T)`, `switch x.(type){case *A: ; case @B,@C: ;
  default:}`, `switch v := x.(type){…}`. Assert the AST kinds/fields.
- Negative parse tests: `x.(type)` outside a switch → parse error; `switch
  x.(type)` with an expression case → error.

---

## Phase 4 — Checker: typing assertions, comma-ok, and type switches

**Goal:** type-check the new nodes; produce the right result type(s); enforce the
recovery-kind table and the nameable-target constraint. No lowering yet.

**Step 4a — assertion typing** (`pkg/binate/types/check_expr.bn` `checkExpr`
dispatch, add an `EXPR_TYPE_ASSERT` arm):
- Operand `e.X` must be an interface value: `checkExpr(e.X)`, then require
  `Kind == TYP_INTERFACE_VALUE || TYP_INTERFACE_VALUE_MANAGED` (reuse the
  inline pattern from `check_builtin.bn` `present()`). Else `errAssertNonIface`.
- Resolve `e.TypeRef` via `resolveTypeExpr` (`resolve_type.bn`). Enforce
  **nameable target**: the base must reduce to a `TYP_NAMED` (concrete) or a
  `TYP_INTERFACE`; reject slice/func/array/struct-literal/`Self`/type-param-less
  targets → `errAssertNonNameable`. (For a `TYP_TYPE_PARAM` target inside a
  generic, defer resolution to monomorphization — see 4d.)
- Enforce the **recovery-kind table** (§11.12): read `K` from the AssertTarget
  node (step 3d gives it directly — `@` managed / `*` borrow / none value). Reject
  `@T` recovery from a `*I` source → `errAssertManagedFromRaw`. Element-level
  `readonly` may be added, not dropped. **⚠ Review note:** there is **no single
  named readonly-lattice helper** to "reuse" — readonly compatibility is embedded
  in `AssignableTo`. The add-not-drop enforcement for element `readonly` needs its
  own small check (compare the target's element-readonly against the boxed type's;
  reject a drop), not a drop-in call.
- Result type: **single-expression form** → the recovered type (`@T`/`*T`/`T` or
  `@J`/`*J`). **Comma-ok form** → see 4b (this needs a NEW mechanism, not a reuse).

**Step 4b — comma-ok wiring. ⚠ SUBSTANTIALLY REVISED per review (MAJOR): this is
NOT a zero-cost reuse — new mechanism is required at BOTH the checker and IR-gen
layers.** The first draft claimed `v, ok := x.(K T)` "flows through the existing
`hasExpandableResults`/`Results` path with no separate mechanism; this IS the
path." That is **false on inspection**:
- **Checker:** `hasExpandableResults` (check_stmt.bn) returns true **only** for
  `TYP_FUNC || TYP_FUNC_VALUE || TYP_MANAGED_FUNC_VALUE`, and `checkCallExpr`
  feeds it the whole `fnType`. A synthesized 2-tuple `Type` from an
  `EXPR_TYPE_ASSERT` is none of those kinds, so the destructure branch is never
  entered. **You must extend the multi-value-RHS path** to recognize an
  `EXPR_TYPE_ASSERT` RHS (either add a kind it accepts, or special-case the assert
  node in `checkShortVarDecl`/`checkAssignStmt` to bind `v := recovered`,
  `ok := bool`). This is new checker wiring.
- **IR-gen:** `genMultiAssign` (`gen_assign_multi.bn`) evaluates the single RHS
  and expects an `OP_CALL`-shaped **packed multi-return struct** to `extractvalue`
  fields from. An assert-expr produces neither a call nor that struct shape. **You
  must either** make the comma-ok assert lowering *produce* a `{recovered, bool}`
  packed value the extractor consumes, **or** add a dedicated assert-expr branch in
  the multi-assign lowering. This is new IR-gen wiring.
Net: budget comma-ok as **real work at two layers**, not a free ride on the
call-tuple path. (The single-expression form is comparatively simple — one value,
runtime panic on miss.)

**Step 4c — type-switch typing** (`pkg/binate/types/check_stmt.bn`, new
`checkTypeSwitchStmt` modeled on `checkSwitchStmt`):
- Check the scrutinee is an interface value (as 4a).
- Set `c.InSwitch` (for `break`). Per case: resolve each target type; enforce
  kind legality against the scrutinee (a `*I` switch admits no `@T` case);
  concrete vs interface target both allowed. `pushScope`; if there's a binder
  `v`: single-target case → `defineVar(v, caseType@kind)`; multi-target case or
  `default` → `defineVar(v, scrutineeType)` (§11.12). Check the body; `popScope`.
- No exhaustiveness, no duplicate-case, no fallthrough (§14.10) — do **not** add
  any of those diagnostics.

**Step 4d — generic/type-param targets:** when the target base is a
`TYP_TYPE_PARAM`, the concrete type is known only after monomorphization. Resolve
per-instantiation in the generic instantiation path
(`pkg/binate/types/check_generic.bn` / the instantiation substitution) so each
`List[int]` vs `List[float]` assertion resolves to its distinct concrete target.
Confirm the substituted target flows into IR-gen with the concrete type.

**Verification for Phase 4:**
- Checker unit tests: positive typings (each recovery kind; interface target;
  comma-ok binds two; type-switch per-case binder narrowing). Negative typings:
  non-interface operand; non-nameable target; `@T` from `*I`; dropping element
  readonly; `@T` case in a `*I` switch.
- **⚠ Review-flagged (MAJOR): Phase 4 standalone lands a SILENT miscompile unless
  an explicit unimplemented-guard is added.** Landing the checker makes `bnc`
  *accept* assertions program-wide. Any full-pipeline test then reaches IR-gen,
  where the fallbacks are **silent**: `genExprInner`'s unhandled-kind path returns
  `b.EmitConstInt(0, TypInt())` (a silent `const 0`), and `genStmt`'s fallback
  returns `b` unchanged (silently **dropping** a `STMT_TYPE_SWITCH`). No panic, no
  diagnostic — and self-compile stays green because `bnc`'s own source uses no
  assertions, so nothing catches it. This is exactly the silent-wrong-code class
  the project rules forbid. **Mandatory:** if Phase 4 is landed before Phase 5,
  add an explicit `panic("type assertion: IR-gen not yet implemented")` (or a hard
  compile error) in the `EXPR_TYPE_ASSERT` / `STMT_TYPE_SWITCH` arms of
  `genExprInner`/`genStmt`. **Recommendation given this hazard: merge Phases 4 and
  5** so an accepted assertion always has real lowering — the standalone-checker
  win is not worth a silent-miscompile window. (Checker *unit* tests in
  `pkg/binate/types` can still run without IR-gen; that's not the risk — the risk
  is any conformance/full-pipeline program that reaches IR-gen.)

---

## Phase 5 — IR-gen + backends + VM: assertion lowering

**Goal:** lower `x.(K T)` and `v, ok := x.(K T)` to real code that reads the
TypeInfo slot, compares/looks-up, applies the recovery-kind refcount discipline,
and (expression form) panics on a miss.

**Step 5a — IR ops.** Decide the lowering shape (**flag for reviewer**):
- **Option A (fewer new ops):** emit the whole assertion as inline IR from
  existing primitives — load `data`/`vtable` from the iface value
  (`IfaceValueDataIndex`/`VtableIndex`), null-check the vtable (unset → miss),
  load `vtable[1]` (TypeInfo), then for a concrete target compare against
  `&bn_TypeInfo.<T>` and branch; for an interface target emit a call to a runtime
  helper `rt.SatLookup(typeinfo, ifaceid) -> subvtable_or_null`. Recovery applies
  RefInc/borrow/copy via existing refcount emitters. This keeps backends
  untouched (no new op to lower four times).
- **Option B (new op):** an `OP_TYPE_ASSERT` that each backend lowers. More work
  (4 backends + VM), justified only if inline IR can't express the branch cleanly.
- **Recommendation: Option A for the CONCRETE case; a NEW IR construct is
  unavoidable for the INTERFACE case.** ⚠ Review-corrected — Option A's "no new
  op, backends untouched" claim is **wrong for interface targets**. A concrete
  assertion is indeed a branch + a pointer compare + refcount — all existing IR,
  no helper even needed (the compare is against the static `&bn_TypeInfo.<T>`).
  But an **interface** assertion `x.(*J)` must construct `{data, vtable(T,J)}`
  where the vtable is the **runtime** result of `rt.SatLookup` — and the only
  iface-value constructor, `EmitIfaceValue`/`OP_IFACE_VALUE`, takes a **static
  mangled vtable symbol name** (chosen at type-check time; codegen emits
  `bitcast [N x i8*]* @<name> to i8*` from that literal). There is **no existing
  way** to build an iface value whose vtable operand is a runtime `*void`. So the
  interface half needs a **new IR construct** — either extend `OP_IFACE_VALUE` to
  accept a dynamic (register) vtable operand, or add an `insertvalue`-style
  primitive that assembles a 2-word iface value from a runtime `data` + runtime
  `vtable`. Budget this explicitly. The **satisfaction scan** itself can still be a
  pure-Binate `rt.SatLookup` helper (like `rt.BoundsCheck`, uniform across native
  and VM); it's the *assembly of the result iface value* from the scan's runtime
  output that needs the new construct.

**Step 5b — the failed-assertion panic** (`impls/core/common/pkg/builtins/rt/rt.bn`
+ `rt_baremetal.bn`): add `rt.AssertFail(dyn *[]readonly char, target *[]readonly
char)` modeled on `BoundsFail`/`DivFail`: print `runtime error: type assertion
failed: ` + dyn + ` is not ` + target, then `Exit(1)`. IR-gen emits, on the
expression-form miss branch, a call `rt.AssertFail(scrutinee.TypeInfo.name,
"<T>")` where `<T>` is a static rodata name for the target and `dyn` is loaded
from the scrutinee's TypeInfo `name` field (null-vtable case: pass a literal
`"<unset>"` since there's no TypeInfo). Then an `unreachable`.
- **rt is BUILDER-relevant?** `pkg/builtins/rt` is compiled by bnc, not part of
  cmd/bnc's own tree, but it must stay within the language the current bnc emits;
  `AssertFail` uses only `print`/`println`/`Exit` — trivially fine.

**Step 5c — satisfaction lookup helper** (`rt.bn`): `rt.SatLookup(ti *TypeInfo,
id *IfaceId) *void` scans the **global `SatEntry` registry** (§2.2b ✅ DECISION —
distributed per-`(T,J)` globals, itab-like), matching `entry.type_id == ti &&
entry.iface_id == id`, and returns `entry.subvtable` or null. It does **not** read
a per-type `ti.sat_table` (that field is vestigial). Pure Binate; works in both
modes. (The concrete-identity compare stays inline — one pointer compare, no
helper.)

**Step 5d — recovery refcount discipline** (IR-gen, reuse existing emitters):
- `@T`/`@J` recovery: `RefInc` the recovered data (retain).
- `*T`/`*J` recovery: borrow — **no** refcount op.
- value `T` recovery: field-wise acquiring copy (`mem.copy` semantics — reuse the
  existing value-copy/acquire path used for struct assignment). A value recovery
  from a typed-nil box dereferences nil — that's user error per spec, not our
  concern to guard.
- **Memory-safety gate:** the recovered `@T` on a **miss** (comma-ok) must be the
  zero/unset value with **no** dangling RefInc; ensure the miss branch does not
  RefInc. On a hit, exactly one RefInc for `@` recovery. Add a refcount
  conformance test (§18-memory style) that asserts no leak/double-free across
  hit and miss.

**Step 5e — form-specific result:**
- Expression form: hit → recovered value; miss → 5b panic + unreachable.
- Comma-ok: yield `(recovered_or_zero, ok_bool)`; never panic.

**Verification for Phase 5:** the positive/negative conformance tests below
(§ Test matrix), across `builder-comp`, `builder-comp-int`, `builder-comp-comp`.
Cross-mode: the same assert must yield the same boolean in native and VM — run
the identical test under `-comp` and `-int` and diff output.

---

## Phase 6 — IR-gen + backends + VM: type-switch lowering

**Goal:** lower `switch [v :=] x.(type) { case … }` to a first-match chain.

- Lower to a sequence of the Phase-5 assertion primitives: null-vtable check
  first (→ `default`); then, per case in order, a concrete identity compare or an
  `rt.SatLookup`; first hit binds `v` (per-case type/kind, or scrutinee type for
  multi-target/`default`) and runs the body; no fallthrough. Reuse the
  Phase-5 recovery-kind refcount discipline for the bound `v` (managed cases
  RefInc into the case scope and RefDec at case-scope exit).
- Bind-scope refcount: a `@` binder retained at case entry must be released at
  case exit (reuse block-scope cleanup). This is the subtlest leak risk in the
  whole feature — add a refcount conformance test with a `@`-binding switch.
  **⚠ Review — the load-bearing detail the first draft hand-waved:** the existing
  cleanup (`emitDecForScopeVars`) keys purely on slot *type* and DecRefs managed
  slots at scope exit, and break/return unwinds are covered by `BreakVarLen` (set
  at switch entry). But it only fires **if the binder is registered into
  `ctx.Vars` as a properly-typed managed slot at case entry** — a `@T` binder
  pushed as a managed slot is auto-RefDec'd; a `*T` borrow-binder pushed as a raw
  slot is correctly skipped. So the concrete requirement is: **push the per-case
  binder `v` into `ctx.Vars` with its recovered type/kind at case-scope entry.**
  Get that right and the existing machinery (including the early-return/`break`
  unwind path) handles release; miss it and it's the double-free / leak R5 warns
  of. State this in the impl, don't just say "reuse block-scope cleanup."

**Verification:** type-switch conformance (single/multi/default/binder/unset →
default/typed-nil matches type/generic targets/`any`), all modes.

---

## Phase 7 — Test matrix, spec status, docs

- **Spec status flip** (`docs/spec/`): once conformance is green, remove the
  "_Draft; not yet implemented_" note from §11.12, update §7.13.8/§7.13.14 (the
  any-block now really carries `*TypeInfo`), flip §17.5's failed-assertion row
  from Draft to implemented, and update `docs/spec/00-index.md` rows 47/52/54/56
  (the "Draft (specified, not yet implemented)" annotations for §7.13.14,
  §11.12, §13.8, §14.10). Regenerate anything generated.
- **claude-notes.md / claude-todo.md**: move the RTTI/type-assertion item to the
  done log with the landed commits.
- All doc edits committed+pushed immediately (shared-checkout discipline).

### Test matrix (conformance, all across `builder-comp` / `-int` / `-comp`)

Positive:
1. Concrete assert, each recovery kind: `@I→@T`, `@I→*T`, `@I→T`, `*I→*T`, `*I→T`.
2. Interface assert, direct impl: `x.(*J)` where `impl T:J`.
3. Interface assert, **transitive ancestor**: `impl R:Child`, assert `x.(*Parent)`
   (the high-level plan's "review's critical" — must succeed via the closure).
4. comma-ok hit and miss (bool correct; `v` zero on miss).
5. Type switch: single-target, multi-target case, `default`, `v:=` binder
   narrowing, most-specific-first ordering.
6. `unset` scrutinee → miss / `default` (null-vtable short-circuit).
7. typed-nil box matches its type (data nil, `present` re-test).
8. Generic `List[int]` vs `List[float]` distinct-identity asserts.
9. `any` source (`*any`/`@any`) asserting to a concrete and to an interface.
10. **Refcount** (§18-memory): `@` recovery hit = exactly one RefInc, miss = none;
    `@`-binder type switch releases at case exit — no leak, no double-free.
11. **Cross-mode**: pick 2–3 of the above, assert byte-identical output under
    `-comp` vs `-int`.

Negative (compile errors):
1. Assert a non-interface value.
2. Non-nameable target (slice/func/array/struct/`Self`).
3. `@T` recovery from a `*I`.
4. Dropping element-level `readonly` on recovery.
5. `@T` case in a `*I` type switch.

Runtime (expression-form abort):
6. Wrong-type expression assert → the §17.5 panic with the exact diagnostic
   `runtime error: type assertion failed: <dyn> is not <T>` (goldens for the
   message; xfail-free since the feature is landing).

---

## 3. Risk register (correctness invariants)

| # | Risk | Mitigation |
|---|------|------------|
| R1 | Any-block growth re-bases method slots → **silent misdispatch** if any of the 4 emitters, 2 producers, size formula, or VM guard is missed | Phase 1 is one atomic commit touching all 8 sites (§0.1–0.4); smoke every backend package + all conformance modes; keep the null-slot phase provably inert before any reader exists |
| R2 | Satisfaction table not the transitive closure → `x.(*Parent)` wrongly fails | Reuse `collectImplsFromDecl`'s already-flattened `(T, ancestor)` entries (§0.8); test #3 exercises exactly this |
| R3 | Nested sub-vtable carries parent's TypeInfo instead of leaf's → downcast-after-upcast recovers wrong type | Emitters use the **top-level receiver's** TypeInfo at every nesting level (§2e); test: box, upcast to Parent, assert back to concrete |
| R4 | **Cross-mode identity — the biggest hole (review MAJOR).** Address-sharing works only for native-injected vtables; VM-lowered types (the default in `builder-comp-int`) have no TypeInfo materialization | Use the spec-sanctioned **own-native-TypeInfo-per-mode** model (§2f revised): VM builds per-type identity handles + a satisfaction map from the same `m.Impls` grouping; agreement is on the *result*, not the address. Test #11 diffs `-comp` vs `-int` |
| R5 | Recovery refcount bug (leak on hit, dangling on miss, double-free in switch binder) | Explicit refcount tests #10; `@`=RefInc / `*`=borrow / value=acquiring-copy discipline (§5d, §6); **push the per-case binder into `ctx.Vars` as a typed managed slot** (§6 revised); the compiler must never leak (project rule) |
| R6 | Duplicate `TypeInfo`/`IfaceId` symbols for generic instantiations across modules → link error | Weak linkage (`DG_WEAK`), same coalescing `@__ivt`/dtors use (§0.7); link-smoke in Phase 2. Note: `identity` self-ref is unprecedented — prefer using the record's own address as identity (§1 revised) |
| R7 | BUILDER breakage | No new language feature enters cmd/bnc's tree (§0.9); layout edits are `1→2`/`+1→+2`; run full self-compile chain; grep cmd/bnc tree for `.(` (none) |
| R8 | `*any` upcast (offset-0 reuse) breaks when any-block grows to 2 words | Dtor stays at 0; the upcast points at block start and now spans both words legitimately (§0.5); test any-boxing + dispatch |
| R9 | **No injection channel for TypeInfo/IfaceId (review MAJOR).** `registerVtableAddr` is fed only from the reflect descriptor's `Vtables` table | **Extend the reflect package descriptor** with TypeInfo/IfaceId tables (`reflect.bni`, all four `*_pkg_descriptor.bn`, `BuildPackageDescriptor`, VM ingestion) — a first-class Phase-2 work item (§1.1, §2f), previously unlisted |
| R10 | **Comma-ok / interface-value-assembly presented as free reuse but aren't (review MAJOR).** `hasExpandableResults` rejects assert-exprs; `OP_IFACE_VALUE` takes a static vtable symbol | New wiring at checker + `genMultiAssign` for comma-ok (§4b revised); a new IR construct to assemble `{data, runtime-vtable}` for interface targets (§5a revised) — budget both |
| R11 | **Phase 4 standalone = silent miscompile.** genExpr/genStmt fallbacks silently emit `const 0` / drop the switch | Merge Phases 4+5, or add an explicit unimplemented-panic guard in the new IR-gen arms (§4 verification revised) |

## 4. Open decisions to raise with the user before/while implementing

*(Several first-draft "open decisions" were resolved to hard findings by the
2026-07-03 adversarial review — see §6. These remain genuinely open:)*

1. **`IfaceId` token** (§1.1): dedicated per-interface identity symbol vs. name-string
   comparison. Recommend the symbol. (Confirmed: no existing per-interface artifact.)
2. **`identity` field** (§1): self-referential symref (unprecedented in-tree) vs.
   using the `TypeInfo` record's **own address** as identity (no interior field).
   Recommend the latter.
3. **TypeInfo builder location** (§2a): layout helpers in `pkg/types`, `DataGlobal`
   builder in `pkg/ir` — mild stretch of "define in pkg/types." Recommend the split.
4. **Interface-target IR construct** (§5a): extend `OP_IFACE_VALUE` to accept a
   dynamic vtable operand vs. a new `insertvalue`-style iface-assembly primitive.
5. **Phase 4+5 merge** (§4): recommend merging to avoid the silent-miscompile
   window (R11); confirm.
6. **Reflect-descriptor extension scope** (R9): confirm extending the reflect
   descriptor now (needed for VM cross-boundary TypeInfo mapping) vs. staging it.

## 5. Landing cadence

Phase 1 is one commit (atomic ABI shift). Phases 2–7 are each independently
landable and green; within a phase, split by package where a split keeps the tree
green (e.g. Phase 5: rt helper + IR-gen can be one commit; the checker error
messages another). Follow the standard landing procedure (rebase → re-run hygiene
→ smoke every changed package → base-check → cherry-pick → push from local main →
resync), with per-round explicit approval for each cherry-pick.

**Revised effort shape after review:** the first draft's cost curve was too flat.
The real weight is (1) Phase 1's atomic ABI shift + its ~10-file test-goldens
sweep, (2) the **cross-mode/VM** TypeInfo story incl. reflect-descriptor extension
(R4/R9 — was under-flagged as an "open question"), and (3) comma-ok + interface-
value-assembly wiring (R10 — was miscalled "free reuse"). Budget accordingly.

---

## 6. Adversarial review findings ledger (2026-07-03)

Consolidated audit trail. Severity is the reviewers'; "disposition" is how the
plan now handles it. All three reviewers grounded findings in file:line evidence.

**Survived scrutiny (verified, no defect) — the plan's load-bearing claims held:**
- Phase-1 site inventory (§0.1–0.4) is **complete** — a repo-wide grep found no
  hidden 5th vtable emitter and confirmed all dispatch readers consume
  `instr.Index`/`IfaceUpcastSlotOffset` **raw** (only the 2 producers + VM emitter
  carry the `+1`). The "silent misdispatch via a missed site" crown-jewel does
  not exist.
- Transitive-closure reuse (§0.8/R2): `collectImplsFromDecl` **does** register
  `(T, ancestor)` for every transitive ancestor; grouping `m.Impls` by receiver
  yields the full satisfaction set. `x.(*Parent)` will work.
- Leaf-TypeInfo propagation (§2e/R3): all four emitters thread the **top-level
  receiver** through parent recursion — a receiver-keyed TypeInfo slot is
  correctly the leaf at every nesting level.
- Weak coalescing + mangling determinism (R6): impl vtables/dtors/func-value
  handles are all `DG_WEAK` / `N_WEAK_DEF`; `instantiationMangledName`/`StructName`
  are pure deterministic string builders → stable across TUs.
- `*any` upcast survives the 2-word any-block (§0.5/R8); dtor stays at offset 0.
- Panic diagnostic text + `rt.AssertFail` signature match §17.5.

**MAJOR — folded in:**
1. **Cross-mode/VM TypeInfo (R4, §2f):** address-sharing covers only native-
   injected vtables; VM-lowered types (default in bytecode mode) have no TypeInfo
   materialization and no assertion-site symref resolution. → Rewrote §2f to the
   spec-sanctioned own-native-TypeInfo-per-mode model (agreement on the *result*).
2. **No injection channel (R9, §1.1/§2f):** `registerVtableAddr` is fed only from
   the reflect descriptor's `Vtables` table. → New first-class work item: extend
   the reflect descriptor with TypeInfo/IfaceId tables across all four writers +
   VM ingestion.
3. **Comma-ok is not free reuse (R10, §4b):** `hasExpandableResults` accepts only
   func kinds, and `genMultiAssign` expects an `OP_CALL`-shaped packed struct. →
   Rewrote §4b: new wiring required at both checker and IR-gen.
4. **Interface-value assembly needs a new IR construct (R10, §5a):**
   `OP_IFACE_VALUE` takes a *static* vtable symbol; an interface assertion's vtable
   is a *runtime* `SatLookup` result. → Rewrote §5a: Option A holds for concrete
   targets only; interface targets need a dynamic-vtable iface-assembly construct.
5. **Phase 4 standalone = silent miscompile (R11, §4):** genExpr/genStmt fallbacks
   silently emit `const 0` / drop the switch. → Recommend merging Phases 4+5, or a
   mandatory unimplemented-panic guard.

**MINOR — folded in:**
- `identity` self-pointer has no in-tree precedent (§1) → prefer the record's own
  address as identity.
- `parseType` conflates the recovery-kind prefix with pointer/slice constructors
  (§3d) → dedicated `parseAssertTarget`.
- `startsType` cited for reuse **does not exist** (§3c) → removed the citation.
- `continuePostfix` DOT arm needs restructuring, not just "add an arm" (§3b).
- "reuse the readonly-lattice check" overstates reuse (§4a) → it's embedded in
  `AssignableTo`; write the add-not-drop check explicitly.
- Phase-1 test-goldens sweep must be grep-enumerated (~10 files), not "any test"
  (§Phase 1 verification) — the one place under-scoping would land "done-but-red."
- Step 9 (`gen_iface.bn` any-vtable) is **comment-only**, not a `[1 x i8*]`
  literal edit (auto-tracks via `IfaceFullVtableSize`).
- `@`-binder release requires pushing the binder into `ctx.Vars` as a typed
  managed slot (§6) — the load-bearing detail behind "reuse block-scope cleanup."

**NIT / noted:** `<unset>` for the `<dyn>` panic field is implementation-defined
(acceptable); the `any`-only-boxing enumeration works on the native path but needs
the "emit from every module naming `(T,*)`" rule + VM-ordering parity (§2c).
