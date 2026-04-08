# Binate Assembler Design Notes

## Goals

- Multi-architecture assembler for use with Binate's native compiler backends
- Library with a thin CLI wrapper — the compiler backend calls the library directly, the CLI provides a standalone tool for hand-written assembly
- Written in Binate, in the binate repo (alongside the compiler and interpreter — avoids cross-repo dependency issues; can split out later once boundaries stabilize)
- Support multiple architectures: ARM32 (highest priority), AArch64 (practical for development), x86-64 (CI, common Linux)
- Pluggable object format output: ELF, Mach-O

## Integration with Binate — DECIDED

### Assembly functions (Plan 9 / Go model)

Whole functions written in assembly in `.s` files, declared in `.bni` interface files, linked with compiler output. The assembler is a separate tool that produces object files; the linker combines them with compiler-generated object files.

This covers: syscall wrappers, crypto primitives, startup code, interrupt vectors, and any case where a complete function is written in assembly.

### Compiler intrinsics for special instructions

Memory barriers, atomics, cache operations, and similar special instructions are compiler intrinsics — a closed, known set per architecture, emitted directly into the instruction stream by the compiler backend. The compiler understands their semantics (clobbers, ordering) because they are a finite, well-defined set.

Rough intrinsic surface per architecture:
- **Atomics**: load-acquire, store-release, compare-and-swap, atomic add/sub/or/and (parameterized by width and ordering)
- **Barriers**: DMB/DSB/ISB (ARM), MFENCE/LFENCE/SFENCE (x86)
- **System**: supervisor call (SVC/syscall), breakpoint, halt, cache maintenance
- **Bit manipulation**: CLZ, CTZ, popcount, byte-reverse (where hardware supports them)

Intrinsic names can be architecture-neutral where it makes sense (e.g., `atomic_cas` works on all targets, the backend picks the right instruction sequence). Architecture-specific intrinsics (cache maintenance, system registers) use qualified names.

### No inline assembly

No GCC/LLVM-style inline assembly with operand constraints. The constraint language is complex, interacts badly with register allocation, and the use cases are covered by the above two mechanisms. If you need a few special instructions, use intrinsics. If you need a whole function in assembly, write it in a `.s` file.

## Assembly Syntax — DECIDED

### Instruction syntax

**Use each architecture's vendor/reference manual syntax.** This means:
- **ARM32**: ARM UAL (Unified Assembly Language)
- **AArch64**: ARM's standard syntax
- **x86-64**: Intel syntax

For ARM32 and AArch64, this is the same as what GNU gas uses for instructions. For x86-64, this means Intel syntax (not AT&T), matching Intel/AMD reference manuals, NASM, and the wider x86 ecosystem.

GNU gas's AT&T syntax for x86 was a historical accident (AT&T conventions for a different architecture applied to x86) and was not repeated for later architectures.

### Directives, labels, comments — unified across architectures

While instruction syntax is per-architecture, everything else is standardized.

#### Architecture declaration

```
.arch aarch64
```

- Must be the first non-comment line
- Exactly one per file, mandatory
- Command-line flag, if present, must match (catches mistakes)

#### Comments

```
// line comment
```

Single style. `//` only. No `#`, `@`, `;`.

#### Sections

```
.section text
.section data
.section rodata
.section bss
.section my_custom, "rw"
```

Always use `.section`. Section names do not carry a leading dot — the assembler maps names to the correct format-specific representation (e.g., `text` becomes `.text` in ELF, `__TEXT,__text` in Mach-O). Well-known names (`text`, `data`, `rodata`, `bss`) have default flags; custom sections require explicit flags. Defaults are overrideable.

#### Symbol binding

```
.global my_func
.local my_helper
.weak my_fallback
```

#### Labels

```
my_func:                    // global label
.loop:                      // local label, scoped to preceding global label
```

Local labels use a leading dot and are scoped to the preceding non-local label (NASM-style). The assembler mangles them internally (e.g., `.loop` under `my_func` becomes `my_func.loop`). Local labels can be reused under different global labels without collision.

#### Constants

```
PAGE_SIZE = 4096
HEADER_FLAGS = (1 << 0) | (1 << 2)
my_string_len = $ - my_string
```

`name = expr` syntax. No redefinition (error to assign the same name twice).

#### Data emission

Size-explicit, matching Binate's type naming:

```
// Unsigned (reject negative literals)
.uint8  0xff
.uint16 0x1234
.uint32 0xdeadbeef, 0xcafebabe
.uint64 0x123456789abcdef0

// Signed (reject out-of-range literals)
.int8   -1
.int16  -1000
.int32  -1
.int64  -1

// Target word size
.int    -1
.uint   0xffffffff

// Floating point
.float32 3.14
.float64 2.718281828
```

