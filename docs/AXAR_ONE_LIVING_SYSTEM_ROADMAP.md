# AXAR ONE — Living System: product vision & engineering roadmap

Status: proposed, 2026-11-25. This is a design proposal, not a claim of implemented features. Integrate with existing AXAR POS, SAFE, BUILD, CONNECT and other work; do not create competing modules or rewrite the working POS. Names below are provisional capabilities, not necessarily separate services.

## North star

Build a human-centered operating system for businesses: software serves people, automates routine work, connects physical operations with digital intelligence, and enables increasingly autonomous operation within explicit owner-defined limits. Start with hospitality, then extend through adapters and reusable domain capabilities to retail, hotels, logistics and other industries.

Design lenses: first-principles simplification, electrical and physical systems thinking, real-world chef and hospitality operator workflows. These are inspirations, not endorsements or statements by named individuals.

## Capability portfolio

| Capability | Product behavior | Acceptance / constraints |
| --- | --- | --- |
| AXAR CHEF | Recipes, yield, unit conversions, portion scaling, costing, menu engineering, preparation plans, training and versioned technique cards; photo/voice intake | Verified ingredient master, effective-dated price and yield, human-approved allergen declarations; never infer food safety from AI alone |
| AXAR FLOW | Kitchen orchestration by table/course, predicted prep times, station capacity, equipment constraints, batching and simultaneous serving; manual override | Replay historic orders, compare late tickets and waste, show uncertainty, keep kitchen working offline |
| AXAR SAFE | Location-specific food safety checklists, fridge/freezer/hot-holding registers, sensor ingestion, deviations, corrective actions, traceability and audit exports | Configurable approved thresholds, calibration and missing-data alerts, signed human verification, no invented readings or automatically closed critical deviations |
| AXAR ZERO | Lot/expiry tracking, FEFO, waste weighing, demand planning, safe surplus redistribution and chef-approved specials | Never extend expiry or bypass food safety; trace every stock movement, measure waste per cover |
| AXAR ENERGY | Metering and forecasting of electricity, water, HVAC, cold storage, solar and batteries; safe scheduling of flexible loads | Observe first; hard temperature, equipment and electrical safety limits; local fail-safe control; no AI override of independent protection |
| AXAR ROBOTICS / EYES | Vendor-neutral sensors, scales, kitchen equipment, robots and privacy-preserving occupancy/queue counting | Certified device interfaces; scoped commands, device identity, signed firmware where supported, emergency stop, local interlocks; no facial recognition or employee emotion inference |
| AXAR CUSTOMER | Opt-in preference and accessibility profile, menu personalization, loyalty and feedback | Explicit permissions, revocation, data minimization, no sensitive profiling by default; approved allergen information always visible |
| AXAR SENSE / VISION | Real-time operational metrics, demand forecasts using weather/events/footfall, anomaly alerts, cash-flow and unit economics | Source provenance, freshness, confidence bands, baseline comparisons; actual vs forecast clearly separated |
| AXAR TWIN / TIME MACHINE | Event-based historical replay, digital twin of locations and operations, scenario simulation | Versioned inputs, reproducible assumptions, simulated data isolated from ledger; counterfactuals labeled estimates |
| AXAR AUTOPILOT / COUNCIL | Specialized agents propose, challenge and coordinate schedules, procurement, stock transfers and business decisions | Policy engine outside models; least privilege, explicit budgets, approval gates, idempotent actions, complete audit trail; humans remain accountable |
| AXAR BUILD / EVOLUTION | Natural-language configuration, workflow generation, improvement proposals and automated testing | Restricted schema/DSL, isolated sandbox, review and CI before deployment, canary and rollback; agents never rewrite production policy or grant themselves rights |
| AXAR FRANCHISE | Portable versioned operating model: recipes, menus, SOPs, equipment requirements, roles, training and configuration | Export/import with schema validation, local tax/legal settings, no secrets or customer data in templates |
| AXAR NETWORK / SWARM | Opt-in inter-company procurement, surplus exchange, partner discovery, privacy-preserving benchmarking; future marketplace adapters | Tenant separation, contractual data rights, aggregation thresholds, competition-law review; no sharing competitors' forward-looking prices or sensitive strategies |
| AXAR OPPORTUNITY / GENESIS | Explore and simulate new ventures, business plans, setup templates and operating models | Explicit assumptions and risk, no automatic incorporation, financing or unapproved capital commitments |

## Architecture foundations

