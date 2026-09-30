# Self-driven bug fixing

A standing procedure for a worker session that fixes bugs from `claude-todo.md` on its own, without
checking in, until it has a batch ready to land.  Parameters, given when the procedure is started:

- **N** — the number of bugs to fix before asking to land the batch (e.g. 10).
- **Worktree / branch** — the session's own `binate` worktree and work branch (e.g.
  `temp-binate-7` on `work-7`), which it uses for all work except landing.

## The instructions

As the user gave them (with N in place of the number):

> - claim a bug (likely a MAJOR one) from the todo file (in explorations/); mark it as claimed in the
>   file (and commit/push explorations). pick a bug that seems reasonably self-contained and that you
>   can fix on your own.
> - fix the bug (and keep the commit on your work branch) and get it reviewed adversarially (repeating
>   until reviewed clean)
> - if the bug turns out to be overly complex, or requires interaction/major decisions from me, you
>   can either unclaim it (maybe adding notes to the todo file as necessary) or save your work to a
>   temporary branch and put the bug on the back burner until later (see below), and claim another bug
>   (and fix it, etc.)
>
> Repeat the above until you've fixed N bugs. At that point, ask me for permission to land the batch.
> (Don't pause and ask me for anything until this point -- you're self-driving!) After that, we can go
> over any bugs that you back-burnered.

And one addition:

> after each fix/commit is finalized (reviewed clean), rebase on top of binate/ main (if main has
> moved). (If the rebase was nontrivial, re-run hygiene, and renumber conformance tests, etc., if
> necessary.)

"Don't pause and ask" covers the fixing phase only.  It does not lift the landing rules: nothing goes
to `main` until the user approves the batch, and that approval covers only that batch.

## Working practice

The rest of this document is how the procedure has been carried out, following CLAUDE.md.  It
is not part of the user's instructions.

### Per bug

1. **Claim first.**  Mark the todo entry `🟡 IN PROGRESS (claimed <date>, work-N/session)` and
   commit + push `explorations/` BEFORE writing any fix code.  A claim added after the fix protects
   nothing: another session can pick up the same entry in between.
2. **Fix it at the root, with tests.**  Add a conformance test for runtime behaviour, or a unit test for a
   checker / IR / codegen property.  Check that the new test fails without the fix: revert the fix's
   files in the worktree, run the test, then restore.
3. **Validate.**  Run the unit tests of every changed package, including packages downstream of an IR
   shape change (codegen, vm, native/*).  Run the relevant conformance subsets on every backend the
   change can affect: LLVM, VM, and the native aa64, x64 and arm32 backends (the `native` in the mode
   name matters).  Run hygiene on the exact commit.
4. **Adversarial review, until clean.**  Keep it to one or two lenses (for example correctness, and
   behaviour changes plus test coverage).  Fold each round's follow-ups into the commit they fix
   (`fixup!` / `amend!` + autosquash) rather than stacking extra commits, then re-review.
5. **Rebase onto `main` if it moved**, and redo hygiene / renumber conformance tests if the rebase was
   nontrivial.

### What the review turns up

- **A new bug that is not part of this fix:** add a todo entry (symptom, reproduction, suspected
  cause, which test covers it).  Don't silently fix it inside an unrelated commit, and don't drop it.
  Fix it now only if it blocks the current fix.
- **A question only the user can answer** (a spec decision, a scope call): record it as a
  `🔴 NEEDS DECISION` todo entry with the options and a recommendation, and keep going.  These, and the
  back-burnered bugs, are the agenda for after landing.
- **A critical or major defect** is still raised explicitly per CLAUDE.md, in the todo file, but
  without pausing the batch.

### Don'ts

- Don't edit the worktree while a background validation run is reading it.  Draft changes in the
  scratchpad, or wait for the run to finish.
- Don't carve a hard part out of a fix and call it done.  Back-burner or unclaim the bug instead,
  with notes.
- Don't count a bug as fixed until its commit is reviewed clean.

### At N fixed bugs

Ask to land the batch.  List the commits in order, and give the validation run on the rebased batch
(conformance per mode, unit tests, hygiene).  After approval, land it per the Landing Procedure in
CLAUDE.md.  Then move the landed entries to `claude-todo-done.md`, and go over the back-burnered bugs
and the NEEDS DECISION items with the user, one at a time.
