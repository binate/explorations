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
until stages 5–9 link it — landed binate `4d91dc223` (2026-10-17).  Next: stage 2.

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
- ADD (32- or 64-bit, not ADDS / SUB) with `:tprel_hi12:` (implicitly `lsl #12`; an explicit one is an error),
  `:tprel_lo12:`, `:tprel_lo12_nc:`, the `:dtprel_*:` three, and `:tlsdesc_lo12:` (TLSDESC_ADD_LO12).
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

## Scope decision

All three arches (user, 2026-10-17: "All three arches (Recommended)"): stages 3 and 4 (the x86-64 and arm32
assembler operators) and the x86-64 / arm32 halves of stages 5–7 are in the series, as written above —
bnld links clang-compiled C objects on every target.
