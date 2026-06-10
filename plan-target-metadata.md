# Plan: `pkg/builtins/build` — per-target compile-time metadata consts

Status: **Core LANDED-READY** 2026-06-10 (in worktree, not yet cherry-picked).
Adds an implementation-less builtin package whose `.bni` carries the active
target's OS / arch / pointer-width as compile-time constants, selected per
target via a `binate-paths --target` flag. Follow-up to `plan-binate-paths.md`
(this extends the `binate-paths` helper). One integration piece is
deliberately deferred to the user (cross-runner wiring — see §4).

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
  consts agree with the compiler's own `sizeof` (i.e. the right tree was
  selected) and (b) the enums are distinct, type-safe, in range. Output is
  target-independent (no OS/arch named) so one `.expected` holds everywhere.
  Verified green in `builder-comp`, `builder-comp-int`, `builder-comp-comp`.

## 4. Deferred to the user — cross-runner wiring (NOT done unilaterally)

The conformance cross runners pass `--target X` to **bnc** but not to their
`binate-paths --iface` call, so on a cross target `import "pkg/builtins/build"`
resolves to the *host* tree. For the 64-bit native runners (`x86_64-linux`,
`x86_64-darwin`) the host's 8/8 happens to match, so 692 passes there. For the
two ILP32 runners (4/4) it mismatches the host's 8/8, so 692 is **xfail**'d on
`builder-comp_arm32_linux` and `builder-comp_arm32_baremetal` (with a note +
todo). The fix is one token per runner — mirror the bnc `--target` onto the
`binate-paths --iface` call in the four cross runners
(`builder-comp_arm32_linux`, `builder-comp_arm32_baremetal`,
`builder-comp_native_x64-…`, `builder-comp_native_x64_darwin-…`) — but wiring
runners is the user's scope call (CLAUDE.md "Stay Within the Asked Scope"), so
it's proposed, not done. Doing it un-xfails the two arm32 modes.

## 5. Possible future work (not scoped)

- More consts as needs arise (endianness, float-ABI, page size, …).
- Dead-branch-fold optimization for `if build.<const> { … }` at the Binate IR
  level (currently a constant `br` left for clang DCE; fine for both-valid
  branches, matters only if a dead branch holds target-incompatible code).
- A generator/hygiene check that the invariant part of the six `build.bni`
  files stays byte-identical (today they're hand-maintained).
