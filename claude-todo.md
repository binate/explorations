# Binate TODO

Tracks work items discussed across sessions. Items move to "Done" when committed.

---

## TODO

### Linux/x86-64: boot-comp-comp string corruption
- **boot and boot-comp PASS on CI** (x86-64 Linux). The sret fix for C ABI struct returns works correctly for boot-comp.
- **boot-comp-comp and boot-comp-comp-comp FAIL**: the gen1 compiler (compiled by boot-comp) produces garbage filenames when calling clang. Output looks like raw pointer values (e.g., `<A0><97><FF><9E><C8>U`) — suggests `@[]char` / `BnManagedSlice` data is being misinterpreted.
- **Root cause unknown**: sret is correctly applied to `bootstrap.Concat` and `bootstrap.Itoa` declarations. The boot-comp compiler (interpreted bnc) correctly compiles programs that call Concat. But the gen1 compiler (compiled bnc binary) fails when IT calls Concat to build clang args. This suggests a difference in how the self-compiled binary handles `@[]char` return values vs the interpreted path.
- **CI currently runs basic modes** (boot, boot-comp, boot-comp-int) which all pass.
- **Approach**: reproduce on x86-64, compare LLVM IR from boot-comp vs boot-comp-comp for a simple Concat call. Check if the gen1 binary's code for `appendRawCharSlice` / `compileLL` differs from what the interpreted bnc produces.

### Self-hosted interpreter: investigate boot-comp-int-int failures
- boot-comp-int-int (compiled bni interprets bni which interprets test) fails 75/142 conformance tests and 11/14 unit test packages. The failing tests produce empty output (silent failure or hang).
- **This is surprising**: boot-int (bootstrap interprets bni which interprets test) worked fine (128/142). boot-comp-int (compiled bni interprets test) works (129/142). So the self-hosted interpreter can interpret user programs, but breaks when interpreting *itself* interpreting a program.
- **Must investigate before flat memory migration**: the interpreter has fundamental bugs that should be fixed first, otherwise the flat memory migration will be fighting these bugs at the same time.
- Likely causes: stack overflow from deep recursion, unhandled edge cases in self-interpretation (e.g., the interpreter's own use of managed slices/structs differs from user code patterns), or missing features in the self-hosted interpreter that the bootstrap handles.
- **Approach**: compare a specific failing test (e.g., 003_variables) between boot-comp-int (passes) and boot-comp-int-int (fails) to isolate what goes wrong when the interpreter interprets itself.

### Self-hosted interpreter memory model parity with compiler
- Plan: `explorations/plan-interp-memory-parity.md`
- Phase 1 (done): infrastructure — `flat.bn` with readFlatValue/writeFlatValue, using bit_cast and pkg/rt
- Phase 2 (started): scalar variables stored in flat memory — envDefine/envGet/envSet use flat addresses for ints
- **Blocked on**: investigating boot-comp-int-int failures first (see above)
- The interpreter no longer runs on the bootstrap (boot-int dropped). It only runs compiled (boot-comp-int and above). This means it can freely use bit_cast, pointer indexing, and pkg/rt.

### Binate type checker: duplicate function detection
- The Binate type checker (pkg/types) does not detect duplicate function declarations within the same package
- The bootstrap Go type checker does ("foo redeclared in this block")
- Conformance test 206 is xfail'd for boot-int and boot-comp

### Verify .bni vs .bn visibility semantics
- Both `.bni` and `.bn` files can contain type declarations, constants, aliases, and globals
- `.bni` declarations are public (visible to importers); `.bn` declarations are private
- **Verify**: that declarations in `.bn` files are NOT accessible to importing packages
- **Add negative conformance tests**: importing a private type/constant should fail to compile
- **Check**: if the same name is declared in both `.bni` and `.bn`, does it cause duplicate registration errors?
- **Related**: Forward struct declarations in `.bni` (declare name only, define in `.bn`) — future feature.

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### Continue backfilling negative conformance tests
- 19 negative tests exist (112, 200-210, 214-220), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types
- `.error` files now use `grep -E` regex matching (patterns like `(foo|bar)` match across type checkers)
- Still needed: shadowing errors, import errors, package mismatch, type conversion errors, const expression errors
- Some errors not caught by either type checker: break outside loop, missing return, assign to const

### Pointers to interface values
- Interface values are regular value types — allow `*Iface`, `@(Iface)`, `*@Iface`, `@(@Iface)`, etc.
- `@Iface` sugar parallels `@[]T` sugar; parens break it
- Needed for: generics (`*T` where `T=Stringer`), out parameters, arrays of interfaces, containers
- Implementation: grammar, parser, type checker, codegen, bootstrap interpreter

### Function-local type declarations — design question
- Go supports `type Foo struct { ... }` inside function bodies. Binate currently doesn't handle this in the compiler (works in bootstrap interpreter).
- **Consider**: do we want function-local types at all? They're somewhat limited in Go.
- If not, the parser should reject them. If yes, the IR gen needs to handle them.
- Low priority — package-level types cover most use cases.

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`[]T{...}`) as sugar

