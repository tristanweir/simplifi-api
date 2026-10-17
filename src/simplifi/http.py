"""Read-only HTTP helpers for services.quicken.com."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from simplifi.secure_session import SecureSession

from simplifi.constants import APP_BUILD, APP_RELEASE, BASE_URL, CLIENT_ID, DEFAULT_PAGE_LIMIT
from simplifi.errors import ReadOnlyViolation, SimplifiHTTPError

logger = logging.getLogger("simplifi.http")

_ALLOWED_METHODS = frozenset({"GET", "POST"})
# POST is allowed only for known read-only endpoints that Quicken exposes as POST.
_READONLY_POST_PATHS = frozenset(
    {
        "/transactions/earliest-date-on",
    }
)


class HttpClient:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
        get_access_token: Any = None,
        refresh_access_token: Any = None,
        dataset_id: str | None = None,
        client_id: str = CLIENT_ID,
        timeout: float = 60.0,
    ) -> None:
        self.session = session or SecureSession()
        self.base_url = base_url.rstrip("/") + "/"
        self._get_access_token = get_access_token
        self._refresh_access_token = refresh_access_token
        self.dataset_id = dataset_id
        self.client_id = client_id
        self.timeout = timeout

    def set_dataset_id(self, dataset_id: str) -> None:
        self.dataset_id = dataset_id

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        require_dataset: bool = True,
    ) -> Any:
        method_u = method.upper()
        if method_u not in _ALLOWED_METHODS:
            raise ReadOnlyViolation(f"Mutating method {method_u} is not allowed")
        if method_u == "POST":
            clean = path.split("?", 1)[0]
            if clean not in _READONLY_POST_PATHS:
                raise ReadOnlyViolation(f"POST to {path} is not on the read-only allowlist")

        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "app-client-id": self.client_id,
            "app-release": APP_RELEASE,
            "app-build": APP_BUILD,
        }
        if self._get_access_token:
            headers["Authorization"] = f"Bearer {self._get_access_token()}"
        if require_dataset:
            if not self.dataset_id:
                raise SimplifiHTTPError(0, path, "dataset_id is required for this request")
            headers["Qcs-Dataset-Id"] = self.dataset_id
            headers["qcs-dataset-id"] = self.dataset_id

        url = urljoin(self.base_url, path)
        if urlsplit(url).scheme != "https" or urlsplit(url).netloc != "services.quicken.com":
            raise ReadOnlyViolation("Blocked pagination/request outside the Quicken API origin")
        logger.debug("%s %s", method_u, url)
        response = self.session.request(
            method_u,
            url,
            headers=headers,
            params=params,
            json=json,
            timeout=self.timeout,
            allow_redirects=False,
        )
        if response.status_code == 401 and self._refresh_access_token:
            headers["Authorization"] = f"Bearer {self._refresh_access_token()}"
            response = self.session.request(
                method_u, url, headers=headers, params=params, json=json,
                timeout=self.timeout, allow_redirects=False,
            )
        if 300 <= response.status_code < 400:
            raise SimplifiHTTPError(response.status_code, "Quicken API", "Redirect blocked")
        if response.status_code >= 400:
            raise SimplifiHTTPError(response.status_code, url, response.text)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def get_paginated(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        require_dataset: bool = True,
    ) -> list[dict[str, Any]]:
        query = dict(params or {})
        query.setdefault("limit", limit)
        resources: list[dict[str, Any]] = []
        next_link: str | None = path
        first = True
        seen = set()
        while next_link:
            if next_link in seen:
                raise SimplifiHTTPError(0, "Quicken API", "Pagination loop detected")
            seen.add(next_link)
            if first:
                payload = self.get(
                    next_link, params=query, require_dataset=require_dataset
                )
                first = False
            else:
                payload = self.get(next_link, require_dataset=require_dataset)
            if payload is None:
                break
            if isinstance(payload, list):
                resources.extend(payload)
                break
            chunk = payload.get("resources")
            if isinstance(chunk, list):
                resources.extend(chunk)
            meta = payload.get("metaData") or payload.get("metadata") or {}
            next_link = meta.get("nextLink") or meta.get("next_link")
            if not next_link:
                break
        return resources
