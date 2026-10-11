# PROJECT: BLACKSMITH

## Build a Working Agentic Customer Support System With an Enforced Governance Layer

You are working inside my existing Final Year Project repository, BlackSmith.

Your task is to turn the current repository into a coherent, runnable first version of an agentic customer support system. This is an implementation task, not a request for architectural suggestions. Inspect the repository, make the necessary changes, implement the missing functionality, run the application and tests, resolve problems you find, and leave the project in a state where I can launch it from the terminal using `python main.py`.

Do not stop after inspecting the project or writing a plan. Implement the system.

Do not ask me to make decisions that can reasonably be resolved from the requirements below. Make sensible engineering decisions, document them, and proceed.

The implementation must prioritize correctness, safety, maintainability, and a complete end-to-end execution path over unnecessary complexity.

---

# 1. UNDERSTAND THE PROJECT BEFORE MODIFYING IT

Start by inspecting the complete current workspace.

Examine:

* The current directory structure.
* All Python source files.
* Existing database code, database schemas, seed scripts, and validation scripts.
* The current LangGraph implementation, if present.
* Tool definitions and tool registries.
* Agent and persona configurations.
* Environment configuration and dependency files.
* Git status, current branch, and recent commits where available.
* Existing tests and documentation.
* The installed Python environment and versions of the important dependencies.
* Existing database connectivity and schema compatibility, if the database can be accessed safely.

A previous workspace inspection suggested that directories such as `agents`, `core`, `autonomy`, `database`, `graph`, `integrations`, and `tests` exist but contain little or no source code. It also suggested that `customer_support/registery.py` might be the only application source file. Treat that as an unverified observation. Confirm the actual workspace contents yourself.

Do not assume the latest repository is identical to an earlier version.

## Rules for inspecting and modifying the repository

1. Preserve existing functionality that works and fits the specification.
2. Repair existing files where doing so is cleaner than recreating them.
3. Rebuild missing components when necessary.
4. Do not create a second implementation of a feature that already exists and works.
5. Do not keep obsolete implementations merely because they are present.
6. Do not create duplicate registries, duplicate agent states, competing configuration systems, or multiple database connection implementations.
7. Resolve naming inconsistencies. For example, if both `registry.py` and the misspelled `registery.py` exist, establish one canonical registry module and update imports.
8. Do not delete the virtual environment or reinstall every dependency unnecessarily.
9. Do not run destructive Git commands, discard my uncommitted changes, or push changes to GitHub.
10. Never print or copy real credentials into source code, logs, documentation, test fixtures, or your final report.

If you discover that a database password, API key, or other secret has been committed to a tracked file, remove it from the current application and documentation, tell me that the exposed credential needs to be rotated, and do not reproduce it. Do not rewrite Git history or force-push changes without explicit authorization.

Once you understand the existing workspace, implement the specification below.

---

# 2. PROJECT PURPOSE AND FIRST-VERSION SCOPE

BlackSmith is intended to become a system in which AI agents can perform business tasks while a separate system determines what actions they are allowed to perform.

The initial environment is customer support.

The system should receive a customer complaint, understand the problem, inspect relevant business information, decide which tools are needed, and prepare an appropriate response.

However, an LLM must not have unrestricted authority over the business environment.

The architecture must distinguish three responsibilities.

**The agent determines what it wants to do.**

It interprets customer requests, classifies tickets, retrieves relevant information, proposes tool calls, and drafts responses.

**The business tools provide access to the environment.**

They read and modify actual records in the PostgreSQL database through narrow, explicitly defined operations.

**The governance system determines which proposed actions are permitted.**

It inspects the proposed action, validates it against business rules, evaluates relevant context, and determines whether the action can proceed autonomously, needs human approval, or must be blocked.

The system must enforce this separation in executable code.

The LLM must never become the sole authority simply because its response says an action is safe.

## Deliverable

The result must be a usable local customer support application with:

* A real LangGraph execution pipeline.
* An LLM-powered support agent.
* Ticket classification and prioritization.
* Context retrieval through registered tools.
* Response drafting.
* A real PostgreSQL-backed business environment.
* A centralized tool registry.
* A working, explainable risk evaluator.
* An enforceable action authorization layer.
* Autonomous, assisted, and halted execution modes.
* Human approval for actions that require it.
* Transactionally correct database mutations.
* Persistent audit records.
* Deterministic sample data for testing.
* Automated tests.
* A terminal-based user interface.
* Clear setup instructions.
* A working `main.py` entry point.

This is the first version, not the final research system.

Do not build a general-purpose master agent that decomposes arbitrary problem statements and dynamically creates other agents in this iteration. Do not implement an adaptive learning system or complex feedback-training loop yet.

The architecture should leave room for those capabilities without pretending that they already exist.

---

# 3. EXPECTED USER EXPERIENCE

The application must be usable from the terminal.

After configuration, I should be able to run:

`python main.py`

The program should initialize its dependencies, verify database connectivity, perform safe startup checks, and open an interactive customer support session.

The exact visual design is not important. The workflow and correctness are.

## 3.1 Startup

On startup, the application should:

1. Load configuration from environment variables and a local `.env` file where appropriate.
2. Validate the required configuration.
3. Establish a connection to PostgreSQL.
4. Verify or initialize the required database schema.
5. Seed deterministic sample data only when explicitly permitted and appropriate.
6. Validate that the expected tables and tools are available.
7. Initialize the configured LLM.
8. Construct the tool registry and graph.
9. Display whether the system is ready.

Startup errors must be informative.

If PostgreSQL is unavailable, show that the database connection failed and explain what must be configured.

If the model API key is missing, explain which environment variable is required.

If the configured model cannot perform the required tool-calling operation, report the problem rather than silently pretending that the agent is working.

Never display database passwords or API keys in error messages.

## 3.2 Interactive support session

Once startup succeeds, I should be able to enter a customer complaint directly in the terminal.

For example:

"I ordered earbuds five days ago, but my package still hasn't arrived. Your support team has been ignoring me."

The system should process the complaint and show:

* A ticket identifier.
* The detected category.
* The detected sentiment.
* The priority.
* The relevant customer or order information, if supplied or safely identifiable.
* The context retrieved from the business database.
* The recommended next action.
* The drafted response.
* Any governance decision made during processing.
* A concise explanation of why that decision was reached.
* Whether the response has been saved.

