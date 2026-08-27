# Plan: Linker Implementation (detailed)

**Companion to [`plan-linker.md`](plan-linker.md).** That doc is the *design*
(linker-as-library, Binate-as-linker-script). This is the *implementation* plan,
re-grounded in the current tree — the design doc predates most of the self-hosted
toolchain and carries stale assumptions (corrected in §1). Written 2026-08-26;
revised after two adversarial reviews (§13 records what they changed).

All `file:symbol` references are into the `binate` repo (the toolchain checkout).
Nothing here is implemented yet: there is no `pkg/link`, no `cmd/bnld`.

---

## 0. What this plan decides

Five scope decisions, **ratified 2026-08-26** (the roadmap they imply is in §11):

- **(D1) Object scope** — **native-only for the MVP, then extend to a full,
  general reader that also links the default (LLVM/clang) build.** The general
  reader is a *planned later phase* (§11 M2+), not a rejected alternative.
- **(D2) PIC/GOT** — **no GOT in the MVP; add GOT support after the MVP.** A
  hermetic native program needs none (`PLT32`≡`PC32`; GOT only via `__c_global`),
  but the D1 extension — linking clang/LLVM-build objects, which are PIC — *does*
  need it, so GOT lands in the same post-MVP "full-featured" phase (§11 M2+).
- **(D3) Driver model** — **compiled drivers first**, interpreted `-driver
  file.bn` later — but design the `pkg/link` API with the interpreted path in view
  now (§app-B).
- **(D4) libgcc `__aeabi_*`** — **removing the `libgcc.a` dependency is an
  orthogonal project** (port the helpers to Binate/bnas), tracked separately in
  `claude-todo.md` — not a linker decision. The MVP links `libgcc.a` via archive
  support (Step 5) for 64-bit-int/float programs, or sidesteps it (32-bit-int-only
  proof).
- **(D5) Mach-O** — **deferred behind ELF**, incl. ad-hoc code-signing. ⚠️ The dev
  host is an **arm64 Mac**, so ELF output isn't natively runnable locally
  (validate hosted output via **Docker** — aa64 native, x64 emulated — + Linux CI;
  QEMU covers deferred bare-metal arm32); native-macOS runs wait for Mach-O.

The **single most important framing correction** from review: the hard part of a
*useful hosted* linker is **not** native-backend maturity (already ~done) and
**not** GOT/PLT relaxation (near-nonexistent for hermetic code) — it is **hosted
ELF-executable emission (segments/perms/page-alignment) + a hermetic `_start`**.
Bounded, but that is where the schedule goes (§11).

---

## 1. Corrections to the design doc (verified against code)

