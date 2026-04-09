"""Preprocessing for LL analysis: regularity check, pattern detection."""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Regularity detection
# ---------------------------------------------------------------------------

def check_regularity_hints(ir: dict) -> dict:
    """Check if the language appears to be regular based on structural analysis.

    Returns:
    {
        "is_regular": bool,
        "confidence": float,  # 0.0 to 1.0
        "reason": str | None,
        "method": str | None  # "finite", "regex_pattern", "trivial_constraint", etc.
    }

    Checks (in order of confidence):
    1. task_type is ll_check_grammar with grammar that only accepts finite language
       → is_regular=True (finite languages are regular)
    2. language_spec kind is "regex" → is_regular=True
    3. Set-builder with no constraints or trivially empty constraints → check
    4. Set-builder where all variables are bounded → may be finite/regular

    This is a heuristic/advisory function. Returns is_regular=False when uncertain.
    """
    task_type = ir.get("task_type", "")

    # Check 1: grammar at top level — is it finite (no recursion)?
    if task_type == "ll_check_grammar":
        grammar = ir.get("grammar")
        if grammar and isinstance(grammar, dict):
            if _is_finite_grammar(grammar):
                return {
                    "is_regular": True,
                    "confidence": 0.9,
                    "reason": "Grammar generates a finite language (no recursive rules)",
                    "method": "finite",
                }
            return {
                "is_regular": False,
                "confidence": 0.7,
                "reason": "Grammar has recursive rules; language may be infinite",
                "method": None,
            }

    # Check 2: language_spec kind is "regex"
    language_spec = ir.get("language_spec", {})
    if isinstance(language_spec, dict):
        kind = language_spec.get("kind")
        if kind == "regex":
            return {
                "is_regular": True,
                "confidence": 1.0,
                "reason": "Language is specified as a regular expression",
                "method": "regex_pattern",
            }

        # Check 3: set_builder with no unbounded variables
        if kind == "set_builder":
            variables = language_spec.get("variables", [])
            if not variables:
                return {
                    "is_regular": True,
                    "confidence": 0.8,
                    "reason": "Set-builder has no variables; language is trivially finite",
                    "method": "trivial_constraint",
                }
            # Check 4: all variables are bounded (e.g., domain type "bounded" or explicit upper)
            all_bounded = all(
                isinstance(v, dict)
                and isinstance(v.get("domain"), dict)
                and v["domain"].get("type") in ("bounded", "finite", "range")
                for v in variables
            )
            if all_bounded:
                return {
                    "is_regular": True,
                    "confidence": 0.75,
                    "reason": "All set-builder variables are bounded; language may be finite/regular",
                    "method": "trivial_constraint",
                }

    # Default: uncertain
    return {
        "is_regular": False,
        "confidence": 0.5,
        "reason": None,
        "method": None,
    }


def _is_finite_grammar(grammar: dict) -> bool:
    """Heuristic: check if grammar generates only finite language.

    A grammar generates a finite language if there are no cycles
    (no nonterminal A such that A =>+ A through any derivation).
    Uses reachability analysis.
    """
    return _has_no_recursive_rules(grammar)


def _has_no_recursive_rules(grammar: dict) -> bool:
    """Return True if grammar has no recursive nonterminals.

    Builds a dependency graph where A → B means nonterminal A can derive
    nonterminal B in one rule step.  Then checks for cycles via DFS.
    """
    nonterminals: set[str] = set(grammar.get("nonterminals", []))
    rules: list[dict] = grammar.get("rules", [])

    # Build adjacency list: reachable nonterminals from each nonterminal
    deps: dict[str, set[str]] = {nt: set() for nt in nonterminals}
    for rule in rules:
        lhs: str = rule.get("lhs", "")
        rhs: list[str] = rule.get("rhs", [])
        if lhs in nonterminals:
            for sym in rhs:
                if sym in nonterminals:
                    deps[lhs].add(sym)

    # Check for cycles using DFS from each nonterminal
    def _has_cycle(start: str) -> bool:
        visited: set[str] = set()
        stack: list[str] = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                if node == start:
                    return True
                continue
            visited.add(node)
            for neighbour in deps.get(node, set()):
                if neighbour == start:
                    return True
                if neighbour not in visited:
                    stack.append(neighbour)
        return False

    for nt in nonterminals:
        if _has_cycle(nt):
            return False
    return True


