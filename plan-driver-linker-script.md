# Driver scripts with linker-script-level control (Step 7, phase 2)

Status: DESIGN (2026-09-02).  Follow-up to the interpreted linker drivers (Step 7;
plan-step7-driver.md) and the shipped `pkg/binate/link` interface (plan-driver-api.md,
landed `6efec9cf1`).  Goal (user, 2026-09-02): make a bnld driver script able to do
everything a GNU ld linker script can, **within what we support internally** — so a driver
is genuinely useful, not just "pick which whole-link function to call."

## Where we are — the gap

Today a driver calls one of `link.Link` / `LinkDynElf` / `LinkDynMacho` with
`(inputPaths, entry, machine, base, out[, sharedLibs])`.  All layout is HARDCODED inside
`link.Layout` (`pkg/binate/link/layout.bn`):

- exactly **4 output-section kinds** — text / rodata / data / bss — classified from ELF
  section flags (`kindOf`), in that fixed **order**, with fixed **names** (`.text`, …);
- a single **`base`** address, sections placed sequentially, each aligned to the max input
  alignment; one hardcoded page-aligned read-only/read-write split;
- **no** memory regions, per-section addresses, input-section wildcards, `KEEP`/`DISCARD`,
  script-defined symbols, or location-counter control.

`EmitElfExec` then derives exactly **two PT_LOADs** (the RO group and the RW group) from
section writability (`groupStats`) — the segment model is hardcoded to that 2-group split.

So a driver cannot express anything our own `runtime/baremetal_arm32/baremetal.ld` does.

## The concrete reference — `baremetal.ld`

That script (consumed today by the LLVM/GNU-ld arm32-baremetal path, NOT by bnld) is the
best statement of "what we support internally."  It uses exactly these GNU-ld features:

- `ENTRY(_start)` — entry symbol (we already handle via the `entry` param → `e_entry`).
- `MEMORY { RAM (rwx) : ORIGIN = 0x40000000, LENGTH = 16M }` — one named region.
- `SECTIONS { … }` with, per output section:
  - a name (`.text`, `.ARM.exidx`, `.data`, `.bss`, …);
  - `ALIGN(4)` on the section;
  - input-section patterns: `*(.text*)`, `*(.rodata*)`, `KEEP(*(.text.startup))`, `*(COMMON)`;
  - region assignment `> RAM`;
- **script-defined symbols at a layout point**: `__exidx_start = .;` … `__exidx_end = .;`,
  `__bss_start`/`__bss_end`;
- **location-counter arithmetic**: `. = ALIGN(8);`, `_stack_top = ORIGIN(RAM) + LENGTH(RAM);`.

That is the v1 target feature set.  Things GNU ld can do that baremetal.ld does NOT use, and
that we almost certainly don't need for "what we support internally," are explicitly out of
v1 scope (see below).

## Key insight — the driver IS the script; no text DSL

GNU ld invents a whole language (its own expression grammar, `ALIGN`/`ORIGIN`/`LENGTH`
functions, `.` location counter) because a `.ld` file is inert text.  **Our driver is Binate
code, interpreted.**  So we do NOT build a linker-script parser, grammar, or expression
evaluator.  The driver writes ordinary Binate — loops, arithmetic, helper calls — to
*construct a layout spec* (a value), and hands it to the linker.  `_stack_top =
ORIGIN(RAM) + LENGTH(RAM)` is just `ram.Origin + ram.Length` in Binate; `. = ALIGN(8)` is a
spec directive the engine interprets.  The only thing the engine must expose that the driver
can't precompute is the **location counter `.`** — addresses aren't known until layout runs
(they depend on accumulated input-section sizes) — so symbol-at-a-point and `. = expr` are
*directives in an ordered spec list* that the engine processes while tracking `.`.

This makes the whole feature far smaller than "implement GNU ld scripts": it is a
**layout-spec data model + a spec-driven layout engine**, plus exposing the pipeline.

## Proposed driver-facing API (in `pkg/binate/link`, shipped via ifaces/toolchain)

A declarative spec mirroring SECTIONS, built by the driver.  Sketch (names/shapes TBD):

    // A named memory region (MEMORY).
    type Region struct { Name @[]char; Origin uint64; Length uint64; Attrs int }

    // One entry in an output section's body, processed in order (mirrors a SECTIONS
    // output-section body: input patterns, KEEP, symbol defs, location-counter ops).
    // A tagged item — exactly one field is meaningful per Kind.
    type SecItem struct {
        Kind    int          // pattern | keepPattern | symbolAtDot | setDot | align
        Pattern @[]char       // "*(.text*)" style glob over input section names
        Symbol  @[]char       // for symbolAtDot: define Symbol = current `.`
        Value   uint64        // for setDot: `.` = Value  (driver precomputed the expr)
        Align   int           // for align: `.` = ALIGN(Align)
    }

    // An output section (a SECTIONS entry): name, alignment, ordered body, region.
    type OutSpec struct {
        Name   @[]char
        Align  int
        Items  @[]SecItem
        Region @[]char        // "> RAM"
        // (Flags/type derived from matched inputs, or overridable.)
    }

    // The whole script.
    type LayoutScript struct {
        Entry    @[]char
        Regions  @[]Region
        Sections @[]OutSpec        // output sections + top-level symbol/dot directives
        // top-level `. = ALIGN(8); _stack_top = …` handled as trailing directives.
    }