### Full DWARF debug info (line-level source mapping)
- Add `Pos token.Pos` field to `ir.Instr` struct (in `ir.bni`)
- Thread `token.Pos` from AST nodes through IR generation (~40 sites)
- Emit per-instruction `DILocation` with real line numbers (currently all line 0)
- Prerequisite: lightweight debug info (done)

### Package directory organization and conventions
- Think more carefully about `pkg/` directory structure and naming conventions
- Current layout mixes toolchain internals with runtime and bootstrap support
- Questions: should toolchain packages be under a sub-prefix? Where do future stdlib packages live?

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Slice ownership model — design notes
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC. Caller manages lifetime (like C).
- Cannot be compared to `nil` — check `len(s) == 0` for empty.

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `[]T`. Refcounted via backing_refptr.
- backing_len stores total element count for destructor cleanup.
- `make_slice(T, n)` returns `@[]T`. `@[]T → []T` conversion: extractvalue fields 0,1.

---

## Done (session 2026-04-03/04/05)

### Destructors — struct, managed-slice, array, anonymous struct
- `rt.RefDec(ptr *uint8, dtor *uint8)` — dtor called before Free when rc hits 0
- `types.NeedsDestruction(t)` — recursive query for types requiring cleanup
- `OP_FUNC_ADDR` — new IR opcode for function address as `i8*`
- Struct dtors, managed-slice dtors (with element cleanup loops), array dtors, anonymous struct dtors
- All use `linkonce_odr` for linker dedup. Cross-package references via `qualifiedDtorNameForType`.
- Conformance tests: 113-116.

### Anonymous struct support
- Both type checkers: `Identical()` with structural equivalence (field names + types in order)
- IR gen: `resolveTypeExpr` handles TEXPR_STRUCT, synthetic names, deduplication
- Conformance tests: 113, 119-121.

### `*any` → `*uint8` migration in pkg/rt

### Array codegen fixes
- `arr[i].Field` for managed-ptr elements, `cont.Items[i] = v` selector-base, element refcounting
- Conformance tests: 117, 118.

### Temporary lifetime fix
- Removed all leaking `consumeTemp` for `@[]T→[]T`. Temps RefDec'd at end of statement.
- Migrated bnc to `@[]@[]char`. `bootstrap.Exec` now takes `[]@[]char`.
- Conformance test: 122.

### .bni processing: RegisterSelfTypes expanded
- Now handles struct types, type aliases, and constants from the package's own .bni file.

### Negative conformance tests (19 total)
- 112 (slice nil), 200-210 (type mismatch, undeclared, wrong args, nil, return type, duplicate decl, operators, conditions, field access, indexing), 214-220 (comparisons, unary, call non-func, managed ptr arith, slice nil assign, multi-return, undefined type)
- `.error` files use `grep -E` regex matching for cross-checker compatibility

### Test infrastructure
- 6-mode unit test runner: boot, boot-int, boot-comp, boot-comp-int, boot-comp-comp, boot-comp-comp-comp
- Mode sets: basic (3), all (5), full (6). `bnc --test` just compiles (runner executes).
- Summary lines show mode. Bug discovery protocol in CLAUDE.md. Never-leak rule. Coding guide reference.

## Done (previous sessions)

### @[]T refcounting, OP_MAKE_SLICE migration, C runtime cleanup — `80b5150`
### Self-hosted interpreter HeapObj tracking — `c997b9f`
### Package search paths and implicit pkg/rt import — `ad394ee`
### @[]T layout, MakeManagedSlice, @[]T → []T conversion — `da07f70`
### bit_cast, pointer indexing, pkg/rt — `c80d962`
### Codegen bugs (074-087) — ALL FIXED
### Self-compiled compiler — FULLY PASSING ✓
### Remove append — DONE
### Remove null termination — DONE
### 4-word managed-slice layout — DONE
### Unit test backfill (two passes) — DONE