Multiple comma-separated values per directive are allowed. `.int`/`.uint` emit the target's word size (4 bytes on 32-bit, 8 bytes on 64-bit). Endianness is determined by the target architecture.

#### Strings

```
.ascii "hello"              // raw bytes, no terminator
.asciz "hello"              // null-terminated
```

#### Alignment and fill

```
.align 4                    // align to 4-byte boundary, zero-fill
.align 16, 0x90             // align with custom fill byte
.zero 64                    // emit 64 zero bytes
.fill 64, 0xcc              // emit 64 bytes of 0xcc
```

`.align` always means bytes (not power-of-two). `.fill` is byte-only (no multi-byte fill values).

#### Expressions

Directives and instruction operands accept constant expressions:

- **Literals**: decimal, hex (`0x`), octal (`0o`), binary (`0b`), character (`'A'`)
- **Arithmetic**: `+`, `-`, `*`, `/`, `%` (integer)
- **Bitwise**: `&`, `|`, `^`, `~`, `<<`, `>>`
- **Unary**: `-`, `~`
- **Parentheses** for grouping
- **Label references**: a label name evaluates to its address
- **Same-section label differences**: `end - start` resolves at assembly time
- **Current position**: `$` (address of current location in section)

Label references in data directives emit relocations. Same-section label differences resolve at assembly time.

## Core Data Structures — DECIDED

```
// A section being assembled
Section:
    name        []char
    flags       uint        // read, write, execute, etc.
    data        @[]uint8    // byte buffer (grows during assembly)
    fixups      @[]Fixup    // unresolved references within this section

// A fixup — an unresolved reference to a label
Fixup:
    offset      uint        // byte offset in section's data buffer
    label       []char      // target label name
    kind        int         // architecture-specific: AARCH64_BRANCH26, X86_REL32, etc.
    addend      int         // constant to add to resolved address

// A symbol in the symbol table
Symbol:
    name        []char
    section     int         // index into section list (-1 for external/undefined)
    offset      uint        // byte offset within section
    binding     int         // LOCAL, GLOBAL, WEAK

// A relocation — a fixup that couldn't be resolved internally
// (cross-section or external), passed to the object file emitter
Relocation:
    section     int         // which section contains the reference
    offset      uint        // byte offset in that section
    symbol      int         // index into symbol table
    kind        int         // architecture-specific relocation type
    addend      int

// The assembler itself
Assembler:
    sections    @[]@Section
    symbols     @[]Symbol
    current     int         // index of current section
    arch        int         // target architecture
    word_size   int         // 4 or 8
```

Growing byte buffers for section data use a `ByteBuf` type (aliased from or copied from `CharBuf`, since `char = uint8`). This is temporary until generics and a standard library provide a generic growable buffer.

Symbol lookup is by linear scan over the symbol list. Sufficient for initial use; can be upgraded to sorted array + binary search if performance requires it.

### Fixup resolution callback

The shared core calls back into per-architecture code during `Finalize()` to patch fixup bytes. Each architecture provides a fixup resolver via function pointer:

```
// Per-architecture: given fixup kind, patch the bytes
// Returns false if the fixup can't be resolved (e.g., out of range)
ResolveFixup(data @[]uint8, fixup Fixup, target_addr uint, fixup_addr uint) bool
```

The core dispatches to the appropriate resolver based on the assembler's architecture. Using a function pointer (not interfaces) keeps this compatible with the bootstrap subset.

## Library API — DECIDED

### Architecture

Three layers:

1. **Shared core** (`pkg/asm`) — architecture-independent: section management, label/symbol table, fixup tracking, data emission, finalization (resolve fixups, report errors)

2. **Per-architecture packages** (`pkg/asm/aarch64`, `pkg/asm/arm32`, `pkg/asm/x86`) — register constants, operand types, instruction emit functions. Each emit function appends encoded bytes to the core's section buffer and records fixups for label references.

3. **Per-format packages** (`pkg/asm/elf`, `pkg/asm/macho`) — take finalized assembler state and write object files.

### Core API

```
a := asm.New()

asm.SetSection(a, "text")
asm.DefineLabel(a, "my_func")
asm.SetGlobal(a, "my_func")

// Data emission
asm.EmitUint32(a, 0xdeadbeef)
asm.EmitAsciz(a, "hello")
asm.Align(a, 4)
asm.Zero(a, 64)
asm.EmitAddr(a, "my_func")       // word-sized label reference (relocation)

asm.Finalize(a)                   // resolve internal fixups, error on unresolved
elf.Write(a, output_path)
```

