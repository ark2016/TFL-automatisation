"""LangGraph state schema for regularity checking."""
from typing import List, Optional, TypedDict, Annotated
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.models.results import HeuristicResult, RegularityVerdict


class RegularityCheckState(TypedDict, total=False):
    """State for the LangGraph regularity checking pipeline."""
    # Input
    input_description: str
    mode: str  # "full", "quick", "specific_heuristic"

    # Parsed
    language_spec: Optional[LanguageSpec]

    # Processing
    heuristic_results: List[HeuristicResult]
    current_heuristic: str
    pumping_constant: int
    iteration: int

    # Output
    verdict: Optional[RegularityVerdict]
    error: Optional[str]
