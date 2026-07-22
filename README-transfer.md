# Sprint B-1 — GitHub Transfer Package

REQ-W28-001 (Reflex Observation Mirroring Implementation), Brain Ops-side
implementation. Signed off by Engineering Authority.

**No test execution has been performed anywhere in this package's
preparation.** All contents are source only. Do not treat inclusion
in this package as evidence that tests pass.

---

## 1. Changed / New Files and Target Paths

| File in this package | Target path in repository | Status |
|---|---|---|
| `models.py` | `app/database/models.py` | Modified (additive — see `models.py.diff`) |
| `engineering.py` | `app/api/engineering.py` | Modified (full file only — no original was retained on disk to diff against; see note below) |
| `main.py` | `app/main.py` | Modified (additive — see `main.py.diff`) |
| `test_mirror.py` | `tests/test_mirror.py` | New file |
| `requirements-sprint-b1.txt` | repo root, or merge into existing dependency file if one exists | New file |
| `sprint-b1-tests.yml` | `.github/workflows/sprint-b1-tests.yml` | New file |

**Note on `engineering.py`:** unlike `models.py` and `main.py`, the
original pre-Sprint-B-1 version of this file was provided to me only
as message text earlier in this session and was not independently
saved to disk as a standalone upload. I reconstructed it from that
message content before making Sprint B-1 edits. I'm disclosing this
so the repository owner applies `engineering.py` as a full-file
replacement and diffs it locally against the actual current
repository file, rather than trusting a diff I generated against a
reconstruction.

---

## 2. Full List of Changed and Newly Created Files

**Modified:**
- `app/database/models.py`
- `app/api/engineering.py`
- `app/main.py`

**Newly created:**
- `tests/test_mirror.py`
- `requirements-sprint-b1.txt` (or merge into existing dependency file)
- `.github/workflows/sprint-b1-tests.yml`

---

## 3. Local Test Command

```bash
pip install -r requirements-sprint-b1.txt
pytest tests/test_mirror.py -v
```

## 4. CI Test Command

Identical command, run automatically by the included GitHub Actions
workflow (`sprint-b1-tests.yml`) on every push and pull request:

```bash
pytest tests/test_mirror.py -v
```

---

## 5. What Has NOT Been Verified

- The test suite has never been executed, in this sandbox or anywhere
  else. No `fastapi`, `sqlmodel`, or `pytest` were available in the
  implementation environment (no network access, no pre-installed
  packages, confirmed exhaustively during Sprint B-1).
- `requirements-sprint-b1.txt` lists unpinned minimum package names
  inferred from what the code imports — no existing dependency file
  was ever supplied to confirm actual version constraints used by the
  real repository.
- The GitHub Actions workflow assumes a repo-root-importable `app.*`
  package layout, matching every import statement seen throughout the
  supplied source, but this has not been confirmed against the actual
  repository structure.

Results should be accepted only from execution in the actual
repository/CI environment, per Engineering Authority's instruction.
