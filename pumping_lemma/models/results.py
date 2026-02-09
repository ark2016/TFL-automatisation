from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class HeuristicResult:
    heuristic_name: str
    verdict: str  # "regular", "non_regular", "unknown"
    confidence: float  # 0.0 to 1.0
    proof_trace: List[str] = field(default_factory=list)
    counterexample: Optional[Dict] = None  # word, split, pumped result details

    @property
    def is_conclusive(self) -> bool:
        return self.verdict != "unknown" and self.confidence >= 0.7


@dataclass
class RegularityVerdict:
    verdict: str  # "regular", "non_regular", "unknown"
    confidence: float
    proof_trace: List[str] = field(default_factory=list)
    heuristic_results: List[HeuristicResult] = field(default_factory=list)
    language_description: str = ""

    def summary(self) -> str:
        status = {"regular": "РЕГУЛЯРНЫЙ", "non_regular": "НЕРЕГУЛЯРНЫЙ", "unknown": "НЕ ОПРЕДЕЛЕНО"}
        lines = [f"Вердикт: {status.get(self.verdict, self.verdict)} (уверенность: {self.confidence:.0%})"]
        for step in self.proof_trace:
            lines.append(f"  • {step}")
        return "\n".join(lines)
