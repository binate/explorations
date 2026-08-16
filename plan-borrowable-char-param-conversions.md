# Plan: convert read-only `@[]char` params to `*[]readonly char` (borrow-not-copy)

> **Status: NOT STARTED (next task).** Driven by the new `borrowable-char-param`
> bnlint rule. Goal: convert every parameter the rule flags to `*[]readonly char`
> so that after the next `CHECK_TOOLS` bump (which activates the rule in the
> bundled bnlint the hygiene lint runs) `scripts/hygiene/lint.sh` stays GREEN
> (no `borrowable-char-param` diagnostics) — insofar as possible.

This doc is written to survive a context compaction; it is self-contained.

## Background / why

A `@[]char` (managed char slice) parameter that is only READ forces a MORTAL
heap copy when the caller passes a **string literal** (the literal's immortal
rodata is copied into a fresh managed allocation to satisfy managed-slice
ownership). If a value derived from that param is then held as a raw
`*[]readonly char` borrow past the statement, the copy is freed and the borrow
dangles — a use-after-free. This exact class was fixed in `cmd/bnc`
(`unquote`/`shortName`/`stripExt`, main `bf8a91a7c`) and `loader.unquote`
(main `f09a89bb3`). The `borrowable-char-param` rule flags every remaining
read-only `@[]char` param; converting them to `*[]readonly char` removes the
copy (a literal arg then aliases immortal rodata) and the footgun.

## The rule (already implemented)

