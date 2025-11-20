# streamlit_app_1.py
# UI for stateless Step 4–5 generator with: file OR manual input + auto-apply feedback.

import json
import re
from pathlib import Path
import streamlit as st

# Reuse helpers from your minimal pipeline
from legacy.Andy.simple_run import (
    load_products, ensure_candidates, format_product_block,
    format_descriptions_block, to_markdown, Proposal
)
from legacy.Andy.simple_schemas import InputPayload
from langchain_ollama import OllamaLLM
from langchain_core.output_parsers import StrOutputParser

# -------------------------
# Streamlit page settings
# -------------------------
st.set_page_config(page_title="Ateema Proposal Generator", page_icon="🧩", layout="wide")
st.title("Ateema – Stateless Proposal Generator (Step 4–5)")
st.caption("Uses local Ollama (Gemma 3:4b) • Consumes upstream input + product JSONs • Outputs JSON + Markdown")

# -------------------------
# Helpers: parse, retry, cap
# -------------------------
def _extract_first_json_block(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start:end+1] if (start != -1 and end != -1 and end > start) else ""

def _tolerant_parse(raw: str) -> dict:
    if not raw or not raw.strip():
        raise ValueError("Model returned empty output (nothing to parse).")
    try:
        return json.loads(raw)
    except Exception:
        pass
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(),
                     flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'[\x00-\x1F]+', ' ', cleaned)
    cleaned = cleaned.replace('\\n', ' ').replace('\\t', ' ').replace('\r', ' ')
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    block = _extract_first_json_block(cleaned)
    if block:
        return json.loads(block)
    snippet = (raw[:500] + "…") if len(raw) > 500 else raw
    raise ValueError("Could not parse model output as JSON. First 500 chars:\n" + snippet)

def _call_llm_with_retry(prompt_str: str, model_name: str, temperature: float, attempts: int = 2) -> str:
    llm = OllamaLLM(model=model_name, temperature=temperature)
    last = ""
    for _ in range(attempts):
        last = (llm | StrOutputParser()).invoke(prompt_str)
        if last and last.strip():
            return last
    return last

def _enforce_soft_cap(prop: Proposal, budget: float, soft_cap_pct: int) -> Proposal:
    cap = round(budget * (1 + soft_cap_pct/100), 2)
    if prop.subtotal is not None and prop.subtotal > cap:
        prop.selections.sort(key=lambda s: float(s.line_total or 0), reverse=True)
        removed = []
        while len(prop.selections) > 1 and sum(float(s.line_total or 0) for s in prop.selections) > cap:
            removed.append(prop.selections.pop(0))
        prop = Proposal(**prop.model_dump())
        removed_list = ", ".join(f"{r.product_name} (${r.line_total})" for r in removed) or "none"
        note = f"Soft cap enforced at ${cap} (budget ${budget} + {soft_cap_pct}%). Removed: {removed_list}. Final subtotal: ${prop.subtotal}."
        prop.notes = (prop.notes + " " if prop.notes else "") + note
    return prop

def _build_prompt(inp, subset, product_block, desc_block, budget_cap, soft_cap_pct) -> str:
    from legacy.Andy.simple_prompt import PROPOSAL_PROMPT
    # Read survey extras if present; otherwise default
    business_type = st.session_state.get("_survey_business_type", "Local")
    alloc_impact = st.session_state.get("_survey_alloc_impact", 60)
    alloc_awareness = st.session_state.get("_survey_alloc_awareness", 40)

    return PROPOSAL_PROMPT.format(
        profile=inp.client_profile,
        business_type=business_type,
        budget=inp.budget,
        budget_cap=budget_cap,
        soft_cap_pct=soft_cap_pct,
        alloc_impact=alloc_impact,
        alloc_awareness=alloc_awareness,
        similar_clients=json.dumps([sc.model_dump() for sc in inp.similar_clients], ensure_ascii=False, indent=2),
        allowed_products="\n".join(f"- {n}" for n in subset.keys()),
        product_data=product_block,
        product_descriptions=desc_block,
    )

