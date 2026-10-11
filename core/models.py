"""Domain models and data schemas for BlackSmith using Pydantic v2.

Matches the PostgreSQL relational database schema and governance boundaries.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# Enums
# ============================================================================

class ExecutionMode(str, Enum):
    """Governance decision modes for tool execution."""
    AUTO = "AUTO"
    ASSISTED = "ASSISTED"
    HALTED = "HALTED"


class RiskLevel(str, Enum):
    """Categorized risk severity levels."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AccountStatus(str, Enum):
    """Customer account standing."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    FLAGGED = "flagged"
    CLOSED = "closed"


class OrderStatus(str, Enum):
    """Fulfillment status of an order."""
    PLACED = "placed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class PaymentStatus(str, Enum):
    """Payment status of an order."""
    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"
    FAILED = "failed"


class RefundStatus(str, Enum):
    """Lifecycle status of a refund request."""
    REQUESTED = "requested"
    PENDING = "pending"
    APPROVED = "approved"
    PROCESSED = "processed"
    EXECUTED = "executed"
    REJECTED = "rejected"


class TicketCategory(str, Enum):
    """Classification category of customer support ticket."""
    DELIVERY_STATUS = "DELIVERY_STATUS"
    DAMAGED_DEFECTIVE = "DAMAGED_DEFECTIVE"
    REFUND_REQUEST = "REFUND_REQUEST"
    PRODUCT_INQUIRY = "PRODUCT_INQUIRY"
    ACCOUNT_ACCESS = "ACCOUNT_ACCESS"
    COMPLAINT = "COMPLAINT"
    UNKNOWN = "UNKNOWN"


class TicketSentiment(str, Enum):
    """Detected sentiment tone in customer message."""
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    ANGRY = "ANGRY"


class TicketPriority(str, Enum):
    """Ticket routing and processing priority."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TicketWorkflowStatus(str, Enum):
    """Workflow state of a support ticket."""
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RESOLVED = "RESOLVED"
    HALTED = "HALTED"


class ActionCategory(str, Enum):
    """Categorization of tool actions for governance policies."""
    READ = "READ"
    FINANCIAL_MUTATION = "FINANCIAL_MUTATION"
    INVENTORY_MUTATION = "INVENTORY_MUTATION"
    COMMUNICATION = "COMMUNICATION"
    ACCOUNT_MODIFICATION = "ACCOUNT_MODIFICATION"


# ============================================================================
# Customer & Commerce Domain Models
# ============================================================================

class Customer(BaseModel):
    """Customer account record matching PostgreSQL schema."""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    customer_id: int
    email: str
    full_name: str
    account_status: str = "active"
    identity_verified: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def id(self) -> int:
        return self.customer_id

    @property
    def name(self) -> str:
        return self.full_name


class Product(BaseModel):
    """Product catalog record."""
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    sku: str
    name: str
    description: Optional[str] = None
    price: Decimal
    is_limited_edition: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def id(self) -> int:
        return self.product_id


class Campaign(BaseModel):
    """Promotional or limited-release campaign."""
    model_config = ConfigDict(from_attributes=True)

    campaign_id: int
    product_id: int
    campaign_name: str
    starts_at: datetime
    ends_at: datetime
    initial_quantity: int
    status: str = "active"

    @property
    def id(self) -> int:
        return self.campaign_id


class Inventory(BaseModel):
    """Inventory tracking for stock and returns."""
    model_config = ConfigDict(from_attributes=True)

    inventory_id: int
    product_id: int
    quantity_total: int
    quantity_sold: int = 0
    quantity_returned: int = 0
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def quantity_available(self) -> int:
        return max(0, self.quantity_total - self.quantity_sold + self.quantity_returned)


class OrderItem(BaseModel):
    """Line item in customer order."""
    model_config = ConfigDict(from_attributes=True)

    order_item_id: int
    order_id: int
    product_id: int
    quantity: int
    unit_price: Decimal
    product_name: Optional[str] = None


class OrderSummary(BaseModel):
    """High-level summary of an order with line items."""
    model_config = ConfigDict(from_attributes=True)

    order_id: int
    customer_id: int
    campaign_id: Optional[int] = None
    order_status: str = "placed"
    payment_status: str = "paid"
    payment_fingerprint: Optional[str] = None
    shipping_address_hash: Optional[str] = None
    placed_at: datetime = Field(default_factory=datetime.utcnow)
    total_amount: Decimal = Decimal("0.00")
    items: List[OrderItem] = Field(default_factory=list)

    @property
    def id(self) -> int:
        return self.order_id


