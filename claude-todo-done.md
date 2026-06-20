# Binate TODO — Done

Items moved from [claude-todo.md](claude-todo.md) once fully complete. Active work lives there.

Some older entries reference design/plan docs that have since been archived (see
[historical-notes.md](historical-notes.md)) or removed outright; those filenames may
no longer resolve in the tree, though git history retains them.

---

## same-final-segment generic FUNCS collide at monomorphization (conformance/792) (2026-06-15) — ✅ FIXED (`330c42fe`, 2026-06-20)

**✅ FIX LANDED (`330c42fe`).** The non-generic form was fixed earlier (`e201f448`,
approach B: loader keys import resolution on the full path). The generic-FUNC form
(`bb.Pick[int]` resolving to `aa.Pick[int]` → `100 100` not `100 200` — silent
miscompile) is now fixed: `instantiationMangledName` (gen_generic_mangle.bn)
qualifies the per-(decl,args) symbol by the DEFINING package, not the consumer;
`ensureInstantiated` emits the instantiation `IsLinkOnce` so each consumer TU's
copy MERGES to one (LLVM weak_odr / native SetWeak / VM funcIndex-replace) — the
per-type dtor/copy-helper pattern. Also dedups widely-instantiated generics
(`slices.Append[T]` across ~97 TUs). The checker was already correct (full-path
package resolution; the bug was purely IR-gen mangling). Adversarial review
confirmed ODR-identity (monomorphized bodies are consumer-independent — verified
by cross-consumer byte-diff with conflicting alias maps), name-consistency across
all 5 backends, and no regressions. conformance/792 un-xfailed + renamed (drops
`_xfail`); conformance/867 added (2-consumer ODR-merge guard: two sibling
consumers instantiating the same library generic must merge to one weak symbol).
gen_generic.bn split → gen_generic_mangle.bn (+ sibling test) for length.
builder-comp 1675/0, gen2 (builder-comp-comp) 1675/0, ir unit 558/0, hygiene
15/15. Follow-ups tracked as separate OPEN entries in claude-todo.md: generic-
STRUCT same-segment collision (struct re-key cascades into dtor/copy-helper
naming), and `mangleTypeArg` non-injectivity for func/array/readonly type args.

## Stdlib conformance suite + convert stdlib unit tests to cross-mode conformance tests (2026-06-19/20) — ✅ DONE

The `conformance/stdlib/*` suite is built and EVERY injected stdlib package now
has cross-mode conformance coverage: real `main` programs exercising the stdlib
as it ships — INJECTED (native) — across all modes, the gap the
lowered-to-bytecode unit tests cannot cover. Stdlib unit tests no longer run
under the interpreter modes (`scripts/unittest/run.sh` skips `pkg/std/*` under
`*int*`); their cross-mode coverage is this suite.

Infrastructure: `d05464ce` (discovery — `conformance/stdlib` added to run.sh's
find roots + a path-scoped `conformance-imports` relaxation for `pkg/std/*` under
`stdlib/`), `c8aa8f01` (stop stdlib unit tests under int), `53abd110` (os
wholesale injection — see the os.Seek done entry).

Per-package conformance tests (representative, deterministic subsets stressing
the injection boundary — floats/aggregate-returns through the marshaling shim,
iface dispatch, error sentinel identity; per-function correctness stays in the
unit tests):
- errors — `001` upcast, `002_is_and_chain`, `003_base_hierarchy` (`99f0b385`)
- strconv — int format / parse / FormatFloat g/e/f/specials/f32 (`970b8a77`)
- strings — Builder API + 256-byte fidelity (`c51d2a2d`)
- io — covered by `663_io_iseof` (no new test needed)
- time — negative / pre-epoch (`bf0dcd63`; 855 covers the positive cases)
- math — classify/round + roots/pow/trig + Pi/E/Sqrt2 constants (`1a001c50`)
- math/big — Nat arith / shifts / divmod, multi-return DivMod (`9db20d89`)
- os — file roundtrip, Seek, ReadAt/WriteAt, ReadByte/WriteByte (`d05464ce`,
  `565dc3c8`, `fc10987f`)

`pkg/stdx/slices` is a non-injected generic library — its unit tests still run
under int as real VM coverage; not converted. Optional follow-ups (fold the ~8
ad-hoc stdlib tests; drop the redundant `os_test.bn` `TestErrorIfaceUpcast`)
remain tracked in claude-todo.md.

## MAJOR (native x64 codegen) — C-extern calls emit a non-PIC relocation → PIE link failure on `builder-comp_native_x64-comp_native_x64` (2026-06-18) — ✅ FIXED reloc-level (`7976cb8f`, 2026-06-19); 🟡 CI to confirm the native_x64 link+run

**✅ FIX LANDED (`7976cb8f`).** `pkg/binate/asm/elf` now emits `R_X86_64_PLT32` (=4) for a `FIX_REL32` CALL/JMP against an UNDEFINED symbol (`asm.Symbol.Section < 0`), keeping `R_X86_64_PC32` for a DEFINED (same-object) target; PLT32 shares PC32's end-of-field semantics so `elfRelocAddend` applies the same −4. `elfRelocType` gained a `symUndefined` param, passed from the single `elf.bn` reloc-loop call site. This fixes not just the libc externs but ALL cross-object calls (e.g. `bootstrap.Write`), which were equally PC32-against-undefined. Scope: `FIX_REL32_LEA` (RIP-relative data) targets only local defined labels → stays PC32, no GOTPCRELX needed (no extern data referenced). AArch64 (ELF JUMP26 / Mach-O BRANCH26) already PLT-veneers undefined calls → unaffected; Mach-O / LLVM / non-PIE static links untouched (`ld` relaxes PLT32→direct for non-PIE). **Validated locally (darwin):** regenerated `498` object flips the libc `abs` + cross-object bootstrap calls PC32→PLT32 while same-object calls + the string LEA stay PC32; `elf` unit tests +4 isolated-mapper (PLT-vs-PC for undefined/defined, LEA-stays-PC32, AArch64-unchanged, PLT32 addend −4) + 1 end-to-end (`6f2e64a7`: `TestWriteElfX64RelocPltVsPc` builds an object with a defined + an undefined CALL, writes/reads it back, parses `.rela.text` against `.symtab`, asserts undefined→PLT32 / defined→PC32 — covers the call-site predicate the isolated tests can't; confirmed to fail if the predicate is inverted); gen1/gen2 self-host green; hygiene 15/15. **🟡 NOT yet verified end-to-end:** the full PIE link+run of the 10 c-call tests is Linux-only (dev box is darwin), so CI confirms the native_x64 mode actually goes green. The fix is self-contained at the object level (the linker synthesizes the PLT entry — no backend GOT setup), so the residual risk is low. The 10 c-call tests carry NO native_x64 xfail (so they'll surface green/red directly in CI — no marker to flip). Original diagnosis below.

The hand-written x64 native backend's C-extern call path emits a direct PC-relative relocation (`R_X86_64_PC32`) against external libc symbols (`printf@@GLIBC_2.2.5`, `abs`, `strlen`, `labs`, …) instead of going through the PLT / a GOT-relative (`R_X86_64_GOTPCRELX` / `@PLT`) relocation. The linker, producing a PIE, rejects it: `relocation R_X86_64_PC32 against symbol printf@@GLIBC_2.2.5 can not be used when making a PIE object; recompile with -fPIE`. Every C-interop conformance test fails to **link** (COMPILE_ERROR) in the native_x64 conformance mode: `498_c_call_basic`, `500_c_call_variadic`, `527_c_call_variadic_multi`, `530_c_call_variadic_stack`, and `regressions/c-call/{abs-negative,abs-positive-zero,labs,printf-variadic-float,printf-variadic-int,strlen}`. These tests PASS on LLVM and the other modes — it is x64-native-specific. It is NOT a 64-bit-integer or int-width-print issue (`499_int64_arith` / `507_int64_min` PASS in the same job). C-interop is the project's one sanctioned use of C (syscalls), so this blocks the native_x64 C-free-with-libc-shim path. Fix: emit a PLT/GOT-relative relocation (or call through the GOT) for extern symbols in the x64 backend's call lowering; the AArch64 backend likely needs the same audit. The failing tests already exist (they fail only in native_x64); xfailing them in that mode is part of the deferred cross-compile-CI-cleanup (the conformance CI has been red for 11+ commits, mostly on NON-32-bit-clean tests — a separate effort).

## MAJOR (VM interop / injection) — `os.Seek`/`ReadAt`/`WriteAt` abort under int even from a REAL program importing injected os (2026-06-19) — ✅ FIXED (`53abd110`, 2026-06-19)

**Symptom (PROVEN).** A standalone `main` importing os and calling `g.Seek(...)`
aborted under the int modes with `vm: extern not found: pkg/std/os.cLseek` —
NOT a unit-test artifact (an earlier note wrongly claimed that): the conformance
test `conformance/stdlib/os/002_seek` is a real program with os injected and it
still aborted. `builder-comp` (all-native) passed.

**Root cause.** os was special-cased OUT of the injected-stdlib set
(`cmd/bni/externs.bn`'s `nativeOnlyStdPkgs`): every other pkg/std package was
injected WHOLESALE (lowered-skipped), but os was LOWERED with a per-function
`__c_call` skip so its pure-Binate funcs (and Test*) ran as bytecode. Then
`os.Seek`/`ReadAt`/`WriteAt` — which have no DIRECT `__c_call` (they route
through the unexported off64_t wrappers `cLseek`/`cPread`/`cPwrite`) — were
lowered (`funcHasCCall` is DIRECT-only), and the lowered bytecode `Seek` SHADOWS
the injected native `Seek` in the VM funcIndex, then can't reach `cLseek` (a
`__c_call`, not lowered; unexported, so absent from the exported-only
`_Package()` descriptor → not injected). The `stdlib-injected.sh` hygiene check
had a mechanism-2 carve-out that BLESSED exactly this lowered+injected hybrid, so
it enforced "registered" rather than "actually injected / un-shadowed" and passed
os while os.Seek was broken.

**Fix.** Treat os like every other native-only stdlib package: add it to the set
(renamed `nativeOnlyStdPkgs` → `stdPkgs`, since with os it is simply every
pkg/std package), drop the special-case `os._Package()` injection, and drop the
hygiene check's mechanism-2 carve-out (now one rule: every pkg/std package must
be in `stdPkgs` — strictly stronger). os is now injected wholesale and never
lowered as a dependency, so `os.Seek` dispatches to the linked native impl, which
calls `cLseek` internally (no VM boundary); `cLseek` needs no injection. Paired
with stopping stdlib unit tests under the interpreter modes
(`scripts/unittest/run.sh` skips `pkg/std/*` under `*int*`) — os-as-test-target
would now be lowered-not-injected under int — with cross-mode coverage moving to
the new `conformance/stdlib/*` suite that caught this. `os/002_seek` un-xfailed.

**Landed** (`d05464ce` suite + import relaxation; `565dc3c8` os/002 xfail;
`c8aa8f01` stop stdlib unit tests under int; `53abd110` os wholesale injection +
un-xfail). **Validation**: conformance/stdlib green on all 6 default modes; full
conformance `builder-comp-int` 1595/0; os unit tests pass under `builder-comp`
and are skipped under int; cmd/bni unit tests pass both modes; hygiene 15/15
(`stdlib-injected` now enforces os ∈ `stdPkgs`).

## MAJOR (type-checker / .bni loader) — a free function and a same-named METHOD in one package collide: the .bni→scope loader registers methods as package-scope func symbols, so `func Stat(...)` + `func (f *File) Stat()` fails ".bn has 1 parameters but .bni declares 0" (2026-06-19) — ✅ FIXED (`796effc7`, 2026-06-19)

**✅ FIX LANDED (`796effc7`).** Adversarial review found the receiver-blind name-match was a FAMILY of three sites, all fixed together: (1) `bni_scope.bn` `buildScopeFromFile` now guards `defineFunc` with `if d.Recv == nil` (methods live in the receiver type's method set, like the sibling `collectDeclNames` already does); (2) `loader/loader_util.bn` `markBniExportedFuncs` and (3) `loader/loader.bn`'s `.bni`-extern merge `hasImpl` gate now use a shared receiver-aware `sameFuncDecl` matcher (free↔free, method↔method-with-same-receiver). Site (2) had been marking only the FIRST name-matching merged decl `Exported`, silently dropping the other of `os.Stat`/`File.Stat` from the reflect Functions table (→ broken in VM/int modes). Six regression tests, each confirmed fail-without/pass-with: `bni_scope_test.bn` (method-not-in-scope, free+method-coexist, method-doesn't-shadow-const), `checker_test.bn` (end-to-end CheckPackage reproducing the verbatim error), `loader_util_test.bn` (sameFuncDecl matrix, free+method export, methods-different-receivers export). Full unit suite 46/0, hygiene 15/15.

End-to-end cross-mode coverage added separately: `conformance/865_free_func_same_named_method` (`d860c6e0`) compiles & runs a real program — importer calls both the free `stat.Stat(41)`→42 and the method `info.Stat()`→5 — across native, the bytecode VM, and gen2 self-host, exercising IR-gen/codegen/dispatch that the checker-level tests don't.

**Symptom (REPRODUCED, builder-comp).** Declaring both a free function and a method of the SAME name in one package — e.g. `func Stat(name *[]readonly char) (@FileInfo, @errors.Error)` and `func (f *File) Stat() (@FileInfo, @errors.Error)` (the standard Go `os.Stat` + `File.Stat` pattern) — fails type-checking at the free function: `stat.bn:85:1: Stat: .bn has 1 parameters but .bni declares 0`. The free `Stat` (.bn, 1 param) is matched against the METHOD's signature (0 params), not its own `.bni` declaration. Renaming the method makes it compile (confirmed).

**Root cause.** `buildScopeFromFile` (pkg/binate/types/bni_scope.bn:210-213) loads a `.bni`'s declarations into the package scope and registers EVERY `DECL_FUNC` via `defineFunc(s, d.Name, ft)` with no `d.Recv != nil` skip. So a `.bni` method `(f *File) Stat()` is registered as a package-scope function symbol `Stat` (0 params — the receiver isn't in `Params`), colliding with the free `Stat`. The checker's own pass-1 (check_decl.bn:201-208) correctly routes methods to `collectMethodDecl` vs free funcs to `defineFunc`, and the sibling `collectDeclNames` (bni_scope.bn:510) correctly skips methods (`if d.Kind == ast.DECL_FUNC && d.Recv != nil { continue }`) — `buildScopeFromFile` is just missing that same guard. Methods are registered on their receiver type's method set elsewhere, so the `defineFunc`-on-method at :212 is a spurious, wrong extra registration.

**Latent risk (why MAJOR).** Beyond the loud blocking error, this pollutes the package value-namespace with method names from every loaded/imported `.bni`. A method name colliding with an imported free function, const, or type could shadow it and silently resolve the WRONG symbol. The `os.Stat` case surfaces it loudly; other shapes might not.

**Discovery.** Implementing `os.Stat` (free) + `File.Stat` (method) for plan-os-stat Stage 4 — the Go-parity pattern the user explicitly requested.

**Proposed fix.** Add `if d.Recv != nil { continue }` to the `DECL_FUNC` branch at bni_scope.bn:210-213 (mirroring collectDeclNames:510). Add a types unit test: a package whose `.bni` has both a free func and a same-named method type-checks clean. Verify gen2 self-host + conformance stay green (touches the compiler's own `.bni` loading).

**Blocks.** plan-os-stat Stage 4 (`os.Stat` + `File.Stat`). The only workaround is renaming one entry point (deviates from the requested Go parity) — NOT taken, pending this fix.

---

## MAJOR (IR-gen / cross-pkg generics, ALL modes) — a cross-package generic body that INSTANTIATES another package's generic fails when the consumer imports neither: transitive generic DECLS aren't registered for monomorphization (2026-06-19) — ✅ FIXED (`dfe60903`, 2026-06-19)

**✅ FIX LANDED (`dfe60903`, 2026-06-19).** Three pieces, all driven over the transitive closure of generic-bearing loaded packages (in both cmd/bnc and cmd/bni): (1) `ir.RegisterGenericDecls` stashes every loaded package's generic func/struct/interface decls (full-path keyed, deduped) — in-memory only, so broad is cheap — so the consumer can monomorphize a generic it reaches through another package; (2) `registerGenericBodyExternDeps` broadened from the consumer's DIRECT generic-bearing imports to ALL generic-bearing loaded packages (with a selfPath skip), so a call ≥2 levels down (deep.G) gets its extern; (3) `ir.RegisterImportedImpls` collects the transitive packages' impls into `m.ImportedImpls` (deduped via `moduleHasImportedImpl`), so a generic body that UPCASTS to a transitive-package interface gets the impl's vtable wired. Piece (3) was added after an adversarial review found that pieces (1)+(2) alone turned the transitive-iface-upcast case from a compile-error into a NIL-VTABLE SIGSEGV (silent miscompile) — the review caught it before landing, and (3) closes it. Conformance `858` (func chain), `860` (transitive interface upcast — was the SIGSEGV), `861` (transitive generic struct) all pass; all modes green incl. gen2 self-host (1578/0).

**Symptom (REPRODUCED, builder-comp + builder-comp-int).** `main` imports only `genlib`; `genlib.Sum[T]` (generic) calls `mid.F[T]` (mid's generic), which calls `deep.G()`.  `main` cannot monomorphize `mid.F[int]` because `mid`'s generic decl is not registered in the consumer (`mid` is a TRANSITIVE, not direct, import), so the inner instantiation resolves to an EMPTY mangled name: native `use of undefined value '@bn_main__'`, VM `extern not found: main.`.  Conformance `858_generic_cross_pkg_generic_chain` (now PASSING).

**Root cause / relationship to part (c) (`fcb0dcbc`).** Part (c) registered transitive func EXTERNS so a generic body's call to a NON-generic transitive func links (`ir.RegisterFuncExterns` for generic-bearing direct imports' imports).  But generic DECLS are stashed only for DIRECT imports (`RegisterImports`); `RegisterFuncExterns` skips generic decls.  So when a generic body instantiates ANOTHER package's generic that the consumer doesn't import, the consumer has no decl to monomorphize → empty instantiation name.  This is a deeper, distinct gap (transitive generic-DECL availability) on top of the transitive-extern (func-call) fix — both are Slice 7 (cross-package instantiation) completeness.

**Discovery.** Investigating the part-(c) one-level-transitivity limitation (the consumer registers the imports of generic-bearing DIRECT imports, but not transitively).  Confirmed real via the 3-package chain above.

**Fix status.** FIXED — see the ✅ FIX LANDED block above (`dfe60903`).

---

## Done

## MAJOR (LLVM codegen) — a default-init SCALAR local was not zero-initialized → STACK GARBAGE — ✅ DONE & LANDED `2d856a0f` (2026-06-19)

A scalar local without an initializer (`var s int`) was not zeroed on the LLVM backend: codegen `emitAlloc` only zero-fills struct/array OP_ALLOCs, and IR-gen's var-decl default-init path stored zero only for `needsStructCopy` (managed-containing) types — a plain scalar got a bare alloca, so a read returned stack garbage.  Violates spec `decl.var.zero-init` (a positive rule: numeric → 0, bool → false) and diverged from the bytecode VM (which zeroes scalars).  Pre-existing; surfaced by an adversarial review of the named-array zero-init fix (`77bdd64c`), whose comment had wrongly ratified leaving scalars un-zeroed.  Fix: IR-gen's default-init path now emits a typed zero store (EmitConstBool/Float/Int) for a SCALAR local — fires only for the no-initializer case (no double-zero of an initialized var), putting the store in the IR so both backends agree.  Conformance `863` (a `dirty()` call writes non-zero to the frame first, then reads default-init int/bool/char/int8 → all 0); LLVM + VM + gen2.

## (h-cmp gap) — a const unsigned-≥2^63 compare nested in &&/|| folded SIGNED — ✅ DONE & LANDED `6aaba642` (2026-06-19)

The h-cmp fix (`0625521f`) stamps a directly-foldable const relational compare of unsigned operands ≥ 2^63 so it folds unsigned, but a comparison nested in `&&`/`||` with an UNFOLDABLE bool const ident (`const C bool = BT && (U > 5)`) bailed the checker's `foldConstBoolValue` (a bool ident has no recorded value) → no stamp → IR-gen's `evalConstBool` re-folded the WHOLE expression, including the comparison, with a signed host-int compare → folded false (should be true), on both backends.  Fix: `evalConstBool`'s relational arm now compares UNSIGNED when an operand resolves to an unsigned integer type (new `constOperandIsUnsignedInt`, recursing through unary/binary operators).  EQ/NEQ are signedness-independent.  Found by an adversarial review of the four h residuals.  Conformance `840` (expanded: `BT && (U>5)`, `(U>5) && BT`, `BF || (U<5)`); LLVM + VM + gen2.  Rare residual: a pure untyped-2^63-literal comparison nested in `&&` (the direct form is stamp-folded).


## CRITICAL (IR-gen / silent miscompile, ALL modes) — a GENERIC struct/interface's qualified by-value field/method TYPE binds to the CONSUMER's same-named import when the consumer imports a DIFFERENT package under the SAME short alias the generic uses → wrong layout; checker and IR-gen DISAGREE (2026-06-19) — ✅ FIXED (`83971b2e`, 2026-06-19)

**✅ FIX LANDED (`83971b2e`, 2026-06-19).** `overlayFileImports` (`gen_register_import.bn`) now treats the overlaid file's own imports as AUTHORITATIVE: it drops any base entry whose alias the file rebinds (new `fileRebindsAlias`) before recording the file's imports, into FRESH arrays so the caller's restore snapshot is untouched. The generic-instantiation sites pass a use-site `base` carrying the consumer's short aliases; the drop lets the defining file's `dep` win over a colliding consumer `dep`. A no-op for the per-decl callers in GeneratePackage (their base is the clean post-RegisterImports snapshot). IR-gen now agrees with the (already-correct) checker. Conformance `850_generic_cross_pkg_alias_collision` (the sizeof shape) is a PASSING regression test (native + VM); all 3 native self-host gens 1506/0; 558 ir + 778 types unit. Found by adversarial review of `d2a9ff20`.

**Symptom (REPRODUCED + independently confirmed, builder-comp + builder-comp-int).** `main` imports `pkg/other` AS `dep`; `pkg/genlib` imports `pkg/dep` AS `dep` and declares `type Box[T] struct { tag T; p dep.Pair }` (genlib's `dep.Pair` = `{x,y}` = 16B). `main` does `println(cast(int, sizeof(genlib.Box[int])))` → printed **32** (consumer's `pkg/other.Pair` = `{pad,x,y}` = 24B, + tag 8 = 32) instead of **24** (genlib's `pkg/dep.Pair`, + tag 8 = 24). The instantiated `Box[int]`'s `p` field bound to the WRONG package. Conformance `850_generic_cross_pkg_alias_collision` (now PASSING; the defect was in the shared IR-gen layout, so it failed native AND VM).

**Root cause.** `ensureInstantiatedStruct` / `ensureInstantiatedInterface` / `ensureInstantiated` (`gen_generic.bn`) overlay the generic's defining-file imports via `overlayFileImports(SaveAliasMapState(), defFile)`. That `base` is captured at the USE site, where (during GeneratePackage body-emit) the CONSUMER decl's file overlay is active — so `base` still carries the consumer's short alias `dep`. `overlayFileImports` does `RestoreAliasMapState(base)` then `RecordImportPath(alias, path)` for the defining file's imports — and `RecordImportPath` is **FIRST-WINS** (`gen_import.bn:144`), so the defining file's `dep` no-ops because the consumer's `dep` is already present. This violates `overlayFileImports`' own documented contract (`gen_register_import.bn`: "base must be the post-RegisterImports snapshot with NO short aliases"). The CHECKER is correct (`populateInstantiated*` use `c.Scope = NewScope(defScope)` — full chain replacement, no first-wins merge), so **checker and IR-gen now DISAGREE** on the same instantiated type's layout — the checker types the field as `pkg/dep.Pair` (24) while IR-gen lays it out as `pkg/other.Pair` (32).

**Relationship to `d2a9ff20`.** The collision sub-case was already wrong pre-`d2a9ff20` (IR-gen resolved the body fully at the use site → `pkg/other.Pair`), but so was the checker — they AGREED (both 32, self-consistent). `d2a9ff20` fixed the checker (and the IR-gen ABSENT-import case, conformance 841) but its IR-gen overlay does not close the COLLISION sub-case, and it makes the checker correct → the two now DISAGREE, which is more dangerous (the checker blesses code IR-gen miscompiles). Also affects the implicit same-last-segment alias case (two packages whose path ends in `/dep`).

**Discovery.** Adversarial multi-agent review of `d2a9ff20` (2026-06-19); the alias-map-overlay risk dimension I had flagged before landing. Empirically reproduced (sizeof shape; the Make/Total routing shape PASSES despite the bug — the wrong layout is used self-consistently and cancels, so it is NOT a valid guard; only the sizeof shape exposes it).

**Fix (landed) — see the ✅ FIX LANDED block above.** The chosen approach was the "drop the colliding base alias so the defining file rebinds" variant (in `overlayFileImports` itself, so it is robust for all callers), not threading a separately-cleaned base. Residual follow-up: a `pkg/binate/ir/gen_generic_test.bn` unit assertion that the instantiated field's @Type package path is the generic's (not the consumer's) would lock the invariant at the unit level (tracked under coverage backfill).

---

## MAJOR (IR-gen / cross-pkg generic interfaces, ALL modes) — a cross-package generic interface with a LIBRARY-SIDE impl cannot be upcast at the consumer → nil interface value (raw) / `extractvalue` compile error (managed) (2026-06-19) — ✅ FIXED (`6b59c6bb`, 2026-06-19)

**✅ FIX LANDED (`6b59c6bb`, 2026-06-19).** Instantiated generic interfaces are now keyed on their DEFINING package (where the generic iface decl lives), not the consumer's `gc.PkgPath` — so a library-side impl's vtable symbol matches between the library TU (which emits it, weak_odr) and consumers (which reference it).  Re-keyed at all sites that must agree: `ensureInstantiatedInterface` (the `definingPkg` param drives the dedup lookup, `mi.Pkg`, and the recorded parent pkg), `ifaceTypeForName`'s TEXPR_INSTANTIATE branch, and `collectImplsFromDecl`'s instantiate branch (`refIfacePkg = lookupPkg`, the change that keeps consumer-side `464` working).  `collectImportedImplsFromDecl` was threaded `gc` and extended to handle TEXPR_INSTANTIATE so the consumer registers the imported generic-instantiation impl and emits the matching `external` vtable decl.  The old consumer-pkg keying guarded against a cross-consumer link conflict that is moot (instantiated vtables are weak_odr single-owner; consumer-side impls differ by receiver pkg).  Conformance `854_generic_cross_pkg_iface_qualified_method` now PASSES (native + VM); all modes green incl. gen2 self-host (1575/0); 464/768/769/451/452/726 unaffected.  Plan adversarially reviewed (the initial 2-site plan would have regressed 464).

**Symptom (REPRODUCED, builder-comp + builder-comp-int).** `pkg/iflib` declares `interface Box[T any] { wrap(v T) ... }`, a struct `IntImpl`, and `impl IntImpl : Box[int]` (the impl lives in iflib, the interface's own package). A consumer `main` (importing only iflib) does `var bx *iflib.Box[int] = &m` (m an `iflib.IntImpl`) and dispatches → VM: `call through nil interface value`; native: no output (nil dispatch). The `@Box[int]` (managed) form instead fails to compile: `extractvalue operand must be aggregate type` (the upcast does `extractvalue i8* <ptr>, 0`, treating the receiver pointer as an aggregate). Conformance `854_generic_cross_pkg_iface_qualified_method` (now PASSING).

**Trigger isolated.** The LIBRARY-SIDE impl is the trigger, NOT qualified method types: it reproduces with an iflib-LOCAL struct result too. `464_cross_pkg_generic_iface` works because its `impl` is CONSUMER-side (in main), so main emits the vtable; here the consumer must find/wire iflib's vtable for the (IntImpl, Box[int]) instantiation and does not. Independent of the d2a9ff20/83971b2e qualified-import fixes (those are about defining-file import resolution; this is impl/vtable wiring for a cross-pkg generic-interface instantiation — Slice 7c completeness).

**Discovery.** Building the interface-path coverage test (review w5ck1416h item B2) for the qualified-method-type overlay; the test couldn't reach the overlay because the upcast failed first. `854` uses a qualified result (`dep.Tag`), so it ALSO now covers the generic-interface qualified-method-type overlay (the interface counterpart of `841`/`850`/`853`) — both the upcast wiring AND the overlay are exercised, and it passes.

---

## CRITICAL (checker + IR-gen / wrong-code) — a GENERIC decl's body resolves its package-qualified references against the INSTANTIATION SITE's scope, not the generic's defining-file imports → dangling symbol / wrong-package layout corruption (2026-06-18) — ✅ FULLY FIXED: qualified-TYPE facet (`d2a9ff20`) + transitive-extern func-call (`fcb0dcbc`), 2026-06-19

**✅ PART (c) FIXED (`fcb0dcbc`, 2026-06-19) — transitive-extern func-call.** A generic body that CALLS a function from a package the consumer doesn't import (`dep.V()`) now gets that callee registered (sig + extern Func) in the consumer's module via new `ir.RegisterFuncExterns`, driven by `registerGenericBodyExternDeps` (cmd/bnc/compile_imports.bn AND cmd/bni/irgen.bn) for the imports of any directly-imported package that contains generic decls.  NOTE: this was NOT native-only — the earlier framing was wrong.  837 passed in the VM only because `dep.V` returns a SCALAR (the IR-gen sig guess happened to match); a STRUCT-returning transitive call (conformance `856_generic_cross_pkg_body_call_struct_ret`) was miscompiled in the VM too (guessed scalar → garbage).  Registering the REAL sig fixes both: native links the declare, the VM lowers correct aggregate handling.  `837` now passes in ALL modes (8 native xfail markers removed); `856` is the struct-return regression guard.  All 3 modes 1555/1540 green incl. gen2 self-host.

**✅ FIX LANDED (`d2a9ff20`, 2026-06-19) — qualified-TYPE facet.** Both layers now record each generic decl's defining-file import scope/AST file (checker: `GenericTypeDeclScopes`/`GenericIfaceDeclScopes`; IR-gen: `@GenCtx.GenericTypeDeclFiles`/`GenericIfaceDeclFiles`/`GenericDeclFiles`) parallel to the decl registries, and overlay it while resolving the instantiated body: checker `populateInstantiatedStruct`/`populateInstantiatedInterface` resolve under `NewScope(defining-file scope)` with type-params layered on top; IR-gen `ensureInstantiatedStruct`/`ensureInstantiatedInterface` (and the func path `ensureInstantiated`) overlay the defining file's imports onto the alias map. Also fixed a latent lockstep bug (`collectInterfaceDecl` appended to `GenericIfaceDecls` without a parallel scope → out-of-bounds in `lookupGenericDeclScope` for any same-package generic interface). Conformance `841_generic_cross_pkg_struct_field` covers the cross-package generic-struct qualified-field-type case (fails without the fix: `undefined: dep`). All modes green; 558 ir + 778 types unit tests.

**Part (c) historical detail (now fixed — see the PART (c) FIXED block above).** A generic body that CALLS a function from a package the consumer does not import (`dep.V()`) needs that callee declared (LLVM `declare`) as an extern in the consumer's module; before `fcb0dcbc` no transitive-extern declaration was emitted, so `main.ll` referenced an undeclared `@bn_pkg__dep__V` (LLVM "use of undefined value"). The qualifier already resolved to the CORRECT package (`@bn_pkg__dep__V`, not the old wrong `@bn_dep__V`) — only the declare was missing in native, and the VM lowered a guessed (scalar) signature.

**Symptom (REPRODUCED, builder-comp gen1).** `pkg/genlib.bni`: `import "pkg/dep"; func Id[T any](x T) T { var unused int = dep.V(); return x }` (generic body lives in the `.bni`, references genlib's own `dep`). `main` imports `pkg/genlib` but NOT `pkg/dep`, and calls `genlib.Id[int](42)`. The generic body is monomorphized AT the call site and `dep.V()` resolves against MAIN's scope/alias-map (where `dep` is unknown) → LLVM `error: use of undefined value '@bn_dep__V'`; the checker does NOT catch it. For a generic body with a qualified FIELD TYPE (`type Box[T] struct { tag dep.Thing }` where two packages define different-layout `Thing` under the same alias across files), the same defect binds the WRONG package → wrong struct/field GEP = **memory-layout corruption** (the review built this and read a field absent from the declared struct). [The FIELD-TYPE half is the part fixed by `d2a9ff20`; the func-CALL half remains as part (c) above.]

**Root cause.** Non-generic decls are resolved EAGERLY at collection under their own file scope (`check_decl.bn` per-decl `c.Scope = scopeForFile`). Generic decls early-return at collection (`check_type_redecl.bn:140-142`, `if len(d.TypeParams) > 0 { return }`) — their body stays AST-form and is resolved LAZILY at the instantiation site. The checker's `populateInstantiatedStruct`/`populateInstantiatedInterface` (`check_generic_type.bn:183,303`) only `pushScope(c)` on top of the USE-SITE's `c.Scope`, then `resolveStructType`/`resolveFuncDeclType` → a qualified ref hits `resolveNamedTypeExpr` → `c.Scope.Lookup(te.Pkg)` (`resolve_type.bn:135`) = the consumer's file scope, never the generic's defining-file imports. IR-gen matches: `ensureInstantiated*` (`gen_generic.bn`) resolve the body under the consumer's alias map (no defining-file overlay; the non-method struct path doesn't even thread `currentImportAlias`). Both layers mis-resolve identically → silent miscompile, not an ICE.

**Discovery.** Adversarial review of the file-scoped-imports work (2026-06-18). `cf0d1cad` made NON-generic decls file-scoped and claimed facets A/B/C/E fixed; the review checked the generic sub-case (a deliberately-flagged risk area in `plan-file-scoped-imports.md`) and found it still broken — independently reproduced end-to-end with a non-generic control that correctly rejects, isolating the defect to the generic path.

**Pre-existing, NOT a regression from cf0d1cad.** Generic instantiation always resolved the body under the consumer's scope (pre-fix: consumer's package scope; post-fix: consumer's file scope) — never the generic's own. So `cf0d1cad` did not introduce it, but it did NOT fix it and the entry below overclaimed A/B/C as fixed without the generic caveat (now corrected). Distinct from `792` (same-final-segment monomorphization-KEY collision).

**Fix status.** FULLY FIXED.  The type-facet fix (record the defining-file scope/file and overlay it during instantiated-body resolution, type-ARGS still in the consumer's context) landed in `d2a9ff20`; the transitive-extern func-call piece (part c) landed in `fcb0dcbc`.  `837` passes in all modes; `841`/`850`/`853` (struct types) and `856` (transitive struct-returning call) are the regression guards.

## MAJOR (IR-gen) — a FORWARD-REFERENCED const used as an array dimension miscompiled to a GARBAGE size — ✅ DONE & LANDED `02cf6c03` (2026-06-19)

A const used as an array dim folded to a garbage size whenever IR-gen resolved the dim before registering the const it references. Adversarial review found it was BROADER than first recorded: (a) same-module GLOBAL var / TYPE ALIAS dims (`var g [N]int` … `const N int = 8` → `[30 x i64]` from `parseIntLit("N")`); (b) — worse — imported `.bni` struct fields / aliases, where the IMPORTER laid the type out at 30 words while the OWNER used 8: a checker-LEGAL (const-before-type, not even a forward ref) silent cross-package struct-layout / ABI divergence; and (c) for NESTED garbage dims the size blows up multiplicatively (`[ROWS][COLS]` ≈ 37525·19585) so the miscompile also OOMs the compiler building the zero-initializer (40 GB+), not only mis-sizes it. Root cause: IR-gen's `resolveTypeExpr` re-folds the dim against `gc.Mod.Consts`, which the global-var / type-alias pass and the four import-registration paths populate AFTER the use site. **Fix (root-cause):** array layout is a language-level contract the checker already computes correctly — so the checker now STAMPS the resolved length on the dim's AST node (`TypeExpr.LenVal` / `LenKnown` in `ast.bni`, set in `types/resolve_type.bn`), and IR-gen's `resolveTypeExpr` (`ir/gen_type_resolve.bn`) reads the stamp instead of re-folding. Every array-dim resolution (same-module + all four import paths) funnels through that one function, so this corrects all of them in one place. A REJECTED earlier approach — reorder IR-gen to register consts before type-aliases — degraded a named-scalar-typed const's declared type to int (`type U8 uint8; const B U8 = 200` → `i64`, breaking conformance `651`); the stamp avoids any reordering. Tests: conformance `849` (same-module global / alias / const-group / const-expr / nested / sizeof-of-struct; nested dim consts use single-char names so a regression's garbage stays bounded, not OOM), `847` (cross-package imported field + alias), `848` (cross-package transitive `[a.N]int` selector). Verified: full builder-comp 1508/0, gen2, VM, 10-package unit smoke, adversarial review ("no defect in the fix itself").

## MAJOR (codegen) — a local of a NAMED ARRAY TYPE was not zero-initialized → reads of unset elements returned STACK GARBAGE — ✅ DONE & LANDED `77bdd64c` (2026-06-19)

`type Arr [8]int; var a Arr; a[0] = 1; println(a[3])` printed stack garbage instead of `0`; the anonymous-type form (`var a [8]int`) zero-inited correctly. Memory-unsafe. Pre-existing (reproduced identically pre/post the array-dim-stamp fix), surfaced during that fix's review. Root cause: `emitAlloc` (`pkg/binate/codegen/emit_helpers.bn`) gated the fieldwise zero-fill on `instr.TypeArg.Kind == TYP_STRUCT || == TYP_ARRAY` **unpeeled** — a named distinct type over an array has Kind `TYP_NAMED`, so it returned early and emitted no zero-stores. (A named STRUCT was fine — named structs are Kind `TYP_STRUCT` two-word; only named-distinct-over-array leaked.) **Fix:** peel `TYP_NAMED`/`TYP_READONLY` before the kind check (fire only for a struct/array underlying, keeping a named SCALAR consistent with an anon scalar), mirroring the peeling `emitZeroRec`/`needsStructCopy` already do — `emitAlloc` was the lone unpeeled site. The zero-fill callee already peels, so it lowers the named array once invoked. LLVM-backend-only; the bytecode VM zero-inits the named-array local correctly (conformance `852` passes unchanged in builder-comp-int). Test: conformance `852_named_array_zero_init` (read unset elements of a named array + a named-over-named chain → 0); existing `723` set every element so never exercised default zero-init.

## opaque-layout — opaque types with no layout (forward decl, no body) — ✅ DONE & LANDED (2026-06-16 → 2026-06-19)

An opaque type's SizeOf/AlignOf fabricated ptrSize, so anywhere its layout was
needed the compiler silently emitted i64 — wrong code, and for generics a
mangled-symbol collision (`Box[Opaque]` shared `Box__bn_inst__int` with a real
`Box[int]`).

Step 1 — removed the (source-reachable, crash-regression) SizeOf/AlignOf panic;
const-fold / bit_cast checker callers give clean diagnostics; named-distinct
peel + value-embedding gates. Commits: ffc56b36, 26f6e5b3, f3807ed2, e887543e.

Step 2 — "an opaque value can never be FORMED", enforced ENTIRELY in the checker
(IR-gen's precondition is valid input, so the fix belongs there — not an IR-gen
guard). Slices:
  - 1  `2e979554` embedsOpaqueByValue recurses struct fields.
  - 1b `1c40ba52` slice-of-opaque, cycle-aware (per-branch visited set; a naive
       recursion hung on a recursive managed type `struct { kids @[]Node }`).
  - 2  `b7cbedaa` make/make_slice/sizeof/alignof gates use embedsOpaqueByValue
       (the dedicated generic gate was found redundant + double-erroring).
  - 3  `40924b14` generic function / interface instantiation gates.
  - 4  `6d541973` composite-literal + inferred-var gates.
  - 4b `fe048395` deref-as-rvalue assignment gate (`_ = *p`, `*dst = *src`).
  - 5  `5968e1e2` REPL CheckDeclInScope hook (batch + REPL both enforced).
  - 6  `d00fcd81` reject `*Box[Opaque]` / `@Box[Opaque]` (a pointer to an
       opaque-embedding non-bare type) — the last path reaching IR-gen with an
       opaque arg; closing it means IR-gen never instantiates `Box[Opaque]`.
  - nested-pointer closure `13943373` — pointeeEmbedsOpaque peels through
       every pointer level (`**Box[Opaque]` / `@(*Box[Opaque])`), with a
       visited-name set so `type P *P` terminates.
conformance/{809,824,827,828,838,839,842,846,851} + extensive unit coverage.
Plan: plan-opaque-step2.md. No residuals.

### ~~Cast/shift const-fold silent-miscompile class — checker/IR-gen const-fold asymmetry~~ — ✅ FIXED+LANDED (2026-06-15 .. 2026-06-17)

Started from a CAST-hidden negative constant shift count silently treated as an overshift (`const N int8 = cast(int8, 0) - cast(int8, 3); x << N` printed `0` instead of a `negative shift count` error). Root cause: the checker's `evalConstIntValue` diverged from IR-gen's `evalConstExpr` on which const forms it folds. Closed the whole asymmetry, form by form, plus the cast-semantics decision it surfaced. Each step verified full builder-comp + gen2 (conformance + unit).

- **cast case** — ✅ `c9cce5ef` (option B: `evalConstIntValue` folds cast/bit_cast passthrough; new `constIntFor` records cast-const values). Test 799.
- **(a) char-literal arithmetic** — ✅ `81d2655b`. New `parseCharLiteral` + EXPR_CHAR_LIT arm; `['A']int` dims now fold. Test 803.
- **(b) selector-qualified cross-package const** — ✅ `2e783acd`. EXPR_SELECTOR arm + new `defineBniConst` folds every imported `.bni` const. Tests 804/805.
- **(c) bit_cast sign-reinterpret** — ✅ `83abb2cb`. Shared `types.ReinterpretBitCast` (truncate+sign-extend) used by BOTH evaluators. Test 807.
- **(d) doc comment parity** — ✅ rewritten across the above.
- **(e) positive end-to-end tests** — ✅ `e27008fe`. Tests 810/811.
- **(f) negated sizeof/alignof shift count** — ✅ `767ca300`. `evalConstIntValue` folds sizeof/alignof. Test 808.
- **(g) malformed char-escape in array-dim** — ✅ `200b8989`. New `validateDimCharLitEscapes`. Test 812.
- **(i) sizeof/alignof in isConstShaped/dimFullyKnown** — ✅ `d6978201`. Tests 813-815.
- **(j) alignof coverage** — ✅ `d6978201` (incl. `[alignof([4]int32)]int`→4, a SizeOf/AlignOf-swap discriminator).
- **CRITICAL transitive-`.bni`-const regression** (introduced by b) — conservative guard ✅ `8dd35667` (`exprRefsSelector`); the PROPER IR-gen `RegisterImport` fix is still open in the active todo.
- **(h) strict cast — a cast does NOT launder constants** — ✅ `a393c89c` (option S). New `checkCastConstFits` (value position) + `validateDimCasts` (array-dim); `cast(int8, cast(int, 200))` and `const M int = 257; cast(uint8, M)` now rejected. Fallout (4 `STATIC_MANAGED_REFCOUNT` sites, a vm test, conformance 650) → `bit_cast` / masking. Decision recorded in `claude-notes.md`. Tests 816-818.
  - **h-precision (bare const-ident / qualified const ≥ 2^63)** — ✅ `afe0254b`. New `castConstExactFits` reconstructs the exact value from the symbol's host-int bit pattern + signedness (no Symbol struct change). Test 819.
  - **dim-literal ≥ 2^63 exactness + xfail for the const-arithmetic residual** — ✅ `77d7cc38`. `validateDimCasts` checks literal operands; tests 820/821/823, plus 822 (`.xfail.all`) tracking the const-arithmetic residual.
  - **h-arith (const arithmetic ≥ 2^63)** — ✅ `c699cd78`. New `foldConstNum` folds const `+,-,*,/,%` exactly in `bignum.Num` (range `[-2^63, 2^64-1]`, overflow signalled not wrapped), so `cast(int64, A*B)`==2^63 is rejected and the prior false-reject of in-range 2^63+1 is accepted; `castConstExactFits` subsumed and removed. 822 becomes a normal reject test, 825 guards the accept. `check_builtin.bn`'s cast-fit cluster split to `check_cast_fits.bn`.
  - **h-bitwise (non-negative const `&` `|` `^` `<<` ≥ 2^63)** — ✅ `3f57dc3a`. `bignum.Num` gains `And`/`Or`/`Xor`/`Shl`; `foldConstNum` folds non-negative `&`/`|`/`^`/`<<` exactly (a negative operand routes to the host-int fold, symmetric with IR-gen). 826 (`A<<1`==2^63) becomes a normal reject test, 827 (`M|1`==2^63+1) guards the accept. Right shift (`>>`) deliberately excluded — see h-shr below.

- **(h-shr) signedness-aware const-fold — the `/`,`%`,`>>` + relational + grouped + imported family — ✅ DONE & LANDED.** `evalConstIntValue`/`evalConstExpr` folded the signedness-DEPENDENT ops as SIGNED host ints, so an unsigned operand ≥ 2^63 mis-folded (`(2^63)/(2^61)` → -4 not 4). Fixed in lockstep across every fold path via an ExprType value stamp the checker writes and IR-gen reads:
  - **single consts** (`05d08117`): Root cause (worse than first thought — a SILENT MISCOMPILE, not a false-reject): `evalConstIntValue` (checker) and `evalConstExpr` (IR-gen) fold the signedness-DEPENDENT ops `/`,`%`,`>>` as SIGNED host-int ops, so an unsigned operand ≥ 2^63 (stored as a negative host int) gives the wrong value — `const Q uint64 = (2^63)/(2^61)` emitted `-4` not `4`, silently (both folds agree on the wrong value). Hit typed AND untyped operands. **Fixed for SINGLE (non-grouped) consts** in lockstep: `bignum.Shr` (logical); `foldConstNum` re-enables `>>`; `constIntFor` prefers the bignum fold (`foldConstIntValue`); `checkConstDecl` stamps the value on the initializer's ExprType (`attachConstLitVal`); IR-gen's `genConst` reads the stamp (it can't re-fold — no checker scope at emit). Signed operands unchanged. Conformance `829`. Open residuals (same family, separate fold paths, all pre-existing; an adversarial review of the fix surfaced them):
    - **(h-group)** ✅ DONE & LANDED `beffb741`. Grouped consts (`const ( … )`) were excluded from the signedness fix, so a grouped `Q uint64 = U / D` (U ≥ 2^63) folded signed (-4 not 4). Fix: stamp EXPLICIT group members (signedness-aware) and have `genConstGroup` read the stamp; bare-repeat members keep the host-int fold. New `InGroupBareMember` flag (set only around a bare-repeat member's check) replaces `InConstGroup` (removed, dead) as the fold/stamp gate. The shared-node restamp problem (a bare iota member re-checks the preceding member's node, leaving the last iota's `HasLitVal` on its ExprType) is handled by a new `Type.IsConstStamp` marker set ONLY by `attachConstLitVal`/`attachConstBoolVal`: `genConst`/`genConstGroup` read the stamp only when `IsConstStamp` is set, ignoring `checkExpr`'s incidental `HasLitVal` — so the bit-flag iota idiom stays correct. Conformance `844` (un-xfailed, expanded: grouped /, %, >> over 2^63 + a `1<<iota` bit-flag subgroup); LLVM + VM + gen2. **Residual (rare):** a signedness const immediately followed by a BARE repeat (`Q = U/D; R`) loses Q's stamp to R's re-check (folds signed); the common shared-node case (iota groups) is correct.
    - **(h-bni)** ✅ DONE & LANDED `cf549e2f`. Imported `.bni` consts: `defineBniConst` (checker) folded via host-int `evalConstIntValue`, `gen_import` via host-int `evalConstExpr`, so a cross-package `const Q uint64 = U/D` (U ≥ 2^63) read -4 on both sides. Fix: `defineBniConst` now folds via `foldConstIntValue` (signedness-aware; host-int fallback) and STAMPS the value — giving the un-checkExpr'd `.bni` value node a fresh `ResolvedTypeID` (`registerExprType`) then `attachConstLitVal` (`IsConstStamp`). IR-gen's three import-const paths read the stamp via a shared `importConstStampVal(gc.Mod.Checker.ExprType)` helper, falling back to `evalConstExpr` when unstamped. `sizeof`/`alignof` stamps stay target-correct because `t.SizeOf()` reads the global `SetTarget` config shared by the checker and IR-gen. Conformance `843` (un-xfailed, expanded: imported /, %, >> over 2^63 + signed control + the imported value used as an array dim); LLVM + VM + gen2.
  - (family complete: (single consts `05d08117`; h-cmp `0625521f`, h-inline-shift `865e2e79`, h-group `beffb741`, h-bni `cf549e2f`). Remaining adjacent items: the runtime-count negative-shift variant (xfail `859`, tracked above under h-inline-shift) and the rare grouped-signedness-const-followed-by-bare-repeat residual (noted under h-group).
    - **(h-cmp)** ✅ DONE & LANDED `0625521f`. Relational compares (`<,>,<=,>=,==,!=`) of unsigned operands ≥ 2^63 folded SIGNED (`const C bool = U > D`, 2^63 > 2^61 → false). Fixed in lockstep like the int-const stamp: the checker folds the comparison via `foldConstNum` + `bignum.Num.Cmp` (orders by sign then magnitude; an unsigned ≥ 2^63 operand is a non-negative Num → unsigned compare) in new `foldConstBoolValue`, and stamps the bool on the initializer's ExprType (new `Type.HasBoolVal`/`BoolVal` via `attachConstBoolVal`); IR-gen's `genConst` reads the stamp instead of re-folding via the host-int `evalConstBool`. Signed compares unaffected. Stamp SKIPPED inside a const group (h-group). Conformance `840` (un-xfailed, expanded: unsigned ops + untyped 2^63 literals + signed controls).
    - **(h-inline-shift)** ✅ DONE & LANDED `865e2e79`. A const-folded inline negative-literal `>>` in value position (`var y int = (0-16) >> 2`) emitted a LOGICAL shift (`(2^64-16)>>2`) instead of arithmetic `-4`. Root cause: `foldIntBitwise` (the checker's typed-literal HasLitVal fold) bailed to a value-less untyped-int for any negative operand, so genBinary found no folded value and IR-gen emitted a RUNTIME shift whose untyped-int (unsigned) operand lowered to `lshr`. Fix: `foldIntBitwise` now folds `>>` of a negative left operand arithmetically via the host int (matching IR-gen's `evalConstExpr`), so genBinary materializes the constant. Conformance `845` (un-xfailed, expanded: floor-rounding `-17>>2==-5`, sign-fill, controls).
  - **(runtime-count residual)** 🔴 OPEN (still in the active todo). The RUNTIME-count variant `(0 - 16) >> n` (n a runtime var) still lowers to `lshr`: the negative untyped-int const left operand reaches the shift lowering, which picks ashr/lshr from the operand TYPE's `Signed` flag, not the operand's value sign. A proper fix needs a value-based decision (an untyped `2^63` operand legitimately wants `lshr`). Distinct from the const-count case above (which folds before any shift is emitted). xfail conformance `859_runtime_count_signed_shift`.

Remaining open residuals tracked in the active todo: iota-grouped `.bni` consts, `parseCharLiteral`/`parseCharLit` dedup, multi-byte char-literal leniency, and the proper IR-gen transitive-`.bni`-const fix.

### ~~Type-system (opaque) — `make`/`make_slice`/`sizeof`/`alignof` on an opaque type not gated~~ — ✅ FIXED+LANDED `fe9e131e` (2026-06-16)

The ratified design (plan-type-decls.md) says make / sizeof / alignof on an
opaque type (a forward decl whose layout isn't visible — a pure opaque type
defined in C/asm, or a cross-package opaque export seen only through the
exporter's .bni) must be rejected; the checker enforced only field access, so
these failed only as a downstream layout/codegen error (or silently).
`isOpaqueType` (TYP_NAMED with nil Underlying, peeling alias/const) now gates
make / make_slice / sizeof / alignof in check_builtin.bn — a clean use-site
diagnostic. Generic type params are TYP_TYPE_PARAM, so make(T)/sizeof(T) in
generic bodies are NOT gated; inside the defining package the forward decl's
body is filled, so the gate doesn't fire there. conformance/809 + unit
coverage. (Note: cross-package opacity isn't exercised by the whole-program
conformance build — it loads every dependency's .bn, filling layouts — so the
testable target is pure opaque types; the gate's code path covers both.)

### ~~MAJOR (import resolution) — same-final-segment imports double-emit one package's `_Package` → `invalid redefinition`~~ — ✅ FIXED+LANDED `e201f448` (approach B; 2026-06-15)

A package directly importing two packages with the same final path segment
(e.g. `pkg/basic/io` + `pkg/std/io`, even under different import names) collided:
the IR-gen import-alias map was keyed by the **short name** (last path segment),
and the loader (`cmd/bnc/compile_imports.bn`, `cmd/bni/irgen.bn`) passed
`shortName(path)` as the registration key, so `RecordImportPath` first-wins-
deduped — every symbol of the second package mangled with the FIRST's path, and
`_Package` (registered unconditionally per import) became a hard clang duplicate
(`invalid redefinition of bn_pkg__basic__io___Package`). Fix (approach B): the
loader keys on the **full import path** (`alias == path`); the `rt`/`bootstrap`/
`lang` short-name checks and the 6 generic/interface call-site lookups
(`gen_type_resolve`, `gen_call`, `gen_iface` ×3, `gen_impl`, `gen_iface_registry`)
were made path-based via `resolveImportPkg`. Verified full builder-comp (1451/0)
+ builder-comp-int (1437/0) + unit (45/0); conformance/785 covers it
comprehensively (funcs, extern vars, structs, unqualified local-type fields,
methods, interface, impl). Same-segment GENERICS remain DEFERRED — see
claude-todo.md / conformance/792.

### ~~MAJOR (native codegen) — `bit_cast` of a SLICE value emits invalid IR (`add %BnSlice, 0`) → clang error; the VM accepts it~~ — ✅ FIXED+LANDED, decision: raw-slices-only (binate `cc0d86a8` + tests `40c5c544`; conformance 799/800; 2026-06-15)

`bit_cast` was type-unchecked ("the unchecked escape"); a slice operand fell
to `emitBitCast`'s scalar `add %BnSlice, 0` fallback — invalid LLVM IR (clang
error) while the VM passed it through. A native↔VM divergence.

- **Decision — bit_cast supports raw slices only** (user, 2026-06-15): `bit_cast`
  is a same-size reinterpret. Allowed: scalar/pointer (incl. `@T` managed
  pointer) on both sides, and a RAW slice `*[]T` ↔ raw slice `*[]U` when the
  element types are the same size and both non-managed — a `{ptr,len}`-identical
  element retype (e.g. `*[]uint8` ↔ `*[]int8`), a runtime no-op. Rejected:
  managed slices (would need refcount-ownership semantics — reinterpret a raw
  view instead), structs, arrays, func/iface values, slice-vs-non-slice, and
  mismatched element sizes (which would break `len`). The element retype is the
  one aggregate case that's meaningful because a slice's element type isn't in
  its runtime representation.
- **Fix**: `checkBitCastShapes` (`types/check_builtin.bn`, both modes) enforces
  the rule; `emitBitCast` (`codegen/emit_ops.bn`) lowers the raw-slice case as an
  identity `select` (valid for `%BnSlice`) instead of `add`. The x64/aarch64
  NATIVE backends already handled it correctly (they MOV the aggregate pointer,
  never a scalar) — verified by running 799/800 on `builder-comp_native_aa64`.
- **No code broken**: a repo-wide sweep (adversarially verified) found zero
  existing `bit_cast` used an aggregate operand.
- **Tests**: conformance 799 (raw-slice retype incl. signedness — native / VM /
  gen2 / native-aa64), 800 (managed-slice / struct / array rejected); unit tests
  in `check_builtin_test.bn` (accept raw-slice retype + managed pointer; reject
  managed-slice, struct, array, raw-slice-with-managed-elements, size mismatch,
  slice-vs-scalar). An adversarial review (correctness / cross-backend /
  coverage) found no code defects.

### ~~MAJOR (types, REGRESSION) — `x << ~0` no longer rejected; the negative-shift-count gate mis-trusted `~`'s stale LitSign~~ — ✅ DONE (binate `46204267` + `fc3c496d` + `9a6af307`, 2026-06-15)
The gate (added by `393eaa0b`) keyed on the type's `LitSign`, but `checkUnaryExpr`'s `~` branch left it stale (the operand's), so `~0` (== -1) slipped through and `x << ~0` compiled to garbage. Fix: the gate skips only a BARE `EXPR_INT_LIT` and routes everything else through the folder's bignum-correct `LitSign` / `evalConstIntValue`; `checkUnaryExpr`'s `~` now propagates the COMPLEMENTED literal (`~x = -x-1`), which also corrects the AssignableTo fit-check (`var x uint8 = ~0` rejects like Go). The same `LitSign`/magnitude approach fixed the two `2^63`-magnitude const-shift edges: IR-gen `emitConstOvershiftOrNil` compares the count magnitude UNSIGNED (so `x << 0x8000000000000000` overshifts to 0), and a COMPUTED huge count (`x << (1 << 63)`) is accepted. Tests: `conformance/793` (wide-negative `<<=`), `797` (`>>=`), `798` (2^63 overshift), checker tests for the computed allow-case + `~` fit-check. `checkUnaryExpr` extracted to `check_expr_unary.bn` (file-length cap). Full conformance green: LLVM 1464/0, gen2 self-compile 1464/0.

### ~~Shift-work adversarial review — confirmed follow-up findings (gate holes, VM register-pair, coverage)~~ — ✅ DONE (binate `393eaa0b` / `75d279a9` / `e44e2c28`, 2026-06-15)
From the first shift-work adversarial review (13 confirmed / 3 dismissed; the CRITICAL IDENT-compound sibling is its own done entry). Fixed: the named-unsigned + huge-literal gate holes (peel `TYP_NAMED` + use `LitSign`, `393eaa0b`); the VM `BC_SHIFT_CHECK` / `BC_DIV_CHECK` single-slot read of a register-pair int64 on a 32-bit host (`guardInt64` reads `joinInt64(lo,hi)` when `REG_SLOT < 8`, `75d279a9`; pair *integration* still awaits a 32-bit-host VM lane); arity-test honesty + coverage gaps — untyped-value unsafe-shift, >4 GiB Seek (`e44e2c28`). Dismissed 3 (no action): the x64 getOperand bail, the `!is(arch,"arm32")` future-arch footgun, native-x64 panic being CI-host-dependent.

### ~~Remove the `pkg/binate/vm` lint skip after the next release~~ — ✅ DONE (binate `eab1ca5a`, 2026-06-15)
The bnlint skip for pkg/binate/vm (+ importers repl, cmd/bni) existed because the
BUILDER-bundled bnlint predated `_Package()` / `_func_handle(rt._Package)` /
`@reflect.Package` typecheck support. BUILDER_VERSION reached bnc-0.0.9 whose
bundled bnlint handles them (verified: lints all three cleanly, EXIT 0), so the
skip was retired (`LINT_SKIP=""`) and all three are style-linted again; full
hygiene green. (The separate `#[build]` build.bni shim is a distinct workaround,
left in place.)

### ~~Cross-package managed refcount-safety + extern-var coverage gaps (2026-06-04 audit)~~ — ✅ DONE (2026-06-15)
The 2026-06-04 audit's 17 cross-package gaps are all closed:
- **rc-balance**: managed-slice extern-var value-copy (592); a managed value
  crossing a package boundary as a slice-element assign / function arg / return /
  struct-field store / interface construct / interface return / generic type arg
  — conformance 673/674/675/676/677/678/682 (`8741c552` also cleared their
  int-int xfails).
- **extern-var functional**: `&pkg.X` scalar addr-of (687); field write through
  an imported raw-ptr / value-struct var (686); raw-slice element write through a
  `*[]T` extern var (796, binate `e45a8cca` — the final residual, green incl.
  native aa64).

### ~~CRITICAL (IR-gen) — IDENT compound shift-assign (`v <<= c`) pre-truncated the count, defeating both the wide-overshift fix and the negative-count guard~~ — ✅ DONE (binate `11f0b413`, 2026-06-15)
The IDENT lvalue arm pre-`ensureWidth`'d the shift count before
`emitCompoundBinop`, so the wide-overshift fix and the negative-count guard read
a truncated count. `11f0b413` gates the pre-truncation
`if varTyp != nil && !isCompoundAssign(stmt)` (gen_control.bn:151). Conformance
793 (negative-count compound) + regressions/shift-runtime-wide-overshift + unit
`TestCompoundSignedShiftEmitsGuards`.

### ~~Bundle tier-1 stdlib (pkg/std, pkg/stdx) with the BUILDER; cut a new BUILDER release~~ — ✅ DONE (bnc-0.0.9, 2026-06-15)
`make-bundle.sh` ships ifaces/ + impls/ wholesale; `fetch-builder.sh --lib`
resolves stdlib (cmd/bnc imports pkg/stdx/slices, pkg/std/strings). math/big +
strconv.ParseFloat present and reachable (impl closures stay within bundled
tiers). BUILDER_VERSION=bnc-0.0.9 (release `cea0cb6e`); float-const BUILDER gap
cleared (`27ba1f7e`).

### ~~Static-managed sentinel refcount (prerequisite for package descriptors)~~ — ✅ DONE (binate `04ff8cf0` / `f78a4951`, 2026-06-15)
Negative-as-immortal (`h[0] < 0`) sentinel landed across all five refcount paths
(rt library, baremetal, LLVM-inline, native aarch64 TBNZ, VM); the static-node
emitter is consumed by package descriptors (`emit_pkg_descriptor.bn`). Tests:
`TestRefIncDecImmortal`, `TestVMRefIncImmortal`, conformance 525/532 + reflect
708/709/725. ("IN PROGRESS" header was stale; descriptor work continues
separately. Deferred optimization follow-ups re-filed as a standalone active
entry.)

### ~~bnc: top-level consts of non-int types silently emitted `EmitConstInt(0)` at read sites~~ — ✅ DONE (Phase A landed; Phases B/C canceled — const is scalar-only) (2026-06-15)
Phase A (string/bool/float scalar consts): `classifyConstLit` + `ModuleConst.Kind`
read-site dispatch (gen_const.bn / gen_expr.bn) emit the right constant; the
`EmitConstInt(0)` mis-emit is gone. Conformance 539/540/541/642/645/651/691 +
gen_const unit tests. Phases B (composite) / C (pointer) **canceled**:
`checkConstDecl` now rejects non-scalar const types via `errNonScalarConst`
("use `var readonly`"). (The "purely-value const extension" future idea is
re-filed as a standalone active entry.)

### ~~arm32 unit-test cleanup: 5 remaining int64-boundary tests~~ — ✅ DONE (2026-06-15)
Zero `builder-comp_arm32_linux` unittest xfails remain; the 5 Bucket-3 int64-min
boundary tests are present and host-pass.

### ~~Multi-return tail-call return (`return f(...)`)~~ — ✅ DONE self-hosted; bootstrap clause moot (2026-06-15)
Self-hosted (landed 2026-05-01): the type-checker (`check_stmt.bn` checkReturnStmt)
and IR-gen (`gen_return.bn`, split from gen_stmt.bn) accept `return f(...)` for a
matching tuple (per-result AssignableTo; lowers to OP_CALL + per-result
OP_EXTRACT; `@[]T → *[]T` coercion preserved on extracted values). Tests:
check_stmt_test.bn, `TestGenReturnMultiCallEmitsExtracts`, conformance 347 (no
xfails, all default modes). The "Bootstrap (pending decision)" clause is **moot**
— the Go bootstrap was retired 2026-05-21 (`bootstrap/` gone). Spec in
claude-notes.md.

### ~~MAJOR — field access on a struct composite-literal rvalue read 0 (`Foo{...}.field`) — silent miscompile~~ — ✅ FIXED (binate `ff291fc6`; conformance 795; 2026-06-15)
IR-gen's `genSelector` dispatched on the base kind (IDENT / INDEX / SELECTOR /
CALL-BUILTIN / deref-UNARY) but had no `EXPR_COMPOSITE` case, so `Foo{...}.field`
(and the paren form `(Foo{...}).field`) fell through to the `EmitConstInt(0)`
fallback and produced 0 on all modes — and never even evaluated the composite,
so its side effects were silently dropped. Construction and method calls on a
composite rvalue already worked; only the immediate field read was wrong.

A first minimal fix (`00a3f937`, conformance 794) added the arm to `genSelector`
only; it was **reverted** (`c93a6388`) in favor of `ff291fc6`, which is complete:
- **Three sibling sites** all lacked the `EXPR_COMPOSITE` base case and are fixed
  together: `genSelector` (value read), `genSelectorPtr` (field pointer — used by
  a nested read like `Line{...}.p.x`, which the minimal fix left reading 0), and
  `getSelectorType` (so the nested selector resolves `.p`'s struct type).
- **Managed-field cleanup**: because the composite is now actually evaluated, a
  composite with managed fields is an anonymous rvalue temp that would leak.
  `genCompositeBasePtr` registers it for end-of-statement cleanup (mirroring a
  struct-returning call result via `registerManagedCallResult`) when the struct
  `NeedsDestruction`; scalar structs add no cleanup. Verified via LLVM IR that the
  managed case now emits the struct dtor in the caller (matching `var b = Foo{...}`)
  and the scalar case is unchanged.
- `genSelectorPtr` moved to `gen_selector_ptr.bn` (file-length) with its own tests.
- Conformance 795 (scalar / struct-typed / nested / in-expression / managed-field),
  green on builder-comp / -int (VM) / -comp (gen2) / -int-int / -comp-comp; plus IR
  unit tests for both selector arms.

### ~~CRITICAL (native codegen) — static-data `@T` managed-pointers omitted the +headerSize addend → pointed at the object header, not the payload~~ — ✅ RESOLVED (binate `f78a4951`, 2026-06-15)
The Mach-O writer dropped relocation addends (a `relocation_info` has no addend
field), so a static initializer storing a `@T` to another static managed object
resolved to the object's 16-byte header (the static-refcount sentinel) instead
of the payload — walking rt's reflective `Functions` table read each field 16
bytes low (`Name.ptr == 0` → SIGSEGV in repl/vm/cmd-bni native VM-host).
`f78a4951` bakes each absolute (UNSIGNED) reloc's non-zero addend INLINE into the
section data (`macho_reloc.bn` `bakeAddend`); PC-relative kinds keep their 0
placeholder. ELF was already correct via `r_addend`. repl/vm/cmd-bni native_aa64
xfails removed; unit suite green there.

### ~~native_x64 (ELF) was NOT "WIP" — one reloc bug masked a 99%-working backend~~ — ✅ FIXED+LANDED incl. the C-call/variadic gap (verified 2026-06-15)
Core fixed by the PC32 reloc-addend fix (binate `dd74c91e`) + `.note.GNU-stack`
(`c097a381`). The remaining C-call/variadic gap is also closed (`0d0f35b7`,
`62ae438f`): SysV variadic `AL = #vector-regs` is implemented in `x64_call.bn`
(`if OP_C_CALL && CFixedArgs < len(Args) { Mov AL, nsrn }`), and all native_x64
xfails on the c-call conformance tests (498/500/527/530 + regressions/c-call/*)
are gone. `builder-comp_native_x64-comp_native_x64` is in `scripts/modesets/all`
→ CI runs it on a real x86_64 host (not the Rosetta-darwin path that hid the
bug). Remaining native_x64 xfails belong to separate entries (float-closure
shim; static-data @T addend; named-func-value literal).

### ~~MAJOR — native Mach-O writer emitted no LC_DYSYMTAB / unpartitioned symtab → cross-object weak defs wouldn't coalesce → `duplicate symbol`~~ — ✅ RESOLVED (binate `3fb0e805`, 2026-06-15)
`3fb0e805` ("make objects atom-safe so weak symbols coalesce") sets
MH_SUBSECTIONS_VIA_SYMBOLS, emits LC_DYSYMTAB (ncmds 3→4) with a
local/extdef/undef-partitioned symtab (`partitionSymbols` + `macho_dysymtab.bn`),
drops unreferenced L-temps, relocates every inter-atom ref (incl. x64 RIP-LEA,
aa64 ADRP+ADD), and adds `macho_dysymtab_test.bn`. The 8 dup-symbol xfails this
caused were removed; the 3 remaining (cmd-bni/repl/vm) were re-attributed to the
separately-resolved static-data @T-addend entry.

### ~~[CR-2 Plan-1 review] AMEND CRITICAL "iface-upcast −1-offset footgun" with two new reproducer details~~ — ✅ DONE (parent resolved binate `ca155319`; verified 2026-06-15)
Both sub-fixes landed in `ca155319` (the parent CRITICAL is already in this log):
`IfaceParentSlotOffset(X,X)→0` for the same canonical interface
(`gen_iface_extends.bn`), and a hard negative-offset panic in all three lowerings
(`emit_iface_upcast.bn`, native aa64/x64 `*_dispatch.bn`). The follow-on
false-fire regression was fixed in `4ac123da`. Conformance 685/689 cover it.

### ~~Sub-word arithmetic results not narrowed in the VM (and natives) — dirty upper bits → wrong values~~ — ✅ DONE incl. aa64-subword extension (verified 2026-06-15)
Primary add/mul narrowing landed earlier (VM `435b6cdd`, aa64 `ee671b6c`, x64
`57e72d9e`; `applyNarrow`/`narrowToWidth` in `vm_exec_pure.bn`). The aa64-subword
extension is also resolved: per-backend sign-extend/re-narrow fixes (`d186b73d`,
`f627e815`, `0ca49975`, `68616b20`) let `5f94558b` drop 29 stale native_aa64
scalar-diff xfails (29/29 re-verified XPASS on a real aarch64 host); 0
native_aa64 xfails remain in conformance/matrix.

### ~~Verify anonymous struct equivalence — edge cases~~ — ✅ DONE (verified 2026-06-15)
All three requested edge cases are covered by landed, passing, non-xfail'd
conformance tests on main: nested (`490_nested_anon_struct_equiv`), managed field
(`491_anon_struct_managed_field_equiv`), and cross-package
(`402_cross_pkg_anon_struct`, a real pkg dep) — commits `3af592d9` / `5e29c9d3`.

### ~~REPL refactor: embeddable component for non-CLI hosts~~ — ✅ DONE (v1 Stages 1–5; the "DESIGN RATIFIED, not started" header was stale; verified 2026-06-15)
The embeddable engine landed as `pkg/binate/repl` (interface in
`pkg/binate/repl.bni`): `replSession`/`NewReplSession` (errors-as-values),
`ReplIO` sink, `Init()`/`Step()→StepResult`, an inert v1 interrupt seam
(`SetPoll`). `cmd/bni` is rewired to drive it (`cmd/bni/repl.bn` imports
`pkg/binate/repl`, calls `NewReplSession`/`Init`/`Step`). Plan doc
`plan-repl-embeddable.md`: "Stages 1–5 DONE (2026-06-02)". Stages 6/7 are
FUTURE/out-of-v1-scope, tracked there.

### ~~Unknown escapes silently dropped (spec Ch.5)~~ — ✅ REJECTED+LANDED (binate `be30129e`; conformance 791; 2026-06-15)

`unescapeStr`/`parseCharLit` decoded only `\n \r \t \\ \' \" \0 \xHH \uHHHH`;
any other `\X` fell through to a verbatim `X` (backslash dropped) with no
diagnostic — so `"\a"` decoded to `"a"`.

- **Resolution — reject** (completes the §5 escape batch alongside the `\u`
  work): `validateEscapes` (`types/escape.bn`, run from `checkExpr`) now
  reports any backslash not starting a recognized escape as
  `unknown escape sequence '\X'`, naming the offending char.  The IR decoder
  catch-alls became unreachable for valid input (the checker gates them) and
  carry a note marking them defensive fallbacks.
- **No existing code broken**: a repo-wide sweep of every `.bn`/`.bni` in the
  binate repo + the examples repo (2468 files, adversarially verified for
  completeness) found zero literal using an unknown escape.
- **Tests**: conformance 791 (negative: `"\a"` rejected); unit test in
  `escape_test.bn`.

### Shift count: negative → panic (Go semantics) + unchecked-shift intrinsic — ✅ DONE (2026-06-15)
Decided 2026-06-15 (after the runtime-overshift fix above): a shift count is non-negative; a NEGATIVE count is a programmer error, matching Go. All three sub-items landed:
- **(2a) Runtime negative-count panic via a dedicated check op — ✅ DONE (binate `6bf1efab`)**: new `OP_SHIFT_CHECK` → `rt.ShiftCheck` (panics via `rt.ShiftFail`, "runtime error: negative shift count"), parallel to `OP_DIV_CHECK`/`rt.DivCheck`. `EmitShiftCheck` (ir_ops) emitted by `emitShiftCheckGuard` (gen_shift) only for a SIGNED count on the guarded path, from the original (pre-width-reconciliation) count sign-extended to int64. Lowered on every backend: LLVM (`emit_instr`), x64 + aarch64 dispatch, VM (`BC_SHIFT_CHECK` → `rt.ShiftCheck`). Runtime decls/impls in `rt.bni`/`rt.bn`/`rt_baremetal.bn`. Conformance `787_err_shift_negative_count` (panics on LLVM/VM/native); IR-gen + per-backend lowering unit tests. Full conformance green on LLVM gen1 (1454/0) and gen2 self-compile (1454/0). The shift-codegen cluster was extracted into `gen_shift.bn` (+ `gen_shift_test.bn`) to keep `gen_binary.bn` under the file cap.
- **(3) Constant negative shift count → COMPILE error — ✅ DONE (binate `f6b9ebce`)**. New `checkShiftCountNonNegative` (folds the count via `evalConstIntValue`, errors on a constant < 0; gated off concrete-unsigned count types so a huge unsigned const isn't mis-folded to negative), wired into both the expression (`checkBinaryExpr`) and compound-assign (`checkAssignStmt`) shift sites. Tests in `check_expr_binop_test.bn`. So a const negative count never reaches IR-gen.
- **`unsafe_shl` / `unsafe_shr` intrinsic — ✅ DONE (binate `c9a6ed36`)**: the analogue of `unsafe_div`/`unsafe_rem` — skips BOTH the overshift guard and the negative-count check, lowering to a bare `OP_SHL`/`OP_SHR` (target-defined for a count outside [0, width); the contract is "count in [0, width)"). New `UNSAFE_SHL`/`UNSAFE_SHR` keyword tokens; the two-arg unsafe-builtin parser was generalized (`parseUnsafeDivRemCall` → `parseUnsafeBinaryCall`); `check_builtin` type-checks them like a shift (result = LEFT operand's type; the const-negative compile error does NOT apply — opting out is the point); `gen_expr` lowers the bare shift. `conformance/788_unsafe_shift` (in-range behavior on LLVM/VM/native) + checker error-path + IR-gen skips-guards unit tests. Full gen2 self-compile green (1455/0).
- **Doc note:** the negative-shift-count semantics (const → compile error, runtime → panic) is NOT yet recorded in a language-spec doc; `claude-notes.md:934` documents only the overshift behavior. (`grammar.ebnf` is no longer authoritative, per the user.)

### ~~`\uHHHH` documented but unimplemented (spec Ch.5)~~ — ✅ IMPLEMENTED+LANDED (binate `1c43ef79`; conformance 789/790; 2026-06-15)

`claude-notes.md` and `grammar.ebnf` listed a `\uHHHH` escape but neither
decoder had a `\u` case (it would emit `u` followed by the hex digits).

- **Resolution — implement, UTF-8 expansion** (user decision, 2026-06-15):
  `\uHHHH` denotes Unicode code point U+HHHH and expands at compile time to
  its UTF-8 encoding (1-3 bytes).  Pure source sugar — strings stay byte
  sequences, no runtime Unicode machinery (no `rune`, no Unicode-aware
  `string`).  In a char literal (one byte) only U+0000..U+007F is allowed.
  The user clarified that "not fully supporting UTF-8" only means no `rune`
  and no Unicode-aware `string` type; it does not bar compile-time `\u`
  expansion.
- **Implementation**: `types.Utf8Bytes` (exported, `types/escape.bn`) is the
  single encoder, shared by `unescapedStrLen` (natural-type length) and the
  IR decoders (`unescapeStr`/`parseCharLit`).  `validateEscapes` (run from
  `checkExpr`) rejects malformed `\u` (fewer than four hex digits, a UTF-16
  surrogate, or a multi-byte code point in a char literal) and incomplete
  `\x`.  Lexer `scanChar` now scans to the closing quote (fixing a latent
  mis-scan of `'\xHH'` too).  The >0xFF-into-`char` question is answered:
  rejected (only single-byte code points fit a char).
- **Follow-up**: rejecting *unknown* escapes (`\a` etc., still silently
  dropped) is a separate one-branch extension of `validateEscapes`; tracked
  under the Ch.5 entry in claude-todo.md.

### Shift: RUNTIME count-wider OVERSHIFT corner mis-detected — ✅ DONE (binate `0db709a1`)
- **Symptom**: a shift `value << count` / `value >> count` whose `count` was a RUNTIME value of a TYPE wider than `value`, with `count >= 2^valueBitWidth` but `count mod 2^valueBitWidth` a small residue (e.g. a runtime `uint16` count of 261 shifting a `uint8`), silently yielded the wrong (non-overshift) result instead of the spec'd 0 (logical) / sign-fill (arithmetic `>>`).
- **Root cause**: `genBinaryExpr` / `emitCompoundBinop` truncated the runtime count to the value width via `ensureWidth` BEFORE `emitGuardedShift`'s `count < width` guard ran, so the guard saw the truncated residue (5), not 261. `emitConstOvershiftOrNil` (untruncated `count.IntVal`) covered only the CONSTANT case; there was no runtime equivalent.
- **Fix (binate `0db709a1`)**: new `emitShiftInrangeOrNil` computes the `count < width` predicate from the ORIGINAL, pre-`ensureWidth` count, reading it in its OWN type — so a wider count keeps the high bits that reveal the overshift, and the count's signedness is honored (a `uint8` count of 200 is 200 → overshift, not −56 → in range; the same-width-other-signedness corner is fixed too). `emitGuardedShift` takes the precomputed predicate; its mask + shift still use the width-reconciled count. Both shift paths (`gen_binary` expr, `gen_control` compound-assign) fixed. New `conformance/regressions/shift-runtime-wide-overshift` (opaque-fn runtime counts) green on LLVM / both VM lanes / native aarch64.
- (Full resolved diagnosis of the `int64 << int` regression that spawned this — checker/IR-gen fix in `fd3cb7ac` — is archived in claude-todo-done.md.)

### ~~`cast` is unchecked at the type layer; literal fit-check not enforced — spec Ch.8~~ — ✅ FIXED+LANDED, decision: reject (binate `042d2fe6`; conformance 650; 2026-06-15)

`claude-notes.md` said `cast(uint, -1)` is a compile error (literal doesn't
fit), but the type-checker did NOT enforce it: `check_builtin.bn`'s CAST arm
resolved the target type and returned it **unconditionally** — no constant
fit-check (whereas a plain assignment `var x uint8 = 256` IS rejected via
`untypedIntLitFitsTarget`). So `cast(uint, -1)` / `cast(uint8, 256)` were
silently accepted.

- **Resolution — reject** (user choice, 2026-06-15): `castTargetIsInteger`
  helper + a fit-check in `checkBuiltinCall`'s CAST arm — if the argument is a
  constant (`HasLitVal`), the target resolves to an integer type, and the value
  doesn't fit (`!untypedIntLitFitsTarget`), it's a compile error: "constant does
  not fit the cast target type (use bit_cast to reinterpret)". `bit_cast`
  untouched.
- **Escape for intentional wrap**: launder the constant through a runtime value
  — `cast(T, cast(int, N))`. The inner `cast(int, …)` strips `HasLitVal`,
  yielding a non-constant int, so the outer cast wraps/truncates at runtime
  (`cast(uint8, cast(int, 300))` → 44). A deliberate Binate-vs-Go divergence:
  in Go `int(N)` of a constant stays a typed constant; in Binate a `cast` always
  produces a runtime value. `bit_cast` is NOT the escape — it's a *same-size*
  reinterpret, so `bit_cast(uint8, 300)` is a size-mismatch error.
- **Tests**: `check_builtin_test.bn` (`TestCheckCastConstantOverflowRejected`,
  `TestCheckCastConstantInRangeAccepted`); conformance 650 updated to launder
  its intentional out-of-range casts through `cast(int, …)`. `claude-notes.md`
  §8 cast semantics paragraph updated with the laundering/`bit_cast` clarifics.

### ~~parallel assignment `a, b = 1, 2` / swap `a, b = b, a` type-checked clean but generated NO code (silent dropped writes)~~ — ✅ FIXED+LANDED, decision (A) Support (binate `d2a3b8f1`; conformance 778-784; spec Ch.14, 2026-06-15)

A matched-arity multi-expression assignment (>1 expr on each side) type-checked
clean (per-element checks in `checkAssignStmt`) but `genAssign` matched NEITHER
lowering arm — `genMultiAssign` needs `len(Exprs2)==1`, single-assign needs
`len(Exprs)==1` — so it fell straight to `return b`, emitting NO store IR. Both
writes silently dropped: `a, b = 1, 2` and the swap `a, b = b, a` compiled to a
no-op. Only multi-RETURN forms (`q, r = f()`) ever reached codegen, so the gap
was never exercised.

- **Resolution — decision (A) Support** (user choice, 2026-06-15): new
  `genParallelAssign` (`pkg/binate/ir/gen_assign_parallel.bn`), wired as a third
  `genAssign` arm (`len(Exprs) > 1 && len(Exprs2) == len(Exprs)`). Go-style
  two-phase lowering: phase 1 resolves each LHS target and evaluates+ACQUIRES
  its RHS value; phase 2 stores each into its slot, releasing the old occupant
  (Axiom 5) WITHOUT re-acquiring. The up-front acquire avoids the swap UAF
  (storing b into a would otherwise RefDec a's old value to zero — freeing it —
  before the second store reads it). A managed struct VALUE is snapshot-copied
  into a temp and MOVED into its slot, so the discipline holds for aggregates.
  Covers ident / `*p` / `p.f` / array+pointer+slice index targets with the
  single-assign coercions (string→chars, `@[]T`→`*[]T`, aggregate load) and
  sub-word width. No checker change (it already accepted matched-arity).
- **Tests**: conformance 778-784 (basic/rotation/old-value, `@T` swap refcount,
  array+slice index swap, string+width coercion + managed-slice swap,
  struct-field + managed-struct-value swap, deref targets, owned-temp RHS) —
  green on builder-comp / builder-comp-int (VM) / builder-comp-comp (gen2);
  unit `gen_assign_parallel_test.bn` pins the IR shape.
- **Follow-up (separate item)**: ✅ DONE (2026-06-15) — spec §14.4
  `stmt.assign.parallel` documents decision (A): all RHS evaluated before any
  store, the swap works, and the matched-arity multi-expression form is legal
  (not multi-return only). The §14 badge and the index no longer flag parallel
  assignment as a defect.

---

### pkg/std/os arm32-linux off_t width (Seek/ReadAt/WriteAt) — ✅ DONE (binate `d33d7819`)
`Seek`/`ReadAt`/`WriteAt` (`impls/stdlib/pkg/std/os/os.bn`) passed `int64`
offsets straight to `lseek`/`pread`/`pwrite` via `__c_call`. On ILP32
arm32-linux glibc `off_t` is 32-bit, so the 64-bit arg shifted the AAPCS
register-pair arg layout (an `int64` goes in an even-aligned register pair;
a 32-bit `off_t` expects a single register) and corrupted the call — even
small offsets, since `whence` then read the offset's low word and lseek saw
an invalid whence.
- **Fix (binate `d33d7819`)**: the three offset `__c_call`s are factored into
  `cLseek`/`cPread`/`cPwrite` wrappers with per-declaration `#[build]`
  variants (the function-impl-level gate, like `build.bni`'s per-`const`
  variants — the `.bni` surface is unchanged). `#[build(is(arch, "arm32"))]`
  routes to the LFS `*64` entry points (`lseek64`/`pread64`/`pwrite64`,
  64-bit `off64_t` on every Linux arch); `#[build(!is(arch, "arm32"))]` keeps
  the plain names for every LP64 hosted target (x64/aarch64 linux + darwin;
  Darwin has no `*64` symbols). Per-decl variants — not a runtime
  `build.OS`/`build.Arch` branch — because `__c_call` needs a literal symbol
  that must LINK (same reasoning as the per-OS `errno()` accessor).
- Dropped `scripts/unittest/pkg-std-os.xfail.builder-comp_arm32_linux`; the
  existing `TestSeek`/`TestReadAtWriteAt` (non-zero offsets) now exercise the
  arm32 path. **Validated only on CI** (no local qemu/cross-toolchain) — watch
  the unit-tests `builder-comp_arm32_linux` job on the push.
- Same commit teaches `bn-doc` to treat `#[...]` annotation lines as neutral
  (these are the tree's first `#[build]`-on-`func` decls in a `.bn` file, and
  the checker had been resetting the doc context on the annotation line).
- The `arm32_baremetal` xfail stays (no filesystem) and was never part of this
  item. *Untested (impractical in a unit test):* a >2 GiB offset, which would
  additionally confirm the full 64-bit range — the ABI fix is already
  validated by the small-offset tests.
(The O_* compile-time-flags diagnosis and the now-resolved VM-mode residual
are archived in claude-todo-done.md; the O_* fix landed in binate 590906c8,
and os now passes under the VM modes via native injection — commit 55229591.)

### ~~D4: composite literal in a condition wasn't usable via the documented paren-escape — spec §13.13 (2026-06-12)~~ — ✅ LANDED on main (binate `23f41e22`, 2026-06-14)

`noCompositeLit` was a sticky bool never cleared on descending into
`(` / `[` / call-args, so the documented paren-escape — `if (Point{1,2})
== p {}` — was suppressed too (the `{` mis-read). New
`parseExprAllowComposite` clears/restores `noCompositeLit` for
parenthesized, call-argument, and index/slice-operand sub-expressions
(Go's `exprLev`); a bare composite in a condition still stays suppressed.
Tests: `TestParseParenCompositeEscapesNoCompositeCtx` +
`TestParseBareCompositeStaysSuppressed`; conformance
`777_composite_lit_paren_in_cond` (paren-escape exercised via a method
call — direct field access on a composite rvalue is the separate MAJOR
bug now tracked in claude-todo.md). Green on builder-comp / -int / -comp.

## MAJOR — native funcval shim marshalling used `ArgWords`, not the CallConv classifier — x64 false-rejected, aa64 SILENTLY MISCOMPILED (✅ NON-CLOSURE shim RESOLVED — Stage A + Stage B + B0 force-emit landed on main `cd417081`, 2026-06-11)
**Split 2026-06-14**: resolved bulk (non-closure shim Stage A/B + B0 Functions-table) archived here; the open closure-shim-cousins follow-up is a slim ## entry in claude-todo.md.

> The NON-closure funcval shim bug is fixed and landed. What remains: the
> CLOSURE-shim cousins (the FOLLOW-UP bullet below — still latent) and B0
> step 3 (the Functions table — separate from this bug). Kept here rather
> than moved to done because both reference this context.

- **Confirmed silent miscompile (native aa64)**: a function VALUE with > 8 user-arg words (e.g. a `*func(int×9) int`) is **silently miscompiled** — `aarch64_funcvalue.bn:283` does `if nUserWords > 8 { nUserWords = 8 }` (a clamp the comment calls "accept the truncation"), so the 9th+ arg is dropped/garbled. **Runtime-verified: a 9-int-arg funcval returns `43` instead of `45`** (correct on LLVM + VM; wrong only on native aa64). x64 has the loud analogue (`x64_funcvalue.bn:324` `a.SetError(... "unimplemented stack-spill path ...")` → build fails).
- **Root cause**: the per-function shim counts/shifts arg WORDS via raw `common.ArgWords` (managed-slice = 4 words, iface = 2), but the dispatch caller (`emitCallFuncValue`) and the underlying ABI use the `CallConv` classifier where a >16B aggregate is **IndirectLargeAggregates = 1 pointer word**. The two only "agree" by a contiguous-block-shift coincidence (real words land right; over-counted extras spill into unread high regs). Consequences: (a) **x64 false over-budget** — `pkg/std/errors.Wrap(cause @Error, msg @[]char) @Error` is 3 real classifier-words but `ArgWords` counts 6 > the 4-word pack budget → spurious `SetError`; (b) **aa64 silent truncation** for genuinely-wide funcvals; (c) **latent**: an indirect-large arg followed by another arg misplaces the trailing arg (the `ArgWords` shift over-advances ngrn) — affects the closure shims' user-arg path too (`emitClosureShimFast_*` / the spill paths use `ArgWords` for users, `effectiveCapWords` only for captures).
- **Discovery trigger**: B0 of `plan-package-introspection-phase-b.md` force-emits a func-value triple for every exported func; `errors.Wrap` is the first wide funcval emitted, hitting the x64 `SetError`. (Before force-emit, only a handful of narrow-signature funcvals existed, so the gap was masked.)
- **Fix (the proper one — `B`, no workaround)**: switch the shim counting AND marshalling to **effective words** (indirect-large = 1, via the classifier / an `effectiveArgWords` helper) on both incoming and outgoing sides, for the non-closure funcval shims AND the closure shims, on x64 + aa64; add a genuine-overflow **stack-spill** path (mirroring the already-correct `emitClosureShimStackSpill_x64`/`AA64` scalar reference) for funcs whose effective words truly exceed the GP reg file; replace aa64's silent clamp with the spill (loud-or-correct, never silent). The dispatch caller + `common_callconv.bn` classifier need **no** change. Then re-apply the B0 native force-emit.
- **Tests to land with the fix**: conformance cases for (1) `errors.Wrap`-class wide funcval (managed-slice + iface args), (2) indirect-large arg NOT in last position, (3) the 9-scalar-arg funcval (the confirmed aa64 repro). All must pass on LLVM / VM / native aa64 / native x64.
- **Map**: full subsystem map in the workflow output (dispatch caller already spills; VM dispatch caps at `a0..a6` = 7 words via `rt._call_shim_*`, so a >7-word funcval would ALSO need the VM helpers widened — `errors.Wrap` at 3 effective words is well under, so not blocking).

#### Status (2026-06-11)
- **Stage A — DONE, landed `9ceab3be`**: non-closure funcval shims count/marshal by EFFECTIVE words (`cc.EffectiveArgWords`) on both backends; aa64's silent clamp replaced with a loud over-budget `SetError`. Verified: 696 green on all 4 modes; x64/aa64 funcval regression 237/0; the 9-arg aa64 miscompile is gone (now fails loud, pending Stage B). `conformance/696_funcval_indirect_large_args` pins the effective-words cases.
- **Stage B — DONE, LANDED `cd417081` (rebased SHAs `f4fe9f76` split / `e599d2fc` ir.bni / `43573a33` spill / `4d7f7fe0` SPLIT-arg coverage / `cd417081` test renumber)**: replaced the loud `SetError` over-budget guards with a real genuine-overflow **stack-spill** for the NON-closure funcval shims on both backends. (Pre-rebase worktree SHAs were `d56c5fa2`/`bde64614`/`5e4d0899`.) Conformance tests renumbered to **716/717/718** at land time (696/697/698 were taken by concurrently-landed tests). Design (mirrors `emitClosureShimStackSpill_*`): when `nUserWords > userBudget`, `SUB rsp/sp` an outgoing-args frame (x64 ≡8 mod 16 for the return-addr push; aa64 STP FP,LR to preserve LR), marshal user args with spill (incoming overflow read from the dispatch caller's stack; outgoing overflow placed via the CallConv classifier with AAPCS SPLIT honored; floats peel to FP regs), `CALL/BL` (not tail-jump), post-process (pack: store result through stashed retbuf; sret: retbuf in RDI / X8; float-ret: fmov FP→GP), `ADD`, `RET`. Verified: 697 green on all 8 modes (x64 scalar/sret/pack + aa64 sret/pack spill, within the VM 7-word cap); 698 green on the 5 native lanes (aa64 scalar spill = the 9-int repro now returns 45; float-scalar return + arg spill), xfailed on the 3 VM modes (VM dispatch cap — line 14); unit `*_funcvalue_spill_test.bn` pin no-SetError.
- **B0 step 2b (native force-emit) — DONE, LANDED `cd417081` (rebased SHA `df496851`)**: `collectFuncValueRefs` (both backends) now also adds every `.bni`-exported non-extern func, mirroring codegen's `addExportedFuncsToSeen` (LLVM half, rebased SHA `e80c49b8`). Unblocked by Stage B. (B0 step 1 = rebased `d6d60b00`, Stage A = `a7c462a5`.) Verified: `ir`/`types`/`mangle` native unit tests pass on both backends (the exact packages that previously failed); gen1→gen2 self-compile builds; full `builder-comp` conformance green (1360 passed, 0 failed). Pre-existing-and-unrelated: `pkg/binate/native/aarch64`'s link-and-run tests (`TestEmitEmptyMainLinksAndRuns` / `TestEmitCallExitsWithCode`) fail under the `builder-comp_native_x64_darwin` CROSS mode (cross-linking aa64 code from an x64 harness) — confirmed failing WITHOUT 2b too; not a funcval issue.
- **B0 step 3 (the Functions table) — IN PROGRESS**:
  - **3a `Sig` serializer — DONE, LANDED `d277c7d3`**: `types.Type.SigString` renders a func-value type as `(params)(results)` via `QualifiedTypeName` (deterministic, BUILDER-compatible); 3 unit tests. (Limitation: array/func PARAM types use QualifiedTypeName's placeholder — fine while Sig is opaque in Phase B.)
  - **3b-i ABI layout bump (empty table) — DONE, committed `211be04f` (worktree, NOT landed; 3b-i is a valid/landable ABI on its own)**: `reflect.Package` grew `{Name}` → `{Name, Functions *[]@FunctionInfo}` + the full `FunctionInfo` struct (all 5 payload fields per D1, one ABI bump) in lockstep across `reflect.bni` + the LLVM emitter (`emit_pkg_descriptor.bn`) + the shared-native emitter (`common_pkg_descriptor.bn`). `Functions` emitted EMPTY `{null,0}`. Node grows native 32→48 B, LLVM payload `{ptr,int}`→`{ptr,int,ptr,int}`. Verified: descriptor unit tests (codegen + native/common) green; existing `_Package` conformance 532/708/709 green on native + VM; gen1 builds; VM + hygiene green.
  - **3b-ii populate the table — DONE, LANDED on main (`aa698e5d`)**: per `.bni`-exported non-extern func, emit a static-managed `FunctionInfo` node (header + 8-word payload) + a `*[]@FunctionInfo` pointer array, wire `Functions.{data,len}`. `Pkg @Package` (immortal back-ref — managed, per the immortal-refcount design; NOT `*Package`); `Name` = `f.Name` verbatim (already fully-qualified via NewFunc/QualifyName); `Value` = `&@__handle.<mangled>`; `ResultSize` = `SizeOf(result)`; `ParamSlots` = `len(params)`; `Sig` = `SigString`. Landed SHAs: `bfa1ed89` (LLVM half, `emit_pkg_functions.bn` + `emitPackageDescriptor(m)`), `aa698e5d` (native half, `common/common_pkg_functions.bn` + per-arch). Test is **conformance/725** (renumbered from 720 at land — collision). Verified: 725 green on LLVM host + both native cross modes + gen1→gen2; xfailed on 3 VM modes (Gap 2); full conformance green on `builder-comp` (1385) + both all-native modes (no duplicate symbols).
  - **The `@Package` dtor-handle gap — ROOT-CAUSED + FIXED, LANDED `d0b6fc78`**: `FunctionInfo.Pkg @Package` makes `FunctionInfo` need a dtor. A managed type declared in an INTERFACE-ONLY package (reflect) generates its dtor LOCAL in the defining package, but its dtor HANDLE was never emitted: the defining pkg doesn't reference its own dtor as `OP_FUNC_HANDLE`, and the native CONSUMER *skipped* cross-package `OP_FUNC_HANDLE` refs (the `lookupFuncValueType` extern gate). LLVM never hit it (consumer-side `EmitFuncHandle` emits the weak triple unconditionally). Fix: native `lookupFuncValueType_{x64,AA64}` now synthesize a sig for extern handle targets, so the consumer emits the WEAK `(shim,vt,handle)` triple (deduped via `N_WEAK_DEF`), matching LLVM. (Earlier I wrongly proposed `Pkg *Package` to dodge the dtor — REJECTED by owner: `*Package` breaks assignability to anything taking `@Package`; immortal refcounts exist exactly so `@Package` works on an immortal node. Reverted.)
  - **Post-land adversarial review (8 agents) — DONE; 0 correctness/ABI bugs, 5 coverage gaps, addressed + LANDED**: review confirmed LLVM↔native byte-identical node layout, correct field targets/offsets, the weak-triple dtor fix, and the multi-return tuple ResultSize (LLVM `functionResultSize` == native `FuncResultSize`). Coverage filled: `8bfceb41` (aa64 `lookupFuncValueType` extern-synthesis unit test — was x64-only; multi-return ResultSize unit tests), `0458f71a` (**conformance/727** — table across multi-return tuple [ResultSize 16], wide 7-param [ParamSlots 7], and float). One gap NOT added (noted, owner's call): a USER interface-only package with *real* (non-static) destruction — 725 already exercises the fix's purpose (handle resolution; links+runs+correct output) and real destruction isn't observable without `rt.Refcount` plumbing, so marginal value.
  - **Remaining for B0**: nothing in B0 step 3 — done. (Whole-package auto-injection / dropping the VM's hardcoded extern table is the Gap-2 VM-backend project, separately deferred.)
- **Closure-shim cousins — FOLLOW-UP (not Stage B; user owns)**: the closure shims (`emitClosureShimFast_*` / `emitClosureShimStackSpill_*` / the closure-aggregate shims) (1) still count USER words via raw `ArgWords` (the indirect-large divergence, per line 10), and (2) don't marshal float-scalar USER args GP→FP at all (the non-closure shim does). Both are latent miscompiles for closures with managed-slice/iface or float params. B0's force-emit only emits NON-closure triples (top-level exported funcs aren't closures), so these don't block B0 — Stage B has now landed, so this is a ready-to-pick follow-up (the non-closure spill in `*_funcvalue_spill.bn` is the reference to mirror).

### Lexer leading-zero integer (`0123` / `00`) splits into two INT tokens — ✅ RESOLVED
- `scanNumber` now consumes the digit run after a leading `0` (the `leadingZeroInt` branch, `pkg/binate/lexer/scan.bn`), emitting ONE token that upgrades to FLOAT on a `.`/`eE` tail (`0123.5`) else a single ILLEGAL.  Non-xfailed unit tests (`pkg/binate/lexer/scan_test.bn`) assert `0123`→ILLEGAL, `00`→ILLEGAL, `0123.5`→FLOAT.  (Split from the lexer-Ch.5 entry; the two escape-decoding gaps remain open in claude-todo.md.)

### Named func-value type (`type Fn @func(...)`) is unconstructible — all backends — PRE-EXISTING — REF-HALF ✅ RESOLVED (binate `e1dcd14e` 2026-06-11); literal-half 🔴 OPEN (tracked follow-up)
- **DESIGN (decided 2026-06-11)**: named func-value types are **constructible from func REFERENCES / literals but NOMINAL for func VALUES** (parallel to named scalars — untyped-literal construction, nominal typed values). So `var f Fn = dbl` (ref) and `var f Fn = func(...){}` (literal) should work; `var f Fn = g` (a `@func` value) stays rejected.
- **REF-HALF ✅ RESOLVED (`e1dcd14e`)**: `var f Fn = dbl` (+ raw `*func` named types + reassignment) now construct and call correctly on all modes. The originally-proposed `checkExprWithFVHint`-peel was the LITERAL half; the REF half was actually two checker peels (`AssignableTo`'s func-ref arm + `checkCallExpr`) **plus the real root fix in IR-gen**: `typeDeclEntryType` (moved to `gen_typedecl.bn`) now represents a named func-value type transparently as its underlying `@func`, because func values carry no IR-level nominal identity and every consumer (construction / call dispatch / copy / dtor / refcount) keys off the func-value kind — a TYP_NAMED wrapper made each mis-handle it (call → direct global ref to a nonexistent symbol; dtor skipped). Stripping once at the source routes the value through all existing `@func` machinery (no missed-site UAF/leak risk). Cells: `named-func-value-construct` (un-xfailed), `named-func-value-reject-value` (locks the value-rejection); unit `gen_typedecl_test.bn`.
- **LITERAL-HALF 🔴 OPEN**: `var f Fn = func(...){}` still rejected (`conformance/regressions/named-func-value-construct-literal`, xfailed all modes). Needs `checkFuncLit` to RETURN the named type when hinted by one (so the literal is `Identical` to `Fn` — a `@func` value isn't assignable to the nominal `Fn`) AND `isManagedFuncValueLit` (`gen_func_lit.bn:192`) to peel TYP_NAMED. This is the **memory-sensitive** piece: a func literal can CAPTURE, so the stack-vs-heap-alloc + refcount classification must be right (validate under guard-malloc). `checkExprWithFVHint` (`check_expr.bn:30`) must also peel the hint so the literal gets the `@func` flavour.
- **Symptom**: `type Fn @func(int) int; var f Fn = dbl` → rejected "cannot assign func(...) to Fn"; `var f Fn; f = func(x int) int {…}` → "cannot assign <unknown> to Fn". The anonymous spelling `var f @func(int) int = dbl` WORKS (prints 42). So a named func-value type can be declared but never constructed.
- **Root cause**: `checkExprWithFVHint` (`pkg/binate/types/check_expr.bn:30-39`) installs the func-value flavour hint only when `hint.Kind` is TYP_FUNC_VALUE / TYP_MANAGED_FUNC_VALUE; it never peels TYP_NAMED/ALIAS/READONLY. A named func-value resolves to TYP_NAMED, so the hint is dropped and the literal defaults to raw `*func`. Broader: AssignableTo's named-func-reference arm (`types_assignable.bn:69-73`) also doesn't peel the named dst, so even `var f Fn = someTopLevelFunc` fails. Shared by ALL func-value hint sites (plain `=`, var-init, return-slot, call-arg); `e15680d7` routed plain `=` through the SAME pre-existing single-peel-short guard, so this is not a regression from it.
- **Severity**: MAJOR — a whole supported, tested feature (`conformance/matrix/globals/noinit/named-func.bn` declares one) is unusable; spurious compile-time rejection (fail-safe, no miscompile). Workaround: use the anonymous `@func(...)` spelling.
- **Fix**: peel transparent wrappers in `checkExprWithFVHint` before reading `hint.Kind`, AND peel the dst in AssignableTo's func arms. Touches the shared hint mechanism.
- **Test**: `conformance/regressions/named-func-value-construct` (xfailed all modes, binate `a77591e0`). Cells at each assignment position + a unit test still wanted.
- **Discovery**: 2026-06-09 CR-2-batch review (B2 finder); runtime-confirmed (named rejected, anon works).

---

### Interface syntax revision — *Stringer / @Stringer + top-level decl — ✅ DONE (`iv == nil` is intentionally REJECTED by design — use `present(iv)`; not a gap)
**Split 2026-06-14**: resolved bulk archived here; the open residual is tracked as a slim follow-up entry in claude-todo.md.
- **Plan doc**: `explorations/plan-interface-syntax-revision.md`
  (RATIFIED 2026-05-01).
- **Implementation status (audited 2026-05-22 / 2026-05-23)**:
  Plan §1–§5 all landed.  §6 (`any` universal interface) landed
  end-to-end across type-checker (`e5f2f8a`) and IR-gen + codegen
  (`61eb6cd`): universe `any` is a real empty-method-set
  TYP_INTERFACE registered in both `pkg/types` (via
  `defineInterface`) and `pkg/ir` (via `registerUniverseAny` at
  `InitModule` time). `wrapAsIfaceValue` synthesizes a per-(T, any)
  ImplInfo on demand so codegen emits
  `__ivt.bn_<T_pkg>__<T>__any` as `[1 x i8*]` with T's dtor in
  slot 0 (or null if T has no dtor).  `@any` of a managed-field-
  bearing pointee now RefDec's the pointee's managed fields at
  scope exit via the synthesized vtable's dtor slot — the
  previously-silent leak is closed.
  Verified working: top-level `interface X { ... }` decl
  (`pkg/parser/parse_decl.bn:35`), `*Iface` / `@Iface` syntax
  (`pkg/types/resolve_type.bn:38-50`), bare-name rejection
  (`resolve_type.bn:30-35`, test 348), interface alias
  `interface X = Y` (test 369), construction-site explicit-only
  conversions (`types_assignable.bn:149-189`, tests 379/380/381),
  five receiver kinds + `impl T : Iface` (tests 357–410), per-
  (impl, interface) vtable codegen (`pkg/codegen/emit_impls.bn:24-40`),
  cross-package `.bni` interface visibility (tests 373–388, 464),
  universe `any` (tests 470–474, plus
  `pkg/ir/gen_iface_vtable_test.bn` for vtable-name mangling
  including the empty-pkg form).
- **Remaining (small) gaps**:
  1. **`type X = BareIface` explicit negative test** — the code
     flow should reject via `resolveTypeExpr`'s bare-interface
     error path, but it isn't separately covered. One-line
     negative test.
  2. **Interface-value nil comparison** — `iv == nil` (for any
     iv type, not just `*any`) is currently rejected:
     `IsNillable` in `pkg/types/types_query.bn:196` returns true
     only for pointer types and function-value types.  A nil iv
     IS a meaningful runtime state (both data and vtable slots
     zero, mirroring `*func(...)`'s convention), so the natural
     extension is to add `TYP_INTERFACE_VALUE` /
     `TYP_INTERFACE_VALUE_MANAGED` to `IsNillable`'s positive
     set and check both slots zero at the comparison site
     (codegen + VM lowering for `iv == nil`).  Not a regression;
     pre-existed plan §6 — surfaced while writing a nil-
     propagation test for the iv→any upcast.  This is a real
     language-semantics extension that should be confirmed
     before implementing.

### `=` (assignment) multi-bind from an interface dispatch / func-value call mistyped every component as int — FIXED 2026-06-08 (`f8916b88`)
**Split 2026-06-14**: resolved bulk archived here; the open residual is tracked as a slim follow-up entry in claude-todo.md.
- **Found by the Plan-2 adversarial review.** `genMultiAssign` (`pkg/binate/ir/gen_assign_multi.bn`, the `a, b = …` form) derived per-component result types only from `lookupFuncResults(val.StrVal)` for a DIRECT call (`OP_CALL`). An interface dispatch (`OP_CALL_IFACE_METHOD`) and a func-value call (`OP_CALL_FUNC_VALUE`) have no callee name, so retTypes stayed empty and every component defaulted to `int`: a sub-word component was stored as i64 (invalid IR → clang reject) and a managed component skipped its Axiom-3 copy-RefInc (latent UAF if it had compiled). `a, b = iv.m()` / `a, b = fv()` with any non-int component thus failed to compile; the `:=` form (`genShortVar`) already had the `multiReturnFieldTypes` fallback, so the asymmetry hid it. Became reachable once iface/func-value multi-return dispatch started working (the CR-2 SEAM `6c39d460` + iface-dispatch-by-value `43cb195d` + func-value destructure `2a77188c`); no test caught it because the whole abi multi-return matrix binds with `:=` and uses only int/u16.
- **Fix**: mirror genShortVar's fallback in genMultiAssign (derive component types from the multi-return tuple struct when retTypes is empty). Additive. Pinned by `gen_assign_multi_test.bn` TestMultiAssignFuncValueCallCopyRefInc (verified red without the fix); end-to-end (uint16,int) and (int,@[]int) `=`-form iface + func-value repros compile/run, 200k-iter managed loop balances.
- **OPEN follow-ups (from the same review)**: (a) **coverage** — extend `conformance/gen-abi-matrix.py` with an `=`-form (assignment) binding axis + a managed-component type for the multi-return-through-dispatch cells (the surface that hid this bug; today all cells use `:=` and int/u16 only).

### `int64 << int` rejected in 32-bit-int modes → breaks ALL 32-bit-int compilation — REGRESSION from `efeb0f94` — ✅ RESOLVED 2026-06-10 (binate `fd3cb7ac`)
**Split 2026-06-14**: resolved bulk archived here; the open residual is tracked as a slim follow-up entry in claude-todo.md.
- **✅ RESOLVED 2026-06-10 (binate `fd3cb7ac`).** Root-caused as a TYPE-CHECKER + IR-gen defect, NOT the missing source cast of the initial diagnosis. Per the user's semantics decision: a shift `x << y` / `x >> y` takes its result type from the LEFT (value) operand, and the count `y` may be ANY integer type, independent of the value (Go semantics). Fix: (1) checker `check_expr.bn` — shifts get their own arm instead of being lumped with the symmetric bitwise ops `& | ^` (which unified the operands via commonType → "mismatched types"); untyped-operand cases still defer to foldIntBitwise (byte-identical to before — this matters, see below), a typed-vs-typed pair returns the left operand's type; (2) IR-gen `gen_binary.bn` — a shift's result type is the value (left) type, not the symmetric widenType (which would narrow the result to `int` for `int64 << int` in 32-bit-int, silently truncating). `cast(int64, 1) << (width - 1)` (`types_query.bn:168`) now compiles as-written. Verified: native conformance 1337/0, VM 1307/0, gen2; unit ir/types/codegen/vm/native 8/8; cell `regressions/shift-count-any-int-type`. **arm32 unit/conformance confirmation pending CI on `fd3cb7ac`.**
- **A dead-end worth recording**: a first, larger rewrite (a dedicated `emitShift` that also fixed the count-wider OVERSHIFT corner by widening the value) was correct on native but **regressed signed sub-word shifts on the bytecode VM** — identical-looking IR, different VM result (`int8(1) << 2` → -64). Reverted for the minimal change above. Separately, the checker's `return lt` for an UNTYPED count (vs deferring to foldIntBitwise's commonType) also broke signed sub-word `>>` on the VM (`(-i8v) >> 4` → 0 not -1); hence the minimal checker only short-circuits the typed-vs-typed case. The VM-fragility of these paths is real but was avoided, not fixed.
- **Symptom**: `pkg/binate/types/types_query.bn:168` is `var shifted int64 = cast(int64, 1) << (width - 1)`. In 32-bit-int target modes the shift count `(width - 1)` is `int` (32-bit) while the shifted operand is `int64`, so the checker rejected it: `mismatched types int64 and int`. Because `types_query.bn` sits in nearly every package's transitive dependency, the single error cascaded — arm32 unit + conformance failed to compile. Compiles fine in 64-bit-int host modes (where `int`'s width == `int64`), which is why every `-comp*` mode stayed green and the break was invisible to the green legs.
- **Baseline / regression proof**: `builder-comp_arm32_baremetal` Unit was **green at bnc-0.0.7** (commit `ee06ec87`, job `success`); it was **red at `ac738936`**. The offending line landed in `efeb0f94` (2026-06-05, the integer divide/remainder fault-guard work), after the 0.0.7 tag (2026-06-04) → in-window regression.
- **Follow-ups**: (a) ✅ split `pkg/binate/types/check_expr.bn` (binate `a57496e6`) — back under the soft limit; binary-op checking + tests in `check_expr_binop{,_test}.bn`. (b) ✅ comprehensive shift type-pair MATRIX (binate `93d6ecd4`) — `conformance/matrix/shift-typepair/` covers the full (value-type, count-type) product for `<<`/`>>`, asserting permitted + result-type-is-the-value's + value correctness; green on native/VM/gen2. (c) 🟡 OPEN (narrowed) — RUNTIME count-wider OVERSHIFT corner: when the count is a RUNTIME value whose TYPE is wider than the value AND whose VALUE ≡ a small residue mod 2^valueBitWidth (e.g. a runtime `uint16` count of 256 shifting a `uint8`), the count truncates to the value width and overshift is mis-detected (silent wrong value). The CONSTANT case of this (a literal/const count `>= width`, any count type) is now handled — `emitConstOvershiftOrNil` keys on the untruncated `IntVal` (see N1 RESOLVED `11f99ed9`); only the runtime sub-case remains. Reachable only with an absurd RUNTIME count (≥ 2^width); proper fix = do the overshift comparison at the wider (count's) width (VM-safe, not sub-word) before truncating. The matrix deliberately uses count = valueWidth (≤ 64, fits every count type) so it does NOT exercise this corner.
- **Coverage gap (origin)**: `SignedMinForWidth`'s tests ran only in 64-bit-int host mode, so the 32-bit-int break was invisible to the green legs — the recurring "tests only exercise host-int" trap.
- **Discovery**: 2026-06-10 bnc-0.0.8 release-gate verification.

### pkg/std/os O_* flags now compile-time-correct via build.OS — ✅ RESOLVED 2026-06-10 (binate 590906c8); arm32 off_t + VM residuals remain
**Split 2026-06-14**: resolved bulk archived here; the open residual is tracked as a slim follow-up entry in claude-todo.md.
`nativeOpenFlags` (`impls/stdlib/libc/pkg/std/os/os.bn`) branches on
`build.OS` — a per-target compile-time constant from `pkg/builtins/build`
(`ifaces/targets/<key>/pkg/builtins/build.bni`) that the compiler folds —
to emit the correct native open(2) modifier bits for Linux (asm-generic:
`O_CREAT`=0x40 / `O_TRUNC`=0x200 / `O_APPEND`=0x400 / `O_EXCL`=0x80 /
`O_SYNC`=0x101000) vs macOS (0x200/0x400/0x8/0x800/0x80); access modes
(0/1/2) are POSIX-identical and pass through. No runtime `uname` (the
user ruled that out as counter to Binate's compile-time-determinism
goals). The four Linux/host xfails were removed in the same commit, so os
is now green on every unit-test mode except the residuals below.
- **Residual — arm32-linux off_t (still xfailed,
  `pkg-std-os.xfail.builder-comp_arm32_linux`)**: Seek/ReadAt/WriteAt
  pass `int64` offsets, but on ILP32 arm32-linux `off_t` is 32-bit — a
  64-bit arg shifts the `lseek`/`pread`/`pwrite` register-pair arg layout
  and corrupts the call. Fix: use the `*64` variants or a target-width
  off_t (key off `build.Arch`/`build.PtrSize`), then drop that xfail.
- **Residual — os under the bytecode VM (still xfailed: the three
  `-int`/VM modes)**: the VM never interprets `__c_call` (by design); os
  runs under the VM only as the injected compiled package (registered
  native externs, like `pkg/builtins/rt`) — not wired up. Tracked
  separately. `arm32_baremetal` (no filesystem) stays xfailed too.

### Extend hygiene checks to scan `ifaces/` and `impls/` (not just `pkg/`+`cmd/`) — ✅ DONE (sub-todo: .bni cap)
**Split 2026-06-14**: resolved bulk archived here; the open residual is tracked as a slim follow-up entry in claude-todo.md.
- **Goal (user-requested, 2026-06-10)**: `line-length`, `file-length`,
  `bni-doc`, `bn-doc`, `naming` find-roots were `$BINATE_DIR/pkg` (+`cmd`)
  only, so source under `ifaces/`+`impls/` wasn't linted (surfaced by
  `ifaces/targets/**/build.bni`, `a3755cb4`; `file-format` already covers the
  whole tree).  Extend each to also scan `ifaces/`+`impls/`.
- **Approach (user, 2026-06-10)**: extending surfaces ~150 PRE-EXISTING
  violations, almost all in ported stdlib (math/strconv/os, never linted under
  `impls/`).  Do it **one check at a time**: land the backlog fixes for a check
  and enable that check alongside (fix + enable as separate commits, landed
  together).  Triage, never mass-suppress.
- **Status**:
  - ✅ **file-length** — enabled (binate `a8c37bdf`); `.bn` keeps 500/600, `.bni`
    gets a higher 1500/1800 cap (interfaces can't be split like impls).  No
    backlog (largest `.bni` is ir.bni ~1159 < 1500).
  - ✅ **naming** — enabled (binate `4c79b2d1`+`79ca70f2`).  The 9 lowercase-in-.bni
    (`bootstrap.format*` 5 + `rt._call_*` 4) were already whitelisted, but under
    pre-move `pkg/...` paths; repointed to `ifaces/core/...` (latent bug: the
    whitelist would've silently stopped matching once naming scanned ifaces/).
  - ✅ **bni-doc** — enabled (binate `a0a82aa4`+`812c9dd1`).  Added the missing
    package doc to `ifaces/core/pkg/builtins/reflect.bni` (its block documented
    `type Package`, not the package).
  - ✅ **line-length** — enabled (binate `beff4c89`+`2281cabd`).  Wrapped 128
    long lines across 20 stdlib math/strconv files (all wrappable — no
    LONG-LINE-ALLOWED needed); semantics-preserving (numeric-token multiset
    identical per file; math+strconv unit tests green).  Follow-up that the
    wrapping forced: bessel01.bn grew 407→502 (file-length soft-WARN), so its
    asymptotic machinery (pzero/qzero/pone/qone + tables) was split into
    `bessel01_asymp.bn` (binate `4c31ba50`); both files now <300 lines.
  - ✅ **bn-doc** — enabled (binate `56784a86`+`705f4928`).  Fixed all 118: erf
    (4) + gamma (1) coefficient blocks const-grouped (existing section comment →
    group doc); 37 lookup-table vars (bessel01_asymp R/S tables, cosTab,
    Stdout/Stderr, …) + 23 funcs (@Nat methods, os Read/Write/…, Shl/Shr, …)
    documented individually.  Semantics-preserving (numeric-token sequence
    byte-identical per file; math+strconv tests green; os/rt edits comment-only).
- **DONE** 2026-06-10: all five file checks (file-length, bni-doc, naming,
  line-length, bn-doc) now scan `ifaces/`+`impls/`.  ~150 pre-existing stdlib
  violations were triaged + fixed (not suppressed), one check at a time.
- **Sub-TODO (file-length .bni cap)**: consider lowering the `.bni` cap from
  1500/1800 toward 1000/1200; `ir.bni` (~1159) would need refactoring (split
  into sub-interfaces) first.
- **Discovery**: adversarial verification workflow over `a3755cb4`; user asked
  for the extension as a follow-up.

---

### bnc front-end / IR-gen memory blows up (>8.5 GB, OOM) compiling a ~1370-line program — ✅ RESOLVED (fix 1, binate `7804c287`; minbasic OOM gone) — perf follow-ups (2)-(4) SPLIT to an open entry
**Split 2026-06-14**: primary fix (1) is done (minbasic compiles fine); the remaining super-linear factors (2)-(4) are tracked as an open follow-up entry in claude-todo.md.
- **Status (2026-06-05)**: fix **(1)** below LANDED on main (binate
  `7804c287`) — `registerPendingStructDtor`/
  `registerPendingMsDtor` now dedup via a precomputed-name list (`hasName`) with
  the incoming name built once, instead of re-spelling every existing entry per
  call. **Validated**: minbasic `bnc cmd/run` now compiles to a working 270 KB
  binary in **~1 s at 27 MB peak RSS** (was >8.5 GB / OOM-killed after ~15 min);
  `--emit-llvm` 27 MB / 2 s (was 7.5 GB / 54 s / 0 IR lines). `refcount` matrix
  105/0 and the `pkg/binate/ir` unit tests stay green. Fixes (2)-(4) below remain
  as follow-ups — they remove the *other* super-linear factors (unmemoized Type
  queries, O(n) `slices.Append`, `ctx.Vars` rescan) for even larger programs, but
  (1) alone brought minbasic back to tractable.
- **Symptom**: compiling the minbasic example (examples repo, `minbasic/cmd/run`
  — ~1370 lines of `pkg/basic` plus transitive `strconv`/`buf`/`slices`/`errors`)
  drives `bnc` to **>8.5 GB RSS** and it is OOM-killed (SIGKILL) after ~15 min on
  a 24 GB machine. `bni` similarly peaks ~8 GB. M0 (the banner skeleton) compiled
  in seconds; the jump is the M1 interpreter code.
- **Localization — front-end / IR-gen, NOT the LLVM backend**: `bnc --emit-llvm`
  (stops after IR-gen, before the native/LLVM backend) reaches **7.5 GB in 54 s
  and emits 0 IR lines** before being killed. So the blowup is in `bnc`'s
  front-end / IR-gen, not LLVM codegen.
- **NOT raw program size**: `bnc`/`bni` themselves (far larger) build fine.
  Ruled out by probes (all `bnc --emit-llvm`, peak RSS, on a `main` bundle):
  trivial `strconv.FormatFloat` user → light (2 s); recursive/nested managed AST
  types (`Expr{@Expr, @[]@Expr}` + `Stmt`/`Line`) → light; a struct
  `Value{int,float64,@[]char}` returned BY VALUE, standalone → light;
  `Value` + nested AST types + `slices.Append[@Line]` + `buf` together,
  standalone → light; synthetic 10/20/30 functions each building managed
  `Expr`/`Value` → all light.
- **Bisected trigger (a super-linear interaction)**: within minbasic's
  `pkg/basic`, the **parser side alone** (token/ast/lex/parse/parse_expr + the
  basic.bn loader — ~700 lines; nested-managed AST types, `slices.Append`, `buf`)
  compiles LIGHT (2 s). **Adding `value.bn`** — 34 lines: a
  `Value{int,float64,@[]char}` struct + two by-value constructors, *not even
  referenced by the parser side* — flips it to an **8.56 GB blowup**. Each piece
  is light in isolation; the combination is not. Cost appears super-linear in
  (functions × managed-types) within one package, but is NOT reproduced by
  synthetic isolations — the real parser-side code's structure matters.
- **Repro**: (full) build `examples/minbasic/cmd/run` against a `main` `bnc`
  bundle → OOM. (reduced) the same package with the eval-side files
  (eval/exec/print/format/env) removed and `runProgram` stubbed, leaving the
  parser side + `value.bn`, still OOMs at ~8.5 GB; removing `value.bn` makes it
  light (~2 s).
- **Discovery**: 2026-06-05, building minbasic M1 slice 1 (examples `5b55644`).
- **Root cause (triaged 2026-06-05, 5-agent static analysis — strong
  cross-corroboration; all five independently fingered the same site)**: the
  dominant term is **`registerPendingStructDtor` / `registerPendingMsDtor`**
  (`pkg/binate/ir/gen_util_refcount.bn:96-102` / `:143-149`). Each call does a
  linear dedup scan of the **module-global** `pendingStructDtors` list AND, for
  **every** existing entry, *recomputes* `dtorNameForType(entry)` — a `buf.New()`
  managed-slice allocation + a recursive type-spelling walk + `Bytes()`. It is
  invoked from `emitStructCopy`/`emitStructDtor`, which fire at every
  managed-AGGREGATE copy/dtor/scope-cleanup site (var-init, assignment,
  composite-literal field/element, return, and every scope-exit cleanup for every
  managed-aggregate local) across **all** functions; the list grows monotonically
  for the whole package. Net **O(functions × managed-aggregate-types)** with a
  throwaway name-buffer allocation per existing entry per call → both the 54 s
  time and the multi-GB transient/persistent RSS, all before a single IR line.
- **Why `value.bn` is the trigger**: before it, the parser side holds its AST via
  `@Expr` / `@[]@Expr` — managed **pointers/slices**, which take the *scalar*
  refcount arms (`EmitRefInc`/`emitManagedSliceRefDec`), NOT
  `emitStructCopy`/`emitStructDtor`, so `pendingStructDtors` stays ~empty.
  `Value{int,float64,@[]char}` is a managed-**aggregate** (`needsStructCopy` via
  the `@[]char` field), so the moment any `Value` is copied/dtor'd/cleaned-up the
  *aggregate* arms fire across the package's many functions — flipping the
  dominant term from ~0 to `functions × aggregate-sites`.
- **Amplifiers (corroborated, secondary)**: (a) `slices.Append` (stdx) is **O(n)
  per append** — `make_slice(n+1)` + copy-all, no capacity doubling — so every
  hot IR-gen accumulator (`pendingStructDtors`, `ctx.Temps`, `ctx.Vars`, return
  `vals`) is O(n²); (b) `NeedsDestruction` (`types_query.bn:377`) and
  `SizeOf`/`AlignOf`/`FieldOffset` (`scope.bn:112/160/207`) are **unmemoized**
  (no cache slot on `@types.Type`, `types.bni:71`), recomputed at every emit-site;
  (c) `emitDecForManagedLocals` re-scans **all** `ctx.Vars` at each scope-exit;
  (d) `resolveTypeExpr` allocates a fresh `@Type` per type-expr occurrence (no
  interning); (e) `lookupFuncParams`/`collectFuncStrings` do O(n) linear scans.
  The unifying disease: **no memoization on the `@types.Type` node + module-global
  accumulators scanned/re-mangled linearly.**
- **Fix (ranked, layered)**: **(1) PRIMARY** — make the
  `registerPendingStructDtor`/`registerPendingMsDtor` dedup O(1): compute the
  dtor name once for the incoming type, look it up in a set (or hang a
  `DtorRegistered` flag / cached name on `@types.Type`); never recompute
  `dtorNameForType(existing)` in the loop. This alone removes the dominant
  O(functions × types) + per-entry-allocation term. **(2)** add cache slots to
  `@types.Type` and memoize `NeedsDestruction` + `SizeOf`/`AlignOf`/`FieldOffset`
  + the dtor/copy name (layout is fixed within a compile). **(3)** give `slices`
  a capacity-doubling amortized-O(1) append (or use growable buffers for the hot
  accumulators). **(4)** track managed-cleanup slots in a compact per-function
  list instead of re-scanning `ctx.Vars`. (1) is the high-leverage fix; (2)-(4)
  remove the remaining super-linear factors.
- **Validation suggested**: instrument `registerPendingStructDtor`'s call-count ×
  list-length (or a knob-scaled repro: N managed-aggregate types × M functions)
  to confirm the O(N×M) curve, then re-run the reduced minbasic repro after fix
  (1). No `bnc` profiling flag exists; a temporary counter is the cheapest probe.

### ~~Generic-instantiated composite-literal head `Foo[T]{…}` not built by the parser — spec §13.10 (2026-06-13)~~ — ✅ LANDED on main (binate `d005a11e`, 2026-06-14)

`Foo[int]{...}` mis-parsed: `parseIdentOrCompositeLit` recognized only
`Type{` / `pkg.Type{`, so the postfix `[int]` became an
`EXPR_INSTANTIATE_OR_INDEX` and the trailing `{...}` was orphaned (a
parse error). `continuePostfix` now reinterprets a bracket head followed
by `{` (outside a no-composite context) as a `TEXPR_INSTANTIATE`
composite type and parses the body; new `exprToCompositeTypeExpr`
converts the head + type args (ident / pkg-qualified / `@T` EXPR_TYPE /
`*T` / nested generic), returning nil for a non-type head so a real
value index is left alone. Downstream already handled a
`TEXPR_INSTANTIATE` composite head (`checkCompositeLit` /
`genCompositeLit` resolve via `resolveTypeExpr`). Tests:
`TestParseGenericCompositeLit` + `TestParseIndexNotGenericComposite`;
conformance `776_generic_composite_lit` (keyed + nested) green on
builder-comp / -int / -comp.

### Native backends mis-lower float consts/returns — `541` silently reads 0 (Phase A float-const gap on the native code generators) — ✅ RESOLVED (binate `5281b138` + `cc6d0e9b` AAPCS64 D0 float-return + `1285683e` runtime link; `541` green on native aa64)
- **Symptom**: `conformance/541_cross_pkg_const_float` passes on the
  default C/LLVM-backed modes but **fails on the native aarch64 backend**
  (`builder-comp_native_aa64-comp_native_aa64`): expected `7 -3 7 -3 9`,
  actual `7 0 0 …`.  Two distinct silently-wrong cases (both → `0.0`):
  1. **Negative float const** — `cfg.NegHalf` (`= -1.5`) read cross-package
     reads as `0.0` (line 2).  The positive sibling `cfg.Ratio` (`= 3.5`)
     read the same way (cross-pkg `EXPR_SELECTOR`) is **correct** (line 1 → 7),
     so positive `EmitConstFloat` + float-mul + `cast(int, float)` all work
     on the native backend; only the **negative/unary-minus-folded** float
     literal mis-lowers.
     **FIXED 2026-06-03 (binate `5281b138`)**: the root cause was
     `common.ParseFloatLitToBits` (the shared text→bits converter used by
     every native backend) silently dropping a leading `-` in the folded
     literal text and returning 0; it now honors the sign.  Verified at unit
     level (`TestParseFloatSigned`) and via `541` on the VM modes (the VM was
     made to route through the same converter).  The native aa64 *lane* can't
     confirm end-to-end because it no longer links (the duplicate-symbol entry
     above), but the converter is the shared piece and native's emit path was
     already correct for positive consts.  Case 2 below was subsequently FIXED (`cc6d0e9b`, AAPCS64 D0 float-return).
  2. **Float function return** — `cfg.Scale()` (returns `Ratio` via an
     in-package `EXPR_IDENT` read) reads as `0.0` (line 3), ditto
     `cfg.NegScaled()` (line 4).  Either the native float-return ABI (value
     should arrive in `d0`, caller reads 0) or the in-package `EXPR_IDENT`
     float-const read is broken — 541 alone can't disambiguate (need a
     direct-return-vs-direct-read probe).
- **Discovery**: 2026-06-03, running `./conformance/run.sh
  builder-comp_native_aa64-comp_native_aa64` (the aa64 lane the user
  watches).  `541` has **no xfail markers** and its own header explicitly
  intends cross-backend stability ("cast-to-int keeps the expected output
  stable across backends"), so this is a genuine native-backend correctness
  hole, not an intended skip.
- **Why MAJOR**: silent wrong float values (reads 0 instead of the real
  value) on a shipping backend — the exact silent-miscompile class.  The
  IR-gen Phase A fix (above, line ~462) is correct at the IR level; the gap
  is in the **native code generators** (`pkg/binate/native/{aarch64,x64}`),
  which Phase A never validated (it was checked on the C/LLVM modes only).
- **All residuals (a)/(b)/(c) closed — verified 2026-06-14**: `541` and `534`
  pass on every mode including native aa64 (both 0 xfail markers; native_aa64
  CI green).  (a) native_x64 does NOT fail (541 not in its failure set); (b)
  case 2 was disambiguated + fixed (`cc6d0e9b`); (c) 534 needs no xfails.

### ~~Wire `--version` into bnc / bni / bnas / bnlint~~ — ✅ LANDED on main (binate `8ff87399`, 2026-06-14)

Each tool now detects `--version` before the rest of arg parsing and
prints `<tool>-` + `version.Version` (e.g. `bnc-0.0.10-pre`) to stdout,
then exits 0. Single source of truth is `pkg/binate/version.Version`.
The 2026-06-03 deferral was gated on BUILDER being able to compile
`cmd/bnc` reading the version extern var cross-package; verified the
current BUILDER (`bnc-0.0.9`) handles it (tested a `version.Version`
read directly), so all four landed together. bnc uses a `hasVersionFlag`
helper (+ `TestHasVersionFlag`); bni/bnas/bnlint inline the scan (bnas
compares with `charsEqual`, the others `streq`); bni stops at `--` so a
program's own `--version` isn't intercepted. `release-process.md`
step-4 smoke + the VERSION-manifest note updated to confirm-by-banner.

### ~~pkg/std VM inject-all: inject + factor + hygiene check~~ — ✅ LANDED on main (2026-06-14)

Every `pkg/std` package is injected into the bytecode VM (backed by the single
compiled instance): `errors`/`io`/`strconv`/`math`/`math/big`/`strings` are
native-only (lowered-skipped + fully injected — functions, globals, managed-struct
dtors, and interface-impl vtables via the shim-route `93f75f27`); `os` is lowered
+ injected (its `__c_call` funcs dispatch to native while its pure funcs run as
bytecode). `os/internal` was folded into `os` as an unexported `#[build]`-gated
`errno()` (`55b3b044`), so the invariant is simply "every pkg/std package is
injected" — no exemptions.

- **List-factoring** (`8e45cc7e`): the native-only set lives in one source of
  truth — `nativeOnlyStdPkgs()`, a path↔`_Package()`-thunk table iterated by both
  `isNativeOnlyInVM` and `injectStdlibExterns`.
- **Hygiene check** (`452bc970`): `scripts/hygiene/stdlib-injected.sh`
  (auto-discovered by `run.sh`) enumerates `ifaces/stdlib/pkg/std/*.bni` and fails
  if any package isn't injected via `nativeOnlyStdPkgs()` or an explicit
  `RegisterPackageFunctions(vmInst, <pkg>._Package())` call. No exemption list.
- Related cleanup: `impls/stdlib` flattened (`5ae15031`; `common -> .` compat
  symlink) since stdlib needs no per-platform dirs (all `#[build]`-gated), and the
  layout spec updated to match.

### Stale `native_x64` (ELF) iface-multi-return xfails — REMOVED (binate `10798d42`) — 2026-06-10 (Lane B)
- **What**: the 16 markers `conformance/matrix/abi/iface-multi-return{,-assign}/{int,u16}/{2,3,4,5}.xfail.builder-comp_native_x64-comp_native_x64` blamed "iface dispatch multi-return: native tuple-packing not yet implemented". That packing **IS implemented** (`pkg/binate/native/x64/x64_iface.bn` routes `OP_CALL_IFACE_METHOD` multi-returns through `collectMultiReturnTuple`), and the **identical-codegen** `builder-comp_native_x64_darwin` (Mach-O; same `pkg/binate/native/x64` backend, only object format differs) **PASSES all of these cells** (Lane B run 2026-06-10, and already noted in `03b80566`). ELF also passes the un-xfailed `multi-return` / `funcval-multi-return` / iface `f64` / `iface-param` / `iface-return` cells, so iface dispatch and multi-return both work there — these int/u16 markers were the lone stale holdouts.
- **Removed** on the x64-darwin evidence (user-authorized 2026-06-10). The ELF mode isn't locally runnable on macOS/arm64 (no `qemu-x86_64`), so **CI is the confirmation point**: it runs ELF natively on the x86-64 ubuntu runner and will exercise these 16 cells once Lane A's `-comp*` link break clears. Expected green; **treat any ELF failure as a real x64-ELF-specific bug to fix (not a re-xfail).** (arm32 iface-multi-return xfails left in place — different, less-complete backend.)

- **REMAINING — x64 float32 cross-package native↔LLVM ABI mismatch (tracked, NOT a regression):** an adversarial review of the float64 commit found that a sub-8-byte float (float32) multi-return component COALESCES into a shared eightbyte on SysV-AMD64 — `(float32,float32)` → one SSE eightbyte (XMM0), `(float32,int32)` → one INTEGER eightbyte (RAX). The native x64 pack/collect (`multiReturnEightbyteIsSSE`-driven, self-consistent) still disagree with LLVM's actual x64 float32 ABI, so cross-package float32 reads garbage / faults. aa64 is correct (each float gets its own D register). `conformance/684_cross_pkg_mr_f32` pins this **xfailed on native x64** (passes aa64/LLVM/VM). float32 multi-return was always broken (the integer-only path); this surfaced it. **Fix direction:** dump LLVM's actual register usage for an x64 float32 multi-return (the `F32F32`/`F32I32` `.ll`/asm), then align the native x64 pack/collect — the per-eightbyte `emitMultiReturnPack` is the groundwork. The aa64 per-field scheme is already correct, so this is x64-only.
- **CORRECTED ROOT CAUSE — empirically dumped 2026-06-10 (the bullet above had it BACKWARDS), and the bug is BROADER than float32:** our LLVM backend emits LITERAL struct return types (`{float,float}`, `{float,i32}`, `{i16,i16,i16}`, `{i32,i32}`, …) and LLVM lowers a first-class IR aggregate return **purely FIELD-PER-REGISTER, with NO SysV eightbyte coalescing** — confirmed by lowering hand-written `.ll` with `clang -S --target=x86_64-*` (Darwin == Linux): `{float,float}`→XMM0,**XMM1**; `{float,i32}`→XMM0,**EAX**; `{i16,i16,i16}`→AX,DX,**CX**; `{i32,i32}`→EAX,**EDX**; `{i64,double}`→RAX,XMM0. So the native x64 **eightbyte-coalescing** model (`multiReturnEightbyteIsSSE`, packs `(i32,i32)`/`(f32,f32)` into ONE register) is the WRONG model for native↔LLVM agreement: it only COINCIDES with LLVM when every field is a full 8 bytes (`(int,f64)`/`(f64,f64)` — why 683 is green). It DIVERGES for **every sub-8-byte field** (`(f32,f32)`, `(f32,i32)`, `(u16,u16)`, `(i32,i32)`, …) crossing the native↔LLVM (hybrid: native main + LLVM dep) boundary → silent garbage. **The abi matrix never caught this because its multi-return cells are SAME-MODULE** (`package "main"`, callee inline → native↔native self-consistent), so only the cross-package 683/684 exercise the boundary. **aa64 is already correct because it does FIELD-PER-REGISTER** (each leaf → next reg of its class), matching LLVM (684 green on aa64). **FIX = replace the x64 eightbyte-coalescing pack/collect with FIELD-PER-REGISTER-BY-CLASS** (int leaves → RAX,RDX,RCX,… ; float leaves → XMM0,XMM1,… ; store/load at the field's offset), mirroring aa64 + LLVM's literal-struct lowering. NOT a float32 patch and NOT codegen coercion (emitting `<2 x float>`/`i64` would fix x64 but BREAK aa64, since one target-independent IR type can't express both targets' ABIs — clang lowers `<2 x float>` to V0-packed on aa64, which aa64's per-field collect would then mis-read). Need to confirm LLVM's exact GP/FP return-reg sequence + the >N-register sret threshold before implementing. **Surfaced to user as a major finding + design reversal (the b5911fbe eightbyte choice) — user APPROVED the field-per-register rework (2026-06-10).**
- **EXACT LLVM x64 first-class-struct return CC (empirically probed via `clang -S` on hand-written `.ll` with CALLERS that read each field — the definitive register map):** GP-class leaves → **RAX, RDX, RCX** (3 regs; `{i64,i64,i64}` is IN-REGISTER with field 2 in RCX); 4+ GP-words → **sret**. FP-class leaves → **XMM0, XMM1** (2 regs); a 3rd/4th float64 spills to **x87 ST0/ST1** (NOT sret, NOT XMM — `{double,double,double}`/`{...,double}` read the field via `fstpl`); 5+ floats → sret. INTEGER and FP counters are INDEPENDENT and there is **no eightbyte coalescing**. So x64's sret threshold is **register-count-based** (gpWords>3 OR fpCount>2-ish), NOT the 16-byte rule — `{i64,i64,i64}` is 24 bytes yet in-register.
- **BOUNDED FIX PLAN (delivers the greenlit scope + fixes the whole sub-8-byte class):** x64 `emitMultiReturnPack` + `collectMultiReturnTuple` → field-per-register-by-class: a non-float field's words → RAX,RDX,RCX (retGp); a float-scalar field → XMM0,XMM1 (retFp); each stored/loaded at its field offset (mirror of aa64 `collectMultiReturnFields`). Delete `multiReturnEightbyteIsSSE`. x64 sret decision (currently the shared 16-byte `CallReturnsBigMultiReturn`) → an **x64-specific** register-count rule (gpWords>3 OR fpCount>2 → sret), so `{i64,i64,i64}` stays in-register matching LLVM while the same-module abi-matrix (int/3 etc.) stays green (native↔native self-consistent). Keep aa64 on its 16-byte rule (unchanged). Un-xfail 684; add cross-package coverage for `(u16,u16)`/`(i32,i32)`. Verify 683/684 + abi matrix green on aa64 + x64-darwin.
- **LANDED — binate `47ebdbac` (2026-06-10).** x64 multi-return pack/collect are now field-per-register-by-class (RAX,RDX,RCX / XMM0,XMM1 at each field offset); the multi-return sret threshold is target-aware (`CallConv.MultiReturnTupleNeedsSret`, exported): SysV register-count (>3 GP-words / >2 FP-fields), AAPCS64 unchanged (SizeOf>16). `multiReturnEightbyteIsSSE` deleted; the x64 funcval sret classifier `isBigMultiReturn_x64` (from `f0747762`) was reconciled onto the same shared threshold (same-area concurrent commit — its size>16 rule disagreed for `(i64,i64,i64)` funcvals). Conformance 684 un-xfailed both x64 modes; new 693 (`(i32,i32)`,`(u16,u16,u16)`,`(i32,i32,i32)`) added. Verified: 683/684/693 + full abi MR matrix + `funcval-big-multi-return-args` green on aa64 + x64-darwin; unit + hygiene green.
- **SIDE-EFFECT — 526 (`strconv_parse_cross_pkg`, managed-iface multi-return) now PASSES on x64, still FAILS on aa64.** My fix resolved 526 on x64-darwin (its `(int,@errors.Error)` = 3 GP-word multi-return was mis-collected by the eightbyte scheme); `0d29a4b5`'s `builder-comp_native_x64{,_darwin}` xfails for 526 are now STALE → **REMOVED (binate `f895848b`, 526 un-xfailed + verified green on x64-darwin)**. 526 still fails on aa64 (a separate aa64-specific managed-iface-multi-return bug, NOT fixed by this x64-only change) → keep the aa64 xfail; likely related to residual gap (2) below or an iface-value-in-multi-return refcount issue. Track as an aa64 follow-up.
- **RESIDUAL GAPS (loud follow-ups, NOT silently deferred):** (1) **x87 cross-package — ✅ RESOLVED 2026-06-11 (`50850315`).** A multi-return with >2 FLOAT fields crossing native↔LLVM diverged on x64: LLVM x86_64 returns the 3rd/4th float in x87 ST0/ST1 (empirically dumped via clang + a Rosetta run: `{f64,f64,f64}`→XMM0,XMM1,ST0; `{f64×4}`→XMM0,XMM1,ST0,ST1; `{f32,f32,f32}`→XMM0,XMM1,ST0 via FLDS; `{i64,f64,f64}`→RAX,XMM0,XMM1, no x87 — GP/FP counters independent, no eightbyte coalescing; field N→ST0, N+1→ST1), while native x64 sret'd at `fpCount>2`. Pinned by `conformance/698_cross_pkg_mr_float3`. **Option B (force-sret in pkg/binate/codegen) was ATTEMPTED 2026-06-11 and REVERTED** — a codegen sret attribute affects ALL call paths, breaking the LLVM func-value + iface multi-return shims (`abi/{funcval,iface}-multi-return*/f64/{3,4,5}`). **Option A (chosen, localized to native x64, no codegen change):** (a) added FLDS/FLDL (D9/DD /0) + FSTPS/FSTPL (D9/DD /3) to `pkg/binate/asm/x64` (byte-exact vs clang); (b) CallConv gained `NumX87RetRegs` (SysV 2, AAPCS64 0) and the shared sret threshold is now `fpCount > NumFpRetRegs + NumX87RetRegs` — x64 register-returns up to 4 floats while aa64 reduces to the identical `fpCount>8` (untouched); (c) `emitMultiReturnPack` (`x64_return.bn`) pushes overflow floats in REVERSE field order (so field N lands on ST0) and `collectMultiReturnTuple` (`x64_call.bn`) pops ST0-first to the result slot — the spill-everything frame policy collects every MR call so the x87 stack stays balanced even for a discarded result. Un-xfailed 698 on native x64 (darwin + linux; the linux lane is CI's native-x86_64 runner, where 698's 2-float sibling 683 already passes). New `715_x87_mr` covers float32 x87 (FLDS/FSTPS), mixed int+float x87, and a stack-balance stress loop — green on native x64 / aa64 / LLVM / VM. Full x64-darwin suite 1376 passed / 0 failed; abi funcval/iface MR matrix green (no Option B blast radius); aa64 unaffected. (2) **aa64 SAME threshold bug — ✅ RESOLVED (`d206635d` 2026-06-11).** aa64 native used the 16-byte rule and sret'd any 17..64-byte tuple while LLVM register-returns up to 8 GP (X0..X7) + 8 FP (D0..D7); `MultiReturnTupleNeedsSret` now uses the per-target register-count rule (aa64 8/8). This was hit in practice by 526's `(int64, @errors.Error)` (3 GP words) — see the dedicated 526 entry. Float-HFA ≥3-component cross-pkg on aa64 is now register-returned too (D0,D1,D2..), though a dedicated cross-pkg FP-≥3 cell isn't added (the x64 sibling of that shape is the open x87 gap (1), so such a cell would need an x64 xfail). (3) **aggregate FIELDS inside a multi-return** — LLVM flattens; keep current behavior / sret, don't regress.
- **Symptom (direction)**: a multi-return tuple with a FLOAT component (`(int, f64)`, `(f64, f64)`) — the native callee pack (aa64 `aarch64_dispatch.bn:354-385` OP_RETURN multi-return loop; x64 `emitMultiReturnPack` `x64_return.bn:159-201`) has only two arms (aggregate / else-scalar→X-or-RAX/RDX), with NO `IsFloatScalarTyp` branch and no HFA/SSE eightbyte classification (only the LONE-single-scalar-float early return is float-aware). So a float field is packed into an INTEGER register, and the native caller collect reads it from an integer register — native↔native self-consistent, but DIVERGENT from AAPCS64 / SysV-AMD64 + LLVM, which return a float eightbyte in D0/XMM0 (or an SSE-classified aggregate eightbyte in an FP reg). cmd/bnc compiles only the main module natively and routes cross-package callees through LLVM/clang, so a float-component multi-return crossing the native↔LLVM boundary (e.g. an impl method or multi-return func defined in a non-main, LLVM-compiled package) reads the float field from the WRONG register class → silent garbage. Now reachable for iface dispatch too (post-SEAM); still ZERO coverage (abi matrix is int/u16 only).
- **Severity**: MAJOR — silent wrong value at the native↔LLVM ABI boundary on a type-valid shape; narrow trigger (float-component multi-return crossing the boundary) but real and untested.
- **Fix direction**: add `IsFloatScalarTyp` handling (and HFA/SSE eightbyte classification) to the native multi-return callee pack + caller collect on both arches, matching AAPCS64 / SysV-AMD64 + the LLVM legalization. Extend `gen-abi-matrix.py`'s type axis with `f64` for multi-return / iface-multi-return / funcval-multi-return — decisive shapes `(f64,f64)` (HFA on aa64) and `(int,f64)` (mixed INTEGER+SSE eightbytes on x64).
- **Discovery**: 2026-06-08, adversarial review of plan-cr2-3 — the iface-classifier (`cc2ddcc4`) made a float-component iface multi-return reachable; the underlying native multi-return pack was never float-aware. Filed (not fixed) per user decision.

### x64 native backend mis-packs sub-word multi-return + non-8-multiple struct params — ✅ RESOLVED (2026-06-14: the 5 repro cells `abi/multi-return/u16/{3,4,5}` + `abi/struct-param/{three-u32,five-u8}` now pass on native_x64 — 0 xfail markers, not in the native_x64 CI failure set)
- **Symptom**: (a) a sub-word (`uint16`) multi-return at arity ≥ 3 mis-packs the
  3rd+ component; (b) a `3×uint32` (12B) or `5×uint8` (5B) struct passed by value
  as a param loses its trailing field. (x64 struct-RETURN works.) On x64 native.
- **Test**: `conformance/matrix/abi/multi-return/u16/{3,4,5}` +
  `abi/struct-param/{three-u32,five-u8}` (5 cells, xfailed both x64 modes). Pass
  on LLVM + VM (and aa64 multi-return).
- **Discovery**: 2026-06-05, P1 ABI matrix. §3.9. NOTE: the all-int multi-return
  n=2-cap from §3.1 is **FIXED** (arity ≤ 5 all-int passes everywhere).
- **Root cause**: x64 aggregate-arg + sub-word multi-return packing. Needs
  investigation.

### `main` existence/signature not checked at compile time — ❌ NOT A BUG — BY DESIGN (closed 2026-06-14)
Previously filed (2026-06-12) as a missing-diagnostic defect
(`prog.main.unchecked`). That was WRONG — it is **by design**, per the user.
Under **separate (per-package) compilation** the compiler never sees the whole
program, so a valid `func main()` entry point cannot be resolved when a package
is compiled (not without a weird hack). Moreover, **requiring `main` to exist
runs counter to the dual-mode interop story**: any package may be compiled or
loaded independently and have its functions called across the
compiled/interpreted boundary (Ch.19), so a package is never obligated to furnish
an entry point. The entry is resolved at **link / program-assembly** time; a
missing or wrong-shaped `main` surfaces as a link error, which is intrinsic to
the model — NOT a missing diagnostic. **Do NOT re-file this and do NOT add a
checker rule for `main`.** Spec corrected (docs `4af9c72`): §17.3 now carries a
"_Note (by design)_" (the `prog.main.unchecked` ID is retired) and §21.9 no longer
lists it as a non-conformance.

### Global `var` of an interface-value / func-value (or readonly-wrapped aggregate) type emits invalid LLVM (`global %BnIfaceValue 0`) — ✅ RESOLVED — LANDED `91ef4fc4` (verified on main 2026-06-10)
- **Symptom**: any package-level `var x @Iface` / `@errors.Error` / `*func()` / `@func` (with or without an initializer), AND any `readonly`-qualified aggregate/iface/func/struct/array/slice global, made the LLVM backend emit `@<mangled> = global %BnIfaceValue 0` (or `%BnFuncValue 0`, `%bn_main__Pt 0`, …), which clang rejects: `error: integer constant must have integer type` — the whole package fails to compile. Blocked a `pkg/std/io` `var EOF @errors.Error = errors.New("EOF")` sentinel (and any iface/func-value package global).
- **Root cause**: `pkg/binate/codegen/emit.bn` global-var static-zero dispatch — the SAME dispatch as the float-global sibling above — picks the zero by type kind (`null` ptr, `zeroinitializer` slice/struct/array, ` 0.0` float, ` 0` otherwise). Two gaps: (1) the 16-byte address-aggregate kinds (`TYP_INTERFACE_VALUE[_MANAGED]` → `%BnIfaceValue`; `TYP_[MANAGED_]FUNC_VALUE` → `%BnFuncValue`) fell through to ` 0` but are LLVM struct types needing `zeroinitializer`; (2) the dispatch tested `g.Typ.Kind` DIRECTLY while `llvmType`/`IsFloat` unwrap `TYP_READONLY` first, so a `readonly`-wrapped aggregate global got the right printed type but the wrong ` 0` init token. Same code-red "missing iface/func-value arm" + "aggregate-as-scalar" shape, in the global emitter.
- **Fix — LANDED `91ef4fc4` (the orphaned worktree commit `5dddef7d` on `temp-binate-4` was superseded by this functionally-identical landed commit; `f2ebaca1` later extended the dispatch to also peel `TYP_NAMED`)**: add the four address-aggregate kinds to the `zeroinitializer` branch AND unwrap `TYP_READONLY` at the top of the dispatch (mirroring `llvmType`). Verified on main 2026-06-10: `--emit-llvm` emits `@bn_main__g = global %BnIfaceValue zeroinitializer`, `@bn_main__f = global %BnFuncValue zeroinitializer`, `@bn_main__ro = global %bn_main__Big zeroinitializer` (valid, not the invalid ` 0`); full clang compile exit 0. Adversarially reviewed (4-agent workflow): correctness + refcount confirmed (the `__init` store MOVES the fresh value in via consumeTemp; the zeroinitializer prior-occupant RefDec is a verified null-data no-op; immortal sentinel by design, like Go's io.EOF); the readonly variant was the review's blocker finding; no regression to int/bool/char/ptr/float/struct globals. Unit test `pkg/binate/codegen/emit_global_test.bn` (func/iface/readonly → zeroinitializer, not ` 0`; + the float sibling). End-to-end: cross-package `var EOF @errors.Error = errors.New("EOF")` compiles, `__init` runs it, consumer reads it + `.Error()` correct; 1000-iter stress clean.
- **Severity**: MAJOR — hard compile error (not silent), blocked any package-level interface-value / func-value (or readonly-aggregate) global. Discovered 2026-06-07 implementing `pkg/std/io`'s `io.EOF`.
- **Test-gap analysis (the "why wasn't this caught / how to prevent" ask) + FOLLOW-UP**: the defect lived in a structurally-EMPTY matrix intersection — `conformance/matrix/aggregate/global` sweeps the `global` op over {scalar,array,struct}×{int,float} but NOT iface/func kinds; `conformance/matrix/addr-aggregate/{func-value,iface-value}` sweeps those kinds over {direct,copy,return,arg,return-arg,field,array-elem} but has NO `global` op. Neither product's coordinates included "a package-level global of a 2-word address-aggregate", and there was ZERO codegen unit coverage of the module-global path. PREVENTIVE FOLLOW-UP (deferred per the user): add a `global` operation to `conformance/gen-addr-aggregate-matrix.py` (OPERATIONS) → `addr-aggregate/{func-value,iface-value}/global.bn` + a no-initializer companion (sweeping the with/without-runtime-initializer axis), update its README, run hygiene. ALSO unverified: VM (`-int`) + native modes — the VM materializes globals separately (`vm/lower_data.bn`); confirm it handles iface/func-value globals before relying on `io.EOF` in `-int`/native (xfail per mode if not). The unit test is mode-independent and already guards the codegen fix.

### VM: a function value RETURNED from a call and PASSED DIRECTLY as an argument has a nil vtable — CONFIRMED, VM-only — ✅ RESOLVED (binate `e337e413`, `isVMAddressAggregate` single-return copy-back in `lowerReturn`)
- **Symptom**: `use(mk())`, where `mk() @func(...)` returns a (non-capturing)
  function value and `use(w @func(...))` invokes it, aborts in the bytecode VM
  with `vm: function value has nil vtable`. Compiled (native) is correct.
- **Scope**: bytecode VM ONLY (LLVM/native correct). Triggered specifically by
  passing a freshly-RETURNED function value DIRECTLY as a call argument. The two
  halves work in isolation: returning a function value then calling it directly
  via an EXPLICITLY-typed local (`var w @func(...) = mk(); w(x)`) is fine, and
  passing a LOCAL/param function value as an
  arg (`use(w)` with `w` a local) is fine — only the un-materialized
  return-value-as-arg combination loses the vtable word here.  (The
  INFERRED-type spelling `var w = mk(); w(x)` — no `@func` annotation — is
  separately broken on ALL backends; see the `## MAJOR` entry "Inferred-type
  func-value local call mis-lowers to a direct symbol".)  Specifically, only the un-materialized
  return-value-as-arg combination loses the vtable word. Workaround: bind to a
  local first (`var w @func(...) = mk(); use(w)`).
- **Test**: ✅ `conformance/regressions/funcval/return-as-arg` (binate
  `d493b25b`, on the worktree, pending cherry-pick). `use(mk())` returning/
  passing a non-capturing `@func(int) int`, asserts `42`. Verified: compiled-
  final + native pass; the 3 VM-final modes (`builder-comp-int`,
  `builder-comp-int-int`, `builder-comp-comp-int`) abort `nil vtable` and are
  xfailed — un-xfail when the fix lands.
- **Discovery**: 2026-06-05, wiring minbasic's injected `@func` writer
  (`basic.Run(host.NewWriter())`): the VM aborted with nil vtable. Isolated to
  the return-value-as-arg pattern; `bnc-0.0.7`.
- **Why it matters**: blocks injecting a `@func` writer/sink built by a factory
  (`Run(host.NewWriter())`) — a natural DI shape. Together with the iface-vtable
  2-word-slice-arg bug, it leaves only static/direct calls reliable for I/O
  injection on `bnc-0.0.7`, so minbasic uses a clearly-marked static temp
  meanwhile.
- **Fix**: in the VM, marshal a function-value (2-word {vtable,data}) call
  argument that is an un-spilled call result the same way a local/param function
  value is marshalled — the vtable word is being dropped for the return-value-as-
  arg case.

### Unsigned int→float uses a SIGNED conversion in the VM — wrong value — CONFIRMED — UPDATE 2026-06-06: the scalar-diff differential shows the unsigned→**float64** path now PASSES on the VM (so this specific signedness bug appears resolved); a *distinct* int→float32 defect remains — see `vm-int-to-float32` below
- **Symptom**: `cast(float64, y)` for an unsigned int whose top register bit is
  set (on the 64-bit host, only `uint64` with bit 63) yields a NEGATIVE float —
  the VM converts as signed. E.g. `cast(float64, <uint64 bit-63>) > 0.0` is
  true on LLVM, false on the VM.
- **Root cause (CONFIRMED)**: the VM's int→float lowering uses `BC_SITOF`
  (signed) regardless of the operand's signedness; LLVM uses `uitofp` for
  unsigned. The native backends carry the same gap (§3.8). A `uint32` is
  zero-extended (positive in the 64-bit register), so only `uint64` triggers
  it on the host.
- **Test**: `conformance/matrix/scalar/int-to-float/64/unsigned` (xfailed the 3
  VM modes; `/32` passes as a baseline).
- **Discovery**: 2026-06-05, P1 scalar matrix int-to-float cells. Flagged §3.8.
- **Fix**: dispatch int→float on operand signedness (a `BC_UITOF` / unsigned
  path), mirroring the cmp/div/shift signedness selection. Same for float→int
  and the native backends.

### Float-literal converter 1 ULP low for ~38+ sig-digit literals just above a tie (round-bit loss) — ✅ RESOLVED (binate `58570970`, `ParseFloatLitToBits` via `strconv.ParseFloat` — exact round bit)
- **Symptom**: a float64 literal with ~38+ significant digits sitting JUST
  ABOVE a binary rounding tie (e.g. `1.0000000000000001110223024625156540424`)
  converts 1 ULP LOW.  `common.ParseFloatLitToBits` holds the significand in a
  128-bit window and collapses everything below the kept 53 bits into a single
  sticky flag, losing the exact round bit.  LLVM (its own strtod) is correct;
  the VM and native backends share the converter, so they are wrong.
- **Discovery**: 2026-06-03 completeness review of the 128-bit-accumulation
  rewrite; reproduced vs strconv + a big.Float reference (~50% of constructed
  just-above-tie inputs diverge, all +1 ULP in strconv's favor).  Realistic
  literals (≤~37 sig digits) are correct — this is the table-maker's-dilemma
  tail.
- **Test**: `conformance/538_float_lit_tie_roundbit` (passes on LLVM, xfailed
  on the VM modes).
- **Proper fix**: exact rounding via `pkg/std/math/big` (mantInt*10^exp as a
  Nat, extract 53 bits + round-to-even from the exact remainder — Go's
  slow-path).  **No longer blocked**: the earlier "cmd/bnc's BUILDER tree can't
  import stdlib `big`" caveat is STALE — verified 2026-06-05 that the current
  BUILDER (`bnc-0.0.7`) compiles and runs a `pkg/std/math/big`-importing program
  correctly (`Nat.Mul` → 3000000). `math/big` is float-free integer big-num (no
  floats / generics / closures / interfaces), so it is BUILDER-compilable; only
  `strconv`-as-a-whole stays blocked (its `ftoa.bn` is float-using), and the fix
  needs `math/big` directly, not `strconv`. So the converter (in
  `pkg/binate/native/common`) can `import "pkg/std/math/big"` and do the exact
  mantInt*10^exp rounding. Remaining check before landing: confirm no tier/layer
  hygiene rule forbids the compiler tree depending on tier-1 stdlib (a layering
  question, not a BUILDER-compilability one). Interim alternative (no longer
  needed if the proper fix lands): widen the fixed window (256-bit → ~76 digits).
- **Severity**: MAJOR (silent 1-ULP-wrong float constant), narrow (38+ digits
  AND just-above-tie).

### Multi-value return assignment to `_` leaks the discarded managed component(s) — FIXED 2026-06-03 (binate; LANDED — `570_blank_discard_managed_balance` green, 0 xfails)
- **Was**: `_, n = f()` where `f` returns `(@T, int)` (or `@Iface`, `@[]T` — any managed type) never RefDec'd the `_`-discarded managed result → +1 leak per execution.  Root cause: the multi-assign loop (`genAssign`, `gen_control.bn`) ran the Axiom-3 copy-RefInc for the `_` component unconditionally, but a blank target stores nothing (`lookupVar("_") == nil`), so that RefInc had no matching RefDec.  (The single-value `_ = g()` path doesn't leak because its RefInc is *inside* the `ptr != nil` guard.)
- **Fix**: skip a blank-identifier target entirely in the multi-assign loop (`if lhs.Kind == EXPR_IDENT && isBlank(lhs.Name) { continue }`) — no copy-RefInc, no store; the call-result temp's dtor RefDec's the owned ref at end of statement.
- **Test**: `conformance/570_blank_discard_managed_balance` (loop of 100 discards; b's refcount returns to baseline 1, was 101 pre-fix).  Verified to fail on the unfixed compiler.
- **NOTE — the BOTH-bound form `a, n = f()` is NOT balanced** (the old entry wrongly claimed it was — it had only been checked for `@T` bound to a fresh-nil var).  See the two multi-assign defects in the CRITICAL section.

### Native aa64 self-host lane failed to BUILD — `duplicate symbol` (62 dups) — FIXED 2026-06-03 (binate; LANDED — the native_aa64 lane builds in CI)
- **Was**: `builder-comp_native_aa64-comp_native_aa64` failed at
  compiler-build (link) time, `ld: 62 duplicate symbols` (e.g.
  `_bn_pkg__binate__types__predeclaredNil`,
  `_bn_pkg__binate__ir__moduleGlobals`, …) — each a top-level package var
  defined in BOTH `main.o` and its owning package's `.o`.  The lane never
  reached running a test.
- **Root cause (the static-managed-sentinel hypothesis was WRONG)**:
  `ir.Global` carries `IsExtern` (an imported `.bni` extern var, defined by
  its owner's TU).  The LLVM backend honors it — emits `external global`
  (declaration only).  The NATIVE backends' `emitGlobals`
  (`pkg/binate/native/{aarch64,x64}`) did NOT check `IsExtern`: they emitted
  a strong definition for EVERY global, so every importing TU carrying an
  IsExtern entry re-defined the owner's symbol → duplicate-symbol link
  failure.  The recent cross-package extern-var feature (binate `be49c0a9`
  etc.) populated modules with IsExtern globals, tipping the latent native
  gap into a build break.
- **Fix**: native `emitGlobals` (both backends) now `continue`s on
  `g.IsExtern` (no definition — the reference resolves to the owner
  cross-object, exactly like LLVM's `external global`).  Also open the data
  section LAZILY (only once a real non-extern global is emitted): a module
  whose globals are ALL extern was otherwise leaving an empty data section
  that the Mach-O writer turned into a malformed load command (the
  `548/552/558` cross-pkg link failures).  Unit tests:
  `TestEmitGlobalsSkipsExtern` in both backends.
- **Result**: the aa64 self-host lane BUILDS and runs — `491 passed, 0
  failed` (xfails skipped).  `534` (the `@func` fix) passes on native aa64;
  `541` stays xfailed (native float gap).
- **Newly-exposed native-aa64 gaps (xfailed + tracked; NOT regressions —
  these tests never ran before the lane built)**: `550` (@func
  capture-record refcount wrong on native), `569` (float captured in a
  closure reads 0 — native float gap, 541-family), `559`/`561` (cross-package
  MANAGED extern var — already xfailed on every mode; needs the imported
  type's dtor).  `550`/`569` are the genuinely native-specific ones worth a
  follow-up.  (`551` `&G`-as-rvalue is now FIXED — see entry below.)

### `550` native @func capture-record refcount — FIXED 2026-06-04 (binate `7dab4be7`; split `879fe3a1`) — LANDED (`550_func_value_capture_released` green, 0 xfails)
- **Symptom**: a capturing `@func`'s captured managed value was not
  released when the closure died on native aa64; `conformance/550` read
  rt.Refcount 2 instead of 1.  Green on every other mode (VM via
  `0a0d00af`; LLVM via the func-value vtable dtor slot).
- **Root cause**: native `emitFuncValueVtables` always wrote the
  vtable's slot-0 (dtor) as 8 zero bytes, even for a capturing managed
  closure whose struct needs destruction.  `fv.vtable[0]` null ->
  OP_FUNC_VALUE_DTOR yields null -> rt.ZeroRefDestroy skips the dtor ->
  the captured value's ref leaks.  The OP_FUNC_VALUE_DTOR load and
  emitRefDecInline forwarding were already correct; only slot-0 wiring
  was missing.
- **Fix**: new `emitFuncValueVtableDtorSlot` (aarch64) /
  `emitFuncValueVtableDtorSlot_x64` emit slot 0 as a pointer to the
  closure-struct dtor's HANDLE (`___handle.<dtor>`) when
  `lookupClosureFuncAA64(mod, seen[i])` returns a func that is
  `IsManagedFuncValue && ClosureStruct != nil &&
  ClosureStruct.NeedsDestruction() && len(ClosureStructDtorName) > 0`;
  else 8 zero bytes (unchanged).  Mirrors `emitFuncValueVtableDtor` in
  pkg/binate/codegen.
- **Symbol-convergence note (the part the pre-fix plan got slightly
  wrong)**: `f.ClosureStructDtorName` is the UNqualified dtor name
  (`__dtor_<closure>`), NOT the dtor func's qualified `Name`
  (`<pkg>.__dtor_<closure>`).  They still resolve to ONE symbol because
  `handleSymFor` routes through `mangle.FuncName(pkgName, ...)`, which
  folds a same-package qualifier prefix and a pkgName-prefixed
  unqualified name to the identical `bn_<pkg>__<dtor>` — so slot 0
  references exactly the `___handle.<dtor>` triple that
  collectFuncValueRefs' IsLinkOnce pre-pass already emits.  No new
  global, no dangling reference.  (Used the EXISTING `lookupClosureFuncAA64`,
  which returns the closure func directly — the planned
  `lookupModuleFuncAA64` was unnecessary.)
- **x64 parity**: same fix in `pkg/binate/native/x64/x64_funcvalue.bn`
  (no CI lane, but had the identical latent capture-leak).
- **Hygiene**: the +45-line fix pushed `aarch64.bn` over the 500-line
  cap, so the func-value emission was first extracted to
  `aarch64_funcvalue.bn` (mirrors `x64_funcvalue.bn`) in `879fe3a1`.
- **Tests**: 550 un-xfailed on native aa64 (verified fail pre-fix /
  pass post-fix); `aarch64_funcvalue_test.bn` pins slot-0 shape (dtor
  handle for a capturing managed closure, null otherwise, null for the
  *func and no-managed-capture forms).

### ~~Comparability "deferred to instantiation" comments were false; `eq[@[]int]` emits invalid icmp (CR-2 N3) — 2026-06-08~~ — ✅ LANDED on main (binate `15946a55`, 2026-06-14)

`checkEqOperands` (`checker_errors.bn`) and `relationalOperandOK`
(`types_query.bn`) claimed a non-comparable type argument is "deferred
to the concrete instantiation" — implying the instantiation re-checks
it. It doesn't: operand comparability is checked only on the generic
DEFINITION (T opaque → skipped), so `eq[@[]int]` reaches codegen and
emits an invalid `icmp` on a `%BnManagedSlice` (a clang failure), where
the non-generic path cleanly rejects ("slices cannot be compared").
Reworded both comments to describe the actual gap + point at the todo,
and added `conformance/772_generic_eq_managed_slice` (xfail all default
modes): a `.error` cell expecting the desired clean rejection, so when
generic instantiation re-runs operand checks it XPASSes (fix-detector).
The same-entry siblings N2 (dead `peelTransparent` comment in
`gen_iface.bn`) and N10/N11 (stale iface/funcval-multi-return xfail
markers) were already resolved in-tree by later work (verified absent).

### ~~`@[N]T` parser leniency: bare `@[N]T` silently accepted (`*[N]T` rejected) — spec Ch.7 (2026-06-12)~~ — ✅ LANDED on main (binate `7ccd13e1`, 2026-06-14)

`@[` is managed-slice sugar (`@[]T`) and `*[` is raw-slice sugar
(`*[]T`); a (managed-)pointer to an array needs parens (`@([N]T)` /
`*([N]T)`). The parser rejected bare `*[N]T` but silently accepted
`@[N]T` as `@([N]T)` — asymmetric. Now mirrors the `*[` rejection in the
`@[` branch (error + recover as managed-pointer-to-array). Tests:
`TestParseBareManagedArrayRejected` + `TestParseParenManagedArrayAccepted`.
(The other Ch.7-types sub-item — named func-value LITERAL construction —
stays open in claude-todo.md; opaque make/sizeof/alignof gating landed
`fe9e131e`, though it covers only the direct/bare case — named-distinct and
value-embedding gaps are tracked as a new MAJOR in claude-todo.md.)

### ~~Generic methods accepted at declaration (`func (b Box) Get[T any](…)`) — spec Ch.12 (2026-06-12)~~ — ✅ LANDED on main (binate `a7e0beb2`, 2026-06-14)

Generics v1 has no generic methods (only generic free functions and
generic types), but the parser reads a `[T any]` list after a receiver,
so a generic method type-checked clean and failed only at a call site
with a confusing "cannot index this type" (`b.Get[int](…)` parses
`[int]` as indexing on the `b.Get` selector). Now rejected at collection
time (`collectMethodDecl`, `len(d.TypeParams) > 0`) with "methods cannot
have type parameters", for every receiver kind. Tests:
`TestCheckGenericMethodRejected` + `TestCheckNonGenericMethodAccepted`.
(Sibling Ch.12 defect — constraint satisfaction unchecked for generic
struct/interface instantiation — stays open in claude-todo.md.)

### ~~CRITICAL: managed-slice composite literals with INLINE struct-literal elements miscompile (`@[]T{ T{...} }`)~~ — ✅ LANDED on main (binate `326b3a60` fix + `b5baf317` coverage, 2026-06-14)

ONE IR-gen defect: a managed-slice composite literal whose elements are **inline
struct literals**, e.g. `@[]Pt{ Pt{x:1,y:2}, Pt{x:3,y:4} }`, miscompiled. No
conformance test or codebase site exercised it at runtime (the IR-gen test
`TestManagedSliceLitAggregateAcquires` only asserts `__copy_` is *emitted*, never
runs the program), so it was silently broken.

**Discovery:** while factoring cmd/bni's native-only inject list — `nativeOnlyStdPkgs()`
used inline struct-literal elements (each with a `*func` field), and cmd/bni produced
**empty output for every program** because its startup table read back garbage.

**Root cause:** `genManagedSliceLit` (`pkg/binate/ir/gen_composite.bn`) was missing
the `isAggregateAllocToLoad` load guard that `genArrayLit` and the struct-field path
already have. For an inline struct literal, `genExpr` returns the temp's alloca
POINTER (OP_ALLOC); without the guard, the per-element struct-copy slot and
`EmitSliceSet` stored that 8-byte pointer into the struct-sized slot instead of the
aggregate value. Symptom split by backend (same defect): compiled → garbage data
(the stored values were the temps' stack addresses, 16 apart = `sizeof(Pt)`);
bytecode VM → SIGSEGV (dereferences the bad pointer). **Variable elements were always
fine** — `genExpr` returns an OP_LOAD value, so the guard is a no-op for them.

**Fix:** `genManagedSliceLit` now loads the aggregate value before the struct-copy /
`EmitSliceSet`, mirroring `genArrayLit`. The guard fires only for OP_ALLOC
struct/array element values; OP_LOAD (variable) and managed-slice/scalar elements are
untouched. Tests: conformance 773 (value struct, inline + variable), 774 (struct with
a managed `@[]char` field — exercises load + `emitStructCopy` RefInc / refcount), 775
(array-element branch `@[][N]int{...}`). All pass across the six default modes; full
`builder-comp` sweep green (1439/0). The cmd/bni inject-list factoring that surfaced
it landed as `8e45cc7e`.

### ~~Comparison chaining wrongly accepted on the `for`-clause path (`for a < b < c {}`) — spec §13.6 (2026-06-13)~~ — ✅ LANDED on main (binate `55360652`, 2026-06-14)

The main `parseCompareExpr` path takes at most one comparison operator,
but the for-clause's identifier-leading expression runs a separate Pratt
engine (`continueBinaryExpr`) whose left-associative loop parsed
`for a < b < c {}` as `(a < b) < c` instead of erroring (and
`for (a) < b < c {}` was already rejected — internally inconsistent).
Fix adds an `isCompareOp` non-chaining check: report "comparison
operators do not chain" when a comparison op would apply to a left
operand that is itself a comparison, then keep parsing so the clause
resyncs (one clean error, no cascade). Tests:
`TestParseForCompareChainingRejected` + `TestParseForSingleCompareAccepted`.

### ~~`const X T` (typed, no initializer) wrongly accepted by the parser — spec §9 (2026-06-13)~~ — ✅ LANDED on main (binate `29ae5bc6`, 2026-06-14)

`parseConstSpec` made the `=` optional once a type was present, so
`const Foo int` parsed into a value-less `DECL_CONST` that then failed
downstream with a confusing `undefined: Foo`. §9 makes the `=`
mandatory when a `Type` is present (the only value-less form is a bare
identifier — no type — repeating the previous value inside a grouped
`const ( … )`). Fix tightens `parseConstSpec` to require the `=` and
emit a clear parse error ("const declaration with a type requires a
value"); the bare-repeat exception is preserved (its current token is
`;`/`)`, not a type). Tests: `TestParseConstTypedNoValueRejected`
(typed-no-value, single + grouped) + `TestParseGroupedConstBareRepeat`
(the value-less bare repeat still parses, no type / no value).

### DONE (2026-06-13..14) — `buf.CharBuf` → `buf.Builder` → stdlib `strings.Builder` (swap landed `9e69617f`, BUILDER bnc-0.0.9)

The bespoke value-typed `buf.CharBuf` is fully retired: every caller (out-of-cone
and in-cone) migrated to the managed-reference `buf.Builder`, and **`CharBuf` itself
deleted (`d92726b7`)**.  The post-release `buf.Builder` → `strings.Builder` swap is now DONE — landed
`9e69617f` once BUILDER bnc-0.0.9 shipped the stdlib Builder (mechanical
import-and-rename: `buf.Builder`→`strings.Builder`, `buf.WriteInt`→`stringutils.WriteInt`;
the codegen `strings` StringConst identifier renamed `strs` to avoid the import
clash; `buf` keeps only CopyStr/Concat).  Detail retained as a UAF-gotcha reference.

Full plan: [plan-buf-deprecation.md](plan-buf-deprecation.md).

Retire the bespoke `buf.CharBuf` byte-buffer for the stdlib `strings.Builder`.
- **Landed:** `strings.Builder`; `pkg/binate/stringutils` (binate `04c67dd3`) supplying the Builder-method gap as free functions over `*strings.Builder` (`WriteInt`/`WriteInt64`/`WriteHexByte`); the `.bni`-impl-registration fix (`3d147369`) that unblocks Builder-through-`io.Writer`. `stringutils.Freeze` was dropped (`7350bdd1`) — it equals `buf.CopyStr(b.String())`. **ALL out-of-cone callers migrated 2026-06-12** (the complete out-of-cone `CharBuf` set, verified `CharBuf`-free after): `cmd/bnlint` `ab076c5d`, `cmd/bnas` `e10472da`, `cmd/bni` `fd8ccefc`, `pkg/binate/repl` `dc50ef48`, `pkg/binate/vm` `27efd23a`, `pkg/binate/lint` `f7ce3d89`.
- **Main cost:** buf is a VALUE type (`b = b.WriteStr(..)`, chainable); Builder is a managed REFERENCE mutated in place (`b.Write(..)`, void). Migration is a value→reference rewrite, not a rename — it touches the high-count sites (WriteStr 2956, Bytes 718, New 700, WriteInt 423).
- **In-cone strategy — `buf.Builder` (lets in-cone migrate NOW, no release needed):** the cone can't import `pkg/std/strings` until the next BUILDER release (interface machinery the current BUILDER can't carry), so `pkg/binate/buf` now has **`buf.Builder`** (binate `26c224c0`) — a BUILDER-compilable copy of `strings.Builder` (same struct/methods; `Write`/`WriteByte` return void, no `io.Writer` impl) + free fns `buf.WriteInt`/`WriteInt64`/`WriteHexByte` mirroring `stringutils`. In-cone callers migrate `CharBuf`→`buf.Builder` now; **post-release the swap to `strings.Builder` (`buf.Builder`→`strings.Builder`, `buf.WriteInt`→`stringutils.WriteInt`) is a mechanical import-and-rename** (call sites discard returns, so the void/error mismatch is invisible).
- **In-cone migration progress:** DONE — `lexer` `97b3af36`, `parser` `80bf8687`, `loader` `1bc59c7b`, `buildcfg`+`asm/parse` (`9e5a6a3d`), `asm/macho`+`asm/elf` (`9d500c1d`), `cmd/bnc` `69679440`, **`mangle` `880efb22`** (dependency root — see below), **`native/{x64,aarch64}` `69fcbca0`**, **`types` `667317aa`**, **`ir` `41e3f949`**, **`codegen`** (prep leaf builders `672011d4` + atomic threaded core `0d5a7954`) (all 2026-06-12). (`native/common`, `debug` already CharBuf-free.) **`token` `c23415d8`** (the last test-file builders). **The in-cone `CharBuf`→`buf.Builder` migration is COMPLETE; `CharBuf` deleted `d92726b7`.**
  - **codegen done — how:** the ~120 `out`-threading emit fns converted atomically (`func f(out buf.CharBuf,…) buf.CharBuf` → `func f(out *buf.Builder,…)` void; 9 multi-return kept their trailing `int`/`(int,@[]char)`); `writeBuf` eliminated; `EmitModule` → `buf.CopyStr(out.String())`; `normalizeFloatLiteral` value-returns `@[]char`. `emit_debug.bn`'s `!dbg` splicing was refactored from CharBuf `out.Len=`/`out.Data[]` **truncation** to **append-only** (emit each instr into a temp Builder, annotate the snapshot via `addDbgAnnotations`) — keeps Builder == strings.Builder. The blanket `.Bytes()`→`buf.CopyStr(x.String())` produced 52 raw-borrow UAFs (`var src *[]readonly char = buf.CopyStr(cb.String())`) → `cb.String()` (zero-copy borrow); ALWAYS grep for that shape after a blanket sweep. Verified gen2 self-compile + full builder-comp conformance (1419/0).
  - **Post-release swap (eventual):** `buf.Builder`→`strings.Builder`, `buf.WriteInt`/`WriteInt64`/`WriteHexByte`→`stringutils.*` — mechanical import-and-rename once a BUILDER release ships the stdlib Builder. (`emit_debug.bn` is now append-only so the swap stays clean — no Truncate needed.)
  - **⚠️ `.Bytes()` sink is LIFETIME-SENSITIVE (this bit types — a silent UAF):** `Builder.String()` returns a *borrow* of the live builder's backing; the old `CharBuf.Bytes()` returned a *copy*. So `x.Bytes()` does NOT map uniformly to `buf.CopyStr(x.String())`. Two cases: (a) escaping / owned sinks (`return x.Bytes()`, a func arg the callee stores as `@[]char`, an intermediate `f(x.Bytes())` consumed in-statement) → `buf.CopyStr(x.String())` (fresh owned copy). (b) a **raw**-slice local that borrows and is read on a LATER line (`var s *[]readonly char = x.Bytes()` … later `use(s)`) → `x.String()` directly (zero-copy borrow of the still-alive builder). Wrapping case (b) in `buf.CopyStr` binds the raw local to a temporary `@[]char` that is RefDec'd at end of statement → dangling raw slice → use-after-free (in types it silently fed garbage source to the checker; manifested as "expected errors but got none"). When sweeping ir/codegen, after the blanket transform grep for `var \w+ \*\[\](readonly )?char = buf\.CopyStr\(` (and raw struct-field / raw-return forms) and convert those back to `.String()`.
  - **`mangle` — DONE `880efb22`** (the dependency ROOT): `mangle.{FuncName,GlobalName,StructName}` now RETURN `@[]char` (built internally with `buf.Builder` → `buf.CopyStr(b.String())`; `writePathIdent` takes `*buf.Builder`). All ~30 caller sites in `native/x64`, `native/aarch64`, `codegen` dropped `.Bytes()`, retyped held `var x buf.CharBuf = mangle.X(...)` locals to `@[]char`, and switched `writeBuf(out, mangle.X(...))` → `out.WriteStr(...)` (those packages still build with `buf.CharBuf` internally — only the mangle call sites adapted; `@[]char` coerces to `*[]readonly char`, bytes unchanged). `ir`'s `mangle.{F,G,S}` references were comments only. Mapped with a 6-agent analysis-fan-out before editing. Verified host + gen2 self-compile (symbols byte-identical) + VM. **This unblocked the internal CharBuf→buf.Builder migration of `native/x64`, `native/aarch64`, `codegen`.**
  - **`codegen` is heaviest** (~400 `WriteInt` → `buf.WriteInt`); `types` is large but mangle-independent (error/type-name builders).
  - **Patterns** (validated through gen2 self-compile each package): `buf.New()`→`buf.NewBuilder()`; `cb = cb.WriteStr(x)`→`cb.Write(x)`; `cb = cb.WriteInt(n)`→`buf.WriteInt(cb, n)`; `cb.Bytes()`/`cb.Freeze()`→`buf.CopyStr(cb.String())` for a foreign-mutable `@[]char` sink, or retype the sink to `@[]readonly char`+`cb.String()` when it's a local read-only consumer; a single `WriteStr(slice)`+`Bytes` collapses to `buf.CopyStr(slice)`; consecutive `WriteStr` of string literals collapse to one adjacent-literal arg; a `CharBuf` struct field → `@buf.Builder` (drop value-threading reassignments). No import change (packages already import `buf`). `BinBuf` (asm) is a DIFFERENT type — leave it.
- **Decisions:** (1) `buf.CopyStr`/`Concat` (pure slice utils, 168+103 sites, used in-cone) have NO home — DEFERRED 2026-06-12 (leave in `buf` for now; revisit later). (2) `Bytes()` (mutable `@[]char`) vs `String()` (readonly) — ✅ RESOLVED 2026-06-12: audited all 734 sites, ZERO mutate the result; `String()`+`buf.CopyStr(b.String())` suffice, NO mutable accessor; ~30 mechanical retype-to-`readonly char` follow-ups (params/fields that only read). See the plan.
- **Convention** (per bnlint): readonly-correct by default (retype local sinks, consume `String()` zero-copy); `buf.CopyStr(builder.String())` only where a sink stays a foreign mutable `@[]char`; no `Freeze`.
- **Readonly-correctness follow-up:** `ast.ImportSpec.Path @[]char` → `@[]readonly char` would make `quotePath` (bnlint/bni/bnc) zero-copy; in-cone (cascades through `loader.unquote` + callers), not release-gated. See the plan.

### ~~`.bni` const initializer's unqualified sibling-ident ref silently resolved to 0~~ — ✅ FIXED (binate `8fd4f378`; conformance 504/502) [entry had a stray duplicate header]
- **Final diagnosis**: an unqualified EXPR_IDENT inside a
  `.bni`-declared const initializer (e.g. `WORDS` in
  `const SIZE int = WORDS * cast(int, sizeof(int))`) wasn't
  resolving during import processing — pkg/ir's evalConstExpr
  looked the name up only in unqualified form, but the sibling
  const had been registered under the import-qualified name
  (`pkg/x.WORDS`).  The EXPR_IDENT arm returned (0, false), the
  binary expression silently became 0, and the resulting const
  was registered with value 0.
- **Fix (binate `8fd4f378`)**: retry the lookup with
  `buildQualName(currentImportAlias, e.Name)` when the
  unqualified one misses.  Pinned by conformance
  `504_bni_const_sibling_ref`.
- **Boundary-enforcement aside**: my first writeup of this also
  speculated that bnc was accepting unexported cross-package
  references.  Re-tested with a focused repro: bnc DOES correctly
  reject `pkg.NAME` references when NAME isn't in the package's
  `.bni`.  Pinned positively by conformance
  `502_err_unexported_const_rejected`.  That part was always fine
  — the only bug was the sibling-ident lookup above.
- **Discovery**: managed-allocation-header refactor (binate
  `c7323fb2`).  Replacing pkg/vm's hardcoded `-16` managed-header
  offset with `ptr - rt.HEADER_SIZE` (declared as
  `HEADER_WORDS * cast(int, sizeof(int))`) built cleanly but
  produced `ptr - 0`, silently corrupting the payload's first
  word.  TestExecRefIncRefDecInline (pkg/vm) caught it on amd64.

### ~~Mirror `return f(...)` acceptance in the Go bootstrap~~ — LOW PRIORITY
- Self-hosted accepts the shape (commits `b88918e` /
  `d11e4f2` / `d3fc0db` / `96572fb` on main; conformance
  `347_return_multi_call`). Bootstrap still rejects it.
- **What's needed**:
  1. **Type-checker** (`bootstrap/types/checker.go:checkReturnStmt`,
     ~lines 963-978): when `len(s.Results) == 1` and
     `len(c.funcRet) > 1`, allow it iff the single expression is
     a `CallExpr` whose function type returns a matching tuple
     and each per-result type is `AssignableTo` the
     corresponding `c.funcRet[i]`. Mirrors the existing
     multi-return shape in `checkShortVarDecl` (~lines
     937-955) — same `(len(s.RHS) == 1 && rhsType is FuncType
     with matching Results)` predicate.
  2. **Bootstrap interpreter STMT_RETURN execution path**:
     extend it to handle the single-expression-multi-return
     shape, mirroring how `q, r := f()` is already executed
     (single call eval + per-result destructure).
  3. **Conformance**: drop `347_return_multi_call.xfail.boot`
     once both impls handle it. Drop the bootstrap-only
     rejection note from `bootstrap-subset.md`.
- **Why low priority**: the bootstrap subset is intentionally
  restrictive; the self-hosted toolchain doesn't need this to
  compile, and no in-flight work depends on it. Pick up when
  there's a concrete user (e.g., a self-hosted source file that
  wants the form, or a broader bootstrap-subset widening pass).

### ~~[CR-2 Plan-1 review] Memory-safety / refcount audit~~ — CLEAN on the probed Plan-1 paths (2026-06-08)
- The dedicated memory-safety finder failed to emit output twice; I audited the highest-risk Plan-1 refcount changes by hand with `rt.Refcount`-balance probes. **All balanced — no leak / UAF / double-free found:** (a) Defect-1 `peelTransparent` on `readonly @Box` (the managed/raw-classification flip risk): `var p readonly @Box = b` RefIncs 1→2, stays 2 while alive, scope-end RefDec balances — the peel makes the readonly-wrapped managed ptr counted (correct). (b) Defect-6 managed array element (`a[0] = b` on `[1]@Box`): 1→2→2 balanced. (c) Defect-3/4 multi-return managed field (`x, n := wrap(b)` returning `@Box`): 1→2→2 balanced. NOT exhaustive (the `=`-destructure path M1 fails to compile, and the un-migrated store arms M2 can't be reached with managed inner without crashing), but the directly-attributable Plan-1 refcount changes are sound.

### ~~Unit tests RED on compiled-native modes — `pkg/binate/vm` / `repl` / `cmd/bni` test binaries don't link the std `_Package` objects — ✅ FIXED binate `f7d116f3` 2026-06-10~~ — was MAJOR
- **FIX**: the native backend now emits the per-package `_Package` descriptor + accessor (binate `f7d116f3`, `native: emit the per-package _Package descriptor`), so the `_bn_pkg__*___Package` symbols resolve. After it: `pkg/binate/repl` + `cmd/bni` are GREEN on native_aa64; `pkg/binate/vm` LINKS and runs (168 pass). conformance/532 now green on native aarch64 + x64-darwin (was LLVM/VM only). **Remaining native_aa64 `pkg/binate/vm` red is NOT this bug**: the 2 surviving failures are `TestExternFloat{,32}ArgViaRegistry` — the separately-tracked float-arg-shim bug (see below; it was MASKED by this link failure, now visible). Root-cause direction below was correct (native never emitted `_Package`); the original "make the link include the objects / xfail" framing is superseded by the real fix.
- **Symptom**: in `builder-comp_native_aa64-comp_native_aa64` unit mode, `pkg/binate/vm`, `pkg/binate/repl`, and `cmd/bni` FAIL to link: `Undefined symbols: _bn_pkg__bootstrap___Package, _bn_pkg__builtins__reflect___Package, _bn_pkg__builtins__rt___Package, referenced from _bn_pkg__binate__vm__RegisterStandardExterns`. **Reproduces LOCALLY** (`scripts/unittest/run.sh builder-comp_native_aa64-comp_native_aa64 pkg/binate/vm` → 0 passed, 1 failed). Pre-existing across many commits (Plan-C, getSelectorType, lang-bool, #113, …). NOT introduced by any bnc-0.0.8 release-prep lane; NOT xfailed (so it reads as a hard CI fail, not a tracked xfail).
- **Root cause (direction)**: `RegisterStandardExterns` (`pkg/binate/vm/extern_register_std.bn`, the Phase-B `_Package()` VM-extern feature, binate `feadde2c`) takes `*func() @reflect.Package` handles to `rt._Package` / `bootstrap._Package` / `reflect._Package`. Those `_Package` accessors are codegen-only (no `.bn` body — bnc synthesizes the def per *module*). The native unit-test link of the vm/repl/bni binaries references them but doesn't link any object that DEFINES them (no module in the test link emits the builtin packages' `_Package`), so they're undefined. The `builder-comp` (LLVM) unit mode passes — so this is specific to the native-backend unit-test link step.
- **Distinct from**: (a) Lane A's conformance `-comp*` `bn_pkg__bootstrap__Write` break (CI-only, doesn't reproduce locally; this one DOES reproduce locally and is the `_Package` symbol class); (b) the `loader` struct-mangler collision (different symptom). 
- **Also red (separate)**: `builder-comp-int-int` unit shards time out (~30m) in CI; `builder-comp_native_x64` (ELF) Perf fails (Lane A compiled-link family — native_aa64 Perf passes). 
- **Fix direction (owner's call)**: either make the native unit-test link include the builtin packages' `_Package`-defining objects (the harness/link step), or xfail `pkg/binate/{vm,repl}` + `cmd/bni` on `builder-comp_native_aa64`/`_x64` unit modes with a tracked reason until the `_Package` extern registration is link-complete for test binaries. **Not a shipped-artifact blocker** (the four release binaries build fine — `make-bundle.sh` green), but a red CI category to resolve or consciously accept before the bnc-0.0.8 tag.

### ~~e2e/repl.sh + print-args.sh build broken — gen1 build resolved stdlib current-first, so the BUILDER hit current `std/errors` (`same`)~~ — ✅ FIXED binate `c44ab9b7` 2026-06-10 (Lane C)
- **Symptom**: `e2e/repl.sh` (and `e2e/print-args.sh`) fail at their BUILDER-stage gen1 build: `impls/stdlib/common/pkg/std/errors/errors.bn:104:6: undefined: same` (+ `cannot call non-function` / `non-bool condition`). The e2e never runs because the toolchain build aborts.
- **Root cause (CORRECTED)**: the e2e scripts' two-stage gen1 build listed the checkout's stdlib roots AHEAD of the `$BUILDER_LIB` bundle (current-first), unlike the canonical `scripts/lib/build-compilers.sh` `build_gen1`, which lists the BUILDER's stdlib first. `cmd/bnc`'s import closure reaches `pkg/std/errors` (bnc → `native/common` → `std/strconv` → `std/errors`; `std/strconv` → `std/errors` also gives bni/bnlint the same closure), and `std/errors` uses the `same` builtin (binate `1f87b905`), which BUILDER `bnc-0.0.7` predates — so the BUILDER compiled CURRENT `std/errors` and choked on `same`. NOT a current-bnc bug (`same` works in gen1; conformance `661_same_ref` is green). The conformance/unit runners were unaffected precisely because `build_gen1` resolves stdlib BUILDER-first; the e2e scripts had drifted from that ordering.
- **Fix**: reorder the e2e scripts' gen1-stage `-I`/`-L` to put the `$BUILDER_LIB` stdlib roots ahead of the checkout's (core stays current-first), matching `build_gen1` (binate `c44ab9b7`). e2e/repl.sh now builds + passes 54/54 and print-args.sh 2/2 on the **pre-bump** tree. (The `same` skew also self-heals at the Convergence BUILDER bump, but the script fix makes e2e green without waiting for it.)
- **Correction to the prior note**: the earlier claim that "the four binaries don't import `std/errors`" is WRONG — bnc (via `native/common` → `std/strconv`), bni, and bnlint all transitively import it. The release **bundle** build (`make-bundle.sh`) was never blocked because its build scripts already resolve stdlib BUILDER-first, NOT because the binaries avoid the import.
- **Discovery**: 2026-06-09, building the CR-2 Plan-B B3 e2e value test (REPL parked-member iota-repeat). B3's e2e case (`tier3-pending-const-group-bare-iota-repeat`) now runs and passes.

### ~~Generics in cmd/bnc's tree~~ — UNBLOCKED 2026-05-26 (BUILDER → bnc-0.0.2)
- **Status**: BUILDER is now bnc-0.0.2 (binate `5414bab`), which
  was cut from a tree that has generics (slices 4–7).  Verified the
  builder compiles generic decls + explicit instantiation
  `f[T](...)`; cross-package monomorphization works too.  So
  cmd/bnc-tree code may now use generics.
- **No type inference** (claude-notes.md:537, 1000): always spell
  the type arg, e.g. `slices.Append[@ast.Decl](xs, d)`.  The
  builder's "generic function requires type arguments" diagnostic
  on a bare `f(...)` call is intended behavior, not a gap.
- **First consumer — `pkg/slices`** (IN PROGRESS): `Append[T]`
  collapses the dozens of per-type `appendXxx` / `appendXxxPtr`
  helpers scattered across cmd/bnc + pkg/*.  Migration is staged
  one package at a time (see below).
  - **Generic packaging pattern**: a generic's body must live in
    the `.bni` (body-included) so cross-package consumers can
    monomorphize at the call site.  For an all-generic package the
    `.bn` needs **no** copy of the body — just the `package` decl
    (the package's own compile + tests resolve the generic from the
    merged `.bni`).  Keeping a second body in the `.bn` is a
    needless sync hazard; don't.
- **Mechanical migration DONE 2026-05-28**: ~62 per-type append
  helpers across pkg/{ast,types,ir,parser,loader,codegen,vm,
  native/aarch64} + cmd/bnc collapsed into ~378 call sites of
  `slices.Append[T]`, one commit per package boundary
  (binate `2714e67` loader → `ed727f8` parser → `bbb7fab5` ir →
  `60f385ff` cmd/bnc → `12f20a06` types → `79c11465` ir literals →
  `efbac9db` codegen → `d43185bb` vm → `1a45bb9b` aarch64 →
  `d226b237` ir scattered → `13477619` types capture → `a66b287c`
  cmd/bnc test).  Four `pkg/{loader,parser,ir,cmd-bnc}/slices.bn`
  files deleted.  Net ~-750 lines.

### ~~Review remaining non-standard `appendXxx` helpers~~ — opportunistic
- 13 helpers were kept past the `slices.Append[T]` migration because
  their bodies aren't a pure slice-of-T append (per the commit
  messages around 2026-05-28).  Worth reviewing whether any could be
  refactored to use `slices.Append` plus a small adapter:
  - ~~**Char-concat into a `@[]char` buffer** (not slice-of-T):
    `pkg/native/x64/x64_iface.bn`'s `appendPkgIdent_x64`,
    `appendStrIface`; `pkg/native/aarch64/aarch64_iface.bn`'s
    `appendPkgIdentNative`, `appendStrLocal`.  These four could
    probably share a single `buf.WriteStr`-style helper.~~ — DONE
    2026-05-28 (binate `fd1e931c` + `1b762f16`): pulled the two
    distinct shapes into `pkg/native/common.AppendStr` /
    `AppendPkgIdent`, x64/aarch64 callers rewritten, 4 duplicate
    helpers deleted, direct unit coverage in common_test.bn.
  - **Dedup / diagnostic-emitting**:
    `pkg/types/check_iface_extends.bn`'s
    `appendIfaceMethodWithConflictCheck` (emits a `CheckError` on
    signature mismatch) and `appendUniqueMethods` (dedup by method
    name).  These stay non-standard.
  - **Parallel two-slice append**:
    `pkg/ir/gen_iface_extends.bn`'s `appendAncestors(pkgs, names,
    pkg, name)` — could split into two `slices.Append` calls but
    the paired-update pattern is the helper's value; debatable.
  - **Conditional multi-arg append**: `cmd/bnc/target.bn`'s
    `appendTargetFlags`, `appendTargetRuntime` — fine as-is.
  - **Loader-level Imports**: `cmd/bnc/compile_imports.bn`'s
    `appendRtImport`, `appendLibcImport`, `appendBootstrapImport` —
    not slice append; fine as-is.
  - **Raw-slice wrap-and-append**: `cmd/bnc/util.bn`'s
    `appendRawCharSlice(s, *[]const char) → @[]@[]char` (CopyStr +
    append).  Could inline the 47 call sites as
    `slices.Append[@[]char](s, buf.CopyStr(v))` but the named
    helper documents the wrap-and-append idiom; debatable.

### ~~Slice ownership model~~ — design notes
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`*[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC. Caller manages lifetime (like C).
- Cannot be compared to `nil` — check `len(s) == 0` for empty.

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `*[]T`. Refcounted via backing_refptr.
- backing_len stores total element count for destructor cleanup.
- `make_slice(T, n)` returns `@[]T`. `@[]T → *[]T` conversion: extractvalue fields 0,1.

### ~~Adversarial audit of `7f53b9ce` (2026-06-09, find→verify workflow)~~ — sibling findings
The audit re-verified its own findings at runtime; **note a concurrent worker clobbered shared `/tmp` test files mid-run**, producing one false positive (below) — all findings here were re-reproduced by hand with unique paths.

- **MAJOR — `readonly` aggregate multi-return component → LLVM SIGBUS — ✅ RESOLVED 2026-06-10 (binate `c63a7e3f`)**. Root was the FE check gap (the "Also suspicious" note below), NOT primarily the zero-init: the multi-return destructure path skipped the const-location check the simple-assign path applies, so `x, n = makeSlice()` to a `readonly @T` (a const LOCATION — distinct from `@(readonly T)` whose pointer is rebindable; confirmed with the user) compiled an illegal assignment → the multi-assign RefDec'd the uninitialized slot → SIGBUS. FIX: extracted `checkAssignTargetConst` (const-symbol + qualified-const + readonly-location) and applied it per target in the destructure loop (`pkg/binate/types/check_stmt.bn`); `695_err_const_loc_multi_return` pins the compile error; 185 const/assign/multi-return conformance + `pkg/binate/types` unit green; mode-agnostic (rejected at type-check, before codegen). The zero-init-skip below is the crash MECHANISM but now UNREACHABLE (the illegal program no longer compiles); the readonly-not-peeled zero-init classifier (VM `isMultiWordField`/`isVMAddressAggregate`, `resolveToStruct`) stays a separate latent-consistency follow-up (#113 family). ORIGINAL ANALYSIS (root framing was off; kept for the mechanism): `func makeSlice() (readonly @[]int, int) {...}; func main() { var x readonly @[]int; var n int; x, n = makeSlice(); println(len(x)) }` compiled cleanly but CRASHED on LLVM (exit 138 / SIGBUS); native-aa64, native-x64-darwin, VM all correct. Non-readonly control (`@[]int` component) is green everywhere — the `readonly` wrapper is the precise trigger. **Root (LLVM, separate path from the `7f53b9ce` fixes)**: the multi-assign `x, n = makeSlice()` RefDec's `x`'s *old* value (loaded from its slot) before storing the new one, but `var x readonly @[]int` (no initializer) does not get its slot zero-initialized — a readonly-not-peeled classification skips the nil-init — so the RefDec reads a garbage refptr and `(garbage-16)` as a refcount header → fault. Sibling un-peeled classifiers in the same family: VM `isMultiWordField` / `isVMAddressAggregate` (`vm/lower_instr_helpers.bn:90,116`) and `resolveToStruct` peel alias+named but NOT readonly (latent; not the LLVM crash root but should also peel for consistency). **Also suspicious**: a multi-assign into a `readonly` local compiled at all (a direct `x = ...` to a readonly location is FE-rejected) — possible separate readonly-assign-check gap in the multi-assign path. Repro: the makeSlice program above.
- **DECIDED 2026-06-10 (accept the consistency fix) — front-end over-rejection: `@[]T → readonly *[]T` / `@T → readonly *T` rejected**: `var x readonly *[]readonly char = someManagedSlice` is rejected on all four backends ("cannot assign @[]uint8 to readonly *[]readonly uint8"), but the non-readonly `@[]T → *[]T` managed→raw decay IS accepted. Root: `types/types_assignable.bn` `AssignableTo` — the `@[]T→*[]T` and `@T→*T` decay arms test the UNPEELED `dst.Kind` (so `readonly *[]T` whose Kind is the wrapper falls through to `return false`), whereas the interface-value arms just below peel via `resolveAliasAndConst` first. **The user ratified the fix** (the asymmetry is a gap; readonly is strictly more restrictive on capability, so it should decay the same): peel readonly/alias off `dst` before the `TYP_SLICE` / `TYP_POINTER` kind tests (reuse the resolved `d` the iface arms compute), plus `pkg/binate/types` checker unit tests asserting `@[]T` / `@T` assign to `readonly *[]T` / `readonly *T`. **✅ RESOLVED 2026-06-12 (binate `81e2a3d3`)**: hoisted the `resolveAliasAndConst(dst)` peel above the two managed→raw decay arms (the iface arms now reuse the same hoisted `d`); the element-level const check (`dropsConst`) is unchanged, so dropping const BEHIND the reference is still rejected. Coverage: checker unit tests (the two ratified forms) + conformance `734_managed_to_readonly_raw_decay` (end-to-end borrow + read, green on native / VM / gen2 / gen2-VM / double-VM); full unit 45/0 + full conformance 1397/0 on builder-comp. Makes `coerceArg`'s managed→raw readonly-peel (`487fb95c`) reachable from source (the "Note" above is now stale — the assignment is no longer FE-rejected). The `@func → *func` decay arm also tested the unpeeled `dst.Kind` — **consistency follow-up ✅ LANDED 2026-06-12 (binate `d86757ff`)**: peeled it too. That follow-up surfaced a **latent IR-gen miscompile**: `genCall` classified a call through the callee by `Kind == TYP_FUNC_VALUE` WITHOUT peeling readonly, so a call through a `readonly *func` local/field lowered to a DIRECT call on an undefined symbol named after the variable (compile error; a silent miscompile had it resolved). Fixed by `peelReadonly` at both func-value-call classification sites (Ident local + selector/index) in `gen_call.bn`; checker+codegen land together (the checker peel alone is a miscompile). 734 extended with a `readonly *func` call-through (all 5 modes). Alias-wrapped decay targets (`type RP = *int` / `= *[]int` / `= *func`) were verified to work end to end too (aliases are resolved before IR-gen, so only the `readonly` wrapper needed an explicit peel).
- **FALSE POSITIVE (recorded so it isn't re-chased)** — "readonly float binop emits integer add": reported wrong by an audit agent (LLVM 2.0625 / native 0.0) but NOT reproducible — `readonly MyFloat` arithmetic gives correct `4.0` on all four backends in clean re-runs (local-var and param variants). The agent's run was contaminated by the concurrent `/tmp` clobbering noted above. `codegen` `unwrapNamed` not peeling readonly is real in the source, but the binop's IR operand/result types arrive already peeled, so it does not manifest.
- **RE-CONFIRMATION (already tracked)** — VM `int → float32` cast yields 0 (compiler backends correct). This is the existing `vm-int-to-float32` bug, already xfailed via `conformance/matrix/scalar` int-to-float cells; the audit independently re-confirmed it via a minimal reproducer. No new action.

### ~~MAJOR — cross-package generic functions over struct types: `.bni` signature can't resolve a same-`.bni` generic struct (`undefined: Box` / `@void`), AND consumer-side monomorphization silently passes struct args as zero (2026-06-14)~~ — ✅ LANDED on main (binate `02fa92d7` types, `1c3618c2` ir, 2026-06-14)

Two distinct root causes, found together: fixing the resolution bug
(originally filed in claude-todo.md, explorations `c828f766`; repro
`pkg/boxlib.bni` with `Box[T]` + `GetMan[T](@Box[T])`) unblocked
compilation but exposed a silent miscompile — the cross-package call
returned `0` instead of the boxed value.

**Bug 1 — `.bni` resolution (types, `02fa92d7`).** `buildScopeFromFile`
keys a package's generic type/iface decls under its short name (so
importers' qualified `pkg.Box[…]` refs resolve), but a same-file function
signature references them UNQUALIFIED (empty pkg key);
`lookupGenericTypeDeclPkg` / `lookupGenericIfaceDeclPkg` matched only the
exact key, so the unqualified head missed → `undefined` → parameter
recorded `@void` → mismatch vs the body's `@Box[T]`. Affects generic AND
non-generic funcs, concrete (`@Box[int]`) or type-param (`@Box[T]`) args,
and the generic-interface analog. Fix: same-package fallback — an
unqualified lookup that misses the empty key retries under
`currentPkgShort` (only after the exact-key miss, so already-resolving
lookups are unchanged). Unit test in `bni_scope_test.bn`.

**Bug 2 — consumer-side monomorphization (ir, `1c3618c2`).**
`ensureInstantiated` emits the specialized body into the CONSUMER's
module, but the body was written in the DEFINING package's namespace and
refers to its types / consts / sibling functions UNQUALIFIED. The IR-gen
resolved those bare names against the consumer: `resolveTypeExpr`
`TEXPR_NAMED` (`@Pair`) fell to the `TypInt` fallback (imported structs
are keyed qualified) and `TEXPR_INSTANTIATE` (`@Box[T]`) keyed on
`currentModulePkgPath` — both collapsing a struct parameter to `int`, so
the ABI passed one word and field reads emitted `0`; bare calls / consts
in the body targeted the consumer too. Fix: thread the defining-package
alias (the call-site `pkg.` qualifier; empty for same-package) into
`ensureInstantiated` and install it as `currentImportAlias` while
emitting the body, so every bare-name resolver (`resolveTypeExpr`,
`funcRefName`, the `gen_expr` const lookup, the `TEXPR_INSTANTIATE`
generic-decl lookup) keys on the defining package; nested same-package
generic calls inside an instantiated body propagate the alias (arbitrary
depth).

**Coverage / verification.** Conformance
`770_cross_pkg_generic_struct_arg` (the headline vec/hashmap case —
exercises both bugs) and `771_cross_pkg_generic_body_refs` (body
references the defining pkg's non-generic struct, a const, a sibling free
function, a nested generic); green across `builder-comp`,
`builder-comp-int` (VM), `builder-comp-comp`. Full conformance 1435/0;
`types` / `ir` / `codegen` / `vm` unit tests green; hygiene clean. Full
matrix verified (by-value / managed struct params, struct
construction+return, interface param + method dispatch); generic
*methods* remain unsupported (clean parse error, not silent-wrong).
`examples/generics/` `vec` / `hashmap` are now unblocked. The
`gen_util.bn` soft-limit overflow this introduced was resolved by
splitting the TypeExpr→Type resolution cluster (`resolveTypeExpr` +
`ifaceValueTypesAgree` + `findAnonStruct`) and its tests into
`gen_type_resolve.bn` (binate `a77bb248`).

### ~~MAJOR — VM cannot capture 9–16-byte by-value returns (X0:X1) from a raw compiled fn ptr; `_call_shim_aggregate` is sret-only → garbage → crash (2026-06-12)~~ — ✅ LANDED on main (binate `75049ff9`, 2026-06-13)

**Fix (2026-06-12):** added `_call_shim_pair(fn, data, a0..a6) ShimPair`
(`rt.bni` + recognized in `gen_call.bn`) — declared to return a 16-byte
struct, it lowers to `OP_CALL_INDIRECT` and reuses the generic
small-single-aggregate return path that already spills X0:X1 into a buffer
on every backend, so **no backend change** was needed.
`dispatchCompiledIfaceMethod` now picks scalar (≤8) / pair (9–16) /
sret-loud-fail (>16) by result size; `dispatchNativeIndirect` got the
matching bytecode arm (distinct shape selector, avoiding the silent Imm==8
collision). The >16-byte sret-from-raw-fn case (e.g. `Error.Error()`'s
32-byte managed-slice) is a loud-fail, NOT silently wrong — a follow-up if
ever hit. Validated: os builder-comp-int no longer crashes in
`errors.Is`/`Unwrap`; vm unit tests green both modes; conformance
builder-comp 1404/0; hygiene clean. Will land with the iface-interop /
stdlib-injection work. Original report below.

Discovered building VM↔compiled interface interop (calling methods on an
`@errors.Error` returned by an injected compiled `pkg/std/os`).  The os unit
tests in `builder-comp-int` crash nondeterministically inside `errors.Is`
(`io.IsEOF` → `errors.Is` → `cur.Unwrap()`), e.g. `TestReadEOF` aborts with no
result while `TestReadAtWriteAt` fails gracefully on the SAME first dispatch.

- **Symptom.** `dispatchCompiledIfaceMethod` (VM, the new compiled-vtable iface
  path) calls the raw method fn ptr from the `@__ivt` slot via
  `rt._call_shim_aggregate(fn, retbuf, data, …)` for any result >8 bytes.  For
  a **9–16-byte by-value return** (an iface `@Error` is 16 bytes; a raw slice
  `[]T` is 16; a 2-word struct) the callee returns in **X0:X1**, not via the
  X8 sret retbuf — so `retbuf` is left **unwritten**.  The bytecode then reads
  an uninitialized 16-byte iface `{data, vtable_word}` off the VM stack; its
  garbage vtable word makes `present(cur)` and the next `cur.Unwrap()`
  nondeterministic → dispatch into a garbage fn ptr → crash.  The
  nondeterminism is the tell: dispatch #1 is byte-identical across two tests,
  but the divergent control flow comes from whatever stack garbage the retbuf
  aliases.
- **Root cause.** AArch64 (and the Binate ABI, `aarch64_call.bn:153`) uses
  sret (X8) **only for returns >16 bytes**; ≤16-byte aggregates come back in
  X0:X1 (`aarch64_call.bn:105`).  `@__ivt` method slots hold **raw fn ptrs**
  (`aarch64_iface.bn:221-249`), NOT retbuf-normalizing shims.
  `_call_shim_aggregate` is defined as the **shim/retbuf convention**
  (`rt.bni:72-85`) — it only works when `fn` is a per-function shim that writes
  through X8 for every aggregate size.  `dispatchExternBinding` is safe because
  it calls `vtable[1]` = that shim; the new raw-fn iface path is not.  So the
  VM currently has **no primitive that captures X0:X1** — scalar grabs only
  X0, aggregate assumes X8 sret.  ≤8-byte (X0) and >16-byte (X8 sret) raw-fn
  returns are fine; only the 9–16-byte middle is broken.
- **Proposed fix.** Add a pair-capture primitive (e.g. `_call_shim_pair(fn,
  retbuf, data, …)` that calls the raw fn and stores X0→retbuf[0],
  X1→retbuf[8]); IR-gen recognizes it like the other `_call_shim_*` and the
  backends emit the X0:X1 store.  Then `dispatchCompiledIfaceMethod` picks
  scalar (≤8) / pair (9–16) / aggregate-sret (>16) by `resultSize`.
  Alternative: route iface-method dispatch through the method's registered
  **shim** (build a raw-fn-ptr→shim map in `RegisterPackageFunctions`) so the
  uniform shim ABI handles every size — avoids a new ABI primitive but needs
  the map + reflect exposure of both ptrs.
- **Note.** Independent of this, the os tests' `io.IsEOF` would STILL fail on
  the cross-mode sentinel-identity issue (compiled `io.EOF` ≠ bytecode
  `io.EOF`) until `io`/`errors` are injected **native-only** (one instance).
  Native-only injection also makes `errors.Is` run native→native, sidestepping
  this X0:X1 path for stdlib internals — but the gap is still a real VM-interop
  defect for user bytecode that calls a method on a compiled iface value.
- **Covered by.** `scripts/unittest/pkg-std-os.xfail.builder-comp-int`
  (`TestReadAtWriteAt` / `TestReadEOF`), currently xfailed.

### ~~MAJOR — `cast(int, float)` for non-finite / out-of-range floats is platform-dependent (undefined; a hole in the "no UB" promise)~~ — ✅ CORE LANDED 2026-06-12 (binate `b3a52025`); follow-ups remain

- **✅ LANDED (binate `b3a52025`)**: float→int now SATURATES to the target type's
  `[MIN, MAX]` + NaN→0, lowered ONCE in shared IR-gen (`emitGuardedFloatToInt`,
  `pkg/binate/ir/gen_cast_float.bn`) — every backend + the VM inherits it, no
  per-backend logic. `conformance/732_float_int_saturation` green on builder-comp,
  builder-comp-int (VM), gen2/gen3, native aa64 + x64; unit tests + spec updated.
  Plan: `plan-float-int-saturation.md`.
- **✅ Commit 2 LANDED (binate `068749c8`)**: `gen-diff-scalar.py` float→int matrix
  re-enabled with a `saturate_to_int` oracle — sweeps the 2^(N-1)/2^N thresholds,
  doubles, negations, and exact ±Inf/NaN bit patterns across every width int8…int64
  signed+unsigned × f32/f64. Green builder-comp/VM/gen2; 2 pre-existing native-aa64
  signed-narrow xfails stay (orthogonal `aa64-subword`). Closed the self-review's
  coverage observations.
- **Only remaining**: un-skip the 3 minbasic programs (P168/P174/P180) — **handed
  off to the `binate/examples` repo** (per user 2026-06-12, someone else owns the
  examples work); their `+Inf → index` now has a defined cross-platform value.

- **DECISION (RATIFIED 2026-06-12, user)**: float→int where the value is `±Inf`,
  `NaN`, or outside the target integer type's range is **SATURATE to the target
  width's [MIN, MAX] + NaN→0** — well-defined and identical across all targets.
  Refines (does not contradict) Go, whose spec leaves it "implementation-specific,
  conversion succeeds (no panic)"; saturation pins a defined value while staying
  panic-free. Matches Rust `as` (since 1.45) and WASM `trunc_sat`. arm64
  (`FCVTZS`/`FCVTZU`) already conforms. **Saturation is to the TARGET width**
  (`cast(int8, 1000.0)` → `127`, NOT `int64`-saturate then modular-narrow). Plan:
  `plan-float-int-saturation.md`.

- **Symptom**: converting a float that is `±Inf`, `NaN`, or outside the integer
  range to an int yields a **different value per target ISA**, with no defined
  contract:
  - **arm64** (`FCVTZS`/`FCVTZU`) *saturates*: `+Inf → INT64_MAX`, `-Inf →
    INT64_MIN`, `NaN → 0`.
  - **x86-64** (`CVTTSD2SI`) returns the "integer indefinite" `INT64_MIN`
    (`0x8000…0000`) for ALL out-of-range/non-finite inputs.
  - **LLVM text backend** emits raw `fptosi`/`fptoui` (not `llvm.fptosi.sat`) →
    out-of-range/NaN is **poison/undef**.
- **Discovery trigger (2026-06-12)**: wiring the `binate/examples` minbasic NBS
  e2e suite to CI. minbasic converts an overflowed BASIC number (machine `+Inf`)
  to an integer index via `cast(int, roundf(evalNum(...)))` for an array
  subscript (NBS P168), an `ON…GOTO` index (P180), and a `TAB` column (P174). On
  arm64 the macOS-frozen fixtures show `9223372036854775807` (= `INT64_MAX`,
  arm64 saturation); on the x86-64 Linux CI runner the same programs produce
  `INT64_MIN`. compiled == interpreted on *each* platform, but the two platforms
  disagree. (minbasic now skips those 3 programs with its own follow-up TODO; the
  "right integer to print" is moot once this is defined.)
- **Root cause / confirmation** (source-level audit): every backend emits the
  bare truncating-convert with no saturation/range/NaN normalization —
  `pkg/binate/native/aarch64/aarch64_float.bn:275-290` (`FCVTZS`/`FCVTZU`),
  `pkg/binate/native/x64/x64_float.bn:366-409` (`CVTTSD2SI`),
  `pkg/binate/codegen/emit_ops.bn:280-294` (`fptosi`/`fptoui`, not `.sat`). The
  VM does a bare `cast(int, f)` (`pkg/binate/vm/vm_exec_helpers.bn:226-258`), so
  it inherits the host ISA's behavior too. `conformance/gen-diff-scalar.py:26-33`
  **deliberately excludes** "out-of-range float→int, and NaN/±Inf → int" as
  "hardware semantics → target-dependent, so there is no single target-stable
  expected to assert." The only spec statement is `claude-notes.md:483` ("`cast`
  … wraps/truncates — hardware semantics, well-defined"), whose examples are all
  int↔int; float→int out-of-range is **not** in the implementation-defined
  catalogue (`plan-language-spec.md` §21) — so it is neither pinned nor
  catalogued, a genuine gap in the otherwise-emphatic "no UB" promise.
- **Proposed fix (owner decides the contract)**: make it well-defined. Most
  natural is **saturation + NaN→0** (the arm64 `FCVTZS` contract, so arm64 is
  already conformant): use `llvm.fptosi.sat`/`fptoui.sat` on the LLVM path, and
  add a saturation/NaN guard around `CVTTSD2SI` (x64) and the VM's `cast(int,f)`.
  Alternative: trap. Either way, compiler and VM must agree per-target AND across
  targets once defined. Then **add the now-excluded conformance coverage**
  (out-of-range, `±Inf`, `NaN` → every int width, signed + unsigned) and either
  pin the value or list it in §21.

### ~~arm32-baremetal runtime files (`crt0.s`/`semihost.s`) resolved against the target-iface OVERLAY, not the repo root → link failed → arm32 unit + conformance red — REGRESSION (in-window) — ✅ RESOLVED 2026-06-10 (binate `1d95923e`; CI-CONFIRMED: baremetal link fixed~~ — conformance 1304✓/8✗, the 8 are pre-existing arm32 codegen residuals)
- **Symptom**: on `fd3cb7ac` (after the shift fix unmasked it), arm32-baremetal unit + conformance fail at LINK, not compile: `clang: error: no such file or directory: '.../ifaces/targets/arm32-baremetal/runtime/baremetal_arm32/crt0.s'` (and `semihost.s`). The files exist at the **repo root** `runtime/baremetal_arm32/`, not under the `ifaces/targets/arm32-baremetal/` overlay.
- **Root cause**: `appendTargetRuntime` (`cmd/bnc/target.bn:308`) joins the relative `targetRuntimeFiles` (`runtime/baremetal_arm32/crt0.s`, …) against `root`, where `root = primaryRoot(cli)` (`cmd/bnc/args.bn:58`) = `cli.BniPaths[0]` — the FIRST `-I` path. The per-target `ifaces/targets/` overlay work (build.bni metadata, in-window) makes `binate-paths --iface --target arm32-baremetal` **prepend** `ifaces/targets/arm32-baremetal` as the first `-I` entry (confirmed; the unittest-runner change `ac738936` mirrors `--target` onto `--iface` for cross modes). So `BniPaths[0]` is the overlay dir, not the repo root, and the runtime files resolve to a non-existent path. (Harmless on host: host `targetRuntimeFiles` is empty, so `appendTargetRuntime` is a no-op — which is why host modes stayed green and this hid behind the `int64 << int` compile error until that was fixed.)
- **Baseline**: `builder-comp_arm32_baremetal` Unit was green at bnc-0.0.7 (before both the `ifaces/targets/` overlay and the types compile error). In-window regression; the SECOND arm32 regression masked behind the first (the `int64 << int` one, now resolved `fd3cb7ac`).
- **Severity**: MAJOR — breaks all arm32-baremetal linking on a previously-green mode — but **NOT a bnc-0.0.8 release blocker**: per the user (2026-06-10), arm32-baremetal is excluded from the release gate. Fix tracked for after.
- **Fix (direction, per user)**: the runtime files + linker script belong on the existing **`--runtime` flag** mechanism — the runner should pass the concrete `crt0.s`/`semihost.s`/`.ld` paths explicitly (as it already does for `libgcc.a` via `--link-after-objs`), NOT have bnc infer a `root` from `-I[0]` (`primaryRoot` = `BniPaths[0]`, which is wrong now that the iface overlay is prepended). i.e. retire the `appendTargetRuntime`/`primaryRoot`-based path inference for these in favor of `--runtime`. (My initial `primaryRoot`-skip-overlay idea was wrong — recorded so it isn't retried.)
- **Discovery**: 2026-06-10, watching CI on the shift fix `fd3cb7ac` — the arm32 error changed from `mismatched types int64 and int` (compile) to the missing-`crt0.s` link error.
- **✅ RESOLVED 2026-06-10 (binate `1d95923e`)**: rooted out `primaryRoot`/`root`
  from bnc entirely — per the user's "full root-out" decision, which SUPERSEDED
  the earlier "runner passes the link files explicitly" direction above. The
  loader is now seeded from `discoverBinateRoot(--runtime)`; `appendTargetRuntime`
  resolves `targetRuntimeFiles`+linker relative to `dirOf(--runtime)`; baremetal's
  `targetRuntimeFiles = {"semihost.s"}`, `targetLinkerScript = "baremetal.ld"`, and
  `crt0.s` is the `--runtime` (linked via a split link-gate: link the `--runtime`
  file whenever present, stubs only when `!suppressHostRuntime`). `binate-paths
  --target arm32-baremetal` now supplies `impls/core/baremetal` +
  `runtime/baremetal_arm32` on `-I`/`-L` (replacing the deleted
  `targetImplPathSuffixes`); the two baremetal runners pass `--runtime
  .../crt0.s` + `--target arm32-baremetal` on their `--impl` call.  Verified host
  (gen1 builds, conformance 001+692, bnc-unit 114) + baremetal package-resolution
  via `gen1 --target arm32-baremetal -c`.  CI on `1d95923e` CONFIRMS the fix: the
  `crt0.s` link error is gone; arm32-baremetal conformance is 1304 passed / 8
  failed (the 8 = pre-existing arm32 codegen residuals: `uint32` bitnot/neg +
  `int64` add/div/sub, shared with arm32_linux), unit 25 passed / 2 failed
  (pre-existing arm32 float precision: Sin/Cos/Tan LargeArg) — was broken at link
  for everything before.  Host green; no new regression (the parent `93d6ecd4`
  had the identical cross-lane failures).
- **Bonus finding → follow-up — dead `Builtin` machinery**: `root` was threaded
  through the registration call-graph ONLY to feed `collectPkgFile`'s
  `if depPkg.Builtin { read <root>/<pkg>.bni }` branch, but `RegisterBuiltin` has
  NO production caller (only `loader_test.bn`), so `pkg.Builtin` is never true in a
  real build → that branch was DEAD (and pointed at a path that no longer exists
  post-regularization).  Removed the dead branch + vestigial `root`.  The REST of
  the Builtin machinery (`loader.RegisterBuiltin`, `loader.Package.Builtin`, the
  `pkg.Builtin` guards in cmd/bnc compile/main/test) was fully dead-but-harmless →
  ✅ REMOVED 2026-06-11 in a staged 4-commit series (binate `8f148f35` no-ops +
  always-false guards, `1dac744f` cmd/bni de-thread, `4516579b` repl de-thread,
  `f0a6a637` keystone field+RegisterBuiltin+test). The removal also de-threaded the
  vestigial `root`/`bniPaths` that only fed the dead `loadBNIFromDisk` disk-fallback
  through cmd/bni + repl IR-gen imports (the interpreter loads all interfaces via the
  loader's `pkg.BNI`/`pkg.Merged`); zero `Builtin` references remain repo-wide.

### ~~Interface-method dispatch of a multi-return method mis-packs the result tuple on two backends — SILENT wrong values~~ — BOTH FACETS RESOLVED 2026-06-08 (residual: arm32 + x64-linux xfails)
- **STATUS 2026-06-08 — RESOLVED on every runnable host/native mode.** The CR-2 SEAM (`6c39d460`) fixed the front-end (typed the iface multi-return as the same anonymous tuple struct a direct multi-return uses), which exposed two BACKEND tuple-lowering gaps; both are now fixed: **Facet A** (LLVM >16-byte sret) by Plan-2 `43cb195d`, **Facet B** (native aa64 sub-word) by Plan-3 `cc2ddcc4`. `iface-multi-return/{int,u16}/{2,3,4,5}` are green on `builder-comp{,-comp,-comp-comp}` (LLVM), all three `-int` modes (VM), `builder-comp_native_aa64` (aa64), and `builder-comp_native_x64_darwin` (x64 via Rosetta). The DIRECT multi-return call was already correct for the same shapes, which is what localized each gap to iface-dispatch lowering.
- **Facet A — LLVM, >16-byte result (codegen, Plan 2) — RESOLVED (`43cb195d`)**: `iface-multi-return/int/{3,4,5}` (3/4/5 `int`s = 24/32/40-byte struct) printed GARBAGE on `builder-comp{,-comp,-comp-comp}`. The LLVM iface-call emission dispatched a register-returned tuple via sret incorrectly; `emit_iface_call.bn` now dispatches it by value (plan-cr2-2 Defect 3). LLVM-host int/3,4,5 xfails removed.
- **Facet B — native aa64, sub-word result (native, Plan 3) — RESOLVED (`cc2ddcc4`)**: `iface-multi-return/u16/{2,3,4,5}` (2..5 `uint16`s) printed wrong values on `builder-comp_native_aa64` (`int/*` was correct). Root cause: `common.IsMultiReturnCall` recognized only `OP_CALL`/`OP_CALL_FUNC_VALUE`, so an iface multi-return fell into the aggregate-single-return collect; on aa64 (one register per tuple field) that collect read `ArgWords` eightbytes and dropped every field past the first (e.g. `(u16,u16)` lost field 1). x64 survived because its callee coalesces sub-word fields into the RAX/RDX byte image. Fix: add the `OP_CALL_IFACE_METHOD` arm to `IsMultiReturnCall` — every downstream native site keys on it (PlanFrame tuple-vs-pointer spill, the per-arch collect, EXTRACT's `SpillHoldsAggregatePointer` split, `CallReturnsBigMultiReturn`), so aa64 runs its per-field collect and x64 runs `collectMultiReturnTuple` (pre-wired by `760402b7`). aa64 u16/* unxfailed (XPASS confirmed); x64 verified via darwin-x64; new common unit tests pin the classifier arm + PlanFrame split.
- **RESIDUAL (not part of either facet)**: `iface-multi-return/{int,u16}/{2,3,4,5}` stay xfailed on (a) **arm32** (baremetal + linux) — arm32 has no native backend and goes through LLVM, yet stays broken after the host-LLVM Facet-A fix, so it is a SEPARATE arm32-specific issue (cause unconfirmed — likely 32-bit sub-word/aggregate ABI; not runnable on the dev host); and (b) **`builder-comp_native_x64-comp_native_x64`** (x64-linux/ELF) — the x64 backend codegen is verified correct via the darwin-x64 runner (same codegen, different objfmt), so these are almost certainly STALE, but unrunnable on this host (no qemu) so left for a follow-up where x64-linux executes. Both residuals are tracked here; neither is silent wrong-code on a runnable mode.

### ~~~~Native (aa64/x64) mis-packs a SUB-WORD struct-return (`five-u8`) returned through a FUNCTION-VALUE call — SILENT wrong values~~~~ — FIXED aa64+x64 (binate `3950f59f`, plan-cr2-3 Defect 2); arm32 remains
- **FIXED (aa64/x64)**: the caller passes a retbuf for ANY aggregate funcval return, but the per-function shim only wrote retbuf for retSz 9..16 (usePack) / >16 (useSret) — a ≤8-byte aggregate fell into the SCALAR (tail-branch) shim and never wrote retbuf, so the caller read an unwritten alloc region. Lowered the shim's pack-path floor to cover an aggregate result of 1..16 bytes, gated on a new `shimReturnIsAggregate[_x64]` (a ≤8-byte SCALAR still tail-branches — size alone can't tell a scalar from a ≤8-byte aggregate). `funcval-return/five-u8` unxfailed on native aa64 (CI-verified) + x64 (verified via the darwin-x64 mode); 9..16 / sret / scalar funcval returns unaffected; native funcvalue-shim unit tests green.
- **arm32 REMAINS xfailed**: arm32 has no native backend (LLVM path) and mis-handles this for a SEPARATE, unconfirmed reason — not the native shim. The `funcval-return/five-u8` arm32 xfail reason now says so; needs its own investigation.
- **Follow-up (GAP)**: the only ≤8-byte funcval-return cell is sub-word (`five-u8`, 5B). A non-sub-word ≤8-byte cell (e.g. `two-u32`=8B or `{int32}`=4B) would pin the whole retSz≤8-aggregate class end-to-end (the fix keys on aggregate-ness + retSz≤16, NOT sub-word packing). Deferred: adding a STRUCTS shape to `gen-abi-matrix.py` generates all 6 abi families with arm32 / x64-linux xfails not verifiable on the dev host.
- **Was**: `funcval-return/five-u8` printed `16,146,211,…` on native aa64/x64 instead of `1,2,3,…`; the iface-dispatch variant and the DIRECT struct-return passed. Discovery 2026-06-07 (abi result-side matrix sweep); fixed 2026-06-08.

### ~~`readonly` / `const` type modifier is broken for managed values~~ — FULLY RESOLVED: field read (binate `27c1ee8b`), iface dispatch (binate `d3761004`), and the `readonly @Box` method-receiver rejection via the object-const model (binate `408cc533`)
- **Symptom 1 (SILENT wrong-code) — FIXED (binate `27c1ee8b`, plan-cr2-1 Defect 1)**: reading a field through a `readonly @T` managed pointer returned the wrong value. `var p @Box = make(Box); p.v = 7; var rp readonly @Box = p; println(rp.v)` printed `0`, not `7`. Root cause: genSelector / genSelectorPtr (and the managed/raw-ptr-to-struct predicates) didn't peel the IR-transparent `readonly`/named/alias wrapper, so the read fell through every Kind-dispatch arm to `EmitConstInt(0)`. Fixed by adding `peelTransparent` (readonly/named/alias to fixpoint) and peeling the dispatched type at acquisition + each `val.Typ` read. `field-read/*` matrix cells (+ `pass-arg/value-struct`, `globals/readonly/struct` on the modes their internal field-read unblocked) unxfailed; conformance 660 + a gen_selector unit test added.
- **Symptom 2 (compile error)** — iface dispatch part FIXED (binate `d3761004`, plan-cr2-1 Defect 2): `readonly @Iface` → `cannot access field on this type` is gone; `tryMethodCall` now resolves the receiver with `resolveAliasAndConst` (peels readonly) and `gen_iface.bn` peels the receiver before dispatch/mangling. **Still rejected**: `readonly @Box` calling a `*Box`-receiver method (`method/managed-struct` cell, xfailed).
- **The remaining rejection — FIXED via the object-const model (binate `408cc533`, plan-cr2-1 Defect 2b)**: `receiverShape`'s const flag now tracks OBJECT-constness only. An outer `readonly` on a POINTER (`readonly @Box`) is handle-const and no longer blocks dispatch — `readonly @Box` (const pointer, mutable object) calls any method, including `*Box`/`@Box`-receiver ones. Only an inner `readonly` on the pointee (`@readonly Box` / `*readonly Box`) or an outer `readonly` on a VALUE receiver (`readonly Box`) is object-const, and may call only a const-pointee-receiver method (`*Box`/`@Box`-receiver methods rejected — they could mutate the const object). Confirmed `@(readonly Box)` IS accepted (parses as `@readonly Box`) and `*readonly Box` receivers are supported. No const-method annotations. `method/managed-struct` unxfailed (all backends); 3 `check_method` unit tests; spec clarified in `claude-notes.md` ("Method dispatch keys off OBJECT-constness").
- **Impact**: `readonly`/`const` is effectively unusable on any managed value — interfaces (`@Iface`) and managed structs/ptrs with methods can't be called at all, and `readonly @struct` field reads silently corrupt. Directly blocks a *readonly* `io.EOF` sentinel.
- **Discovery**: 2026-06-07, designing `pkg/std/io`'s `io.EOF` (wanted a readonly managed-value global).
- **Root cause direction (needs investigation)**: (1) field-access lowering mis-bases / doesn't see through the `readonly` modifier on a managed pointer (the silent one — fix first); (2) method resolution needs a non-mutating-receiver path so a `readonly` receiver can call methods that don't mutate (cf. Rust `&self`, C++ const methods) — partly a language-design call (does Binate want const-correct receivers, or does `readonly` implicitly permit non-mutating method calls?).
- **Tests**: PINNED by `conformance/matrix/readonly` (Code-Red-2 Class B). After the Defect-1 fix: `field-read/{value-struct,managed-ptr,raw-ptr}` are GREEN on all backends; `pass-arg/value-struct` and `globals/readonly/struct` are green on LLVM (+aa64 for pass-arg) and stay xfailed only on VM / native-globals (Plan 2/3). `method/{iface,managed-struct}` (compile-error, all modes) remain xfailed red — that is Symptom 2 / plan-cr2-1 Defect 2 (check_method `resolveAliasAndConst`), still OPEN. `scalar/*` + `index/array` are green controls.

### ~~Named-distinct type transparency — ✅ LANDED 2026-06-11 (slices/pointers/arrays + field access + assignability + present/same/operators + named arrays + matrix cells); one remaining facet~~ — named COMPOSITE LITERALS (🔴 MAJOR, below)
- **DECISION (RATIFIED 2026-06-11, user)**: adopt Go's defined-type model — a named-distinct type is transparent to its underlying for operators, the built-ins `len`/`present`/`same`, indexing, slicing, and field access; methods are NOT inherited; assignability follows Go's identical-underlying + ≥1-unnamed rule; comparison follows the underlying's comparability EXCEPT Binate slices are never comparable (not even to `nil`). Peel only a **concrete/visible** underlying — opaque (nil-underlying) types stay rejected (encapsulation). Spec written: `claude-notes.md` "Type declarations — DECIDED"; `plan-language-spec.md` D5 (was v1-RESTRICTIVE, now adopted — forward-compatible, only accepts more code).
- **INCREMENT 1 — ✅ LANDED (binate `88e13633`)**: slice/pointer/array OPERATIONS (index, slice, `len`) + Go assignability. `IsSlice`/`IsPointer` peel `TYP_NAMED` via `peelNamedBounded` (consistent with `IsInteger`; opaque nil-underlying stays rejected); `checkIndexExpr`/`checkSliceExpr`/the `len` arg-check peel; `AssignableTo` recurses on a named type's composite `Underlying` (identical-underlying + ≥1-unnamed, readonly/const-drop preserved). **Func-value kinds EXCLUDED** — a named func-value type stays NOMINAL for func values (Option A/B2; `regressions/named-func-value-reject-value`). Comparison unchanged (slices non-comparable, incl. nil). Un-xfailed `regressions/len-named-managed-slice` (all modes); `conformance/719_named_slice_transparency` + negative unit tests (scalar-underlying / two-named still need a cast). Full builder-comp suite green (1380/0).
- **INCREMENT 2 — ✅ LANDED (binate `b7481bae`)**: field-access transparency. `checkSelectorExpr` now peels the alias/readonly/named-distinct chain to a concrete base (`peelFieldAccessBase`, tracking object-const) BEFORE the auto-deref, then re-peels the pointee — so `type H @Box; h.field` and `type P *Box; p.field` reach the struct's fields (read+write, auto-deref). Methods NOT inherited (lookup stays off `origXt`, the named type's own set); opaque (nil-underlying) types stay rejected; readonly-write rejection preserved (405 / readonly-inner-pointee still reject). `conformance/720_named_ptr_field_access` + selector unit tests. Full builder-comp suite green (1385/0).
- **present / same / operators / comparison — ✅ VERIFIED + PINNED**: `present`/`same` already peel named (via `comparabilityKind`); named-scalar `+`/`<`/`==` peel via `IsInteger`/`comparabilityKind`. `conformance/721_named_type_builtins` (present/same on named slice+ptr, named-int arithmetic/relational/equality) + `722_named_slice_eq_reject` (named slice stays non-comparable) — binate `e5201a44`. No code change needed (already correct).
- **NAMED ARRAYS — ✅ DONE & LANDED**: (a) parser (binate `722b804f`) — grammar D11 two-token lookahead in `parseTypeSpec`, so `type Row [3]int` parses (was greedily read as generic type-params); (b) IR-gen (binate `68d24423`) — `gen_access.bn`/`gen_expr.bn`/`gen_control.bn`/`gen_assign_multi.bn` now peel `TYP_NAMED` (via the combined `peelTransparent`) before the `TYP_ARRAY` test at every index/len/slice/store site, so `r[i]` lowers to a valid array GEP and `len(r)` returns the real length (was invalid LLVM / 0 — `MakeNamedType` leaves `.Elem` nil / `.ArrayLen` 0). Pinned by `conformance/723_named_array_type` (index write/read, len, by-value param, array slice) — green builder-comp + builder-comp-int. The parser fix correctly *raised* the IR-gen bug (xfail) rather than working around it; user authorized fixing both.
- **Matrix cells — ✅ DONE (binate `e91e2a1e`)**: `conformance/matrix/globals` `noinit/named-managed-slice` now reads `len(G)` (was compile-only); added `init/named-array` + `noinit/named-array`; README refreshed. Green builder-comp + builder-comp-int.
- **✅ FIXED & LANDED — named COMPOSITE LITERALS (binate `2eeb71c1`)**: `Row{10,20,30}` / `NS{7,9}` / `Buf{1,2,3}` now keep their initializers. `genCompositeLit` (`gen_composite.bn`) dispatches on `peelTransparent(resolveTypeExpr(e.TypeRef)).Kind` (was the syntactic `e.TypeRef.Kind`, which routed a `TEXPR_NAMED` to the struct path → `EmitConstInt(0)`), threading the peeled type through the array/managed-slice/raw-slice/struct lowerings; the var-decl alloca-reuse fast path (`gen_stmt.bn`) peels both sides. **Companion MAJOR fix, same commit — named managed-LOCAL leak**: `isManagedSliceType`/`isManagedIfaceValueType`/`isManagedFuncValueType` peeled only readonly, never `TYP_NAMED`, so a `var b Buf` local was never RefDec'd (0 vs 1 `ZeroRefDestroy` — silent leak); now peel via `peelTransparent` (matching `isManagedPtrType`), and the cleanup loops (`gen_util_refcount.bn`) peel `slot.Typ`. Verified refcount-identical to the unnamed form (no leak/double-free). `conformance/728_named_composite_literal` (array/struct/managed-slice, local + global) green builder-comp + builder-comp-int; full suite 1393/0.
- **✅ FIXED & LANDED — `return <named composite literal>` (binate `672d884d`)**: `func f() Row { return Row{100,200,300} }` returned GARBAGE (e.g. `var a Row = f(); a[0]+a[2]` printed `14420308617`, not 400). Root cause: `genReturnStmt` (`gen_return.bn`) loads the by-value array/struct out of its composite-lit alloca only when the DECLARED result type's `.Kind` is `TYP_STRUCT`/`TYP_ARRAY`, but a named result has `.Kind == TYP_NAMED`, so the load was skipped and the alloca POINTER was returned where the value belonged. Fix: `peelTransparent(ctx.Func.Results[i])` before the kind test (mirroring the var-decl fast-path peel `2eeb71c1` and the line-124 `&local` peel). Named managed-slice return went through the slice path and was already correct. `728` now also covers return-position (array/struct/managed-slice) + call-arg position; full builder-comp suite green (1395/0), 728 also green builder-comp-int + builder-comp-comp.
- **✅ FIXED & LANDED — `func f() RS { return <managed-slice> }` over a named RAW-slice (binate `b34928f5`)**: with `type RS *[]int`, returning a `@[]int` value where the declared result was `RS` produced invalid LLVM `ret %BnManagedSlice` against result type `%BnSlice` → LLVM-verifier rejection (a LOUD compile error on checker-accepted code, not silent corruption — major, not critical). Root cause: the `@[]T→*[]T` conversion in `genReturnStmt` was gated on the UNPEELED declared-result kind — `retTyp.Kind == types.TYP_SLICE` (`gen_return.bn:93`, single-value path) and `resTyp.Kind == types.TYP_SLICE` (`gen_return.bn:60`, `return f(...)` multi-return arm) — both false for a named raw-slice result, so `EmitManagedToRaw` was skipped. Fix: peel (`peelTransparent`) the declared result type before both `== TYP_SLICE` tests, mirroring the line-124 peel from `672d884d`. `conformance/730_named_raw_slice_return` covers both paths (single-value `return s` + multi-return `return f(...)`) as valid borrows; full builder-comp green (1397/0), also green builder-comp-int + builder-comp-comp. **Audit context — 4 sibling sites were FALSE POSITIVES** (the unpeeled `.Kind` check is redundant; downstream `emitStoreManagedSlot`/store paths already peel; all compile+run correctly, empirically verified): `gen_stmt.bn:301` (`var rs RS = mslice`), `gen_composite.bn:108` (named-raw-slice struct field init), `gen_control.bn:133` + `:211` (plain assignment, named source or target). Their unpeeled checks could be tidied for consistency but are not bugs — left as-is.
- **Minor follow-up (checker coverage)** — ✅ RESOLVED (binate `e81bfbbe`; coverage `340e8ff5`): `checkCompositeLit` now peels the alias/const/named-distinct chain to the underlying composite SHAPE (`peelNamedBounded`, cycle-bounded) and routes EVERY named kind — struct, array, managed-slice, raw-slice — to its element/over-count checker (the literal's TYPE stays the original named type). `Row{1, "x", 3}` and a named-struct over-count are now flagged. Negative tests: `742_named_array_lit_checked` (named array) + `744_named_composite_lit_checked` (named struct over-count + wrong-type for named struct/managed-slice/raw-slice). Reviewed complete + correct.
- **── Historical context below ──** (the original 455 question + the v1-RESTRICTIVE decision — SUPERSEDED 2026-06-11 by the adopt-Go ratification + landed increments above; kept for the discovery/rationale trail.)
- **What surfaced it**: building the Defect-1 named-distinct companion test (plan-cr2-1). `type Handle @Box; var h Handle = cast(Handle, p); h.v` is rejected by the *type checker* with `cannot access field on this type` — and likewise `type NamedBox Box; nb.v` (named-distinct over a struct value). This is NOT the Defect-1 IR-gen literal-0 bug (that was `readonly`/alias and is fixed `27c1ee8b`); the named-distinct case never reaches IR-gen because the checker rejects field access first.
- **The question**: should a named-distinct type (`type X <underlying>`) inherit field access / non-mutating method dispatch from its underlying type? Reference points (verified empirically, go1.26.3): Go ALLOWS field access through a named-distinct type whether the underlying is a struct VALUE (`type B A` → `b.X` reads/writes) OR a POINTER-to-struct (`type P *A` → `p.X` works via auto-deref); only the underlying's METHODS are not inherited (call them via an explicit conversion, e.g. `A(b).M()`). (An earlier note here claimed Go disallows field access through a named pointer type — that is wrong.) Today Binate rejects field access through named-distinct in BOTH cases. **Decision (plan-language-spec.md D5, 2026-06-08):** stay RESTRICTIVE in v1 (reject — the forward-compatible direction, since opening up later breaks no code while tightening later would), with the documented target being Go's rule (allow field access, incl. auto-deref for a pointer underlying; never auto-inherit methods).
- **Where**: the field-access type-checker (`pkg/binate/types`, the selector/`check_selector` path) — it peels `readonly`/alias (those field reads type-check) but not named-distinct. Whatever the decision, it is a deliberate language-semantics change and must be ratified before implementing (do NOT silently make the checker peel named-distinct).
- **Scope**: a separate decision from Defect 1; IR-gen is already wrapper-transparent for named (peelTransparent peels `TYP_NAMED`), so if the checker is later opened up, the lowering is ready. Also relevant to whether a named-distinct *managed pointer* variable is refcounted correctly (isManagedPtrType now peels named, so a `Handle` var IS RefDec'd — that part is handled).
- **Discovery**: 2026-06-08, plan-cr2-1 Defect 1 companion-test reconnaissance.

### ~~A relational op with an untyped int literal on the LEFT and a signed int on the right uses an UNSIGNED comparison — silent wrong result, ALL backends~~ — FIXED 2026-06-06 (binate `b54c9fdf`)
- **Fix**: `gen_binary.bn` (`genBinary`) now stamps the resolved concrete type
  onto an untyped-int operand after `widenType`+`ensureWidth`.  `widenType`
  already resolves an untyped operand to the other's concrete type, but
  `ensureWidth` returns it unchanged at equal width, leaving it
  `TYP_UNTYPED_INT` (Signed=false) — so every backend's relational lowering read
  it as unsigned.  Stamping the concrete type fixes signed/unsigned selection on
  all backends at once (and makes div/rem/shift with an untyped-literal operand
  use the resolved signedness consistently).  Pinned by
  `conformance/regressions/cmp-literal-left-signedness` (operand order ×
  relational × signedness × width) across LLVM/VM/gen2/native; full builder-comp
  conformance 1069/0.  `math.Pow` reverted to Go's faithful `4096 < xe`
  (binate `f7d6446b`).  The systematic home for this class is the scalar
  matrix's named-but-unbuilt "comparisons" axis (plan-differential-testing.md v2).
- **Symptom (was)**: `5 < xe` where `var xe int = -1` evaluated to **true** (`5 < -1` is
  false).  An untyped integer literal on the LEFT of `<` / `<=` / `>` / `>=`,
  compared against a SIGNED `int` variable, emits an unsigned compare — so a
  negative signed value is read as a huge unsigned one.  Silent: no error, wrong
  control flow / result.
- **Scope confirmed by probing** (builder-comp / LLVM, builder-comp-int / VM, and
  native-aa64 — so it is a shared IR/type-checker bug, not a backend):
  - `literal < signedVar` (literal LEFT): UNSIGNED → BUG (`0 < -1`, `5 < -1`,
    `4096 < -1` all wrongly true).
  - `signedVar < literal` (literal RIGHT): signed → CORRECT.
  - `cast(int, literal) < signedVar` (typed literal LEFT): signed → CORRECT.
  - `var < var` (both `int`): signed → CORRECT.
  So the defect is operand-order-dependent: an untyped-literal LEFT operand drives
  the comparison signedness to unsigned.
- **Discovery**: 2026-06-06, porting `math.Pow` — Go's `1<<12 < xe` overshoot
  guard (`Othreshold`/exponent check) reads `4096 < xe` for a negative `xe`,
  making `Pow(0.5, 2)` return a wrong value instead of `0.25`.
- **Severity**: CRITICAL — silent wrong comparison result for a fundamental
  operation; any `literal < signedVar` (or `<=`/`>`/`>=`) in the codebase is
  miscompiled.  Most existing code writes `var OP literal` (literal on the right),
  which is why it went unnoticed.
- **Likely root cause (needs confirming)**: the relational lowering picks
  signed-vs-unsigned from the LEFT operand's type; an untyped int literal defaults
  to (or is treated as) unsigned, so the whole compare goes unsigned even though
  the other operand is a signed `int`.  The fix is in the type-checker / IR: when
  one operand is untyped and the other a typed integer, the untyped operand must
  take the typed operand's type (incl. signedness), and the compare's signedness
  must come from the unified type regardless of operand order.
- **Test (TODO when fixing)**: `conformance/matrix/scalar` (or a regression) — a
  comparison cell with the literal on the LEFT against a negative signed var, all
  four relationals, all signed widths; this is the "comparisons — signed vs
  unsigned at width boundaries" axis already named in `plan-differential-testing.md`
  (v2).  xfail until fixed.

### ~~Whole-array (aggregate) `=` assignment is silently dropped~~ — FIXED 2026-06-06 (binate, gen_control.bn)
- **Fix**: the ident and deref assignment arms in `gen_control.bn` now load the
  aggregate value out of an `OP_ALLOC` RHS (`isStructOrArrayAlloc(rhs)` →
  `EmitLoad`) before the store, matching the selector arm (which already did).
  Whole-array/struct `=` from a composite literal or another variable, and
  `*p = {...}`, now copy the value.  This *also* fixes GLOBAL array/struct
  initializers (they route through `__init`'s `x = expr`).  Pinned by
  `conformance/regressions/whole-aggregate-assign` + `global-aggregate-init`
  (LLVM/VM/gen2/native); full builder-comp conformance 882/0, no regression.
- **Confirmed root cause**: `emitStoreManagedSlot`'s non-managed path does a plain
  `EmitStore(slotPtr, val)`; the ident/deref arms passed `val` = the RHS `OP_ALLOC`
  *pointer* (a composite literal lowers to a stack alloca), so the pointer bits
  were stored into the aggregate slot instead of the contents. The selector and
  (struct-only) index arms already loaded first; ident/deref did not.
- **Symptom (was)**: `a = [4]int{10,20,30,40}` (a whole-array assignment via `=`,
  RHS a composite literal) did NOT update `a` — it stayed at its prior value. The
  store was silently a no-op; no error, no diagnostic.
- **Discovery**: 2026-06-06, porting `math.Pow10` (which wants package-level
  `var pow10tab [32]float64 = {...}` lookup tables). Minimal repro in a unit test:
  `var a [4]int = [4]int{0,0,0,0}; a = [4]int{10,20,30,40}; a[0]` reads `0`.
- **Scope confirmed by probing (builder-comp / LLVM gen1)**:
  - LOCAL array *decl-init* (`var a [N]T = [N]T{...}`): WORKS (int + float).
  - Whole-array `=` *assignment* (`a = [N]T{...}`): BROKEN (no-op) — the LHS keeps
    its old value. This is the underlying defect.
  - GLOBAL array initializer (`var arr [N]T = {...}` at package scope): BROKEN
    (reads as all-zero) — because the synthetic per-package `__init` (gen_init.bn)
    lowers each `var x = expr` into the assignment `x = expr`, and whole-array
    assignment is the dropped op. (GLOBAL *scalar* int init via `__init` WORKS,
    confirming `__init` itself runs in the unit-test harness.)
- **Likely root cause (needs confirming)**: IR-gen for `STMT_ASSIGN` with an
  aggregate (array, and probably struct) LHS/RHS doesn't emit an element-wise copy
  / memcpy — only scalar assignments store. The decl-init path (genLocalVarDecl)
  emits the element stores, which is why decl-init works but `=` doesn't.
- **Severity**: CRITICAL — silent data loss on a routine operation (`arr = other`,
  `arr = {...}`, and therefore *all* global array/struct initializers). Any program
  relying on a package-level table reads zeros with no warning.
- **Impact / blocks**: `math.Pow10` (table-based) is blocked; any global aggregate
  table or `arr = arr2` copy is unsafe until fixed.
- **Test (TODO when fixing)**: conformance cell for whole-array `=` assignment and
  global array-initializer readback (LLVM/VM/native/gen2), xfailed until the fix.

### ~~Global float `var` emits invalid LLVM (`global double 0`)~~ — FIXED 2026-06-06 (binate, emit.bn)
- **Fix**: `emit.bn`'s global-var static-zero emission now emits ` 0.0` when
  `g.Typ.IsFloat()` (else ` 0` for integers).  The runtime initializer value
  still flows through `__init`, so `var x float64 = 7.5` both compiles and reads
  back 7.5.  Pinned by `conformance/regressions/global-aggregate-init`.
- **Symptom (was)**: any package-level `var x float64` (with or without an initializer)
  makes the LLVM backend emit `@<mangled> = global double 0`, which clang rejects:
  `error: integer constant must have integer type` — the whole package fails to
  compile. (`var x float64 = 7.5` fails identically; the initializer is irrelevant
  because the static zero is what's malformed.)
- **Root cause**: `pkg/binate/codegen/emit.bn` global-var emission (~line 156-170)
  picks the static zero by type kind: `null` for pointers, `zeroinitializer` for
  slice/struct/array, and a bare ` 0` for *everything else* — but ` 0` is only
  valid for integer LLVM types. For `double`/`float` it must be ` 0.0` (or
  `0.000000e+00`). The runtime value (for `= expr`) comes from `__init`, which
  works for scalars — so emitting the correct float zero fully fixes scalar float
  globals.
- **Severity**: MAJOR — hard compile error (not silent), blocks any global float
  var. Discovered 2026-06-06 alongside the array-assignment bug, porting `Pow10`.
- **Proposed fix**: in the global-var zero-emission, branch on float type kinds
  (TYP_FLOAT64/TYP_FLOAT32) to emit ` 0.0`; keep ` 0` for integers. One-line-ish.
- **Test (TODO when fixing)**: codegen unit test asserting a `double`/`float`
  global emits a float zero, plus a conformance cell reading back a global float.

### ~~Plan-1 adversarial review (2026-06-06) — regressions + completeness gaps from the const/slice fixes — ✅ ALL FIXED+LANDED except ONE REPL-only leftover (parked-member iota-repeat~~ — see "Minor follow-ups" below; tracked in plan-cr2-followup.md Plan B)

The Plan-1 fixes (binate 1.1-1.6, landed 2026-06-05) were adversarially
reviewed. Real defects found, several wrong-code on main. Listed worst-first.
Repros marked (verified) were reproduced directly; (reviewer) were proven by a
review subagent via --emit-llvm / gen1. Each needs an xfail test added (Bug
Discovery Protocol) — most don't have one yet.

#### C1 — inc/dec on a local const mutates it — ✅ FIXED+LANDED (binate `2e8fbb33`, 2026-06-06)
- **Symptom**: `func main(){ const C int = 5; C++; println(C) }` prints **6** (verified). Pre-fix C++ was a silent no-op (const not in ctx.Vars → lookupVar nil); local-const materialization (binate 273d7e4a) put the slot in ctx.Vars, and the checker's STMT_INC_DEC arm (check_stmt.bn ~39-45) only checks IsInteger(), never const-ness, so genIncDec now load/add/store-s into the const slot.
- **Root cause**: checker STMT_INC_DEC doesn't reject a SYM_CONST target (assign / compound-assign / &C ARE rejected; only ++/-- slip through).
- **Fix**: reject ++/-- on a const in the checker. **Test**: conformance .error or a checker unit test (expectError), currently xfail/known-gap.

#### C2 — untyped non-int local const mistyped as int — ✅ FIXED+LANDED (binate `912718e6`, 2026-06-06)
- **Symptom**: `const C = 0.5; var y float32 = C` → high lane **24191** (garbage; verified); `const C = 0.5; var x float64 = C + 0.5` → invalid `add i64 …, double`, clang rejects. genDecl's no-TypeRef inference defaults typ=TypInt() (only special-cases EXPR_STRING_LIT), so an untyped float/bool/char local const gets an i64 slot and a `sitofp`/int op. The checker accepts it (untyped const stays assignable to float32), so it miscompiles silently. The var-init sibling `var C = 0.5` is checker-rejected for the float32 assign, so this divergence is specific to routing DECL_CONST through the int-defaulting path.
- **Root cause**: gen_stmt.bn genDecl untyped-inference covers only string literals; float/bool/char untyped local consts fall to TypInt default.
- **Fix**: infer the type from the initializer literal kind (float→float64, bool, char) for an untyped local const (mirror checker default-type), or reject untyped non-int local const. **Test**: conformance xfail (float32/float64 untyped local const).

#### C3 — local const as array dimension → IR-gen wrong size — ✅ FIXED+LANDED (binate `c97d7acc`, 2026-06-06)
- **Symptom**: `const N int = 3; var a [N]int; println(len(a))` → **30** (verified); package-scope const gives 3. Checker sees the local const via c.Scope.Lookup (correct length 3), but IR-gen resolveTypeExpr→evalConstExpr→lookupConst (gen.bn ~386) walks only moduleConsts (module scope) and falls back to parseIntLit("N")=garbage. Checker/IR-gen layout disagreement.
- **Root cause**: IR-gen has no function-local const table; lookupConst is module-only. (1.3a fixed array-dim for PACKAGE consts; locals were not covered.)
- **Fix**: give IR-gen access to local const values for resolveTypeExpr (a function-scoped const table), or restrict array dims to package consts at the checker. **Test**: conformance xfail (local const array dim).

#### C4 — &s[i] on a readonly-wrapped slice mis-strides — ✅ FIXED+LANDED (binate `f4769aac`, 2026-06-06)
- **Symptom**: `var s readonly @[]uint8 = "AB"; var p *uint8 = &s[1]; println(cast(int,*p))` → **0** (verified; expect 66). Dropping the TYP_STRUCT guard (binate 937ae78e) exposed it: for `readonly @[]uint8`, arrTyp.Kind==TYP_READONLY; isSliceType peels readonly (true) but arrTyp.Elem is then the INNER managed-slice, not uint8, so EmitSliceElemPtr GEPs with a ~32-byte stride. Pre-fix this crashed (guard failed → wild-pointer fall-through); now silently wrong.
- **Root cause**: genIndexPtr (gen_access.bn) uses arrTyp.Elem / collTyp.Elem without peeling TYP_READONLY.
- **Fix**: peel readonly (resolve to the underlying slice type) before reading .Elem in both slice arms. **Test**: conformance xfail (&readonly-slice[i]).

#### C5 — cross-package float const-EXPRESSION reads int 0 — ✅ FIXED+LANDED (binate `3dfc4b4a`, 2026-06-06)
- **Symptom**: a `.bni`-exported `const C float64 = 1.5 + 2.5`, read package-qualified, lowers to `add i64 0, 0` (reviewer). The CONST_EXPR family (binate 9ef5db58) was wired into gen_expr.bn's EXPR_IDENT read but NOT into gen_selector.bn's qualified read (no CONST_EXPR arm → falls to EmitConstInt(Val=0)), and the importer (gen_import.bn single + registerImportConstGroup) never registers a float const-expr at all.
- **Root cause**: const-folding fixes scoped to in-package producers/readers; the cross-package read (gen_selector) + import producers were not updated.
- **Fix**: add a CONST_EXPR arm to gen_selector read + route import producers through the shared classifiers (see M1/M4 — a unifying shared const-classifier is the real fix). **Test**: cross-pkg conformance xfail.

#### M1 — cross-package bool/float-comparison + bool-logic consts → silent int 0 — ✅ FIXED+LANDED (binate `3dfc4b4a`, 2026-06-06)
- **Symptom**: `.bni`-exported `const CMP bool = 1 < 2` / `(1<2)&&(3>2)` / `1.5 < 2.5` read cross-package lower to `add i64 0,0` → 0 (reviewer). gen_import single-const handles only EXPR_BOOL_LIT + float-literal; registerImportConstGroup calls only classifyConstLit; neither calls classifyConstBoolExpr/classifyConstFloatExpr.
- **Fix**: route both import producers (and gen_repl GenConstMember) through the same classifier chain genConst/genConstGroup use. **Test**: cross-pkg conformance xfail (bool-cmp, bool-logic, float-cmp).

#### M2 — composite-LITERAL element float32 store → memory corruption — ✅ FIXED+LANDED (binate `975db032`, 2026-06-06)
- **Symptom**: `var a [2]float32 = [2]float32{0.5, 0.5}` emits `store double %v, float* %slot` — an 8-byte store through a 4-byte slot (reviewer). The 1.1 coerceScalarWidth was wired into call-arg/field/return but NOT the three composite-literal element-store loops (genArrayLit, genManagedSliceLit, genRawSliceLit). Worse than the contained-field case (clobbers adjacent memory).
- **Fix**: call coerceScalarWidth before the element store in all three composite-literal loops. **Test**: conformance xfail (array/mslice/rawslice float32 literal).

#### M3 — const array dim in a struct field → spurious type-check rejection — ✅ FIXED+LANDED (binate `a56943c8`, 2026-06-06)
- **Symptom**: `const N int = 3; type S struct { arr [N]int }; … s.arr passed to a [3]int param` is REJECTED `cannot assign [..] to [..]` (reviewer). Struct types resolve once in pass 1 (collectTypeDecl), where no const has HasConstVal yet, so evalConstInt's leniency returns 0 and [0]int sticks on Field.Type; the var path re-resolves in pass 2, struct fields don't. Codegen is fine (resolves independently) → false-positive rejection, not a miscompile.
- **Fix**: collectDecls now folds the const's integer value (defineConstVal) at pass-1 forward-registration when evalConstIntValue can fold it — so a struct field's array dim resolving in the same pass sees the value. evalConstIntValue doesn't checkExpr, so non-literal / forward initializers fold to nothing and the name still resolves value-less (unchanged forward-ref behavior). **Test**: `TestConstArrayDimInStructField` (checker unit, expectNoErrors).
- **Residual gap (M3-residual)** — ✅ FIXED+LANDED by M6 (binate `3a3fa453`, 2026-06-06): the struct-BEFORE-const order (`type S struct { arr [N]int }; const N int = 3`) now resolves correctly — dependency-ordered const resolution (resolveTopLevelConsts) runs before struct types are collected, so the dim sees N's folded value. **Test**: `TestStructBeforeConstDim` (checker unit, expectNoErrors).

#### M4 — float const referencing only float consts → int 0 — ✅ FIXED+LANDED (binate `c716ea0c`, 2026-06-06)
- **Symptom**: `const C float64 = A + B` (A,B float consts, no float literal) → isFloatExpr false (literal-only) → integer evalConstExpr → lookupConst returns Val=0 for CONST_FLT entries → C registers CONST_INT 0 (reviewer). Checker accepts.
- **Fix**: isFloatExpr should also recognize a const-ident operand whose const is float; or the shared classifier should consult the operand const kinds. **Test**: conformance xfail.

#### M5 — iota inside a float CONST_EXPR re-lowers to 0 — ✅ FIXED+LANDED (binate `c716ea0c`, 2026-06-06)
- **Symptom**: `const ( C float64 = 1.5*cast(float64,iota); D; E )` → 0.0,0.0,0.0 (reviewer). CONST_EXPR stashes only the AST, not the iotaVal; the read-site genExpr has no iota in scope → `iota` ident → EmitConstInt(0). Affects bare iota-repeat float members too.
- **Fix**: capture iotaVal with the CONST_EXPR and bind it at the read site, or fold float-with-iota at gen time. **Test**: conformance xfail.

#### M6 — forward-ref non-literal untyped const → silent false-accept — ✅ FIXED+LANDED (binate `3a3fa453`, 2026-06-06)
- **Symptom**: `var x int = A; const A = B; const B = 1.5` is accepted with NO error (reviewer-verified probe); reversed order correctly errors. The pass-1 placeholder for a NON-literal initializer is a value-less untyped-int (untypedConstPlaceholder fall-through), which AssignableTo treats as assignable to any int with the fit-check skipped — so a forward use sees int, not the const's real (float/out-of-range) type. Trades a loud `undefined` for a silent missed type error.
- **Root cause**: untypedConstPlaceholder returns value-less untyped-int for non-literal initializers; AssignableTo skips the fit-check for value-less untyped-int.
- **Coarse fix REJECTED**: "don't forward-register non-literal untyped consts" (gate on `isSimpleLiteral`) was tried and reverted — it regresses the *legal* `var x int = A; const A = 1 + 2` (pass-2 use-sites are source-ordered and see only the placeholder → `undefined A`). The gate can't tell a legal forward int const from an illegal float one in pass 1.
- **Fix**: `resolveTopLevelConsts` (check_const.bn) resolves every top-level const in DEPENDENCY order in pass 1 — depth-first, resolving each initializer's referenced consts first (ConstResolving stack → cycle detection; ConstResolved memo), then `checkConstDecl` records the real type+value. A forward use sees the real type; struct field array dims see the folded value regardless of source order (also fixes M3-residual). Gated on a new `ReplDeclMode` flag (NOT TentativeMode, which is false during the REPL's pass-1) so the REPL keeps parking forward-ref consts. Approved acceptance changes: forward float-const→int errors; forward int-const out-of-range for a narrower target fails the fit-check; const cycles report a clean error. **Tests**: check_const_test.bn (float-rejected, struct-before-const, int-accepted, float-chain, cycle, self-cycle, out-of-range). Full builder-comp conformance 1070/0.

#### M7 — &f()[i] / &a[i][j] wild-pointer — ✅ FIXED+LANDED (binate `fdc92562`, 2026-06-06)
- **Symptom**: `&get()[1]` (call base) and `&a[i][j]` (nested-index base) compile then SIGSEGV / invalid IR (reviewer). genIndexPtr only handled e.X.Kind IDENT/SELECTOR; other bases returned nil → genUnary fell through to the r-value wild-pointer path (gen_expr.bn:177). Pre-existing (not a regression).
- **Fix (gen_access.bn genIndexPtr)**: general arm — (1) nested-index base recurses genIndexPtr for an in-place pointer to the inner element, then indexes it (array inner → GEP the pointer; slice/raw-ptr inner → load then index); (2) r-value base (call result) is genExpr'd and its slice/raw-pointer backing is GEP'd; an r-value array has no stable address → nil. The `&a[i][j]`-array sub-case became reachable once **M8** landed (same commit). **Test**: conformance 623 (unxfailed, call→managed-slice) + 638 (&a[i][j] array + slice-of-slices).

#### M8 — nested ARRAY indexing `a[i][j]` emits invalid LLVM — ✅ FIXED+LANDED (binate `fdc92562`, 2026-06-06)
- **Symptom**: plain `a[i][j]` on a 2-D array (e.g. `var a [2][3]int; a[1][2] = 7; println(a[1][2])`) — NO `&` involved — fails to compile: `error: '%vN' defined with type 'i64' but expected 'ptr'` (the codegen GEP-on-raw-pointer handler bitcasts i8*→elem*, but the base is the LOADED array r-value, an integer-ish value, not a pointer). Affects both READ (genIndex) and WRITE (assignment lowering).
- **Root cause**: same non-IDENT/SELECTOR index-base limitation as M7, but in genIndex (read) and the lvalue/assignment path: for a nested base `a[i]` they loaded the inner array as an r-value and then GEP/SliceGet it. Nested SLICE indexing already worked (the loaded inner slice value still carries its backing pointer); nested ARRAY did not.
- **Fix**: genIndex + the index-assignment lowering detect a nested-ARRAY base by TYPE (indexExprType / isNestedArrayBase — no genExpr, so the inner index isn't evaluated twice) and route through genIndexPtr to load/store via an in-place element pointer. Array-element store logic extracted into emitArrayElemStore (shared with the IDENT/SELECTOR arm). Verified 2-D/3-D arrays, arrays of managed slices, slice-of-slices. **Test**: conformance 637 (nested array read/write, incl. 3-D + managed element).

#### Minor follow-ups (adversarial review 2026-06-06)
- ~~bool-logic (`&&`/`||`/`!`) const-folding has no test~~ — ✅ FIXED (binate `1d41aa62`): adding the test surfaced a real miscompile (a bool const referencing another bool const, `const C bool = !A`, misfolded to int 0 — evalConstBool had no ident arm); fixed via lookupConstBool + ident/selector arms.  Conformance 642 + evalConstBool unit tests; gen_const folding helpers split into gen_const_fold.bn.
- REPL parked-member + iota-repeat — ✅ effectively RESOLVED (note was stale); investigated 2026-06-12. The headline ("a bare member after a PARKED member gets plain iota") does NOT reproduce: `checkGroupDeclTentative` (`check_pending.bn:383-406`) SYNTHESIZES a repeat decl carrying the preceding `prevExpr`+`effTypeRef` for a bare member, so the bare member PARKS with that expression and `GenConstMember` re-folds it (its `d.Value != nil`), AND `prevExpr`/`prevTypeRef` are carried across a parked member there. Pinned by the passing e2e `tier3-pending-const-group-bare-iota-repeat` (`const ( B0 int = M << iota; B1 ); const M int = 2` → `B1 == 4`, NOT plain iota). The `genConstGroup` parked-branch not carrying `prevExpr`/`prevTyp` is **non-manifesting**: a bare member after a parked member is itself ALWAYS parked (→ `GenConstMember`, never `genConstGroup`'s resolved-bare branch), so the `prevExpr` carry is unreachable; and a value-bearing no-type member is typed UNTYPED by the tentative checker (it only synthesizes the inherited type for BARE members), so `genConstGroup` leaving `prevTyp` unset MATCHES the checker. An attempted "consistency" fix (carry `prevTyp`/`prevExpr` across the parked position) was a no-op observably (`const ( A uint8 = M; B = 250 )` → `B + 10 == 260`, untyped, fix or not) and would have made `genConstGroup` type B `uint8` while the checker types it untyped — REVERTED. **Semantics sub-question — ✅ RESOLVED (user, 2026-06-12): Go-style is correct; normal/REPL consistency is the only requirement, and it holds.** Empirically tested all four quadrants (width-sensitive ops to expose the actual type): a BARE member inherits the preceding type — `const ( A uint8 = …; B ); B << 1` → 144 (uint8 wrap) — in BOTH the normal compile AND the REPL-after-parked path (`const ( A uint8 = M; B ); M = 200; B << 1` → 144, NOT 400). A VALUE-BEARING no-type member is UNTYPED — `B = 250; B + 10` → 260 — in BOTH paths. So the REPL does NOT diverge from the normal compile in any case, and the behavior matches Go (bare inherits value+type; a value-bearing member is its own untyped value). The earlier "normal inherits, REPL doesn't" framing was imprecise: `genConstGroup`'s `memberTyp = prevTyp` sets the inherited type in `moduleConsts` for a value-bearing member, but that is NON-MANIFESTING (the checker types it untyped and that wins for expression folding; `genConstGroup`'s own comment even says inheritance is "across BARE members"). No action: behavior is correct + consistent. (The non-manifesting `genConstGroup` value-bearing-inherit detail is left as-is — changing it is churn with no observable effect.)
- ~~named-float / named distinct scalar type mis-lowering~~ — ✅ FIXED + LANDED (binate `b43a0057` LLVM + shared type/IR-gen, `5b64b44a` VM, `0ca49975` native aa64/x64).  IR-gen now registers a named distinct non-struct type as a `TYP_NAMED` carrying its name (bare for the current package / REPL / self-types, qualified for imports — mirroring named structs, so method-dispatch keys agree) with `.Underlying` set, via a shared `typeDeclEntryType` helper at the six registration sites; resolveTypeExpr returns the TYP_NAMED.  Every Kind/Width/Signed-based lowering decision peels TYP_NAMED (codegen llvmType/typeBits/typeWidth/isUnsigned/emitBinop/emitCmp/emitCast/emitBitCast/OP_NEG/funcval-ABI + emitCopyRec/emitZeroRec; ir gen_print/gen_dtor/shift+divide signedness; VM via vmUnwrapNamed; native via common.UnwrapNamed).  types IsInteger/IsFloat now recurse and IsBool gained the peel.  Checker `resolveBuiltinScalarTypeDecls` fills a named-over-builtin underlying before top-level consts resolve (so `const C Rate = 0.5` over `type Rate float64` typechecks).  Also fixed a latent miscompile this surfaced: a named struct method-value receiver wider than one word was copied/zeroed as a single i64 (the int fallback masked it).  Conformance 646-652 (float, value+pointer methods, struct/array/managed-slice members, func/multi-return, sized-int width+sign, named-float const, cross-package value+method) green on every runnable mode; unit tests pin the codegen/types peels.  **Plan: `plan-named-distinct-scalar-types.md`.**
- ~~negative / div-by-zero array dims have no clean diagnostic~~ — ✅ FIXED (binate `a341b521`): evalConstInt now reports a negative length and a fully-known div/mod-by-zero dimension.  Conformance 643 / 644 error tests.
- ~~bare iota-repeat member type uses the GROUP (first-member) type~~ — ✅ FIXED (binate `9af67422`): genConstGroup tracks prevTyp alongside prevExpr, so a bare member inherits the PRECEDING member's type.  Conformance 645.
- ~~stale comments~~ — ✅ DONE (binate `73046ef3`): iota-repeat.bn comment updated to the fixed runtime (1,2,4,8).  The aarch64 "D-regs at offset 100" comment is already gone from the tree (recent float work removed it).

### ~~`handle` is not a user-expressible call shape~~ — NOT a bug, design note
- While extending the ABI matrix with call shapes, confirmed there is **no user
  syntax that emits `OP_CALL_HANDLE` with a value argument**: `OP_CALL_HANDLE`
  is the compiler-internal dtor/free dispatch (`_call_dtor` / `_call_free_fn`,
  gen_call.bn:241), always invoked with a single pointer. A user "call through a
  function value" lowers to `OP_CALL_FUNC_VALUE`, already covered by the ABI
  matrix's `funcval-param` cells. So the §3.9 "CALL_HANDLE aggregate by-value"
  concern has no user-level test surface; nothing to add.

### ~~`@func` copy-RefInc symmetry~~ — FIXED 2026-06-03 (binate `d118a3c4` + `76099018`); `@Iface` analogue FIXED 2026-06-03 (binate `97a767e8`)
- **Was**: `@func` / `@Iface` values (`TYP_MANAGED_FUNC_VALUE` /
  `TYP_INTERFACE_VALUE_MANAGED`) had `NeedsDestruction() == false`, so the
  struct copy/dtor generators, `emitStructElemRefcount`, and the
  assignment paths skipped them on COPY, while `@func`/`@Iface` LOCALS
  *were* RefDec'd at scope end — an acquire/release asymmetry.  A
  capturing `@func` stored into a struct field, passed as a parameter, or
  returned dropped its only owning ref; the param/scope-end RefDec then
  freed the capture record while a field/caller still pointed at it, and a
  later invocation was a use-after-free.  Concrete all-modes repro:
  `conformance/534_func_value_param_to_field_capture`
  (`func install(h @Holder, f @func(int) int) { h.F = f }` then invoke
  `h.F`) — SIGSEGV compiled.
- **`@func` half FIXED** (binate `d118a3c4`, `76099018`):
  1. `d118a3c4` — null-safe `emitManagedFuncValueRefDec`: guard the
     closure-dtor fetch (vtable[0] load, `OP_FUNC_VALUE_DTOR`) + RefDec
     behind `data != null`.  The flip below makes struct dtors run on the
     zero-inited `@func` fields a managed struct's `make()` leaves behind
     (`{vtable=null, data=null}`); the unguarded vtable[0] load faulted on
     the null vtable.  Shared IR layer → fixes every backend + the VM.
  2. `76099018` — flip `NeedsDestruction(@func) = true` + acquire (RefInc)
     at every copy site: parameter entry, var-init / short-var
     (isFresh-guarded), the three assignment paths, return,
     `emitStructElemRefcount`, and slice/array element stores.
  `534` now passes in **all 6 default modes** and is un-xfailed; `542`
  adds a return-a-capturing-closure regression.  Unit test
  `TestEmitFuncValueRefDecGuardsNullData` pins the guard shape.
- **VM capture-record leak — FIXED 2026-06-03 (binate `0a0d00af`).**  Under
  the bytecode VM a capturing `@func`'s data slot is a 32-byte
  `DATA_KIND_COMPILED_CLOSURE` rec whose `rec[3]` points at the heap
  closure struct; RefDec'ing the @func value decremented the *rec* and
  (`vt.Dtor == 0`) just freed it, never the struct → the struct and its
  captured managed values leaked.  Fix:
  `ensureHandle` marks an IsClosure callee's vtable dtor slot with a `-1`
  sentinel; `BC_REFDEC_INLINE_FAST` recognizes it, frees the rec and
  RefDec's the closure struct, running its dtor via an iterative frame push
  (flat-stack, no host recursion at `-int-int` depth).  Dtor name plumbed
  ir.Func → VMFunc, resolved by `LookupFunc`.  Conformance `550` pins it
  (captured `@Counter` refcount returns to baseline).  @func is now
  leak-clean on every backend + the VM.
- **`@Iface` analogue — ✅ RESOLVED 2026-06-03 (binate `97a767e8`, "bnc:
  wire managed interface values through the refcount lifecycle"; verified
  still-resolved on main 2026-06-12).**  The symmetric half landed the same
  afternoon this bullet was written (the `@func` fix was 09:05; the iface
  wiring 14:36), so the "still BROKEN" text above was stale.  The full
  recipe is in the tree: `emitManagedIfaceValueRefDec` is null-guarded (the
  iface dtor / `EmitIfaceDtor` vtable[0] load only runs when `data != null`,
  `gen_util_refcount.bn`); `NeedsDestruction(TYP_INTERFACE_VALUE_MANAGED) =
  true` (`types_query.bn`); and the `emitManagedIfaceValueRefInc` acquire arm
  is wired at every copy site via the shared `emitManagedValueCopyRefInc`
  dispatcher (var-init / the assign paths) plus struct-field copy
  (`gen_copy_emit.bn`), array/slice element copy, return (`gen_return.bn`),
  and the borrowed call-arg site (`gen_call.bn`).  Iface PARAMS deliberately
  use the MOVE model (no entry RefInc) — the caller moves a fresh arg
  (`consumeTemp`) or RefIncs a borrowed one at the call site, balanced by the
  param's scope-exit RefDec; an entry RefInc is impossible for a 2-word iface
  passed on transient `vm.SP` (documented at `gen_func.bn`).  Coverage:
  `520_iface_dtor_callee_sole_ref` (callee-sole-ref: `inner-rc` 1→1, proving
  leak-free + no-UAF) is GREEN in all 4 default modes; `383_cross_pkg_iface_
  dtor` is GREEN in `builder-comp` / `-int` / `-int-int` — so the int-int
  multi-package loader bug the bullet warned about is also resolved.  No
  separate `@Iface` VM-leak remains (520's VM-mode rc-balance proves it).
- **Unblocks the REPL interrupt seam (Stage 5 of `plan-repl-embeddable.md`)
  — DONE.**  `vm.SetPoll(poll @func(@VM) int) { vm.Poll = poll }` is the
  param→field `@func` store; with the acquire arms a CAPTURING poll no
  longer UAFs.  Capturing-poll seam tests added and green in every int
  mode: `pkg/binate/vm/vm_poll_test.bn` (`TestCapturingPollFiresViaSetPoll`,
  `TestCapturingPollSuspendsAfterThreshold` — direct `vm.SetPoll`) and
  `pkg/binate/repl/step_test.bn` (`TestStepCapturingPollSuspendsTurn` — the
  end-to-end `s.SetPoll → vm.SetPoll` forward, a capture-driven SUSPEND
  mapping onto `STEP_SUSPENDED`).  The previously-omitted non-capturing
  NOTEs in those files are updated to describe the capturing coverage.

### ~~~~`pkg/std/io`: add `io.EOF` sentinel~~ — LANDED (binate `4fdbd1f9`, plain non-readonly var)~~ — two NON-blocking refinements remain
- **LANDED**: `var EOF @errors.Error` declared in `io.bni` (extern), defined in `impls/.../io/io.bn` as `errors.New("EOF")`; the synthetic `pkg/std/io.__init` constructs it before main; a consumer reads `io.EOF` + `.Error()` correctly. Plain (non-readonly) var, matching Go's `io.EOF`. (Needed the iface-value global-init codegen fix, landed `91ef4fc4`.)
- **Refinement, NOT a blocker — readonly**: making `io.EOF` immutable to consumers (`readonly`) is wanted eventually but does NOT gate the sentinel; it's a plain reassignable var for now (as Go's is). Gated on the readonly-for-managed-values CRITICAL.
- **Refinement — ergonomic detection: RESOLVED 2026-06-08.** `err == io.EOF` is (correctly) NOT the mechanism — `==` on interface values is disallowed. Detection is `io.IsEOF(err)` = `errors.Is(err, io.EOF)` (binate `5282563b`), built on `errors.Is` (`1f87b905`) walking the `Unwrap()` chain via the `same` reference-identity builtin (`e7c1b7fc`). Robust to wrapping; identity (not message) is the test.

### ~~float32 const literal: VM/native loaded the float64 pattern (wrong value)~~ — FIXED 2026-06-05 (binate, plan-cr-p2 Plan 4 step 1)
- **LLVM compile error — FIXED 2026-06-03 (binate `4fd196d0`)**: a float32-typed
  OP_CONST_FLOAT emitted a decimal `float` constant (`fadd float 0.0, 0.1`),
  which LLVM rejects unless exactly representable (`floating point constant
  invalid for type`).  Fixed in `pkg/binate/codegen/emit_instr.bn`: materialize
  the value as a `double` (decimal is valid there) and `fptrunc` to `float`.
- **VM/native value bug — FIXED**: a float32-typed OP_CONST_FLOAT now narrows
  through `common.F64BitsToF32Bits` (round-to-nearest-even f64→f32) in the VM
  (`vm/lower_instr.bn` OP_CONST_FLOAT arm) and both natives' `emitConstFloat`, so
  `bit_cast(int32, C)` observes the true float32 pattern (`0x3DCCCCCD` for `0.1`,
  not `0x9999999A`).
- **The "blocked on a new BUILDER release" diagnosis was WRONG**: the real blocker
  was that `F64BitsToF32Bits` was defined in `common_float.bn` but never declared
  in `common.bni`, so no importer could resolve it.  BUILDER recompiles
  `native/common` from current source when it builds `cmd/bnc`, so a new `.bni`
  export is honored with no BUILDER bump.  Exporting it unblocked the one-liner
  wire-ins.
- **Test**: `conformance/539_float32_const` — now passes on the C/LLVM **and** VM
  lanes (those xfails dropped).  Native lanes still xfail, but ONLY on the
  negative const: native leaves the high-bit-set `bit_cast(int32)` result
  zero-extended (`3184315597`) not sign-extended (`-1110651699`).  That residual
  is sub-word value correctness — folded into **plan-cr-p2-4 #4.1** (the float32
  narrowing itself is correct on native too: the four non-negative lines pass).
- **Discovery**: 2026-06-03 (fixing the LLVM compile error surfaced the value
  bug).  **Severity**: MAJOR (was a silent wrong float32 const on VM/native).

### ~~bnlint typechecks dependency BODIES, not just signatures~~ — FIX LANDED 2026-06-03 (binate `3fcfdf8c`); deployment pending next BUILDER bump
- **Status**: source fix LANDED (binate `3fcfdf8c`, + composition test
  `a079621d`).  Takes effect in hygiene only after BUILDER_VERSION is bumped
  to a snapshot containing it — the bundled bnlint is what hygiene runs.
- **Symptom**: linting package A that imports package B re-typechecks B's
  function *bodies*, not just its exported signatures.  A body-level type
  error in B then surfaces when linting A — false coupling.  Concrete
  trigger: `pkg/binate/vm`'s `_func_handle(rt._Package)` (valid, but newer
  than the BUILDER-bundled bnlint can typecheck) made `pkg/binate/repl` and
  `cmd/bni` *also* fail lint purely because they import vm, forcing the
  `scripts/hygiene/lint.sh` skip to cascade across all three.
- **Root cause**: `cmd/bnlint/main.bn` (`lintPackages`) loops over ALL loaded
  packages (`ldr.Order` — targets AND transitive deps) and calls
  `c.CheckPackage(...)` on each, which runs Pass 1 (`collectDecls`) + Pass 1.5
  (`checkAllImplsSatisfaction`) + Pass 2 (`checkDecls`, body checking).  The
  *lint* loop below only iterates the target `pkgs`, so it already
  distinguishes targets from deps — the body-checking of deps is incidental
  over-reach.  Dependents only ever consume a dep's exported surface, which
  `collectDecls` + `registerPackage` provide; body-checking a dep adds
  nothing for the dependent.
- **Fix (landed)**: `pkg/binate/types/checker.bn` gained `CheckPackageDecls`
  — Pass 1 (`collectDecls`) + `registerPackage`, skipping Pass 1.5/2 —
  sharing `checkPackageImpl(checkBodies)` with `CheckPackage`.
  `cmd/bnlint/main.bn` body-checks (`CheckPackage`) only the lint targets and
  registers transitive deps decls-only (`CheckPackageDecls`), routed by
  `isLintTarget`.  Removes redundant re-checking and stops a dep's body
  errors from leaking into importers.  Once deployed, shrinks the present
  skip from {vm, repl, bni} to {vm}.
- **Severity**: major for the *linter's* robustness (false failures + wasted
  work); linter-only, no effect on generated code.
- **Deployment**: takes effect after a BUILDER_VERSION bump — same release
  that ships the `_Package` typecheck support (Phase B entry above).
- **Tests (landed)**: `pkg/binate/types/checker_test.bn` —
  `TestCheckPackageDeclsSkipsBodies` (decls-only reports no body error; full
  check does), `TestCheckPackageDeclsRegistersScope` (exported surface still
  registered), `TestCheckPackageDeclsDependentResolves` (a dependent resolves
  a decls-only dep AND its body error doesn't leak).  `cmd/bnlint/main_test.bn`
  — `TestIsLintTarget`.

### ~~Cross-package managed-PTR extern var: value-copy (559) + field-write (561)~~ — BOTH RESOLVED 2026-06-04 (native-aa64 stale xfails removed `c4036777`)
- **Resolution (2026-06-04)**: with the native aa64 lane now building
  (after the `551`/`573` `&G`-rvalue fix `9a0f4f9a`), a per-mode
  `--check-xpass` sweep showed **`559` XPASSes on every execution path**
  (LLVM, VM, self-host gen2/gen3, native aa64) and **`561` XPASSes on
  native aa64**.  Both were stale:
  - `559`'s cross-package value-copy crash (the importer lacking the
    imported type's dtor for the scope-end RefDec) was closed by recent
    main work.  `559` is now the ORIGINAL aliasing test — green on ALL 6
    default modes + native aa64, no xfail.  The refcount-BALANCE check
    (which needs an `rt` import, tripping the int-int loader bug) was
    split out into a new directory test `586_cross_pkg_managed_ptr_copy_balance`,
    xfailed only in `builder-comp-int-int` (`66aef4c1`).  (Interim
    history: `32bee84c` strengthened `559` in place + carried an int-int
    xfail; `c4036777` dropped the stale native-aa64 xfails; `66aef4c1`
    then split aliasing vs balance so `559` is xfail-free again.)
  - `561` was already RESOLVED on the default modes 2026-06-03
    (`733d4485`, below); only its native-aa64 xfail lingered, because
    that lane didn't build until `9a0f4f9a`.
  The native-aa64 xfails for BOTH `559` and `561` removed in `c4036777`
  (the strengthened `559` test XPASSes on native aa64).  `559`'s
  `builder-comp-int-int` xfail intentionally remains (rt loader bug).
  (My earlier combined removal attempt `20d7a59d` was abandoned — it
  collided with `32bee84c`'s better, concurrent 559 handling.)  Surfaced
  while landing `550`; not caused by it (559/561 use no closures).
- **~~Symptom A (value-copy crash, 559)~~ — RESOLVED 2026-06-04**: the
  crash (importer lacking the imported type's dtor for the scope-end
  RefDec) was closed by recent main work; see the Resolution note above.
  Tests: `conformance/559_cross_pkg_managed_ptr_copy` (aliasing — green on
  all 6 default modes + native aa64) and
  `conformance/586_cross_pkg_managed_ptr_copy_balance` (refcount balance —
  rc 1->2 on copy, ->1 at the scope-end RefDec; xfailed in
  `builder-comp-int-int` for the orthogonal rt-loader bug).
- **~~Symptom B (field-write no-op, 561)~~ — RESOLVED 2026-06-03 (binate
  `733d4485`)**: `pkg.G.V = v` through an imported managed-ptr var
  silently dropped the store.  Root cause was NOT `genSelectorPtr`'s
  EXPR_IDENT-only branch (its nested-selector branch already recurses and
  obtains the lvalue) but `getSelectorType` returning nil for `pkg.G` — it
  resolved the import alias `pkg` as a (nonexistent) variable, so the
  nested branch couldn't type the inner selector and skipped the
  managed-ptr field-store case.  Fixed with a package-qualified-var case
  in `getSelectorType` (returns the imported var's declared type via
  `lookupImportedGlobalPtr`); `getSelectorType` moved to
  `gen_selector_type.bn` (length cap).  `conformance/561` un-xfailed
  (green all 6 default modes + native aa64 — the stale native-aa64 xfail
  was removed in `c4036777`).  Unit: `TestGetSelectorTypeQualifiedImportedVar`.
- **Discovery**: 2026-06-03, deferral-2 Slice 4 + coverage review.

### ~~Dispatch conflicts (extern registered + Binate body provided) should be a HARD ERROR~~ — ❌ REVERTED, NOT A REAL BUG (landed `e508c841`, reverted `71bf2b2a`, 2026-06-09)
- **Misdiagnosis**: extern + Binate body is a LEGITIMATE pattern — VM trampolines (`pkg/binate/vm.TrampolineScalar`) are intentionally both. The hard-error guard false-positived when the inner VM lowers `cmd/bni` (int-int only), breaking the whole int-int lane. The single-VM 1263/0 check missed it (it lowers the test module, not `cmd/bni`; int-int was dead then). No real bug to fix; do not re-implement without proving an accidental collision actually occurs.
- **What**: today the VM dispatches a `BC_CALL` by name: `LookupFunc`
  → if `>=0`, run the bytecode body; if `-1`, fall through to
  `execExtern` (which consults `vm.Externs`).  Functions registered
  via `RegisterExtern` shadow whatever the .bni declares, but ONLY
  when there's no Binate body — if a user (or a future migration)
  adds a `.bn` body for a name that's also extern-registered, the
  bytecode body silently wins and the extern is dead code.
- **Why a hard error**: the previously-explored "dispatch flip"
  (silently skip lowering when an extern is registered, so the
  extern wins) is the wrong design — the conflict represents
  contradictory definitions of the function, and the right answer
  is to make the user resolve it explicitly, not pick a winner
  silently.
- **Where**: `pkg/binate/vm/lower.bn::LowerModule` (the loader
  pass) is the natural place to detect it — when about to lower
  a function whose qualified name `vm.LookupExtern(...) >= 0`,
  abort with a clear diagnostic naming the offending function
  and both sources.  Same shape as the existing extern-registry
  pre-checks but loud instead of silent.
- **Tests**: unit test pinning the abort path (register an
  extern + lower an IR module with a function under that name
  → assert it errors with a recognizable message).

### ~~Interface alias re-export → spurious `OP_IFACE_UPCAST` (−1 offset) → SIGSEGV~~ — RESOLVED 2026-06-08 (plan-cr2-1 Defect 8, binate `a869e8e7`)
- **Symptom (was)**: a consumer that imports package A — which re-exports `interface I = B.I` from package B — and uses `@A.I` crashed (SIGSEGV) dispatching a method. `conformance/665_transitive_iface_reexport`.
- **Actual root cause** (lldb-traced; the original "degrades to `i8*`" hypothesis was WRONG — the iface value is correctly 2-word throughout): `A.Get()` returns `B.Make()` (typed `@B.I`) coerced to declared return type `@A.I` (the alias). `ifaceValueTypesAgree` (`gen_util.bn`) compared the two iface types by raw `(Pkg, Name)` — `pkg/B` vs `pkg/A` — without resolving the alias chain, so they looked distinct and the coercion emitted a spurious `OP_IFACE_UPCAST`. Its offset is `IfaceParentSlotOffset(B.I, A.I)` = **−1** (the alias is not a PARENT of its target), used directly as a vtable GEP index → `vtable − 8` → the method slot loads the dtor word (NULL) → call through null → SIGSEGV.
- **Fix**: `ifaceValueTypesAgree` canonicalizes both sides through the alias chain (`canonicalIfacePkg`/`canonicalIfaceName`) before comparing; an alias IS the same interface, so no upcast is emitted. `conformance/665` un-xfailed on all 6 runnable modes (LLVM ×3, VM single-int ×2, native aa64) + 2 unit tests.
- **Residual**: `665.xfail.builder-comp-int-int` kept (retagged) — blocked by the SEPARATE pre-existing `-int` multi-package crash below, not this defect. And the −1-as-GEP-offset footgun this exposed is filed as its own CRITICAL (next entry).

### ~~Inferred-type func-value local call mis-lowers to a direct symbol — `var f = <func value>; f(x)` → undefined `main.f` on ALL backends — PRE-EXISTING~~ — ✅ FIXED+LANDED (binate `148650ef`) 2026-06-11
- **Symptom**: binding a func value to a local with an INFERRED type and then calling it fails to compile/link on every backend. `func mk(c int) @func(int) int {…}; var f = mk(5); f(3)` → LLVM `use of undefined value '@bn_main__f'`, VM `vm: extern not found: main.f`, native aa64 `Undefined symbols … main.f`. Same failure for a closure LITERAL bound to an inferred local (`var f = func(x int) int {…}; f(3)`). The EXPLICITLY-typed spelling `var f @func(int) int = mk(5); f(3)` (and `var f @func(int) int = func(x int) int {…}`) WORKS on all backends. Float vs int is irrelevant — int reproduces identically.
- **Root cause** (FOUND): IR-gen's local var-decl handler (`pkg/binate/ir/gen_stmt.bn` `genDecl`) derived the storage-slot type for an INFERRED `var x = <expr>` only from a few literal special-cases (string→@[]readonly char, char, untyped-float→float64, untyped-bool→bool) and otherwise left it at the `TypInt()` default.  For any non-literal initializer (call result, closure literal, composite), the slot stayed int.  Then the func-value-call dispatch in `gen_call.bn` (the Ident-callee branch, ~line 203) gates on `lookupVarType(name).Kind == TYP_FUNC_VALUE/TYP_MANAGED_FUNC_VALUE`; with the slot mis-typed int it falls through to the direct-call path, mangling the callee Ident `f` as the function symbol `main.f`.  The checker is fine — it infers `f` correctly (that's why `f(3)` type-checks); only IR-gen's slot type was wrong.  (Confirmed broader than func-values: an inferred struct/slice var hit `extractvalue operand must be aggregate type` from the same int-slot mis-typing.)
- **Severity**: MAJOR — a normal-looking pattern (`var f = factory(); f(args)`) is unusable; spurious compile/link failure on valid-looking code (fail-LOUD, not a silent miscompile). Easy workaround: annotate the local `@func(...)`/`*func(...)`. Also **corrects a stale claim**: the VM return-value-as-arg entry above asserted `var w = mk(); w(x)` "is fine" — it is NOT for the inferred spelling; that prose elided the required explicit type.
- **Discovery**: 2026-06-11, during the claude-todo #121 closure-float review — the failed verify agent's edge tests all used the inferred spelling and failed for THIS reason (not a #121 bug). Isolated with int/float × literal/returned-value repros across LLVM/VM/native.
- **Fix** (`gen_stmt.bn`, binate `148650ef`): for an inferred decl, after lowering the initializer, set the slot type to the lowered value's own type (`typ = val.Typ`) in the general case, keeping the literal pre-sets (a `typFromLiteral` flag protects the string case — its lowered value is a string constant, not the @[]readonly char slot) and mapping untyped int/float/bool to their defaults.  Now the inferred func-value local is registered as a func value and `f(...)` lowers indirectly; an inferred struct/slice/iface var gets a correctly-sized slot.
- **Test**: `conformance/710_var_infer_func_value` (inferred func value: returned / closure-literal / float closure) + `711_var_infer_aggregate` (inferred struct + slice).  Green on LLVM, VM, native aa64, native x64-darwin.  Full conformance sweeps green on all four (1373 / 1343 / 1337 / 1365, 0 failed) + `ir` unit tests green → no regressions from the shared var-decl path change.
- **FOLLOW-UP — `@func` as the inferred default + bare func-ref `var f = add` — ✅ DONE+LANDED (binate `9eda6028`) 2026-06-11**: implemented as designed — `@func→*func` borrow rule (`types_assignable.bn`; refcount-neutral, identical 2-word layout so no IR-gen op / backend change), `checkFuncLit` no-hint default flipped `*func`→`@func` (a `*func` hint still downgrades to a stack closure), `defaultType(TYP_FUNC)`→`@func`, and IR-gen `@func` synthesis for an inferred bare ref in BOTH `var f = add` (`gen_stmt.bn`) and `f := add` (`gen_short_var.bn`), guarded by a `lookupVarType==nil` scope check (a same-named local var still shadows the function).  Conformance `712`/`713`/`714`; 0-regression on all four full sweeps (lone `577_std_errors` pre-existing: concurrent std/errors readonly-types × #115).  Adversarial review found+fixed the `:=` gap and a shadowing bug, and surfaced the pre-existing recursive-closure-self-reassignment MAJOR bug (own entry above) + `var p,q = a,b` multi-decl-inference unsupported (by-design).  **ORIGINAL plan (historical):** the bare top-level func reference still mis-lowers after this fix (the checker infers it as `TYP_FUNC`, not a func-value — `defaultType(TYP_FUNC)` returns it unchanged — and `gen_stmt.bn` excludes `TYP_FUNC` from the general inference, so the call still resolves to a direct `main.f` symbol).  **Decision: make `@func` the inferred default for func values** (was `*func`), mirroring `@[]T`-by-default: `@func` borrows down to `*func`, so a materialized `var f = func(){…}; foo(f)` then works whether `foo` takes `*func` OR `@func` (today the `*func` default only works for `*func` params — a surprising inline-vs-named asymmetry).  Cost is confined: the inline `foo(func(){…})` case is hint-driven (call-arg supplies `foo`'s param type via `checkExprWithFVHint`, `check_expr.bn:371`) so it still resolves to `*func`/stack when borrowed — the only extra heap closure is the un-hinted `var f = func(){…capture…}` case, opt-out-able with explicit `*func`.  **Scope** (the default is only *felt* in no-hint contexts = inferred vars): invert `checkFuncLit`'s no-hint default (`*func`→`@func`, a `*func` hint still downgrades to stack); `defaultType(TYP_FUNC)`→`@func`; wire IR-gen to emit an `@func` OP_FUNC_VALUE for an inferred bare func-ref.  Then `var f = add` works as `@func` as a uniform consequence.  Verify the full func-value suite + refcount/leak checks (non-capturing `@func` should be ~free {vtable,nil}; confirm).  NOTE adjacent concurrent work `e1dcd14e` ("named func-value types constructible from func references") — reconcile.

### ~~arm32 asm-lib SIBLING: immediate-offset memory encoders silently WRAPPED an out-of-range offset~~ — ✅ RESOLVED 2026-06-11 (`62c2ea79`)
- **✅ RESOLVED 2026-06-11 (`62c2ea79`).** Per the fix-direction bullet below (the recommended assembler behavior): the two arm32 memory encoders now `a.SetError` on an over-range immediate offset instead of silently masking it.  `ldrstrEnc`'s three immediate op-kinds (OP_MEM_IMM/PRE/POST) route through a new `imm12Offset(a, offset)` (errors at magnitude > 4095); `ldrstrHalfEnc`'s immediate path through `imm8SplitOffset(a, offset)` (errors at > 255).  In-range encodings are byte-identical; the public `Ldr`/`Str`/`Ldrh`/… signatures are unchanged (only the private encoders gained the assembler handle), so bnas/asm-parse are unaffected.  Tests: boundary (4095/255 OK) + overflow (4096/256 → `HasError`), positive and negative, across LDR/STR/LDRH/STRH/LDRSB/LDRSH (`arm32_mem_test.bn`).  Repo-wide swept `pkg/binate/asm/arm32` for the memory-offset-wrap pattern — only these two encoders had it (both fixed).  `MOVW`/`MOVT`'s `& 0xffff` is a 16-bit VALUE immediate (load-low-half), a different/plausibly-intentional case, left as-is.  Still latent (no native arm32 backend); a future native arm32 backend will need a materialize path like aa64's `ldrStrSubWordEmit` for its own compiler-generated stack access, on top of this assembler-level error.
- **Symptom / bug (historical)**: the same bug class as the aa64 one above (`4dc78d2e`), but pervasive in the arm32 asm lib (`pkg/binate/asm/arm32/arm32_mem.bn`): `ldrstrEnc` (word/byte LDR/STR/LDRB/STRB) masks the immediate with `offset & 0xfff` (wraps at 4096); `ldrstrHalfEnc` (LDRH/STRH/LDRSB/LDRSH — the ARM32 8-bit *split* imm4H:imm4L form) masks with `(offset>>4)&0xf : offset&0xf` (wraps at **256**). An out-of-range offset is silently truncated mod 4096 (or mod 256) → wrong slot, instead of materializing the address or raising an assembler error.
- **Reachability — LATENT, NOT a compiler miscompile**: there is **no native arm32 backend** (`pkg/binate/native/arm32` does not exist); the arm32 conformance modes (`builder-comp_arm32_*`) compile via the **LLVM** path, which emits its own arm32 machine code and never touches `pkg/binate/asm/arm32`. That library is exercised ONLY by `bnas`/the assembler-parser (`pkg/binate/asm/parse/arm32*.bn`) and its own unit tests. So the bug bites only hand-written arm32 assembly with a large immediate offset assembled through bnas — not any compiler output.
- **Severity**: MINOR while latent (no compiler path; arm32 is not release-gated). Would escalate to MAJOR the moment a native arm32 backend lands — it would inherit this for stack-frame access and reproduce the exact aa64 sub-word miscompile (a function with ≥32 sub-word locals already crosses the 256 halfword limit). File this so the future arm32 backend work picks it up.
- **Fix direction (decision deferred to user)**: for an **assembler**, the correct behavior on an unencodable immediate is to **error** (`a.SetError(...)`), NOT to silently materialize via a scratch register — the programmer wrote a specific instruction and the assembler must not invent ADDs or clobber a register. (This differs from the aa64 *compiler* helper `ldrStrSubWordEmit`, which materializes via X17 — correct for compiler-generated code where X17 is a known scratch, but note that same helper ALSO materializes when reached through bnas, which is arguably wrong for the assembler use; pre-existing, separate question.) Minimum fix: make the arm32 encoders error on overflow; a future native arm32 backend then needs a materialize path like aa64's.
- **Discovery**: 2026-06-11, cross-arch sweep during the adversarial review of the aa64 fix `4dc78d2e`. x64 was also swept and is NOT susceptible (disp32 addressing); aa64's other memory encoders are clean (LDP/STP only save FP/LR at small offsets; floats spill through the overflow-safe integer X-LDR/STR path).

### ~~A float literal narrowed to `float32` is NOT coerced at call-arg / composite-field / return positions~~ — FIXED+LANDED (binate `d37cc7ba`, 2026-06-05)
- **Symptom**: an untyped float literal flowing into a `float32` slot via a
  function **argument** (`f(0.1)` where `f(x float32)`), a **composite-literal
  field** (`S{f: 0.1}`, field `f float32`), or a **return** (`func g() float32 {
  return 0.1 }`) is NOT narrowed double→float32. Arg and field SILENTLY produce
  the wrong value: `bit_cast(int32, x)` reads `0x9999999A` (low 32 bits of
  `double(0.1)`) instead of `0x3DCCCCCD` (`float32(0.1)`). Return emits invalid
  LLVM (`value doesn't match function result type 'float'`) → clang rejects.
  Fails on **every** backend (LLVM, VM, native) — it is a front-end gap, not a
  backend issue. The control cases `var x float32 = 0.1`, `const C float32 = 0.1`,
  and a const-group member all narrow correctly (so the coercion exists; it is
  just not applied at these three positions).
- **Root cause (suspected)**: the front-end inserts the float-narrowing
  `OP_CAST` (→ `fptrunc` / `BC_F64_TO_F32`) only on var-init / typed-const decls
  via `ensureWidth`; the call-arg path (`genExprOrFuncRef` / `coerceArg`),
  composite-field store (`gen_composite.bn` `EmitStore`, no `ensureWidth`), and
  the `return` path do INT narrowing only — an untyped-float literal at a
  `float32` slot keeps its `double` type. Cite: gen_composite.bn:50-59,140;
  gen_expr.bn:37-39 (untyped-float born `double`).
- **Severity**: CRITICAL — passing a float literal to a `float32` parameter or
  initializing a `float32` struct field with one are idiomatic, and the value is
  silently wrong (no diagnostic). Distinct from the DEFERRED §844 (which is the
  *backend* float32-const bug on VM/native); this is a front-end coercion gap
  that hits LLVM too.
- **Test**: `conformance/matrix/const/{call-arg,field,return}/float32/*` (9 cells;
  arg/field = wrong value, return = compile error). To land: see the
  matrix-vs-regressions decision below — likely a few representative
  `regressions/` cells (the bug is position-dependent, not type-dependent).
- **Discovery**: 2026-06-05, P1 const matrix (read-form axis).
- **Fix**: apply the float-width coercion (`ensureWidth`/equivalent) for
  untyped-float literals at call-arg, composite-literal-field, and return
  positions — the same narrowing the var-init path already performs.

### ~~A NAMED distinct *signed sub-word* integer's MIN/-1 divide escapes the divide-fault guard — ✅ RESOLVED in behavior (binate `b43a0057`, named-distinct landing~~ — `widenType` preserves named width+sign); regression test pending (plan-cr2-followup Plan B)
- **Symptom**: `type I8 int8; var a I8 = <I8 MIN>; var b I8 = -1; a / b` does NOT
  panic with "integer overflow" (the ratified signed-MIN/-1 behavior); it
  silently wraps (the int64 divide `-128 / -1 = 128` truncates back to `-128`
  in the I8 result). Divide-by-zero on the same type IS still caught, and
  unsigned named types / named full-width signed types (`type Count int`) are
  fine — only a named *signed sub-word* type at exactly MIN/-1 is affected.
- **Root cause**: IR-gen's `widenType` (gen_binary.bn) collapses a distinct
  NAMED integer type to plain `int` (signed, host width) — the named/sized-ness
  is lost before the `OP_DIV_CHECK` guard sees the result type, so the guard
  uses INT64_MIN instead of the type's true (e.g. int8) MIN. This is a
  pre-existing `widenType` behavior, not a defect in the divide-fault guard
  itself (plain, non-named `int8`/`int16`/`int32` MIN/-1 ARE detected — they
  keep their TYP_INT width through widenType).
- **Discovered**: 2026-06-05 by the adversarial coverage review of the
  divide-fault guard (plan-divide-by-zero.md). The guard itself is correct;
  this is the one width-dependent corner it can't reach because the type info
  is already gone.
- **Proper fix**: make `widenType` preserve a named integer type (or at least
  its underlying width/signedness) for same-named operands, so `I8 / I8` keeps
  width 8. Out of scope for the divide-by-zero work (touches general arithmetic
  typing). A reproducer xfail cell can be added when this is picked up.

### ~~x64 native backend drops a global address (`&G`) used as an RVALUE — `return &G` emits an empty body → SIGBUS~~ — ✅ RESOLVED (binate `0c707e1f`, 2026-06-08)
- **STATUS 2026-06-08 — RESOLVED & LANDED.** Mirrored aa64: added `emitValOperand(a, pkgName, m, ins)` to `x64_regmap.bn` (`isGlobalRef` → `emitGlobalAddr` into a scratch reg, else `getOperand`), threaded `pkgName` into `emitReturn`/`emitSretReturn`/`emitMultiReturnPack`/`emitCompare`/`emitCallIndirect`/`emitCallFuncValue`/`emitCallIfaceMethod`, and routed every x64 value-operand fetch through it — scalar + multi-return return values, comparison operands, store value, call/dispatch args, and the `OP_BIT_CAST` source. `conformance/551,573` flip green on x64-darwin (full suite 1166 passed / 4 pre-existing-unrelated failures, no regressions); aa64/LLVM/VM unchanged (x64-only); new `x64_global_ref_test.bn` pins `emitReturn`/`emitValOperand`/`emitCompare` materializing an `IsGlobalRef` via a RIP-relative LEA.
- **Symptom (historical)**: `conformance/551_addr_of_global_scalar` and `573_addr_of_two_globals_one_instr` crash (SIGBUS, exit 138) on `builder-comp_native_x64_darwin`. Disassembly: `func getG() *int { return &G }` compiles to an EMPTY body (prologue/epilogue only, RAX never set) — `return &G` emits nothing — so the caller dereferences garbage. Green on native aa64 (also Mach-O) and on LLVM/VM, so **NOT Mach-O-specific** despite the surface framing: it is an x64-codegen gap exposed only because x64-darwin is the one runnable x64 mode on the dev host (x64-linux/ELF needs qemu; likely wrong there too at runtime, unverified).
- **Root cause (CONFIRMED)**: the IR emits a global reference as an `IsGlobalRef` pseudo-Instr with ID -1 (no SSA register). x64's value-operand sites fetch operands with the bare `getOperand(a, rm, id)` (`pkg/binate/native/x64/x64_regmap.bn`), which receives only an `id` (no `ins`) and so cannot test `isGlobalRef` — for ID -1 it returns -1 and the site DROPS the operand: `emitReturn` scalar arm (`x64_return.bn` — `getOperand(ins.Args[0].ID)` → RAX never set), `emitBinop`/cmp (`x64_ops.bn` — `getOperand(Args[i].ID)` → `lhs<0||rhs<0` → not emitted, e.g. `&G==&H`), and the call-arg / dispatch-arg sites. aa64 handles this via `emitValOperand(a, pkgName, m, ins)` (`aarch64_regmap.bn`): `if isGlobalRef(ins) { emitGlobalAddr(...) } else getOperand(ins.ID)`, used at all 11 of its value-operand sites. **x64 has NO `emitValOperand`**, and its `emitReturn`/`emitBinop` emitters don't even thread `pkgName` (which `emitGlobalAddr` needs). x64 handles `isGlobalRef` only piecemeal at address-position sites (load/store/refcount/dispatch-data in `x64_emit.bn`/`x64_managed.bn`), never the generic value positions.
- **Severity**: MAJOR — silent wrong-code / crash on an idiomatic, common pattern (`return &global`, `f(&global)`, `&a == &b`) in the x64 native backend. Confined to x64-native (aa64/LLVM/VM are correct). x64-native is still being built out (Phase 3), so this is a completeness gap, not a regression of a once-working path.
- **Tests**: `conformance/551_addr_of_global_scalar` (8 rvalue positions), `573_addr_of_two_globals_one_instr` (multi-return + comparison) — currently UNxfailed (fail on x64-darwin, pass elsewhere).
- **Discovery**: 2026-06-08, plan-cr2-3 follow-up — investigating the x64-darwin-only 551/573 failures per user direction; built bnc, compiled 551 `--target x86_64-darwin`, ran under Rosetta (SIGBUS), disassembled (`getG` empty; only 4 of ~8 `&G/&H` LEAs present).
- **Fix**: mirror aa64 — add `emitValOperand(a, pkgName, m, ins)` to x64 (`isGlobalRef` → `emitGlobalAddr` into a scratch reg, else `getOperand`), thread `pkgName` into `emitReturn`/`emitBinop`/cmp (+ their `x64_dispatch.bn` callers), and route every value-operand fetch (return value, binop lhs/rhs, cmp operands, call/dispatch args) through it. Breadth fix across `x64_{return,ops,call,call_indirect,iface,dispatch,regmap}.bn` + signature changes. Pin with 551/573 flipping green on x64-darwin + a unit test that `emitReturn`/`emitBinop` materialize an `IsGlobalRef` operand.

### ~~Compound shift-assign (`<<=` / `>>=`) bypasses the overshift guard~~ — FIXED + LANDED (binate `fa265629`)
- **Symptom**: `var y uint32 = 1; y <<= 40; println(cast(int, y))` printed `256` (= `1 << (40 & 31)`) on `builder-comp`, not the spec's `0` (count 40 ≥ width 32). The expression form `y = y << 40` correctly gives `0` (fixed at the CRITICAL "shift by ≥ bit width" entry, binate `32fde83d`). Native aa64 gave the correct `0` — so this was an LLVM-path divergence. `uint8 x <<= 9` happened to read `0` (the `1<<9=512` result is narrowed to `uint8` → 0, masking the bug); only a width where the masked count stays in range (`uint32 <<= 40` → `<<8`) exposed it.
- **Root cause (path-parity)**: the overshift guard (`emitGuardedShift`) was applied on the expression-shift path but NOT on the compound-assign path — `emitCompoundBinop` (`pkg/binate/ir/gen_control.bn`) lowered `<<=`/`>>=` without routing through `emitGuardedShift`. Classic Code-Red-2 path-parity gap: a guard added to one of N sibling lowerings (expr-shift) was never mirrored into the others (compound-assign). See `plan-code-red-2.md`.
- **Fix (landed, binate `fa265629`)**: route compound `OP_SHL`/`OP_SHR` through `emitGuardedShift` in `emitCompoundBinop`, mirroring `genBinaryExpr`, keeping the in-range-const fast path. **Companion fix in the same commit**: `emitCompoundBinop` now width-coerces both operands to the lvalue type internally (only the IDENT arm did so before), so a sub-word element/field/deref compound assign no longer keeps an untyped-int count/operand at int64 and emits width-mismatched IR — latent for sub-word non-IDENT compound assigns generally (a `uint32` `a[0] += 5` would have emitted `add i32, i64`), previously unexercised.
- **Severity**: MAJOR — was silent wrong-code, but narrow (a compile-time shift count ≥ width in a compound-assign).  Plan-1 defect (7) in `plan-cr2-1-frontend.md`.
- **Test**: `conformance/659_compound_shift_overshift` — `<<=`/`>>=` overshift across variable / array-elem / slice-elem / nested-array-elem / field / deref lvalues at uint32 & int32, runtime + out-of-range-const counts, self-checking (target-stable 0/1).  Green on builder-comp{,-comp,-comp-comp}, builder-comp-int{,-int}, -comp-comp-int, native aa64.  (Exhaustive `op × lvalue-form` compound-assign coverage — incl. sub-word non-shift arith that the companion width fix also repairs — is the `conformance/matrix/operator` follow-up, §3.3.)
- **Discovery**: 2026-06-07, Code-Red-2 probing of path-parity predictions (the operator pattern).

### ~~Cyclic non-struct named-type definitions (`type A B; type B A`, `type A A`) accepted with no diagnostic → every `Underlying`-walking helper hangs/crashes the compiler~~ — ✅ RESOLVED (landed binate `68a62f8c`, 2026-06-09)
- **Resolution**: `collectTypeDecl` now rejects the cyclic definition (`cyclic type definition involving X`) and breaks the cycle (`Underlying = nil`), so NO `Underlying`-walker — `IsInteger`/`IsFloat`/`IsBool`/`NeedsDestruction`/`AssignableTo`/`comparabilityKind` — ever encounters a cycle. The four operand-comparability predicates additionally carry a bounded named-peel (`peelNamedBounded`) as defense-in-depth; `NeedsDestruction`/`AssignableTo` are protected transitively (the cycle can't exist) rather than independently bounded. See the CR-2 Plan-1 review entry above for coverage. (Original report retained below for context.)
- **Symptom**: a cyclic named-distinct-type definition that is NOT struct-field-mediated — `type A B` + `type B A`, or the self-cycle `type A A` — is accepted by the checker with ZERO errors. The cyclic `TYP_NAMED.Underlying` chain then makes every helper that walks `Underlying` unsafe: `IsInteger`/`IsFloat`/`IsBool`/`NeedsDestruction`/`AssignableTo` recurse unboundedly → SIGSEGV; the new `comparabilityKind` (types_query.bn, loop-based) → infinite hang. Any expression touching such a type (e.g. `var a A; var b A; a == b`, or merely `AssignableTo(A, A)`) takes down the compiler.
- **Root cause**: no cycle detection for non-struct named-type `Underlying` chains. `FindFreshCycles` (check_pending.bn) catches only SIZED-use (struct-field) cycles; const-cycle detection exists too; bare `type A B; type B A` is unguarded.
- **Severity**: MAJOR — compiler DoS (hang/crash) on invalid source that should be rejected with a diagnostic; NOT silent wrong-code. PRE-EXISTING (the old `==` path already SIGSEGV'd here via `AssignableTo`); surfaced while adversarially reviewing the `==`-comparability change (binate `e0f40c06`), which converts the crash into a hang on its one path but neither introduces nor worsens the root defect.
- **Fix direction**: detect named-type underlying cycles at definition time (in `collectTypeDecl`, mirroring struct-field-cycle and const-cycle detection) and emit a `type cycle: A -> B -> A` diagnostic so the cyclic type never reaches IR-gen or the predicates. Defense-in-depth: a shared visited/depth guard for the `Underlying`-walking helpers. Do NOT band-aid `comparabilityKind` alone — that leaves IsInteger/AssignableTo crashing.
- **Test**: add WITH the fix — a checker test for `type A B; type B A` and `type A A` expecting a cycle diagnostic. (Cannot add now as an xfail: the defect is a hang/crash, so the test would hang/crash the suite rather than fail cleanly.)
- **Discovery**: 2026-06-07, adversarial review of the `==`-comparability change.

### ~~Implement the strconv `Parse...` series (ParseInt / ParseUint / ParseBool / ParseFloat)~~ — LANDED (complete)
- **What**: strconv has only the `Format.../Append...`/`Itoa` (number→string)
  direction; add the parse direction.  `ParseFloat` is the correct,
  fully-rounded decimal→double, built over `pkg/std/math/big` (exact
  mantInt*10^exp, round-to-even from the remainder) — the canonical home for
  what `common.ParseFloatLitToBits` approximates.  Once stdlib is
  BUILDER-bundled, the compiler's float-literal converter can route through it
  (or share its core), fixing the round-bit bug above.
- **Plan**: `explorations/plan-strconv-parse.md` (errors via the now-landed
  `@errors.Error`; input `*[]readonly uint8`).
- **Landed (binate)**: full series —
  `ParseBool` + unexported `numError` (`@errors.Error` impl) (`b4bfe843`;
  surfaced + fixed a MAJOR anon-tuple field-GEP codegen bug, `5f4a8eaf`);
  integer core `ParseInt`/`ParseUint`/`Atoi` (`6a91cf5b`); `ParseFloat`
  over `big` — exact, correctly-rounded decimal→binary for f64 and f32
  (`eb4a7aee`); `_` digit separators across all of them (`ea706e43`).
  Verified by Go differentials of the algorithms (integers 9.6M; floats
  2.59M incl. underscores + the over/underflow error kind; 0 divergences),
  exact-bit unit goldens, a Format↔Parse round-trip, and the
  `526_strconv_parse_cross_pkg` cross-package consumer (LLVM/VM/gen2;
  arm32/native via CI — the code is ILP32-safe, all math in uint64).
- **Hex floats — DONE both directions**: `ParseFloat` reads `0x1.8p3`
  (`15b6ce90`, pure-binary path sharing the rational rounding core; Go
  differential ~2M) and `FormatFloat`/`AppendFloat` emit `'x'`/`'X'`
  (`e85eb129`, exact nibble rendering, no big.Nat; Go differential ~4M).
  `_` separators accepted in hex too.
- **No remaining strconv follow-up** for parse/format parity.  (The only Go
  float format not implemented is `'b'` — decimal mantissa, binary exponent —
  which nothing needs yet.)  Once stdlib is BUILDER-bundled, route the
  compiler's float-literal converter through `ParseFloat`'s core to retire the
  round-bit dtoa bug + the duplicate converter (tracked above).

### ~~Native (aa64 + x64) miscompiles a cross-package multi-return whose component is a managed interface value (`@Iface`) — MAJOR, silent wrong-code / crash~~ — ✅ RESOLVED (x64 `47ebdbac` 2026-06-10; aa64 `d206635d` 2026-06-11)
- **✅ RESOLVED — and the original "importer mis-sizes" root cause below was WRONG (empirically refuted 2026-06-11).** The `@errors.Error` tuple component resolves CORRECTLY to a 16-byte `TYP_INTERFACE_VALUE_MANAGED` in the consumer (type resolution is backend-shared — if it mis-sized, LLVM/VM would fail too, but they pass). The REAL cause is a native↔LLVM multi-return **sret-threshold** disagreement: our codegen emits a multi-return as a by-value first-class IR aggregate (no sret attr), which LLVM lowers FIELD-PER-REGISTER (aa64 X0..X7 / D0..D7; x64 RAX,RDX,RCX / XMM0,XMM1), sret'ing only on register-class overflow. The native backends used the 16-byte single-aggregate rule (`SizeOf > 16 → sret`), so a 24-byte `(int64, @errors.Error)` tuple (3 GP words) was sret'd by the native caller while the LLVM-compiled callee register-returned it in X0,X1,X2 — the caller read its never-written sret buffer (garbage err + corrupt scalar). Proven by disassembling the actual `Atoi` (returns X0,X1,X2, no x8/sret) + the fix making 526 pass. x64 was fixed by the field-per-register rework (`47ebdbac`, register-count threshold 3 GP / 2 FP); aa64 by the same register-count rule (`d206635d`, 8 GP / 8 FP — `MultiReturnTupleNeedsSret` now uses per-target `NumGpRetRegs`/`NumFpRetRegs`, not SizeOf). 526 un-xfailed on aa64 (full suite 1323✓); new `conformance/696_cross_pkg_mr_wide_gp` (3- and 4-GP-word) + `TestAapcs64MultiReturnRegisterCountThreshold`.
- **Symptom**: `conformance/526_strconv_parse_cross_pkg` (added with the
  strconv `Parse*` series, `6a91cf5b`) crashes on
  `builder-comp_native_aa64-comp_native_aa64` — empty output.  The
  `Parse*` functions return `(T, @errors.Error)`; the cross-package
  multi-return of a managed-interface-value component is miscompiled:
  the returned `@Iface` comes back as **non-nil garbage** and the scalar
  component is **corrupted**, then the program crashes when the garbage
  `@Iface` is used.  Green on the default C/LLVM and VM modes.
- **Root cause (BISECTED 2026-06-04 with minimal native-aa64 repros)** —
  the break is exactly *cross-package* + *multi-return* + *managed-
  interface-value component*:
  - same-package `(int64, @errors.Error)` multi-return → **passes**
  - cross-package *single* `@errors.Error` return (`errors.New`) → **passes**
  - cross-package `(int, int)` multi-return → **passes**
  - cross-package `(int, @errors.Error)` multi-return → **FAILS**
    (returned `@Iface` non-nil, scalar corrupted)
  Minimal repro: a helper pkg `func Maybe(x int) (int, @errors.Error)`
  returning `x, <nil>`, with `main` doing `n, err = helper.Maybe(7)` — on
  native aa64 `present(err)` reads true (should be false) and `n` is
  wrong.  The importer mis-sizes the `@Iface` tuple component (resolves
  it to a managed pointer / wrong word-count within the return tuple), so
  the caller's sret layout disagrees with the callee's — the native-aa64
  analogue of the LLVM ABI mismatch fixed in `cb8c0f1a` (line ~434), but
  in the MULTI-RETURN-tuple case (the single-`@Iface` case is already
  correct on native aa64, hence `errors.New` passes).
- **Also fails on native x64 (SysV)** — same root cause (the importer's
  tuple-component type resolution for `@Iface` returns is backend-shared,
  not aa64-specific); here it crashes (SIGSEGV) rather than printing
  garbage.  Surfaced 2026-06-10 running the full x64 (Rosetta) lane.  NOT
  funcval-related (the big-multi-return-x64 fix `f0747762` doesn't touch
  it — `526` uses a direct cross-package call).
- **Status**: `526` xfailed on native aa64 (binate `49d03616`) and now on
  both x64 native modes (`builder-comp_native_x64` + `…_x64_darwin`,
  2026-06-10) + this TODO.  **MAJOR (silent wrong-code / crash) — NOT a
  workaround; needs a real fix to the native importer's tuple-component
  type resolution for `@Iface` returns (fixes aa64 AND x64 together).**
  Discovery: 2026-06-04
  full native-aa64 `--check-xpass` lane (first correct end-to-end run; the
  flag had been mis-positioned after the mode).  Not caused by the `550`
  work.

### ~~MAJOR (VM) — compiled iface method returning >16 bytes had no sret path → cross-mode call aborted (2026-06-14)~~ — ✅ RESOLVED (binate `2654d858`)

**✅ RESOLVED (binate `2654d858` "vm/codegen: support >16-byte compiled
iface method returns").**  A compiled interface method whose return is
>16 bytes (e.g. a 4-word `@[]readonly char` managed-slice) used to abort
the interpreted VM (`vm: compiled iface method returns >16 bytes (sret
path unimplemented)`).  `2654d858` added the 17–32-byte path
(`rt._call_shim_quad` → a 4-word VM-stack retbuf) in
`pkg/binate/vm/vm_exec_iface.bn`, covering `errors.Error.Error()`'s
32-byte managed-slice.  `577_std_errors` now passes under
`builder-comp-int`.

- **My note**: I briefly mis-tracked this and landed a *stale*
  `577_std_errors.xfail.builder-comp-int` (binate `6e8415df`) — the fix
  was already on main when I landed; the marker was removed in binate
  `e83d5f42`.  The discovery (an untracked failure on the pre-fix base
  `c94e2f74`) was real, but the fix landed concurrently before mine.
- **Residual (loud-fail, no test hits them — NOT silent)**: a compiled
  iface method returning **>32 bytes**, or taking **>7 arg slots**, still
  `rt.Exit(1)`s with a clear message (`vm_exec_iface.bn`).  Widen with
  another fixed-size shim / a true sret primitive (and wider arg shims)
  if such a method ever appears.

### ~~MAJOR — `panic(msg)` is a NO-OP in the bytecode VM (does not abort; control falls through) — spec Ch.15 (2026-06-12)~~ — ✅ RESOLVED 2026-06-14 (binate `a4946ebe`)

**✅ RESOLVED 2026-06-14 (binate `a4946ebe`).** `panic(args...)` now lowers in
IR-gen to print `"panic: " + args + "\n"` then a `bootstrap.Exit(1)` call +
`unreachable` terminator (NOT `OP_PANIC`), so it prints its message AND aborts on
every backend: LLVM, the VM (the `BC_NOP` no-op is gone — the dedicated
`OP_PANIC` op was removed), and native.  The message-to-stdout stream is
consistent with the other traps.  `conformance/767_panic_message` fires
`panic(...)` and pins the message (abort — post-panic line not reached — verified
on all three backends).  Original report below.

Found while grounding spec Ch.15 (Built-in Operations). `panic(msg)` lowers to
`OP_PANIC` (a block terminator) which the compiled backends turn into an abort,
but the bytecode VM lowers it to **`BC_NOP`**:
`vm/lower_instr.bn:429-433` — `if instr.Op == ir.OP_PANIC { bc.Op = BC_NOP //
TODO: implement panic; bc.Dst = -1; return bc }`. Known-unimplemented (explicit
TODO), but untracked here.

- **Effect**: under the VM, `panic(msg)` does NOT abort and does NOT emit the
  message — it's a no-op. Worse, `OP_PANIC` is a terminator, so the message
  operand is computed then discarded and **control falls through** into whatever
  bytecode follows the (now non-terminated) panic block — undefined continuation,
  not a clean stop.
- **Why it matters**: `panic` is part of the closed set of defined
  non-recoverable behaviors that the dual-mode contract requires to be identical
  across compiled and interpreted execution (spec §19). A `panic("unreachable")`
  guard that aborts compiled silently continues under the VM.
- **Compiled mode ALSO discards the message** (surfaced grounding spec Ch.17):
  `OP_PANIC` lowers to a bare `rt.Exit(1)` + `unreachable`
  (`codegen/emit_instr.bn:217-221`) — the `msg` operand is evaluated (`gen_call.bn:124`)
  then thrown away, never printed. So even in the "working" compiled mode,
  `panic("reason")` aborts (exit 1) but shows nothing. Contrast the runtime traps
  (BoundsFail/DivFail/MakeManagedSlice-negative), which DO print a `runtime error:
  …` diagnostic. So the message-printing is unimplemented on BOTH paths, not just
  the VM.
- **Output stream**: every realized trap diagnostic currently goes to **stdout**
  (via `print`/`println` → `bootstrap.Write(STDOUT=1, …)`, `gen_print.bn:195`), not
  stderr. The "print to stderr" in the fix below would be INCONSISTENT with the
  other five panics — pin the stream (current reality = stdout for all).
- **No conformance coverage** of a firing `panic("msg")` + message (the one
  panic-terminator test, `289`, only exercises the non-panic return path).
- **Fix**: (1) make `panic(msg)` print its message + exit non-zero in BOTH
  compiled mode (emit the message before `rt.Exit(1)`) and the VM (implement
  `BC_PANIC`, replacing the `BC_NOP`); keep the stream consistent with the other
  traps (stdout today). (2) Add a conformance test that fires `panic("x")` with a
  `.error` regex. Referenced as `builtin.panic.vm-noop` from spec
  `15-builtin-operations.md` and §17.5 of `17-program-initialization-and-execution.md`.

### ~~Recursive closure via self-reassignment returns the snapshot value — ✅ NOT A BUG (documented capture-by-value); optional future ergonomic~~ — 2026-06-11
- **What was observed**: `var g @func(int) int = func(x int) int { return 0 }; g = func(x int) int { if x <= 1 { return 1 }; return x * g(x-1) }; println(g(5))` → **0**, not 120.  Initially mis-filed as a MAJOR bug during the #123 review; on checking the design it is **intended behavior**.
- **Resolution — this is the documented, intentional semantics.** `plan-function-values-phase-2.md` §"Capture semantics: always by value": *"Captured locals are snapshot at the moment of the literal's evaluation … Writes to the original outside the closure, after the closure is constructed, are not visible."*  Its own example `x := 5; f := func() int { return x }; x = 10; f()` → `5` is the same mechanism (verified: a scalar capture-then-reassign returns the snapshot; a captured **pointer**'s pointee mutation IS visible — capture a pointer for shared mutable state).  And §"recursion": recursive lambdas are **"Not supported"** explicitly — *"the body would close over the nil/old value the var has at literal-evaluation time, not the closure itself."*  The recursive case captures the `return 0` stub.
- **Idiomatic recursion**: a NAMED top-level function (self-reference is a static symbol, not a capture) — works; or the documented explicit self-passing form `var step Step = func(self *Step, x int) int { … (*self)(self, x-1) }`.
- **Only-residual (optional, NOT a bug)**: the recursive-closure form *silently* yields the snapshot rather than a diagnostic.  The design deferred a diagnostic ("cheaper to add later than to take away"); a future ergonomic could warn when a closure captures a var that is `nil`/uninitialized at capture and called within the body.  No action unless the user wants the diagnostic.

### ~~`build-bnc.sh --debug` (-O0 -g) emits invalid LLVM~~ — ✅ RESOLVED 2026-06-11 (`e0b3cebb`)
- **Symptom**: `scripts/build-bnc.sh -o <path> --debug` failed compiling `pkg__binate__native.ll` with `error: expected instruction opcode` on a bare `  , !dbg !DILocation(...)` line (a `!dbg` attached to no instruction).  Once that fatal was past, 18 modules / 2031 sret calls hit the warning `inlinable function call in a function with debug info must have a !dbg location`, so LLVM discarded those modules' debug info ("ignoring invalid debug info") — a silently mostly-undebuggable build.  Release (`-O2`) was fine; `--debug`-only.
- **Root cause (confirmed)**: the debug-info emitter attached `!dbg` to only the LAST line `emitInstr` produced for each IR instruction (`addDbgToLastLine`).  That broke on (a) ops emitting NO instruction text — an empty struct/array `OP_ALLOC` (no fields to zero) leaves only `emitInstr`'s `  ` indent, so the `!dbg` dangled on it (the fatal; `pkg/binate/native` allocates an empty `aarch64Backend`); and (b) ops emitting MULTIPLE lines — an sret `call void @f(...)` emits the call + the result `load`, so the call line got no `!dbg`.
- **Fix**: the main emit loop now annotates EVERY instruction line `emitInstr` produced (`[lenBefore, out.Len)`) via the previously-unwired per-line `addDbgAnnotations` helper (skips labels + empty/indent-only lines); `addDbgToLastLine` stays for the single-line hoisted-alloca-decl path.  A full `--debug` build now compiles with 0 errors / 0 "inlinable call" warnings / 0 "ignoring invalid debug info" (was 1 / 2031 / 18), and the resulting bnc compiles+runs correctly.
- **Test**: `pkg/binate/codegen/emit_debug_test.bn` — `TestEmitDebugEmptyStructAllocaNoDangling` (no dangling `!dbg` line) + `TestEmitDebugSretCallHasDbg` (sret call line carries `!dbg`).  The `--debug` whole-tree build is a manual integration check (not wired into CI).
- **Follow-up — ✅ DONE (`27d5e185`)**: `addDbgAnnotations`'s instruction-line test was `first char == ' '`; hardened to require a non-space char (`lineIsInstr`) so a hypothetical whitespace-only line WITH a trailing newline (`  \n`) can't be mis-annotated into a dangling `!dbg` — correct-by-construction, not just for the current op set.  `TestAddDbgAnnotationsSkipsNonInstrLines` pins it.

### ~~native-aa64 corrupts SIGNED sub-word (int8/int16) values under register pressure → wrong shift results~~ — ✅ RESOLVED 2026-06-11 (binate `4dc78d2e`)
- **✅ RESOLVED 2026-06-11 (binate `4dc78d2e`).** Root cause was NOT the SSA spill/reload (that path is a 64-bit-faithful `Str`/`Ldr` round-trip) but the **asm-lib sign-extending sub-word LOAD encoders**: `Ldrsb`/`Ldrsh`/`Ldrsw` (`pkg/binate/asm/aarch64/aarch64_branch.bn`) masked the stack offset with `& 0xfff` and emitted directly, with NO overflow handling for an offset past the scaled-imm12 range (4095 byte / 8190 halfword / 16380 word). So a signed sub-word local whose slot sits past that range loaded from `offset mod 4096*scale` — a different, usually-live slot — returning garbage. The unsigned/full-word loads (`Ldrb`/`Ldrh`/`Ldr`) and ALL stores already materialize the address into X17 (`ldrStrSubWordEmit`/`emitLdrStr`); only the three signed loads were missing it — exactly why unsigned sub-word cells passed (they zero-extend through the overflow-safe `Ldrb`/`Ldrh`) and signed ones failed. Fix: route `Ldrsb`/`Ldrsh`/`Ldrsw` through `ldrStrSubWordEmit` (passing the opc incl. the W/X-form bit, and the access scale) so they share the X17 materialization; non-overflow encoding is byte-identical, misaligned offsets now materialize too. Verified: `shift-typepair` 20/20 cells green on the real `builder-comp_native_aa64-comp_native_aa64` runner; asm-aarch64 unit 98/0, native-aarch64 unit 125/0. Added `TestLdrs{b,h,w}ImmOverflowMaterializes` pinning the materialized tail; un-xfailed all 4 cells. **This completes an earlier incomplete sweep**: `ldrStrSubWordEmit` + the X17 materialization were added by Cluster A `1612221` (see claude-todo-done.md), which fixed `Str`/`Ldr`/`Strb`/`Strh`/`Ldrb`/`Ldrh` but MISSED `Ldrsb`/`Ldrsh`/`Ldrsw` — the 3 signed loads live in `aarch64_branch.bn`, not `aarch64_arith.bn` with the other six, so the original by-file sweep didn't reach them (the "enumerate sweep sites repo-wide" lesson). Test-coverage follow-up `446c68bd`: a both-sides boundary pair (4095 inline / 4096 materialize) pins the `>4095` threshold against off-by-one, plus a W-form (sf=false) overflow test (the native backend only emits the X-form). **Cross-arch SIBLING raised** (see the arm32 entry below): the arm32 asm lib has the same offset-wrap class pervasively but latent (no native arm32 backend; bnas-only).
- **Symptom (historical)**: the `conformance/matrix/shift-typepair/sh{l,r}/int{8,16}` cells (value type int8/int16, sweeping all 10 count types) failed ONLY on `builder-comp_native_aa64-comp_native_aa64`. Each cell's ~60 sub-word locals grow the frame past the imm12 range; output was correct for the first ~9 self-checks, then wrong once a slot offset crossed 4096 — POSITIONAL, not count-type-specific (`shl/int8`: checks 1-9 = 1, then 10-20 = 0). Isolated `int8<<int8` etc. passed (small frame); `uint8`/`uint16` and `int32`/`int64`/`int`/`uint` cells passed. host-int / VM / gen2 always passed (the bug is in the aa64 instruction encoder, not the shift logic or shared IR).
- **Severity**: MAJOR — silent wrong values for signed sub-word arithmetic in any large-enough aa64 frame (not just shifts; any int8/int16 local past the imm12 range). Was NOT a bnc-0.0.8 release blocker: the release bundle's bnc is built by the BUILDER (LLVM backend), not native-aa64.
- **Discovery**: 2026-06-10, bnc-0.0.8 release-gate recheck — the new shift-typepair matrix (binate `93d6ecd4`) exposed it (its many sub-word locals grow the frame past 4096 as prior shift tests didn't). Root-caused + fixed 2026-06-11 by disassembling the failing `main` (byte loads capped at 0xf88 while 64-bit spills reached 0x2468 → 12-bit offset wrap).

### ~~[CR-2 Plan-1 review] `readonly`-wrapped >16-byte aggregate parameter: by-value signature vs by-pointer call site → garbage / SIGSEGV~~ — ✅ RESOLVED 2026-06-09 (LLVM+IR `79ebfa98`, native `c6fe0914`)
- **NATIVE half DONE (binate `c6fe0914`, Plan 3):** a `peelTransparent` helper (alias+readonly+named to a fixpoint, mirroring `Type.IsByvalParam`) now backs the native classifiers `IsAggregateTyp`, `IsFloatScalarTyp`, AND `StructTypeOf` (which all peeled only `UnwrapNamed`/TYP_NAMED). Un-xfails `conformance/matrix/readonly/pass-arg/value-struct-large` on native aa64 + x64 + x64-darwin; `common_test.bn` pins the peel. The `StructTypeOf` peel also fixed a pre-existing sibling — `readonly` struct-pointer field reads (`matrix/globals/readonly/struct`), un-xfailed on native aa64 + x64 (it was loud-failing unmarked on x64-darwin). Full native aa64 sweep 1288/0. The VM keeps its xfails (its own aggregate classifier — separate fix, still tracked by the VM xfail markers).
- **STATUS 2026-06-09**: the LLVM + IR-gen halves are FIXED. The two byte-identical `isByvalParam` copies (`codegen/emit_util.bn` for the param signature, `ir/gen_func.bn` for the `IsByvalParamRef` flag that drives the callee param-copy) had to agree; they were unified into one `Type.IsByvalParam()` in `pkg/types` (`scope.bn`) — which peels alias/readonly/named — and IR-gen + codegen (11 call sites) route through it, so the "two predicates must agree" hazard can't recur. Tests: `types_query_test.bn TestIsByvalParamPeelsWrappers` + conformance `matrix/readonly/pass-arg/value-struct-large` (green on every LLVM mode; xfailed on VM = shared-IR readonly field-read defect, this list; and on native — see remainder). **REMAINING (Plan 3, native backend):** `common.IsAggregateTyp` (`pkg/binate/native/common/common.bn:345`) peels only `UnwrapNamed` (TYP_NAMED), not readonly/alias → a `readonly Big` >16B param is passed by value on aa64 + x64 (both natives print garbage, confirmed 2026-06-09). Fix: peel readonly+alias there too (mirror `Type.IsByvalParam`). The new conformance cell xfails the native modes for this until it lands.
- **Symptom**: a param typed `readonly Big` (24-byte struct) / `readonly [4]int` / `readonly @[]int` is lowered by-value in the callee signature but passed by-pointer at the call site → silent garbage (exit 0) or SIGSEGV. Probe: `func first(b readonly Big) int { return b.a }; first(x)` with `x.a=123` → garbage `6102984704` (expected 123). Controls: plain `Big`/`@[]int`, below-16B readonly struct, and alias-typed slices all work — only readonly-wrapped >16B aggregates diverge.
- **Root cause**: `isByvalParam` (`pkg/binate/codegen/emit_util.bn:290`, and the copy at `gen_func.bn:26`) tests `t.Kind` against the aggregate set BEFORE peeling `readonly`, so a `TYP_READONLY` param returns false and never reaches the (peel-aware) `SizeOf() > 16` gate; `SizeOf`/`llvmType` DO peel → signature and gate disagree. Native `common.IsAggregateTyp` (`pkg/binate/native/common/common.bn:345`) peels only `UnwrapNamed` (TYP_NAMED — not readonly/alias).
- **Distinctness**: NEW — not the already-filed byval entry (that is the INDIRECT iface/func-value call; the DIRECT call is confirmed broken here only for readonly-wrapped aggregates). Same wrapper-transparency class as Defect 1/2, in the calling-convention layer the fixes never touched.
- **Severity**: CRITICAL — silent miscompile + SIGSEGV on both LLVM and native. **Owner: Plan-2/3 (codegen `emit_util.bn`/`gen_func.bn` + native `common.bn`).** Fix: peel transparent wrappers (readonly+named+alias to fixpoint) at the top of both `isByvalParam` copies and the native aggregate classifiers before the Kind test. Add conformance (readonly >16B struct/array/`@[]T` DIRECT call + plain/below-threshold/alias controls) on LLVM+native+VM; xfail until fixed. No existing test passes a readonly aggregate >16B as an argument.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed).

### ~~[CR-2 Plan-1 review] `[N][M]Struct` (value struct) field write `a[i][j].field = …` stores NOWHERE (silent data loss) + read → 0~~ — ✅ RESOLVED (landed binate `c2b9bbe8`, 2026-06-09)
- **Symptom**: `var a [1][1]B; a[0][0].v = 9; println(a[0][0].v)` → `0`; a following whole-struct read `var w = a[0][0]; println(w.v)` → `0` — so the WRITE went nowhere, not just the read. IR shows the value computed (`add i64 9, 0`) but NO `store`, and the read folds to const 0. Controls: single-level `s[0].v`, nested-array scalar `m[1][1]`, and whole-struct read `var w = a[1][1]` all work — isolating it to {nested-array base `a[i][j]`} × {struct-field selector}, on read AND write.
- **Relationship to filed**: the only tracked/xfailed test (`conformance/regressions/nested-array-managed-ptr-field`) covers ONLY `[N][M]@Box` (managed pointer), characterized as a read-path bug. The VALUE-struct variant and the write-stores-nowhere aspect are neither tested nor characterized → materially BROADER than the filed item; broaden that entry.
- **Severity**: CRITICAL — silent data loss (write to nowhere). **Owner: Plan-1 (`pkg/binate/ir` — root the field GEP at the in-place element pointer the inner index produces, for both gen_assign field-write lvalue and gen_selector index-selector read).** Add `[N][M]ValueStruct` field read+write conformance coverage (xfail per failing mode).
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed).

### ~~[CR-2 Plan-1 review] whole-inner-array composite-literal store via slice/pointer element or struct field-init stores the alloca POINTER (managed-inner variant CORRUPTS)~~ — ✅ RESOLVED (landed binate `cac7a2e0`, 2026-06-09)
- **Symptom**: `s[i] = [M]T{...}` (raw `*[][M]T` or managed `@[][M]T`) and `S{ [M]T{...} }` struct field-init store the inner alloca pointer instead of the array value → garbage (exit 0); the managed-inner variant `@[][N]@[]int` CORRUPTS (`index out of bounds: 0 (len 0)`, exit 1 — the misplaced pointer is read as a managed-slice header). Probe: raw-slice `s[0] = [2]int{5,6}; s[0][0]+s[0][1]` → `6102280160` (expected 11).
- **Root cause**: three sibling store arms keep the struct-only guard `... .Kind == TYP_STRUCT` instead of `isAggregateAllocToLoad`: `pkg/binate/ir/gen_composite.bn:97` (struct field init), `gen_control.bn:288` (TYP_POINTER/raw-slice arm), `gen_control.bn:324-330` (managed/generic slice-set arm). Defect 6 (`7583b669`) migrated only `genArrayLit` (gen_composite.bn:155) and `emitArrayElemStore` (gen_control.bn:23).
- **Severity**: MAJOR (silent wrong-code, exit 0; managed-inner corrupts). **Owner: Plan-1 (`pkg/binate/ir/gen_composite.bn`, `gen_control.bn`).** Fix: replace all three struct-only guards with `isAggregateAllocToLoad(rhs, <slotElem/elemTyp/fields[i].Type>)`. Add conformance: raw-slice, managed-slice (plain-int AND managed-inner), struct-field composite-lit array stores; xfail any not fixed immediately. None has a tracking xfail today.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed).

### ~~Global address (`&G`) as an rvalue dropped at `OP_CAST` (all 3 non-VM backends) + iface-method ARG (aa64 + LLVM) — `emitValOperand`/`emitValRef` per-op whack-a-mole~~ — ✅ RESOLVED 2026-06-09 (LLVM `d086ccac`, native `4a9775cf`)
- **STATUS 2026-06-09**: RESOLVED in two halves. LLVM (CR-2 Plan-2 Round-2, binate `d086ccac`): `emit_ops.bn` `emitCast` precomputes the source via `emitValRef(buf.New(), Args[0])` (mirroring `emitBitCast`) and writes it at every arm; `emit_iface_call.bn:156` switched `emitRef`→`emitValRef`; conformance `669_cast_global_addr` + `670_iface_method_global_addr` added (green on all LLVM modes + VM, xfailed on the native modes pending Plan 3). NATIVE (Plan 3, binate `4a9775cf`): Facet 1 native OP_CAST (`x64_dispatch.bn`, `aarch64_dispatch.bn`) and Facet 2 native aa64 iface-arg (`aarch64_iface.bn` `emitCallIfaceMethod`, now threading `pkgName`) both route the source/arg through `emitValOperand`; only the scalar branch needs it (a global ref is always a scalar pointer). Un-xfailed `669` on all 3 native modes and `670_iface_method` on aa64; both green on native aa64 + x64-darwin (`x64`-elf not host-runnable, but the OP_CAST fix is in shared `x64_dispatch.bn`, verified via x64-darwin). Native unit pins assert the OP_CAST source materializes (aa64 ADRP+ADD+MOV; x64 RIP-LEA). The architectural root (per-op whack-a-mole vs the VM's op-agnostic materialization pass) below still stands as the durable-fix recommendation (FILED follow-up: make `emitValOperand`/`emitValRef` the SOLE value-operand fetch).
- **Context**: binate `0c707e1f` (x64) + the earlier aa64 `emitValOperand` work fixed `&G`-as-rvalue at the *enumerated* value-operand sites (return value, compare operands, store value, call/dispatch args, `OP_BIT_CAST`). An adversarial multi-agent review of that work found the enumeration was INCOMPLETE — two more value positions still drop the `IsGlobalRef` pseudo (ID -1) via bare `getOperand` / `emitRef`.
- **Facet 1 — `OP_CAST` source: silent wrong-code (native) + compile error (LLVM), REPRODUCED on all 4 host-runnable modes**: `var addr int = cast(int, &G)` → `builder-comp` (LLVM) `error: use of undefined value '%v-1'` (clang fails on `ptrtoint i8* %v-1`); on `builder-comp_native_x64_darwin` AND native aa64 the cast drops the address so the `bit_cast`-back round-trip prints the UNCHANGED global (`10`, not `11`) — silent corruption (a dropped cast leaves a garbage register that gets reused). VM is CORRECT. Sites: `pkg/binate/native/x64/x64_dispatch.bn:388` (OP_CAST arm, bare `getOperand(ins.Args[0].ID)`), `pkg/binate/native/aarch64/aarch64_dispatch.bn:411` (same), `pkg/binate/codegen/emit_ops.bn` `emitCast` (uses `emitRef`, not the `emitValRef` precompute-srcRef treatment `emitBitCast` already has). `bit_cast(int,&G)` was fixed (conformance 551); the value-preserving `cast(int,&G)` sibling was MISSED and is UNCOVERED.
- **Facet 2 — iface-method ARG: silent wrong-code (aa64) + compile error (LLVM)**: `i.m(&G)` (a global address passed to an interface method) — aa64 `emitCallIfaceMethod` (`pkg/binate/native/aarch64/aarch64_iface.bn`) never took `pkgName` and fetches its scalar args via bare `getOperand` (the x64 sibling WAS routed through `emitValOperand` in `0c707e1f`; aa64 was a pre-existing gap); LLVM `pkg/binate/codegen/emit_iface_call.bn:156` uses `emitRef(out, argInstr.ID)` not `emitValRef`. NO conformance test passes `&global` to an iface method on ANY backend.
- **ROOT CAUSE / why this recurs (the architectural finding)**: the VM is correct for ALL these sites FOR FREE because `pkg/binate/vm/lower_func.bn` (~276-291) does an OP-AGNOSTIC pre-pass — for every instruction it materializes any `IsGlobalRef` arg into a fresh register (`BC_LOAD_IMM`) and rewrites `Args[k].ID` BEFORE the op is lowered. The native + LLVM backends handle `IsGlobalRef` PER-OP (whack-a-mole), so each value-operand site must be individually converted and the missed ones are exactly these defects. The DURABLE fix is to centralize: make `emitValOperand` / `emitValRef` the SOLE value-operand fetch (audit every site so none can forget), or mirror the VM's up-front materialization pass. Also-noted latent asymmetry (true negative today): `OP_FUNC_VALUE` data slot (`x64_dispatch.bn:166`, aa64 analog) is NOT global-ref-aware while its `OP_IFACE_VALUE` sibling IS — one IR change from becoming live.
- **Severity**: CRITICAL — silent wrong-code / corruption on idiomatic, type-valid programs (`cast(<int>, &global)`, `iface.method(&global)`) across the native backends, plus hard compile errors on the primary LLVM backend. Confined to the global-address-as-value feature; reproduced on the dev-host-runnable modes.
- **Tests to add WITH the fix**: a conformance cell `cast(int, &G)` round-trip (FAILS pre-fix on LLVM/aa64/x64-darwin, PASSES on VM); an `i.m(&G)` iface-method-arg cell; unit pins for the OP_CAST + iface-arg `emitValOperand`/`emitValRef`.
- **Discovery**: 2026-06-08, adversarial multi-agent review of plan-cr2-3 work (`cc2ddcc4` / `0c707e1f`); OP_CAST empirically reproduced on all four host-runnable modes (independently re-confirmed after the review). Per user decision (2026-06-08) this is FILED, not yet fixed; native parts (x64/aa64 OP_CAST + aa64 iface-arg) are Plan-3, the LLVM parts (`emit_ops.bn` emitCast, `emit_iface_call.bn:156`) are codegen/Plan-2.

### ~~Iface upcast lowerings use `IfaceParentSlotOffset`'s −1 sentinel directly as a vtable GEP/byte offset — silent vtable corruption~~ — ✅ RESOLVED 2026-06-09 (binate `ca155319`)
- **STATUS 2026-06-09**: FIXED as the coordinated 3-plan set the "two sub-parts" below describe. (b) IR (`gen_iface_extends.bn`): `IfaceParentSlotOffset` returns **0** for the same canonical interface (extends the `any` special-case) — the source vtable IS the target's, so no slot adjustment; this removes the only emittable −1 (the zero-method `@X→*X` decay). (a) the LOWERINGS — `emit_iface_upcast.bn` (LLVM) and `aarch64/x64_dispatch.bn` (native) — now hard-`panic` on a negative offset instead of feeding it to a GEP / silently skipping it (`if byteOff > 0`). The IR fix lands WITH the asserts (without it they'd fire on the zero-method decay's −1). Verified the asserts never false-fire: the full iface conformance suite (140 cells) is green on builder-comp, builder-comp-comp, builder-comp-int, native aa64, and native x64-darwin. Tests: `gen_iface_extends_test.bn TestIfaceParentSlotOffsetSameInterfaceIsZero` + conformance `685_iface_same_interface_decay` (`@E→*E`, green on every mode; with the asserts a regression to −1 is a compile error). The stale "leaves offset at 0" comment on `TestIfaceParentSlotOffsetNotAParent` was corrected.
- **Symptom**: `OP_IFACE_UPCAST` lowering computes the target's vtable slot via `IfaceParentSlotOffset(src, target)`, which returns **−1** when `target` is not a (transitive) PARENT of `src`. The LLVM lowering (`pkg/binate/codegen/emit_iface_upcast.bn:34`) and the native aarch64/x64 dispatch lowerings feed that result DIRECTLY into a `getelementptr` / byte-offset with **no −1 guard** — so a −1 walks the vtable pointer one slot BEFORE its base, and the dispatched method slot reads the wrong word (e.g. the dtor) → call through garbage/null → SIGSEGV or silent wrong dispatch. The unit-test comment at `gen_iface_extends_test.bn:72-75` CLAIMS the caller "leaves offset at 0" for the −1 case, but no caller actually clamps it — the comment is aspirational, the code is a footgun.
- **How exposed**: Defect 8 (above) hit exactly this — a spurious alias upcast produced a −1 offset → `vtable − 8` → crash. The Defect-8 fix stops the ALIAS path from emitting that upcast, but the lowering still trusts the offset blindly, so any OTHER non-parent upcast (or the same-interface case below) corrupts the same way.
- **Severity**: CRITICAL — silent vtable-base corruption → memory-unsafe crash / wrong method dispatch. Currently latent (no remaining known emitter of a −1-offset upcast), but a sharp edge: a future mis-emitted upcast becomes a memory-safety bug instead of a loud error.
- **Two sub-parts**: (a) the LOWERINGS (`pkg/binate/codegen/emit_iface_upcast.bn`, `pkg/binate/native/{aarch64,x64}/*_dispatch.bn` — Plan-2/Plan-3 territory) should clamp/assert: a −1 must be a hard error (or 0 only where provably same-interface), never a silent GEP index. (b) `IfaceParentSlotOffset` itself (`pkg/binate/ir/gen_iface_extends.bn` — Plan-1) returns −1 for the SAME canonical interface `(X, X)` rather than 0 — the `any` case is special-cased to 0 but same-interface is not, so a managed↔raw decay of the same interface (which still routes through the upcast path via the Kind check in `ifaceValueTypesAgree`) would also corrupt. Reachability of that decay-through-upcast not confirmed.
- **Discovery**: 2026-06-08, while root-causing Defect 8 (disassembly of `Get`'s `subs x1, x8, #0x8`). User opted to file (not fix) for now; decide separately.

### ~~Cross-package struct-name mangler collision (`reflect.Package` vs a module's own `type Package`) broke the `bni` build~~ — FIXED 2026-06-08 (`7ebafc51` mangler fix + `aa8d6828` Defect-2 re-land)
- **STATUS 2026-06-08 — FIXED & LANDED.** Fixed at its source: the synthetic `_Package()` descriptor's `reflect.Package` result type now carries its path-qualified name `pkg/builtins/reflect.Package` (`7ebafc51`, `pkg/binate/ir/gen_import.bn` `qualifiedReflectPackageType`), so the mangler folds it to the reflect package's own symbol and it can never collide with the compiling module's structs. Defect 2 (the `m.Globals` scan + `TYP_NAMED`/`TYP_ARRAY` discovery arms) was then re-landed (`aa8d6828`) — safe now — with `conformance/657_cross_pkg_struct_global` and the `globals/noinit/named-struct` cell. Verified on `builder-comp` + **`builder-comp-int`** (the VM build that broke). History: the original Defect-2 commit `b0402d04` was REVERTED (`1ae18289`) to un-break main, then re-landed on top of the mangler fix; Defect 1 (`f2ebaca1`, global static-zero NAMED-peel) was never reverted (independent, correct).
- **FOLLOW-UPS — ✅ BOTH DONE 2026-06-10 (Option B).** (a) **Class-level fix
  (Option B) — LANDED `59771b8d`..`f5b3b387` + identity fix `1e37a637`.** Struct
  types now carry their fully-qualified name at definition (checker qualifies via
  `currentPkgPath`/`QualifyName`; IR registers qualified; lookups qualify-if-bare),
  killing the cross-package collision class at the root; `Identical` distinguishes
  cross-pkg same-name structs (was still comparing the bare TYP_NAMED wrapper); the
  latent `genMethodValue` cross-package value-receiver leak is fixed too.
  Byte-identical, green across all modes + self-host. (b) **Dedup-mismatch guard —
  LANDED `15f1fae2`.** `addStructDef` now aborts as a codegen precondition when a
  mangled-name match has a disagreeing field layout (`structShapesMatch`), instead
  of silently keeping the first. See `plan-cr2-optionb.md`.
- **Symptom**: building `cmd/bni` via gen1 (any `-int` mode: `builder-comp-int` / `builder-comp-int-int` / `builder-comp-comp-int`) fails — `clang … pkg__binate__loader.ll: error: invalid getelementptr indices` on `getelementptr %bn_pkg__binate__loader__Package, …Package* %v.sc, i32 0, i32 4`. The emitted `Package` LLVM struct type has fewer fields than the field-4 GEP expects. Deterministic (reproduced 3×, fresh build dirs).
- **Bisected**: builds `bni` cleanly at `27c1ee8b` (b0402d04's parent); FAILS at `b0402d04`. So `b0402d04` ("codegen: discover struct types reachable only through globals", plan-cr2-2 Defect 2) is the culprit. NOT caused by the plan-cr2-3 Defect-1 commit (`68616b20`, native/VM only) — the regression reproduces at `b0402d04` without it.
- **Root cause (direction — needs confirmation)**: `b0402d04` added an `m.Globals` scan to `collectStructTypes` plus `TYP_NAMED→.Underlying` / `TYP_ARRAY→.Elem` recursion arms to `discoverStructFromType`. Claimed "purely additive," but in **cmd/bni's** module (which has globals cmd/bnc lacks — `builder-comp-comp`/gen2 appeared to still build, so the trigger is bni-module-specific) the new discovery emits the `Package` struct type with a wrong/truncated body (likely the `TYP_NAMED` arm registering the underlying struct under a name that collides via `addStructDef` dedup, OR an `m.Globals`-discovered path emitting a partial def), so a later field-read GEP into field 4 is out of range. Inspect the emitted `loader.ll` `%bn_pkg__binate__loader__Package = type {…}` def vs the GEP.
- **Scope (BROAD)**: the failing operation is gen1 (LLVM) compiling `loader` while building `cmd/bnc`/`cmd/bni`, so EVERY mode that rebuilds the toolchain via gen1 is broken — `-int` (bni build), `builder-comp_native_aa64`/`_x64` (the native-backend bnc binary is itself BUILT by gen1's LLVM codegen — CONFIRMED fails with the same `loader.ll` GEP), and gen2 (`builder-comp-comp`/-comp-comp-comp, once the stale gen2 cache is invalidated). Only `builder-comp` (BUILDER compiles cells directly, no gen1 recompile of `loader`) and unit tests for packages that don't import `loader` (e.g. the native/x64 backend test binaries) still build. Nearly all conformance verification is blocked until this is fixed.
- **Severity**: CRITICAL/MAJOR — breaks the self-hosted bytecode-VM build and ≥3 conformance modes on `main`; loud (compile error). Landed minutes before discovery (concurrent Plan-2 work); CI may not have run the `-int` modes against it yet.
- **Discovery**: 2026-06-08, building `bni` to test the `unary-minus-subword` regression cell during plan-cr2-3 Defect 1. `bni` had built fine earlier this session pre-rebase (at `c2aaaabf`).
- **Fix direction**: revisit `b0402d04` — revert + re-land with a self-host guard (a `builder-comp-int` smoke that builds the FULL `cmd/bni` toolchain, not just simple cells, would have caught it), or fix the struct-def emission in the new discovery arms.
- **Refined root cause + VERIFIED mitigation (2026-06-08, plan-cr2-2 author session)**: the trigger is specifically the `discoverStructFromType` recursion **arms**, NOT the `m.Globals` scan — removing only the scan does NOT fix it; removing the scan AND the `TYP_NAMED`/`TYP_ARRAY` arms DOES (bni builds clean). The colliding struct is the per-package **`reflect.Package` descriptor** payload (`<{ %BnSlice }>` = `{ Name *[]readonly char }` emitted by `emit_pkg_descriptor.bn`): a new arm reaches it with the UNQUALIFIED name `"Package"`, and `addStructDef` mangles every discovered struct via `mangle.StructName(modulePkgName, t.Name)` — the **current module's** prefix — so while compiling the `loader` module it registers as `bn_pkg__binate__loader__Package`, colliding (dedup, first-wins) with the loader's own 5-field `type Package`; the 1-field descriptor def wins and the field-4 GEP into the real Package is out of range. So this is a **cross-package struct-name mangler collision** (`addStructDef` keys by current-module prefix, not the struct's defining package) that the new discovery arms merely EXPOSE. gen2 builds because its `loader.o` is reused from the builder-compiled artifact (gen1 never recompiles loader for gen2); the `-int` path compiles `cmd/bni` fresh with gen1, hitting it. **Reverting `b0402d04` restores green (verified: revert of the discovery change on top of `f2ebaca1` → bni builds + `globals/struct` passes `-int`).** Proper fix: make `addStructDef` mangle a discovered struct by its DEFINING package (or ensure cross-package structs reach it qualified), so a same-named struct in the compiled module can't shadow it — then the discovery arms can be restored.

### ~~Package-level global of a NAMED type miscompiles — named scalar emitted `global i64 0`, named-over-aggregate emitted an invalid zero token~~ — FIXED (binate `b43a0057` IR-gen + `f2ebaca1` codegen, plan-cr2-2 Defect 1)
- **Fix (two layers, both landed)**: (1) IR-gen now registers a named-distinct non-struct type as a `TYP_NAMED` alias (binate `b43a0057`, the named-distinct-scalar work), so `resolveTypeExpr` returns the real `TYP_NAMED` (carrying `.Underlying`) instead of the old `TypInt()` fallback — named-scalar/float globals get `double 0.0` / `float` / `iN`, and named-over-aggregate globals reach codegen as `TYP_NAMED`. (2) The `emit.bn` global static-zero token dispatch now peels `TYP_NAMED` as well as `TYP_READONLY` (via the new `stripWrappers` helper, binate `f2ebaca1`), so a named-over-aggregate global emits `zeroinitializer` / `null` instead of the invalid bare ` 0`. Pinned by `emit_global_test.bn` (TestEmitGlobalNamed{IfaceValue,FuncValue,ManagedSlice,ManagedPtr}ZeroInit) + the four `conformance/matrix/globals/noinit/named-{iface,func,managed-ptr,managed-slice}` cells (now green on the LLVM modes; xfails removed). Verified by reverting `f2ebaca1` (cells red) and re-applying (green on gen1+gen2).
- **Symptom (was)**: `type Celsius float64; var C Celsius = 3.5` emitted `@C = global i64 0` (should be `double 0.0`); a named-over-address-aggregate (`type MyErr @errors.Error; var X MyErr`) emitted `@X = global %BnIfaceValue 0` — an invalid LLVM token clang rejects (`integer constant must have integer type`).
- **Note on the prior root-cause text (now corrected)**: an earlier version blamed `resolveTypeExpr`'s `gen_util.bn:294` `TypInt()` fallback as still-live; that was made stale by `b43a0057`, which registers the `TYP_NAMED` alias so the fallback is no longer reached for these. The remaining live gap was purely the `emit.bn` token peel, fixed by `f2ebaca1`.
- **Severity**: MAJOR (was an invalid-LLVM hard failure for named-over-aggregate; latent wrong-type/width for named-scalar). Discovered 2026-06-07 by the adversarial review of the global-init fix.

### ~~Integer shift by a count >= bit width is hardware-masked (mod width), NOT the spec's defined 0 / sign-extend~~ — FIXED 2026-06-06 (binate `32fde83d`)
- **Fix**: a branchless overshift guard in IR-gen (`gen_binary.bn`,
  `emitGuardedShift`), so a non-constant (or out-of-range constant) shift count
  yields 0 (logical) / sign-fill (arithmetic `>>`) per the spec, on every
  backend with no per-backend logic. An in-range constant count stays a plain
  shift (the common case is unchanged). `math.RoundToEven`'s temporary IsInf/
  IsNaN workaround was removed. Pinned by `conformance/631_shift_overshift`
  (LLVM/VM/native-aa64/gen2) + IR-gen unit tests; full builder-comp 854/0.
- **Symptom (was)**: a shift whose count is >= the operand's bit width returns a
  hardware-masked result instead of the documented value. Confirmed (LLVM, both
  const-folded and runtime counts): `full >> 64 == full` and `1 << 64 == 1`
  (both should be `0`); `full >> 70 == full >> 6` (count masked to `70 mod 64`).
  The native backends (aarch64 `LSL`/`LSR`, x64 `SHL`/`SHR` mask the count to 5/6
  bits) and the VM (host shift) almost certainly do the same — needs confirming
  per backend.
- **Spec violated**: `claude-notes.md` Operators — "Shift by >= bit width:
  defined behavior (zero for `<<` and logical `>>`, sign-extended for arithmetic
  `>>`)". Matches Go (which guarantees shift-away-to-0). The implementation does
  C/hardware masking instead.
- **Impact**: any shift by a *runtime* count that can reach/exceed the width is
  silently wrong. Breaks ported code that assumes Go's shift semantics — e.g.
  `math.RoundToEven` (its `e >= bias` branch shifts by huge counts for ±Inf/NaN
  and relies on `>> n == 0`; worked around with an IsInf/IsNaN guard, removable
  once this is fixed), and likely upcoming fdlibm ports. Discovered 2026-06-06
  porting `math.RoundToEven` (the ±Inf/NaN case produced a non-NaN).
- **Root cause**: codegen emits the raw hardware shift. LLVM `shl`/`lshr`/`ashr`
  by >= width is poison, lowered to a masking hardware shift; the native shifts
  mask the count register directly.
- **Test**: `conformance/matrix/scalar/{shl,shr}-overshift/<width>/<sign>` (16
  cells, binate `6fdb56eb`) — count == width, runtime `var` count (exercises the
  backend shift, not const-fold). CONFIRMED wrong on **every** backend (LLVM, VM,
  both natives); xfailed all modes — **un-xfail when the fix lands**. (Closes the
  scalar matrix's value-axis gap: shifts were only tested as in-range consumers.)
- **Fix (in progress, honor the spec)**: make codegen guard each variable-count
  shift so a count >= width yields 0 (logical `<<` / `>>`) or sign-fill
  (arithmetic `>>`), on every backend + the VM. The alternative — changing the
  spec to hardware-masked / UB-on-overshift (cheaper, matches C/hardware) — was
  considered and rejected in favour of keeping the documented Go-style guarantee.

### ~~Managed struct `@func` fields: stale `ctx.CurBlock` after a block split → malformed IR~~ — FIXED + LANDED 2026-06-06 (binate `47d05c81`)
- **Symptom**: a managed struct holding `@func` fields crashes — compiled SIGTRAPs
  (rc 133, no output), interpreted aborts `vm: func_value_dtor on nil fv address`
  (the `fvAddr == 0` "IR-gen bug — fatal" branch in `vm_exec_iface.bn`). NOTE: this
  is NOT the destructor walking a wrong field offset (the original guess, now
  disproven) — it is malformed IR produced during *construction*.
- **Root cause (confirmed)**: `genExprOrFuncRef` (`pkg/binate/ir/gen_util.bn`) had a
  function-reference early-return that emitted into block `b` and returned WITHOUT
  `ctx.CurBlock = b` — unlike every other return path in that function (the typed-int
  returns and `genExpr`'s pre-amble all sync it; the function's own comment documents
  why). Assigning a function reference to an `@func` field emits an old-value RefDec
  whose null-guard SPLITS the block; the split leaves `ctx.CurBlock` pointing at the
  now-terminated block, and the next statement's `b = ctx.CurBlock` reverts `b` to it.
  So two consecutive func-ref `@func` assignments emit statement 2 into the already-
  terminated block → two terminators + an orphaned `unreachable` continuation, i.e.
  malformed IR. It is built before backend selection, so BOTH native and the VM crash.
  Raw `*func` has no managed dtor → no split → no desync, which is why `*func` is clean.
- **Minimal repro (cross-package was incidental — the real discriminator is func-ref
  vs param RHS)**: single package, two function-reference assignments to `@func` fields
  in sequence — `io.W = sinkW; io.E = sinkE` → malformed `newIO` (two `br` in `entry.0`;
  `fv_refdec_cont.2` → `unreachable`; rc 133 / vm-fatal). The param form `io.W = w;
  io.E = e` is well-formed (params route through `genExpr`, which syncs). Verified:
  old bnc rc 133 / fixed bnc rc 0; param control rc 0 both. Every prior single-package
  minimization used params or a single assignment, which dodged it.
- **Discovery**: 2026-06-06, building minbasic's M3 embeddable REPL; basicSession's
  duplicated `@func` `ReplIO` crashed `cmd/basic`. minbasic's `newIO` / session setup
  assigns function references, which is what tripped it.
- **Fix**: add `ctx.CurBlock = b` before the func-ref `return fv` in `genExprOrFuncRef`.
  Covered by `conformance/634_funcref_managed_field_seq` (basicSession-shaped: inline
  `@func`-bearing struct field + sibling `@func`, all assigned from function
  references; prints `1 2 1 2 7 42`, crashed rc 133 / vm-fatal before the fix).
  Landed binate `47d05c81` (fix + test).
- **Sibling instance (found by adversarial review, also FIXED + LANDED)**: the same
  `ctx.CurBlock`-desync class was live in `genMultiAssign`'s SELECTOR arm
  (`gen_assign_multi.bn`) — a multi-assign whose earlier target is a managed
  `@func`/`@Iface` IDENT (block-splitting old-value RefDec) and a later target is a
  selector silently DROPPED the selector store and every statement after the
  multi-assign (`f, h.n = twoFI()` printed nothing pre-fix; `11`/`5` after). Root
  cause: `genSelectorPtr` (unlike `genExpr`) does not sync `ctx.CurBlock`, so the
  arm's `b = ctx.CurBlock` reverted to the stale block. Fixed by re-syncing
  `ctx.CurBlock = b` per target. Landed binate `2f507f26` + `conformance/641`.
- **Follow-up (broader gap) — DONE + LANDED 2026-06-06**: this whole class — a
  `ctx.CurBlock` desync in *any* codegen path after a block split — is invisible to
  output/refcount conformance tests (they only see the end result, if the program
  survives at all). A structural IR verifier now catches it at the source:
  `VerifyFunc`/`VerifyModule` (binate `c899e33b`, `pkg/binate/ir/verify.bn`) check
  per-block single-terminator-last + valid successors (the exact malformed shapes the
  desync produces); wired into `genFunc` behind `SetVerifyIR` (off by default; binate
  `4e78e28d`). Designed + adversarially critiqued (the critique excluded reachability
  — IR-gen legitimately leaves benign orphaned `switch.exit`/`if.merge` blocks when
  all arms return — and SSA dominance, as false-positive-prone / redundant for this
  class). Shadow-validated with the assertion forced on over the whole conformance
  corpus + gen2 self-compile in all three modes (1069/0, 1039/0, 1069/0): zero false
  positives. On its first run it caught a real pre-existing bug — `panic(...)` emitted
  a dead `OP_CONST_NIL` into the block `EmitPanic` had terminated, so the finalizer
  added a redundant `unreachable` (a two-terminator block on every panic-terminated
  func); fixed in binate `b03d1f07` (return a detached const-nil). **Enabled in CI**:
  `cmd/bnc --verify-ir` (binate `b4312c0e`) flips `SetVerifyIR(true)`; the
  `e2e/verify-ir.sh` test (binate `ff42d9ec`) builds gen1, then compiles the whole
  toolchain — `cmd/{bnc,bni,bnas,bnlint}` + full dep closure (≈ the entire codebase,
  incl. the compiler's own self-compiled IR) — with `--verify-ir`, so a malformed-IR
  regression fails CI at IR-gen.  (An earlier conformance `verify-ir` job, `64fb2c19`,
  covered only test-program IR via a redundant full-suite re-run and was dropped in
  favor of the e2e test, `e6fdb3f8`.)  Remaining (optional): add reachability (needs
  IR-gen to prune benign orphans first) / SSA-dominance to the verifier itself.

### ~~Non-integer const-EXPRESSIONS (binary float, bool comparison) and const-as-array-dimension are dropped → read as int 0~~ — FIXED+LANDED (binate `52a9eabf` and predecessors, 2026-06-05)
- **Scope**: this is the const-*expression* tail of the non-int-const family
  (the literal cases — `const C float64 = 0.1`, `const B bool = true` — were
  fixed in Phase A; see the "top-level consts of non-int types" MAJOR entry).
  `classifyConstLit` recognizes only a *bare / unary-minus* float or bool
  **literal**; any non-int const whose initializer is an **expression** still
  falls through to the integer-only `evalConstExpr`, which can't evaluate it, so
  `genConst` drops the const and reads fall to `EmitConstInt(0, TypInt())`.
- **Confirmed manifestations** (2026-06-05, on LLVM — default mode):
  - **binary float** — `const X float64 = 1.5 + 2.5` (and `*`, `/`) reads as
    **0** (silent wrong; in some shapes emits `mul i64` over `double` operands →
    invalid IR / clang reject).
  - **bool comparison** — `const B bool = 1 < 2` reads as **0** (false) instead
    of true; `< == > …` const-comparisons are dropped.
  - **const-as-array-dimension** — `const N int = 3; var a [N]int` →
    `len(a)` is wrong (observed 30, not 3): `resolveTypeExpr` (gen_util.bn:354-359)
    uses `parseIntLit(te.Len.Name)` on the *ident text*, never resolving the
    const; and `[N+1]int` is rejected outright by the checker's `evalConstInt`
    ("array length must be a constant integer") even though it is one.
- **Root cause**: IR-gen's const-expression evaluation is integer-only
  (`evalConstExpr`, gen_const.bn) and `classifyConstLit` is literal-only; the
  checker accepts these decls (it does fold ints via `foldIntArith`/
  `foldIntBitwise` but attaches no value to float/bool exprs). Same root as the
  non-int-literal family — extended from *literals* to *expressions* and to the
  array-dimension read path.
- **Severity**: MAJOR — silent wrong values (bool/float) and a silently wrong
  array length, on idiomatic const-expressions; the binary-float shape can also
  emit invalid IR.
- **Tests**: `conformance/regressions/const-expr/*` — green baselines
  (`int-arith`, `int-bitwise`, `int-paren`, `int-of-const`, `float-neg-literal`,
  `bool-literal`) confirm the integer/literal paths fold; xfailed
  (`float-binary-{add,div,mul}`, `bool-comparison`, `array-dim`) pin the gaps.
- **RESOLVED — now a Plan-1 defect (2026-06-05, user decision)**: a **bare**
  const-group member must **repeat the previous initializer expression**
  (Go-style), not take plain iota. Today it takes plain iota
  (`gen_const.bn:293-299`), so `const ( B0 int = 1 << iota; B1; B2; B3 )` gives
  `1,1,2,3` instead of the correct `1,2,4,8` bit-flag idiom, and
  `const ( K0 int = iota + 100; K1; K2 )` gives `1,2` instead of `101,102`. This
  is now a CONFIRMED bug to fix in Plan 1: a bare member re-evaluates the most
  recent explicit initializer expression with its own `iota`. Test:
  `conformance/regressions/const-expr/iota-repeat` (the `1<<iota` bit-flag form,
  xfailed until implemented).
- **Discovery**: 2026-06-05, P1 const-expr loose-axis (design fan-out + probes).
- **Fix**: evaluate non-int const *expressions* at the right type — fold float
  const-exprs at float precision and bool const-comparisons to a bool, and
  resolve const idents/exprs in the array-dimension path — or reject
  unsupported const-exprs with a clear diagnostic rather than dropping to int 0.

### ~~Native backends mis-pass a variadic float `__c_call` argument — CONFIRMED, both native backends~~ — ✅ RESOLVED (binate `56f09bc6`, SysV `AL=nsrn` + AAPCS64-darwin variadic-stack rule)
- **Symptom**: a variadic `double` passed via `__c_call` reaches the callee
  wrong on the native backends — `__c_call("printf", int32, fmtPtr, ...,
  cast(float64, 2.0))` with format `"%.0f\n"` prints **0**, not **2**. Correct
  on LLVM (comp) and the VM is N/A (`__c_call` is compiled-mode-only). Fails on
  both `native_aa64` and `native_x64`.
- **Root cause (suspected, §3.9)**: the variadic calling-convention edge — on
  x86-64 SysV the caller must set `AL` = number of vector (XMM) args so a
  variadic `double` is read from `XMM0`; on darwin-arm64 every variadic arg is
  passed on the stack as an 8-byte slot (not in registers). The native backends
  do neither for the `__c_call` variadic tail, so the float lands in the wrong
  place and printf reads garbage/0.
- **Test**: `conformance/regressions/c-call/printf-variadic-float` (xfailed the
  3 native modes; also xfailed VM + arm32 like all `__c_call` cells).
- **Discovery**: 2026-06-05, P1 `__c_call` loose-axis.
- **Fix**: in the native `__c_call` lowering, implement the variadic ABI —
  set `AL`=vector-count on x64-SysV; stack-pass varargs on darwin-arm64
  (per-target, since the convention differs).

### ~~Multi-value assignment `a, n = f()` mishandled managed targets~~ — FIXED + LANDED 2026-06-03 (binate `0b3f4abe`)
- **Was**: `genMultiAssign` (then inline in `genAssign`) Axiom-3 copy-RefInc'd each managed component then stored it, with two defects:
  - **Defect A (CRITICAL, wrong-code/UAF)**: the copy-RefInc had arms for `@T` / `@[]T` / `@Iface` but **none for `@func`**, so `g, n = f()` returning `(@func(...), int)` stored the `@func` without a copy-RefInc; the call-result temp's dtor freed the closure record while `g` still pointed at it → UAF on invoke (+ double-free at scope exit).  Probe: a capturing `@func` multi-assigned then invoked → SIGSEGV.
  - **Defect B (MAJOR, leak)**: the IDENT / INDEX / SELECTOR stores overwrote the target with no RefDec of its OLD managed value, so reassigning a live managed variable leaked the previous value (+1/exec).
- **Fix**: reworked the multi-assign managed-store to mirror single-assign's RefInc-new / RefDec-old discipline (Axiom 5) across all four managed VALUE types (`@T`/`@[]T`/`@func`/`@Iface`) and all three target shapes (IDENT / INDEX / SELECTOR), via new shared dispatchers `emitManagedValueCopyRefInc` / `emitManagedValueRefDec` (gen_util_refcount.bn) + predicate `isManagedScalarType` (gen_refcount_pred.bn).  The multi-assign body was extracted to `genMultiAssign` + `emitIndexStore` in a new `gen_assign_multi.bn` (gen_control.bn was over the 500-line soft cap).  Blank `_` targets still skip copy-retain (the `_`-discard fix, `567`).
- **Tests**: conformance `571_multiassign_old_value_released` (B: aliased object's refcount returns to baseline), `572_multiassign_func_value_retained` (A: capturing `@func` multi-assigned + invoked, no UAF — crashed pre-fix), plus `gen_assign_multi_test.bn` unit tests (bound component copy-RefInc'd vs blank `_` skipped, for `@T` and `@func`; index target refcounts the old element).  Green in all 6 default modes; compiled 491/0, int 485/1 (the 1 = pre-existing 520).
- **Struct-aggregate SELECTOR/INDEX — FIXED 2026-06-03 (binate, pending cherry-pick)**: a managed *struct/array AGGREGATE* field/element targeted by a multi-assign SELECTOR/INDEX (`s.structField, n = f()` / `arr[i], n = f()` where the element is a managed struct) was a plain store — no save-copy-destroy — so the new aggregate's managed fields were under-retained (double-free at scope end) and the old element's leaked.  Now save-copy-destroyed: SELECTOR mirrors the IDENT struct case; INDEX array/pointer via a new `emitElemPtrStore` helper, INDEX slice via `emitStructElemRefcount`.  Test `conformance/574_multiassign_struct_aggregate` (captured `@Counter` refcount returns to baseline 2, was 1 pre-fix); green in all 6 modes, verified to fail pre-fix.
- **Discovery**: 2026-06-03, reviewing the multi-assign path while fixing the `_`-discard leak (`570`).  Pre-existing.

### ~~`136_grouped_imports` / `383_cross_pkg_iface_dtor` — `package "pkg/builtins/rt" not found` under int-int~~ — FIXED+LANDED (binate `db18f26b`, 2026-06-05; harness wiring, not the loader)
- **Symptom**: both fail ONLY in `builder-comp-int-int` with
  `package "pkg/builtins/rt" not found` (a loader error, before execution);
  green in all other modes.  Confirmed pre-existing on a clean tree
  (2026-06-03) — independent of the `@func`/`@Iface` work.  Both are
  multi-package tests (grouped imports / cross-package), so the deeply
  nested interpreter's package resolver appears to mis-resolve a transitive
  core import at int-int depth.  No xfail markers yet.  Root cause: unknown
  — needs investigation of the int-int package search-path setup.

### ~~Wire the cross runners to `binate-paths --target`~~ — ✅ RESOLVED 2026-06-10
- **Conformance (binate `a3755cb4`)**: the four cross *conformance* runners
  mirror their bnc `--target` onto the `binate-paths.sh --iface` call
  (arm32-linux, arm32-baremetal, x86_64-linux, x86_64-darwin); 692 green on
  every mode, no xfails.
- **Unittest (binate `ac738936`)**: the three parallel
  `scripts/unittest/runners/` cross runners (arm32_linux, arm32_baremetal,
  native_x64_darwin) now mirror `--target` too.  Inert today (no unit-test
  package imports `build`), but it closes the latent silent-miscompile gap.
- **Sweep complete**: a repo-wide grep confirms every `.sh` that passes
  `--target` to a compiler AND calls `binate-paths` now carries `--target` on
  its `--iface` call (7 sites: 4 conformance + 3 unittest).
- **Discovery**: adversarial verification workflow over the `a3755cb4` change.

### ~~Remove `pkg/builtins/lang` → `pkg/bootstrap` dependency~~ — ✅ FIXED (binate `69eaf662`, 2026-06-11)
- **STATUS 2026-06-11 — FIXED & LANDED (binate `69eaf662`).** lang carries its own float formatter; the integer formatting is unified around the buffer-writing primitives `formatUint64Into` / `formatInt64Into`, shared by both the integer Stringers and the float formatter — a single 64-bit integer→decimal path (no narrow/wide `formatInt`/`formatInt64` pair).  Output byte-for-byte unchanged; `conformance/664` extended to pin the fallback.  `import "pkg/bootstrap"` is gone.  Follow-up DONE (binate `92b9aa9e`): the fallback for `|v| >= 2^53` / `|v| < ~1e-6` now emits approximate decimal e-notation (`1e16`, `1.5e20`, `1e-7`) instead of the binary `mantissa*2^exp` — decimal exponent estimated from the binary one, normalized via `pow10` (binary exponentiation), rounded to 7 significant digits with carry, trailing zeros trimmed.  Approximate by construction (the `÷10^k` isn't exact), consistent with the truncating fixed-point branch; `664` covers it.
- **What**: `pkg/builtins/lang` (tier 0) imports `pkg/bootstrap` solely for `bootstrap.formatFloat`, called from `floatToCharSlice` (the helper behind `float32.String()` / `float64.String()`, `lang.bn:163-184`). Drop this dependency.
- **Two rules violated**:
  1. **`pkg/bootstrap` is slated for deprecation** — it's the transitional I/O + format primitive layer meant to be removed (cf. the println-hack / bootstrap-retirement direction). A tier-0, always-bundled stdlib package building a *public* API (`Stringer`) on top of it cements a dependency on infrastructure designed to go away.
  2. **`formatFloat` is semi-private** — lowercase (package-private by Binate naming convention) and exported via `pkg/bootstrap.bni` ONLY for a technical reason: "cross-compilation-unit linkage: IR-gen for the print/println builtin emits direct calls into this helper" (`bootstrap.bni:36-38`), and whitelisted in `scripts/hygiene/naming.whitelist` precisely because it's a lowercase-in-a-`.bni` linkage hook, NOT a public API. Same for `formatInt`/`formatUint`/`formatBool`/`formatInt64`. lang reaching for `formatFloat` abuses an internal print-builtin linkage hook as if it were a library function.
- **Fix direction**: give lang its own float→decimal formatter (it already carries its own *integer* formatters — `formatUint64`/`formatInt64` — for exactly this reason; the integer `Stringer`s do NOT borrow `bootstrap.formatInt`), or source float formatting from a proper public package. Honest caveat: a real float formatter (shortest-round-trip / `%g`-grade dtoa) is non-trivial — but that's an algorithm question, not a reason to keep borrowing bootstrap's helper; scope the formatter against what `Stringer` actually needs and decide. NOT caught by the tier-dependency hygiene check above (`pkg/bootstrap` IS bundled, so a tier check won't flag it, and it doesn't break the bundle) — this is a distinct "don't build a public API on deprecated / semi-private internals" concern.
- **Discovery**: 2026-06-10, release-prep for `bnc-0.0.8`, while removing lang's sibling `pkg/binate/buf` violation (binate `84818a77`). With `buf` gone, `bootstrap` is lang's remaining questionable dependency.

### ~~Remove `findRuntime` auto-resolution; require an explicit `--runtime`~~ — ✅ RESOLVED 2026-06-10 (binate `aa757361`)
- **What**: `cmd/bnc`'s `findRuntime` (`cmd/bnc/util.bn:163-188`) auto-resolves the libc C runtime path when `--runtime` is absent. Its search is fragile: phase 1 probes `{runtime,../runtime,../../runtime}/binate_runtime.c` relative to the input file's dir (only **3 levels**), phase 2 falls back to those suffixes **relative to CWD**, and on a miss it returns **empty** — at which point the link gate (`main.bn:214`, `len(runtimePath) > 0`) **silently drops the C runtime** (and rt/libc stubs) from the clang link, producing a cryptic downstream `undefined _bn_pkg__bootstrap__Write` / `undefined reference to main`. The preferred end-state (per user) is to **delete `findRuntime` entirely and require `--runtime`**.
- **Why**: this implicit, CWD-dependent resolution caused the Lane A CI conformance break — deeply-nested conformance cells, compiled from CI's workspace-root CWD (checkout one dir deeper, under `binate/`), resolved empty → runtime dropped → every deep `-comp*` cell failed to link. The immediate release-blocker fix made the conformance runners pass explicit `--runtime` (binate `a256c893`). With that, **no caller relies on auto-resolution** — `scripts/build-*.sh`, `e2e/*.sh`, `scripts/lib/build-compilers.sh` (gen1), and the `release-process.md` smoke tests all already pass `--runtime`.
- **Direction**: (1) Confirm no remaining caller depends on `findRuntime` (grep repo + scripts + any embedder). (2) Delete `findRuntime` + its call in `main.bn:85-88`. (3) When a host-runtime-linking compile is requested without `--runtime`, **error clearly** ("no host runtime: pass --runtime <binate_runtime.c>") instead of silently dropping it. Only error when a runtime is actually needed — baremetal targets use `appendTargetRuntime` (`target.bn`), and `-c`/VM/interpret paths don't link a host runtime.
- **Caveats**: `cmd/bnc` is BUILDER-compiled — deleting a function + adding an error stays BUILDER-`bnc-0.0.7`-compatible. Update any docs that mention runtime auto-resolution.
- **Discovery**: 2026-06-10, Lane A root-cause (`plan-bnc-0.0.8-release-blockers.md`): the depth-correlated CI failure (615 flat cells PASS, whole `matrix/` tree FAIL) traced to `findRuntime`'s CWD-relative fallback.
- **RESOLVED 2026-06-10 (binate `aa757361`; the `arm32_linux` runner --runtime fix `328582d7` is what surfaced it)**: `findRuntime` deleted; `main.bn` + `test.bn` error if `--runtime` is absent when linking, exempting `--emit-llvm` / `-c` and bare-metal (`suppressHostRuntime`). **The "Why" claim above that "no caller relies on auto-resolution" was WRONG** — ~13 in-tree LINKING sites silently depended on `findRuntime` and had to be given explicit `--runtime` (via `binate-paths --runtime`): `build-compilers.sh` gen2/native/interp, `build-{bnc,bni,bnas,bnlint}.sh` Stage-2 (both branches), the 5 native unittest runners, the 4 compiling perf runners, e2e repl/print-args/verify-ir, and the `arm32_linux` conformance+unit runners. Validated across every locally-runnable compile mode (conformance/unittest/perf comp+native, e2e, make-bundle, check-alloca) + the error/baremetal paths; arm32 confirmed on CI.

### ~~Float `!=` is ORDERED (`NaN != NaN` is false) — diverges from IEEE/Go/C; `==` and `!=` not complementary for NaN~~ — FIXED 2026-06-06 (binate `8f78575f`)
- **Symptom**: `var n float64 = NaN; n != n` evaluates to **false** (and `n == n`
  is also false), so the two are not complements. Every other language (Go, C,
  Rust, IEEE 754) makes `!=` *unordered*: `NaN != NaN` is **true**, and
  `(a == b) == !(a != b)` always holds. Any Binate code using the idiomatic
  `x != x` NaN test, or doing NaN-aware compare/sort/dedup, silently
  mis-behaves.
- **Root cause (deliberate, now reversed by user, 2026-06-06)**: the float
  compare emitters force ordered semantics for `!=`. LLVM `emit_ops.bn` uses
  `one` (ordered) instead of `une`; x64 `x64_float.bn` AND's `SETNE` with
  `SETNP` (NaN-gate); aarch64 `aarch64_float.bn` adds a `Csel … COND_VC` to
  zero the unordered result. `==` (`oeq`) and the four relationals (`olt`/`ole`/
  `ogt`/`oge`) are already correct; only `!=` is wrong.
- **Fix** (Phase 0 of `plan-std-math.md`): `one`→`une` (LLVM); `SETNE OR SETP`
  (x64); delete the aarch64 `OP_NE` Csel block; VM is fixed transitively
  (recompile) + a test. `oeq`/`une` are exact complements, restoring
  complementarity. Pin with a conformance cell (NaN compares + complementarity)
  across all default + native alt-modes; update the misleading code comments and
  add a float-comparison spec entry to `claude-notes.md`.
- **Discovered**: 2026-06-06 while scoping `pkg/std/math` (IsNaN needs correct
  NaN semantics). Prerequisite for the math package; lands standalone first.

### ~~Self-referential interface method (`Unwrap() @Error` — a method whose return type is its own interface) mis-resolves to a managed pointer → in-package ABI mismatch~~ — FIXED 2026-06-03 (binate `77499153`)
- **Symptom**: an interface with a method that returns its own interface type — e.g. `interface Error { Error() @[]char; Unwrap() @Error }` — miscompiles *in-package* at every dispatch of that method.  The vtable dispatch shim is typed `i8* (i8*)` (return = single pointer), but the method *body* returns a 16-byte `%BnIfaceValue`; the copy-site at the call (`var cause @Error = e.Unwrap()`) RefIncs the result via `extractvalue %BnIfaceValue …, 0`, so LLVM gets `%v6 = extractvalue i8* %v5, 0` → verifier error `extractvalue operand must be aggregate type`.  (Caught here only by that `extractvalue`; a dispatch whose iface-value result is merely stored/forwarded would **silently miscompile** — caller reads 1 word, callee wrote 2.)
- **Root cause (CONFIRMED)**: `collectInterfaceFromDecl` (`pkg/binate/ir/gen_iface_registry.bn`) resolves each method's return type via `resolveTypeExpr(m.Results[0])` (≈line 143) and stores it in `mi.MethodResults` **before** appending the interface to `moduleInterfaces` (≈line 201).  So while resolving `Unwrap`'s `@Error`, `Error` is not yet in the registry → `isInterfaceTypeExpr(Error)` misses → `resolveTypeExpr` falls to `MakeManagedPtrType` (`gen_util.bn:349`) → `i8*`.  `genInterfaceMethodCall` then reads `mi.MethodResults[j]` (`gen_iface.bn:153`) as the dispatch result type, so the shim returns `i8*`.  The method *definition*'s return type is resolved later (in `gen_func`, after all interfaces are collected) and correctly yields `%BnIfaceValue` — hence the in-module mismatch.
- **Why never caught**: `Unwrap() @Error` is the FIRST self-referential interface method in the codebase (an interface method whose return type is its own — or any not-yet-registered — interface).  All prior interface methods return scalars / `@[]char` / managed pointers, where the managed-ptr fallback and the correct type coincide at the LLVM level.
- **Severity**: MAJOR — in-package ABI mismatch for a whole class of interface (anything self-referential: builders, linked nodes, iterator-returns-iterator, and `Unwrap`).  Verifier-loud here, silent on store-only dispatch paths.
- **Fix (landed `77499153`)**: two layers.  `types/check_interface.bn` defines the interface symbol BEFORE resolving its method/parent signatures (matching the `.bni` bni_scope pre-registration, for in-`.bn` decls).  `ir/gen_iface_registry.bn` appends an identity stub to `moduleInterfaces` and points `currentImportAlias` at the interface's package before resolving method results (so a self-ref resolves even in the cross-package `RegisterAllInterfaces` pre-pass), then overwrites the stub.  Defining the interface early would let `interface A : A` resolve A as its own parent, so `resolveInterfaceExtension` now rejects self-extension explicitly.  Tests: `575_self_ref_iface_method` + `TestInterfaceSelfReferentialMethod`.
- **Discovery**: 2026-06-03, implementing `plan-std-errors.md` Part 1 — `pkg/std/errors`'s in-package unit tests (`TestNewUnwrapEmpty`/`TestWrapUnwrapCause`/`TestChainWalk` all call `.Unwrap()`).  Pre-existing latent bug.  Distinct from (but same managed-ptr-fallback symptom as) the cross-package entry below.

### ~~Multi-return of a `@func` component was miscompiled — capture lost (LLVM) + invalid closure-data kind (VM)~~ — FIXED 2026-06-03
- **Was**: a function returning a tuple with a function-value component — `func two(...) (int, @func(int) int)` — was wrong-coded for the `@func` slot.  `two(false)` returns `(0, adder(10))` (a capturing `func(x){ return x + n }`, n=10); `f(5)` then gave `5` not `15` in LLVM (capture `n` read as 0) and crashed `vm: unsupported function-value data kind: 0` in the VM.
- **Fix — two independent halves**:
  - **LLVM/IR (capture loss)**: fixed by the multi-assign managed-target refcount work (binate `0b3f4abe` + `6c4d45b0`) — the `@func` component was under-retained through the multi-value path, so the closure record was freed before invocation.  (Landed independently for the multi-assign CRITICAL bug; it also closed the LLVM half here.)
  - **VM (invalid closure data)**: binate `98f65edb`.  Once the closure record was valid again, the only remaining issue was the VM packing a 16-byte address-based `@func` component as one scalar word — the same shape as the iface case `578`.  Generalized `isVMInterfaceValue` → `isVMAddressAggregate` (iface + func) for both the multi-return result-layout classification and the EXTRACT pointer-mode.  (578 deliberately scoped to iface because the LLVM half was still broken then; with that fixed, extending to `@func` completes it cleanly.)
- **Tests**: `579_multi_return_func_value` (empty + capturing `@func` component, reassignment, invocation) — green in all six default modes.  Single-return `@func` stays pinned by 534/542/555.
- **Discovery**: 2026-06-03, while fixing the `@Iface` multi-return VM bug for `plan-std-errors.md` (the `(T, @Error)` error-return pattern).  Was pre-existing.

### ~~`551`/`573` native-aa64 `&G`-as-rvalue~~ — FIXED 2026-06-04 (binate `9a0f4f9a`)
- **Was**: taking a top-level global's address as a VALUE (`&G` as an
  rvalue: store value, call arg, return value, comparison operand,
  bit_cast source) was silently wrong on the native aarch64 backend.  `&G`
  is the IsGlobalRef pseudo-instr (ID -1, no SSA register); `getOperand`
  missed every lookup and returned -1, so the value-operand site dropped
  the operand (call args / return) or stored garbage.  Native handled
  IsGlobalRef only in ADDRESS-operand positions (load/store target, GEP
  base) via `emitGlobalAddr`; value positions were unwired.  The native
  analogue of the LLVM bug fixed in `99655f4e` (which rendered `%v-1`).
- **Fix**: new `emitValOperand` (aarch64_regmap.bn) — the value-operand
  analogue of `getOperand`: materializes an IsGlobalRef into a fresh
  scratch via ADRP+ADD, else defers to `getOperand`.  Routed every
  value-operand site through it (OP_STORE value; direct / indirect /
  func-value / handle call args; OP_RETURN single / sret-multi / packed;
  comparison operands; OP_BIT_CAST source); threaded `pkgName` into
  emitCallIndirect / emitCallFuncValue / emitCompare.  Two globals in one
  instruction (`&G == &H`) each get their own scratch — no clobber
  (contrast the VM's shared globalReg, 573's still-open `-int` bug).
- **Result**: `551` un-xfailed on native aa64; `573` (`return &G,&H` /
  `&G == &H`) — which was failing native aa64 UNMARKED — now passes there
  too.  Full native aa64 lane: 498 passed, 0 failed.  Unit tests:
  `aarch64_global_ref_test.bn`.  573's VM (`-int`) xfails are unaffected
  (the separate shared-globalReg bug, another worker's).
- **x64 parity — ✅ DONE (this bullet was stale; superseded by the x64
  `emitValOperand` work, RESOLVED & LANDED 2026-06-08, see the "`551`/`573`
  native-aa64 `&G`-as-rvalue" + "Global address (`&G`) as an rvalue dropped at
  `OP_CAST`" entries above).** x64 added its own `emitValOperand`
  (`x64_regmap.bn`) and routes EVERY value-operand site through it — store value
  (`x64_emit.bn`), direct / indirect / func-value / iface-method call args
  (`x64_call.bn`, `x64_call_indirect.bn:77/267`, `x64_iface.bn:119`), `OP_RETURN`
  single / sret-multi (`x64_return.bn:61`), comparison operands (`x64_ops.bn:254`),
  and the `OP_BIT_CAST` source (`x64_dispatch.bn:306`).  Re-audited 2026-06-11
  (every value-operand site confirmed on `emitValOperand`); `551`/`573` have no
  x64 xfail markers and pass on `native_x64_darwin` (and run in CI's native
  x86_64-linux `builder-comp_native_x64-comp_native_x64` lane).

### ~~Float function-values are silently miscompiled in the VM (`-int` modes)~~ — FIXED on main (`7abc3809`)
- **Plan**: [`plan-float-arg-shim.md`](plan-float-arg-shim.md). Design A
  (uniform all-`int` shim ABI) approved + landed on main `7abc3809`
  (2026-06-03), verified across all default LLVM modes + codegen/vm unit
  tests, hygiene clean. Unblocks the bootstrap native-only work below.
- **Now visible on native_aa64 (2026-06-10)**: `TestExternFloat{,32}ArgViaRegistry` are the SOLE remaining `pkg/binate/vm` unit failures on `builder-comp_native_aa64` after the `_Package` native-emit fix (binate `f7d116f3`) unmasked them (the package previously link-failed before any test ran). So this float-arg-shim native gap is now the one thing keeping native_aa64 `pkg/binate/vm` unit red.
- **NATIVE-GAP root cause + fix plan (2026-06-10 investigation)**: Design A int-ified the shim on the LLVM side ("native backends — all unchanged"), but in a `--backend native` UNIT build the package-under-test (`pkg/binate/vm`, incl. `vmTestFloatBits` + its `@__shim`) is compiled NATIVELY, so the LLVM int-ified shim is never used — the NATIVE shim is. `_raw_func_addr(fn)` → `OP_FUNC_HANDLE` → the `@__shim` (always-shim), called by the VM's all-`int` dispatch (`rt._call_shim_scalar`) with every arg in a GP register. The native shim emitters (`pkg/binate/native/aarch64/aarch64_funcvalue.bn emitFuncValueShims` + `aarch64_closure_shim.bn`; the x64 siblings) only SHIFT GP arg registers (drop the data param) and tail-branch — they do NO float int↔FP reconciliation, so a float-scalar arg reaches the real fn in a GP reg where AAPCS64/SysV says it reads `d0`/`xmm0` → garbage; a float-scalar return breaks symmetrically. (The native `OP_CALL_INDIRECT` float path, `aarch64_call_indirect.bn:44` `if isFloatTyp(arg.Typ) { Fmov_gp_to_fp }`, can't help — it keys on the IR OPERAND type, which is all-`int` in the magic dispatch.) **Fix = the native half of Design A**: in the native shim emitters, per the func-value's PARAM types, `fmov` each float-scalar arg's GP reg → its FP reg (FP index counted independently of the GP shift, mirroring `aarch64_call_indirect.bn`'s `nsrn`/`ngrn` split), and for a float-scalar return drop the tail-branch → `Bl` + `fmov` return-reg ← `d0` + `ret` (x64: xmm0 → rax). Reuse `codegen.isFloatScalarParam`/`floatSlotIsI32` (or a native mirror) so emit + call agree. ×2 arches; interacts with the closure-shim + pack-return shim shapes. Locally verifiable: `scripts/unittest/run.sh builder-comp_native_aa64-comp_native_aa64 pkg/binate/vm` → the 2 `TestExternFloat*` tests go green.
- **SCOPE CORRECTION — the fix is TWO-SIDED, not shim-only (2026-06-10, post-mapping; approved by user)**: the shim-only framing above is incomplete. The native func-value float ABI is **FP-resident on BOTH the shim AND the compiled caller** today: `emitCallFuncValue` (`{aarch64,x64}_call_indirect.bn`) places float args in `d/xmm[nsrn]` (`Fmov_gp_to_fp`/`Movq_gp_to_xmm`) and reads float returns from `d0`/`xmm0`, and the shim is FP-passthrough (does nothing to floats) — self-consistent, which is exactly why conformance `562–568` (float func-value arg/return/roundtrip/mixed/float32/aggregate) are GREEN on native today (only `569` closure-float is xfailed). The VM caller (`_call_shim_scalar`) is all-int (floats in GP), so only it is red. A shim is one static piece of code → it can serve ONE convention, and that convention is FORCED to all-int: (a) `_call_shim_scalar` can't place floats in FP without a float-aware trampoline (Design A rejected that); (b) the `@__shim` symbol is `weak_odr`/`SetWeak` and linker-deduped with the **LLVM** shim, which is already all-int — and the VM can't know which backend emitted the shim it calls. So the native shim MUST go all-int, which FORCES the native compiled caller all-int too; the two move together. **Shim-only would regress 562–568.** **Latent silent-miscompile bug this also fixes**: in a hybrid build (native `main` + LLVM deps), a float-arg func value for an LLVM-compiled dep function uses the LLVM all-int shim, but native `emitCallFuncValue` places the float in FP → mismatch → garbage (untested today; 562–568 keep everything in one native module). **Two-sided fix**: (1) SHIM (`emitFuncValueShims` + closure shims, ×2 arches): per the func-value PARAM types, `fmov` each float-scalar arg's positional-GP slot → its FP reg (`d/xmm[nsrn]`, 32-bit `S`/`movd` for float32; independent `nsrn`), and for a float-scalar return `bl`+`fmov`(FP→GP)+`ret` (frame) instead of tail-branch. (2) CALLER (`emitCallFuncValue`, ×2 arches): build the shim-boundary `argTypes` with float-scalars replaced by an int slot so they flow the GP positional path; drop the `xmm`/`nsrn` arg branch and the `Movq_xmm_to_gp` float-return special-case. `emitCallIndirect` (real fn-pointer calls — dtors/free_fn/cross-mode) keeps its FP handling: it does NOT go through the all-int shim. Re-verify `562–569` + `TestExternFloat*` on native aa64 AND x64-darwin; un-xfail `569`.
- **FUNC-VALUE HALF LANDED — binate `34533cf8` (2026-06-10)**: the two-sided all-int func-value shim fix (native shim does GP↔FP via a per-arch `emitShimArgMarshal*` walk + a float-scalar-return shape; `emitCallFuncValue` passes/reads floats via GP — substitutes a 1-word int slot in the shim-boundary `argTypes`, drops the xmm/nsrn branch and the float-result-from-xmm0 special-case). Added `common.FloatScalarIsI32` (+ unit test) so emit/call agree on i32-vs-i64 slot width; split `x64_funcvalue.bn` → `x64_funcvalue_vtables.bn` for the length cap. **Verified**: `TestExternFloat{,32}Arg` + `TestExternFloatReturn` ViaRegistry green on native aa64 AND x64-darwin; conformance 562–568 green on both; 22 func_value conformance green on aa64; hygiene clean. This is the bug that kept native_aa64 `pkg/binate/vm` unit red — now GREEN.
- **closure-float follow-up — ✅ RESOLVED 2026-06-11 (binate `085065d9`, claude-todo #121)**: added a float-aware closure shim path on each backend (`aarch64_closure_shim_float.bn` / `x64_closure_shim_float.bn`), routed from the dispatcher via `closureHasFloatParts` — moves the struct base to a scratch reg (X9/R10), spills incoming user args to the stack, then loads each underlying-call leaf by class into x[NGRN] / d|xmm[NSRN] (clobber-free, all memory-sourced), and fmov's a float-scalar return back to x0/rax.  Non-float closures untouched; overflow / aggregate-return / multi-return float closures `a.SetError` (loud, rare follow-ups).  569 un-xfailed + green on aa64 AND x64-darwin; new `697_func_value_closure_float_mixed` (mixed int+float captures+params + float return) green on aa64/x64/LLVM/VM; native unit tests + 27 func_value/closure conformance green both arches.  ORIGINAL (now historical): conformance `569` (a closure capturing+passing+returning float64) failed — the closure shims (`emitClosureShim*`: fast / stack-spill / aggregate, ×2 arches) had NO float GP↔FP handling and never did. **PRE-EXISTING, not caused by the func-value fix** (empirically `actual:0` on the pre-`34533cf8` tree for x64-darwin). Now xfailed on BOTH arches (aa64 already had one; the missing `builder-comp_native_x64_darwin` xfail was added in `34533cf8`). FIX = extend the float GP↔FP marshalling into the closure shims (captures from the closure struct + user args, splitting NGRN/NSRN; plus a float-scalar-return shape), ×2 arches × 3 shapes — a separate sizable rework atop the just-reworked (`646e1638`) closure code. Un-xfails `569` on both arches when done.  **POST-LANDING REVIEW (2026-06-11) — correctness CONFIRMED, coverage gap closed**: every reviewer-flagged untested shape was exercised on native aa64 + x64-darwin against the LLVM and VM oracles and matches — float32 capture/param/return (the FloatScalarIsI32 32-bit branch), float-param VOID return, managed-slice (indirect-large) capture interleaved with a float param, multiple GP captures ahead of a float param (NGRN≥2 with NSRN), mixed float32/float64 widths, readonly-float capture (transparent-wrapper peeling).  The three bounded-gap shapes (multi-return result, aggregate return, >8 FP-capture overflow) loudly `a.SetError` on both native arches (no silent miscompile) and run on LLVM/VM.  The review's one "critical" finding (`nUserWords` over-counts float params) was a **FALSE POSITIVE**: the shim is entered under the all-int dispatch, so a float param arrives as GP int bits and DOES occupy an incoming GP word — `nUserWords` counting it is correct, as `569`/`697` and the new float-param tests (whose params are spilled from incoming GP regs) prove.  New coverage `conformance/699–707` (binate `6e1cefe8`, LANDED): 6 positive + 3 native-xfail guards.  **Separately, the review surfaced an orthogonal pre-existing MAJOR bug** (inferred-type func-value local calls mis-lower to a direct symbol on ALL backends) — see its own `## MAJOR` entry.
- **Canonical repro**: `pkg/binate/vm` `TestExternFloat*ViaRegistry` (a
  bytecode caller invoking a native float extern via the registry) — the
  only path that hits the bug; user float func-values in `-int` are
  bytecode/trampoline (all-int VM slots) and round-trip fine without the
  fix, so the conformance 562-566 tests are compiled-mode reshape guards,
  not the repro.
- **Symptom**: a function-value call with a `float64`/`float32` arg or
  return produces the wrong value in any `-int` (bytecode VM) mode.
  Compiled modes are correct. Currently masked: there is *zero* test
  coverage for float func-values.
- **Root cause**: VM dispatch routes through `rt._call_shim_scalar(fn,
  data, a0..a6 int)` — an all-`int` `OP_CALL_INDIRECT`. The native
  backend only places an arg in an FP register when the IR operand type
  is float, so a float arg's bits land in a GP register while the natural-
  typed shim reads `d0`/`xmm0`. Float returns break symmetrically
  (aarch64 indirect has no float-return path).
- **Fix (Design A)**: int-ify float **scalars** in shim signatures and
  `bitcast` `i64↔double` / `i32↔float` at the shim boundary; the compiled
  call site (`emitCallFuncValue`) bitcasts to match. VM/`rt`/native
  unchanged; no-op for non-float signatures. Pure `pkg/binate/codegen`
  change. Conventions: exact-width slots (f64→i64, f32→i32), aggregate
  retbufs stay natural-typed, one shared `shimIntSlotType` predicate so
  shim and call site can't disagree (the only silent-miscompile path).
- **Why now**: prerequisite for the bootstrap injection below
  (`bootstrap.formatFloat` is a native extern once bootstrap is native-
  only) — without it, `conformance/287_float_println` regresses in `-int`.
  Per Bug Discovery Protocol, the new func-value-float tests are the
  tracked reproduction. Surfaced 2026-06-03 by the bootstrap work.

### ~~MAJOR — generic-interface-VALUE upcast (`@WideBox[int]` → `@Box[int]`) type-checks then fails silently in codegen (2026-06-13)~~ — ✅ RESOLVED 2026-06-14

**✅ RESOLVED 2026-06-14.** Both sub-issues fixed:
- **Sub-issue (2) — the upcast itself (binate `52f322fb`)**: the root cause was
  `ensureInstantiatedInterface` leaving an instantiated generic interface's
  `ParentPkgs`/`ParentNames` empty, so `collectImplsFromDecl`'s ancestor walk
  never emitted the inherited `(Impl, Box[int])` vtable and
  `IfaceParentSlotOffset` returned −1 (the VM name-swap found no target vtable).
  The non-generic path already recorded parents (incl. the `TEXPR_INSTANTIATE`
  parent case, conformance 455); extracted `collectInterfaceParents`, now called
  from both paths (the generic one under the type-param substitution context so
  `Box[T]`→`Box[int]`), with a self-stub to keep self-reaching parent chains from
  recursing. `conformance/768_generic_iface_value_upcast` (inherited dispatch +
  upcast-view dispatch) green on builder-comp / -int / -comp / native-aa64.
- **Sub-issue (1) — the silent exit (binate `a4946ebe`)**: root cause was that
  `panic()` discarded its message and aborted backend-dependently (LLVM dropped
  it, the **VM treated `OP_PANIC` as a NO-OP so panic didn't even abort**, native
  had no arm). `panic(args...)` now lowers to print `"panic: " + args + "\n"`
  then `bootstrap.Exit(1)` + unreachable — proven ops on every backend — so the
  message prints and the process aborts on LLVM/VM/native. Dead `OP_PANIC`/
  `EmitPanic` removed. `conformance/767_panic_message`.

Original report below.

Assigning an instantiated generic interface value to one of its (instantiated)
parent interface values — a parent-interface upcast — completes type-checking
and IR generation, then the LLVM/codegen emission exits 1 with **no diagnostic**.
The non-generic equivalent (`@Wide` → `@Cmp`) compiles and runs correctly, so
this is specific to *instantiated generic* interface values (Slice 6c
generic-iface-value support is partial — dispatch works, conformance 451–455;
this upcast does not).

- **Repro** (type-checks, then silent codegen exit 1):
  ```
  interface Box[T any] { get() T }
  interface WideBox[T any] : Box[T] { extra() int }
  type Impl struct { v int }
  impl @Impl : WideBox[int]
  func (i @Impl) get() int { return i.v }
  func (i @Impl) extra() int { return 0 }
  func main() { var im @Impl = make(Impl); var w @WideBox[int] = im; var b @Box[int] = w; _ = b }
  ```
- **Was masked**: before the `.Parents` checker fix (entry above), this same
  program was rejected at the checker (`cannot assign @WideBox[int] to
  @Box[int]`). Recording the parents makes the upcast type-check, exposing the
  codegen gap. So fixing the checker turned a clean rejection into a silent
  codegen failure for *this specific value-upcast pattern* (the
  constraint/forwarding paths — the actual reported bug — are now fully correct).
- **Two sub-issues**: (1) the silent exit emits NO diagnostic — codegen should at
  least report which construct it can't lower; (2) the generic-iface-value upcast
  itself needs codegen support (re-wrap with the parent vtable, or confirm the
  layout is uniform as for non-generic upcasts).
- **Discovery**: surfaced while landing the `.Parents` checker fix (2026-06-13).
- **Bug-discovery protocol**: add a conformance test for the upcast marked xfail
  until codegen lands.

### ~~MINOR — a broken generic-interface extension clause is re-reported once per instantiation (2026-06-13)~~ — ✅ RESOLVED 2026-06-14

**✅ RESOLVED 2026-06-14 (binate `e8713878`).** `addCheckError` now suppresses an
exact duplicate (same position and message) before appending to `c.Errors` — a
decl-level diagnostic resolved per instantiation collapses to one, and an
identical `(pos, message)` is never worth showing twice in general. Tentative
errors are untouched (they migrate/discard wholesale). Tests:
`checker_errors_test` (dedup mechanism) + `check_generic_type_test`
(generic-iface-extension scenario reports exactly once). Original report below.

Generic interface decls resolve their extension clause at instantiation
(`buildInstantiatedInterface` → now `populateInstantiatedInterface`), not at decl
time, so a decl-level error in that clause is emitted once per DISTINCT
instantiation. Repro: `interface Bad[T any] : NotAnIface { f() T }` instantiated
as `Bad[int]` and `Bad[bool]` emits `interface extension target must be an
interface` twice (count == number of distinct instantiations). Should be one
diagnostic per decl. Diagnostic-quality only (no miscompile). Possible fix:
validate the generic interface's extension clause once at collection time (with
type-params opaque), or dedupe decl-site diagnostics by (pos, message).
- **Discovery**: adversarial review of `298ef806`/`aef4422e` (2026-06-13).

### ~~MINOR — diagnostic name formatter drops a bracket on nested package-qualified generic args (2026-06-13)~~ — ✅ RESOLVED 2026-06-14

**✅ RESOLVED 2026-06-14 (binate `aa617f84`).** `displayLeafName` now strips the
package-path prefix of every `.`-separated segment, respecting bracket nesting:
a `.` is a package/name separator only when it follows a path segment and
precedes an identifier start (so a `[...]`/`func(...)` placeholder's dots
survive), and the type-syntax delimiters (`[ ] , @ * ( )` and the `readonly `
space) end a segment. `main.Box[@pkg/foo.Pair[bool,bool]]` now displays
`Box[@Pair[bool,bool]]`. Unit test in `type_name_test`. Original report below.

`displayLeafName` (`pkg/binate/types/type_name.bn`) splits a mangled name on the
FIRST `.`, which can land INSIDE a bracketed type argument, so a nested
package-qualified generic arg renders with a dangling/again-missing bracket —
e.g. `reportConstraintMiss` shows `Pair[bool,bool]]` instead of
`Box[@Pair[bool,bool]]`. Display-string only — accept/reject uses (Pkg, Name),
not the rendered string, so NO soundness impact. Pre-existing in the shared
formatter; surfaced (not caused) by the generics constraint work.
- **Discovery**: adversarial review of `298ef806`/`aef4422e` (2026-06-13).

### ~~MAJOR — `&(*p)` (address-of-dereference) mis-lowers → SIGSEGV on write-through (2026-06-13)~~ — ✅ FIXED+LANDED (binate `465b44b5`)

`genUnary`'s `&` arm now lowers a deref operand `&(*p)` to `genExpr(e.X.X)` (the
pointer `p`, since `&*p == p`), instead of falling through to `genExpr(e.X)`
which returned the loaded VALUE of `*p` as a pointer (write-through SIGSEGV'd).
`conformance/759_addr_deref` pins write-through + aliasing (builder-comp / VM /
gen2). Original investigation below.

`&(*p)` is checker-addressable (a dereference is a valid lvalue; `&*p == p`),
but IR-gen mis-lowers it.  `genUnary`'s `&` arm (`pkg/binate/ir/gen_expr.bn:152-178`)
handles `&ident` / `&index` / `&selector` and FALLS THROUGH to
`return genExpr(ctx, b, e.X)` for any other operand — so for `&(*p)` it returns
the LOADED VALUE of `*p` (e.g. `11`) as if it were a pointer.  Writing through
it crashes: `var p *int = &v; var pd *int = &(*p); *pd = 12` stores to address
`11` → SIGSEGV on otherwise-valid code (read-only `var pd = &(*p)` "works" — the
bad pointer just isn't dereferenced).  **Discovered** 2026-06-13 building the
addressability positive test `conformance/755_addr_lvalue_ok` (the `&*p` case
was dropped from it pending this fix).  **Fix**: add a case in genUnary's AMP
arm for `e.X.Kind == ast.EXPR_UNARY && e.X.Op == token.STAR` →
`return genExpr(ctx, b, e.X.X)` (the pointer `p`), since `&*p == p`.  Niche
(you'd just write `p`), but a loud crash on valid code.  Surfaced by the general
`&` addressability check (which correctly accepts `&*p`); the old kind-whitelist
also accepted it, so the IR-gen defect is PRE-EXISTING.  Needs a conformance
test (xfail until fixed, or passing if fixed).

### ~~MAJOR — aliased import `import a "pkg/x"` + cross-package call `a.Fn()` mangles the callee with the ALIAS, not the package path → undefined symbol (2026-06-12)~~ — ✅ RESOLVED (binate `52d1c832`)

Discovered while adding per-import build-constraint gating — the conformance
test happened to use an aliased import and surfaced this latent bug.  There
are **zero** aliased imports (`import <ident> "..."`) anywhere in `pkg/` or
`cmd/`, so the aliased-import code path has never been exercised.

- **Symptom.** `import a "pkg/aliastgt"` then `a.Code()` compiles a call to
  `@bn_a__Code` — the import ALIAS (`a`) is used as the package qualifier in
  the mangled callee — instead of the package path (`bn_…aliastgt__Code`).
  The symbol is undefined → LLVM `use of undefined value '@bn_a__Code'` (and
  the equivalent on the VM).  Fails in **all six default modes** (compiled +
  VM), so the root cause is in the shared front-end (selector resolution /
  IR-gen / mangle), not a backend.
- **Root cause (direction; needs confirmation).** The cross-package CALL path
  mangles using the alias as the package name rather than resolving the import
  alias → its path first.  The const path already resolves it — see
  `pkg/binate/ir/gen_const_fold.bn:58-59,218-219` (`currentImportAlias` +
  `buildQualName`) — so the func/call selector resolution (likely
  `pkg/binate/ir/gen_call.bn` / whatever feeds `mangle.FuncName`) needs the
  same alias→path resolution.
- **Repro / tracking.** `conformance/738_aliased_import_call` (xfailed in the
  six default modes): `import a "pkg/aliastgt"` + `println(a.Code())`, expects
  `7`.  Un-xfail when fixed.
- **Fixed** (binate `52d1c832`): `pushFileImports` (`gen_import.bn`) now uses
  the explicit `ImportSpec.Alias` when present (was always `lastPathSegment`),
  and `GeneratePackage`/`GenModule` overlay the module file's own imports
  (push/pop) so a module's own aliased refs resolve — not just imported
  packages' internal refs.  No-op for non-aliased imports.  Verified across
  reference kinds (738 = aliased func call, 6 modes; 742 = aliased type +
  const + func).  Full builder-comp suite 1405/0.

### ~~MAJOR — generic-instantiation recursion overflows the stack (SIGSEGV): recursive generic interfaces (introduced by `.Parents` `298ef806`) AND recursive generic structs (pre-existing) AND unbounded-growth chains~~ — FIXED + LANDED 2026-06-13 (binate `0880f663`, split `c6576ef2`)
- **Was**: `instantiateGenericDeclWithArgs` installed the (decl, args) cache entry AFTER `buildInstantiated{Struct,Interface}` returned, so any recursive reference during body/parent resolution missed the cache, re-entered instantiation, and overflowed the stack at type-check (exit 139). Manifestations: a **legitimate recursive generic struct** `Node[T]{ next @Node[T] }` (a linked list — pre-existing, crashes even on the pre-generics-work baseline); a **self-cyclic generic interface** `N[T] : N[T]` (introduced by `298ef806`, which started resolving the extension clause at instantiation); **mutually-cyclic generic interfaces** `P[T]:Q[T]` / `Q[T]:P[T]`; an **infinitely-growing chain** `Box[T]{ @Box[Wrap[T]] }`.
- **Fix** (`0880f663`), all at the instantiation chokepoint: `instantiateGenericDeclWithArgs` now creates the result shell (final mangled name + InstDecl/InstArgs) and registers it in the cache BEFORE populating; `buildInstantiated*` became `populateInstantiated*` helpers that fill the pre-created shell. So a legit recursive struct terminates and **builds & runs**, and a self-cyclic interface's parent resolves to the placeholder and hits the existing self-extension guard (clean reject). `resolveInterfaceExtension` consults a new `Checker.GenericIfacePopulating` stack to detect inheritance cycles across distinct instantiations (drops the back-edge, keeping Parents acyclic so the unguarded ancestor-closure walks can't loop). A `GenericInstDepth` bound (128) rejects unbounded-growth chains cleanly.
- **Test**: `conformance/762_generic_recursive_struct` (linked list runs → 3, ×3 modes) + `763`/`764`/`765` (self-cycle / mutual-cycle / unbounded-chain reject with clean diagnostics, not SIGSEGV) + 3 checker unit tests. Full unit (45 pkg) + full conformance (1432) green; prior generics fixes (749–761) still green.
- **Follow-up split** (`c6576ef2`): the fix pushed `check_generic.bn` over the 500-line soft limit; split generic-TYPE instantiation into `check_generic_type.bn` (pure code move, tests alongside in `check_generic_type_test.bn`).
- **Discovery**: adversarial review of the .Parents (`298ef806`) + constraint-substitution (`aef4422e`) fixes (2026-06-13); the recursive-struct case is pre-existing and was surfaced while scoping the interface fix.

### Replace redundant `buf.CopyStr("<string-literal>")` calls with bare string literals — ✅ DONE & LANDED 2026-06-11 (binate `a87e4285`..`f4b38754`, 5 commits)
- **DONE 2026-06-11**: swept ALL 165 literal-arg `buf.CopyStr("...")` sites across 22 files (more than the ~59 estimated — that counted only the bnc-cone) → bare literals; 3 files dropped a now-unused `buf` import; `buf/buf_test.bn`'s unprefixed `CopyStr("copy")` (testing the function itself) kept; the 168 variable-arg `buf.CopyStr(var)` calls correctly remain. BUILDER `bnc-0.0.8` accepts `@[]char = "lit"` (the Stage-2b implicit copy `OP_RODATA_MSLICE_COPY`), confirmed by the gen1 build. Landed as lexer/ir/codegen + asm-tests + native-tests + types-tests + cmd.
- **What**: ~59 call sites across 7 `.bn` files (e.g. `pkg/binate/lexer/lexer.bn` token lits, `pkg/binate/ir/gen_init.bn` / `gen_iv_thunk.bn` / `gen_import.bn`, `cmd/bni/main.bn`) call `buf.CopyStr("<literal>")` to materialize a `@[]char`. The bare literal already does exactly this: a string literal has natural type `[N]readonly char`, and assigning it to a *writable* `@[]char` is a **literal-init allocate+copy** (`OP_RODATA_MSLICE_COPY`) — identical to what `CopyStr` produces. So `tok.Lit = buf.CopyStr("+=")` → `tok.Lit = "+="`. Same realization that fixed `lang.bn` (binate `84818a77`); the wrapper is pure redundancy.
- **Two non-negotiable caveats**:
  1. **Literal args ONLY.** `buf.CopyStr(<variable>)` copies a runtime slice and must stay — this is strictly `CopyStr("...")` with a string-literal argument.
  2. **Target must be (explicit) `@[]char`.** Valid where the literal lands in a writable managed-slice context (assignment / field-init / param / return typed `@[]char`). An INFERENCE site — `x := buf.CopyStr("...")` → `x := "..."` — would change `x`'s type from `@[]char` to the literal's default `@[]readonly char` (a refcount-exempt rodata VIEW, not a writable copy): a semantic change. Such sites need an explicit `@[]char` annotation or must be left.
- **BUILDER-compat precondition (CHECK FIRST)**: most sites live in `cmd/bnc`'s BUILDER-compiled tree (lexer, ir/gen_*). Confirm the current BUILDER (`bnc-0.0.7`) accepts `var x @[]char = "lit"` before touching those files; if it's a post-0.0.7 feature, the cleanup in BUILDER-compiled files must wait for a BUILDER bump (or be limited to non-BUILDER files). `lang.bn` was safe because it's stdlib-impl (compiled by gen1, not the BUILDER).
- **Optional**: add a `bnlint` / hygiene rule flagging `CopyStr("...literal...")` so the pattern doesn't creep back.
- **Discovery**: 2026-06-10, after the `lang.bn` buf fix (binate `84818a77`) showed the wrapper is redundant.

### Bare const-group member drops its INHERITED narrow type — checker accepts an overflow the explicit form rejects; IR-gen truncates → SILENT wrong value — all backends — REGRESSION from `05901f97`/`5fc5a52f` — ✅ RESOLVED 2026-06-10 (binate `b9d6d807`) — the `const-group-bare-inherited-overflow` xfail (11 files) was confirmed STALE 2026-06-13 (test passes when un-xfailed); xfail removal pending
- **✅ RESOLVED 2026-06-10 (binate `b9d6d807`).** Per the user's semantics decision (**A — typed inheritance, Go-style**): a bare const-group member inherits the preceding member's TYPE, so it is range-checked at the inherited width. Fix threads the effective type (own if present, else the closest preceding member's, mirroring `genConstGroup`'s `prevTyp`) into the synthesized repeat in BOTH `checkGroupDecl` (`check_const.bn`) and `checkGroupDeclTentative` (`check_pending.bn`). Now `const ( B0 uint8 = 1<<iota; …; B8 )` rejects B8 at the declaration; an UNTYPED-base group is unaffected (members stay untyped, narrow at the use site). Also resolves the **B3 type-divergence** minor below (the parked bare member now carries the inherited type). Verified: full builder-comp suite 1328/0; cells 690 (typed-base decl overflow) + 691 (in-range typed bit-flag values) + 672 (reframed to untyped-base use-site overflow) green across all 5 modes; REPL path confirmed via manual bni (`println(B1)`→4). Known minor: the overflow error points at the inherited initializer expression (shared node), message correct. Two existing "Fits" unit tests + 672 reframed (they encoded the old untyped-narrowing behavior).
- **Symptom**: `const ( B0 uint8 = 1 << iota; B1; …; B8 )` — B8 = 1<<8 = 256 inherits `uint8`. The checker **ACCEPTS** it (compile exit 0); at runtime B7 correctly prints 128 but **B8 prints 0** (IR-gen types the bare member at the inherited width → `add i8 256, 0` → truncates). The explicit equivalents `var x uint8 = 256` AND `const B8 uint8 = 256` are BOTH rejected ("cannot assign untyped int to uint8"). So the bare-member path silently miscompiles an overflow the rest of the language rejects.
- **Root cause**: when synthesizing the repeat decl for a bare member, `checkGroupDecl` (`pkg/binate/types/check_const.bn:154`) sets `rep.TypeRef = inner.TypeRef` (nil for a bare member) — it never threads the PRECEDING member's TYPE. So `checkConstDecl` stores the member as untyped-int with NO range check. IR-gen's `genConstGroup` (`pkg/binate/ir/gen_const.bn`) DOES track `prevTyp` and types the bare member at the inherited width — hence the checker/IR disagreement + truncation. Same gap in the REPL path (`check_pending.bn:373`, B3).
- **Severity**: MAJOR — silent wrong-value miscompile from compiler-accepted source, contradicting conformance/645's documented rule and undercutting B1's own overflow-catching goal (B1's 672 cell uses a WIDE `int` base, so the bare-member-narrow path was untested). Held at major (not critical): trigger needs a narrow-typed flag word with an overflowing bare member.
- **Fix (NOT a semantics change)**: thread the inherited type into the synthesized rep in both `checkGroupDecl` and `checkGroupDeclTentative` (mirror genConstGroup's `prevTyp`), so the checker range-checks the bare member at the inherited width and rejects 256:uint8 like the explicit form — aligning the checker with itself and with IR. (Companion: the X3-highbit SIGNED sign-bit variant is a related divergence whose DIRECTION is contested/semantics-owned — see the CR-2-review section. Decide separately.)
- **Test**: `conformance/regressions/const-group-bare-inherited-overflow` (`.error`, expects "cannot assign untyped int to uint8"; currently compiles → xfailed all modes, binate `a77591e0`). ✅ Unit test added 2026-06-11 (binate `aca51964`): `check_const_test.bn` `TestConstGroupBareMemberInheritsType{RejectsOverflow,AcceptsInRange}` — a self-validating pair pinning that a bare member is range-checked at its inherited width (`128<<iota` overflow rejected; `1<<iota` in-range accepted).
- **Discovery**: 2026-06-09 CR-2-batch review (B1 + X3-constfold finders, folded); runtime-confirmed (128 then 0; explicit form rejected).

### `&slice[i]` (address-of a slice element) lowers to a wild pointer — FIXED+LANDED (binate `937ae78e`, 2026-06-05)
- **Symptom**: taking the address of a *slice*-indexed element yields a garbage
  pointer instead of the element address. `var p *uint8 = &s[0]; *p = 66`
  SIGSEGVs (the store writes through `(i8*)0x41`). Affects both `@[]T`
  managed-slices and `*[]T` raw slices; **fixed arrays `[N]T` are correct**
  (`&a[0]` works). Crashes identically compiled (bnc) and interpreted (bni), so
  the defect is in the shared IR address-of lowering, not a backend.
- **Root cause (CONFIRMED)**: the address-of path for a slice-indexed l-value
  computes the correct element address via GEP, then wrongly falls through to the
  *r-value* path — it loads the element and `inttoptr`s the byte:
  `%a = getelementptr i8, i8* %data, i64 %idx` (element address — correct) →
  `%v = load i8, i8* %a` (BUG: loads the VALUE) →
  `%p = inttoptr i8 %v to i8*` (BUG: byte → pointer). Fixed arrays take the
  proper address path (yield the GEP), which is why `&a[0]` works; slice-indexed
  operands share the load path instead. Likely in IR-gen's address-of handling
  for a SliceIndex operand (gen_expr l-value path).
- **Test**: `conformance/599_addr_of_slice_elem.bn` — `&slice[i]` write-through +
  read-back on `@[]T` and `*[]T` (mutation must be visible; currently SIGSEGVs).
  Xfailed in all 6 default modes.
- **Discovery**: 2026-06-05, while probing bundle I/O for the minbasic example —
  `__c_call("write", …, &buf[0], …)` silently wrote nothing; chasing it exposed
  the address-of miscompile. Confirmed firsthand against `bnc-0.0.7` with
  `--emit-llvm`, and **confirmed still present in local main HEAD** (2026-06-05)
  via `conformance/run.sh builder-comp` + `builder-comp-int`.
- **Fix**: the slice-indexed l-value address-of must yield the GEP'd element
  address, not load+inttoptr — mirror the fixed-array address path. (If
  `&slice[i]` were intentionally unsupported, reject at type-check instead — but
  arrays support it and raw pointers are the documented hot-path escape, so
  emitting the address is the intended fix.)

### VM drops a returned aggregate / managed-slice element of a local (`return container[i]`) — wrong-result, VM-only — FIXED + LANDED 2026-06-06 (binate `61488b48`)
- **Symptom**: under `builder-comp-int` (bytecode VM), a function that returns an
  aggregate element loaded directly from a local container — e.g.
  `func f() @[]char { var s @[]@[]char = @[]@[]char{"hello","world"}; return s[0] }`
  — returns an EMPTY/garbage value (the managed-slice element comes back empty; a
  struct array element reads garbage). The compiled backends (LLVM + native) are
  correct; only the VM is wrong.
- **Confirmed**: `conformance/regressions/return-aggregate-element-of-local` —
  expected `hello\n1\n2\n3`, VM prints an EMPTY first line then `1 2 3`. PASSES in
  `builder-comp` and `builder-comp-comp` (922/0), FAILS only in `builder-comp-int`
  (untracked — NOT xfail'd, so the default VM conformance lane is live-red on it).
- **This is the VM analog of the native aggregate-`OP_LOAD` aliasing bug** fixed in
  binate `1285683e` (PlanFrame/emitLoad now reserve an own data region so the load
  owns its bytes instead of aliasing the source, which gets RefDec'd/freed at
  function cleanup BEFORE the copy into the sret/result). That entry asserts "LLVM
  and the VM were always correct" — STALE: the VM mishandles this exact case.
- **Root cause (confirmed)**: `pkg/binate/vm/lower_memory.bn` `lowerLoad` emitted
  `BC_MOV` for a multi-word (aggregate) load — the loaded register just ALIASED the
  source pointer ("the consumer handles the bytes"). For `return container[i]` that
  alias pointed into the local's backing, which the function's cleanup RefDec'd
  (freed/zeroed) before the sret copy ran, so the return read freed memory.
- **Fix (binate `30f21816`, work-3)**: the VM frame planner (`lower_func.bn`) now
  reserves an own region for every aggregate `OP_LOAD` (`isAggregateLoadTyp`,
  matching native `common.IsAggregateTyp`); a new `BC_LOAD_AGGREGATE` bytecode copies
  the loaded bytes into that region and points the result there, so the load owns its
  bytes — mirroring the LLVM/native aggregate load (and native fix `1285683e`).
- **Severity**: MAJOR — silent wrong-result (data loss) on a routine
  `return container[i]` under the VM; VM-only (the compile path is correct).
- **Discovery**: 2026-06-06, regression-testing the `genExprOrFuncRef` CurBlock fix
  (binate `47d05c81`); unrelated to that fix (the test has no function-value types —
  same IR passes natively, failed only in the VM).
- **Tests**: `conformance/regressions/return-aggregate-element-of-local` now passes
  `builder-comp-int` (full lane 895/0, was 894/1); `TestAggregateElementLoadMaterializesCopy`
  (`lower_memory_test.bn`) pins aggregate `OP_LOAD` → `BC_LOAD_AGGREGATE`.

### ~~CRITICAL — `Identical` treats ALL type parameters as equal → generic-instantiation cache aliases `Foo[A]` and `Foo[B]` → wrong types + UNSOUND constraint accepts (miscompile)~~ — FIXED + LANDED 2026-06-13 (binate `588f6f7f`)
- **Was**: `(@Type) Identical` (`types_query.bn`) had no `TYP_TYPE_PARAM` arm; two type params hit `a.Kind == b.Kind` then fell through to the catchall `return true`, so ANY two type params compared identical. `lookupCachedInstantiation` keys the instantiation cache on `Identical`, so building `Foo[B]` after `Foo[A]` (distinct params, indices 0/1) returned the cached `Foo[A]` — the two aliased to one @Type carrying the first param's index. For any generic decl with ≥2 type params each used as a type ARG to a generic struct/interface/constraint: wrong reject (`pair[A,B](Vec[A],Vec[B])` called with `(Vec[int],Vec[bool])`) AND unsound accept (a Vec[int]/Container[int] impl accepted into the Vec[bool]/Container[bool] slot → body monomorphized with the wrong type → miscompile).
- **Fix** (`588f6f7f`): add the `TYP_TYPE_PARAM` arm — two type params identical iff same `TpOwner` (owning generic decl) and `TpIndex` (the documented (Owner, Index) identity, types.bni). Same defect class the `TYP_FUNC_VALUE` arm already fixed.
- **Root cause pre-existing, exposed by the generics work**: unreachable until the generic-struct-substitution (`0a62d3f4`) and constraint-substitution (`aef4422e`) fixes made multi-type-param instantiation signatures actually substitute — the higher-priority follow-up to that work.
- **Test**: `conformance/761_generic_two_distinct_type_params` (end-to-end `combine[int,bool](Box[int],Box[bool])` → 10) green builder-comp / -int / -comp; unit tests — a direct `Identical` type-param test (same/different owner & index) + struct-path and constraint-path accept/reject pairs. Full unit (45 pkg) + full conformance (1427) green; 0 regressions.
- **Discovery**: adversarial review of the .Parents (`298ef806`) + constraint-substitution (`aef4422e`) fixes (2026-06-13).

### Unary minus on a SUB-WORD int (`-uint8`/`-int16`/…) is mis-typed in IR-gen — FIXED + LANDED 2026-06-08 (binate `fce07ccd`, plan-cr2-1 Defect 9; the exact analog of the fixed `~` `bitnot-result-type` bug)
- **FIX (landed `fce07ccd`)**: `genUnary` MINUS arm now types OP_NEG at the operand's exact integer width (any concrete `TYP_INT`, or the checker-resolved type for an untyped literal), not just float/Width==64.  The native/VM sub-word re-narrow (`68616b20`) was already landed, so facet B is correct on every backend and facet A compiles.  Pinned by `conformance/regressions/unary-minus-subword` + a gen_expr OP_NEG sub-word width unit test, plus the exhaustive `scalar-diff/neg/{8,16,32,64}/{signed,unsigned}` differential family (binate `d64b76d0`, green on every backend).
- **Symptom (two facets, one root, like `bitnot-result-type`)**:
  - **A (invalid IR / compile error)**: `-x` for any sub-word int (`uint/int 8/16/32`) emits `sub i64 0, %x` with a hardcoded i64 zero while `%x` is i8/i16/i32 → clang rejects it (`'%x' defined with type 'i8' but expected 'i64'`). Unary minus simply does not compile for sub-word ints on the LLVM backend (all `comp`/`comp-comp`/`comp-comp-comp` + arm32 LLVM modes).
  - **B (silent wrong value)**: on the VM and native aa64/x64, `-x` computes at host width and the result keeps dirty upper bits / is the host-width negation, not the sub-word value — e.g. `-1` as `uint8` reads as host `-1`, not `255`. Silent.
- **Root cause (CONFIRMED)**: `pkg/binate/ir/gen_expr.bn:223-241` (`genUnary`, MINUS arm) sets `negTyp` defaulting to host-word `types.TypInt()` and only overrides it for floats or `Width == 64` (the int64-preserving path). A sub-word operand matches NEITHER, so `EmitUnary(OP_NEG, arg, negTyp)` carries i64 while `arg` is i8/16/32. This is the SAME mistake `~` had — and the fixed `~` entry (`bitnot-result-type`, binate `42ad4fa0`) even says its fix "mirrors `OP_NEG`," not realizing OP_NEG had the identical latent gap for sub-word.
- **Fix direction**: type the `OP_NEG` result as the operand's resolved (sub-word) type — accept any concrete `TYP_INT` width from the checker resolution / operand type, not just `Width == 64`, mirroring the `~` fix at `gen_expr.bn:247`. A one-site IR-gen change. Once it lands, the native/VM `OP_NEG` sub-word re-narrow (already landed, binate `68616b20`, plan-cr2-3 Defect 1) makes B correct on every backend, and A compiles.
- **Owner**: IR/frontend (Plan 1 territory — `pkg/binate/ir`). NOT owned by any CR2 plan as written; surfaced during plan-cr2-3 Defect 1. plan-cr2-3 Defect 1 explicitly did NOT touch `gen_expr.bn` (disjointness).
- **Severity**: MAJOR — a basic operation (negate a sized int) is broken: loud (compile error) on LLVM, silent (wrong value) on VM + native.
- **Test**: a `conformance/regressions/unary-minus-subword` cell (xfailed every mode until the IR fix) pins it. The full `scalar-diff/neg/{8,16,32}/{signed,unsigned}` generator family (reverted out of the Defect-1 commit) should be re-added when the fix lands — it goes green across all backends once IR-gen types OP_NEG correctly and Defect 1's narrow applies. (Defect 1's OP_NEG narrow is itself already pinned by the new aarch64/x64 `emitUnop` narrow unit tests, which construct a correctly-typed sub-word OP_NEG directly and so don't depend on this fix.)
- **Discovery**: 2026-06-08, adding `neg` cells to the scalar-diff differential harness as Defect-1 (sub-word unary narrow) coverage; the cells compile-errored on LLVM, exposing the upstream IR mis-typing.

### `len()` on a named-managed-slice (`type Buf @[]int; len(buf)`) — ✅ FIXED & LANDED 2026-06-11 (binate `88e13633`, increment 1)
- **FIXED 2026-06-11** (named-distinct transparency increment 1, `88e13633`): `IsSlice`/`IsPointer` now peel `TYP_NAMED` (via `peelNamedBounded`), so `len(b)` works on a named slice; `conformance/regressions/len-named-managed-slice` un-xfailed (all modes). See the named-distinct transparency entry above. Original note kept below for context.
- **Symptom**: `type Buf @[]int; var b Buf; len(b)` → checker error `len argument must be slice or array`. The `len` builtin's argument check tests the raw `Kind` (`TYP_NAMED`), never peeling to the underlying managed-slice. A wrapper-transparency miss (Code-Red-2 Class B / Invariant A) on the `len` builtin specifically.
- **Severity**: MAJOR — `len` unusable on any named-slice/array type; loud (compile error).
- **Root cause direction**: the `len`-arg type check (checker / builtin resolution) must peel `TYP_NAMED` (and `TYP_READONLY`) before testing slice/array-ness. Likely the same fix shape as plan-cr2-1's other peels.
- **Test**: `conformance/regressions/len-named-managed-slice` (xfailed all modes, binate `a77591e0`) pins the `len()` rejection. The `conformance/matrix/globals/noinit/named-managed-slice` cell reads `0` (compile-only) to isolate the codegen zero-token defect.
- **Discovery**: 2026-06-07, building the Code-Red-2 globals matrix.

### `builder-comp-int-int` (double-VM) globally broken — every test SIGSEGVs — ✅ RESOLVED 2026-06-09 (binate `c997cf2e`; root cause `71ff7489`)

- **Symptom**: EVERY `builder-comp-int-int` conformance test produces empty output and exits 139 (SIGSEGV) — including the most trivial: `001_hello` (`println("hello world")`), `002_arithmetic`, `003_variables`, bare `println(42)`. The whole int-int lane is dead, not a per-test issue.
- **Where it crashes**: the compiled `bni` (gen1-compiled `cmd/bni`) **SIGSEGVs while interpreting `cmd/bni`** — the bni-under-bni (double-VM) path. The inner VM dies at startup/load, before any test output. Reproduced manually outside the harness:
  `COMPILED_INTERP -I … cmd/bni -- -I … conformance/001_hello.bn` → exit 139, no output.
- **Not a stack limit**: a 64 MB stack (`ulimit -s 65532`) changes nothing; the crash is immediate, not a gradual overflow.
- **Single-VM is fine**: `builder-comp-int` and `builder-comp-comp-int` (one VM layer) pass normally — only the double-VM (`int-int`) crashes.
- **Scope**: `builder-comp-int-int` is in the `all` CI modeset (comprehensive lane red across ~1150 tests), NOT in `basic` (basic smoke = `builder-comp` + `builder-comp-int`, both green). So basic smoke is green; the comprehensive lane is red.
- **Pre-existing / not from Round-2 work**: crashes on field-access-free `001_hello`, which no front-end fix touches. The earlier Defect-8 note (at `a869e8e7`) characterized int-int crashing only for MULTI-package tests; it is now GLOBAL. The worsening happened somewhere in `a869e8e7..0c707e1f` (unbisected), or int-int single-package was already broken then and only the multi-package case was checked.
- **Root cause (CONFIRMED)**: `71ff7489` (the "length-0 ⟹ no backing" rep change) made the bytecode VM lower an *aggregate* `OP_CONST_NIL` — an empty string literal, an empty raw composite, `make_slice(_,0)` — to a scalar `0`, i.e. a NULL address. The VM carries every aggregate value (slice / struct / iface- / func-value) by the ADDRESS of its in-memory image, so any by-address consumer (a call argument, an `OP_EXTRACT` such as `len()`) read through null. Single-VM only tripped on test programs that actually hit that path (e.g. `110_cross_pkg_type_alias`); under double-VM the inner program *is* `cmd/bni`, which uses empty literals by-address during load → universal null-deref at startup (hence even `001_hello` SIGSEGV'd, before any test output). The suspected `a869e8e7..0c707e1f` range and the `68616b20` candidate were red herrings — the culprit `71ff7489` predates that range, so "int-int single-package was already broken then" (per the bullet above) was the correct read.
- **Discovered**: 2026-06-09 while validating CR-2 Plan-1 Round-2 (R2-D1). Per the user (2026-06-09): FILE this; do NOT add per-cell `.xfail.builder-comp-int-int` to new Round-2/Plan-A cells (the whole lane is down — per-cell xfails would be noise that falsely reads as a known per-cell issue). Validate Round-2/Plan-A fixes on the other 6 runnable modes; the cells are mode-agnostic and pass int-int once this is fixed.
- **RESOLVED 2026-06-09 (binate `c997cf2e`)**: the VM now reserves a dedicated zeroed frame region for each aggregate `OP_CONST_NIL` (mirroring native's dedicated data region and LLVM's alloca + zero-fill), so the value's register is a valid address of a `{0,…}` image. This is the SAME commit recorded elsewhere in this file as fixing the single-VM `110_cross_pkg_type_alias` regression — the int-int entry just wasn't connected to it. Bisect-verified: int-int `001_hello` SIGSEGVs at `c997cf2e^` (`b4d5b37b`) and passes at HEAD; the full int-int sweep is green at HEAD (1245 passed, 0 failed, 48 xfail-skipped).

### `71ff7489` (length-0 slices → nil-equivalent rep) regressed the bytecode VM — `110_cross_pkg_type_alias` fails on `builder-comp-int` (a default CI mode) — RESOLVED 2026-06-09 (plan-cr2-3 Round-2, binate `c997cf2e`)
- **Symptom**: `conformance/110_cross_pkg_type_alias` fails on `builder-comp-int`: the final `if mylib.IsEmpty(MakeResult("")) { println("empty ok") }` does NOT print, so the output is missing the `empty ok` line. `IsEmpty(r)` is `len(r) == 0` over an empty `@[]char` produced by `make_slice(char, len(""))` — the VM reads `len != 0` for the empty managed-slice. Green on `builder-comp` (LLVM); fails ONLY on the VM. No xfail marker (was passing).
- **Bisect (CONFIRMED)**: `110` PASSES on `builder-comp-int` at `43cb195d` (71ff7489's parent) and FAILS at `71ff7489` / `cc2ddcc4`. So `71ff7489` ("ir: enforce length-0 slices have no backing (nil-equivalent rep)") is the cause.
- **Mechanism (direction)**: `71ff7489` made empty string/byte literals emit `EmitConstNil`, and normalized `lo==hi` subslices / empty raw composite literals to the `{null,0}` nil-equivalent. The VM (`pkg/binate/vm`) was not updated to AGREE with the new length-0 rep, so either `len("")` (empty-literal-as-nil) or `len(make_slice(char,0))` reads non-zero on the VM and the emptiness check inverts. LLVM/codegen handle the new rep; the VM lowering/runtime does not.
- **Severity**: MAJOR — breaks a default CI mode (`builder-comp-int`) with wrong output on the idiomatic `len()==0` empty-slice test through the VM. Narrow blast radius: the full-suite VM sweep showed ONLY `110` failing.
- **Discovery**: 2026-06-08, plan-cr2-3 post-landing full-suite `--check-xpass` sweep on both arches + LLVM + VM (the only VM failure in the full suite).
- **Root cause (confirmed)**: the bytecode VM carries every aggregate value (slice / struct / iface- / func-value) by the ADDRESS of its in-memory image, but lowered EVERY `OP_CONST_NIL` — scalar AND aggregate — to `BC_LOAD_IMM 0`. For an aggregate const-nil that 0 is a null address; a by-address consumer (a call argument, an `OP_EXTRACT` such as `len()`) reads through null. The var-decl form (`666`) was masked because `OP_STORE` of a const-nil memsets the destination directly and never reads the source register; `MakeResult("")`'s direct call-argument form was not. The codegen + native backends already give an aggregate `OP_CONST_NIL` a dedicated zero-filled data region; the VM was the lone backend that didn't.
- **Fix (binate `c997cf2e`)**: the VM planner (`lower_func.bn`) reserves a dedicated frame region for each aggregate `OP_CONST_NIL` — zeroed at frame entry by `pushFrame`, never written — and the lowering (`lower_instr.bn`) points `BC_STACK_ALLOC` at it, so the nil value's register is a valid address of a `{0,...}` image. Scalar nils stay the immediate 0. `110` green on `builder-comp-int`; new `conformance/668_empty_slice_byaddr` isolates the mechanism (direct `len()`, call-argument, empty composite literal, `make_slice(_,0)` by argument) green on LLVM + all three `-int` modes and fails pre-fix; full `builder-comp-int` sweep 1165/0; VM unit tests pass.

### VM mis-unpacks a SUB-WORD (uint16) multi-return returned through interface dispatch — SILENT wrong values — ✅ RESOLVED by the CR-2 SEAM (`6c39d460`)
- **STATUS 2026-06-08 — RESOLVED.** This was the symptom pre-SEAM, when the front-end dropped the iface multi-return result type (void-typed dispatch) so the VM's tuple lowering operated on a malformed shape; the symptom presented AS a sub-word `BC_EXTRACT` width bug (`13107300 = (200<<16)|100` for `(u16,u16)→(100,200)`). Once the SEAM typed the dispatch as a proper tuple struct, the VM lowers it correctly — `iface-multi-return/u16/{2,3,4,5}` pass on all three `-int` modes (verified 0-failed under `--check-xpass`; the SEAM removed the VM xfails). So the separately-planned VM `BC_EXTRACT` sub-word fix (plan-cr2-3 "Defect 5") is MOOT — the VM's value-mode `BC_EXTRACT` already does a sized sub-word read; the bug was upstream typing, not VM extract.
- **Original symptom (historical)**: `iface-multi-return/u16/2` printed `13107300, 1` on the VM instead of `100, 200`. The `int` variant was correct, which is why it read as sub-word-specific.
- **Discovery**: 2026-06-07, abi result-side matrix sweep. **Resolution confirmed**: 2026-06-08, post-SEAM `--check-xpass` sweep of the abi subtree on the three `-int` modes (0 failed).

### Native widening int casts don't sign/zero-extend from the SOURCE width — silent wrong value for a non-canonical source — FIXED 2026-06-05 (binate 445d846a)
- **Symptom**: a widening integer cast (`cast(int, <int32 x>)`, sub-word →
  host-word) on both native backends does NOT re-extend the value from the
  source width; it just MOVs, assuming the source register is already
  sign/zero-canonical. The VM (`BC_SEXT`/`BC_ZEXT`) and LLVM (`sext`/`zext`)
  extend per the source type, so this is a native-only divergence — a silent
  wrong value whenever the source register is non-canonical.
- **Root cause**: `emitCast` (aa64 `aarch64_ops.bn:476`, x64 mirror) keys ONLY
  on the TARGET width: for `target.Width == 0 || >= 64` it emits a plain MOV
  (no extension); the sub-word LSL+ASR/LSR path only runs for a *narrowing*
  target. It never receives the source type, so it cannot extend-from-source on
  a widening cast.
- **Why it surfaced now**: post-4.1 (sub-word arith narrowing), arith results
  ARE canonical, so `cast(int, arithResult)` is correct via the MOV. But a
  `bit_cast(int32, <float32 const>)` result is left ZERO-extended (bit_cast is a
  plain reinterpret MOV), so `cast(int, bit_cast(int32, Neg))` keeps the
  zero-extended bits → `println` prints `3184315597` instead of `-1110651699`.
  This is the residual on **conformance/539_float32_const** (xfailed on all 3
  native lanes; the 4 non-negative lines pass; passes on VM + LLVM).
- **Fix (LANDED 445d846a)**: thread the source type into `emitCast` on both
  natives; on a widening cast (target host-word), sign/zero-extend from the
  SOURCE width per the source's signedness — mirroring the VM's `BC_SEXT`/
  `BC_ZEXT`. Narrowing casts keep the target-width behavior. No-op for canonical
  sources (scalar-matrix cells unaffected). The fix at the CAST is the right
  layer — do NOT narrow at OP_BIT_CAST instead (that would also touch the
  compiler's internal pointer bit_casts; the cast site is where the widening
  semantics belong).
- **CORRECTION — the earlier "blocked by a self-compilation break" conclusion
  was WRONG**: I had attributed a ~267/796 aa64 conformance wipeout (`bnc` link
  error `_bn_pkg__bootstrap__Write` undefined) to this fix. That breakage is the
  **separate, already-tracked CRITICAL aa64-native lane regression** (from the
  divide-fault guard series) — my experiments were rebased onto a base that
  already had it. There is NO hidden cmd/bnc cast/bit_cast dependency. Proof: the
  fix on the **clean x64_darwin lane** gives 807 passed / 4 failed (only the 4
  unrelated pre-existing failures, NOT 267), and 539 passes. The aa64 lane can't
  confirm until its CRITICAL issue is resolved, but 539 passed there too and the
  aa64 emitCast uses identical logic.
- **Test**: `conformance/539_float32_const` — now green on all modes (native
  xfails dropped). A direct `cast(int, bit_cast(int32, <high-bit u32>))`
  regression cell would harden it further.
- **Severity**: was MAJOR (silent wrong value, native-only). Resolved.

### Bytecode VM `@Iface` (interface) value handling — two VM bugs — FIXED 2026-06-03
- **Part A — single interface-value return not copied back → "call through nil interface value"** (binate `511e1395`).  Interface values are 16-byte address-based VM stack slots.  `lowerReturn` set BC_RETURN's copy-back size only for `isMultiWordField` types (struct / slice / array) — it omitted interface values, so a single `@Iface` return dangled in the reclaimed callee frame and the next call clobbered it; `consume(makeFoo(i))` (an iv call result passed directly as an arg) then panicked `vm: call through nil interface value` in `-int` only (LLVM + native don't use this lowering).  Fix: set the copy-back size for `TYP_INTERFACE_VALUE` / `_MANAGED` single returns too.  Pinned by `560_iface_return_call_arg` (green all modes).
- **Part B — interface-value receiver dtor crashed on RefDec-to-zero** (binate `5de3d09d`, the direct analogue of the `@func` capture-record dtor `0a0d00af`).  `BC_IFACE_DTOR` produced the receiver dtor's 1-based func index, but `BC_REFDEC_INLINE_FAST` consumes its dtor input as a func-value HANDLE — so an interface value that was the *last* holder of a managed-field receiver bit_cast the small index to a pointer and crashed (520; the dtor arms of 554 / 556).  473 hid it because its iv lives in a nested block the receiver outlives, so its RefDec never reached zero.  Fix: `BC_IFACE_DTOR` hands `BC_REFDEC` the dtor func's handle via `ensureHandle` (the same `{Vtable, ClosureRec{VM_CLOSURE_REC, FnIdx}}` the `@func` path uses); the existing iterative-push arm runs the receiver dtor and frees it via `freeOnPop`.
- **Result**: `520_iface_dtor_callee_sole_ref` (a standing `-int` red) is green; `554_iface_refcount_balance` and `556_iface_struct_field_balance` un-xfailed in all VM modes; `-int` suite 478/0.  Both were `pkg/vm`-only (codegen always emitted correct IR; LLVM + native were already correct).

### MAJOR — `++`/`--` on a non-identifier lvalue (`a[i]++`, `p.f++`, `*p++`) type-checks clean but generates NO code (silent no-op) — spec Ch.14 (2026-06-12) — ✅ FIXED+LANDED (binate `6a2f551f`, coverage `124a0b40`)

`genIncDec` (`gen_flow.bn`) now lowers every integer lvalue kind — ident,
selector (`p.f++`, incl. value-struct field), index (`a[i]++` / `s[i]++` /
nested `m[i][j]++`), and deref (`(*p)++`) — by computing the storage address
(via `genSelectorPtr` / `genIndexPtr` / the deref'd pointer, the same helpers
`genAssign` uses) and read-modify-writing. The integer-lvalue restriction
means no managed/slice/struct handling is needed. `conformance/739` covers
array / named-array / slice elements, value + managed-ptr struct fields,
nested-array, deref, and sub-word two's-complement wrap; green builder-comp /
VM / gen2.

Found + verified firsthand while grounding spec Ch.14 (Statements). The SAME
class as the parallel-assignment drop above (checker accepts a general lvalue;
IR-gen only lowers the identifier case), but a distinct code path.

- **Checker accepts any integer lvalue.** `checkStmt`'s `STMT_INC_DEC` arm
  (`check_stmt.bn:39-65`) checks only `s.X.IsInteger()` and rejects const
  identifiers/qualified-consts; it imposes **no** identifier restriction, so
  `a[i]++` / `p.f++` (element/field of integer type) pass clean.
- **IR-gen drops non-idents.** `genIncDec` (`gen_flow.bn:214-231`) is gated
  entirely on `if stmt.X.Kind == ast.EXPR_IDENT { ... }`; for any other lvalue
  the `if` never fires and the function `return b`s with **no IR**. So
  `counts[i]++`, `node.count++`, `(*p)++` compile to a no-op — no diagnostic.
- **No conformance coverage.** Inc/dec tests only exercise identifier operands
  (`x++`, `count++`, `i++`); no `a[i]++` / `p.f++` test exists.
- **Severity**: MAJOR silent wrong-code — `counts[i]++` (histogram) and
  `node.count++` (field bump) are common idioms that silently do nothing.
- **Fix**: implement the non-identifier lvalue arms in `genIncDec` (mirror the
  index / selector / deref lvalue arms `genAssign` already has — load through the
  element/field/deref pointer, `± 1`, store back). Reject-instead (option B) is
  worse here: `a[i]++` is a legitimate lvalue mutation, not a thing to forbid.
  Either way the current accept-then-drop is the bug. Add conformance for
  `a[i]++` / `p.f++` / `(*p)++` with the fix.

### Zero-parameter functions accept any number of arguments — spec Ch.10 (2026-06-12) — ✅ RESOLVED 2026-06-12 (binate `29fdc4c0`)
RESOLUTION: restricted the no-arity-check branch (`check_expr.bn`, numParams==0 && numArgs>0) to the variadic builtins via a new `isVariadicBuiltinCall` (callee name print/println/panic, mirroring `isPanicCall`); any other zero-param call with args is now "too many arguments". conformance/741_zero_param_arity_rejected; print/println/panic still accept args (covered by the whole existing suite). Original report below.
MINOR (over-permissive arity check; extra args are evaluated then discarded —
not a miscompile, but should be a diagnostic). `check_expr.bn:369` keys the
no-arity-check path on `numParams == 0 && numArgs > 0` for the GENERAL call
path, not just the empty-parameter builtins (print/println/panic). So a user
`func f()` called as `f(1, 2)` type-checks clean (the args are checked for
side effects, then ignored; `f` is called with no args). It should be a "too
many arguments" error. Functions with >=1 parameter correctly require exact
arity (`check_expr.bn:373`). Rule `func.call.zero-param-arity` in the spec
(`10-functions-methods-function-values.md`).

### Bare func literal in assignment position doesn't infer its managed/raw flavour from the LHS — ✅ RESOLVED 2026-06-10 (binate `e15680d7`)
- **✅ RESOLVED `e15680d7`** — the simple-assign RHS is checked via
  `checkExprWithFVHint(c, rhs, lhsType)`, so a bare func-literal `existing =
  func(){…}` (where `existing @func(...)`) now picks up the managed/raw flavour
  from the LHS, like var-init already did. NOTE: the NAMED func-value spelling
  (`type Fn @func(...)`) is still broken — the hint doesn't peel `TYP_NAMED` —
  tracked separately as the B2 MAJOR entry above.
- `existing = func(){...}` where `existing @func(...)...` fails type checking
  with `cannot assign <unknown> to <unknown>`: a bare func literal in
  **assignment** (non-var-init) position does not pick up its managed
  (`@func`) vs raw (`*func`) flavour from the assignment target's type.
  Var-init works (`var x @func(...)... = func(){...}` — the declared type
  hints the flavour).
- **Workaround in use**: assign through a typed var
  (`var drop @func(...)... = func(){...}; existing = drop`) — see
  `conformance/587_closure_captures_func_value.bn` and
  `conformance/matrix/assign/ident/func-value.bn`.
- **Fix**: in the assignment type-checker, flow the LHS func type's flavour
  to a bare func-literal RHS — the same hinting var-init already applies.
- Surfaced 2026-06-05 while authoring the conformance matrix func-value cell
  (plan-code-red.md §7 / P1).

### Float-component multi-return mis-packed on the native backends — packed into INTEGER regs, not D0/XMM0 — native↔LLVM ABI divergence — ✅ RESOLVED 2026-06-10 (float64 `b5911fbe`; x64 field-per-register rework `47ebdbac`; verified on main — `(int,f64)`, `(f64,f64)` HFA, `(f32,f32)` HFA, and iface-dispatch `(f64,f64)` all pass on builder-comp + native aa64 + native x64-darwin). Residual aa64/x87 ≥3-float-component gaps tracked in the RESIDUAL GAPS bullet below.
- **STATUS 2026-06-09 — float64 RESOLVED & LANDED (binate `b5911fbe`).** Native pack + collect now assign each leaf to the next register of its CLASS: aa64 `emitReturn` (FP counter D0.. alongside GP X0..) + a shared `collectMultiReturnFields` routed from all four collect sites (direct/iface/funcval/call-indirect, which were four copies of the integer-only loop); x64 `emitMultiReturnPack` builds the full byte image then loads each eightbyte by class (new `multiReturnEightbyteIsSSE`, SysV two-eightbyte rule) with `collectMultiReturnTuple` the symmetric mirror. `conformance/683_cross_pkg_mr_float` ((int,float64)+(float64,float64) collected by native main from an LLVM pkg) fails pre-fix / passes post-fix on both native arches; green LLVM+VM. `gen-abi-matrix.py` gained an `f64` axis; full abi matrix green native aa64 + x64-darwin.
### A named fixed-array type (`type Row [3]int`) — ✅ FIXED & LANDED 2026-06-11 (parser `722b804f` + IR-gen `68d24423`)
- **FIXED 2026-06-11** (part of the named-distinct transparency work above): the parser implements grammar D11's two-token lookahead in `parseTypeSpec` (`722b804f`), so `type Row [3]int` parses as an array distinct type (not generic type-params). That surfaced a sibling MAJOR codegen bug — IR-gen's array index/len/slice/store path didn't peel `TYP_NAMED` — fixed in `68d24423` (`peelTransparent` at every array site). `conformance/723_named_array_type` (index write/read, `len`, by-value param, array slice) green on builder-comp + builder-comp-int. Original investigation note kept below for context.
- **Symptom**: `type Row [3]int` → parse error `expected IDENT, got INT` / `expected type`. After `type Row [`, the parser commits to the TypeParams form (`type Row [T U] …`), which requires an identifier, so a fixed-array size (an integer) is rejected. You cannot name a fixed-array type at all; `type Buf @[]int` (managed-slice) and `type S struct{…}` parse fine — only the `[N]T` array form collides with the generic-params `[ident ident]` syntax.
- **Root cause**: the `[`-after-type-name disambiguation in the parser (grammar `TypeDecl`/`TypeSpec`, the `[` → ArrayType-vs-TypeParams ambiguity noted in grammar.ebnf ~158-164). The parser must look past `[` for an integer/expression (ArrayType) vs two identifiers (TypeParams).
- **Severity**: MAJOR — a whole type-construction form is unavailable; loud (parse error), workaround is to use the structural type inline.
- **Test**: the `conformance/matrix/globals` `named-array` cell is omitted for this reason; a `conformance/regressions/named-array-type` point-test would pin it.
- **Discovery**: 2026-06-07, building the Code-Red-2 globals matrix.

### float32 ops (arithmetic, negate, comparison) were computed in double precision on the f32 bit pattern — FIXED/LANDED 2026-06-06 (binate df7a5ec1, 12a24e74, fc11d862)
The VM and both native backends computed float32 `+ - * /`, unary negate, and all six comparisons as float64 on the raw f32 bit pattern (the low-4-byte f32 bits reinterpreted as a double), producing garbage — a silent miscompile (LLVM was always correct). All three now compute at single precision:
- **arithmetic** (df7a5ec1): native single-precision ops (aa64 FADD/FSUB/FMUL/FDIV `_s` ty=00 encoders; x64 ADDSS/SUBSS/MULSS/DIVSS) and VM `BC_F32ADD/SUB/MUL/DIV`.
- **negate** (12a24e74): aa64 FNEG `_s`; VM `BC_F32NEG` (sign-bit XOR); x64 already XOR'd the f32 sign bit.
- **comparison** (fc11d862): aa64 FCMP `_s`; x64 UCOMISS; VM `BC_F32EQ/NE/LT/LE/GT/GE` — NaN-unordered semantics preserved via the shared condition logic.
- **Tests**: `conformance/635_float32_arith` (4 binops + negate + bit-exact `1.0f/3.0f` rounding) and `639_float32_compare` (negative operand exposes the order-flip + runtime NaN unordered/ordered checks); green on every lane.  Golden-encoding tests for all new native encoders.  The `builder-comp_native_x64` x86_64-linux 635 marker is retained (unverifiable without qemu-user on this arm64 host; same x64 codegen as the passing x64_darwin lane).
- **NOT broken / unchanged**: float32 CONST materialization (539), float32 RETURN bits (636), and float32 CASTS (`BC_F32TOSI` / `emitFloatCast` carry width) were already correct.  Discovered by the Plan-4 + float32-arithmetic adversarial reviews.

### Field access into an anonymous (multi-return tuple) struct miscomputes the LLVM GEP index when a field has alignment padding before it — FIXED 2026-06-03 (binate `5f4a8eaf`)
- **What**: `emitGetFieldPtr` (`pkg/binate/codegen/emit_helpers.bn:118`) maps the
  Binate field index to the LLVM field index via `structLLVMIndex` (which counts
  inserted `[N x i8]` padding fields) **unconditionally**.  But anonymous
  multi-return tuple structs are emitted by `llvmType()` in the non-packed
  `{...}` form **without** explicit padding fields — so for them the Binate index
  already IS the LLVM index.  When such a tuple has a field with
  `PaddingBefore > 0` (a pointer/aligned field following a sub-word field like
  `bool`/`i1`), the mapping overshoots by the number of preceding padding gaps.
- **Symptom**: a `(bool, @errors.Error)` multi-return (e.g. `strconv.ParseBool`)
  generates its anon-tuple destructor `__dtor_anon_bool_unknown` with
  `getelementptr inbounds {i1, %BnIfaceValue}, ... i32 0, i32 2` — index 2 into a
  2-field struct → `error: invalid getelementptr indices`, clang fails.  If the
  overshoot had landed in-bounds it would be a SILENT wrong-field access instead.
- **Root cause**: `emitGetFieldPtr` is the lone `structLLVMIndex` caller missing
  the named-vs-anonymous guard.  The SSA copy paths already do it right:
  `emit_copy_ssa.bn:103` and `emit_copy_ssa_load.bn:85` apply `structLLVMIndex`
  only `if named` (`named = len(t.Name) > 0`) and otherwise use the raw index.
- **Fix**: `emitGetFieldPtr` now gates the `structLLVMIndex` remap on
  `len(baseTyp.ResolveAlias().Name) > 0` — named structs remap past padding
  fields; anonymous tuples use `instr.Index` directly.  Mirrors the
  named-vs-anonymous split already in `emitStoreSSARec`.  `pkg/codegen`
  function-body change (BUILDER-safe).
- **Affects**: LLVM backend (the GEP-index path).  VM uses byte offsets and was
  unaffected (conformance 144 passes on `builder-comp-int` as well as
  `builder-comp`).
- **Discovery**: 2026-06-03, implementing `strconv.ParseBool` (first
  `(bool, @errors.Error)` multi-return).  Had blocked `ParseBool`; the rest of
  the Parse series (`int64`/`uint64`/`float64` first elements — pointer-aligned,
  no padding) was unaffected.
- **Tests**: codegen unit test `TestAnonTupleDtorFieldGepIndex`
  (emit_refcount_test.bn) pins the GEP index; `conformance/144_multi_return_bool_iface`
  covers it end-to-end (green on LLVM + VM).

### Cross-package function returning `@Iface` resolves the return type to a managed pointer (`i8*`) in the consumer → ABI mismatch — FIXED 2026-06-03 (binate `cb8c0f1a`)
- **Symptom**: a consumer that imports a package and calls a function declared (in the `.bni`) to return a managed interface value — e.g. `errors.New(msg) @Error` / `errors.Wrap(...) @Error` — fails to compile with LLVM verifier error `extractvalue operand must be aggregate type` on `%v6 = extractvalue i8* %v5, 0`, because the consumer lowers the call as `call i8* @bn_pkg__std__errors__New(...)` (single pointer) while the callee's real ABI returns a 16-byte `%BnIfaceValue` (register pair).  The consumer's own refcount/copy machinery *correctly* treats the OP_CALL result as an interface value (hence the `extractvalue …, 0` to RefInc the data field), so the call-return-type and the copy machinery disagree inside one module.
- **Root cause (CONFIRMED)**: `isInterfaceTypeExpr` / `ifaceTypeForName` (`pkg/binate/ir/gen_iface.bn`) resolve a **bare** interface name (`te.Pkg` empty) by looking it up in `moduleInterfaces` only under `currentModulePkgPath` (the *consumer's* package) — never under `currentImportAlias` (the package whose `.bni` decls are currently being registered, `gen_import.bn:registerImportFieldsAndFuncs`, which sets `currentImportAlias = alias`).  The imported interface is registered (by `collectInterfaceFromDecl`) under its full path (`resolveImportPkg(alias)` = `pkg/std/errors`).  So while registering `errors.bni`'s `func New(...) @Error`, `resolveTypeExpr(@Error)` calls `isInterfaceTypeExpr(Error)` → lookup `("main","Error")` MISS → falls through to `MakeManagedPtrType` (`gen_util.bn:349`) → `llvmType` = `i8*`.  The struct / `TEXPR_NAMED` path already consults `currentImportAlias` (`gen_util.bn:271–283`, mirrored in `gen_const.bn:85`); the interface path does **not** — that asymmetry is the entire bug.
- **Why never caught**: errors is the FIRST cross-package function whose return type is an interface value.  The mis-resolution is INVISIBLE for managed-pointer (`@T`) and managed-slice (`@[]T`) returns — those lower to `i8*` / `%BnManagedSlice` whether resolved correctly or as the managed-ptr fallback — and strconv/big return exactly those.  An interface value is the first return type where correct (`%BnIfaceValue`, 2-word) and fallback (`i8*`, 1-word) diverge.  In-package compilation is fine (there the interface is under `currentModulePkgPath`), so `pkg/std/errors` itself builds; only the consumer mis-resolves.
- **Severity**: MAJOR — a cross-package ABI mismatch.  Here the LLVM verifier happens to reject it (the copy machinery's `extractvalue` on an `i8*`); on any codegen path that does NOT extractvalue the result (e.g. a `@Iface`-returning function whose result is only stored/passed, not retained at the call site) it would be a **silent miscompile** — caller reads a 1-word return, callee wrote a 2-word value.  Also affects `*Iface` returns by the same path.  (Almost certainly also `@func` / `*func` returns from a cross-package function whose signature spells the func-value type via a NAMED alias — not the structural `@func(...)` form, which resolves context-free — though unconfirmed.)
- **Fix (landed `cb8c0f1a`)**: in `isInterfaceTypeExpr` and `ifaceTypeForName` (`gen_iface.bn`), a bare name that misses under `currentModulePkgPath` now also tries `currentImportAlias` (keying the produced `TYP_INTERFACE` on the resolved full path), mirroring `gen_util.bn`'s `TEXPR_NAMED` arm.  Test: `576_cross_pkg_iface_return` (and the `577_std_errors` cross-package suite).
- **Discovery**: 2026-06-03, implementing `plan-std-errors.md` Part 1 (`pkg/std/errors`).  Pre-existing latent bug, exposed by the first cross-package interface-value return.

### Conformance int-int mode: `136_grouped_imports` + `383_cross_pkg_iface_dtor` fail with "pkg/builtins/rt not found" — FIXED+LANDED (binate `db18f26b`, 2026-06-05)
- **Symptom**: on `builder-comp-int-int` (the double-VM default mode),
  `136_grouped_imports` and `383_cross_pkg_iface_dtor` fail at compile time
  with `package "pkg/builtins/rt" not found`.  Both PASS on `builder-comp-int`
  and `builder-comp-comp-int`; the other ~468 int-int tests pass.
- **Pre-existing**: confirmed on clean `17c722d1` (reproduced with the
  pre-float-fix VM tree), so NOT caused by the float-constant work; it is a
  recent main regression in the int-int package-resolution path.
- **Root cause (unknown)**: only certain multi-package tests can't resolve
  `rt` in the int-int pipeline; needs investigation of how that mode locates
  the `rt` package (vs the single-int / comp-int modes that succeed).
- **Discovery**: 2026-06-03, full-suite regression sweep while landing the
  float-constant fix (536).
- **Severity**: MAJOR — a default conformance mode is red, masking real
  coverage on those tests.

### Checker does not fold `iota` in expressions — bit-flag const COMPILE-TIME values stay plain-iota — ✅ RESOLVED (binate `05901f97`, 2026-06-09)
- **STATUS 2026-06-09**: FIXED exactly per the fix sketch below (both parts). `checkIdent` returns `makeUntypedIntWithLit(c.Iota)` for `iota`; `checkGroupDecl` repeats the previous explicit member's initializer (re-folded at the current iota) for bare members. The pre-flagged tightening is now in effect (`var x uint8 = B8` with `B8 = 1<<8 = 256` is correctly rejected). No existing unit/conformance cell changed (151 const/iota/enum cells green). Tests: conformance `672_err_iota_bitflag_overflow` + two unit tests in `check_expr_constfold_test.bn` (CR-2 Plan-B).
- **Symptom**: iota-repeat (binate `52a9eabf`) gives correct RUNTIME values for bit-flag consts (`const ( B0 int = 1 << iota; B1; B2 )` -> 1,2,4 at runtime). But `checkIdent` returns a plain `TYP_UNTYPED_INT` for `iota` (no `HasLitVal`), so the checker never folds an iota expression to a value: a bare member is given the plain-iota value via `makeUntypedIntWithLit(c.Iota)`; an explicit `1 << iota` member gets no value. So a bit-flag const's COMPILE-TIME value (array dimensions, assignability/overflow checks) is wrong/absent -- e.g. `var x uint8 = B10` with `B10 = 1 << 10 = 1024` is wrongly accepted because the checker thinks `B10 = 10`.
- **Scope**: compile-time only; runtime values are correct (IR-gen). The dominant `= iota` enum idiom is unaffected (plain-iota == iota-repeat there). Affects only bit-flag-style consts used as array dims or in narrow-type checks -- rare.
- **Fix sketch**: fold `iota` in `checkExpr` (return `makeUntypedIntWithLit(c.Iota)` from `checkIdent`), and have `checkGroupDecl` re-check a bare member's repeated previous expression with the current iota so its symbol value matches IR-gen. Watch for new overflow errors on large iota enums assigned to narrow types.
- **Discovery**: 2026-06-05, while implementing iota-repeat (Plan 1 / 1.3d).

### Untyped single const (`const X = 5`) is not forward-referenceable — FIXED+LANDED (binate `99057185`, 2026-06-05)
- **Symptom**: a top-level untyped single const with no explicit type
  (`const X = 5`) reports `undefined` when referenced from a decl
  checked BEFORE it — a forward reference within a file, or a sibling
  file ordered ahead of it (package files are merged).  `const X int = 5`
  (typed) does NOT have this problem.
- **Relationship**: the sibling of the const-GROUP bare-iota-member bug
  fixed in binate `88c9c0b7` — same root cause, `collectDecls`
  (`pkg/binate/types/check_decl.bn`) only forward-registers consts whose
  `TypeRef != nil`.  The group fix handled bare iota members (always
  untyped int → trivial untyped-int placeholder); this single-const case
  was left because it is **harder**: an untyped single const's type
  depends on its VALUE, and naively `checkExpr`-ing the value during the
  collection pass would emit spurious `undefined` errors for
  reference-valued consts (`const X = Y; const Y = 5`, where Y is checked
  after X).
- **Discovery**: 2026-06-02, characterizing the completeness of the
  group fix (a probe test, `TestForwardRefUntypedSingleConstKnownGap` in
  `pkg/binate/types/check_decl_test.bn`, asserts the current buggy
  behavior so the suite stays green).
- **Why MAJOR (loud, not silent)**: compile-time `undefined`, not a
  silent miscompile.  Lower-priority than the group case in practice —
  untyped single consts forward-referenced are uncommon (most code
  writes `const X int = …` or uses a group).
- **Proposed fix direction**: in `collectDecls`, for an untyped single
  const, forward-register the name when the value is a simple LITERAL
  (int / string / float / bool / char) whose type is unambiguous and
  dependency-free; leave reference / expression values for a later pass
  (or a two-phase const resolution).  Avoids the spurious-error trap.
- **Tests covering it**: `TestForwardRefUntypedSingleConstKnownGap`
  (flip to `expectNoErrors` when fixed); add a conformance test mirroring
  `526_forward_ref_iota_const` for the single-const case as part of the
  fix.

### (b1) Class 2 matrix — VM 16-byte address-aggregate (iface / func value) handling — ✅ REALIZED 2026-06-05 (binate `12d6782f`)
- **Realized**: `conformance/matrix/addr-aggregate` (generator
  `gen-addr-aggregate-matrix.py`). Axes `kind (@func / @Iface) × operation
  (direct / copy / return / arg / return-arg / field / array-elem)`; assertion:
  both words of the 16-byte value survive the boundary, observed by invoking it
  (→ 42); a dropped/swapped word faults or returns wrong. 14 cells.
- **Result**: all 14 green on `comp` (LLVM), `int` (VM), and x64-native — the
  Class-2 fixes that landed in P2 (the VM func-value nil-vtable `e337e413`, the
  2-word-slice-len-drop) hold across the grid; this is regression coverage, no
  new defects. aa64-native is collateral-red on the self-hosting `BNC_NATIVE`
  miscompile (separate CRITICAL), not these cells.
- **Note**: the `field`/`array-elem` cells store an already-typed value (a bare
  func literal in those positions trips the separate filed bare-func-literal
  flavour-inference MINOR, not 2-word survival).

### `readonly`-wrapped slice argument mis-classified → SIGSEGV/garbage on clean code — ✅ LANDED binate `487fb95c` 2026-06-10 — was MAJOR
- **Symptom**: passing a string/managed-slice value through a `readonly`-wrapped slice parameter mis-classifies it as a non-slice scalar. `func lenMro(s readonly @[]readonly char) int { return len(s) }; lenMro("cde")` COMPILES CLEANLY but SIGSEGVs on native (aarch64 / x86-64) **and** LLVM, and returns a wrong length on the bytecode VM.
- **CORRECTED diagnosis** (the original reading-review entry above was WRONG; empirical reproduction reclassified it): this is NOT primarily an `OP_CONST_NIL` defect, and the VM const-nil path was actually fine. The real bug is a family of **un-peeled `readonly` at coercion / shape-classification points** (`readonly` is IR-transparent — same representation — so every aggregate-vs-scalar decision must peel it). The const-nil only appears for the *empty* string sub-case; the dominant trigger is the bare-pointer-passed-as-aggregate at `coerceArg`. The earlier "native const-nil region predicate" claim was stale — `common.IsAggregateTyp` already peels (R2-D4 `c6fe0914`) and the native region+lowering both consult it, so native const-nil was already correct.
- **Fix (4 sites, all in `7f53b9ce`)**: (1) `coerceArg` (`ir/gen_call.bn`) string→chars / nil→slice gates use `isSliceType` (peels readonly), managed→raw gate peels readonly — gating on bare `paramTyp.Kind` skipped the conversion, passing the bare string-literal pointer where the callee reads a 4-word aggregate by address. (2) VM `isAggregateLoadTyp` (`vm/lower_instr_helpers.bn`) → new `vmPeelTransparent` (alias+readonly+named), mirroring native `common.peelTransparent`. (3) VM `lowerStore` (`vm/lower_memory.bn`) value-type classification peels transparent — a readonly managed-slice param copied into a local was storing one scalar word. (4) LLVM `constNilLLVMTypeName` (`codegen/emit_const_nil.bn`) peels outer readonly/alias — an empty `readonly @[]readonly char` was emitting scalar `inttoptr 0`, mismatching the `%BnManagedSlice` consumer.
- **Tests**: `conformance/688_readonly_slice_param` (empty+non-empty string literals at `readonly @[]char` / `readonly @[]readonly char` / `readonly *[]readonly char` params + readonly-typed locals), green on LLVM / native-aa64 / native-x64-darwin / VM; unit tests pin `isAggregateLoadTyp` + `constNilLLVMTypeName` peeling. Full conformance: LLVM 1317/0, VM 1295/0, native-aa64 1294/0.
- **Note**: `coerceArg`'s managed→raw readonly-peel is currently unreachable from source (the `@[]T → readonly *[]T` assignment is FE-rejected — see the assignability over-rejection finding below); the peel is correct and forward-looking.

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

## Archived adversarial-review records — CR-2 Plan-1 / Round-2 / follow-up batch (2026-06-08/09)

Completed adversarial-review writeups moved from [claude-todo.md](claude-todo.md). Each
RESOLVED finding referenced here also has its own dedicated entry elsewhere in this file;
these records additionally preserve the REFUTED / do-not-re-chase verdicts (which have no
other home). The still-open residues (alias-receiver known-limitations, the X3-highbit
contested-semantics decision, and the CR-2 coverage gaps) were carried forward to
claude-todo.md under "## CR-2 review — carried-forward open residues".

## CR-2 Plan-1 Round-2 + Plan-A — closing adversarial review (2026-06-09): SIBLING gaps in the just-landed fixes

A 28-agent adversarial review of the 9 landed CR-2 Round-2 + Plan-A fixes (the same review style that found the Round-1 siblings) — verdicts triaged below against the code + (where noted) runtime probes. **Headline: the recurring pattern recurred — several of THIS round's fixes peeled/guarded SOME sites sharing a root cause and left siblings broken.** All are PRE-EXISTING/latent (variants the landed fixes didn't cover; none is a regression from the fixes — they're the *un*covered cousins). Filed per the bug-discovery protocol; **fix decisions are the user's.**

> ⚠️ **The two reviews MASSIVELY over-confirmed via static reasoning — runtime-verify before acting on ANY finding here.** (1) The 28-agent closing review's 6 "confirmed" gaps reduced under runtime probing to: 1 real (S1, fixed `5c9b00e1`) + 2 niche real-rejections (S3/S4, filed) + 3 false positives (S2/S5/S6). (2) A follow-up 32-agent sweep (verifying S1 + hunting more un-peel siblings) flagged **21 further candidate sites** in `gen_selector` fallback arms / `gen_access` (readonly/named/alias slice+array+ptr indexing) / `gen_iface` ptr-to-readonly-iface — **ALL runtime-refuted**: one probe per distinct category (`mk().v`, `(*p).v`, slice-of-`@readonly Box` field, `readonly @[]int` index, `[2]readonly int` struct field, `*readonly @Getter` dispatch) returns the CORRECT value; named-array variants don't even parse. The static agents flag `.Elem` reads without tracing that the type arrives ALREADY-unwrapped (return-coercion strips readonly; predicate guards peel before the arm). The sweep DID verify the S1 fix + the A2 revert are correct/clean. **Net real bugs from BOTH reviews: S1 (fixed) + S3/S4 (filed niche). Do not chase the 21 phantoms.**

### [closing-review] Triaged verdicts — RUNTIME-verified (the review's static verify phase over-confirmed: of 6 "confirmed", 1 was a clean real fix, 3 are false positives, 2 are real rejections whose type-only fix is a compile→SIGSEGV regression)

**✅ RESOLVED**
- **CRITICAL — `getSelectorType` un-peeled pointee** (`gen_selector_type.bn:56,63`) — ✅ landed `5c9b00e1`. Read the un-peeled `.Elem.Name` of a managed/raw ptr-to-struct base; `@readonly Box`/alias base → `""` → nil; `rp.inr.x` folded to const-0. R2-D1 sibling. Fixed with `peelTransparent(peelTransparent(baseTyp).Elem).Name` (peel the base's own alias wrapper too — an alias base has nil `.Elem`). Cell `regressions/nested-selector-readonly-pointee`, 7 modes.

**⚠️ REAL reject, but the type-only fix is a compile→SIGSEGV safety regression (needs an IR-gen companion) — per the user (2026-06-09): FILE as a known limitation, do NOT pursue the IR-gen work now. Type fixes were prototyped + REVERTED.**
- **MAJOR — alias receiver unsupported for METHOD VALUES** (`pkg/binate/types/check_expr_access.bn:249` + IR-gen): `type AB = @Box; var mv = ab.getV` is rejected ("undefined: getV") because the method-value path calls `ReceiverBaseNamed()` on the un-alias-peeled `origXt`. Peeling it (`resolveAliasAndConst(origXt).ReceiverBaseNamed()`) makes it type-check, but the method-value CLOSURE layout (`gen_method_value.bn`) doesn't peel the alias → runtime **SIGSEGV**. A DIRECT method value (`p.getV`) works; only the alias receiver is broken. Niche (method values × alias receiver). To fix properly: type peel + peel the alias in the closure-capture IR-gen.
- **MAJOR — alias receiver unsupported for IMPL declarations** (`pkg/binate/types/check_impl.bn:90` + dispatch): `type AB = *Box; impl AB : Getter` is rejected ("impl receiver must be (a wrapper around) a named type") because `checkImplSatisfaction` calls `ReceiverBaseNamed()` on the possibly-`TYP_ALIAS` `recv`. Peeling it accepts the impl, but dispatch through the alias-impl iface value → runtime **SIGSEGV**. Niche (impl on alias receiver). To fix properly: type peel + alias handling in impl/vtable dispatch.

**❌ REFUTED / non-exploitable — RUNTIME-verified; do NOT act**
- **R2-D6 ALIAS cycles** (flagged CRITICAL) — **REFUTED**: `type A = B; type B = A` does NOT hang (3 variants tested; compiles + runs). `type A = B` with `B` forward sets `A.Target` to a `TYP_NAMED` forward (not a `TYP_ALIAS`), so `resolveAliasAndConst`'s loop terminates at the named type — the cycle the review imagined isn't formed. The static "unguarded loop" claim missed the forward-decl resolution.
- **R2-D2 named-array `peelReadonly`** (flagged MAJOR) — **REFUTED**: named-distinct array types (`type Arr [N]S`) don't PARSE (syntax error), and alias arrays (`type Arr = [N]S`) resolve via `indexExprType` and work (`a[i][j].x` → 9). The `peelReadonly`-vs-`peelTransparent` gap doesn't manifest for arrays.
- **R2-D6 unbounded `Underlying`-walkers** (`NeedsDestruction`/`SizeOf`/`AlignOf`/`discoverStructFromType`) (flagged MAJOR) — **non-exploitable**: only reachable via a cycle; named cycles are decl-time-rejected + broken (`Underlying=nil`), and alias cycles don't form (above). No reachable hang; `peelNamedBounded` on the 4 comparison predicates is sufficient. (Bounding them anyway is harmless defense-in-depth if ever wanted, but defends an unreachable state.)
- **gen_stmt.bn:259 genDecl iface boxing** (flagged CRITICAL R2-D4 sibling) — **REFUTED**: runtime-verified `var iv readonly @Getter = im; iv.get()` → 7. `genExprOrFuncRef` boxes before the unpeeled `typ.Kind` check, so the skipped re-box at :259 is harmless.
- **LowerOneFunc / LowerOneFuncShadow missing externNameConflict** (flagged CRITICAL A2 sibling) — **MOOT**: A2 was reverted as a misdiagnosis; the guard no longer exists.

### [closing-review] Coverage gaps (lower priority — add tests)
R2-D7: no readonly/alias-wrapped named-int or named-float-minus test. R2-D5: matrix covers only `type AB = @Box` (not alias-over-readonly / value-receiver alias). R2-D4: only managed `readonly @Iface` construct un-xfailed (no `readonly *Iface`, no return/arg-pass position). A1: no float-scalar / named-sub-word / box-in-loop box test.

---

## CR-2 follow-up batch adversarial review (2026-06-09) — post-landing

Adversarial review (find → perspective-diverse cross-examine → synthesize, 56 agents)
of the 8 landed CR-2 follow-up commits (R2-1 `79ebfa98`, R2-2 `d086ccac`, B2
`e15680d7`, B1 `05901f97`, B4 `b4648200`, B3 `5fc5a52f`, R2-3 `ca155319`, split
`2beab6e5`). **Heeding the over-confirmation caution at the top of this file, the
three critical/major entries below were RUNTIME-verified by hand (gen1/gen2 bnc
built from the worktree + an A/B against BUILDER bnc-0.0.7), not just statically.**
Two of the serious findings are regressions in THIS batch's own commits.

- **CRITICAL — X2** (R2-3 `ca155319`): the new negative-offset `panic` false-fires
  on valid code (iface-value upcast to an unrelated zero-method interface).
  **✅ RESOLVED 2026-06-10 (binate `4ac123da`)** — root-caused as a checker
  duck-typing hole; fixed via `isUniverseAny` + supported `@Iface -> *Iface`
  decay (fork B). Full entry under ## CRITICAL.
- **MAJOR — B1/X3** (`05901f97`/`5fc5a52f`): bare const-group member drops its
  inherited narrow type → checker accepts an overflow the explicit form rejects,
  IR truncates (silent wrong value). Full entry under ## MAJOR. Straight bug fix.
- **MAJOR — B2** (pre-existing, NOT from `e15680d7`): named func-value types
  (`type Fn @func(...)`) are unconstructible. Full entry under ## MAJOR.

**Lower-severity / follow-up (not yet runtime-triaged unless noted):**
- **X3-highbit (major, DIRECTION CONTESTED — semantics-owned).** `1<<iota` now
  folds in the checker (B1), so a flag member hitting the SIGN bit of a signed
  target (`1<<63` → `int` on 64-bit; `1<<31` on 32-bit) computes positive
  2^(W-1), which `FitsSigned(W)` rejects — while IR's `evalConstExpr` wraps to the
  valid two's-complement `INT_MIN`. A real checker-vs-IR divergence, but the
  RESOLUTION is a spec call: `claude-notes.md` §const decides const values are
  abstract and must fit the target range (→ the reject may be CORRECT; the
  canonical idiom uses an UNSIGNED target, unaffected). Do NOT change semantics
  unilaterally. (The literal `1<<63` form was already rejected pre-B1; B1 only
  widens that to the iota form without aligning IR.)
- **X2b (major, derivative/pre-existing).** The VM upcast path (`vm_exec_iface.bn`)
  reacts to the SAME checker-accepted upcast with a runtime abort (`iface_upcast:
  target vtable not found`) — a third distinct behavior. Not touched by R2-3.
  Whatever fixes X2 must reconcile all four consumers (LLVM/aa64/x64/VM).
- **B3 type-divergence (minor) — ✅ RESOLVED 2026-06-10 (binate `b9d6d807`).** A bare
  const member that PARKS (REPL) used to resolve via `GenConstMember` (reads only
  `d.TypeRef`=nil → untyped int), whereas the non-parked sibling got the inherited
  type via `genConstGroup`. Fixed by the B1/X3 fix: `checkGroupDeclTentative` now
  threads the inherited type onto the synthesized repeat, so the parked member
  carries `d.TypeRef`=the inherited type and resolves at that width.
- **✅ RESOLVED 2026-06-10 (binate `e16d53bc`) — the four cheap CR-2-review minors:**
  - arm32 xfail rationale (value-struct-large linux+baremetal): corrected to the
    real cause (shared IR-gen readonly field-read defect / Defect 1), matching the
    sibling value-struct markers verbatim so both clean up together (was an XPASS
    landmine).
  - `IsByvalParam` unbounded peel: routed through `peelNamedBounded` (1024 cap),
    behaviour-identical for valid types.
  - stale `gen_func.bn` comment: rewritten to the actual mechanism (`IsByvalParamRef`
    flag drives `OP_STORE`'s memcpy; `ParamIndex` is debug-info only).
  - B3 test: added the `IotaIdx == 1` assertion (mirrors the sibling iota test).

REFUTED by cross-examination (recorded so they aren't re-chased): no other
`emitRef`/`emitValRef` global-ref drop sites beyond OP_CAST + iface-arg (R2-2 clean);
B2's `=` change correct for multi-assign/non-func-LHS; the split (`2beab6e5`) moved
all functions/tests intact; B4 regression tests are non-vacuous.

---

## CR-2 Plan-1 Adversarial Review — pre-existing sibling miscompiles (2026-06-08)

An adversarial multi-agent review (53 agents) + hand-verification of the CR-2
Plan-1 defect fixes (Defects 1–9). **Headline: the landed fixes are correct
for exactly what they claimed, but INCOMPLETE — each peeled/migrated at SOME of
the sites sharing its root cause and left the siblings broken.** These siblings
are PRE-EXISTING miscompiles (no Plan-1 fix touched them; C1's pre-existence
was confirmed by building a pre-fix compiler) — **none is a regression
introduced by the fixes**, and no green test went red. The recurring root
causes: (R1) wrapper-transparency peeled in predicates but not at the consuming
extraction / call-convention / construction sites; (R2) `isAggregateAllocToLoad`
migrated to only 2 of ≥6 aggregate-store/arg arms; (R3) the multi-return
slot-typing fallback landed in `:=` but not `=`; plus the Defect-9 `-` fix
gating on `TYP_INT` (not peeling `TYP_NAMED`). Each fix is a peel-at-the-
consuming-site / swap-the-guard one-liner + xfail-then-fix coverage; all ship
green because no test exercises the wrapped / nameless / composite-literal /
named-type variant. Per the user (2026-06-08): FILE all, FIX nothing yet.
The CRITICAL entries below are also surfaced in `## CRITICAL`-class triage.

### [CR-2 Plan-1 review] MINOR / doc-comment & xfail-hygiene corrections (2026-06-08)
- **N2 / N3 / N10 / N11 — ✅ DONE**: N2 (dead `peelTransparent` comment in `gen_iface.bn`) and N10/N11 (stale iface/funcval-multi-return xfail markers) were resolved in-tree by later work (verified absent); N3 (the false "deferred to the concrete instantiation" comparability comments + an xfail `eq[@[]int]` cell, `conformance/772`) landed binate `15946a55`. See claude-todo-done.md.
- **N1 (narrow, pre-existing) — ✅ RESOLVED 2026-06-12 (`11f99ed9`)**: an out-of-range CONSTANT shift count was wrapped into [0,width) by `ensureWidth` BEFORE the overshift guard (`v << 256` on uint8 → 1 not 0; signed `int8 >> 256` stays -64 not sign-filled; same in `<<=`/`>>=`). New `emitConstOvershiftOrNil` (`gen_binary.bn`) detects a constant count `>= width` from its ORIGINAL (pre-`ensureWidth`) `IntVal` and emits the spec result directly — 0 (logical `<<`/unsigned `>>`) or sign-fill `lhs >> (W-1)` (signed `>>`), the SAME result `emitGuardedShift` already produces for a runtime overshift (VM-consistent — the path the reverted "widen the value" attempt regressed). Wired into BOTH `genBinaryExpr` and `emitCompoundBinop`, before each truncates the count. Keying on `IntVal` also covers a wider-TYPED constant count (uint16 const 256 shifting a uint8). `conformance/729_const_shift_overshift` green on LLVM / both VM lanes / native aa64 / native x64-darwin; the 48 existing runtime-count shift/overshift cases + ir unit tests unaffected. (The **runtime** count-wider corner (c) is now also ✅ RESOLVED — binate `0db709a1` reads the UNTRUNCATED count so a runtime count wider than the value is detected. Related shift hardening landed alongside: a runtime **negative** shift count now panics — `6bf1efab`, `runtime error: negative shift count` — and a constant negative count is a compile error — `f6b9ebce`; plus the guard-free `unsafe_shl`/`unsafe_shr` intrinsics — `c9a6ed36`. Spec updated: §13.5 `expr.shift.overshift`/`expr.shift.negative`, §15.8, §17.5, §21.)
- **Coverage-only (verified-correct paths)**: 659 omits raw-pointer-index compound-shift (`p[i] <<=`) and signed `>>=` overshift on non-IDENT lvalues; the genShortVar nameless `multiReturnFieldTypes` fallback has no IR-gen unit test / no managed-component func-value `:=` cell; Defect-2b raw-pointer & value receiver rows have no conformance/unit coverage (the reject paths are soundness-critical and the TYP_POINTER/TYP_MANAGED_PTR arms are duplicated).

### ~~Package name/path conventions — decide and possibly reorganize~~ — ✅ DONE — decided + realized as `pkg-layout-spec.md`

Decided and ratified in [`pkg-layout-spec.md`](pkg-layout-spec.md): the tier scheme
(0/0b `pkg/builtins/*` < 1 `pkg/std/*` < 1x `pkg/stdx/*` < 2 `pkg/<org>/*` e.g.
`pkg/binate/*` < 3 app-specific), in-`pkg/`-tree naming, namespace-contention rules,
the `ifaces/`/`impls/` parallel-tree layout (realized in-tree: `ifaces/{core,stdlib}/pkg/…`,
`impls/{core,stdlib}/pkg/…`), and the mangling story (no scheme change — the mangler already
uses the full package path; hard-coded mangled-name strings update mechanically when packages
move). The reorg is realized: toolchain internals under `pkg/binate/*`, stdlib under
`pkg/std/*` / `pkg/stdx/*`, runtime/builtins under `pkg/builtins/*`. The package-manager
naming interaction (URL vs registry vs short alias) is sketched in the spec's "Package manager
interaction" section and folded into the package-manager sketch entry in claude-todo.md.
Tier dependency-direction enforcement is tracked separately ("Tier + dependency-direction
hygiene checks").

### ~~DWARF debug info — foundation in place, type coverage missing~~ — ✅ DONE — foundation + full type coverage (2026-05-07/09)

Foundation and full type coverage landed (details below). The one remaining piece — finer-grained source positions (thread `.Line` through more IR-gen sites; `llvm.dbg.value`; columns) — is open-ended and carried forward to claude-todo.md as "DWARF debug info — finer-grained source positions".

**Done** (via `56ea542`, `a15ef50`, `2cd2c25`):
- `-g` flag in `cmd/bnc`, `SetDebugInfo` in `pkg/codegen`; off by default.
- Module-level: `source_filename`, `DICompileUnit` (FullDebug), `DIFile`, `DISubroutineType`, per-function `DISubprogram`.
- Line-level: `Line int` field on `ir.Instr` (`pkg/ir.bni:170`). `genExpr` sets `.Line` from `e.Pos.Line` (`pkg/ir/gen_expr.bn:16`). `annotateBlockInstrs` backfills zero-line instrs to statement line (`pkg/ir/gen_stmt.bn:11-14`). Per-instruction inline `!DILocation(line: N, scope: !M)` in emitted LLVM (`pkg/codegen/emit_debug.bn:99-114`).
- Variables: `llvm.dbg.declare` + `DILocalVariable` for named allocas (`emit_debug.bn:139-162`). Names propagated via `StrVal` on `OP_ALLOC`.
- lldb/gdb now show Binate function names, file, line numbers, and local variable names.

**Gaps**:
- ~~Type coverage is basically just `i64`.~~ FIXED for scalars,
  pointers, structs, slices, interface-values, function-values,
  arrays, and named typedefs (2026-05-07/08).
- ~~Parameters don't get `DILocalVariable`~~ — FIXED (2026-05-07).
  Param allocas were already named so the existing dbg.declare
  fired; step 3 added `arg: <N>` so lldb shows them as function
  arguments rather than mixed in with locals.
- ~~`DISubprogram` has `line: 0` and `scopeLine: 0`~~ — FIXED
  (2026-05-07). `ir.Func` carries a `Line` field; gen_func.bn
  populates it from the AST decl's `Pos.Line`; emit_debug.bn
  threads it into both the `line:` and `scopeLine:` fields.
  Synthetic helpers (init dispatcher / entry wrapper / dtor /
  copy stubs) keep `line: 0`.
- ~~`DISubroutineType` is a single shared generic~~ — FIXED
  (2026-05-09). Per-function DISubroutineType + types tuple
  emitted; void/nullary funcs get `!{null}`, parameterised funcs
  get `!{<ret-or-null>, <param1>, ...}` referencing the type
  registry. See step 7 below.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).
- Line positions: only `genExpr` explicitly threads `.Line`; most IR-emission sites rely on statement-line backfill (coarse). No columns.

**Reasonable next steps** (roughly ordered by effort/payoff):
1. ~~Emit `DIBasicType` for each scalar kind~~ — DONE (2026-05-07).
   Unit tests in `pkg/codegen/emit_debug_test.bn` pin the slot
   layout (`TestDbgTypeIDScalars`), the emitted DIBasicType nodes
   (`TestEmitDebugBasicTypesEmitted`), and the `dbg.declare` →
   slot wiring (`TestEmitDebugDeclareReferencesScalarType`). Full
   conformance (boot-comp, 317/0) compiled with `BINATE_FLAGS=-g`.
2. ~~Capture function definition lines into `DISubprogram`~~ —
   DONE (2026-05-07). `TestEmitDebugSubprogramLine` pins
   `line:` / `scopeLine:` for two functions on different source
   lines; `TestSyntheticFuncDefaultLineZero` pins the synthetic
   `Line == 0` invariant.
3. ~~Emit `DILocalVariable` for parameters~~ — DONE (2026-05-07).
   Step actually emitted `arg: <N>` on the existing DILocalVariable
   for params (vs. the gap entry's premise of "no dbg.declare for
   params" — the dbg.declare was already firing once defineVarParam
   tagged the alloca). Tests:
   `TestEmitDebugDeclareParamsCarryArgIndex`,
   `TestEmitDebugMethodReceiverIsArgOne`,
   `TestParamAllocaParamIndex`.
4. ~~Emit `DICompositeType` for structs / `DIDerivedType` for
   pointers~~ — DONE (2026-05-08). `pkg/codegen/emit_debug_types.bn`
   carries a per-module type registry keyed by structural string
   (raw vs managed pointers distinguished); ids allocate past the
   per-function metadata block. Recursive interning means a
   `*Counter` local pulls in Counter's struct nodes; field types
   route back through `dbgTypeID` so scalar fields wire to !5..!15.
   Tests in `emit_debug_types_test.bn` cover pointer + struct
   emission, the pointer-to-struct chain, the dedup invariant, and
   the structural-key helper. Full conformance under -g: 327/0.
5. ~~Wire slices, managed-slices, interface-values, function-values,
   arrays, and named typedefs into the registry~~ — DONE
   (2026-05-08). New `pkg/codegen/emit_debug_aggr.bn` carries
   intern + emit functions for each kind. Slices map to
   DICompositeType DW_TAG_structure_type with the runtime layout
   (2-word for raw, 4-word for managed); iface and func values
   map to 2-word DICompositeType; arrays map to DICompositeType
   DW_TAG_array_type with DISubrange(count:); named typedefs map
   to DIDerivedType DW_TAG_typedef. Tests in
   `emit_debug_aggr_test.bn`. Full conformance under -g: 327/0
   (1 unrelated xfail). NOTE: TYP_NAMED rarely surfaces in
   today's IR-gen because `type Pos int` is currently treated
   as an alias and unwrapped before reaching the alloca's
   TypeArg; the typedef path is in place for when distinct-
   named-type semantics land.
6. Thread positions through more IR-gen sites (statements, assignments, calls) for finer-grained `DILocation`.
7. ~~Per-function `DISubroutineType` with real parameter + return
   types~~ — DONE (2026-05-09). `setupDbgFuncSubroutineTypes`
   allocates a (typesList, subrType) id pair per non-extern Func
   and eagerly interns each function's param + return types so the
   tuple resolves; `emitDbgFuncSubroutineTypes` writes both nodes
   after the per-function metadata block. DISubprogram now
   references the per-func DISubroutineType instead of `!4` (the
   legacy shared empty placeholder remains for backwards compat).
   Tests in `emit_debug_test.bn`:
   `TestEmitDebugSubroutineTypePerFunc` (non-!4 + `!{!5, !5...}`
   shape), `TestEmitDebugSubroutineTypeVoidNullary` (`!{null}`),
   `TestEmitDebugSubroutineTypeVoidWithParam` (`!{null, !5}`).
   Full conformance under -g: 327/0 (1 unrelated xfail).

### ~~Continue backfilling negative conformance tests~~ — ✅ DONE (superseded) — backfilling is now standard practice

The early snapshot (31 negative tests, numbers 200–246) is long superseded: ~137 negative
`.error` conformance tests now exist, and every item it tracked is resolved or moot —
missing-return detection (245) landed (no longer xfail'd; control-flow analysis implemented),
the "fixed diagnostics" (assign-to-const 238, break/continue-outside-loop 239/242, duplicate
params 243, var-redeclaration 246) shipped long ago, and the `boot`-mode caveat (244 xfail on
boot) is dead since the Go bootstrap interpreter was retired. Adding negative tests for new
diagnostics is now standard practice under the Bug Discovery Protocol, not a standalone TODO.

### ~~Differential scalar harness (`matrix/scalar-diff`) landed — two backend defects found~~ — ✅ DONE — harness (v1+v2) landed; all defects fixed

The harness landed (v1 + v2, 2026-06-06) and every backend defect it found is resolved:
`vm-int-to-float32` (`289420b6`), `vm-float32-to-unsigned` (`3fd7e712`), and `aa64-subword`
(`5f94558b` — see the sub-word-narrowing entry; the body's "17 xfailed cells / Fix: …" aa64 text is
the pre-fix snapshot). scalar-diff now has 0 xfails. The only residual — re-evaluating native-x64 /
arm32-linux on an x64 host — is carried forward to claude-todo.md. Body kept as the harness record.

- **What landed**: `conformance/gen-diff-scalar.py` + 41 cells / 1707 tuples
  under `conformance/matrix/scalar-diff/` — a property-based **differential**
  value-correctness harness for scalar shifts & conversions. Oracle is the
  **spec** (computed at full precision, independently validated by a 5-reader
  adversarial pass), not a backend, so spec-divergences (the shift-bug class)
  are caught too. Self-checking cells (`println(cast(int, computed == spec))`)
  for target-stability across 32/64-bit. Green on all LLVM modes + arm32
  baremetal; the two clusters below are xfailed (verified non-stale via
  `--check-xpass`). Idempotent generator; `int↔int` casts and all shifts pass
  on every real backend (broadened regression net for `32fde83d`).
- **`vm-int-to-float32` — VM `int → float32` is broken (every width/sign) — ✅ RESOLVED 2026-06-12 (binate `289420b6`)**:
  every `cast(float32, <int>)` diverged — even `cast(float32, 1) > 0.0` was
  false on the VM. Root cause: `int → float` lowered to `BC_SITOF`/`BC_UITOF`,
  which land at **float64**; the VM's float32 register form is the float32 IEEE
  bits in the low 4 bytes, so the float64 pattern's low word (usually zero) read
  back as ~0. Fix: fused `BC_SITOF32`/`BC_UITOF32` opcodes that write the
  float32 bit pattern directly, selected in `lower_cast` when the cast dest is
  float32 (signedness still picks signed/unsigned). Un-xfailed **16 of 17** VM
  cells across all 3 VM modes; 3 VM unit tests added (lowering decision ×2 +
  end-to-end round-trip). The 17th cell (`float-to-int/64/unsigned`) uncovered a
  **distinct sibling bug** (`vm-float32-to-unsigned`, now also resolved — see
  below).
- **`vm-float32-to-unsigned` — VM `float32 → unsigned int` used the SIGNED conversion — ✅ RESOLVED 2026-06-12 (binate `3fd7e712`)**:
  surfaced while fixing `vm-int-to-float32`. `lower_cast`'s `float → int` arm
  picked `BC_F32TOSI` (signed) for a float32 source regardless of dest sign
  (its comment admitted "float32 → unsigned is not yet exercised; it stays on
  the signed `BC_F32TOSI`"). So `cast(uint64, <float32 ≥ 2^63>)` saturated to
  `INT64_MAX` instead of the in-range unsigned value — a *defined* (in-range)
  conversion miscompiled, MINOR (only float32→uint64 of values ≥ 2^63; the
  8/16/32-bit unsigned high-bit values fit signed int64 so those cells already
  passed). Fix: the exact mirror of the float64→unsigned `BC_FTOUI` — added a
  `BC_F32TOUI` opcode (`cast(int, cast(uint64, <float32>))`), picked in
  `lower_cast` for a float32 source with an unsigned dest. Un-xfailed the last
  scalar-diff VM cell (`float-to-int/64/unsigned`, the 2^63 round-trip) across
  all 3 VM modes; 2 unit tests added (lowering decision + high-bit round-trip).
  **All scalar-diff conversion cells are now green on every VM mode** — the VM
  int↔float32 story is complete in both directions.
- **`aa64-subword` — native-aa64 doesn't narrow/sign-extend sub-word results**:
  a sub-word op leaves dirty high bits / wrong sign. `int8(-128) << 1` keeps
  bit 8 set (so `== 0` fails); `cast(int8, 128:uint8)` and the other
  `uint8 → int{8,16}` casts are wrong. 17 xfailed cells: `shl`/`shr` 8/16/32
  **signed**, all 8 `int-cast`, signed sub-word `float-to-int`/`int-to-float`.
  64-bit and most unsigned paths are fine. The native sibling of the VM/native
  sub-word-narrowing gap above, here confirmed across shifts/casts/conversions
  (not just arithmetic). Fix: post-op narrow + sign-extend sub-word results in
  the aa64 backend (or an IR-gen narrow — the shared P3 design call).
- **native-x64 / arm32-linux not evaluated**: the host lacks x86_64 C runtime
  headers (`stdio.h` → every native-x64 cell `COMPILE_ERROR`s uniformly, an env
  limitation, *not* a backend result — no x64 xfails placed), and `arm32-linux`
  needs `qemu-arm` (skipped). Re-check on an x64 host: the aa64 sub-word defect
  very likely has an x64 analog needing its own xfails.
- **Discovery**: 2026-06-06, differential-harness v1 (plan-differential-testing.md).
- **v2 (arith/cmp/bitwise) — LANDED 2026-06-06** (binate `42ad4fa0` fix +
  `e71de1e0` harness): 123 cells / 5415 tuples total. v2 found+fixed the LLVM
  `~` bug (`bitnot-result-type`, above). Remaining divergences, all xfailed
  (`--check-xpass`-clean) and in the known classes: VM
  `bitwise/not/{8,16,32}/unsigned` (sub-word `~` dirty bits); native-aa64
  sub-word *signed* `arith/{add,sub,mul}/8`, `bitwise/{and,or,xor}/{8,16}`,
  `cmp/{8,16,32}`, `bitwise/not/*/unsigned`. Float compares incl. NaN/Inf/-0 pin
  the ordered/unordered `==`/`!=` semantics (corrected 2026-06-06). `fcmp/32`
  was xfailed at first but the float32-compare fix (binate `fc11d862`) landed
  concurrently, so it un-xfailed at land time (`--check-xpass` flagged the
  XPASS). The remaining VM `float32` *conversion* xfails (`int-to-float` /
  `float-to-int` / `float-cast`) stand — that gap is separate from compare.

### ~~Function values — MAJOR PROJECT (interop prerequisite)~~ — ✅ DONE — all three phases landed (Phase 1 non-capturing, Phase 2 closures/capture, Phase 3 cross-mode trampolines)

All three phases landed. The body's "Phase 2 DEFERRABLE" framing is the pre-completion snapshot —
`plan-function-values-phase-2.md` is now "COMPLETE (shipped)" (closures + capture; conformance
501/508–510/513). Residual follow-ups (broader trampoline signatures, recursive lambdas, and the
downstream interop hand-off — tracked under the Compiler/interpreter interop entry) carried forward
to claude-todo.md. Body kept as the design + phasing record.

- **Plan docs**: `explorations/plan-function-values.md` (parent;
  Phase 1 COMPLETE) + `explorations/plan-function-values-phase-3.md`
  (cross-mode trampolines; Slices 3.1, 3.1.5, 3.2, 3.3, 3.4 all
  LANDED).
- **Phase 1 COMPLETE (2026-05-01)**: A.1–A.7 all landed. Type
  syntax, nil + zero-init, function-reference-as-value, calling
  through a function value, flow through args/returns/fields,
  method expressions `T.M`, and non-capturing function literals
  (lifted to synthetic `__funclit_<n>` top-level Funcs).
  Conformance tests 338–342 + 344 cover each slice; pkg/ir + pkg/types
  unit tests cover each coercion site, AssignableTo predicate,
  and capture-rejection. `pkg/ir/gen_call.bn` and
  `pkg/ir/gen_func_lit.bn` extracted to keep file-length hygiene
  clean.
- **Phase 3 LANDED (per plan-function-values-phase-3.md)**:
  cross-mode trampolines bridge compiled ↔ VM through a uniform
  always-shim convention `<ret>(*uint8 data, <args>)`. Compiled
  side: per-function `__shim.<mangled>` set in each `__vt.<mangled>`'s
  `call` slot (Slice 3.1). Common kind-tag at the start of `data`
  (Slice 3.1.5) discriminates `DATA_KIND_VM_CLOSURE_REC` vs
  `DATA_KIND_COMPILED_CLOSURE` (Phase 2). Compiled→VM goes through
  `vm.TrampolineScalar`, a fixed 7-int-arg trampoline that reads
  VM handle + vm_func_idx from the closure rec and dispatches via
  `execFunc` (Slice 3.2). Bytecode→compiled goes through
  `dispatchCompiledFuncValue` (`pkg/vm/vm_exec_helpers.bn:247`),
  which routes via `rt._call_shim_scalar` — a new IR-magic helper
  alongside `_call_dtor` / `_call_free_fn`, lowered to
  OP_CALL_INDIRECT (Slice 3.3). The earlier `5f4333f` cross-mode
  hack for `func(*uint8)` is now reframed as `dispatchNativeIndirect`
  — the BC_CALL_INDIRECT counterpart of BC_CALL_FUNC_VALUE's
  data==null branch (Slice 3.4). VM handle lives in the
  VMClosureRec (not a global), so multi-VM works without ordering
  concerns. Bootstrap-subset constraint: scalars + pointers ≤7,
  no floats, no aggregates — broader signatures need additional
  trampoline shapes when they actually reach this path.
- **Phase 2 DEFERRABLE**: closures + capturing function literals;
  capture design (by-value vs by-ref, mutability, lifetime) is
  its own pass. The bytecode dispatcher (`BC_CALL_FUNC_VALUE`)
  already has a `DATA_KIND_COMPILED_CLOSURE` arm (clear-error
  guard) ready to fill in.
- **Downstream**: Phase 3's machinery is what the
  compiler/interpreter interop project needs. With per-signature
  shims + the `(data, args)` convention, a "package descriptor"
  of function-value pointers is enough to dispatch arbitrary
  cross-mode calls — no per-function hand-coding required. This
  also opens the door to retiring `pkg/vm/vm_extern.bn`'s
  hand-written extern arms (~30 of them, including the
  `rt.RefInc` / `rt.RefDec` arms flagged for retirement above);
  see the Compiler/interpreter interop entry below.
- **Reframed scope**: function values were originally framed as
  "blocked on / a piece of interop." Inverted: data interops fine
  via shared `.bni` layout; what crosses the compiled/interpreted
  boundary at runtime are *exported functions and methods passed
  as values*. The package descriptor the interop work needs is just
  a struct of function values per export. So function values are
  the **upstream prerequisite** for the broader interop project,
  not a sub-item of it.
- **Representation**: 2-word `{vtable, data}`, identical to
  interface values. The vtable type is per-signature; the vtable
  *instance* is per-(function, capture-shape). Vtable layout has
  `dtor` first (matching all other vtables — common destruction
  sequence) and `call` second. Function types are structural —
  `*func(...)` / `@func(...)` — with no user-visible "function
  interface" declaration; the compiler synthesizes the impls at
  function-literal and method-value sites.
- **Frontend syntax**: `*func(int) int` raw / `@func(int) int`
  managed, mirroring the slice migration (`*[]T` / `@[]T`) and the
  proposed interface revision. Bare `func(...)` is not a usable
  type.
- **Upstream prerequisite**: `plan-call-indirect.md` — LANDED.
  The `OP_CALL_INDIRECT` IR op (LLVM + VM + native arm64
  lowerings) is what Phase 1's vtable-indirect call sequence is
  built on. Already exercised end-to-end by RefDec's dtor
  dispatch; this plan's Phase 1 doesn't need to re-invent
  indirect dispatch.
- **Phasing** (per the plan doc):
  - **Phase 1 — backend vtable machinery + non-capturing function
    values.** This is primarily about *building the shared
    interface/vtable backend* (vtable type/instance generation,
    `call`-shim mechanism, vtable indirect-call sequence in
    compiler + VM). Non-capturing function values are the
    smallest user-visible thing the backend can deliver. The same
    machinery is what user-declared interfaces will need at the
    runtime layer. Non-capturing call sites use a check-data-nil
    short-circuit (consistent with other nil-checks in the
    codebase) rather than always going through the shim.
  - **Phase 2 — closures + method values (DEFERRABLE).** Capture
    analysis, closure-struct generation, receiver-capture for
    method values. **Capture design is open** (by-value vs. by-
    reference, mutability semantics, lifetime extension) and is
    its own design pass before implementation. Most current goals
    do *not* need Phase 2; the compiler and self-hosted runtime
    don't write closures, CallDtor retirement doesn't need it
    (see Path B above), and the interop descriptor exposes only
    non-capturing function values. Defer until there's a concrete
    user-facing need.
  - **Phase 3 — cross-mode trampolines.** LANDED. Per-signature
    (currently per-return-shape: TrampolineScalar) trampolines
    bridge compiled ↔ VM through the always-shim convention.
    See plan-function-values-phase-3.md for slice-by-slice detail
    and the "Phase 3 LANDED" bullet above for the LANDED summary.
    Unlocks the broader interop work; doesn't require Phase 2.
- **Recursive lambdas — explicit non-goal for Phase 1.** Go-style
  recursive closures (`var f = func(x) { ... f(...) ... }`) are
  NOT supported. Top-level named recursive functions work as
  always. Y-combinator pattern is the workaround if needed.
  Revisit when Phase 2 capture design is settled.
- **Backend dependency**: function values share the vtable layout
  and dispatch path with interfaces, but **not** the frontend
  interface syntax. They depend on the runtime/codegen vtable
  machinery, not on `plan-interface-syntax-revision.md`. Either
  plan can land first; both share the backend.
- **Method values** (`x.M`, `T.M`) and **closures** are folded
  under this plan rather than tracked separately.

### ~~Per-file build constraints — conditional file inclusion/exclusion by target — DESIGN~~ — ✅ DONE — `#[build(EXPR)]` arch/os MVP landed (deferred follow-ups carried forward)

The `#[build(EXPR)]` arch/os MVP is implemented + landed across all four granularities (through
`c7249552`; conformance 731/733/735/736/737/746/747); the design (generalized per the user from
per-file to per-declaration) is in `plan-build-constraints.md`. The deferred follow-ups (extended
vocabulary, `bnlint --target`, main-module gating, `impls/`-tree migration, the inline-asm `#[asm]`
doc) are carried forward to claude-todo.md. Body kept as the design record.

- **STATUS — arch/os MVP IMPLEMENTED + LANDED.** The `#[build(EXPR)]`
  mechanism is live with the minimal `is(arch, …)` / `is(os, …)` vocabulary
  (membership form, bnas-aliased), gating at all four granularities: file
  (package clause), declaration, import, and `.bni` interface decls. The
  active config defaults to the host (read from `pkg/builtins/build` via
  `loader.ResolveBuildConfig`), overridable per `--target`. Landed across
  binate increments through `c7249552` (`.bni` gating + the `loader.bn` /
  `MergeFiles` split + conformance 746/747; the aliased-import fix `52d1c832`
  + coverage 738/745 was a detour surfaced en route). Conformance:
  731 (file), 733/735/736 (decl: const/var/type/func), 737 (import), 746
  (`.bni` decl), 747 (whole-`.bni` drop, negative). See
  [`plan-build-constraints.md`](plan-build-constraints.md) for the full
  status. **Still deferred** (each its own follow-up, none started):
  vocabulary beyond arch/os (`triple`/`backend`/`libc`/`ptrsize`/`version`
  with `is`/`at_least`/`at_most`), `bnlint --target`, main-module gating,
  migrating the `impls/` duplicate trees onto constraints, and the separate
  inline-asm (`#[asm]`) doc.
- **Concrete proposals**: see [`plan-build-constraints.md`](plan-build-constraints.md) — generalized per the user from *per-file* to **per-declaration** conditional compilation via a first-class `#[build(EXPR)]` annotation on any top-level decl (`const`/`type`/`var`/`func`/`package`/`import`); the `#[...]` grammar already reserves an `[ Annotation ]` slot on every top-level form (only `PackageClause` lacks it) and the attachment + `compiler.*`/`tool.*` namespacing are decided. Covers the predicate model + expression semantics (closed typo-checked vocab; ordered comparisons for `ptrsize`/`intsize`/`version`/`os.version`; hard-error on unknown/malformed/not-yet-wired), two gate seams (pre-parse file-level + post-merge/pre-resolve decl-level), disjoint variant definitions / conditional imports / conditional `.bni` decls (relaxing Invariant 1), the impls/-tree relationship + migration, tooling (bnlint `--target` now necessary; `tool.lint` lint-exempt), and a phased roadmap. Inline asm (`#[asm]`) is deferred to its own sibling doc that composes with this substrate.
- **What**: a way for a single file to opt *itself* in or out of
  compilation based on the build configuration — arch, target triple,
  OS, libc-vs-freestanding, backend (LLVM / native-aa64 / native-x64),
  engine (`bnc` compiled vs `bni` interpreted), etc.
- **Why the current mechanisms are inadequate**:
  - **Separate trees + symlinks** (what we have now —
    `impls/{common,libc,baremetal}/…`, per
    [`pkg-layout-spec.md`](pkg-layout-spec.md) invariant 5 "Whole-package
    selection only"): too **coarse** (selection is whole-package /
    whole-variant-dir; "shared core + one per-variant file in the same
    package" is unrepresentable) and too **annoying** (symlinks to share
    the common files across variant dirs; a new axis means a new tree).
  - **Go-style filename suffixes** (`foo_posix.bn`, `foo_arm32.bn`): too
    **magical** (the constraint is invisible *inside* the file, smuggled
    in via the name) and too **coarse** (only a fixed suffix vocabulary;
    can't express conjunctions/disjunctions like "arm32 AND libc", or
    "any of {x64,aa64} but not baremetal").
- **Proposed shape**: an **annotation (writ large) near the top of the
  file** declaring the file's applicability condition as an *expression*
  over target predicates (`arch == "arm32"`, `libc`, `engine == "bni"`,
  with `&&` / `||` / `!`).  Two candidate syntactic forms to weigh:
  - a real **annotation on the `package` clause** (e.g.
    `#[build(arch == "arm32" && libc)] package foo`) — first-class,
    grammar-integrated, parseable; but the file must parse far enough to
    read it before we know whether to compile it, so the condition has to
    be evaluable from a cheap leading-prefix scan (read annotation →
    decide → continue or drop the file);
  - a **comment-form pragma** (a recognized leading comment, e.g.
    `//bn:build arch == "arm32" && libc` — Go-`//go:build`-shaped but
    expression-based, not suffix-based) — even cheaper to scan, but
    out-of-grammar / more "magical".
- **Design questions**:
  - **Predicate vocabulary + authority**: arch, triple, OS,
    libc-vs-freestanding, backend, engine, possibly user-defined build
    tags.  Where is the canonical list defined?  How extensible?
  - **Relationship to the `impls/` trees**: does this *replace* the
    `{common,libc,baremetal}` split (collapse back toward one tree, files
    self-select) or *complement* it (trees for the coarse axis,
    annotations for the fine)?  At minimum it should retire the symlink
    workaround; possibly the per-variant impl dirs too.  Decide
    explicitly — interacts with `pkg-layout-spec.md`.
  - **Loader/merge interaction**: excluded files simply don't join the
    merged package; ensure a package can still be legitimately empty (or
    require ≥1 surviving file) for a given target without spurious errors.
- **Tooling interaction (the bnlint question)**:
  - bnlint + the hygiene scripts must **understand** the annotation, so a
    file inapplicable to the current config isn't false-flagged (and so
    they can choose to lint each file under its applicable config(s)).
  - **Corollary worth designing in**: the same annotation surface could
    carry a directive telling bnlint / hygiene checks to **skip or ignore**
    a file (or regions of it) — a first-class "lint-exempt this file"
    mechanism, unifying build-constraints and lint-control under one
    annotation vocabulary.
- **Related entries to unify with**: the MAJOR "Better test-mode/target
  annotation than `.xfail`" entry above wants exactly this shape for
  *tests* (declare applicable modes/targets); and "Annotations and C
  function interop" below is the general annotation-syntax design.  This
  is the *source-file* instance of the same idea — design them together.
- **Prior art to consult**: Go build constraints (the `//go:build`
  expression form that replaced the `_GOOS` suffix era), Rust
  `#[cfg(...)]` / `cfg_if!`, Zig comptime target switches.  The
  expression form is the model.

### ~~REPL — All five tiers LANDED (2026-05-29)~~ — ✅ DONE — all five tiers landed (Tier-4 follow-ups + pretty-printer carried forward)

All five REPL tiers landed (Tier 5 mid-session imports `78685ac3`, 2026-05-29; Tier 3 forward refs all
stages 2026-05-28/29; Tier 4 replace + shadow). The body's Tier-3 "pending types/vars/consts … deferred"
note is superseded by the "ALL STAGES LANDED" line right below it. Residual — the Tier-4 refcount-aware
shadow warning + forced-shadow escape hatch, and the interfaces-gated pretty-printer — carried forward
to claude-todo.md.

- **Status**: `bni --repl <file.bn|dir>` ships.  `plan-repl.md` is
  the live source of truth for per-step state — commit tables,
  verified behaviors, deviations from the original plan, and the
  per-tier remaining-follow-ups list.  Briefly:
  - **Tier 1 (load-then-poke)** LANDED.
  - **Tier 2 (top-level decls at the prompt)** LANDED in full,
    including the body-introduced dtor-regen follow-up landed
    2026-05-28 (`EnsureReplBodyHelpers`).  Every top-level decl
    kind supported by the language works at the prompt: `func`
    (incl. methods, redefinition replace + shadow), `const`
    (single, untyped, grouped), `var` (typed,
    untyped-with-literal-init, with init), `type` (aliases,
    named non-struct, structs incl. managed-field).  Bodies that
    introduce a fresh managed-aggregate shape with a destructible
    element (e.g. `@[]@Bag`) have their helper emitted before the
    body lowers.
  - **Tier 3 (forward refs)** LANDED for `func` decls.  Pending
    types / vars / consts (need a structural treatment of
    "unsized" type symbols) are deferred.
  - **Tier 4 (redefinition)** LANDED for both replace and shadow
    paths, free funcs and methods.
  - **Tier 5 (mid-session imports)** LANDED 2026-05-29 via
    `78685ac3`.  `import "pkg/foo"` at the prompt loads pkg/foo
    transitively, type-checks, IR-gens, lowers, and defines the
    package symbol in the session scope.
- **Remaining REPL work**, per plan-repl.md:
  - ~~**Tier 3**: pending types / vars / consts; cycle
    detection.~~  **ALL STAGES LANDED** 2026-05-28 → 2026-05-29
    via 9 commits on main; see
    [`plan-repl-tier3-pending-types.md`](plan-repl-tier3-pending-types.md)
    for the per-stage commit table.  Every top-level decl
    kind parks on forward-referenced dependencies; use-site
    propagation works through sized contexts (struct field,
    var decl, func sig, composite literal, impl recv, method
    receiver); per-caller sized-vs-reference distinction
    preserves recursive types via pointers; cycle detection
    catches genuine cycles through sized fields with a clean
    `pending cycle: A -> B -> A` diagnostic.
  - **Tier 4**: refcount-aware shadow warning (today fires
    unconditionally); forced-shadow escape hatch (syntax TBD per
    `claude-notes.md`).
  - ~~**Tier 5**: loader entry point for "load this one package
    now."~~  LANDED 2026-05-29 — `evalReplImport` in
    `cmd/bni/repl_import.bn` drives it via the session loader's
    existing LoadImports (plus a SaveAliasMapState /
    RestoreAliasMapState bracket around the per-package InitModule
    loop so the main alias map survives the wipes).
  - **Pretty-printer** (`pkg/replprint`) — **deferred** until
    interfaces land.  `bootstrap.println` is a temporary hack;
    building features on top of it would entrench it.
- **Why this matters now**: the REPL is an explicit core goal in
  `claude-notes.md` (see "Forward references & REPL model — DECIDED"
  and the dual-mode rationale in
  `claude-discussion-detailed-notes.md` § 11 / § 23). Its semantics
  are largely *already decided*; what's not decided is the
  toolchain shape. Writing it down now so that adjacent decisions
  (function values, interop descriptors, layout extraction, IR
  cleanup) get checked against REPL feasibility before they land
  — and so that interpreter-only REPL work can start in parallel,
  since most of it overlaps with the audit work the interop story
  already needs.
- **Already-decided semantics** (do NOT relitigate here — see
  `claude-notes.md`):
  - **Retained mode** (definitions) — parsed and stored, validation
    deferred until dependencies are met. Source files are entirely
    retained mode.
  - **Immediate mode** (bare expressions / statements at the prompt)
    — fully checked at entry, can reference validated retained defs.
    Top-level scope in source files is declarative-only; bare exprs
    are REPL-only.
  - **No forward declarations.** Deferred validation handles forward
    references. Errors surface at use, not at definition.
  - **Redefinition**: *compatible* (same sig) → replace; *incompatible*
    (different sig) → shadow with refcounted old-def retention; warn
    on outstanding refs at shadow time. Forced-shadow escape hatch.
  - **Hot-swap of interpreted functions while a compiled binary runs**
    — fall-out of the thunk model.
- **What the VM is/isn't rigid about** (corrects an earlier overstatement
  in this entry):
  - **`BC_CALL` is name-resolved per call, not idx-baked.** Bytecode
    stores a per-VMFunc strings index for the callee's qualified name;
    `LookupFunc` walks `vm.Funcs` by name on every call
    (`pkg/vm/vm_exec.bn:418-421`). That makes replace-redefinition an
    in-place body swap and shadow-redefinition an append-then-shadow,
    both nearly free given `@VMFunc` already being managed.
  - **`vm.Funcs` is already incremental.** `LowerModule` is called
    per-module and appends; multiple modules already coexist in one
    VM with their own preserved string pools (`pkg/vm/lower.bn:42`).
    Globals are also append-only via `materializeGlobals`.
  - **The frontend pipeline is module-shaped, not declaration-shaped.**
    Loader, parser, type checker, and IR-gen are entered per-package;
    there's no "type-check this single decl against an existing scope"
    entry point. Forward refs work today only because the whole module
    is parsed before checking.
  - **Type checker has no concept of pending.** Errors fire immediately
    on undefined names. Deferred validation (the "retained" half of
    the model) is real new infrastructure.  *(Now: Tier 3 added a
    pending queue (`check_pending.bn`) for `func` decls; types / vars
    / consts still fire immediately.)*
  - **No pretty-printer for arbitrary values.** `println` covers char
    slices and primitives only.  *(Still true; deferred — see above.)*
  - **`LookupFunc` is a linear scan.** Fine today; will matter if REPL
    workloads run real volumes of calls. Easy to fix (name → idx hash)
    and worth doing before Tier 1 ships, since the alternative
    (bake-idx-into-bytecode) would close off the redefinition story.
    *(Now: Tier 4 substrate (`9af2d56`) added the funcIndex hash;
    `LookupFunc` is O(1).  Eager CallCache fill keeps shadow
    semantics correct.)*
- **Tiered plan** (each tier shippable on its own; see
  `plan-repl.md` for entry-point names, per-step commit tables,
  and the live follow-up state):
  1. ~~**Load-then-poke.**~~ **LANDED (2026-04-30).** Load a `.bn`
     module the normal way; prompt accepts immediate-mode entries.
     Multi-line input via paren-aware accumulator.  Auto-`println`
     wrap of bare exprs deferred (gated on interfaces).
  2. ~~**Add new top-level decls at the prompt.**~~ **FULLY LANDED
     (2026-04-30 → 2026-05-28).**  All decl kinds: `func` (incl.
     methods), `const`, `var` (typed + untyped-with-literal-init +
     var-initializer evaluation), `type` (aliases, named
     non-struct, structs incl. managed-field).  Body-introduced
     new-managed-aggregate dtor regen also landed (2026-05-28,
     `EnsureReplBodyHelpers`).
  3. ~~**Forward references.**~~ **LANDED for `func` decls
     (2026-05-05).**  Pending-validation queue in the type checker;
     parked decls retry on every newly-resolved name.  Pending
     types / vars / consts remain (see follow-ups above).
  4. ~~**Redefinition.**~~ **LANDED in full (2026-05-01 →
     2026-05-05).**  Compatible-sig: in-place rebind keeps
     CallCache valid.  Incompatible-sig: `LowerOneFuncShadow`
     appends + re-points funcIndex; old callers retain old VMFunc
     via eager-filled CallCache.  Methods follow the same rules,
     keyed on qualified `<pkg>.<TypeName>.<Method>`.  Substrate
     `9af2d56`; shadow `63cc49b`; method redef `026ad22`.
     Refcount-aware shadow warning + forced-shadow escape hatch
     are remaining follow-ups.
  5. ~~**Mid-session imports.**~~  **LANDED** 2026-05-29 via
     `78685ac3`.  evalReplImport in cmd/bni/repl_import.bn
     drives the existing loader's LoadImports for incremental
     transitive loads, brackets the per-package InitModule
     loop with SaveAliasMapState/RestoreAliasMapState so the
     session's main alias map survives, and routes through
     c.RegisterReplImport to make `foo.X` resolvable from
     subsequent prompt entries.
- **What's free / "should-do-now-anyway"**:
  - ~~The audit itself~~ — done; `plan-repl.md` is the live doc.
  - ~~Per-decl entry points exposed opportunistically when the
    relevant code is touched for unrelated reasons.~~  Done as part
    of Tier 1 + Tier 2 (parser ParseExpr / ParseStmtList /
    ParseTopLevelDecl / IsAtTopLevelDecl; types CheckExprInScope /
    CheckStmtListInScope / CheckDeclInScope / CheckMainPersistent;
    ir GenSyntheticFunc / GenDecl; vm LowerOneFunc / CallByVMFunc).
  - ~~Name → idx hash in `LookupFunc`.~~  Solved differently:
    per-VMFunc CallCache (commit `6c8e0c0`) memoizes the lookup
    result per call site, removing the per-dispatch scan; lazy fill
    on first call; explicitly designed for REPL invalidation.
  - A minimal pretty-printer (probably `pkg/replprint`, leaning on
    `pkg/buf.CharBuf`). Useful well beyond REPL.  **Deferred until
    interfaces land** — `bootstrap.println` is a temporary hack
    scheduled for removal; building features on top of it would
    entrench the hack.  See "Pretty-printer" in plan-repl.md and
    the auto-`println` deferral note.
- **Decisions / non-decisions in adjacent work to pressure-test**:
  - **Function values** (`plan-function-values.md`): a function value
    must be a *stable identity for what it refers to*, not for the
    bytes of the underlying body. Re-binding the body of an
    interpreted function does not invalidate function values pointing
    at it. Add this clause to that plan when it moves out of DRAFT.
  - **Compiler/interpreter interop** (above): the package descriptor
    is shaped right for REPL — interpreted-package descriptors are
    mutable, compiled ones are read-only. Sorted-by-mangled-name
    layout interacts with "add a new exported function mid-session"
    (positions move when a new export sorts in); confirm that's the
    intended behavior.
  - **Layout extraction** (archived — see `historical-notes.md`): expose a
    runtime-extensible type universe, not a closed-at-startup one.
  - **IR/backend cleanup**: no closed-world assumptions in the shared
    layer.
- **What this entry is NOT**:
  - A REPL implementation plan — that lives in `plan-repl.md`.
  - A relitigation of REPL semantics — those are decided; if they
    change, update `claude-notes.md` first.
- **Open design questions worth pinning before Tier 1 starts** —
  resolved as part of the Tier 1 work:
  - ~~Top-level prompt grammar.~~  Settled as bare statement list;
    auto-`println` wrap deferred until interfaces (above).  `func`
    decls are dispatched to the decl path via
    `parser.IsAtTopLevelDecl`.
  - ~~Error recovery.~~  Implemented exactly as proposed: parse /
    type / IR-gen / lower / runtime errors in immediate mode print
    and return to prompt; loaded state unaffected.  Verified by
    `e2e/repl.sh` cases.
  - ~~Where pretty-printing lives.~~  Deferred (see above).
  - ~~Sentinel for "no result".~~  Nothing — empty stmt lists are
    skipped by `evalReplStmtList` before reaching IR-gen.
  - ~~Whether REPL is a separate `cmd/bnrepl` or a `--repl` flag on
    `cmd/bni`.~~  Settled as `--repl` flag on `cmd/bni`.
    `scripts/build-bni.sh` (commit `22ea525`) is a convenience
    wrapper for casual use.

### ~~Remove the build.bni-dedup workarounds after a BUILDER bump~~ — ✅ DONE & LANDED `9c2ac789` (2026-06-19)

BUILDER reached `bnc-0.0.9` (its bundled `build.bni` has `ARCH_AARCH64` in `ifaces/core`, and
both `bnc` and `bnlint` parse `#[build]` — verified directly against the bundle), so all three
temporary workarounds were removed: the `ARCH_ARM64` back-compat alias (`buildcfg.HostConfig` now
compares `ARCH_AARCH64`), the `lint.sh` ungated-`build.bni` shim (plain `bnlint` restored), and the
`binate-paths.sh` `[ -d ]`-guarded `ifaces/targets/<key>` lookup + `TARGET_DIR` (`set_target_extras`
arm32-baremetal dirs preserved). Verified: gen1 self-host build, hygiene 15/15 (lint de-shimmed),
conformance build-constraint smoke 731/733/735/736/737/746/747 (7/0). The `pkg/bootstrap` `#[build]`
collapse (the "bonus" below) is carried forward to claude-todo.md as its own entry.

- **What**: the build-constraint migration collapsed `pkg/builtins/build` to one
  `#[build(...)]`-gated `ifaces/core/pkg/builtins/build.bni` and re-sourced the
  build config from the active target (binate `5a8714d8` / `b64b21fd` /
  `b0bd1096`).  Because the pinned BUILDER (`bnc-0.0.8`) predates BOTH the
  `ARCH_ARM64 → ARCH_AARCH64` rename AND `#[build]` parsing, three TEMPORARY
  workarounds were needed:
  1. an `ARCH_ARM64` alias (`= ARCH_AARCH64`) in `build.bni`, referenced by
     `buildcfg.HostConfig`, so `cmd/bnc` (which now imports `build`) compiles
     under the bundle's pre-rename `build.bni`;
  2. a throwaway ungated-`build.bni` shim in `scripts/hygiene/lint.sh` (prepended
     to `-I`) so the bundled bnlint — which can't parse `#[build]` and now loads
     `build` transitively via `buildcfg` — typechecks against the shim, not the
     gated file (keeps the fast bundled-bnlint path);
  3. a `[ -d ]`-guarded `ifaces/targets/<key>` lookup in `scripts/binate-paths.sh`
     so a bundle's old per-target `build.bni` (the bundle still ships
     `ifaces/targets/`) is still found when compiling cmd/bnc, while being a
     no-op against the current tree (`build` lives in `ifaces/core`).
- **Removal condition**: bump `BUILDER_VERSION` to a snapshot built AFTER this
  migration (its `build.bni` has `ARCH_AARCH64` and lives in `ifaces/core`, and
  its bnc/bnlint parse `#[build]`).  Then: drop the alias + switch
  `buildcfg.HostConfig` to `ARCH_AARCH64`; remove the lint shim (restore the
  plain bundled-bnlint invocation); drop the guarded `ifaces/targets` lookup +
  `TARGET_DIR` from binate-paths.  Each is comment-flagged in-tree
  (`TEMPORARY`/`Remove once BUILDER`).  Full plan +
  workaround list in
  [`plan-impls-constraints-migration.md`](plan-impls-constraints-migration.md).
- **Bonus**: the same bump would also let `pkg/bootstrap` be collapsed onto
  `#[build]` (it's in cmd/bnc's BUILDER-compiled tree, currently left
  path-selected — see that plan doc).

### ~~`__c_call` should support void returns~~ — ✅ DONE & LANDED `5e23923f` (2026-06-20)

`__c_call` now accepts a `"void"` return spelling — `__c_call("free", "void", ptr)` /
`__c_call("exit", "void", code)` — for a void C call with no result, replacing the
dummy-scalar-return-then-discard placeholders.  The string `"void"` sits in the return-type slot
(no `pkg/c` marker-types package; the string mechanism is extensible to other Binate-less C type
names later; only `"void"` is accepted today).  Parser: `"void"` → nil `TypeRef`; checker: nil
`TypeRef` ⇒ void; IR-gen + `EmitCCall`: the result-less `newVoidInstr` form (ID -1); LLVM lowering:
`call void @sym(...)`; the native backends already skip result collection for a result-less instr.
`rt.Exit`/`rt.RawFree` converted (the placeholders below).  Conformance `866_c_call_void_return`
(xfail in the VM modes — the VM does no FFI, by design); parser unit tests (void→nil TypeRef; a
non-`"void"` string rejected).  Verified: gen1 self-host build, hygiene 15/15, refcount matrix
105/0 on LLVM + VM + native aa64.  Follow-up: the rt.bn conversion needed a temporary `LINT_SKIP`
(the BUILDER-bundled bnlint predates the syntax) — removal tracked in active "Remove the
void-`__c_call` lint skip after a BUILDER bump".

- Today `__c_call` "requires a return type" and `checkCCall` rejects
  void ("void and struct returns not yet supported"). So calling a void
  C function (`free`, `exit`) means declaring a dummy scalar return
  (e.g. `int`) and discarding it as a bare statement — see the
  placeholders in `impls/core/libc/pkg/builtins/rt/rt.bn`
  (`__c_call("free", int, ptr)` / `__c_call("exit", int, code)`).
- **Fix**: accept a void return spelling for `__c_call` (and a bare-
  statement form), so void C calls don't carry a misleading return type.
- Surfaced 2026-06-03 by the drop-libc work.

### ~~Reorganize stdlib tests to meet the per-file test-coverage bar~~ — ✅ DONE (landed `e5ed6574` / `a0f6d86b` / `072fccdf`, 2026-06-20)

`scripts/hygiene/test-coverage.sh` now enforces "every non-test `.bn` has a sibling `_test.bn`" on
`impls/` (extended `d7c6b323`), and the temporary whitelist was whittled from 24 to 14 — now holding
ONLY genuine exceptions (no "TEMPORARY, add a test later" entries):
- **Math (`e5ed6574`)**: 6 per-function files got their own `_test.bn` — `acosh`/`atanh`/`tanh`/`modf`
  relocated out of family-named files; `asin`/`acos` + `logb`/`ilogb` newly tested.
- **OS (`a0f6d86b`)**: `mode`/`fileinfo` got per-file tests (relocated out of the catch-all
  `os_test.bn`); the per-platform `stat_*` / `os_errno_*` / `os_baremetal` files were classified as
  documented genuine exceptions (each `stat_*` is a distinct kernel struct-stat ABI; the errno split
  is a `__c_call` link-symbol constraint) — tested via `stat_test.bn`/`os_test.bn` + `e2e/stat-values.sh`.
- **strconv (`072fccdf`)**: white-box tests for the all-internal `atof_lex.bn`/`atof_convert.bn` (the
  lexer byte-class/underscore validators + `lexInfo` fields; `pow10`/shifts/`floorBits`/the
  round-half-to-even `roundedSig` + `convert`/`convertHex` bit patterns).
The 14 remaining whitelist entries are genuine exceptions: math internal helpers (`bessel01_asymp`,
`trig_reduce`), constants (`const`), the `big/nat` type (tested across operation files), the os
per-platform ABI files, and the baremetal `bootstrap`/`rt_baremetal` variants (the synthetic test
runner can't run them as packages).

`scripts/hygiene/test-coverage.sh` now enforces "every non-test `.bn` has a sibling `_test.bn`" on
`impls/` too (landed `54a15fc6`); the TEMPORARY whitelist in
`scripts/hygiene/test-coverage.whitelist` is down to **18** (was 24).  The bar is per-file tests
with very few genuine exceptions — remaining work to whittle it down:
- **Math — ✅ DONE (landed `e5ed6574`)**: the 6 per-function files got their own `_test.bn`
  (acosh/atanh/tanh/modf relocated out of family-named files; asin/acos + logb/ilogb newly tested).
  The 4 still-whitelisted math entries are genuine exceptions: internal helpers
  (`bessel01_asymp.bn`, `trig_reduce.bn`), constants (`const.bn`), and `big/nat.bn` (a type tested
  across `nat_arith/div/shift_test.bn`).
- **OS per-platform files** (`os_errno{,_darwin,_linux}.bn`, `stat_{darwin,linux_aarch64,linux_arm32,
  linux_x64}.bn`, `os_baremetal.bn`, `fileinfo.bn`, `mode.bn`): amalgamate via `#[build(...)]` into
  fewer files (e.g. one `os_errno.bn` + one `stat.bn` gated by os/arch), each with a test — better
  than per-platform sprawl.  A real refactor (build structure + multi-platform verify) — scope with
  the user first.
- **strconv `atof_lex.bn`/`atof_convert.bn`**: split from `atof.bn`, covered by `atof_test.bn` — add
  per-file tests or keep as a genuine exception.
- **Baremetal `bootstrap.bn`/`rt_baremetal.bn` variants**: share the existing bootstrap exception
  (the synthetic test runner can't run them as packages — see the whitelist note).
Goal: the whitelist holds only genuine exceptions.
