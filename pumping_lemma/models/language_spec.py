from dataclasses import dataclass, field
from typing import Set, Optional, Callable, List


@dataclass
class ParikhConstraint:
    """Constraint on Parikh vectors, e.g. count(a) == count(b)"""
    constraint_type: str  # "equal", "less", "greater", "linear", "modular"
    symbols: List[str]  # involved symbols
    coefficients: List[int] = field(default_factory=list)  # for linear: c1*|w|_a1 + c2*|w|_a2 = c0
    constant: int = 0
    description: str = ""


@dataclass
class LanguageSpec:
    description: str
    alphabet: Set[str] = field(default_factory=set)
    spec_type: str = "unknown"  # "regex", "set_builder", "grammar", "oracle"
    regex: Optional[str] = None
    dfa: Optional[object] = None  # DFA from reverse_morfism
    membership_fn: Optional[Callable[[str], bool]] = None
    constraints: List[ParikhConstraint] = field(default_factory=list)
    structural_pattern: Optional[str] = None  # "balanced", "palindrome", "power", "copy", etc.

    def accepts(self, word: str) -> bool:
        if self.membership_fn:
            return self.membership_fn(word)
        if self.dfa:
            return self.dfa.accepts(word)
        raise ValueError("No membership test available")

    def has_membership_test(self) -> bool:
        return self.membership_fn is not None or self.dfa is not None