# ============================================================================
# Refund & Eligibility Models
# ============================================================================

class RefundRecord(BaseModel):
    """Refund entity representation."""
    model_config = ConfigDict(from_attributes=True)

    refund_id: int
    order_id: int
    customer_id: int
    amount: Decimal
    reason: Optional[str] = None
    status: str = "requested"
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    idempotency_key: Optional[str] = None

    @property
    def id(self) -> int:
        return self.refund_id


class PolicyCheckResult(BaseModel):
    """Result of an individual business rule evaluation."""
    rule_name: str
    passed: bool
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class RefundEligibility(BaseModel):
    """Comprehensive evaluation of refund eligibility."""
    is_eligible: bool
    order_id: int
    max_refundable_amount: Decimal = Decimal("0.00")
    reason: str
    violations: List[str] = Field(default_factory=list)
    policy_checks: List[PolicyCheckResult] = Field(default_factory=list)


# ============================================================================
# Ticket Models
# ============================================================================

class TicketClassification(BaseModel):
    """Triage classification for support ticket."""
    category: TicketCategory
    sentiment: TicketSentiment
    priority: TicketPriority
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    reasoning: str = ""
    detected_entities: Dict[str, Any] = Field(default_factory=dict)


class SupportTicket(BaseModel):
    """Support ticket entity stored in PostgreSQL."""
    model_config = ConfigDict(from_attributes=True)

    ticket_id: int
    customer_id: Optional[int] = None
    order_id: Optional[int] = None
    body: str
    subject: str = ""
    category: str = "UNKNOWN"
    sentiment: str = "NEUTRAL"
    priority: str = "MEDIUM"
    workflow_status: str = "NEW"
    response_draft: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def id(self) -> int:
        return self.ticket_id


class MessageRecord(BaseModel):
    """Inbound customer message or response communication."""
    model_config = ConfigDict(from_attributes=True)

    message_id: int
    customer_id: int
    channel: str
    direction: str
    subject: Optional[str] = None
    body: str
    ticket_id: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Governance, Risk & Tool Execution Models
# ============================================================================

class ToolDefinition(BaseModel):
    """Canonical registry definition of an executable agent tool."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    arg_schema: type[BaseModel]
    function: Optional[Callable[..., Any]] = None
    permissions: List[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    is_mutation: bool = False
    side_effect: bool = False
    reversible: bool = False
    action_category: ActionCategory = ActionCategory.READ


class RiskFactor(BaseModel):
    """Specific factor contributing to total risk evaluation."""
    factor_type: str
    description: str
    weight: float = 1.0
    score: float = Field(ge=0.0, le=1.0)
    details: Dict[str, Any] = Field(default_factory=dict)


class RiskAssessment(BaseModel):
    """Aggregate risk assessment score and factor breakdown."""
    overall_score: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    risk_factors: List[RiskFactor] = Field(default_factory=list)
    requires_human_approval: bool = False
    reasoning: str = ""


class GovernanceDecision(BaseModel):
    """Authoritative decision rendered by the policy engine."""
    decision: ExecutionMode
    risk_score: float = Field(ge=0.0, le=1.0)
    policy_violations: List[str] = Field(default_factory=list)
    approval_required: bool = False
    explanation: str
    conditions: Dict[str, Any] = Field(default_factory=dict)


class HumanApprovalResult(BaseModel):
    """Record of human supervisor review on a gated action."""
    approved: bool
    approved_by: str
    reason: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    signature: Optional[str] = None


class ToolExecutionResult(BaseModel):
    """Standardized output of tool invocation."""
    success: bool
    output: Any = None
    error: Optional[str] = None
    tool_name: str
    execution_time_ms: float = 0.0
    mutation_performed: bool = False


class AuditEvent(BaseModel):
    """Audit trail record for agent governance decisions and tool execution."""
    model_config = ConfigDict(from_attributes=True)

    audit_id: Optional[int] = None
    run_id: str
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    authorization_decision: ExecutionMode
    risk_score: float
    explanation: str
    execution_result: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
