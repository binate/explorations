# Plan: Fix Slice Usage in the Self-Hosted Interpreter

> **STATUS (2026-03-31): COMPLETED.** All `append` calls in the self-hosted interpreter have been replaced. `append` has been fully removed from the language.

## Problem

The self-hosted interpreter (`pkg/interp/`) uses raw slices with append throughout
— for runtime value storage, string building, environment entries, escape sequence
parsing, and more. Like the compiler, it treats slices as Go-style growable arrays.
Per the Binate spec, raw slices are unmanaged fixed-size views.

## Current Append Usage (68 calls across 4 files)

| File | Appends | Primary Use |
|------|---------|-------------|
| interp.bn | 26 | Runtime eval: args, return vals, elem/field building, escape parsing |
| value.bn | 19 | Value conversion, string building, env entries, copy/zero |
| bootstrap_fwd.bn | 4 | Package registration, string/arg conversion |
| interp_test.bn | 23 | Test setup + test source strings using append |

## Categories of Misuse

### 1. String building (`[]char` append) — ~25 calls

**Where:** `value.bn` (intToStr, concatStr, valueToString), `interp.bn` (escape
sequence parsing in string literals)

**Pattern in value.bn:**
```
func intToStr(n int) []char {
    var buf []char
    ...
    buf = append(buf, cast(char, d + cast(int, '0')))
    ...
}
```

**Pattern in interp.bn (escape parsing, lines 1397-1415):**
```
if s[i] == 'n' { buf = append(buf, '\n'); i = i + 1; continue }
if s[i] == 'r' { buf = append(buf, '\r'); i = i + 1; continue }
...
```

**Fix:** Replace with `CharBuf` from `pkg/buf` (see `claude-plan-charbuf.md`).
Same type used by the compiler. Requires `make` fix first (`claude-plan-fix-make.md`).

### 2. Value list accumulation — ~30 calls

**Where:** `interp.bn` (building argument lists for calls, return value lists,
struct field lists, array/slice element lists), `value.bn` (copyValue, ZeroValue)

**Subpatterns:**

**a) Building function call arguments (interp.bn:894-905):**
```
var args []@Value
for i := 0; i < len(e.Args); i++ {
    args = append(args, evalExpr(interp, e.Args[i]))
}
```
Size is known: `len(e.Args)`. Could pre-allocate.

**b) Building return value lists (interp.bn:378):**
```
var vals []@Value
for i := 0; i < len(s.Exprs); i++ {
    vals = append(vals, evalExpr(interp, s.Exprs[i]))
}
```
Size is known: `len(s.Exprs)`. Could pre-allocate.

**c) Copying elements in copyValue (value.bn:150-157):**
```
var elems []@Value
for i := 0; i < len(v.Elems); i++ {
    elems = append(elems, copyValue(v.Elems[i]))
}
```
Size is known: `len(v.Elems)`. Could pre-allocate.

**d) Zero-initializing struct fields (interp.bn:1130, value.bn:201):**
```
var fields []@Value
for i := 0; i < len(st.Fields); i++ {
    fields = append(fields, ZeroValue(st.Fields[i].Type))
}
```
Size is known: `len(st.Fields)`. Could pre-allocate.

**e) Append builtin implementation (interp.bn:1001-1005):**
```
// copy existing elements
for i := 0; i < len(sv.Elems); i++ {
    newElems = append(newElems, sv.Elems[i])
}
// append new elements
for i := 1; i < len(e.Args); i++ {
    newElems = append(newElems, evalExpr(interp, e.Args[i]))
}
```
This is the self-hosted interpreter's implementation of the `append` builtin.
Once append is removed from the language, this entire code path gets deleted.

**f) Slice expression (interp.bn:1063):**
```
for i := lo; i < hi; i++ {
    sliced = append(sliced, elems[i])
}
```
Size is known: `hi - lo`. Could pre-allocate.

**Fix for all known-size cases:** Use `make_raw_deprecated([]T, n)` (or
`make_slice(T, n)` once available) and index assignment instead of append.
Since these are all cases where the final size is known before the loop:
```
var args @[]@Value = make_slice(Value, len(e.Args))
for i := 0; i < len(e.Args); i++ {
    args[i] = evalExpr(interp, e.Args[i])
}
```
Or with `make_raw_deprecated` during the transition:
```
var args []@Value = make_raw_deprecated([]Value, len(e.Args))
```

