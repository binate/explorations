# Notes: Package Introspection + RTTI

**Status**: design notes, not a plan. Captures the discussion to date so it's not lost. Ratification and a real plan come later.

## Frame

Two related but distinct features, currently both unimplemented:

1. **RTTI** — runtime concrete-type identity, so that `iv.(*T)` and concrete-type switches on `*Iface` / `@Iface` work.
2. **Package introspection** — a `Package` object per package, exposing the package's exported surface: types, functions, impls, etc.

The proposal is to treat (1) as one query against (2)'s data structure rather than building a parallel side channel. The same `*TypeInfo` that vtable assertions compare against is the same `*TypeInfo` introspection would surface via `pkg.Types`.

## Why unify

- **Single source of truth.** One TypeInfo per type, reachable from both vtable (for assertions) and Package (for introspection). No duplicate metadata.
- **Identity-by-address falls out.** `iv.(*T)` is `vtable[typeinfo_offset] == &TypeInfo.T`. Same pointer is what `pkg.Types` would return for that type.
- **Cross-mode interop benefit.** The VM can walk a compiled package's `__pkg_info.<pkg>` global to discover functions/types — same way it consumes `.bni` files today, but at runtime rather than load time.

## The transitivity question

The dominant cost/design question is **how much data is reachable from any given introspection entry point**:

- **Minimum (identity-only)**: `*TypeInfo` is an opaque address. No other data hangs off it. Reflection from an interface value gives you a comparable identity but nothing readable. Linker easily strips anything unused.
- **Names+structure**: TypeInfo carries name, size, kind (struct/interface/...), maybe field/method tables. Reflection becomes "useful" but every type's metadata is reachable, so the linker can't strip much once anything references the package's TypeInfo list.
- **Full reflection**: types, fields with names+offsets+types, methods with signatures, impls, function bodies (callable), etc. Go-style.

Lower transitivity → linker can strip unused metadata → pay only for what you use. Higher transitivity → simpler API but always-on cost.

## Key revised insight: function values for interop

The critical reflection consumer in Binate is **not** general user-facing reflection (`reflect`-style API for marshaling, debugging, etc.). It's **dual-mode interop**:

- The VM needs to call into compiled code by name. Today it does that via a hand-maintained extern table (`RegisterStandardExterns`).
- A clean replacement: each compiled package emits a `Package` object whose `Functions` table lists every exported function as a (name, signature, function-value) tuple. The VM (or another module) walks the table and binds.
- Same mechanism in reverse: compiled code wanting to call into the VM by name uses the VM's equivalent table.

This reshapes the **phasing**:

The most important Phase-B chunk is *not* "type metadata for user-facing reflection" — it's "function-value table for every exported function, with names". That's what unlocks automatic interop. Type metadata for reflection comes after.

## Proposed phasing

- **Phase A — Identity-only RTTI** (small, well-defined):
  - `TypeInfo` per concrete type, identity-by-address only (no useful payload beyond a back-pointer to Package and maybe a name string for panic messages).
  - Vtable `any`-block carries `*TypeInfo` (offset 0 — see embedding/extension notes).
  - `x as *T` / `x as @T` / type switches over concrete types.
  - No interface-to-interface assertion (nominal typing — see interface extension docs).
  - Stripping-friendly: if no code references `&TypeInfo.T`, the linker can drop it. Vtable still has a slot but it's a single pointer.

- **Phase B — Function-value table for interop** (interop-critical):
  - Each package emits `__pkg_info.<pkg>.Functions = [{name, signature, function_value}, ...]` for exported functions.
  - VM (and other modules) can load this table to bind names → function values without a hand-maintained extern list.
  - Names are required; signatures are required (the caller has to know what to pass); function values are required (the thing to invoke).
  - Note: this still doesn't expose *general* reflection — just the function-binding surface.
  - This phase is largely independent of Phase A's RTTI scope: function tables don't need TypeInfo unless their signatures want to reference user-defined types by introspection rather than by direct LLVM IR / mangled name.

