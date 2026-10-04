# Plan: a named pointer type is a value type for boxing

Status: drafted 2026-10-03 (work-3).  Implements the user's decision on the claude-todo entry "`@any` of a
named managed pointer or function value ... never matches its own `case`" (option (A)), and with it the MAJOR
"A value-receiver method of a named POINTER type called through an interface reads garbage" and conformance
1469 (xfail.all today).

## The rule

A box's data word always points to an object of its dynamic type, for every named type whatever its
representation.  For `type H @Node`, `type PS *S`, `type X @J`, `type PP @(@[]int)`:

- `box(h)` makes an `@H` (a heap H cell); `&h` is a `*H`.  Either constructs an interface value whose
  dynamic type is `H` (keyed by name, `__typeinfo.<pkg>.H`), with the data word pointing at the H cell /
  variable.
- `.(@H)` / `.(*H)` recover the cell, `.(H)` copies the H out, `case @H:` matches, `.(@Getter)` works when
  `impl H : Getter`.
- H's value-receiver methods dispatch through the ordinary deref thunk (load the H from the cell, call).
- Widening a bare `h` is treated exactly as widening any value: into a raw `*I` / `*any` it is the implicit
  value-borrow (`iface.construct.value-borrow`, i.e. `&h`); into a managed `@I` / `@any` it is rejected
  ("box a value first", `iface.construct.box`).
- `cast(@Node, h)` gives the underlying `@Node`, which boxes as a Node (dynamic type `Node`).
- An owning box's slot-0 destructor is H's own destructor: release the `@Node` held in the cell (`type H
  @Node`), the interface value (`type X @J`), the managed-slice cell (`type PP @(@[]int)`); nothing for a
  named raw pointer (`type PS *S`).  The box's own reference on the cell is released by the drop as usual.

## What the tree does today (each site encodes "a named pointer is its own data word")

Checker (`pkg/binate/check`):
- `types_assignable_iface.bn` `canAssignToRawInterfaceValue` / `canAssignToManagedInterfaceValue`: the
  pointer-shape gate peels a named type (`StripWrappers`), so a bare `h` is admitted as a pointer source into
  `*I` / `@I` / `*any` / `@any`.  Under the rule: the gate looks at the type's own constructor (alias and
  outer `readonly` only); a named pointer falls to the value path.
- `check_addr.bn` `canBorrowValueIntoRawIface`: its source filter must treat a named pointer as a value.
- The diagnostic for a rejected managed widening should say to box the value (check what today's message for
  `var g @I = s` with `s S` is, and reuse it).

IR-gen (`pkg/binate/irgen`):
- `gen_borrow.bn` `isBorrowableValueSource`: peels with `StripWrappers`, so a named pointer is not borrowed.
  Peel alias / readonly only.
- `gen_iv_thunk.bn` `needsRecvThunk`: exempts a named pointer receiver from the deref thunk.  Remove the
  exemption (the thunk then loads the H from the cell).  The REPL path (`gen_repl_iface.bn`) uses the same
  predicate.
- `gen_impl.bn` `recvBaseValueType`: maps a named pointer receiver to its POINTEE (the boxed value type,
  TypeInfo size, slot-0 dtor key).  Under the rule the boxed value type is H itself.
- `gen_impl.bn` `isNamedRawPointer` + `boxSlot0DtorName`: slot 0 keyed on the pointee.  Key it on H (a
  structural managed-pointer / interface-value / managed-slice dtor of H's underlying; null for a raw pointer).
- `gen_iface.bn` `wrapAsIfaceValue`: peels the source pointer with `StripWrappers` (a bare `h` reaches here
  as a pointer); erases a named pointer's identity to its pointee for `any` (`anyPointeeName`); and keys a
  named managed POINTER pointee structurally (the TODO at the `namedPointee` branch).  Under the rule the
  source is always a literal `*T` / `@T` (the checker no longer admits a bare named pointer), `T` may be H,
  and H keys by name like any named pointee.
- `gen_iface_anybox.bn` (slot-0 dtor naming for the any row, `StripWrappers(recvTyp)`) and
  `gen_iface_nameless.bn` (`isBoxableNamelessType` peels a named pointer to reach a name-less pointee) follow
  from the above.
- `gen_assert.bn` `typeInfoSymFor`: the TODO about `@H` goes away; the match side already keys on `H`.

Backends: every backend (LLVM `codegen/emit_impls.bn`, the VM `vm/lower.bn`, native aarch64/x64/arm32
`*_iface.bn`) takes vtable slots from IR's `LookupVtableSlotName` / `ThunkNameForMethod`, so the thunk and
slot-0 changes reach them without backend code.  Verify, don't assume: run each native mode on the new tests.

## Open points to settle while implementing (raise, don't decide silently)

1. A named INTERFACE-VALUE type with no own impl (`type NShape *Shape`) assigned to an ancestor interface:
   today the VM upcast code (`vm/lower.bn` `buildIfaceUpcastSuffixes`) peels it as an interface-value
   upcast.  The decision covers boxing; an upcast of a named interface value whose underlying is an interface
   value is not boxing.  Check how the checker admits it today and keep it an upcast unless that conflicts.
2. Tree code that widens a bare named pointer into an interface (`var g @I = h`) becomes a compile error.
   Find every such site by building the tree, the stdlib and the conformance suite with the new checker, and
   fix each to `box(h)` / `cast(@Node, h)` as its meaning requires.  Report the count before rewriting.
   Known: conformance 1143 and 1145 pin today's convention explicitly (their header comments cite a rule id
   `iface.satisfaction.named-distinct-ptr` that the spec does not define), and 1134, 1465 and 1466 are built
   around it (slot-0 null for `type PS *S`, the struct dtor for `type MS @S`).  Each is rewritten to the new
   convention, keeping what it was guarding (no leak, no double release) under the new layout.

## Steps

1. Checker gates + diagnostics; unit tests in `types_assignable_iface_test.bn` (accepted: `box(h)` into
   `@I`/`@any`, `&h` and bare `h` into `*I`/`*any`; rejected: bare `h` into `@I`/`@any`).
2. IR-gen: borrow source, thunk exemption, receiver base type, slot-0 dtor, box identity.  Unit tests where
   IR shape is pinned (irgen, codegen, vm, native/* tests that compile source through IR-gen).
3. Sweep the tree for now-rejected widenings (open point 2).
4. Conformance: a positive test over `@`- and `*`-named pointers, a named managed interface value and a named
   managed-slice pointer, each into raw and managed user interfaces and `any`: dispatch, `case`, `.(@H)`,
   `.(*H)`, `.(H)`, `.(@Getter)`, drop (refcount balance via `rt.RefCount` or a dtor counter), cross-package
   (the impl in an imported package).  An error test for the managed widening of a bare named pointer.  Drop
   1469's xfail.  Every backend: LLVM, VM, native aa64 / x64 / arm32.
5. Spec: §11.4 — "pointer-shaped (`*T` or `@T`)" means a pointer type constructor, not a named type over one;
   a named pointer type is a value for construction.  §11.12 — the dynamic type of a box of `@H` is `H`.
   Regenerate `rule-ids.txt` if a rule id is added.
6. Move the two todo entries (and the 1469 note) to the done file.
