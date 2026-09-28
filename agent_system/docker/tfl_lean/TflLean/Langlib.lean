/-
  TflLean.Langlib -- the langlib modules the CFL/DCFL formalizer prompts
  depend on: regular/context-free/deterministic-context-free class
  definitions, Ogden's lemma and the CF pumping lemma, DCFL closure under
  complement/intersection-with-regular, and worked examples (a^n b^n c^n
  is context-free but not regular, a^n b^n is regular). Building this
  target warms .olean files for these so `lake env lean` on a generated
  proof body doesn't recompile langlib (or Mathlib) each time.

  langlib is pinned by commit (not tag/branch) in ../lakefile.toml; see
  the README's Lean section for how to refresh the pin.
-/
import Langlib.Classes.Regular.Definition
import Langlib.Classes.ContextFree.Definition
import Langlib.Classes.ContextFree.Basics.Pumping
import Langlib.Classes.ContextFree.Basics.Ogden
import Langlib.Classes.DeterministicContextFree.Definition
import Langlib.Classes.DeterministicContextFree.Closure.Complement
import Langlib.Classes.DeterministicContextFree.Closure.IntersectionRegular
import Langlib.Classes.ContextFree.Examples.AnBnCn
import Langlib.Classes.Regular.Examples.AnBn
