# Plan: `pkg/builtins/build` — per-target compile-time metadata consts

Status: **LANDED** 2026-06-10 (binate `a3755cb4`). Adds an implementation-less
builtin package whose `.bni` carries the active target's OS / arch /
pointer-width as compile-time constants, selected per target via a
`binate-paths --target` flag. The four cross *conformance* runners were wired
to mirror `--target` onto their `binate-paths --iface` call (so 692 is green on
every mode, no xfails). Follow-up to `plan-binate-paths.md` (extends the
`binate-paths` helper). Verified before landing by a 5-dimension adversarial
workflow (verdict: LAND, no critical/major). Two tracked follow-ups remain
(see §4–§5): the parallel *unit-test* cross runners stay unwired (latent, safe
today), and the hygiene checks don't yet scan `ifaces/`+`impls/`.

---

## 1. The idea (as the user framed it)

> add a `pkg/builtins/<name>.bni` that has *const* values for target OS,
> target arch, etc. … it'll be the `.bni` file that changes — since it has
> consts — not the (nonexistent) implementation. Using consts allows
> compile-time folding, versus a readonly var declared in a `.bni` and then
> only varying the `.bn`.

So: no `.bn` implementation at all. The constants *are* the package, and the
`.bni` varies per target. Constants (not readonly vars) so the values are
compile-time — foldable. (Where the dead-branch fold actually happens — Binate
IR vs clang DCE on a constant `br` — is a later, relocatable optimization, not
part of this work.)

## 2. Decisions (with the user, 2026-06-10)

- **Package name: `build`** (`pkg/builtins/build`). Not `target`: "target" is
  the bnc *caller's* view (what you pass to `--target`); the code under
  compilation *lives in* the target, so `build.OS` reads right. (`platform`
  was the runner-up.)
- **Representation: named-type int enums** (the user's call), not bare bool
  flags. Exactly the `pkg/binate/token.bni` idiom:
  ```
  type OSType int
  const ( OS_LINUX OSType = iota; OS_DARWIN; OS_BAREMETAL )
  type ArchType int
  const ( ARCH_X64 ArchType = iota; ARCH_ARM64; ARCH_ARM32 )
  const OS   OSType   = …    // the per-target lines
  const Arch ArchType = …
  const PtrSize int = …      // 8 (LP64) or 4 (ILP32)
  const IntSize int = …
  ```
  No string type in Binate, so OS/arch can't be string consts — named-type
  enums give type-safe switching (`if build.OS == build.OS_LINUX`).
  BUILDER-safe: `token.bni` uses this exact shape and is in bnc's BUILDER cone.
- **Selection: physical per-target `.bni`, one tree per target key.** The
  loader resolves one `.bni` per package (first match on the iface path wins —
  shadow, not merge), so each target tree carries the *whole* self-contained
  `build.bni` (type defs included; ~6 small near-identical files, only the four
  `OS`/`Arch`/`PtrSize`/`IntSize` lines vary). Trees live at
  `ifaces/targets/<key>/pkg/builtins/build.bni`.
- **`binate-paths --target <key>`** prepends `<base>/ifaces/targets/<key>` to
  the `--iface` output (no effect on `-L`/`--runtime` — `build` has no impl).
  Pass the SAME key you pass to `bnc --target` so `build.bni` matches codegen.
- **Host (default / no `--target`): uname-detect.** `binate-paths` with no
  `--target` (or `--target host`) runs `uname -s`/`-m` and maps to the matching
  tree; unrecognised host yields no tree (a `build` import then fails clearly,
  rather than resolving the wrong layout). Stays physical, no generation.

## 3. What landed in the core commit

- `ifaces/targets/<key>/pkg/builtins/build.bni` for the six keys:
  `x86_64-linux`, `x86_64-darwin`, `aarch64-linux`, `aarch64-darwin`,
  `arm32-linux`, `arm32-baremetal` (LP64 → 8/8; arm32 → 4/4).
  `aarch64-darwin` is host-only (no bnc `--target` key for it; it's the
  Apple-Silicon default host).
- `scripts/binate-paths.sh`: `--target KEY` flag + uname host-detect; prepends
  the target tree to `--iface`; explicit bad key is a hard error.
- `conformance/692_build_target_consts.bn` (+`.expected`): pins (a) the layout
  consts agree with the compiler's own `sizeof` (so a build.bni of the matching
  *width class* was selected — this catches the real wiring bug, a host 8/8 tree
  used for a 4/4 arm32 cross) and (b) the enums are distinct/type-safe and
  OS/Arch hold a valid member. Output is target-independent (no OS/arch named)
  so one `.expected` holds everywhere. Verified green in `builder-comp`,
  `builder-comp-int`, `builder-comp-comp`, and `native_x64_darwin` (Rosetta);
  arm32 compile verified clean locally (run is CI's, QEMU).
- The four cross *conformance* runners (`builder-comp_arm32_linux`,
  `builder-comp_arm32_baremetal`, `builder-comp_native_x64-…`,
  `builder-comp_native_x64_darwin-…`) now mirror their bnc `--target` onto the
  `binate-paths --iface` call, so a cross compile selects the matching tree.
  This un-xfails 692 on the two arm32 modes (the xfails are gone).

## 4. Tracked follow-up — the parallel unit-test cross runners stay unwired

The three `scripts/unittest/runners/` cross runners (`arm32_linux`,
`arm32_baremetal`, `native_x64_darwin`) have the same shape — they pass
`--target X` to **bnc** but *not* to their `binate-paths --iface` call — and
were **not** wired by this change. It's a latent incompleteness, not an active
defect: a repo-wide grep shows the only importer of `pkg/builtins/build` is
`conformance/692`, and `build` has no `_test`, so no unit-test compile selects a
tree today. The moment a package under unit test specializes on `build` (or
`build` gets a test), those runs would silently resolve the host-width tree.
Wiring runners is the user's scope call, so it's documented here + in
claude-todo rather than done. Fix when needed: add the matching `--target` to
the three unit-test cross runners' `binate-paths --iface` calls.

## 5. Possible future work

- **Hygiene scan coverage** (user-requested, 2026-06-10): `line-length.sh`,
  `file-length.sh`, and `bni-doc.sh` scan only `$BINATE_DIR/pkg`+`cmd`, so the
  new `ifaces/targets/**/build.bni` (and `ifaces/`+`impls/` generally) aren't
  linted by them (`file-format.sh` does cover them). Extend the find-roots to
  also scan `ifaces/` and `impls/`. Tracked in claude-todo.
- Stronger 692 coverage: a same-width wrong-tree swap (right size, wrong
  OS/Arch) isn't distinguishable from a target-independent `.expected`; pinning
  the exact OS/Arch would need per-mode `.expected` files
  (`692….expected.<mode>`). Unreachable in practice (explicit-key selection),
  so low priority.
- More consts as needs arise (endianness, float-ABI, page size, …).
- Dead-branch-fold optimization for `if build.<const> { … }` at the Binate IR
  level (currently a constant `br` left for clang DCE; fine for both-valid
  branches, matters only if a dead branch holds target-incompatible code).
- A generator/hygiene check that the invariant part of the six `build.bni`
  files stays byte-identical (today they're hand-maintained).
