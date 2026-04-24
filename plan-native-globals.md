# Plan: Globals in `pkg/native/arm64` — ADRP + PAGEOFF12

Package-level variable support for the native arm64 backend. Attempted
once during the slice/strings push; punted because it needs asm
infrastructure the arm64 emitter alone can't provide. This is a plan
for picking it up cleanly.

## Status

- [x] **Step 1** (`da2640d`, `db66efd`) — `FIX_ADD_LO12` / `FIX_LDR_LO12`
  fixup kinds plus `AddImmLabel` / `LdrImmLabel` wrappers in
  `pkg/asm/aarch64`.
- [x] **Step 2** (`de950c9`) — Mach-O relocation emission.
  `machoRelocType` maps the new kinds to `ARM64_RELOC_PAGEOFF12`. Also
  bundled two correctness fixes discovered via E2E tests:
  - `r_extern=1` is now forced for any globally-visible (`N_EXT`)
    symbol, even when defined in a local section. Apple's ld rejects
    PAGE21/PAGEOFF12 relocations with `r_extern=0`.
  - Symbol `n_value` now carries the absolute VM address (section
    base + offset), not section-local offset. Required whenever the
    object has more than one section.
- [ ] **Step 3** — new `data` section in the native emitter
  (`pkg/native/arm64/arm64.bn`). See below.
- [ ] **Step 4** — wire ADRP+ADD at use sites. See below.
- [x] **Step 5** (`f2beade`) — 8-byte alignment for `__DATA` / `__BSS`
  via `machoSectAlign(name)`.

## What's already in place

- IR represents a global reference as a pseudo-`OP_ALLOC` with
  `ID == -1`, `StrVal = global name`, `Typ = *T`, `TypeArg = T`
  (see `pkg/ir/gen_stmt.bn` `lookupVar` fallback).
- LLVM backend emits `@bn_<pkg>__<name> = global <type> <init>` for
  each `mod.Globals` entry and references it through `emitPtrRef`.
- `pkg/asm/macho` maps section names to `__TEXT` / `__DATA` /
  `__DATA,__bss`; "data" → `__DATA,__data` already works.
- `pkg/asm/aarch64` has the ADRP+ADD and ADRP+LDR wrappers with the
  PAGEOFF12 fixup kinds the use sites need (step 1).
- `pkg/asm/macho` wires those fixups to `ARM64_RELOC_PAGEOFF12`, emits
  the right `r_extern` bit, computes correct `n_value` for symbols in
  non-first sections, and 8-byte aligns `__DATA` (steps 2, 5).
- End-to-end Mach-O tests prove the full pipeline on macOS:
  `TestAdrpAddGlobalE2E`, `TestAdrpLdrGlobalE2E`,
  `TestAdrpAddGlobal64E2E` in `pkg/asm/macho/macho_test.bn`. These
  assemble a program that reads a global via ADRP+ADD+LDR (or
  ADRP+LDR directly), link it, run it, and check the exit code.

## Why the first attempt failed

Naïve approach was to stuff globals at the tail of the text section
and reach them via ADR (21-bit PC-relative, which we already use for
string literals in the same section). Two problems:

1. **Read-only**: `__TEXT` is RX only. A `STR` into a global lives in
   the text section produces `EXC_BAD_ACCESS code=2`
   (KERN_PROTECTION_FAILURE).
2. **Alignment**: even when I padded within our `.o` to 8 bytes, the
   linker concatenates `.o`s using the *section's* declared alignment.
   Our `__text` advertises `align 2^2 = 4`, so the linker can splice
   our section at a 4-byte-aligned address that leaves globals only
   4-byte aligned in the final binary — SIGBUS on 64-bit LDR.

Globals have to live in a writable, 8-byte-aligned section (`__DATA`).
ADR's ±1MB PC-relative range doesn't reach across to a separately
loaded segment, so references need the ADRP+offset pair.

## What the real fix needs

Steps 1, 2, 5 are done. The remaining work is in the native emitter.

### 3. New `data` section in the native emitter

`pkg/native/arm64/arm64.bn`:

- After `emitStringTable` and the in-text content, switch to a "data"
  section via `asm.SetSection(a, "data", -1)`.
- `emitGlobals` defines each global's `_bn_<pkg>__<name>` label and
  emits `types.SizeOf(g.Typ)` zero bytes (rounded up to 8 for a
  clean layout). `pkg/asm/macho` will map the section to
  `__DATA,__data` automatically and mark it `align 2^3`.
- Keep `asm.SetGlobal` so the symbol is exported for cross-section
  relocations; don't revert to `asm.SetSection(a, "text", ...)`
  before `ResolveFixups` (same-section ADR fixups still resolve; the
  new cross-section PAGE21/PAGEOFF12 fixups go to the relocation
  table for the linker).

### 4. Wire ADRP+ADD at use sites

Drop `emitGlobalAddr`'s ADR path. Replace with:

```binate
func emitGlobalAddr(a, pkgName, rd, ins) {
    var label = globalSymFor(pkgName, ins.StrVal)
    asm.SetGlobal(a, label)
    aarch64.Adrp(a, rd, label)                  // existing FIX_ADRP_HI21
    aarch64.AddImmLabel(a, true, rd, rd, label) // FIX_ADD_LO12 (step 1)
}
```

`emitGlobalAddr` callers (`emitLoad`, `emitStore`, `emitStructCopy`,
`emitGetFieldPtr`, `emitGetElemPtr`) keep the shapes I'd already
drafted — the only behavioral change is that `rd` now contains the
full runtime address by construction, no ADR gymnastics needed.

## Test targets unlocked

Phase-1 scope (scalar globals, no initializer expressions):

- 028_global_var — the canonical test.
- 087_global_slice_nil — aggregate global; needs the same path but
  loads the 16/32-byte value, not a scalar.
- 124_global_struct_field — global struct + field access; uses the
  same emit path through `emitGetFieldPtr`.
- 137_return_global_managed_ptr — load a global managed pointer.
- 275_global_mslice_then_loop — same family as 087.

231_return_global_managed_rc mixes globals with refcount subtleties
and probably stays failing until the refcount cluster is handled
independently.

## Out of scope for this plan

- Non-zero initializers (`var x int = 42`) — we're matching the LLVM
  backend which emits `zeroinitializer` and relies on an init phase
  for non-zero values. Running with `0` matches 028; richer init would
  need an `OP_STORE` sequence in a synthesized init function.
- String-literal pre-baked `%BnManagedSlice` globals (plan-native-strings
  TODO) — those would use the same PAGEOFF12 infra once it exists.
- ELF / Linux support — our walking skeleton is Mach-O-only for now.
- x86-64 globals — Mach-O x86-64 uses `GOTPCREL` / `SIGNED`
  relocations; different infra, same shape.
