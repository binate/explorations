# Plan: refcount the VM's per-instance "static" data via one owning slice-list

Status: DESIGN v2 — **re-reviewed clean, ready to implement** in vertical slices. Reworked
after a v1 adversarial review killed the per-path OWN/BORROW-classification approach (v1's
findings are in git history, commit `569ad91c`). A v2 adversarial re-review (3 lenses:
re-leak/completeness, UAF/double-free/address-stability, globals) returned **0 confirmed
defects** — the completeness and memory-safety lenses found nothing; the lone finding was a
restatement of the already-OPEN globals managed-word-RefDec item and was refuted. The one
genuinely-open implementation detail (how globals RefDec their managed content at teardown)
is resolved by phasing slice 1.

## Problem

The bytecode VM (`pkg/binate/vm`) `RawAlloc`s package metadata (reflect descriptors +
sub-nodes/tables/rodata blobs, TypeInfo records + name/field trailer blobs, IfaceId markers,
native interface vtables) and global-variable storage **per module load**, tags each block
immortal with `rt.STATIC_REFCOUNT`, and **never frees it** → leaks on every VM teardown.
Embedding a VM is a core feature (an app spins VMs up and down freely), so this leaks
unboundedly on host and exhausts the bare-metal arena. The dominant per-VM leak, the
execution stack, is already fixed and landed (`7f029699c`); this plan covers the rest.

**The compiled/native backend has no analogous leak and is unchanged:** it emits the same
data as static sections in the binary image (`.rodata`/`.data.rel.ro`/`.data`), reclaimed by
the OS at process exit.

## Lifetime model (carried over from v1 — this part survived review)

- **Isolation is the default.** A VM (and native code) is its own world; a VM owns its local
  packages' metadata + globals.
- **Injection establishes shared package identity** (`RegisterPackage*` / `registerGlobalAddr`)
  and is the only isolation-breaker. It **only ever targets immortal image data** — there is
  NO VM↔VM injection (the REPL does no `RegisterPackage*`; grep of `pkg/binate/repl` is
  empty). So injected instances are always immortal-image (referenced by raw address); live
  refcounting is purely for VM-LOCAL instances.
