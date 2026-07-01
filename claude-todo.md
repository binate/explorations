# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

**BUG BASH 2026-06-27.** Every open *bug* below is triaged into 3 parallel-worker lanes,
tagged inline `🏷[BUG-BASH 2026-06-27 → LANE N]` at the start of its entry:
- **LANE 1** — front-end semantics (`pkg/binate/{checker,types,parser}`): wrongly accepts / rejects.
- **LANE 2** — IR-gen & native codegen (`pkg/binate/{ir,codegen,native/*}`): emits wrong / invalid code.
- **LANE 3** — VM & cross-mode runtime (`pkg/binate/vm`, `pkg/std`, ABI): cross-mode marshaling / 32-bit-host.

Flags: ⚠ needs a semantics/scope **decision** first · 🔶 large / deferred (not a quick bash) · 🤝 shares
a file with another lane (coordinate). Untagged open entries are non-bugs (design / planning / perf /
coverage / doc) or already-resolved residuals.

---

## 🏷[BUG-BASH 2026-06-27 → LANE 2, NEEDS TRIAGE] MAJOR (IR-gen / cross-package link failure) — a cross-package METHOD VALUE mis-mangles the method symbol to the IMPORTER's package (2026-06-30) — 🔴 OPEN — CONFIRMED

`pkg/binate/ir/gen_method_value.bn` `genMethodValue` computes the underlying
method symbol via `buildMethodQualName(ctx.Gc.PkgPath, recvBase.Name, e.Name)` —
using **the importing module's** `ctx.Gc.PkgPath` (e.g. `main`) as the package,
not the receiver's DEFINING package. So `var mv = p.get` where `p` is
`*boxlib.Box` emits a reference to `main.Box.get`, a symbol that does not exist
(the real one is `pkg/boxlib.Box.get`) → the module fails to link
(`error: use of undefined value '@bn_F…main…Box…get'`).

Independent of aliases: a DIRECT, non-alias cross-package method value triggers
it. Confirmed on the pre-754 baseline compiler, so it is **pre-existing**, not
introduced by the 754 alias-receiver fix. (The 754 fix does newly make the
*alias* variant reach this same IR-gen path — `type AB = *boxlib.Box; var mv =
ab.get` — where before the checker rejected it earlier for a different reason;
so alias and direct cross-package method values now fail identically here.)

**How found (2026-06-30):** the bug-754 minimal adversarial review (cross-package
lens); reproduced with a two-package boxlib test.

**Test:** `conformance/942_xpkg_method_value/` (direct cross-pkg method value,
`.xfail.all`).

**Proposed fix:** derive the method symbol's package from the RECEIVER's defining
package rather than `ctx.Gc.PkgPath` — read it off the resolved `recvBase`'s
package-qualified name (the same technique `ir.recvBaseNameAndPkg` uses for the
impl-vtable key in the 754 fix: split the qualifier segment out of the base's
registered name), and pass that to `buildMethodQualName`. Mirrors how the impl
path derives `RecvPkg`. Un-xfail 942 when fixed.

---

## 🏷[BUG-BASH 2026-06-27 → LANE 3, NEEDS TRIAGE] MAJOR? (VM / intermittent halt) — `rt.Refcount` on an interned `@[]readonly char` literal's backing halts the VM mid-program in some statement sequences (2026-06-29) — 🟡 OPEN — NOT CLEANLY REPRODUCED

