# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests verifying the SqliteStorefrontBackend and SqliteMerchantBackend implementations."""

import pytest
from custom_backend.sqlite_merchant_backend import SqliteMerchantBackend
from custom_backend.sqlite_storefront_backend import SqliteStorefrontBackend

from merchant_agent import ChangeStatus, MerchantSessionContext, PriceUpdateItem
from shopping_agent import SearchFilters, ShoppingSessionContext


@pytest.fixture
def storefront_backend() -> SqliteStorefrontBackend:
    backend = SqliteStorefrontBackend(":memory:")
    backend.seed_sample_data()
    return backend


@pytest.fixture
def merchant_backend() -> SqliteMerchantBackend:
    backend = SqliteMerchantBackend(":memory:")
    backend.seed_sample_data()
    return backend


@pytest.fixture
def shopping_session() -> ShoppingSessionContext:
    return ShoppingSessionContext(
        session_id="test_sess_001",
        user_id="user_demo",
    )


@pytest.fixture
def merchant_session() -> MerchantSessionContext:
    return MerchantSessionContext(
        session_id="merchant_sess_001",
        merchant_id="acme_corp",
        operator="op_test_lead",
    )


@pytest.mark.asyncio
async def test_storefront_catalog_and_details(
    storefront_backend: SqliteStorefrontBackend, shopping_session: ShoppingSessionContext
) -> None:
    # 1. Search products
    results = await storefront_backend.search_products(shopping_session, query="tent")
    assert len(results) >= 1
    assert "Tent" in results[0].title
    assert results[0].price == 199.99

    # 2. Search with filters
    filters = SearchFilters(max_price=100.0)
    cheap_items = await storefront_backend.search_products(
        shopping_session, query="stove", filters=filters
    )
    assert len(cheap_items) == 1
    assert cheap_items[0].product_id == "prod_stove_03"

    # 3. Product details
    details = await storefront_backend.get_product_details(shopping_session, "prod_tent_01")
    assert details is not None
    assert details.long_description is not None
    assert "Engineered for rugged trails" in details.long_description


@pytest.mark.asyncio
async def test_storefront_cart_lifecycle(
    storefront_backend: SqliteStorefrontBackend, shopping_session: ShoppingSessionContext
) -> None:
    # 1. Empty cart initially
    cart = await storefront_backend.get_cart(shopping_session)
    assert len(cart.items) == 0

    # 2. Add product to cart
    cart = await storefront_backend.add_to_cart(shopping_session, "prod_tent_01", quantity=1)
    assert len(cart.items) == 1
    assert cart.items[0].product_id == "prod_tent_01"
    assert cart.items[0].quantity == 1
    assert cart.subtotal == 199.99

    # 3. Add more quantity
    cart = await storefront_backend.add_to_cart(shopping_session, "prod_tent_01", quantity=2)
    assert cart.items[0].quantity == 3
    assert cart.subtotal == 599.97

    # 4. Update quantity
    cart = await storefront_backend.update_cart_item(shopping_session, "prod_tent_01", quantity=1)
    assert cart.items[0].quantity == 1
    assert cart.subtotal == 199.99

    # 5. Remove item
    cart = await storefront_backend.remove_from_cart(shopping_session, "prod_tent_01")
    assert len(cart.items) == 0


@pytest.mark.asyncio
async def test_storefront_orders_and_policies(
    storefront_backend: SqliteStorefrontBackend, shopping_session: ShoppingSessionContext
) -> None:
    # 1. User preferences
    prefs = await storefront_backend.get_preferences(shopping_session)
    assert prefs.user_id == "user_demo"
    assert prefs.display_name == "Alex Mercer"
    assert prefs.loyalty_tier == "Gold Explorer"

    # 2. Orders
    orders = await storefront_backend.get_orders(shopping_session)
    assert len(orders) == 1
    assert orders[0].order_id == "ord_9001"
    assert orders[0].status.value == "delivered"

    # 3. Single order lookup
    order = await storefront_backend.get_order(shopping_session, "ord_9001")
    assert order is not None
    assert order.total == 199.99

    # 4. Policy search
    policies = await storefront_backend.search_policies(shopping_session, "refund")
    assert len(policies) >= 1
    assert "30 days" in policies[0].content

    # 5. Checkout handoff
    cart = await storefront_backend.get_cart(shopping_session)
    handoffs = await storefront_backend.checkout_handoff(shopping_session, cart)
    assert len(handoffs) == 1
    assert "session_id=test_sess_001" in handoffs[0].url


@pytest.mark.asyncio
async def test_merchant_snapshot_and_metrics(
    merchant_backend: SqliteMerchantBackend, merchant_session: MerchantSessionContext
) -> None:
    # 1. Business snapshot
    snapshot = await merchant_backend.get_business_snapshot(merchant_session)
    assert snapshot.sales > 0
    assert snapshot.orders > 0
    assert snapshot.average_order_value > 0

    # 2. Query metrics series
    series = await merchant_backend.query_metrics(merchant_session, metric="sales")
    assert len(series.points) == 5
    assert series.points[0].value == 4250.0


@pytest.mark.asyncio
async def test_merchant_staged_changes_and_approval(
    merchant_backend: SqliteMerchantBackend, merchant_session: MerchantSessionContext
) -> None:
    # 1. Verify initial listing price
    listing = await merchant_backend.get_listing(merchant_session, "list_tent_01")
    assert listing is not None
    assert listing.price == 199.99

    # 2. Stage price change (proposal)
    change = await merchant_backend.stage_price_update(
        merchant_session,
        items=[PriceUpdateItem(listing_id="list_tent_01", new_price=219.99)],
        note="Increase price due to high seasonal demand",
    )
    assert change.status == ChangeStatus.STAGED
    assert len(change.items) == 1

    # Verify pending ledger
    pending = await merchant_backend.get_pending_changes(merchant_session)
    assert len(pending) == 1
    assert pending[0].change_id == change.change_id

    # Database price MUST still be unchanged before approval!
    listing_before_apply = await merchant_backend.get_listing(merchant_session, "list_tent_01")
    assert listing_before_apply is not None
    assert listing_before_apply.price == 199.99

    # 3. Apply change (Human Approval)
    applied = await merchant_backend.apply_change(merchant_session, change.change_id)
    assert applied.status == ChangeStatus.APPLIED
    assert applied.applied_by == "op_test_lead"

    # Database price is now updated
    listing_after_apply = await merchant_backend.get_listing(merchant_session, "list_tent_01")
    assert listing_after_apply is not None
    assert listing_after_apply.price == 219.99


@pytest.mark.asyncio
async def test_merchant_analysis_sql_query(
    merchant_backend: SqliteMerchantBackend, merchant_session: MerchantSessionContext
) -> None:
    table = await merchant_backend.execute_analysis_query(
        merchant_session, "SELECT listing_id, price FROM listings ORDER BY price DESC"
    )
    assert table is not None
    assert table.row_count >= 3
    assert table.columns == ["listing_id", "price"]

    # Refuse non-SELECT queries
    with pytest.raises(ValueError, match="Only read-only SELECT"):
        await merchant_backend.execute_analysis_query(merchant_session, "DELETE FROM listings")
