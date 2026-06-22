# Plan: invertible (demanglable) name mangling

Status: 🟢 PLAN APPROVED — implementing (2026-06-20)

Decisions (locked 2026-06-20):
- **Encoding: Option L** (length-prefixed components, Itanium-style).
- **Prep consolidations land incrementally** as each is ready.
- **Demangler: full structured `DemangledName`** (kind + pkg path + name/recv +
  decoded type-args); migrate `recvTypeIsGenericInst` + the VM `__ivt` matcher
  onto it.

## Grammar (Option L — synthesized from the 3-design panel, 2026-06-21) — ✅ SIGNED OFF (prefix `bn_`+kind)

Every mangled symbol is `bn_<kind><body>` (the object-format leading `_` is still
added outside, by `symPrefixed`). `bn_entry` stays a reserved literal (special-
cased first in FuncName, demangle-exempt). All output is `[A-Za-z0-9_]`; counts
are decimal (no leading zero).

Core productions — length-prefix makes every boundary self-delimiting, so literal
`_`/`.`/digits inside a name are never confused with structure:

    Ident   = <len> "_" <bytes:len>            # parseExpr -> 9_parseExpr ; __entry -> 7___entry
    PkgPath = <segcount> "_" Ident{segcount}   # pkg/binate/parser -> 3_3_pkg6_binate6_parser

Kind bodies:

    bn_F PkgPath Ident                 free function
    bn_G PkgPath Ident                 global var
    bn_S PkgPath Ident                 struct type
    bn_M PkgPath Ident Ident           method   (pkg, recvType, method)
    bn_I PkgPath Ident ArgList         generic-inst function (decl + type args)
    bn_T PkgPath Ident ArgList         generic-inst struct
    bn_V PkgPath Ident PkgPath Ident   __ivt    (recvPkg, recvType, ifacePkg, ifaceName)
    bn_W ...(same shape as V)          __ivtshim
    ArgList = <argcount> TypeArg{argcount}

Type-arg sub-language — **resolves Finding A**: a NAMED type is introduced ONLY by
the `N` leaf tag, so a type named `ptr`/`slc`/`arr` can never be read as a
constructor (the old prefix collision is impossible):

    TypeArg = "p" TypeArg              # *T            "m" TypeArg   # @T
            | "s" TypeArg              # *[]T          "M" TypeArg   # @[]T (managed-slice)
            | "r" TypeArg              # readonly T
            | "i" TypeArg | "j" TypeArg# *Iface / @Iface
            | "a" <len> TypeArg        # [len]T
            | "f" Sig | "F" Sig | "g" Sig            # func / *func / @func value
            | "N" PkgPath Ident        # named/primitive leaf (primitive: empty PkgPath = `0_`)
    Sig     = "p" <pc> TypeArg{pc} "r" <rc> TypeArg{rc}

(Kind letters appear only right after `bn_`; type-arg constructors only inside
ArgList/Sig — so `M`-method vs `M`-managed-slice never collide; context decides.)

Decorations (wrappers the demangler peels): `Dvt_<core>` (was `__vt.`),
`Dhd_<core>` (`___handle.`), `Dsh_<core>` (`__shim`). Reflect symbols stay as
recognizable suffixes on a core: `<core>__fnname`/`__fnsig`/`__fninfo`, per-package
`___Package`.

Demangler: strip object prefix → `bn_entry`? reserved → peel decoration tag → read
kind letter → parse the length-prefixed body → `DemangledName{kind, pkg[], name,
recv, args[]}`. No side table. `__c_call` C symbols are never mangled.

Injectivity: every variable-length field is length-counted, so no concatenation of
distinct components can realign into a different parse → distinct inputs give
distinct strings. Verified by the `demangle(mangle(x))==x` round-trip suite.

Open sub-decisions (recorded; defaults chosen unless you object): **(P)** prefix
`bn_`+kind (continuity) vs `_B`+kind (clean old/new separation) — default `bn_`;
**(D)** dtor/copy helpers ride the func/ident path (their `__dtor_<suffix>` name is
just a length-prefixed Ident) rather than a dedicated `bn_D TypeArg` kind — default
ride-along (less invasive; the dtor-type-suffix mini-language's own injectivity is
tracked separately). **Riskiest implementation point (not a grammar choice):** the
VM's runtime `__ivt` iface-suffix rewrite (`swapIfaceSuffix`) must change from
byte-scanning `__<ifacePkg>__<ifaceName>` to reading the trailing `PkgPath Ident`
of the `bn_V` body — the cross-mode contract.

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
2. ✅ **LANDED (`2837438e`)** — **Consolidate the four `__ivt` builders** into
   `mangle.ImplVtableName`/`ImplVtableShimName`. Byte-identical. (The VM runtime
   *matcher*/decoder is folded into the flip step, where the format changes.)
3. ✅ **LANDED (`a754c6a8`)** — **Route the three rt-literal manifests**
   (LLVM/x64/aarch64) + `vm.Trampoline*` through `mangle.FuncName` (LLVM via a
   `rtSym()` helper). Byte-identical.

Flip — split into 4a (de-risk, landable) then 4b (atomic):

