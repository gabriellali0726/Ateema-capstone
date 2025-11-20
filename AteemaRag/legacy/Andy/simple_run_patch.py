
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple
import json
import argparse
from dataclasses import asdict

def read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class ProductRecord:
    name: str
    price_options: List[Dict[str, Any]]
    discount_policy: Any = None
    sales_strategy: Any = None
    description: str = ""


def load_products(dir_path: Path) -> Tuple[Dict[str, ProductRecord], Dict[str, dict]]:
    out: Dict[str, ProductRecord] = {}
    meta: Dict[str, dict] = {}

    for p in sorted(dir_path.glob("*.json")):
        obj = read_json(p)
        name = obj.get("name") or obj.get("product_name") or obj.get("product_id") or p.stem

        rec = ProductRecord(
            name=name,
            price_options=obj.get("price_options", obj.get("options", [])) or [],
            discount_policy=obj.get("discount_policy"),
            sales_strategy=obj.get("sales_strategy"),
            description=obj.get("product_description") or obj.get("description") or "",
        )
        out[name] = rec

        dur = obj.get("duration_quarter_map") or {}
        dur_up = { (k.upper() if isinstance(k, str) else k): v for k, v in dur.items() }

        meta[name] = {
            "category": obj.get("category"),
            "billing_period": obj.get("billing_period"),
            "duration_quarter_map": dur_up,
        }

    return out, meta


def normalize_category(raw: str | None) -> str | None:
    if not raw:
        return None
    r = raw.strip().lower()
    if "tourist" in r:
        return "tourist"
    if "industry" in r:
        return "industry"
    return r  # fallback

@dataclass
class PoolInfo:
    label: str
    budget: float
    subtotal: float = 0.0
    items: List[str] = None

    def __post_init__(self):
        if self.items is None:
            self.items = []

def partition_by_category(catalog: Dict[str, ProductRecord], meta: Dict[str, dict]) -> Tuple[Dict[str, ProductRecord], Dict[str, ProductRecord]]:
    tourist, industry = {}, {}
    for name, rec in catalog.items():
        cat = normalize_category((meta.get(name) or {}).get("category"))
        if cat == "tourist":
            tourist[name] = rec
        elif cat == "industry":
            industry[name] = rec
        # else: ignore/misc bucket for now
    return tourist, industry

def compute_pools(total_budget: float, tourist_pct: float, industry_pct: float) -> Tuple[PoolInfo, PoolInfo]:
    # permissive rounding to keep cents out of the way
    t_budget = round(total_budget * (tourist_pct / 100.0), 2)
    i_budget = round(total_budget * (industry_pct / 100.0), 2)
    return PoolInfo("tourist", t_budget), PoolInfo("industry", i_budget)

def print_pool_audit(total_budget: float, tourist_pct: float, industry_pct: float,
                     t_set: Dict[str, ProductRecord], i_set: Dict[str, ProductRecord]) -> None:
    t_pool, i_pool = compute_pools(total_budget, tourist_pct, industry_pct)
    print("\n=== Focus Split & Pools ===")
    print(f"Total budget: ${total_budget:,.0f} | split: {tourist_pct:.1f}% tourist / {industry_pct:.1f}% industry")
    print(f"Tourist pool:  ${t_pool.budget:,.0f}  | {len(t_set)} products: {', '.join(t_set.keys()) or '—'}")
    print(f"Industry pool: ${i_pool.budget:,.0f}  | {len(i_set)} products: {', '.join(i_set.keys()) or '—'}")
    # Acceptance target example for 45k/60-40
    soft_example = (total_budget == 45000 and abs(tourist_pct - 60) < 1e-6)
    if soft_example:
        print("Acceptance guidance: spend ≥ $27k tourist and ≥ $18k industry unless pool is capped by available SKUs.")

# OPTIONAL: a unified price accessor for later steps (fill-to-cap etc.)
def first_known_price(opt: Dict[str, Any]) -> float | None:
    """
    Returns a representative price from an option for auditing or simple heuristics.
    Handles both {'price_usd': {plan: price}} and {'price_usd_by_plan': {...}} and {'price_usd': number}.
    """
    if isinstance(opt.get("price_usd"), dict):
        # pick the priciest plan as a conservative estimate
        return max(opt["price_usd"].values())
    if isinstance(opt.get("price_usd_by_plan"), dict):
        return max(opt["price_usd_by_plan"].values())
    if isinstance(opt.get("price_usd"), (int, float)):
        return float(opt["price_usd"])
    if isinstance(opt.get("pricing"), dict):
        return max(opt["pricing"].values())
    return None

