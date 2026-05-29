# pkg/ Directory Layout — Spec

Defines the tiered organization of packages, in-`pkg/`-tree naming,
the parallel-tree split (`ifaces/` / `impls/`) for bundle-selectable
tiers, and the collocation rule for project-local packages.

Supersedes the "Package directory organization and conventions" TODO
entry. Companion: [`pkg-layout-plan.md`](pkg-layout-plan.md) for the
migration sequence.

## Scope

**Covers**

- Five tiers (0, 0b, 1, 1x, 2) with explicit semantics; tier 3 as a
  named-but-unstructured outlier.
- In-tree naming under `pkg/`.
- Parallel trees: `ifaces/{core,stdlib}/` and
  `impls/{core,stdlib}/{common,libc,baremetal,...}/`.
- Collocated layout for tiers 2 and 3.
- How an external package manager plugs in.

**Does not cover**

- The package manager's manifest format, dependency-resolution model,
  or registry/distribution mechanism — separate spec, TBD.
- Per-file selection within a package (today: whole-package only;
  symlinks workaround "common + per-variant file" cases).
- Status — most of the structure described here is aspirational.

## Tiers

| Tier | Role | Bundling | Examples |
|---|---|---|---|
| **0** | Runtime essentials for normal programs | Always shipped with toolchain | `pkg/builtins/lang`, `pkg/builtins/rt`, future `pkg/builtins/reflect` |
| **0b** | Runtime essentials for non-standard execution modes | Always shipped with toolchain | `pkg/builtins/testing`; future profiling, tracing |
| **1** | Standards library — strict-compat, versioned with the core | Bundled by default; user-opt-out | `pkg/std/io`, `pkg/std/os`, `pkg/std/containers/vector` |
| **1x** | Standards-track — no inter-version compat | Bundled by default; user-opt-out | `pkg/stdx/slices`, future experimental APIs |
| **2** | Public packages others may depend on | Project-pulled via the package manager | `pkg/binate/parser`, `pkg/binate/vm`, `pkg/binate/interp` (embeddable interpreter) |
| **3** | App-specific | Not bundled | Free placement; may sit outside `pkg/` |

Tiers 0 and 0b are layout-identical (both under `pkg/builtins/`); the
0/0b distinction is about *activation* — 0b packages are only relevant
in particular execution modes (`--test`, `--profile`, …). Each entry's
manifest records its own activation story.

Tiers 2 and 3 differ in *intent*: tier 2 declares "external consumers
may depend on this"; tier 3 declares "this is internal to one app."
The loader doesn't enforce the distinction; convention guides placement
and the API-stability contract the author chooses to offer.

