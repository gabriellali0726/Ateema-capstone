from __future__ import annotations

import argparse, json, re, string, datetime
from pathlib import Path
from typing import Dict, Tuple, List, Any

from pydantic import ValidationError
from langchain_ollama import OllamaLLM
from langchain_core.output_parsers import StrOutputParser

from simple_schemas import InputPayload, ProductRecord, Proposal
from simple_prompt import PROPOSAL_PROMPT

# ---------- helpers ----------
def read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))

def load_input(path: Path) -> InputPayload:
    return InputPayload(**read_json(path))

def _canonical(s: str) -> str:
    """lowercase, remove spaces/punct, drop the word 'does', singularize trailing 's'."""
    t = s.lower()
    for ch in string.punctuation + " ":
        t = t.replace(ch, "")
    t = t.replace("does", "")  # project-specific normalization
    if t.endswith("s"):
        t = t[:-1]
    return t

def load_products(dir_path: Path) -> Dict[str, ProductRecord]:
    """Keyed by REAL product name."""
    out: Dict[str, ProductRecord] = {}
    for p in sorted(dir_path.glob("*.json")):
        obj = read_json(p)
        name = obj.get("name") or obj.get("product_name") or obj.get("product_id") or p.stem
        rec = ProductRecord(
            name=name,
            price_options=obj.get("price_options", obj.get("options", [])) or [],
            discount_policy=obj.get("discount_policy"),
            sales_strategy=obj.get("sales_strategy"),
            description=obj.get("product_description") or obj.get("description")  # <- NEW
        )
        out[name] = rec
    return out

def ensure_candidates(candidates: List[str], products: Dict[str, ProductRecord]) -> Tuple[List[str], List[str], Dict[str,str]]:
    index = { _canonical(n): n for n in products.keys() }
    present_real: List[str] = []
    missing_orig: List[str] = []
    mapping: Dict[str, str] = {}
    for c in candidates:
        can = _canonical(c)
        real = index.get(can)
        if real:
            present_real.append(real); mapping[c] = real
        else:
            missing_orig.append(c)
    # de-dup while keeping order
    seen = set()
    present_real = [x for x in present_real if not (x in seen or seen.add(x))]
    return present_real, missing_orig, mapping

def _format_option(name: str, opt: Dict[str, Any]) -> List[str]:
    lines = []
    if "name" in opt and ("price_usd" in opt or "price_usd_by_plan" in opt):
        oname = opt.get("name", "")
        lines.append(f"- option: {oname}")
        price_map = opt.get("price_usd") or opt.get("price_usd_by_plan") or {}
        if isinstance(price_map, dict):
            for k, v in price_map.items():
                lines.append(f"  - {k}: {v}")
        elif isinstance(price_map, (int, float)):
            lines.append(f"  - price_usd: {price_map}")
        for extra in ("notes","audience","distribution_estimate","discount_up_to_usd"):
            if extra in opt:
                lines.append(f"  - {extra}: {opt[extra]}")
        return lines

    if "pricing" in opt and isinstance(opt["pricing"], dict):
        lines.append(f"- option: Pricing")
        for k, v in opt["pricing"].items():
            lines.append(f"  - {k}: {v}")
        return lines

    lines.append(f"- {json.dumps(opt, ensure_ascii=False)}")
    return lines

def format_product_block(products: Dict[str, ProductRecord]) -> str:
    lines = []
    for name, rec in products.items():
        lines.append(f"## {name}")
        for opt in rec.price_options:
            if not isinstance(opt, dict):
                lines.append(f"- {json.dumps(opt, ensure_ascii=False)}"); continue
            lines.extend(_format_option(name, opt))
        if rec.discount_policy is not None:
            dp = rec.discount_policy
            lines.append("Discount Policy: " + (dp if isinstance(dp, str) else json.dumps(dp, ensure_ascii=False)))
        if rec.sales_strategy is not None:
            ss = rec.sales_strategy
            lines.append("Sales Strategy: " + (ss if isinstance(ss, str) else json.dumps(ss, ensure_ascii=False)))
        lines.append("")
    return "\n".join(lines).strip()

def format_descriptions_block(products: Dict[str, ProductRecord]) -> str:
    # NEW: surface product descriptions for richer reasoning
    lines = []
    for name, rec in products.items():
        if rec.description:
            lines.append(f"### {name}\n{rec.description}\n")
    return "\n".join(lines).strip()

