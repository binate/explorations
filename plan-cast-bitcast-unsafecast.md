# Plan: `cast` / `bit_cast` / `unsafe_cast` redesign

**✅ COMPLETE (all phases landed).** Phase 4 (gate `cast`) landed — binate `bd730a679`
+ docs (§8.5) `721629d`: `checkCastSafeSet` gates `cast` to the safe set (assignable
incl. interface widening/upcast; numeric incl. directional bool; named↔underlying
any-type incl. same-field structs; aggregate leaf-retype; const-fit), rejecting the
rest with an actionable diagnostic. **This closes the original MAJOR** —
`cast(@[]char, arr)` (array→managed-slice) is now a compile error, not silent
garbage/UAF. Type-param casts defer to instantiation; IR-gen lowers widening
(`wrapAsIfaceValue`) / upcast (`OP_IFACE_UPCAST`) and fails loud on narrowing /
mismatched-kind / different-element-size aggregates reached via a generic.
Adversarial review caught a blocker (iface-upcast ICE) + a major (generic slice OOB),
both fixed. Full `builder-comp` suite 2980/0 at land. The remaining `bool → {all
numerics}` widening the leaf-rule note mentioned is now IN (directional rule); nothing
outstanding. Per-phase history below.

**Status:** Phase 0 (spec) **landed** — docs `29449c1` (Ch.8 §8.5–8.7 rewrite +
new `unsafe_cast`, §15/§21/grammar/Annex A, readonly-drop stragglers swept, marked
Draft since impl is pending). Phase 1 (loosen `bit_cast`) **landed** — binate
`8c6fd014e`: `checkBitCastShapes` reduced to opaque-guard + same-proximal-size;
aggregate / by-address↔by-value-crossing `bit_cast` lowers via a refcount-neutral
IR-gen scratch round-trip (`emitBitCastViaMemory`), scalar/pointer/raw-slice↔raw-slice
stay direct `OP_BIT_CAST`; no backend changes; fail-loud guards on aggregate-reaches-
`EmitBitCast` and generic size-mismatch. Adversarial review caught the raw-slice↔scalar
crossing (same size only on 32-bit) slipping to the direct path — fixed via
`bitCastNeedsMemory`. Phase 2 (add `unsafe_cast`) **landed** — binate `ddfadfd0c`:
new `UNSAFE_CAST` token/parser/checker/IR-gen; since the ungated `cast` already
accepts the non-interface unsafe conversions, the new capability is interface
NARROWING (unchecked data-word extract, refcount-neutral, via `emitRecoveredValue`)
and WIDENING (`wrapAsIfaceValue`, RefInc); the non-interface lowering is factored
into a shared `genCastValueConversion`; interface↔interface is out of §8.7 scope
(use `x.(I)`). Fail-loud guards mirror `cast` (EmitCast never sees an iface; a
generic iface↔iface unsafe_cast panics). Adversarial review caught two one-line
holes (missing both-iface generic guard; `validateDimCasts` missing `unsafe_cast`)
— both fixed. Phase 3 (migrate outlawed `cast` uses) **landed** — binate `63825841f`
+ docs `e1226ad`: repo-wide enumeration (examples/docs had ZERO outlawed casts);
readonly-drop → `unsafe_cast` (in-tree BUILDER site → `bit_cast`), ptr↔int → `bit_cast`,
const-0→ptr → `nil`; struct/named-conversion sites STAY in `cast` per the decision to
**extend §8.5 named↔underlying to any type** (same-layout retype, safe — docs `e1226ad`).
Rule-ID re-vendor ripple fixed (Phase 0's `cast-drops`→`drop` and removed
`conv.cast.unchecked` surfaced when re-vendoring binate's `rule-ids.txt`; tests
`197`/`007`/`032`/`669` renamed + re-cited). No compiler code changed. Phase 4 not started.

