from regex_to_automaton import regex_to_dfa, canonical_renumbering
from visualize_automaton import visualize_dfa

if __name__ == "__main__":
    # Пример регулярного выражения
    regex_str = "a(b|c)*d"

    # Преобразование регулярного выражения в DFA
    dfa = regex_to_dfa(regex_str)

    # Каноническая перенумерация состояний
    canonical_dfa = canonical_renumbering(dfa)

    # Визуализация DFA
    visualize_dfa(canonical_dfa, "dfa_visualization")
