# Binate — Phase 5: Self-Hosted Toolchain

> **NOTE (2026-03-31):** `append` has been fully removed from the Binate language. References to `append` as a builtin in this historical plan are outdated.

This plan covers the transition from the Go bootstrap interpreter to a self-hosted Binate toolchain — interpreter, compiler, and supporting tools.

Reference documents:
- `claude-plan-1.md` — Phases 1–4 (language design through bootstrap interpreter)
- `claude-notes.md` — running design decisions
- `claude-discussion-detailed-notes.md` — detailed rationales

---

## Big-Picture Decisions

### Interpreter first, then compiler

We should write the **interpreter first**, then the compiler. Reasons:

1. **Shared frontend.** The lexer, parser, and type checker are the same for both. Writing the interpreter first means we build and validate the entire frontend before tackling codegen. The interpreter's backend is just a tree-walker — comparatively simple.

2. **Self-hosting milestone sooner.** A working self-hosted interpreter proves the language can express its own toolchain. This is a meaningful checkpoint before the much larger compiler effort.

3. **Development platform.** Once the self-hosted interpreter works (running on the Go bootstrap), we can use it to develop the compiler. The Go bootstrap is slow but functional; the self-hosted interpreter running on it will be slow too, but it validates correctness.

4. **Incremental testing.** We can test the self-hosted interpreter against the exact same test programs as the Go bootstrap. If both produce the same output, we know the frontend is correct. Then adding codegen is a pure extension, not a rewrite.

The bootstrapping chain is:
```
Go bootstrap interprets → self-hosted interpreter (in Binate)
Self-hosted interpreter runs → self-hosted compiler (in Binate)
Self-hosted compiler compiles → itself (native binary)
Native compiler compiles → everything else
```

### One repo to start, split later

Starting with a single repo (`binate/binate` or similar) is the right call:

- **No cross-repo dependency tooling.** Binate has no package manager. Managing dependencies across repos would require manual coordination or building tooling we don't need yet.
- **Shared code evolves together.** The frontend packages (lexer, parser, ast, types) will be heavily iterated as we discover what the self-hosted version needs. Having them in one repo makes refactoring trivial.
- **Package boundaries are already clean.** Binate's package system (`pkg/lexer`, `pkg/parser`, etc.) enforces separation regardless of repo structure. Moving to separate repos later is a mechanical operation — split directories, no code changes needed.
- **Premature splitting creates friction.** If the AST needs a new node, we'd have to coordinate across repos. In one repo, it's a single commit.

We can split into separate repos once the boundaries are stable (probably after the compiler is self-hosting). The likely future split:
- `binate/core` — lexer, parser, ast, types, token (shared frontend)
- `binate/interp` — tree-walking interpreter
- `binate/compiler` — IR, optimization, codegen, backends, linker
- `binate/tools` — formatter, linter, bni generator, etc.

### Repo and package layout

```
binate/
  main.bn                        entry point (dispatches to subcommands?)
  pkg/
    token/                        token types and positions
      token.bni + token/*.bn
    ast/                          AST node types
      ast.bni + ast/*.bn
    lexer/                        tokenizer + semicolon insertion
      lexer.bni + lexer/*.bn
    parser/                       recursive descent parser
      parser.bni + parser/*.bn
    types/                        type system and checker
      types.bni + types/*.bn
    interp/                       tree-walking interpreter
      interp.bni + interp/*.bn
    ir/                           intermediate representation (later)
      ir.bni + ir/*.bn
    codegen/                      backend interface + backends (later)
      codegen.bni + codegen/*.bn
    platform/                     OS/arch abstractions
      platform.bni + platform/*.bn
    fmt/                          code formatter (later)
    lint/                         linter (later)
    bnigen/                       .bni file generator (later)
```

---

## Phase 5a: Self-Hosted Interpreter

### What we're building

A Binate program that, when run on the Go bootstrap interpreter, can itself interpret Binate programs. It must support the full bootstrap subset (everything the Go interpreter supports), because eventually it needs to run the compiler source code.

### Step 1: Port the frontend

The frontend is the bulk of the shared code. Port in this order (each package depends on the previous):

