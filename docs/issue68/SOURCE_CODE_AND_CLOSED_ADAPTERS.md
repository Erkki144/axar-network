# AXAR ONE #68 — vajalik lähtekood, suletud liidesed ja P0 rahaliikumiste leping

**Koostatud:** 2026-11-26; status: koodiaudit + disain, mitte teostatud funktsiooni kinnitus. [K] tähendab AXAR main lähtekoodist kontrollitut; [A] kavandatavat koodi/andmelepingut; [S] kolmanda osapoole suletud või lepingulist liidest. Ektaco CC5 sisemist lähtekoodi ei kasutata ega vajata AXARi iseseisva POS-i loomiseks.

## 1. Olemasolev AXARi lähtekood [K]

| Osa | Lähtekood | Kontrollitud funktsioon / piirang |
| --- | --- | --- |
| Kassa HTML | products/axar-pos/axar_pos/static/one/index.html | kolm veergu, kiirmüük/lauad/köök/juht/ladu, ostukorv |
| Kassa kliendiloogika | static/one/app.mjs | renderProductGrid, renderTables, renderKitchen, renderManager, openDrawerDialog, closeDrawerDialog, openPayment, openBillPayment, openRefundDialog |
| Visuaalne süsteem | static/one/styles.css, integration.css | responsive olemas; teenindaja kõik töövood pole katetud |
| API/Pydantic | axar_pos/api.py | drawer open/close, sales/cash, orders, kitchen, payment, refund, auth, sync |
| Kassa äriloogika | axar_pos/services.py | _expected_cash_expr = opening_float + cash_sales - cash_refunds; **cash IN ja OUT puuduvad valemist** |
| Makseolekud | axar_pos/payments.py | cash + simulated card + transfer; terminali provider end-to-end veel mitte tõendatud |
| DB mudelid | axar_pos/models.py | DrawerSession, Sale, Payment, Order, Product; eraldi cash movement ledger vaja |
| Migratsioonid | axar_pos/migrations/versions/0001..0008 | järgmine revisjon tuleb valida pärast teiste avatud PR-ide kontrolli |
| Autoriseerimine | axar_pos/auth.py | PIN, TOTP, rollid, device binding, juhi step-up; cash_out approval action laiendada |
| Transactional outbox | axar_pos/events.py, consumers.py | cash movement event ei tohi käivitada müügitulu või lao mahakandmist |
| Offline | journal.py, offline.py, sync.py, sync_agent.py | olemas device journal; cash movement offline replay vajab eraldi tõestust |
| Testid | tests/test_drawer_and_reports.py; tests/test_auth.py; tests/test_registers_and_concurrency.py; tests_e2e/test_cashier_ui.py | avamine/sulgemine, rollid, brauseri põhikäitumine; cash IN/OUT testid puudu |
| Registrite kontroll | PR #63, reconcile.py | 13 reegli mustand, cash movement tuleb sinna siduda ilma R1 rikkumata |

**Oluline:** PR-id #63–#66 on avatud eraldi harudes; ühegi neist teostust ei loeta automaatselt main-i osaks. Lõplikul töö alustamisel kontrollida merge-base ja avatud muudatused.

## 2. Meie enda kirjutatav AXARi kood [A]

