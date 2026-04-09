# Plan: Linker Written in Binate

## Goal

Replace the dependency on system linkers (`cc`, `ld`, `arm-none-eabi-ld`) with a
linker written in Binate. This completes the self-hosted toolchain: Binate source
→ compiler → assembler → object file → **linker** → executable. No external tools
needed beyond the OS kernel.

**Priority**: Low. Wait until language support is more complete, bugs are ironed
out, and we have a standard library (especially growable collections and string
utilities).

## Scope

### In scope (v1)

- **Static linking only** — no shared libraries, no dynamic linking, no PLT/GOT
- **Relocatable object files as input** — ELF32, ELF64, Mach-O (little-endian only)
- **Executable output** — ELF executables (Linux) and Mach-O executables (macOS)
- **Architectures**: AArch64, ARM32, x86-64
- **Features**: symbol resolution, relocation patching, section merging, entry
  point, static BSS allocation
- **Archive support** — `.a` static libraries (ar format), for linking against
  libc or runtime libraries

### Out of scope (v1)

- Dynamic linking (shared objects, dylibs, PLT/GOT, lazy binding)
- Link-time optimization (LTO)
- Debug info (DWARF) — pass through but don't process
- Linker scripts (beyond hardcoded layout)
- Incremental/partial linking
- Cross-compilation (linker runs on same arch it targets)
- Thin archives
- Version scripts, symbol visibility beyond local/global/weak

## Architecture

```
                  ┌─────────┐
                  │  Input   │  .o files (ELF/Mach-O), .a archives
                  └────┬────┘
                       │
                  ┌────▼────┐
                  │  Parse   │  Read object headers, sections, symbols, relocations
                  └────┬────┘
                       │
                  ┌────▼─────┐
                  │  Resolve  │  Match undefined symbols to definitions
                  └────┬─────┘
                       │
                  ┌────▼────┐
                  │  Layout  │  Assign virtual addresses to sections
                  └────┬────┘
                       │
                  ┌────▼────┐
                  │  Patch   │  Apply relocations using resolved addresses
                  └────┬────┘
                       │
                  ┌────▼────┐
                  │  Emit    │  Write executable (ELF/Mach-O)
                  └─────────┘
```

### Phase 1: Parse Object Files

Read relocatable object files and extract:
- Section table (name, type, flags, data, alignment)
- Symbol table (name, section, offset, binding, type)
- Relocation entries (section, offset, symbol, type, addend)

For ELF: parse ELF header → section headers → .strtab/.shstrtab → .symtab →
.rela.* sections. Handle both ELF32 and ELF64 (different struct sizes, field
order in Sym, r_info encoding).

For Mach-O: parse Mach-O header → load commands → LC_SEGMENT_64 (sections) →
LC_SYMTAB (nlist + strtab) → section relocation entries. Handle both arm64 and
x86_64 relocation semantics.

