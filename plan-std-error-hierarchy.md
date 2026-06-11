# Plan: stdlib standard failure hierarchy

Status: DESIGN (2026-06-11). Builds on the shipped `@errors.Error`
interface (`Error()` + `Unwrap()`) and `errors.Is` in
`ifaces/stdlib/pkg/std/errors.bni` / `impls/stdlib/common/pkg/std/errors/`.
The settled tree + mechanism are §§1–6; §7 is the `os` errno mapping; §8
collects open questions a 3-critic adversarial review raised (some
challenge decisions made during design — they need a call before code).

## 1. Principle

Errors are organized by **caller recourse** — "what must change for this
call to succeed?" — not by surface description. That's the non-arbitrary
core of Google's `absl::Status`; the RPC-historical parts
(`ABORTED`/`UNAVAILABLE`/`FAILED_PRECONDITION` retry trio,
`UNAUTHENTICATED`, `DEADLINE_EXCEEDED`) are dropped or refit.

- **They're "failures," not "errors" colloquially.** A failure is any way an
  operation doesn't fully succeed, including benign ones.
- **Every stdlib `errors.Error` roots in exactly one base failure.** Hard
  rule. (Caveat: `errors.New` currently roots in *nothing* — see §5/§8.)

> Review caveat: the "recourse falls out cleanly" claim below does **not**
> hold for a few dual-category cases (disk-full, read-only-fs, timeout) and
> for corrupt-data — see §8.A/§8.B.

## 2. The hierarchy

```
@errors.Error  (the interface — the type root)
├─ InvalidArgument        ⟶ fix the call (request prima-facie wrong; no state makes it succeed)
├─ Unsupported            ⟶ different build/platform (capability fundamentally absent here)
│  └─ Unimplemented        ⟶ implement it (supportable here, just not done — no promise it will be)
├─ ConditionsUnmet        ⟶ change that specific state (the op's own precondition, but could be met)
│  ├─ NotFound
│  ├─ AlreadyExists
│  └─ OutOfRange            (past a *variable* bound, e.g. EOF — not prima-facie)  [parent under review §8.D]
├─ PermissionDenied       ⟶ obtain authority (actively denied, orthogonal to validity)
├─ Retryable              ⟶ retry later (environmental: not the op's target; may need external action)
│  ├─ ResourceExhausted    (space, handles, memory, quota)
│  ├─ RateLimited          (deliberate throttle)
│  └─ Unavailable          (a needed service/device is down/unreachable)
└─ Unknown                ⟶ report/abort (genuinely unclassifiable — e.g. an unmapped errno)
```

Recourse table:

| Branch | What must change | Caller move |
|--------|------------------|-------------|
| `InvalidArgument`, `Unsupported`/`Unimplemented` | the call itself | fix code / build |
| `ConditionsUnmet` | a specific target state | act on that target, then retry |
| `PermissionDenied` | a third party's grant | obtain authority |
| `Retryable` | the environment | retry later (± external action) |
| `Unknown` | unknown | report / abort |

Node notes: `Unsupported` is fundamental-absence; `Unimplemented` (its
child) is a fillable gap (no "yet"). `OutOfRange` is under `ConditionsUnmet`
because the bound is *variable* — but the review argues it doesn't share
ConditionsUnmet's "act-and-retry" recourse (§8.D). `PermissionDenied` and
`Retryable` are their own axes. `Retryable` means "retry is the move
(possibly after external action)," **not** "self-resolves" — blind
back-off-retry being reasonable is its membership test.

## 3. Mechanism — the Unwrap lineage *is* the hierarchy

No separate kind/code enum. Base failures are process-lifetime
**singletons** in `pkg/std/errors`; their `Unwrap()` links encode the tree
(`errors.NotFound.Unwrap() == errors.ConditionsUnmet`;
`errors.ConditionsUnmet.Unwrap() == empty`; `errors.Unimplemented.Unwrap()
== errors.Unsupported`; …). A concrete error roots in a base by wrapping the
most-specific base singleton with context (`errors.Wrap(errors.NotFound,
"open /etc/foo")`) or via a package type whose `Unwrap()` returns the base.

`errors.Is(err, base)` is the **only** blessed check — it walks the `Unwrap`
chain and is true iff `base` is in the lineage, giving "is-a" for free:
`errors.Is(err, NotFound)` (exact), `errors.Is(err, ConditionsUnmet)` (also
true), `errors.Is(err, Retryable)` ("can my retry loop handle this?").

Rules / facts (some corrected by review):

