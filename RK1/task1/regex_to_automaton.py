from pyformlang.regular_expression import Regex
from pyformlang.finite_automaton import DeterministicFiniteAutomaton, State, Symbol, EpsilonNFA

def regex_to_nfa(regex_str: str) -> EpsilonNFA:
    # Создание объекта Regex
    regex = Regex(regex_str)

    # Преобразование регулярного выражения в конечный автомат
    nfa = regex.to_epsilon_nfa()

    return nfa

def nfa_to_dfa(nfa: EpsilonNFA) -> DeterministicFiniteAutomaton:
    # Преобразование NFA в DFA
    dfa = nfa.minimize()

    return dfa

def canonical_renumbering(dfa: DeterministicFiniteAutomaton) -> DeterministicFiniteAutomaton:
    # Создание нового DFA с канонической перенумерацией состояний
    new_states = {state: State(i) for i, state in enumerate(dfa.states)}
    new_start_state = new_states[dfa.start_state]
    new_final_states = {new_states[state] for state in dfa.final_states}

    new_dfa = DeterministicFiniteAutomaton(
        states=set(new_states.values()),
        input_symbols=dfa.symbols,
        start_state=new_start_state,
        final_states=new_final_states
    )

    # Добавление переходов
    for state, transitions in dfa.to_dict().items():
        new_state = new_states[state]
        for symbol, next_states in transitions.items():
            if isinstance(next_states, set):
                for next_state in next_states:
                    new_dfa.add_transition(new_state, Symbol(symbol), new_states[next_state])
            else:
                new_dfa.add_transition(new_state, Symbol(symbol), new_states[next_states])

    return new_dfa
