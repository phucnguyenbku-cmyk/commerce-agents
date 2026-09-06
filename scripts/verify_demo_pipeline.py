# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Demo Pipeline Verification Script.

Tests and verifies that the FastAPI backend, storefront endpoints, and merchant
portal endpoints can boot and respond correctly without requiring an external browser.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "examples"))


def verify_retail_demo_pipeline() -> None:
    from fastapi.testclient import TestClient

    from retail.api.main import app

    print("\n=======================================================")
    print("  ACME Commerce Agents: Retail Demo Pipeline Verification")
    print("=======================================================\n")

    client = TestClient(app, base_url="http://localhost")

    # 1. Health check
    print("1. Checking API health...")
    res = client.get("/api/health")
    assert res.status_code == 200, f"Health check failed: {res.text}"
    print(f"   ✓ Health check status {res.status_code}: {res.json()}")

    # 2. Session creation
    print("2. Starting storefront session for 'user_demo'...")
    res = client.post("/api/session", json={"user_id": "user_demo"})
    assert res.status_code == 200, f"Session creation failed: {res.text}"
    session_data = res.json()
    session_id = session_data["session_id"]
    headers = {"X-Session-Id": session_id}
    print(f"   ✓ Session established: id={session_id}")

    # 3. Product catalog
    print("3. Querying products catalog...")
    res = client.get("/api/products", headers=headers)
    assert res.status_code == 200, f"Get products failed: {res.text}"
    catalog_data = res.json()
    products = catalog_data.get("products", [])
    assert len(products) > 0, "No products returned from catalog"
    sample_product_id = products[0]["product_id"]
    print(
        f"   ✓ Found {len(products)} products. Sample item: '{products[0]['title']}' ({sample_product_id})"
    )

    # 4. Product details
    print(f"4. Querying product details for '{sample_product_id}'...")
    res = client.get(f"/api/products/{sample_product_id}", headers=headers)
    assert res.status_code == 200, f"Get product details failed: {res.text}"
    print(f"   ✓ Product detail retrieved with price: ${res.json().get('price')}")

    # 5. Cart initial read
    print("5. Checking initial cart status...")
    res = client.get("/api/cart", headers=headers)
    assert res.status_code == 200, f"Get cart failed: {res.text}"
    print(f"   ✓ Cart response status {res.status_code}: {len(res.json().get('items', []))} items")

    # 6. Merchant Portal Session & Overview
    print("6. Starting Merchant Portal session & querying overview...")
    merchant_sess_res = client.post("/api/merchant/session")
    assert merchant_sess_res.status_code == 200, (
        f"Merchant session failed: {merchant_sess_res.text}"
    )
    merchant_session_id = merchant_sess_res.json()["session_id"]
    merchant_headers = {"X-Session-Id": merchant_session_id}

    res = client.get("/api/merchant/overview", headers=merchant_headers)
    assert res.status_code == 200, f"Merchant overview failed: {res.text}"
    overview = res.json()
    snapshot = overview.get("snapshot", {})
    print(
        f"   ✓ Merchant overview: sales=${snapshot.get('sales')}, orders={snapshot.get('orders')}"
    )

    # 7. Merchant Inventory Alerts & Listings
    print("7. Querying Merchant Portal alerts and listings...")
    res = client.get("/api/merchant/alerts", headers=merchant_headers)
    assert res.status_code == 200, f"Merchant alerts failed: {res.text}"
    alerts = res.json().get("alerts", [])
    print(f"   ✓ Retrieved {len(alerts)} inventory alert(s)")

    res = client.get("/api/merchant/listings", headers=merchant_headers)
    assert res.status_code == 200, f"Merchant listings failed: {res.text}"
    listings = res.json().get("listings", [])
    print(f"   ✓ Retrieved {len(listings)} catalog listing(s)")

    print("\n-------------------------------------------------------")
    print("  All demo pipeline endpoints verified successfully!")
    print("-------------------------------------------------------\n")


if __name__ == "__main__":
    verify_retail_demo_pipeline()
