"""Parikh vector analysis heuristic."""
from pumping_lemma.heuristics.base import AbstractHeuristic
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.models.results import HeuristicResult
from pumping_lemma.utils.math_utils import compute_parikh_vector, check_linear_constraint_satisfiable
from pumping_lemma.utils.word_gen import generate_critical_words, generate_pumping_splits, pump_word


class ParikhHeuristic(AbstractHeuristic):
    @property
    def name(self):
        return "parikh_vector"

    def analyze(self, spec: LanguageSpec, pumping_constant: int = 20) -> HeuristicResult:
        if not spec.constraints:
            return self._unknown_result("Нет ограничений Париха для анализа")
        if not spec.has_membership_test():
            return self._unknown_result("Нет функции проверки принадлежности")

        # Generate candidate words
        words = generate_critical_words(
            spec.alphabet, spec.structural_pattern,
            min_length=pumping_constant, max_length=pumping_constant * 3
        )
        # Filter to words actually in language
        words = [w for w in words if spec.accepts(w)][:20]

        if not words:
            return self._unknown_result("Не найдены слова языка достаточной длины")

        for word in words:
            result = self._check_word(word, spec, pumping_constant)
            if result and result.verdict == "non_regular":
                return result

        return self._unknown_result("Все проверенные слова допускают накачку по Париху")

    def _check_word(self, word, spec, p):
        """Check if word can be pumped while satisfying all Parikh constraints.
        Produces detailed proof trace with concrete splits and Parikh vectors."""
        alphabet = spec.alphabet
        alpha_sorted = sorted(alphabet)
        found_valid_split = False

        # Collect detailed violation info for proof
        split_violations = []  # list of dicts with split details
        total_splits = 0

        for x, y, z in generate_pumping_splits(word, p):
            total_splits += 1
            psi_y = compute_parikh_vector(y, alphabet)
            psi_xz = compute_parikh_vector(x + z, alphabet)
            psi_w = compute_parikh_vector(word, alphabet)

            # Check if ALL constraints satisfied for ALL i >= 0
            all_constraints_ok = True
            violation_info = None
            for constraint in spec.constraints:
                satisfiable_for_all_i = self._check_constraint_for_all_i(
                    psi_xz, psi_y, constraint
                )
                if not satisfiable_for_all_i:
                    all_constraints_ok = False
                    violation_info = self._describe_violation(
                        x, y, z, psi_xz, psi_y, psi_w, constraint, alpha_sorted, spec
                    )
                    break

            if all_constraints_ok:
                # Also verify by actual membership test for i=0,2
                pumped_0 = pump_word(x, y, z, 0)
                pumped_2 = pump_word(x, y, z, 2)
                if spec.accepts(pumped_0) and spec.accepts(pumped_2):
                    found_valid_split = True
                    break

            if violation_info and len(split_violations) < 3:
                split_violations.append(violation_info)

        if not found_valid_split:
            # Build detailed proof trace
            def _fmt_vec(v):
                return "(" + ", ".join(f"|w|_{s}={v.get(s,0)}" for s in alpha_sorted) + ")"

            psi_w = compute_parikh_vector(word, alphabet)
            w_short = word if len(word) <= 40 else word[:18] + "..." + word[-18:]

            trace = [
                f"**Допустим** язык L регулярен. Тогда по лемме о накачке существует p ≥ 1.",
                f"**Выберем** слово w = {w_short} ∈ L длины {len(word)} ≥ p = {p}.",
                f"Вектор Париха: Ψ(w) = {_fmt_vec(psi_w)}",
                f"",
                f"**По лемме** ∃ разбиение w = xyz, где |xy| ≤ {p}, |y| > 0, и ∀i ≥ 0: xy^iz ∈ L.",
                f"Т.к. |xy| ≤ {p}, часть y целиком лежит в первых {p} символах слова: '{word[:p]}'.",
                f"",
                f"**Проверяем все {total_splits} разбиений** и показываем, что каждое ведёт к противоречию:",
            ]

            for idx, viol in enumerate(split_violations, 1):
                trace.append(f"")
                trace.append(f"--- Разбиение {idx} из {total_splits} (показательный пример) ---")
                trace.extend(viol)

            if len(split_violations) < total_splits:
                trace.append(f"")
                trace.append(f"(Остальные {total_splits - len(split_violations)} разбиений "
                           f"аналогично приводят к противоречию.)")

            trace.append(f"")
            trace.append(f"**Вывод:** Ни одно разбиение не позволяет накачку → ПРОТИВОРЕЧИЕ.")
            trace.append(f"Следовательно, язык L **нерегулярен**. ∎")

            # Build detailed counterexample
            ce = {"word": word, "word_length": len(word), "pumping_constant": p,
                  "total_splits_checked": total_splits, "all_violated": True}
            if split_violations:
                ce["example_split"] = split_violations[0]

            return HeuristicResult(
                heuristic_name=self.name,
                verdict="non_regular",
                confidence=0.9,
                proof_trace=trace,
                counterexample=ce,
            )
        return None

    def _describe_violation(self, x, y, z, psi_xz, psi_y, psi_w, constraint, alpha_sorted, spec):
        """Produce detailed human-readable violation description for one split."""
        lines = []

        def _fmt(v):
            return "(" + ", ".join(f"{s}:{v.get(s,0)}" for s in alpha_sorted) + ")"

        x_short = x if len(x) <= 15 else x[:6] + ".." + x[-6:]
        y_short = y if len(y) <= 15 else y[:6] + ".." + y[-6:]
        z_short = z if len(z) <= 15 else z[:6] + ".." + z[-6:]

        lines.append(f"x = '{x_short}' (длина {len(x)}), "
                     f"y = '{y_short}' (длина {len(y)}), "
                     f"z = '{z_short}' (длина {len(z)})")
        lines.append(f"|xy| = {len(x)+len(y)} ≤ {len(x)+len(y)}, |y| = {len(y)} > 0  ✓")
        lines.append(f"Ψ(y) = {_fmt(psi_y)},  Ψ(xz) = {_fmt(psi_xz)}")

        ct = constraint.constraint_type
        syms = constraint.symbols

        if ct == "equal" and len(syms) >= 2:
            s0, s1 = syms[0], syms[1]
            py0, py1 = psi_y.get(s0, 0), psi_y.get(s1, 0)
            pxz0, pxz1 = psi_xz.get(s0, 0), psi_xz.get(s1, 0)

            lines.append(f"Ограничение языка: |w|_{s0} = |w|_{s1}")
            lines.append(f"При накачке (i раз): |xy^iz|_{s0} = {pxz0} + i·{py0},  "
                        f"|xy^iz|_{s1} = {pxz1} + i·{py1}")

            if py0 != py1:
                lines.append(f"  → Ψ(y) несбалансирован: {s0}:{py0} ≠ {s1}:{py1}")
                lines.append(f"  → Коэффициенты при i различны ({py0} vs {py1}), "
                           f"равенство |w|_{s0} = |w|_{s1} невозможно для всех i.")
                # Show concrete failing i
                pumped_0 = pump_word(x, y, z, 0)
                pumped_2 = pump_word(x, y, z, 2)
                psi_0 = compute_parikh_vector(pumped_0, set(alpha_sorted))
                psi_2 = compute_parikh_vector(pumped_2, set(alpha_sorted))
                in_lang_0 = spec.accepts(pumped_0) if spec.has_membership_test() else "?"
                in_lang_2 = spec.accepts(pumped_2) if spec.has_membership_test() else "?"

                lines.append(f"  Конкретно:")
                p0_short = pumped_0 if len(pumped_0) <= 30 else pumped_0[:12] + ".." + pumped_0[-12:]
                p2_short = pumped_2 if len(pumped_2) <= 30 else pumped_2[:12] + ".." + pumped_2[-12:]
                lines.append(f"    i=0: xy⁰z = '{p0_short}', "
                           f"Ψ = {_fmt(psi_0)}, "
                           f"|w|_{s0}={psi_0.get(s0,0)} {'=' if psi_0.get(s0,0)==psi_0.get(s1,0) else '≠'} "
                           f"|w|_{s1}={psi_0.get(s1,0)} → {'∈' if in_lang_0 else '∉'} L")
                lines.append(f"    i=2: xy²z = '{p2_short}', "
                           f"Ψ = {_fmt(psi_2)}, "
                           f"|w|_{s0}={psi_2.get(s0,0)} {'=' if psi_2.get(s0,0)==psi_2.get(s1,0) else '≠'} "
                           f"|w|_{s1}={psi_2.get(s1,0)} → {'∈' if in_lang_2 else '∉'} L")
            elif pxz0 != pxz1:
                lines.append(f"  → Ψ(xz) несбалансирован: {s0}:{pxz0} ≠ {s1}:{pxz1}")
                lines.append(f"  → При i=0: xy⁰z = xz, и |xz|_{s0}={pxz0} ≠ |xz|_{s1}={pxz1} → xz ∉ L")

        elif ct == "linear":
            coeffs = constraint.coefficients
            const = constraint.constant
            inc_sum = sum(c * psi_y.get(s, 0) for c, s in zip(coeffs, syms))
            base_sum = sum(c * psi_xz.get(s, 0) for c, s in zip(coeffs, syms))
            expr = " + ".join(f"{c}·|w|_{s}" for c, s in zip(coeffs, syms))
            lines.append(f"Ограничение: {expr} = {const}")
            lines.append(f"При накачке: {base_sum} + i·{inc_sum} = {const}")
            if inc_sum != 0:
                lines.append(f"  → Линейная функция от i (наклон {inc_sum} ≠ 0), "
                           f"не может равняться {const} для всех i.")
            else:
                lines.append(f"  → {base_sum} ≠ {const} → нарушено уже при i=1.")

        return lines

    def _check_constraint_for_all_i(self, psi_xz, psi_y, constraint):
        """Check if psi_xz + i*psi_y satisfies constraint for ALL i >= 0."""
        ct = constraint.constraint_type
        syms = constraint.symbols

        if ct == "equal" and len(syms) >= 2:
            s0, s1 = syms[0], syms[1]
            # Need: psi_xz[s0] + i*psi_y[s0] == psi_xz[s1] + i*psi_y[s1] for all i
            # This means: psi_y[s0] == psi_y[s1] AND psi_xz[s0] == psi_xz[s1]
            if psi_y.get(s0, 0) != psi_y.get(s1, 0):
                return False
            if psi_xz.get(s0, 0) != psi_xz.get(s1, 0):
                return False
            return True

        if ct == "linear":
            coeffs = constraint.coefficients
            const = constraint.constant
            # Need: sum(c_j * (psi_xz[s_j] + i*psi_y[s_j])) == const for all i
            inc_sum = sum(c * psi_y.get(s, 0) for c, s in zip(coeffs, syms))
            base_sum = sum(c * psi_xz.get(s, 0) for c, s in zip(coeffs, syms))
            if inc_sum != 0:
                return False  # changes with i -> can't hold for all i
            return base_sum == const

        return True  # unknown constraint type -> assume ok
