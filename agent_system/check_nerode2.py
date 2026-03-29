from lib.oracle import oracle_from_ir
from lib.congruence import compute_nerode_classes
import json

ir = json.load(open('examples/task1_palindrome_prefix_suffix.json'))
oracle = oracle_from_ir(ir)

result = compute_nerode_classes(oracle, ['a','b'], max_depth=8)

# Показать представителей разных классов и различающие контексты
classes = result.get('classes', {})
reps = result.get('representatives', [])

# Берём первые 10 классов и их представителей
if reps:
    for i, r in enumerate(reps[:15]):
        print(f"Class {i}: '{r}'")
elif classes:
    for k, v in list(classes.items())[:15]:
        print(f"'{k}' -> class {v}")
else:
    print("Keys:", list(result.keys()))
