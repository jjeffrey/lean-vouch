/-!
# Steps

A record of how many steps a computation took, as a small recursive type.
-/

namespace ReviewExample

/-- Either finished, or one more step and then the rest. -/
inductive Steps where
  | done
  | step (rest : Steps)

/-- How many steps are in a record. -/
def Steps.count : Steps → Nat
  | .done => 0
  | .step rest => rest.count + 1

end ReviewExample