- **Callers never use `same()` directly** — only `errors.Is`. (`Is` uses
  `same()` internally, per node, which is correct.)
- **Single `Unwrap` ⇒ linear chain, one base.** A wrapper of another error
  *inherits* that error's classification (bottom-most base wins). **But
  reclassifying-while-preserving-the-cause is impossible with one `Unwrap`**
  (you can point at the cause *or* a new base, not both) — this is a real
  gap the shipped types can't satisfy; see §8.E.
- **`errors.Is` has no visited-set guard** (`errors.bn` walk is `for
  present(cur)`), so a mis-authored *cyclic* parent link would infinite-loop.
  The base graph is a tree by construction (acyclic) — add a unit test
  asserting that, rather than trusting it.
- **Returning a bare base** (`return errors.AlreadyExists`) is valid but
  discouraged (no context).

## 4. `io.EOF` re-rooted

`io.EOF` becomes an `errors.Error` rooted in `ConditionsUnmet` (a read that
couldn't get the requested bytes). It stays a value in `pkg/std/io` so
`errors.Is(err, io.EOF)` works specifically *and* `errors.Is(err,
ConditionsUnmet)` is true; `io.IsEOF` is sugar.

Two corrections from review:
- **It is NOT a one-line change.** Today `io.EOF = errors.New("EOF")` is a
  `leafError` whose `Unwrap()` is hard-wired empty — it can't be re-rooted by
  assignment. `io.EOF` must become the §6 base-type object (own-message). Do
  **not** use `errors.Wrap(ConditionsUnmet, "EOF")` — that renders
  `"EOF: conditions unmet"`.
- **Ergonomics are contested** (§8.C): "EOF is a failure" risks conflating
  benign end-of-stream with errors in the dominant loop-on-`Read` pattern,
  and `ConditionsUnmet`'s "act-and-retry" is the wrong recourse for EOF
  ("stop reading"). Resolution options in §8.C.

## 5. Surface (`pkg/std/errors`)

`Error` is an interface, so a concrete error type carries whatever fields it
likes and exposes them via its own methods. No new machinery beyond:

- The base-failure singletons (bare names, no `Err` prefix — no stutter after
  `errors.`; `io.EOF` precedent): `errors.InvalidArgument`,
  `errors.Unsupported`, `errors.Unimplemented`, `errors.ConditionsUnmet`,
  `errors.NotFound`, `errors.AlreadyExists`, `errors.OutOfRange`,
  `errors.PermissionDenied`, `errors.Retryable`, `errors.ResourceExhausted`,
  `errors.RateLimited`, `errors.Unavailable`, `errors.Unknown`.
- `errors.New`, `errors.Wrap`, `errors.Is` (Is already walks the lineage).
- **`errors.New` vs the hard rule (§8.E):** `New(msg)` produces a leaf that
  roots in nothing — so `New` (and today's `io.EOF`) violates §1. Either
  `New` gains a base parameter (`New(base, msg)`) or §1 carves out `New`.
- **Language gap (noted, not solved):** without type assertions / downcasts
  on interface values, a caller can classify (`errors.Is`) but can't pull
  *structured* fields (a path, etc.) out — only `Error()` text. Until then,
  structured extraction is limited.

## 6. Base-error construction

A base singleton is a **small distinct type** holding `{own message,
parent-base link}`; its `Error()` returns **its own message only** — it is
NOT a `wrappedError` (which renders `"ctx: cause"`).

- **Message concatenation: SETTLED to own-message-only** (was TBD). If
  base→parent links concatenated, `errors.NotFound.Error()` would render
  `"not found: conditions unmet"` — noise on every message, leaking the
  taxonomy, with no upside (classification is recovered via `errors.Is`, never
  by parsing the string). The parent link is for classification, not context.
- **Init declaration-order rule (review footgun, §8 confirmed):**
  intra-package global initializers run in **source order, not
  topologically** (`pkg/binate/ir/gen_init.bn`). So a base must be declared
  **after** the base it links to (`ConditionsUnmet` before `NotFound`), or
  the child reads an empty parent and silently misclassifies. Either keep
  parents-first by discipline **and** add a test asserting full lineage
  (don't trust order). Cross-package init *is* dependency-ordered, so
  `errors.__init` runs before `io.__init` (io.EOF is safe).

## 7. `os` errno → base mapping

Replaces `os`'s message-only errors. The libc impl reads `errno` via the
per-platform function selected at compile time by `build.OS` (`__error()` on
Darwin, `__errno_location()` on Linux), then wraps the right base with
context (path, op).

**This mapping is the per-operation *default*, not a global truth.** Several
errnos are multi-meaning (`ENXIO`, `EBUSY`, `EAGAIN`, `EPERM`, `ENOTDIR`)
and a specific `os` function may override per its semantics. **Network
errnos** belong to net packages, not here.

| errno | base | notes |
|-------|------|-------|
| `ENOENT` | `NotFound` | |
| `EEXIST`, `ENOTEMPTY` | `AlreadyExists` / `ConditionsUnmet` | ENOTEMPTY (rmdir) is "must-be-absent present" → ConditionsUnmet |
| `EACCES`, `EPERM` | `PermissionDenied` | EPERM is sometimes categorical (≈Unsupported) |
| `EROFS` | `PermissionDenied` ★ | real recourse "remount rw" fits no node — §8.B hole |
| `EINVAL`, `EBADF`, `ENAMETOOLONG` | `InvalidArgument` | EBADF/EFAULT are *defects* — see §8.F |
| `EFAULT` | `Unknown` | a defect; report/abort, not "fix the argument" |
| `EISDIR`, `ENOTDIR` | `InvalidArgument` ★ | but "wrong entity type" is state-dependent → maybe ConditionsUnmet (§8.D consistency) |
| `ELOOP` | `InvalidArgument` ★ | symlink cycle is filesystem state/bad-data, not a bad call — →BadData if §8.A lands |
| `ESPIPE` | `Unsupported` ★ | seek on a pipe: op not supported on this object |
| `ENOSYS` | `Unsupported` | kernel lacks the syscall ≠ *we* didn't implement it (NOT `Unimplemented`) |
| `EOPNOTSUPP`/`ENOTSUP`, `EXDEV` | `Unsupported` ★ | EXDEV (cross-device link) "use copy+delete" fits no node well — §8.B |
| `ENODEV` | `Unsupported` ★ | usually "device can't do this op," not "device absent" |
| `ENXIO` | `Unavailable` ★ | overloaded: device-absent vs FIFO-no-reader (the latter is Unavailable) — per-op |
| `ENOSPC`, `EDQUOT`, `EMFILE`, `ENFILE`, `EMLINK`, `ENOMEM` | `ResourceExhausted` | ENOSPC also has a ConditionsUnmet claim — §8.A |
| `EFBIG`, `EOVERFLOW`, `ERANGE` | `OutOfRange` | value/size past a bound (EOVERFLOW: 32-bit off_t) |
| `EAGAIN`/`EWOULDBLOCK`, `ETIMEDOUT` | `Retryable` | ETIMEDOUT also has Unavailable/InvalidArgument claims — §8.A |
| `EBUSY` | `Retryable` ★ | per-op: a busy mountpoint on unlink is closer to ConditionsUnmet |
| `EINTR` | (auto-retried) | see below — do NOT list as a surfaced Retryable |
| `EIO`, unmapped | `Unknown` | EIO ★: deliberate Unknown; could be Unavailable (flaky/failing device) |
| (net: `EPIPE`,`ECONNRESET`,`ECONNREFUSED`,`ENETUNREACH`,`EHOSTUNREACH`,`EADDRINUSE`,…) | net pkgs | EADDRINUSE→AlreadyExists nicely validates that node cross-domain |

**`EINTR`:** auto-retried inside the file/os impl (the classic restart loop)
and not surfaced — so it does **not** appear as a `Retryable` value. Two
carve-outs: **`close()` must NOT be retried on `EINTR`** (on Linux the fd may
already be closed; retrying can close an unrelated fd) — treat as
closed/success; and once deadlines/cancellation land (§8.G), `EINTR` becomes
meaningful (cancellation) and must surface, not be eaten.

## 8. Open design questions (from adversarial review, 3 critics)

These were raised against the design above; several challenge choices made
during the design conversation, so they need an explicit call.

**A. Dual-category failures.** Some failures genuinely *are-a* two branches,
but the linear single-`Unwrap` chain forces one root. `ENOSPC` is both
`ResourceExhausted`/`Retryable` **and** `ConditionsUnmet` (free space, retry)
— and blind-retry is *not* reasonable on a single-user full disk, which is
`Retryable`'s own membership test, so the critic argues `ENOSPC` →
`ConditionsUnmet`. Likewise `EROFS`, `ETIMEDOUT`, `ECONNRESET`. *Proposed:* a
"dominant-recourse + record-the-rejected-branch" subsection (turns silent
single-rooting into a reviewed decision), and reconsider `ENOSPC`'s root.

**B. A `BadData` node? (highest-impact finding.)** Corrupt/malformed *data*
has no home: `strconv.ParseInt("12x3")`, a JSON/TOML syntax error, a bad CRC,
invalid UTF-8, a bad-magic Mach-O/ELF header (**this project has
`asm/macho`, `asm/elf`**). It fits `InvalidArgument` badly (the *code* is
fine, the *data* is wrong — "fix your code" is wrong advice), `ConditionsUnmet`
awkwardly, `Unknown` wrongly (it's classifiable + often recoverable). The
absl `DATA_LOSS` we dropped *was* this notion. *Proposed:* add a top-level
**`BadData`** (recourse: "supply valid input" — a fourth distinct answer to
"what must change?": not code, not world-state, not authority, not
environment, but the input bytes). `EROFS`/`EXDEV` "capability-bounded-by-
this-context" may also want a node distinct from `Unsupported`.

**C. `io.EOF` ergonomics.** Rooting EOF in `ConditionsUnmet` is mechanically
fine but invites a busy-loop mis-recourse (a caller keying on
`ConditionsUnmet` = "act then retry" catches EOF, whose real recourse is
"stop"), and "EOF is a failure" encourages conflating benign end-of-stream
with errors in `for { Read… }`. *Options:* (1) promote `OutOfRange` to
top-level with a "stop / extend the bound" recourse and root EOF there; (2)
keep EOF in the tree (hard rule) but document it as the one intentionally-
benign member, with `io.IsEOF` as the blessed test so callers don't route it
through the generic recourse branches.

**D. `OutOfRange`'s parent.** It doesn't share `ConditionsUnmet`'s
"act-on-target-then-retry" recourse (overflow's recourse is "use a wider
type / smaller value"; EOF's is "stop"). Should it be promoted to top-level?
This also affects `EISDIR`/`ENOTDIR`/`ELOOP`, which are state-dependent yet
placed in `InvalidArgument` — inconsistent with the variable-bound reasoning
used to justify `OutOfRange`. Resolve the "prima-facie vs state-dependent"
line once and apply it uniformly.

**E. Reclassify-while-preserving-cause.** With single `Unwrap`, a high-level
op cannot both re-classify (e.g. `InvalidArgument` for a bad config path) and
keep the low-level cause (the `NotFound` os error) — you wrap the cause (keep
classification) or wrap a new base (drop the cause). Pick a policy: (a) a
reclassifying-wrapper drops the lower cause; (b) a sibling marker for the
second link; (c) accept inheritance only. Also reconcile `errors.New`
rooting-in-nothing with the §1 hard rule.

**F. A defect / `Internal` notion?** `EBADF`/`EFAULT` are programmer bugs,
not recoverable conditions; lumping them in `InvalidArgument` lets recovery
handlers swallow what should abort. Add a defect node, or route them to
`Unknown` (recourse "report/abort" matches). (Binate already panics on many
defects — decide which surface as errors at all.)

**G. `Cancelled` is NOT `Retryable`** (corrects an earlier note). A cancelled
operation won't un-cancel on retry; recourse is "stop, the caller asked to."
When deadlines/cancellation land, `Cancelled` wants its own axis, and `EINTR`
must surface as it. `Timeout` still slots under `Retryable`.

**H. `PermissionDenied`/`NotFound` indistinguishability.** Systems return
`ENOENT` instead of `EACCES` to hide a resource's existence — so the two are
not always distinguishable; a caller must not treat `NotFound` as proof of
access. (Doesn't change the axis split; a documented limitation.)

## 9. Migration

- `errors.bni` / `errors.bn`: add the base singletons (distinct base type,
  own-message `Error()`, parent link via `Unwrap`); parents declared first;
  add a lineage/acyclicity test. `Is`/`Wrap` unchanged.
- `io`: re-root `io.EOF` as the base-type object (NOT a one-line edit; NOT
  `Wrap` — see §4).
- `os` (libc): construct errno-derived errors rooted in bases (§7), replacing
  the `errors.New("os: …")` strings; read errno per-platform via `build.OS`.
- Hygiene/review check: every stdlib error constructor bottoms out in a base
  (§1) — but reconcile `errors.New` first (§8.E).

## 10. Provenance

Tree + naming settled in design discussion (2026-06-09…11). §7/§8 reflect a
3-critic adversarial review (errno mapping, taxonomy holes, mechanism
soundness) of the draft, 2026-06-11. §8 items are unresolved and owned by the
language designer.
