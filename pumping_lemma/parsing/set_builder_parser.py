"""Parser for set-builder notation: {w | condition}."""
import re
from typing import Optional, Callable
from pumping_lemma.models.language_spec import LanguageSpec, ParikhConstraint


class SetBuilderParser:
    """Parse set-builder notation like {w ∈ Σ* | condition}."""

    def parse(self, notation: str) -> Optional[LanguageSpec]:
        """Parse set-builder notation into LanguageSpec."""
        notation = notation.strip().strip('{}')

        # Split on | or :
        parts = re.split(r'\s*[|:]\s*', notation, maxsplit=1)
        if len(parts) != 2:
            return None

        element_part, condition_part = parts

        # Extract alphabet from element part
        alphabet = self._extract_alphabet(element_part, condition_part)

        # Analyze condition
        constraints = self._extract_constraints(condition_part, alphabet)
        pattern = self._identify_pattern(condition_part)
        membership_fn = self._build_membership_fn(condition_part, alphabet)

        return LanguageSpec(
            description=notation,
            alphabet=alphabet,
            spec_type='set_builder',
            structural_pattern=pattern,
            constraints=constraints,
            membership_fn=membership_fn,
        )

    def _extract_alphabet(self, element_part: str, condition_part: str) -> set:
        """Extract alphabet from notation."""
        # Look for explicit alphabet: w ∈ {a,b}*
        m = re.search(r'\{([a-z,\s]+)\}\*?', element_part)
        if m:
            return set(c.strip() for c in m.group(1).split(',') if c.strip())
        # Infer from symbols used in conditions
        symbols = set(re.findall(r'\b([a-z])\b', condition_part))
        # Filter out common variable names
        symbols -= {'n', 'i', 'j', 'k', 'm', 'w', 'u', 'v', 'x', 'y', 'z'}
        return symbols or {'a', 'b'}

    def _extract_constraints(self, condition: str, alphabet: set) -> list:
        """Extract Parikh constraints from condition."""
        constraints = []
        # Pattern: |w|_a = |w|_b or count(a) = count(b)
        for m in re.finditer(r'\|w\|_([a-z])\s*=\s*\|w\|_([a-z])', condition):
            constraints.append(ParikhConstraint(
                constraint_type='equal',
                symbols=[m.group(1), m.group(2)],
                description=f'count({m.group(1)}) == count({m.group(2)})'
            ))
        return constraints

    def _identify_pattern(self, condition: str) -> Optional[str]:
        """Identify structural pattern from condition."""
        if re.search(r'palindrom|w\s*=\s*w\^?R|w\s*=\s*rev', condition, re.IGNORECASE):
            return 'palindrome'
        if re.search(r'ww\b|w\s*=\s*uu', condition):
            return 'copy'
        if re.search(r'n\^2|n²', condition):
            return 'power'
        if re.search(r'\|w\|_.\s*=\s*\|w\|_.', condition):
            return 'balanced'
        return None

    def _build_membership_fn(self, condition: str, alphabet: set) -> Optional[Callable]:
        """Try to build a membership function from condition. Returns None if too complex."""
        # Only handle simple well-known patterns
        return None
