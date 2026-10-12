# Plan: bnld dynamic ELF exports the definitions its needed libraries define or reference

The claude-todo MAJOR "bnld dynamic ELF exports none of the program's definitions" (claimed 2026-10-12, work-2).
Decisions (user, 2026-10-12): read the needed libraries' `.dynsym` ("Probably we should read .dynsym"), as lld
does; when a needed library cannot be found, fail loud ("Fail loud") rather than guess.

## What is wrong

A dynamic ELF executable's .dynsym holds only its imports and (binate `80279f202`) its commons.  A program
definition of a name a needed library also defines and uses stays private: `int optind;` built with the default
`-fno-common` gives libc's getopt and `main` two variables; a program's own `malloc` / `free` does not replace
libc's for libc's internal calls.  lld exports any executable definition a linked shared library defines or
references (GNU ld exports what one references, and copy-relocates data the program uses from one).  bnld reads
only each library's SONAME; libc.so.6 is named without being read at all.

## Facts that shape the design

- bnc's `--linker bnld` cross-links Linux ELF on any host (`e2e/bnc-bnld-linux.sh` builds both Linux targets on
  macOS); a macOS host has no Linux libc.so.6.  With "fail loud", such a link needs a sysroot.
- The e2e matrix runs every script on ubuntu-latest (x86-64, Docker available) and macos-latest (arm64, no
  Docker).  Scripts that dynamically link Linux ELF: `bnld-dynamic-linux.sh`, `bnld-dynamic-linux-aarch64.sh`,
  `bnc-bnld-linux.sh`, `bnc-bnld-llvm-linux.sh`.
- Unit tests call LinkDynElf on every host (`pkg/binate/link/dynlink_test.bn`, `dynlink_exports_test.bn`,
  `cmd/bnc/bnld_link_test.bn`, cmd/bnld's): they need a synthesized libc.so.6 (`parse_so_test.bn`'s `mkSo`
  builds a minimal shared object already).
- Modern glibc's libc.so.6 has DT_GNU_HASH and no DT_HASH, so the symbol count comes from the section headers
  (SHT_DYNSYM, as lld reads a DSO) rather than the dynamic segment.
- bnld does not read symbol visibility today; a hidden definition must never be exported.
- bnc's `-L` is the Binate impl path, so bnc needs its own flag (`--sysroot`) for the library search.

## Series

1. ✅ landed binate `d7d6ce938` (2026-10-12).  Also, from its review: collectImports leaves out a name some
   object references as hidden (it must resolve inside the image); the Mach-O symbol loop moved to
   `parse_macho_syms.bn`.  The assembler has no visibility directives (todo "asm: no symbol-visibility directives").
   **Visibility.**  The ELF reader records STV_HIDDEN / STV_INTERNAL (InputSymbol gains a `Hidden` mark; the
   Mach-O reader marks N_PEXT private externs).  Nothing exports yet, so no behavior change beyond the mark.
2. **Library search and symbols, fail loud.**  Read a shared object's dynamic symbols (SHT_DYNSYM + its string
   table; defined vs undefined, binding); find each needed library — libc.so.6 and the `-l` ones — in the given
   directories or, by default, under a sysroot (`/` unless given): `<sysroot>/{lib,usr/lib}/<multiarch triple>`,
   then `{lib64,usr/lib64,lib,usr/lib}`, skipping a file of another machine (as GNU ld does).  A needed library
   found nowhere is an error naming it and the directories searched.  LinkDynElf takes the search directories;
   cmd/bnld gains `--sysroot` (its `-L` directories join the search); bnc gains `--sysroot` for `--linker bnld`.
   Unit tests get a synthesized libc.so.6.  e2e: each Linux dynamic-link script provisions a sysroot — the host's
   `/` for a native x86-64 Linux link, else libc.so.6 copied out of the Debian image when Docker is usable
   (the Linux CI lane, or BINATE_E2E_DOCKER=1 locally) — and SKIPs the dynamic link where neither is available
   (the macOS CI lane, which then no longer link-checks Linux dynamic ELF; the Linux lane still links and runs
   both targets).
3. **Exports.**  Export each global, non-hidden program definition (commons included) whose name a needed
   library defines or references, typed from its section (STT_FUNC for code, STT_OBJECT for data) — replacing
   "export every common".  Tests: unit (a synthesized libc defining `optind` and `malloc`); e2e `optc` without
   `-fcommon`, and a program whose own malloc / calloc / realloc / free must receive libc's internal calls
   (strdup's result lies in the program's arena).
