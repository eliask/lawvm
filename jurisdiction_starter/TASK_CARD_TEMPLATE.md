# Task card: <short task title>

Use one task card per bounded implementation unit.

---

## 1. Task identity

- Jurisdiction:
- Phase:
- Owner:
- Reviewer:
- Depends on:
- Blocks:

---

## 2. Goal

State one narrow outcome.

Good:
- “Emit synthetic clause surface rows from structured amendment metadata.”
- “Parse current sections and subsections into IR.”

Bad:
- “Improve frontend quality.”
- “Handle edge cases.”

---

## 3. Inputs

List exact artifact(s) or source family(s).

- Input artifact:
- Source family:
- Example fixture(s):

---

## 4. Outputs

List exact artifact(s) the task must produce.

- Output artifact:
- Output schema / file:
- New adjudications allowed:
- Tests / report required:

---

## 5. Non-goals

State what this task must not attempt.

- Not in scope:
- Explicitly unsupported after this task:

---

## 6. Acceptance criteria

Concrete pass conditions.

- [ ] Artifact is serialized and inspectable
- [ ] Fixture(s) added
- [ ] Unsupported cases are typed, not hidden
- [ ] No doctrine change required
- [ ] Eval/report updated

---

## 7. Failure conditions

Merge must be rejected if:

- [ ] task widened beyond stated scope
- [ ] source honesty was weakened
- [ ] current surface was used as historical proof
- [ ] unsupported cases were silently treated as success
- [ ] adjudication ownership became ambiguous

---

## 8. Notes for implementer

Add any jurisdiction-specific caveats here.
