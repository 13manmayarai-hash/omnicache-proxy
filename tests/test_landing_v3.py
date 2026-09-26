import pytest
from starlette.testclient import TestClient
from server.gateway import app

client = TestClient(app)

def test_landing_v3_served_on_landing_route():
    resp = client.get("/landing")
    assert resp.status_code == 200
    assert "Stop paying" in resp.text
    assert "twice" in resp.text
    assert "omnicache" in resp.text
    # Verify GitHub link with logo is present at extreme right
    assert 'class="btn btn-sm gh"' in resp.text
    assert 'https://github.com/13manmayarai-hash/omnicache-proxy' in resp.text
    # Verify Live Dashboard wayfinding links
    assert 'href="/dashboard"' in resp.text
    assert 'id="mobileNavToggle"' in resp.text
    assert 'id="mobileDrawer"' in resp.text

def test_landing_v3_served_on_v3_route():
    resp = client.get("/v3")
    assert resp.status_code == 200
    assert "Stop paying" in resp.text

def test_landing_v3_served_on_root_html():
    resp = client.get("/", headers={"accept": "text/html"})
    assert resp.status_code == 200
    assert "Stop paying" in resp.text

def test_docs_html_served():
    resp = client.get("/docs.html")
    assert resp.status_code == 200
    assert "OmniCache" in resp.text
    assert "docs" in resp.text
    assert 'href="/landing"' in resp.text
    assert 'Live Dashboard' in resp.text

def test_omnicache_og_png_served():
    resp = client.get("/omnicache-og.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert len(resp.content) > 1000

def test_img_assets_served():
    images = [
        "hero-1000.webp",
        "hero-1983.webp",
        "how-1536.webp",
        "how-768.webp",
        "impact-1536.webp",
        "impact-768.webp",
        "start-1536.webp",
        "start-768.webp",
    ]
    for img in images:
        resp = client.get(f"/img/{img}")
        assert resp.status_code == 200, f"Failed for {img}"
        assert resp.headers["content-type"] == "image/webp"
        assert len(resp.content) > 100

def test_landing_v3_heading_accents_and_kinetic_motion():
    resp = client.get("/landing")
    assert resp.status_code == 200
    # Verify color consistency: all 3 sub-phrases have .accent burnt-orange
    assert '<span class="accent">A fraction of the cost.</span>' in resp.text
    assert '<span class="accent">Under a millisecond.</span>' in resp.text
    assert '<span class="accent">Zero config.</span>' in resp.text
    # Verify living hero engine elements
    assert 'class="hero-glow-core"' in resp.text
    assert 'class="hero-beam-scanner"' in resp.text
    assert 'id="heroImg"' in resp.text
    # Verify parallax images
    assert 'class="parallax-img"' in resp.text
    assert 'updateParallax' in resp.text

def test_docs_html_design_consistency():
    resp = client.get("/docs.html")
    assert resp.status_code == 200
    assert "Plus Jakarta Sans" in resp.text
    assert "--neu-flat-1" in resp.text
    assert 'class="nav-dash"' in resp.text

def test_simulator_html_served_and_styled():
    resp = client.get("/simulator")
    assert resp.status_code == 200
    assert "Plus Jakarta Sans" in resp.text
    assert 'href="/landing"' in resp.text
    assert "Pipeline Replay Sandbox" in resp.text
