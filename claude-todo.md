# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## MINOR (entry / under-enforcement) — the `main` entry-point signature is not enforced (2026-06-22) — 🔴 OPEN

`func main(x int)` and `func main() int` compile, LINK, and RUN (the extra
parameter is ignored; a value-returning main's result is discarded) instead of
being rejected. Spec `prog.main.signature` (§17.3) says the entry is `func main()`
with no parameters and no results, and §17.3.1 says a wrong-shaped main is a
link-time failure -- but the entry synthesis accepts any `main`. Found authoring
`conformance/spec/17-program`. Pinned: `009_err_main_wrong_signature_xfail`
(xfail.all). Low priority (a wrong-shaped main is unusual), but it is silent
acceptance of an ill-formed entry point.

---

## MAJOR (codegen / ABI / memory-unsafe) — arm32 `MaxAlign=4` wrongly caps `int64`/`uint64`/`float64` alignment to 4 (AAPCS wants 8) → undersized C-interop structs → SIGSEGV in `os.Stat` (2026-06-21) — ✅ FIXED & LANDED (`f4b934ce`)

**Symptom.** `conformance/stdlib/os/003_stat` SIGSEGVs under `builder-comp_arm32_linux`
(uncaught target signal 11 in qemu). A minimal `os.Stat("/tmp")` that prints nothing
about the result also crashes — the fault is *inside* `os.Stat`, not a field accessor.

**Root cause.** `cmd/bnc/target.bn:setArm32Layout` set `t.MaxAlign = 4`. `MaxAlign`
caps every type's alignment, so on arm32 the 8-byte fundamental types (`int64` /
`uint64` / `float64`) align to 4, not 8. AAPCS — the ARM EABI used by BOTH
arm-linux-gnueabihf and bare-metal arm-none-eabi — aligns `long long` / `double`
to 8 even though pointers and `int` are 4. So `os`'s `osStat` (glibc `struct
stat64`, 104 bytes *with* the 8-align pads at the `st_size` / `st_blocks`
boundaries) lays out as **96 bytes** under the 4-cap; the kernel's `stat64`
syscall writes 104 bytes into the 96-byte buffer → 8-byte overrun → corruption →
crash. More broadly, EVERY arm32 struct with an 8-byte member is mislaid vs the
platform ABI — latent, manifesting wherever a Binate struct must match C/kernel
layout (`stat`, future `dirent`/`timespec`/etc.).

**Verified.** Host arm32 IR probe `struct{uint64;uint32;uint64}`: `sizeof` = 20
under `MaxAlign=4` (2nd uint64 at offset 12, 4-aligned); = **24** under
`MaxAlign=8` (offset 16, 8-aligned), matching AAPCS. Under qemu (Docker
linux/amd64 + qemu-arm): `003_stat` and `006_readdir` PASS with the fix;
`290_sizeof_alignof`'s arm32 expected (linux + baremetal) corrected
`alignof(int64)`/`alignof(float64)` 4→8, verified against a clean run. 385's
arm32 IR is byte-identical under MaxAlign 4 vs 8 (the change touches only
8-byte-member layouts; 290 was the sole arm32-expected test asserting such).

**Fix (`f4b934ce`).** `t.MaxAlign = 8` in `setArm32Layout` + the two 290 expected
corrections. Raises the cap so 8-byte types get 8-align; 4-byte types
(int/pointer) are unaffected (natural align ≤ 4). Self-consistent for
pure-Binate code AND correct for C interop. Also fixes the `TestStatIoArm32`
unit test (same `osStat`/`stat64` path).

**Follow-ups.** (1) coverage — add a `MaxAlign==8` assertion to the arm32 layout
test in `cmd/bnc/target_test.bn` (it currently checks PointerSize/IntSize only).
(2) CI is the full-matrix gate (arm32_linux + arm32_baremetal conformance + unit);
the local full arm32_linux suite is slow under triple-emulation (qemu-user hangs
on guest segfaults so each crash-intended test costs the 10s timeout).

## TEST GAP (not a compiler bug) — matrix/generic conformance cells pin LP64-only `.expected`; the compiler is correct under ILP32 (arm32) (2026-06-21) — ✅ FIXED & LANDED (`9254f848`)

**Symptom.** 10 `builder-comp_arm32_linux` conformance failures where the actual
output is the CORRECT ILP32 value but `.expected` holds the LP64 value:
- `matrix/scalar/{add,sub}/64/unsigned`, `matrix/scalar/div/64/{signed,unsigned}`:
  `println(cast(int, <wide value>))`. On arm32 `int` is 32-bit, so `cast(int,…)`
  truncates/sign-reinterprets. E.g. add/64 → `-1` (`0xFFFFFFFF` as int32); expected
  `4294967295` (the int64 value).
- `matrix/operator/{neg,bitnot}/{named,plain}/uint32`: `cast(int, uint32)` → signed
  int32 on arm32 vs zero-extended int64 on LP64. neg → `-5`; expected `4294967291`.
- `850_generic_cross_pkg_alias_collision`, `864_..._implicit_segment_collision`:
  `sizeof(genlib.Box[int])` — `tag(int) + dep.Pair(2×int)` = 12 on arm32 (int=4),
  24 on LP64 (int=8). The cross-pkg collision is correctly AVOIDED on arm32 too;
  12 is the right ILP32 size.

**Root cause.** `gen-scalar-matrix.py` / `gen-operator-matrix.py` compute `.expected`
as an arbitrary-precision Python value and never simulate the final `cast(int,…)`
(or the `sizeof`) at the TARGET int width — implicitly LP64. The cells were
authored for "64-on-32 / sub-word VALUE correctness" but only validated on a
64-bit host.

**Fix (`9254f848`).** Parameterized both generators' `cast(int,…)` simulation by
target int width; they now emit `.expected.builder-comp_arm32_{linux,baremetal}`
only where the 32-bit result differs (LP64 `.expected`/`.bn` untouched; stale
overrides auto-removed; both idempotent). 850/864 (hand-written) got the same
overrides (=12). All 10 cells verified PASS under qemu. NOT a compiler bug
(verified correct via IR + qemu).

## MAJOR (stdlib / os ReadDir) — `os.ReadDir` used the 32-bit C `readdir`, which `EOVERFLOW`s on >2^32 inodes (silent listing truncation); arm32 also assumed the 32-bit dirent layout (2026-06-22) — ✅ FIXED & LANDED (`1686aac9`)

**Symptom.** `conformance/stdlib/os/006_readdir` failed `builder-comp_arm32_linux`
in CI (`foundMarker=0`) but PASSED locally. Cause: `ReadDir` called the 32-bit C
`readdir`, which returns `EOVERFLOW`→NULL for a directory entry whose `d_ino`
exceeds 2^32; `ReadDir` can't distinguish that from end-of-stream, so it silently
truncates the listing. CI's `/tmp` has large inodes (marker missed); a host with
small inodes (the Docker repro container) works — explaining the discrepancy.
Also `readdir_linux_arm32.bn` assumed a 32-bit `struct dirent` (`d_name@11`); the
real layout on modern-glibc Linux (cross-libc C-probe: `offsetof(d_name)==19`) is
the 64-bit one, identical to x86_64/aarch64.

**Fix (`1686aac9`).** Linux `ReadDir` now calls `readdir64` (64-bit `d_ino`, no
`EOVERFLOW`) with the `@19` layout, unified into one `readdir_linux.bn` for all
Linux arches (on 64-bit `readdir64`≡`readdir`). `readDirEnt` moved per-platform
(the symbol differs: Linux `readdir64` vs macOS `readdir`); macOS keeps `readdir`
+`@21`. New `readdir_{linux,darwin}_test.bn`. Pre-existing (predates the MaxAlign
work; the readdir e2e only runs on the host, so the arm32 layout/symbol was never
validated). Verified: 006 + os unit pass on arm32 (qemu) + darwin; hygiene green.
The large-inode `EOVERFLOW` case can't be reproduced where inodes are small, but
`readdir64`'s 64-bit `d_ino` eliminates that failure class by construction.

## CRITICAL (compiler crash / SIGSEGV) — a **tagless** `switch { … }` null-derefs the compiler in IR-gen (2026-06-21) — 🔴 OPEN — REPRODUCED

**Symptom.** ANY tagless switch crashes `bnc` with SIGSEGV (no diagnostic). Minimal
repro: `func main() { switch { default: println("d") } }` → `EXC_BAD_ACCESS
(address=0x0)` in `bn_pkg__binate__ir__genExprInner`. Also crashes `switch { case
n > 2: … }` and `switch { case 3: … }`. A **tagged** switch is fine (`switch b {
case true: … }` and `switch n { case 1: … }` compile+run correctly).

**Root cause (likely).** The tagless form (`SwitchStmt` with no tag Expression,
§14.10 — the idiomatic if/else-if replacement, equivalent to `switch true`) lowers
by generating the tag expression, but the tag is **absent (nil)** — `genExprInner`
is called on the nil tag and dereferences it. The tagged path supplies a real tag,
so it is unaffected. Fix: in the switch lowering, synthesize a `true` tag (or take
the tagless branch that compares each case as a bool condition) instead of gen-ing
the nil tag expression.

**Discovery.** Authoring `conformance/spec/14-statements` (probing `stmt.switch.tag`).
**No existing conformance test uses a tagless `switch {`** (grep: 0 files), so this
was never caught. The spec §14.10 open item `stmt.switch.tagless-bool` notes only
that tagless **non-bool** cases are wrongly *accepted* — it predates / misses this
crash (the crash hits bool cases too).

**Pinned.** `conformance/spec/14-statements/<NNN>_switch_tagless*` (xfail.all, to be
added with the chapter) — a tagless-switch positive that currently crashes.

**Proposed stale-note correction (separate, await OK):** spec §14.5's
`stmt.incdec.lvalue` "MAJOR implementation defect" note is **stale** — `a[i]++`,
`p.f++`, `(*p)++` were FIXED+LANDED (`6a2f551f`, claude-todo-done) and all increment
correctly now; the §14.5 status line + open-note should be dropped.

---

## MAJOR (codegen / invalid IR) — a `switch` on a SUB-64-bit integer tag with an UNTYPED integer-literal case emits invalid IR (`i64` vs `iN`) (2026-06-21) — 🔴 OPEN — REPRODUCED

**Symptom.** `switch t { case 1: … }` where `t` has a sub-64-bit integer type
(`char`/`uint8`, `int8`, `int16`, `int32`) and the case value is an UNTYPED integer
literal makes codegen emit the literal as `i64` and compare it `icmp eq iN` against
the narrower tag → LLVM verifier/clang error `'%v' defined with type 'i64' but
expected 'i8'` (or i16/i32). `int`/`int64` tags are fine (literal is already i64),
and a CAST case value (`case cast(char, 65):`) is fine. So the case literal is not
narrowed to the tag's type before the equality comparison.

**Discovery.** Adversarial review of `conformance/spec/14-statements` (probing
`stmt.switch.tag` case-assignable-to-tag with a `char` tag). Per spec §14.10 the
case value must be *assignable to the tag's type*, and an untyped literal coerces —
so `case 64:` against a `char` tag is well-formed and should compile.

**Fix (likely).** In the switch lowering, coerce/narrow each case constant to the
tag's type (as a normal assignment/comparison would) before the `icmp`, rather than
emitting the untyped literal at its default `i64` width.

**Pinned.** `conformance/spec/14-statements/134_switch_subword_tag_untyped_case_xfail`
(xfail.all).

---

## MAJOR (codegen / memory-unsafe) — a bare raw-pointer local `var p *T` (no initializer) is NOT zero-initialized → reads STACK GARBAGE (non-nil) (2026-06-21) — 🔴 OPEN — REPRODUCED

**Symptom (silent, memory-unsafe).** `var p *T` with no initializer is not zeroed
when its stack slot was previously dirtied: `present(p)` returns **true** (garbage
non-nil) instead of false. Repro: `func main() { var a int; var b int; var p *int;
_=a;_=b; if !present(p) { println("nil-ok") } else { println("GARBAGE") } }` prints
`GARBAGE`. A bare `var p *int` as the FIRST/only local happens to read nil (fresh
stack), masking it. An explicit `var p *int = cast(*int, 0)` is correct.

**Scope (probed).** ONLY the single-word RAW POINTER `*T` is affected. Zero-init is
CORRECT for: scalars (int/bool/float — fixed in `2d856a0f`), named arrays
(`77bdd64c`), structs (zero-filled fieldwise, incl. a `*T` field), managed pointers
`@T`, raw slices `*[]T` (2-word, zero-filled), and function values `*func(...)`. So
this is the raw-pointer facet that the earlier scalar/array zero-init fixes missed.
Spec `decl.var.zero-init` (§9.2): "pointers ... to nil". Confirmed (conformance/spec/09 045): manifests on the LLVM backends
(builder-comp/-comp-comp/-comp-comp-comp) AND native aarch64; the bytecode VM and arm32 happen to read nil.

**Root cause (likely).** The IR-gen default-init path (the one `2d856a0f` made emit a
typed zero-store for SCALAR locals via EmitConstBool/Float/Int) does not classify a
raw pointer `*T` as a scalar, and unlike struct/array/slice locals a `*T` is not
aggregate-zero-filled by `emitAlloc` — so a bare `*T` local gets no zero-store at all.
Fix: emit a null-pointer zero-store for a bare raw-pointer local (extend the scalar
default-init path to cover `*T`, or aggregate-zero it), putting the store in the IR so
both backends agree.

**Pinned.** `conformance/spec/09-declarations-and-scope/045_zero_init_raw_ptr_xfail`
(per-mode xfail: builder-comp, -comp-comp, -comp-comp-comp, native_aa64).

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

## MAJOR (checker / silent integer-wrap) — `:=` (short-var default type) does NOT fit-check a literal exceeding the target int → silent wrap (2026-06-21) — 🔴 OPEN — REPRODUCED

**Symptom (silent wrong value).** `x := 0xFFFFFFFFFFFFFFFF` compiles clean (rc=0, no
diagnostic) and binds `x` to int **-1** — the literal (2^64-1, a valid union-range
literal but NOT in int64) is silently WRAPPED to the default `int` instead of being
rejected. The explicit-type path is correct: `var x int = 0xFFFFFFFFFFFFFFFF` →
`cannot assign untyped int to int`. So the `:=` default-type path omits the
range/fit-check that `var x T = …` performs.

**Spec.** `const.default.int-width` (§6.2): a literal whose value does not fit the
target's `int` "cannot use the default and shall be given an explicit type." So
`x := 0xFFFFFFFFFFFFFFFF` must be rejected, not wrapped.

**Discovery.** Authoring `conformance/spec/06-constants` (const.default.int-width).
**Scope.** Front-end (checker/const-fold), so the wrong value is target-invariant
(all modes). Likely the `:=` path infers the default type and stores the literal
without re-fit-checking against it.

**Fix (likely).** In the short-var (`:=`) default-typing path, fit-check the
literal/const-expr against its chosen default type (the same check the explicit
`var x T = e` assignment does), and reject on overflow.

**Pinned.** `conformance/spec/06-constants/007_err_default_int_width_exceeds` (xfail.all,
asserts the `cannot assign untyped int to int` rejection the explicit path gives).

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

## MAJOR (arm32 codegen / silent wrong compare) — `int64 == <inline untyped negative const-expr>` is miscompiled on the 32-bit ARM backend (2026-06-21) — 🔴 OPEN — REPRODUCED

**Symptom (silent wrong boolean, arm32 only).** `var n int64 = -42; if n == 0 - 42`
evaluates to **false** on `builder-comp_arm32_baremetal` (the spec's `-K == 0-K`
negation identity, §6.3) — it is true on every 64-bit mode (LLVM, VM, native aa64).
The inline untyped negative const-expression `0 - 42` is not widened to 64 bits
before the `int64` equality, so the high word mismatches and the compare is wrongly
false. NARROW: `n == -42` (a literal), `var m int64 = 0 - 42; n == m` (via a var),
and `(0 - 42) == -42` (pure untyped const) all work on arm32 — only the inline
`int64 == negative-const-expr` form fails.

**Discovery.** Authoring `conformance/spec/06-constants` (const.int.sign).
**Likely fix.** Sign-/zero-extend (widen) the untyped const-expr operand to the int64
operand's width before emitting the 32-bit-pair comparison on arm32 (the const-expr
is being materialized as a 32-bit value with no high word).

**Pinned.** `conformance/spec/06-constants/034_int_sign_negation_int64_xfail`
(xfail.builder-comp_arm32_baremetal). 033 was restructured to avoid the form so it
stays green on arm32.

## Ch.7 spec-conformance findings (2026-06-20, authoring `conformance/spec/07-types`) — 🔴 OPEN

Four findings surfaced while authoring the Ch.7 type spec tests; each is pinned by an xfail.

1. **MAJOR (type-checker / wrong-code) — cross-package distinct named SCALAR types wrongly inter-assign.** Same-package `type A int; type B int; var b B = a` correctly rejects ("cannot assign A to B"), but cross-package `red.T -> blue.T` (each `type T int`) **compiles** without a cast — cross-pkg named-type identity is not enforced for scalar underlyings (type.named.identity, type.named.assignability). Possibly related to the int↔int64 identity-by-width bug, but distinct (that one is same-package width; this is cross-pkg). Pinned: `conformance/spec/07-types/049_named_identity_cross_pkg` (xfail.all).

2. **MAJOR (native-aa64 codegen) — a distinct named type over a managed-slice (`type Buf @[]int`) miscompiles on the native AARCH64 backend.** Index/len/slice/assignment of the named managed-slice produce wrong output or crash on `builder-comp_native_aa64`, but are correct on LLVM, the VM, gen1, gen2, AND the 32-bit ARM native backend (arm32_baremetal passes). So it is native-aa64-specific (the named-managed-slice transparency landed for LLVM/VM via `88e13633`/`b43a0057`; native-aa64 has a gap). Pinned: `033_named_transparency`, `036_named_assignability_composite` (xfail.builder-comp_native_aa64-comp_native_aa64).

3. **MINOR (type-checker / opaque encapsulation) — direct field access on an OPAQUE cross-package type is not rejected.** `b.v` on an `@bx.Box` (Box forward-declared in the .bni, full body in the .bn) **compiles** instead of "cannot access field on this type" (type.opaque.field-rejection). The build enforces .bni surfaces for symbols (16-packages/031) but not for opaque-type field access. Needs investigation: real gap vs build-model artifact. Pinned: `222_err_opaque_field_access` (xfail.all).

4. **MINOR (codegen) — managed pointer-to-array `@([N]T)` indexing is broken.** `(*m)[i]` on an `@([3]int)` (the spec's heap-managed-array form, type.value.managed-arise / type.ptr.array-parens) emits an "invalid getelementptr indices" codegen error; `m[i]` gives "cannot index this type". The raw `*([N]T)` form works (`(*p)[i]`). Not pinned by a dedicated test (146 avoids indexing the managed form); noted for follow-up.

---

## Ch.5 spec-conformance findings (2026-06-20, authoring `conformance/spec/05-lexical`) — 🔴 OPEN

Surfaced while authoring the Ch.5 lexical spec tests. The two spec/impl
**divergences** (`\uHHHH` escape; `1.foo` greedy-float-vs-selector) moved to
[`spec-todo.md`](spec-todo.md) — they need a "fix spec or fix impl" decision and
are pinned by `055`/`035`. The minor unary-`+`-rejected question is there too.
The items below are settled-intent impl gaps already pinned by xfails.

1. **Open item now pinned — single-byte character-literal constraint (`lex.literal.char.one`) is not enforced.** Empty `''` silently decodes to `0x00`; multi-byte `'ab'` silently truncates to its first byte (`'a'`=97); neither is diagnosed. Already acknowledged as an open item in spec §5.10/§5.14. Pinned: `056_char_empty_xfail`, `057_char_multibyte_xfail` (xfail.all). (No new decision; the xfails make it reproducible so Annex C flips when a diagnostic is added.)

2. **Reused existing gap — `[...]T{}` inferred-length array literals unimplemented.** `var a [...]int = [...]int{...}` is rejected `expected expression` (same gap as `conformance/spec/13-expressions/041`). The `...` token itself lexes as one token. Pinned for Ch.5's `lex.punctuation.set` `...` coverage by `122_punct_ellipsis_xfail` (xfail.all).

**Stale-note correction (DONE) in `docs/spec/05-lexical-elements.md`:** the §5.11 note claiming unknown escapes are "silently decoded … backslash dropped … no diagnostic" was **stale** — they are rejected with `unknown escape sequence` (and bad `\x` with `\x escape requires two hex digits`). Corrected; the Ch.5 negatives `047`–`054` pin the rejection (green). (The `\uHHHH`/§5.1 and §5.8 reconciliations stay open in [`spec-todo.md`](spec-todo.md).)

---

## Ch.15 spec-conformance findings (2026-06-21, authoring `conformance/spec/15-builtins`) — 🔴 OPEN

Authoring + reviewing the Ch.15 built-in tests surfaced one impl gap now pinned and
TWO stale spec notes (both flagged defects are actually FIXED). No new bugs.

1. **bit_cast to a SUB-WORD type, used DIRECTLY, is not narrowed in the VM / native-aa64 — now pinned.** `bit_cast(uint32, int32 -1)` read directly (not stored into a typed local first) leaks the high bits → reads as full-width −1, not 4294967295, on `builder-comp-int`, `builder-comp-int-int`, and `builder-comp_native_aa64-comp_native_aa64`. Correct on the LLVM backends and arm32; a *stored* `bit_cast` (`var u uint32 = …`) narrows and is fine. This is the bit_cast facet of the existing sub-word-narrowing gap (claude-todo ~line 814). Pinned: `conformance/spec/15-builtins/040_bit_cast_int_reinterpret` (per-mode xfail on the three failing modes).

2. **Stale §15.3 note — cast-to-sub-word on native-aa64 is FIXED.** The §15.3 implementation note still describes `cast` to a sub-word integer as miscompiled on native aarch64; that defect was resolved (`5f94558b` per claude-todo-done; 0 native_aa64 xfails remain in the suite). `034_cast_sub_word` passes on all modes incl. native_aa64 — NO xfail added. → drop the §15.3 note.

3. **Stale §15.7 note — `panic` VM no-op is FIXED.** The §15.7 "Open (MAJOR — dual-mode divergence)" note says `panic` is a no-op in the bytecode VM; that was resolved (`a4946ebe` per claude-todo-done). `131_panic_abort` aborts and PASSES on the VM (int / int-int). → drop the §15.7 vm-noop note. (The single-arg `*[]readonly char` signature "in progress" note is still accurate — leave.)

> _Side observation (not pinned)._ The `builtin.opaque-gate` constraint (`make`/`sizeof`
> of an opaque type rejected) is enforced when the layout is genuinely unavailable — a
> PURE `.bni` forward declaration with NO body compiled (`150`/`151` are green this way).
> It does NOT fire when the body `.bn` is compiled in the same tree (the layout leaks). The
> sibling `07-types/222` opaque-field-access xfail uses the body-compiled setup, so its
> xfail may be a test-setup artifact rather than a real impl gap — worth re-checking with a
> pure-.bni setup as a 222 follow-up.

---

## Ch.9 spec-conformance findings (2026-06-21, authoring `conformance/spec/09-declarations-and-scope`) — 🔴 OPEN

Authoring the Ch.9 tests surfaced the MAJOR raw-pointer zero-init bug (filed separately,
above) plus two MINOR items.

1. **MINOR (parser/checker) — `iota` is not resolved in a SINGLE-member grouped const block.**
   The spec §9.1 `decl.const.iota` says iota "is recognized only inside a grouped const block."
   The impl is stricter: `const ( X int = iota )` (one member) is rejected with `undefined: iota`
   — identical to the non-grouped case — and iota only resolves once the block has 2+ members
   (`const ( A int = iota; B )` → 0, 1). So iota availability is tied to multi-member grouped
   blocks, not merely the grouped form. The Ch.9 tests sidestep it (004 uses ≥2 members, 005 uses
   the non-grouped form), so nothing is pinned against the corner. Either the impl should resolve
   iota in a single-member grouped block, or §9.1 should say "a grouped block with two or more
   members." Not pinned (avoided in the tests).

2. **MINOR (underspecified) — package-level VAR initialization is declaration-order, not
   dependency-order; the spec doesn't pin it.** `var A int = B + 1; var B int = 10` makes `A == 1`
   (B is still 0 when A initializes), NOT 11. `decl.order.forward` guarantees the forward NAME
   reference resolves (it compiles), but the VALUE at init time follows declaration order. Go
   initializes package vars in dependency order; Binate does not, and §9.8 is silent on var-init
   order. → a spec-vs-impl decision (declaration-order vs dependency-order) for `spec-todo.md`.
   The Ch.9 tests do not assert any var-init-order value (forward-ref is tested via a function).

---

## Ch.20 spec-conformance findings (2026-06-22, authoring `conformance/spec/20-tier0`) — 🔴 OPEN

1. **MINOR/Provisional (lang / float-NaN total order + Hash) — the shipped `float32`/`float64`
   `Compare`/`Hash` do not realize the ratified IEEE total order at NaN.** The current `Compare`
   is `a<b ? -1 : a>b ? 1 : 0`, so any NaN comparison returns **0** (NaN compares "equal" to every
   value, incl. other NaNs and finites) — not a total order (breaks antisymmetry/transitivity), so
   `impl float64 : Orderable`'s promise is unmet at NaN. And `Hash` reinterprets the bit pattern, so
   distinct NaN bit patterns Hash **differently** while `Compare` calls them equal — violating
   `pkg0.lang.hashable` consistency. Ratified intent (§20.1 `pkg0.lang.float-nan`): IEEE total
   ordering (NaN sorts after +Inf) with a matching Hash. This is a known Provisional non-conformance,
   to revise when NaN-correctness is load-bearing. Pinned: current behavior is GREEN in
   `017_float_nan_compare_current` + `018_float_nan_hash_inconsistent`; the ratified intent is
   `019_float_nan_total_order_intent` (**xfail.all** — flips green when the IEEE total order lands).

2. **GAP (harness limitation, not a defect) — `pkg0.testing.testfunc` + `pkg0.testing.run` are not
   conformance-testable.** Both require the `--test` discovery/execution runner (`cmd/bnc --test` /
   `cmd/bni --test`); `conformance/run.sh` only runs ordinary programs (no `--test` plumbing). They
   are exercised by the unit-test suite, not conformance. Closing them would need a test-runner mode
   added to the harness. Left as documented coverage gaps (Ch.20 is 18/20). Candidate for an
   `untestable`/`framework` reclassification in `extract-rule-ids.py` (a denominator decision).

---

## MAJOR (VM / wrong-output) — `os.Stat(...).ModTime()` returns sec ≤ 0 under the bytecode VM (`builder-comp-int`); LLVM + native correct (2026-06-21) — 🔴 OPEN — REPRODUCED

**Symptom.** `conformance/stdlib/os/004_modtime_chain` (`fi.ModTime().ToUnix()`
then `yn(sec > 0)`) prints `0` on `builder-comp-int` (expected `1`) — `ModTime`'s
seconds come back ≤ 0. PASSES on `builder-comp` and `builder-comp_native_aa64`.
xfail: `004_modtime_chain.xfail.builder-comp-int`.

**arm32 facet — RESOLVED by `f4b934ce` (was the `os.Stat` osStat-overflow, NOT a
distinct bug).** 004 crashed on `builder-comp_arm32_linux` only because `os.Stat`
itself SIGSEGV'd (the `MaxAlign=4` osStat-undersize overrun) before `ModTime` was
reached. With the MaxAlign fix, a clean MaxAlign=8 gen1 runs `004_modtime_chain`
→ **PASS** (verified under qemu alongside `290_sizeof_alignof`). An earlier
"distinct chained-`ModTime().ToUnix()` aggregate-ABI" theory here was WRONG: it
came from probing with a non-deterministically-picked STALE `MaxAlign=4` gen1
(two compilers coexisted in /tmp; `ls -dt | head -1` mixed them), so the probes
hit the already-fixed `os.Stat` crash and a phantom os/main layout "divergence"
(a MaxAlign-4 disassembly vs a MaxAlign-8 main IR). So arm32_linux is green for
004; the only remaining 004 issue is the VM wrong-value below (xfail.builder-comp-int).

**Discovery / scope.** Surfaced when the chained-method `extractvalue`-on-scalar
codegen bug was fixed (`b19d69ef`) — that fix un-masked 004 (it now COMPILES
everywhere), exposing this separate VM-only wrong-value. NOT the codegen fix: the
minimal dependency-free repro for that fix, `890_chained_method_transitive_struct`
(same chained multi-return shape, no `os`), passes on ALL modes incl. the VM.
`003_stat` (plain `os.Stat` → `Size`/`Mode`, no `ModTime`) also passes on the VM,
so generic `os.Stat` marshaling works — it's `ModTime` specifically.

**Likely root (needs investigation).** `os` is INJECTED (native) in the VM, so
`os.Stat` returns a native `@FileInfo`; calling its `ModTime() time.Point` (a
by-value struct return) across the VM↔native injection boundary, or the
`time.Point`→`ToUnix` marshaling, drops the `int64` seconds. Candidates: the
cross-mode marshaling of a 16-byte struct RETURN (`{i64,i32}`) from an injected
method, or `time.Point`'s value flowing through the VM. Needs a narrower probe
(e.g. inject a method returning a known-nonzero `time.Point` and read it on the
VM side).

## MAJOR (IR-gen / wrong-code) — a method EXPRESSION over a named SCALAR type (`type Celsius int`; `Celsius.M`) miscompiles: direct call emits an undefined symbol `@bn_T__M`; the *func form compiles but null/garbage call-shim → SIGSEGV. Fails on BOTH compiled and VM backends (2026-06-20) — 🔴 OPEN — REPRODUCED

**Symptom (REPRODUCED).** `type Celsius int` with method `func (t Celsius) Plus(d Celsius) Celsius`. (a) Direct: `Celsius.Plus(a, b)` → LLVM "use of undefined value '@bn_Celsius__Plus'" (the method-expr target symbol is never emitted/named for a scalar receiver). (b) Via a function value: `var f *func(Celsius,Celsius) Celsius = Celsius.Plus; f(a,b)` → COMPILES (rc=0) but SIGSEGVs at the indirect call (null/garbage call-shim). The SAME pattern over a STRUCT type works, and a scalar method VALUE (`c.Plus`, bound) works — so the defect is specifically the (named-scalar type × method EXPRESSION) combination. Fails on builder-comp AND builder-comp-int (the VM), so it is a shared front-end/IR-gen defect, not LLVM-only.

**Discovery.** Authoring Ch.10 spec test `132_method_expr_named_scalar_noncapturing` (the spec, `func.method-expr`, explicitly allows `T` to be "a named-distinct scalar such as type Celsius int"). The test passes the method expression through a `*func` param and calls it → SIGSEGV.

**Proposed fix (needs investigation).** Find where the method-expression trampoline/target for a named scalar receiver is mangled/emitted; the scalar receiver path likely fails to emit (or mis-names) the `@bn_T__M` shim that the struct path emits correctly. Compare the scalar vs struct method-expr lowering. Remove `132_method_expr_named_scalar_noncapturing.xfail.all` when fixed.

**Test.** `conformance/spec/10-functions/132_method_expr_named_scalar_noncapturing` (positive, `.xfail.all`).

---

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

## MINOR/MAJOR (type-checker / assignability) — an impl of a SUB-interface is not assignable to its SUPER-interface: `impl R : Sub` where `interface Sub : Base`, then `var b *Base = &r` is rejected "cannot assign *R to *Base" (2026-06-19) — 🔴 OPEN

**Symptom (REPRODUCED, same-package, no generics).** `interface Base { foo() int }`, `interface Sub : Base { bar() int }`, `type R struct{...}`, `impl R : Sub`.  `var b *Base = &r` (r an R) fails the type-checker: `cannot assign *R to *Base`.  R satisfies Sub, and Sub extends Base, so R should satisfy Base transitively — the assignability / impl-satisfaction check does not walk the implemented interface's ANCESTORS.

**Discovery.** Adversarial review of the cross-pkg-generic transitive-impl wiring (`dfe60903`): the reviewer noted the imported-impl path records only the listed iface (not its ancestor closure, unlike local `collectImplsFromDecl`).  Investigating, the gap is upstream in the TYPE-CHECKER (assignability), independent of generics / cross-package / `dfe60903` — it fails identically same-package.  So the IR-side imported-impl ancestor-closure asymmetry (gen_impl.bn collectImportedImplsFromDecl vs collectImplsFromDecl) is currently UNREACHABLE behind this checker rejection; both should be fixed together (checker assignability first, then ensure the imported-impl path wires the ancestor (R, Base) vtable so cross-package inherited upcasts don't nil-vtable once the checker allows them).

**Proposed fix.** Type-checker: when checking `*R -> *I` assignability (and impl satisfaction), accept R if R implements I OR any descendant interface of I (walk the implemented interface's parent chain).  Then IR: extend collectImportedImplsFromDecl to record the ancestor closure (mirror collectImplsFromDecl) so the inherited (R, Base) vtable is wired for cross-package/transitive upcasts.

---

## MAJOR (IR-gen / wrong-code) — a cross-package `impl R : I` declared ONLY in an IMPORTED third package (not R's package, not the compilation root) is accepted by the checker but its (R, I) vtable is NOT wired at the construction site → null-vtable crash at dispatch (2026-06-19) — 🔴 OPEN

**Symptom (REPRODUCED, no generics, no inheritance).** `pkg/shape` declares `interface Talker { speak() int }`; `pkg/widget` declares `type Widget` + method `speak` (no impl); `pkg/glue` declares the SOLE impl `impl *widget.Widget : shape.Talker`.  A `main` that imports shape + widget + glue and constructs `var iv *shape.Talker = &w` (w a Widget) **compiles cleanly** (the checker sees the impl via the glue import — even forcing glue to link with a `glue.Touch()` call) but **SIGSEGVs at run time**: the interface value's vtable word is null.  The compiled backend faults on the null-vtable deref; the VM aborts on the null vtable.  Moving the impl into `widget` (R's own package) works, and an impl in `main` (the root, cf. conformance `055`/`spec 11-interfaces`) works — so the gap is specifically **the imported third-package-only impl**.

**Discovery.** Adversarial review of the Ch.11 spec tests pushed `iface.construct.visible-impl` to be tested as a genuine visibility scenario (impl exists but is/ isn't imported).  Building the positive companion (impl in a third package, imported → should dispatch) surfaced the crash.  Spec `iface.crosspkg.no-orphan` (§11.8) explicitly permits an impl "in any package … R's package, I's package, **or a third package**", so this is non-conformant.  Same `collectImportedImplsFromDecl` machinery as the ancestor-walk entry above, but a DISTINCT facet: here it's a DIRECT (R, I) impl (no inheritance) and the **checker accepts** (so the rejection-side of that entry does not apply) — IR-gen simply fails to emit/wire the (R, I) vtable reference at the construction site for an impl that lives in an imported package other than R's.

**Proposed fix.** IR-gen: when a construction site needs the (R, I) vtable, ensure the imported-impl path (`gen_impl.bn` `collectImportedImplsFromDecl`) records and emits/links the (R, I) vtable for an impl declared in ANY imported package (not just R's home package / the root), mirroring how a same-package or root impl is wired.  Likely the same code path the ancestor-walk fix touches; fix together and keep checker (already accepts) and IR-gen in lockstep.

**Test.** `conformance/spec/11-interfaces/062_noorphan_imported_third_pkg` (positive, `.xfail.all` — checker accepts, runtime null-vtable crash in every execution mode).  The green companion `055_noorphan_third_pkg` covers the working "impl in main (root third package)" case; `061_err_construct_no_visible_impl` covers the not-imported rejection.  Flip 062 to a normal positive (drop the xfail) when this lands.

---

## MINOR (type-checker / under-enforcement) — the primitive-impl carve-out (§11.10) is NOT enforced in the IMPL pass: a non-`pkg/builtins/lang` package may `impl <primitive> : <empty interface>` and it is wrongly ACCEPTED (2026-06-20) — 🔴 OPEN

**Symptom (REPRODUCED).** In package `main` (non-lang): `interface Empty {}` + `impl int : Empty` compiles and runs (`var iv *Empty = &x` works).  Spec `iface.canonical.carveout` (§11.10): "Exactly one package, pkg/builtins/lang … may declare methods **and impls** on the universe primitives … no other package may."  So this should be rejected.

**Root cause.** The carve-out gate (`AllowUniverseRecv`, set only for pkg/builtins/lang) is enforced in the METHOD-declaration pass (`resolveMethodReceiver`, check_decl_func.bn) — so `func (x int) m()` IS caught ("method receiver int is not a type defined in this package").  But an `impl <primitive> : I` where `I` has NO methods reaches no method-declaration check, and the IMPL-collection pass has no equivalent universe-receiver gate, so it slips through.  (A non-empty interface is still indirectly blocked because you cannot declare the methods to satisfy it.)

**Severity.** MINOR: an under-enforcement (accepts spec-forbidden code), not wrong-code or a crash.  Harmless in practice but a real spec-vs-impl divergence.

**Proposed fix.** Add the universe-receiver carve-out gate to the impl-collection pass (reject `impl R : I` when R reduces to a universe primitive and the current package is not pkg/builtins/lang), mirroring `resolveMethodReceiver`'s `AllowUniverseRecv` check.

**Test.** `conformance/spec/11-interfaces/085_err_impl_primitive_carveout` (negative, `.xfail.all` — currently accepted; flip to a normal reject when the gate lands; its `.error` pattern `primitive` is a best-guess at the eventual diagnostic).

---

## MINOR (import hygiene) — two non-wrong-code follow-ups from the file-scoped-imports work — 🟡 OPEN
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
(`var data *[]uint8 = sec.Data` — a borrow of a held `@[]uint8`).  Investigate per-site: is it a real
use-after-free risk (the raw view outliving the managed owner) or a safe borrow the rule
conservatively over-flags (the `@asm.Section`/`@asm.Assembler` owner outlives the borrow)?  Then
either fix the code or tune the rule, and un-skip those 5 packages.  (The other `LINT_SKIP` compiler
entries — `pkg/binate/{vm,repl,interp}` — are BUILDER-lag for rt's void `__c_call`, tracked with the
other BUILDER-lag skips; they clear at the next BUILDER bump.)

## Cast/shift const-fold class — ✅ DONE & LANDED (moved to [claude-todo-done.md](claude-todo-done.md)); open residuals below (2026-06-17)

The cast-hidden negative-shift-count → silent-0 class (and the cast-semantics decision it surfaced) is fully closed and landed across `c9cce5ef`..`77d7cc38` — full detail in the done file. Remaining OPEN residuals:

- (Const-fold fit-check for arithmetic + non-negative `&`/`|`/`^`/`<<` is ✅ DONE & LANDED — `c699cd78` (h-arith) and `3f57dc3a` (h-bitwise); recorded in [claude-todo-done.md](claude-todo-done.md). Only the `>>` residual below remains.)
- **(h-shr / signedness-aware const-fold family) — ✅ DONE & LANDED** (single consts `05d08117`; h-cmp `0625521f`, h-inline-shift `865e2e79`, h-group `beffb741`, h-bni `cf549e2f`). Full detail in [claude-todo-done.md](claude-todo-done.md). Two adjacent residuals remain OPEN:
  - **(runtime-count negative shift)** 🔴 OPEN. `(0 - 16) >> n` (n a runtime var) still lowers to `lshr`: the negative untyped-int const left operand reaches the shift lowering, which picks ashr/lshr from the operand TYPE's `Signed` flag, not the value sign (an untyped `2^63` operand legitimately wants `lshr`, so the fix needs a value-based decision). xfail conformance `859_runtime_count_signed_shift`.
  - **(grouped signedness const followed by a bare repeat)** 🔴 OPEN (rare). `const ( Q uint64 = U/D; R )` loses Q's stamp to R's shared-node re-check → folds signed; the common iota-group case is correct.
- **iota-grouped `.bni` consts stay value-less** — `defineBniConst` doesn't fold an iota-group member, so a negative iota-grouped imported const used as a shift count would slip; needs iota substitution ported into `bni_scope`. Narrow.
- **`parseCharLiteral` (types) / `parseCharLit` (ir) duplicated** with no tie test — drift risk; factor into one shared decoder.
- **raw multi-byte char literal** (`'é'`) accepted as its first UTF-8 byte — front-end leniency (pre-existing).
- (The proper IR-gen transitive-`.bni`-const fix is tracked under the CRITICAL entry above. The forward-ref-const array-dim garbage bug and the named-array zero-init bug are ✅ DONE — see [claude-todo-done.md](claude-todo-done.md).)

## MINOR (latent) — same-final-segment generic INTERFACES collide (the iface analog of the now-fixed struct/func same-segment collisions) (2026-06-20) — 🔴 OPEN

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

## MINOR (checker) — duplicate same-short-name imports are accepted silently; `pkg.X` resolves first-wins (2026-06-20) — 🔴 OPEN

Importing two same-final-segment packages BOTH unaliased (`import "pkg/aa/gen"`
+ `import "pkg/bb/gen"`, both default short name `gen`) is accepted with NO
diagnostic, and `gen.X` silently resolves to the first-imported one (import
order decides).  Surfaced by the generic-struct collision investigation
(2026-06-20).  Should be a duplicate-import error (or require an alias), like
the same-alias-different-path facet already handled for explicit aliases.  The
realistic workaround (alias the imports) now works for generic structs after
`5ae791d2`.

## MINOR (parser/checker, pre-existing) — two generic-body limitations surfaced during the struct-collision work (2026-06-20) — 🔴 OPEN

Both reproduce on a SINGLE package and predate the struct-collision fix
(independent of it):
- A concrete-instantiation PARAMETER type written in a `.bni` body
  (`func Sum(x @Box[int]) int { ... }`) fails to PARSE (`expected ;, got {`).
  The generic-reader idiom (`func Sum[T any](x @Box[T]) T`) is the working form
  (it's what 853/874 use).
- A generic-body expression combining a type-param field / slice-index with
  arithmetic (`return x.items[0] + x.items[1]`, `return b.hi + b.p.y`) fails
  with `arithmetic op requires numeric operands` / `cannot assign void to int`
  — an unconstrained `[T any]` T isn't numeric, but the diagnostic/handling for
  a struct-field-of-T in an arithmetic position is the rough edge.

## MINOR — cross-mode interface dispatch: test-coverage gaps + LP64 assumption (2026-06-14) — 🟡 OPEN

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

## MAJOR — closure-shim cousins still use raw `ArgWords` for user words (latent funcval miscompile) — 🟡 OPEN

FOLLOW-UP to the now-resolved non-closure funcval-shim marshalling fix (full
diagnosis + Stage A/B + B0 Functions-table archived in claude-todo-done.md).
The non-closure shims were switched to `cc.EffectiveArgWords`, but the CLOSURE
shims were NOT:
- **(1) raw `ArgWords` for USER words** — every closure shim does `common.ArgWords(ut)`
  for user words instead of `cc.EffectiveArgWords`. For an indirect-large user
  arg (managed-slice = 4 words, iface = 2, `>16B` struct ≥ 3) this over-counts
  vs. the dispatch caller's single-pointer placement, mis-shifting `inRegBase` /
  outgoing regs. **CONFIRMED wrong-code (not just latent)** by the 2026-06-21
  adversarial review of the 706 work: e.g. a closure `@func(s @[]int) Big24`
  capturing a `float64` passes the budget gate (`nUserWords = ArgWords(@[]int) =
  4`, not `> 4`), spills 4 incoming GP words when the dispatcher set only 1 (the
  slice pointer), and reloads garbage — silent miscompile / memory corruption.
  The defect spans ALL closure-shim families: the GP-only fast/spill
  (`x64_closure_shim.bn` / `aarch64_closure_shim.bn`), the GP-only aggregate
  (`*_closure_shim_aggregate.bn`, `loadClosureAggCallArgs_*`), AND the float
  shims' shared marshaller `loadClosureFloatCallArgs_{x64,AA64}`
  (`*_closure_shim_float.bn`) — the user-arg loop has no `isIndirectLargeCap`
  branch (the CAPTURE loop does). The 706 float-aggregate work (landed binate
  `0c54d69d`) deliberately landed CONSISTENT with its siblings (user decision
  2026-06-21: "land 706 as-is"), so the fix is this one cross-cutting sweep, not
  a per-path patch. Fix: switch the user-word counts to `cc.EffectiveArgWords`
  and add an `isIndirectLargeCap` branch to each user-arg loop (forward the one
  spilled pointer word into one GP reg), mirroring the capture branch and the
  non-closure `emitShimArgMarshal_*` (which already get this right).
- **(2) no float-scalar user-arg GP→FP marshalling** — ✅ RESOLVED by the
  closure-float shims (claude-todo #121: 569/705/706, binate `085065d9` …
  `0c54d69d`). `emitClosureShimFloat*` / `emitClosureShimFloatAggregate*` now
  marshal float-scalar captures/params GP→XMM/D. Only (1) remains.

Reference to mirror: the landed non-closure spill in
`pkg/binate/native/{x64,aarch64}/*_funcvalue_spill.bn` (uses
`cc.EffectiveArgWords`). No closure-spill/wide-closure conformance test exists
yet. B0's force-emit only emits NON-closure triples, so this doesn't block B0 —
ready-to-pick follow-up. (User owns.)

### Array composite-literal defects (indexed silent-miscompile; over-count OOB write) — spec Ch.13 (2026-06-12) — 🔴 OPEN
Found + verified firsthand authoring spec Ch.13 (read the type-check +
IR-gen; not run, but the code path is conclusive). Two MAJOR array-literal
defects; the type checker `checkArrayLit` (`check_expr_composite.bn:84-91`)
iterates elements positionally, never reading `el.Key`, and never checks
element count against `ArrayLen`; IR-gen `gen_composite.bn:149-152` stores
element `i` at index `i`.
- **Indexed array literals silently MISCOMPILE** (`expr.composite.array.indexed`,
  MAJOR wrong-code). `[5]int{1: 10, 3: 30}` is DECIDED (claude-notes.md:801) to
  mean `{0,10,0,30,0}`, but the keys are ignored and values stored positionally
  → `{10,30,0,0,0}`. Silent wrong values, no diagnostic, no test. Fix: in
  checkArrayLit/genArrayLit, when an element has a Key, fold it to a const index
  and place the value there (validate `index < N`, detect duplicates), zero-fill
  gaps — OR reject indexed-array syntax outright (user's call).
- **Array over-count not rejected → OUT-OF-BOUNDS stack writes** — ✅ RESOLVED 2026-06-12 (binate `910e08cb`; over-count reject only — indexed-literal + `[...]T` sub-items below remain OPEN). `checkArrayLit` now rejects `len(elems) > ArrayLen` with "too many elements in array literal" before IR-gen. conformance/740_array_overcount_rejected; full unit 45/0 + conformance 1407/0 native + 1389/0 VM (no previously-valid code rejected).
  - **Sibling found in self-review + fixed (binate `e81bfbbe`)**: NAMED array/slice composite literals (`type Row [3]int; Row{...}`) bypassed element validation ENTIRELY — `checkCompositeLit` routed a `TYP_NAMED` underlying to its element checker only for STRUCT underlyings, so named-array over-count (→ OOB) AND wrong-element-type (→ miscompile) were both silently accepted (exposed when named composite literals were enabled, `2eeb71c1`, which fixed IR-gen but not the checker). Fix: peel alias/const/named (`peelNamedBounded`) to the composite shape once up front so all element-check branches handle named + unnamed uniformly. conformance/742_named_array_lit_checked; 723/728 still green; full unit 45/0 + conformance 1408/0 native + 1390/0 VM.
  (`expr.composite.array.overcount`, MAJOR, latent memory-unsafety). `[3]int{1,2,3,4,5}`
  is accepted; `gen_composite.bn:149-152` emits stores at indices 0..4 into a
  3-element alloca → 2 out-of-bounds stack writes. Should be "too many elements
  in array literal". No test. (Struct over-count — the benign analogue, extra
  positional values silently discarded — ✅ RESOLVED 2026-06-12 binate
  `e185c9c4`: `checkStructLit` rejects `len(Elems) > len(Fields)` for a
  positional literal, "too many values in struct literal"; negative test
  `743_struct_overcount_rejected`. Applies to named structs too via the
  `peelNamedBounded` routing.)
- **Inferred-length `[...]T{...}` NOT IMPLEMENTED** (`expr.composite.array.inferred-len`).
  DECIDED (claude-notes.md:798) but the checker rejects it ("array length must be
  a constant integer"). Either wire it (substitute `len(Elems)` for the `...`
  marker) or mark deferred.
- **(minor) Positional struct-literal elements are not assignability-checked**
  (`check_expr_composite.bn:73-79` checks keyed but not positional values).
All referenced from `13-expressions.md`.

### `_Package()`: bytecode VM works only for the 4 builtins (Gap 2; unqualified form ✅ FIXED; builtin auto-injection ✅ LANDED) — 🔴 OPEN (user-package bytecode `_Package` remains)

> **Update 2026-06-12** — two related pieces landed on main:
> - **VM injection Part A** (binate `a8ba52f2`): `RegisterStandardExterns` now
>   auto-enumerates `rt._Package().Functions` (+ empty reflect) via
>   `registerPackageFunctions`, replacing the hand-maintained rt block. bootstrap
>   stays hand-bound (deprecation path + extern-heavy; table skips `IsExtern`);
>   the 3 `_Package` accessors + 2 trampolines stay hand-bound. See
>   `plan-vm-package-injection.md` Part A.
> - **`_Package` self-listing** (binate `53ea3875`): every package self-lists its
>   own `_Package` accessor as the last `Functions` entry (closing the reflection
>   gap), and `--pkg` compilation force-loads reflect (`ensureReflectLoaded`) so
>   it holds even for packages that don't import reflect — i.e. `cmd/bnc` now
>   force-loads reflect on ALL paths (main/test already did; `compileSinglePkg`
>   now too). fv stashed on `ir.Module.PackageAccessorSig` → byte-identical
>   LLVM/native entry (Name `<pkg>._Package`, ResultSize 8, ParamSlots 0, Sig
>   `()(@pkg/builtins/reflect.Package)`). Validated: builder-comp 1395/0,
>   builder-comp-int 1360/0, reflect byte-identical across LLVM/native-aa64/native-x64.
>   Follow-ups (binate `2988cda4`, `6d052181`): arm32 (ILP32) per-mode `expected`
>   overrides for 725/727 — the self-entry's ResultSize is `ptrSize()` (4 on
>   ILP32, 8 on LP64), breaking target-independence (⚠️ NOT verified locally —
>   no qemu; needs arm32 CI confirmation); plus native unit tests
>   (`TestEmitPackageDescriptorSelfListsPackage{AA64,X64}`) for the self-listing.
> - **Still open (the core Gap 2 below)**: user/stdlib packages compiled to
>   BYTECODE still have no `_Package` body → Part B (§2a of the VM-injection plan).
>   The `cmd/bni`-doesn't-force-load-reflect asymmetry below is still accurate
>   (the fix above is `cmd/bnc`-side only).

The compiler synthesizes a `_Package() @reflect.Package` accessor per package
returning the package's immortal static-managed descriptor (Phase B,
notes-package-introspection.md).  `codegen/emit_pkg_descriptor.bn` (+
`native/{x64,aarch64}/_pkg_descriptor.bn`) emit it as a NATIVE function; the
checker synthesizes its signature in BOTH the qualified-access arm
(`check_expr_access.bn`) and the unqualified `checkIdent` arm
(`check_expr.bn`).  Two gaps, surfaced 2026-06-11 by writing
`conformance/708_reflect_package_all_kinds` (user-requested "every package has a
`_Package`" coverage):

- **Gap 1 — no unqualified form (checker) — ✅ FIXED (binate `1164ef04`).** An
  UNQUALIFIED `_Package()` (the current package's own accessor) was `undefined:
  _Package`; now it type-checks and lowers like a normal exported function,
  callable unqualified within AND qualified from importers.  `checkIdent`
  (`check_expr.bn`) synthesizes the `() @reflect.Package` type; IR-gen's
  `registerCurrentModulePackageAccessor` (`gen_import.bn`) registers the current
  module's `_Package` FuncSig so the bare-ident call path lowers it to the local
  symbol `emit_pkg_descriptor.bn` emits.  Compiled modes only — VM still hits
  Gap 2.  Pinned by `conformance/709_reflect_package_unqualified` (compiled PASS,
  3 VM modes xfailed for Gap 2).
- **Gap 2 — VM works only for builtins (MAJOR VM-backend project; DEFERRED).**
  `_Package()` is emitted only as a native function; the bytecode VM reaches
  `_Package` ONLY for the four builtin packages, via the HARDCODED externs in
  `vm/extern_register_std.bn`.  A user/stdlib package compiled to bytecode has no
  native `_Package` symbol → `vm: extern not found: <pkg>._Package`.  The extern
  approach CANNOT work for bytecode-compiled packages.  Fix: emit `_Package()` +
  its static-managed descriptor as BYTECODE per package (the VM equivalent of
  `emit_pkg_descriptor`) so the VM runs it directly, dropping the
  hardcoded-builtin extern table.  Major VM-backend work — the user explicitly
  deferred this.  (Subsumes a sibling asymmetry: `cmd/bni` does not force-load
  reflect the way `cmd/bnc` does — `ensureReflectLoaded` is cmd/bnc-only — so
  reflect-dependent type-checking under the VM needs an explicit reflect import;
  709 imports reflect for exactly this reason.  When the VM emits `_Package`, it
  will force-load reflect too.)
- **Test**: `708_reflect_package_all_kinds` pins `<pkg>._Package().Name` == import
  path for a user package + all four builtins + a stdlib package.  PASSES on the
  3 compiled modes; **xfailed on the 3 VM modes** (`-int`/`-int-int`/`-comp-int`)
  for Gap 2 (int-int also hits the pre-existing multi-package double-VM failure).

## MAJOR

### Named func-value type (`type Fn @func(...)`) — func-LITERAL construction still rejected — literal-half follow-up 🟡 OPEN
- **Symptom**: `type Fn @func(int) int; var f Fn = func(x int) int {...}` is rejected (a bare `@func` literal isn't `Identical`/assignable to the nominal `Fn`). The func-REFERENCE half (`var f Fn = dbl`) and value-rejection (`var f Fn = g`) already work — see archived diagnosis.
- **Test**: `conformance/regressions/named-func-value-construct-literal` (xfailed all 11 modes).
- **Fix (3 sites, none peel TYP_NAMED yet)**: `checkFuncLit` (`check_func_lit.bn:83`) must RETURN the named type when hinted by one (gates on `ExpectedFVType.Kind == TYP_FUNC_VALUE` only); `checkExprWithFVHint` (`check_expr.bn:32`) must peel TYP_NAMED before installing the FV hint (currently ignores non-FUNC_VALUE/MANAGED_FUNC_VALUE hints, so a `Fn`=TYP_NAMED hint is dropped); `isManagedFuncValueLit` (`gen_func_lit.bn:188-194`) must peel TYP_NAMED (keys on `TYP_MANAGED_FUNC_VALUE`).
- **Memory-sensitive**: a func literal can CAPTURE, so the stack-vs-heap-alloc + refcount classification must be right — validate under guard-malloc.
- **Severity**: MAJOR (spurious compile-time rejection, fail-safe, no miscompile). Workaround: anonymous `@func(...)` spelling.
- (Full resolved REF-half diagnosis — design decision, root cause, IR-gen `gen_typedecl.bn` fix — archived in claude-todo-done.md.)

## CR-2 review — carried-forward open residues (2026-06-08/09)

The CR-2 Plan-1 / Round-2 / follow-up-batch adversarial-review records (resolved + refuted
findings) are archived in [claude-todo-done.md](claude-todo-done.md); each resolved finding
also has its own dedicated RESOLVED entry there, and the records preserve the
REFUTED-do-not-re-chase verdicts. These are the still-open residues kept here for tracking:

### Alias receivers unsupported for METHOD VALUES and IMPL declarations — filed known limitations, user-deferred 2026-06-09 — 🟡 PARKED
- **Method values** (`type AB = @Box; var mv = ab.getV` → "undefined: getV"): the method-value path in `check_expr_access.bn` calls `ReceiverBaseNamed()` on the un-alias-peeled `origXt`. A DIRECT method value (`p.getV`) works; only the alias receiver is broken.
- **Impl declarations** (`type AB = *Box; impl AB : Getter` → "impl receiver must be (a wrapper around) a named type"): `checkImplSatisfaction` (`check_impl.bn`) calls `ReceiverBaseNamed()` on the possibly-`TYP_ALIAS` `recv`.
- **Why parked**: the type-only fix (peel the alias) makes both type-check, but the method-value closure layout (`gen_method_value.bn`) and the impl/vtable dispatch don't peel the alias → runtime **SIGSEGV**. A proper fix needs the IR-gen companion (peel the alias in closure-capture / vtable dispatch). Type fixes were prototyped + REVERTED. Per the user (2026-06-09): FILE as a known limitation, do NOT pursue the IR-gen work now. Niche (alias receiver × method-value / impl).

### X3-highbit — signed sign-bit const-fold checker-vs-IR divergence — DIRECTION CONTESTED (semantics-owned) — 🟡 NEEDS DECISION
- `1<<iota` now folds in the checker, so a flag member hitting the SIGN bit of a signed target (`1<<63` → `int` on 64-bit; `1<<31` on 32-bit) computes positive 2^(W-1), which `FitsSigned(W)` rejects — while IR's `evalConstExpr` wraps to the valid two's-complement `INT_MIN`. A real checker-vs-IR divergence.
- **Resolution is a spec call** (`claude-notes.md` §const: const values are abstract and must fit the target range → the reject may be CORRECT; the canonical idiom uses an UNSIGNED target, unaffected). Do NOT change semantics unilaterally. Companion to the bare-const-group-member inherited-type fix (`b9d6d807`) — decided separately.

### CR-2 review coverage gaps (low priority — add tests) — 🟡 OPEN
- **R2-D7**: no readonly/alias-wrapped named-int or named-float-minus test.
- **R2-D5**: the method-value/alias matrix covers only `type AB = @Box` (not alias-over-readonly / value-receiver alias).
- **R2-D4**: only the managed `readonly @Iface` construct is un-xfailed (no `readonly *Iface`, no return/arg-pass position).
- **A1**: no float-scalar / named-sub-word / box-in-loop `box` test.
- **CR-2 Plan-1 coverage-only**: 659 omits raw-pointer-index compound-shift (`p[i] <<=`) and signed `>>=` overshift on non-IDENT lvalues; the genShortVar nameless `multiReturnFieldTypes` fallback has no IR-gen unit test / no managed-component func-value `:=` cell; Defect-2b raw-pointer & value-receiver reject rows have no conformance/unit coverage.

## CRITICAL

### abi-matrix multi-return-through-dispatch cells lack a managed-component type — 🟡 OPEN
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

### Generic struct/interface instantiation skips constraint satisfaction — spec Ch.12 (2026-06-12) — 🔴 OPEN
Found authoring spec Ch.12 (verified via toolchain probes through
builder-comp). MAJOR (the spec implies it's enforced; it isn't) but it
doesn't miscompile. (The sibling "generic methods accepted at
declaration" defect is ✅ FIXED — rejected at collection time, binate
`a7e0beb2`; see claude-todo-done.md.)
- **Constraint satisfaction unchecked for generic struct/interface instantiation**
  (`gen.satisfy.struct-iface-unchecked`). `typeSatisfiesConstraint`/
  `reportConstraintMiss` are called ONLY from `instantiateGenericFunc`
  (`check_generic.bn:259-264`); `buildInstantiatedStruct` (:196-218) and
  `buildInstantiatedInterface` (:115-138) install the type-param scope but make
  NO satisfaction call. So `type Box[T lang.Orderable] struct{val T}`
  instantiated as `Box[NoOrder]` (no `impl NoOrder : Orderable`) compiles clean.
  Generic-FUNCTION constraint checking works correctly.
- **Pinned (2026-06-20):** `conformance/spec/12-generics/034_err_satisfy_struct_unchecked_xfail`
  (xfail.all) regresses this — flip it to a normal reject when the satisfaction
  check is added to `buildInstantiatedStruct`/`buildInstantiatedInterface`. The
  green generic-FUNCTION case is `033_err_satisfy_func_no_impl`.

### Value-receiver "always readonly" not enforced — spec Ch.10 (2026-06-12)
MINOR (design-intent vs impl; no correctness bug — by-value copy makes any
mutation harmless). `claude-notes.md:359` says a value receiver `(r T)` is
"always readonly". The checker does NOT enforce it: `receiverShape`
(`check_method.bn:251-285`) classifies a plain `(r T)` as kind 0 with
`isObjectConst=false`, and no pass rejects `r.field = ...` in the body — the
mutation just modifies the discarded copy. Decide: enforce read-only on value
receivers (a checker addition + a diagnostic), or downgrade the design note to
"the receiver is a copy; mutations are local" (the implemented semantics, which
the spec `func.method.value-recv` currently describes). Referenced from
`10-functions-methods-function-values.md`.

### Layout follow-ups surfaced authoring spec Ch.7.13 (Type Layout) — 2026-06-12
Both referenced from the spec (`07b-type-layout.md`).
- **`type.layout.funcval-order-hardening`** (hardening). The function-value
  field order `{vtable, data}` and the interface-value order `{data, vtable}`
  (the deliberate, verified ABI asymmetry) are encoded as fixed/magic indices
  in codegen + IR (`emit_instr.bn`, `emit_funcvals.bn`, `emit_iface_call.bn`,
  `ir_ops_flow.bn`) rather than as shared named-offset helpers in
  `pkg/binate/types` (unlike `SliceDataOffset`/`MSliceBackingOffset`/
  `ManagedRefcountOffset`, which ARE shared helpers). The VM and codegen agree
  by convention, not a single shared definition — a divergence risk for the
  keystone cross-mode contract. Harden the func/iface field orders into shared
  named-offset constants in `pkg/binate/types`.
- **`type.layout.byte-order`** (DECIDED 2026-06-17; impl follow-up open). Byte
  order is **implementation-defined**: fixed and documented per target, identical
  across modes (observable via `bit_cast` and the representation builtins). Spec
  ratified — §7.13.12 `type.layout.byte-order`, §21.4
  `behavior.impl-defined.endianness` (docs `9a0e2b9`); claude-notes recorded.
  The current implementation is **little-endian only**, and `TargetInfo`
  (`types.bni:400-405`) carries no endianness field. **Impl follow-up (not
  done):** add a `TargetInfo` endianness field + big-endian support, the path to
  big-endian/cross-endian targets.

### Type-system issues surfaced while authoring spec Ch.7 (Types) — 2026-06-12
Found writing the docs spec's Types chapter (grounding + adversarial
verification against pkg/binate/types). The spec (`07-types.md`)
documents these as open items.
- **Named func-value LITERAL construction unimplemented** (gap). A func
  *reference* constructs a named `@func` type fine, but a func *literal*
  into a named func-value type is rejected in ALL modes
  (`conformance/regressions/named-func-value-construct-literal` xfailed
  everywhere; checkFuncLit must return the named type when hinted and peel
  TYP_NAMED at isManagedFuncValueLit). Value-rejection and reference
  construction both work.

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
- **`bit_cast` to a sub-word type isn't narrowed in the VM AND the native
  backends — 🔴 OPEN (new facet of `aa64-subword`).** `bit_cast(uint8, <int8 -1>)`
  used directly (no intervening typed store) should be `255`, but stays
  sign-extended on the bytecode VM (all 3 `-int` modes) and on native_aa64 (the
  LLVM compiler narrows correctly; a `var r uint8 = bit_cast(...)` store also
  narrows). This is the **`bit_cast` facet** of the sub-word-narrowing gap
  (claude-todo `aa64-subword`, line ~526); the existing `matrix/scalar-diff`
  differential harness covers `cast` (now green on the VM) but **not** `bit_cast`,
  so it's uncaught. Right home: add a `bit_cast`-to-sub-word row to
  `matrix/scalar-diff` (it already does per-mode xfails for this class) rather
  than a spec test needing ~6 per-mode markers. The conv.bit-cast *rule* itself
  is satisfied (covered by `spec/08-conversions/010_bit_cast`).
- **MAJOR (type-checker / `conv.no-implicit-numeric` strictness) — distinct
  same-width integer types implicitly inter-convert (`int ↔ int64`, `uint ↔
  uint64`, `int ↔ int32` on 32-bit, …) — 🔴 OPEN; CONFIRMED a bug by the user
  (2026-06-19).** `var y int64 = x` (and the reverse) is accepted with `x int`,
  contradicting §8.2 (which lists "int ↔ int64" as requiring a `cast`; Go
  semantics — distinct types). `int → uint` (signedness) and `int → float64`
  (kind) reject correctly.
  - **Root cause:** `Type.Identical`'s integer arm (`pkg/binate/types/
    types_query.bn:376`) returns `a.Width == b.Width && a.Signed == b.Signed`,
    ignoring the type **name** — so `int` (width 64, signed) tests Identical to
    `int64` on a 64-bit target, and `AssignableTo` accepts it via the case-1
    (`Identical`) path. The comment just above (line 375) says "match by kind
    **and name**" — i.e. the code diverged from the intended name-match.
  - **Not a miscompile:** the conflation only fires for SAME-width types, which
    are bit-identical, so `int↔int64` is a runtime no-op. This is over-
    permissiveness (accepts code §8.2 rejects), not wrong-code or a layout/
    generics-cache hazard (different widths already test non-Identical).
  - **Fix shape (decide):** (a) make the INT/FLOAT `Identical` arm compare
    predeclared scalars **by name** so `int ≠ int64` at any width; or (b) keep
    `Identical` (layout) loose but add a name-aware gate in `AssignableTo`'s
    case-1 so distinct-named scalars need a `cast`. Either way the **blast
    radius is broad** — every implicit `int↔int64` in the tree (incl. the
    compiler's own source) would start needing a `cast`; a sweep + fixups is
    part of the fix.
  - **Test (deferred to the fix):** a unit test on `Identical`/`AssignableTo`
    (`int` not assignable to `int64`) is the clean pin (single host-side xfail);
    a conformance `.error` test would need per-mode xfails on every 64-bit mode
    (target-dependent), so it waits for the fix. The Ch.8 spec test uses `int →
    uint` for the no-implicit-numeric reject in the meantime.
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
- **`expr.composite.array.inferred-len` — 🔴 OPEN (genuine gap).** `[...]T{…}`
  is rejected at parse ("expected expression"), though now declared. Covered by
  `spec/13-expressions/041` (.xfail.all). Fix: infer the length from the
  element count.
- **(minor) `expr.composite.struct` bad-key diagnostic.** A keyed struct
  literal whose key names no field reports the generic `undefined: <key>` (the
  key is resolved as an identifier) rather than a field-specific "no field
  <key> in <T>". NOT a correctness bug — a key that shadows an in-scope
  variable still errors (not silently accepted). Covered by `spec/13-
  expressions/027_err_composite_struct_badkey`. Fix: emit a field-not-found
  diagnostic naming the struct + key in the keyed-literal checker.
- **(note, non-defect) `expr.compare.relational` chain diagnostic reach.**
  `a < b < c` is correctly rejected in every context, but the dedicated
  "comparison operators do not chain" message fires only for the
  identifier-leading for-clause Pratt path (`parse_for.bn:199`); `if`/`var`/
  literal-leading contexts reject via generic parse errors. Conformant
  (rejection holds) — a diagnostic-consistency nicety only.

### Untyped `const` coercion: implementation diverges from a DECIDED note — surfaced authoring spec Ch.6 (2026-06-11)
Needs a decision (MINOR — no miscompile; a type-system permissiveness
question).
- **The note (`claude-notes.md` "Type conversions & literals — DECIDED",
  ~line 444)**: untyped-literal coercion "does NOT extend to named
  constants — only literals." (A deliberate divergence from Go.)
- **The implementation does the opposite.** An untyped `const X = <expr>`
  (no explicit type) carries `TYP_UNTYPED_INT` (with `HasLitVal`) and
  **coerces / narrows at each use, exactly like a literal**:
  `check_const.bn:91-102` (no-`TypeRef` branch defines the name with the
  untyped `valType`), `check_expr.bn:185` (`checkIdent` returns it),
  fit-checked at the use site like a literal. Tests confirm:
  `check_const_test.bn:160-167` (`const A = 1+2` → assignable to `int`),
  `:210-217` (`const A = 200+100` → rejected against `uint8` because 300
  doesn't fit — pure literal-coercion behavior), and
  `check_expr_constfold_test.bn:181-204` whose comment says "the bare
  members stay untyped and **narrow freely at the use site**." Only a
  `const X <type> = …` (explicit type) gets a definite, non-coercing type.
- **Decision**: either (a) enforce the note — give an untyped `const` name
  a definite default type that does not coerce (the Go-divergent design),
  or (b) accept the implemented Go-like behavior and update
  `claude-notes.md:444`. The spec (docs `06-constants.md`,
  `const.untyped.coercion`) currently describes the **implemented**
  behavior and flags this as an open item.

### Lower the file-length `.bni` cap toward 1000/1200 — 🟡 OPEN
- **Residual** of the (now-archived) "Extend hygiene checks to scan `ifaces/`+`impls/`" work. The `.bni` file-length cap is currently 1500/1800 (warn/error); consider lowering toward 1000/1200.
- **Blocker**: `pkg/binate/ir.bni` (~1183 lines) exceeds the proposed lower cap and would need refactoring (split into sub-interfaces) first. A live `TODO` in `scripts/hygiene/file-length.sh` tracks this.
- (Full resolved diagnosis of the ifaces/impls hygiene-scan extension archived in claude-todo-done.md.)

## MAJOR

### MAJOR PROJECT — unify module-level static data into one IR representation (`ir.DataGlobal`) + one per-backend emitter — FILED 2026-06-10 (🟢 PHASE 1 + INC 2 + INC 3a + INC 3b LANDED, phased migration in progress)
- **🟢 PHASE 1 LANDED (binate `1ae1b52b`, 2026-06-21)**: `ir.DataGlobal` + `emitDataGlobal` (both backends) + `ir.BuildPackageDescriptor`; the `_Package` descriptor NODE+name now flow through `DataGlobal` (the hand-rolled node layout is gone from both backends). Also unified the native node/name to weak_odr/local, **closing the strong-symbol hardening item below**. Living plan + remaining increments in [`plan-codegen-data-global.md`](plan-codegen-data-global.md). The bullets below are the original design notes (still accurate for the unlanded phases).
- **🟢 INC 2 LANDED (binate `b2667902`, 2026-06-22)**: the FunctionInfo/GlobalInfo/VtableInfo info-node tables + their name/sig rodata + the `_pkg_funcs/globals/vtables` backing arrays now flow through one shared `ir.BuildPackageDescriptors` (per-kind builders in `ir/data_pkg_{funcs,globals,vtables,descriptor}.bn`); both backends gather row metadata + lower via `emitDataGlobal`. Deleted the per-kind byte emitters (`emit_pkg_{functions,globals,vtables}.bn`, `common_pkg_{functions,globals,vtables}.bn`), `EmitPackageDescriptorData`, and `emit_static_managed.bn`'s `emitStaticManagedGlobal` — the interim native emitter (`f7d116f3`) is fully retired (net −529 lines). Info-node PAYLOAD-pointer addend uses `2*IntSize` (header size), correct on ILP32. Native Global/Vtable nodes + arrays unified to weak/local (matching LLVM). Reflect output byte-identical; full builder-comp 2219/0 + native-aa64 2212/0, reflect 525/532/708/709/725/727/726 green both backends, adversarial review clean. Rebased onto concurrent `043318b1` (725/727 golden refresh) + `d47d1a2e` (`ResultSize`→`RetbufSize` rename, adopted throughout).
- **🟢 INC 3a LANDED (binate `30aca2d7`, 2026-06-22)**: func-value vtables + handles. `@__vt` (`{ dtor, call }`) + `@__handle` (`{ vtable, data=null }`) now flow through one shared `ir.BuildFuncValue` (`ir/data_funcval.bn`); both backends lower via `emitDataGlobal`. The LLVM closure-dtor triple (`emitClosureDtorTriple`) also routes through it, so NO hand-rolled func-value vt/handle emitter remains. Deleted codegen emitFuncValueVtable/Dtor/Handle + native emitFuncValueVtableDtorSlot{,_x64} + dead emitQuadLabelFV. LLVM globals became anonymous `{ ptr, ptr }` (was named %BnVtable/%BnFuncValue); the typed-pointer refs elsewhere auto-upgrade to `ptr` under opaque pointers (clang-verified). Full builder-comp 2300/0 + native-aa64 2296/0, func-value/closure/handle conformance both backends, adversarial review clean.
- **🟢 INC 3b LANDED (binate `787ed644`, 2026-06-22)**: impl vtables (`@__ivt.*` raw + `@__ivtshim.*` shim). The variable-length, recursively-computed layout (per iface level: dtor HANDLE slot, then each parent's FULL sub-vtable INLINE so `*Child→*Parent` upcast is a fixed offset, then own methods; raw uses fn symbols, shim uses `@__handle.<m>`) now flows through one shared `ir.BuildImplVtable` (`ir/data_impl_vtable.bn`). Each backend's gather only collects the ordered slot symbols (`collectImplVtableSlots`/`…_x64`/`…Native`, recursive) + keeps SetGlobal bookkeeping on referenced method/dtor-handle symbols; only the byte layout moved. LLVM globals became anonymous `{ ptr, ptr, … }` (was `[N x i8*]`); dtor slot became `ptr @__handle…` (was `i8* bitcast (%BnFuncValue* … to i8*)`). Native impl/shim vtables unified from strong `SetGlobal` to **weak** (`DG_WEAK`) to match LLVM (user-approved). Deleted codegen emitImplVtable/emitImplShimVtable/emitImplVtableLayout/…Slot + dead writeFuncPtrType/writeFuncResultLLVM; native emitOneImpl{,Shim}Vtable bodies + dead emitQuad{Label,Zero}{,Iface} (net −62 lines). Full builder-comp 2359/0 + native-aa64 2356/0, gen1+gen2 green, units ir/codegen/native 7/0, hygiene 15/15, adversarial review clean. **Next: strings (preserve `FinalizeStrings` dedup) → globals (front-end-coupled, last).**
- **The smell**: module-level constant data is currently modeled and emitted **per kind**, each with its own IR rep + its own LLVM emitter + its own native emitter: `mod.Strings` (string consts), `mod.Globals` (`var` storage), `mod.Impls` (impl vtables), func-value vtables/handles (derived from `mod.Funcs`), and the package descriptor `_Package` (worst case: LLVM-text-only, no IR rep, no native emitter). That's ~5 kinds × 2 backends ≈ 10 emitters for ONE concept — *a named, module-level constant blob the backend lays into a data section.* The proliferation is what let `_Package` ship with only its LLVM half written (see the native-`_Package` link bug below) — the LLVM-only-divergence bug class is structural to this design.
- **The unification**: one IR concept `ir.DataGlobal { Name; Linkage (private|weak_odr|linkonce_odr|external); Align; Init }` where `Init` is a sequence of terms: `bytes` | `int(width)` | **`symref(symbol, +offset)`** (pointer to another symbol). The `symref` term is the one expressive thing today's `ir.Global.Init` (a single int-only `@Instr`) lacks, and it's what every interesting blob needs. Then ONE `emitDataGlobal` per backend (lay bytes + apply relocations + linkage/align) replaces all the per-kind emitters. Mappings: string → `bytes`; var → `int/zero`; `_Package` → `int(RC),int(0),symref(_pkgname),int(len)` (the static-managed node, no special primitive); impl/func-value vtable → `[symref(dtor),symref(m0),…]`. Both backends walk one path → LLVM-only divergence becomes impossible. Consonant with `ir-backend-guidelines.md` ("string constant collection belongs in a shared layer") — this is the shared *static-data manifest* backends lower.
- **What stays / what resists (design must handle)**: (1) func-value `__shim`s are CODE → stay in `mod.Funcs`; only the symref *table* is data. (2) impl vtables carry **per-arch layout** + `weak_odr`/`linkonce` linkage + alignment — the model must carry linkage/align and backends keep arch layout knowledge. (3) **string interning/dedup** (`FinalizeStrings`) is a real optimization to preserve, not regress to one-global-per-occurrence. (4) `mod.Globals` carries **front-end semantics** (extern vars, qualified-name resolution, `IsExtern` external-decl emission) — the front-end layer maps onto `DataGlobal`, isn't replaced by it.
- **Payoff**: kills the LLVM-only-divergence bug class structurally; ~10 emitters → ~2; new static-data needs get both backends for free. **Cost/risk**: real IR + dual-backend refactor of *currently-working* code; non-trivial regression surface; per-kind quirks above. This is a project, not a bug fix — needs a `plan-*.md` (design the `Init`/relocation model + linkage/align + interning; phased migration).
- **Suggested migration order**: introduce `ir.DataGlobal` + one `emitDataGlobal` per backend → migrate `_Package` onto it FIRST (the proving case; also retires the interim native emitter below) → then impl + func-value vtables → then strings → then globals (front-end-coupled, last). Each step keeps all backends green.
- **Interim DONE**: the short-term native `emitPackageDescriptor` is LANDED (binate `f7d116f3`) — `common.EmitPackageDescriptorData` (shared static-managed-node layout) + a per-arch accessor. Explicitly throwaway: the `_Package` migration step of this project deletes it (and `codegen/emit_pkg_descriptor.bn`) once the descriptor is an `ir.DataGlobal`.
- **Low-priority hardening surfaced by the interim's adversarial review (not reachable today)**: the native interim `SetGlobal`s `_pkg_info` + `_pkgname` as STRONG symbols, vs LLVM's `weak_odr` (`_pkg_info`) / `private` (`_pkgname`). NOT a current bug — in `--backend native` only `main` is native and all deps go via LLVM (disjoint package names), so the same package's strong native `_pkg_info` never lands in two objects; conformance/532 + the native vm/repl/bni unit links are clean. It WOULD bite a future native-library-packaging path (a precompiled native `.o` for a package linked beside a from-source native recompile of it → duplicate strong symbol where `weak_odr` dedupes). Cheap fix when that lands (or sooner): `a.SetWeak` on `_pkg_info` (matches `weak_odr`); `_pkgname` only needs same-object visibility (sole consumer is the same-object `Name.data` fixup) so it can be local/weak. The `ir.DataGlobal` unification should carry a linkage field so this is expressed once. (`_pkg_info` must stay a defined symbol the accessor's cross-section reloc can target — the native Adrp/Lea fixup resolves to it like `emitGlobalAddr` — so not an unnamed local.)

### Add a hygiene check enforcing package-tier dependency rules (`pkg-layout-spec.md`) — bundled tiers must not import non-bundled tiers — FILED 2026-06-10
- **What**: a `scripts/hygiene/` check that statically validates every package's import closure against the tier ordering in `pkg-layout-spec.md` ("Tiers"). A package must not import a *less-bundled* (higher-numbered) tier. Concretely — tier 0/0b/1/1x packages (always- or by-default-bundled: `pkg/builtins/*`, `pkg/std/*`, `pkg/stdx/*`) must NOT import a tier-2/3 package (project-pulled / not bundled: `pkg/binate/*` and any other `pkg/<org>/*`). Also enforce the tier-2 transitive-closure rule (`pkg-layout-spec.md` "Tiers": tier 2's dependency closure must itself be tier 2). Tier is derivable from the import-path prefix (`pkg/builtins/`→0/0b, `pkg/std/`→1, `pkg/stdx/`→1x, `pkg/binate/` & other `pkg/<org>/`→2); `pkg/bootstrap` is a bundled runtime primitive (treat as tier-0-equivalent). EXEMPT `*_test.bn` — tests aren't bundled (e.g. `lang_test.bn` legitimately imports `pkg/binate/buf`).
- **Why**: a bundled package whose dependency closure escapes the bundled tiers silently breaks the bundle — the dependency's source isn't shipped, so a consumer compiling against the bundle gets `package "<dep>" not found`. NOTHING currently catches this: it only manifests when a consumer compiles the offending package from a real bundle (`make-bundle.sh` output), which no CI / hygiene / conformance step does today.
- **Motivating bug (discovery 2026-06-10, release-prep for `bnc-0.0.8`)**: `pkg/builtins/lang` (tier 0, always bundled) imported `pkg/binate/buf` (tier 2) for two `buf.CopyStr("true"/"false")` calls in `bool.String()`. The bundle ships only `lib/pkg/bootstrap`, not `pkg/binate/buf`, so the tier-0 `Stringer` carve-out (`var s *lang.Stringer = &x; s.String()`) failed to compile from ANY bundle with `package "pkg/binate/buf" not found` — present since `bnc-0.0.7`, undetected because the carve-out smoke step (`release-process.md` step 5) had never actually been run against a real bundle. Fixed in binate `84818a77` (lang returns bare string literals; `[N]readonly char → @[]char` is a literal-init allocate+copy). This check would have caught it at the `import` line.
- **Scope note**: adding the check ≠ wiring it into `scripts/hygiene/run.sh` / CI — but a hygiene check belongs in the run.sh master, so do both when implementing. A first audit may surface other pre-existing violations to triage.
- **First manual sweep (Lane C, 2026-06-10) — CLEAN baseline**: swept every import (incl. aliased) in the bundled trees (`ifaces/{core,stdlib}`, `impls/{core,stdlib}`, `pkg/bootstrap`, `runtime/`). No non-test bundled package imports outside the bundled set. Two non-obvious cases the eventual check must handle: (1) `impls/core/baremetal/pkg/builtins/rt` imports `pkg/semihost`, which is NOT a violation — `pkg/semihost.bni` ships under `runtime/baremetal_arm32/` (a bundled runtime component) and resolves under the arm32-baremetal build's own `-I`/`-L`; the check should treat shipped `runtime/<target>/pkg/*` as bundled, or scope tier rules per build target. (2) all `pkg/builtins/testing` imports are in `*_test.bn` (already EXEMPT) and it has a bundled `.bni` with a harness-provided impl. So `lang → pkg/binate/buf` (binate `84818a77`) was the only true tier-0→tier-2 violation; the baseline is otherwise clean.

### `==` / `!=` (and relational) on aggregates: checker now rejects — no more invalid LLVM. DECIDED + LANDED at the checker (binate `60719e01`, coverage `78af9c23`); struct/array impl + generic path remain OPEN
- **What it was**: the comparison type-check rule only checked mutual assignability and returned bool, so `==`/`!=`/`<`/`>`/`<=`/`>=` were accepted on *any* same-typed operands. For aggregates (raw/managed slice, raw/managed func value, interface value, struct, array) codegen then emitted `icmp` on a multi-word value → invalid LLVM (`error: icmp requires integer operands`), hard package compile failure.
- **DECIDED (user, 2026-06-07)** and **LANDED** in `pkg/binate/types` (binate `60719e01`; coverage `78af9c23`):
  - **Equality (`==`/`!=`)**: scalars + pointers compare directly. **Slices, interface values, func values → permanently rejected** with a type-specific diagnostic (consistent with `slice == nil` / `iface == nil` already being disallowed footguns; the sanctioned tests are `len()` / `present()` / identity). **Structs and arrays → "not yet implemented"** (comparable in principle; the fieldwise/elementwise lowering is deferred — arrays in the same bucket as structs, per user). `nil` is judged by the other operand (`ptr == nil` OK; `iface == nil` / `func == nil` rejected).
  - **Relational (`<`/`>`/`<=`/`>=`)**: numeric operands only — ordering is undefined for pointers (claude-notes.md:898) and every aggregate (folds in the same invalid-IR bug for `<` etc.).
  - **Type parameters / Self**: deferred (no error at generic-definition time) in both paths — preserves prior generic behavior; NOT a unilateral generic-semantics change.
  - Validated: 21 targeted checker unit tests; full unit suite (40 pkgs) green; conformance (1094) green; adversarial-reviewed (no real defects introduced).
- **STILL OPEN — do not lose these**:
  1. **Struct/array equality implementation** — currently a clean "not yet implemented" checker error. When implemented: a recursive "comparable iff all fields/elements comparable" check (a struct with a slice/iface/func field → permanent reject; all-comparable struct → fieldwise compare); add a runtime equality conformance cell then.
  2. **Generic path NOT covered** — `==`/relational on a type parameter later INSTANTIATED with an aggregate is not caught: the body is checked once with `T` opaque (deferred), and instantiation does not re-check it (`check_generic.bn`), so it can reach IR-gen → the same invalid-IR class, via generics. PRE-EXISTING (before this change all aggregate `==` was permissive); this change does not worsen it. Needs instantiation-time re-checking OR a `comparable`-style constraint decision. Separate follow-up.
  3. **Sentinel detection (`err == io.EOF`)** — disallowing interface-value `==` means this is NOT the mechanism; needs `identical`/`same` + `errors.Is` (under discussion / see io.EOF TODO). Resolve before the first real `Reader` lands.

### Collapse `pkg/bootstrap` onto `#[build]` — 🟡 OPEN (next, per user 2026-06-19)
With BUILDER at `bnc-0.0.9` (both `bnc` and `bnlint` parse `#[build]`), `pkg/bootstrap` — whose
per-target variants are currently PATH-selected and which lives in cmd/bnc's BUILDER-compiled
tree — can be collapsed onto `#[build(...)]`-gated declarations, the same way `pkg/builtins/build`
was. See [`plan-impls-constraints-migration.md`](plan-impls-constraints-migration.md). (This was
the "bonus" of the build.bni-dedup workaround removal, now landed — binate `9c2ac789`, archived in
[claude-todo-done.md](claude-todo-done.md).)

### Remove the BUILDER-lag lint skips after a BUILDER bump — 🟡 OPEN (gated on BUILDER)
`scripts/hygiene/lint.sh` now lints the stdlib/runtime tier (`pkg/std/*`, `pkg/stdx/*`,
`pkg/builtins/*`, `pkg/bootstrap` — under `impls/`+`ifaces/`, landed `3f2fdf4a`), and that
extension surfaced two packages the BUILDER-bundled bnlint (`bnc-0.0.9`) can't typecheck because
they use a feature/fix newer than the bundle.  Both are in `LINT_SKIP` and clear at the next
BUILDER bump:
- **`pkg/builtins/rt`** + its importer chain (`pkg/binate/vm` → `pkg/binate/repl`, `cmd/bni`, whose
  bodies bnlint typechecks): rt's `Exit`/`RawFree` use the `"void"` `__c_call` spelling
  (`__c_call("free", "void", ptr)` → a result-less C call; conformance `866_c_call_void_return`),
  a parser feature newer than bnc-0.0.9.
- **`pkg/std/os`**: depends on the `.bni` free-function-vs-same-named-method fix (`796effc7`, the
  `os.Stat`/`File.Stat` case) which postdates bnc-0.0.9 — the same BUILDER-lag that makes
  `e2e/stat-values.sh` build gen1 from the tree.
Once `BUILDER_VERSION` is bumped past those, drop the rt+vm/repl/bni and os entries from
`LINT_SKIP` (restoring full lint coverage) — verify the bundled bnlint parses both directly first.

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

### Package descriptors (Phase B) — `_Package()` works in compiled + VM modes (builtins); general Functions-table still future
- **Status**: compiled-mode AND VM-mode `_Package()` landed (binate
  `feadde2c`, VM-mode for the builtin packages).  The general interop
  Functions-table (user packages, auto-enumeration) remains future work.
- **What works (compiled mode)**: every package emits an immortal
  static-managed `reflect.Package` descriptor node + a generated
  `_Package() @reflect.Package` accessor (codegen `emit_pkg_descriptor.bn`,
  via the static-managed emitter).  The type checker synthesizes the
  `_Package` signature at selector resolution (`check_expr_access.bn`
  `packageAccessorType`), IR-gen registers it as an imported extern so calls
  resolve + a `declare` emits (`gen_import.bn`), and `reflect` is force-loaded
  (`ensureReflectLoaded`).  Drives a real immortal node through the compiled
  RefInc/RefDec sentinel end-to-end (see [`plan-static-managed-sentinel.md`]).
- **What works (VM mode, binate `feadde2c`)**: the earlier "Functions-table
  is genuinely required" finding was too pessimistic.  `_Package` is already
  a real exported per-module symbol, and the IR/func-value path already
  mangles a qualified `pkg._Package` reference to call it — so the only
  blocker was the type checker rejecting `_func_handle(pkg._Package)` (it's
  compiler-synthesized, not a `SYM_FUNC` in scope).  Two small changes wired
  it: (1) `types/check_builtin.bn` accepts `pkg._Package` as a `_func_handle`
  argument by name; (2) `vm/extern_register_std.bn`
  `registerPackageDescriptorExterns` binds the builtin packages' `_Package`
  (rt, libc, bootstrap, reflect) as VM externs.  Interpreted `pkg._Package()`
  now dispatches through the func-value shim to the real accessor, and the
  returned `@reflect.Package` is RefDec-safe via the static-managed sentinel —
  exercising the sentinel end-to-end in interpreted mode too.
- **Coverage**: `conformance/532_reflect_package_accessor`
  (`rt._Package().Name` → "pkg/builtins/rt") now green in ALL 6 default modes
  (the 3 VM-mode xfails removed).
- **Still future — the general Functions-table**
  ([`notes-package-introspection.md`](notes-package-introspection.md) Phase B):
  `registerPackageDescriptorExterns` is a hand-maintained precursor covering
  only the builtins compiled INTO the host binary (their `_Package` is a real
  symbol the shim can call).  USER packages run as interpreted bytecode and
  have no `_Package` body — those need the real table: codegen emits a
  per-package `Functions` table (name + signature + function-value per
  exported func), and the VM auto-enumerates all packages' tables (the
  cross-package registry, open Q4 in the notes — likely a linker section with
  start/stop symbols) to bind names → function values, replacing the hand-
  maintained `RegisterStandardExterns` entirely.  Then richer type metadata
  (Phase C) for reflection/printing + RTTI for type assertions.
- **Linter caveat (see "bnlint typechecks dependency bodies" + lint-skip
  entries)**: `registerPackageDescriptorExterns` is the first `_Package`
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

### IR integer constants are host-width `int` (blocks 32-bit-hosted toolchain) — LAYER 1 + 2 (INT64 + FLOAT64) DONE
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

### Sweep for STALE xfails — the runner skips xfailed tests, so now-passing ones sit marked-failing forever (2026-06-13) — 🟡 OPEN (all host-runnable modes SWEPT; only the qemu-gated cross modes remain)
Discovered while triaging done-but-residual todo entries: `const-group-bare-inherited-overflow` was fixed by `b9d6d807` but its 11 `.xfail.*` files were never removed, and `conformance/run.sh` does NOT re-run xfailed tests (it skips them — they show as `x`, never `XPASS`), so the stale xfail was invisible. There are ~247 conformance `.xfail.*` files (+29 unittest); an unknown number are similarly stale.
- **builder-comp + builder-comp-comp (gen2) swept (2026-06-13)**: only ONE stale xfail — `const-group-bare-inherited-overflow` — REMOVED (binate `680a4eca`, all 11 markers; `.error` type-check test, stale in every mode). Both default LLVM modes otherwise clean.
- **VM modes swept (2026-06-13)** — `builder-comp-int` / `-comp-int` / `-int-int`, via `run.sh --check-xpass <mode> <test-names>` (run only the xfailed tests, not the whole hang-prone suite). **25 stale removed in 2 commits:**
  - `8741c552` (14 top-level): `718_funcval_spill_over_vm_cap` ×3 VM modes (bytecode→bytecode func-value dispatch never hits the 7-arg `_call_shim_*` cap — that cap only bites compiled-target/nested-VM); + 11 `-int-int`-only that all blamed now-fixed double-VM infra (`272_raw_slice_star_sugar`; the `586/592/673/674/675/676/677/678/682` cross-pkg `*_balance` family on the int-int "package pkg/builtins/rt not found" loader bug; `665_transitive_iface_reexport` on the int-int multi-package `rt.MemCopy` NULL-deref). Confirmed fixed: the canaries `136`/`383`/`061`/`373`/`384` are unmarked + green under int-int.
  - `bcb3c362` (11 subdirectory readonly/matrix): `pass-arg/value-struct{,-large}` (int/-comp-int/-int-int) + the `-int-int` Round-2 cells (`nested-index/field/nested-value-struct`, `readonly/alias/method-receiver`, `readonly/construct/readonly-iface`, `readonly/wrapper-order/inner-{managed,raw}-ptr`). These were left xfailed only on VM after the plan-cr2-1 Defect-1/Round-2 fixes landed on LLVM (cf. line ~879 "stay xfailed on VM / native-globals").
  - **VM xfails KEPT (genuine)**: `regressions/c-call/*` + top-level `498/500/527/530` (VM has no FFI); `matrix/globals/readonly/struct` (Defect-1 `gen_selector` global-readonly path, still open); `regressions/named-func-value-construct-literal` (open B2 follow-up, xfailed in every mode incl. LLVM); `385/386_iface_nil_dispatch*`; `708/709/725/727_reflect_*`.
- **Unittest comp-comp-int swept (2026-06-13)** — `76fe86cc`: 4 stale (`cmd-bnlint`, `pkg-binate-{codegen,ir,vm}`) that blamed the now-fixed "boot-comp-int VM field-layout bug"; all 4 packages' full suites pass under comp-comp-int. NOTE: `scripts/unittest/run.sh` has NO XPASS detection (it just skips xfailed packages) — sweep by hand (move marker aside → run → restore). The 8 ccall unittest xfails (`pkg-bootstrap`/`pkg-builtins-rt`/`pkg-std-os` in VM modes) are genuine (VM can't interpret `__c_call`).
- **Native aa64 + x64_darwin swept (2026-06-13)**: 0 stale. `386` (compiled SEGVs with no VM panic msg; mode-correct, pinned by `385`), `705/706/707` (native closure-float shim gaps, claude-todo #121 open) all genuinely fail. gen3 (`builder-comp-comp-comp`) lone xfail is `386` — same mode-correct reason, structurally can't XPASS.
- **CROSS MODES SWEPT via the CI workflow (2026-06-14) — 99 stale conformance xfails removed.** The on-demand `.github/workflows/conformance-xpass.yml` (Actions → "Conformance XPASS (stale-xfail sweep)" → Run workflow; blank `mode` = all 10 modes, or pass one) re-runs each mode's xfailed tests under `--check-xpass`; a red job lists XPASS = stale markers. Full-matrix run results:
  - `native_aa64`: **29** `matrix/scalar-diff/*` signed sub-word cells (arith/bitwise/cmp/int-cast/shift/float-conv) — aa64-subword narrowing fixed; binate `5f94558b`. Host-runnable but MISSED by the earlier top-level-only host sweep (the same subdirectory-enumeration lesson — these live under `matrix/scalar-diff/`).
  - `arm32_linux`: **40**, `arm32_baremetal`: **30** — native arm32 backend + multi-return tuple-packing caught up (markers blamed "native arm32 not yet implemented" / Plan-3 tuple-packing; some carried already-stale "drops result type / SILENT wrong-code" text). binate `1ce5a6d9` / `56c275b6`. (Includes the line-~5077 `abi/iface-multi-return{,-assign}` cells — confirmed stale as predicted.)
  - `native_x64`: **22** stale, but only visible AFTER a **workflow bug** was fixed. run.sh filters were substring-match, so the `value-struct` xfail filter also pulled in the *unmarked* `value-struct-large` (which crashes on native_x64) → false-positive that masked everything else. Fixed by `run.sh --exact` (exact filter match) + the workflow passes it (binate `982727d1`). With `--exact`, two consecutive native_x64 CI runs agree on 22 stale: `538_float_lit_tie_roundbit` + `635_float32_arith` + the `matrix/const/*` float32/float64 tie/half/neg/tenth cells (native float round-bit / float32-narrowing, "blocked on a new BUILDER release" = bnc-0.0.9, now shipped); plus `matrix/readonly/*` + `matrix/nested-index/field/*` (plan-cr2-1 Defect-1/Round-2 shared-IR-gen, same cells dropped on the VM modes). Removed: binate `27ba1f7e`. Post-removal native_x64 sweep: green. **All 10 modes now green under the sweep** (121 stale conformance markers removed total: aa64 29 / arm32_linux 40 / arm32_baremetal 30 / native_x64 22).
  - **Unittest sweep now possible** — `scripts/unittest/run.sh` gained `--check-xpass` (binate `ddc624d2`; same XPASS-on-stale semantics, per-package): run `scripts/unittest/run.sh --check-xpass <mode>`. Swept the 3 VM modes: `pkg/builtins/rt`, `pkg/bootstrap`, `pkg/std/os` all XPASS (they're injected as native in the VM, so their tests run against native code and pass — e.g. rt runs 21 passing tests). **8 stale markers removed** (bootstrap+rt on `builder-comp-int`; bootstrap+rt+os on `-comp-int` and `-int-int`); binate `55229591`. The `native_aa64` unittest xfails (11, the weak-`buf.Builder`-dtor dup-symbol MAJOR bug) correctly stay XFAIL (`mangle` re-confirmed genuinely failing). The arm32 unit xfails (16 baremetal + 1 linux) need qemu + the unittest `--check-xpass` isn't wired into CI, so they're UNSWEPT.
  - **STILL OPEN — cross-mode unittest xfails (17)**: the unittest runner (`scripts/unittest/run.sh`) still lacks `--check-xpass` (it just skips xfailed packages), so the workflow is CONFORMANCE-only; sweep those by hand or teach the runner XPASS detection.
  - **FOLLOW-UP — `value-struct-large` on `native_x64`**: it's *not* xfailed there yet crashes (empty output) when run — a real missing-xfail or native_x64 bug, surfaced (then masked) by the substring collision. Worth a look now that `--exact` no longer pulls it in.
- **METHODOLOGY (learned the hard way)**: enumerate sweep sites with `find conformance -name '*.xfail.*'` (RECURSIVE) — a top-level `ls conformance/*.xfail.*` misses ~160 subdirectory (`matrix/`, `regressions/`, `abi/`) markers. Per-mode list: `find conformance -name '*.xfail.<mode>'`. Run only the xfailed tests as filters (amortizes one toolchain build); `--check-xpass` reports `XPASS` for the stale ones.
- **Why it matters**: stale xfails hide regressions (a real future failure on that test would still show `x`) and inflate the xfail count; each one may correspond to a "done-but-not-archived" todo entry.

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

## MAJOR (codegen / closure capture-pad) — closure thunk read captures at the raw capture index, not the padded LLVM field index → dropped/shifted captures past an alignment pad; first exposed on arm32 by `MaxAlign=8` (891/697 float closures) (2026-06-22) — ✅ FIXED & LANDED (`2e279fb9`)

**Symptom.** `conformance/891_func_value_closure_mixed_float_overflow` (a closure
capturing 1 int + 9 float64; the 9th float64 arg overflows the AAPCS VFP regs onto
the stack) prints **136** on `builder-comp_arm32_linux`, expected **145** (=100+1+..+9):
the 9th float64 (9.0) is dropped → 145−9=136. `697_func_value_closure_float_mixed`
similarly wrong (3 vs 33). PASSED at `6f8a2b23` (the commit before `f4b934ce`),
FAIL at `9254f848` → regressed exactly at the MaxAlign fix. arm32-only (native_x64
/ aa64 green); `1ad9e00f` (which added these tests) is an ancestor of 6f8a2b23, so
they genuinely passed before.

**Root cause (CONFIRMED).** `emitClosureCaptureLoads` (emit_funcvals_closure.bn)
GEP'd each capture at its raw capture index `i`, but the packed closure-capture
struct has explicit alignment-pad fields — a capture whose 8-byte type (int64 /
uint64 / float64) follows a narrower one gets a `[N x i8]` pad field before it. The
capture WRITER already maps through `structLLVMIndex` (pad-aware, like all
struct-field access); the thunk's load did not, so it read a pad field as a capture
and shifted every later capture down by one (dropping the last). NOT MaxAlign's
fault (8-align is correct AAPCS + required for the stat fix); `f4b934ce` just
exposed it on arm32 by making float64 8-aligned. Latent on ALL targets too (any
int32-then-int64 capture pads on LP64 — `gen1cur` yielded garbage for 898 on the
host).

**Fix (`2e279fb9`).** Thunk GEPs `structLLVMIndex(f.ClosureStruct, i)` (returns `i`
when no pads, so the common case is unchanged). 891/697 PASS on arm32 (qemu); 68
LP64 closure tests + codegen unit pass. New `898_closure_capture_pad` pins it
cross-target (int32/int64 interleaved capture → 1112; garbage without the fix).

**Diagnostic lesson (recorded).** Earlier 004/closure probing used `ls -dt |
head -1` to pick the gen1, which silently mixed two stale compilers (MaxAlign 4
vs 8) in /tmp and produced phantom "cross-module divergence" conclusions. ALWAYS
pin the exact gen1 path for diagnostics; never `ls -dt | head -1` when multiple
compilers can coexist.

## MAJOR (runtime C / sweep gap) — `bootstrap.ReadDir` (runtime/binate_runtime.c) still uses 32-bit `readdir()` → same EOVERFLOW listing-truncation as os.ReadDir, on a live bnc compile path (2026-06-22) — 🟠 DEFERRED (latent; user decision 2026-06-22)

**Symptom (latent, silent).** `runtime/binate_runtime.c` `bn_F2_3_pkg9_bootstrap1_7_ReadDir`
calls plain `readdir()` (lines 167 count-pass + 181 fill-pass) over a 32-bit
`struct dirent`. The runtime C has NO `#define _FILE_OFFSET_BITS 64` (grep:
none in runtime/), and the arm32-linux clang flags add no `-D` for it
(`cmd/bnc/target.bn` sets only `-march=armv7-a`). So on a 32-bit-Linux host the
runtime gets the 32-bit `readdir` + struct dirent, which returns `EOVERFLOW`→NULL
for a directory entry whose `d_ino` exceeds 2^32 — and `bootstrap.ReadDir` treats
NULL as end-of-stream, silently TRUNCATING the listing. This is the IDENTICAL bug
class fixed for `os.ReadDir` in `1686aac9` (readdir64), left unswept on the
sibling runtime-C path.

**Why it matters (not cosmetic).** `bootstrap.ReadDir` is load-bearing on the
compile path: `pkg/binate/loader/loader.bn:152` enumerates an impl-package
directory's `.bn` files through it; `cmd/bnc/util.bn:321` expands directory args
through it. A truncated listing drops source files → missing funcs/types →
**silent miscompilation**. It bites when `bnc` runs NATIVELY on a 32-bit-Linux
host with a large-inode filesystem. Masked in current CI because
`builder-comp_arm32_linux` cross-compiles bnc on x64 (readdir==readdir64) and
runs only the produced test binary under qemu — bnc itself never runs at 32-bit.

**Root cause (process).** The `os.ReadDir` readdir64 fix did not follow the
"Enumerate Sweep Sites Repo-Wide" rule: a `grep -rn readdir` across the runtime C
would have surfaced this immediately. Found by the adversarial review of the
landed fixes.

**DECISION (2026-06-22): DEFER (option C).** Fully latent — it can only trigger
when `bnc` runs NATIVELY on a 32-bit-Linux host with large inodes, which NO current
config does (CI cross-compiles bnc on x64; arm32-linux is a v0 derisking target, not
a native host). So nothing breaks today. Fix it before `bnc`-native-on-32-bit-Linux
becomes real, or fold it into the eventual `bootstrap.ReadDir` elimination.

**Why not eliminate the path now (the cleaner fix).** `bootstrap.ReadDir` is slated
for removal (subsumed by `os.ReadDir`), but that is BLOCKED in the pinned-BUILDER
tree: its `cmd/bnc` callers (`loader.bn:152`, `util.bn:321`) are BUILDER-compiled and
CANNOT call `os.ReadDir` — the BUILDER (`bnc-0.0.9`) cannot compile `os.ReadDir`
(tested: it returns the managed-slice aggregate `@[]@DirEntry` the BUILDER can't
handle), and `cmd/bnc` does not import `pkg/std/os` at all. Converting
`bootstrap.ReadDir`'s own runtime-C impl off `readdir` is itself a runtime-ABI change
needing a BUILDER bump (see claude-todo ~line 1187). A third caller exists too:
`pkg/binate/interp/externs.bn:284` registers it as a VM extern.

**Fix when addressed (user prefers NO compiler-flag macros).** Change the two
`readdir()` calls (runtime/binate_runtime.c:167,181) to `readdir64()` over `struct
dirent64`, guarded `#ifdef __linux__` (macOS has neither — keep `readdir`/`struct
dirent`). NOT `-D_FILE_OFFSET_BITS=64` (rejected: it sets a global LFS macro via a
flag). The narrower explicit change does NOT cover the runtime's `stat()`/`off_t`
32-bit-Linux LFS exposure — that needs its own explicit `stat64`/64-bit-`off_t`
treatment (same `#ifdef` shape), tracked here as the adjacent item.
