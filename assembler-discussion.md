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

## 4. Core Data Structures

### Growing byte buffers

Sections accumulate bytes as instructions and data are emitted. Binate has no built-in `append` — growable collections are a library concern. The existing `CharBuf` type handles growable `*[]char` buffers. Since `char = uint8`, a `ByteBuf` for section data can be type-aliased from `CharBuf` or trivially copied with `s/char/uint8/g`. This is pragmatic scaffolding until generics provide a proper `Vec[T]`.

### Symbol lookup

The self-hosted compiler uses linear scan everywhere — `Lookup()` in `pkg/types/scope.bn` iterates `@[]@Symbol` with `charEq` comparisons, as do field lookup, package lookup, and codegen symbol lookup. All O(n). This is sufficient for the compiler's symbol table sizes.

The assembler will start with the same approach. For large assembly files (thousands of symbols), sorted array + binary search would be an upgrade, but linear scan is fine initially.

### Fixup resolution: architecture callback

The shared core needs to patch bytes during `Finalize()`, but the patching logic is architecture-specific — different fixup kinds mean different bit fields, scaling factors, and PC-relative calculations.

**Approaches considered:**
- **Interface method**: clean, but requires interfaces (not in bootstrap subset)
- **Switch on architecture enum**: core dispatches to per-arch resolver based on `Assembler.arch`. Couples the core to all architectures.
- **Function pointer**: the assembler stores a resolver function pointer set at initialization. Per-arch packages provide the resolver. Core is decoupled.

**Chose function pointer** — keeps the core architecture-independent, works in the bootstrap subset, and is a single function pointer (not worth an interface even if interfaces were available).

---

## 5. Instruction Encoding

### Hand-coded vs table-driven

**Table-driven encoding** defines instructions as data (opcode pattern, operand field positions, bit widths) with a generic encoder that reads the tables. Benefits: tables can drive both assembly and disassembly, adding instructions is data not code, and the generic encoder is tested once. Costs: the framework is complex upfront, irregular encodings need escape hatches, and the table format is its own design problem.

**Hand-coded encoding** writes a function (or shares a helper) per instruction family. Benefits: simple, easy to audit per-instruction, irregular encodings are just code. Costs: more code per architecture, disassembly requires separate work, and patterns may be duplicated.

**Chose hand-coded** — simpler to get started, especially for ARM64 (likely first target). ARM64's fixed-width 32-bit instructions have enough regularity that internal helper functions naturally reduce duplication:

- Data processing instructions (ADD, SUB, AND, ORR, etc.) share a common bit layout — one helper per form (register, immediate, shifted register, extended register)
- Load/store instructions share encoding structure — helpers for immediate offset, register offset, pre/post-index
- Branch instructions share a common layout — helpers for conditional, unconditional, compare-and-branch

If the pattern of adding architectures later justifies it, a table-driven approach can be introduced — but it's not needed to ship the first architecture.

---

## 6. Text Parser

### Design: line-oriented, two layers

The text parser is intentionally simple — it's a line-oriented processor, not a full language parser. Read a line, parse it, call into the library, move on. No AST, no multi-pass analysis. The only state is the assembler itself (current section, symbol table, pending fixups).

The shared layer handles all architecture-independent syntax. The per-architecture layer handles instruction parsing. This split means adding a new architecture to the text parser requires only writing the instruction parser — all directive/label/expression handling is shared.

### Mnemonic dispatch: sorted array

A sorted array of `(mnemonic, handler)` entries, searched with binary search. Each handler is a function pointer that receives the assembler and the remaining tokens, parses operands, and calls emit functions.

**Alternatives considered:**
- **Hash map**: faster lookup but requires a hash map implementation (not available in the bootstrap subset without writing one)
- **Trie/prefix tree**: ARM64 mnemonics cluster by prefix, but the complexity isn't justified over sorted search for ~200-300 entries
- **Linear scan**: fine for small instruction sets but O(n) per line parsed

Sorted array is the right complexity level — O(log n) lookup with no extra data structure dependencies.

### Operand parsing challenges