# ---------------------------------------------------------------------------
# Disjunction pattern detection
# ---------------------------------------------------------------------------

def detect_disjunction_pattern(ir: dict) -> dict:
    """Detect structural patterns that suggest the language is not LL.

    Returns:
    {
        "detected": bool,
        "pattern_type": str | None,
        "shared_prefix": str | None,
        "branches": list[str] | None,
        "shared_counter": str | None,
        "description": str | None
    }

    Pattern types:
    1. suffix_disjunction: language is union of two languages with same prefix
       but different suffixes (e.g., {aⁿbⁿ} ∪ {aⁿcⁿ})
    2. prefix_ambiguity: grammar has rules with same FIRST set
    3. palindrome: language contains palindromic structure without explicit midpoint marker
    4. symmetric: w and w^R both in language with same length condition
    """
    task_type = ir.get("task_type", "")
    language_spec = ir.get("language_spec", {})

    _NOT_DETECTED: dict = {
        "detected": False,
        "pattern_type": None,
        "shared_prefix": None,
        "branches": None,
        "shared_counter": None,
        "description": None,
    }

    # From set_builder spec
    if isinstance(language_spec, dict) and language_spec.get("kind") == "set_builder":
        result = _detect_suffix_disjunction_from_setbuilder(language_spec)
        if result["detected"]:
            return result

    # From grammar (Format 2 or 3)
    grammar: dict | None = None
    if task_type == "ll_check_grammar":
        grammar = ir.get("grammar")
    elif isinstance(language_spec, dict) and language_spec.get("kind") == "grammar":
        grammar = language_spec
    if grammar and isinstance(grammar, dict):
        result = _detect_prefix_ambiguity_from_grammar(grammar)
        if result["detected"]:
            return result

    # From source_text heuristics
    source_text: str = ir.get("source_text", "")
    if source_text:
        # Palindrome indicator
        if any(kw in source_text.lower() for kw in ("palindrom", "ww^r", "w^r", "reverse")):
            return {
                "detected": True,
                "pattern_type": "palindrome",
                "shared_prefix": None,
                "branches": None,
                "shared_counter": None,
                "description": "Source text mentions palindrome / reversal structure",
            }
        # Union of two similar languages
        if "∪" in source_text or " union " in source_text.lower():
            return {
                "detected": True,
                "pattern_type": "suffix_disjunction",
                "shared_prefix": None,
                "branches": None,
                "shared_counter": None,
                "description": "Language is defined as a union, which may cause prefix ambiguity",
            }

    return _NOT_DETECTED


def _detect_suffix_disjunction_from_setbuilder(spec: dict) -> dict:
    """Detect suffix disjunction from set-builder spec.

    Looks for patterns like template = ["aⁿ", "Xⁿ"] where variables share
    a counter, suggesting the language encodes a union-like structure in
    its template (or the source_text indicates a union).
    """
    _no: dict = {
        "detected": False,
        "pattern_type": None,
        "shared_prefix": None,
        "branches": None,
        "shared_counter": None,
        "description": None,
    }

    template: list[str] = spec.get("template", [])
    variables: list[dict] = spec.get("variables", [])

    if not template or not variables:
        return _no

    # Extract counters referenced in template
    import re
    counters = {v["name"] for v in variables if isinstance(v, dict) and "name" in v}

    # Look for a template that has a shared prefix followed by an ambiguous suffix
    # e.g., ["aⁿ", "Xⁿ"] — the variable n appears in multiple positions
    # with different terminal prefixes
    template_str = " ".join(template)
    matching_counters = [c for c in counters if template_str.count(c) > 1]

    if matching_counters:
        # Heuristic: if the same counter appears more than once with different
        # surrounding terminals, that suggests a suffix disjunction pattern
        shared_counter = matching_counters[0]
        # Try to identify the branches (split by counter occurrences)
        parts = re.split(rf"{re.escape(shared_counter)}", template_str)
        if len(parts) >= 3:
            # There are at least two occurrences → could be e.g. "a^n b^n | a^n c^n"
            prefix_part = parts[0].strip()
            suffix_parts = [p.strip() for p in parts[1:] if p.strip()]
            return {
                "detected": True,
                "pattern_type": "suffix_disjunction",
                "shared_prefix": f"{prefix_part}{shared_counter}" if prefix_part else shared_counter,
                "branches": suffix_parts,
                "shared_counter": shared_counter,
                "description": (
                    f"Template uses counter '{shared_counter}' multiple times with "
                    f"different surrounding symbols, suggesting a suffix disjunction pattern"
                ),
            }

    return _no


