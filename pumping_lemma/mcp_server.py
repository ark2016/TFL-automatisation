"""MCP server exposing regularity checking tools."""
import json
from typing import Optional

try:
    from mcp.server.fastmcp import FastMCP
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from pumping_lemma.orchestrator.graph import RegularityChecker
from pumping_lemma.parsing.nl_parser import NLParser
from pumping_lemma.llm.client import LLMClient
from pumping_lemma.llm.prompts import SYSTEM_PROMPT, SUGGEST_PROOF_STRATEGY_PROMPT


def create_server():
    if not HAS_MCP:
        raise ImportError("mcp package required: pip install mcp")

    mcp = FastMCP("pumping-lemma-checker", description="Проверка регулярности формальных языков")
    llm = LLMClient()
    checker = RegularityChecker(llm_client=llm)
    parser = NLParser(llm_client=llm)

    @mcp.tool()
    def check_regularity(description: str, mode: str = "full", pumping_constant: int = 20) -> str:
        """Проверить, является ли язык регулярным.

        Args:
            description: Описание языка (NL, regex, set-builder notation)
            mode: Режим проверки ('full' или 'quick')
            pumping_constant: Константа накачки
        """
        verdict = checker.check(description, mode=mode, pumping_constant=pumping_constant)
        return verdict.summary()

    @mcp.tool()
    def parse_language(description: str) -> str:
        """Разобрать описание языка в структурированную спецификацию.

        Args:
            description: Описание языка на естественном языке
        """
        spec = parser.parse(description)
        return json.dumps({
            "description": spec.description,
            "alphabet": sorted(spec.alphabet),
            "spec_type": spec.spec_type,
            "regex": spec.regex,
            "structural_pattern": spec.structural_pattern,
            "has_membership_test": spec.has_membership_test(),
            "constraints": [
                {"type": c.constraint_type, "symbols": c.symbols, "description": c.description}
                for c in spec.constraints
            ]
        }, ensure_ascii=False, indent=2)

    @mcp.tool()
    def test_word_membership(description: str, word: str) -> str:
        """Проверить принадлежность слова языку.

        Args:
            description: Описание языка
            word: Слово для проверки
        """
        spec = parser.parse(description)
        if not spec.has_membership_test():
            return json.dumps({"error": "Не удалось построить функцию проверки"})
        result = spec.accepts(word)
        return json.dumps({"word": word, "in_language": result}, ensure_ascii=False)

    @mcp.tool()
    def suggest_proof_strategy(description: str, user_idea: str = "") -> str:
        """Предложить стратегию доказательства (не)регулярности.

        Args:
            description: Описание языка
            user_idea: Идея пользователя (опционально)
        """
        if not llm.is_available():
            return json.dumps({"error": "LLM недоступен"})

        goal = "нерегулярности"
        previous = f"\nИдея пользователя: {user_idea}" if user_idea else ""
        prompt = SUGGEST_PROOF_STRATEGY_PROMPT.format(
            goal=goal, description=description, previous_attempts=previous
        )
        try:
            result = llm.complete_json(prompt, system=SYSTEM_PROMPT)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def run_heuristic(description: str, heuristic: str, pumping_constant: int = 20) -> str:
        """Запустить конкретную эвристику проверки.

        Args:
            description: Описание языка
            heuristic: Имя эвристики (parikh, density, myhill_nerode, closure, neural_symbolic)
            pumping_constant: Константа накачки
        """
        from pumping_lemma.heuristics.parikh import ParikhHeuristic
        from pumping_lemma.heuristics.length_density import LengthDensityHeuristic
        from pumping_lemma.heuristics.myhill_nerode import MyhillNerodeHeuristic
        from pumping_lemma.heuristics.closure import ClosureHeuristic
        from pumping_lemma.heuristics.neural_symbolic import NeuralSymbolicHeuristic

        heuristic_map = {
            "parikh": ParikhHeuristic,
            "density": LengthDensityHeuristic,
            "myhill_nerode": MyhillNerodeHeuristic,
            "closure": lambda: ClosureHeuristic(llm),
            "neural_symbolic": lambda: NeuralSymbolicHeuristic(llm),
        }

        cls = heuristic_map.get(heuristic)
        if not cls:
            return json.dumps({"error": f"Неизвестная эвристика: {heuristic}"})

        h = cls() if isinstance(cls, type) else cls()
        spec = parser.parse(description)
        result = h.analyze(spec, pumping_constant)

        return json.dumps({
            "heuristic": result.heuristic_name,
            "verdict": result.verdict,
            "confidence": result.confidence,
            "proof_trace": result.proof_trace,
            "counterexample": result.counterexample,
        }, ensure_ascii=False, indent=2)

    return mcp


def main():
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
