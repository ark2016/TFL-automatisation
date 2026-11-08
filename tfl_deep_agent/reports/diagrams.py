"""State diagrams for the report: Graphviz DOT built by code from the automata
in the evidence ledger, rendered to inline SVG with the local ``dot`` binary.

Only code-made DOT is ever rendered. The renderer collects the DOT sources it
made in a set, and ``markdown_to_html`` turns a ```` ```dot ```` fence into a
picture only when its text is one of them, so a ``dot`` fence in model text
stays a code block (Graphviz HTML labels can read local files: ``<IMG SRC>``).

Without ``dot`` (or on a render error / timeout) the HTML keeps the DOT source
as a code block next to the transition table; nothing else changes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

MAX_DIAGRAM_STATES = 30
MAX_DIAGRAM_EDGES = 120
DOT_TIMEOUT_S = 15

_UNSET: Any = object()
_dot_binary: Any = _UNSET


def dot_binary() -> str | None:
    """The Graphviz ``dot`` binary on PATH, looked up once per process."""
    global _dot_binary
    if _dot_binary is _UNSET:
        _dot_binary = shutil.which("dot")
    return _dot_binary


def _q(s: Any) -> str:
    """A DOT quoted string (ids and labels): only ``\\`` and ``"`` need escaping,
    and newlines are flattened, so the text can never close the string."""
    text = re.sub(r"[\r\n]+", " ", str(s))
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sym(a: Any) -> str:
    s = "" if a is None else str(a)
    return "ε" if s == "" else s


_HEAD = [
    "  rankdir=LR;",
    '  bgcolor="transparent";',
    '  node [shape=circle, fontname="Helvetica", fontsize=12];',
    '  edge [fontname="Helvetica", fontsize=11, arrowsize=0.7];',
    '  __start [shape=point, width=0.05, height=0.05, label=""];',
]


def dfa_to_dot(dfa: Mapping[str, Any]) -> str | None:
    """DOT for a DFA ``{states, alphabet, start, accept, transitions: {q: {a: q'}}}``.

    Parallel edges merge into one labelled ``a, b``; accepting states are
    double circles; a non-accepting state whose every letter loops back to
    itself (the trap) is drawn dashed. ``None`` when the automaton is too big
    to read as a picture or has no transitions."""
    trans = dfa.get("transitions")
    if not isinstance(trans, Mapping) or not trans:
        return None
    states = [str(s) for s in dfa.get("states") or trans.keys()]
    if not states or len(states) > MAX_DIAGRAM_STATES:
        return None
    alphabet = [str(a) for a in dfa.get("alphabet") or []]
    if not alphabet:
        alphabet = sorted({str(a) for row in trans.values() if isinstance(row, Mapping) for a in row})
    start = str(dfa.get("start"))
    accept = {str(s) for s in dfa.get("accept") or dfa.get("accepting") or dfa.get("final") or []}
    edges: dict[tuple[str, str], list[str]] = {}
    for s in states:
        row = trans.get(s)
        if not isinstance(row, Mapping):
            continue
        for a in alphabet:
            if row.get(a) is not None:
                edges.setdefault((s, str(row[a])), []).append(_sym(a))
    if not edges or len(edges) > MAX_DIAGRAM_EDGES:
        return None
    traps = {s for s in states if s not in accept and alphabet
             and all(isinstance(trans.get(s), Mapping) and str(trans[s].get(a)) == s for a in alphabet)}
    lines = ["digraph DFA {", *_HEAD]
    for s in states:
        attrs = []
        if s in accept:
            attrs.append("shape=doublecircle")
        if s in traps:
            attrs.append("style=dashed")
        lines.append(f"  {_q(s)}" + (f" [{', '.join(attrs)}]" if attrs else "") + ";")
    if start in states:
        lines.append(f"  __start -> {_q(start)};")
    for (src, dst), letters in edges.items():
        lines.append(f"  {_q(src)} -> {_q(dst)} [label={_q(', '.join(letters))}];")
    lines.append("}")
    return "\n".join(lines)


def dpda_to_dot(dpda: Mapping[str, Any]) -> str | None:
    """DOT for a DPDA in the dcfl ``dpda`` contract (``transitions: [{from, read,
    top, to, push}]``, push topmost first). Edge labels read ``a, Z / AZ``; ε
    for an epsilon move or an empty push."""
    transitions = dpda.get("transitions")
    if not isinstance(transitions, Sequence) or isinstance(transitions, (str, bytes)) or not transitions:
        return None
    start = str(dpda.get("start"))
    accept = {str(s) for s in dpda.get("accept_states") or []}
    states: list[str] = []
    for s in [start, *(str(x) for x in dpda.get("states") or [])]:
        if s not in states:
            states.append(s)
    edges: dict[tuple[str, str], list[str]] = {}
    for t in transitions:
        if not isinstance(t, Mapping):
            continue
        src, dst = str(t.get("from", t.get("from_"))), str(t.get("to"))
        for s in (src, dst):
            if s not in states:
                states.append(s)
        read = t.get("read")
        read = "ε" if read is None or str(read).strip().lower() in ("", "ε", "eps", "epsilon") else str(read)
        push = "".join(str(x) for x in t.get("push") or []) or "ε"
        edges.setdefault((src, dst), []).append(f"{read}, {t.get('top')} / {push}")
    if len(states) > MAX_DIAGRAM_STATES or len(edges) > MAX_DIAGRAM_EDGES:
        return None
    lines = ["digraph DPDA {", *_HEAD]
    for s in states:
        lines.append(f"  {_q(s)}" + (" [shape=doublecircle]" if s in accept else "") + ";")
    lines.append(f"  __start -> {_q(start)};")
    for (src, dst), labels in edges.items():
        # one transition per line; \n inside a DOT string is a centred line break
        label = "\\n".join(_q(x)[1:-1] for x in labels)
        lines.append(f'  {_q(src)} -> {_q(dst)} [label="{label}"];')
    lines.append("}")
    return "\n".join(lines)


_UNSAFE_SVG = re.compile(r"<\s*(script|foreignObject|iframe|image|use)\b|\bon\w+\s*=|javascript:", re.I)


def dot_to_svg(dot_src: str) -> str | None:
    """Inline SVG for ``dot_src`` (code-made DOT only), themed by
    ``currentColor`` so it reads in light and dark pages; ``None`` without
    ``dot``, on an error or a timeout, or if the output holds anything active."""
    exe = dot_binary()
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "-Tsvg"], input=dot_src.encode("utf-8"), capture_output=True,
                              timeout=DOT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    svg = proc.stdout.decode("utf-8", errors="replace")
    start = svg.find("<svg")
    if start < 0:
        return None
    svg = re.sub(r"<!--.*?-->", "", svg[start:], flags=re.S).strip()
    if _UNSAFE_SVG.search(svg):
        return None
    svg = (svg.replace('stroke="black"', 'stroke="currentColor"')
              .replace('fill="black"', 'fill="currentColor"')
              .replace('fill="white"', 'fill="none"'))
    return svg  # natural size; the page's CSS shrinks a wide one to the column (max-width)
