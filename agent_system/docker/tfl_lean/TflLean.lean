/-
  TflLean -- minimal library used only to pin and pre-build the Mathlib
  and langlib submodules the TFL formalizer prompts depend on. See
  TflLean/Basic.lean (Mathlib.Computability.*) and TflLean/Langlib.lean
  (langlib's Regular/ContextFree/DeterministicContextFree classes), and
  re-exports TflLean/Lemmas.lean (reusable lemmas for R-Lean proof bodies,
  `TflLean.*`).
-/
import TflLean.Basic
import TflLean.Langlib
import TflLean.Lemmas
