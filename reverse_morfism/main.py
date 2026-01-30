"""
Konsol'nyj interfejs dlja reshenija zadach na obratnyj gomomorfizm.

Zapusk: python main.py
"""

import sys
from inverse_homomorphism import (
    InverseHomomorphismSolver,
    Homomorphism,
    dfa_to_table,
    dfa_to_dot,
    dfa_to_regex
)


def parse_homomorphism(line: str) -> dict:
    """
    Parsing stroki vida: a=baa b=aaa c=a d=ab
    ili: a->baa, b->aaa, c->a, d->ab
    """
    mapping = {}

    line = line.replace(',', ' ')
    pairs = line.split()

    for pair in pairs:
        if '->' in pair:
            symbol, image = pair.split('->', 1)
        elif '=' in pair:
            symbol, image = pair.split('=', 1)
        else:
            raise ValueError(f"Wrong format: '{pair}'. Use a=baa or a->baa")

        symbol = symbol.strip()
        image = image.strip()

        if len(symbol) != 1:
            raise ValueError(f"Symbol must be one letter: '{symbol}'")

        if image.lower() in ('eps', 'epsilon', ''):
            image = ''

        mapping[symbol] = image

    return mapping


def print_homomorphism(h: dict) -> str:
    """Format homomorphism for output."""
    parts = []
    for sym in sorted(h.keys()):
        img = h[sym] if h[sym] else 'eps'
        parts.append(f"h({sym})={img}")
    return ', '.join(parts)


def main():
    print("=" * 60)
    print("INVERSE HOMOMORPHISM FOR REGULAR LANGUAGES")
    print("=" * 60)
    print()

    print("Enter regular expression for language L:")
    print("  Syntax: a, b, ... (symbols), | (or), * (star),")
    print("          + (plus), ? (optional), () (grouping)")
    print("  Examples: (ab)*, a*b*, ((aba)*bb|aa)*")
    print()

    regex = input("L = ").strip()
    if not regex:
        print("Error: empty expression")
        return 1

    # Support finite language notation: {w1,w2,...} -> (w1|w2|...)
    if regex.startswith('{') and regex.endswith('}'):
        words = [w.strip() for w in regex[1:-1].split(',')]
        # Replace empty word notation
        words = ['eps' if w in ('', 'eps', 'epsilon') else w for w in words]
        regex = '|'.join(words)
        if len(words) > 1:
            regex = f"({regex})"
        print(f"  -> converted to regex: {regex}")

    print()

    print("Enter homomorphism:")
    print("  Format: a=image b=image c=image ... (ALL symbols on ONE line)")
    print("  Example: a=baa b=aaa c=a d=ab")
    print("  For epsilon use: a=eps or a=")
    print()
    print("  For COMPOSITION of multiple homomorphisms:")
    print("  Enter each homomorphism on a separate line, empty line to finish")
    print()

    homomorphisms = []
    h_num = 1

    while True:
        prompt = f"h{h_num}: " if len(homomorphisms) > 0 else "h: "
        try:
            line = input(prompt).strip()
        except EOFError:
            break

        if not line:
            if not homomorphisms:
                print("Error: need at least one homomorphism")
                continue
            break

        try:
            h_mapping = parse_homomorphism(line)
            if h_mapping:
                homomorphisms.append(h_mapping)
                print(f"  Added: {print_homomorphism(h_mapping)}")
                h_num += 1
        except ValueError as e:
            print(f"  Error: {e}")
            continue

    print()
    print("-" * 60)
    print("SOLUTION")
    print("-" * 60)
    print()

    try:
        solver = InverseHomomorphismSolver()
        solver.set_language(regex)

        for h_mapping in homomorphisms:
            solver.add_homomorphism(h_mapping)

        dfa = solver.solve(minimize=True, verbose=False)

        print(f"Language L: {regex}")
        for i, h in enumerate(homomorphisms, 1):
            prefix = f"h{i}" if len(homomorphisms) > 1 else "h"
            print(f"Homomorphism {prefix}: {print_homomorphism(h)}")
        print()

        print(f"Result: minimal DFA with {len(dfa.states)} states")
        print(f"Alphabet: {{{', '.join(sorted(dfa.alphabet))}}}")
        print(f"Start state: q{dfa.start_state}")
        accept_str = ', '.join(f'q{s}' for s in sorted(dfa.accept_states))
        print(f"Accept states: {{{accept_str}}}")
        print()

        # Build regex with timeout (may fail for complex automata)
        import threading
        regex_result = [None]
        regex_error = [None]

        def compute_regex():
            try:
                regex_result[0] = dfa_to_regex(dfa)
            except Exception as e:
                regex_error[0] = str(e)

        thread = threading.Thread(target=compute_regex)
        thread.daemon = True
        thread.start()
        thread.join(timeout=3.0)  # 3 second timeout

        if thread.is_alive():
            print("Regex: (computation timeout, use transition table)")
        elif regex_error[0]:
            print(f"Regex: (too complex to display)")
        elif regex_result[0]:
            print(f"Regular expression for h^(-1)(L): {regex_result[0]}")
        print()

        print("Transition table:")
        print(dfa_to_table(dfa))

        print()
        print("-" * 60)
        print("TEST WORDS")
        print("-" * 60)
        print("Enter words to test (space separated), or 'exit':")
        print("  For epsilon use: eps")
        print()

        def apply_all(word: str) -> str:
            result = word
            for h_mapping in homomorphisms:
                h = Homomorphism(h_mapping)
                result = h.apply(result)
            return result

        while True:
            try:
                line = input("Words: ").strip()
            except EOFError:
                break

            if line.lower() == 'exit':
                break

            if not line:
                continue

            words = line.split()
            print()

            for word in words:
                if word.lower() == 'eps':
                    word = ''

                try:
                    h_word = apply_all(word)
                    accepts = dfa.accepts(word)

                    w_display = word if word else 'eps'
                    h_display = h_word if h_word else 'eps'
                    status = "[+] ACCEPTED" if accepts else "[-] REJECTED"

                    print(f"  w = '{w_display}'")
                    print(f"  h(w) = '{h_display}'")
                    print(f"  w in h^(-1)(L): {status}")
                    print()

                except (ValueError, KeyError) as e:
                    print(f"  w = '{word}': error - {e}")
                    print()

        print("-" * 60)
        print("Save automaton? (filename without extension, or 'n'):")
        try:
            filename = input("> ").strip()
            if filename and filename.lower() != 'n':
                if filename.endswith('.dot') or filename.endswith('.png'):
                    filename = filename.rsplit('.', 1)[0]

                dot_file = f"{filename}.dot"
                png_file = f"{filename}.png"

                with open(dot_file, 'w', encoding='utf-8') as f:
                    f.write(dfa_to_dot(dfa, "InverseHomomorphism"))
                print(f"DOT saved: {dot_file}")

                try:
                    import graphviz
                    source = dfa_to_dot(dfa, "InverseHomomorphism")
                    graph = graphviz.Source(source)
                    graph.render(filename, format='png', cleanup=True)
                    print(f"PNG saved: {png_file}")
                except ImportError:
                    print(f"graphviz not installed. For PNG run:")
                    print(f"  pip install graphviz")
                    print(f"  dot -Tpng {dot_file} -o {png_file}")
                except Exception as e:
                    print(f"graphviz error: {e}")
                    print(f"Try manually: dot -Tpng {dot_file} -o {png_file}")

        except EOFError:
            pass

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print()
    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
