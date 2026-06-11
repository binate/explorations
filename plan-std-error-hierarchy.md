# Plan: stdlib standard failure hierarchy

Status: DESIGN (2026-06-11). Builds on the shipped `@errors.Error`
interface (`Error()` + `Unwrap()`) and `errors.Is` in
`ifaces/stdlib/pkg/std/errors.bni` / `impls/stdlib/common/pkg/std/errors/`.
Tree + mechanism are §§1–6; §7 is the `os` errno mapping; §8 collects the
open questions that survive (a 3-critic adversarial review of an earlier
draft is folded in; the questions it raised that have since been resolved
are noted in §8 for the record).

## 1. Principle

Errors are organized by **caller recourse** — "what must change for this
call to succeed?" — not by surface description. That's the non-arbitrary
core of Google's `absl::Status`; the RPC-historical parts
(`ABORTED`/`UNAVAILABLE`/`FAILED_PRECONDITION` retry trio,
`UNAUTHENTICATED`, `DEADLINE_EXCEEDED`) are dropped or refit.

- **They are all "failures."** "Error" vs "benign failure" is not a technical
  distinction — every one of these means the requested operation did not
  *fully* succeed. End-of-input is a failure (the read didn't get the bytes)
  exactly like "file not found" is; nothing here is special-cased as benign.
- **Every error the stdlib *returns* roots in exactly one base failure.**
  Hard rule. It constrains the stdlib's own error production, NOT the
  `errors.New` primitive: `New(msg)` deliberately roots in *nothing* (it's
  the root-maker — §8.B), and apps use it to seed their own hierarchies. The
  rule is enforced by a hygiene check that flags `errors.New(` in the stdlib
  outside a whitelist (the base-singleton definitions + package roots like
  `io.EOF`).

## 2. The hierarchy

```
@errors.Error  (the interface — the type root)
├─ InvalidArgument        ⟶ fix the call (request prima-facie wrong; no state of the world makes it succeed)
├─ Unsupported            ⟶ different build/platform (capability fundamentally absent here)
│  └─ Unimplemented        ⟶ implement it (supportable here, just not done — no promise it will be)
├─ ConditionsUnmet        — the call is valid in principle, but current conditions don't permit it
│  ├─ NotFound
│  ├─ AlreadyExists
│  ├─ OutOfRange            (past a *variable* bound — e.g. reading past EOF, a parse overflowing the type)
│  └─ BadData              (the input or content is malformed / corrupt)
├─ PermissionDenied       ⟶ obtain authority (actively denied, orthogonal to validity)
├─ Retryable              ⟶ retry later (the *environment* can't currently support the call)
│  ├─ ResourceExhausted    (space, handles, memory, quota)
│  ├─ RateLimited          (deliberate throttle)
│  └─ Unavailable          (a needed service/device is down/unreachable)
└─ Unknown                ⟶ report/abort (genuinely unclassifiable — e.g. an unmapped errno)
```

### The middle, broad category

`ConditionsUnmet` is deliberately **broad and recourse-agnostic**: the call
is well-formed and supported — it *could* succeed — but the current state of
its *subject* (the entity, its type, its contents/data, a position within
it) doesn't permit it. The recourse is **not** "act and retry" — that
over-specifies. *Something* must change the conditions, and that something
may be the caller, a third party (a peer appends to the file), or **nothing**
(the condition may never change — a terminated stream). Permanence is a
property of the situation, not a node: it's situation-dependent and at best
approximate (even a read-only filesystem becomes writable if remounted/
swapped), so it is not modeled.

The commonly-tested specifics get named children (`NotFound`,
`AlreadyExists`, `OutOfRange`, `BadData`); finer slicing is arbitrary, so
other "conditions don't permit" cases (a wrong-type entity, a symlink loop)
just root in `ConditionsUnmet` directly.

`ConditionsUnmet` vs `Retryable`: both are "conditions don't permit," but
`ConditionsUnmet` is about the op's **own subject**, while `Retryable` is
about the **environment** around it (capacity/throughput/availability),
independent of the subject — and `Retryable`'s membership test is "blind
back-off-retry is reasonable," which is false for `ConditionsUnmet` (retrying
`NotFound` is pointless until the entity exists).

### Recourse