If the system does not know the customer or order, it must not invent one. It should ask for additional identifiers when necessary or produce a response explaining that the information could not be verified.

## 3.3 Interactive commands

Provide a minimal command interface with the following capabilities:

* Help: display available commands and explain the workflow.
* New ticket: process a complaint supplied by the user.
* Existing tickets: list and select available seeded or previously submitted tickets.
* Demo: run a predefined demonstration scenario.
* Seed: initialize sample data safely when explicitly requested.
* Validate: check database integrity and required scenario availability.
* Quit: shut down cleanly.

These commands may be implemented using any maintainable terminal-input design.

Do not require a web dashboard, frontend, Docker, or an elaborate service deployment merely to use the first version.

---

# 4. ESTABLISH A CLEAN PROJECT STRUCTURE

Inspect the existing repository and adapt it to a coherent structure. Do not mechanically recreate every directory if it already has an appropriate equivalent.

The final architecture should have recognizable responsibilities.

## Core

Contains shared domain models and application configuration.

It should define the canonical representations of tickets, customers, orders, tool permissions, risk assessments, governance decisions, and other shared business objects.

## Agents

Contains the construction and configuration of the LLM agent, along with its role-specific instructions.

The support agent should be created through one clear factory or construction function rather than being assembled independently in several modules.

## Graph

Contains the LangGraph workflow, its shared runtime state, its execution nodes, and its routing logic.

## Customer support

Contains the customer support tool implementations, tool metadata, and centralized registry.

Business tools must not be implemented separately inside agent prompts or graph nodes.

## Autonomy

Contains the risk evaluator, authorization policy, and execution decision logic.

This layer must be independent of the LLM's final opinion about whether a proposed action is safe.

## Database

Contains the schema, connection management where appropriate, seed data, database validation, and related persistence code.

## Tests

Contains unit tests and integration tests.

## Other infrastructure

Include an appropriate environment template, dependency file, `.gitignore`, README, and any additional small modules that simplify the implementation.

Use one canonical source of truth for each responsibility.

Do not create architecture for its own sake. Every module should have a clear purpose and should be used by the running application.

---

# 5. DEFINE A CLEAR DATA AND STATE MODEL

The application needs structured state that survives multiple steps of a workflow.

Do not represent the entire system using loosely structured strings.

Use Pydantic models for structured domain objects where appropriate, and use a compatible typed state representation for LangGraph.

## 5.1 Domain models

Define appropriate representations for:

* Support tickets.
* Ticket classifications.
* Customer records.
* Order summaries.
* Refund eligibility.
* Tool definitions.
* Risk factors.
* Risk assessments.
* Governance decisions.
* Human approval results.
* Tool execution results.
* Audit events.

Use consistent naming, validation, and types.

Avoid duplicate model definitions for the same concept.

## 5.2 Graph runtime state

The actual LangGraph workflow must have one canonical runtime state type.

It should hold the information needed across the execution of a support ticket, including:

* A unique run identifier.
* The ticket identifier and ticket contents.
* Customer and order identifiers where known.
* The conversation and relevant messages.
* Ticket classification and confidence.
* Retrieved business context.
* Proposed tool calls.
* Tool execution results.
* Risk assessments.
* Governance decisions.
* Human approval outcomes.
* Response drafts.
* Validation errors.
* Current workflow status.
* Audit references.

Use the appropriate LangGraph state-update mechanism for the installed version.

Avoid accidental mutation of shared state or conflicting updates from multiple graph nodes.

Domain models and graph state are related but different concepts. Keep them distinct rather than creating several competing classes all called `AgentState`.

The current customer support workflow does not need a separate runtime state for a hypothetical master agent.

---

# 6. BUILD THE POSTGRESQL BUSINESS ENVIRONMENT

The database is the environment in which the agent operates.

It must be a real database accessed through actual SQL operations, not a collection of hard-coded mock results.

Seeded records may be synthetic, but tools must retrieve and modify those records through PostgreSQL.

Do not expose a general-purpose SQL execution tool to the LLM.

Instead, provide narrow business operations with validated arguments.

## 6.1 Required data entities

Use the existing schema if it already supports the requirements. Otherwise, create the minimum coherent schema necessary.

The environment should support the following entities.

### Customers

Store stable customer identifiers, contact information needed for the local simulation, account status, and relevant customer metadata.

### Products

Store product identifiers, names, prices, and relevant product properties, including whether the product belongs to a limited-edition release.

### Campaigns

Store campaign information, the linked products, start and end dates, campaign status, and limited-production quantities when applicable.

### Inventory

Track product stock and the inventory changes associated with fulfilled orders and completed returns.

Do not rely solely on the number of orders to infer current inventory when an explicit inventory record is available.

### Orders

Store customer ownership, order status, order date, payment status, and the monetary totals required for customer support.

### Order items

Store the product, quantity, unit price, and any return-related quantities necessary to prevent the system from refunding more than was purchased.

### Refunds

Track the order and customer, requested amount, lifecycle status, reason, request time, processing time, and any other fields needed to enforce refund eligibility.

Support multiple refund records when partial refunds are legitimate.

### Support tickets

Store the ticket body, subject where available, customer and order links where known, category, sentiment, priority, workflow status, response draft, timestamps, and classification results as appropriate.

### Messages

Store relevant incoming messages and drafted replies, linked to tickets where appropriate.

Do not claim that a response has been sent externally when it has only been drafted or saved.

### Risk signals

Store structured signals associated with relevant customers, orders, products, campaigns, or other supported entities.

A signal should contain enough information to identify its type, severity, relevant entity, details, and observation time.

### Events

Maintain a history of relevant business events, including refund requests, refund processing, inventory changes, order updates, and significant support actions.

Support cross-entity references where appropriate so the risk evaluator can correlate events across customers, orders, products, and campaigns.

### Audit records

Persist important application decisions and executions.

Audit information should include the run identifier, proposed action or tool, relevant arguments, authorization decision, risk assessment reference or summary, decision explanation, execution result, and timestamp.

Record blocked actions as well as successful actions.

Never store credentials in the audit trail.

## 6.2 Database correctness requirements

Use proper foreign keys, suitable indexes, appropriate constraints, and consistent timestamps.

Use parameterized SQL for values.

Do not construct queries by concatenating untrusted customer text or LLM-generated SQL fragments.

