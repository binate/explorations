# Plan: emit `LC_DYSYMTAB` + sort symbol table in `pkg/asm/macho`

**Status**: design / not started
**Severity**: MAJOR (ABI/spec compliance; not blocking anything today)
**Tracks**: no claude-todo entry yet — file one when starting

## Problem

`pkg/asm/macho/macho.bn` currently emits three load commands:
`LC_SEGMENT_64`, `LC_SYMTAB`, `LC_BUILD_VERSION`.  It does NOT emit
`LC_DYSYMTAB`, and it does NOT sort its symbol table.

The Mach-O ABI specifies that an object file's symbol table is
ordered as: local symbols → externally-defined symbols → undefined
external symbols.  `LC_DYSYMTAB` declares the index/length of each
range so the linker (and other tools — `dsymutil`, `dyld`,
`otool -Iv`) can walk the table efficiently and apply per-class
processing (e.g., visibility, dead-strip, indirect-symbol
resolution).

clang/llvm always emits both: sorted symbol table + `LC_DYSYMTAB`.
The modern Apple linker (`ld-1266.8`, Xcode 16+) sometimes falls
back to legacy heuristics when `LC_DYSYMTAB` is absent — and may
process weak-data coalescing differently in that path.  Even
when `LC_DYSYMTAB` is present but the symtab is unsorted, some
tools misreport ranges.

### Why this is NOT a blocker today

The dtor-vt duplicate-symbol cluster (which led to discovering
this gap) has a separate root cause: codegen was emitting weak
`__vt` definitions in every consumer package, and the modern
linker refuses to coalesce them regardless of `LC_DYSYMTAB`.
That bug was fixed via `pkg/binate/native/{aarch64,x64}`'s
`IsLinkOnce` pre-pass + `IsExtern` lookup gate (binate
`94b75294` + `daf51bf1`) — owner-only emission means duplicates
never reach the linker.

Empirical verification (2026-05-30): adding `LC_DYSYMTAB` +
sorting + canonical load-command order to our `.o` files does
NOT make the duplicate-data symbols coalesce.  The owner-only
emission rule fixes the dedup question; this plan addresses a
parallel spec-compliance gap.

So: this work is for ABI conformance and downstream tool
correctness, not to unblock the dtor-vt cluster.

## Fix shape

Two coordinated changes to `pkg/asm/macho/macho.bn`:

1. **Sort the symbol table** into the canonical order:
   - **Local symbols** first: `n_type & N_EXT == 0` (every L_…
     debug label, internal-only constants, etc.).
   - **Externally-defined symbols** next: `N_EXT` set,
     `N_TYPE != N_UNDF` (n_sect > 0; the symbol has a
     definition in this TU).
   - **Undefined external symbols** last: `N_EXT` set,
     `N_TYPE == N_UNDF` (`n_sect == 0`; only a reference in
     this TU, defined elsewhere).

   `pkg/asm/elf/elf.bn` already does the analogous sort
   (`elf.bn:184` — "Build sorted symbol order: locals first,
   then globals").  Mirror that approach.

2. **Update relocation indices** to track the new symbol order.
   Each relocation entry's `r_symbolnum` (low 24 bits of the
   second word) is a 0-based index into the symbol table when
   `r_extern == 1`.  After sorting, build an
   `oldIdx → newIdx` map and rewrite every relocation's
   `r_symbolnum`.  Non-external relocations (`r_extern == 0`)
   reference section indices, not symbol indices — leave those
   alone.

3. **Emit `LC_DYSYMTAB`** as a 4th load command.  Layout (after
   the 8-byte cmd/cmdsize header, all u32):

   ```
   ilocalsym, nlocalsym,
   iextdefsym, nextdefsym,
   iundefsym, nundefsym,
   tocoff, ntoc,           ; 0 / 0 — no MH_DYLIB table-of-contents
   modtaboff, nmodtab,     ; 0 / 0 — no module table
   extrefsymoff, nextrefsyms,  ; 0 / 0 — no external reference table
   indirectsymoff, nindirectsyms,  ; 0 / 0 — no indirect symbol table
   extreloff, nextrel,     ; 0 / 0 — sections carry their own relocs
   locreloff, nlocrel      ; 0 / 0 — same
   ```

   = 18 × 4 = 72 bytes payload + 8-byte cmd/cmdsize = 80 bytes total.

