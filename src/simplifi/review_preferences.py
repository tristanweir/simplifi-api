"""Versioned local analysis context; these preferences never create live rules."""

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from simplifi.rule_plan_models import StrictModel
from simplifi.views.classify import transaction_payee


def normalize(value):
    return " ".join(str(value).casefold().split())


class CategoryConvention(StrictModel):
    id: str = Field(min_length=1)
    account_ids: list[str] = Field(default_factory=list)
    payee_contains: str | None = None
    category_ids: list[str] = Field(min_length=1)
    reports_excluded: StrictBool | None = None
    spending_excluded: StrictBool | None = None
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def scoped(self):
        if self.payee_contains is not None and not self.payee_contains.strip():
            raise ValueError("Payee scope must not be blank")
        if not self.account_ids and not (self.payee_contains and self.payee_contains.strip()):
            raise ValueError("A category convention requires an account or payee scope")
        return self

    def matches_scope(self, transaction):
        return (not self.account_ids or str(transaction.get("accountId")) in self.account_ids) and (
            not self.payee_contains
            or normalize(self.payee_contains) in normalize(transaction_payee(transaction))
        )

    def accepts(self, transaction):
        return (
            self.matches_scope(transaction)
            and (transaction.get("coa") or {}).get("type") == "CATEGORY"
            and str(transaction["coa"].get("id")) in self.category_ids
            and (
                self.reports_excluded is None
                or transaction.get("isExcludedFromReports", False) is self.reports_excluded
            )
            and (
                self.spending_excluded is None
                or transaction.get("isExcludedFromF2S", False) is self.spending_excluded
            )
        )


class PaymentChannel(StrictModel):
    bank_account_ids: list[str] = Field(min_length=1)
    payment_account_id: str = Field(min_length=1)
    statement_contains: str = Field(min_length=1)

    @model_validator(mode="after")
    def distinct(self):
        if self.payment_account_id in self.bank_account_ids or not self.statement_contains.strip():
            raise ValueError("A payment channel requires distinct accounts and a descriptor")
        return self


class ReviewPreferences(StrictModel):
    version: Literal[1] = 1
    account_owners: dict[str, str] = Field(default_factory=dict)
    payee_aliases: dict[str, str] = Field(default_factory=dict)
    statement_aliases: dict[str, list[str]] = Field(default_factory=dict)
    conventions: list[CategoryConvention] = Field(default_factory=list)
    payment_channels: list[PaymentChannel] = Field(default_factory=list)

    @model_validator(mode="after")
    def unambiguous(self):
        if any(not v.strip() for v in self.account_owners.values()):
            raise ValueError("Owner labels must not be blank")
        if len({c.id for c in self.conventions}) != len(self.conventions):
            raise ValueError("Convention IDs must be unique")
        for mapping in (self.payee_aliases, self.statement_aliases):
            if len({normalize(k) for k in mapping}) != len(mapping):
                raise ValueError("Normalized alias keys must be unique")
        if any(not normalize(k) or not normalize(v) for k, v in self.payee_aliases.items()):
            raise ValueError("Payee aliases must not be blank")
        aliases = {normalize(k): normalize(v) for k, v in self.payee_aliases.items()}
        if any(v in aliases and aliases[v] != v for v in aliases.values()):
            raise ValueError("Alias chains and cycles are unsupported; use canonical targets")
        if any(
            not normalize(k) or not values or any(not normalize(v) for v in values)
            for k, values in self.statement_aliases.items()
        ):
            raise ValueError("Statement aliases must not be blank")
        return self


def preferences_for(review):
    return ReviewPreferences.model_validate(review.context.get("analysis_preferences", {}))
