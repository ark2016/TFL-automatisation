from graphviz import Digraph
from pyformlang.finite_automaton import DeterministicFiniteAutomaton, NondeterministicFiniteAutomaton, EpsilonNFA

def visualize_dfa(dfa: DeterministicFiniteAutomaton, name: str):
    dot = Digraph(comment='DFA')

    # Добавляем состояния
    for state in dfa.states:
        if state in dfa.final_states:
            dot.node(str(state), str(state), shape='doublecircle')  # Конечное состояние
        else:
            dot.node(str(state), str(state), shape='circle')  # Обычное состояние

    # Добавляем начальные состояния
    for start_state in dfa.start_states:
        dot.node(str(start_state) + "_start", shape="point")
        dot.edge(str(start_state) + "_start", str(start_state))

    # Добавляем переходы
    for from_state, to_dict in dfa.to_dict().items():
        for symbol, to_state in to_dict.items():
            if isinstance(to_state, set):
                for next_state in to_state:
                    dot.edge(str(from_state), str(next_state), label=str(symbol))
            else:
                dot.edge(str(from_state), str(to_state), label=str(symbol))

    # Рендерим и сохраняем в файл
    dot.render(f'{name}', format='png', view=True)

def visualize_nfa(nfa: NondeterministicFiniteAutomaton, name: str):
    dot = Digraph(comment='NFA')

    # Добавляем состояния
    for state in nfa.states:
        if state in nfa.final_states:
            dot.node(str(state), str(state), shape='doublecircle')  # Конечное состояние
        else:
            dot.node(str(state), str(state), shape='circle')  # Обычное состояние

    # Добавляем начальные состояния
    for start_state in nfa.start_states:
        dot.node(str(start_state) + "_start", shape="point")
        dot.edge(str(start_state) + "_start", str(start_state))

    # Добавляем переходы
    for from_state, to_dict in nfa.to_dict().items():
        for symbol, to_state in to_dict.items():
            if isinstance(to_state, set):
                for next_state in to_state:
                    dot.edge(str(from_state), str(next_state), label=str(symbol))
            else:
                dot.edge(str(from_state), str(to_state), label=str(symbol))

    # Рендерим и сохраняем в файл
    dot.render(f'{name}', format='png', view=True)

def visualize_epsilon_nfa(enfa: EpsilonNFA, name: str):
    dot = Digraph(comment='EpsilonNFA')

    # Добавляем состояния
    for state in enfa.states:
        if state in enfa.final_states:
            dot.node(str(state), str(state), shape='doublecircle')  # Конечное состояние
        else:
            dot.node(str(state), str(state), shape='circle')  # Обычное состояние

    # Добавляем начальные состояния
    for start_state in enfa.start_states:
        dot.node(str(start_state) + "_start", shape="point")
        dot.edge(str(start_state) + "_start", str(start_state))

    # Добавляем переходы
    for from_state, to_dict in enfa.to_dict().items():
        for symbol, to_state in to_dict.items():
            if isinstance(to_state, set):
                for next_state in to_state:
                    dot.edge(str(from_state), str(next_state), label=str(symbol))
            else:
                dot.edge(str(from_state), str(to_state), label=str(symbol))

    # Рендерим и сохраняем в файл
    dot.render(f'{name}', format='png', view=True)
