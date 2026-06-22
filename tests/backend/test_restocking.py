"""
Tests for restocking API endpoints.
"""
import pytest

from main import restocking_orders


@pytest.fixture(autouse=True)
def clear_restocking_orders():
    """Module-level list is shared across tests; clear it so each test
    starts from an empty submitted-orders state."""
    restocking_orders.clear()
    yield
    restocking_orders.clear()


class TestRestockingRecommendations:
    """Test suite for GET /api/restocking/recommendations."""

    def test_get_recommendations_structure(self, client):
        """Test that recommendations have all required fields."""
        response = client.get("/api/restocking/recommendations")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        for key in ("sku", "name", "trend", "current_demand", "forecasted_demand",
                    "suggested_quantity", "unit_cost", "line_cost",
                    "lead_time_days", "cost_source"):
            assert key in first

    def test_recommendations_field_types(self, client):
        """Test that recommendation fields have correct types and ranges."""
        response = client.get("/api/restocking/recommendations")
        data = response.json()

        for r in data:
            assert isinstance(r["suggested_quantity"], int)
            assert isinstance(r["unit_cost"], (int, float))
            assert isinstance(r["line_cost"], (int, float))
            assert isinstance(r["lead_time_days"], int)
            assert r["suggested_quantity"] > 0
            assert r["unit_cost"] > 0
            assert 3 <= r["lead_time_days"] <= 14
            assert r["cost_source"] in ("inventory", "fallback")
            assert r["trend"] in ("increasing", "stable", "decreasing")

    def test_recommendations_sorted_by_priority(self, client):
        """Test that recommendations are sorted increasing > stable > decreasing."""
        response = client.get("/api/restocking/recommendations")
        data = response.json()

        ranks = {"increasing": 0, "stable": 1, "decreasing": 2}
        seq = [ranks[r["trend"]] for r in data]
        assert seq == sorted(seq)

    def test_recommendations_lead_time_deterministic(self, client):
        """Test that lead times are stable across calls."""
        a = client.get("/api/restocking/recommendations").json()
        b = client.get("/api/restocking/recommendations").json()
        assert {r["sku"]: r["lead_time_days"] for r in a} == \
               {r["sku"]: r["lead_time_days"] for r in b}

    def test_recommendations_line_cost_calculation(self, client):
        """Test that line_cost = suggested_quantity * unit_cost."""
        response = client.get("/api/restocking/recommendations")
        data = response.json()

        for r in data:
            expected = round(r["suggested_quantity"] * r["unit_cost"], 2)
            assert abs(r["line_cost"] - expected) < 0.01


class TestRestockingOrders:
    """Test suite for POST/GET /api/restocking/orders."""

    def test_get_orders_initially_empty(self, client):
        """Test that restocking orders list starts empty."""
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_order_success(self, client):
        """Test creating a restocking order within budget."""
        payload = {
            "budget": 1000.0,
            "items": [
                {"sku": "WDG-001", "name": "Widget A", "quantity": 10, "unit_cost": 34.5}
            ]
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201

        body = response.json()
        assert body["order_number"].startswith("RSO-")
        assert body["status"] == "Submitted"
        assert abs(body["total_value"] - 345.0) < 0.01
        assert body["budget"] == 1000.0
        assert 3 <= body["lead_time_days"] <= 14
        assert "expected_delivery" in body
        assert len(body["items"]) == 1
        assert body["items"][0]["unit_price"] == 34.5

    def test_created_order_appears_in_list(self, client):
        """Test that a created order appears in GET /api/restocking/orders."""
        payload = {
            "budget": 500.0,
            "items": [{"sku": "X-1", "name": "X", "quantity": 5, "unit_cost": 10.0}]
        }
        created = client.post("/api/restocking/orders", json=payload).json()

        listed = client.get("/api/restocking/orders").json()
        assert any(o["id"] == created["id"] for o in listed)

    def test_create_order_rejects_empty_items(self, client):
        """Test that an order with no items is rejected."""
        response = client.post("/api/restocking/orders",
                               json={"budget": 100.0, "items": []})
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_create_order_rejects_over_budget(self, client):
        """Test that an order exceeding budget is rejected."""
        response = client.post("/api/restocking/orders", json={
            "budget": 10.0,
            "items": [{"sku": "X", "name": "x", "quantity": 100, "unit_cost": 50.0}]
        })
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "budget" in data["detail"].lower()

    def test_create_order_lead_time_is_max_of_items(self, client):
        """Test that order lead_time_days = max(lead times of constituent items)."""
        recs = client.get("/api/restocking/recommendations").json()
        assert len(recs) >= 2
        two = recs[:2]
        expected_lead = max(r["lead_time_days"] for r in two)

        payload = {
            "budget": 1_000_000.0,
            "items": [
                {"sku": r["sku"], "name": r["name"], "quantity": 1, "unit_cost": r["unit_cost"]}
                for r in two
            ]
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201
        assert response.json()["lead_time_days"] == expected_lead
