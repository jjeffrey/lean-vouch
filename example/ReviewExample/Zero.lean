import ReviewExample.Divides

/-!
# Divisibility and zero

How zero behaves under `Divides`: every number divides zero (`Divides.zero`, in `Divides`), and
zero divides only zero.
-/

namespace ReviewExample

/-- Zero divides `n` exactly when `n` is zero. -/
theorem Divides.zero_left (n : Nat) : Divides 0 n ↔ n = 0 := by
  constructor
  · intro ⟨k, h⟩
    rw [Nat.zero_mul] at h
    exact h
  · intro h
    rw [h]
    exact Divides.zero 0

end ReviewExample
