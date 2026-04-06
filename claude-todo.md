# Binate TODO

Tracks work items discussed across sessions. Items move to "Done" when committed.

---

## TODO

### Self-hosted interpreter memory model parity with compiler
- The self-hosted interpreter (cmd/bni / pkg/interp) currently uses a tagged-union value model (`VAL_INT`, `VAL_STRUCT`, `VAL_SLICE`, etc.) that doesn't correspond to flat memory. This means `bit_cast` and pointer indexing can't work because there are no real addresses to reinterpret or index into.
- Currently XFAIL: 090 (bit_cast), 091 (pointer indexing), 092/093 (pkg/rt — depends on both)
- **This is more tractable than it first appears.** The fix is to make the interpreter lay out data in memory the same way the compiler does:
  - Structs: fields at the same offsets with the same padding (matching `SizeOf`/`AlignOf`/`FieldOffset`)
  - `@T`: heap-allocated with the refcount header at negative offset (matching `rt.Alloc` layout)
  - `@[]T`: `{data_ptr, len, backing_refptr, backing_len}` — same 4-word `%BnManagedSlice` layout
  - `[]T`: `{data_ptr, len}` — same 2-word `%BnSlice` layout
  - Arrays: contiguous elements at `elem_size` stride
- With ABI-compatible layout, `bit_cast(int, ptr)` is just reading the pointer address as an integer, and `ptr[i]` is pointer arithmetic — no simulated heap needed, just real memory operations on the interpreter's own heap.
- **Scope**: this is a refactor of `pkg/interp`'s value representation, not a fundamental architecture change. The interpreter already tracks types, sizes, and refcounts — it just needs to store values in flat memory instead of tagged unions.

### ~~Temporary lifetime~~ — FIXED
- All `consumeTemp` calls for `@[]T → []T` conversion removed. Temps stay in cleanup and get RefDec'd at end of statement. No more leaks.
- If `var s []int = make_slice(int, 3)` causes UAF, that's user error (raw slice borrows a temporary). The compiler never leaks.
- `bootstrap.Exec` signature changed to `[]@[]char` (was `[][]char`). bnc migrated to `@[]@[]char` throughout.
- Conformance test: 122_temp_slice_param.

### ~~Compiler must process package's own .bni file~~ — DONE
- `RegisterSelfTypes(pkg.BNI)` now handles struct types, type aliases, and constants from the package's own .bni file. Also fixed: moduleStructs[si].Fields was not being set.

### Verify .bni vs .bn visibility semantics
- Both `.bni` and `.bn` files can contain type declarations, constants, aliases, and globals
- `.bni` declarations are public (visible to importers); `.bn` declarations are private
- **Verify**: that declarations in `.bn` files are NOT accessible to importing packages
- **Add negative conformance tests**: importing a private type/constant should fail to compile
- **Check**: if the same name is declared in both `.bni` and `.bn`, does it cause duplicate registration errors?
- **Related**: Forward struct declarations in `.bni` (declare name only, define in `.bn`) — future feature.

### Backfill negative conformance tests
- The conformance test framework supports `.error` files for negative tests
- Currently only 112_slice_nil_rejected exists
- Need negative tests for: type mismatches, undeclared variables, wrong number of arguments, invalid type conversions, duplicate declarations, invalid nil usage on non-pointer types

### Pointers to interface values
- Interface values are regular value types — allow `*Iface`, `@(Iface)`, `*@Iface`, `@(@Iface)`, etc.
- `@Iface` sugar parallels `@[]T` sugar; parens break it
- Needed for: generics (`*T` where `T=Stringer`), out parameters, arrays of interfaces, containers
- Implementation: grammar, parser, type checker, codegen, bootstrap interpreter

### Function-local type declarations — design question
- Go supports `type Foo struct { ... }` inside function bodies. Binate currently doesn't handle this in the compiler (works in bootstrap interpreter).
- **Consider**: do we want function-local types at all? They're somewhat limited in Go (can't define methods on them in the same scope, can't use them outside the function).
- If not, the parser should reject them. If yes, the IR gen needs to handle them.
- Low priority — package-level types cover most use cases.

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`[]T{...}`) as sugar

### Package directory organization and conventions
- Think more carefully about `pkg/` directory structure and naming conventions
- Current layout mixes toolchain internals (token, ast, lexer, parser, types, ir, codegen, linker, interp) with runtime (rt) and bootstrap support (bootstrap)
- Questions: should toolchain packages be under a sub-prefix? Where do future stdlib packages live?

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs
- Consider: what's in the language vs. stdlib vs. third-party, naming conventions, minimal footprint for embedded targets

### Full DWARF debug info (line-level source mapping)
- Add `Pos token.Pos` field to `ir.Instr` struct (in `ir.bni`)
- Thread `token.Pos` from AST nodes through IR generation (~40 sites)
- Emit per-instruction `DILocation` with real line numbers (currently all line 0)
- Prerequisite: lightweight debug info (done)

