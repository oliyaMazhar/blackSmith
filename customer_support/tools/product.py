"""Product, inventory, and campaign tools backed by PostgreSQL.

Provides:
- product.get: Retrieve product catalog record by ID or SKU
- inventory.get: Retrieve warehouse stock levels, distinguishing production from available stock
- campaign.get: Retrieve marketing/release campaign details
- commerce.campaign_stats: Analyze campaign return rates and inventory distribution
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from core.models import ActionCategory, RiskLevel, ToolDefinition
from customer_support.registry import ToolRegistry, get_registry
from database.connection import get_connection

logger = logging.getLogger(__name__)


# ============================================================================
# Argument Schemas
# ============================================================================

class GetProductInput(BaseModel):
    """Input arguments for reading a product from catalog."""
    product_id: Optional[int] = Field(None, description="Product ID")
    sku: Optional[str] = Field(None, description="Product SKU code")

    @model_validator(mode="after")
    def validate_identifier(self) -> GetProductInput:
        if self.product_id is None and (not self.sku or not self.sku.strip()):
            raise ValueError("Either product_id or sku must be provided to look up a product.")
        return self


class GetInventoryInput(BaseModel):
    """Input arguments for inspecting product inventory."""
    product_id: int = Field(..., description="Product ID whose inventory to inspect")


class GetCampaignInput(BaseModel):
    """Input arguments for inspecting marketing/drop campaigns."""
    campaign_id: Optional[int] = Field(None, description="Campaign ID")
    product_id: Optional[int] = Field(None, description="Product ID associated with campaign")

    @model_validator(mode="after")
    def validate_identifier(self) -> GetCampaignInput:
        if self.campaign_id is None and self.product_id is None:
            raise ValueError("Either campaign_id or product_id must be provided to inspect campaign.")
        return self


class GetCampaignStatsInput(BaseModel):
    """Input arguments for analyzing campaign and product return ratios."""
    product_id: Optional[int] = Field(None, description="Product ID")
    campaign_id: Optional[int] = Field(None, description="Campaign ID")

    @model_validator(mode="after")
    def validate_identifier(self) -> GetCampaignStatsInput:
        if self.product_id is None and self.campaign_id is None:
            raise ValueError("Either product_id or campaign_id must be provided.")
        return self


# ============================================================================
# Tool Implementations
# ============================================================================

def get_product(
    product_id: Optional[int] = None,
    sku: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve product catalog record including pricing and limited-edition status."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT product_id, sku, name, description, price, is_limited_edition, created_at
                FROM products
                WHERE (%s IS NOT NULL AND product_id = %s)
                   OR (%s IS NOT NULL AND LOWER(sku) = LOWER(%s))
                LIMIT 1;
                """,
                (product_id, product_id, sku, sku),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "found": False,
                    "product_id": product_id,
                    "sku": sku,
                    "message": f"Product not found for identifier (product_id={product_id}, sku={sku}).",
                }

            return {
                "found": True,
                "product_id": row[0],
                "sku": row[1],
                "name": row[2],
                "description": row[3],
                "price": float(row[4]),
                "is_limited_edition": bool(row[5]),
                "created_at": row[6].isoformat() if row[6] else None,
            }


def get_inventory(product_id: int) -> Dict[str, Any]:
    """Check inventory stock levels, maintaining distinction between total manufactured vs available stock."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.inventory_id, i.product_id, p.name AS product_name, p.sku,
                       i.quantity_total, i.quantity_sold, i.quantity_returned, i.updated_at,
                       p.is_limited_edition
                FROM inventory i
                JOIN products p ON i.product_id = p.product_id
                WHERE i.product_id = %s;
                """,
                (product_id,),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "found": False,
                    "product_id": product_id,
                    "message": f"Inventory record for product #{product_id} not found.",
                }

            qty_total = row[4]
            qty_sold = row[5]
            qty_returned = row[6]
            qty_available = max(0, qty_total - qty_sold + qty_returned)

            return {
                "found": True,
                "inventory_id": row[0],
                "product_id": row[1],
                "product_name": row[2],
                "sku": row[3],
                "quantity_total_manufactured": qty_total,
                "quantity_sold": qty_sold,
                "quantity_returned": qty_returned,
                "quantity_available": qty_available,
                "is_limited_edition": bool(row[8]),
                "updated_at": row[7].isoformat() if row[7] else None,
            }


