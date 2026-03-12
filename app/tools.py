"""Agent tool definitions for the bank customer service chatbot.

Provides mock implementations of common banking customer service operations.
These are designed as openai-agents @function_tool decorated functions.
"""

from __future__ import annotations

from agents import function_tool


@function_tool
def escalate_to_human() -> str:
    """Transfer the conversation to a human agent when the issue
    cannot be resolved automatically or the customer explicitly requests it."""
    return (
        "Conversation transferred to a human agent. "
        "A representative will be with you shortly."
    )


@function_tool
def get_account_balance(account_last4: str) -> str:
    """Query the current balance of a customer's account by the last 4 digits of the account number."""
    return (
        f"Account ending in {account_last4}: "
        f"Available balance: NTD 128,450. "
        f"Pending transactions: NTD -3,200. "
        f"As of: 2026-03-12 09:30."
    )


@function_tool
def get_transaction_history(account_last4: str, days: int = 30) -> str:
    """Query recent transaction history for a customer's account.

    Args:
        account_last4: Last 4 digits of the account number.
        days: Number of days of history to retrieve (default: 30).
    """
    return (
        f"Account ending in {account_last4} — last {days} days:\n"
        f"2026-03-10  POS Purchase - SUPERMARKET          -NTD 1,250\n"
        f"2026-03-09  Online Transfer In                  +NTD 50,000\n"
        f"2026-03-07  ATM Withdrawal                      -NTD 10,000\n"
        f"2026-03-05  Direct Debit - Utility Bill          -NTD 1,980\n"
        f"2026-03-01  Salary Credit                      +NTD 85,000"
    )


@function_tool
def freeze_card(card_last4: str, reason: str) -> str:
    """Immediately freeze a debit or credit card to prevent unauthorized use.

    Args:
        card_last4: Last 4 digits of the card number.
        reason: Reason for freezing (e.g., lost, stolen, suspicious activity).
    """
    return (
        f"Card ending in {card_last4} has been frozen immediately. "
        f"Reason: {reason}. "
        f"No further transactions will be authorized. "
        f"To request a replacement card, please visit a branch or call 0800-XXX-XXX."
    )


@function_tool
def dispute_transaction(card_last4: str, transaction_date: str, amount: str, merchant: str) -> str:
    """File a dispute for an unauthorized or incorrect card transaction.

    Args:
        card_last4: Last 4 digits of the card number.
        transaction_date: Date of the disputed transaction (YYYY-MM-DD).
        amount: Transaction amount (e.g., NTD 3,500).
        merchant: Merchant or description shown on the statement.
    """
    return (
        f"Dispute filed for card ending in {card_last4}. "
        f"Transaction: {merchant} on {transaction_date} for {amount}. "
        f"Case reference: DISP-20260312-8841. "
        f"Investigation period: up to 45 business days. "
        f"A provisional credit may be applied within 5 business days."
    )


@function_tool
def report_fraud(account_last4: str, description: str) -> str:
    """Report suspected fraud, scam, or unauthorized account access.

    Args:
        account_last4: Last 4 digits of the affected account number.
        description: Brief description of the suspected fraud.
    """
    return (
        f"Fraud report received for account ending in {account_last4}. "
        f"Details: {description}. "
        f"Case reference: FRAUD-20260312-3317. "
        f"Account has been flagged for review. "
        f"Our fraud team will contact you within 1 business day. "
        f"If you believe your account is actively compromised, please call 0800-XXX-XXX immediately."
    )
