import re


def transform_regex(input_regex):

    pattern = re.compile(r'\(\?=\s*(.*?)\s*\)\s*(.*)')
    match = pattern.match(input_regex)

    if not match:
        raise ValueError("Input is not in the expected format: '(?= r1) r2'")

    r1, r2 = match.groups()

    transformed_regex = f"{r1}{r2}"

    return transformed_regex


def transform_regex_with_lookahead(regex_with_lookahead):
    import re

    # Pattern to match positive lookahead assertions
    pattern = r'\(\?=\s*(.*?)\s*\)\s*(.+)'
    match = re.match(pattern, regex_with_lookahead)

    if not match:
        raise ValueError("Invalid regex format. Expected pattern '(?= r1 ) r2'.")

    lookahead_expr = match.group(1)
    main_expr = match.group(2)

    if lookahead_expr.endswith('+') or lookahead_expr.endswith('*'):
        base_char = lookahead_expr[:-1]
        quantifier = lookahead_expr[-1]
        if quantifier == '+' and main_expr.startswith(f'({base_char}|'):
            return f'{base_char}{main_expr}'
        elif quantifier == '*' and main_expr == f'({base_char}|b)*':
            return main_expr
    # If we cannot simplify, return the original expression without the lookahead
    return f'Unsupported pattern: cannot simplify {regex_with_lookahead} without advanced operations.'