# Plan: implement `__c_entry(f) -> *uint8` (`plan-c-entry-impl`)

Implements the ratified spec rules `pkg.centry` / `pkg.centry.eligible` /
`pkg.centry.identity` + the shared `pkg.cexport.semantics` (docs `a03d4b2`, §16.9).
Design + implementer notes: `proposal-c-entry-builtin.md` (§3 builtin, §4 thunk).
Supersedes the c_export callback-gate scope-limitation.

`__c_entry(f)` yields the address of a **C entry** of the declared function `f`, as
an opaque raw `*uint8`, suitable for handing to C as a callback. Third FFI primitive
alongside `__c_call` / `__c_global`. Compiled-mode only (VM does no FFI).

## No BUILDER dependency

bnc *implements* `__c_entry`; it does not *use* it. Adding a new builtin token +
checker/codegen arms is ordinary new code the current BUILDER compiles fine (unlike
os.MkdirTemp, which cmd/bnc had to *call*). So this lands without a BUILDER cut.

## Eligibility (checker — `pkg.centry.eligible`)

Operand must be a reference to a **declared, non-generic, top-level function** — a
local `Ident`→SYM_FUNC or a package-qualified selector→SYM_FUNC; public OR
package-private. Reject: a method, a function *value*, a function literal, a generic
function (no context slot in a C pointer). `f`'s signature must satisfy
`pkg.cexport.signature` (C-ABI-replicable — the export direction rejects nothing at
the ABI level, so this is effectively always satisfied). Result type `*uint8`.
Compiled-mode only: reject in an interpreted context (Checker.Interpreted), like
`checkCCall`/`checkCGlobal`. Closest analogs: `check_builtin.bn`'s `_func_handle`
arm (operand shape) + `checkCGlobal` (compiled-only + raw-pointer result).

## Lowering

- **LLVM backend** — the degenerate case is "always" here (proposal §4): a
  Binate function's mangled entry is already C-ABI-callable (LLVM's CC lowering
  handles narrow-arg dirty bits), so `__c_entry(f)` lowers to a direct reference to
  `f`'s mangled function symbol, cast to `*uint8`. No thunk.
- **Native backends (aa64/x64/arm32)** — the mangled entry assumes narrow args
  arrive already sign/zero-extended (the native invariant), so a C caller with dirty
  high bits needs the C-entry adaptation. Reuse the c_export thunk machinery
  (`emitCExportRegNorm{,X64,Arm32}` + branch-to-mangled, NOT fall-through — ld64
  atom lesson). Emission is **use-site collection, every copy weak**
  (IsLinkOnce/SetWeak; ld64 N_WEAK_DEF / ELF COMDAT / bnld strong-over-weak → one
  survivor program-wide = `pkg.centry.identity`). When the adaptation is empty:
  degenerate to a direct reference/relocation to the mangled entry (a use-site
  *alias* to a cross-TU symbol is not expressible — must be a reference).

## Increments (land each independently green)

