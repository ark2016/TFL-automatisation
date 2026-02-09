"""Natural language parser: uses Claude API to convert NL description to LanguageSpec."""
import re
import json
from typing import Optional

from pumping_lemma.models.language_spec import LanguageSpec, ParikhConstraint
from pumping_lemma.llm.client import LLMClient
from pumping_lemma.llm.prompts import SYSTEM_PROMPT, PARSE_LANGUAGE_PROMPT


class NLParser:
    """Parse natural language description of a formal language into LanguageSpec."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    def parse(self, description: str) -> LanguageSpec:
        """Parse NL description into LanguageSpec."""
        # Try rule-based parsing first
        spec = self._try_rule_based(description)
        if spec and spec.has_membership_test():
            return spec

        # Fall back to LLM parsing
        if self.llm.is_available():
            return self._parse_with_llm(description)

        # Minimal spec if nothing else works
        return LanguageSpec(description=description)

    def _try_rule_based(self, description: str) -> Optional[LanguageSpec]:
        """Try to parse common patterns without LLM."""
        desc = description.strip()

        # Pattern: {a^n b^n c^n | n >= 0}  — checked BEFORE a^n b^n to avoid shadowing
        m = re.search(r'a\^n\s*b\^n\s*c\^n', desc)
        if m:
            def check_anbncn(w):
                n = len(w)
                if n % 3 != 0: return False
                k = n // 3
                return w == 'a' * k + 'b' * k + 'c' * k
            return LanguageSpec(
                description=desc,
                alphabet={'a', 'b', 'c'},
                spec_type='set_builder',
                structural_pattern='balanced',
                constraints=[
                    ParikhConstraint(constraint_type='equal', symbols=['a', 'b'], description='count(a)==count(b)'),
                    ParikhConstraint(constraint_type='equal', symbols=['b', 'c'], description='count(b)==count(c)'),
                ],
                membership_fn=check_anbncn
            )

        # Pattern: {a^n b^n | n >= 0}
        m = re.search(r'a\^n\s*b\^n', desc)
        if m:
            return LanguageSpec(
                description=desc,
                alphabet={'a', 'b'},
                spec_type='set_builder',
                structural_pattern='balanced',
                constraints=[ParikhConstraint(
                    constraint_type='equal', symbols=['a', 'b'],
                    description='count(a) == count(b)'
                )],
                membership_fn=lambda w: (
                    len(w) % 2 == 0 and
                    all(c == 'a' for c in w[:len(w)//2]) and
                    all(c == 'b' for c in w[len(w)//2:]) and
                    w.count('a') == w.count('b')
                )
            )

        # Pattern: {ww | w in {a,b}*}
        if re.search(r'ww\b', desc) and not re.search(r'w\^?R', desc):
            def check_ww(w):
                if len(w) % 2 != 0: return False
                half = len(w) // 2
                return w[:half] == w[half:]
            return LanguageSpec(
                description=desc,
                alphabet={'a', 'b'},
                spec_type='set_builder',
                structural_pattern='copy',
                membership_fn=check_ww
            )

        # Pattern: palindromes
        if re.search(r'палиндром|palindrome', desc, re.IGNORECASE):
            return LanguageSpec(
                description=desc,
                alphabet={'a', 'b'},
                spec_type='set_builder',
                structural_pattern='palindrome',
                membership_fn=lambda w: w == w[::-1]
            )

        # Pattern: {a^(n^2) | n >= 0} or {a^(n²)}
        if re.search(r'n\^2|n²|квадрат|square', desc, re.IGNORECASE):
            def check_square(w):
                if not w or not all(c == w[0] for c in w): return False
                from pumping_lemma.utils.math_utils import is_perfect_square
                return is_perfect_square(len(w))
            return LanguageSpec(
                description=desc,
                alphabet={'a'},
                spec_type='set_builder',
                structural_pattern='power',
                membership_fn=check_square
            )

        # Pattern: {a^p | p is prime}
        if re.search(r'прост|prime', desc, re.IGNORECASE) and re.search(r'a\^', desc):
            def check_prime_power(w):
                if not w or not all(c == 'a' for c in w): return False
                from pumping_lemma.utils.math_utils import is_prime
                return is_prime(len(w))
            return LanguageSpec(
                description=desc,
                alphabet={'a'},
                spec_type='set_builder',
                structural_pattern='power',
                membership_fn=check_prime_power
            )

        return None

    def _parse_with_llm(self, description: str) -> LanguageSpec:
        """Use LLM to parse language description."""
        prompt = PARSE_LANGUAGE_PROMPT.format(description=description)
        try:
            data = self.llm.complete_json(prompt, system=SYSTEM_PROMPT)
        except Exception:
            return LanguageSpec(description=description)

        alphabet = set(data.get('alphabet', []))
        constraints = []
        for c in data.get('constraints', []):
            constraints.append(ParikhConstraint(
                constraint_type=c.get('constraint_type', 'equal'),
                symbols=c.get('symbols', []),
                coefficients=c.get('coefficients', []),
                constant=c.get('constant', 0),
                description=c.get('description', '')
            ))

        membership_fn = None
        # NOTE: We intentionally do NOT eval() LLM-returned code.
        # Instead, build membership functions from structured data only.

        return LanguageSpec(
            description=description,
            alphabet=alphabet,
            spec_type=data.get('spec_type', 'unknown'),
            regex=data.get('regex'),
            structural_pattern=data.get('structural_pattern'),
            constraints=constraints,
            membership_fn=membership_fn,
        )