- **Data references are freely, dynamically shareable** = ordinary refcounting: a **managed**
  reference keeps a block alive while held; a **raw borrow** (`*T`) is valid only while the
  referent lives and imposes no lifetime obligation (holder's responsibility afterward).
- **Imports are static outside the REPL**, so the sharing graph is fixed at load.

## Design: one owning slice-list

Every block the VM allocates today via `rt.RawAlloc`/`RawAllocZero` becomes a managed
`@[]uint8` (via `make_slice(uint8, sz)`, which zero-fills like `RawAllocZero`), pushed into a
single per-VM owning collection — a new `@VM` field:

    ownedBlocks @vec.Vec[@[]uint8]

The raw base every existing call site needs is `&blk[0]` (exactly the landed stack-fix
pattern). A one-line helper replaces each raw alloc:

    func vmOwn(vm @VM, sz int) *uint8 {
        var b @[]uint8 = make_slice(uint8, sz)   // zero-filled
        vm.ownedBlocks.Push(b)
        return &b[0]
    }

**Why this works uniformly:**
- The slice's OWN managed header carries the refcount — so the record payload stays
  **header-less**, and its address is **stable** (Binate never moves managed allocations), so
  TypeInfo/IfaceId **address-identity** (type assertions compare `&__typeinfo.<T>`) is
  preserved.
- **Teardown is automatic:** the `@VM` generated dtor RefDecs `ownedBlocks` → the vec's
  backing RefDecs each `@[]uint8` → each block frees. No cycle (a byte slice has no edge back
  to the VM). No new teardown code beyond the field.
- **Ownership is uniform** (the list), so there is NO per-path owning-vs-borrow verdict to
  make. All raw pointers into the payloads — `dataSymAddrs` lookup entries, baked
  `BC_LOAD_IMM` addresses, address-identity operands, native-vt slots, escaped reflect/iface
  pointers — are **borrows** into VM-owned slices: valid while the VM lives, holder's
  responsibility if retained past teardown (standard borrow rules; matches the compiled
  side's raw pointers into immortal image data).

**Why it dissolves the v1 review findings** (rather than patching them):
1. *Descriptors had no owning root* → the list IS the root; descriptors need not be in any
   lookup table.
2. *`dataSymAddrs` mixed VM-owned + injected addresses* → injected externals are never
   `make_slice`'d, so never in the list; teardown frees only VM-allocated slices.
   `dataSymAddrs` stays a pure raw-address lookup.
3. *Header-less records couldn't be refcounted* → the slice header refcounts; the record
   stays header-less.
4. *Multi-block records* → each block is its own slice in the list; all owned + freed.
5. *No VM↔VM injection* → injected data is immortal image, borrow-referenced, never owned.

**The sentinel is NOT touched.** `rt.STATIC_REFCOUNT` stays on the header-carrying reflect
nodes; it governs USER-CODE RefInc/RefDec of a reflect *value* (no-op = borrow semantics),
which is correct — a `reflect.Package` is a borrow into a VM-owned slice, not an
independently-refcounted object. The slice, not the node's word-0 header, frees the block.
Sentinel and slice are orthogonal: sentinel = user-refcount no-op; slice = VM teardown
ownership.

## Allocation sites to convert (`rt.RawAlloc*` → `vmOwn`)

- `lower_pkg_descriptor.bn:47` (`lowerDataGlobals` — every `DataGlobal` block: descriptor
  nodes, `_pkg_*` backing arrays, name/sig rodata blobs, TypeInfo records + `__typename` /
  `__typefields` / `__typefieldnames` trailers, IfaceId).
- `vm_iface_native_vt.bn:85` (native interface vtable).
- `lower_data.bn:57`, `:76` (global storage — see globals below).

`materializeModuleStrings` (`lower_data.bn:16-29`) already uses managed slices for string
constants — confirm it is (or fold it into) the same ownership so it frees too.

## Globals: managed content + independent initializer allocation

A global `var x @T` stores a **managed pointer** in its storage block; the pointee is
**independently allocated** — a global initialized with a literal (`var s @[]char = "..."`,
`var p @Foo = Foo{...}`) allocates the initializer object as its own managed allocation, and
the storage holds a pointer to it (the initializer object must NEVER be baked inline into the
storage block — that would make it un-shareable and un-refcountable; verify the VM's global
init path allocates it independently and assigns via a normal managed store that RefIncs).

Consequence: freeing the storage slice reclaims the pointer word but does NOT RefDec the
pointee → it would leak the independently-allocated object. So **at teardown the VM must
RefDec the managed word(s) a global block holds** (before / as part of freeing it). `nil`
words RefDec to a no-op; a multiply-assigned global holds exactly one reference at teardown
(IR-gen does RefDec-old/RefInc-new on each store), so one RefDec is correct.

The VM needs per-global "which words are managed." It is derivable from the global's
`ir.Global.Typ` at `materializeGlobals` time.

**DECIDED — content-RefDec via an EXPLICIT VM teardown method, NOT a custom FreeFn.** Recon
confirmed there is no user-destructor hook (VMs are just refcount-dropped; the generated
`@VM` dtor RefDecs only *fields*, so `ownedBlocks` frees the storage *bytes* but never their
typed content) and no custom-FreeFn precedent. A custom `FreeFn` is rejected as an
abuse — free functions are the allocator's byte-reclaim callback, not destructors. The
remaining alternative (make each global block a managed struct carrying a `@[]@any` so the
generated dtor walks it) is rejected as hopelessly complex for heterogeneous global types.
So: a `Shutdown`/`ReleaseGlobals` method the VM's owner invokes before dropping the VM,
which iterates `globalAddrs` with a per-global managed-word map (from `ir.Global.Typ`) and
RefDecs each managed word. It runs *before* the `ownedBlocks` slice-list free (byte
reclamation stays automatic via the dtor; only the content-RefDec is explicit). Callers
(`cmd/bni`, `interp`, `repl`) add the call. This is globals-only; the metadata blocks hold
only raw borrows (symrefs / handle addresses owned elsewhere), so they carry no such
obligation.

**Split of slice 1:** Part A (block ownership — `ownedBlocks` + `vmOwn`, both global allocs
converted) is **LANDED, main `8eb93eee7`** — adversarially reviewed (memory-safety +
regression lenses, 0 defects), hosted + bare-metal vm green, int-mode reflect/globals smoke
green. It also lands the shared `ownedBlocks`/`vmOwn` infrastructure slices 2–3 reuse. Part B
(the explicit-teardown content RefDec) follows as its own change.

*Pre-existing observation* (both Part-A review lenses flagged it; NOT introduced by this
change, NOT a safety issue — both blocks are valid and each is freed once): `materializeGlobals`
appends across modules, so re-lowering the same package materialises a **second** owned block
for the same global name (`lookupGlobalAddr` returns the first). A correctness/aliasing concern
that predates the refcount work; worth a separate look, out of scope here.

## Sharing (for the record)

These are now refcounted managed objects: a Binate holder that takes a `@[]uint8` reference
keeps a block alive past VM teardown. Current references are raw borrows, so absent such a
holder the blocks are VM-lifetime; injection references immortal image data (no VM
ownership). If a future path needs a block to outlive its VM, it holds the managed slice —
that mechanism now exists (unused today).

## Phasing (vertical slices; each re-verified on bare metal + adversarially reviewed)

1. **Globals** — Part A (block ownership) **LANDED main `8eb93eee7`**; Part B (managed-content
   teardown RefDec via an explicit Shutdown) is still open.
2. **Native vtables** — **LANDED main `78dd7611d`** (single alloc site, no managed content;
   clean 2-lens review, 1 doc-comment fix).
3. **Descriptors + TypeInfo + IfaceId** — **LANDED main `c0715343e`** (single shared alloc
   site `lowerDataGlobals` threaded with `vm` → `vmOwn`; covers the descriptor nodes/tables/
   rodata blobs, TypeInfo records + trailers, and IfaceId — all multi-block records; address
   identity preserved. 2-lens review: 0 code defects, comment fixes only).

**→ The entire VM static-data BLOCK leak is now closed** (stack + globals + native vtables +
descriptors/TypeInfo/IfaceId). The only remaining piece is **globals Part B** (a managed
global `@T`'s independently-allocated *content* — RefDec via an explicit Shutdown).

**Leak-test facility — LANDED main `af01418b6`.** `rt.LiveBlocks()` (a process-global live
raw-allocation count; +1 per RawAlloc/RawAllocZero, −1 per non-nil RawFree) with rt_test.bn
validating the counter (balance / count-once / nil-free-noop).

**Definitive VM leak test — LANDED with slice 3 (`c0715343e`), PASSING.** `leak_test.bn`:
build a module outside a measured scope, lower it into a VM, drop the VM, assert
`rt.LiveBlocks()` returns to baseline. The source (interface + impl + struct + global)
materializes TypeInfo + IfaceId + globals (driving the shared `lowerDataGlobals`/`vmOwn`) +
the stack, and passes on hosted, `builder-comp-int`, and `builder-comp_arm32_baremetal` — so
it is a real, non-spurious guard (reverting slice 3 turns it red). Un-deferred as a normal
passing unit test (no xfail-vehicle problem once it passes). NOT covered by the loader-less
harness: the package-descriptor *accessor* path (`emitPackageDescriptorVM`, needs reflect
loaded) and native vtables (need cross-mode dispatch) — both share the same `vmOwn` path.

Each slice: convert allocs to `vmOwn`; confirm via the bare-metal per-class RawAlloc/RawFree
leak dump (under a create-drop-VM loop) that the family now frees; adversarially review for a
**missed alloc site** (→ re-leak) and any **raw borrow dereferenced after its slice frees
within the VM's own operation** (→ UAF; expected: none beyond the documented
holder-responsibility escapes).

## Verification

- Per slice: instrumented bare-metal per-class leak dump shows the family freeing under a
  create-drop-VM loop; no double-free.
- Full `vm` `builder-comp_arm32_baremetal` unit lane stays green.
- Hosted + `int`-mode conformance unchanged (behavior-preserving; the sentinel is untouched,
  so compiled paths and user-visible reflect semantics are identical).
- Add a permanent create-drop-VM bare-metal regression conformance test (like `1209` for the
  allocator) once all families land.

## What stays unchanged

- The compiled/native backend's static image data (immortal) — correct as-is.
- `rt.STATIC_REFCOUNT` — now purely governs user-code reflect-value refcount no-op; NOT
  flipped.

## Superseded (v1)

The per-path OWN/BORROW classification and the "uniform sentinel no-op, refcount every block
in place" mechanism are abandoned — they failed for header-less / address-identity /
multi-block records and for descriptors that have no owning root (see the review findings in
commit `569ad91c`). The lifetime model and the allocation-site inventory carry over.

## Related

- Dominant stack leak already fixed: main `7f029699c` (the `stackBuf` pattern this design
  generalizes).
- Root cause + audit: `claude-todo.md` ("vm bare-metal LEAK …").
- `native/arm32` bare-metal unit lane is still xfail'd (separate analogous raw-fixture leak).