def _detect_prefix_ambiguity_from_grammar(grammar: dict) -> dict:
    """Detect ambiguous prefixes in grammar rules.

    Checks for left recursion and for rules of the same nonterminal that
    share a common terminal prefix in their RHS (FIRST conflict indicator).
    """
    _no: dict = {
        "detected": False,
        "pattern_type": None,
        "shared_prefix": None,
        "branches": None,
        "shared_counter": None,
        "description": None,
    }

    nonterminals: set[str] = set(grammar.get("nonterminals", []))
    terminals: set[str] = set(grammar.get("terminals", []))
    rules: list[dict] = grammar.get("rules", [])

    # Check for left recursion
    for rule in rules:
        lhs = rule.get("lhs", "")
        rhs = rule.get("rhs", [])
        if rhs and rhs[0] == lhs:
            return {
                "detected": True,
                "pattern_type": "prefix_ambiguity",
                "shared_prefix": lhs,
                "branches": None,
                "shared_counter": None,
                "description": f"Grammar has direct left recursion on '{lhs}'",
            }

    # Check for rules of same nonterminal sharing first terminal
    from collections import defaultdict
    rules_by_nt: dict[str, list[list[str]]] = defaultdict(list)
    for rule in rules:
        lhs = rule.get("lhs", "")
        rhs = rule.get("rhs", [])
        rules_by_nt[lhs].append(rhs)

    for nt, rhss in rules_by_nt.items():
        first_terminals: dict[str, list[list[str]]] = defaultdict(list)
        for rhs in rhss:
            if rhs and rhs[0] in terminals:
                first_terminals[rhs[0]].append(rhs)
        for term, competing in first_terminals.items():
            if len(competing) > 1:
                return {
                    "detected": True,
                    "pattern_type": "prefix_ambiguity",
                    "shared_prefix": term,
                    "branches": [str(r) for r in competing],
                    "shared_counter": None,
                    "description": (
                        f"Nonterminal '{nt}' has multiple rules starting with terminal '{term}' "
                        f"(FIRST/FIRST conflict)"
                    ),
                }

    return _no


# ---------------------------------------------------------------------------
# Structural feature extraction
# ---------------------------------------------------------------------------

