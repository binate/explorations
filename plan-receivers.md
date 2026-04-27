# Plan: Method Receivers (no interfaces)

## Status (2026-04-27)

**Feature is landed and complete across all four execution paths.**
Stages 1–8 done; Stage 5c (receiver smoothing in LLVM IR-gen) closed
the last known gap. Stage 9 (self-hosted migration) is opportunistic
and not part of feature completeness.

| Stage | Description                                  | Status   | Commits (binate) |
| ----- | -------------------------------------------- | -------- | ---------------- |
| 1     | Grammar + parser (bootstrap + self-hosted)   | DONE     | `0d088c6`        |
| 2     | Type checker — declaration side              | DONE     | `4455ce5`        |
| 3     | Type checker — call site                     | DONE     | `9a9f803`        |
| 4     | Mangler — fully-qualified names              | DONE     | `60e2f72`        |
| 5a    | IR-gen — method declarations                 | DONE     | `f08a83f`        |
| 5b    | IR-gen — method calls                        | DONE     | `a95aaeb`        |
| 5c    | IR-gen — receiver smoothing                  | DONE     | (work-1 `b58ac58`) |
| 6     | Codegen / VM / interpreter                   | DONE     | `1c1de68` (binate xfail removal); `7592647` (bootstrap interpreter) |
| 7     | Conformance test suite (322–329)             | DONE     | `0d54ae1`, follow-up in 5c commit |
| 8     | bootstrap-subset.md update                   | DONE     | `a47af21` (explorations) |
| 9     | Migrate self-hosted code (`buf.CharBuf` etc.)| TODO     | —                |

---

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

## Implementation Stages (as built)

### Stage 1: Grammar and parser — DONE (`0d088c6`)

Bootstrap (Go) and self-hosted parsers accept `func (r RT) Name(...)`.
Disambiguation: an LPAREN after `func` introduces a receiver; an IDENT
means a free function. `Recv` field on the func decl AST node is nil for
free functions and non-nil for methods.

Tests: `TestParseMethodDecl{,PointerRecv,ValueRecv,ManagedRecv}` plus a
free-function nil-Recv check. `MethodDecl` flipped to `[BOOTSTRAP]` in
the grammar EBNF.

### Stage 2: Type checker — declaration side — DONE (`4455ce5`, `4f9b63c` bootstrap)

`Method` struct (Name, RecvType, Func) and `Methods` field on NamedType
in both Go and self-hosted type systems. `collectMethodDecl` validates
the receiver (named type defined in the current package; not aliases /
builtins / imports) and registers via `AddMethod`, which detects
duplicates. `checkFuncDecl` branches on Recv — methods bind the receiver
name in the body's scope. Methods are skipped at IR-gen and at
bootstrap-interpreter top-level-decl processing for now.

Tests: bootstrap `TestCheckMethod{Pointer,Value,Managed}Recv`,
`TestCheckMethodOn{NamedPrimitive,Alias,Builtin}IsError`,
`TestCheckMethodDuplicateIsError`,
`TestCheckMethod{AndFreeFunctionSameName,SameNameDifferentTypes}OK`,
`TestCheckMethodRegisteredOnNamedType`, `TestCheckMethodBodySeesReceiver`,
`TestCheckMethodBodyTypeError` (and self-hosted equivalents).

### Stage 3: Type checker — call site — DONE (`9a9f803`, `0ac9773` bootstrap)

`tryMethodCall` dispatches `obj.M(args)` callees. Skips package-qualified
calls (`pkg.Func(...)`) by detecting an IDENT receiver bound as `SYM_PKG`.
`receiverShape` and `receiverAssignable` encode the smoothing table from
Decision 3 (and the const-direction rules in the self-hosted side).

Tests: bootstrap and self-hosted
`TestCheckMethodCall{Pointer,Value,Managed}OnPointer/Value/Managed` plus
arg-count, missing-method, raw→managed-rejected, dispatch-by-type.

### Stage 4: Mangler — fully-qualified names — DONE (`60e2f72`)

`mangle.FuncName` now treats any name containing a dot as pre-qualified
and replaces every `.` with `__`. Free-function shapes are unchanged.
Bootstrap doesn't have a mangler (it's an interpreter), so this is
self-hosted-only.

Tests: `TestFuncName{Method,MethodCrossPkg,MethodMultiDot}`.

### Stage 5a: IR-gen — method declarations — DONE (`f08a83f`)

`pkg/ir/gen_method.bn` introduces `methodQualName`, `recvTypeName`,
`methodSig`, `genMethod`. `genMethod` synthesizes a free-function-shaped
`ast.Decl` with the receiver promoted to `Params[0]` and defers to
`genFunc` for body emission. `GeneratePackage` and `GenModule` register
both free-function and method signatures and emit both bodies in their
existing passes.

