from lib.oracle import oracle_from_ir
import json

ir = json.load(open('examples/task1_palindrome_prefix_suffix.json'))
oracle = oracle_from_ir(ir)

print("=== Even: (ab)^n vs (ab)^{n+1}, context (ba)^n * aab ===")
for n in range(1, 6):
    w1 = "ab" * n
    w2 = "ab" * (n + 1)
    z = "ba" * n + "aab"
    r1 = oracle(w1 + z)
    r2 = oracle(w2 + z)
    print(f"  (ab)^{n} + '{z}' = {'IN' if r1 else 'NOT':3s}   (ab)^{n+1} + '{z}' = {'IN' if r2 else 'NOT':3s}   distinguishes: {r1 != r2}")

print()
print("=== Odd: (ab)^n a vs (ab)^{n+1} a, context (ba)^n * baaab ===")
for n in range(1, 6):
    w1 = "ab" * n + "a"
    w2 = "ab" * (n + 1) + "a"
    z = "ba" * n + "baaab"
    r1 = oracle(w1 + z)
    r2 = oracle(w2 + z)
    print(f"  (ab)^{n}a + '{z}' = {'IN' if r1 else 'NOT':3s}   (ab)^{n+1}a + '{z}' = {'IN' if r2 else 'NOT':3s}   distinguishes: {r1 != r2}")