# -------------------------
# Sidebar controls
# -------------------------
with st.sidebar:
    st.header("Settings")
    products_path = st.text_input("Products folder", value=str(Path("../../Data/PriceStrategy")))
    input_mode = st.radio("Input mode", ["Load JSON", "Survey-style"], horizontal=True)
    model_name = st.text_input("Ollama model", value="gemma3:4b")
    temperature = st.slider("Temperature", 0.0, 1.0, 0.1, 0.05)
    soft_cap_pct = st.slider("Soft cap (+%)", 0, 50, 10, 1, help="Allow total up to budget + this %, enforced.")

# We need product names even for manual mode (to offer a multiselect)
try:
    all_products_map = load_products(Path(products_path))
    all_product_names = sorted(all_products_map.keys())
except Exception as e:
    all_products_map, all_product_names = {}, []
    st.sidebar.error(f"Failed to load products: {e}")

# -------------------------
# Input area (file or manual)
# -------------------------
input_payload: InputPayload | None = None

if input_mode == "Load JSON":
    input_path = st.text_input("Input JSON path", value=str(Path("../../Data/Inputs/input.json")))
    st.caption("Tip: switch to **Survey Mode** to type client profile, budget, similar clients, and pick products here.")
    if st.button("Generate Proposal", type="primary"):
        input_payload = InputPayload(**json.loads(Path(input_path).read_text(encoding="utf-8")))

elif input_mode == "Survey-style":
    st.subheader("Survey-style input")
    with st.form("survey_form", clear_on_submit=False):
        colA, colB = st.columns([2,1])
        with colA:
            client_name = st.text_input("Client name", value="")
            business_type = st.selectbox("Business type", options=["Local", "Destination"])
        with colB:
            budget = st.number_input("Total budget (USD)", min_value=0.0, step=500.0, value=10000.0)

        st.markdown("**Focus split** (must total 100%)")
        col1, col2 = st.columns(2)
        with col1:
            alloc_impact = st.number_input("Tourist Messaging (%)", min_value=0, max_value=100, value=60, step=1)
        with col2:
            alloc_awareness = st.number_input("industry relationship (%)", min_value=0, max_value=100, value=40, step=1)

        desc = st.text_area("Extra context (optional)", height=120, placeholder="Audience, timing, campaign goals…")

        st.markdown("**Similar clients (optional)** — one per line: `Name | purchased1, purchased2 | notes`")
        sc_text = st.text_area("Examples:\nRiver North Hotel | ChicagoDoes Interactive Map, Concierge Email Blast | Strong email engagement")

        # We still need the product candidates
        all_products_map = all_products_map if 'all_products_map' in globals() else load_products(Path(products_path))
        all_product_names = sorted(all_products_map.keys())
        candidate_products = st.multiselect("Candidate products (must be in Products folder)", options=all_product_names)

        submit_survey = st.form_submit_button("Generate Proposal", type="primary")

    if submit_survey:
        if alloc_impact + alloc_awareness != 100:
            st.error("Tourist Messaging % + industry relationship % must total 100.")
        elif not client_name.strip():
            st.error("Please enter a client name.")
        elif not candidate_products:
            st.error("Please select at least one candidate product.")
        else:
            # Build profile text from fields
            profile_text = (
                f"Business Name: {client_name}\n"
                f"Type: {business_type}\n"
                f"Focus: Tourist Messaging {alloc_impact}% / industry relationship {alloc_awareness}%\n"
                f"Additional Notes: {desc.strip() if desc else ''}"
            )

            # Parse similar clients from textarea
            from legacy.Andy.simple_schemas import SimilarClient, InputPayload
            similar_clients = []
            for line in (sc_text or "").splitlines():
                parts = [p.strip() for p in line.split("|")]
                if not parts or not parts[0]:
                    continue
                name = parts[0]
                purchased = [p.strip() for p in parts[1].split(",")] if len(parts) >= 2 and parts[1] else []
                notes = parts[2] if len(parts) >= 3 and parts[2] else None
                similar_clients.append(SimilarClient(name=name, purchased=purchased, notes=notes))

            # Create the normal InputPayload (we keep your pipeline intact)
            input_payload = InputPayload(
                client_profile=profile_text,
                budget=float(budget),
                similar_clients=similar_clients,
                candidate_products=candidate_products,
            )

            # Stash the extra structured fields for the prompt formatter
            st.session_state["_survey_business_type"] = business_type
            st.session_state["_survey_alloc_impact"] = int(alloc_impact)
            st.session_state["_survey_alloc_awareness"] = int(alloc_awareness)

