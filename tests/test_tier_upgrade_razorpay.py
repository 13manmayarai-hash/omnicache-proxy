import pytest
import hmac
import hashlib
from starlette.testclient import TestClient
from server.gateway import app
from server.quotas import quota_manager, TIER_SPECS
from core.config import config


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_tenant_key():
    key_id = "omni_live_test_tier_upgrade_key"
    quota_manager.register_key(
        key_id=key_id,
        team_name="Test Upgrade Org",
        org_id="org_test_upgrade",
        monthly_budget_usd=5.0,
        rate_limit_rpm=30,
        role="tenant",
        tier="free"
    )
    return key_id


def test_get_workspace_tier_status(client, test_tenant_key):
    res = client.get(
        "/v1/workspace/tier",
        headers={"Authorization": f"Bearer {test_tenant_key}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["tier"] == "free"
    assert data["monthly_budget_usd"] == 5.0
    assert data["rate_limit_rpm"] == 30
    assert "available_tiers" in data
    assert "scale" in data["available_tiers"]


def test_sandbox_tier_upgrade(client, test_tenant_key):
    res = client.post(
        "/v1/workspace/upgrade",
        headers={"Authorization": f"Bearer {test_tenant_key}"},
        json={"requested_tier": "scale", "payment_method": "sandbox"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["tier"] == "scale"
    assert data["monthly_budget_usd"] == 500.0
    assert data["rate_limit_rpm"] == 300

    status_res = client.get(
        "/v1/workspace/tier",
        headers={"Authorization": f"Bearer {test_tenant_key}"}
    )
    assert status_res.status_code == 200
    assert status_res.json()["tier"] == "scale"
    assert status_res.json()["monthly_budget_usd"] == 500.0


def test_razorpay_create_order(client, test_tenant_key):
    res = client.post(
        "/v1/billing/razorpay/create-order",
        headers={"Authorization": f"Bearer {test_tenant_key}"},
        json={"tier": "scale"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "order_id" in data
    assert data["amount"] == TIER_SPECS["scale"]["price_inr_paise"]
    assert data["currency"] == "INR"


def test_razorpay_verify_payment_signature(client, test_tenant_key, monkeypatch):
    test_secret = "test_secret_razorpay_12345"
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", test_secret)

    order_id = "order_rzp_mock_123"
    payment_id = "pay_rzp_mock_456"
    valid_sig = hmac.new(
        test_secret.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    bad_res = client.post(
        "/v1/billing/razorpay/verify-payment",
        headers={"Authorization": f"Bearer {test_tenant_key}"},
        json={
            "requested_tier": "scale",
            "payment_method": "razorpay",
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": "invalid_signature_hex"
        }
    )
    assert bad_res.status_code == 400
    assert bad_res.json()["error"]["type"] == "payment_verification_error"

    good_res = client.post(
        "/v1/billing/razorpay/verify-payment",
        headers={"Authorization": f"Bearer {test_tenant_key}"},
        json={
            "requested_tier": "scale",
            "payment_method": "razorpay",
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": valid_sig
        }
    )
    assert good_res.status_code == 200
    assert good_res.json()["status"] == "success"
    assert good_res.json()["tier"] == "scale"
    assert good_res.json()["monthly_budget_usd"] == 500.0


def test_exhaustion_recovery_flow(client):
    key_id = "omni_live_exhaustion_recovery_test"
    quota_manager.register_key(
        key_id=key_id,
        team_name="Exhaustion Team",
        org_id="org_exhaust",
        monthly_budget_usd=1.0,
        rate_limit_rpm=30,
        role="tenant",
        tier="free"
    )
    quota_manager.record_spend(key_id, 2.0)

    allowed, reason, info = quota_manager.check_authorization(key_id)
    assert allowed is False
    assert "budget" in reason.lower()

    tier_res = client.get(
        "/v1/workspace/tier",
        headers={"Authorization": f"Bearer {key_id}"}
    )
    assert tier_res.status_code == 200
    assert tier_res.json()["is_exhausted"] is True

    upgrade_res = client.post(
        "/v1/workspace/upgrade",
        headers={"Authorization": f"Bearer {key_id}"},
        json={"requested_tier": "scale", "payment_method": "sandbox"}
    )
    assert upgrade_res.status_code == 200
    assert upgrade_res.json()["monthly_budget_usd"] == 500.0

    allowed_now, reason_now, _ = quota_manager.check_authorization(key_id)
    assert allowed_now is True
    assert reason_now == "authorized"
