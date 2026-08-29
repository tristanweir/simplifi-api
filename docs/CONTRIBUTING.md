# Contributing

Thanks for helping keep this unofficial client aligned with the live Simplifi web app.

## Principles

1. **Read-only.** Do not add PUT/PATCH/DELETE helpers for money data.
2. **Personal only.** Skip Business & Personal modules (invoices, mileage, etc.).
3. **No real household data in the repo.** Fixtures must be synthetic.
4. **Document endpoint changes** in `docs/endpoint-map.md`.

## Adding or fixing an endpoint

1. Capture a HAR from https://simplifi.quicken.com while exercising the feature
   (see `scripts/parse_har.py`).
2. Confirm method + path + required headers (`Authorization`, `qcs-dataset-id`).
3. Add or adjust a method on `Resources` / `Client`.
4. Prefer soft fallbacks (`_try_paginated`) when Quicken has renamed paths.
5. Add a synthetic unit test where logic is non-trivial (especially views).
6. Update `docs/endpoint-map.md`.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Code style

- Python 3.10+
- `ruff` for lint
- Public response shapes for views should be Pydantic models in `simplifi.models`

## Disclosure

This project is independent and not affiliated with Quicken. Endpoint behavior can
change without notice.
