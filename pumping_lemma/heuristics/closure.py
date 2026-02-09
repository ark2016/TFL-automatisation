"""Closure property reduction heuristic."""
from pumping_lemma.heuristics.base import AbstractHeuristic
from pumping_lemma.models.language_spec import LanguageSpec, ParikhConstraint
from pumping_lemma.models.results import HeuristicResult
from pumping_lemma.llm.client import LLMClient
from pumping_lemma.llm.prompts import SYSTEM_PROMPT, SUGGEST_REGULAR_INTERSECTIONS_PROMPT
from typing import Optional, List
import re


class ClosureHeuristic(AbstractHeuristic):
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client

    @property
    def name(self):
        return "closure_properties"

    def analyze(self, spec: LanguageSpec, pumping_constant: int = 20) -> HeuristicResult:
        if not spec.has_membership_test():
            return self._unknown_result("Нет функции проверки принадлежности")

        # Get candidate regular languages for intersection
        candidates = self._get_intersection_candidates(spec)

        for regex, description in candidates:
            result = self._try_intersection(spec, regex, description, pumping_constant)
            if result and result.verdict == "non_regular":
                return result

        return self._unknown_result("Пересечение с регулярными языками не помогло")

    def _get_intersection_candidates(self, spec) -> List[tuple]:
        """Get candidate regular languages. Use LLM if available, else heuristic."""
        candidates = []
        alpha = sorted(spec.alphabet)

        # Standard candidates based on alphabet
        if len(alpha) >= 2:
            a, b = alpha[0], alpha[1]
            candidates.append((f"{a}*{b}*", f"блоки {a} затем {b}"))
            candidates.append((f"({a}{b})*", f"чередование {a}{b}"))
            if len(alpha) >= 3:
                c = alpha[2]
                candidates.append((f"{a}*{b}*{c}*", f"блоки {a},{b},{c}"))

        # Try LLM suggestions
        if self.llm and self.llm.is_available():
            try:
                prompt = SUGGEST_REGULAR_INTERSECTIONS_PROMPT.format(description=spec.description)
                data = self.llm.complete_json(prompt, system=SYSTEM_PROMPT)
                for s in data.get('suggestions', []):
                    candidates.append((s['regex'], s.get('description', '')))
            except Exception:
                pass

        return candidates

    def _try_intersection(self, spec, regex, description, pumping_constant):
        """Try intersecting spec with given regex and analyze result."""
        try:
            from pumping_lemma.parsing.regex_analyzer import RegexAnalyzer, HAS_REVERSE_MORFISM
            if not HAS_REVERSE_MORFISM:
                return None
            analyzer = RegexAnalyzer()
            if not analyzer.is_regex_valid(regex):
                return None
            dfa = analyzer.regex_to_dfa(regex)
        except Exception:
            return None

        # Build intersection membership
        def intersection_test(w):
            return spec.accepts(w) and dfa.accepts(w)

        intersection_spec = LanguageSpec(
            description=f"({spec.description}) ∩ L({regex})",
            alphabet=spec.alphabet,
            spec_type='oracle',
            membership_fn=intersection_test,
            constraints=spec.constraints,
            structural_pattern=spec.structural_pattern,
        )

        # Run simpler heuristics on intersection
        from pumping_lemma.heuristics.parikh import ParikhHeuristic
        from pumping_lemma.heuristics.myhill_nerode import MyhillNerodeHeuristic

        for heuristic_cls in [ParikhHeuristic, MyhillNerodeHeuristic]:
            h = heuristic_cls()
            result = h.analyze(intersection_spec, pumping_constant)
            if result.verdict == "non_regular":
                return HeuristicResult(
                    heuristic_name=self.name,
                    verdict="non_regular",
                    confidence=result.confidence * 0.95,
                    proof_trace=[
                        f"Пересечение с регулярным языком R = L({regex}): {description}",
                        f"L ∩ R нерегулярен (доказано через {result.heuristic_name}):",
                    ] + [f"  {t}" for t in result.proof_trace] + [
                        "Т.к. регулярные языки замкнуты относительно пересечения,",
                        "и L ∩ R нерегулярен, то L НЕРЕГУЛЯРЕН."
                    ],
                    counterexample=result.counterexample
                )

        return None
