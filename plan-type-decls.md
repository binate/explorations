# Plan: single-source-of-truth type declarations

Removes the .bni/.bn duplication for type definitions that triggered
the Stage 4 silent miscompile (see CRITICAL section of
claude-todo.md, binate `0d0f35b7`).

## Current state

Both `.bni` and `.bn` may declare the same type:

```binate
// pkg/foo.bni
type S struct { a int; b int; c bool }
func MakeS() S

// pkg/foo/foo.bn
type S struct { a int; b int; c bool }     // duplicated, must match by convention
func MakeS() S { ... }
```

Failure mode: the type checker uses one source (`.bni` fields), pkg/codegen
uses the other (`.bn` layout via `moduleStructs` first-wins dedup at
`gen_module.bn:174`).  When the two disagree, the program type-checks
but emits machine code against the wrong layout — silent miscompile.

## Proposed model

A type is declared in EXACTLY one place:

| Form                              | Visibility         | Field access     |
| --------------------------------- | ------------------ | ---------------- |
| `type S struct { ... }` in `.bni` | Fully exported     | yes              |
| `type S` in `.bni` + `type S struct { ... }` in `.bn` | Opaque export | no — `*S`/`@S` only |
| `type S struct { ... }` in `.bn`  | Package-private    | yes (in-pkg)     |
| (nothing)                         | doesn't exist      | n/a              |

Forward-decl form (`type S` with no body) is new syntax.  It declares
the type's existence but not its layout — callers can hold pointers
or managed handles, but cannot read fields, take SizeOf, or
zero-initialize the type.

## Phases

### Phase 1: parser + AST

- Parser accepts `type S` (no body, no `=`) as a forward declaration.
- Optional type-params still allowed (`type S[T any]` forward-decl —
  unlikely common but consistent).
- `ast.Decl.IsForward bool` distinguishes forward-decl from
  full struct/alias/named.  `TypeRef == nil && !IsAlias &&
  IsForward == true`.

### Phase 2: type checker

- Forward decl creates a TYP_OPAQUE (new) or TYP_NAMED-with-no-
  Underlying entry.
- `s.field` access on opaque-typed value errors: "cannot access
  field on opaque type".
- `SizeOf` on opaque type errors (would need layout).
- `&s` to get `*Opaque` works.  `make(Opaque)` errors (needs layout).
- Pointer/handle types `*Opaque` / `@Opaque` work normally
  (pass-around, dispatch, etc.).
- If both `.bni` and `.bn` have full definitions: ERROR.  (Initially
  may be a WARN with deprecation note; flip to ERROR after the
  cleanup pass.)
- If `.bni` has forward-decl + `.bn` has full definition: the `.bn`
  body provides the layout; callers see opaque type.
- Symmetric rule for named-type (non-struct) declarations.

### Phase 3a: forward-decl-safe bni loading — LANDED 2026-05-29 (binate `7a6af095`)

- `pkg/types/bni_scope.bn::resolveTypeDeclInScope` short-circuits
  on `d.IsForward`, leaving the TYP_NAMED placeholder's
  Underlying nil rather than crashing on the nil TypeRef.
- Forward decls in `.bni` no longer break cross-package loading.
- `gen_module.bn`'s struct registration already skips forward
  decls naturally (every site filters on `d.TypeRef != nil &&
  d.TypeRef.Kind == ast.TEXPR_STRUCT`), so no IR-gen change was
  needed for Phase 3a.

### Phase 3b: cross-package opaque-handle export — DEFERRED

The forward-decl + body pattern still doesn't work cross-package
because of a signature/body type-resolution split:

  ```binate
  // pkg/handle.bni
  type Handle
  func New() *Handle

  // pkg/handle/handle.bn
  type Handle struct { value int }
  func New() *Handle { var h Handle; return &h }
  ```