Tests: `TestRecvTypeName*`, `TestMethodQualName*`.

### Stage 5b: IR-gen — method calls — DONE (`a95aaeb`)

`genCall` detects a method-call SELECTOR (receiver isn't a package alias
in scope) and routes through `tryMethodCall` → `genMethodCall`.
`genMethodCall` evaluates the receiver, prepends it as `Args[0]`, and
emits a call to the fully-qualified method name. `currentModulePkgShort`
global threads the package name from `GeneratePackage` / `GenModule`
into `genMethodCall`. `baseNamedTypeName` walks one level of `*T` / `@T`
to find the receiver's named-type name (handles both `TYP_NAMED` and
`TYP_STRUCT` since IR-gen represents named structs as the latter).

Tests: `TestBuildMethodQualName`, `TestBaseNamedTypeName{Named,Struct,
Pointer,Managed,NonNamed}`. End-to-end `322_method_basic`.

### Stage 5c: IR-gen — receiver smoothing — DONE (work-1 `b58ac58`)

After Stage 5b, the receiver was passed as-is to the call. That worked
for exact matches and for the cells LLVM happens to be lenient about
(`@T → *T`, since both lower to `i8*`), but a `*T → T` deref or a
`T → *T` take-address produced an LLVM type-mismatch. Stage 5c adds
`applyReceiverConversion` after the receiver is evaluated and before
the call is emitted:

  src `*T` or `@T`, dst `T` (value)   → `EmitLoad` (deref to value)
  src `T` (value),  dst `*T`          → take address: `lookupVar` slot
                                        for ident receivers, else
                                        materialize a temp slot
  src `@T`,         dst `*T`          → reinterpret (both `i8*` in LLVM
                                        — retype the Instr)
  matching shape                      → identity (`recvShapeMatches`
                                        fast path)

Conformance: 327 expanded to cover all four cells of the *T/value
table; 329 covers @T → *T; xfail.boot-comp marker removed.

### Stage 6: Codegen / VM / interpreter — DONE (`7592647` bootstrap)

LLVM, bytecode VM, and ARM64 native backends are transparent — they
consume the IR produced by Stage 5. Verified by running 322 across
boot-comp, boot-comp-int, and boot-comp_native_aa64.

The bootstrap (Go) interpreter required real work: a per-type method
registry (`methods map[typeName]map[methodName]*ast.FuncDecl`),
`registerMethod` in `execTopLevelDecl`, `lookupMethod` /
`valueTypeName` for receiver-driven dispatch, and a method-call branch
in `evalCall` that prepends the receiver. `isPackageSelector` is the
twin of `isMethodCallSel` for the dispatch decision.

Tests: bootstrap `TestMethod{PointerReceiver,ValueReceiver,WithArgs,
SameNameDifferentTypes}`. Conformance 322 now passes on boot too.

### Stage 7: Conformance test suite — DONE (`0d54ae1`, +Stage 5c follow-up)

Eight tests in `conformance/`:

- 322 — basic *T-on-*T (landed in Stage 5b)
- 323 — `@T` receiver chained over a linked list
- 324 — negative: method on type alias
- 325 — negative: method on builtin (`int`)
- 326 — negative: duplicate method declaration
- 327 — full smoothing table (*T-on-*T, *T-on-T, T-on-T, T-on-*T) —
  expanded in Stage 5c after IR-gen smoothing landed
- 328 — pointer-receiver mutation
- 329 — `@T → *T` smoothing (managed receiver expr on *T method) —
  added in Stage 5c

Cross-package method declaration in `.bni` (originally suggested as
326_err_method_external) is deferred — it requires `.bni` method
visibility, which is itself a follow-up.

### Stage 8: bootstrap-subset.md update — DONE (`a47af21` explorations)

Methods moved from "Not supported" to the supported list, with a note
about the LLVM-IR-gen smoothing caveat. `impl Type : Interface` remains
deferred. Method values / expressions called out as separate
not-supported items.

### Stage 9: Migrate self-hosted code — TODO

Opportunistic. Suggested first candidate: `pkg/buf.CharBuf` operations
as `func (b *CharBuf) Write(...)`. Not required for any other work; do
when ergonomic.

## Open questions / pinned for later

- **Method values** (`x.M` as a value). Out of scope here; depends on
  function-values feature being designed.
- **Method on a type that's already used in `.bni`**. The `.bni` would
  need to declare the method too (visibility rule). Same mechanism as
  function visibility checks (test 235/236).

## Other ratified decisions

- **`_` receiver name** is allowed — same semantics as `_` parameter
  names: an explicit indicator that the receiver isn't used in the
  method body. Type-checker treats it like any other unused-name.