### Per-architecture API

Each architecture defines its own operand type (tagged union) and instruction emit functions. Operand structs handle the variety of addressing modes without requiring a separate function per instruction form:

```
// ARM64 example
aarch64.Add(a, X0, X1, aarch64.Reg(X2))
aarch64.Add(a, X0, X1, aarch64.Imm(42))
aarch64.Add(a, X0, X1, aarch64.RegShift(X2, aarch64.LSL, 3))

aarch64.Ldr(a, X0, aarch64.MemImm(X1, 8))
aarch64.Ldr(a, X0, aarch64.MemPre(X1, 16))
aarch64.Ldr(a, X0, aarch64.MemPost(X1, 16))
aarch64.Ldr(a, X0, aarch64.Label("my_data"))

aarch64.B(a, "my_label")
aarch64.Ret(a)
```

### Fixup mechanism

When an instruction references a label (e.g., a branch target), the emit function:
1. Encodes the instruction with a placeholder offset
2. Records a fixup: `{section, offset_in_section, label_name, fixup_kind}`

Fixup kinds are architecture-specific (e.g., `AARCH64_BRANCH26`, `X86_REL32`) and tell the finalizer which bits to patch and how (scaling, PC-relative, etc.).

On `Finalize()`:
- Same-section label references are resolved by patching the encoded bytes
- Cross-section or external references become relocations in the object file

### Compiler backend usage

The compiler backend calls the per-architecture emit functions directly — no text assembly involved:

```
// Compiling an IR add
aarch64.Add(a, dst_reg, src1_reg, src2_reg)

// Compiling an IR branch
aarch64.B(a, block_label)

// Function prologue
aarch64.SubImm(a, SP, SP, frame_size)
aarch64.Stp(a, FP, LR, SP, 0)
```

## Instruction Encoding — DECIDED

**Hand-coded** with ad hoc internal helper functions to reduce repetition. No table-driven encoding framework.

ARM64 instructions are 32-bit fixed-width with regular field positions. Instruction families (data processing, loads/stores, branches, system) share encoding structure, so internal helpers handle the common bit layouts:

```
// Internal helper: emit a 32-bit instruction word
func emit32(a @asm.Assembler, inst uint32) { ... }

// Internal helper: data processing (register) — covers ADD, SUB, AND, ORR, etc.
func dpReg(sf bool, opc uint32, rm uint8, rn uint8, rd uint8) uint32 { ... }

// Internal helper: data processing (immediate)
func dpImm(sf bool, opc uint32, imm uint32, rn uint8, rd uint8) uint32 { ... }

// Public API dispatches on operand kind
func Add(a @asm.Assembler, rd uint8, rn uint8, op Operand) {
    if op.Kind == REG {
        emit32(a, dpReg(isX(rd), 0x0B000000, op.Reg, rn, rd))
    } else if op.Kind == IMM {
        emit32(a, dpImm(isX(rd), 0x11000000, cast(uint32, op.Imm), rn, rd))
    }
    // ... shifted register, extended register
}
```

This approach is simple, easy to audit per-instruction, and handles irregular encodings naturally (just write the special case). Helpers emerge organically from the encoding patterns.

## Text Parser — DECIDED

The CLI assembler reads `.s` files and produces the same sequence of operations as the library API.

### Two-layer structure

**Shared parser** — handles architecture-independent syntax:
- `.arch` declaration (selects the per-arch instruction parser)
- Directives (`.section`, `.global`, `.align`, data emission, etc.)
- Labels (global and local)
- Constants (`name = expr`)
- Expression parsing (arithmetic, labels, `$`)
- Comments

**Per-architecture instruction parser** — handles one line of instruction text:
- Parses the mnemonic (possibly with condition/size suffixes)
- Parses operands in architecture-specific syntax (registers, addressing modes, shifts)
- Calls the corresponding library emit function

The shared parser reads a line: if it starts with `.`, it's a directive; if it ends with `:`, it's a label; if it matches `name = expr`, it's a constant; otherwise, hand it to the per-arch instruction parser.

### Mnemonic dispatch

A sorted array of `(mnemonic, handler_function_pointer)` entries per architecture. The handler receives the assembler and the remaining tokens on the line, parses operands, and calls the appropriate emit function. Binary search for lookup. ARM64 has ~200-300 mnemonics; sorted search is adequate.

### Operand parsing

Per-architecture. For ARM64, operands are parsed greedily — after parsing a register, check if the next token is a shift keyword (`lsl`, `lsr`, `asr`, `ror`) and if so, consume it as part of a compound shifted-register operand. Memory operands are bracketed (`[...]`) and unambiguous.

