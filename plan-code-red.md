# Code Red: Tricky-Semantics Specification & Cross-Backend Pipeline Audit

> **Status:** authoritative (living document). **Scope:** the four lowering targets (LLVM codegen, native-aarch64, native-x64, bytecode VM) plus the shared type/layout layer and shared IR-gen. **Describes:** the state of `main`. **Audit basis:** the findings were gathered against worktree `work-1` @ `97cecf95`, now **32 commits behind** `main` (current @ `e85eb129`), and reconciled to `main` per the note below — many flagged bugs have since landed. Per-situation status lines reading "broken / live here" reflect the *audited worktree*; read them through the reconciliation. In any case every "broken / suspected" status is an audit lead to be re-confirmed by the P1 repros (Section 9), not a settled fact — when this document and `claude-todo.md` + the actual `main` tree disagree on a specific cell, the tree wins.

> **Audit basis & reconciliation to `main` (current @ `e85eb129`).** The audit ran against `97cecf95`, now 32 commits behind `main`; many findings have since landed. **Already fixed on `main` — NOT open:** short-var multi-bind acquire / double-free (`efa4f569`, conf 584); the *entire* managed-aggregate-by-value store class — single-assign array + slice-`@Iface`, array-element aggregate, and the literal siblings (`32bad348` + all-siblings landing); raw-pointer single-assign index `p[i]=v` element refcount (`5429a37d`); array-/managed-slice-/composite-literal managed-element acquire incl. the composite `@func` field (`f2aff0d4`); the closure `@func` **capture-site RefInc / capture-UAF — the Class-7 root cause** (`fd82c0a9` / `388c48d3`), with the VM-free repl poll now landed (`e3dc0d07`) so the wrapPoll heap-corruption thread is closed; native `@func` **vtable dtor slot → closure-struct dtor handle** + the 550 capture-record refcount (`45416376`, `7dab4be7`); by-value struct through iface dispatch (`9baa579d`, conf 585); anon-tuple field GEP padding (`5f4a8eaf`); cross-package managed-ptr value-copy `559` + field-write `561` (`32bee84c` / `733d4485` / `c4036777`, rc-balance conf 586/592); native-aa64 `&G`-as-rvalue 551/573 (`9a0f4f9a`) + `OP_BIT_CAST` global-ref (`1df89d9a`); `lower_func.bn` split (`1cc8ada8`). **Newly tracked since the audit — now a confirmed (no-longer-suspected) divergence, folded into §3.1/§3.6:** `526` — native-aa64 miscompiles a **cross-package multi-return whose component is an `@Iface`** (surfaced via `strconv.Parse`'s `(value, @errors.Error)` return; MAJOR, silent wrong-code, xfailed on the native-aa64 lane, `49d03616`). **Genuinely still open / suspected — the real remaining targets of the plan:** the **interface-method-dispatch managed-result leak** (todo: lifecycle "unwired", IN PROGRESS) + the `@[]@I` slice-literal element leak; **for-range value-bind** managed-element acquire + phantom `_` scope var; the array/slice **INDEX arms' RefDec-old-before-RefInc-new ordering** → self-alias UAF (untracked); the `@func` call-result discard leak; the VM **func-value single-return copy-back**; the **aggregate-returning capturing-closure shim** (LLVM retbuf / native); the entire **newly-surfaced sub-word dirty-upper-bits and unsigned-int↔float classes** (untracked before this audit); the **x64 multi-return** n=2 cap / ptr-not-bytes (no x64 CI lane); the **ABI iface-method byval / PlanFrame outgoing-args** holdouts; the cross-package **mangle injectivity** + four-way `implVtableName` duplication; the **generic array/func-value mangle collisions**; the **nil-`EXTRACT` VM crash**; `541` native float consts/returns + the float32-const deferral; the function-local const GROUP + untyped-single-const forward-ref; and the int-int loader `rt`-not-found. **Unrelated active work** (confirming this doc is settled relative to it): the `strconv` `ParseFloat`/`FormatFloat` series (`eb4a7aee`…`e85eb129`). When this document and `claude-todo.md` + the actual `main` tree disagree on a cell, the tree wins.

---

## 0. Why this exists (the diagnosis)

The Binate toolchain lowers one shared IR through four independent targets and one shared type/layout layer. Each target re-derives, per-arm and by hand, the same semantic decisions: which managed values to RefInc/RefDec, how to pack a tuple into registers, what the sret threshold is, whether a value is a one-word scalar or a two-word address-aggregate. The result is a recurring failure mode: **a feature is correct in mode X and silently broken in mode Y**, because the fix for one cell of a `{form × target-shape × value-type × backend}` matrix is never mechanically propagated to its siblings.

The bug history is not a scatter of unrelated defects. It clusters into eight root-cause classes (Section 2), every one of which is the *same* shape: a single invariant enforced **non-uniformly** across the matrix. The dominant class — managed-value refcount discipline (Axiom 5: RefInc-the-new, RefDec-the-old; save-copy-destroy for aggregates) — is hand-written per assignment-arm in shared IR-gen. Each new copy-site (short-var multi-bind, for-range value, raw-pointer index, composite `@func` field, interface-method call result) is authored with three of four managed-kind arms, or none. Because almost every conformance test exercises only scalar `int`/`bool` components, a missing acquire on a managed component produces **no visible failure** until the first mortal `@T`/`@func`/`@Iface`/aggregate flows through that exact untouched cell — at which point it is a double-free, UAF, or leak, not a graceful error.

Three structural coincidences hide every instance until it triggers:

1. **LP64 host coincidence.** A 64-bit host `int` absorbs every magnitude (32-on-64 truncation never fires); the 16-byte sret threshold makes def and call coincidentally agree; for `@T`/`@[]T` returns the `i8*` managed-ptr fallback and the correct type coincide at the LLVM level. So the entire ABI, sub-word, and cross-package-resolution bug families surface only on the arm32 / x64-darwin CI lanes or the VM/native float converter — never on the default host build.
2. **VM-unique slot-address representation.** The VM holds the *address* of a 16-byte iface/func value in a register; every copy/return/extract/pack pass independently re-classifies whether a value is one scalar word or a two-word address-aggregate. A fix in one pass (return copy-back) does not propagate to the others (multi-return packing, EXTRACT, dtor-handle).
3. **Test-shape coincidence.** Conformance tests put each tuple field in its own eightbyte, range over `@[]int` (scalar elements), capture only `@T`/`int`, and discard only scalar results — so the address-aggregate, sub-eightbyte, managed-element, and managed-discard cells are never exercised.

**Thesis.** The fix is not more whack-a-mole. It is three things, in order:

- **(a) A written contract per tricky-situation and per IR/VM op.** The refcount/ownership/ABI rules currently live in tribal knowledge and scattered helpers. They must be written *once* at the IR layer (and per VM op) so all four targets are checked against one spec rather than re-deriving it inconsistently. These specs live **in source** — at the op in `ir.bni` / `vm.bni`, reviewed in the same diff as the code — not in an external `.md` that drifts (Sections 4–5).
- **(b) A systematic per-op cross-backend comparison.** For each IR op, lay the four lowerings side by side against the canonical contract and flag every divergence — the methodology demonstrated by the worked traces in Section 6.
- **(c) A test matrix that exercises every cell.** The `{form × target-shape × value-type}` matrix for managed values, the address-aggregate cells, the sub-eightbyte/sub-word/32-on-64 cells, the cross-package cells — each run in **all** relevant modes (compiled, VM, native-aa64, native-x64), organized as a dedicated, manifest-tracked conformance suite rather than ad-hoc one-offs (Section 7).

Confirmed critical/major defects discovered in this audit are **raised to the user** per the project's "raise, don't work around" rule (Section 8), not silently patched.

---

## 1. The lowering pipeline & where contracts live

```
                            ┌─────────────────────────────────────────────┐
   .bn / .bni  ──► lexer ──►│ parser ──► type-checker (pkg/binate/types)   │
                            └─────────────────────────────────────────────┘
                                                │  (typed AST; types carry width, signedness, managedness)
                                                ▼
                            ┌─────────────────────────────────────────────┐
                            │  IR-gen (pkg/binate/ir, SHARED)             │
                            │  • Axiom-5 refcount discipline emitted HERE  │
                            │    as OP_REFINC / OP_REFDEC[_DTOR] /          │
                            │    __copy_ / __dtor_ / consumeTemp            │
                            │  • name mangling, string collection,         │
                            │    multi-return tuple type, vtable layout     │
                            └─────────────────────────────────────────────┘
                                                │  one IR
              ┌─────────────────────┬───────────┴───────────┬─────────────────────┐
              ▼                     ▼                        ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
   │  LLVM codegen     │  │  native aarch64   │   │  native x64       │   │  bytecode VM      │
   │ pkg/binate/codegen│  │ native/aarch64    │   │ native/x64        │   │ pkg/binate/vm     │
   │ emit_instr.bn     │  │ aarch64_dispatch  │   │ x64_dispatch      │   │ lower_instr.bn    │
   │ (SSA aggregates,  │  │ (16B data-region, │   │ (16B data-region, │   │ (16B SLOT ADDRESS │
   │  LLVM ABI legaliz)│  │  addr result)     │   │  addr result)     │   │  in a register)   │
   │ UNHANDLED: silent │  │ UNHANDLED: silent │   │ UNHANDLED: silent │   │ UNHANDLED: LOUD-  │
   │ comment-NOP       │  │ no-op             │   │ no-op             │   │ FAIL rt.Exit(1)   │
   └──────────────────┘  └──────────────────┘   └──────────────────┘   └──────────────────┘
              │                                  shared: pkg/binate/native/common
              │                                  (PlanFrame, CallConv, IsAggregateTyp)
              └──────────────────────── all four consume ───────────────────────────┐
                                                                                     ▼
                                        ┌────────────────────────────────────────────────┐
                                        │ TYPE / LAYOUT LAYER (pkg/binate/types, SHARED)  │
                                        │ SizeOf / AlignOf / FieldOffset / NeedsDestruction│
                                        │ target-parameterized by SetTarget(word size)     │
                                        └────────────────────────────────────────────────┘
```

### The contract-placement rule

Three classes of logic, three homes:

1. **Memory layout** — struct/array/slice/managed-slice layout, managed-pointer headers, the 16-byte iface-value `{data, vtable}` and func-value `{vtable, data}` aggregates — is a **language-level contract** shared by *all four backends AND the type/layout layer* (for dual-mode interop). It lives in `pkg/binate/types` (`scope.bn:112` SizeOf, `:160` AlignOf, `:207` FieldOffset), parameterized by target word size. No backend may re-derive an offset. *Today this is correctly centralized for byte sizes — but the natives re-derive slice-field offsets as `8*Index` outside the layer (Section 3.11), a tolerated-but-tracked parallel derivation.*

2. **Other language-semantic logic** — name mangling, refcount/ownership discipline, multi-return tuple type, vtable slot layout, string-constant collection — is a **shared-IR-layer contract** (`pkg/binate/ir`, `pkg/binate/mangle`). The four targets *lower* whatever IR-gen emits; they must not add or drop a RefInc/RefDec in their lowering. *Today refcount is centralized into `emitManagedValueCopyRefInc`/`emitManagedValueRefDec` but is invoked per-arm and inconsistently; mangling is hand-duplicated four ways (Section 3.12).*

3. **Target-specific lowering** — instruction selection, register allocation, calling convention *mechanism* (which register, sret vs byval emission), binary format, the VM's slot-address representation — belongs in the backend. *This is the only layer that may legitimately differ between targets.*

**The principle, stated once:** *Memory layout and language semantics are shared contracts; only target-specific lowering mechanism differs.* Every confirmed divergence in this document is a violation of this principle — a shared-contract decision that one target got wrong, or that was re-derived per-target and drifted.

---

## 2. Bug taxonomy (what actually went wrong)

Eight root-cause classes. Each is one invariant enforced non-uniformly across the matrix.

### Class 1 — Managed-value refcount matrix gaps (Axiom 5)

**Invariant whose uniform enforcement prevents the class:** Every site that writes a managed value into a managed storage slot — across `{= , a,b=f() , := , q,n:=f() , composite-lit elem , array-lit elem , mslice-lit elem , return , param-entry , for-range value} × {IDENT , SELECTOR , INDEX-array , INDEX-slice , INDEX-raw-ptr , blank _} × {@T , @[]T , @func , @Iface , managed struct/array}` — must acquire the new value (RefInc, or `__copy_` for aggregates, or `consumeTemp` on genuine ownership transfer) and release the prior occupant (RefDec / `__dtor_`), uniformly, with `consumeTemp` used **only** on genuine transfer and a discard target `_` skipping the acquire.

**Representative bugs** (confirmed in the audited worktree; **most have since landed on `main`** — see reconciliation):
- Short-var multi-bind `q,n := f()` does ZERO acquire (`gen_short_var.bn:17-34`) — CRITICAL double-free/UAF. (FIXED on `main` `efa4f569`.)
- for-range value bind over a managed-element collection does ZERO acquire but IS scope-registered for RefDec (`gen_flow.bn:146-149`) — CRITICAL, **untracked**, no test.
- Composite-literal `@func` field never acquires (`gen_composite.bn:115-129`) — CRITICAL. (FIXED on `main` `f2aff0d4`.)
- Array-literal elements never acquire (`gen_access.bn:22-48`) — CRITICAL. (FIXED on `main` `f2aff0d4`.)
- Single-assign raw-pointer index `p[i]=v` no element refcounting (`gen_control.bn:314-323`) — MAJOR. (FIXED on `main` `5429a37d`.)
- Managed-slice-literal `@func`/struct elements never acquire (`gen_access.bn:152-171`) — MAJOR. (FIXED on `main` `f2aff0d4`.)
- `@func`-returning call results not registered as cleanup temps → leak when discarded (`gen_call.bn:269-287`, `gen_method.bn:269-292`) — MAJOR.
- Single-assign array-element aggregate / multi-assign slice aggregate-with-@Iface (`gen_control.bn:242-313`, `emitStructElemRefcount` at `gen_util_refcount.bn:15-48`) — MAJOR. (Fixed on main `32bad348`; absent here.)
- Array/slice **INDEX** assignment arms RefDec-old *before* RefInc-new (`gen_control.bn:275-310`, `:349-390`), violating the documented "RefInc-before-RefDec" order the IDENT/SELECTOR/deref arms follow → self-alias UAF for `a[i]=a[i]` — MAJOR, **untracked**.

**Targets affected:** All four (shared IR-gen origin). **Status:** mostly FIXED on `main` since the audit — short-var multi-bind (`efa4f569`), the composite-`@func`-field / array-lit / mslice-lit acquires (`f2aff0d4`), raw-ptr index (`5429a37d`), and the array-element-aggregate sibling (`32bad348` + all-siblings) all landed. **Remaining open:** for-range value-bind acquire, the `@func` call-result discard registration, and the array/slice INDEX-arm RefDec-before-RefInc ordering.

### Class 2 — VM 16-byte address-aggregate (iface/func value) mishandling

**Invariant:** In the VM a register holds the SLOT ADDRESS of a 16-byte address-aggregate, so every place that copies, returns, extracts, sizes, or dtor-handles such a value must classify it via `isVMAddressAggregate` as a two-word address-aggregate — never one scalar word — across EXTRACT pointer-mode, multi-return result-layout, BC_RETURN copy-back sizing, and the dtor-handle path, treating iface and func **identically**.

**Representative bugs:** single iface-value return copy-back (fixed `511e1395`); BC_IFACE_DTOR handle-vs-index (fixed `5de3d09d`); multi-return @func packing (fixed `98f65edb`); non-capturing @func ClosureRec RefInc (fixed `d2029503`); capture-record leak (fixed `0a0d00af`). **Still open here:** single-return copy-back omits `@func`/`*func` (`lower_instr_helpers.bn:42-44` — iface present, func-value absent) — MAJOR, latent.

**Targets affected:** bytecode VM only (LLVM/native use true 16-byte register-pair/sret values). **Status:** mostly-done; one func-value single-return holdout.

### Class 3 — Cross-package / interface-name type-resolution ordering (`i8*` fallback)

**Invariant:** A type-expression naming an interface (bare, qualified, alias, self-referential) must resolve to its true 2-word type regardless of resolution order — consulting `currentImportAlias` for cross-package refs and pre-registering the symbol before resolving any signature that can reference it — instead of silently falling through to a one-word managed-ptr (`i8*`) / `TypInt()` default.

**Representative bugs:** by-value struct through iface-method dispatch (fixed `9baa579d` on main, **absent here** — live CRITICAL in this worktree); self-referential iface method (fixed `77499153`); cross-package `@Iface` return (fixed `cb8c0f1a`); same-last-segment package mangler collision (fixed `7f989ad`/`2122648`). **Still open:** qualified `TEXPR_NAMED` arm silently returns `TypInt()` on unregistered name (`gen_util.bn:244`) — MAJOR; in-package interface-before-struct degradation (`gen_iface_registry.bn:165` + `gen_util.bn:284`) — MAJOR suspected; `writeBnDotted` `'/'`/`'.'` non-injectivity (`mangle.bn:114`) — MAJOR.

**Targets affected:** all (shared IR-gen origin); LLVM verifier-loud on extractvalue paths, silent ABI miscompile on store-only paths and native; VM mis-sizes. **Status:** mostly-done upstream, but the worktree lacks `9baa579d` and the fallback-to-`int` surface persists structurally (per-call-site resolver, no single resolver).

### Class 4 — Aggregate ABI: byval/sret/outgoing-args/convention agreement across def + all call-sites + all backends

**Invariant:** The aggregate-passing and aggregate-return convention (byval threshold, sret threshold, outgoing-args reservation, indirect-large, in-register packing) must be one target-parameterized decision applied identically at the definition and at EVERY call-site path (direct, C, indirect, handle, func-value, **iface-method**) and identically by LLVM and both native backends — never hardcoded or computed per-op.

**Representative bugs:** universal-sret call-site threshold (fixed `cde84e86`); byval omission on >16B params (fixed `f5340fac`/`8ba29d11`); PlanFrame outgoing-args miss func-value/handle (fixed `a8a7dc7a`); closure-shim indirect-large (fixed `47223d3c`). **Still open here:** native-x64 multi-return caps at n=2 and packs one-field-per-GP-reg, dropping fields 3..N and mis-coalescing vs LLVM (`x64_return.bn:145-162`, `x64_call.bn:230-247`) — CRITICAL; PlanFrame outgoing-args skips `OP_CALL_IFACE_METHOD` (`common_call.bn:139-146`) — MAJOR; LLVM iface-method/handle pass aggregate args by-value vs byval callee (`emit_iface_call.bn:97-142`, `emit_call_handle.bn:89-116`) — MAJOR; `isByvalParam` hardcodes >16 not target-parameterized (`emit_util.bn:305`) — MINOR; variadic float `__c_call` mis-passed on both natives (`x64_call.bn:213-215`, `aarch64_call.bn:48-60`) — MAJOR; NSRN exhaustion drops 9th+ float (both natives) — MINOR.

**Targets affected:** LLVM + both natives; arm32-via-LLVM and x64-darwin disproportionately (small thresholds reach the masked window). **Status:** mostly-done, several confirmed-live holdouts.

### Class 5 — Sub-word & 64-on-32 scalar handling

**Invariant:** A scalar's width and signedness are carried by its resolved/target type, not the host int width: literals emit at resolved width; sub-word results stay reduced-mod-2^width with correct sign; loads sign/zero-extend per signedness; signedness selects op family (SAR/SHR, IDIV/DIV, signed/unsigned compare, sitofp/uitofp); a 64-bit scalar on a <8-byte word is a consistent register/slot pair; float32 carries its narrowed 32-bit value.

**Representative bugs:** int64-literal-under-unary-minus truncation (fixed `224e7bef`); Itoa INT_MIN (fixed `756209e2`). **Still open here:** sub-word arithmetic results not narrowed on x64/aa64/VM → dirty-upper-bits corrupt unsigned compare/shift/div/widen (`x64_ops.bn:88-216`, `aarch64_ops.bn:53-121`, `vm_exec_pure.bn:19-108`) — CRITICAL; unsigned int↔float uses signed conversion on all three non-LLVM targets (`x64_float.bn`, `aarch64_float.bn`, `vm_exec_helpers.bn:195-223`) — MAJOR; float32 const loads low 32 bits of float64 pattern (VM + both natives; `F64BitsToF32Bits` exists unused) — MAJOR, blocked on BUILDER bump; VM int→float32 not narrowed + 16-bit narrow falls to BC_MOV (`lower_cast.bn:38-118`) — MAJOR; the entire VM 64-on-32 register-pair path is inert on every CI lane (no 32-bit-host VM runner) — MAJOR latent.

**Targets affected:** VM + both natives (LP64 host immune). **Status:** mostly-open for the dirty-upper-bits and unsigned-float classes (these are *new* confirmed defects, not previously tracked).

### Class 6 — `@Iface` / `@[]@I` first-class lifecycle completeness

**Invariant:** Interface-values and managed-slice-of-interface must follow the identical lifecycle established for `@func`: `NeedsDestruction=true`, null-data-guarded RefDec, an acquire arm at every copy site, a managed-slice dtor that walks interface-valued elements regardless of construction shape, and — critically — interface-method *results* tracked for cleanup exactly like direct-call results.

**Representative bugs (confirmed live):** interface-method dispatch result of managed type LEAKS — `genInterfaceMethodCall` never registers the result as a temp and `isFreshManagedIfaceValue` omits `OP_CALL_IFACE_METHOD` (`gen_iface.bn:63-97`, `gen_refcount_pred.bn:170-179`) — CRITICAL; VM SP-leak for iface-method managed returns (no `StmtGrewSP`) — MAJOR; discarded iface-method managed result leaks (`gen_iface.bn:95-96`) — MAJOR; `@[]@I` slice-literal element-walk (440 family) — partially open. The scalar `@Iface` copy arms (single-assign, var-decl, return, multi-assign, param-move) are **present and correct** here; `emitManagedIfaceValueRefDec` IS null-guarded (`gen_util_refcount.bn:216-236`) — reconciling a stale todo claim that it was unguarded.

**Targets affected:** all (shared IR-gen); VM specifically for SP-leak. **Status:** mostly-open for the dispatch-result-leak axis.

### Class 7 — Native call-a-captured-`@func` over-release via VM trampoline

**Invariant:** Invoking a captured managed `@func` from inside a closure entered via the VM trampoline/`_call_shim` must consume exactly the call-ABI refs and not consume a ref the closure record still owns. This audit reduces a large part of this to **Class 1**: `emitCaptureRefInc` (`gen_func_lit.bn:288-305`) has no `@func`/`@Iface` arm, so a CAP_MANAGED capture is never RefInc'd while the closure-struct dtor RefDecs it — a shared-IR over-release predicted on every target.

**Representative bug:** wrapPoll-style closure that captures AND calls a managed `@func`, installed as VM poll, corrupts the heap; native-only observed, `-int` clean. **Open question:** does it fully reduce to the `emitCaptureRefInc` omission, or is there an *additional* native-trampoline-entry over-release on top (the `-int`-clean asymmetry needs a RefInc-vs-RefDec count probe).

**Targets affected:** native backends (observed); the IR-gen omission predicts all targets. **Status:** RESOLVED on `main` — the `emitCaptureRefInc` `@func` capture-site RefInc landed (`fd82c0a9` / `388c48d3`) and the VM-free repl poll (`e3dc0d07`) retired the wrapPoll thread.

### Class 8 — Multi-package loader resolution at int-int depth

**Invariant:** The package resolver must locate every transitive core import (e.g. `pkg/builtins/rt`) identically at all interpreter nesting depths (single-int, comp-int, int-int).

**Representative bug:** `136_grouped_imports` + `383_cross_pkg_iface_dtor` fail ONLY in `builder-comp-int-int` with `package "pkg/builtins/rt" not found`; green in every other mode; untracked default-mode red.

**Targets affected:** bytecode VM at int-int depth, multi-package tests. **Status:** open, root cause unknown.

**Cross-cutting meta-pattern (all eight classes):** the same shared-IR op is lowered through per-target/per-arm code paths, so a fix to one cell never mechanically propagates to its siblings; LP64-host and test-shape coincidences hide every instance until the first value of the exact triggering shape flows through the exact untouched cell.

---

## 3. Tricky situations — philosophy & contracts

This is the heart of the document. One subsection per tricky situation. Each gives the bolded **invariant**, the **philosophy** (how it must be handled across all targets + VM), the **current state** (ok/partial/broken per layer), and the **known + suspected divergences**.

### 3.1 Multi-value return / multi-bind destructuring

**Invariant: A multi-return function and every call-site that consumes it must agree on one target-parameterized tuple ABI — the anonymous struct produced by `makeMultiReturnStructType`, laid out by `types.FieldOffset/SizeOf/AlignOf` — for both packing (callee `OP_RETURN`) and unpacking (caller `OP_EXTRACT`); each component is acquired exactly once for its owner and the call-result tuple temp is RefDec'd via its struct-dtor at end-of-statement; address-aggregate components (iface/func) are moved by value, never by copying a holding register; a blank `_` acquires nothing.**

**Philosophy.** Multi-return is *one* anonymous tuple struct, and its byte layout — `FieldOffset/SizeOf/AlignOf` from `pkg/binate/types`, parameterized by target word size — is the single source of truth the callee's pack and every caller's per-field unpack must obey on all four targets. There is exactly one target-parameterized register-vs-indirect decision (the sret threshold, which must equal what LLVM legalizes for the same `{…}` struct so the native-main↔LLVM-dep boundary agrees), and exactly one in-register packing scheme **per ABI** that callee and caller derive identically — never a hand-rolled "first field reg A, second reg B, cap at two." Every component is written and read at its own `FieldOffset` with its own width: sub-word scalars use sized loads/stores; aggregate and 16-byte address-aggregate components are moved by value (memcpy of their bytes — the holding register for an alloca/aggregate/iface/func holds a *pointer/address*, not the value). Refcounting layers on top with one shared discipline regardless of `{=, :=, return f()}` form: the call produces an owned tuple temp registered for end-of-statement cleanup; each bound target acquires its component exactly once (RefInc for `@T`/`@[]T`, copy-RefInc for `@func`/`@Iface`, save-copy-destroy for managed-field aggregates) and releases the prior occupant; a blank `_` acquires nothing. All three forms and all six target shapes must reach the same per-component acquire/release path, funneled through one dispatcher.

**Current state:**
- types / type-checker: **ok** (`scope.bn`, `check_stmt.bn:128-275`).
- IR-gen `genMultiAssign` (`=` form): **ok** (`gen_assign_multi.bn:82-173`, full discipline + blank skip).
- IR-gen `genShortVar` multi-bind (`:=`): **broken** (`gen_short_var.bn:17-34`, ZERO acquire). *(Fixed on main `efa4f569`, absent here.)*
- IR-gen `emitStructElemRefcount` (multi-assign slice-element struct): **partial** (`gen_util_refcount.bn:15-48`, omits `@Iface` + nesting). *(Fixed on main `32bad348`, absent here.)*
- IR-gen call-result temp registration / anon-tuple dtor: **ok** (`gen_call.bn:268-287`, `gen_dtor_emit_bodies.bn:48-94`).
- LLVM: **ok** (insertvalue/extractvalue, `emit_helpers.bn:227-303`; multi-return never sret, relies on ABI legalization).
- native-aa64 `OP_RETURN`: **ok** for in-package returns (sret + X0..X7 field-spread, `aarch64_dispatch.bn:252-374`; matched to AAPCS in `43ab7a3`) — **but a cross-package multi-return with an `@Iface` component is miscompiled** (silent wrong-code; `526` xfailed, `49d03616`).
- native-x64 pack/collect: **broken** (`x64_return.bn:141-162`, `x64_call.bn:230-247` — n=2 cap, no aggregate load-through, per-field-per-reg).
- VM: **ok** (`packMultiReturn` per-`FieldOffset` memcpy, `isVMAddressAggregate` covers iface+func, `vm_exec_return.bn:24-66`).

**Known divergences:**
- **CRITICAL (x64):** ≤16-byte tuple with 3+ sub-word fields drops fields 2..N; def and call both cap at n=2, so even a native-only self-call gets garbage for the 3rd/4th result (`x64_return.bn:145-162`, `x64_call.bn:234-235`).
- **CRITICAL (x64):** ≤16-byte tuple with an aggregate component puts the component's *pointer* into RAX/RDX, not its bytes (`x64_return.bn:148-159`, no `IsAggregateTyp` branch); EXTRACT then Lea's a spill slot holding a dangling pointer.
- **CRITICAL (all targets):** short-var multi-bind ZERO refcount → under-retain → double-free/UAF (`gen_short_var.bn:26-32`). **FIXED on `main` (`efa4f569`).**
- **MAJOR (native-aa64), tracked:** a cross-package multi-return whose component is an `@Iface` is miscompiled — `526` xfailed on the native-aa64 lane (`49d03616`); root cause in the aa64 multi-return collect/spread for a 2-word address-aggregate component crossing a package boundary. (Anticipated under §3.9 "suspected"; now confirmed.)
- **MAJOR (x64 + LLVM):** both natives pack small tuples one-field-per-GP-reg, disagreeing with LLVM's eightbyte coalescing at the native↔LLVM-dep boundary (x64 not matched to SysV; aa64 was matched in `43ab7a3`).
- **MAJOR (all):** `emitStructElemRefcount` omits `@Iface` + nesting for `s[i],n=f()` (`gen_util_refcount.bn:25-46`). **FIXED on `main` (`32bad348` + all-siblings).**

**Suspected:** anon-tuple component with a sub-word field before a pointer-aligned field could mis-GEP on any `structLLVMIndex`/native-EXTRACT path that keeps the `8*Index` assumption for non-`TYP_STRUCT` carriers (`aarch64_emit.bn:285`, `x64_emit.bn:34-63`); needs a `{bool,@T}`/`{uint32,int64}` repro.

---

### 3.2 Blank / `_` discard of a value

**Invariant: A discarded value participates in exactly the cleanup its ownership demands — a fresh owned temp is RefDec'd/dtor'd exactly once at end of statement and the discard target adds no acquire; a borrow acquires and releases nothing; the discard never creates a storage slot or scope variable a later cleanup pass would RefDec. This is target-independent (IR-gen decides), and all four backends treat the resulting EXTRACT/LOAD of a discarded component as a pure read.**

**Philosophy.** Discarding to `_` (or leaving a call result unused) is purely the *absence* of a binding: IR-gen must add no acquire and create no slot/scope var. Cleanup correctness is delegated entirely to the producer side — every expression yielding a fresh owned managed value (direct/func-value/method/**interface-method** call, `make`/`make_slice`/`box`, string-to-chars) registers it as an end-of-statement temp at its single point of creation, and `emitTempCleanup` releases it once whether or not anything consumed it. A multi-return aggregate is registered as one whole-struct temp whose dtor recursively releases every managed field, so discarding any subset (including all) is automatically balanced — no per-component logic, and emitting a per-component EXTRACT for a discarded slot is dead code. Blank handling must be an **explicit `isBlank` branch** at every form (single/multi `=`, single/multi `:=`, `var _`, `for _ in`), never an implicit consequence of `lookupVar` returning nil. The only VM-specific concern: a 2-/3-word managed aggregate result needs `StmtGrewSP` set at the same producer site so the VM reclaims its transient SP growth.

**Current state:**
- type-checker: **ok** for assignment forms; **partial** for `for` (`check_stmt.bn:293-302`, no blank/managed-element handling).
- `genMultiAssign` blank, `genShortVar` single `_ :=`, `var _ T`: **ok**.
- single `_ =` via implicit `lookupVar==nil`: **partial** (`gen_control.bn:32-34`, fragile).
- `genShortVar` multi-blank emits a dead EXTRACT: **ok** (harmless, misleading comment).
- `genInterfaceMethodCall` result registration: **broken** (`gen_iface.bn:95-96`, no `registerTemp`/`StmtGrewSP`).
- `genForIn` value bind: **broken** (`gen_flow.bn:147-149`, no acquire; `_` becomes a phantom scope var).
- LLVM/native/VM EXTRACT: **ok** (pure reads).

**Known divergences:**
- **MAJOR (all):** discarded/unused managed-returning interface-method call leaks (`gen_iface.bn:95-96`); VM also leaks SP for `@[]T`/`@Iface`.
- **MAJOR (all):** `for _ in coll`/`for v in coll` over a managed-element collection — no acquire + scope-RefDec → over-release; blank creates a phantom `_` scope var (`gen_flow.bn:147-149`).
- **MINOR (all):** single `_ =` relies on implicit `lookupVar==nil` not an explicit `isBlank` branch; dead per-component EXTRACT in multi-blank with a misleading comment; `retTypes` gated on `OP_CALL` only so func-value/iface-method components default to `TypInt()` for the blank slot (masks the real bound-component miscompile).

**Suspected:** `_` discard of a managed-aggregate-by-value component depends on correct anon-tuple element-type propagation in the temp dtor (same family as the `@[]@I` literal-dtor gap).

---

### 3.3 Managed structs & arrays passed/stored/returned by value (save-copy-destroy)

**Invariant: Whenever a managed aggregate (struct or `[N]T` that `NeedsDestruction`) is written into a managed slot, the write must acquire the new value (`__copy_` after the store, RefIncing each managed leaf — unless the source is a fresh temp whose ownership transfers, then move via `consumeTemp` and skip the copy) and release the prior occupant (`__dtor_` on the saved old value). Each managed leaf's refcount rises by exactly one per live alias and falls by exactly one per alias destroyed — uniformly across every `{assignment-form × target-shape × aggregate-shape}` cell and identically across all four targets, because the acquire/release is emitted once in shared IR-gen. The pass/return ABI (byval/sret threshold) is an orthogonal target decision that must agree at def and every call-site but must never change which leaves get RefInc'd.**

**Philosophy.** A managed aggregate is a value type: copying it logically copies every managed leaf, and copying a leaf means RefInc. The language mandates a single mechanical save-copy-destroy at every overwrite of a managed slot, regardless of syntactic form, target shape, or backend: (1) save the slot to a scratch alloca; (2) store the new value; (3) `__copy_(slot)` RefIncs the freshly-stored value's leaves; (4) `__dtor_(saved)` RefDecs the old's. The sole exception is genuine ownership transfer (fresh composite literal or fresh call result) — then the store is a move, skip `__copy_`, `consumeTemp`. A blank `_` is the degenerate move. Because this protocol is identical for every matrix cell, it MUST be funneled through one shared dispatcher (a single `emitAggregateStore(slot, value, type, isFresh)`), not re-hand-written per arm — per-arm authorship is precisely why the matrix has holes. The four targets differ only in how they *move the bytes* (LLVM register-legalized returns vs native byval/sret/reg-pack thresholds vs the VM's slot-address copy-back); that ABI choice is orthogonal and must agree at def + every call-site path + across backends, but never changes which leaves are RefInc'd. Latency is the trap: almost every test exercises scalar fields, so a missing acquire is invisible until the first mortal managed leaf flows through the untouched cell.

**Current state:**
- `needsStructCopy`/`emitStructCopy`/`emitStructDtor`/`genStructCopy`/`genArrayCopy`/`genStructDtor`/`genArrayDtor`: **ok** — the `@func`+`@Iface` arms are present and symmetric on both copy and dtor sides (`gen_copy_emit.bn`, `gen_dtor_emit_bodies.bn`).
- var-decl, single-assign IDENT/deref/SELECTOR/slice-index, multi-assign IDENT/SELECTOR, return: **ok**.
- single-assign **array-index** aggregate: **broken** here (`gen_control.bn:242-313`, no `needsStructCopy` arm). *(Fixed on main `32bad348`.)*
- single-assign **raw-pointer-index** `p[i]=v`: **broken** (`gen_control.bn:314-323`, bare GEP+store).
- `genArrayLit` element store: **broken** (`gen_access.bn:22-48`, bare store, no acquire).
- `genManagedSliceLit` element store: **broken** (`gen_access.bn:152-171`, no `@func` arm, no `needsStructCopy`).
- `genCompositeLit` struct field: **partial** (`gen_composite.bn:115-129`, no `@func` arm).
- `genShortVar` multi-bind: **broken** (ZERO acquire).
- LLVM/native/VM: **ok** as faithful lowerings of whatever IR-gen emits — they inherit every gap above identically.

**Known divergences (all confirmed via LLVM emit inspection):**
- **CRITICAL:** short-var multi-bind ZERO acquire (`gen_short_var.bn:17-34`). **FIXED on `main` (`efa4f569`).**
- **CRITICAL:** composite-literal `@func` field `Holder{f}` never acquires; field-*assignment* `h.F=f` is correct (which is why 531/534 pass) (`gen_composite.bn:116-129`). **FIXED on `main` (`f2aff0d4`).**
- **CRITICAL:** array literal of managed elements `[2]Box{b,b}`/`[2]@T{a,a}` never acquires (`gen_access.bn:22-48`). **FIXED on `main` (`f2aff0d4`).**
- **MAJOR:** single-assign array-element aggregate from a *variable* (non-fresh) RHS — **FIXED on `main`** (`32bad348` + all-siblings landing); was live only in the audited worktree (371/366 had coincidentally balanced via fresh-literal RHS with immortal strings).
- **MAJOR:** single-assign raw-pointer index `p[i]=v` no refcounting (`gen_control.bn:314-323`). **FIXED on `main` (`5429a37d`).**
- **MAJOR:** managed-slice literal struct/`@func` elements never acquire (`gen_access.bn:152-171`). **FIXED on `main` (`f2aff0d4`).**

**Suspected:** native-aa64 16..64-byte aggregate-in-registers `[2 x i64]` pack must match LLVM legalization byte-for-byte; a 24-byte/alignment-padded aggregate could corrupt the tail word (`aarch64_call.bn:88-113`, asserted-equal by comment, not pinned). By-value struct through iface dispatch recurrence risk (Class 3) for any aggregate result resolved during interface collection before its struct decl is registered.

---

### 3.4 Cross-cutting refcount / ownership discipline (Axioms 3/4/5)

**Invariant: For every site that places a managed value into a slot whose occupant a cleanup path later releases, the site must acquire the new value and release the prior occupant, exactly balancing every release the corresponding dtor/scope-cleanup will perform. The set of managed kinds handled at every construction/copy/store site must equal the set handled by the matching copy-constructor and destructor and scope-cleanup. Any kind released by cleanup but not acquired at a store is an over-release; any acquired but never released is a leak. `consumeTemp` only on genuine ownership transfer; cross-function refs never elided.**

**Philosophy.** Managed-value ownership is a single language-level contract enforced exclusively in shared IR-gen, never re-decided per target. Every managed kind is destroyed by exactly one mechanism (`__copy_`/`__dtor_` plus scalar RefInc/RefDec), and those generators are the authoritative definition of "what gets released." Correctness reduces to one symmetry law: at every point a managed value enters a slot a release path will later touch, the acquire side must cover the *identical* set of kinds the release side covers. Because that law is matrix-shaped and today hand-written arm-by-arm, the project keeps reintroducing the same defect: a new copy-site is authored with three of four scalar arms, or none, while the matching `__copy_`/`__dtor_` already handles all four — producing silent over-release or leak. The fix is structural: route every copy-site through the two existing shared dispatchers (`emitManagedValueCopyRefInc`, `emitManagedValueRefDec`) so that adding a managed kind is a one-line change that propagates to all sites at once. A single `emitStoreManagedSlot(ctx, b, slotPtr, val, slotTyp, isInit)` should encapsulate the full Axiom-5 sequence (release-old unless init; acquire-new; store) and be the ONLY way IR-gen writes a managed slot. Blank `_` skips the acquire. A fresh result that can be discarded must always be registered as a cleanup temp at its construction op (including the currently-missing `@func` call-result case). Cross-function refs are never elided (interop transparency: a value crossing native↔VM carries its own refcount via the handle-based dtor path). The four targets then never diverge by construction.

**Current state:**
- `NeedsDestruction` (all four kinds + array-via-elem + struct-via-fields): **ok**.
- shared dispatchers `emitManagedValueCopyRefInc`/`emitManagedValueRefDec`, `emitManagedIfaceValueRefDec` (null-guarded), `emitManagedFuncValueRefDec` (null-guarded): **ok**.
- copy/dtor generators (`@func`+`@Iface` arms present): **ok**.
- complete copy-sites: var-decl, single-assign IDENT/deref/SELECTOR/array-index*/slice-index, multi-assign, return, param-entry. (*array-index aggregate broken here.)
- broken copy-sites — **most FIXED on `main`** (short-var multi-bind `efa4f569`; composite `@func` field, array-lit, mslice-lit `f2aff0d4`; raw-ptr index single-assign `5429a37d`). **Remaining open:** for-range value bind, the `@func` call-result temp registration, and the INDEX-arm ordering defect below.
- **ordering defect:** array-INDEX and slice-INDEX arms RefDec-old *before* RefInc-new (`gen_control.bn:275-310`, `:349-390`), violating Axiom 5 → self-alias UAF.
- LLVM/native: **ok** as faithful lowerings.
- VM: **partial** — also misses `StmtGrewSP` for the `@func` call-result gap → unreclaimed SP growth.

**Known divergences:** the Class-1 cells above — of which short-var multi-bind, composite `@func` field, array-lit, raw-ptr index, and mslice-lit are **FIXED on `main`** (see reconciliation). **Remaining open** Class-1 cells: for-range value bind, the `@func` call-result leak, and the array/slice INDEX-arm RefDec-before-RefInc ordering. `@Iface` scalar copy arms are present and correct here; the residual `@Iface` work is the `@[]@I` literal-element-dtor (440 family) and the interface-method-dispatch result leak (§3.6).

**Suspected:** VM-entered capturing closure that calls a captured `@func` over-releases (Class 7) — predicted by the `emitCaptureRefInc` `@func` omission, possibly with an additional native-trampoline over-release; params use MOVE for `@Iface`/`@func` (no entry RefInc) and a copy-model arg site for these on the VM would read reclaimed transient SP.

---

### 3.5 Closures / function-values (`@func`, `*func`)

**Invariant: A function value is a 16-byte `{vtable, data}` address-aggregate (vtable-first — opposite of iface) with exactly one owning reference to its data payload; every operation preserves a single target-independent ownership ledger: every durable store of a managed `@func` (incl. closure-capture) RefIncs the data slot; every overwrite/scope-exit/dtor RefDecs it once via a null-data-guarded dtor-handle fetch; the closure-struct dtor is reachable through vtable slot-0 on all compiled targets and the VM sentinel; CALL_FUNC_VALUE/CALL_HANDLE dispatch through `vtable.call(data, args)` with the def/call convention agreeing across LLVM/both natives/VM for scalar AND aggregate returns; the VM treats the value as a two-word address-aggregate (`isVMAddressAggregate`) at every copy/return/extract/pack/dtor site.**

**Philosophy.** Function values are 16-byte `{vtable, data}` address-aggregates with one owning reference, governed by a single target-independent ownership ledger expressed once in shared IR-gen. Acquire and release are total: any site that durably stores a managed `@func` — every assignment form, every literal element, return, param-entry, AND closure-capture — RefIncs the data slot (or save-copy-destroy for an aggregate carrying a `@func` field), using `consumeTemp` only when a fresh value's sole reference transfers; a discard skips the acquire. Every overwrite/scope-exit/dtor RefDecs once through a null-data-guarded fetch. This must funnel through ONE dispatcher with the type-checker's CAP_MANAGED/managed-scalar classification as the single source of truth — a cell the checker says is managed but IR-gen skips is a latent double-free by construction. The closure-struct dtor must be reachable through vtable slot-0 on EVERY compiled target (LLVM's `__handle` dtor triple, both natives' vtable slot-0, the VM's `compiledClosureDtorMark` sentinel) — no target may emit a null dtor slot for a capturing managed closure. The shim ABI (captures prepended, user args spilled on overflow) and the convention (scalar via always-shim, aggregate via retbuf/sret) must be one target-parameterized decision applied identically at the closure shim, the non-closure shim, and every call site — capturing and non-capturing must not diverge. The VM's slot-address representation must be classified as a two-word address-aggregate everywhere, and every region-sizing pass must account for every arg-packing call op including CALL_HANDLE.

**Current state:**
- type-checker `captureKindFromType` (CAP_MANAGED for `@func`/`@Iface`): **ok**, but `genMethodValue` hardwires `*func`: **partial**.
- IR-gen `emitCaptureRefInc`: **broken** (`gen_func_lit.bn:288-305`, only `@T`/`@[]T` arms; no `@func`/`@Iface`).
- IR-gen literal-element / composite-field acquire arms: **broken** (missing `@func`).
- LLVM: **partial** — vtable dtor slot wired correctly (`emit_funcvals.bn:374-388`); capturing-closure aggregate-return shim has NO retbuf path (`emit_funcvals_closure.bn:37-139`).
- native-aa64/x64 vtable dtor slot: **broken** (`aarch64.bn:391-392` `a.Zero(8)`, `x64_funcvalue.bn:303-304` `a.Fill(8,0)` — unconditional zero); x64 value-operand sites (no `emitValOperand`): **broken**.
- VM: **partial** — `BC_FUNC_VALUE`/`ensureHandle`/dispatch correct; single-return copy-back omits func-value (`lower_instr_helpers.bn:42-44`); `findMaxCallArgs` omits CALL_HANDLE; `*func` capture-rec leak.

**Known divergences:**
- **CRITICAL (all):** `emitCaptureRefInc` no `@func`/`@Iface` arm → 0 acquires / 1 release → premature free of the captured value; the real origin of the wrapPoll heap corruption (Class 7). **FIXED on `main` (`fd82c0a9` / `388c48d3`); wrapPoll thread closed by the VM-free poll (`e3dc0d07`).**
- **MAJOR (both natives):** vtable dtor slot hardcoded zero for capturing managed closures → captured managed values freed without cleanup; aa64 xfailed (550), x64 untracked-latent. **FIXED on `main` (`45416376` + `7dab4be7`).**
- **MAJOR (LLVM + both natives):** aggregate-returning capturing closures — LLVM shim has no retbuf path; natives fall back to a plain branch.
- **MAJOR (x64):** func value from `&G`/global drops a word (no `emitValOperand`); no x64 CI lane.
- **MAJOR (VM):** single-return func-value copy-back omitted (`lower_instr_helpers.bn:42-44`).
- **MINOR:** method values can never be `@func`; VM `findMaxCallArgs` omits CALL_HANDLE; VM `*func` capture-rec leak; VM cross-mode shim reads a fixed 7 args.

**Suspected:** the `emitCaptureRefInc` omission is FIXED (`fd82c0a9`) and the wrapPoll thread is closed (`e3dc0d07`), so the "additional trampoline-entry over-release" hypothesis is retired; the x64 value-operand parity gap remains open (aa64 fixed `9a0f4f9a`).

---

### 3.6 Interfaces (`@Iface`, `*Iface`, vtable dispatch, upcast, dtor)

**Invariant: An interface value is one language-level 16-byte two-word value `{data, vtable}`; (A) a type-expr naming an interface resolves to its true 2-word type at every resolution order, never degrading to a one-word `i8*`/int fallback; (B) `@Iface` owns its data ref, acquired once at construction (null-guarded RefDec via vtable[0] at release), with every storage-write site acquiring the new and releasing the old, and the PRODUCER (incl. interface-method dispatch) irrelevant to lifecycle — a dispatch result is registered for cleanup exactly like a direct-call result; (C) the 2-word value, construction, dispatch (incl. sret), upcast, dtor behave identically across LLVM/native, and the VM classifies it as a 16-byte address-aggregate everywhere.**

**Philosophy.** The layout is a target-parameterized contract owned by `pkg/binate/types`, identical for all four (the VM merely holds the *address* of the 16-byte slot). Type-expressions naming an interface (bare, qualified, alias, self-referential, self-extending) must resolve through ONE resolver that pre-registers the interface's identity before resolving any signature that can reference it and consults the active import alias — never falling through to a one-word default; method-result types naming by-value structs must resolve against a struct table fully name-populated before ANY interface-method-result resolution (the pre-pass must cover the current package, not just imports). Lifecycle follows Axiom 5/3 uniformly: acquired once at construction, released once via a NULL-DATA-GUARDED RefDec fetching the dtor from vtable[0]. Crucially, the PRODUCER is irrelevant: a value from `OP_CALL_IFACE_METHOD` must be registered for cleanup and classified as fresh exactly like a direct-call value — funneled through one shared "register-managed-call-result" helper and one "is-this-a-fresh-managed-construction" predicate that BOTH enumerate every managed-result-producing op. The single emptiness predicate is "vtable word non-null" (the dtor lives there), shared by `present()` and the RefDec guard. Backends differ only in mechanism: LLVM/native use a true 16-byte value (sret above threshold at def AND every dispatch site); the VM uses `isVMAddressAggregate` in every pass and sets `StmtGrewSP` for any aggregate copy-back.

**Current state:**
- type-checker `defineInterface` before sig resolution: **ok**.
- IR-gen `isInterfaceTypeExpr`/`ifaceTypeForName` (consults `currentImportAlias`): **ok**.
- IR-gen `genInterfaceMethodCall` result handling: **broken** (`gen_iface.bn:63-97`, no `registerTemp`/`StmtGrewSP`); `isFreshManagedIfaceValue` omits `OP_CALL_IFACE_METHOD`: **broken**.
- IR-gen scalar `@Iface` copy arms (single-assign, var-decl, return, multi-assign, param-move): **ok and present here** — including the null-guarded `emitManagedIfaceValueRefDec` (reconciling a stale todo that claimed it unguarded).
- IR-gen short-var multi-bind `@Iface`: **broken** (ZERO acquire); raw-pointer index `p[i]=v` `@Iface`: **broken**.
- IR-gen in-package MethodResults resolution: **partial** (`gen_iface_registry.bn:156-169` resolves during interleaved collection; `TypInt()` fallback at `gen_util.bn:284`).
- LLVM/native iface dispatch/dtor/upcast: **ok** (vtable slot-0 = dtor handle, the 520 fix).
- VM: **partial** — dispatch/dtor/upcast correct (loud-fails on nil-iface); single-iface return copy-back present (560 fix); but `genInterfaceMethodCall` gap leaks SP.

**Known divergences:**
- **CRITICAL (all):** interface-method dispatch result of managed type LEAKS — never registered as a temp; `isFreshManagedIfaceValue` omits the op so copy-sites treat it as a borrow and apply an extra RefInc (`gen_iface.bn:63-97`, `gen_refcount_pred.bn:170-179`). Value-correct, silent, compounding; `575` exercises `cur=cur.next()` but checks only summed values.
- **CRITICAL (all):** short-var multi-bind `@Iface` ZERO acquire.
- **FIXED on `main` (`9baa579d`, conformance 585):** by-value struct through iface dispatch degraded to `int` (`gen_iface_registry.bn:165` + `gen_util.bn:284` fallback) — a live CRITICAL in the audited worktree. The *structural* hazard persists and is suspected-defect #2 (Section 8): the fix is a struct-name pre-pass, not one unified resolver, so the same-package interleaved-collection ordering can still degrade an aggregate result resolved before its struct is registered.
- **MAJOR (VM):** per-call SP leak for iface-method managed returns (`StmtGrewSP` never set).
- **MAJOR (all):** raw-pointer index `p[i]=v` `@Iface` no refcounting.
- **MINOR:** `present()` tests vtable word; RefDec guards on data word — un-enforced asymmetry.

**Suspected:** in-package interface-before-struct MethodResults degradation (needs a `interface I { m() S } ... type S struct{...}` repro); `@[]@I` slice-literal element-walk (440 family); cross-package store-only `@Iface` 1-word-vs-2-word fallback.

---

### 3.7 Constants

**Invariant: A named constant is a typed compile-time scalar with no storage; wherever declared (package single/group, imported, function-local) and read (bare ident or `pkg.Name`), every target materializes the SAME value at the SAME resolved width/type the checker computed — host-independent, decl/order-independent, registration-path-independent; non-int consts (bool/float32/float64) materialize their true value (float32 NARROWED, not the low 32 bits of the float64 pattern); `&C`/`&pkg.C` and assign-to-const rejected uniformly; a read never falls through to a zero placeholder.**

**Philosophy.** There must be exactly ONE evaluator and ONE registry for constants, target-parameterized, used by every declaration form and consulted by every read form. That evaluator computes values at full bignum precision (matching `foldIntArith`/`foldIntBitwise`); the registry entry carries the value as a host-independent int64 (or IEEE bits for floats) AND the resolved type+width — never a host-`int`-truncated value, never a default placeholder. Read sites materialize at the const's resolved type. Each backend lowers `OP_CONST_FLOAT` identically: a float32-typed const MUST be narrowed (shared `F64BitsToF32Bits` or LLVM `fptrunc`), never the low 32 bits. Const-folding through binops happens once in the checker's bignum folder. Reading a const must NEVER fall to zero — a miss is a compiler bug. Function-local consts are first-class — both single and group forms defined by the checker into local scope AND registered by IR-gen. `&C`/assign-to-const rejected uniformly for every scope (already correct).

**Current state:**
- type-checker const-folding, `&C`/assign rejection: **ok**; local `STMT_DECL` handles single but NOT `DECL_GROUP`: **broken** (`check_stmt.bn:76-97`).
- IR-gen `evalConstExpr`/`ModuleConst.Val` (host int): **partial**; `genDecl` handles only `DECL_VAR` (drops local consts): **broken** (`gen_stmt.bn:125-333`); REPL `GenConstMember` int-only: **broken**; six divergent registration paths: **partial**.
- LLVM `OP_CONST_FLOAT` (fptrunc for f32): **ok**.
- native-aa64/x64 `emitConstFloat`: **broken** (full 64-bit `ParseFloatLitToBits`, no narrow).
- VM `OP_CONST_FLOAT`: **broken** (`lower_instr.bn:46-68`, low 32 bits).
- `F64BitsToF32Bits`: **absent** (exists in common, zero non-test callers).

**Known divergences:**
- **CRITICAL (all):** function-local `const X int = 42` silently reads 0 (`gen_stmt.bn` genDecl has no `DECL_CONST` arm); module-level reads correctly. Untracked.
- **MAJOR (types + all):** function-local const GROUP → `undefined: A/B/C` type error (`check_stmt.bn:76-97` no `DECL_GROUP`).
- **MAJOR (VM + both natives):** float32 const loads low 32 bits of float64 pattern; `F64BitsToF32Bits` unwired (blocked on BUILDER bump); 539 xfailed.
- **MINOR:** REPL parked-const non-int path misclassifies; six const-registration paths drift.

**Suspected:** large typed const direct-read truncates on a 32-bit host (`ModuleConst.Val` is host `int`); latent on 64-bit host — needs a 32-bit-host repro.

---

### 3.8 Sub-word & 64-on-32 scalars

**Invariant: A scalar's observable value is defined by its resolved type (width + signedness), parameterized by the target word size — never by the host int width or the carrying register/slot width. Integer literals/constants materialize at resolved width (int64/uint64 survive a 32-bit target; float32 carries the rounded 32-bit pattern). A sub-word result of any op reads as reduced-mod-2^width with the correct sign — bits above its width must not leak into a width-sensitive consumer. Loads sign-extend iff signed. Signedness selects op family (SAR/SHR, IDIV/DIV, signed/unsigned compare, sitofp/uitofp). A 64-bit scalar not fitting one host word is a consistent register/slot pair every op agrees on.**

**Philosophy.** Width and signedness are properties of the resolved type, parameterized by target word size, honored identically by every target — never inferred from host int or physical carrying width. The types layer is the single source: predeclared `int`/`uint` widths derive from `SetTarget` (every driver must call it before any types op), and all targets consume those numbers. Exactly one place per concern, consulted everywhere: signedness selects the op family from the operand type; a sub-word value carries the invariant "bits above its width are correct sign/zero extension," and because LLVM gets this free from true-width SSA while native and the VM compute in full host-word registers, native and the VM MUST re-establish the invariant after every value-producing sub-word op — either narrow after each sub-word arithmetic/shift, or narrow at every width-sensitive consumer — decided once in a shared classifier; constants materialize at resolved width on every target (bignum fold, not host arithmetic; float32 via `F64BitsToF32Bits`); a 64-bit scalar has ONE register-pair representation exercised by a real 32-bit lane. The recurring failure is LP64 coincidence masking each gap until the first value of the exact shape flows through.

**Current state:**
- types `makeIntType`/`makeFloatType`/`SetTarget`: **ok**.
- IR-gen literal narrowing (`tryFoldOversizedConst`): **ok**; `EmitBinop` emits one binop at result width with NO post-op truncation: **partial**; legacy `parseIntLit` host-int: **partial**.
- LLVM `emitCast` (sext/zext, sitofp/uitofp, fptrunc): **ok**.
- native-aa64/x64 scalar load/store sign-ext, cast narrowing: **ok**; but binops 64-bit with no sub-word narrowing: **broken**; `emitFloatCast` always signed: **broken**; `emitConstFloat` no f32 narrow: **broken**.
- VM lowerBinOp/lowerCmpOp (signedness-correct, pair-aware): **ok**; but `execArithOp`/`execCmpOp` at host int with no sub-word narrow: **broken**; `lowerCast` int→float always signed + 16-bit narrow falls to BC_MOV: **broken**; the 64-on-32 pair machinery inert on every CI lane: **partial**.

**Known divergences:**
- **CRITICAL (x64 + aa64 + VM):** sub-word arithmetic results NOT narrowed → dirty upper bits corrupt unsigned compare/shift/div/widen/return. LLVM correct. Untracked. Trigger: `(a+b) <u c`, `(a*b)>>n`, `cast(uint64, a*b)` with overflowing uint32 operands.
- **MAJOR (x64 + aa64 + VM):** unsigned int↔float uses signed conversion (Cvtsi2sd/Scvtf/BC_SITOF); LLVM correct (isUnsigned-gated). Checker permits the cast. Untracked.
- **MAJOR (VM + both natives):** float32 const loads low 32 bits (539 xfailed).
- **MAJOR (VM):** int→float32 not narrowed; 16-bit narrow falls to BC_MOV (`lower_cast.bn:38-118`).
- **MAJOR (VM):** the entire 64-on-32 register-pair path is correct only by inspection — no 32-bit-host VM lane.
- **MINOR:** legacy `parseIntLit` host-int overflow on a 32-bit host.

**Suspected:** anon-tuple sub-word-before-pointer field mis-GEP on native/VM extract (`8*Index` for non-`TYP_STRUCT`).

---

### 3.9 ABI / calling convention

**Invariant: For any function f, how each parameter is passed and how the result is returned is a single target-parameterized contract derived only from f's types + the target ABI descriptor, applied byte-identically at the definition, at EVERY call-site lowering (direct, C, indirect, handle, func-value, iface-method), and across every backend that can be on either side of the boundary (LLVM, native-aa64, native-x64; the VM uses a value-copy model exempt from register/sret ABI but its copy-back must agree on byte layout). The param/return shape a call site emits for a >16-byte (LP64) / >4-byte (ILP32) aggregate must equal the shape the callee declares — never per-op, per-dispatch-shape, or hardcoded to one word size.**

**Philosophy.** The calling convention is a single target-parameterized contract, not a constellation of per-op thresholds. There must be ONE classifier: given a type and the ABI descriptor (word size, GP/FP reg counts, sret threshold, aggregate-in-reg max, split-vs-memory, indirect-large, sret-in-gp-arg-reg, variadic-stack-only) it answers pass-shape and return-shape for every value. Every consumer — the definition emitter, all six call-site lowerings, both natives — reads from that one classifier; none re-derives a threshold inline or special-cases one dispatch shape. The natives centralize this in `pkg/binate/native/common`; the LLVM backend must reach the SAME decisions through `needsSret`/`isByvalParam`/`writeParamTypeLLVM`, which must be uniformly target-parameterized (`isByvalParam` must consult `GetTarget()` exactly as `needsSret` does) and invoked by EVERY call-site emitter (iface-method and handle dispatch must pass aggregates by the same byval/indirect/`i8*`-shim shape their callees declare). Variadic handling is part of the contract: `fixedCount` and the platform rule (darwin stack-only, SysV `AL`=vector-count) threaded through BOTH integer and float arms. The VM is exempt from sret/byval but its copy-back/packing must agree byte-for-byte on `FieldOffset` (dual-mode interop). Cross-package/cross-target agreement falls out for free — and the LP64 16-byte coincidence must be treated as a coincidence to remove, not a property to rely on.

**Current state:**
- type-checker `checkCCall`/`isCCompatibleArgType`: **ok**.
- IR-gen (carries types both backends classify from): **ok**.
- LLVM `needsSret` (target-parameterized): **ok**; `isByvalParam` (hardcoded >16): **partial**; iface-method/handle aggregate-arg paths (bare `llvmType`, not byval): **broken**.
- native-aa64/x64 common `CallConv`/`argRegWordsStackWords`/return classifiers: **partial** (PlanFrame outgoing-args misses iface-method; variadic float arm bypasses `fixedCount`; NSRN exhaustion drops floats).
- VM (value-copy model): **absent** (ABI thresholds N/A; copy-back layout must agree).

**Known divergences:**
- **MAJOR (LLVM):** iface-method dispatch passes >16-byte aggregate arg by-value vs the byval-`ptr` callee thunk (`emit_iface_call.bn:97-142`); natives correct.
- **MAJOR (LLVM):** CALL_HANDLE passes >8-byte aggregate arg by-value vs the `i8*`-shim (`emit_call_handle.bn:89-116`).
- **MAJOR (both natives):** variadic float `__c_call` mis-passed — x64 `AL=0` always; aa64-darwin float in D-reg not stack (`x64_call.bn:213-215`, `aarch64_call.bn:48-60`).
- **MINOR:** multi-return-sret decided in two places with different predicates (coincide on LP64); `isByvalParam` not target-parameterized; NSRN exhaustion drops 9th+ float on both natives.

**Suspected:** >16-byte multi-return through an iface method (dispatch-sret vs thunk-struct-by-value) — needs confirmation that multi-return iface methods are representable; PlanFrame sret-shift + saturated-GP + float-args interaction untested.

---

### 3.10 Spilling & register pressure (PlanFrame, byval-spill hoist, VM SP)

**Invariant: For every function the native frame must be sized so the outgoing-args area is ≥ the stack-arg footprint of EVERY call op that dispatches through arg-regs + stack (direct, C, func-value, handle, indirect, AND iface-method), computed by the single shared classifier; per-call temporary buffers (byval spill, sret retbuf, aggregate-arg slot) are alloca'd once in the entry block, never per-iteration; the sret-stash slot and the SysV hidden-sret-in-GP-arg-reg shift are accounted identically at def and every call site. In the VM, any value whose register holds the ADDRESS of a 16-byte address-aggregate built in the callee's vm.SP region must be copy-back-sized on single-return, memcpy-packed on multi-return, and extracted in pointer mode — uniformly — and SP_RESTORE fires only at statement boundaries where no register still points into the about-to-be-reclaimed region.**

**Philosophy.** Spilling is governed by ONE target-parameterized frame model. The natives use a deliberately simple spill-everything allocator, so the only correctness obligation is that `PlanFrame` sizes the frame correctly: the outgoing-args area must be the max over the stack-arg footprint of every call shape that dispatches through arg-regs-plus-stack — `{direct, C, func-value, handle, indirect, iface-method}` enumerated in exactly one place (`isCallOp`) feeding one arg-shape classifier (`callDispatchArgTypesAnyOp`) that knows each shape's hidden-prefix slots. Omitting any one shape lets that call's spilled args overwrite the caller's frame — silent because LP64 register budgets hide it until enough args spill. Per-call buffers must be alloca'd once in the entry block (LLVM non-entry allocas aren't reclaimed until return), so the hoist pre-pass must cover every buffer-allocating call op. In the VM, the unique hazard is a register holding the SLOT ADDRESS of a 16-byte aggregate built in the growing vm.SP region; every return/pack/extract/store pass must classify it via `isVMAddressAggregate`, with iface and func handled identically. SP_RESTORE is the VM's statement-end reclaim and is correct because IR-gen is the oracle — it emits SP_RESTORE only after a statement stored every live address-aggregate into stable storage, never on the return path. The natives/LLVM treat SP_RESTORE as an explicit, tested no-op (explicit so a future "diagnose unhandled op" change can't silently regress it).

**Current state:**
- IR-gen `noteSPGrowingResult`/`StmtGrewSP`/`EmitSPRestore`: **ok**.
- LLVM byval-spill hoist: **partial** (covers OP_CALL; iface-method byval not emitted at all); SP_RESTORE no-op pinned by test: **ok**.
- common `PlanFrame` outgoing walk: **partial** (gates on `isCallOp` which omits iface-method).
- native iface emitters spill user args into the unreserved area: **broken** (`x64_iface.bn:125-126`, `aarch64_iface.bn:130-131`).
- x64 SP_RESTORE no explicit arm/test: **partial**.
- VM single-return copy-back: **broken** (omits func-value); multi-return/EXTRACT/STORE via `isVMAddressAggregate`: **ok**; BC_SP_RESTORE: **ok**.

**Known divergences:**
- **MAJOR (both natives):** PlanFrame outgoing-args skips `OP_CALL_IFACE_METHOD` (`common_call.bn:139-146`); `callDispatchArgTypesAnyOp` has no iface-method arm → overflow user args land in an unreserved region overlapping spill/alloc/sret slots. Exact 523-class bug left unfixed for iface-method.
- **MAJOR (VM):** single-return copy-back omits func-value (`lower_instr_helpers.bn:42-44`).
- **MINOR (x64):** no explicit SP_RESTORE arm/test (correct by silent-drop, fragile).

**Suspected:** LLVM iface-method >16-byte struct arg byval-vs-value mismatch (Section 3.9); VM cross-mode shim fixed-7-arg truncation; iface-method outgoing-args gap reachability on arm32-via-LLVM.

---

### 3.11 RODATA / static data & string constants

**Invariant: A value aliasing static/rodata data (`RODATA_SLICE`/`MSLICE`/`ARRAY`, string constants, static-managed globals) is IMMORTAL — RefInc/RefDec on it is a guaranteed no-op (null or negative-sentinel refcount), never freeing the shared backing, never depending on dynamic balance; a string literal stored into a char-slice element must go through `EmitStringToChars` to build the full slice header, not just an 8-byte data pointer; offsets/sizes for static globals come from the shared layout layer.**

**Philosophy.** Static/read-only data must be modeled as IMMORTAL — refcount-inert by construction, not by dynamic balance. A single shared classifier (`isStaticOrImmortalManaged`) should drive this: any value derived from a RODATA op, a string constant, or a static-managed global carries an inert refcount (null refptr/data, or the negative `STATIC` sentinel) so every RefInc/RefDec path short-circuits at the runtime's existing `ptr==nil`/`refcount<0` guards. The aliasing is then provably sound because the static value is both immutable (gated by the const-element type rule) and inert. The VM currently materializes string constants via `make_slice` (a real refcount-1 slice), staying balanced only by dynamic accounting — fragile: any arm that `consumeTemp`s a `RODATA_MSLICE` or any unbalanced RefInc would corrupt the shared string table. The VM should materialize with a `STATIC_REFCOUNT` sentinel (truly immortal, matching LLVM/native `refptr=null`) to remove the dependence.

**Current state:**
- IR-gen `CollectStrings` (dedups all RODATA + string-const), `genRawSliceLit`/`genManagedSliceLit`, `isFreshManagedSlice` (excludes `RODATA_MSLICE` → borrowed): **partial** (genArrayLit element acquire gaps inherited from Class 1).
- types `isConstByteElemType` (gates rodata aliasing to immutable views): **ok**.
- LLVM `.str.N.ms` refptr=null, `emitConstNil` fieldwise-zero: **ok**.
- native-aa64/x64 rodata header refptr=0, `emitConstNil` zero-fill: **ok**.
- VM `materializeModuleStrings` (real refcount-1 slices, not immortal): **broken** by-design-divergence; `CONST_NIL`→`BC_LOAD_IMM 0` (single scalar, type-blind): **broken** for aggregate-nil.

**Known divergences:**
- **CRITICAL (VM):** EXTRACT on a `CONST_NIL` managed-slice → null deref. `var x @[]T = nil`, `x = nil`, `obj.field = nil`, `*p = nil`, `arr[i] = nil` reach an unguarded `emitManagedSliceRefInc` arm (the only managed-scalar arm NOT isFresh-guarded) → `EXTRACT(CONST_NIL, field 2)` → VM reads address 16 → SEGV (`gen_stmt.bn:255-257`, `gen_control.bn:73-200`, `lower_instr.bn:36-40`/`:338-342`, `vm_exec_helpers.bn:389-392`). `gen_composite.bn:110-115` guards the struct path but the guard was never propagated. Latent (no test does explicit `@[]T = nil`).
- **MAJOR (all):** array-lit / mslice-lit managed-element acquire gaps (Class 1, here through the static-data lens).
- **MINOR (VM):** `RODATA_MSLICE` balanced only by dynamic accounting, not an inert sentinel.
- **MINOR (LLVM vs native):** `FindStringID==-1` fallthrough handled incompatibly (native `return`, LLVM emits `@.str.-1.ms`); currently unreachable.

**Suspected:** string literal stored into a char-slice element without `EmitStringToChars` writes only the data pointer → len reads 0; cross-package managed extern-var dtor (559/561).

---

### 3.12 Cross-package handling

**Invariant: For any symbol S defined in package A and referenced from B, every layer agrees on (a) S's canonical identity — A's full package PATH, never the import alias, never A's last segment; (b) S's true type — a cross-package interface resolves to a 16-byte iface-value, a struct to its real layout, NEVER degrading to a one-word int/`i8*`; (c) the single mangled symbol string, computed identically by every backend; (d) cross-package managed values follow the same Axiom-5 discipline with the importer reaching the owner's dtor. Resolution is order-independent.**

**Philosophy.** Cross-package handling must funnel through ONE shared identity-and-mangling layer. (1) Canonical identity is always the full package PATH, resolved from the import alias exactly once at IR-gen time; the alias must never survive into a symbol/key/type identity. (2) Resolution must be principled and order-independent: IR-gen should consult the type-checker's full-path scope lookup rather than re-deriving through alias-string heuristics with a `TypInt()`/`i8*` fallback — and where IR-gen still resolves independently, an unresolved qualified name must be a hard error, never a silent one-word degrade. (3) Every mangled symbol — function, struct, global, dtor, impl-vtable, generic instance — must be produced by a single shared function in `pkg/binate/mangle` that all four targets import; the impl-vtable name, method-func name, and pkg-ident fold must each have exactly one definition, and the mangling must be injective (a path-`/` and a member-`.` must encode distinguishably). (4) A generic instance is keyed on the emitting package only because impl + construction + dispatch live together; if any can cross a boundary, the instance identity must be canonicalized on the defining generic's package. (5) Cross-package managed values obey the identical Axiom-5 discipline (the importer reaches the owner's dtor via the handle/extern-declaration for every form). (6) Cross-package storage resolves to the owner's single allocation, and a missing resolution is a diagnostic, never a 0/null fallback.

**Current state:**
- types `resolveNamedTypeExpr`/`PackageType`: **ok**.
- IR-gen `resolveTypeExpr` bare arm (consults alias): **partial**; qualified arm falls to `TypInt()` silently: **partial** (`gen_util.bn:244`).
- IR-gen iface/`buildQualName`/generic mangle: **partial** (generic instances keyed on consumer pkg; `mangleTypeArg` lossy for arrays/func-values; see 3.13).
- mangle `writeBnDotted` (`'/'`/`'.'` non-injective): **partial**.
- LLVM/aa64/x64/VM `implVtableName`: **broken** (four hand-duplicated copies).
- runtime dtor via handle: **ok**.

**Known divergences:**
- **MAJOR (all):** impl-vtable symbol name hand-duplicated across FOUR functions (`emit_impls.bn:275`, `aarch64_iface.bn:341`, `x64_iface.bn:298`, `lower.bn:327`); the VM comment admits declining a shared helper.
- **MAJOR (all):** `resolveTypeExpr` qualified arm silently returns `TypInt()` on unregistered name (`gen_util.bn:244`).
- **MAJOR (all):** `writeBnDotted` folds `'/'` and `'.'` identically → `pkg/geom/Point.M` (free func) collides with `pkg/geom.Point.M` (method).
- **FIXED on `main`:** `var n @pkg.T = pkg.G` whole-value-copy (importer's RefDec via the imported dtor) — `559`/`561` un-xfailed (`32bee84c` / `c4036777`, rc-balance conf 586/592). Was a live crash in every mode in the audited worktree.
- **MINOR:** `&pkg.N` (address-of cross-package scalar global) unimplemented; singular `RegisterImport` (test-only) int-only.

**Suspected:** cross-package generic-interface instance keyed on consumer not defining pkg (impl-in-A/dispatch-in-B vtable-name mismatch; 464 keeps both in main); VM `lookupGlobalAddr` 0-on-miss silent null-deref.

---

### 3.13 Generics (monomorphization)

**Invariant: Each distinct `(generic decl, type-arg vector)` produces exactly one specialized concrete entity whose name is (a) injective — distinct tuples map to distinct names, no two non-identical instantiations share a cache slot/symbol — and (b) a valid object-format identifier; the specialized body is IR-gen'd with each type-param substituted to its concrete type and applies the full managed-value lifecycle according to the SUBSTITUTED type, identically regardless of raw-vs-managed type-arg and target. The four backends see only the post-monomorph concrete entity.**

**Philosophy.** Generics are resolved entirely by IR-gen monomorphization; after it the four backends see only concrete entities. Target-independence rests on two IR-gen contracts. First, COMPLETE SUBSTITUTION: the type-param context is pushed while emitting the body, so `make_slice(T,n)`, field types, params, results, constraint receivers all resolve to the substituted type, and from that point the body obeys the exact same managed-value lifecycle a hand-written concrete function would. Second, an INJECTIVE, LEGAL MANGLE: the instance name is both the cache key and the linker symbol, so it must map distinct tuples to distinct names and be a valid identifier. This forces three properties the current `mangleTypeArg` lacks: (a) every type-arg shape the language admits — primitives, named types, pointers, managed pointers, slices, ARRAYS (size-bearing), FUNC-VALUES (signature-bearing), const-qualified, nested instantiations — contributes a LOSSLESS structure-preserving token (`arr_N_elem`, `fv_<sig>`, `ro_elem`), never the lossy diagnostic spellings `[...]`/`<unknown>`/`readonly int` that collapse distinct types or inject illegal chars; (b) the token incorporates the DEFINING package; (c) it folds every non-identifier char uniformly on both def and call paths. The mangle should be a single shared total function over `@Type` the checker's cache key and IR-gen's symbol both derive from. When these hold, generics need zero per-target code.

**Current state:**
- type-checker `resolveTypeInstantiation`/`substituteTypeParams`: **ok**.
- IR-gen `instantiationMangledName`/`mangleTypeArg`: **broken** (array/func-value/const collisions + illegal chars); `ensureInstantiated*`: **ok**; substitution context: **ok**.
- imported generic registered under bare name (alias not threaded into mangle): **broken**.
- LLVM/native/VM: **ok** (no generic-specific code; lower the concrete entity).
- broken cells inherited from Class 1: `genArrayLit`/`genManagedSliceLit` managed-element acquire (widened reachability — one generic body monomorphized across many managed Ts).

**Known divergences:**
- **CRITICAL (all):** `mangleTypeArg` collapses all array type-args to `'[...]'` → `id[[3]int]` and `id[[5]int]` cache-collide → wrong body reused (`gen_generic.bn:39-91`, `types_query.bn:92-94`).
- **CRITICAL (all):** `mangleTypeArg` collapses all func-value type-args to `'<unknown>'` → cache-collision + illegal `'<' '>'` in symbol.
- **MAJOR (all):** `instantiationMangledName` ignores the defining package → two same-named imported generics collide (`gen_generic.bn:24-33`).
- **MAJOR (all):** generic array-lit / mslice-lit managed-element acquire gaps (Class 1 via generics).
- **MINOR:** const/readonly type-arg embeds a space in the symbol.

**Suspected:** generic over a managed-aggregate-by-value returned through an instantiated-interface method (combines Class 3 ordering with lazy struct registration); generic interface multi-result methods (single-result limit, shared with non-generic).

---

## 4. IR op semantics — audit & documentation plan

**Specs live in the source, not in this document.** The canonical per-op contract is a doc comment **at the op itself** — in `pkg/binate/ir/ir.bni` for IR ops and `pkg/binate/vm.bni` for bytecode ops, plus a lowering note at each emit helper — never a parallel table in this or any external `.md`. An external spec drifts from the code the instant either side changes; an in-source comment is reviewed in the same diff as the code it describes and is the exact oracle the per-op cross-backend audit (Section 6) keys off. This document is the **methodology, plan, and index**: it specifies *what* each op's contract must capture and *where* the gaps are; the authoritative *content* lands as source comments. (This is the project's "Comments Stand Alone" rule applied to op semantics.) The template below is therefore a template for a **source comment**, not for a `.md` entry.

### State of IR-op specs

Op enum + doc comments live in `ir.bni:13-257` (`NUM_OPS` at `:256`; note `OP_CONST_FLOAT` at `:16` sits between CONST_INT and CONST_BOOL — ~68 ops total). Emit helpers in `ir.bni:525-689` and `gen_*.bn`/`ir_ops*.bn`.

**Well-to-excellently documented (operand/result + role):** the structural and lifecycle ops — `RODATA_MSLICE`/`RODATA_SLICE`/`RODATA_ARRAY`/`RODATA_MSLICE_COPY` (the RODATA family states its ownership result — `RODATA_MSLICE_COPY`: "refcount 1, independent of rodata"), `IFACE_DTOR`/`FUNC_VALUE_DTOR` (state their refcount role — "feeds RefDecDtor"), `SP_RESTORE` (exemplary: void boundary, full per-backend semantics, when gen emits it), `FUNC_HANDLE`/`CALL_HANDLE`/`IFACE_UPCAST`/`C_CALL`, and the terminators.

**Adequately documented by category header (legitimately no refcount obligation — pure scalar):** `ADD`/`SUB`/`MUL`/`DIV`/`REM`, `AND`/`OR`/`XOR`/`SHL`/`SHR`, `EQ`/`NE`/`LT`/`LE`/`GT`/`GE`, `NEG`/`NOT`/`BITNOT`, `CAST`, `BOUNDS_CHECK`, `NIL_CHECK`.

**The systemic, code-red-relevant gap is refcount/ownership.** Across the ~24 ops that touch managed values, almost NONE document the consume/retain/borrow contract — who owns the result, whether the op retains its operands, where the balancing RefDec is emitted. This is the exact mechanism behind the entire CRITICAL/MAJOR bug matrix. Because the contract is unwritten at the IR layer, each of the four targets re-derives it, and the same op (canonically EXTRACT, STORE, LOAD, RETURN, STRUCT_LIT) is lowered with refcounting in some arms and a plain word-copy in others.

**Underspecified, most urgent first:**

| Op | Gap | Why it matters |
|---|---|---|
| `EXTRACT` | own-vs-borrow of a managed component; 16-byte address-aggregate must propagate BOTH words | Center of multiple tracked bugs (iface field-extract, 2-word packing). Most consequential. |
| `STORE` | does NOT do save-copy-destroy — raw word store; emitter owns refcount | Recurring defect surface (`gen_short_var`, array-lit, raw-ptr index). |
| `LOAD` | yields a BORROWED ref — no RefInc; borrow-vs-own is the crux of the todo matrix | All four must agree LOAD doesn't RefInc. |
| `RETURN` | primary ownership-transfer site; borrowed-load-vs-owned-producer per-shape matrix; VM copy-back size | Where double-frees/UAFs lived. |
| `STRUCT_LIT` | managed-field acquire obligation for aggregate construction | composite/array-literal under-retain. |
| `CALL`/`CALL_INDIRECT`/`CALL_FUNC_VALUE`/`CALL_IFACE_METHOD` | result-ownership (caller owns fresh ref); arg MOVE-vs-copy model | iface MOVE model is target-divergent, exists only in the todo. |
| `BOX`/`MAKE`/`MAKE_SLICE` | result is fresh refcount-1 caller-owned, must be RefDec'd | ownership-critical, undocumented. |
| `IFACE_VALUE`/`FUNC_VALUE` | does construction RETAIN the boxed/captured data? | NeedsDestruction was dead code until a fix; capture-loss territory. |
| `MANAGED_TO_RAW` | result `*[]T` BORROWS, must NOT be RefDec'd, source must outlive | borrow/lifetime hazard. |
| `REFINC`/`REFDEC` | mechanics documented; the POLICY (when must gen emit it) is not | the entire bug surface is inconsistent policy application. |
| `PHI` | managed-merge through control flow: which edges own vs borrow, where the single RefDec lands | SUSPECTED (no live caller today, but contract absent). |

**Two non-refcount spec defects:** (1) `LAND`/`LOR` are headed "short-circuit" (`ir.bni:43`) but a two-operand op with pre-evaluated Args cannot short-circuit — the doc contradicts the op shape (real short-circuit is gen branches). (2) `DIV`/`REM`/`SHR` leave signedness/trap behavior — a real cross-target contract — unpinned.

### Proposed canonical per-op spec template

For each managed-touching op, add to its `ir.bni` doc:

```
OP_<NAME>
  Operands : Args[i] = <role, type/shape>; <Index/StrVal/TypeArg/Typ as used>
  Result   : <type/shape>; OWNERSHIP = { fresh-owned | borrowed-load | aliased | none }
  Refcount : OPERAND RETENTION = { retains <which> | borrows | moves }
             RESULT OWNERSHIP   = caller owns N refs; balancing RefDec emitted by <site>
             (or: N/A — pure scalar, no managed operands)
  Effects  : <side effects, panics, control-flow>
  Lowering : LLVM <...> | native-aa64 <...> | native-x64 <...> | VM <...>
             (call out any target where the shared decision differs in mechanism)
```

The ops that DO state their ownership role — `RODATA_MSLICE_COPY`, `IFACE_DTOR`/`FUNC_VALUE_DTOR`, `SP_RESTORE` — are the template the rest must follow. Minimally each managed-touching op needs a one-line clause: result ownership (fresh-owned vs borrowed-load vs aliased), operand retention (retains/borrows/moves), and which emit site is responsible for the balancing RefInc/RefDec — written once at the IR layer rather than re-derived per backend.

### Worked example of the spec format (from the OP_RETURN cross-backend trace)

The trace methodology (Section 6) produces exactly the per-op contract the spec template prescribes. For `OP_RETURN` the canonical entry reads:

> **Contract.** Ends the function, delivers `len(Args)` results. Args carry already-ownership-transferred values (Axiom-3 RefInc/save-copy-destroy/`consumeTemp` emitted by IR-gen in `gen_return.bn` BEFORE this op — backends MUST NOT add or elide refcount). Component placement for multi-return is EXACTLY `types.FieldOffset(MultiReturnType, i)` for `SizeOf(component)` bytes — the type layer is the sole authority.
>
> **Invariants:** (1) ALL bytes delivered — for aggregate-of-size S, exactly S bytes (not `(S/word)*word`); backends copying in word chunks MUST handle a trailing sub-word and MUST NOT read/write past S (catches aa64 `nWords=sz/8` truncation AND x64 `[src+8]` over-read). (2) 16-byte address-aggregate components (iface, func) deliver BOTH words in EVERY shape, symmetrically, via the SAME predicate (`isVMAddressAggregate` VM / `IsAggregateTyp` native / 16B SSA LLVM) — iface and func treated identically (catches the VM single-func-value copy-back gap). (3) No dangling into a reclaimed frame. (4) Indirection threshold = target word×2 (16 LP64 / 4 ILP32), the SAME at def and every call/trampoline/extern boundary. (5) A 64-bit scalar single return on a sub-8-byte word returns as a slot/register PAIR.

This is the format every underspecified op should reach.

---

## 5. VM bytecode op semantics — audit

Audited all ~131 `BC_*` opcodes in `vm.bni` against handlers in `vm_exec*.bn` and lowering in `lower_*.bn`. The arithmetic/comparison/unary/cast families and the entire `*64` register-pair family are well-specified and accurate. The fragile address-based-aggregate and refcount ops are where the `.bni` drifts from — or contradicts — the code.

**CONFIRMED defects (each cites file:line, grounded in the handler/lowering code):**

| BC op | `.bni` says | Code does | Severity |
|---|---|---|---|
| `BC_CALL_EXTERN` (`vm.bni:87`) | `Aux = extern index` dispatch | DEAD opcode — never emitted by any lowering, no handler; real path is `BC_CALL` + negative CallCache → `execExtern` | spec describes nonexistent path |
| `BC_IFACE_DTOR` (`vm.bni:220-229`) | `Dst = vt.Methods[0]` as a 1-based VM func index | returns `bit_cast(int, dtorFunc.Handle)` — a func-value HANDLE pointer; an in-handler comment (`vm_exec_iface.bn:75-87`) says a raw index would crash | `.bni` documents the wrong (crashing) contract |
| `BC_IFACE_VALUE` (`vm.bni:201-210`) | iv[1] = `vtable_addr`; lazy vtable alloc | stores `vtIdx+1` (1-based INDEX into `vm.IfaceVtables`, 0=nil sentinel); vtables pre-built by `LowerModule` | `vtable_addr` contradicts the load-bearing index encoding |
| `BC_EXTRACT` (`vm.bni:271`) | only `Imm*8` word-indexed | THREE Aux-keyed modes: Aux=0 byte-offset load (dominant, NOT `*8`), Aux=1 POINTER mode (16-byte address-aggregate fields — the fragile case, **undocumented**), Aux=2 legacy word-index | doc hardcodes `Imm*8`, only matching Aux=2 |
| `BC_RETURN` (`vm.bni:92`) | `return Src1 (or void if Dst==-1)` | omits the entire multi-return packing path (Dst>0 → `packMultiReturn`), the shape-code meaning of Dst, and the iface-value copy-back (Aux) | |
| `BC_CALL` (`vm.bni:86`) | `Aux = func index` | Aux is a NAME-TABLE index (`f.Names[instr.Aux]`); omits Src1=callArgBase, Imm=packed-slot-count | |
| `BC_REFDEC_INLINE_FAST` (`vm.bni:169-172`) | `Src2=dtor … dispatches the dtor iteratively` | the core managed-refcount op — collapses a FOUR-way handler (Src2==0 → rt.Free; `compiledClosureDtorMark`(-1) → COMPILED_CLOSURE struct-release; VM_CLOSURE_REC → iterative dtor-frame push; else → cross-mode shim) and mislabels the handle as a dtor; immortal sentinel skip undocumented | the recurring double-free/leak theme |
| `BC_FUNC_VALUE` (`vm.bni:187-200`) | non-capturing only, `Aux = name index` | omits the non-capturing-ClosureRec RefInc (a real refcount contract — without it the shared rec is freed by the first instance's RefDec) AND the capturing heap-COMPILED_CLOSURE leak path (Src1!=-1) | |

**Lesser drift:** `BC_CALL_IFACE_METHOD` (Aux is the ABSOLUTE vtable slot, slot 0 = dtor, embedding at nested offsets — not "method-declaration order"); `BC_FUNC_VALUE_DTOR` (undocumented -1 `compiledClosureDtorMark` sentinel; returns slot raw, no Handle wrap).

**Gaps to close:** every CONFIRMED row above needs the `.bni` doc corrected to match the handler (the handler is the source of truth in each case). The most consequential are `BC_IFACE_DTOR` (documents a crashing contract), `BC_EXTRACT` (the Aux=1 pointer-mode for 16-byte address-aggregates is undocumented — the exact fragile case), and `BC_REFDEC_INLINE_FAST` (the core refcount op's four-way dispatch is collapsed to one wrong line). A per-op spec whose own comments lie is worthless as a cross-check oracle — these are not cosmetic. All corrections land **in `vm.bni`** (and the handler comments next to the code) — in source, per the Section 4 principle, never mirrored into this document.

---

## 6. Cross-backend lowering audit (the pipeline comparison)

### Methodology

For each IR op, lay the four targets' lowerings side by side against the canonical contract (Section 4 spec template) and flag every divergence. The procedure for one op:

1. Write the **contract** (operand/result/ownership/byte-image/ABI), grounded in the type layer for layout and the IR layer for semantics.
2. For each of LLVM / native-aa64 / native-x64 / VM: locate the dispatch arm, summarize the lowering, and check it against each contract invariant.
3. Flag every cell where a target diverges from the contract or from the other three; classify confirmed-vs-suspected and severity.
4. Note the conformance gap — the test shape that *would* distinguish the divergent lowerings and is currently absent.

### Proof-of-method: four representative traces

The four traces in the findings demonstrate the method end-to-end and are the worked examples future audits replicate:

**`OP_RETURN`** — Contract pinned (all bytes; both address-aggregate words in every shape; no dangling; word×2 threshold; pair for 64-on-32). Divergences found: VM single-return copy-back covers iface but not func-value (MAJOR); non-8-multiple aggregate return drops the tail on aa64 / over-reads on x64 (MAJOR); multi-return indirection mechanism differs (minor, LP64-coincident); stale "> 64 bytes" comment (`common.bn:160-161`).

**`OP_EXTRACT`** — Contract pinned (field at `FieldOffset`, never `8*Index`; one `extractYieldsAddress(fieldTyp)` predicate driving both RETURN packing and EXTRACT mode; the packed byte image must equal the platform ABI for the equivalent LLVM struct). Divergences: natives collect ≤16-byte tuples one-field-per-reg vs per-eightbyte (MAJOR, native↔LLVM boundary); natives use `FieldOffset` only for `TYP_STRUCT`, else `8*Index` (minor latent); the pointer-mode predicate is expressed three different ways that only coincidentally agree (minor-fragile); BC_EXTRACT scalar mode reads a full word with no sub-word sign-dispatch (minor suspected).

**`OP_IFACE_VALUE` + `CALL_IFACE_METHOD` + `IFACE_DTOR`** — Contract pinned (2-word layout; word-1 encoding target-defined — address on LLVM/native, 1-based index on VM, all-zeros = universal nil; vtable concat layout, slot 0 = dtor HANDLE; Index indexed RAW). Divergences: by-value-struct-through-iface degraded to `int` (CRITICAL — **FIXED on `main` `9baa579d`**, conf 585); native-aa64 cross-package multi-return-`@Iface` miscompile (MAJOR, `526` xfailed, `49d03616`); nil-iface dispatch — VM diagnostic vs compiled silent SEGV vs baremetal no-trap (MAJOR, deliberate, pinned by 385/386); three stale doc sites (aa64 says `Index+1`, vm.bni says `vtable_addr` / "declaration order") (minor).

**`OP_FUNC_VALUE` + `CALL_FUNC_VALUE` + `FUNC_VALUE_DTOR`** — Contract pinned (16-byte `{vtable@0, data@8}` — OPPOSITE of iface, the single most error-prone fact; data null compiled / never-null VM; dtor at slot 0 must hold the closure-struct dtor handle when capturing+managed). Divergences: native vtable dtor slot hardcoded zero for capturing managed closures (MAJOR — **FIXED on `main` `45416376` + `7dab4be7`**); dtor-consumption mechanism differs (VM raw vtable[0]/-1 sentinel vs compiled handle) (minor); slot-1 semantics differ by design but undocumented (minor); native call-a-captured-`@func` double-free (Class 7 — **FIXED on `main` `fd82c0a9`**); the VM func-value single-return copy-back holdout remains (MAJOR).

### Per-op checklist template (the full audit fills this in)

| IR op | Contract written? | LLVM | native-aa64 | native-x64 | VM | Divergences (sev) | Test gap |
|---|---|---|---|---|---|---|---|
| RETURN | ✅ (§6) | ok | tail-drop (M) | n=2 cap (C), ptr-not-bytes (C), 1-field-per-reg (M) | func copy-back miss (M) | listed | non-8B struct; func-return-as-arg |
| EXTRACT | ✅ (§6) | ok | `8*Index` non-struct (m) | same | full-word scalar (m) | listed | sub-eightbyte tuple across boundary |
| STORE | ❌ | ok | ok | ok | nil-EXTRACT crash (C) | INDEX RefDec-before-RefInc (M) | nil-managed-slice; self-alias `a[i]=a[i]` |
| IFACE_VALUE/CALL/DTOR | ✅ (§6) | ok | 526 xpkg-MR-`@Iface` (M) | ok | ok | struct-thru-iface→int ✓fixed; 526 (M); dispatch-result leak (C) | iface-method managed return |
| FUNC_VALUE/CALL/DTOR | ✅ (§6) | retbuf shim miss (M) | dtor-slot ✓fixed | valOperand (M) | single-return copy-back (M) | capturing managed closure |
| CALL/CALL_IFACE_METHOD | ❌ | byval miss (M) | outgoing-args miss (M) | outgoing-args miss (M) | SP-leak (M) | many-arg iface method |
| LOAD / BOX / MAKE / MAKE_SLICE / STRUCT_LIT / PHI | ❌ | — | — | — | — | (refcount contract absent) | — |

Empty cells in the right columns are the work P3 (Section 9) fills in.

---

## 7. Test matrix

Coverage is the third leg of the thesis. Three test families, every cell run in all relevant modes.

### 7.1 The `{form × target-shape × value-type}` matrix for managed values

The dominant gap. The matrix axes:

- **assignment form:** var-decl init · single `=` · multi `a,b=f()` · short-var `:=` · multi short-var `q,n:=f()` · composite-literal field · array-literal element · managed-slice-literal element · `return` · param-entry · for-range value bind
- **target shape:** IDENT · SELECTOR · INDEX-array · INDEX-slice · INDEX-raw-ptr · blank `_`
- **value type:** `@T` · `@[]T` · `@func` (capturing & non-capturing) · `@Iface` · managed struct/array by value (incl. nested, incl. holding `@Iface`/`@func`)

For each filled cell, a **refcount-balance** test: construct a *mortal* managed value (not an immortal string literal — fresh `make`/`box`/capturing closure), flow it through the cell, then assert `rt.Refcount` returns to baseline after the enclosing scope and the value is still usable (no premature free / double-free / UAF). The non-fresh/mortal source is essential — the existing passing tests (371/366) use fresh-literal RHS with immortal strings and *coincidentally balance*, which is exactly what hides the gaps.

A mechanical **symmetry-check** unit test per cell: assert the emitted IR's RefInc/`__copy_` count at the store site equals the RefDec/`__dtor_` count the matching dtor path will run.

### 7.2 Per-situation test cells

- **Multi-return:** `(uint32,uint32,uint32,uint32)` (16B, 4 sub-word) all four survive (x64); `(uint32,uint32)` across the native↔LLVM boundary (packing disagreement); `(struct{p @T}, bool)` aggregate component (x64 ptr-not-bytes); `(@Iface,int)`/`(@func,int)` (24B > 16 → sret) bound/discarded/returned-through.
- **Blank/discard:** expression-statement and `_ =`/`_ :=` discard of a managed-returning direct/func-value/method/**interface-method** call (each managed type); `for _ in coll` / `for v in coll` over `@[]@T`/`@[]@func`/`@[]@Iface`/`[N]@T`; discard of a borrow (`_ = someManagedVar`) asserting NO RefDec.
- **Aggregates:** array-element/raw-ptr-index/array-lit/mslice-lit/composite-field of each managed kind from a *variable* (non-fresh) source; nested aggregate; native-aa64 24-byte / alignment-padded aggregate cross-checked vs aa64-via-LLVM byte-for-byte; ILP32 5..16-byte struct return.
- **Closures:** capturing `@func` capturing+invoking a managed `@func`/`@Iface` (Class 7); capturing closure with a managed-field-struct capture freed on aa64 AND x64; aggregate-returning capturing closure (incl. >16B sret on arm32); method value to an `@func` slot.
- **Interfaces:** iface-method `@Iface`-return looped over a chain with `rt.Refcount` balance (the leak `575` doesn't catch); VM SP-leak loop in `-int`/`-int-int`; `interface I { m() S }` declared before `type S` (MethodResults degradation); by-value `>16B`-with-managed-field struct through iface (sret + balance); cross-package store-only `@Iface` (2-word ABI); `present()` vs dtor on a typed-nil box; nil-interface dispatch (deterministic SEGV compiled, message VM).
- **Constants:** function-local single + group consts every scalar type (currently 0 / `undefined`); float32 const bit-exactness across all targets (un-xfail 539 once `F64BitsToF32Bits` wired); large typed const on a 32-bit host; cross-package bool/float const via `pkg.Name`.
- **Sub-word/64-on-32:** `(a+b) <u c` and `(a*b)>>n` and `cast(uint64, a*b)` with overflowing uint32 (dirty upper bits); uint8/uint16 wrap; unsigned int↔float round-trip with high bit set; int→float32 narrowing; signed narrow-int load sign-extension; int64/uint64/float64 round-trip on a **32-bit VM lane** (the missing qemu runner).
- **ABI:** iface method taking a >16-byte struct by value (LLVM byval-vs-value); iface method with 8 scalar args (PlanFrame outgoing-args overflow on AAPCS; 6 on SysV); variadic float `__c_call` (AL=vector-count on x64; stack on aa64-darwin); >8 float args (NSRN spill); all five call shapes passing the same >16-byte aggregate arg (one-classifier invariant).
- **RODATA/nil:** `var x @[]T = nil` and the four assign-nil siblings (VM EXTRACT-on-nil crash); string literal stored into a char-slice element (full header vs bare pointer); shared static `@[]uint8` literal used after first scope, in a loop, returned (immortality + VM string-table refcount).
- **Cross-package:** two same-last-segment packages (alias-vs-fullpath + `writeBnDotted` collision); free-func-`M`-in-`/Point` vs method-`M`-on-`Point` (injectivity); `var n @pkg.T = pkg.G` managed-copy (559); 3-package generic-iface split (impl-in-A / dispatch-in-B); unregistered qualified type → diagnostic not `TypInt()`.
- **Generics:** `id[[3]int]` vs `id[[5]int]` vs `id[[3]byte]` distinct (array cache-collision); `f[*func()int]` vs `f[*func()bool]` (func-value collision + illegal `<>`); two same-named imported generics (defining-pkg disambiguation); generic body building array-lit/mslice-lit of a managed T (Class-1 via generics); `slices.Append[T]` per managed-T kind.

### 7.3 Every cell runs in all relevant modes

Reference the existing conformance modes (`conformance/run.sh`): default `builder-comp`, `builder-comp-int`, `builder-comp-int-int`, `builder-comp-comp`, `builder-comp-comp-int`, `builder-comp-comp-comp`; cross-compile / alternate-backend `builder-comp_native_aa64-comp_native_aa64`, `builder-comp_arm32_baremetal`, `builder-comp_arm32_linux`. A cell that touches only the VM (SP-leak, nil-EXTRACT, 64-on-32 pair) must run in the `-int` modes; a cell that touches native packing must run the native lanes; an ABI cross-boundary cell must run a mode where the main module is native and a dep is LLVM. **Two modes are missing and must be added:** an **x64 native lane** (the x64 dtor-slot, multi-return, and value-operand bugs are all green-by-absence-of-test today) and a **32-bit-host VM runner** (qemu) so the entire `is64BitScalar`/`splitInt64`/`BC_*64` path is exercised rather than correct-by-inspection.

### 7.4 Organizing the suite — a dedicated, systematic conformance set

The matrix above is large, systematic, and *generated by a product of axes* — it must not be sprinkled into the flat `conformance/NNN_*` numbering as ad-hoc one-offs, or coverage becomes unauditable (a current failure mode: nobody can see which cells exist). The systematic suite should be its own organized body of tests with a checked-in coverage manifest. Three structural options, to be chosen **with the user** at the start of P1 (not decided unilaterally):

1. **Reserved numeric band + manifest.** Carve a documented band (e.g. `conformance/9xx_*`) for the matrix cells, with a checked-in `conformance/matrix.md` manifest mapping every `{form × target-shape × value-type}` cell and every per-situation cell to its test file and current pass/xfail status per mode. Minimal runner change; fits the existing flat scheme.
2. **Dedicated subtree.** `conformance/matrix/<situation>/<cell>.bn` (the runner already supports directory-style tests — 576 is one), giving the product structure a directory structure. Cleaner grouping; needs the runner to walk nested dirs.
3. **Table-driven generator.** A small checked-in generator emits the cell tests (and their `.expected`) from one table of axes, so "add a managed kind" or "add a call shape" regenerates the full product mechanically and completeness is structural, not best-effort. Most robust against the exact "nobody added the cell" failure this whole document targets; highest up-front cost.

Whichever structure is chosen, the suite has three fixed properties:

- **Each cell is a refcount-balance test with a *mortal* source** (fresh `make`/`box`/capturing closure, never an immortal string literal — the coincidental-balance trap of 371/366), asserting `rt.Refcount` returns to baseline after scope and the value stays usable.
- **Each cell has a paired unit symmetry-check** (the emitted IR's RefInc/`__copy_` count at the store site equals the RefDec/`__dtor_` count the matching dtor path runs) — a cheap, host-only oracle that catches a missing arm without needing the value's exact runtime shape.
- **A checked-in coverage manifest** (the Section 6 per-op checklist + the 7.1 matrix) is the source of truth for "what is tested," updated in the same diff that fills a cell. An empty manifest cell is a visible, reviewable gap — never an implicit one.

This is the testing analogue of the in-source-spec principle (Section 4): the *organization* of the suite is itself a guardrail, because the entire bug history is cells nobody knew were untested.

---

## 8. Newly surfaced suspected defects (need repro before filing)

These are **audit leads, not yet-confirmed bugs** (`confirmed=false`). Each is a candidate `claude-todo.md` entry with symptom / suspected root cause / repro mode. They must be reproduced before filing per the Bug Discovery Protocol; confirmed critical/major findings are raised to the user, not silently fixed.

1. **native-aa64 24-byte / alignment-padded aggregate in registers may corrupt the tail word.** Symptom: a 24-byte aggregate (3 words) or one whose field padding shifts the second register returned/passed in-registers reads garbage in the tail. Suspected cause: `aarch64_call.bn:88-113` hand-rolls the regWords/stack split, asserting `[2 x i64]` matches LLVM by comment, not by a pinned cell. Repro: aa64 native vs aa64-via-LLVM byte-for-byte on a 24-byte / padded aggregate.

2. **By-value struct returned through an iface method may re-degrade for any aggregate result resolved during interface collection.** Symptom: 1-word-vs-multi-word ABI mismatch, both LLVM and VM mis-size identically. Suspected cause: the `9baa579d` fix is a struct-name pre-pass, not a unified resolver, so the ordering hazard persists structurally; the same-package interleaved-collection case (`gen_iface_registry.bn:165` + `gen_util.bn:284`) is not covered by the import-only pre-pass. Repro: `interface I { m() S } ... type S struct{a,b int}` declared in that order, value-receiver impl, dispatched `iv.m()`, on LLVM + VM + native.

3. **LLVM iface-method >16-byte struct arg passed by-value vs the byval-`ptr` callee.** Symptom: silent ABI mismatch, possible memory corruption. Suspected cause: `emit_iface_call.bn:97-142` uses bare `llvmType` for aggregate args; the thunk param is `ptr byval`. Repro: an iface method taking a >16-byte struct arg, LLVM-compiled, cross-checked native.

4. **VM cross-mode func-value/iface-thunk dispatch truncates args past index 6.** Symptom: trailing args dropped at the bytecode→native shim boundary. Suspected cause: `dispatchCompiledFuncValue` and the `_call_shim` arms unpack a fixed `a0..a6` (`vm_exec_funcref.bn:298-332`). Repro: a function value / iface thunk invoked with 8+ effective args through the cross-mode shim.

5. **Cross-package generic-interface instance keyed on consumer not defining package.** Symptom: impl vtable keyed `IfacePkg=A` while the dispatching iv expects `IfacePkg=B` → vtable-name mismatch. Suspected cause: `gen_generic.bn:349` `mi.Pkg = currentModulePkgPath`. Repro: 3-package split — generic declared in `iflib`, impl in `impl`, dispatch in `main`.

6. **Generic over a managed-aggregate returned through an instantiated-interface method.** Symptom: instantiated-struct result resolved before registration → degraded result type. Suspected cause: `ensureInstantiatedInterface` resolves method results possibly before the struct is in `moduleStructs` (`gen_generic.bn:358-363`); the cross-pkg/iface ordering fixes targeted the non-generic arm. Repro: `interface Box[T]{ get() T }` with a managed-struct T, dispatched via `@Box[ManagedStruct]`.

7. **Large typed const direct-read truncates on a 32-bit host.** Symptom: `const X int64 = <value > 2^31>` read directly (not via a checker-folded binop) truncates. Suspected cause: `ModuleConst.Val` is host `int` (`gen.bn:40`); read sites don't consult the checker bignum. Confirmed-unreachable on 64-bit host. Repro: a bnc binary running 32-bit, or a unit test on `evalConstExpr`/`ModuleConst.Val` width.

8. **Anon-tuple sub-word-before-pointer field mis-GEP on native/VM extract.** Symptom: GEP overshoot for `{bool,@T}` / `{uint32,int64}` anon multi-return. Suspected cause: `aarch64_emit.bn:285` / `x64_emit.bn:34-63` keep `8*Index` for non-`TYP_STRUCT` carriers. Repro: a padded anon multi-return on native + VM.

9. **VM `STORE` of a managed func-value into a struct FIELD where the func-value reg is a non-fresh borrow.** Symptom: address-vs-value reg confusion. Suspected cause: `OP_STORE` of a 16-byte aggregate relies on the value reg holding the slot address; unverified for the field-store path with a borrowed (not alloca-slot) reg. Repro: a targeted VM run storing a borrowed `@func` into a struct field.

10. ~~**Native call-a-captured-`@func` double-free has an additional trampoline over-release beyond the `emitCaptureRefInc` omission.**~~ **RESOLVED on `main`:** the `emitCaptureRefInc` `@func` capture-site RefInc landed (`fd82c0a9` / `388c48d3`) and the VM-free repl poll (`e3dc0d07`) closed the wrapPoll thread — no separate trampoline over-release remained. (Retained as a record, not an open lead.)

11. **PlanFrame sret-shift + saturated-GP + float-args interaction untested** (`common_call.bn:60-75` models sret-in-gp-arg-reg but not NSRN). 12. **VM `lookupGlobalAddr` 0-on-miss silent null-deref** (`lower_data.bn:111-117`). 13. **`OP_PHI` managed-merge contract absent** (no live caller, but if phi-lowering is introduced LLVM/native silently drop it).

**P0 verification deltas (2026-06-04; landed `070f9e84`/`0acdafa5`).** Authoring the in-source op specs (P0) re-verified every op against current `main` and surfaced these additional deviations — the spec states the correct contract, these are the lowerings to conform:

14. **Divide-by-zero / mod-by-zero is unspecified across targets — needs a ratified language contract.** `OP_DIV`/`OP_REM` emit raw division on every backend: LLVM `sdiv`/`udiv` (`/0` is **UB** at the LLVM level, not a guaranteed trap), native `IDIV`/`SDIV` (hardware SIGFPE — traps only by accident), VM host `/`/`%` (`vm_exec_pure.bn`). There is no `OP_DIV_CHECK`. Whether the intended semantics is a defined panic (like bounds-check) or target-dependent is a **user decision** — tracked as a DISCUSS in `claude-todo.md`; `ir.bni` documents it as UNSPECIFIED until ratified.
15. **LLVM `emitExtract` resolves the real component type only for `OP_CALL` results** (`emit_helpers.bn:299-304`); for `OP_CALL_FUNC_VALUE` / `OP_CALL_IFACE_METHOD` / `OP_CALL_INDIRECT` results it falls to `llvmType(Args[0].Typ)`, which can mis-type a managed / 16-byte-address-aggregate component (MAJOR suspected; sharpens the §3.2 "retTypes gated on OP_CALL only" note from the EXTRACT side).
16. **`isFreshManagedFuncValue` / `isFreshManagedIfaceValue` omit the call ops** (`gen_refcount_pred.bn:152-161` / `:170-179`): a managed `@func`/`@Iface` returned by `CALL_FUNC_VALUE`/`CALL_HANDLE`/`CALL_INDIRECT`/`CALL_IFACE_METHOD` is mis-classified non-fresh → an extra RefInc at the copy-site; AND the `@func` call result is never `registerTemp`'d (`gen_call.bn:268-288`, `gen_method.bn`) → a discarded `@func`-returning call LEAKS (MAJOR — the precise sites behind the open "`@func` call-result registration" item).
17. **aa64/x64 `OP_EXTRACT` use `FieldOffset` only for `TYP_STRUCT` carriers**, else `8*Index` (`aarch64_emit.bn:285`, `x64_emit.bn:38`) — latent mis-GEP for a sub-word-before-pointer field on a non-struct carrier (sharpens suspected #8 with the carrier-kind condition).
18. **VM `OP_DEREF` always lowers to `BC_LOAD64`** (one 8-byte word; `lower_instr.bn:235-238`) — no aggregate / 64-on-32-pair / sub-word path; correct for today's word-sized-pointee callers, latent under-copy otherwise.
19. **`isFreshManagedSlice` omits `OP_RODATA_MSLICE_COPY`** (`gen_refcount_pred.bn:137-145`): on the generic copy path the fresh-owned `@[]T` copy would be RefInc'd (leak) instead of moved; not currently reached (produced only at store sites that consume it directly), but a latent trap if a new caller routes it through the generic path.
20. **`BC_EXTRACT` Aux=1 pointer-mode predicate is re-spelled per pass** (`isMultiWordField || isVMAddressAggregate` in `lower_instr.bn` vs `packMultiReturn`) — they only coincidentally agree; editing one and not the other desyncs packing from extraction. (Candidate for the P2 single-classifier collapse.)

---

## 9. Execution plan & sequencing

Phased. Earlier phases produce the artifacts later phases consume. The ordering rationale is uniform: **write the contract before building the comparison; build the comparison before mass-fixing; centralize the discipline before it can drift again.** Confirmed critical/major defects are raised to the user for prioritization at the moment of confirmation — not silently fixed in-phase.

### P0 — Write the contracts/specs (no behavior change)

**Goal.** Make the implicit contracts explicit so all four targets are checked against one written spec.

**Deliverables:**
- **All op specs land in source** — `ir.bni` / `vm.bni` / emit-helper comments, never in this or any external `.md` (Section 4 principle) — so each spec is reviewed in the same diff as its code and cannot drift. This document points at them; it does not hold them.
- Per-op refcount/ownership clause in `ir.bni` for the ~24 managed-touching ops (Section 4 template), starting with the urgent list (EXTRACT, STORE, LOAD, RETURN, STRUCT_LIT, CALL family, BOX/MAKE/MAKE_SLICE, IFACE_VALUE/FUNC_VALUE, MANAGED_TO_RAW, REFINC/REFDEC).
- Fix the `LAND`/`LOR` "short-circuit" contradiction and pin `DIV`/`REM`/`SHR` signedness/trap behavior in the doc.
- Correct the eight CONFIRMED `vm.bni` doc defects (Section 5) to match the handlers — `BC_IFACE_DTOR`, `BC_EXTRACT` Aux-modes, `BC_IFACE_VALUE`, `BC_RETURN`, `BC_CALL`, `BC_REFDEC_INLINE_FAST`, `BC_FUNC_VALUE`, and the dead `BC_CALL_EXTERN`.
- Fix the three stale per-op doc sites in the iface trace (`aarch64_iface.bn:17-19`, `vm.bni:202/214/216`) and the stale `> 64 bytes` comment (`common.bn:160-161`).
- The four worked cross-backend traces (Section 6) become the canonical per-op spec entries for RETURN / EXTRACT / IFACE / FUNC_VALUE.

**Ordering rationale:** docs are load-bearing for P1/P3 (a wrong spec comment lets a real regression hide behind "matches the spec"). Zero behavior change → trivially green.

**Status — DONE (2026-06-04, landed `070f9e84` + `0acdafa5`):** `vm.bni` 8 bytecode-op corrections; `ir.bni` per-op refcount/ownership contracts for the managed-touching ops (the four traced ops — RETURN/EXTRACT/IFACE/FUNC_VALUE — get the richer contract) plus the LAND/LOR-short-circuit, DIV/REM/SHR-signedness, and `CALL_IFACE_METHOD`-`Index`-is-the-absolute-concat-vtable-slot doc-vs-code fixes (the last verified against `gen_iface.bn:99-106`). The 5-agent verification surfaced §8 items 14–20. **Remaining P0 tail:** the stale per-op doc sites in the native iface emitters (`aarch64_iface.bn` "Index+1") + the `vm.bni` `BC_CALL_IFACE_METHOD` "declaration order" comment + the stale `> 64 bytes` comment (`common.bn:160-161`) are not yet corrected.

### P1 — Build the test matrix & confirm/deny the suspected defects

**Goal.** Defeat the LP64/test-shape coincidence; turn audit leads into confirmed-or-denied.

**Deliverables:**
- **Choose the systematic-suite structure** (Section 7.4 — reserved band / dedicated subtree / table-driven generator) **with the user** before authoring cells, and stand up the checked-in coverage manifest. The organization is decided once, up front, so the suite is auditable from the first cell.
- The `{form × target-shape × value-type}` refcount-balance matrix (Section 7.1) with *mortal* sources, as conformance + unit symmetry-check tests, authored into that structure.
- The per-situation cells (Section 7.2), each xfailed where it currently fails with a one-line description per the Bug Discovery Protocol.
- The two missing modes: an **x64 native lane** and a **32-bit-host VM (qemu) runner**.
- Repros for the 13 suspected defects (Section 8); each resolves to a tracked todo (confirmed bug + xfail) or a documented non-issue.

**Ordering rationale:** the tests must exist before the fixes, so a fix is provably validated and a regression is caught. Adding a failing-but-xfailed test is green-preserving. Confirmed CRITICAL/MAJOR defects surfaced here (e.g. the x64 multi-return drop, the dirty-upper-bits class, the iface-method-dispatch leak, the nil-EXTRACT VM crash) are **raised to the user** for prioritization before any fix lands.

### P2 — Centralize the refcount discipline so arms can't drift

**Goal.** Make the dominant class (Class 1) structurally impossible to reintroduce.

**Deliverables:**
- A single `emitStoreManagedSlot(ctx, b, slotPtr, val, slotTyp, isInit)` encapsulating the full Axiom-5 sequence (release-old unless init; acquire-new with isFresh/consumeTemp; store), and a single "register-managed-call-result" helper that enumerates every managed-result-producing call op (CALL, CALL_FUNC_VALUE, CALL_IFACE_METHOD, methods).
- Route every copy-site through these: var-decl, all assignment forms (single/multi `=`, single/multi `:=`), composite/array/managed-slice literal elements, return, param-entry, for-range value, raw-ptr index. Make the four managed kinds (and aggregates) a one-line addition in the dispatcher, not a per-arm switch.
- Collapse the parallel predicates (`isFreshManaged*`, `isVMAddressAggregate` vs `IsAggregateTyp` vs the natives' two-way split) toward single shared classifiers so a list-edit can't desync a backend.
- This phase *fixes* the **remaining** Class-1 cells (for-range value bind, the array/slice INDEX RefDec-before-RefInc ordering, the `@func` call-result registration, the iface-method-dispatch result registration) by construction — and *folds in* the cells already fixed individually on `main` since the audit (short-var multi-bind, composite `@func` field, array/mslice-lit, raw-ptr index) so they route through the one dispatcher and cannot re-diverge — but only with user authorization per the raise-don't-work-around rule, and only after P1's tests pin them.

**Ordering rationale:** centralization is the actual fix the codebase needs (Section 0 thesis), not a quick win. Doing it after P1 means the matrix tests validate every cell at once; doing it before P3 means the per-op audit measures the centralized state, not a moving target. Each routed copy-site is a small, independently-stageable, green-preserving commit.

### P3 — The full per-op cross-backend audit

**Goal.** Fill in the Section 6 checklist for every IR op, not just the four traced.

**Deliverables:**
- For each op: the contract written (P0), the four lowerings compared, divergences flagged with severity and a pinning test (P1 format). The empty cells in the Section 6 table (STORE, CALL family, LOAD/BOX/MAKE/STRUCT_LIT/PHI) get filled.
- Resolve the ABI-classifier divergences (Class 4): one shared classifier consulted at def + all six call-site lowerings (the iface-method/handle byval gaps, the x64 multi-return packing, the variadic-float arms, NSRN exhaustion); make `isByvalParam` target-parameterized.
- Resolve the sub-word/signedness divergences (Class 5): decide (user call) whether sub-word narrowing is IR-gen's job (one explicit narrowing cast) or each non-LLVM backend's (a shared classifier at width-sensitive consumers); wire `F64BitsToF32Bits`; fix unsigned int↔float.

**Ordering rationale:** the per-op audit is broad and exposes the ABI/sub-word classes that aren't pure refcount. It comes after P2 because a centralized refcount layer removes Class-1 noise from each op's comparison. Several P3 items are user-owned design calls (narrowing placement, native-vs-LLVM small-tuple packing convergence, whether to reject unsigned-float / array-type-args until the mangle is lossless) and must be surfaced, not decided unilaterally.

### P4 — Close confirmed divergences

**Goal.** Land the fixes for every confirmed divergence, smallest-self-contained-commit-cherry-picked-to-main first.

**Deliverables (each gated on user authorization and a P1 pinning test):** the x64 multi-return drop + packing; the **native-aa64 cross-package multi-return-`@Iface` miscompile (526)**; the VM func-value single-return copy-back; the aggregate-returning capturing-closure shim; the PlanFrame iface-method outgoing-args; the cross-package mangle injectivity + the four-way `implVtableName` collapse into `pkg/binate/mangle`; the generic array/func-value mangle losslessness; the nil-EXTRACT VM crash (root-cause: lower `CONST_NIL` of an aggregate type to a zeroed slot+address, matching native `emitConstNil`); the loader int-int `rt`-not-found (Class 8); the interface-method-dispatch managed-result leak (Class 6). *(The native dtor-slot zero and the cross-package managed-copy dtor reachability (559/561) have since landed on `main` — see reconciliation.)*

**Ordering rationale:** fixes land last because each needs its contract (P0), its test (P1), and a stable centralized base (P2). Each is structured as a self-contained green commit cherry-picked to main promptly, never a stack of unmerged commits.

**Keeping everything green / avoiding regression (all phases):** P0 is doc-only. P1 adds tests that are xfailed where they fail (green-preserving, Bug Discovery Protocol). P2/P4 land one routed copy-site / one divergence per commit, each validated by P1's matrix, cherry-picked to main and resynced before the next — never an autopilot loop, each round freshly authorized. **Confirmed critical/major defects are raised to the user with severity, symptom, discovery, and proposed fix — and the user decides "fix now / workaround / stop" — they are never silently worked around.**

---

## 10. Maintenance: keeping it from recurring

The contract docs + matrix prevent regression only if the discipline lives in **one place** and every new op/feature pays the same tax before landing.

1. **Every new IR op ships with its spec entry.** A managed-touching op without the Section 4 ownership clause does not land. The clause is the cross-check oracle the per-op audit (Section 6) keys off — an op whose own doc is silent forces each backend to re-derive, which is the root cause this whole document exists to remove.

2. **Every new VM op ships with a `vm.bni` doc that matches its handler.** The eight CONFIRMED `.bni`-vs-code disagreements (Section 5) are the failure mode: a spec comment that lies lets a real index/encoding regression hide behind "matches the spec." The handler is the source of truth; the doc must track it.

3. **Every new copy-site / value-type / call-shape adds its matrix cells before landing.** A new assignment form, a new managed kind, or a new call op must add its `{form × target-shape × value-type}` refcount-balance cells (with mortal sources) and its per-op checklist row — *before* the feature lands, not after. The matrix is the regression net; an unexercised cell is a latent double-free by construction (the entire bug history is cells nobody tested).

4. **The discipline lives in one dispatcher.** After P2, adding a managed kind is a one-line change in `emitStoreManagedSlot` / the shared classifiers, propagating to all sites at once. The maintenance rule is: **never re-hand-write a copy-site's acquire/release arms** — route through the dispatcher. A reviewer seeing a bare `EmitStore` of a managed-typed value, or a hand-rolled four-way `isManaged*` switch at a new site, must reject it: the symmetry between acquire-sites and the `__copy_`/`__dtor_` generators is what guarantees no over-release or leak, and per-arm authorship is what repeatedly broke it.

5. **The ABI classifier and the mangle are single shared functions.** `isCallOp`/`callDispatchArgTypesAnyOp` (one place enumerating every call shape), one `needsSret`-family target-parameterized threshold consulted by def + every call-site, and one `pkg/binate/mangle` function producing every symbol (no four-way `implVtableName` copies). Adding a call shape, a backend, or a type-arg kind is a single-point change.

6. **Two CI lanes stay alive.** The x64 native lane and the 32-bit-host VM lane (P1) defeat the LP64/test-shape coincidence permanently. Without them, the x64 packing/dtor/value-operand bugs and the entire 64-on-32 register-pair path go green-by-absence-of-test — the worst kind of green.

The cost of paying this tax per change is small (one spec entry, a handful of matrix cells, one routed call). The cost of skipping it compounds: every later piece of work assumes the cell is exercised and the contract is written, and the eventual root-cause fix has to undo all of them. That asymmetry is why the contract + matrix + single-dispatcher must be maintained as a hard landing requirement, not a best-effort aspiration.