| Branch | What's wrong | Caller move |
|--------|--------------|-------------|
| `InvalidArgument`, `Unsupported`/`Unimplemented` | the call itself | fix code / build |
| `ConditionsUnmet` | the subject's current state | handle per the specific condition; the conditions must change (by some means — possibly never) |
| `PermissionDenied` | a third party withholds authority | obtain authority |
| `Retryable` | the environment | retry later (the environment may recover) |
| `Unknown` | unknown | report / abort |

`Unsupported` is fundamental-absence; `Unimplemented` (its child) is a
fillable gap, no "yet." `PermissionDenied` and `Retryable` are their own
axes. `Retryable` means "retry is the move (possibly after external
action)," **not** "self-resolves."

## 3. Mechanism — the Unwrap lineage *is* the hierarchy

No separate kind/code enum. Base failures are process-lifetime **singletons**
in `pkg/std/errors`; their `Unwrap()` links encode the tree
(`errors.NotFound.Unwrap() == errors.ConditionsUnmet`;
`errors.ConditionsUnmet.Unwrap() == empty`; `errors.Unimplemented.Unwrap()
== errors.Unsupported`; `errors.ResourceExhausted.Unwrap() ==
errors.Retryable`; …). A concrete error roots in a base by wrapping the
most-specific base singleton with context (`errors.Wrap(errors.NotFound,
"open /etc/foo")`) or via a package type whose `Unwrap()` returns the base.

`errors.Is(err, base)` is the **only** blessed check — it walks the `Unwrap`
chain and is true iff `base` is in the lineage, giving "is-a" for free:
`errors.Is(err, NotFound)` (exact), `errors.Is(err, ConditionsUnmet)` (also
true), `errors.Is(err, Retryable)` ("can my retry loop handle this?").

Rules / facts:

- **Callers never use `same()` directly** — only `errors.Is`. (`Is` uses
  `same()` internally, per node, which is correct.)
- **Single `Unwrap` ⇒ linear chain, one base.** A wrapper of another error
  *inherits* that error's classification (bottom-most base wins). But
  reclassifying-while-preserving-the-cause is impossible with one `Unwrap`
  (point at the cause *or* a new base, not both) — §8.B.
- **`errors.Is` has no visited-set guard**, so a mis-authored *cyclic* parent
  link would infinite-loop. The base graph is a tree by construction; add a
  unit test asserting acyclicity rather than trusting it.
- **Returning a bare base** (`return errors.AlreadyExists`) is valid but
  discouraged (no context).

## 4. `io.EOF`

