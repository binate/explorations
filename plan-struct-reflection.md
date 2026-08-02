# Plan: struct reflection for `fmt` `%v` (field-table RTTI + runtime rendering)

Status: **P1 (`133db88d`) + P2 (`2ef97634`) + P3 (`6a8b7f8f`) + P4a `%+v` (`f7a3ec06`)
LANDED; P4b (array/slice/pointer fields + cycle guard) + anon-struct mangler
remaining.** Design settled: the §0
adversarial review resolved decisions 1/2/3/5/6, and the user ratified 4 & 7 on
2026-07-31 (see §5). P1 (field-table RTTI + reflect API) landed 2026-08-01 —
conformance `1150`, plus a prereq raw-ptr-to-struct selector fix (`4b281158`,
conformance `1151`); a pre-land adversarial review (`wf_fe852b03-8b7`) caught the
missing ILP32 arm32-linux `.expected` and a bare-form intercept hazard, both fixed
before landing. Written 2026-07-30 from a recon sweep of the RTTI / reflect / types /
fmt substrate (workflow `wf_69ec9265-9c4`); revised 2026-07-31 per the design review
(see §0). Spec §7.13.14 update (docs repo) still pending.

## 0. Design-review outcome & folded-in revisions (2026-07-31)

Verdict: **SOUND WITH REVISIONS.** The two load-bearing assumptions were
code-validated and HOLD: (a) the weak record can grow 5→8 words — words 0–4 keep
their offsets and there is NO hard-coded `5`-word/stride assumption anywhere (only
doc comments); (b) read-bytes-per-kind IS mode-portable — the recon's worry that
"the VM vtable word is a table index, not a pointer" does NOT extend to DATA words:
a boxed struct's data word is a genuine host address (`vm_exec_helpers.bn:321-335`
`BC_LOAD64`/`BC_STACK_ALLOC` on real `*uint8`), so `bit_cast(*int, dataPtr+off)[0]`
lowers to a real load in both modes. Read-bytes-per-kind is therefore also strictly
SAFER than a re-box (it is a pure borrow — see §7). Nothing is a dead end.

Five issues must be designed out; all are folded into the sections below:

1. **(MAJOR, §3.4) ODR corruption — a struct's own field table MUST be
   unconditional & TU-invariant.** The record is weak/ODR-coalesced; today words
   0–4 are TU-invariant so all copies are byte-identical. If word6/word7 (field
   count / fields-ptr) depend on whether *that TU* reflected T, a TU that only
   *asserts* T could emit an empty-field copy that wins coalescing → reflection
   silently returns zero fields, non-deterministically by link order. FIX: for a
   struct kind, word6/word7 are a **pure function of the type definition** — every
   TU that emits T's record emits its full field table. The "render opaque" option
   is admissible ONLY for a *nested field type you decline to force-emit*, NEVER for
   a struct's own record. (Resolves open-decision-3's ambiguity.)

2. **(MAJOR, §4) Determinism — `CollectTypeInfoDescs` is called ~5× (LLVM
   `emitTypeInfos` + `collectDefinedDataSyms`, 3 native backends, VM) and its output
   must be byte-identical every time,** or the "defined" set and "emitted" set
   diverge → a force-emitted `__typeinfo.<Inner>` gets both a weak definition and an
   `external` decl → LLVM redefinition error. FIX: run the transitive force-emit as
   a **one-time pre-pass that populates `m.TypeInfos` before any backend runs**,
   keeping `CollectTypeInfoDescs` pure; add a check that the emitted-set == the
   defined-set.

3. **(MAJOR, P3 blocker, §4) VM cross-record symref.** `materializeTypeInfos`
   lowers one type per batch and resolves `DT_SYMREF` only against the current
   batch's names (`lower_pkg_descriptor.bn:99-135`), so a field table's
   `field-typeinfo-ptr → __typeinfo.<Inner>` (a *different* batch) resolves to null.
   Harmless at P1/P2 (the word is unused until recursion) but a hard P3 blocker; the
   plan's "resolve via lookupDataSymAddr like SatEntry" was wrong (SatEntry resolves
   AFTER all typeinfos exist; field tables are built inside materialization). FIX:
   restructure `materializeTypeInfos` into **two phases — allocate+register every
   record & field-blob first, then back-patch each `field-typeinfo-ptr` via
   `lookupDataSymAddr`** (cycle-proof). Named as a P3 VM slice.

