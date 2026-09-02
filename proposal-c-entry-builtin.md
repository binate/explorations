# Proposal: `__c_entry` — a raw C-callable function pointer for any Binate function (`proposal-c-entry-builtin`)

Status: **PROPOSAL — under review, not yet ratified.** Spec design only; the
implementation is planned/executed separately. Executes the todo "native:
builtin to obtain a raw C-callable function pointer (THUNK) for ANY Binate
function" (which supersedes the c_export callback-gate scope-limitation).

## 1. Problem

The only C→Binate entry today is a `#[c_export]`-**named** function. There is no
way to hand C a function pointer for an *arbitrary* Binate function — a
`qsort` comparator, a signal handler, an event-loop callback. Two concrete
consequences:

- **The callback path is unsupported, and unsound if faked.** Taking a
  function's "address" (via its function value / `_func_handle`) reaches the
  **mangled Binate entry** — which on the native backends does **not** perform
  the C-entry narrow-argument canonicalization. AAPCS64/SysV leave the register
  bits above a sub-word integer argument **unspecified**; Binate's native
  backends keep sub-word values canonically extended internally — so a C caller
  entering the mangled entry with `int32 -5` reads a wrong (positive) value at
  `-O1+`. The per-function normalization is deliberately gated on
  `#[c_export]` (decision 2026-09-01: don't pay unconditional entry overhead to
  defend an unsupported path).
- **The workaround is clumsy**: give every callback a `#[c_export]` name and
  hand C the *named symbol* — no address-of, no passing a Binate-chosen
  function at run time.

## 2. Prerequisite concept: the **C entry** (also fixes stale §16.9 text)

The implemented `#[c_export]` mechanism (landed; the spec's "Draft / pending —
specified but not yet implemented" status and its pure-**alias** description
are both stale) is:

- **LLVM backend**: the C name is a symbol **alias** of the mangled entry
  (LLVM-emitted bodies already receive arguments per the platform C ABI).
- **Native backends** (aarch64/x64/arm32): the C name labels a **thunk** —
  narrow-GP-argument sign/zero-extension, then a **branch** to the mangled
  entry. Native Binate callers enter at the mangled entry and skip it; when no
  adaptation is needed the thunk is empty and the two addresses coincide.

The observable both realize, which the spec should define **once**:

> A **C entry** of a Binate function `f` is an entry point whose incoming
> convention is exactly the **platform C ABI** — in particular, argument
> register bits above a sub-word integer parameter's width are treated as
> unspecified and canonicalized on entry. Calling a C entry of `f` from C, with
> the C signature that `f`'s Binate signature maps to (`pkg.cexport.signature`),
> behaves identically to calling `f` from Binate. Whether a C entry shares the
> mangled entry's address is unspecified (an implementation may alias when no
> adaptation is needed and must interpose a thunk when it is).

`#[c_export("name")]` then reads: *the name denotes a C entry of `f`* (replacing
"aliasing that function"). Return values need no adaptation clause: a Binate
function's returns already satisfy the C ABI (sub-word returns are
canonically extended, which over-satisfies "unspecified upper bits").

## 3. The builtin

```
__c_entry(f)          // f: a reference to a declared function
```

yields the **address of a C entry of `f`**, as a raw **`*uint8`** — joining
`__c_call` (call a C function) and `__c_global` (address of a C global) as the
third foreign-function primitive: *hand C a Binate function*. A `#[c_export]`
is exactly the **named, eager** instance of the same concept; `__c_entry` is the
anonymous, by-address instance.

Proposed spec text (new rule in §16.9, after `pkg.cglobal`):

> `pkg.centry` — `__c_entry(f)` yields the address of a **C entry** of the
> declared function `f`, as an opaque raw pointer **`*uint8`** (§7.8
> `type.ptr.opaque-byte`) suitable for passing to C (typically as a `__c_call`
> argument) wherever C expects a function pointer of the corresponding C
> signature. The operand must be a **reference to a declared top-level
> function** — local or package-qualified, public or package-private (a
> callback is legitimately a private implementation detail) — not a method, not
> a function *value*, and not a function literal: a C function pointer carries
> **no context slot**, so a capturing value cannot be lowered to one (pass a
> context through the C API's `void* user_data` parameter instead, as C code
> does). `f`'s signature must satisfy `pkg.cexport.signature` (the same
> C-ABI-replicable rule as `#[c_export]`). The result is a **code address**:
> immortal static data — never freed, no reference count, exempt from the
> raw-borrow lifetime discipline.
>
> `pkg.centry.identity` — Every evaluation of `__c_entry(f)` for the same `f`
> in a program yields the **same address** (so C-side registration/deregistration
> by pointer works), realized like other per-entity static records
> (linker-deduplicated). Whether that address equals a `#[c_export]` name's
> address for the same `f`, or the mangled entry's, is **unspecified** — the
> guarantee is behavioral (each is a C entry of `f`), not positional.
>
> `pkg.centry.mode` — `__c_entry` is **compiled-mode only**, like
> `__c_call`/`__c_global` (the bytecode VM does no FFI).