def get_campaign(
    campaign_id: Optional[int] = None,
    product_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Retrieve marketing/release campaign details by campaign ID or associated product."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.campaign_id, c.product_id, p.name AS product_name, p.sku,
                       c.campaign_name, c.starts_at, c.ends_at, c.initial_quantity, c.status
                FROM campaigns c
                JOIN products p ON c.product_id = p.product_id
                WHERE (%s IS NOT NULL AND c.campaign_id = %s)
                   OR (%s IS NOT NULL AND c.product_id = %s)
                ORDER BY c.starts_at DESC
                LIMIT 1;
                """,
                (campaign_id, campaign_id, product_id, product_id),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "found": False,
                    "campaign_id": campaign_id,
                    "product_id": product_id,
                    "message": f"Campaign not found for identifier (campaign_id={campaign_id}, product_id={product_id}).",
                }

            return {
                "found": True,
                "campaign_id": row[0],
                "product_id": row[1],
                "product_name": row[2],
                "sku": row[3],
                "campaign_name": row[4],
                "starts_at": row[5].isoformat() if row[5] else None,
                "ends_at": row[6].isoformat() if row[6] else None,
                "initial_quantity": row[7],
                "status": row[8],
            }


def get_campaign_stats(
    product_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Analyze campaign performance, return volume, and stock concentration."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.campaign_id, c.product_id, c.campaign_name, c.initial_quantity, c.status,
                       p.name AS product_name, p.price, p.is_limited_edition,
                       i.quantity_total, i.quantity_sold, i.quantity_returned
                FROM campaigns c
                JOIN products p ON c.product_id = p.product_id
                JOIN inventory i ON c.product_id = i.product_id
                WHERE (%s IS NOT NULL AND c.campaign_id = %s)
                   OR (%s IS NOT NULL AND c.product_id = %s)
                LIMIT 1;
                """,
                (campaign_id, campaign_id, product_id, product_id),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "found": False,
                    "campaign_id": campaign_id,
                    "product_id": product_id,
                    "message": "Campaign and inventory records not found for requested identifier.",
                }

            initial_qty = row[3]
            qty_sold = row[9]
            qty_returned = row[10]
            return_rate = (qty_returned / qty_sold * 100.0) if qty_sold > 0 else 0.0

            return {
                "found": True,
                "campaign_id": row[0],
                "product_id": row[1],
                "campaign_name": row[2],
                "initial_quantity": initial_qty,
                "campaign_status": row[4],
                "product_name": row[5],
                "unit_price": float(row[6]),
                "is_limited_edition": bool(row[7]),
                "quantity_sold": qty_sold,
                "quantity_returned": qty_returned,
                "return_rate_percentage": round(return_rate, 2),
                "is_elevated_return_rate": return_rate > 20.0,
            }


# ============================================================================
# Tool Registration
# ============================================================================

def register_product_tools(reg: Optional[ToolRegistry] = None) -> None:
    """Register all product and commerce tools into target tool registry."""
    target_reg = reg or get_registry()

    target_reg.register(
        ToolDefinition(
            name="product.get",
            description="Retrieve product catalog record including pricing and limited-edition status by ID or SKU.",
            arg_schema=GetProductInput,
            function=get_product,
            permissions=["products.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="inventory.get",
            description="Check warehouse stock levels, distinguishing total manufactured quantity from available stock.",
            arg_schema=GetInventoryInput,
            function=get_inventory,
            permissions=["inventory.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="campaign.get",
            description="Retrieve promotional/release campaign details by campaign ID or associated product ID.",
            arg_schema=GetCampaignInput,
            function=get_campaign,
            permissions=["campaigns.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="commerce.campaign_stats",
            description="Analyze campaign return rates and stock concentration to detect abnormal return surges.",
            arg_schema=GetCampaignStatsInput,
            function=get_campaign_stats,
            permissions=["inventory.read", "campaigns.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )
