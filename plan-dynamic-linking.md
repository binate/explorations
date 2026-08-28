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
- x86-64 ELF dynamic linking (different PLT/GOT/relocs; would let CI run it natively on
  an x86-64 Linux runner without binfmt).
- Multiple imports / a real archive of libc stubs; `-l`/`-rpath` ergonomics.
- Then Mach-O dynamic (LC_LOAD_DYLINKER + LC_MAIN + LC_LOAD_DYLIB + chained fixups),
  reusing the R31–R35 Mach-O writer/signer — the path to running on macOS arm64.
- (done) e2e Docker policy fixed for both bnld-dynamic-linux.sh and bnld-linux-aarch64.sh
  (`fb791594a`): the aarch64 run happens under Docker only on a Linux CI lane (one
  platform, not duplicated), never on a default local run — CI has no native aarch64
  Linux runner (matrix is x86-64 Linux + arm64 macOS).
