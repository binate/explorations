# Plan: aa64 text assembler — symbols and data (item 3 of the completeness list)

Tracked by the `claude-todo.md` entry "aa64 text assembler: clang-valid instruction families still
rejected (completeness)".  Reference: Apple clang 21 (`-target arm64-apple-macos11` for Mach-O,
`-target aarch64-linux-gnu` for ELF).  One reviewed commit per piece, in this order.

The assembler's directive set is its own dialect (`.section text`, `.uint64`, `.global_c`; data
directives take numeric expressions only), so this item is about names, constants, literal pools
and relocation operators in instruction operands — plus the two directives literal pools need.

## 3a. `.`-leading names and numeric local labels — ✅ landed `477048003` (2026-09-30)

clang: any name may start with `.` (`.Lfoo:`, `.foo:`, `b .Lfoo`, `adr x0, .Lfoo+4`); a statement
starting `.name` is a directive unless `:` (label) or `=` follows.  `N:` (decimal) defines an
instance of numeric label N; `Nb` / `Nf` refer to the nearest instance before / after (`b 1b`,
`cbz x0, 2f`, `b.eq 1b`, `.quad 1b`).  Temporary (not in the symbol table) labels differ by
format: Mach-O `L…` (`.L…` is an ordinary local symbol there, and starts an atom); ELF `.L…`
(`L…` is an ordinary local); numeric-label instances on both.  ELF relocations against a
temporary go against the section symbol plus its offset (`R_AARCH64_ABS64 .text+0x28`).

Today: `.` always lexes alone, so `.Lfoo` is a directive and fails; `1:` / `1b` / `1f` fail;
`resolveLabel`'s NASM-style `.name` scoping (prefixing the last global label) is dead code
(never reached) and wrong for clang (`.foo` is a plain name) — delete it.  The ELF writer lists
every local label and emits no section symbols.

Design: the lexer makes `.` followed by a letter, `_`, `$` or `.` start a name; parseLine sends
a `.`-name to the directive parser unless `:` / `=` follows.  A numeric definition `N:` names
its instance uniquely (a name no source text can spell, temporary on both formats); `Nb` / `Nf`
lex as one directional-label token that the operand scanners resolve at their line (backward:
the current instance, which must exist; forward: the next one, which must be defined by the end).
Which labels are temporary becomes format-dependent (decided at write time, since the format is
set after parsing): Mach-O `L…` (unchanged), ELF `.L…`, numeric instances on both; the ELF writer
omits temporaries and relocates against new section symbols.

## 3b. `name = expr` constants

clang: `N = 5` may be redefined (`N = 6`; each use sees the value at that point); a constant is
usable in every immediate, offset, data value and later definition, with or without `#` where the
instruction takes an immediate (`add x6, x7, N`); constants are listed as local absolute symbols.
Forward references: clang resolves a few at layout time (`mov x0, #M`, a scaled `ldr` offset
`[x1, #M*8]`, an EXT index — which it encodes as #0) and rejects most (`add #M`, `lsl #M`,
`ubfx`, `movk`, `cmp`, `and`, `tbz`, `svc`, unscaled `ldr` offsets); with several later
definitions `mov x0, #M` takes the *first*.  Symbol-valued definitions: `A = lbl` / `B = lbl+8`
(aliases: `b B` branches to lbl+8), `C = .` (the current location), `D = lbl2 - lbl` (a label
difference, evaluated at layout).

Today: definitions are stored (redefinition is an error) but nothing reads them.

Design: definitions before use, redefinable, numeric expressions only (plus aliases, see the
decisions); ParseExpr resolves a name through the constant table; the aarch64 operand scanner
turns a bare constant name into an immediate.  Whether constants go in the symbol table as
absolute symbols (clang lists them) — yes, as local absolute symbols, if the writers can emit an
absolute symbol cheaply; otherwise its own follow-up.

## 3c. Literal pools

clang: `ldr Xt|Wt, =expr`.  A constant that one MOVZ can build (a single 16-bit chunk at any
shift of the register's width) becomes that MOVZ (`ldr x0, =0x1234` is `mov x0, #0x1234`;
`=-1` is not MOVN'd — it goes to the pool).  Otherwise an entry of the register's size (8 / 4
bytes), deduplicated by (value, size), each aligned to its size, and an LDR (literal) to it.  A
symbol (± addend) is an absolute-address entry (Mach-O `ARM64_RELOC_UNSIGNED`, ELF
`R_AARCH64_ABS64`).  The pool is emitted at `.ltorg` / `.pool` and at the end of each section.

Design: a per-section pending pool in the parser; LDR (literal) fixups to pool-entry labels
(temporary names); flush at `.ltorg` / `.pool` (new directives) and at end of file per section.
The pool-distance limit (±1 MB) is the existing LD_PREL_LO19 fixup range check.

## 3d. Relocation operators

ELF: the MOVW group — `:abs_g0:` … `:abs_g3:`, `_nc`, `_s` (MOVZ / MOVN / MOVK), `:prel_g0:` …
`:prel_g3:` (+`_nc`); TLS — `:tprel_g2:` … `:tprel_lo12_nc:`, `:dtprel_*:`, `:gottprel:` /
`:gottprel_lo12:` / `:gottprel_g1:` / `:gottprel_g0_nc:`, `:tlsdesc:` / `:tlsdesc_lo12:` and the
`.tlsdesccall` directive; plus whatever else clang 21 accepts (`:pg_hi21_nc:`, `:gotpage_lo15:`,
…) — enumerate from clang at the start of the piece.  Mach-O: `@TLVPPAGE` / `@TLVPPAGEOFF`
(deliberately rejected today only because there is no fixup).  New isa fixup kinds, the ELF /
Mach-O relocation mappings, and the resolver (none of these resolve at assembly time).  Likely
two commits: MOVW, then TLS.

## Decisions (user, 2026-09-30 — each the recommended option)

1. ELF temporaries: match clang — omit `.L…` and numeric-label instances from the ELF symbol table
   and relocate against section symbols.
2. Forward references to a constant: reject all; the `mov` / scaled-`ldr` forms clang accepts go
   on the deliberate-reject list.
3. Symbol-valued constants: aliases (`A = sym ± k`) and `C = .` in 3b; label differences (in
   definitions, immediates and data directives) are their own family on the todo list.
4. `.ltorg` / `.pool` are added as directives.