def to_markdown(prop: Proposal) -> str:
    rows = []
    rows.append(f"# Proposal for {prop.client_name}")
    rows.append(f"**Budget:** ${prop.budget}  \n**Currency:** {prop.currency}\n")
    rows.append("| Product | Option | Price Window | Unit Price | Qty | Line Total | Reasoning |")
    rows.append("|-|-|-|-:|-:|-:|-|")
    for s in prop.selections:
        rows.append(f"| {s.product_name} | {s.chosen_option} | {s.chosen_price_window or ''} | ${s.unit_price} | {s.qty} | ${s.line_total} | {s.reasoning} |")
    rows.append(f"\n**Subtotal:** ${prop.subtotal}")
    if prop.notes:
        rows.append(f"\n**Notes:** {prop.notes}")
    return "\n".join(rows)

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Step 4–5: Stateless proposal generation (Ollama)")
    ap.add_argument("--input", required=True, help="input payload JSON from upstream")
    ap.add_argument("--products", required=True, help="folder of product JSONs")
    ap.add_argument("--out_json", default="proposal.json")
    ap.add_argument("--out_md", default="proposal.md")
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--soft_cap_pct", type=float, default=0.10,
                    help="Allow up to this fraction above budget; final subtotal forced <= cap.")
    args = ap.parse_args()

    inp = load_input(Path(args.input))
    all_products = load_products(Path(args.products))
    present_real, missing_orig, name_map = ensure_candidates(inp.candidate_products, all_products)
    if not present_real:
        raise SystemExit("No candidate products matched. Check names or normalization.")

    subset = {name: all_products[name] for name in present_real}
    product_block = format_product_block(subset)
    desc_block = format_descriptions_block(subset)

    cap = round(inp.budget * (1.0 + args.soft_cap_pct), 2)

    # warm, straightforward call
    llm = OllamaLLM(model=args.model, temperature=args.temperature)
    prompt_str = PROPOSAL_PROMPT.format(
        profile=inp.client_profile,
        budget=inp.budget,
        budget_cap=cap,
        soft_cap_pct=int(args.soft_cap_pct * 100),
        similar_clients=json.dumps([sc.model_dump() for sc in inp.similar_clients], ensure_ascii=False, indent=2),
        allowed_products="\n".join(f"- {n}" for n in subset.keys()),
        product_data=product_block,
        product_descriptions=desc_block,
    )
    raw = (llm | StrOutputParser()).invoke(prompt_str)

    # tolerant JSON parse
    # tolerant JSON parse with control-char cleanup
    try:
        data = json.loads(raw)
    except Exception:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE | re.DOTALL)
        # replace unescaped control characters (bad newlines/tabs inside strings)
        cleaned = re.sub(r'[\x00-\x1F]+', ' ', cleaned)
        # ensure backslashes and quotes are balanced
        cleaned = cleaned.replace('\\n', ' ').replace('\\t', ' ').replace('\r', ' ')
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            Path("raw_debug.json").write_text(raw, encoding="utf-8")
            raise ValueError(
                f"Gemma output not valid JSON — saved raw text to raw_debug.json for inspection: {e}"
            )

    prop = Proposal(**data)

    # Enforce soft cap as a safety net
    if prop.subtotal is not None and prop.subtotal > cap:
        prop.selections.sort(key=lambda s: float(s.line_total or 0), reverse=True)
        removed = []
        while len(prop.selections) > 1 and sum(float(s.line_total or 0) for s in prop.selections) > cap:
            removed.append(prop.selections.pop(0))
        prop = Proposal(**prop.model_dump())
        removed_list = ", ".join(f"{r.product_name} (${r.line_total})" for r in removed) or "none"
        note = f"Soft cap enforced at ${cap} (budget ${inp.budget} + {int(args.soft_cap_pct*100)}%). Removed: {removed_list}. Final subtotal: ${prop.subtotal}."
        prop.notes = (prop.notes + " " if prop.notes else "") + note

    Path(args.out_json).write_text(json.dumps(prop.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(to_markdown(prop), encoding="utf-8")

    print("[matched products]", present_real)
    if name_map:
        print("[name mapping] ", name_map)
    if missing_orig:
        print("[missing from folder]", missing_orig)
    print("[ok] wrote:", Path(args.out_json).resolve())
    print("[ok] wrote:", Path(args.out_md).resolve())

if __name__ == "__main__":
    main()
