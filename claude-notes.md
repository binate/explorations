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

### Arrays and strings — IN PROGRESS

### Slices/views — IN PROGRESS

`char[]` (and arrays of unspecified size generally) are **slices** — a view into underlying data, not a container of data themselves.

**Terminology — IMPORTANT**: "managed-slice" (hyphenated) refers specifically to `@[]T`,
the 3-word type `(data_ptr, length, refptr)` created by `make_slice`. "Managed slice"
(two words, no hyphen) is ambiguous — it could mean `@[]T` (managed-slice) or `@([]T)`
(a managed pointer to a raw slice). In these notes we use the hyphenated form
"managed-slice" when referring to `@[]T` to avoid confusion.

**Two flavors**:
- **Managed-slice** (`@[]T`): keeps the underlying allocation alive via refcounting. Three words: (raw pointer to start of slice, length, managed pointer to allocation). The first two words match raw slice layout exactly.
- **Raw slice** (`[]T`): (raw pointer to start, length). Two words. No refcounting. Caller manages lifetime.

**Key benefits**:
- Fixed-size arrays (`char[123]`) don't need to store their length — it's captured in the slice when you create a view
- No wasted word for inlined arrays
- String literals (static data) can be sliced without allocation
- Subarrays are just slices with offset pointers

**String literals**: raw static data in the binary. Compiler generates wrapping code based on context:
- Assigned to a slice → slice pointing into static data
- Assigned to a managed array → allocate, copy, set up refcount
- Raw pointer → pointer to static data

**Managed-slice representation — DECIDED (updated 2026-03-30)**: a managed-slice (`@[]T`) is a raw slice followed by a managed pointer: `(data_ptr, length, refptr)` — three words. The first two words are identical in layout to a raw slice `[]T`, so `@[]T` → `[]T` conversion is trivial (just read the first 16 bytes, no field extraction needed). The refptr is the third word and carries the refcount. Equivalently:
1. Raw pointer to the start of the view (direct data access, no arithmetic needed)
2. Length
3. Managed pointer to the underlying allocation (keeps it alive via refcounting)

**Raw slice representation**: two words: (raw pointer to start, length)

**Constraint**: managed-slices can only refer to managed allocations. For stack/static data, use a raw slice. To pass stack/static data where a managed-slice is expected, copy into a managed allocation first. This maintains clean lifetime guarantees.

**API semantics**: managed-slice vs. raw slice at function boundaries communicates intent — managed-slice = "I will retain this," raw = "I just need it now."

**Introspection builtins**: for low-level transparency, testing, and debugging:
- Something that takes a managed pointer (`@T`) and returns the management header (refcount, free function) as a Binate struct.
- Something that takes a raw slice (`[]T`) and returns the slice representation (data ptr, length) as a Binate struct.
- Something that takes a managed-slice (`@[]T`) and returns the managed-slice representation (data ptr, length, refptr) as a Binate struct.
- All management/representation structs should be proper Binate structs, not opaque C constructs.
- These can have "obscure" names (e.g., `_refcount_header`, `_slice_repr`, or `bn_`-prefixed) since they're not intended for normal use.

**`append` — REMOVE**: `append` should be removed from the language entirely. It's a performance footgun (O(n) per call, O(n²) for incremental building) and doesn't fit the language's design. Growable collections belong in the standard library (e.g., a `Buffer[T]` or `Vec[T]` type with capacity management), not as a language builtin. Raw slices are fixed-size views; managed-slices can be backed by a library type that handles growth.

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
- Slice operations (append, get, set, slice expressions) — still in C runtime
- String-to-chars conversion, printing — still in C runtime

**End state**: declare external C library functions via compiler annotations (or a natural FFI mechanism) and remove the C runtime entirely. Pure Binate systems — where everything is written in Binate — should be possible. The C dependency exists only insofar as it's the practical way to talk to the OS; it's not a permanent architectural choice.

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

### Untyped pointers & casting — DECIDED

