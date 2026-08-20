# Plan: decentralize the native SatEntry registry (RTTI transitive-closure PoC)

**Status:** proposed (not started). A preliminary proof-of-concept of the
decentralized dependency-graph-of-fragments approach, done *before* the stacktrace
symbolization table ([`plan-stacktraces.md`](plan-stacktraces.md)) adopts the same shape.

## Problem

The native interface-satisfaction registry (`_satentry_root`, spec §11.12 `iface.rtti`)
is built by the **main module enumerating the full transitive package set** from the
driver's `ldr.Order` (`cmd/bnc/main.bn:279-331`: `for … ldr.Order[i] … appendSatRootEntry`,
under a `satN > 0` guard), emitted only for the main module (it hangs off `__entry`;
`mod.SatRootRequested`), and consumed by `rt.BuildSatRegistry(&_satentry_root)`
(`native/common/common_satroot.bn`, `codegen/emit_satroot.bn`, `irdata/data_satroot.bn`).

This assumes the final build has **loaded — and can name — every transitive package.**
It holds for a whole-program-from-source build (everything is in `ldr.Order`), so it is
correct today. It **breaks under opaque binary distribution**: a closed-source library
shipped as `{facade.bni, bundle.a}` bundles internal packages the consumer's loader never
sees. Two failures compound:

- **Archive non-inclusion.** A static-archive member is pulled into the link only to
  resolve an *undefined symbol*. The consumer's flat root (main only) has no reference to
  the blob's internal packages, so their objects are never pulled from `bundle.a` at all.
- **Root non-reference / dead-strip.** Even if pulled, their `_pkg_satentries` are
  unreferenced by the consumer's root, so dead-strip drops them.

Result: an interface assertion on a facade-hidden implementation type — the *canonical*
use of interfaces — fails at runtime. Latent today, a hard blocker for binary package
distribution (which "package = separately compilable/linkable unit" aspires to).

## Fix — a dependency-graph of self-describing fragments

Each package emits its own `_pkg_satentries` fragment carrying (a) its OWN entries and
(b) **symrefs to its DIRECT dependencies' `_pkg_satentries`**. The runtime takes the
transitive closure by **graph-walking from the main module's fragment** (dedup via a
visited-set). The main module loses its special whole-program role — it references only
its direct imports; every deeper package is reached through the graph.

Concretely:

- **Fragment format.** Extend `_pkg_satentries` (already emitted per package by the
  descriptor, `codegen/emit_pkg_descriptor.bn:33`) to be self-describing and graph-linked:
  `{ ownCount, own-entry ptrs…, depCount, <direct-dep>._pkg_satentries symrefs… }`. The
  per-package count **moves out of the root pairs into the fragment**, so no external
  length table is needed.
- **Every package emits a fragment — even with zero own-entries.** This is the load-bearing
  change from today: the current root *skips* empty packages (`satN > 0` guard,
  `main.bn:296`). A graph cannot skip them — an empty package is a **waypoint** to
  non-empty descendants; dropping it severs reachability to any package reached only
  through it. Empty fragments still carry their dep edges.
- **Edges are per-package-local.** A package knows its direct imports at *its own* compile
  time, so it emits `<direct-import>._pkg_satentries` symrefs — undefined externals
  resolved at link, with `SetGlobal` forcing cross-object inclusion/retention exactly as
  the current root does per package (`common_satroot.bn:24-30`). No driver-side
  whole-program enumeration.
- **Retention & archive inclusion follow the dependency graph.** `__entry` references
  main's `_pkg_satentries`; main references its direct deps; transitively the whole
  reachable graph is (a) *pulled* from static archives — each fragment's undefined
  dep-symrefs pull the next package's object — and (b) *retained* against dead-strip, by
  induction on the strong-symref chain (the same primitive the current root uses, now
  chained rather than star-shaped). This is precisely what makes the opaque-blob case
  work: the blob's facade object, pulled in because main references it, has undefined
  refs to the blob's internal packages → pulls and retains them, with the consumer never
  naming them.
- **Runtime walk.** `rt.BuildSatRegistry(&main._pkg_satentries)` becomes a DFS/BFS:
  register a fragment's own entries, recurse into its dep symrefs, dedup by fragment
  address (handles diamonds; also cycles, though package imports are acyclic). O(V+E) once.
- **Remove** the main-only `_satentry_root` / `_satentry_root_pairs`, the driver-side
  `satRootEntries` gather + `ldr.Order` enumeration (`main.bn:279-331`, `test.bn` twin),
  and `mod.SatRootEntries` / `mod.SatRootRequested`. The graph replaces them — a net
  simplification of the driver.

## Out of scope (tracked separately)

The **VM/interp** satentry path is separate machinery: `vm.RegisterPackageSatEntries`
iterates the interp's *injected* package set, and "the bytecode backend lowers no native
`__satentry` globals" (`vm/lower_pkg_descriptor.bn:178`, `interp/externs.bn:336`). It has
an analogous "who enumerates the packages" question (the interp inject-set), but is NOT
part of this native-codegen PoC. Note it; do not fix it here.

## Phasing (each commit green & cherry-pickable)

1. **Fragment format** — make `_pkg_satentries` self-describing (`ownCount` prefix) and
   emit it for *every* package including empty ones (drop the `satN > 0` skip), still
   consumed by the existing root (root reads the in-fragment count instead of the pairs
   count). Green: no behavior change, just relocation of the count + empty fragments.
2. **Dep edges + graph walk** — add the direct-dep symrefs to each fragment; convert
   `BuildSatRegistry` to a dedup'd graph walk seeded from main's fragment; wire `__entry`
   to reference main's fragment for retention.
3. **Remove the flat root** — delete `_satentry_root`/pairs, the driver enumeration, and
   `SatRootEntries`/`SatRootRequested`.

## Verification

Since a true opaque-binary-blob build isn't constructible yet, verify two ways:

- **No regression:** the full conformance matrix — especially cross-package interface
  assertion / `x.(J)` / dtor-cleanup tests — stays green (the whole-program-source path).
- **The fix, directly:** a targeted conformance test where a satentry is reachable **only
  through an empty intermediate package** (pkg A imports empty pkg B imports pkg C whose
  type/impl provides the asserted `(T, J)`), asserted from A. Today's `satN > 0` skip of B
  would leave C unreachable from a naive graph; this test pins the empty-waypoint
  requirement. It should pass only with the fragment-for-every-package rule in place.

Bug-discovery protocol applies to anything surfaced.

## Why this first (PoC value)

It exercises the decentralized graph-of-fragments mechanics — per-package edge emission,
archive inclusion + dead-strip retention along the dependency graph, the empty-waypoint
rule, and the runtime dedup walk — on an **existing, well-tested** subsystem with the
conformance suite as a backstop. If it works here, the stacktrace symbolization table
reuses a proven pattern instead of pioneering it.
