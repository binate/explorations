# Binate TODO — Done

Items moved from [claude-todo.md](claude-todo.md) once fully complete. Active work lives there.

Some older entries reference design/plan docs that have since been archived (see
[historical-notes.md](historical-notes.md)) or removed outright; those filenames may
no longer resolve in the tree, though git history retains them.

---

## Done

### `&` of a non-addressable operand is not diagnosed — spec Ch.13 (2026-06-12) — ✅ FULLY LANDED (general `isAddressable` gate, binate `7f8d0b9c`)

`checkUnaryExpr`'s `&` arm grew a series of rejections for non-addressable
operands (each found by self-review of the prior one):
- **Literals** (binate `807c8ff0`): `isLiteralExprKind` rejects `&5` / `&3.14`
  / `&true` / `&'a'` / `&"s"` / `&nil` — "cannot take the address of a literal".
  `conformance/748_addr_of_literal_rejected`.
- **Func literals** (binate `3964ca24`): `&func(){}` added to `isLiteralExprKind`
  (matches Go; it produced a no-storage func value). Also in `748`.
- **Named functions** (binate `05b6bd5c`): `&g` / `&pkg.f` (SYM_FUNC in the IDENT
  + SELECTOR arms, `errCannotAddrFunc`) — `&g` previously yielded a malformed
  `*func(...)` type that every use rejected with "cannot assign … to <unknown>".
  `conformance/751_err_addr_func` (test-local `pkg/x` fixture).
- **Bound method values** (binate `f6982a7e`): `&obj.m` (`selectorIsMethodValue`,
  distinguishing it from an addressable func-typed FIELD `&obj.f` — fields win;
  reads the receiver's cached type so capture analysis isn't re-run). `751`.
- Spec: `claude-notes.md` "Pointer syntax" — `&` addressability rule.

✅ **General fix LANDED (binate `7f8d0b9c`)**: replaced the kind-by-kind
whitelist with a single `isAddressable(operand)` gate in the `&` arm —
addressable = variable / struct field / imported variable / index / `*p` deref
/ composite literal; everything else (call result, method expression `&T.m`,
arithmetic `&(a+b)`, slice/make/cast result) is rejected. `reportCannotAddr`
keeps the specific literal/const/function/method-value messages and gives a
generic "cannot take the address of a non-addressable value" for the rest.
`conformance/756_err_addr_rvalue` (rvalue rejects) + `755_addr_lvalue_ok`
(lvalues write through). Surfaced the orthogonal `&(*p)` IR-gen defect (its own
MAJOR entry at the top). (Original investigation below.)

MINOR (missing diagnostic). Found + verified firsthand re-reviewing spec Ch.13.
`checkUnaryExpr`'s `&` branch (`check_expr.bn:300-321`) rejects address-of only
for an `EXPR_IDENT` or `EXPR_SELECTOR` resolving to `SYM_CONST` (a *named*
constant); a bare literal operand (`&5`, `EXPR_INT_LIT`) matches neither gate and
falls through to `return MakePointerType(xt)`, so `&5` type-checks clean with no
diagnostic (IR-gen behavior for the resulting non-addressable operand is
unverified — likely a downstream error). A literal has no storage, so it should
be rejected like a named constant. Fix: also reject `&` of a literal operand.
`expr.unary.addr-literal` in `13-expressions.md`. No test.

### `impl` declared in a `.bni` is NEVER registered → cross-package interface impls dead — ✅ RESOLVED 2026-06-12 (`3d147369`)
- **✅ RESOLVED 2026-06-12 (`3d147369`).** Root cause was BROADER than the original diagnosis below (which framed it as a checker-only gap in the `.bni` loader `LoadPackageInterface`): the type checker (`CheckPackage`), IR-gen's vtable generation (`GeneratePackage` → `m.Impls`), AND the importer's imported-impl collection (`collectPkgFile` returns the package's MERGED file) ALL read the loader's merged `.bni`+`.bn` file — and the merge filter (`loader.bn`) prepended `.bni` type/const/group/interface/func decls but NOT `DECL_IMPL`.  So a `.bni`-only impl was invisible to assignability, to vtable generation, and to cross-package use.  The checker-only fix was implemented first and REJECTED: it made `var w @io.Writer = b` type-check but the package then failed CODEGEN (`extractvalue operand must be aggregate type`) because IR-gen still had no vtable.  **Fix = fold `DECL_IMPL` into the loader's `.bni`→`.bn` merge filter** — one change feeding all three consumers through the channel they already read.  Moved `strings.Builder`'s `impl *Builder : io.Writer, io.ByteWriter` from `strings.bn` (the workaround) back to `strings.bni`; `os.File`'s `.bni` impl is unblocked by the same change.  Both real `.bni` impls are `.bni`-only (verified `os.bn`/`strings.bn` declare no impl), so each registers exactly once.  `conformance/726_cross_pkg_iface_impl` (a different package uses `@strings.Builder` through `@io.Writer` + `@io.ByteWriter`) green on LLVM/VM/native-aa64/native-x64-darwin; strings' `TestBuilderSatisfies{Writer,ByteWriter}` pass with the impl in the `.bni`; full unit 43/0 + full builder-comp conformance 1388/0 (no regressions).
- **Post-landing review (adversarial multi-agent, 14 findings) — no correctness bugs; one coverage gap closed (`e3e6b425`).** The merge change was confirmed sound (impl-source axis `.bni`-vs-`.bn` and iface-shape axis raw-vs-managed are orthogonal in the lowering — `findImplVtableName`/`m.ImportedImpls` are shared). One real gap: `726` covered only MANAGED iface values; the RAW-iface quadrant over a `.bni`-folded impl was untested (`376`/`377` pair raw with a `.bn` impl). Extended `726` to also box a raw `*strings.Builder` into a raw `*io.Writer` — green on all four lanes. Judged mechanism-covered (not separately tested): `os.File`'s `.bni` impl (same merge+vtable path; a dedicated cell needs heavy file I/O), and a latent (non-active) VM note that cross-package vtables aren't sourced from `m.ImportedImpls` (726 passes on the VM lane, raw + managed). Cosmetic: commit `3d147369`'s message says `723` but the test is `726` (renumbered during the landing rebase; tree/whitelist correct).
- **Symptom (historical)**: an `impl *T : Iface` declared in a `.bni` interface file type-checks and the package compiles, but the type CANNOT be assigned to the interface — `var w @Iface = t` (and `*Iface`) fails with `cannot assign @T to @Iface`. Moving the SAME impl into the `.bn` implementation file fixes it. Found building `pkg/std/strings.Builder`: `impl *Builder : io.Writer, io.ByteWriter` in `strings.bni` → `var w @io.Writer = b` failed; moving the impl to `strings.bn` made all 8 unit tests (incl. interface dispatch) pass.
- **Root cause (CONFIRMED)**: the `.bni` loader `bni_scope.bn` (reached via `LoadPackageInterface`, `pkg/binate/types/checker.bn:101`) registers `DECL_TYPE` / `DECL_INTERFACE` / `DECL_FUNC` from a `.bni` into the package scope but has NO `DECL_IMPL` handling — it never calls `collectImplDecl`. So a `.bni`-declared impl is parsed but never added to `c.Impls`, the registry `types_assignable.bn` (`:242`, `:291`) scans for interface-value assignability. The `.bn` path works because `check_decl.bn:177` routes `DECL_IMPL` → `collectImplDecl` (`check_impl.bn:19`) → `c.Impls`.
- **Severity — CRITICAL**: the `.bni` is the cross-package contract; a consumer in another package sees ONLY the `.bni`. For a public type to be usable as an interface by OTHER packages, the impl MUST register from the `.bni`. Because `.bni` impls never register, **no public type can be used as an interface across package boundaries** — the explicit-interface feature is non-functional cross-package. Concretely, `ifaces/stdlib/pkg/std/os.bni`'s `impl *File : io.Reader, io.Writer, io.Closer, io.ReaderAt, io.WriterAt` is dead: no other package can pass a `@File` where an `@io.Reader`/`@io.Writer` is wanted. LATENT only because nothing consumes the io interfaces as values yet — `io.Reader/Writer/Closer/...` are declared + implemented but never used as a value or parameter anywhere (`strings.Builder` is the first would-be consumer; it works only because its test is same-package AND the impl was moved into the `.bn`).
- **Proposed fix**: have the `.bni` loader (`bni_scope.bn` / `LoadPackageInterface`) route `DECL_IMPL` declarations through `collectImplDecl` (same registration the `.bn` path uses), adding them to `c.Impls` — so a `.bni` impl is visible for interface-value assignment both same-package and cross-package. Then move `impl *Builder : io.Writer, io.ByteWriter` back from `strings.bn` to `strings.bni`. Watch for double-registration if a package's own `.bni` impl is collected while compiling its `.bn` (dedup, or only collect `.bni` impls for IMPORTED packages).
- **Workaround in place**: `pkg/std/strings.Builder`'s impl lives in `strings.bn`, not `strings.bni`, so it works SAME-package; cross-package interface use of Builder stays blocked until this lands. Builder's primary use is direct (Write/WriteByte/String), so it is useful meanwhile.
- **Test/repro + coverage gap**: move `impl *Builder` from `strings.bn` back to `strings.bni` → `pkg/std/strings` unit tests `TestBuilderSatisfiesWriter` / `...ByteWriter` fail (`cannot assign @Builder to @Writer`). A CROSS-package conformance cell — package A imports strings (or os) and assigns `@Builder`/`@File` to a `@io.Writer` — is still wanted; none exercises an io interface as a value today, which is exactly why this stayed latent.
- **Discovery**: 2026-06-11, building `pkg/std/strings.Builder` (the eventual stdlib replacement for `pkg/binate/buf`'s CharBuf).

### Iface-value upcast to an unrelated zero-method interface ABORTS the compile (R2-3 negative-offset panic false-fires on valid code) — LLVM + native aa64/x64 — REGRESSION from `ca155319` — ✅ RESOLVED 2026-06-10 (binate `4ac123da`)
- **✅ RESOLVED 2026-06-10 (binate `4ac123da`).** Fixed at the ROOT (the checker duck-typing hole), per the user's choice of the secondary fork **(B)**. The four assignability arms now gate universal satisfiability on a new checker `isUniverseAny` (mirrors IR-gen's predicate) instead of `len(Methods)==0`, and managed→raw same-interface decay rides an explicit `sameInterface` check so `@Iface -> *Iface` works for EVERY interface (not just empty by accident). Now `*Speaker -> *Empty` / `*T -> *Empty` (no impl) are rejected; `*any`/`@any` and real upcasts (incl. to an empty PARENT via extends) unchanged; the R2-3 panic is now unreachable on valid code (kept as defense-in-depth); R2-3's same-canonical→0 stays and is now correctly exercised for non-empty decay. conformance/685 extended to non-empty decay + conformance/689 nominal-rejection guard (both green across builder-comp / -int / -comp / native aa64 / native x64-darwin; full builder-comp suite 1318/0); unit tests in `check_iface_empty_marker_test.bn`. Fork (B) chosen over (A) because decay should mirror `@T -> *T`.
- **Symptom**: `var e *Empty = s` where `s` is `*Speaker` and `Empty` is a user-declared ZERO-method interface (unrelated to Speaker) — accepted by the checker — aborts the gen1/gen2 compile with **exit 1 and no diagnostic** (OP_PANIC discards its message). Managed variant (`@Speaker -> @Empty`) identical. **A/B proof**: BUILDER bnc-0.0.7 (pre-R2-3) compiles the same program through codegen, emitting a harmless `getelementptr inbounds i8*, i8** %vt, i64 -1` (harmless because `Empty` has no dispatchable methods, so the −1-offset vtable pointer is never dereferenced); gen1/gen2 (post-R2-3) emits NO `.ll` and aborts.
- **Root cause (two layers)**: (1) PRE-EXISTING checker hole — `canAssignToInterfaceValue` / `canAssignToManagedInterfaceValue` (`pkg/binate/types/types_assignable.bn:185` / `:234`) short-circuit `if len(iface.Methods) == 0 { return true }`, accepting an iface-value upcast to ANY zero-method target, not just `any`/same/ancestor. For such an upcast `IfaceParentSlotOffset` (`pkg/binate/ir/gen_iface_extends.bn:145`) returns −1 (target is not `any`, not same-canonical, not a parent). (2) REGRESSION — `ca155319` added `if offset < 0 { panic(...) }` to all three offset-based upcast lowerings (`emit_iface_upcast.bn:38`, `aarch64_dispatch.bn`, `x64_dispatch.bn`) on the FALSE premise (stated in the comment) that "the checker should never produce a negative offset." It does. R2-3 turned a latent-but-running path into a hard compile abort.
- **VM divergence (X2b, separate/pre-existing)**: the VM (`vm_exec_iface.bn`) doesn't use IfaceParentSlotOffset; it looks up a `(T, target)` vtable by name (`findIfaceVtable`), never registered → runtime abort `vm: iface_upcast: target vtable not found`. Its only zero-method shortcut matches literal `any`, not a user empty interface. So the SAME accepted upcast now has THREE behaviors: pre-R2-3 LLVM/native = works; post-R2-3 LLVM/native = compile abort; VM = runtime abort.
- **Severity**: CRITICAL — a newly-added assert aborts the compile of previously-accepted code on all offset-based backends; the exact "panic false-fires on valid code" class this review exists to catch. (Loud abort, not silent miscompile; gated on the checker hole + an unusual shape, so the 140-cell iface suite stayed green, and R2-3's own 685 covers only the empty-interface decay.)
- **ROOT CAUSE is a DUCK-TYPING checker hole (confirmed 2026-06-09 with the user — Binate is nominal, no structural typing).** Design docs are unambiguous: `any` is THE single built-in/universe universal interface (`claude-notes.md:575` "a small, closed, language-defined set… `any` is the primary one"; `plan-interface-syntax-revision.md §6`); a USER-declared `interface Empty {}` is a NOMINAL marker interface requiring an explicit `impl`. The four `len(iface.Methods)==0 { return true }` sites (`types_assignable.bn:185/194/234/240`) are a too-broad proxy for "is `any`". IR already has the correct predicate `isUniverseAny()` (`gen_iface.bn:446`: `Kind==TYP_INTERFACE && len(Pkg)==0 && Name=="any"`). The hole is SYSTEMATIC, not upcast-only: a CONCRETE `*T -> *Empty` with NO `impl *T : Empty` ALSO compiles today (runtime-verified). Correct fix core = gate those 4 sites on a checker `isUniverseAny` instead of `len(Methods)==0`; then `*Speaker -> *Empty` and `*T -> *Empty` are rejected, `*any`/`@any` still work, and the −1/panic path is unreachable on valid code (panic stays as defense-in-depth). The earlier "(B) make any zero-method target universal" idea is REFUTED by the docs — do not do it.
- **SECONDARY DESIGN FORK this surfaces (USER-OWNED) — managed→raw iface-value decay.** Tightening also rejects `@E -> *E` (the empty decay conformance 685 exercises). Turns out `@Iface -> *Iface` decay is ALREADY rejected for NON-empty interfaces (`@Speaker -> *Speaker` → "cannot assign @Speaker to *Speaker", runtime-verified); the empty case only ever worked via this same hole, so 685 tests buggy behavior. Decide: **(A)** decay stays unsupported for all interfaces — rewrite/drop 685, and R2-3's same-canonical→0 machinery (`gen_iface_extends.bn:160-165`) becomes dead → remove; minimal + consistent. **(B)** make `@Iface -> *Iface` decay a real supported op for all interfaces (mirroring `@T -> *T` at `types_assignable.bn:77`) via a reflexive same-interface acceptance — keep+extend 685 to non-empty; R2-3's same-canonical→0 stays. (`isDescendantInterface` is NOT reflexive today — `types_assignable.bn:259`.)
- **All four upcast consumers** (LLVM/aa64/x64/VM) auto-resolve once the checker rejects the bad upcast (IR/codegen/VM never see it). Add reject cells for both concrete `*T -> *Empty` (no impl) and iface-value `*Speaker -> *Empty` (raw + managed).
- **Test (to add)**: `conformance/NNN_err_iface_assign_unrelated_empty` (`.error`) covering concrete + iface-value sources; plus the 685 decision (A: drop/rewrite, B: extend to non-empty) per the fork.
- **Discovery**: 2026-06-09 CR-2-batch adversarial review (X2 finder); runtime A/B confirmed; root-cause + fork confirmed with the user.


### [CR-2 Plan-1 review] `@readonly Box` / `*readonly Box` field read → literal 0 (and `&field` → SIGSEGV) — ✅ RESOLVED (landed binate `b4d5b37b` + `73bd9081`, 2026-06-09)
- **Symptom**: reading a field through a pointer whose POINTEE is wrapped (`@readonly Box`, `*readonly Box`, and nested fields of that type) compiles clean and reads literal `0`; taking the address `&p.v` lowers to a const-0 pointer then dereferences → exit 139 (SIGSEGV). Probe: `var p @readonly Box = mk(); println(p.v)` → `0` (expected 55).
- **Pre-existing (verified)**: built a compiler at fa265629 (parent of Defect 1) — same `0`. Defect 1 (`27c1ee8b`) fixed the OUTER wrapper (`readonly @Box`) and left the inner-pointee family untouched; it is NOT a regression introduced by the fix.
- **Root cause**: `isManagedPtrToStruct`/`isRawPtrToStruct` now peel and answer TRUE, but the ~19 value-extraction sites in `gen_selector.bn` (genSelector/genSelectorPtr: lines ~31,47,77,90,108,120,151,164,193,228,239,323,335,363,375,390,400,426,438) still read the UN-peeled `t.Elem`, whose `.Name` is "" → `lookupStructIdx == -1` → const-0 fallback.
- **Severity**: CRITICAL — silent miscompile (wrong value) + SIGSEGV on the lvalue form, on valid documented code. **Owner: Plan-1 (`pkg/binate/ir/gen_selector.bn`).** Fix: peel the pointee (`peelTransparent(varTyp.Elem)`) at each extraction site, mirroring `gen_access.bn`'s indexing path. Add conformance + IR-gen coverage for `@readonly Box`/`*readonly Box` read AND `&field` (assert GET_FIELD_PTR, not const-0). The green suite (conformance 660, `TestGenReadonlyManagedPtrFieldRead`) only exercises the OUTER wrapper — the inner-pointee family is uncovered → false confidence.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed; pre-existence confirmed via pre-fix build).

### [CR-2 Plan-1 review] concrete value cannot be iface-wrapped into a `readonly @Iface` / `readonly *Iface` target (reject-only) — ✅ RESOLVED (landed binate `5d9cdeb1`, 2026-06-09; NOT reject-only — needed a companion IR-gen boxing peel in coerceExprToType, else compile→SIGSEGV)
- **Symptom**: `var rr readonly @Getter = im` (concrete `@Impl`) → `cannot assign @Impl to readonly @Getter`; same for `return im` from a `readonly @Getter` func and arg-pass; the raw arm `readonly *Getter = &im` is symmetric. Dropping the outer `readonly` compiles.
- **Root cause**: `AssignableTo` (`pkg/binate/types/types_assignable.bn:110,120`) gates the two iface-wrap arms on `dst.Kind == TYP_INTERFACE_VALUE[_MANAGED]` with NO peel → a `TYP_READONLY` dst misses both and falls to `return false`. Same transparent-wrapper principle as Defect 2's DISPATCH-site fix, left unapplied at the CONSTRUCTION site.
- **Severity**: MAJOR (reject-only — soundness intact; blocks factory functions returning `readonly @Iface` and readonly-iface params). **Owner: Plan-1 (`pkg/binate/types/types_assignable.bn`).** Fix: peel an outer `TYP_READONLY` (`resolveAliasAndConst(dst)`) before both iface-value Kind checks (raw + managed). Add conformance for concrete→readonly-iface construction across var-init/return/arg-pass.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed by the review; reject-only).

### [CR-2 Plan-1 review] method call through an alias-typed receiver (`type AB = @Box`) rejected "cannot call non-function" (reject-only) — ✅ RESOLVED (landed binate `b24978b6`, 2026-06-09)
- **Symptom**: `type AB = @Box; func (b *Box) m(); var r AB = p; r.m()` → "cannot call non-function"; `type RB = readonly Box; ... r.peek()` likewise. Direct (un-aliased) forms compile; FIELD access through the same alias works.
- **Root cause**: `ReceiverBaseNamed` (`pkg/binate/types/types.bn:458`) peels POINTER/MANAGED_PTR/READONLY but NOT ALIAS; `check_method.bn:77` calls it on the raw `recvType`. Attributed by `git` to 05fb3216 (readonly migration), NOT 408cc533 — pre-existing.
- **Severity**: MAJOR (reject-only). **Owner: Plan-1 (`pkg/binate/types`).** Fix: use the already-resolved `resolved.ReceiverBaseNamed()` for method lookup (keep raw `recvType` for the object-const classification), or make `ReceiverBaseNamed` peel `TYP_ALIAS`. Add method-call-through-alias conformance + a `ReceiverBaseNamed` unit test.
- **Discovery**: 2026-06-08 adversarial review of Plan-1.

### [CR-2 Plan-1 review] cyclic named type + the new `==`/`<` operand checks → infinite hang (`==`) / SIGSEGV (`<`) — ✅ RESOLVED (landed binate `68a62f8c`, 2026-06-09; def-time reject + bounded operand-predicate guards)
- **Symptom**: `type A B; type B A; func f(a A, b A) bool { return a == b }` → exit 124 (hang in `comparabilityKind`'s unguarded Underlying-loop, `pkg/binate/types/types_query.bn:235`); self-cycle `type A A` + `==` → 124; relational `a < b` → exit 139 (stack overflow in `IsNumeric`→`IsInteger`/`IsFloat`). The same cyclic type WITHOUT a comparison compiles through the front-end → the new `checkEqOperands`/`relationalOperandOK` entry points (commit `60719e01`, Defect 5) are the specific trigger.
- **Relationship to filed**: the underlying cyclic-type bug is filed, but a "neither introduces nor worsens" note there is WRONG — 60719e01's operand checks genuinely introduce the hang/SIGSEGV on the comparison path. The reviewer also refuted the filed claim that the old path already SIGSEGV'd via AssignableTo (`Identical`'s name-based TYP_NAMED branch short-circuits, no recursion). Amend that entry's attribution.
- **Severity**: MAJOR (compiler DoS — hang/crash on pathological but valid-to-parse input). **Owner: Plan-1 (`pkg/binate/types`).** Fix direction: cycle detection at type-definition time + a shared visited/depth guard on the Underlying-walking helpers (`comparabilityKind`, `IsNumeric`/`IsInteger`/`IsFloat`).
- **Discovery**: 2026-06-08 adversarial review of Plan-1.

### [CR-2 Plan-1 review] unary minus on a NAMED sub-word/non-host-width int → invalid IR (`sub i64 0, %i8`) — Defect-9 fix incomplete for `TYP_NAMED` — ✅ RESOLVED (landed binate `3c609caf`, 2026-06-09; conformance-pinned — the unit-test harness can't resolve named-type underlyings)
- **Symptom**: `-x` on a named integer type (`type Small uint8`, `type Tiny int8`, `type Mid int16`, `type W int64`) emits `sub <host-i64> 0, %i8/%i16` → clang hard error `'%vN' defined with type 'i8' but expected 'i64'` (no binary). Probe: `type Small uint8; var ns Small = -s` → `error: '%v3' defined with type 'i8' but expected 'i64'`. Plain (non-named) sub-word `-x` works (prints 251). The named-`int64` case only links on this 64-bit host (host int==i64); on the 32-bit primary target it emits `sub i32 0, %i64` → silent truncation (the conformance-423 class).
- **Root cause**: `genUnary`'s MINUS arm (`pkg/binate/ir/gen_expr.bn:225-236`) selects `negTyp = arg.Typ` only when `arg.Typ.Kind == TYP_INT` (or float, or checker-resolved `TYP_INT`); a `TYP_NAMED` operand misses BOTH guards → falls through to host `types.TypInt()`. The commit (`fce07ccd`) claims to be "the exact analog of the `~` fix", but the TILDE arm (`gen_expr.bn:~249`) sets `bnTyp = arg.Typ` UNCONDITIONALLY (passes TYP_NAMED through; `llvmType` unwraps it to the underlying width), so `~` is correct for named sub-word ints while `-` is a build break — the MINUS fix is strictly weaker than the `~` fix it mirrors.
- **Severity**: MAJOR (hard build break on named sub-word negation; silent truncation for named int64 on 32-bit). **Owner: Plan-1 (`pkg/binate/ir/gen_expr.bn`).** Fix: type OP_NEG at `arg.Typ` for any non-float concrete operand (mirroring TILDE), letting `llvmType` unwrap TYP_NAMED — do NOT gate on `Kind == TYP_INT`. Add a regression mirroring `conformance/regressions/unary-minus-subword.bn` with `type` over int8/16/32/64 operands (fails the build today), plus a named-type unit test; correct the commit message's "exact analog" claim. The added tests use only PLAIN sub-word ints, so the TYP_NAMED hole is invisible to CI.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed by me).

### Cross-module global of struct type emits `external global %Struct` without declaring the type — FIXED 2026-06-08 (binate `b0402d04`, plan-cr2-2 Defect 2)
- **Fix**: `collectStructTypes` (`pkg/binate/codegen/emit_types.bn`) now scans
  `m.Globals` after `m.Funcs`, so a struct reachable only through a package-level
  global (the defining `global` or a consuming `external global` — both record
  `g.Typ`) is discovered and its `%bn_<pkg>__Struct = type {...}` def emitted
  before the global that references it. `discoverStructFromType` also gained
  `TYP_NAMED`→`.Underlying` and `TYP_ARRAY`→`.Elem` recursion arms (a named-over-
  struct or `[N]Struct` global's struct was missed even via a function). Purely
  additive (discovery can only find more structs; `addStructDef` dedups). Pinned
  by `emit_types_test.bn` (TestStructTypeDiscoveredViaGlobal / ...ArrayOfStruct...
  / ...NamedStruct...) and `conformance/657_cross_pkg_struct_global`.
- **Symptom (was)**: a module that references another package's package-level `var
  g StructType` emitted `@<mangled> = external global %bn_<pkg>__Struct` but never
  `%bn_<pkg>__Struct = type {...}` in that module → clang "use of undefined type".
  Confirmed to bite a normal cross-package import (not just the test harness); also
  the defining package of a zero-init struct global (no function references the
  struct).
- **Discovery**: 2026-06-06; root cause was `collectStructTypes` scanning only
  `m.Funcs`. **Severity**: MAJOR (was a hard compile failure).

### `box(<scalar>)` is unimplemented on the native backend — silent no-emit → garbage result (MINOR wrong-code) — ✅ RESOLVED (landed binate `6235e43a`, 2026-06-09; native AND VM — the "VM works" claim below was wrong, BC_BOX SIGSEGV'd too)
- **Symptom**: `box(i)` where the operand is a scalar register (not an OP_ALLOC
  or aggregate) compiles fine on the LLVM backend but the native backends'
  `emitBox` hits the `else { ... return }` scalar arm (aarch64_emit.bn /
  x64_managed.bn) and emits **nothing** — no `rt.Box` call — so the OP_BOX
  result is undefined; the managed pointer then carries garbage.
- **Discovery**: 2026-06-06, building the loop-leak matrix (a `box(i)`-in-a-loop
  cell crashed on native while the LLVM build leaked-then-was-fixed). Not a leak.
- **Scope**: native aa64 + x64 only; LLVM/VM compile+run `box(scalar)` correctly.
  `box(struct-literal)` (OP_ALLOC source) and `box(iface-value)` (aggregate
  source) ARE handled — only the bare-scalar source is dropped.
- **Fix**: emit the scalar-source spill + `rt.Box` call in the native `emitBox`
  else arm (store the scalar into a frame slot, pass its address), OR reject
  `box(scalar)` in the checker if it isn't meant to be supported. **Test**: a
  conformance cell `box(i)` returning the boxed value; currently no coverage.

### Local `const` declarations silently materialize 0 — FIXED+LANDED (binate `273d7e4a`, 2026-06-05)
- **Symptom**: a `const` declared inside a function body (`func main() { const C
  T = V; var x T = C }`) reads as **0** (the zero value), for EVERY type
  (int/uint of all widths, float32, float64). The value `V` is dropped entirely.
  Fails on every backend (LLVM/VM/native). Package-level `const`, const-group
  members, and inline literals all work — only the **local** const form is
  broken. Local `const` is currently used nowhere in the compiler tree or
  conformance suite, so real-world impact is nil today, but it is a silent-wrong-
  value landmine.
- **Root cause (unknown — needs investigation)**: a local const declaration
  appears to register the name but never bind its value at the IR-gen read site
  (the read resolves to a zero-initialized slot rather than the const's
  materialized value). Either local consts must materialize like package consts,
  or the type-checker should reject local `const` until supported — silently
  emitting 0 is the wrong outcome.
- **Test**: `conformance/matrix/const/local-const/*` (12 cells, all types). To
  land: see the matrix-vs-regressions decision (one representative cell likely
  suffices — the bug is type-independent).
- **Discovery**: 2026-06-05, P1 const matrix (read-form axis).
- **Fix**: bind a local const's materialized value at its read site (mirror the
  package-const path), or reject local `const` at type-check if intentionally
  unsupported.

### Returning a by-value struct through interface-method dispatch was miscompiled — FIXED + LANDED 2026-06-04 (binate `9baa579d`)
- **Was**: an interface method returning a by-value struct (small
  aggregate, NOT a managed handle like `@T`/`@[]T`) came back through
  vtable dispatch with only its FIRST field correct, later fields garbage,
  in BOTH the LLVM backend and the bytecode VM.  Direct (concrete-receiver)
  calls were fine.
- **Root cause**: the interface method's result type was resolved during
  interface collection (GeneratePackage / GenModule first pass), which ran
  interleaved with struct-name registration in declaration order.  An
  interface method whose result is a struct declared LATER in the file
  (`interface B { get() Pair }` before `type Pair struct {...}`) resolved
  the struct via resolveTypeExpr's unresolved-name path, which silently
  falls back to `int`.  OP_CALL_IFACE_METHOD's result type (`instr.Typ`)
  thus degraded to a single word; both backends read `instr.Typ`, so both
  miscompiled identically (llvmType -> `i64`; the VM mis-sized the result).
  Latent because conformance/553 only returned a scalar / a managed-slice
  through an interface, never a plain struct.
- **Fix** (`9baa579d`): a struct-name pre-pass registers every struct name
  before the first pass, so interface method result types resolve to the
  real struct type.  Interface collection stays interleaved in the first
  pass (order vs globals / type-aliases -- which may be interface-typed;
  isInterfaceTypeExpr consults moduleInterfaces -- is unchanged).
  conformance/581 covers 2- and 3-field structs through managed- and
  raw-receiver dispatch, interfaces declared before the structs.  Full
  conformance green (505 comp / 499 int); no other
  by-value-struct-returning interface exists in-tree (Backend returns
  bool / @[]char).
- **Unblocked + LANDED 2026-06-04** (binate `b9ca1acc`): the repl ReplSession->interface conversion.

### A closure that captures a `@func` under-retained the captured value — FIXED + LANDED 2026-06-04 (binate `388c48d3`)
- **Was**: a closure that captures a `@func` value did not acquire a ref
  to the captured @func's record, but the closure struct's dtor RefDec'd
  it (NeedsDestruction(@func) = true).  The captured @func was
  under-retained: its record freed when the source @func's scope ended,
  then the closure called / dtor'd freed memory (use-after-free).  Native
  only; a flaky crash in __dtor_closure_* (deterministic under
  guard-malloc).  First seen as a wrapper poll (capturing a host @func)
  installed via vm.SetPoll — the shape an embedder needs for a VM-free
  poll — but the root cause is general (any closure capturing a @func).
- **Root cause**: gen_func_lit.bn emitCaptureRefInc handled
  TYP_MANAGED_PTR / TYP_MANAGED_SLICE but had no TYP_MANAGED_FUNC_VALUE
  branch — the capture-side acquire counterpart of the @func copy-RefInc
  symmetry work (d118a3c4 / 76099018), missing for closure captures.
- **Fix** (`388c48d3`): add the TYP_MANAGED_FUNC_VALUE branch calling
  emitManagedFuncValueRefInc (the acquire helper every other @func copy
  site uses).  conformance/586 pins it deterministically via refcounts;
  pkg/binate/vm TestWrappedCapturingPollSuspends covers the wrapper-poll
  shape.  Full conformance green (513 comp / 507 int).
- **Unblocked + the VM-free poll is now LANDED 2026-06-04** (binate
  `e3dc0d07`): repl's SetPoll takes a VM-free `@func() PollResult`, so
  the ReplSession interface no longer mentions pkg/binate/vm.

### ~~Compound assignment (`+=`, `-=`, …) to a non-IDENT lvalue silently drops the operator~~ — FIXED+LANDED (binate `45b9e767`, 2026-06-06) (`compound-assign-nonident`)
- **Symptom**: `a[i] += x`, `s[i] += x`, `a[i][j] += x`, `p.field += x`, and `*p += x` all store the BARE RHS (`x`), discarding the operator and the old value — a silent miscompile (no error, wrong result). Only the plain-variable form `v += x` is correct. Repro (each prints `5`, should print `15`):
  ```
  func main() { var a [3]int; a[1] = 10; a[1] += 5; println(a[1]) }          // array elem
  func main() { var a @[]int = make_slice(int,3); a[1]=10; a[1]+=5; println(a[1]) } // slice elem
  type P struct { x int }; func main() { var p P; p.x = 10; p.x += 5; println(p.x) } // field
  func main() { var v int = 10; var p *int = &v; *p += 5; println(v) }        // deref
  ```
- **Root cause**: `genAssign` (gen_control.bn) applies the compound op (`cur = load; rhs = cur OP rhs`, incl. the `/=` `%=` div-check guard) ONLY in the IDENT arm. The EXPR_INSTANTIATE_OR_INDEX (array/slice), EXPR_SELECTOR, and `*p` deref arms ignore `stmt.Op` and store `rhs` directly. Pre-existing; unnoticed because the whole codebase writes these longhand (`x.f = x.f + 1`) — 0 occurrences of compound-assign-to-lvalue in non-test source. Found during M7/M8 coverage review.
- **Fix (landed)**: the compound step (load current lvalue → `cur OP rhs` with the `/=` `%=` div-check guard) is factored into `emitCompoundBinop` + `isCompoundAssign`; every lvalue arm (IDENT, array, slice, pointer, struct-field, deref, nested-array) runs it before its store — a slot load through the elem/field/deref pointer, or EmitSliceGet for a slice element. **Test**: conformance 640 (variable, array elem, slice elem, nested array, field, deref; `+= -= *= /=`), green on LLVM + VM.

### ~~`~` (bitwise complement) IR-gen hardcodes the result type to `int` — invalid IR for sub-word, wrong-signed shift on uint64~~ — FIXED + LANDED (binate `42ad4fa0`, 2026-06-06) (`bitnot-result-type`)
- **FIXED**: `gen_expr.bn:247` now types `OP_BITNOT` as the operand's type
  (nil-fallback to `int`), mirroring `OP_NEG`. All `bitwise/not` cells pass on
  LLVM (123/123); unit tests `TestGenBitnotOn{Uint16PreservesWidth,
  Uint64IsUnsigned}` added. NOTE: the *native* backends keep a separate
  sub-word `~` gap — aa64's `Mvn` / x64's `not` ignore the operand width (part
  of `aa64-subword`); not addressed by this IR-gen fix.
- **Symptom (two facets, one root)**:
  - **A (invalid IR)**: `~x` for any sub-word int (`uint/int 8/16/32`) emits
    `xor i64 %x, -1` with a hardcoded i64 — clang rejects it
    (`'%x' defined with type 'i8' but expected 'i64'`). `~` simply does not
    compile for sub-word ints on the LLVM backend.
  - **B (wrong value)**: `(~v) >> k` consumed DIRECTLY (no intervening store)
    on `uint64` does an ARITHMETIC shift, not logical: `(~0) >> 32` is
    `2^64-1`, not the spec `2^32-1`. Storing `~v` into a `uint64` var first
    masks it (the store re-types to unsigned), and `(a+b) >> k` for unsigned is
    fine — so it is specific to `~`-results.
- **Root cause (CONFIRMED)**: `pkg/binate/ir/gen_expr.bn:247` lowers `~` as
  `b.EmitUnary(OP_BITNOT, arg, types.TypInt())` — the result type is hardcoded
  to `int` (signed, target-width i64) instead of the OPERAND's type. So the
  BITNOT instr is mis-typed: i64 width (→ facet A, mismatched `xor` width for a
  sub-word arg) and signed (→ facet B, a directly-consumed `>>` lowers to
  `ashr` not `lshr` per `emit_ops.bn:48-52`, which keys on `instr.Typ.Signed`).
  This is the SHARED IR layer, so it likely affects the VM/native backends too
  (facet B at least; the full `all` sweep is pending this decision).
- **Test**: `conformance/matrix/scalar-diff/bitwise/not/*` — 7 cells fail on
  `builder-comp` (the sub-word ones COMPILE_ERROR; `64/unsigned` value-diverges;
  `64/signed` passes — i64 + signed happen to match the hardcoded type).
- **Discovery**: 2026-06-06, differential-harness v2 (bitwise cells).
- **Fix**: type the `OP_BITNOT` result as the operand's type, mirroring the
  adjacent `OP_NEG` path's `negTyp` derivation (`gen_expr.bn:223-241`) — for
  `~`, the result type is always exactly the operand type (no widening). A
  one-site fix resolving both facets.

### ~~Compiled program leaks native stack per loop iteration for a default-init managed local~~ — FIXED + LANDED 2026-06-06 (binate `2411295c`)
- **Was**: a *compiled* program declaring a default-init managed local
  (`var m @[]char`) inside a loop body SIGSEGV'd once the loop ran enough
  iterations (~130k at an 8 MiB stack; threshold scaled linearly with
  `ulimit -s`, RSS flat — a native-stack leak, ~32 B/iter). The VM ran it fine.
- **Attribution correction**: this was the **LLVM codegen** (the `comp` /
  compiled modes), NOT the native-aa64 backend the old title named. The native
  aa64/x64 backends use a fixed frame (PlanFrame) and don't leak; the VM doesn't
  touch the native C stack. "native stack" = the C stack of the *LLVM-compiled*
  binary. (Verified: `var m @[]char` in a 3 M-iter loop completes on
  `--backend native`, crashes via `comp`.)
- **Root cause**: codegen hoists every alloca to the function entry block (an
  alloca in a non-entry block isn't freed until return, so a loop body alloca
  leaks per iteration), but the hoist pre-pass was missing three alloca-emitting
  ops, leaving their allocas in the loop body:
  - `OP_CONST_NIL` — the `.a` zero-fill slot of a default-init managed aggregate
    (the reported case).
  - `OP_RODATA_ARRAY` — the `.tmp` `[N x i8]` slot of `var a [N]char = "..."`.
  - `OP_BOX` — the `.tmp` spill slot of `box(<scalar register>)`.
  The latter two were **found by the new static checker** below, not the
  original repro.
- **Fix**: each op now splits its alloca into a hoistable decl emitter (run by
  the entry-block pre-pass) plus the in-place fill/store/load, matching
  OP_ALLOC. The pre-pass dispatch lives in `pkg/binate/codegen/emit_alloca_hoist.bn`.
  This also resolves the compiled-minbasic `runProgramInto` `var errMsg @[]char`
  crash without the doc's suggested side-step.
- **Detection (3 legs)** — the "detect this class in general" ask:
  - `conformance/check-alloca-hoist.py` + `scripts/check-alloca-hoist.sh` — a
    static checker asserting every alloca lives in its function's entry block,
    swept over the corpus (734 cells, 0 violations post-fix; it found the
    rodata-array + box siblings). The construct-agnostic, compile-time detector.
  - `conformance/gen-loop-leak-matrix.py` → `matrix/loop-leak/` — runtime cells
    that loop a construct enough to overflow an 8 MiB stack if it leaks, then
    print 42 (leak-prone cells crash pre-fix, pass post-fix on LLVM/VM/native).
  - `pkg/binate/codegen/emit_alloca_hoist_test.bn` — unit tests asserting each
    construct's alloca precedes the loop body in the emitted IR.

### ~~Native backends drop `binate_runtime.c` — every native program fails to link~~ — FIXED + LANDED 2026-06-05 (binate `1285683e`)
- **Was**: every `builder-comp_native_aa64-comp_native_aa64` cell failed at link
  with `Undefined symbols for architecture arm64: "_bn_pkg__bootstrap__Write"`.
  Self-hosted `BNC_NATIVE` computed an empty `runtimePath` (findRuntime ends in
  `return suffixes[i]`) so the `if len(runtimePath) > 0` gate dropped
  `binate_runtime.c` from the link.
- **Actual root cause** — a **shared native-backend** wrong-code bug, NOT what
  this entry first guessed: both native backends (aa64 AND x64 — not aa64-only)
  lowered an aggregate `OP_LOAD` as a bare *pointer into the source object*
  instead of materializing a copy. `return container[i]` then copied the
  element header into the sret buffer only AFTER the function's cleanup RefDec'd
  (and freed) the local container's backing → read freed/zeroed memory, so the
  return came back empty/garbage. LLVM and the VM were always correct (LLVM loads
  the aggregate into an SSA value at the load site).
- **`ee671b6c` (sub-word narrowing) was REFUTED by bisect** — rebuilding gen1
  with `emitSubWordNarrow` neutralized left the repro broken. It was never the
  cause; the bug is not char/sub-word arithmetic and predates `ee671b6c`. The
  earlier "aa64-only / findRuntime char handling / prime-suspect ee671b6c"
  framing in this entry was all wrong (recorded here so the mistake isn't
  repeated).
- **Fix**: `PlanFrame` now reserves an own data region for an aggregate
  `OP_LOAD` (as `OP_MAKE_SLICE` / aggregate calls already do); `emitLoad` copies
  the loaded bytes into it and points the result there, so the load owns its
  bytes and can't alias a freed source. Fixed in both the aa64 and x64
  `emitLoad`. aa64-native lane: 0 passed (all COMPILE_ERROR) → 811 passed, 0
  failed.
- **Tests**: `conformance/regressions/return-aggregate-element-of-local`
  (managed-slice element + struct array element returned directly — caught in
  the existing gen1-native lane, which is why a bespoke BNC_NATIVE smoke wasn't
  needed) + `TestPlanFrameReservesAggregateLoadDataRegion` (native/common).

### ~~`present(...)` is interface-value-only~~ — DONE 2026-06-08 (binate `29c9dc47`, conformance `667`): extended to func values (vtable field 0), pointers (non-null), slices (`len > 0`); value types rejected. Prerequisite length-0 ⟹ no-backing invariant landed (`71ff7489`, conformance `666`). Original investigation note kept below for context.
- **Current state**: the checker (`pkg/binate/types/check_builtin.bn:78-92`) accepts `present(x)` ONLY when `x` is a raw or managed interface value (`TYP_INTERFACE_VALUE` / `TYP_INTERFACE_VALUE_MANAGED`); everything else is rejected with "present argument must be an interface value". Lowering (`pkg/binate/ir/ir_ops.bn` `EmitIfacePresent`) extracts the vtable word (field 1) and compares it non-null (honest about typed-nil: boxing a nil `*T` still fills the vtable, so `present` is true).
- **Why this matters**: `present()` is the language's *sanctioned* "does this hold something / is it set" test for types where a direct `== nil` is a footgun or outright disallowed. We deliberately disallow `slice == nil` (a nil slice acts like an empty slice but is not the same) and steer interface values to `present(iv)` rather than `iv == nil` (typed-nil). For that story to be complete, `present()` must cover every type that has a meaningful "set / unset" (nullable) notion — otherwise disallowing `== nil` leaves users with no sanctioned test.
- **Investigate — which types are "sensible", and what does `present` mean for each**:
  - Interface values (`*Iface`/`@Iface`) — DONE (vtable non-null).
  - Managed pointers (`@T`) — if `@T` is nillable, `present(@T)` is the natural replacement for `@T == nil` (test the pointer word non-null). Confirm nillability, then define.
  - Func values (`*func`/`@func`) — `present(fv)` = code-pointer non-null (is the func value set?); replaces `fv == nil`. Ties into the `==`-on-func-values disallow above.
  - Raw pointers (`*T`) — already comparable to nil via `==` (spec: address equality). Decide whether `present(*T)` is ALSO accepted for uniformity, or left out as redundant.
  - Slices (`*[]T`/`@[]T`) — the footgun case. `present(slice)` testing data-ptr-non-null would re-introduce the exact nil-vs-empty footgun that disallowing `slice == nil` exists to avoid. Likely EXCLUDE (or define very deliberately) — specify explicitly either way.
  - Scalars / value structs / arrays — no presence notion; keep rejecting.
- **Then implement**: extend the checker rule (per-type accept/reject), add lowerings (each is the same "extract the relevant word, compare to null" shape as `EmitIfacePresent`, so every backend lowers it for free), and keep a clear diagnostic for the rejected types.
- **Tests (with the work)**: checker accept/reject per type; a runtime conformance cell per accepted type (set vs unset).
- **Relation to the `==` spec gap (above)**: the decision to DISALLOW `==`/`!=` on aggregates (incl. interface values) leans on `present()` covering all the nullability tests — land this so disallowing `== nil` does not leave a gap. NOTE: `present()` answers "is there anything here", NOT sentinel identity — `err == io.EOF` ("is this THE EOF error") is a separate, still-open question (see io.EOF entry).
- **Requested**: 2026-06-07, by user.

### ~~Interface method dispatch drops args after a width-mismatched managed-slice arg (codegen)~~ — FIXED + LANDED 2026-06-04 (binate `d6bb3b2f`)
- **Fixed**: factored the per-arg coercion loop out of `genCall` into a shared
  `coerceArg` helper (used by `genCall` + `genMethodCall`); `genInterfaceMethodCall`
  now evaluates args via `genExprOrFuncRef(...paramTyp)` + `coerceArg` like the
  regular path.  Interface method param types are carried via
  `ModuleInterface.MethodParamsFlat` + `MethodParamCounts` (flat encoding —
  `@[]@[]@types.Type` as a struct field trips a missing nested cross-package
  element dtor in the BUILDER, tracked separately below), populated at the decl
  AND generic-instantiation sites; `findInterfaceMethod` returns the param list
  from the inheritance level that owns the method (so embedded methods coerce
  too).  Pinned by `conformance/593` (own + inherited + func-value arg;
  negative-verified 3/3/3 without the fix vs 700/3/700 with) and `e2e/repl.sh`
  (now 53/53; `basic-call` was the hang).  Full conformance 522/0 + unit 39/39.
  Adversarial-reviewed before implementing (C1 inherited / C2 whole coercion
  machinery / M2 generic site / M3 self-ref timing / V2 flat encoding).
  Follow-up: a dedicated generic-interface-method slice-arg regression test
  (the generic-site population is code-identical to the verified decl path).
- **Root cause (CONFIRMED)**: `genInterfaceMethodCall` (`pkg/binate/ir/gen_iface.bn:89-94`)
  builds its call args with a bare `genExpr` per arg — it **omits the argument
  coercions** the regular call path applies (`gen_call.bn:140-202`), notably the
  `@[]T → *[]T` managed→raw slice conversion (`EmitManagedToRaw`).  When an iface
  method param is a raw slice (`*[]readonly uint8`, 2 words) and the arg is a
  managed slice (`@[]uint8`, 4 words), the unconverted 4-word value is passed
  where 2 words are expected, **shifting every following argument** — the next
  scalar arg is read from the wrong slot.  General MAJOR codegen bug; latent in
  conformance (no iface method has a managed-slice→raw-slice param).  The other
  omitted coercions (string-lit→chars, nil→slice, by-value struct-copy RefInc,
  iface-value move/RefInc) are each their own latent iface-arg bug.
- **How it surfaces (repl)**: the host loop calls `s.Step(line, eof)` where
  `line` is `@[]uint8` and `Step(line *[]readonly uint8, eof bool)`; with the
  conversion missing, `eof` is read as garbage/false, so an EOF turn never
  returns `STEP_EOF_CLEAN`.  The loop spins forever printing `> ` (NOT a clean
  segfault — it exhausts and dies; CI's captured output shows `> 14` then the
  crash).  `b9ca1acc` (ReplSession→interface) exposed it by routing `Step`
  through iface dispatch; green through `16:47`, first red `16:52`.  Not from
  the stdlib / bnc-0.0.7 work.
- **Minimal repro**: an iface method `M(line *[]readonly uint8, b bool) Res`
  (struct return) called via the interface with a `@[]uint8` arg returns the
  `b=false` branch even when `b=true` is passed.  Controls: `(int,bool)→int`,
  `(int,bool)→struct`, and `(@[]uint8,bool)→struct` (matched width) all pass —
  isolating it to the width mismatch, not sret / multi-word args in general.
- **Fix (planned)**: add `MethodParams` to `ModuleInterface` (populate alongside
  `MethodResults` during registration); factor the per-arg coercion loop out of
  `gen_call.bn` into a shared helper and call it from `genInterfaceMethodCall`
  too, so both paths stay in sync.
- **Why MAJOR**: silent wrong-arg in iface dispatch (not just repl).  Also E2E is
  red on *every* main commit, masking new E2E regressions; and `bnc-0.0.7` ships
  a `bni` whose interactive REPL hangs (accepted — REPL is a Tier-1 PoC, not
  build-critical; fix to land in 0.0.8-pre).
- **Test**: `e2e/repl.sh` `basic-call` (covers it end-to-end) + a new unit/
  conformance test from the minimal repro above.

### ~~MAJOR — generic-interface constraint parameterized by another type param isn't substituted before the satisfaction check → valid instantiation wrongly rejected~~ — FIXED + LANDED 2026-06-13 (binate `aef4422e`)
- **Was**: when a generic function's type parameter is bounded by a generic-interface instantiation that names ANOTHER of the function's type params — `func use[X any, T Container[X]]` — the constraint `Container[X]` was checked against the supplied type argument WITHOUT substituting `X`. So `use[int, @IntBox]` (where `impl @IntBox : Container[int]`) was rejected with `type argument @IntBox does not satisfy constraint Container[X]` (unsubstituted `X`).
- **Root cause**: `instantiateGenericFunc` (`check_generic.bn`) resolved each arg and checked its constraint in one pass, using `ft.TypeParams[i].TpConstraint` verbatim.
- **Fix** (`aef4422e`): split into two passes — resolve all type args first, then check each against its constraint after running the constraint through `substituteTypeParams` with the full arg list, so `Container[X]` becomes the concrete `Container[int]` (the InstDecl re-instantiation path from the generic-struct-substitution fix) before `typeSatisfiesConstraint`. The two-pass order also lets a constraint name a type param declared later. **Checker-acceptance widening** (flagged, user-approved): makes previously-rejected valid code compile; NOT over-widened (`use[bool, @IntBox]` still rejected — @IntBox impls Container[int], not the substituted Container[bool]).
- **Test**: `conformance/760_generic_constraint_type_param_arg` (end-to-end `use[int, @IntBox]` returning X via the constraint method → 77) green builder-comp / -int / -comp; 2 checker unit tests (positive + over-widening guard). Full unit (45 pkg) + full conformance (1422) green; 0 regressions.
- **Discovery**: adversarial review of the constraint-forwarding fix `614e6eea` (2026-06-13). Pre-existing.

### ~~MAJOR — instantiated generic interface never records `.Parents`, so its inheritance is invisible → valid descendant satisfaction wrongly rejected~~ — FIXED + LANDED 2026-06-13 (binate `298ef806`)
- **Was**: `buildInstantiatedInterface` (`check_generic.bn`) built the instantiated `TYP_INTERFACE` and its methods but left `.Parents` empty. Generic interface decls are deferred at `collectInterfaceDecl` (no stable type at decl time), so the extension clause was resolved NOWHERE — `isDescendantInterface(WideBox[int], Box[int])` returned false even for `interface WideBox[T] : Box[T]`. Valid descendant relations were wrongly rejected: `@WideBox[int]` satisfying a `Box[int]` constraint (forwarding), and the concrete `@IntImpl` (impl of WideBox[int]) satisfying `Box[int]` via its descendant impl.
- **Fix** (`298ef806`): resolve the extension clause inside `buildInstantiatedInterface` with the type-param scope active (so a parent `Box[T]` substitutes to `Box[int]`), via the same `resolveInterfaceExtension` the non-generic path uses — filling `.Parents` and running the same validation / method-conflict checks.
- **Test**: `conformance/754_generic_iface_extends_constraint` (a `WideBox[int]:Box[int]` hierarchy exercising both the concrete-impl descendant satisfaction and the forwarded-type-param descendant satisfaction, dispatching a constraint method end-to-end → 42) green builder-comp / -int / -comp; 2 checker unit tests (positive descendant + negative direction). Full conformance (1421) green; the one full-unit failure (`native/common` reflect-descriptor size) is a pre-existing, unrelated concurrent regression.
- **Discovery**: adversarial review of the constraint-forwarding fix `614e6eea` (2026-06-13). Pre-existing.
- **Exposed a separate gap**: the generic-interface-VALUE *upcast* (`@WideBox[int]` → `@Box[int]`) now type-checks then fails in codegen — previously masked by this checker rejection. Tracked as its own MAJOR in claude-todo.md.

### ~~MAJOR — a constrained type parameter forwarded as a type ARGUMENT isn't recognized as satisfying the same constraint → `type argument T does not satisfy constraint Orderable`~~ — FIXED + LANDED 2026-06-13 (binate `614e6eea`)
- **Was**: a constrained generic that forwarded its own type parameter as the type ARGUMENT to another constrained generic — `outer[T Orderable]` calling `inner[T](...)` (callee `inner[T Orderable]`) — was rejected with `type argument T does not satisfy constraint Orderable`. Forwarding to an `any`-constrained callee worked; only a non-trivial constraint match for a type-param argument was broken. Forced every constrained generic algorithm into one monolithic function (`Sort[T Orderable]` → `quicksort[T]` → `partition[T]` could not be factored into helpers).
- **Root cause**: `typeSatisfiesConstraint` (`check_generic.bn`, reached from `instantiateGenericFunc`) only matched a CONCRETE type argument against `c.Impls` (via `impl t : I`). When the argument is itself a type PARAMETER, no impl record has a receiver Identical to it, so the check always failed — it never consulted the parameter's declared bound (`TpConstraint`).
- **Fix** (`614e6eea`): add an early branch — when the argument is a `TYP_TYPE_PARAM`, succeed iff its `TpConstraint` bound IS the required interface (same Pkg/Name) or extends it (`isDescendantInterface`). A nil bound (`any`) satisfies no non-trivial constraint. **Checker-acceptance widening** (flagged before landing, user-approved): makes previously-rejected valid code compile, restoring the spec-intended behavior that a type param bounded by an equal-or-stronger constraint satisfies a callee's weaker-or-equal one. Verified NOT over-widened — a weaker-bounded param forwarded to a stronger-constrained callee, and an `any`-bounded param forwarded to any constraint, are both still rejected.
- **Test**: `conformance/752_generic_constraint_forwarded` (a 3-level same-constraint chain top→mid→leaf plus a stronger→weaker forward via `Orderable : Comparable` inheritance) — green builder-comp / builder-comp-int / builder-comp-comp; 4 checker unit tests in `check_generic_test.bn` (same-constraint, stronger-bound-via-inheritance, and the two over-widening guards). Full unit (45 pkg) + full conformance (1418) green in builder-comp; 0 regressions.
- **Coverage follow-up** (binate `f6701dee`): added `conformance/753_cross_pkg_same_name_constraint_distinct`, a negative test pinning the `(Pkg, Name)` discrimination of the forwarded-type-param branch (forwarding a `main.Foo`-bound param into a `pkg/dep.Foo` callee is rejected — same short name, distinct interfaces). The single-package tests above couldn't catch a regression that dropped the `Pkg` half. Surfaced by the adversarial review.
- **Discovery**: 2026-06-12, factoring a generic quicksort into helpers in the examples-repo `generics/` work; sibling of the generic-struct-substitution MAJOR fixed the same day.

### ~~MAJOR — generic function with a generic-struct (instantiated with its OWN type param) in its signature doesn't substitute the type param → `cannot assign Vec[T] to Vec[int]`~~ — FIXED + LANDED 2026-06-12 (binate `0a62d3f4`)
- **Was**: a generic function whose parameter or result type named a generic struct instantiated with the function's own type parameter — `Vec[T]` / `@Vec[T]` — left the type arg unsubstituted at the call site, so `newVal[int]()` kept result type `Vec[T]` and `var a Vec[int] = newVal[int]()` failed with `cannot assign Vec[T] to Vec[int]` (and the reverse in argument position). Blocked the headline generics use case: free functions over generic struct containers (Vec/Map/Set/List), the only available shape given "no generic methods on types" (so the `vec`/`hashmap` examples were held).
- **Root cause**: `substituteTypeParams` (`check_generic.bn`) walked `TYP_TYPE_PARAM` + the composite kinds (pointer/slice/array) but had NO case for an instantiated generic struct/interface, so a `Vec[T]` signature type fell through unchanged. The instantiation's originating decl + type-args lived only on the unreachable `GenericInstantiation` cache entry, never on the `@Type` itself.
- **Fix** (`0a62d3f4`): record the originating generic decl (`InstDecl`, an opaque `*uint8` like `TpOwner` — keeps `types` free of a cyclic `ast` dep) and the type-args (`InstArgs`) on the instantiated `TYP_NAMED` / `TYP_INTERFACE` (set in `buildInstantiatedStruct` / `buildInstantiatedInterface`). `substituteTypeParams` now re-instantiates such a type for the substituted args via the shared cache-aware helper `instantiateGenericDeclWithArgs` (extracted from `resolveTypeInstantiation`), so `Vec[T]` with T→int becomes the same canonical cached `Vec[int]` a direct mention produces. IR-gen needed NO change — it monomorphizes by re-resolving the AST under its own type-param context (`gen_util.bn` `TEXPR_INSTANTIATE`), independent of this `@Type` (confirmed empirically across builder-comp / -int / -comp).
- **Test**: `conformance/749_generic_struct_in_generic_func` (value + managed result, value + Push-style managed param, a `T`-typed field, a second instantiation) — green builder-comp / builder-comp-int / builder-comp-comp; checker unit tests in `check_generic_test.bn` incl. a negative pinning `Vec[int]` is NOT assignable to `Vec[bool]` (guards against dropping the type arg entirely). Full unit (45 pkg) + full conformance (1416) green in builder-comp; 0 regressions.
- **Interface follow-up** (`d8cbb9f7`): the self-review found the INTERFACE sibling of the same gap — a generic interface instantiated with the function's own type param inside an interface-value (`@Container[T]` / `*Container[T]`) was still unsubstituted, because `substituteTypeParams` had no `TYP_INTERFACE_VALUE` / `TYP_INTERFACE_VALUE_MANAGED` case to recurse into the wrapper. Added those two cases (recurse into `Elem`, rewrap), which reaches the `InstDecl` interface inside so `@Container[T]` → `@Container[int]`. Covered by `conformance/750_generic_iface_in_generic_func` (managed iface-value param over the function's own T, two instantiations, dispatch) + 2 checker unit tests (incl. a negative: `@IntBox` does not satisfy the substituted `@Container[bool]`); full unit + full conformance (1417) green in builder-comp.
- **Discovery**: 2026-06-12, building the examples-repo `generics/` example (a generic `Vec[T]` container).
- **Sibling still open**: a constrained type-param forwarded as a type ARGUMENT isn't recognized as satisfying the same constraint — separate MAJOR in claude-todo.md; next up.

### ~~Big MULTI-RETURN function values are miscompiled on the x86-64 (SysV) native backend — data pointer collides with the sret pointer~~ — FIXED + LANDED 2026-06-10 (binate `f0747762`)
- **Was**: SysV-AMD64 returns a > 16 B aggregate (single OR multi-return tuple) via an sret pointer in RDI (the hidden first arg).  For a multi-return funcval, `emitCallFuncValue` used the SCALAR convention (data → RDI), then the `bigMultiRet` block OVERWROTE RDI with the sret pointer — so a CAPTURING closure's data pointer was destroyed before the shim could load captures (SIGBUS), and a NON-capturing funcval WITH user args fared no better (the scalar shim's left-shift dropped a user arg into RDI, clobbering sret).  Only {non-capturing big multi-return, no user args} survived by luck.  aa64 was correct (sret uses a SEPARATE register, X8); LLVM + VM correct.  Broke `capturing-closure-multi-return` case (b) on the x64 lanes since `3540118c` (the x64 lane was wrongly assumed a non-functional stub — it runs on Rosetta locally and qemu in CI).
- **Fix** (`f0747762`): route a big multi-return (`multiReturnTupleSize_x64 > 16`) through the SAME retbuf-in-RDI / data-in-RSI / user-args-RDX+ convention single aggregates use — the call site sets RDI = retbuf (the spill slot the tuple result is collected from) and RSI = data, dropping the old RDI-overwrite; the non-capturing shim takes the sret shape (`useSret || bigMultiRet`); the closure shim reuses `emitClosureShimAggregate_x64`'s sret path.  Small multi-return (`≤ 16 B`, packed RAX:RDX) unchanged.  Also closed a silent-miscompile the adversarial review surfaced (and that the single-aggregate sret shape already had): the non-capturing shim CLAMPED `nUserWords` to 6 instead of guarding it, silently truncating user args beyond the register budget — replaced with a loud `a.SetError` over-budget guard across all shapes (scalar ≤ 5, pack/sret ≤ 4), mirroring `emitClosureShimAggregate_x64`.  Rebased over a concurrent "all-int float-scalar ABI for func-value shims" change (`34533cf8`); the sret arm now uses the float-aware `emitShimArgMarshal_x64(…, 2, 1)`.
- **Test**: `conformance/regressions/funcval-big-multi-return-args` (capturing + non-capturing, managed + struct components, with user args) — green on LLVM / VM / native aa64 / native x64; `capturing-closure-multi-return` now passes on x64.  Full x64 (Rosetta) suite 1329 passed / 0 regressions.  Unit test pins the > 16 B size threshold.  10-agent adversarial review found the over-budget guard gap (now fixed) and otherwise confirmed the ABI change sound.
- **Discovery**: 2026-06-10, validating the x64 single-aggregate closure fix (`48e30320`) on the Rosetta lane.

### ~~Capturing closure returning a SINGLE AGGREGATE is miscompiled on the NATIVE x86-64 (SysV) backend too~~ — FIXED + LANDED 2026-06-10 (binate `48e30320`)
- **Was**: the x64 closure shim (`emitClosureShim_x64`) had the same scalar-only gap the aa64 backend did — a capturing closure returning a SINGLE aggregate read RDI as the data pointer instead of the retbuf, so pack (1..16 B) returned garbage and sret (>16 B) left the SysV sret pointer unset.  Found because the x64 lane (Rosetta locally / qemu in CI) is a real, usable backend — earlier wrongly assumed a non-functional Phase-2 stub from a stale runner/EmitObject comment (those comments are refreshed in this commit).
- **Fix** (`48e30320`): `emitClosureShimAggregate_x64` (new `x64_closure_shim_aggregate.bn`) mirrors the aa64 sibling (`646e1638`) but adapts to SysV — sret uses RDI (an arg reg), so captures occupy argReg(1..) (capBaseSlot 1, retbuf already in RDI, tail-jump) for sret and argReg(0..) (capBaseSlot 0, frame + RAX/RDX copy-back) for pack; data is stashed in R11.  Over-budget is a loud `a.SetError`.  Verified on the `builder-comp_native_x64_darwin` (Rosetta) lane: `conformance/regressions/capturing-closure-aggregate-return` passes, and the pack/sret/user-arg/float-arg/large-arg/indirect-large-capture edge cases all match LLVM.  Relative byte-count shape unit tests pin the dispatch.
- **Sibling still open**: big MULTI-RETURN (>16 B) funcvals remain broken on x64 (the call site overwrites the data pointer with the sret pointer — both want RDI).  Filed as a CRITICAL in claude-todo.md; next up.

### ~~Capturing closure returning a SINGLE AGGREGATE is miscompiled on the NATIVE aa64 backend (pack reads garbage; sret null-derefs)~~ — FIXED + LANDED 2026-06-10 (binate `646e1638`)
- **Was**: the native aa64 closure shim (`aarch64_closure_shim.bn`) implemented only the scalar / void return shape.  The funcval call site (`emitCallFuncValue`) and the non-capturing shim use a uniform retbuf-in-X0 convention for a SINGLE aggregate return (X0=retbuf, X1=data, user args X2+), but the scalar closure shim read X0 as the data pointer and never threaded retbuf into the lifted body's result location.  A pack-return (1..16 B) closure read garbage; an sret-return (>16 B) closure left X8 unset, so the lifted body wrote its struct through a NULL sret pointer → SIGSEGV (`str x11,[x10]`, x10=0).  Scope was broader than first filed (originally "sret only"): BOTH pack and sret single-aggregate returns broke.  Non-capturing aggregate func-values, and capturing closures with a scalar or multi-return result, all already worked; only {capturing closure} × {single aggregate return} broke.  LLVM (post-`3540118c`) and the VM were correct.
- **Fix** (`646e1638`): `emitClosureShimAggregateAA64` (new `aarch64_closure_shim_aggregate.bn`) mirrors the non-capturing pack/sret shapes but loads captures from the data pointer (held in X9, a non-arg scratch, so capture loads never clobber the base) and prepends them.  sret: MOV retbuf X0→X8, set up args, tail-branch (B preserves X8); pack: frame up, stash retbuf, set up args, BL, copy packed return X0..X(retWords-1) back through retbuf.  User args shift from incoming X(2+j) to outgoing X(W+j) uniformly; counting user words via `common.ArgWords` over-counts floats/indirect-large aggregates but only as harmless TRAILING phantom moves (verified by runtime A/B on `@func(Big,int)`, `@func(float64,int)`, interleaved — all match LLVM).  Over-budget (captures+args > X0..X7) is a loud `a.SetError`, not a silent `B` (stack-spill aggregate path is a follow-up).  The identical x64 closure shim (scalar-only, but x64 native isn't a usable backend yet) is cross-referenced to this file as the model to mirror.
- **Test**: `conformance/regressions/capturing-closure-aggregate-return` (pack 8/16 B, sret 24/32 B, a user-arg case, a managed-slice return) — green on LLVM, VM, native aa64; byte-count shape unit tests pin sret(16)/pack(44)/sret+arg(20) so a regression dropping the aggregate dispatch is caught.  Full native aa64 conformance 1300/0.  Adversarial review (10-agent workflow) found 0 confirmed bugs.
- **Discovery**: 2026-06-10, building the comprehensive `capturing-closure-multi-return` regression while fixing the LLVM sibling (`3540118c`) — the native counterpart of that LLVM-only bug, the same {closure × aggregate-result} gap in a different backend.

### ~~Capturing closure (`@func` with environment) returning an AGGREGATE reads the result from the wrong location — silent garbage / crash~~ — FIXED + LANDED 2026-06-10 (binate `3540118c`)
- **Was**: a capturing closure whose function-value type returns an aggregate (a multi-return tuple, or a single >register-width result) emitted a shim that returned the aggregate BY VALUE, while its vtable call slot was the `void(i8*, i8*, …)` retbuf shape (`funcSignatureLLVM` → `isAggregateReturn`). `emitCallFuncValue` allocated a result buffer, called `shim(retbuf, data, …)`, and read the result from a buffer the shim never wrote; the shim, taking `data` as its first param, read its captures out of the retbuf pointer instead. → garbage on LLVM (`6469215644`, …); a managed component → crash. `emitClosureShim` (`emit_funcvals_closure.bn`) had no aggregate-return branch (only `isVoid`/`floatRet`); the non-capturing `emitFuncValueShim` already dispatches to `emitFuncValueShimAggregate` at its top. A NON-capturing func-value multi-return, and a capturing closure with a SINGLE scalar return, both already worked; VM + native were correct (multi-return) — LLVM-codegen-only. (Was bug "110".)
- **Fix** (`3540118c`): `emitClosureShimAggregate` emits the retbuf shim (`void @__shim.<m>(i8* %retbuf, i8* %data, <args>)`) that loads captures from `%data`, calls the lifted body, and writes the result through `%retbuf` — register-return for a multi-return tuple, sret pass-through for a single >register-width result. Shared capture-load / underlying-arg-list helpers keep the scalar and aggregate closure shims in lockstep. Pinned by `conformance/regressions/capturing-closure-multi-return` (reshaped to the multi-return sub-shapes green on every backend: plain `{int,int}`, `{int,@[]int}` with a managed component = the pre-fix crash, and a multi-return closure with an aggregate user-arg) + non-vacuous `TestEmitClosureShimAggregateReturnUsesRetbuf`. Full builder-comp suite 1330/0; VM + native aa64 green.
- **Sibling found (still open)**: the comprehensive test surfaced a SEPARATE native-backend crash — a capturing closure returning a SINGLE >16-byte aggregate (sret) SIGSEGVs through a null sret pointer. Native-only (LLVM post-fix + VM correct). Filed as a new CRITICAL in claude-todo.md.

### ~~Passing a plain (no-managed-field) struct/array composite literal by value passes the alloca POINTER, not the value~~ — FIXED + LANDED 2026-06-10 (binate `32f2e2e8`)
- **Was**: `dist(Pt{a:3,b:4})` / `asum([2]int{5,6})` printed garbage on LLVM + every native backend (the callee read the composite's alloca pointer bits as the fields); the VM was correct (carries aggregates by address). Root cause: `coerceArg` (`gen_call.bn`) only loaded the OP_ALLOC aggregate inside the `needsStructCopy` branch, so a plain (no-managed-field) aggregate fell through with the alloca pointer unloaded.
- **Fix** (`32f2e2e8`): an else-arm in `coerceArg` that loads the value when the arg is an aggregate alloca AND the param takes it by value (reusing the dest-type-aware, wrapper-transparent `isAggregateAllocToLoad`, so a pointer param `f(&x)` doesn't match and still passes the address). No RefInc/copy — no managed fields. Pinned by `conformance/regressions/composite-arg-by-value` (un-xfailed; struct + array; all modes) + a non-vacuous IR unit test. Full LLVM/gen2/VM/native-aa64 suites 0-failed.
- **Discovered** 2026-06-08 (adversarial review of Plan-1); elevated to CRITICAL + pinned + fixed 2026-06-10.

### ~~plain `=` destructure of a nameless (iface-method/func-value) multi-return → invalid IR for any non-int field~~ — RESOLVED (binate `f8916b88` + the MethodResultsFlat seam); retired by triage 2026-06-10: `gen_assign_multi.bn` has the `multiReturnFieldTypes` fallback; `matrix/abi/{iface,funcval}-multi-return-assign/*` cells green on LLVM/VM/aa64/gen2.

### ~~Interface-method dispatch drops the result type for any method with ≠1 results~~ — RESOLVED (MethodResultsFlat/MethodResultCounts seam + native `cc2ddcc4`); retired by triage 2026-06-10: `gen_iface_registry.bn`/`gen_iface.bn` read the per-method result LIST; `matrix/abi/iface-multi-return/*` green on every runnable mode. The residual x64-elf/arm32 xfails are a SEPARATE native tuple-packing item (still open).

### ~~Destructuring a multi-return FUNCTION-VALUE call is rejected at type-check~~ — RESOLVED (`hasExpandableResults` in `check_stmt.bn`); retired by triage 2026-06-10: used at both the `=` and `:=` sites; `matrix/abi/funcval-multi-return/*` cells green with no xfails.

### ~~Nested arrays (`[N][M]T`) are mis-compiled — wrong-code / invalid IR, LLVM~~ — RESOLVED (binate `7583b669` inner-array value load + `fdc92562` `a[i][j]` addressing); retired by triage 2026-06-10: `regressions/nested-array-literal-store` + `637`/`638` green, no LLVM xfails.

### ~~>16-byte struct passed by value through an indirect call SIGSEGVs on LLVM~~ — RESOLVED (binate `3892e7d1`, pass >16B aggregate args by pointer); retired by triage 2026-06-10: `matrix/abi/{iface,funcval,struct}-param` 24B cells green on LLVM/VM/gen2, no SIGSEGV. Only the arm32 `three-int` xfails remain (separately tracked, not host-runnable).

### ~~Field read through a nested-array managed-POINTER element (`a[i][j].field`, `[N][M]@Struct`) → literal 0~~ — RESOLVED (`gen_access.bn` `getIndexElemType` recursion + `genIndexPtr` inner-element handling); retired by triage 2026-06-10: `matrix/nested-index/field/nested-managed-ptr` green on every runnable backend; its `builder-comp-int-int` xfail was XPASS-stale (removed). The x64-elf/arm32 xfails are likely stale too but were not host-runnable — left pending a run in those modes.

### ~~aa64 native backend mis-packs non-8-multiple / sub-word-packed structs (param + return)~~ — RESOLVED (aa64 regWords-vs-stack tail-drop fix); retired by triage 2026-06-10: `matrix/abi/struct-{param,return}/{three-u32,five-u8}` green on native aa64, xfail markers gone.

### ~~Managed-struct under multi-assign / multi-short-var miscompiled on x64 native~~ — RESOLVED (binate `b5616b32`, coalesce multi-return tuples into SysV eightbytes); retired by triage 2026-06-10: `matrix/refcount/multi-{assign,short-var}/.../managed-struct` green on x64-darwin, xfails gone.

### ~~Interface dispatch drops the trailing scalar after a multi-word by-value arg~~ — RESOLVED (binate `3892e7d1`); retired by triage 2026-06-10: `598_iface_dispatch_multiword_arg` green on LLVM modes + VM, un-xfailed.


### ~~Indexing a dereferenced pointer-to-array `(*p)[i]` (p is `*([N]T)`) drops the write / emits invalid IR~~ — FIXED + LANDED 2026-06-09 (binate `2a15d102`)
- **Was**: `(*p)[i] = v` where p is `*([N]T)` silently mutated a loaded COPY of the array (store dropped — `arr[0]` stayed 10, not 99); the read `(*p)[i]` emitted invalid IR; `(*pm)[i][j]` likewise. The whole-array store `*p = {...}` and `p = &arr` always worked — only element-indexing-through-the-deref was broken. Pre-existing on all backends (confirmed at `38a552e7~1`); scoped to the rare pointer-to-fixed-array construct.
- **Root cause**: the index access/assign lowering `genExpr`'d the `*p` base, loading the whole array as a value (no backing storage), so the array arm couldn't recover an element pointer.
- **Fix** (`2a15d102`, `pkg/binate/ir`): recover the element pointer through p (the array's backing address) — add an `EXPR_UNARY`/STAR deref arm to `genIndex` (read), the index-assign arm (`gen_control.bn`), and `genIndexPtr` (for the nested `(*pm)[i][j]` case), plus an `indexExprType` deref case so `(*pm)[i][j]` classifies as a nested-array base. Pinned by `conformance/regressions/deref-ptr-to-array-index` (read+write, single+multi-dim, all backends) + an IR unit test (write emits GEP+store, non-vacuous). Full builder-comp / gen2 / VM / native-aa64 suites 0-failed.
- **Discovered** 2026-06-09 by the adversarial review of the `&<aggregate-global>` fix — a probe's pointer-to-array repro surfaced it.

### ~~Storing `&<aggregate-global>` (address of a struct/array global) into a pointer writes NULL — SILENT wrong-code — all backends — regression from the whole-aggregate-assign fix~~ — FIXED + LANDED 2026-06-09 (binate `38a552e7`)
- **Was**: `var RP *T = &VS` / `RP = &VS` / `q := &VS` (VS a struct/array global) stored null instead of the address, on every backend (silent wrong-code; a field write / deref through the pointer then read 0 or crashed). `&VS` lowers to an IsGlobalRef pseudo (Op=OP_ALLOC, struct/array TypeArg) that presents like a composite-literal alloca, so the dest-type-blind `isStructOrArrayAlloc(rhs)` load-before-store (added by the 2026-06-06 whole-aggregate-assign fix) loaded the aggregate VALUE (0) out of the address and stored that. Confirmed identically on LLVM/VM/native-aa64/native-x64-darwin (an IR-gen bug, not per-backend).
- **Fix** (`38a552e7`, three sites in `pkg/binate/ir`): (1) the ident/deref/selector assignment arms (`gen_control.bn`) → dest-type-aware `isAggregateAllocToLoad(rhs, destTyp)` (pointer dest stores the address; aggregate dest still loads); (2) `genShortVar` excludes the IsGlobalRef from the alloca-direct bind so `q := &VS` infers `*T`; (3) `isAggregateAllocToLoad` peels readonly/named/alias wrappers on both operands — the latter was REQUIRED: the arms-only fix regressed `matrix/globals/readonly/struct` (a `readonly S` global) on LLVM, caught by the full builder-comp suite, because the un-peeled predicate skipped the load for the wrapped destination; peeling also closes the same latent wrapper bug in the index-arm callers. The originally-proposed one-liner (`if instr.IsGlobalRef { return false }` in `isStructOrArrayAlloc`) was confirmed WRONG — it re-breaks `x = VS` whole-struct copy from a global. Pinned by `conformance/regressions/addr-of-aggregate-global` (struct + array; init/assign/short-var forms) + two non-vacuous IR unit tests in `gen_control_test.bn`. All four full-suite modes 0-failed (gen2 self-host included); hygiene clean.
- **Discovered** 2026-06-09 building Plan-C C8 (`cross_pkg_extern_field_write`) — setting up an imported raw-ptr var via `var RP *T = &VS` surfaced it.

### ~~A `@[]@[]@T` (managed-slice-of-managed-slice) STRUCT FIELD emits a reference to an undefined nested cross-package element dtor~~ — FIXED + LANDED 2026-06-05 (binate `1cb4490c`, plan-cr-p2-2 step 6; `elemDtorName`/`elemCopyName` call ms/array element dtor/copy by their LOCAL weak_odr name; `607`). NOTE: the `MethodParamsFlat` `@[]@types.Type` workaround is NOT yet reverted — gated on a BUILDER bump (a bnc rebuilt from this fix accepts the natural nested encoding).
- **Symptom**: adding a struct field of type `@[]@[]@types.Type` to a struct in
  `pkg/binate/ir` made clang fail building `pkg__binate__ir.ll` with `use of
  undefined value '@bn_pkg__binate__types____dtor_ms_mp_pkg__binate__types__Type'`.
  The generated nested dtor `__dtor_ms_ms_mp_Type` (for the field) references the
  inner element dtor `__dtor_ms_mp_Type` qualified to the *element's* package
  (`pkg/binate/types`), but that inner dtor is never emitted/defined there.
- **Discovery**: 2026-06-04, building the interface-arg-coercion fix (`d6bb3b2f`)
  — `ModuleInterface` initially carried `MethodParams @[]@[]@types.Type`.  Worked
  around by switching to a flat encoding (`MethodParamsFlat @[]@types.Type` +
  `MethodParamCounts @[]int`), so the shape stays at `@[]@Type` (known-good, ==
  `MethodResults`).  `gen_dtor.bn` documents `ms_ms_mp` dtors as supported in the
  abstract, but the cross-package element-dtor emission for a *struct field* of
  that shape isn't wired up.
- **Why MAJOR / latent**: it's a silent undefined-symbol at link for a legal
  type shape; latent because nothing in the BUILDER tree currently needs a
  `@[]@[]@T` struct field (the flat workaround avoids it).  A non-flat use would
  hit it again.
- **Root cause**: unknown — needs investigation in the dtor-emission path
  (does the nested ms-of-ms dtor ensure its inner element dtor is emitted, and
  with the right package qualification, when the element type is cross-package?).
- **Fix direction**: ensure `__dtor_ms_mp_<Elem>` is emitted (in the element's
  package, or homed where referenced) whenever a `__dtor_ms_ms_mp_<Elem>` is
  generated.  Add a unit/conformance test with a `@[]@[]@T` struct field where T
  is a cross-package managed type.

### ~~A managed-slice-of-interface-value (`@[]@I`) constructed via a slice LITERAL leaks its elements~~ — FIXED + LANDED 2026-06-05 (binate `fddf8676`, plan-cr-p2-2 step 6; root cause was the `__dtor_ms_unknown` name collision when a module has both `@[]@I` and `@[]@func` — dtorTypeSuffix now emits injective `iv`/`fv` suffixes; `606`)
- **Symptom**: `var s @[]@Foo = @[]@Foo{makeFoo(i)}` (a slice literal of interface values), dropped at scope exit, never RefDec's its `@Foo` elements — the receiver (and its managed fields) leak (rc 1→2, never back to 1).  The element-ASSIGN form (`var s @[]@Foo = make_slice(@Foo, n); s[0] = makeFoo(i)`) is balanced; only the literal leaks.
- **Root cause (from `--emit-llvm`)**: both forms call the slice's `__dtor_ms_unknown`, which RefDec's the slice backing with a NULL dtor and does not walk the interface-valued elements (no per-element iface dtor).  So the element-type isn't propagated into the managed-slice dtor selection for the literal shape.  This is the `@[]@I` feature area already flagged as incomplete by `440_iv_in_slice_mgd` ("compiles, but writes into the iv slot segfault").
- **Discovery**: 2026-06-03 adversarial coverage audit of the `@Iface` refcount lifecycle.  Likely **pre-existing** / part of the known-incomplete `@[]@I` support — NOT a regression in the core refcount wiring (the common copy-sites — return / var-init / assign / field / array-element / managed-slice-element-assign / composite / struct-copy / param / deref — are all rc-balanced, pinned by 553/554/556/560/567).
- **Status**: tracked, not fixed.  Lower priority (exotic shape in a known-incomplete feature); fix alongside the broader `@[]@I` completion (440).

### ~~Managed-interface-value refcount lifecycle is unwired — FAMILY of leaks + 1 UAF~~ — FIXED + LANDED (core wired 2026-06-03; residual closed 2026-06-05 plan-cr-p2-2 steps 2+5: the iface-method-DISPATCH result leak — `genInterfaceMethodCall` registered nothing — via `registerManagedCallResult` (binate `f5410fcf`), and the per-arm `@Iface`/`@func` copy switches consolidated onto `emitStoreManagedSlot` (binate `ce2c8175`); b2 depth coverage `605`)
- **Root cause (CONFIRMED)**: managed interface values (`@Iface`) were added to the language, but the refcount *lifecycle* machinery in `pkg/binate/ir` was only ever wired for managed-ptr / managed-slice / struct — **never iface**.  Three distinct sites are missing the `isManagedIfaceValueType` case, producing three bugs:
  1. **UAF — return a named-local `@Iface`** (`func f() @I { var s @I = q; return s }` → `f().m()` reads freed data).  `gen_return.bn`'s Axiom-3 retain loop has no iface case, so a *borrowed* (loaded) iface return is never retained for the caller; the source local's scope-exit RefDec frees it.  (The original target bug; found 2026-06-03 building `plan-std-errors.md` Part 1, where `errors.New`/`Wrap` return `@Error`.)
  2. **LEAK — discarded / non-moved iface temp** (`makeFoo(inner)` as a bare statement → inner rc 1→2, dtor never runs).  `emitTempCleanupBody` (gen_util_refcount.bn:292) RefDec's managed-ptr/slice/struct temps but **skips iface temps**, even though they are registered in `ctx.Temps` (gen_call.bn:252).  **Pre-existing**, independent of the return path (reproduces on Part-0 `bnc`).
  3. **LEAK — reassigning an `@Iface` local** (`var f @I = a; f = b` → `a`'s old iface value is overwritten without a RefDec → leaked).  `gen_assign` doesn't RefDec the previous managed-interface value.  **Pre-existing.**
- **Why these were never caught**: NO conformance test returns / discards / reassigns a managed interface value — every `@…` test uses managed *pointers* (`@Counter`/`@Item`/…).  520 is the only test that returns an `@Foo`, and only via the *boxed-on-return* shape (which happens to be balanced).
- **Verified shape matrix** (rt.Refcount before/after, 8 return shapes, adversarially adjudicated): balanced *before any fix* = boxed-on-return (A/520), call-result (C), field-extract (E), multi-return (H), empty (G).  Broken *before any fix* = named-local (B) and param (D) → the UAF.  A naive unconditional `gen_return` RefInc fixes B/D but **over-retains the already-owned producers** (C call-result, E field-extract) → new leaks.  A narrow `rv.Op != OP_IFACE_VALUE` gate still leaks C/E (call/extract are owned too).  → the discriminator is "borrowed load vs owned producer", which the temp/local machinery already tracks for `@T`.
- **Fix (chosen: principled / uniform, 2026-06-03)**: wired `@Iface` through the refcount machinery everywhere `@func` / `@[]T` already go.  Added `isFreshManagedIfaceValue` (gen_refcount_pred); iface RefDec in `emitTempCleanupBody`/`Since`; the consume-fresh / RefInc-borrowed hybrid at every copy-site (return / var-init / `:=` / assign / index-range / composite / slice-literal element); iface struct/array copy+dtor field cases (gen_copy_emit, gen_dtor_emit_bodies); registration of iface call/method results (gen_call, gen_method); and `NeedsDestruction → true` for `TYP_INTERFACE_VALUE_MANAGED` (types_query — was making the struct-field handling dead code).
  - **Params/args use the MOVE model, NOT the copy model** (this is the subtle part): an iface param gets NO entry RefInc; the caller MOVES a fresh arg in via `consumeTemp` or RefInc's a borrowed one (gen_call/gen_method arg sites), and the param's scope-exit RefDec releases that single ref.  Reason: the bytecode VM passes a 2-word iface value on transient `vm.SP` that the call reclaims, so the copy model (caller retains + cleans its arg COPY post-call) reads freed stack and crashes (370/383 in `-int`).  `@T` can use the copy model only because it's 1 word in a stable local.
- **Verification**: all 16 lifecycle shapes (return×6 / var-init / assign / composite / struct-by-value-copy / multi-consumer / discard / reassign / 1000-iter loop / self-assign) rt.Refcount-balanced, adversarially adjudicated.  Conformance 370/383/473/521/545/546 green in builder-comp / -int / -comp-comp / native aa64+x64.  (520 still fails in `-int` = the separate pre-existing "call through nil interface value" VM bug; 383 fails only in `-int-int` = the pre-existing cross-package double-interp loader limit, which also fails 136_grouped_imports.)
- **Why MAJOR/critical**: #1 is a silent UAF; #2/#3 are silent leaks (violate the "compiler must NEVER leak" invariant).  Blocks `plan-std-errors.md` Part 1.
- **Tests**: 546 (method-value, catches UAF) exists; add a new rt.Refcount-*balance* conformance test (catches leaks) for the return / discard / reassign / param shapes before landing.
- **Status**: FIX IMPLEMENTED + verified on worktree (branch `work-1`); adding the balance conformance test, then full regression + cherry-pick.  Part 0 (`present`) already landed.  See `plan-std-errors.md`.

### ~~Short-var single-bind `x := s` of a managed struct-by-value skips the copy~~ — FIXED + LANDED 2026-06-05 (binate `b0eb7299`, plan-cr-p2-2 step 3; routed through `emitStoreManagedSlot`; matrix short-var/ident/managed-struct un-xfailed)
- **Symptom**: `x := src` where `src` is a struct with a managed field copies the
  struct WITHOUT `__copy_` — the copy's managed field is not RefInc'd, so when
  both `src` and `x` leave scope the field is RefDec'd twice (double-free).
  `var x T = src` and `x = src` (var-init / assign) copy correctly; only short-var
  `:=` under-copies.
- **Root cause (CONFIRMED)**: `genShortVar`'s single-bind arm
  (`gen_short_var.bn:83-117`) has `isManagedPtrType` / `isManagedSliceType` /
  `isManagedFuncValueType` / `isManagedIfaceValueType` cases but NO
  `needsStructCopy` arm — a managed struct/array aggregate RHS is stored raw.
  var-init and the short-var MULTI-bind arm (`:41`) both `emitStructCopy`; the
  single-bind arm is the gap.
- **Test**: `conformance/matrix/short-var/ident/managed-struct.bn` (xfailed all 6
  default modes) — observable refcount stays 1 after `tgt := src` vs the balanced 2.
- **Discovery**: 2026-06-05, P1 matrix generator (the managed-struct cell across
  forms — var-init/assign pass, short-var fails).
- **Fix**: add a `needsStructCopy(typ) { emitStructCopy(...) }` arm to
  genShortVar's single-bind path, mirroring var-init.

### ~~`for v in coll` over a managed-element collection over-releases the bound value~~ — FIXED + LANDED 2026-06-05 (binate `b0eb7299`, plan-cr-p2-2 step 3; the bind acquires via `emitStoreManagedSlot`, blank `_` skips the bind; matrix for-range-value cells + `602`)
- **Symptom**: `for v in s` where `s @[]@T` (or `[N]@T`) loads each element as a
  borrow (no RefInc) but `defineVar` registers `v` as a managed scope var, so
  scope cleanup RefDec's `v` — an unbalanced release. Per iteration the bound
  element is over-released by one; at the collection's destruction it
  double-frees. Latent because the over-release lands at v's SCOPE END (after a
  mid-function refcount read), so it surfaces only once that scope closes.
- **Root cause (CONFIRMED)**: `genForIn` (`gen_flow.bn:137-149`) emits the
  element load (a borrow) then a raw `OP_STORE` into v's slot + `defineVar` —
  no RefInc of the new value, yet v joins `ctx.Vars` and is RefDec'd at cleanup.
  The bind must acquire (RefInc / `__copy_`, the isFresh/RefInc-borrowed hybrid
  the assignment arms use) before defining v, OR v must be a non-owning borrow
  not registered for RefDec. Also covers `for i, v`, array collections, and the
  blank `_` value (a phantom scope var today).
- **Test**: `conformance/matrix/for-range-value/value/managed-ptr.bn` (xfailed in
  all 6 default modes) — `loopOnce(s)` ranges + returns, then `rt.Refcount`
  reads 1 instead of the balanced 2. Confirmed comp / int / int-int /
  comp-comp-comp.
- **Discovery**: 2026-06-05, P1 conformance-matrix authoring. Pre-existing;
  flagged suspected in plan-code-red.md §3.2/§3.4, now confirmed with a repro.

### ~~Discarded `@func`-returning call result leaks~~ — FIXED + LANDED 2026-06-05 (binate `f5410fcf`, plan-cr-p2-2 step 2; `registerManagedCallResult` at all 4 call sites + the missing `@func` arm in `emitTempCleanupBody`/`Since` + `OP_CALL_FUNC_VALUE`/`OP_CALL_IFACE_METHOD` in the isFresh predicates; matrix assign/blank/func-value + discard/stmt + `601`)
- **Symptom**: a managed `@func` returned by a call and discarded (`_ = f()`,
  or an unused call result) is never released — its closure record (and any
  captured managed values) leaks. `@T` / `@[]T` / `@Iface` / struct call results
  are registered as cleanup temps and freed; only `@func` is missing.
- **Root cause (CONFIRMED)**: `genFuncDirectCall` (`gen_call.bn:268-288`) /
  `genFuncValueCall` (`gen_call.bn:366-382`) / `gen_method.bn` register
  `@T`/`@[]T`/`@Iface`/struct results as end-of-statement cleanup temps but have
  no `isManagedFuncValueType` arm; `emitTempCleanupBody` likewise lacks the
  func-value RefDec arm, and `isFreshManagedFuncValue` omits the call ops.
- **Test**: `conformance/matrix/assign/blank/func-value.bn` (xfailed all 6
  default modes) — `_ = wrap(src)` leaves the @func record at 2 instead of 1.
- **Discovery**: 2026-06-05, P1 matrix blank-discard form. Pre-existing; flagged
  suspected in plan-code-red.md §3.4 / §8 #16, now confirmed with a repro.
- **Fix**: add the `isManagedFuncValueType` arm to the call-result temp
  registration (gen_call / gen_method) + the func-value RefDec arm in
  `emitTempCleanupBody`; add the call ops to `isFreshManagedFuncValue`.

### ~~Managed-aggregate-by-value element/field stores skip save-copy-destroy~~ — ALL SIBLINGS FIXED + LANDED 2026-06-04 — MEMORY-CORRECTNESS (was latent)
- **UPDATE 2026-06-04 (binate `32bad348`)**: the two gaps below are now
  FIXED.  The single-assign ARRAY-element aggregate arm landed; the
  multi-assign SLICE aggregate case was switched from the incomplete
  `emitStructElemRefcount` to the two-slot `emitStructCopy`/`emitStructDtor`
  form (complete for `@Iface` fields + nested aggregates), and
  `emitStructElemRefcount` was deleted.  Pinned by `conformance/583`
  (multi-assign slice element with an `@Iface` field — verified to fail
  pre-fix) and `582` (single-assign array aggregate).  All ASSIGNMENT-store
  paths (single + multi assign, IDENT/SELECTOR/array/pointer/slice) now
  save-copy-destroy correctly.  ALL SIBLINGS now also done: short-var
  multi-bind (CRITICAL, `efa4f569`), raw-pointer single-assign index (MAJOR,
  `5429a37d`), array/managed-slice/struct literals (MAJOR, `f2aff0d4`,
  including a third-sibling `@func` struct-field UAF found during that work) —
  see the (struck-through) entries below.
- **What**: when the store TARGET is a managed struct/array **by value**
  (`needsStructCopy(T)` true — a struct/array holding managed fields, NOT
  `@T`/`@[]T` which are handles), a plain store under-retains the new
  aggregate's managed fields and leaks the old's — violates "the compiler
  must NEVER generate code that leaks."  Several store paths had this gap.
- **FIXED (multi-assign `=` SELECTOR/array-INDEX/pointer-INDEX)**: binate
  `6c4d45b0` (concurrent worker) added `emitElemPtrStore`
  (`gen_assign_multi.bn`) — the save-copy-destroy via `emitStructCopy`/
  `emitStructDtor`.  Pinned by `conformance/574_multiassign_struct_aggregate`.
- **MAJOR BUG INTRODUCED by that fix — multi-assign SLICE aggregate is
  INCOMPLETE**: `6c4d45b0` routed the multi-assign managed-slice-element
  aggregate case (`gen_assign_multi.bn`, `needsStructCopy` arm) through
  `emitStructElemRefcount` (`gen_util_refcount.bn`), which RefDec/RefIncs
  `@T`/`@[]T`/`@func` fields field-by-field but **omits `@Iface` fields and
  does NOT recurse into nested aggregates**.  So `s[i], n = f()` where the
  slice element is a struct holding an `@Iface` (or a nested managed
  aggregate) field leaks the old field / under-retains the new.  `574`
  doesn't catch it — it uses a `@Counter` (managed-ptr) field only.  **Fix**:
  replace the `emitStructElemRefcount` call with the complete two-slot
  `EmitSliceGet`→`oldSlot`/`newSlot`→`emitStructCopy(newSlot)`/
  `emitStructDtor(oldSlot)` form (mirrors single-assign slice
  `gen_control.bn:391-401`, which uses the generated `__copy_`/`__dtor_`
  helpers — complete for all field kinds + nesting); then delete the now-dead
  `emitStructElemRefcount`.  Add a conformance test with an `@Iface` field in
  a slice-element struct.
- **STILL MISSING — single-assign ARRAY-element aggregate** (`gen_control.bn`
  TYP_ARRAY arm): handles the four managed scalar kinds but no
  `needsStructCopy` arm → `arr[i] = w` (managed-struct array element) leaks
  old / under-retains new.  Fix: `emitElemPtrStore(ctx, b, elemPtr, rhs,
  elemTyp)`.  (Single-assign SELECTOR + slice already complete.)
- **Severity / priority**: real memory-correctness, but **purely latent** —
  no caller in pkg/+cmd/ today (SELECTOR/INDEX multi-assign sites target
  scalar `int`; fixed-size arrays are all `[N]uint8`/`[N]char`).  Invariant-
  hardening.  See sibling entries: short-var multi-bind (CRITICAL, below),
  raw-pointer single-assign index, array/managed-slice literals.
- **Discovery**: 2026-06-03 investigation + 2026-06-04 adversarial review
  workflow; the `@Iface` slice incompleteness found reviewing `6c4d45b0`.

### ~~Short-var multi-bind `q, n := f()` does NO refcounting on bound components — CRITICAL (double-free)~~ — FIXED + LANDED 2026-06-04 (binate `efa4f569`)
- **Fixed**: `genShortVar`'s multi-bind branch now acquires each managed
  component after the store — `emitManagedValueCopyRefInc` (scalar) +
  `emitStructCopy` for `needsStructCopy` aggregates (fresh slot → no dtor) —
  mirroring `genMultiAssign`.  Pinned by `conformance/584`
  (`q := fresh @Box`, aliased into `keep`, rc must read 2; verified to fail
  pre-fix where `q` was freed at the end of the `:=` statement) + a unit
  test asserting the scalar (OP_REFINC) / aggregate (`__copy_`) acquire.
- **Original analysis retained below.**
- **What**: `genShortVar`'s multi-assign branch (`gen_short_var.bn`, the
  `len(Exprs)>1 && len(Exprs2)==1` arm) does `EmitExtract` → `EmitAlloc` →
  plain `EmitStore` → `defineVar` with **zero acquire** — neither the Axiom-3
  copy-RefInc for managed scalars (`@T`/`@[]T`/`@func`/`@Iface`) nor
  `emitStructCopy` for managed aggregates.  The extracted component is a
  borrow from the OP_CALL result temp (whose dtor RefDec's it at end of
  statement); the new var is registered via `defineVar` so its scope-exit
  dtor RefDec's it AGAIN → **0 acquires, 2 releases = double-free / UAF** for
  any managed component.  This is the exact bug `0b3f4abe` fixed for the `=`
  form (`genMultiAssign` calls `emitManagedValueCopyRefInc`), never applied to
  the `:=` short-var sibling.
- **Fix**: in the multi-bind loop, after `EmitExtract`, mirror
  `genMultiAssign`: `emitManagedValueCopyRefInc(ctx.Func, b, extracted,
  elemTyp)` for scalar components, and for `needsStructCopy(elemTyp)`
  `emitStructCopy` on the freshly-alloc'd slot (no old value → no dtor).
- **Latent**: every conformance multi-`:=` (023, 066, 288) returns scalar
  int/bool components.  Add a conformance test returning a managed scalar and
  a managed aggregate via `:=` (rt.Refcount balance) + a unit test asserting
  the acquire is emitted.
- **Discovery**: 2026-06-04 adversarial review workflow (probe-confirmed:
  short-var multi with `@Node` emits refinc=0 in `foo` vs the `=` form's 2).

### ~~Raw-pointer single-assign index `p[i] = v` does no element refcounting~~ — FIXED + LANDED 2026-06-04 (binate `5429a37d`)
- **Fixed**: the TYP_POINTER arm now mirrors the array arm — RefDec-old +
  consumeTemp-if-fresh-else-RefInc-new for the four managed-scalar kinds, and
  save-copy-destroy (`emitStructCopy`/`emitStructDtor`) for managed aggregates.
  Pinned by `conformance/589` (raw `*@Box`: old released 3->2, new acquired
  1->2; output `3` not `2` pre-fix, green all 6 modes) + unit tests
  `TestRawPtrIndexAssignManagedRefcounts` (baseline-delta) /
  `TestRawPtrIndexAssignAggregateCopies` (`__copy_`); both fail pre-fix.
- **Original analysis retained below.**
- **What**: `gen_control.bn` single-assign INSTANTIATE_OR_INDEX `TYP_POINTER`
  arm is a bare `EmitGetElemPtr`+`EmitStore` — no managed-scalar RefDec-old/
  acquire-new arms (the adjacent array arm has them) and no `needsStructCopy`
  arm.  `p[i] = v` for a managed-scalar OR managed-aggregate element leaks the
  old slot contents / under-retains the new.  The multi-assign `emitIndexStore`
  pointer arm (via `emitElemPtrStore`) IS correct, so the two forms diverge.
  The earlier "(raw = unmanaged, likely fine)" note was WRONG: the raw pointer
  only excuses keeping the *block* alive, not balancing the managed values
  *inside* the slot.
- **Fix**: give the TYP_POINTER arm the same discipline as the array arm —
  the four managed-scalar arms + `emitElemPtrStore` for the aggregate case.
  Conformance + unit test (`*Wrap` receiver).
- **Discovery**: 2026-06-04 review (probe: `p[0]=w` → copy=0, dtor=1).

### ~~Array-literal / managed-slice-literal elements don't acquire managed-aggregate fields~~ — FIXED + LANDED 2026-06-04 (binate `f2aff0d4`)
- **Fixed**: all three composite-literal constructors now acquire managed
  elements/fields.  `genArrayLit` gained the FULL acquire — it was missing
  EVERY managed-scalar arm, not just the aggregate one this entry named, so
  `[2]@Node{a,a}` under-retained too; now mirrors `genCompositeLit`
  (always-RefInc @T/@[]T, consumeTemp-if-fresh @func/@Iface, OP_CONST_NIL-
  guarded) + `emitStructCopy` for aggregates.  `genManagedSliceLit` gained the
  omitted `@func` arm + the aggregate arm (throwaway-slot `__copy_`).
  `genCompositeLit` (struct literals) gained the omitted `@func` field arm —
  the SAME closure-record UAF in a third sibling, fixed as part of the class
  (was untracked; discovered during this work).  Pinned by `conformance/590`
  (array aggregate) + `591` (managed-slice aggregate) — green all 6 modes,
  off-by-2 + double-free pre-fix — and 5 unit tests in `gen_composite_test.bn`
  (aggregate `__copy_` for array+slice; per-element acquire deltas for
  array-scalar, slice-`@func`, struct-`@func`); all fail pre-fix.  The literal
  constructors relocated `gen_access.bn` → `gen_composite.bn` (500-line cap).
- **Original analysis retained below.**
- **What**: `genArrayLit` (`gen_access.bn`) element store is a bare
  `EmitStore` with no `needsStructCopy` follow-up; `genManagedSliceLit`
  handles managed-scalar elements (and even there omits the `@func` arm) but
  has no `needsStructCopy` arm before `EmitSliceSet`.  So `[2]Wrap{w,w}` /
  `@[]Wrap{w,w}` copy the elements' managed fields by value without RefInc
  (initialization sites — no old value to release, but the new still needs
  the acquire half, as `genCompositeLit` does for struct fields).  Under-
  retain → double-free when source and element are both destroyed.
- **Fix**: `genArrayLit` — after `EmitStore`, `if needsStructCopy(elemTyp) {
  emitStructCopy(ctx.Func, b, elemPtr, elemTyp) }`.  `genManagedSliceLit` —
  add a `needsStructCopy` arm (two-slot copy of `val` before/at
  `EmitSliceSet`) AND the missing `@func` scalar arm.  Unit tests asserting
  `__copy_` count == element count.
- **Discovery**: 2026-06-04 review (probe: array/managed-slice literal
  copy=0 vs struct literal copy=1).

### ~~Divide-by-zero / mod-by-zero must panic (DEFINED) + `unsafe_div`/`unsafe_rem`~~ — DONE 2026-06-05 (binate `f3327891`, `efeb0f94`, `6852902a`)
- **What landed**: integer `/` and `%` (all widths) are now CHECKED like
  array subscripting — a zero divisor OR the signed `MIN/-1` overflow is a
  DEFINED runtime panic on every backend (was accidental: LLVM `sdiv`/`udiv`
  UB, native SIGFPE, VM host trap). Floats unaffected (IEEE).
  - `rt.DivCheck` / `rt.DivFail` runtime guards, design B —
    `DivCheck(dividend int64, divisor int64, signedMin int64, isSigned int)`,
    all compare logic in the runtime (both libc + baremetal impls) — `f3327891`.
  - `OP_DIV_CHECK` emitted before integer `OP_DIV`/`OP_REM`, lowered on all
    four backends (LLVM / aarch64 / x64 / VM `BC_DIV_CHECK`). IR-gen widens
    the operands to 64-bit once (shared, target-aware) so each backend just
    marshals two operands + the per-width MIN via `types.SignedMinForWidth` —
    `efeb0f94`.
  - Compound divide-assign (`/=`, `%=`) routed through the same shared
    `emitDivCheckGuard` (it had bypassed `genBinary`), and the
    `unsafe_div` / `unsafe_rem` opt-out builtins (truncated, NOT
    `unsafe_mod`) — `6852902a`.
- **Tests**: conformance cells (`608`/`609`/`611`–`616` after concurrent
  number clashes): `/0`, `%0`, signed `MIN/-1` at int32 & int64, `%`
  `MIN/-1`, unsigned `/0`, compound `/= 0`, and the `unsafe_div`/`unsafe_rem`
  value cell; unit tests across types/ir/codegen/vm. Verified green on LLVM
  and VM.
- **Known narrow gap** (tracked MINOR, not part of this work): a NAMED
  *signed sub-word* type's `MIN/-1` divide escapes detection because
  IR-gen's `widenType` collapses named ints to plain `int` before the guard
  — see the MINOR entry in `claude-todo.md`.
- Plan: `plan-divide-by-zero.md`. Ratified contract was `plan-code-red.md` §8 #14.

### ~~VM clobbers ≥2 distinct global addresses in one instruction (shared `globalReg`)~~ — RESOLVED 2026-06-03 (binate `d5d31b13`)
- **Was**: silent wrong-code in the bytecode VM when ONE instruction took
  two (or more) distinct global addresses as args — `return &G, &H`,
  `&G == &H`, `g(&G, &H)`.  `lower_func.bn` materialized EVERY
  `IsGlobalRef` arg into the single shared `globalReg` via consecutive
  `LOAD_IMM`s, so the second clobbered the first before the instruction
  consumed either: `return &G, &H` yielded `(&H, &H)`; `&G == &H`
  compared `&H == &H`.  Compiled backend was correct (each `@<mangled>`
  is an independent operand).
- **Fix**: reserve one global-address register per global arg of the
  widest instruction (`findMaxGlobalsPerInstr`), floored at one so a
  function with 0 or 1 global args keeps the prior frame layout exactly
  (only genuinely multi-global instructions change).  The emit pass
  assigns each global arg its own register (`globalRegBase + g`) instead
  of the single shared one.  The first-pass `LOAD_IMM` count is unchanged
  (one per global ref — only the Dst register differs), so block offsets
  still agree.
- **Tests**: `conformance/573_addr_of_two_globals_one_instr` un-xfailed —
  green in all 6 modes.  Unit `TestLowerGlobalRefMultiplePerInstr`
  (`vm/lower_func_test.bn`) pins that two globals in one instruction load
  into DISTINCT registers.  Verified: full VM suites
  `builder-comp-int` 492/0, `builder-comp-comp-int` 492/0,
  `builder-comp-int-int` 489/2 (the 2 are `136`/`383`, the pre-existing
  int-int loader bug, unrelated).
- **Discovery**: 2026-06-03, expanding the `551` `&G`-as-value test
  (above) to cover multi-global instructions exposed it.  Pre-existing;
  the simple single-global `551` never triggered it.

### ~~Conformance-test renumbering + next-free-number helper scripts~~ — DONE 2026-06-03 (binate `30a9499a`)
- **Done**: `conformance/next-number.sh` (next free NNN; default
  next-after-max, `--gap` for lowest unused) and `conformance/renumber.sh`
  (`<test> [target]` — `git mv`s the whole file fan-out: `.bn`,
  `.expected`/`.error`, every `.xfail.<mode>` / `.expected.<mode>` sidecar,
  and the multi-file `NNN_<name>/` directory; bare-number collisions list
  candidates and require a stem to disambiguate).  Scripts only, no CI/hook
  wiring.  Decided policy: next-after-max default (monotonic, never reuses a
  retired number).
- **Original spec retained below for reference.**
- **What**: two small scripts that take the manual bookkeeping out of
  conformance test numbers, complementing the existing
  `scripts/hygiene/conformance-test-numbers.sh` (which only *detects*
  duplicate numbers — it doesn't pick or reassign them):
  1. **Find next available number**: print the next free `NNN` prefix
     (decide the policy — lowest unused vs. next after the current max;
     numbers currently have gaps, e.g. max is 541 with 522/526/530–539
     unused).  Handy when authoring a new test.
  2. **Renumber a test**: given a test name/number, move it to a free
     number, renaming **all** of its files together — `NNN_name.bn` (or
     the `NNN_name/` directory for multi-file tests), the `.expected` /
     `.error` sidecar, and every `NNN_name.xfail.<mode>` sidecar.  Default
     target = next free number; allow an explicit target.  Primary use:
     resolving the duplicate-number collisions the hygiene check flags
     (e.g. when two branches both grabbed the same `NNN`).
- **Details to get right**: a test is single-file (`NNN_name.bn` + one of
  `.expected`/`.error`) OR multi-file (`NNN_name/` dir); the rename must
  carry the full sidecar fan-out (one `.xfail.<mode>` per applicable mode in
  `conformance/run.sh`).  Use `git mv` so history follows.  Only the `NNN`
  prefix changes; the `_name` suffix is preserved (unless a rename is also
  explicitly requested).
- **Scope**: add the scripts only; no CI/hook wiring (user's call).

### ~~bni VM crashes calling a non-capturing `@func` returned inside a managed aggregate~~ — FIXED 2026-06-02 (binate `d2029503`)
- **Was**: a non-capturing `@func` in the VM uses the shared per-callee
  `callee.ClosureRec` as its data slot (vs nil in compiled).
  `BC_FUNC_VALUE` borrowed that shared rec WITHOUT RefInc, but `@func`
  locals RefDec their data slot at scope end
  (`emitManagedFuncValueRefDec`, ungated — `@func` is
  `TYP_MANAGED_FUNC_VALUE` with `NeedsDestruction()==false`, so struct
  copy/dtor never touch `@func` fields, but locals always do).  So the
  first `@func` local's scope-end RefDec freed the shared rec, dangling
  any surviving copy — e.g. one returned inside a managed aggregate (the
  `ReplIO` sink `setupReplState` returns): the freed, zeroed rec reached
  `BC_CALL_FUNC_VALUE` as `data kind: 0`.  Compiled immune (nil data →
  RefDec no-op).
- **Discovery**: CI regression — cmd/bni int-mode unit tests failing
  since REPL Stage 3 (`bc70d478`) introduced the `@func` sink;
  `TestEvalReplDeclParseErrorPreservesState` first to hit it.
- **Fix**: RefInc the shared `ClosureRec` at non-capturing construction
  (`BC_FUNC_VALUE`), balancing the scope-end RefDec; honors the
  `hd[0] < 0` immortal sentinel (no-op on a sentinel rec).
- **Tests**: `conformance/528_func_value_struct_field` (all modes);
  cmd/bni passes `builder-comp-int`; full `builder-comp-int` conformance
  453/1 (the 1 is `520`, a pre-existing int-mode iface-dtor issue —
  likely the `@Iface` sibling, tracked above + MAJOR below).

### ~~x64 native: closure struct allocated in outgoing-args area — silent overwrite at call site~~ — FIXED 2026-06-02 (binate `a8a7dc7a`)
- **Final root cause** (refined from the original analysis): PlanFrame DID reserve outgoing-args at the bottom of the frame, but its sizing loop only counted `OP_CALL` and `OP_C_CALL` — `OP_CALL_FUNC_VALUE` / `OP_CALL_HANDLE` / `OP_CALL_INDIRECT` were excluded.  So func-value calls with stack-spilled user-args got an undersized outgoing-args area, and main's local-allocator placed the closure-struct local at an offset INSIDE that region.  At the call site, main wrote outgoing args (e.g. value 8 at `(rsp+0x10)`) over the closure's captured fields, and the shim later loaded the overwritten bytes as the capture (gave 53 instead of 145 = 8 + 1+2+…+9 vs 100 + 45).
- **Fix**: new helpers in `pkg/binate/native/common/common_call.bn`:
  - `callDispatchArgTypesAnyOp(cc, ins)` — dispatch-arg-type sequence for ANY call op (handles the prefix-slot prepending for func-value calls + the args[0]-skip for indirect-ptr).
  - `isCallOp(op)` — predicate over the 5 dispatch ops.
  PlanFrame uses both; the outgoing-args area is now sized correctly across all call shapes.
- **Tests**: `conformance/523_closure_many_user_args` (xfail manifest removed, now passes), plus 3 direct unit tests in `pkg/binate/native/common/common_call_test.bn`.

### ~~`&const` rejection misses qualified consts (`&pkg.C`)~~ — RESOLVED 2026-06-03 (deferral 3)
- **Was**: the `token.AMP` const-rejection handled only the unqualified
  `EXPR_IDENT` case; `&otherpkg.SomeConst` (an imported const via
  `EXPR_SELECTOR`) was silently accepted, returning a pointer to a
  storage-less value.
- **Fix**: extracted `resolveQualifiedSym` (`check_expr_access.bn`) and
  used it in the `token.AMP` branch to reject `&pkg.C` (gate on
  `SYM_CONST`).  The assignment sibling `pkg.C = v` — which had silently
  lowered to a no-op store — was the same gap and is rejected via the
  same helper in `checkAssignStmt`.
- **Tests**: `conformance/544_err_addr_qualified_const`,
  `557_err_assign_qualified_const`; `TestCheckRejectAddrOfQualifiedConst`,
  `TestCheckRejectAssignQualifiedConst`.

### ~~Redesign `pkg/binate/version` — simplify; drop the `bnc-` prefix~~ — RESOLVED 2026-06-03 (binate `b745c877`)
- **Done**: the package-private `version` (`bnc-0.0.7-pre`) is now the
  exported extern var `Version` (`0.0.7-pre`) — declared in
  `version.bni`, defined in `version.bn`.  The `bnc-` prefix is gone
  from the value; a calling tool prepends its own display name.
  `Format` and `bncPrefixLen` are removed (Format had no callers outside
  the package's own test).  `scripts/hygiene/version-sync.sh` now strips
  VERSION's `bnc-` builder prefix before comparing against the package
  literal.  The unit test (`TestVersionHasNoPrefix`) pins the no-prefix
  invariant and the VM `__init` global-read path.
- **Deliberately NOT changed**: the repo-root `VERSION` file and
  `BUILDER_VERSION` keep their `bnc-` prefix (user decision) — the prefix
  there distinguishes bnc-as-builder from the retired bootstrap
  interpreter, so it stays and the hygiene check accounts for it.
- **Public shape settled**: exported `var Version` (not an accessor),
  enabled by the now-landed `.bni` extern-var support.  No bnc-tree
  consumer imports `version` yet, so this plants no BUILDER trap (a
  future consumer would need a BUILDER that supports extern vars — the
  next snapshot after the extern-var landing).
- **Follow-up (separate)**: wire `--version` into the four tools to
  consume `version.Version` (none consume it yet) — see the `--version`
  entry below.
- **Discovery**: 2026-06-03, user request during the `.bni` extern-var work.

### ~~Type-checker can't slice a `readonly`-wrapped slice~~ — RESOLVED 2026-06-03 (deferral 1)
- **Was**: `var v readonly *[]readonly char = "..."; v[i:j]` failed
  type-checking with `cannot slice this type` — `checkSliceExpr` didn't
  see through an outer `TYP_READONLY` to the underlying slice (indexing
  already did).
- **Fix**: `checkSliceExpr` peels the outer `TYP_READONLY` (mirroring
  `checkIndexExpr`); the result is the underlying slice type — the
  subslice is a fresh value, so the outer readonly does not ride along
  (no re-wrap needed, contrary to the original proposal).
- **Also surfaced + fixed a pre-existing MAJOR IR-gen miscompile**:
  `isSliceType` / `isManagedSliceType` / `isCharSliceType` did not peel an
  outer readonly, so a string-literal init into an outer-readonly slice
  stored a bare data pointer with a garbage length word, and a
  `readonly @[]T` was mis-classified out of the RefInc/RefDec machinery.
- **Tests**: `conformance/542_readonly_slice_init`, `543_readonly_slice`;
  `gen_refcount_pred_test` outer-readonly cases;
  `TestCheckSliceExprReadonlyOuter`.
- **Note**: `version.bn` was deliberately NOT re-typed to the enforced
  `readonly *[]readonly char` — re-typing would plant a latent BUILDER
  trap once a bnc-tree consumer imports `version`; the enforced shape
  rides with the version redesign (MINOR entry above).
- **Discovery**: 2026-06-02, plan-const-readonly step 8.

### ~~`.bni` extern `var` (cross-package var export) is unsupported~~ — DONE 2026-06-03 (deferral 2)
- **Delivered**: a top-level `var X T` in a `.bni` is an extern
  declaration (storage defined in the package's `.bn`), read AND written
  cross-package as `pkg.X` for SCALAR, RAW/readonly-slice, and
  MANAGED-SLICE types, across all 6 default modes.
- **Mechanism**: the cross-package reference carries the DEFINING
  package's dotted qualname (`buildQualName`), which
  `mangle.GlobalName` → `writeBnDotted` mangles to the owner's symbol —
  the same escape hatch exported funcs use, so no `emit_util` change and
  no silent-wrong-symbol risk.  Layers: `bni_scope` (`DECL_VAR` →
  `SYM_VAR`), `checkBniVarMatch` (`.bni`/`.bn` type agreement),
  `ir.Global.IsExtern` + `gen_import` registration,
  `gen_func`/`gen_selector` (`lookupImportedGlobalPtr`/`Read`,
  `genImportedVarLvalue`), `emit` (`external global`), and the VM
  (`materializeGlobals` qualified-name keying + cross-module accumulation).
- **Tests**: `conformance/548` (read), `552` (write), `549` (.bni/.bn
  type mismatch), `558` (managed-slice + managed-ptr field); unit tests
  in `bni_scope_test`, `check_decl_test`, and `lower_test`
  (globals-accumulation isolation).
- **Tracked follow-ups** (managed/ptr edge cases, with xfail repros — see
  the MAJOR entries below): `&globalScalar` compiled (`551`), cross-pkg
  managed-ptr value-copy crash (`559`), field-write through an imported
  ptr var (`561`).
- **Plan**: [`plan-extern-var.md`](plan-extern-var.md).
- **Discovery**: 2026-06-02, plan-const-readonly step 8.

### ~~`&G` (address of a global scalar as a value) miscompiles in the compiled backend~~ — RESOLVED 2026-06-03 (binate `99655f4e`)
- **Was**: `&G` for a top-level global used as an rvalue rendered the
  IsGlobalRef pseudo (id -1) as `%v-1` (undefined) in the compiled (LLVM)
  backend — `emitPtrRef` rendered `@<mangled>` only in address-operand
  positions (load/store target, GEP base); value-operand emitters used
  `emitRef(instr.ID)` → `%v-1`.  Same-package `&G` and `&pkg.Var` alike.
- **Fix**: kept in codegen only (the IR rep is correct — the VM
  materializes the address uniformly; an IR-gen change would have forced
  VM/native changes for a bug they don't have).  Added `emitValRef` (the
  value-operand analogue of `emitPtrRef`: `@<mangled>` for an IsGlobalRef
  instr, `%vN` otherwise — a valid LLVM value operand under opaque
  pointers, no materialization needed) and routed every value-operand
  site through it: OP_STORE value, `emitReturn` (single/sret/multi), the
  call-arg paths (direct, C-call, indirect, func-value, method-handle),
  comparison operands, and the `bit_cast` source.
- **Tests**: `conformance/551_addr_of_global_scalar` expanded (one
  global per instruction across store / call arg / func-value call arg /
  single return / slice-element store / composite + assigned struct
  field) and un-xfailed — green in all 6 modes.  `conformance/573`
  covers two globals in one instruction (compiled-correct, VM-xfail; see
  the VM entry below).  Unit: `TestEmitAddrOfGlobalAsValue` (no-`%v-1` +
  `@<mangled>`).  Completeness verified by emitting a comprehensive
  probe and grepping for zero `%v-1`.
- **Discovery**: 2026-06-03, deferral-2 Slice 3 (`&pkg.Var`).

### ~~`pkg/binate/version` top-level `var` reads as `len 1` under the bytecode VM~~ — RESOLVED 2026-06-03 (binate `d903ea4b`)
- **Was**: `pkg/binate/version` unit tests failed in the `-int` modes
  (`runtime error: index out of bounds: 4 (len 1)`) — the package-private
  `var version *[]readonly char = "bnc-..."` read as a zero (len-0)
  slice, so `version[bncPrefixLen:]` aborted.  Surfaced when step 8
  migrated `version` `const`→`var` (a `const` needs no `__init`).
- **Root cause** (NOT the global-read/init lowering first guessed):
  `cmd/bni`'s `--test` mode (`runTests`) lowered all packages but never
  built or invoked the package `__init` dispatcher — unlike the run path
  (`EmitInitDispatcher`), which a test program lacks (no `main.__entry`).
  So a top-level `var` initialized by `<pkg>.__init` read its zero value
  under the `-int` unit harness.  (Conformance `main` programs were
  unaffected — they run through `main.__entry`, which dispatches inits.)
- **Fix**: `runTests` collects each lowered package's `__init` (where
  `HasPackageInit`, loader dep order) and invokes it via
  `vmInst.CallFunc` before the `Test*` functions.
- **Verified**: `pkg/binate/version` passes in `builder-comp-int` /
  `-comp-comp-int`; full `builder-comp-int` unit suite 37/0.  Guarded by
  version's existing `-int` tests (`TestVersionStartsWithBncDash` reads
  the global) — red before, green after.  The version-redesign MINOR
  entry above should keep a global-var `-int` test as the durable guard.

### ~~Perf-tests CI lane fully red — `println(int)` programs fail to link `bootstrap.formatInt64`~~ — FIXED 2026-06-02 (binate `22b2c897`)
- **Confirmed root cause**: the perf runners were never updated for the iface/impl pkg-layout split.  Compile runners (`builder-comp`, `-comp-comp`, `-comp-comp-comp`, `native_aa64`) passed only `-I "$src_dir" -L "$src_dir"` (= `perf/`); `println(int)` lowers to a `bootstrap.formatInt64` call, but `perf/` has no `pkg/bootstrap`, so int-printing tests (`001_fib`, `002_many_funcs`) failed to link.  Interp runners (`builder-comp-int`, `-comp-comp-int`, `-comp-int-int`) passed bare `-I "$BINATE_DIR" -L "$BINATE_DIR"`, missing the `ifaces/`+`impls/` entries — so even `000_noop` failed (cmd/bni / the test couldn't resolve a stdlib package).
- **Fix**: every runner now uses the conformance/unit canonical set, rooted at `$BINATE_DIR` (where `pkg/bootstrap` + `pkg/binate/*` live) plus `ifaces/core:ifaces/stdlib` (-I) and `impls/core/{common,libc}:impls/stdlib/common` (-L).  The int-int runner applies it to both the outer (compiled-bni→cmd/bni) and inner (cmd/bni→test) invocations.  `$src_dir` dropped from compile runners (perf tests are single-file `main` packages importing only stdlib).
- **Verified locally**: `builder-comp` 3/3, `builder-comp-int` 3/3, `builder-comp-int-int` 3/3 (all `000_noop` + the two int-printing tests PASS; previously every mode was red).  Note: `001_fib` under `builder-comp-int-int` runs ~228s (inherent double-interpretation cost of a recursive program) — correct; kept as-is per user decision (no per-mode skip).

### ~~conformance/520_iface_dtor_callee_sole_ref fails on both native lanes~~ — FIXED 2026-06-02 (binate `394ef21b`)
- **Final root cause**: the native impl-vtable emitter (`emitImplVtableLayoutNative` in aarch64, `emitImplVtableLayout_x64` in x64) stored the raw dtor fn pointer in slot 0 of the impl vtable.  The LLVM-side codegen had been updated in binate `dc46ac7f` (Jun 1) to store the dtor HANDLE pointer there instead — required by `_call_dtor`'s OP_CALL_HANDLE lowering, which dereferences slot 1 of the value to get the call fn and slot 0 to get the vtable.  Passing a raw fn pointer through that path BLR's the function's first instructions as a {vtable, data} struct, SIGSEGV.  Native side missed the corresponding update; surfaced when 520 (added in `dc46ac7f` precisely to catch this) was run on the two native lanes.
- **Fix**: switch both native emitters to reference `handleSymFor` / `handleSymFor_x64` (the `___handle.<mangled-dtor>` global), matching the LLVM-side `@__handle.<mangled-dtor>` emission.
- **Companion fix LANDED**: `521_managed_func_value_propagation`, which surfaced alongside 520, was a separate gap — OP_FUNC_VALUE_DTOR silently NOPing in the native dispatchers — fixed in binate `d014b559` by mirroring the OP_IFACE_DTOR handler.
- **Coverage**: conformance/520 end-to-end + direct unit test `TestEmitImplVtableSlot0IsHandleNotRawFn` in `pkg/binate/native/aarch64/aarch64_iface_test.bn` pinning slot 0 = handle symbol, NOT raw fn symbol (binate `fe233126`).  All three lanes now match at 454/0/1.
- **The discovery story** (kept because the disasm walk was nontrivial): lldb showed the crash in `_call_dtor` at `ldr x8, [x8, #0x8]; blr x8` — the OP_CALL_HANDLE dispatch reading slot 1 of x8.  Initial trace through `makeFoo`'s return suggested the @Foo's data slot was being trashed (sp+0x58 held a code address at return).  That was a red herring — the "code address" was actually the raw dtor fn pointer being LOADED FROM the impl vtable's slot 0, which my mental model assumed was a heap data pointer.  Looking at the actual emitter (rather than chasing the symptom in the stack frame) revealed the slot-0 divergence between LLVM and native.

### ~~bnc codegen: byval-spill alloca emitted at call site leaks per loop iteration — `*-int*` unit-test modes overflow~~ — FIXED 2026-06-02 (binate `440485b0`)
- **Root cause**: `writeByvalArgPreamble` (`pkg/binate/codegen/emit_util.bn`) emitted the per-byval-arg `alloca <T>` at the CALL-SITE basic block, not the function entry block.  LLVM allocas outside the entry block allocate fresh stack each time the block runs and aren't reclaimed until function return, so a call passing a >16-byte struct by value from inside a loop leaked one spill slot per iteration.  bni's `execLoop` passes a 48-byte `BCInstr` by value to ~13 helper calls (`execStringOp`/`execFuncRefOp`/`execMemoryOp`/`execArithOp`/...) per dispatch iteration; at ~165K iterations the 8 MiB default host stack was exhausted and the next call's prologue faulted (`EXC_BAD_ACCESS` / SIGSEGV).  lldb showed only ~5 native frames with SP ~8 MB below FP — accumulated per-iteration leaks within one `execLoop` frame, NOT recursion (the earlier extern-callback-recursion hypothesis was wrong).
- **Pre-existing**: the `Unit tests` CI workflow had been red for 982+ runs (since 2026-05-18).  The team had already hand-hoisted one analogous leak (`callArgs` in `execLoop`); the emit_util.bn comment had even predicted this one.
- **Fix**: split the preamble.  `writeByvalArgPreamble` now emits only the `store`; new `emitByvalAllocDecls` emits the `alloca` in the entry block, hooked into `emitFuncDbg`'s alloca-hoist pre-pass (alongside OP_ALLOC / OP_MAKE_SLICE / sret).  Slot names `%v<callID>.bv<i>` are a pure function of (instr.ID, arg index) so the entry alloca and call-site store agree without extra plumbing.  Same change removed the `ulimit -s 65520` band-aid from the three bni-using runners (added in `1f2dc9b4` / `c132324a`).
- **Verification** (default 8 MiB stack, band-aid removed): `execLoop` 14 → 0 dynamic stack-adjustments; `pkg/binate/types` 527/527 (was crash after test #1); `builder-comp-int` 34/0 (was 24/10 even WITH the band-aid); `builder-comp-comp-int` 30/0/4xfail; all 24 previously-crashing packages green; conformance `builder-comp` 450/0/1.  Regression test `TestByvalSpillAllocaHoistedToEntry` (`emit_helpers_test.bn`) pins "no alloca in/after for.body for a byval call in a loop."
- **Follow-up — also FIXED 2026-06-02 (binate `d9800429`)**: the same call-site-alloca class on the func-value-call (`.ap<i>` aggregate args + `.rb` retbuf) and iface-method (`.rb` sret) paths was hoisted too (latent — no package triggered it, but same shape).  Details in `plan-codegen-byval-spill-hoist.md` (archived — see `historical-notes.md`).

### ~~arm32_baremetal: pkg/native/{aarch64,x64} test binaries overflow `.bss` region~~ — FIXED 2026-05-30 (binate `b0c64b14`)
- **Final fix**: combined option-(a) + xfail-manifest-rename:
  - `runtime/baremetal_arm32/baremetal.ld`'s `LENGTH` bumped from 8 MiB to 16 MiB — the runner already launched QEMU with `-m 16M`, so the linker was underusing available memory.  Both `.bss` overflows clear with headroom.
  - Stale xfail manifests `pkg-native-arm64.xfail.…` and `pkg-native-amd64.xfail.…` renamed to `pkg-native-aarch64.xfail.…` / `pkg-native-x64.xfail.…` (the packages were renamed away from `arm64`/`amd64` per the mangler-bug story in CLAUDE.md but the manifests were left at the old names and stopped applying).  Restores the original author intent ("tests require host filesystem / subprocess / native-host arch") so the file-write tests (TestArm64FormatSelectsWriterAndPrefix, TestEmitObject*) that the .bss-overflow had been masking get skipped cleanly.
- **Followup uncovered**: `pkg/builtins/lang.TestInt32StringNegative` ("int32 INT_MIN unexpected") fails on arm32_baremetal but passes on arm32_linux.  Both targets are ILP32 with the same `Itoa` implementation; LLVM IR for the `cast(uint64, int32)` is identical (`sext i32 to i64`).  Tracked as a separate bug below.

### ~~runtime/baremetal_arm32 pkg/bootstrap.Itoa: stale int-only impl miscompiles INT_MIN~~ — FIXED + VERIFIED 2026-06-02 (binate `756209e2`)
- **Symptom**: `pkg/builtins/lang.TestInt32StringNegative` fails with "int32 INT_MIN unexpected" on `builder-comp_arm32_baremetal` — `x.String()` for `var x int32 = -2147483648` returns just `"-"` (length 1, no digits).  `Itoa(-INT_MIN)`'s digit-count loop runs 0 iterations because the magnitude variable ends up 0.
- **Discovery**: 2026-05-30, instrumented test under QEMU semihosting after the pkg/native xfail-manifest rename made the lane reach pkg/builtins/lang as a non-xfail.
- **Root cause** (NOT a compiler bug — earlier "target-specific IR-gen divergence" reading was wrong): `--target arm32-baremetal` swaps in a separate source file at `runtime/baremetal_arm32/pkg/bootstrap/bootstrap.bn` whose `Itoa` was duplicated from libc-host BEFORE the int-min fix (commit `8b94bf6b`) landed.  That fix moved libc-host `Itoa` to `uint64` magnitude; the baremetal copy still does `v = 0 - v` directly on `int`, which overflows at INT_MIN and leaves `temp` still negative — the digit-count loop never runs.  The "IR diff between `--target arm32-linux` and `--target arm32-baremetal` on `pkg/bootstrap`" was real but trivial: different files.  `formatInt`/`formatInt64` in the baremetal copy WERE updated for int-min (commit `38f9319c`); only `Itoa` slipped.
- **Fix**: port the libc-host uint64-magnitude `Itoa` to the baremetal copy.  1:1 textual port, no semantic change vs the libc-host version.  Long-term followup (already noted in the file's header): extract these format helpers into a shared `pkg/format` so the duplicate goes away entirely (also true for `formatInt`, `formatInt64`, `formatUint`, `formatBool`, `formatFloat`).
- **Status**: LANDED on main as `756209e2` and VERIFIED 2026-06-02.  The separate link-error blocker (missing `__aeabi_memcpy` etc.) is itself fixed (binate `af8f4683`), so the lane now reaches + passes this test: `builder-comp_arm32_baremetal pkg/builtins/lang` runs 26/26 green, including `TestInt32StringNegative` (`impls/core/common/pkg/builtins/lang/lang_test.bn:62`, asserting `int32(-2147483648).String() == "-2147483648"`).  No xfail manifest for the package on that lane.

### ~~runtime/baremetal_arm32: missing `__aeabi_memcpy` / etc. aliases~~ — FIXED 2026-06-01 (binate `af8f4683`)
- **Different resolution than originally proposed**: instead of adding the AEABI aliases on the runtime side, the compiler was changed to not *emit* the symbols in the first place.  Plan: `explorations/plan-codegen-c-free-copies.md`, landed across 5 commits (steps 1–5).  No bnc-emitted LLVM IR now contains `@llvm.memcpy.*` intrinsics, `@bn_pkg__libc__Memcpy` calls, aggregate `store <T> zeroinitializer`, or aggregate `store <T> %v, ptr` — the LLVM ARM EABI backend has no aggregate-copy lowering opportunity, so no libc memory primitive is referenced.  pkg/binate/buf, pkg/builtins/lang, conformance/064 + the other surfaced cases all link cleanly on `builder-comp_arm32_baremetal` (20/0/14 unit, 437/1/11 conformance — the 1 remaining conformance failure is unrelated, see below).
- **Followups still on the plan** (steps 6, 7):
  - Native backends (`pkg/binate/native/x64`, `pkg/binate/native/aarch64`) still emit assembly that calls `bn_pkg__libc__Memcpy` (rodata→stack/managed-slice paths).  These don't go through pkg/codegen so step 5 doesn't touch them; needs its own audit + fix.
  - Aggregate `OP_LOAD` still emits `load <T>, ptr <p>` for aggregate T.  In practice the LLVM ARM EABI backend doesn't lower aggregate-LOAD to memcpy the way it does aggregate-STORE, so the baremetal lane went green without addressing this.  But for full hygiene, OP_LOAD should also fieldwise-decompose (insertvalue chain to build the aggregate result) — defer until measured to matter.

### ~~conformance/512_opaque_handle_cross_pkg: baremetal failure on local-escape UB~~ — FIXED 2026-06-02 (binate `f152e6cc`)
- **Symptom**: `builder-comp_arm32_baremetal` conformance run, test `512_opaque_handle_cross_pkg`: expected output `42`, actual `1090518960` (or similar — looks like an address).  Other modes (`builder-comp`, gen2, etc.) passed; only baremetal failed.
- **Root cause**: the test exercised `return &local` for an opaque type — `var h Handle; ...; return &h`.  That is **UB** (the stack frame is destroyed at return).  On LP64 host-libc builds the stack slot happened to retain the value long enough for `Get(handle)` to read it back; on arm32-baremetal the semihosting `Write` / `Exit` calls (and the bump-allocator / debug paths between New and Get) clobber the stack region, so Get read garbage.  Pre-existing — verified by reproducing on the prior commit (62f4fb0c) without the C-free-copies step-5 change; the test was simply never xfail-marked.
- **Fix**: rewrite the test so `New` heap-allocates via `make(Handle)` and returns the resulting `@Handle`.  This makes the lifetime well-defined on every target while still exercising the opaque-type-cross-package property the test is actually about (which is independent of stack-vs-heap).
- **Why not the alternatives** (both explicitly rejected): (a) xfail on baremetal — a conformance test must not rely on UB even where it *happens* to work, so accepting the UB on the green lanes was wrong; (b) make `&local` escape-aware (auto-heap-allocate escaping locals, Go-style) — runs counter to Binate's core philosophy.  Binate is **transparent** about what is allocated and where; stack vs. heap is source-determined (`var` vs `make`), not an optimizer's decision.  Implicit escape-analysis heap-allocation is exactly what Binate is *not* (this is the opposite of Go, where stack-allocation is an invisible optimization).


### ~~pkg/vm test binary crashes silently on arm32_linux after universal-sret commit~~ — FIXED 2026-05-29 (binate `cde84e86`), confirmed 2026-06-02
- **Symptom**: under `builder-comp_arm32_linux`, the `pkg/vm` unit-test binary built successfully but the run produced zero test output and the runner reported `FAIL: pkg/vm [8s]` — crash before the first test ran.  LP64 (`builder-comp`) ran all tests green.  Bisected: `22a55e49` PASS → `5331235e` (force universal sret for >16-byte aggregate returns) FAIL.
- **Final root cause** (the original "multi-return frame corruption" hypothesis was wrong — multi-returns are consistently *non-*sret on both ends, so LLVM picks one convention for the matching def+call and they agree): `5331235e` made the function-**definition** sret decision target-aware (`needsSret`, threshold 4 bytes on arm32 vs 16 on LP64) but left the **func-value** (`funcValueUsesSret`) and **iface-method** (`emitCallIfaceMethod`) *call-site* sret decisions at a hardcoded `> 16`.  On arm32 any aggregate return in the 5..16-byte window — raw slices (`%BnSlice`, 8 B), 2-field structs, etc., which are pervasive — was *declared* sret (`define void @f(ptr sret(%T) …)`) but *called* register-style (`%r = call %T @f()`).  Caller and callee disagree on the hidden indirect-result pointer: the callee writes the aggregate through an sret pointer the caller never set up, corrupting the stack and faulting before the first test.  Only arm32 was affected because LP64's 16-byte threshold never routes a 2-word return through sret, so its def and call stayed in agreement.
- **Fix**: binate `cde84e86` "pkg/codegen: target-aware aggregate-return + func-value sret thresholds" routed the func-value and iface-method call-site thresholds through `needsSret` too, so the definition and *every* call path (direct via `lookupIsSret`, iface via `emitCallIfaceMethod`, func-value via `funcValueUsesSret`) agree on each target.  `OP_CALL_HANDLE` (only `_call_dtor`/`_call_free_fn`, void return) and `OP_CALL_INDIRECT` (`_call_shim_aggregate` returns its aggregate through an explicit buffer-pointer arg, not an LLVM `sret` attribute) carry no sret-annotated aggregate return, so they needed no change.
- **Confirmation**: CI *Unit tests* run `26845826907` (2026-06-02): the `builder-comp_arm32_linux` job logs `PASS: pkg/binate/vm (157 passed) [11s]`, lane summary `35 passed, 0 failed, 0 xfail, 0 skipped` (pkg/vm moved to `pkg/binate/vm`; 153→157 tests).  `builder-comp_arm32_baremetal` also green.  Independently re-verified by emitting `--target arm32-linux` LLVM IR for 2-field-struct + raw-slice returns: def and call both use `ptr sret(...)`.
- **Coverage added**: `TestNeedsSretIsTargetAware` (`pkg/binate/codegen/emit_types_test.bn`) pins the foundation threshold host-runnably — an 8-byte aggregate srets on arm32 (> 4) but not LP64 (≤ 16); a 4-byte aggregate srets on neither; a managed-slice (>16 B) srets on both.  This runs on *every* lane (incl. amd64), so a regression in the CI-only arm32 path can't slip through silently again.  Complements the pre-existing `TestFuncValueUsesSretIsTargetAware` (func-value path).

### ~~`__c_call` Stage 4 (variadic in the native backends)~~ — DONE 2026-06-02 (binate `62ae438f`)
- **Resolution**: variadic `__c_call` works on both native lanes.  darwin-arm64 forces every vararg onto the stack via `AAPCS64_Darwin`'s `VariadicStackOnly` + the `CallArg*V` arg-dispatch helpers keyed on `ins.CFixedArgs`; amd64-SysV sets `AL = 0` for integer-only varargs before the `CALL`.  The implementation landed via the universal-sret series (which fixed the underlying convention mismatch — `InternalSretBytes` 64→16 — and the second-trailing-bool field-offset miscompile) plus the pkg-layout migration; this session verified + finalized it (added multi-vararg coverage, removed the stale WIP/KNOWN-BUG comments).
- **Verified**: `conformance/498` (non-variadic), `500` (single vararg), and `527` (multi-vararg `printf("%d %d %d\n", 11, 22, 33)`) are all green on builder-comp (LLVM) + native aa64 + native x64; native aa64 full sweep 456/0/1.  The earlier "KNOWN BUG: 498 and 500 can't both be green simultaneously" was a transient artifact of the convention mismatch and no longer holds.
- **Out of scope / future**: float varargs (amd64 `AL` = actual vector-reg count used; the V-variant + AL=0 path is integer-only) and the native arm32 backend (500/526 stay xfailed on arm32).  The `stage-4-wip-broken` branch on the `temp-binate-2` worktree is obsolete and can be deleted.
- Full investigation history (the two distinct native-aarch64 bugs, the InternalSretBytes analysis, the disasm walks) is preserved in git history and summarized in `plan-c-call.md` §6–§7.

### ~~LLVM codegen: `&global` as an interface-value data pointer emits `%v-1`~~ — FIXED 2026-05-26 (binate `a2d84c0`)
- **Was**: constructing an interface value from the address of a
  package-level global — `var iv *Greeter = &g` where `g` is a global
  struct — emitted an invalid data-pointer operand (`%v-1`, no SSA id
  for the global's address) and clang rejected the module.  Loud, not
  silent.  Both the LLVM and native paths now materialize the global's
  address correctly; conformance/495_iface_construct_from_global passes
  in all modes (its xfails are gone).

### ~~CI: bump artifact actions off deprecated Node 20~~ — DONE 2026-05-26 (binate `665c198`)
- `actions/upload-artifact@v4` / `download-artifact@v4` ran on Node
  20 (deprecation flagged on every artifact step of the bnc-0.0.2
  release run; GitHub forces Node 24 on 2026-06-02, removes Node 20
  from runners 2026-09-16).
- Bumped the 4 uses — `release.yml` + `perf-tests.yml` — to
  `upload-artifact@v7` / `download-artifact@v8` (both node24).
  Params we use (`name`, `path`, `if-no-files-found`,
  `retention-days`, `pattern`) are stable across the bump; v8's
  "direct download" skip-unzip path only triggers for
  `archive:false` uploads, which we don't use.  `checkout@v6` /
  `setup-go@v6` were already node24.
- Not yet exercised by an actual run; the next Release or perf run
  will confirm the deprecation warnings are gone.

### ~~bnc: managed local inside a `switch case` body miscompiles~~ — FIXED 2026-05-25 (binate `4306197`)
- **Was**: `genSwitch` generated case bodies with a bare `genStmt`
  loop and no variable-scope boundary (unlike `genBlock`, which
  saves/restores `ctx.Vars` and emits `emitDecForScopeVars` at scope
  exit).  A managed local declared in a `case` body lingered in
  `ctx.Vars` for the rest of the function and was RefDec'd on every
  later exit path — sibling cases and the switch's fall-through
  `return`s.  On those paths the local's alloca held a stale value
  from an earlier call that DID run the case (slot reuse), so the
  spurious RefDec freed a still-live backing → heap corruption.
  The VM tripped it hard: `execStringOp` runs for every bytecode
  instruction, and the `return false` path RefDec'd a stale
  `@[]char` slot (silent SEGV / empty output, ~340 builder-comp-int
  failures).
- **Fix**: extracted `genCaseBody`, mirroring `genBlock` — per-case
  var-scope save/restore + `emitDecForScopeVars` on normal fall-off.
- **Pinned by**: conformance/489_switch_case_managed_local_scope
  (SEGV pre-fix, correct post-fix).  Workaround removed:
  `pkg/vm/vm_exec_helpers.bn:execStringOp` now has all dispatchers
  in `switch` form.

### ~~Move `pkg/math/big` → `pkg/std/math/big`~~ — DONE 2026-06-03 (binate `fce2da76`)
- `math/` is a stdlib namespace and belongs under `pkg/std/` with the other
  tier-1 packages, not at a bare `pkg/math/`.  Pure path move (ifaces + impls +
  the strconv import + comments + the conformance-imports whitelist + the
  explorations docs); no code change.  big's tests and the strconv
  cross-package consumer (535) stay green.

### ~~Float-literal converter: long-mantissa + huge-exponent overflow~~ — DONE 2026-06-03 (binate `26771993`)
- **What was wrong** (found by a coverage review of `5281b138` below): the
  significand was accumulated into a uint64, so any literal with > ~19
  significant digits — or a value ≥ 2^64 written out (`3.14…279`,
  `18446744073709551616.0`) — overflowed to arbitrary wrong bits; the worst
  case (value ≡ 0 mod 2^64) collapsed to **+0.0**.  The lexer keeps every
  literal digit verbatim, so it's reachable from source.  Separately, the
  decimal exponent accumulated into `int` with no guard, so a huge exponent
  (`1e4294967296`) wrapped on a 32-bit target → finite wrong value not +Inf/0.
- **Fix**: accumulate the significand DIRECTLY into the 128-bit window (×10 +
  digit + renormalize-right with sticky), so the round bit is always exact and
  only sub-2^-128 tails are sticky (no double-rounding); saturate the exponent
  and short-circuit unambiguous over/underflow.  `parseLargePos/NegExpToBits`
  became `applyPos/NegPow10` over the accumulated window.
- **Verification**: the algorithm matched `strconv.ParseFloat` across 800k
  cases (1..60-digit mantissas, full exponent range incl. saturation, signed,
  all boundaries).  New unit goldens (many-digit, 2^64 write-out, huge-exp), an
  `Itoa(INT_MIN)` case, and `conformance/537_float_lit_many_digits`; green on
  builder-comp, the VM, and gen2.
- **Review non-findings**: digit separators (`1_000.0`) and hex floats
  (`0x1p4`) are not reachable as FLOAT tokens — no parser change needed.
- **ILP32 follow-on — DONE (binate `55d324f1`)**: a focused review of the
  rewrite found the exponent-accumulation cap (`if expVal < 1000000000`)
  admitted a multiply up to ~1e10, overflowing a 32-bit `int` on arm32 (e.g.
  `1e4294967296` wrapped to a finite wrong double instead of +Inf) — invisible
  on the 64-bit test host.  Lowered the cap to `< 100000000` (max post-multiply
  999999999 < INT32_MAX); verified ILP32-correct via a simulated-int32
  accumulation vs strconv.  Added coverage the rewrite missed: lexer dot-forms
  (`1.e3`/`.5e3`), the largest-denormal→smallest-normal round-up boundary, the
  saturation boundary, and a `1e2147483648` int32-boundary guard.

### ~~Float literal text→bits conversion wrong for large-exponent + signed literals (536 + native 541 case 1)~~ — DONE 2026-06-03 (binate `5281b138`)
- **What was wrong**: a float literal's value rides the IR as text
  (`OP_CONST_FLOAT.StrVal`), parsed per-backend.  Two parsers were broken:
  the VM's `parseFloatLit` computed `10^exp` by repeated float64 multiply
  (imprecise — `1e100` a few ULP off, = conformance 536), and the native
  backends' `common.ParseFloatLitToBits` computed `mantInt * 10^netExp` in
  uint64 which **overflowed for |value| ≥ ~1e20** (silent miscompile on every
  native backend, latent because no native test used large-exponent literals).
  It also dropped a leading `-` in const-folded negative literals (541 case 1).
- **Fix**: one correct integer-only converter — a 128-bit (hi:lo) mantissa
  window, decimal `10 = 5×2` factored into a binary exponent (5^n via 128-bit
  ×5/÷5 chunking, 2^n via the exponent), 53-bit significand rounded
  nearest-even with a sticky bit, plus leading-sign handling.  The VM now
  routes `OP_CONST_FLOAT` through `common.ParseFloatLitToBits`, so the constant
  is identical across LLVM, the VM, and all native backends; the VM's own
  parser was deleted.
- **Verification**: a line-for-line Go transcription matched
  `strconv.ParseFloat` across 701k cases (1eN..1e308, 1e-N..smallest denormal,
  tie-prone integers, random mantissas, signed).  536 un-xfailed, green on all
  6 default modes; 541 green on the VM modes; full conformance clean on
  LLVM/gen2/gen3 + the VM modes (modulo the pre-existing 520 -int red).
- **Still open**: 541 case 2 (native float-function-return ABI reads 0) and the
  aa64 self-host link failure remain in [claude-todo.md](claude-todo.md).
  Possible cleanup: `common.ParseFloatLitToBits` is now shared by the VM too,
  so it arguably belongs in a neutral layer rather than under `native/`.

### ~~`pkg/std/math/big.Nat` + `strconv` float formatting (Dragon4 dtoa)~~ — DONE 2026-06-03
- Plan: [`plan-strconv-float.md`](plan-strconv-float.md) (now marked COMPLETE).
- **What landed**: `pkg/std/math/big.Nat` — a complete ILP32-correct
  arbitrary-precision unsigned integer (Add/Sub/Mul/MulUint32/Shl/Shr/
  DivMod via Knuth Algorithm D / DivModUint32 / Cmp / BitLen / …). On top
  of it, `pkg/std/strconv.AppendFloat`/`FormatFloat`: a Dragon4 /
  Burger-Dybvig dtoa over `Nat`, both shortest-round-trip (`prec < 0`) and
  fixed precision (`prec >= 0`), for `'f'`/`'e'`/`'E'`/`'g'`/`'G'` (f32 and
  f64). Refactored layout into a shared `renderDigits`.
- **Verification**: a line-for-line Go transcription of the algorithm
  matched `strconv.FormatFloat` (go1.26.3) across 208k cases (random
  doubles + structured edges + prec to 120); two adversarial multi-agent
  reviews found 0 bugs (only coverage gaps, all closed). Green on
  builder-comp / VM / gen2 and arm32; cross-package
  `conformance/535_strconv_float_cross_pkg`.
- **Open follow-ups** (intentionally out of scope; see the plan): signed
  `Int` wrapping `Nat` (to replace `pkg/binate/bignum`); `'b'`/`'x'` float
  formats; `println` rewiring off `bootstrap.formatFloat`. The VM
  large-exponent float-*constant* defect found during testing stays in
  [claude-todo.md](claude-todo.md) (`conformance/536`).

### ~~Drop `pkg/libc` via `__c_call` in `rt`~~ — DONE 2026-06-03 (binate `e56e4d0c` + `aa017052`)
- Plan: [`plan-rt-ccall-drop-libc.md`](plan-rt-ccall-drop-libc.md)
  (Approach A — native-only rt — chosen over the BC_C_CALL opcode; rt
  is fundamental enough to mandate "rt must be native", and the future
  is package-level native registration via the `_Package` infra).
- **What landed**: deleted `pkg/libc` + `runtime/libc_stubs.c`. The
  libc-host rt reaches the C allocator + exit directly via
  `__c_call("malloc"/"calloc"/"free"/"exit", ...)` (free/exit use a
  dummy `int` return, discarded — see the void-return follow-up todo).
  rt is now NATIVE-ONLY in the VM: `cmd/bni` no longer lowers
  `pkg/builtins/rt` to bytecode; `rt.X` resolves through the registered
  native externs (like the C-shaped `pkg/bootstrap` surface).
  `registerRtExterns` gained the two previously-missing entries (Exit,
  ZeroRefDestroy), pinned by a new assertion in `extern_register_std_test`.
- **No BUILDER bump**: verified `bnc-0.0.6` compiles `__c_call`;
  `findLibcStubs` already no-ops when `libc_stubs.c` is absent.
- **Tests**: rt's own bytecode unit tests are xfailed in the `-int`
  modes (rt is native-only); compiled modes + every other `-int` test's
  native-rt calls cover rt's behavior.  Verified clean across
  builder-comp / -comp-comp / baremetal (zero new failures; the `-int`
  failures — 520, 136/383, pkg/binate/version — are all pre-existing on
  main, confirmed by baseline runs).
- **Conflict note**: the concurrently-landed VM-mode `_Package` work
  (`feadde2c`) had registered `libc._Package`; the rebase resolution
  dropped that orphaned registration.

### ~~`pkg/slices` → `pkg/stdx/slices` (tier-1x layout move)~~ — DONE 2026-06-02 (binate `a79b698a`)
- **Moved**: `pkg/slices.bni` → `ifaces/stdlib/pkg/stdx/slices.bni`;
  `pkg/slices/slices.bn` + `slices_test.bn` →
  `impls/stdlib/common/pkg/stdx/slices/`, mirroring the existing
  pkg/std/strconv split-tree placement.  `package "pkg/slices"` →
  `"pkg/stdx/slices"` and every `import "pkg/slices"` →
  `"pkg/stdx/slices"` (79 sites across cmd/bnc + pkg/binate/*); the
  local qualifier stays `slices.` (trailing segment unchanged).
- **No release dance**: slices is fully generic (monomorphized per call
  site) so it emits no standalone symbols, and BUILDER carries no
  compiled-in literal referencing pkg/slices.  Direct cherry-pick, no
  BUILDER bump.
- **Verified**: `scripts/unittest/run.sh builder-comp` → 37 passed,
  0 failed; the `slices` filter resolves the moved package and its tests
  pass; hygiene 12/12.

### ~~`pkg/bignum` → `pkg/binate/bignum` (tier-2 layout move)~~ — DONE 2026-06-02 (binate `c94c893a`)
- **Moved**: `pkg/bignum.bni` → `pkg/binate/bignum.bni`;
  `pkg/bignum/{bignum,bignum_test}.bn` → `pkg/binate/bignum/`
  (collocated under the `binate` org slot).  `package "pkg/bignum"` →
  `"pkg/binate/bignum"` and every `import "pkg/bignum"` →
  `"pkg/binate/bignum"` (pkg/binate/types); affected import groups
  re-sorted to stay alphabetical.  Local qualifier stays `bignum.`.
- **Scope note**: bignum's only consumer is cmd/bnc's type checker
  (compile-time const arithmetic), so tier-2 under pkg/binate/ is honest
  about scope.  If an external consumer ever wants general-purpose
  bignum, promote to pkg/stdx/bignum then — don't pre-position.
- **No release dance**: pure-Binate package; its own .o defines the
  renamed `bn_pkg__binate__bignum__*` symbols and callers recompile.
  BUILDER carries no literal for pkg/bignum.  Direct cherry-pick.
- **Verified**: `scripts/unittest/run.sh builder-comp` → 37 passed,
  0 failed (moved package discovered + green); `conformance/run.sh
  builder-comp const_fold` → 6 passed, 0 failed; hygiene 12/12.

### ~~`len()` on a bare string literal mis-lowers the literal (silent wrong value on the VM)~~ — FIXED 2026-06-02 (binate `a842b691`)
- **Was**: `len("true")` — a string literal used *directly* as the `len`
  operand, with no slice-typed coercion target — did not produce 4.  A
  bare literal lowers to `OP_CONST_STRING` (a `*readonly uint8` data
  pointer), not a `{ptr,len}` slice, but the `len` IR handler fell through
  to `EmitSliceLen`, whose field-1 extract was invalid IR on compiled
  backends (`extractvalue i8* %v, 1`) and a silent garbage read on the VM.
  Every other slice-consuming site promotes `OP_CONST_STRING` via
  `EmitStringToChars`; `len` was the lone omission.
- **Fix**: fold `len` of an `OP_CONST_STRING` to a compile-time
  `EmitConstInt(len(StrVal))`, mirroring the existing `TYP_ARRAY ->
  EmitConstInt(ArrayLen)` fast-path in the same handler
  (`pkg/binate/ir/gen_expr.bn`).  The parallel `gen_call.bn` len-branch is
  unreachable dead code — `len` is keyword-dispatched (`parse_primary.bn`
  → `EXPR_BUILTIN`), never an `EXPR_CALL`, confirmed by a sabotage probe
  (full conformance green with that branch returning a sentinel) — but was
  kept in sync defensively.
- **Test**: `conformance/533_len_string_literal` (passes all modes;
  covers empty / single / multi-char, an escaped literal, and an
  arithmetic context) + an IR unit test in `pkg/binate/ir/strings_test.bn`
  asserting the fold to `OP_CONST_INT` rather than an extract over
  `OP_CONST_STRING`.

### ~~Top-level `const ( ... )` group members in a `.bn` file not visible across files in the same package~~ — FIXED 2026-06-02 (binate `88c9c0b7`)
- **Was**: `collectDecls` (the forward-reference collection pass in
  `pkg/binate/types/check_decl.bn`) only `defineConst`'d const-group
  members with an explicit `TypeRef`.  Bare iota-continuation members
  (`B`/`C` in `const ( A int = iota; B; C )`) have no `TypeRef`, so they
  were never forward-registered — defined only later by `checkConstDecl`
  in decl order.  Any reference checked BEFORE the group failed
  `undefined`: a forward ref within a file, or (since a package's files
  are merged before checking) a reference from a sibling file ordered
  ahead of the group.  Blocked the const-group enum idiom in any package
  WITHOUT a `.bni` interface (package-main executables); `.bni`-declared
  enums were unaffected (the `.bni` exports the members).
- **Discovery**: 2026-06-02, REPL push inversion (Stage 4a) — a
  `const ( STEP_… int = iota; … )` group in `cmd/bni/repl_step.bn`,
  `cmd/bni/repl.bn` referencing `STEP_EOF_CLEAN` → `undefined`.
- **Fix**: `collectDecls` forward-registers bare group members with an
  untyped-int placeholder; `checkConstDecl` attaches the real iota
  value/type when the group is checked.  Bare members are always
  iota-valued untyped ints per `checkConstDecl`, so the placeholder
  agrees.
- **Tests**: `TestForwardRefBareIotaConstMember` (`check_decl_test.bn`)
  + `conformance/526_forward_ref_iota_const` (full pipeline: type-check
  + IR-gen + runtime fold the forward-ref iota values correctly — IR-gen
  had no parallel defect).  No regressions: `pkg/binate/types` 528/0,
  full `builder-comp` conformance 455/0/1.
- **Follow-up (not a bug)**: `cmd/bni` is LINTED by the BUILDER's
  bundled `bnlint` (`scripts/hygiene/lint.sh` prefers the
  `BUILDER_VERSION` tool), whose checker predates this fix — so `cmd/bni`
  keeps `STEP_*` as individual `const X int = N` decls until a BUILDER
  carrying the fix ships (binate `575b9d27` documents this in-code).
  Switch `cmd/bni` (and other package-main enums) to `const ( … iota )`
  groups once `BUILDER_VERSION` includes the fix.

### ~~aa64 closure shim: outgoing user-args don't stack-spill when captures fill X0..X7~~ — FIXED 2026-06-01 (binate `1f25568b`)
- **Was**: a closure whose total outgoing-arg word count exceeded 8
  (e.g. two `@[]T` captures (4+4 words) plus a single user `int` =
  9 words) fell back to plain `B underlying` on aarch64.  Captures
  landed in X0..X7 fine, but the user-arg that should have spilled
  to the outgoing stack was never written; the underlying body read
  garbage from `[SP+0]`.  The symmetric x64 case (`nUserWords > 5`
  overflowing RSI..R9 after RDI holds data) fell back to JMP without
  moving args.  Pinned by `conformance/510_capture_managed_slice`
  (was xfailed on `builder-comp_native_aa64`).
- **Resolution**: the native shim stack-spill landing (this session's
  #2) added `emitClosureShimStackSpillAA64` / `emitClosureShimStackSpill_x64`,
  which plan the outgoing call via the `AAPCS64()` / `SysVAMD64()`
  classifier, `SUB`/`sub` the outgoing-args area, and write each
  stack-bound capture / user-arg word through a scratch register
  (X16 on aa64, RAX on x64) — covering all four transfer shapes
  (reg→reg, reg→stack, stack→reg, stack→stack).  `emitClosureShim`'s
  dispatcher routes any arg-on-stack case to the spill path, so the
  old `captureWords + nUserWords > 8` `B`-fallback in the fast path
  is now unreachable (it can only be reached when nothing spills,
  which contradicts `> 8`).
- **Tests**: `conformance/510`'s aa64 xfail removed (now green);
  `conformance/523_closure_many_user_args` (1 cap + 9 user-args) and
  `conformance/524_closure_many_caps_reg_to_stack` (3 caps + 8
  user-args, exercises the stack→stack shuttle) pin the spill path.

### ~~@func / @Iface scope-end cleanup: `_call_dtor(raw_fn_ptr, ptr)` lowered as HANDLE dispatch (silent miscompile on every target)~~ — FIXED 2026-06-01 (binate `67952cf1` + `dc46ac7f`)
- **Was**: `emitManagedFuncValueRefDec` / `emitManagedIfaceValueRefDec`
  extracted slot 0 of the value's vtable as `dtor` and passed it to
  `rt.ZeroRefDestroy`.  Inside ZeroRefDestroy, `_call_dtor(dtor,
  ptr)` is lowered as `OP_CALL_HANDLE` — which expects `dtor` to be
  a `*BnFuncValue` HANDLE POINTER, not a raw fn pointer.  Both the
  `@func` vtable (`@__vt.<closure>` slot 0) and the iface impl
  vtable (`@__ivt.<R>__<I>[0]`) were emitting a raw fn pointer
  bitcast.  OP_CALL_HANDLE then byte-pun-read the dtor function's
  own machine code as `{vtable, data}` and jumped through random
  bytes.  LP64 / aa64 / x64 exit cleanly after the random jump;
  arm32-baremetal loops.  Captured `@T` / `@[]T` references in
  closure captures were never RefDec'd on any target — every
  `@func` capturing literal leaked the captures.  The same defect
  existed on the iface side but was masked in 370 by caller-side
  RefInc keeping the holder's refcount > 1 inside consume.
- **Discovery**: 2026-05-31, investigating the
  `.xfail.builder-comp_arm32_baremetal` on
  `conformance/515_managed_func_value_capture`.  Instrumented
  ZeroRefDestroy with semihosting traces + LP64 println traces;
  localised the hang to `_call_dtor`'s entry.  Dumped LLVM IR for
  both targets — identical broken shape.  lldb on 370 + assembly
  walk confirmed the iface side had the same bug but the broken
  path was never reached in 370 due to caller-side RefInc.
- **Fix**: mirror the `@T-with-managed-fields` cleanup path.  For
  each closure literal whose struct needs destruction, emit the
  standard function-value triple — `@__shim.<dtor>(i8* data,
  i8* ptr) { tail call closure_dtor(ptr) }`, `@__vt.<dtor>` =
  `{null, &shim}`, `@__handle.<dtor>` = `{&vt, null}`.  Change
  `emitFuncValueVtableDtor` and `emitImplVtableDtorSlot` so slot 0
  stores the HANDLE POINTER (`bitcast %BnFuncValue* @__handle.<dtor>
  to i8*`).  `_call_dtor`'s OP_CALL_HANDLE dispatch then works:
  `handle.vtable.call(null, ptr)` → shim strips data → invokes
  closure / receiver dtor with ptr.  Cross-mode-clean — VM
  trampolines dispatch the same handle through existing machinery.
  For the iface side, `addImplDtorsToSeen` pre-pass routes each
  impl's `DtorFuncName` through `emitFuncValueVtables`'s main
  emission loop so the standard triple gets emitted (dedups
  against existing OP_FUNC_HANDLE references, so a module that
  BOTH does `@T` cleanup AND constructs an iface from `@T` doesn't
  double-define `@__handle.<dtor>`).
- **Tests covering it**:
  - `conformance/515_managed_func_value_capture` — was xfail'd on
    arm32-baremetal; xfail lifted in the fix (now passes).
  - `conformance/520_iface_dtor_callee_sole_ref` (new) — drives an
    iface arg to refcount 0 inside the callee (the path 370
    misses).  Pre-fix this hangs on arm32-baremetal and silently
    leaks on LP64 / aa64 / x64; post-fix it prints
    `inner-rc-after: 1` correctly across all modes.
  - `pkg/binate/codegen/emit_funcvals_dtor_test.bn` (5 tests) —
    pins shim / vt / handle shapes, the @func vtable[0] post-fix
    bitcast (with explicit regression guard against the raw fn
    ptr shape), and the no-managed-captures negative case.
  - `pkg/binate/codegen/emit_impls_test.bn`:
    `TestEmitImplVtableDtorSlotForManagedReceiver` strengthened to
    pin `@__ivt[0]` = handle bitcast + regression guard +
    assertion that `@__handle.<dtor>` / `@__shim.<dtor>` are
    defined in the same TU (catches the dangling-handle case for
    modules that only have iface usage of the receiver type).

### ~~pkg/vm.TestExternRtMakeManagedSliceViaRegistry crashes mid-run on x64-darwin native~~ — FIXED 2026-05-31 (binate `5e4cc23d`)
- **Was**: under `builder-comp_native_x64_darwin`, `pkg/vm`'s test binary printed `=== RUN   TestExternRtMakeManagedSliceViaRegistry` then died with SIGBUS (exit 138).  The test exercises end-to-end cross-mode aggregate-return dispatch: bytecode user code → execExtern → dispatchExternBinding → `rt._call_shim_aggregate` (lowers to OP_CALL_INDIRECT, void return) → shim function pointer.  Was masked behind `TestEvalFloatCmp64`'s explicit FAIL pre-fix; surfaced as the sole failure once binate `268a57cc` cleared float-compare.
- **Discovery**: 2026-05-31, immediately after the float-compare fix.  Tracked via lldb: the crashing PC was 0x7f7980108068 (a zero-filled memory region) reached via `callq *%r11` — confirming R11 held garbage at CALL time.
- **Root cause**: x64's `emitCallIndirect` stashes the fn-ptr in R11 before the arg-dispatch loop, then RELOADS R11 from a SpillID slot just before `CALL r11`.  The reload is gated on `fnPtrSlot >= 0`, which requires PlanFrame to have allocated a SpillID slot for the call's `ins.ID`.  PlanFrame's spill-alloc was gated on `!InstrIsVoid(ins)` — for `_call_shim_aggregate` (void return), no slot was allocated, the reload was suppressed, and R11 was free for the arg-load loop to clobber (`regPool(1) = R11`).  The comment in `emitCallIndirect` claiming "R11 is caller-saved and not in regPool — safe" was wrong on the regPool half.  AArch64 was structurally immune: X17 (intra-call scratch) is kept out of the aa64 regPool by construction.
- **Resolution**: extend PlanFrame's spill-alloc gate to fire for void indirect-call ops via a new `isIndirectCallOp` predicate covering OP_CALL_INDIRECT / OP_CALL_FUNC_VALUE / OP_CALL_HANDLE / OP_CALL_IFACE_METHOD.  Adds one 8-byte stack slot per void indirect call site; emitCallIndirect's existing stash+reload pattern then works uniformly across void and non-void calls.  Fix lives in shared `pkg/binate/native/common` so both backends stay in lockstep, even though only x64 surfaced the bug.
- **Tests**: `TestPlanFrameVoidIndirectCallGetsSpillSlot` + `TestIsIndirectCallOpCoversAllStashUsers` in `pkg/binate/native/common/common_test.bn` pin the allocation and the predicate's positive/negative cases.  End-to-end: x64-darwin native pkg/vm now 153/0 (was failing); all three conformance modes (builder-comp, native_aa64, native_x64_darwin) at 448/0/1; x64-darwin native unit-test sweep clean at 34/0.

### ~~pkg/binate/native/aarch64 test binary crashes silently during TestBuildMethodFuncNameNative on x64-darwin~~ — FIXED 2026-05-31 (binate `f22afb47`)
- **Was**: under `builder-comp_native_x64_darwin`, `pkg/binate/native/aarch64`'s test binary printed `=== RUN   TestBuildMethodFuncNameNative` then died with SIGBUS (exit 138) before completing the test.  Surfaced after binate `daf51bf1` cleared the dtor-vt collision that had been masking it.
- **Discovery**: 2026-05-30, immediately after the dtor-vt fix landed.  Hypothesised in the initial entry as a Rosetta / testing-harness issue; tracking it via lldb showed the real cause was much more interesting.
- **Root cause**: a PlanFrame bug, not a Rosetta artefact.  `TestBuildMethodFuncNameNative` calls `buildMethodFuncNameNative(*[]const char, *[]const char, *[]const char) @[]char`.  Visible args = 3 × 2 = 6 reg-words; the hidden RDI sret pointer shifts everything up by one → 1 word spills to the outgoing-args area.  `pkg/binate/native/common.PlanFrame`'s outgoing-args walk used `callArgTypes(ins)`, which doesn't synthesise the hidden sret slot, so the reservation was computed as 0 bytes.  `SretSlotOff` of the OUTER function (itself `@[]char`-returning) then landed at offset 0; the inner call's stack spill clobbered the outer's sret-stash at `(%rsp)`, and the outer's return-marshalling wrote to a code-segment address.  Bug was x64-specific (AAPCS64 uses X8 outside the GP arg-reg pool, no shift needed).
- **Resolution**: add `CallConv.SretInGpArgReg` flag (true for SysV-AMD64, false for AAPCS64).  New helper `callDispatchArgTypes(cc, ins)` prepends a TypInt() to argTypes when the callee returns a big aggregate AND `SretInGpArgReg` is set, mirroring the synthesis loop in `pkg/binate/native/x64/x64_call.bn::emitCall`.  PlanFrame uses the new helper.  CalleeUsesCSret isn't consulted (PlanFrame doesn't have allFuncs); a C-extern callee with a saturating arg list would still trip the same overlap bug — tracked separately if it surfaces.
- **Tests**: `TestCallDispatchArgTypesScalarReturnNoSynthesis`, `TestCallDispatchArgTypesPrependsSretOnSysV`, `TestCallDispatchArgTypesNoSretPrependOnAapcs64`, `TestPlanFrameAccountsForSretReturnedShiftOnSysV` in `pkg/binate/native/common/common_test.bn` pin the three branches plus the OUTER-function frame layout.  End-to-end: `pkg/binate/native/aarch64`'s `TestBuildMethodFuncNameNative` now passes on x64-darwin; all three conformance modes (builder-comp, native_aa64, native_x64_darwin) at 447/0/1.

### ~~pkg/binate/asm/macho + asm/parse: link-and-run tests assume host-arch == target-arch (x64-darwin unit-test fail)~~ — FIXED 2026-05-31 (binate `7be8192a`)
- **Was**: under `builder-comp_native_x64_darwin`, `pkg/binate/asm/macho` and `pkg/binate/asm/parse` test binaries failed multiple `TestLinkAndRun` / `TestParseAndRun` / `TestLoopSum` / etc. with `ld: warning: ignoring file '/tmp/binate_asm_link_test.o': found architecture 'arm64', required architecture 'x86_64'` followed by `Undefined symbols for architecture x86_64: "_main"`.  Tests assembled AArch64 instructions to an arm64 Mach-O .o, then invoked `cc` to link without `-arch arm64`; under x64-darwin the test binary ran as x86_64 (Rosetta on Apple Silicon), so cc inherited the parent's arch and defaulted to x86_64 link target → arch mismatch.
- **Discovery**: 2026-05-30, immediately after the dtor-vt fix (binate `daf51bf1`) unblocked the link step.  Pre-existed the fix but was masked.
- **Root cause**: `canLinkAndRun()` in `macho_test.bn` (and the equivalent in `aarch64_instr_test.bn`) only gated on otool being present (macOS detection); didn't check host arch.  Test code invoked `cc` without `-arch arm64`, so cc followed the parent's arch.
- **Resolution**: pass `-arch arm64` explicitly to cc in all 4 affected call sites (`TestLinkAndRun`, `assembleAndRun` helper, `TestCrossObjectCall`, `TestParseAndRun`).  On Apple Silicon hosts the resulting arm64 binary runs natively even when exec'd from an x86_64 parent.  On real x86_64 Macs the arm64 binary couldn't run, but that's not a currently-tested host config.

### ~~Codegen emits per-type dtor vtable in every package that references the type — native-mode linker collides them~~ — FIXED 2026-05-30 (binate `daf51bf1`)
- **Was**: linking multiple `pkg/<X>.o` files together (e.g. the `builder-comp_native_*` unit-test runners that bundle every test package into one binary) failed with `duplicate symbol '_bn_pkg__binate__asm____dtor_Assembler__vt'` and similar `___dtor_<T>__vt` globals.  Each `pkg/asm/<sub>` and `pkg/native/<sub>` that used `@Assembler` emitted its own copy of `__dtor_Assembler__vt`; the Mach-O linker saw ≥ 2 strong definitions and errored.  Reproduced under `builder-comp_native_x64_darwin pkg/binate/asm/<anything>` (6 of 7 packages failed) and `pkg/binate/native/<anything>` (3 of 4 failed).  Conformance was unaffected (single main module + runtime, not multi-pkg).
- **Discovery**: 2026-05-29, during the float-lowering work.  Verified the failure existed on a clean tree.  The original aa64 sibling cluster had been fixed in binate `94b75294` but the x64 backend never got the parallel treatment.
- **Root cause**: x64 backend's `collectFuncValueRefs` was missing the IsLinkOnce pre-pass that aa64's `collectFuncValueRefs` had (since `94b75294`), and `lookupFuncValueType_x64` was missing the matching `IsExtern { continue }` gate.  Without the pre-pass, only TUs that internally took an `OP_FUNC_HANDLE` to their own dtor emitted the `__vt/__handle/__shim` triplet — every consumer TU referenced the dtor via OP_FUNC_HANDLE (RefDec calls), so every consumer also emitted the triplet and the strong vt globals collided.
- **Resolution**: port the aa64 fix verbatim to `pkg/binate/native/x64/x64_funcvalue.bn`.  The pre-pass adds every locally-defined `IsLinkOnce=true, IsExtern=false` function to `seen[]` regardless of OP_FUNC_HANDLE refs — defining TU always emits the triplet.  `lookupFuncValueType_x64` returns nil for `IsExtern` entries so consumer TUs skip local emission; cross-TU resolution at link time resolves to the defining TU's emission.
- **Tests**: `TestCollectFuncValueRefsIncludesLocalIsLinkOnce` + `TestLookupFuncValueTypeSkipsExtern` in `pkg/binate/native/x64/x64_funcvalue_test.bn` pin the two halves of the gate.  4 of 6 previously-failing `pkg/binate/asm/*` unit-test packages and 3 of 4 `pkg/binate/native/*` packages now link cleanly under `builder-comp_native_x64_darwin`; remaining failures (asm/macho, asm/parse, native/aarch64) are unrelated pre-existing issues (test-driver clang/ld invocation, mid-run hang).

### ~~Closure-shim emit drops `IndirectLargeAggregates` convention for >16-byte captures~~ — FIXED 2026-05-30 (binate `47223d3c`)
- **Was**: clang compile-error on any `*func(...)` / `@func(...)` whose closure captured a >16-byte aggregate (e.g. a `@[]T` managed-slice, 32-byte `BnManagedSlice`): `'%cap0' defined with type '%BnManagedSlice = type { ptr, i64, ptr, i64 }' but expected 'ptr'`.  Surfaced by `conformance/510_capture_managed_slice` and `conformance/514_capture_split_aggregate` after `f5340fac` (the byval/IndirectLargeAggregates landing) made the underlying funclit's signature switch from struct-value to `ptr`.
- **Discovery**: 2026-05-30, immediately after resync to `f5340fac`.
- **Root cause**: `pkg/binate/codegen/emit_funcvals_closure.bn::emitClosureShim` did GEP → `%capN_ptr` → load `%capN = struct value` → tail-call with `%capN`.  `writeParamTypeLLVM` (used to emit the call's arg types) now returns `ptr` for any aggregate over the 16-byte threshold, since the underlying funclit was emitted with `ptr` params under the new convention.  Result: the shim passed the loaded struct value where a `ptr` was expected.  The byval/indirect-aggregate convention from `plan-codegen-byval.md` had been applied to function signatures (`writeParamTypeLLVM`), regular call sites (`writeByvalArgPreamble` in emit_call.bn), and impl emission, but not to the closure-shim caller.
- **Resolution**: in `emitClosureShim`, gate each capture on `isByvalParam(capTyp)`.  Byval-passed captures skip the load and pass `%capN_ptr` (the GEP into the closure struct) directly as the indirect pointer — the closure struct already lives in memory and outlives the call, so no fresh alloca + memcpy is needed.  Non-byval captures keep the load + value-pass shape unchanged.
- **Test**: `TestEmitClosureShimByvalCaptureSkipsLoad` in `pkg/binate/codegen/emit_funcvals_closure_test.bn` pins the no-load behavior + the `ptr %cap0_ptr` arg shape.  `conformance/510_capture_managed_slice` + `conformance/514_capture_split_aggregate` are the end-to-end regressions.
- **Native backend equivalents not investigated**: the native aa64 + x64 closure-shim emit paths may need a similar audit, but this fix unblocks the builder-comp LLVM path; conformance 510/514 are currently passing on builder-comp and the native modes were green prior to f5340fac.

### ~~pkg/native/common: ParseFloatLitToBits overflows for extreme denormals~~ — FIXED 2026-05-30 (binate `6db081fc`)
- **Was**: `pkg/vm.TestEvalFloatArith64` failed on `builder-comp_native_aa64-comp_native_aa64` with "FMUL64 must keep float64 precision".  At runtime `1.0e-300 * 1.0e-30` correctly produced the denormal `1.0e-330`; at compile time the literal `1.0e-330` parsed to a garbage bit pattern.  Integer CMP (per the float-compare bug also fixed in this batch) then said they were not equal.
- **Discovery**: 2026-05-29, during aa64 self-host triage.
- **Root cause**: `ParseFloatLitToBits` in `pkg/native/common/common_float.bn:118` handled fractional-exponent literals via `divToDoubleBits(mantInt, pow10(-netExp))`.  `pow10(n)` looped `r = r * 10` `n` times in a `uint64`; for `n` ≥ 20 the multiplication overflowed uint64 (`10^20` > 2^64) and `r` wrapped to a garbage value.  For `1.0e-330` we hit `pow10(331)` which was wildly overflowed.  `divToDoubleBits(10, garbage)` then computed a quotient whose bit pattern bore no relation to the IEEE 754 denormal that LLVM (or any spec-conforming parser) would produce.
- **Resolution**: two-part change to `pkg/native/common/common_float.bn`:
  - `underflowsToZero` guard: conservative log2 over-approximation (`bitLen(mantInt) × 10 + netExp × 33 < -10750`) routes any literal below 2^-1075 directly to 0 — clears `1.0e-330` before `pow10` even runs.
  - `parseLargeNegExpToBits` (new): for `-netExp > 19`, maintains `mantInt` as a 128-bit mantissa (`hi:lo`) with a separately-tracked binary exponent, processing each decimal-place shift as `10 = 5 × 2` — the `×2` goes into `binExp`, the `/5` goes through `div128by5` (bit-by-bit long divide).  Per-step normalization via `shl128` shifts the top set bit back into `hi`'s MSB so 128 bits of precision are maintained throughout (accumulated error stays below 2^-53 after 330 steps).  `bitsFrom128` packs the final `(hi:lo, binExp)` tuple into IEEE 754 double bits — handles normal, denormal, underflow, and overflow ranges.
- **Result**: `pkg/vm.TestEvalFloatArith64` passes, completing the aa64 self-host lane: `builder-comp_native_aa64-comp_native_aa64` 34/0 — full sweep green for the first time since this lane was tracked.  Tests `TestParseFloatTinyUnderflows` (underflow gate) + `TestParseFloatLargeNegExp` (biased exponents within ±1) are the unit pins.

### ~~codegen omits `byval` on >16-byte struct params — cross-pkg ABI miscompile~~ — FIXED 2026-05-30 (binate `f5340fac` + `8ba29d11`, plan-codegen-byval.md)
- **Was**: cross-package call where the callee was LLVM-compiled and the caller was native (or vice-versa) and the signature included a >16-byte struct param by value: callee read the struct from the wrong place, returned wrong answer (or segfaulted).  Surfaced as conformance failures 331 / 337 / 411 on `builder-comp_native_x64_darwin`.  On aa64 the same root cause was latent — the native backend *matched* LLVM's non-textbook emission (SplitAggregates=true) so aa64 conformance was green, but that match was to a non-textbook ABI, not the spec.
- **Discovery**: 2026-05-29, while investigating remaining x64-darwin conformance failures after the float-lowering work landed.  Verified empirically by compiling a minimal C file with the same struct shape via clang `-target x86_64-apple-darwin` and comparing the emitted IR + asm to binate's.
- **Root cause**: clang emits `ptr byval(%struct.T) align 8` for >16-byte struct params; that attribute tells LLVM to lower per the target's textbook calling convention (MEMORY-on-stack for SysV, indirect-pointer-pass for AAPCS).  Binate's codegen never emitted `byval` (zero matches across `pkg/codegen/`).  Without `byval`, LLVM fell back to IR-level struct-value rules — on x86_64 decomposing the struct into separate i64 args, on AAPCS64 splitting across X regs + stack.  Native backends were forced to match the outlier convention.
- **Resolution**: emit a plain `ptr` (NO `byval` attribute) for >16-byte aggregate params in pkg/codegen — both arches' LLVM lowering then treats it as "pointer in next free GP arg reg", uniformly matching the indirect-pointer-pass semantics native backends now implement.  Plan doc originally proposed `ptr byval(<T>)`, but empirical verification showed LLVM's `byval` lowering on AArch64 lays the struct on the caller stack (matching the SysV-byval shape), not the pointer-in-reg-indirect shape clang picks for AAPCS at the frontend.  Plain `ptr` gets the desired indirect-pointer-pass on BOTH targets.  Caller-side alloca + memcpy lives in the call's preamble (`writeByvalArgPreamble`).  Native common gained `IndirectLargeAggregates` flag (true for AAPCS64 / AAPCS64_Darwin / SysV-AMD64); pkg/native/x64 also needed a separate sret-shift fix in `emitCallIfaceMethod` (`8ba29d11`) to place iv.data in RSI when the iface-dispatched callee returns via sret.
- **Result**: x64-darwin conformance 432 → 438 / 438 = 100%.  aa64 unchanged at 437 / 437 = 100%.  Conformance 411 + 331 + 337 are the end-to-end regression pins; common_callconv_test.bn / aarch64_call_test.bn / x64_call_test.bn / x64_emit_func_test.bn cover the unit shape.

### ~~pkg/native/aarch64: float compares use integer CMP instead of FCMP~~ — FIXED 2026-05-29 (binate `21366bfa`)
- **Was**: `pkg/vm.TestEvalFloatCmp64` failed on `builder-comp_native_aa64-comp_native_aa64` with "NaN == NaN must be false".  Two NaN values constructed the same way (both `0.0/0.0`) had identical bit patterns; integer CMP said they were equal; IEEE / Binate's ordered-fcmp semantics said they were not.  `+0.0 == -0.0` was also wrong by the same mechanism (different bit patterns; IEEE says equal).  Every float compare in any program built via the aa64 native backend had wrong NaN / signed-zero semantics.
- **Discovery**: 2026-05-29, while triaging the residual aa64 failures after the dtor-vt fix landed.
- **Root cause**: `pkg/native/aarch64/aarch64_ops.bn::emitCompare` unconditionally emitted `Cmp` (integer compare) for `OP_EQ`/`NE`/`LT`/`LE`/`GT`/`GE` without checking operand type.  `Fcmp` was defined in `pkg/asm/aarch64/aarch64_fp.bn:103` but was not called from anywhere in the native backend.
- **Resolution**: `emitCompare` now gates on `ins.Args[0].Typ.IsFloat()` and routes float operands to a new `emitFloatCompare` helper that emits `FCMP` + `CSET` (= `CSINC Rd, XZR, XZR, invCond`) with ARM ordered-FP condition codes — `EQ`/`MI`/`LS`/`GT`/`GE` per the proposed table.  `OP_NE` uses a two-step `CSET NE` then `CSEL rd, rd, XZR, VC` to zero the result when the operands were unordered.  Unit-test `TestInvertFloatCondForOp` pins the structural invariants of the inverse table.

### ~~macOS aa64-comp_native_aa64: duplicate destructor-vtable symbol across package boundary~~ — FIXED 2026-05-29 (binate `94b75294`)
- **Was**: link failure for every package downstream of `pkg/asm` under the `builder-comp_native_aa64-comp_native_aa64` mode on macOS — `duplicate symbol '_bn_pkg__asm____dtor_Assembler__vt' in pkg__asm.o + pkg__asm__x64.o`.  Cascaded into `FAIL: pkg/asm/{x64,macho,parse,arm32,aarch64,elf}`, `pkg/native/{x64,aarch64}`, `pkg/vm`, `cmd/{bnas,bnc}` — every consumer of `pkg/asm.Assembler` re-emitted the destructor-vtable symbol.  Blocked the entire macOS aa64 self-host lane (14 failures).
- **Discovery**: 2026-05-28, while triaging the macOS aa64 CI lane during the int64-fold work (binate `224e7bef`).  Pre-existing — failures appeared on every completed CI run going back ≥28 commits.
- **Root cause (final diagnosis)**: a *separate* dtor-name-mangling bug feeding the symbol collision.  In defining package `pkg/ast`, `dtorNameForType` wrote `"__dtor_File"` → qualified to `"pkg/ast.__dtor_File"` → mangled `bn_pkg__ast____dtor_File`.  In consumer `pkg/parser`, `dtorNameForType(pst with pst.Name="pkg/ast.File")` wrote `"__dtor_pkg/ast.File"` — the package path got baked *into* the dtor token → mangled `bn___dtor_pkg__ast__File` (DIFFERENT shape).  Meanwhile the `OP_FUNC_HANDLE` reference resolved to `bn_pkg__ast____dtor_File` (matching the defining package).  So consumers emitted a *second* dtor implementation under a different mangled name than the handle referred to.  On Mach-O native-aa64, the consumer's `__vt` collided with the defining package's emission.  Underneath: `pkg/native/aarch64/aarch64.bn:lookupFuncValueTypeAA64` did not distinguish between locally-defined functions and `IsExtern` import stubs, so consumers emitted vtable triplets for cross-package handles too.
- **Resolution**: two-part change to ir + native/aa64:
  - **ir**: `gen_dtor_emit.bn` / `gen_copy_emit.bn` pass-3 now mirrors pass-2's cross-package gate.  Consumer-side dtor/copy generation for cross-package struct types is replaced with `declareExternDtor` / `declareExternCopy` (via `funcAlreadyDeclared` dedup), so the consumer-side wrong-named duplicate stops being emitted.
  - **native/aa64**: `collectFuncValueRefs` gets a pre-pass that adds every locally-defined IsLinkOnce function to `seen[]` regardless of OP_FUNC_HANDLE references — so the defining TU always emits `__vt`/`__handle`/`__shim`.  `lookupFuncValueTypeAA64` gets an `IsExtern { continue }` gate so consumer TUs skip emitting triplets for cross-package handles (defining TU resolves them at link).
- **Result**: macOS aa64 self-host sweep (builder-comp_native_aa64-comp_native_aa64): 33/1 (was 14 failures).  Remaining failure was a pre-existing aa64 float-codegen issue (the float-compare bug above and the denormal-parser bug above, both also now fixed).  LLVM-side unchanged: clang's `weak_odr` already dedups via `__DATA,__datacoal_nt` + `S_COALESCED`.

### ~~bnc: int64 literals under unary-minus silently truncate to i32 on ILP32 targets~~ — FIXED 2026-05-29 (binate `224e7bef`)
- **Was**: `cast(int64, -9223372036854775807)` evaluated to `1` (not `-9223372036854775807`) under `--target arm32-linux`.  Any int64 literal with magnitude > 2^31 wrapped in unary-minus (or any non-cast typed context that didn't route through `genIntLitWithHint`) got truncated to i32 before negation, silently producing wrong values.  No LP64 host effect — `intLL()` returned i64 there, which could hold the full magnitude.
- **Discovery**: 2026-05-28, while triaging arm32_linux unit-test failures `TestBignumToIntInt64Min` (pkg/ir), `TestFormatInt64Boundaries` (pkg/bootstrap), `TestWriteInt` (pkg/buf), which all construct int64-min via `cast(int64, -9223372036854775807) - cast(int64, 1)`.  The expression evaluated to `0` on arm32, not int64-min.
- **Root cause**: `genExprInner`'s `EXPR_INT_LIT` branch (pkg/ir/gen_expr.bn:34) unconditionally emitted the literal at `types.TypUntypedInt()`.  `TypUntypedInt` has `Width=0`, so `llvmType` fell through to `intLL()` — i64 on LP64, **i32 on `--target arm32-linux`**.  The literal text was widened to int64 by `exprIntLitValue` (via the type checker's bignum-fold), but the LLVM emit type dropped back to host int, so the IR-text writer's i32 literal silently wrapped.  `genIntLitWithHint` papered over this for the most common case (bare `EXPR_INT_LIT` argument to `cast(T, …)` or `var x T = …`), but didn't peek through `EXPR_UNARY`, so `cast(T, -lit)` fell through to the buggy path.
- **Resolution**: added `tryFoldOversizedConst` in `pkg/ir/gen_util_literals.bn`, dispatched from `genExprInner`'s EXPR_UNARY / EXPR_BINARY branches.  When the type checker's bignum-fold on the resolved type carries a magnitude that exceeds the target's host-int signed range, emit a single OP_CONST_INT at int64 directly — bypassing the recursive `genExpr` that would emit the leaf literal at TypUntypedInt → intLL() = i32 on the 32-bit target.  No-op on LP64 — `targetIntBits >= 64` short-circuits.  Tests: `TestGenCastNegLitOverflowingHostIntPromotesToInt64` (unit), `conformance/507_int64_min_via_unary_minus` (end-to-end).  Companion fix-up `8981d5bf` locks LP64 around `TestGenUnaryMinusOnInt64Preserves`.

### ~~Type definitions duplicated between `.bni` and `.bn` — silent miscompile on mismatch~~ — FIXED 2026-05-29 (binate `f18b2e39`..`553649fc`, plan-type-decls.md series)
- **Was**: when a struct was declared in `pkg/foo.bni` AND in `pkg/foo/foo.bn` with DIFFERENT field lists, the compiler accepted the program and silently emitted machine code that mis-resolved the mismatched field(s).  Discovered via Stage 4 of plan-c-call.md (binate `0d0f35b7`): `common.bni` declared `CallConv.VariadicStackOnly bool` as the 8th field, but `common_callconv.bn`'s impl-side `struct` only listed 7 fields (missing VariadicStackOnly).  The type checker accepted `cc.VariadicStackOnly = true`, but pkg/codegen lowered the field access to GEP index 0 (the first field, NumGpArgRegs) and emitted an `i64` store of the zext'd `i1` — overwriting NumGpArgRegs with 1 instead of setting the trailing bool.  Took a full debug session to track down; classic silent-miscompile shape.
- **Why it mattered**: any package whose bni/bn pair drifted (a field added to one without the other, or fields reordered) silently miscompiled, with the symptom landing far from the cause.  Easy to hit during refactoring; impossible to diagnose without reading the emitted LLVM IR.
- **Resolution**: full plan-type-decls.md series — the proper "single source of truth" fix rather than a stop-gap validator.  Phase 1 (`f18b2e39`) added forward-decl syntax `type S` (no body) to parser/AST.  Phase 2 (`42c10d6c`) extended the type checker to handle forward decls + warn on mismatched duplicates.  Phase 3a (`7a6af095`) made bni loading forward-decl-safe.  Phase 4 (`f3447cba`, `0c7d93d8`, `e8f27e07`, `c9308b16`) cleaned up all 5 pre-existing duplicates in tree (pkg/native/common, pkg/rt, pkg/ir, pkg/builtins/testing).  Phase 5 (`0166ce0c`) flipped the mismatched-duplicate warning to a hard error — the original silent-miscompile shape is now structurally impossible.  Phase 3b (`553649fc`) made cross-package opaque handles round-trip cleanly through codegen.
- **Test**: `conformance/514_opaque_handle_cross_pkg/` exercises the opaque-handle pattern end-to-end; `pkg/types/check_decl_test.bn` covers the duplicate-detection / forward-decl-acceptance unit cases.

### ~~Mangler collides symbols from packages with the same last-segment short name~~ — FIXED 2026-05-27 (binate `7f989ad` + `f7f8f04`), un-rename follow-up `dd05118`
- **Was**: `pkg/foo/bar` and `pkg/baz/bar` both mangled to the same `bn_bar__*` symbol prefix because `mangle.PkgShortName` only took the last `/`-segment of the package path.  At link time the second `.o` overwrote the first, breaking any program where both packages were in the same transitive imports.
- **Discovery**: Phase 2 of the SysV-AMD64 backend hit this when `pkg/native/x64` and `pkg/asm/x64` both produced `bn_x64__*` symbols.  Builds failed with "undefined symbol bn_x64__Push" because the native package's `x64.o` clobbered the asm package's slot.
- **Root cause**: `pkg/mangle/mangle.bn:PkgShortName` returned the last segment of the import path.  Mangled-symbol generation used that segment as the unique-per-package prefix.  Two packages sharing the last segment were indistinguishable in the mangled namespace.
- **Resolution**: Phase B (binate `7f989ad` + `f7f8f04` trampoline fixup, option 1 of the original triage) flipped mangling to use the full path with `/` → `__`: `pkg/asm/x64` → `bn_pkg__asm__x64__*`, `pkg/native/x64` → `bn_pkg__native__x64__*`.  Two packages with the same last segment now mangle to distinct symbols.  The cross-package CALL residual (next entry) was a separate fix.
- **Aftermath**: the collision-dodging workaround that renamed `pkg/native/x64` → `pkg/native/amd64` (`9f36f62`) was reverted in `dd05118` ("pkg/native: rename amd64 → x64 (matches pkg/asm/x64)").  Both directories now agree on `x64`; no rename pressure remains.

### ~~Cross-package CALL to a same-last-segment package resolves to the wrong package (Phase B residual)~~ — FIXED 2026-05-27 (binate `2122648`)
- **Was**: when two packages shared a last path segment (e.g. `pkg/alpha/widget` and `pkg/beta/widget`) and at least one consumer package imported the second one, the consumer's cross-package call via the source-level alias `widget.X` resolved to the FIRST same-last-segment package the IR registered, not the one the consumer actually imported.  `pkg/mid` `import "pkg/beta/widget"; func BetaCode() { return widget.Code() }` emitted a call to `bn_pkg__alpha__widget__Code` (alpha — wrong) instead of `bn_pkg__beta__widget__Code`.  Manifests as either a link-time undefined symbol (when alpha isn't in the link unit) or a silent miscompile (when both are linked — the call dispatches to the WRONG function).
- **Discovery**: 2026-05-27, while writing the end-to-end regression test for the original same-last-segment collision bug.  Phase B's symbol-definition mangling fixed definitions correctly, but its alias→fullpath resolution map had this residual gap.
- **Root cause**: `pkg/ir/gen_import.bn`'s alias→fullpath map (`importAliasNames` / `importAliasPaths`) was a flat per-module list keyed by the short alias, with first-write-wins dedup (`RecordImportPath`).  `registerAllStructTypes` called `RecordImportPath(shortName(path), path)` for every package in `ldr.Order` for transitive struct-name visibility, so the FIRST same-last-segment package registered won the alias slot; later ones got ignored.  Then `buildQualName`'s `resolveImportPkg(alias)` in any consumer-side call resolved the source alias to the wrong full path.
- **Resolution**: binate `2122648` (pkg/ir: per-file alias scoping).  Both the registries (struct types, interfaces) and the cross-package call resolution now use the IMPORT PATH directly rather than the alias map.  The alias map itself is populated only by the importing module's RegisterImports (whose direct imports are unambiguous within a file by source-level constraint); broad-registration and imported-file processing push/pop the file's OWN imports around per-file processing so cross-package refs resolve via THAT file's aliases — not whichever alias happened to win the global first-wins dedup.  A parallel `.o`-filename collision was fixed in the same commit (pathFileBase folds slashes for distinct intermediate filenames).
- **Test**: `conformance/392_same_last_segment_pkgs/` is the regression guard.

### ~~Cross-package generic instance def emitted with empty module qualifier (blocked appendXxx→slices.Append migration)~~ — RESOLVED 2026-05-27 (binate `7f51f2a`, migration landed `2714e67`)
- **Was**: a generic instantiated with a type argument whose type
  lived in a directly+transitively-imported package emitted the
  instantiated function's **definition** with an EMPTY module
  qualifier while the **call site** used the consumer's qualifier —
  e.g. `pkg/loader`'s `slices.Append[@ast.File]` produced
  `define %BnManagedSlice @bn_Append__bn_inst__mptr_ast__File(...)`
  (def, no `loader`) but `call ... @bn_loader__Append__bn_inst__-
  mptr_ast__File(...)` (call).  Only cross-package-type-arg instances
  desynced; `Append[@Package]` (loader-local) and `Append[@[]uint8]`
  (primitive) were fine.
- **Root cause** (identified 2026-05-27): the bug was in the PINNED
  BUILDER, not the current tree.  Phase B (`pkg/mangle: flip to full-
  path symbol mangling`, binate `7f989ad` + follow-ups) had already
  fixed the current-tree mangler so cross-package generic instances
  emit matching def+call symbols.  But until 0.0.4 was promoted, the
  BUILDER (`bnc-0.0.3`) was pre-Phase-B and emitted the desynced
  symbols when compiling `cmd/bnc`'s tree (which imports `pkg/loader`
  via `cmd/bnc/main` etc.), so the gen1 build of any tree using
  `slices.Append[@ast.X]` failed at link.
- **Resolution**: cut bnc-0.0.4 (binate `5ea0208`) carrying Phase B,
  promoted `BUILDER_VERSION → bnc-0.0.4` (binate `7f51f2a`),
  then landed the loader migration (binate `2714e67`).  No further
  fix needed in current main beyond the BUILDER bump.
- **Validation before promote**: built current-tree bnc with
  `BUILDER_VERSION=bnc-0.0.4` and ran conformance (`builder-comp`)
  430/0/1; spot-checked the headline feature by applying the
  loader migration locally and confirming 47/47 loader tests pass.
- **Related defensive cleanup**: binate `0e5fafc` makes
  `mangleTypeArg` fold `/` (not just `.`) into `__` so its output
  is identifier-safe regardless of downstream re-qualification
  semantics in `NewFunc` / `FuncName`.  Not strictly needed to fix
  this bug (downstream `writeBnDotted` already folds `/`), but
  pins the helper's documented contract and provides defense-in-
  depth.

### ~~amd64 native backend: aggregate argument passing unimplemented~~ — FIXED 2026-05-27 (binate `f7a182b`, `b719d7e`)
- **Was**: under `builder-comp_native_x64_darwin-comp_native_x64_darwin`
  (the new local Rosetta runner), `002_arithmetic` and most tests
  miscompiled — garbage output + `runtime error: index out of bounds`.
  `pkg/native/amd64/amd64_call.bn::emitCall` explicitly skipped
  aggregate args (`if common.IsAggregateTyp(arg.Typ) { continue }`),
  so e.g. `bootstrap.formatInt(int, *[]uint8)`'s raw-slice arg (a 2-
  eightbyte `%BnSlice`) left RSI/RDX undefined.  Discovery surfaced a
  separate MAJOR latent bug in the shared CallConv (see next entry).
- **Fix landed in two commits**:
  1. `f7a182b` — `emitAggregateArg` handles the SysV INTEGER-eightbyte
     in-register case (≤ 16 B aggregate fitting in remaining GP arg
     regs).  Loads each eightbyte from the aggregate's storage into
     `argReg(regStart + w)`.  Conformance on builder-comp_native_-
     x64_darwin: 0/428 → 103/428.
  2. `b719d7e` — together with the CallConv classifier fix, the
     MEMORY-class path (> 16 B aggregates passed entirely on the
     stack) is now also emitted: each word is loaded into RAX and
     stored to `[rsp + stackOff + 8*w]`.  RAX is the load-shuttle
     scratch (not in regPool, not a GP arg register, dead pre-CALL).
- **Tests** (in `pkg/native/amd64/amd64_call_test.bn`):
  - `TestEmitCallAggregateArgLoadsTwoEightbytes`: 16 B aggregate as
    arg 1 after a scalar → 2 MOV-from-mem loads + LEA + CALL.
  - `TestEmitCallAggregateArgFirstSlot`: 16 B aggregate as arg 0
    (regStart = 0) — no off-by-one in `argReg(regStart + w)`.
  - `TestEmitCallAggregateArgOver16OnStack`: 32 B managed-slice →
    ≥ 4 MOV-from-mem loads + ≥ 4 MOV-to-mem stores.
- **What's still amd64-specific work** (separate gaps, not aggregate
  arg passing): many remaining x64_darwin failures (103/429) sit on
  non-aggregate-arg issues — e.g., `101_println_managed_chars` prints
  empty (likely OP_LOAD-of-aggregate or runtime crash), `003_variables`
  prints `0` for `var x int = 10` (OP_LOAD-of-int chain).  Float-arg
  passing (XMM regs) is also still unimplemented.

### ~~MAJOR: shared SysV CallConv mis-models aggregate-arg dispatch~~ — FIXED 2026-05-27 (binate `b719d7e`)
- **Was**: `pkg/native/common/common_callconv.bn` modeled SysV-AMD64
  aggregate dispatch with AAPCS-style register/stack *splitting*, but
  real SysV/x86_64 (and LLVM's by-value lowering of structs on x86_64)
  classifies any aggregate larger than `AggregateInRegMax` (= 16) as
  MEMORY class — passed *entirely* on the stack, never split, NGRN
  unchanged.  And a ≤ 16 B aggregate that doesn't fit in the remaining
  regs *also* goes wholly to MEMORY under SysV (no split).
  `TestCallArgStackOffSysVSplit` deliberately pinned the wrong split
  shape.  Implementation against the wrong classifier would silently
  miscompile every managed-slice / > 16 B-struct argument crossing
  the native↔LLVM boundary.
- **Fix**: new `CallConv.SplitAggregates bool` field (AAPCS64 = true,
  SysV_AMD64 = false).  The three dispatch helpers (`CallArgRegStart`,
  `CallArgStackOff`, `CallStackBytes`) are rewritten to consume a
  shared per-arg classifier `argRegWordsStackWords` gated by the
  flag.  AAPCS path byte-identical (arm64 conformance 427/0
  unchanged); SysV now classifies > 16 B aggregates as MEMORY and
  ≤ 16 B aggregates that don't fit as MEMORY too, with NGRN unchanged
  so later args still take remaining GP regs.
- **Tests** (in `pkg/native/common/common_callconv_test.bn`):
  - Replaced `TestCallArgStackOffSysVSplit` with three positive SysV
    tests: 32 B managed-slice → MEMORY; 16 B raw-slice-doesn't-fit
    → MEMORY with NGRN preserved (trailing scalar takes the still-
    free reg); 16 B raw-slice-fits → all-in-regs at the right
    regStart.
  - Added `TestCallArgStackOffAapcs64SplitUnchanged` pinning that
    AAPCS64 still splits as before.
  - Constructor smoke tests now assert the `SplitAggregates` field.

### ~~Generic call type args reject `@T` / `@[]T` / `*[]T` (parser)~~ — FIXED 2026-05-26 (binate `18b8047`)
- **Was CRITICAL.** Expression-position generic instantiation with a
  managed-pointer (`f[@T](...)`), managed-slice (`f[@[]T](...)`), or
  raw-slice (`f[*[]T](...)`) type argument failed to **parse** —
  `parseIndexOrSlice` parsed the `f[...]` bracket contents as
  expressions (to share the `arr[i]` index path), and those three
  forms have no expression spelling.  Only bare names and `*T`
  survived (ident / unary-deref).  Blocked the appendXxxPtr →
  `slices.Append[@T]` migration (the bulk of the helpers append
  managed pointers).
- **Fix**: a new `EXPR_TYPE` Expr node wrapping a parsed `TypeExpr`,
  threaded through four layers.  Parser (`parse_expr.bn`):
  `startsBracketTypeArg` detects a bracket element with no expression
  spelling — `@…` or `*[` — and `parseBracketTypeArg` parses it via
  `parseType`, wrapping in `EXPR_TYPE`; `*T` / `*p` still flow through
  `parseExpr` (only `*[` routes to the type parser, so `arr[*p]` stays
  an index).  Type checker (`typeArgFromExpr`) resolves `EXPR_TYPE`'s
  `TypeRef`; `checkExpr` errors cleanly if one reaches value position.
  IR-gen (`exprToTypeExpr`) hands the `TypeRef` straight through.
- **Coverage**: `pkg/parser` units (`@T` / `@[]T` / `*[]T` / mixed
  `f[int,@T]` / `arr[*p]`-stays-an-index); conformance 492 (end-to-end
  over the three forms, all modes) + 493 (type-arg-in-value-position
  rejection); `pkg/slices` `Append[@Thing]` test restored.  Green
  across builder-comp / -int / -comp.
- **Build ladder**: BUILDER bnc-0.0.2 still has the bug (it predates
  the fix); a future bnc-0.0.3 cut from a post-fix tree is needed
  before `slices.Append[@T]` can be used *inside* cmd/bnc's own
  (BUILDER-compilable) tree.  See version-history.md.

### ~~arm32_linux unit tests SEGV at startup — C-extern struct-return sret threshold was LP64-only~~ — FIXED 2026-05-25 (binate `4874fe6`)
- **Symptom**: every `builder-comp_arm32_linux` unit-test binary
  SEGV'd at startup (0 passed / 33 failed), while
  `builder-comp_arm32_linux` *conformance* was fully green.  The
  distinguishing factor: the synthetic unit-test runner calls
  `bootstrap.Args()` at startup (to parse `--run`), and no
  conformance test calls a struct-returning C extern.
- **Root cause**: `pkg/codegen/emit_types.bn:needsSret` hardcoded
  the LP64 rule "C-extern struct return > 16 bytes → sret".  On
  arm-linux-gnueabihf the AAPCS32 rule is "> 4 bytes → sret"
  (verified against clang: an 8-byte struct gets `sret`, a 4-byte
  one returns in r0).  `bootstrap.Args()` returns a 16-byte
  `BnManagedSlice`; clang's C side (binate_runtime.c) used sret,
  but the Binate caller — seeing 16 ≯ 16 — emitted a register-
  return call.  The conventions diverged and the returned slice
  was read from the wrong place: `len(bootstrap.Args())` came back
  as garbage (0x41000004), so the runner crashed before running a
  single test.
- **Fix**: `needsSret` picks the threshold from the target's
  pointer size — 4 bytes for ILP32, 16 for LP64.  Only consulted
  for `IsCExtern` returns, so Binate-internal struct returns
  (consistent on both sides) and all LP64 codegen are untouched.
- **Isolated reproducer**: `conformance/487_bootstrap_args`
  (`len(bootstrap.Args())`), which failed on arm32_linux pre-fix
  and now passes across host (LP64) + arm32 modes — the
  cross-mode regression guard.
- **After-fix state**: arm32_linux conformance 417/0; unit tests
  0→19 passing.  The remaining 14 unit-test package failures are
  the same 32-bit-target categories tracked below (filesystem /
  native-host arch / int32-literal-fit), plus two genuine
  test-level failures still to investigate
  (`TestBinBufWriteU64LittleEndian`, `TestOrrImm`).

### ~~`(*p).x` (field access through explicit deref) returns 0 — bnc-compiled only~~ — FIXED 2026-05-21 (binate `5a5ffb1`)
- Root cause was as originally diagnosed: `genSelector`
  (`pkg/ir/gen_selector.bn`) had no EXPR_UNARY base case — IDENT /
  SELECTOR / INSTANTIATE_OR_INDEX / CALL / BUILTIN bases all routed
  to a real field-pointer; the explicit-deref `*p` form fell
  through to the `return b.EmitConstInt(0, types.TypInt())`
  fallback, so `(*p).x` read a constant 0.
- Fix: mirror the EXPR_CALL pattern.  `genExpr` the operand (`*p`);
  the resulting `val` carries either a struct value (when `*p`
  loads a `T`), a managed-pointer-to-struct (when it loads `@T`),
  or a raw-pointer-to-struct (when it loads `*T`).  Each routes
  through the existing field-pointer + load logic.  The struct-
  value branch alloca+stores the loaded value (you can't GEP
  through an SSA value), mirroring the EXPR_CALL value-struct arm.
- Relies on the deref-typing extension from Slice P.2
  (`pkg/ir/gen_expr.bn` sizes `*p` loads by the operand's `Elem`
  for both raw and managed pointers), so `val.Typ` is the pointee
  rather than `i64`.
- **Pins**: conformance `456_field_access_through_explicit_deref`
  (was rejection-pinned with `.xfail` markers in all six bnc-
  compiled modes; flipped to `.expected` 42 in the fix commit) plus
  IR-layer unit tests `TestGenExplicitDerefRawPtrFieldRead` /
  `TestGenExplicitDerefManagedPtrFieldRead` in
  `pkg/ir/gen_selector_test.bn` (each asserts a `GET_FIELD_PTR` is
  emitted rather than the const-0 fallback).

### ~~Phase 4: aa64 native backend missing OP_FUNC_HANDLE / OP_CALL_HANDLE handlers~~ — FIXED 2026-05-24 (binate `9d23198`)
- `builder-comp_native_aa64-comp_native_aa64`: 2/413/1 → 415/0/1.
- Three changes in `pkg/native/arm64`: new LLVM-shape name helpers
  (`handleSymFor`, `vtableSymForLLVM`, `shimSymForLLVM`) in
  `arm64_names.bn`; OP_FUNC_HANDLE + OP_CALL_HANDLE dispatch
  handlers in `arm64_dispatch.bn` (handle is ADRP+ADD against
  `___handle.<mangled>`, call delegates to `emitCallFuncValue`);
  `collectFuncValueRefs` extended to OP_FUNC_HANDLE filtered
  local-only via new `lookupFuncValueTypeAA64`, and
  `emitFuncValueVtables` emits a weak `___handle.<mangled>` per
  local entry whose vtable_ptr slot points at the existing
  aa64-style vtable.
- Cross-rebase note: `807a9bf` (concurrent) removed OP_FUNC_ADDR
  entirely (Phase 4 left it dead), so the eventual landed shape
  handles only OP_FUNC_HANDLE / OP_CALL_HANDLE.
- The previous attempt's duplicate-symbol pitfall is avoided by
  the local-only filter in `lookupFuncValueTypeAA64`: cross-
  module references resolve at link time to the LLVM-emitted
  dep's weak_odr definition; we never emit a competing one.

### ~~Phase 4 (uniform native fn ptrs) — finish: dtor refs MUST move from idx to handle~~ — DONE 2026-05-23 (binate `f3d9436`)
- emitManagedPtrRefDec now emits OP_FUNC_HANDLE end-to-end (handle
  pointer in both native and bytecode), and BC_REFDEC_INLINE_FAST's
  slow path inspects `handle.data`:
  * `DATA_KIND_VM_CLOSURE_REC` → recover FnIdx from
    `closureRec[2]`, push the dtor frame on vm.Stack with ptr
    stashed in `freeOnPop`, jump.  BC_RETURN pops the frame and
    frees ptr.  No host C-stack recursion through the dtor field
    graph — the iterative win the earlier stop-gap was protecting.
  * Otherwise (data is null, or future kinds like
    DATA_KIND_COMPILED_CLOSURE) → load handle.vtable.call (the
    per-function shim) and dispatch via
    `rt._call_shim_scalar(shim, data, ptr, ...)`.  Cross-mode
    call — takes a host frame but cannot recurse back into the
    bytecode VM, so depth is bounded by the cross-mode call
    chain.  After the shim returns, `rt.Free(ptr)`.
- Cross-mode interop now works as Phase 4 intended: a managed
  value created in native that crosses into a bytecode VM (or
  vice-versa) resolves its dtor through the shared handle layout
  instead of an intra-vm-only function index.
- Follow-up retired in binate `807a9bf` (2026-05-24): emitIndirectCall
  was renamed to emitDtorOrCopyCall and now takes a name string
  directly (no throw-away EmitFuncAddr Instr), so `OP_FUNC_ADDR` /
  `BC_FUNC_ADDR` had no producer left and were deleted from the IR
  + bytecode + LLVM + aa64 surface in one pass.
- Follow-up retired in binate `aab30cf` (2026-05-24):
  `ExternBinding.RawFnAddr` (raw int handle pointer — latent UAF
  for heap-allocated source handles) → managed `@VMFuncHandle
  HandleAddr` that RegisterExtern populates with a binding-owned
  copy.  Ownership test pinned in `24fb091`.
- Phase 4 plan doc (`plan-uniform-native-fnptrs.md`) updated to
  mark Phase 4 LANDED in binate `42f463f`.
- **Original context** (kept for posterity): Phase 4 landed at
  binate `666ecc0` with a stop-gap (emitManagedPtrRefDec emits
  OP_FUNC_ADDR; BC_REFDEC reads Src2 as 1-based intra-vm idx) to
  fix `builder-comp-int-int` stack overflow.  Reverted in
  `f3d9436` with the proper handle-pointer kind-discriminating
  design above.

### ~~Native aa64 backend: managed-pointer-to-iv deref segfaults at dispatch~~ — FIXED 2026-05-22
- Root cause: `pkg/native/arm64/arm64_emit.bn:emitBox` silently
  returned for non-OP_ALLOC operands, so `box(iv)` for a loaded
  iv (the way to construct `@(*I)` / `@(@I)`) never emitted the
  `bn_rt__Box` call — `p` (the @-pointer) stayed uninitialized
  and downstream dispatch chased a stack alias instead of the
  heap iv.
- Fix: aggregate-load branch in `emitBox` — `getOperand` already
  returns a register holding the pointer to the data (per
  `common.SpillHoldsAggregatePointer`); pass it directly to
  `bn_rt__Box`.  Mirrors LLVM's `emitBoxInstr` non-OP_ALLOC arm.
- Conformance 444 / 445 / 450 / 458 flipped from xfail to pass
  on `builder-comp_native_aa64-comp_native_aa64` (binate 01bb5b6).

### ~~IR-gen: large literals force i64 in narrow-context operations~~ — FIXED
- The context-driven literal type resolution that was the proper
  fix has landed via `plan-ir-gen-typed-literals.md` Phases A/B:
  the type checker now resolves a literal's type from context
  (var-decl LHS, binop operand type, etc.) before IR-gen sees it,
  so `0xFFFFFFFF` in a uint32 context lands at TYP_INT
  Width=32 unsigned directly — no int64 promotion, no widening
  ripple.
- Pinned by `pkg/ir/gen_expr_test.bn`:
  `TestGenVarDeclUint32LiteralStaysUint32`,
  `TestGenBinopLiteralLhsAdoptsRhsType`,
  `TestGenUint32MaskLiteralNarrowsToUint32`, plus
  `TestGenNarrowIntLitStaysUntyped` for the inverse (narrow
  literals retain TYP_UNTYPED_INT for further inference).
- End-to-end probe: `func ror32(x, n uint32) uint32 { return (x >> n
  | x << (32-n)) & 0xFFFFFFFF }` compiles and runs cleanly on both
  host (builder-comp) and arm32-baremetal (cross-compile) — no
  "ret i64 in i32-result function" mismatch.  Verified 2026-05-24.

### ~~Substitute LP64-pinned conformance tests with target-aware variants~~ — DONE 2026-05-22
- **Mechanism**: `conformance/run.sh` now honors per-mode
  `NNN_name.expected.<mode>` (and `.error.<mode>`) overrides,
  mirroring the `.xfail.<mode>` convention.  See binate 39bac8a.
- **Tests retired**: 290 (override) and 330 (rewritten to
  `bit_cast(int64, ...)`).  Both xfail.builder-comp_arm32_-
  baremetal markers gone.  See binate 0044cde.
- Approach for future arm32-broken tests: either drop in
  an `.expected.<mode>` override (option 1) or rewrite the .bn
  to be target-agnostic (option 3).  The substitution-syntax
  option (option 2) wasn't needed.

### ~~`println(int64)` hangs on arm32-baremetal~~ — FIXED 2026-05-22
- Diagnosis was on the right track (int64 codegen on ILP32) but
  wrong about the AEABI helper.  The actual fixes landed on main
  in three coupled commits: `c2f8501` routes `println(int64)`
  through `bootstrap.formatInt64` (emitPrintInt was previously
  truncating int64 args to the target's `int` via a cast to
  formatInt's declared param type); `d5195f0` types int64-magnitude
  integer literals as int64 (TYP_UNTYPED_INT was lowering via
  llvmType to i32 on arm32, silently truncating wide constants)
  and preserves int64 width through unary-minus; `38f9319` fixes
  formatInt's int-min handling and gives 424 an arm32 .expected
  override.
- 330 now passes in `builder-comp_arm32_baremetal` with no xfail.

### ~~bnc: function-call element inside `@[]@[]char{...}` composite literal stores wrong value~~ — FIXED 2026-05-23
- **Was**: `var a @[]@[]char = @[]@[]char{buf.CopyStr("libc")}`
  compiled fine but at runtime `a[0]` was empty (len=0).  Cause:
  `pkg/ir/gen_access.bn:genManagedSliceLit` stored each element
  without a refcount handoff, so a fresh managed value from a
  call (registered as a temp by `gen_call.bn`) got RefDec'd by
  the end-of-statement temp cleanup — leaving the slot with a
  dangling data ptr + freed header.
- **Fix**: mirror `gen_short_var.bn`'s `var x = …` handoff
  pattern in `genManagedSliceLit` — for managed-ptr / managed-
  slice element types, `consumeTemp` if `isFreshManagedPtr` /
  `isFreshManagedSlice` (slot inherits the temp's refcount),
  otherwise `EmitRefInc` / `emitManagedSliceRefInc` (slot takes
  its own reference).
- **Pinned by**: conformance/473_mslice_mslice_char_lit_call_elem
  (output check on `@[]@[]char{copyStr("a"), copyStr("b")}`).

### ~~bnc: `return ""` for `@[]char` leaves undeclared `bn_libc__Memcpy`~~ — FIXED
- **Surfaced by**: adding `--test --run <substr>` to `cmd/bnc`'s
  generated test runner (`21c03a4`).  The generator wanted
  `func _runnerFilter() @[]char { ...; return "" }`; the bnc codegen
  lowered the `""` exit-path literal to
  `call void @bn_libc__Memcpy(%dst, %src, i64 0)` (size-0 memcpy
  to copy zero bytes from a rodata placeholder into a freshly
  `rt.MakeManagedSlice`'d 0-length buffer).  The generated runner
  module imports `pkg/bootstrap` + the test packages — but not
  `pkg/libc` directly — so `test_main.ll` has no
  `declare … @bn_libc__Memcpy` and clang errors with
  `use of undefined value '@bn_libc__Memcpy'`.
- **Workaround in place**: the generator returns a zero-init local
  (`var empty @[]char; … return empty`) instead of `""`.  See
  `genTestRunner` in `cmd/bnc/test.bn` and the comment block above
  the `_runnerFilter` emission.
- **Two clean fixes**:
  1. In codegen, when lowering a `""` literal for `@[]char`, skip
     the `libc.Memcpy` emit when the size is statically zero (no
     bytes to copy — the `rt.MakeManagedSlice` already produced an
     empty backing).  Plausibly the right call regardless of this bug.
  2. Or: emit a `declare void @bn_libc__Memcpy(i8*, i8*, i64)` (and
     similar implicit-use declarations) into every module that calls
     into them through string-literal lowering, regardless of whether
     `pkg/libc` is in the import set.
- **Repro after removing the workaround**:
    1. Revert the `var empty` branch in `genTestRunner` back to
       `return ""`.
    2. `go run cmd/bnc -- --test --build-dir <tmp> cmd/bni` — clang
       fails on `test_main.ll` with the undefined-value error.
  Test would live in `pkg/codegen` (a minimal module with a single
  `@[]char`-returning function that does `return ""`).  Not yet
  added — recommend adding alongside fix (1).

### ~~pkg/vm: VMFunc.Vtable / VMClosureRec lazy allocs leak on VMFunc death~~ — FIXED
- VMFunc's lazy heap blocks moved from raw `int` slots (filled via
  `rt.RawAlloc`) to managed struct types: `VMFuncVtable`,
  `VMClosureRec`, and the new `VMFuncHandle` (a 16-byte
  `{VtableAddr, DataAddr}` block matching the `@__handle.F` static
  shape so dispatch is uniform between bytecode-only and natively-
  compiled functions).  `VMFunc.Vtable` / `ClosureRec` / `Handle`
  are now `@VMFuncVtable` / `@VMClosureRec` / `@VMFuncHandle`
  fields, allocated via `make(...)` in `vm_exec_funcref.bn:ensureHandle`.
  VMFunc's auto-emitted dtor refdec's all three on death; no leak.
- Same fix for `ExternBinding.HandleAddr` — now `@VMFuncHandle`.
- Phase 4 of `plan-uniform-native-fnptrs.md` is the umbrella that
  carried this in (along with the dtor-handle interop fix and the
  aa64 handler additions).

### ~~pkg/vm:TestExecRefIncRefDecInline crashes under boot-comp-int-int~~ — FIXED
- Phases 1–3 of `plan-uniform-native-fnptrs.md` landed
  (`9561a3b`, `c557870`).  Pre-existing diagnostic detail retained
  below for context.
- **Repro**: `./scripts/unittest/run.sh boot-comp-int-int pkg/vm`.
  Symptom is actually a **SIGSEGV** (exit 139), not a hang —
  earlier "hang past 8 min" reports were the runner timing out
  on the segfaulted child.  xfail marker:
  `scripts/unittest/pkg-vm.xfail.boot-comp-int-int`.
- **Shape**: three-level VM nesting.  OUTER cmd/bni native dispatches
  the inner cmd/bni's bytecode (the unit-test harness); the test
  creates a fresh VM_test via `vm.NewVM(...)` and runs a hand-built
  IR module — `EmitMake → EmitRefInc → EmitRefDec (rc=1, fast
  path) → BC_CALL "rt.Refcount" → EmitRefDec (rc=0, slow path) →
  BC_RETURN`.
- **Bisection** (variant-by-variant build of the IR module):
    - `EmitMake` (BC_ALLOC) alone — ✅ returns.
    - `EmitMake + EmitRefInc` — ✅ returns.
    - `EmitMake + EmitRefInc + EmitRefDec(fast)` — ✅ returns.
    - `+ BC_CALL "rt.Refcount"` — ❌ crashes.
  So the trigger is the BC_CALL extern dispatch on a name that's
  not in VM_test.Funcs but IS in VM_test.Externs (registered via
  RegisterStandardExterns).
- **Specific to 3-level nesting.**  pkg/vm passes 107/107 under
  boot-comp-int (2-level): TestExecRefIncRefDecInline runs cleanly
  there.  The crash only manifests in the deeper boot-comp-int-int
  chain.
- **Crash details (2026-05-12 via lldb on `/tmp/bni_dbg` built with
  `-g`)**:
    - `EXC_BAD_ACCESS (code=1, address=0x1)` in OUTER native
      `bn_vm__execMemoryOp` at line 251 — the BC_LOAD8 handler's
      `regs[instr.Dst] = cast(int, p[0])`.
    - The BC_LOAD8 being processed lives in
      `VM_INNER.Funcs[1068].Code[97]` (= `vm.execMemoryOp`'s OWN
      bytecode); pc=98 (one past). Instruction is
      `(Op=43, Dst=78, Src1=77, Imm=0)`.
    - vm.execMemoryOp's register 77 holds `0x01`. Bytecode at
      pc=95/96/97: `BC_LOAD_IMM R76, 0` → `BC_ELEM_PTR R77 = R75
      + R76*1` → `BC_LOAD8 R78 = *R77`. This corresponds to the
      source-level `cast(int, p[0])` where `p = bit_cast(*uint8,
      regs[instr.Src1])`. So source-level `p == 0x01` —
      vm.execMemoryOp was called with a `regs+instr` pair where
      `regs[instr.Src1] == 1`.
    - Caller of execMemoryOp (saved in execMemoryOp's frame
      header): funcIdx=1060 (= `vm.execLoop`) at saved pc=185.
      Caller of that inner execLoop (savedFuncIdx=1064) at pc=91.
      The inner execLoop's parameters at regsOff=12368 are
      reg[0]=0xAF079D310 (vm), reg[1]=1032 (funcIdx), reg[2]=1168
      (regsOff).
    - The inner execLoop's `vm` (0xAF079D310) is NOT the
      VM_INNER_CMD_BNI (0xAF0B58510) we entered through — so we're
      at the deeper-nested level (probably the test's
      `execFunc(VM_T, ...)` → execLoop call, with vm=VM_T).
      Unresolved discrepancy: funcIdx=1032 is way out of range for
      a VM_T that LowerModule populated with one function. So
      either the inner execLoop is iterating something other than
      VM_T (some intermediate VM?), or our register-offset
      assumption for params (reg[0..2]) is off.
- **Root cause (2026-05-13, confirmed via lldb on `/tmp/bni_dbg`
  with `--run TestExecRefIncRefDecInline`)**: vtable.call slot for
  every `rt.*` extern binding in `VM_T.Externs` is stored as
  `0x423` (= 1059), a tiny integer that isn't a native function
  pointer.  By contrast `libc.*` / `bootstrap.*` bindings have
  proper native call slots (e.g. `0x10010d4d4`).
- **Dispatch path that crashes**: `dispatchExternBinding` reads
  `vtable[1] = 1059` and feeds it into `rt._call_shim_scalar` →
  `BC_CALL_INDIRECT` with `fnIdx=1059`.  The handler in inner
  `pkg/vm.execLoop` does `calleeFuncIdx = fnIdx - 1 = 1058`,
  passes the `1058 < len(vm.Funcs)` check (`INNER vm.Funcs.len`
  = 1194), and pushes a frame for `vm.Funcs[1058]` — which is
  `vm.genModule` (a `vm_test.bn` helper).  genModule's first
  action is `toBytes(src)`, which dereferences `src.data`; src
  is actually the closure record passed as `dataPtr` (=
  `b.DataAddr`), whose word 0 is `rt.DATA_KIND_VM_CLOSURE_REC =
  1`.  Reading the byte at address `0x1` segfaults — exit 139.
  (Also explains the 44 GB memory blow-up the user observed when
  leaving the test running: genModule continues past toBytes
  into `parser.New / ParseFile` parsing the closure record as
  Binate source — unbounded allocation.)
- **Why vtable.call is the wrong number (cross-VM index leak)**:
  BC_FUNC_VALUE construction (Path B in
  `pkg/vm/vm_exec_funcref.bn:99-107`) sets
  `vtPtr[1] = bit_cast(int, _raw_func_addr(TrampolineScalar))`.
  `_raw_func_addr` lowers to BC_FUNC_ADDR.  When INNER
  pkg/vm.execLoop's bytecode dispatches BC_FUNC_VALUE, it
  source-level-calls `execFuncRefOp(vm=INNER vm, …)`.  But
  execFuncRefOp's BYTECODE (which contains the BC_FUNC_ADDR)
  is then iterated by OUTER NATIVE execLoop (one level up the
  call ladder).  OUTER native execLoop's BC_FUNC_ADDR handler
  uses OUTER's `vm` = VM_INNER_CMD_BNI for the LookupFunc, not
  the inner level's vm.  OUTER_vm.LookupFunc("vm.TrampolineScalar")
  = 1058, so `vtPtr[1] = 1059`.
- **Both directly verified via lldb**:
    - `INNER vm.LookupFunc("vm.TrampolineScalar")` = 1076,
      `INNER vm.Funcs[1076].Name = "vm.TrampolineScalar"`,
      `INNER vm.Funcs[1058].Name = "vm.genModule"`.
    - `execFuncRefOp.CallCache[22]` (the slot for the BC_FUNC_ADDR
      to TrampolineScalar) = 1076 in INNER vm.
    - But the actual stored `vtable[1]` for all rt.* externs
      registered in `VM_T.Externs` = 1059.
  So the construction came from a DIFFERENT execFuncRefOp execution
  context — namely the one iterated by OUTER NATIVE execLoop's
  handler chain.
- **Generalized bug shape**: any function-value vtable whose `call`
  slot is a 1-based VM index (Path B) is meaningful only in the
  vm at construction time.  In 3-level VM nesting, the vtable can
  be constructed by an upper-level execLoop and consumed by a
  lower-level execLoop, so the numeric index resolves to the
  wrong function.  Path A (extern registry fallback, libc.* /
  bootstrap.*) doesn't have this problem because vtables there
  hold native function pointers (immune to vm-context shifts).
- **Possible fixes (require user buy-in)**:
    1. Make Path B's `call` slot a NATIVE function pointer (the
       address of TrampolineScalar / TrampolineAggregate in the
       containing process).  In bytecode-mode VMs the index-based
       path goes away; BC_CALL_INDIRECT's `dispatchNativeIndirect`
       arm (Imm=8/9) takes over uniformly.  Cost: TrampolineScalar
       needs to be reachable as a native function from any vm
       depth — works if the outermost host is always native cmd/bni,
       which is the assumption.
    2. Store a vm-identity tag alongside the numeric index and
       translate at dispatch time.  More invasive.
    3. Re-resolve at first dispatch (lazy-translate the numeric
       call slot through dispatch-time vm.LookupFunc by Name).
       Requires keeping the symbol name in the vtable record.
- **Surfaced by** the boot-comp-int-int unit-test sweep after the
  vm_extern.bn cleanup (`a6a74c8`).  Pre-cleanup the test was
  hidden behind a separate codegen bug fixed in `666f2c9`.
- **Repro is now seconds**: `/tmp/bni_dbg -root <root> cmd/bni
  -- --test --run TestExecRefIncRefDecInline -root <root> pkg/vm`
  segfaults within ~2 s of launch (needs the `--run` filter from
  `6bea5ba`).
- **Investigation owner**: in progress.  Next concrete step:
  from lldb at the SEGV, call (or inline-script) the INNER vm's
  LookupFunc on `"vm.TrampolineScalar"` and compare against a
  linear scan of `INNER vm.Funcs[i].Name`.  If they disagree the
  bug is in funcIndex insertion / probing; if they agree at idx
  1058 the bug is in LowerModule's appendVMFunc / funcIndexSet
  pairing.

### ~~Pointers to interface values~~ — DONE 2026-05-21
- **Plan**: `plan-pointers-to-iface-values.md` (sliced P.1–P.5).
  Slices P.1 (audit) + P.2 (fix `@(*I)` / `@(@I)` deref-
  dispatch) LANDED 2026-05-20; P.3 (smoothing for pointer-to-iv
  receivers) + P.4 (iv-in-slice / iv-in-array element-write)
  LANDED 2026-05-21.  P.5 (bootstrap parity) DROPPED — boot
  mode is gone.
- Design pinned in `claude-notes.md` § "Interfaces" line 421:
  `**Stringer`, `*@Stringer`, `@(*Stringer)`, `@(@Stringer)` are
  all valid pointer-to-iv shapes; parens are required by the
  grammar to disambiguate the `@(@…)` form.
- **Conformance pins**: 408 + 443 + 444 + 445 cover
  `(*p).Foo()` dispatch through every shape; 438 + 452 + 453 +
  450 cover `p.Foo()` smoothing; 439 + 440 + 441 cover
  iv-in-slice / iv-in-array; 442 pins pointer-to-iv struct
  field; 456 pins the orthogonal `(*p).x` bnc-compiled bug
  still in `gen_selector.bn` (see entry above).
- Was needed for: generics (`*T` where `T=Stringer`), out
  parameters, arrays of interfaces, containers.

### ~~Test harness `isTestResultReturn` should resolve type aliases~~ — FIXED
- The test harnesses (bootstrap Go `main.go` and self-hosted `cmd/bnc/test.bn`) only accept `testing.TestResult` (qualified) or `@[]char` (literal managed-slice of char) as test return types.
- They don't resolve type aliases, so an unqualified `TestResult` from within the `pkg/builtin/testing` package itself is rejected ("wrong signature").
- **Fix**: resolve the return type through aliases before checking. If the return type is a named type in the current package, look up its definition and check the underlying type.
- **Workaround**: use `@[]char` as the return type in `pkg/builtin/testing/testing_test.bn`.
- Affects: `cmd/bnc/test.bn:isTestResultReturn`, `bootstrap/main.go:isTestResultReturn`.

### ~~Type-checker drops typed-const value through untyped binop fold~~ — FIXED 2026-05-23

- **Discovered + fixed**: 2026-05-23, while wiring up
  plan-ir-gen-typed-literals.md Phase A4 (consume the type
  checker's bignum fold from IR-gen).
- **Symptom**: when one operand of an untyped-arithmetic binop was
  an EXPR_IDENT referring to a bare iota-counted const (e.g.
  `keyword_start + 1` in pkg/token.bn:148, where `keyword_start`
  is declared inside `const ( ... Type = iota; ... ; keyword_start )`),
  the type checker treated the binop as foldable and wrote a
  result Type carrying `HasLitVal=true`, but `LitMag` on that
  result reflected only the untyped operand's value (the literal
  `1`), not the const's iota.  In effect the fold computed
  `0 + 1 = 1` instead of `iota_of_keyword_start + 1`.
- **Root cause**: `pkg/types/check_decl.bn:checkConstDecl` for a
  bare iota'd const called `defineConst(name, TypUntypedInt())` —
  the predeclared singleton, with no `HasLitVal` attached.  At
  reference time, `checkIdent` returned that LitVal-less
  singleton; `foldIntArith` bailed (lt.HasLitVal == false) and
  fell through to `commonType`, which returned the OTHER
  operand's Type — so the binop's resolved Type inherited the
  literal's LitVal.
- **Fix** (binate `7ced362` / main `936a904`): construct a fresh
  `TYP_UNTYPED_INT` Type with `HasLitVal=true` / `LitMag=c.Iota`
  / `LitSign=false` for each bare iota-counted const, via a new
  `makeUntypedIntWithLit` helper.  `foldIntArith` now folds
  correctly through typed-const operand references, and Phase A4
  drops its direct-literal gate.
- **Tests**: `TestConstFoldIotaConstPlusLiteralFits` +
  `TestConstFoldIotaConstPlusLiteralOverflows` in
  pkg/types/check_expr_constfold_test.bn pin the fix at the
  type-checker layer.  Bare-metal conformance ticks up from
  398 → 400 passes (two integration tests that depended on
  iota-arithmetic now compile + run cleanly).

### ~~Integer literals and constant expressions~~ — RATIFIED + IMPLEMENTED 2026-05-15..2026-05-18
- **Spec**: `claude-notes.md` § "Integer literal value range and
  constant-expression arithmetic — DECIDED 2026-05-15".
- **Slices** (all on self-hosted bnc; xfail.boot on the conformance
  tests since boot mode uses the Go bootstrap which doesn't run
  const-fold or fit-check — and that mirror is explicitly out of
  scope given the move toward bnc-as-builder):
  - **Slice 0** (`97115da`) — `pkg/bignum` (uint64 magnitude + sign;
    parse / arithmetic / fit-checks).
  - **Slice 1** (`d463bf0`) — EXPR_INT_LIT rejects literals whose
    magnitude exceeds `2^64-1` at parse time
    (`409_err_int_literal_overflow`).
  - **Slice 2** (`24ca04a`) — `TYP_UNTYPED_INT` carries `(LitMag,
    LitSign)` primitives on Type; EXPR_UNARY MINUS propagates with
    the sign flipped; AssignableTo enforces the fit-check, unwrapping
    `TYP_NAMED` / `TYP_ALIAS` / `TYP_CONST`
    (`419_err_int_fits_uint8`).
  - **Slice 3** (`df58bdd`) — `+ - *` on literal-bearing untyped-int
    operands fold at type-check (`421_const_fold_arith`,
    `418_err_const_fold_overflow`).
  - **Slice 4** (`bcfdc20`) — `& | ^ << >>` fold the same way
    (`424_const_fold_bitwise`); folders extracted to
    `pkg/types/check_expr_constfold.bn` along the file-length cap.
  - **Cleanup** (`72a0bac`, after `bootstrap/63a8889` fixed the
    uint64-as-int64 bug) — drop the bootstrap workarounds; const-
    fold uses full bignum.Add / Sub / Mul across the int64 ∪ uint64
    union range (`422_const_fold_wide`).
  - **Slice 5** (`25fad6f`) — `/` and `%` fold with div-by-zero +
    Go-semantics sign rules; new bignum.Num.Div + Mod (with seven
    unit tests) underpin the fold (`426_const_fold_div_mod`,
    `427_err_const_fold_div_by_zero`).

### ~~Bootstrap Go interpreter: uint64 ordering / division go through int64 (signed)~~ — FIXED 2026-05-18
- **Fix**: `bootstrap/63a8889` updated `evalIntBinaryOp` to
  dispatch on operand signedness for the ops where it matters
  (SLASH, PERCENT, SHR, LT, GT, LEQ, GEQ).  Unsigned uint64 values
  with the high bit set now compare and divide correctly under
  bnc-interpreted execution.
- **Symptom**: in `boot` mode, uint64 comparisons (`<`, `>`, `<=`,
  `>=`) and division (`/`) gave wrong results when one operand
  had the high bit set.  Concrete repro: `cast(uint64, 1) << 63 >
  5` was **false** under boot, true under boot-comp.
- **Cleanup landed in `binate/72a0bac`**: dropped the
  bootstrap-specific workarounds in `pkg/bignum.parseDigits`
  (precomputed thresholds → natural `uint64Max - du / base`
  overflow check), `pkg/types/types_assignable.bn:untypedIntLitFitsTarget`
  (inline bit-shift bounds → bignum.Num.Fits* methods), and
  `pkg/types/check_expr_constfold.bn:foldIntArith`
  (31-bit-magnitude window → full bignum.Add / Sub / Mul).
  `pkg/bignum` xfail.boot marker removed.
- **Pinned by** `conformance/422_const_fold_wide` (wide-fold cases
  that the 31-bit window couldn't handle).

### ~~Native AArch64 backend — regPool saturation (cluster A follow-up)~~ — WRAPPED UP
- **Silent-corruption hazard removed** (`e8dfb85`, 2026-05-01).
  `pkg/native/arm64/arm64_regmap.bn:regPool(i)` previously returned
  X15 for any `i >= 6`, silently aliasing distinct SSA values when
  more than 7 live scratch regs were needed (the original cluster-A
  miscompile shape). It now panics with a clear message that prints
  the offending `ir.OP_*` so the next saturation case identifies
  itself.
- **Two live sites fixed**: `emitCall` (8-arg call in
  `046_many_params`, `e8dfb85`) and `emitReturn`'s sret + pack-into-
  X0..X7 paths (9-value return in pkg/asm/parse, `f704e09`).  Both
  walked `ins.Args` without resetting the regmap; fix is per-arg
  `rm.ResetRegs()` between arg slots (plus reload of `dstPtr` inside
  the sret loop so the reset doesn't strand it).
- **Pool extended to X9..X17** (`ecdd8ad`, 2026-05-14).  X16/X17 are
  AAPCS IP0/IP1 — caller-saved intra-procedure scratches; safe under
  two disciplines (audited in tree):
    1. *BL discipline.* No emitter reads a pool reg after a BL/BLR;
       every BL site is followed by `rm.ResetRegs()`.
    2. *Direct-use discipline.* emitCall / emitCallIndirect use
       X16/X17 directly outside the pool, paired with per-arg
       `rm.ResetRegs()` so the pool never hands those regs back
       inside the same op.
- **If a future op ever needs 10+ live scratches**, regPool panics
  at slot 9 with `currentEmitOp` in the message.  Fix is either the
  per-arg ResetRegs pattern (emitCall / emitReturn) or a real
  spill-on-exhaustion allocator.  Not actionable until something
  trips it; the playbook lives in the regPool source comment.

### ~~Bytecode VM: unsigned compare / div / rem dispatched as signed~~ — FIXED
- **Symptom**: pkg/bignum had 7 failing tests in `boot-comp-int`
  (Add / Sub / Mul / FitsUnsignedMax).  Root cause: uint64
  comparisons returned wrong answers when an operand had the high
  bit set (e.g. `uint64Max > 100` was false), and `uint64Max / 7`
  was 0.  bignum's overflow checks rely on both.
- **Root cause**: `pkg/vm/lower_instr_helpers.bn` always routed
  integer cmp through BC_S* and integer DIV/REM through BC_DIV /
  BC_REM regardless of operand signedness.  The unsigned opcodes
  (BC_ULT / BC_ULE / BC_UGT / BC_UGE / BC_UDIV / BC_UREM) were
  declared in `pkg/vm.bni` but had neither dispatch nor executors.
- **Fix**: lowerCmpOp / lowerBinOp check `Args[0].Typ` (resp.
  `instr.Typ`) for `IsInteger() && !Signed` and dispatch to the
  BC_U* opcodes; added executors that cast operands to uint64
  before applying the operator.

### ~~Bytecode VM: BC_LOAD8 zero-extends signed sub-word loads~~ — FIXED
- **Symptom**: under any `*-int*` mode, signed narrow integer values
  with the high bit set came back wrong after a load through alloca'd
  storage (`var x int32 = -5; x < 0` was false; `int32 INT_MIN.String()`
  printed `"2147483648"`; `int32(-5).Compare(5)` returned 1).
- **Root cause**: `pkg/vm/vm_exec_helpers.bn` `BC_LOAD8` zero-filled
  upper bits regardless of the loaded type's signedness, and the
  lowering in `pkg/vm/lower_memory.bn:lowerLoad` had no signal to
  distinguish signed from unsigned sub-word loads.
- **Fix**: `lowerLoad` now sets `bc.Aux = 1` when the load is a
  sub-word `TYP_INT` with `Signed == true`.  `BC_LOAD8` honours the
  flag by checking the assembled value's sign bit and OR-ing in the
  upper-bit mask when set.  Store side untouched (`BC_STORE8`
  already wrote the correct byte payload).
- **Tests**: `conformance/416_narrow_int_sign_ext.bn` (now passes
  in all `*-int*` modes; xfail markers dropped).  `pkg/std`
  unit tests `TestInt32StringNegative` + `TestInt32CompareNegatives`
  now pass; package xfail markers for `boot-comp-int` /
  `boot-comp-comp-int` / `boot-comp-int-int` dropped.

### ~~pkg/types boot-comp regression: hang during unit-test run~~ — FIXED
- **Root cause**: `pkg/ir/gen_method.bn` was missing the
  needsStructCopy-on-arg handling that `gen_call.bn` does for free-
  function calls. When a method takes a value-struct arg with
  managed fields (e.g. `p.addError(pos, msg)` where `pos` is
  `token.Pos` with `@[]char File`), the method-call path passed
  the struct by value WITHOUT RefIncing the managed field. The
  callee's scope cleanup then RefDec'd the field at end of scope,
  freeing the backing under the caller. After many such calls the
  freed-but-still-referenced backings led to use-after-free, then
  malloc heap corruption — eventually trapped at the next Malloc
  (which happened to be deep inside checkSrc → ParseFile →
  appendDecl during TestCheckSizeofBasic).
- **Why it appeared at 7251ffc**: parser helpers like next /
  expect / addError were free functions before that commit, so
  argument copies went through `gen_call.bn`'s correct handling.
  Method form routed them through `gen_method.bn` instead, which
  was missing the args-side struct-copy emit. The receiver-side
  branch already had it; only user args were missed.
- **Fix**: add the args-side `needsStructCopy` block to
  `gen_method.bn` (mirrors `gen_call.bn`), and also the
  `ctx.StmtGrewSP = true` markers on managed-slice / struct-copy
  results (also missed). Boot-comp `pkg/types` 270/270 after fix.

### ~~Array of managed-slice elements: string→@[]char in array context~~ — FIXED
- **Was**: two distinct bnc miscompiles for arrays whose element type
  is a char-slice (`@[]char`):
  - `[N]@[]char{"a","b","c"}` array-literal — silent wrong output,
    each slot's data ptr written but len/refptr/backing_len left at
    zero, so println saw len=0 and printed nothing.
  - `var arr [N]@[]char; arr[i] = "x"` indexed assignment — bnc
    aborted with `extractvalue operand must be aggregate type` on
    the refcount-Inc step (extractvalue called on a bare i8* from
    OP_CONST_STRING instead of a %BnManagedSlice).
  Both: var-decl / non-array-assign paths were converting
  OP_CONST_STRING → managed-slice value via EmitStringToChars; the
  array-literal and array-index-assign paths weren't.
- **Repros** (now passing in all modes):
  conformance/365_array_managed_elem_lit.bn,
  conformance/366_array_managed_elem_assign.bn.
- **Unit tests** in pkg/ir/gen_access_test.bn:
  TestArrayLitManagedElemEmitsRodataMSliceCopy,
  TestArrayIndexAssignManagedElemEmitsRodataMSliceCopy.
- **Related verification sweep (2026-05-06)**: tested arrays of
  OTHER managed element shapes after the initial fix.  `[N]@T`
  and `[N]@[]int` (with @[]int{...} elements) work cleanly under
  bnc.  `[N]struct-with-managed-field` revealed two additional
  bugs in genCompositeLit and genArrayLit, now fixed and pinned
  by conformance/367 + 368 and
  TestGenCompositeLitStructManagedCharField:
  - genCompositeLit's per-field string→char-slice conversion was
    gated `&& ft.Kind == types.TYP_SLICE`, so it only fired for
    raw-slice fields; @[]char fields fell through and the
    managed-slice RefInc / store wrote 8 bytes into the 32-byte
    slot.  Fix: drop the kind gate (isCharSliceType already
    matches both raw and managed).
  - genArrayLit didn't load struct values from their alloca
    pointer before storing into the array slot (mirroring what
    gen_control.bn's array-index-assign branch already did), so
    `[N]S{S{...}, ...}` wrote each element's i8* alloca pointer
    into the struct-sized slot instead of the struct value.
    Fix: add the same load-from-alloca guard.
- **Third site, found 2026-05-07** while resuming the unit-test
  cleanup sweep into asm / bnc / bni / bnlint args fixtures
  (which want to use `@[]@[]char{"a","b",...}` in place of
  `make_slice(@[]char,N)` + indexed assigns): genManagedSliceLit
  had the same gap.  String-literal elements stored only their
  bare data pointer (8 bytes) into the 32-byte managed-slice
  element slot, so reads came back len=0 (silent empty output).
  Fixed and pinned by conformance/372 +
  TestManagedSliceLitCharElemEmitsRodataMSliceCopy.  All three
  sites — genArrayLit, gen_control's array-branch, gen_composite
  per-field, genManagedSliceLit — now apply the same isCharSliceType
  + OP_CONST_STRING → EmitStringToChars conversion.  If a fourth
  store site surfaces, look for a missing instance of that same
  pattern.

### ~~boot-comp-int-int: blocked on registerPureCExterns from interpreted cmd/bni~~ — DONE (2026-05-07)
- **Resolved by**: `b9e1fed` (BC_FUNC_VALUE registry-fallback in
  execFuncRefOp). `2662c5c` then unblocked the build chain by
  fixing four leftover `TypeName(t)` free-function call sites in
  `pkg/types/check_decl_func.bn`. Mode now in the `all` modeset.
  boot-comp-int-int: 314 passed / 0 failed / 1 skipped (the
  pre-existing `272_raw_slice_star_sugar.xfail`).
- **Repro**: `conformance/run.sh boot-comp-int-int 001_hello`.
  Smaller repro: e2e/print-args.sh's `bni-under-bni` case
  (currently SKIPed pointing here).
- **State (2026-05-04)**: TWO root causes were stacked.
  1. **vm.Stack overflow** — FIXED via OP_SP_RESTORE plumbing
     across IR + all backends + IR-gen end-of-statement emission.
     Five-step series: `322a90a`, `2e1a4c3`, `7079fa6`, `f47f474`,
     `3393e62`.
  2. **Infinite recursion** — FIXED. Inner cmd/bni called
     `bootstrap.Args()` and got the OUTER process's full argv
     (including `cmd/bni` itself), so its parseArgs reinterpreted
     cmd/bni at every level. Fix: cmd/bni now registers a Binate
     shim (`progArgsAfterDash`) under the `"bootstrap.Args"`
     extern name in the per-VM registry, so programs running in
     bni's VM see post-`--` args (matching the spec and the Go
     bootstrap interpreter). This is what made the original "leak"
     symptom (8 MB vmInst per recursion level) catastrophic.
  3. **CURRENT BLOCKER**: registerPureCExterns crashes when called
     from interpreted cmd/bni. `var libcMalloc *func(int) *uint8 =
     libc.Malloc` requires LookupFunc("libc.Malloc") to find a
     VMFunc; libc.Malloc has no `.bn` body, so lookup fails and
     execLoop calls rt.Exit(1) with "vm: function not found:
     libc.Malloc". Outer cmd/bni's main runs natively (so the
     direct function-pointer dereference works); inner cmd/bni
     runs as bytecode (so the same code path is hit through
     BC_FUNC_VALUE, which can only resolve VMFunc names).
  - Introduced by the registry refactor (`a841f30`, `9486de9`,
    `faa98dc`). Pre-refactor, hand-coded arms in vm_extern.bn
    served libc/bootstrap calls without any registration step;
    refactor moved bindings into a per-VM registry that requires
    a function value at registration time.
- **Chosen fix (2026-05-06)**: extend `BC_FUNC_VALUE`'s
  `LookupFunc` miss path in `pkg/vm/vm_exec_helpers.bn:execFuncRefOp`
  to fall back to the executing VM's `vm.Externs` registry. On
  hit, build the function value as
  `{vtable=ExternBinding.VtableAddr, data=ExternBinding.DataAddr}`
  — same shape `OP_FUNC_VALUE` produces today, just sourced from
  the registry instead of from `vm.Funcs`. ~15 lines, one file.
  - **Why this and not a manifest / .bn-body wrappers**: the wall
    is at the lookup. The registry is already populated by each
    layer's host (cmd/bni's `registerPureCExterns`) before the
    next layer's main runs, so each layer's `BC_FUNC_VALUE` is
    dispatched by a VM whose `vm.Externs` already has the
    bindings. Works at arbitrary recursion depth without any
    bytecode-side compile-time emission and without forcing
    pkg/libc.bn (or analogous wrapper bodies) to be loaded into
    every nested VM.
  - **Soft limitation**: a user program that does
    `var f = libc.Malloc` at top-level with no surrounding
    `RegisterExtern("libc.Malloc", ...)` in the calling VM gets
    "function not found". Not an issue for cmd/bni-on-cmd/bni;
    soft problem for ad-hoc scripts under unusual embeddings.
- **Considered and rejected**:
  1. Detect interpreted context in cmd/bni and skip
     registerPureCExterns. Fragile; "interpreted" detection isn't
     first-class.
  2. Revert pure-C externs out of the registry — mixes two
     dispatch shapes per extern name.
  3. Compile-time-emitted shim manifest in both native backends +
     `rt.LookupShim`. Drafted in (now-deleted)
     `plan-shim-manifest.md`. Comparable cost to option 2 below;
     redundant with the chosen fix; only wins for the
     "no-pre-registration" case which doesn't apply here.
  4. `.bn`-body wrappers (intrinsic-call form `_c_<name>` or
     `@cextern` annotation) for pure-C externs. Cleanest in
     theory but doesn't help nested VMs that don't load
     `pkg/libc.bn` — same wall recurs at depth.
- **CI status**: now in the `all` modeset; conformance, unit-tests,
  and perf-tests workflows run boot-comp-int-int as a matrix entry.
- **Earlier original diagnosis** (pre-leak-fix, kept for context):
  caller was bytecode `rt.Free`, fnIdx was a NATIVE function
  pointer (e.g. 0x1043F5BAC ≈ 4.37e9) being treated as a 1-
  based VM index. The allocation was made by NATIVE rt.Alloc
  via the BC_MAKE_SLICE handler in vm_exec.bn calling native
  rt.MakeManagedSlice → native rt.Alloc, which stored
  `_raw_func_addr(RawFree)` in h[1] as a native pointer; later
  RefDec'd by bytecode rt.RefDec → bytecode rt.Free →
  BC_CALL_INDIRECT mismatch. Phase 3 trampolines retire this.

### ~~Native AArch64 backend — emitCallFuncValue slice-arg ABI mismatch~~ — FIXED
- Root cause was actually in `emitFuncValueShims` (arm64.bn), not
  the call site: the shim shuffles X1..XN → X0..X(N-1) to drop
  the closure-data slot, but counted register words by
  `len(fvTyp.Params)` instead of summing each param's
  `common.ArgWords`.  A slice param occupies 2 consecutive arg
  registers, so the shim ran a single MOV X0, X1 and left
  slice.len in X2 dangling — the callee read X1 (= slice.data)
  as its len, so any `len(s)`-driven loop ran 0 iterations.
- Fix: sum `common.ArgWords(fvTyp.Params[i].Type)` across all
  params and shift that many register words.
- `conformance/364_funcval_slice_arg` now passes under
  boot-comp_native_aa64.

### Native AArch64 backend — interface dispatch — LANDED
- Implemented OP_IFACE_VALUE, OP_CALL_IFACE_METHOD, OP_IFACE_DTOR
  in pkg/native/arm64; added `__ivt.<...>` vtable emission to
  EmitObject; added TYP_INTERFACE_VALUE / TYP_INTERFACE_VALUE_MANAGED
  cases to IsAggregateTyp and PlanFrame's data-region allocator.
  See `arm64_iface.bn` + the new ops in `arm64_dispatch.bn`.
- Verified: boot-comp_native_aa64 conformance went from 0/327
  (everything failed at link with `_bn_entry undefined` — that
  side was fixed earlier in the same commit chain) → 321/1/6
  passing/failing/xfail.  The remaining failure (364) is the
  slice-arg ABI mismatch above.
- Layout note: matches LLVM's emit_impls.bn exactly — slot 0 is
  the receiver dtor (or null if no dtor in this TU), slots 1..N
  are method pointers in interface-declaration order, each slot
  is an 8-byte ARM64_RELOC_UNSIGNED fixup that the linker
  resolves to the symbol's absolute address.

### ~~Inline RefInc / fast-path inline RefDec (perf)~~ — DONE
- **Plan doc**: `explorations/plan-refcount-inlining.md` (Status: DONE).
- New IR ops `OP_REFINC` / `OP_REFDEC` added alongside the old `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC`; IR-gen switched to emit the new ops; old emitters (`EmitRefcountInc` / `EmitRefcountDec` / `EmitRefcountDecDtor`) deleted in favor of `EmitRefInc` / `EmitRefDec` / `EmitRefDecDtor`.
- All three backends (LLVM, VM, native arm64) lower the new ops inline:
  - LLVM: nil-check diamond + header GEP at -16 + load/{add,sub}/store, with a slow-path call to `@bn_rt__ZeroRefDestroy` for RefDec when the count hits zero.
  - VM: fused single-dispatch bytecode ops `BC_REFINC_INLINE` / `BC_REFDEC_INLINE_FAST` — one switch arm per refcount site, vs ~5 if the IR had pre-expanded to primitives.
  - arm64: CBZ + LDR(pre-index for RefInc, separate SUB+LDR for RefDec to keep ptrReg alive across the BL) + add/sub + STR + CBNZ for RefDec; BL `bn_rt__ZeroRefDestroy` only on the slow path.
- **Slow-path helper**: `rt.ZeroRefDestroy(ptr, dtor)` lives in `pkg/rt`; called only when the inline RefDec decrement leaves the refcount at zero. Runs the optional dtor (via `_call_dtor`) and `Free`.
- **User-visible impact**: none. All call sites are compiler-emitted.
- **Commits** (chronological): `eb7332e` (OP_REFINC), `9cb934d` (LLVM RefInc), `e972953` (VM RefInc), `8b896de` (arm64 RefInc), `34511bd` (RefInc switchover); `6aa78d1` (ZeroRefDestroy), `46e8e52` (OP_REFDEC), `a8104d2` (LLVM RefDec), `445e40d` (VM RefDec), `a4847b2` (arm64 RefDec), `19502d4` (RefDec switchover + with-dtor tests).
- **Cleanup status (2026-05-02)**: IR/backend dead code is GONE — old `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` constants, all three backends' old dispatch arms, the non-INLINE `BC_REFINC` / `BC_REFDEC` bytecode ops + their VM exec handlers, and `emitRefcountCall` are all removed. The `bn_rt__RefInc` / `bn_rt__RefDec` runtime symbols (declared `pkg/rt.bni:122-127`, defined `pkg/rt/rt.bn:157,166`) are NOT dead — but their remaining callers are dubious and they should probably be retired:
  - **Remaining callers**: (a) VM extern handlers in `pkg/vm/vm_extern.bn` — the `rt.RefInc` / `rt.RefDec` extern arms at lines 21-29 plus the managed-slice copy/dtor paths at 169/175/191/195 that hand-RefInc element backings during structural copies; (b) conformance tests `092_rt_alloc`, `093_rt_managed_slice`, `104_rt_refcount`, which exercise these as a public manual-refcount API.
  - **Why retire**: with every compiled refcount op inlined, the runtime symbols exist only for these dubious users. Keeping them in `pkg/rt`'s public surface entrenches a manual-refcount escape hatch that nothing in the language model encourages. The `vm_extern.bn` callers are part of a broader "all of `vm_extern.bn` is dubious" question — the managed-slice copy paths there should probably move out of host code entirely.
  - **Scope when picked up**: drop or rewrite the three conformance tests; audit/migrate the `vm_extern.bn` paths (likely part of a larger vm_extern.bn rework); then delete the symbols from `pkg/rt.bni` + `pkg/rt/rt.bn`. Not a "just deletion" change — has public-API implications. The "VM extern dispatch: name → function-value registry" entry below describes the natural vehicle: the `rt.RefInc` / `rt.RefDec` extern arms cease to exist (no caller left to register), and the surgical refcount paths in `bootstrap.Args` / `ReadDir` get audited as part of that rework.

### ~~VM extern dispatch: name → function-value registry~~ — DONE
- ExternBinding registry + RegisterExtern / LookupExtern API:
  landed.
- BC_FUNC_VALUE registry-fallback (`b9e1fed`): execFuncRefOp
  consults `vm.Externs` on `LookupFunc` miss and constructs the
  function value from `binding.VtableAddr` / `DataAddr`.  Removes
  the chicken-and-egg that blocked nested-VM
  `var x = pure_C_extern` constructions.
- All host externs (rt.*, libc.*, the full bootstrap.* C-shaped
  surface) migrated through the registry; vm_extern.bn's
  execExtern is now a pure registry dispatch.
- ReadDir's migration surfaced a latent codegen bug: emit_funcvals.bn's
  aggregate-shim was emitting a register-style call
  (`%r = call <ret> @<fn>(...)`) for IsCExtern callees regardless
  of whether they used the C-ABI sret convention.  For >16-byte
  returns (e.g., `@[]@[]char`), the sret-declared callee would
  write the result through what it interpreted as the sret
  pointer (the first user arg), corrupting memory.  Fixed in
  `666f2c9` — sret-aware shim emission, now consistent with
  emit.bn (declarations) and emit_call.bn (regular call sites).

### Migrate self-hosted code to method form (opportunistic) — DONE 2026-05-13
- All originally-listed candidates landed (free function +
  method shim → all callers converted → shims dropped):
  - `pkg/buf.CharBuf` — `Len` / `Bytes` / `Freeze` /
    `WriteHexByte` / `WriteInt` / `WriteByte` / `WriteStr` (commits
    `174666c..8f96357`).  `New` and `CopyStr` stay free.
  - `pkg/asm/elf.BinBuf` — `WriteU8` / `WriteU16` / `WriteU32` /
    `WriteU64` / `WriteBytes` / `WriteZeros` / `Align` /
    `WriteAddr` / `grow`.
  - `pkg/asm.Assembler` — `SetError` / `SetSection` / `DefineLabel` /
    `SetGlobal` / `SetWeak` / `AddFixup` / `Emit*` /
    `Align`/`AlignFill`/`Zero`/`Fill` / `Finalize` plus
    helpers (`findSection`/`addReloc`/etc.).
  - `pkg/types.Type` — `IsInteger` / `IsFloat` / `Identical` /
    `AssignableTo` / `ResolveAlias` / `SliceElem` / `PointerElem` /
    `FieldByName` / `NeedsDestruction` / `IsConst` / `StripConst` /
    `TypeName`.
  - `pkg/types.Scope` and `pkg/types.Checker` — full API
    methodified (Lookup / Check / CheckPackage / ExprType /
    LoadPackageInterface / etc.; commits `cb0f624`,
    `0b573b7`).
  - `pkg/parser.Parser` — top-level (`Parse*`) and primitives
    (`next` / `expect` / `got` / `peekTok`); commit `5fbba29`.
  - `pkg/lexer.Lexer` — `Next` / `advance` / `peek` / `col` /
    `curPos` / `newline` / `scan`.
  - `pkg/asm/parse.Parser` — `ParseLine` / `ParseFile`
    (commit `d18e5c8`).
  - `pkg/native/common.RegMap` — full API (commit `33e6475`).
  - `pkg/vm.VM` — `CallFunc` / `CallByVMFunc` (`9b4465d`),
    later `LookupFunc` / `LookupExtern` / `LowerModule` /
    `LowerOneFunc` / `LowerOneFuncShadow` / `RegisterExtern`
    (`b6b6155`).
  - `pkg/ir.Module` — `AddFunc` / `AddGlobal` / `AddTypeDef` /
    `CollectStrings` (`00fd13a`), `FinalizeStrings` /
    `HasPackageInit` (`d5dc8f4`), `EmitInitDispatcher` /
    `EmitMainEntry` (`8d05e92`).
  - `pkg/ir.Block` — `Emit*` family (~50 emitters) migrated in
    a four-stage pass with a temporary `Block.Func` back-pointer:
    `6708b49` (back-pointer), `49254ba` (method form alongside),
    `8cf9093` (call sites), `d67231a` (drop shims).
  - `pkg/ir.Instr` — `IsTerminator` (`b320c98`).
- Migration discipline: each batch added method-form +
  free-function shim, converted all call sites, then dropped the
  shim — one commit per stage, conformance/`basic` green
  throughout.  Documented in CLAUDE.md.

### Interface embedding/extension — DONE 2026-05-13
- **Plan**: `plan-interface-embedding.md`.  Design ratified in
  `claude-notes.md` § "Interfaces" (extension paragraph) and
  detailed in `claude-discussion-detailed-notes.md` § "Interface
  Extension".  Vtable layout from `claude-plan-1.md` § 2.3.
- **Slices** (all committed on main):
  - **E.1**: parser + AST + reject-extension placeholder (parser
    accepts `interface X : I1, I2, ... { ... }`; parent list
    stored in the existing `Decl.Interfaces` field).
  - **E.2**: type-checker parent resolution + method-set
    propagation (no cycles via forward-ref-only rule, no
    duplicate parents, no same-name signature conflicts; impl
    satisfaction walks `ifaceFullMethods`).
  - **E.3**: IR-gen transitive impl emission + concat vtable
    codegen (`(R, child)` triggers `(R, ancestor)` ImplInfo
    entries; LLVM vtable `[any-block][parent1 full vtable]...[own]`).
  - **E.4 part 1**: dispatch through inherited methods —
    `findInterfaceMethod` walks the parent chain and returns an
    absolute vtable slot; codegen + VM consume the slot directly
    (the old `+1` adjustment is gone).
  - **E.4 part 2a**: type-checker iface upcast assignability
    (`*Child → *Parent` etc.) + latent `Identical` bug fix for
    iface types.
  - **E.4 part 2b**: explicit upcast IR/codegen — new
    `OP_IFACE_UPCAST`, LLVM lowering via static slot-offset GEP,
    VM lowering via runtime name-rewrite of the vtable's mangled
    suffix.
  - **E.5**: cross-package extension verified by conformance
    388; docs flipped from "not yet implemented" to "implemented".
    Conformance positives + negatives added in a follow-up
    (`6a5203b`) to pin user-facing error wording at the bnc
    layer — 395 (multi-parent), 396 (3-level deep), 397
    (forward-ref cycle), 398 (duplicate parent), 399 (method
    signature conflict), 400 (parent isn't an interface).
- **Coverage**: 4 direct tests for the `Identical` fix /
  inherited slot / GEP dispatch slot / managed-to-raw upcast
  (commits `d485136..277f8b0`); end-to-end conformance 387
  (same-package upcast) + 388 (cross-package upcast) + 395–400
  (extension positives + negatives); 11 type-checker tests
  covering single/multi/deep extension, diamond inheritance,
  parent recording, full-method-set order, forward-ref/self/
  non-interface/duplicate-parent/signature-conflict rejections;
  IR test for transitive ImplInfo emission + redundant-parent
  dedup + recursive vtable size; codegen test for the concat
  layout shape plus a direct test for `emitIfaceUpcast` (LLVM
  extract/GEP/rebuild sequence and parent-slot offset).
- **Connection to RTTI** (still open): if/when concrete-type
  assertions land, a `*TypeInfo` slot in the `any`-block makes
  it reachable from any interface vtable via offset 0 —
  independent of which interface the value is currently typed
  as.  Tracked separately in `notes-package-introspection.md`.

### `Self` type in interface declarations — RATIFIED 2026-05-12
- **Outcome**: ratified as DECIDED per the proposal in
  `claude-notes.md` § "`Self` type in interface declarations
  — DECIDED 2026-05-12".  Reserved identifier valid only
  inside interface declarations; substituted with the
  receiver type at impl-collection time.
- **Open question resolution**: methods using `Self` in
  non-receiver positions are **rejected** when called
  through an interface value (Rust's "object-safe"
  restriction).  Such methods are callable only through
  generic constraints where T is statically known.
  Rationale: the alternative (type-erased dispatch through
  `*Iface`) would require every impl to provide a
  heterogeneous entry point — `int.Compare(*Comparable)`
  would have no useful behavior when called with a
  `string`, leaving only a panicking type assertion as the
  implementable shape.
- **Downstream**: unblocks `plan-primitives-impl-interfaces.md`
  Slice 2b (`Comparable` / `Orderable` / `Hashable` for
  primitives) and the constrained-generics path in
  `plan-generics.md` (Slice 3).

### ~~Method receivers (no interfaces)~~ — DONE
- Methods supported across all four execution paths: boot (Go
  interpreter), boot-comp (LLVM), boot-comp-int (bytecode VM),
  boot-comp_native_aa64 (ARM64 native).
- Receiver kinds: `T`, `*T`, `@T` (and const variants where
  applicable). Static dispatch only — no interfaces.
- One level of receiver smoothing: `*T → T` (auto-deref), `T → *T`
  (auto-take-address), `@T → *T` (reinterpret). Honored in the type
  checker, bootstrap interpreter, bytecode VM, and LLVM IR-gen.
- IR-level naming: methods are fully qualified
  (`<pkgShort>.<TypeName>.<MethodName>`); the mangler converts every
  dot to `__`, yielding `bn_<pkgShort>__<TypeName>__<MethodName>` C
  symbols.
- Conformance: 322–331 cover positive cases (basic, managed, full
  smoothing table, mutation, cross-package), the @T → *T smoothing
  case, and the three negative cases (alias, builtin, duplicate).
- Bootstrap subset: methods are now in (`bootstrap-subset.md`,
  Functions section). `impl Type : Interface` and method values
  remain deferred — see "Function values" / "Cross-package method
  visibility in .bni" entries below for the open follow-ups.
- Decision summary in `claude-notes.md` § "Method resolution &
  dispatch — DECIDED" (receiver kinds, smoothing, naming, `_`
  receiver name).

### ~~pkg/vm: Stage 2b implicit-copy + OP_STRING_TO_ARRAY~~ — DONE (`9e9042a`)
- Added `BC_STRING_COPY_MS` (Stage 2b: fresh `@[]char` via
  `MakeManagedSlice` + memcpy from rodata) and `BC_STRING_COPY_ARR`
  (Stage 2c Phase 1: stack buffer of size N, zero-padded, with
  literal bytes copied in). Lowering of `OP_STRING_TO_CHARS` now
  branches on `instr.BoolVal`, mirroring the LLVM codegen path.
- Latent fix: `lowerStore` for `TYP_ARRAY` was a scalar 8-byte
  store (test `051_array_copy` passed by coincidence — only read
  element 0). Added array to both `lowerLoad` and `lowerStore`
  multi-word paths.
- Removed `xfail.boot-comp-int` markers on tests 298, 299, 307;
  boot-comp-int now at 258 passing (was 254, 7 xfails remain).
- Refactor: extracted `lowerLoad` / `lowerStore` / `lowerGetFieldPtr`
  into `pkg/vm/lower_memory.bn` to keep `lower_instr.bn` under the
  600-line cap.

### ~~Implement adjacent string-literal concatenation (C-style)~~ — DONE
- Implemented at the parser level (not lexer) because the lexer can't
  tell apart "merge me" from "you're between two grouped-import paths"
  — both look like STRING SEMI("\n") STRING. Parser merges only in
  `parsePrimaryExpr` (expression context), so grouped imports are
  unaffected.
- Cross-line merge works via a one-token parser lookahead (`peekTok`):
  if the current is STRING and the next is `SEMI("\n")` followed by
  another STRING, consume the SEMI as spurious and merge.
- Conformance test 308 covers same-line, cross-line, three-or-more,
  comment-in-gap, escapes, and the comma-blocks-merge negative case.
- Migrated `pkg/parser/parser.bn:135` (the original `// LONG-LINE
  ALLOWED` site) to use the new feature.

### ~~`&` on EXPR_SELECTOR doesn't return a field pointer (IR-gen bug)~~ — FIXED (`8866baa`)
- Pre-fix: `genUnary`'s `&` arm only special-cased EXPR_IDENT and
  EXPR_INDEX; an EXPR_SELECTOR fell through to `genExpr(e.X)` which
  emitted the LOAD of the field. Result: `&s.f` came back as the
  field VALUE rather than a field pointer; downstream
  deref/write-through touched the wrong memory.
- Fix shape was as anticipated: one branch in `genUnary`'s `&` arm
  routing EXPR_SELECTOR through `genSelectorPtr` (which already
  handles value structs, `@Struct`, `*Struct`, and indexed-element
  struct fields).
- Tests: `conformance/334_amp_on_selector` covers all four shapes
  (xfailed on boot — bootstrap doesn't support `*int` index-assign,
  separate issue not under test here). pkg/ir unit test
  `TestGenAmpOnLocalSelector` pins the IR shape directly: `&p.x`
  must produce OP_GET_FIELD_PTR and must NOT produce OP_LOAD-of-
  GET_FIELD_PTR.
- Discovered while writing diagnostic tests for the
  pkg/types-VM-regression entry below — `&target.PointerSize` and
  `target.PointerSize` returned the same number (the field address)
  in the VM, which initially looked like a VM-LOAD bug; that turned
  out to be a separate `IsGlobalRef` issue (also fixed), and the
  selector-`&` bug was the second bug they were tangled up with.

### ~~pkg/types unit tests fail under bytecode-VM modes (target.PointerSize)~~ — FIXED (`1b0cef8`)
- Symptom: 10 pkg/types tests failed under boot-comp-int /
  boot-comp-comp-int / boot-comp-comp (TestSizeOfPointers,
  TestSizeOfSlice, TestAlignOfPrimitives, TestAlignOfArray,
  TestSizeOfUniformStruct, TestSizeOfMixedStruct, TestFieldOffsetMixed,
  TestFieldOffsetPackedSmall, TestSizeOfNestedStruct,
  TestSizeOfStructWithSlice) — all transitively exercised
  `target.PointerSize` and saw a heap address instead of `8`.
- Both the original "write doesn't persist / stale zero" and the
  intermediate "OP_LOAD lowered to BC_MOV instead of BC_LOAD64"
  hypotheses were wrong. The actual bug was in **all three backends'
  global-pseudo-Instr detection**: they used a name-based heuristic
  (`Op == OP_ALLOC && len(StrVal) > 0`, with `lookupGlobalAddr(StrVal)
  != 0` as a tiebreaker for VM/arm64; LLVM used `ID == -1 &&
  len(StrVal) > 0`). Local parameter allocas tagged with the parameter
  name for debug info matched the same shape. When a local's name
  collided with a global, the local's storage was routed to the
  global's heap memory.
- Trigger in pkg/types: `MakeAliasType(name @[]char, target @Type)`
  has a parameter named `target` — same name as
  `var target TargetInfo`. The parameter prologue's STORE-into-slot
  wrote the parameter VALUE (a `@Type` pointer) into the global's
  memory; subsequent reads of the parameter loaded back from the
  global. Every call clobbered `target.PointerSize` with a heap
  pointer.
- Fix (`1b0cef8`): added `IsGlobalRef bool` to `ir.Instr`,
  `lookupVar` sets it on the global pseudo-Instr, all three backends
  (pkg/vm, pkg/codegen, pkg/native/arm64) key off the flag instead of
  the name. Regression test `conformance/333_param_shadows_global`
  covers the exact pattern.
- Discovery surfaced one separate IR-gen bug (still open): see "&` on
  EXPR_SELECTOR doesn't return a field pointer" entry above.
- Verified: boot-comp-int unit tests now 29/29 passing (was 28/29 with
  pkg/types failing 10 tests). conformance basic clean across modes.

### ~~boot-comp-int: cross-pkg multi-return struct destructure clobbers struct on 2nd+ call~~ — FIXED (`c5b29cb`)
- The hypothesis ("destructure path overlaps src/dst on 2nd call") was
  wrong. The actual bug was in BC_RETURN's multi-return *packing*:
  the branch chose MEMCPY vs scalar-store based on `sz > 8`, but
  `srcVal` is a *pointer* for any multi-word type (lowerLoad returns
  the alloca pointer for struct/slice/array). For a struct exactly 8
  bytes (like `Counter { Val int }`), the scalar branch wrote the
  pointer-to-callee's-local-alloca into the tuple slot; after the
  callee frame popped, the destructure landed a pointer-into-dead-
  stack-memory in the destination variable. The 1st call's
  destructure of `c` was already corrupt — just unobserved until the
  2nd call's `prev2` (= old `c.Val`) and the final `Read(c)`
  surfaced it.
- Fix: branch on type, not size. `VMFunc.ResultMultiWord []bool`
  populated at lower time via `isMultiWordField(t)`; BC_RETURN
  consults it and chooses MEMCPY for any multi-word type regardless
  of size.
- conformance/157_cross_pkg_struct_multiret xfail.boot-comp-int
  removed; passes boot-comp-int and boot-comp-comp-int.
- Conformance basic green (204/281/275 — boot-comp-int +1 pass);
  pkg/vm unit tests green.

### ~~boot-comp-int-int: SIGSEGV after ~218s (post-BC_RETURN-fix)~~ — FIXED (`900a44e` + `a723acb`)
- (Mode renamed from `boot-comp-int2-int2` after the int2→int rename in `b1e4f98`.)
- History (2026-04-25/26):
  1. Original symptom: SIGSEGV with no output.
  2. `bootstrap.ReadDir` was missing from `pkg/vm/vm_extern.bn` — added the binding. Fixed in `c44419f`.
  3. Next symptom: clean `vm: stack overflow` after ~35s on `001_hello` at 8 MiB stack.
  4. Probe at 64 MiB → clean overflow replaced by host SIGSEGV after ~335s.
  5. Probe at 1 MiB + diagnostic dump in `pushFrame` overflow handler → caller depth only **4** (main → runProgram → LowerModule → lowerFunc); `lowerFunc` runtime frame ~998 KB; lower-time frame only ~7912 B → **126x bloat per call**.
  6. Root cause identified: `BC_RETURN` was bumping `callerSP = vm.SP` whenever retVal pointed into callee region — leaking the entire callee frame on every call. In `lowerFunc`'s loop calling `lowerInstr`, ~90 × 11000 B ≈ 990 KB leaked.
  7. **FIXED in `be3c22e`**: `BC_RETURN` now mirrors `execFunc`'s copy-then-pop pattern, but with a precise size known at lower time (encoded in `BC_RETURN.Aux` for single returns; existing `totalSize` for multi-returns). Conformance test 320_struct_return_loop covers it.
  8. New symptom (2026-04-26 post-fix): `001_hello` runs for ~218s (vs 35s pre-fix), peaks at ~152 MiB RSS, then exits with SIGSEGV (139). No "vm: stack overflow" — this is genuine memory corruption / bad pointer, not a VM-stack issue.
- **Why progress matters**: pre-fix, the leak hit overflow within ~35s of useful work. Post-fix, ~6× more work happens before any failure, so the next bug is much further along the execution. The new SIGSEGV is a separate (heap-side) bug, not a regression.
- Not in the `all` modeset, so CI/default runs don't exercise it.
- **Diagnosis (2026-04-29)**: ASan caught a HOST stack-overflow
  inside `malloc`, triggered from
  `execLoop → execExtern → libc.Malloc`. Diagnostic instrumentation
  showed `execFuncCalls=1` and `execFuncDepth=1` throughout the
  entire 260M+ iteration run — so the leak was NOT host-recursion of
  `execFunc`. ulimit confirmed it was a true leak (8 MiB → 246s,
  64 MiB → 1264s, roughly 5x more time for 8x more stack).
- **Root cause**: 1 alloca outside execLoop's entry block —
  `var callArgs @[]int = make_slice(int, instr.Imm)` declared
  inside the BC_CALL extern branch. bnc emits the @[]int header
  alloca in that branch's BB, not the function entry, so each
  extern call leaks 32 bytes that's only released on execLoop
  return. 8 MiB / 32 = 262144 extern calls before overflow —
  matches the observed ~218s.
- **Fix (two commits)**: First (`900a44e`) hoisted callArgs's @[]int
  header alloca by declaring it at function entry — but bnc still
  emitted a temp alloca for `make_slice`'s sret return INSIDE the
  branch when the buffer needed to be (re)allocated, so the leak
  was only partly closed. Second (`a723acb`) closed it fully:
  pre-allocate a generously-sized callArgs (capacity 64) ONCE at
  entry; reuse across all extern calls; panic on overflow.
  Bundled with a defensive iterative-dtor reform of BC_REFDEC
  (no host recursion through dtor cascades), though that wasn't
  load-bearing for this specific bug.
- **Regression test**: `conformance/339_extern_call_loop.bn` —
  1M iterations of `bootstrap.Close(-1)` (cheap scalar-arg extern
  that doesn't push onto vm.Stack per call). Pre-fix, SIGSEGV at
  ~150K calls. Post-fix, runs in <1s.
- **Followup work landed in this same arc**:
  - `f3478cb` (codegen-side hoist for OP_MAKE_SLICE / sret OP_CALL):
    closes the bug class in the LLVM backend.
  - `daacfe3` (BC_LOAD_STR no-push): closes the parallel vm.Stack
    leak so loops with string-literal extern args don't overflow
    vm.Stack at ~262K iterations.
- **Aftermath**: After the full chain (`900a44e` + `a723acb` +
  `f3478cb` + `daacfe3`), boot-comp-int-int 001_hello no longer
  hangs OR crashes silently. It now exits cleanly with a
  diagnosable error from a SEPARATE bug:
  `vm: indirect call: function index out of range`. That comes
  from BC_CALL_INDIRECT's dtor-dispatch path (the new f08ddcb
  `rt._call_dtor` mechanism) — its own followup, tracked below.

### ~~bnc: hoist managed-slice allocas to function entry~~ — FIXED (`f3478cb`)
- pkg/codegen already hoisted OP_ALLOC decls to the entry block via
  emit_debug.bn's hoisting loop. But two other inline-alloca paths
  were leaking:
  - emitMakeSliceInstr's `.p = alloca %BnManagedSlice` slot for
    bn_rt__MakeManagedSlice's store/load shuffle.
  - emitCall's sret path's `.sret = alloca <type>` slot for callees
    using sret return convention.
- Fix: extended the hoisting loop to cover OP_MAKE_SLICE and sret
  OP_CALL via two new helpers (`emitMakeSliceAllocDecl`,
  `emitSretAllocDecl`). The original emit*Instr functions now emit
  only the non-alloca portion.
- Verified pkg/vm LLVM IR has zero non-entry allocas across all
  functions. With this change, the prior hand-hoisted fix
  in execLoop's BC_CALL extern branch (a723acb) is no longer
  load-bearing — the codegen would have hoisted that case too.
  The hand-hoist stays as belt-and-suspenders.
- bnc IR-gen still emits OP_ALLOC at the current insertion point;
  the codegen is what fixes it post-hoc. A future cleanup would
  move the hoisting upstream to IR-gen, but the current arrangement
  is correct.
- Independent followup (still open): bnc -O2 has missing-symbol
  link errors. Worth investigating separately if/when we want
  optimization enabled by default.

### ~~conformance/283_float_untyped: VM float32 storage~~ — FIXED (`882893c`)
- VM registers carry IEEE bits in their declared width — float64 in
  8 bytes, float32 in low 4 bytes (zero-extended). float64 → float32
  needs a real IEEE conversion (the exponent biases differ); the
  prior lowering emitted BC_MOV, which left float32 storage
  containing the low half of a float64 bit pattern (garbage).
- Fix added BC_F64_TO_F32, BC_F32_TO_F64, and BC_F32TOSI; lowerCast
  now picks the right one for f64↔f32 width changes and f32→int.
  lowerLoad/lowerStore for float32 stay as 4-byte sub-word ops; the
  cast does the conversion.
- 283 now passes boot-comp-int and boot-comp-comp-int (both in
  `all`); xfail markers removed. The boot-comp-int-int xfail was
  also dropped — the test still fails there but only because the
  mode itself is broken (see entry above).

### ~~Native AArch64 backend — float args via D-registers (`287_float_println`)~~ — DONE (`8cd555e`)
- Two-part fix:
  - `common.IsFloatScalarTyp` and `CallArgRegStart` / `CallArgStackOff`
    / `CallStackBytes` skip floats from the GP NGRN budget. Mixed
    `(int, float, *[]u8)` signatures now place the slice at X1..X2
    instead of X2..X3 (`bootstrap.formatFloat(v float64, buf *[]uint8)`
    is the canonical case).
  - `emitFunc` prologue tracks NSRN separately and reads each float
    param from D(NSRN) via FMOV → scratch GP → spill slot, mirroring
    `emitCall`'s already-present caller-side NSRN handling.
- Tests: `pkg/native/common.TestIsFloatScalarTyp` and
  `TestCallArgRegStartSkipsFloats` lock in the dispatch behavior.
  Conformance 287_float_println passes on `boot-comp_native_aa64`;
  full native conformance 278/278.

### ~~Native AArch64 backend — unit-test packages failing under `boot-comp_native_aa64`~~ — DONE (`1612221`)
- Conformance suite passes end-to-end under `boot-comp_native_aa64`,
  but a unit-test sweep on 2026-04-27 failed 10 of 29 packages. Three
  clusters: (C) a Mach-O reloc emission bug (pkg/ir), (A) seven
  test-binary crashes/runtime errors, (B) two packages with
  assembler-encoding assertion failures.
- **Cluster C — DONE** (`8bc6196` + `f18ff2c` + `e4c9edd` + `491ac60`):
  Mach-O r_extern always 1; `cmd/bnc --keep-objs`; cross-section string
  refs use ADRP+ADD instead of ADR (±1MB → ±4GB); ResolveFixups errors
  on out-of-range PC-rel fixups; macho writer rejects unsupported
  fixup-kind→reloc mappings; new tests in `pkg/asm/aarch64` and
  `pkg/asm/macho`.
- **Cluster A — partial** (`ca9f287` + `ac7be3f`): a tight conformance
  reduction (`332_struct_arg_forward_inserts`) caught the
  pkg/asm/macho TestLoopSum crash. Root cause: `regPool(i)` returns
  X15 for any index >= 6, so `getOperand` (for the source pointer)
  and `scratchReg` (for the load temp) both hand out X15 once
  m.Next exceeds the pool. The collision turns the per-word ldr/str
  into `ldr x15, [x15, #N]` chasing through loaded values — eventually
  faults on the first NULL it traces. Fixed in emitCall's stack-arg
  branch by hardcoding X16 (AAPCS intra-call scratch) for the load
  temp; safe across ldr/str (no `bl` between).
  - **pkg/asm/macho** unblocked. Other cluster A packages (pkg/types,
    pkg/asm/parse, pkg/asm/aarch64, pkg/native/arm64, pkg/codegen,
    pkg/vm, pkg/ir) need verification via a clean re-sweep — they
    may be the same bug or other distinct crashes.
  - pkg/types specifically had a different shape pre-fix: crash inside
    RefInc writing to a read-only memory region (`r--`), suggesting a
    bad managed pointer — possibly unrelated to the X16 collision.
  - Larger root cause: regPool's saturation at X15 is unsafe in
    general. A real fix spills when the pool is exhausted (or grows
    the pool); the X16 patch only covers this one call site. Worth
    a follow-up.
- **Cluster B — DONE** (`43ab7a3`): one root cause for all 22 failures
  — native ARM64 mishandled multi-return tuples with sub-word fields.
  The caller-side spill walked by 8-byte word, losing the second
  X-register for `(uint32, uint32)`; emitExtract used 64-bit LDR for
  sub-word fields. Fixed by walking by FIELD (with sized stores) and
  size-dispatching through emitScalarLoad. pkg/asm/elf 22/22; the
  19 dpEnc-family tests in pkg/asm/arm32 all pass.
- **Cluster A residual — DONE** (`1612221`): all 8 remaining failing
  packages collapsed to a single root cause — `aarch64.Str/Ldr/Strb/
  Strh/Ldrb/Ldrh` silently masked the imm12 offset to 12 bits when it
  didn't fit. Frames > 32KB (or for sub-word ops, > 4KB) caused
  STR/STRB to write at a truncated address, corrupting unrelated data
  in the same frame. The auto-generated test runner has a frame
  proportional to the test count, so packages with many tests
  (pkg/types, pkg/codegen, pkg/native/arm64, pkg/ir, etc.) all hit
  this. Fix: `emitLdrStr` and `ldrStrSubWordEmit` materialize
  base+off into X17 when the offset doesn't fit
  (`LdrStrImmFitsUnsigned`). Clean sweep: 29/29 unit-test packages,
  285/285 conformance.
  - This sweep covered only the six encoders in `aarch64_arith.bn`
    (`Str`/`Ldr`/`Strb`/`Strh`/`Ldrb`/`Ldrh`); the three sign-extending
    loads `Ldrsb`/`Ldrsh`/`Ldrsw` in `aarch64_branch.bn` had the
    identical imm12-wrap bug and were MISSED here, surfacing later as the
    native-aa64 signed-sub-word miscompile (fixed binate `4dc78d2e`,
    2026-06-11). A repo-wide grep for the `& 0xfff` offset mask, rather
    than a per-file pass, would have caught all nine at once.
- Full inventory + plan of action in `explorations/native-aa64-bugs.md`.
- CI hookup for `boot-comp_native_aa64`: DONE — added to the `all`
  modeset and the unit/conformance/perf workflows now split the
  matrix so native_aa64 runs on `macos-latest` (Apple Silicon) while
  the LLVM-chain modes stay on `ubuntu-latest`.

### ~~Native AArch64 backend — cross-package by-value struct ABI mismatch (`337_cross_pkg_struct_arg`)~~ — FIXED (`0e3f357`)
- Surfaced while reducing the original cluster A pkg/asm/arm32 LDRSH
  unit-test crash. Not the cause of that crash — unit tests build all
  packages with native, so caller and callee agree. But it was a real
  native-backend bug exposed by the conformance runner, which builds
  main with -backend native and dependencies via LLVM.
- Repro: 56-byte struct (3 ints + @[]char), passed by value to a
  function in another package after 2 leading int args. LLVM's callee
  prologue does a split fill (X2..X7 + 1 stack arg). Native main's
  emitCall used to put the whole 7-word struct on stack[0..48] — when
  `ngrn + w > 8`, `CallArgRegStart` returned -1 and emitCall took
  the all-stack branch.
- Fix in `0e3f357`: support split passing in three call sites:
  1. `pkg/native/common/common.bn` `CallArgRegStart` /
     `CallArgStackOff` / `CallStackBytes` — when an aggregate
     straddles, regStart returns the first reg AND stackOff returns
     the overflow start; both can be ≥ 0 simultaneously.
     CallStackBytes only counts post-X7 words.
  2. `pkg/native/arm64/arm64_ops.bn` emitCall aggregate branch — fill
     `8 - regStart` regs first, then write overflow to stack via X16.
  3. `pkg/native/arm64/arm64.bn` prologue aggregate branch — store
     reg portion to data slot, copy overflow words from caller's
     stack-args area.
- Bug required the @[]char (managed-slice) field to repro — pure-int
  structs of the same total size pass. LLVM's struct ABI for managed
  types differs from int-only structs, so the disagreement only
  triggered on managed-aware structs.
- Conformance test `337_cross_pkg_struct_arg` (multi-package). Now
  passes under `boot-comp_native_aa64`. Verified no regressions:
  pre-fix and post-fix unit-test sweeps both 18 passed, 11 failed,
  same 11 packages.

### ~~Remove OP_CALL_BUILTIN and the empty C-runtime manifest~~ — DONE (`0b7dd90`)
- After Step 2b (print rewired to `bootstrap.formatX` + `bootstrap.Write`)
  and Step 3.2 (`bn_exit` migrated to `rt.Exit`, runtime manifest
  emptied), no IR-gen path emitted `OP_CALL_BUILTIN`. Plumbing was
  dormant; this commit removed it (20 files, −332 lines net).
- Removed: `pkg/ir/runtime.bn` + `runtime_test.bn` (entire files);
  `OP_CALL_BUILTIN`, `EmitCallBuiltin`, op-name dispatch arm, and the
  `RuntimeFunc`/`RuntimeFuncs`/`RT_*` block from `pkg/ir.bni` +
  `pkg/ir/ir_ops.bn`; `RuntimeFuncs()` declare-emission loop +
  `emitRuntimeDecl` + `rtKindToLLVM` from `pkg/codegen/emit.bn`;
  `OP_CALL_BUILTIN` arms from `emit_util.bn` / `emit_ops.bn` /
  `emit_instr.bn`; `OP_CALL_BUILTIN` arms (~6 sites) from
  `pkg/native/common/common.bn`; arm from `pkg/native/arm64/arm64.bn`;
  `isBuiltin` parameter from `pkg/native/arm64/arm64_ops.bn:emitCall`
  (collapses `_underscorePrefix` vs `symFor` to `symFor` only);
  `BC_CALL_BUILTIN` from `pkg/vm.bni` + `pkg/vm/vm_exec.bn` +
  `pkg/vm/lower_instr.bn` + `pkg/vm/lower.bn`; `execBuiltin` from
  `pkg/vm/vm_extern.bn`; `TestEmitCallBuiltin` from
  `pkg/ir/ir_ops_test.bn`.
- Verified: boot 202/202, boot-comp 278/278, boot-comp-int 271/271,
  boot-comp-comp 278/278, boot-comp-comp-int 277/277. Hygiene 9/9.
- Cherry-pick onto main (post-merge with `pkg/buf` Stage-9 migrations)
  required one-file conflict resolution in `pkg/codegen/emit_ops.bn`:
  combined the OP_CALL_BUILTIN-arm collapse with main's `.Bytes()`
  method-syntax migration. boot-comp 278/278 post-merge confirms.

### ~~Un-export `rt.c_*`~~ — DONE (via pkg/libc, `43179b7` / `eae28a1` / `d3e2081`)
- `pkg/rt.bni` no longer exports any `c_*` bridges. The libc dependency surface (Malloc / Calloc / Free / Memset / Memcpy / Exit) lives in a new package `pkg/libc` (.bni-only; implementations in `runtime/libc_stubs.c`). pkg/rt imports pkg/libc and forwards its raw-memory wrappers (RawAlloc / RawAllocZero / RawFree / MemCopy / MemZero) through it.
- pkg/libc is the **only** "magic" package: it is always libc, and on a libc-free target (ARM32 bare-metal etc.) code does NOT substitute a different pkg/libc — instead, that target ships an entirely different pkg/rt that doesn't import pkg/libc and implements the runtime directly.
- Naming whitelist: the eight `pkg/rt.bni:c_*` exemptions were dropped (no longer needed since `c_*` is gone).
- One residual non-libc C extern remains: `rt.CallDtor` (function-pointer dispatch helper in `runtime/rt_stubs.c`). Tracked separately under "Retire `rt.CallDtor`" below.
- The cmd/bnc + cmd/bni IR-gen drivers auto-import pkg/libc into every package's IR module (mirroring the existing pkg/rt and pkg/bootstrap auto-imports), so `bn_libc__Memcpy` calls emitted by the backends always have a matching `declare` line. Regression tests in `cmd/bnc/compile_test.bn`.
- Discovery sequence: rename the wrappers to RawAlloc/RawAllocZero/RawFree/MemCopy/MemZero with proper preconditions (`fde6760`); introduce pkg/libc + migrate pkg/rt (`43179b7`); switch backend memcpy emission to `bn_libc__Memcpy` (`eae28a1`); auto-import pkg/libc (`d3e2081`).

### ~~Retire `rt.CallDtor` via `OP_CALL_INDIRECT`~~ — DONE
- **Plan doc**: `explorations/plan-call-indirect.md`.
- `rt.CallDtor` is gone. RefDec now calls a compiler-internal helper `_call_dtor` (declared in `pkg/rt.bni` as a type-checking shape only — no real symbol). IR-gen recognizes the `_call_dtor` / `rt._call_dtor` symbol and emits `OP_CALL_INDIRECT` in place of `OP_CALL`. `runtime/rt_stubs.c` deleted; `vm_extern.bn`'s two `rt.CallDtor` arms removed; the C trampoline retires.
- **Path taken (option C from the plan)**: compiler-internal-only — no new builtin or keyword. The `.bni` decl gives the type-checker the right signature to validate RefDec's call against; IR-gen swaps in `OP_CALL_INDIRECT` for that one magic name. Lighter weight than designing a `call_indirect` user-facing builtin; generalizes naturally when function values land (which will need their own spelling).
- **Hygiene**: `scripts/hygiene/naming.sh` was tightened to also flag `_`-prefix exports (previously the `[a-z]` regex let them slip through). `_call_dtor` is whitelisted.
- **Commits**: `ee93644` (PR 1: IR op + LLVM), `6f064a5` (PR 2 part 1: VM lowering), `4e20ffb` (PR 2 part 2: native arm64), `f08ddcb` (PR 2 part 3: RefDec migration + retire C trampoline).
- **Paired with**: "Free-function pointer in managed-allocation header — bug" (also DONE) — `Free` reads `header[1]` and dispatches indirect through it via the parallel `_call_free_fn` magic helper, sharing the same OP_CALL_INDIRECT lowering as `_call_dtor`.

### ~~Compiler bug: `bnc -g` emits invalid LLVM IR after OP_REFDEC inline lowering~~ — FIXED
- **Repro** (2026-05-01): any source exercising `OP_REFINC` or
  `OP_REFDEC`, built with `bnc -g ...`, failed clang at compile time:
  ```
  error: expected instruction opcode
   ri.0.skip:, !dbg !DILocation(line: 179, scope: !12)
             ^
  ```
  Affected both inline RefInc and RefDec sites; in practice surfaced
  via OP_REFDEC since most -g use hits a managed-pointer destructor.
- **Root cause**: the inline lowerings (`emitRefIncInline` /
  `emitRefDecInline`) emit a multi-line sequence ending with a
  basic-block label (`ri.<seq>.skip:` / `rd.<seq>.skip:`).
  `addDbgToLastLine` in `pkg/codegen/emit_debug.bn` then appended
  `, !dbg !DILocation(...)` to the trailing line — including label
  lines, which is invalid LLVM IR.
- **Fix**: `addDbgToLastLine` now detects label declarations (last
  non-newline char is `:`) and skips the annotation. The label and
  any intermediate instructions in the multi-line emission stay un-
  annotated, but LLVM tolerates that — the surrounding `DISubprogram`
  is enough metadata for IR validity; only source-line attribution
  within those few lines is lost. Same convention as other multi-
  line emitters (e.g., `emitBoxInstr`).
- **Test**: `pkg/codegen/emit_debug_test.bn::TestEmitDebugDoesNotAnnotateLabels`
  compiles a managed-ptr copy under `SetDebugInfo(true)` and asserts
  no `<label>:, !dbg` substring appears in the output.
- **Verification**: full conformance under `BINATE_FLAGS="-g"` is
  green (boot-comp 287/287).

### ~~Lift function-name qualification into IR (shared across backends)~~ — DONE
- IR is now the single source of truth for canonical fully-qualified
  function names. `ir.Func.Name` (formerly `QualifiedName`, with the
  bare-name field retired) holds dot-qualified names everywhere
  ("asm.New", "main.main", "geom.Point.M"). All backends — LLVM
  codegen, VM, native AArch64 — read from `f.Name` directly; their
  prior `modulePkgName + bare-name` qualification dance is gone.
  `EmitCall` / `EmitFuncAddr` / `EmitFuncValue` / `OP_FUNC_VALUE`
  all carry already-qualified `instr.StrVal` strings.
- Migration was incremental (Steps 1–5b across `c1d4074` and
  surrounding commits): introduce `QualifiedName` field, populate it
  in `NewFunc` / `NewExternFunc`, flip writers, flip readers, then
  rename to `Name`. `mangle.QualifyName` / `mangle.FuncName` are
  unchanged — they already accepted pre-qualified dotted names.
- Regression guard: `TestGeneratePackageQualifiesByModuleName` in
  `pkg/ir/gen_module_test.bn` pins down the cmd/* divergence
  (`file.PkgName="main"` vs `m.Name="cmd/foo"`) where IR-gen had
  previously qualified by `file.PkgName` and broken every cmd/*
  binary's auto-helper symbols (`__copy_X`, `__dtor_X`).

### ~~boot-comp-int: all unit-test packages pass~~ — DONE
- All 27 unit-test packages pass under boot-comp-int (cmd/bni bytecode VM); zero xfails. Down from 17 failing at start of work.
- **Fixes**:
  - pkg-asm and cmd-bnc unblocked by VM function-name qualification fix (`32eb2f6` / `76294d8`).
  - pkg-asm-macho's `bootstrap.Exec` extern stub fixed (`e6b0d00`); pkg-asm-elf/macho unblocked via `bootstrap.Stat` extern stub fix (`4b70a9b`). Conformance tests 273 / 277.
  - Cross-package struct field resolution fix (`2be80b9`); conformance 270.
  - **pkg-ir, pkg-codegen, pkg-vm unblocked** by zero-init fix (`0933158`). Root cause: `var x T` (no initializer) for struct/array types allocated uninitialized memory; subsequent `x.field = ...` did "axiom 5 copy-then-destroy" — load old + RefDec — on garbage bytes that occasionally looked like a valid managed pointer, freeing a stranger's allocation. LLVM hides this via dead-load elimination on uninitialized allocas; the bytecode VM doesn't. Fix: IR now emits `OP_CONST_NIL + OP_STORE` after `OP_ALLOC` for struct/array types that contain managed fields. Both backends consume the same IR — refcount semantics are now IR-driven. Also extended pkg/codegen's `emitConstNil` to handle struct/array/named types.
  - **cmd-bnlint unblocked** by VM `bootstrap.Args` extern fix (`503a79b`). Stub was returning 0; cmd/bnlint's findRoot called bootstrap.Args() and crashed on null managed-slice. Fix: call host bootstrap.Args(), push the @[]@[]char header, and pre-RefInc both the outer and each inner @[]char's backing so the result's scope-cleanup dtor leaves all allocations alive for the VM caller.
- (Note: the prior `boot-comp-int2` mode was renamed to `boot-comp-int` in `b1e4f98` after `pkg/interp` and `cmd/bni` were retired; only one interpreter mode remains.)

### ~~Compiler bug: missing RefInc on struct copies with managed fields~~ — FIXED
- **Root cause**: two related issues:
  1. When a struct containing `@[]T` or `@T` fields is copied by value, the compiler did not RefInc the managed fields in the copy.
  2. Stack-allocated struct locals with managed fields were not cleaned up at scope exit (no dtor call).
- **Compiler fix**: Generate `__copy_X` functions (symmetric to `__dtor_X`) for structs and `[N]T` arrays. Call copy at struct copy sites (var decl, var assign, field assign, deref assign, function args, function return). Call dtor at scope exit for struct locals.
- **Interpreter fix**: `structRefInc`/`structRefDec` helpers walk struct fields recursively. Called from `cleanupEnvExcept` (scope exit), `envDefine` (var decl), `envSet` (var assign). Also fixed: `cleanupEnvExcept` false `isRet` match for `@T` (offset-0 field address collision); `IsFresh` leak on fresh `@T` function args.
- **`VAL_MANAGED_SLICE`**: added to distinguish `@[]T` from `*[]T` at Value.Kind level (was both `VAL_SLICE`), matching `VAL_MANAGED_PTR` vs `VAL_POINTER`.
- **Conformance tests**: 222 (struct copy managed), 223 (nested struct copy), 224 (struct field assign), 225 (managed ptr scope cleanup).
- **Detailed writeup**: `explorations/bug-struct-copy-refcount.md`
- **Plans**: `explorations/plan-copy-constructors.md`, `explorations/plan-interp-struct-copy-refcount.md`
- **Principled slow path** (2026-04-11): always copy on return, always dtor at scope exit, register struct call results as temps. Tests 226 and 227 now pass on compiled modes. See `design-refcount-axioms.md`.
- **[]char UAF migration** (2026-04-12): the slow path exposes latent UAFs where `*[]char` (or `*[]T`) borrows from `@[]char` (or `@[]T`) that gets freed by struct dtors. Systematic migration of function return types and callers. Key fixes: `EmitModule`, `llvmType`, `pathJoin`, `FuncRetType` fields, `parser.Errors`/`CheckerErrors` callers, `sliceToChars`/`StrOf` callers, `concatChars`, `quotePath`, test helpers. Also fixed: slice element assignment for nested struct fields (was only handling top-level `@T`/`@[]T`), multi-return assignment for struct variables (missing save-copy-destroy).
- **Status**: 187/187 conformance on boot-comp, boot-comp-comp, boot-comp-comp-comp. **26/26 boot-comp unit tests pass.** Zero failures.
- **`--cflag` option** added to bnc for passing flags to clang (e.g., `--cflag -fsanitize=address`). Used with libgmalloc to debug UAFs.

### ~~Linux/x86-64: boot-comp-comp string corruption~~ — FIXED
- **Root cause**: use-after-free in `cmd/bnc/test.bn`. `runtimePath` was declared as `*[]char` (raw slice) instead of `@[]char` (managed). When the `candidate @[]char` from `bootstrap.Concat(root, "/runtime/binate_runtime.c")` went out of scope, it was RefDec'd and freed — but `runtimePath` still borrowed its data, creating a dangling pointer. The garbage filenames were freed memory being read as strings.
- **Fix**: changed `var runtimePath *[]char` to `var runtimePath @[]char = buf.CopyStr(cli.RuntimePath)` in test.bn, matching the pattern already used in main.bn.
- **CI now runs all modes** including boot-comp-comp and boot-comp-comp-comp.

### ~~Compiler bug: `-O2` / `-Og` build fails to link (undefined dtor symbol)~~ — FIXED (`65cb258`)
- Linkage was `linkonce_odr`, which lets the LLVM optimizer's
  GlobalDCE pass drop a dtor as internally-unused even though it's
  referenced from another compilation unit. Switched dtors and
  copies to `weak_odr`, which keeps the symbol live across object
  boundaries while still allowing the linker to dedupe.
- Verified `-O0` / `-O2` / `-Og` all link and self-compile cmd/bnc;
  boot-comp-comp green (282/282).

### ~~Free-function pointer in managed-allocation header — bug~~ — DONE
- `pkg/rt/rt.bn` defines a 2-word managed-allocation header
  `{refcount, free_fn}`. The free_fn slot is now populated by
  `Alloc` (with `&rt.RawFree`) and read by `Free`, which dispatches
  indirect through it via the new `_call_free_fn` magic helper
  (parallel to `_call_dtor`, same OP_CALL_INDIRECT lowering). Each
  rt impl plugs in *its own* RawFree without Free needing to know.
- The runtime's C-side `managed_alloc` helper (used by
  `cstr_to_managed_slice` etc.) was updated to set
  `header[1] = &bn_rt__RawFree`, keeping C-created managed
  allocations consistent with rt.Alloc-created ones.
- **Cross-mode caveat (unchanged from prior state)**: works within
  a single mode (compiled-side allocation freed compiled-side; VM-
  side allocated freed VM-side). Cross-mode allocation+free still
  requires per-signature trampolines (function values Phase 3) to
  translate header[1] between the C-pointer and VM-function-index
  conventions. No regression vs. before — pre-fix Free silently
  used libc.Free regardless of origin.
- **Sub-task that landed alongside**: a new compiler-internal
  builtin `_raw_func_addr(funcRef)` returning the raw function
  address as `*uint8`. Underscore-prefixed because it isn't a
  permanent language feature — when function values land, the
  canonical spelling will accept a function value and extract the
  underlying call slot. Used by Alloc to populate header[1].
- **Prelim layering fix**: Alloc now routes through RawAlloc and
  MemZero rather than calling libc.Malloc / libc.Memset directly,
  so a non-libc pkg/rt impl can plug in its own raw-memory layer.
- **Commits**: `eda5941` (Alloc → RawAlloc+MemZero), `217f8bb`
  (`_raw_func_addr` builtin), `7b325eb` (header[1] populate+use).

### ~~Verify .bni vs .bn visibility semantics~~ — VERIFIED
- Private functions (235) and types (236) in `.bn` but not `.bni` are correctly rejected by both type checkers.
- Public declarations work across packages (237). `.bni` and `.bn` definitions coexist without duplicate errors.
- Forward struct declarations in `.bni` (declare name only, define in `.bn`) — future feature.

### ~~Raw slice subslice expression copies data (bug)~~ — FIXED
- Fixed by lowering `OP_SLICE_EXPR` to primitive IR ops (step 3.1). Raw slice `s[lo:hi]` now produces a zero-copy view `{data + lo * elemSize, hi - lo}` via GEP. The C runtime `bn_slice_expr_*` functions (which incorrectly copied) have been removed.

### ~~Bounds checks on `s[i]` / `s[lo:hi]` are not wired up~~ — DONE
- `emitIndexBoundsCheck` helper added in `pkg/ir/gen_access.bn`; called from `genIndex`, from the multi-return / EXPR_INDEX assign paths in `gen_control.bn`, and from `genSliceExpr` (two checks: hi against len+1, lo against hi+1). `unsafe_index` stays check-free — `genIndex` takes a `checked bool` param and `EXPR_INDEX` passes true while `unsafe_index` passes false.
- Conformance tests 309–314 cover index OOB on slice/array, index-assign OOB, slice-hi OOB, slice lo>hi, and negative slice lo. Tests 312/313/314 xfailed on boot only because Go's bootstrap interpreter formats the trap message differently. (Original numbers 298–303; renumbered when conformance suite duplicates were resolved.)

### ~~Phase 3: unify strings as composite-literal sugar~~ — DONE
- Plan: `plan-composite-literal-generalization.md` § Phase 3 +
  `plan-phase3-string-unification.md` (sub-plan).
- End state: no string-specific IR ops, no `TYP_STRING` kind. String
  literals flow through the same `OP_RODATA_*` ops as user-written
  const-byte composite literals. Backend lowerings are uniform.
- Stages and commits:
  - **3.1** (`c164807`) — added `OP_RODATA_MSLICE` / `OP_RODATA_SLICE`;
    `genManagedSliceLit` / `genRawSliceLit` detect all-const-byte
    composites at IR-gen time and emit the new ops directly. Conformance
    test 320 covers `@[]const char{'a','b','c'}` etc.
  - **3.2** (`1264902`) — `EmitStringToChars` redirects read-only
    string→slice through the new ops.
  - **3.2b** (`29c4aaf`) — added `OP_RODATA_ARRAY`; redirected
    string→array through it.
  - **Stage 2b copy** (`d043acf`) — added `OP_RODATA_MSLICE_COPY` for
    `@[]char = "..."` (mutable) — alloc + memcpy from rodata.
  - **3.3** (`a868b4c`) — deleted `OP_STRING_TO_CHARS`,
    `OP_STRING_TO_ARRAY`, `EmitStringToArray`, all backend lowerings.
  - **3.4** (`b7243e7`) — eliminated `TYP_STRING`; IR-gen dispatch
    keys on `val.Op == OP_CONST_STRING` instead of the type-marker.
  - **Test backfill** (`4a2eb28`) — 7 IR-gen unit tests for the
    dispatch + fast-path detection.
- `EmitStringToChars` survives as the multi-way dispatch helper that
  picks the right rodata op based on target type. `OP_CONST_STRING`
  also survives — it's the IR's "raw bytes pointer" op (lowers to
  LLVM `getelementptr`), now typed as `*const uint8` instead of
  `TYP_STRING`. Both are non-string-specific in shape.

### ~~Enforce parse-level rejection of function-local `type` declarations~~ — DONE
- Both parsers (`pkg/parser/parse_stmt.bn` and
  `bootstrap/parser/parser.go`) now emit
  `"type declarations must be at package level, not inside a function
  body"` when they encounter `TYPE` at statement position. Recovery
  is "parse the type-decl anyway and discard," so downstream parsing
  isn't derailed.
- Conformance test 319 (`319_err_function_local_type`) covers the
  rejection across all three basic modes.

### ~~.bni/.bn return type mismatch should be a compile error~~ — FIXED
- The type checker now verifies that `.bn` function definitions match their `.bni` declarations (parameter count/types, return count/types). Mismatches are reported as compile errors.
- Immediately caught two real bugs: `MakeStringVal` and `AddBlock` had `@[]char` in `.bni` but `*[]char` in `.bn`. Both `.bni` files fixed.
- Conformance test 221 now passes on all compiled modes.

### ~~Compiler bug: cast to sub-word pointer type emits invalid LLVM IR~~ — FIXED
- Cast codegen now uses `bitcast` (ptr→ptr), `ptrtoint` (ptr→int), `inttoptr` (int→ptr) instead of `add` for pointer types.
- Conformance test 161 passes on all compiled modes.

### ~~Compiler bug: multi-return with struct containing managed fields~~ — FIXED
- Bug was already fixed by earlier refcounting changes. Workaround reverted. Test 141 passes.

### ~~Multi-return as anonymous struct~~ — DONE
- Multi-return is an ABI contract: `func f() (T1, T2)` returns `struct { _0 T1; _1 T2 }`.
- Compiler side done long ago: `Func.MultiReturnType` propagated through FuncSig/call sites/return instructions; LLVM emission uses `llvmType(MultiReturnType)`.
- Interpreter side moot: the original tree-walker `pkg/interp` was retired in 2026-04-17. The bytecode VM (`pkg/vm`) consumes the compiler's IR directly, so it inherits the anonymous-struct layout — no separate work. Verified 2026-04-26: zero references to `VAL_MULTI`, `Value.Elems`, or `HeapObj` remain in pkg/ or cmd/.
- Plan file `plan-multi-return-struct.md` deleted (was MOOT).

### ~~Package path strategy (Phase 1)~~ — DONE (2026-04-28)
- Two-path resolution shipped: `BniPath` (`.bni` interfaces) and
  `ImplPath` (impl directories) are independently-searched, ordered
  lists. CLI surface: `-I` / `--interface-path` and `-L` / `--impl-path`
  on bnc, bni, bnlint, and the Go bootstrap. `--root <dir>` stays as
  sugar for "add to both paths."
- Stages 1–6 (loader split → per-tool CLI → drop deprecated `Roots`
  field) all landed across the binate + bootstrap repos. See
  [`plan-package-search-paths.md`](plan-package-search-paths.md) for
  the design and the per-stage commit table.

### ~~CLI flag coherence~~ — DONE (2026-04-28, alongside Stage 1–6)
- Standardized on `--word` for long flags across bnc, bni, bnlint,
  bootstrap. Existing single-dash long flags (`-root`, `-add-root`,
  `-verbose`, `-test`, `-cpuprofile`) stay accepted as back-compat
  aliases. Single `-` is reserved for short flags (`-v`, `-I`, `-L`),
  including future combinable `-abc`-style.

### ~~Simplify bootstrap.Read/Write signatures~~ — DONE
- `Read(fd int, buf *[]uint8) int` and `Write(fd int, buf *[]uint8) int` — redundant `len` parameter removed. Callers subslice if they want a smaller length.

### ~~Raw slice syntax migration: `[]T` → `*[]T`~~ — DONE (2026-04-17)
- Raw slices now spelled `*[]T` (the `*`/`@` prefix consistently means raw/managed for both pointers and slices). Disambiguation rule: `*[` and `@[` before `]` are always slice sugar; pointer-to-array and pointer-to-slice require parens.
- Stages landed in order: Stage 0 (reclaim `*[`), Stage 1 (accept `*[]T` alongside `[]T`), Stage 2 (migrate all code + docs), Stage 3 (remove `[]T` entirely — `bare "[" "]"` is now a parse error in both the Go bootstrap and `pkg/parser`). Covered by conformance test 276.

---

## Done (session 2026-04-08/09)

### ~~NeedsDestruction TYP_NAMED resolution~~ — FIXED
- Fixed: `NeedsDestruction` resolves `TYP_NAMED`. Conformance test 140 passes.

### ~~Managed-slice dtor: iterate from backing start, not data ptr~~ — FIXED

### Phase 3.1: Lower slice ops to primitive IR ops — DONE
- All slice ops (`OP_SLICE_GET/SET/LEN/EXPR/ELEM_PTR`) lowered to primitives (`OP_EXTRACT`, `OP_GET_ELEM_PTR`, `OP_LOAD/STORE`) in the IR gen layer. Deprecated opcodes removed from `ir.bni`.
- 13 C runtime functions removed (22→9 in manifest). `emit_slice.bn` deleted.
- Raw slice subslice copy bug fixed: `s[lo:hi]` now zero-copy (was incorrectly copying in C runtime).
- **EmitSliceSet element type bug**: was using `val.Typ` (int/64-bit) instead of slice element type, causing wrong GEP stride for `*[]uint8`. Test 141 added.
- **EmitSliceExpr GEP type mismatch**: codegen's internal bitcast produced typed pointer but slice field 0 expects `i8*`. Fixed with byte-level GEP.
- **readFile UAF** (6 call sites in cmd/bnc, cmd/bni, pkg/loader): `var src *[]uint8 = readFile(...)` dropped backing reference immediately. Changed to `@[]uint8`. Previously masked by copying slice_expr. Tests 142 added.

### ~~Remove dead bn_append_* functions~~ — DONE

### ~~ModuleConst.Name UAF~~ — FIXED
- Fixed: `ModuleConst.Name` changed from `*[]char` to `@[]char`.

### 161/161 — ZERO XFAILS IN ALL MODES
- **boot-comp: 161/161. boot-comp-int: 161/161. boot-comp-comp: 161/161.**
- Was 158/158 before Phase 3 work. New tests: 140 (named struct slice elem rc), 141 (slice param mutation + multi-return managed field), 142 (read slice mutation).

### [N]@T field-write-through-index — FIXED (test 139)
- `genSelectorPtr` for `arr[i].Field` only handled struct elements. For `[N]@Node`, element type is `@Node` (TYP_MANAGED_PTR). Added: load managed-ptr from array element, then GEP for field.

### Duplicate function detection — FIXED (test 206)
- Added `checkDuplicateDecls`: O(n²) scan of declaration list for duplicate names. Reports "redeclared in this block". Skips .bni→.bn matches (only checks within same file).
- Added `LookupLocal` to Scope (current scope only, not parents).

### Compiler refcount fixes
- **Managed-slice return leak** (test 131): skip RefInc for returned managed-slice locals via `lookupLocalVar`.
- **Managed-ptr return leak** (test 132): same pattern. Key bug: `lookupVar()` fell back to globals — returning a singleton freed it. Fixed with `lookupLocalVar()`.
- **Element-copy refcounting** (tests 133-135): RefInc/RefDec for managed-ptr, managed-slice, and struct elements during slice/array assignment.
- **RefInc-before-RefDec ordering** (test 138): cascade-safe assignment (e.g., popScope).
- **Parser raw-slice borrow** (test 136): `parseImportDecl` `*[]@ast.ImportSpec` → `@[]@ast.ImportSpec`.
- **Debugging**: sentinel-based RefDec (rc=-999) and ASan with instrumented .ll files.

### Interpreter flat migration — COMPLETE
- ALL data types use flat storage: int, bool, *[]T, @[]T, @T, *T, [N]T, struct, string, named types. Only function values remain Cell-based (pending interop design).
- readFlatValue no longer materializes Elems — O(1) variable read.
- evalMakeSlice, evalArrayLit, evalStructLit, ZeroValue, stringToCharSlice all produce flat Values directly.
- Legacy code removed: MakeSliceVal, MakeArrayVal, MakeManagedSliceVal, writeFlatValue Elems paths, HeapObj deref fallbacks, legacy index/subslice/for-in/struct-field paths. Elems: 53→3. HeapObj: 30→3.

### Interpreter refcount fixes
- **Return leak**: IsFresh flag on Value. make/make_slice/box set IsFresh (rc starts at 1, skip envDefine RefInc). execReturn sets IsFresh for local-ident returns via envGetLocalAddr (not parents/globals). envDefine/envSet skip RefInc when IsFresh.
- **Element-copy**: RefInc/RefDec for managed-ptr, managed-slice, and struct elements in both flat slice and flat array assignment paths.
- **Struct field assignment**: RefInc/RefDec for managed-ptr and managed-slice fields in both auto-deref and value-struct paths.
- **Managed-slice element cleanup**: only iterates elements when backing refcount==1 (last reference). Handles managed-ptr, managed-slice, and struct elements.
- **Assignment cascade**: RefInc new before RefDec old for managed-ptrs (cascade-safe).
- **Pointer deref write**: RefInc/RefDec for managed types in `*p = val`.

### Managed-slice flat storage in interpreter
- boot-comp-int: 148/156 (was 142 before).
- `TYP_MANAGED_SLICE` in `useFlatType`, flat subslicing, `@[]T→*[]T` coercion, element refcounting, backing refcounting.

### 4-word managed-slice migration — finalized
- Conformance test 129 (subslice preserving backing_len), bootstrap interpreter confirmed no changes needed.

### x86-64 assembler backend — IMPLEMENTED
- **pkg/asm/x64**: full x86-64 instruction encoding with REX prefix, ModR/M, SIB byte. MOV, PUSH/POP, LEA, ADD/SUB/AND/OR/XOR/CMP/TEST, INC/DEC/NEG/NOT, SHL/SHR/SAR, IMUL (2 and 3 operand)/IDIV/DIV, CQO/CDQ, JMP/Jcc/CALL/RET, NOP/SYSCALL/INT. 40 unit tests.
- **x86-64 text parser**: register parsing (4 sizes × 16 regs), memory operands with `[base + index*scale + disp]`, size prefixes, Jcc mnemonic parsing. Full parity with encoding backend. 28 parser tests.
- **ELF relocation mapping**: FIX_REL32 → R_X86_64_PC32, FIX_ABS64 → R_X86_64_64.
- **x86-64 native end-to-end tests on Linux** (`29f4230`): assemble x86-64 → ELF64 → link with cc → run via host SYSCALL. Three tests in `pkg/asm/elf/elf_test.bn`: `TestX86_64ElfExit` (exit via SYSCALL), `TestX86_64ElfLoop` (sum 1..9 = 45), `TestX86_64ElfCall` (function call with PUSH/POP). `canLinkX86_64Elf()` probe makes them skip cleanly off Linux/x86-64. Verified passing on CI.
- 295 tests total across all assembler packages.

### AArch64 parser: MVN added, full parity
- Added MVN (bitwise NOT) to encoding backend and parser. MVN Rd, Op2 = ORN Rd, XZR, Op2. AArch64 parser now has full parity with encoding backend. 3 encoding tests + 1 parser test.

### ARM32 semihosting end-to-end tests — IMPLEMENTED
- 3 tests: exit code, loop (sum 1..9=45), function call (PUSH/POP with BL)
- Uses `qemu-system-arm -semihosting` with SYS_EXIT_EXTENDED (0x20) for exit code passthrough
- Linked with `arm-none-eabi-ld` as bare-metal at 0x40000000 (virt machine)
- Fixed ELF symbol table ordering (locals before globals, required by GNU ld)

### ARM32 assembler backend — IMPLEMENTED
- **pkg/asm/arm32**: full ARMv7-A instruction encoding (data processing, load/store, load/store multiple, branches, multiply, system). Rotated 8-bit immediate encoder. All instructions accept condition codes. 73 unit tests.
- **ELF32 support**: generalized `pkg/asm/elf` writer to emit ELF32 (for ARM32) or ELF64 (for AArch64/x86-64). Proper structure sizes, field ordering, r_info encoding for each class. Extracted `elf_util.bn` for code hygiene. 16 tests.
- **ARM32 text parser**: register parsing (r0-r15 + named), all operand types including register lists with range syntax (`{r0-r7, lr}`). Condition suffix + S flag stripping from mnemonics (`bne`→B+NE, `addseq`→ADD+S+EQ). Full instruction dispatch. Added `TOK_LBRACE`/`TOK_RBRACE` to lexer. 32 new parser tests (65 total).
- **Parser hookup**: `.arch arm32` directive, dispatch to ARM32 instruction parser.
- **CLI**: `cmd/bnas` already works for ARM32 via the parser — no changes needed.
- 220 tests total across all assembler packages.

### 4-word managed-slice migration — finalized
- **Conformance test 129**: subslice preserving backing_len. Creates `@[]int` of 5 elements, subslices to `s[1:3]` (len=2), verifies backing_len stays 5. Also tests double-subslice.
- **Bootstrap interpreter**: confirmed no changes needed.
- **Status**: all plan steps complete.

### Managed-slice flat storage in self-hosted interpreter
- **boot-comp-int: 146/147 conformance tests pass** (was 142 before)
- Added `TYP_MANAGED_SLICE` to `useFlatType` — managed-slice variables now use 32-byte flat headers with real `rt.MakeManagedSlice` backing
- `writeFlatValue`: added flat-to-flat copy path (memcpy 32-byte header)
- `@[]T → *[]T` coercion: flat managed-slice creates flat raw slice sharing same data pointer
- Flat managed-slice subslicing: creates new 4-word header sharing backing, preserves backing_len, RefIncs backing
- Element refcounting: flat index assignment RefInc/RefDec managed-ptr elements; `cleanupFlatMSliceElems` on reassignment
- Managed-slice backing refcounting deferred (leaks backing allocations, no correctness issues)
- Removed xfails: 126 (boot-comp-int, boot-comp-comp-int), 129 (boot-comp-int, boot-comp-comp-int)

## Done (session 2026-04-07)

### Interpreter flat memory: fix 4 struct regressions + 2 new bugs
- **Managed-slice RawAddr confusion**: `readFlatValue` for `TYP_MANAGED_SLICE` set `RawAddr` to backing refptr, but `evalLen` treated it as the slice header address. Fixed: `RawAddr` = header address, `evalLen` uses `MSliceLenOffset` for managed-slices. Tests: 109, unit `TestFlatStructManagedSliceField`.
- **Self-referential type resolution**: `execTypeDecl` replaced pre-registered struct types with new objects, breaking `Node { next @Node }` where the field still pointed to the old empty placeholder. Fixed: update placeholder's Fields in-place. Tests: 058, unit `TestSelfReferentialType`.
- **Return value managed-slice cleanup**: `cleanupEnvExcept` called `interpCleanupSlice` on return values, freeing elements. Fixed: skip cleanup for managed-slices in the return-values exception list. Tests: 107, unit `TestReturnManagedSlicePreservesElements`.
- **Lazy struct reads**: `readFlatValue` for `TYP_STRUCT` eagerly materialized ALL fields (including string/slice data), causing O(n) allocation per struct access. Fixed: return lazy Value with RawAddr only. `evalSelector` reads specific fields on demand. This fixed the `parser.ParseFile` infinite hang in boot-comp-int.
- **TYP_NAMED resolution**: `readFlatValue`/`writeFlatValue` didn't resolve named types (`type Kind int`), falling through to memset. Fixed: `resolveUnderlying` resolves both aliases and named types. Tests: 127, unit `TestFlatStructNamedTypeField`.
- **Lazy struct copy**: `copyValue` and `writeFlatValue` handle lazy structs via memcpy. Tests: 128, unit `TestFlatStructCopyOutAndBack`.

### Unit test backfill for flat memory model
- 15 new unit tests in `pkg/interp/call_test.bn` (total: 151)
- Covers: managed-slice fields, managed-ptr fields, string→@[]char, nil managed-slice, self-referential types, return value survival, len/index through flat struct fields, nested structs, named types, lazy struct copy

### Conformance tests added
- 127: named type struct fields (TYP_NAMED in flat memory)
- 128: struct field copy (lazy struct copy/write paths)

### boot-comp-int progress
- 142/144 conformance tests pass (was 138 at start of session)
- Fixed 4 xfails: 058, 102, 107, 109 (flat struct regressions)
- pkg/interp unit test xfail updated: no longer hangs (was "RegisterBootstrapPackage hang"), now xfail'd for inner interpreter return value wrapping

## Done (session 2026-04-03/04/05)

### Destructors — struct, managed-slice, array, anonymous struct
- `rt.RefDec(ptr *uint8, dtor *uint8)` — dtor called before Free when rc hits 0
- `types.NeedsDestruction(t)` — recursive query for types requiring cleanup
- `OP_FUNC_ADDR` — new IR opcode for function address as `i8*`
- Struct dtors, managed-slice dtors (with element cleanup loops), array dtors, anonymous struct dtors
- All use `linkonce_odr` for linker dedup. Cross-package references via `qualifiedDtorNameForType`.
- Conformance tests: 113-116.

### Anonymous struct support
- Both type checkers: `Identical()` with structural equivalence (field names + types in order)
- IR gen: `resolveTypeExpr` handles TEXPR_STRUCT, synthetic names, deduplication
- Conformance tests: 113, 119-121.

### `*any` → `*uint8` migration in pkg/rt

### Array codegen fixes
- `arr[i].Field` for managed-ptr elements, `cont.Items[i] = v` selector-base, element refcounting
- Conformance tests: 117, 118.

### Temporary lifetime fix
- Removed all leaking `consumeTemp` for `@[]T→*[]T`. Temps RefDec'd at end of statement.
- Migrated bnc to `@[]@[]char`. `bootstrap.Exec` now takes `*[]@[]char`.
- Conformance test: 122.

### .bni processing: RegisterSelfTypes expanded
- Now handles struct types, type aliases, and constants from the package's own .bni file.

### Negative conformance tests (19 total)
- 112 (slice nil), 200-210 (type mismatch, undeclared, wrong args, nil, return type, duplicate decl, operators, conditions, field access, indexing), 214-220 (comparisons, unary, call non-func, managed ptr arith, slice nil assign, multi-return, undefined type)
- `.error` files use `grep -E` regex matching for cross-checker compatibility

### Test infrastructure
- 6-mode unit test runner: boot, boot-int, boot-comp, boot-comp-int, boot-comp-comp, boot-comp-comp-comp
- Mode sets: basic (3), all (5), full (6). `bnc --test` just compiles (runner executes).
- Summary lines show mode. Bug discovery protocol in CLAUDE.md. Never-leak rule. Coding guide reference.

## Done (previous sessions)

### @[]T refcounting, OP_MAKE_SLICE migration, C runtime cleanup — `80b5150`
### Self-hosted interpreter HeapObj tracking — `c997b9f`
### Package search paths and implicit pkg/rt import — `ad394ee`
### @[]T layout, MakeManagedSlice, @[]T → *[]T conversion — `da07f70`
### bit_cast, pointer indexing, pkg/rt — `c80d962`
### Codegen bugs (074-087) — ALL FIXED
### Self-compiled compiler — FULLY PASSING ✓
### Remove append — DONE
### Remove null termination — DONE
### 4-word managed-slice layout — DONE
### Unit test backfill (two passes) — DONE