- **Phase C — Richer type metadata** (user-facing reflection):
  - TypeInfo grows fields (size, alignment, name, kind, field list, method list, impls).
  - Package object grows beyond Functions to expose Types, Impls, Consts, Vars.
  - Probably opt-in per package (or per build), since this is where the binary-size cost really lands.

Phase A and B can land in either order — they don't gate each other. Phase C is the biggest design surface and should come last.

## Sketch (preliminary, not ratified)

```
// language-defined, in some core/runtime package
type Package struct {
    Name      *[]const char
    Functions *[]@FunctionInfo
    Types     *[]@TypeInfo        // Phase A: identity only; Phase C: full
    // Phase C: Impls, Consts, Vars, Imports
}

type TypeInfo struct {
    Pkg  @Package
    Name *[]const char     // even Phase A: useful for panic on failed assertion
    // Phase C: Kind, Size, Align, Fields, Methods, Impls
}

type FunctionInfo struct {
    Pkg       @Package
    Name      *[]const char
    Signature ...           // form TBD — string mangling, or structured?
    Value     <func-value>  // the callable
}
```

Each compiled package emits `__pkg_info.<pkg>` as a weak_odr global, with TypeInfo / FunctionInfo entries laid out as linker-stripable static data. Per-type TypeInfo gets its own `__typeinfo.<pkg>__<name>` weak_odr global so it's individually stripable.

## Open questions

1. **Opt-in granularity for Phase C.** Per-build flag (`--reflect`), per-package annotation, per-type annotation, or always-on? Each has different consequences for the linker-stripping story.
2. **Function `Signature` representation.** Mangled name string (simple, compares cheaply, but you can't decompose), structured (`*[]@TypeInfo` for params + return), or both? Structured is more useful but couples Phase B more tightly to Phase A/C.
3. **Where do `Package` / `TypeInfo` / `FunctionInfo` types live?** Probably a new core/runtime package (`pkg/reflect` or `pkg/intro` or similar). The compiler emits globals against these definitions, so they have to be the authoritative ones.
4. **Cross-package lookup.** Can `Package` objects be looked up by name (`reflect.PackageByName("pkg/foo")`)? If yes, there needs to be a registry — probably the linker emits one. If no, you can only access packages you've imported, which is fine for most use cases but limits dynamic-loading scenarios.
5. **Versioning / ABI stability.** `Package` layout has to be ABI-stable across compiler versions, since VM/compiled-mode interop crosses build boundaries.
6. **Function-value compatibility for non-exported functions.** Phase B initially only covers exported functions. Is there a reason to ever expose un-exported functions? (Probably not, but worth flagging.)
7. **Generic functions and types.** A generic isn't a single function — it's a family. Phase B for generics is meaningfully harder: do you list instantiated specializations, or the template?
8. **Static `Package` data vs runtime-constructible.** Phase A/B/C all describe statically-emitted data. Is there a case for runtime-registered entries (e.g., plugin systems)? Probably not v1.
9. **Method introspection.** Should methods (under impl blocks) appear in `Package.Functions`, in `TypeInfo.Methods`, in `Package.Impls`, or all of these? They're the same data viewed three ways.

## Connection to the broader picture

- **Interfaces/embedding**: TypeInfo lives in vtable `any`-block at offset 0 (alongside dtor). All interfaces reach it the same way regardless of extension depth.
- **Generics**: `T` constraint may want to reference TypeInfo (`size_of[T]()` could be a builtin that resolves at instantiation time and emits a TypeInfo reference — TBD).
- **Linker / multi-backend**: per-type weak_odr globals are already a pattern (vtables, dtors). TypeInfo / Package globals fit the same model.
- **`.bni` files**: today they carry signatures at load time. Phase B's runtime function-value table is the same information surfaced at runtime — possibly a step toward dropping the load-time `.bni` step entirely in favor of always reading from `__pkg_info`. (Not a Phase B commitment, just a direction to think about.)