- First inspect repository structure, current PRs and architecture decisions; map capabilities to existing code, document gaps, and reuse established contracts.
- Canonical multi-tenant domain model: organization, legal entity, site, outlet, device, employee role, product, ingredient, recipe version, lot, inventory movement, order, payment reference, supplier, safety observation, incident and policy. Stable IDs, units, currencies, timestamps and provenance.
- Transactional core and append-only audit/event history; versioned event schemas, outbox, idempotency keys, reconciliation, retry/dead-letter strategy. Do not assume all data needs event sourcing.
- Offline POS and local operational continuity; device-bound encrypted storage, bounded offline authorization, explicit conflict resolution and reconciliation after reconnection; payment behavior depends on certified provider capabilities.
- Adapter boundary for devices and external platforms; versioned contracts, capability discovery, test harness, simulators and health checks. Prefer modular monolith until service isolation or scale justifies extraction.
- Intelligence layer reads governed facts and emits structured proposals. Deterministic validation and approval service executes approved commands. Separate permissions for read, propose, approve and execute. Use shadow mode before limited automation. Emergency disable and manual fallback are mandatory.
- Security by design: tenant isolation tests; role and attribute-based authorization, MFA for administrators, encryption in transit/at rest with managed key rotation, secrets management, dependency and supply-chain checks, backup/restore drills, incident response, audit access controls. No raw card data, passwords, credentials or personal records in logs or prompts. Threat-model prompt injection, device compromise, cross-tenant leakage and replay.
- Privacy and regulatory readiness: local food safety, tax, payments, accessibility, employee privacy, retention and deletion policies. Verify requirements by jurisdiction before deployment; no blanket compliance claims.
- Operational observability: latency, sync lag, uptime, alert quality, cost and error budgets; correlate events without logging sensitive payloads.

## Delivery sequence and gates

### P0 — protect the core / hospitality pilot readiness
- [ ] Inventory existing PRs, issues and modules; produce capability map and architecture decision records for gaps.
- [ ] Validate POS order/payment/refund, offline and reconnect behavior, accounting exports, roles, stock ledger, recipe costs and SAFE workflows.
- [ ] Baseline security threat model, tenant isolation, CI, backup and restore; pilot test data only.
- [ ] Establish metrics and baseline, run staff usability testing, document manual fallback and rollback.
Gate: no lost or duplicate financial/stock entries in tested failure scenarios; critical safety/security issues resolved; approved release checklist. Target: pilot preparation February 2027, conditional on actual readiness.

### P1 — intelligent kitchen and resource control
- [ ] CHEF, FLOW, SAFE sensor adapters, ZERO, demand forecasts, location dashboards.
- [ ] Hardware simulator and a read-only sensor pilot; test calibration failures, out-of-order events and offline behavior.
- [ ] Compare forecast and orchestration against simple baselines; require chef/operator approval of safety-sensitive outputs.
Gate: validated improvement over baseline, no safety degradation, manual fallback demonstrated.

### P2 — digital twin and supervised autonomy
- [ ] Replay and scenario engine; proposal/approval/execution service; agent evaluations and red-team scenarios.
- [ ] Energy metering, equipment adapters, human-approved procurement and schedule recommendations.
- [ ] Canary deployments, hard policy limits and emergency stop; only narrowly scoped reversible actions may graduate to automation.
Gate: measured accuracy and operator trust, zero unauthorized actions in test suite, incident recovery exercised.

### P3 — extensibility and network
- [ ] BUILD/Evolution sandbox and template registry; FRANCHISE portability; governed partner API.
- [ ] Privacy-preserving benchmarking and procurement pilot, subject to legal review.
- [ ] Explore robotics control, opportunity discovery and cross-industry adapters after core stability.
Gate: portability, permission and tenant isolation tests, vendor interoperability, documented economics and regulatory review.

## Measure what matters

Set targets after gathering baseline: manager admin hours, time to train new staff, order throughput, p95 ticket time, order accuracy, waste per cover, contribution margin, forecast error, energy per cover, safety deviation closure, POS uptime, reconciliation exceptions, unauthorized actions and user satisfaction. Compare pilot with baseline and track seasonal confounders. Do not promise arbitrary gains.

## Handoff to Claude and other engineering agents

1. Review this proposal against current branches and existing work, including POS, CONNECT, BUILD and SAFE.
2. Report overlap and dependencies; open small, non-overlapping implementation issues with acceptance criteria and tests. Existing POS work remains priority.
3. Implement P0 prerequisites first. Use isolated branches, draft PRs, peer review and CI. No unreviewed production deployment or secrets exposure.
4. Record what is implemented, tested, blocked, proposed or deferred separately. Update this roadmap when actual evidence changes priorities.

Definition of done: requirements linked to tests, security and privacy review, failure-mode tests, operator documentation, migration/rollback plan, and measured acceptance results. Do not treat this document as permission for autonomous spending, physical equipment actuation, or production deployment.