- **Inc A — front-end + LLVM (steps 1-4). LANDED 2026-09-02** — front-end + codegen
  guard (3defdc02d), then the LLVM codegen (f78e4d3eb): OP_C_ENTRY + EmitCEntry + the
  getelementptr-0 emission (f's mangled entry as i8*, no thunk); FFI arms split into
  genFFIBuiltin; unit tests (ir + codegen) + manual qsort round-trip; adversarial review
  SAFE-TO-LAND (imported-fn case verified NOT to dangle — imports declare eagerly). The
  end-to-end CONFORMANCE TEST (step 5) is deferred to Inc B: once native works it passes on
  native + LLVM and only the compiled-only VM/int modes need xfail (the clean 498 pattern),
  avoiding ~5 fragile native xfails that would just be deleted again. Reviewer note (m2): add
  an expose-forwarder + __c_entry conformance case when that interaction is in scope.
  1. `token.bn`: add `C_ENTRY` builtin token (`__c_entry`) in the builtin range +
     keywordMap; `TokenName`.
  2. `parser/parse_builtin.bn`: `parseCEntry` — `__c_entry ( Expr )`, one operand
     (same shape as `_func_handle`); AST node.
  3. `types/check_builtin.bn`: `checkCEntry` — eligibility above; result `*uint8`;
     compiled-only. Checker unit tests for every rejection (method / value /
     literal / generic / non-func / VM-mode) + the accept case.
  4. IR + LLVM codegen: lower to a `*uint8` reference to `f`'s mangled symbol.
     (Model the op on how `_func_handle` / a function address is referenced.)
  5. Conformance test: a C `__c_call` through an `__c_entry(f)` pointer calls `f`
     (e.g. hand a comparator to `qsort`, or a trivial C shim). xfail on the native
     modes (`.xfail.builder-comp_native_*`) until Inc B.
- **Inc B — native codegen (aa64/x64/arm32).** Use-site-weak C-entry thunk +
  degenerate mangled-ref; then remove Inc A's native xfails. Authoritative test =
  the linked self-compile (native conformance modes), NOT a `.o` disasm.
- **Inc C (related, small) — `_func_handle` generic guard.** check_builtin.bn's
  `_func_handle` arm has no TypeParams guard → `_func_handle(GenericF)` type-checks,
  codegen skips the triple, the `@__handle` ref dangles at link. Add the same
  non-generic guard `pkg.centry.eligible` needs + a unit test. (Separate todo entry;
  fold in if convenient since it's the same guard.)

## Out of scope (proposal §6)

Calling a raw C function pointer *from* Binate; per-value/closure thunks; widening
the always-on entry normalization; VM-mode FFI; cross-thread/async-signal invocation.

## Inc B — native codegen (execution-ready design, 2026-09-02)

Split into two safe sub-increments; native currently fails LOUD on OP_C_ENTRY (each
arch dispatch's `SetError "unimplemented IR op c_entry"` default), so no silent
miscompile exists today.

**Inc B1 LANDED (2026-09-02, commit 5d24fbe49)** exactly as designed below: the
degenerate address reference on all three backends (aa64 ADRP+ADD, x64 RIP-LEA,
arm32 MOVW/MOVT via emitCEntryAddr / emitSymAddr) + the common.EmitObject
narrow-arg fail-loud gate (CheckCEntryNarrowArgs) using each target's word size +
ir.FuncParamTypesByName + conformance 1235_c_entry_qsort (verified LLVM / native
aa64 / native x64; xfail VM/int + baremetal). Adversarial review clean; the gate
is intentionally conservative (flags narrow GP scalars by type, so it also
over-rejects the safe stack-passed-narrow-arg case — B2 removes the gate). Inc B2
remains open.

- **Inc B1 — degenerate reference + narrow-arg fail-loud gate.** For a callback
  whose args are all pointer/word-size/float/aggregate (the common case: qsort /
  bsearch / signal comparators), f's mangled entry IS a correct C entry on native,
  so OP_C_ENTRY lowers to the ADDRESS of @<mangled f> (no thunk). Only *narrow GP
  integer args* (int8/int16, and int32 on the 64-bit backends) need the c_export-
  style normalization — defer those to B2 and FAIL LOUD for now (never silently
  reference the mangled entry for a narrow-arg fn — that would reintroduce the
  dirty-upper-bits bug).
  - Gate (common, has `mod`): a pre-pass in `common.EmitObject` (or per-EmitFunc)
    — for each OP_C_ENTRY, `findFuncNamed(mod, ins.StrVal)` (ir/data_satregistry.bn)
    → if any param peels to an INTEGER/bool scalar (NOT pointer/float/aggregate;
    IsFloatScalarTyp/IsAggregateTyp excluded) with SizeOf < 8 → ReportEmitError
    ("native __c_entry: adaptation thunk for narrow-argument callbacks not yet
    implemented; use only pointer/word-size args, or the LLVM backend"). Conservative
    cross-arch (over-conservative for int32 on arm32 — safe). VERIFY the StrVal↔f.Name
    match empirically (same qualification the func-value path relies on).
  - Per-arch degenerate materialization (emitInstr OP_C_ENTRY case): the mangled
    function symbol's address into the result reg — aa64 `SetGlobal(symFor(pkg,StrVal))`
    + Adrp + AddImmLabel (mirror OP_DATA_SYM_ADDR, but symFor not symPrefixed, since
    StrVal is the func NAME not a mangled data sym); x64 LEA rip-relative; arm32
    MOVW/MOVT (or the arch's local-symbol-address idiom). Binate statically links all
    packages into one binary, so an imported fn is a LOCAL symbol (PC-relative, NOT
    GOT-indirect like OP_C_GLOBAL).
  - Then the conformance test (below) passes on native + LLVM; xfail ONLY the
    compiled-only VM/int modes (builder-comp-int / -comp-int / -int-int /
    _arm32_linux_int) — the clean 498 set, no native/baremetal churn.
- **Inc B2 — narrow-arg adaptation thunk.** Emit a use-site, WEAK
  `__centry.<mangled>` thunk (emitCExportRegNorm{,X64,Arm32} normalization → branch
  to <mangled>, NOT fall-through — ld64 atom lesson), collected like the func-value
  shims (one weak survivor program-wide = pkg.centry.identity); OP_C_ENTRY then
  references the thunk for narrow-arg fns and the mangled entry otherwise. Drop the
  B1 gate. Authoritative test = the linked native self-compile.

## Conformance test (lands with Inc B1)

`<N>_c_entry_qsort.bn`: a Binate `int(*)(const void*, const void*)` comparator handed
to libc `qsort` via `__c_entry`, sorting an int32 array (all args pointer/word-size →
degenerate-safe, so it works on native under B1). Verified manually on LLVM host
(`[3,1,4,1,5] -> [1,1,3,4,5]`). Model on 498_c_call_basic; xfail the compiled-only
VM/int modes. (Baremetal LLVM would need xfail too if it uses libc qsort — or write
a pointer/identity variant that needs no libc to also cover baremetal.)
