"""
UI smoke tests — verifies the dashboard HTML is served correctly.

These tests use the same TestClient infrastructure as test_api.py.
They don't test JS behaviour (that would need a browser driver), but they
confirm the server route, content-type, and presence of key HTML landmarks
so a bad deploy can't silently serve a blank page or a 404.

How to run:
    pytest tests/test_ui.py -v
"""
import pytest

# client and reset_db fixtures come from conftest.py


# ---------------------------------------------------------------------------
# Root route serves the dashboard
# ---------------------------------------------------------------------------

class TestDashboardRoute:

    def test_root_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_root_returns_html(self, client):
        response = client.get("/")
        assert "text/html" in response.headers["content-type"]

    def test_html_has_title(self, client):
        response = client.get("/")
        assert "<title>Eligibility Dashboard</title>" in response.text

    def test_html_has_stat_cards(self, client):
        """Four stat card IDs must be present — they're filled by JS on load."""
        html = client.get("/").text
        assert 'id="stat-today"'   in html
        assert 'id="stat-pending"' in html
        assert 'id="stat-gaps"'    in html
        assert 'id="stat-failed"'  in html

    def test_html_has_appointments_table(self, client):
        assert 'id="appt-body"' in client.get("/").text

    def test_html_has_gaps_panel(self, client):
        assert 'id="gaps-body"' in client.get("/").text

    def test_html_has_resolve_modal(self, client):
        assert 'id="resolveModal"' in client.get("/").text

    def test_html_references_api_endpoints(self, client):
        """The JS must call the correct API paths."""
        html = client.get("/").text
        assert "/dashboard"     in html
        assert "/appointments"  in html
        assert "/gaps"          in html

    def test_html_has_refresh_button(self, client):
        assert "refreshAll()" in client.get("/").text

    def test_html_has_resolve_submit(self, client):
        assert "submitResolve()" in client.get("/").text


# ---------------------------------------------------------------------------
# Static file mount
# ---------------------------------------------------------------------------

class TestStaticMount:

    def test_static_index_accessible(self, client):
        """The file is also reachable via /static/index.html directly."""
        response = client.get("/static/index.html")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_static_404_for_missing_file(self, client):
        response = client.get("/static/does_not_exist.js")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# API still works alongside the UI routes
# ---------------------------------------------------------------------------

class TestApiAlongsideUi:

    def test_health_still_works(self, client):
        assert client.get("/health").status_code == 200

    def test_appointments_endpoint_still_works(self, client):
        assert client.get("/appointments").status_code == 200

    def test_dashboard_endpoint_still_works(self, client):
        assert client.get("/dashboard").status_code == 200

    def test_gaps_endpoint_still_works(self, client):
        assert client.get("/gaps").status_code == 200
