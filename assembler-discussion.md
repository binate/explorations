# Binate Assembler — Detailed Design Discussion

This document captures the rationale, alternatives considered, and tradeoffs behind the assembler design decisions documented in `assembler-design.md`.

---

## 1. Integration Model: How Assembly Meets the Compiler

### The core question

How does hand-written assembly interact with Binate's compiled code? Three possible models:

1. **Assembly functions** (Plan 9 / Go model): whole functions in `.s` files, declared in `.bni`, called normally via the platform calling convention
2. **Inline assembly** (GCC/LLVM model): assembly blocks embedded in Binate source, with operand constraints linking Binate values to registers
3. **Compiler intrinsics**: special instructions known to the compiler, emitted directly into the code stream

### Why not inline assembly?

GCC/LLVM-style inline asm (`asm("add %0, %1, %2" : "=r"(out) : "r"(in))`) was considered and rejected:

- The **constraint language** is its own mini-DSL that's notoriously hard to use correctly
- It **interacts with the register allocator** — the compiler must understand which registers are used, clobbered, and available, creating deep coupling between the assembler and the compiler's internals
- The **parsing problem** is significant — the compiler must parse architecture-specific assembly syntax within Binate source

An alternative was considered: `#[asm("arch")]` annotated functions that follow the platform calling convention, where the compiler could optionally inline the function body (copying the bytes). This was rejected because inlining only works for trivial cases — if the function has arguments, the compiler would still need to set up registers per the calling convention, defeating the purpose of inlining. For a function like an atomic operation, where the whole point is a tight instruction sequence, the call/return overhead matters but so does the argument setup overhead.

### The chosen split

**Assembly functions** for anything substantial (whole functions in `.s` files) + **compiler intrinsics** for special instructions that need tight integration with surrounding generated code.

This is clean because:
- No inline asm parsing or constraint language needed
- No interaction between user-written assembly and the register allocator
- The assembler is purely a separate tool, not embedded in the compiler
- Intrinsics cover the cases where tight integration matters (atomics, barriers, special instructions)
- Adding a new intrinsic is a compiler change, but the set of instructions worth intrinsifying is small and stable per architecture
- This is exactly what Go does, and it works well in practice

---

## 2. Assembly Syntax

### Instruction syntax: vendor/reference manual syntax per architecture

The key decision: each architecture uses the syntax from its own reference manual.

For ARM32, AArch64, MIPS, RISC-V, PowerPC, and most RISC architectures, GNU gas already uses essentially the same instruction syntax as the vendor. The differences between gas and vendor tools are almost entirely in directives (`.global` vs `EXPORT`, `.word` vs `DCD`), not in how instructions are written.

x86 is the exception. GNU gas defaults to AT&T syntax (source-first operand order, `%` register prefix, `$` immediate prefix, size suffixes on mnemonics), which differs fundamentally from Intel's own syntax (destination-first, no prefixes, size specified on memory operands). The AT&T conventions were inherited from PDP-11/Unix toolchains and applied to x86 where they don't fit — a historical accident that wasn't repeated for later architectures. Even gas supports Intel syntax via `.intel_syntax noprefix`.

**Decision: Intel syntax for x86, vendor syntax for everything else.** This means "use whatever the architecture's reference manual uses" — consistent principle, and instructions can be copied from reference manuals directly.

### Unified directives and labels

While instruction syntax varies per architecture, directives, labels, comments, and expression syntax are standardized across all architectures. This means assembly files for different architectures look the same except for the instructions themselves.

### Comments: `//` only

Line comments use `//`. Single style across all architectures.

**Alternatives considered:**
- `;` — common in NASM, ARM assemblers, but conflicts with statement separation in some contexts
- `#` — used by gas on some architectures, conflicts with preprocessor directives
- `@` — ARM gas comment character, conflicts with Binate's managed pointer sigil
- `/* */` block comments — adds parser complexity for marginal benefit in assembly files

`//` is familiar from Binate and C, unambiguous, and doesn't conflict with any instruction syntax.

### Section naming: no dot prefix

Section names in directives don't carry a leading dot — the assembler maps abstract names to format-specific representations. `text` becomes `.text` in ELF and `__TEXT,__text` in Mach-O.