def extract_structural_features(ir: dict) -> list[str]:
    """Extract structural features of the language for hints.

    Returns list of feature strings from:
    ["palindrome_construction", "explicit_midpoint_marker", "length_condition",
     "disjunction_in_suffix", "counting_constraint", "reversal_component",
     "union_structure", "left_recursive_grammar", "ambiguous_grammar"]
    """
    features: list[str] = []
    task_type = ir.get("task_type", "")
    source_text: str = ir.get("source_text", "").lower()
    language_spec = ir.get("language_spec", {})

    # Gather grammar (from top-level or language_spec)
    grammar: dict | None = None
    if task_type == "ll_check_grammar":
        grammar = ir.get("grammar")
    elif isinstance(language_spec, dict) and language_spec.get("kind") == "grammar":
        grammar = language_spec

    # Palindrome
    if any(kw in source_text for kw in ("palindrom", "ww^r", "w^r", "reverse")):
        features.append("palindrome_construction")

    # Explicit midpoint marker
    if any(kw in source_text for kw in ("marker", "midpoint", "middle", "center", "#", "c#")):
        features.append("explicit_midpoint_marker")
    if isinstance(language_spec, dict):
        template = language_spec.get("template", [])
        if "#" in template or "c" in template:
            features.append("explicit_midpoint_marker")

    # Length condition
    if any(kw in source_text for kw in ("|w|", "length", "|w|_a", "|w|_b", "= 2")):
        features.append("length_condition")
    if _has_counting_constraint(language_spec if isinstance(language_spec, dict) else {}):
        if "length_condition" not in features:
            features.append("length_condition")
        features.append("counting_constraint")

    # Union / disjunction
    if "∪" in ir.get("source_text", "") or "union" in source_text:
        features.append("union_structure")
        features.append("disjunction_in_suffix")

    # Reversal
    if _has_reversal_component(language_spec if isinstance(language_spec, dict) else {}):
        if "reversal_component" not in features:
            features.append("reversal_component")

    # Grammar-specific features
    if grammar and isinstance(grammar, dict):
        nonterminals: set[str] = set(grammar.get("nonterminals", []))
        rules: list[dict] = grammar.get("rules", [])

        # Left recursion
        for rule in rules:
            lhs = rule.get("lhs", "")
            rhs = rule.get("rhs", [])
            if rhs and rhs[0] == lhs:
                if "left_recursive_grammar" not in features:
                    features.append("left_recursive_grammar")
                break

        # Indirect left recursion
        if "left_recursive_grammar" not in features:
            from collections import defaultdict
            deps: dict[str, set[str]] = defaultdict(set)
            for rule in rules:
                lhs = rule.get("lhs", "")
                rhs = rule.get("rhs", [])
                if rhs and rhs[0] in nonterminals:
                    deps[lhs].add(rhs[0])
            # DFS to detect cycles indicating indirect left recursion
            for start in nonterminals:
                visited: set[str] = set()
                stack = list(deps.get(start, set()))
                found = False
                while stack and not found:
                    node = stack.pop()
                    if node == start:
                        found = True
                        break
                    if node not in visited:
                        visited.add(node)
                        stack.extend(deps.get(node, set()))
                if found:
                    features.append("left_recursive_grammar")
                    break

        # Ambiguity: rules of same NT sharing first terminal (FIRST/FIRST)
        from collections import defaultdict as _defaultdict
        terminals_set: set[str] = set(grammar.get("terminals", []))
        rules_by_nt: dict[str, list] = _defaultdict(list)
        for rule in rules:
            rules_by_nt[rule.get("lhs", "")].append(rule.get("rhs", []))
        for nt, rhss in rules_by_nt.items():
            first_syms = [r[0] for r in rhss if r and r[0] in terminals_set]
            if len(first_syms) != len(set(first_syms)):
                if "ambiguous_grammar" not in features:
                    features.append("ambiguous_grammar")
                break

    return features


def _has_reversal_component(spec: dict) -> bool:
    """Check if language spec involves string reversal (w^R pattern)."""
    if not spec:
        return False
    spec_str = str(spec).lower()
    return any(kw in spec_str for kw in ("reverse", "w^r", "ww^r", "^r", "reversal"))


def _has_counting_constraint(spec: dict) -> bool:
    """Check if spec has counting constraints (|w|_a = |w|_b type)."""
    if not spec:
        return False
    constraints = spec.get("constraints", [])
    if constraints:
        return True
    # Check variables for nat domain (suggests counting)
    variables = spec.get("variables", [])
    for v in variables:
        if isinstance(v, dict):
            domain = v.get("domain", {})
            if isinstance(domain, dict) and domain.get("type") in ("nat", "integer", "positive"):
                return True
    return False


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def compute_preprocess_hints(ir: dict) -> dict:
    """Compute full set of preprocessing hints for the pipeline.

    Returns the preprocess_hints dict as per spec §4.2:
    {
        "is_regular": bool,
        "regularity_confidence": float,
        "regularity_reason": str | None,
        "disjunction_pattern": {
            "detected": bool,
            "pattern_type": str | None,
            "shared_prefix": str | None,
            "branches": list[str] | None,
            "shared_counter": str | None,
            "description": str | None
        },
        "extracted_language": None,  # Phase 1: not implemented, always None
        "structural_features": list[str]
    }
    """
    reg = check_regularity_hints(ir)
    disjunction = detect_disjunction_pattern(ir)
    features = extract_structural_features(ir)

    return {
        "is_regular": reg["is_regular"],
        "regularity_confidence": reg["confidence"],
        "regularity_reason": reg["reason"],
        "disjunction_pattern": disjunction,
        "extracted_language": None,
        "structural_features": features,
    }