Use transactions for operations that change several related records.

Use one clear database connection-management strategy and avoid leaking connections or cursors.

Do not have every tool implement its own unrelated connection logic.

Prefer a shared database layer that provides consistent connection handling and transaction management.

## 6.3 Safe database initialization

Startup must never erase an existing database.

Implement idempotent schema initialization and safe sample-data initialization.

Rules:

* Never drop and recreate existing tables automatically.
* Never truncate tables during normal application startup.
* Never overwrite existing business records just because the application started.
* Seed only when permitted by configuration and when the database is genuinely in an appropriate initialization state.
* Make the seed process repeatable without creating duplicates.
* Use stable identifiers or unique seed markers where appropriate.
* If the existing database has an incompatible schema, report the mismatch and explain the required migration or repair.

If seed data already exists, preserve it.

Do not assume that README dataset counts are correct. Derive the actual counts from the database.

---

# 7. CREATE REAL BUSINESS TOOLS

Build a set of narrow, useful tools that operate on the PostgreSQL environment.

Each tool must perform a real operation and return a structured, meaningful result.

Do not implement empty placeholder functions that return success.

Do not let the LLM access the database directly.

## 7.1 Customer tools

Provide the minimum operations needed to:

* Read a customer by identifier.
* Inspect a customer's relevant order history.
* Retrieve relevant customer activity when that information exists.

Return only the information necessary for the requested support task.

## 7.2 Order tools

Provide operations to:

* Read an order.
* Read its items.
* Check its current status.
* Calculate the amount already refunded.
* Determine its remaining refundable balance.

Validate identifiers and handle missing records explicitly.

## 7.3 Product and commerce tools

Provide operations to:

* Read product information.
* Check inventory.
* Inspect relevant campaign information.
* Read the inventory and campaign state needed to understand suspicious return patterns.

Do not confuse original production quantity with current available inventory.

## 7.4 Refund tools

Provide separate, clearly defined operations for:

* Reading refund history.
* Checking refund eligibility.
* Calculating a permissible refund amount.
* Creating a refund request.
* Approving a refund request.
* Executing an approved refund in the local business database.
* Rejecting a refund request where appropriate.

These operations must have different permission and side-effect classifications.

A refund calculation must not execute a refund.

Creating a request must not automatically mark it as processed.

Approving a refund must not accidentally execute it twice.

Executing a refund must verify that the refund is in an appropriate state.

All rules must be enforced in Python and PostgreSQL operations, not merely explained in a prompt to the LLM.

### Scope limitation for external payments

The first version may execute refund operations against its own PostgreSQL-backed business simulation. It must not pretend that doing this moves actual money through a payment processor.

Do not integrate a live payment provider as part of this iteration.

The application should clearly describe the boundary between its local business environment and external production systems.

## 7.5 Communication tools

Provide operations to read message history, inspect relevant messages, and save a response draft.

Saving a draft is not equivalent to sending a message.

Do not claim that an email, WhatsApp message, or external communication has been sent unless a corresponding real integration was implemented and the send operation actually succeeded.

External Gmail or messaging integrations are outside the required first-version scope.

## 7.6 Context tools

Provide the data access needed to inspect relevant context for a proposed action.

This should include appropriate access to:

* Customer history.
* Order history.
* Refund activity.
* Product and inventory state.
* Campaign state.
* Existing risk signals.
* Relevant events.
* Aggregate activity over a defined time window.

The system must be capable of combining information associated with different entities.

For example, it should not be limited to examining the refund history of one customer when the risk may arise from many customers returning the same product during the same campaign.

Implement aggregate queries in the database or a dedicated context service where that is more appropriate than retrieving large numbers of individual records.

---

# 8. IMPLEMENT A CENTRAL TOOL REGISTRY

The tool registry is the authoritative inventory of available business capabilities.

Every tool exposed to the LLM must be registered.

Every LLM-proposed tool call must be resolved through this registry before execution.

Each tool definition should include:

* Canonical tool name.
* Description.
* Validated argument schema.
* Executable implementation.
* Permission requirements.
* Inherent risk classification.
* Whether it reads or mutates business state.
* Side-effect classification.
* Reversibility information.
* Relevant action category.
* Any applicable execution restrictions.

Use consistent naming and reject duplicate registrations.

Validate arguments before calling the underlying function.

Reject unknown tool names, invalid arguments, missing required fields, and invalid identifiers.

Do not allow the agent to invent a Python function and invoke it.

Do not provide a generic tool that runs arbitrary Python, shell commands, or SQL.

## Important distinction

The tool's inherent risk classification describes the capability.

It does not fully describe the risk of a particular invocation.

For example, two calls to a refund execution tool may involve different amounts, orders, campaign states, and surrounding activity.

Keep tool metadata separate from the contextual risk assessment of the proposed action.

Do not attempt to solve this distinction by arbitrarily changing the tool's registered risk level for every invocation.

---

# 9. BUILD THE SUPPORT AGENT AND TICKET TRIAGE PIPELINE

The application needs a real LLM-powered agent, not merely a collection of models and helper functions.

Use the current compatible LangChain, LangGraph, and Groq integration available in the environment.

Inspect installed versions before choosing the exact APIs.

Do not use outdated API patterns if the installed versions no longer support them.

## 9.1 Agent responsibilities

The support agent must:

1. Understand the customer's complaint.
2. Identify the likely issue category.
3. Determine the urgency and sentiment.
4. Identify what information is missing.
5. Retrieve relevant business information when necessary.
6. Propose suitable tool calls.
7. Use tool results to understand the issue.
8. Draft a relevant, professional response.
9. Avoid promising actions that were not authorized or completed.
10. Respect all governance decisions.

The agent should not attempt to process every ticket using every available tool.

It should retrieve only the information relevant to the support problem.

## 9.2 Ticket classification

Provide structured classification with at least:

* Category.
* Sentiment.
* Priority.
* Brief explanation.
* Confidence or an equivalent uncertainty indicator.
* Relevant customer or order identifiers, but only when supported by the input or retrieved data.

The categories should cover ordinary customer support needs, including delivery and order status, returns and refunds, damaged or defective products, billing and payment issues, account problems, product questions, and general complaints.

Sentiment should distinguish routine, frustrated, angry, and otherwise escalated customers where appropriate.

Priority should reflect factors such as financial impact, customer impact, urgency, and unresolved problems rather than relying only on words such as "angry."