1. **`--backend native` already routes EVERY package through the native
   backend** — native-for-all-packages is *not* a far-off prerequisite. In
   `cmd/bnc/main.bn`, `useNative = streq(cli.Backend,"native")` and the
   *dependency* loop (`for i … ldr.Order`) calls
   `compileModuleVia(mod, …, useNative)` (main.bn:297; comment at :293-296: "must
   route EVERY package through the native backend, leaving clang for linking
   only"). Proof: the `builder-comp_native_aa64-comp_native_aa64` etc. modes
   **self-compile the whole compiler natively through gen2** — impossible if only
   `main` went native. (⚠️ There is a **stale comment at `main.bn:348`** —
   "deps above always go through LLVM; only the main module honors --backend
   native" — that contradicts the code 50 lines above and misled an early draft of
   this plan; worth a one-line cleanup. The stdlib `cmd/bnc/compile.bn` dispatch
   `compileModuleVia` (:266) honors the flag it is *passed*, and both call sites
   pass `useNative`.)

2. **The interpreter-as-library ALREADY EXISTS.** The design doc's Risk #7 / Step 7
   ("the VM will need a clean eval-and-call entry point") is done:
   `pkg/binate/interp` (`New` interp.bn:57, `LoadProgram` :149, `RunMain` :241,
   `RunFunc` :267, `RunFuncTyped` runfunc_typed.bn:23, `CallIfaceMethod`
   call_iface.bn:20). `cmd/bni/main.bn:runProgram` (:121) embeds it. Interpreted
   code calling injected *compiled* stdlib is the production `builder-comp-int`
   mode. **The VM is `pkg/binate/vm`, not `pkg/vm`** (doc reference stale).

3. **There is no C runtime to link.** No `.c` files exist; the runtime is pure
   Binate (`impls/core/common/pkg/builtins/rt` + `.../startup`). Hosted entry:
   libc-`_start` → `main` (= `startup._entry`, `#[c_export("main")]`,
   `impls/core/common/pkg/builtins/startup/args_main.bn:29`) → `__c_call("bn_entry")`
   (:67). `bn_entry` = mangled `main.__entry` (`mangle.bn:214`), emitted by
   `ir.Module.EmitMainEntry` (`ir/gen_init.bn:355`). The linker's entry story is
   this `bn_entry` contract, **not** a generic `_start`/`main` (§8). *(The design
   doc never actually named a `.c` runtime; its genuinely-stale items are the
   `bootstrap.Open/Read/Close` "already available" note — the Go bootstrap was
   dropped — and `pkg/vm`.)*

4. **The current linker is `clang`, invoked as a driver — never `ld` directly.**
   Final program link is one `process.Run("clang", …)` (main.bn:391); test
   binaries (test.bn:266); per-object `clang -c` (compile.bn:201); `--library` uses
   `ar` (library.bn:132). The argv is assembled in `cmd/bnc/target.bn` helpers
   (`appendTargetFlags` :340, `appendTargetLinkFlags` :362, `appendLinkStripFlags`
   :297, `appendTargetRuntime` :384). So `bnld` replaces a **clang-driven link**;
   the integration point is those helpers + `main.bn`/`test.bn`.

5. **GOT is `__c_global`-only; PLT is trivial** (this replaces the design doc's
   confused "static non-PIE only, no GOT/PLT" scope — see §1.4-detail below). The
   only *pervasive* PIC reloc, `PLT32`, is field-identical to `PC32`.

6. **The stdlib is ready** (`vec.Vec[uint8]`, `hashmap.Map`/`mapfn.MapFn`, `os`
   file I/O, mature generics/interfaces) — the doc's "wait for growable
   collections / a map" caveat is obsolete. Remaining gaps are small and
   enumerable (§7).

7. **The assembler has ELF/Mach-O *writers* and all format constants, but ZERO
   reader infrastructure.** The reader/resolver/relocation-decode front-end is
   genuinely **greenfield** — not "reuse existing" (§4, §5).

### 1.4-detail — GOT vs PLT, precisely (drives D2)

Native codegen emits two *different* IR ops with *different* reloc behavior
(verified in `pkg/binate/native/x64/x64_dispatch.bn:415-433`, mirrored on aarch64):

- `OP_C_GLOBAL` (the `__c_global` builtin — address of an **external C** datum like
  libc's `environ`) → **GOT-indirect** (`FIX_GOTPCREL` → `R_X86_64_REX_GOTPCRELX`;
  aa64 `ADR_GOT_PAGE`+`LD64_GOT_LO12_NC`). **Only appears when the program links
  C.**
- `OP_DATA_SYM_ADDR` (a **local Binate** global — vtable slot, `__typeinfo`,
  `__ifaceid`) → **plain RIP-relative LEA** (`FIX_REL32_LEA` → `R_X86_64_PC32`),
  explicitly "NOT a GOT-indirect load."
- Ordinary cross-object **calls** → `FIX_REL32`, mapped to `R_X86_64_PLT32` only
  when the callee is undefined (cross-object), else `PC32` (`elf_util.bn:257-259`).
  **`PLT32` is field-identical to `PC32`** — same field, same `−4` addend — so the
  patcher treats a defined-symbol `PLT32` exactly as `PC32`.

**Consequence:** a hermetic Binate program (no `__c_global`) emits **no GOT relocs
at all**; the linker needs *zero* GOT synthesis or risky x86 `GOTPCRELX`
relaxation. That relaxation (the one with ABI rules about which instruction
sequences are legally rewritable — a genuine silent-miscompile hazard) is needed
**only** on the C-linking path, which is out of the hermetic scope. So D2 is a
near-non-issue for the recommended direction; it becomes real only if/when we link
against libc.

---

## 2. The five decisions (ratified 2026-08-26)

### D1 — Object scope: native-only MVP → full general reader

**Decided: native-only for the MVP, then extend to full-featured.** The MVP `bnld`
consumes only Binate-native-backend + `bnas` objects (controlled conventions —
single flags-by-name sections, `STT_NOTYPE` symbols, the enumerated
`FIX_*`-mapped reloc set), viable **today** for any `--backend native` build
(§1.1) — fast path to a working, testable linker.

**Then** it grows into a *general* ELF reader that also links the **default
(LLVM/clang) build**: clang objects bring COMDAT/section groups, `SHF_MERGE`
string-merge, full symbol types/sizes, `.eh_frame` passthrough, and clang's
PIC/GOT relocs (so the D2 extension rides along). This is a **planned phase (§11
M2+)**, not a rejected option — it's what makes `bnld` able to replace the system
linker in the *default* build, independent of whether `--backend native` ever
becomes the default backend. End state: `bnld` links both native and clang output.

### D2 — PIC/GOT: none in MVP, added post-MVP

**Decided: no GOT in the MVP; add GOT support in the post-MVP "full-featured"
phase.** A hermetic native program needs no GOT at all (§1.4-detail: `PLT32`≡`PC32`;
GOT-indirect relocs appear only from `__c_global`), so M0–M2 handle zero GOT. GOT
support then lands **together with the D1 general reader (§11 M2+)** — because
clang/LLVM-build objects are PIC and *do* carry GOT relocs, and because
`__c_global` / static-libc linking needs it. When it lands, prefer **building a
real static GOT** over x86 `GOTPCRELX` instruction relaxation (relaxation has ABI
rules about which sequences are legally rewritable — a silent-miscompile hazard).

### D3 — Driver model: compiled-first

Interpreted drivers are more feasible than the design doc thought (§9), but v1 =
**compiled drivers selected by `-target`** (zero interop). Interpreted
`-driver file.bn` is a named later stage. **Caveat (Min-2):** design the compiled
`pkg/link` `.bni` (§app-B) with the interpreted entry-shape in view *now* (design
doc Risk #6 — "design the API carefully"), so M5 can call it without a breaking
change.

### D4 — libgcc `__aeabi_*`: an orthogonal project (tracked separately)

**Decided: removing `libgcc.a` is its own project, not a linker sub-decision.**
The native arm32 backend calls EABI helpers for ops the 32-bit ISA can't do in one
instruction, pulled from **GCC's `libgcc.a`**. Verified set: **64-bit** integer
mul/div/mod/shift (`__aeabi_{lmul,ldivmod,uldivmod,llsl,llsr,lasr}`,
`arm32_int64_libcall.bn`) and **software float** (`__aeabi_{d,f}{add,sub,mul,div}`,
`…cmp*`, `arm32_float.bn`); **32-bit** integer (incl. divide) is hardware
`SDIV`/`UDIV`, no helper. (x64/aa64 have hardware 64-bit divide + FPUs, so this is
arm32-only.)

Porting these helpers to Binate/bnas assembly — to drop the GCC dependency, per
the **C-Free Target** goal — is **orthogonal to the linker** and tracked in
`claude-todo.md`. For `bnld` itself there is nothing to decide: it links
whatever archive/objects it's handed. The MVP either links `libgcc.a` via
**archive support (Step 5)** for 64-bit-int/float programs, or sidesteps it with a
32-bit-integer-only proof-of-life; once the port lands, `bnld` links the ported
objects instead — no `bnld`-side change.

### D5 — Mach-O: deferred behind ELF (with a dev-host caveat)

**Decided: defer Mach-O** (reader, writer, ad-hoc signing) behind ELF. macOS arm64
executables need **ad-hoc code signing** (`LC_CODE_SIGNATURE` → a SHA-256
code-directory); ld64 does it implicitly and **nothing in the repo reproduces it**
(no `codesign` call anywhere). Mach-O load-command/dyld complexity is also higher.
ELF (Linux + bare-metal) covers CI and the C-free goal first.

**⚠️ Dev-host caveat:** the working machine is an **arm64 Mac**, so `bnld`'s ELF
output cannot be *run natively* on it. Local validation of hosted output therefore
goes through **Linux CI**; **bare-metal arm32 runs under QEMU**, which is
host-agnostic and works fine on the Mac (another point for bare-metal-first, §3).
macOS-native runs of `bnld` output must wait for the Mach-O stage — a mild pull
toward doing Mach-O sooner than dead-last, though deferral stands for now.

---

## 3. Target sequencing (ratified 2026-08-26)

**Decision: hosted `linux-x64` (native, hermetic) first — not bare-metal arm32.**
Ordered to (a) avoid D4 in the early milestones and (b) attack M2's real risk
(hosted ELF-exec emission + a hermetic `_start`) from the start, rather than in a
throwaway warmup. **arm32 is deferred** — any non-trivial arm32 program pulls
libgcc `__aeabi_*` (D4), so arm32 waits on the orthogonal `__aeabi_*` port
(claude-todo); x64/aa64 have hardware 64-bit divide + FPUs and are D4-free.

| Order | Target | Reloc set | GOT? | `_start` | Local test | Notes |
|---|---|---|---|---|---|---|
| **1st** | linux-x64 (native, hermetic) | `ABS64`, `PC32`, `PLT32`(≡`PC32`) | none | own stub | Docker (qemu-emulated) + Linux CI (authoritative) | simplest hosted relocs → de-risks the reloc core first |
| **2nd** | linux-aarch64 (native, hermetic) | + `CALL26/JUMP26`, `ADRP`+`ADD/LDR_LO12`, `CONDBR19/TSTBR14` | none | own stub | **Docker native on the arm64 Mac** (fast) | reuses all ELF-exec/`_start` work; only relocs differ |
| **3rd** | general reader + GOT (links default LLVM/clang build) | + clang's full set incl. GOT | static GOT | — | Docker + CI | the D1/D2 extension (§11 M2+) |
| **later** | archives; Mach-O + signing; interpreted drivers; **arm32** (post-`__aeabi_*` port) | Mach-O set; arm32 `MOVW/MOVT/JUMP24` | `__c_global` only | — | — | D4/D5/§9 gates |

**Why hosted-x64-hermetic first (not bare-metal-arm32):**
- **D4 avoidance** (the user's key point) — arm32, bare-metal *or* linux, needs
  libgcc `__aeabi_*` for any 64-bit-int/float program; x64 needs none. Starting on
  arm32 would restrict the proof to 32-bit-int-only or immediately drag in the
  libgcc port.
- **Attacks the real risk early** (reviewer M-E) — bare-metal-arm32 de-risks only
  the read→resolve→patch→emit plumbing (~40%), not M2's ELF-exec
  segments/perms/page-align + hermetic `_start` (the ~60% that is the true
  long-pole). Hosted-x64 builds that machinery from milestone one.
- **Locally testable** (D5 caveat) — hosted output runs in Docker on the Mac (aa64
  natively, x64 emulated), with Linux CI authoritative; no dependence on a native
  macOS run.
- **Hermetic, not libc** — write our own tiny `_start` (argc/argv off the stack →
  `bn_entry` → `exit` syscall; belongs in `pkg/builtins/startup`) rather than
  transitionally linking crt1+libc. Linking libc would pull the general reader +
  archives (the M2+ phase) in prematurely; a hermetic `_start` keeps the MVP
  native-only.
- **x64 before aa64** — x64's `PC32`/`PLT32` are the simplest relocs, so the
  correctness-critical reloc core (the #1 risk) is proven on the easy case before
  aa64's `ADRP`/lo12 bit-splits are added atop the same, now-proven, ELF-exec
  machinery.

**The gentle warmup lives *inside* the x64 target, not in a throwaway detour:**
stage the ELF-exec sophistication — (1) a single RWX `PT_LOAD` + minimal `_start`
running a trivial native Binate program (proof of life), then (2) proper
multi-segment perms + page-alignment + the full `bn_entry`/startup chain.

---

## 4. Incremental steps

### Step 1 — shared format layer + ELF reader (M0)

- **Factor `pkg/binate/binfmt`.** ELF/Mach-O *constants* live in the writer
  packages (`asm/elf/elf_const.bn`, `asm/macho/macho_const.bn` — importable but
  siloed); the LE `BinBuf` encoder (`asm/elf/elf_util.bn:14`, `WriteU8/16/32/64` LE
  + `Align/WriteBytes/WriteZeros/WriteAddr`) is package-private (unexported
  `newBinBuf` :20); there are **no on-disk record structs** (fields emitted
  positionally) and **no LE decode**. Create `binfmt` with a public `ByteReader`
  (LE `ReadU8/16/32/64`, `ReadBytes`, `Seek`), a public `ByteWriter` (lift
  `BinBuf`), re-homed constants, and on-disk record structs (Ehdr/Shdr/Sym/Rela;
  nlist_64/relocation_info). Migrate the assembler writers onto it (pays down the
  existing `BinBuf` duplication rather than adding a third copy).
  - **⚠️ BUILDER constraint (F5/R4):** `asm/{elf,macho}` are in `cmd/bnc`'s
    **BUILDER-compiled** tree, so `binfmt` *becomes a new BUILDER-compiled
    package*. It must stay within what the **pinned** BUILDER accepts (test with
    `scripts/fetch-builder.sh --tool bnc` on a snippet, not just the current
    compiler), and CLAUDE.md's enumerated BUILDER surface must be updated. This is
    more than "keep existing files clean."
- **`parse_elf.bn`** — decode ELF64 (x64/aa64 — the first targets; ELF32/arm32
  dormant until the arm32 stage) into the input model (§5). Reference for exact
  field offsets: the test-private readback in `asm/elf/elf_test.bn`
  (`rdU16/rdU32/rdU64` :1002 + hard-coded offset walk).
- **Round-trip test corpus (Min-3):** assemble/emit a fixture with **both `bnas`
  AND the native backend** → read back → assert sections/symbols/relocs match. The
  native backend shares `asm/elf`, but native vs bnas may exercise different reloc
  kinds/section shapes, so the corpus must include native-emitted objects.

### Step 2 — resolve + relocate (M1 core; correctness-critical)

- **`resolve.bn`** — global symbol table via
  **`mapfn.MapFn[@[]readonly char, GlobalSym]`** with a hand-written FNV-1a byte
  hash + `streq` equality (§7.4; `@[]char` is not `Hashable`). Rules:
  - **Unresolved-symbol diagnostics (M-G — the most-used linker error):** collect
    every reloc whose symbol has no definition; report `name + referencing
    object/section`, non-zero exit. Do **not** silently patch against garbage.
  - **Weak-undefined → 0 (M-H):** an unresolved *weak-undefined* resolves to
    address 0 (does NOT error) — `libgcc`/`exidx` weak refs rely on this. Strong
    def wins over weak; two strong defs = duplicate-symbol error.
- **`relocate.bn`** — per-arch patch dispatch, using the **decoded on-disk reloc
  type** (a fresh normalized enum), **not** the writer's one-way `FIX_*` tag
  (`Relocation.Kind` in `asm.bni` is Kind→number only). **Addend location is
  format-dependent** — ELF32/ARM and Mach-O bake the addend into the section field
  (`elf.bn:15-17,313`); ELF64/RELA keeps it in `r_addend`. See the **relocation
  patch reference, §app-A** — that table (field layout + `S+A[-P]` formula +
  overflow check per kind) is the correctness core. **Cross-check every field
  layout against the assembler's inverse encoders** (`arm32.ResolveFixups`,
  `x64`/`aarch64` fixup resolvers) so linker and assembler agree bit-for-bit,
  including the ARM branch pipeline bias.
- **Cross-object test:** two objects with mutual external refs → resolve → patch →
  assert patched bytes.

### Step 3 — hosted x64 ELF-exec + hermetic `_start` + `linux_x64` driver (M1 → proof of life)

The first *runnable* target. Stage the ELF-exec sophistication so there is a
warmup inside the useful target (not a throwaway detour):

- **x64 reloc kinds** (§app-A): `ABS64`, `PC32`, `PLT32` (patched identically to
  `PC32` for a defined symbol). No GOT (hermetic — §1.4).
- **`emit_elf.bn` (exec)** — `ET_EXEC`, program header table, `e_entry`,
  `e_phoff`. **Stage 3a:** a single RWX `PT_LOAD` covering everything (crudest
  loadable ELF) to get a trivial native program running. **Stage 3b:**
  multi-segment `PT_LOAD` with correct RX/RW/R perms, `p_align` = page size, the
  `p_vaddr ≡ p_offset (mod p_align)` constraint, and BSS (`p_memsz > p_filesz`,
  zero-filled).
- **Hermetic `_start`** — with no libc, `pkg/builtins/startup` provides `_start`:
  read argc/argv/envp off the entry stack, call `bn_entry` (= `main.__entry` →
  `main.__init_all()` + `main.main()`), then the `exit`/`exit_group` syscall. Small
  hand-written stub (x64 syscall ABI); belongs to the `startup`/FFI-export effort,
  coordinated here. Stage 3a can start with a minimal `_start` that skips argv.
- **`linux_x64.bn` driver** — merge by name, assign addresses, resolve, patch,
  emit; set `e_entry = _start`.
- **`cmd/bnld/main.bn`** — args via `pkg/stdx/flags` (as bnfmt/bnas/bnlint/bni):
  `-o`, `-target`, `-e`, `-base`; compiled-driver dispatch.
- **e2e:** native-compile a tiny Binate program → `bnld` → run in a **linux-x64
  Docker container** (+ Linux CI as the authoritative x64 run) → check exit
  code/output; new `e2e/bnld-linux.sh`. **This is the proof-of-life milestone —
  first clang-free hosted link.**

### Step 4 — linux-aarch64 (M2, hardening the useful milestone)

- **aa64 reloc kinds** (§app-A): `ABS64`, `CALL26/JUMP26`, `ADRP` (page-hi21) +
  `ADD/LDR_LO12` (the bit-split relocs — the trickiest; cross-check against the
  assembler's inverse encoder), `CONDBR19`, `TSTBR14`. No GOT (hermetic).
- Reuses **all** of Step 3's ELF-exec/segment/`_start` machinery — only the reloc
  kinds and the `_start` syscall ABI differ. `linux_aarch64.bn` driver.
- **e2e:** runs **natively in Docker on the arm64 Mac** (fast loop) + Linux CI.
- Finish any Stage-3b hardening (perms/page-align/BSS) not yet done, so both hosted
  targets are proper multi-segment executables.

### Step 4b (deferred) — bare-metal arm32 + `bare_arm32` driver

Deferred until the `__aeabi_*` port lands (D4) — arm32 can't run non-trivial
programs without libgcc. When taken up: `emit_flat.bn` (concatenate at fixed base)
+ ELF-exec; `bare_arm32.bn` driver — `AssignAddresses(0x40000000)`, reproduce
`baremetal.ld`'s boundary symbols (`__bss_start/__bss_end`, `_stack_top`,
`__exidx_start/__exidx_end`) and `KEEP` the boot section (F6: `.text.startup`
*ordering* "no longer matters" — entry is `e_entry`, so the `KEEP` is defensive);
arm32 relocs (`ABS32`, `CALL/JUMP24`, `MOVW/MOVT_ABS`). e2e via
`qemu-system-arm … -semihosting -kernel` (host-agnostic; runs on the Mac).

### Step 5 — archives (.a)

`parse_ar.bn` (System V `ar`: `!<arch>\n`, 60-byte headers, `/`/`//` tables). Lazy
member selection: pull a member only if it defines a currently-undefined symbol;
iterate to fixpoint. Needed generally, and the D4 `libgcc.a` fallback.

### Step 6 — Mach-O + ad-hoc signing (v2)

`parse_macho.bn` + `emit_macho.bn` (`__PAGEZERO`, `LC_SEGMENT_64` set, `LC_MAIN`,
`LC_CODE_SIGNATURE`). Implement **ad-hoc `LC_CODE_SIGNATURE`** (SHA-256 code
directory over pages — documented, small, exacting). Largest single stage;
deferred behind everything (D5).

### Step 7 — interpreted drivers

Embed `pkg/binate/interp`, inject `pkg/link`, resolve the host→driver entry shape
(§9). Additive; the hard part is already proven.

---

## 5. Data model (input side — greenfield)

```
type InputObject struct {
    Path     @[]char
    Format   int            // ELF32 | ELF64 | MACHO64
    Machine  int            // EM_ARM | EM_AARCH64 | EM_X86_64
    Sections @[]InputSection
    Symbols  @[]InputSymbol
    Relocs   @[]InputReloc
}
type InputSection struct {
    Name @[]char; Kind int; Flags int      // Kind: TEXT|RODATA|DATA|BSS
    Data @[]uint8                           // empty for BSS
    Size uint; Align int                    // read sh_addralign — NOT the writer's hardcoded 16
    OutIndex int; OutOffset uint            // set by layout
}
type InputSymbol struct {
    Name @[]char; SecIndex int              // -1 undefined-external, -2 absolute
    Value uint; Binding int                 // LOCAL|GLOBAL|WEAK
    Resolved uint                           // final vaddr (set by resolve)
}
type InputReloc struct {
    SecIndex int; Offset uint; SymIndex int
    Kind int                                // DECODED on-disk reloc → linker-normal enum (NOT FIX_*)
    Addend int; AddendInField bool          // where the addend lives, per format
}
```

Reviewer-checked caveats baked in: decode the *on-disk* reloc, not `FIX_*` (which
is lossy for Mach-O `PAGEOFF12` — one number for two `FIX_*`; ELF mappings are
distinct); Binate-produced symbols are all `STT_NOTYPE`, size 0
(`elf.bn:331,345`) so the resolver must not rely on symbol type/size; honor
per-input `sh_addralign` (writer hardcodes 16). **Common symbols
(`SHN_COMMON`)**: `baremetal.ld` has `*(COMMON)`, so verify whether `rt`/native
actually emit any; if none in native output (likely — Binate globals are defined,
not tentative), the linker may reject `SHN_COMMON` as unsupported for v0 and
revisit. **`.init_array` is N/A** — Binate does init ordering in the compiler
(`main.__init_all` via `EmitInitDispatcher`, run by `bn_entry`), not via linker
init arrays; the linker only places the entry symbol.

---

## 6. Package structure

```
pkg/binate/binfmt/     — NEW shared (BUILDER-compiled): ByteReader/ByteWriter, records, constants
pkg/link/              — linker library (compiled; native-for-all-packages; NOT BUILDER-constrained)
    link.bni/.bn       — public API (§app-B) + Link()
    input.bn           — InputObject model (§5)
    parse_elf.bn / parse_ar.bn / parse_macho.bn
    resolve.bn resolve (mapfn+FNV) / layout.bn / relocate.bn (§app-A)
    emit_flat.bn / emit_elf.bn / emit_macho.bn
    drivers/ bare_arm32.bn, linux_x64.bn, linux_aarch64.bn, …
    *_test.bn
cmd/bnld/main.bn       — CLI (pkg/stdx/flags), driver dispatch
```

---

## 7. Stdlib gaps (explicit work items — not assume-existing)

1. **LE binary decode** — none (`elf.BinBuf` is encode-only, private). Build
   `binfmt.ByteReader`. *Small.*
2. **`os.Chmod`/`Fchmod` — MISSING.** `os.Create` = 0o666 (os.bn:207); `OpenFile`
   perm applies only at O_CREATE and is umask-subject. Needed to mark the output
   executable (0o755). **Rationale (Min-1 correction):** QEMU `-kernel` does *not*
   need `+x`; the driver here is the conformance runner's own `[ -x ]` gate and
   general usefulness (a real `ld` produces an executable file). Add a
   `chmod`/`fchmod` wrapper to `pkg/std/os/sys` + `pkg/std/os`. **Touches the
   syscall seam** (per-platform) — a small but cross-cutting shared change.
3. **Whole-file read/write** — no `os.ReadFile`/`WriteFile`; assemble from
   `Stat().Size()`+`Read`/`ReadAt` and `Create`+`Write`. *Trivial.*
4. **String hash + equality** — `@[]char` not `Hashable` (impls only primitives,
   `lang/order.bn:302`). FNV-1a + `streq` (`elf_util.bn:118` pattern), fed to
   `mapfn.MapFn` (injected `hashFn`/`eqFn`, `mapfn.bni:36`). *Trivial.*
5. **No `strings` utility package** (only `Builder`) — hand-roll any prefix/compare
   the linker needs. *Trivial.*

Item 2 is the only cross-subsystem one and sits on the critical path of any
runnable-output milestone.

---

## 8. Entry-point & placement (`bn_entry`/`startup` + the Phase-8 hand-off)

- **Entry symbol.** Bare-metal: crt0 `_start` → `bl bn_entry` directly
  (`runtime/baremetal_arm32/crt0.s`, `sp=0x41000000`). Hosted: `_start`→`bn_entry`
  (hermetic) or libc-`_start`→`main`→`bn_entry` (transitional). `bn_entry` =
  `main.__entry` (`mangle.bn:214`). Driver sets `e_entry` to the chosen symbol.
- **`#[section]` / `#[link_at]` — the Phase-8 hand-off.** `claude-todo.md:553`:
  *"The design's Phase 8 (baremetal linker-placement annotation) … is a
  linker-placement problem, tracked in plan-linker.md."* The linker is the home for
  honoring section-placement annotations (fixed-address symbols / named sections —
  MMIO, vector tables). This needs its **own mini-design** (annotation → object
  metadata → linker placement) — flag it as a sub-project. **It is not on the
  MVP path at all:** the hosted x64/aa64 targets need only ordinary segment
  placement; `#[section]`/`#[link_at]` is a bare-metal concern that arrives with
  the deferred arm32 work (Step 4b), which can hardcode the `baremetal.ld`
  placement first and generalize later (P4).

---

## 9. Interpreted drivers (deferred, not dropped)

- **Feasibility: needs-modest-work, no VM-core surgery.** `interp` exists (§1.2).
  The driver→library direction (interpreted driver calling compiled `pkg/link` on
  managed aggregates) IS the production `builder-comp-int` mechanism: injected
  native package + by-address managed aggregates through the call shim
  (`vm/vm_extern.bn:dispatchExternBinding`), no serialization.
- **The one real gap is the host→driver *entry* shape.** `RunFuncTyped` caps
  by-value structs at 64B (runfunc_typed.bn:58), rejects raw slices and `@T`/`@func`
  args (:208-214); `Value` has only scalar/string/string-slice ctors (value.bn).
  So you can't hand `@[]InputObject` to the driver via the typed path today. **Two
  ways out:** (a) entry `link(ctx int) int` with `ctx` a `bit_cast` handle to a
  compiled-built `LinkContext`, driver pulls inputs via injected `pkg/link` getters
  (uncapped by-address path — zero VM work); or (b) extend `RunFuncTyped`/`Value`
  ("Stage C": raw slices, managed pointers, >64B structs) — contained, has a 32-test
  harness. **Recommend (a)** first.
- **Caveats:** 7-slot extern-arg cap (`vm_extern.bn` execExternCall); cross-mode
  interface-arg substitution is fiddly (avoid interface-typed driver params early);
  float-in-V-register trampoline arg unimplemented (avoid float driver params);
  `pkg/link.bni` must be on the interpreter's interface path at driver type-check.

---

## 10. Testing strategy

- **Unit (reader):** round-trip against **both bnas and native** output (Min-3).
- **Unit (resolve/relocate):** synthetic multi-object cross-refs; assert resolved
  addresses + patched bytes; explicit unresolved-symbol and weak-undef-→0 cases.
- **e2e (M1, x64):** native-compile a tiny program → `bnld` → run in a **linux-x64
  Docker container** (+ Linux CI, authoritative) → exit code/output; **comparison
  test**: same objects linked by clang and by `bnld`, diff *behavior* (not bytes —
  layouts differ).
- **e2e (M2, aa64):** same, run **native in Docker on the arm64 Mac** + Linux CI.
- **e2e (arm32, deferred):** `qemu-system-arm … -semihosting -kernel` (host-agnostic,
  runs on the Mac) — once the `__aeabi_*` port (D4) unblocks arm32.
- **Determinism (M-I):** assert byte-identical output across two runs of the same
  inputs (matters for CI caching / reproducible builds) — a property to design in,
  not retrofit.
- **Conformance:** a `…-comp_native_*-bnld` mode eventually — **build the script,
  do NOT wire it into CI here** (lane-wiring is a separate user-owned decision).

---

## 11. Milestones & effort (re-based after review)

- **M0 — `binfmt` + ELF reader + round-trip.** Medium. Unblocks everything; pays
  down BinBuf/const duplication; BUILDER-cleanliness gate (F5).
- **M1 — resolve + relocate (x64) + hosted ELF-exec + hermetic `_start` +
  `linux_x64` driver + `cmd/bnld` + `os.Chmod` seam + Docker/CI e2e.** **Medium**
  (not "a few days"). Sub-item risk: x64 reloc math (§app-A) and resolve
  diagnostics are correctness-critical; the hermetic `_start` (argc/argv/syscall)
  is new; `os.Chmod` is a cross-platform syscall add. Staged: single-`PT_LOAD`
  proof (3a) → proper segments (3b). x64 is D4-free. **Proof of life — first
  clang-free hosted link.**
- **M2 — linux-aarch64 + segment/perms hardening.** **Large-ish** — but for a
  *revised* reason: **NOT** GOT relaxation (near-nil, §1.4) and **NOT**
  native-backend maturity (~done: hosted native modes carry ~9 xfails and
  self-compile gen2). The cost is aa64's `ADRP`/lo12 bit-split relocs (the
  trickiest — cross-check vs the assembler encoder) atop M1's now-proven ELF-exec +
  multi-segment `PT_LOAD`/perms/page-align machinery. aa64 runs native-fast in
  Docker on the arm64 Mac. Completes the *useful* (hosted, D4-free) milestone.
- **M3 — archives.** Small. Also the `libgcc.a`-read path for arm32
  64-bit-int/float programs (pending the orthogonal `__aeabi_*` port, D4/§13).
- **M2+ — full-featured reader + GOT (the D1/D2 extension; links the default
  LLVM/clang build).** **Large — the second deliberate mountain.** A *general* ELF
  reader (COMDAT/section groups, `SHF_MERGE`, full symbol types/sizes, `.eh_frame`
  passthrough) + **static-GOT construction**, so `bnld` links clang-produced
  objects and can replace the system linker in the *default* build — not just
  `--backend native`. Ratified as a planned phase; sequenced **after** the
  native-ELF MVP (M0–M2) proves the core. (Numbered "2+" to mark it as the phase
  after M2, not a late add-on.)
- **M4 — Mach-O + ad-hoc signing.** Large. Deferred (D5); the arm64-Mac dev host
  wants it eventually for local native runs.
- **M5 — interpreted drivers.** Small–Medium (entry-shape (a)).

**Headline:** M0+M1 stands up the hosted-x64 linker end to end — reader, resolver,
x64 relocs, ELF-exec emission, and the hermetic `_start` (the one real
MVP long-pole) — depending only on things that exist, and is D4-free. M2 adds aa64
atop that proven machinery. The one genuinely large *later* mountain is **M2+**
(the general reader + GOT that makes `bnld` usable for the default LLVM build).
Everything else (archives, Mach-O, interpreted drivers, and arm32 post-`__aeabi_*`)
is small-to-large but clearly sequenced behind those. Note the long-pole is
**linker + startup code**, never native-backend maturity (~done) or GOT relaxation
(near-nil for hermetic code).

## 12. Prerequisites & risks

- **P1: hermetic `_start`** (startup effort) — the true MVP long-pole, now on the
  **M1** critical path (hosted-x64 has no existing crt0 — the bare-metal crt0
  doesn't apply). A small x64-syscall stub in `pkg/builtins/startup`
  (argc/argv → `bn_entry` → `exit`); Stage 3a can start argv-less.
- **P2: `os.Chmod` seam** (§7.2) — on the critical path for any runnable output;
  small but cross-platform.
- **P3: `binfmt` BUILDER-cleanliness** (§4 Step 1 / F5) — new BUILDER-compiled
  package; test against the *pinned* BUILDER and update CLAUDE.md's surface.
- **P4: `#[section]`/`#[link_at]` mini-design** (§8) — before generalizing
  placement; not before v0 (hardcode first).
- **R1: reloc patch correctness** (§app-A) — silent-miscompile class; cross-check
  every field against the assembler's inverse encoders; test exhaustively.
- **R2: D1 coupling** (§2 D1) — the native-only MVP serves only `--backend native`
  builds; the M2+ general reader is what extends `bnld` to the default build
  (ratified as a planned phase, not left to chance).
- **R3: Mach-O ad-hoc signing** (D5) — real unimplemented format burden; ELF first.
- **R4: sequencing** — **ratified (§3): hosted-x64-hermetic first**, arm32 deferred
  (D4). Hosted output is Docker/CI-tested (the arm64-Mac can't run ELF natively);
  the earlier "bare-metal-first vs hosted-with-libc" question is closed.

---

## app-A. Relocation patch reference (the correctness core)

`S` = resolved symbol vaddr, `A` = addend (from field or `r_addend` per format),
`P` = vaddr of the patched location. Bit positions are indicative — **authoritative
source is the assembler's inverse encoder** (`arm32.ResolveFixups`, x64/aa64 fixup
resolvers); the linker must match them exactly.

**ARM32 (deferred, post-`__aeabi_*` port — REL, addend baked in field):**
| Reloc | Value | Field encoding | Range check |
|---|---|---|---|
| `R_ARM_ABS32` | `S + A` | 32-bit LE word | none |
| `R_ARM_CALL`/`JUMP24` | `(S + A) − P` | imm24 = `val >> 2` → insn[23:0]; A from field incl. pipeline bias (match bnas) | ±32 MB, `val & 3 == 0` |
| `R_ARM_MOVW_ABS_NC` | `(S + A) & 0xFFFF` | imm4=val[15:12]→insn[19:16], imm12=val[11:0]→insn[11:0] | none (NC) |
| `R_ARM_MOVT_ABS` | `((S + A) >> 16) & 0xFFFF` | same split | none |

**x86-64 (M1, first target — RELA, addend in `r_addend`, PC-rel already `−4`):**
| Reloc | Value | Field | Range |
|---|---|---|---|
| `R_X86_64_64` | `S + A` | 64-bit LE | none |
| `R_X86_64_PC32` / `PLT32` | `S + A − P` | 32-bit LE (PLT32 identical for defined S) | signed 32 |
| `R_X86_64_REX_GOTPCRELX` | GOT slot; `__c_global` only | build GOT entry `= S`, patch ref to slot (or relax `mov`→`lea`) | signed 32 |

**AArch64 (M2 — RELA):**
| Reloc | Value | Field | Range |
|---|---|---|---|
| `R_AARCH64_ABS64` | `S + A` | 64-bit LE | none |
| `CALL26`/`JUMP26` | `(S+A−P) >> 2` | insn[25:0] | ±128 MB, `&3==0` |
| `ADR_PREL_PG_HI21` | `page(S+A) − page(P)` `>>12` | immlo→[30:29], immhi→[23:5] | ±4 GB |
| `ADD_ABS_LO12_NC` | `(S+A) & 0xFFF` | insn[21:10] | none |
| `LDST64_ABS_LO12_NC` | `((S+A)&0xFFF) >> 3` | insn[21:10] (scaled ×8) | 8-aligned |
| `CONDBR19` | `(S+A−P) >> 2` | insn[23:5] | ±1 MB |
| `TSTBR14` | `(S+A−P) >> 2` | insn[18:5] | ±32 KB |
| `ADR_GOT_PAGE`/`LD64_GOT_LO12_NC` | GOT; `__c_global` only | GOT entry + page/lo12 to slot | — |

## app-B. `pkg/link` public API sketch (the driver contract — design once, Risk #6)

```
// Errors are values (@[]readonly char, empty = ok), Go-style multi-return.
func ReadObject(path @[]readonly char) (@InputObject, @[]readonly char)
func ReadArchive(path @[]readonly char) (@[]@InputObject, @[]readonly char)   // Step 5

func NewSymbolTable() @SymbolTable
func (t @SymbolTable) AddObject(obj @InputObject)
func (t @SymbolTable) Resolve() @[]readonly char            // "" ok; else unresolved/dup report
func (t @SymbolTable) Lookup(name @[]readonly char) (uint, bool)

func NewOutputSection(name @[]readonly char, flags int, align int) @OutputSection
func PlaceInput(out @OutputSection, in @InputSection)
func AssignAddresses(secs @[]@OutputSection, base uint)

func PatchRelocations(objs @[]@InputObject, t @SymbolTable) @[]readonly char   // "" ok; else OOR/unrelaxable

func EmitFlat(secs @[]@OutputSection, path @[]readonly char) @[]readonly char
func EmitElfExec(secs @[]@OutputSection, entry uint, machine int, path @[]readonly char) @[]readonly char

// Driver contract (compiled, v1):
func LinkTarget(inputs @[]@InputObject, opts @LinkOpts) @[]readonly char
// Interpreted (deferred, entry-shape (a)): link(ctx int) int  where ctx bit_casts to @LinkContext
```

## app-C. Other linker mechanics to design in (from review)

- **BSS zeroing:** `.bss` placed with `p_memsz > p_filesz`; loader/crt zeroes
  `[__bss_start,__bss_end)` (bare-metal) or the segment tail (hosted). Emit must
  set filesz/memsz correctly.
- **Segment loadability:** `p_vaddr ≡ p_offset (mod p_align)` for every `PT_LOAD`
  (M2).
- **Deterministic output:** stable section/symbol ordering; no timestamps; assert
  byte-identical re-runs (§10).
- **Debug sections:** the design doc says "pass through"; v0/v1 may simply **drop**
  `.debug_*` (state this explicitly rather than silently). `.ARM.exidx` on
  bare-metal is kept + bounded by `__exidx_start/end`.
- **RELRO / TLS:** out of scope; state it (Binate has no TLS today).

## 13. What the adversarial reviews changed

Two independent reviews (one fact-verifier, one reasoning-adversary), each with
repo access; findings I verified against code before folding in:

- **Corrected a false premise** (both a draft and a recon agent parroted the stale
  `main.bn:348` comment): deps *are* compiled native under `--backend native`
  (§1.1). This *strengthened* D1=native (viable now) and removed a bogus
  "un-shippable for years" gate.
- **Deflated D2** (§1.4): GOT is `__c_global`-only; `PLT32`≡`PC32`. The "GOT/PLT
  relaxation subproject" was mostly a myth for hermetic code.
- **Re-based the M2 long-pole** (§11): native maturity is ~done (≈9 xfails,
  gen2-self-compiling); the real cost is hosted ELF-emit + hermetic `_start`.
- **Added the detail a "detailed plan" was missing:** reloc patch math (§app-A),
  public API (§app-B), unresolved-symbol/weak-undef rules (§4 Step 2), and the
  missing-mechanics list (§app-C).
- **Re-scoped D4** (not "small"; enumerated the real `__aeabi_*` set; v0 sidesteps
  it) and surfaced the **sequencing** and **D1-coupling** steelmen (§3, §2 D1) as
  genuine user decisions rather than settled recommendations.
- Minor fixes: `binfmt` is a new BUILDER-compiled package (F5); `interp.Interp`
  isn't "opaque" (F3); reloc-decode loss is Mach-O-only (F4); `.text.startup`
  ordering "no longer matters" (F6); chmod rationale is the runner's `[ -x ]` gate,
  not QEMU (Min-1).

## Implementation progress

A running log as the linker is built (see §11 for the milestone plan).

- **M0 — ELF64 reader:** ✅ landed `7667300b9` (`pkg/binate/link`).  Parses an
  ELF64 relocatable object into the InputObject model: header + content sections
  (SHF_ALLOC-filtered, so `.note.GNU-stack`/`.debug_*` are dropped) + `.symtab`
  symbols + `.rela.*` relocations.  Structural offsets/sizes are validated against
  the file length (truncated/malformed → error, not abort); a 64-bit field that
  would not fit a machine int fails loud (readOff) rather than truncating on a
  32-bit target.  Validated by round-tripping the assembler's own ELF writer, plus
  reject-non-ELF / reject-truncated and byte-reader unit tests.  Deferred: the
  `binfmt` factoring (ELF constants redeclared locally for now); SHN_ABS/SHN_COMMON
  symbol kinds (→ the resolver); a golden-bytes fixture pinning the layout
  independently of the writer.
- **M1 (resolve) — symbol resolver:** ✅ landed `579e856c8` (`resolve.bn`).
  `Resolve` builds a global symbol table over a set of objects: strong-over-weak
  precedence (either order), duplicate-strong error, undefined-GLOBAL error,
  weak-undefined → 0, locals excluded; `SymbolTable.Lookup` maps a name to its
  defining object/symbol (linear scan; hashed index a follow-up).  Tested over
  synthetic multi-object inputs.  COMMON tentative defs stay deferred (foreign
  objects only).  Next: layout (output sections + address assignment).
- **M1 (layout) — section layout + addressing:** ✅ landed `29b32d932`
  (`layout.bn`).  Merges content sections into per-kind output sections
  (.text/.rodata/.data/.bss), packs each kind with per-input alignment, records
  each input's placement (OutIndex/OutOffset on InputSection), and assigns virtual
  addresses from a base in load order (uint address math for bases ≥ 2^31; bss
  reserves size without data).  Tested for merge/ordering/addressing/alignment/
  rodata.  Segment perms + page alignment left to emit.  Next: relocation patching.
- **M1 (relocate) — relocation patching (x86-64):** ✅ landed `a111bfc23`
  (`relocate.bn`).  Computes each patch site's address P and target symbol's
  address S (following undefined refs cross-object; weak-undef → 0), applies
  R_X86_64_64 (S+A), PC32/PLT32 (S+A−P, signed-32 checked), rejects GOT/unknown
  kinds, and writes into the merged output Data.  Bounds-checks the symbol index
  (also in ReadObject) and the write; errors (not silently 0-writes) an
  unresolvable defined symbol.  Tested end-to-end: a cross-object call lands on an
  independently-derived target address; ABS64 patches the target's address.
  Next: emit (ELF-executable writer).
- **M1 (emit) — ELF64 executable writer:** ✅ landed `87a6abb48` (`emit_elf.bn`).
  EmitElfExec writes a static ET_EXEC with one loadable PT_LOAD covering the ELF
  header + program headers (so AT_PHDR is mapped) through the section data; each
  section maps to its assigned vaddr, p_offset stays page-congruent (incl. a
  non-page-aligned base), .bss is memsz-only, and guards reject a too-low base /
  out-of-image entry.  Output is not yet +x (no os.Chmod).  Tested by re-parsing
  the emitted file for the load-critical invariants.

**Pipeline status:** read → resolve → layout → relocate → emit are all landed and
unit-tested (30 tests).  Remaining for a *runnable* hosted-x64 proof-of-life (M1
completion): a hermetic `_start` (startup), an `os.Chmod` stdlib add to mark the
output executable (§7.2 gap), a `cmd/bnld` CLI + a public `Link()` entry chaining
the stages, and a Linux-CI e2e (emit → chmod → run → check exit) — the one check
that proves loadability, which the macOS dev host cannot run locally (D5).
- **M1 (driver) — Link() + executable output:** ✅ landed `da00f402c` (`link.bn`).
  Link chains read→resolve→layout→relocate→emit over a list of object paths
  (rejecting a machine mismatch), resolves the entry symbol's address, and writes
  the executable.  The emitter now creates the output 0o755, unlinking any existing
  file first (so the create mode applies on re-link and re-linking a running build
  avoids ETXTBSY) — no os.Chmod needed.  Tested end-to-end incl. a verified-patched
  cross-object call and a re-link-over-existing case.  Next: cmd/bnld CLI, then a
  Linux e2e proof-of-life.
- **M1 (cmd/bnld) — linker CLI:** ✅ landed `999b81438` (`cmd/bnld/main.bn`).  Thin
  flags-based front end over link.Link (-o/-e/-target/-version + positional
  objects), parseArgs -> CLIArgs, unit-tested.  No build-script/CI wiring yet
  (separate decision).  Next: a Linux e2e proof-of-life (bnas exit-syscall program
  -> bnld -> run -> check exit code).

### 🎉 M1 hosted-x86-64 proof-of-life: COMPLETE (2026-08-27, `472f542d5`)

`e2e/bnld-linux.sh` proves it end to end: a tiny `exit(42)` x86-64 program
assembled by `bnas`, linked by the Binate-native `bnld` (no clang/ld in the link),
produces a static ELF64 executable that **runs on Linux and returns 42**.  The full
read→resolve→layout→relocate→emit pipeline plus the `Link()` driver and `cmd/bnld`
CLI are landed and unit-tested; the e2e joins the CI e2e matrix.  This closes the
M0/M1 core (reader, resolver, layout, relocate, emit, driver, CLI, proof-of-life).

Remaining hardening / next targets: per-section RX/RW segments (stage 3b — the emit
uses a single RWX PT_LOAD today); a richer runtime proof (cross-object call / rodata
at runtime); then **aarch64** (the 2nd target — reader handles ELF64 already;
relocate needs the ADRP/lo12 kinds, emit is arch-agnostic); then archives and,
gated on a hermetic `_start` (startup), linking a real bnc-compiled Binate program.
- **M1 runtime proofs:** ✅ e2e extended (`b3efae819`) with a hello-world (writes
  "hi\n" via a .rodata string reached by a PC-relative reloc) — proving rodata +
  cross-section relocation run correctly, not just in unit tests.
- **M2 (aarch64) — target wiring:** in progress.  Reader/layout/emit are
  arch-agnostic (emit takes the machine); adding EM_AARCH64 + `-target
  linux-aarch64` and proving Link→emit produces a valid aa64 ELF exec.  aa64
  relocation patching (ADRP/lo12/CALL26 bit-fields) and a Docker-linux/arm64
  run-e2e are the following rounds.
- **M2 (aarch64) — target wiring:** ✅ landed `254f40a7e`.  EM_AARCH64 +
  `-target linux-aarch64`; the machine-agnostic pipeline links a reloc-free aa64
  object to a loadable aa64 ELF exec.  The x86-only relocator rejects real aa64
  relocs cleanly (no number collision; ELFCLASS64 excludes ILP32) rather than
  mis-patching — pinned by a test.  Next: aa64 relocation patching (CALL26/JUMP26,
  ADRP page-split, ADD/LDR lo12, condbr/tstbr), then a linux/arm64 run-e2e.
- **M2 (aarch64) — relocation patching:** ✅ landed `f49b3b94b`.  `Relocate` now
  dispatches on the object's machine to `patchAArch64` (x86-64 logic extracted to
  `patchX64`).  Handles ABS64 (S+A, 8-byte data), the imm26 branches (CALL26/
  JUMP26, (S+A−P)>>2), ADRP (page delta split across immlo/immhi), ADR imm21,
  ADD/LDST64 lo12 (low 12 bits of S+A, LDST64 scaled by 8), and CONDBR19/TSTBR14;
  `patch32Field` does the read-modify-write of a 32-bit instruction field.  Every
  PC-relative field is range-checked (mirroring x86-64 PC32) so an over-long
  displacement errors loudly instead of truncating into a wrong target; GOT kinds
  are rejected.  Tested end-to-end: a cross-object BL lands on its target, an ABS64
  pointer holds a symbol's absolute address, and each field's boundary range check.
  Next: a Docker linux/arm64 run-e2e exercising ADRP+ADD_LO12 at runtime.
- **M2 (aarch64) — runtime proof + ELF-aarch64 assembler output:** ✅ landed
  `846802a77`.  The blocker: `bnas` only ever emitted Mach-O for aarch64, so
  `bnld` (ELF-only) couldn't link a linux-aarch64 program end to end.  Fix:
  `assemble.AssembleFile` gained an `osName` param and picks the object format by
  OS (linux → ELF, darwin → Mach-O; `""` keeps the per-arch host default, so
  every existing caller — incl. bnc's in-process runtime assembly — is unchanged);
  `bnas` gained `-target <os>-<arch>` (e.g. `linux-aarch64`), routing aarch64+linux
  to the existing `elf.WriteAArch64`.  `e2e/bnld-linux-aarch64.sh` then assembles +
  links + **runs** an exit(42) and a hello program under linux/arm64 (native, or
  Docker linux/arm64 — native on Apple silicon, binfmt/qemu on x86-64 Linux); hello
  reaches its `.rodata` string via `adr`, so an R_AARCH64_ADR_PREL_LO21 relocation
  is applied and exercised on a real kernel (verified locally: prints "hi", exits
  0).  Deferred: ADRP+ADD_LO12 has no text-asm `:lo12:` syntax yet, so that reloc
  pair is unit-proven but not yet runtime-proven (would need an assembler operand
  feature).  Next: per-section RX/RW segments (stage 3b), then archives / a real
  bnc-compiled program (gated on a hermetic `_start`).
- **Stage 3b — per-section RX/RW segments (W^X):** ✅ landed `5fef2398a`.  The
  emitter mapped everything with one RWX PT_LOAD; now it emits two page-disjoint
  segments — read-only/exec (.text/.rodata) and read-write (.data/.bss) — so no
  segment is both writable and executable.  The RW group's page-aligned start is
  decided in Layout (addresses freeze before relocation), while segment
  permissions stay an emit concern; a single-group program still emits one
  segment, and the lowest segment covers the ELF header + phdrs (AT_PHDR mapped).
  Verified for page-aligned and non-page-aligned bases, a bss-only writable
  segment, and W^X + page-congruence in checkLoadable; e2e/bnld-linux.sh gains
  `datax`, which loads its exit code from .data and runs on a real kernel (proves
  the two-segment image loads and .data is mapped/readable).  Next: archives, or a
  real bnc-compiled program (gated on a hermetic `_start`).

### 🎉 Real bnc-compiled program linked by bnld: PROVEN (round 14 recon)

A genuine bnc-native-compiled Binate program, linked **entirely by bnld** (no
clang/ld anywhere), runs on a real Linux kernel and exits 42 — the value returned
by the bnc-compiled `compute()`.  This validates the whole from-scratch pipeline
end to end: bnc native x86-64 codegen → bnas → bnld (read → resolve → layout →
relocate → two-segment W^X emit) → execve.  The emitted image is a 118 KB static
ELF64 with two PT_LOADs (R+X 0x5, R+W 0x6) and a 4 MiB `.bss` arena carried as
memsz-only (not in the file) — the layout/emit handling all confirmed on the real
program, not just unit fixtures.

**What linking a real `main` program takes (the recon payoff):**

- `bnc --backend native --target x86_64-linux -c -o <stem> prog.bn` emits **five**
  ELF objects — `main` plus the auto-pulled runtime packages `builtins/rt`,
  `builtins/reflect`, `builtins/lang`, `builtins/startup` — not one.  (Search paths
  via `scripts/binate-paths.sh` → `BINATE_PACKAGE_INTERFACE_PATH` /
  `BINATE_PACKAGE_IMPL_PATH`.)
- The program entry is **`bn_entry`**: an ordinary function (sat-registry build →
  `__init_all` → `main`) that *returns* and never touches the argv stack, so a
  custom `_start` can simply `call bn_entry`.  Functions mangle as
  `bn_F1_4_main1_7_compute` etc.
- The only **genuinely-undefined (external) symbols** across the five objects are
  five libc names: `malloc`, `calloc`, `free`, `write`, `abort`.  Everything else
  is inter-object and resolves once the five link together.  A hand-written
  hermetic shim (`_start` + a bump allocator over a `.bss` arena + `write`/`exit`
  syscalls) satisfies them with no libc — then `bnld -o prog shim.o <5 objects>`
  links and the binary runs.

**Follow-on work this surfaced:**

1. **C-free hermetic runtime (the real project):** those five libc symbols are the
   remaining C dependency of a statically-linked Binate program.  A fully C-free
   link (the C-Free Target goal) needs Binate/asm implementations of the allocator
   and the `write`/`abort` primitives — the shim here is a throwaway stand-in.
2. **Assembler ergonomics (minor, GAS-compat):** the text assembler requires an
   external symbol to be `.global`-declared *before* it is referenced, else it errors
   ("assembly failed") instead of emitting a relocation; comments are `//` only
   (not `#`/`;`).  BSS is reserved with `.zero N` inside `.section bss`.
3. **bnld needed no changes** to link a real program — the reader/resolver/layout/
   relocate/emit stack built over rounds M0–13 was already sufficient for a real
   native object graph.  Next candidates: a reproducible e2e for this (heavier — it
   builds bnc), archives, or the C-free runtime.

**Round 15 — regression test landed (`a798eb726`):** `e2e/bnld-real-program.sh`
captures the milestone above: it builds bnc+bnas+bnld, native-compiles the trivial
program, links it with the hermetic shim via bnld, and runs it (asserting exit 42
from the compiled `compute()`).  The mangled symbol is discovered with `nm` (not
hard-coded).  It runs only on a native x86-64 Linux host and skips early
everywhere else (no Docker/qemu; bnld's linking is already covered on other lanes
by `bnld-linux.sh`).  bnld itself needed no changes — the M0–13 stack already
links a real native object graph.

- **Round 16 — aa64 `#:lo12:` text operand + ADRP+ADD runtime proof:** ✅ landed
  `4dbd8bb4e`.  The aa64 text assembler gained the `add rd, rn, #:lo12:label`
  operand (low-12-bits half of an ADRP+ADD address materialization → FIX_ADD_LO12),
  via a side-effect-free lookahead confined to `add`.  `e2e/bnld-linux-aarch64.sh`'s
  new `hellopg` reaches its `.rodata` string with ADRP+ADD instead of ADR, so bnld
  applies R_AARCH64_ADR_PREL_PG_HI21 + R_AARCH64_ADD_ABS_LO12_NC and they run on a
  real aa64 kernel — closing the R12 deferral (those relocs were unit-proven only).

- **Round 17 — real-program e2e exercises the runtime memory path:** ✅ landed
  `e15fd8128`.  The real-program e2e's `compute()` returned a constant, so the
  linked binary proved only that static init ran.  It now allocates a managed
  slice, fills it in a bounds-checked loop, and sums it (→ 42), so a passing run
  proves the runtime memory path — `MakeManagedSlice` → malloc (shim bump
  allocator), indexed-access bounds checks, refcount cleanup — is linked by bnld
  and executes on a real kernel.  External surface unchanged (same five libc
  symbols); verified end to end.

- **Round 18 — report all undefined symbols at once:** ✅ landed `4ffaa2d1a`.  The
  resolver returned on the first undefined symbol, so a link missing several (e.g.
  a real program's runtime referencing malloc/calloc/free/write/abort without a
  shim) took one re-link per missing symbol to enumerate.  Resolve now collects
  every distinct undefined GLOBAL reference (deduped across objects) and reports
  them together — `undefined symbols: malloc, calloc, free, write, abort` — while a
  single miss keeps the `undefined symbol: X` wording.  The detection predicate is
  unchanged; verified end to end (bnld lists all five) plus dedup/order/singular/
  cross-resolved-exclusion unit tests.

- **Round 19 — duplicate-symbol error names the colliding objects:** ✅ landed
  `f42a454b0`.  Companion to round 18: a duplicate said only `duplicate symbol: X`;
  it now names both objects — `duplicate symbol: dup (defined in a.o and b.o)` —
  the actionable detail when linking a large object graph.  Tested + verified end
  to end.

- **Round 20 — aa64 `ldr [xn, #:lo12:]` operand + LDST64 lo12 runtime proof:** ✅
  landed `7419309b8`.  Completes the aa64 lo12 operand pair (ADD from round 16, now
  LDR): the text assembler accepts `ldr xt, [xn, #:lo12:label]` → LdrImmLabel
  (FIX_LDR_LO12 → R_AARCH64_LDST64_ABS_LO12_NC), via a bracket-wrapped side-effect-
  free lookahead; a 32-bit destination is rejected (the reloc always encodes the
  64-bit scaled form).  `e2e/bnld-linux-aarch64.sh`'s new `dataval` loads an 8-byte
  value from .data via ADRP+LDR and exits with it (42), runtime-proving
  LDST64_ABS_LO12_NC on a real aa64 kernel (and the two-segment W^X image).

### Direction (from 2026-08-27): make bnld able to REPLACE the external linker

The goal is that bnc never needs clang/ld: bnld should link real programs against
real libraries.  C-freeness is explicitly NOT a goal (on Unix we interact with C
libraries).  So the work is linker features — archives, then linking against libc,
etc. — not replacing libc.

- **Round 21 — read System V / GNU `ar` archives:** ✅ landed `a80e98c56`.  The
  ELF object reader was refactored into `parseObjectBytes(bytes, path)` (parses an
  object from an in-memory range, copying everything out), and `ReadArchive` reads
  a `.a` — magic, 60-byte member headers, short/`/N`-long names via the `//` table,
  skipping the `/` symbol index and `//` table — returning each object member as an
  InputObject labeled `path(member)`.  GNU/SysV format (what C libraries use on
  Linux); bounds-safe on malformed input (incl. a 32-bit-overflow-safe size check).
  Validated against a real llvm-ar GNU archive.  Next: symbol-based member
  inclusion in Link() (pull only the members that resolve undefined refs) + the CLI.

- **Round 22 — extract archive members on demand in Link:** ✅ landed `b4e5ef255`.
  Link classifies each input by magic (ELF object → always linked; `.a` → members
  pulled on demand) and `selectMembers` extracts the members that resolve strong
  undefined references, to a fixpoint (a pulled member's own refs pull further
  members).  Weak references don't force extraction, and — agreeing with Resolve —
  a symbol already defined strong OR weak is satisfied (a weak default isn't
  overridden by dragging in the archive; a review caught this).  `cmd/bnld` needs
  no change (it already passes positional inputs to Link).  Validated out-of-band:
  bnld links a program against a real llvm-ar GNU archive, extracts only the needed
  member, and the binary runs.  Next: a CI e2e for archive linking; then `-l`/`-L`
  library search in the CLI.

- **Round 23 — CI e2e for archive linking:** ✅ landed `c77e3ef26`.
  `e2e/bnld-archive-linux.sh` assembles two objects (a `helper` that exits 42 and an
  unused one), bundles them into a GNU archive with the system `ar`, links a `main`
  that calls `helper` against the archive with bnld — which extracts the `helper`
  member and leaves `unused` out — and runs it (exit 42).  Native x86-64 Linux only
  (GNU `ar` + native run); skips elsewhere, no Docker.  Next: `-l`/`-L` library
  search in the CLI.

- **Round 24 — `-L`/`-l` library search in the CLI:** ✅ landed `782b1a66b`.  bnld is
  now invocable the way ld is: `-L<dir>` adds a search directory and `-l<name>`
  links `lib<name>.a` from the first `-L` dir that has it (attached `-Ldir`/`-lc` and
  space-separated forms).  These are pulled from argv before the flag parser and
  resolved to archive paths (appended to the inputs — Link extracts to a fixpoint,
  so order is irrelevant); an unresolvable `-l` is a usage error.  Unit-tested and
  validated end to end (`bnld -L dir -l helper` links + runs).  Next: a runtime e2e
  of transitive archive extraction via -l/-L.

- **Round 25 — transitive archive extraction via -L/-l at run time:** ✅ landed
  `a5e735ca1`.  The archive e2e now also links `main2` (calls `entrypt`, in the
  archive, which calls `helper2`, also in the archive) with `bnld -L dir -l stuff`:
  bnld extracts `entrypt` and then, from its reference, `helper2` (fixpoint),
  leaving `unused2` out, and the binary runs and exits 42 — proving the -L/-l CLI
  and correct runtime linkage of transitively-extracted members.

**Archive linking (rounds 21–25) — done.** bnld now links against `.a` archives the
way ld does: read GNU/SysV archives, extract members on demand by symbol (to a
fixpoint, weak-aware), invoke with `-L`/`-l`, all runtime-proven in CI.  bnld links
real GNU archives (validated against llvm-ar output).  Remaining toward full
ld-replacement: symbol-index-driven lazy extraction (skip parsing unused members of
a large library), and eventually dynamic linking (.so) — both future work, not
started.
