import ReviewExample.Gcd

/-!
# Least common multiple

The least common multiple from the greatest common divisor. The key fact, `gcd_mul_lcm`, is left
as a `sorry`, so this module shows how lean-vouch traces one: to what contains it, and to
everything that relies on it, even through proofs.
-/

namespace ReviewExample

/-- The least common multiple: the product, divided by what the two numbers share. -/
def lcm (a b : Nat) : Nat :=
  a * b / gcd a b

/-- The greatest common divisor times the least common multiple is the product. Not proved yet. -/
theorem gcd_mul_lcm (a b : Nat) : gcd a b * lcm a b = a * b := by
  sorry

/-- `gcd_mul_lcm`, with the factors the other way around. -/
theorem lcm_mul_gcd (a b : Nat) : lcm a b * gcd a b = a * b := by
  rw [Nat.mul_comm]
  exact gcd_mul_lcm a b

/-- The greatest common divisor divides the product. -/
theorem gcd_divides_mul (a b : Nat) : Divides (gcd a b) (a * b) :=
  ⟨lcm a b, (gcd_mul_lcm a b).symm⟩

/-- The same, with the product the other way around. -/
theorem gcd_divides_mul_comm (a b : Nat) : Divides (gcd a b) (b * a) := by
  rw [Nat.mul_comm]
  exact gcd_divides_mul a b

end ReviewExample
