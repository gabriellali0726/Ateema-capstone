
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Dict, List
import streamlit as st

from ateema.io_loader import load_products
from ateema.summit_rules import apply_summit_rules
from ateema.catalog import partition_by_category
from ateema.upgrader import run_fill_to_cap
from ateema.formatting import format_product_block
from ateema.pricing import apply_discounts


# ---------- Awards config (Gabby) ----------
BASE_DIR = Path(__file__).resolve().parents[1]  # .../Final_Model_Ver2.0_Dazhou
AWARDS_PATH = BASE_DIR / "Data" / "PriceStrategy" / "AwardsConfig" / "Summit_Awards.json"

try:
    with AWARDS_PATH.open("r", encoding="utf-8") as f:
        AWARDS_CONFIG = json.load(f)

    # 1) 所有 general_category，用于下拉框
    GENERAL_AWARD_CATEGORIES = sorted(
        {a.get("general_category") for a in AWARDS_CONFIG if a.get("general_category")}
    )

    # 2) 自动生成 Business Type → Award 的映射
    BUSINESS_TYPE_TO_AWARD = {}

    for a in AWARDS_CONFIG:
        award_name = a.get("name")
        gen_cat = a.get("general_category")

        # 2-1. general_category 本身也可以直接映射到 award
        if gen_cat and award_name:
            BUSINESS_TYPE_TO_AWARD[gen_cat] = award_name

        # 2-2. 把 eligible_business_types 也映射到同一个 award
        for bt in a.get("eligible_business_types", []):
            if bt and award_name:
                BUSINESS_TYPE_TO_AWARD.setdefault(bt, award_name)

except Exception as e:
    AWARDS_CONFIG = []
    GENERAL_AWARD_CATEGORIES = []
    BUSINESS_TYPE_TO_AWARD = {}
    print(f"[WARN] Failed to load Summit_Awards.json: {e}")


# ---------- Page ----------
st.set_page_config(page_title="Ateema – Proposal Builder", page_icon="🧭", layout="wide")
st.title("Ateema – Proposal Builder")

# ---------- Sidebar (old layout restored) ----------
DEFAULT_PRODUCTS = r"C:\Users\Dazhou Wu\Desktop\Mine\master\Fall 25\Capstone ii\Model\drive-download-20251118T010756Z-1-001\Data\PriceStrategy"
with st.sidebar:
    st.header("Settings")
    products_path = st.text_input("Products folder", value=DEFAULT_PRODUCTS)
    input_mode = st.radio("Input mode", ["Load JSON", "Survey-style"], horizontal=False)
    # parity settings (not used by deterministic allocator)
    ollama_model = st.text_input("Ollama model", value="gemma3:4b")
    temperature = st.slider("Temperature", 0.0, 1.0, 0.10, 0.05)
    soft_cap_pct = st.slider("Soft cap (+%)", 0, 50, 10, 1)

# ---------- Utils ----------
def list_jsons(folder: Path) -> List[str]:
    try:
        return sorted([str(p) for p in folder.glob("*.json")])
    except Exception:
        return []

def qty_from_tier(tier: str) -> int:
    if not tier:
        return 1
    m = re.fullmatch(r"(\d+)\s*[xX]", tier.strip())
    return int(m.group(1)) if m else 1

def make_reasoning(
    product: str,
    option: str,
    tier: str,
    label: str,
    focus: str,
    market_target: str,
) -> str:
    """
    Produce 2–4 short bullet-point style phrases.
    Clean, scannable, cue-card style.
    """

    info = meta.get(product, {})
    desc = info.get("product_description", "")
    strategy = info.get("sales_strategy", "")
    notes = info.get("option_notes", {}).get(option, "")

    bullets = []

    # 1) Focus + Target — compressed to short phrase
    bullets.append(
        f"Supports {focus.lower()}"
    )
    bullets.append(
        f"Reaches {market_target.lower()}"
    )

    # 2) Option-level advantage — shorten aggressively
    if notes:
        short_notes = notes.strip().split(".")[0]
        bullets.append(short_notes)

    # 3) Product description — only most essential clause
    if desc:
        first_clause = desc.strip().split(".")[0]
        bullets.append(first_clause)

    # 4) Strategy — also shortened
    if strategy:
        short_strategy = strategy.strip().split(".")[0]
        bullets.append(short_strategy)

    # Keep only first 3–4 bullets
    bullets = bullets[:4]

    # Format as bullet-point phrases separated by semicolons
    reasoning = "; ".join(bullets)

    return reasoning


