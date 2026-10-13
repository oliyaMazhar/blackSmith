"""Tool registrations for BlackSmith customer support operations."""

from typing import Optional

from customer_support.registry import ToolRegistry, get_registry
from customer_support.tools.communication import register_communication_tools
from customer_support.tools.context import register_context_tools
from customer_support.tools.customer import register_customer_tools
from customer_support.tools.order import register_order_tools
from customer_support.tools.product import register_product_tools
from customer_support.tools.refund import register_refund_tools


def register_all_tools(reg: Optional[ToolRegistry] = None) -> ToolRegistry:
    """Register all domain tools into the target or global registry."""
    target = reg or get_registry()
    register_customer_tools(target)
    register_order_tools(target)
    register_product_tools(target)
    register_refund_tools(target)
    register_communication_tools(target)
    register_context_tools(target)
    return target


__all__ = [
    "register_all_tools",
    "register_customer_tools",
    "register_order_tools",
    "register_product_tools",
    "register_refund_tools",
    "register_communication_tools",
    "register_context_tools",
]
