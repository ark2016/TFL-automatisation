"""CLI entry point for regularity checking."""
import sys
import os
import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from pumping_lemma.orchestrator.graph import RegularityChecker
from pumping_lemma.llm.client import LLMClient


def main():
    parser = argparse.ArgumentParser(
        description="Проверка регулярности формального языка",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python -m pumping_lemma.cli "Является ли регулярным язык {a^n b^n | n >= 0}?"
  python -m pumping_lemma.cli "{ww | w ∈ {a,b}*}" --mode full
  python -m pumping_lemma.cli "a*b*" --mode quick
        """
    )
    parser.add_argument("description", help="Описание языка")
    parser.add_argument("--mode", choices=["full", "quick"], default="full", help="Режим проверки")
    parser.add_argument("--pumping-constant", type=int, default=20, help="Константа накачки (по умолчанию: 20)")
    parser.add_argument("--model", default="claude-sonnet-4-5", help="Модель Claude")
    parser.add_argument("--no-llm", action="store_true", help="Без LLM (только символьные методы)")

    args = parser.parse_args()

    llm = None if args.no_llm else LLMClient(model=args.model)
    checker = RegularityChecker(llm_client=llm)

    print(f"Анализ языка: {args.description}")
    print(f"Режим: {args.mode}, константа накачки: {args.pumping_constant}")
    print("=" * 60)

    verdict = checker.check(args.description, mode=args.mode, pumping_constant=args.pumping_constant)

    print(verdict.summary())
    print("=" * 60)

    if verdict.heuristic_results:
        print("\nРезультаты эвристик:")
        for r in verdict.heuristic_results:
            status_icon = {"regular": "✓", "non_regular": "✗", "unknown": "?"}
            print(f"  {status_icon.get(r.verdict, '?')} {r.heuristic_name}: {r.verdict} ({r.confidence:.0%})")

    return 0 if verdict.verdict != "unknown" else 1


if __name__ == "__main__":
    sys.exit(main())
