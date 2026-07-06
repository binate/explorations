# Plan: the `__c_global` C-global-variable intrinsic

Status: PLANNED (2026-07-05), reviewed. Sibling of `__c_call` (see
`plan-c-call.md`, COMPLETE). `__c_global` is DECIDED but unimplemented — spec §16.9
(`pkg.cglobal`, `docs/spec/16b-build-constraints.md`), grammar `BuiltinCall` in
`binate.ebnf` line 482 (`"__c_global" "(" string_literal "," Type ")"`), design
note in `claude-notes.md` (search `__c_global`).

> _Review disposition (3-way adversarial review, 2026-07-05)._ All three
> reviewers returned "sound-with-fixes" — architecture, two-phase staging, and the
> whole GOT crux confirmed against the code. Corrections folded in: Mach-O
> `ARM64_RELOC_GOT_LOAD_PAGEOFF12` is **pcrel=0** not pcrel=1 (§5.3); x64 uses
> `R_X86_64_REX_GOTPCRELX` and the `-4` fix goes in `elfRelocAddend` (§5.3); the
> LLVM lowering uses the module's `bitcast <TypeArg>* @sym to i8*` idiom, not an
> opaque GEP (§4.4); the §6 "BUILDER trap" was overblown — a plain enum-member add
> needs no dance (§6); BUILDER is `bnc-0.0.10`; the native `xfail` set adds
> `builder-comp_native_x64-comp_native_x64` and the spec test needs a `.rules`
> sidecar (§4.8); native `__c_call` already works (branch/PLT) — only external
> *data* addressing (GOT) is missing, phrasing sharpened (§1, §3).

`__c_global("symbol", T)` yields the **address** of the C global variable named
`symbol` — emitted **verbatim, no mangling** (exactly like `__c_call`) — as a
**raw pointer `*T`** (never `@T`: the storage is the C side's, no refcount
header). Read the global with `*p`, write it with `*p = …`. `T` must be a
C-ABI-passable **scalar or pointer** (the same constraint as `__c_call`'s
arguments). Compiled-mode only: the bytecode VM does no FFI. Canonical example —
POSIX `environ` has C type `char **` (Binate `**char`), so
`__c_global("environ", **char)` is a `***char`, and `*it` is the current
`**char`.

---

## 1. Why this is smaller than it looks (and where the hard part is)

`__c_global("sym", T)` is essentially **`&extern_global`** — the address of an
external, verbatim-named data symbol of type `T`. So it is the **fusion** of two
mechanisms the compiler already has:

1. **`__c_call`'s verbatim-unmangled-symbol emission.** `__c_call` is the sole
   path that emits a C symbol name raw (`instr.StrVal` written with no
   `mangle.*`); every other symbol routes through `bn_<pkg>__<name>`. `__c_global`
   reuses exactly this "don't mangle, this is a C name" discipline.
2. **The existing "address of a global variable" lowering.** `&someGlobal` /
   a bare global-var reference already lowers to an address materialization on
   every backend (LLVM `@sym`; native `emitGlobalAddr` → aarch64 ADRP+ADD, x64
   RIP-relative LEA, arm32 MOVW/MOVT).

The frontend, checker, IR, and LLVM layers are near-mechanical mirrors of
`__c_call`, and **simpler** (no args, no variadics, no `"void"` return, no sret).