4. **(MAJOR, §3.2/§3.3, bites P2) The data-word accessor is a real new primitive.**
   read-bytes-per-kind avoids box-*from*-(addr,TypeInfo) but still needs to obtain
   the operand's DATA word; `OP_IFACE_TYPEINFO` extracts vtable-slot-1 only, and fmt
   (pure Binate) can't emit `EmitExtract(iv, 0)`. FIX: add a **`reflect.DataOf(x
   *any) *uint8` op mirroring `OP_IFACE_TYPEINFO`** (preferred over blessing fmt to
   `bit_cast` into iface internals, so the ABI stays encapsulated). Add to P2 scope.

5. **(MAJOR/MINOR, §3.3) `reflect.TypeOf` is not a "thin wrapper".** The raw 8-word
   record layout ≠ the `TypeInfo` struct, so `TypeOf` must marshal (walk
   `__typefields.<T>`, build `FieldInfo`s). FIX: **overlay the `reflect.TypeInfo` /
   `FieldInfo` structs directly on the raw record/entry layout** so `TypeOf` is a
   cast and the byte contract lives in ONE place (also read by fmt's own helper).
   Re-budget P1 for the marshaler if overlay proves impractical.

Minors folded in: force-emit follows **only by-value struct fields** in P1–P3 (so
the closure terminates without a visited-set; pointer/slice fields render opaque
until P4, which must then carry a visited-set for cycles); each native backend
(x64/arm32/aarch64) must `symPrefixed` every new field-blob / field-typeinfo /
per-field-name symbol (mechanical but real, mirror the `NameSym` precedent); the
KIND enum is a cross-artifact contract (compiler emits, stdlib+VM read) so its
numeric values are **pinned in spec §7.13.14** like `iropcode`; kind derivation
peels `TYP_READONLY` (a `readonly int` field must kind as int); `char`==`uint8`
so a `*[]char` byte buffer renders as text and a bare `char`/`uint8` field as a
number (matches Go `byte`); `&s` and `s` are indistinguishable (`%v` prints `{…}`,
never Go's `&{…}`); validate the in-VM `int↔float64` reinterpret at P2; the "5-word"
doc/spec sites to update are `irdata.bni:128`, `irdata/data_typeinfo.bn:13`,
`vm_exec_iface.bn:303`, and spec §7.13.14. Refcount: read-bytes-per-kind is a
**pure borrow** (reads a `@[]char`/`@T` field through the still-live parent, builds
raw views like writeArg's existing cases, never RefInc/RefDec/free) — §7 states
this as an explicit invariant the helper must preserve.

## 1. Goal

`fmt.Printf("%v", &s)` (and `%s`, `Print`/`Sprint`) on a struct that has no
`String()` currently prints `%!?(unknown)`. Make it render the struct's fields —
Go-style `{3 4}` for `%v` (and, later, `{x:3 y:4}` for `%+v`) — recursing into
nested struct fields. This is the last fmt layer (custom `Stringer` formatting
landed `04e0b3d7`; the Print/Printf verb surface is complete). It reuses the
existing per-type RTTI (`__typeinfo`) rather than inventing a parallel mechanism.

Non-goals (for the first cut; revisit later): a full Go `reflect`-style public API,
formatting of maps (Binate has none), `%#v` Go-syntax output, and pointer-cycle
rendering beyond a simple depth/seen guard.

## 2. Verified substrate (recon + empirical)