`borrowable-char-param` — `pkg/binate/lint/borrowable_char_param.bn` (+ `_util.bn`
predicates + `_return.bn` return-conflict analysis; tests in the three
`*_test.bn`; registered in `lint.bn`'s `LintFile`). Committed on the `temp-5`
worktree as `e4a5469ed`; **landing to main via cherry-pick is in progress** —
re-read the landed hash on `main` (`git -C ~/binate/binate log --oneline | grep borrowable-char-param`).

It flags a param declared exactly `@[]char` whose EVERY use is a read-only
borrow (index read, `len`, sub-slice, `range`, `return p`/`return p[lo:hi]`, or
passed to a `*[]readonly char` callee param). It stays silent (conservative,
no known false positives) if the param is ever mutated, stored/escaped as
owning, passed to a `@[]char`/unresolvable callee, or captured by a closure.
A view-returned param is SUPPRESSED when the function also returns an owned
`@[]char` on another path (make/make_slice/call/composite, or a local bound to
one) — the `shortTypeName`/`LookupVtableSlotName`/`qualifyForPkgPath`-`name`
class, which must STAY `@[]char` (do NOT convert them).

## How to regenerate the exact work list (it drifts as code lands)

```
cd <a binate worktree, e.g. ~/binate/temp-binate-5>
./scripts/hygiene/lint.sh --from-source 2>&1 | grep borrowable-char-param
```
Each line is `file:line:col: [borrowable-char-param] parameter <name> is a read-only @[]char; …`.
(`--from-source` builds bnlint from the current tree so the rule is present even
before the `CHECK_TOOLS` bump.)

**Snapshot at rule-landing time: 172 flags.** Two flavors (distinguishable by the
message suffix):
- **113 "read-only consumer"** — message has NO `(the return type would also
  become *[]readonly char)` suffix. The function READS the param and returns a
  fresh/other value. **CLEAN conversion: change ONLY the param type**
  `@[]char` → `*[]readonly char`. Callers pass their `@[]char` arg as an implicit
  managed→raw borrow — **no caller change needed**. (e.g. the `*Msg`/`*Err`
  builders in lint/types/buildcfg, mangle helpers, etc.)
- **59 "view helper"** — message HAS the `(the return type would also become
  *[]readonly char)` suffix. The function RETURNS a sub-slice view of the param.
  **Change param AND return type** to `*[]readonly char`; then callers that need
  to OWN the result must wrap it in `buf.CopyStr(...)`. These RIPPLE to callers.

Per-package counts at snapshot: ir 34, types 15, lint 11, vm 10, codegen 10,
native/x64 8, stdx/containers/setfn 7, stdx/containers/mapfn 7, loader 5, repl 3,
parser 3, native/arm32 3, native/aarch64 3, mangle 3, std/strconv 3, + ~40
packages with 1–2 each (native/common, buildcfg, stdx/fmt, os/process, bnlint,
and one-offs across asm/*, stdx/*, std/*, builtins/*, cmd/{bnc,bni,bnas,bnfmt}).

## Conversion pattern (per param)

1. **Change the param type** `@[]char` → `*[]readonly char` in the signature.
2. **If a VIEW helper** (return suffix present): change the RETURN type to
   `*[]readonly char` too. Sub-slicing/returning a `*[]readonly char` yields
   `*[]readonly char` — bodies usually need no change.
3. **Fix callers** (only needed for the view-helper flavor, and for any caller
   that BINDS a now-borrow result as owning `@[]char`):
   - `var x @[]char = view(arg)` → `var x @[]char = buf.CopyStr(view(arg))`.
   - `field = view(arg)` (field is `@[]char`) → `buf.CopyStr(...)`.
   - `slices.Append[@[]char](coll, view(arg))` → `buf.CopyStr(...)`.
   - `f(buf.CopyStr(view(X)))` where the inner CopyStr was on the ARG → move it
     to the RESULT: `buf.CopyStr(f(X))` (cleaner, copies less).
   - **In-statement consumers taking `@[]char`** (e.g. an `*Msg` builder): keep
     the INTERMEDIATE binding owning (`buf.CopyStr` once at the binding) so the
     downstream `@[]char` consumers see an owned value — avoids per-consumer
     copies. A direct in-statement `@[]char`-consumer of a view result gets one
     `buf.CopyStr` around the arg.
   - Add `import "pkg/binate/buf"` if the caller file lacks it.
4. **Fix tests** that bound the result as `@[]char` when it's now a borrow: retype
   to `*[]readonly char` where they only read it, or `buf.CopyStr(...)` if owned.
   (Tests binding a view result to `*[]readonly char` become correct unchanged.)

The reference conversions to mirror exactly: cmd/bnc `bf8a91a7c` and loader
`f09a89bb3` (both in the done log, `explorations/claude-todo-done.md`
"String-literal `@[]char`-view use-after-free").

## Do NOT convert (leave `@[]char`)

- The rule already excludes them (won't be in the list): `shortTypeName`
  (`ir/gen_impl_recvname.bn`), `LookupVtableSlotName` (`ir/gen_iv_thunk.bn`),
  `qualifyForPkgPath`'s `name` param (`ir/gen.bn`) — mixed view+owned-alloc
  returns; `*[]readonly char` would break the alloc-return path.
- Already fixed (won't be in the list): cmd/bnc `unquote`/`shortName`/`stripExt`,
  `loader.unquote`.

## Gotchas / discipline

- **Never introduce a UAF.** The whole point is borrow-not-copy WITHOUT
  dangling. For every view-helper conversion, ensure each caller that outlives
  the statement OWNS the result (`buf.CopyStr`). Get a minimal adversarial review
  (memory-safety + completeness) before landing, like the cmd/bnc/loader fixes.
- **BUILDER-compat.** Many flagged params are in `cmd/bnc`'s BUILDER-compiled
  tree (`pkg/binate/{ast,ir,types,mangle,codegen,native,…}`). `*[]readonly char`
  + `buf.CopyStr` are basic and BUILDER-safe, but VERIFY gen1 still builds
  (`conformance/run.sh builder-comp <a test>` or a unit build).
- **Pessimization is accepted** (user's explicit call): view-helper conversions
  trade a cheap view-sharing RefInc for a fresh `buf.CopyStr` allocation at
  owning callers. The 113 read-only-consumer conversions are clean wins (no
  copy added). Do them all anyway — the goal is lint-clean.
- **"Insofar as possible."** If a specific site resists a clean conversion (a
  consumer that genuinely needs `@[]char`, an awkward ripple), STOP and surface
  it — it may indicate a rule over-warn (→ refine the rule) or a legitimately-
  hard case (→ a rule suppression or a `test-coverage`-style baseline). Don't
  force a broken conversion just to silence the lint.
- **Shared checkout / concurrent workers.** `explorations` is shared — edit →
  commit → push immediately, stage only the specific file, never the untracked
  `probe/`. The `binate` `main` moves constantly — resync the worktree and
  REGENERATE the list before each batch. `binate` worktree = `temp-5` on branch
  `temp-5` (session-assigned); do not create new worktrees.
- **Landing.** Per-instance approval for every cherry-pick to `main` (write the
  verbatim approval quote first). Land THROUGH local `main` (`~/binate/binate`),
  never push origin from the worktree. Rebase → hygiene (read the passed line) →
  base-check (separate command) → re-read branch HEAD → cherry-pick → push →
  resync. Batch sensibly (e.g. a few packages per commit) but each cherry-pick
  still needs its own explicit approval.

## Suggested execution order

Package by package (or a careful workflow), smallest/leaf-most first to limit
ripple, verifying each package's unit tests + a gen1 smoke as you go:
1. The many 1–2-flag leaf packages + the 113 clean read-only-consumers (low risk,
   no caller ripple) — can be batched aggressively.
2. The view-helper flavor per package (needs caller `buf.CopyStr` + tests).
3. Heavy packages last (ir 34, types 15) — most ripple; do carefully.
Re-run the regeneration command after each batch; the remaining count should
monotonically drop toward 0.

## Verification (definition of done)

`./scripts/hygiene/lint.sh --from-source 2>&1 | grep -c borrowable-char-param`
returns 0 (or only sites explicitly decided to keep, documented here), AND the
full unit-test + conformance smoke of every touched package is green, AND a
gen1/gen2 self-compile passes (the BUILDER-compiled packages).

## Session context (for the post-compaction continuation)

- Working in the `temp-5` binate worktree (`~/binate/temp-binate-5`, branch
  `temp-5`). `bnfmt`: `scripts/build-bnfmt.sh -o /tmp/bnf && /tmp/bnf -w <files>`.
- Landed earlier this session (all on `main`): VM static-data Part B `1aa82ac25`;
  string-literal leak fix `f68fbc0bc`; the two `@[]char`-view UAF fixes
  cmd/bnc `bf8a91a7c` + loader `f09a89bb3`; done-log + todo updates.
- The `borrowable-char-param` rule (`e4a5469ed` on temp-5) is under a final
  minimal adversarial review, then lands, then THIS conversion work begins.