Plus either a one-call entry or exposed primitives (or both):

    // High-level: read inputs, lay out per the script, resolve, relocate, emit.
    func LinkWithScript(inputPaths @[]@[]char, script @LayoutScript, machine int,
            out *[]readonly char) @[]readonly char

    // Primitives, so a driver can interpose (already have ReadObject/ReadArchive):
    func LayoutScripted(objs @[]@InputObject, script @LayoutScript)
            (@LayoutResult, @SymbolTable-additions, @[]readonly char)
    // Resolve / Relocate / EmitElfExec exposed too (Resolve + Emit are currently
    // package-internal-but-exported; confirm they're in the shipped .bni or add them).

The engine walks `Sections` in order maintaining `.` (seeded from each output section's
`Region` origin or the running counter), matches each `pattern` against the pool of input
sections (glob on section name), concatenates matched inputs (honoring `KEEP` = never GC),
applies `align`/`setDot`, and records `symbolAtDot` as a new absolute symbol fed into the
symbol table so relocations to `_stack_top`/`__bss_start` resolve.  Unmatched input sections
either fall to a default/`/DISCARD/` per the script's policy.

## Internal refactoring

1. **Spec-driven layout engine** (`layout.bn`): a `LayoutScripted` alongside the existing
   `Layout`/`LayoutPaged` (which stay for the default whole-link path).  It produces the
   same `LayoutResult` (so Relocate/Emit are reused) plus a set of script-defined absolute
   symbols.
2. **Script symbols into `Resolve`** (`resolve.bn`): the symbol table must admit
   layout-defined absolute symbols (`__bss_start`, `_stack_top`) so `Relocate` and the entry
   lookup see them.  Ordering: script symbols depend on layout addresses, so inject them
   after `LayoutScripted` computes `.` values, before `Relocate`.
3. **Region → segment emit** (`emit_elf.bn`): generalize `EmitElfExec`'s hard 2-PT_LOAD
   model to build segments from the laid-out sections' regions/permission groups (baremetal
   wants a single `rwx` RAM PT_LOAD; hosted static wants the W^X 2-group split).  This is the
   most involved change — the current groupStats/2-phdr logic assumes exactly RO+RW.
4. **Input-section pattern matching**: a small glob (`*(.text*)`) over input section names —
   pure string matching, no new infra.

Dynamic ELF / Mach-O keep their existing structural layout (dynamic segments, PLT/GOT,
Mach-O __DATA) — script-driven layout is primarily a **static / baremetal** capability, so
`LinkWithScript` targets the static path first.

## v1 scope vs deferred

**v1 (matches baremetal.ld):** ENTRY, MEMORY regions, output sections with names + ALIGN +
ordered bodies, input-section glob patterns, KEEP, `> REGION`, symbol-at-`.`, `. = ALIGN(n)`,
top-level `sym = expr` (expr precomputed by the driver in Binate), `*(COMMON)`, default
discard of unmatched-and-unKEPT sections.  Concrete proof: a Binate driver script reproduces
`baremetal.ld` and bnld links the arm32-baremetal target with it.

**Deferred (out of v1 unless a real need appears):** separate LMA/VMA (`AT>` for ROM-copy —
baremetal.ld is RAM-only so unused), explicit `PHDRS`, overlays, `SORT_*` variants, `FILL`,
`INSERT`, `PROVIDE`/`PROVIDE_HIDDEN` weak semantics, `ASSERT`, `/DISCARD/` as an explicit
target (default-discard covers the need), byte-data commands (`BYTE`/`LONG`/`QUAD`),
`NOLOAD`.

## Incremental plan (proposed)

1. **Spec types + glob matcher + `LayoutScripted`** producing `LayoutResult` + script symbols
   for the STATIC path; unit tests on synthetic InputObjects (no emit yet).
2. **Script symbols into Resolve** + entry-from-script; wire `LinkWithScript` for static ELF
   reusing the existing single-group emit for a simple case.
3. **Region→segment emit generalization** (the `rwx`-single-PT_LOAD baremetal case + keep the
   hosted W^X 2-group case green).
4. **Reproduce `baremetal.ld` as a Binate driver script**; an e2e that bnld-links a small
   arm32-baremetal program with it and runs under QEMU — the useful end-to-end proof.
5. Expose the spec types + `LinkWithScript` (+ any needed primitives) in the shipped
   `pkg/binate/link.bni`; a reference scripted driver alongside `drivers/elf.bn`.

## Open scope questions for the user

1. **Driving goal:** is the concrete target "bnld links our arm32-baremetal target via a
   driver script (retiring the GNU-ld-consumed baremetal.ld for the *self-hosted* path)"?
   That gives a testable end-to-end proof and bounds scope.  (The LLVM path can keep its
   `.ld` independently.)  Or is the goal a general scripted-layout API with baremetal as just
   one example?
2. **API shape:** declarative spec (above — driver builds a `LayoutScript` value, engine
   walks it) vs imperative primitives (driver calls `beginSection`/`placePattern`/`defineSym`
   step by step against a live layout builder).  The declarative spec is recommended (it
   mirrors SECTIONS and keeps the engine in control of `.`), but the imperative form gives
   the driver more raw control.  Or expose BOTH (declarative sugar over primitives).
3. **v1 feature line:** is the baremetal.ld feature set the right v1 cut, with everything
   else deferred until needed — or are specific deferred items (LMA/`AT>`, `PHDRS`, data
   commands) wanted up front?
