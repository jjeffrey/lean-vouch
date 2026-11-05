import ReviewExample.Divides

/-!
# Greatest common divisor

Euclid's algorithm, and the two facts that make it the greatest common divisor: it divides both
numbers, and every common divisor divides it.
-/

namespace ReviewExample

/-- The greatest common divisor, by Euclid's algorithm: replace `(a, b)` by `(b % a, a)` until
`a` is zero. -/
def gcd : Nat → Nat → Nat
  | 0, b => b
  | a + 1, b => gcd (b % (a + 1)) (a + 1)
termination_by a => a
decreasing_by exact Nat.mod_lt _ (Nat.succ_pos _)

/-- `gcd a b` divides both `a` and `b`. -/
theorem gcd_divides (a b : Nat) : Divides (gcd a b) a ∧ Divides (gcd a b) b := by
  induction a, b using gcd.induct with
  | case1 b =>
    rw [gcd]
    exact ⟨Divides.zero b, Divides.refl b⟩
  | case2 a b ih =>
    rw [gcd]
    exact ⟨ih.2, Divides.of_mod ih.1 ih.2⟩

/-- The old name of `gcd_divides`, kept so older proofs still compile. -/
@[deprecated gcd_divides (since := "2026-11-03")]
theorem gcd_dvd (a b : Nat) : Divides (gcd a b) a ∧ Divides (gcd a b) b :=
  gcd_divides a b

/-- **Every common divisor of `a` and `b` divides `gcd a b`**, so it is the greatest. -/
theorem divides_gcd {d : Nat} (a b : Nat) (ha : Divides d a) (hb : Divides d b) :
    Divides d (gcd a b) := by
  induction a, b using gcd.induct with
  | case1 b =>
    rw [gcd]
    exact hb
  | case2 a b ih =>
    rw [gcd]
    exact ih (Divides.mod hb ha) ha

end ReviewExample
