# Plan: `pkg/std/os` Stat / File.Stat / FileInfo (staged)

Goal: `os.Stat(path)` and `File.Stat()` returning a `FileInfo`, patterned after
Go (Name/Size/Mode/ModTime/IsDir), plus the `FileMode` type bits Go carries.

The hard part is reading the platform `struct stat`, whose memory layout
diverges across every target (OS × arch; Darwin's 16-bit `st_mode`; arm32's
flavored variants; struct-vs-stat64). This plan **stages the work so that hard
question is reached last, isolated, and reversible** — and makes real progress
on everything else first.

## The structural key: a fixed-struct boundary

Define one internal boundary that hides the platform layout:

```
// the fixed, Binate-defined result — raw st_mode (the S_IF* bits are
// identical on every platform, so the st_mode -> FileMode map is shared).
struct statbuf { size int64; stMode uint32; mtimeSec int64; mtimeNsec int32 }

internal.statFd(fd int, out *statbuf) int        // 0 / -1 (errno set)
internal.statPath(path *[]readonly char, out *statbuf) int
```

Everything above this boundary — `os.Stat`/`File.Stat`, `FileInfo`, the
`st_mode -> FileMode` mapping, `failErrno` — is written **once, mechanism-
agnostic**. Only the boundary's *implementation* is the A-vs-B question (§
Mechanism). This isolates the dilemma to one small last piece and makes the
choice **reversible**: ship one mechanism, swap to the other by editing only
`internal.statFd`/`statPath`, touching nothing above.

## Staging (dependency order)

| Stage | What | Depends on | Hard questions |
|---|---|---|---|
| **1. `pkg/std/time` (bare-bones)** | a `Time` value type (Unix sec + nsec) + ctor + accessors + comparisons. NO clock read (`Now()` is a separate syscall — deferred), no formatting/zones/Duration. | — | none (pure value type) — **design under discussion** |
| **2. `FileMode` extension** | Go's file-type bits (Dir/Symlink/Device/NamedPipe/Socket/CharDevice [+ setuid/gid/sticky?]) + `IsDir`/`IsRegular`/`Perm`/`Type` (+ `String()`?). | — | none (pure bit logic) |
| **3. `FileInfo`** | struct + `Name`/`Size`/`Mode`/`ModTime`/`IsDir`. | 1, 2 | none (testable with synthetic data) |
| **4. boundary + `os.Stat`/`File.Stat`** | the `statbuf` boundary signature, the shared `st_mode -> FileMode` map, the entry points wiring it to `FileInfo`. | 3 | none above the boundary |
| **5. implement the boundary** | A or B (below) + an `e2e/stat-values.sh` C cross-check. | 4 | **the A-vs-B decision lives only here** |

Stages 1–4 also dispose of the *other* hard questions: Stage 1 settles "how is
ModTime represented" (a `time.Time`) and sidesteps the clock-reading syscall.

## The per-OS mechanism context (changed since the errno work)

The errno work used a per-target directory tree (`impls/targets/`). That has
since been **replaced by file-level build constraints** — `#[build(is(os,
"darwin"))]`, `#[build(is(arch, "aarch64") && is(os, "darwin"))]`, `!is(...)`,
`&&` — see `plan-build-constraints.md` and `plan-impls-constraints-migration.md`.
`os` now lives in one directory (`impls/stdlib/common/pkg/std/os/`) with
per-OS files gated by tags (`internal_darwin.bn` / `internal_linux.bn`,
`os_baremetal.bn`); per-OS *values* (open flags, the errno table) still use
`build.OS` const-folding in shared files. This is the mechanism Stage 5 builds
on; it is cleaner than `impls/targets` and removes the path machinery.

## Mechanism — A vs B (decided at Stage 5)

- **A — per-OS/arch Binate.** `statFd`/`statPath` call fstat/stat into a raw
  buffer and read `st_size`/`st_mode`/`st_mtim` at hand-encoded offsets, in
  files gated by `#[build(is(os, …) && is(arch, …))]`. Offsets are hand-written
  per target and **C-verified** (like the errno values).
- **B — C shim.** A tiny `bn_os_fstat(fd, &out)` / `bn_os_stat(path, &out)` in
  `binate_runtime.c` that `#include <sys/stat.h>`, calls the syscall, and copies
  the four fields into `statbuf`; Binate never encodes the platform layout.

**Analysis.** B is correctness-now-simplest (the system C compiler owns the
layout). But **A is where C-free actually leads**: direct syscalls (or
own-assembler stubs) still return a `struct stat` buffer that *something in
Binate* must decode, so the layout has to live in Binate eventually regardless —
B doesn't avoid that, it relocates it into `binate_runtime.c`, the file we want
to shrink and eventually delete, not grow. The constraints rework makes A's
*file organization* clean (gated per-(os,arch) files, no path tricks); A's
residual cost is the hand-encoded offset constants, mitigated by the same e2e
C cross-check the errno table uses. The boundary lets us not pre-commit: start
at B and migrate to A when direct-syscall infra exists, at the cost of one file.

Open ABI check for *both*: `__c_call` passing a pointer-to-Binate-struct
out-param (`&out`) — confirm against `plan-c-call.md` before Stage 5 lands.

## Deferred / open questions

- **Mechanism A vs B** — decided at Stage 5 (lean A, per above; reversible).
- **`ModTime` representation** — `time.Time` (Stage 1); shape of that type is
  the Stage-1 discussion.
- **`FileInfo`: concrete struct vs interface.** `os` *can* use interfaces (it
  already does, `impl *File : io.Reader, …`), so this is a simplicity call, not
  forced. Leaning concrete struct for the first cut.
- **`FileMode` type-bit set** — Go's full set vs just the producible file-type
  bits.
- **`FileMode.String()`** — include or defer (pure int/char work).
- **`Lstat`** (doesn't follow symlinks; needed to *detect* `ModeSymlink`) —
  this cut or a follow-up.
- **`Sys()`** — omit from the first cut (would leak a per-target ABI detail).
- **`File.name` field** — add to the `File` struct (populated by Open/OpenFile/
  Create) so `File.Stat().Name()` works, as Go's `os.File` does. Confirm.

## Verification

The per-OS ABI facts are **adversarially C-verified**, exactly as the errno
values are (`e2e/errno-values.sh`): an `e2e/stat-values.sh` compiles a C program
that fstats a known file and prints size/mode/mtime, and diffs against what
Binate's `Stat` returns on each OS (Linux CI covers the Linux arches; a mac
covers Darwin). Option A's e2e additionally checks `offsetof`/`sizeof`.

## Status

- Stage 1 (`time`) — **design under discussion** (next).
- Stages 2–4 — pending.
- Stage 5 (mechanism) — **deferred**; lean A; reversible via the boundary.
