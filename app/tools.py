"""Agent tool definitions for the customer service chatbot.

Provides mock implementations of common customer service operations.
These are designed as openai-agents @function_tool decorated functions.
"""

from __future__ import annotations

from agents import function_tool


@function_tool
def transfer_to_human() -> str:
    """Transfer the conversation to a human agent when the issue
    cannot be resolved automatically or the customer explicitly requests it."""
    return (
        "Conversation transferred to a human agent. "
        "A representative will be with you shortly."
    )


@function_tool
def check_order(order_id: str) -> str:
    """Check the current status of a customer order by order ID."""
    return (
        f"Order {order_id}: shipped on 2026-03-10, "
        f"expected delivery 2026-03-14. "
        f"Current status: in transit."
    )


@function_tool
def request_refund(order_id: str, reason: str) -> str:
    """Submit a refund request for a customer order."""
    return (
        f"Refund request for order {order_id} submitted successfully. "
        f"Reason: {reason}. "
        f"Processing time: 3-5 business days."
    )