For archives (.a): parse ar header, member headers, extract object files by
name. Only pull in members that define symbols referenced by other inputs
(archive semantics — don't link everything).

**Data structures**:
```
type InputObject struct {
    Path     @[]char
    Sections @[]InputSection
    Symbols  @[]InputSymbol
    Relocs   @[]InputReloc
    // ... counts
}

type InputSection struct {
    Name      @[]char
    Flags     int        // read/write/exec
    Data      @[]uint8   // raw bytes (empty for BSS)
    Alignment int
    OutIndex  int        // assigned output section index
    OutOffset uint       // offset within output section
}

type InputSymbol struct {
    Name    @[]char
    Section int          // -1 = undefined
    Offset  uint
    Binding int          // local/global/weak
    Resolved uint        // final virtual address (filled in during resolve)
}

type InputReloc struct {
    Section int          // input section containing the reference
    Offset  uint         // byte offset within that section
    Symbol  int          // index into InputSymbol
    Type    int          // architecture-specific relocation type
    Addend  int
}
```

### Phase 2: Symbol Resolution

Build a global symbol table from all input objects:

1. Collect all global/weak symbols with definitions (section != -1)
2. For each undefined symbol reference, find the definition
3. Handle precedence: strong (global) > weak > undefined
4. Error on multiply-defined strong symbols
5. Error on unresolved undefined symbols (no definition found anywhere)

**Algorithm**: For v1, a simple linear scan is fine given expected input sizes.
When the standard library provides a hash map, switch to that.

Archive member selection: scan undefined symbols; if an archive member defines
one, pull it in (and re-scan, since the new member may introduce new undefineds).
Iterate until no new members are pulled.

### Phase 3: Section Layout

Merge input sections into output sections by name/type:
- All `.text` inputs → one `.text` output
- All `.data` inputs → one `.data` output
- All `.rodata` inputs → one `.rodata` output
- All `.bss` inputs → one `.bss` output (no file backing)

Assign virtual addresses:
- **ELF (Linux)**: start at conventional base (e.g., 0x400000 for x86-64,
  0x10000 for ARM32, 0x400000 for AArch64). Sections placed sequentially with
  alignment padding. Program headers: one PT_LOAD per permission group
  (RX for text, RW for data).
- **Mach-O (macOS)**: single `__TEXT` segment (text + rodata) and `__DATA`
  segment (data + bss). Page-aligned (0x4000 on arm64, 0x1000 on x86-64).

BSS goes at the end of data, occupying address space but no file space (memsz >
filesz in program headers).

Entry point: look up `_start` (ELF) or `_main`/`main` (Mach-O) in the global
symbol table. Error if not found.

### Phase 4: Relocation Patching

For each relocation in each input section:
1. Look up the target symbol's resolved virtual address
2. Compute the value based on relocation type:
   - **PC-relative**: `target + addend - patch_site_address`
   - **Absolute**: `target + addend`
3. Patch the bytes in the output section data

Architecture-specific relocation types (already defined in the assembler):

**AArch64**:
- `R_AARCH64_ABS64` (257) — 64-bit absolute
- `R_AARCH64_ABS32` (258) — 32-bit absolute
- `R_AARCH64_JUMP26` (282) — 26-bit PC-rel, shift right 2, mask into imm26
- `R_AARCH64_CONDBR19` (280) — 19-bit PC-rel, shift right 2, mask into imm19
- `R_AARCH64_ADR_PREL_LO21` (274) — 21-bit PC-rel for ADR
- `R_AARCH64_ADR_PREL_PG_HI21` (275) — page-relative for ADRP
- `R_AARCH64_TSTBR14` (279) — 14-bit PC-rel for TBZ/TBNZ

**ARM32**:
- `R_ARM_ABS32` (2) — 32-bit absolute
- `R_ARM_JUMP24` (29) — 24-bit PC-rel, shift right 2, adjust for pipeline (+8)

**x86-64**:
- `R_X86_64_64` (1) — 64-bit absolute
- `R_X86_64_PC32` (2) — 32-bit PC-relative
- `R_X86_64_32` (10) — 32-bit absolute (truncated)

**Mach-O** (different relocation model — paired relocations, scattered, etc.):
- `ARM64_RELOC_BRANCH26` (2) — branch
- `ARM64_RELOC_PAGE21` (3) — ADRP
- `ARM64_RELOC_PAGEOFF12` (4) — page offset
- `X86_64_RELOC_BRANCH` (2) — 32-bit PC-rel call/jump
- `X86_64_RELOC_SIGNED` (1) — 32-bit PC-rel data
- `X86_64_RELOC_UNSIGNED` (0) — absolute

### Phase 5: Emit Executable

**ELF executable**:
- ELF header (ET_EXEC, entry point, phdr/shdr offsets)
- Program headers (PT_LOAD segments)
- Section data (text, rodata, data — already patched)
- Optional section headers (for debugging; can omit in v1)

**Mach-O executable**:
- Mach-O header (MH_EXECUTE)
- Load commands: LC_SEGMENT_64 (__TEXT, __DATA, __LINKEDIT),
  LC_MAIN (entry point offset), LC_UUID
- Segment data (already patched)

## Package Structure

```
pkg/link/           — linker core
    link.bn         — main link function, orchestration
    link.bni        — public interface
    parse_elf.bn    — ELF object file reader
    parse_macho.bn  — Mach-O object file reader
    parse_ar.bn     — ar archive reader
    resolve.bn      — symbol resolution
    layout.bn       — section merging and address assignment
    relocate.bn     — relocation patching (per-arch dispatch)
    emit_elf.bn     — ELF executable writer
    emit_macho.bn   — Mach-O executable writer
    link_test.bn    — unit tests
```

The existing `pkg/asm/elf/` and `pkg/asm/macho/` packages handle *writing*
object files from the assembler. The linker needs to *read* them — different
code, different package. Some constants (ELF magic, section types, relocation
type numbers) could be shared, but duplicating a few constants is simpler than
creating a shared constants package.

## CLI

```
cmd/bnld/           — linker command
    main.bn         — argument parsing, invokes pkg/link
```

Usage:
```
bnld -o output [-e entry] [-static] input1.o input2.o libfoo.a
```

Flags:
- `-o output` — output executable path (required)
- `-e entry` — entry point symbol (default: `_start` for ELF, `_main` for Mach-O)
- `-static` — (default and only mode in v1)
- `-arch arm32|aarch64|x64` — target architecture (auto-detect from input objects)

## Dependencies and Prerequisites

**Language features needed**:
- File I/O: `bootstrap.Open`, `bootstrap.Read`, `bootstrap.Close` — already
  available. Need to read binary files into `@[]uint8` buffers.
- Growable byte buffers: `pkg/buf` (CharBuf) exists but is char-oriented. May
  need a byte-oriented variant, or use the managed-slice directly.
- String comparison and lookup: currently manual. A hash map from the standard
  library would help symbol resolution performance but isn't strictly necessary.

**What should exist first**:
- Standard library with growable collections (at minimum, a growable `@[]uint8`
  and some kind of map/dictionary)
- String utilities (comparison, searching, formatting)
- Stable compiler — the linker will be one of the more complex Binate programs;
  compiler bugs need to be minimal
- Possibly: a binary reader utility (read uint16/uint32/uint64 from byte buffer
  at offset, little-endian)

## Incremental Plan

### Step 1: ELF object reader

Write `parse_elf.bn` that reads a relocatable ELF object file and populates the
`InputObject` structure. Test with objects produced by the assembler.

### Step 2: Minimal static linker (single object, ELF → ELF)

Link a single `.o` file into an ELF executable. This exercises layout, trivial
symbol resolution (all symbols are local/defined), relocation patching, and ELF
executable emission. Validate by running the output.

### Step 3: Multi-object linking

Extend to handle multiple `.o` files with cross-object references. This is the
core linking use case — symbol resolution across objects, relocation of external
references.

### Step 4: Archive support

Parse `.a` files, implement archive member selection. This allows linking against
static libraries.

### Step 5: Mach-O support

Add Mach-O object reader and executable writer. Mach-O is more complex
(load commands, two-level namespaces, code signing requirements on arm64 macOS)
so this comes after ELF is solid.

### Step 6: ARM32 bare-metal support

Support linker scripts or hardcoded layout for bare-metal ARM32 (entry at
specific address, no program headers needed — just raw binary or minimal ELF).

## Testing Strategy

- **Unit tests**: parse known object files, verify extracted symbols/relocs
- **Round-trip tests**: assemble → write object → read object → verify match
- **End-to-end tests**: assemble → link → run → check exit code (same pattern as
  existing assembler e2e tests, but replacing `cc`/`ld` with `bnld`)
- **Cross-object tests**: two objects with external references → link → run
- **Comparison tests**: link with both `bnld` and system linker, compare behavior

## Risks and Open Questions

1. **Mach-O code signing**: arm64 macOS requires ad-hoc code signing
   (`codesign -s -`) for executables to run. The linker may need to invoke
   `codesign` as a post-step, or embed a minimal signature. Alternatively, just
   support ELF first and let macOS users use the system linker.

2. **Mach-O complexity**: Mach-O load commands, dyld info, and two-level
   namespaces are significantly more complex than ELF. Consider ELF-only for v1.

3. **Memory usage**: The linker loads all object file data into memory. For the
   expected input sizes (small self-hosted programs), this is fine. Not a concern
   until linking large projects.

4. **Missing language features**: Without a hash map, symbol lookup is O(n) per
   reference. With hundreds of symbols this is fine; with thousands it may be
   slow. A sorted array with binary search is a reasonable intermediate step.

5. **ASLR/PIE**: Modern Linux defaults to position-independent executables.
   Static non-PIE executables (`-static -no-pie`) are simpler and sufficient for
   v1, but producing PIE executables requires GOT/PLT support.

6. **Thread-local storage (TLS)**: Not needed until Binate supports threads.

## Effort Estimate

- Step 1 (ELF reader): Medium — binary parsing is tedious but straightforward
- Step 2 (single-object linker): Medium — ELF executable format has many fields
- Step 3 (multi-object): Small — symbol resolution is the core algorithm
- Step 4 (archives): Small — ar format is simple
- Step 5 (Mach-O): Large — Mach-O is complex, code signing is a wildcard
- Step 6 (ARM32 bare-metal): Small — minimal output format

Total: probably a few days of focused work for a basic ELF-only static linker,
assuming the language and standard library are ready.
