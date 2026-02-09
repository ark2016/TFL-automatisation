"""LangGraph agent definition for regularity checking pipeline."""
from typing import Optional
from pumping_lemma.orchestrator.state import RegularityCheckState
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.models.results import HeuristicResult, RegularityVerdict
from pumping_lemma.parsing.nl_parser import NLParser
from pumping_lemma.parsing.regex_analyzer import RegexAnalyzer, HAS_REVERSE_MORFISM
from pumping_lemma.heuristics.parikh import ParikhHeuristic
from pumping_lemma.heuristics.length_density import LengthDensityHeuristic
from pumping_lemma.heuristics.myhill_nerode import MyhillNerodeHeuristic
from pumping_lemma.heuristics.closure import ClosureHeuristic
from pumping_lemma.heuristics.neural_symbolic import NeuralSymbolicHeuristic
from pumping_lemma.llm.client import LLMClient

try:
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False


class RegularityChecker:
    """Main orchestrator for regularity checking. Uses LangGraph if available, else sequential."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()
        self.parser = NLParser(self.llm)
        self.heuristics = [
            ParikhHeuristic(),
            LengthDensityHeuristic(),
            MyhillNerodeHeuristic(),
            ClosureHeuristic(self.llm),
            NeuralSymbolicHeuristic(self.llm),
        ]

    def check(self, description: str, mode: str = "full", pumping_constant: int = 20) -> RegularityVerdict:
        """Run regularity checking pipeline."""
        if HAS_LANGGRAPH:
            return self._check_langgraph(description, mode, pumping_constant)
        return self._check_sequential(description, mode, pumping_constant)

    def _check_sequential(self, description: str, mode: str, pumping_constant: int) -> RegularityVerdict:
        """Sequential pipeline without LangGraph."""
        # Step 1: Parse
        spec = self.parser.parse(description)

        # Step 2: Quick check — if regex, it's regular
        if spec.spec_type == 'regex' and spec.regex:
            return RegularityVerdict(
                verdict="regular",
                confidence=1.0,
                proof_trace=["Язык задан регулярным выражением → регулярный."],
                language_description=description
            )

        # Quick check — try DFA construction
        if spec.regex and HAS_REVERSE_MORFISM:
            try:
                analyzer = RegexAnalyzer()
                dfa = analyzer.regex_to_dfa(spec.regex)
                spec.dfa = dfa
                spec.membership_fn = dfa.accepts
                return RegularityVerdict(
                    verdict="regular",
                    confidence=1.0,
                    proof_trace=[f"Построен ДКА с {len(dfa.states)} состояниями → регулярный."],
                    language_description=description
                )
            except Exception:
                pass

        if not spec.has_membership_test():
            return RegularityVerdict(
                verdict="unknown",
                confidence=0.0,
                proof_trace=["Не удалось построить функцию проверки принадлежности."],
                language_description=description
            )

        # Step 3: Run heuristics
        results = []
        heuristics_to_run = self.heuristics if mode == "full" else self.heuristics[:3]

        for heuristic in heuristics_to_run:
            try:
                result = heuristic.analyze(spec, pumping_constant)
                results.append(result)

                if result.is_conclusive:
                    return RegularityVerdict(
                        verdict=result.verdict,
                        confidence=result.confidence,
                        proof_trace=result.proof_trace,
                        heuristic_results=results,
                        language_description=description
                    )
            except Exception as e:
                results.append(HeuristicResult(
                    heuristic_name=heuristic.name,
                    verdict="unknown",
                    confidence=0.0,
                    proof_trace=[f"Ошибка: {e}"]
                ))

        # Aggregate results
        return self._aggregate(results, description)

    def _aggregate(self, results, description):
        """Aggregate multiple heuristic results into final verdict."""
        non_regular_votes = sum(1 for r in results if r.verdict == "non_regular")
        regular_votes = sum(1 for r in results if r.verdict == "regular")
        total = len(results)

        if non_regular_votes > regular_votes and non_regular_votes > 0:
            best = max((r for r in results if r.verdict == "non_regular"), key=lambda r: r.confidence)
            return RegularityVerdict(
                verdict="non_regular",
                confidence=best.confidence * 0.8,
                proof_trace=best.proof_trace + [f"({non_regular_votes}/{total} эвристик голосуют за нерегулярность)"],
                heuristic_results=results,
                language_description=description
            )
        elif regular_votes > 0:
            best = max((r for r in results if r.verdict == "regular"), key=lambda r: r.confidence)
            return RegularityVerdict(
                verdict="regular",
                confidence=best.confidence * 0.8,
                proof_trace=best.proof_trace,
                heuristic_results=results,
                language_description=description
            )

        return RegularityVerdict(
            verdict="unknown",
            confidence=0.0,
            proof_trace=["Ни одна эвристика не дала убедительного результата."],
            heuristic_results=results,
            language_description=description
        )

    def _check_langgraph(self, description: str, mode: str, pumping_constant: int) -> RegularityVerdict:
        """LangGraph-based pipeline with conditional routing."""
        graph = StateGraph(RegularityCheckState)

        def parse_input(state):
            spec = self.parser.parse(state["input_description"])
            return {"language_spec": spec, "heuristic_results": [], "iteration": 0, "pumping_constant": pumping_constant}

        def quick_check(state):
            spec = state.get("language_spec")
            if spec and spec.spec_type == "regex" and spec.regex:
                return {"verdict": RegularityVerdict(
                    verdict="regular", confidence=1.0,
                    proof_trace=["Язык задан регулярным выражением → регулярный."],
                    language_description=state["input_description"]
                )}
            return state

        def should_continue(state):
            if state.get("verdict"):
                return "format_result"
            return "run_heuristics"

        def run_heuristics(state):
            spec = state.get("language_spec")
            if not spec or not spec.has_membership_test():
                return {"verdict": RegularityVerdict(
                    verdict="unknown", confidence=0.0,
                    proof_trace=["Нет функции проверки принадлежности."],
                    language_description=state["input_description"]
                )}

            p = state.get("pumping_constant", 20)
            results = state.get("heuristic_results", [])
            current_mode = state.get("mode", "full")
            heuristics_to_run = self.heuristics if current_mode == "full" else self.heuristics[:3]

            for h in heuristics_to_run:
                try:
                    r = h.analyze(spec, p)
                    results.append(r)
                    if r.is_conclusive:
                        return {
                            "heuristic_results": results,
                            "verdict": RegularityVerdict(
                                verdict=r.verdict, confidence=r.confidence,
                                proof_trace=r.proof_trace, heuristic_results=results,
                                language_description=state["input_description"]
                            )
                        }
                except Exception:
                    pass

            return {
                "heuristic_results": results,
                "verdict": self._aggregate(results, state["input_description"])
            }

        def format_result(state):
            return state

        graph.add_node("parse_input", parse_input)
        graph.add_node("quick_check", quick_check)
        graph.add_node("run_heuristics", run_heuristics)
        graph.add_node("format_result", format_result)

        graph.set_entry_point("parse_input")
        graph.add_edge("parse_input", "quick_check")
        graph.add_conditional_edges("quick_check", should_continue)
        graph.add_edge("run_heuristics", "format_result")
        graph.add_edge("format_result", END)

        app = graph.compile()
        result = app.invoke({
            "input_description": description,
            "mode": mode,
        })

        return result.get("verdict", RegularityVerdict(
            verdict="unknown", confidence=0.0,
            proof_trace=["Ошибка в конвейере LangGraph."],
            language_description=description
        ))