### Slice ownership model — design notes
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC
- Caller manages lifetime (like C)
- Sub-slicing copies data (no aliasing, no double-free risk)
- Cannot be compared to `nil` — check `len(s) == 0` for empty

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `[]T` — first two words identical
- Refcounted via backing_refptr (field 2)
- backing_len (field 3) stores total element count for destructor cleanup
- `@[]T` is syntactic sugar, distinct from `@([]T)` (managed pointer to raw slice)
- `make_slice(T, n)` returns `@[]T`
- `@[]T → []T` conversion: trivial extractvalue of fields 0,1

---

## Done (this session — 2026-04-03/04)

### Destructors — struct, managed-slice, array, anonymous struct
- `rt.RefDec(ptr *uint8, dtor *uint8)` — dtor called before Free when rc hits 0
- `c_call_dtor` C stub for indirect function pointer calls
- `types.NeedsDestruction(t)` — recursive query for types requiring cleanup
- `OP_FUNC_ADDR` — new IR opcode for function address as `i8*`
- Struct dtors: `__dtor_<Name>` generated for structs with managed fields. Handles @T fields (RefDec with pointee dtor), @[]T fields (call managed-slice dtor), inline struct/array fields (call type dtor).
- Managed-slice dtors: `__dtor_ms_<elemType>` generated for @[]T types. Check Refcount==1, loop over backing_len, call element cleanup, RefDec backing. Recursive for nested types.
- Array dtors: `__dtor_arrN_<elemType>` generated for [N]T where T needs destruction.
- Anonymous struct dtors: `__dtor_anon_<type1>_<type2>_...` with hash fallback for long names.
- All dtors use `linkonce_odr` for linker deduplication across modules.
- `emitManagedPtrRefDec` — passes struct dtor to RefDec at all managed pointer cleanup sites.
- `emitManagedSliceRefDec` — calls managed-slice dtor function (unified path, returns continuation block).
- Cross-package dtor references via `qualifiedDtorNameForType`.
- Conformance tests: 113-116 (struct dtor, slice elem dtor, array dtor, anon struct dtor).

### Anonymous struct support
- Both type checkers: `Identical()` now does structural equivalence for anonymous structs (field names + types in order). Named structs still by name only.
- IR gen: `resolveTypeExpr` handles TEXPR_STRUCT, assigns synthetic names (`__anon_N`), deduplicates identical field sequences via `findAnonStruct`.
- Codegen: works naturally (anonymous structs have synthetic names).
- Conformance tests: 113, 119 (field), 120 (param), 121 (return).

### `*any` → `*uint8` migration
- All `*any` in pkg/rt (.bn, .bni), tests, and conformance tests replaced with `*uint8`
- `*uint8` is the opaque byte pointer type; `any` reserved for future empty interface type
- Design notes updated.

### Array element field access and selector-base array assignment
- `arr[i].Field` where arr is `[N]@T`: genSelector handles managed-ptr elements from genIndexPtr
- `cont.Items[i] = v`: genControl and genIndexPtr handle EXPR_SELECTOR base for arrays
- Array element assignment emits RefInc/RefDec for managed pointer elements
- Conformance tests: 117, 118.

### Re-enable rt.RefDec freeing — DONE
- Free is called in `rt.RefDec` when refcount hits 0
- Slice element refcounting (RefInc on store, RefDec on overwrite) implemented
- Destructors clean up managed fields before Free

### Test runner improvements
- Summary lines show mode: `=== Summary (boot-comp): 121 passed, 0 failed, 0 skipped ===`
- Bug discovery protocol added to CLAUDE.md

## Done (previous sessions)

### @[]T refcounting, OP_MAKE_SLICE migration, C runtime cleanup
- Committed: `80b5150`

### Self-hosted interpreter HeapObj tracking for managed slices
- Committed: `c997b9f` (binate), `4e346c5` (bootstrap)

### Package search paths and implicit pkg/rt import
- Committed: `ad394ee`

### @[]T layout, MakeManagedSlice, @[]T → []T conversion
- Committed: `da07f70`

### bit_cast, pointer indexing, and pkg/rt Binate runtime
- Committed: `c80d962`

### Nil-to-slice assignment stores i8* instead of zeroed BnSlice
- Committed: `ce85c8f`

### OP_SLICE_ELEM_PTR for in-place struct slice element access
- Committed: `cace611`

### bn_slice_expr_struct and chained managed-ptr assignment workarounds
- Committed: `21e1c9e`

### Codegen bugs (074-087) — ALL FIXED
- Struct literal init, field assign refcount, string-to-chars, selector ptr, short-circuit, for-loop back-edge, nil-slice arg, debug info, array-to-slice, slice-of-structs, nested struct slice

### Self-compiled compiler — FULLY PASSING ✓
- boot-comp-comp: all conformance tests pass
- Gen2 compiler (boot-comp-comp-comp) also passes

### Remove append from the language — DONE
### Remove implicit null termination from string literals — DONE
### Unit test runners for all 3 modes — DONE
### 4-word managed-slice layout — DONE
### Backfill unit tests (two passes) — DONE
