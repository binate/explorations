# Plan: the `__c_call` C-call intrinsic (Option E)

Status: COMPLETE (shipped through native variadics, 2026-06-02); kept for
design rationale. Bootstrap adoption (retiring `pkg/bootstrap`'s C
wrappers) remains gated on a BUILDER bump — see §8.

Implements **Option E2** from the "Annotations and C function interop"
item in [claude-todo.md](claude-todo.md): a call-site compiler intrinsic
for calling an arbitrary C symbol, with the C signature supplied as
explicit Binate types rather than parsed from a C prototype string.

```binate
import "pkg/c"

// ssize_t write(int fd, const void *buf, size_t count);
var n int = __c_call("write", c.C_ssize_t, cast(c.C_int, fd), buf, cast(c.C_size_t, len))

// int open(const char *path, int flags, ...);   // variadic: mode is a vararg
var fd int = __c_call("open", c.C_int, path, flags, ..., cast(c.C_int, 0))

// void free(void *ptr);
__c_call("free", c.C_void, ptr)
```

`__c_call(symbol, RetType, arg…)`: first arg is a string literal naming
the C symbol (emitted **verbatim** — no `bn_…` mangling); second is the
return type; the rest are argument values already in (or `cast` to) the
Binate types matching the C ABI. A `...` marker (see §4) separates fixed
from variadic args.

The end goal is to retire `pkg/bootstrap`'s hand-written C wrappers and
the special shim machinery. That adoption is gated on a BUILDER bump
(see §8).

---

## 1. Why this is smaller than it looks (and where the hard part is)

Two architecture findings reshape the work:

**The C-extern ABI path already exists.** Externs are `.bni` decls with
no body → `IsExtern=true`; `pkg/bootstrap` ones additionally get
`IsCExtern=true` and the platform C calling convention, including
struct-`sret` handling via `CallConv.CExternSretBytes`. So `__c_call`
passing or returning structs **reuses this** — it is not new work.

**Transparent type aliases work.** `type C_int = i32` is identical to
`i32` at the identity level, so an `i32` value passes where `C_int` is
expected with no cast. `pkg/c` is viable exactly as designed.

**Only two capabilities are genuinely new:**

1. **Raw unmangled symbol emission.** Every symbol today routes through
   `mangle.FuncName` → `bn_<pkg>__<name>`. There is no verbatim-C-name
   path (`@write`, `SetGlobal("write")`).
2. **Variadics.** Nothing existed in IR, codegen, or the native backends.
   - LLVM side: nearly free — emit `declare i32 @open(i8*,i32,...)` and a
     varargs call; LLVM does the platform-correct lowering.
   - Native side: the hard chunk, and almost entirely **darwin-arm64**,
     which passes *all* variadic args on the stack (`comp_native_aa64`,
     the host). amd64-SysV needs `AL` = vararg-float-count (0 for
     integer varargs), but there is no native-amd64 conformance mode
     today, so arm64-darwin is the only CI-exercised variadic case.

---

## 2. IR representation — new `OP_C_CALL` opcode

A dedicated opcode (keeps the already-large `OP_CALL` lowering
untouched; isolated dispatch in codegen + both native backends).

```
OP_C_CALL   // raw C call. StrVal = verbatim C symbol (no mangling),
            // Args = all args (fixed then variadic), Typ = return type
            // (nil => void), CFixedArgs = count of fixed/named args
            // (-1 => non-variadic; >=0 => Args[CFixedArgs:] are varargs).
```

New `ir.Instr` field:

```
CFixedArgs int   // OP_C_CALL: fixed-arg count; -1 = not variadic
```

ABI: `OP_C_CALL` is lowered as a C-extern call (the `IsCExtern` path) —
`CallConv.CExternSretBytes` thresholds, C struct-by-value passing. The
verbatim symbol is the only difference from a normal IsCExtern `OP_CALL`
in the non-variadic case. A `nil`/void `Typ` produces no SSA result.

---

## 3. `pkg/c` — C-type alias package

A normal (gen1-compiled, not BUILDER-tree) package of transparent
aliases pinning the Binate↔C scalar correspondence in one place.

| `pkg/c` alias            | Binate underlying | C type                  |
|--------------------------|-------------------|-------------------------|
| `C_char`                 | `int8`            | `char` (signedness impl-defined; promoted on pass) |
| `C_schar` / `C_uchar`    | `int8` / `uint8`  | `signed`/`unsigned char`|
| `C_short` / `C_ushort`   | `int16` / `uint16`| `short` / `unsigned short` |
| `C_int` / `C_uint`       | `int32` / `uint32`| `int` / `unsigned int` (32-bit on ILP32 **and** LP64) |
| `C_long` / `C_ulong`     | `int` / `uint`    | `long` (target-word on Unix: ILP32→32, LP64→64) |
| `C_longlong`/`C_ulonglong`| `int64` / `uint64`| `long long`           |
| `C_size_t`               | `uint`            | `size_t` (pointer-width)|
| `C_ssize_t`              | `int`             | `ssize_t`               |
| `C_intptr_t`/`C_uintptr_t`| `int` / `uint`   | `intptr_t`/`uintptr_t`  |
| `C_void`                 | (void marker, §4) | `void` return           |
| `C_varargs`/`...`        | (marker, §4)      | the `, ...` boundary     |