- **Every struct boxed into `*any` already gets a `__typeinfo.<T>` record.**
  Verified: an impl-less `type Plain struct{x,y int}` boxed into `*any` emits
  `@__typeinfo.bn_…_Plain = {dtor=null, size=16, align=8, name-ptr, name-len}` and
  an any-block vtable whose slot 1 points at it. So the top operand fmt receives
  always carries a resolvable `*TypeInfo` (via the iface value's vtable slot 1,
  `iropcode` any-block layout; read by `OP_IFACE_TYPEINFO`).
- **The `__typeinfo` record is a FIXED 5-word record** (word = target int size):
  `[dtor, size, align, name-ptr, name-len]`. Its BYTES are laid in exactly one
  place — `pkg/binate/irdata/data_typeinfo.bn` `BuildTypeInfo` — and lowered by
  five backends (LLVM `codegen/emit_impls.bn`, native `x64/arm32/aarch64
  *_typeinfo.bn`, and the VM `vm/lower_typeinfo.bn`, which materializes its own
  copy from the same `BuildTypeInfo` blob). It is weak/ODR-coalesced: exactly one
  per concrete type program-wide, keyed by `mangle.TypeInfoName`. The record is
  explicitly designed to grow (`emit_impls.bn` calls slot payloads a "placeholder
  for now"; `reflect.bni` says "richer type metadata (TypeInfo) lands in a later
  phase").
- **Field metadata is fully available at codegen** in `pkg/types`: a struct
  `@Type` has `Fields @[]@Field{Name, Type}` (declaration order), `FieldOffset(i)`,
  `StructLayout()`, and `QualifiedTypeName()`. Layout is target-parameterized
  (`SetTarget` locks int/ptr width) and must be read at CODEGEN (the shared
  `RecvTyp` is populated by a later pass), which is exactly where
  `ir.CollectTypeInfoDescs` already reads `RecvTyp.SizeOf/AlignOf/QualifiedTypeName`.
- **The IR/backend split is load-bearing**: record bytes live only in `irdata`
  (an `@Module`-free leaf); the "which types, read the type facts" half lives in
  `pkg/binate/ir` (`CollectTypeInfoDescs`); backends only symbol-prefix and lower
  `DataGlobal`s. Any field-table bytes MUST be added in `irdata`, never per-backend.
- **fmt hook**: `writeArg` (`fmt.bn`) default branch — after the `tryStringer`
  hit (so a struct with its own `String()` still wins) and before
  `w.Write("%!?(unknown)")`. Everything funnels here (`%v`, `%s`-of-non-string,
  error-verb value rendering), so one hook covers all.
- **`reflect.Package`** descriptor (functions/globals/vtables/satentries;
  append-only; `STATIC_REFCOUNT` immortal sentinel) is the template for any new
  reflective table, but see §4 — we prefer extending the per-type record over a
  new Package table.

## 3. Design

### 3.1 RTTI: add a kind discriminator + a field table

Extend the per-type record (and update spec §7.13.14, dropping "fixed 5-word"):

```
word0  dtor handle          (unchanged)
word1  size                 (unchanged)
word2  align                (unchanged)
word3  name ptr             (unchanged)
word4  name len             (unchanged)
word5  KIND                 (NEW: enum — int/uint/float/bool, string, struct,
                             array, slice, ptr, iface, func, other)
word6  fields ptr           (NEW: &__typefields.<T>, or null)
word7  field count          (NEW: 0 for non-structs)
```

Words 6–7 are deliberately ordered `{ptr, len}` so a `*[]FieldInfo` raw slice
overlays them directly (§3.3). The KIND at word5 is a *coarse* discriminator; a
field's exact width comes from the per-field `size` (below), so fmt can load a
scalar without dereferencing anything.

`__typefields.<T>` is a new TU-local rodata/static blob (following the
`__typeinfo_name.<T>` name-blob precedent — reloc-safe, emitted alongside the weak
record), an array of `FieldEntry` (6 words each):

```
FieldEntry { name-ptr, name-len, offset, field-kind, size, field-typeinfo-ptr }
```

Field names are concatenated into ONE TU-local `__typenames.<T>` blob; each
`name-ptr` is a symref to it with the field's byte offset as the addend (so a
struct adds exactly two new symbols — `__typefields.<T>` + `__typenames.<T>` —
regardless of field count, not one blob per field). `size` = the field type's
`SizeOf()`, so fmt loads a scalar of the right width from `field-kind` + `size`
alone. `field-typeinfo-ptr` = `&__typeinfo.<fieldType>` when the field's type has
its own record (enables recursion into nested structs); **null until P3** — P1/P2
leave it null (see §6), so there are NO cross-record references and every backend
stays transparent. Words 0–4 keep their offsets, so every existing assertion reader
(which reads words 0–4 by fixed offset and compares the record ADDRESS for
identity) is unaffected — the extra trailing words are inert to them.

