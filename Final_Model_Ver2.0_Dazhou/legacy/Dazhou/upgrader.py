from __future__ import annotations
from typing import Dict, Tuple, Optional
from datetime import date

from ateema.pricing import (
    price_points,
    effective_line_price,
    get_effective_unit_price,
)
from ateema.models import Selection, ProductRecord

import re

def greedy_fill_to_cap(budget: float,
                       products: Dict[str, ProductRecord],
                       meta: Dict[str, dict],
                       chosen_date: Optional[date],
                       is_advertiser: bool = False) -> Selection:
    picks = {}
    subtotal = 0.0

    # baseline picks
    for pname, product in products.items():
        best = None
        best_line = None
        for opt in product.price_options:
            opt_name = opt.get("name", pname)

             # Dazhou 11/17 Advertiser: Summit Booth differentiate price of 
            if "summit" in pname.lower() and "booth" in pname.lower():
                low = opt_name.lower()
                has_non = ("non-advertiser" in low) or ("non advertiser" in low)
                has_adv = ("advertiser" in low) and (not has_non)

                if is_advertiser:
                    # 现有广告主：跳过非广告主专属价，其余都可以（含 generic）
                    if has_non:
                        continue
                else:
                    # 非广告主：跳过“纯 advertiser”专属价，保留 non-advertiser 和 generic
                    if has_adv:
                        continue

            for lbl, base_price in price_points(opt):
                # apply seasonal price and advertiser override
                eff_base = get_effective_unit_price(pname, opt_name, base_price, meta.get(pname, {}), chosen_date,is_advertiser=is_advertiser,) # Dazhou 11/17 Advertiser
                line = effective_line_price(product, lbl, eff_base)
                
                
                if best is None or line < best_line:
                    best = (opt_name, lbl, eff_base)
                    best_line = line

        if best:
            opt_name, lbl, eff_base = best
            subtotal += effective_line_price(product, lbl, eff_base)
            picks[pname] = (opt_name, lbl, eff_base)

    # upgrade loop
    improved = True
    while improved:
        improved = False
        for pname, (cur_opt, cur_lbl, cur_base) in list(picks.items()):
            product = products[pname]
            cur_line = effective_line_price(product, cur_lbl, cur_base)

            upgrades = []
            for opt in product.price_options:
                opt_name = opt.get("name", pname)

                # Keep the filter of advertiser/non-advertiser 
                if "summit" in pname.lower() and "booth" in pname.lower():
                    low = opt_name.lower()
                    has_non = ("non-advertiser" in low) or ("non advertiser" in low)
                    has_adv = ("advertiser" in low) and (not has_non)

                    if is_advertiser:
                        # 现有广告主：跳过非广告主专属价，其余都可以（含 generic）
                        if has_non:
                            continue
                    else:
                        # 非广告主：跳过“纯 advertiser”专属价，保留 non-advertiser 和 generic
                        if has_adv:
                            continue

                for lbl, base_price in price_points(opt):
                    eff_base = get_effective_unit_price(pname, opt_name, base_price, meta.get(pname, {}), chosen_date,is_advertiser=is_advertiser,) # Dazhou 11/17 Advertiser
                    line = effective_line_price(product, lbl, eff_base)
                    if line > cur_line:
                        upgrades.append((opt_name, lbl, eff_base, line))

            upgrades.sort(key=lambda x: x[3])

            for opt_name, lbl, eff_base, new_line in upgrades:
                if subtotal - cur_line + new_line <= budget:
                    subtotal = subtotal - cur_line + new_line
                    picks[pname] = (opt_name, lbl, eff_base)
                    improved = True
                    break

    return Selection(picks=picks, subtotal=round(subtotal, 2))


def run_fill_to_cap(total_budget: float,
                    tourist_pct: float,
                    industry_pct: float,
                    t_set: Dict[str, ProductRecord],
                    i_set: Dict[str, ProductRecord],
                    meta: Dict[str, dict],
                    chosen_date: Optional[date] = None,
                    is_advertiser: bool = False):

    t_budget = total_budget * (tourist_pct / 100.0)
    i_budget = total_budget * (industry_pct / 100.0)

    t_sel = greedy_fill_to_cap(t_budget, t_set, meta, chosen_date, is_advertiser = is_advertiser) # Dazhou 11/17 Advertiser
    i_sel = greedy_fill_to_cap(i_budget, i_set, meta, chosen_date, is_advertiser = is_advertiser) # Dazhou 11/17 Advertiser

    grand_total = round(t_sel.subtotal + i_sel.subtotal, 2)
    return t_sel, i_sel, grand_total
