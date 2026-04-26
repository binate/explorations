# Plan: Method Receivers (no interfaces)

## Goal

Add method-receiver syntax to the language. Functions declared with a
receiver participate in dotted-call lookup (`obj.Method(...)`); method
calls lower to ordinary function calls with the receiver passed as the
first argument. **Interfaces, `impl` declarations, and dynamic dispatch
are out of scope** — methods are static; resolution is fully resolved at
type-check time.

## Decisions (ratified)

1. **Bootstrap parity.** Implement in both the bootstrap (Go) and
   self-hosted side. Otherwise the self-hosted code can't use methods
   itself, which defeats the purpose. Pattern matches the const and
   raw-slice-syntax rollouts.

2. **Receiver kinds.** Three forms: value `T`, raw pointer `*T`,
   managed pointer `@T`. Const variants (`*const T`, `@const T`) come
   for free since const has landed.

3. **Auto-deref / receiver smoothing.** One level of auto-deref
   (Go-style). At a call site `obj.M()`, if the receiver of `M` is
   `*T`, accept `obj : T` (take address) or `obj : @T` (extract data
   pointer). Conversions follow `claude-notes.md:828` — safe direction
   only:
   - `@T` → `*T` → `*const T`
   - `@T` → `@const T` → `*const T`
   - any pointer → value (by copy)
   - never `*T` → `@T` implicitly

4. **Naming: fully qualified.** Method `M` on type `Point` in package
   `geom` is named `geom.Point.M` in the IR. The mangler converts dots
   to `__`, yielding the C symbol `bn_geom__Point__M`. This decision
   prepares the eventual move to fully-qualified function names
   (existing TODO: "Lift function-name qualification into IR").

5. **Null receivers.**
   - Value receiver `T`: under the hood `*const T`, **never null**
     (compiler guarantees).
   - Raw pointer `*T` / `*const T`: **may be null**. The method call
     itself is fine — the receiver is just passed as the first arg.
     If the method body dereferences the receiver (field access, etc.)
     and it's null, that's a normal null-pointer deref crash, the same
     contract as any other deref of `*T`. Nothing method-specific.
   - Managed pointer `@T` / `@const T`: by language rules `@T` is
     non-null at the language level, so this is moot.
   - Users who want a nullable value-style receiver pick `*const T`.

6. **No method overloading.** One method per `(receiver-base-type,
   name)` pair. Re-declaration is a duplicate-decl error.

