# Plan: structural type-identity for slices (`proposal-slice-type-identity`)

Status: **Spec RATIFIED** (2026-07-16) — the design (exact-match structural
identity + the `AssertTarget` grammar) is locked and in the canonical `binate.ebnf`;
`iface.assert.slice` is **Draft** on the stability axis only because it is **not yet
implemented**. Impl not started. Remaining owner choice is sequencing (§5).

## 1. Goal

Give slice types a **structural type-identity** so a slice can be the dynamic
type of a well-formed `any` box and can be an assertion / type-switch target
(`case *[]readonly char:`). This is the enabling primitive for the decided fmt
`...*any` direction (claude-notes.md:252, `builtin.print`): a Binate string is a
**raw char-slice** (no named string type, no wrapper), and `slice types cannot
impl an interface` (receiver must be named), so a formatter can only recover a
string operand by asserting a slice target. Structural slice identity is
therefore not one option among several — under the ratified constraints it is the
**only** path to "fmt recognizes a string."

It also subsumes the fix for the open **MAJOR crash** (below): the crash-fix is a
strict subset of this work and lands first.

## 2. Background: the MAJOR crash (independent of this feature)

Boxing a **name-less** type (currently: any slice) into `any` produces a
**structurally invalid** interface value. The repro boxes `&s` (a `*@[]char`),
which passes the pointer-shape guard at `gen_iface.bn:196`; then
`receiverBaseTypeName` returns empty for the slice pointee, so `wrapAsIfaceValue`
bails at its `len(srcName) == 0` guard (`:207`) and returns `nil` — it never
reaches the `ensureAnyImplInfo`/`findImplVtableName` synthesis at `:236`–`:250`.
The "box" is then a bare data pointer where a 2-word `%BnIfaceValue` is expected,
and any concrete type-compare over it (`case *int:`, `x.(*int)`) loads `vtable[1]`
off a garbage word → SIGSEGV. A well-typed program must never segfault, so this is
a MAJOR in its own right, fixable **before and independent of** the feature. (A
*bare* slice value would bail earlier at the `:196` pointer-shape guard, but that
is a clean checker error, not this crash.)

## 3. Two layers

- **Layer A — crash fix (option i).** Make every name-less box **well-formed**:
  emit a real, shared **opaque** `__ivt` + `__typeinfo` for name-less types so a
  match is a clean **guaranteed miss** (falls to `default`). Sound because no
  assertion target can name a slice today, so its identity is never compared
  against a written target. **No spec change** (impl-conformance to the existing
  "match is a clean miss, never a crash" contract) and **no §11.12 relaxation**.
- **Layer B — the feature.** Upgrade **slices** from the shared opaque record to
  a **distinct structural** identity per spelling, and relax §11.12 to admit
  slice targets. Layer A is forward-compatible: B replaces the slice subset of
  the opaque record with structural records and adds the parser relaxation.

## 4. Phased execution

Each phase is independently landable and keeps every mode green. Verify = unit
tests of touched packages (§8.5 smoke map) + targeted conformance, NOT the full
suite (landing discipline). Recon-confirmed facts backing each step: §8.

### Phase 0 — Layer A (crash fix, RAW `*any` only). ✅ LANDED `742b6f8e` (2026-07-16). *No spec change.*

*(Landed as described below. The adversarial diff review added: nested-pointer
coverage — `isBoxableNamelessType` peels wrappers to any depth, matching
`receiverBaseTypeName` — plus conformance `1074` extended to slice/array/func/
nested and `1075` for the expr-form clean panic. Verified LLVM / VM / native-aa64
+ hygiene 17/17.)*

**Scope — RAW iface only (adversarial-review finding).** Phase 0 fixes the
name-less box crash for the **raw** iface value (`*any`, `TYP_INTERFACE_VALUE`)
— the tracked MAJOR and the fmt-relevant path. The **managed** iface (`@any`)
name-less case is a *separate* problem, deliberately NOT handled here — see the
"Managed `@any`" note below and §9.

