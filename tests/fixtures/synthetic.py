"""Synthetic fixtures — no real household data."""

TRANSACTIONS = [
    {
        "id": "t1",
        "amount": -42.5,
        "type": "CASH",
        "payee": "Cafe Example",
        "cpData": {"txnOn": "2026-08-05"},
        "coa": {"type": "CATEGORY", "id": "cat-food"},
    },
    {
        "id": "t2",
        "amount": -120.0,
        "type": "CASH",
        "payee": "Market",
        "cpData": {"txnOn": "2026-08-12"},
        "coa": {"type": "CATEGORY", "id": "cat-food"},
    },
    {
        "id": "t3",
        "amount": 3000.0,
        "type": "CASH",
        "payee": "Employer",
        "cpData": {"txnOn": "2026-08-01"},
        "coa": {"type": "CATEGORY", "id": "cat-income"},
    },
    {
        "id": "t4",
        "amount": -500.0,
        "type": "TRANSFER",
        "payee": "Savings",
        "cpData": {"txnOn": "2026-08-03"},
        "coa": {"type": "TRANSFER", "id": "xfer"},
        "isTransfer": True,
    },
    {
        "id": "t5",
        "amount": -15.0,
        "type": "CASH",
        "payee": "Future Bill",
        "cpData": {"txnOn": "2026-08-30"},
        "coa": {"type": "CATEGORY", "id": "cat-util"},
    },
    {
        "id": "t6",
        "amount": -10.0,
        "type": "INVESTMENT",
        "payee": "Broker",
        "cpData": {"txnOn": "2026-08-08"},
        "coa": {"type": "CATEGORY", "id": "cat-inv"},
    },
    {
        "id": "t7",
        "amount": -1.0,
        "type": "CASH",
        "payee": "Adjust",
        "cpData": {"txnOn": "2026-08-09"},
        "coa": {"type": "BALANCE_ADJUSTMENT", "id": "ba1"},
    },
    {
        "id": "t8",
        "amount": -80.0,
        "type": "CASH",
        "payee": "Gas",
        "cpData": {"txnOn": "2026-07-20"},
        "coa": {"type": "CATEGORY", "id": "cat-transport"},
    },
    {
        "id": "t9",
        "amount": -25.0,
        "type": "CASH",
        "payee": "Recent Cafe",
        "cpData": {"txnOn": "2026-08-27"},
        "coa": {"type": "CATEGORY", "id": "cat-food"},
    },
]

SCHEDULED = [
    {
        "id": "s1",
        "type": "BILL",
        "payee": "Rent",
        "amount": -1500,
        "dueOn": "2026-08-01",
    },
    {
        "id": "s2",
        "type": "SUBSCRIPTION",
        "payee": "StreamCo",
        "amount": -15,
        "dueOn": "2026-08-10",
    },
    {
        "id": "s3",
        "type": "INCOME",
        "payee": "Employer",
        "amount": 3000,
        "dueOn": "2026-08-01",
    },
    {
        "id": "s4",
        "type": "BILL",
        "payee": "Past Due Utility",
        "amount": -60,
        "dueOn": "2026-07-28",
    },
]

ACCOUNTS = [
    {
        "id": "a1",
        "name": "Checking",
        "type": "BANK",
        "onlineBalance": 5000,
        "isClosed": False,
    },
    {
        "id": "a2",
        "name": "Card",
        "type": "CREDIT",
        "onlineBalance": -200,
        "isClosed": False,
    },
    {
        "id": "a3",
        "name": "Brokerage",
        "type": "INVESTMENT",
        "onlineBalance": 10000,
        "isClosed": False,
    },
]

CATEGORIES = [
    {"id": "cat-food", "name": "Food"},
    {"id": "cat-income", "name": "Income"},
    {"id": "cat-transport", "name": "Transport"},
]

GOALS = [{"id": "g1", "name": "Emergency", "monthlyContribution": 200}]