**The one genuinely new capability is referencing an *external data* symbol.**
The existing address-of-global lowering targets **module-internal** symbols (the
compiler's own `bn_…`-mangled globals, co-located in the same object) or
**intra-image** Binate `IsExtern` globals (defined in a *sibling `.o`* linked
into the same executable — the native `emitGlobals` comment at
`native/aarch64/aarch64.bn:116-120` already notes an imported extern var "resolves
cross-object, exactly as the LLVM `external global` declaration does"). An
external **libc** data symbol like `environ` lives in a *separate shared object*
(`libc.so`), which under a **position-independent executable (PIE)** is a
different, harder relocation class. This is the crux, treated in §5.2 and §6.

**Where the two backends diverge on the hard part:**

- **LLVM backend (`--backend`-default `comp`): essentially free.** Declare
  `@sym = external global <T>` and reference `@sym` — clang synthesizes the
  correct addressing (GOT-indirect or copy-relocation, platform-appropriate) for
  us, exactly as it does for any C `extern` global. **This covers every default
  conformance mode** (`builder-comp`, `builder-comp-comp`, `builder-comp-comp-comp`
  are all LLVM-backed; `comp` = the compiler's default LLVM backend), and covers
  the native modes whenever `__c_global` is used in a *dependency* package
  (dependencies always route through LLVM; only the main module honors
  `--backend native`).
- **Native backend (`comp_native_*` modes only): the hard chunk — and only for
  external *data*.** Native `__c_call` is already fully implemented (it resolves
  the verbatim C **function** symbol via a *branch/PLT* relocation, which already
  handles undefined externals — that is why `498_c_call_basic` runs green on the
  native modes). `__c_global` needs the symbol's *data address in a register*, a
  different relocation class (GOT). The native object writers (`asm`/`elf`/`macho`)
  have **no GOT relocation kinds** at all (verified: the entire fixup-kind set is
  PC-relative-to-page + absolute; §5.1). A direct, non-GOT reference to a cross-DSO
  data symbol is **rejected by the PIE linker on macOS** (Mach-O has no copy
  relocations — external data *must* go through the GOT) and is fragile on Linux
  (works only via linker-synthesized copy relocations). Since a primary native CI
  host is **macOS/aarch64** (`comp_native_aa64`), native `__c_global` genuinely
  requires adding GOT-relocation plumbing — a well-scoped but real sub-project
  across ~6 files.

This split motivates the phasing in §3.

---

## 2. Design decisions (resolved)

- **Result type is raw `*T`, never `@T`.** The checker returns
  `MakePointerType(T)`, not `MakeManagedPtrType(T)`. The pointee is foreign
  storage with no refcount header; the IR result is never registered as a managed
  temp (no `RefInc`/`RefDec`). `__c_global("environ", **char)` → `***char`; `*it`
  → `**char`.
- **`T` is mandatory and C-ABI-passable.** Reuse `__c_call`'s exact predicate
  `isCCompatibleArgType` (scalar/float/bool or raw `*T`; rejects `@T`, slices,
  structs, func/interface values). Unlike `__c_call`'s return position, there is
  **no `"void"` spelling** — you cannot take the address of a `void` global. The
  grammar makes `Type` mandatory; the checker keeps a defensive nil-`TypeRef`
  guard anyway.
- **New dedicated IR opcode `OP_C_GLOBAL`** (not a flag on an existing
  global-address op). Rationale, mirroring why `__c_call` got its own `OP_C_CALL`:
  a distinct opcode (a) cleanly encodes "external, verbatim, *don't-mangle* data
  symbol" vs. the existing global-address pseudo's "my module's own *mangled*
  global — and emit storage for it," which are semantically opposite (the C
  global must **not** get a storage definition); (b) avoids threading a
  "don't-mangle / is-external" flag through the ~8 backend sites that assume the
  `IsGlobalRef` pseudo means "mangle + I own this"; (c) gives each backend one
  obvious dispatch point for the external-data lowering (LLVM `external global`
  declaration; native GOT load). `OP_C_GLOBAL` produces a **real SSA result**
  (unlike the `&G` pseudo, which is ID −1 rematerialized per use), so the pointer
  flows normally into casts/derefs/stores.
- **`OP_C_GLOBAL` carriers reuse existing `ir.Instr` fields** — no new field:
  `StrVal` = verbatim C symbol; `Typ` = `*T` (the result type, via
  `MakePointerType(T)`); `TypeArg` = `T` (the pointee, so backends can emit the
  `external global <T>` declaration / size the reference). This is exactly the
  `Typ = *T` / `TypeArg = T` convention the existing global-address pseudo already
  uses (`ir/gen_func.bn:290-291`).
- **Verbatim-symbol invariant is load-bearing.** `EmitCGlobal` sets `StrVal`
  directly (like `EmitCCall`), never through `qualifyForPkgPath`/`mangle.*`. If a
  backend ever mangled it, `environ` would become `bn_…environ…` and fail to
  link.
- **Compiled-mode only; fail-loud everywhere else.** The checker rejects it under
  `Checker.Interpreted` (the load-bearing gate — every interp/REPL host sets that
  flag, and injected native packages are loaded interface-only so their `.bn` is
  never lowered). If `OP_C_GLOBAL` nevertheless reached VM lowering, the existing
  `vm/lower_instr.bn` default arm `vmPanicName`s on it for free — **do not** add a
  NOP arm. In the native backend, an unimplemented arch (arm32, and x64/aarch64
  before Phase 2) must `SetError`/abort, never silently emit a wrong reloc.

---

## 3. Phasing

The LLVM/frontend work delivers a **fully working, tested feature** on the LLVM
backend (all default modes, and native modes via dependency packages). The native
GOT work extends it to the natively-compiled *main* module. These are separable
because clang is the correctness oracle: Phase 1's LLVM output is exactly what
Phase 2's native output must match at runtime.

**Phase 1 — frontend + checker + IR + LLVM backend (+ tests).** Ships
`__c_global` end-to-end through the LLVM path. The `environ` conformance test runs
live on the default LLVM modes and is `xfail`'d on the `-int`-terminal modes (VM,
no FFI), on `builder-comp_arm32_baremetal` (no libc), **and** on the native
modes `builder-comp_native_aa64-comp_native_aa64`,
`builder-comp_native_x64-comp_native_x64`,
`builder-comp_native_x64_darwin-comp_native_x64_darwin` (native external-data/GOT
lowering not implemented yet — the §4.7 fail-loud stub). Phase 2 removes the three
native-mode `xfail`s (the baremetal one stays — no libc). The exact live-vs-xfail
set is enumerated in §4.8.

**Phase 2 — native GOT-relocation sub-project + native `OP_C_GLOBAL` lowering.**
Adds GOT fixup kinds to `asm/{x64,aarch64}` and the matching ELF + Mach-O
relocation types, then the per-arch `OP_C_GLOBAL` emitter (GOT-indirect load of
the symbol's address). Un-`xfail`s the native modes. **arm32-native is proposed
as fail-loud indefinitely** (baremetal-only target, no libc/`environ`); this is a
scope call for the user (§7), not a unilateral non-goal.

> **User decisions to confirm before Phase 2 (see §7):** (a) is native
> `OP_C_GLOBAL` in scope now, or is Phase 1 (LLVM + fail-loud native) the
> deliverable for now? (b) arm32-native fail-loud acceptable? (c) keep the
> default-PIE link (→ GOT is the right answer) vs. switching bnc's link to
> `-no-pie`/`-static` (a global link-model change that would let absolute relocs
> reach external data with no new GOT kinds — but affects *all* output and does
> not help macOS). The recommendation is **keep PIE + add GOT**.

---

## 4. Phase 1 — precise edit sites

### 4.1 Frontend: token + parser (`pkg/binate/token`, `pkg/binate/parser`)

- **`pkg/binate/token.bni`** — add `C_GLOBAL` to the builtin-token block,
  **at the end of the `builtin_start…builtin_end` range** (after `C_CALL`, before
  `builtin_end`; in-range placement is all `IsBuiltin`/`Lookup` need — both are
  range/scan-based, there is no keyword map). Doc-comment mirroring `C_CALL`.
- **`pkg/binate/token/token.bn`** — in `TypeName`, after
  `case C_CALL: return "__c_call"`, add `case C_GLOBAL: return "__c_global"`.
  This alone makes `Lookup("__c_global") → C_GLOBAL` and `IsBuiltin(C_GLOBAL)`.
- **`pkg/binate/parser/parse_builtin.bn`** — add `parseCGlobal(p @Parser)
  @ast.Expr`, modeled on `parseCCall` (lines 97-155) but **stripped**: parse
  `__c_global` `(` string-literal `,` `Type` `)`. **No** return-spec branch (no
  `"void"`), **no** arg loop, **no** `...`/`CFixedArgs`. Reuse `stripStringQuotes`
  (line 169). `parseType(p)` runs unconditionally on the second argument. Build
  `EXPR_BUILTIN` with `Op = cast(int, token.C_GLOBAL)`, `Name = sym`,
  `TypeRef = typ`; leave `Args` nil and `CFixedArgs` 0.
- **`pkg/binate/parser/parse_primary.bn`** — after the `C_CALL` dispatch block
  (lines 56-58) add `if p.tok.Typ == token.C_GLOBAL { return parseCGlobal(p) }`.
- **AST: no change.** `EXPR_BUILTIN`'s existing `Op`/`Name`/`TypeRef` fit exactly.
  Do **not** add a field or a new `EXPR_` kind.

### 4.2 Checker (`pkg/binate/types`)

- **`pkg/binate/types/check_builtin.bn`** —
  - Dispatch: next to the `C_CALL` arm (lines 329-331) add
    `if e.Op == cast(int, token.C_GLOBAL) { return checkCGlobal(c, e) }`.
  - New `checkCGlobal(c @Checker, e @ast.Expr) @Type` (place after `checkCCall`,
    before `isCCompatibleArgType`):
    1. `if c.Interpreted { addCheckError(c, e.Pos, "__c_global cannot be
       interpreted (native-only FFI); it is usable only in compiled code") }`
       (no early return — keep surfacing diagnostics, mirroring `checkCCall`).
    2. Defensive nil-`TypeRef` guard → `addCheckError(… "__c_global requires a
       type argument")`, return `TypVoid()` (error-recovery placeholder, matching
       `checkCCall`'s error paths). This path is unreachable from a well-formed
       parse — the grammar makes `Type` mandatory — so it fires only on malformed
       AST after an error is already emitted.
    3. `var t = resolveTypeExpr(c, e.TypeRef)`.
    4. `if !isCCompatibleArgType(t) { addCheckError(… "__c_global type must be a
       scalar or pointer (managed pointers, slices, and structs are not C-ABI
       globals)") }` — **message must not mention `"void"`** (a `__c_call`
       copy-paste bug).
    5. `return MakePointerType(t)` — raw `*T`, **not** `MakeManagedPtrType`.
  - `isCCompatibleArgType` is reused unchanged.

### 4.3 IR + opcodes (`pkg/binate/iropcode`, `pkg/binate/ir`)

- **`pkg/binate/iropcode.bni`** — add `OP_C_GLOBAL` near `OP_C_CALL`, documented:
  `StrVal` = verbatim C data symbol (not mangled); `Typ` = `*T`; `TypeArg` = `T`;
  real SSA raw-pointer result; no `Args`; compiled-mode only.
- **`pkg/binate/iropcode/opcodes.bn`** — add `case OP_C_GLOBAL: return "c_global"`
  to `OpName` (next to line 56), plus its `opcodes_test.bn` assertion.
- **`pkg/binate/ir/ir_ops_flow.bn`** — add
  `func (b @Block) EmitCGlobal(symbol @[]char, pointeeTyp @types.Type) @Instr`
  after `EmitCCall`: `ptrTyp = types.MakePointerType(pointeeTyp)`;
  `instr = newInstr(b.Func, iropcode.OP_C_GLOBAL, ptrTyp)`;
  `instr.StrVal = symbol`; `instr.TypeArg = pointeeTyp`; `addInstr`; return. **No**
  `qualifyForPkgPath`/mangle on `StrVal`; **no** `registerTemp` (raw pointer).
- **`pkg/binate/ir/gen_expr.bn`** — add a `token.C_GLOBAL` arm beside the `C_CALL`
  arm (454-472): `pointeeTyp = resolveTypeExpr(ctx.Gc, e.TypeRef)`;
  `return b.EmitCGlobal(e.Name, pointeeTyp)`. **No** arg-lowering loop, **no**
  `registerTemp`/`noteSPGrowingResult`.
- **`pkg/binate/ir.bni`** — no struct-field change (`StrVal`/`Typ`/`TypeArg`
  already exist). Optional one-line note that `OP_C_GLOBAL` reuses them.

### 4.4 LLVM codegen (`pkg/binate/codegen`)

- **`pkg/binate/codegen/emit_cglobal.bn` (new)** — `emitCGlobalDeclares(out, m)`,
  mirroring `emitCCallDeclares` (`emit_ccall.bn:98-115`): walk non-extern funcs'
  instrs; for each `OP_C_GLOBAL`, dedup on `StrVal` (reuse `cCallSymbolSeen`/
  `cCallStrEq` — they are symbol-name helpers, consider renaming to
  symbol-neutral) and emit **once** `@<StrVal> = external global
  <llvmType(TypeArg)>`. This exactly parallels the module's own imported-extern-var
  decl (`emit.bn:151-157`), differing only in the verbatim (unmangled) name.
- **`pkg/binate/codegen/emit_instr.bn`** — add `case iropcode.OP_C_GLOBAL:` beside
  the `OP_C_CALL` case (line 244). Emit the SSA result with the module's **own
  documented global-address-to-`i8*` idiom**:
  `%vID = bitcast <llvmType(TypeArg)>* @<StrVal> to i8*`. The declared
  `@<StrVal> = external global <llvmType(TypeArg)>` gives `@<StrVal>` LLVM type
  `<llvmType(TypeArg)>*`, and `llvmType(*T)` collapses to `i8*` — so the pointee
  type (`TypeArg`) **must be spelled explicitly** in the bitcast, exactly as
  `emit_instr.bn:396-413` does for the existing `&global` case (its comment:
  "`llvmType` collapses a raw pointer to `i8*`, which would mismatch the global
  symbol's real pointer type — so spell the pointee type (`TypeArg`) explicitly").
  Do **not** use the opaque-`ptr` GEP spelling; the module emits typed pointers
  pervasively. A codegen unit test should assert the exact bitcast text. Keeping
  it a real `%vID` instruction (rather than reusing the `&G` inline-`@sym`
  value-operand path) sidesteps the `emitPtrRef` mangling branch entirely.
- **`pkg/binate/codegen/emit.bn`** — call `emitCGlobalDeclares(out, m)` in the
  module preamble immediately after `emitCCallDeclares(out, m)` (line 249), before
  the `out.WriteByte('\n')` at 251.

### 4.5 Formatter (`pkg/binate/format`)

- **`pkg/binate/format/print_builtin.bn`** — the generic `printBuiltin` path drops
  `e.Name`, so a `C_GLOBAL` node would round-trip as `__c_global(**char)` (symbol
  lost). Add `if op == token.C_GLOBAL { printCGlobal(b, e); return }` beside the
  `C_CALL` guard (21-24), plus `printCGlobal` (simpler than `printCCall`: no args,
  no variadic, no void): emit `__c_global("` + `e.Name` + `", ` +
  `printType(e.TypeRef)` + `)`. Update the doc comment (11-12).

### 4.6 VM / interp (`pkg/binate/vm`) — comment-only

- **`pkg/binate/vm/lower.bn`** — extend `LowerModule`'s doc comment (162-167) to
  name `OP_C_GLOBAL` under the same two-part invariant as `OP_C_CALL`. No code
  change; the `lower_instr.bn` default arm already fail-louds. Do **not** add a
  NOP arm.

### 4.7 Native — Phase 1 stub (fail-loud)

- **`pkg/binate/native/{aarch64,x64,arm32}/*_dispatch.bn`** — add an
  `OP_C_GLOBAL` arm that `SetError`s ("native __c_global not yet implemented —
  needs GOT relocation support; see plan-c-global.md Phase 2") beside the
  `OP_C_CALL` arm. This makes the native modes fail cleanly (justifying the
  Phase 1 native-mode `xfail`s) instead of silently miscompiling.

### 4.8 Tests (Phase 1)

- **Unit** (mirror the `__c_call` unit-test sites, section-for-section):
  - `parser/parse_builtin_test.bn`: `TestParseCGlobal`,
    `TestParseCGlobalNonStringSymbolRejected`, `TestParseCGlobalRequiresType`.
  - `types/check_builtin_test.bn`: `TestCheckCGlobalBasic` (`*T`),
    `TestCheckCGlobalEnvironShape` (`**char` → `***char`),
    `TestCheckCGlobalInterpretedRejected`, `TestCheckCGlobalRejectsNonCType`
    (`@T`/slice/struct), `TestCheckCGlobalRequiresType`.
  - `iropcode/opcodes_test.bn`: `OpName(OP_C_GLOBAL) == "c_global"`.
  - `codegen/emit_cglobal_test.bn` (new): emits `@sym = external global …`
    verbatim (no `bn_` mangle), deduped by symbol; the `%vID` materialization.
  - `format/print_builtin_test.bn`: `TestPrintCGlobal`.
- **Conformance** (compiled-only; linking is zero-config — bnc's final link is a
  plain `clang -o … objs` that implicitly links libc, which is exactly why every
  `__c_call` test uses a libc symbol and nothing else; no companion-`.c` /
  extra-link path exists and none should be added). The live modes below are all
  **Linux-glibc** (the CI matrix), where `environ` binds via clang's
  `external global` — the load-bearing evidence for Phase 1 is the Linux-glibc
  LLVM path, not a darwin one-off.
  - `NNN_c_global_environ.bn` (+`.expected` = `ok`): `var pp ***char =
    __c_global("environ", **char); var env **char = *pp;` count non-null entries,
    print `ok` if > 0 else `FAIL` (assert-internal pattern → width-stable
    `.expected`). Exercises verbatim external symbol + address-of-global + double
    deref end-to-end.
    - **Runs live** (all LLVM-backed): `builder-comp`, `builder-comp-comp`,
      `builder-comp-comp-comp`, `builder-comp_arm32_linux`.
    - **`xfail` — VM (no FFI):** `builder-comp-int`, `builder-comp-int-int`,
      `builder-comp-comp-int`.
    - **`xfail` — no libc (permanent):** `builder-comp_arm32_baremetal` and, if
      run, `builder-comp_native_arm32_baremetal` (baremetal has no `environ` at
      all — stays `xfail` even after Phase 2).
    - **`xfail` — Phase 1 native (data/GOT unimplemented → §4.7 fail-loud;
      removed in Phase 2):** `builder-comp_native_aa64-comp_native_aa64`,
      `builder-comp_native_x64-comp_native_x64`,
      `builder-comp_native_x64_darwin-comp_native_x64_darwin`. **NB:** contrast
      `498_c_call_basic`, which has **no** native `xfail` (native `__c_call` works
      via branch/PLT); the native-mode markers here are new *because* native
      external-data/GOT is the unimplemented gap. Cross-check the exact set of
      native modes the live blocking modeset runs at implementation time.
  - `NNN_c_global_interp_error.bn` (+`.error`): asserts the checker rejects
    `__c_global` in interpreted mode; runs live on the interpreted (`-int`) modes,
    `xfail`'d on every compiled/native mode. **Copy `961_c_call_interp_error`'s
    exact 8-marker set** verbatim (`builder-comp`, `builder-comp-comp`,
    `builder-comp-comp-comp`, `builder-comp_arm32_baremetal`,
    `builder-comp_arm32_linux`, and the three `builder-comp_native_*` modes) — it
    differs from the environ test's set.
  - Spec-tree mirror `conformance/spec/16-packages/NNN_cglobal_basic.bn` (§16.9),
    **with a `NNN_cglobal_basic.rules` sidecar** citing the §16.9 `pkg.cglobal`
    rule ID(s) — an untagged spec test is a `spec-coverage` hygiene **error**
    (mirror `092_ccall_basic.rules` / `093_ccall_no_mangle.rules`). Same `xfail`
    set as the environ test.
  - Pick numbers with `conformance/next-number.sh`; `conformance-test-numbers`
    hygiene enforces uniqueness (re-run after any landing rebase).

---

## 5. Phase 2 — native GOT-relocation support

### 5.1 The gap (verified)

The complete native fixup-kind set carries **no GOT kind**:
`aarch64.bni` `FIX_BRANCH{26,19,14}`, `FIX_ADR_LO21`, `FIX_ADRP_HI21`,
`FIX_ADD_LO12`, `FIX_LDR_LO12`; `x64.bni` `FIX_REL32`, `FIX_ABS64`,
`FIX_REL32_LEA`; `arm32.bni` `FIX_BRANCH24`, `FIX_ABS32`, `FIX_MOVW_ABS_NC`,
`FIX_MOVT_ABS`. The ELF (`elf_const.bn`/`elf_util.bn`) and Mach-O
(`macho_const.bn`/`macho_reloc.bn`) writers likewise define no GOT relocation
types. So the native address-of-global path (`emitGlobalAddr`) can only emit
PC-relative-to-page (ADRP+ADD / RIP-LEA) or absolute (MOVW/MOVT) references —
correct for internal / intra-image symbols, wrong for a cross-DSO external data
symbol under PIE.

### 5.2 Why GOT (not the existing relocs)

bnc links a **default PIE** (`cmd/bnc/main.bn:235-258` runs plain
`clang -o out objs…` with no `-no-pie`/`-static`; clang defaults to `-pie`, and
macOS is PIE-only). For a preemptible external data symbol in another DSO:

- **macOS/aarch64 (`comp_native_aa64`, the primary native host): GOT is
  mandatory.** Mach-O has **no copy relocations**; external data is reached only
  via `_sym@GOTPAGE` + `_sym@GOTPAGEOFF`. A direct PAGE21/PAGEOFF12 to an
  undefined external symbol is an illegal text-relocation / link error.
- **Linux (x86-64, aarch64): GOT is the portable answer.** A direct
  PC-relative reference *can* link via a linker-synthesized **copy relocation**,
  but that is fragile (aarch64 toolchains often reject `ADR_PREL` against a
  preemptible symbol; copy relocations are a legacy mechanism) and does not help
  macOS. GOT-indirect is what clang itself emits for `-fPIE` external-data access.

(Absolute relocs — `R_X86_64_64`, `R_AARCH64_ABS64` — *exist* in the writer but
do **not** rescue this: an absolute reloc against external data in a PIE is a text
relocation the linker rejects. The only way absolute works is a global
`-no-pie`/`-static` link-model switch, which is a §7 user decision, not a local
workaround.)

### 5.3 Edit sites (Phase 2)

- **asm fixup kinds + emitters:**
  - `asm/aarch64.bni` + `asm/aarch64/aarch64_branch.bn`: `FIX_ADRP_GOT_HI21` +
    `FIX_LD_GOT_LO12`; `AdrpGot` (ADRP with the GOT-page fixup) + `LdrGotLo12`
    (LDR imm12 with the GOT-lo12 fixup).
  - `asm/x64.bni` + `asm/x64/x64_data.bn`: `FIX_GOTPCREL`; `MovGotPcRel` (0x8B
    RIP-relative load, `mov r64, [rip + sym@GOTPCREL]`).
- **ELF writer:** `asm/elf/elf_const.bn` add `R_X86_64_GOTPCREL`(9) /
  `R_X86_64_REX_GOTPCRELX`(42), `R_AARCH64_ADR_GOT_PAGE`(311),
  `R_AARCH64_LD64_GOT_LO12_NC`(312). The x64 emitter emits
  **`R_X86_64_REX_GOTPCRELX`(42)** for the REX.W `mov r64, [rip+…]` (the
  linker-relaxable form; plain `9` also works but forgoes relaxation). `elf_util.bn`
  needs arms in **two** functions: `elfRelocType` (map the new fixup kinds) **and**
  `elfRelocAddend` — the `-4` PC-relative addend correction currently fires only
  for `R_X86_64_PC32`/`PLT32` (`elf_util.bn:266-276`); the new GOTPCREL type must
  be **added to that `-4` condition** or the reloc lands 4 bytes off. Mark the
  symbol undefined (`Section < 0`) so it is an external reference.
- **Mach-O writer:** `asm/macho/macho_const.bn` add `X86_64_RELOC_GOT_LOAD`(3),
  `ARM64_RELOC_GOT_LOAD_PAGE21`(5), `ARM64_RELOC_GOT_LOAD_PAGEOFF12`(6);
  `asm/macho/macho_reloc.bn` add the `machoRelocType`/`Length`/`PCRel` arms.
  **PC-rel is NOT uniform:** the ADRP-side `ARM64_RELOC_GOT_LOAD_PAGE21` and the
  x64 `X86_64_RELOC_GOT_LOAD` are **pcrel=1**, but the LDR-side
  `ARM64_RELOC_GOT_LOAD_PAGEOFF12` is **pcrel=0** — it patches a within-instruction
  12-bit immediate, matching the existing non-GOT `ARM64_RELOC_PAGEOFF12`, which
  `machoRelocPCRel` (`macho_reloc.bn:50-62`) already returns 0 for. Emitting
  pcrel=1 on the PAGEOFF12 produces a malformed reloc ld64 rejects. Add a native
  unit assertion on the pcrel bit of each of the three GOT relocs.
- **Native `OP_C_GLOBAL` emitters** (parallel to `emitGlobalAddr`, but GOT +
  external + verbatim `symPrefixed` name, into `nextReg(rm, ins.ID)` as a
  scalar-return SSA result — no arg marshal, no call, spill like `OP_LOAD` not
  like a call):
  - `native/aarch64/{aarch64_dispatch.bn,aarch64_names.bn}`: `ADRP rd, :got:_sym`
    + `LDR rd, [rd, :got_lo12:_sym]`.
  - `native/x64/{x64_dispatch.bn,x64_emit.bn}`: `mov rd, [rip + _sym@GOTPCREL]`.
  - `native/arm32/arm32_dispatch.bn`: keep fail-loud (§7).
- **Un-`xfail`** the native modes on the `environ` conformance test.

### 5.4 Correctness oracle & the indirection footgun

The native GOT lowering must produce **byte-for-byte the same runtime behavior as
the LLVM `external global` reference** (Phase 1 is the oracle). The signature bug
to guard against: a GOT slot holds the symbol's *address*; the lowering must load
**the slot's contents** (the address) into the result register, i.e. one level of
indirection through the GOT — getting this wrong yields the address *of the GOT
slot* instead of `&environ` (an off-by-one-indirection, data-corruption class).
Cover with a native unit test **and** the live conformance run on
`builder-comp_native_aa64-comp_native_aa64`,
`builder-comp_native_x64-comp_native_x64`, and
`builder-comp_native_x64_darwin-comp_native_x64_darwin`; unit assertions on
`macho_reloc` pcrel/length fields are easy to get subtly wrong, so an on-target
link+run is required, not just byte-pattern assertions. (Darwin caveat: direct
`_environ` access via the GOT is valid for a **main executable** — which is what
bnc links; the `_NSGetEnviron()` requirement applies only to dylibs/bundles, so it
does not bite here.)

---

## 6. BUILDER cadence

Every touched package except the `format`/`vm`/native-test files is in `cmd/bnc`'s
BUILDER-compiled tree (`token`, `parser`, `types`, `ir`, `iropcode`, `codegen`,
`asm`, `native`). All Phase 1 code uses only constructs already present in the
`__c_call` implementation (plain funcs, `if`/`for`, `cast`; no interfaces,
generics, closures, floats) → stays within the current BUILDER's subset
(`BUILDER_VERSION` = `bnc-0.0.10`).

**No special BUILDER dance is needed** — and it is worth being precise about why,
since the CLAUDE.md build-constraint rule is easy to over-apply here. The BUILDER
`bnc` resolves `token.C_GLOBAL` by **name-resolving it against the `token`
package's `.bni` in the tree it is compiling**, not against a baked-in enum in its
own binary. `token.C_GLOBAL` is a plain `iota` enum member, syntactically
identical to `token.C_CALL`, which is **already** consumed via
`cast(int, token.C_CALL)` across the bnc tree (`check_builtin.bn:329`,
`gen_expr.bn:454`, `parse_builtin.bn:149`) and compiles green under the current
BUILDER today. The genuine BUILDER-lag hazard (per CLAUDE.md) is a new *syntax /
spelling* or *renamed exported symbol* the BUILDER's own **frontend** can't parse
or resolve (e.g. `#[build(...)]`, the `_func_handle` lexer-spelling lag) — **not**
an ordinary new enum constant referenced from compiled code. The compiler source
never contains a literal `__c_global("…", …)` *call* (only the tests /
conformance do, and those are outside the BUILDER-compiled tree), so the BUILDER's
lexer never has to recognize the `__c_global` keyword string either. A cheap
`scripts/build-bnc.sh` gen1 smoke after the token+parser commit is sufficient
confirmation; this is a sanity check, not a landing-gating risk.

Land in small, independently-green commits (per the stay-close-to-main cadence):
e.g. (1) token + parser + AST-less dispatch + parser tests; (2) checker + tests;
(3) IR opcode + emitter + gen_expr + tests; (4) LLVM codegen + tests; (5) native
fail-loud stub; (6) formatter; (7) conformance tests + `xfail`s + doc. Each stays
compilable and passes its package's unit tests.

---

## 7. Scope & related (user decisions)

Surfaced explicitly rather than pre-decided (per CLAUDE.md "don't unilaterally
defer scope"):

- **Native Phase 2 now, or later?** Phase 1 (LLVM + fail-loud native) is a
  complete, tested feature on all default modes. Native support is a real
  ~6-file GOT sub-project. Recommendation: land Phase 1, then do Phase 2 as a
  distinct tracked project — **but this is the user's call**, and Phase 1's
  native-mode `xfail`s must not be presented as "done."
- **arm32-native fail-loud indefinitely?** Native arm32 is baremetal-only (no
  libc, no `environ`); arm32-Linux runs on the LLVM path (works). Proposal:
  `SetError` on native-arm32 `__c_global`. Needs ratification, not a silent
  non-goal.
- **PIE vs `-no-pie`/`-static`.** Keeping PIE (recommended) makes GOT the correct
  mechanism. Switching the link model would let absolute relocs reach external
  data with no new GOT kinds, but changes *all* output and does not help macOS —
  a global decision the user owns.
- **Write-through test.** The spec allows `*p = …`; `environ` is read-mostly and
  portable writable libc scalars are scarce. Whether to add a write test (and
  against which symbol) is a scope call, not to be dropped silently.
- **`pkg/c` C-type aliases** stay **not built** (the `__c_call` decision):
  `__c_global` call sites open-code `int32`/`*uint8`/`**char` directly.
- **In-tree adoption** (e.g. a `pkg/std/os` `Environ`/`Getenv` on top of
  `__c_global`) is a **separate** follow-on, not bundled here — same as
  `plan-c-call.md` scoped `pkg/bootstrap` retirement separately.
- **Symbol-collision edge:** if one program used the same name in both `__c_call`
  and `__c_global`, the two LLVM declare-sweeps would emit conflicting
  `declare @sym(...)` + `@sym = external global …` (ill-formed). Rare (a symbol is
  either a function or a datum); optionally a checker-side reject. Noted, low
  priority.
- **FFI end-state:** long-term, `__c_call` + `__c_global` may be subsumed by
  annotation-based `extern` FFI declarations (the stated direction).