# -------------------------
# Proposal generation flow
# -------------------------
if input_payload is not None:
    try:
        # Whitelist candidate products from folder
        present_real, missing_orig, name_map = ensure_candidates(input_payload.candidate_products, all_products_map)
        if not present_real:
            st.error("No candidate products matched. Check names or normalization against product files.")
            st.stop()

        subset = {name: all_products_map[name] for name in present_real}
        product_block = format_product_block(subset)
        desc_block = format_descriptions_block(subset)

        # Call Ollama
        cap = round(input_payload.budget * (1 + soft_cap_pct/100), 2)
        prompt_str = _build_prompt(input_payload, subset, product_block, desc_block, cap, soft_cap_pct)

        with st.status("Calling local model…", expanded=False) as status:
            raw = _call_llm_with_retry(prompt_str, model_name, temperature, attempts=2)
            status.update(state="complete")

        data = _tolerant_parse(raw)
        prop = Proposal(**data)
        prop = _enforce_soft_cap(prop, input_payload.budget, soft_cap_pct)

        # UI: Left = proposal table; Right = inputs + debug
        c1, c2 = st.columns([2, 1])

        with c1:
            st.subheader("Proposal")
            st.markdown(to_markdown(prop))
            st.divider()
            st.json(prop.model_dump(), expanded=False)

            # Downloads
            st.download_button("Download proposal.json",
                               data=json.dumps(prop.model_dump(), ensure_ascii=False, indent=2),
                               file_name="../Data/Inputs/proposal.json",
                               mime="application/json")
            st.download_button("Download proposal.md",
                               data=to_markdown(prop),
                               file_name="proposal.md",
                               mime="text/markdown")

        with c2:
            st.subheader("Inputs Summary")
            st.write("**Client Profile**")
            st.caption(input_payload.client_profile)
            st.write("**Budget**: $", input_payload.budget, " (Soft cap $", cap, ")")
            st.write("**Candidate Products (matched)**")
            st.code("\n".join(present_real))
            if name_map:
                st.write("**Name normalization**")
                st.json(name_map, expanded=False)
            if missing_orig:
                st.warning(f"Missing from folder: {missing_orig}")

            with st.expander("Product data sent to model"):
                st.code(product_block)
            with st.expander("Product descriptions sent to model"):
                st.code(desc_block)

        st.success("Done.")

        # Cache latest context for the auto-apply feedback section
        st.session_state["_latest_inp"] = input_payload
        st.session_state["_latest_prop"] = prop
        st.session_state["_latest_blocks"] = (product_block, desc_block)
        st.session_state["_latest_cap"] = cap
        st.session_state["_latest_model"] = model_name
        st.session_state["_latest_temp"] = temperature
        st.session_state["_latest_softcap_pct"] = soft_cap_pct

    except Exception as e:
        st.exception(e)

# -------------------------
# Auto-apply Feedback (Regenerate Proposal)
# -------------------------
st.divider()
st.subheader("🔁 Auto-apply Feedback (Regenerate Proposal)")

st.caption(
    "Type instructions like: "
    "“remove Chicago Does Reels”, “switch Interactive Map to 1/2 Panel”, "
    "“keep under $7,000; prioritize email over booth”. "
    "The model will return a **revised proposal JSON** only."
)

