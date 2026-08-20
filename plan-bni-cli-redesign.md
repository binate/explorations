# Plan: redesign bni's command-line interface (package-oriented, no positional source files)

Status: **DONE** — implemented and landed on `main` (`1b75e5a8e`), with all
in-repo callers updated atomically. `examples/` (separate repo) migrates at its
next BUILDER bump, tracked in `examples/TODO.md`. This was a BREAKING change to
bni's user-facing CLI.

## Motivation

bni's current grammar takes **positional source files** and uses `--` to mean
"end of filenames, start of the program's argv". That is a real vulnerability:
`bni * -- args` with a file literally named `--` in the glob mis-splits
filenames from program args. It also conflates three different "targets"
(a main package for run/repl; package paths for test) through one positional
list, supports flag/filename interspersing, and still carries dead `--root` /
`--add-root` flags (only their tests survive, asserting they're dead).

Binate's model is already package-oriented: a `main` is a **package**
(a directory of `.bn` files), per `docs/guide.md` and `examples/README.md`
(`cmd/<sub>/` = one runnable `package "main"`). A tree-wide survey confirms the
redesign is safe:

- **0** invocations anywhere pass more than one source file (15 single-file,
  3 directory). So multi-file main (`-main-files`) is YAGNI.
- `--` is always `path -- args` — never bare, never a glob.
- Shebangs append the script as the last token: `bni -x <script> <args>`.

## Ratified CLI

Exactly one **target** flag is required (mutually exclusive):

| Flag | Meaning |
|---|---|
| `-main-dir <dir>` | run the `main` package in `<dir>` |
| `-main-file <file>` | run the `main` package = the single file `<file>` (replaces `-x`) |
| `-test <pkg>` | run `Test*` in `<pkg>`; **repeatable** (`-test a -test b`) |

Modifier:

- `-repl` — valid only with `-main-dir`/`-main-file` (NOT `-test`): load the
  target, then start a REPL instead of running `main()`. (Decision (a): `-repl`
  pairs with a `-main-*` target rather than being a standalone mode.)

Behavior flags (unchanged): `-I`/`--interface-path` and `-L`/`--impl-path`
(colon-split, repeatable), `-v`/`--verbose`, `--check-nil`, `--run <substr>`,
`--skip <substr>`, `--version`.

**Positionals are ONLY the interpreted program's argv (progargs).** Because the
main package is named by a flag, there are no positional source files — so
`bni <glob> -- args` is not a grammar and the "file named `--`" injection vector
is gone. `--` keeps only its standard meaning ("end of bni's flags"), needed to
pass a progarg that looks like a flag. Progargs are valid only in run mode
(`-main-dir`/`-main-file`, no `-repl`); in repl/test mode a positional is an
error.

Validation: exactly one of {`-main-dir`, `-main-file`, `-test` non-empty}, else
an error; `-repl` with `-test` or without a `-main-*` target is an error;
positionals in repl/test mode are an error.

Deleted: `-x` (→ `-main-file`), `--root`, `--add-root` (+ their tests),
positional source files, the `--`-splits-filenames rule, `-main-files`.

## Shebangs (the load-bearing constraint)

`-main-file` subsumes `-x`. `#!/usr/bin/env -S bni … -main-file` makes the
kernel run `bni … -main-file <script> <args>`, so the kernel-appended script
*becomes `-main-file`'s value* and the rest are progargs — provided
`-main-file` is the **last** flag on the line. So:

- Direct shebang `#!/usr/bin/env -S bni -x` → `#!/usr/bin/env -S bni -main-file`.
- `bnrun` wrapper `bni -x -I … -L … "$@"` → `bni -I … -L … -main-file "$@"`
  (`$@` = `<script> <args>`).

## Old → new for each real pattern

- run single file: `bni -I … -L … f.bn -- a b` → `bni -I … -L … -main-file f.bn -- a b` (or `… -main-file f.bn a b` when a/b don't look like flags).
- run directory: `bni -I … -L … dir -- args` → `bni -I … -L … -main-dir dir -- args`.
- `-int-int` nesting: `bni <cmd/bni-dir> -- <inner argv>` → `bni -main-dir <cmd/bni-dir> -- <inner argv rewritten to the new CLI>` (the inner cmd/bni is the same new binary, so its argv uses `-main-file`/`-test`/…).
- shebang script: see above.
- test: `bni --test -I … -L … pkg` → `bni --test pkg -I … -L …` (`--test` now takes the package as its value).
- repl: `bni --repl -I … -L … fixture.bn` → `bni --repl -main-file fixture.bn -I … -L …` (or `-main-dir` for a dir fixture).

## Rollout

Transition (bni accepting both CLIs) is **infeasible**: the in-repo runners
build bni from `cmd/bni` and use it immediately, and supporting positional
source files at all keeps the injection-prone path alive. So: **atomic in-repo
change** — `cmd/bni` + every in-repo call site land together; the `examples/`
repo (which pins its own released bni) updates at its next BUILDER bump.

In-repo sites to update atomically with `cmd/bni`:
- conformance/runners: `builder-comp-int.sh`, `builder-comp-comp-int.sh`, `builder-comp-int-int.sh`, `builder-comp_arm32_linux_int.sh`.
- perf/runners: `builder-comp-int.sh`, `builder-comp-comp-int.sh`, `builder-comp-int-int.sh`.
- scripts/unittest/runners: `builder-comp-int.sh`, `builder-comp-comp-int.sh`, `builder-comp-int-int.sh`.
- e2e: `os-args.sh`, `fmt-os-args.sh`, `os-env.sh`, `env-paths.sh`, `bni-nil-check.sh`, `repl.sh`, `bni-test-fault.sh`, `bni-test-nil.sh`, `shebang-exec.sh`.
- docs: `BUNDLE-HOWTO.md`, `docs/guide.md`, `docs/overview.md`, `docs/spec/17-…` (script-mode text), the `cmd/bni` usage string; the `190_shebang.bn` fixture + `cmd/bnfmt/main_test.bn`'s shebang-string constant (both use `bni -x` illustratively — update to `-main-file`).

Deferred to the examples/ BUILDER bump: `examples/scripts/run-interpreted.sh`,
`examples/scripts/run-tests-interpreted.sh`, `examples/scripting/bin/bnrun`,
`examples/scripting/cmd/greet/main.bn` shebang, examples READMEs.

Verification surface (must stay green): every `-int` / `-int-int` conformance,
unittest, and perf mode, plus the bni e2e tests. Given the size, likely one
coordinated commit (cmd/bni + runners + e2e + docs) since the runners break the
instant cmd/bni changes.

## Also fix (separately, small): landed cruft

- bnlint's `TestParseArgsRootIsRejected` (landed `1b3215ba3`) pins a dead
  `--root` — under strict parsing any unknown flag errors, so a `--root`-specific
  test is redundant; delete it rather than keep it.
- bnfmt's `progArgs` comment (landed) still names `buf.CopyStr` (dropped) — stale.
