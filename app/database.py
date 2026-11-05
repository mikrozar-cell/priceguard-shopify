"""
SQLite Database Layer for PriceGuard Flash Sale Manager.
Stores shop OAuth tokens, collection sale rules, immutable pre-sale price snapshots
(for 100% accurate 1-Click Rollback), and per-session sandbox state.
"""
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get(
    "PRICEGUARD_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "priceguard.sqlite3"),
)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shops (
            shop_domain TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            scope TEXT,
            plan_status TEXT DEFAULT 'trial',
            charge_id TEXT,
            installed_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sale_rules (
            shop_domain TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            discount_type TEXT NOT NULL DEFAULT 'percentage',
            discount_value REAL NOT NULL DEFAULT 20.0,
            rounding_mode TEXT NOT NULL DEFAULT '99',
            min_price_floor REAL NOT NULL DEFAULT 0.0,
            auto_tag_sale INTEGER NOT NULL DEFAULT 1,
            sale_tag TEXT NOT NULL DEFAULT 'on-sale',
            skip_already_on_sale INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY (shop_domain, collection_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS price_snapshots (
            shop_domain TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            rules_json TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY (shop_domain, collection_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_domain TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def upsert_shop(shop_domain: str, access_token: str, scope: str = "read_products,write_products") -> None:
    now = time.time()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO shops (shop_domain, access_token, scope, installed_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(shop_domain) DO UPDATE SET
            access_token = excluded.access_token,
            scope = excluded.scope,
            updated_at = excluded.updated_at
        """,
        (shop_domain, access_token, scope, now, now),
    )
    conn.commit()
    conn.close()


def get_shop(shop_domain: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM shops WHERE shop_domain = ?", (shop_domain,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_shop_data(shop_domain: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM shops WHERE shop_domain = ?", (shop_domain,))
    conn.execute("DELETE FROM sale_rules WHERE shop_domain = ?", (shop_domain,))
    conn.execute("DELETE FROM price_snapshots WHERE shop_domain = ?", (shop_domain,))
    conn.execute("DELETE FROM audit_logs WHERE shop_domain = ?", (shop_domain,))
    conn.commit()
    conn.close()


def get_sale_rules(shop_domain: str, collection_id: str) -> Dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM sale_rules WHERE shop_domain = ? AND collection_id = ?",
        (shop_domain, collection_id),
    ).fetchone()
    conn.close()
    if not row:
        return {
            "shop_domain": shop_domain,
            "collection_id": collection_id,
            "discount_type": "percentage",
            "discount_value": 20.0,
            "rounding_mode": "99",
            "min_price_floor": 0.0,
            "auto_tag_sale": True,
            "sale_tag": "on-sale",
            "skip_already_on_sale": False,
            "is_active": False,
        }
    d = dict(row)
    d["auto_tag_sale"] = bool(d["auto_tag_sale"])
    d["skip_already_on_sale"] = bool(d["skip_already_on_sale"])
    d["is_active"] = bool(d["is_active"])
    return d


def save_sale_rules(shop_domain: str, collection_id: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    current = get_sale_rules(shop_domain, collection_id)
    merged = {**current, **rules}
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO sale_rules (
            shop_domain, collection_id, discount_type, discount_value,
            rounding_mode, min_price_floor, auto_tag_sale, sale_tag,
            skip_already_on_sale, is_active, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shop_domain, collection_id) DO UPDATE SET
            discount_type = excluded.discount_type,
            discount_value = excluded.discount_value,
            rounding_mode = excluded.rounding_mode,
            min_price_floor = excluded.min_price_floor,
            auto_tag_sale = excluded.auto_tag_sale,
            sale_tag = excluded.sale_tag,
            skip_already_on_sale = excluded.skip_already_on_sale,
            is_active = excluded.is_active,
            updated_at = excluded.updated_at
        """,
        (
            shop_domain,
            collection_id,
            str(merged.get("discount_type", "percentage")),
            float(merged.get("discount_value", 20.0)),
            str(merged.get("rounding_mode", "99")),
            float(merged.get("min_price_floor", 0.0)),
            1 if merged.get("auto_tag_sale", True) else 0,
            str(merged.get("sale_tag", "on-sale")).strip() or "on-sale",
            1 if merged.get("skip_already_on_sale", False) else 0,
            1 if merged.get("is_active", False) else 0,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_sale_rules(shop_domain, collection_id)


def save_price_snapshot(
    shop_domain: str,
    collection_id: str,
    products_state: List[Dict[str, Any]],
    rules_state: Optional[Dict[str, Any]] = None,
) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO price_snapshots (shop_domain, collection_id, snapshot_json, rules_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(shop_domain, collection_id) DO UPDATE SET
            snapshot_json = excluded.snapshot_json,
            rules_json = excluded.rules_json,
            created_at = excluded.created_at
        """,
        (
            shop_domain,
            collection_id,
            json.dumps(products_state),
            json.dumps(rules_state) if rules_state else None,
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def get_price_snapshot(shop_domain: str, collection_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute(
        "SELECT snapshot_json, rules_json, created_at FROM price_snapshots WHERE shop_domain = ? AND collection_id = ?",
        (shop_domain, collection_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "products": json.loads(row["snapshot_json"]),
        "rules": json.loads(row["rules_json"]) if row["rules_json"] else None,
        "created_at": row["created_at"],
    }


def add_audit_log(shop_domain: str, collection_id: str, action: str, details: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_logs (shop_domain, collection_id, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (shop_domain, collection_id, action, details, time.time()),
    )
    conn.commit()
    conn.close()


def get_audit_logs(shop_domain: str, collection_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT action, details, created_at FROM audit_logs WHERE shop_domain = ? AND collection_id = ? ORDER BY id DESC LIMIT ?",
        (shop_domain, collection_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