**Change.** `ir/gen_iface.bn wrapAsIfaceValue`, at the `:206`–`:207` guard: when
`receiverBaseTypeName(val.Typ)` is empty AND the pointee is a boxable-but-
name-less type (passed the `:196` pointer-shape guard, no nominal name) AND
`dstTyp.Kind == TYP_INTERFACE_VALUE` (raw), do NOT bail. Substitute a **single
reserved sentinel identity** and fall through to the existing `isUniverseAny`
synthesis at `:236`–`:239`, passing `val.Typ.Elem` as the record's `recvTyp`
(valid size/align/name). For a **managed** dst (`TYP_INTERFACE_VALUE_MANAGED`)
with a name-less pointee, keep the current bail unchanged (§9). The rest
(`ensureAnyImplInfo` → `registerTypeInfo` `:301` → `findImplVtableName` →
`EmitIfaceValue`) is unchanged and already correct.

- **Add an `isBoxableNamelessType(t)` helper** (review nit) — true for the kinds
  that pass `:196` but have no nominal name — rather than an implicit
  "receiverBaseTypeName was empty" test, so the intent is explicit and future
  kinds aren't silently swept in.
- **Sentinel choice.** Fixed `(recvPkg, recvName)` — `("pkg/builtins/rt",
  "__nameless")` — so all name-less boxes share ONE weak-coalesced record
  program-wide (`__typeinfo`/`__ivt` are `DG_WEAK`, §8.2; VM materializes it via
  `CollectTypeInfoDescs`, review-confirmed). A per-package sentinel is also
  correct but proliferates symbols. `implDtorFuncName("pkg/builtins/rt",
  "__nameless")` names a non-existent dtor → `implDtorSlotSym` returns empty →
  **null dtor slot** — which is *correct only for the raw borrow* (no RefInc, no
  ownership; §8.1). *(Cross-TU cosmetic: the record's name blob is whichever
  slice type registered first in the surviving TU; only matters for an expr-form
  panic message, never for correctness.)*

**Why no leak / mis-drop (raw).** A raw `*any` does no RefInc at construction
(`:256` RefInc is managed-only) and no RefDec at drop — it borrows. The opaque
record is never a match *hit* (no `case` names a slice pre-Phase-4), so its
content is cosmetic; only its stable ADDRESS matters and every compare is a miss.

**Managed `@any` — deliberately out of Phase 0 (see §9).** A name-less *managed*
pointee CAN reach `@any` (e.g. `var a @any = box(s)` where `s @[]char` →
`@(@[]char)`), and it crashes today (same degenerate box). But there a null dtor
would **leak**: `@any` RefIncs at `:256` and drops via `emitManagedIfaceValueRefDec`,
whose null-slot-0 path falls through to plain `rt.Free` (`gen_util_refcount.bn:201`)
— freeing the cell but skipping the inner slice's backing RefDec. Fixing it needs
a *real* dtor for the name-less managed type (or a checker rejection), so it is
tracked separately (§9). Phase 0 leaves the managed path bailing (still the
pre-existing crash — not regressed, but not fixed).

**Test.** New conformance `1074_any_box_nameless_no_crash` (positive): a
`switch v.(type)` with `case *int:` + `default`, and a comma-ok `v.(*int)`, both
over a `*any` boxing `&s` (`s @[]char`) AND over a raw-slice box (`*[]char`, the
no-dtor path) — reach `default` / `ok == false`, program completes, exact
`.expected`. PASSes in every mode (no xfail). *(Optional companion error-test:
expr-form `v.(*int)` over the same box panics CLEANLY via `rt.AssertFail` instead
of crashing.)*

**Verify.** `./scripts/unittest/run.sh builder-comp pkg/binate/ir` +
`pkg/binate/codegen`; `./conformance/run.sh builder-comp 1074` AND
`builder-comp-int 1074` (VM path — MANDATORY, review-flagged) AND one native
mode; `./scripts/hygiene/run.sh`. On landing: narrow the `claude-todo.md` MAJOR
entry to the still-open managed `@any` case (§9), moving the raw fix to the done
log.

### Phase 1+2 (raw) — structural mangling primitive + boxing. ✅ LANDED `724cd6df` (2026-07-16).