### Error reporting

Source location (file, line, column) attached to all errors:
```
foo.s:12:5: error: expected register, got '#42'
foo.s:15:1: error: unknown mnemonic 'addd' (did you mean 'add'?)
foo.s:20:15: error: immediate value out of range for ADD (got 8192, max 4095)
```

Range checking on immediates is important — ARM64 has specific ranges for different instruction forms.

### Intentional limitations

- No preprocessor (`#include`, `#define`, `#ifdef`)
- No macros
- No multi-file input (one `.s` in, one object file out; build system handles composition)
- Line-oriented processing, no AST, no multi-pass analysis

## Implementation Status

### Shared core (`pkg/asm`) — IMPLEMENTED

Section management, symbol table, labels, fixups, data emission (uint8/16/32/64, asciz, align, zero, fill), fixup resolution with per-arch callbacks, finalization (unresolved fixups become relocations). 12 unit tests.

### AArch64 encoding (`pkg/asm/aarch64`) — IMPLEMENTED

Operand tagged union (register, immediate, shifted register, memory modes, label). Comprehensive instruction coverage:

- **Arithmetic**: ADD, SUB, ADDS, SUBS, MUL, MADD, MSUB, SDIV, UDIV, NEG
- **Logical**: AND, ORR, EOR, ANDS (register, shifted-register, and bitmask immediate forms)
- **Shift**: LSL, LSR, ASR (register and immediate), ROR (register)
- **Move**: MOV (register, SP-aware, immediate), MVN, MOVZ, MOVK, MOVN
- **Compare/test**: CMP, CMN, TST (register and immediate)
- **Conditional**: CSEL, CSINC
- **Load/Store**: LDR, STR (unsigned/pre/post/register), LDRB, STRB, LDRH, STRH, LDRSB, LDRSH, LDRSW, LDP, STP
- **Extend**: SXTB, SXTH, SXTW, UXTB, UXTH
- **Branches**: B, BL, BR, BLR, RET, B.cond, CBZ, CBNZ, TBZ, TBNZ
- **Address**: ADR, ADRP
- **System**: NOP, SVC

Bitmask immediate encoding (N/immr/imms) handles repeating bit patterns at element sizes 2–64, with rotated contiguous masks. Workarounds for bootstrap limitations (no `~` operator, no large hex literals, no 4-value multiple return).

Fixup resolver handles BRANCH26, BRANCH19, BRANCH14, and ADR for same-section references. 49 unit tests.

### ARM32 encoding (`pkg/asm/arm32`) — IMPLEMENTED

Operand tagged union (register, immediate, shifted register, register-shifted register, all memory addressing modes, label). Comprehensive instruction coverage:

- **Arithmetic**: ADD, SUB, RSB, ADC, SBC, RSC (all with optional S suffix and condition code)
- **Logical**: AND, ORR, EOR, BIC (register, shifted register, and rotated 8-bit immediate forms)
- **Move**: MOV, MVN (register and immediate), MOVW, MOVT (16-bit immediate halves)
- **Compare/test**: CMP, CMN, TST, TEQ (register and immediate)
- **Load/Store**: LDR, STR, LDRB, STRB (immediate offset, register offset, scaled register, pre/post-index), LDRH, STRH, LDRSB, LDRSH (extra load/store encoding)
- **Load/Store multiple**: LDM, STM, LDMDB, STMDB, PUSH, POP
- **Branches**: B, BL (with 24-bit PC-relative fixups), BX, BLX (register)
- **Multiply**: MUL, MLA, UMULL, SMULL, SDIV, UDIV
- **Misc**: CLZ, NOP, SVC, BKPT

Rotated 8-bit immediate encoding (16 rotation positions). Every instruction accepts a condition code parameter. Fixup resolver handles BRANCH24 for same-section references (with ARM pipeline +8 adjustment). 73 unit tests.

### Mach-O emission (`pkg/asm/macho`) — IMPLEMENTED

Emits valid Mach-O MH_OBJECT files for AArch64 (or x86-64). Includes mach_header_64, LC_SEGMENT_64 with section headers, LC_SYMTAB, LC_BUILD_VERSION, relocation entries (ARM64_RELOC_BRANCH26, ARM64_RELOC_UNSIGNED, etc.). 8 tests including end-to-end tests that assemble, link with the system linker, and run the resulting executable:

- Loop with backward branch (sum 1..9 = 45)
- Conditional branch (CBNZ)
- Function call (BL with prologue/epilogue)
- Cross-object linking (external BL relocation)
- Multiply and divide (MUL, SDIV)
- Conditional select (CSEL)