Surfaced while investigating the readonly-char-literal backing model (the
backing-form divergence itself is **resolved as intended** — environment-lifetime,
see `claude-todo-done.md`). Reading `rt.Refcount(<backing of an interned VM
literal>)` **halted execution mid-program** in some shapes: a minimal single read
works (`var s @[]readonly char = "abc"; rt.Refcount(backing(s))` → 2, full output),
but (a) a scope holding the literal, exiting, then re-evaluating the same literal,
and (b) a `println`-separated read of a different string both stopped right at the
refcount read — no further output, no diagnostic captured. Could be a real VM bug
(reading an interned-literal allocation's header in certain states) or a probe
artifact (raw-backing extraction via `bit_cast`). NEEDS a clean repro + root-cause.
`conformance/spec/07-types/278_..._literal` exercises only the minimal working form
(one `rt.Refcount(...) >= 2` read) and is green, so this item is the deeper
fragility, separate from that test.

---

## 🏷[BUG-BASH 2026-06-27 → LANE 3] MINOR (entry / link-time) — a program with NO `main` package (no entry) is not rejected at link/assembly time — 🔴 OPEN

The SIGNATURE half of the original entry-point bug — `func main(x int)` /
`func main() int` / generic `main` in package `main` silently accepted — is **✅
FIXED at COMPILE time** (LANE 1, `checkMainSignature`; see claude-todo-done.md /
binate `c1735910`). Per the language designer (2026-06-28) that is
the correct phase: compiling the `main` package SEES its own `main`, so its
*shape* is checkable there.

What remains is the **complementary LINK-TIME facet**: whether the assembled
*program* has a `main` package with a `main` function **at all** cannot be
determined per-package (Binate compiles one package at a time; any package may be
compiled/loaded independently), so a missing entry is a link / program-assembly
failure, not a compile-time check. §17.3.1 (amended 2026-06-28) now states this
split explicitly. Open: add the existence check to the link/program-emit step
(reject assembling a program whose `main` package has no `func main`). No
conformance repro yet (needs a no-main-package program at link time).

---


## rt.Abort/rt.Panic Plan 2 — make user-code VM faults recoverable (host survives) — 🟡 SCOPE REQUIRED (2026-06-20)

Plan doc: [`plan-rt-abort-panic.md`](plan-rt-abort-panic.md). **Plan 1 (the
`rt.Abort`/`rt.Panic` primitives, the `panic()` single-string + lowering change,
and the VM internal-abort migration through `panic()`) is DONE & LANDED** — see
claude-todo-done.md.

User-code runtime faults (bounds / divide / shift / nil-deref / stack-overflow /
call-through-nil) should be RECOVERABLE in the VM (the host REPL / test-runner /
embedder survives a bad interpreted program) while staying fatal in compiled
code. The 6 VM user-fault sites are deliberately still on `rt.Exit(1)` pending
this. Approach (per user): rt is already injected into the VM, so a faulting user
op already calls the *injected* `rt.Panic`/`rt.Abort`; inject a VM-specific
variant that unwinds the VM's DATA-stack frames (`vm.Stack`) back to `CallFunc`
instead of killing the host (no longjmp — the user call stack is data, not the
host stack). Open: the exec-loop unwind mechanism + refcount-correct frame
teardown.

Related smaller follow-up: route panic / `runtime error:` / VM diagnostics to
**stderr** (fd 2) — deferred out of Plan 1 (infra exists: `bootstrap.Write(fd)`,
`bootstrap.STDERR = 2`); a real behavior change for anything scraping them off
stdout.

---

## Embeddable-interp — open follow-ups (Inc 2 extern cleanup core landed) — 🟡 OPEN (2026-06-20)

The embeddable-interp core (Inc 1, Inc 2 Layers 1/2 + the review (b)-fix, and the
loader de-rooting) is **✅ DONE & LANDED** — full detail in
[claude-todo-done.md](claude-todo-done.md). Plan:
[`plan-embeddable-interp.md`](plan-embeddable-interp.md). Remaining open
follow-ups (deferred with user sign-off):

- **runTests / global `IsNativeOnlyInVM` unification.** The `--test` runner
  (`cmd/bni/main.bn`) still keys the lowering skip on the hardcoded
  `IsNativeOnlyInVM` (fixed stdPkgs config); only the `@Interp` run path derives
  it from the inject-set. Unify so there is one mechanism.
- **Lower-time "this impl can't be interpreted" guard.** Dropping a package
  whose only impl needs native facilities (today's `os`/`__c_call`) and letting
  it lower yields silently-broken bytecode (can't reach `cLseek`). The
  principled guard is a lower-time check on the impl (does it use `__c_call` /
  native-only facilities?) that errors clearly instead.
- **Globals/vtables-sensitive inject-set test.** `TestNewCustomPkgsRespected`
  proxies on `len(Externs)` (function registration only); add a test that a
  custom set's globals + impl vtables are honored (the `errors.Is`
  sentinel-identity path).
- **Layer 2b — `@reflect.Package` wrapping helper.** Build a modified descriptor
  from an existing one with selected `FunctionInfo` values replaced, so an
  embedder overrides e.g. `os.Args()` without hand-constructing a descriptor.
  This is the ergonomic per-function override path; it also rehomes the
  `progArgsAfterDash` Args shim (becomes a cmd/bni-built wrapped-`os` concern
  rather than baked into interp's bootstrap registration). Land with an
  end-to-end test proving a wrapped package changes observed runtime behavior.
- Optional: auto-enumerate bootstrap's exported format helpers via
  `RegisterPackageFunctions` (they qualify — exported, non-extern), leaving only
  the 9 extern C-I/O entries hand-bound.

---

## Ch.7 spec-conformance findings (2026-06-20, authoring `conformance/spec/07-types`) — 🔴 OPEN

Four findings surfaced while authoring the Ch.7 type spec tests; each is pinned by an xfail.

1. 🏷[BUG-BASH 2026-06-27 → LANE 1] **MAJOR (type-checker / wrong-code) — cross-package distinct named SCALAR types wrongly inter-assign.** Same-package `type A int; type B int; var b B = a` correctly rejects ("cannot assign A to B"), but cross-package `red.T -> blue.T` (each `type T int`) **compiles** without a cast — cross-pkg named-type identity is not enforced for scalar underlyings (type.named.identity, type.named.assignability). Possibly related to the int↔int64 identity-by-width bug, but distinct (that one is same-package width; this is cross-pkg). Pinned: `conformance/spec/07-types/049_named_identity_cross_pkg` (xfail.all).

2. 🏷[BUG-BASH 2026-06-27 → LANE 2] **✅ DONE & LANDED (main `0e7fd844`) — native (aa64+x64) named-managed-slice subslicing.** A distinct named managed-slice (`type Buf @[]int`) SIGSEGV'd at a subslice (`buf[0:2]`) on BOTH native backends: `emitGetFieldPtr`'s slice-base classifier `isSliceFieldBase` did not peel `TYP_NAMED`, so the refptr/backingLen field stores got no offset and corrupted the header (index/len worked; only the slice-expr field stores crashed). Fix: hoisted `isSliceFieldBase` → `native/common.IsSliceFieldBase` beside its peeling sibling `StructTypeOf`, peeling transparent wrappers before the Kind check — one site, both backends, removing the byte-identical aa64/x64 copies (verified it was the only un-peeled native slice-Kind site). Un-xfailed `033_named_transparency` (aa64; also red→green on x64), `719_named_slice_transparency` (aa64+x64), `904_named_slice_backing_pin` (aa64; red→green on x64); `036_named_assignability_composite`'s aa64 xfail was already stale from the earlier IR-gen un-peel sweep. Verified native_aa64 (real HW) + native_x64_darwin (Rosetta). Full diagnosis in [claude-todo-done.md](claude-todo-done.md).

3. 🏷[BUG-BASH 2026-06-27 → LANE 1] **opaque-type encapsulation LEAKS under co-compilation — ✅ DONE & LANDED (main `f9e915fa`, 2026-06-29).** An imported opaque type (forward `type Box` in .bni, full body in .bn) leaked its layout to importers when the .bn was co-compiled (the shared Type's Underlying got filled for the provider). Fixed via a **two-view model**: new `Type.OpaqueExportPkg` (owner package) stamped ONLY at the .bni forward-decl site (so a plain .bn-only cross-package struct is NOT marked — that distinction fixes the 157/331 over-rejection); `isCrossPkgOpaqueExport` / `isOpaqueValueType` make an opaque export opaque to OTHER packages while the provider keeps full layout for codegen (checker-only — IR-gen never reads it). Gated at EVERY importer value-formation route: field-read (methods still resolve), all by-value declaration sites, deref, single + multi-return call-result, index, range, cast, + composite-literal field-info-leak. **Scope grew well beyond the original field-read bug** (the by-value + expression-level gaps were partly pre-existing; four adversarial-review passes drove it). Un-xfailed 222; new 226/227/228/229; 846's `x := *g()` now genuinely rejected. builder-comp-comp 2517/0, builder-comp-int 2494/0. **Known benign residual:** `var b Box = *p` double-reports "cannot use an opaque type by value" (decl gate + deref gate, different positions) — compile fails either way. Full write-up in [claude-todo-done.md](claude-todo-done.md).

4. 🏷[BUG-BASH 2026-06-27 → LANE 2] **✅ DONE & LANDED (main `e9bdd05f`) — managed pointer-to-array `@([N]T)` indexing.** `(*m)[i]` on an `@([3]int)` emitted "invalid getelementptr indices": the LLVM `emitGetElemPtr` gated its single-index pointer path (bitcast i8*→ElemT*, one-index GEP) on `TYP_POINTER` only, so a managed pointer (also an i8* pointing AT its element buffer) fell to the array-alloca double-index path. Fix (`pkg/binate/codegen/emit_helpers.bn`): include `TYP_MANAGED_PTR` in the pointer-value gate (LLVM-backend-only — native/VM compute the element address by offset and already handled it). Pinned by new `conformance/spec/07-types/147_managed_ptr_array_index` (green on builder-comp / -int / native_aa64). (`m[i]` — direct index without an explicit deref — still rejects "cannot index this type"; that matches the raw `*([N]T)` form and is not part of this bug.)

---

## 🏷[BUG-BASH 2026-06-27 → LANE 1] Ch.5 spec-conformance findings (2026-06-20, authoring `conformance/spec/05-lexical`) — 🔴 OPEN

Surfaced while authoring the Ch.5 lexical spec tests. The two spec/impl
**divergences** (`\uHHHH` escape; `1.foo` greedy-float-vs-selector) moved to
[`spec-todo.md`](spec-todo.md) — they need a "fix spec or fix impl" decision and
are pinned by `055`/`035`. The minor unary-`+`-rejected question is there too.
The items below are settled-intent impl gaps already pinned by xfails.

1. **Open item now pinned — single-byte character-literal constraint (`lex.literal.char.one`) is not enforced.** Empty `''` silently decodes to `0x00`; multi-byte `'ab'` silently truncates to its first byte (`'a'`=97); neither is diagnosed. Already acknowledged as an open item in spec §5.10/§5.14. Pinned: `056_char_empty_xfail`, `057_char_multibyte_xfail` (xfail.all). (No new decision; the xfails make it reproducible so Annex C flips when a diagnostic is added.)

2. **Reused existing gap — `[...]T{}` inferred-length array literals unimplemented.** `var a [...]int = [...]int{...}` is rejected `expected expression` (same gap as `conformance/spec/13-expressions/041`). The `...` token itself lexes as one token. Pinned for Ch.5's `lex.punctuation.set` `...` coverage by `122_punct_ellipsis_xfail` (xfail.all).

**Stale-note correction (DONE) in `docs/spec/05-lexical-elements.md`:** the §5.11 note claiming unknown escapes are "silently decoded … backslash dropped … no diagnostic" was **stale** — they are rejected with `unknown escape sequence` (and bad `\x` with `\x escape requires two hex digits`). Corrected; the Ch.5 negatives `047`–`054` pin the rejection (green). (The `\uHHHH`/§5.1 and §5.8 reconciliations stay open in [`spec-todo.md`](spec-todo.md).)

---

## Ch.9 spec-conformance findings (2026-06-21, authoring `conformance/spec/09-declarations-and-scope`) — 🔴 OPEN

Authoring the Ch.9 tests surfaced the MAJOR raw-pointer zero-init bug (filed separately,
above) plus two MINOR items.

1. 🏷[BUG-BASH 2026-06-27 → LANE 1] **func-local grouped declarations — ✅ DONE & LANDED (main `81a4566b`, 2026-06-29).**
   A grouped `const ( … )` / `var ( … )` inside a function body failed with `undefined: <member>`
   (even with explicit values, independent of iota): the statement-level decl handling routed only
   `DECL_CONST` / `DECL_VAR` / `DECL_TYPE`, never `DECL_GROUP`, in BOTH the checker and IR-gen.
   Fixed broader than originally filed — turned out to break `var` groups too. Checker routes
   statement-level `DECL_GROUP` → `checkGroupDecl`; IR `genLocalGroupDecl` lowers per member kind
   (const → `genConstGroup`, reads via `emitModuleConstByName`; var → materialize each; func-local
   `type` group correctly rejected, decl.type.package-only). 007 un-xfailed; new 008 (var group) /
   009 (type-group rejection). builder-comp 2490/0, builder-comp-int 2465/0. Full write-up in
   [claude-todo-done.md](claude-todo-done.md).

2. **MINOR (underspecified) — package-level VAR initialization is declaration-order, not
   dependency-order; the spec doesn't pin it.** `var A int = B + 1; var B int = 10` makes `A == 1`
   (B is still 0 when A initializes), NOT 11. `decl.order.forward` guarantees the forward NAME
   reference resolves (it compiles), but the VALUE at init time follows declaration order. Go
   initializes package vars in dependency order; Binate does not, and §9.8 is silent on var-init
   order. → a spec-vs-impl decision (declaration-order vs dependency-order) for `spec-todo.md`.
   The Ch.9 tests do not assert any var-init-order value (forward-ref is tested via a function).

---

## Ch.20 spec-conformance findings (2026-06-22, authoring `conformance/spec/20-tier0`) — 🔴 OPEN

1. **GAP (harness limitation, not a defect) — `pkg0.testing.testfunc` + `pkg0.testing.run` are not
   conformance-testable.** Both require the `--test` discovery/execution runner (`cmd/bnc --test` /
   `cmd/bni --test`); `conformance/run.sh` only runs ordinary programs (no `--test` plumbing). They
   are exercised by the unit-test suite, not conformance. Closing them would need a test-runner mode
   added to the harness. Left as documented coverage gaps (Ch.20 is 18/20). Candidate for an
   `untestable`/`framework` reclassification in `extract-rule-ids.py` (a denominator decision).

---

## 🏷[BUG-BASH 2026-06-27 → LANE 3] cross-mode coerced-agg func-value ABI — follow-ups after the by-address land (binate `233cc82d`) — 🟡 OPEN

The cross-mode coerced-aggregate-ARG residuals — the iface/func-value by-address
fix (items 1, MAJOR), the >7-arg extern guard (item 2), and the sub-word/bool RETURN
(item 4) — LANDED via the by-address ABI rework (`233cc82d`) + the >7-arg guard
(`17cfc16b`); see claude-todo-done.md. Smaller follow-ups remain:

1. **Observable fixture (coverage).** Items 1/4 are validated via conformance 937
   (func-value coerced-agg, all backends + VM) + 938 (wide spill) + the math
   narrow-mechanism, but the IFACE-method coerced-agg path and the sub-word/bool
   RETURN have no DIRECT observable test (no injected stdlib exercises them). Wants a
   pkg/binate/vm unit fixture: a synthetic injected native package with an iface
   method / func value taking a coerced-agg by value AND returning a sub-word/bool.
   Closest: TestExternSmallStructAggregateDispatch + vm_exec_iface hand-built vtables.

2. **shim-extends RETURN (cleanup, optional).** The sub-word RETURN was fixed VM-side
   (the 25117a2e VM-narrow mechanism extended to iface/func-value), since item 4 is
   VM-only. The review's cleaner shim-extends design (every backend's shim sext/zext's
   sub-word returns; drop the VM narrow) is deferred — a multi-backend,
   target-word-dependent change with a tail-branch→call-shape wrinkle.

3. **x64_closure_shim.bn soft length** (584 > 500 warn; not a hard blocker) — split
   like aarch64_closure_shim_spill.bn was. The native SPILL paths also still stage
   incoming unconditionally (rare over-budget path; could be made conditional like the
   register-only marshalers).

See explorations/plan-funcvalue-byaddr-abi.md.

## MINOR (e2e / BUILDER-lag cleanup) — drop the gen1 build in e2e/stat-values.sh after the next BUILDER bump (2026-06-20) — 🔴 OPEN

`e2e/stat-values.sh` builds gen1 from the tree (`scripts/build-bnc.sh`) and compiles its os.Stat probe through gen1, instead of the simpler `$BUILDER … cmd/bnc -- …` form the other e2e scripts use. Reason: os.Stat depends on the `.bni` free-func/method fix (`796effc7`) and the wholesale-os-injection work, which postdate `BUILDER_VERSION` (bnc-0.0.9) — the pinned BUILDER can't compile os yet. Once BUILDER is bumped past those, revert `e2e/stat-values.sh` to the plain `$BUILDER … cmd/bnc -- …` pattern (drops the ~1-min gen1 build per e2e run).

---

## Stdlib conformance suite — optional follow-ups — 🟢 LOW (2026-06-20)

The suite is built and every injected stdlib package has cross-mode coverage
(moved to claude-todo-done.md). Two optional cleanups remain:
- Fold the ~8 ad-hoc stdlib-importing tests in the MAIN conformance set
  (`577_std_errors`, `855_std_time`, `662_errors_is`, `526/528/535_strconv`,
  `663_io_iseof`, `726_cross_pkg_iface_impl`) into `conformance/stdlib/*` (and
  drop their `conformance-imports.whitelist` entries).
- Remove the now-redundant `os_test.bn` `TestErrorIfaceUpcast` (covered by
  `conformance/stdlib/errors/001`; only runs under `builder-comp` now), or keep
  it as a native-only smoke.
---

## 🏷[BUG-BASH 2026-06-27 → LANE 1] MINOR (import hygiene) — two non-wrong-code follow-ups from the file-scoped-imports work — 🟡 OPEN
The PACKAGE-scoped-imports CRITICAL (all wrong-code facets — visibility leak, same-alias miscompile,
qualified-TYPE memory-layout corruption, implicit same-last-segment, generic instantiation, the
cross-file package-level `var x = dep.Foo()` residual) is ✅ FULLY RESOLVED & LANDED and archived in
[claude-todo-done.md](claude-todo-done.md).  Two non-miscompile follow-ups remain:
- **(F-checker) the checker has ZERO unused-import handling** — the only unused-import check is the
  opt-in bnlint rule, whose per-file attribution has false-positive (sibling-file use) and
  false-negative (local var shadowing an alias) corners.  Entangled with / tracked by the
  "(planning) unused-entity checks" entry below (`plan-unused-checks.md`).
- **Build-confirmation coverage want**: an incompatible-signature escalation test for the A/B facets
  (`func V() *uint8` vs `func V() int` colliding members → show ABI/result-type confusion), on top of
  the existing 830/831/832 conformance coverage.  Low priority — the facets are fixed and tested.

## (planning) unused-entity checks — fix the unused-import `(a)` cross-file gap + add `(b)` unused locals / `(c)` unused private funcs / `(d)` unused private globals / `(e)` unused private types — 🟡 PLAN WRITTEN (`plan-unused-checks.md`)

bnlint today has exactly one "unused" rule (`unused-import`, `pkg/binate/lint/unused_import.bn`); the type checker has no usage tracking at all. **Plan written: `explorations/plan-unused-checks.md`** (phasing, per-rule design, edge cases, tests, open decisions). Foundational dependency: the CRITICAL import-scoping bug above — fix direction **1 (file-scoped imports)** chosen; that is Phase 0 and `(a)` rides on it. `(b)` is checker-side (Used flag + popScope sweep, BUILDER-compatible); `(c)`/`(d)`/`(e)` are lint-side over a shared `refs.bn` reference index. Open decisions (warning-vs-error, reference-vs-reachability, params/write-only/consts, receiver-as-use) are listed in the plan for the user. Two latent bugs surfaced and noted there: `markBniExportedVars` skips `DECL_GROUP`; `DECL_TYPE` carries no `Exported` flag.

## MINOR (hygiene / lint) — investigate the `[managed-to-raw-assign]` findings in `pkg/binate/asm/*` (2026-06-20) — 🟡 OPEN
The compiler-tree lint-coverage gap is ✅ FIXED & LANDED (`582c1327`): `scripts/hygiene/lint.sh`
discovery is now recursive over `pkg/`, so all ~23 `pkg/binate/*` compiler packages are bnlint
targets (the old one-level `pkg/*/` glob matched only `pkg/binate/`, which has no direct `.bn`, after
the `pkg/parser`→`pkg/binate/parser` reorg — so ZERO compiler packages were linted; only the
bnlint-RULES check had this gap, since file-length/naming/doc use a recursive `find`).  Two real
`[unused-import]`s it surfaced (`ir/gen.bn`→ast, `native/aarch64/aarch64_call.bn`→mangle, both
comment-only) were removed.  **Residual** — 5 asm subpackages are temporarily in `LINT_SKIP`
(`pkg/binate/asm/{arm32,elf,macho,parse,x64}`) for a `[managed-to-raw-assign]` finding
(`var data *[]uint8 = sec.Data` — a borrow of a held `@[]uint8`).

**Per-site audit DONE (2026-06-30, bnc-0.0.10 bnlint + adversarial workflow + source verification of
the one real bug).** 19 findings across the 5 packages:
- **1 REAL use-after-free** — `parse/parse.bn:160` (`name = expr` constant def borrowed `tok.Text`,
  then `LexNext` freed it before the read). ✅ **FIXED & LANDED (main `8a883450`)** — own the name
  first (`buf.CopyStr`) + regression test `TestParseConstNamePreserved` (verified failing pre-fix);
  write-up in the done file. The rule was RIGHT here — the skip hid a real UAF.
- **1 real `[unused-import]`** — `parse/aarch64.bn:3` imported `pkg/binate/asm`, never used. ✅ FIXED
  (main `8a883450`, same commit).
- **17 safe-borrow over-flags** — every site in `arm32`/`elf`/`macho`/`x64` (all 9) + 6 of the 8
  `parse` sites. All borrow a field of a managed owner (`@asm.Section`/`@asm.Assembler`/a `BinBuf`
  local / a by-value `Token` param / a function-scope buffer) that provably outlives the raw view's
  synchronous read or in-place patch. The rule conservatively flags `@[]T → *[]T` without lifetime
  analysis.
**Un-skip path:** the two real findings are ✅ FIXED (main `8a883450`), but all 5 packages still carry
safe-borrow over-flags (`parse` now has 8; `arm32`/`elf`/`macho`/`x64` all their sites), so none can be
un-skipped yet — un-skipping as-is would red hygiene. **Remaining (OPEN) — decide how to handle the 17
false positives:** refine the `[managed-to-raw-assign]` rule to do lifetime/escape analysis, or add a
per-borrow suppress annotation, then drop the 5 packages from `LINT_SKIP`.
(The BUILDER-lag `LINT_SKIP` entries — rt/os + chain cleared at bnc-0.0.10, only `pkg/binate/interp`
remains — are tracked in the separate BUILDER-lag-lint-skips entry.)

## 🏷[BUG-BASH 2026-06-27 → LANE 1] MINOR (latent) — same-final-segment generic INTERFACES collide (the iface analog of the now-fixed struct/func same-segment collisions) (2026-06-20) — 🔴 OPEN

The generic-FUNC (`330c42fe`) and generic-STRUCT (`5ae791d2`) same-final-segment
collisions are fixed by keying on the DEFINING package.  Generic INTERFACES were
deliberately left on SHORT-name keying (to bound the struct fix and avoid the
interface-identity tangle — `MakeInterfaceType` uses the short name, and #130
keys instantiated ifaces on `mi.Pkg`).  So two same-final-segment packages each
declaring a generic interface of the same decl name still collide: the generic
iface decl stash (`GenericIfaceDeclPkgs`, keyed `curPkgShort` in bni_scope.bn /
check_interface.bn) and `resolveTypeInstantiation`'s iface lookup (raw
`head.Pkg`) both use the short name.  Fix mirrors the struct change: stash
generic iface decls under the full path, resolve the aliased head to a full path
for the iface lookup, and reconcile with the `mi.Pkg`/`MakeInterfaceType`
short-name identity (the part that needs care).  Same bounded/fail-safe severity
as the struct case.  No conformance test yet.

**BLOCKED behind unimplemented generic-interface-VALUE codegen (Slice 6c)
— discovered 2026-06-28 (BUG-BASH LANE 1).** Attempting to build a reproducing
test surfaced that this collision is UNREACHABLE behind a far larger gap: an
instantiated generic interface used as a VALUE with dispatch (`var h
@gen.Holder[int] = gen.Make(...); h.get()`) fails in codegen — `error:
extractvalue operand must be aggregate type` (LLVM) — even in a SINGLE package
(no collision). This is the pre-existing Slice-6c gap that check_interface.bn's
own comment flags ("IR-gen for instantiated interfaces (vtable layout, dispatch)
is Slice 6c territory; until that lands, declaring a value of a
generic-interface-value type would fail at IR-gen time"). So the same-segment
COLLISION can't be reproduced or validated until generic-interface-value codegen
exists. The checker-side decl-resolution mirror (stash by full path, resolve the
aliased head, fallback on curPkgPath in lookupGenericIfaceDeclPkg) WAS prototyped
and gets the checker past `undefined: Holder` to reach the codegen gap — but was
REVERTED (an unvalidatable partial that enables nothing usable on its own).
**Re-scope:** this is really "implement generic-interface-VALUE codegen (Slice
6c)" first (a major IR project: vtable layout + dispatch for instantiated generic
interfaces), THEN the same-segment-collision keying becomes a small mirror of the
struct fix on top.  Not a quick same-segment-keying bash.

## 🏷[BUG-BASH 2026-06-27 → LANE 3] MINOR — cross-mode interface dispatch: test-coverage gaps + LP64 assumption (2026-06-14) — 🟡 OPEN

The shim-route that dispatches a native-only package's interface methods from
bytecode (landed `93f75f27` + the math/big extension `7c3b17a2`) is exercised by
726 (`strings.Builder` via `io.Writer`: a raw-slice arg, a scalar arg, a no-arg
method; scalar + multi-return) and 577 (`errors.Error`: no-arg, multi-return).
An adversarial review found these shapes UNTESTED — each needs a SYNTHETIC
native-only test package, since no current stdlib impl hits them:

- A VALUE-receiver iface method (`@__ivtshim` slot holds the thunk's handle, and
  `a0` = the iv-data ptr the thunk derefs). 410 covers native-to-native only.
- A method with MULTIPLE aggregate args (the `a1/a2/...` slot accounting).
- A FLOAT arg / float-containing aggregate (the shim's int-slot bitcast path).
- The `n>6` user-arg overflow guard (a negative test).

Latent, LP64-host-only (NOT active — default VM modes run a 64-bit host):
- `dispatchCompiledIfaceMethod`'s `resultSize > 8` aggregate-vs-scalar threshold
  (and `dispatchExternBinding`'s identical one) must track `isAggregateReturn`'s
  `> target.PointerSize`; on an ILP32 VM host a 5–8-byte aggregate return would
  pick the wrong shim shape. (Now commented in `vm_exec_iface.bn`.)
- 64-bit-scalar args pack as 2 slots on a 32-bit host (`argSlots`); the dispatch
  reads them as positional shim args.

Separately (PRE-EXISTING, independent of the VM): the COMPILED native iface-call
path (`emitCallIfaceMethod`) has no HFA classification — a struct-of-floats arg
is mis-seen as a GP aggregate (no `IsFloatScalarTyp`-style struct handling in the
native backend; the LLVM side relies on LLVM to classify HFAs).

**Native-source iface UPCAST (task #94, 2026-06-19): only offset-0 dispatch is
reachable.** The VM's `BC_IFACE_UPCAST` native-source branch (`vm_exec_iface.bn`)
advances the native vtable word by `offset*8`, mirroring `emit_iface_upcast.bn`.
Offset 0 (`@X→@any`, `@X→*X` managed↔raw decay) passes the registered base
through unchanged, so a later native method dispatch still resolves via
`lookupShimVtable`. A REAL-parent upcast (offset>0) forms the result value
correctly, but a method call ON the result would do `lookupShimVtable(base +
offset*8)` — an exact-match MISS on the unregistered adjusted address → loud
"no shim vtable" abort (NOT silent corruption). Unreachable today: no stdlib
runtime interface `extends` another (`Orderable`/`Hashable : Comparable` are
generic constraints, not upcast as iface values). To support offset>0 dispatch,
`lookupShimVtable` needs a RANGE lookup — register each injected vtable's SIZE,
find the base `B` with `B ≤ addr < B + size*8`, and map to `shim_base(B) +
(addr − B)` so the parent sub-block's shim resolves. Covered as-is by
`pkg/binate/vm` unit tests (the offset arithmetic, incl. offset>0 value
formation) + `os` `TestErrorIfaceUpcast` (offset-0 end-to-end, both modes).

---

## MINOR — remove the `impls/stdlib/common` compat symlink at the next BUILDER bump (2026-06-14) — 🟡 OPEN

`impls/stdlib/` was flattened (`impls/stdlib/common/pkg` → `impls/stdlib/pkg`,
`5ae15031`), but `scripts/binate-paths.sh` still emits `$BASE/impls/stdlib/common`
as the stdlib impl search root, and a `common -> .` symlink makes that resolve
against the flattened tree. The symlink exists ONLY because the pinned BUILDER
bundle (`bnc-0.0.9`) still ships a real `impls/stdlib/common/` dir, and
binate-paths uses one formula for both the current tree and the bundle base —
so emitting `$BASE/impls/stdlib` now would break gen1's resolution of the
bundle's stdlib.

**Do this once `BUILDER_VERSION` is bumped to a bnc cut from a tree at/after the
flatten** (any BUILDER built from main ≥ `5ae15031` ships `impls/stdlib/pkg`
directly, so `$blib/impls/stdlib` resolves):
1. Change `scripts/binate-paths.sh` (the `build_list impl` branch, ~line 169)
   from `$BASE/impls/stdlib/common` to `$BASE/impls/stdlib`.
2. `git rm impls/stdlib/common` (the symlink).
3. Sweep remaining `impls/stdlib/common` references: `scripts/fetch-builder.sh`
   (comment examples), `BUNDLE-HOWTO.md`, and the `pkg-layout-spec.md` /
   `impls/stdlib/README.md` notes that describe the symlink as transitional.
4. Verify: full `builder-comp` (gen1 from the new BUILDER + compile) green.

Until then the symlink is load-bearing — don't remove it without the
binate-paths change, and don't make the binate-paths change without a flattened
BUILDER.

## closure-shim user-word `EffectiveArgWords` + stack-overflow (funcval miscompile) — 🟢 COMPLETE both backends (EffectiveArgWords + GP/float scalar/aggregate overflow, register + spill); only the big multi-return-WITH-floats SetError follow-up remains (see end of entry)

FOLLOW-UP to the now-resolved non-closure funcval-shim marshalling fix (full
diagnosis + Stage A/B + B0 Functions-table archived in claude-todo-done.md).
The non-closure shims were switched to `cc.EffectiveArgWords`, but the CLOSURE
shims were NOT:
- **(1) raw `ArgWords` for USER words** — a closure shim does `common.ArgWords(ut)`
  for user words instead of `cc.EffectiveArgWords`. For an indirect-large user
  arg (managed-slice = 4 words, iface = 2, `>16B` struct ≥ 3) this over-counts
  vs. the dispatch caller's single-pointer placement, mis-shifting `inRegBase` /
  outgoing regs. **CONFIRMED wrong-code (not just latent)** by the 2026-06-21
  adversarial review of the 706 work: e.g. a closure `@func(s @[]int) Big24`
  capturing a `float64` passes the budget gate (`nUserWords = ArgWords(@[]int) =
  4`, not `> 4`), spills 4 incoming GP words when the dispatcher set only 1 (the
  slice pointer), and reloads garbage — silent miscompile / memory corruption.
  - **✅ FIXED for ALL aarch64 closure shims:** the GP-only
    (`aarch64_closure_shim.bn`) and GP-aggregate
    (`aarch64_closure_shim_aggregate.bn`) shims (binate `b78819a1`, review
    wf_25bd5ddf) and the FLOAT shims' shared marshaller
    `loadClosureFloatCallArgsAA64` (`aarch64_closure_shim_float.bn`, binate
    `ba9555b9`, review wf_bb2f5df3 — rewritten classifier-driven, user-word
    counts via `cc.EffectiveArgWords`).  An indirect-large user arg is one
    pointer word in/out; the GP shim's `emitUserArgWordMoveAA64` and the float
    marshaller both handle SPLIT.  `EffectiveArgWords`==1 + the classifier's
    single-slot placement subsume an explicit `isIndirectLargeCap` user-arg
    branch.  Covered by conformance 907 (GP) + 915 (float).
  - **✅ FIXED for ALL x64 closure shims** (binate `1603b542`, x64-mirror
    increment A, review wf_8bc32446): the GP-only (`x64_closure_shim.bn`),
    GP-aggregate (`x64_closure_shim_aggregate.bn`), and float
    (`x64_closure_shim_float.bn`, `loadClosureFloatCallArgs_x64`) shims switched
    their user-word counts to `cc.EffectiveArgWords`.  SysV doesn't SPLIT, so the
    classifier's single-pointer slot subsumes an explicit `isIndirectLargeCap`
    user-arg branch.  Un-xfail'd 915 (was a spurious x64 SetError).

So the `EffectiveArgWords` miscount (1) is RESOLVED on both backends.  The
broader **"closure shims SetError on stack OVERFLOW"** follow-up (the GP analogue
of GAP D) is also mostly landed:
  - ✅ **x64 GP-aggregate stack-spill** (binate `20c1d9be`, increment B, review
    wf_e5367900): `emitClosureShimAggregateStackSpill_x64` (pack/sret, retbuf via
    a prepended marshal-arg for sret, data in R11).  The review caught a CRITICAL
    pre-land downward-shift miscompile — unconditional high-first user-arg moves
    corrupt the pack + single-capture-word case; fixed with a two-branch
    direction selector (low-first when `capBaseSlot + captureWords < 2`).  Chasing
    it exposed the SAME defect in the *landed* aarch64 GP-aggregate shim → fixed
    (binate `a187d60b`, conformance 925, review wf_dbd6d6fa; `emitAggUserArgAA64`
    gained a `lowFirst` flag).  Both FLOAT aggregate shims audited clean — their
    memory-spill design has no register-to-register aliasing.  Flipped x64 xfails
    906/907; added 921–924.
  - ✅ **x64 FLOAT overflow (increment C)** (binate `2c5e3c04`, review
    wf_f7212aaf): `emitClosureShimFloat_x64` / `emitClosureShimFloatAggregate_x64`
    / `loadClosureFloatCallArgs_x64` (+ new `marshalFloatShimArg_x64`) restructured
    classifier-driven, mirroring the aarch64 float shim (reserve outgoing-args
    area, spill incoming from regs+caller-stack, marshal from memory by class; sret
    uses the same prepended-sret-slot trick as the GP-aggregate).  The shim asm was
    verified correct by hand.  Flipped x64 xfails 912/913/914/919/920.  The big
    multi-return-with-floats case stays SetError on both backends (separate
    follow-up).  Added 926 (aggregate FP-overflow), xfail'd on x64 — it exposed the
    separate native-x64 frame-overlap bug below, NOT a shim defect.

The closure-shim `EffectiveArgWords` + stack-overflow story is now COMPLETE on
both backends (GP scalar/aggregate + float scalar/aggregate, register + overflow).
- **(2) no float-scalar user-arg GP→FP marshalling** — ✅ RESOLVED by the
  closure-float shims (claude-todo #121: 569/705/706, binate `085065d9` …
  `0c54d69d`). `emitClosureShimFloat*` / `emitClosureShimFloatAggregate*` now
  marshal float-scalar captures/params GP→XMM/D. Only (1) remains.

Reference to mirror: the landed non-closure spill in
`pkg/binate/native/{x64,aarch64}/*_funcvalue_spill.bn` (uses
`cc.EffectiveArgWords`). No closure-spill/wide-closure conformance test exists
yet. B0's force-emit only emits NON-closure triples, so this doesn't block B0 —
ready-to-pick follow-up. (User owns.)

### Keyed array-literal index: checker and IR-gen fold with DIFFERENT folders — 🟡 OPEN (latent, no reproducer)
The Array composite-literal defects (indexed literals, over-count reject + named/struct
siblings, inferred-length `[...]T`, positional struct-lit assignability) are ✅ DONE &
LANDED — see [claude-todo-done.md](claude-todo-done.md). One latent residual remains
(pre-existing; surfaced 2026-06-29 by the bug-662 adversarial review, NOT introduced by it;
shared by ALL `[N]T{k: v}` keyed array literals): the checker folds a keyed index via
`evalConstIntValue` (`check_expr_composite.bn` checkArrayLit / inferArrayLitLen) and
bounds-checks it against N, while IR-gen recomputes the per-element index via a DIFFERENT
folder, `evalConstExpr` (`gen_composite.bn`). If the two ever disagree on a constant key, IR
could place an element at an index the checker did not bound-check → an out-of-bounds store.
No known reproducer (the folders agree for normal int-literal/const keys). Same family as the
759 host-int-vs-bignum divergence. **Fix:** make IR-gen reuse the checker's already-validated
index (stamp it, like LenVal) instead of re-folding — OR route both through one folder. No
test (no reproducer); add an xfail if one is found.

### 🏷[BUG-BASH 2026-06-27 → LANE 3] `__Package()` bytecode VM (Gap 2) — ✅ DONE & LANDED (binate `77c3378d` MIN + `d4edd671` FULL, 2026-06-29)

Bytecode-VM `__Package()` + `reflect.Package` descriptor for user/stdlib
packages: the VM emits the descriptor (name + Functions table with callable
`FunctionInfo.Value` handles) per lowered package via a synthesized accessor +
a generic `DataGlobal` relocation lowerer (`pkg/binate/vm/lower_pkg_descriptor.bn`);
reflect is force-loaded in every VM driver. 708/709/725/727 flipped to PASS on
all 3 VM modes. (Gap 1 — unqualified `__Package()` in compiled mode — was fixed
earlier, binate `1164ef04`.) Full writeup + commits +
validation in [claude-todo-done.md](claude-todo-done.md). The whole
**VM-package-injection project is now ✅ COMPLETE** — §2b (enumeration replacing
the hardcoded extern table) landed back in Part A (binate `a8ba52f2`) + the
`pkg/std` injection; §2a (bytecode descriptor) landed this session. See
`plan-vm-package-injection.md` for the closeout. **Remaining follow-ups (open,
NOT blockers) — framed as cross-ENVIRONMENT consistency, not "no consumer":**
`__Package()`/`reflect.Package` is the substrate for reflection, dynamic typing /
type assertions, and cross-VM injection (one VM's package into another's), so a
package's reflective surface should be as complete + identical as possible across
native / LLVM / bytecode:
- The `__c_call` `FunctionInfo.Value` parity divergence — next entry below.
- **Globals/Vtables table population** — the bytecode descriptor emits these
  tables EMPTY where native populates them. Filling them (globals → runtime
  `lookupGlobalAddr`; vtables → `vm.IfaceVtables`, via a runtime back-patch like
  Value, since the addresses aren't static symbols) completes cross-environment
  descriptor parity. No test pins it yet.

### 🏷[BUG-BASH 2026-06-27 → LANE 3] FULL-descriptor follow-up: bytecode `FunctionInfo.Value` is null for an EXPORTED direct-`__c_call` function in a LOWERED package (cross-mode parity vs native) — 🟡 OPEN (latent, MAJOR-class, surfaced by FULL adversarial review)
- **Symptom**: the VM's bytecode-descriptor gather (`gatherPackageFuncs`,
  `pkg/binate/vm/lower_pkg_descriptor.bn`) selects exported / struct-dtor
  functions the same way codegen's `collectPackageFuncs` does (so the table
  COUNT + Name/RetbufSize/ParamSlots/Sig match native). But the callable-`Value`
  back-patch (`patchFuncValueHandles`) resolves the handle via `funcIndexLookup`,
  which fails for a function that wasn't lowered to a VMFunc — i.e. one with a
  DIRECT `OP_C_CALL` body (`funcHasCCall` skips it in `LowerModule`). Such an
  entry keeps `Value == null`, where the NATIVE descriptor emits a callable
  `@__handle`. Cross-mode parity divergence on `Value` (Name/sig/sizes still match).
- **Reachability (narrow / latent)**: requires an **exported** function with a
  **direct** `__c_call` in a package that is **lowered** (not injected) — i.e. a
  native-only package under `--test` — AND something reflecting that entry's
  `Value`. No current test exercises it (725/727's `pkg/fns`/`pkg/sigs` have no
  `__c_call` exports; unexported C wrappers like `os.cLseek` aren't in the table;
  exported wrappers like `os.Seek` call the wrapper rather than `__c_call`
  directly, so they DO lower and get a handle). Not memory-unsafe — null is a
  defined "not callable from bytecode" state, never deref'd on the lowering path.
- **Root cause**: a bytecode package genuinely has no bytecode handle for a
  `__c_call` function (it can only run natively). Full parity needs the VM to
  bind the function's NATIVE handle into `Value` — the cross-mode handle
  resolution that is Part 2b territory (`RegisterPackageFunctions` enumeration).
- **Options for owner**: (a) accept null `Value` for these (the honest
  "no-bytecode-handle" state) and document; (b) bind the native extern handle
  (2b-adjacent); (c) something else. NOTE: do NOT "fix" by skipping `__c_call`
  funcs in the gather — that makes the table COUNT diverge from native, a worse
  drift. **Add a covering test** once the direction is chosen (an exported
  direct-`__c_call` function in a lowered package, reflecting its `Value`).

## MAJOR

## CR-2 review — carried-forward open residues (2026-06-08/09)

The CR-2 Plan-1 / Round-2 / follow-up-batch adversarial-review records (resolved + refuted
findings) are archived in [claude-todo-done.md](claude-todo-done.md); each resolved finding
also has its own dedicated RESOLVED entry there, and the records preserve the
REFUTED-do-not-re-chase verdicts. These are the still-open residues kept here for tracking:

### 🏷[BUG-BASH 2026-06-27 → LANE 1] Alias receivers unsupported for METHOD VALUES and IMPL declarations — ✅ DECIDED (2026-06-28): un-park, fix now (full cross-layer) — 🔴 OPEN (fix-now list)
- **Method values** (`type AB = @Box; var mv = ab.getV` → "undefined: getV"): the method-value path in `check_expr_access.bn` calls `ReceiverBaseNamed()` on the un-alias-peeled `origXt`. A DIRECT method value (`p.getV`) works; only the alias receiver is broken.
- **Impl declarations** (`type AB = *Box; impl AB : Getter` → "impl receiver must be (a wrapper around) a named type"): `checkImplSatisfaction` (`check_impl.bn`) calls `ReceiverBaseNamed()` on the possibly-`TYP_ALIAS` `recv`.
- **DECISION (2026-06-28, designer): un-park and fix.** The 2026-06-09 parking was explicitly TEMPORARY; ~3 weeks on, do the proper cross-layer fix now. The type-only fix (peel the alias) makes both type-check but SIGSEGVs because `gen_method_value.bn`'s closure layout + impl/vtable dispatch don't peel the alias. Fix = peel `TYP_ALIAS` in BOTH the checker (method-value path + `checkImplSatisfaction`, prototyped earlier) AND IR-gen (closure-capture layout in `gen_method_value.bn` + impl/vtable dispatch), so it type-checks AND runs. + conformance tests: alias-recv method-value runs correctly, alias impl dispatches. Cross-layer (front-end + IR) — riskiest remaining fix-now item.

### CR-2 review coverage gaps (low priority — add tests) — 🟡 OPEN
- **R2-D7**: no readonly/alias-wrapped named-int or named-float-minus test.
- **R2-D5**: the method-value/alias matrix covers only `type AB = @Box` (not alias-over-readonly / value-receiver alias).
- **R2-D4**: only the managed `readonly @Iface` construct is un-xfailed (no `readonly *Iface`, no return/arg-pass position).
- **A1**: no float-scalar / named-sub-word / box-in-loop `box` test.
- **CR-2 Plan-1 coverage-only**: 659 omits raw-pointer-index compound-shift (`p[i] <<=`) and signed `>>=` overshift on non-IDENT lvalues; the genShortVar nameless `multiReturnFieldTypes` fallback has no IR-gen unit test / no managed-component func-value `:=` cell; Defect-2b raw-pointer & value-receiver reject rows have no conformance/unit coverage.

## CRITICAL

### 🏷[BUG-BASH 2026-06-27 → LANE 3] abi-matrix multi-return-through-dispatch cells lack a managed-component type — 🟡 OPEN
- **Coverage gap (residual of the `=`-multibind fix, full diagnosis archived in claude-todo-done.md).** The `=`/`:=` × {direct, iface-dispatch, func-value} multi-return abi-matrix cells (`conformance/matrix/abi/*multi-return*`) all use value-only component types — `MR_TYPES = {"int","u16","f64"}` in `conformance/gen-abi-matrix.py`. None binds a managed component (`@[]T` / `@T`), which is exactly the surface that hid the original mistyping bug (a managed component skipped its Axiom-3 copy-RefInc). 
- The managed-through-dispatch path is currently covered only at the IR-unit level (`gen_assign_multi_test.bn` TestMultiAssignFuncValueCallCopyRefInc), not end-to-end in conformance.
- **TODO**: extend `gen-abi-matrix.py` with a managed-component type for the multi-return-through-dispatch cells (both `:=` and `=` forms), regenerate the matrix, and confirm the 200k-iter-style refcount balance holds end-to-end.

### bnc IR-gen — remaining super-linear factors (perf, for very large programs) — 🟡 OPEN
The minbasic OOM that motivated this is FIXED (fix (1) — O(1) dtor-dedup, binate
`7804c287`; minbasic now ~1 s / 27 MB, was >8.5 GB / OOM).  Full diagnosis
archived in [claude-todo-done.md](claude-todo-done.md).  These secondary
super-linear factors remain — none blocks correctness, but they bite
even-larger programs (the unifying disease: no memoization on the `@types.Type`
node + module-global accumulators scanned/re-mangled linearly):
- **(2) memoize `@types.Type` queries** — add cache slots to `@types.Type`
  (`types.bni`) and memoize `NeedsDestruction` + `SizeOf`/`AlignOf`/`FieldOffset`
  + the dtor/copy name (layout is fixed within a compile); today each is
  recomputed at every emit-site.
- **(3) capacity-doubling `slices.Append`** — it does `make_slice(n+1)` +
  copy-all per append → O(n²) for the hot IR-gen accumulators
  (`pendingStructDtors`, `ctx.Temps`, `ctx.Vars`, return `vals`); give it
  amortized-O(1) growth or use growable buffers for those.
- **(4) compact per-function managed-cleanup list** — `emitDecForManagedLocals`
  re-scans ALL `ctx.Vars` at each scope-exit; track cleanup slots in a compact
  per-function list instead.
- Minor: `resolveTypeExpr` allocates a fresh `@Type` per occurrence (no
  interning); `lookupFuncParams`/`collectFuncStrings` do O(n) linear scans.

### Differential scalar harness (`matrix/scalar-diff`) — re-evaluate native-x64 / arm32-linux on an x64 host — 🟡 OPEN (low priority)
The harness (v1 + v2) and every backend defect it found are done (archived in
[claude-todo-done.md](claude-todo-done.md): `vm-int-to-float32` `289420b6`, `vm-float32-to-unsigned`
`3fd7e712`, `aa64-subword` `5f94558b`; scalar-diff has 0 xfails now). Remaining: native-x64 and
arm32-linux were never evaluated on this host (no x86_64 C runtime headers → uniform COMPILE_ERROR;
arm32-linux needs `qemu-arm`). Re-check on an x64 host — the aa64 sub-word defect very likely has an
x64 analog needing its own xfails.

### Audit the home of generic low-level helpers shared by cmd/bni + the REPL engine (low priority / code-org)
- **Context**: extracting the REPL engine to `pkg/binate/repl` (Stage 4c
  of `plan-repl-embeddable.md`) needs generic helpers that ALSO stay in
  cmd/bni: `streq`, `appendCharSlice`, `appendFilePtr`, `appendImportSpec`,
  `readFile`, `quotePath` (+ the IR-gen import-registration subtree
  `registerPkgImports`/`registerMainImports`/`loadBuiltinBNIs`/
  `ensureBootstrapLoaded`/`addLoaderPaths`).  For 4c these are
  **DUPLICATED** (each package keeps its own copy) to avoid a weird
  dependency (runProgram/runTests pulling in `pkg/binate/repl` just for
  `streq`).  `pkg/binate/buf` is the WRONG home (it owns CharBuf/CopyStr;
  `readFile`/`quotePath` don't belong there).
- **What to audit**: where these generic string / slice / file / IR-gen
  helpers SHOULD live long-term.  Survey the codebase for the real
  commonalities (who needs `streq`, `readFile`, the import-registration
  helpers?) and decide: a genuinely-shared tier-2 package (a possibly-
  uselessly-named `pkg/binate/utils`? a split between string-utils /
  file-utils / ir-import-helpers?), vs leaving the small ones duplicated.
  Consolidate the 4c duplicates once decided.

---

## MINOR

### 🏷[BUG-BASH 2026-06-27 → LANE 2] Big-endian CODEGEN — deferred (no BE target exists yet) — 🟡 DEFERRED
The Ch.7.13 layout follow-ups (`type.layout.funcval-order-hardening` + the
`type.layout.byte-order` decision / `TargetInfo.BigEndian` field + little-endian-only
assert) are ✅ DONE & LANDED — see [claude-todo-done.md](claude-todo-done.md). What
remains: actual big-endian byte-EMISSION (object writers, `ir.DataGlobal` int terms,
`bit_cast` / the representation builtins) for a future big-endian / cross-endian
target. `SetTarget` currently `panic`s on a big-endian target, so there is no
silent-wrong-code risk meanwhile; do this when such a target is actually needed.

### Spec Ch.16 (Packages) — adversarial-review follow-ups (test-quality, non-blocking) — 2026-06-19
The Ch.16 review found 0 blockers, 7 should-fix (landed tests work; these
improve rigor). 015 mis-cite already FIXED (re-cited pkg.resolve→pkg.identity).
Remaining, for a focused follow-up (with the build-constraint rework below):
- **Harness limit (root cause of 2 findings):** the runner gives a test ONE
  search root, so `pkg.resolve.public` (013, public-vs-local under DIFFERENT
  roots) and `pkg.resolve`'s independent-.bni/impl-roots facet (012) can't be
  exercised — both tests only show "resolves under one root". Soften their
  comments to not overclaim; the multi-root facets need a harness extension (a
  second `--prepend` root) — note in Annex C as untested.
- **Vacuity to tighten:** 050 (`pkg.identity`) asserts values, not type-
  distinctness — the distinctness is actually pinned by 051's cross-pkg-assign
  reject; re-scope 050's comment. 091 (`pkg.extern` var) only reads once — make
  var-ness load-bearing (mutate via a setter, observe). 090 extern-func is the
  same shape as a normal exported func (inherent).
- **Missing coverage:** `pkg.bni.consistency` only tests return/var-type
  mismatch (033/034) — add param-type + param-count + result-count mismatch.
  `pkg.bni` (032) omits the opaque-type and interface/impl .bni decl kinds.
  `pkg.ccall` (092) has no C-ABI-passability reject test (§16.9). `pkg.clause`
  (010) and `pkg.import` (001) lack negative tests (package-must-be-a-string-
  literal; no block-scoped import).

### Spec Ch.16 (Packages) — build-constraint group needs rework + a possible gap — 2026-06-19
Ch.16 landed at **21/22 rules** (`spec/16-packages/`, binate `f7ed4eb4`):
imports / bni / identity / extern groups are green (compiler/VM/gen1/gen2/
native_aa64). The **build-constraint group** (the `#[build(EXPR)]` rules) was
authored by a fan-out agent on a wrong "gating-active by default + decl-level
gating + predicate-validation-errors" assumption; 8 of its tests failed and were
removed. The real mechanism (per `conformance/737_build_import_select`,
`747_err_build_bni_dropped`) gates whole FILES (via the package clause) and
IMPORTS by arch with `#[build(is(arch, …))]`, not individual decls. **Follow-up
(focused):** re-author the build-constraint tests on the real mechanism, which
restores the lone GAP **`pkg.build.errors`** (the Constraint: a false constraint
on a *required* element is an error). Surviving build tests: `070_annotation_
namespace`, `071_annotation_degenerate`, `072_err_annotation_no_stack`.
  - **Possible real gap to confirm during that rework:** the agent's
    `#[build(<unknown-predicate>)]` and `#[build]` with an unknown annotation
    name **compiled and ran** (printed `0`) instead of erroring — `pkg.build.errors`
    / `pkg.annotation.namespace` say these should be rejected. Either the tests
    were malformed (wrong gating context, so the annotation was never validated)
    or build-constraint validation doesn't fire — determine which.

### Issues surfaced authoring spec Ch.8 conformance tests — 2026-06-19
Found writing the `conformance/spec/08-conversions/` rule tests (plan-spec-
tests.md Phase B). Ch.8 itself is clean (11 tests, 100%, green on compiler /
VM / gen1 / gen2 / native_aa64 / arm32_baremetal). Three findings:
- 🏷[BUG-BASH 2026-06-27 → LANE 3 🤝] **`bit_cast` to a sub-word type isn't narrowed — VM facet ✅ FIXED (binate `8e09e808`); native backends ✅ FIXED (LANE 2, main `b3d451a4`).** `bit_cast(uint8, <int8 -1>)`
  used directly (no intervening typed store) should be `255`, but stayed
  sign-extended on the bytecode VM (all 3 `-int` modes) and on native_aa64 (the
  LLVM compiler narrows correctly; a `var r uint8 = bit_cast(...)` store also
  narrows). This is the **`bit_cast` facet** of the sub-word-narrowing gap
  (claude-todo `aa64-subword`). **VM fixed:** OP_BIT_CAST to a sub-word integer
  now lowers to BC_ZEXT/BC_SEXT (see the pinned `040_bit_cast_int_reinterpret`
  entry under the Ch.15 findings above — its `-int` xfails are gone). **Native ✅ FIXED (main `b3d451a4`):** both native emitters now call
  `emitSubWordNarrow` after the bit_cast MOV (the shared `common.SubWordNarrow`
  classifier the VM uses), so a sub-word target lands in its value range;
  `040`'s native_aa64 xfail removed, and x64 — which had the identical gap —
  fixed too (verified native_aa64 + native_x64_darwin). The conv.bit-cast *rule* itself is satisfied (covered by
  `spec/08-conversions/010_bit_cast`).
- 🏷[BUG-BASH 2026-06-27 → LANE 1] **distinct same-width integer types implicitly inter-convert (`int ↔ int64`, …) — ✅ DONE & LANDED (main `2834c57b`, 2026-06-29).** `var y int64 = x` (`x int`) and the
  whole int↔int64 family are now rejected (decision (b): keep `Identical` loose
  for layout, add a name-aware gate). New `distinctNamedInts(a,b)` predicate gates
  `AssignableTo` (assignment + comparison) AND `commonType` (arithmetic + bitwise,
  added after the AssignableTo-only gate was found to leave `anInt + anInt64`
  accepted), so int↔int64 is rejected in every scalar context; untyped constants
  still coerce. **The predicted "broad blast radius / sweep" did NOT materialize:**
  the codebase was already int/intN-clean — gen1 recompiled the whole compiler
  (builder-comp-comp 2503/0) and the VM (builder-comp-int 2478/0) with ZERO new
  failures, no sweep. Tests: `spec/07-types/026` (reject in arith/comparison/
  assignment), `027` (casts bridge), `023` comment corrected. **Caveat:** the
  separate `examples/` repo (6/74 files use sized ints) was NOT verified against
  the gate (BUILDER-injected build); any breakage there is a separate-repo
  follow-up, not from this landing. Full write-up in
  [claude-todo-done.md](claude-todo-done.md).
- **§8.5 "Open (precision residual)" note appears STALE.** The note says a
  constant ≥ 2^63 reached through a bitwise/shift op "is not yet rejected":
  `cast(int64, 0x4000000000000000 << 1)`. That exact example — and `cast(int64,
  1 << 63)` — now **reject** ("constant does not fit the cast target type"). The
  bitwise-const fold may have been fixed; verify (other patterns?) and, if so,
  drop the §8.5 residual note (like the Ch.13 generic-unparsed/d4-paren stale
  notes). No born-stale xfail added (rejection is the correct behavior).
- **§8.5 "Open (precision residual)" note appears STALE.** The note says a
  constant ≥ 2^63 reached through a bitwise/shift op "is not yet rejected":
  `cast(int64, 0x4000000000000000 << 1)`. That exact example — and `cast(int64,
  1 << 63)` — now **reject** ("constant does not fit the cast target type"). The
  bitwise-const fold may have been fixed; verify (other patterns?) and, if so,
  drop the §8.5 residual note (like the Ch.13 generic-unparsed/d4-paren stale
  notes). No born-stale xfail added (rejection is the correct behavior).

### Issues surfaced authoring spec Ch.13 conformance tests — 2026-06-18
Found writing the `conformance/spec/13-expressions/` rule tests (plan-spec-
tests.md Phase B). Each has a reproducing test cited by `.rules`.
- **Two stale composite-literal "known defect" notes — both ✅ CORRECTED in the
  spec (docs `2389676`, `2f95afc`).** `--check-xpass` flagged the first as a
  born-stale xfail; probing then showed the second is also fixed.
  - `expr.composite.generic-unparsed`: generic-instantiated literal heads
    `Box[int]{…}` ARE built + instantiated (var-decl, `:=`, call-arg,
    multi-type-arg) — `spec/13-expressions/032`, a passing positive test.
  - `expr.disambiguation.d4-paren`: the parenthesized escape WORKS —
    `(Point{…}).x` in an `if`/`for` condition (`spec/13-expressions/042`). The
    base D4 rule (an UN-parenthesized literal in a condition is not recognized,
    so `if Point{…}.x` fails) is correct/intended, not a defect.
  Both, plus `expr.composite.array.indexed` and `…inferred-len`, are now
  declared col-0 rule-IDs (tests cite them precisely; Ch.13 denominator 29→32).
- 🏷[BUG-BASH 2026-06-27 → LANE 1] **`expr.composite.array.inferred-len` — ✅ DONE & LANDED (main `135ea813`, 2026-06-29).** `[...]T{…}`
  inferred-length array literals implemented (literal-head only, Go's rule). See
  the detailed entry above and [claude-todo-done.md](claude-todo-done.md).
- ✅ **FIXED & LANDED (binate `7523b14d`, BUG-BASH LANE 1) — (minor) `expr.composite.struct` bad-key diagnostic.** A keyed struct
  literal whose key names no field now reports `no field \`<key>\` in <T>`
  (errNoSuchField) instead of the generic `undefined: <key>`. 027's `.error`
  tightened to require the field-specific form.
- **(note, non-defect) `expr.compare.relational` chain diagnostic reach.**
  `a < b < c` is correctly rejected in every context, but the dedicated
  "comparison operators do not chain" message fires only for the
  identifier-leading for-clause Pratt path (`parse_for.bn:199`); `if`/`var`/
  literal-leading contexts reject via generic parse errors. Conformant
  (rejection holds) — a diagnostic-consistency nicety only.

### Lower the file-length `.bni` cap toward 1000/1200 — 🟡 OPEN
- **Residual** of the (now-archived) "Extend hygiene checks to scan `ifaces/`+`impls/`" work. The `.bni` file-length cap is currently 1500/1800 (warn/error); consider lowering toward 1000/1200.
- **Blocker**: `pkg/binate/ir.bni` (~1183 lines) exceeds the proposed lower cap and would need refactoring (split into sub-interfaces) first. A live `TODO` in `scripts/hygiene/file-length.sh` tracks this.
- (Full resolved diagnosis of the ifaces/impls hygiene-scan extension archived in claude-todo-done.md.)

## MAJOR

### 🏷[BUG-BASH 2026-06-27 → LANE 2] NEEDS-INVESTIGATION — `types.GetTarget().IntSize` reads stale/unset in the native function-lowering emit phase — FILED 2026-06-29
- **✅ DONE & LANDED (main `94f0268f`)** for the refcount fix itself: parameterized via `types.ManagedHeaderSize()` (= 2*ptrSize) — `hdrBytes`; `wordBytes = hdrBytes/2`; `rcW = wordBytes==8`; `signBit = wordBytes*8-1`. Pointer ops (CBZ, the address SUB) stay 64-bit (a 32-bit pointer is zero-extended); only the refcount VALUE ops track the word width. No-op on LP64; verified by the native refcount unit test + 603 refcount-heavy native_aa64 conformance tests, 0 failures (incl. 489/617, which an initial `GetTarget().IntSize` attempt had broken). The arm64_32 path is correct-by-construction but untestable.
- **The footgun (NEEDS-INVESTIGATION)**: during that fix, `types.GetTarget().IntSize` read at function-body instruction lowering (`aarch64_refcount.bn`) returned a WRONG value — not the live target's int size — silently mis-emitting the refcount load offset/width. The SAME expression in `*_pkg_descriptor.bn` (DESCRIPTOR phase) IS correct (its reflect tests pass). `ptrSize()` (= `target.PointerSize`) is reliable in BOTH phases. So at function-lowering time `target.PointerSize` is live but `target.IntSize` is not (or GetTarget returns a stale copy).
- **Why it matters**: a latent footgun — any native function-lowering code reading `GetTarget().IntSize` for layout is silently wrong on a target where it isn't pre-set. Contained today (the only other native `GetTarget().IntSize` uses are the `__Package` accessor, descriptor phase, safe). 
- **Needs**: root-cause why `target.IntSize` is unset/stale at function-lowering time while `PointerSize` is live (init / `SetTarget` ordering in the native pipeline) — then either make IntSize live there, or adopt the convention: use `ptrSize()`/`ManagedHeaderSize()` (NOT `GetTarget().IntSize`) for layout in that phase. (On all current ABIs int==pointer, so PointerSize is a correct layout substitute.)

### Add a hygiene check enforcing package-tier dependency rules (`pkg-layout-spec.md`) — bundled tiers must not import non-bundled tiers — FILED 2026-06-10
- **What**: a `scripts/hygiene/` check that statically validates every package's import closure against the tier ordering in `pkg-layout-spec.md` ("Tiers"). A package must not import a *less-bundled* (higher-numbered) tier. Concretely — tier 0/0b/1/1x packages (always- or by-default-bundled: `pkg/builtins/*`, `pkg/std/*`, `pkg/stdx/*`) must NOT import a tier-2/3 package (project-pulled / not bundled: `pkg/binate/*` and any other `pkg/<org>/*`). Also enforce the tier-2 transitive-closure rule (`pkg-layout-spec.md` "Tiers": tier 2's dependency closure must itself be tier 2). Tier is derivable from the import-path prefix (`pkg/builtins/`→0/0b, `pkg/std/`→1, `pkg/stdx/`→1x, `pkg/binate/` & other `pkg/<org>/`→2); `pkg/bootstrap` is a bundled runtime primitive (treat as tier-0-equivalent). EXEMPT `*_test.bn` — tests aren't bundled (e.g. `lang_test.bn` legitimately imports `pkg/binate/buf`).
- **Why**: a bundled package whose dependency closure escapes the bundled tiers silently breaks the bundle — the dependency's source isn't shipped, so a consumer compiling against the bundle gets `package "<dep>" not found`. NOTHING currently catches this: it only manifests when a consumer compiles the offending package from a real bundle (`make-bundle.sh` output), which no CI / hygiene / conformance step does today.
- **Motivating bug (discovery 2026-06-10, release-prep for `bnc-0.0.8`)**: `pkg/builtins/lang` (tier 0, always bundled) imported `pkg/binate/buf` (tier 2) for two `buf.CopyStr("true"/"false")` calls in `bool.String()`. The bundle ships only `lib/pkg/bootstrap`, not `pkg/binate/buf`, so the tier-0 `Stringer` carve-out (`var s *lang.Stringer = &x; s.String()`) failed to compile from ANY bundle with `package "pkg/binate/buf" not found` — present since `bnc-0.0.7`, undetected because the carve-out smoke step (`release-process.md` step 5) had never actually been run against a real bundle. Fixed in binate `84818a77` (lang returns bare string literals; `[N]readonly char → @[]char` is a literal-init allocate+copy). This check would have caught it at the `import` line.
- **Scope note**: adding the check ≠ wiring it into `scripts/hygiene/run.sh` / CI — but a hygiene check belongs in the run.sh master, so do both when implementing. A first audit may surface other pre-existing violations to triage.
- **First manual sweep (Lane C, 2026-06-10) — CLEAN baseline**: swept every import (incl. aliased) in the bundled trees (`ifaces/{core,stdlib}`, `impls/{core,stdlib}`, `pkg/bootstrap`, `runtime/`). No non-test bundled package imports outside the bundled set. Two non-obvious cases the eventual check must handle: (1) `impls/core/baremetal/pkg/builtins/rt` imports `pkg/semihost`, which is NOT a violation — `pkg/semihost.bni` ships under `runtime/baremetal_arm32/` (a bundled runtime component) and resolves under the arm32-baremetal build's own `-I`/`-L`; the check should treat shipped `runtime/<target>/pkg/*` as bundled, or scope tier rules per build target. (2) all `pkg/builtins/testing` imports are in `*_test.bn` (already EXEMPT) and it has a bundled `.bni` with a harness-provided impl. So `lang → pkg/binate/buf` (binate `84818a77`) was the only true tier-0→tier-2 violation; the baseline is otherwise clean.

### `==` / `!=` (and relational) on aggregates — residual (generic re-check corner cases) — 🟢 LOW
The `==`/`!=`/relational aggregate story is ✅ DONE & LANDED — checker rejection
(binate `60719e01`), struct/array implementation (920a, main `f99f4a4e`),
generic-function path (920b, `6b748a24`), the sentinel-comparison decision, and the
generic-aggregate-field re-check (main `076eb525`); full arc archived in
[claude-todo-done.md](claude-todo-done.md). Two small, documented residuals in the
generic instantiation re-check remain (neither a regression, neither a miscompile):
- **(a) Order-dependent** — a forward-ref instantiation checked BEFORE the generic's
  body is type-checked falls back to the loud IR-gen error (never a silent
  miscompile, never a false reject). A fully order-independent version needs a
  checker sub-pass or an explicit `comparable` constraint.
- **(b) Generic-TYPE methods** — the re-check covers generic FUNCTIONS, not yet the
  rarer generic-TYPE-method comparison sub-case (`checkInstantiationConstraints`
  iterates AST type-params, not the `@Type` the inferred-comparable flag lives on,
  and the method-body-check timing differs).

### Collapse `pkg/bootstrap` onto `#[build]` — 🟡 OPEN (next, per user 2026-06-19)
With BUILDER at `bnc-0.0.9` (both `bnc` and `bnlint` parse `#[build]`), `pkg/bootstrap` — whose
per-target variants are currently PATH-selected and which lives in cmd/bnc's BUILDER-compiled
tree — can be collapsed onto `#[build(...)]`-gated declarations, the same way `pkg/builtins/build`
was. See [`plan-impls-constraints-migration.md`](plan-impls-constraints-migration.md). (This was
the "bonus" of the build.bni-dedup workaround removal, now landed — binate `9c2ac789`, archived in
[claude-todo-done.md](claude-todo-done.md).)

### Remove the BUILDER-lag lint skips after a BUILDER bump — 🟡 OPEN (narrowed to `pkg/binate/interp`; gated on next BUILDER bump)
`scripts/hygiene/lint.sh`'s `LINT_SKIP` group (A) is the BUILDER-lag set — packages the bundled
bnlint can't typecheck because they use a feature/fix newer than the bundle.

**The bnc-0.0.9 lag is CLEARED** (BUILDER is now `bnc-0.0.10`, checked 2026-06-29). `pkg/builtins/rt`
(the `"void"` `__c_call` spelling) and `pkg/std/os` (the `.bni` free-function-vs-method fix
`796effc7`), plus their importer chain `pkg/binate/{vm,repl}` + `cmd/{bni,bnas,bnlint}`, all lint
**clean** under the bnc-0.0.10 bundled bnlint (verified each directly). Dropped from `LINT_SKIP` —
restoring style-lint coverage on those seven packages, hygiene 15/15 — in `binate` lint.sh change
`c5a14146`.

**Still skipped — `pkg/binate/interp`**, but for a *newer* lag (not the rt/os one). **Root-caused
(2026-06-30): a synthesized-accessor NAME skew, not a missing bnlint capability — so the next bump
fixes it and NO linter work is needed.** The compiler-synthesized reflect accessor was renamed
`_Package` → `__Package` in `e12a8a3b` ("fix CRITICAL … close silent collision", 2026-06-26), which
postdates the bnc-0.0.10 release (`cdea9b9f`, 2026-06-23). interp's extern-registration references the
new name as a func value (`rt.__Package`, `reflect.__Package`, `errors.__Package`, …), but the bundled
bnc-0.0.10 checker still synthesizes/resolves the OLD `_Package` (verified: `emit_pkg_descriptor.bn`
mangles `"_Package"` at cdea9b9f, `"__Package"` at HEAD), so `<pkg>.__Package` is undefined under the
bundle — cascading to all four errors (`undefined: __Package` → `cannot call non-function` → `cannot
assign void to @Package` → `_func_handle argument must be a named function`). A current-source
(post-rename) bnlint lints interp clean. Action: at the next BUILDER bump (source ≥ `e12a8a3b`), drop
`pkg/binate/interp` from `LINT_SKIP` and close this entry. (The `asm/*` skips are a separate group (B)
— real `[managed-to-raw-assign]` findings — not this entry.)

### `rt.Exit` paradigm: `exit` vs `abort`/`panic` — DISCUSS
- `rt.Exit` (→ libc `exit`) is the wrong model in general: process exit
  is meaningless in an embedded/freestanding environment, and the
  runtime mostly invokes it for *abort* conditions (OOM, bounds-fail,
  refcount corruption). `abort`/`panic` is likely the right paradigm.
- Surfaced 2026-06-03 alongside the `__c_call`/drop-libc work; that
  change preserves `Exit`→`exit` behavior, so this is a clean,
  independent follow-up. Needs a design discussion before any change.

### Inject `pkg/bootstrap` into the VM + convert I/O to `__c_call` — Phase 1 DONE; Phase 2 DEFERRED (BUILDER-runtime coupling)
- **Phase 1 LANDED** on main (`a7fabc7a`, 2026-06-03): bootstrap is now
  native-only in the VM — cmd/bni skips lowering it, the format helpers
  (formatInt/Int64/Uint/Bool/Float, Itoa) are registered as externs in
  both `registerBootstrapExterns` copies, bootstrap's bytecode unit tests
  are xfailed in the 3 `-int` modes, and `extern_register_std_test` guards
  format-helper registration.  `formatFloat` (the first native float
  extern) dispatches via the all-int shim ABI (`7abc3809`).  Verified:
  `287_float_println` green in `-int`; full `builder-comp-int` /
  `-comp-int` / `-int-int` clean but for pre-existing failures.
- **Plan**: [`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md). The
  rt-drop-libc pattern applied to bootstrap: eliminate the hand-written
  `bn_pkg__bootstrap__*` I/O glue in `binate_runtime.c` by converting it
  to `.bn` + `__c_call`, and make bootstrap native-only in the VM.
- **Phase 2 DEFERRED (2026-06-03), possibly indefinitely**: converting
  the I/O to `.bn` *adds* `bn_pkg__bootstrap__{Open,Read,Write,Close,Exit}`
  defs that collide with BUILDER's pinned runtime (gen1 links it,
  `build-compilers.sh:55-62`) → duplicate-symbol link failure building
  gen1. It's a runtime-ABI change, so it can only be done *during a
  BUILDER bump/release* (the new BUILDER's runtime omits the I/O), not in
  the pinned-BUILDER tree. The trivial+moderate `.bn` code was written +
  reviewed (correct modulo the link blocker) and is preserved in
  plan-bootstrap-ccall.md's appendix. `Stat` is a further defer (struct
  stat platform divergence → needs a per-libc-platform impl split). It may
  be better to *eliminate* these bootstrap I/O functions (subsumed by a
  real stdlib `io`) than convert them — so this may never be worth doing.
- **Harder than rt**: `__c_call` is scalar/pointer-only, but bootstrap's
  I/O takes slices + returns managed-slice aggregates → marshalling
  (null-term cstr, data-ptr extraction, aggregate construction). `Args`
  can't be pure `__c_call` (no libc fn returns argv) — a minimal argv
  hook stays in C. Not C-freedom (still links libc syscall wrappers).
- **Needs a BUILDER bump** (the deferral reason above; the original
  "no BUILDER bump" claim was wrong — BUILDER *compiles* `__c_call` fine,
  but its *runtime* still defines the I/O symbols gen1 links). Baremetal
  keeps its semihost impl (per-target, like rt). Filed 2026-06-03.

### Better test-mode/target annotation than `.xfail` (unit + conformance)
- We lean on `.xfail.<mode>` files to mark tests that can't run in a
  given configuration (e.g. `pkg-builtins-rt.xfail.builder-comp-int*`
  because rt is native-only in the VM; the `__c_call` conformance tests
  498/500/527/530 xfailed in every VM-leg mode). But "expected to FAIL"
  is the wrong semantics for "not APPLICABLE here" — these tests are
  *bnc-only* / *vm-only* / *target-specific* by nature, not regressions.
- **Want**: a first-class annotation (in the test source or a manifest)
  declaring a test's applicable modes/targets — `bnc-only`, `vm-only`,
  per-backend, per-target — so the runner *skips* inapplicable configs
  cleanly and reserves `xfail` for genuine known-failures. Would also
  let `__c_call` tests declare "compiled-only" honestly instead of a
  fan of per-mode xfail files.
- Surfaced 2026-06-03 by the drop-libc / native-only-rt work.

### Slim `pkg/bootstrap` and `pkg/libc` by migrating callers OUT
- **What**: rather than converting bootstrap's I/O surface
  in place, migrate callers AWAY from `pkg/bootstrap.X` and
  `pkg/libc.X` toward whatever the long-term replacement is
  (a new I/O package, a slimmer `pkg/std/os`, etc., TBD).
  Goal: shrink the surface of both bootstrap and libc until
  they can either be retired entirely or held as truly minimal
  bootstrap primitives.
- **Approach** (sketch — needs design): identify call sites,
  classify them by what they want (formatted print, file I/O,
  process control, raw libc memops), and route each class to
  the canonical replacement.  bootstrap and libc only get
  what's TRULY platform-essential and inappropriate for any
  higher-level package.
- **Progress**:
  - **libc Memcpy / Memset — DONE 2026-06-02 (binate `87965b70`)**:
    the libc-host rt's MemCopy / MemZero now do pure-Binate byte loops
    (matching the baremetal rt, which already did) and Box copies via
    MemCopy, so both primitives were removed from the whole surface —
    `pkg/libc.bni`, `runtime/libc_stubs.c`, the cmd/bni + vm extern
    registries, and the vestigial baremetal `bn_pkg__libc__*` aliases
    in semihost.s.  No BUILDER bump (gen1 links BUILDER's runtime;
    gen1's outputs emit no `bn_pkg__libc__*` and link checkout's
    runtime).  Verified across compiled / VM / self-hosted / baremetal
    lanes.  Perf footnote: the byte loops are slower than libc
    memcpy/memset at -O0 (no idiom recognition) — accepted for now,
    revisit with a word-at-a-time loop if it shows in profiles.  This
    does NOT touch the C-ABI memcpy/memset LLVM emits for aggregate
    copies (llvm.memcpy intrinsics), which are independent of pkg/libc.
- **Remaining libc surface**: Malloc / Calloc / Free (now the only
  callers; need a real Binate allocator to retire) and Exit (needs a
  process-exit syscall, gated on the C-free syscall story).
  `pkg/bootstrap` — the larger I/O surface — is the next target.
- **`bootstrap.Itoa` — FULLY RETIRED (2026-06-08, `f7966135`).**  Every
  caller migrated, then the function, declaration, tests, baremetal
  duplicate, and VM extern registration all removed.  Now that
  `pkg/std/strconv` has `Itoa(v int)`
  (base 10), `FormatInt(v int64, base)`, and `FormatUint(v uint64, base)`,
  they are the canonical replacement for `bootstrap.Itoa`.  Goal: every
  Tier-1/Tier-2/Tier-3 caller uses strconv instead of bootstrap (a
  sub-step of retiring the bootstrap int-format surface).
  - **The old "BUILDER tree CANNOT import strconv" constraint was wrong /
    is now moot.**  `strconv` (whole package, incl. its `pkg/std/math/big`
    dependency via `ftoa.bn`) is ALREADY in cmd/bnc's BUILDER-compiled
    tree: `pkg/binate/ir/gen_const_fold.bn` and
    `pkg/binate/native/common/common_float.bn` import it, and BUILDER
    compiles them when building gen1.  So BUILDER-surface packages
    (`token`, `native/*`, codegen, ir, …) CAN migrate — verified by
    migrating `token` (gen1 rebuilds clean across builder-comp / -int /
    -comp).  No integer-only strconv subpackage is needed.
  - **`pkg/builtins/lang` (Tier-0 core) — DONE (2026-06-07):** lang can't
    import `strconv` (below Tier 1; layering inversion, and a cycle since
    strconv's closure reaches the builtins), so it got package-internal
    full-width formatters (`formatUint64` / `formatInt64`, mirroring
    `bootstrap.Itoa`'s uint64-magnitude approach incl. the two's-complement
    trick for int64-min).  This also fixed a correctness bug: the impls had
    funnelled through `bootstrap.Itoa(cast(int, x))`, which on 32-bit
    targets TRUNCATED the wide types — `(int64/uint32/uint64).String()`
    were WRONG on ILP32 for values outside int32 range — and mis-signed
    unsigned values ≥ 2^63 on every target.  Each impl now widens
    losslessly (signed → `cast(int64, x)`, unsigned → `cast(uint64, x)`);
    lang keeps `bootstrap` only for `formatFloat`.  Covered by lang_test.bn
    boundary cases (the unsigned ≥ 2^63 ones fail under the old code on a
    64-bit host) and `conformance/653_int_string_width` (width-independent
    output, one .expected for LP64+ILP32; guards the 32-bit truncation
    under the arm32 modes — green on all 64-bit modes locally, arm32 needs
    qemu so it runs in CI).
  - **Conversion discipline for the migration:** route each site by the
    *argument's* type, never by a lossy down-cast — bare `int` →
    `strconv.Itoa`; wider signed → `strconv.FormatInt(cast(int64, x), 10)`;
    unsigned → `strconv.FormatUint(cast(uint64, x), 10)`.
  - **Leave (not formatting calls / separate decisions):** the extern
    registrations that expose `bootstrap.Itoa` to interpreted code
    (`pkg/binate/vm/extern_register_std.bn`, `cmd/bni/externs.bn`) — those
    go when `bootstrap.Itoa` is deleted, not now; the test-runner codegen
    in `cmd/bnc/gen_test_runner.bn` (emits source that calls
    `bootstrap.Itoa`); and `conformance/064_bootstrap_funcs.bn` (tests
    `bootstrap.Itoa` itself).
  - **Progress — all migratable package callers DONE** (2026-06-07; each
    green across builder-comp / -int / -comp, landed on main, one package
    per commit): `token`, `repl`, `native/{x64,aarch64}`, `vm`, `ir`
    (test-only), `lexer` (test-only), `types` (test-only), `lint`
    (test-only), `cmd/bnlint`, `cmd/bni`.  Every arg was a bare `int`, so
    all sites used `strconv.Itoa` directly (no `FormatInt`/`FormatUint`
    needed yet).
  - **Retirement — DONE** (landed in order, each its own commit):
    `gen_test_runner.bn` formats counts via `passed.String()` (`c2aaaabf`,
    relying on [A]); `321` migrated to `total.String()` (`9ba85eec`);
    `conformance/064` retired (`0d7c0501`); the VM extern registration
    dropped from both drivers (`6d2384de`); and finally the definition,
    `.bni` declaration, unit tests, and baremetal duplicate removed
    (`f7966135`).  The bootstrap int-formatting surface used by
    print/println (`formatInt`/`Int64`/`Uint`/`Bool`/`Float`) deliberately
    STAYS — only the standalone allocating `Itoa` is gone.
  - **Done since:** the ad-hoc `intToChars` helpers — the package-scoped
    one in `pkg/binate/ir/gen_func_lit.bn` (3 call sites: `__closure_local_`,
    `__funclit_`, `__mv_local_`) and a duplicate in
    `pkg/binate/vm/func_index_test.bn` — now use `strconv.Itoa` and are
    deleted (2026-06-07).
- **[A] Primitive `.String()` without importing `pkg/builtins/lang` —
  DONE across all execution modes (compiled `37b2ffcc`, VM `487c2d08`).**
  `myInt.String()` resolves AND links/executes with no import in both the
  compiled backends and the bytecode VM; naming the `lang.Stringer`
  interface *type* still requires the import (gated by the type checker).
  Mechanism (reverses the "No auto-import" decision in
  `plan-primitives-impl-interfaces.md`, for methods only): `ensureLangLoaded`
  force-loads lang so its carve-out impls attach `String()`/`Compare()` to
  the global primitive singletons (resolution); `appendLangImport` (a clone
  of `appendBootstrapImport`, added at every `RegisterImports` site with the
  same self-import guard, in BOTH `cmd/bnc/compile_imports.bn` and
  `cmd/bni/irgen.bn`) registers lang's signatures so the cross-package call
  resolves/links.  DCE/baremetal worry is moot (unused impls stripped by
  `--gc-sections`/`-dead_strip`).  Full conformance green in both
  builder-comp (1085) and builder-comp-int (1072).  Covered by conformance
  `654`–`656` (per-type positives) + `658` (negative).
  - **Remaining follow-up — the repl.** The repl has its own import setup
    (`pkg/binate/repl/{ir_imports,session,util}.bn`) not covered by the
    `cmd/bni` change; add `ensureLangLoaded` + `appendLangImport` there so
    `.String()` works at the repl too.  Small, same pattern.
- **[B] Test runners can depend on the stdlib — DONE (2026-06-08,
  `36e979df`).**  The `cmd/bnc --test` runner (`gen_test_runner.bn`,
  compiled by `test.bn`) is parsed *after* typecheck, so a stdlib package
  it imports that no test package pulls in was never loaded → not compiled
  → wouldn't link.  Fix: `genTestRunner` declares its stdlib deps in
  `testRunnerStdlibImports()`, and `test.bn` force-loads that list before
  typecheck (the compile loop already builds every loaded package, so they
  then link).  Adding the future `pkg/std/os` (for `Args`/`Open` when
  bootstrap I/O migrates) is a one-line addition to that list plus its use
  in the runner.  Exercised end-to-end now by a placeholder: the runner
  imports `pkg/std/errors` and makes one harmless `errors.New` call
  (TODO-marked for removal once a real dep lands) — proven by
  `pkg/binate/buf` (closure `{buf, testing}` excludes errors) whose test
  binary links the errors-importing runner only via the force-load.  The
  whole unit-test suite now exercises [B].  (The VM `-int` path is
  unaffected — `cmd/bni` executes tests directly, no generated runner; a
  future VM stdlib dep would be force-loaded there the same way as
  bootstrap/lang.)  Distinct from [A], which force-loaded lang to make
  `bootstrap.Itoa` removable.
- **Why migrate OUT rather than convert in place (do NOT re-attempt the
  in-place shape)**: in-place renames of packages whose surface is
  declared-only and resolved by C symbols (`pkg/libc`, and the I/O side
  of `pkg/bootstrap`) hit a wall that pure-Binate-package renames
  (pkg/rt → pkg/builtins/rt) do not.  The wall: at Stage 1, gen1 is
  linked against BUILDER's bundled `libc_stubs.c` (auto-found next to
  `--runtime`), which only defines symbols under the OLD mangled name
  (e.g. `bn_pkg__libc__Memset`).  Checkout source — now compiling under
  the NEW package name — emits calls to `bn_pkg__builtins__libc__Memset`,
  which is UNRESOLVED at Stage 1's link.  Pure-Binate packages don't hit
  this because the bnc-compiled package provides the NEW-name symbols as
  definitions in its own `.o`; declare-only-via-C packages have no such
  Binate-side definition.  Compat aliases in checkout's `libc_stubs.c`
  don't help — BUILDER's runtime is what Stage 1 links against, not
  checkout's.  Resolving would require either (a) pointing Stage 1's
  `--runtime` at checkout's (build-script surgery), (b) a supplemental
  compat .o via `--link-after-objs` (build-script surgery + new
  artifact), or (c) two release cycles with a transitional bridge —
  none worth the bootstrap migration's payoff.  Migrating callers OUT
  side-steps the whole tangle.
- **Status**: in progress.

### Package descriptors (Phase B) — `__Package()` works in compiled + VM modes (builtins); general Functions-table still future
- **Status**: compiled-mode AND VM-mode `__Package()` landed (binate
  `feadde2c`, VM-mode for the builtin packages).  The general interop
  Functions-table (user packages, auto-enumeration) remains future work.
- **What works (compiled mode)**: every package emits an immortal
  static-managed `reflect.Package` descriptor node + a generated
  `__Package() @reflect.Package` accessor (codegen `emit_pkg_descriptor.bn`,
  via the static-managed emitter).  The type checker synthesizes the
  `__Package` signature at selector resolution (`check_expr_access.bn`
  `packageAccessorType`), IR-gen registers it as an imported extern so calls
  resolve + a `declare` emits (`gen_import.bn`), and `reflect` is force-loaded
  (`ensureReflectLoaded`).  Drives a real immortal node through the compiled
  RefInc/RefDec sentinel end-to-end (see [`plan-static-managed-sentinel.md`]).
- **What works (VM mode, binate `feadde2c`)**: the earlier "Functions-table
  is genuinely required" finding was too pessimistic.  `__Package` is already
  a real exported per-module symbol, and the IR/func-value path already
  mangles a qualified `pkg.__Package` reference to call it — so the only
  blocker was the type checker rejecting `_func_handle(pkg.__Package)` (it's
  compiler-synthesized, not a `SYM_FUNC` in scope).  Two small changes wired
  it: (1) `types/check_builtin.bn` accepts `pkg.__Package` as a `_func_handle`
  argument by name; (2) `vm/extern_register_std.bn`
  `registerPackageDescriptorExterns` binds the builtin packages' `__Package`
  (rt, libc, bootstrap, reflect) as VM externs.  Interpreted `pkg.__Package()`
  now dispatches through the func-value shim to the real accessor, and the
  returned `@reflect.Package` is RefDec-safe via the static-managed sentinel —
  exercising the sentinel end-to-end in interpreted mode too.
- **Coverage**: `conformance/532_reflect_package_accessor`
  (`rt.__Package().Name` → "pkg/builtins/rt") now green in ALL 6 default modes
  (the 3 VM-mode xfails removed).
- **Still future — the general Functions-table**
  ([`notes-package-introspection.md`](notes-package-introspection.md) Phase B):
  `registerPackageDescriptorExterns` is a hand-maintained precursor covering
  only the builtins compiled INTO the host binary (their `__Package` is a real
  symbol the shim can call).  USER packages run as interpreted bytecode and
  have no `__Package` body — those need the real table: codegen emits a
  per-package `Functions` table (name + signature + function-value per
  exported func), and the VM auto-enumerates all packages' tables (the
  cross-package registry, open Q4 in the notes — likely a linker section with
  start/stop symbols) to bind names → function values, replacing the hand-
  maintained `RegisterStandardExterns` entirely.  Then richer type metadata
  (Phase C) for reflection/printing + RTTI for type assertions.
- **Linter caveat (see "bnlint typechecks dependency bodies" + lint-skip
  entries)**: `registerPackageDescriptorExterns` is the first `__Package`
  reference in *linted* source, which the BUILDER-bundled bnlint can't yet
  typecheck — `scripts/hygiene/lint.sh` temporarily skips pkg/binate/vm +
  pkg/binate/repl + cmd/bni until the next BUILDER bump.

### Static-managed sentinel — deferred follow-ups (optimizations, not correctness) — 🟢 LOW
Follow-ups split out of the (now-done) static-managed sentinel landing:
- **String-literal null-backing unification**: can the string-literal
  `backing_refptr = null` immortality trick (`emit.bn`) be unified under the
  negative-refcount sentinel? Representation can plausibly unify; the nil-check
  itself can't be dropped (it guards genuinely-nil `@` values). Repr cleanup.
- **ClosureRec-as-sentinel**: the VM's shared per-callee non-capturing-`@func`
  `ClosureRec` (`vm_exec_funcref.bn`) is a static, never-freed managed object.
  The premature-free CRITICAL was already fixed symmetrically (conformance 528);
  making the shared `ClosureRec` an immortal sentinel would remove per-instance
  refcount churn on a shared singleton. Optimization, not a correctness gap.

### Purely-value const extension (future language direction) — DESIGN, not started
Future direction split out of the (now-resolved) non-int-const mis-emit bug:
allow `const` of certain non-scalar but purely-value types (no storage, no
managed fields). Currently `const` is scalar-only (non-scalar → `errNonScalarConst`,
"use `var readonly`"); no `isPurelyValueType` predicate exists yet. A genuine
language extension, not a bug fix.

### Raw-slice escape: decide whether a BROADER best-effort escape lint is wanted — 🟡 NEEDS DECISION
The original framing ("demote the raw-slice escape TYPE ERROR to a linter rule")
is obsolete: there is NO type-check rejection for raw-slice escape (the checker
never rejected it), and a `raw-slice-return` LINT rule already exists (`lint.bn`,
landed `10d19369`) — but it only covers the `@[]T → *[]T` "drops the managed
wrapper" return case. **Open decision (user):** is a broader best-effort escape
lint wanted (return / store-to-outliving-field / assign-to-global of a raw slice
borrowing a local), or is the current narrow rule + "raw is an opt-in escape
hatch" sufficient (close this out)?

### 🏷[BUG-BASH 2026-06-27 → LANE 3] IR integer constants are host-width `int` (blocks 32-bit-hosted toolchain) — LAYER 1 + 2 (INT64 + FLOAT64) DONE
- **Symptom**: under `builder-comp_arm32_linux` unit tests, `pkg/ir`
  and everything downstream of it (`pkg/native{,/amd64,/arm64,/common}`,
  `pkg/codegen`, `pkg/vm`, `cmd/{bnc,bni,bnas}`) fail to compile for
  arm32 with int-width type errors.  `pkg/ir` is the cascade root.
- **Discovery**: triaging the 14 arm32_linux unit-test failures after
  type-check errors gained source locations (binate `c011827`,
  conformance/494).  With locations on, `pkg/ir`'s only *source* error
  is `gen_util_literals.bn:234` (`intFitsInType` compares against
  `4294967295` > INT32_MAX), and tracing the value upstream shows the
  whole literal path is `int`.
- **Root cause**: the IR stores program integer constants in
  `Instr.IntVal`, typed `int` (`pkg/ir.bni:356`) — host-width.  The
  feeding path (`exprIntLitValue`, `bignumToInt`, `parseIntLit`,
  `EmitConstInt`) is all `int` too.  On a 64-bit host this happens to
  work (it's really storing a 64-bit *bit pattern* — a `uint64`-max
  literal lands as the int64 pattern `-1` and codegen emits it fine).
  On a 32-bit host `int` is 32 bits, so the path neither compiles nor
  can represent a `uint32`/`int64` constant.  Symbol/codegen output
  must not depend on host int width.
- **Severity**: major.  Loud (compile failure) on 32-bit, not a silent
  64-bit-host miscompile — but it blocks the C-free / 32-bit-hosted
  self-hosting goal.  `int64` vs `uint64` for the field is immaterial
  (it's a stored bit pattern reinterpreted by the constant's type);
  `int64` is the minimal-churn choice since the existing range-check /
  negation code is written in signed terms whose bounds fit `int64`.

- **Layer 1 — IR + codegen + native (DONE)**: made the program
  -constant path host-independent.  Landed: binate `879ba38`
  (asm 64-bit immediates: x64 Imm→int64 + Imm64, finished aarch64
  Imm consumers in pkg/asm/parse), `035022c` (IR int64 contract),
  `294b5f0` (wide-constant tests), `075e1f5` (made the int-width
  -assuming bootstrap/vm tests 32-bit compatible).
  - `Instr.IntVal` `int` → `int64`.
  - `exprIntLitValue` / `bignumToInt` return `int64`; `intFitsInType`
    takes `int64`.  (`parseIntLit` stayed host-`int` — a
    non-type-checked fallback; the real path takes the bignum branch.)
  - `EmitConstInt(int)` kept (widens internally) + new
    `EmitConstInt64(int64)` for the literal path.
  - `buf.WriteInt64` added; codegen's OP_CONST_INT emit uses it.
  - `pkg/native/{amd64,arm64}` `emitConstInt64` → `int64`; arm64
    extracts MOVZ/MOVK chunks via int64 shifts.  Fixed a latent bug:
    arm64 `emitConstFloat` did `cast(int, bits)` on a 64-bit IEEE
    pattern (dropped the high word on a 32-bit host) → `cast(int64,…)`.
  - VM boundary: `lower_instr.bn` `bc.Imm = cast(int, instr.IntVal)`
    — lossless on a 64-bit host; the truncation-on-32-bit is what
    Layer 2 addresses.
  - **Result**: all 14 packages in the arm32_linux unit-test set
    compile for arm32 (verified locally; runtime validated by the
    `builder-comp_arm32_linux` CI job).

- **Layer 2 — VM machine word (INT64 PATH DONE)**: `pkg/vm` uses host
  `int` as its universal machine word — registers, immediates,
  pointer arithmetic (`bit_cast(int, frameBase) + instr.Imm`),
  offsets.  So a 32-bit-hosted VM is a 32-bit machine and can't carry
  64-bit immediates.  Open design question (raised by user): can the
  VM keep host-sized words for most values and use 64-bit only when
  necessary?
  - On a 32-bit host the VM interprets 32-bit-*target* bytecode, where
    pointers / `int` / sizes / offsets are all 32-bit by definition —
    so host-word is already correct for the vast majority of values.
    The 64-bit cases are exactly the explicitly-64-bit ones: `int64` /
    `uint64` values and large literals.
  - Two implementations of "64-bit only when necessary":
    (a) uniform 64-bit value slots + width-aware ops — simplest and
    correct; on a 32-bit host it costs 64-bit slot storage and 64-bit
    arithmetic only where the op is 64-bit (the compiler already
    supports `int64` on 32-bit; bytecode is largely typed already).
    (b) host-word slots + 64-bit via register pairs / a parallel wide
    slot, switched by typed opcodes — saves the 32-bit storage but
    complicates the register model and bytecode (must track which
    slots are wide).
  - Recommendation: do (a) first (correctness, minimal model change);
    treat (b)'s host-word-mostly layout as a later 32-bit perf
    refinement, not a correctness prerequisite.
  - **Investigation findings (2026-05-26)**: the change is larger and
    more entangled than the (a)/(b) framing implies — `int` is a
    *single conflated word* across three distinct roles, so it can't
    be swapped to int64 blindly:
    1. **Register slots.** `regs *int`, accessed `regs[i]`.  But
       `pushFrame` already budgets `f.NumRegs * 8` bytes/reg
       (`vm.bn:181`) — 8-byte slots.  On a 64-bit host int==8 so it's
       consistent; **on a 32-bit host this is a latent stride bug**
       (8-byte budget, 4-byte `*int` access → registers alias).  So
       `regs *int → *int64` actually *fixes* this and matches the
       existing layout.
    2. **Host pointers.** Registers also hold host addresses via
       `bit_cast(int, vm.Stack)` / `bit_cast(*uint8, regs[i])`.  With
       int64 regs on a 32-bit host these become a width mismatch
       (host ptr 32-bit, reg 64-bit) — `bit_cast` is illegal
       (size differs); they need explicit widen-on-store /
       truncate-on-read helpers (`ptrToReg` / `regToPtr`).
    3. **Target-memory-structure access.** `bit_cast(*int, hdrPtr)`
       reads managed-slice/refcount headers as `*int`.  These are
       target-word-sized fields; tying their stride to the register
       word is wrong if the two ever differ.  Needs separating
       "VM register word" from "target word".
  - Surface: ~106 `bit_cast(int,…)/(*uint8,…)/(*int,…)` sites across
    vm_exec*.bn + vm.bn, plus `BCInstr.Imm int→int64`, register
    arithmetic, and the memory ops.  This is a multi-step refactor;
    settle the register-word-vs-target-word model before editing.
  - **What landed (int64 path)** — model:
    register == host word; 64-bit values use register pairs; pair ops
    only engage when `REG_SLOT < 8` (no-op on a 64-bit host).
    Pointer-vs-target-word ambiguity stays narrow because `bit_cast`
    sites are at register-vs-pointer boundary — register word stays
    host `int`, so the ~106 `bit_cast` sites are untouched.
    - Step 1 (binate `f7cae70`): `REG_SLOT = sizeof(int)`; register
      area / frame header sized by it.
    - Step 2a (`ca7def6`, `394a16a`, `ca41a75`): `buildSlotMap` /
      `regWidths` / `remapRegisters` — id→slot mapping with the
      audited `BC_RETURN.Dst` exception.
    - Step 3 (`fd3ca06`, `f764a66`, `be877fd`, `60657fd`, `947205f`,
      `ebaa077`): full `BC_*64` handler set — `LOAD_IMM64`, `MOV64`,
      arith / bitwise / shifts / signed+unsigned compares / unary
      (NEG, BITNOT) / casts (WIDEN_S, WIDEN_U, NARROW, MOV64-bitcast)
      / pair memory `LOAD64_PAIR` / `STORE64_PAIR`.  Pure compute
      factored into evalArith64 / evalCmp64 / evalShift64 /
      evalUnary64 / widen64* — host-tested across the tricky cases.
    - Step 4 (`925e9bc`, `949ea29`, `ebaa077`): lowering emits the
      `BC_*64` ops host-word-aware — `OP_CONST_INT`, all binary
      arith / cmp / shift, load/store, casts, NEG/BITNOT.
    - Step 2b (`24a5d67` RETURN64, `7353523` direct CALL,
      `2eaa8f9` indirect/func-value/iface call ABI,
      `11da9d7` multi-return pair-aware): int64 return + call ABI
      complete.  `NumParamSlots` + slot-count `Imm` semantics.
    - Step 6 (`1fd3b9f`): conformance/499 int64 arithmetic E2E.
  - **Float64-on-32-bit (DONE)**: mirrors the int64 pair pattern.
    - `ba1a798`: route the existing `BC_FNEG` / `BC_F*` /
      `BC_SITOF` / `BC_FTOSI` / `BC_F64_TO_F32` / `BC_F32_TO_F64` /
      `OP_CONST_FLOAT` `bit_cast(int, float64)` hops through
      int64 — compile-clean on a 32-bit host without yet changing
      lowering semantics.
    - `3126655`: `BC_F*64` opcode decls (`BC_FNEG64`,
      `BC_FADD64..BC_FDIV64`, `BC_FEQ64..BC_FGE64`) + pure
      `evalFloatArith64` / `evalFloatCmp64` / `evalFloatNeg64`
      helpers in `vm_exec64.bn` + host-testable unit tests for
      each helper.
    - `ae08c1ed`: `execOp64` dispatch glue — joins source pair(s),
      bit_casts through `int64` to `float64` for the compute,
      bit_casts back, splits to dst pair (or single-slot bool for
      compares).  Direct `execOp64(&stackArr[0], instr)` tests
      cover all three shapes (binary arith, unary FNEG, compare-
      writes-single-slot).
    - `00b10e38`: lowering — `lowerBinOp` / `lowerCmpOp` add an
      `isFloatPair` branch alongside the existing `isIntPair`;
      `OP_NEG` dispatches `BC_FNEG64`; `OP_CONST_FLOAT` emits
      `BC_LOAD_IMM64` with `splitInt64` halves when
      `is64BitScalar(instr.Typ) && REG_SLOT < 8`.
    - `769d2e54`: gate test for OP_CONST_FLOAT — confirms 64-bit
      host falls back to `BC_LOAD_IMM` (no spurious pair branch).
  - **REMAINING GAP — int64 side of int↔float CONVERSION casts is NOT
    pair-aware (latent; surfaced 2026-06-12 by the int↔float32 VM-fix
    review).** The "DONE" above covers float *arith/compare* pairs and
    the *float* side of conversions; it does NOT cover an int64/uint64
    operand of a `cast` to/from a float:
    - int→float SOURCE side (`BC_SITOF`/`BC_UITOF`/`BC_SITOF32`/
      `BC_UITOF32`): the handlers read the int source as a single slot
      (`regs[instr.Src1]`) and `lowerCast`'s int→float arm has no
      `is64BitScalar(srcTyp) && REG_SLOT < 8` check, so `cast(float*,
      <int64>)` on a 32-bit host drops the source's high half. (These
      handlers ARE dest-pair-aware for the float64 result — the
      asymmetry is source-only.)
    - float→int DEST side (`BC_FTOSI`/`BC_FTOUI`/`BC_F32TOSI`/
      `BC_F32TOUI`): the handlers write a single dest slot via
      `cast(int, f)` (host int) and `lowerCast`'s float→int arm has no
      `is64BitScalar(dstTyp)` check, so `cast(<int64/uint64>, <float>)`
      on a 32-bit host leaves the dest's high slot stale (and truncates
      through a 32-bit host int). (These handlers ARE source-pair-aware
      for a float64 source — the asymmetry is dest-only.)
    Latent, not a live miscompile: no conformance mode runs the bytecode
    VM on a 32-bit host (the `-int` legs run `bni` natively on the
    64-bit build host; arm32 modes are comp/native, not VM), and the
    arm32 `pkg/vm` unit tests don't exercise int64↔float conversion
    casts. NOT introduced by the int↔float32 fixes (`289420b6`/
    `3fd7e712`) — the new float32 ops faithfully mirror the existing
    single-slot float64 ones. Fix (to land before/with any arm32
    VM-host enablement): add `is64BitScalar` gates in both conversion
    arms of `lowerCast` and pair-aware source/dest handling
    (`joinInt64`/`splitInt64`) in the eight handlers, plus direct
    `execNumericCast` unit tests in `vm_exec64_test.bn` driving a
    pair-wide int64 source and dest.
  - **End-to-end arm32 coverage status (2026-05-28)**:
    - `pkg/vm` source compiles cleanly on arm32 (since `ba1a798`).
    - Conformance `builder-comp_arm32_linux`: green.
    - **pkg/vm unit tests on `builder-comp_arm32_linux`: green**
      (was 16 failures pre-session → 9 → 1 → 0).  The bytecode-VM
      BC_*64 / BC_F*64 dispatch and slot allocation are now fully
      end-to-end-validated on a real 32-bit target — including
      the `TestRepro_StructWithManagedSliceFieldAppend` managed-
      memory path, which surfaced the hardcoded-LP64 managed-
      allocation-header offset that `81d31b7c`'s MANAGED_HDR
      const fixed.
    - The cascade-revealed packages — pkg/{types, codegen,
      native/{common,aarch64,x64}} — are also green on arm32 now
      after the LP64-baked-test cleanup (`11ff9864`, `2d13838d`).
    - Remaining arm32_linux failures (5) are all the int64-min-
      boundary cluster in pkg/{bootstrap,buf,ir} — see the
      "arm32 unit-test cleanup" entry for the bucket.  Unrelated
      to this work.

### `print(42)` and friends: how do primitives implement interfaces? — DESIGN OPEN
- **Problem**: with the current rules, `int` (and other predeclared
  primitives) can't implement interfaces. Methods can only be
  declared on TYP_NAMED types (the receiver lookup in
  `check_decl_func.bn:resolveMethodReceiver` rejects `func (x int)
  ...` because `int` is TYP_INT, not TYP_NAMED). So a user-written
  `printIt(s *Stringer) { ... println(s.String()) }` can't accept
  a literal `42` — the user has to wrap with `type MyInt int` +
  impl, then write `printIt(&MyInt(42))`. That's a lot of
  ceremony for a basic use case.
- **Generics don't help.** A `printIt[T Stringer](t T)` call site
  still requires `T` to satisfy `Stringer`, so `int` would need a
  Stringer impl somewhere — same blocker as the non-generic case.
  Generics solve "extensible dispatch", not "primitives need to
  carry methods."
- **Today's escape**: `println(42)` works only because it's a
  compiler builtin — `bootstrap.println` synthesizes per-type
  formatting at the call site. Not user-extensible. The hack is
  documented as temporary in `feedback_println_hack.md`.
- **Two real options** (discussed 2026-05-07):
  1. **Language-blessed implicit interfaces.** The interface plan
     already lists `any` as a built-in implicit interface and
     reserves the mechanism for "small, closed, language-defined
     set" of others. Add `Stringer` (and possibly `Eq`, `Hash`,
     etc.) to that set — every type, including primitives, gets
     a synthesized impl from the compiler. Then a user-written
     `printIt(s *Stringer)` accepts any value uniformly.
     Cost: every iv gets a real vtable, even for primitives, and
     the language has to define the canonical formatting story
     for each primitive.
  2. **Standard-library carve-out for methods on universe types.**
     Allow a designated package (`pkg/std` or similar) to declare
     `func (x int) String() ...` even though `int` is a universe
     type. The carve-out exists only for the language's own std
     library; user packages still can't extend `int`. Closer to
     Go's `fmt.Println` model. Heavier carve-out but lets the
     std lib look like normal Binate code.
- **Lean (preliminary):** option 1 — the implicit-interface
  mechanism is already the named escape hatch, the formatting
  story for primitives is small + closed, and the result is
  user-extensible (their own types implement Stringer normally).
  But this is a real design call; needs a plan doc before
  shipping.
- **Not blocking**: today's `println(42)` carries the load.
  Revisit when generics land or when a user-written `printIt`-
  style function becomes pressing.

### Use interfaces more (opportunistic)
- **Constraint**: now bounded by `BUILDER_VERSION`-pinned bnc
  rather than the historical bootstrap subset — cmd/bnc no longer
  has to be bootstrap-runnable now that boot mode is gone (binate
  `c1be3cc`, 2026-05-21).  bnc-0.0.1 (the current BUILDER) supports
  interfaces, so anything in cmd/bnc's dep tree is fair game too.
  Generics are NOT in bnc-0.0.1, but interfaces are.
- **Candidates that look natural**: anywhere we currently
  switch on a kind tag with a dispatch table (e.g. opcode
  handlers, AST visitors, asm encoders) is the textbook shape
  where an interface compresses the dispatch.  Print/format
  helpers that take a kind + value pair are another easy lift.
  pkg/ast's tagged-union nodes (DECL_*, EXPR_*, STMT_*, TEXPR_*
  Kind enums + switch-on-Kind in pkg/{parser,types,ir,codegen,
  loader}) is the biggest single target but also the longest
  refactor — touches every layer.
- **How to land**: pick one site per PR, define the interface
  alongside, methodify the concrete types, drop the dispatch
  switch.  Keeps each step small enough that conformance +
  unit-tests stay green.  Mirrors the
  `migrate-to-method-form-opportunistic` pattern from
  `claude-todo-done.md` (DONE 2026-05-13).
- **Recon finding (2026-05-26)**: there is NO clean *small*
  retrofit target.  The candidates above split into two
  unappealing buckets: (a) enum→value lookups (reloc maps,
  opName, the emitInstr op dispatch) where `switch` is genuinely
  the right tool and an interface would mean manufacturing one
  empty marker type per enum value — pure ceremony; and (b)
  monolithic tagged unions (`ast.Stmt`/`Decl`, `ir.Instr`) where
  a real interface means splitting a struct that touches every
  layer.  So "use interfaces more" here is a deliberate design
  choice, not opportunistic cleanup.
- **Landed (2026-05-26): driver `Backend` interface** (binate
  `0ee0faa`, `bda81ca`, `6dacb23`).  The genuinely-valuable use
  found: `cmd/bnc/compile.bn`'s `Backend` interface
  (`compileModule`) with `llvmBackend` / `nativeBackend` impls,
  dispatched via `compileModuleVia`.  This collapsed the
  duplicated driver flow — `compileMainNative` is gone, `main()`
  picks the backend and the LLVM/native paths are unified.
  pkg/native also got an internal arch `Backend`
  (arm64/amd64).  These are the first non-synthetic interface
  users beyond pkg/std's `Stringer`.  NOTE: interface values
  must be constructed from locals, not package globals — `&global`
  iface construction was a codegen bug (now fixed, see
  conformance/495).

### Use `@[]@[]char{...}` composite literals (opportunistic)
- **Constraint**: previously forbidden because bootstrap didn't
  support managed-slice-of-managed-slice composite literals; now
  unlocked everywhere (bnc-0.0.1 supports them).  Mirrors the
  unconstraint situation for `cmd/bnlint`'s tests, which already
  use this shape.
- **Pattern to replace**: a known-fixed-length run of
  `args = appendCharSlice(args, "foo"); args = appendCharSlice(args, "bar"); ...`
  → `var args @[]@[]char = @[]@[]char{"foo", "bar", ...}`.  Same
  shape for `appendRawCharSlice` (since string literals are
  already `*[]const char`).  When the run mixes constants with
  computed values, leave it alone — the literal form only helps
  for known-static sets.
- **Candidates**: argv construction in build scripts (e.g.
  `cmd/bnc/{main,test,compile}.bn` clang-args setup), test
  scaffolding (anywhere a test builds a known `@[]@[]char`
  fixture), and short fixed sets of import paths.
- **Why bother**: cuts line count, removes a runtime O(n²)
  rebuild pattern (each `appendCharSlice` allocates a new
  slice + copies), and matches the language's expressive
  default instead of the bootstrap workaround.

### Use function values to collapse explicit dispatch shims (opportunistic)
- **Constraint**: function values are unlocked now that
  cmd/bnc is no longer bootstrap-bound; bnc-0.0.1 has the
  function-value machinery (see plan-function-values-phase-3
  in `claude-todo-done.md`).
- **Pattern to look for**: places where we route through a
  `kind` int + a per-kind dispatch table, when the data flow
  would be clearer as "the caller hands us the function it
  wants invoked".  Candidates need a closer look before they're
  fully scoped — function-value adoption isn't always a win
  (each call adds an indirect-call overhead), so this is
  selectively-opportunistic, not blanket.
- **How to land**: TBD; needs concrete site survey.

### Expand `pkg/slices` beyond `Append` — opportunistic
- `pkg/slices.Append[T]` is the only generic helper today.  Natural
  additions when call sites demand them (don't add speculatively):
  - `Concat[T](a, b) @[]T` — for the managed-slice + managed-slice
    shape.  `bootstrap.Concat` covers the char-slice case but is
    raw-slice-typed.
  - `Filter[T, P]` / `Map[T, U]` — block on closures or func-value
    params; only worth it once those constraints land properly.
  - `RemoveLast[T](s) @[]T` — `popLoading`-style pattern (rebuild
    minus last occurrence) repeats per element type.
  - Don't pre-add a kitchen-sink set — let the first 2-3 call
    sites pull each helper in.
- **Survey 2026-05-28** of the BUILDER-compilable tree: none of the
  above clears the "2-3+ same-shape sites" bar at the moment.
  Concrete numbers found:
    * `Concat[T]` over two managed slices: 0 sites; the only
      `Concat` callers all funnel through char-specialised
      `bootstrap.Concat`.
    * `Contains[T]`: 4 candidate sites (`containsTypePtr` /
      `containsName` / `containsPkgName` / `containsStr`) but each
      uses a different equality (Identical / charEq / streq), so
      collapsing them needs func-value comparators or method-based
      equality — gap.
    * `Reverse[T]`: 1 site (loader `popLoading`).
    * `RemoveLast` / `RemoveByValue[T]`: 1 site (also loader
      `popLoading`, but it's "rebuild minus *streq match*", which
      is `RemoveWhere` shape — not a pure index/value remove).
    * `Copy[T]` one-liner: 2 sites; most slice-copies in the tree
      are inlined in larger functions.
  So no new helper to add right now without going speculative.
- **The real next pkg/slices step** the survey surfaced: 168
  `slices.Append[T]` calls live inside `for` loops, i.e. O(n²)
  builds.  Folding those into a growable container with amortised
  O(1) append (a `Vector[T]` / `Builder[T]` shape with capacity
  tracking) is a substantive design, not a quick add — file it for
  later when the surface is being intentionally pulled into a
  proper stdlib effort.

### Replace repeated `WriteStr(literal)` runs with adjacent-string concat (opportunistic)
- **Pattern**: code that builds output via a CharBuf often calls
  `WriteStr` many times with adjacent string literals — e.g.
  `cb.WriteStr("foo"); cb.WriteStr("bar"); cb.WriteStr("baz")`.
  Binate allows adjacent string literals to be concatenated by
  juxtaposition (`"foo" "bar" "baz"`), so a single
  `cb.WriteStr("foo" "bar" "baz")` (split across lines for
  readability) does the same work in one call.
- **Why it matters**: each `WriteStr` call is a method dispatch
  plus a CharBuf grow check.  Collapsing the literals into one
  call cuts both, and is also less code to read.
- **Most of these are in tests**, which compounds with the
  slow-tests theme — every saved WriteStr in a test that runs
  under boot-comp-int-int (or any interpreted mode) saves
  bytecode-dispatch overhead × test count.
- **How to land**: opportunistic, file at a time.  Best
  candidates: `cmd/bnc/test.bn`'s `genTestRunner`, anywhere
  building LLVM-IR text, and test fixtures that paste source
  fragments together a chunk at a time.
- **First pass landed** (binate `07b21ed`, 2026-05-15): 18 files,
  ~200 runs coalesced (`cmd/bnc/test.bn`, `cmd/bnc/util.bn`,
  `cmd/bni/main.bn`, plus check_*_test.bn and emit_*_test.bn /
  gen_*_test.bn in pkg/types, pkg/codegen, pkg/ir).  The
  cmd/bnc/test.bn growth (524 → 533) prompted a follow-up split
  to a new `gen_test_runner.bn` — test.bn now 381 lines.

### Replace if-return chains with `switch` where applicable (opportunistic)
- **Pattern**: code that does
  `if x == A { ... return ... }; if x == B { ... return ... }; ...`
  over many cases.  Common in op-dispatchers, kind-handlers, and
  predicates.
- **Why it matters**: a `switch` makes the structure obvious (all
  cases over the same scrutinee, mutually exclusive), gives the
  type-checker a hook for exhaustiveness checking if/when it
  lands, and reads more naturally.
- **Watch out for**: chains where the conditions aren't really
  equality on a single scrutinee — those genuinely are
  if/else-if and should stay.  Also: the bootstrap subset
  supports `switch`, so this isn't restricted to non-bootstrap
  code (unlike the interface TODO above).
- **How to land**: opportunistic.  Top candidates: the per-op
  dispatchers in `pkg/native/arm64/arm64_dispatch.bn`,
  `pkg/codegen/emit_instr.bn`, `pkg/vm/vm_exec*.bn`, and
  `pkg/ir/ir_ops.bn`'s opName / similar string-form helpers.
- **Landed (2026-05-25/26)**: the big per-op dispatchers are
  converted — `pkg/vm/vm_exec_pure.bn` + `vm_exec_helpers.bn`
  (binate `b4456ab`, `e4e7d29`), `pkg/codegen/emit_instr.bn`
  (`2d6d0f7`), `pkg/native/arm64/arm64_dispatch.bn` (`3756acc`).
  Where a chain mixes equality cases with op-RANGE checks
  (emit_instr's OP_ADD..OP_SHR / OP_EQ..OP_GE; arm64_dispatch's
  emitCompare/emitBinop/emitUnop delegates), the range arms stay
  as guards alongside the switch.  `ir_ops.bn`'s opName was
  already a switch — nothing to do there.  This work flushed out
  a CRITICAL case-scope miscompile (managed local in a `case`
  body), since fixed (`4306197`) — see the FIXED entry above.
  Remaining candidates are smaller / lower-value (assorted
  if-chains in cmd/* and pkg/* tools).

### pkg/codegen `TestEmitDebug*` dominates `boot-comp-int-int` runtime (perf)
- **Symptom**: pkg/codegen unit tests take ~1084s in CI under
  `boot-comp-int-int` (vs ~4s under `boot-comp-int`). The 26
  `TestEmitDebug*` tests account for ~78% of that runtime (~500s
  on local Apple Silicon, scaling up on CI x86). Top offenders:
  `TestEmitDebugStructWithArrayAndSliceFields` (~79s),
  `TestEmitDebugSliceFieldInStruct` (~41s),
  `TestEmitDebugSliceOfPointerChain` (~32s).
- **Isolated repro**: `TestEmitDebugStructWithArrayAndSliceFields`
  alone — 0.7s under `boot-comp-int`, ~120s under
  `boot-comp-int-int` (>100× slowdown for one test).
- **Mitigation in tree**: `scripts/unittest/pkg-codegen.skip.boot-comp-int-int`
  skips the `TestEmitDebug` substring under double interp. Coverage
  is preserved by every other mode that exercises codegen
  (`boot`, `boot-comp`, `boot-comp-int`, `boot-comp-comp*`).
- **Root cause to investigate**: each `TestEmitDebug*` runs
  `compileToLLVM(src)` with `SetDebugInfo(true)`. The DWARF emission
  path (DICompositeType chains, DIDerivedType members, member
  scope/baseType references) is heavy on string-building and
  small allocations. Under double interp every byte append /
  small allocation pays 2× bytecode-dispatch overhead, and there
  are many of them per test.
- **Possible angles** (investigated; first attempt was a net loss):
  1. Buffered string construction in `pkg/codegen/emit_debug*.bn`
     — coalesce per-node fragments to reduce CharBuf grows.  On
     inspection the literal-string `WriteStr` calls are already
     coalesced; the only repeating fusable pattern is `WriteByte('!')
     + WriteInt(id)` (~18 sites).  Mechanically fusable but ~18
     dispatches saved per node-emit × ~10 nodes/test ≈ milliseconds.
     Won't move 100s+ runtimes meaningfully.
  2. Cache stable strings (e.g. DI tag names, common type keys).
     **Tried 2026-05-13**: pointer-keyed cache in `dbgTypeID` that
     short-circuits `dbgTypeKey` for repeat lookups.  Single-test
     baseline 160s → 106s (-34%), but aggregate of all 26
     `TestEmitDebug*` went 441s → 513s (+16%) under boot-comp-int-int
     locally — the added pointer-scan per call pays off only when
     the registry is large (few slow tests) but slows the small-
     registry common case.  Reverted; needs a cache that's O(1)
     per call (e.g. a side-table on `@types.Type` itself, with the
     attendant `pkg/types` layout-contract implications).
  3. Reduce redundant work in the type registry — same composite
     type is rebuilt every call to `compileToLLVM`.  Cross-test
     state would also need per-module id offsets to keep nodes
     self-consistent; non-trivial.
- **Real next step**: actually profile before guessing again.  The
  intuition that "many small allocations × double-interp overhead"
  is the cost was correct in direction but wrong in distribution —
  most of the cost isn't where it looks like it should be.
- **Not blocking anything**; mitigation in tree (`1bffc43`).

### pkg/asm/aarch64 slow under `builder-comp-int-int` (perf)
- **Symptom**: under `builder-comp-int-int`, the
  `pkg/asm/aarch64` test package alone is slow enough to time
  out its CI shard at the 30-min cap. Other packages in the
  same mode finish comfortably.
- **Mitigation in tree**: skipped via the whole-package skip
  mechanism `scripts/unittest/pkg-binate-asm-aarch64.skip-pkg.builder-comp-int-int`
  (2026-06-10 — migrated from the old `.xfail`; slowness is a skip,
  not an expected failure). Coverage is preserved by `builder-comp`,
  `builder-comp-int`, `builder-comp-comp*` and the native_aa64 / arm32
  modes — this is purely a double-interp pacing issue. See the
  "int-int slow-package skips" entry below.
- **Hypothesis**: same shape as the codegen `TestEmitDebug*`
  entry above — many small CharBuf / refcount / bounds-check
  operations per emitted instruction, each paying 2× bytecode-
  dispatch overhead under VM-on-VM. The aarch64 assembler is
  string-heavy (encoding tables, mnemonic dispatch). Hasn't
  been profiled.
- **Next step**: profile one `pkg/asm/aarch64` test under
  `builder-comp-int-int` to confirm the hypothesis and identify
  the actual hot path before guessing at fixes. See the codegen
  entry above for the lesson on guessing-without-profiling.
- **Not blocking anything**; mitigation in tree.

### int-int slow-package skips — re-add after optimizing (or decide double-VM coverage isn't worth it) — FILED 2026-06-10
- **Context**: `builder-comp-int-int` (double-VM, VM-interpreting-VM) was "globally broken — every cell SIGSEGV'd" until `c997cf2e` (2026-06-09) made cells actually run. Now-healthy, the lane runs ~120+ min of work and was timing out its CI shards. Bumping unit sharding 4→8 (binate `e40fe3a0`) helped the light half but **4 of 8 shards still timed out at the 30-min cap, each completing ≤1 package** — i.e. a handful of packages each take **>~24 min (or hang) under double-VM**, which sharding can't fix (a single package can't be split across shards).
- **New mechanism (not xfail)**: added a whole-package skip — `scripts/unittest/<pkg-key>.skip-pkg.<mode>` (run.sh). Distinct from `.xfail` (asserts the package FAILS; XPASS-errors if it ever passes) and from `.skip` (drops individual tests but still runs the package). `.skip-pkg` omits the whole package from a mode because it's too slow there; it is NOT a failure (the tests pass — they're just not run in this lane). Counted as `pkg-skipped` in the summary.
- **Skipped under `builder-comp-int-int`**: round 1 (2026-06-10) — `pkg/binate/codegen` (its `TestEmitDebug` per-test `.skip` was insufficient), `pkg/binate/ir`, `pkg/binate/types`, `pkg/std/math/big`, `pkg/binate/asm/aarch64` (migrated from `.xfail`); these took 6 of 8 shards green. Round 2 (2026-06-10) — added `pkg/binate/vm` itself (CI showed it was the last timed-out shard's >24-min offender). The set was found empirically (heuristic + iterating on which shard still timed out), since the timed-out shards never log the offender's time.
- **Re-add work (the "separately" part)**: for each skipped package, either (a) profile + optimize its double-VM runtime so it fits a shard, or (b) make the explicit call that the double-VM lane adds no coverage over single-VM (`-int`) for that package (strong for the compiler-side ones — codegen/ir/types/asm test the COMPILER; `-int` already runs their tests through the VM; double-VM is the same logic + an extra dispatch layer). `pkg/binate/vm` is the one whose lost double-VM coverage is most arguable — its logic is still covered by `builder-comp-int` / `-comp-int` (single VM), and the lane's unique value is exercised by every OTHER package; re-adding it likely wants per-test `.skip` of its slowest tests rather than the whole package. When re-adding `codegen`, its `TestEmitDebug` per-test `.skip` still applies.
- **Separately unmasked**: `pkg/std/os` (landed `3ca36c82`) fails `vm/lower: unhandled IR opcode c_call` on ALL three VM-leg unit modes — libc-backed (native-only), same category as the `rt`/`bootstrap` xfails. NOT a slow-skip case (it genuinely FAILS in the VM), so it's `.xfail`'d (not `.skip-pkg`'d) for `builder-comp-int` / `-comp-int` / `-int-int`, matching that convention. My skips merely unmasked it (the shard used to time out before reaching it); it was already reding `builder-comp-int` independently.
- **Not a release blocker** (int-int non-blocking per `release-process.md`; was red at `bnc-0.0.7` too). Tracked here so the skips don't become permanent silent coverage loss.
- **STATUS 2026-06-10 — GREEN** (unit run on `3342460e`): all 8 `builder-comp-int-int` shards pass (2.5–26.7 min) and `builder-comp-int` / `-comp-int` pass. **Margin note**: shard 4/8 ran 26.7 min — ~89% of the 30-min cap; the 8-shard + skip set is sufficient but thin, so if the int-int suite grows it may need a 9th–10th shard or one more skip before it times out again. (The remaining unit reds — `arm32_{linux,baremetal}`, `native_x64` — are separate modes, not this. NOTE: `native_x64` was NOT "WIP" — it was broken by an ELF PC32 reloc bug, fixed 2026-06-14 `dd74c91e`; see the top-of-file native_x64 entry.)

### Function values — residual follow-ups (the MAJOR PROJECT landed) — 🟡 OPEN (low priority)
Function values are done across all three phases (archived in [claude-todo-done.md](claude-todo-done.md):
Phase 1 non-capturing + type/vtable machinery, Phase 2 closures/capture — `plan-function-values-phase-2.md`
is "COMPLETE (shipped)", conformance 338–344 + 501/508–510/513…, Phase 3 cross-mode trampolines).
Residual:
- Broader cross-mode trampoline signature shapes beyond `TrampolineScalar` (floats, aggregates, >7 args) —
  add when a path actually reaches them.
- Recursive lambdas (`var f = func(x){ … f(…) … }`) — non-goal during Phase 1; revisit now that Phase 2
  capture is settled (Y-combinator is the current workaround).
- Downstream interop hand-off (package descriptor; retiring ~30 hand-written `vm_extern` arms) is tracked
  under "Compiler/interpreter interop — MAJOR PROJECT".

### Cross-package method visibility in `.bni`
- Methods defined on a public type in package `foo` need to be declared
  in `foo.bni` for callers in other packages to see them — analogous to
  the existing `.bni` rules for free functions and types (covered by
  conformance tests 235/236, "Verify .bni vs .bn visibility semantics"
  is DONE).
- Currently, methods *do* work cross-package (conformance 330/331 cover
  it via `pkg/buf.CharBuf` methods called from `main`) because IR-gen's
  `RegisterImport` registers methods from the imported package's `.bn`
  source via the loader. That's a happy accident of the loader path, not
  a deliberate visibility design.
- Open: should `.bni` method declarations be required for cross-package
  visibility (matching free functions / types), and should the type
  checker enforce that? Today methods skip the `.bni` requirement.
- When picking this up, look at: how `pkg/buf.bni` declares its type but
  not its methods, yet cross-package callers still resolve them; whether
  to extend `checkBniSignatureMatch` to methods; whether `.bni` method
  decls are mandatory or just allowed.

### Readonly method receivers — deferred (gated on methods/interfaces)
- A method's receiver kind (`*readonly T` / `@readonly T`, plus value
  receivers — which are always readonly) determines which pointer kinds
  satisfy an `impl` and bounds what the method may mutate.  See
  `claude-notes.md` (value receivers always readonly; readonly-restricted
  dispatch expressed at the impl level; `*readonly T` receiver smoothing
  auto-takes `&t` at the call site).
- This was "Stage 3" of the old `const` type modifier.  The rest of that
  work landed and the type-level modifier is now spelled `readonly`
  (`plan-const-readonly.md`, COMPLETE 2026-06-03 — `const` split into
  compile-time `const` / `var` storage / `readonly T` modifier; that
  plan's three listed deferrals — readonly-slice slicing, `.bni`
  extern-var, `&pkg.Const` — are all since resolved).
- Deferred, not abandoned — depends on the methods/interfaces feature.
  Fold into that project's tracking when it firms up.

### Observable optimizations and UB policy — broader question
- Surfaced while planning const: allowing the compiler to allocate
  a shared static global for all-const composite literals is an
  optimization observable via raw-pointer comparison (`&a[0] ==
  &b[0]` where `a`, `b` are both `"hello"`). The const plan accepts
  this as UB rather than either blocking the optimization or
  carving out precise "same-literal-text gives same address"
  semantics.
- Same class as the refcounting move optimizations that are already
  observable via `rt.Refcount(...)` without a nailed-down spec.
- **Broader question**: do we want a general policy of "these kinds
  of observations are UB, the compiler may optimize across them",
  written up somewhere authoritative? Candidates for the same UB
  bucket: literal address identity, refcount timing, struct padding
  bytes, uninitialized-memory reads of stack-allocated vars. The
  alternative (fully specified observable behavior) is probably
  incompatible with small-target codegen goals.
- Not urgent — we're already making these trade-offs silently. A
  short design note ratifying the policy would be useful when a
  future optimization / feature forces the question.

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar

### DWARF debug info — finer-grained source positions (open-ended, low priority) — 🟡 OPEN

The DWARF foundation + full type coverage are done (archived in [claude-todo-done.md](claude-todo-done.md):
`-g`, DICompileUnit/DIFile/DISubprogram, per-function DISubroutineType, DILocalVariable for
locals + params, and DIBasicType/DICompositeType/DIDerivedType covering scalars, pointers,
structs, slices, managed-slices, interface-values, function-values, arrays, named typedefs).
The one remaining, open-ended piece:
- Thread source positions through more IR-gen sites (statements, assignments, calls) for
  finer-grained `DILocation` — today only `genExpr` threads `.Line`; most emission sites rely
  on coarse statement-line backfill. No columns.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).

### Package manager — sketch a design
- We don't have one yet. The current model is "everything lives under a
  root directory; `-I` and `-L` point the loader at extra search paths."
  Fine for the toolchain and a handful of conformance fixtures; doesn't
  scale to "I want to depend on `someone/foo` at version vX."
- Questions a sketch should answer:
  - Naming: are packages identified by URL (`github.com/...` Go-style),
    by a registry name, by a flat namespace? Interacts heavily with the
    package path conventions, decided in [`pkg-layout-spec.md`](pkg-layout-spec.md).
  - Manifest file format and location (`binate.toml` / `bn.mod` / TBD).
    What does a minimal valid manifest look like?
  - Dependency resolution: version constraints, lockfile, MVS vs SAT,
    handling of mutually-incompatible transitive deps.
  - Vendor / cache layout: per-project, per-user, or system-wide.
    Reproducibility story.
  - Binary artifacts vs. source: tied to the existing IMPL_PATH split
    (compiled `.o` / `.a` distribution vs. source) — see
    "Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)"
    below.
  - Interop with `.bni` distribution: the loader already treats `.bni`
    and impl as independent search paths; the package manager must
    respect that.
  - Bootstrap path: how does the bootstrap interpreter find packages?
    Probably "vendored copy in tree, no resolver." Confirm that's the
    right answer.
  - Out-of-tree builds: where do build artifacts go? How does the
    package manager interact with `--build-dir`?
- Output: a plan doc in `explorations/` (e.g. `plan-package-manager.md`),
  not implementation. The path conventions are already ratified in
  [`pkg-layout-spec.md`](pkg-layout-spec.md); this sketch builds on them
  (esp. its "Package manager interaction" section).

### Tier + dependency-direction hygiene checks (enforce `pkg-layout-spec.md`)
- **What**: a hygiene check (new script under `scripts/hygiene/`, alongside
  `conformance-imports.sh`) that enforces the tier dependency-direction rule
  from [`pkg-layout-spec.md`](pkg-layout-spec.md): a package may import only
  packages at its own tier or **lower**; importing a strictly-higher tier is
  a violation.  Tiers, low→high: 0 / 0b (`pkg/builtins/*`) < 1 (`pkg/std/*`)
  < 1x (`pkg/stdx/*`) < 2 (`pkg/<org>/*`, e.g. `pkg/binate/*`) < 3
  (app-specific).  E.g. `pkg/builtins/rt` importing `pkg/std/io` is illegal;
  `pkg/binate/parser` importing `pkg/std/os` is fine.  (This is the runtime
  enforcement of the spec's "Transitive constraint" + tier table.)
- **Special case — `pkg/std` → `pkg/stdx`**: tier 1 (`std`) may depend on
  tier 1x (`stdx`) **internally** (in `.bn` impl files) but **not externally**
  (in `.bni` interface files).  A `.bni` importing `stdx` would leak a
  no-inter-version-compat (1x) type into `std`'s strict-compat (tier 1)
  surface.  So the check must scan `.bni` imports separately from `.bn`
  imports: the std→stdx edge is allowed only from `.bn`.  (Generalize if
  other interface-vs-impl tier asymmetries surface.)
- **How**: derive each package's tier from its path — the realized layout
  makes tier path-derivable (`ifaces/core` + `impls/core/*` → tier 0/0b;
  `ifaces/stdlib/pkg/std` → tier 1, `…/pkg/stdx` → tier 1x; `pkg/binate/*`
  → tier 2).  Walk every package's imports (split by `.bni` vs `.bn`), map
  importer + imported to tiers, flag any higher-than-self edge, applying the
  std/stdx interface refinement.  A whitelist file (cf.
  `conformance-imports.whitelist` / `naming.whitelist`) covers sanctioned
  exceptions.
- **Scope** (per CLAUDE.md "Stay Within the Asked Scope"): add the script
  only; wiring it into `scripts/hygiene/run.sh` and CI is a separate decision
  for the user.

### Build constraints (`#[build(EXPR)]`) — deferred follow-ups (arch/os MVP landed) — 🟡 OPEN
The `#[build(EXPR)]` arch/os MVP is landed at all four granularities (file / decl / import / `.bni`),
host-default config overridable per `--target`, through `c7249552` (conformance 731/733/735/736/737/746/747);
full design in [`plan-build-constraints.md`](plan-build-constraints.md), archived in
[claude-todo-done.md](claude-todo-done.md). Still deferred (none started):
- Vocabulary beyond arch/os: `triple` / `backend` / `libc` / `ptrsize` / `version` with `is` / `at_least` / `at_most`.
- `bnlint --target`; main-module gating; migrating the `impls/` duplicate trees onto constraints.
- The separate inline-asm (`#[asm]`) doc that composes with this substrate.

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

### Language spec(s) — write the primary spec; later, secondaries
- See `claude-notes.md` § "Language specification — primary spec is
  minimal — DECIDED" for the philosophy.
- **Primary language spec**: syntax, type system, semantics, plus
  *only* the packages intrinsically tied to the language
  implementation — `pkg/rt` (after the review below) and a future
  reflection/introspection package. Includes the one-line note that
  user files cannot be named `*_test.bn` (reserved).
- **Minor secondary spec — testing**: `_test.bn` packaging
  convention + `pkg/builtin/testing`. May fold into primary; TBD.
- **Major secondary spec(s) — stdlib**: I/O, containers, formatting,
  string utilities, etc. Probably split across multiple specs by
  area.
- **Not started.** Discussion-only at this point. When writing
  begins, the natural artifact is `explorations/spec-*.md` (or a
  separate `spec/` directory). The primary spec is gated on the
  pkg/rt review entry below, since the primary spec describes
  pkg/rt's normative surface.

### pkg/rt review — decide runtime vs. stdlib vs. internal
- Today `pkg/rt` is a grab-bag of runtime helpers, refcount
  primitives, allocator wrappers, bounds-check stubs, etc.
- For the primary spec to nail down "what the runtime contract
  is," `pkg/rt`'s surface needs a review: classify each member as
  **stay** (truly language-runtime, normative in the primary
  spec), **move** (standard-library-shaped — belongs in a stdlib
  package, out of `pkg/rt`), or **make-internal** (only used by
  the language implementation itself, no `.bni` export).
- Output: a classification of `pkg/rt` members + a follow-up
  cleanup plan (a `plan-*.md` doc under `explorations/`). The
  cleanup itself is separate work and can be sequenced
  independently — what's important first is the *classification*,
  which unblocks the primary spec writeup.

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Test runner improvements
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Parallelization**: consider running test packages in parallel within a mode.

### ARM32 bare-metal target — MAJOR PROJECT
- **Why**: enable Binate as an OS-development language on ARM32
  bare-metal (Cortex-A and possibly Cortex-M). Bare-metal is the
  endgame — we want to write the OS in Binate, not run on top of
  one. **ARM32 Linux via LLVM** has been added to the plan as an
  explicit v0 derisking step (it shares all the prerequisites and
  validates the 32-bit type-system path without committing to
  bare-metal runtime work); see plan doc.
- **Existing substrate that already handles bare-metal cleanly**:
  - `pkg/asm/arm32` encodes ARMv7-A instructions (data-processing,
    load/store, multiply/divide, branches, system); 73 unit tests pin
    bit patterns. Assembler-side is essentially done.
  - `pkg/asm/elf` emits ELF32 with the right ARM32 reloc set
    (R_ARM_JUMP24, R_ARM_ABS32). End-to-end tests in
    `pkg/asm/elf/elf_test.bn` already link with `arm-none-eabi-ld`
    (bare-metal linker) and run under `qemu-system-arm -semihosting`
    on virt machine. Three tests: exit, loop sum, function call.
  - `cmd/bnas` already accepts `.arch arm32` and routes through the
    ARM32 instruction parser.
- **What's missing**: an IR-to-machine-code lowering for ARM32 (a
  `pkg/native/arm32` sibling of `pkg/native/arm64`), and a bare-metal
  runtime port.
- **The interesting bit: bare-metal makes the runtime story
  non-trivial.** Things the language/runtime currently assumes from
  the host that don't exist on bare metal:
  - **Allocator**: `pkg/rt`'s managed-pointer/managed-slice
    allocations go through `bn_rt__c_malloc` / `bn_rt__c_free` /
    `bn_rt__c_calloc` (libc-shaped C stubs). On bare metal we need
    a Binate-implemented allocator — probably a simple bump
    allocator first (no free, suitable for early boot), then a real
    heap (free-list or buddy). Allocator implementation lives in
    pkg/rt (or a peer package) and replaces the `c_*` bridges for
    the bare-metal target. The existing "Un-export `rt.c_*`" TODO
    is a prerequisite — once those are private, we can swap them.
  - **`memset` / `memcpy`**: tiny Binate or asm implementations.
  - **Exit / abort / panic**: semihosting `SYS_EXIT_EXTENDED` for
    QEMU testing; on real hardware, `wfi` loop or reset.
  - **I/O**: no stdout/stderr — need a UART driver or semihosting.
    Two flavors:
    - Semihosting (used by the existing QEMU tests): debug-only,
      requires a debugger / QEMU. Useful for development, not for
      shipping.
    - UART: target-specific MMIO. Need a small driver per board —
      PL011 for ARM virt machine, vendor-specific for real hardware.
      The `bootstrap.Write` extern would dispatch to a board-defined
      `uart_putbyte` instead of `write(2)`.
  - **`bootstrap.*` shape**: today's bootstrap.bni is libc-shaped
    (Open / Read / Write / Stat / Args). Bare metal has no
    filesystem and no argv. We'd want a smaller bare-metal-friendly
    bootstrap interface — probably just an output sink and a panic.
    The `formatInt` / `formatBool` / `formatFloat` helpers stay
    (they're pure Binate); only the I/O surface changes.
- **Boot**: a tiny crt0 in asm (or Binate inline-asm if we ever add
  it) to set up the stack, zero BSS, copy .data from flash to RAM,
  then jump to `bn_main`. Provided as a per-board file alongside the
  linker script.
- **Linker script**: per-board memory map (text/rodata in flash, data
  in RAM, BSS, stack at top of RAM, optional MMU page tables for A-
  class). The QEMU virt machine convention (text at 0x40000000) is a
  good first target.
- **Two paths to actual codegen**, similar to the ARM32-Linux
  consideration but with bare-metal twists:
  - **LLVM-via-clang**: pass `--target=armv7a-none-eabi`,
    `-mfloat-abi=soft` (or `hard` if we want NEON/VFP), no sysroot.
    Fastest to first-light, but the LLVM dependency is heavier on a
    bare-metal toolchain story (we'd need to ship clang + lld or
    require the user to have a cross toolchain installed).
  - **Native pkg/native/arm32**: full sibling of `pkg/native/arm64`.
    AAPCS32 calling convention (NGRN over R0..R3, args 5+ on stack,
    return values in R0..R3, large-aggregate return via the hidden
    pointer in R0). Mach-O isn't relevant here — only ELF32 output.
    No external dependency once written. Larger upfront cost; closer
    to the OS-language goal of "no LLVM at runtime."
- **Testing**: the existing `pkg/asm/elf` semihosting harness scales
  up — write conformance programs that use only the bare-metal
  runtime surface, link with `arm-none-eabi-ld`, run under QEMU
  with `-semihosting`. Once the UART driver lands, switch to
  reading stdout from QEMU's serial0.
- **Adjacent in-flight items that affect this**:
  - "Un-export `rt.c_*`" — direct prerequisite for swapping the
    allocator/memops bridges per-target.
  - "Native AArch64 backend cluster A" — in flight; the
    common AAPCS dispatch helper in `pkg/native/common` is shared
    between ARM64 and a future ARM32, so ARM32 work shouldn't start
    until the ARM64 native backend is stable enough that we know the
    common shape is right.
  - The compiler/interpreter interop work is independent of this —
    interop is mostly a layout/representation question, not a
    target question.
- **Suggested first milestone**: get a meaningful subset of
  conformance running on QEMU via the LLVM backend with semihosting
  I/O. Concretely:
    - Pick the codegen path: LLVM-via-clang first
      (`--target=armv7a-none-eabi -mfloat-abi=soft`). Defer the
      native `pkg/native/arm32` backend until LLVM-via-clang
      validates the runtime/boot/linker story.
    - Implement a bump allocator in `pkg/rt` (no free) — enough for
      every conformance test that doesn't actually run out of memory.
      Allocations touch managed-pointer / managed-slice paths only,
      so this is the same surface the existing `c_malloc`/`c_calloc`
      bridges expose. Wire it behind a build-mode switch alongside
      the existing libc-bridges path.
    - Implement semihosting `SYS_EXIT_EXTENDED` (already used by the
      pkg/asm/elf QEMU tests) and `SYS_WRITE0` for putchar/print.
      Replace `bootstrap.Write` (the I/O primitive everything
      eventually funnels into after the print rewire) with the
      semihosting variant for this target.
    - Add `memset` / `memcpy` in pure Binate (or a tiny inline-asm
      wrapper if one is later added).
    - Conformance tests that DON'T touch file I/O / argv / dirs
      should pass: arithmetic, control flow, structs, slices,
      managed pointers, methods, etc. Probably 200+ of the existing
      278. Tests that rely on `bootstrap.Open` / `Read` / `Args` /
      `Stat` / `ReadDir` / `Exec` would be excluded for v1.
- **Plan doc**: `explorations/plan-arm32-bare-metal.md` exists as a
  **DRAFT** (initial sketch — not yet ratified). Covers the items
  above plus: target board choice (QEMU virt + one real Cortex-A
  board TBD), allocator design (bump first, heap second), bare-
  metal `bootstrap.bni` shape, boot/linker-script convention, and a
  placeholder for the per-package inventory of `bootstrap.*` calls
  (the inventory itself is deferred to a follow-up). Needs review
  pass before any implementation begins.

### Compiler/interpreter interop — MAJOR PROJECT
- **Why this is high priority**: dual-mode execution is a core promise of the
  Binate language. Compiled-and-interpreted code calling each other (in both
  directions) is what makes "compile some packages, interpret others" actually
  useful. We should make this real BEFORE pushing on more language features —
  large language additions risk locking in design choices that close off
  interop options.
- **Likely-already-compatible substrate** (verify rather than redesign):
  - **In-memory layout of types** is supposed to match across modes. Compiler
    uses `pkg/types`'s SizeOf/AlignOf/FieldOffset; interpreter uses (or should
    use) the same. Verify with a small cross-mode struct-pass test.
  - **Refcounting**: managed allocations carry a header with refcount and a
    pointer to the destructor, populated at allocation site. Compiled and
    interpreted code use the same `rt.RefInc` / `rt.RefDec` / `rt.Free`. Free
    paths invoke the per-type dtor through the header, so a managed value
    allocated on one side and dropped on the other should clean up correctly.
    Verify with a cross-mode managed-pointer round-trip.
- **Direction to start with**: interpreted code calling compiled code. Simpler
  than the reverse (no need for the compiler to plant trampolines into a
  running interpreter). Once that works, compiled code calling interpreted
  code falls out roughly symmetrically.
- **Granularity: package-level.** For interpreted code in package P to call
  into a compiled package Q, the interpreter needs:
  - Q's `.bni` (so the interpreter can type-check P against Q's signatures —
    this already works today via the existing `.bni` loading path).
  - **Pointers to Q's compiled functions** (the actual interop primitive).
- **Proposed mechanism: auto-generated package descriptor.** The compiler emits,
  for each package Q, a synthetic `const` of a synthetic struct type — call it
  e.g. `foo.Package` (working name; could be `foo.PackageImpl` or another
  canonical name) — whose fields are pointers to Q's exported functions in some
  canonical order (e.g., sorted by mangled name). The interpreter, when it
  loads compiled package Q, reads that descriptor and binds each field as the
  function value for the corresponding name in Q's scope. Naming and layout
  must be canonical so an interpreter built against Q's `.bni` can read Q's
  descriptor without further metadata.
- **Symmetry**: the interpreter should produce the same shape on its own end —
  for each interpreted package, expose a `foo.Package` whose function-pointer
  fields are trampolines into the interpreter (call into the bytecode VM
  using the trampoline's bound bytecode/closure-env/types/aliases). That way
  compiled code calling interpreted code is the same mechanism, mirrored.
- **Prerequisite — DONE**: function values (see
  `plan-function-values.md` + `plan-function-values-phase-3.md`).
  The descriptor's fields are pointers to functions — that's
  exactly what function values are. The 2-word `{vtable, data}`
  representation, the `(*uint8 data, <args>)` always-shim
  convention, the per-function `__shim.<mangled>` shims, the
  bytecode-side `dispatchCompiledFuncValue` (via
  `rt._call_shim_scalar`), and the compiled-side `TrampolineScalar`
  are all in place. The remaining work is the descriptor itself
  (naming, layout, emission, loading) plus the symmetric VM-side
  emission for interpreted packages — pure plumbing; no new
  trampoline machinery needed.
- **Adjacent cleanup, lighter-weight first step**: see the
  "VM extern dispatch: name → function-value registry" entry
  above. A per-VM name → function-value registry with manual
  registration (no descriptor design needed) replaces
  `pkg/vm/vm_extern.bn`'s hand-coded switch via the same
  `dispatchCompiledFuncValue` path Phase 3 already provides.
  Auto-generated descriptors are the more general form of the
  same idea — the registry stays as the manual-registration
  escape hatch for host-only externs that have no Binate-side
  `.bni` package.
- **Design open questions** (need a writeup before implementation):
  - Canonical name for the descriptor — `foo.Package` reads naturally but
    risks conflicting with user names. `foo.PackageImpl` or a reserved-prefix
    name (`__pkg_foo`)? Reserve a keyword?
  - Canonical layout — sort by mangled name? By declaration order in `.bni`?
    Layout must be agreed-upon by the descriptor's emitter and reader.
  - Interaction with import aliases (`import alt "pkg/foo"`) and blank imports
    (`import _ "pkg/foo"`) — see the "Import aliases and blank imports" entry.
  - What does the descriptor look like for the package being compiled itself
    (the "self" descriptor)?
  - How are package-level globals exposed? Functions are the obvious starting
    point; globals are a separate (but related) interop question.
  - Versioning: if Q's `.bni` and Q's compiled descriptor disagree (different
    function set, different layout), how do we detect and report it?
- **Adjacent in-flight work that affects this**:
  - "Function values — MAJOR PROJECT" (above) and
    `plan-function-values.md` — direct prerequisite. Phase 3 of
    that plan delivers the cross-mode trampoline machinery this
    work consumes.
  - "Free-function pointer in managed-allocation header — bug"
    (above, DONE within a single mode) — Free now dispatches through
    `header[1]`. Cross-mode allocate-on-one-side / free-on-the-
    other still requires Phase 3's trampolines to translate
    `header[1]` between the C-pointer and VM-index conventions.
  - "Lift function-name qualification into IR" (above) — would simplify name
    resolution at the interop boundary.
  - "Import aliases and blank imports" (below) — affects how the descriptor
    is named at the import site.
- **Suggested next step**: write a design doc (e.g.
  `explorations/plan-compiler-interp-interop.md`) that nails down the
  descriptor name/layout, walks through one concrete cross-mode call end-to-
  end on each side, and identifies the first concrete code change to make.
  Don't start implementation until the design is reviewed.

### REPL: remove process-global session state (multi-session blocker)
- **Now owned by [`plan-embeddable-vm.md`](plan-embeddable-vm.md)** (scoped
  2026-06-16): the `ir` half below is increments 4–5 of that plan, which
  covers the full compiler/VM global inventory, not just the REPL's two.
  This entry's `ir/gen.bn` line numbers are stale as of 2026-06-02; see the
  plan for verified ones.
- **What**: the REPL engine keeps per-session state in PROCESS-GLOBAL
  package vars instead of threading it through the session. v1 of the
  embeddable refactor (above) lifts the cmd/bni-local ones into
  `@ReplSession` but deliberately keeps **single live session per
  process**, leaving two `pkg/binate/ir` globals in place.
- **The globals**:
  - cmd/bni-local (lifted into `@ReplSession` by Stage 1 of the
    refactor): `replLoader`/`replRoot`/`replBniPaths`/`replProcessedPkgs`
    (`cmd/bni/repl_import.bn:24-41`) and `replInitCounter`
    (`cmd/bni/repl_decl.bn:411`).
  - `pkg/binate/ir` process-globals (NOT lifted in v1, the real
    multi-session blocker): `currentChecker` (`pkg/binate/ir/gen.bn:148`,
    set via `ir.SetChecker`) and the import alias map
    `importAliasNames`/`importAliasPaths` (`gen.bn:107/110`), with
    `Save`/`RestoreAliasMapState` bracketing in `evalReplImport`
    (`repl_import.bn:101/146`).
- **Why it matters**: single re-entrant session is unaffected (the ir
  globals are set once and save/restored inside import turns as today).
  But >1 concurrent embedded session in one process needs those globals
  session-scoped (or save/restored at every `Step` boundary) — a
  separate, larger change that must land BEFORE `pkg/binate/repl` can
  honestly claim multi-session support.
- **Guidance (applies now)**: **do not add any new REPL globals.** New
  per-session state goes through `@ReplSession`. Adding a global "to keep
  a signature stable" (the exact shortcut that created the current ones,
  per `repl_import.bn:18-20`) is what this entry exists to stop.
- **When**: only if multi-session embedding becomes a goal. Not needed
  for wasm B1 (one worker = one session).

### REPL — Tier-4 follow-ups + pretty-printer (all five tiers landed) — 🟡 OPEN (low priority)
All five REPL tiers are landed (archived in [claude-todo-done.md](claude-todo-done.md): Tier 1–2 eval +
redefinition, Tier 3 forward refs incl. pending types/vars/consts + cycle detection, Tier 4 replace +
shadow for funcs & methods, Tier 5 mid-session imports `78685ac3`). Residual:
- **Tier 4**: refcount-aware shadow warning (today fires unconditionally); forced-shadow escape hatch (syntax TBD per `claude-notes.md`).
- **Pretty-printer** (`pkg/replprint`) — deferred until interfaces land (`bootstrap.println` is a temporary hack; don't entrench it).

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

### Package path: env-var support (Stage 7)
- Add `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`
  (long names match `LD_LIBRARY_PATH`/`PYTHONPATH` style; aliases TBD)
  as the fallback when CLI flags are absent.
- Gated on adding `bootstrap.Getenv` (a few lines of C + Go-interp
  glue). Deferred because direct shell invocations of bnc/bni today
  can construct CLI arguments — the env-var fallback is convenience
  for users invoking the tools by hand.
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Env vars".

### Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)
- Once we have a stable per-package ABI/linker contract: accept
  `.o`/`.a`/`.so` files on `IMPL_PATH` as alternatives to `.bn`
  source. `hasImplFiles(dir)` becomes "has at least one of {.bn, .o,
  .a, .so}". Precedence rule (likely .o/.a/.so wins over .bn, with
  `--prefer-source` to override) is open.
- bnc would also gather binary artifacts from `IMPL_PATH` and feed
  them to the linker automatically (today users supply via
  `--cflag`).
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Future: binary impl artifacts".

### Build out e2e testing
- We have unit tests (per package) and conformance tests (language
  semantics). What we don't have is a place for **end-to-end tool
  integration tests** — checks that the CLI/loader/runtime wiring
  works the same way across all four tools that load Binate
  packages: `bootstrap`, `bnc`, `bni`, `bnlint`.
- **What's landed (2026-04-30):**
  - Two scripts: `e2e/split-paths.sh` (the original — `-I`/`-L`
    cross-tool contract; covers Stage 1–6 of the package-search-paths
    plan) and `e2e/repl.sh` (9 cases for `bni --repl`: basic call,
    multi-stmt, error recovery, multi-line for-block, braces in
    string literal, plus four Tier 2 cases — func persists, cross-
    decl call, type rejected with diagnostic, bad body recovery).
  - CI hookup at `.github/workflows/e2e-tests.yml` — matrix-
    discovery via `ls e2e/*.sh`, one runner per script, `fail-fast:
    false`.  Standard checkout layout (binate + bootstrap as
    siblings) matches what the scripts assume.  New e2e scripts are
    picked up automatically.
- **Unique challenges this dir still has to solve over time:**
  - **4 tools, not 1.** A single feature (like `-I`/`-L`) needs to
    be exercised on each tool independently, since each parses CLI
    flags separately and threads them into the loader differently.
  - **Multiple build/run modes for the binate-written tools.** bnc,
    bni, and bnlint can each be exercised through several pipelines:
    bnc via boot-comp / boot-comp-comp / boot-comp-comp-comp /
    boot-comp_native_aa64; bni via boot-comp-int / boot-comp-comp-int;
    bnlint via the same chains as bnc. Note that bni cannot be
    interpreted directly by the bootstrap (cmd/bni imports pkg/vm,
    whose float literals the bootstrap lexer doesn't recognize) —
    bni really has to be built via boot-comp first.
    Full e2e coverage of "feature X works" multiplies tools × build
    modes — easily 10+ runs per feature. We don't necessarily want
    that today; figuring out which slice is worth the cost is part
    of building this out.  Today both shipping scripts pick a
    single mode each (split-paths covers all four tools at their
    "default" build path; repl uses boot-comp bni).
  - **Fixture management.** Conformance tests share a single root;
    e2e tests like split-paths need disjoint fixtures, ad-hoc temp
    dirs, optional checked-in subtrees. No standard pattern yet —
    both current scripts use `mktemp -d` + `trap rm -rf` and inline
    `cat <<EOF` heredocs for fixture files.
- **Why these scripts are useful motivating examples:**
  - **split-paths**: the `-I`/`-L` feature is something `bootstrap`,
    `bnc`, `bni`, and `bnlint` should all support **identically** —
    a deliberate cross-tool contract.  e2e is the only layer where
    that contract can be observed directly.
  - **repl**: the `bni --repl` PoC is a multi-stage user-facing
    flow (load module → drive prompt via stdin → check banner +
    prompts + results byte-for-byte).  No unit test could easily
    exercise the full input-to-output transcript; e2e is the right
    layer for "the REPL works end-to-end".
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  for the spec `e2e/split-paths.sh` validates and
  [`plan-repl.md`](plan-repl.md) for what `e2e/repl.sh` covers.

### Annotations and C function interop
- **Option E (`__c_call` intrinsic) has a detailed implementation plan:
  [plan-c-call.md](plan-c-call.md).**
- Consider implementing annotations (decorators/attributes).
- Specific use case: annotating functions as C functions.
  - **Option A**: annotation in `.bni` — callers know the name and calling convention, but mixes interface with implementation.
  - **Option B**: annotation on the definition (with empty body) — `bnc` generates a trampoline. But empty body is weird (missing return values?).
  - **Option C**: annotation on a call site, indicating it's a C function call. Maybe a "magic" C package so no annotation is needed at all.
  - **Option D**: manual trampolines, with a magic C package for declarations.
  - **Option E**: a `__c_call` compiler intrinsic at the call site, no
    declaration needed.  Two forms were considered:
    - **E1 (rejected)**: pass a C prototype string —
      `__c_call("ssize_t write(int, const void*, size_t)", fd, buf, len)`.
      Reads nicely, but forces the compiler to parse C and resolve C
      types, which drags in typedefs, macros, and platform builtins
      (`__size_t` &c.).  Not practical.
    - **E2 (preferred)**: pass the C symbol name, an explicit return
      type, then the argument values already in (or cast to) the
      Binate types that match the C ABI —
      `result = __c_call("write", int, cast(int, fd), cast(*uint8, buf), cast(uint, len))`
      (casts are unnecessary when the variables already have the right
      type).  Supported argument/return types: scalars, struct types,
      and pointers to these (to any depth: `*T`, `**T`, …).  This
      reuses the backends' existing platform-C-ABI lowering (struct
      sret thresholds, register assignment) — no C parsing, no type
      resolution, no new ABI logic.  The symbol name is emitted
      verbatim (no `bn_` mangling); the backend emits the matching
      `extern`/`declare`.
  - **C-types alias package (decided)**: a package (e.g. `pkg/c`)
    pins the Binate↔C scalar correspondence in one place so call sites
    don't open-code it.  `C_int`/`C_uint` = `i32`/`u32` (C `int` is
    32-bit on both ILP32 and LP64, *not* target-word-width like Binate
    `int`); `C_long`/`C_ulong` = target-word (LP64 Unix; matches Binate
    `int`/`uint`); `C_size_t` = `uint` (pointer-width); `C_char` = `i8`
    (signedness is platform-dependent in C — note the caveat, but it's
    promoted on pass so rarely matters).  Plus a sentinel `C_void` for
    the return-type slot of functions that return nothing.  So the
    example's `fd` is really `C_int` (= `i32`), not `int`.
  - **Scope decisions (v1)**:
    - **Compiled-mode-only to start.** The compiler emits a direct
      call; the VM would need FFI-style dispatch (resolve the symbol
      via the extern registry + marshal by the supplied types) — punt
      that.  `__c_call` outside compiled mode is an error for now.
    - **Include variadics from the start.** The whole point of
      `__c_call` is to retire `pkg/bootstrap`'s hand-written C
      wrappers and the special shim machinery — and several of those
      OS interfaces are variadic in C (`open(const char*, int, ...)`
      where `mode` is a vararg; `fcntl`, eventually the `printf`
      family).  Punting variadics would leave bootstrap unable to go
      away, defeating the purpose.  So v1 supports them.
      - **Boundary marker (required).** The call site must declare
        where fixed args end and variadic args begin — it can't be
        inferred from the values (`open(path, flags, mode)` is
        indistinguishable from a 3-fixed-arg call).  Proposed: a
        `C_varargs` sentinel (or a recognized `...` token) in the
        argument list:
        `__c_call("open", C_int, path, flags, C_varargs, mode)`.
        Everything after the marker is an anonymous/variadic arg.
      - **Backend work is lopsided.** LLVM path: nearly free — emit
        `declare i32 @open(i8*, i32, ...)` + a varargs call with the
        right fixed-arg count, and LLVM does the platform-correct
        lowering (x86-64 `AL` = vararg float count, darwin-arm64
        stack-passing, 64-bit-vararg alignment) for us.  Native
        backends (`pkg/native/{arm64,amd64}`): real work — they emit
        machine code directly and must implement the vararg
        convention per target (darwin-arm64 stacks all varargs;
        x86-64 SysV sets `AL`; AArch64-Linux/arm32 mostly match the
        fixed convention but 64-bit varargs need 8-byte alignment).
        This extends the existing `CallConv`/register-assignment
        logic; needs per-target tests.
  - **Open considerations for E2 (still to resolve)**:
    - Confirm the full `pkg/c` scalar table against each target
      (`C_long` on a 32-bit target, `C_char` signedness, the float
      types if/when floats land).
    - Final spelling of the variadic boundary marker (`C_varargs`
      sentinel vs a `...` token vs an explicit fixed-arg count).
    - VM/dual-mode FFI dispatch (deferred above) when interpreted-mode
      `__c_call` is eventually wanted.
  - **Companion idea — link-requirement annotation (sketch)**: Option E
    makes a C symbol *callable*; a complementary annotation would make
    it *resolve at link time* by declaring, at the source level, that
    using a package requires linking some C library — so the driver
    adds the flag automatically instead of every consumer passing
    `--cflag -lm` / `--link-after-objs` by hand.  Prior art:
    Rust `#[link(name = "m", kind = "static")]`, Go cgo
    `// #cgo LDFLAGS: -lm`, MSVC `#pragma comment(lib, "foo")`.
    Natural shape: `#[link("m")]` (optionally a `static`/`dynamic`/
    `framework` kind), most naturally on the `.bni` since the link
    requirement is part of the package's contract.  This is also the
    first real payoff of the general annotations feature this item is
    about — both Option E and this want it.
    - **Open wrinkles**:
      - **Transitivity** — the requirement must propagate through the
        import graph (aggregate + dedup all declared libs for any
        binary that transitively imports the package).  Hooks into the
        loader's `ldr.Order` walk + the driver's `clangArgs` assembly.
      - **Link ordering** — static archives only supply symbols
        referenced by *earlier* inputs, so aggregated `-l` entries
        need correct placement vs. the `.o` files and runtime (the
        driver already does this for `linkAfterObjs`).
      - **Search paths** — keep the annotation name-only (`-l`); leave
        `-L<dir>` to driver flags.
      - **Platform-conditionality** — a `libm` dep is meaningless on
        bare-metal arm32 and `framework` kind is macOS-only, so the
        annotation likely needs to be target-qualifiable.  Ties into
        the C-free principle: this exists only to interface with
        existing C systems and should evaporate on freestanding
        targets.
      - **Static-spec portability** — even with `kind = static`,
        expressing it portably is messy (GNU ld `-l:libfoo.a` /
        `-Wl,-Bstatic`; macOS `ld` has neither), so it may need
        per-platform lowering in the driver or a full-path escape
        hatch.

---

## TEST COVERAGE — conformance matrix follow-ups

### Stale-xfail sweep — residuals (the cross-mode CONFORMANCE sweep is done) — 🟡 OPEN
The big stale-xfail sweep — all 10 modes via the `conformance-xpass.yml` CI workflow;
121 stale conformance markers + 8 VM-mode unittest markers removed; per-mode detail +
methodology — is ✅ DONE; see [claude-todo-done.md](claude-todo-done.md). Two residuals:
- **Cross-mode UNITTEST xfails (17)** — UNSWEPT. The unittest `--check-xpass` (binate
  `ddc624d2`) exists but isn't wired into CI, so the XPASS workflow is conformance-only;
  the 16 arm32-baremetal + 1 arm32-linux unittest xfails need qemu. Sweep by hand, or
  wire unittest `--check-xpass` into CI.
- **`value-struct-large` on `native_x64`** — *not* xfailed there yet crashes (empty
  output) when run; a real missing-xfail or native_x64 bug, surfaced (then masked by a
  substring collision) during the sweep. Worth a look now that `run.sh --exact` no
  longer pulls it into the `value-struct` filter.

### Plan-3 adversarial-review follow-ups (test-hygiene + coverage gaps from `cc2ddcc4` / `997c4c04` / `0c707e1f`) — 2026-06-08
Non-wrong-code items from the adversarial review of the plan-cr2-3 work; each is small. (The live wrong-code findings are the OP_CAST/iface-arg CRITICAL and the float-multi-return MAJOR above.)
- **Weak / over-claimed Defect-6 pin**: the addr-aggregate `global` cells (`997c4c04`) + their generator docstring/README claim to pin "2-word sizing / mis-sized-to-one-word drops a word" — but store+load are width-consistent so the cell is INVARIANT to allocation size (it pins materialization + `__init`-store + read-back wiring, NOT sizing). Fix the docstring (`gen-addr-aggregate-matrix.py:96-104`) / README / commit framing to match. Also Defect 6 closed using only the two shapes that typecheck; readonly-wrapped + named-over-aggregate + raw `*func()` + uninitialized-nil global companions (the Class-A materialization risk in `plan-code-red-2.md`) were left out — record as an explicit deferral (invoking them is blocked upstream at the call typechecker).
- **Coverage gaps**: aa64 per-field iface-multi-return collect (`aarch64_iface.bn:204-228`, the exact loop that dropped sub-word fields) has NO unit test (only conformance on aa64); x64 `collectMultiReturnTuple`-for-iface has no unit test for the IFACE op; an aggregate-component iface multi-return tuple (`(Pair,int)`) is uncovered; the iface-method-arg-with-global position is covered by neither a unit test nor 551/573 (see the CRITICAL entry).
- **Latent fragility (nit)**: `pkg/binate/ir/gen_call.bn` computes `resultTyp` generically and hands it to `EmitCallHandle`/`EmitCallIndirect` (magic-name dispatch) with no structural guard that it isn't a multi-return struct — add a cheap assert so the "these ops never carry a multi-return" invariant is enforced in code, not convention.
- **Discovery**: 2026-06-08, adversarial multi-agent review of plan-cr2-3 work (6 reviewers → adversarial verify → completeness critic; 21/23 findings confirmed).

The code-red conformance-matrix family (`conformance/matrix/`, see
`plan-code-red.md` §7) has four members realized: `refcount` (Class 1),
`scalar` (Class 5), `abi` (Class 4), `const` (named-constant invariant). These
are the remaining matrix-shaped classes not yet built as their own matrix —
candidates for after the loose-axis finish (const-expr folding + ABI
`handle`/`__c_call` shapes).

### (b2) Lifecycle matrix — Class 6 (`@Iface` / `@[]@I`) + Class 7 (captured-`@func` over-release) — PARTLY ADDRESSED 2026-06-05 (plan-cr-p2-2 step 5)
- **Status**: the existing `conformance/matrix/refcount` form × type grid already
  covers Class 6's construction/consumption shapes (the copy-sites are now uniform
  after the `emitStoreManagedSlot` consolidation), and `604`/`605` add lifecycle-
  DEPTH balance (a value chained through param/store/pass/return/bind/invoke) for
  captured-`@func` and cast-from-impl `@Iface`, green in builder-comp/-int/-comp/
  native-aa64. REMAINING: a true single-program **Class 7 native↔VM trampoline**
  balance test is not expressible in the single-mode conformance harness (each
  test runs in one mode) — needs a cross-mode harness; left as a follow-up.
- **Why a matrix**: Class 6 (`@Iface`/`@[]@I` first-class lifecycle) and Class 7
  (native call-a-captured-`@func` over-release via the VM trampoline) are
  lifecycle-completeness classes. Axes would be `managed-kind (@Iface / @[]@I /
  captured-@func) × construction (make / literal / cast-from-impl / capture) ×
  consumption (call-method / index / range / pass / return / discard) ×
  backend`, with a refcount-balance assertion (mortal source).
- **Status**: the refcount matrix already covers `@Iface`/`@func` as value-types
  across assignment-forms, so this would EXTEND rather than start fresh — the
  new axis is construction × consumption depth (esp. the native↔VM trampoline
  path for Class 7, which the refcount matrix does not exercise).
- **Note**: several `@Iface` lifecycle bugs are already filed (leaks/UAF family,
  `@[]@I` literal element leak); a matrix would close the long tail.

### (b3) Class 3 / Class 8 — point-bugs, NOT matrices
- Class 3 (cross-package / interface-name type-resolution ordering → `i8*`
  fallback) and Class 8 (multi-package loader resolution at int-int depth) are
  one-off ordering/loader bugs, not systematic products. Track them as
  individual regression tests under `conformance/regressions/` + filed bugs, not
  as a matrix.

### (b4) Differential harness v3 — port `gen-diff-scalar.py` to Binate (dogfood) + flavor B — NOT STARTED
- **Context**: the property-based differential value-correctness harness
  (`conformance/matrix/scalar-diff`, oracle = spec) is realized through v2 —
  shifts, conversions, arithmetic, comparisons, bitwise; 123 cells / 5415
  tuples; generator `conformance/gen-diff-scalar.py` (Python). See
  `plan-differential-testing.md` (phasing item 3) for the full design.
- **v3 scope** (the remaining phase):
  1. **Port the generator to Binate** — rewrite `gen-diff-scalar.py` as a `.bn`
     program so the harness dogfoods the language on a real codegen-shaped task
     (LCG, two's-complement oracle, bit-pattern formatting). Keep the emitted
     cells byte-identical so the existing `.expected`/`.xfail` set and
     `--check` idempotence carry over unchanged.
  2. **Flavor B (optional, for the highest-volume ops)** — one self-checking
     `.bn` per op that loops an embedded `(inputs, expected)` table and prints
     `mismatch i: got… want…`, denser than the current static-cell flavor A and
     debuggable on failure (flavor A shows *which* tuple, not the wrong value).
     Decide per op once flavor A shows which need the volume.
  3. **Sample-size knob** — a fixed, seeded count parameter so coverage can be
     dialed up without touching the generator logic.
- **Why**: dogfooding is the highest-leverage *process* check (the OOM, the
  `@func`-dtor crash, the shift bug all first surfaced by compiling real Binate
  programs); porting the generator turns the harness itself into one more such
  program. Not urgent — v1/v2 already give the value coverage; v3 is the
  dogfood + debuggability upgrade.

## P3 — low-priority follow-ups

### `os` errors carry only the op, not the failing path (P3)
`pkg/std/os` `failErrno(op)` renders e.g. `"open: not found"`, but
plan-std-error-hierarchy.md §7 specifies context `(path, op)` —
`"open /etc/foo: not found"`. The path is available in `OpenFile`'s `name`
param (Create/Open delegate to it); `read`/`write`/`seek` operate on an fd and
have no path, so op-only is correct there. Add the failing path to the open
family's error context (e.g. a path-aware wrapper, or `failErrno(op, path)`).
Deferred 2026-06-11 (user: op-only acceptable for now) — low impact (message
richness, not classification). Tests: extend the `TestOpen*Classified` cases
to assert the path appears in the rendered message.