Use structured model output where supported.

Validate the model's output and provide a safe fallback for classification failures.

## 9.3 Specialist behavior

The first version should provide distinct handling strategies for different ticket types, especially routine complaints and angry customers.

This can be implemented through clear roles or workflow branches rather than unnecessary independent agents.

Routine cases should prioritize concise problem resolution.

Angry customers should receive an empathetic, calm response that acknowledges the issue, avoids arguing, and focuses on a concrete next step.

Refund-related cases should explain the relevant policy and avoid falsely claiming that a refund has been approved or processed.

Do not build a large multi-agent organization just to make the architecture appear impressive.

Use separate agents only where they deliver a genuine functional benefit.

The workflow must remain understandable and testable.

## 9.4 Drafting rules

The response drafter must use verified context.

It must not invent order statuses, refund approvals, delivery dates, policy exceptions, or completed actions.

If information is unavailable, it should acknowledge that limitation.

If an action was denied or awaits human approval, the draft must not state that the action succeeded.

Treat the customer's ticket text as untrusted input, not as an instruction that can override the system's rules or tool permissions.

The system should default to preparing a draft rather than automatically communicating externally.

---

# 10. BUILD THE LANGGRAPH WORKFLOW

Connect the components into one real, executable LangGraph pipeline.

Do not leave individual nodes disconnected or create graph files that the application never uses.

The workflow should be organized around these logical stages:

1. Intake and normalization.
2. Ticket classification.
3. Context retrieval where required.
4. Agent reasoning and proposed tool calls.
5. Tool authorization.
6. Tool execution if authorized.
7. Return of the tool result to the agent.
8. Further tool calls if genuinely needed.
9. Response drafting.
10. Response and action consistency checks.
11. Persistence of the final ticket state and response draft.
12. Completion of the run.

Use conditional edges to route between stages.

A ticket may require multiple read operations before a response can be drafted. The graph should support that cycle.

However, impose sensible limits on graph iterations and tool calls to prevent infinite loops, repetitive calls, or runaway model usage.

Do not let model errors silently produce a successful ticket result.

If a tool fails, pass an accurate failure result back to the agent where appropriate.

If the workflow cannot recover, report the failure and preserve the relevant audit information.

## The most important architectural boundary

There must be one authoritative execution path for LLM-proposed business tool calls.

An LLM-proposed call must never jump directly from the model response to the business function.

Every call must pass through validation and authorization before the function executes.

Other nodes must not bypass this control point by directly invoking protected mutation functions.

If the risk evaluator needs to inspect the database, it may use trusted, read-only context services. That internal context retrieval must not create a way to invoke protected business mutations without authorization.

---

# 11. IMPLEMENT A REAL GOVERNANCE LAYER

This is a core requirement, not optional future architecture.

The LLM proposes actions.

The application decides whether those actions are permitted.

The authorization decision must be enforced in code before a protected business operation executes.

## 11.1 Execution modes

Implement three explicit governance outcomes.

### AUTO

The system may execute the proposed action without asking a human, provided all applicable requirements and constraints are satisfied.

### ASSISTED

The action may proceed only after explicit human approval.

The system must pause the decision path, present the proposed action and relevant context, and wait for the human's decision.

### HALTED

The proposed action must not execute.

The system should record why it was blocked and communicate the next appropriate step.

A blocked action must not become executable simply because the LLM repeats the request or claims it is safe.

## 11.2 Policy enforcement

At minimum, the policy should distinguish among read-only operations, low-impact internal operations, refund requests, refund approvals, refund executions, and external communication.

Read-only operations may normally execute automatically after argument validation.

Drafting and classification should normally be autonomous.

Refund creation, approval, and execution must follow explicit business rules and permission requirements.

The initial system may permit small, eligible refunds to execute automatically under a configurable policy, while requiring approval for larger or uncertain cases. It may also require approval for particular action types regardless of score.

Define those exceptions explicitly.

A refund that violates eligibility rules must not be executed.

An unusually risky action must not become autonomous solely because the LLM describes it as routine.

External message sending should remain disabled unless a real integration and its authorization policy have been implemented.

## 11.3 Human approval

Implement a working terminal approval mechanism.

When an action requires approval, present:

* The action and target.
* The relevant arguments.
* The proposed side effect.
* The risk score and decision.
* The main supporting evidence.
* Any eligibility checks.
* The consequences of proceeding.
* The available decision options.

The human must be able to approve or reject the proposed action.

Only an explicit approval from the human operator may satisfy the human approval requirement.

The LLM must not be allowed to approve its own proposed actions.

A rejection must leave the protected business operation unexecuted.

An approval must not bypass unrelated hard policy restrictions, invalid arguments, refund eligibility checks, or duplicate-execution protections.

Keep the approval mechanism testable. Do not hard-code terminal input in a way that makes every governance test impossible to run automatically.

## 11.4 Enforce the decision at execution time

Do not treat governance as a recommendation that the LLM may ignore.

The business function must execute only when the authoritative execution layer has validated the action and obtained the required authorization.

If the system rejects a call, the tool function must not run.

This must be verified through tests.

---

# 12. IMPLEMENT THE FIRST RISK EVALUATOR

Build an initial working contextual risk evaluator.

It does not need to implement the final research contribution. It must, however, be a genuine, integrated mechanism that evaluates a proposed action before allowing a protected operation to execute.

Do not leave it as an unused class, a placeholder function, or a comment.

## 12.1 Its input

The evaluator should receive enough information to understand the proposed operation:

* The registered tool and its inherent risk metadata.
* The requested action and validated arguments.
* The affected customer, order, refund, or product where relevant.
* The potential consequence.
* Available business context.
* Relevant transaction and event history.
* Applicable policy conditions.
* Any important uncertainty or missing data.

## 12.2 Relevant context acquisition

Do not dump the entire database into the LLM.

Acquire relevant information through narrow database-backed queries.

Context retrieval should be conditional on the proposed action.

For example, a refund execution request may require the system to inspect:

1. The refund request and its current state.
2. The order and order items.
3. Payment status and previous refunds.
4. The applicable return policy.
5. Customer refund history.
6. Relevant customer and order risk signals.
7. Product inventory.
8. Campaign information.
9. Aggregate refund and return activity.
10. Relevant events around the period of interest.

