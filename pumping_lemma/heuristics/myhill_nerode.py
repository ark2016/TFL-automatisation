"""Myhill-Nerode infinite distinguishing prefixes heuristic."""
from pumping_lemma.heuristics.base import AbstractHeuristic
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.models.results import HeuristicResult
from pumping_lemma.utils.word_gen import generate_all_words


class MyhillNerodeHeuristic(AbstractHeuristic):
    @property
    def name(self):
        return "myhill_nerode"

    def analyze(self, spec: LanguageSpec, pumping_constant: int = 20) -> HeuristicResult:
        if not spec.has_membership_test():
            return self._unknown_result("Нет функции проверки принадлежности")

        alpha = sorted(spec.alphabet)
        if not alpha:
            return self._unknown_result("Пустой алфавит")

        # Generate prefix families
        prefix_families = self._generate_prefix_families(alpha, pumping_constant)

        # Generate test suffixes
        suffixes = self._generate_suffixes(alpha, max_length=pumping_constant)

        for family_name, prefixes in prefix_families:
            result = self._check_family(prefixes, suffixes, spec, family_name)
            if result and result.verdict == "non_regular":
                return result

        return self._unknown_result("Не найдено бесконечное множество различимых префиксов")

    def _generate_prefix_families(self, alpha, max_n):
        """Generate parametric prefix families."""
        families = []
        a = alpha[0]
        b = alpha[1] if len(alpha) > 1 else alpha[0]

        # Family: a^i
        families.append((f"{a}^i", [a * i for i in range(1, min(max_n + 1, 30))]))

        if a != b:
            # Family: a^i b
            families.append((f"{a}^i{b}", [a * i + b for i in range(1, min(max_n + 1, 30))]))
            # Family: (ab)^i
            families.append((f"({a}{b})^i", [(a + b) * i for i in range(1, min(max_n + 1, 15))]))

        return families

    def _generate_suffixes(self, alpha, max_length):
        """Generate test suffixes."""
        suffixes = ['']
        for s in alpha:
            for length in range(1, min(max_length + 1, 20)):
                suffixes.append(s * length)
        # Short combinations
        if len(alpha) > 1:
            for word in generate_all_words(set(alpha), min(5, max_length)):
                if len(word) <= 5:
                    suffixes.append(word)
        return list(set(suffixes))

    def _check_family(self, prefixes, suffixes, spec, family_name):
        """Check if prefixes in family are pairwise distinguishable."""
        # Build signature for each prefix: tuple of membership results for all suffixes
        signatures = {}
        for prefix in prefixes:
            sig = tuple(spec.accepts(prefix + suffix) for suffix in suffixes)
            signatures[prefix] = sig

        # Count distinct signatures
        unique_sigs = {}
        for prefix, sig in signatures.items():
            if sig not in unique_sigs:
                unique_sigs[sig] = []
            unique_sigs[sig].append(prefix)

        num_distinct = len(unique_sigs)

        if num_distinct >= 10:
            # Found many distinct equivalence classes -> likely infinite -> non-regular
            # Find distinguishing examples
            examples = []
            sigs_list = list(unique_sigs.items())
            for i in range(min(3, len(sigs_list))):
                for j in range(i + 1, min(4, len(sigs_list))):
                    sig_i, prefixes_i = sigs_list[i]
                    sig_j, prefixes_j = sigs_list[j]
                    # Find distinguishing suffix
                    for k, (a, b_val) in enumerate(zip(sig_i, sig_j)):
                        if a != b_val:
                            examples.append((prefixes_i[0], prefixes_j[0], suffixes[k]))
                            break

            trace = [
                f"**Теорема Майхилла-Нероуда:** L регулярен ⟺ отношение ≡_L имеет конечный индекс.",
                f"Два слова u ≡_L v, если ∀z: (uz ∈ L ⟺ vz ∈ L).",
                f"",
                f"**Семейство префиксов:** {family_name}",
                f"Проверено {len(prefixes)} префиксов, найдено **{num_distinct} различных классов** эквивалентности.",
                f"",
                f"**Конкретные различимые пары:**",
            ]
            for idx, (u, v, z) in enumerate(examples[:5], 1):
                uz_in = spec.accepts(u + z)
                vz_in = spec.accepts(v + z)
                trace.append(f"")
                trace.append(f"  Пара {idx}: u = '{u}', v = '{v}'")
                trace.append(f"    Различающий суффикс: z = '{z}'")
                trace.append(f"    u·z = '{u}{z}' → {'∈ L ✓' if uz_in else '∉ L ✗'}")
                trace.append(f"    v·z = '{v}{z}' → {'∈ L ✓' if vz_in else '∉ L ✗'}")
                trace.append(f"    Следовательно: '{u}' ≢_L '{v}'  (разные классы)")

            trace.extend([
                f"",
                f"Число различимых префиксов растёт линейно с параметром → **индекс ≡_L бесконечен**.",
                f"По теореме Майхилла-Нероуда → язык L **нерегулярен**. ∎",
            ])

            return HeuristicResult(
                heuristic_name=self.name,
                verdict="non_regular",
                confidence=0.85,
                proof_trace=trace,
                counterexample={
                    "family": family_name,
                    "distinct_classes": num_distinct,
                    "examples": [
                        {"u": u, "v": v, "suffix": z,
                         "uz_in_L": spec.accepts(u + z), "vz_in_L": spec.accepts(v + z)}
                        for u, v, z in examples[:5]
                    ]
                }
            )

        return None
