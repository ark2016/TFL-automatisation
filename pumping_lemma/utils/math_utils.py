"""Math utilities: Parikh vectors, primality, linear algebra."""
from typing import Dict, List, Set, Tuple, Optional
from math import gcd
from functools import reduce


def compute_parikh_vector(word: str, alphabet: Set[str]) -> Dict[str, int]:
    return {s: word.count(s) for s in alphabet}


def parikh_vector_add(v1: Dict[str, int], v2: Dict[str, int]) -> Dict[str, int]:
    keys = set(v1) | set(v2)
    return {k: v1.get(k, 0) + v2.get(k, 0) for k in keys}


def parikh_vector_scale(v: Dict[str, int], scalar: int) -> Dict[str, int]:
    return {k: val * scalar for k, val in v.items()}


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


def is_perfect_square(n: int) -> bool:
    if n < 0:
        return False
    r = int(n ** 0.5)
    return r * r == n


def is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def solve_linear_diophantine(a: int, b: int, c: int) -> Optional[Tuple[int, int]]:
    """Solve ax + by = c for integers. Returns (x0, y0) or None."""
    if a == 0 and b == 0:
        return (0, 0) if c == 0 else None
    if a == 0:
        return (0, c // b) if c % b == 0 else None
    if b == 0:
        return (c // a, 0) if c % a == 0 else None
    g = gcd(abs(a), abs(b))
    if c % g != 0:
        return None

    # Extended Euclidean
    def ext_gcd(a, b):
        if b == 0:
            return a, 1, 0
        g, x1, y1 = ext_gcd(b, a % b)
        return g, y1, x1 - (a // b) * y1

    g, x0, y0 = ext_gcd(abs(a), abs(b))
    scale = c // g
    x0 *= scale * (1 if a > 0 else -1)
    y0 *= scale * (1 if b > 0 else -1)
    return (x0, y0)


def check_linear_constraint_satisfiable(base: Dict[str, int], increment: Dict[str, int],
                                         constraint_type: str, symbols: List[str],
                                         coefficients: List[int], constant: int) -> Tuple[bool, Optional[int]]:
    """Check if base + i * increment satisfies constraint for some non-negative i.
    Returns (satisfiable, i_value)."""
    if constraint_type == "equal":
        # symbols[0] count == symbols[1] count
        s0, s1 = symbols[0], symbols[1]
        diff_base = base.get(s0, 0) - base.get(s1, 0)
        diff_inc = increment.get(s1, 0) - increment.get(s0, 0)
        if diff_inc == 0:
            return (diff_base == 0, 0 if diff_base == 0 else None)
        if diff_base % diff_inc != 0:
            return (False, None)
        i_val = diff_base // diff_inc
        return (i_val >= 0, i_val if i_val >= 0 else None)
    elif constraint_type == "linear":
        # sum(coeff_j * count(symbol_j)) = constant
        lhs_base = sum(c * base.get(s, 0) for c, s in zip(coefficients, symbols))
        lhs_inc = sum(c * increment.get(s, 0) for c, s in zip(coefficients, symbols))
        # Need lhs_base + i * lhs_inc == constant
        diff = constant - lhs_base
        if lhs_inc == 0:
            return (diff == 0, 0 if diff == 0 else None)
        if diff % lhs_inc != 0:
            return (False, None)
        i_val = diff // lhs_inc
        return (i_val >= 0, i_val if i_val >= 0 else None)
    return (True, 0)  # unknown constraint type -> assume satisfiable


def analyze_growth_pattern(lengths: List[int]) -> str:
    """Analyze growth pattern of a sorted list of lengths."""
    if len(lengths) < 5:
        return "unknown"
    gaps = [lengths[i + 1] - lengths[i] for i in range(len(lengths) - 1)]
    # Check constant gaps (linear/arithmetic)
    if len(gaps) >= 3 and all(abs(g - gaps[0]) <= 1 for g in gaps):
        return "linear"
    # Check constant second differences (quadratic)
    if len(gaps) >= 4:
        second_diffs = [gaps[i + 1] - gaps[i] for i in range(len(gaps) - 1)]
        if all(abs(d - second_diffs[0]) <= 1 for d in second_diffs):
            return "quadratic"
    # Check exponential (ratio > 1.5 consistently)
    if all(lengths[i] > 0 for i in range(len(lengths))):
        ratios = [lengths[i + 1] / lengths[i] for i in range(len(lengths) - 1) if lengths[i] > 0]
        if ratios and all(r > 1.4 for r in ratios[-5:]):
            return "exponential"
    # Check if all are prime
    if all(is_prime(l) for l in lengths[:10] if l > 1):
        return "prime"
    # Check if all are perfect squares
    if all(is_perfect_square(l) for l in lengths[:10]):
        return "quadratic_values"
    return "unknown"


def find_gaps(lengths: List[int], min_gap: int = 2) -> List[Tuple[int, int]]:
    """Find gaps in sorted list of lengths. Returns list of (start, end) of gaps."""
    gaps = []
    for i in range(len(lengths) - 1):
        if lengths[i + 1] - lengths[i] > min_gap:
            gaps.append((lengths[i] + 1, lengths[i + 1] - 1))
    return gaps
