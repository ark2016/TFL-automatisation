# check_nerode.py
from lib.oracle import oracle_from_ir
from lib.congruence import compute_nerode_classes
import json

ir = json.load(open('examples/task1_palindrome_prefix_suffix.json'))
oracle = oracle_from_ir(ir)

# Проверим конкретные слова
test = ["aa", "bb", "ab", "ba", "abba", "baab", "aba", "bab",
        "abbba", "aab", "bba", "abab", "baba", "ababab", "ababba"]
for w in test:
    print(f"  {w:12s} -> {oracle(w)}")

print()
result = compute_nerode_classes(oracle, ['a','b'], max_depth=10)
print(f"Classes: {result['num_classes']}")
print(f"Likely infinite: {result['likely_infinite']}")
print(f"Growth pattern: {result['growth_pattern']}")
if not result['likely_infinite']:
    print("ЯЗЫК РЕГУЛЯРЕН — классы стабилизировались")
else:
    print("ЯЗЫК НЕРЕГУЛЯРЕН — классы растут")