*(Combined into one "Chunk 1": the primitive `namelessAnySrcName` reuses the
existing `mangleTypeArg`, and the raw-`*any` box branch keys each name-less type
on its own structural `pkg/builtins/rt.__nameless_<lp>` identity — superseding
Phase 0's single shared-opaque sentinel. Still a clean MISS (inert) until the
HIT phase; conformance 1075 guards the wire-in via the per-type panic name. The
§10 readonly gap remains a HIT-phase blocker.)*

### Phase 1 — structural mangling primitive. *(Layer B; no behavior change yet.)*

**Change.** `pkg/binate/mangle`: add an entry deriving a stable `__typeinfo.` /
`__ivt` core from a slice `@types.Type`, encoding `{ raw (TYP_SLICE=10) | managed
(TYP_MANAGED_SLICE=11), element-readonly (TYP_READONLY wrapper on .Elem),
element-type }` **recursively**. Reuse `lpTypeArgSlice` (`'s'+inner`) /
`lpTypeArgMslice` (`'M'+inner`) (`mangle_lp.bn:195–213`) — they already lp-encode
raw/managed slices for generic instantiation, element recursing through the same
pipeline. No new alias logic: `char`/`uint8`/`byte` are the *same singleton Type*
(§8.3), so `@[]char` ≡ `@[]uint8` mangles identically for free.

**Unit tests** (`pkg/binate/mangle/*_test.bn`): distinct spellings → distinct
symbols (`@[]char` ≠ `*[]char` ≠ `@[]readonly char` ≠ `*[]int`); alias-collapse
(`@[]char` == `@[]uint8` == `@[]byte`); nested `@[]@[]char`; no double-encoded
element in the `bn_V` core. **Verify.** mangle unit tests; hygiene.

### Phase 2 — boxing keys slices on structural identity. *(Layer B.)*

**Change.** Same `:207` site Phase 0 touched: when the pointee is a **slice**,
derive its Phase-1 *structural* name (instead of the shared opaque sentinel), so
`ensureAnyImplInfo` + `registerTypeInfo` produce a real `(slice, any)` ImplInfo +
vtable keyed on the structural symbol. Unnamed struct/array/func keep the shared
opaque record. Now `@[]char` and `*[]int` have **distinct** `__typeinfo`
addresses. No box *representation* change — the `&s` data slot already holds a
pointer to the slice header (passed `:196`).

*(Boxing a **bare** multi-word slice value — a shape fmt args might take — needs
the value materialized so its address can be boxed, or stays a checker error;
deferred to Phase 5, not needed for the crash fix or `&s`-form.)*

**Test.** conformance: distinct slice boxes (`@[]char`, `*[]int`) each reach
`default` and do not alias (still no crash, no *hit* until Phase 4). **Verify.**
ir + codegen unit tests; conformance; hygiene.

### Phase 3 — match + recovery. *(Layer B; consumes Phase 2's records.)*

**Change.** `ir/gen_assert.bn typeInfoSymFor` derives the Phase-1 structural
symbol for a slice target; the slot-1 compare (`gen_assert.bn:45–52`,
`gen_assert_commaok.bn:69–75`, `gen_type_switch.bn`) then Just Works.

**The one genuinely new bit — recovery is by-value, not pointer.** `recoverPointer`
(`gen_assert.bn:126`) today returns the data slot AS the target type. But a slice
target (`case *[]readonly char:`) is a *slice type*, not pointer-recovery `*T`:
the box's data slot holds `&s` (a pointer to the slice header), so recovery must
**load the slice value** (the 2-/4-word header) from that pointer — one extra
indirection. **Refcount (review-corrected):** the box is a `&s` *borrow*, so the
recovered slice shares `s`'s backing and must NOT RefInc — a blind RefInc on the
borrow path double-counts. (Only the deferred bare-value boxing of §4-Phase-2
would own backing and need `mem.copy`/RefInc.) Pin the exact borrow semantics
when writing the branch. Add a slice-target branch to `recoverPointer`; verify
**exact-match**: `@[]char` box ≠ `case @[]readonly char:`, `*[]char` ≠ `@[]char`.

