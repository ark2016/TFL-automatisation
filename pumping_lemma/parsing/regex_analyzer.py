"""Regex analyzer: bridge to reverse_morfism DFA construction."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from typing import Optional
from pumping_lemma.models.language_spec import LanguageSpec

try:
    from reverse_morfism import (
        DFA, ThompsonConstruction, SubsetConstruction,
        make_total_dfa, minimize_dfa, dfa_to_regex
    )
    HAS_REVERSE_MORFISM = True
except ImportError:
    HAS_REVERSE_MORFISM = False


class RegexAnalyzer:
    """Analyze regular expressions using DFA construction from reverse_morfism."""

    def __init__(self):
        if not HAS_REVERSE_MORFISM:
            raise ImportError("reverse_morfism package required")

    def regex_to_dfa(self, regex: str) -> 'DFA':
        """Convert regex to minimized DFA."""
        nfa = ThompsonConstruction().build(regex)
        dfa = SubsetConstruction.convert(nfa)
        dfa = make_total_dfa(dfa)
        dfa = minimize_dfa(dfa)
        return dfa

    def regex_to_spec(self, regex: str) -> LanguageSpec:
        """Convert regex to LanguageSpec with DFA membership."""
        dfa = self.regex_to_dfa(regex)
        return LanguageSpec(
            description=f"L({regex})",
            alphabet=dfa.alphabet,
            spec_type='regex',
            regex=regex,
            dfa=dfa,
            membership_fn=dfa.accepts,
        )

    def is_regex_valid(self, regex: str) -> bool:
        """Check if regex can be parsed."""
        try:
            ThompsonConstruction().build(regex)
            return True
        except Exception:
            return False
