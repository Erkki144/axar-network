# 13 — Delivery journey: how AXAR ONE is built, by whom and with what

Status: **Proposed** (owner review). Complements `adr/ADR-0008-agent-coordination.md` (GitHub as system of record)
and `docs/DELIVERY_ROADMAP.md` (what comes next). This file says **how** every piece of work travels from idea to a running till.

## Kokkuvõte eesti keeles

Iga funktsioon läbib samad **7 etappi**. Igal etapil on vastutaja, kindel tulemus (artefakt) ja **värav**.
Edasi minnakse alles siis, kui värava tingimus on täidetud. Visiooni hoiab omanik. Tehnilise terviku hoiab arhitekt.
Kumbki ei jäta etappi vahele.

| Etapp | Küsimus | Tulemus | Kes otsustab väravas |
|---|---|---|---|
| 1. Visioon | miks ja kellele? | visiooni- ja põhimõtete dokument, prioriteet | omanik |
| 2. Avastamine | kuidas seda päriselt tehakse? | valdkonna süvaanalüüs: väljad, tähendus, töövoog, turu võrdlus | omanik kinnitab, et sisu on õige |
| 3. Disain | kuidas see AXAR ONE-s elab? | registrid, teekonnad, sündmused, ADR, ekraanivoog | arhitekt; omanik äriküsimustes |
| 4. Plaan | mis järjekorras ja millal on valmis? | GitHubi issue mõõdetava valmimiskriteeriumiga, tegevuskava rida | omanik kinnitab järjekorra |
| 5. Ehitus | — | mustand-PR: kood, migratsioon, testid, dokumentatsioon | CI (7 kontrolli) + sõltumatu ülevaade |
| 6. Vastuvõtt | kas see töötab päris inimestega? | demo, kasutajatest, raamatupidaja kinnitus, kui puudutab raha | omanik (+ raamatupidaja) |
| 7. Käitus | kas see töötab iga päev? | väljalase, monitooring, kooskõla aruanne, õppetunnid | omanik lubab väljalaske |

**Praegu oleme valdkonnas „tooted ja menüü“ etapis 2.** Süvaanalüüs on pinnapealne ja vajab kordamist (§4).

---

## 1. Roles

| Role (Eesti) | Who today | Responsible for | Decides | Does not do |
|---|---|---|---|---|
| **Omanik / visioonihoidja** (product owner) | the owner | vision, priorities, business rules, money, contracts, providers, release approval | what and why; go / no-go | technical design choices |
| **Arhitekt** (system architect) | Claude | register map, journeys, ADRs, data model, consistency of the whole | how; whether a change fits the whole | business priorities |
| **Analüütik** (domain analyst) | Claude, with the owner and the pilot staff | discovery: fields, meaning, workflows, market comparison, edge cases | completeness of the requirement | invents business rules without the owner |
| **Arendaja** (engineer) | Claude (coding agents per ADR-0008) | code, migrations, tests, PR evidence | implementation details | merges or deploys |
| **Ülevaataja** (reviewer) | a second, independent agent session | review of design and code before the owner gate | blocking findings | approves its own work |
| **Kvaliteet / test** | Claude + CI | test plan per acceptance criterion, E2E, reconciliation and tamper tests | whether evidence is sufficient | skips a failing test |
| **Raamatupidaja** | the owner's accountant | VAT, rounding, credit notes, export format, period close | anything legal / fiscal | — |
| **Piloodi meeskond** | pilot outlet manager + 2–3 cashiers / waiters | usability, real workflows, acceptance on site | "works for us" | — |
| **Partnerid** | terminal provider, hosting, accounting software | sandbox access, contracts, API changes | their own terms | — |

## 2. Stages, artifacts and gates

| # | Stage | Artifact (file / place) | Gate: we move on only when … |
|---|---|---|---|
| 1 | **Vision** | `products/axar-one/VISION.md` (to be written), principles, target customer, non-goals | owner signs the principles |
| 2 | **Discovery** | `docs/discovery/<domain>.md`: per screen and field: meaning, required, default, validation, where used, related fields; workflow and states; real examples; market comparison (functions only, no copying) | every field in the domain has a meaning and a "used by"; owner confirms |
| 3 | **Design** | update `12-register-map-and-journeys.md`; ADR if a rule changes; event schema in `contracts/events/`; screen flow (draft → preview → publish) | every register has an owner, an event, a journey and a reconciliation rule; reviewer agrees |
| 4 | **Plan** | GitHub issue (one per step), row in `docs/DELIVERY_ROADMAP.md` with dependency, measurable acceptance, test command | owner confirms order and scope |
| 5 | **Build** | draft PR: additive migration, code, unit + API + browser tests, docs, reconciliation rule + tamper test | the 7 CI checks green; reviewer has no blocking findings; PR lists what is still not proven |
| 6 | **Acceptance** | demo script, usability notes from the pilot team, accountant sign-off where money or VAT is touched | owner accepts; accountant accepts if fiscal |
| 7 | **Operate** | release notes, migration run, monitoring and daily reconciliation, incident log, lessons learned → back to stage 2 | 14 days green reconciliation at the pilot (P3-3) before wider rollout |

