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

Each phase is independently landable and keeps every mode green.

- **Phase 0 — Layer A (crash fix).** Name-less box → shared opaque
  `__ivt`+`__typeinfo`; `wrapAsIfaceValue` stops bailing for a name-less type.
  Conformance test: `switch`/`case *int:`/`default` **and** comma-ok `v.(*int)`
  over a slice-typed `*any` box → reaches `default` / `ok == false`, no crash;
  raw-slice box too (no-dtor path). Ship as its own MAJOR fix. *(No spec change.)*
- **Phase 1 — structural mangling primitive.** New mangler entry: a stable
  `__typeinfo.<…>` symbol derived from a slice `@types.Type`, encoding
  `{ managed | raw, element-readonly?, element-type }` **recursively**. The one
  genuinely new primitive. Unit tests: distinct spellings → distinct symbols;
  **alias collapse** (`char`≡`uint8` ⇒ `@[]char` and `@[]uint8` share a symbol);
  nested `@[]@[]char`.
- **Phase 2 — boxing keys on structural identity.** The concrete change is at the
  `:207` guard: instead of bailing when `receiverBaseTypeName` is empty, derive a
  **structural** name so the existing `:236`–`:250` synthesis path runs —
  producing a real `(slice, any)` ImplInfo + vtable and `registerTypeInfo`
  keyed on the structural symbol (replacing the opaque record **for slices only**;
  unnamed struct/array/func keep the opaque record). `@[]char` ≠ `*[]int` now. No
  box *representation* change is needed for the address (`&s`) form — its data slot
  already holds a pointer to the slice header (it passes `:196`). *(Boxing a
  **bare** multi-word slice value — the shape fmt args may take — is a separate
  question: it needs the value materialized so its address can be boxed, or it
  stays a checker error; deferred to Phase 5 / fmt adoption, not needed for the
  crash fix or for `&s`-form boxing.)*
- **Phase 3 — match + recovery.** `typeInfoSymFor` derives the structural symbol
  for a slice target; the identity compare Just Works. **Recovery detail to pin
  precisely** (the one under-specified spot): the box holds a pointer to the slice
  header, and recovery reads that pointer and copies the slice out **by value**
  (one more indirection than a pointer target; managed-slice copy acquires backing
  per `mem.copy`). Verify **exact-match** — a `@[]char` box must NOT match `case
  @[]readonly char:`. Independent of Phase 2's boxing change (boxing produces the
  record; this phase consumes it on a match), so the two land in either order once
  Phase 4 admits the target.
- **Phase 4 — parser/checker §11.12 relaxation.** Admit a slice target in
  `AssertTarget` (`parseAssertTargetName`) per the ratified production
  (`( "*" | "@" ) "[" "]" Type`); keep func/array/struct/`Self` rejected. Checker
  resolves the slice target and drives the structural compare. *(Design ratified —
  no longer gated.)*
- **Phase 5 — impl-complete flip + fmt adoption.** Once implemented and
  conformance-green, flip `iface.assert.slice` Draft→Provisional on the stability
  axis (the grammar is already in `binate.ebnf`), and let fmt's `...*any` fast-path
  use slice `case`s (one `case` per accepted string spelling — a library concern).
  This phase also settles **bare multi-word slice-value boxing** for fmt args (see
  Phase 2's note): materialize-and-box-the-address, or keep it a checker error.

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
