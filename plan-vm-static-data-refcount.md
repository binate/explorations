# Plan: refcount the VM's per-instance "static" data (drop the immortal-sentinel leak)

Status: DESIGN — **MAJOR REVISION NEEDED.** A medium adversarial review (5 lenses vs the
code) found the reference-path classification below substantially wrong: the Family A OWN
paths are illusory (no owning root exists), the owning tables mix in injected immortal
addresses, and most of these records are HEADER-LESS so the "uniform sentinel no-op"
mechanism does not apply to them. Do NOT implement the classification tables as written —
see **§Adversarial review findings** at the bottom first. The lifetime *model* (isolation /
injection-identity / data-refs / static-imports) survives; the *mechanism and per-path
classification* need rework.

## Problem

The bytecode VM (`pkg/binate/vm`) `RawAlloc`s package metadata (reflect descriptors,
TypeInfos, ifaceids, native interface vtables) and global-variable storage **per module
load**, tags each block immortal with `rt.STATIC_REFCOUNT` (`-2^30`), and **never frees
it**. Embedding a VM is a core feature — an application spins VMs up and down freely — so
this leaks unboundedly on host and exhausts the bare-metal arena. (The dominant per-VM
leak, the execution stack, is already fixed and landed, `7f029699c`; this plan covers the
remaining families.)

**The compiled/native backend has NO analogous leak and needs no change:** it emits the
exact same data as static sections in the binary image (`.rodata` / `.data.rel.ro` /
`.data`; `native/common/common_data_global.bn`, `codegen/emit_data_global.bn`), reclaimed
by the OS at process exit. Immortality is correct there. **The fix is VM-side only.**

## Lifetime model (the architecture this fix must honor)

- **Isolation is the default.** A VM (and native code) is its own world. A VM owns its
  local packages' metadata + globals as **refcounted** managed allocations (not immortal).
  Absent sharing, the VM's tables are the only holders, so those instances free when the VM
  drops them (teardown) — refcounted, not immortal, not unconditionally VM-freed.