def rows_from_selection(
    label: str,
    sel,
    focus: str,
    market_target: str,
    all_products: set[str],
    prepay_full_year: bool,
    is_advertiser: bool, # Dazhou 11/17 Advertiser
) -> List[Dict]:
    """
    Build table rows for a pool, applying product-specific discounts.

    - unit_price_original: pre-discount unit price from allocator
    - unit_price: discounted unit price (may be same as original)
    """
    out: List[Dict] = []

    for prod, (opt_name, tier, unit_price_original) in sel.picks.items():
        qty = qty_from_tier(tier)
        has_other_products = len(all_products - {prod}) > 0

        # Phase 1 discount engine
        unit_price_discounted, discount_label = apply_discounts(
            product_name=prod,
            option_name=opt_name,
            base_price=unit_price_original,
            tier=tier,
            has_other_products=has_other_products,
            prepay_full_year=prepay_full_year,
            is_advertiser=is_advertiser, # Dazhou 11/17 Advertiser
        )

        line_total_original = unit_price_original * qty
        line_total = unit_price_discounted * qty

        reasoning = make_reasoning(prod, opt_name, tier, label, focus, market_target)

        out.append(
            {
                "product": prod,
                "option": opt_name,
                "qty": qty,
                "unit_price_original": unit_price_original,
                "unit_price": unit_price_discounted,
                "discount": discount_label or "",
                "total_price_original": line_total_original,
                "total_price": line_total,
                "reasoning": reasoning,
            }
        )

    return out


# ---------- Load products ----------
folder = Path(products_path)
if not folder.exists():
    st.error(f"Folder not found: {products_path}")
    st.stop()

found = list_jsons(folder)
if not found:
    st.warning("No *.json files found in the selected folder.")
else:
    with st.expander("Found product files (debug)"):
        for f in found:
            st.write(f)

catalog = {}
meta = {}
try:
    catalog, meta = load_products(folder)
except Exception as e:
    bad = None
    for p in folder.glob("*.json"):
        try:
            _ = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception as sub_e:
            bad = (str(p), str(sub_e))
            break
    if bad:
        st.error(f"Failed to load. Problem file: {bad[0]} — {bad[1]}")
    else:
        st.error(f"Failed to load products: {e}")
    st.stop()

all_names = sorted(catalog.keys())

# ---------- Input areas ----------
profile_text = ""
focus_text = ""
market_text = ""
audience_type_text = ""
is_advertiser = True
total_budget = 45000.0
tourist_pct = 60
industry_pct = 40
prepay_full_year = False          # ← ADD THIS
billing_date = None
chosen: List[str] = []

if input_mode == "Load JSON":
    st.subheader("Load JSON")
    json_path = st.text_input("Input JSON path", value=str(Path(folder.parent, "Inputs", "input.json")))
    if st.button("Generate Proposal", type="primary", key="gen_from_json"):
        raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
        profile = raw.get("client_profile", "")
        total_budget = float(raw.get("budget", 0))
        similar_clients = raw.get("similar_clients", [])
        chosen = list(raw.get("candidate_products", []))

        # Extract focus/target/audience from profile for reasoning
        focus_match = re.search(r"Focus:\s*(.*)", profile, re.IGNORECASE)
        target_match = re.search(r"Market Target:\s*(.*)", profile, re.IGNORECASE)
        audience_match = re.search(r"Audience Type:\s*(.*)", profile, re.IGNORECASE)

        focus_text = focus_match.group(1).strip() if focus_match else ""
        market_text = target_match.group(1).strip() if target_match else ""
        audience_type_text = audience_match.group(1).strip() if audience_match else ""

        profile_text = profile

        st.session_state["_trigger_generate"] = True
        st.session_state["_payload_similar"] = similar_clients

else:
    # Survey-style (vertical order)
    st.subheader("Client Survey")
    billing_date = st.date_input(
        "Billing Date",
        help="This determines seasonal booth pricing such as 4/15–6/14 or 6/15–8/31."
    )
    business_name = st.text_input("Business Name", value="River North Seasonal Kitchen")

