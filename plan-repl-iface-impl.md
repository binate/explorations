# Plan: `interface` and `impl` declarations at the REPL prompt, parking like other declarations

Status: IN PROGRESS (work-6, 2026-09-30) — support at the prompt landed as binate `d220d330b`; parking remains.  Todo: "Interface and `impl` declarations at the REPL prompt …"
(part 2).  User: "Yes, claim both and start with 1."; decisions "1-3 recs seem fine; 4: do what you think
is best (if it expands scope too much, then no); 5: yes".  Part 1 (undo a refused declaration) landed as
binate `418119a87`; decision 5 (vtables for rows minted by prompt functions / var initializers, and
lowering everything a prompt entry's generation appends) landed as binate `4af0cd413`.

## Decisions

1. Redefining an interface, a type over an interface, or an interface over a type is rejected, like a
   type redefinition, until REPL shadowing of types exists.
2. An incompatible redefinition of a method an `impl` uses shadows it; the `impl`'s vtable keeps the old
   method (its slots are resolved when it is built), as existing callers keep the old definition.
3. A parked `impl` is announced as `impl *Box : Sizer`; a conversion to its interface, or to any parent of
   that interface, waits on it.
4. Generic interfaces at the prompt: yes (IR-gen stashes them; instantiated at use).  Generic-receiver
   `impl`s: refused at the prompt (and so undone) — they need generic-receiver methods declared at the
   prompt, which are broken (todo "Generic functions and methods of generic types declared at the REPL
   prompt panic").

## Checker (pkg/binate/check)

- DECL_INTERFACE and DECL_IMPL become parkable (isParkableKind): checked as a batch
  (checkDeclsTentative); pendingDeclKindNoun "interface" / "impl".
- A prompt `impl`'s coverage is checked (today checkAllImplsSatisfaction runs only for whole packages):
  the impl records the declaration added (c.Impls[mark:]).  In TentativeMode a method missing from a
  session type is a forward reference, captured as `T.M` (like noteMissingMethod), so the impl parks on
  it.
- Keys become plural: an `impl T : I, J` provides `T:I`, `T:J` and `T:P` for every parent P of those
  (pendingKeys); lookupPending / IsPendingDecl / groupProvides / dropSupersededPending / pendingGroups
  match any of a declaration's keys; keyBound understands `T:I` (an impl record exists).  T is the
  receiver's base type name.
- A conversion of a session type's value to an interface with no impl record for (T, I): in TentativeMode
  capture `T:I` (the declaration parks on the impl); at the prompt, with a parked impl providing it,
  report the impl unresolved.  Hooked where the conversion error is reported, only when no impl record
  for (T, I) exists at all (a genuine mismatch — say pointer-ness — is still an error).
- Redefinition rejection (rejectTypeRedefinitions) covers interfaces both ways, and generic interfaces
  (stash).

## IR-gen (pkg/binate/irgen)

- GenDecl: DECL_INTERFACE → collectInterfaceFromDecl (generic: stashGenericIfaceDecl); DECL_IMPL →
  collectImplsFromDecl (generic receiver: refused).
- A resolved group with interfaces and types: struct shells, then interfaces, then struct fields, then
  helpers (GenTypeDecls grows an interface phase) — a struct field `*I` needs I registered, and I's
  method signatures may name the group's structs.
- Slot-0 destructor of an impl whose receiver is a named managed-slice / managed-pointer / array type:
  emitted when the impl is declared (today only at a boxing site), else a vtable built before the first
  box has slot 0 empty — a leak.
- Value-receiver dispatch thunks: the review found VM dispatch through a value receiver correct without
  them (LookupVtableSlotName falls back to the method); verify with a test, add thunks built from the
  latest method definition only if needed.

## REPL (pkg/binate/repl)

- evalReplOneDecl: the interface / impl kinds emit via GenDecl; their vtables are built before the next
  code runs (lowerPendingImpls).
- parkedDeclLabel: "interface I", "impl *Box : Sizer".
- emitResolvedGroup: types-and-interfaces (GenTypeDecls), then impls, consts, functions, vars.

## Tests

check: impl parks on a missing method / type / interface; a conversion parks on a parked impl, also to a
parent interface; redefinitions rejected; a failed impl is undone.  repl: interface + impl at the prompt
dispatch; an impl declared before its method parks and resolves; a method redefined with another
signature leaves the impl on the old one; a boxed named managed-slice receiver's destructor runs.
e2e/repl.sh: the transcript forms of these.
