"""Thin GET wrappers for Simplifi backend resources."""

from __future__ import annotations

from typing import Any

from simplifi.constants import DEFAULT_PAGE_LIMIT
from simplifi.errors import SimplifiHTTPError
from simplifi.http import HttpClient


class Resources:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    # --- Core ---

    def datasets(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._http.get_paginated(
            "/datasets", params={"limit": limit}, require_dataset=False
        )

    def accounts(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if modified_after:
            params["modifiedAfter"] = modified_after
        return self._http.get_paginated("/accounts", params=params)

    def account(self, account_id: str) -> dict[str, Any]:
        return self._http.get(f"/accounts/{account_id}")

    def account_balances(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if modified_after:
            params["modifiedAfter"] = modified_after
        return self._try_paginated(
            ["/accounts-balances", "/accounts/balances", "/balances"],
            params=params,
        )

    def transactions(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        date_on_after: str | None = None,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if date_on_after:
            params["dateOnAfter"] = date_on_after
        if modified_after:
            params["modifiedAfter"] = modified_after
        return self._http.get_paginated("/transactions", params=params)

    def transaction(self, transaction_id: str) -> dict[str, Any]:
        return self._http.get(f"/transactions/{transaction_id}")

    def transactions_earliest_date_on(
        self, account_ids: list[str] | None = None
    ) -> dict[str, Any]:
        return self._http.post(
            "/transactions/earliest-date-on",
            json={"accountIds": account_ids or []},
        )

    def categories(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if modified_after:
            params["modifiedAfter"] = modified_after
        return self._http.get_paginated("/categories", params=params)

    def tags(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if modified_after:
            params["modifiedAfter"] = modified_after
        return self._http.get_paginated("/tags", params=params)

    # --- Bills & Income ---

    def scheduled_transactions(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if modified_after:
            params["modifiedAfter"] = modified_after
        return self._http.get_paginated("/scheduled-transactions", params=params)

    # --- Goals / Watchlists / Investments ---

    def goals(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(["/goals", "/savings-goals"], params={"limit": limit})

    def goal_transactions(
        self, goal_id: str | None = None, *, limit: int = DEFAULT_PAGE_LIMIT
    ) -> list[dict[str, Any]]:
        if goal_id:
            return self._try_paginated(
                [f"/goals/{goal_id}/transactions"],
                params={"limit": limit},
            )
        return self._try_paginated(
            ["/goal-transactions", "/transactions/goals"],
            params={"limit": limit},
        )

    def spending_watchlists(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(
            ["/spending-watchlists", "/watchlists"],
            params={"limit": limit},
        )

    def securities(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(
            ["/v2/investments/securities", "/investments/securities", "/securities"],
            params={"limit": limit},
        )

    def holdings(
        self, *, account_id: str | None = None, limit: int = DEFAULT_PAGE_LIMIT
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if account_id:
            params["accountId"] = account_id
        # Prefer /v2 — /investments/holdings often returns an empty page and
        # short-circuits the fallback chain.
        return self._try_paginated(
            ["/v2/investments/holdings", "/investments/holdings"],
            params=params,
        )

    # --- Spending Plan ---

    def free_to_spend(self) -> dict[str, Any] | list[dict[str, Any]] | None:
        return self._try_get(
            ["/free-to-spend", "/spending-plan/free-to-spend", "/planned-spending"]
        )

    def planned_spending(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(
            ["/planned-spending", "/planned-spend", "/spending-plan/items"],
            params={"limit": limit},
        )

    # --- Settings / reference (read) ---

    def preferences(self) -> dict[str, Any] | list[dict[str, Any]] | None:
        return self._try_get(["/preferences", "/user/preferences"])

    def filters(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(["/filters"], params={"limit": limit})

    def alert_rules(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(
            ["/alert-rules", "/alerts/rules"], params={"limit": limit}
        )

    def alerts(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(["/alerts"], params={"limit": limit})

    def institution_logins(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(["/institution-logins"], params={"limit": limit})

    def institutions(self, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        return self._try_paginated(["/institutions"], params={"limit": limit})

    def report_configurations(
        self, *, limit: int = DEFAULT_PAGE_LIMIT
    ) -> list[dict[str, Any]]:
        return self._try_paginated(
            ["/report-configurations", "/reports/configurations", "/saved-reports"],
            params={"limit": limit},
        )

    def profile(self) -> dict[str, Any]:
        return self._http.get("/userprofiles/me", require_dataset=False)

    def feature_flags(self) -> dict[str, Any] | list[dict[str, Any]] | None:
        return self._try_get(["/feature-flags", "/featureFlags"])

    def entitlements(self) -> dict[str, Any] | list[dict[str, Any]] | None:
        return self._try_get(["/entitlements", "/datasets/entitlements"])

    def transfers_balance(self) -> dict[str, Any] | list[dict[str, Any]] | None:
        return self._try_get(["/transfers/balance", "/transfers"])

    # --- helpers ---

    def _try_paginated(
        self, paths: list[str], *, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for path in paths:
            try:
                return self._http.get_paginated(path, params=params)
            except SimplifiHTTPError as exc:
                if exc.status_code in {404, 405, 501}:
                    last_error = exc
                    continue
                raise
        if last_error:
            return []
        return []

    def _try_get(self, paths: list[str]) -> dict[str, Any] | list[dict[str, Any]] | None:
        for path in paths:
            try:
                return self._http.get(path)
            except SimplifiHTTPError as exc:
                if exc.status_code in {404, 405, 501}:
                    continue
                raise
        return None