# 原有细分 business types
    BASE_BUSINESS_TYPES = [
        "Restaurant - Contemporary American",
        "Restaurant - Other",
        "Bar",
        "Attraction",
        "Retail",
    ]

# 在原有选项后面拼接上 awards 的 general_category
    ALL_BUSINESS_TYPE_OPTIONS = BASE_BUSINESS_TYPES + GENERAL_AWARD_CATEGORIES

    business_type = st.selectbox(
        "Business Type",
        ALL_BUSINESS_TYPE_OPTIONS,
        index=0,
    )
    
    # ---------- Auto-match Summit Awards ----------
    
    matched_award = BUSINESS_TYPE_TO_AWARD.get(business_type)

    if matched_award:
        st.success(f"Matched Summit Award: **{matched_award}**")
    else:
        st.info("No Summit Award automatically matched for this business type.")


    audience_type_text = st.selectbox(
        "Audience Type",
        ["Tourist", "Local", "Meeting and Event Planner"],
        index=0,
        help="Primary audience this proposal is meant to reach.",
    )

    focus_text = st.text_area(
        "Focus (what outcome?)",
        value="Launch seasonal tasting menu; boost lunch & pre-theatre reservations",
        height=80,
    )
    market_text = st.text_area(
        "Market Target (who to reach?)",
        value="Downtown professionals; tourists near River North theatres",
        height=80,
    )


    is_advertiser = st.checkbox("Existing Advertiser?", value=True)


    st.subheader("Budget & Split")
    total_budget = st.number_input("Total Budget (USD)", min_value=0.0, value=45000.0, step=500.0)
    tourist_pct = st.slider("Budget Distribution %", min_value=0, max_value=100, value=60, step=1)
    st.markdown(
        f"<div style='margin-top:-8px;margin-bottom:6px;font-size:18px;font-weight:600;'>Tourist Messaging: {tourist_pct}%  •  Industry Relationship: {100-tourist_pct}%</div>",
        unsafe_allow_html=True
    )
    industry_pct = 100 - tourist_pct

    # Prepay toggle – used for Interactive Map discount
    prepay_full_year = st.checkbox(
        "Prepay eligible annual programs (10% discount on Interactive Map)",
        value=False,
        help="If checked, Interactive Map pricing will reflect a 10% prepay discount where applicable."
    )
    # --- Similar clients (auto-generate) ---
    st.markdown("### Similar Clients")
    k_sim = st.slider("How many similar clients?", 1, 10, 5, 1)
    if st.button("Generate similar clients", type="secondary"):
        try:
            from partner.client_to_product_final import similar_clients_json
            new_client_payload = {
                "Business Name": business_name,
                "Business Type": business_type,
                "Focus": focus_text,
                "Market Target": market_text,
                "Business Description": "",
            }
            sc = similar_clients_json(new_client_payload, k=k_sim)
            st.session_state["_payload_similar"] = sc["similar_clients"]
            lines = [
                f"{d['name']} | {', '.join(d.get('purchased', []))} | {d.get('notes','')}"
                for d in sc["similar_clients"]
            ]
            st.code("\n".join(lines), language="text")
            # helper: seed multiselect
            if st.button("Use products from similar clients"):
                picked = set(chosen)
                for d in sc["similar_clients"]:
                    for p in d.get("purchased", []):
                        if p in all_names:
                            picked.add(p)
                chosen = st.multiselect("Choose candidate products", options=all_names, default=sorted(picked))
        except Exception as e:
            st.error(f"Failed to generate similar clients: {e}")

    # Candidate products – start EMPTY
    chosen = st.multiselect("Choose candidate products", options=all_names, default=[])

    profile_text = (
        f"Business Name: {business_name}\n"
        f"Business Type: {business_type}\n"
        f"Matched Summit Award: {matched_award if matched_award else 'None'}\n"
        f"Audience Type: {audience_type_text}\n"
        f"Focus: {focus_text}\n"
        f"Market Target: {market_text}"
    )


    if st.button("Generate Proposal", type="primary", key="gen_from_survey"):
        st.session_state["_trigger_generate"] = True

