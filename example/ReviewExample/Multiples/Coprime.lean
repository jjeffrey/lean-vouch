import ReviewExample.Multiples.Lcm

/-!
# Coprime numbers

Numbers with no common factor. One theorem here uses the `sorry` in `Multiples.Lcm` and one doesn't.
-/

namespace ReviewExample

/-- Two numbers are coprime when their greatest common divisor is one. -/
def Coprime (a b : Nat) : Prop :=
  gcd a b = 1

/-- The least common multiple of coprime numbers is their product. -/
theorem Coprime.lcm_eq {a b : Nat} (h : Coprime a b) : lcm a b = a * b := by
  have key := gcd_mul_lcm a b
  rw [show gcd a b = 1 from h, Nat.one_mul] at key
  exact key

/-- A common divisor of coprime numbers divides one. -/
theorem Coprime.divides_one {a b d : Nat} (h : Coprime a b) (ha : Divides d a) (hb : Divides d b) :
    Divides d 1 := by
  have key := divides_gcd a b ha hb
  rwa [show gcd a b = 1 from h] at key

end ReviewExample