### 3. Retained state accumulation — ~8 calls

**Where:** `interp.bn` (Packages, ImportAliases), `value.bn` (Env.Entries),
`bootstrap_fwd.bn` (Packages)

**Pattern:**
```
interp.Packages = append(interp.Packages, pkg)
interp.ImportAliases = append(interp.ImportAliases, entry)
e.Entries = append(e.Entries, entry)
```

These grow over the lifetime of interpretation. They're small (packages: ~10,
aliases: ~10, env entries: ~50 per scope) but truly dynamic.

**Fix:** Concrete buffer types (e.g., `PkgList`, `AliasList`, `EntryList`) or
keep as raw slices if we add a `make_raw_deprecated` + copy growth pattern. Since these
are small and grow infrequently, even O(n) copy on growth is acceptable.

### 4. Nil-slice semantics

**interp.bn:835:** `isNil = other.Elems == nil` — slice nil comparison in evalNilCompare

**interp.bn:74-75:** `interp.Types = nil; interp.ImportAliases = nil` — reset to nil

**Fix:** Remove nil comparison (type checker should reject). Replace `= nil` with
`= []T{}` or `.Clear()` on buffer types.

## Implementation Order

### Prerequisite: Fix `make` + implement CharBuf
See `claude-plan-fix-make.md` and `claude-plan-charbuf.md`.

### Step 1: CharBuf conversion (depends on pkg/buf existing)
- `value.bn`: intToStr, concatStr, valueToString, strCopy
- `interp.bn`: escape sequence parsing (lines 1397-1415)
- `bootstrap_fwd.bn`: bytesToStr

### Step 2: Pre-sized allocation for known-size lists
- Function call arguments (interp.bn:894)
- Return value lists (interp.bn:378)
- copyValue element/field copies (value.bn:150-157)
- ZeroValue struct fields (value.bn:201, interp.bn:1130)
- Slice expression (interp.bn:1063)
- Array/make element initialization (interp.bn:1159, 1202)
- String-to-bytes conversion (interp.bn:1286)

### Step 3: Buffer types for retained state
- `Env.Entries` — either a concrete buffer or keep as raw slice with manual growth
- `interp.Packages`, `interp.ImportAliases` — small, infrequent growth

### Step 4: Remove append builtin implementation
- Delete the `append` evaluation path in evalExpr (interp.bn:998-1006)
- This happens when append is removed from the language

### Step 5: Remove nil-slice semantics
- Remove slice branch from evalNilCompare
- Replace `= nil` assignments with zero-value syntax
- Update interp_test.bn tests that exercise nil-slice behavior

## Files to Modify

| File | Changes |
|------|---------|
| `pkg/interp/interp.bn` | CharBuf for escapes, pre-sized lists, remove append eval, remove nil-slice |
| `pkg/interp/value.bn` | CharBuf for string building, pre-sized copies, buffer for Env.Entries |
| `pkg/interp/bootstrap_fwd.bn` | CharBuf for bytesToStr, buffer for Packages |
| `pkg/interp/interp_test.bn` | Update tests (remove append-based test sources, fix nil-slice tests) |

## Dependencies

- `make` must be fixed first (`claude-plan-fix-make.md`)
- `pkg/buf` (CharBuf) must exist (`claude-plan-charbuf.md`)
- `make_raw_deprecated([]T, n)` for raw slice pre-allocation during migration
  (or `make_slice(T, n)` once available, returning `@[]T`)
- Append removal (language-wide) must happen before step 4

## Validation

- `bootstrap -test pkg/interp` — unit tests
- Full conformance suite (selfhost mode specifically exercises this interpreter)
- Self-compilation chain (bootstrap → selfhost interpreter → compiler)

## Risk

Medium. The self-hosted interpreter is exercised by the selfhost conformance mode
and is less critical than the compiler (the bootstrap Go interpreter is the primary
path). But it must remain correct for the selfhost mode to pass.

The biggest risk is the pre-sized allocation pattern — `make_raw_deprecated([]T, n)`
preserves the current raw-slice allocation behavior during migration.
`make_slice(T, n)` returns `@[]T` (managed-slice) once implemented.