The leading dot on section names is an ELF artifact, not meaningful to the programmer. Dropping it keeps assembly files portable across object formats without requiring format-specific section names.

Well-known names (`text`, `data`, `rodata`, `bss`) get default flags. Custom sections require explicit flags. Defaults are overrideable.

### Local labels: NASM-style dot-prefix scoping

Local labels use a leading dot and are scoped to the preceding non-local (global) label.

**Alternatives considered:**

| Approach | Mechanism | Pros | Cons |
|---|---|---|---|
| **gas numeric** (`1:`, `1f`/`1b`) | Direction-based nearest match | Maximally reusable, no naming needed | Hard to read, fragile under code reordering |
| **NASM dot-prefix** (`.loop:`) | Scoped to preceding global label | Readable, simple implementation (name mangling) | Names can collide when cut-and-pasting blocks |
| **MASM PROC/ENDP** | Scoped to explicit procedure delimiters | Most structured | Requires delimiter syntax, adds complexity |
| **armasm ROUT** | Scoped to explicit ROUT regions | Explicit boundaries | Numeric labels within regions, less readable |
| **MASM `@@:`** | Single anonymous label slot | Minimal | Only one at a time, very limited |

**Chose NASM style** — best balance of readability and simplicity. The cut-and-paste collision issue is minor in practice, and the implementation is trivial (prepend the preceding global label name).

### Data emission: Binate-style type names with fixed sizes

**Alternatives considered:**

| Option | 1 byte | 2 bytes | 4 bytes | 8 bytes | Notes |
|---|---|---|---|---|---|
| **A: byte counts** | `.d8` | `.d16` | `.d32` | `.d64` | Maximally unambiguous |
| **B: English names** | `.byte` | `.half` | `.word` | `.dword` | More readable but `.word` is ambiguous across architectures |
| **C: Binate types** | `.uint8` | `.uint16` | `.uint32` | `.uint64` | Matches the language |

**Chose Option C** (Binate type names) with extensions:
- Signed variants (`.int8` through `.int64`) for negative literal validation
- Floating point (`.float32`, `.float64`) for IEEE 754 data
- Target-dependent `.int`/`.uint` for word-sized values

Option B was rejected primarily because `.word` means different things on different architectures in gas (2 bytes on x86, 4 bytes on ARM) — exactly the kind of historical inconsistency we're trying to avoid.

The signed/unsigned distinction is purely about what literals are accepted — `.int8 -1` and `.uint8 0xff` emit the same byte. But it catches mistakes (`.uint8 -1` is an error) and improves readability.

Target-dependent `.int`/`.uint` are justified because assembly is already architecture-dependent (endianness affects anything larger than a byte), and word-sized values are common (pointer-sized data, function addresses).

### Alignment: always in bytes

`.align N` always means "align to an N-byte boundary."

Gas's `.align` means power-of-two on some targets and byte count on others — one of its worst inconsistencies. Always meaning bytes eliminates this confusion.

### Fill: byte-only

`.fill count, byte_value` — always fills with single bytes.

Gas's `.fill repeat, size, value` allows multi-byte fill values, but the endianness semantics are unclear and the use case is rare. Byte-only is simpler. For multi-byte patterns, use repeated data emission directives or the library API.

### Constants: `name = expr`, no redefinition

**Alternatives considered:**

| Syntax | Used by | Notes |
|---|---|---|
| `name = expr` | gas, MASM, FASM | Reads as assignment, familiar |
| `.equ name, expr` | gas | Directive form, awkward argument order (name is an argument) |
| `name equ expr` | NASM | Bare keyword, not a dot-directive |

**Chose `name = expr`** — most readable, consistent with Binate's `const x = 5`, and doesn't waste a directive name on something that reads naturally as syntax.

**No redefinition.** Gas's `=` allows rebinding; `.equ` doesn't. Redefinition is confusing and the use cases (assembler-internal counters) are mostly subsumed by macros, which are deferred.

### Current position: `$`

Gas uses `.` (dot) for the current position; NASM uses `$`. We chose `$` because `.` is used for local label prefixes and directive prefixes, so reusing it for current position would create ambiguity.