- **Injection establishes shared package IDENTITY** and is the *only* isolation-breaker.
  `RegisterPackage*` (`extern_register.bn:55-168`) / `registerGlobalAddr`
  (`lower_data.bn:94-101`) make VM-B's package P *the same* P (same global / vtable /
  satentry / typeinfo instances) as the injector's. Refcount terms: injection = RefInc the
  shared instance; teardown / un-inject = RefDec. **Today injection targets the compiled
  binary's immortal image instances** (recon-confirmed), which stay immortal (the sentinel
  no-ops) — so live refcounting only bites for VM↔VM injection (the REPL's dynamic case)
  and for VM-local instances.
- **Data references are freely, dynamically shareable** = ordinary refcounting. A **managed**
  reference to a record keeps it alive while held; a **raw borrow** (`*T`) is valid only
  while the referent is alive and imposes *no* lifetime obligation (holder's responsibility
  afterward — standard borrow rules).
- **Imports are static outside the REPL**, so the whole sharing graph is fixed at load. The
  REPL's mid-session import (`repl/mid_session_import.bn`) is the only place identity is
  established dynamically — the case to handle with care.

## Mechanism (why this is feasible without origin-discrimination)

`RefInc`/`RefDec` already sign-bit-check and no-op on a negative refcount — runtime
`rt_managed.bn:109` / `:119`; VM inline `vm_exec_helpers.bn:237`, `vm_exec.bn:391`; native
inline (`aarch64_refcount.bn:72`, `arm32_refcount.bn:39`); LLVM `emit_refcount.bn:90`. So
adding RefInc/RefDec on a reference path is **uniform**: an immortal *image* instance
no-ops, a refcounted *VM* instance is tracked. The fix therefore is (1) VM allocates these
with a **real** refcount (positive, `FreeFn = RawFree`) instead of `rt.STATIC_REFCOUNT`,
and (2) every reference path does the correct RefInc/RefDec. No code needs to know whether
a given pointer is image-static or VM-heap.

## Reference-path classification (THE design — review this)

Legend: **OWN** = owning managed reference (RefInc on acquire, RefDec on release; keeps the
block alive). **BORROW** = raw pointer, no lifetime obligation (valid while alive; UB after
— holder's responsibility). **IDENT** = shared-identity via injection (RefInc the shared
instance). **OPEN** = classification needs a code check during the slice, NOT guessed here.

Get it wrong three ways: an OWN path left raw → re-leak; a BORROW path that RefIncs without
a matching RefDec → leak; a genuinely-OWN path left as a BORROW → free-too-early → UAF.

### Family A — type metadata: reflect descriptors + TypeInfo + IfaceId
Alloc `lowerDataGlobals` → `rt.RawAllocZero` (`lower_pkg_descriptor.bn:47`), per module
load, weak-coalesced (~once per type). Header-carrying reflect nodes (Package / Function /
Global / Vtable / SatEntry) vs **header-less, address-identity** TypeInfo + IfaceId.

| Path | file:line | Class |
|---|---|---|
| `vm.dataSymAddrs` / `dataSymNames` table | vm.bni:788-789; lower_typeinfo.bn:61-62,122-123 | OWN (RefDec all at teardown) |
| `__Package()` accessor baked payload addr | lower_pkg_descriptor.bn:190-192,379-416 | OWN (held by the returning VMFunc) |
| `reflect.Package` value returned to caller | same | OWN if retained (managed reflect value) / BORROW if transient — **OPEN** (is the reflect value managed?) |
| reflect slices' `data` ptrs into the block | data_pkg_descriptor.bn:145-155 | BORROW (interior; owner is the node) |
| iface value's dynamic `*TypeInfo` | vm_exec_iface.bn:294-338; vm_iface_native_vt.bn:70-75 | **OPEN** — verify how an iface value holds its type ptr; if an escaped iface must keep TypeInfo alive it is OWN |
| `BC_DATA_SYM_ADDR` → register (assertion operand) | vm_exec_funcref.bn:27-37 | BORROW (transient identity operand) |
| `BC_SAT_LOOKUP` / `BC_IFACE_TYPEINFO` | vm_exec_funcref.bn:38-46; vm_exec_iface.bn:294-338 | BORROW (identity compare) |
| `FunctionInfo.Value` = callee.Handle | lower_pkg_descriptor.bn:343-357 | cross-family (handle lifetime) |
| `GlobalInfo.Addr` = global storage | lower_pkg_descriptor.bn:319-330 | cross-family (Family C) |

**Address-identity invariant (hard):** TypeInfo / IfaceId are compared by address
(`BC_DATA_SYM_ADDR` vs `BC_IFACE_TYPEINFO`). There must be exactly ONE canonical instance
per (type, identity-scope). Within a VM, weak-coalescing gives one; injection gives shared
identity across the boundary. A refcounted design MUST preserve "one canonical instance per
type per identity scope" — never allocate a second copy that would compare unequal.

### Family B — native interface vtables
Alloc `buildNativeIfaceVtable` → `rt.RawAlloc` (`vm_iface_native_vt.bn:85`), cached on
`vt.NativeVt` (raw int), lazy per `@IfaceVtable`.

| Path | file:line | Class |
|---|---|---|
| `vt.NativeVt` cache on `@IfaceVtable` (in `vm.IfaceVtables`) | vm.bni:821; vm_iface_native_vt.bn:97 | OWN (RefDec when the @IfaceVtable is freed) |
| escaped iface vtable word in native retbuf/stack copy | vm.bn:224; vm_iface_crossmode.bn:108 | BORROW (native memory; not refcounted by native) |
| block slots (handle addrs, `*TypeInfo`, 0) | vm_iface_native_vt.bn:87-96 | internal raw ints (handles/TypeInfo owned elsewhere) |

The block is refcount-owned by `vt.NativeVt`; a managed holder *could* keep it alive
(shareable in principle), but current cross-mode escapes are raw borrows, so absent one it
frees at teardown.

### Family C — global storage
Alloc `materializeGlobals` / `MaterializeOneGlobal` → `rt.RawAlloc` (`lower_data.bn:57,76`),
per non-extern global, per-VM, appended across modules; stored in `vm.globalAddrs`.

| Path | file:line | Class |
|---|---|---|
| `vm.globalAddrs` table | vm.bni:778-780; lower_data.bn:60 | OWN (RefDec at teardown) |
| managed pointer STORED IN a global (`var x @T`) | lower_func.bn:288-290 | **teardown obligation**: block free must RefDec the managed word first (else leak pointee). Write-time RefInc / RefDec-old already in IR-gen. |
| injected global (`registerGlobalAddr` → external addr) | lower_data.bn:94-101; extern_register.bn:70-76 | IDENT / BORROW (injector's immortal storage; not VM-owned) |
| `&global` baked into bytecode (`BC_LOAD_IMM`) | lower_func.bn:299-311 | BORROW (raw `*T`) |
| `&global` into reflect `GlobalInfo.Addr` | lower_pkg_descriptor.bn:319-330 | BORROW (raw addr to embedder) |

## Teardown protocol (@VM)

There are no user destructors; the `@VM` generated dtor RefDecs managed FIELDS only. So the
OWN references above must be made **managed** for the dtor to release them:
- The owned-block references currently held as raw `int` in `@vec.Vec[int]` tables
  (`dataSymAddrs`, `globalAddrs`) and as `int` (`vt.NativeVt`) must become **managed
  handles** (one per block) so the `@VM` dtor RefDecs them → each block frees when the VM's
  ref (and any other OWN ref) drops.
- Global block free must **RefDec the managed word it holds** before reclaiming the block.
  **OPEN:** how to attach content-RefDec to a global block's free — a per-block `FreeFn`
  that RefDecs then RawFrees, vs an explicit teardown pass over `globalAddrs`. Needs the
  type info per global (which words are managed) at teardown — check what's available.

**OPEN (the crux):** a block is referenced both by an OWN table entry and by raw BORROW
bakes (bytecode, reflect). Converting the OWN reference to a managed handle while the
BORROWs stay raw is correct **iff** the OWN handle outlives every borrow. That holds when
the owner is the VM and the borrows are VM-scoped (bytecode/reflect read during the VM's
life); escaped *native* borrows are the holder's responsibility per the model. Confirm this
ordering per family during the slice.

## Implementation phasing (vertical slices, each adversarially reviewed)

1. **Globals** — most self-contained (single owning table; the new bit is the
   managed-content RefDec on free). Establishes the "managed owning handle + free-with-
   content-RefDec" pattern.
2. **Native vtables** — single owning ref (`vt.NativeVt`); no managed contents; escapes are
   borrows.
3. **Descriptors + TypeInfo + IfaceId** — most paths + the address-identity invariant + the
   iface-`*TypeInfo` classification. Do last, most carefully.

Each slice: switch the alloc from the sentinel to a real refcount; make the OWN reference
managed; confirm (baremetal per-class leak dump) the family's blocks now free; adversarial
review for completeness (no missed RefInc → UAF; no missed RefDec → leak) and, for Family
A, the one-canonical-instance-per-type invariant.

## Verification

- Per slice: instrumented baremetal per-class RawAlloc/RawFree dump shows the family's
  blocks freeing under a create-drop-VM loop.
- Full `vm` `builder-comp_arm32_baremetal` unit lane stays green.
- Hosted + `int`-mode conformance unchanged (the change must be behavior-preserving; the
  sentinel uniformity keeps compiled paths untouched).
- Add a permanent baremetal create-drop-VM regression conformance test (like `1209` for the
  allocator) once all families land.

## What stays unchanged

- The compiled/native backend's static data (immortal image instances) — correct as-is.
- The `rt.STATIC_REFCOUNT` sentinel + sign-bit RefInc/RefDec mechanism — it is what makes
  the VM-side fix uniform.

## Adversarial review findings (2026-08-14) — the classification is wrong; rework required

A 5-lens adversarial review + self-verification against the code confirmed the following.
All are grounded (grep/read anchors given); the workflow's verify pass was partly cut off by
a weekly token limit, but each item below was re-checked by hand.

1. **CRITICAL/MAJOR — Family A reflect-descriptor blocks have NO owning reference.** Both
   OWN rows for the descriptor family are false. `vm.dataSymAddrs` is pushed to ONLY at
   `lower_typeinfo.bn:62` (TypeInfo record), `:123` (IfaceId), `:141` (`registerDataSymAddr`)
   — the reflect Package/Function/Global/Vtable nodes, tables, and rodata blobs are in NO VM
   table (`emitPackageDescriptorVM` discards `lowerDataGlobals`' returned `names,addrs` into
   locals, `lower_pkg_descriptor.bn:184`). The `__Package()` accessor holds the descriptor
   only as a raw `BC_LOAD_IMM` immediate (`:396-398`) — a BORROW (`BCInstr` has no managed
   field, `vm.bni:456`). So implementing "flip to real refcount" re-leaks the whole descriptor
   family, or — if the returned `reflect.Package` is a managed value — UAFs it (a caller RefDec
   drops a rootless refcount-1 block that later `__Package()` calls still bake).

2. **CRITICAL/MAJOR — `dataSymAddrs` mixes VM-owned and INJECTED-external addresses.** The
   same table holds VM-`RawAllocZero`'d TypeInfo/IfaceId (`:62`,`:123`) AND injector-owned
   native rodata `&TypeInfo(T)` pushed by `registerDataSymAddr` (`:141`, fed by
   `RegisterPackageSatEntries`/`RegisterPackageRttiSyms`, `extern_register.bn:125,165-166`).
   The doc's "OWN — RefDec all at teardown" would RefDec/free a native **rodata** pointer →
   write-to-read-only SIGSEGV / free-of-non-heap. Family A must be origin-split like Family C.
   Related: `satEntryTypes`/`satEntryIfaces` alias the same VM TypeInfo/IfaceId blocks
   (`lower.bn:309,324-325`) → double-RefDec risk; both are unclassified.

3. **MAJOR (fundamental) — the "uniform sentinel no-op" mechanism does not apply to
   HEADER-LESS records.** TypeInfo (word 0 is the dtor symref, not a refcount —
   `data_typeinfo.bn:58-61`), IfaceId, every rodata blob / backing pointer array / field
   table, and the Family B native vtable (`vm_iface_native_vt.bn:85`) carry NO 2-word managed
   header. RefInc/RefDec on them reads adjacent memory as a "refcount" → garbage/crash. They
   cannot be refcounted by the sentinel scheme, and adding a header shifts the payload address
   — which breaks TypeInfo/IfaceId ADDRESS-IDENTITY (assertions compare addresses) and diverges
   from the header-less image records. This invalidates the doc's headline feasibility premise
   for these records.

4. **MAJOR — records are MULTI-BLOCK; only the record base is tracked.** `BuildTypeInfo`
   emits up to 4 interdependent blocks per struct type (record + `__typename` blob +
   `__typefields` table + `__typefieldnames` blob, `data_typeinfo.bn:84-98`), linked by raw
   symrefs; only the record base is in `dataSymAddrs` (`:141`/`:62`), the trailers
   "deliberately NOT registered" (`lower_typeinfo.bn:48-49`). "One managed handle per block"
   re-leaks the trailers, and the trailers can't be freed independently of a still-live record.

5. **MAJOR (correction) — there is NO VM↔VM injection.** The REPL mid-session import calls
   `LowerModule` on the single session VM and no `RegisterPackage*`/`registerGlobalAddr`
   (grep of `pkg/binate/repl` is empty). ALL injection is native→VM of immortal image data.
   So injected instances are ALWAYS immortal-image (sentinel no-ops, no refcount needed); live
   refcounting is purely for VM-LOCAL instances. Simplifies the model, but the doc's framing
   was wrong.

**Corrected direction (needs sign-off before re-drafting the classification):** the VM must
track the blocks IT allocated in a dedicated per-VM OWNING side-table (NOT the address-lookup
tables, which mix in injected immortal addresses), and release them at teardown — RawFree for
header-less blocks, RefDec for the header-carrying reflect nodes. A descriptor's header-less
sub-blobs (name/field blobs reachable from an escapable `reflect.Package`) must be tied to the
header-carrying node's lifetime (freed when it hits refcount 0), not freed independently.
Address-identity records (TypeInfo/IfaceId) are referenced by raw address (BORROWs), so
freeing them at teardown is consistent with the model — escaped raw borrows are the holder's
responsibility. Injected-external addresses are never in the owning side-table.

## Related

- Dominant stack leak already fixed: main `7f029699c`.
- Root-cause + audit: `claude-todo.md` ("vm bare-metal LEAK …").
- `native/arm32` bare-metal unit lane is still xfail'd (separate analogous raw-fixture
  leak, its own investigation).
