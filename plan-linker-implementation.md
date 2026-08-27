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

Five scope decisions the **user owns** and should ratify before Step-1 code. Each
has a recommendation *and* the steelman for the rejected option (adversarial
reviews pushed hard on these — don't treat the recommendations as settled):

- **(D1) Object scope** — general clang/GCC reader vs. Binate-native-backend-only.
  → recommend **native-only** (already viable *today*, see §1.1 correction), but
  see the §2 D1 steelman: native-only can't touch the *default* (LLVM) build until
  native becomes the default backend.
- **(D2) PIC/GOT** — how to handle position-independent relocs. → **mostly a
  non-issue** once you separate GOT from PLT (§2 D2 / §1.4): a hermetic Binate
  program emits **zero GOT relocs**; the pervasive `PLT32` is field-identical to
  `PC32`. GOT only appears via `__c_global` (i.e. only when linking C).
- **(D3) Driver model** — compiled drivers now, interpreted `-driver file.bn`
  later? → recommend **compiled-first** (interpreted deferred, not dropped).
- **(D4) libgcc `__aeabi_*`** — replace with Binate/bnas helpers vs. read the GCC
  archive. → recommend **v0 sidesteps it** (proof-of-life uses no div/float),
  then decide; *not* "small" (§2 D4).
- **(D5) Mach-O** — defer behind ELF (code-signing gap)? → recommend **yes**.

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

## 2. The five decisions (recommendation + steelman)

### D1 — Object scope: native-only vs. general reader

**Recommend native-only:** `bnld` consumes only Binate-native-backend + `bnas`
objects (controlled conventions — single flags-by-name sections, `STT_NOTYPE`
symbols, the enumerated `FIX_*`-mapped reloc set). This is viable **today** for any
`--backend native` build (§1.1) — it does *not* wait on native maturing.

**Steelman the rejected general reader (the real cost of native-only):** `bnld`
under D1 can only link `--backend native` builds; it can **never** replace the
linker in the **default (LLVM) build** until `--backend native` becomes the
*default* backend — a separate, large, unrelated decision. A general ELF reader
(eating clang objects: COMDAT/section groups, `SHF_MERGE`, full symbol
types/sizes, `.eh_frame`, clang's PIC/GOT) would deliver a **clang-free *link*
step for the default build immediately** (compile still uses `clang -c`, which is
orthogonal to "stop invoking `ld`"). So for the narrow goal "stop shelling out to a
system linker," general-reader is strictly more useful sooner; native-only is the
right call only if the goal is the *fully* C-free/hermetic end state and we're
willing to gate on native-default. **User decides.** This plan is written for
D1=native.

### D2 — PIC/GOT

Per §1.4-detail, **for hermetic Binate programs there is nothing to decide**: no
GOT, and `PLT32` patches as `PC32`. The only open question is the **C-linking
path** (`__c_global` / static libc), which needs either a static GOT or x86
`GOTPCRELX` relaxation. **Recommend: out of scope until we link C**; when it lands,
prefer building a real GOT over instruction relaxation (avoids the ABI
legal-sequence hazard). No decision blocks M0–M2.

### D3 — Driver model: compiled-first

Interpreted drivers are more feasible than the design doc thought (§9), but v1 =
**compiled drivers selected by `-target`** (zero interop). Interpreted
`-driver file.bn` is a named later stage. **Caveat (Min-2):** design the compiled
`pkg/link` `.bni` (§app-B) with the interpreted entry-shape in view *now* (design
doc Risk #6 — "design the API carefully"), so M5 can call it without a breaking
change.

### D4 — libgcc `__aeabi_*`

**Not "small."** `semihost.s` already provides `memcpy/memmove/memset/memcmp/
abort`, so those are **not** the issue. What libgcc actually supplies (per
`arm32_float.bn`, `arm32_int64_libcall.bn`, and
`scripts/lib/find-arm32-baremetal-toolchain.sh:9` "lld pulls AEABI helpers
`__aeabi_ldivmod` etc."): integer div/mod (`__aeabi_{idiv,uidiv,idivmod,
uidivmod}`), 64-bit `__aeabi_{lmul,ldivmod,uldivmod}` (+ shifts), and float soft-
helpers (`__aeabi_{d,f}{add,sub,mul,div,cmp*}`). Writing these ABI-exact in asm is
bug-prone (64-bit long division especially); floats are a large lift.

**Recommend:** **v0's proof-of-life uses a program that divides nothing and uses no
floats → pulls essentially no `__aeabi_*`, so D4 doesn't block the proof.** When
linking real programs, choose: implement the integer helpers as `bnas` objects
(bounded), or bring **archive support (Step 5) forward and read `libgcc.a`**
(lower-risk, at some hermeticity cost). Enumerate the exact set the target program
pulls (via `nm` on the objects) before committing.

### D5 — Mach-O: defer behind ELF

macOS arm64 executables need **ad-hoc code signing** (`LC_CODE_SIGNATURE`); ld64
does it implicitly today and **nothing in the repo reproduces it** (no `codesign`
call anywhere). Mach-O load-command/dyld complexity is also higher. **Recommend
ELF-first**; macOS dev-loop keeps using clang-link until a later Mach-O+signing
stage.

---

## 3. Target sequencing (reloc-complexity gradient)

| Stage | Target | Reloc set | GOT? | Base | Output | Externals | Gates on |
|---|---|---|---|---|---|---|---|
| **v0** | bare-metal arm32 (native) | `ABS32`, `CALL/JUMP24`, `MOVW/MOVT_ABS` | none | 0x40000000 | flat / ELF-exec | none (div-free proof) → `__aeabi_*` later | native arm32 backend (exists) |
| **v1** | linux-x64 + linux-aarch64 (native) | + `PC32/PLT32`, `CALL26/JUMP26`, `ADRP/ADD/LDR_LO12`, `CONDBR19/TSTBR14` | none (hermetic) | ELF default | ELF64 exec (`PT_LOAD` segs) | hermetic `_start` (startup effort) | hosted ELF emit + `_start` |
| **v2** | archives → Mach-O + signing → interpreted drivers | + Mach-O set; GOT if linking C | `__c_global` only | — | Mach-O exec | codesign | D5, §9 |

**Honest note on sequencing (M-E):** v0 (absolute relocs, fixed base, single
segment, no libc, semihost exit) shares little with v1's *real* unknowns
(multi-segment `PT_LOAD` with perms + the `p_vaddr ≡ p_offset (mod p_align)`
loadability constraint; hermetic `_start` doing argc/argv/stack/syscall). So v0
de-risks the **read→resolve→patch→emit plumbing** (~40%) but leaves v1's hard part
un-prototyped. **Alternative first target for the user to weigh:** linux-x64/arm32
static *transitionally linking crt1+libc* would de-risk hosted ELF-exec emit +
`PT_LOAD` perms + `_start` handoff (the M2-critical parts) earliest, at the cost of
not-yet-hermetic. v0 is the *greenest* target; hosted-with-libc is the most
*de-risking* one. **User picks which risk to attack first.**

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
- **`parse_elf.bn`** — decode ELF32 (+ ELF64, dormant until v1) into the input
  model (§5). Reference for exact field offsets: the test-private readback in
  `asm/elf/elf_test.bn` (`rdU16/rdU32/rdU64` :1002 + hard-coded offset walk).
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

### Step 3 — flat/ELF-exec emit + bare-metal driver (M1 → proof of life)

- **`emit_flat.bn`** — concatenate placed section data at fixed base.
- **`emit_elf.bn` (exec)** — `ET_EXEC`, `PT_LOAD` program header(s), `e_entry`,
  `e_phoff`; **BSS**: `p_memsz > p_filesz`, zero-init at load (§app-C item on BSS).
  QEMU `-kernel` loads ELF.
- **`bare_arm32.bn` driver** — merge by name, `AssignAddresses(0x40000000)`,
  resolve, patch, emit; reproduce `baremetal.ld`'s boundary symbols
  (`__bss_start/__bss_end`, `_stack_top`, `__exidx_start/__exidx_end`) and `KEEP`
  the boot section. (Placement note F6: `baremetal.ld:10-14` says `.text.startup`
  *ordering* "no longer matters" — entry is `ENTRY(_start)`→`e_entry`, not section
  order — so the `KEEP` is defensive, not a layout-rooting requirement.)
- **`cmd/bnld/main.bn`** — args via `pkg/stdx/flags` (as bnfmt/bnas/bnlint/bni now
  do): `-o`, `-target`, `-e`, `-base`; compiled-driver dispatch.
- **e2e:** assemble → `bnld` → `qemu-system-arm -M virt … -semihosting -kernel` →
  exit code; new `e2e/bnld-baremetal.sh` mirroring the existing baremetal runner
  with clang-link → `bnld`. **This is the proof-of-life milestone.**

### Step 4 — hosted ELF exec (v1 / M2, the *useful* milestone)

- ELF64 reader completion; x64 + aa64 reloc kinds (§app-A). No GOT for hermetic
  code (§1.4); `PLT32`→treat as `PC32`.
- **Multi-segment `PT_LOAD`** with correct RX/RW/R perms, `p_align` = page size,
  the `p_vaddr ≡ p_offset (mod p_align)` constraint, and BSS.
- **Hermetic `_start`** (the real long-pole): with no libc, `startup` must provide
  `_start` (stack/argc/argv setup, call `bn_entry`, exit-syscall) — the hosted
  analog of the bare-metal crt0. This belongs to the `startup`/FFI-export effort
  and is coordinated here. *Intermediate option:* v1 links `crt1.o`+libc statically
  (not hermetic) to defer `_start` — a sub-decision (ties to §3's sequencing
  question).
- `linux_x64.bn` / `linux_aarch64.bn` drivers.

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
  metadata → linker placement) — flag it as a sub-project. **For v0, hardcode the
  bare-metal placement** (matching `baremetal.ld`); generalizing to
  `#[section]`/`#[link_at]` is a named follow-up (P4).

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
- **e2e (v0):** assemble → `bnld` → QEMU semihosting → exit code; **comparison
  test**: same objects linked by clang and by `bnld`, diff *behavior* (not bytes —
  layouts differ).
- **e2e (v1):** assemble → `bnld` → run native on Linux CI (x64 + aa64).
- **Determinism (M-I):** assert byte-identical output across two runs of the same
  inputs (matters for CI caching / reproducible builds) — a property to design in,
  not retrofit.
- **Conformance:** a `…-comp_native_*-bnld` mode eventually — **build the script,
  do NOT wire it into CI here** (lane-wiring is a separate user-owned decision).

---

## 11. Milestones & effort (re-based after review)

- **M0 — `binfmt` + ELF reader + round-trip.** Medium. Unblocks everything; pays
  down BinBuf/const duplication; BUILDER-cleanliness gate (F5).
- **M1 — resolve + relocate (arm32) + flat/ELF-exec + `bare_arm32` + `cmd/bnld` +
  `os.Chmod` seam + QEMU e2e.** **Medium** (not "a few days"). Sub-item risk:
  reloc patch math (§app-A) and resolve diagnostics are correctness-critical;
  bare-metal placement reproduction is fiddly; `os.Chmod` is a cross-platform
  syscall add. div-free proof avoids D4. **Proof of life — first clang-free link.**
- **M2 — hosted ELF exec (x64+aa64) + hermetic `_start`.** **Large, and the real
  schedule sink** — but for a *revised* reason: **NOT** GOT relaxation (near-nil,
  §1.4) and **NOT** native-backend maturity (~done: hosted native modes carry ~9
  xfails and self-compile gen2). The cost is **hosted ELF-exec emission
  (multi-segment `PT_LOAD` + perms + page-align) + the hermetic `_start`/startup
  work**. The *useful* milestone.
- **M3 — archives.** Small.
- **M4 — Mach-O + ad-hoc signing.** Large. Deferred (D5).
- **M5 — interpreted drivers.** Small–Medium (entry-shape (a)).

**Headline:** M0+M1 is a bounded medium effort depending only on things that
exist. M2 is the big one — and its long-pole is **linker + startup code**, not the
backend. Anyone reading this for go/no-go should aim effort at M2's ELF-emit +
`_start`, not at "waiting for native" or "GOT relaxation" (both mostly myths).

## 12. Prerequisites & risks

- **P1: hermetic `_start`** (startup effort) — the true M2 gate (not native
  maturity). For v0, bare-metal crt0 already exists.
- **P2: `os.Chmod` seam** (§7.2) — on the critical path for any runnable output;
  small but cross-platform.
- **P3: `binfmt` BUILDER-cleanliness** (§4 Step 1 / F5) — new BUILDER-compiled
  package; test against the *pinned* BUILDER and update CLAUDE.md's surface.
- **P4: `#[section]`/`#[link_at]` mini-design** (§8) — before generalizing
  placement; not before v0 (hardcode first).
- **R1: reloc patch correctness** (§app-A) — silent-miscompile class; cross-check
  every field against the assembler's inverse encoders; test exhaustively.
- **R2: D1 coupling** (§2 D1 steelman) — native-only can't serve the *default*
  build until native-default; a general reader would, sooner. User call.
- **R3: Mach-O ad-hoc signing** (D5) — real unimplemented format burden; ELF first.
- **R4: sequencing** (§3) — v0 de-risks plumbing, not M2's ELF-emit/`_start`;
  consider a hosted-with-libc first target if attacking M2 risk earliest matters.

---

## app-A. Relocation patch reference (the correctness core)

`S` = resolved symbol vaddr, `A` = addend (from field or `r_addend` per format),
`P` = vaddr of the patched location. Bit positions are indicative — **authoritative
source is the assembler's inverse encoder** (`arm32.ResolveFixups`, x64/aa64 fixup
resolvers); the linker must match them exactly.

**ARM32 (v0; REL — addend in field):**
| Reloc | Value | Field encoding | Range check |
|---|---|---|---|
| `R_ARM_ABS32` | `S + A` | 32-bit LE word | none |
| `R_ARM_CALL`/`JUMP24` | `(S + A) − P` | imm24 = `val >> 2` → insn[23:0]; A from field incl. pipeline bias (match bnas) | ±32 MB, `val & 3 == 0` |
| `R_ARM_MOVW_ABS_NC` | `(S + A) & 0xFFFF` | imm4=val[15:12]→insn[19:16], imm12=val[11:0]→insn[11:0] | none (NC) |
| `R_ARM_MOVT_ABS` | `((S + A) >> 16) & 0xFFFF` | same split | none |

**x86-64 (v1; RELA — addend in `r_addend`, PC-rel already `−4`):**
| Reloc | Value | Field | Range |
|---|---|---|---|
| `R_X86_64_64` | `S + A` | 64-bit LE | none |
| `R_X86_64_PC32` / `PLT32` | `S + A − P` | 32-bit LE (PLT32 identical for defined S) | signed 32 |
| `R_X86_64_REX_GOTPCRELX` | GOT slot; `__c_global` only | build GOT entry `= S`, patch ref to slot (or relax `mov`→`lea`) | signed 32 |

**AArch64 (v1; RELA):**
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
