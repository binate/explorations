# Plan: injective dtor/copy name mangling

Status: F1 + F2 FIXED (2026-09-18, work-1; fix pending cherry-pick to main) — the
nested struct/named encoding now uses `mangle.LpTypeArgNamedRaw` (length-prefixed,
identifier-only, full path), closing kind-token spoofing AND cross-package same-leaf.
No normative spec change (mangling is impl-defined §21); Annex B's informative flag
updated in the docs repo.  A residual **F3** (a TOP-LEVEL struct source-named to embed
a wrapper's exact encoding) and the anon-struct >128-char hash fallback are left open —
see the todo.  Validated: ir 837/0, refcount-balanced spoof compiled+VM, self-compile
builder-comp-comp + native-aa64 3037/0, mangler-critical review clean.  Scope B was
chosen by the user; implementation turned out contained (only the nested arm changed,
via the existing `LpTypeArgNamedRaw` — no top-level naming-layer rework needed for
F1/F2).  Todo: `claude-todo.md` "Dtor/copy name-mangling non-injectivity".

## The defect

`dtorTypeSuffix` (`pkg/binate/ir/gen_dtor.bn`) encodes a type into the suffix of a
weak_odr `__dtor_<suffix>` / `__copy_<suffix>` symbol.  `copyNameForType` reuses
`dtorTypeSuffix`, so the fix is centralized in that one function.  The encoding is
NON-injective: two distinct types can produce the same suffix, so their link-once
dtor/copy bodies collide — the linker keeps one body and the other call runs it
(wrong element size/kind) → silent leak / double-free / SIGSEGV.

The grammar (informal), with the unsafe terminals marked:

    suffix(struct Name) = leaf(Name)          -- VERBATIM leaf, no length/delimiter, no path
    suffix(@T)          = "mp_"  suffix(T)
    suffix(@[]T)        = "ms_"  suffix(T)
    suffix([N]T)        = "arr" N "_" suffix(T)
    suffix(@Iface)      = "interface"          -- keyword: unspoofable (already fixed this way)
    suffix(@func)       = "func"               -- keyword: unspoofable
    suffix(int/bool)    = name                 -- IDENT, not a keyword: spoofable
    suffix(anon struct) = "anon_" fields | "anon_h" hex

Two facets of the same root (non-injective struct/primitive name encoding):

- **F1 — kind-token spoofing** (the finding both 2b adversarial reviews raised): a
  struct legally named `mp_Node` / `ms_…` / `arr5_…` / `elems_ms_mp_Node` produces a
  suffix that reconstructs as a *wrapped* type's suffix.  Canonical case:
  `__dtor_ms_elems_ms_mp_Node` names BOTH the 2b elems helper of `@[]@Node` AND the
  by-address dtor of `@[]struct{…}elems_ms_mp_Node`.
- **F2 — cross-package same-leaf** (the class Annex B already flags; see below):
  `dtorTypeSuffix` writes the LEAF only (`dotSuffix(t.Name)`), so `@[]pkg/a.Config`
  and `@[]pkg/b.Config` — two *distinct* types — both mangle to `__dtor_ms_Config`.

The scheme already solved this for `@Iface`/`@func` by using the reserved keywords
`interface`/`func` (a struct can never be named a keyword).  F1/F2 are the tokens
that reform of the same idea did NOT cover.

## ABI / spec impact (the investigation the user asked for)

- **No NORMATIVE spec change is required.**  Name mangling is **implementation-defined**
  (§21 `behavior.impl-defined`, "Symbol decoration / name mangling … the scheme is
  informative", → §16.6 `pkg.identity`; Annex B).  The exact characters are ours to
  choose.
- **§16.6 `pkg.identity` (normative) actually REQUIRES the fix's direction.**  "A
  package's identity — and therefore the identity of the types it declares and **the
  symbols it links** — is its **full import path**, not the path's last segment."  The
  current leaf-only element suffix contradicts this in effect (F2): distinct-package
  same-leaf types are *supposed* to be distinct symbols.  So making the mangler
  injective w.r.t. full type identity is aligning the implementation WITH the
  normative rule, not changing the rule.
- **Annex B (informative) update.**  Annex B already lists this as an open item:
  *"FLAG the OPEN mangler class (struct types not carrying fully-qualified names;
  genMethodValue cross-package value receiver)."*  A complete fix (F1+F2) resolves the
  "struct types not carrying fully-qualified names" half of that flag; Annex B's line
  should be updated to record it (informative annex, not a normative rule).  The §21
  table entry stays as-is (still impl-defined; the scheme is still informative — now
  injective).
- **Runtime ABI = whole-program-consistent, self-contained per build.**  The change
  alters EVERY `__dtor_`/`__copy_` weak_odr symbol name.  That is safe because a single
  `bnc` compiles a whole program from source with one scheme (all TUs agree), and there
  is **no prebuilt-object ABI boundary** at these names: only the `bnc` BINARY is
  prebuilt (the BUILDER), never objects — each generation fully recompiles, so gen1
  (built by the old-scheme BUILDER) internally links old-scheme symbols consistently and
  EMITS the new scheme when it compiles gen2.  **No BUILDER cut is needed**, and old and
  new objects are never linked together.

## Fix

Make every terminal in `dtorTypeSuffix` self-delimiting so a name can never be
confused with a kind token or an adjacent terminal.  A **length-prefixed** name
starts with a digit; every kind token (`mp_`, `ms_`, `arr`, `interface`, `func`,
`bool`, `anon`) starts with a letter → the encoding is prefix-free / injective.

- **Scope A (F1 only — the review's finding):** length-prefix the struct LEAF name and
  the primitive names in `dtorTypeSuffix` (and the anon-struct leaf).  `@Node` →
  `mp_4Node`; a struct named `mp_Node` → `7mp_Node`; the elems-helper collision is
  gone.  Leaves F2 (cross-package same-leaf) OPEN — Annex B stays flagged.
- **Scope B (F1 + F2 — resolves the whole Annex B flag, aligns pkg.identity):** encode
  the struct's FULL package path, length-prefixed, instead of the leaf — e.g.
  `<len>pkg/a.Config`.  Keep the helper MODULE-LOCAL (the whole suffix is still one
  local weak_odr symbol; do NOT reference the defining package's module — preserves the
  `elemDtorName` / `isElemDtorModuleLocal` model).  Fixes both facets; distinct-package
  same-leaf types get distinct symbols.

**Recommendation: Scope B.**  F1 and F2 are the same root and the same code; fixing
only F1 leaves the Annex B flag open and guarantees a second pass over the same
function.  B is modestly bigger (the TYP_STRUCT arm uses the qualified name + a small
change to how the by-address vs element paths pick leaf-vs-qualified), but it retires
the whole "struct types not carrying fully-qualified names" mangler class.

Whichever scope: the kind tokens (`mp_`/`ms_`/`arr`) can stay as-is once terminals are
length-prefixed (they are already letter-initial, so they can't be confused with a
digit-initial length-prefixed name) — no need to keyword-ize them.

## Validation

- **Unit tests** (`gen_dtor` / `gen_copy` test files): the spoofing pairs produce
  DISTINCT symbols — a struct named `mp_Node` vs `@Node`; the `elems_ms_mp_Node` struct
  vs the `@[]@Node` elems helper; (Scope B) `@[]a.Config` vs `@[]b.Config`.
- **Conformance program**: a struct named to spoof a kind token, used alongside the
  spoofed aggregate in one module — asserts correct output / no double-free (would
  corrupt under the collision).  Runs at -O0 in every mode (no SROA needed).
- **Full self-compile** — the real integration check, since every dtor/copy symbol name
  changes and must re-derive consistently across all TUs + the linker's weak_odr dedup:
  builder-comp-comp (LLVM), builder-comp_native_aa64/x64/arm32 (all native backends),
  and a VM mode.  Hygiene.
- **Adversarial review** (mangler-collision-critical).
- **Annex B** informative update in the same landing (docs repo).

## Files

- `pkg/binate/ir/gen_dtor.bn` — `dtorTypeSuffix` (the whole fix; `copyNameForType`
  inherits it).  Possibly `anonStructSuffix` (length-prefix its leaf too).
- Scope B additionally: the TYP_STRUCT arm's leaf-vs-qualified choice, cross-checked
  against `elemDtorName` / `qualifiedDtorNameForType` / `isElemDtorModuleLocal`.
- Tests: `gen_dtor*_test.bn` / `gen_copy*_test.bn`; a new `conformance/NNN_*`.
- `docs/spec/annex-b-implementation-model-and-idb-index.md` — update the flagged line.

## F3 + anon-hash (full unification) — chosen 2026-09-18, work-1

Close the residual: make the TOP-LEVEL struct dtor/copy leaf injective too, so a
struct source-named like a wrapper helper can't shadow it.  The PACKAGE stays in the
`pkg.` symbol qualifier (cross-package resolution), so only the LEAF needs encoding —
length-prefix it (`<declen><leaf>`, digit-initial, can't reconstruct a letter-initial
`mp_`/`ms_`/`arr` token).  Applied via ONE shared helper (`writeStructLeafToken`) at
EVERY struct-leaf dtor/copy naming site so definition and reference stay in agreement
(a missed site = undefined symbol, caught by self-compile).

Sites (repo-wide grep for `dotSuffix` / `.__dtor_` / `.__copy_` / raw-name writes into
`__dtor_`/`__copy_`):
- gen_dtor.bn: dtorTypeSuffixRec top-level struct arm (:113); qualifiedDtorNameForType
  struct arm (:245/248); dtorName (:325); qualifiedDtorName (:344).
- gen_copy.bn: the copy twins (copyNameForType shares dtorTypeSuffix; qualifiedCopy-
  NameForType struct arm; copyName; qualifiedCopyName).
- gen_dtor_emit.bn (:22,:68) + gen_copy_emit.bn (:49): base = dotSuffix(...) for
  extern-decl / definition names.
- gen_impl.bn (:452): `.__dtor_` write.
- codegen/emit_funcvals.bn (:305): the backend closure-struct dtor name — must match.

Then the anon-struct >128-char `anon_h<fnv32>` fallback (anonStructSuffix): FNV-32 is
non-injective; replace the truncating hash with the full field-sequence encoding (no
truncation — symbols may be long but the backend length-prefixes them) OR a wider,
collision-resistant digest.  Validate: ir unit tests (all struct dtor/copy name
assertions change — update them); a spoof-named TOP-LEVEL struct program; self-compile
LLVM + all native + VM (every struct symbol changes → the real def/ref check); review.
