from typing import Dict, List, Literal

DistributionMode = Literal["equal", "pyramid_down", "pyramid_up"]


def _round_price(price: float) -> int:
    return max(0, int(round(price)))


def calculate_hdr_range(high_price: int, low_price: int) -> Dict[str, int]:
    if high_price <= 0 or low_price <= 0:
        raise ValueError("가격은 0보다 커야 합니다.")
    if high_price <= low_price:
        raise ValueError("고점은 저점보다 커야 합니다.")

    price_range = high_price - low_price

    return {
        "high": high_price,
        "low": low_price,
        "range": price_range,
        "mid": _round_price((high_price + low_price) / 2),
        "q1": _round_price(low_price + price_range * 0.25),
        "q2": _round_price(low_price + price_range * 0.50),
        "q3": _round_price(low_price + price_range * 0.75),
    }


def get_distribution_weights(split_count: int, mode: DistributionMode = "equal") -> List[float]:
    if split_count <= 0:
        raise ValueError("split_count는 1 이상이어야 합니다.")
    if mode == "equal":
        return [1 / split_count] * split_count
    if mode == "pyramid_down":
        base = list(range(split_count, 0, -1))
        total = sum(base)
        return [x / total for x in base]
    if mode == "pyramid_up":
        base = list(range(1, split_count + 1))
        total = sum(base)
        return [x / total for x in base]
    raise ValueError(f"지원하지 않는 mode입니다: {mode}")

def build_buy_plan(
    high_price: int,
    low_price: int,
    budget: int,
    split_count: int = 3,
    mode: DistributionMode = "equal",
) -> List[Dict]:
    if budget <= 0:
        raise ValueError("budget은 1 이상이어야 합니다.")
    if split_count <= 0:
        raise ValueError("split_count는 1 이상이어야 합니다.")

    hdr = calculate_hdr_range(high_price, low_price)
    weights = get_distribution_weights(split_count, mode)

    upper_price = hdr["q3"]
    lower_price = hdr["low"]

    if split_count == 1:
        price_levels = [hdr["q2"]]
    else:
        step = (upper_price - lower_price) / (split_count - 1)
        price_levels = [_round_price(upper_price - step * i) for i in range(split_count)]
        price_levels[-1] = lower_price

    plan = []
    remaining_budget = budget

    for i, (price, weight) in enumerate(zip(price_levels, weights), start=1):
        allocated_budget = int(budget * weight)
        if i == split_count:
            allocated_budget = remaining_budget

        qty = allocated_budget // price if price > 0 else 0
        used_budget = qty * price
        remaining_budget -= used_budget

        plan.append({
            "step": i,
            "price": price,
            "weight": round(weight, 4),
            "weight_pct": round(weight * 100, 2),
            "allocated_budget": allocated_budget,
            "qty": qty,
            "used_budget": used_budget,
        })

    return plan


def build_sell_plan(
    high_price: int,
    low_price: int,
    holding_qty: int,
    split_count: int = 3,
    mode: DistributionMode = "equal",
) -> List[Dict]:
    if holding_qty <= 0:
        raise ValueError("holding_qty는 1 이상이어야 합니다.")
    if split_count <= 0:
        raise ValueError("split_count는 1 이상이어야 합니다.")

    hdr = calculate_hdr_range(high_price, low_price)
    weights = get_distribution_weights(split_count, mode)

    lower_price = hdr["q2"]
    upper_price = hdr["high"]

    if split_count == 1:
        price_levels = [hdr["q3"]]
    else:
        step = (upper_price - lower_price) / (split_count - 1)
        price_levels = [_round_price(lower_price + step * i) for i in range(split_count)]

    remaining = holding_qty
    plan = []
    for i, (price, w) in enumerate(zip(price_levels, weights)):
        if i == split_count - 1:
            qty = remaining
        else:
            qty = max(1, int(holding_qty * w))
            qty = min(qty, remaining)
        remaining -= qty
        plan.append({
            "step": i + 1,
            "price": price,
            "qty": qty,
            "expected_amount": price * qty,
            "weight": round(w, 4),
            "weight_pct": round(w * 100, 2),
        })

    return plan