`io.EOF` roots in `errors.ConditionsUnmet` (a read couldn't get the requested
bytes — a condition on the stream's available content). It stays a value in
`pkg/std/io`: `errors.Is(err, io.EOF)` tests it specifically, `errors.Is(err,
errors.ConditionsUnmet)` generally, `io.IsEOF` is sugar. With the broadened,
recourse-agnostic `ConditionsUnmet`, there is no "busy-loop" hazard — EOF is
just "conditions don't permit reading more"; the caller tests it and stops.

**Construction (review correction):** `io.EOF` must be the §6 base-type
object (own-message). It is **not** a one-line edit — today `io.EOF =
errors.New("EOF")` is a `leafError` whose `Unwrap()` is hard-wired empty and
can't be re-rooted; and `errors.Wrap(ConditionsUnmet, "EOF")` would render
`"EOF: conditions unmet"`.

## 5. Surface (`pkg/std/errors`)

`Error` is an interface, so a concrete error type carries whatever fields it
likes and exposes them via its own methods. No new machinery beyond:

- The base-failure singletons (bare names, no `Err` prefix — `io.EOF`
  precedent): `errors.InvalidArgument`, `errors.Unsupported`,
  `errors.Unimplemented`, `errors.ConditionsUnmet`, `errors.NotFound`,
  `errors.AlreadyExists`, `errors.OutOfRange`, `errors.BadData`,
  `errors.PermissionDenied`, `errors.Retryable`, `errors.ResourceExhausted`,
  `errors.RateLimited`, `errors.Unavailable`, `errors.Unknown`.
- `errors.New`, `errors.Wrap`, `errors.Is` (Is already walks the lineage).
- **Language gap (noted, not solved):** without type assertions / downcasts
  on interface values, a caller can classify (`errors.Is`) but can't pull
  *structured* fields (a path, etc.) out — only `Error()` text. Until then,
  structured extraction is limited.

## 6. Base-error construction

A base singleton is a **small distinct type** holding `{own message,
parent-base link}`; its `Error()` returns **its own message only** — it is
NOT a `wrappedError` (which renders `"ctx: cause"`).

- **Message concatenation: SETTLED to own-message-only.** If base→parent links
  concatenated, `errors.NotFound.Error()` would render `"not found:
  conditions unmet"` — noise on every message, leaking the taxonomy, with no
  upside (classification is via `errors.Is`, never by parsing the string).
- **Init declaration-order rule:** intra-package global initializers run in
  **source order, not topologically** (`pkg/binate/ir/gen_init.bn`). A base
  must be declared **after** the base it links to (`ConditionsUnmet` before
  `NotFound`), or the child reads an empty parent and silently misclassifies.
  Keep parents-first **and** add a test asserting full lineage. Cross-package
  init *is* dependency-ordered, so `errors.__init` runs before `io.__init`.

## 7. `os` errno → base mapping

Replaces `os`'s message-only errors. The libc impl reads `errno` via the
per-platform function selected at compile time by `build.OS` (`__error()` on
Darwin, `__errno_location()` on Linux), then wraps the right base with
context (path, op).

**This is the per-operation *default*, not a global truth.** Several errnos
are multi-meaning (`ENXIO`, `EBUSY`, `EAGAIN`, `EPERM`, `ENOTDIR`); a
specific `os` function may override. **Network errnos** belong to net
packages, not here.

| errno | base | notes |
|-------|------|-------|
| `ENOENT` | `NotFound` | |
| `EEXIST` | `AlreadyExists` | |
| `ENOTEMPTY` | `ConditionsUnmet` | "must-be-absent present" (rmdir non-empty) |
| `EISDIR`, `ENOTDIR` | `ConditionsUnmet` | wrong-type entity — *not* prima-facie bad args |
| `ELOOP` | `ConditionsUnmet` / `BadData` | symlink cycle = malformed fs structure |
| `EILSEQ` | `BadData` | illegal byte sequence |
| `EFBIG`, `EOVERFLOW`, `ERANGE` | `OutOfRange` | value/size past a bound (EOVERFLOW: 32-bit off_t) |
| `EACCES`, `EPERM` | `PermissionDenied` | EPERM is sometimes categorical (≈`Unsupported`) |
| `EROFS` | `PermissionDenied` ★ | real recourse "remount rw" fits no node cleanly |
| `EINVAL`, `EBADF`, `ENAMETOOLONG` | `InvalidArgument` | EBADF/EFAULT are *defects* — §8.C |
| `EFAULT` | `Unknown` | a defect; report/abort |
| `ESPIPE`, `EOPNOTSUPP`/`ENOTSUP`, `EXDEV`★ | `Unsupported` | op not supported on this object/across this boundary |
| `ENOSYS` | `Unsupported` | kernel lacks the syscall ≠ *we* didn't implement it (NOT `Unimplemented`) |
| `ENODEV`★, `ENXIO`★ | `Unsupported` / `Unavailable` | per-op (device-can't-do-this vs FIFO-no-reader) |
| `ENOSPC`, `EDQUOT`, `EMFILE`, `ENFILE`, `EMLINK`, `ENOMEM` | `ResourceExhausted` | ENOSPC also has a ConditionsUnmet claim — §8.A |
| `EAGAIN`/`EWOULDBLOCK`, `ETIMEDOUT` | `Retryable` | ETIMEDOUT also Unavailable/InvalidArgument — §8.A |
| `EBUSY`★ | `Retryable` | per-op: busy mountpoint on unlink is closer to `ConditionsUnmet` |
| `EINTR` | (auto-retried) | not surfaced — see below |
| `EIO`★, unmapped | `Unknown` | EIO could be `Unavailable` (failing device) — deliberate `Unknown` |

`strconv` (not an errno, but the canonical parse case): a **syntax** error →
`BadData`; a value that **overflows** the target type → `OutOfRange`. Bad
magic / truncated headers in the asm/macho, asm/elf loaders → `BadData`.

**`EINTR`:** auto-retried inside the file/os impl and not surfaced — so it is
**not** a `Retryable` value. Two carve-outs: **`close()` must NOT be retried
on `EINTR`** (the fd may already be closed; a retry can close an unrelated fd)
— treat as closed/success; and once deadlines/cancellation land (§8.D),
`EINTR` becomes meaningful and must surface, not be eaten.

## 8. Open items

Resolved during design (kept for the record): a separate top-level `BadData`
node (→ made a child of `ConditionsUnmet`); the `io.EOF` "busy-loop
ergonomics" worry (→ dissolved once `ConditionsUnmet` stopped meaning "act
and retry"); `OutOfRange`'s parent (→ stays under the now-recourse-agnostic
`ConditionsUnmet`); modeling permanent-vs-changeable conditions (→ rejected,
situation-dependent).

Still open:

**A. Dual-category errnos.** A few failures have a claim on two branches and
the linear chain picks one. `ENOSPC` is `ResourceExhausted`/`Retryable`
(environmental) but also "free space, then it works" (`ConditionsUnmet`);
`EROFS`, `ETIMEDOUT`, `ECONNRESET` similarly. The subject-vs-environment line
(§2) usually decides — disk/quota/availability are environmental →
`Retryable` — but record the dual cases (chosen branch + why the other lost)
rather than leaving the pick silent.

**B. Reclassify-while-preserving-cause + `errors.New`. ✅ RESOLVED.** (B1) With
single `Unwrap`, a wrapper inherits its cause's classification; to re-classify
you mint a *fresh* error rooted in the new base (the original cause survives
only in the message). An app that needs reclassify-*and*-keep-the-structured-
cause defines its own concrete `Error` type with extra links — `Error` is an
interface, so single-`Unwrap` is only the *stdlib's* contract. (No second link
in the stdlib.) (B2) `errors.New(msg)` stays the unconstrained **root-maker**
(empty `Unwrap`); the §1 hard rule applies to errors the stdlib *returns*,
enforced by a hygiene check whitelisting `New` to base/root definitions.