if "feedback_history" not in st.session_state:
    st.session_state.feedback_history = []

feedback_text = st.text_area("Edit instructions", height=100, placeholder="e.g., Remove Chicago Does Reels and keep the total below $7,000.")
apply_btn = st.button("Apply & Regenerate", type="primary")

def _build_revision_prompt(inp, prop, product_block, desc_block, cap, soft_cap_pct, feedback_history, feedback_now):
    return f"""
You are a proposal reviser. Update the proposal strictly according to the user's instructions.
Return ONLY the revised proposal JSON. No prose or markdown.

=== Schema (must match exactly) ===
{{
  "client_name": "<string>",
  "budget": {inp.budget},
  "currency": "USD",
  "selections": [
    {{
      "product_name": "<exact name from Allowed Products>",
      "chosen_option": "<exact option/item shown in Product Data>",
      "chosen_price_window": "<string or ''>",
      "unit_price": <number>,
      "qty": <integer>,
      "line_total": <number>,
      "reasoning": "<3–5 sentences grounded ONLY in given descriptions, sales_strategy, discount_policy>"
    }}
  ],
  "subtotal": <number>,
  "notes": "<brief overall note>"
}}

=== Hard limits ===
- Total must be <= ${cap} (budget + {soft_cap_pct}%).
- Use ONLY the products/options exactly as written in Product Data.
- If a product is removed, adjust totals accordingly.
- Keep qty = 1 unless texts justify multiples.

=== Context ===
Client Profile:
{inp.client_profile}

Product Data:
{product_block}

Product Descriptions:
{desc_block}

Current Proposal JSON:
{json.dumps(prop.model_dump(), ensure_ascii=False, indent=2)}

Previous feedback (most recent first):
{json.dumps(list(reversed(feedback_history))[:5], ensure_ascii=False, indent=2)}

New feedback to apply now:
{feedback_now}

Return ONLY the revised proposal JSON, nothing else.
"""

if apply_btn:
    if "_latest_prop" not in st.session_state:
        st.warning("Generate a proposal first, then apply feedback.")
    elif not feedback_text.strip():
        st.warning("Please enter feedback instructions.")
    else:
        inp = st.session_state["_latest_inp"]
        prop = st.session_state["_latest_prop"]
        product_block, desc_block = st.session_state["_latest_blocks"]
        cap = st.session_state["_latest_cap"]
        model_name = st.session_state.get("_latest_model", "gemma3:4b")
        temperature = st.session_state.get("_latest_temp", 0.1)
        soft_cap_pct = st.session_state.get("_latest_softcap_pct", 10)

        revision_prompt = _build_revision_prompt(
            inp, prop, product_block, desc_block, cap, soft_cap_pct,
            st.session_state.feedback_history, feedback_text
        )

        with st.status("Applying feedback and regenerating…", expanded=False) as status:
            raw_rev = _call_llm_with_retry(revision_prompt, model_name, temperature, attempts=2)
            status.update(state="complete")

        try:
            rev_data = _tolerant_parse(raw_rev)
            revised_prop = Proposal(**rev_data)
            revised_prop = _enforce_soft_cap(revised_prop, inp.budget, soft_cap_pct)

            st.session_state["_latest_prop"] = revised_prop
            st.session_state.feedback_history.append(feedback_text)

            st.success("Updated proposal applied.")
            st.markdown(to_markdown(revised_prop))
            st.json(revised_prop.model_dump(), expanded=False)

            st.download_button(
                "Download revised proposal.json",
                data=json.dumps(revised_prop.model_dump(), ensure_ascii=False, indent=2),
                file_name="proposal_revised.json",
                mime="application/json",
            )
            st.download_button(
                "Download revised proposal.md",
                data=to_markdown(revised_prop),
                file_name="proposal_revised.md",
                mime="text/markdown",
            )
        except Exception as e:
            st.error("The model did not return valid proposal JSON. Showing raw output for debugging:")
            st.code(raw_rev[:2000])
            st.exception(e)