ARM64 operand parsing has one notable complication: shifted register operands. In `add x0, x1, x2, lsl #3`, the comma-separated tokens are `x0`, `x1`, `x2`, `lsl #3` — but `x2, lsl #3` is a single compound operand (shifted register), not two operands. The parser handles this by greedy consumption: after parsing a register, check if the next token is a shift keyword and consume it as part of the operand.

x86 (Intel syntax) has its own challenge: memory operand parsing (`[rbx+rcx*4+8]`) requires expression parsing within brackets, with the scale factor being a specific syntax element rather than general arithmetic.

Both are manageable with per-architecture operand parsers — the shared expression parser handles the general case, and per-arch code handles the architecture-specific forms.

---

## 7. Bootstrapping

The assembler is written in Binate. This raises the question of which subset of Binate to target:

**Bootstrap subset**: can test via the Go interpreter (fast edit-run cycle), but no interfaces or generics. Operand types would be tagged unions (the same pattern as the self-hosted compiler's AST). Workable but less elegant.

**Full Binate**: interfaces and generics would be natural for an assembler (e.g., an `Encoder` interface, generic encoding table helpers). But requires compiling via the LLVM-backed compiler for testing, which is a slower iteration cycle.

The decision doesn't need to be made upfront — starting in the bootstrap subset and refactoring once the native backends exist (and provide a fast compile cycle) is viable. The self-hosted compiler's AST already proves the tagged-union approach works at scale.

---

## 8. Multi-Architecture Coverage

### Priority order

1. **ARM32** — highest priority for the language's 32-bit target story
2. **AArch64** — practical for development (primary dev machine is AArch64)
3. **x86-64** — important for CI, common Linux servers, broad adoption

### Testing strategy

ARM32 binaries tested via QEMU user-mode emulation (`qemu-arm`) on the development Mac. This is already planned for the ARM32 compiler backend (see `ir-backend-cleanup-plan.md`). AArch64 and x86-64 can be tested natively on appropriate hardware or via CI.

---

## 9. Object Format Output

### Mach-O implementation

Mach-O MH_OBJECT emission is implemented. The emitter writes:
- `mach_header_64` with correct CPU type/subtype
- `LC_SEGMENT_64` with per-section headers, mapping assembler section names to Mach-O conventions (`text` → `__TEXT,__text`, `data` → `__DATA,__data`, etc.)
- Section data with inter-section alignment
- Relocation entries (`relocation_info` structs) between section data and symbol table, mapping assembler fixup kinds to Mach-O ARM64 relocation types
- `LC_SYMTAB` with `nlist_64` entries and string table
- `LC_BUILD_VERSION` for macOS platform identification (suppresses linker warnings)

The section name mapping is abstract — assembler code uses `text`, `data`, `rodata`, `bss` and the emitter maps to format-specific names. This keeps assembly files portable across object formats.

### Relocation mapping

Assembler fixup kinds map to Mach-O types:
- `FIX_BRANCH26` → `ARM64_RELOC_BRANCH26`
- `FIX_ADR_LO21` / `FIX_ADRP_HI21` → `ARM64_RELOC_PAGE21`
- Generic absolute (kind 0) → `ARM64_RELOC_UNSIGNED`

The relocation entry packs symbol index, PC-relative flag, length, extern flag, and type into the `r_symbolnum`/flags word per Mach-O spec.

### ELF emission — deferred

ELF emission follows the same pattern: same core data structures, different serialization. The section naming is simpler for ELF (assembler names map directly with a `.` prefix). Deferred until Linux/CI targets are needed.

---

## 10. Fixup Resolution Without Function Pointers

### The bootstrap subset constraint

The original design used a function pointer (`*uint8`) in the `Assembler` struct for per-architecture fixup resolution. This doesn't work in the bootstrap subset — the bootstrap interpreter can't call through function pointers (only `c_call_dtor` in the C runtime supports indirect calls, and that's a special case for destructors).

**Approaches considered:**
- **Function pointer callback**: clean but doesn't work in bootstrap subset
- **Architecture enum dispatch in core**: the core switches on `Assembler.arch` and calls per-arch code. Couples the core to all architectures.
- **Per-arch ResolveFixups function**: each architecture provides a `ResolveFixups(a)` function that the user calls explicitly before `Finalize`.

**Chose per-arch ResolveFixups** — the user calls `aarch64.ResolveFixups(a)` then `asm.Finalize(a)`. The core stays architecture-independent, it works in the bootstrap subset, and the two-call pattern is explicit about what's happening. Resolved fixups are marked with `Kind = -1` so `Finalize` skips them.

### Fixup ordering

Fixups must be recorded *before* emitting the instruction, not after. `AddFixup` captures `CurrentOffset()` as the fixup location, so if it's called after `emit32`, the offset points past the instruction rather than at it. This was caught by the first branch resolution tests.

---

## 11. Bitmask Immediate Encoding

### The problem

AArch64 logical immediates (AND/ORR/EOR with immediate operands) use a compact N/immr/imms encoding that represents repeating bit patterns. Not all 64-bit values are encodable — only values consisting of a contiguous run of 1-bits within a power-of-2 sized element, tiled across the register.

### The algorithm

1. Reject all-zeros and all-ones (not encodable)
2. For 32-bit operations, replicate the 32-bit value to fill 64 bits
3. Find the smallest repeating element size by checking `ror64(value, half) == value` for decreasing sizes (64 → 32 → 16 → 8 → 4 → 2)
4. Extract one element, count set bits
5. Find the 0→1 transition point in the element ring — this is where the contiguous run of 1-bits starts
6. Verify the 1-bits are actually contiguous
7. Compute immr from the start position: `immr = (size - start) % size`
8. Compute N and imms: N=1 for 64-bit elements, N=0 with a size-encoding prefix for smaller elements. `imms = ((~(2*size-1)) & 0x3f) | (ones-1)`

### Bootstrap limitations encountered

Three issues required workarounds:

1. **4-value multiple return**: the bootstrap interpreter crashed on `func f() (bool, int, int, int)`. Solution: return a `BitmaskResult` struct instead.

2. **Large hex literals**: `0xFFFFFFFFFFFFFFFF` causes the bootstrap's integer parser to panic ("invalid integer literal"). Solution: `allOnes64()` helper that builds the value from `(0xFFFFFFFF << 32) | 0xFFFFFFFF`.

3. **Bitwise NOT operator**: `~x` may not be supported by the bootstrap. Solution: use `x ^ -1` instead.

---

## 12. Text Parser Implementation

### Design confirmed: line-oriented, two layers

The implemented parser follows the designed architecture exactly. Each line is lexed, then dispatched:
- `.` prefix → directive
- Identifier followed by `:` → label definition
- Identifier followed by `=` → constant definition
- Otherwise → instruction (dispatched to per-arch parser)

### Lexer design

A simple hand-written lexer that returns `(Lexer, Token)` pairs. The `Lexer` is a value type (position in source), so backtracking is trivial — just save the lexer state. This is used for peeking ahead (e.g., checking if an identifier is followed by `:` or `=`).

Token text is stored as `@[]char` (managed-slice) — each identifier/string is a fresh copy. This is slightly wasteful but simple and correct.

### Expression evaluation

The expression parser uses recursive descent with standard precedence levels. Expressions are evaluated immediately (no AST) — this works because all expression values are compile-time constants.

### AArch64 instruction parsing: operand ambiguity

The main parsing challenge is **shifted register operands**. In `add x0, x1, x2, lsl #3`, the commas are ambiguous — is `lsl` a fourth operand or a modifier on `x2`? The parser handles this with greedy lookahead: after parsing a register in operand position, it peeks ahead for a shift keyword. If found, it consumes the shift as part of the operand.

### Register name ambiguity

Named registers `wzr`, `xzr` start with `w`/`x`, which is the same prefix as numbered registers `w0`–`w30`, `x0`–`x30`. The initial implementation tried numbered parsing first, which failed on `wzr` (the `z` isn't a digit). Fixed by checking named registers before attempting the `x`/`w` + digits pattern.

### SP vs XZR encoding ambiguity

Register 31 means SP in some instruction contexts (ADD/SUB immediate) and XZR in others (logical register operations). The text parser doesn't need to handle this — it passes register numbers to the library API, which already handles the disambiguation (e.g., `MOV` uses ADD when SP is involved).