At callers: `*Handle` resolves to a pointer to the placeholder
TYP_NAMED (no underlying) → emitted in LLVM as `ptr` (opaque).
At impl: `*Handle` resolves to a pointer to the TYP_NAMED with
the struct filled in → IR-gen emits as `*%bn_pkg__handle__Handle`
(typed pointer).  The `define ... ptr @New(...)` declaration and
the `ret %struct` body disagree, and clang rejects the module.

Two possible fixes (pick one when Phase 3b resumes):

1. Re-resolve signatures after the body fills in Underlying.
   The signature would then see the full struct on both sides.
   Loses the encapsulation benefit (callers see the full layout)
   but matches the existing duplicated-struct pattern.

2. Make pkg/codegen treat `*Opaque` and `*FullStruct` as
   structurally interchangeable at function-signature time —
   both emit as `ptr` (opaque pointer).  Preserves callers' view
   of the opaque type but requires every emit-side use of the
   struct-typed-pointer form to switch to `ptr`.  Bigger change,
   more places to touch.

Tracked here so a future return-to-Phase-3b knows the shape.

### Phase 4: cleanup (incremental, future commits)

Each currently-duplicated struct gets converted to ONE of the three
forms above:

- `pkg/native/common::CallConv` etc. — fully exported (used as
  values across the .bni boundary).  Move to `.bni`-only.
- `pkg/rt::ManagedSlice` — same.
- Anything used only in-package via the impl file — `.bn`-only.
- Anything currently exposed via `*T` but where callers shouldn't
  peek (e.g. parser internals?) — forward-decl + `.bn` body.

Cleanup happens package-by-package post-Phase 3.

## Open design questions

1. **Backwards compatibility window**: phase 2 starts as WARN or
   ERROR for duplicate definitions?  WARN gives time to migrate
   without breaking the tree; ERROR forces immediate cleanup but is
   safer (no silent miscompile in the WARN window).

2. **Forward-decl syntax**: `type S` alone (no body, no `=`) — does
   this conflict with the existing distinct-type form
   `type X T` (where X is a new named type with underlying T)?  If
   the parser sees `type X` followed by SEMICOLON, that's
   unambiguous; if followed by an IDENT, it's the existing
   distinct-type form.  Should be OK but want to confirm.

3. **What about other declaration kinds?**
   - Functions: signature-only in .bni, body in .bn.  Already
     single-source (no duplication issue).  Untouched.
   - Variables: currently `.bni` declares `var X T` (decl only),
     `.bn` declares `var X T = init` (def with init).  Probably
     already single-source.  Worth confirming.
   - Constants: `const X = 42` in .bni IS the definition; .bn
     doesn't redeclare.  Already single-source.
   - Type aliases (`type X = Y`): same model as structs.  Forward-
     decl of an alias makes no sense; if alias is in .bni, .bn
     can't redeclare; if alias is private, only in .bn.

4. **Methods on forward-declared types**: can `.bni` carry
   `func (s @S) M()` declarations on a forward-declared S?  Yes —
   that's the only way to give callers anything to do with the
   opaque type.  Method receivers can be pointers/handles to
   opaque types.

5. **Cross-pkg field access through opaque type**: enforce that
   callers can't do `myOpaque.f` even when the field is technically
   present in the .bn body.  This is the encapsulation benefit.

6. **Generic types**: same model — `type Container[T any]` in .bni
   is a forward decl; full body in .bn (or in .bni for fully
   exported generics).  Per plan-generics.md, generics get
   monomorphized per-package, so opaque generics may not make
   sense — needs thought.

## Sizing

- Phase 1 (parser + AST): small, <100 lines.
- Phase 2 (type checker): medium, ~200–400 lines including tests.
  Touches `check_decl.bn`, `check_expr.bn`'s field access path,
  scope tables.
- Phase 3 (IR-gen): small once Phase 2 lands cleanly.
- Phase 4 (cleanup): ~10–20 packages × small-to-medium per pkg.
  Independent of Phase 1–3 once the language change lands.

Per user preference (2026-05-29): "Language first, cleanup
incrementally" — Phase 1–3 in one coordinated series, Phase 4 as
follow-up commits.
