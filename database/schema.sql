-- BlackSmith Relational Database Schema
-- Idempotent schema initialization matching existing PostgreSQL tables and required additions.

-- 1. Customers
CREATE TABLE IF NOT EXISTS customers (
    customer_id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    account_status VARCHAR(30) NOT NULL DEFAULT 'active',
    identity_verified BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. Customer Sessions
CREATE TABLE IF NOT EXISTS customer_sessions (
    session_id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(customer_id),
    ip_address INET,
    device_fingerprint VARCHAR(255),
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 3. Products
CREATE TABLE IF NOT EXISTS products (
    product_id BIGSERIAL PRIMARY KEY,
    sku VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price NUMERIC(10, 2) NOT NULL,
    is_limited_edition BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 4. Campaigns
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(product_id),
    campaign_name VARCHAR(255) NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    initial_quantity INTEGER NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'active'
);

-- 5. Inventory
CREATE TABLE IF NOT EXISTS inventory (
    inventory_id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL UNIQUE REFERENCES products(product_id),
    quantity_total INTEGER NOT NULL,
    quantity_sold INTEGER NOT NULL DEFAULT 0,
    quantity_returned INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 6. Orders
CREATE TABLE IF NOT EXISTS orders (
    order_id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(customer_id),
    campaign_id BIGINT REFERENCES campaigns(campaign_id),
    order_status VARCHAR(30) NOT NULL DEFAULT 'placed',
    payment_status VARCHAR(30) NOT NULL DEFAULT 'paid',
    payment_fingerprint VARCHAR(255),
    shipping_address_hash VARCHAR(255),
    placed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 7. Order Items
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL
);

-- 8. Support Tickets (Required for ticket triage & resolution pipeline)
CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(customer_id),
    order_id BIGINT REFERENCES orders(order_id),
    body TEXT NOT NULL,
    subject VARCHAR(255) NOT NULL DEFAULT '',
    category VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
    sentiment VARCHAR(32) NOT NULL DEFAULT 'NEUTRAL',
    priority VARCHAR(32) NOT NULL DEFAULT 'MEDIUM',
    workflow_status VARCHAR(32) NOT NULL DEFAULT 'NEW',
    response_draft TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 9. Messages
CREATE TABLE IF NOT EXISTS messages (
    message_id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(customer_id),
    channel VARCHAR(30) NOT NULL,
    direction VARCHAR(20) NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    ticket_id BIGINT REFERENCES support_tickets(ticket_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 10. Refunds
CREATE TABLE IF NOT EXISTS refunds (
    refund_id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(order_id),
    customer_id BIGINT NOT NULL REFERENCES customers(customer_id),
    amount NUMERIC(10, 2) NOT NULL,
    reason TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'requested',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    reviewed_by VARCHAR(255),
    idempotency_key VARCHAR(128) UNIQUE
);

-- 11. Policies
CREATE TABLE IF NOT EXISTS policies (
    policy_id BIGSERIAL PRIMARY KEY,
    policy_name VARCHAR(255) NOT NULL,
    policy_type VARCHAR(50) NOT NULL,
    policy_text TEXT NOT NULL,
    version VARCHAR(50) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 12. Risk Signals
CREATE TABLE IF NOT EXISTS risk_signals (
    signal_id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(customer_id),
    order_id BIGINT REFERENCES orders(order_id),
    product_id BIGINT REFERENCES products(product_id),
    signal_type VARCHAR(100) NOT NULL,
    signal_value JSONB NOT NULL DEFAULT '{}'::jsonb,
    severity VARCHAR(20),
    confidence NUMERIC(5, 4),
    source VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 13. Events
CREATE TABLE IF NOT EXISTS events (
    event_id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    customer_id BIGINT REFERENCES customers(customer_id),
    order_id BIGINT REFERENCES orders(order_id),
    product_id BIGINT REFERENCES products(product_id),
    refund_id BIGINT REFERENCES refunds(refund_id),
    campaign_id BIGINT REFERENCES campaigns(campaign_id),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 14. Audit Records (Required for Governance Layer)
CREATE TABLE IF NOT EXISTS audit_records (
    audit_id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    tool_name VARCHAR(64) NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    authorization_decision VARCHAR(32) NOT NULL,
    risk_score NUMERIC(5, 4) NOT NULL,
    explanation TEXT NOT NULL,
    execution_result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Alter table safe additions if existing tables missed columns
ALTER TABLE refunds ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS ticket_id BIGINT REFERENCES support_tickets(ticket_id);
ALTER TABLE events ADD COLUMN IF NOT EXISTS refund_id BIGINT REFERENCES refunds(refund_id);

-- Performance and lookup indexes
CREATE INDEX IF NOT EXISTS idx_customer_sessions_customer_id ON customer_sessions(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_placed_at ON orders(placed_at);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product_id ON order_items(product_id);
CREATE INDEX IF NOT EXISTS idx_refunds_order_id ON refunds(order_id);
CREATE INDEX IF NOT EXISTS idx_refunds_customer_id ON refunds(customer_id);
CREATE INDEX IF NOT EXISTS idx_refunds_status ON refunds(status);
CREATE INDEX IF NOT EXISTS idx_refunds_idempotency_key ON refunds(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_support_tickets_customer_id ON support_tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_support_tickets_order_id ON support_tickets(order_id);
CREATE INDEX IF NOT EXISTS idx_support_tickets_workflow_status ON support_tickets(workflow_status);
CREATE INDEX IF NOT EXISTS idx_messages_ticket_id ON messages(ticket_id);
CREATE INDEX IF NOT EXISTS idx_messages_customer_id ON messages(customer_id);
CREATE INDEX IF NOT EXISTS idx_risk_signals_customer_id ON risk_signals(customer_id);
CREATE INDEX IF NOT EXISTS idx_risk_signals_order_id ON risk_signals(order_id);
CREATE INDEX IF NOT EXISTS idx_risk_signals_type ON risk_signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_customer_id ON events(customer_id);
CREATE INDEX IF NOT EXISTS idx_events_refund_id ON events(refund_id);
CREATE INDEX IF NOT EXISTS idx_audit_records_run_id ON audit_records(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_records_tool_name ON audit_records(tool_name);
