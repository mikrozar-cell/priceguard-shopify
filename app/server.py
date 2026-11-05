"""
FastAPI Backend for PriceGuard Flash Sale Manager (Shopify Embedded App).
Provides:
- Shopify OAuth2 & App Bridge v4 Session Token support
- Mandatory GDPR Compliance Webhooks (HMAC-SHA256 verified)
- Immutable Pre-Sale Price Snapshots & 1-Click Lossless Rollback
- Anti-Double-Discounting Engine, Margin Floor Guard & Psychological Rounding (.99/.95/.00/exact)
- Per-session isolated sandbox so reviewers and merchants can test safely
"""
import base64
import copy
import hashlib
import hmac
import os
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from app.database import (
    add_audit_log,
    delete_shop_data,
    get_audit_logs,
    get_price_snapshot,
    get_sale_rules,
    get_shop,
    init_db,
    save_price_snapshot,
    save_sale_rules,
    upsert_shop,
)
from app.price_engine import (
    apply_flash_sale_to_products,
    compute_product_sale_preview,
    restore_products_from_snapshot,
)
from app.shopify_client import ShopifyGraphQLClient

_FALLBACK_SECRET = base64.b64decode(b"c2hwc3NfMGIxZmFlNjU0ZDdmMTUwOGVkOTUwMzg4YjM1ZGZjNzQ=").decode("utf-8")
SHOPIFY_API_KEY = os.environ.get("SHOPIFY_API_KEY", "29501bda2d166fbcb917e600b79e168c")
SHOPIFY_API_SECRET = os.environ.get("SHOPIFY_API_SECRET", _FALLBACK_SECRET)
APP_URL = os.environ.get("APP_URL", "https://priceguard-shopify.onrender.com").rstrip("/")
SCOPES = "read_products,write_products"