A routine request to explain an order status should not automatically trigger the same investigations.

Reuse efficient aggregate queries where possible.

Do not repeatedly retrieve the same information without a reason.

If required context is unavailable, identify the gap rather than treating missing evidence as proof of safety.

## 12.3 Risk score

Implement an explainable score on a scale of 1 to 100.

The initial score may use deterministic, configurable rules.

This is preferable to allowing an LLM to return a number that has no reproducible relationship to the evidence.

The evaluator should consider, as appropriate:

* The potential impact of the action.
* The financial amount.
* Refund eligibility.
* The remaining refundable balance.
* The return window.
* Previous refunds.
* Relevant recent activity.
* Refund volume over a defined time window.
* Cross-customer or cross-account patterns when supported by available data.
* Inventory depletion and campaign state.
* Relevant risk signals.
* Important temporal patterns.
* Conflicting evidence.
* Missing information that materially affects the decision.

Do not invent links between customers or accounts. Only use relationships supported by stored data or explicitly defined signals.

Put weights, thresholds, and policy settings in a coherent configuration or policy module rather than scattering unexplained numbers across business tools.

Normalize the score into the specified range and document what it represents.

The initial score is an operational heuristic, not a scientifically validated probability that a customer is committing fraud. The README must describe that distinction accurately.

## 12.4 Explainability

The evaluator must provide structured results.

Include:

* The score.
* The recommended governance mode.
* The relevant evidence.
* The factors contributing to elevated risk.
* The relevant mitigating factors.
* Important missing information.
* The rules that affected the final decision.
* A concise human-readable explanation.

Where the score uses explicit factor weights, show those contributions consistently.

Do not invent precise percentage contributions for an opaque LLM judgment.

The explanation must be grounded in retrieved records, calculations, and policy decisions.

## 12.5 Risk and authorization are separate stages

Do not combine risk scoring and permission enforcement into one uncontrolled LLM call.

Use this logical sequence:

Proposed action → context acquisition → risk assessment → policy evaluation → authorization decision → possible execution.

The evaluator estimates how risky the action is in the present situation.

The authorization policy determines which execution modes are permissible for that action and that risk level.

A score by itself must never automatically authorize an action.

---

# 13. CORRELATE ACTIVITY ACROSS THE BUSINESS ENVIRONMENT

The risk evaluator must be capable of looking beyond a single customer or order.

This is important to the eventual research contribution.

Implement at least one working aggregate-risk scenario.

## 13.1 Limited-edition return scenario

Create deterministic seeded data representing a limited-edition earbud release with 500 units.

The release sells out.

A suspected coordinated group has acquired 200 units using multiple customer accounts.

Those units begin returning in an abnormal pattern near the end of the campaign.

Ensure that the seeded data actually establishes the intended relationships between relevant records, rather than merely inserting an event containing the words "possible fraud."

When evaluating a refund associated with that product, the system should be able to discover and explain relevant evidence such as:

* The original production quantity.
* The campaign and sell-out state.
* The relevant orders and customers.
* The concentration of returns.
* The time window in which returns are occurring.
* The relationship between the number of returned units and available inventory.
* Any corroborating risk signals.
* The resulting effect on the proposed refund action.

This scenario must exercise the actual database queries and authorization pipeline.

It must not be implemented as a hard-coded special case in which the product name alone causes a high risk score.

The goal is to establish a reusable aggregate-context capability.

## 13.2 Legitimate mass-return scenario

Create or preserve a separate scenario in which a large number of customers legitimately return a product because of a shared manufacturing defect.

The system must be able to inspect evidence supporting the legitimate explanation.

High activity should trigger investigation where appropriate, but high volume alone must not be treated as conclusive proof of fraud.

The legitimate scenario must be included in the automated tests.

These scenarios will be useful for later evaluating whether the autonomy controller distinguishes genuinely suspicious patterns from unusual but legitimate business events.

---

# 14. REFUND ELIGIBILITY MUST ENFORCE REAL BUSINESS RULES

Implement one authoritative refund eligibility service and make the refund tools use it.

Avoid duplicating inconsistent eligibility logic across several tools.

Eligibility should check, as applicable:

* Whether the customer and order exist.
* Whether the order belongs to the relevant customer.
* Whether the order has an eligible payment status.
* Whether the request falls within the configured return window.
* Whether the product or item is eligible under the defined policy.
* Whether an existing refund has already processed the requested amount.
* Whether the requested amount exceeds the remaining refundable amount.
* Whether the refund's current state permits the proposed transition.
* Whether required information is missing.

Make the return-window policy explicit, configurable, and documented.

Calculate order age from the stored order timestamp.

Use a consistent timezone strategy.

Do not claim that a return-window check exists unless the eligibility function actually enforces it.

If refund eligibility fails, the authorization layer must prevent refund execution.

If a refund is in an invalid lifecycle state, the system must refuse to execute it regardless of what the LLM requests.

---

# 15. REFUND EXECUTION MUST BE TRANSACTIONALLY CORRECT

Refund execution can change several related records.

Those changes must be handled as one logical transaction.

The system must not leave a refund marked as processed while the related inventory update or event insertion has failed.

The execution operation should:

1. Validate the refund identifier and arguments.
2. Open a database transaction.
3. Obtain the necessary locks or use suitable concurrency protections.
4. Recheck the refund's current state and remaining refundable amount.
5. Confirm that the action was authorized.
6. Apply the refund state transition.
7. Update inventory when the business policy requires it.
8. Record the associated event.
9. Record the relevant audit outcome.
10. Commit only when the required changes succeed.
11. Roll back the transaction if any required operation fails.

Choose a transaction boundary that prevents inconsistent business state.

Where database-level constraints or row locking are needed to prevent concurrent duplicate refunds, implement them.

Use an idempotency mechanism or equivalent state protections to prevent repeated calls from processing the same refund twice.

Do not treat an already completed operation as a new successful refund.

Be precise about inventory semantics. A refund does not necessarily mean that returned physical inventory can immediately be resold. Define and document the behavior in the local simulation.

---

# 16. MAKE THE TOOL EXECUTION NODE A SECURITY BOUNDARY

Create one clearly identifiable execution pathway responsible for turning proposed tool calls into actual business operations.

It must perform these steps in the correct order:

1. Resolve the proposed tool through the registry.
2. Reject unregistered tools.
3. Validate the arguments.
4. Check the applicable permission requirements.
5. Identify whether the operation reads or mutates state.
6. Acquire the necessary contextual evidence for protected actions.
7. Calculate the risk assessment where applicable.
8. Ask the authorization policy for the permitted execution mode.
9. Obtain human approval if the mode is ASSISTED.
10. Execute the operation only when authorization requirements are satisfied.
11. Record the result or denial.
12. Return the correct result to the graph.

The execution boundary must not trust an LLM-generated claim that a human has approved an operation.

Approval must originate from the application-controlled approval mechanism.

The execution boundary must also not trust an LLM-generated claim that it already checked eligibility. Eligibility must be checked by the application.

If the action is denied, return an accurate result indicating that it was blocked.

The agent may use that result to explain what happened, but it must not retry the same action indefinitely or achieve the same prohibited outcome through an alternative registered tool.

## Indirect bypasses

Consider the intended effect of an action, not only its tool name.

For example, if refund execution is forbidden because of a policy violation, the agent must not be able to accomplish the same prohibited outcome using a different tool that updates the refund or inventory in another way.

Every mutation pathway must be covered by the same authorization policy.

No other graph node should be able to call a protected mutation function directly.

---

# 17. DESIGN THE SYSTEM TO FAIL SAFELY

The system must distinguish between:

* A successful tool execution.
* A tool returning a legitimate empty result.
* A validation failure.
* A permission denial.
* A governance halt.
* A human rejection.
* An unavailable database.
* A model failure.
* A partial or incomplete investigation.

Do not convert an exception into a success-looking string and continue as though nothing happened.

Do not fabricate business facts if a tool fails.

Do not mark a ticket as successfully resolved when the response could not be produced or required business information could not be verified.

Use bounded retries only where appropriate.

Do not retry non-idempotent mutations automatically after an uncertain database outcome.

Return concise user-facing errors while retaining useful, non-sensitive diagnostic information in application logs.

Ensure the database connection and transaction resources are properly closed or rolled back after failures.

---

# 18. PERSIST AUDIT INFORMATION

The audit layer is part of the application, not an optional file that only exists in a proposed architecture diagram.

Record important stages of each run, including:

* Ticket intake.
* Classification outcome.
* Proposed tool calls.
* Authorization evaluations.
* Risk assessments.
* Human approvals and rejections.
* Blocked actions.
* Tool execution outcomes.
* Important business mutations.
* Final response draft.
* Final run status.

Include a consistent run identifier so records from one execution can be correlated.

Record the reason for important governance decisions.

Do not persist secrets, API keys, or database credentials.

Avoid logging unnecessary personal data.

Never present an audit record as proof that a transaction succeeded unless the operation was actually confirmed.

Audit failures must not silently turn an unsuccessful business mutation into a reported success.

The documentation should explain the limits of the audit trail and whether records are stored in the same database transaction as a business operation or through a separate logging mechanism.

---

# 19. SECURITY AND INPUT VALIDATION

Apply the following rules throughout the system.

* Never execute arbitrary LLM-generated Python or SQL.
* Never treat untrusted ticket content as system instructions.
* Validate identifiers and arguments before database operations.
* Use parameterized SQL.
* Keep credentials in environment configuration, not source code.
* Include a `.env.example` with placeholder values only.
* Ensure `.env`, virtual environments, caches, and sensitive local files are excluded through `.gitignore`.
* Avoid printing credentials in exceptions.
* Validate tool names and argument schemas.
* Do not accept a free-form model response as proof of human approval.
* Enforce authorization in executable code.
* Prevent duplicate refund execution.
* Make policy violations non-bypassable through alternative registered tools.
* Do not silently fall back to a different agent persona or a dummy tool when a required component fails.

If an agent persona or required configuration file is missing, report that problem clearly.

The application may provide a development fallback where it is safe, but it must disclose that fallback and must not use it silently in evaluation runs.

---

# 20. CONFIGURATION AND DEPENDENCIES

Inspect the current environment and reuse compatible installed dependencies wherever reasonable.

Maintain one dependency specification.

Do not add libraries merely because they are fashionable or convenient when existing packages can solve the problem.

Use the installed compatible versions of LangGraph, LangChain, the Groq integration, Pydantic, and the PostgreSQL driver where feasible.

If an API has changed between library versions, use the supported interface for the installed version.

Keep model selection configurable through environment variables.

Use the project's current intended Groq model if it is compatible with the required operations. Otherwise, provide a documented configuration for a compatible model and explain the change.

Do not hard-code a supposedly available model and silently assume that it supports tool calling.

Document required environment settings, including:

* The Groq API key.
* The model name.
* The PostgreSQL connection URL or the individual database connection settings.
* Any safe seed-on-start configuration.
* Relevant development or evaluation settings.

Do not commit `.env`.

The application should load environment configuration consistently.

A missing or invalid configuration should produce a clear error and explain the remedy.

Do not claim the application has been tested against a live external integration if you could not access that integration.

---

# 21. IMPLEMENT SAFE, DETERMINISTIC SAMPLE DATA

The first version needs data that makes the system genuinely demonstrable.

If useful seed data already exists, validate and preserve it.

Otherwise, create a deterministic seed dataset that supports normal customer support and the aggregate-risk scenarios.

At minimum, include:

1. A routine delivery-status complaint.
2. A legitimate damaged or defective product complaint.
3. A normal, eligible, low-value refund.
4. An ineligible refund request.
5. A complaint from an angry customer.
6. A ticket with missing order information.
7. A limited-edition product with an abnormal aggregate-return scenario.
8. A legitimate mass-return scenario associated with a manufacturing defect.
9. Records containing relevant but non-conclusive risk signals.
10. Tickets that can be used to test blocked actions and approval requirements.

The records must be internally consistent.

Orders must refer to existing customers. Order items must refer to valid products. Refunds must be consistent with the corresponding order and amount. Events and risk signals must reference meaningful entities.

Generate enough linked records to exercise the aggregate queries, but do not create thousands of arbitrary records just to make the database look large.

The limited-edition scenario must actually contain the 500-unit release and the intended 200-unit return pattern.

The legitimate mass-return scenario must be independently identifiable.

Use stable seed identifiers or keys so the dataset is repeatable.

Seeding must not create duplicated customers, refunds, or events every time I launch the application.

---

