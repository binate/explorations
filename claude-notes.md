# Binate Design Notes

## Summary of Goals (from README)

- Systems programming language, simple but expressive enough for kernel work
- **Dual-mode execution**: compiled and interpreted, with seamless interop between modes
- Compiled code can call into an embedded interpreter; interpreted code can call compiled code
- Embeddable interpreter — small enough for systems with only a few MB of RAM
- REPL support via interpreted mode
- Low resource footprint for both compiler and interpreter
- Self-hosting as a long-term goal
- Primary target: 32-bit systems, with full 64-bit support
- Assumes modern CPU characteristics (e.g. two's complement)

## Initial Questions & Discussion Points

### Dual-mode interop — DECIDED

**Function pointers as the unification layer.** The caller doesn't know or care whether a function is compiled or interpreted.

- **Compiled functions**: native function pointer, direct call
- **Interpreted functions**: pointer to a thunk that packages args, invokes the interpreter, returns the result
- Overhead (one indirection) only paid when crossing the boundary
- Mixed vtables are possible: some interface methods compiled, others interpreted — caller is oblivious

**Why this works — everything else already supports it:**
- Same heap, same refcounting, same struct layouts — no marshalling
- Same type system in both modes — thunk bridges calling conventions, not types
- Package interface files — interpreter discovers compiled function signatures and resolves addresses

**Interpreted → compiled calls**: interpreter loads interface files, resolves function addresses from the compiled binary's symbol table (or explicit registration). Calls through native function pointers.

**Compiled → interpreted calls**: compiled code holds a function pointer that is actually a thunk. The thunk invokes the interpreter on the function body.

### Memory model — DECIDED

**Reference counting** as the managed memory model. Two worlds:

1. **Managed structs** (the default):
   - Carry a refcount + management info (e.g., how to free them, possibly custom allocator info)
   - Accessed via **managed pointers** (refcounted, the default pointer type)
   - When refcount hits zero, the struct is freed according to its management info

2. **Raw structs**:
   - No refcount, no management overhead
   - Accessed via **raw/unmanaged pointers**
   - Manual lifetime management, like C

**Key rule (revised)**: raw pointers can point to raw structs OR managed structs. Pointing a raw pointer at a managed struct is the escape hatch for breaking cycles — it's essentially a weak/unowned reference, but without safety nets. The programmer is responsible for ensuring the managed struct hasn't been freed.

**Design philosophy**: willing to trade some safety for power and simplicity. Refcounting is chosen over GC (too heavy, non-deterministic) and ownership/borrowing (too complex for the "simple and approachable" goal).

**Known trade-offs accepted**:
- Cycles can leak (no automatic cycle detection) — programmer responsibility
- Refcount bumps on every copy/assignment — raw structs are the escape hatch for hot paths
- Thread safety of refcounts is an open question (atomic vs. non-atomic)

**Dual-mode benefit**: management info embedded in structs means the interpreter doesn't need special knowledge to handle objects from compiled code (or vice versa) — the object carries its own cleanup semantics.

**Cycle-breaking strategy**: use raw pointers to managed structs as unowned references. Unsafe (dangling possible), but simple and avoids needing a separate weak-ref concept.

**Drop semantics**: when refcount hits zero, recursively release all managed fields (decrement their refcounts, which may trigger further drops). This gives deterministic, predictable cleanup.

**Ownership transfer convention — DECIDED (2026-04-02, spec updated 2026-04-11)**: every managed value (`@T`, `@[]T`) carries its refcount as the count of live references. When a value is passed between contexts (return, assignment, argument), ownership of one reference is transferred.

**Function parameters**: the callee owns a reference to each `@T`/`@[]T` parameter for the duration of the function body. The callee RefInc's on entry and RefDec's at scope exit (unless the parameter is returned). The caller is uninvolved — its reference is unaffected. For struct parameters with managed fields, copy constructors handle the RefInc at the call site; destructors handle RefDec at scope exit.

**Function returns**: the callee arranges for the return value to carry exactly one transferred reference for the caller. For the slow approach, this is RefInc before scope cleanup. A move optimization (implemented for local returns) skips the RefInc and the local's scope RefDec, transferring the local's reference directly.

**Move semantics**: currently an optimization, not a language guarantee. The observable behavior (when objects are freed) is the same. Intermediate refcount values may differ. Whether to make move a language-level concept is an open question.

**Invariant: rc == 0 means dead.** Every live reference must be reflected in the refcount. See `explorations/refcount-lifecycle.md` for the full spec covering returns, arguments, temporaries, struct fields, slice operations, and scope cleanup.

### Value types vs. reference types — DECIDED

**Value types**: integers, floats, pointers (including managed pointers!), raw structs, fixed-size arrays, fixed-size strings. Copied on assignment/pass. Live on the stack or inline within other structs. You can only take raw pointers to them, never managed pointers.

**Reference types**: managed structs. Heap-allocated, refcounted, accessed via managed pointers.

**A managed pointer is itself a value type.** Copying it bumps the refcount of the thing it points to. This is the "special semantics" for managed pointers — they're small, copyable values, but copying has a side effect.

**Struct definition style (leaning toward)**: C/C++ approach — the unadorned struct type is always the value/raw type. To get a refcounted heap instance, you put it behind a managed pointer. No auto-generation of managed/raw variants.

**Struct fields that are themselves structs**:
- Inline raw struct (value type, embedded in parent's memory layout)
- Managed pointer to a managed struct (a pointer-sized value type field that references a heap object)
- You do NOT embed a managed struct inline — you always go through a managed pointer

**Interior pointers**: you can take a raw pointer to a field within any struct (managed or raw). For managed structs, this is dangerous — the managed struct could be freed while the interior pointer is live. Same risk profile as raw-pointer-to-managed-struct.

### Arrays and strings — DECIDED

### Slices/views — DECIDED

`char[]` (and arrays of unspecified size generally) are **slices** — a view into underlying data, not a container of data themselves.

**Terminology — IMPORTANT**: "managed-slice" (hyphenated) refers specifically to `@[]T`,
the 4-word type `(data_ptr, length, backing_refptr, backing_len)` created by `make_slice`. "Managed slice"
(two words, no hyphen) is ambiguous — it could mean `@[]T` (managed-slice) or `@(*[]T)`
(a managed pointer to a raw slice). In these notes we use the hyphenated form
"managed-slice" when referring to `@[]T` to avoid confusion.

**Two flavors**:
- **Managed-slice** (`@[]T`): keeps the underlying allocation alive via refcounting. Four words: (raw pointer to start of slice, length, backing refptr, backing len). The first two words match raw slice layout exactly.
- **Raw slice** (`*[]T`, previously `[]T`): (raw pointer to start, length). Two words. No refcounting. Caller manages lifetime.

**Key benefits**:
- Fixed-size arrays (`char[123]`) don't need to store their length — it's captured in the slice when you create a view
- No wasted word for inlined arrays
- String literals (static data) can be sliced without allocation
- Subarrays are just slices with offset pointers

**String literals**: raw static data in the binary. Compiler generates wrapping code based on context:
- Assigned to a slice → slice pointing into static data
- Assigned to a managed array → allocate, copy, set up refcount
- Raw pointer → pointer to static data

**Managed-slice representation — DECIDED (updated 2026-04-02)**: a managed-slice (`@[]T`) is four words: `(data, len, backing_refptr, backing_len)`. The first two words are identical in layout to a raw slice `*[]T`, so `@[]T` → `*[]T` conversion is trivial (just read the first 16 bytes). The remaining two words describe the backing allocation:
1. Raw pointer to the start of the view (direct data access, no arithmetic needed)
2. View length (number of elements visible through this slice)
3. Managed pointer to the backing allocation (keeps it alive via refcounting)
4. Backing length (total number of elements in the backing allocation)

The backing length is needed for destructor cleanup — when the backing's refcount hits zero, the destructor must iterate all backing_len elements to RefDec managed references. This cannot be derived from the view length because subslicing changes the view but not the backing.

**Subslicing semantics**: `s[lo:hi]` on a managed-slice produces a new view: `(data + lo * elemSize, hi - lo, same_refptr, same_backingLen)`. The subslice RefInc's the backing (shared ownership) but does NOT RefInc individual elements. When the last reference to the backing dies (refcount → 0), the destructor iterates from `refptr` (the backing allocation start), NOT from `data` (the view start), over all `backingLen` elements. This is critical: after subslicing, `data ≠ refptr`, so iterating from `data` would walk into wrong memory. **Length-0 result (2026-06-08):** when `hi == lo`, the subslice is instead the nil-equivalent `{null, 0, null, 0}` (raw: `{null, 0}`) — it carries no backing pointer and does NOT RefInc the backing, per the length-0 ⟹ no-backing invariant (see "Nil slices" below). Retaining the backing would only pin it unreachably (a length-0 slice can't be read or re-extended).

Note: with both view length and backing length available, the Go-style "capacity" (`backing_len - (data - backing_start) / elem_size`) is computable. This means a correct `append()` is possible. However, the spirit of managed-slices is as pure views — append may be reconsidered in the future but is not planned.

(Previous 3-word layout `(data, len, refptr)` was updated to 4 words to support destructor cleanup.)

**Raw slice representation**: two words: (raw pointer to start, length). Syntax: `*[]T` (the `*` prefix parallels `*T` for raw pointers).

**Constraint**: managed-slices can only refer to managed allocations. For stack/static data, use a raw slice. To pass stack/static data where a managed-slice is expected, copy into a managed allocation first. This maintains clean lifetime guarantees.

**API semantics**: managed-slice vs. raw slice at function boundaries communicates intent — managed-slice = "I will retain this," raw = "I just need it now." The `*[]T` / `@[]T` syntax makes this distinction visually prominent.

**Introspection builtins**: for low-level transparency, testing, and debugging:
- Something that takes a managed pointer (`@T`) and returns the management header (refcount, free function) as a Binate struct.
- Something that takes a raw slice (`*[]T`) and returns the slice representation (data ptr, length) as a Binate struct.
- Something that takes a managed-slice (`@[]T`) and returns the managed-slice representation (data ptr, length, refptr) as a Binate struct.
- All management/representation structs should be proper Binate structs, not opaque C constructs.
- These can have "obscure" names (e.g., `_refcount_header`, `_slice_repr`, or `bn_`-prefixed) since they're not intended for normal use.

**`append` — REMOVED**: `append` has been fully removed from the language (parser, type checker, IR gen, codegen, interpreter, all source code, tests, and conformance tests). Growable collections are a library concern: `buf.CharBuf` for strings, per-type append helpers that do O(n) copy for other types, and eventually a generic `Vec[T]` type. For known-size allocations, use `make_slice(T, n)` + indexed assignment. Note: with the 4-word managed-slice layout, a correct append is now technically feasible (capacity is computable), so this decision may be revisited.

### Destructors and RefDec cleanup — IMPLEMENTED (2026-04-03; reshaped to handle dispatch 2026-05; dtor-slot miscompile fixed 2026-06-01)

When a managed allocation's refcount hits zero, managed references inside it must be RefDec'd before the memory is freed. This is done via **destructors** — per-type generated functions passed to RefDec at each call site.

**RefDec takes a dtor handle**: `RefDec(ptr *uint8, dtor *uint8)` where `dtor` is either nil or the address of a **per-dtor handle** (a static `%BnFuncValue` value — see "Function values" above). At each call site, the codegen knows the type being dec'd and passes the matching dtor handle. When refcount hits 0: `_call_dtor(dtor, ptr)` if non-nil, then `Free(ptr)`. `_call_dtor` is a compiler intrinsic — it lowers as `OP_CALL_HANDLE` (handle dispatch: `handle.vtable.call(handle.data, ptr)`), the same shape every other function-value invocation uses. There is no C stub.

**Why handle, not raw fn pointer**: handles are the cross-mode dispatch currency (Phase 4 of `plan-uniform-native-fnptrs.md`). The same dtor handle works whether the call site is in native code or bytecode VM, because the handle's vtable.call shim adapts the calling convention. A raw fn pointer would force the VM to know how to invoke a native function — which it can't, without per-target lowering. Pre-2026-06-01 the closure-dtor and impl-vtable dtor slots stored raw fn pointers and the OP_CALL_HANDLE dispatch byte-pun-read the dtor function's own machine code as a `{vtable, data}` struct. arm32-baremetal hung; LP64/aa64/x64 happened to exit cleanly via a random jump after the dispatch went off into the weeds, so the bug was silent (captured `@T` / `@[]T` references leaked on every target). See `claude-todo-done.md` for the post-mortem.

**Per-dtor triple**: every dtor function `<name>` ships with a `(shim, vt, handle)` triple emitted by `emitFuncValueVtables` (`pkg/binate/codegen/emit_funcvals.bn`):
- `@__shim.<name>(i8* %data, i8* %ptr) { tail call void @<name>(i8* %ptr); ret void }` — the data-stripping wrapper that gives the dtor body the standard `void(i8*)` shape.
- `@__vt.<name>` = `%BnVtable { i8* null, i8* &__shim.<name> }` — slot 0 (a dtor-for-the-dtor) is null since the dtor itself has no captures; slot 1 is the call shim.
- `@__handle.<name>` = `%BnFuncValue { i8* &__vt.<name>, i8* null }` — the address passed as `dtor` to RefDec / ZeroRefDestroy.

The triple's emission is driven from three places:
- `emitManagedPtrRefDec` (`pkg/binate/ir/gen_util_refcount.bn`) emits `EmitFuncHandle(qualifiedDtorName)` for each `@T`-with-dtor cleanup → `OP_FUNC_HANDLE` instruction → triple emitted by the main `emitFuncValueVtables` walk.
- `emitFuncValueVtables` pre-pass walks `m.Funcs` for `IsManagedFuncValue` closures whose struct needs destruction (covers `@func` capturing literals).
- `emitFuncValueVtables` pre-pass walks `m.Impls` for non-empty `DtorFuncName` (covers `@Iface` cleanup via impl vtables).

All three feed the same emission path; dedup is by mangled name.

**Where the dtor handle is stored**:
- For `@T` cleanup: `emitManagedPtrRefDec` resolves the handle by name and passes it directly.
- For `@func` cleanup: the @func value's vtable slot 0 stores the closure dtor's handle address; `emitManagedFuncValueRefDec` extracts it via `OP_FUNC_VALUE_DTOR` (load vtable[0]) and passes it to `OP_REFDEC_DTOR`.
- For `@Iface` cleanup: the impl vtable `@__ivt.<R>__<I>[0]` stores the receiver dtor's handle address; `emitManagedIfaceValueRefDec` extracts it via `OP_IFACE_DTOR` (load vtable[0]) and passes it to `OP_REFDEC_DTOR`.

**Destructors are separate from free_fn**: The `free_fn` in the management header is for custom allocator support (different deallocation strategies). The destructor handles deinitialization (decrementing managed fields). Deinitialization ≠ deallocation.

**Every type that `NeedsDestruction` gets a dtor** (generated at the IR level, backend-agnostic):
- **Struct dtors** (`__dtor_<Name>`): walks fields, RefDec's `@T` fields with pointee dtor handle, calls managed-slice/array/struct dtors for inline fields.
- **Managed-slice dtors** (`__dtor_ms_<elemType>`): checks `Refcount(backing) == 1`; if last reference, iterates `backing_len` elements starting from `refptr` (backing start, NOT `data`) calling element cleanup, then RefDec's the backing. If not last reference, just RefDec's backing without touching elements. Generated even when elements don't need destruction (just RefDec backing).
- **Array dtors** (`__dtor_arrN_<elemType>`): iterates N elements calling element cleanup. Per-size function (trampoline design for future interface vtables).
- **Anonymous struct dtors** (`__dtor_anon_<type1>_<type2>_...`): named by field type sequence. Hash fallback (`__dtor_anon_h<hex>`) for names exceeding 128 characters.
- **Closure-struct dtors** (`__dtor___closure___funclit_<n>` for capturing literals): generated alongside the closure struct in `gen_func_lit.bn`'s `registerClosureStructForCleanup`; walks the captured fields and RefDec's each managed capture.

**All dtors use `weak_odr`** for linker deduplication across modules. (Earlier `linkonce_odr` was switched to `weak_odr` so the symbol stays visible to the linker for cross-TU emission of the same triple.)

**Destructor is statically known**: at every RefDec call site (for `@T` of a known struct type), the type is known, so the dtor handle is resolved at compile time. For `@func` / `@Iface`, the dtor handle is loaded from the value's vtable at runtime — the vtable is statically known per literal / per impl, so still no runtime type info.

**Future optimization**: move/transfer ownership semantics to avoid refcount bumps (e.g., last use of a managed pointer skips the bump/decrement). Pure optimization, doesn't change semantics — deferred.

### Minimize C runtime — DIRECTION

The C runtime (`binate_runtime.c`) should shrink over time, not grow. The goal is to write as much as possible in Binate itself.

**What must stay in C** (or equivalent FFI):
- Wrappers for OS interfaces where the C standard library provides the stable ABI (file I/O, memory allocation — `malloc`/`free`/`realloc`).
- Eventually, even these can be replaced by direct syscalls (as Go does on Linux), but that's more work and amounts to replacing C with assembly.

**What should move to Binate** (status as of 2026-03-30):
- ~~Refcount management (inc, dec, free dispatch)~~ — DONE (pkg/rt: Alloc, RefInc, RefDec, Free)
- ~~Managed-slice creation~~ — DONE (pkg/rt: MakeManagedSlice)
- ~~Bounds checking~~ — DONE (pkg/rt: BoundsCheck, c_bounds_fail stub)
- ~~Box wrappers~~ — DONE (pkg/rt: Box = Alloc + c_memcpy)
- Slice operations (get, set, slice expressions) — still in C runtime
- String-to-chars conversion, printing — still in C runtime

**End state**: declare external C library functions via compiler annotations (or a natural FFI mechanism) and remove the C runtime entirely. Pure Binate systems — where everything is written in Binate — should be possible. The C dependency exists only insofar as it's the practical way to talk to the OS; it's not a permanent architectural choice.

**`__c_call` void return — DECIDED 2026-06-19 (in progress)**: a void-returning C function is written with the **string literal `"void"`** in `__c_call`'s return-type position (`__c_call("sym", "void", args…)`), where a Binate type would otherwise go. Struct returns remain unsupported (pass a pointer to an out-parameter). Until it lands, a void C function still uses the throwaway-scalar-return-and-discard workaround. Spec: §16.9 `pkg.ccall`, `binate.ebnf` `CCallRet`.

### Language specification — primary spec is minimal — DECIDED

Binate will have **multiple specs**, not one monolith. The split:

**Primary language spec.** Syntax, type system, semantics, and *only* the packages intrinsically tied to the language implementation. Concretely:
- `pkg/rt` — the runtime contract. The current `pkg/rt` is a grab-bag and needs a review pass: what truly belongs there as language-runtime, what doesn't, and what should be made internal/private (no `.bni` surface) versus part of the normative spec.
- A future reflection / introspection package (yet-to-be-designed). Packages should automatically produce reflection information, including a list of function values for exported functions. The primary spec describes the shape of that introspection surface.

**Minor secondary spec — testing.** `_test.bn` file naming/packaging convention and `pkg/builtins/testing`. The primary spec needs at least a one-line note that user files cannot be named `*_test.bn` (reserved). Everything else about the testing convention is secondary and may be folded into the primary spec or kept separate — TBD.

**Major secondary spec(s) — standard library.** I/O, containers, formatting, string utilities, etc. Possibly split across multiple specs by area. The bar for "is this in the stdlib spec" is high enough that omitting it is the default.

**Rationale.** Binate is a systems programming language. It must be well-specified for environments where:
- `printf` makes no sense (no console, no output destination, or simply no support hooked up).
- There's no filesystem (bare-metal embedded).
- There's no process model.
- Threading, dynamic allocation, or other host-OS facilities aren't available.

A language whose primary spec embeds I/O assumptions (a `string` type, an output stream, a process model, …) becomes either unspecifiable on such targets, or requires the spec to be partially unimplemented on them. Neither is acceptable. Keeping the primary spec free of stdlib means target-specific subsets are conformant to the primary spec without contortion; the stdlib spec is what gets layered selectively per target.

**Implication for current code.** `pkg/rt` review (mentioned above) is a prerequisite to writing the primary spec. See `claude-todo.md` for the spec writeup and pkg/rt-review entries.

### Threading — DECIDED

**Single-threaded by default**, but threading-compatible:
- Language doesn't spawn or manage threads, but doesn't prevent OS-level threads
- Compiler must not optimize based on single-threaded assumptions: no reordering memory operations visible across threads, no assuming globals can't change externally
- Non-atomic refcounts (v1). Managed objects belong to one thread; cross-thread sharing requires explicit locks.
- Atomic refcounts as a possible v2 opt-in (per-type)

**Interrupt handlers / kernel context**:
- Constraint: don't manipulate managed objects in interrupt handlers
- Best practice: bump/queue work out of interrupt context (already standard kernel design)
- These constraints are much milder than e.g. Unix signal handler restrictions

### Error handling — DECIDED

No language-level error handling. No exceptions, no panic/recover. Errors are just values — return them as part of a tuple, check them, handle them. Convention, not language machinery.

Benefits:
- No hidden control flow, no stack unwinding
- Trivial across the compiled/interpreted boundary (errors are just return values)
- Go-style multiple returns provide clean ergonomics: `result, err := doSomething(x)`

### Diagnostics: errors only, no compiler/interpreter warnings — DECIDED

The compiler and the interpreter emit **errors only — never warnings.** A
construct is either accepted or rejected with a hard error; there is no advisory
middle tier in the build itself. Rationale: once a compiler has warnings, the
next demand is `-Werror`, then per-warning enable/disable flags, then the
fragility of code that builds clean under one warning set and breaks under
another, plus the CI churn as the warning set evolves — the same reasoning Go
followed in keeping its build warning-free. Advisory / style / footgun
diagnostics belong in **bnlint** (the separate linter), which has its own rule
names and is run, configured, and gated independently of a build. Examples
already in bnlint: `func-value-escape`, `managed-func-raw-capture`. (So "should
the compiler warn about X?" is always answered "no — if anything, a bnlint
rule.")

### Untyped pointers & casting — DECIDED

- **`*uint8`** is the opaque byte pointer type (equivalent to C's `void*`). Use `bit_cast` to convert to/from typed pointers. This is what `pkg/rt` uses for `Alloc`, `Free`, `RefInc`, `RefDec`, `Box`, etc.
- **`any`** is the empty interface type (2 words: data ptr + vtable ptr). `*any` is a pointer to an interface value — NOT equivalent to `void*`.
- **`cast(T, expr)`**: value conversion (e.g., `cast(int, myFloat)`). Explicit, required between named types.
- **`bit_cast(T, expr)`**: reinterpret bits. No conversion, no checking. The "I know what I'm doing" escape hatch.
- Both are builtins (like `make`), not functions — they take types as first arguments.
- **`sizeof(T)`**: size of type T in bytes. Returns `uint`. Compile-time constant. Takes a type, not an expression.
- **`alignof(T)`**: alignment requirement of type T in bytes. Returns `uint`. Compile-time constant. Takes a type, not an expression.
- For composite value types: `sizeof(*[]int)` = 2 words (the slice value itself), `sizeof(Stringer)` = 2 words (the interface value itself) — not the data they point to.
- **`present(x)`**: "does x hold something / is it set?" — for the nullable reference/view types: interface values (vtable set; honest about typed-nil), function values (vtable set — they have no `== nil`), pointers (non-null), and slices (`len > 0`). Returns `bool`. The sanctioned emptiness test where `== nil` is a footgun or disallowed.
- **`same(a, b)`**: reference identity — true iff `a` and `b` denote the same underlying thing: pointers (same address), interface values (same `{data, vtable}`), slices (same view `{data, len}`). Operands must have the same static type; function values (no canonical identity) and value types are excluded. Returns `bool`. See `plan-same-builtin.md`.
- **Builtins are keywords** (not predeclared names): `make`, `make_slice`, `box`, `cast`, `bit_cast`, `len`, `unsafe_index`, `sizeof`, `alignof`, `present`, `same`. The type-taking ones (`make` / `cast` / `sizeof` / …) can't be parsed as regular function calls; the value-taking ones (`len` / `unsafe_index` / `present` / `same`) are keywords so they stay reserved and uniformly lowered.

### Const-ness — DECIDED (revised 2026-06-01; see `plan-const-readonly.md` for the migration sequence)

Two related but distinct concepts: compile-time constants (no storage)
and read-only access (a property of types).  Each has its own keyword
in the surface syntax.

**Compile-time constants — `const`**: a `const` declaration introduces
a name bound to a compile-time-knowable value.  No storage, no address
(`&X` is an error).  Each use of X is replaced by the value at
codegen time.

- Scalar types only: `int` / sized ints / `char` / `bool` /
  `float32` / `float64`.  Strings, slices, arrays, structs, pointers,
  and other composite-storage types use `var` (see below) with
  `readonly` modifiers as appropriate — they need storage and aren't
  immediate-replaceable.
- Must have a value.  Iota groups count: bare names inherit the
  previous spec's expression with iota re-evaluated.
- Visibility by location: declared in `.bni` → exported; declared in
  `.bn` → package-private.  Cannot be declared in both files for the
  same name (no "extern const" — consts resolve at every use site,
  there's no symbol to link).

Examples:
- `const x = 5` — untyped int const, value 5.
- `const x int = 5` — typed int const.
- `const ( A int = iota; B; C )` — A=0, B=1, C=2.

**Variables — `var`**: introduces a named variable backed by storage in
the package's compilation unit.

- Storage always lives in the package's `.bn` file.  The `.bni` form
  is an extern declaration ("this var exists in this package's `.bn`
  and is exported") — must NOT carry an initializer.
- `.bni` and `.bn` decls for the same name must agree on the full
  type, including any `readonly` modifiers.
- Type may carry `readonly` modifiers (see below); a value at a
  readonly slot can't be written through its name.  Linter warns
  on a `readonly` global var declared without an initializer
  (zero-forever, almost always a bug).
- `&X` is always legal.  For `readonly T`-typed X, the result type
  is `*readonly T`.
- Default-init (no `=`) is allowed at the language level for any
  type; the storage is zero-initialized.

Examples:
- `var x int` — mutable global, zero-initialized.
- `var x int = 5` — mutable global, initialized to 5.
- `var x readonly int = 5` — readonly global (writes to x are
  type-checker errors).
- (`.bni`) `var Version readonly *[]readonly char` — exported,
  readonly declaration.
- (`.bn`) `var Version readonly *[]readonly char = "bnc-0.0.6-pre"`
  — matching storage definition.

**Read-only types — `readonly T`**: a type modifier marking "the value
at this storage location can't be written through this access path."

Left-to-right reading; each modifier applies to the thing immediately
to its right:
- `readonly *int` — readonly pointer to int (pointer can't change,
  but *p can).
- `*readonly int` — pointer to readonly int (data can't change).
- `readonly *readonly int` — both.
- `*[]readonly *int` — slice of readonly pointers to int.
- `*[]*readonly int` — slice of pointers to readonly int.

**`readonly` on function parameters**: the outermost `readonly` on a
parameter type is local discipline, not part of the function's type
signature — it says "this parameter can't be reassigned inside the
body," useful for self-documentation, ignored for signature matching.

**Deep immutability**: skipped for v1.

**Receivers — five receiver kinds**:
1. readonly value — read-only copy
2. readonly raw pointer — read-only view, no refcount
3. readonly managed pointer — read-only view, with refcount
4. raw pointer — mutable, no refcount
5. managed pointer — mutable, with refcount

Value receivers are always readonly (mutating a copy is pointless).

**Auto-conversion**: more-permissive → more-restrictive:
- managed → raw → readonly raw
- managed → readonly managed → readonly raw
- any pointer → value/readonly value (by copy)

**Method dispatch keys off OBJECT-constness, not handle-constness.** Whether
a receiver value may call a method depends only on whether the OBJECT it
denotes is read-only — never on whether the *handle* (pointer) is read-only.
Per the left-to-right modifier reading above, `readonly @Box` is a read-only
*handle* to a *mutable* `Box` (the `readonly` binds the pointer), so it
dispatches to ANY method, including one declared with a mutable `*Box` / `@Box`
receiver. `@(readonly Box)` — and `*readonly Box`, and a `readonly Box` value —
denote a read-only *object*, so they may only call a method whose receiver is
itself const-pointee (`*readonly Box` / `@readonly Box` / `readonly Box`);
calling a mutable-receiver method (`*Box` / `@Box`) on a read-only object is
rejected (it could mutate the object). There are deliberately NO const-method
annotations — the receiver type in the method declaration is the sole statement
of whether the method needs a mutable or a read-only object.

**`impl` declarations specify receiver type.** The receiver kind
determines what pointer/value types can satisfy the interface.
Interfaces themselves say nothing about readonly-ness.
- `impl Stringer for FileHandle` with readonly receiver →
  `*readonly FileHandle` satisfies `Stringer`
- `impl Stringer for Widget` with mutable receiver → only
  `*Widget` satisfies `Stringer`, not `*readonly Widget`

This means the same interface can be implemented with different
receiver kinds by different types. No extra syntax needed in
interface declarations.

### Volatile — DECIDED

Not a type qualifier (unlike C). Instead, builtin functions for volatile reads/writes. Volatility is at the point of access, not on the type. Avoids viral type annotations, keeps the type system simpler, and makes every volatile access explicit at the use site.

### Type system — DECIDED

Statically typed. Compiled and interpreted modes use the **same type system and rules**. The difference is only *when* checks run.

**Primitive types — DECIDED**:
```
int, uint                           // platform word size
int8, int16, int32, int64           // fixed-width signed
uint8, uint16, uint32, uint64      // fixed-width unsigned
float32, float64                    // floating point
bool                                // true, false
byte = uint8                        // alias
char = uint8                        // alias
```
- `int`/`uint` are the platform's natural word size (32-bit on 32-bit targets, 64-bit on 64-bit)
- `int64`, `uint64`, `float32`, `float64` are optional subject to hardware support
- No `uintptr` — `uint` serves this purpose (pointer size = word size on all target platforms)
- No unqualified `float`

### Forward references & REPL model — DECIDED

**Key insight**: the REPL must distinguish between **retained mode** and **immediate mode** entries.

- **Retained mode**: definitions (functions, types, structs). Parsed and stored, but full validation is deferred until dependencies are available or validation is triggered. This is what source files contain — a compiled program is entirely retained mode.
- **Immediate mode**: expressions/statements to execute now. Fully checked at entry time. Can reference validated retained definitions.

**In compiled / non-REPL interpreted mode**: everything is retained. The compiler/interpreter sees the whole program (or file set), validates everything, then execution begins via an external call (e.g., `main()`). No forward reference problem — the whole program is available.

**In the REPL**: retained and immediate entries are interleaved. Retained definitions can sit pending until their dependencies are met. Immediate entries trigger checking of anything they depend on. Redefinition of retained entries is allowed (to fix mistakes).

**No forward declarations required.** The retained/deferred-validation model handles forward references naturally. This is more ergonomic than requiring prototypes, and keeps compiled/interpreted semantics identical — only the *timing* of validation differs.

**Redefinition in the REPL**: supported even after use. Two modes depending on compatibility:

- **Compatible redefinition** (same signature/type, different body): **replace**. The name table updates to the new definition. All existing references (including captured function pointers, even in compiled code) continue to work since the signature is unchanged.
- **Incompatible redefinition** (different signature/type): **shadow**. The old definition stays alive (refcounted) for anything that captured it. New code sees the new definition. Warn if old definition has outstanding references (refcount > 1).
- **Forced shadowing**: an escape hatch to force shadowing even for compatible redefinitions (syntax TBD).

**Deferred validation and shadowing**:
- When `f` is pending (waiting for `g`) and `g` is defined: if `g` matches what `f` expects, `f` validates. If not, `g` is just a different `g` from `f`'s perspective — `f` remains pending.
- If `f` never gets a compatible dependency, it stays pending. Error surfaces when someone tries to call `f`.

Same principle for struct/type redefinition: existing instances retain the old layout/type definition.

### Type conversions & literals — DECIDED

**Explicit casts required** between named types (Go-style). No implicit conversions between e.g. `int` and `uint`.

**Untyped literals**: literals have no inherent type and coerce to any compatible type from context. Unlike Go, this does NOT extend to named constants — only literals.
- `123` → `int`, `uint`, `i32`, `byte`, etc.
- `3.14` → `f32`, `f64`, etc.
- `"abc"` → `[3]readonly char` (natural type), `@[]readonly char` / `*[]readonly char` (slice, len=3)

**Default types** (when context is ambiguous, e.g., `x := 123`):
- Integer literals: `int`
- Float literals: `float64`
- String literals: `@[]readonly char` (default type — a managed-slice view of the static data; verified against the implementation, `pkg/binate/types` `defaultStringLitType`). `*[]readonly char` is also an allowed (raw borrow) target.
- Bool literals: `bool`

**Literal overflow**: assigning a literal to an explicit type that can't hold it is a compile error (`var x uint8 = 256` → error). Literals are checked at compile time for fit.

**Integer literal value range and constant-expression arithmetic — DECIDED 2026-05-15**:

Integer literals and constant expressions follow the rules below. The model is a pragmatic, fixed-width adaptation of Go's untyped-const semantics — no bignum, but coherent rules for the int64 / uint64 union range.

- **Parseable range**: an integer literal must have a value in `[-2^63, 2^64-1]` (the union of `int64` and `uint64` ranges). Outside that range → parse error. So `0xFFFFFFFFFFFFFFFF` (= `2^64-1`) is parseable; `0x10000000000000000` (= `2^64`) is not. All bases (decimal, hex, binary, octal) parse to the same value space.

- **Default type when context is ambiguous** (e.g. `x := 1`, or a function arg without inference): `int`. `int` is target-width: 32 bits on 32-bit targets, 64 bits on 64-bit targets. A literal that doesn't fit in the target's `int` cannot use the default — the user must give it an explicit context (`var x int64 = 100000000000` or `cast(int64, 100000000000)`).

- **Type from context**: assignment, function argument, cast, and return all provide a target type. The constant's **mathematical value** must fit in the target type's range. Hex `0xFFFFFFFFFFFFFFFF` has mathematical value `2^64-1` (a positive integer); it fits in `uint64` but not in `int64`.

- **Constant-expression arithmetic** operates on **abstract values** (signed integers in the conceptual sense), internally represented at `int64`-or-`uint64` precision (union range `[-2^63, 2^64-1]`). Each operation computes the mathematical result. If any intermediate result exceeds the union range, the const-expr is **rejected at type check** — no silent wrap. Examples:
  - `1000 - 1000` → 0 → fits any type that includes 0 ✓
  - `0xFFFFFFFFFFFFFFFF - 1` → `2^64-2` → fits `uint64` ✓
  - `0xFFFFFFFFFFFFFFFF + 1` → `2^64` → exceeds union range → rejected
  - `0xFFFFFFFFFFFFFFFF + 1 - 1` → also rejected (intermediate overflows; no bignum to absorb it)
  - `0xFF * 0xFF * 0xFF * 0xFF` → fits `uint64`, then assignability check against target type

- **Sign handling**: a constant whose bit pattern fits in `int64` is treated as a signed integer (mathematical value, possibly negative). One that requires `uint64` (value > `int64`-max) is treated as a non-negative integer. The fit-check against a signed target accepts the mathematical value if it's in the signed range; against an unsigned target if it's in the unsigned range. So:
  - `var x uint64 = -1` → mathematical value -1 → does not fit `[0, 2^64-1]` → rejected
  - `var x int64 = 0xFFFFFFFFFFFFFFFF` → value `2^64-1` → does not fit `[-2^63, 2^63-1]` → rejected
  - `var x uint64 = 0xFFFFFFFFFFFFFFFF` → value `2^64-1` → fits `[0, 2^64-1]` → accepted

- **Mixed signed/unsigned in const-expr**: arithmetic operates at abstract precision, so `0xFFFFFFFFFFFFFFFF + (-1)` evaluates to `2^64-2` (a positive value) and is fine in a `uint64` context. Implementation-wise the const-expr evaluator stores `(uint64 magnitude, sign bool)` and does sign-magnitude arithmetic with overflow detection.

- **Acknowledged limitation**: chains like `0xFFFFFFFFFFFFFFFF + 1 - 1` whose final value is representable but whose intermediates overflow the union range are rejected. The fix in user code is to reorder or break up the expression. Go avoids this via bignum; we deliberately don't, in exchange for tractable implementation.

**Cast semantics**: `cast(T, expr)` on a typed (non-constant, runtime) value wraps/truncates — hardware semantics, well-defined; `cast(uint, x)` where `x` is a runtime int wraps to UINT_MAX. **Casting an out-of-range CONSTANT to an integer type is a compile error** (`cast(uint, -1)`, `cast(uint8, 256)`): the same mathematical fit-check as an assignment/argument target (§ untyped-int fit rules above), enforced in `types.checkCastConstFits` (reached from `checkBuiltinCall`'s CAST arm and from the array-dimension walk `validateDimCasts`, since array dims bypass `checkExpr`). **A `cast` does NOT launder a constant** (DECIDED 2026-06-17): `cast(T, <constant>)` is *still a constant*, so `cast(int8, cast(int, 200))` and `const C int = 200; cast(int8, C)` are fit-check errors exactly like the direct `cast(int8, 200)` — there is **no** constant→runtime escape through `cast`. (Rationale: a `cast` or a `const` declaration is the only way to give a literal a type, so if `const C int8 = 200` is an error, then so must every cast that names that same out-of-range value, however nested.) To produce an out-of-range value on purpose: **mask** for a different-size truncation (`cast(uint8, N & 0xff)`), or **`bit_cast`** for a *same-size* reinterpret (`bit_cast(uint64, cast(int64, neg))`; `bit_cast` can't target a narrower type — `bit_cast(uint8, 300)` is a size-mismatch error). The fit-check folds the operand via `evalConstIntValue` (a direct/untyped-int literal uses its exact bignum `LitMag`/`LitSign`; a const-ident / arithmetic / nested cast routes through a host `int`, so a const whose magnitude is ≥ 2^63 can be mis-judged — a tracked limitation, claude-todo.md). This reverses the earlier (pre-2026-06-17) "launder through `cast(int, …)`" idiom, under which `cast(uint8, cast(int, 300))` wrapped to 44; that now errors, and the compiler's own code + test 650 were updated to mask / `bit_cast` accordingly. **float→int SATURATES** (ratified 2026-06-12): a value above `T`'s max (incl. `+Inf`) → `T_MAX`, below its min (incl. `-Inf`) → `T_MIN` (0 for unsigned), `NaN` → 0; in-range truncates toward zero. Well-defined and identical across every backend + the VM (lowered once in shared IR-gen, `emitGuardedFloatToInt`, mirroring the shift-overshift guard). Refines Go (whose float→int out-of-range is "implementation-specific, no panic") by pinning a defined value; matches Rust `as` / WASM `trunc_sat`.

**No implicit null termination (revised 2026-04-01)**: string literals contain exactly the characters specified, with no hidden null terminator. `"abc"` is stored as `{'a','b','c'}` (3 bytes), natural type `[3]readonly char`, default type `@[]readonly char` with `len()` = 3. (Note: `*[]readonly char` is also allowed — a raw slice borrowing from static data.) If a null terminator is needed (e.g., for C interop), include it explicitly: `"abc\0"` (4 bytes, natural type `[4]readonly char`). Null termination for C interop can also be handled by library functions. This replaces the previous design where string literals always included a hidden null terminator beyond the slice view — that was too complicated to reason about in practice (tracking which slices had a null beyond their bounds was impractical).

**No `string` type.** `string` does NOT exist as a type in Binate. String literals are untyped constants with natural type `[N]readonly char` and default type `@[]readonly char`. Allowed targets: `@[]readonly char` (borrows static data, zero cost), `@[]char` (allocate+copy — managed-slice owns its backing, so mutation is safe), `*[]readonly char` (raw slice borrowing static data), `[N]readonly char` / `[N]char` (array copy). NOT allowed: `*[]char` (raw slice can't own a mutable copy, and borrowing static data mutably is unsound). This generalizes to all slice/array literals. (The bootstrap uses `*[]char` as a stand-in since it lacks readonly types.) Language targets small systems where full UTF-8 support is too heavy to justify a separate type.

**Adjacent string-literal concatenation (C-style).** Two string literals with only whitespace between them are glued into one literal at lex/parse time:
```binate
return errMsg(pos, "expected 'func', 'type', 'var', 'const', "
                   "'import', or identifier at top level")
```
produces a single literal `"expected 'func', 'type', 'var', 'const', 'import', or identifier at top level"`. Pure compile-time operation — no runtime `Concat` call, no allocation, no const-folding pass needed. Binate doesn't have a string `+` operator (no `string` type), and runtime concatenation via `bootstrap.Concat` allocates every time the expression is reached, which is wrong for error-message literals that may fire rarely but still live in tight code paths. Adjacent concat is the right tool for splitting one logical string across source lines cleanly. See `differences-with-go.md` for why Go chose differently.

### Type system richness

**Generics**: implemented — monomorphized, interface-constraint-based (see the Generics section below).

**Sum types**: not included. The type calculus and inference complexity is too high for the simplicity goal. Tagged unions (defined in one place, fixed variants) are a simpler alternative — to discuss separately.

**Null/optionality — DECIDED (v1)**:
- v1: all pointers are nullable by default (C-style)
- Future: non-nullable pointer types via `!` annotation (e.g., `!*MyStruct` or `*MyStruct!` — exact syntax TBD)
- **Design constraint**: don't make choices in v1 that would block adding non-nullability later. Specifically:
  - Don't assume every type has a zero value in core semantics
  - Don't design initialization rules that conflict with future definite-initialization analysis
  - Ensure null checks (`if p != nil`) are clean and expressible — they become compiler-checked patterns later

### Maps / hash tables — DECIDED

**No built-in map type.** Maps are a library/package concern, provided via generics.

Rationale:
- Built-in maps (like Go's) are "magic" — special deletion syntax, special iteration, can't take address of elements. This kind of special-casing conflicts with Binate's minimal-core philosophy.
- With generics, `Map[K, V]` in a standard package is just as ergonomic, with hashability/comparability expressed via interface constraints.
- Library maps allow implementation flexibility (hash map, tree map, open addressing, etc.) rather than locking in one implementation.
- Binate targets small systems — not every program needs a hash table. Import only if needed.

Before generics landed, two viable approaches were considered:
- Concrete map types per key/value combination (`StringToInt`, `StringToType`, etc.) — more code but translates cleanly to generic `Map[K, V]` later.
- Sorted arrays + binary search — simpler, sufficient for bootstrap-scale symbol tables.

### Enums — DECIDED (revised: no first-class enums)

**No first-class enums.** Use `type` + `const` blocks with `iota` (Go's approach).

First-class enums were considered but dropped because:
- Enum values need a namespace, creating scoping problems with anonymous enums
- Named enums would break the anonymous-type parallel that structs/interfaces have
- The special casing and inconsistencies aren't worth the benefit
- `type` + `const` + `iota` covers the practical use cases

```
type Opcode uint8

const (
    OpAdd Opcode = iota    // 0
    OpSub                   // 1
    OpMul                   // 2
)

type Flags uint32

const (
    FlagRead  Flags = 1 << iota   // 1
    FlagWrite                      // 2
    FlagExec                       // 4
)
```

- `iota`: predeclared constant, zero-based index within a `const (...)` block
- Omitting type and expression repeats the previous spec (with `iota` incremented)
- Distinct types require `cast()` to convert — provides type safety
- Free casting between underlying integer and the type (systems-friendly)
- No exhaustiveness checking (linter could recognize the pattern)

**Discriminated/tagged unions**: punted for v1. Desirable but not essential.

### Interfaces — IMPLEMENTED

Full design lives in `plan-interface-syntax-revision.md` (RATIFIED 2026-05-01). Implemented end-to-end: `interface X { ... }` declarations, bare-interface-name as a type expression for `*Iface` / `@Iface`, `impl T : Iface`, vtable dispatch, and generics atop the same machinery.

Cross-package interfaces are feature-complete (canonical (R, I) mangling, qualified iface refs, IR-gen impl-table threading, orphan-free duplicate impls). Mixed-mode iface dispatch (compiled ↔ VM across packages) is a parked follow-up.

**Core design:**
- Interfaces are declared top-level with a set of method signatures, using a dedicated `interface` keyword (not `type X interface { ... }` — which is dropped):
  ```
  interface Stringer {
      toString() *[]readonly char
  }
  ```
- Bare `Stringer` is **not** a type expression — it's a referenceable name only. Usable in `*Stringer` / `@Stringer`, in `impl T : Stringer` decls, and on the LHS of an interface alias.
- No anonymous interfaces. Interfaces are always declared, top-level, and named.
- `impl` declarations are separate from both the struct definition and the method definitions.
- Methods use Go-style receiver syntax, defined outside impl blocks — not tied to a single file.
- Vtable-based dynamic dispatch; compiler may devirtualize as an optimization.
- Interface values follow the raw/managed pattern (mirroring the slice migration to `*[]T` / `@[]T`):
  - `*Stringer` — raw interface value: `(raw ptr to data, vtable ptr)`. No refcounting; caller keeps data alive.
  - `@Stringer` — managed interface value: `(managed ptr to data, vtable ptr)`. Keeps data alive via refcounting.
- Both are 2-word value types (small, copyable). Pointers to interface values follow normal pointer rules: `**Stringer`, `*@Stringer`, `@(@Stringer)`.
- **No `*readonly Stringer` / `@readonly Stringer`** — analogous to `*readonly []T` not being a thing. `readonly` qualifies element types in slice spellings (`*[]readonly T`); the interface-value spelling has no analogous slot. Readonly-restricted dispatch is expressed at the impl level (impls with readonly receivers).

**Construction-site conversions — explicit only**: when constructing an interface value from a non-interface source, **no implicit conversions** happen. No implicit copies, no implicit `&t`, no implicit `box(t)`. The user writes the conversion explicitly, because the interface value can outlive the source. (Method-call receiver smoothing is unaffected — `t.Foo()` auto-takes `&t` for `*readonly T` receivers, since the receiver lifetime is bounded by the call.) Concrete table:
- `T → *Iface` requires explicit `&t` (then routes if impl matches `*T` / `*readonly T` / value receiver)
- `T → @Iface` rejected — write `box(t)` first to get `@T`, then `@T → @Iface`
- `*T → @Iface` rejected — `*T` can't promote to `@T`
- `@T → *Iface` and `@T → @Iface` work directly (managed acts as raw data ptr; `@T → @Iface` if impl matches)

**Built-in implicit interfaces**: a small, closed, language-defined set of interfaces implicitly implemented by all types. `any` is the primary one — usable as `*any` (type-erased raw) and `@any` (type-erased managed). Others may be added (e.g., `Sized`) but only by the language spec.

**`Self` type in interface declarations** (implemented 2026-05-13): a reserved type identifier valid only inside interface method signatures. Substituted with the receiver type at impl-collection time; methods that mention `Self` in non-receiver positions are rejected at interface-value call sites (object-unsafe per Rust's terminology) and reachable only through generic constraints where the implementing type is statically known. See dedicated section above for the full design.

**Interface extension** (implemented 2026-05-13, plan-interface-embedding.md): syntax `interface X : I1, I2, ... { methods }`, mirroring `impl T : I1, I2`. Parents are listed once between `:` and `{` — no interspersing parents and methods, no anonymous embedding (Binate has no anonymous interfaces). Empty body is allowed. Distinct from aliases: `interface X = A` is an alias (same identity); `interface X : A {}` is a distinct interface that requires exactly A's methods. `impl T : Child` transitively satisfies all ancestors. `*Child → *Parent` is a static, nominal upcast — no runtime query (Binate is nominally typed; there is no Go-style structural satisfaction check). Cross-package extension (parent in another package) works the same.

**Vtable layout for extension** (per `claude-plan-1.md` § 2.3 and `claude-discussion-detailed-notes.md` § "Interface Extension"): the vtable for `(R, X)` where `interface X : I1, I2 { own1; own2 }` is the concatenation `[any-block][full vtable of (R, I1)][full vtable of (R, I2)][R's own1, own2]`. All interfaces implicitly extend `any`, so every interface vtable starts with the `any`-block at offset 0 — holds the destructor pointer and is the natural home for further language-defined slots (e.g., a `*TypeInfo` pointer if RTTI is added). Layout is recursive: each parent's "full vtable" itself starts with its own `any`-block. Conversion `*X → *Parent` is a fixed compile-time pointer offset; no swap, no lookup. Some `any`-block content is duplicated at every nested origin in exchange for uniform fixed-offset conversion.

**Interface aliases**: `interface X = Y` for nominal-equivalent aliasing of interface names. `MyStringer` and `Stringer` are the same interface; `impl T : MyStringer` is indistinguishable from `impl T : Stringer`. There is *no* newtype-style "make this a distinct interface that happens to share the shape" form. Note: `type X = Y` aliases type expressions, so `type X = @Stringer` / `type X = *Stringer` work as type aliases; `type X = Stringer` (bare) is a type error.

**Five receiver kinds** (per `claude-discussion-detailed-notes.md` § 6.5):
1. readonly value
2. readonly raw pointer
3. readonly managed pointer
4. raw pointer
5. managed pointer

Receiver-kind preference (informational, not a hard rule): `*T` and `*readonly T` are the common cases — caller guarantees the receiver's lifetime during the method call. `@T` receivers are for impls that need to *retain* the receiver. Value receivers operate on a copy.

**Receiver smoothing at method call sites**: compiler auto-converts safe-direction at *method-call* sites. Cannot auto-promote raw → managed. Distinct from interface-value construction, which never auto-converts (see above).

**Separate `impl` for types defined elsewhere**: naturally supported by the model. Scoping rules (who can declare an impl) TBD.

**Package interface files** (`.bni`): contain the public API of a package — type definitions, function signatures, constants. Bodies are omitted for functions (except generics, which need bodies for instantiation).

**Struct definitions in `.bni` files**: A struct fully defined in the `.bni` is the **authoritative definition**. It does NOT need to be redefined in the `.bn` files. The `.bni` definition is compiled as part of the package — the compiler processes both `.bni` and `.bn` files.

**Forward struct declarations**: A `.bni` can declare `type Foo` (no body), meaning "Foo exists but the full definition is in the `.bn` files" — analogous to C's `struct foo;` forward declaration. The type stays opaque to importers (they hold pointers/handles but can't read fields or take its size).

**No full redeclaration across `.bni`/`.bn` — DECIDED, enforced**: a type may be declared *full* at most once per package. The legal shapes are: full in the `.bni` only (transparent); a forward `type T` in the `.bni` + a full definition in one `.bn` (opaque export); or a full definition in one `.bn` with no `.bni` (package-private). A full definition in *both* the `.bni` and a `.bn`, or two full definitions across `.bn` files, is rejected by the checker (`checkTypeRedeclaration`); generics included.

**Lone forward declarations are legal**: a `type T` forward decl with NO full definition anywhere in the package is a *pure opaque type* whose layout is defined outside Binate (C, assembly, or the runtime). Callers hold `*T`/`@T` only (as above). There is deliberately **no "dangling forward" check** requiring a forward decl to be paired with a full definition — it would be unsound: Binate compiles a package at a time and sees a *dependency* only through its `.bni`, so a dependency's forward decl always looks unpaired; and pure opaque types are a legitimate pattern. See `plan-type-redecl.md`.

**Impl syntax — DECIDED**: `impl ReceiverType : Interface, Interface2, ...`
- Receiver-type-first, colon separator, comma-separated interfaces.
- Receiver kind is specified on the receiver type:

```
impl FileHandle : Stringer           // value receiver
impl *FileHandle : Writer, Reader    // raw pointer receiver
impl @FileHandle : Retainable        // managed pointer receiver
impl *readonly FileHandle : Stringer // readonly raw pointer receiver
```

**Example sketch:**
```
interface Writer {
    write(buf *[]readonly char) int
    close()
}

type FileHandle struct {
    fd int
}

impl *FileHandle : Writer

func (f *FileHandle) write(buf *[]readonly char) int { ... }
func (f *FileHandle) close() { ... }
```

**Generics — DECIDED**:
- Generic types AND functions, with interface constraints on type parameters.
- No type inference for generics — always spell out type params fully (can relax in v2).
- Monomorphized.
- Type checking against interface constraints (checked once against the constraint, not per instantiation).

```
func sort[T Comparable](items *[]T) { ... }
sort[int](myArray)
```

**Boxing**: `box(value)` is the standard way to box a value into a managed allocation. Required for `T → @Iface` interface-value construction.

### `Self` type in interface declarations — DECIDED 2026-05-12

**Problem.**  Many natural interfaces have methods whose argument or return type is "the implementing type" — `Equatable.Equals(other Self)`, `Comparable.Compare(other Self) int`, `Cloneable.Clone() Self`, `Add.Plus(other Self) Self`.  Without a way to refer to "the implementing type" inside an interface declaration, these interfaces can't be expressed.  Three workarounds exist, none clean:

1. Concrete type in the signature (`Equals(other int) bool` on a per-type basis) — defeats the point of an interface.
2. Type-erased argument (`Equals(other *Equatable) bool`) — gives up monomorphization, defeats generic dispatch.
3. Generic interfaces (`interface Equatable[T] { Equals(other T) bool }` instantiated as `Equatable[int]`) — works, but requires the generic-interfaces piece of the generics work to land first, AND every interface that mentions Self carries a redundant type parameter that must equal the implementing type at every use site.

**Proposal.**  Add `Self` as a reserved type identifier valid only inside interface declarations.  Inside `interface I { ... }`, `Self` refers to "the type that will implement I."  At impl-collection time (`impl T : I { ... }`), each occurrence of `Self` in I's signature is substituted with T.  Mirrors Rust's `Self` and Swift's protocol-`Self` conventions.

**Examples.**
```
interface Equatable {
    Equals(other Self) bool
}

interface Comparable : Equatable {
    Compare(other Self) int  // <0, 0, >0
}

interface Cloneable {
    Clone() Self
}
```

`impl int : Equatable { func (a int) Equals(b int) bool { return a == b } }` — `b`'s declared type is `int`, matching Self under T=int.  The type checker validates that the impl's method signature matches the interface signature with Self substituted by the receiver type.

**Where Self is allowed.**
- Argument types in interface methods.
- Return types in interface methods.
- Inside composite types in those positions: `*Self`, `@Self`, `*[]Self`, `@[]Self`, `(Self, Self)`, etc.
- NOT in receiver position — the receiver is always Self implicitly (the impl's receiver type).
- NOT in interface-extension parents: `interface X : Comparable[Self]` makes no sense; use the `Self` propagation through method signatures alone.

**Interaction with interface values — DECIDED**.  Methods that mention `Self` in non-receiver positions are **rejected** when called through an interface value (only callable through a generic constraint where T is statically known).  Matches Rust's "object-safe" trait restriction.

The alternative — accepting `other: *Comparable` at the dispatch site — doesn't work cleanly: the impl is monomorphic on the receiver type (`int.Compare` takes `int`, not `*Comparable`), so calling `iv.Compare(otherIv)` would have to either (1) require every impl to provide a type-erased entry point alongside the monomorphic one, doubling the surface; or (2) do a runtime type assertion that panics on type mismatch (e.g., calling `int.Compare` with a `string` on the other side).  Both defeat the purpose.  Rejection at the call site is cleaner — `Self`-using methods are a generic-only capability.

The practical effect: `*Comparable` is a useful type for interface-value variables (you can hold them, store them, etc.), but you can't directly call `Compare` through one.  Comparison happens in generic code where T is known.  This matches the natural use pattern (you don't usually need heterogeneous comparison across two arbitrary `*Comparable` values).

**Interaction with generic constraints.**  `func sort[T Comparable](xs *[]T) { ... xs[i].Compare(xs[j]) ... }` — at instantiation T=int, `Compare`'s argument type is `int` (Self → T → int), the call type-checks naturally.  This is the headline use case.

**Interaction with interface extension.**  `interface Comparable : Equatable { Compare(other Self) int }` — Self in Comparable refers to the implementing type of Comparable, which is also the implementing type of Equatable (per the extension semantics).  Consistent.

**Open: Self in struct types.**  Could `type Foo struct { next *Self }` work as syntactic sugar for the self-recursive case?  Currently expressed via the type's own name: `type Foo struct { next *Foo }`.  Not motivated by anything; defer.

**Status.**  DECIDED 2026-05-12.  Implementation tracked downstream when the relevant slice lands (`plan-primitives-impl-interfaces.md` Slice 2b uses Self for `Comparable` / `Orderable` / `Hashable`; `plan-generics.md` constraint check uses it for `[T Comparable]`-style constraints).

### Syntax direction — DECIDED

C-family, leaning toward Go's direction (clean, minimal, familiar).

**Decided**:
- Type-after-name declarations (`x int` not `int x`) — more natural, especially for complex types
- `:=` short declarations — supported for ergonomics

- No semicolons (automatic insertion)
- **Multiple return values** (Go-style, not first-class tuples). First-class tuples were considered but reconsidered — they raise many type system questions (is `(int)` the same as `int`? named fields? nesting?) for limited practical benefit over Go-style multiple returns.
- Destructuring assignment for multiple returns: `x, y := foo()`
- **Tail-call return for multi-return functions**: `return f(...)` is allowed when `f` returns the matching tuple, mirroring Go. Per-result types must be assignable to the outer function's declared results.

**Pointer syntax — DECIDED**:
- `*T` = raw pointer to T (C-like)
- `@T` = managed pointer to T
- `&x` = take raw address of x. Operand must be **addressable** (have storage):
  a variable, a slice/array element, a struct field, a dereference, a composite
  literal (`&Point{1,2}` — it has a backing alloca). **NOT addressable** (a
  compile error): a named constant; a **named function** `&g` / `&pkg.f` (a func
  value is taken by naming the function directly — `var fp *func() = g` — not by
  addressing it); or a bare LITERAL — `&5` / `&3.14` / `&true` / `&'a'` / `&"s"`
  / `&nil` / a **func literal** `&func(){}`. (All match Go.)
- `make(T)` = allocate managed T (zero-init), returns `@T` (any type T, no size arg)
- `make_slice(T, n)` = allocate runtime-sized managed-slice, returns `@[]T`
- `box(expr)` = allocate managed copy of value, returns `@T` (e.g., `box(Point{x: 1})`, `box(42)`)
- Forward-compatible with non-nullable pointers (no intermediate nil state)
- `.` auto-dereferences (Go-style, no `->`)
- Implicit conversion from `@T` to `*T` (safe: managed is "narrower"). Never implicit `*T` → `@T`.

**Slice syntax — DECIDED**:
- `*[]T` = raw slice of T (two words: raw ptr, length) — the `*` prefix parallels `*T` for raw pointers
- `@[]T` = managed-slice of T (four words: data ptr, length, backing refptr, backing len) — syntactic sugar
- `*(*[]T)` = raw pointer to a raw slice (parens required — bare `*[]T` is raw slice, not pointer-to-slice)
- `@(*[]T)` = managed pointer to a raw slice (parens required)
- `*([N]T)` = raw pointer to array (parens required — bare `*[` is always slice sugar)
- `@([N]T)` = managed pointer to array (parens required, unchanged)
- `arr[low:high]` = slice expression (exclusive end, like Go)
- The `@[]` sugar is syntactic only: in generics, `@T` where `T=*[]int` means `@(*[]int)` (managed pointer to raw slice), not managed-slice.
- **Disambiguation rule**: `*` or `@` immediately before `[` is only valid as slice sugar. For pointer-to-array or pointer-to-slice, parens are required.
- **Rationale**: makes `*`/`@` consistently mean raw/managed for both pointers and slices. Visually distinguishes raw slices from Go slices (which look identical but have very different ownership semantics).
- (History: raw slices were originally spelled `[]T`. The change to `*[]T` was decided 2026-04-11 and the migration completed soon after.)

**Interface value syntax — DECIDED**:
- `Iface` = raw interface value (two words: raw ptr to data, vtable ptr)
- `@Iface` = managed interface value (two words: managed ptr to data, vtable ptr) — syntactic sugar, like `@[]T`
- `*Iface` = raw pointer to a raw interface value
- `*@Iface` or `*(@Iface)` = raw pointer to a managed interface value
- `@(Iface)` = managed pointer to a raw interface value (parens break the `@Iface` sugar)
- `@(@Iface)` = managed pointer to a managed interface value
- The `@Iface` sugar is syntactic only: in generics, `@T` where `T=Stringer` means `@(Stringer)` (managed pointer to raw interface value), not managed interface value.
- Interface values are regular value types — pointers to them, arrays of them, etc. all work. This avoids special-casing in generics (`*T` where `T=Stringer`), enables out parameters (`result *Stringer`), and keeps the type system uniform.

**Function syntax — DECIDED**:
```
func add(a int, b int) int { return a + b }
func divmod(a int, b int) (int, int) { return a / b, a % b }
func (p *Point) translate(dx int, dy int) { p.x += dx; p.y += dy }
func (p *readonly Point) distance() float64 { ... }
```
- No named return values (confusing, not best practice)
- No same-type param shorthand (e.g., no `a, b int`)

**Variadic functions — DECIDED**:
- Go-style `...T` syntax, packages args as a slice
- Raw interface variadics (`...Stringer`) are zero-overhead: args packaged as (raw ptr, vtable ptr) pairs on the stack. No boxing, no heap alloc.
- Managed interface variadics (`...@Stringer`) for functions that retain args.
- `println`/`printf` likely compiler builtins for practical reasons, but user-defined variadic logging functions work efficiently with raw interface args.

### Spread operator — DECIDED

- `...` spread operator for passing slices to variadic functions
- Syntax: `expr...` where `expr` is a slice — expands the slice into individual arguments
- Use cases: passing slices to variadic functions (e.g., forwarding args in `printf` calling `sprintf`)
- Deferred from bootstrap subset — bootstrap uses `Concat` builtin for string concatenation instead

**Type declarations — DECIDED**:
- `type Celsius float64` — distinct new type, same representation. Requires `cast()` to convert. Can have methods and impl interfaces.
- `type byte = uint8` — alias, fully interchangeable. Cannot have methods.
- Named structs via `type`: `type Point struct { x int; y int }` — the only way to declare a named struct (no `struct Point{...}` shorthand, like Go).
- Distinct types from any type: pointers (`type Handle @SomeStruct`), slices (`type Buffer *[]uint8`), etc.
- **Distinct-type transparency (Go's defined-type model) — DECIDED**: a distinct type behaves as its underlying type for operators, the built-ins that act on the underlying kind (`len`, `present`, `same`), indexing, slicing, and field access — the checker peels the named wrapper (and any `readonly` wrapper) to the underlying. `type Buf @[]int; var b Buf` → `len(b)` / `b[i]` / `b[lo:hi]` all work; `type H @Box; h.field` reads Box's field. **Method sets are NOT inherited**, though: a distinct type does not pick up its underlying's methods/impls — declare those on the distinct type itself (field access transparent, method inheritance not — exactly Go).
- **Distinct-type assignability — DECIDED**: a value crosses the boundary *without* a cast iff the two types have identical underlying types AND at least one side is an *unnamed* type (Go's rule). An unnamed composite underlying assigns freely both ways: `var b Buf = make_slice(int, 3)` (unnamed `@[]int` → `Buf`) and `var s @[]int = b` both work. Two *named* types never inter-assign implicitly even with identical underlying (`type A @[]int; type B @[]int` → `A`↔`B` needs `cast`); a scalar/named underlying always needs a cast (`Celsius`↔`float64`, since `float64` is itself a named type) — which is exactly what "requires `cast()` to convert" above always meant.
- **Distinct-type comparison — DECIDED**: comparability follows the underlying type (`type ID int` is comparable; `type P struct{…}` is comparable iff its fields are), with one deviation from Go — **Binate slices are never comparable, not even to `nil`** — so a distinct *slice* type (`type Buf @[]int`) is not comparable at all; test emptiness with `len(b) == 0` or `present(b)`, never `b == nil`.
- Anonymous struct types: `struct{x int}` — structural equivalence (two occurrences of the same field sequence = same type). Equivalence requires both field **names** and **types** to match in order (following Go). `type Foo = struct{x int}` is an alias for the anonymous type.
- Methods and `impl` require named types. Anonymous types cannot be receivers (Go's rule). Methods can only be defined on a named type that's declared in the same package as the method — you cannot add methods to types imported from other packages (also Go's rule).
- **Anonymous struct destructors**: dtor naming is based on field TYPE sequence only (not names), since cleanup logic depends only on types. Short names: `__dtor_anon_int_mp_Node_ms_uint8`. If the name exceeds ~128 characters, a hash of the stringified type sequence is used instead: `__dtor_anon_h<hex>`. `weak_odr` for linker dedup across modules.

**Struct literals — DECIDED**:
- Named fields: `Point{x: 1, y: 2}`
- Positional: `Point{1, 2}` (also needed for anonymous fields)
- Partial: `Point{x: 1}` — unspecified fields zero-initialized
- Empty: `Point{}` — all fields zero-initialized

**Array literals — DECIDED**:
- Full: `[3]int{1, 2, 3}`
- Inferred size: `[...]int{1, 2, 3}` (Go-style)
- Zero-init: `[3]int{}`
- Partial: `[3]int{1}` → `{1, 0, 0}` — unspecified elements zero-initialized (Go-style)
- Indexed: `[5]int{1: 10, 3: 30}` → `{0, 10, 0, 30, 0}` — useful for sparse/lookup tables

**To discuss further**:
**Annotation system — DECIDED**:

Syntax: `#[annotation]` or `#[annotation(args)]` or `#[ns.annotation]`

Namespacing:
- Unqualified = language-standard. Compiler/interpreter enforces these are known/valid (catches typos).
- `compiler.*` (or specific compiler name) = compiler/interpreter-specific. Unknown namespaces are ignored.
- `tool.*` = external tools. Compiler ignores.

Attachment model — "annotates the immediately following element":
- Before a declaration keyword: annotates the declaration (`#[tools.export] type Foo ...`)
- After the name in a type declaration: annotates the definition (`type Foo #[packed] struct { ... }`)
- Before a field (with explicit name or `_`): annotates the field (`#[align(4)] x int`)
- After a name, before the type: annotates the type (`x #[foo] int` or `_ #[foo] int`)
- **Ambiguous case disallowed**: `#[foo] int` (no name) is an error. Must use `_` to disambiguate: `#[foo] _ int` (annotates field) or `_ #[foo] int` (annotates type). Same rule in argument lists.

Multiple annotations: comma-separated within one block (`#[packed, align(4)]`). No stacking of separate `#[...]` blocks.

Type identity: only standard/compiler annotations that affect representation (e.g., `packed`) affect type identity. Tool/metadata annotations do not.
**Package system — DECIDED**:

File extensions:
- `.bn` — implementation files
- `.bni` — interface files
Package declaration: string-based, matches import path:
```
package "pkg/foo"
```

Directory layout: interface file is sibling of implementation directory:
```
pkg/
  foo.bni          // interface
  foo/             // implementation
    impl1.bn
    impl2.bn
```

One interface file per package. Compiler finds `.bni` on search path, verifies implementation matches.

Import syntax:
```
import "pkg/foo"
import myname "pkg/foo"    // alias
```

Search path: project root is highest priority, followed by other directories. `pkg/`-prefixed packages are "public" and found via search path. Non-`pkg/` packages are inherently local.

No language-enforced `internal/` — with separate interfaces, visibility is already controlled by whether a `.bni` exists.

Shadowing: allowed. Project-local packages take priority over external.

Main package: `package "main"` is a special case — requires `main()` function, no `.bni` needed. Multiple `.bn` files per package supported (all in same directory).

### Naming conventions — DECIDED

- Exported symbols (those in `.bni` files) should be capitalized (Go-style): `TypeName`, `IsKeyword`, `Lookup`
- Private symbols (not in `.bni`) use lowercase/snake_case: `helper_func`, `internal_state`
- This is a convention, not enforced by the compiler — visibility is still determined by `.bni` presence
- Types, functions, and constants in `.bni` should all follow this convention

### Visibility & package interfaces — DECIDED

**No per-symbol visibility keywords** (no `pub`, no capitalization convention). Instead:

- Packages have **explicit, separate interface files** — declarations separate from definitions
- If a symbol is in the interface file, it's public. If not, it's private.
- The compiler verifies that implementations satisfy their interfaces.

**Advantages over C headers**:
- Authoritative (compiler-enforced match between interface and implementation)
- No preprocessor mess

**Benefits**:
- Clear API contracts (interface file = API docs)
- Faster compilation (consumers only need the interface)
- ABI stability (change implementation without changing interface)
- Binary-only library distribution (ship interface + compiled lib)
- Dual-mode interop: interpreter can load interface files to call compiled code without source

### Interpreter embedding model — DECIDED

- The interpreter is a **library** linked into the compiled binary
- Accesses compiled symbols via **interface files + symbol resolution** (symbol table or explicit registration)
- Shares the same heap as compiled code (no separate managed heap)
- Has its own evaluation state but operates on the same data

### Self-hosting bootstrap — DONE

**Strategy**: interpreter-first bootstrap.
1. Write a minimal interpreter in a host language (subset of Binate only)
2. Write the full interpreter and compiler in Binate
3. Use the minimal interpreter to run the Binate compiler → compile everything → native binaries
4. Discard the bootstrap interpreter. Fully self-hosted.

The compiler should have a backend architecture that supports cross-compilation from the start, so bootstrap doesn't need to happen on target (32-bit) systems.

**Status — COMPLETE.** The toolchain is fully self-hosted. The Go bootstrap
interpreter (`github.com/binate/bootstrap`) was retired 2026-05-21; builds now
start from a prebuilt BUILDER `bnc` (pinned by `BUILDER_VERSION`, fetched by
`scripts/fetch-builder.sh`) rather than a host-language interpreter — Step 4
(discard the bootstrap interpreter) is done.

The self-hosted compiler (`cmd/bnc`) produces native binaries via LLVM IR and
self-compiles: gen1 (`builder-comp-comp`) and gen2 (`builder-comp-comp-comp`)
both pass the conformance suite, as do the alternate backends (native aa64/x64,
arm32 linux/baremetal). The interpreter is now a bytecode VM (`pkg/vm`, driven
by `cmd/bni`), replacing the original tree-walking `pkg/interp`.

Conformance modes are chains of `builder` (prebuilt BUILDER bnc), `comp`
(compiler), and `int` (bytecode VM) — e.g. `builder-comp`, `builder-comp-int`,
`builder-comp-comp-comp`; see `conformance/run.sh`. (The old Go-interpreter
`boot*` modes are gone with the bootstrap.)

### Operators — DECIDED

**Arithmetic**: `+`, `-`, `*`, `/`, `%`
- Integer division truncates toward zero (C99+/Go/hardware behavior)
- `%` result has same sign as dividend (`-7 % 2 = -1`); identity `(a/b)*b + a%b == a` holds
- Division by zero: runtime trap (defined behavior, not UB)
- Integer overflow: wrapping (two's complement). No UB — systems language, matches hardware.

**Bitwise**: `&`, `|`, `^`, `~`, `<<`, `>>`
- `>>` is arithmetic for signed types, logical for unsigned (C/Go/Rust behavior). No separate `>>>`.
- A shift's result type is the LEFT (value) operand's; the count may be ANY integer type, independent of the value (Go semantics — a shift is not a symmetric binop).
- Shift by >= bit width: defined behavior (zero for `<<` and logical `>>`, sign-extended for arithmetic `>>`). This holds for a count of any width: the overshift is detected from the count's true value before it is reconciled to the value width (so a wide runtime count >= 2^valueWidth still yields the spec'd result, not a truncated residue).
- **Negative shift count: a programmer error (DECIDED 2026-06-15, matching Go).** A *constant* negative count is a **compile error** ("negative shift count"); a *runtime* negative count **panics** ("runtime error: negative shift count"). An unsigned-typed count can never be negative, so it is never checked.
- `unsafe_shl(v, n)` / `unsafe_shr(v, n)` are the guard-free counterparts (the shift analogue of `unsafe_div`/`unsafe_rem`): a bare hardware shift with NEITHER the overshift handling NOR the negative-count check. The caller asserts `n` is in `[0, bitwidth)`; otherwise the result is target-defined.

**Comparison**: `==`, `!=`, `<`, `>`, `<=`, `>=`
- No chaining (`a < b < c` is a compile error, like Go)
- Pointer comparison with `==`/`!=` (address equality only); scalars compare directly
- `==`/`!=` are **not allowed** on slices, interface values, or function values — use `present()` for emptiness and `same()` for identity (DECIDED 2026-06-07). On structs and arrays `==`/`!=` are reserved (fieldwise / elementwise) but **not yet implemented** — the checker emits a clear "not yet implemented" diagnostic.
- Relational `<`/`>`/`<=`/`>=` require **numeric** operands — ordering is undefined for pointers and for every aggregate.
- **Float comparison is IEEE 754 (matches Go/C/Rust)**: `==` and the four
  relationals (`<`, `<=`, `>`, `>=`) are *ordered* — they are `false` when
  either operand is NaN (so `NaN == NaN` is `false`, `NaN < x` is `false`,
  etc.). `!=` is *unordered* — `true` when either operand is NaN (so
  `NaN != NaN` is `true`), keeping it the exact complement of `==`
  (`(a == b) == !(a != b)` always holds). `+0.0 == -0.0` is `true`. The
  idiomatic NaN test is `x != x`. (Lowering: LLVM `oeq`/`une`/`olt`/`ole`/
  `ogt`/`oge`; the native backends mirror it via FCMP/UCOMISD condition
  codes. Earlier builds made `!=` ordered too — corrected 2026-06-06.)

**Logical**: `&&`, `||`, `!` — short-circuit. Operands must be `bool` (no truthy/falsy).

**Assignment**: `=`, `+=`, `-=`, `*=`, `/=`, `%=`, `&=`, `|=`, `^=`, `<<=`, `>>=`
- Assignment is a statement, not an expression (no `x = y = 5`)

**Increment/decrement**: `x++`, `x--` — postfix only, statements only (not expressions). No `++x`.

**Unary**: `-` (negation), `~` (bitwise complement), `!` (logical not), `*` (deref), `&` (address-of)

**Member access**: `.` only, auto-dereferences. No `->`.

**No operator overloading.**

**Precedence** (highest to lowest):
1. Unary: `!`, `~`, `-`, `*`, `&`
2. Multiplicative: `*`, `/`, `%`
3. Additive: `+`, `-`
4. Shift: `<<`, `>>`
5. Bitwise AND: `&`
6. Bitwise XOR: `^`
7. Bitwise OR: `|`
8. Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=`
9. Logical AND: `&&`
10. Logical OR: `||`

### Scoping rules — DECIDED

- **Block scoping**: every `{}` block introduces a new lexical scope
- **Variable shadowing**: allowed, but compiler warns by default (suppressible)
- **Top-level scope**: `type`, `func`, `const`, `var`, `interface`, `impl`, `import`. No bare expressions/statements (those are REPL immediate-mode only).
- **Package-level variables**: mutable `var` and `const` both allowed (mutable globals are a fact of life in systems programming)
- **Initialization order**: dependency-based, then source order within a file, then file order within a package
- **No `init()` functions** (unlike Go) — explicit initialization in `main` or setup functions
- **No function-local `type` declarations.** `type Foo ...` is a top-level-only declaration — writing it inside a function body is a parse error. Rarely used, but carries disproportionate complexity: shadowing rules vs. package-level types, name mangling, (eventual) interaction with generic parameter binding. Package-level types plus a doc comment covering the "only used by `F`" case is enough. See `differences-with-go.md`.

### Memory management details — DECIDED

**Managed allocation layout** (two words overhead):
```
[ refcount (uint) | free function ptr | user data ... ]
                                        ^
                                        managed pointer points here
```

- Refcount at -2 words offset, free function at -1 word offset from the managed pointer
- Free function called when refcount hits zero, after managed fields are recursively released
- Normal heap: free function is `free(base_ptr)`. Static/pre-initialized data: no-op. Custom allocators: allocator's dealloc.
- Static managed data uses a sentinel refcount (e.g., `UINT_MAX`) — never decremented, never freed
- No destructor in the header — statically-typed code knows the concrete type's drop behavior. Interface values carry drop info in the vtable/type-info.

**`make`, `make_slice`, and `box` — clean split, no ambiguity**:
```
make(Point)              // @Point, zero-init (takes a type)
make([100]int)           // @([100]int), zero-init managed fixed-size array
make(*[]int)              // @(*[]int), managed pointer to zero-value raw slice

make_slice(int, n)       // @[]int, runtime-sized managed-slice, n zero-init elements

box(42)                  // @int, box a literal (takes an expression)
box(x)                   // @T where x: T, copies value
box(Point{x: 1, y: 2})  // @Point, allocate and init
```

- `make(T)` always takes a type, returns `@T`. Zero-initializes. Works for ANY type T,
  including `*[]T` (→ `@(*[]T)`, managed ptr to raw slice) and `[k]T` (→ `@([k]T)`,
  managed ptr to fixed-size array). No size argument.
- `make_slice(T, n)` takes an element type and runtime size. Returns `@[]T` (managed-slice
  — the special 4-word type). This is the ONLY way to create runtime-sized managed-slices.
  Separate builtin because `make(*[]T, n)` is ambiguous (does it return `@(*[]T)`
  or `@[]T`?). **Always returns managed-slice** — a non-managed version makes no sense,
  since you'd be allocating memory with no way to free it.
- `box` always takes a value expression. Allocates and copies. No ambiguity.
- No capacity argument — growing is a library concern (CharBuf, Vec[T], etc.)

**Notation — DECIDED**: `@([k]T)` (with parens) for managed pointer to fixed-size array,
to distinguish from `@[]T` (managed-slice sugar). `@[k]T` is ambiguous and should not
be used. The `@[]` sugar applies ONLY to `@[]T` (managed-slice); all other combinations
use explicit parens to break the sugar.

### Temporary lifetime — DECIDED

**Rule: each statement has an implicit scope; temporaries are locals in that scope.**

When an expression creates a managed value (via `make`, `make_slice`, `box`, or a managed literal) that is not assigned to a named variable of managed type, the value is an unnamed local in the statement's implicit scope. Its refcount is decremented when the statement completes.

This guarantees that managed temporaries survive implicit conversions to raw types within the same statement:

```
foo(@[]int{1, 2, 3})    // foo takes *[]int — managed-slice lives through the call
foo(make(Point))         // foo takes *Point — @Point lives through the call
bar(foo(@[]int{1, 2, 3}))  // chained — temporary lives through entire statement
```

The same rule applies to stack-allocated temporaries (e.g., `*[]int{1, 2, 3}` creates a temporary `[3]int` on the stack and slices it — the array lives in the statement scope).

**What this does NOT save you from:**
```
var s *[]int = @[]int{1, 2, 3}   // temporary freed at end of statement
foo(s)                           // s is a dangling slice — programmer error
```

This is consistent with the raw slice contract: `*[]int` means "caller manages lifetime." Use `@[]int` if you need the allocation to persist.

### Method resolution & dispatch — DECIDED

**One method per name per base type.** No overloading on receiver kind. A method name is defined once, regardless of whether the receiver is value, `*T`, or `@T`.

**Auto-dereferencing**: one level only (like Go). If `obj` is `@T` or `*T`, compiler looks for methods on the pointer type and on `T`.

**Receiver conversion** at call sites (safe direction only):
- `@T` → `*T` → `*readonly T` (implicit)
- `@T` → `@readonly T` → `*readonly T` (implicit)
- Any pointer → value (by copy)
- `*T` → `@T`: never implicit

**Value receivers — implementation strategy.** The default implementation is to pass value receivers by value (struct copy or primitive value, like any other parameter), matching the user-visible semantics directly.  An optimization to lower value receivers as `*readonly T` (avoiding the copy for large structs) is permitted as a future compiler optimization but is NOT part of the language contract — method expressions, call sites, and method-value types all see the value receiver as the user wrote it.  See `plan-primitives-impl-interfaces.md` § "Interface-value dispatch and value receivers" for how iv vtable slots adapt the iv data-pointer ABI to value-receiver methods via per-(T, I) thunks.

**`_` receiver name** is allowed, with the same semantics as `_` parameter names — an explicit indicator that the receiver isn't used in the method body. The type checker treats it like any other unused-name; nothing method-specific.

**Interface dispatch**: vtable-based. One vtable per (type, interface) pair. Vtable entries are function pointers.

**Interface declarations**: `type Name interface { ... }`, consistent with `type Name struct { ... }`. Anonymous interfaces supported: `interface { ... }`.

**Interface embedding**: list interface names in the body. Means "is-a" for all embedded interfaces. `impl *T : Child` implies `impl *T : Parent` for all embedded parents.

```
type ReadWriter interface {
    Reader
    Writer
    flush()
}
```

**Vtable layout** — no deduplication, uniform structure:
```
[any] [embed1's full vtable] [embed2's full vtable] [own methods]
```
- Every vtable starts with its own `any` entry (destructor)
- Embedded interface vtables included in full (recursively), in declaration order
- Own methods appended at the end
- Converting child → parent interface: adjust vtable pointer by known fixed offset
- Redundant `any` entries are acceptable (static data, negligible cost)

### Function values & closures — IMPLEMENTED (2026-05-31)

Two function-value flavours:
- `*func(...)` — raw 2-word `{vtable, data}` value, 16 bytes.
  Data slot is nil for non-capturing literals; for capturing
  literals it points at a stack-alloca'd closure struct whose
  lifetime is bound to the constructing frame.
- `@func(...)` — managed 2-word value with the same layout but
  refcount-managed data.  Capturing literals heap-allocate the
  closure struct so the closure outlives its constructing
  frame; cleanup runs via the vtable's dtor slot when the
  `@func` variable's refcount hits zero.

Captures are **always by value** (no capture lists, no by-
reference) — writes inside the closure are local; writes to
the source variable outside the closure, after construction,
are not visible.  Shared mutable state is via a captured
managed pointer (the `@count := make(int)` pattern).  See
`claude-discussion-detailed-notes.md` §"Closures" for the
rationale.

**Method values** (`x.M`) bind a specific receiver instance to
a method, producing a function value whose signature is the
method's signature minus the receiver.  The receiver is
captured in the form the method declares (not necessarily x's
form); cross-shape smoothing applies:
- `*T`-method called as `c.M` with `c:T` → captures `&c`.
- `T`-method called as `cp.M` with `cp:*T` / `cp:@T` → captures
  `*cp` (snapshot copy).
- `*T`-method called as `bp.M` with `bp:@T` → captures the raw
  pointer alias (linter warns).

**Lifetime escape hatches**:
- `*func(...)` capturing literal returned from its enclosing
  function → lint warning `func-value-escape`.
- `@func(...)` capturing a raw pointer → lint warning
  `managed-func-raw-capture`.
- A func literal bound to a variable it also CAPTURES (`var g =
  func(){…g…}` / `g = func(){…g…}` / `g := func(){…g…}`) → lint
  warning `recursive-closure-capture` (the "recursive closure"
  footgun: capture-by-value snapshots the variable's pre-assignment
  value, so the closure closes over a stale value, not itself, and a
  recursive self-call silently misbehaves — recursive lambdas are
  unsupported by design, see plan-function-values-phase-2.md). The
  compiler stays silent (errors-only policy); the linter flags it.
  Implemented in `pkg/binate/lint/recursive_closure_capture.bn`
  (binate `b634773d`).

Cross-reference: [plan-function-values-phase-2.md](plan-
function-values-phase-2.md) for the implementation slices
B.1..B.6 (capture analysis, closure struct / dtor / shim,
heap-alloc for `@func`, method values, linter rules, docs)
and binate commit pointers per slice.

### Generics — DECIDED

**Type parameters** on functions, structs, and interfaces:
```
func sort[T Comparable](items *[]T) { ... }
type List[T any] struct { head @Node[T] }
type Container[T any] interface { get(index int) T }
```

**Constraints**: type parameter followed by interface name. For multiple constraints, define a named combined interface (no `+` operator):
```
type ComparableStringer interface { Comparable; Stringer }
func foo[T ComparableStringer, U any](a T, b U) { ... }
```

**No type inference** — always spell out type params: `sort[int](myArray)`. Can relax in v2.

**Monomorphized** — each unique instantiation generates specialized code. `List[int]` and `List[uint8]` are distinct types.

**Type checking**: generic body checked once against the constraint. Instantiation only verifies the concrete type satisfies the constraint.

**No generic methods on types** (like Go). Use generic free functions instead.

**No conditional impls** for v1. Only specific instantiations can have `impl` declarations.

**Cross-package generics**: generic bodies included in `.bni` files (consumer needs them for instantiation, like C++ templates in headers).

### String & array semantics — DECIDED

**Bounds checking**: always on by default. `s[i]` and `s[low:high]` are bounds-checked; out-of-bounds is a runtime trap (not UB, not recoverable). `unsafe_index(buf, i)` builtin for unchecked access in performance-critical code. Compiler may also optimize away redundant checks, but programmer doesn't have to rely on it.

**Nil slices — DECIDED (reaffirmed 2026-04-03; empty-vs-no-backing distinction REVERSED 2026-06-08)**: slices cannot be compared to `nil` or assigned from `nil`. `nil` is only for pointer types (`*T`, `@T`). Check `len(s) == 0` for empty (raw and managed alike). The type checker enforces this — `s == nil` and `s = nil` on slice types are compile errors.

**Length-0 ⟹ no backing (DECIDED 2026-06-08)**: a length-0 slice ALWAYS has no backing — its representation is the nil-equivalent (`{null, 0}` raw / `{null, 0, null, 0}` managed). Empty and "nil" slices are therefore indistinguishable; there is NO "empty view of live backing" state. This REVERSES the earlier (2026-04-03) idea that a managed-slice could be an empty view still pinning live backing, distinguishable via `rt.HasBacking` (which was never implemented and is dropped). Rationale: a length-0-with-backing slice can never be read, re-sliced (subslicing is bounds-checked to `len`), or appended to (no `append`), so its backing is permanently unreachable — yet a *managed* one silently pins the allocation alive, which IS observable (a raw alias into that backing stays valid) and a footgun. Eliminating the state removes the last nil-vs-empty difference. Every slice-producing operation (subslicing, `make_slice`, composite/string literals, rodata aliases) must establish this; `make_slice(T, 0)` already does (`rt.MakeManagedSlice`).

Rationale: Go's nil-slice vs empty-slice distinction is a well-known source of confusion. For raw slices, nilness would be equivalent to `len(s) == 0` (adding no information). For managed-slices, the semantics would differ from raw slices (nil = no backing, not just empty), making it even more confusing. Disallowing nil for slices entirely is cleaner.

(This was briefly revised to allow nil comparisons (2026-04-03) but immediately reverted — the distinction is more confusing than helpful, especially since it would differ from Go's semantics.)

**Indexing**: zero-based. `s[i]` reads/writes element. `s[low:high]` creates a sub-slice (exclusive end). `s[:]`, `s[low:]`, `s[:high]` are shorthand forms. All bounds-checked.

**`len()`**: returns slice length field, or compile-time constant for fixed-size arrays. `len("abc")` = 3 (whether taken as a slice or array). No hidden null terminator to account for.

### Comparison points
- **Forth**: simple, dual-mode (threaded interpretation + compilation), embeddable, used in firmware — but very different paradigm
- **Lua**: embeddable interpreter, small footprint, interop with C — but not a systems language
- **Zig**: systems language, simple, C interop — but no interpretation story
- **Terra/Lua combo**: compiled (Terra) + interpreted (Lua) interop — closest existing analog?

---

## Next Steps

Phases 1–4 are complete. See `claude-plan-1.md` for the full record.

**Phase 5: Self-hosted toolchain — COMPLETE** — see `claude-plan-2.md` for the detailed plan. The key decisions (still the design of record):

1. **Interpreter first, then compiler.** Shared frontend (lexer, parser, types) is the bulk of the work. Interpreter adds just a tree-walker (later a bytecode VM); compiler adds IR, codegen, backends.
2. **Single repo to start** (`binate/binate`). Split into core/interp/compiler repos once boundaries stabilize.
3. **Compiler architecture**: SSA-based IR, pluggable backends (x86-64, ARM64, LLVM IR), optional optimization passes (refcount elision and escape analysis prioritized). LLVM IR backend gives quality native codegen on big platforms quickly; custom backends needed for embedded targets where LLVM is too heavy.
4. **Object files**: emit platform-native formats (ELF/Mach-O) directly, shell out to system linker initially.
5. **Inline assembly**: `#[asm("arch")]` annotation syntax proposed; deferred for initial self-hosting.
6. **AST representation — DECIDED**: tagged unions (structs with `Kind int` fields). Without interfaces in the bootstrap subset, each AST node type (Expr, Stmt, Decl, TypeExpr) is a single struct with a Kind discriminator and union of fields. Managed pointers (`@Expr`, `@Stmt`) enable self-referential types. Two-pass type resolution (pre-register placeholders, then resolve) handles forward references.

### IR/backend architecture — IMPLEMENTED

The compiler's backend layer needs to support multiple targets. The current LLVM backend (`pkg/codegen`) mixes language-semantic logic (struct layout, name mangling, runtime function declarations, string constant collection) with LLVM-specific code generation. This was separated so that the backends (aarch64, x64, arm32) don't duplicate shared logic.

**Key principle**: if two backends would compute the same thing, it belongs in a shared layer.

**Hard constraint from dual-mode interop**: memory layout of all data types (structs, arrays, slices, managed-slices, managed pointer headers, future interface values) must be identical between the compiler (all backends) and the interpreter. They share the same heap and call each other via function pointers — any layout divergence means data corruption. This means layout is not a backend-internal decision; it is a language-level contract defined once in `pkg/types` and used by everything.

**Shared layer** (IR, types, or new shared packages):
- **Memory layout** — struct padding, slice/managed-slice/array/managed-pointer-header representation (parameterized by target). Used by all backends AND the interpreter.
- Name mangling (`bn_pkg__Name` convention)
- String constant collection and deduplication
- Runtime function manifest (which functions the generated code may call)
- Multi-return struct representation

**Backend-specific**:
- Instruction selection, register allocation, calling convention
- Type representation format (LLVM types, ARM registers, etc.)
- Binary/object format (ELF, Mach-O, `.ll` text)
- Debug info format
- Linking

**Target parameterization**: `types.SizeOf`/`AlignOf`/`FieldOffset` must be parameterized by a `TargetInfo` (pointer size, int size, max alignment) rather than assuming 64-bit. This is in place (it was a prerequisite for the arm32 backend).

**Byte order (endianness) — DECIDED 2026-06-17**: implementation-defined — an implementation fixes and documents a single byte order per target, and (where both modes exist) the compiled and interpreted modes must agree on it. It is observable through `bit_cast` and the representation-introspection builtins. The current implementation is **little-endian only**, and `TargetInfo` carries no endianness field yet; a complete target-parameterized layout description (to describe a big-endian / cross-endian target) needs an endianness field in `TargetInfo` — adding that field + big-endian support is a tracked impl follow-up, not done. Spec: §7.13.12 `type.layout.byte-order`, §21.4 `behavior.impl-defined.endianness`.

**Testing strategy**: 32-bit ARM binaries are tested via QEMU user-mode emulation (`qemu-arm`) on the development Mac. Binaries target Linux/ARM ELF format (minimal syscall usage: `write`, `exit_group`, `mmap2`). The conformance runner has `builder-comp_arm32_linux` / `builder-comp_arm32_baremetal` modes.

See `ir-backend-guidelines.md` for the full guidelines.

### Testing convention — DECIDED

Unit testing built into the toolchain with a lightweight, convention-based approach:

- **Test files**: `*_test.bn`, live alongside regular `.bn` files in the same package directory
- **Same-package tests**: test files use the same `package` declaration as the code they test (no separate `foo_test` package). They can access all symbols including unexported helpers.
- **Exclusion by default**: `_test.bn` files are excluded from normal builds. Only included when the package is a `-test` target.
- **Test functions**: `TestXxx() testing.TestResult` — no parameters, returns `testing.TestResult`. Discovered automatically by name prefix and signature.
- **Failure signaling**: return a non-empty string (the failure message). Empty string means pass. No panic recovery needed — works identically in interpreter and compiled code.
- **`pkg/builtins/testing`**: provides `type TestResult = *[]char`. Test files import this package.
- **CLI**: `binate -test [-root dir] <pkg/foo> [pkg/bar ...]` — supports multiple packages in one invocation.
- **Output format**: Go-style (`=== RUN`, `--- PASS`/`--- FAIL`, `ok`/`FAIL` per package, summary).

Design rationale: minimal complexity, works within the bootstrap subset (no interfaces, no generics, no closures needed), and works identically in interpreted and compiled code (no panic recovery needed). The convention is close enough to Go's that it feels familiar, but simpler (no `testing.T` parameter, no sub-tests). Wrong-signature `TestXxx` functions produce a warning.

### `append` — REMOVED

`append` has been fully removed from the language (parser, type checker, IR gen, codegen, both interpreters, all source code, tests, and conformance tests). Growable collections are a library concern: `buf.CharBuf` for strings, per-type append helpers for other types, and `Vec[T]` (post-generics) for general lists. `make_slice(T, n)` provides the primitive for allocating managed-slices; library types handle growth/capacity on top of that.

### Self-hosting: DECL_GROUP import bug — FIXED (2026-03-27)

**Bug**: When the self-compiled compiler processed imported packages, `registerImportFieldsAndFuncs`
handled `DECL_CONST` (individual constants) but not `DECL_GROUP` (const groups using iota).
This caused all iota constants from imported packages to resolve to 0 in the compiled binary.

**Impact**: The self-compiled compiler's parser couldn't distinguish token types — `token.STRING`,
`token.PACKAGE`, etc. all had value 0, causing parse failures.

**Fix**: Added `if d.Kind == ast.DECL_GROUP { registerImportConstGroup(alias, d) }` in
`registerImportFieldsAndFuncs` (gen.bn). The older `RegisterImport` function already handled
both cases; the newer multi-import path was missing it.

**Root cause**: `RegisterImports` (plural) was added later for cross-package type resolution
and its inner function `registerImportFieldsAndFuncs` was written from scratch rather than
factored from `RegisterImport`, so the DECL_GROUP case was missed.

### Self-hosting: short-circuit evaluation — FIXED

**Bug (now fixed)**: The IR generator (`genBinary` in gen.bn) evaluated both sides of `&&`
and `||` eagerly. LLVM's `and`/`or` instructions don't short-circuit — they're bitwise
operations on already-computed values. This meant `p != nil && p.Val > 0` crashed because
`p.Val` was evaluated even when `p` was nil.

**Fix**: Alloca+branch+load pattern with `CurBlock` tracking through `GenContext`.

- Added `CurBlock @Block` field to `GenContext` so `genExpr` can communicate block changes
  to callers when short-circuit creates new blocks.
- `genShortCircuitAnd`: alloca result (default false), evaluate LHS, branch on LHS
  (true → evaluate RHS and store, false → skip to merge), load result from merge block.
- `genShortCircuitOr`: same pattern, inverted — default true, branch false → evaluate RHS.
- All ~40 `genExpr` call sites updated with `b = ctx.CurBlock` to pick up block changes.

The earlier attempt failed because `genExpr` created new blocks but callers continued
emitting to the old block. The `CurBlock` field solves this by providing a side channel
for block state. The manual `&&` workarounds in `GeneratePackage` are now redundant but
harmless.

Conformance tests 071 (short-circuit &&) and 072 (short-circuit ||) pass in all modes.

---

## Proposed Changes

### Restrict implicit `@T` → `*T` conversion to borrowing positions — PROPOSAL
- Currently `@T` converts implicitly to `*T` (and `@[]T` to `*[]T`) in all contexts, including variable assignment, struct field stores, and returns — where the raw pointer can outlive the managed value.
- Proposal: restrict implicit conversion to **borrowing positions** only (function arguments, method receivers, subexpressions). In storing positions (assignment, return, field store), require explicit conversion.
- This would make many use-after-free bugs compile-time errors instead of runtime crashes.
- Needs investigation: migration impact, explicit conversion syntax, edge cases.
- Full proposal in `explorations/proposal-restrict-implicit-raw-conversion.md`.

---

### Debugging process improvements — TO DISCUSS

> _Predates the current BUILDER-`bnc` build flow (the Go bootstrap interpreter was retired 2026-05-21); the specific cycle described below has changed, though some pain points may still apply._

During self-hosting debugging, several pain points surfaced:

1. **No `gtimeout` initially**: macOS lacks `timeout`, so hung binaries had to be killed
   manually. Now resolved — `gtimeout` is available via coreutils.

2. **Slow feedback loop**: Each test of the self-compiled compiler requires:
   bootstrap interprets compile.bn → compiles compile.bn to native → run native compiler.
   This multi-minute cycle makes iterating on bugs expensive.

3. **Limited debug output from compiled binaries**: When the compiled compiler crashes,
   there's no stack trace or useful error — just SIGSEGV. Adding debug prints requires
   a full rebuild cycle.

4. **Conformance tests don't cover the compiled-compiler path well**: Most tests run via
   bootstrap or single-stage compilation. The compiled-compiler runner exists but is slow
   and doesn't have good coverage of compiler-internal edge cases.

**Potential improvements**:
- Add a `--trace` or `--debug` flag to the compiler that can be toggled without recompilation
- Build a "compiler test corpus" of .bni/.bn inputs that exercise specific IR generation paths
- Consider an incremental approach: test individual IR generation functions via unit tests
  before running the full compiler pipeline
- Use the unit test framework to test `RegisterImports`, `GeneratePackage`, etc. with
  crafted AST inputs that trigger specific code paths

### Debug lifecycle hooks for structs — PROPOSED

> _The motivating use case below (the tree-walking interpreter's `Value` struct) predates the bytecode VM; re-evaluate relevance against the current `pkg/vm`._

**Annotation-based pre-copy and pre-destruction hooks.** Structs can
declare hook functions via standard annotations. When a compiler debug
mode flag (`--debug-hooks`) is enabled, the compiler calls these hooks
at the appropriate lifecycle points. With the flag disabled, zero overhead.

```binate
#[pre_copy(debugPreCopy), pre_destroy(debugPreDestroy)]
type Value struct {
    Kind    int
    RawAddr *uint8
    Typ     @types.Type
    IsClean bool
    // ...
}

func debugPreCopy(dst *uint8, src *uint8) {
    panic("Value must not be copied by value")
}

func debugPreDestroy(ptr *uint8) {
    var v *Value = cast(*Value, ptr)
    if !v.IsClean {
        panic("Value destroyed without cleaning contents")
    }
}
```

**Semantics:**
- `pre_copy` hook: called after raw data copy, before copy constructor.
  Signature: `func(dst *uint8, src *uint8)`.
- `pre_destroy` hook: called before destructor. Signature: `func(ptr *uint8)`.
- Having either hook (with debug mode enabled) forces the struct to be
  "managed" (requires copy construction/destruction) regardless of whether
  it has managed fields.
- Hooks are regular functions in the same package.
- Uses the existing annotation syntax: `#[pre_copy(funcName)]`.

**Motivation:** Primarily for the self-hosted interpreter's `Value`
struct, where we need to enforce unique ownership and explicit content
cleanup. But the mechanism is general — any struct with lifecycle
invariants benefits.

See `explorations/plan-debug-hooks.md` for implementation plan.
See `explorations/plan-interp-value-ownership.md` for interpreter changes.
See `explorations/plan-interp-value-hooks.md` for hook usage in interpreter.

### `move` builtin — PROPOSED

> _The "primary use case" below (interpreter `Value` ownership) predates the bytecode VM, but `move` remains broadly useful for explicit ownership transfer — re-evaluate the framing, not the proposal._

**Explicit ownership transfer.** `move(x)` returns the value of `x` and
nils/zeroes the source. This makes axiom 4 (move → zero source) a
first-class language operation.

```binate
var x @Node = make(Node)
f(move(x))    // x is now nil; f owns the Node
```

**Semantics by type:**
- `@T`: returns the managed pointer, sets source to nil.
- `@[]T`: returns the managed-slice, zeroes the source (4 words).
- Struct value: returns the struct, zeroes the source. Naive: copy + dtor.
  Optimized: memcpy + memset-zero (skip copy constructor and destructor
  since the source is zeroed and the destination has the live data).

**Applies to lvalues:** variables and struct fields. `move(s.field)`
transfers ownership of a field, nilling the field in the struct.

**Implementation:** parsed as a builtin (like `make`, `cast`). At the IR
level, `OP_MOVE` loads the value and stores zero to the source.
Optimization: the compiler can recognize move patterns and elide
copy+dtor pairs.

**Why a builtin, not a generic function:**
- Type inference for free — `move(x)` has the type of `x`.
- No pointer indirection — works on values directly.
- Compiler knows it's a move at the IR level — enables optimizations.
- Avoids requiring generics or type inference for generics.

**Primary use case:** interpreter Value ownership (`envDefine(name, move(val))`).
Also useful anywhere ownership transfer is explicit: return from function,
pass to consuming function, swap fields.

See `explorations/claude-discussion-detailed-notes.md` section 32 for
detailed design discussion.

### Build-time configuration system — PROPOSED (revised 2026-06-02)

**Compile-time, per-package configuration via `#[config]`-annotated variables and a CLI override.**  Per-package scoped, fully typed.

(Originally framed around `#[config] const`; retargeted to `var`
once the const / var / readonly-modifier split landed.  Consts
under the new model have no storage and no symbol, so they can't
be flag-overridden through a link-time mechanism; vars do have
storage and are the natural target.  The single-source-of-truth
property is cleaner this way too: a var's value is determined
entirely by the flags the *declaring* package was compiled with,
removing the cross-package-flag-inconsistency concern.)

**Motivation.**  The most concrete use is dead-coded debug/trace
blocks: `if DEBUG { ... }` that the optimizer folds away when
`DEBUG` is false.  The same mechanism serves any per-build knob —
default log level, feature toggles, target flag guards, version
strings, etc.

**Design sketch.**

A configurable value is a `#[config]`-annotated `var` declaration.
The declaration fixes the name, the type, and the default initial
value; its placement (between `.bni` and `.bn`) fixes visibility
exactly like any other `var`.

`readonly` is the usual modifier — configs are typically set at
compile time and read everywhere, so `var readonly` is the common
shape:

```binate
// pkg/myapp/myapp.bn — package-private config
#[config]
var DEBUG readonly bool = false

// Applied to a `var ( ... )` group, every member of the group becomes a config.
#[config]
var (
    LOG_LEVEL  readonly int              = 3
    MAX_CONNS  readonly int              = 128
    BUILD_TAG  readonly *[]readonly char = "dev"
)
```

For an exported config, declaration sits in `.bni` (as the usual
extern declaration — no initializer), with the matching definition
+ default in the `.bn`:

```binate
// pkg/myapp.bni — exported config (extern declaration)
#[config]
var VERSION readonly *[]readonly char

// pkg/myapp/myapp.bn — matching storage + default
#[config]
var VERSION readonly *[]readonly char = "0.1.0"
```

`#[config]` may also annotate `var` without `readonly`, allowing a
mutable global whose initial value is flag-controllable but which
the package may further mutate at runtime.  Linter notes that the
combination is unusual.

The CLI overrides a specific package's specific config value:

```
bnc -Dpkg/myapp:DEBUG=true -Dpkg/myapp:LOG_LEVEL=5 ...
bnc -Dpkg/myapp:VERSION='"0.2.0-dev"' ...
```

Syntax: `-D<package-path>:<name>=<value>`.  Package path and name
are both required — no unqualified form.  Values are parsed against
the declared type; type mismatches are compile errors.

**Semantics.**

- `#[config] var X T = default` declares an ordinary variable of
  type `T` whose initial-data value is the CLI override if one was
  supplied for this name in this package, otherwise the declared
  default.  Everything else follows from how `var` already works:
  storage in the declaring package's `.bn`, exported via `.bni`,
  read at every use site.
- The flags affecting `X`'s initial value are **the flags for the
  package that declares `X`**.  A consumer compiling against
  `pkg/a`'s `.bni` sees `a.X` as a symbol; the value at that
  symbol is whatever `pkg/a`'s compilation step wrote into the
  data section.  Consumer-side flags don't enter into it.
- `#[config]` attaches to a top-level `var` declaration or a whole
  `var (...)` group.  On a group, every member becomes a
  configurable value, with its own independent CLI override key.

**Types.**  Any type a `var` can carry — scalars (int, bool, char,
float), strings, slices, arrays, structs, pointers.  Values coming
in from the CLI are parsed per the declared type; mismatches are
compile errors (`-Dpkg/a:X=256` where `X uint8` is rejected).

**Separate compilation, binary distribution, and dual-mode
interop.**  Because `var` already has storage in the declaring
package's compilation unit, the configuration story falls out
naturally:

- **Split compilation**: recompiling `pkg/a` with a new `-D` updates
  only `pkg/a`'s data section.  Dependents (`pkg/b`, `pkg/c`, ...)
  don't rebuild — their references still resolve to the same
  exported symbol, which now holds the new value.
- **Binary package distribution**: a consumer shipping `pkg/a` as
  a compiled artifact + `.bni` carries the current config values
  in the data section.  No separate config blob, no side-channel
  metadata.
- **Dual-mode interop**: the interpreter, when loading a compiled
  package, reads from the same data-section global that compiled
  code reads.  When a package is interpreted (no compiled
  artifact), the interpreter resolves the value from `-D` flags
  (or the declaration's default) at package-load time.  Either
  way there is one authoritative value per (package, name).

**Dead-code-eliminating `if DEBUG { ... }`.**  Within the declaring
package, the LLVM optimizer can const-propagate from a
`readonly`-typed global with a static initializer into a single
folded value — IPSCCP / GlobalOpt handle this for `readonly`
globals that aren't escaped via `&`.  So `if DEBUG { ... }`
written in the same package as `var DEBUG readonly bool = false`
will reliably get DCE'd at `-O2`, but it's an optimization-pass
property, not a language-level guarantee.

Cross-package, the same DCE wants link-time optimization (LTO) to
see all packages' resolved globals together.  Without LTO,
`pkg/b`'s `if a.DEBUG { ... }` is a runtime branch on a load from
a foreign symbol.  Both flow naturally from the var-has-storage
model; the per-build flag value is real data in the producing
package's object file, and any optimizer that sees that data can
fold through it.

**Open questions.**

- Strict vs. lax on unknown `-D` keys.  Typo-protection suggests
  strict (unknown package or unknown name = error).  But "set
  DEBUG globally for a subset of packages that care" is awkward
  without a wildcard.  Maybe `-Dpkg/a:DEBUG=true` is strict, and a
  future `-D*:DEBUG=true` (if we ever want it) is a separate
  opt-in.
- Reproducible builds.  The set of `-D` values effectively changes
  the output; should be part of a build's recorded fingerprint.
- `.bni` authoritativeness for the `#[config]` annotation.  Does
  the `.bni` carry the annotation (visible to consumers as
  documentation that this var is build-configurable), or only the
  `var` extern declaration?  The former is clearer documentation;
  the latter keeps `.bni` purely type-level.

### `ispod(T)` builtin — PROPOSED

**Compile-time type-property query: does T need destruction/copy handling?**

```binate
ispod(int)                      // true — primitive
ispod(*T)                       // true — raw pointer is just a word
ispod(*@T)                      // true — the raw pointer is still just a word
ispod(*[]T)                     // true — raw slice is just (ptr, len)
ispod(@T)                       // false — copy RefIncs, destroy RefDecs
ispod(@[]T)                     // false — same
ispod([N]T)                     // ispod(T)
ispod(struct{...})              // true iff every field is POD
```

**Semantics.** `ispod(T)` is `true` iff values of `T` can be copied by raw
`memcpy` and discarded with a no-op destructor — i.e., `T` is fully
characterized by `sizeof(T)` / `alignof(T)` and no refcount traffic is
needed on copy or destruction. Returns `bool`. Compile-time constant.

**Key subtlety:** `*@T` is POD. The raw pointer itself is a plain word —
copying it does not touch the managed target's refcount. The raw-pointer
contract is "you don't own this" by definition.

**Motivation.** Generic code (once generics land) that wants to pick a
fast `memcpy`-based path vs. a copy-constructor path. Serialization.
Runtime-library helpers that want to assert POD-ness at compile time.

**Name.** `ispod` fits because "plain old data" is the standard term for
this property. An alternative `ismanaged(T)` (= `!ispod(T)`) was
considered; `ispod` was chosen because the common use is to branch on
the fast path being available.
