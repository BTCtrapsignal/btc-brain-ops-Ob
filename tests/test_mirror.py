"""
tests/test_mirror.py

Brain Ops-side automated tests for POST /engineering/mirror and
GET /engineering/mirror[/{observation_id}] (REQ-W28-001, Sprint B-1).

No existing test framework convention was found anywhere in the
supplied Brain Ops source (confirmed in the Implementation Bootstrap
Report). pytest + FastAPI's TestClient + an in-memory SQLite database
is used here as the standard default for this stack (FastAPI +
SQLModel), in the absence of any existing convention to follow.

Covers, per REQ-W28-001 Section 4 Required Testing:
  - successful mirror (new record persistence)      -> AC-1, AC-2, AC-3
  - duplicate handling                               -> AC-3, AC-4
  - invalid payload rejection                        -> AC-6
  - schema validation (EC-W28-002)                   -> AC-6
  - repository retrieval                             -> AC-8
  - structured logging (presence, not content assertions
    on exact wording — EC-W28-005 event names checked)-> AC-7

Not covered here (require infrastructure not in scope for Sprint B-1
Brain Ops-side tests, or require Reflex-side evidence):
  - repository outage / timeout handling (FM-1, FM-2) — these describe
    Reflex-side behaviour when Brain Ops is unreachable, not testable
    from the Brain Ops side in isolation.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select
from sqlmodel.pool import StaticPool

from app.main import app
from app.database import get_session
from app.database.models import MirroredObservation


# ─────────────────────────────────────────────────────────────
# Test database fixture — isolated in-memory SQLite per test
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _valid_payload(**overrides) -> dict:
    """A minimal valid MirrorObservationRequest payload (ADR-W28-001 Section 4)."""
    payload = {
        "source_system": "reflex",
        "observation_id": "obs-0001",
        "observed_at": "2026-07-22T12:00:00Z",
        "market": "BTCUSDT",
        "timeframe": "1H",
        "structural_state": "accumulation",
        "behavioural_state": "pressure_accumulating",
        "confidence": 0.62,
        "summary": "Illustrative test summary.",
        "runtime_context": {"note": "test context"},
        "schema_version": "1.0",
    }
    payload.update(overrides)
    return payload


def _select_all_mirrored(session: Session):
    return session.exec(select(MirroredObservation)).all()


# ─────────────────────────────────────────────────────────────
# AC-1, AC-2, AC-3: successful mirror, field preservation, one record
# ─────────────────────────────────────────────────────────────

def test_successful_mirror_creates_record(client: TestClient, session: Session):
    response = client.post("/engineering/mirror", json=_valid_payload())
    assert response.status_code == 201

    body = response.json()
    assert body["source_system"] == "reflex"
    assert body["observation_id"] == "obs-0001"
    assert body["structural_state"] == "accumulation"
    assert body["behavioural_state"] == "pressure_accumulating"
    assert body["confidence"] == 0.62
    assert body["schema_version"] == "1.0"

    assert len(_select_all_mirrored(session)) == 1


# ─────────────────────────────────────────────────────────────
# AC-3, AC-4: duplicate handling (EC-W28-003)
# ─────────────────────────────────────────────────────────────

def test_duplicate_request_returns_existing_with_200(client: TestClient, session: Session):
    first = client.post("/engineering/mirror", json=_valid_payload())
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = client.post("/engineering/mirror", json=_valid_payload())
    assert second.status_code == 200
    assert second.json()["id"] == first_id

    assert len(_select_all_mirrored(session)) == 1  # no duplicate record created


def test_duplicate_key_is_source_system_plus_observation_id(client: TestClient, session: Session):
    """Same observation_id but different source_system is NOT a duplicate."""
    client.post("/engineering/mirror", json=_valid_payload(source_system="reflex"))
    response = client.post(
        "/engineering/mirror",
        json=_valid_payload(source_system="reflex_v2"),
    )
    assert response.status_code == 201  # treated as a new record

    assert len(_select_all_mirrored(session)) == 2


# ─────────────────────────────────────────────────────────────
# AC-6: invalid payload rejection (missing/wrong-typed fields)
# ─────────────────────────────────────────────────────────────

def test_missing_required_field_rejected(client: TestClient, session: Session):
    payload = _valid_payload()
    del payload["confidence"]
    response = client.post("/engineering/mirror", json=payload)
    assert response.status_code == 422

    assert len(_select_all_mirrored(session)) == 0  # no partial record created


def test_wrong_type_confidence_rejected(client: TestClient, session: Session):
    response = client.post(
        "/engineering/mirror",
        json=_valid_payload(confidence="high"),  # not a float
    )
    assert response.status_code == 422

    assert len(_select_all_mirrored(session)) == 0


def test_wrong_type_runtime_context_rejected(client: TestClient, session: Session):
    response = client.post(
        "/engineering/mirror",
        json=_valid_payload(runtime_context="not a dict"),
    )
    assert response.status_code == 422


# ─────────────────────────────────────────────────────────────
# AC-6: schema_version validation (EC-W28-002)
# ─────────────────────────────────────────────────────────────

def test_unsupported_schema_version_rejected(client: TestClient, session: Session):
    response = client.post(
        "/engineering/mirror",
        json=_valid_payload(schema_version="2.0"),
    )
    assert response.status_code == 422
    assert "Unsupported schema_version" in response.json()["detail"]

    assert len(_select_all_mirrored(session)) == 0  # no record created for rejected schema version


def test_supported_schema_version_accepted(client: TestClient, session: Session):
    response = client.post(
        "/engineering/mirror",
        json=_valid_payload(schema_version="1.0"),
    )
    assert response.status_code == 201


# ─────────────────────────────────────────────────────────────
# AC-8: repository retrieval (EC-W28-006: composite identity route)
# ─────────────────────────────────────────────────────────────

def test_get_single_mirrored_observation_by_composite_identity(client: TestClient, session: Session):
    client.post("/engineering/mirror", json=_valid_payload())

    response = client.get("/engineering/mirror/reflex/obs-0001")
    assert response.status_code == 200
    assert response.json()["observation_id"] == "obs-0001"
    assert response.json()["source_system"] == "reflex"


def test_get_missing_composite_identity_returns_404(client: TestClient, session: Session):
    response = client.get("/engineering/mirror/reflex/does-not-exist")
    assert response.status_code == 404


def test_get_same_observation_id_different_source_system_returns_404(client: TestClient, session: Session):
    """EC-W28-006: identity is (source_system, observation_id) — no default,
    no inference. A mismatched source_system for an existing observation_id
    must NOT resolve to the record under a different source_system."""
    client.post("/engineering/mirror", json=_valid_payload(source_system="reflex"))

    response = client.get("/engineering/mirror/some_other_system/obs-0001")
    assert response.status_code == 404


def test_retrieval_route_requires_both_path_segments(client: TestClient, session: Session):
    """EC-W28-006: a single-segment path (the old, rejected default-source_system
    shape) must not resolve to the single-record retrieval endpoint at all —
    it should instead be handled by the list route or produce a 404/405,
    never silently defaulting source_system."""
    client.post("/engineering/mirror", json=_valid_payload())
    # /engineering/mirror/obs-0001 (single segment) must NOT match the
    # composite-identity route (which requires two segments); FastAPI's
    # routing will not match this pattern against {source_system}/{observation_id}.
    response = client.get("/engineering/mirror/obs-0001")
    assert response.status_code == 404  # not found as a route, not a defaulted retrieval


def test_list_mirrored_observations_with_filter(client: TestClient, session: Session):
    client.post("/engineering/mirror", json=_valid_payload(observation_id="obs-a", market="BTCUSDT"))
    client.post("/engineering/mirror", json=_valid_payload(observation_id="obs-b", market="ETHUSDT"))

    response = client.get("/engineering/mirror/?market=BTCUSDT")
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["observation_id"] == "obs-a"


# ─────────────────────────────────────────────────────────────
# AC-7: structured logging (presence / event name checks per
# EC-W28-005; not exact message-format assertions)
# ─────────────────────────────────────────────────────────────

def test_logs_request_received_and_persisted_events(client: TestClient, session: Session, caplog):
    with caplog.at_level(logging.INFO, logger="app.api.engineering"):
        client.post("/engineering/mirror", json=_valid_payload())

    messages = [record.message for record in caplog.records]
    assert any("engineering_mirror.request_received" in m for m in messages)
    assert any("engineering_mirror.persisted" in m for m in messages)


def test_logs_validation_failed_event_on_bad_schema_version(client: TestClient, session: Session, caplog):
    with caplog.at_level(logging.WARNING, logger="app.api.engineering"):
        client.post("/engineering/mirror", json=_valid_payload(schema_version="9.9"))

    messages = [record.message for record in caplog.records]
    assert any("engineering_mirror.validation_failed" in m for m in messages)


def test_logs_duplicate_returned_event(client: TestClient, session: Session, caplog):
    client.post("/engineering/mirror", json=_valid_payload())
    with caplog.at_level(logging.INFO, logger="app.api.engineering"):
        client.post("/engineering/mirror", json=_valid_payload())

    messages = [record.message for record in caplog.records]
    assert any("engineering_mirror.duplicate_returned" in m for m in messages)


def test_logging_does_not_include_full_runtime_context(client: TestClient, session: Session, caplog):
    """EC-W28-005 Logging Restrictions: do not log runtime_context content."""
    with caplog.at_level(logging.INFO, logger="app.api.engineering"):
        client.post(
            "/engineering/mirror",
            json=_valid_payload(runtime_context={"secret_marker": "SHOULD_NOT_APPEAR_IN_LOGS"}),
        )

    messages = [record.message for record in caplog.records]
    assert not any("SHOULD_NOT_APPEAR_IN_LOGS" in m for m in messages)


# ─────────────────────────────────────────────────────────────
# EC-W28-007: Pydantic/framework-level request validation failure
# logging, scoped to POST /engineering/mirror
# ─────────────────────────────────────────────────────────────

def test_missing_field_logs_validation_failed_and_preserves_422(client: TestClient, session: Session, caplog):
    payload = _valid_payload()
    del payload["confidence"]

    with caplog.at_level(logging.WARNING, logger="app.api.engineering"):
        response = client.post("/engineering/mirror", json=payload)

    # HTTP 422 semantics preserved exactly as FastAPI's default handler produces
    assert response.status_code == 422
    assert "detail" in response.json()

    messages = [record.message for record in caplog.records]
    assert any("engineering_mirror.validation_failed" in m for m in messages)
    # source_system/observation_id were present in the payload and should
    # be extracted even though confidence was the missing field
    assert any("source_system=reflex" in m for m in messages)
    assert any("observation_id=obs-0001" in m for m in messages)


def test_wrong_type_field_logs_validation_failed_and_preserves_422(client: TestClient, session: Session, caplog):
    with caplog.at_level(logging.WARNING, logger="app.api.engineering"):
        response = client.post(
            "/engineering/mirror",
            json=_valid_payload(confidence="not_a_number"),
        )

    assert response.status_code == 422
    assert "detail" in response.json()

    messages = [record.message for record in caplog.records]
    assert any("engineering_mirror.validation_failed" in m for m in messages)


def test_validation_failed_logging_does_not_include_runtime_context_content(
    client: TestClient, session: Session, caplog
):
    """EC-W28-007: failure_reason must not leak runtime_context/summary content."""
    payload = _valid_payload(runtime_context={"secret_marker": "SHOULD_NOT_LEAK"})
    del payload["confidence"]  # triggers a validation failure

    with caplog.at_level(logging.WARNING, logger="app.api.engineering"):
        client.post("/engineering/mirror", json=payload)

    messages = [record.message for record in caplog.records]
    assert not any("SHOULD_NOT_LEAK" in m for m in messages)


def test_malformed_request_uses_unavailable_when_fields_missing(client: TestClient, session: Session, caplog):
    """EC-W28-007: use 'unavailable' when fields cannot be safely obtained
    (e.g. body is not a dict at all, or fields are absent)."""
    with caplog.at_level(logging.WARNING, logger="app.api.engineering"):
        # Sending a JSON array instead of an object — exc.body will not be
        # a dict, so all three fields should fall back to "unavailable".
        response = client.post("/engineering/mirror", json=["not", "an", "object"])

    assert response.status_code == 422
    messages = [record.message for record in caplog.records]
    assert any("source_system=unavailable" in m for m in messages)


def test_retrieval_endpoint_validation_errors_not_logged_by_mirror_handler(
    client: TestClient, session: Session, caplog
):
    """Scoping check: the EC-W28-007 handler is scoped to POST /engineering/mirror
    only, per the scoping decision noted in the implementation. A validation
    error on a different route must not emit engineering_mirror.validation_failed."""
    with caplog.at_level(logging.WARNING, logger="app.api.engineering"):
        # limit expects an int; send a non-integer to trigger a query-param
        # validation error on the list endpoint instead of the mirror endpoint.
        response = client.get("/engineering/mirror/?limit=not_an_int")

    assert response.status_code == 422
    messages = [record.message for record in caplog.records]
    assert not any("engineering_mirror.validation_failed" in m for m in messages)
