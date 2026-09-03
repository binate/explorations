# Driver scripts with linker-script-level control (Step 7, phase 2)

Status: DESIGN (2026-09-02, rev 2).  Follow-up to the interpreted linker drivers (Step 7;
plan-step7-driver.md) and the shipped `pkg/binate/link` interface (plan-driver-api.md,
landed `6efec9cf1`).  Goal (user, 2026-09-02): make a bnld driver script able to do
everything a GNU ld linker script can, **within what we support internally** — so a driver
is genuinely useful.

Design direction set by the user (2026-09-02):
- **Do NOT design for arm32-baremetal alone** — too narrow, risks a limited/wrong API that
  needs drastic later changes.  Pick REPRESENTATIVE examples (kernels, bootloaders,
  firmware) and make sure at least the **shape** of the APIs supports them.
- **Provide BOTH layers**: imperative primitives (the complete, full-control substrate) AND
  a declarative spec.  The primitives are the escape hatch for complex/esoteric cases, which
  lets the declarative spec stay deliberately simple/limited.

## Where we are — the gap

A driver today calls one of `link.Link` / `LinkDynElf` / `LinkDynMacho` with
`(inputPaths, entry, machine, base[, sharedLibs])`.  All layout is HARDCODED in
`link.Layout` (`pkg/binate/link/layout.bn`): exactly 4 output kinds (text/rodata/data/bss)
by ELF flags, fixed order + names, one `base`, one page-aligned RO/RW split.  `EmitElfExec`
then derives exactly **two PT_LOADs** (RO group, RW group) from section writability
(`groupStats`).  A driver can control none of it.

## Key insight — the driver IS the script; no text DSL

GNU ld invents a language (expression grammar, `ALIGN`/`ORIGIN`/`LOADADDR`, the `.`
location counter) only because a `.ld` file is inert text.  **Our driver is Binate code,
interpreted.**  So we build NO linker-script parser/grammar/evaluator.  The driver writes
ordinary Binate — arithmetic, loops, helpers — to drive a layout, and the only state the
engine must expose that the driver can't precompute is the **location counter `.`**
(addresses depend on accumulated input-section sizes).  `_estack = ORIGIN(RAM)+LENGTH(RAM)`
is `ram.Origin + ram.Length`; `_sidata = LOADADDR(.data)` is a value the engine hands back.
This turns "implement GNU ld scripts" into "a layout ENGINE the driver drives, plus a
declarative spec that compiles to the same engine calls."

## Representative use cases → required API shape

Concrete, well-known linker-script patterns (stated as representative shapes, not quotes):

### A. Cortex-M-style firmware (flash + RAM, ROM→RAM data copy)
Two regions `FLASH (rx)` + `RAM (rwx)`; vector table `KEEP`t at flash start; `.text`/`.rodata`
in FLASH; **`.data` has VMA in RAM but LMA in FLASH** (`> RAM AT> FLASH`) so the image ships
in flash and startup copies it to RAM using boundary symbols (`_sidata = LOADADDR(.data)`,
`_sdata`/`_edata`); `.bss` boundary symbols for zeroing; `_estack = ORIGIN(RAM)+LENGTH(RAM)`.
Needs: **multiple regions, LMA≠VMA, KEEP, section-boundary symbols, region-expr symbols.**

### B. Higher-half OS kernel (x86-64 / aarch64)
Loads physically low (e.g. `0x100000`) but runs at a high VMA (`0xffffffff80000000`);
**explicit `PHDRS`** (separate `text`/`rodata`/`data` PT_LOADs with chosen flags); each
section `AT(ADDR(.text) - KERNEL_VMA)` so **LMA = VMA − offset**; multiboot/boot header
`KEEP`t first; **sections assigned to named segments** (`:text`); boundary symbols
(`__init_begin`/`__init_end`, `_end`); discard of build-only sections.
Needs: **explicit PHDRS + section→segment assignment, LMA≠VMA (offset), KEEP/DISCARD,
symbols, page alignment.**

