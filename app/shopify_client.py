"""
Shopify GraphQL Admin API Client (2025-01) for PriceGuard Flash Sale Manager.
Supports:
- Fetching real store collections (`collections(first: 50)`)
- Fetching real collection products, variants (`price`, `compareAtPrice`, `sku`), inventory & tags
- Bulk variant price & compareAtPrice updates (`productVariantsBulkUpdate`)
- Sale tag updates (`tagsAdd` / `tagsRemove`)
- App Subscription billing (`appSubscriptionCreate`)
"""
from typing import Any, Dict, List, Optional
import httpx

SHOPIFY_API_VERSION = "2025-01"

AVATAR_COLORS = [
    "#008060", "#2962ff", "#5c6ac4", "#8e44ad",
    "#16a085", "#e67e22", "#0097a7", "#c0392b",
]


class ShopifyGraphQLClient:
    def __init__(self, shop_domain: str, access_token: str) -> None:
        self.shop_domain = shop_domain
        self.access_token = access_token
        self.endpoint = f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

    async def execute(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                self.endpoint,
                json={"query": query, "variables": variables or {}},
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                raise RuntimeError(f"Shopify GraphQL error: {payload['errors']}")
            return payload.get("data", {})

    async def list_collections(self, limit: int = 50) -> List[Dict[str, Any]]:
        query = """
        query ListCollections($first: Int!) {
          collections(first: $first) {
            nodes {
              id
              title
              handle
              productsCount {
                count
              }
            }
          }
        }
        """
        data = await self.execute(query, {"first": limit})
        nodes = (data.get("collections") or {}).get("nodes") or []
        return [
            {
                "id": n["id"].split("/")[-1],
                "gid": n["id"],
                "title": n["title"],
                "handle": n.get("handle", ""),
                "product_count": (n.get("productsCount") or {}).get("count", 0),
            }
            for n in nodes
        ]

    async def fetch_collection_products(self, collection_id: str, first: int = 100) -> Dict[str, Any]:
        gid = collection_id if collection_id.startswith("gid://") else f"gid://shopify/Collection/{collection_id}"
        query = """
        query GetCollectionProducts($id: ID!, $first: Int!) {
          collection(id: $id) {
            id
            title
            handle
            products(first: $first) {
              nodes {
                id
                title
                handle
                totalInventory
                tags
                variants(first: 1) {
                  nodes {
                    id
                    price
                    compareAtPrice
                    sku
                  }
                }
              }
            }
          }
        }
        """
        data = await self.execute(query, {"id": gid, "first": first})
        col = data.get("collection") or {}
        nodes = (col.get("products") or {}).get("nodes") or []
        products: List[Dict[str, Any]] = []

        for idx, n in enumerate(nodes):
            var_nodes = (n.get("variants") or {}).get("nodes") or []
            if not var_nodes:
                continue
            v = var_nodes[0]
            price_val = float(v.get("price") or 0.0)
            cmp_raw = v.get("compareAtPrice")
            cmp_val = float(cmp_raw) if cmp_raw not in (None, "") else None
            sku = v.get("sku") or f"SKU-{n['id'].split('/')[-1]}"
            words = [w for w in (n.get("title") or "PG").split() if w and w[0].isalnum()]
            badge = (words[0][0] + (words[1][0] if len(words) > 1 else words[0][-1])).upper()
            tags_list = list(n.get("tags") or [])

            products.append(
                {
                    "id": n["id"],
                    "variant_id": v["id"],
                    "title": n.get("title") or "Untitled Product",
                    "sku": sku,
                    "badge": badge,
                    "color": AVATAR_COLORS[idx % len(AVATAR_COLORS)],
                    "price": price_val,
                    "compare_at_price": cmp_val,
                    "original_price": price_val,
                    "original_compare_at_price": cmp_val,
                    "inventory": int(n.get("totalInventory") or 0),
                    "tags": tags_list,
                    "original_tags": list(tags_list),
                    "on_sale": cmp_val is not None and cmp_val > price_val,
                }
            )

        return {
            "id": collection_id.split("/")[-1],
            "gid": col.get("id", gid),
            "title": col.get("title") or f"Collection #{collection_id}",
            "handle": col.get("handle") or "",
            "products": products,
        }

    async def bulk_update_product_prices(
        self,
        product_gid: str,
        variants: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        mutation = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
          productVariantsBulkUpdate(productId: $productId, variants: $variants) {
            product {
              id
            }
            productVariants {
              id
              price
              compareAtPrice
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        return await self.execute(mutation, {"productId": product_gid, "variants": variants})

    async def add_tags(self, resource_gid: str, tags: List[str]) -> Dict[str, Any]:
        mutation = """
        mutation AddSaleTags($id: ID!, $tags: [String!]!) {
          tagsAdd(id: $id, tags: $tags) {
            node { id }
            userErrors { field message }
          }
        }
        """
        return await self.execute(mutation, {"id": resource_gid, "tags": tags})

    async def remove_tags(self, resource_gid: str, tags: List[str]) -> Dict[str, Any]:
        mutation = """
        mutation RemoveSaleTags($id: ID!, $tags: [String!]!) {
          tagsRemove(id: $id, tags: $tags) {
            node { id }
            userErrors { field message }
          }
        }
        """
        return await self.execute(mutation, {"id": resource_gid, "tags": tags})

    async def sync_products_to_shopify(
        self,
        products: List[Dict[str, Any]],
        sale_tag: str = "on-sale",
        auto_tag: bool = True,
        is_rollback: bool = False,
    ) -> int:
        synced_count = 0
        for p in products:
            product_gid = p["id"]
            variant_gid = p["variant_id"]
            price_str = f"{float(p['price']):.2f}"
            cmp_val = p.get("compare_at_price")
            cmp_str = f"{float(cmp_val):.2f}" if cmp_val is not None else None

            await self.bulk_update_product_prices(
                product_gid=product_gid,
                variants=[
                    {
                        "id": variant_gid,
                        "price": price_str,
                        "compareAtPrice": cmp_str,
                    }
                ],
            )
            if sale_tag:
                if is_rollback:
                    orig_tags = p.get("original_tags") or []
                    if sale_tag not in orig_tags:
                        await self.remove_tags(product_gid, [sale_tag])
                elif auto_tag and p.get("on_sale"):
                    await self.add_tags(product_gid, [sale_tag])
            synced_count += 1
        return synced_count

    async def create_subscription(
        self,
        return_url: str,
        plan_name: str = "Pro Unlimited",
        price_usd: float = 9.99,
        trial_days: int = 7,
        test_mode: bool = True,
    ) -> Dict[str, Any]:
        mutation = """
        mutation AppSubscriptionCreate(
          $name: String!,
          $lineItems: [AppSubscriptionLineItemInput!]!,
          $returnUrl: URL!,
          $trialDays: Int,
          $test: Boolean
        ) {
          appSubscriptionCreate(
            name: $name,
            returnUrl: $returnUrl,
            trialDays: $trialDays,
            test: $test,
            lineItems: $lineItems
          ) {
            userErrors {
              field
              message
            }
            confirmationUrl
            appSubscription {
              id
              status
            }
          }
        }
        """
        variables = {
            "name": plan_name,
            "returnUrl": return_url,
            "trialDays": trial_days,
            "test": test_mode,
            "lineItems": [
                {
                    "plan": {
                        "appRecurringPricingDetails": {
                            "price": {"amount": price_usd, "currencyCode": "USD"},
                            "interval": "EVERY_30_DAYS",
                        }
                    }
                }
            ],
        }
        return await self.execute(mutation, variables)
