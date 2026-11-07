# Tool inventory (P1)

For each tool in the architecture table ("Инструменты агента", `docs/architecture.html`), this file lists the
functions in `agent_system/lib`, `cfl_system/lib`, `dcfl_system/lib` and `ll_system/lib` that back it: their
signatures as of commit `752a0b5`, the input format, the output, cost and limits, and the trust the wrapper may
report under `docs/VERDICT_POLICY.md`. Tool names come from the architecture. The few tools added here are marked
**(added)**.

Group modules are built on `tfl_deep_agent/tools/base.py`:

- Each tool is `@tfl_tool(group=..., max_trust=..., timeout_s=..., timeout_mode=...)` over
  `func(args: <ToolArgs subclass>) -> ToolResult`.
- The group function computes the trust from the lib output. `REGISTRY.invoke` clamps it to the tool's `max_trust`
  and to the group ceiling (`GROUP_MAX_TRUST`). A failure is always `not_verified`.
- `bounded_pass` must report `bounds` (`max_len`, `n_words`, `n_unknown`, `max_k`, `p_values`, `i_values`,
  `truncated`, plus any extra keys).
- An oracle answer of `None` (unknown) is never "not in L". Count it in `bounds.n_unknown` and leave it out of
  the decisive words (VERDICT_POLICY §4).
- Every oracle check is **relative to the IR the agent built**. Put the IR's id or hash in `data`, so the report
  can say "проверено относительно IR №…" (architecture, "Вход").

## 0. Shared formats

| Object | Format | Used by |
|---|---|---|
| IR | JSON object with `task_type`, `source_text` and a `language_spec` (LL Format 3: a top-level `grammar`). The task types and `language_spec.kind` values differ by class, see §1. | all validators and oracles |
| grammar | `{"terminals": [...], "nonterminals": [...], "start": "S", "rules": [{"lhs": "S", "rhs": ["a", "S"]}, ...]}`; ε is `rhs: []` (ll also accepts `["ε"]`, `ll utils.normalize_grammar`). Terminals and nonterminals must not overlap. In ll, `$` and `ε` are reserved. | cnf, cyk, grammar_utils, parikh, first_follow, ll_table_builder, grammar_transforms |
| DFA | `{"states", "alphabet", "transitions": {q: {a: q'}}, "start", "accept"}` with **single-character** symbols (`dfa_runner.run_dfa` iterates over characters) | dfa_runner, dfa_builder, oracle_test |
| PDA (cfl) | `{"states", "input_alphabet", "stack_alphabet", "start_state", "start_stack", "accept_mode": "final_state" or "empty_stack", "transitions", ...}` (`pda_simulator.validate_pda`). The prompt contract is converted with `cfl_oracle_test.normalize_agent_pda(pda, acceptance_mode)`. | pda_simulator |
| DPDA (dcfl) | the `dpda` proof_sketch contract (`dcfl_system/tz_dcfl_agent_system.md` §5.2): transitions with `read` / `top` / `push` (topmost first), `initial_stack`, `accept_states`, optional `accept_configs`, `accept_mode` | dcfl `dpda.py` |
| word | a plain `str` of single-character symbols, `""` = ε | every oracle |

Oracles do not all have the same signature. The `check_membership` wrapper must not mix them up:

| Class | Builder | Oracle type | No oracle for this IR |
|---|---|---|---|
| REG | `agent_system.lib.oracle.oracle_from_ir(ir: dict) -> Callable[[str], bool]` | `bool` | raises `ValueError` |
| CFL | `cfl_system.lib.cfl_oracle.cfl_oracle_from_ir(ir: dict) -> Callable[[str], bool]` | `bool` (the exponent-pattern path can give `None`) | raises `UnsupportedOracleKindError` |
| DCFL | `dcfl_system.lib.word_sampler.build_membership_oracle_from_ir(ir: dict, max_word_len: int = 60)` | `bool \| None` | returns `None` |
| LL | `ll_system.lib.word_oracle.oracle_from_ll_ir(ir: dict) -> Callable[[str], bool \| None] \| None` | `bool \| None` | returns `None` |

The exponent-notation oracle (`cfl_system.lib.exponent_pattern.parse_exponent_pattern(text)`, used inside the CFL
and DCFL builders) answers `None` when a word is longer than `_MAX_WORD_LEN = 40` or when its `_STEP_BUDGET = 200000`
is used up. The ll template matcher has `_MAX_STEPS = 200000`. Map a missing oracle to `ToolUnsupported`
(`error_kind: "unsupported"`), never to a guess.

