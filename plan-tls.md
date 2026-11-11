# Plan: thread-local storage — assembler and bnld

Item (5) of `plan-aa64-asm-symbols.md`'s remaining work ("TLS, staged"; scope, user 2026-10-09: "All,
staged" — TLS sections and symbols plus every operator in the assembler and both writers, verified
against clang, then bnld: PT_TLS / thread-pointer layout and LE / IE / TLSDESC resolution in static and
dynamic ELF links, and Mach-O TLV).  Nothing exists today: the assembler rejects the `.tdata` / `.tbss`
names (`asm.SetSection`), `%tls_object` and every TLS operator; bnld reads `.tdata` as plain data,
`.tbss` as plain bss and an STT_TLS symbol as a plain address (claude-todo: "bnld: an input symbol's ELF
type is ignored"), and rejects every TLS relocation as unsupported, so a C object using `__thread`
cannot be linked by bnld on any target.

## What clang emits (clang 21, 2026-10-17; `__thread` C, -O1)

- ELF sections: `.section .tdata,"awT",@progbits` and `.tbss,"awT",@nobits` (SHF_TLS).  Every symbol
  in a TLS section is STT_TLS, `.type x,@object` included (the mapping symbols too), and an undefined
  symbol a TLS operator refers to is STT_TLS.
- aarch64: non-PIC LE `add :tprel_hi12:` / `:tprel_lo12_nc:`, IE `adrp :gottprel:` /
  `ldr :gottprel_lo12:` for an extern; PIC TLSDESC `adrp :tlsdesc:` / `ldr :tlsdesc_lo12:` /
  `add :tlsdesc_lo12:` / `.tlsdesccall` / `blr`.  Plus the operators listed in `plan-aa64-asm-symbols.md`
  3d (`:tprel_g2:` …, `:dtprel_*:`, `:gottprel_g1:` / `_g0_nc:`, `:tlsdesc_auth*:`).
- x86-64: LE `%fs:x@TPOFF`, IE `x@GOTTPOFF(%rip)`; PIC GD `leaq x@TLSGD(%rip), %rdi` /
  `callq __tls_get_addr@PLT`; LD `@TLSLD` / `@DTPOFF`; `-mtls-dialect=gnu2` TLSDESC `@tlsdesc` /
  `call *x@tlscall(%rax)`.
- arm32: TP from `mrc p15, 0, rN, c13, c0, 3`; LE `.long x(TPOFF)`, IE `.long x(GOTTPOFF)-(…)`, PIC
  GD `.long x(TLSGD)-(…)` + `bl __tls_get_addr`; LD `(TLSLDM)` / `(TLSLDO)`; TLSDESC `(TLSDESC)` /
  `(TLSCALL)` / `.tlsdescseq` (clang rejects `-mtls-dialect` for arm32; the assembler forms exist).
- Mach-O (arm64 and x86-64): `__DATA,__thread_data,thread_local_regular` holds `x$tlv$init`;
  `.tbss x$tlv$init, size, align` (zerofill); `__DATA,__thread_vars,thread_local_variables` holds each
  variable's descriptor (`.quad __tlv_bootstrap`, `.quad 0`, `.quad x$tlv$init`); code loads the
  descriptor with `adrp x0, _x@TLVPPAGE` / `ldr x0, [x0, _x@TLVPPAGEOFF]` (x86-64 `movq _x@TLVP(%rip)`)
  and calls through its first word.

## Stages (each a reviewed, landed commit or two)

Progress: stage 1 — landed binate `08e9b84b1` (2026-10-17).  bnld rejects thread-local storage in an input
until stages 5–9 link it — landed binate `4d91dc223` (2026-10-17).  Stage 2a — landed binate `54d587043`
(2026-10-17); 2b — landed binate `7df7a13b2` (2026-10-18; deliberate rejects beyond the design: `.tlsdesccall`
must be followed by `blr` / a `blra*`, and no addend on a slot or descriptor reference); 2c — landed binate
`0b78c67a8` (2026-10-18; `@TLVPPAGEOFF` on `ldr Xt` only, which is all ld64 links there).  Stage 3 design below (2026-11-09); 3a — landed binate `ed7d60e28` (2026-11-09); 3b — landed binate `d7a1706fc` (2026-11-09; also rejected: a
call or jump through a thread-local slot, and a datum's addend mixing + or - with | ^ & << >>).  The
alias-addend MAJOR the 3b review found — fixed, binate `007aabfb8`; 3c — landed binate `858380bd5` (2026-11-10; `@TLVP` on
`mov r64` only: a `lea` links into a crash, see below).  Stage 3d design below (2026-11-10); 3d1 — landed binate `1685462f9` (2026-11-10; also: a number
after `offset` is that immediate, as clang takes a constant's name there).  Next: 3d2.

1. **Assembler: thread-local sections and symbols** (all arches, both formats).  ELF: SHF_TLS (the `T`
   flag; `.tdata*` / `.tbss*` imply it, `.tbss*` NOBITS), `%tls_object` / `@tls_object` → STT_TLS, a symbol
   defined in a TLS section STT_TLS whatever its `.type`.  Mach-O: the `__thread_data` /
   `__thread_bss` / `__thread_vars` section types (`thread_local_regular` / `_zerofill` / `_variables`)
   and the `.tbss` directive.  Golden-checked against clang.
2. **Assembler: aa64 TLS operators** — the 3d list (LE, LD `:dtprel_*:`, IE, TLSDESC + `.tlsdesccall`,
   `:tlsdesc_auth:` / `_lo12:`), each operand form clang accepts, ELF relocations; Mach-O `@TLVPPAGE` /
   `@TLVPPAGEOFF`.  An undefined symbol a TLS operator refers to becomes STT_TLS.
3. **Assembler: x86-64 TLS operators** — `@TPOFF`, `@GOTTPOFF`, `@TLSGD`, `@TLSLD`, `@DTPOFF`,
   `@tlsdesc`, `@tlscall` (ELF), `@TLVP` (Mach-O).
4. **Assembler: arm32 TLS operators** — `(TPOFF)`, `(GOTTPOFF)`, `(TLSGD)`, `(TLSLDM)`, `(TLSLDO)`,
   `(TLSDESC)`, `(TLSCALL)`, `.tlsdescseq`.
5. **bnld ELF: the TLS segment and local-exec**, all three arches, static and dynamic links: merge
   `.tdata*` then `.tbss*` into one TLS template (PT_TLS; `.tbss` takes no address space in the RW
   segment), an STT_TLS symbol's value its offset in the template, and the LE relocations (aarch64
   TLSLE_*, x86-64 TPOFF32 / TPOFF64, arm32 TLS_LE32) resolved to thread-pointer offsets by each ABI's
   layout (variant I on aarch64 — TCB 16 — and arm32 — TCB 8; variant II on x86-64).  An STT_TLS symbol
   under a non-TLS relocation, or a TLS relocation against a non-TLS symbol, is an error.
6. **bnld: initial-exec** — a GOT slot per TLS symbol holding its TP offset (a constant for a definition
   in the executable; R_AARCH64_TLS_TPREL64 / R_X86_64_TPOFF64 / R_ARM_TLS_TPOFF32 for an import from a
   shared library, e.g. glibc's), and the IE relocations; relaxed to LE where lld relaxes.
7. **bnld: general / local-dynamic and TLSDESC in an executable** — relaxed to IE / LE as lld does per
   arch (x86-64 GD / LD code rewrites, aarch64 TLSDESC; on arm32, lld's handling, checked first).
8. **bnld scripted layouts**: a builder call (and script item) placing `.tdata` / `.tbss` with PT_TLS,
   for bare-metal images whose runtime sets the thread pointer.
9. **bnld Mach-O TLV**: `__thread_vars` / `__thread_data` / `__thread_bss` in the dynamic Mach-O writer
   (the S_THREAD_LOCAL_* section types, `__tlv_bootstrap` bound from libSystem), and the TLVP
   relocations (ARM64_RELOC_TLVP_LOAD_PAGE21 / PAGEOFF12, X86_64_RELOC_TLV).
10. **e2e**: C `__thread` (LE, IE, an import of glibc's TLS, PIC objects) linked by bnld and run on
    x86-64 / aarch64 / arm32 Linux (docker) and macOS; checked against the clang + lld build.

## Stage 2 design: the aa64 TLS operators (surveyed 2026-10-17, clang 21)

Commits: (2a) local-exec `:tprel_*:` and local-dynamic `:dtprel_*:`; (2b) initial-exec `:gottprel*:`,
TLSDESC `:tlsdesc*:` / `:tlsdesc_auth*:` and `.tlsdesccall`; (2c) Mach-O `@TLVPPAGE` / `@TLVPPAGEOFF`.

What clang accepts (each its own fixup kind; never resolved at assembly time):
- MOVZ / MOVN / MOVK / `mov` with `:tprel_g2:` `:tprel_g1:` `:tprel_g1_nc:` `:tprel_g0:` `:tprel_g0_nc:`, the
  same five `:dtprel_*:`, and `:gottprel_g1:` / `:gottprel_g0_nc:` (TLSLE / TLSLD / TLSIE_MOVW_*).  A MOVZ (and
  `mov`, at shift 0) with a checked (non-`_nc`) one is encoded as MOVN — the linker picks the opcode by the
  value's sign; a MOVK or a `_nc` keeps its opcode.  G2 on a W register is an error.  An addend is allowed.
- ADD (32- or 64-bit, not ADDS / SUB) with `:tprel_hi12:` (its `lsl #12` form), `:tprel_lo12:`, `:tprel_lo12_nc:`,
  the `:dtprel_*:` three, and `:tlsdesc_lo12:` (TLSDESC_ADD_LO12); written with '#', an optional `lsl #0|#12` as
  after `:lo12:` (`#:tprel_hi12:sym, lsl #12`, GCC's spelling; a HI12 is shifted either way).
- Every scaled-offset load / store, PRFM and the sign-extending loads, with `:tprel_lo12:` / `_nc` and
  `:dtprel_lo12:` / `_nc` — LDST8/16/32/64/128 by the access size; not LDUR, writeback or pairs.
- `:gottprel_lo12:` on an 8-byte transfer (LD64_GOTTPREL_LO12_NC; STR too, as `:got_lo12:`), `:tlsdesc_lo12:` on
  the 64-bit LDR (TLSDESC_LD64_LO12); ADRP `:gottprel:` / `:tlsdesc:`; the literal `ldr Rt, :gottprel:sym` in
  every form `:got:` takes (TLSIE_LD_GOTTPREL_PREL19); `.tlsdesccall sym` (TLSDESC_CALL on the next
  instruction); the PAuth `:tlsdesc_auth:` (ADRP) / `:tlsdesc_auth_lo12:` (ADD, 64-bit LDR).
- A symbol a TLS operator names is STT_TLS (clang's fixSymbolsInTLSFixups), and its relocation keeps it.

Deliberate rejects (clang accepts): a number after any TLS operator (clang folds the lo12 forms, `add x0, x0,
:tprel_lo12:5`, and errors on the MOVW ones); a TLS operator naming a symbol defined in a section that is not
thread-local (clang retypes the `.data` label STT_TLS — nonsense either way); `ldr Rt, :tlsdesc:sym` (clang
emits a plain R_AARCH64_LD_PREL_LO19, dropping the descriptor); any TLS operator on Mach-O (ELF-only, as the
MOVW group).

## Stage 3 design: the x86-64 TLS operators (surveyed 2026-11-09, clang 22, Intel syntax)

Commits: (3a) on ELF, the operators in a memory operand, segment overrides, and the `data16` / `rex64` /
segment prefix instructions; (3b) `@tlscall`, with the CALL / JMP through memory it needs, and the operators in
data directives; (3c) Mach-O `@TLVP`; (3d) the operators in the positions the x86-64 parser reads for no symbol
yet — an immediate (`offset x@TPOFF`), GCC's `x@gottpoff[rip]` (`disp[base]`) and a bare `x@TPOFF` operand with
no segment override — built with the plain-symbol forms of those positions (x64 parser coverage, the item after
this stage).

What clang accepts (each its own fixup kind, never resolved at assembly time; operators case-insensitive):
- `@TPOFF` / `@DTPOFF` (R_X86_64_TPOFF32 / DTPOFF32, absolute, the addend kept) as a memory operand's
  displacement, with or without base and index (`[rax + x@TPOFF + 8]`, `[rbx + rcx*4 + x@DTPOFF]`, `[x@TPOFF]`
  the absolute [disp32]), always a disp32 (mod=10, or SIB 0x25), in any instruction taking memory.
- `[rip + x@GOTTPOFF]` / `@TLSGD` / `@TLSLD` / `@tlsdesc` (R_X86_64_GOTTPOFF / TLSGD / TLSLD /
  GOTPC32_TLSDESC, PC-relative: addend -4, less any immediate after the field), in any instruction taking memory.
- `call qword ptr [rax + x@tlscall]` (ff 10; R_X86_64_TLSDESC_CALL at the instruction's first byte, addend 0).
- A segment override `es: cs: ss: ds: fs: gs:` before a memory operand (after its size), bracketed or a bare
  displacement (`qword ptr fs:0`, `fs:x@TPOFF`, GCC's spelling), its prefix (26 2E 36 3E 64 65) emitted first,
  before 66 and REX; and `data16` (66), `rex64` (48) and the six segment names as instructions of their own,
  alone or before another on the line (`data16 data16 rex64 call __tls_get_addr@PLT`), emitted as written.
- Data: `.uint32` / `.int32` `x@TPOFF` / `x@DTPOFF` (TPOFF32 / DTPOFF32), `.uint64` / `.int64` (TPOFF64 /
  DTPOFF64), with an addend.
- `offset x@TPOFF` / `@DTPOFF` as an imm32 (TPOFF32 / DTPOFF32): mov r32 (B8+r), mov r64 (C7 /0), the ALU
  group's 81 /n, push (68), imul r, r/m, imm32 (69) — never an imm8 form.
- Mach-O: `[rip + _x@TLVP]` (X86_64_RELOC_TLV, pcrel, 4 bytes, extern; the field 0) — ld64 links it on
  `mov r64` and rejects `add` ("GOT load fixup does not point to a movq instruction"); on `mov r32` or with an
  addend it links and the program crashes.  (Corrected 2026-11-10 by the 3c review: `lea r64` links too, but for a
  variable a dylib defines ld64 points it at a slot holding the descriptor's address and the call through it
  crashes; ld_classic refuses it.  The survey had tried only a variable in the same image.)
- ELF: a symbol a TLS operator names is STT_TLS (elfTlsRelocsOk for EM_X86_64), with aarch64's checks (defined in
  a thread-local section, not absolute).

Deliberate rejects (clang accepts them, assembling something else or nothing a link can use):
- A slot or descriptor operator (`@GOTTPOFF`, `@TLSGD`, `@TLSLD`, `@tlsdesc`) anywhere but `[rip + sym@OP]`:
  `[rbx + x@TLSGD]`, `fs:[x@GOTTPOFF]`, `offset x@GOTTPOFF`, `.uint32 x@TLSGD` (clang relocates them all); with an
  addend (clang drops `+ 4` after `@GOTTPOFF` on a mov and keeps it after `@TLSGD` — a slot plus an addend is
  nothing, the stage 2 precedent); with a segment override (the load would read fs.base plus the slot's address).
- `@TPOFF` / `@DTPOFF` in `[rip + …]` (clang: the absolute relocation with addend -4).
- A subtracted TLS symbol: `[rax - x@TPOFF]`, `[rip - x@GOTTPOFF]` (clang adds it).
- `@tlscall` anywhere but `call qword ptr [r64 + sym@tlscall]` (no index, segment, displacement or addend): clang
  takes it on mov / lea / jmp / rip, with an addend, as `call x@tlscall` and in `.uint64`.
- A TLS operator in a 1- or 2-byte datum (clang relocates `.uint16 x@TPOFF` with TPOFF32) or in a difference
  (clang drops the `- .` of `.uint32 x@TPOFF - .`).
- `@TLVP` anywhere but `mov r64, qword ptr [rip + _x@TLVP]` with no addend or segment override (`lea` included); every ELF operator on Mach-O (clang: "unsupported symbol modifier in relocation") and `@TLVP`
  on ELF.
- A numeric local label (`1b@TPOFF`; clang: "unknown token in expression").

## Stage 3d design: symbols in immediates and the other memory spellings (surveyed 2026-11-10, clang 22)

Commits: (3d1) a symbol as an immediate, `offset sym` (and `@TPOFF` / `@DTPOFF`), with `movabs`; (3d2) a symbol as
a memory operand's absolute displacement (`[x]`, `[rbx + x]`, a bare `x`) and the `disp[base]` spelling (`8[rbx]`,
`x@TPOFF[rax]`, `x@gottpoff[rip]`, `x[rip]`).  Both build the plain-symbol forms with the thread-local ones; the
rest of the x64 parser coverage follows.

3d1, what clang takes on ELF: `offset sym [± n]` as an instruction's immediate, always the full-width field (never
an imm8 short form) — R_X86_64_32S in a 64-bit operation's sign-extended imm32 (`mov rax`, the ALU group, `test`,
`push`, `imul r64, r/m, imm`, `mov qword ptr [m]`), R_X86_64_32 / 16 / 8 in a 32- / 16- / 8-bit one, R_X86_64_64 in
`movabs r64, offset sym` (REX.W B8+r io); `offset sym@TPOFF` / `@DTPOFF` in the imm32 of a 32- or 64-bit operation
(TPOFF32 / DTPOFF32) and in `movabs` (TPOFF64 / DTPOFF64).  On Mach-O, UNSIGNED of the field's size (`mov eax`,
`movabs`, `mov ax`); the sign-extended imm32 is an error (clang: "32-bit absolute addressing is not supported in
64-bit mode").  Library: an OP_IMM with a symbol (x64.ImmSym), its field relocated by the absolute data kinds
(asm.FIX_DATA_ABS8 / 16 / 32 / 64) or, for the sign-extended imm32, a new x64.FIX_ABS32S (ELF R_X86_64_32S; the
Mach-O writer's error); x64.MovAbs.  Deliberate rejects (clang takes them): a slot or descriptor operator or `@PLT`
as an immediate (`offset x@GOTTPOFF` is R_X86_64_GOTTPOFF on an imm32); `@TPOFF` / `@DTPOFF` in an 8- or 16-bit
field (clang relocates `mov ax, offset x@TPOFF` with TPOFF32); a symbol as a shift count, an INT vector or RET's
stack release (R_X86_64_8 / 16 — no program means a symbol's address there).  clang errors on `offset` before a
number or `.`.  bnas emits `test eax, offset x` as F7 C0 where clang has A9 (no accumulator short forms yet).

3d2, what clang takes (surveyed again 2026-11-10 for the design): a memory operand is an optional displacement
written before any brackets — a symbol plus numbers (one expression, `x + 8`, `8 + x`, `N + x`, `(x)`), the symbol
optionally with an operator and an addend after it (`x@TPOFF + 8`) — then any number of bracket groups, all summed
(`8[rbx]`, `x[rbx + 8]`, `x + 8[rbx]`, `N[rbx]`, `x[8]`, `[rbx][rcx*4]`, `x[rbx][8]`, `[x][rbx]`); a group's terms
are today's plus one symbol term (`[x]`, `[x + 8]`, `[rbx + x]`, `[x + rcx*4]`, `[(x + 8)]`) and `rip` anywhere
(`x[rip]`, `x[rip + 8]`, `[x + rip]`, `[rip][x]`, `[x@GOTPCREL + rip]`).  With no bracket group the operand is
memory when it has a symbol (`mov rax, x`, `push x`, `lea rax, x`, `qword ptr x + 8`, `fs:x`, a bare `x@TPOFF`:
the absolute [disp32]) and otherwise an immediate as today (`N`, `N + 4`).  A plain symbol is R_X86_64_32S on the
forced disp32 (x64.MemSym with FIX_ABS32S; on Mach-O the writer's error, as clang's "32-bit absolute addressing is
not supported in 64-bit mode"); beside `rip` it is `[rip + x]` (PC32 / SIGNED); `@TPOFF` / `@DTPOFF` / `@tlscall` and
the RIP operators as today, wherever written (`x@TPOFF[rax]`, `fs:x@TPOFF[rbx]`, `x@gottpoff[rip]`,
`x@tlsgd[rip]`, `x@tlsdesc[rip]`, `x@GOTPCREL[rip]`, `call x@tlscall[rax]`).  JMP / CALL take memory in the
`disp[...]` spelling (`call x[rbx]`, `jmp 8[rbx]`, `call x[rip]`); a bare symbol plus numbers stays a direct
target.  Deliberate rejects (clang misreads them): a subtracted or negated symbol (`[rbx - x]`, `-x[rbx]`, `8 - x`:
clang adds x), a scaled symbol (`[x*2]`, `[rbx + x*1]`: clang drops the scale), `rip` beside a base or index
(`[rip + rbx]`, `x[rip][rbx]`: clang drops rip), a slot, descriptor or `@PLT` operator outside `[rip + ...]`
(`x@GOTPCREL`, `x@gottpoff[rbx]`, `x@PLT[rbx]`: relocated as nonsense), a number in another group beside a slot
(`x@GOTPCREL[rip][8]`: the addend rule), a register in the displacement before or without brackets (`x@TPOFF +
rbx`: clang drops it), and a size with a bare number (`qword ptr 8`: clang an immediate).  clang rejects `[rbx]8`,
`[rbx]+8`, `x[rbx]@TPOFF` and two symbols.  Left for x64 parser coverage: `movabs`'s moffs forms (`movabs rax, [x]`,
A1 with R_X86_64_64), and CALL / JMP through `[rip + x@GOTPCREL]` (GCC's `-fno-plt` call; clang GOTPCRELX /
X86_64_RELOC_GOT; the library takes no GOT operand there).

## Scope decision

All three arches (user, 2026-10-17: "All three arches (Recommended)"): stages 3 and 4 (the x86-64 and arm32
assembler operators) and the x86-64 / arm32 halves of stages 5–7 are in the series, as written above —
bnld links clang-compiled C objects on every target.