### C. MBR / raw bootloader (x86, or a chain-load stub)
**Raw-binary output** (no ELF headers); fixed origin (`0x7C00`); pad to a fixed offset and
emit a literal signature (`. = 0x1FE; SHORT(0xAA55)`); everything in one blob.
Needs: **raw-binary output format, fixed origin, fill/pad-to-offset, literal data commands
(BYTE/SHORT/LONG/QUAD).**

### D. Our own arm32-baremetal (`baremetal.ld`) — the on-hand concrete case
One `rwx` RAM region; `ENTRY`; sections with `ALIGN` + globs + `KEEP` + `> RAM`;
symbol-at-`.` (`__exidx_start/end`, `__bss_start/end`); `. = ALIGN(8)`; `*(COMMON)`;
`_stack_top`.  Simplest case — a good first e2e — but explicitly NOT the whole target.

### Capability matrix the SHAPE must support (even if v1 defers implementing some)

| Capability | A fw | B kern | C boot | D bare | Notes |
|---|---|---|---|---|---|
| Multiple MEMORY regions | ✓ | ✓ | – | – | region origin/length/attrs |
| Output sections, name, ALIGN | ✓ | ✓ | ✓ | ✓ | |
| Input globs `*(.text*)`, KEEP | ✓ | ✓ | ✓ | ✓ | |
| `> REGION` (VMA region) | ✓ | ✓ | – | ✓ | |
| **LMA ≠ VMA** (`AT>`/`AT()`/LOADADDR) | ✓ | ✓ | – | – | p_paddr≠p_vaddr; ROM-copy/higher-half |
| Section-boundary + region-expr symbols | ✓ | ✓ | – | ✓ | fed to symbol table |
| location counter `.`: read / set / ALIGN | ✓ | ✓ | ✓ | ✓ | engine state |
| **Explicit PHDRS + section→segment** | – | ✓ | – | – | vs auto-derive from perms |
| **Literal data (BYTE/SHORT/LONG/QUAD)** | – | – | ✓ | – | boot sig, hand tables |
| **Fill / pad-to** | – | – | ✓ | – | |
| **Raw-binary output** | (opt) | – | ✓ | – | plus ELF (have) / Mach-O (have) |
| DISCARD / default-drop unplaced | – | ✓ | – | ✓ | |
| `*(COMMON)` | – | – | – | ✓ | |
| ENTRY (sym or addr) | ✓ | ✓ | ✓ | ✓ | have (param) |

