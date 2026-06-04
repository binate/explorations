# Binate — Phase 5: Self-Hosted Toolchain

> **Status: COMPLETE (shipped).** The self-hosted toolchain is implemented and stable; the Go bootstrap interpreter has been retired (2026-05-21). This doc is kept for design rationale (interpreter-first, repo-split sketch, IR/backend/object-file/inline-asm direction). The per-step execution plan that originally lived here has been removed as spent. See `claude-todo.md` for current work and `claude-plan-3.md`+ for compiler-phase plans.

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

---

## Compiler design notes (Phase 5b)

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

Each backend translates IR to machine code for a specific target. A backend must provide:
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

## Open Questions

These don't need to be resolved now but should be addressed as we encounter them:

1. **Map/hash table.** The lexer needs keyword lookup, the type checker needs symbol tables. Maps are a library feature (no builtin `map` type — see design notes). Without generics in the bootstrap, use concrete map types per key/value combination (`StringToInt`, `StringToType`, etc.) — these translate mechanically to `Map[K, V]` once generics arrive.

2. **String operations.** The bootstrap has minimal string support. We'll need: concatenation (currently `+`?), substring, comparison, conversion to/from byte slices. May need to extend `pkg/bootstrap`.

3. **Standard library boundary.** What's in `pkg/bootstrap` (Go-backed) vs. what's in pure Binate packages? The bias should be toward pure Binate — only things that genuinely need OS interaction belong in `pkg/bootstrap`.

4. **Debug info.** When should we start emitting debug info (DWARF)? Not for initial self-hosting, but eventually needed for a usable toolchain.

5. **Cross-compilation.** The compiler architecture should support cross-compilation from day one (just select a different backend). But we don't need to test it until after self-hosting.

---

## Key decisions made during implementation

- **AST representation**: tagged unions with `Kind int` discriminators. Each node type (Expr, Stmt, Decl, TypeExpr) is a single struct with a union of fields. Managed pointers (`@Expr`, `@Stmt`) for self-referential types. Works well without interfaces. (This resolved the original open question of how to represent "any expression"/"any statement" without interfaces: a `kind` field + union, rather than adding interfaces to the bootstrap.)
- **Testing convention**: `TestXxx() testing.TestResult` — return-value based (empty = pass, non-empty = failure message). No panic recovery needed.
- **char = uint8**: `char` is an alias for `uint8` in the bootstrap, matching the language design. Char literals produce uint8 values.
- **Parser style**: layered precedence functions for expressions (not Pratt), matching the bootstrap parser structure. Pratt-style `continueBinaryExpr` used only for the for-loop disambiguation path.
- **No variadic `append`**: bootstrap doesn't support `...` spread. String building uses `appendChars` helper loops instead. (`append` has since been removed from the language entirely — see note at top.)

### Bootstrap additions (beyond original plan)

These builtins/hooks were added to the Go bootstrap to support the self-hosted toolchain (the Go bootstrap has since been retired; recorded here as the sole written record):
- `ReadDir`, `Stat` builtins for package loader file discovery
- `append(nil, x)` and `len(nil)` handling (Go semantics)
- `StringVal` slicing support (produces `*[]char SliceVal`)
- Bootstrap forwarding layer (`RegisterBootstrapPackage`, `callBootstrapBuiltin`)
- `SetArgs` for passing program arguments to inner interpreter
