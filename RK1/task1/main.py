from regex_to_automaton import regex_to_nfa, nfa_to_dfa, canonical_renumbering
from visualize_automaton import visualize_dfa, visualize_epsilon_nfa
from regex_to_academic import transform_regex


if __name__ == "__main__":
    # Пример регулярного выражения
    # regex_str = "a(b|c)*d"
    # regex_str = "(?=a+)(a|b)*"
    r1 = "((aa|b)*b)"
    print(f"r1 = {r1}")
    r2 = "(bb|a)+a)"
    print(f"r2 = {r2}")
    r3 = f"(?={r1}){r2}"
    print(f"r3 = {r3}")
    r3 = transform_regex(r3)
    print(f"r3 = {r3}")
    r4 = "(a(a|b)*a)"
    print(f"r4 = {r4}")
    regex_str = f"(?={r3}){r4}"
    print(f"regex_str = {regex_str}")
    print(f"transform_regex({regex_str}) = {transform_regex(regex_str)}")
    regex_str = "(aa|b)*b(bb|a)+aa(a|b)*a"
    print(f"(?=(?={r1}){r2}){r4}")

    # print(transform_regex(regex_str))
    # regex_str = transform_regex(regex_str)
    # # Преобразование регулярного выражения в NFA
    nfa = regex_to_nfa(regex_str)

    # Визуализация NFA
    visualize_epsilon_nfa(nfa, "nfa_visualization")

    # Преобразование NFA в DFA
    dfa = nfa_to_dfa(nfa)

    # Каноническая перенумерация состояний
    canonical_dfa = canonical_renumbering(dfa)
    print(canonical_dfa.to_regex())
    # Визуализация DFA
    visualize_dfa(canonical_dfa, "dfa_visualization")


