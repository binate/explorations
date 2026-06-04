# Historical notes

Pointers to design/notes/plan docs and bug writeups that were **removed**
from `explorations/` once their substance had fully landed in the code, in
`claude-notes.md` / `claude-discussion-detailed-notes.md`, or in
`claude-todo-done.md`. Git forgets nothing: to read a removed file in full,

    git -C explorations log --all --oneline -- <filename>
    git -C explorations show <commit>:<filename>

Each entry keeps the one non-obvious nugget worth recalling, plus the
landing commit(s)/test(s). Removed 2026-06-04 unless noted.

## Removed docs

- **claude-bootstrap-plan.md** — implementation plan for the (retired
  2026-05-21) Go bootstrap interpreter. Nugget: the bootstrap-subset
  boundary and `pkg/bootstrap` I/O surface — also in `grammar.ebnf`
  (`[BOOTSTRAP]`) and `claude-plan-1/2`.
- **claude-plan-managed-headers.md** — the *3-word* `@[]T`/`@T`-header
  design, superseded by the 4-word layout (`claude-notes.md`). Nugget:
  the prefix-layout rationale — `@[]T`'s first two words match `*[]T`, so
  it reads as a raw slice with no arithmetic.
- **bug-struct-copy-refcount.md** — FIXED. By-value struct copies of
  structs with managed `@[]T`/`@T` fields missed a RefInc; fixed by
  `__copy_X` generation (commit `2052570`) + interpreter `structRefInc`
  (`78f959c`). Now covered by generated copy-constructors.
- **compiler-loop-var-stack-leak.md** — FIXED. Per-iteration `OP_ALLOC`
  for loop-body vars wasn't hoisted to the entry block, leaking one slot
  per iteration. Guarded by `conformance/268_compiler_loop_var_leak`.
- **native-aa64-bugs.md** — postmortem of the native-aa64 unit sweep, all
  RESOLVED (final 29/29 unit + 285/285 conformance). Nuggets: imm12 STR
  truncation, `ARM64_RELOC_PAGE21` `r_extern`, sub-word multi-return, and
  cross-pkg by-value struct ABI; the regPool-saturation follow-up.
- **plan-codegen-byval-spill-hoist.md** — LANDED (`440485b0` + `d9800429`,
  2026-06-02). A call-site byval-spill `alloca` leaked per loop iteration;
  fix hoists it to the entry block (LLVM idiom: allocas belong in entry).
- **plan-const-nonint.md** — superseded by `plan-const-readonly.md`
  (Phase A shipped scalar-only, Phase B composites CANCELED, Phase C
  became `var readonly *T`). Nugget: Binate `const` is an immutable
  *variable*, not a reducible-to-immediate constant (also in
  `claude-notes.md`).
- **plan-ir-gen-typed-literals.md** — shipped 2026-05-23 (A1–A5, B).
  Nugget: the bignum→`IntVal` conversion rules (two's-complement handling
  for `int64`-min and uint64-only positives).
- **plan-managed-fields.md** — the ast.bni raw→managed field migration,
  shipped. Nugget/rationale (also in the coding guide): a raw-slice field
  dangles when its backing `@T` is freed, so every managed-struct field
  with separate backing must itself be managed.
- **plan-self-type.md** — COMPLETE 2026-05-13 (commits `dca093f`,
  `b780acd`, `77369e7`, `4c33df7`; canonical spec is in `claude-notes.md`).
  Nuggets: `TYP_SELF` singleton identity, substitution-under-aliases, and
  the object-safety rejection rationale.
- **plan-string-literals.md** — shipped; string literals are now static
  `@[]const char` globals (`TYP_STRING`/`VAL_STRING`/`bn_string_to_chars`
  all gone). Nugget: a null `backing_refptr` makes them immortal via the
  no-op RefInc/RefDec on null.
- **plan-struct-temp-cleanup.md** — RESOLVED (test 226, no xfail). Writeup
  of four failed statement-level attempts before the principled slow-path
  in `design-refcount-axioms.md`. Nugget: struct-return-move skips the
  dtor but left an extra field RefInc (the root cause).
- **plan-vm-64bit-on-32bit.md** — COMPLETE / arm32-validated 2026-05-28
  (commit trail in `claude-todo.md`). Nuggets: "register size == host word
  size; pay for 64-bit only on 64-bit hosts," and the single-post-pass
  width inference with the `BC_RETURN` exception.
- **plan-vm-compiled-closure-dtor.md** — DONE 2026-06-03 (binate
  `0a0d00af`; `conformance/550_func_value_capture_released`). Nugget: why
  a dedicated dtor-handle sentinel was used rather than reading `rec[0]`.
- **slice-operations-analysis.md** — Phase-2 analysis, all conclusions
  shipped (slice ops lowered to IR primitives, `append` removed, raw-slice
  subslice copy fixed). Nugget: "lower slice ops in IR gen, not per
  backend" because slice layout is a language-level contract (also in
  `runtime-abstraction-plan.md` §3.1).

- **ir-backend-cleanup-plan.md** — the work plan for multi-backend
  support; shipped (`TargetInfo`/`--target`, layout extraction, slice-op
  inlining, runtime abstraction, and the ARM backend — native aa64/x64 +
  arm32 lanes pass conformance). The durable rationale lives in
  `ir-backend-guidelines.md`. (Was a CLAUDE.md key reference.)
- **layout-extraction-plan.md** — plan to move layout computation
  (`SizeOf`/`AlignOf`/`FieldOffset`, struct padding, target-parameterized
  pointer/slice sizes) out of the LLVM backend into `pkg/types` for all
  backends + the interpreter; shipped (`StructLayout`/`FieldLayout`/
  `TargetInfo`, 32-bit targeting). Its "Future Work" section flagged a
  runtime-extensible type universe for interpreter byte-level interop.
  Rationale in `ir-backend-guidelines.md`. (Was a CLAUDE.md key reference.)

## Removed profiling snapshots

Point-in-time profiles whose recommended fixes all landed; kept only for
the root-cause nuggets.

- **notes-profiling-bnc-2026-04-29.md** — `-O0` baseline. Nugget:
  `addStructDef` O(M·N) remangling defect, fixed in `c884838`.
- **notes-profiling-bnc-followup-2026-04-29.md** — `-O2` v3. Nugget:
  `linkonce_odr`→`weak_odr` dtor-discard root cause (`65cb258`); `f08ddcb`
  noted as a possible regression.
- **notes-profiling-bnc-followup-2026-05-01.md** — v4 self-compile profile
  (RefInc/RefDec inlined away; clang ~80% of wall). The `-g`-emits-invalid-IR
  bug it flagged is since FIXED (see `claude-todo-done.md`,
  `addDbgToLastLine` label-line detection).
- **notes-profiling-bni-2026-04-30.md** — bni `fib(36)` baseline. Nugget:
  per-`VMFunc` CallCache replacing the per-call linear `LookupFunc` scan
  (`6c8e0c0`, −30% wall) and its REPL invalidation design.
- **notes-profiling-bni-followup-2026-05-01.md** — final bni profile
  (cumulative −43% wall). Remaining levers noted: dispatch-loop threading,
  BoundsCheck elision.
- **notes-profiling-bootstrap-2026-05-01.md** — profile of the retired Go
  bootstrap interpreter; findings (newEnv allocation / GC pressure) are
  moot for code that no longer exists.
