# Plan: decentralize the native SatEntry registry (RTTI transitive-closure PoC)

**Status:** ✅ **COMPLETE** — all three phases landed: 1 (`46f288d35`), 2 (`ff073777e`),
3 (`21a7d04a1`). A
proof-of-concept of the decentralized dependency-graph-of-fragments approach, done *before*
the stacktrace symbolization table ([`plan-stacktraces.md`](plan-stacktraces.md)) adopts the
same shape. Revised after a 3-lens adversarial review that caught a shared-symbol corruption
and an LLVM-retention gap; dep edges are STRONG (`external`/`SetGlobal`) — see the edges bullet.

## What this is (and is NOT)

This is a **PoC of the decentralized-graph mechanism** — its real payoff is the stacktrace
symbolization table, which reuses the same shape. It fixes **one** of Binate's
whole-program-enumeration points (the native SatEntry root). It is **not**, by itself, a
complete "opaque binary distribution" capability: the `__init` dispatcher (`__init_all`)
is built the *same* main-enumerated way (`initPkgNames` from `ldr.Order`,
`EmitInitDispatcher`, `main.bn:291-349`), so a facade-hidden package's `__init` /
top-level var initializers would be just as absent — and no wired/tested
Binate-consumes-a-prebuilt-`.a` path exists yet (`--library` targets C via `bn_init` /
`EmitLibInit`, not Binate import). Full opaque distribution would need the same treatment
applied to `__init` (and any other main-enumerated structure). We fix satentries now
because it de-risks the symbol table; we flag the rest honestly rather than claiming the
PoC "makes opaque distribution work."

## Problem

The native interface-satisfaction registry (`_satentry_root`, spec §11.12 `iface.rtti`)
is built by the **main module enumerating the full transitive package set** from the
driver's `ldr.Order` (`cmd/bnc/main.bn:280-308`, under a `satN > 0` skip of empty
packages), emitted only for the main module, consumed by `rt.BuildSatRegistry`. The root
is what **retains** each package's satentry data against dead-strip.

