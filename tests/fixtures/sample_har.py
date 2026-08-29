"""Minimal HAR fixture for discovery script tests (no auth tokens)."""

SAMPLE_HAR = {
    "log": {
        "entries": [
            {
                "request": {
                    "method": "GET",
                    "url": "https://services.quicken.com/accounts?limit=1000",
                    "headers": [
                        {"name": "qcs-dataset-id", "value": "synthetic"},
                        {"name": "app-client-id", "value": "acme_web"},
                    ],
                }
            },
            {
                "request": {
                    "method": "GET",
                    "url": "https://services.quicken.com/scheduled-transactions",
                    "headers": [{"name": "qcs-dataset-id", "value": "synthetic"}],
                }
            },
            {
                "request": {
                    "method": "GET",
                    "url": "https://example.com/other",
                    "headers": [],
                }
            },
        ]
    }
}
