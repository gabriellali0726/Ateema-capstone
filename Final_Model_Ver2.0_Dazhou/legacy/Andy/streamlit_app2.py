
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

# ---------- Page ----------
st.set_page_config(page_title="Ateema – Proposal Builder", page_icon="🧭", layout="wide")
st.title("Ateema – Proposal Builder")

# ---------- Sidebar (old layout restored) ----------
DEFAULT_PRODUCTS = r"C:\Users\fanmu\PycharmProjects\AteemaRag\Data\PriceStrategy"
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

def make_reasoning(product_name: str, option: str, tier: str, pool_label: str,
                   focus: str, market_target: str) -> str:
    bits = []
    bits.append(f"{product_name} – selected '{option}'{(' at ' + tier) if tier and tier.lower()!='base' else ''} for the {pool_label} pool.")
    if focus:
        bits.append(f"Aligns with focus: {focus.strip()}.")
    if market_target:
        bits.append(f"Reaches: {market_target.strip()}.")
    return " ".join(bits[:3])

def rows_from_selection(label: str, sel, focus: str, market_target: str,
                        meta_map: Dict[str, dict]) -> List[Dict]:
    out = []
    for prod, (opt_name, tier, unit_price) in sel.picks.items():
        m = meta_map.get(prod, {}) or {}
        notes_map = m.get("notes_map", {}) or {}
        prod_desc = (m.get("product_description") or "") or ""
        sales_strat = (m.get("sales_strategy") or "") or ""
        opt_note = notes_map.get(opt_name, "")

        # Build reasoning from: notes (option-level), product_description, sales_strategy
        bits = []
        if opt_note:
            bits.append(opt_note.strip())
        if prod_desc:
            bits.append(str(prod_desc).strip())
        if sales_strat:
            bits.append(str(sales_strat).strip())
        # Keep your focus/target tie-in at the end
        if focus:
            bits.append(f"Aligns with focus: {focus.strip()}.")
        if market_target:
            bits.append(f"Reaches: {market_target.strip()}.")

        reasoning = " ".join([b for b in bits if b][:3])  # keep concise, first 2–3

        qty = qty_from_tier(tier)
        line_total = unit_price * qty
        out.append({
            "product": prod,
            "option": opt_name,
            "qty": qty,
            "unit_price": unit_price,
            "total_price": line_total,
            "reasoning": reasoning,
            "discount_policy": _short_policy_text(m.get("discount_policy")),  # NEW column
        })
    return out

def _short_policy_text(policy) -> str:
    # Accepts str or dict; returns a single-line summary
    if policy is None:
        return ""
    if isinstance(policy, str):
        return policy
    if isinstance(policy, dict):
        parts = []
        if "rules" in policy and isinstance(policy["rules"], list):
            parts.extend([str(r) for r in policy["rules"] if r])
        if "stacking_allowed" in policy:
            parts.append(f"stacking_allowed={policy['stacking_allowed']}")
        return " | ".join(parts)
    return str(policy)

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
is_advertiser = True
total_budget = 45000.0
tourist_pct = 60
industry_pct = 40
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

        # Extract focus/target from profile for reasoning
        focus_match = re.search(r"Focus:\s*(.*)", profile, re.IGNORECASE)
        target_match = re.search(r"Market Target:\s*(.*)", profile, re.IGNORECASE)
        focus_text = focus_match.group(1).strip() if focus_match else ""
        market_text = target_match.group(1).strip() if target_match else ""
        profile_text = profile

        st.session_state["_trigger_generate"] = True
        st.session_state["_payload_similar"] = similar_clients

else:
    # Survey-style (vertical order)
    st.subheader("Client Survey")
    client_name = st.text_input("Business Name", value="River North Seasonal Kitchen")
    business_type = st.selectbox("Type", ["Restaurant - Contemporary American", "Restaurant - Other", "Bar", "Attraction", "Retail"], index=0)
    focus_text = st.text_area("Focus (what outcome?)", value="Launch seasonal tasting menu; boost lunch & pre-theatre reservations", height=80)
    market_text = st.text_area("Market Target (who to reach?)", value="Downtown professionals; tourists near River North theatres", height=80)
    is_advertiser = st.checkbox("Existing Advertiser?", value=True)

    st.subheader("Budget & Split")
    total_budget = st.number_input("Total Budget (USD)", min_value=0.0, value=45000.0, step=500.0)
    tourist_pct = st.slider("Tourist Messaging %", min_value=0, max_value=100, value=60, step=1)
    st.markdown(
        f"<div style='margin-top:-8px;margin-bottom:6px;font-size:18px;font-weight:600;'>Current: {tourist_pct}%  •  Industry Relationship: {100-tourist_pct}%</div>",
        unsafe_allow_html=True
    )
    industry_pct = 100 - tourist_pct

    # --- Similar clients (auto-generate) ---
    st.markdown("### Similar Clients")
    k_sim = st.slider("How many similar clients?", 1, 10, 5, 1)
    if st.button("Generate similar clients", type="secondary"):
        try:
            from partner.client_to_product_final import similar_clients_json
            new_client_payload = {
                "Business Name": client_name,
                "Type": business_type,
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
        f"Business Name: {client_name}\n"
        f"Type: {business_type}\n"
        f"Focus: {focus_text}\n"
        f"Market Target: {market_text}"
    )

    if st.button("Generate Proposal", type="primary", key="gen_from_survey"):
        st.session_state["_trigger_generate"] = True

# ---------- Output helpers ----------
def _render_table(label: str, sel):
    import pandas as pd
    rows = rows_from_selection(label, sel, focus_text, market_text)
    if rows:
        st.write(f"**Subtotal:** ${sel.subtotal:,.0f}")
        df = pd.DataFrame(rows, columns=["product", "option", "qty", "unit_price", "total_price", "reasoning"])
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
    t_sel, i_sel, grand_total = run_fill_to_cap(total_budget, tourist_pct, industry_pct, t_set, i_set)

    # header
    st.markdown(f"### {profile_text.splitlines()[0].replace('Business Name:','').strip() or 'Client'}")
    if focus_text:
        st.write(f"**Focus:** {focus_text}")
    if market_text:
        st.write(f"**Market Target:** {market_text}")
    st.write(f"**Budget:** ${total_budget:,.0f} | **Split:** {tourist_pct}% tourist / {industry_pct}% industry")
    st.markdown("---")

    st.subheader("Tourist Pool")
    _render_table("tourist", t_sel)
    st.markdown("---")
    st.subheader("Industry Pool")
    _render_table("industry", i_sel)

    st.markdown("---")
    st.markdown(f"### Grand Total: ${grand_total:,.0f} (Hard cap = ${total_budget*1.10:,.0f})")

    with st.expander("Allocator input preview"):
        st.code(format_product_block(subset, {k: meta.get(k, {}) for k in subset.keys()}))
