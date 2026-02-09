"""Prompt templates for LLM interactions (Russian language)."""

SYSTEM_PROMPT = """Ты — эксперт по теории формальных языков и автоматов.
Ты помогаешь анализировать языки на регулярность, используя лемму о накачке,
теорему Майхилла-Нероуда, свойства замыкания и другие методы.
Отвечай точно, формально и кратко. Используй математическую нотацию."""

PARSE_LANGUAGE_PROMPT = """Проанализируй описание формального языка и извлеки структурированную информацию.

Описание языка: {description}

Верни JSON со следующими полями:
{{
    "alphabet": ["a", "b"],
    "spec_type": "set_builder" | "regex" | "grammar" | "oracle",
    "regex": "регулярное выражение если применимо, иначе null",
    "structural_pattern": "balanced" | "palindrome" | "power" | "copy" | "counting" | "mixed" | null,
    "constraints": [
        {{
            "constraint_type": "equal" | "less" | "greater" | "linear" | "modular",
            "symbols": ["a", "b"],
            "coefficients": [1, -1],
            "constant": 0,
            "description": "описание ограничения"
        }}
    ],
    "membership_rule": "правило проверки принадлежности слова языку на Python (lambda w: ...)",
    "parametric_form": "параметрическая форма слов, например a^n b^n",
    "known_regular": true | false | null,
    "reasoning": "краткое объяснение анализа"
}}"""

SUGGEST_PROOF_STRATEGY_PROMPT = """Предложи стратегию доказательства {goal} языка.

Язык: {description}

{previous_attempts}

Предложи конкретную стратегию. Верни JSON:
{{
    "strategy_type": "pumping_word" | "parikh" | "myhill_nerode" | "closure" | "direct",
    "details": {{
        "word": "конкретное слово для накачки (если pumping_word)",
        "split_hint": "подсказка по разбиению",
        "regular_language": "регулярный язык для пересечения (если closure)",
        "prefix_family": "семейство префиксов (если myhill_nerode)",
        "explanation": "объяснение стратегии"
    }},
    "confidence": 0.8,
    "reasoning": "почему эта стратегия должна сработать"
}}"""

SUGGEST_REGULAR_INTERSECTIONS_PROMPT = """Для языка L = {description}, предложи простые регулярные языки R,
пересечение L ∩ R с которыми может быть проще анализировать.

Цель: упростить язык, сохранив его нерегулярность (если он нерегулярный).

Верни JSON:
{{
    "suggestions": [
        {{
            "regex": "a*b*",
            "description": "слова из блока a, затем блока b",
            "expected_intersection": "описание L ∩ R",
            "reasoning": "почему это упрощает анализ"
        }}
    ]
}}"""

NEURAL_SYMBOLIC_STRATEGY_PROMPT = """Задача: Доказать, что язык L нерегулярен используя лемму о накачке.

Язык: {description}
Алфавит: {alphabet}

Предыдущие попытки:
{previous_attempts}

Частичные результаты:
{partial_results}

На основе анализа предложи НОВУЮ стратегию. Верни JSON:
{{
    "strategy_type": "test_word" | "parikh_analysis" | "intersection" | "length_density" | "myhill_nerode",
    "word": "конкретное слово если test_word (используй p для константы накачки)",
    "split_preference": "pump_first_symbol" | "pump_boundary" | "pump_middle" | null,
    "regular_language": "регулярный язык если intersection",
    "prefix_pattern": "паттерн префиксов если myhill_nerode",
    "symbols_to_analyze": ["a", "b"],
    "reasoning": "подробное объяснение выбора стратегии"
}}"""

EXPLAIN_RESULT_PROMPT = """Объясни результат проверки регулярности языка на русском языке.

Язык: {description}
Вердикт: {verdict}
Уверенность: {confidence}
Результаты эвристик:
{heuristic_results}

Напиши понятное объяснение с математическими деталями доказательства.
Если язык нерегулярный, приведи полное доказательство через лемму о накачке.
Если регулярный, объясни почему."""
