# Plan: single-source-of-truth type declarations

Status: COMPLETE (shipped); kept for design rationale.

Removes the .bni/.bn duplication for type definitions that triggered
the Stage 4 silent miscompile (see CRITICAL section of
claude-todo.md, binate `0d0f35b7`).

## Current state (the problem)

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

## Model (ratified)

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

Type-checker semantics for the forward-decl form:

- `s.field` access on opaque-typed value errors: "cannot access
  field on opaque type".
- `SizeOf` / `alignof` on an opaque type error (would need layout).
- `&s` to get `*Opaque` works.  `make(Opaque)` and `make_slice(Opaque, n)`
  error (need the layout).  A distinct type over an opaque base (`type A
  Opaque`) is opaque too — the gate peels to the bottom.
- Pointer/handle types `*Opaque` / `@Opaque` work normally
  (pass-around, dispatch, etc.).
- If both `.bni` and `.bn` have full definitions: ERROR (see "mismatch
  is a hard error" below).
- If `.bni` has forward-decl + `.bn` has full definition: the `.bn`
  body provides the layout; callers see opaque type.
- If `.bni` has forward-decl + NO full definition anywhere in the
  package: LEGAL — a *pure opaque type* whose layout is defined
  outside Binate (C, assembly, the runtime).  Callers see the opaque
  type (pointers/handles only).  A "dangling forward" check that
  would require a paired full definition was considered and REJECTED
  as unsound under package-at-a-time compilation (a dependency is
  seen only through its `.bni`, so its forward decl always looks
  unpaired).  See `plan-type-redecl.md` §4.
- Symmetric rule for named-type (non-struct) declarations.

`ast.Decl.IsForward bool` distinguishes forward-decl from full
struct/alias/named (`TypeRef == nil && !IsAlias && IsForward == true`).

## Mismatch is a hard error

With no duplicates remaining in the tree, the mismatch path on
mismatched bni/bn definitions is a hard error.  The duplicate paths in
`pkg/types/check_decl.bn`'s struct- and named-type handling route to
`addCheckError` (not a warning).  Same-shape duplicates remain silently
deduplicated (the loader prepends .bni decls into .bn during file
merge, so the same shape reaches collectTypeDecl twice and must be
benign).

This catches the original Stage-4-style silent-miscompile shape
(`type S struct {a, b}` in .bni + `type S struct {a, b, c}` in
.bn) at type-check time: any future re-introduction of mismatched
duplicates fails type-checking instead of slipping through.

## Cross-package opaque-handle export: gen_return.bn gotcha

The forward-decl + body pattern round-trips through the LLVM backend.
The non-obvious failure was in the return path.  Consider:

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
At impl: `*Handle` resolves to a pointer to the TYP_NAMED with the
struct filled in.  The `define ... ptr @New(...)` declaration and the
body must agree.

Root cause: `pkg/ir/gen_return.bn`'s `genReturnStmt` unconditionally
loaded struct/array allocas before returning, via

```binate
if isStructOrArrayAlloc(val) {
    val = b.EmitLoad(val, val.TypeArg)
}
```

That branch is correct for functions that return the struct value
itself (`func f() S { var s S; return s }` — load alloca → struct
value, signature emits `define %S`).  But for functions returning a
POINTER to the struct (`func f() *S { var s S; return &s }`), the
alloca pointer IS the desired return value; loading turned it into the
struct value and produced `ret %S %loaded` against a `define ptr`
signature — clang rejected.

Fix: gate the load on `ctx.Func.Results[i].Kind` being TYP_STRUCT or
TYP_ARRAY.  Functions returning pointers keep the alloca as the return
SSA value, matching the `ptr` signature.

End-to-end coverage: `conformance/512_opaque_handle_cross_pkg/`
exercises the full pattern — `type Handle` forward decl in `.bni` +
`type Handle struct { value int }` in `.bn` + opaque `*handle.Handle`
caller — and asserts `handle.Get(handle.New(42))` prints `42`.

Implementation note on bni loading: `pkg/types/bni_scope.bn::resolveTypeDeclInScope`
short-circuits on `d.IsForward`, leaving the TYP_NAMED placeholder's
Underlying nil rather than crashing on the nil TypeRef.  `gen_module.bn`'s
struct registration already skips forward decls naturally (every site
filters on `d.TypeRef != nil && d.TypeRef.Kind == ast.TEXPR_STRUCT`).

## Open / deferred design questions

1. **Forward-decl syntax** vs the existing distinct-type form
   `type X T` (X is a new named type with underlying T): if the parser
   sees `type X` followed by SEMICOLON, that's unambiguous forward-decl;
   if followed by an IDENT, it's the existing distinct-type form.

2. **Other declaration kinds** (all already single-source — no change):
   - Functions: signature-only in .bni, body in .bn.
   - Variables: `.bni` declares `var X T` (decl only), `.bn` declares
     `var X T = init` (def with init).
   - Constants: `const X = 42` in .bni IS the definition; .bn doesn't
     redeclare.
   - Type aliases (`type X = Y`): same model as structs.  Forward-decl
     of an alias makes no sense; if alias is in .bni, .bn can't
     redeclare; if alias is private, only in .bn.

3. **Methods on forward-declared types**: `.bni` can carry
   `func (s @S) M()` on a forward-declared S — that's the only way to
   give callers anything to do with the opaque type.  Method receivers
   can be pointers/handles to opaque types.

4. **Cross-pkg field access through opaque type**: callers can't do
   `myOpaque.f` even when the field is technically present in the .bn
   body.  This is the encapsulation benefit.

5. **Generic types** (needs thought): same model would make
   `type Container[T any]` in .bni a forward decl with full body in .bn
   (or in .bni for fully exported generics).  But per plan-generics.md,
   generics get monomorphized per-package, so opaque generics may not
   make sense.