**1. `pkg/token`** — Token types, positions, keywords
- Straightforward data definitions
- Token enum via `iota` const groups
- Position struct with file/line/col
- Keyword lookup (map or linear scan)
- Smallest package, good warmup for writing real Binate

**2. `pkg/ast`** — AST node types
- Struct definitions for every node type
- This is mostly type declarations — a good test of struct and pointer ergonomics
- Will expose limitations in the bootstrap subset (no interfaces = no `ast.Node` base type)
- **Key design question:** Without interfaces, how do we represent "any expression" or "any statement"? Options:
  - Tagged union via distinct type + `switch` on tag (C-style)
  - Managed pointer to a struct with a `kind` field + type-specific data as `*any`
  - Wait until we add interfaces to the bootstrap
  - **Recommendation:** Use a `kind` field (int enum) + `*any` for now. It's ugly but works in the bootstrap subset. Once we self-host with interface support, we refactor to proper interfaces.

**3. `pkg/lexer`** — Tokenizer
- Character-by-character scanning
- String/char literal parsing
- Number literal parsing
- Automatic semicolon insertion
- Keyword recognition
- This is the first package that does real work; good integration test

**4. `pkg/parser`** — Recursive descent parser
- Largest package by far (the Go version is ~1500 lines)
- Pratt parsing for expressions (or hand-coded precedence climbing)
- Statement and declaration parsing
- Error recovery (at minimum: sync to next statement boundary)
- .bni interface file mode (bodyless function declarations)

**5. `pkg/types`** — Type system and checker
- Type representations (named, pointer, slice, array, struct, function)
- Scope/environment management
- Expression type checking
- Statement checking
- Package interface loading
- Cross-package type resolution

### Step 2: Port the backend (tree-walker)

**6. `pkg/interp`** — Tree-walking interpreter
- Runtime value representation
- Expression evaluation
- Statement execution
- Environment/scope management
- Managed pointer refcounting
- Builtin functions (print, println, append, panic, len)
- Package loading and cross-package calls

### Step 3: Port the driver

**7. `main.bn`** — CLI entry point
- Argument parsing
- File discovery and package loading
- Orchestration: parse → check → interpret

### Step 4: Self-test

- Run the self-hosted interpreter (on the Go bootstrap) against every test program
- Compare output with Go bootstrap output — must be identical
- Once passing: the self-hosted interpreter is correct

### What we'll discover along the way

Writing a real program in the bootstrap subset will surface gaps:

- **No interfaces.** The AST is naturally polymorphic — every expression, statement, and declaration is a different type. Without interfaces, we need a workaround (see above). **Alternatively, we could add interfaces to the bootstrap subset** — this is a real option since interfaces are already fully designed (vtable layout, impl declarations, method dispatch). Adding them to the Go bootstrap would be significant work but would make the self-hosted code much cleaner. Decision point: when we start writing `pkg/ast`.
- **No function values.** The parser likely wants callbacks or function tables. Without function values, we use switch statements.
- **No closures.** Environment capture for nested scopes must be explicit.
- **No generics.** Data structures (lists, maps) must be written per-type or use `*any` with casts.
- **Missing builtins?** We may need to add things to `pkg/bootstrap` (e.g., `stat`, `getenv`, string manipulation).

Each of these is a decision point: do we extend the bootstrap subset, or work around the limitation? The bias should be toward **working around it** unless the workaround is truly unworkable — the goal is to keep the Go bootstrap simple.

---

## Phase 5b: Self-Hosted Compiler

Once the self-hosted interpreter works, we extend it with compilation.

### Compiler architecture

```
Source → Lexer → Parser → AST → Type Checker → Typed AST
                                                    ↓
                                              IR Generation
                                                    ↓
                                            Optimization (optional)
                                                    ↓
                                              Code Generation
                                                    ↓
                                            Object File Emission
                                                    ↓
                                              Linking → Executable
```

### Intermediate Representation (IR)

We need an IR between the typed AST and machine code. Design goals:

- **SSA-based.** Static Single Assignment form is the standard for modern compilers. Each value is defined exactly once, making dataflow analysis and optimization straightforward.
- **Typed.** The IR carries type information from the checker. This enables type-specific codegen (e.g., managed pointer operations emit refcount adjustments).
- **Lowered progressively.** High-level operations (managed pointer creation, slice bounds checks, refcount adjustments) start as single IR nodes and get lowered to primitive operations before codegen.
- **Target-independent.** The IR should not mention registers, calling conventions, or instruction encodings. Those are the backend's job.