**Why extend the record, not add a `reflect.Package.Types` table:** the value →
vtable slot-1 → `*TypeInfo` path already exists at runtime; hanging fields off the
record gives `reflect.TypeOf(x).Fields` directly with no new Package table, no
per-package aggregation, and no append-only-layout risk. (The Package-table
alternative stays open if we later want to enumerate a package's types.)

### 3.2 Rendering primitive: read-bytes-per-kind (NOT a runtime re-box)

The naive design ("re-box each field value into a fresh `*any` and recurse into
`writeArg`") needs a **runtime box-from-(addr, TypeInfo)** constructor — genuinely
new and hard (a `*any`'s vtable word isn't reconstructible from a `*TypeInfo`
alone; the VM's vtable word is a table index, not a pointer). We avoid it.

Instead, fmt renders a struct by walking its field table and, per field, reading
the raw bytes at `dataPtr + offset` **interpreted by `field-kind`**:

- int/uint/bool/float → load the scalar (by width) and format via the existing
  strconv paths;
- string (char-slice) → load the slice header (2 or 4 words) and write the bytes;
- struct → recurse using the field's `*TypeInfo` (its own field table);
- ptr/iface/slice/array → later phases (render opaque or `[...]` initially).

`field-kind` tells fmt how to interpret the bytes; the field's `*TypeInfo` gives
size (for stride) and, for structs, the nested field table. Reading a field is
pointer arithmetic (`bit_cast`/offset) + a typed load — contained in a small
`reflect`/fmt helper, kept in one place. Recursion terminates on scalar leaves.

### 3.3 reflect API surface (`pkg/builtins/reflect`)

```
// Overlaid on the 8-word __typeinfo record (field offsets == word offsets):
type TypeInfo struct {
    Dtor   *uint8            // word 0
    Size   int               // word 1
    Align  int               // word 2
    Name   *[]readonly char  // words 3-4 ({ptr,len})
    Kind   int               // word 5
    Fields *[]FieldInfo      // words 6-7 ({ptr,len}) -> the __typefields blob
}
// Overlaid on the 6-word FieldEntry:
type FieldInfo struct {
    Name   *[]readonly char  // words 0-1
    Offset int               // word 2
    Kind   int               // word 3
    Size   int               // word 4
    Type   *uint8            // word 5 (&__typeinfo.<fieldType>; null until P3)
}
func TypeOf(x *any) *TypeInfo   // -> EmitBitCast(EmitIfaceTypeInfo(x))
func DataOf(x *any) *uint8      // -> EmitExtract(x, IfaceValueDataIndex())
```

`TypeInfo`/`FieldInfo` are **overlaid directly on the raw record / field-entry byte
layout** (§0.5), so `TypeOf` is a cast rather than a marshaler and the byte contract
lives in one place; `reflect.TypeOf(x).Fields` is a real `*[]FieldInfo` a caller can
index. Both are compiler intrinsics intercepted by qualified name in `gen_call.bn`
(the `_call_dtor` precedent): `TypeOf` bit-casts `EmitIfaceTypeInfo`'s `*uint8`
result to `*TypeInfo`; `DataOf` extracts the iface value's data word
(`IfaceValueDataIndex()==0`) — a plain load, backend-uniform, so **no new op is
needed** (the §0.4 "DataOf op" is subsumed by `EmitExtract`). Their `.bni` decls are
type-check shapes only (no impl body, like `_call_dtor`). (A general "read field
value into an `*any`" is deferred with the re-box question.)

### 3.4 Coverage — nested field types

The TOP struct always has a record (§2). A NESTED struct field's type has a
`__typeinfo` only if that type is itself boxed somewhere. To make recursion
reliable, when the gather emits a struct's field table it must **transitively
force-emit `__typeinfo` (with fields) for every struct type reachable as a field**
(a bounded closure over field types). Scalars/strings need only a kind tag (no
record). Open decision (§5): transitive force-emit vs. render an unrecorded nested
struct opaquely.

## 4. Cross-mode (VM) and IR/backend split

- **Bytes in `irdata`**: extend `BuildTypeInfo` (+ a `buildFieldTable`/blob) in
  `data_typeinfo.bn`; add the new fields to `TypeInfoDesc` (`irdata.bni`).
- **Gather in `ir`**: `CollectTypeInfoDescs` (`ir/data_typeinfo.bn`) — for a
  struct `RecvTyp`, iterate `Fields`, compute `FieldOffset(i)`, derive each field's
  kind and its `TypeInfoName` (for the field-typeinfo-ptr), and drive the
  transitive force-emit. `@Module`-side, stays out of the leaf.
- **Backends**: LLVM + native `*_typeinfo.bn` and VM `lower_typeinfo.bn` already
  loop `CollectTypeInfoDescs → BuildTypeInfo → lower`; the field blob rides the
  same path. Field-typeinfo references must be **name-keyed** cross-mode (VM/native
  record addresses differ) and resolved via the existing data-symbol table
  (`lookupDataSymAddr`), exactly as `SatEntryInfo.TypeSym` does today.

## 5. Decisions

All settled — the §0 design review resolved 1/2/3/5/6, and the user ratified 4 & 7
on 2026-07-31. Proceed on all of these.

1. **Record shape** — extend `__typeinfo` to 8 words (validated offset-safe; no
   `Package.Types` table).
2. **Rendering** — read-bytes-per-kind (validated feasible cross-mode AND a pure
   borrow; no runtime re-box), PLUS the small `reflect.DataOf` data-word op (§0.4).
3. **Nested coverage** — transitively force-emit records for by-value struct field
   types; a struct's OWN record always carries its full field table (§0.1);
   "opaque" applies only to a declined nested type.
4. **`%v` output format** *(user-ratified 2026-07-31)* — match Go: `%v` = `{3 4}`
   (values only), `%+v` = `{x:3 y:4}` (field names). `%#v` (Go-syntax) stays a
   non-goal. The renderer keys the `name:` prefix off `Spec.plus` (already parsed).
5. **Kinds P1/P2** — scalars + string + nested struct first; arrays/slices/
   pointers/ifaces render opaque until P4.
6. **Cycles** — no risk until P4 (pointer fields opaque until then); P4's
   pointer-following closure carries a visited-set.
7. **Anonymous/unnamed struct types** *(user-ratified 2026-07-31)* — emit a record +
   full field table uniformly with named structs. The field-table machinery is keyed
   on the mangled symbol + `Fields`, not on having a user name, so this is the default
   path (rendering opaque would need *extra* suppression code). Consequences: `%v` of
   an anon struct renders `{…}` identically to a named one (matches Go), and P3
   recursion into an anon struct field works uniformly. The `name` word reuses the
   existing `__nameless_<shape>` record name (used only by `%T`/panics — out of scope
   here).

## 6. Phasing

- **P1 — field-table RTTI (no cross-record refs). ✅ LANDED `133db88d`** (+ prereq
  selector fix `4b281158`). Add KIND + a field table to
  `__typeinfo` (irdata bytes: extend `TypeInfoDesc` + `BuildTypeInfo`; `ir` gather:
  derive KIND, build the field descs with name/offset/kind/size and a **null**
  field-typeinfo-ptr), plus the `reflect.TypeInfo`/`FieldInfo` surface and
  `reflect.TypeOf`/`DataOf` (gen_call intrinsics). Because the field-typeinfo-ptr is
  null, there are NO cross-record DT_SYMREFs: LLVM/native/VM all lower the new blobs
  transparently via the existing `BuildTypeInfo` loop (native only needs `symPrefixed`
  on the two new per-type symbols `FieldsSym`/`NamesSym`). Test: a conformance program
  reads `reflect.TypeOf(&s).Fields` and prints names/offsets/kinds/sizes across
  LLVM/VM/native. No fmt change yet.
- **P1.5 (was "P1 force-emit") — moved into P3.** Transitive force-emit, the VM
  two-phase allocate-then-backpatch (§0.3), LLVM external-global decls for cross-TU
  records, and native `symPrefixed` on per-field typeinfo refs all land in **P3**,
  which is the first consumer of the field-typeinfo-ptr (recursion). Deferred here
  because the pointer is inert until then and folding it into P1 buys nothing but
  risk (user-ratified 2026-08-01).
- **P2 — fmt scalar/string structs. ✅ LANDED `2ef97634`** (conformance `1157`).
  `writeArg` default renders a struct with
  scalar/string fields as `{…}` via read-bytes-per-kind (no recursion). Go-diff.
- **P3 — recursion. ✅ LANDED `6a8b7f8f`** (conformance `1158`).  Nested by-value
  NAMED struct fields recurse via their `*TypeInfo`: CollectTypeInfoDescs became a
  deterministic pure worklist closure that force-emits each reachable named struct
  field type's record + field table (populating the per-field typeinfo-ptr); VM
  `materializeTypeInfos` went two-phase; native prefixes the per-field symbol; LLVM
  unchanged.  Anonymous struct fields render `{...}` (their `__anon_N` name is not
  TU-invariant) — a structural `mangleTypeArg` `TYP_STRUCT` arm is the follow-up
  prerequisite for anon-struct records (decision 7).
- **P4 — aggregates in fields.** Arrays/slices/pointers, `%+v` names, cycle guard.
  - **P4a — `%+v` field names. ✅ LANDED `f7a3ec06`** (conformance `1159`).  fmt-only
    (`withNames` flag; putDefaultNamed keeps Stringer winning).  `{x:3 y:4}`.
  - **P4b — array/slice/pointer fields + cycle guard.** REMAINING.  Needs an RTTI
    element/pointee-type extension (per array/slice/pointer FieldEntry) + fmt code to
    walk elements / follow pointers, with a visited-set cycle guard for pointers.

Each phase lands independently (P1 is self-contained and useful for any reflection
consumer; P2/P3/P4 are incremental fmt behavior), verified across LLVM/VM/native
and Go-diffed, per the fmt cadence used for Print/Printf.

## 7. Risks

- **Layout change touches five backends + the VM + spec §7.13.14** — must land the
  record extension atomically (all emitters agree via the single `BuildTypeInfo`,
  which structurally prevents drift, but the spec + comment + any hard-coded
  "5-word" assumptions must move together). `readTypeInfoName`'s fixed offsets are
  unaffected (words 3/4 unchanged).
- **Transitive force-emit can grow binaries** (a record + field blob per reachable
  struct type). Bounded by the reachable-struct closure; measure. A `--no-reflect`
  or size-conscious mode is out of scope here.
- **Raw byte reads in fmt are unsafe** — contained in one reflect/fmt helper,
  driven only by compiler-emitted offsets/kinds (never user input).
- **BUILDER compat**: `irdata`/`TypeInfoDesc`/`BuildTypeInfo` changes must compile
  under the pinned BUILDER; verify before extending (the CHECK_TOOLS-lag lesson).

## 8. Estimate

P1 is a multi-file compiler change (irdata + ir + 5 emitters + reflect.bni + VM
data-sym binding) — the largest single piece. P2/P3 are contained fmt changes.
Overall this is a major feature (comparable to the compiler/interp-interop tier),
appropriately staged. Recommend ratifying §5 decisions, then starting P1.
