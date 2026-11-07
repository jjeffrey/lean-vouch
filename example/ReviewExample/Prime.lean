import ReviewExample.Multiples.Coprime

/-!
# Primes

A definition of primes with a mistake Lean can't catch: it lets 1 count as prime. Everything here
compiles, and the theorem is proved, but about this definition. The
[walkthrough](https://github.com/jjeffrey/lean-vouch/blob/main/docs/usage.md) follows it from
noticing the mistake to fixing it.
-/

namespace ReviewExample

/-- `n` is prime: its only divisors are 1 and itself. -/
def Prime (n : Nat) : Prop :=
  ∀ d, Divides d n → d = 1 ∨ d = n

/-- A prime is coprime to every number it doesn't divide. -/
theorem Prime.coprime {p n : Nat} (hp : Prime p) (h : ¬ Divides p n) : Coprime p n := by
  rcases hp _ (gcd_divides p n).1 with h1 | hp'
  · exact h1
  · have := (gcd_divides p n).2
    rw [hp'] at this
    exact absurd this h

end ReviewExample
