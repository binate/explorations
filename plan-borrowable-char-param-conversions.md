# Plan: convert read-only `@[]char` params to `*[]readonly char` (borrow-not-copy)

> **Status: DONE — rule sound + landed (`ff34bfd9e`), all conversions landed
> (through `cf37c5336`), whole-repo `--tests` scan = 0 sites.** The only thing
> left is the "adopt" step — dropping `--disable borrowable-char-param` from
> `scripts/hygiene/lint.sh` — which is BLOCKED on a CHECK_TOOLS bump to a bnlint
> carrying the sound rule (the current bundle `bnc-0.0.14-pre2` has the OLD
> over-warning rule that flags the `TestResult` test helpers the sound rule
> suppresses; removing the flag now would fail hygiene). Tracked in
> `claude-todo.md` ("Adopt `borrowable-char-param`"). The rest of this doc is the
> historical record of how the rule + conversions were done.

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

## Rule soundness — RESOLVED (landed `ff34bfd9e`)

The rule is a sound forward value-family / escape analysis. Getting there took a
full rewrite (the first cut `e4a5469ed` was a per-occurrence classifier and was
UNSOUND) plus five adversarial verification passes that found and fixed **four**
distinct over-warns (a parameter the rule FLAGS whose driven `*[]readonly char`
conversion breaks — a hard type error or a dangling-return UAF). Root insight: a
sub-slice `p[lo:hi]` of a `@[]char` is itself an OWNED `@[]char` sharing backing
(`checkSliceExpr` returns the managed-slice type unchanged), NOT a raw borrow, so
reducibility depends on where the parameter AND its sub-slices (and locals bound
to them) ultimately FLOW.

**The analysis** (`borrowable_char_param.bn` walk + family scope stack;
`_expr.bn` classification; `_util.bn` predicates; tests in the three `*_test.bn`;
`_return.bn` deleted, subsumed). FAMILY of param P = { P } ∪ { sub-slices
`fam[lo:hi]` } ∪ { locals FRESHLY bound `var x = fam` / `x := fam` to a family
value }, transitive, lexical scope stack. FLAG P iff `HasBorrow && !HasOwn &&
!(Returned && OwnedCharReturn)`:
- HasBorrow: a family member is read (`fam[i]`/`len`/range), sub-sliced,
  returned, or passed to a `*[]readonly char` callee.
- HasOwn (owning escape → suppress): a family member stored into a non-family
  managed slot (global / field / existing non-family local / composite element /
  a `@[]char`/`*[]char`/unresolvable callee arg), mutated (`fam[i]=e`,
  reassigned), address-taken (`&fam`, `&fam[i]`, `&fam[lo:hi]`), or closure-captured.
- Returned + OwnedCharReturn: a family member returned means the return type must
  cascade to `*[]readonly char`; any NON-family, non-nil char-slice return
  operand blocks the cascade (recognised through every spelling — a
  bare/readonly/named `@[]char`, and a multi-value call forward `return g()` /
  `return fv()` whose node type is the callee signature, not the `@[]char` slot).

**The five over-warns found + fixed** (all pinned by regression tests):
1. a DEFINED NAMED char-slice return (`type Str @[]char`) — `isManagedCharSliceType`
   now peels `TYP_NAMED`.
2. a MULTI-VALUE plain/method call forward (`return g()`, `g() (@[]char,…)`) —
   node type is the callee `TYP_FUNC`, caught by `isFuncTyped`.
3. a MULTI-VALUE func-VALUE call forward (`return fv()`) — `TYP_FUNC_VALUE` /
   `TYP_MANAGED_FUNC_VALUE`, added to `isFuncTyped`.
4. `&fam[i]` (address of a family ELEMENT) — `pcRead`'s `&` branch now treats any
   family-rooted address as owning.
5. a view returned into a NAMED-managed / `@[]readonly char` return SLOT (`func
   fail(msg @[]char) testing.TestResult { return msg }`) — the cascade can't
   rewrite a shared named type, so converting is a type error; `pcCheckCascadeSlot`
   now requires the declared return slot to be a literal `@[]char` or
   `*[]readonly char` (landed `519fa5330`). Found during the phase-2 conversion
   recon; removed ~50 false flags (the replicated test helpers).

