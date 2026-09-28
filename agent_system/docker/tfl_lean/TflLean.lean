/-
  TflLean -- minimal library used only to pin and pre-build the Mathlib
  submodules the TFL formalizer prompts (agent_system/prompts/formalizer.md)
  and the LL(k) live-run examples (ll_system/examples/live_outputs/*.lean)
  import. Building this target during the Docker image build warms
  .olean files for these imports so that lake env lean type-checks
  generated proofs without recompiling Mathlib each time.
-/
import Mathlib.Computability.DFA
import Mathlib.Computability.RegularExpressions
