# Plan: Linker Kit Written in Binate

## Goal

Replace the dependency on system linkers (`cc`, `ld`, `arm-none-eabi-ld`) with a
linker library and tool written in Binate. This completes the self-hosted
toolchain: Binate source → compiler → assembler → object file → **linker** →
executable. No external tools needed beyond the OS kernel.

**Priority**: Low. Wait until language support is more complete, bugs are ironed
out, and we have a standard library (especially growable collections and string
utilities).

## Key Design Idea: Linker as Library, Binate as Linker Script

Traditional linkers embed a domain-specific scripting language (GNU ld's linker
scripts) to let users control layout decisions — base addresses, section ordering,
memory regions, etc. This language is notoriously arcane and limited.

Binate already has an interpreted mode that interoperates seamlessly with compiled
code via function pointers. We can exploit this:

- **`pkg/link`** is a compiled library providing the core machinery: object file
  parsing, symbol resolution, relocation patching, binary emission primitives.
- **Layout policy** is expressed as ordinary Binate code that calls into `pkg/link`.
  It decides base addresses, section ordering, segment grouping, output format, etc.
- **Standard drivers** ship as `.bn` files for common targets (Linux ELF x86-64,
  macOS Mach-O arm64, bare-metal ARM32, etc.).
- **Custom layouts** are just user-written `.bn` files — loaded and interpreted at
  link time. The user has the full language available (conditionals, loops,
  arithmetic, string manipulation) to express arbitrarily complex layout logic.

The `bnld` command is thin: it parses arguments, loads the driver (compiled
built-in or interpreted user-provided), and calls into it.

This means:
- No linker script DSL to design, parse, or maintain
- Bare-metal is not a special case — it's just a simpler driver
- Hosted executables (ELF, Mach-O) are drivers that encode OS conventions
- The hot path (relocation patching, binary I/O) is compiled; the policy layer
  (which runs once per link) can be interpreted with no performance concern

## Scope

### In scope (v1)

- **Static linking only** — no shared libraries, no dynamic linking, no PLT/GOT
- **Relocatable object files as input** — ELF32, ELF64, Mach-O (little-endian only)
- **Output formats** — ELF executables (Linux), Mach-O executables (macOS),
  flat binary (bare-metal)
- **Architectures**: AArch64, ARM32, x86-64
- **Features**: symbol resolution, relocation patching, section merging, entry
  point, static BSS allocation
- **Archive support** — `.a` static libraries (ar format), for linking against
  libc or runtime libraries
- **Driver model** — built-in drivers for standard targets, user-provided `.bn`
  files for custom layouts

### Out of scope (v1)

- Dynamic linking (shared objects, dylibs, PLT/GOT, lazy binding)
- Link-time optimization (LTO)
- Debug info (DWARF) — pass through but don't process
- Incremental/partial linking
- Cross-compilation (linker runs on same arch it targets)
- Thin archives
- Version scripts, symbol visibility beyond local/global/weak

## Architecture

The linker has two layers: a **library** (compiled, performance-sensitive) and a
**driver** (the layout policy — compiled or interpreted).

### Library Layer (`pkg/link`)

Provides building blocks that drivers call:

```
Parse:    ReadELF(path) → InputObject
          ReadMachO(path) → InputObject
          ReadArchive(path) → *[]InputObject  (with lazy member selection)

Resolve:  BuildSymbolTable(objects) → SymbolTable
          ResolveSymbols(table) → errors

Layout:   CreateOutputSection(name, flags, alignment) → OutputSection
          PlaceSection(input, output, address)
          AssignAddresses(sections, baseAddr)

Patch:    PatchRelocations(objects, symtab)

Emit:     EmitELFExec(sections, entry, path)
          EmitMachOExec(sections, entry, path)
          EmitFlatBinary(sections, path)
```

### Driver Layer

A driver is a Binate function (compiled or interpreted) that orchestrates a link.
Conceptually:

```
func linkLinuxX64(inputs *[]InputObject, output *[]char) {
    // Merge sections by name
    text := link.CreateOutputSection(".text", SF_READ|SF_EXEC, 16)
    data := link.CreateOutputSection(".data", SF_READ|SF_WRITE, 8)
    bss  := link.CreateOutputSection(".bss",  SF_READ|SF_WRITE, 8)
    // ... merge input sections into output sections ...

    // Assign addresses (Linux x86-64 convention)
    link.AssignAddresses(sections, 0x400000)

    // Resolve and patch
    symtab := link.BuildSymbolTable(inputs)
    link.ResolveSymbols(symtab)
    link.PatchRelocations(inputs, symtab)

    // Emit
    entry := link.LookupSymbol(symtab, "_start")
    link.EmitELFExec(sections, entry, output)
}
```

A bare-metal ARM32 driver is simpler:

```
func linkBareMetal(inputs *[]InputObject, output *[]char) {
    text := link.CreateOutputSection(".text", SF_READ|SF_EXEC, 4)
    data := link.CreateOutputSection(".data", SF_READ|SF_WRITE, 4)
    // ... merge ...

    link.AssignAddresses(sections, 0x40000000)  // QEMU virt load address

    symtab := link.BuildSymbolTable(inputs)
    link.ResolveSymbols(symtab)
    link.PatchRelocations(inputs, symtab)

    link.EmitFlatBinary(sections, output)
}
```

Users can write arbitrary drivers — scatter-loading for embedded, multiple
memory regions, custom section ordering, gap-filling, checksums, etc.

### Pipeline

```
                  ┌─────────┐
                  │  Input   │  .o files (ELF/Mach-O), .a archives
                  └────┬────┘
                       │
                  ┌────▼────┐
                  │  Parse   │  Library: read objects into InputObject structs
                  └────┬────┘
                       │
                  ┌────▼──────────┐
                  │  Driver       │  Policy: merge sections, assign addresses,
                  │  (Binate code)│  choose output format, set entry point
                  └────┬──────────┘
                       │
                  ┌────▼─────┐
                  │  Resolve  │  Library: match undefined symbols to definitions
                  └────┬─────┘
                       │
                  ┌────▼────┐
                  │  Patch   │  Library: apply relocations using resolved addresses
                  └────┬────┘
                       │
                  ┌────▼────┐
                  │  Emit    │  Library: write output in chosen format
                  └─────────┘
```

## Data Structures

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
    OutIndex  int        // assigned output section index (set by driver)
    OutOffset uint       // offset within output section (set by driver)
}

type InputSymbol struct {
    Name     @[]char
    Section  int          // -1 = undefined
    Offset   uint
    Binding  int          // local/global/weak
    Resolved uint         // final virtual address (filled during resolve)
}

type InputReloc struct {
    Section int          // input section containing the reference
    Offset  uint         // byte offset within that section
    Symbol  int          // index into InputSymbol
    Type    int          // architecture-specific relocation type
    Addend  int
}

type OutputSection struct {
    Name      @[]char
    Flags     int
    Alignment int
    BaseAddr  uint       // virtual address (set by driver)
    Data      @[]uint8   // merged data from inputs
    Len       int
    FileSize  int        // may differ from Len for BSS
}

type SymbolTable struct {
    // Global symbol index — maps names to definitions
    // (linear scan for v1, hash map when available)
    Symbols  @[]GlobalSymbol
    NumSyms  int
}