Pointers use plain Binate `*T` (e.g. `void*` → `*uint8`, `char*` →
`*const char` or `*[]const char`). Float aliases (`C_float=f32`,
`C_double=f64`) are **deferred until floats land** (not in BUILDER, and
native amd64 defers float args).

Note: C `long` ≠ C `int` on LP64; spelling `C_int` as `int32` (not
Binate `int`) is load-bearing — on a 64-bit target Binate `int` is
64-bit but C `int` is 32-bit.  (Binate's fixed-width spellings are
`int8`/`int16`/`int32`/`int64` and `uint8`/.../`uint64`, not the
`i32`/`u32`-style abbreviations.)

---

## 4. `C_void` and `C_varargs` markers

Both need recognizing by the checker without a `pkg/types`→`pkg/c`
dependency. Approach: recognize by **qualified name** in the
`__c_call` checker case (a tiny, contained special-case, like how other
compilers special-case a few library types).

- **`C_void`** (return slot): when the resolved return-type is the named
  type `pkg/c.C_void`, the call is void (no SSA result, `Typ`=nil).
  Define it in `pkg/c` as a distinct zero-sized marker type.
- **`...` boundary** (arg list): a `...` ellipsis token as the
  fixed/variadic separator — syntactically distinct from identifiers and
  reads naturally: `__c_call("open", C_int, path, flags, ..., mode)`.
  `parseCCall` records the index where varargs begin into the AST
  (`CVariadicFrom`, §5) and does **not** add the marker to `Args`.

---

## 5. Frontend plumbing

The `cast` builtin is the template — it already mixes a *type* arg
(parsed via `parseType` into `Expr.TypeRef`) with a value arg.

1. **Token**: `C_CALL` in the `builtin_start..builtin_end` range, string
   `"__c_call"` registered in `Lookup`. (`__c_call` is a valid identifier
   spelling; the lexer's identifier path → `token.Lookup` maps it.)
2. **AST**: reuse `EXPR_BUILTIN` with `Op=C_CALL`.
   - `Args[0]` = the symbol string-literal expr; `TypeRef` = return type;
     `Args[1:]` = argument value exprs.
   - Field `CVariadicFrom int` (index into the *value args* where
     varargs begin; -1 if non-variadic). Set by the parser from the `...`
     marker.
3. **Parser** (`parseCCall`, modeled on `parseCastCall`): parse the
   string literal, then `parseType` for the return type, then a
   comma-separated value-arg list, recognizing `...` as the variadic
   boundary.
4. **Type checker** (new case alongside `CAST`): validate `Args[0]` is a
   string literal; resolve `TypeRef` (recognize `pkg/c.C_void` → void);
   each value arg must be a C-compatible type — scalar, struct, or
   pointer (to any depth) thereof; result type = return type (or void).

---

## 6. Backend plumbing

**LLVM codegen** (`pkg/codegen`): `OP_C_CALL` → `emitCCall`, emitting
`[%id =] call <ret> [(<sig>)] @<rawsym>(args)`. Non-variadic uses an
ordinary signature; variadic (`CFixedArgs>=0`) emits the explicit
function-pointer type `<ret> (<fixed-types>, ...)` before `@<rawsym>`.
A collection pass (mirroring the `IsExtern` loop) scans module instrs for
`OP_C_CALL`, dedups by symbol, and emits the matching `declare`.

**Native backends** (`pkg/native/{arm64,amd64}`): `OP_C_CALL` reuses the
existing `emitCall` arg-dispatch + C-extern ABI but with a **raw**
`SetGlobal(symbol)` instead of the mangled `symFor(...)`. The `CalleeUsesCSret`
short-circuit on non-`OP_CALL` keeps sret out of the scalar/pointer
contract.

**Variadic native dispatch:** `CallConv` and its arg-dispatch take the
fixed/variadic boundary (`fixedCount = ins.CFixedArgs`):
  - **darwin-arm64**: args at value-index ≥ `CFixedArgs` go on the stack
    (Apple variadic rule), even within the first 8 GP slots — via an
    `AAPCS64_Darwin` `VariadicStackOnly` distinction. (The arm64 backend
    already targets Mach-O via `pkg/asm/macho`.)
  - **amd64-SysV**: set `AL` = number of vector regs used by varargs
    (0 for integer-only varargs) immediately before the `CALL`.

Float varargs (`AL` = actual vector-reg count) are out of scope — a
future extension, gated on floats landing.

---

## 7. Bootstrap cadence & scope

- **Implementation stays BUILDER-compilable.** The changes live in
  `pkg/{token,parser,ast,types,ir,codegen,mangle,native,native/*}` — all
  in the BUILDER tree — but use only plain Binate, so they compile under
  `bnc-0.0.2`.
- **`pkg/c` is gen1-compiled** (cmd/bnc does not import it), so it may use
  the full language.
- **Adopting `__c_call` inside bnc / to retire `pkg/bootstrap` waits for a
  BUILDER bump** that carries the feature — same cadence as generics. Not
  part of this plan; it's the follow-on that this unlocks.
- **Compiled-mode only for v1.** The VM would need FFI-style dispatch
  (resolve the symbol via the extern registry + marshal by the supplied
  types); deferred. `__c_call` in interpreted mode is an error for now.

## 8. Related

- Option E + the **link-requirement annotation** companion (auto-add
  `-lm` etc. at link) are tracked under the same claude-todo.md item; the
  link annotation is independent and later.
- Reuses the `IsCExtern` C-ABI path rather than introducing new
  calling-convention logic.