**Transitive constraint**: tier 2's dependency closure must also be
tier 2. If a package is intended as publicly consumable, everything it
imports transitively must also be publicly consumable. (Tier 0 and 1
imports are fine — they're toolchain-bundled.)

## In-`pkg/`-tree naming

```
pkg/
  builtins/          tier 0 + 0b — always-bundled runtime essentials
    lang/            language-defined interfaces + canonical
                       primitive impls (`Stringer`, etc., plus the
                       only legal `int` / `bool` / … method impls)
    rt/              runtime: allocator, refcount, slice ops, …
    testing/         the testing framework (only via --test mode)
    reflect/         (future) reflection / type assertions
    ...
  std/               tier 1 — standards library, strict-compat
    io/
    os/
    containers/vector/
    ...
  stdx/              tier 1x — standards-track, no inter-version compat
    slices/
    ...
  <org>/             tier 2 — public packages from one repo or org
    <X>/             e.g., pkg/binate/parser/, pkg/binate/vm/
  <X>/               tier 2 — well-known/standalone packages may
                       claim a bare pkg/X slot; convention only,
                       not enforced
```

Tier 3 packages may live anywhere — under `pkg/binate/<internal>/X`,
under `cmd/<app>/<sub>`, or at the repo root as `<X>/`. Placement is
the package author's call.

### Namespace contention

The shared `pkg/<org>/` and `pkg/<X>/` namespace is informal — the
loader doesn't enforce uniqueness or arbitrate authority. Two projects
that both call something `pkg/json` don't invalidate each other; the
collision matters only when a single consumer tries to import both
transitively (made worse by transitive deps). Convention: pick names
with the awareness that downstream consumers exist.

For bare `pkg/<X>` slots (the well-known case), expect harder pressure
on uniqueness; for nested `pkg/<org>/<X>`, the org segment usually
keeps collisions out.

## Parallel trees: `ifaces/` and `impls/`

For tiers 0, 0b, 1, and 1x, `.bni` and `.bn` files live in separate
top-level trees organized to support selectable bundling:

```
ifaces/
  core/                      tier 0 + 0b interfaces
    pkg/builtins/...
  stdlib/                    tier 1 + 1x interfaces
    pkg/std/...
    pkg/stdx/...

impls/
  core/                      tier 0 + 0b implementations
    common/                  ... platform-independent
      pkg/builtins/lang/...
    libc/                    ... libc-using
      pkg/builtins/rt/...
    baremetal/               ... bare-metal
      pkg/builtins/rt/...
  stdlib/                    tier 1 + 1x implementations
    common/
      pkg/std/containers/vector/...
    libc/
      pkg/std/os/...
    baremetal/
      pkg/std/os/...
```

### Invariants

1. **`ifaces/` is implementation-independent.** One interface tree
   regardless of which `impls/` variant is selected. The interface
   files never change shape based on platform.
2. **`ifaces/` is tier-organized; `impls/` is tier-then-platform.**
   The `core` / `stdlib` split exists in both trees; the
   `common` / `libc` / `baremetal` axis is impl-side only.
3. **`common/` is always a valid platform.** Packages with no
   environment-dependent variants live entirely under `common/`.
4. **Tier 1x ships with tier 1.** Both `pkg/std/...` and `pkg/stdx/...`
   sit under the `stdlib` root, so they bundle and select as one unit.
   The stability difference is per-package, not per-tree.
5. **Whole-package selection only.** A given package's files come
   from one impl variant (`common`, `libc`, …). Mixing — "shared
   core code + per-platform file in the same package" — needs the
   symlink workaround until per-file selection is designed.

### Search-root configuration

The toolchain consumes the trees via `-I` (interfaces) and `-L`
(implementations). One `-L` per included {tier, platform} pair. A
typical "tier 0 + tier 1, libc target" configuration:

```
-I ifaces/core/
-I ifaces/stdlib/                 # if stdlib included
-L impls/core/common/
-L impls/core/libc/               # for libc target
-L impls/stdlib/common/           # if stdlib included
-L impls/stdlib/libc/             # if stdlib included
```

`common/` is always included alongside the chosen platform variant.

## Tier 2 / 3 collocation

Tier 2 and 3 packages collocate `.bni` and `.bn` under one tree:

```
pkg/binate/
  parser.bni
  parser/
    parser.bn
    parse_expr.bn
    parser_test.bn
    ...
  vm.bni
  vm/
    ...
```

No `ifaces/` / `impls/` split. There's no bundling-selection axis at
this tier — what's in the project is in the project. The same loader
that searches `ifaces/` + `impls/` also searches `pkg/`; mangling,
import-path syntax, and file-format conventions are identical.

## Package manager interaction (sketch)

The package manager is an **external** tool, not part of the toolchain
(in contrast to Go). Per-project flow (full spec TBD in a separate
document):

- The project declares dependencies in a `DEPS`-style manifest.
- Running the package manager fetches them into a `deps/` subdir
  under the project.
- The manager generates the right `-I` / `-L` arguments so the
  toolchain finds dependency packages.

This preserves the property that the toolchain is distribution-
agnostic: it never asks "where did this come from"; it just searches
the configured paths.

Tier 0/1 (bundled with toolchain) and tier 2 (project-pulled) flow
through the same loader from two distinct source streams.

## Mangling

Symbol mangling already uses the full package path (`/` and `.`
become `__`): `pkg/binate/asm.New` mangles to
`bn_pkg__binate__asm__New`. Deeper paths produce longer mangled
names; no collision risk, no scheme change.

The mangler logic itself doesn't need to change for any of the moves
this spec implies. Hard-coded mangled-name strings in
`runtime/binate_runtime.c`, native backends, and runtime manifest
tables update mechanically when packages move.
