from __future__ import annotations
from typing import Any, List, Tuple, Optional
from datetime import date
from .models import ProductRecord

# ================================================================
# Helper: parse mm/dd -> mmdd integer
# ================================================================
def _mmdd(d: date) -> int:
    return d.month * 100 + d.day

# ================================================================
# Exact seasonal windows (non-overlapping, lowest-price priority)
# ================================================================
EXACT_WINDOWS = {
    "4/15-6/14": (415, 614),
    "6/15-8/31": (615, 831),
    "9/1-9/30": (901, 930),
}

# ================================================================
# Named seasonal windows (non-overlapping)
# ================================================================
NAMED_WINDOWS = {
    "Before Valentine's Day": (101, 213),
    "After Valentine's Day": (214, 414),
    "Before Halloween": (1001, 1031),
    "Before Christmas": (1101, 1224),
}

# ================================================================
# Determine seasonal booth price
# ================================================================
def booth_season_for_date(meta: dict, option_name: str, chosen_date: date) -> float | None:
    seasonal_map = meta.get("seasonal_price_windows", {})
    if not seasonal_map:
        return None

    mmdd = _mmdd(chosen_date)

    # 1) Exact windows first (always lowest)
    for label, (start, end) in EXACT_WINDOWS.items():
        if label in seasonal_map:
            if start <= mmdd <= end:
                return float(seasonal_map[label].get(option_name, None))

    # 2) Named windows after exact windows
    for label, (start, end) in NAMED_WINDOWS.items():
        if label in seasonal_map:
            if start <= mmdd <= end:
                return float(seasonal_map[label].get(option_name, None))

    return None

# ================================================================
# Existing helpers
# ================================================================
def price_points(opt: dict) -> List[tuple[str, float]]:
    items = []
    if isinstance(opt.get("price_usd"), dict):
        items = [(str(k), float(v)) for k, v in opt["price_usd"].items()]
    elif isinstance(opt.get("price_usd_by_plan"), dict):
        items = [(str(k), float(v)) for k, v in opt["price_usd_by_plan"].items()]
    elif isinstance(opt.get("price_usd"), (int, float)):
        items = [("base", float(opt["price_usd"]))]
    elif isinstance(opt.get("pricing"), dict):
        items = [(str(k), float(v)) for k, v in opt["pricing"].items()]
    else:
        items = []

    def _key(lbl: str, price: float):
        num = "".join(ch for ch in lbl if ch.isdigit() or ch == ".")
        try:
            x = float(num)
        except:
            x = float("inf")
        return (x, price)

    return sorted(items, key=lambda t: _key(t[0], t[1]))

def option_min_budget(opt: dict) -> float:
    v = opt.get("target_budget_min")
    try:
        return float(v) if v is not None else 0.0
    except:
        return 0.0

def effective_line_price(product: ProductRecord, tier_label: str, base_price: float) -> float:
    if not product or not product.duration_quarter_map:
        return base_price
    lbl = (tier_label or "").upper()
    dur_raw = product.duration_quarter_map.get(lbl, 1)
    try:
        duration = float(dur_raw)
    except:
        duration = 1.0
    return base_price * max(duration, 1.0)

def baseline_pick(product: ProductRecord) -> tuple[str, str, float] | None:
    best = None
    best_line = None
    for opt in product.price_options:
        opt_name = opt.get("name", product.name)
        for lbl, base_price in price_points(opt):
            line = effective_line_price(product, lbl, base_price)
            if best is None or line < best_line:
                best = (opt_name, lbl, base_price)
                best_line = line
    return best

def upgrade_candidates(product: ProductRecord, current: tuple[str, str, float] | None):
    cur_line = 0.0
    if current:
        _, cur_lbl, cur_base = current
        cur_line = effective_line_price(product, cur_lbl, cur_base)

    cands = []
    for opt in product.price_options:
        opt_name = opt.get("name", product.name)
        min_budget = option_min_budget(opt)
        for lbl, base_price in price_points(opt):
            line = effective_line_price(product, lbl, base_price)
            if line > cur_line:
                cands.append((opt_name, lbl, base_price, min_budget))

    cands.sort(key=lambda t: effective_line_price(product, t[1], t[2]))
    return cands

def first_known_price(opt: dict) -> float | None:
    if isinstance(opt.get("price_usd"), dict):
        return max(float(v) for v in opt["price_usd"].values())
    if isinstance(opt.get("price_usd_by_plan"), dict):
        return max(float(v) for v in opt["price_usd_by_plan"].values())
    if isinstance(opt.get("price_usd"), (int, float)):
        return float(opt["price_usd"])
    if isinstance(opt.get("pricing"), dict):
        return max(float(v) for v in opt["pricing"].values())
    return None

