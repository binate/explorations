# Native backends: DWARF debug info

Status: SCOPING (claimed 2026-11-13, work-3; claude-todo "The native backends emit no debug info").  Until this
lands, bnc rejects `-g` with `--backend native` (binate `44972c53c`).

## What exists (checked 2026-11-13, binate `907e25509`)

- IR: every instruction carries a source line (`Instr.Line`), each function its declaration line (`Func.Line`); no
  column and no file.  The LLVM backend's debug info (codegen/emit_debug*.bn, DWARF 4) builds on these; it names one
  DIFile per module, `<module>.bn` in ".", so in a package of several files its lines do not say which file they are
  in.
- Assembler (pkg/binate/asm): named sections (SetSection), relocations with an addend and a subtracted symbol
  (Mach-O SUBTRACTOR pairs), section symbols.  Neither object writer knows a debug section: no Mach-O `__DWARF`
  segment / S_ATTR_DEBUG, no ELF non-allocated debug PROGBITS.
- The native backends emit no debug sections and no CFI (no .eh_frame / .debug_frame / compact unwind).  Their frames
  keep the frame-pointer chain, so a debugger can still walk the stack.
- bnld drops debug info: an ELF input's non-allocated sections are not kept (link/parse_elf_keep.bn), a Mach-O
  input's stab entries are skipped, and no Mach-O debug map (N_SO / N_OSO / N_FUN stabs, which point lldb and
  dsymutil at the objects' DWARF) is written.  The system linkers (ld64, ld / lld) handle well-formed objects.

## Steps

0. **The IR records each function's source file** (IR-gen), so line tables can name the right file; the LLVM
   backend then emits a DIFile per source file.
1. **Debug sections in the object writers**: ELF `.debug_*` as non-allocated PROGBITS with their relocations (a code
   address: R_X86_64_64 / R_AARCH64_ABS64 / R_ARM_ABS32; an offset into another debug section: a 32-bit absolute
   against its section symbol); Mach-O `__DWARF,__debug_*` with S_ATTR_DEBUG (code addresses by UNSIGNED relocations;
   offsets between debug sections are plain, as clang emits them).  Assembler and bnas text forms both.
2. **A shared DWARF encoder** (a new package, used by all three backends): abbreviations, the compile unit, the line
   program, the string table, at DWARF 4 like the LLVM backend.
3. **Line tables and subprograms in the native backends**: each backend records (code offset, file, line) as it
   emits an instruction and each function's code range; the encoder turns them into `.debug_line` and a compile unit
   with a DW_TAG_subprogram per function.  `-g` with `--backend native` is accepted from here.
4. **bnld**: ELF keeps and relocates the debug sections; Mach-O writes the debug map.
5. **Variables and types**: DW_TAG_variable with locations (frame slots by DW_OP_fbreg; a value in a register for a
   stretch needs a location list) and the types they need, from a type walk the two backends can share.
6. **Call-frame information** (.debug_frame / .eh_frame, Mach-O compact unwind) -- overlaps bnas's own CFI entry
   (claude-todo "asm: unwind directives are rejected").

Testing: dump the sections (llvm-dwarfdump / dwarfdump) in unit and e2e tests; e2e line breakpoints and backtraces
under lldb (macOS) and gdb (Linux, docker) on all three architectures, with both linkers.

## Questions for the user

- Order, and what the release needs: steps 0-4 give `-g` builds with working file:line breakpoints and symbolized
  backtraces on every native target; 5 (variables, types) and 6 (CFI) are each a project of their own.
- DWARF 4 (the LLVM backend's version) or 5.
