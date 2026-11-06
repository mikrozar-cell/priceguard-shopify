"""
PriceGuard Core Pricing & Rollback Engine.
Guarantees zero double-discounting, psychological rounding (.99/.95/.00/exact),
margin floor protection, automatic sale tag management, and 100% lossless rollback.
"""
import copy
import math
from typing import Any, Dict, List, Optional


def apply_rounding(raw_price: float, original_price: float, rounding_mode: str) -> float:
    """Apply psychological price ending while guaranteeing sale_price <= original_price."""
    if raw_price <= 0:
        return 0.01
    mode = str(rounding_mode or "exact").lower()
    if mode == "exact":
        res = round(raw_price, 2)
    elif mode == "99":
        whole = math.floor(raw_price)
        candidate = whole + 0.99
        if candidate > raw_price + 0.49 or candidate >= original_price:
            candidate = max(0.99, (whole - 1) + 0.99) if whole >= 1 else round(raw_price, 2)
        res = round(candidate, 2)
    elif mode == "95":
        whole = math.floor(raw_price)
        candidate = whole + 0.95
        if candidate > raw_price + 0.49 or candidate >= original_price:
            candidate = max(0.95, (whole - 1) + 0.95) if whole >= 1 else round(raw_price, 2)
        res = round(candidate, 2)
    elif mode == "00":
        res = float(max(1, round(raw_price)))
        if res >= original_price and original_price > 1:
            res = float(math.floor(raw_price))
    else:
        res = round(raw_price, 2)

    if res >= original_price:
        res = round(max(0.01, raw_price), 2)
    return res


def compute_product_sale_preview(
    product: Dict[str, Any],
    rules: Dict[str, Any],
    selected_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Given a product with `original_price` and `original_compare_at_price`,
    compute its previewed sale state without mutating the input dict.
    """
    p = copy.deepcopy(product)
    orig_price = float(p.get("original_price", p["price"]))
    orig_compare = p.get("original_compare_at_price")
    orig_Compare_val = float(orig_compare) if orig_compare is not None else None

    was_already_on_sale = orig_Compare_val is not None and orig_Compare_val > orig_price

    if selected_ids is not None and p["id"] not in selected_ids:
        p["preview_skipped"] = True
        p["skip_reason"] = "Not selected"
        return p

    if rules.get("skip_already_on_sale", False) and was_already_on_sale:
        p["preview_skipped"] = True
        p["skip_reason"] = "Already on sale (Protected)"
        return p

    discount_type = str(rules.get("discount_type", "percentage"))
    discount_val = float(rules.get("discount_value", 20.0))
    rounding_mode = str(rules.get("rounding_mode", "99"))
    min_floor = float(rules.get("min_price_floor", 0.0))

    # If discount is 0 or negative, keep exact original pricing without markdowns
    if discount_val <= 0:
        p["preview_price"] = round(orig_price, 2)
        p["preview_compare_at_price"] = orig_Compare_val
        p["preview_savings_amount"] = 0.0
        p["preview_savings_pct"] = 0.0
        p["floor_applied"] = False
        p["preview_skipped"] = False
        p["skip_reason"] = None
        return p

    # Always calculate from the immutable original base price (or original compare_at_price if higher)
    anchor_price = orig_Compare_val if (orig_Compare_val and orig_Compare_val > orig_price) else orig_price

    if discount_type == "fixed":
        raw_new = max(0.01, anchor_price - discount_val)
    else:
        pct = max(0.0, min(95.0, discount_val))
        raw_new = anchor_price * (1.0 - pct / 100.0)

    rounded_new = apply_rounding(raw_new, anchor_price, rounding_mode)

    floor_applied = False
    if min_floor > 0 and rounded_new < min_floor:
        rounded_new = round(min(anchor_price, min_floor), 2)
        floor_applied = True

    p["preview_price"] = rounded_new
    p["preview_compare_at_price"] = round(anchor_price, 2) if rounded_new < anchor_price else orig_Compare_val
    p["preview_savings_amount"] = round(max(0.0, anchor_price - rounded_new), 2)
    p["preview_savings_pct"] = (
        round(((anchor_price - rounded_new) / anchor_price) * 100.0, 1) if anchor_price > 0 else 0.0
    )
    p["floor_applied"] = floor_applied
    p["preview_skipped"] = False
    p["skip_reason"] = None
    return p


def apply_flash_sale_to_products(
    products: List[Dict[str, Any]],
    rules: Dict[str, Any],
    selected_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Applies the flash sale rules to a list of products, updating `price`,
    `compare_at_price`, `on_sale`, and `tags` while preserving `original_*` fields.
    """
    updated: List[Dict[str, Any]] = []
    sale_tag = str(rules.get("sale_tag", "on-sale")).strip() or "on-sale"
    auto_tag = bool(rules.get("auto_tag_sale", True))

    for prod in products:
        preview = compute_product_sale_preview(prod, rules, selected_ids=selected_ids)
        if preview.get("preview_skipped"):
            # Keep product as-is
            clean = copy.deepcopy(prod)
            clean["floor_applied"] = False
            updated.append(clean)
            continue

        new_prod = copy.deepcopy(prod)
        new_prod["price"] = preview["preview_price"]
        new_prod["compare_at_price"] = preview["preview_compare_at_price"]
        new_prod["floor_applied"] = preview.get("floor_applied", False)
        new_prod["on_sale"] = (
            new_prod["compare_at_price"] is not None
            and float(new_prod["compare_at_price"]) > float(new_prod["price"])
        )

        tags = list(new_prod.get("tags", []))
        if auto_tag and new_prod["on_sale"]:
            if sale_tag not in tags:
                tags.append(sale_tag)
        new_prod["tags"] = tags
        updated.append(new_prod)

    return updated


def restore_products_from_snapshot(
    current_products: List[Dict[str, Any]],
    snapshot_products: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Restores exact pre-sale `price`, `compare_at_price`, `tags`, and `on_sale` state
    from the immutable snapshot.
    """
    snap_by_id = {p["id"]: p for p in snapshot_products}
    restored: List[Dict[str, Any]] = []
    for cur in current_products:
        snap = snap_by_id.get(cur["id"])
        if not snap:
            restored.append(copy.deepcopy(cur))
            continue
        item = copy.deepcopy(cur)
        item["price"] = float(snap["price"])
        item["compare_at_price"] = (
            float(snap["compare_at_price"]) if snap.get("compare_at_price") is not None else None
        )
        item["original_price"] = float(snap.get("original_price", snap["price"]))
        item["original_compare_at_price"] = (
            float(snap["original_compare_at_price"])
            if snap.get("original_compare_at_price") is not None
            else item["compare_at_price"]
        )
        item["tags"] = list(snap.get("tags", []))
        item["original_tags"] = list(snap.get("original_tags", item["tags"]))
        item["on_sale"] = (
            item["compare_at_price"] is not None and item["compare_at_price"] > item["price"]
        )
        item["floor_applied"] = False
        restored.append(item)
    return restored

