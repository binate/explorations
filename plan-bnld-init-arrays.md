# Plan: bnld runs constructors — init / fini arrays (ELF) and __mod_init_func (Mach-O)

Item 1 of the claude-todo MAJOR "bnld input readers" (claimed 2026-10-09, work-2).  Scope (user, 2026-10-10:
"ELF + Mach-O, all"): a short series, ELF first.  Unwind tables (x86-64 `.eh_frame`, arm32 `.ARM.exidx`) are
their own todo item ("bnld: unwind tables"; user: "Support both, own item").

## What is wrong

- bnld's ELF reader keeps only SHF_ALLOC PROGBITS / NOBITS sections: a C object's `.init_array` (SHT_INIT_ARRAY,
  from `__attribute__((constructor))` at -O0 — clang folds a constructor that only stores constants into the
  global's initial value at -O1), `.fini_array`, `.preinit_array` are dropped with their relocations, so
  constructors silently never run.
- A symbol defined in a dropped section reads as undefined (SecIndex -1): a reference to it from a kept section
  binds by name to another object's global, or (a local) resolves to 0.
- Mach-O: `parse_macho.bn` reads `__DATA,__mod_init_func` (S_MOD_INIT_FUNC_POINTERS, 0x9) and
  `__mod_term_func` (0xa) as plain data, and the dynamic Mach-O writer emits no `section_64` records at all
  (`writeSeg64`: nsects 0), so dyld never finds them.
- `.ctors` / `.dtors` (an older GCC's PROGBITS form) are read as ordinary data: they would never run either —
  bnld links no crtbegin to walk them.

## Facts that shape the design

- bnld's ELF output is never PIE (ET_EXEC; `t.PIE` is set only by the Mach-O driver), so array entries — absolute
  pointers — need no dynamic relocations on ELF.
- In a dynamic ELF link bnld's own `_start` calls `__libc_start_main(main, …, init = 0, fini = 0, …)`; glibc
  (2.34+) then runs the executable's DT_INIT_ARRAY via `call_init` and DT_FINI_ARRAY at exit, and `_dl_init`
  runs its DT_PREINIT_ARRAY.  So the dynamic tags are what makes constructors run there.
- A static ELF link (`Link`) and the scripted builder have no libc start code that walks the arrays; what they
  can do is define `__init_array_start` / `_end` (and the preinit / fini pairs), as lld does, for whatever start
  code the program brings.
- The scripted builder discards an input section its script does not place unless something references it; an
  init array is referenced by nothing (start code reaches it through the bracketing symbols), so an unplaced one
  would vanish silently.
- The assembler's ELF writer makes every section ALLOC PROGBITS / NOBITS, so unit-test fixtures for the reader
  patch a written object's section type (as the malformed-relocation tests do).

## Series

1. ✅ landed binate `541fabde3` (2026-10-10).  Also, from its review: clang's arm32 array entries are
   R_ARM_TARGET1, now patched as ABS32 (lld's / GNU ld's arm-linux default); a rejection is the object's
   `Rejected`, so an archive member fails a link only if the link pulls it in (`ReadArchive` still rejects one);
   reader errors name the input.  (The array keeps its input flags; layout places it by type in (2).)
   **Reader.**  Keep ALLOC SHT_INIT_ARRAY / FINI_ARRAY / PREINIT_ARRAY sections (type kept, flags read + write).
   Reject an input `.ctors*` / `.dtors*` section ("bnld does not run .ctors / .dtors; build with
   -fuse-init-array").  Reject a relocation in a kept section whose symbol is defined in a section the reader
   drops, and a global / weak symbol defined in a dropped *allocatable* section (a debug section's symbols are
   local and referenced only from debug relocations, which stay unapplied).  Until (2) lands the kept arrays are
   laid out as ordinary data (still not run): (1) and (2) land together or (2) first.
2. ✅ landed binate `147bca034` (2026-10-10).  Also, from its review: bnc's hosted ELF `_start` passes an `init`
   that walks `__init_array_start..__init_array_end` (glibc before 2.34 runs only that — ld.so skips the
   executable's own DT_INIT_ARRAY — and bnld writes no symbol versions, so a binary would load there and skip its
   constructors; 2.34+ runs a non-null init instead of DT_INIT_ARRAY, so they run once either way).  e2e `ctors`
   ran on aarch64 and x86-64 Linux.
   **Layout, symbols, ELF drivers.**  Output kinds `.preinit_array`, `.init_array`, `.fini_array` in the
   writable group before `.data` (lld's order); within `.init_array` / `.fini_array`, inputs sorted by the
   priority suffix (`.init_array.N`, ascending; no suffix = 65536, lld's default), stable.  Linker-defined
   `__{preinit,init,fini}_array_{start,end}` (AbsSym, as `_GLOBAL_OFFSET_TABLE_`) when referenced, start = end
   for an empty array.  The dynamic ELF driver writes DT_PREINIT_ARRAY / DT_PREINIT_ARRAYSZ (32 / 33),
   DT_INIT_ARRAY / DT_INIT_ARRAYSZ (25 / 27), DT_FINI_ARRAY / DT_FINI_ARRAYSZ (26 / 28) for each non-empty array.
   Tests: layout / symbol unit tests; an e2e dynamic link (linux-x64 and linux-aarch64, the latter runnable here
   in the `binate-ci` image) of a clang C object with a constructor and a destructor, checking both run.
3. **Scripted builder.**  An input init / fini / preinit array the script does not place is an error (its
   constructors would silently not run); the script places it and may define the bracketing symbols with
   SymbolAtDot.  Check the drivers in `drivers/`.
4. **Mach-O.**  The reader keeps a section's S_MOD_INIT_FUNC_POINTERS / S_MOD_TERM_FUNC_POINTERS type; layout
   gives each its own output section in the writable group; the dynamic Mach-O writer emits `section_64` records
   for them in `__DATA` with that type (the pointers are rebased like other absolute pointers there).  e2e: a
   bnld-linked macOS program with a C constructor (runs on this host).
