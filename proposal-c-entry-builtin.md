# Proposal: `__c_entry` — a raw C-callable function pointer for any Binate function (`proposal-c-entry-builtin`)

Status: **RATIFIED — spec landed as Draft, pending implementation (2026-09-02, docs `a03d4b2`).**
Ratified with a framing correction from the owner: **the spec must not mention
thunks, backends, or ABI mechanics** — thunks exist only because of an
implementation choice (Binate-only entries are made cheaper by not being
C-callable; under a different choice or C ABI, every function might be directly
C-callable), so the landed spec text states only the language-level observable
(`pkg.cexport.semantics`: callable from C with the mapped C signature, behaves
as a call to `f`, caller-side §18.5 obligations, single-thread execution-model
limit, mechanism deliberately unspecified) plus `pkg.centry` /
`pkg.centry.eligible` / `pkg.centry.identity`. The four open questions were
ratified as recommended: name `__c_entry`; generics rejected for now; signature
rule shared with `#[c_export]`; `*uint8` result. §§2-3 below are the
pre-correction draft (superseded by the landed rules — the spec is
authoritative); §4's implementation notes remain valid **implementer guidance
that is deliberately NOT spec content**. The two MAJOR `#[c_export]` bugs of §8
remain open in `claude-todo.md`.

## 1. Problem

The only C→Binate entry today is a `#[c_export]`-**named** function. There is no
way to hand C a function pointer for an *arbitrary* Binate function — a
`qsort` comparator or an event-loop callback (a signal handler too, within the
threading limits of §2's execution-model note). Two concrete consequences:

- **The callback path is unsupported, and unsound if faked.** There is no sound
  C-callable address today: a function's `_func_handle` points at a static
  `{vtable, data}` *record* (data, not code — calling it executes rodata); the
  shim behind it expects a leading context parameter (an argument shift for a C
  caller); and even the mangled entry itself skips the C-entry
  canonicalization. AAPCS64/SysV leave the register bits above a sub-word
  integer argument **unspecified**; Binate's native backends keep sub-word
  values canonically extended internally — so a C caller entering the mangled
  entry with `int32 -5` reads a wrong (positive) value at `-O1+`. The
  per-function normalization is deliberately gated on `#[c_export]` (decision
  2026-09-01: don't pay unconditional entry overhead to defend an unsupported
  path).
- **The workaround is clumsy**: give every callback a `#[c_export]` name and
  hand C the *named symbol* — no address-of, no passing a Binate-chosen
  function at run time.

## 2. Prerequisite concept: the **C entry** (also fixes stale §16.9 text)

The implemented `#[c_export]` mechanism (landed; the spec's "Draft / pending —
specified but not yet implemented" status and its pure-**alias** description
are both stale) is:

- **LLVM backend**: the C name is a symbol **alias** of the mangled entry
  (LLVM-emitted bodies receive arguments per the platform C ABI).
- **Native backends** (aarch64/x64/arm32): the C name labels a **thunk** —
  narrow-GP-argument sign/zero-extension, then a **branch** to the mangled
  entry. Native Binate callers enter at the mangled entry and skip it; when no
  adaptation is needed the thunk is empty and the two addresses coincide.

The observable both realize, which the spec should define **once** — at the top
of the §16.9 export subsection (retitled to cover both the named and the
by-address form), so `pkg.cexport` and `pkg.centry` both sit under it:

> A **C entry** of a Binate function `f` is an entry point whose calling
> convention is exactly the **platform C ABI**, in both directions: argument
> bits above a sub-word integer parameter's width — **in its register or its
> stack slot** — are treated as unspecified and canonicalized on entry, and a
> sub-word integer **result** is extended on exit per the platform C ABI's
> return convention. Calling a C entry of `f` from C, with the C signature that
> `f`'s Binate signature maps to (`pkg.cexport.signature`), behaves identically
> **at the ABI level** to calling `f` from Binate; the **caller-side**
> reference-count contract of §18.5 `mem.param` (e.g. an `@Iface` argument's
> caller-delivered reference) is the C caller's responsibility. Whether a C
> entry shares the mangled entry's address is unspecified (an implementation
> may use one entry point when no adaptation is needed and must interpose a
> thunk when it is).
>
> _Note (execution model)._ A C entry is invoked **within the program's single
> Binate thread of execution** (§14.14; reference counting is non-atomic,
> §18). Invocation from another thread, or from an asynchronous signal context
> interrupting Binate code, is outside the execution model — undefined
> behavior (Ch.21).

`#[c_export("name")]` then reads: *the name denotes a C entry of `f`* (replacing
"aliasing that function"). The definition's stack-slot and return clauses state
the full contract; the current toolchain realizes the argument-**register**
half — the stack-arg and LLVM-return halves are the two raised MAJOR
conformance gaps of §8 (Axis-2 non-conformance, not design holes).

## 3. The builtin

```
__c_entry(f)          // f: a reference to a declared function
```

