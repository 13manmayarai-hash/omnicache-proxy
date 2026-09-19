"""
Unit and integration tests for Enterprise Policy & Compliance Audit Log Exporter.
Verifies immutable event logging, JSON/CSV/OpenTelemetry export formats,
tenant filtering, and gateway REST endpoints.
"""

import json
import csv
import io
import time
import pytest
from starlette.testclient import TestClient

from server.audit import AuditLogger, audit_logger
from server.gateway import app


@pytest.fixture
def client():
    return TestClient(app)


def test_audit_logger_lifecycle_and_ring_buffer(tmp_path):
    db_file = str(tmp_path / "test_audit.db")
    logger = AuditLogger(max_memory_records=10, db_path=db_file)

    # Initial seeding check
    summary = logger.get_summary()
    assert summary["total_audit_events"] >= 5
    assert summary["pii_redactions"] >= 1

    # Log new event
    rec = logger.log_event(
        event_type="TEST_EVENT",
        tenant_id="org_test",
        severity="WARNING",
        actor="unit_test",
        description="Testing audit logger ring buffer",
        details={"key": "value"}
    )
    assert rec["event_type"] == "TEST_EVENT"
    assert rec["tenant_id"] == "org_test"
    assert rec["severity"] == "WARNING"

    # Ring buffer overflow test
    for i in range(15):
        logger.log_event(
            event_type=f"OVERFLOW_EVT_{i}",
            tenant_id="org_test",
            severity="INFO",
            actor="unit_test",
            description=f"Overflow record {i}"
        )

    # Memory ledger must not exceed max_memory_records
    events = logger.get_events(limit=50)
    assert len(events) == 10


def test_audit_logger_export_json():
    json_str = audit_logger.export_json(limit=10)
    data = json.loads(json_str)

    assert data["service"] == "OmniCache Enterprise AI Proxy"
    assert "version" in data
    assert "exported_at" in data
    assert "events" in data
    assert isinstance(data["events"], list)
    assert len(data["events"]) > 0

    first = data["events"][0]
    for key in ("event_id", "timestamp", "event_type", "tenant_id", "severity", "actor", "description", "details"):
        assert key in first


def test_audit_logger_export_csv():
    csv_str = audit_logger.export_csv(limit=10)
    reader = csv.DictReader(io.StringIO(csv_str))

    expected_fields = [
        "timestamp", "event_id", "event_type", "tenant_id",
        "severity", "actor", "description", "details_json"
    ]
    assert reader.fieldnames == expected_fields

    rows = list(reader)
    assert len(rows) > 0
    assert rows[0]["event_type"] != ""


def test_audit_logger_export_otel():
    otel_data = audit_logger.export_otel(limit=10)

    assert "resourceLogs" in otel_data
    assert len(otel_data["resourceLogs"]) > 0

    res_log = otel_data["resourceLogs"][0]
    assert "resource" in res_log
    assert "scopeLogs" in res_log
    assert len(res_log["scopeLogs"]) > 0

    scope_log = res_log["scopeLogs"][0]
    assert scope_log["scope"]["name"] == "omnicache.enterprise.audit"
    assert len(scope_log["logRecords"]) > 0

    rec = scope_log["logRecords"][0]
    assert "timeUnixNano" in rec
    assert "severityNumber" in rec
    assert "severityText" in rec
    assert "body" in rec
    assert "attributes" in rec

    attr_keys = [attr["key"] for attr in rec["attributes"]]
    assert "omnicache.event_id" in attr_keys
    assert "omnicache.event_type" in attr_keys


def test_audit_logger_filtering_and_summary():
    test_id = f"tenant_{int(time.time())}"
    audit_logger.log_event(
        event_type="PII_REDACTION",
        tenant_id=test_id,
        severity="WARNING",
        actor="compliance_bot",
        description="Scrubbed SSN"
    )

    # Filter by tenant
    tenant_events = audit_logger.get_events(tenant_id=test_id)
    assert len(tenant_events) == 1
    assert tenant_events[0]["tenant_id"] == test_id

    # Filter by event type
    pii_events = audit_logger.get_events(event_type="PII_REDACTION")
    assert all(e["event_type"] == "PII_REDACTION" for e in pii_events)

    # Summary with tenant filter
    summary = audit_logger.get_summary(tenant_id=test_id)
    assert summary["total_audit_events"] == 1
    assert summary["pii_redactions"] == 1


def test_gateway_audit_summary_endpoint(client):
    res = client.get("/v1/enterprise/audit/summary")
    assert res.status_code == 200
    data = res.json()
    assert "total_audit_events" in data
    assert "pii_redactions" in data
    assert "model_cascades" in data
    assert "quota_warnings" in data
    assert "severities" in data


def test_gateway_audit_events_get_and_post(client):
    # GET events
    res = client.get("/v1/enterprise/audit/events?limit=5")
    assert res.status_code == 200
    data = res.json()
    assert "events" in data
    assert "count" in data
    assert len(data["events"]) <= 5

    # POST new event
    payload = {
        "event_type": "ACCESS_CONTROL_CHECK",
        "tenant_id": "org_security",
        "severity": "INFO",
        "actor": "rbac_guard",
        "description": "Passed zero-trust identity token validation",
        "details": {"method": "mTLS"}
    }
    post_res = client.post("/v1/enterprise/audit/events", json=payload)
    assert post_res.status_code == 200
    res_data = post_res.json()
    assert res_data["status"] == "recorded"
    assert res_data["event"]["event_type"] == "ACCESS_CONTROL_CHECK"


def test_gateway_audit_export_formats(client):
    # JSON export
    res_json = client.get("/v1/enterprise/audit/export?format=json&limit=5")
    assert res_json.status_code == 200
    assert "application/json" in res_json.headers["content-type"]
    assert "attachment" in res_json.headers["content-disposition"]
    data = res_json.json()
    assert "events" in data

    # CSV export
    res_csv = client.get("/v1/enterprise/audit/export?format=csv&limit=5")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert "attachment" in res_csv.headers["content-disposition"]
    assert "timestamp,event_id,event_type" in res_csv.text

    # OpenTelemetry export
    res_otel = client.get("/v1/enterprise/audit/export?format=otel&limit=5")
    assert res_otel.status_code == 200
    assert "application/json" in res_otel.headers["content-type"]
    data_otel = res_otel.json()
    assert "resourceLogs" in data_otel


def test_gateway_cache_purge_generates_audit_record(client):
    # Trigger cache purge
    res = client.post("/v1/cache/purge?org_id=audit_test_org")
    assert res.status_code == 200

    # Verify audit record is logged
    events = audit_logger.get_events(limit=5, event_type="CACHE_PURGE")
    assert len(events) > 0
    assert events[0]["event_type"] == "CACHE_PURGE"
    assert "purged" in events[0]["description"].lower()
