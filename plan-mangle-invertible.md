# Plan: invertible (demanglable) name mangling

Status: 🟢 PLAN APPROVED — implementing (2026-06-20)

Decisions (locked 2026-06-20):
- **Encoding: Option L** (length-prefixed components, Itanium-style).
- **Prep consolidations land incrementally** as each is ready.
- **Demangler: full structured `DemangledName`** (kind + pkg path + name/recv +
  decoded type-args); migrate `recvTypeIsGenericInst` + the VM `__ivt` matcher
  onto it.

## Motivation

The compiler's symbol mangler is **non-injective**: `mangle.writeBnDotted` /
`writePathIdent` fold both `.` and `/` to `__` and never escape a literal `_`,
so `a.b`, `a/b`, and `a__b` all mangle identically. The same fold is inlined in
`mangleTypeArg`'s named-type leaf. Consequences:

- **Finding A** (filed in claude-todo): a generic type-arg named type can
  reproduce a synthetic prefix (`ptr_q.Bar` vs `*q.Bar` → `ptr_q__Bar`), and the
  `.`/`/` 2-to-1 fold collides distinct package-qualified names. Latent (needs an
  unusual-but-legal package name) but real wrong-code (two instantiations → one
  symbol).
- The codebase **already** reads structure back out of mangled names by fragile
  substring scanning — `recvTypeIsGenericInst` scans for `__bn_inst__`, and the
  `__ivt.` vtable name is structurally parsed at VM runtime. A real **demangler**
  would replace those hacks with a principled API, and is independently valuable
  for the reverse/debug toolchain (objdump, crash backtraces, the VM's
  vtable-name scan — the symbol-only consumers DWARF can't help).

Goal: a mangling that is **injective and invertible**, verified by a
`demangle(mangle(x)) == x` round-trip over every name class, plus injectivity
tests for the collision cases the current scheme fails.

## Current state (recon 2026-06-20)

Two **independent** name namespaces; keep them separate:

1. **`bn_` linker-symbol namespace** — `mangle.FuncName` / `StructName` /
   `GlobalName` (→ `writeBnDotted` / `writePathIdent`). Used by the LLVM + native
   backends and the cross-mode vtable-symbol contract. **This is what we make
   invertible.**
2. **dot-qualified namespace** — `mangle.QualifyName` (`<pkg>.<name>`). The VM's
   flat `funcIndex`, the C-extern registry, the global registry, and
   `reflect.FunctionInfo.Name` / `GlobalInfo.Name` all key on this. **Leave the
   dot-qualified strings as-is** (the redesign is free here) — but `QualifyName`
   must stay injective over `(pkgPath, name)` (it already is; don't regress it).

The fold is reimplemented in **six** places that must all agree, and the
`__ivt.` vtable symbol is built in **four** and parsed in the VM:

- Fold sites: `mangle.writePathIdent`, `mangle.writeBnDotted`,
  `ir.writePkgIdent` (gen_iface_vtable.bn), `codegen.writePkgIdent`
  (emit_impls.bn), `vm.writeVtablePkgIdent` (lower.bn), and `mangleTypeArg`'s
  default branch (gen_generic_mangle.bn).
- `__ivt.bn_<recvPkg>__<recvType>__<ifacePkg>__<ifaceName>` built by
  `ir.buildImplVtableSymbol` (declared "single source of truth" but not actually
  shared), `codegen.implVtableName` + `implShimVtableName`, `vm.implVtableName`;
  stored as `reflect.VtableInfo.Name` and **structurally parsed at VM runtime**
  (suffix find-and-replace on `__`-delimited components). Highest-risk contract.
- Runtime-call symbols (`bn_pkg__builtins__rt__{RefInc,RefDec,Alloc,Box,
  MakeManagedSlice,BoundsCheck,DivCheck,ShiftCheck,ZeroRefDestroy,RawFree}`)
  hardcoded as **literals** in three emitters (LLVM codegen, x64, aarch64),
  bypassing the mangler.

**Hand-written external pins** (must change in lockstep with the scheme):
`runtime/binate_runtime.c` (defines `bn_pkg__bootstrap__*`, `extern bn_entry`),
`runtime/baremetal_arm32/crt0.s` + `semihost.s` (`bn_entry`,
`bn_pkg__semihost__*`), `runtime/native_test_stubs.c`
(`bn_pkg__builtins__rt__RawFree`).

**NOT BUILDER-gated.** `build_gen1` links the BUILDER's *bundled* runtime
(`--runtime $blib`); gen1's *outputs* link the *checkout* runtime
(`--runtime $BINATE_DIR`) — confirmed by `build-bnas.sh:91-95`. The two never
cross, so a lockstep edit of the checkout runtime C/asm keeps gen1 (old scheme +
bundle runtime) and gen1-outputs (new scheme + checkout runtime) each internally
consistent. No BUILDER bump needed.

**Not constraints:** `__c_call` carries verbatim C symbols (untouched); DWARF
carries source + linkage name separately; the object-format leading-`_` (Mach-O)
is applied by `symPrefixed` *outside* the mangler (strip it before demangling).

**Test blast radius:** ~43 non-conformance test files assert exact mangled
strings (mangle_test.bn, gen_generic_mangle_test.bn, + ~41 native/codegen/asm).

## Invariants the new scheme must preserve

1. **def/call byte-identity.** An instantiation's IR-level instance name is the
   same dotted string at def and call sites (gen_generic.bn:121 overrides
   `f.Name` to the full dotted name); a *single* deterministic mangle must map it
   (no path-dependent folding). Pinned by conformance/867 (ODR-merge).
2. **`__ivt` cross-mode contract.** The four builders + the VM matcher must
   produce/parse byte-identical names — so consolidate them into ONE shared
   encoder/decoder in `pkg/binate/mangle`, and replace the VM's `__`-split parse
   with a call to the shared decoder.
3. **method triple** `<recvPkg>.<recvName>.<methodName>` stays injective (it's
   the join key between the bn_ symbol and the VM slot lookup).
4. **`__bn_inst__` detectability.** Keep a reliable "is generic instantiation"
   marker; replace the substring scan with a structured check via the demangler.
5. **`bn_entry` reserved.** `main.__entry` → `bn_entry` stays a demangle-exempt
   sentinel (referenced by C/asm; the documented entry ABI point).
6. **object-format prefix** stays outside the invertible core.

## Encoding decision (USER SIGN-OFF NEEDED)

### Option L — length-prefixed components (Itanium-style) — RECOMMENDED

Each component encoded as `<kind/sep tag><len><chars>`; lengths self-delimit so
`_` stays **literal** (big readability win given Binate's `__entry`,
`__bn_inst__`, `___handle`, `__dtor_` names). Carries a kind tag (func / global
/ struct / method) so the class is recoverable. Sketch:

```
bn_ + P<len>pkg P<len>binate P<len>parser  F<len>parseExpr      # free func
bn_ + P3pkg P4geom  T5Point M1M                                 # method (kinds shown spaced for clarity; emitted contiguous)
```

Demangle = read tag, read decimal length, read that many bytes, repeat.
Type-arg sub-language (`ptr_`/`slc_`/`arr<N>_`/`fn_`+sig/named) gets the same
length-prefix treatment for its named-type leaf, so a named type can never
reproduce a prefix.

- **Pros:** `_` literal (readable); fully injective; class/separator recoverable;
  clean recursive demangler; standard.
- **Cons:** the VM `__ivt` parse and the `__bn_inst__` scan must become
  length/structure-based (they're being consolidated anyway); decimal lengths
  add minor noise.

### Option E — escape characters

`.`→`_d`, `/`→`_s`, literal `_`→`_u`; structural joins stay `__` (no escape
output produces a bare `__`). Demangle = scan, substitute back.

- **Pros:** simplest transform/demangler; smallest conceptual change.
- **Cons:** every literal `_` doubles, so the synthetic `__`-heavy names become
  noisy (`__bn_inst__` → `_u_ubn_uinst_u_u`); no class tag unless added.

Recommendation: **Option L.** Decide before any code.

## Demangler

New `pkg/binate/mangle`: `Demangle(sym) -> (DemangledName, ok)` returning a
structured result (kind + package path components + name/type/method +, for
instantiations, the decoded type-arg list). Strip the object-format prefix and
the `bn_` prefix first; special-case `bn_entry`. BUILDER-compatible (plain
parsing). Consumers to migrate onto it: `recvTypeIsGenericInst` (instantiation
check), the VM `__ivt` matcher.

## Staging (each commit green; no BUILDER bump)

Prep (behavior-preserving — byte-identical output, land incrementally):

1. ✅ **LANDED (`e8637018`)** — **Consolidate the six fold sites** into two
   shared `mangle` encoders (`WritePkgIdent` `/`-fold, `WriteFoldedName`
   `.`+`/`-fold); `ir`/`codegen`/`vm`/`mangleTypeArg` route through them. Byte-
   identical.
2. **Consolidate the four `__ivt` builders** + the VM matcher onto one shared
   `mangle` vtable-name encoder/decoder. No output change.
3. **Route the three rt-literal manifests** (LLVM/x64/aarch64) and the
   `vm.Trampoline*` literals through `mangle.FuncName` (one runtime manifest). No
   output change.

Flip (necessarily atomic — but prep shrank its surface to the encoder + runtime
+ tests):

4. **Switch the shared encoder** to Option L (incl. `mangleTypeArg` leaf) +
   lockstep-edit the checkout runtime C/asm (`binate_runtime.c`, `crt0.s`,
   `semihost.s`, `native_test_stubs.c`) + update the VM `__ivt` decoder + the
   `__bn_inst__` check + update the ~43 exact-string test assertions.
5. **Add `Demangle` + round-trip + injectivity tests** (the collision cases the
   old scheme failed: literal-`__` vs `.` vs `/`, named-type vs prefix). Can ride
   with (4) or immediately follow.

Post: update claude-todo (close Finding A + the writeBnDotted general-symbol
fold); land the deferred named-axis injectivity guard from the coverage commit.

## Risk / effort

Real project: ~3 prep commits (each a safe consolidation) + 1 large flip +
1 demangler/tests. The flip is the risk concentration (cross-mode `__ivt`,
runtime lockstep, ~43 test files); the prep work exists specifically to shrink
and de-risk it. Bug fixed is latent, but the consolidation (one fold encoder,
one rt manifest, one vtable-name codec) and the demangler are durable structural
wins beyond the bug.

## Open decisions for the user — RESOLVED 2026-06-20

1. Encoding: **Option L (length-prefix)**.
2. Prep consolidations: **land incrementally**.
3. Demangler: **full structured `DemangledName`**.
