# registry.py

from models import ToolDefinition
from tools.order import read_order

ORDER_READ = ToolDefinition(
    name="order.read",
    description="Read an order using its order ID.",
    function=read_order,
    permissions=["orders.read"],
    risk_level="low",
    reversible=True,
    side_effect=False,
)