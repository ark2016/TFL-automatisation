"""Word-membership oracle for ll_system IRs: `set_builder` (Format 1) specs and
explicit grammars (Format 2/3, via CYK -- see `oracle_from_ll_ir`).

The rest of this docstring describes the `set_builder` part.


`cfl_system.lib.cfl_oracle.cfl_oracle_from_ir` has no `set_builder` kind (that
notation is specific to ll_system's Format 1 IR — CFL specs use
`repeated_subword` / `exists_decomposition` with a `concat_pattern` list
instead), so `ll_system.lib.claim_verifier._try_word_oracle` falls through to
`None` for every Format-1 task today (docs/VERDICT_POLICY.md §4: "для
set_builder-языков нужен общий word-оракул … до его появления … trust
остаётся well_formed"). This module is that oracle: it interprets a
`set_builder` `language_spec` directly (its own template matcher — see
module docstring below — rather than translating into a CFL IR, since the
`aⁿ`/`Xⁿ` exponent notation used by ll_system's own examples has no CFL
counterpart) and exposes

    oracle_from_ll_ir(ir) -> Callable[[str], bool] | None
    generate_words(ir, max_len) -> list[str]

`oracle_from_ll_ir` returns `None` (never raises) when the spec isn't a
`set_builder` kind, or its `template`/`constraints` use a shape this module
doesn't understand — the caller's contract is "None = language not
supported", exactly like `cfl_oracle_from_ir` raising
`UnsupportedOracleKindError`, just without the exception.

Language-spec shape handled (see `ll_system/examples/format1_anbn_union_ancn.json`
for the schema this refines):

    {
      "kind": "set_builder",
      "alphabet": ["a", "b", "c"],
      "variables": [
        {"name": "w", "domain": {"type": "word", "alphabet": ["a", "b"]}},
        {"name": "n", "domain": {"type": "nat", "min": 0}},
        {"name": "choice", "domain": {"type": "enum", "values": ["b", "c"]}}
      ],
      "template": ["w", "c", "rev(w)"],
      "constraints": [{"op": "leq", "left": "i", "right": "j"}]
    }

Variable domains:
  - `"nat"`      — a non-negative integer, used as the exponent of a
                   `<base><superscript-exponent>` template token (e.g. `"aⁿ"`,
                   `"choiceⁿ"`). Optional `"min"` (default 0).
  - `"enum"`     — one symbol from `"values"`; usable bare in the template
                   (one of the symbols, chosen freely) or as an exponent base
                   (`"Xⁿ"` = that chosen symbol repeated `n` times).
  - `"word"`/`"string"` — an arbitrary substring over `"alphabet"` (defaults
                   to the language's own `alphabet`), usable bare (`"w"`) or
                   under `"rev(w)"`. The same variable name reused elsewhere
                   in the template (bare, or under `rev(...)`) must bind to
                   the identical string — this is how `{w c rev(w)}` ties the
                   two halves together, with no separate "same variable"
                   constraint needed.

Template tokens (each element of `"template"`) are one of:
  - a literal run of alphabet characters (e.g. `"c"`, `"bc"`),
  - a variable name (`"word"`/`"string"`/bare `"enum"`),
  - `"rev(<var>)"` for a `"word"`/`"string"` variable,
  - `"<base><superscript-exponent>"` where `<base>` is either a literal
    alphabet character/run or an `"enum"` variable name, and the exponent
    (decoded via `_SUPERSCRIPT_MAP`, e.g. `ⁿ`→`n`, `ⁱ`→`i`, `ʲ`→`j`) names a
    `"nat"` variable. If `<base>` matches neither a literal run nor a
    declared variable (a stand-in placeholder, as in the shipped
    `format1_anbn_union_ancn.json` example, whose template is
    `["aⁿ", "Xⁿ"]` although its only enum variable is named `choice`, not
    `X`) it falls back to the language's sole `"enum"` variable, if there is
    exactly one.

Constraints (each element of `"constraints"`) are either purely informal
(`{"comment": "..."}`, ignored) or a formal boolean expression tree:
`{"op": "and"|"or"|"not", "operands": [...]}` or
`{"op": "eq"|"neq"|"lt"|"leq"|"gt"|"geq", "left": ..., "right": ...}` where
`left`/`right` are each an `int` constant or a variable name (a `"nat"`
variable resolves to its bound integer; any other variable resolves to the
length of its bound string). A constraint with an `"op"` this module doesn't
recognise, or operands it can't resolve to a declared variable/int, makes
the whole oracle unsupported (`None`) rather than being silently ignored —
only comment-only entries are informal.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Callable, Iterator

# ---------------------------------------------------------------------------
# Superscript-exponent decoding
# ---------------------------------------------------------------------------

# Unicode "superscript" letters proper (n, i) plus the IPA "modifier letter
# small <x>" series commonly reused as superscripts in set-builder notation
# (j, k, m, p, x, r, s, t, h, l) when no true superscript codepoint exists.
_SUPERSCRIPT_MAP: dict[str, str] = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3",
    "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7",
    "⁸": "8", "⁹": "9",
    "ⁿ": "n", "ⁱ": "i",
    "ʲ": "j", "ᵏ": "k", "ᵐ": "m", "ᵖ": "p",
    "ˣ": "x", "ʳ": "r", "ˢ": "s", "ᵗ": "t",
    "ʰ": "h", "ˡ": "l",
}

# Safety cap on the recursive search (matcher and generator alike) so a
# malformed/adversarial spec degrades to "no match found" instead of hanging.
_MAX_STEPS = 200_000


def _split_base_exponent(token: str) -> tuple[str, str] | None:
    """Split a template token into (base, decoded-exponent-name).

    Returns None if the token has no trailing superscript run at all.
    """
    i = len(token)
    while i > 0 and token[i - 1] in _SUPERSCRIPT_MAP:
        i -= 1
    if i == len(token):
        return None
    base, exp_raw = token[:i], token[i:]
    exp = "".join(_SUPERSCRIPT_MAP[c] for c in exp_raw)
    return base, exp


# ---------------------------------------------------------------------------
# Template parts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _LiteralPart:
    text: str


@dataclass(frozen=True)
class _EnumPart:
    name: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class _WordPart:
    name: str
    reversed: bool
    alphabet: tuple[str, ...]


@dataclass(frozen=True)
class _ExpLiteralPart:
    text: str
    nat_name: str
    nat_min: int


@dataclass(frozen=True)
class _ExpVarPart:
    enum_name: str
    values: tuple[str, ...]
    nat_name: str
    nat_min: int


_Part = _LiteralPart | _EnumPart | _WordPart | _ExpLiteralPart | _ExpVarPart


def _domain_type(var: dict) -> str | None:
    domain = var.get("domain")
    if not isinstance(domain, dict):
        return None
    t = domain.get("type")
    if t == "string":
        return "word"
    return t


def _var_alphabet(var: dict, language_alphabet: tuple[str, ...]) -> tuple[str, ...]:
    domain = var.get("domain", {})
    alpha = domain.get("alphabet")
    if isinstance(alpha, list) and alpha:
        return tuple(alpha)
    return language_alphabet


def _classify_token(
    token: str, variables: dict[str, dict], alphabet_set: set[str],
    language_alphabet: tuple[str, ...],
) -> _Part | None:
    """Classify one template token. Returns None if unrecognized/unsupported."""
    if not isinstance(token, str) or not token:
        return None

    if token.startswith("rev(") and token.endswith(")"):
        name = token[4:-1]
        var = variables.get(name)
        if var is not None and _domain_type(var) == "word":
            return _WordPart(name=name, reversed=True, alphabet=_var_alphabet(var, language_alphabet))
        return None

    if token in variables:
        var = variables[token]
        dtype = _domain_type(var)
        if dtype == "word":
            return _WordPart(name=token, reversed=False, alphabet=_var_alphabet(var, language_alphabet))
        if dtype == "enum":
            values = var.get("domain", {}).get("values")
            if isinstance(values, list) and values:
                return _EnumPart(name=token, values=tuple(values))
        return None

    split = _split_base_exponent(token)
    if split is not None:
        base, exp_name = split
        nat_var = variables.get(exp_name)
        if nat_var is None or _domain_type(nat_var) != "nat":
            return None
        nat_min = nat_var.get("domain", {}).get("min", 0)
        if not isinstance(nat_min, int) or nat_min < 0:
            nat_min = 0

        if base in variables and _domain_type(variables[base]) == "enum":
            values = variables[base].get("domain", {}).get("values")
            if isinstance(values, list) and values:
                return _ExpVarPart(enum_name=base, values=tuple(values), nat_name=exp_name, nat_min=nat_min)
            return None

        if base and all(ch in alphabet_set for ch in base):
            return _ExpLiteralPart(text=base, nat_name=exp_name, nat_min=nat_min)

        # Stand-in placeholder base (e.g. "Xⁿ" when the enum variable is
        # actually named "choice") — fall back to the sole enum variable.
        enum_names = [n for n, v in variables.items() if _domain_type(v) == "enum"]
        if len(enum_names) == 1:
            name = enum_names[0]
            values = variables[name].get("domain", {}).get("values")
            if isinstance(values, list) and values:
                return _ExpVarPart(enum_name=name, values=tuple(values), nat_name=exp_name, nat_min=nat_min)
        return None

    if all(ch in alphabet_set for ch in token):
        return _LiteralPart(text=token)

    return None


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

_CMP_OPS: dict[str, Callable[[int, int], bool]] = {
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "leq": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "geq": lambda a, b: a >= b,
}
_BOOL_OPS = ("and", "or", "not")


def _validate_operand(operand: Any, variables: dict[str, dict]) -> bool:
    if isinstance(operand, bool):
        return False
    if isinstance(operand, int):
        return True
    if isinstance(operand, str):
        return operand in variables
    return False


def _validate_constraint(node: Any, variables: dict[str, dict]) -> bool:
    """Pre-validate one formal constraint node. `{"comment": ...}` (no "op")
    is informal and always valid (it is simply skipped at eval time)."""
    if not isinstance(node, dict):
        return False
    if "op" not in node:
        return True  # comment-only / informal
    op = node["op"]
    if op in _BOOL_OPS:
        operands = node.get("operands")
        if not isinstance(operands, list) or not operands:
            return False
        return all(_validate_constraint(o, variables) for o in operands)
    if op in _CMP_OPS:
        return (
            "left" in node and "right" in node
            and _validate_operand(node["left"], variables)
            and _validate_operand(node["right"], variables)
        )
    return False


def _resolve_operand(operand: Any, nat_values: dict[str, int], bindings: dict[str, str]) -> int | None:
    if isinstance(operand, int):
        return operand
    if operand in nat_values:
        return nat_values[operand]
    if operand in bindings:
        return len(bindings[operand])
    return None


def _eval_constraint(node: dict, nat_values: dict[str, int], bindings: dict[str, str]) -> bool:
    op = node.get("op")
    if op == "and":
        return all(_eval_constraint(o, nat_values, bindings) for o in node["operands"])
    if op == "or":
        return any(_eval_constraint(o, nat_values, bindings) for o in node["operands"])
    if op == "not":
        return not _eval_constraint(node["operands"][0], nat_values, bindings)
    left = _resolve_operand(node.get("left"), nat_values, bindings)
    right = _resolve_operand(node.get("right"), nat_values, bindings)
    if left is None or right is None:
        return False
    return _CMP_OPS[op](left, right)


# ---------------------------------------------------------------------------
# Spec compilation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _CompiledSpec:
    parts: tuple[_Part, ...]
    constraints: tuple[dict, ...]  # formal constraints only (comments dropped)


def _compile_set_builder(spec: dict) -> _CompiledSpec | None:
    if not isinstance(spec, dict) or spec.get("kind") != "set_builder":
        return None
    alphabet = spec.get("alphabet")
    if not isinstance(alphabet, list) or not alphabet:
        return None
    language_alphabet = tuple(alphabet)
    alphabet_set = set(alphabet)

    variables_list = spec.get("variables")
    if not isinstance(variables_list, list):
        return None
    variables: dict[str, dict] = {}
    for var in variables_list:
        if not isinstance(var, dict) or not isinstance(var.get("name"), str):
            return None
        variables[var["name"]] = var

    template = spec.get("template")
    if not isinstance(template, list) or not template:
        return None
    parts: list[_Part] = []
    for token in template:
        part = _classify_token(token, variables, alphabet_set, language_alphabet)
        if part is None:
            return None
        parts.append(part)

    formal_constraints: list[dict] = []
    for c in spec.get("constraints") or []:
        if not _validate_constraint(c, variables):
            return None
        if isinstance(c, dict) and "op" in c:
            formal_constraints.append(c)

    return _CompiledSpec(parts=tuple(parts), constraints=tuple(formal_constraints))


# ---------------------------------------------------------------------------
# Matching (membership oracle)
# ---------------------------------------------------------------------------

class _StepBudgetExceeded(Exception):
    pass


def _match(
    word: str, pos: int, parts: tuple[_Part, ...], idx: int,
    bindings: dict[str, str], nat_values: dict[str, int],
    constraints: tuple[dict, ...], steps: list[int],
) -> bool:
    steps[0] += 1
    if steps[0] > _MAX_STEPS:
        raise _StepBudgetExceeded()

    if idx == len(parts):
        if pos != len(word):
            return False
        return all(_eval_constraint(c, nat_values, bindings) for c in constraints)

    part = parts[idx]
    n = len(word)

    if isinstance(part, _LiteralPart):
        end = pos + len(part.text)
        if end > n or word[pos:end] != part.text:
            return False
        return _match(word, end, parts, idx + 1, bindings, nat_values, constraints, steps)

    if isinstance(part, _EnumPart):
        for val in part.values:
            end = pos + len(val)
            if end <= n and word[pos:end] == val:
                if _match(word, end, parts, idx + 1, bindings, nat_values, constraints, steps):
                    return True
        return False

    if isinstance(part, _WordPart):
        if part.name in bindings:
            actual = bindings[part.name][::-1] if part.reversed else bindings[part.name]
            end = pos + len(actual)
            if end > n or word[pos:end] != actual:
                return False
            return _match(word, end, parts, idx + 1, bindings, nat_values, constraints, steps)
        allowed = set(part.alphabet)
        for end in range(pos, n + 1):
            seg = word[pos:end]
            if any(ch not in allowed for ch in seg):
                continue
            bound_value = seg[::-1] if part.reversed else seg
            new_bindings = {**bindings, part.name: bound_value}
            if _match(word, end, parts, idx + 1, new_bindings, nat_values, constraints, steps):
                return True
        return False

    if isinstance(part, _ExpLiteralPart):
        unit = part.text
        ulen = len(unit) if unit else 1
        if part.nat_name in nat_values:
            count = nat_values[part.nat_name]
            seg = unit * count
            end = pos + len(seg)
            if end > n or word[pos:end] != seg:
                return False
            return _match(word, end, parts, idx + 1, bindings, nat_values, constraints, steps)
        max_count = (n - pos) // ulen if ulen else 0
        for count in range(part.nat_min, max_count + 1):
            seg = unit * count
            end = pos + len(seg)
            if word[pos:end] != seg:
                continue
            new_nat = {**nat_values, part.nat_name: count}
            if _match(word, end, parts, idx + 1, bindings, new_nat, constraints, steps):
                return True
        return False

    if isinstance(part, _ExpVarPart):
        enum_bound = part.enum_name in bindings
        nat_bound = part.nat_name in nat_values
        candidate_values = (bindings[part.enum_name],) if enum_bound else part.values
        for val in candidate_values:
            vlen = len(val) if val else 1
            if nat_bound:
                counts = (nat_values[part.nat_name],)
            else:
                max_count = (n - pos) // vlen if vlen else 0
                counts = range(part.nat_min, max_count + 1)
            for count in counts:
                seg = val * count
                end = pos + len(seg)
                if end > n or word[pos:end] != seg:
                    continue
                new_bindings = bindings if enum_bound else {**bindings, part.enum_name: val}
                new_nat = nat_values if nat_bound else {**nat_values, part.nat_name: count}
                if _match(word, end, parts, idx + 1, new_bindings, new_nat, constraints, steps):
                    return True
        return False

    return False  # pragma: no cover — exhaustive Part union above


def _build_oracle(compiled: _CompiledSpec) -> Callable[[str], bool]:
    def oracle(word: str) -> bool:
        if not isinstance(word, str):
            return False
        try:
            return _match(word, 0, compiled.parts, 0, {}, {}, compiled.constraints, [0])
        except _StepBudgetExceeded:
            return False

    return oracle


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def task_grammar_from_ir(ir: dict) -> dict | None:
    """The explicit grammar given by the task, if any.

    Format 2 (`ll_check_grammar_lang`): `language_spec` of kind "grammar".
    Format 3 (`ll_check_grammar`): the top-level `grammar`.
    Returns the raw (un-normalized) grammar dict, or None.
    """
    if not isinstance(ir, dict):
        return None
    spec = ir.get("language_spec")
    if isinstance(spec, dict) and spec.get("kind") == "grammar":
        return spec
    if ir.get("task_type") == "ll_check_grammar":
        g = ir.get("grammar")
        if isinstance(g, dict):
            return g
    return None


def _grammar_oracle(grammar: dict) -> Callable[[str], bool] | None:
    """Exact CYK membership oracle for an explicit grammar.

    Reuses the CNF/CYK machinery of cfl_system (via `grammar_transforms`'
    `_grammar_membership`, which also maps the "ε" rhs spelling to []).
    Returns None -- "no oracle", never "not in L" -- when the grammar is not
    a character-level grammar over declared symbols that the CYK code can
    interpret (multi-character or reserved terminals, conversion failure).
    """
    try:
        from ll_system.lib.grammar_transforms import _grammar_membership
        from ll_system.lib.utils import RESERVED_SYMBOLS

        terminals = grammar.get("terminals")
        nonterminals = grammar.get("nonterminals")
        if not isinstance(terminals, list) or not isinstance(nonterminals, list):
            return None
        if any(not isinstance(t, str) or len(t) != 1 for t in terminals):
            return None
        if (set(terminals) | set(nonterminals)) & RESERVED_SYMBOLS:
            return None
        accepts = _grammar_membership(grammar)
    except Exception:
        return None

    def oracle(word: str) -> bool | None:
        if not isinstance(word, str):
            return None
        if any(ch not in terminals for ch in word):
            return False  # a symbol outside the grammar's alphabet is never generated
        try:
            return bool(accepts(word))
        except Exception:
            return None  # unknown, not "not in L"

    return oracle


def oracle_from_ll_ir(ir: dict) -> Callable[[str], bool] | None:
    """Build a membership oracle (word -> bool) from an ll_system IR.

    - Format 1 (`language_spec.kind == "set_builder"`): the template matcher
      of this module.
    - Format 2/3 (explicit grammar: `language_spec.kind == "grammar"` or the
      top-level `grammar` of an `ll_check_grammar` task): CYK over the CNF of
      that grammar.

    Returns None when neither applies, or the spec uses a shape this module
    doesn't understand -- never raises for a malformed-but-well-typed IR.
    The oracle itself may answer None for a word (unknown); callers must not
    read that as "not in L" (docs/VERDICT_POLICY.md section 4).
    """
    if not isinstance(ir, dict):
        return None
    grammar = task_grammar_from_ir(ir)
    if grammar is not None:
        return _grammar_oracle(grammar)
    spec = ir.get("language_spec")
    if not isinstance(spec, dict):
        return None
    try:
        compiled = _compile_set_builder(spec)
    except Exception:
        return None
    if compiled is None:
        return None
    return _build_oracle(compiled)


# ---------------------------------------------------------------------------
# Word generation (bounded, for sample-equivalence checks)
# ---------------------------------------------------------------------------

def _generate(
    parts: tuple[_Part, ...], idx: int, remaining: int,
    bindings: dict[str, str], nat_values: dict[str, int], steps: list[int],
) -> Iterator[tuple[str, dict[str, str], dict[str, int]]]:
    steps[0] += 1
    if steps[0] > _MAX_STEPS:
        raise _StepBudgetExceeded()

    if idx == len(parts):
        yield "", bindings, nat_values
        return

    part = parts[idx]

    if isinstance(part, _LiteralPart):
        if len(part.text) <= remaining:
            for suf, bd, nv in _generate(parts, idx + 1, remaining - len(part.text), bindings, nat_values, steps):
                yield part.text + suf, bd, nv
        return

    if isinstance(part, _EnumPart):
        for val in part.values:
            if len(val) <= remaining:
                for suf, bd, nv in _generate(parts, idx + 1, remaining - len(val), bindings, nat_values, steps):
                    yield val + suf, bd, nv
        return

    if isinstance(part, _WordPart):
        if part.name in bindings:
            actual = bindings[part.name][::-1] if part.reversed else bindings[part.name]
            if len(actual) <= remaining:
                for suf, bd, nv in _generate(parts, idx + 1, remaining - len(actual), bindings, nat_values, steps):
                    yield actual + suf, bd, nv
            return
        for length in range(0, remaining + 1):
            if not part.alphabet and length > 0:
                continue
            for combo in itertools.product(part.alphabet, repeat=length):
                seg = "".join(combo)
                bound_value = seg[::-1] if part.reversed else seg
                new_bindings = {**bindings, part.name: bound_value}
                for suf, bd, nv in _generate(parts, idx + 1, remaining - length, new_bindings, nat_values, steps):
                    yield seg + suf, bd, nv
        return

    if isinstance(part, _ExpLiteralPart):
        unit = part.text
        ulen = len(unit) if unit else 1
        if part.nat_name in nat_values:
            count = nat_values[part.nat_name]
            seg = unit * count
            if len(seg) <= remaining:
                for suf, bd, nv in _generate(parts, idx + 1, remaining - len(seg), bindings, nat_values, steps):
                    yield seg + suf, bd, nv
            return
        max_count = remaining // ulen if ulen else 0
        for count in range(part.nat_min, max_count + 1):
            seg = unit * count
            new_nat = {**nat_values, part.nat_name: count}
            for suf, bd, nv in _generate(parts, idx + 1, remaining - len(seg), bindings, new_nat, steps):
                yield seg + suf, bd, nv
        return

    if isinstance(part, _ExpVarPart):
        enum_bound = part.enum_name in bindings
        nat_bound = part.nat_name in nat_values
        candidate_values = (bindings[part.enum_name],) if enum_bound else part.values
        for val in candidate_values:
            vlen = len(val) if val else 1
            if nat_bound:
                counts = (nat_values[part.nat_name],)
            else:
                max_count = remaining // vlen if vlen else 0
                counts = range(part.nat_min, max_count + 1)
            for count in counts:
                seg = val * count
                if len(seg) > remaining:
                    continue
                new_bindings = bindings if enum_bound else {**bindings, part.enum_name: val}
                new_nat = nat_values if nat_bound else {**nat_values, part.nat_name: count}
                for suf, bd, nv in _generate(parts, idx + 1, remaining - len(seg), new_bindings, new_nat, steps):
                    yield seg + suf, bd, nv
        return


def _generate_grammar_words(grammar: dict, max_len: int) -> list[str]:
    """Words of length <= max_len derived from an explicit grammar.

    Best-effort (bounded sentential-form BFS: the result may omit words) and
    filtered through the exact CYK oracle, so every returned word is in L.
    Returns [] when no oracle can be built.
    """
    oracle = _grammar_oracle(grammar)
    if oracle is None:
        return []
    try:
        from ll_system.lib.grammar_transforms import _generate_words
        candidates = _generate_words(grammar, max_len)
    except Exception:
        return []
    return sorted(
        (w for w in candidates if len(w) <= max_len and oracle(w) is True),
        key=lambda w: (len(w), w),
    )


def generate_words(ir: dict, max_len: int) -> list[str]:
    """Enumerate words of length <= max_len generated by a `set_builder` IR.

    Best-effort, for sample-equivalence checks (docs/VERDICT_POLICY.md §4):
    returns [] when the spec isn't supported (same "None-able" cases as
    `oracle_from_ll_ir`) rather than raising. The returned list already
    satisfies every formal constraint — generation ignores constraints while
    building candidates (for simplicity — only structural consistency
    between repeated variable/exponent occurrences is enforced during
    generation) and this function filters the result through the full
    oracle before returning it, so callers never see a constraint violation.
    """
    if not isinstance(ir, dict) or not isinstance(max_len, int) or max_len < 0:
        return []
    grammar = task_grammar_from_ir(ir)
    if grammar is not None:
        return _generate_grammar_words(grammar, max_len)
    spec = ir.get("language_spec")
    if not isinstance(spec, dict):
        return []
    try:
        compiled = _compile_set_builder(spec)
    except Exception:
        return []
    if compiled is None:
        return []

    oracle = _build_oracle(compiled)
    words: set[str] = set()
    try:
        for word, _bindings, _nat in _generate(compiled.parts, 0, max_len, {}, {}, [0]):
            words.add(word)
    except _StepBudgetExceeded:
        pass
    return sorted((w for w in words if oracle(w)), key=lambda w: (len(w), w))


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    import json
    import sys

    with open(sys.argv[1], encoding="utf-8") as f:
        _ir = json.load(f)
    _oracle = oracle_from_ll_ir(_ir)
    if _oracle is None:
        print("unsupported set_builder spec")
    else:
        _max_len = int(sys.argv[2]) if len(sys.argv) > 2 else 8
        for w in generate_words(_ir, _max_len):
            print(repr(w))