**Test.** conformance: box `&s` (`@[]char`), recover via `case @[]char:`, observe
the bytes; `@[]readonly char` box does NOT hit `case @[]char:`; managed-recovery
refcount balance (no leak / no double-free, loop form). **Verify.** ir + codegen
unit tests; conformance incl. `builder-comp-int` (refcount parity); hygiene.
*(Land after Phase 4 admits the target; Phases 2/3 order-independent once it is.)*

### Phase 4 — parser/checker §11.12 relaxation. *(Design ratified — not gated.)*

`parser/parse_assert.bn parseAssertTargetName` (`:69`–`:99`): admit a **slice**
target in `AssertTarget` per the ratified production
`( ( "*" | "@" ) "[" "]" Type )`; keep func/array/struct/`Self` rejected (current
`errMsg` stays for those). Checker resolves the slice target and drives the
structural compare. **Test.** `case *[]char:` now type-checks; func/array/struct
targets still error (keep existing `.error` tests green). **Verify.** parser +
types unit tests; §11.12 `.error` conformance tests; hygiene.

### Phase 5 — impl-complete flip + fmt adoption. *(On conformance-green.)*

Flip `iface.assert.slice` Draft→Provisional on the stability axis (grammar
already in `binate.ebnf`); let fmt's `...*any` fast-path use slice `case`s (one
`case` per accepted string spelling — a library concern). Settle **bare
multi-word slice-value boxing** for fmt args (Phase 2 note): materialize-and-box-
the-address, or keep it a checker error. fmt itself is a separate follow-up plan.

## 5. Decisions

**Ratified (2026-07-16):**

1. **Exact-match distinctness** — element-`readonly` and managed-vs-raw are
   **distinct** identities (⇒ `@[]char` box does not match `case @[]readonly
   char:`). The sound choice (collapsing would silently drop/add `readonly` or
   confuse the 2-word vs 4-word representations); it drives the fmt "one case per
   spelling" wart, accepted as a library concern.