This holds for whole-program-from-source builds (correct today), but is the wrong shape
for **opaque binary distribution**: a closed-source library shipped as
`{facade.bni, bundle.a}` bundles internal packages the consumer's loader never names. The
**primary** failure is dead-strip: the facade's code references the internal package's
functions/vtables (to construct/return its types), so the archive member IS pulled in —
but the internal package's `_pkg_satentries` / `__satentry.<T,J>` **data** nodes are weak
and referenced only by the root, which (built from the consumer's `ldr.Order`) never names
them → they are dead-stripped → an interface assertion on a facade-hidden implementation
type MISSes (returns nil). (Archive non-inclusion is a secondary, conditional leg — only
if nothing in the pulled objects references the internal symbols at all.)

Latent today (whole-program-from-source loads everything); a blocker for the
binary-distribution model "package = separately compilable/linkable unit" aspires to.

## Fix — a dependency-graph of self-describing fragments (a SEPARATE symbol)

Introduce a **new per-package symbol `_pkg_satfrag`** — the graph node — *distinct from*
the existing `_pkg_satentries` array. **Do NOT repurpose `_pkg_satentries`**: it is
simultaneously the backing array for `reflect.Package.SatEntries`, read from offset 0 as a
bare `@SatEntryInfo[]` by the cross-mode VM injector `RegisterPackageSatEntries`
(`vm/extern_register.bn:122-128`, run in `-int`/VM modes) — prepending a header word would
shift element[0] and corrupt that consumer. So `_pkg_satentries` stays exactly as-is, and:

- **`_pkg_satfrag` = `{ entriesPtr = &_pkg_satentries (or null), ownCount, depCount,
  <direct-dep>._pkg_satfrag symrefs… }`.** It *points at* the existing reflect array for
  its own entries (no duplication) and carries symrefs to its **direct dependencies'**
  `_pkg_satfrag`. Self-describing (`ownCount`/`depCount` in the node), so no external
  length table.
- **Every package emits `_pkg_satfrag`, even with zero own-entries** (entriesPtr null,
  ownCount 0, but still its dep edges) — an empty package is a **waypoint** to non-empty
  descendants; dropping it severs reachability. Note: this is a NEW symbol, so the
  existing empty-package behavior of `_pkg_satentries` (skipped, `reflect.Package.SatEntries
  = {null,0}`, `data_pkg_descriptor.bn:72`) is **unchanged** — the waypoint rule lives
  entirely in `_pkg_satfrag` and does not ripple into the reflect descriptor or the VM
  injector.
- **Edges are the package's EFFECTIVE (compiler-injected) import set**, i.e.
  `m.ImportAliasPaths` — NOT its source `import`s. `lang` (50+ primitive-type satentries:
  `int:Stringer`, `Orderable`, `Hashable`, …) and `rt` are injected into *every* package's
  effective imports (`compile_imports.bn:186-188`, `registerMainImports:217-218`), rarely
  source-imported. Edges from raw source imports would drop `main → lang` and make
  `boxedInt.(Stringer)` MISS. `m.ImportAliasPaths` is per-package (no driver enumeration)
  and includes rt/lang/expose-closure; it over-approximates (extra edges only add
  already-reachable waypoints — safe).
- **Retention is per-backend and needs the LLVM primitive named explicitly.** On the
  **native** main-module build, `__entry`'s `OP_DATA_SYM_ADDR` LEA reloc roots main's
  `_pkg_satfrag`. On the **default LLVM** build, that same reference is a folded-away no-op
  `bitcast` (`emit_satroot.bn:52-55`, `ir/data_satroot.bn:22-24`) — so main's fragment
  must be pinned in **`@llvm.used`** exactly as the current root is (`emit_satroot.bn:49-59`).
  From that pinned root the dep-symref chain transitively retains (dead-strip) each present
  descendant fragment, and each fragment's `entriesPtr` retains that package's
  `_pkg_satentries` + `__satentry` nodes. (phase-1 note: `emit_cglobal.bn`'s
  `collectDefinedDataSyms` must list `_pkg_satfrag` alongside `_satentry_root`, or the native
  `__entry` LEA emits a conflicting `external global` declaration on LLVM — a redefinition.)
- **Dep edges are STRONG undefined refs** (LLVM `external global` / native `SetGlobal`),
  matching the existing `_satentry_root` cross-object pattern. The initial phase-1 draft used
  *weak* edges on the premise that an interface-only package like `reflect` emits no node and
  a strong edge would dangle — but that premise is **false**: `reflect` (and every compiled
  package) emits a `_pkg_satfrag`, since the driver compiles it as an LLVM dependency even
  when it is `.bni`-only (verified via `nm` + green native reflect tests). So no dep frag is
  ever absent in a whole-program build, and strong edges (a) always resolve, (b) **pull the
  defining object from a static archive** — the opaque-binary-distribution goal weak edges
  can NOT achieve, and (c) **fail loud** if a dependency ever emits no node, rather than
  silently dropping its satentries (a weak edge's null would be a silent RTTI hole). Strong
  edges also sidestep a native-Mach-O limitation: the Mach-O writer cannot express a
  weak-*undefined* symbol (it emits `N_EXT` strong-undefined; no `N_WEAK_REF`), so "weak"
  edges were already strong-undefined there. A dep equal to the module's own path is skipped
  (a self-edge would redefine the node). The full `builder-comp` suite links (no dangling),
  which is itself the proof that every dep frag is defined.
- **Runtime walk.** `rt.BuildSatRegistry(&main._pkg_satfrag)` becomes a graph walk. Two
  real constraints in Tier-0 `rt` (no growable collections; the current fill pre-sizes the
  hash via `countSatEntries` before allocating — `rt_satregistry.bn:98-136`): (1) a
  **visited-set** is required — not for correctness (`satInsert` is idempotent,
  `rt_satregistry.bn:77-96`, and imports are acyclic) but to avoid **exponential re-walk of
  a diamond DAG**; a simple linear-scan list of visited `_pkg_satfrag` pointers suffices
  (package count is small, so O(V²) membership is fine — no hash-set primitive needed).
  (2) sizing: do **two dedup'd walks** (count own-entries across unique fragments, then
  fill) reusing the same visited-list. Both are small, self-contained additions to `rt`.
- **Remove** the satroot-specific pieces only: the `_satentry_root` / `_satentry_root_pairs`
  emission (`emit_satroot.bn`, `common_satroot.bn`, `irdata/data_satroot.bn`), the
  driver-side `satRootEntries` gather (`main.bn:279`,`:296-300`,`:323-331` and the `test.bn`
  twin), and `mod.SatRootEntries` / `mod.SatRootRequested`. (The surrounding `main.bn`
  loop that compiles each dependency + gathers `initPkgNames`/`oFiles` **stays** — only ~6
  satroot lines are removable; net change is roughly cost-neutral, not a big simplification.)

## Out of scope (tracked separately)

The **VM/interp** satentry path (`vm.RegisterPackageSatEntries` over the interp inject-set;
"the bytecode backend lowers no native `__satentry` globals") is separate machinery and not
part of this native-codegen PoC. **But note it consumes `reflect.Package.SatEntries` — the
`_pkg_satentries` array — so leaving that array untouched (per the separate-symbol fix
above) is precisely what keeps this path correct.** The `-int`/cross-mode conformance tests
are a required no-regression axis (see Verification), NOT an ignorable path.

## Phasing (each commit green & cherry-pickable)

1. ✅ **Emit `_pkg_satfrag` for every package** (codegen + native/common) — `{ &_pkg_satentries
   (or null), ownCount, depCount, dep symrefs }`, edges from `m.ImportAliasPaths`, pinned
   from `__entry` (native) / `@llvm.used` (LLVM) on the main module. Not yet consumed —
   coexists with the existing root. Green: additive. **Landed `46f288d35`** (builder-comp
   2980/0, native aa64 + `-int` smokes, unit tests, `nm`-confirmed graph retention); dep
   edges STRONG (see edges bullet); fixes: `collectDefinedDataSyms` += `_pkg_satfrag`, a
   self-edge skip, and (review-caught) the `@llvm.used` arity assertion in `emit_satroot_test`.
2. ✅ **Convert `BuildSatRegistry` to the dedup'd graph walk** seeded from
   `main._pkg_satfrag`. **Landed `ff073777e`** — a BFS where `visited` doubles as the
   queue (one growable list, dedup by node address; the two-pass sizing folded into the
   single collect + fill). `__entry` now passes `&main._pkg_satfrag`; the flat root is
   still emitted but unconsumed. Verified behavior-equivalent (builder-comp 2980/0, native
   aa64 + `-int`, rt unit tests), plus a new transitive test
   (`1221_xpkg_iface_assert_transitive`, main→mid→leaf, all 3 modes) covering the 2-hop
   reach the walk newly depends on. Review-driven docs (rt.bni / g_satFallback /
   `ensureRuntimeDepsLoaded` force-load invariant).
3. ✅ **Remove the flat root** — `_satentry_root`/pairs, the driver gather,
   `SatRootEntries`, and rename the now-misleading `SatRoot*` → `SatRegistry*` /
   `emitSatFragPin` / `EmitSatRegistryWiring` (+ the `emit_satroot.bn`/`data_satroot.bn`
   files → `emit_satfrag_pin.bn`/`data_satregistry.bn`). **Landed `21a7d04a1`** (net
   −469 lines). Verified by strategic sampling (builder-comp 32/0, gen2 6/0, native aa64
   6/0, `-int` 3/0, unit tests 5/0) + the e2e retention test updated to assert the
   `_pkg_satfrag` graph survives dead-strip (2/2 llvm+native, 58 `__satentry` nodes) —
   a review-caught blocker (the e2e test still checked the removed `_satentry_root`).

## Verification

- **No regression (required axes):** the full conformance matrix, INCLUDING the `-int` /
  cross-mode interface-assertion tests (the reflect `_pkg_satentries` array + VM injector
  must be byte-identical — this is the guard on the shared-symbol hazard), and a
  primitive-interface assertion (`x.(Stringer)` / `x.(Hashable)`) from a program that does
  **not** source-import `lang` (guards the effective-imports edge rule).
- **The empty-waypoint rule, directly:** a conformance test where a satentry is reachable
  **only through an empty intermediate package** — pkg A imports empty pkg B imports pkg C
  whose `impl T:J` + T live in C, asserted from A. Constructibility constraint: A must gain
  no direct A→C edge, so A must not import C **and** B must not `expose` C into A (an
  `expose` folds C into A's effective imports — `compile_imports.bn:189`); J may be declared
  in B while T and the impl live in C. This guards the graph's empty-waypoint rule; it does
  **not** reproduce the opaque-blob failure (a from-source link puts every `.o` on the line,
  so archive-pull/dead-strip through a fragment chain stays asserted-but-unverified until a
  real `.a`-consuming build exists).

Bug-discovery protocol applies to anything surfaced.

## What the PoC de-risks for the symbol table — and what it does NOT

De-risks: per-package fragment emission on **both** backends, the `@llvm.used` + native-LEA
retention split, the strong dep-symref chain (archive inclusion + dead-strip), the
empty-waypoint rule, and the dedup'd runtime graph walk. Does **not** exercise (so the
symtab phase must still budget for): **function**-address relocations across all three
native backends (RTTI edges/entries are all data→data; the symtab symrefs function start
addresses — the mechanism exists, `irdata/data_funcval.bn:29`, but is unexercised here),
the **runtime address-sort** (a clean orthogonal addition — dedup-by-fragment vs
sort-by-entry-address don't interact), **nearest-start-below** lookup, and `Ok`
out-of-range handling.