4a. ✅ **LANDED (`a0a0ea80`).** New encoders + demangler +
    round-trip/injectivity tests, UNWIRED (`mangle_lp.bn`, `mangle_lp_demangle.bn`
    + tests). Adversarial review caught + fixed a CRITICAL (Demangle integer-
    overflow crash → unsound bounds check; now `lpReadCount` caps indexing counts
    at `len(c.s)`, array length skipped via `lpSkipCount`) plus oversize-count,
    negative-array-length-doc, and dead-`lpWritePkgPath` items.
    ORIGINAL: Add the Option-L encoders
    (`encodeIdent`/`encodePkgPath`/the kind bodies/the type-arg sub-language) and
    the full `Demangle` as NEW functions in `mangle` (not yet called by
    FuncName/etc.), plus a `demangle(mangle(x))==x` round-trip + injectivity test
    suite over every name class (incl. the collision cases the old scheme fails:
    literal-`__` vs `.` vs `/`, named-type vs prefix). Pure new code, no behavior
    change → lands incrementally. Validates the grammar+demangler in isolation
    before the disruptive rewire.

4b. ✅ **DONE (`ceff78ec`, worktree — pending land).** Validated: gen1 builds;
    full unit suite green; conformance green on builder-comp / builder-comp-int
    (VM 2109/0) / native-aarch64 / gen2 self-host — only failures anywhere are
    the PRE-EXISTING reflect 725/727 (ResultSize=0), which fail identically on
    the clean base (a concurrent ABI regression, filed). Runtime lockstep + all
    diffs hand-verified; test sweep not weakened. ORIGINAL:
    **The atomic flip.** Rewire `FuncName`/`GlobalName`/`StructName`/
    `ImplVtableName`/`mangleTypeArg`/`mangleFuncSig` to the new encoders (FuncName
    infers kind F/M/I and StructName S/T from the name shape: `.`-member count +
    `__bn_inst__` marker) + lockstep-edit the checkout runtime C/asm
    (`binate_runtime.c`, `crt0.s`, `semihost.s`, `native_test_stubs.c`) + switch
    the VM `swapIfaceSuffix` to read the trailing `PkgPath Ident` of `bn_V` +
    `recvTypeIsGenericInst` to the demangler/marker + update the ~43 exact-string
    test assertions. Necessarily one commit (every symbol changes at once).

Note (implementation): the instantiation type-args ride as the existing
`__bn_inst__`-suffixed IR name (now built from the injective new `mangleTypeArg`
tokens); FuncName splits `decl|tokens` to emit `bn_I` with the tokens passed
through (they are already the self-delimiting type-arg sub-language).

### 4b execution order (one atomic commit — the build is red until ALL of it is done)

Integration decisions:
- **Export the lp encoders** from `mangle.bni` so `ir`'s `mangleTypeArg`/
  `instantiationMangledName` build the new type-arg tokens via the SAME encoders
  the demangler inverts (one source, no divergence). Promote `mangle_lp.bn`'s
  `lp*` type-arg encoders + `Ident`/`PkgPath` helpers to exported names; add a
  raw-`pkgPath`-string entry that splits + delegates to `lpWritePkgPathSegs`
  (the one canonical split site 4b needs — re-adds what 4a removed, now USED + tested).
- **`Demangle` becomes exported** (vm needs it for `swapIfaceSuffix` +
  `recvTypeIsGenericInst`).
- Only these hand-written externals need lockstep (rt.*/bootstrap.* etc. that go
  through the compiler are auto-consistent): **`binate_runtime.c`** (the ~9
  `bn_pkg__bootstrap__*` defs), **`native_test_stubs.c`** (`rt.RawFree`),
  **`crt0.s`+`semihost.s`** (arm32 `bn_pkg__semihost__*`). `bn_entry` stays literal.

Order:
1. `mangle.bn`: rewire `FuncName`/`GlobalName`/`StructName`/`ImplVtableName`/
   `ImplVtableShimName` to the lp encoders (FuncName parses pkg + `.`-members,
   infers F/M/I from member-count + `__bn_inst__`); keep `bn_entry`; drop the now-
   unused `WritePkgIdent`/`WriteFoldedName` (+ from `.bni`). Update `mangle_test.bn`.
2. `ir/gen_generic_mangle.bn`: `mangleTypeArg`/`mangleFuncSig` → exported lp
   type-arg encoders; `instantiationMangledName` unchanged in shape (tokens now lp).
   Update `gen_generic_mangle_test.bn`.
3. Runtime lockstep: regenerate the hand-written `bn_*` symbols above to the new
   scheme (compute each via the new `FuncName`).
4. `vm`: `swapIfaceSuffix` → read the trailing `PkgPath Ident` of `bn_V` (via
   `Demangle`); `recvTypeIsGenericInst` → `Demangle` + `__bn_inst__` marker.
5. Sweep the remaining ~43 exact-mangled-string test assertions to the new form
   (build a small "old→new" computor; replace; do NOT hand-guess).
6. Validate: gen1 build, full unit suite, native (aa64) + VM conformance, hygiene.
   Re-verify the arm32 asm path compiles (crt0/semihost symbols).

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