Grammar (`binate.ebnf`, `BuiltinCall`): `"__c_entry" "(" Expression ")"` — the
same operand shape as `_func_handle`; the reference-to-declared-function
restriction is a checker constraint, not grammar. §15.8 `builtin.internal`'s
foreign-function family mention covers it alongside `__c_call`/`__c_global`.

## 4. What the thunk does (informative, for Annex B / the implementer)

For a function with no `#[c_export]` name, the compiler synthesizes the same
C-entry adaptation `#[c_export]` gets today: narrow-GP-argument
canonicalization, then a **branch/relocation** to the mangled entry — not
fall-through (ld64 links per-symbol atoms; a fall-through prefix landed in
inter-atom padding and SIGILL'd — the authoritative test is the **linked
self-compile**, not a `.o` disassembly). Emission follows the established
per-function static-record pattern (`__shim`/`__vt`/`__handle` triples):
collected at use sites, `weak_odr`/comdat-deduplicated program-wide, degenerate
(alias to the mangled entry) when the adaptation is empty. On the LLVM backend
the C entry is the mangled entry (alias), as for `#[c_export]`.

## 5. Companion spec corrections (required for §3's text to land coherently)

1. **`pkg.cexport` status**: Draft "not yet implemented" → implemented (alias
   emission, `--library` + `bn_init`/`bn_entry`, the startup entry move, and
   the native C-entry thunks are all landed and conformance-green).
2. **`pkg.cexport` wording**: "an additional, unmangled C symbol **aliasing**
   that function" → "…**denoting a C entry** of that function" (§2), keeping
   the mangled-symbol-unchanged and verbatim-no-mangling clauses.
3. **`pkg.cexport.eligible`**: the spec's "only a package-public function" rule
   contradicts the **ratified** decision (a package wrapping a C library hands
   it a *private* callback; package-public is NOT required, and the
   implementation enforces only top-level-function placement). Align the rule:
   top-level functions only; visibility unrestricted.
4. Optionally, a one-line cross-ref: the C entry is unrelated to the dual-mode
   **thunk** of `term.thunk` (mode-bridging); different mechanism, same word —
   this proposal deliberately says *C entry*, not *thunk*, in normative text.

## 6. Non-goals / explicitly out of scope

- **Calling** a raw C function pointer *from Binate* (`__c_call` takes a symbol
  name, not a pointer; an indirect variant would be a separate proposal).
- Per-**value** thunks (a C pointer for a closure/function value) — requires
  runtime code generation; the `void* user_data` idiom covers the use case.
- Widening the always-on entry normalization (the 2026-09-01 gate decision
  stands; `__c_entry` is the proper fix).
- VM-mode support (FFI stays compiled-only).

## 7. Open questions for ratification

1. **Name.** `__c_entry` (recommended — says what it returns, matches the
   family's `__c_*` spelling and the "C entry" concept) vs `__c_func` /
   `__c_callback` / `__c_fnptr`.
2. **Generic instantiations.** Should `__c_entry(f[int])` be allowed (a
   monomorphized instantiation is a concrete function)? Recommend **defer** —
   v1 restricts to non-generic declared functions, matching `_func_handle`'s
   operand rule; relax later if a use case appears.
3. **Signature-rule sharing.** §3 reuses `pkg.cexport.signature` wholesale.
   Recon found that boundary is **fully permissive in the implementation** — a
   variadic or managed-typed function can be `#[c_export]`'d silently, though a
   C caller cannot produce a Binate slice for a variadic callee. Recommend:
   keep the rules shared, and (separate follow-up, both features) either
   exclude variadic functions at this boundary or lint them
   (`pkg.cexport.signature` already anticipates a lint for
   unusable-in-practice signatures).
4. **Result-type bikeshed.** `*uint8` (recommended: the language's `void*`
   analog; C function-pointer types are not expressible in Binate, and the
   pointer's only use is being handed to C) vs introducing a dedicated opaque
   C-function-pointer type (more type safety, more machinery — deferrable
   without breaking anything, since a nominal wrapper can be added later).

## 8. Sources

Grounded in: the implemented C-entry prefix design (done log, commit
`81b3e6d36`, incl. the ld64 fall-through post-mortem), the narrow-arg
normalization landings (`a171551d0`, `4798da30a`, `eeb7b400`), the ratified
private-callback eligibility (`done/plan-ffi-export-detailed.md`), the
`_func_handle`/`_raw_func_addr` precedents (checker: named-function-reference
operand, `*uint8` result), and current spec §16.9 / §15.8 / §7.8 / `term.thunk`.
