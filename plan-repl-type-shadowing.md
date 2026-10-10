# Plan: type and interface shadowing at the REPL prompt

Status: DRAFT for discussion (2026-10-09, work-6).  User decisions (2026-10-09): semantics 2, 3, 4 and 5
below as proposed ("2/3/4/5 sound fine"); 6 — distinguish an old and a new T in diagnostics: "ideally,
yes"; generic types IN scope ("7: yes").  Approach A vs B: an adversarial review first (user: "get an
adversarial review of approach A vs B").  Todo entry: "REPL redefinition of types and
interfaces, and across kinds: shadowing (the design) is not implemented".

## Goal

claude-notes.md ("Redefinition in the REPL"): a compatible redefinition replaces; an incompatible one
shadows — "existing instances retain the old layout/type definition".  Functions, variables, generic
functions and methods of generic types do this today.  Types and interfaces do not: `type T …` over a
bound T, an interface over a bound interface, and any declaration across kinds with a type or an
interface on either side are rejected at the prompt (check/check_redefinition.bn, rejectRedefinitions).

## Why types are harder than variables

A named type's identity is its qualified name (types.NamedIdentityName: `main.T`, or the underlying
struct's name), and every layer keys on that name:
- the checker (Identical, method sets, impl lookups, the generic-instance cache);
- IR-gen (the struct / alias / interface registries, `__dtor_T` / `__copy_T` helper names, method
  symbols `main.T.M`, value-receiver thunks, impl rows, typeinfo records);
- the VM (function index by name, vtables and typeinfo looked up by name at box / assert time).

Two definitions of `T` must therefore differ in name somewhere.

## Approach A — rename the OLD definition out of the way (as variables are)

Give the old type a hidden name (`__repl_shadowed_3_T`) everywhere it is registered: the checker's Type
object, IR-gen's registries, the FuncSigs of its methods and helpers, the VM's functions (methods,
helpers, thunks), its vtables and typeinfo.  The new definition then takes `main.T`.

Cost: every layer's name-keyed state is renamed after the fact, including state already lowered into
the VM (vtable / typeinfo names consulted at run time), and IR-gen must emit the old type's helpers again
under the hidden name for new code that drops an old value.  Many places, each a chance of a stale name.

## Approach B (recommended) — the NEW definition takes a fresh internal identity

A shadowing definition of `T` is registered under an internal name the user cannot write
(`__repl2_T`: names beginning with `__` are reserved for the compiler, mangle.IsReservedIdentifier),
and the session's name `T` is bound to it from then on.  Nothing already registered changes: the old
`main.T` keeps its layout, methods, helpers, impls, vtables and typeinfo, so existing values and code are
untouched by construction.  Everything emitted for the new type is named after `__repl2_T`, so nothing
collides.

What it needs:
1. **Checker**: at the prompt (c.AllowRedef), a type / interface declaration over a bound complete type /
   interface that is not a compatible redefinition (below) creates its Type under the internal name and
   binds the scope name to it; the rejection (rejectRedefinitions) is lifted for that case.
2. **IR-gen sees the internal name**: IR-gen resolves type expressions from the AST by name (resolveTypeExpr
   → its registries), so a later prompt's `T` must reach IR-gen as `__repl2_T`.  Proposal: where the
   checker resolves a type name to a type whose internal name differs from what is written (TEXPR_NAMED,
   a method receiver, a method expression `T.M`), it records the internal name on the AST node (as it
   already rewrites nodes, e.g. lowerUnsafeIndex), so IR-gen needs no REPL-specific map.
3. **Display**: diagnostics, REPL notices and the runtime type names (typeinfo records, which fmt and
   assertion panics print) show `T`, not `__repl2_T` — a display rule that drops the internal prefix.  When
   an error involves both an old and a new `T`, the old one should read distinctly (e.g. "T (shadowed)").
4. **Warning**: "warning: type T shadowed (incompatible definition); existing values and code keep the old
   type", as for functions and variables.

## Semantics to decide

1. **Compatible redefinition of a type**: proposal — a definition identical to the current one (same
   underlying type, structurally) is accepted and keeps the existing type (no-op: methods and impls stay).
   Anything else shadows.  (Alternative: "same layout" counts as compatible — but a field rename then
   changes meaning under existing code, so identity of the definition seems right.)
2. **Methods and impls do not carry over** to the new type: it starts with none; the user re-declares
   them.  Old-type methods stay callable on old values.  A `func (t T) M()` typed after the shadow is a
   method of the new T.
3. **Interfaces**: the same — an identical method set (and parents) keeps the interface; otherwise the new
   one shadows it; old impls stay with the old interface; boxes made before keep dispatching.
4. **Across kinds** (a type over a constant / variable / function name, or the reverse): with distinct
   identities per definition these become ordinary shadows — a variable over a type name renames nothing
   of the type; a type over a variable name shadows the variable as variables are shadowed today
   (shadowRebound, "redeclared as a type").
5. **Mixing old and new values** is a type error (they are different types), reported with the
   distinguishing display of (3).
6. **Generic types**: in scope, or rejected for now?  (A generic type's instances, its stashed declaration
   and methods are keyed by name too — a shadow would need the same treatment through the instance cache.)

## Order of work (proposal)

1. Non-generic types (checker identity + AST internal name + display + warning), with REPL and e2e
   tests over values, methods, impls and helpers of both definitions.
2. Interfaces.
3. Across kinds.
4. Generic types (or keep them rejected — decision 6).