type GlobalSymbol struct {
    Name    @[]char
    Object  int          // which InputObject defines it
    SymIdx  int          // index into that object's Symbols
    Binding int
    Addr    uint         // resolved virtual address
}
```

## Package Structure

```
pkg/link/              — linker library (compiled)
    link.bn            — orchestration, top-level Link() function
    link.bni           — public interface
    parse_elf.bn       — ELF object file reader (ELF32 + ELF64)
    parse_macho.bn     — Mach-O object file reader
    parse_ar.bn        — ar archive reader
    resolve.bn         — symbol resolution and table building
    layout.bn          — output section creation and address assignment
    relocate.bn        — relocation patching (per-arch dispatch)
    emit_elf.bn        — ELF executable writer
    emit_macho.bn      — Mach-O executable writer
    emit_flat.bn       — flat binary writer
    link_test.bn       — unit tests

pkg/link/drivers/      — standard link drivers (can be compiled or interpreted)
    linux_x64.bn       — Linux x86-64 ELF executable
    linux_aarch64.bn   — Linux AArch64 ELF executable
    linux_arm32.bn     — Linux ARM32 ELF executable
    macos_aarch64.bn   — macOS arm64 Mach-O executable
    macos_x64.bn       — macOS x86-64 Mach-O executable
    bare_arm32.bn      — bare-metal ARM32 flat binary

cmd/bnld/              — linker command
    main.bn            — argument parsing, driver selection/loading