2. **Grammar form** — `AssertTarget = ( [ "*" | "@" ] [ "readonly" ] TypeName ) |
   ( ( "*" | "@" ) "[" "]" Type )`; slice target is value-recovery-only (the
   leading `@`/`*` is the slice's managed/raw marker, not a recovery-kind prefix).
   In `binate.ebnf` + Annex A.

**Remaining — owner's operational call (not a spec blocker):**

3. **Sequencing.** Land Phase 0 (crash fix) **now** as an independent MAJOR
   regardless of fmt timing, then Phases 1–5 when fmt work starts — or do B in one
   shot if fmt is imminent (avoids touching boxing/match twice).

## 6. Non-goals

- Structural identity for unnamed **struct / array / function** targets — they
  keep the shared opaque record (well-formed, un-nameable) until separately
  requested. Layer A already makes their boxes safe.
- Any **structural/kind** ("any char slice regardless of managed/readonly")
  matching primitive — that is a much larger type-switch question, out of scope.
- A named `string` type or a `Stringer`-style constraint for char-slices — both
  ruled out by prior decisions; this plan is the alternative they imply.

## 7. Spec status

**Ratified as Draft** (design locked, not yet implemented): §11.12
`iface.assert.slice` (+ carve-out in `iface.assert`, present-tense inlined grammar,
caveat in the "Implemented" note, chapter badge), the §7.13.14
`type.layout.typeinfo` name-less-types note (Layer A baseline + Layer B upgrade),
and the `AssertTarget` production in the canonical `binate.ebnf` (propagated to
Annex A). `rule-ids.txt` unchanged (no new rule-ID). The only remaining spec move
is the **Draft→Provisional** flip once the implementation is conformance-green
(Phase 5).

## 8. Recon-confirmed facts (2026-07-16)

### 8.1 Vtable emission (Phase 0)
- `ensureAnyImplInfo` (`ir/gen_iface.bn:280`) appends the ImplInfo AND calls
  `registerTypeInfo` (`:301`) — the coupling that guarantees slot-1 `__typeinfo`
  exists. `ensureGenericImplInfo` (`ir/gen_generic_method.bn:234`) is the working
  precedent for synthetic recv-names.
- `implDtorFuncName` (`ir/gen_impl.bn:429`) → `""` when no dtor; `implDtorSlotSym`
  (`codegen/emit_impls.bn:323`) → `""` (null slot) when dtor empty OR
  `lookupModuleFunc(m, dtorName) == nil` — so **no dangling dtor symbol**.
- `emitImplVtable` (`codegen/emit_impls.bn:222`) walks `m.Impls` with **no check**
  that `RecvTypeName` is a declared type; `BuildImplVtable`
  (`irdata/data_impl_vtable.bn:21`) emits `[N x i8*]`, empty slot → null reloc.
- **Risk:** slot-1 undefined if the opaque name isn't `registerTypeInfo`'d — the
  `:301` coupling covers it, so Phase 0 must reach `ensureAnyImplInfo`, not an
  early return.

### 8.2 TypeInfo record (Phases 0–3)
- 5-word layout (IntSize each): `[dtor, size, align, name-ptr, name-len]`
  (`irdata/data_typeinfo.bn:13`). Empty name → null ptr + 0 len, no blob; empty
  dtor → null. `DG_WEAK` → one survivor per symbol program-wide.
- `registerTypeInfo` dedups on `(RecvPkg, RecvTypeName)`; first `recvTyp` wins;
  size/align/name read at codegen (`CollectTypeInfoDescs`, `ir/data_typeinfo.bn:53`).
  Cosmetic for the opaque record (address-only identity).
- Miss path never reads the record (`gen_assert_commaok`, `gen_type_switch`); only
  expr-form's *panic* path reads the name (`gen_assert.bn:58`) — safe with a valid
  opaque record.

### 8.3 Types + mangler (Phases 1–2)
- `TYP_SLICE=10` (raw `*[]T`), `TYP_MANAGED_SLICE=11` (`@[]T`); element in `.Elem`;
  element-`readonly` = `TYP_READONLY` wrapper on `.Elem`.
- `char`/`uint8`/`byte` = one singleton (`predeclaredUint8`) — alias-collapse is
  free (pointer identity, not a `TYP_ALIAS` wrapper).
- Reuse `lpTypeArgSlice`/`lpTypeArgMslice` (`mangle_lp.bn:195–213`). `TypeInfoName`
  = `"__typeinfo." + StructName(pkg, name)` (`mangle.bn:304`); need a structural
  variant taking a slice `@types.Type`. `receiverBaseTypeName` (`gen_iface.bn:332`)
  returns `""` for slices — the site to extend.

### 8.4 Match + recovery (Phase 3)
- iface value = 2 words `{data(0), vtable(1)}` (`types/layout_offsets.bn:48`);
  vtable any-block = 2 words `{dtor(0), *TypeInfo(1)}`. `EmitIfaceTypeInfo`
  (`ir/ir_ops_iface.bn:93`) loads slot-1, no record deref.
- `recoverPointer` (`gen_assert.bn:126`) returns the data slot as the target type
  + RefInc for `@T`. **Slice target needs a by-value load** (data slot holds
  `&slice`; target is the slice value) — the new branch.

### 8.5 Test infra + smoke map (all phases)
- Next free conformance number: **1074**. Modes (`scripts/modesets/all`, 10):
  `builder-comp`, `builder-comp-int`, `builder-comp-int-int`, `builder-comp-comp`,
  `builder-comp-comp-int`, `builder-comp-comp-comp`,
  `builder-comp_native_aa64-comp_native_aa64`,
  `builder-comp_native_x64-comp_native_x64`, `builder-comp_arm32_linux`,
  `builder-comp_arm32_baremetal`. Basic = `builder-comp`, `builder-comp-int`.
- One conformance test: `./conformance/run.sh <mode> <filter>` (substring;
  `--exact` for full). Unit: `./scripts/unittest/run.sh <mode> <pkgfilter>`.
  Hygiene: `./scripts/hygiene/run.sh`.
- Smoke map: `gen_iface.bn` → `pkg/binate/ir`; `emit_impls.bn` →
  `pkg/binate/codegen`; `mangle.bn` → `pkg/binate/mangle`; typeinfo →
  `pkg/binate/ir` + `pkg/binate/irdata`; `parse_assert.bn` → `pkg/binate/parser`.

## 9. Related follow-up: name-less MANAGED pointee into `@any` (crash → would-leak)

Surfaced by the Phase-0 adversarial review. Boxing a name-less **managed**
pointee into a managed iface value crashes today, same degenerate-box root cause
as the raw case:

```
var s @[]char = "hi"
var a @any = box(s)   // box(s) : @(@[]char) — managed-ptr, name-less pointee
// type-compare or scope-exit drop over `a` → SIGSEGV (exit 139, confirmed)
```

Unlike the raw case, Phase 0's shared-opaque-with-null-dtor fix would convert the
crash into a **memory leak**: `@any` RefIncs its data at construction
(`gen_iface.bn:256`) and drops via `emitManagedIfaceValueRefDec`, whose
null-slot-0 path falls through to plain `rt.Free` (`gen_util_refcount.bn:201`) —
freeing the outer cell but skipping the inner slice/backing cleanup. Trading a
detectable crash for a silent leak is the wrong direction (Memory-Management
rule). Same applies to a managed func-value pointee.

**DECIDED (2026-07-16): (a) — correct dtor.** A constructed managed value MUST
have its cleanup run (the "compiler never generates leaking code" invariant);
rejecting the construct (b) is an arbitrary carve-out, not a principled fix.
- **(a) Correct dtor** — emit / reference the boxed managed type's real drop
  (RefDec the pointee) in the any-block slot 0, instead of null.
- ~~(b) Reject at the checker~~ — declined (bans a well-typed construct to dodge
  the work).

**Implementation — folded into the feature, not a separate opaque-dtor patch.**
The real dtor a slice-typed managed box needs is exactly what Phase 2/3 produces
once slices get a structural `(slice, any)` ImplInfo: extend the boxing change to
run for the MANAGED iface too (drop the `dstTyp.Kind == TYP_INTERFACE_VALUE`
raw-only gate for slices) and carry the slice's real dtor in that ImplInfo. Open
sub-question to resolve in Phase 2: whether a managed-slice type already has a
CALLABLE dtor symbol the any-block can point at, or one must be synthesized
(managed-slice drop is currently inline logic — `emitManagedSliceRefDec`,
`gen_util_refcount.bn:224` — not a named function). Unnamed struct/array/func
managed boxes (outside the slice feature) keep the shared opaque record and still
need their own dtor answer — a smaller follow-up once the slice path proves the
mechanism.

Tracked in `claude-todo.md` alongside the (now-narrowed) MAJOR crash entry.

## 10. Open finding: element-`readonly` stripped at box sites — HIT-phase blocker (found 2026-07-16)

Surfaced by the adversarial review of the Phase-1+2 structural-identity chunk
(`7c600d41`). The structural mangler (`mangleTypeArg` / `namelessAnySrcName`)
correctly distinguishes an element-`readonly` slice (`@[]readonly int` → `Mr…` ≠
`@[]int` → `M…`) — but at a REAL box site the type reaching `wrapAsIfaceValue`
has already **dropped** the element-level `readonly`: `var r *[]readonly char =
s[0:len(s)]; box(&r)` and `var r2 *[]char; box(&r2)` BOTH emit the *same*
`__nameless_sN0_5_uint8` record (no `r` tag). Same for `@[]readonly int` vs
`@[]int`.

**Benign now** (Phase 1+2 is inert — both boxes MISS regardless), **but a
blocker for the HIT phase**: §5.1 ratified that element-`readonly` is a DISTINCT
identity (`@[]char` box must NOT match `case @[]readonly char:`). If box sites
can't preserve `readonly`, that distinctness is unimplementable — a `*[]char`
value would HIT `case *[]readonly char:` (or vice-versa).

The gap is **upstream** (checker / type-representation loses element-`readonly`
on a slice by the time it reaches the box site), not in the mangler. Before the
HIT phase (Phase 3/4) either: (a) fix the representation so a box site sees the
element-`readonly`, or (b) revisit the §5.1 ratified distinctness (e.g. collapse
readonly for slice-in-`any` identity — a spec change needing owner sign-off).
**Owner decision needed before Phase 3/4.** A conformance test asserting
`*[]readonly char` and `*[]char` boxes have distinct identities should land WITH
the fix (it would fail today, documenting the gap).
