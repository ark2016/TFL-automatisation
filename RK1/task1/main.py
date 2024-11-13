from regex_to_automaton import regex_to_dfa
from visualize_automaton import visualize_dfa

if __name__ == "__main__":
    # Пример регулярного выражения
    regex_str = "a(b|c)*d"

    # Преобразование регулярного выражения в DFA
    dfa = regex_to_dfa(regex_str)

    # Визуализация DFA
    visualize_dfa(dfa, "dfa_visualization")