### x86-64 encoding (`pkg/asm/x64`) — IMPLEMENTED

Full x86-64 instruction encoding with variable-length CISC encoding: REX prefix generation, ModR/M byte with register/memory addressing, SIB byte for scaled index, operand size handling (8/16/32/64-bit).

- **Data movement**: MOV (reg/reg, reg/mem, mem/reg, reg/imm, mem/imm), PUSH, POP, LEA
- **Arithmetic**: ADD, SUB, AND, OR, XOR, CMP, TEST (shared ALU encoder for the standard opcode pattern), INC, DEC, NEG, NOT
- **Shift**: SHL, SHR, SAR (immediate and CL forms)
- **Multiply/divide**: IMUL (2-operand and 3-operand), IDIV, DIV, CQO, CDQ
- **Branches**: JMP, Jcc (all 16 conditions), CALL, RET, JMP/CALL register indirect
- **System**: NOP, SYSCALL, INT

Fixup resolver handles 32-bit PC-relative displacements (rel32) for same-section branches and calls. 40 unit tests.

### ELF emission (`pkg/asm/elf`) — IMPLEMENTED

Emits valid ELF relocatable object files. Supports both ELF64 (AArch64, x86-64) and ELF32 (ARM32). Handles section headers, symbol table (sorted: locals before globals, as required by ELF spec), string tables, `.rela` relocation sections. Architecture-specific relocation type mapping for AArch64 (JUMP26, CONDBR19, ADR, ADRP, TSTBR14), ARM32 (JUMP24, ABS32), and x86-64 (PC32, ABS64). 19 unit tests including ARM32 ELF32 header validation and 3 ARM32 QEMU semihosting end-to-end tests (exit code, loop, function call).

### Text parser (`pkg/asm/parse`) — IMPLEMENTED

Line-oriented text assembly parser with two-layer architecture:

**Lexer**: identifiers, integers (decimal/hex/binary/octal), strings, characters, all assembly punctuation tokens including `{`/`}` for register lists. Handles `//` comments.

**Expression parser**: full precedence — `|`, `^`, `&`, `<<`/`>>`, `+`/`-`, `*`/`/`/`%`, unary `-`/`~`. Parentheses, integer/char literals, `$` for current position.

**Directive parser**: `.arch`, `.section`, `.global`/`.local`/`.weak`, data emission (`.uint8` through `.uint64` and signed variants), `.ascii`, `.asciz`, `.align`, `.zero`, `.fill`. Labels (global and NASM-style local scoping). Constants (`name = expr`).

**AArch64 instruction parser**: register parsing (x0-x30, w0-w30, sp, xzr, wzr, fp, lr), operand parsing (#immediates, [memory] modes with pre/post-index, shifted registers), condition codes for B.cond. Full parity with the AArch64 encoding backend.

**ARM32 instruction parser**: register parsing (r0-r15, sp, lr, pc, fp, ip), operand parsing (#immediates, [memory] with all addressing modes, shifted registers, register-shifted registers), register list parsing (`{r0-r7, lr}`). Condition suffix stripping from mnemonics (`addeq` → ADD+EQ, `adds` → ADD+S, `addseq` → ADD+S+EQ). Full parity with the ARM32 encoding backend.

**x86-64 instruction parser**: register parsing (rax-r15 for 64-bit, eax-r15d for 32-bit, ax-r15w for 16-bit, al-r15b for 8-bit — register name encodes size), operand parsing (registers, immediates, labels, memory with `[base + index*scale + disp]`, size prefixes `byte`/`word`/`dword`/`qword` with optional `ptr`), Jcc mnemonic parsing (`je`/`jne`/`jg`/etc.). Full parity with the x86-64 encoding backend.

### CLI tool (`cmd/bnas`) — IMPLEMENTED

Command-line assembler: reads a `.s` file, assembles it, writes a `.o` file. Supports `-o` output path (default: input with `.s` → `.o`). 3 unit tests.

94 parser tests, 295 tests total across all assembler packages.

## Deferred / TODO

- **x86-64 end-to-end tests**: assemble → ELF64 → link → run natively on Linux (no QEMU needed). Would validate the full x86-64 pipeline on CI.
- **Convenience directives for Binate types**: emitting `[]const char` or `@[]const char` from assembly. v2.
- **Macros**: adds significant complexity. Binate can generate assembly programmatically via the library API. Defer unless hand-written assembly demand justifies it.
- **Conditional assembly** (`.if`, `.ifdef`): same reasoning as macros. Defer.
- **`.include`**: build system can handle file composition. Defer.
- **LLVM backend integration**: whether/how the existing LLVM backend interacts with the assembler is TBD.
