"""Abstract base class for heuristics."""
from abc import ABC, abstractmethod
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.models.results import HeuristicResult


class AbstractHeuristic(ABC):
    """Base class for regularity heuristics."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def analyze(self, spec: LanguageSpec, pumping_constant: int = 20) -> HeuristicResult:
        pass

    def _unknown_result(self, reason: str = "") -> HeuristicResult:
        return HeuristicResult(
            heuristic_name=self.name,
            verdict="unknown",
            confidence=0.0,
            proof_trace=[reason] if reason else []
        )