# 22. BUILD A USEFUL TERMINAL OUTPUT

The terminal must help me understand what the system did.

For each ticket, display information in a clear order.

### Ticket analysis

Show the ticket ID, category, sentiment, priority, and a short explanation.

### Retrieved context

Summarize the relevant customer and order information, if available.

Do not dump the entire database or every event into the terminal.

### Tool calls

For meaningful tool calls, show which operation was requested and whether it succeeded, failed, or was blocked.

### Governance

When an action is assessed, show its mode, risk score, main factors, and explanation.

When human approval is required, show the decision options.

When an action is halted, explicitly state that it was not executed.

### Response draft

Display the final draft in a readable format.

### Persistence

Confirm whether the ticket and draft were saved.

Never claim an external response was sent when it was only drafted.

The output should be detailed enough for debugging and demonstration without exposing credentials or flooding the terminal with raw internal data.

---

# 23. AUTOMATED TESTING IS REQUIRED

Do not stop after the project starts once.

Add automated tests that exercise the actual business rules and control boundaries.

Use the project's existing testing framework if one is already established. Otherwise, use an appropriate lightweight Python testing framework.

Separate tests that can run without a database from tests that require a configured PostgreSQL instance.

Do not require a live Groq API call for every unit test.

Use controlled test inputs and replace the external model boundary with deterministic test doubles where appropriate. These test doubles are only for testing; they must not replace the actual database-backed business tools in the running application.

## 23.1 Tool registry tests

Verify that:

* Registered tools can be resolved.
* Duplicate tool names are rejected.
* Unknown tools cannot execute.
* Invalid arguments are rejected.
* A successful tool call returns its actual business result.

## 23.2 Refund eligibility tests

Verify that:

* A valid order can pass eligibility checks when the policy permits it.
* An order outside the return window is rejected.
* An unpaid or otherwise ineligible order is rejected.
* An amount exceeding the refundable balance is rejected.
* Previous refunds are counted correctly.
* An already processed refund cannot execute again.
* Missing or inconsistent records fail safely.

## 23.3 Transaction tests

Verify that successful refund execution updates all the required records consistently.

Where practical, inject a failure during execution and verify that the transaction rolls back rather than leaving inconsistent state.

Test that duplicate execution does not process a refund twice.

Test inventory behavior against the documented return and restocking policy.

## 23.4 Authorization tests

Verify all three modes.

AUTO must permit execution only when policy and eligibility checks permit it.

ASSISTED must not execute an operation until a human approval has been recorded.

A human rejection must prevent execution.

HALTED must never call the protected business function.

Test that repeating a blocked request does not bypass the decision.

Test that choosing a different registered tool cannot circumvent a protected business rule.

At least one test must explicitly prove that a denied operation's underlying mutation function was never invoked.

## 23.5 Risk evaluation tests

Verify that:

* A normal, eligible refund receives the expected low-risk treatment under the configured policy.
* Elevated risk signals affect the assessment.
* An abnormal aggregate-return pattern is detected.
* A limited-edition sell-out and concentrated returns influence the explanation.
* The legitimate mass-return scenario is evaluated using its corroborating evidence.
* The risk score remains within the documented scale.
* Missing critical context is not silently treated as evidence of safety.
* A risk score alone does not override an absolute policy restriction.

Avoid tests that simply assert that the evaluator returns whatever number is hard-coded for one particular product.

Test the underlying evidence and decision rules.

## 23.6 Workflow tests

Verify that:

* A ticket can be accepted.
* Classification is saved correctly.
* Relevant context can be retrieved.
* A draft can be generated.
* Tool calls return their results to the graph.
* Governance decisions are respected by the graph.
* A blocked mutation remains blocked.
* Tool failures do not become false success reports.
* The graph terminates within its configured execution limits.
* The final ticket status and draft are persisted correctly.

## 23.7 Database validation

Provide a database validation command or script that checks the actual schema and records.

It should report:

* Required tables.
* Basic foreign-key or relational consistency.
* Relevant record counts.
* Presence of the required demo scenarios.
* Whether the limited-edition scenario is internally consistent.
* Whether the legitimate mass-return scenario is internally consistent.

Do not report predefined record counts as observed facts without querying the database.

---

# 24. PREPARE THE MAIN ENTRY POINT

The root `main.py` must be the obvious way to run the application.

It should invoke the real configured application rather than presenting a placeholder menu.

Starting it must initialize the required components and launch the interactive workflow.

It must not require me to manually run several Python modules in a particular order.

The application must close database resources cleanly when the session ends.

The startup process must be safe to repeat.

Provide clear error messages for missing dependencies, missing configuration, database connection failures, and incompatible schemas.

If an external model call fails during a ticket, report that failure accurately.

Do not silently switch to a fabricated response and claim that the LLM agent worked.

If a deterministic offline demonstration mode is useful, it may be included separately, but it must be labelled as offline and must not be confused with a live LLM run.

The primary interactive mode must use the configured model and the actual PostgreSQL-backed tools.

---

# 25. UPDATE THE README TO MATCH REALITY

Rewrite or repair the README after the implementation is complete.

It must explain the system that actually exists, not the system that might exist in the future.

Include:

* What BlackSmith does.
* The current scope of the project.
* A high-level architecture.
* The distinction between the LLM agent, tool registry, risk evaluator, and authorization layer.
* The supported business tools.
* The purpose of the PostgreSQL database.
* The role of the sample data.
* Setup requirements.
* Environment configuration.
* Database initialization.
* How to run the application.
* How to execute tests.
* How to validate the database and demo scenarios.
* How human approval works.
* What the risk score means and what it does not mean.
* What operations are simulated locally.
* Which external integrations are not yet implemented.
* Current limitations and planned extensions.

Include the actual commands needed to set up and launch the project.

Do not claim that Gmail is integrated if it is not.

Do not claim that an email is sent if the system only stores a draft.

Do not claim that the system dynamically learns or improves its policy unless that learning is implemented.

Do not describe governance as enforced until all relevant tool calls pass through the working authorization boundary.

Do not copy stale database counts or architecture descriptions from an older README without verification.

Be explicit that the current risk evaluator is an initial, explainable heuristic that will serve as a baseline for later research.

---

# 26. KEEP FUTURE RESEARCH FEATURES SEPARATE FROM THE FIRST VERSION

The project will eventually investigate context-aware and adaptive autonomy.

