"""Neural-symbolic heuristic: LLM-propose, symbolic-verify loop."""
from pumping_lemma.heuristics.base import AbstractHeuristic
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.models.results import HeuristicResult
from pumping_lemma.llm.client import LLMClient
from pumping_lemma.llm.prompts import SYSTEM_PROMPT, NEURAL_SYMBOLIC_STRATEGY_PROMPT
from pumping_lemma.utils.word_gen import generate_pumping_splits, pump_word
from typing import Optional
import re


class NeuralSymbolicHeuristic(AbstractHeuristic):
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client
        self.max_iterations = 5

    @property
    def name(self):
        return "neural_symbolic"

    def analyze(self, spec: LanguageSpec, pumping_constant: int = 20) -> HeuristicResult:
        if not self.llm or not self.llm.is_available():
            return self._unknown_result("LLM клиент недоступен")
        if not spec.has_membership_test():
            return self._unknown_result("Нет функции проверки принадлежности")

        attempts = []
        partial_results = []

        for iteration in range(self.max_iterations):
            # LLM proposes strategy
            strategy = self._propose_strategy(spec, attempts, partial_results)
            if not strategy:
                continue

            # Execute strategy
            result = self._execute_strategy(strategy, spec, pumping_constant)
            attempts.append({"strategy": strategy, "result": str(result)})

            if result and result.verdict == "non_regular":
                result.proof_trace.insert(0, f"Нейросимвольный цикл: решение на итерации {iteration + 1}")
                return result

            if result:
                partial_results.append(str(result))

        return self._unknown_result(f"Нейросимвольный цикл: {self.max_iterations} итераций без результата")

    def _propose_strategy(self, spec, attempts, partial_results):
        """Ask LLM to propose next strategy."""
        prev = "\n".join(
            f"  Попытка {i+1}: {a['strategy'].get('reasoning','')[:100]}... → {a['result'][:100]}"
            for i, a in enumerate(attempts[-3:])
        )
        partials = "\n".join(partial_results[-3:]) if partial_results else "Нет"

        prompt = NEURAL_SYMBOLIC_STRATEGY_PROMPT.format(
            description=spec.description,
            alphabet=', '.join(sorted(spec.alphabet)),
            previous_attempts=prev or "Нет предыдущих попыток",
            partial_results=partials
        )
        try:
            return self.llm.complete_json(prompt, system=SYSTEM_PROMPT)
        except Exception:
            return None

    def _execute_strategy(self, strategy, spec, p):
        """Execute proposed strategy symbolically."""
        stype = strategy.get('strategy_type', '')

        if stype == 'test_word':
            return self._execute_test_word(strategy, spec, p)
        elif stype == 'parikh_analysis':
            from pumping_lemma.heuristics.parikh import ParikhHeuristic
            return ParikhHeuristic().analyze(spec, p)
        elif stype == 'myhill_nerode':
            from pumping_lemma.heuristics.myhill_nerode import MyhillNerodeHeuristic
            return MyhillNerodeHeuristic().analyze(spec, p)
        elif stype == 'intersection':
            from pumping_lemma.heuristics.closure import ClosureHeuristic
            return ClosureHeuristic(self.llm).analyze(spec, p)
        elif stype == 'length_density':
            from pumping_lemma.heuristics.length_density import LengthDensityHeuristic
            return LengthDensityHeuristic().analyze(spec, p)

        return self._unknown_result(f"Неизвестный тип стратегии: {stype}")

    def _execute_test_word(self, strategy, spec, p):
        """Test a specific word proposed by LLM."""
        word_template = strategy.get('word', '')
        if not word_template:
            return self._unknown_result("LLM не предложил конкретное слово")

        # Resolve template: replace 'p' with actual pumping constant
        word = word_template
        word = re.sub(r'a\^p', 'a' * p, word)
        word = re.sub(r'b\^p', 'b' * p, word)
        word = re.sub(r'c\^p', 'c' * p, word)
        word = re.sub(r'a\^(\d+)', lambda m: 'a' * int(m.group(1)), word)
        word = re.sub(r'b\^(\d+)', lambda m: 'b' * int(m.group(1)), word)

        if not spec.accepts(word):
            return self._unknown_result(f"Предложенное слово '{word[:50]}' не принадлежит языку")

        # Check all splits
        all_splits_fail = True
        for x, y, z in generate_pumping_splits(word, p):
            valid_split = True
            for i in [0, 2, 3]:
                pumped = pump_word(x, y, z, i)
                if not spec.accepts(pumped):
                    valid_split = False
                    break
            if valid_split:
                all_splits_fail = False
                break

        if all_splits_fail:
            return HeuristicResult(
                heuristic_name=self.name,
                verdict="non_regular",
                confidence=0.9,
                proof_trace=[
                    f"LLM предложил слово: w = '{word[:50]}' (длина {len(word)})",
                    f"Для всех разбиений xyz (|xy|≤{p}, |y|>0):",
                    "  существует i, при котором xy^iz ∉ L.",
                    "По лемме о накачке → НЕРЕГУЛЯРНЫЙ."
                ],
                counterexample={"word": word, "pumping_constant": p}
            )

        return self._unknown_result(f"Слово '{word[:30]}...' допускает накачку")
