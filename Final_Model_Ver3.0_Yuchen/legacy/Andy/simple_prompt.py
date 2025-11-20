from langchain_core.prompts import PromptTemplate


PROPOSAL_PROMPT = PromptTemplate.from_template("""
You are an AI media strategist. Construct a proposal using ONLY the provided products, price options,
discount policy, sales strategy, product descriptions, and inputs. Output STRICT JSON matching the schema.

=== New Client Profile ===
{profile}

=== Business Type ===
{business_type}

=== Budget (USD) ===
{budget}

=== Budget Split ===
Immediate Impact: {alloc_impact}%   |   Brand Awareness: {alloc_awareness}%

=== Soft Budget Cap (hard limit) ===
Total MUST be <= ${budget_cap} (i.e., {soft_cap_pct}% above budget). Prefer staying within {budget}.

=== Similar Clients ===
{similar_clients}

=== Allowed Products (exact names only) ===
{allowed_products}

=== Product Data (verbatim) ===
{product_data}

=== Product Descriptions (verbatim) ===
{product_descriptions}

RETURN STRICT JSON ONLY:
{{
  "client_name": "<from profile or given>",
  "budget": {budget},
  "currency": "USD",
  "selections": [
    {{
      "product_name": "<exact product name from Allowed Products>",
      "chosen_option": "<exact option/item name shown in Product Data>",
      "chosen_price_window": "<label if applicable, else ''>",
      "unit_price": <number>,
      "qty": <integer>,
      "line_total": <number>,
      "reasoning": "1-2 short sentences. Tie (a) the client's goals/profile & business type, (b) product description benefits/audience (quote phrases), (c) our sales_strategy and any discount_policy, and (d) why this option/window fits the budget and the Impact/Awareness split.>"
    }}
  ],
  "subtotal": <number>,
  "notes": "<brief overall budget note and tradeoffs (use only provided info)>"
}}

RULES:
- Use ONLY products listed under Allowed Products.
- Use exact strings for product and option names.
- Try to diversify across products (one option per product; qty=1 unless text explicitly allows multiples).
- If prices are given as a map (e.g., 1X/2X or Retail/Bundle), put the chosen key in 'chosen_price_window'.
- Keep total <= ${budget_cap}. Prefer totals close to the cap without exceeding it.
- Reflect the budget split: favor conversion-oriented selections for Immediate Impact % and reach/awareness items for Brand Awareness %.
- Do not invent facts. Cite only phrases present in the Product Descriptions / Sales Strategy / Discount Policy.
""")