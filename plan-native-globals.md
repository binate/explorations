# Plan: Globals in `pkg/native/arm64` — ADRP + PAGEOFF12

Package-level variable support for the native arm64 backend. Attempted
once during the slice/strings push; punted because it needs asm
infrastructure the arm64 emitter alone can't provide. This is a plan
for picking it up cleanly.

## What's already in place

- IR represents a global reference as a pseudo-`OP_ALLOC` with
  `ID == -1`, `StrVal = global name`, `Typ = *T`, `TypeArg = T`
  (see `pkg/ir/gen_stmt.bn` `lookupVar` fallback).
- LLVM backend emits `@bn_<pkg>__<name> = global <type> <init>` for
  each `mod.Globals` entry and references it through `emitPtrRef`.
- `pkg/asm/macho` maps section names to `__TEXT` / `__DATA` /
  `__DATA,__bss`; "data" → `__DATA,__data` already works.

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

### 1. New fixup kinds in `pkg/asm/aarch64`

Two new entries in the `FIX_*` enum (declared in `pkg/asm/aarch64.bni`,
implemented in `pkg/asm/aarch64/aarch64_sys.bn`):

- `FIX_ADD_LO12` — the `imm12` field of `ADD (immediate)`, written
  with the low 12 bits of the target address (no scaling).
- `FIX_LDR_LO12` — the scaled `imm12` field of `LDR/STR (immediate)`,
  written with the low 12 bits of the target shifted right by the
  transfer-size log2 (3 for 64-bit, 2 for 32-bit, etc.). For this
  skeleton we only need the 64-bit LDR case.

Neither is PC-relative (`machoRelocPCRel` returns 0). Both are resolved
at link time; we emit them as relocations, not in-place ResolveFixups
patches, because the target page offset is only known after the linker
places `__DATA`.

Emit wrappers in `aarch64_arith.bn` / `aarch64_branch.bn`:

- An `AddImmLabel(a, sf, rd, rn, label)` — encodes `ADD rd, rn, #0`
  and adds a `FIX_ADD_LO12` against `label`.
- An `LdrImmLabel(a, sf, rt, base, label)` — encodes `LDR rt, [base, #0]`
  and adds a `FIX_LDR_LO12`.

### 2. Mach-O relocation emission

`pkg/asm/macho/macho.bn` already declares `ARM64_RELOC_PAGEOFF12 = 4`.
Wire it up in the two mapping helpers:

- `machoRelocType(fixKind, CPU_TYPE_ARM64)`:
  - `FIX_ADD_LO12` → `ARM64_RELOC_PAGEOFF12`
  - `FIX_LDR_LO12` → `ARM64_RELOC_PAGEOFF12`
- `machoRelocLength`: both are 4-byte instructions (`log2(4) = 2`),
  same as the existing ADR/ADRP entries.
- `machoRelocPCRel`: both return 0 (not PC-relative).

### 3. New `data` section in the native emitter

`pkg/native/arm64/arm64.bn`:

- After `emitStringTable` and the in-text content, switch to a "data"
  section via `asm.SetSection(a, "data", -1)`.
- `emitGlobals` defines each global's `_bn_<pkg>__<name>` label and
  emits `types.SizeOf(g.Typ)` zero bytes (rounded up to 8 for a
  clean layout). `pkg/asm/macho` will map the section to
  `__DATA,__data` automatically.
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
    aarch64.Adrp(a, rd, label)                 // existing FIX_ADRP_HI21
    aarch64.AddImmLabel(a, true, rd, rd, label) // new FIX_ADD_LO12
}
```

`emitGlobalAddr` callers (`emitLoad`, `emitStore`, `emitStructCopy`,
`emitGetFieldPtr`, `emitGetElemPtr`) keep the shapes I'd already
drafted — the only behavioral change is that `rd` now contains the
full runtime address by construction, no ADR gymnastics needed.

### 5. Align section alignment

`pkg/asm/macho/macho.bn` currently hardcodes `bbWriteU32(bb, 2)` for
every section's align field (= 2^2 = 4). For the `data` section that
holds 64-bit globals, bump to 3 (= 8) so LDR Xt doesn't SIGBUS. The
cleanest shape is a per-section align query (look at
`a.Sections[i].Name` and return 3 for data, 2 for text), not a global
change. The walking skeleton only touches `text` and `data`, so a
two-branch helper suffices.

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