yields the **address of a C entry of `f`**, as a raw **`*uint8`** — joining
`__c_call` (call a C function) and `__c_global` (address of a C global) as the
third foreign-function primitive: *hand C a Binate function*. A `#[c_export]`
is exactly the **named, eager** instance of the same concept; `__c_entry` is the
anonymous, by-address instance.

Proposed spec text (three rules in the retitled §16.9 export subsection, after
`pkg.cexport.signature`):

> `pkg.centry` — `__c_entry(f)` yields the address of a **C entry** of the
> declared function `f`, as an opaque raw pointer **`*uint8`** (§7.8
> `type.ptr.opaque-byte`) suitable for passing to C (typically as a `__c_call`
> argument) wherever C expects a function pointer of the corresponding C
> signature. The result is a **code address, not a data pointer**: it borrows
> no managed value (the raw-borrow discipline of §18.7 `mem.raw-uaf` is
> inapplicable), is never freed, and remains valid for the life of the program.
> `__c_entry` is **compiled-mode only**, like `__c_call`/`__c_global` (the
> bytecode VM does no FFI).
>
> `pkg.centry.eligible` _(Constraint)_ — The operand must be a **reference to a
> declared, non-generic, top-level function** — a local identifier or a
> package-qualified selector; a function that is public or, within the
> declaring package, package-private (a callback is legitimately a private
> implementation detail). A method, a function *value*, a function literal, or
> a generic function is rejected: a C function pointer carries **no context
> slot**, so a capturing value cannot be lowered to one (pass context through
> the C API's `void* user_data` parameter instead, as C code does). `f`'s
> signature must satisfy `pkg.cexport.signature` (the same C-ABI-replicable
> rule as `#[c_export]`).
>
> `pkg.centry.identity` — Every evaluation of `__c_entry(f)` for the same `f`
> in a program yields the **same address**, so C-side registration and
> deregistration by pointer work. Whether that address equals a `#[c_export]`
> name's address for the same `f`, or the mangled entry's, is **unspecified** —
> the guarantee is behavioral (each is a C entry of `f`), not positional.
> _(Realization — one weak, linker-deduplicated record per function — is
> informative; Annex B.)_

Grammar and lexical companion edits: `binate.ebnf`'s `BuiltinCall` gains
`"__c_entry" "(" Expression ")"` (the same operand shape as `_func_handle`; the
reference-to-declared-function restriction is `pkg.centry.eligible`, not
grammar), the ebnf's keyword-builtin comment block adds the spelling, and §5.5's
reserved-builtin-spellings paragraph gains `__c_entry` ("Three further" →
"Four"). §15.8 `builtin.internal`'s foreign-function family mention covers it
alongside `__c_call`/`__c_global`; the normative rules live in §16.9, exactly as
for its siblings.

## 4. What the thunk does (informative, for Annex B / the implementer)

For a function without a usable `#[c_export]` entry, each use-site TU emits the
C-entry adaptation — sub-word argument canonicalization (register and, on
8-byte-slot conventions, stack), then a **branch/relocation** to the mangled
entry, not fall-through (ld64 links per-symbol atoms; a fall-through prefix
landed in inter-atom padding and SIGILL'd — the authoritative test is the
**linked self-compile**, not a `.o` disassembly). Emission is use-site
collection with **every copy weak** (the `IsLinkOnce`/`SetWeak` weak-def
precedent: ld64 `N_WEAK_DEF` coalescing, ELF COMDAT/weak, and `bnld`'s
strong-over-weak resolution give one survivor program-wide — which is what
delivers `pkg.centry.identity`). When the adaptation is empty the degenerate
lowering is **no thunk symbol at all**: the builtin lowers to a direct
reference/relocation to the mangled entry's address (always so on the LLVM
backend today) — note a use-site *alias* to a cross-TU symbol is not
expressible on either backend, so the degenerate case must be a reference, not
an alias. The strong, named `#[c_export]` symbol and any weak `__c_entry`
record for the same function are disjoint symbols; no collision arises when a
function is both exported and `__c_entry`'d.

## 5. Companion spec corrections (required for §3's text to land coherently)

1. **Split and scope the §16.9 Draft status note** (it covers four rules as one
   block): `pkg.cexport` / `pkg.cexport.eligible` / `pkg.cexport.signature` →
   **implemented** (alias emission; `--library` + `bn_init`/`bn_entry`; the
   startup entry move; the native C-entry thunks — all landed and
   conformance-green). **`pkg.link-placement` stays Draft/pending** (no
   `section`/`link_at` implementation exists; tracked as a linker concern).
2. **The three sibling stale-status sites** that would otherwise contradict
   (1): the §16b chapter-header badge ("outbound `#[c_export]` … Draft/pending"),
   the §16.7 annotation-namespace bullet (`c_export` implemented;
   `section`/`link_at` still pending), and **§17.3.2's status note** —
   `prog.entry.glue` / `prog.init.idempotent` and the hosted + library entry
   legs of `prog.entry.pluggable` are implemented (`bn_init`/`bn_entry`, the
   startup entry move); the placed freestanding `_start` leg remains pending on
   link-placement.