- **Managed `*any`**: pointer to refcounted allocation of unknown type. Refcounting works (management info is in the allocation). Must cast to use the data.
- **Raw `*any`**: just an address. Equivalent to C's `void*`.
- **`cast(T, expr)`**: value conversion (e.g., `cast(int, myFloat)`). Explicit, required between named types.
- **`bit_cast(T, expr)`**: reinterpret bits. No conversion, no checking. The "I know what I'm doing" escape hatch.
- Both are builtins (like `make`), not functions — they take types as first arguments.
- **Builtins are keywords** (not predeclared names): `make`, `make_slice`, `box`, `cast`, `bit_cast`, `len`, `unsafe_index`. They take types as arguments, which can't be parsed as regular function calls.

### Const-ness — DECIDED

**Compile-time constants**: `const x = 5` — value baked in.

**Const in types**: left-to-right reading, each `const` applies to the thing immediately to its right:
- `const *int` — const pointer to int (pointer can't change)
- `*const int` — pointer to const int (data can't change)
- `const *const int` — const pointer to const int
- `[]const *int` — slice of const pointers to int
- `[]*const int` — slice of pointers to const int

**Const on variable declarations**: means the variable can't be reassigned:
- `const x int = 5`
- `const p *int = &y` (p can't be reassigned, but *p can be modified)

**Const on function parameters**: `const` on the parameter variable itself (outermost const) is allowed but not part of the function's type signature. It's a local implementation detail, like parameter names — present for self-documentation and local discipline, ignored for signature matching.

**Deep immutability**: skipped for v1.

**Const and receivers — five receiver kinds**:
1. const value — read-only copy
2. const raw pointer — read-only view, no refcount
3. const managed pointer — read-only view, with refcount
4. raw pointer — mutable, no refcount
5. managed pointer — mutable, with refcount

Value receivers are always const (mutating a copy is pointless).

**Auto-conversion**: more-permissive → more-restrictive:
- managed → raw → const raw
- managed → const managed → const raw
- any pointer → value/const value (by copy)

**`impl` declarations specify receiver type.** The receiver kind determines what pointer/value types can satisfy the interface. Interfaces themselves say nothing about const-ness.
- `impl Stringer for FileHandle` with const receiver → `const *FileHandle` satisfies `Stringer`
- `impl Stringer for Widget` with mutable receiver → only `*Widget` satisfies `Stringer`, not `const *Widget`

This means the same interface can be implemented with different receiver kinds by different types. No extra syntax needed in interface declarations.

### Volatile — DECIDED

Not a type qualifier (unlike C). Instead, builtin functions for volatile reads/writes. Volatility is at the point of access, not on the type. Avoids viral type annotations, keeps the type system simpler, and makes every volatile access explicit at the use site.

### Type system — IN PROGRESS

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
- `"abc"` → `char[4]` (if null-terminated?), `char[3]`, `char[]`, etc. — depends on null termination decision

**Default types** (when context is ambiguous, e.g., `x := 123`):
- Integer literals: `int`
- Float literals: `float64`
- String literals: `[]const char` (raw slice into static read-only data)
- Bool literals: `bool`

**Literal overflow**: assigning a literal to an explicit type that can't hold it is a compile error (`var x uint8 = 256` → error). Literals are checked at compile time for fit.

**Cast semantics**: `cast(T, expr)` on typed (non-literal) values wraps/truncates — hardware semantics, well-defined. `cast(uint, -1)` is a compile error (literal doesn't fit). `cast(uint, x)` where x is int wraps to UINT_MAX.

**Null termination**: always null-terminated for string literals. One byte of overhead per string, avoids ambiguity. Any string literal is safe to pass to C without copying.

**String literal → slice**: the raw storage includes the null, but a slice of it excludes the null. `"abc"` → storage is `{'a','b','c','\0'}` (4 bytes), but the slice is `(ptr, 3)`. `len()` returns 3. The null is there in memory for C interop but isn't part of the slice's view. This avoids the "does len include the null?" confusion.

**No `string` type.** `string` does NOT exist as a type in Binate. String literals are `[]const char` (or `[]char` in the bootstrap, which lacks const types). The bootstrap interpreter has `string` as an internal convenience alias for `[]char`, but self-hosted code must use `[]char`. Language targets small systems where full UTF-8 support is too heavy to justify a separate type.

### Type system richness

**Generics**: originally punted, but reconsidering — see discussion below.

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

For the bootstrap (no generics), two viable approaches:
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

### Interfaces — IN PROGRESS (revised)

Explicit, declared interfaces with **separate `impl` declarations** and **methods defined outside `impl` blocks**.

**Core design:**
- Interfaces are declared with a set of method signatures
- `impl` declarations are separate from both the struct definition and the method definitions
- Methods use Go-style receiver syntax, defined outside impl blocks — not tied to a single file
- Vtable-based dynamic dispatch; compiler may devirtualize as an optimization
- Interface values follow the managed/raw pattern:
  - Raw interface value (e.g., `Stringer`): (raw ptr to data, vtable ptr) — no refcounting, caller keeps data alive
  - Managed interface value (e.g., `@Stringer`): (managed ptr to data, vtable ptr) — keeps data alive via refcounting
- Both are value types (small, copyable)

**Built-in implicit interfaces**: a small, closed, language-defined set of interfaces implicitly implemented by all types. `any` is the primary one (provides `void*`/type-erasure equivalent). Others may be added (e.g., `Sized`) but only by the language spec — user-defined interfaces are always explicit.

**Interface extension**: supported. An interface can extend one or more other interfaces.

**Separate `impl` for types defined elsewhere**: naturally supported by the model. Scoping rules (who can declare an impl) TBD.

**Three receiver kinds**:
- Value receiver: gets a copy. Good for small types, builtins.
- Raw pointer receiver: direct access, no refcount overhead. Common case even for managed objects.
- Managed pointer receiver: bumps refcount for duration. Needed when method might cause self-destruction.

**Receiver smoothing**: compiler auto-converts at call sites. Safe direction only: managed → raw → value (copying). Cannot auto-promote raw → managed.

**Package interface files**: can contain forward declarations of interfaces, types, impl declarations, and method signatures — without bodies.

**Impl syntax — DECIDED**: `impl Type : Interface, Interface2, ...`
- Type-first, colon separator, comma-separated interfaces
- Leading keyword for parser, reads naturally
- Receiver type specified on the type side:

```
impl FileHandle : Stringer           // value receiver
impl *FileHandle : Writer, Reader    // raw pointer receiver
impl @FileHandle : Retainable        // managed pointer receiver
impl *const FileHandle : Stringer    // const raw pointer receiver
```

**Example sketch:**
```
type Writer interface {
    write(buf []char) int
    close()
}

type FileHandle struct {
    fd int
}

impl *FileHandle : Writer

func (f *FileHandle) write(buf []char) int { ... }
func (f *FileHandle) close() { ... }
```

**Generics — RECONSIDERED**:
- Generic types AND functions, with interface constraints on type parameters
- No type inference for generics — always spell out type params fully (can relax in v2)
- Monomorphized
- Type checking against interface constraints (checked once against the constraint, not per instantiation)

```
func sort[T Comparable](items []T) { ... }
sort[int](myArray)
```

**Boxing**: `make(T)` or similar as the standard way to box a value type into a managed allocation.

### Syntax direction — IN PROGRESS

C-family, leaning toward Go's direction (clean, minimal, familiar).

**Decided**:
- Type-after-name declarations (`x int` not `int x`) — more natural, especially for complex types
- `:=` short declarations — supported for ergonomics

- No semicolons (automatic insertion)
- **Multiple return values** (Go-style, not first-class tuples). First-class tuples were considered but reconsidered — they raise many type system questions (is `(int)` the same as `int`? named fields? nesting?) for limited practical benefit over Go-style multiple returns.
- Destructuring assignment for multiple returns: `x, y := foo()`

**Pointer syntax — DECIDED**:
- `*T` = raw pointer to T (C-like)
- `@T` = managed pointer to T
- `&x` = take raw address of x
- `make(T)` = allocate managed T (zero-init), returns `@T` (any type T, no size arg)
- `make_slice(T, n)` = allocate runtime-sized managed-slice, returns `@[]T`
- `box(expr)` = allocate managed copy of value, returns `@T` (e.g., `box(Point{x: 1})`, `box(42)`)
- Forward-compatible with non-nullable pointers (no intermediate nil state)
- `.` auto-dereferences (Go-style, no `->`)
- Implicit conversion from `@T` to `*T` (safe: managed is "narrower"). Never implicit `*T` → `@T`.

**Slice syntax — DECIDED**:
- `[]T` = raw slice of T (two words: raw ptr, length)
- `@[]T` = managed-slice of T (three words: raw ptr, length, managed ptr) — syntactic sugar
- `*[]T` = raw pointer to a raw slice
- `@([]T)` = managed pointer to a raw slice (parens break the `@[]` sugar)
- `arr[low:high]` = slice expression (exclusive end, like Go)
- The `@[]` sugar is syntactic only: in generics, `@T` where `T=[]int` means `@([]int)` (managed pointer to raw slice), not managed-slice.

**Function syntax — IN PROGRESS**:
```
func add(a int, b int) int { return a + b }
func divmod(a int, b int) (int, int) { return a / b, a % b }
func (p *Point) translate(dx int, dy int) { p.x += dx; p.y += dy }
func (p *const Point) distance() float64 { ... }
```
- No named return values (confusing, not best practice)
- No same-type param shorthand (e.g., no `a, b int`)

**Variadic functions — DECIDED**:
- Go-style `...T` syntax, packages args as a slice
- Raw interface variadics (`...Stringer`) are zero-overhead: args packaged as (raw ptr, vtable ptr) pairs on the stack. No boxing, no heap alloc.
- Managed interface variadics (`...@Stringer`) for functions that retain args.
- `println`/`printf` likely compiler builtins for practical reasons, but user-defined variadic logging functions work efficiently with raw interface args.

### Spread operator — DECIDED

- `...` spread operator for passing slices to variadic functions and for `append`: `append(a, b...)`
- Syntax: `expr...` where `expr` is a slice — expands the slice into individual arguments
- Use cases: `append(a, b...)` for slice concatenation, passing slices to variadic functions (e.g., forwarding args in `printf` calling `sprintf`)
- Deferred from bootstrap subset — bootstrap uses `Concat` builtin for string concatenation instead

**Type declarations — DECIDED**:
- `type Celsius float64` — distinct new type, same representation. Requires `cast()` to convert. Can have methods and impl interfaces.
- `type byte = uint8` — alias, fully interchangeable. Cannot have methods.
- Named structs via `type`: `type Point struct { x int; y int }` — the only way to declare a named struct (no `struct Point{...}` shorthand, like Go).
- Distinct types from any type: pointers (`type Handle @SomeStruct`), slices (`type Buffer []uint8`), etc.
- Anonymous struct types: `struct{x int}` — structural equivalence (two occurrences of the same shape = same type). `type Foo = struct{x int}` is an alias for the anonymous type.
- Methods and `impl` require named types. Anonymous types cannot be receivers (Go's rule).

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

### Visibility & package interfaces — LEANING

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

### Self-hosting bootstrap — IN PROGRESS

**Strategy**: interpreter-first bootstrap.
1. Write a minimal interpreter in a host language (subset of Binate only)
2. Write the full interpreter and compiler in Binate
3. Use the minimal interpreter to run the Binate compiler → compile everything → native binaries
4. Discard the bootstrap interpreter. Fully self-hosted.

The compiler should have a backend architecture that supports cross-compilation from the start, so bootstrap doesn't need to happen on target (32-bit) systems.

**Current status**: Step 1 is complete. The Go bootstrap interpreter
(`github.com/binate/bootstrap`) can parse, type-check, and run `.bn` programs
covering the bootstrap subset: functions, structs, pointers (raw and managed), slices,
arrays, control flow, constants with iota, string indexing, and I/O via the `pkg/bootstrap`
package. Multi-file packages, `.bni` interface loading, user-defined package imports with
transitive dependency resolution, and runtime error reporting with source positions are
all implemented.

**Step 2 is complete** (self-hosted frontend and backend). All 10 packages of the
self-hosted toolchain are implemented: `pkg/token`, `pkg/ast`, `pkg/lexer`, `pkg/parser`,
`pkg/types`, `pkg/ir`, `pkg/codegen`, `pkg/linker`, `pkg/bootstrap`, and `pkg/interp`.
The self-hosted interpreter (`main.bn`) passes all 70 conformance tests. The self-hosted
compiler (`compile.bn`) produces native binaries via LLVM IR emission and system linking.

**Step 3 is in progress** (self-compilation). The bootstrap interpreter can run
`compile.bn` to compile `compile.bn` itself, producing a ~410KB native binary. The
self-compiled compiler runs (prints usage) but segfaults when actually compiling programs —
debugging the self-compiled binary is the current frontier.

**Conformance test coverage**: 70 tests, run in 5 modes:
- `bootstrap` — Go bootstrap interpreter runs `.bn` directly (70/70 pass)
- `selfhost` — bootstrap interprets `main.bn`, which runs `.bn` (70/70 pass)
- `compiled` — bootstrap interprets `compile.bn`, compiles `.bn` to native (58/70 pass)
- `compiled-interp` — self-compiled interpreter binary runs `.bn` (not yet working)
- `compiled-compiler` — self-compiled compiler binary compiles `.bn` to native (not yet working)

Note: many items marked "IN PROGRESS" above were resolved during the grammar
specification phase (Phase 3). See `grammar.ebnf` for the authoritative specification
and `claude-bootstrap-plan.md` for implementation status.

**Host language for bootstrap interpreter**: Go

### Operators — DECIDED

**Arithmetic**: `+`, `-`, `*`, `/`, `%`
- Integer division truncates toward zero (C99+/Go/hardware behavior)
- `%` result has same sign as dividend (`-7 % 2 = -1`); identity `(a/b)*b + a%b == a` holds
- Division by zero: runtime trap (defined behavior, not UB)
- Integer overflow: wrapping (two's complement). No UB — systems language, matches hardware.

**Bitwise**: `&`, `|`, `^`, `~`, `<<`, `>>`
- `>>` is arithmetic for signed types, logical for unsigned (C/Go/Rust behavior). No separate `>>>`.
- Shift by >= bit width: defined behavior (zero for `<<` and logical `>>`, sign-extended for arithmetic `>>`)

**Comparison**: `==`, `!=`, `<`, `>`, `<=`, `>=`
- No chaining (`a < b < c` is a compile error, like Go)
- Pointer comparison with `==`/`!=` (address equality only)

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
make([]int)              // @([]int), managed pointer to zero-value raw slice

make_slice(int, n)       // @[]int, runtime-sized managed-slice, n zero-init elements

box(42)                  // @int, box a literal (takes an expression)
box(x)                   // @T where x: T, copies value
box(Point{x: 1, y: 2})  // @Point, allocate and init
```

- `make(T)` always takes a type, returns `@T`. Zero-initializes. Works for ANY type T,
  including `[]T` (→ `@([]T)`, managed ptr to raw slice) and `[k]T` (→ `@([k]T)`,
  managed ptr to fixed-size array). No size argument.
- `make_slice(T, n)` takes an element type and runtime size. Returns `@[]T` (managed-slice
  — the special 3-word type). This is the ONLY way to create runtime-sized managed-slices.
  Separate builtin because `make([]T, n)` is ambiguous (does it return `@([]T)`
  or `@[]T`?). **Always returns managed-slice** — a non-managed version makes no sense,
  since you'd be allocating memory with no way to free it.
- `box` always takes a value expression. Allocates and copies. No ambiguity.
- No capacity argument — growing is a library concern (CharBuf, Vec[T], etc.)

**Notation — DECIDED**: `@([k]T)` (with parens) for managed pointer to fixed-size array,
to distinguish from `@[]T` (managed-slice sugar). `@[k]T` is ambiguous and should not
be used. The `@[]` sugar applies ONLY to `@[]T` (managed-slice); all other combinations
use explicit parens to break the sugar.

### Method resolution & dispatch — DECIDED

**One method per name per base type.** No overloading on receiver kind. A method name is defined once, regardless of whether the receiver is value, `*T`, or `@T`.

**Auto-dereferencing**: one level only (like Go). If `obj` is `@T` or `*T`, compiler looks for methods on the pointer type and on `T`.

**Receiver conversion** at call sites (safe direction only):
- `@T` → `*T` → `*const T` (implicit)
- `@T` → `@const T` → `*const T` (implicit)
- Any pointer → value (by copy)
- `*T` → `@T`: never implicit

**Value receivers implemented as `*const T`** under the hood. Avoids copying large structs. The compiler knows value receiver pointers are never null.

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

### Generics — DECIDED

**Type parameters** on functions, structs, and interfaces:
```
func sort[T Comparable](items []T) { ... }
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

**Nil slices**: slices cannot be compared to `nil`. `nil` is only for pointer types (`*T`, `@T`). Slices are value types — check `len(s) == 0` for empty. Use `*[]T` or `@[]T` if you need optional/nullable slice semantics.

**Indexing**: zero-based. `s[i]` reads/writes element. `s[low:high]` creates a sub-slice (exclusive end). `s[:]`, `s[low:]`, `s[:high]` are shorthand forms. All bounds-checked.

**`len()`**: returns slice length field, or compile-time constant for fixed-size arrays. String literal slices: length excludes null terminator.

### Comparison points
- **Forth**: simple, dual-mode (threaded interpretation + compilation), embeddable, used in firmware — but very different paradigm
- **Lua**: embeddable interpreter, small footprint, interop with C — but not a systems language
- **Zig**: systems language, simple, C interop — but no interpretation story
- **Terra/Lua combo**: compiled (Terra) + interpreted (Lua) interop — closest existing analog?

---

## Next Steps

Phases 1–4 are complete. See `claude-plan-1.md` for the full record.

**Phase 5: Self-hosted toolchain** — see `claude-plan-2.md` for the detailed plan. Key decisions:

1. **Interpreter first, then compiler.** Shared frontend (lexer, parser, types) is the bulk of the work. Interpreter adds just a tree-walker; compiler adds IR, codegen, backends.
2. **Single repo to start** (`binate/binate`). Split into core/interp/compiler repos once boundaries stabilize.
3. **Compiler architecture**: SSA-based IR, pluggable backends (x86-64, ARM64, LLVM IR), optional optimization passes (refcount elision and escape analysis prioritized). LLVM IR backend gives quality native codegen on big platforms quickly; custom backends needed for embedded targets where LLVM is too heavy.
4. **Object files**: emit platform-native formats (ELF/Mach-O) directly, shell out to system linker initially.
5. **Inline assembly**: `#[asm("arch")]` annotation syntax proposed; deferred for initial self-hosting.
6. **AST representation — DECIDED**: tagged unions (structs with `Kind int` fields). Without interfaces in the bootstrap subset, each AST node type (Expr, Stmt, Decl, TypeExpr) is a single struct with a Kind discriminator and union of fields. Managed pointers (`@Expr`, `@Stmt`) enable self-referential types. Two-pass type resolution (pre-register placeholders, then resolve) handles forward references.

### Testing convention — DECIDED

Unit testing built into the toolchain with a lightweight, convention-based approach:

- **Test files**: `*_test.bn`, live alongside regular `.bn` files in the same package directory
- **Same-package tests**: test files use the same `package` declaration as the code they test (no separate `foo_test` package). They can access all symbols including unexported helpers.
- **Exclusion by default**: `_test.bn` files are excluded from normal builds. Only included when the package is a `-test` target.
- **Test functions**: `TestXxx() testing.TestResult` — no parameters, returns `testing.TestResult`. Discovered automatically by name prefix and signature.
- **Failure signaling**: return a non-empty string (the failure message). Empty string means pass. No panic recovery needed — works identically in interpreter and compiled code.
- **`pkg/builtin/testing`**: provides `type TestResult = []char`. Test files import this package.
- **CLI**: `binate -test [-root dir] <pkg/foo> [pkg/bar ...]` — supports multiple packages in one invocation.
- **Output format**: Go-style (`=== RUN`, `--- PASS`/`--- FAIL`, `ok`/`FAIL` per package, summary).

Design rationale: minimal complexity, works within the bootstrap subset (no interfaces, no generics, no closures needed), and works identically in interpreted and compiled code (no panic recovery needed). The convention is close enough to Go's that it feels familiar, but simpler (no `testing.T` parameter, no sub-tests). Wrong-signature `TestXxx` functions produce a warning.

### `append` — REMOVE (decided)

`append` is being removed from the language. See the "append — REMOVE" note above.
Growable collections are a library concern: `CharBuf` for strings, `Vec[T]` (post-generics)
for general lists. `make_slice` provides the primitive for allocating managed-slices;
library types handle growth/capacity on top of that.

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

### Debugging process improvements — TO DISCUSS

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
