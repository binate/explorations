# Dynamic linking for bnld (ELF first, then Mach-O)

## Why (decision 2026-08-27)

The static-Mach-O plan hit a wall: **macOS arm64 does not run statically-linked
executables** — the kernel mandates the main binary load `/usr/lib/dyld` (verified: a
bnld-linked static Mach-O is otool-clean and `codesign -v`-valid, yet SIGKILL'd as
"Bad executable"; a *dynamic* binary doing the identical direct `svc #0x80` syscall
exits cleanly). The user's call: **support dynamic linking** — and not just for macOS.
On Linux/ELF too, dynamic libc is the standard, and it is the real path to C interop.
So dynamic linking becomes the linker project. (This reverses the earlier "dynamic
linking not needed" note.)

Validation loop: **Docker** runs Linux containers locally (arm64 natively on Apple
Silicon), so ELF dynamic binaries are runnable on this Mac — no waiting on CI.

## First milestone (user-chosen: "import one libc function")

Link a **dynamic aarch64 Linux ELF** whose `_start` **calls a libc function**
(`exit(42)`), resolved by `ld-linux-aarch64.so.1`, and run it in Docker → exit 42.
Confirmed viable: a bare `_start` calling libc `exit` (no `__libc_start_main`) works
(`gcc -nostartfiles -no-pie`, ran, exit 42).

## Blueprint — minimal dynamic aarch64 ELF (`-no-pie`, base 0x400000)

Reverse-engineered from `gcc -nostartfiles -no-pie`.  Sections/segments:

- **PT_INTERP** / `.interp`: `"/lib/ld-linux-aarch64.so.1\0"`.
- **`.dynsym`**: null symbol + `exit` (STT_FUNC, STB_GLOBAL, SHN_UNDEF).
- **`.dynstr`**: `\0exit\0libc.so.6\0` (+ the ld.so version string if versioning).
- **`.hash`** (SysV — simpler than `.gnu.hash`; ld.so accepts either): a hash table
  over `.dynsym` so the loader can traverse it.
- **`.rela.plt`**: one `R_AARCH64_JUMP_SLOT` (type 1026) → the GOT slot for `exit`,
  symbol index = exit's `.dynsym` index.
- **`.plt`**: an `exit@plt` stub — `adrp x16, GOTpage ; ldr x17,[x16,#:lo12:got] ;
  add x16,x16,#:lo12:got ; br x17`.  (With **BIND_NOW** we can skip the PLT0 lazy
  resolver trampoline entirely — see below.)
- **`.got.plt`**: 3 reserved slots (`[0]=&.dynamic`, `[1]=[2]=0`) + one slot per
  import; ld.so writes the resolved address into the import slot.
- **`.text`**: `_start` — `mov w0,#42 ; bl exit@plt`.
- **PT_DYNAMIC** / `.dynamic`: `DT_NEEDED`(libc.so.6), `DT_HASH`, `DT_STRTAB`,
  `DT_SYMTAB`, `DT_STRSZ`, `DT_SYMENT`(24), `DT_PLTGOT`(&.got.plt),
  `DT_PLTRELSZ`(24), `DT_PLTREL`(RELA=7), `DT_JMPREL`(&.rela.plt),
  `DT_FLAGS`(DF_BIND_NOW=0x8) + `DT_BIND_NOW`, `DT_NULL`.

**BIND_NOW simplification:** set `DF_BIND_NOW`/`DT_BIND_NOW` so ld.so resolves every
JUMP_SLOT at load, before `_start`.  Then the GOT slot already holds the real address
when the PLT stub reads it — no PLT0 trampoline, no `_dl_runtime_resolve`, no lazy GOT
initialization needed.  This removes the hairiest part of a first cut.

## bnld work (the pieces)

1. **Resolve treats unresolved externals as dynamic imports** (not errors) when a
   `--dynamic` mode + needed libs are given.  bnld need not parse libc.so.6 — it just
   names the import and lets ld.so bind it.
2. **Synthesize dynamic sections** (.interp, .dynsym, .dynstr, .hash, .plt, .got.plt,
   .rela.plt, .dynamic) with correct cross-offsets.
3. **PLT/GOT generation**: for each imported function reached by a CALL26 to an
   undefined symbol, emit a PLT stub + GOT slot + JUMP_SLOT reloc, and retarget the
   call to the stub.
4. **Emit the dynamic ELF**: PT_INTERP + PT_DYNAMIC + PT_LOAD(s), e_entry=_start.

## Rounds (tentative)

- **D1** — this recon + plan + Docker loop. (done)
- **D2** — dynamic-ELF emitter producing a runnable `exit(42)` binary (the milestone).
  Likely split: (a) dynamic-section synthesis + .dynamic; (b) PLT/GOT + JUMP_SLOT +
  call retarget; (c) wire into Link + CLI `--dynamic`/`-l`; (d) e2e run in Docker.
- **D3+** — call a stdio function (`puts`) to exercise a data-ish import; x86-64 ELF;
  then Mach-O dynamic (LC_LOAD_DYLINKER + LC_MAIN + LC_LOAD_DYLIB + chained fixups),
  reusing the R31–R35 Mach-O writer/signer.

The R31–R35 Mach-O reader/writer/ad-hoc-signer all carry forward; nothing there is
wasted — the Mach-O port of dynamic linking builds directly on them.

## D1 result: approach VALIDATED end-to-end (2026-08-27)

A from-scratch Python prototype (`explorations/proto-dynamic-elf-aarch64.py`) emits a
minimal dynamic aarch64 ELF whose `_start` does `mov w0,#42 ; bl exit@plt`, and it
**runs in Docker and exits 42** — ld.so loads it, and `LD_DEBUG=bindings` confirms
`binding file ./proto to libc.so.6: normal symbol 'exit'`.  The risky unknown (does
ld.so accept a hand-rolled dynamic ELF and bind the import) is now proven.  The exact
working recipe to port to bnld:

- **No section headers** (e_shnum=0); ld.so uses only program headers + `.dynamic`.
- Four program headers: `PT_INTERP`, `PT_LOAD` r-x (page 0: ehdr+phdrs, .interp,
  .hash, .dynsym, .dynstr, .rela.plt, .plt, .text), `PT_LOAD` rw (page 1: .dynamic,
  .got.plt), `PT_DYNAMIC`.
- **BIND_NOW** (`DT_FLAGS=DF_BIND_NOW` + `DT_BIND_NOW`): ld.so resolves the JUMP_SLOT
  before `_start`, so **no PLT0 trampoline / `_dl_runtime_resolve` / lazy GOT init**.
- `.hash` = SysV (nbucket=1, nchain=nsym); `.dynsym` = {null, exit(UND,FUNC,GLOBAL)};
  `.dynstr` = `\0exit\0libc.so.6\0`.
- `.rela.plt` = one `R_AARCH64_JUMP_SLOT` (r_info = (symidx<<32)|1026) → the import's
  `.got.plt` slot.
- `.plt` stub = `adrp x16,GOTpage ; ldr x17,[x16,#:lo12] ; add x16,x16,#:lo12 ; br x17`.
- `.got.plt` = `[0]=&.dynamic, [1]=0, [2]=0, [3..]=import slots` (ld.so fills imports).
- `.dynamic` = NEEDED(libc.so.6), HASH, STRTAB, SYMTAB, STRSZ, SYMENT(24),
  PLTGOT(&.got.plt), PLTRELSZ(24), PLTREL(RELA=7), JMPREL(&.rela.plt), FLAGS(BIND_NOW),
  BIND_NOW, NULL.
- **Gotcha found:** a call site's `bl` PC-relative offset is from the `bl`
  instruction's own address, not the function start — trivial but it segfaulted until
  fixed.

Next (D2): port this to a bnld dynamic-ELF emitter — Resolve treats unresolved externs
as dynamic imports; synthesize the sections above; generate PLT/GOT + JUMP_SLOT and
retarget the CALL26; emit the PT_INTERP/PT_DYNAMIC/PT_LOAD ELF; wire `--dynamic`/`-l`
into the CLI; e2e run in Docker.

## D2 landed (2026-08-28): bnld links a dynamic ELF that calls libc

- **Library** `251d5f73d` — `LinkDynElf` + `EmitDynElfExec` + the synthetic
  "dynamic-stubs" object.  bnld produces a dynamically-linked aarch64 ELF whose
  undefined externals (libc's `exit`) are bound at load by ld-linux-aarch64.so.1.
  Reuses Resolve→Layout→Relocate unchanged (imports enter as a stubs object that
  defines each at its PLT stub); BIND_NOW so no lazy PLT0 resolver.  Unit tests for the
  table builders + the PT_INTERP/PT_DYNAMIC emitter + an end-to-end LinkDynElf test.
- **CLI + e2e** `390e6e16a` — `bnld -dynamic` flag; `e2e/bnld-dynamic-linux.sh` builds
  bnas+bnld, assembles `mov x0,#42 ; bl exit`, links `-dynamic`, checks the ELF names
  the interp + libc.so.6, and RUNS it → exit 42.  Runs natively on any Linux host with
  the aarch64 glibc loader (the CI path, no Docker); SKIPs on a non-Linux box unless
  `BINATE_E2E_DOCKER=1` opts into a glibc arm64 container.  (CI auto-runs everything in
  e2e/; e2e scripts must not invoke Docker by default — the run is native-or-skip.)

Proven end to end: the bnld-linked binary runs and calls libc's exit through the
synthesized PLT/GOT.

### Next (D3+)
- (done, `fe19a61ee`) stdio + multiple imports: a program calls `puts(msg)` then
  `exit(0)` — two imports, a data argument, libc stdio (ld.so runs libc init before
  _start).  Works with no linker change (buildDynStubs sizes tables per-import); the e2e
  now links+runs `hello` (prints "hello from bnld", exit 0) alongside exit42.
- (done, `dd278c06a`) x86-64 ELF dynamic linking: generalized the linker (interp path,
  R_X86_64_JUMP_SLOT, `jmp *disp32(%rip)` PLT stub); `bnld -target linux-x64 -dynamic`
  links exit(42) + puts/exit `hello`, both RUN (native on the x86-64 Linux CI runner —
  no Docker).  e2e split per arch (bnld-dynamic-linux.sh = x64, -aarch64.sh = aarch64).
  Adversarial review of the whole dynamic path came back clean.
- Multiple imports / a real archive of libc stubs; `-l`/`-rpath` ergonomics.
- (done, `e5527b716`) review follow-ups: weak-undef externals are now dynamically
  imported (collected with their binding, marked STB_WEAK so ld.so binds-or-zeros); and
  writePltStub range-checks the stub->GOT displacement (aarch64 ADRP page count / x86-64
  disp32) and fails loud instead of silently truncating.  A focused review of the delta
  was clean.  Known corner (PLT-only model): `&weak_fn` for a genuinely-absent weak
  function is the non-null stub, not 0 — fine for calls; revisit if address-of-weak
  matters (route it through a can-be-0 GOT entry).
- Then Mach-O dynamic (LC_LOAD_DYLINKER + LC_MAIN + LC_LOAD_DYLIB + chained fixups),
  reusing the R31–R35 Mach-O writer/signer — the path to running on macOS arm64.
- (done) e2e Docker policy fixed for both bnld-dynamic-linux.sh and bnld-linux-aarch64.sh
  (`fb791594a`): the aarch64 run happens under Docker only on a Linux CI lane (one
  platform, not duplicated), never on a default local run — CI has no native aarch64
  Linux runner (matrix is x86-64 Linux + arm64 macOS).

## "More libc": data-symbol imports (GLOB_DAT) + multi-lib `-l` (plan, 2026-08-29)

The dynamic linker today treats **every** undefined external as a *called function*:
it defines each at a `.plt` stub with a `JUMP_SLOT` reloc, and `Relocate` *relaxes*
the GOT-indirect relocs (aarch64 `ADR_GOT_PAGE`/`LD64_GOT_LO12_NC`, x86-64
`REX_GOTPCRELX`) to direct addressing — valid only for a *defined* symbol. So a
program that reads a libc **data** global (`environ`, `stdout`) via a GOT-indirect
load — exactly what the native backend's `__c_global` emits — is silently
miscompiled: the load is relaxed against a bogus address (the PLT stub). Closing
that is the substance of "more libc". Decomposition (small, green, self-contained):

- **ML2 — text-assembler GOT syntax.** `:got:`/`:got_lo12:` (aarch64) and
  `@GOTPCREL` (x86-64) in `pkg/binate/asm/parse`, so `bnas` can assemble a
  data-import program from `.s`. The programmatic API (`AdrpGot`/`LdrGotLo12`,
  `MovGotPcRel`) already exists; this is only the text front-end. Own unit tests.
  (Lands first so ML1 can be Docker-validated and ML3 can be a real `.s` e2e.)
- **ML1 — linker data-symbol imports (GLOB_DAT), both arches.** Classify each import
  by how it is *referenced*: a call reloc (aarch64 CALL26/JUMP26, x86-64 PLT32) →
  PLT import (today's path); a GOT-indirect reloc → **GOT import** (data global or
  address-taken). For GOT imports synthesize a `.got` section (one 8-byte slot each)
  + a `.rela.dyn` with `R_*_GLOB_DAT` (aarch64 1025 / x86-64 6) so ld.so writes the
  real address into the slot at load, and add `DT_RELA`/`DT_RELASZ`/`DT_RELAENT` to
  `.dynamic`. Define each GOT import at its `.got` slot; fork `Relocate` so a
  GOT-indirect ref to a GOT import **keeps the load** (aarch64: keep the `LDR`, don't
  rewrite to `ADD`; x86-64: keep the `mov`, don't flip to `lea`) pointing at the
  slot. A symbol referenced *both* ways is rejected loudly (rare; the real targets —
  `exit`/`puts` call-only, `environ`/`stdout` GOT-only — never collide). Strong unit
  tests via the programmatic assembler; **Docker-validate** ld.so actually binds the
  GLOB_DAT before landing (as D1 validated JUMP_SLOT).
- **ML3 — runnable e2e.** Extend the dynamic e2e (both arches) with a program that
  reads `environ` through the GOT and derives its exit code from it, proving the
  GLOB_DAT slot was bound. Native on the x86-64 Linux CI lane; aarch64 under Docker
  on the Linux lane (per the existing e2e Docker policy).
- **ML4 — multi-lib `-l` / correct `DT_NEEDED` (needs a decision).** Today the single
  `DT_NEEDED` is hardcoded `libc.so.6`. A correct `DT_NEEDED` for `-lfoo` is the
  library's **SONAME** (`libc.so.6`, not `libc.so`), which lives *inside* the `.so`'s
  `.dynamic` (`DT_SONAME`). Options: (a) parse each `-l`'s `.so` to read `DT_SONAME`;
  (b) a convention/flag to pass explicit SONAMEs. This is a real product fork —
  **surface it to the user, don't pick unilaterally.** Independent of ML1–ML3 (they
  keep the single libc.so.6 NEEDED), so it comes last.

### ML2a + ML1 LANDED (2026-08-29)

- **ML2a — aarch64 GOT operand syntax** (landed `d8a425b35`): `bnas` now assembles
  `adrp rd, :got:sym` (FIX_ADRP_GOT_HI21) + `ldr xt, [xn, #:got_lo12:sym]`
  (FIX_LD_GOT_LO12) — the `.s` front-end over the existing `AdrpGot`/`LdrGotLo12`
  API. Unit tests for both forms, plain-adrp non-regression, and the 32-bit
  rejection.
- **ML1 — data-symbol imports via GLOB_DAT** (landed `1a45642f3`): imports are classified
  (call→PLT vs GOT-load→data); data imports get a `.got` slot + `.rela.dyn`
  `R_*_GLOB_DAT`; `Relocate` keeps a data import's GOT load (aarch64 LDR / x86-64
  mov) pointing at the slot instead of relaxing it. Both arches; static links
  unaffected (GotImports empty → relax as before). **Docker-validated on aarch64
  Linux**: a program that GOT-loads libc `stdout` and exits 42 iff non-null runs and
  exits 42, LD_DEBUG confirming ld.so binds the binary's `stdout` slot via GLOB_DAT
  (+ `exit` via the PLT). exit42/hello (no data imports) still run.
- **glibc gotcha found + fixed**: emitting `DT_RELA` for a *zero-size* `.rela.dyn`
  laid out adjacent to `.rela.plt` makes glibc's ld.so skip the JUMP_SLOT
  relocations entirely (a called import binds to 0 → jump to null → SIGSEGV). Fix:
  emit the `DT_RELA`/`RELASZ`/`RELAENT` trio only when there IS a data import (what
  ld/lld do); the no-data-import path is byte-structure-unchanged.

- **ML3a — aarch64 data-import e2e** (landed `799da0559`): a `datum` program
  GOT-loads libc `stdout` and exits 42 iff non-null; guards the .got + GLOB_DAT path
  in CI alongside exit42's PLT/JUMP_SLOT path. Docker-verified (linux/arm64).
- **ML2b — x86-64 `@GOTPCREL` `.s` syntax** (landed `94b32958d`): `mov rax, [rip +
  sym@GOTPCREL]` → MOV(0x8B, a GOT load) + FIX_GOTPCREL, via a new `@` lexer token +
  OP_RIPGOTLABEL. Unit tests + bad-suffix rejection.
- **ML3b — x86-64 data-import e2e** (landed `2a84e4158`): the x64 `datum` sibling;
  Docker-verified (linux/amd64), exits 42.

So **data-symbol imports are complete on both arches** — implementation, `.s`
syntax, and runnable CI-guarded e2e, all validated end to end (both binaries
GOT-load libc `stdout`, bind it via GLOB_DAT, and exit 42, with `exit` via the PLT).

Still open at the time (both since done): **ML4 — multi-lib `-l` / correct
`DT_NEEDED`** (landed — see "ML4 LANDED" below) and **Mach-O dynamic** (landed — see
the MD sections below).

## Roadmap reorder (2026-08-29): Mach-O dynamic BEFORE ML4

Data-symbol imports are complete on both ELF arches. Per the user's call, the next
work is **Mach-O dynamic linking** (run bnld-linked binaries natively on macOS
arm64 — the whole reason we pivoted to dynamic linking); **ML4** (multi-lib `-l` via
`.so` `DT_SONAME` parsing) is deferred to after it.

### M1 recon finding: use the classic LC_DYLD_INFO_ONLY format, not chained fixups

A minimal dynamic arm64 Mach-O whose `_start` does `mov w0,#42 ; bl _exit` (linked
`clang -e _start -nostartfiles`) runs and exits 42.  Modern ld64 defaults to
**chained fixups** (LC_DYLD_CHAINED_FIXUPS) — a compressed, complex import format —
but `-Wl,-no_fixup_chains` produces the **classic LC_DYLD_INFO_ONLY** (bind-opcode)
format, which **dyld on this macOS still accepts** (also ran, exit 42).  The classic
format is much simpler to hand-roll, so bnld's Mach-O dynamic writer will use it.

The mechanism mirrors ELF dynamic linking exactly: `bl _exit` → a `__stubs` entry in
__TEXT → indirects through a `__got` pointer in __DATA_CONST → dyld binds that pointer
to `_exit` at load (a BIND opcode in LC_DYLD_INFO).  So the ELF PLT/GOT design ports
over: a synthesized stub + GOT slot per import, non-lazy (bind-at-load) to avoid the
dyld_stub_binder/lazy machinery — the Mach-O analogue of DF_BIND_NOW.

Load commands in the classic reference (superset; the minimal subset is being
determined): __PAGEZERO, __TEXT, __DATA_CONST, __LINKEDIT segments; LC_DYLD_INFO_ONLY,
LC_SYMTAB, LC_DYSYMTAB, LC_LOAD_DYLINKER(/usr/lib/dyld), LC_MAIN, LC_LOAD_DYLIB(
/usr/lib/libSystem.B.dylib), LC_CODE_SIGNATURE (ad-hoc — required on arm64, already
have the R35 signer), plus droppable LC_UUID/LC_BUILD_VERSION/LC_SOURCE_VERSION/
LC_FUNCTION_STARTS/LC_DATA_IN_CODE (TBD which dyld actually requires).

Reuses the R31–R35 Mach-O writer/signer (static exec writer + ad-hoc CodeDirectory).

### M2 recipe VALIDATED (2026-08-29): hand-rolled non-lazy dynamic Mach-O runs on macOS arm64

`explorations/proto-dynamic-macho-arm64.py` emits a from-scratch minimal dynamic
arm64 Mach-O whose `_start` does `mov w0,#42 ; bl <stub>`; the stub jumps through a
`__got` slot that dyld binds to libSystem's `_exit` at load.  Ad-hoc signed, it
**runs and exits 42** (3/3), and `dyld_info -fixups` confirms one bind:
`__DATA_CONST/__got -> libSystem/_exit` (non-lazy).  The risky unknown — does dyld
accept a hand-rolled classic-format dynamic Mach-O with a non-lazy GOT bind — is
answered YES.  The exact working recipe bnld will port:

- **Format: classic `LC_DYLD_INFO_ONLY`** (bind opcodes), NOT chained fixups.
- **Non-lazy**, mirroring ELF DF_BIND_NOW: one `__got` slot per import in
  `__DATA_CONST`, bound at load by a BIND opcode; a `__TEXT` stub `adrp x16,__got@page
  ; ldr x16,[x16,#off] ; br x16` jumps through it.  No `__la_symbol_ptr`, no
  `dyld_stub_binder`, no `__stub_helper`.
- **Segments**: `__PAGEZERO` (vmsize 4GB), `__TEXT` (r-x, maps mach header + load
  commands at file 0, then `__text`), `__DATA_CONST` (rw, `__got`; **MUST carry the
  `SG_READ_ONLY` segment flag 0x10** — dyld rejects `__DATA_CONST` without it),
  `__LINKEDIT`.  16 KB pages.
- **Load commands** (minimal set that dyld accepts): `LC_SEGMENT_64` ×4;
  `LC_DYLD_INFO_ONLY` (bind_off/size only); `LC_SYMTAB`; `LC_DYSYMTAB` (indirect
  symtab: one entry -> the import's nlist, `__got.reserved1` = its indirect index);
  `LC_LOAD_DYLINKER` (`/usr/lib/dyld`); **`LC_BUILD_VERSION`** (platform macOS,
  minos — required by dyld4); `LC_UUID`; `LC_MAIN` (entryoff); `LC_LOAD_DYLIB`
  (`/usr/lib/libSystem.B.dylib`); `LC_CODE_SIGNATURE`.  Droppable:
  LC_SOURCE_VERSION, LC_FUNCTION_STARTS, LC_DATA_IN_CODE, LC_DYLD_EXPORTS_TRIE.
- **Bind opcodes** for one import: `SET_DYLIB_ORDINAL_IMM 1`,
  `SET_SYMBOL_TRAILING_FLAGS_IMM "_exit"`, `SET_TYPE_IMM POINTER`,
  `SET_SEGMENT_AND_OFFSET_ULEB seg=__DATA_CONST off=slot`, `DO_BIND`, `DONE`.
- **nlist** for an import: `n_strx`, `n_type=N_EXT` (0x01, undefined), `n_sect=0`,
  `n_desc=(dylib_ordinal<<8)` (two-level), `n_value=0`.
- **Signing**: arm64 requires a valid signature; bnld EMITS `LC_CODE_SIGNATURE` and
  ad-hoc-signs via the R35 CodeDirectory signer (proven for static Mach-O).  (codesign
  default-strict only balked at ADDING a missing LC_CODE_SIGNATURE — a prototype
  artifact; with the LC present, `codesign -s -` is strict-clean.)

The mechanism is the exact Mach-O analogue of the ELF PLT/GOT + BIND_NOW design, so
the bnld port reuses Resolve -> Layout -> Relocate with a synthesized Mach-O
"dynamic-stubs" structure — like LinkDynElf/buildDynStubs but emitting Mach-O.

### bnld implementation plan (Mach-O dynamic) — rounds
- **MD1** — recon + M2 recipe (this; done).
- **MD2** — `LinkDynMacho` + a dynamic Mach-O writer: synthesize the __got + stub +
  bind opcodes + symtab/dysymtab, reuse the R31–R35 segment writer + signer, emit the
  load-command set above.  Milestone: bnld links a dynamic arm64 Mach-O whose _start
  calls libSystem `exit(42)`, runs on macOS arm64.  (aarch64 first; x86-64 Mach-O
  later if wanted.)
- **MD3** — multiple imports + a data (GOT) import (stdout), mirroring the ELF datum
  e2e; wire `bnld -target macos-arm64 -dynamic`; e2e that RUNS natively on the macOS
  CI lane.

### M2 refinement + MD2 DONE (2026-08-29): bnld links a runnable dynamic Mach-O

Follow-up to the M2 recipe: **section-less segments work** — dropping the section
records, DYSYMTAB, and indirect symtab from the prototype still runs (dyld binds via
the opcodes, which name the symbol + segment/offset).  So bnld reuses the existing
section-less `writeSeg64` and needs only LC_DYLD_INFO_ONLY + LC_SYMTAB (no DYSYMTAB).

**MD2 LANDED (d77f418b2):** `bnld -target macos-arm64 -dynamic` links an
arm64 Mach-O whose `_start` calls libSystem `_exit`, and it **runs natively on macOS
arm64** (`mov w0,#42 ; bl _exit` → exit 42; a `#99` variant exits 99 — proof the arg
reaches `_exit`).  `otool -L` shows `/usr/lib/libSystem.B.dylib`; `codesign -v` passes
(bnld's own R35 ad-hoc signature is dyld-acceptable at runtime — first runtime proof
of the R35 signer, since static Mach-O never ran).  New code:
- `pkg/binate/link/dynmacho.bn` — `LinkDynMacho` driver + bind-stream/symtab/stub
  builders (reuses collectImports/classifyImports/Resolve/Layout/Relocate; a
  synthesized `__stubs` object defines each import at its stub).
- `pkg/binate/link/emit_dynmacho.bn` — `EmitDynMachoExec`: __PAGEZERO/__TEXT/
  __DATA_CONST(SG_READ_ONLY, the __got)/__LINKEDIT + the dynamic load commands + the
  R35 ad-hoc signature; 16 KB pages; LC_MAIN entry.
- `cmd/bnld/main.bn` — `-target macos-arm64` (⇒ Mach-O, base 0x100004000; dynamic-only).
- Unit tests (bind stream, symtab, stub encoding + range, end-to-end structural).

Known MD2 limits (MD3): function (stub) imports only — a data (GOT) import is rejected;
writable program data (a __DATA segment) is rejected; single hardcoded libSystem
DT-equivalent.  Next (MD3): multi-import + a data import (via the ELF datum pattern) +
`-target macos-arm64` e2e that RUNS on the macOS CI lane.

### MD2 review (2026-08-29): SHIP, one fix applied + two noted follow-ups

Adversarial review of the Mach-O dynamic writer verified multi-import consistency
(stub i*12 / got i*8 / bind i*8 / n_strx all align), the segment/file congruence, the
code-signature ordering, LC sizes, and entryoff. Verdict SHIP for the validated scope.
- **Fixed before landing:** the `__got` was hard-capped at one 16 KB page (the
  `gotCount` writer param was unused) — ≥2049 imports would silently emit a bind
  offset past `__DATA_CONST`. Now `__DATA_CONST` is sized `alignUp(gotCount*8, page)`;
  a 2049-import writer test guards it. (Latent — can't bite few-import libSystem use —
  but a silent mis-emit, so fixed now.)
- **Follow-up (minor):** an import referenced *only* by an ABS64 (`.quad _undef`) is
  classified impPlt and gets a code stub rather than being rejected/handled as data —
  exotic on arm64/Mach-O (address-take emits GOT_LOAD, which IS rejected). Make ABS64
  -to-an-import loud like impGot. Touches shared classifyImports, so deferred.
- **Follow-up (minor):** weak undefined imports are emitted as strong binds (no
  BIND_SYMBOL_FLAGS_WEAK_IMPORT / N_WEAK_REF) — a genuinely-absent weak symbol would
  hard-fail dyld instead of binding to 0. No impact for strong libSystem entry points.

### MD3 in progress (2026-08-30): multi-import + rodata + a MAJOR parse_macho bug fixed

Extending to multi-import + rodata surfaced a **major latent bug in `parse_macho`**
(not new to MD3): a defined symbol's `InputSymbol.Value` was set to the raw nlist
`n_value` instead of the offset *within* its section (`n_value - section.addr`).
bnas/clang lay `__const` before `__text`, so any program with rodata puts `__text` at
a non-zero object address → every `__text` symbol (incl. the entry) resolved off by
that amount. In a dynamic Mach-O this made `LC_MAIN` entryoff land mid-`_start` (dyld
jumped past the prologue → SIGSEGV / wrong exit); it equally corrupted the *static*
Mach-O path. Escaped notice because static Mach-O never ran and prior test objects
were text-only (`__text` at addr 0). **Fixed** (landed `2c6b7bfaf`; was b854118f8, formerly not
landed): capture each `section_64.addr`, set a section-defined symbol's Value to
`n_value - sectionAddr`; regression test asserts `_start.Value==0` / `helper.Value==16`
/ `msg.Value==0` for a `__const`-before-`__text` object.

With that fix, **multi-import + rodata + stdio all run on macOS arm64**: a `hello`
program (`write(1,msg,22)` via `adrp/add` to a rodata string, then `_exit(0)`) prints
"hello from bnld macho" and exits 0; the exit(42)/exit(99) cases still pass.

Remaining MD3: (a) commit a `-target macos-arm64` e2e (exit42 + hello) that RUNS on
the macOS CI lane (natively — no Docker); (b) data (GOT) imports on Mach-O (still
rejected — reuse the ELF GLOB_DAT/keep-load machinery: a __got slot + POINTER bind,
no stub, Relocate keeps the GOT load).

### MD3 DONE (2026-08-30): macOS e2e + Mach-O data (GOT) imports — full ELF parity

- **MD3a — macOS e2e** (landed `e7481c15b`): `e2e/bnld-macho-dynamic.sh` builds
  bnas+bnld, links exit42 + hello (write + a rodata string) with `bnld -target
  macos-arm64 -dynamic`, and RUNS them natively on the macos-latest CI lane (no Docker
  — a Mach-O can't run in a Linux container); SKIPs the run elsewhere.
- **MD3b — Mach-O data (GOT) imports** (landed `f961164d7`): dynamic Mach-O now
  reaches libSystem *data* symbols (e.g. `___stdoutp`), not just functions — the Mach-O
  analogue of the ELF GLOB_DAT path, reusing the same machinery (classify call-vs-GOT,
  `SymbolTable.GotImports`, Relocate's keep-load `gotImp`).  The `__got` moved INTO
  Layout as the synthesized object's writable section (so a data import is *defined at*
  its slot); `LayoutPaged` parameterizes Layout's page size (16 KB for Mach-O; ELF keeps
  linkPageSize); `EmitDynMachoExec` maps the read-only group → __TEXT and the writable
  group (the __got) → __DATA_CONST (SG_READ_ONLY).  Writable *program* data (a __DATA
  segment) is rejected loudly — a follow-up.  The e2e gained a `datum` program that
  GOT-loads `___stdoutp` and exits 42 iff non-null (also calls `_exit`, so the mixed
  function+data case); validated natively (ALL PASS).

**Mach-O dynamic linking now has ELF parity**: function + data imports, runnable +
CI-guarded on macOS arm64.  Remaining follow-ups (all documented, none blocking):
ABS64-to-an-import made loud; weak imports marked weak.  (Writable program `__DATA` is
now done — see "Writable __DATA LANDED" below.)

### ML4 LANDED (2026-08-30): multi-lib `-l` — DT_NEEDED from each `.so`'s SONAME

Landed `34b69a94d`.  A dynamic ELF link previously emitted a single hardcoded
`DT_NEEDED` (`libc.so.6`), so a program could only import from libc.  bnld now records
one `DT_NEEDED` per shared library, named by that library's **own SONAME** (its
`DT_SONAME`, e.g. `libm.so.6` — not the `libm.so` symlink used to find it), so a
program may import from non-libc libraries too.

- **`parse_so.bn` (new): `readSoname`** — reads an ELF64 shared object's `DT_SONAME`:
  walks the program headers for `PT_DYNAMIC` (+ the `PT_LOAD` map that turns the
  `DT_STRTAB` vaddr into a file offset), then the `.dynamic` array for `DT_SONAME` +
  `DT_STRTAB`.  Pure byte parsing (no execution), so bnld reads a target `.so` on any
  host — a Linux `.so` links fine from macOS.
- **`dynStrtab`/`buildDynStubs`/`patchDynSections`/`dynEntryCount`** now take a list of
  needed libraries and emit one `DT_NEEDED` (and one `.dynstr` entry, one `.dynamic`
  slot) per library.  `LinkDynElf` gained a `sharedLibPaths` param; `neededLibs`
  prepends the default `libc.so.6` then adds each `.so`'s SONAME, **deduped**.
- **cmd/bnld**: for a dynamic ELF link, `-l<name>` now prefers `lib<name>.so` (a shared
  dependency, its SONAME → `DT_NEEDED`) over `lib<name>.a` (a static archive, linked in
  as before); a static link or a Mach-O target keeps the archive-only behavior.
- The default single-libc case is **byte-identical** to before (no regression).

**Scope:** ELF-only, as planned.  Mach-O keeps its single hardcoded libSystem
`DT_NEEDED` — its two-level namespace needs per-symbol dylib attribution (scan each
dylib's exports), a separate/larger job.

**Validation:** unit tests (`readSoname` ok / no-PT_DYNAMIC / no-DT_SONAME / bad-magic;
`neededLibs` dedup; multi-lib `buildDynStubs`; `dynEntryCount`; cmd/bnld `.so`-preference
+ `.a`-fallback).  The e2e (both arches) relinks `exit42` with `-lm` against a **real
glibc `libm.so.6`**, asserts both `libc.so.6` and `libm.so.6` appear as `DT_NEEDED` (the
SONAME, not the filename), and runs it → exit 42.  Docker-validated locally on x86-64
and aarch64.

**Known limitation (fail-loud, non-blocking):** a `-l` whose `.so` is a GNU ld *linker
script* (e.g. glibc's `libc.so` dev file is text `GROUP(...)`, not ELF) fails with "not
an ELF file (bad magic)" rather than mis-linking.  `-lc` is redundant anyway (libc is
the default); symlink-style `.so`s (libm, etc.) work.  Handling linker scripts would be
its own feature — a possible future follow-up.

### Writable __DATA LANDED (2026-08-30): mutable program globals in a dynamic link

Landed `b8f32ea44`.  A dynamically-linked program with mutable globals (`.data`) or
zero-init storage (`.bss`) can now be linked on both dynamic backends.

- **ELF: already worked — no code change.**  `Layout` already merges program
  `.data`/`.bss` with the synthesized RW sections (`.got`/`.got.plt`/`.dynamic`) into
  one RW group, and `EmitDynElfExec` emits a RW `PT_LOAD` with correct filesz/memsz
  (incl. NOBITS `.bss`).  Verified end-to-end on both arches (mutate a `.data` + a
  `.bss` global → exit 42; a 1 MB `.bss` stays an 8 KB file).  Added `wdata` regression
  e2e coverage so it stays working.
- **Mach-O: implemented (Option A — single writable `__DATA`).**  Program `.data`/`.bss`
  AND the `__got` now share ONE writable `__DATA` segment (no `SG_READ_ONLY`), mirroring
  ELF's single RW segment; dyld binds the `__got` in the writable `__DATA` (classic
  pre-`__DATA_CONST` behavior).  The rename `__DATA_CONST` → `__DATA` is required, not
  cosmetic: dyld rejects a segment *named* `__DATA_CONST` that lacks `SG_READ_ONLY`.  The
  `__got` is no longer at the segment start (program `.data` precedes it), so
  `buildMachoBind` emits each POINTER bind at `gotSegOff + i*8`, where `gotSegOff` is the
  `__got`'s laid-out vaddr minus the `__DATA` base.  The writable-program-data rejection
  is removed; `writeSeg64Flags`/`MACHO_SG_READ_ONLY` are gone.  (Trade-off: the `__got`
  is no longer frozen read-only after fixups — the `__DATA_CONST` hardening — which is
  not a correctness concern.)
- **`.bss`-bloat bug found + fixed (adversarial review).**  The first cut set
  `__LINKEDIT`'s *file* offset to `dataFileOff + dataVm` (vmsize, which includes the
  `.bss` zero-fill), so a large `.bss` bloated the file 1:1 (a 1 MB `.bss` → a 1.1 MB
  file — the e2e's `.zero 4` was too small to expose it).  Fixed to
  `alignUp(dataFileOff + dataFileSize, page)` (file-backed bytes only, mirroring the
  static emitter), keeping `__LINKEDIT` page-congruent; a 1 MB `.bss` Mach-O is now
  ~49 KB.  Guarded by a host-independent unit test (`TestEmitDynMachoExecLargeBss`:
  file ≤ 128 KB AND `__DATA` vmsize ≥ 1 MB).

**Validation:** unit tests (`buildMachoBind` non-zero `gotSegOff`; end-to-end
`LinkDynMacho` with a writable `.data` section — no longer rejected; the large-`.bss`
no-bloat test).  The e2e gained a `wdata` program on all three lanes; Mach-O validated
NATIVELY on macOS arm64 (`codesign -v` clean; `dyld_info` shows the `_exit` bind at
`__DATA` offset 8, i.e. past the program `.data`); ELF x86-64 + aarch64 via Docker.
Adversarial review: SHIP (after the `.bss`-bloat fix).

Remaining follow-ups: ABS64-to-an-import made loud; weak Mach-O imports marked weak.