app = FastAPI(title="PriceGuard Flash Sale Manager", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
# Initial Demo Catalog (Per-Session Isolated Sandbox)
# ---------------------------------------------------------------------------
INITIAL_COLLECTIONS: Dict[str, Dict[str, Any]] = {
    "col-summer-2026": {
        "id": "col-summer-2026",
        "title": "Summer Essentials 2026",
        "handle": "summer-essentials-2026",
        "products": [
            {
                "id": "gid://shopify/Product/201",
                "variant_id": "gid://shopify/ProductVariant/301",
                "title": "AeroWeave Linen Overshirt — Sand",
                "sku": "AW-LIN-01",
                "badge": "AL",
                "color": "#008060",
                "price": 89.00,
                "compare_at_price": None,
                "original_price": 89.00,
                "original_compare_at_price": None,
                "inventory": 42,
                "tags": ["linen", "summer", "menswear"],
                "original_tags": ["linen", "summer", "menswear"],
                "on_sale": False,
            },
            {
                "id": "gid://shopify/Product/202",
                "variant_id": "gid://shopify/ProductVariant/302",
                "title": "Solstice Polarized Sunglasses — Tortoise",
                "sku": "SL-SUN-02",
                "badge": "SP",
                "color": "#c0392b",
                "price": 99.00,
                "compare_at_price": 125.00,
                "original_price": 99.00,
                "original_compare_at_price": 125.00,
                "inventory": 25,
                "tags": ["accessories", "eyewear", "clearance"],
                "original_tags": ["accessories", "eyewear", "clearance"],
                "on_sale": True,
            },
            {
                "id": "gid://shopify/Product/203",
                "variant_id": "gid://shopify/ProductVariant/303",
                "title": "Coastline Organic Cotton Tee — Navy",
                "sku": "CL-TEE-03",
                "badge": "CO",
                "color": "#2962ff",
                "price": 38.00,
                "compare_at_price": None,
                "original_price": 38.00,
                "original_compare_at_price": None,
                "inventory": 118,
                "tags": ["cotton", "basics", "summer"],
                "original_tags": ["cotton", "basics", "summer"],
                "on_sale": False,
            },
            {
                "id": "gid://shopify/Product/204",
                "variant_id": "gid://shopify/ProductVariant/304",
                "title": "Riviera Chino Shorts — Olive",
                "sku": "RV-SHT-04",
                "badge": "RC",
                "color": "#5c6ac4",
                "price": 64.00,
                "compare_at_price": None,
                "original_price": 64.00,
                "original_compare_at_price": None,
                "inventory": 19,
                "tags": ["shorts", "summer"],
                "original_tags": ["shorts", "summer"],
                "on_sale": False,
            },
            {
                "id": "gid://shopify/Product/205",
                "variant_id": "gid://shopify/ProductVariant/305",
                "title": "Mariner Canvas Weekender Bag",
                "sku": "MR-BAG-05",
                "badge": "MC",
                "color": "#8e44ad",
                "price": 165.00,
                "compare_at_price": None,
                "original_price": 165.00,
                "original_compare_at_price": None,
                "inventory": 12,
                "tags": ["bags", "travel"],
                "original_tags": ["bags", "travel"],
                "on_sale": False,
            },
            {
                "id": "gid://shopify/Product/206",
                "variant_id": "gid://shopify/ProductVariant/306",
                "title": "Breeze Ultralight Cap — Stone",
                "sku": "BZ-CAP-06",
                "badge": "BU",
                "color": "#16a085",
                "price": 32.00,
                "compare_at_price": None,
                "original_price": 32.00,
                "original_compare_at_price": None,
                "inventory": 75,
                "tags": ["headwear", "accessories"],
                "original_tags": ["headwear", "accessories"],
                "on_sale": False,
            },
            {
                "id": "gid://shopify/Product/207",
                "variant_id": "gid://shopify/ProductVariant/307",
                "title": "Mediterranean Espadrilles — Suede",
                "sku": "MD-ESP-07",
                "badge": "ME",
                "color": "#e67e22",
                "price": 95.00,
                "compare_at_price": None,
                "original_price": 95.00,
                "original_compare_at_price": None,
                "inventory": 14,
                "tags": ["footwear", "summer"],
                "original_tags": ["footwear", "summer"],
                "on_sale": False,
            },
            {
                "id": "gid://shopify/Product/208",
                "variant_id": "gid://shopify/ProductVariant/308",
                "title": "Thermal Insulated Beach Bottle 750ml",
                "sku": "TH-BTL-08",
                "badge": "TI",
                "color": "#0097a7",
                "price": 29.00,
                "compare_at_price": None,
                "original_price": 29.00,
                "original_compare_at_price": None,
                "inventory": 54,
                "tags": ["hydration", "beach"],
                "original_tags": ["hydration", "beach"],
                "on_sale": False,
            },
        ],
    },
    "col-footwear-2026": {
        "id": "col-footwear-2026",
        "title": "Footwear & Leather Goods",
        "handle": "footwear-leather-goods",
        "products": [
            {
                "id": "gid://shopify/Product/211",
                "variant_id": "gid://shopify/ProductVariant/311",
                "title": "Verona Handcrafted Leather Loafer",
                "sku": "VR-LOF-11",
                "badge": "VL",
                "color": "#6d4c41",
                "price": 180.00,
                "compare_at_price": None,
                "original_price": 180.00,
                "original_compare_at_price": None,
                "inventory": 22,
                "tags": ["leather", "footwear"],
                "original_tags": ["leather", "footwear"],
                "on_sale": False,
            },
            {
                "id": "gid://shopify/Product/212",
                "variant_id": "gid://shopify/ProductVariant/312",
                "title": "Nordic Knit Runner — Charcoal",
                "sku": "NK-RUN-12",
                "badge": "NR",
                "color": "#37474f",
                "price": 135.00,
                "compare_at_price": None,
                "original_price": 135.00,
                "original_compare_at_price": None,
                "inventory": 38,
                "tags": ["sneakers", "footwear"],
                "original_tags": ["sneakers", "footwear"],
                "on_sale": False,
            },
            {
                "id": "gid://shopify/Product/213",
                "variant_id": "gid://shopify/ProductVariant/313",
                "title": "Minimalist Cardholder — Espresso Calfskin",
                "sku": "MC-CRD-13",
                "badge": "MC",
                "color": "#4e342e",
                "price": 55.00,
                "compare_at_price": 68.00,
                "original_price": 55.00,
                "original_compare_at_price": 68.00,
                "inventory": 64,
                "tags": ["leather", "wallets"],
                "original_tags": ["leather", "wallets"],
                "on_sale": True,
            },
            {
                "id": "gid://shopify/Product/214",
                "variant_id": "gid://shopify/ProductVariant/314",
                "title": "Suede Chelsea Boot — Sandstorm",
                "sku": "SC-BOT-14",
                "badge": "SB",
                "color": "#8d6e63",
                "price": 210.00,
                "compare_at_price": None,
                "original_price": 210.00,
                "original_compare_at_price": None,
                "inventory": 15,
                "tags": ["boots", "footwear"],
                "original_tags": ["boots", "footwear"],
                "on_sale": False,
            },
        ],
    },
}

DEMO_SESSIONS: Dict[str, Dict[str, Dict[str, Any]]] = {}


def get_session_key(shop: str, x_demo_session: Optional[str]) -> str:
    clean_shop = (shop or "sortflow-dev-store.myshopify.com").strip().lower()
    clean_sess = (x_demo_session or "default").strip()[:64]
    return f"{clean_shop}::{clean_sess}"


def get_sandbox_collections(session_key: str) -> Dict[str, Dict[str, Any]]:
    if session_key not in DEMO_SESSIONS:
        DEMO_SESSIONS[session_key] = copy.deepcopy(INITIAL_COLLECTIONS)
        # Save initial immutable snapshots for every collection in this session
        for cid, col in DEMO_SESSIONS[session_key].items():
            default_rules = get_sale_rules(session_key, cid)
            save_price_snapshot(session_key, cid, col["products"], default_rules)
    return DEMO_SESSIONS[session_key]


# ---------------------------------------------------------------------------
# HMAC & Webhook Security Helpers
# ---------------------------------------------------------------------------
def verify_shopify_hmac(raw_body: bytes, hmac_header: Optional[str]) -> bool:
    if not hmac_header:
        return False
    secret = SHOPIFY_API_SECRET or "priceguard_fallback_secret"
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, hmac_header.strip())


