# Plan: Debug Lifecycle Hooks (pre-copy, pre-destruction)

## Overview

Add annotation-based pre-copy and pre-destruction hooks for named struct
types. Controlled by a compiler flag (`--debug-hooks`). Zero overhead
when disabled.

## Syntax

```binate
#[pre_copy(myPreCopy), pre_destroy(myPreDestroy)]
type Foo struct {
    Data @[]char
}

func myPreCopy(dst *uint8, src *uint8) { ... }
func myPreDestroy(ptr *uint8) { ... }
```

## Implementation Steps

### 1. Parser: parse annotations on type declarations

The parser already supports `#[...]` syntax per the grammar. Need to:
- Store parsed annotations on `ast.Decl` for type declarations.
- Recognize `pre_copy` and `pre_destroy` annotation names.
- Extract the function name argument from `pre_copy(funcName)`.

If annotations are not yet fully implemented in the parser, add minimal
support for `#[ident(ident)]` on type declarations.

### 2. Type system: store hook info

Add optional hook function names to the `Type` struct (or a side table):
- `PreCopyFunc @[]char` — name of pre-copy hook function (empty if none)
- `PreDestroyFunc @[]char` — name of pre-destroy hook function

These are populated during type checking from the parsed annotations.

### 3. Compiler flag: `--debug-hooks`

Add a CLI flag. When set, a global `debugHooks bool` is true. The
codegen checks this flag when generating copy constructors and
destructors.

### 4. Copy constructor generation: call pre_copy hook

In `genStructCopyWithName` (gen_copy_emit.bn), when `debugHooks` is true
and the type has a `PreCopyFunc`:
- After the raw data is in the destination (the copy constructor is
  called on the destination pointer, which already has the source's bytes)
- Before walking fields to RefInc
- Emit: `call void @pkg__precopyfunc(i8* %dst, i8* %src)`

Note: the copy constructor takes `(ptr *uint8)` — only the destination.
The source is not normally available. Options:
- Change copy constructor signature to `(dst *uint8, src *uint8)`
- Or: the pre_copy hook is called at the CALL SITE, not inside the
  copy constructor. This is simpler — the call site has both pointers.

**Recommendation**: emit the hook call at the call site (in
`emitStructCopy`), not inside `__copy_X`. This avoids changing the
copy constructor's signature.

### 5. Destructor generation: call pre_destroy hook

In `genStructDtorWithName` (gen_dtor_emit.bn), when `debugHooks` is true
and the type has a `PreDestroyFunc`:
- Before walking fields to RefDec
- Emit: `call void @pkg__predestroyfunc(i8* %ptr)`

This is simpler than pre_copy — the destructor already takes `(ptr *uint8)`.

### 6. Force managed status

When `debugHooks` is true and a type has either hook annotation:
- `NeedsDestruction` returns true for the type (even if no managed fields)
- Copy constructor and destructor are generated for the type

This ensures the hooks are called even for structs that wouldn't normally
need copy/dtor.

### 7. Cross-package hooks

Hook functions must be in the same package as the type. For cross-package
types, the hooks are in the type's package. The mangled name follows the
standard convention: `bn_pkg__funcname`.

## Not in scope

- Runtime-configurable hooks (compile-time only)
- Hooks on non-struct types
- Hooks on anonymous structs
- Multiple hooks of the same kind on one type