3. **`pkg.cexport` wording**: "an additional, unmangled C symbol **aliasing**
   that function" → "…**denoting a C entry** of that function" (§2), keeping
   the mangled-symbol-unchanged and verbatim-no-mangling clauses.
4. **`pkg.cexport.eligible`**: the spec's "only a package-public function" rule
   contradicts the **ratified** decision (a package wrapping a C library hands
   it a *private* callback; package-public is NOT required, and the
   implementation enforces only top-level-function placement). Align the rule:
   top-level functions only; visibility unrestricted.
5. **`pkg.cexport.signature`'s managed-value note**: its "a borrow for the
   call" gloss covers `@T`/`@[]T`/`@func` but is wrong for an `@Iface`
   parameter, where §18.5 `mem.param` has the **caller deliver one reference**
   that the callee releases — align the note with `mem.param` (the same
   correction §2's ABI-level identity clause relies on).
6. **Disambiguation**: the C entry is unrelated to the dual-mode **thunk** of
   `term.thunk` (mode-bridging); this proposal deliberately says *C entry*, not
   *thunk*, in normative text.
7. The §5.5 / ebnf reserved-spelling additions of §3.

## 6. Non-goals / explicitly out of scope

- **Calling** a raw C function pointer *from Binate* (`__c_call` takes a symbol
  name, not a pointer; an indirect variant would be a separate proposal).
- Per-**value** thunks (a C pointer for a closure/function value) — requires
  runtime code generation; the `void* user_data` idiom covers the use case.
- Widening the always-on entry normalization (the 2026-09-01 gate decision
  stands; `__c_entry` is the proper fix).
- VM-mode support (FFI stays compiled-only).
- Cross-thread / async-signal invocation (outside the execution model; §2 note).

## 7. Open questions for ratification

1. **Name.** `__c_entry` (recommended — says what it returns, matches the
   family's `__c_*` spelling and the "C entry" concept) vs `__c_func` /
   `__c_callback` / `__c_fnptr`.
2. **Generic instantiations.** Should `__c_entry(f[int])` be allowed (a
   monomorphized instantiation is a concrete function)? Recommend **defer** —
   v1 rejects generics via an **explicit new checker guard** (note:
   `_func_handle` has no such guard today and appears to accept a generic
   reference, failing only at link — a latent gap, raised as a todo; §8).
3. **Signature-rule sharing.** §3 reuses `pkg.cexport.signature` wholesale.
   That boundary is **fully permissive in the implementation** — a variadic or
   managed-typed function can be `#[c_export]`'d silently, though a C caller
   cannot produce a Binate slice for a variadic callee. Recommend: keep the
   rules shared; the signature *lint* is already tracked as a c_export post-MVP
   follow-on in `claude-todo.md` (the spec itself anticipates none — if lint
   anticipation is wanted in spec text, add it to `pkg.cexport.signature` as a
   further companion edit).
4. **Result-type bikeshed.** `*uint8` (recommended: the language's `void*`
   analog; C function-pointer types are not expressible in Binate, and the
   pointer's only use is being handed to C) vs introducing a dedicated opaque
   C-function-pointer type (more type safety, more machinery — deferrable
   without breaking anything, since a nominal wrapper can be added later).

## 8. Bugs and gaps surfaced by this proposal's review (raised in `claude-todo.md`)

Two **MAJOR pre-existing `#[c_export]` implementation bugs** (both would have
been silently blessed by the original draft's "returns need no adaptation" /
register-only wording — the review caught them; both are conformance gaps
against §2's C-entry contract, for the user to prioritize):

1. **LLVM backend: sub-word/bool returns lack `signext`/`zeroext`** — C callers
   on darwin-arm64 and x86-64 rely on callee-extended sub-word returns and can
   read garbage upper bits at `-O1+`. Native backends are unaffected (returns
   are canonically extended).
2. **Native C entries: narrow integer STACK args not canonicalized** on
   8-byte-slot conventions (SysV x64, AAPCS64-linux) — a C caller whose narrow
   arg lands on the stack (≥7 GP args on x64) hits the same bug class the
   register-arg fix closed; the natural-size spill exists only on
   Darwin-aarch64.

Plus one minor latent gap: **`_func_handle` accepts a generic function
reference** (no checker guard; dangles at link).

## 9. Sources

Grounded in: the implemented C-entry prefix design (done log, commit
`81b3e6d36`, incl. the ld64 fall-through post-mortem), the narrow-arg
normalization landings (`a171551d0`, `4798da30a`, `eeb7b4003`), the ratified
private-callback eligibility (`done/plan-ffi-export-detailed.md`), the
`_func_handle` precedent (checker: named-function-reference operand, `*uint8`
result — checker-only; the spec has no normative `_func_handle` rule), current
spec §16.9 / §15.8 / §5.5 / §7.8 / §18.5 / `term.thunk`, and two adversarial
reviews (spec-consistency; ABI/implementability) whose findings are folded in
throughout.
