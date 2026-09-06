# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Custom SQLite-backed implementation of MerchantBackend.

Demonstrates how to connect back-office operations (listings, metrics, inventory alerts,
campaigns, and human-approved staged changes) to a relational database.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from merchant_agent import (
    ActorKind,
    AlertCounts,
    AnalysisTable,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
    ChangeItem,
    ChangeKind,
    ChangeLedger,
    InventoryActionItem,
    InventoryAlert,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantAgentConfig,
    MerchantBackend,
    MerchantSessionContext,
    MetricPoint,
    MetricSeries,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)


class SqliteMerchantBackend(MerchantBackend):
    """An asynchronous, SQLite-backed MerchantBackend implementation with ChangeLedger."""

    def __init__(
        self, db_path: str = ":memory:", config: MerchantAgentConfig | None = None
    ) -> None:
        self.db_path = db_path
        self.config = config or MerchantAgentConfig()
        self.ledger = ChangeLedger(self.config)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    listing_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    price REAL NOT NULL,
                    currency TEXT DEFAULT 'USD',
                    stock INTEGER DEFAULT 0,
                    category TEXT,
                    content_quality TEXT DEFAULT 'good',
                    cost_price REAL DEFAULT 0.0,
                    attributes_json TEXT,
                    short_description TEXT,
                    long_description TEXT
                );

                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inventory_alerts (
                    alert_id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    current_stock INTEGER NOT NULL,
                    reorder_threshold INTEGER NOT NULL,
                    recommended_reorder INTEGER NOT NULL,
                    runway_days INTEGER,
                    daily_velocity REAL
                );

                CREATE TABLE IF NOT EXISTS order_issues (
                    issue_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    customer_id TEXT,
                    customer_name TEXT,
                    summary TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    start_date TEXT,
                    end_date TEXT,
                    budget REAL,
                    spend REAL,
                    revenue REAL,
                    roas REAL
                );
                """
            )

    def seed_sample_data(self) -> None:
        """Seed initial back-office data for metrics, listings, and alerts."""
        with self._conn:
            # Listings
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO listings (
                    listing_id, title, status, price, currency, stock, category, content_quality, cost_price, short_description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "list_tent_01",
                        "ACME Trailblazer 2-Person Tent",
                        "active",
                        199.99,
                        "USD",
                        14,
                        "Camping",
                        "good",
                        110.0,
                        "Ultralight dome tent.",
                    ),
                    (
                        "list_boots_02",
                        "ACME Alpine Explorer Hiking Boots",
                        "active",
                        149.50,
                        "USD",
                        6,
                        "Footwear",
                        "good",
                        80.0,
                        "Waterproof boots.",
                    ),
                    (
                        "list_stove_03",
                        "ACME Campfire Compact Gas Stove",
                        "active",
                        49.99,
                        "USD",
                        2,
                        "Cooking",
                        "needs_work",
                        22.0,
                        "Backpacking stove.",
                    ),
                ],
            )

            # Metrics
            self._conn.executemany(
                """
                INSERT INTO metrics (date, metric, value) VALUES (?, ?, ?)
                """,
                [
                    ("2026-09-01", "sales", 4250.0),
                    ("2026-09-02", "sales", 5120.0),
                    ("2026-09-03", "sales", 3980.0),
                    ("2026-09-04", "sales", 6100.0),
                    ("2026-09-05", "sales", 5800.0),
                    ("2026-09-01", "orders", 32.0),
                    ("2026-09-02", "orders", 38.0),
                    ("2026-09-03", "orders", 29.0),
                    ("2026-09-04", "orders", 45.0),
                    ("2026-09-05", "orders", 41.0),
                ],
            )

            # Inventory alerts
            self._conn.execute(
                """
                INSERT OR REPLACE INTO inventory_alerts (
                    alert_id, listing_id, kind, severity, current_stock, reorder_threshold, recommended_reorder, runway_days, daily_velocity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("alt_stove_low", "list_stove_03", "low_stock", "high", 2, 10, 25, 3, 0.8),
            )

            # Order issues
            self._conn.execute(
                """
                INSERT OR REPLACE INTO order_issues (
                    issue_id, order_id, kind, customer_id, customer_name, summary
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "iss_delay_101",
                    "ord_9001",
                    "carrier_delay",
                    "user_demo",
                    "Alex Mercer",
                    "Carrier delayed package due to severe weather in hub.",
                ),
            )

            # Campaigns
            self._conn.execute(
                """
                INSERT OR REPLACE INTO campaigns (
                    campaign_id, name, status, start_date, end_date, budget, spend, revenue, roas
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cmp_fall_outdoors",
                    "Fall Trekking Promo",
                    "active",
                    "2026-09-01",
                    "2026-09-30",
                    5000.0,
                    1200.0,
                    4800.0,
                    4.0,
                ),
            )

    # -- Performance Methods -------------------------------------------------------

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        cursor = self._conn.execute(
            "SELECT SUM(value) as total_sales FROM metrics WHERE metric = 'sales'"
        )
        sales_row = cursor.fetchone()
        total_sales = sales_row["total_sales"] or 0.0

        cursor = self._conn.execute(
            "SELECT SUM(value) as total_orders FROM metrics WHERE metric = 'orders'"
        )
        orders_row = cursor.fetchone()
        total_orders = int(orders_row["total_orders"] or 0)

        aov = round(total_sales / total_orders, 2) if total_orders > 0 else 0.0

        return BusinessSnapshot(
            period=period or "last_7d",
            compare_to="previous_period",
            sales=total_sales,
            orders=total_orders,
            traffic=2450,
            conversion_rate=3.2,
            average_order_value=aov,
            sales_change_pct=8.5,
            orders_change_pct=5.0,
            alert_counts=AlertCounts(
                low_stock=1,
                slow_movers=0,
                order_issues=1,
                pending_changes=len(self.ledger.pending()),
            ),
        )

    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        cursor = self._conn.execute(
            "SELECT date, value FROM metrics WHERE metric = ? ORDER BY date ASC",
            (metric,),
        )
        points = [MetricPoint(date=row["date"], value=row["value"]) for row in cursor.fetchall()]
        return MetricSeries(metric=metric, points=points)

    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        sql = "SELECT * FROM campaigns"
        params: list[Any] = []
        if campaign_id:
            sql += " WHERE campaign_id = ?"
            params.append(campaign_id)

        cursor = self._conn.execute(sql, params)
        return [
            Campaign(
                campaign_id=row["campaign_id"],
                name=row["name"],
                status=row["status"],
                start_date=row["start_date"],
                end_date=row["end_date"],
                budget=row["budget"],
                spend=row["spend"],
                revenue=row["revenue"],
                roas=row["roas"],
            )
            for row in cursor.fetchall()
        ]

    # -- Catalog & Listings --------------------------------------------------------

    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str,
        filters: ListingFilters | None = None,
        limit: int = 8,
    ) -> list[Listing]:
        query_pattern = f"%{query.strip().lower()}%" if query.strip() else "%"
        sql = """
            SELECT * FROM listings
            WHERE (LOWER(title) LIKE ? OR LOWER(category) LIKE ? OR LOWER(short_description) LIKE ?)
        """
        params: list[Any] = [query_pattern, query_pattern, query_pattern]

        if filters and filters.category:
            sql += " AND LOWER(category) = ?"
            params.append(filters.category.lower())

        sql += " LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(sql, params)
        return [
            Listing(
                listing_id=row["listing_id"],
                title=row["title"],
                status=row["status"],
                price=row["price"],
                currency=row["currency"] or "USD",
                stock=row["stock"],
                category=row["category"],
                content_quality=row["content_quality"],
                short_description=row["short_description"],
            )
            for row in cursor.fetchall()
        ]

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        cursor = self._conn.execute("SELECT * FROM listings WHERE listing_id = ?", (listing_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return ListingDetails(
            listing_id=row["listing_id"],
            title=row["title"],
            status=row["status"],
            price=row["price"],
            currency=row["currency"] or "USD",
            stock=row["stock"],
            category=row["category"],
            content_quality=row["content_quality"],
            short_description=row["short_description"],
            long_description=row["long_description"],
            sales_last_30d=45,
            return_rate_pct=1.5,
            variants=[],
        )

    # -- Inventory & Order Health --------------------------------------------------

    async def get_inventory_alerts(self, session: MerchantSessionContext) -> list[InventoryAlert]:
        cursor = self._conn.execute(
            """
            SELECT ia.*, l.title FROM inventory_alerts ia
            JOIN listings l ON ia.listing_id = l.listing_id
            """
        )
        return [
            InventoryAlert(
                alert_id=row["alert_id"],
                listing_id=row["listing_id"],
                title=row["title"],
                kind=row["kind"],
                severity=row["severity"],
                current_stock=row["current_stock"],
                reorder_threshold=row["reorder_threshold"],
                recommended_reorder=row["recommended_reorder"],
                runway_days=row["runway_days"],
                daily_velocity=row["daily_velocity"],
            )
            for row in cursor.fetchall()
        ]

    async def get_order_issues(self, session: MerchantSessionContext) -> list[OrderIssue]:
        cursor = self._conn.execute("SELECT * FROM order_issues")
        return [
            OrderIssue(
                issue_id=row["issue_id"],
                order_id=row["order_id"],
                kind=row["kind"],
                customer_id=row["customer_id"],
                customer_name=row["customer_name"],
                summary=row["summary"],
            )
            for row in cursor.fetchall()
        ]

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        cursor = self._conn.execute("SELECT * FROM listings WHERE listing_id = ?", (listing_id,))
        row = cursor.fetchone()
        if not row:
            return None
        price = row["price"]
        cost = row["cost_price"] or (price * 0.6)
        margin = round(((price - cost) / price) * 100, 1)
        return PricingContext(
            listing_id=listing_id,
            title=row["title"],
            current_price=price,
            currency=row["currency"] or "USD",
            cost_price=cost,
            current_margin_pct=margin,
            category=row["category"],
        )

    # -- Staged Writes & Guardrails -----------------------------------------------

    async def stage_listing_update(
        self,
        session: MerchantSessionContext,
        listing_id: str,
        fields: dict[str, Any],
        note: str | None = None,
    ) -> StagedChange:
        items = [
            ChangeItem(target=listing_id, field=k, before="", after=str(v))
            for k, v in fields.items()
        ]
        return self.ledger.stage(
            kind=ChangeKind.LISTING_UPDATE,
            items=items,
            summary=f"Update fields on {listing_id}: {', '.join(fields.keys())}",
            actor=session.operator,
        )

    async def stage_price_update(
        self,
        session: MerchantSessionContext,
        items: list[PriceUpdateItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items = []
        for p in items:
            cursor = self._conn.execute(
                "SELECT price FROM listings WHERE listing_id = ?", (p.listing_id,)
            )
            row = cursor.fetchone()
            before_price = row["price"] if row else 0.0
            change_items.append(
                ChangeItem(
                    target=p.listing_id, field="price", before=before_price, after=p.new_price
                )
            )
        return self.ledger.stage(
            kind=ChangeKind.PRICE_UPDATE,
            items=change_items,
            summary=f"Update prices for {len(items)} listing(s)",
            actor=session.operator,
        )

    async def stage_inventory_action(
        self,
        session: MerchantSessionContext,
        items: list[InventoryActionItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items = [
            ChangeItem(target=item.listing_id, field="stock", before=0, after=item.quantity or 0)
            for item in items
        ]
        return self.ledger.stage(
            kind=ChangeKind.INVENTORY_ACTION,
            items=change_items,
            summary=f"Inventory restock for {len(items)} item(s)",
            actor=session.operator,
        )

    async def stage_promotion(
        self, session: MerchantSessionContext, promotion: PromotionDraft
    ) -> StagedChange:
        items = [
            ChangeItem(
                target=promotion.name, field="discount_pct", before=0, after=promotion.discount_pct
            )
        ]
        return self.ledger.stage(
            kind=ChangeKind.PROMOTION,
            items=items,
            summary=f"Promotion: {promotion.name} ({promotion.discount_pct}% off)",
            actor=session.operator,
        )

    async def stage_campaign(
        self, session: MerchantSessionContext, campaign: CampaignDraft
    ) -> StagedChange:
        items = [
            ChangeItem(target=campaign.name, field="budget", before=0, after=campaign.budget or 0)
        ]
        return self.ledger.stage(
            kind=ChangeKind.CAMPAIGN,
            items=items,
            summary=f"Campaign: {campaign.name} (budget ${campaign.budget})",
            actor=session.operator,
        )

    async def get_pending_changes(self, session: MerchantSessionContext) -> list[StagedChange]:
        return self.ledger.pending()

    async def apply_change(self, session: MerchantSessionContext, change_id: str) -> StagedChange:
        change = self.ledger.get(change_id)
        if not change:
            raise ValueError(f"No change found with id {change_id}")

        # Perform actual database mutation
        with self._conn:
            for item in change.items:
                if item.field == "price":
                    self._conn.execute(
                        "UPDATE listings SET price = ? WHERE listing_id = ?",
                        (float(item.after), item.target),
                    )
                elif item.field == "stock":
                    self._conn.execute(
                        "UPDATE listings SET stock = stock + ? WHERE listing_id = ?",
                        (int(item.after), item.target),
                    )
                elif item.field in ("title", "short_description"):
                    self._conn.execute(
                        f"UPDATE listings SET {item.field} = ? WHERE listing_id = ?",
                        (str(item.after), item.target),
                    )

        return self.ledger.apply(change_id, actor=session.operator)

    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        return self.ledger.discard(change_id, actor=session.operator, actor_kind=actor_kind)

    # -- Analysis SQL Queries -----------------------------------------------------

    async def execute_analysis_query(
        self, session: MerchantSessionContext, sql: str
    ) -> AnalysisTable | None:
        normalized = sql.strip().lower()
        if not normalized.startswith("select"):
            raise ValueError("Only read-only SELECT statements are permitted.")
        cursor = self._conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = [[str(col) for col in row] for row in cursor.fetchall()]
        return AnalysisTable(columns=columns, rows=rows, row_count=len(rows))

    async def get_analysis_schema(self, session: MerchantSessionContext) -> str | None:
        return "TABLE listings (listing_id, title, price, stock, category);\nTABLE metrics (date, metric, value);"
