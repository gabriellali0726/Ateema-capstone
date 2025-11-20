from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator

# ===== Inputs =====
class SimilarClient(BaseModel):
    name: str
    purchased: List[str] = []
    notes: Optional[str] = None

class InputPayload(BaseModel):
    client_profile: str
    budget: float = Field(ge=0)
    similar_clients: List[SimilarClient] = []
    candidate_products: List[str] = []

# ===== Product JSON (from your folder) =====
class ProductRecord(BaseModel):
    name: str
    price_options: List[Dict[str, Any]] = []
    discount_policy: Optional[Any] = None
    sales_strategy: Optional[Any] = None
    description: Optional[str] = None  # <- NEW: product_description/description

# ===== Output Proposal =====
class Selection(BaseModel):
    product_name: str
    chosen_option: str
    chosen_price_window: Optional[str] = ""
    unit_price: float = Field(ge=0)
    qty: int = Field(default=1, ge=1)
    line_total: Optional[float] = Field(default=None, ge=0)
    reasoning: str  # ask the model for 3–5 sentences using the new context

    @model_validator(mode="after")
    def compute_line_total(self):
        expected = round(float(self.unit_price) * int(self.qty), 2)
        if self.line_total is None or abs(float(self.line_total) - expected) > 0.01:
            self.line_total = expected
        return self

class Proposal(BaseModel):
    client_name: str
    budget: float = Field(ge=0)
    currency: str = "USD"
    selections: List[Selection] = []
    subtotal: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def compute_subtotal(self):
        self.subtotal = round(sum(float(s.line_total or 0) for s in self.selections), 2)
        return self