## 1. IR — `group="ir"`, ceiling `well_formed`

| Tool | Backing functions | Input → output | Cost / limits | Trust |
|---|---|---|---|---|
| `describe_ir_schema` | `agent_system.lib.ir_schema.IR_SCHEMA`, `LANGUAGE_SPEC_SCHEMA`, `PREDICATE_SCHEMA`, `EXPR_SCHEMA` (JSON-schema dicts, REG only). The other classes have no schema constant; their kinds live in private sets: `cfl_ir_schema._CFL_TASK_TYPES` / `_CFL_LANG_KINDS`, `dcfl_ir_schema._DCFL_TASK_TYPES` / `_DCFL_INPUT_FORMATS` / `_DCFL_LANG_KINDS`, `ll_ir_schema._LL_TASK_TYPES` / `_LL_LANG_KINDS`. Examples: `docs/examples/*/input.json`, `{agent,cfl,dcfl,ll}_system/examples/*.json`, `tz_*.md`. | `cls: reg\|cfl\|dcfl\|ll` → schema text, allowed task types and kinds, 1–2 example IRs | static, ms | `not_verified` (reference) |
| `validate_ir` | `agent_system.lib.ir_schema.validate_ir(ir: Any) -> list[str]`; `cfl_system.lib.cfl_ir_schema.validate_cfl_ir(ir) -> list[str]`; `dcfl_system.lib.dcfl_ir_schema.validate_dcfl_ir(ir) -> list[str]`; `ll_system.lib.ll_ir_schema.validate_ll_ir(ir) -> list[str]` (the `*_json(json_str) -> (dict \| None, list[str])` variants parse first). None of them raise. | `cls`, `ir` → `{"valid": bool, "errors": [...]}`. The wrapper should also report whether an oracle builds (§0) and whether `lean_ir.render_statement_verbose` succeeds, so ir-builder knows which tools will work. | ms; `check_json_size` on the IR | `well_formed` when the error list is empty, `not_verified` (still `ok=True`) when it is not |
| `ir_to_text` | no lib function. Write it in the package as a deterministic paraphrase of `language_spec` for HITL 1. Helpers: `cfl_system.lib.language_preprocess.preprocess_language(ir) -> dict`, `ll_system.lib.preprocess.compute_preprocess_hints(ir) -> dict` (structural features). | `cls`, `ir` → text plus 5–10 words in and out of L (from `sample_words`) | ms | `well_formed` |

Task types and kinds (from the validators):

- REG: `classify`, `prove_regular`, `prove_non_regular`, `classify_and_prove`, `compute_pumping_length`, `build_dfa`,
  `build_complement`, `build_regex`, `parametric_analysis`. Kinds: `predicate`, `grammar`, `regex`, `natural`,
  `arithmetic_index`.
- CFL: `classify_cfl`, `prove_cfl`, `prove_non_cfl`, `classify_and_prove_cfl`, `grammar_filter_cfl`. Kinds: the REG
  ones plus `grammar_filter`, `repeated_subword`, `exists_decomposition` (example:
  `docs/examples/cfl_ticket50_w1bw2w3_lean_proved/input.json`).
- DCFL: `classify_dcfl`, `prove_dcfl`, `prove_non_dcfl`, `classify_and_prove_dcfl`. `input_format` and kind:
  `set_builder`, `grammar`.
- LL: `ll_check_language` (Format 1, `set_builder`), `ll_check_grammar_lang` (Format 2, `language_spec.kind ==
  "grammar"`), `ll_check_grammar` (Format 3, top-level `grammar`). Kinds: the REG ones plus `set_builder`.

## 2. Words — `group="words"`, ceiling `bounded_pass`

