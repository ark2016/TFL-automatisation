from pyformlang.regular_expression import Regex
from pyformlang.finite_automaton import DeterministicFiniteAutomaton

def regex_to_dfa(regex_str: str) -> DeterministicFiniteAutomaton:
    # Создание объекта Regex
    regex = Regex(regex_str)

    # Преобразование регулярного выражения в конечный автомат
    nfa = regex.to_epsilon_nfa()

    # Преобразование NFA в DFA
    dfa = nfa.minimize()

    return dfa