Do not pretend the first version already solves that research problem.

The first version must establish the infrastructure required to investigate it properly.

It should therefore have clear interfaces between:

1. The proposed action.
2. Context acquisition.
3. Risk assessment.
4. Policy evaluation.
5. Authorization.
6. Tool execution.
7. Recorded outcomes.

The initial evaluator may use deterministic rules and explicit features.

The architecture should permit it to be replaced or extended later with a more sophisticated evaluator that iteratively identifies relevant evidence, weighs contextual signals, accounts for uncertainty, and changes the permitted autonomy level as the situation evolves.

Do not implement a feedback-training loop, dynamic agent generation, or a large general-purpose orchestration system merely to fill the empty directories.

The next research milestone should be able to use this project to compare different authorization strategies on the same environment.

That means the following must already be clearly separable:

* Unconstrained or baseline agent behavior.
* Static business-policy authorization.
* The contextual risk evaluator.
* The policy that maps risk and consequences to authority.
* The actual enforcement mechanism.
* The test scenarios and measured outcomes.

The first version should make it possible to determine whether a particular action was proposed, allowed, blocked, approved by a human, or executed.

This separation is more important than adding more agents or more tools.

---

# 27. ACCEPTANCE CRITERIA

The implementation is not complete until the following criteria have been checked.

## Application

* The root `main.py` exists and launches the actual application.
* Startup errors identify their cause.
* The terminal accepts and processes a customer complaint.
* The configured LLM performs the real classification and support workflow.
* The system saves the ticket and response draft in PostgreSQL.
* The user can inspect the results.
* The application shuts down cleanly.

## Database

* The required schema exists.
* Database access uses PostgreSQL.
* Required records and relationships are internally consistent.
* Seeding is repeatable.
* Existing data is not erased at startup.
* Refund execution is transactionally correct.
* Database validation runs successfully against the configured database.

## Tools

* Registered business tools perform real database operations.
* Tool arguments are validated.
* The agent cannot execute unregistered tools.
* There is one canonical tool registry.
* Protected mutations cannot bypass the governance layer.

## Governance

* AUTO, ASSISTED, and HALTED are implemented.
* Risk is evaluated before protected operations execute.
* Human approval is controlled by the application.
* A rejection prevents execution.
* A halted action never executes.
* Refund eligibility is enforced independently of the LLM.
* Governance decisions are recorded.
* A failed action is not reported as a successful action.

## Risk assessment

* The evaluator uses actual retrieved business evidence.
* It can assess relevant cross-entity activity.
* It produces a score within the documented range.
* It explains the evidence and rules behind the decision.
* The limited-edition return scenario exercises the actual context and authorization pipeline.
* The legitimate mass-return scenario is supported by corroborating evidence and does not rely on return volume alone.

## Testing

* Unit tests cover the tool registry and business rules.
* Authorization tests prove that denied operations do not execute.
* Transaction tests cover successful execution and rollback where feasible.
* Risk tests cover suspicious and legitimate aggregate scenarios.
* Workflow tests cover the principal end-to-end behavior.
* The final test results are reported honestly, including any tests that could not be run.

## Documentation

* The README describes implemented functionality accurately.
* Configuration and launch instructions are correct.
* No exposed credentials remain in current source files or documentation.
* Planned capabilities are clearly distinguished from working functionality.

Do not mark a criterion complete merely because a corresponding file or class exists. Verify its behavior.

---

# 28. IMPLEMENTATION ORDER

Work in this order to avoid building disconnected components.

### Stage 1: Repository integrity

Inspect the workspace, identify what exists, resolve naming inconsistencies, examine the current environment, and determine which existing code can be reused.

### Stage 2: Database and business rules

Implement or repair the schema, connection management, seed data, validation, refund eligibility, and transactional refund operations.

Verify the database layer independently before connecting the LLM.

### Stage 3: Tool infrastructure

Implement the canonical registry, tool definitions, argument validation, and genuine database-backed business operations.

Test the registry and tools without the LLM.

### Stage 4: Risk and authorization

Implement the initial contextual risk evaluator and policy-based authorization layer.

Prove that the system can allow, pause, or block an action before any protected mutation executes.

### Stage 5: Agent and graph

Construct the support agent, ticket classification, context-retrieval workflow, governed tool-execution path, and response drafting pipeline.

Ensure the actual graph uses the implementation created in the previous stages.

### Stage 6: Terminal application

Connect the complete workflow to `main.py`.

Make the startup process, interactive commands, approval flow, and result display usable.

### Stage 7: Verification

Run the automated tests, initialize or validate the local database, perform a startup smoke test, and exercise the demo scenarios.

Resolve failures rather than simply listing them.

### Stage 8: Documentation

Update the README, environment template, dependency list, and any necessary setup instructions to match the tested implementation.

Do not stop after any intermediate stage. Complete the whole first-version implementation.

---

# 29. FINAL REPORT REQUIREMENTS

After implementation, provide a concise but concrete report covering:

1. What was actually present in the workspace when you began.
2. What you implemented or repaired.
3. The final project structure.
4. The database initialization and configuration requirements.
5. The exact command to launch the application.
6. The exact command to run the tests.
7. The tests that passed.
8. Any tests or integrations that could not be verified.
9. Any remaining defects that prevent the acceptance criteria from being satisfied.
10. The current limitations of the first version.

Do not claim success without running the relevant checks.

Do not claim that the entire system works if only imports or unit tests passed.

Where environmental access prevents verification, state precisely what remains unverified.

If you encounter a problem, make the best reasonable correction rather than stopping immediately and asking me what to do.

Do not push to GitHub, rewrite Git history, or discard unrelated work.

## FINAL INSTRUCTION

Build one coherent, functioning first version of BlackSmith.

The LLM should reason about the customer support problem and propose actions. The registered tools should operate on the actual PostgreSQL business environment. The risk evaluator should inspect relevant evidence. The authorization policy should decide what authority is allowed. The execution boundary must enforce that decision. The application should record what happened, produce a response draft, and let me inspect the outcome.

Every important component must be connected to the running application.

No empty architectural shells. No fake success results. No governance layer that only exists in the README. No direct path from an LLM-proposed mutation to execution. No unnecessary infrastructure that prevents me from testing the project locally.

Implement, test, verify, and leave the repository ready for me to run using `python main.py`.