| Tool | Backing functions | Input → output | Cost / limits | Trust |
|---|---|---|---|---|
| `sample_words` | REG: `agent_system.lib.word_generator.generate_test_words(alphabet, strategies=None, pumping_constant=5, max_exhaustive=8, seed=42) -> list[str]` (also `generate_exhaustive(alphabet, max_len=8)`, `generate_boundary(alphabet, p)`), then the REG oracle labels the words. CFL: `cfl_system.lib.cfl_word_generator.generate_test_words(ir, max_positive=30, max_negative=30, max_length=20, seed=42) -> {"positive", "negative", "boundary"}`. DCFL: `dcfl_system.lib.word_sampler.sample_words(ir, count=20, max_len=30) -> list[{"word", "length", "in_language", "variables", "source"}]`. LL: `ll_system.lib.word_oracle.generate_words(ir, max_len) -> list[str]` (set_builder only; already filtered through the oracle). | `cls`, `ir`, `max_len` (≤ 20), `count` (≤ 200) → words in L and not in L. Each word carries a label **re-checked by the class oracle**. | exhaustive enumeration is bounded by `check_enumeration` (≤ 20000 words); 5–20 s; thread timeout | **reference only: always `not_verified`** (tool `max_trust="not_verified"`, `allow_refuted=False`), `data.evidence_role = "reference"`. Labelling words with the oracle checks no claim, so it is not evidence for a verdict (fix round, review finding F8); the bounds are still reported. `exhaustive_up_to` / `bounds.exhaustive_up_to` is the last length enumerated **in full** (0 or `None` when `max_enumerated` cut length 1 short), and the "no word of length ≤ N is in L" note is emitted only for such a fully enumerated N (F3). |
| `check_membership` | the four oracle builders in §0 | `cls`, `ir`, `words` (≤ 500, each ≤ 100 chars), optional `expected: {word: bool}` → per word `true` / `false` / `null` | CYK O(n³·\|G\|); `cyk_parse` raises `TimeoutError` when n > 100 and 10 s pass; wrapper timeout 20 s | trust is over the **expected words only** (`data.evidence_role = "claim_check"`; bounds: `n_words` = decided expected words, `n_unknown` = undecided ones). An exact oracle contradicting a label → `refuted`; any expected word the oracle could not decide → `not_verified` (`data.undecided_expected`, F7); an approximate oracle → `well_formed`; otherwise `bounded_pass`. Without `expected`: `not_verified`, `evidence_role = "reference"` (F8). |

## 3. REG — `group="reg"`, ceiling `verified`