**C. Defects (`EBADF`/`EFAULT`). ✅ RESOLVED.** No defect node (one would
invite recovery handlers to swallow bugs). True defects **panic** — consistent
with Binate aborting on bounds / div-by-zero. The `os` impl routes every
syscall failure through one `failErrno(errno, op, path)` helper that panics on
`EBADF`/`EFAULT` (and other defect errnos) *before* calling a pure, total
`errnoToError(errno) → @Error` mapper for the rest — centralizing the panic
(can't forget it at a syscall site) while keeping the mapper side-effect-free
and testable. A defect surfaced rather than aborted is `Unknown`, never
`InvalidArgument`.

**D. `Cancelled`/`Timeout` (deferred until deadlines/cancellation exist).**
`Cancelled` is **not** `Retryable` — a cancelled op won't un-cancel on retry;
recourse is "stop, the caller asked to" → its own axis. `Timeout` slots under
`Retryable`. `EINTR` surfaces as `Cancelled` once this lands.

**E. `PermissionDenied`/`NotFound` indistinguishability (documented
limitation).** Systems return `ENOENT` instead of `EACCES` to hide a
resource's existence — the two aren't always distinguishable; a caller must
not treat `NotFound` as proof of access.

## 9. Migration

- `errors.bni` / `errors.bn`: add the base singletons (distinct base type,
  own-message `Error()`, parent link via `Unwrap`); parents declared first;
  add a lineage/acyclicity test. `Is`/`Wrap` unchanged. Resolve §8.B (`New`).
- `io`: re-root `io.EOF` as the base-type object (NOT a one-line edit; NOT
  `Wrap` — §4).
- `os` (libc): construct errno-derived errors rooted in bases (§7), replacing
  the `errors.New("os: …")` strings; read errno per-platform via `build.OS`,
  through one `failErrno(errno, op, path)` helper (panics on defect errnos;
  else wraps a pure `errnoToError` mapper — §8.C).
- `strconv`: split syntax (`BadData`) vs overflow (`OutOfRange`).
- Hygiene check: `errors.New(` only at whitelisted base/root sites (§1), which
  is how "every returned stdlib error roots in a base" is enforced.
- **Separate, independent code change (read-only messages):** `errors.New` /
  `errors.Wrap` should take `@[]readonly char` and `Error()` should return
  `@[]readonly char` — a message is immutable to its consumer. Ripples through
  `withContext` / `leafError` / `wrappedError`, `io.EOF` / `io.IsEOF`, and
  every caller that binds a message as `@[]char`. Not coupled to this
  hierarchy; land on its own.

## 10. Provenance

Tree + naming settled in design discussion (2026-06-09…11); the
`ConditionsUnmet` broadening (recourse-agnostic; absorbs bad-data and
wrong-type; `BadData` as a child) and dropping the error/benign distinction
were the final reframe. §7/§8 incorporate a 3-critic adversarial review of an
earlier draft. The remaining §8 items are owned by the language designer.
