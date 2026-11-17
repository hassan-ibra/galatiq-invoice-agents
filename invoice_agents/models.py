from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _reject_float_money(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("money must be Decimal, not float")
    return value


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str
    quantity: int
    unit_price: Decimal
    amount: Decimal | None = None
    note: str | None = None

    @field_validator("unit_price", "amount", mode="before")
    @classmethod
    def money_must_not_be_float(cls, value: Any) -> Any:
        return _reject_float_money(value)

    def model_post_init(self, context: Any) -> None:
        if self.amount is None:
            self.amount = self.unit_price * self.quantity


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: str
    revision: str | None = None
    vendor_name: str
    vendor_address: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = "USD"
    line_items: list[LineItem]
    subtotal: Decimal | None = None
    tax_amount: Decimal = Decimal("0")
    total: Decimal
    payment_terms: str | None = None
    notes: str | None = None
    source_files: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    address_warnings: list[str] = Field(default_factory=list)

    @field_validator("subtotal", "tax_amount", "total", mode="before")
    @classmethod
    def money_must_not_be_float(cls, value: Any) -> Any:
        return _reject_float_money(value)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: int = Field(ge=1, le=12)
    outcome: Literal["reject", "hold", "info"]
    reason: str


class PipelineState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    db_path: str
    audit_path: str
    invoice: Invoice | None = None
    findings: list[Finding] = Field(default_factory=list)
    status: Literal["pending", "rejected", "hold", "approved", "voided"] = "pending"
    late: bool = False
    summary: str = ""
    critique: str = ""
    as_of: date | None = None
    auto_approve: bool = False
    use_llm: bool = False
    void_original: bool = False
    payment_result: dict[str, Any] | None = None

    def apply_findings(self) -> None:
        outcomes = {finding.outcome for finding in self.findings}
        if "reject" in outcomes:
            self.status = "rejected"
        elif "hold" in outcomes:
            self.status = "hold"
        else:
            self.status = "approved"