### Expressions

Standard constant expression support: integer/float/character literals, arithmetic and bitwise operators, parentheses, label references, same-section label differences, `$` for current position. This covers practical needs without getting into macro territory.

---

## 3. Library Architecture

### Three-layer design

The assembler is structured as three independent layers:

1. **Shared core** — section management, labels, symbols, fixups, data emission. Architecture-independent.
2. **Per-architecture instruction encoding** — each architecture defines its own register constants, operand types, and instruction emit functions.
3. **Per-format object file emission** — ELF, Mach-O. Takes finalized assembler state and writes the output.

This mirrors Go's internal assembler structure (`cmd/internal/obj` + `obj/arm64` + linker).

### Per-architecture operand representation: tagged unions

Each architecture defines its own operand type as a tagged union (struct with `Kind` field). This is necessary because addressing modes vary dramatically across architectures:

- ARM64: register, immediate, shifted register, extended register, memory with immediate offset, memory with pre/post-index, memory with register offset, PC-relative label
- x86: register, immediate, memory with base + index*scale + displacement, PC-relative
- ARM32: register, immediate (with rotation), shifted register, memory with various modes, register list (for LDM/STM)

**Alternative considered: one emit function per instruction form** (e.g., `AddReg`, `AddImm`, `AddShifted`). This avoids tagged unions but causes function count explosion — ARM64 `ADD` alone has register, immediate, shifted register, and extended register forms. x86 is worse. Operand structs keep the function count manageable while still being type-safe within each architecture.

### Fixup mechanism

When an instruction references a label, the emit function encodes a placeholder and records a fixup with an architecture-specific kind (e.g., `AARCH64_BRANCH26`, `X86_REL32`). The fixup kind tells the finalizer which bits to patch, what scaling to apply, and whether the reference is PC-relative.

On finalization:
- Same-section references are resolved by patching encoded bytes
- Cross-section or external references become relocations in the output file

This separation means the per-architecture code only needs to know how to encode instructions and record fixups — it doesn't need to know about object file formats.

### Compiler backend integration

The compiler backend calls per-architecture emit functions directly, going from IR to encoded bytes with no text assembly intermediate. This is the primary use case for the library — the CLI/text assembler is a secondary interface built on top of the same library.

---

## 4. Bootstrapping

The assembler is written in Binate. This raises the question of which subset of Binate to target:

**Bootstrap subset**: can test via the Go interpreter (fast edit-run cycle), but no interfaces or generics. Operand types would be tagged unions (the same pattern as the self-hosted compiler's AST). Workable but less elegant.

**Full Binate**: interfaces and generics would be natural for an assembler (e.g., an `Encoder` interface, generic encoding table helpers). But requires compiling via the LLVM-backed compiler for testing, which is a slower iteration cycle.

The decision doesn't need to be made upfront — starting in the bootstrap subset and refactoring once the native backends exist (and provide a fast compile cycle) is viable. The self-hosted compiler's AST already proves the tagged-union approach works at scale.

---

## 5. Multi-Architecture Coverage

### Priority order

1. **ARM32** — highest priority for the language's 32-bit target story
2. **AArch64** — practical for development (primary dev machine is AArch64)
3. **x86-64** — important for CI, common Linux servers, broad adoption

### Testing strategy

ARM32 binaries tested via QEMU user-mode emulation (`qemu-arm`) on the development Mac. This is already planned for the ARM32 compiler backend (see `ir-backend-cleanup-plan.md`). AArch64 and x86-64 can be tested natively on appropriate hardware or via CI.

---

## 6. Object Format Output

### Pluggable format emission

The shared core produces architecture-independent data structures (sections with bytes, symbol table, relocation list). Per-format packages serialize these to ELF or Mach-O.

**Practical order**: Mach-O may come first (primary dev machine), ELF for ARM32/Linux targets. Both should be straightforward since the core data model maps cleanly to either format.

### Format-specific concerns deferred

Exact details of ELF section flags, Mach-O segment/section mapping, symbol table entries, relocation encoding, and debug info format are deferred to implementation time. The design accommodates them without prescribing specifics.