Both sides are closed by a theorem: (return) any operand sharing a char-return
slot with a family view is assignable into it, so its stamped own-type is a
managed-char-slice, a function type, or nil — all caught (a returned string
LITERAL is a deliberate TRUE POSITIVE: rodata is immortal, the converted return
never dangles); (escape) every owning vector routes through `pcRead`, where a
bare family ident / a family sub-slice in a value context / a family-rooted `&`
sets HasOwn. Under-warns (accepted): a fresh zero-value-local return (use `p[0:0]`);
a family member reassigned via `=`.

## The rule (landed, sound)

`borrowable-char-param` — `pkg/binate/lint/borrowable_char_param.bn` +
`_expr.bn` + `_util.bn` predicates; tests in the three `*_test.bn`; registered
in `lint.bn`'s `LintFile`. Landed on `main` as `ff34bfd9e`.

It flags a param declared exactly `@[]char` that is reducible to
`*[]readonly char` per the analysis above. Correctly SUPPRESSED (must STAY
`@[]char`, do NOT convert): a param returned as a view whose function ALSO
returns an owned `@[]char` on another path — the genuinely-mixed-return class
such as `shortTypeName` (`ir/gen_impl_recvname.bn`). NOTE the sound rule is more
precise than the old coarse suppression: `qualifierTypeName` / `nameLooksLikeMethod`
(which READ `name` but return a fresh allocation / a bool — read-only consumers)
ARE correctly flagged now, and a string-literal-returning view helper is a true
positive.

## How to regenerate the exact work list (it drifts as code lands)

```
cd <a binate worktree, e.g. ~/binate/temp-binate-5>
./scripts/hygiene/lint.sh --from-source 2>&1 | grep borrowable-char-param
```
Each line is `file:line:col: [borrowable-char-param] parameter <name> is a read-only @[]char; …`.
(`--from-source` builds bnlint from the current tree so the rule is present even
before the `CHECK_TOOLS` bump.)

**Snapshot: ~114 flags** after the 5th-over-warn fix (`519fa5330`) and the first
conversion batch (buildcfg + cmd/bnlint, `9b2dc6469`) — down from the inflated
165. (The
flavor / per-package breakdown just below predates the soundness rewrite — the
sound rule changed which sites flag, e.g. `qualifierTypeName` is now included and
some mixed-return sites dropped — so REGENERATE it fresh before scoping a batch.)
Two flavors (distinguishable by the message suffix):
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

- The rule already excludes them (won't be in the list) — genuinely-mixed-return
  functions that return a param VIEW on one path and a fresh OWNED `@[]char`
  (make/make_slice/call/composite/multi-value forward) on another, e.g.
  `shortTypeName` (`ir/gen_impl_recvname.bn`); `*[]readonly char` would dangle the
  owned-return path. Trust the rule: whatever it does NOT flag, leave `@[]char`.
  (Do NOT hand-add sites here that the sound rule already flags — e.g.
  `qualifierTypeName` / `nameLooksLikeMethod` READ `name` and return a fresh
  allocation / a bool, so they ARE flagged and SHOULD be converted.)
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
- Landed on `main`: VM static-data Part B `1aa82ac25`; string-literal leak fix
  `f68fbc0bc`; the two `@[]char`-view UAF fixes cmd/bnc `bf8a91a7c` + loader
  `f09a89bb3`; the `borrowable-char-param` rule (sound) `ff34bfd9e`.
- NEXT: phase 2 — the conversions. Regenerate the flag list (`scripts/hygiene/lint.sh
  --from-source | grep borrowable-char-param`), then convert package-by-package per
  the conversion pattern above.
- Orthogonal MAJOR bug found during the rule review (tracked in `claude-todo.md`):
  `bnc-0.0.13` wrongly accepts a `[N]readonly char` array where a slice is expected
  and mis-lowers it. Not part of this plan.