def _fmt_money(v: Any) -> str:
    try:
        return f"${float(v):,.0f}"
    except Exception:
        return str(v)


def _format_option(prod_name: str, opt: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    oname = opt.get("name") or "Option"

    map_keys = ["price_usd_by_plan", "price_usd"]
    price_map = None
    for k in map_keys:
        if isinstance(opt.get(k), dict):
            price_map = opt[k]
            break
    if price_map is None and isinstance(opt.get("pricing"), dict):
        price_map = opt["pricing"]

    if price_map is not None:
        parts = [f"{plan}: {_fmt_money(val)}" for plan, val in price_map.items()]
        price_str = "; ".join(parts)
        lines.append(f"- {oname} — {price_str}")
    else:
        price = opt.get("price_usd", None)
        if price is None:
            lines.append(f"- {oname}")
        else:
            lines.append(f"- {oname} — {_fmt_money(price)}")

    if opt.get("bundle_qty"):
        lines.append(f"  bundle_qty: {opt['bundle_qty']}")
    if opt.get("min_qty") or opt.get("max_qty"):
        mq = opt.get("min_qty", 1)
        Mq = opt.get("max_qty")
        if Mq is not None:
            lines.append(f"  qty_bounds: {mq}–{Mq}")
        else:
            lines.append(f"  min_qty: {mq}")

    notes = opt.get("notes")
    if isinstance(notes, str) and notes.strip():
        lines.append(f"  notes: {notes.strip()}")

    return lines


def format_product_block(products: Dict[str, ProductRecord], meta: Dict[str, dict]) -> str:
    lines: List[str] = []
    for name, rec in products.items():
        m = meta.get(name, {})
        bp = m.get("billing_period")
        dur = m.get("duration_quarter_map") or {}

        lines.append(f"## {name}")
        if bp:
            lines.append(f"- billing_period: {bp}")
        if dur:
            pretty = ", ".join(f"{k}={v}q" for k, v in dur.items() if v is not None)
            if pretty:
                lines.append(f"- duration_quarter_map: {pretty}")

        for opt in rec.price_options:
            if isinstance(opt, dict):
                lines.extend(_format_option(name, opt))
            else:
                lines.append(f"- {json.dumps(opt, ensure_ascii=False)}")

        if rec.discount_policy is not None:
            lines.append("Discount Policy: " + json.dumps(rec.discount_policy, ensure_ascii=False))
        if rec.sales_strategy:
            lines.append("Sales Strategy: " + str(rec.sales_strategy))

        lines.append("")

    return "\n".join(lines).strip()


def format_descriptions_block(products: Dict[str, ProductRecord]) -> str:
    parts = []
    for name, rec in products.items():
        if rec.description:
            parts.append(f"### {name}\n{rec.description}")
    return "\n\n".join(parts)


def _main_cli() -> None:
    ap = argparse.ArgumentParser(description="Preview product blocks with new loader/meta.")
    ap.add_argument("--products", type=str, required=True, help="Folder containing product JSON files")
    ap.add_argument("--filter", type=str, nargs="*", default=None, help="Optional list of product names to include")

    # NEW: focus split & pools
    ap.add_argument("--budget", type=float, default=45000.0)
    ap.add_argument("--tourist-pct", type=float, default=60.0)
    ap.add_argument("--industry-pct", type=float, default=40.0)

    args = ap.parse_args()
    catalog, meta = load_products(Path(args.products))

    if args.filter:
        catalog = {k: v for k, v in catalog.items() if k in args.filter}
        meta = {k: meta[k] for k in catalog.keys()}

    # Step 2 partition + audit
    t_set, i_set = partition_by_category(catalog, meta)
    print_pool_audit(args.budget, args.tourist_pct, args.industry_pct, t_set, i_set)

    # Keep your existing preview if you want
    print("\n--- Product Preview ---")
    print(format_product_block(catalog, meta))


if __name__ == "__main__":
    _main_cli()