7. **No methods on aliases or anonymous types.** Methods require named
   types (Go's rule, `claude-notes.md:555`). Methods on `type Celsius
   float64` are allowed; methods on `type byte = uint8` are not.

8. **Method receivers cannot themselves be methods.** No nested
   declarations; no methods on function types, slice types, etc. Only
   on named structs / named primitives / named arrays defined in the
   current package. (Cross-package: cannot add methods to types from
   another package — same as Go.)

## Out of scope

- Interfaces (`type X interface { ... }`)
- `impl` declarations
- Dynamic dispatch / vtables
- Generic methods (`func (r T) M[U any](...)`)
- Method values (`x.M` as a first-class function value)
- Method expressions (`T.M` as a function value)
- Embedded fields contributing methods to outer structs

## Layout

Method `M` on receiver `(r RT) Point` lowers to a function:

```
geom.Point.M(r RT, args...) ret
```

Receiver is just the first parameter. No special calling convention.

For value receivers (`T`), the parameter is `*const T` under the hood
(never null). The compiler takes the address at the call site.

## Implementation Stages

### Stage 1: Grammar and parser (bootstrap + self-hosted)

**Goal**: parse `func (r RT) Name(...)` declarations into AST. No type
checking, no calls yet.

**Changes**:
- Grammar: flip `MethodDecl` from `[DEFERRED]` to live; add to
  `TopLevelDecl`.
- `bootstrap/parser/parser.go`: extend `parseFuncDecl` to detect a
  receiver (peek for `(` after `func`). Build a `MethodDecl` AST node
  (or extend `FuncDecl` with optional `Receiver`).
- `pkg/parser/parse_decl.bn`: same change.
- `pkg/ast` and `bootstrap/ast`: `Receiver { Name *[]char; Typ
  TypeExpr }` field on the func decl node.
- `pkg/parser` test: round-trip parse of `func (p *Point) Translate(x
  int)`.

**Negative tests**: receiver with multiple params; receiver with no
name; receiver type that's not a named type.

### Stage 2: Type checker — declaration side

**Goal**: register methods on their base type. Reject duplicates;
reject methods on aliases / unnamed / cross-package types. Method
bodies type-check with the receiver in scope.

**Changes**:
- Symbol table: each named type carries a method set
  `{name → Func}`. Adding a method calls `LookupLocal` on the type
  and inserts. Duplicate name → error.
- `bootstrap/check`: validate receiver type is a named type defined
  in the current package; bind receiver name in the function's local
  scope.
- `pkg/check`: same.
- `Func` node carries the receiver type (or nil if free function) so
  later stages can find it.

**Tests**: method on struct, method on `type Celsius int`, method on
struct with managed fields. Negative: method on alias, method on
external type, duplicate methods, methods on slice/anonymous types.

### Stage 3: Type checker — call site

**Goal**: resolve `obj.M(args)` to a method call, applying receiver
smoothing.

**Changes**:
- When checking `Selector(Call)` of form `obj.Name(args)`:
  1. If `obj` has a field named `Name`, check `Name` is callable
     (already supported — no method change).
  2. Otherwise: walk the receiver-method lookup. Strip one level of
     `*`/`@` from `obj`'s type if needed. Look up `Name` in the
     base type's method set.
  3. Apply receiver smoothing — convert `obj` to the receiver type per
     the conversion table in Decision 3.
  4. Emit a normal call with `obj` as the first arg.
- New AST node? Probably not — keep this as `EXPR_CALL` whose callee
  is a synthetic function reference. Tag the call with the resolved
  method `Func`.

**Tests**: pointer receiver from value, value receiver from pointer,
managed receiver from managed, smoothing transitivity. Negative:
trying to call a `@T` method on a `*T`.

### Stage 4: Mangler — fully-qualified method names

**Goal**: extend `mangle.FuncName` to convert all dots to `__`, so
`geom.Point.M` → `bn_geom__Point__M`.

**Changes**:
- `pkg/mangle/mangle.bn`: in `FuncName`, replace every `.` in the name
  with `__` after the package-prefix logic.
- `bootstrap/mangle/...`: same.
- Unit tests for the new shape.

### Stage 5: IR generation

**Goal**: lower method declarations and method calls.

**Changes**:
- `pkg/ir/gen_decl.bn`: when generating a method, name it
  `<TypeName>.<MethodName>` (intra-package) or
  `<pkg>.<TypeName>.<MethodName>` (cross-package). Receiver becomes
  param 0.
- `pkg/ir/gen_call.bn` (or wherever method calls land): when the AST
  node is a method call, emit the receiver-conversion ops first, then
  call. (Should fall out of Stage 3 leaving the method as a normal
  call.)

**Tests**: IR-gen unit tests for method decl and call.

### Stage 6: Codegen / VM / interpreter

**Goal**: get methods running in all four execution modes.

**Changes**:
- LLVM backend: should be transparent — the IR already names the
  function and emits a call. Verify mangling lines up.
- Bytecode VM: should be transparent — function-table lookup uses
  fully-qualified names already (`mangle.QualifyName`).
- ARM64 native backend: same.
- Bootstrap interpreter: extend `eval` for method calls. Bind receiver
  in the new frame as the first parameter.

**Tests**: conformance test exercising methods in all basic modes.
Cover value, `*T`, and `@T` receivers; cover smoothing.

### Stage 7: Conformance test suite

- `321_method_basic` — `func (p *Point) Translate`
- `322_method_value_receiver` — value receiver, called on pointer
- `323_method_managed_receiver` — `@T` receiver
- `324_method_smoothing` — auto-conversion `@T` → `*T`
- `325_err_method_alias` — negative: method on type alias
- `326_err_method_external` — negative: method on imported type
- `327_err_method_duplicate` — negative: re-declared method
- `328_err_method_anonymous` — negative: method on unnamed type

### Stage 8: bootstrap-subset.md update

Move "Methods and Impl Declarations" — split into two: methods are
**now in the subset**; `impl Type : Interface` remains deferred.

### Stage 9: Migrate self-hosted code

Convert opportunistically — start with one obvious candidate (e.g.,
`buf.CharBuf` operations as `func (b *CharBuf) Write(...)`). Not
required for the feature to land; track as a follow-up.

## Open questions / pinned for later

- **Method values** (`x.M` as a value). Out of scope here; depends on
  function-values feature being designed.
- **Method on a type that's already used in `.bni`**. The `.bni` would
  need to declare the method too (visibility rule). Same mechanism as
  function visibility checks (test 235/236). Should fall out of Stage
  2 with no extra work.

## Other ratified decisions

- **`_` receiver name** is allowed — same semantics as `_` parameter
  names: an explicit indicator that the receiver isn't used in the
  method body. Type-checker treats it like any other unused-name.

## Order of work

Stages 1 → 2 → 3 → 4 → 5 → 6 sequentially. Conformance tests added
incrementally per stage where applicable. Run `conformance/run.sh
basic` and `scripts/unittest/run.sh boot` after each stage. Run
`conformance/run.sh all` and full unit tests before declaring the
feature complete.
