"""Typed interpretation of DCFL set-builder constraints.

Canonical ``integer_cmp(left, op, right)`` compares a word block's length
with a natural-number constant or another block's length. The old
``integer_cmp(var, op, value)`` spelling denotes a numeric scalar; it is
kept explicitly distinct and is not a Lean word-block constraint.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from typing import Literal

COMPARISONS = {
    "<": operator.lt, "<=": operator.le, "==": operator.eq,
    "!=": operator.ne, ">=": operator.ge, ">": operator.gt,
}


@dataclass(frozen=True)
class Comparison:
    mode: Literal["length", "numeric_legacy"]
    left: str
    op: str
    right: str | int


@dataclass(frozen=True)
class WordRelation:
    left: str
    right: str
    reverse: bool = False


@dataclass(frozen=True)
class RegexMembership:
    var: str
    pattern: str


@dataclass(frozen=True)
class Disjunction:
    branches: tuple[Constraint, ...]


Constraint = Comparison | WordRelation | RegexMembership | Disjunction


def parse_constraint(raw: object, variable_names: set[str] | None = None) -> Constraint:
    """Validate one JSON constraint, including every nested branch.

    Raises ``ValueError``/``TypeError`` for malformed/unsupported input. Consumers must
    not replace an unsupported predicate by either true or false.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("args"), dict):
        raise TypeError("constraint requires a kind and an args object")
    kind, args = raw.get("kind"), raw["args"]

    def name(value: object) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("constraint operand must be a non-empty variable name")
        if variable_names is not None and value not in variable_names:
            raise ValueError(f"unknown constraint variable: {value}")
        return value

    def fields(expected: set[str]) -> None:
        if set(args) != expected:
            raise ValueError(f"{kind} args must contain exactly {sorted(expected)}")

    if kind in {"length_cmp", "integer_cmp"}:
        op = args.get("op")
        if not isinstance(op, str) or op not in COMPARISONS:
            raise ValueError("comparison op must be one of <, <=, ==, !=, >=, >")
        legacy = kind == "integer_cmp" and ("var" in args or "value" in args)
        fields({"var", "op", "value"} if legacy else {"left", "op", "right"})
        left = name(args["var"] if legacy else args["left"])
        right = args["value"] if legacy else args["right"]
        if kind == "length_cmp" or isinstance(right, str):
            right = name(right)
        elif type(right) is not int:
            raise ValueError("integer_cmp right/value must be an integer or variable name")
        return Comparison("numeric_legacy" if legacy else "length", left, op, right)
    if kind in {"equal", "reverse"}:
        fields({"left", "right"})
        return WordRelation(name(args["left"]), name(args["right"]), kind == "reverse")
    if kind == "regex_member":
        fields({"var", "pattern"})
        var = name(args["var"])
        if not isinstance(args["pattern"], str):
            raise ValueError("regex_member pattern must be a string")
        try:
            re.compile(args["pattern"])
        except re.error as exc:
            raise ValueError("invalid regex_member pattern") from exc
        return RegexMembership(var, args["pattern"])
    if kind == "disjunction":
        fields({"branches"})
        branches = args["branches"]
        if not isinstance(branches, list) or not branches:
            raise ValueError("disjunction branches must be a non-empty list of constraints")
        return Disjunction(tuple(parse_constraint(b, variable_names) for b in branches))
    raise ValueError(f"unsupported constraint kind: {kind}")


def parse_constraints(raw: object, variable_names: set[str] | None = None) -> tuple[Constraint, ...]:
    if not isinstance(raw, list):
        raise TypeError("constraints must be a list")
    return tuple(parse_constraint(c, variable_names) for c in raw)


def evaluate_constraints(variables: dict[str, str], constraints: tuple[Constraint, ...]) -> bool:
    """Evaluate a complete assignment; absent values never stand for ε."""
    def evaluate(c: Constraint) -> bool:
        if isinstance(c, Disjunction):
            return any(evaluate(branch) for branch in c.branches)
        try:
            if isinstance(c, Comparison):
                convert = len if c.mode == "length" else int
                left = convert(variables[c.left])
                right = convert(variables[c.right]) if isinstance(c.right, str) else c.right
                return COMPARISONS[c.op](left, right)
            if isinstance(c, WordRelation):
                right = variables[c.right]
                return variables[c.left] == (right[::-1] if c.reverse else right)
            return re.fullmatch(c.pattern, variables[c.var]) is not None
        except (KeyError, ValueError, TypeError):
            return False

    return all(evaluate(c) for c in constraints)