# ---------- Output helpers ----------
def make_digital_ads_paragraph(audience_type: str,
                               focus: str,
                               market_target: str,
                               meta: Dict[str, dict]) -> str:
    """
    Build a short reasoning paragraph for Digital Advertising, using
    Additional Digital Advertisement.json as context plus client info.
    Always-on: we call this for every proposal.
    """
    digi_meta = meta.get("Digital Advertisement") or {}
    notes_map = digi_meta.get("notes_map") or {}
    product_desc = digi_meta.get("product_description") or ""

    at = (audience_type or "").lower()

    if "meeting" in at or "planner" in at:
        key = "Meeting and Event Planner Digital ads"
    elif "tour" in at:
        key = "Tourism Digital ads"
    else:
        key = "Local Digital ads"

    opt_note = notes_map.get(key, "")

    parts = []
    parts.append("**Digital Advertising Recommendation**")

    if opt_note:
        parts.append(opt_note)
    elif product_desc:
        parts.append(product_desc)
    else:
        parts.append(
            "Ateema’s digital advertising programs extend your reach beyond print, "
            "concierge, and event-based channels, keeping your business visible "
            "when people are actively deciding where to go and what to book."
        )

    if focus:
        parts.append(
            f"This directly supports your focus on _{focus.strip()}_ by adding "
            "measurable, always-on visibility in digital channels."
        )

    if market_target:
        parts.append(
            f"It is especially effective for reaching your target audience: "
            f"_{market_target.strip()}_."
        )

    return "\n\n".join(parts)


# Dazhou 11/17 Advertiser
def _render_table(label: str, sel, all_products: set[str], prepay_full_year: bool, is_advertiser: bool): # Dazhou 11/17 Advertiser
    import pandas as pd

    rows = rows_from_selection(
        label=label,
        sel=sel,
        focus=focus_text,
        market_target=market_text,
        all_products=all_products,
        prepay_full_year=prepay_full_year,
        is_advertiser=is_advertiser, # Dazhou 11/17 Advertiser
    )
    if rows:
        st.write(f"**Subtotal (before discounts):** ${sel.subtotal:,.0f}")
        df = pd.DataFrame(
            rows,
            columns=[
                "product",
                "option",
                "qty",
                "unit_price_original",
                "unit_price",
                "discount",
                "total_price_original",
                "total_price",
                "reasoning",
            ],
        )
        st.markdown(
            """
            <style>
            table { table-layout: fixed; width: 100%; border-collapse: collapse; }
            thead th { font-weight: 700; font-size: 16px !important; text-align: left; padding: 6px; }
            td { white-space: normal !important; word-wrap: break-word !important; font-size: 15px; vertical-align: top; padding: 6px; }
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.table(df)
    else:
        st.info("No items selected.")
# ---------- Generation ----------
if st.session_state.get("_trigger_generate"):
    st.session_state["_trigger_generate"] = False
    if not chosen:
        st.warning("Select at least one product.")
        st.stop()

    subset = {k: catalog[k] for k in chosen if k in catalog}
    subset = apply_summit_rules(subset, profile_text=profile_text, is_advertiser=is_advertiser)

    t_set, i_set = partition_by_category(subset, {k: meta.get(k, {}) for k in subset.keys()})
    t_sel, i_sel, grand_total = run_fill_to_cap(total_budget, tourist_pct, industry_pct, t_set, i_set, meta, billing_date, is_advertiser=is_advertiser,)

    # set of all selected products (used for bundle discounts)
    all_products = set(t_sel.picks.keys()) | set(i_sel.picks.keys())

    # header ...
    st.subheader("Tourist Pool")
    _render_table("tourist", t_sel, all_products, prepay_full_year, is_advertiser) # Dazhou 11/17 Advertiser
    st.markdown("---")
    st.markdown("---")
    st.subheader("Industry Pool")
    _render_table("industry", i_sel, all_products, prepay_full_year, is_advertiser) # Dazhou 11/17 Advertiser

    st.markdown("---")
    st.markdown(f"### Grand Total: ${grand_total:,.0f} (Hard cap = ${total_budget*1.10:,.0f})")

    with st.expander("Allocator input preview"):
        st.code(format_product_block(subset, {k: meta.get(k, {}) for k in subset.keys()}))

    # --- Always-on Digital Ads recommendation (below allocator preview) ---
    try:
        digi_text = make_digital_ads_paragraph(audience_type_text, focus_text, market_text, meta)
        if digi_text:
            st.markdown("---")
            st.markdown(digi_text)
    except Exception as e:
        st.warning(f"Digital advertising note unavailable: {e}")
