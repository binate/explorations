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

Progress: numeric constants — redefinable, read in every expression on every arch, bare
constants as AArch64 / x86-64 immediates and offsets, forward references rejected — ✅ landed
`1a31e768f` (2026-10-01).  Deliberate rejects (clang accepts): a bare constant named like a
condition or shift / extend (read as that name: `add x0, x1, eq`, `b eq`, `ldr x0, [x1], lsl`),
a constant after a relocation qualifier (`:lo12:N`, `:got:N`), an x86-64 constant named like a
register clang knows (`ss`, `ah`).  Symbol-valued expressions — `sym ± k` with multi-term
addends on AArch64 / arm32, aliases `A = sym ± k`, `C = .` — ✅ landed `f9acb7bb6` (2026-10-01;
deliberate rejects: a label taking an alias's name, a difference of two locations).  The x86-64
label addends — ✅ landed `331b13ee4` (2026-10-01).  Constants and aliases in the symbol table
(a number as a local absolute symbol, an alias as a local symbol — an alt entry on Mach-O unless
it is exactly an atom-starting symbol) — ✅ landed `63c3ba948` (2026-10-01).  Global / weak
number constants (listed as global / weak absolute symbols; bnld reads absolute symbols as
definitions) — ✅ landed `bf00c139e` (2026-10-02).  Global / weak aliases (each use relocated
against the alias; a weak one always an alt entry on Mach-O) — ✅ landed `da8befa0a` (2026-10-02);
deliberate rejects: a `.global` / `.weak` after a use of the alias's current definition, a
redefinition after a use, an alias of a symbol not defined here, and on Mach-O a weak alias at a
section's start where no symbol starts an atom.  3b is complete.

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

Progress: the 4-byte absolute data word a W-register symbol entry needs (aarch64 FIX_ABS32: ELF
R_AARCH64_ABS32, Mach-O 4-byte UNSIGNED; bnld patches and reads it) — ✅ landed `2a26ec4d9`
(2026-10-03).  The pools — ✅ landed `14b65cb8b` (2026-10-03); deliberate rejects (clang accepts):
an FP / SIMD destination, sharing an emitted entry beyond a load's reach, sharing across a Mach-O
atom (and a pool in a later atom than its load, as any cross-atom literal load).  3c is complete.

## 3d. Relocation operators

ELF: the MOVW group — `:abs_g0:` … `:abs_g3:`, `_nc`, `_s` (MOVZ / MOVN / MOVK), `:prel_g0:` …
`:prel_g3:` (+`_nc`); TLS — `:tprel_g2:` … `:tprel_lo12_nc:`, `:dtprel_*:`, `:gottprel:` /
`:gottprel_lo12:` / `:gottprel_g1:` / `:gottprel_g0_nc:`, `:tlsdesc:` / `:tlsdesc_lo12:` and the
`.tlsdesccall` directive; plus whatever else clang 21 accepts (`:pg_hi21_nc:`, `:gotpage_lo15:`,
…) — enumerate from clang at the start of the piece.  Mach-O: `@TLVPPAGE` / `@TLVPPAGEOFF`
(deliberately rejected today only because there is no fixup).  New isa fixup kinds, the ELF /
Mach-O relocation mappings, and the resolver (none of these resolve at assembly time).  Likely
two commits: MOVW, then TLS.

Progress: the MOVW group — ✅ landed `7010879cb` (2026-10-04), with bnld patching all 17 relocations.
Deliberate rejects (clang accepts): a number after the operator (clang folds `#:abs_g0:5`), the operator on
a branch, a literal load, EXT and the fixed-point conversions (clang silently drops it there).  Open for the
user (raised 2026-10-04, landed with the first option of each): (a) fold a number after a MOVW operator, as
clang does, instead of rejecting it — the usual way to build a 64-bit constant chunk by chunk; (b) `mov Rd,
#:abs_gN:sym` is clang's MOVZ at shift 0 for every chunk and width, or reject G1–G3 on `mov`; (c) bnld writes
PREL `_NC` on a MOVZ / MOVN as bits only (AAELF64's text) where lld applies the sign switch, and takes
PREL_G3's sign where lld always makes it MOVZ.

The rest, enumerated from clang 21 (2026-10-04; ELF): `:pg_hi21_nc:` (ADRP, ADR_PREL_PG_HI21_NC),
`:gotpage_lo15:` (64-bit LDR, LD64_GOTPAGE_LO15), GOT literal loads (`ldr x0, :got:sym` /
`:got_lo12:` / `:gotpage_lo15:`, GOT_LD_PREL19; `:gottprel:`, TLSIE_LD_GOTTPREL_PREL19); TLS LE
`:tprel_g2:` … `:tprel_g0_nc:` (MOV*), `:tprel_hi12:` / `:tprel_lo12:` / `:tprel_lo12_nc:` (ADD, and
the lo12 forms on every LDR/STR size); TLS LD `:dtprel_*:` likewise; TLS IE `:gottprel:` (ADRP),
`:gottprel_lo12:` (64-bit LDR), `:gottprel_g1:` / `:gottprel_g0_nc:` (MOV*); TLSDESC `:tlsdesc:` (ADRP),
`:tlsdesc_lo12:` (ADD, 64-bit LDR) and `.tlsdesccall`; the PAuth ABI `:got_auth:` (ADRP, ADR, literal
LDR), `:got_auth_lo12:` (ADD, LDR), `:tlsdesc_auth:` / `:tlsdesc_auth_lo12:`.  clang encodes a MOVZ
with a non-`_nc` TLS G operator as a MOVN (the linker sets the opcode).  No `:tlsgd*:` / `:tlsld*:`
(clang has none).  None of the TLS relocations works without TLS symbols and sections, which neither
the assembler (no SHF_TLS `T` flag, no STT_TLS / `%tls_object`) nor bnld (no PT_TLS, no TP-relative
layout, no GOT in a static link) has; Mach-O TLV (`@TLVPPAGE` / `@TLVPPAGEOFF`, `__thread_vars` /
`__thread_data` / `__thread_bss`) likewise.  Scope to be decided with the user.

Order (2026-10-04): (1) `:pg_hi21_nc:` — assembler, ELF writer, bnld (unchecked ADR_PREL_PG_HI21) —
✅ landed `42f0697f2` (2026-10-04).
(2a) The GOT forms in the assembler — ✅ landed `f38f50223` (2026-10-04); an addend on a GOT reference
stays rejected (an existing deliberate reject — "Mach-O cannot represent" — accepting it on ELF is open
for the user).  (2b) bnld's GOT — ✅ landed `c645a568e` (2026-10-05; user: "1 and 2: go with your recs; 3:
all 4"): all four drivers (static ELF, dynamic ELF, dynamic Mach-O with rebased slots, the scripted
builder via a script-placed `.got`); the MAJOR import-addend bug (claude-todo) fixed inside it, with a
GLOB_DAT per (import, addend).
(2) The GOT family: `:gotpage_lo15:` (64-bit LDR / STR), GOT literal loads (`ldr Xt|Wt|St|Dt|Qt,
:got:sym`, LDRSW, PRFM → GOT_LD_PREL19, as clang), an addend on any GOT reference (clang accepts
`:got:sym+8`, `:got_lo12:sym+8` on ELF; we reject today), STR through `:got_lo12:` (clang accepts).
Deliberate: a GOT literal load to a local is always a relocation (clang resolves `ldr x0, :got:f` into a
load of f's bytes — a miscompile); `:got_lo12:` / `:gotpage_lo15:` on a literal load rejected (clang
reads them as `:got:`).  bnld: a real GOT in a static link — one slot per (definition, addend), filled
with S + A after layout, `_GLOBAL_OFFSET_TABLE_` at its start — and in a dynamic link the same slots in
the one `.got` beside the import slots (so `:gotpage_lo15:` has one GOT page); a symbol any of whose GOT
references cannot be relaxed (a literal load, `:gotpage_lo15:`, a STR through `:got_lo12:`) gets a slot
and none of its GOT references is relaxed (today each ADRP / LDR is relaxed on its own, which a STR would
break); a GOT reference with an addend to a dynamic import is a GLOB_DAT with that addend (slots per
(import, addend)).  (3) TLS, scope pending.

## 3e. Label differences

🟡 IN PROGRESS (claimed 2026-10-05; user: "go ahead with label differences").  `l2 - l1` (with `.` on
either side, plus numbers and arithmetic) in a constant definition, an immediate and a data directive.

clang 21 (probed 2026-10-05):
- ELF: a difference of two labels in one section folds to a number wherever a number goes — any
  binding (global, weak), forward or backward, with arithmetic (`(l2 - l1) / 4`, `* 3 + 1`, `-(…)`),
  across alignment padding; `D = l3 - l1` before the labels works.  In data, `sym - .` / `sym + k - .`
  / `sym - l_here` (subtracting a location in the data's own section) is R_AARCH64_PREL16 / 32 / 64
  (`.hword` / `.word` / `.quad`); `. - sym`, a difference across sections otherwise, and one with an
  undefined symbol subtracted are errors.
- Immediates: clang takes a difference only where the operand has a layout-time fixup — ADD / SUB /
  CMP / CMN imm12, load / store offsets, `mov` (as MOVZ / MOVN, ±0xFFFF), branch targets (`b l1 +
  (l3 - l2)`) — and rejects it (even with backward labels) in logical immediates, explicit MOVZ /
  MOVK, TBZ bit numbers and the like.
- Mach-O (`.subsections_via_symbols`): in data, a difference not fixed at assembly is an
  ARM64_RELOC_SUBTRACTOR + UNSIGNED pair (4 or 8 bytes), even of two external symbols; in an
  immediate it is an error ("unknown fixup").  clang folds a difference of two labels in different
  atoms of one section when both precede the use, and emits the pair when they follow it — an
  artifact: the linker may move atoms apart.

Design (proposed):
- The expression evaluator carries a difference (`Sym - Neg + Val`, either side possibly `.`).  It is a
  number once both labels are placed and their distance is fixed — one section on ELF, one atom on
  Mach-O (the rule PC-relative displacements already follow) — so it folds, and any arithmetic
  applies; anywhere a number is read, such a difference may stand.
- Forward references: AArch64 instructions and data directives have a size independent of the
  values, so when the first pass meets a difference it cannot fold yet, the file is assembled a second
  time with the first pass's label offsets (only then — a file with none is assembled once).  A full
  second pass keeps redefined constants and numeric labels meaning what they meant on their line, which
  re-parsing deferred lines at the end would not.  A size-determining operand (`.zero`, `.fill`
  count, `.balign`) takes no forward difference.  The second pass checks every first-pass offset it
  used against where the label landed.
- Not fixed: in data, a relocation — ELF PREL16 / 32 / 64 when the subtracted location is in the
  data's own section (else an error, as clang), Mach-O a SUBTRACTOR + UNSIGNED pair (4 / 8 bytes) —
  for `sym - sym2 + k` only (arithmetic on it is an error); in an immediate, an error.
- bnld: PREL16 / PREL64 (PREL32 exists) and Mach-O SUBTRACTOR pairs.

Commits: (1) evaluator + fixed differences, two-pass forward references (ELF, Mach-O same-atom);
(2) relocatable differences in data (writers, bnld).

Decisions for the user (asked 2026-10-05):
(a) immediates: a fixed difference wherever a number goes (recommended — one rule; a superset of
    clang, which rejects it in operands without a layout fixup), or only where clang takes it;
(b) Mach-O: a difference across atoms is never folded — a relocation pair in data, an error in an
    immediate, even when clang folds it (backward labels) — recommended;
(c) the two-pass mechanism for forward references (recommended), with forward differences rejected in
    size-determining directives;
(d) plain symbols in data directives (`.uint64 sym`, an absolute address: R_AARCH64_ABS64 / UNSIGNED),
    which the dialect does not take today — in scope beside the relocatable differences?

## Decisions (user, 2026-09-30 — each the recommended option)

1. ELF temporaries: match clang — omit `.L…` and numeric-label instances from the ELF symbol table
   and relocate against section symbols.
2. Forward references to a constant: reject all; the `mov` / scaled-`ldr` forms clang accepts go
   on the deliberate-reject list.
3. Symbol-valued constants: aliases (`A = sym ± k`) and `C = .` in 3b; label differences (in
   definitions, immediates and data directives) are their own family on the todo list.
4. `.ltorg` / `.pool` are added as directives.
