# Binate Assembler Design Notes

## Goals

- Multi-architecture assembler for use with Binate's native compiler backends
- Library with a thin CLI wrapper — the compiler backend calls the library directly, the CLI provides a standalone tool for hand-written assembly
- Written in Binate, in a separate repo
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

## Deferred / TODO

- **Convenience directives for Binate types**: emitting `[]const char` (pointer + length pair) or `@[]const char` (with sentinel refcount + managed-slice layout) directly from assembly. Useful for static string data consumed by Binate code. v2.
- **Macros**: adds significant complexity. Binate can generate assembly programmatically via the library API. Defer unless hand-written assembly demand justifies it.
- **Conditional assembly** (`.if`, `.ifdef`): same reasoning as macros. Defer.
- **`.include`**: build system can handle file composition. Defer.
- **Text parser**: the CLI assembler needs a parser that reads `.s` files and produces the same structures as the library API. Design TBD.
- **Object format details**: exact flag syntax for `.section`, Mach-O segment/section mapping, ELF symbol types/sizes. TBD when implementing.
- **LLVM backend integration**: whether/how the existing LLVM backend interacts with the assembler is TBD.
