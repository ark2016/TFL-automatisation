"""Length density / periodicity heuristic."""
from pumping_lemma.heuristics.base import AbstractHeuristic
from pumping_lemma.models.language_spec import LanguageSpec
from pumping_lemma.models.results import HeuristicResult
from pumping_lemma.utils.math_utils import analyze_growth_pattern, find_gaps
from pumping_lemma.utils.word_gen import generate_all_words, generate_pumping_splits, pump_word


class LengthDensityHeuristic(AbstractHeuristic):
    @property
    def name(self):
        return "length_density"

    def analyze(self, spec: LanguageSpec, pumping_constant: int = 20) -> HeuristicResult:
        if not spec.has_membership_test():
            return self._unknown_result("Нет функции проверки принадлежности")

        # Collect word lengths present in language
        max_search_length = pumping_constant * 5
        max_gen_length = min(max_search_length, 15)  # cap to avoid combinatorial explosion
        lengths = set()
        for word in generate_all_words(spec.alphabet, max_gen_length, max_words=50000):
            if spec.accepts(word):
                lengths.add(len(word))

        if len(lengths) < 5:
            return self._unknown_result("Недостаточно данных о длинах слов")

        sorted_lengths = sorted(lengths)
        growth = analyze_growth_pattern(sorted_lengths)

        if growth in ("linear", "unknown"):
            return self._unknown_result(f"Рост длин: {growth}, не указывает на нерегулярность")

        # Find gaps larger than 2*p
        gaps = find_gaps(sorted_lengths, min_gap=2 * pumping_constant)

        if not gaps:
            # Try smaller gaps
            gaps = find_gaps(sorted_lengths, min_gap=pumping_constant)

        if not gaps:
            return self._unknown_result("Не найдены достаточно большие зазоры в длинах")

        # For each gap, find a word just before it and verify pumping fails
        for gap_start, gap_end in gaps:
            word_length = gap_start - 1
            if word_length < pumping_constant:
                continue

            # Find a word of this length in the language
            word = self._find_word_of_length(spec, word_length)
            if word is None:
                continue

            # Check that no valid pumping split exists
            if self._verify_pumping_fails(word, spec, pumping_constant, gap_start, gap_end):
                return HeuristicResult(
                    heuristic_name=self.name,
                    verdict="non_regular",
                    confidence=0.85,
                    proof_trace=[
                        f"Паттерн роста длин: {growth}",
                        f"Зазор в длинах: [{gap_start}, {gap_end}]",
                        f"Слово: w = '{word}' (длина {len(word)})",
                        f"Накачка любого разбиения приводит к длине в зазоре.",
                        "По лемме о накачке → язык НЕРЕГУЛЯРНЫЙ."
                    ],
                    counterexample={
                        "word": word, "gap": (gap_start, gap_end),
                        "growth_pattern": growth
                    }
                )

        return self._unknown_result(f"Рост {growth}, но не удалось построить контрпример")

    def _find_word_of_length(self, spec, length):
        """Find a word in the language of given length."""
        alpha = sorted(spec.alphabet)
        if not alpha:
            return None
        # Try simple constructions first
        for a in alpha:
            w = a * length
            if spec.accepts(w):
                return w
        # Try combinations (only for short lengths to avoid explosion)
        from itertools import product as iproduct
        if length <= 12:
            count = 0
            for combo in iproduct(alpha, repeat=length):
                w = ''.join(combo)
                if spec.accepts(w):
                    return w
                count += 1
                if count > 50000:
                    break
        return None

    def _verify_pumping_fails(self, word, spec, p, gap_start, gap_end):
        """Verify that for ALL splits, some pumped word falls outside language."""
        for x, y, z in generate_pumping_splits(word, p):
            # Check i=0 and i=2
            valid_for_this_split = True
            for i in [0, 2, 3]:
                pumped = pump_word(x, y, z, i)
                if not spec.accepts(pumped):
                    valid_for_this_split = False
                    break
            if valid_for_this_split:
                return False  # found a split that works -> can't prove non-regular
        return True  # no split works -> non-regular