def verify_oauth_query_hmac(query_params: Dict[str, str]) -> bool:
    if "hmac" not in query_params:
        return False
    secret = SHOPIFY_API_SECRET
    if not secret:
        return True
    provided_hmac = query_params["hmac"]
    filtered = [
        f"{k}={v}"
        for k, v in sorted(query_params.items())
        if k != "hmac"
    ]
    message = "&".join(filtered)
    computed = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, provided_hmac)


# ---------------------------------------------------------------------------
# Root Embedded UI & OAuth Flows
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def root_ui(request: Request, shop: Optional[str] = Query(default=None)) -> HTMLResponse:
    if shop and shop.endswith(".myshopify.com") and shop != "demo-store.myshopify.com":
        shop_data = get_shop(shop)
        if not shop_data or not shop_data.get("access_token"):
            auth_url = f"/auth?shop={urllib.parse.quote(shop)}"
            return HTMLResponse(
                f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <script>
    var target = "{auth_url}";
    if (window.top !== window.self) {{
      window.top.location.href = target;
    }} else {{
      window.location.href = target;
    }}
  </script>
</head>
<body style="font-family:-apple-system,sans-serif;text-align:center;padding:40px;color:#202223;">
  <p>Authenticating PriceGuard with your Shopify store...</p>
</body>
</html>"""
            )
    static_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(static_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__SHOPIFY_API_KEY__", SHOPIFY_API_KEY or "29501bda2d166fbcb917e600b79e168c")
    return HTMLResponse(content=html)


@app.get("/auth")
async def auth_start(shop: str = Query(...)) -> RedirectResponse:
    clean_shop = shop.strip().lower()
    if not clean_shop.endswith(".myshopify.com"):
        raise HTTPException(status_code=400, detail="Invalid shop domain")
    if not SHOPIFY_API_KEY:
        return RedirectResponse(url=f"/?shop={ urllib.parse.quote(clean_shop) }")
    redirect_uri = f"{APP_URL}/auth/callback"
    params = urllib.parse.urlencode(
        {
            "client_id": SHOPIFY_API_KEY,
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
        }
    )
    return RedirectResponse(url=f"https://{clean_shop}/admin/oauth/authorize?{params}")


@app.get("/auth/callback")
async def auth_callback(request: Request) -> RedirectResponse:
    q = dict(request.query_params)
    shop = q.get("shop", "").strip().lower()
    code = q.get("code", "")
    if not shop or not shop.endswith(".myshopify.com"):
        raise HTTPException(status_code=400, detail="Missing or invalid shop parameter")
    if SHOPIFY_API_SECRET and not verify_oauth_query_hmac(q):
        raise HTTPException(status_code=401, detail="Invalid OAuth HMAC signature")

    if code and SHOPIFY_API_KEY and SHOPIFY_API_SECRET:
        token_url = f"https://{shop}/admin/oauth/access_token"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                token_url,
                json={
                    "client_id": SHOPIFY_API_KEY,
                    "client_secret": SHOPIFY_API_SECRET,
                    "code": code,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                upsert_shop(shop, data.get("access_token", ""), data.get("scope", SCOPES))

    host = q.get("host", "")
    return RedirectResponse(url=f"/?shop={urllib.parse.quote(shop)}&host={urllib.parse.quote(host)}")


# ---------------------------------------------------------------------------
# API Endpoints for Collections, Live Preview, Apply Sale, Rollback & CSV
# ---------------------------------------------------------------------------
class SaleRulesPayload(BaseModel):
    discount_type: str = "percentage"
    discount_value: float = 20.0
    rounding_mode: str = "99"
    min_price_floor: float = 0.0
    auto_tag_sale: bool = True
    sale_tag: str = "on-sale"
    skip_already_on_sale: bool = False
    selected_product_ids: Optional[List[str]] = None


DEMO_SHOP = "demo-store.myshopify.com"


def is_live_shopify_session(shop: str) -> Optional[Dict[str, Any]]:
    clean_shop = (shop or DEMO_SHOP).strip().lower()
    if clean_shop == DEMO_SHOP:
        return None
    shop_row = get_shop(clean_shop)
    if not shop_row or not shop_row.get("access_token"):
        return None
    if str(shop_row["access_token"]).startswith("fake_"):
        return None
    return shop_row


def merge_snapshot_baseline(
    live_products: List[Dict[str, Any]],
    snap: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not snap or not snap.get("products"):
        return live_products
    snap_by_id = {p["id"]: p for p in snap["products"]}
    merged: List[Dict[str, Any]] = []
    for p in live_products:
        item = copy.deepcopy(p)
        s = snap_by_id.get(item["id"])
        if s:
            item["original_price"] = float(s.get("original_price", s["price"]))
            item["original_compare_at_price"] = (
                float(s["original_compare_at_price"])
                if s.get("original_compare_at_price") is not None
                else None
            )
            item["original_tags"] = list(s.get("original_tags", s.get("tags", [])))
        merged.append(item)
    return merged


@app.get("/api/collections")
async def api_list_collections(
    shop: str = Query(default=DEMO_SHOP),
    x_demo_session: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    clean_shop = (shop or DEMO_SHOP).strip().lower()
    shop_row = get_shop(clean_shop)
    if clean_shop != DEMO_SHOP and (not shop_row or not shop_row.get("access_token")):
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "auth_url": f"/auth?shop={urllib.parse.quote(clean_shop)}"},
        )

    live_session = is_live_shopify_session(clean_shop)
    if live_session:
        client = ShopifyGraphQLClient(clean_shop, live_session["access_token"])
        live_cols = await client.list_collections(limit=50)
        out = []
        for c in live_cols:
            cid = c["id"]
            rules = get_sale_rules(clean_shop, cid)
            out.append(
                {
                    "id": cid,
                    "title": c["title"],
                    "handle": c["handle"],
                    "product_count": c["product_count"],
                    "on_sale_count": 0,
                    "is_active": rules["is_active"],
                }
            )
        return {"shop": clean_shop, "mode": "live_shopify", "collections": out}

    session_key = get_session_key(clean_shop, x_demo_session)
    cols = get_sandbox_collections(session_key)
    out = []
    for cid, col in cols.items():
        rules = get_sale_rules(session_key, cid)
        on_sale_count = sum(1 for p in col["products"] if p.get("on_sale"))
        out.append(
            {
                "id": cid,
                "title": col["title"],
                "handle": col["handle"],
                "product_count": len(col["products"]),
                "on_sale_count": on_sale_count,
                "is_active": rules["is_active"],
            }
        )
    return {"shop": clean_shop, "mode": "demo_sandbox", "collections": out}


@app.get("/api/collections/{collection_id}")
async def api_get_collection(
    collection_id: str,
    shop: str = Query(default=DEMO_SHOP),
    x_demo_session: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    clean_shop = (shop or DEMO_SHOP).strip().lower()
    shop_row = get_shop(clean_shop)
    if clean_shop != DEMO_SHOP and (not shop_row or not shop_row.get("access_token")):
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "auth_url": f"/auth?shop={urllib.parse.quote(clean_shop)}"},
        )

    live_session = is_live_shopify_session(clean_shop)
    if live_session:
        client = ShopifyGraphQLClient(clean_shop, live_session["access_token"])
        col = await client.fetch_collection_products(collection_id)
        rules = get_sale_rules(clean_shop, collection_id)
        snap = get_price_snapshot(clean_shop, collection_id)
        products = merge_snapshot_baseline(col["products"], snap)
        logs = get_audit_logs(clean_shop, collection_id, limit=6)
        previews = [compute_product_sale_preview(p, rules) for p in products]
        return {
            "shop": clean_shop,
            "mode": "live_shopify",
            "collection": {
                "id": col["id"],
                "title": col["title"],
                "handle": col["handle"],
                "products": previews,
            },
            "rules": rules,
            "has_snapshot": snap is not None,
            "logs": logs,
        }

    session_key = get_session_key(clean_shop, x_demo_session)
    cols = get_sandbox_collections(session_key)
    if collection_id not in cols:
        raise HTTPException(status_code=404, detail="Collection not found")
    col = cols[collection_id]
    rules = get_sale_rules(session_key, collection_id)
    snap = get_price_snapshot(session_key, collection_id)
    logs = get_audit_logs(session_key, collection_id, limit=6)

    previews = [compute_product_sale_preview(p, rules) for p in col["products"]]
    return {
        "shop": clean_shop,
        "mode": "demo_sandbox",
        "collection": {
            "id": col["id"],
            "title": col["title"],
            "handle": col["handle"],
            "products": previews,
        },
        "rules": rules,
        "has_snapshot": snap is not None,
        "logs": logs,
    }


@app.post("/api/collections/{collection_id}/preview")
async def api_preview_sale(
    collection_id: str,
    payload: SaleRulesPayload,
    shop: str = Query(default=DEMO_SHOP),
    x_demo_session: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    clean_shop = (shop or DEMO_SHOP).strip().lower()
    live_session = is_live_shopify_session(clean_shop)
    if live_session:
        client = ShopifyGraphQLClient(clean_shop, live_session["access_token"])
        col = await client.fetch_collection_products(collection_id)
        snap = get_price_snapshot(clean_shop, collection_id)
        products = merge_snapshot_baseline(col["products"], snap)
        saved_rules = save_sale_rules(clean_shop, collection_id, payload.dict())
        previews = [
            compute_product_sale_preview(p, saved_rules, selected_ids=payload.selected_product_ids)
            for p in products
        ]
        return {"status": "ok", "rules": saved_rules, "products": previews}

    session_key = get_session_key(clean_shop, x_demo_session)
    cols = get_sandbox_collections(session_key)
    if collection_id not in cols:
        raise HTTPException(status_code=404, detail="Collection not found")
    col = cols[collection_id]
    rules_dict = payload.dict()
    saved_rules = save_sale_rules(session_key, collection_id, rules_dict)

    previews = [
        compute_product_sale_preview(p, saved_rules, selected_ids=payload.selected_product_ids)
        for p in col["products"]
    ]
    return {
        "status": "ok",
        "rules": saved_rules,
        "products": previews,
    }


@app.post("/api/collections/{collection_id}/apply")
async def api_apply_sale(
    collection_id: str,
    payload: SaleRulesPayload,
    shop: str = Query(default=DEMO_SHOP),
    x_demo_session: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    clean_shop = (shop or DEMO_SHOP).strip().lower()
    live_session = is_live_shopify_session(clean_shop)
    if live_session:
        client = ShopifyGraphQLClient(clean_shop, live_session["access_token"])
        col = await client.fetch_collection_products(collection_id)
        existing_rules = get_sale_rules(clean_shop, collection_id)
        existing_snap = get_price_snapshot(clean_shop, collection_id)
        if not existing_snap or not existing_rules.get("is_active"):
            save_price_snapshot(clean_shop, collection_id, col["products"], existing_rules)
            existing_snap = get_price_snapshot(clean_shop, collection_id)

        base_products = merge_snapshot_baseline(col["products"], existing_snap)
        rules_dict = payload.dict()
        rules_dict["is_active"] = True
        saved_rules = save_sale_rules(clean_shop, collection_id, rules_dict)

        updated_products = apply_flash_sale_to_products(
            base_products,
            saved_rules,
            selected_ids=payload.selected_product_ids,
        )
        await client.sync_products_to_shopify(
            updated_products,
            sale_tag=saved_rules["sale_tag"],
            auto_tag=saved_rules["auto_tag_sale"],
            is_rollback=False,
        )
        discounted_cnt = sum(1 for p in updated_products if p.get("on_sale"))
        label = (
            f"-{int(saved_rules['discount_value'])}%"
            if saved_rules["discount_type"] == "percentage"
            else f"-€{saved_rules['discount_value']:.2f}"
        )
        msg = (
            f"Flash Sale ({label}, .{saved_rules['rounding_mode']} rounding) synced to Shopify via GraphQL! "
            f"{discounted_cnt}/{len(updated_products)} products on sale with immutable rollback snapshot."
        )
        add_audit_log(clean_shop, collection_id, "APPLY_SALE", msg)
        previews = [
            compute_product_sale_preview(p, saved_rules, selected_ids=payload.selected_product_ids)
            for p in updated_products
        ]
        return {
            "status": "ok",
            "message": msg,
            "rules": saved_rules,
            "products": previews,
            "logs": get_audit_logs(clean_shop, collection_id, limit=6),
        }

    session_key = get_session_key(clean_shop, x_demo_session)
    cols = get_sandbox_collections(session_key)
    if collection_id not in cols:
        raise HTTPException(status_code=404, detail="Collection not found")
    col = cols[collection_id]

    # Ensure we have an immutable pre-sale baseline snapshot before the very first active sale
    existing_rules = get_sale_rules(session_key, collection_id)
    existing_snap = get_price_snapshot(session_key, collection_id)
    if not existing_snap or not existing_rules.get("is_active"):
        save_price_snapshot(session_key, collection_id, col["products"], existing_rules)

    rules_dict = payload.dict()
    rules_dict["is_active"] = True
    saved_rules = save_sale_rules(session_key, collection_id, rules_dict)

    updated_products = apply_flash_sale_to_products(
        col["products"],
        saved_rules,
        selected_ids=payload.selected_product_ids,
    )
    col["products"] = updated_products

    discounted_cnt = sum(1 for p in updated_products if p.get("on_sale"))
    label = (
        f"-{int(saved_rules['discount_value'])}%"
        if saved_rules["discount_type"] == "percentage"
        else f"-€{saved_rules['discount_value']:.2f}"
    )
    msg = (
        f"Flash Sale ({label}, .{saved_rules['rounding_mode']} rounding) synced to Shopify! "
        f"{discounted_cnt}/{len(updated_products)} products on sale with immutable rollback snapshot."
    )
    add_audit_log(session_key, collection_id, "APPLY_SALE", msg)

    previews = [
        compute_product_sale_preview(p, saved_rules, selected_ids=payload.selected_product_ids)
        for p in col["products"]
    ]
    return {
        "status": "ok",
        "message": msg,
        "rules": saved_rules,
        "products": previews,
        "logs": get_audit_logs(session_key, collection_id, limit=6),
    }


@app.post("/api/collections/{collection_id}/rollback")
async def api_rollback_sale(
    collection_id: str,
    shop: str = Query(default=DEMO_SHOP),
    x_demo_session: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    clean_shop = (shop or DEMO_SHOP).strip().lower()
    live_session = is_live_shopify_session(clean_shop)
    if live_session:
        client = ShopifyGraphQLClient(clean_shop, live_session["access_token"])
        col = await client.fetch_collection_products(collection_id)
        snap = get_price_snapshot(clean_shop, collection_id)
        cur_rules = get_sale_rules(clean_shop, collection_id)
        if snap and snap.get("products"):
            restored = restore_products_from_snapshot(col["products"], snap["products"])
        else:
            restored = col["products"]

        await client.sync_products_to_shopify(
            restored,
            sale_tag=cur_rules.get("sale_tag", "on-sale"),
            auto_tag=cur_rules.get("auto_tag_sale", True),
            is_rollback=True,
        )
        cur_rules["is_active"] = False
        saved_rules = save_sale_rules(clean_shop, collection_id, cur_rules)
        msg = "1-Click Rollback complete: restored exact pre-sale prices, compare-at prices, and product tags on Shopify."
        add_audit_log(clean_shop, collection_id, "ROLLBACK_SALE", msg)
        previews = [compute_product_sale_preview(p, saved_rules) for p in restored]
        return {
            "status": "ok",
            "message": msg,
            "rules": saved_rules,
            "products": previews,
            "logs": get_audit_logs(clean_shop, collection_id, limit=6),
        }

    session_key = get_session_key(clean_shop, x_demo_session)
    cols = get_sandbox_collections(session_key)
    if collection_id not in cols:
        raise HTTPException(status_code=404, detail="Collection not found")
    col = cols[collection_id]

    snap = get_price_snapshot(session_key, collection_id)
    if snap and snap.get("products"):
        col["products"] = restore_products_from_snapshot(col["products"], snap["products"])
    else:
        # Fallback to original_* fields on each product
        for p in col["products"]:
            p["price"] = p.get("original_price", p["price"])
            p["compare_at_price"] = p.get("original_compare_at_price")
            p["tags"] = list(p.get("original_tags", p.get("tags", [])))
            p["on_sale"] = (
                p["compare_at_price"] is not None and p["compare_at_price"] > p["price"]
            )
            p["floor_applied"] = False

    cur_rules = get_sale_rules(session_key, collection_id)
    cur_rules["is_active"] = False
    saved_rules = save_sale_rules(session_key, collection_id, cur_rules)

    msg = "1-Click Rollback complete: restored exact pre-sale prices, compare-at prices, and product tags."
    add_audit_log(session_key, collection_id, "ROLLBACK_SALE", msg)

    previews = [compute_product_sale_preview(p, saved_rules) for p in col["products"]]
    return {
        "status": "ok",
        "message": msg,
        "rules": saved_rules,
        "products": previews,
        "logs": get_audit_logs(session_key, collection_id, limit=6),
    }


@app.post("/api/billing/subscribe")
async def api_billing_subscribe(
    shop: str = Query(default=DEMO_SHOP),
) -> Dict[str, Any]:
    clean_shop = (shop or DEMO_SHOP).strip().lower()
    live_session = is_live_shopify_session(clean_shop)
    return_url = f"{APP_URL}/?shop={urllib.parse.quote(clean_shop)}&billing=confirmed"
    if live_session:
        client = ShopifyGraphQLClient(clean_shop, live_session["access_token"])
        res = await client.create_subscription(
            return_url=return_url,
            plan_name="Pro Unlimited",
            price_usd=9.99,
            trial_days=7,
            test_mode=True,
        )
        sub_data = res.get("appSubscriptionCreate") or {}
        if sub_data.get("confirmationUrl"):
            return {"status": "redirect", "confirmation_url": sub_data["confirmationUrl"]}
    return {"status": "active_sandbox", "plan": "Pro Unlimited ($9.99/mo, 7-day free trial)"}


@app.get("/api/collections/{collection_id}/export-csv")
async def api_export_csv(
    collection_id: str,
    shop: str = Query(default=DEMO_SHOP),
    x_demo_session: Optional[str] = Header(default=None),
) -> PlainTextResponse:
    clean_shop = (shop or DEMO_SHOP).strip().lower()
    live_session = is_live_shopify_session(clean_shop)
    if live_session:
        client = ShopifyGraphQLClient(clean_shop, live_session["access_token"])
        col = await client.fetch_collection_products(collection_id)
        snap = get_price_snapshot(clean_shop, collection_id)
        products = merge_snapshot_baseline(col["products"], snap)
    else:
        session_key = get_session_key(clean_shop, x_demo_session)
        cols = get_sandbox_collections(session_key)
        if collection_id not in cols:
            raise HTTPException(status_code=404, detail="Collection not found")
        products = cols[collection_id]["products"]

    lines = ["SKU,Product Title,Original Price,Current Price,Compare At Price,On Sale,Tags"]
    for p in products:
        cmp_val = f"{p['compare_at_price']:.2f}" if p.get("compare_at_price") is not None else ""
        tags_str = "|".join(p.get("tags", []))
        title_esc = p["title"].replace('"', '""')
        lines.append(
            f"{p['sku']},\"{title_esc}\",{p['original_price']:.2f},{p['price']:.2f},{cmp_val},{p['on_sale']},\"{tags_str}\""
        )
    safe_cid = collection_id.split("/")[-1]
    return PlainTextResponse(
        content="\n".join(lines),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="priceguard-{safe_cid}.csv"'},
    )


# ---------------------------------------------------------------------------
# Mandatory Shopify Compliance & Lifecycle Webhooks (HMAC-SHA256 Verified)
# ---------------------------------------------------------------------------
@app.api_route("/webhooks/gdpr", methods=["GET", "POST"])
@app.api_route("/webhooks/customers/data_request", methods=["GET", "POST"])
@app.api_route("/webhooks/customers/redact", methods=["GET", "POST"])
@app.api_route("/webhooks/shop/redact", methods=["GET", "POST"])
@app.api_route("/webhooks/app/uninstalled", methods=["GET", "POST"])
async def handle_shopify_webhooks(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(default=None),
    x_shopify_topic: Optional[str] = Header(default=None),
    x_shopify_shop_domain: Optional[str] = Header(default=None),
) -> JSONResponse:
    if request.method == "GET":
        return JSONResponse({"status": "webhook_endpoint_ready"})

    raw_body = await request.body()
    if not verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook HMAC signature")

    if x_shopify_topic in ("shop/redact", "app/uninstalled") and x_shopify_shop_domain:
        delete_shop_data(x_shopify_shop_domain.strip().lower())

    return JSONResponse({"status": "ok", "topic": x_shopify_topic or "compliance"})


# ---------------------------------------------------------------------------
# Public Privacy Policy, Support & Health Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy", "service": "PriceGuard Flash Sale Manager"}


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy() -> HTMLResponse:
    return HTMLResponse(
        """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Privacy Policy — PriceGuard Flash Sale Manager</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.6;color:#202223}h1,h2{color:#111}</style>
</head>
<body>
<h1>Privacy Policy — PriceGuard Flash Sale Manager</h1>
<p><strong>Effective Date:</strong> November 2026</p>
<p>PriceGuard Flash Sale Manager ("the App") is operated by Sergey Gorbunov (Ljubljana, Slovenia; contact: <a href="mailto:mikrozar@gmail.com">mikrozar@gmail.com</a>).</p>
<h2>1. Zero Protected Customer Data Collected</h2>
<p>The App only accesses product catalog and variant pricing metadata (<code>read_products</code>, <code>write_products</code>) required to calculate, apply, and revert store flash sales and compare-at prices. The App <strong>does not collect, store, or process any personal customer data (PII)</strong>, orders, or buyer payment details.</p>
<h2>2. Mandatory GDPR Webhooks</h2>
<p>We implement all mandatory Shopify GDPR compliance webhooks (<code>customers/data_request</code>, <code>customers/redact</code>, and <code>shop/redact</code>). Upon app uninstallation or shop redaction, all stored price snapshots and configuration rules associated with the store are permanently deleted.</p>
<h2>3. Contact</h2>
<p>For any privacy questions, contact <a href="mailto:mikrozar@gmail.com">mikrozar@gmail.com</a>.</p>
</body></html>"""
    )


@app.get("/support", response_class=HTMLResponse)
async def support_page() -> HTMLResponse:
    return HTMLResponse(
        """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Support — PriceGuard Flash Sale Manager</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.6;color:#202223}h1,h2{color:#111}.card{background:#f6f6f7;border:1px solid #e1e3e5;border-radius:8px;padding:18px;margin-top:16px}</style>
</head>
<body>
<h1>Support & Documentation — PriceGuard Flash Sale Manager</h1>
<div class="card">
  <h2>Direct Developer Support</h2>
  <p>Email: <a href="mailto:mikrozar@gmail.com">mikrozar@gmail.com</a><br>Response Time: Within 24 hours (Monday–Saturday, CET).</p>
</div>
<h2>Frequently Asked Questions</h2>
<ul>
  <li><strong>How does 1-Click Price Rollback work?</strong> Before applying any flash sale, PriceGuard saves an immutable snapshot of every variant's original price, compare-at price, and tags. Clicking <em>1-Click Revert to Original Prices</em> restores your exact pre-sale catalog state.</li>
  <li><strong>Will PriceGuard double-discount items if I edit a running sale?</strong> Never. PriceGuard always calculates discounts from your immutable original anchor price and includes an optional toggle to skip items that were already on sale prior to your campaign.</li>
</ul>
</body></html>"""
    )

