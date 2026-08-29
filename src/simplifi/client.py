"""High-level read-only Simplifi client."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from simplifi.auth import AuthSession
from simplifi.constants import BASE_URL, DEFAULT_PAGE_LIMIT
from simplifi.http import HttpClient
from simplifi.resources import Resources
from simplifi.token_store import TokenStore
from simplifi.views import Views

logger = logging.getLogger("simplifi")


class Client:
    """Read-only Quicken Simplifi Personal API client."""

    def __init__(
        self,
        *,
        dataset_id: str | None = None,
        token_store: TokenStore | None = None,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
        threat_metrix_session_id: str | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.auth = AuthSession(
            session=self.session,
            token_store=token_store,
            base_url=base_url,
            threat_metrix_session_id=threat_metrix_session_id
            or os.environ.get("SIMPLIFI_THREAT_METRIX_SESSION_ID"),
        )
        self.http = HttpClient(
            session=self.session,
            base_url=base_url,
            get_access_token=self.auth.ensure_access_token,
            dataset_id=dataset_id or os.environ.get("SIMPLIFI_DATASET_ID"),
        )
        self.resources = Resources(self.http)
        self.views = Views(self)

    # --- auth ---

    def login(
        self,
        email: str | None = None,
        password: str | None = None,
        *,
        mfa_code: str | None = None,
        mfa_prompt: bool = True,
    ) -> None:
        email = email or os.environ.get("SIMPLIFI_EMAIL")
        password = password or os.environ.get("SIMPLIFI_PASSWORD")
        if not email or not password:
            raise ValueError("email and password are required (or set SIMPLIFI_EMAIL/PASSWORD)")
        self.auth.login(email, password, mfa_code=mfa_code, mfa_prompt=mfa_prompt)
        self.auth.verify()
        if not self.http.dataset_id:
            self.resolve_dataset()

    def use_token(self, access_token: str, *, refresh_token: str = "") -> None:
        self.auth.use_access_token(access_token, refresh_token=refresh_token)
        self.auth.verify(access_token)
        if not self.http.dataset_id:
            self.resolve_dataset()

    def logout(self) -> None:
        self.auth.logout()

    def resolve_dataset(self) -> str:
        datasets = self.resources.datasets()
        if not datasets:
            raise RuntimeError("No Simplifi datasets returned for this account")
        dataset_id = str(datasets[0]["id"])
        self.http.set_dataset_id(dataset_id)
        logger.info("Using dataset_id=%s", dataset_id)
        return dataset_id

    @property
    def dataset_id(self) -> str | None:
        return self.http.dataset_id

    # --- resource passthroughs ---

    def datasets(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.datasets(**kwargs)

    def accounts(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.accounts(**kwargs)

    def account(self, account_id: str) -> dict[str, Any]:
        return self.resources.account(account_id)

    def account_balances(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.account_balances(**kwargs)

    def transactions(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        date_on_after: str | None = None,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.resources.transactions(
            limit=limit, date_on_after=date_on_after, modified_after=modified_after
        )

    def transaction(self, transaction_id: str) -> dict[str, Any]:
        return self.resources.transaction(transaction_id)

    def transactions_earliest_date_on(
        self, account_ids: list[str] | None = None
    ) -> dict[str, Any]:
        return self.resources.transactions_earliest_date_on(account_ids)

    def categories(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.categories(**kwargs)

    def tags(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.tags(**kwargs)

    def scheduled_transactions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.scheduled_transactions(**kwargs)

    def goals(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.goals(**kwargs)

    def goal_transactions(self, goal_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.goal_transactions(goal_id, **kwargs)

    def spending_watchlists(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.spending_watchlists(**kwargs)

    def securities(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.securities(**kwargs)

    def holdings(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.holdings(**kwargs)

    def free_to_spend(self) -> Any:
        return self.resources.free_to_spend()

    def planned_spending(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.planned_spending(**kwargs)

    def preferences(self) -> Any:
        return self.resources.preferences()

    def filters(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.filters(**kwargs)

    def alert_rules(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.alert_rules(**kwargs)

    def alerts(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.alerts(**kwargs)

    def institution_logins(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.institution_logins(**kwargs)

    def institutions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.institutions(**kwargs)

    def report_configurations(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.resources.report_configurations(**kwargs)

    def profile(self) -> dict[str, Any]:
        return self.resources.profile()

    def feature_flags(self) -> Any:
        return self.resources.feature_flags()

    def entitlements(self) -> Any:
        return self.resources.entitlements()

    def transfers_balance(self) -> Any:
        return self.resources.transfers_balance()
