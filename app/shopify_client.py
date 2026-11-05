"""
Shopify GraphQL Admin API Client (2025-01) for PriceGuard Flash Sale Manager.
Supports bulk variant price & compareAtPrice updates (`productVariantsBulkUpdate`),
sale tag updates (`tagsAdd` / `tagsRemove`), App Subscription billing, and webhook registration.
"""
from typing import Any, Dict, List, Optional
import httpx

SHOPIFY_API_VERSION = "2025-01"


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
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                self.endpoint,
                json={"query": query, "variables": variables or {}},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def bulk_update_product_prices(
        self,
        product_gid: str,
        variants: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Updates variant `price` and `compareAtPrice` on Shopify using GraphQL
        `productVariantsBulkUpdate` (API 2025-01).
        """
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

    async def create_subscription(
        self,
        return_url: str,
        plan_name: str = "PriceGuard Pro Unlimited",
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