```

## CLI

```
bnld -o output [-target target] [-driver file.bn] input1.o input2.o libfoo.a
```

Flags:
- `-o output` — output executable path (required)
- `-target linux-x64|linux-aarch64|linux-arm32|macos-aarch64|macos-x64` —
  select built-in driver (auto-detect from input objects if omitted)
- `-driver file.bn` — use a custom driver (interpreted at link time)
- `-e entry` — override entry point symbol
- `-base addr` — override base address (passed to driver)

When `-driver` is specified, the given `.bn` file is loaded by the interpreter
and its `link` function is called with the parsed inputs and options. The driver
has full access to the `pkg/link` library API.

## Relocation Types

Architecture-specific relocation types the library must handle (already defined
in the assembler):

**AArch64**:
- `R_AARCH64_ABS64` (257) — 64-bit absolute
- `R_AARCH64_ABS32` (258) — 32-bit absolute
- `R_AARCH64_JUMP26` (282) — 26-bit PC-rel branch
- `R_AARCH64_CONDBR19` (280) — 19-bit PC-rel conditional branch
- `R_AARCH64_ADR_PREL_LO21` (274) — 21-bit PC-rel ADR
- `R_AARCH64_ADR_PREL_PG_HI21` (275) — page-relative ADRP
- `R_AARCH64_TSTBR14` (279) — 14-bit PC-rel TBZ/TBNZ

**ARM32**:
- `R_ARM_ABS32` (2) — 32-bit absolute
- `R_ARM_JUMP24` (29) — 24-bit PC-rel, pipeline-adjusted

**x86-64**:
- `R_X86_64_64` (1) — 64-bit absolute
- `R_X86_64_PC32` (2) — 32-bit PC-relative
- `R_X86_64_32` (10) — 32-bit absolute (truncated)

**Mach-O**:
- `ARM64_RELOC_BRANCH26` (2), `ARM64_RELOC_PAGE21` (3),
  `ARM64_RELOC_PAGEOFF12` (4)
- `X86_64_RELOC_BRANCH` (2), `X86_64_RELOC_SIGNED` (1),
  `X86_64_RELOC_UNSIGNED` (0)

## Incremental Plan

The ordering follows the "bare-metal is the base case, hosted is the
specialization" principle:

### Step 1: Object file readers

Write `parse_elf.bn` (ELF32 + ELF64) and test with objects produced by the
assembler. Round-trip test: assemble → write ELF → read ELF → verify sections,
symbols, relocations match.

### Step 2: Core linker (symbol resolution + relocation patching)

Implement `resolve.bn` and `relocate.bn`. Test with multi-object inputs that
have cross-references.

### Step 3: Flat binary output + bare-metal driver

Simplest output format — just concatenated section data. Write `bare_arm32.bn`
driver. Validate by running on QEMU semihosting (replacing `arm-none-eabi-ld`).

### Step 4: ELF executable output + Linux drivers

Add program headers, segments, entry point. Write `linux_x64.bn` and
`linux_aarch64.bn` drivers. Validate by running on Linux.

### Step 5: Archive support

Parse `.a` files, implement archive member selection.

### Step 6: Mach-O support

Add Mach-O object reader and executable writer. Write macOS drivers. Handle
code signing (invoke `codesign` or embed minimal ad-hoc signature).

### Step 7: Interpreted driver loading

Wire up the interpreter so `bnld -driver custom.bn` works — the custom driver
is interpreted at link time, calling into the compiled library.

## Dependencies and Prerequisites

**Language features needed**:
- File I/O: `bootstrap.Open`, `bootstrap.Read`, `bootstrap.Close` — already
  available. Need to read binary files into `@[]uint8` buffers.
- Growable byte buffers: `pkg/buf` (CharBuf) exists but is char-oriented. May
  need a byte-oriented variant, or use managed-slices directly.
- String comparison and lookup: currently manual. A hash map from the standard
  library would help symbol resolution performance but isn't strictly necessary.

**What should exist first**:
- Standard library with growable collections (at minimum, a growable `@[]uint8`
  and some kind of map/dictionary)
- String utilities (comparison, searching, formatting)
- Stable compiler — the linker will be one of the more complex Binate programs;
  compiler bugs need to be minimal
- Dual-mode interop working reliably (for interpreted drivers calling compiled
  library code)
- Possibly: a binary reader utility (read uint16/uint32/uint64 from byte buffer
  at offset, little-endian)

## Testing Strategy

- **Unit tests**: parse known object files, verify extracted symbols/relocs
- **Round-trip tests**: assemble → write object → read object → verify match
- **End-to-end tests**: assemble → link → run → check exit code (same pattern as
  existing assembler e2e tests, but replacing `cc`/`ld` with `bnld`)
- **Cross-object tests**: two objects with external references → link → run
- **Driver tests**: verify each standard driver produces working executables
- **Comparison tests**: link with both `bnld` and system linker, compare behavior

## Risks and Open Questions

1. **Mach-O code signing**: arm64 macOS requires ad-hoc code signing
   (`codesign -s -`) for executables to run. The linker may need to invoke
   `codesign` as a post-step, or embed a minimal signature. Consider ELF-only
   initially.

2. **Mach-O complexity**: Mach-O load commands, dyld info, and two-level
   namespaces are significantly more complex than ELF.

3. **Interpreted driver performance**: Not a concern — the driver runs once per
   link and does O(sections) work. The hot path (relocation patching) is compiled.

4. **Missing language features**: Without a hash map, symbol lookup is O(n) per
   reference. Fine for hundreds of symbols; a sorted array with binary search
   is a reasonable intermediate step for thousands.

5. **ASLR/PIE**: Modern Linux defaults to position-independent executables.
   Static non-PIE (`-static -no-pie`) is sufficient for v1. PIE requires
   GOT/PLT support.

6. **Driver API stability**: The library API exposed to drivers needs to be
   reasonably stable, since users may write custom drivers. Design the API
   carefully before shipping.

7. **Interpreter integration**: Loading and calling an interpreted `.bn` file
   from a compiled program requires the interpreter to be available as a
   library. This is the bytecode VM (`pkg/vm`), which will need a clean
   "eval file and call function" entry point.

## Effort Estimate

- Step 1 (object readers): Medium
- Step 2 (core linker): Medium
- Step 3 (flat binary + bare-metal): Small — simplest output, validates core
- Step 4 (ELF executable): Medium — program headers, segments
- Step 5 (archives): Small — ar format is simple
- Step 6 (Mach-O): Large — complex format, code signing
- Step 7 (interpreted drivers): Small-Medium — depends on interpreter API

Total: probably a week+ of focused work for a basic ELF static linker with
driver model, assuming the language and standard library are ready.
