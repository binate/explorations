### A generic function instance calling itself skipped the by-value struct argument copy — use-after-free; also a variadic self-call segfault and a deferred self-call panic — DONE (binate `a7afb2be9`, 2026-10-04, work-6)

ensureInstantiated registered an instance's FuncSig only after generating its body, so a call to the instance
from inside it found no signature: the by-value managed-struct copy was skipped (a callee overwriting the field
freed the caller's object), a variadic self-call bound its pack as positional arguments, and a deferred
self-call panicked IR-gen.  The signature is now registered before the body, sharing the per-instance context
and signature helpers with methods of generic types (gen_generic_inst.bn).  Tests: conformance
spec/12-generics/103 (self and mutual recursion), 104 (variadic), 105 (deferred); irgen TestInstFuncDeclAndSig.

### A call through a variable named like a generic function panicked IR-gen — DONE (binate `1d3a19624` + `91e8867e7`, 2026-10-04)

`f[0](5)` on a variable `f` beside `func f[T any]` was taken for an instantiation.  An upstream commit
(`1d3a19624`, the unsafe_index work) added the variable checks to genCall and the defer path, and stopped an
imported generic body from seeing the consumer's globals (bareGlobalIdx).  `91e8867e7` (work-6) closed the two
paths left: the method-value result-type lookup never checked, and the defer pre-pass runs before the function's
locals are declared — all four sites now resolve the head through instantiationHead, the checker's type first.
Test: conformance spec/12-generics/106.

### A const-group member that parks shadowed the variable of its name at the REPL — silent wrong value — DONE (binate `09251400e`, 2026-10-04, work-6)

shadowRebound now skips group members that park (as genConstGroup does).  Test: repl
TestReplVarNotShadowedByParkedGroupMember.

### Generic functions declared at the REPL prompt panicked — DONE (binate `bbb9f0c7f`, 2026-10-04, work-6)

GenDecl generated a prompt generic function eagerly as an ordinary one; it now stashes it, and instances are
emitted where used.  A same-signature redefinition re-emits the instances already emitted (the checker checks the
new declaration for each, check_generic_redef.bn), replacing them; any other rebinding of the name — another
signature, a non-generic function, a variable, a constant — shadows them (IR-gen forgets them, vm.UnbindFunc), so
old callers keep them and a later generic of the name emits its own.  Signatures compare with type parameters
matched by position (check.SameFuncSignature).  Three adversarial reviews; their findings (a stale instance
reused across generic → non-generic → generic, type-parameter-count changes parked, shadowing redefinitions
rejected, an IR-gen panic on a variable rebinding, a parked group member shadowing, a prefix collision) were
fixed before landing.  Tests: check check_generic_redef_test, irgen gen_repl_reemit_test, repl
decl_generic_test, vm lower_shadow_test (UnbindFunc), e2e repl case 76.  Open questions moved to their own
entries (generic redefinition rule; self-referential constraints).

