# Plan: struct reflection for `fmt` `%v` (field-table RTTI + runtime rendering)

Status: **DRAFT — for ratification.** Design only; no code landed. Written 2026-07-30
from a recon sweep of the RTTI / reflect / types / fmt substrate (workflow
`wf_69ec9265-9c4`).

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
word5  KIND                 (NEW: enum — scalar-int/uint/float/bool, string,
                             struct, array, slice, ptr, iface, func, other)
word6  field count          (NEW: 0 for non-structs)
word7  fields ptr           (NEW: &__typefields.<T>, or null)
```

`__typefields.<T>` is a new TU-local rodata/static blob (following the
`__typeinfo_name.<T>` name-blob precedent — reloc-safe, emitted alongside the weak
record), an array of `FieldEntry`:

```
FieldEntry { name-ptr, name-len, offset, field-kind, field-typeinfo-ptr }
```

`field-typeinfo-ptr` = `&__typeinfo.<fieldType>` when the field's type has its own
record (enables recursion into nested structs); else null (fmt renders it from
`field-kind` alone, or opaque). Words 0–4 keep their offsets, so every existing
assertion reader (which reads words 0–4 by fixed offset and compares the record
ADDRESS for identity) is unaffected — the extra trailing words are inert to them.

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
type TypeInfo struct { Kind int; Name *[]readonly char; Fields *[]@FieldInfo }
type FieldInfo struct { Name *[]readonly char; Offset int; Kind int; Type ... }
func TypeOf(x *any) *TypeInfo    // thin wrapper over OP_IFACE_TYPEINFO
```

fmt reads field bytes itself (§3.2); the reflect API just surfaces the metadata.
(A general "read field value into an `*any`" is deferred with the re-box question.)

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

## 5. Open decisions (need ratification before coding)

1. **Record shape**: extend the record to 8 words (recommended) vs a parallel
   `reflect.Package.Types` table. (Spec §7.13.14 update either way.)
2. **Rendering**: read-bytes-per-kind (recommended, avoids a new box op) vs a
   runtime box-from-(addr,TypeInfo) primitive (more general, enables a full
   `reflect` later).
3. **Nested coverage**: transitive force-emit field-type records (recommended) vs
   render unrecorded nested structs opaquely (`main.Inner{…}` name only).
4. **`%v` output format**: Go's `%v` of a struct is `{3 4}` (values only); `%+v`
   is `{x:3 y:4}`. Ship `%v` = `{3 4}` first and `%+v` later, or go straight to
   `{x:3 y:4}` for `%v`? (Recommend: match Go — `%v`=`{3 4}`, `%+v`=`{x:3 y:4}`.)
5. **Kinds in scope for P1/P2**: scalars + string + nested struct first;
   arrays/slices/pointers/ifaces render opaque until a later phase.
6. **Cycles / depth**: a self-referential struct (via a raw/managed pointer field)
   needs a depth cap or seen-set once pointer fields are rendered (P4). Until then,
   pointer fields render opaque, so no cycle risk.
7. **Anonymous/unnamed struct types**: emit a record + fields, or skip
   (render opaque)?

## 6. Phasing

- **P1 — field-table RTTI.** Add KIND + field table to `__typeinfo` (irdata bytes,
  `ir` gather with transitive force-emit, all five backends + VM), plus the
  `reflect.TypeInfo`/`FieldInfo` surface and `reflect.TypeOf`. Test: a conformance
  program reads `reflect.TypeOf(&s).Fields` and prints names/offsets/kinds. No fmt
  change yet. This is the bulk of the compiler work.
- **P2 — fmt scalar/string structs.** `writeArg` default renders a struct with
  scalar/string fields as `{…}` via read-bytes-per-kind (no recursion). Go-diff.
- **P3 — recursion.** Nested struct fields recurse via their `*TypeInfo`.
- **P4 — aggregates in fields.** Arrays/slices/pointers, `%+v` names, cycle guard.

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