Key IR operations beyond the basics:
- `refcount_inc`, `refcount_dec`, `refcount_check_free` — managed pointer lifecycle
- `bounds_check` — slice/array access
- `nil_check` — pointer dereference safety
- `call`, `call_indirect` — direct and indirect function calls
- `alloc_managed`, `free_managed` — heap allocation with header

### Backend architecture

Each backend translates IR to machine code for a specific target:

```
pkg/codegen/
  codegen.bni          backend interface
  codegen/*.bn         shared utilities (register allocation, instruction selection helpers)
  x86_64.bni + x86_64/*.bn     x86-64 backend
  arm64.bni + arm64/*.bn       ARM64 backend
  llvm.bni + llvm/*.bn           LLVM IR emission backend
  (future: riscv64, wasm, ...)
```

A backend must provide:
- **Instruction selection.** Map IR operations to target instructions.
- **Register allocation.** Assign IR values to physical registers (linear scan is fine to start; graph coloring later).
- **Calling convention.** Function prologue/epilogue, argument passing, return values.
- **Object file emission.** Encode instructions and relocations into the target format.

**Start with one backend** — whichever architecture we're developing on (likely ARM64 for Apple Silicon or x86-64). Add the second backend once the first is solid.

**LLVM IR backend**: an alternative to custom codegen on "big" platforms. Emit LLVM IR and let LLVM handle instruction selection, register allocation, and optimization. This gives competitive native code quality quickly without writing a full backend per architecture. Custom backends are still needed for embedded/small targets where the LLVM toolchain is too heavy, but LLVM is the pragmatic path for desktop/server use. The pluggable backend interface should accommodate both custom and LLVM backends.

### Optimization

Optimization is optional and pluggable. The compiler should produce correct (if slow) code with no optimizations enabled. Optimizations to consider, roughly in order of impact:

