# AxisAI privacy-first product analytics v1

Task: AXISAI-V1-PRIVACY-FIRST-PRODUCT-ANALYTICS-FOUNDATION. Source preparation only, 2026-11-19.

**HOLD:** no merge, deployment, analytics activation, event-storage migration, SDK installation or App Store Connect change is authorized. Build 101119 is unchanged. All existing production flags are untouched. The new modules are unreferenced by the application and default disabled. No AWS commands or production reads were performed; this is a source audit, not confirmation of deployed runtime configuration.

## Deliverables

| Requested output | Decision / artifact |
|---|---|
| EXISTING_TRACKING_INVENTORY | [Source inventory](inventory.md): mobile no-op events; operational metrics; account-level AI billing telemetry; functional records; disabled web legacy tracking. |
| RECOMMENDED_ANALYTICS_PROVIDER | Conditional PostHog EU Cloud, behind a first-party validation/consent gateway. No direct mobile vendor SDK for the pilot. See [architecture and gates](privacy-architecture.md). |
| FIRST_PARTY_FALLBACK | Same closed gateway contract, approved first-party EU event store and scheduled aggregate SQL reports. No new storage or migration in these PRs. |
| EVENT_CATALOG | [Closed schema v1](catalog.json), [trigger contract](events-kpis.md). Client intentions cannot assert server success. |
| KPI_DEFINITIONS | [Eligibility, activation, retention, inactivity and reactivation](events-kpis.md). UTC storage; Europe/Istanbul cohort dates; consented observable cohorts only. |
| PRIVACY_AND_LEGAL_GATES | [Independent consent, minimization, vendor and transfer review](privacy-architecture.md). No inherited AI/health/landing consent. |
| BACKEND_IMPLEMENTATION_PLAN | [Source delivered and gated follow-ups](implementation.md). Pure parser + disabled in-memory review buffer; no route or producer wiring. |
| MOBILE_IMPLEMENTATION_PLAN | [Source delivered and gated follow-ups](implementation.md). Typed enums + disabled review buffer; existing no-op facade preserved. |
| DASHBOARD_SPEC | [Four dashboards and weekly founder report](dashboards.md). No live dashboards or real-user queries created. |
| APP_STORE_UPDATE_REQUIREMENTS | [Privacy labels, notice and future binary review](privacy-architecture.md#app-store-and-notice). No ASC mutation. |
| EARLIEST_SAFE_ACTIVATION | After separate founder approval of privacy review, source merge, storage/authority, staging qualification, future mobile release and explicit collection enablement. No calendar date promised; never retrofit collection into build 101119. |
| BLOCKERS | Independent analytics consent authority/UI, reviewed notices/legal basis, vendor DPA/transfer review, durable deduplication/erasure, transport/producer integration and runtime qualification remain activation blockers. Full signup attribution needs separately approved pre-signup consent. Coach topic aggregates need a separate minimization/legal review. |

The PRs are deliberately reviewable foundations, not rollout-ready collectors. Passing the memory-buffer tests does not satisfy the deployment gates. Event loss is preferable to affecting authentication, Coach, training, nutrition or workout operations. No historical content, AI usage log or functional record is backfilled into product analytics.
