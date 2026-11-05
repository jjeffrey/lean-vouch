/-!
# Divisibility

When one natural number divides another, defined directly, with the facts the greatest common
divisor needs.
-/

namespace ReviewExample

/-- `d` divides `n`: `n` is a whole multiple of `d`. -/
def Divides (d n : Nat) : Prop :=
  ∃ k, n = d * k

/-- Every number divides itself. -/
theorem Divides.refl (n : Nat) : Divides n n :=
  ⟨1, (Nat.mul_one n).symm⟩

/-- Every number divides zero. -/
theorem Divides.zero (d : Nat) : Divides d 0 :=
  ⟨0, (Nat.mul_zero d).symm⟩

/-- A common divisor of `n` and of `m % n` divides `m`. -/
theorem Divides.of_mod {d m n : Nat} (hmod : Divides d (m % n)) (hn : Divides d n) :
    Divides d m := by
  obtain ⟨x, hx⟩ := hmod
  obtain ⟨y, hy⟩ := hn
  refine ⟨x + y * (m / n), ?_⟩
  have h := Nat.mod_add_div m n
  generalize m / n = q at h ⊢
  rw [hx, hy, Nat.mul_assoc] at h
  rw [Nat.mul_add]
  exact h.symm

/-- A common divisor of `m` and `n` divides `m % n`. -/
theorem Divides.mod {d m n : Nat} (hm : Divides d m) (hn : Divides d n) : Divides d (m % n) := by
  obtain ⟨x, hx⟩ := hm
  obtain ⟨y, hy⟩ := hn
  refine ⟨x - y * (m / n), ?_⟩
  have h := Nat.mod_add_div m n
  rw [Nat.mul_sub, ← Nat.mul_assoc, ← hy, ← hx]
  omega

end ReviewExample