**Phase 4 open items surfaced during Phase 3** (handle when gating `cast`):
- Interface **widening** (`cast(@I, @T)`) becomes ACCEPTED in Phase 4 (assignability
  case 7), so `check_builtin_test:TestCheckCastToInterfaceValueRejected` (and any
  cast-TO-iface error test) must FLIP from expect-error to accepted. Interface
  **narrowing** (`cast(@T, iv)`) stays rejected (→ `unsafe_cast` / `x.(T)`).
- The safe-set gate must ACCEPT: everything assignable (incl. iface widening +
  named↔underlying for ALL types now), numeric scalar, constant-typing, aggregate
  leaf-wise retype; and REJECT (→ named alternative) drop-element-`readonly`, `*T→@T`,
  ptr↔int, const-0→ptr, iface narrowing, non-same-underlying aggregate reinterprets.

Grounded in [`notes-cast-bitcast-unsafecast.md`](notes-cast-bitcast-unsafecast.md)
(the settled design + all decisions/rationale). This plan is the *execution* doc:
what changes, in what order, kept green and cherry-pickable at every step. Read
the notes first for the "why"; this doc assumes them and does not re-argue.

## Goal

Replace today's under-specified/inconsistent explicit-conversion surface (spec says
both `cast` and `bit_cast` are "unchecked at the type layer"; impl gates `bit_cast`
and leaves `cast` ungated, so a runtime `cast(@[]char, arr)` silently mis-lowers to
garbage/UAF — the open MAJOR) with three builtins on two orthogonal axes:

- **`cast`** — high-level, **SAFE-only** logical conversion. Gated: rejects anything
  not defined-and-non-corrupting, pointing the programmer at `unsafe_cast` / `bit_cast`
  / explicit construction. `cast ⊇ everything assignable`.
- **`unsafe_cast`** — a **superset of `cast`**; the same logical axis but additionally
  admits the *unverifiable* conversions (drop-`readonly`, `*T→@T`, unchecked interface
  narrowing, invariant-breaking scalar directions). "Possibly-unsafe cast."
- **`bit_cast`** — low-level byte reinterpret, **loosened** to the simplest rule:
  `sizeof(src) == sizeof(dst)` (proximal / top-level, not leaf-wise) → reinterpret;
  UB if source alignment doesn't meet the target's. Enables slice ↔ explicit-aggregate-form
  round-trips.

Closing state: the original MAJOR becomes a **compile error** with an actionable
diagnostic, and there is no `cast` that silently fabricates a managed value.

## Starting state (verified — anchors the diffs)

