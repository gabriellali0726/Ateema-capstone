from datetime import datetime
from __future__ import annotations
from pathlib import Path
import argparse

from ateema.io_loader import load_products
from ateema.catalog import partition_by_category
from ateema.budget import print_pool_audit
from ateema.upgrader import run_fill_to_cap
from ateema.formatting import format_product_block, print_selection
from ateema.summit_rules import apply_summit_rules

def main():
    ap = argparse.ArgumentParser(description="Ateema CLI runner")
    ap.add_argument("--products", required=True, help="Folder with product JSONs")
    ap.add_argument("--filter", nargs="*", help="Optional list of product names to include")
    ap.add_argument("--budget", type=float, default=45000.0)
    ap.add_argument("--tourist-pct", type=float, default=60.0)
    ap.add_argument("--industry-pct", type=float, default=40.0)
    ap.add_argument("--profile-text", type=str, default="", help="Client profile text for Summit mapping")
    ap.add_argument("--billing-date", type=str, default=None, help="Billing date YYYY-MM-DD for seasonal pricing")
    ap.add_argument("--is-advertiser", action="store_true", help="Client is an advertiser")
    args = ap.parse_args()
    chosen_date = None
    if args.billing_date:
        try:
            chosen_date = datetime.strptime(args.billing_date, "%Y-%m-%d").date()
        except Exception:
            print("Invalid --billing-date format. Use YYYY-MM-DD.")

    catalog, meta = load_products(Path(args.products))
    if args.filter:
        catalog = {k: v for k, v in catalog.items() if k in args.filter}
        meta = {k: meta[k] for k in catalog.keys()}

    catalog = apply_summit_rules(catalog, profile_text=args.profile_text, is_advertiser=args.is_advertiser)

    t_set, i_set = partition_by_category(catalog, meta)
    print_pool_audit(args.budget, args.tourist_pct, args.industry_pct, t_set, i_set)

    t_sel, i_sel, total = run_fill_to_cap(args.budget, args.tourist_pct, args.industry_pct, t_set, i_set, meta, chosen_date)
    print_selection("Tourist Pool", t_sel, catalog)
    print_selection("Industry Pool", i_sel, catalog)
    print(f"\nGrand total: ${total:,.0f}  (hard cap = ${args.budget*1.10:,.0f})")

    print("\n--- Product Preview ---")
    print(format_product_block(catalog, meta))

if __name__ == "__main__":
    main()
