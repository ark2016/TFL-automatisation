from lib.oracle import oracle_from_ir
import json

ir = json.load(open('examples/task1_palindrome_prefix_suffix.json'))
oracle = oracle_from_ir(ir)

# Слова НЕ в L (чередующиеся)
words = ["ab", "aba", "abab", "ababa", "ababab", "abababa"]

# Ищем различающие контексты для каждой пары
from lib.word_generator import generate_exhaustive
contexts = generate_exhaustive(['a','b'], max_len=8)

for i in range(len(words)):
    for j in range(i+1, len(words)):
        w1, w2 = words[i], words[j]
        for z in contexts:
            r1 = oracle(w1 + z)
            r2 = oracle(w2 + z)
            if r1 != r2:
                print(f"  {w1:10s} vs {w2:10s}  context='{z}'  {w1}+z={'IN' if r1 else 'NOT'}  {w2}+z={'IN' if r2 else 'NOT'}")
                break
