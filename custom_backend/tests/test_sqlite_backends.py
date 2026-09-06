# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests verifying the SqliteStorefrontBackend and SqliteMerchantBackend implementations."""

import sqlite3

import pytest
from custom_backend.sqlite_merchant_backend import SqliteMerchantBackend, _deny_writes
from custom_backend.sqlite_storefront_backend import SqliteStorefrontBackend

from merchant_agent import (
    ChangeNotApplicable,
    ChangeStatus,
    InventoryActionItem,
    MerchantSessionContext,
    PriceUpdateItem,
)
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


@pytest.mark.asyncio
async def test_merchant_analysis_accepts_a_common_table_expression(
    merchant_backend: SqliteMerchantBackend, merchant_session: MerchantSessionContext
) -> None:
    """check_analysis_sql opens the statement to WITH as well as SELECT, so a backend that
    only matched a 'select' prefix refused queries the runner had already allowed."""
    table = await merchant_backend.execute_analysis_query(
        merchant_session,
        "WITH cheap AS (SELECT * FROM listings WHERE price < 100) SELECT listing_id FROM cheap",
    )
    assert table is not None
    assert table.columns == ["listing_id"]
    assert table.rows == [["list_stove_03"]]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE listings SET price = 1",
        "SELECT 1; DROP TABLE listings",
        "SELECT * FROM listings -- and a comment",
        "ATTACH DATABASE '/tmp/other.db' AS other",
        "SELECT load_extension('payload.so')",
        "SELECT * FROM pragma_table_info('listings')",
    ],
)
@pytest.mark.asyncio
async def test_merchant_analysis_refuses_everything_that_is_not_a_plain_read(
    merchant_backend: SqliteMerchantBackend,
    merchant_session: MerchantSessionContext,
    sql: str,
) -> None:
    with pytest.raises(ValueError, match="Only read-only SELECT"):
        await merchant_backend.execute_analysis_query(merchant_session, sql)

    assert merchant_backend._conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 3


def test_merchant_analysis_authorizer_denies_a_write_the_keyword_check_never_sees() -> None:
    """The keyword check runs before the engine; the engine refuses the write regardless,
    which is what makes it a second line rather than a restatement of the first."""
    backend = SqliteMerchantBackend(":memory:")
    backend.seed_sample_data()

    backend._conn.set_authorizer(_deny_writes)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            backend._conn.execute("UPDATE listings SET price = 1")
        assert backend._conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 3
    finally:
        backend._conn.set_authorizer(None)

    # Clearing the authorizer leaves ordinary writes working.
    cursor = backend._conn.execute(
        "UPDATE listings SET stock = stock WHERE listing_id = 'list_tent_01'"
    )
    assert cursor.rowcount == 1


@pytest.mark.asyncio
async def test_merchant_pause_and_activate_move_the_listing_status(
    merchant_backend: SqliteMerchantBackend, merchant_session: MerchantSessionContext
) -> None:
    """A pause carries the status field, not a zero-unit stock move that writes nothing."""

    def status() -> str:
        return merchant_backend._conn.execute(
            "SELECT status FROM listings WHERE listing_id = 'list_tent_01'"
        ).fetchone()["status"]

    assert status() == "active"

    paused = await merchant_backend.stage_inventory_action(
        merchant_session,
        items=[InventoryActionItem(listing_id="list_tent_01", action="pause")],
    )
    assert paused.items[0].field == "status"
    assert paused.items[0].before == "active"
    assert paused.items[0].after == "paused"
    await merchant_backend.apply_change(merchant_session, paused.change_id)
    assert status() == "paused"

    live = await merchant_backend.stage_inventory_action(
        merchant_session,
        items=[InventoryActionItem(listing_id="list_tent_01", action="activate")],
    )
    await merchant_backend.apply_change(merchant_session, live.change_id)
    assert status() == "active"


@pytest.mark.asyncio
async def test_merchant_restock_stages_the_level_the_operator_will_see(
    merchant_backend: SqliteMerchantBackend, merchant_session: MerchantSessionContext
) -> None:
    """The staged diff reads from the current stock, and two restocks staged against the
    same starting level both count, because apply writes the staged delta."""

    def stock() -> int:
        return merchant_backend._conn.execute(
            "SELECT stock FROM listings WHERE listing_id = 'list_tent_01'"
        ).fetchone()["stock"]

    assert stock() == 14

    first = await merchant_backend.stage_inventory_action(
        merchant_session,
        items=[InventoryActionItem(listing_id="list_tent_01", action="restock", quantity=10)],
    )
    second = await merchant_backend.stage_inventory_action(
        merchant_session,
        items=[InventoryActionItem(listing_id="list_tent_01", action="restock", quantity=5)],
    )
    assert first.items[0].before == 14
    assert first.items[0].after == 24

    await merchant_backend.apply_change(merchant_session, first.change_id)
    await merchant_backend.apply_change(merchant_session, second.change_id)
    assert stock() == 29


@pytest.mark.asyncio
async def test_merchant_a_change_the_ledger_refuses_never_reaches_the_database(
    merchant_backend: SqliteMerchantBackend, merchant_session: MerchantSessionContext
) -> None:
    """The ledger decides before the write, so a second apply of an applied change cannot
    add the restock a second time."""

    def stock() -> int:
        return merchant_backend._conn.execute(
            "SELECT stock FROM listings WHERE listing_id = 'list_tent_01'"
        ).fetchone()["stock"]

    change = await merchant_backend.stage_inventory_action(
        merchant_session,
        items=[InventoryActionItem(listing_id="list_tent_01", action="restock", quantity=25)],
    )
    await merchant_backend.apply_change(merchant_session, change.change_id)
    assert stock() == 39

    with pytest.raises(ChangeNotApplicable):
        await merchant_backend.apply_change(merchant_session, change.change_id)
    assert stock() == 39


@pytest.mark.asyncio
async def test_merchant_inventory_action_on_an_unknown_listing_is_refused(
    merchant_backend: SqliteMerchantBackend, merchant_session: MerchantSessionContext
) -> None:
    with pytest.raises(ValueError, match="no listing"):
        await merchant_backend.stage_inventory_action(
            merchant_session,
            items=[InventoryActionItem(listing_id="list_absent", action="restock", quantity=1)],
        )
