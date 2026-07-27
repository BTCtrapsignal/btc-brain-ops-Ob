"""
tests/test_evidence_lifecycle_api.py

Tests for REQ-B2-002: Evidence Lifecycle Read-Only Runtime
Verification API — the four new GET-only routes under
/engineering/evidence-lifecycle/*.

Follows the same test baseline as test_mirror.py: pytest + FastAPI
TestClient + isolated in-memory SQLite.

Covers, per REQ-B2-002 Required Tests:
  1. Evidence retrieval by evidence_id — success, 404
  2. Evidence retrieval by producer_id + observation_reference — success, 404
  3. Validation history — count, ordering, fields, multiple records
  4. Archival state — archived, not archived, Evidence not found
  5. Effective validation status reflected correctly
  6. Legacy regression — GET /engineering/evidence/{ref_id} unchanged
  7. Method restrictions — no POST/PATCH/PUT/DELETE on new paths
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from app.main import app
from app.database import get_session
from app.database.models import EngineeringEvidence
from app.services import evidence_lifecycle as el


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session

    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _mirror_payload(**overrides) -> dict:
    payload = {
        "source_system": "reflex",
        "observation_id": "obs-api-test-0001",
        "observed_at": "2026-07-22T12:00:00Z",
        "market": "BTCUSDT",
        "timeframe": "1H",
        "structural_state": "accumulation",
        "behavioural_state": "pressure_accumulating",
        "confidence": 0.6,
        "summary": "test",
        "runtime_context": {"note": "test"},
        "schema_version": "1.0",
    }
    payload.update(overrides)
    return payload


def _create_evidence_via_mirror(client: TestClient, **overrides) -> dict:
    """Creates a MirroredObservation via the real endpoint, which
    triggers Evidence creation through the existing adapter."""
    response = client.post("/engineering/mirror", json=_mirror_payload(**overrides))
    assert response.status_code == 201
    return response.json()


# ─────────────────────────────────────────────────────────────
# 1. Evidence retrieval by evidence_id
# ─────────────────────────────────────────────────────────────

def test_get_evidence_lifecycle_by_id_success(client: TestClient, session: Session):
    mirror = _create_evidence_via_mirror(client)
    evidence = el.get_evidence_by_producer_observation(
        mirror["source_system"], mirror["observation_id"], session
    )
    assert evidence is not None

    response = client.get(f"/engineering/evidence-lifecycle/{evidence.evidence_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["evidence_id"] == evidence.evidence_id
    assert body["producer_id"] == "reflex"
    assert body["observation_reference"] == mirror["observation_id"]
    assert "effective_validation_status" in body
    assert "is_archived" in body


def test_get_evidence_lifecycle_by_id_404(client: TestClient, session: Session):
    response = client.get("/engineering/evidence-lifecycle/does-not-exist")
    assert response.status_code == 404


# ─────────────────────────────────────────────────────────────
# 2. Evidence retrieval by producer_id + observation_reference
# ─────────────────────────────────────────────────────────────

def test_get_evidence_lifecycle_by_observation_success(client: TestClient, session: Session):
    _create_evidence_via_mirror(client, observation_id="obs-api-test-0002")

    response = client.get(
        "/engineering/evidence-lifecycle/by-observation/reflex/obs-api-test-0002"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["observation_reference"] == "obs-api-test-0002"
    assert body["producer_id"] == "reflex"


def test_get_evidence_lifecycle_by_observation_404(client: TestClient, session: Session):
    response = client.get(
        "/engineering/evidence-lifecycle/by-observation/reflex/does-not-exist"
    )
    assert response.status_code == 404


# ─────────────────────────────────────────────────────────────
# 3. Validation history
# ─────────────────────────────────────────────────────────────

def test_validation_history_reflects_creation(client: TestClient, session: Session):
    _create_evidence_via_mirror(client, observation_id="obs-api-test-0003")
    evidence = el.get_evidence_by_producer_observation("reflex", "obs-api-test-0003", session)

    response = client.get(f"/engineering/evidence-lifecycle/{evidence.evidence_id}/validations")
    assert response.status_code == 200
    records = response.json()
    assert len(records) >= 1
    for field in (
        "validation_record_id",
        "outcome",
        "evaluated_at",
        "checks_performed",
        "decision_reasons",
        "validator_identity",
    ):
        assert field in records[0]


def test_validation_history_multiple_records_oldest_first(client: TestClient, session: Session):
    _create_evidence_via_mirror(client, observation_id="obs-api-test-0004")
    evidence = el.get_evidence_by_producer_observation("reflex", "obs-api-test-0004", session)

    second = el.validate_evidence(
        evidence=evidence,
        observation_exists=True,
        observation_reference_valid=True,
        producer_id_present=True,
        timestamp_present=True,
        runtime_context_structurally_valid=True,
        runtime_context_associated=True,
        persisted_data_consistent=True,
        session=session,
    )
    session.commit()

    response = client.get(f"/engineering/evidence-lifecycle/{evidence.evidence_id}/validations")
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 2
    assert records[0]["evaluated_at"] <= records[1]["evaluated_at"]
    assert records[1]["validation_record_id"] == second.validation_record_id


def test_validation_history_404_when_evidence_missing(client: TestClient, session: Session):
    response = client.get("/engineering/evidence-lifecycle/does-not-exist/validations")
    assert response.status_code == 404


# ─────────────────────────────────────────────────────────────
# 4. Archival state
# ─────────────────────────────────────────────────────────────

def test_archival_state_not_archived(client: TestClient, session: Session):
    _create_evidence_via_mirror(client, observation_id="obs-api-test-0005")
    evidence = el.get_evidence_by_producer_observation("reflex", "obs-api-test-0005", session)

    response = client.get(f"/engineering/evidence-lifecycle/{evidence.evidence_id}/archival")
    assert response.status_code == 200
    body = response.json()
    assert body["archived"] is False


def test_archival_state_archived(client: TestClient, session: Session):
    _create_evidence_via_mirror(client, observation_id="obs-api-test-0006")
    evidence = el.get_evidence_by_producer_observation("reflex", "obs-api-test-0006", session)
    el.archive_evidence(evidence.evidence_id, "test reason", session)

    response = client.get(f"/engineering/evidence-lifecycle/{evidence.evidence_id}/archival")
    assert response.status_code == 200
    body = response.json()
    assert body["archived"] is True
    assert body["reason"] == "test reason"
    assert body["archived_at"] is not None


def test_archival_state_404_when_evidence_missing(client: TestClient, session: Session):
    response = client.get("/engineering/evidence-lifecycle/does-not-exist/archival")
    assert response.status_code == 404


# ─────────────────────────────────────────────────────────────
# 5. Effective validation status reflected correctly
# ─────────────────────────────────────────────────────────────

def test_effective_validation_status_matches_service_layer(client: TestClient, session: Session):
    _create_evidence_via_mirror(client, observation_id="obs-api-test-0007")
    evidence = el.get_evidence_by_producer_observation("reflex", "obs-api-test-0007", session)
    expected = el.get_effective_validation_status(evidence.evidence_id, session)

    response = client.get(f"/engineering/evidence-lifecycle/{evidence.evidence_id}")
    assert response.status_code == 200
    assert response.json()["effective_validation_status"] == expected


# ─────────────────────────────────────────────────────────────
# 6. Legacy regression — GET /engineering/evidence/{ref_id} unchanged
# ─────────────────────────────────────────────────────────────

def test_legacy_evidence_endpoint_unchanged(client: TestClient, session: Session):
    """
    Confirms the Sprint B-1 EngineeringEvidence endpoint's behavior is
    untouched: querying by er_id (default ref_type) with a value that
    matches no EngineeringEvidence.er_id returns an empty list, exactly
    as before REQ-B2-002.
    """
    response = client.get("/engineering/evidence/W30-RUNTIME-TEST-001")
    assert response.status_code == 200
    assert response.json() == []


def test_legacy_evidence_endpoint_still_queries_engineering_evidence_table(
    client: TestClient, session: Session
):
    """The legacy endpoint must still query EngineeringEvidence, not the
    new Evidence table — confirms no accidental rewiring occurred."""
    ev = EngineeringEvidence(er_id="ER-TEST-001", evidence_type="positive")
    session.add(ev)
    session.commit()

    response = client.get("/engineering/evidence/ER-TEST-001")
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["er_id"] == "ER-TEST-001"


# ─────────────────────────────────────────────────────────────
# 7. Method restrictions — GET only on all new evidence-lifecycle paths
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    [
        "/engineering/evidence-lifecycle/some-id",
        "/engineering/evidence-lifecycle/by-observation/reflex/some-obs",
        "/engineering/evidence-lifecycle/some-id/validations",
        "/engineering/evidence-lifecycle/some-id/archival",
    ],
)
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_mutation_methods_registered(client: TestClient, path: str, method: str):
    """
    Every approved Evidence Lifecycle path is GET-only. An unsupported
    method must return exactly 405 Method Not Allowed — a route that IS
    registered (for GET) but rejects other methods. 404 is NOT an
    acceptable substitute: it would indicate the path wasn't registered
    at all, routing failed, route shadowing occurred, or some other
    configuration problem — all of which are failures to catch here,
    not passing conditions.
    """
    response = getattr(client, method)(path)
    assert response.status_code == 405, (
        f"{method.upper()} {path} returned {response.status_code}, expected 405. "
        f"A non-405 result may indicate the route is not registered as "
        f"expected — see test docstring."
    )