| Etapp | Muudetav fail / uus moodul | Töö | Mõõdetav tulemus |
| --- | --- | --- | --- |
| P0 Schema | models.py, migrations/versions uus | append-only DrawerCashMovement; DrawerSession IN/OUT summad; indeksid/FK/RLS | migratsioon up/down/up, tenant isolation |
| P0 Service | services.py või cash_drawer.py | guarded row update + idempotency + available cash + expected sum | 100+600-400=300; sama käsk üks kord |
| P0 API | api.py, auth.py | POST /drawer/{id}/movements ja GET /drawer/{id}/movements; approval token | 400/403/409/422 ja audit |
| P0 Events | events.py, reconcile.py (pärast #63) | drawer.cash.moved.v1; raha kassas ≠ käive | tamper/mutation test |
| P0 UI | static/one/app.mjs, index.html, integration.css | Tegevused → Sularaha sisse/välja, põhjus, kinnitaja, pärast kande tegemist uus summa | Playwright test |
| P0 offline piir | sync.py, journal.py | kuni offline cash custody leping on tõestatud: cash OUT serverita blokeeritud | mitte ükski kadunud kassaliikumine |
| P1 cashier actions | menu/orders/KDS/discount/receipt | 80 nupuga masinloetava kataloogi puuduolevad töövood | õiguste ja vigade E2E |
| P2 devices | adapterid: terminal, printer, kaal, KDS | ametlik vendor sandbox/riistvara | HIL tõendus |
| P3 rollout | ops/monitoring/reconcile | 10 müügikohta, 72 h katkestus, 14 päeva varipiloot | 0 kaotatud/duplitseeritud sündmust |

## 3. #68 cash movement täpne ärireegel

**Append-only tabel:** DrawerCashMovement: id(UUID), tenant_id, register_id, drawer_session_id, direction (IN/OUT), amount_cents (positiivne integer), reason_code (SAFE_DROP, FLOAT_TOPUP, CASH_CORRECTION, BANK_DEPOSIT, OTHER), reason_text, actor_id, approval_id, custody_receiver, idempotency_key, request_hash, correlation_id, recorded_at/occurred_at. UNIQUE(tenant_id, idempotency_key). Iga muutus seotud avatud sahtliga; ükski kaart, pank, kinkekaart ega soodustus ei ole füüsiline cash IN/OUT.

**Valem:** expected_cash = opening_float + captured_cash_sales - cash_refunds + non_sale_cash_IN - non_sale_cash_OUT.

**Näide:** 100€ algraha + 600€ sularahamüüki - 400€ üleantud seifi = 300€ kassas. Müügitulu jääb 600€, KM jääb algsetelt müügitehingutelt muutumatuks.

**POST /api/v1/drawer/{id}/movements:** Idempotency-Key (8–128 tähemärki), BODY näide: direction=out, amount_cents=40000, reason_code=SAFE_DROP, reason_text=Seifi üleandmine. Approval peab tulema serveri poolt allkirjastatud lühiajalisest step-up tõendist, **mitte** suvalisest kliendi sisestatud approver_id väljast. Same key+body: tagasta sama movement. Same key+other body: 409. Closed drawer: 409. Permission fail: 403. Zero/negative amount: 422. OUT ei tohi vaikimisi viia loendatavat raha alla nulli.

**Concurrency:** insert movement + increment DrawerSession running totals + outbox event sama ACID tehingu sees; row-level lock / guarded UPDATE `status=open`; paralleelse sulgemise korral üks on enne teist, teine näeb õiget tulemust. Tõrke järel replay sama idempotency key'ga. Reversals ainult uue kompenseeriva kandena, mitte algse kirje DELETE/UPDATE. Juhi kinnitaja, põhjus, raha vastuvõtja ja ajaline jälg püsivad.

**Kassa pimevaade:** cashier ei saa API JSON-ist välja lugeda sahtli oodatavat summat, kui policy forbids; server redaction, mitte UI peitmine. Kassapidaja saab algatada, kui server lubab; suure summa OUT kinnitab manager/owner. Offlines see on P0 fail-closed, kui pole testitud signed local cash custody protokolli.

**GET /api/v1/drawer/{id}/movements:** scoped paged append-only history; ainult vastava registri ja õigustega vaade; korrektsioonid eraldi rea kujul.

**Reconcile:** movement IN/OUT summad võrduvad cached totals'iga; sulgemise loetud summa = expected + variance; cash movement ei muuda gross_sales/net_sales/VAT/stock; ebaõnnestunud serveritransaktsioon ei tohi jätta vastukannet poolikuks.

## 4. Suletud kolmandate osapoolte liidesed [S]

| Provider/ala | Millel on kinnine või lepinguline piir? | Mida on AXARi jaoks vaja? | Milleni ei tohi spekuleerida |
| --- | --- | --- | --- |
| Ektaco CC5 | nende sise-API, server, schema, offline-protokoll, litsents | nende süsteemiga asendamist tehes MITTE midagi; oma andmete migratsioonil volitatud eksport/andmeskeem | pole õigust kopeerida lähtekoodi või suletud endpointi |
| EFT terminal / pankade acquiring | ECR/terminal protocol, capture, void/refund, merchant settlement, SDK certification | valitud pakkuja ametlik SDK, test-merchant, sandbox, signed status callbacks, seadme mudelid | simuleeritud kaart ≠ päris kaardimakse |
| Android SmartPOS | sertifitseeritud terminali OS, secure API, app distribution/MDM | tootja partner SDK, sertifitseeritud testseade | tava-Android rakendus ≠ payment SDK |
| ESC/POS kviitungiprinter | ESC/POS osaliselt standardne; USB/LAN/Bluetooth driver/spooler mudelipõhine | kinnitatud mudel, käsud, testseade, print receipt ACK | tekstikviitung ei tõenda printimist |
| Köök / KDS / printer | terminal/station routing ja printer bridge sõltub reaalsest topoloogiast | oma versioned tickets + dedup + printer SDK ainult vajadusel | SSE event ei tõenda füüsilist väljatrükki |
| Wolt/Bolt | partner account, menu/order endpoints, signed webhooks, payout | ametlik partner onboarding, API leping, testorder, signed webhook | veebijuhendi nimetused ei ole API-spetsifikatsioon |
| RIK, Telema, Merit, Directo | lepingu/saajaga seotud API, e-arve skeem, ack, credentials | provider testkonto, mapping, XML/ISO formaadid, receipt status | HTTP 200 ei tähenda saajale kättetoimetamist |
| Pangad/open banking | PSD2 consent/partner license, connector secret, statement format | kinnitatud CSV/CAMT.053 variant või litsentsitud partner + sandbox | cashier „ülekanne“ ei võrdu laekumisega |
| Apple/Google Wallet | developer credentials/pass signing/push | enda arendajakonto, signer ja stored-value token source of truth | wallet pass ei hoia ise AXARi raha saldot |
| Kaal/vöötkood | seadme draiver ja kontrollitud mõõteseade | kaaluprotokoll, legal calibration, SKU mapping | ühendatud kaal ei tähenda korrektset müügihinda |
| TV/QDS | tootja MDM/HDMI-CEC võib olla kinnine | oma signed read-only web player; seadme heartbeat | iga teleri kaug-sisselülitus pole tagatud |
| Raamatupidamiskontod | päris konto/maksukoodid ettevõtte ja riigi lõikes | raamatupidaja kinnitatud mapping + destination ACK | programmeerija ei otsusta maksuarvestust omavoliliselt |

**Põhimõte:** kõik vendor-liidesed on adapterid, mitte POS-i domeenimudeli osa. Provider võib puududa või tõrkuda, kuid cash sales, immutable receipts, idempotency, stock ledger ja audit ei tohi seetõttu rikkuda. Krediitkaardi PAN/CVV ei kuulu AXARi andmebaasi ega logidesse. Päris API lepingute täpsed väljad/hinnad/õigused tuleb hankida ametlikult, mitte „ära arvata“.

## 5. Liideste programmilised piirid [A]

~~~python
class TerminalAdapter(Protocol):
    async def start_payment(self, amount_minor, currency, register_id, idempotency_key): ...
    async def get_status(self, provider_transaction_id): ...
    async def refund(self, original_provider_transaction_id, amount_minor): ...
    async def settlement_report(self, merchant_id, business_date): ...

class PrinterAdapter(Protocol):
    async def enqueue(self, document_id, template_version, device_id): ...
    async def status(self, job_id): ...

class AccountingAdapter(Protocol):
    async def submit(self, immutable_document_hash, legal_entity_id, mapping_version): ...
    async def read_ack(self, external_reference): ...
~~~

Need: provider-specific model, timeout unknown/reconcile, duplicate retry, status provenance, signed webhook validation, never print/card repeat as second charge. Cashier JS calls own AXAR API, not arbitrary terminal vendor endpoint with long-lived secrets.

## 6. Definition of done ja tests

- DB migration up/down/up + no parallel migration heads.
- PostgreSQL row-level tenant isolation in non-owner app role + SQLite and Postgres concurrency.
- 22 test scenarios: basic IN, basic OUT, safe drop 100+600−400=300, same key replay, key/body conflict, closed drawer, depleted cash, parallel OUT/close, parallel duplicate, role step-up, tenant and bound register, cash≠revenue, event only once, audit actor/reason, migration, rollback, tamper, pimekassa, offline block, two employees one drawer, 10 outlets, browser click.
- UI 80 actions must have evidence classification (present/missing/provider), no fake demo card availability.
- External hardware: provider sandbox and HIL proofs are independent gates, not a result of repository CI.
- No main merge, production deploy, real payments, purchases or productive data changes without separate owner approval.
