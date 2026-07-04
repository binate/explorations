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

### 0.8 The impl registry already gives us the satisfaction closure

- `pkg/binate/ir/gen_impl.bn` → `collectImplsFromDecl` already registers, for
  each `impl T : Child`, one `ImplInfo` per `(T, Child)` **and** one per
  `(T, ancestor)` for every transitive ancestor (via `IfaceAncestorClosure`).
  So **grouping `Module.Impls` by `(RecvPkg, RecvTypeName)` yields, per concrete
  type, the full transitive interface list** the satisfaction table needs — no
  new closure walk required. The per-interface sub-vtable symbol for each cell is
  `findImplVtableName` / `mangle.ImplVtableName`; its offset within T's vtable is
  `IfaceParentSlotOffset`.

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
leaves field order and table search structure informative. Proposed record:

```
TypeInfo {                      // static, one per concrete type, weak linkage
    identity:  *TypeInfo        // = &self (self-referential identity token;
                                //   pointer-equality within a mode is the test)
    dtor:      handle           // same handle as the vtable any-block slot 0
    size:      int              // t.SizeOf()  (target's value, baked at emit)
    align:     int              // t.AlignOf()
    name:      *[]readonly char // t.QualifiedTypeName() into rodata
    sat_len:   int              // number of satisfaction-table entries
    sat_table: *SatEntry        // pointer to sat_len contiguous SatEntry records
}
SatEntry {
    iface_id:  *IfaceId         // per-interface identity token (see §1.1)
    subvtable: *void            // &(T's @__ivt) + IfaceParentSlotOffset*W
                                //   — the sub-vtable to install on a hit
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
- **`sat_table`** is a flat array scanned linearly. Interface counts per type are
  tiny (single digits), so linear scan is fine; a sorted/hashed structure is a
  premature optimization. Search structure is informative.

### 1.1 Per-interface identity token (`IfaceId`) — the key sub-decision

An **interface** assertion `x.(*J)` must, at runtime, find "does T satisfy J?"
by scanning T's `sat_table`. That requires a stable token identifying `J` that
**both** the table entries (emitted when building T's TypeInfo) **and** the
assertion site (which knows J statically) can reference.

**Proposal:** emit one static **`IfaceId`** symbol per interface program-wide
(weak linkage, deterministic mangled name, e.g. `bn_IfaceId.<mangled J>`). It can
be a zero-content 1-byte marker — only its *address* matters. The `sat_table`
entry for J stores `&IfaceId(J)`; the assertion site references `&IfaceId(J)`
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
   the injected `sat_table` holds.
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
id *IfaceId) *void` scans `ti.sat_table[0..sat_len]` for `entry.iface_id == id`,
returns `entry.subvtable` or null. Pure Binate; works in both modes. (The
concrete-identity compare stays inline — it's one pointer compare, no helper.)

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
