# Code Hygiene Checklist

Run these checks before committing significant new code or after file splits/refactors. See `binate-coding-guide.md` for the full conventions.

Automated scripts in `binate/scripts/hygiene/`:
- `file-length.sh` — file size check (warns >500, errors >600)
- `line-length.sh` — line length check (>100 chars)
- `test-coverage.sh` — missing test file check (with whitelist)
- `conformance-test-numbers.sh` — flags conformance tests with duplicate `NNN` prefixes
- `lint.sh` — runs `cmd/bnlint` over all `pkg/` and `cmd/` targets; fails on any diagnostic
- `bni-doc.sh` — first-approximation check for the `.bni` godoc rules (package-level doc + doc above each top-level func/type/const)
- `file-format.sh` — no trailing whitespace; files end with a final newline; alphabetical import groups in `.bn`/`.bni`
- `naming.sh` — first-approximation check that exported `.bni` symbols start uppercase (with a `naming.whitelist` for deliberate exceptions)
- `bn-doc.sh` — first-approximation check that every top-level `func`/`type`/`const`/`var` in a `.bn` file has a godoc-style comment above (skips `_test.bn` files)

---

## 1. File size

Non-test `.bn` files: **500 lines soft limit, 600 hard ceiling** (including comments and blank lines). Test files may be longer.

```
find pkg/ cmd/ -name '*.bn' -not -name '*_test.bn' | xargs wc -l | sort -rn | head -20
```

If a file exceeds 500 lines, split it along natural boundaries (section comments like `// ---`).

## 2. Line length

No lines over **100 characters**.

```
find pkg/ cmd/ -name '*.bn' -o -name '*.bni' | xargs awk 'length > 100 {print FILENAME ":" NR ": " length " chars"}'
```

Fix by breaking long conditions into multiple `if` statements, extracting subexpressions into variables, or wrapping comments.

A line may opt out by ending with `// LONG-LINE ALLOWED`. Use sparingly — only when splitting or shortening the line is impractical (e.g. a long error-message string literal that Binate can't currently split across lines).

## 3. Test file correspondence

Every non-test `.bn` file must have a matching `_test.bn` file. This applies to both `pkg/` and `cmd/` directories.

```
for f in $(find pkg/ cmd/ -name '*.bn' -not -name '*_test.bn'); do
    testf="${f%.bn}_test.bn"
    if [ ! -f "$testf" ]; then echo "MISSING TEST: $f"; fi
done
```

This includes `cmd/` packages — `main` packages **must** be unit tested. As much logic as possible should be extracted from `main()` into helper functions so it can be tested. Test files use `package "main"` and can test helper functions like any other package.

When splitting a source file (e.g., `foo.bn` into `foo.bn` + `foo_bar.bn`), split the test file to match. Shared test helpers (like `fail`, `readInst`) go in one test file and are visible to sibling test files in the same package.

## General commenting guidelines

- **Function comments** (both `.bni` and `.bn`) should specify any pre- and post-conditions that aren't implied by the function signature. Examples: "fd must be a valid open file descriptor," "the returned slice is valid only while the argument is live," "the caller is responsible for closing the returned fd."
- **Type and data structure comments** should be explicit about ownership relations and semantics. If a struct holds a raw pointer to data owned by something else, say so. If a field is expected to be non-nil after initialization, say so. If two fields alias or share backing storage, document it.

## 4. Interface file comments (`.bni`)

Every `.bni` file must have:
- A **package-level doc comment** before the `package` declaration.
- A **godoc-style comment** above every exported function, type, and constant (or constant group).

```
# Quick check: functions without a preceding comment line
for f in $(find pkg/ -name '*.bni'); do
    awk '/^func / && prev !~ /^\/\// {print FILENAME ":" NR ": " $0} {prev=$0}' "$f"
done
```

## 5. Implementation file comments (`.bn`)

- **Every** top-level `func`, `type`, and `const` (or `const ( ... )` group) needs a godoc-style comment immediately above. No "trivial" carve-out — in practice nearly every function has at least one pre-/post-condition, lifetime, ownership, or aliasing consideration that the signature alone doesn't convey, and the carve-out invites omitting comments precisely on the functions that need them most.
  - Specifically call out: whether a returned managed-slice or managed-pointer is a subslice/alias of an argument (and thus shares backing and mutations), a copy, or a fresh allocation; whether the caller is responsible for closing/freeing returned resources; what happens on failure (returns nil? returns a sentinel? aborts?).
- Use inline comments for non-obvious logic, invariants, and "why" explanations.
- Section markers (`// --- Section Name ---`) help with navigation in larger files.

## 6. Naming conventions

- Exported symbols (in `.bni`): `CamelCase` — `TypeName`, `IsKeyword`, `Lookup`.
- Non-exported symbols: `camelCase` or `snake_case` — `parseExpr`, `emit32`.
- Constants: follow the same rule based on whether they're exported.

## 7. Bootstrap subset compatibility

Code that must run via the bootstrap interpreter has a few divergences from the
full language to watch for. The canonical, current list lives in
`bootstrap-subset.md` — refer to that doc rather than maintaining a duplicate
list here. Most of the historical caveats that used to live in this section
(no `~` operator, shift-amount typing, etc.) have closed; the remaining ones
are mostly about features the bootstrap doesn't implement, not about
constructs you have to write differently.

Note: language-level rules sometimes mistaken for bootstrap-only quirks (such
as "slices are not nillable") are documented in `binate-coding-guide.md` and
enforced by both type checkers — they apply everywhere, not just under
bootstrap.

## 8. Test runner coverage

A test that's never run is worse than no test: it implies coverage that doesn't exist, and a future regression it would have caught will go unnoticed. When a test (or test package) is added, or when a test runner is modified, confirm the runner actually exercises the test in the modes it should and that failures are properly detected.

When **adding a test (package)**:

- Run the test suite and confirm the new test/package appears in the runner's per-package output (e.g., `PASS: pkg/foo`) for each mode it's supposed to run in.
- Where practical, force the test to fail on purpose first (return a wrong value, expect the wrong output, etc.) and confirm the runner reports it as a failure. A passing test from the start can't distinguish "the test runner is fine" from "the test runner silently skips it." This is sometimes too onerous for a single test in a large package — use judgment, but at minimum make sure failures somewhere in that package would surface.
- If you add tests at a new directory nesting depth or under a new top-level layout, double-check: the test runner uses `find` to discover packages, so a new layout that doesn't match its globs is the typical way tests go silently undetected.

When **modifying a test runner** (`scripts/unittest/run.sh`, the per-mode runners, or the modeset definitions):

- Confirm the same set of packages still runs in each mode, including xfail entries — comparing the before/after output is the simplest check.
- Confirm failures are still detected: temporarily break a test (or use a known-failing one), run the modified runner, and confirm it reports failure with a non-zero exit code. Silent success after a runner change is the bug this rule exists to catch.

## 9. File formatting

Applies to authored text files (`.bn`, `.bni`, `.sh`, `.md`, `.yml`); excludes `conformance/` test fixtures.

- **No trailing whitespace.** No spaces or tabs at end of line.
- **Final newline.** Every non-empty file ends with a `\n`.
- **Alphabetical import groups.** In `.bn` / `.bni`, a contiguous run of `import "..."` lines is one group; groups are separated by blank lines (or by intervening non-import code). Each group is independently sorted alphabetically by its quoted path.

Automated by `scripts/hygiene/file-format.sh`.