4. **Update dependent header / load-command fields**:
   - `mach_header.ncmds` += 1
   - `mach_header.sizeofcmds` += 80
   - `LC_SEGMENT_64.fileoff` += 80 (and `dataOffset` rebases)
   - Each section header's `offset` += 80
   - Each section header's `reloff` += 80 (when nonzero)
   - `LC_SYMTAB.symoff` += 80, `stroff` += 80
   - Inter-section / pre-data alignment padding may need to
     re-round to 8-byte boundaries after the 80-byte shift —
     verify the existing padding logic still produces aligned
     offsets.

5. **Load-command order**: clang's canonical order is `SEGMENT,
   BUILD_VERSION, SYMTAB, DYSYMTAB`.  Our current order is
   `SEGMENT, SYMTAB, BUILD_VERSION`.  Adopting clang's order
   isn't strictly required (the spec doesn't mandate it) but
   matches every observable producer.  Do it for parity.

## Sites to touch

`pkg/asm/macho/macho.bn` — the only file affected.  Specifically:

- The `EmitObject` function (or whatever the entry point is in
  the current source) needs:
  - A sort pass + reloc-remap helper.
  - A new function `writeDysymtabCmd(bb, ilocal, nlocal,
    iextdef, nextdef, iundef, nundef) → BinBuf`.
  - Updated `ncmds` / `sizeofcmds` / data-offset computations.
  - Reordered load-command emission.

## Tests to add

`pkg/asm/macho/macho_test.bn` (or `pkg/asm/macho/macho_dysymtab_test.bn`
if a separate file fits the existing naming pattern):

1. **`TestSymtabSorted`**: build an Assembler with a mix of local,
   external-def, and undef-extern symbols added in arbitrary
   order.  After `EmitObject`, read back the symbol table and
   assert the canonical ordering.

2. **`TestDysymtabRangesMatchSymtab`**: same setup; parse the
   `LC_DYSYMTAB` ranges and confirm they match the actual
   counts.

3. **`TestRelocSymbolIndicesRemapped`**: add a relocation that
   references an external symbol, then add more local symbols
   between (forcing a reorder).  After `EmitObject`, parse the
   relocation and confirm its `r_symbolnum` points at the
   correct (post-sort) symbol index.

4. **`TestLoadCommandOrderMatchesClang`**: parse the load
   commands of an emitted `.o` and confirm the order
   `LC_SEGMENT_64, LC_BUILD_VERSION, LC_SYMTAB, LC_DYSYMTAB`.

5. **Round-trip via clang**: build a small `.o`, link it via
   `clang` (using the `link-and-run` test pattern already in
   `pkg/binate/asm/macho` if available), confirm the link
   succeeds.

## Risk + rollback

- The change is internal to the Mach-O writer; no other
  package's interface changes.
- All existing tests for `pkg/asm/macho` must continue to pass
  (they test things like relocation correctness, which depends
  on `r_symbolnum` being right after sorting).
- Risk: if the reloc-index remap is wrong, every cross-symbol
  reference in compiled code becomes garbage.  The existing
  tests should catch this, but add explicit pinning per the
  test list above.
- Rollback: revert the commit.  No on-disk format change for
  consumers — `LC_DYSYMTAB` is universally supported; sorted
  symtab is what every other producer emits.

## Phasing

Single atomic commit.  The four sub-changes (sort, remap, emit,
reorder LCs) all touch the same `EmitObject` flow and have to
land together.

## Estimated LOC

~150-250 in `pkg/asm/macho/macho.bn` + ~150-250 in tests.

## Out of scope

- The dtor-vt duplicate-symbol cluster was already fixed
  separately (binate `94b75294` + `daf51bf1`) via owner-only
  emission in `pkg/binate/native/{aarch64,x64}`.  This plan does
  NOT touch that path.
- ELF writer parity — already has the sort (no LC_DYSYMTAB
  equivalent in ELF; the analogous structure is just the
  `Elf64_Shdr.sh_info` field for `SHT_SYMTAB`, which our ELF
  writer already populates).
- Other Mach-O load commands clang sometimes emits
  (`LC_DATA_IN_CODE`, `LC_LINKER_OPTION`, `LC_FUNCTION_STARTS`,
  etc.) — none of these are needed for object files and clang
  itself omits them in minimal `.o` outputs.
