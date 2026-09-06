# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Custom SQLite-backed implementation of StorefrontBackend.

Demonstrates how to map catalog, cart, user profile, order history, and store policies
to a relational SQLite database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from shopping_agent import (
    Cart,
    CartItem,
    CheckoutHandoff,
    FulfillmentOption,
    Order,
    OrderItem,
    OrderStatus,
    Policy,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    Unavailable,
    UserPreferences,
)


class SqliteStorefrontBackend(StorefrontBackend):
    """An asynchronous, SQLite-backed StorefrontBackend implementation."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    product_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    brand TEXT,
                    price REAL NOT NULL,
                    currency TEXT DEFAULT 'USD',
                    rating REAL,
                    review_count INTEGER,
                    image_url TEXT,
                    category TEXT,
                    labels_json TEXT,
                    attributes_json TEXT,
                    in_stock INTEGER DEFAULT 1,
                    short_description TEXT,
                    long_description TEXT,
                    options_json TEXT,
                    option_values_json TEXT,
                    variant_of TEXT
                );

                CREATE TABLE IF NOT EXISTS carts (
                    session_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    image_url TEXT,
                    option_values_json TEXT,
                    variant_of TEXT,
                    PRIMARY KEY (session_id, product_id)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    placed_at TEXT NOT NULL,
                    total REAL NOT NULL,
                    currency TEXT DEFAULT 'USD',
                    items_json TEXT NOT NULL,
                    tracking_url TEXT
                );

                CREATE TABLE IF NOT EXISTS policies (
                    policy_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT,
                    content TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    loyalty_tier TEXT,
                    default_location TEXT,
                    preferences_json TEXT
                );
                """
            )

    def seed_sample_data(self) -> None:
        """Seed initial products, orders, and policies for testing."""
        with self._conn:
            # Products
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO products (
                    product_id, title, brand, price, currency, rating, review_count,
                    category, labels_json, attributes_json, in_stock, short_description, long_description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "prod_tent_01",
                        "ACME Trailblazer 2-Person Tent",
                        "ACME",
                        199.99,
                        "USD",
                        4.8,
                        124,
                        "Camping",
                        json.dumps(["bestseller", "waterproof"]),
                        json.dumps({"capacity": "2-person", "weight": "4.2 lbs"}),
                        1,
                        "Ultralight 2-person waterproof dome tent for backpacking.",
                        "Engineered for rugged trails, the Trailblazer features dual vestibules and weather-shield coating.",
                    ),
                    (
                        "prod_boots_02",
                        "ACME Alpine Explorer Hiking Boots",
                        "ACME",
                        149.50,
                        "USD",
                        4.6,
                        89,
                        "Footwear",
                        json.dumps(["waterproof", "vibram_sole"]),
                        json.dumps({"material": "leather", "gender": "unisex"}),
                        1,
                        "Durable waterproof hiking boots with all-terrain grip.",
                        "Breathable membrane paired with high-traction soles suitable for technical ascents.",
                    ),
                    (
                        "prod_stove_03",
                        "ACME Campfire Compact Gas Stove",
                        "ACME",
                        49.99,
                        "USD",
                        4.4,
                        56,
                        "Cooking",
                        json.dumps(["ultralight"]),
                        json.dumps({"fuel": "isobutane", "weight": "2.6 oz"}),
                        1,
                        "Pocket-sized backpacking stove with piezo ignition.",
                        "Boils 1 liter of water in under 3.5 minutes under windy conditions.",
                    ),
                ],
            )

            # Orders
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO orders (
                    order_id, user_id, status, placed_at, total, currency, items_json, tracking_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "ord_9001",
                        "user_demo",
                        "delivered",
                        datetime.now(UTC).isoformat(),
                        199.99,
                        "USD",
                        json.dumps(
                            [
                                {
                                    "product_id": "prod_tent_01",
                                    "title": "ACME Trailblazer 2-Person Tent",
                                    "quantity": 1,
                                    "price": 199.99,
                                }
                            ]
                        ),
                        "https://tracking.acme.example/ord_9001",
                    )
                ],
            )

            # Policies
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO policies (policy_id, title, category, content)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        "pol_returns",
                        "Return & Refund Policy",
                        "returns",
                        "Full refund within 30 days of delivery on unworn gear in original packaging. Free return shipping for members.",
                    ),
                    (
                        "pol_shipping",
                        "Shipping & Delivery Policy",
                        "shipping",
                        "Standard shipping takes 3-5 business days ($5.99, free on orders over $50). Express shipping takes 1-2 business days ($14.99).",
                    ),
                ],
            )

            # User profile
            self._conn.execute(
                """
                INSERT OR REPLACE INTO user_preferences (user_id, display_name, loyalty_tier, default_location, preferences_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "user_demo",
                    "Alex Mercer",
                    "Gold Explorer",
                    "Seattle, WA",
                    json.dumps({"favorite_activities": "backpacking, trail running"}),
                ),
            )

    def _row_to_product(self, row: sqlite3.Row) -> Product:
        return Product(
            product_id=row["product_id"],
            title=row["title"],
            brand=row["brand"],
            price=row["price"],
            currency=row["currency"] or "USD",
            rating=row["rating"],
            review_count=row["review_count"],
            image_url=row["image_url"],
            category=row["category"],
            labels=json.loads(row["labels_json"]) if row["labels_json"] else [],
            attributes=json.loads(row["attributes_json"]) if row["attributes_json"] else {},
            in_stock=bool(row["in_stock"]),
            short_description=row["short_description"],
            options=json.loads(row["options_json"]) if row["options_json"] else {},
            option_values=json.loads(row["option_values_json"])
            if row["option_values_json"]
            else {},
            variant_of=row["variant_of"],
        )

    # -- Catalog Methods -----------------------------------------------------------

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        query_pattern = f"%{query.strip().lower()}%" if query.strip() else "%"
        sql = """
            SELECT * FROM products
            WHERE (LOWER(title) LIKE ? OR LOWER(category) LIKE ? OR LOWER(short_description) LIKE ?)
        """
        params: list[Any] = [query_pattern, query_pattern, query_pattern]

        if filters:
            if filters.category:
                sql += " AND LOWER(category) = ?"
                params.append(filters.category.lower())
            if filters.min_price is not None:
                sql += " AND price >= ?"
                params.append(filters.min_price)
            if filters.max_price is not None:
                sql += " AND price <= ?"
                params.append(filters.max_price)
            if filters.min_rating is not None:
                sql += " AND rating >= ?"
                params.append(filters.min_rating)

            if filters.sort == "price_asc":
                sql += " ORDER BY price ASC"
            elif filters.sort == "price_desc":
                sql += " ORDER BY price DESC"
            elif filters.sort == "rating":
                sql += " ORDER BY rating DESC"

        sql += " LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(sql, params)
        return [self._row_to_product(row) for row in cursor.fetchall()]

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        cursor = self._conn.execute("SELECT * FROM products WHERE product_id = ?", (product_id,))
        row = cursor.fetchone()
        if not row:
            return None

        prod = self._row_to_product(row)
        return ProductDetails(
            **prod.model_dump(),
            long_description=row["long_description"],
            specs={"warranty": "1-year limited", "origin": "Imported"},
            review_highlights=["Excellent quality", "Easy setup"],
            variants=[],
        )

    # -- Cart Methods -------------------------------------------------------------

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        cursor = self._conn.execute(
            "SELECT * FROM carts WHERE session_id = ? ORDER BY title ASC",
            (session.session_id,),
        )
        items = [
            CartItem(
                product_id=row["product_id"],
                title=row["title"],
                price=row["price"],
                quantity=row["quantity"],
                image_url=row["image_url"],
                option_values=json.loads(row["option_values_json"])
                if row["option_values_json"]
                else {},
                variant_of=row["variant_of"],
            )
            for row in cursor.fetchall()
        ]
        return Cart(items=items)

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        cursor = self._conn.execute("SELECT * FROM products WHERE product_id = ?", (product_id,))
        prod_row = cursor.fetchone()
        if not prod_row:
            raise Unavailable(f"Product {product_id} not found in catalog.")
        if not prod_row["in_stock"]:
            raise Unavailable(f"Product {product_id} is currently out of stock.")

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO carts (session_id, product_id, title, price, quantity, image_url, option_values_json, variant_of)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, product_id) DO UPDATE SET
                    quantity = quantity + excluded.quantity
                """,
                (
                    session.session_id,
                    product_id,
                    prod_row["title"],
                    prod_row["price"],
                    quantity,
                    prod_row["image_url"],
                    prod_row["option_values_json"],
                    prod_row["variant_of"],
                ),
            )
        return await self.get_cart(session)

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        with self._conn:
            self._conn.execute(
                "UPDATE carts SET quantity = ? WHERE session_id = ? AND product_id = ?",
                (quantity, session.session_id, product_id),
            )
        return await self.get_cart(session)

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        with self._conn:
            self._conn.execute(
                "DELETE FROM carts WHERE session_id = ? AND product_id = ?",
                (session.session_id, product_id),
            )
        return await self.get_cart(session)

    # -- Customer Context ---------------------------------------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        cursor = self._conn.execute(
            "SELECT * FROM user_preferences WHERE user_id = ?", (session.user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return UserPreferences(user_id=session.user_id)
        return UserPreferences(
            user_id=row["user_id"],
            display_name=row["display_name"],
            loyalty_tier=row["loyalty_tier"],
            default_location=row["default_location"],
            preferences=json.loads(row["preferences_json"]) if row["preferences_json"] else {},
        )

    async def checkout_handoff(
        self, session: ShoppingSessionContext, cart: Cart
    ) -> list[CheckoutHandoff]:
        return [
            CheckoutHandoff(
                url=f"https://checkout.acme.example/pay?session_id={session.session_id}",
                label="Proceed to Secure Payment",
            )
        ]

    # -- Orders & Policies --------------------------------------------------------

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        cursor = self._conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY placed_at DESC LIMIT ?",
            (session.user_id, limit),
        )
        orders = []
        for row in cursor.fetchall():
            items_raw = json.loads(row["items_json"])
            items = [OrderItem(**item) for item in items_raw]
            orders.append(
                Order(
                    order_id=row["order_id"],
                    status=OrderStatus(row["status"]),
                    placed_at=datetime.fromisoformat(row["placed_at"]),
                    total=row["total"],
                    currency=row["currency"] or "USD",
                    items=items,
                    tracking_url=row["tracking_url"],
                )
            )
        return orders

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        cursor = self._conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND user_id = ?",
            (order_id, session.user_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        items_raw = json.loads(row["items_json"])
        return Order(
            order_id=row["order_id"],
            status=OrderStatus(row["status"]),
            placed_at=datetime.fromisoformat(row["placed_at"]),
            total=row["total"],
            currency=row["currency"] or "USD",
            items=[OrderItem(**item) for item in items_raw],
            tracking_url=row["tracking_url"],
        )

    async def search_policies(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        query_pattern = f"%{query.strip().lower()}%"
        cursor = self._conn.execute(
            """
            SELECT * FROM policies
            WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(category) LIKE ?
            """,
            (query_pattern, query_pattern, query_pattern),
        )
        return [
            Policy(
                policy_id=row["policy_id"],
                title=row["title"],
                category=row["category"],
                content=row["content"],
            )
            for row in cursor.fetchall()
        ]

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ) -> list[FulfillmentOption]:
        return [
            FulfillmentOption(method="shipping", eta="3-5 business days", fee=5.99),
            FulfillmentOption(
                method="pickup", eta="Ready in 2 hours", fee=0.0, location="Downtown Flagship"
            ),
        ]