- **Spec.** §8.5 `cast` defines int/float/`readonly`-drop value conversions + `conv.cast.unchecked`
  ("typed non-constant operand NOT validated... programmer's responsibility") + `conv.cast.const-not-laundered`
  + `conv.cast.float-int-saturation`. §8.6 `bit_cast` = one rule `conv.bit-cast` ("like `cast`,
  unchecked at the type layer; different size or invariant violation is undefined"). §15.3
  restates both as `builtin.cast` / `builtin.bit-cast`. Grammar (§15) lists only `cast` / `bit_cast`.
- **Checker.** `check_builtin.bn:63-92` — CAST validates ONLY `requireSizedType(target)`,
  `checkCastConstFits` (constant→integer fit), and interface-value operand/target **rejection**
  (`:83-89`). **No source→target shape gate.** `check_builtin.bn:94-102` → `checkBitCastShapes`
  (`check_c_interop.bn:151-197`): raw-slice arm requires both raw slices + same element size +
  non-managed elements + non-opaque; `isBitCastRejectedAggregate` rejects
  MANAGED_SLICE/STRUCT/ARRAY/FUNC_VALUE/MANAGED_FUNC_VALUE/INTERFACE_VALUE/INTERFACE_VALUE_MANAGED
  (note: `@T` managed-ptr is NOT rejected → `bit_cast(@T,*T)` already works).
- **Tokens.** `token.bni:46-97` builtin enum (`CAST`, `BIT_CAST`, …, between `builtin_start`/`builtin_end`);
  `token.bn:50-51` names; `token.bn:153-179` `Lookup` (linear scan over the builtin range).
- **Codegen.** `OP_CAST`/`OP_BIT_CAST` are emitted in IR at `ir_ops_flow.bn:261`/`:272` and lowered
  per-backend: LLVM `codegen/emit_instr.bn:257`/`:260`; x64 `native/x64/x64_dispatch.bn:381`/`:278`;
  arm32 `native/arm32/arm32_dispatch.bn:279` (`emitCastOp`)/`:293` (`emitBitCast` — its own comment:
  "a same-size reinterpret (a plain MOV)"); aarch64 `native/aarch64/aarch64_dispatch.bn:330`/`:314`.
  For an unsafe shape (array→managed-slice, raw→managed-ptr, managed→managed-diff-pointee, struct↔array)
  the native path is a plain register `Mov` re-label → silent garbage / fabricated refptr → later
  `RefDec` of an arbitrary address = UAF. LLVM emits malformed IR (clang rejects — loud but downstream).
  **No backend fail-loud on an unhandled shape** (verify per backend during Phases 1/4; these citations
  are the sites the safety argument rests on).
- **In-tree `cast` migration scope is small:** of the ~2900 `cast(` uses in `pkg`+`cmd` (exact count
  re-established at Phase 3), a syntactic scan for managed/slice/array *targets* found only ~3, all test
  strings. That scan is by leading syntax only (misses named/struct/interface targets) — the real
  enumeration (Phase 3) resolves target *kinds*. The dangerous currently-compiling casts live mainly
  (if anywhere) in conformance/examples — enumerated repo-wide (below), not guessed.

## The target design (summary — full detail in the notes)

- **`bit_cast`**: `sizeof(src)==sizeof(dst)` proximal → byte reinterpret; UB on alignment mismatch;
  NOT element-wise. `@[]int32→@[]float32` **is** a `bit_cast`. Round-trips slice ↔ explicit struct form.
- **`cast`** = (⊇ assignability) ∪ (numeric scalar conversions) ∪ (named↔underlying) ∪ (constant-typing,
  fit-checked) ∪ (aggregate leaf-wise retype `@[]T→@[]S` **iff** `sizeof(S)==sizeof(T)` AND the element
  conversion is **total and bit-preserving** (`cast(S,T)` agrees with `bit_cast(S,T)`) **AND does not drop
  element-level `readonly`** — otherwise `@[]readonly char→@[]char` would sneak in as a plain `cast`, but
  dropping element-level `readonly` behind a shared handle is an aliasing hazard routed to `unsafe_cast`)
  ∪ (interface **widening**: concrete→iface and sub-iface→super-iface). Directional bool: `bool→int8` is `cast`
  (total+bit-preserving), `int8→bool` is not. **Transitivity invariant** per relation, used as a design
  filter (scalar relation and aggregate-retype relation are each transitive; they don't cross).
- **`unsafe_cast`** = `cast` ∪ {drop-`readonly` (`readonly T→T`), `*T→@T` (raw→managed ptr, asserts a
  −2W header), unchecked interface **narrowing** (`@Iface→@T`: extract the data word, no runtime check —
  unlike `x.(@T)` which panics), invariant-breaking scalar directions (`int8→bool`, …)}.
- **The 3-bucket sort:** compiler can DERIVE the target → `cast`; runtime can VERIFY → `x.(T)`
  (NOT a cast); neither, programmer asserts → `unsafe_cast`.
- **Not a cast:** `*[]T→@[]T` (because `@[]T→*[]T` is lossy — slices are value types — so it can't
  round-trip; **construct explicitly**). It is not a `bit_cast` either (`*[]T` is 2 words, `@[]T` is 4 —
  size mismatch). The 4-word `bit_cast` round-trip is the *separate* `@[]T ↔ struct{4 words}` reinterpret.

## Spec changes

1. **§8.5 `cast`** — rewrite. Keep the numeric/float-saturation/const-fit clauses. Replace the
   `conv.cast.unchecked` rule (cast is no longer "unchecked/programmer's responsibility") with the
   SAFE-only definition: the assignability lower bound, the explicit safe conversions, the aggregate
   leaf-wise retype rule (total + bit-preserving), interface widening, the directional-bool leaf rule,
   and the transitivity invariant. New/renamed rule-IDs (final names TBD in the plan-review): e.g.
   `conv.cast.safe` (the closed safe set), `conv.cast.aggregate-retype`, `conv.cast.iface-widen`.
   Keep `conv.cast.const-not-laundered`, `conv.cast.float-int-saturation`.
2. **§8.6 `bit_cast`** — replace `conv.bit-cast` with the same-proximal-size rule + UB-on-alignment;
   drop the "unchecked at the type layer" framing (it now has ONE checked precondition: equal proximal
   size) and reconcile with the loosened impl (no managed/aggregate rejection). **Keep the existing
   "violates a type's invariants is undefined" clause** — the proximal rule removes the raw-slice
   same-element-size guard, so `bit_cast(*[]int64, int32Slice)` (equal 2-word proximal size) now compiles
   and yields a `len` that over-counts the backing: still UB, must stay catalogued (see §21 below).
3. **New §8.x `unsafe_cast`** — the possibly-unsafe superset: `unsafe_cast ⊇ cast`, the enumerated
   unverifiable conversions, and unchecked interface narrowing (contrast `x.(T)`). Rule-ID prefix
   `conv.unsafe-cast`.
4. **§15** — add `builtin.unsafe-cast` (§15.3); update `builtin.cast` (drop "unchecked", say "SAFE, gated")
   and `builtin.bit-cast` (proximal-size rule). Grammar production list: add
   `"unsafe_cast" "(" Type "," Expression ")"`. Update the builtin summary table. **Also update the
   keyword-builtin COUNT** — `15-builtin-operations.md` says "eleven keyword-builtins" / "each of these
   eleven names" (§15 intro + `builtin.reserved`) → **twelve** — and add `unsafe_cast` to the
   **type-first-argument list** ("`make, make_slice, cast, bit_cast, sizeof, alignof`" → + `unsafe_cast`).
5. **Grammar** — add `unsafe_cast` to `docs/spec/binate.ebnf` (regenerate Annex A via
   `docs/scripts/gen-annex-a.py`).
6. **Cross-refs** — §8.3 (drop-element-level-`readonly` moves from `cast` to `unsafe_cast`), §8.4
   (raw→managed: the sanctioned explicit form is now `unsafe_cast`, not "never"), §21 behavior catalogue
   (UB entries: `bit_cast` **alignment mismatch**, `bit_cast` **type-invariant violation** — e.g. a
   same-proximal-size slice reinterpret with mismatched element size / over-counted `len`, and
   `unsafe_cast` **false assertion** — `*T→@T` with no real −2W header, unchecked iface narrow to the
   wrong concrete type).

**Spec-vs-impl note:** the drop-`readonly` reclassification (`cast`→`unsafe_cast`) is a semantic change
to §8.3/§8.5 that will ripple to any doc/code that says "cast drops readonly." Enumerate those sites
(grep the spec + notes) as part of the spec step; do not leave a dangling "use cast to drop readonly."

**Binate-side rule-ID / conformance ripple (from Phase 0's `type.readonly.cast-drops`→`type.readonly.drop`
rename — a Phase 4 landing item):** the binate repo vendors a snapshot of `docs/spec/rule-ids.txt` at
`scripts/spec-coverage/rule-ids.txt` and its hygiene `spec-coverage.sh` fails on a **DANGLING** citation
(a `.rules` sidecar naming a rule-ID absent from the vendored inventory). Docs now declare
`type.readonly.drop`; the vendored copy still has the old `type.readonly.cast-drops`, and conformance test
`conformance/spec/07-types/197_readonly_cast_drops_element` cites the old ID **and tests the OLD behavior**
(cast drops element-level `readonly`). Binate hygiene is **still green today** (it reads its own stale
vendored copy). When Phase 4 gates `cast`, that test's construct becomes a compile error, so this ripple
lands WITH Phase 4: (1) re-vendor `cp docs/spec/rule-ids.txt scripts/spec-coverage/`; (2) update/rename
test 197 to cite `type.readonly.drop`, switch its conversion from `cast` to `unsafe_cast`, and update the
`.bn` comment + dir name; (3) confirm no other `.rules` sidecar cites the old ID. Do NOT re-vendor before
updating the test, or hygiene goes DANGLING.

## Impl changes

- **A. Loosen `bit_cast`** (checker + codegen). Replace `checkBitCastShapes` with a single
  `sizeof(src)==sizeof(dst)` proximal check (keep the opaque-size guard: unknown size → clean error).
  Codegen: ensure `OP_BIT_CAST` lowers a same-size aggregate/managed operand as a pure byte reinterpret
  (by-address aggregates: retype the address, no data movement; verify no spurious RefInc/RefDec is
  emitted — a `bit_cast` takes no reference). Add a backend fail-loud for an unhandled `OP_BIT_CAST`
  shape so a future gap is loud, not silent-garbage.
- **B. Add `unsafe_cast`**:
  - Token: `UNSAFE_CAST` in the `token.bni` builtin enum, name in `token.bn:50`, (Lookup auto-covers it
    via the builtin-range scan). Parser: it parses exactly like `cast`/`bit_cast` (Type, Expression).
  - Checker: a new branch mirroring CAST but with the **superset** acceptance (cast's safe set PLUS the
    unverifiable set). Emits `OP_...` for each conversion class.
  - IR-gen: lower each unsafe class — `*T→@T` and drop-`readonly` are pointer/identity (word copy, no
    refcount op); interface **narrowing** extracts the data word (word 0 of the 2-word iface value);
    invariant-breaking scalar directions reuse the scalar lowerings. Add a fail-loud for unhandled shapes.
- **C. Tighten `cast`** (the breaking step — the gate). Add a source→target validation to the CAST
  branch that ACCEPTS exactly the safe set (assignability ∪ numeric ∪ named ∪ const-typing ∪
  aggregate-retype ∪ interface-widening ∪ directional-bool) and REJECTS everything else with a
  diagnostic that names the right alternative:
  - "use `unsafe_cast`" — drop element-level `readonly`, raw→managed ptr, iface-narrow, invariant scalar.
  - "use `bit_cast`" — pure same-proximal-size reinterpret (e.g. `@[]int32→@[]float32`).
  - "use a type assertion `x.(T)`" — checked iface narrowing.
  - **"construct explicitly (allocate + copy)"** — the under-determined constructions with no reinterpret:
    `*[]T→@[]T`, **and `[N]T`/array → `@[]T` managed-slice (the original MAJOR case:
    `cast(@[]char, arr)` — `sizeof(array)=N` bytes ≠ 4-word `@[]char`, so neither `bit_cast` nor
    `unsafe_cast` can do it; the user must allocate a managed backing and copy).** This bucket MUST exist
    or the flagship diagnostic dead-ends by pointing at a builtin that also rejects the conversion.
  - **Interface handling** (replacing the blanket `:83-89` rejection), itemized because it is correctness,
    not polish: (1) accept **widening** — concrete `@T`/`*T` → iface, and sub-iface → super-iface,
    consulting the interface-**extension** relation (§8.1 case 7) to distinguish direction when BOTH
    operand and target are iface values; (2) **reject** `cast` narrowing (`@Iface→@T`, super→sub) →
    "unsafe_cast (unchecked) or x.(T) (checked)"; (3) **reject a bare-value source** to a managed iface
    (`value → @Iface`) — that needs `box` (no implicit heap, §8.1 note); widening-via-cast requires an
    already-managed `@T` source (or a raw `*I` from a value via the §11.4 borrow, unchanged); (4) codegen
    for accepted widening is a **1→2-word construction** ({data, vtable}) and, for a managed `@Iface`
    target, takes a **RefInc** (this is a NEW codegen path — see testing).
  Add the aggregate leaf-wise retype acceptance + its codegen (same-size bit-preserving, non-readonly-drop
  element retype = a whole-value no-op copy). Add a backend fail-loud for an unhandled (now-narrowed)
  `OP_CAST` shape.
- **D. Diagnostics polish** — one shared helper mapping (srcKind, dstKind) → the actionable message,
  so `cast` and `unsafe_cast` give consistent guidance. Must cover every rejected `(srcKind, dstKind)`
  pair — including `(ARRAY, MANAGED_SLICE)` — with a message that points at an alternative that actually
  performs the conversion (no dead-ends).

## BUILDER sequencing constraint (critical — read before ordering the work)

`unsafe_cast` is a NEW keyword. The pinned BUILDER bnc (`bnc-0.0.1`-era) predates it and will
**fail to lex** any source that contains the literal `unsafe_cast`. Therefore:

- **Defining** `unsafe_cast` (enum entry, Lookup, checker, IR-gen) is BUILDER-safe — BUILDER compiles
  that ordinary code fine; it just doesn't recognize the keyword in *user* source. So step B is safe.
- **Using** `unsafe_cast` anywhere in **cmd/bnc's BUILDER-compiled tree** (the transitive imports listed
  in CLAUDE.md) breaks the gen1 build until BUILDER is bumped to a version that knows the keyword.
- Consequence for migration: any now-illegal `cast` **inside bnc's BUILDER tree** must migrate to
  `bit_cast` / `x.(T)` / explicit construction (all BUILDER-known), **not** `unsafe_cast` — unless/until
  a BUILDER bump lands first. Outside the tree (bnlint, vm, interp, rt built-by-bnc, conformance,
  examples) `unsafe_cast` is fine immediately.
- The current in-tree offenders are ~nil (the 3 grep hits are test strings + one interface case), so a
  BUILDER bump is likely **not** required for this plan. But we verify by enumeration (below) and, if any
  in-tree `cast` genuinely needs `unsafe_cast` semantics, we surface it and either restructure to
  `bit_cast` or schedule a BUILDER bump — a user decision, not a silent workaround.
- Also: tightening `cast` (step C) is enforced by the **new** bnc (gen2+), not BUILDER (gen1 uses
  BUILDER's own lenient checker to compile cmd/bnc source). So a now-illegal in-tree `cast` fails at
  **gen2 / conformance**, not gen1 — migration (step 3) must land before the gate (step C) to keep gen2 green.

## Execution phases (each independently green + cherry-pickable)

Ordering follows the user's sketch (add `unsafe_cast`; migrate outlawed `cast` uses; then change
`cast`/`bit_cast`, in steps, `bit_cast` can be earlier), refined for always-green + the BUILDER constraint:

1. **Phase 0 — spec.** Land the §8.5/§8.6/§8.x/§15/grammar changes (docs repo). Spec-only, no code
   behavior change yet; gets the design reviewed-as-written before code. (Minimal adversarial review here.)
2. **Phase 1 — loosen `bit_cast`** (impl A). Additive at the *checker* (accepts more), but NOT free:
   the newly-accepted aggregate/managed shapes reach codegen for the first time, where the current path
   falls through to a silent `Mov` re-label. **The `OP_BIT_CAST` fail-loud AND the correct no-op
   reinterpret lowering MUST land in the same commit as the checker loosening** — the fail-loud is the
   safety net; splitting it out (even one commit) manufactures fresh silent miscompiles, the exact class
   this redesign kills. Independent of `cast`/`unsafe_cast`; safe to do first only as one atomic change.
3. **Phase 2 — add `unsafe_cast`** (impl B). Additive; nothing uses it yet. BUILDER-safe (define-only).
   Full checker + IR-gen + tests for every unsafe class.
4. **Phase 3 — enumerate + migrate outlawed `cast` uses.** Build the site list from a **repo-wide,
   deliberately over-broad grep** across ALL repos (binate `pkg`/`cmd`, `conformance/`, the `examples`
   repo, `docs` snippets), then **resolve each cast's target/source KIND** (not just leading syntax — a
   `cast(NamedSlice, x)` / `cast(SomeStruct, arr)` won't match `cast(@`/`cast([`; match by resolved type)
   — casts whose (src,dst) fall outside the new safe set (aggregate/managed/raw→managed/iface-narrow/
   drop-element-readonly/array→managed-slice). Triage each to its replacement (`bit_cast` / `unsafe_cast` /
   `x.(T)` / allocate+copy construct), respecting the BUILDER rule for in-tree sites. State the dirs the
   grep covered. Land migrations while `cast` is still lenient (they compile either way), so Phase 4 lands
   on a clean tree.
5. **Phase 4 — tighten `cast`** (impl C+D). The breaking gate + new acceptances (iface widening,
   aggregate retype). Lands after Phase 3 so the tree/conformance is already offender-free. Closes the
   MAJOR (add the conformance regression test: `cast(@[]char, runtimeReadonlyCharArray)` is now a
   compile error; the interface widen/narrow paths; `@[]int8→@[]uint8` retype; `bool→int8` vs the
   `int8→bool` rejection).
6. **Phase 5 (conditional) — BUILDER bump.** Only if an in-tree `cast` genuinely needs `unsafe_cast`
   (surfaced in Phase 3). User decision.
7. **Follow-up (out of scope here, per user "orthogonal"): `bool↔int` widening** (`bool→{all ints,
   floats}`). Filed as a separate todo, not built in this plan.

**Ordering dependencies (the real invariant, not just "3 before 4"):** Phase 3's migrations *emit*
`bit_cast` and `unsafe_cast`, so **Phase 3 depends on both Phase 1 (loosened `bit_cast` must already
accept the aggregate targets) and Phase 2 (`unsafe_cast` must exist)**; **Phase 4 depends on Phase 3**
(migrate before you gate). Phases 1 and 2 are mutually order-independent (either first). The default
0→1→2→3→4 order satisfies every dependency; a reorder must preserve 1<3, 2<3, 3<4. Phase 0 (spec) first
so the contract is reviewed before code, but late spec edits can interleave with Phase 1/2 if needed.

## Testing

- **Per builtin, per conversion class:** positive (accepted, correct runtime bytes) + negative (rejected
  with the *right* diagnostic pointing to the correct alternative). Checker unit tests
  (`check_builtin_test.bn`) + conformance for runtime behavior.
- **The MAJOR regression:** `cast(@[]char, arr)` where `arr : [N]readonly char` runtime → compile error
  (conformance negative test; the class the whole redesign exists to close) — and the diagnostic must name
  the **"allocate + copy"** alternative, NOT `bit_cast`/`unsafe_cast` (which also reject it — no dead-end).
- **Transitivity property:** for the scalar-cast relation and (separately) the aggregate-retype relation,
  a small closure check that `cast(T,s) ∧ cast(R,t) ⟹ cast(R,s)` holds on representative types.
- **bool directionality:** `bool→int8` accepted (and value-correct: 0/1), `int8→bool` rejected → must use
  `unsafe_cast` (which is then value-correct/defined).
- **Interface widen/narrow:** `cast(@Iface, m)` where `m : @T` and sub→super widening accepted; a
  **bare-value** source to a managed iface rejected (needs `box`); `@any→@T` via `cast` rejected
  (→ `unsafe_cast` unchecked, or `x.(@T)` checked); `unsafe_cast` narrowing extracts the data word;
  `x.(@T)` panics on mismatch (unchanged — the checked path, per the type-assertion rule in §13/§11).
- **`bit_cast` round-trips:** `@[]T` ↔ its 4-word explicit struct form; `*[]T` ↔ 2-word form; `*T` ↔ `@T`;
  int ↔ float bits; and a size-mismatch `bit_cast` is a compile error.
- **Refcount balance:** `bit_cast` / `unsafe_cast` of managed operands take **no** reference (no spurious
  RefInc/RefDec) — a live-object-count net-zero test, following the existing refcount-balance pattern.
  Conversely, `cast(@Iface, @T)` **widening** DOES construct a managed iface value and takes a **RefInc**
  (the new codegen path, Impl C(4)) — a matched-balance test that the constructed `@Iface` and its
  eventual `RefDec` account for exactly one reference (no leak, no double-free).
- **Fail-loud:** an unhandled `OP_CAST`/`OP_BIT_CAST` shape aborts codegen loudly (a targeted unit test),
  never emits silent garbage.
- Run modes: `builder-comp` (compiled) + `builder-comp-int` (VM) for conformance; unit tests for every
  changed package (checker: `pkg/binate/types`; codegen: `pkg/binate/codegen` + `pkg/binate/native/*`;
  IR: `pkg/binate/ir`).

## Open decisions to finalize while landing (call out; get user calls)

- **Exact `unsafe_cast` conversion list + precise diagnostic wording** (which cases say `unsafe_cast`
  vs `bit_cast` vs `x.(T)` vs "construct"). Draft in Phase 0's spec; refine as code lands.
- **`bool↔int` scope:** confirm it's a deliberate follow-up (leaning yes, per "orthogonal").
- **Whether a BUILDER bump is needed** (Phase 5) — determined by the Phase 3 enumeration.
- **rule-ID final names** for the new §8.5/§8.x rules.
- **The §8.4 Provisional raw→managed/borrow-restriction proposal** interaction: this redesign makes
  `unsafe_cast` the explicit raw→managed form; check it doesn't conflict with
  `proposal-restrict-implicit-raw-conversion` (they're complementary — one restricts the *implicit*
  borrow, the other names the *explicit* raw→managed — but confirm the wording lines up).

## Risks

1. **Migration completeness (silent incompleteness).** A missed outlawed `cast` outside the grepped dirs
   lands as a gen2/conformance break later. Mitigation: over-broad repo-wide grep across ALL repos, state
   coverage, triage down (per the CLAUDE.md sweep rule).
2. **BUILDER breakage** from an in-tree `unsafe_cast`. Mitigation: the in-tree rule above + verify by
   enumeration; restructure to `bit_cast` or bump BUILDER (user decision) rather than working around.
3. **Codegen for newly-allowed aggregate `bit_cast`** — must be a true no-op reinterpret with NO
   refcount op; a stray RefInc/RefDec would leak or double-free. Mitigation: refcount-balance tests +
   fail-loud + adversarial review focused on the ownership walk.
4. **Interface-value gate removal** — dropping the blanket `:83-89` rejection must not re-open the
   malformed-IR path it was added for (bug: `%BnIfaceValue` used as a 1-word pointer). Widening must
   lower to a proper 1→2-word construction; narrowing via `unsafe_cast` must lower to a data-word
   extract. Mitigation: targeted IR/codegen tests on both directions before removing the gate.
5. **Spec/impl drift on drop-`readonly`** (moves `cast`→`unsafe_cast`) — enumerate every "cast drops
   readonly" site (spec + code comments + notes) so none dangles.