1. **Constant folding and propagation** — evaluate compile-time-known expressions
2. **Dead code elimination** — remove unreachable code and unused definitions
3. **Inlining** — small functions, especially accessors and wrappers
4. **Common subexpression elimination** — avoid recomputing identical expressions
5. **Refcount elision** — the big one for Binate: skip refcount inc/dec when the compiler can prove the object stays alive (e.g., local-only managed pointers that don't escape)
6. **Escape analysis** — determine which `make`/`box` allocations can be stack-allocated
7. **Loop optimizations** — strength reduction, loop-invariant code motion

Refcount elision (#5) and escape analysis (#6) are particularly important for Binate because they directly address the overhead of managed memory. These should be prioritized over generic optimizations.

### Object files and linking

Two viable strategies:

**Option A: Emit platform-native object files directly.**
- Emit ELF (Linux), Mach-O (macOS), PE/COFF (Windows) object files.
- Use the system linker (`ld`, `link.exe`) or a bundled one.
- **Pro:** Immediate interop with C libraries and system tools. `nm`, `objdump`, debuggers all work.
- **Con:** Must implement 3+ object file formats. Platform-specific details leak into the compiler.

**Option B: Emit a Binate-specific object format, convert for linking.**
- Define a simple intermediate object format (basically: sections, symbols, relocations).
- Write converters to ELF/Mach-O/PE as a final step.
- **Pro:** Compiler internals are platform-independent. Object format can carry Binate-specific metadata (type info, refcount hints, debug info).
- **Con:** Extra conversion step. Tools like `nm` don't understand the native format.

**Recommendation: Option A to start, with clean abstraction.** The object file emitter should be behind an interface so we can swap strategies later. But starting with direct ELF/Mach-O emission means we get working executables sooner without building converter tools. We only need to support the platform we're developing on initially.

For linking, start by shelling out to the system linker. Writing our own linker is a significant project that can be deferred — it's needed eventually (for hermetic builds and cross-compilation) but not for initial self-hosting.

### Inline assembly

We'll need inline assembly for:
- System calls (no libc dependency for core operations)
- Hardware-specific operations (e.g., atomic instructions, cache control)
- Performance-critical inner loops (eventually)

Proposed syntax (language spec addition):
```
// Single-architecture block
#[asm("x86_64")]
func syscall3(num int, a1 int, a2 int, a3 int) int {
    // raw assembly, registers mapped to parameters by convention
    asm {
        mov rax, num
        mov rdi, a1
        mov rsi, a2
        mov rdx, a3
        syscall
        // result in rax, mapped to return value
    }
}

// Multi-architecture with fallback
#[asm("arm64")]
func syscall3(num int, a1 int, a2 int, a3 int) int {
    asm {
        mov x8, num
        mov x0, a1
        mov x1, a2
        mov x2, a3
        svc #0
    }
}
```

This is a language spec change and should be designed carefully. Key questions to resolve later:
- How do parameters map to registers?
- How are return values specified?
- What about clobber lists?
- Do we support inline asm in regular functions, or only in `#[asm]`-annotated ones?
- Can we get away with a simpler model (e.g., only whole-function asm, no inline)?

**For initial self-hosting, we can avoid inline asm entirely** by having `pkg/bootstrap` (or a new `pkg/sys`) provide system call wrappers as builtins in the interpreter, and as pre-compiled object files for the compiler.

---

## Phase 5c: Self-Compilation

Once the compiler can compile other Binate programs:

1. **Compile the compiler with itself.** Run the compiler (on the interpreter, on the Go bootstrap) to compile its own source to a native binary.
2. **Verify.** The native compiler should produce identical output to the interpreted compiler for all test programs.
3. **Bootstrap complete.** The native compiler binary is the new reference toolchain. The Go bootstrap is no longer needed for development.

---

## Implementation Order

```
Phase 5a: Self-hosted interpreter          (this is next)
  1. pkg/token                              token types
  2. pkg/ast                                AST nodes
  3. pkg/lexer                              tokenizer
  4. pkg/parser                             parser
  5. pkg/types                              type checker
  6. pkg/interp                             tree-walker
  7. main.bn                                CLI driver
  8. Self-test                              validate against Go bootstrap

Phase 5b: Self-hosted compiler             (after interpreter works)
  1. pkg/ir                                 IR definition
  2. IR generation                          typed AST → IR
  3. pkg/codegen (one arch)                 IR → machine code
  4. Object file emission                   machine code → ELF/Mach-O
  5. Link via system linker                 object file → executable
  6. Second backend                         port to other arch
  7. Optimization passes                    optional, incremental

Phase 5c: Self-compilation                 (after compiler works)
  1. Compiler compiles itself               the big test
  2. Verify identical output                correctness proof
  3. Retire Go bootstrap                    native toolchain is primary

Tools (can be built anytime after 5a):
  - binate fmt                              code formatter
  - binate lint                             linter
  - binate bni                              .bni generator from source
```

---

## Open Questions

These don't need to be resolved now but should be addressed as we encounter them:

1. **AST representation without interfaces.** Three options: (a) tagged union with kind field + `*any`, (b) extend the Go bootstrap to support interfaces/impl/methods, or (c) some hybrid. Adding interfaces to the bootstrap is more upfront work but produces cleaner self-hosted code and avoids a painful refactor later. Decision point: when we start writing `pkg/ast`.

2. **Map/hash table.** The lexer needs keyword lookup, the type checker needs symbol tables. Maps are a library feature (no builtin `map` type — see design notes). Without generics in the bootstrap, use concrete map types per key/value combination (`StringToInt`, `StringToType`, etc.) — these translate mechanically to `Map[K, V]` once generics arrive.

3. **String operations.** The bootstrap has minimal string support. We'll need: concatenation (currently `+`?), substring, comparison, conversion to/from byte slices. May need to extend `pkg/bootstrap`.

4. **Error handling.** The Go bootstrap uses `panic` + recovery. The self-hosted version needs a strategy too. For now, `panic` with string messages is probably sufficient.

5. **Testing infrastructure.** How do we test the self-hosted toolchain? Run test programs and compare output? A built-in test runner? For bootstrapping, output comparison is simplest.

6. **Standard library boundary.** What's in `pkg/bootstrap` (Go-backed) vs. what's in pure Binate packages? The bias should be toward pure Binate — only things that genuinely need OS interaction belong in `pkg/bootstrap`.

7. **Debug info.** When should we start emitting debug info (DWARF)? Not for initial self-hosting, but eventually needed for a usable toolchain.

8. **Cross-compilation.** The compiler architecture should support cross-compilation from day one (just select a different backend). But we don't need to test it until after self-hosting.

### Deferred from bootstrap

Features that are fully designed but not implemented in the bootstrap subset:
- **Spread operator** (`...`): needed for `append(a, b...)` and variadic forwarding. Bootstrap uses `Concat` builtin for string concatenation instead.
- **Const types**: bootstrap does not support `const` in types. String literals are `[]char` (not `[]const char`).
- **Generics**: bootstrap uses concrete types per key/value combination.
- **Interfaces / impl / methods**: designed but not in bootstrap subset (decision pending on whether to add for AST representation).
- **Function values / closures**: workaround via switch statements.

---

## Current Status

**Phase 5a complete. Phase 5b (compiler) in progress — see claude-plan-3.md.**

| # | Package | Status | Tests | Notes |
|---|---------|--------|-------|-------|
| 1 | `pkg/token` | Done | 6 | Token types, positions, keyword lookup |
| 2 | `pkg/ast` | Done | 9 | Tagged union AST (Kind discriminators, managed pointers) |
| 3 | `pkg/lexer` | Done | 28 | Full tokenizer with ASI, char/string literals, comments |
| 4 | `pkg/parser` | Done | 44 | Recursive descent, all disambiguations (D1/D2/D4/D10) |
| 5 | `pkg/types` | Done | 58 | Type checker: scopes, type resolution, assignability |
| 6 | `pkg/interp` | Done | 100 | Tree-walking interpreter: values, env, builtins, cross-package calls |
| 7 | `pkg/loader` | Done | 32 | Package discovery, parsing, merging, topological sort |
| 8 | `pkg/ir` | Done | 34 | SSA IR: 55 ops, data structures, constructors, emitters |
| 9 | `pkg/ir/gen.bn` | Done | — | IR generation from AST (funcs, vars, arith, if/else, for, println) |
| 10 | `pkg/codegen` | Done | — | LLVM IR text emission |
| 11 | `runtime/` | Done | — | Minimal C runtime (print, exit) |
| 12 | `compile.bn` | Done | — | Compiler driver: parse → IR → LLVM IR → stdout |
| 13 | `main.bn` | Done | — | Multi-file driver with package loading and arg forwarding |
| 14 | Self-test | Done | 8 | selftest.bn: arithmetic, bools, loops, funcs, recursion, slices |
| 15 | Double interp | Done | — | bootstrap -> main.bn -> main.bn -> selftest.bn verified |
| 16 | Conformance | Done | 25 | Standalone test programs shared across backends (bootstrap + selfhost) |

### Bootstrap additions (beyond original plan)
- `ReadDir`, `Stat` builtins for package loader file discovery
- `append(nil, x)` and `len(nil)` handling (Go semantics)
- `StringVal` slicing support (produces `[]char SliceVal`)
- Bootstrap forwarding layer (`RegisterBootstrapPackage`, `callBootstrapBuiltin`)
- `SetArgs` for passing program arguments to inner interpreter

Total: **~270 self-hosted tests** passing (all run via Go bootstrap), 25 conformance tests passing on both bootstrap and self-hosted interpreter, plus 8 self-test checks through the full bootstrap → self-hosted → selftest chain.

### Key decisions made during implementation

- **AST representation**: tagged unions with `Kind int` discriminators. Each node type (Expr, Stmt, Decl, TypeExpr) is a single struct with a union of fields. Managed pointers (`@Expr`, `@Stmt`) for self-referential types. Works well without interfaces.
- **Testing convention**: `TestXxx() testing.TestResult` — return-value based (empty = pass, non-empty = failure message). No panic recovery needed.
- **char = uint8**: `char` is an alias for `uint8` in the bootstrap, matching the language design. Char literals produce uint8 values.
- **Parser style**: layered precedence functions for expressions (not Pratt), matching the bootstrap parser structure. Pratt-style `continueBinaryExpr` used only for the for-loop disambiguation path.
- **No variadic `append`**: bootstrap doesn't support `...` spread. String building uses `appendChars` helper loops instead.