**Rule:** a stage may be short, never skipped. A one-line fix still has an issue, a test and a PR.

## 3. Tools

| Need | Tool | Where |
|---|---|---|
| Work tracking, decisions, review | GitHub issues, draft PRs, labels, claims (ADR-0008) | `Erkki144/axar-network` |
| Vision, discovery, design | Markdown in the repo; diagrams in Mermaid | `products/axar-one/architecture/`, `docs/` |
| Event contracts | JSON Schema, checked by `tests/test_event_contracts.py` | `architecture/contracts/events/` |
| Server | Python, FastAPI, SQLAlchemy 2, Alembic migrations | `products/axar-pos/axar_pos/` |
| Database | PostgreSQL 16 with row-level security (SQLite for fast local tests) | — |
| Office + till UI | browser ES modules on the same API; installed till wraps it (ADR-0012) | `axar_pos/static/one/` |
| Tests | pytest (unit, API), Playwright (browser E2E), reconciliation + tamper tests, load tests (P2-2) | `tests/`, `tests_e2e/` |
| CI | GitHub Actions: secret scan, SAST, tests SQLite + PostgreSQL, browser E2E, dependency audit | `.github/workflows/` |
| Market discovery | read-only browsing of competitor back offices with the owner's account; notes only, no copying | Claude in Chrome |
| Website (separate product) | Next.js on Vercel | `src/` |

## 4. Naming conventions

| Thing | Pattern | Example |
|---|---|---|
| Module | upper-case noun | CATALOG, PRICING, RECIPES, INVENTORY, POS, PAYMENTS, ORDERS, KDS, CRM, IAM, BOOKS, SYNC |
| Event | `<module>.<entity>.<past-tense verb>` | `pricing.price.published`, `stock.movement.posted` |
| Command (API) | `POST /api/v1/<module>/<entity>` + verb sub-resource | `POST /api/v1/pricing/prices/{id}/publish` |
| Reconciliation rule | `R<n>` + one-sentence invariant | R14 "sale line price = valid price version" |
| Requirement in discovery | `<DOMAIN>-<n>` | `CAT-12` "product must have a VAT class" |
| Gap | `G<nn>` | G13 discounts missing |
| Roadmap step | `P<phase>-<n>` | P1-5 |
| ADR | `ADR-<nnnn>-<slug>.md` | ADR-0016 |
| Branch | `claude/<topic>-<id>` (existing) | `claude/catalog-p1-1-…` |

## 5. Domain order (what we take through stages 2–7)

1. **Tooted ja menüü** (CATALOG + PRICING + RECIPES + POS layout) → P1-1, P1-5. *Now in stage 2; redo discovery properly.*
2. **Müük ja aruanded** (POS, PAYMENTS, ORDERS, reports) → P1-6.
3. **Ladu** (INVENTORY: count, write-off, transfer) → P1-7.
4. **Inimesed ja õigused** (IAM) → P1-8.
5. **Tühistused ja raamatupidamine** (POS + BOOKS) → P1-2, P1-3 with the accountant.
6. **Kliendid** (CRM) → P2-6.
7. **Seadmed** (installed till, printer, terminal) → P2.

## 6. Discovery template (stage 2), per field

| Field | Meaning (why it exists) | Required / default | Validation | Used by (till, stock, books, report) | Related fields / rules | AXAR decision (take / change / drop + why) |
|---|---|---|---|---|---|---|

## 7. Automatic guards (CI) — every piece in its place

Rules people forget are checked by machines on every pull request. A guard that fails names the missing piece and the fix.

| Guard | Who it replaces | What it checks | Where |
|---|---|---|---|
| **Event contracts** | integration architect | every event type the code can emit (also through helper functions) has `contracts/events/<type>.v<n>.schema.json`; no orphan schemas | `tests/test_guard_contracts.py` |
| **Register coverage** | data architect | every database table is watched by a named reconciliation rule or is declared "not a ledger" with a reason; every named rule exists | `tests/test_guard_contracts.py` |
| **Rule count** | tester | screens and tests count reconciliation rules from `reconcile.RULES`, never a hard-coded number | `reconcile.RULES` |
| **Numbering** | release / merge manager | migration, ADR and architecture numbers are unique in the branch (error) and against every other open PR (warning naming the PR and the renumbering fix); one migration head | `scripts/check_number_collisions.py`, CI job `numbering` |
| **Platform marks** | tester | tests that need Linux say so (`skipif` with reason); CI runs them all | `tests_e2e/test_durability.py`, `test_server_auth.py` |

Proof that a guard works is a deliberate break: e.g. a probe module emitting `drawer.cash.moved` without a schema fails the event guard (checked 27.11.2026).