# ================================================================
# Apply seasonal pricing BEFORE discounts | advertiser overrides (Dazhou 11/17)
# ================================================================
def get_effective_unit_price(product_name: str,
                             option_name: str,
                             base_price: float,
                             meta: dict,
                             chosen_date: Optional[date],
                             is_advertiser: bool = False) -> float:
    eff = base_price
    if chosen_date:
        seasonal = booth_season_for_date(meta, option_name, chosen_date)
        if seasonal is not None:
            eff = seasonal

    adv_price, _ = advertiser_overrides(
        product_name=product_name,
        option_name=option_name,
        base_price=eff,
        tier="",                
        is_advertiser=is_advertiser,
    )
    if adv_price is not None:
        return adv_price

    return eff

# ================================================================
# Advertiser-specific price overrides (Dazhou 11/17)
# ================================================================
def advertiser_overrides(
    product_name: str,
    option_name: str,
    base_price: float,
    tier: str,
    is_advertiser: bool,
) -> tuple[float | None, str | None]:
    """
    If the client is an existing advertiser, override the line price
    for certain products (Email Blast / Ambassador / Summit Booth).

    Return:
      (new_price, label)  -> to apply an override
      (None, None)        -> if no override should be applied
    """
    if not is_advertiser:
        # 非广告客户，不做任何特殊价格
        return None, None

    name = (product_name or "").strip()
    opt  = (option_name or "").strip()

    # TODO: 你之后在这里填具体规则
    # 例子结构（先占坑，具体金额你后面给我）：

    # 1) Email Blast 系列
    if name == "Email Blast" and "Blast Email - concierge" in opt:
            if is_advertiser:
                new_price = 450.0  # Contract with multiple products
                label = "Existing advertiser contract rate"
                return new_price, label
            # 非 advertiser 继续走默认折扣逻辑
            return None, None

    # 2) Ambassador Program 系列
    if name == "Ambassador Program":
        # 标准项目
        if opt.startswith("Standard Ambassador Program"):
            # new_price = ...
            # label = "Existing advertiser rate"
            # return new_price, label
            return None, None  # 占位

        # Concierge Intro 项目
        if opt.startswith("Ambassador - Concierge Intro"):
            # new_price = ...
            # label = "Existing advertiser rate"
            # return new_price, label
            return None, None  # 占位

    # 3) Summit Booth 系列
    # 注意：Summit Booth 的产品名在 JSON 里是 "Summit — Booth"
    if name == "Summit — Booth":
        # 这里你可以根据 tier/option name 决定专门价格
        # 例如：
        # if "Basic Booth — advertiser rate" in opt:
        #     new_price = ...
        #     label = "Existing advertiser special"
        #     return new_price, label
        return None, None  # 占位

    # 没有匹配任何 advertiser 规则，就走默认折扣逻辑
    return None, None

# ================================================================
# Discount Logic (unchanged)
# ================================================================
def apply_discounts(
    product_name: str,
    option_name: str,
    base_price: float,
    tier: str,
    has_other_products: bool,
    prepay_full_year: bool,
    is_advertiser: bool, 
) -> tuple[float, str | None]:

    name = (product_name or "").strip()
    opt = (option_name or "").strip()
    label = None
    final_price = float(base_price)
    
    # Check whether the product have advertiser discounts (Dazhou 11/17)
    adv_price, adv_label = advertiser_overrides(
        product_name=name,
        option_name=opt,
        base_price=final_price,
        tier=tier,
        is_advertiser=is_advertiser,
    )
    if adv_price is not None:
        return adv_price, adv_label
    
    if name == "Email Blast" and "Blast Email - concierge" in opt:
        if has_other_products:
            return 450.0, "Contract bundle price (with other products)"
        return 750.0, None

    if name == "Email Blast" and "Planner Eblast" in opt:
        return final_price, None

    if name == "Chicago Does Reels":
        if has_other_products:
            return 895.0, "With other purchase discount"
        return 995.0, None

    if name == "Ambassador Program":
        if opt.startswith("Standard Ambassador Program"):
            retail, with_campaign = 3200.0, 2950.0
        elif opt.startswith("Ambassador - Concierge Intro"):
            retail, with_campaign = 3000.0, 2750.0
        else:
            return final_price, None
        return (with_campaign, "With Any Campaign rate") if has_other_products else (retail, None)

    if name == "Chicago Does Interactive Map":
        if prepay_full_year:
            return round(base_price * 0.9, 2), "Prepay entire year – 10% off"
        return final_price, None

    return final_price, None