Deferred *shape-supported* extras (design must not preclude, needn't implement early):
`SORT_*`, overlays, `PROVIDE`/weak, `ASSERT`, `NOLOAD`, `INSERT`, per-section fill patterns.

## Two-layer API (in `pkg/binate/link`, shipped via ifaces/toolchain)

### Layer 1 — imperative primitives (the complete substrate / escape hatch)

A stateful `LayoutBuilder` the driver drives.  This layer must be able to express EVERY row
of the matrix; the declarative layer and all four use cases compile to it.  Sketch (shapes
TBD; names illustrative):

    func NewLayout(machine int) @LayoutBuilder

    // Regions (MEMORY).
    func (b @LayoutBuilder) DefineRegion(name @[]char, origin uint64, length uint64, attrs int)

    // Location counter.
    func (b @LayoutBuilder) Dot() uint64
    func (b @LayoutBuilder) SetDot(addr uint64)
    func (b @LayoutBuilder) AlignDot(n int)
    func (b @LayoutBuilder) SetDotToRegion(region @[]char)   // `.` = ORIGIN(region)

    // Output sections.  VMA starts at `.`; LMA defaults to VMA unless set.
    func (b @LayoutBuilder) BeginSection(name @[]char, flags int) @Section
    func (b @LayoutBuilder) SectionAtRegion(region @[]char)   // route VMA to region
    func (b @LayoutBuilder) SectionLoadRegion(region @[]char) // AT> : LMA in region
    func (b @LayoutBuilder) SectionLoadAddr(lma uint64)       // AT() : explicit LMA
    func (b @LayoutBuilder) PlaceInputs(pattern @[]char, keep bool) // glob, append, adv `.`
    func (b @LayoutBuilder) EmitData(bytes @[]uint8)          // BYTE/SHORT/LONG/QUAD/tables
    func (b @LayoutBuilder) PadTo(addr uint64, fill uint8)
    func (b @LayoutBuilder) EndSection()

    // Symbols (fed into the symbol table so relocations + entry see them).
    func (b @LayoutBuilder) DefineSymbol(name @[]char, value uint64)  // absolute
    func (b @LayoutBuilder) DefineSymbolAtDot(name @[]char)           // = current `.`
    func (b @LayoutBuilder) LoadAddrOf(section @Section) uint64       // LOADADDR()

    // Segments / PHDRS (or auto-derive if none declared).
    func (b @LayoutBuilder) DefineSegment(name @[]char, ptype int, flags int) @Segment
    func (b @LayoutBuilder) AssignSection(section @Section, segment @Segment)

    // Finish: resolve inputs, relocate against the frozen addresses, emit.
    func (b @LayoutBuilder) SetEntry(name @[]char)                    // or SetEntryAddr
    func (b @LayoutBuilder) LinkInputs(objs @[]@InputObject) @[]readonly char  // resolve+place-remaining
    func (b @LayoutBuilder) EmitElf(out *[]readonly char) @[]readonly char
    func (b @LayoutBuilder) EmitRawBinary(out *[]readonly char) @[]readonly char
    // (EmitMacho later; dynamic ELF/Mach-O keep their structural whole-link paths.)

Open shape question: the ordering of `resolve` vs interleaved `PlaceInputs` — GNU ld resolves
globally then places; a stateful builder can resolve once (`LinkInputs`) and let `PlaceInputs`
pull from the resolved pool.  Needs pinning during design.

### Layer 2 — declarative spec (sugar for the common case)

The `LayoutScript` value from rev 1 (Regions + ordered OutSpec sections whose bodies are
ordered items: pattern / keep / symbol-at-dot / set-dot / align, plus `> REGION`, optional
`AT>`/segment).  It **compiles to Layer-1 calls** — no separate engine.  It targets cases
A/D and the common part of B, deliberately omitting the esoteric (hand data blobs, arbitrary
PHDRS arithmetic) which drop to Layer 1.  A driver mixes them: build most of the layout
declaratively, then reach for the builder for the one weird section.

    func LinkWithScript(inputPaths @[]@[]char, script @LayoutScript, machine int,
            out *[]readonly char) @[]readonly char   // convenience over Layer 1

## Internal refactoring implied

1. **Layout engine = the `LayoutBuilder`** (`layout.bn`): the existing `Layout`/`LayoutPaged`
   become one built-in "default script" over the same engine (or stay as the fast path for
   the whole-link functions).  Produces `LayoutResult` + script-defined symbols + optional
   explicit segment table.
2. **Emit generalization** (`emit_elf.bn`) — the biggest change.  Today: 2 PT_LOADs from
   RO/RW.  Needed: (a) **p_paddr ≠ p_vaddr** when LMA≠VMA; (b) **explicit segments** when the
   script declares PHDRS (else auto-derive as today); (c) **raw-binary emit** (concatenate
   sections by LMA, no ELF headers) as a second output backend.
3. **Symbol injection into `Resolve`** (`resolve.bn`): layout-defined absolute symbols
   available to `Relocate` + entry lookup; injected after addresses are frozen.
4. **Input-section glob matcher**: pure string matching over input section names.

Dynamic ELF / Mach-O keep their structural layout; scripted layout is a **static / bare-metal
/ freestanding** capability first.

## What v1 IMPLEMENTS vs what the SHAPE supports

- **Shape (design now, must not preclude):** the entire capability matrix + Layer-1
  primitives covering all of A/B/C/D, including LMA≠VMA, explicit PHDRS, data/fill, raw
  binary.
- **v1 implements (proposed):** Layer-1 primitives + Layer-2 spec for **A (firmware
  ROM-copy), C (raw bootloader blob), D (baremetal)**, i.e. regions, globs/KEEP, `>REGION`,
  **LMA≠VMA**, symbols, `.`-control, data/fill, ELF + raw-binary emit.  **B (explicit PHDRS)
  is shape-supported but its emit path can land second** — but the primitive signatures for
  segments ship in v1 so the shape is proven.  Confirm this cut with the user.

## Incremental plan (proposed)

1. **Design pin-down**: finalize Layer-1 primitive signatures against A/B/C/D by writing each
   as pseudo-driver code (paper exercise) — proves the shape before any impl.
2. **Engine core**: `LayoutBuilder` + glob matcher + `LayoutResult`/symbols for the static
   path; unit tests on synthetic InputObjects (no emit).
3. **Emit generalization**: p_paddr/LMA, then raw-binary backend; keep the existing hosted
   W^X 2-group ELF path green.
4. **Symbols into Resolve**; `LinkWithScript` + Layer-2 spec compiling to Layer 1.
5. **e2e proofs**: (D) reproduce baremetal.ld as a driver, bnld-link + QEMU; (A) a firmware
   ROM-copy layout with a data-copy startup; (C) a raw-binary blob with a signature.
6. **Explicit PHDRS emit** (case B) + a higher-half-style e2e.
7. Ship the Layer-1 + Layer-2 API in `pkg/binate/link.bni`; reference scripted drivers.

## Scope decisions (user, 2026-09-02)

1. **v1 cut**: implement primitives + spec for **A/C/D now**; **B's explicit-PHDRS emit lands
   second** — but ALL Layer-1 primitive signatures (incl. segments) ship in v1 so the shape
   is locked now.
2. **Raw-binary output**: **yes** — add a raw-binary emit backend (needed for C, common for
   firmware images).
3. **Representative set**: **A/B/C/D is enough** to lock the shape — proceed to pin down the
   primitive signatures against them.

## API pin-down (design step 1) — the four use cases as driver code

Each use case written against the Layer-1 primitives, to prove the signatures are complete
and ergonomic BEFORE implementation.  (Illustrative Binate; error handling elided; a driver's
`Drive(objs @[]@[]char, out @[]char, target @[]char)` first reads the object paths into
`@[]@InputObject` via `link.ReadObject` — shown as `readAll(objs)` — and, if it links
archives, selects members via `link.SelectMembers`.)

### D. baremetal (declarative Layer-2 — the common case)

    script := link.NewScript("_start")
    script.Region("RAM", 0x40000000, 16*1024*1024, link.AttrRWX)
    sText := script.Section(".text", link.SF_READ|link.SF_EXEC); sText.Region("RAM"); sText.Align(4)
    sText.Keep("*(.text.startup)"); sText.Place("*(.text*)"); sText.Place("*(.rodata*)")
    sX := script.Section(".ARM.exidx", link.SF_READ); sX.Region("RAM"); sX.Align(4)
    sX.SymbolAtDot("__exidx_start"); sX.Place("*(.ARM.exidx*)"); sX.SymbolAtDot("__exidx_end")
    // ... .ARM.extab, .data ...
    sBss := script.Section(".bss", link.SF_READ|link.SF_WRITE); sBss.Region("RAM"); sBss.Align(4)
    sBss.SymbolAtDot("__bss_start"); sBss.Place("*(.bss*)"); sBss.Place("*(COMMON)")
    sBss.SymbolAtDot("__bss_end")
    script.AlignDot(8)
    script.Symbol("_stack_top", 0x40000000 + 16*1024*1024)   // driver knows the literals
    err := link.LinkWithScript(readAll(objs), script, out)   // ELF (default)

### C. MBR bootloader (imperative Layer-1 — raw binary, data, pad)

    b := link.NewLayout(link.EM_X86_64); b.SetInputs(readAll(objs))
    b.SetDot(0x7C00)
    b.BeginSection(".text", link.SF_READ|link.SF_EXEC); b.Place("*(.text*)", false); b.EndSection()
    b.BeginSection(".sig", link.SF_READ)
    b.PadTo(0x7C00 + 0x1FE, 0x00)         // fill to offset 510
    b.EmitData([2]uint8{0x55, 0xAA})       // boot signature (SHORT 0xAA55, little-endian)
    b.EndSection()
    if e := b.Finish(); len(e) != 0 { return e }   // resolve + relocate
    err := b.EmitRawBinary(out)

### A. Cortex-M firmware (imperative — two regions, LMA≠VMA ROM→RAM copy)

    b := link.NewLayout(link.EM_ARM); b.SetInputs(readAll(objs))
    b.DefineRegion("FLASH", 0x08000000, 512*1024, link.AttrRX)
    b.DefineRegion("RAM",   0x20000000, 128*1024, link.AttrRWX)
    b.SetDotToRegion("FLASH")
    b.BeginSection(".isr_vector", link.SF_READ); b.SectionAtRegion("FLASH")
    b.Place("*(.isr_vector)", true /*KEEP*/); b.EndSection()
    b.BeginSection(".text", link.SF_READ|link.SF_EXEC); b.SectionAtRegion("FLASH")
    b.Place("*(.text*)", false); b.Place("*(.rodata*)", false); b.EndSection()
    dataSec := b.BeginSection(".data", link.SF_READ|link.SF_WRITE)
    b.SectionAtRegion("RAM")            // VMA cursor = RAM
    b.SectionLoadRegion("FLASH")        // AT> FLASH : LMA cursor = FLASH
    b.SymbolAtDot("_sdata"); b.Place("*(.data*)", false); b.SymbolAtDot("_edata")
    b.EndSection()
    b.DefineSymbol("_sidata", b.LoadAddrOf(dataSec))   // LOADADDR(.data)
    b.BeginSection(".bss", link.SF_READ|link.SF_WRITE); b.SectionAtRegion("RAM")
    b.SymbolAtDot("_sbss"); b.Place("*(.bss*)", false); b.Place("*(COMMON)", false)
    b.SymbolAtDot("_ebss"); b.EndSection()
    b.DefineSymbol("_estack", 0x20000000 + 128*1024)
    b.SetEntry("Reset_Handler")
    if e := b.Finish(); len(e) != 0 { return e }
    err := b.EmitElf(out)              // ELF carrying p_paddr(LMA)≠p_vaddr(VMA) for .data

### B. Higher-half kernel (imperative — explicit PHDRS, LMA = VMA − offset)  [v1 shape; emit 2nd]

    KVMA := cast(uint64, 0xffffffff80000000)
    b := link.NewLayout(link.EM_X86_64); b.SetInputs(readAll(objs))
    segText := b.DefineSegment("text", link.PT_LOAD, link.PF_R|link.PF_X)
    segData := b.DefineSegment("data", link.PT_LOAD, link.PF_R|link.PF_W)
    b.SetDot(0x100000)                                   // physical load
    boot := b.BeginSection(".boot", link.SF_READ|link.SF_EXEC)
    b.Place("*(.multiboot)", true); b.AssignSection(boot, segText); b.EndSection()
    b.SetDot(b.Dot() + KVMA)                             // move to high half
    t := b.BeginSection(".text", link.SF_READ|link.SF_EXEC)
    b.SectionLoadAddr(b.Dot() - KVMA)                    // AT(ADDR(.text) - KVMA)
    b.Place("*(.text*)", false); b.AssignSection(t, segText); b.EndSection()
    // ... .rodata -> segText, .data/.bss -> segData, each SectionLoadAddr(Dot()-KVMA) ...
    b.SetEntry("_start")
    if e := b.Finish(); len(e) != 0 { return e }
    err := b.EmitElf(out)              // explicit PHDRS emit — the part landing second

### What the exercise changed / pinned

- **`SetInputs(objs)`** (the pool patterns match against) is explicit and separate from
  reading — the driver reads paths→objects (and selects archive members via
  `link.SelectMembers`) first.  `Place`/`Keep` glob over the pool's input-section names.
- **Two cursors per region**: a VMA cursor (`SetDotToRegion`/`SectionAtRegion`) and an
  independent **LMA cursor** (`SectionLoadRegion` for `AT>`).  `SectionLoadAddr(addr)` sets an
  explicit LMA (kernel `AT()`).  Default LMA = VMA when neither is set.
- **`Section` handle** returned by `BeginSection`/`Section`, needed by `LoadAddrOf(sec)`
  (LOADADDR) and `AssignSection(sec, seg)` (PHDRS).
- **`Dot()`** exposes the live location counter for driver-side arithmetic (kernel `AT()`,
  higher-half offset) — the one thing the driver can't precompute.
- **`Finish()`** is a distinct step (resolve cross-refs against frozen addresses + inject
  script symbols + relocate) BEFORE `EmitElf`/`EmitRawBinary` — so the output FORMAT is
  chosen after layout, and the same laid-out image can emit as ELF or raw binary.
- **Region attrs** (`AttrRX`/`AttrRWX`/…) and **segment/phdr consts** (`PT_LOAD`, `PF_*`)
  join the shipped consts alongside `EM_*`.  Region origin/length are driver literals (no
  accessor needed); only LMA (`LoadAddrOf`) is engine-computed.
- Declarative Layer-2 (`NewScript`/`Section`/`Place`/`Keep`/`SymbolAtDot`/`AlignDot`) is a
  thin recorder that replays as the Layer-1 calls above — case D shows it; A/B/C use Layer-1
  directly where they need LMA/segments/raw-data.

### Refined Layer-1 primitive set (result of the pin-down)

    NewLayout(machine) @LayoutBuilder
    (b) SetInputs(objs @[]@InputObject)
    (b) DefineRegion(name, origin uint64, length uint64, attrs int)
    (b) Dot() uint64 / SetDot(addr) / AlignDot(n) / SetDotToRegion(name)
    (b) BeginSection(name, flags) @Section / EndSection()
        (sec)  SectionAtRegion(name) / SectionLoadRegion(name) / SectionLoadAddr(lma)
    (b) Place(pattern, keep bool) / EmitData(bytes) / PadTo(addr, fill uint8)
    (b) DefineSymbol(name, value uint64) / SymbolAtDot(name) / LoadAddrOf(sec) uint64
    (b) DefineSegment(name, ptype, flags) @Segment / AssignSection(sec, seg)
    (b) SetEntry(name) / SetEntryAddr(addr)
    (b) Finish() @[]readonly char
    (b) EmitElf(out) @[]readonly char / EmitRawBinary(out) @[]readonly char
    // helpers: ReadObject/ReadArchive (have), SelectMembers(objs, archives) @[]@InputObject

The shape covers A/B/C/D.  Next: implement (plan step 2 — engine core), with `DefineSegment`/
`AssignSection`/`SectionLoadAddr` signatures shipped but their EMIT (case B) landing second.

## Review resolutions (rev 3, 2026-09-02)

Adversarial design review (grounded against `layout.bn`/`emit_elf.bn`/`resolve.bn`/
`relocate.bn` + the shipped `.bni`) found NO break to the exported API (`OutputSection`/
`LayoutResult` are package-internal → additive to extend; the builder API is all-new), but
flagged LAYOUT SEMANTICS that become breaking once drivers depend on them.  Resolutions:

**Semantics pins (decided):**

1. **Orphan / unplaced-section policy (user, 2026-09-02): Layer-1 strict, Layer-2
   auto-places.**  In the low-level builder, an input section left unplaced AND referenced is
   a **hard link error** (fail loud — the driver places what it needs; matches Binate's
   explicitness).  The high-level/declarative convenience auto-places orphans by kind
   (text/rodata/data/bss, like today's `Layout`) so the common case stays easy.  An explicit
   `Discard(pattern)` drops sections intentionally.  (Never silently drop a referenced
   section.)
2. **`Place`/`Keep` = first-match-wins + consume-once.**  An input section is claimed by the
   FIRST matching `Place`/`Keep` and never placed again (so case D's
   `Keep("*(.text.startup)")` then `Place("*(.text*)")` does not double-place it).  The
   builder tracks a consumed set over the `SetInputs` pool.
3. **`Place` baseline ordering = pool order.**  Matched sections append in `SetInputs` object
   order, then in-object section order.  `SORT_*` variants are additive later (a sort-key arg
   / new primitive); the baseline is pinned now so drivers can rely on it.

**Invariants (locked):**

4. **`OutputSection.Addr` stays VMA; add a separate `LMA` field.**  `Relocate`/`symbolAddr`
   patch against VMA (correct — relocations resolve to run-time addresses); emit places bytes
   at LMA.  Overloading `Addr` for LMA would miscompile every relocation in the LMA≠VMA cases
   — hard invariant.
5. **Injected symbols need an absolute-value representation.**  `SymDef`
   (`Name,ObjIdx,SymIdx,Binding`) can't express `DefineSymbol("_estack",0x…)` or a
   `.`-snapshot.  Add an absolute-symbol list consulted by `Lookup`/`symbolAddr`, injected
   after addresses freeze, precedence pinned vs input defs (a strong script symbol colliding
   with a strong input def is an error, as `Resolve` already does; `Provide*` is the additive
   escape).  **`SymbolAtDot` captures an absolute VMA snapshot** — correct for the
   static/bare-metal/freestanding (non-PIE) scope; section-relative would only matter for PIE,
   which stays on the structural path.
6. **Addresses are assigned EAGERLY during `BeginSection`/`Place`**, not deferred to
   `Finish` — so `LoadAddrOf(sec)` (case A) is resolvable when the driver calls it pre-`Finish`.
   `Finish` only resolves cross-refs + injects symbols + relocates.  `Place` MUST honor each
   input section's own alignment (today's `Layout` does `alignUp` per input) — a
   must-preserve, not a new feature.

**Layer-1 / Layer-2 unification (resolves the "no mixing seam" blocker):**

7. **One `LayoutBuilder`; the declarative layer is section-oriented CONVENIENCE METHODS on
   it, not a separate recorder.**  Drop the standalone `NewScript`/`LayoutScript` value and
   `LinkWithScript` one-shot from v1.  A `@Section` handle (from `BeginSection`) carries the
   readable chained sugar (`Region`/`Align`/`Keep`/`Place`/`SymbolAtDot`); the low-level
   primitives are methods on the same builder.  Mixing declarative + imperative is then FREE
   (same object) — case D reads declaratively, A/B/C drop to primitives, all on one builder.
   A portable/inspectable script-as-DATA value + `ApplyScript` remains available as a purely
   additive future layer if wanted.  (This still gives "both layers" per the earlier
   decision — high-level sugar + low-level escape hatch — just unified on one object.)

**Majors — additive, but designed in from day one (not deferred):**

- **Emit rewrite (the real work).**  `EmitElfExec` today uses one `base=sections[0].Addr`,
  file offset `= dataStart+(Addr-base)`, and `p_paddr:=p_vaddr` — which yields a ~400 MB file
  for FLASH(0x08000000)+RAM(0x20000000) and wrong paddr.  Replace with **LMA-keyed, per-region
  compact packing** and `p_paddr` from LMA.  Cannot be patched onto the contiguous model —
  budget a rewrite.  The entry-in-range guard (`entry>=base && entry<memEnd`) must relax for
  multi-region.
- **Region state carries ONE allocation cursor + length** (CORRECTED from the earlier
  "two cursors" pin — the implementation and adversarial review, 2026-09-02, found a single
  cursor is not just sufficient but *required*).  A single `Next` per region is drawn from by
  a section's VMA (`> REGION`) AND by a section's LMA (`AT> REGION`), so case A's `.text >
  FLASH` and `.data`'s `AT> FLASH` load image serialize in flash (`_sidata` lands right after
  `.text`); TWO independent cursors would let `.text`'s VMA and `.data`'s LMA both start at
  FLASH origin and OVERLAP.  This matches GNU ld (a region has one position pointer).  The
  engine **checks region overflow** (accumulated `Next` ≤ origin+length; GNU ld errors here —
  load-bearing for fixed flash/RAM), counting text+rodata+data-image against FLASH.
- **`*(COMMON)`** has no input-model representation (`InputSymbol` lacks SHN_COMMON); the
  reader/engine must synthesize a common area from tentative defs.  Real for baremetal C
  (cases A/D).
- **arm32 (`EM_ARM`) relocation is a PREREQUISITE for the A/D e2e**, not part of "e2e."
  `Relocate` handles only x64/aarch64; `EM_ARM` isn't even a const.  A `patchArm32`
  (R_ARM_ABS32, CALL/JUMP24, PREL31 for `.ARM.exidx`, Thumb variants) must land before the
  A/D proofs.  The layout ENGINE is machine-independent, so engine-core work is unblocked.

**Minor (additive/deferrable):** multi-byte fill patterns (`PadTo` is single-byte),
`SUBALIGN`, independent LMA alignment, `SORT_*`, `PROVIDE`/weak, `NOLOAD`, literal-data sugar
(`Short/Long/Quad` over `EmitData`).  Overlays and `.=.+N` gaps are already expressible
(`SetDot` to a repeated VMA; `SetDot(Dot()+N)`).

### Revised plan ordering (post-review)

1. ✅ DONE (landed 2026-09-02, commit d1914bdc0) — Engine core `LayoutBuilder`: primitives,
   glob matcher (split into `builder_glob.bn`), consume-once pool, eager address assignment,
   ONE allocation cursor per region + overflow check, fail-loud routing-after-content guards,
   input-size clamp, orphan policy; produces `LayoutResult` (with per-section `Lma`; `Addr`
   stays VMA) + an absolute script-symbol set (`AbsSyms`).  Unit-tested on synthetic
   InputObjects (no emit).  NOT the two-cursor model the earlier pin described — see the
   corrected majors bullet above.  Deferred to the emit step (4): the OUTPUT-SECTION START is
   not auto-aligned to its own `Align` (individual inputs get correct aligned VMAs, but
   `OutputSection.Addr` can be non-`Align`-aligned; ELF `sh_addr ≡ 0 mod sh_addralign` must be
   satisfied when emit is wired — either auto-align the section start then or mandate
   `SectionAlign`/`SetDot` by contract; LMA start alignment likewise).
2. ✅ DONE (landed 2026-09-02, commit 5bd7c8b22) — Absolute-symbol injection into
   `Resolve`/`symbolAddr`: `SymbolTable.Abs` + `lookupAbs`; `ResolveWithAbs(objs, abs)`
   skips abs-covered names in the undefined check (`Resolve` delegates with an empty set —
   whole-link path unchanged); `symbolAddr` resolves a Lookup-miss against the abs set;
   object defs win over a same-named abs symbol; `DefineSymbol`/`SymbolAtDot` are
   last-write-wins.  `LayoutBuilder.Resolve()` wires it.  Symbol surface split into
   `builder_symbols.bn` (+`_test`).
3. **arm32 relocator** (`patchArm32`) — prerequisite for A/D.
4. Emit rewrite: LMA-keyed per-region ELF packing (`p_paddr` from LMA, multi-region file
   offsets, relaxed entry guard) + the **raw-binary** backend.
5. `*(COMMON)` synthesis.
6. e2e proofs: D (baremetal → QEMU), A (firmware ROM-copy), C (raw-binary + signature).
7. Explicit-PHDRS emit (case B) + higher-half e2e.
8. Ship the builder API in `pkg/binate/link.bni`; reference scripted drivers.

Verdict (review): shape is sound to start the engine core with the above pins resolved.
