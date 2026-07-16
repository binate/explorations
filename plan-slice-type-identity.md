# Plan: structural type-identity for slices (`proposal-slice-type-identity`)

Status: **DRAFT / proposed** — spec updated as Draft (pending ratification), impl
not started. Owner-decision points flagged in §5.

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
**structurally invalid** interface value. `wrapAsIfaceValue → ensureAnyImplInfo /
findImplVtableName` can't synthesize a `(slice, any)` vtable (a slice has no name;
`receiverBaseTypeName` returns empty) and hits `return nil`, so the "box" is a
bare data pointer where a 2-word `%BnIfaceValue` is expected. Any concrete
type-compare over that box (`case *int:`, `x.(*int)`) then loads `vtable[1]` off a
garbage word → SIGSEGV. A well-typed program must never segfault, so this is a
MAJOR in its own right, fixable **before and independent of** the feature.

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
- **Phase 2 — boxing keys on structural identity.** Slice boxing synthesizes a
  real `(slice, any)` ImplInfo + vtable and `registerTypeInfo`s on the structural
  symbol (replacing the opaque record **for slices only**; unnamed
  struct/array/func keep the opaque record). `@[]char` ≠ `*[]int` now.
- **Phase 3 — match + recovery.** `typeInfoSymFor` derives the structural symbol
  for a slice target; the identity compare Just Works. **Recovery detail to pin
  precisely** (the one under-specified spot): a slice is multi-word, so the box
  holds a **pointer to the slice header**; recovery reads that pointer and copies
  the slice out **by value** (one more indirection than a pointer target;
  managed-slice copy acquires backing per `mem.copy`). Verify **exact-match** — a
  `@[]char` box must NOT match `case @[]readonly char:`.
- **Phase 4 — parser/checker §11.12 relaxation.** Admit a slice target in
  `AssertTarget` (`parseAssertTargetName`); keep func/array/struct/`Self`
  rejected. Checker resolves the slice target and drives the structural compare.
  *(Gated on the §5(1) ratification decision.)*
- **Phase 5 — spec flip + fmt adoption.** On ratification: flip
  `iface.assert.slice` Draft→Provisional, add the slice-target production to the
  canonical `binate.ebnf`, and let fmt's `...*any` fast-path use slice `case`s
  (one `case` per accepted string spelling — a library concern).

## 5. Open decisions (need owner sign-off)

1. **Exact-match distinctness (semantics — owner's call).** Treat
   element-`readonly` and managed-vs-raw as **distinct** identities (⇒ `@[]char`
   box does not match `case @[]readonly char:`). This is the **sound** choice —
   collapsing them would silently drop/add `readonly` or confuse the 2-word vs
   4-word representations — but it is a language-semantics decision and it drives
   the fmt "one case per spelling" wart. **Blocks Phase 4/5.**
2. **Grammar form.** `AssertTarget = ( [ "*" | "@" ] [ "readonly" ] TypeName ) |
   SliceType`. Confirm the slice target is value-recovery-only (the leading
   `@`/`*` is the slice's managed/raw marker, not a recovery-kind prefix).
3. **Sequencing.** Land Phase 0 (crash fix) **now** as an independent MAJOR
   regardless of fmt timing, then Phases 1–5 when fmt work starts — or do B in one
   shot if fmt is imminent (avoids touching boxing/match twice). Owner's call.

## 6. Non-goals

- Structural identity for unnamed **struct / array / function** targets — they
  keep the shared opaque record (well-formed, un-nameable) until separately
  requested. Layer A already makes their boxes safe.
- Any **structural/kind** ("any char slice regardless of managed/readonly")
  matching primitive — that is a much larger type-switch question, out of scope.
- A named `string` type or a `Stringer`-style constraint for char-slices — both
  ruled out by prior decisions; this plan is the alternative they imply.

## 7. Spec status

Landed as Draft (this change): §11.12 `iface.assert.slice` (+ carve-out in
`iface.assert`, caveat in the "Implemented" note), and the §7.13.14
`type.layout.typeinfo` name-less-types note (Layer A baseline + Layer B upgrade).
`rule-ids.txt` regenerated. Flip to Provisional + `binate.ebnf` grammar happen at
Phase 5 on ratification.