| Tool | Backing functions | Input → output | Cost / limits | Trust |
|---|---|---|---|---|
| `regex_to_dfa` | `agent_system.lib.dfa_builder.build_dfa_from_regex(regex: str) -> dict` (minimal DFA; syntax: literals, `\|`, `*`, `+`, `?`, `()`, `\`; **no back-references**), `minimize_dfa(dfa) -> dict`, `dfa_to_regex(dfa) -> str`, `parse_regex(pattern)`. For a right- or left-linear grammar: `agent_system.lib.grammar_utils.grammar_to_dfa(grammar) -> dict \| None`, `is_right_linear(g)`, `is_left_linear(g)`. | `regex` or `grammar` → DFA dict (+ size) | subset construction can blow up: limit regex length (≤ 200) and the resulting state count (≤ 2000); process timeout | `verified` for "the regular language of this regex / linear grammar is recognised by this DFA" (VERDICT_POLICY §1: regularity of a given regex/DFA). This is **not** about the task language unless the regex is the task's own definition. |
| `run_dfa` | `agent_system.lib.dfa_runner.run_dfa(dfa: dict, word: str) -> bool`, `validate_dfa(dfa) -> list[str]` | `dfa`, `words` → accepted per word | O(\|w\|) | `verified` (an exact run on the given DFA); an invalid DFA gives `invalid_input` |
| `nerode_probe` | `agent_system.lib.congruence.find_distinguishing_context(oracle, word1, word2, alphabet, max_len=8) -> str \| None`, `compute_nerode_classes(oracle, alphabet, max_depth=8) -> {"classes", "num_classes", "growth_pattern", "likely_infinite", "distinguishing_pairs"}`, `estimate_index(oracle, alphabet, max_depth=10)`. Claim check: `agent_system.lib.claim_verifier.verify_nerode_claim(nerode_output, oracle, alphabet) -> dict` (instantiates 3 pairs). | `ir`, `pairs: [[u, v], ...]` or a claimed family plus suffix → the distinguishing suffix per pair | \|Σ\|^max_len suffixes per pair: bound with `check_enumeration`; thread timeout 20 s | VERDICT_POLICY §4 reg/nerode: a pair with a found suffix, or a claimed suffix confirmed by the oracle, is `bounded_pass` (bounds: pairs checked, `max_len`). A claimed suffix that does not separate the pair is `refuted`. "No suffix up to L" is `not_verified` (not an equivalence proof). Finitely many pairs never prove an infinite index. `likely_infinite` / `estimate_index` are heuristics: `well_formed` at most. |
| `dfa_vs_ir` | `agent_system.lib.oracle_test.oracle_test(oracle, dfa, alphabet, strategies=None, max_exhaustive=8) -> {"status": "pass" \| "fail", "tested", "passed", "counterexample"}`; DFA against DFA: `differential_test(dfa1, dfa2, alphabet, max_len=10) -> {"status": "identical" \| "diverge", ...}` | `ir`, `dfa` → status and counterexample | exhaustive ≤ 8 (\|Σ\|=2: 511 words) | R6: `pass` → `bounded_pass` (bounds: `max_len`, `n_words` = tested); `fail` with a counterexample → `refuted`. `differential_test` "identical" → `bounded_pass`. |
| `pump_check_reg` **(added; not implemented, deferred)** | `agent_system.lib.claim_verifier.verify_pumping_claim(pumping_output, oracle, alphabet) -> dict`, `detect_pattern_param(pattern)`, `instantiate_word_pattern(pattern, var, value)` | the prover's pumping JSON (`agent_system/prompts`, pumping contract) → per-p diagnostics | p ∈ {2,3,4}, all xyz with \|xy\| ≤ p | §4 reg/pumping: at most `well_formed`; `refuted` only for a concrete broken premise (for example z ∉ L). **Not registered in P1**: the reg group's spec did not include it and the fix round did not add it; the `pump_check_cfl` split mode with `cls="reg"` covers the single-split diagnostic meanwhile. |

## 4. CFL — `group="cfl"`, ceiling `verified`

| Tool | Backing functions | Input → output | Cost / limits | Trust |
|---|---|---|---|---|
| `to_cnf` | `cfl_system.lib.cnf.to_cnf(grammar: dict) -> dict` (raises `ValueError` on an invalid grammar), `is_cnf(grammar) -> bool` | grammar → CNF grammar | polynomial; `check_grammar_size` | `well_formed` (a construction by trusted code, not evidence about the task language). A submitted grammar with undeclared / overlapping symbols or an undeclared start → `ok`, `refuted`, `data = {valid: false, validation_errors, grammar_sha256, evidence_role: "invalid_artifact"}` (VERDICT_POLICY §1: invalid grammar symbols are `refuted`; the same rule in `cyk`, `parikh` grammar mode, `pda_run`, `dpda_run`, the ll group). Size limits and schema errors stay `invalid_input`. |
| `cyk` | `cfl_system.lib.cyk.cyk_parse(grammar_cnf, word) -> bool` (the grammar **must** be CNF: call `to_cnf` first), `cyk_parse_table(grammar_cnf, word) -> list[list[set[str]]]`. Ready-made oracle: `cfl_system.lib.cfl_oracle.grammar_oracle(grammar) -> Callable[[str], bool]` (converts once). | `grammar`, `words` → membership per word (+ optionally a table for one short word) | O(n³·\|G\|); n ≤ 100 | `verified` for "w ∈ L(G) / w ∉ L(G)" for **this** grammar |
| `grammar_vs_ir` **(added)** | `cfl_system.lib.cfl_oracle_test.oracle_test_grammar(grammar, ir, max_words=100, max_length=30) -> {"status": "pass" \| "grammar_incorrect" \| "error", "positive_checked", "positive_passed", "negative_checked", "negative_passed", "boundary_checked", "boundary_passed", "counterexamples", "details"}`; PDA: `oracle_test_pda(pda, ir, ...)` | constructed grammar or PDA and the IR → status and counterexamples | 100 words × CYK; process timeout 60 s | **Implemented (grammar only; the PDA variant is deferred).** It does not call `oracle_test_grammar`: it runs its own CYK loop against `words.oracle_for(cls, ir)` through `words.compare_with_oracle` — every word up to `max_len_within(|Σ|, 3000, 10)` plus generator words up to `sample_max_len` (≤ 30), stopping at 5 counterexamples or the time budget. Exact oracle and a counterexample → `refuted`; an invalid grammar → `refuted` (as `to_cnf`); no decided word → `not_verified`; an approximate oracle or fewer than 30 decided words → `well_formed`; otherwise `bounded_pass` (bounds: `max_len`, `n_words`, `n_unknown`, `exhaustive_up_to`, `truncated`). `data.evidence_role = "artifact_check"`. |
| `input_grammar_cfg` **(added)** | `cfl_system.lib.claim_verifier.input_grammar_is_cfg(ir) -> bool` | IR → `{"certified": bool}` | ms | R2-CFG: `True` → `verified` (the language is CFL by definition; this does **not** apply to `grammar_filter`); `False` → `not_verified`. **Implemented** (`max_trust="verified"`, `allow_refuted=False`); `not_verified` reasons separate `grammar_filter`, an invalid grammar and other kinds. |
| `pda_run` | `cfl_system.lib.pda_simulator.validate_pda(pda) -> list[str]`, `pda_accepts(pda, word) -> bool`, `pda_accepts_detailed(pda, word) -> {"accepted", "steps_used", "max_stack_depth", "accepting_config", "configs_explored"}` (BFS, `MAX_STEPS = 10000`, `MAX_STACK_DEPTH = 1000`; `TimeoutError` / `ValueError`). Prompt-contract PDA: first `cfl_oracle_test.normalize_agent_pda(pda, acceptance_mode)`. | `pda`, `words` → accepted per word | ≤ 10000 steps per word | accepted → `verified` (an accepting run exists). Rejected → `bounded_pass` (bounds: `max_steps`, `max_stack_depth`), because the BFS is cut at the stack-depth bound. `TimeoutError` → failure `timeout`. |
| `parikh` | `cfl_system.lib.parikh.parikh_vector(word, alphabet) -> tuple`, `parikh_image_from_grammar(grammar, max_length=15) -> set[tuple]`, `check_semilinearity(vectors, alphabet_size) -> dict`, `analyze_parikh(ir) -> dict` | `ir` or `grammar` (+ `max_length` ≤ 15) → vector sample and diagnostics | generation up to `max_length`; process timeout | §4 cfl/parikh: a finite sample is always semilinear; `looks_semilinear` is a heuristic. At most `well_formed`; never a basis for a CFL / non-CFL verdict. |
| `pump_check_cfl` | `cfl_system.lib.claim_verifier.verify_pumping_claim(evidence, ir, *, agent="pumping_cfl" \| "ogden", oracle=None) -> {"agent", "verification_status", "trust", "checks_passed", "checks_total", "issues", "details": {"destructive_witnesses", "bounded_check", ...}}`. One concrete split u·vⁱ·w·xⁱ·y: no lib function; build the words in the wrapper and use the §0 oracle. | the prover's pumping or Ogden JSON (with `word_instances` for p ∈ {3,4}), or one explicit split plus `i` values | splits with \|vwx\| ≤ p, i ∈ {0,2,3}; thread timeout 30 s | **at most `well_formed`** (§4 cfl/pumping: closing every finite check proves nothing about all p; a surviving split is not a refutation). `refuted` when z ∉ L or when a word the proof declares ∉ L is in L. Use the lib's own `trust` field, then clamp. The architecture table's "bounded_pass" for CFL is overruled by the policy (PLAN non-negotiable 6). For a single explicit split: membership facts as in `check_membership`, via the shared `words.oracle_for` (optional `cls`; auto order cfl → dcfl → ll → reg; the natural-language-filter oracle is refused). **A single split is only a diagnostic** (`data.evidence_role = "diagnostic"`): at most `not_verified`, `refuted` only for z ∉ L or a confirmed exit refuted by an exact oracle; an approximate oracle never refutes; "no pumped word decided" is reported as such (fix round F1, F4, F6). |
| `stratification` **(optional)** | `cfl_system.lib.stratification.is_bounded_language(ir) -> dict`, `check_stratification(grammar, bounding_words, max_length=20) -> dict` | | | diagnostics only: `well_formed` |

## 5. DCFL — `group="dcfl"`, ceiling `verified`

| Tool | Backing functions | Input → output | Cost / limits | Trust |
|---|---|---|---|---|
| `dpda_run` | `dcfl_system.lib.dpda.normalize_epsilon_accept_sinks(dpda) -> (dpda, notes)`, `check_determinism(dpda) -> list[str]`, `dpda_run(dpda, word) -> bool \| None` (`_MAX_STEPS = 10000`, `None` = step budget exhausted), `dpda_accepts(dpda, word) -> bool` (raises `TimeoutError`), `to_cfl_pda(dpda) -> dict` (structural check only, raises `DPDAFormatError`). **Always normalize first** (R2'). | `dpda`, `words` → normalization notes, determinism conflicts, accepted per word | ms per word | determinism: no conflicts → `verified` for the property "deterministic" (a complete syntactic check); a conflict → `refuted`. Per-word runs: `verified` for this DPDA; `None` → unknown. |
| `dpda_vs_ir` **(added)** | `dcfl_system.lib.oracle_verifier._verify_stack_strategy_dpda(dpda, task_ir) -> (status, check, issue, details)`. Private, but it is the full R2' procedure (normalize, determinism, simulation against `build_membership_oracle_from_ir` on sampled words **and** all words up to the length where Σ\|Σ\|ⁱ ≤ 3000, k ≤ 10, at least 30 decisive words). Public path with the same result: `oracle_verifier.verify_agent_results({"stack_strategy": {"status": "success", "proof_sketch": {...}}}, ir)`, which also checks the other proof_sketch fields. If it is reimplemented, use `max_len_within(len(alphabet), 3000, 10)` from base.py (the same rule). | `dpda`, `ir` → status, conflicts, counterexample, normalization notes | ≤ 3000 + sampled words × ≤ 10000 steps; process timeout 60 s | R2': `bounded_pass` (≥ 30 decisive words) / `refuted` (a counterexample or nondeterminism) / `well_formed` (too few decisive words). **Implemented** without the private `_verify_stack_strategy_dpda`: the `dpda_run` preparation (normalize, determinism, structure) then `words.compare_with_oracle` against `words.oracle_for(cls, ir)` (default `cls="dcfl"`) over all words up to `max_len_within(|Σ|, 3000, 10)` plus generator words up to 10; the same `comparison_result` mapping as `grammar_vs_ir`. `data.evidence_role = "artifact_check"`. |
| `closure_lookup` | `dcfl_system.lib.closure_table.is_closed(operation) -> bool` (raises `KeyError`), `get_implication(op) -> str \| None`, `get_proof_direction(op)`, `closure_scan(ir) -> {"applicable_strategies", "complement_structure", "warnings"}`; table `CLOSURE_TABLE` | `operation` or `ir` → closure facts and strategies | ms | `not_verified` (reference; cite `docs/THEORY.md` §1.4 for the statement) |
| `pattern_match` | `dcfl_system.lib.pattern_db.match_patterns(ir) -> list[{"pattern", "score", "verdict", "method", "reason"}]`, `get_pattern(code)`, `list_patterns()` | `ir` → ranked patterns | ms | `not_verified` (a heuristic hint, not evidence) |
| `dcfl_claim_check` **(optional)** | `dcfl_system.lib.oracle_verifier.verify_agent_results(agent_results, task_ir) -> {agent: {"verification_status", "checks_run", "checks_passed", "checks_total", "issues"}}` for `dcfl_pumping` (Yu lemma instances), `shallit`, `prefix_continuation`, `closure_reduction`; `trust_rank` / `confidence_cap_for` | proof_sketch JSON per method | ≤ 600 / 3000 splits (§4) | §4 dcfl: Yu lemma at most `well_formed` (and `not_verified` without `word_instances`); shallit `bounded_pass` / `well_formed` / `refuted` as in the policy |

## 6. LL(k) — `group="ll"`, ceiling `verified`

| Tool | Backing functions | Input → output | Cost / limits | Trust |
|---|---|---|---|---|
| `first_follow` | `ll_system.lib.first_follow.compute_all(grammar, k) -> (nullable: set, first_k: dict[str, set], follow_k: dict[str, set])`; parts: `compute_nullable(g)`, `compute_first_k(g, k)`, `compute_follow_k(g, k, first_k, nullable)`, `director_set(g, A, rhs, k, first_k, follow_k, nullable)`; `ll_system.lib.utils.normalize_grammar(g)`, `validate_grammar_symbols(g) -> list[str]` | `grammar`, `k` (1..6) → NULLABLE, FIRST_k, FOLLOW_k (sets become sorted lists; `""` = ε) | worklist to a fixed point, exponential in k | `verified` (an exact computation for the given grammar) |
| `ll_table` | `ll_system.lib.ll_table_builder.check_ll_k(grammar, k, *, time_budget_s=None, max_tables=20000) -> {"is_ll_k": bool \| None, "is_strong_ll_k", "k", "first_sets", "follow_sets", "conflicts", "ll_conflicts", "parse_table", "test_complete"}`; `find_min_ll_k(grammar, max_k=10, *, time_budget_s=None) -> {"found", "k", "strong_k", "max_k_checked", "max_k_decided", "undetermined", "result_for_k", "certificate", "not_ll_any_k"}`; `build_parse_table(...)` | `grammar`, `k` or `max_k` (≤ 6) → table and conflicts | the full test can need exponentially many tables: always pass `time_budget_s` (≤ the wrapper timeout − 2 s). The budget does not cover the strong test, so add a thread timeout. | Grammar question (R5, R2 Format 3): `is_ll_k` True → `verified`; False → `refuted` (a conflict for this k); None → `not_verified` (budget). `find_min_ll_k`: found → `verified` for k; `certificate` (left recursion after useless symbols are removed) → `verified` for "this grammar is not LL(k) for any k"; not found without a certificate → `bounded_pass` with `bounds.max_k = max_k_checked` ("не LL(k) при k ≤ K", cap 0.85); `undetermined` → `not_verified`. This is never a statement about the **language**: left recursion ≠ not LL (`ll_system/CLAUDE.md`). |
| `ll_parse` | `ll_system.lib.ll_parser.parse_word(grammar, k, word) -> {"accepted", "word", "steps", "error"?}`, class `LLParser` | `grammar`, `k`, `word` → accepted plus the derivation steps | linear after the table is built | `verified` (an exact parse with an LL(k) table); a grammar that is not LL(k) → failure `invalid_input` (the lib returns `error: "Grammar is not LL(k)"`) |
| `grammar_transform` | `ll_system.lib.grammar_transforms.remove_useless_symbols(g)`, `is_left_recursive(g) -> bool`, `eliminate_left_recursion(g)` (raises `ValueError` on hidden nullable-prefix recursion; run `to_ll_normal_form` then), `left_factor(g)`, `eliminate_epsilon_rules(g)`, `eliminate_unit_rules(g)`, `to_ll_normal_form(g)` (ε → unit → left recursion → factoring); check: `is_grammar_equivalent_sample(g1, g2, max_len=8, alphabet=None) -> (all_equal, mismatches)` | `grammar`, `ops: [...]` → the transformed grammar plus the sample-equivalence result against the input | polynomial; the equivalence check enumerates up to `max_len` (≤ 10) | the transformed grammar is `well_formed`. Equivalence: no mismatch → `bounded_pass` (bounds: `max_len`); a CYK-confirmed mismatch → `refuted` (§4 ll/constructive: a matching sample is never `verified`). |
| `ll_claim_check` **(optional)** | `ll_system.lib.claim_verifier.verify_ll_claim(agent_result, ir)` and per method `verify_ll_grammar_claim`, `verify_substitution_claim`, `verify_grammar_transformation_claim`, `verify_marker_claim`, `verify_prefix_classes_claim`, `verify_essential_ambiguity_claim` (each `(proof_sketch, ir) -> dict`) | proof_sketch JSON | per method (§4 ll) | as in §4 ll: constructive `bounded_pass` / `refuted` (Format 3 + an LL(k) test → `verified`); substitution `bounded_pass`; prefix_classes at most `well_formed` without a dead-class certificate |

All four ll tools: a **submitted** `grammar` that fails `ll_system` validation (reserved `$`/`ε`, undeclared symbols, an undeclared start, overlapping sets) → `ok`, `refuted`, `data = {valid: false, validation_errors, grammar_source, grammar_id, evidence_role: "invalid_artifact"}` (VERDICT_POLICY §1, same rule as `dpda_run`). A grammar taken from `ir` stays `invalid_input`; a schema error (multi-character terminal) stays `invalid_input`.

## 7. Theory — `group="theory"`, ceiling `not_verified`

| Tool | Backing | Input → output | Trust |
|---|---|---|---|
| `list_theorems` | no lib function: parse `docs/THEORY.md` headings in the package. The marked sections at `752a0b5` are §1.1 (Yu's pumping lemma for DCFL), §1.2 (Shallit), §1.3 (continuation lemma), §1.5 (essential ambiguity), §3.1 (LL(k) vs strong LL(k)), §3.2 (properties of LL languages), §3.3 (how to prove "not LL(k) for any k"). §1.4 (DCFL closure) is marked "[источники и границы указаны отдельно]"; §2, §2.5 are "[проверено перебором]"; §0 (part II, REG), §1.10, §3.4 are "[доказано…]". | `cls` → ids, titles, status marks | `not_verified` (reference) |
| `get_theorem` | the same file; return the section text **verbatim** (no paraphrase, PLAN non-negotiable 5), with its status mark and line range | `id` → text | `not_verified` |

## 8. Lean — `group="lean"`, ceiling `verified` (P6)

| Tool | Backing functions | Input → output | Cost / limits | Trust |
|---|---|---|---|---|
| `lean_statement` | `agent_system.lib.lean_ir.render_statement_verbose(ir, direction) -> (LeanStatement \| None, reason \| None)` for `regular` / `non_regular`; `cfl_system.lib.lean_ir.render_statement_verbose` for `cfl` / `non_cfl`; `dcfl_system.lib.lean_ir.render_statement_verbose` for `dcfl` / `non_dcfl`. None of them raise; LL is not formalized. `LeanStatement` fields: `imports`, `alphabet_decl`, `language_decl`, `theorem_decl` (`theorem tfl_main : …`, no `:=`), `name` (`"tfl_main"`), `render(proof="sorry")`. | `ir`, `direction` → statement text plus `name` | ms | `well_formed` (code-generated statement; the model never writes it) |
| `lean_check` | `agent_system.lib.type_check.scan_proof_body(proof_body) -> list[str]` (lexical gate), `compose_lean_file(statement, proof_body) -> str`, `check_lean_file(text, timeout=300, theorem_name=stmt.name) -> {"status": "proved" \| "has_sorry" \| "error" \| "timeout" \| "unavailable", "errors", "warnings", "axioms", "elapsed", "message"}`, `is_docker_available() -> bool`; trust: `agent_system.lib.claim_verifier.compute_lean_proof_trust(formalization) -> {"trust", "direction", "status"}`. `ALLOWED_AXIOMS = {propext, Classical.choice, Quot.sound}`. | `ir`, `direction`, `proof_body` → status, errors, axioms. The statement is **re-rendered from the IR inside the tool**, never taken from the model. | Docker `tfl-lean4`, 30–300 s; thread timeout ≥ 320 s; skip when Docker is unavailable (see DEV_NOTES, daemon flakiness) | `proved` → `verified` (R-Lean); every other status → `not_verified` (R1: not evidence either way) |

## 9. Orchestration and report — no lib backing

| Group | Tools | Phase | Trust |
|---|---|---|---|
| `orchestration` | `spawn_subagent`, `run_workflow`, `budget_status` | P4 (`orchestration/`); these may register into `REGISTRY` with `group="orchestration"` or stay plain LangChain tools | `not_verified` |
| `report` | `list_report_templates`, `submit_report` | P3 (`tools/report.py`); `submit_report` is the HITL 2 interrupt | `not_verified` (the evidence block and trust labels come from the ledger, not from the tool) |

## 10. Notes for the group implementers

- **Fix-round status (review findings).** The dispatch exists as `words.oracle_for(cls, ir) -> ClassOracle`
  (`.query(w) -> (True | False | None, note)`, `.exact`, `.source`, `.describe()`); `check_membership`,
  `sample_words`, `pump_check_cfl`, `grammar_vs_ir` and `dpda_vs_ir` all use it, so a membership fact is the
  same in every tool. The artifact-vs-IR comparison is one helper pair, `words.compare_with_oracle` +
  `words.comparison_result`. Evidence roles in `data.evidence_role`: `reference` (oracle-only labelling),
  `claim_check` (expected labels), `diagnostic` (one pumping split), `artifact_check` (artifact vs IR),
  `invalid_artifact` (a submitted artifact with invalid symbols, `refuted`), `input_grammar` (R2-CFG).
  A grammar embedded in the IR (ll `ir.grammar`) that fails validation stays `invalid_input`: the IR is the
  agent's own input, not an artifact under test.

- **Shared membership dispatch.** Put the class → oracle builder table of §0 into one helper (for example
  `tools/words.py:oracle_for(cls, ir)`), and use it from words, reg, cfl, dcfl and ll. That keeps the
  `bool` vs `bool | None` difference and the unsupported cases in one place.
- **Do not trust lib trust fields blindly.** Several verifiers return their own `trust` / `verification_status`
  (`cfl claim_verifier._make_result`, dcfl `verify_agent_results`, ll `verify_*_claim`). Map them through the
  tool's `max_trust`. Statuses such as `not_applicable` and `error` are `not_verified`.
- **Timeouts.** Functions with their own budget (cyk, pda_simulator, dpda_run, `check_ll_k(time_budget_s=…)`,
  the exponent-pattern oracle) are fine with `timeout_mode="thread"`. Pure searches without one (`regex_to_dfa`
  subset construction, `parikh_image_from_grammar`, `oracle_test_grammar`, the DPDA full enumeration) should use
  `timeout_mode="process"`. That needs module-level functions and picklable args, and costs about a second of
  start-up on Windows. Group loops should call `Deadline.check()`.
- **Sandbox code** (`execute`) is not a registry tool. Its output is capped at `well_formed` by P5 / P3, not
  here.
- The pipelines' "verified" for structure only is now `well_formed` everywhere (VERDICT_POLICY §1 rename). Do not
  copy older labels from `*_renderer.py`.
