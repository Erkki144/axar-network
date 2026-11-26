# AXAR ONE — päris lähtekoodi põhjal andmebaaside ja registrite tugevdamise plaan

**Kuupäev:** 2026-11-26. **Staatus:** teostatav ettepanek. Vaadatud AXAR main (PostgreSQL, SQLite, events, sync, journal, backup, configs, API, worker), PR #63 (R1–R13 mustand), PR #71 (#68 sularahakanded mustand), ADR-0002/0004, PR #47 varundus. Main ei ole automaatselt kõikide PR-ide tulemus.

## 1. Koodi põhjal kontrollitud faktid — kus on juba hea vundament ja mis vajab tööd

| ID / prioriteet | Täpne tõend praegu | Risk / korrektne ettepanek | Test, mis peab enne muutmist läbi minema |
| --- | --- | --- | --- |
| DB01 P0 | \`axar_pos/db.py::make_engine\` SQLite seadistab \`PRAGMA foreign_keys=ON\`, \`busy_timeout=10000\`, \`BEGIN IMMEDIATE\`; **ei seadista seal** \`journal_mode=WAL\` ega \`synchronous=FULL\` ega kohalikku krüptot | Eralda *edge* ja *demo/test* profiil; kasuta lokaalsel SSD-l SQLCipher või tõendatud ketta/full-device krüptot, WAL + FULL ja WAL checkpoint/backup. Ära sea WAL kõigile \`sqlite:///:memory:\` testidele. | tegeliku failipõhise edge instantsi PRAGMA read-back, krüptovõtme/restart/backup, 10k commit'i power-off test ja integriteedikontroll |
| DB02 P0 | \`config.Settings.pilot_problems()\` kontrollib demo off, PostgreSQL, eraldi app/admin URL ja SECRET_KEY; ei kontrolli hosti TLS, restore drill, backup age, printer/terminal readiness | Eraldi *deployment readiness gate* koos infrastruktuuri proovidega; **mitte** kõiki kontrollproove igale HTTP-käivitusele. Ei tohi märkida production ready, kui pole env + HIL + accountant sign-off. | proov: demo=1 -> käivitus keelatud, puuduv PITR/restore proof -> piloodi aktivatsioon keelatud, app runtime fail-closed critical mode |
| DB03 P0 | \`backup.py\` teeb pg_dump + sha256 + tabeli md5/rowcount; keeldub dump'ist, kui tabelid muutusid dump'i ajal; \`OPERATIONS_BACKUP.md\` ütleb et hosting, PITR, krüptitud offsite ja prod schedule **puuduvad** | Säilita praegune kontrollitud \`pg_dump\` kui loogiline ekspordi ja taastamise tööriist, kuid lisa *managed PostgreSQL physical/base backup + continuous WAL/PITR*. Dump, mis live writes korral katkeb, ei ole pideva restorability asendus. | live-traffic ajal 30-min backup + PITR→T-5min taastamisdrill teise isolatsiooni, kontrolli kogu outbox/inbox ning edge replay; mõõda RPO/RTO |
| DB04 P0 | \`db.request_factory\` seab PG tenant \`SET LOCAL app.tenant_id\` iga tehingu alguses; migrations tekitavad RLS poliitikad. App kasutab eraldi rolli, aga migratsioonideks kasutatav owner ei allu automaatselt RLS-le | Auditeeri iga uue tabeli RLS, owner bypass ja \`FORCE RLS\` vajadus; sessiooni taastamine koos connection-pooliga; teenus ei tohi vahetada tenant ID-d request body põhjal; composite FK või server-validated ownership. | kaks ettevõtet ja sama id; 403/404, SQL injection, ad hoc SQL with no tenant, connection pool reuse, views/functions owner invoker õigused |
| DB05 P1 | \`events.py\` on transactional outbox, gap-free per-tenant recorded_seq, inbox and consumers with leases; SSE endpoint \`/events/stream\` pollib DB-d \`stream_poll_s=0.5\` | Ei vaheta välja. Lisa indeksite/retention plaan, laged queue alert, outbox pikaajalise hoiustamise + replay kontroll. Kui 10→100+ aktiivset stream'i nõuab, benchmark LISTEN/NOTIFY ainult wake-up'iks või JetStream bridge; durable truth jääb PG-s. | consumer crash, poison replay, schema version drift, 10/100/1000 live clients mõõdetud DB connections + lag; index EXPLAIN |
| DB06 P0 | \`journal.py\` peab hash chain / monotonic seq; \`sync.py\` serveri cursor ja karantiin; \`offline.py\` offline cap kuni 72h | Loo päris *edge hardening* (installer, anti-rollback clock, local DB encryption key provisioning, disk-full alert, UPS/power cut, signed menu snapshots); ära looda üksnes Pythonis olevale journal koodile | 72h offline, wrong time, 0 disk free, abrupt power cut, Wi-Fi down but LAN up, stolen device, same seq different hash, out-of-order sync |
| DB07 P0 | \`consumers.stock_consume\` võtab sale SKU/qty ja kutsub \`inventory.consume_for_sale\`; #69 järgi \`consume_for_sale\` kasutab registreeritud kassa *ühte vaikimisi ladu* | Versioned stock-source rule per product/outlet ja võimalusel ingredient; every movement warehouse_id ning cost version; unknown rule FAIL/REVIEW, mitte valesse lattu | sama korv vesi baarilaost + burger köögilaost; koostisosa teisest laost; 2 worker retry; 0 topeltkulud |
| DB08 P0 | \`services._expected_cash_expr()\` main ei arvesta non-sale cash; PR #71 mustand teeb uued kassaliikumised | Enne PR #71 merge'i auditeeri SQLite/PG, RBAC, blind cashier, kassa close vs deposit, R1 reconcile ühilduvus. Ära dubleeri paralleelselt uut ledger'it | 100+600-400=300 ning aruanne müük=600, tamper fail, fail closed offline |
| DB09 P1 | \`tenant\` tähistab main-is sisuliselt üht firmat; \`legal_entity/site/outlet\` pole täielikult eristatud (#27 ja PR #63) | Tee additive migration: tenant -> legal_entity -> site -> outlet -> register, warehouses can serve more than one outlet; maksu/KM ja raamatupidamine **legaalselt ettevõtte** põhised. Katkesta cross-company scope igas query's. | kahe legaalüksuse eraldi VAT, kontoplaan, arve number; 10 outlet, 3 warehouses; RLS ja reports scope |
| DB10 P1 | \`outbox_events\` kannab sale/refund ja stock/books consumers; #63 reconcile R1–R13 on draft ja #47 backup on main | Ühenda #63 kontrollid worker scheduling ja owner'i NERVE Health vaatega; arvesta #68/#69 uued füüsilised liikumised, reconciliation reeglite ajalugu ja exception resolution | tahtlik 1-cent mismatch, missing stock, pending books ACK, missing print/job, negative cash; alert within configured worker cycle |
| DB11 P1 | \`products/axar-pos/README.md\` on bootstrap-tekstina osaliselt aegunud; repo root README räägib Next.js-ist, mitte AXAR ONE deploymentist | Üks näidistarbimise võimalus: canonical OPERATIONS / RUNBOOK / DEV-QUICKSTART; käivitus-, andmete ja seadmevaade peaks näitama konkreetset versiooni ja end-to-end readiness | roheline deploy ainult kui docs ja runtime health identifitseerivad õige build sha/migration head |
| DB12 P1 | Worker \`worker.run_once\` käib tenantide kaupa, kasutab PG app rolli; admin and recipient adapterid on eraldi | Worker peab kasutama lubatud tenant discovery/lease ja RLS õigustega protsessi, millel pole owner cred'i; fail-closed poison ja retry; backpressure | no-tenant sees 0 customer PII; 2 workers lease race, poison alert, cold restart pärast 1000 ootel sõnumit |

### Ranged piirangud

1. Ülaltoodu on **funktsioonide kaupa nähtud kooditõend**, mitte universaalne otsing kogu AXARi kõigisse tulevastesse harudesse.
2. \`PR #63\` ja \`PR #71\` on kontrolli hetkel mustandid, mitte juba \`main\` implementeeritud.
3. Kriitilisi DB muudatusi ei juurutata ilma staged migration'i, varukoopia ja regressioonitestita.
4. PostgreSQL-ist eraldi Redis/NATS ei paranda kadunud tehingut, kui selle kirjutamine algsesse ACID baasregistrisse pole korras.

## 2. Arhitektuurne valik: üks füüsiline baas, loogilised andmeomanikud

Alguses üks PostgreSQL cluster/primary, valikul hot standby ja PITR, moodulite *schemas* ja tabelid loogiliselt eraldi:
- IAM: tenant, legal_entity, site, outlet, register, employee, roles, scopes, device tokens.
- CATALOG: product, variant, pricebook, tax policy, recipe_version, modifier, menu_version, menu_target_ack.
- SALES: cart session, order, sale, immutable sale_line_snapshot, receipt_no, correction/credit_note.
- PAYMENTS: tender, payment_attempt/provider_state, provider settlement, cash movement, refund; **PCI kaardiandmeid ei säilitata**.
- INVENTORY: inventory_item, supplier_product, warehouse, stock_source_rule, batch, receipt, transfer, production, waste, stock_movement append-only, count.
- DELIVERY: kitchen_ticket, kitchen_station, printer_job + device_ack, QR order and pickup projection, external order inbox.
- BOOKS: chart mapping, export_queue, external_ack, bank statement, reconciliation.
- NERVE: event outbox, consumer inbox, dead letter, per-device journal, sync cursor/quarantine, command audit.
- INSIGHTS: read-only projections ja tehingulised aruanded. AI kirjutab soovituse/command proposal'i, **mitte** otse finants- või laorida.

See on loogiline andmemudel. **See ei nõua igale moodulile uut DB instantsi** ja võib alata ühisest \`public\` skeemist kuni migratsioonid on kontrollitud. Kasutusel olevate tabelite muutmine nimetamise pärast pole ise väärtus.

## 3. Indeksite, versioonide, lukustuse ja lõikeaegade poliitika

- Igal tenant tabelil \`tenant_id NOT NULL\`; vajadusel \`legal_entity_id\`, \`outlet_id\`, \`warehouse_id\` koos komposiitsemantikaga, FK kontrolliga; päringud filtrivad õigustega scope järgi.
- Rahalised summad \`bigint\` sente, kogused fikseeritud baasyhiku micro-units või \`numeric(p,s)\`, mitte ujukomaga hinnakalkulatsioon.
- Iga kande \`source_document_id\`, \`line_id\`, \`event_id\`, \`idempotency_key\`, \`request_hash\`, \`recorded_at\`, \`effective_at\`; unique business key ja optimistic \`version\`.
- Ette nähtud indeksid (katsetada \`EXPLAIN ANALYZE\` ja tegeliku kardinaalsusega): \`(tenant_id,aggregate_id,aggregate_version)\`, \`(tenant_id,recorded_seq)\`, \`(consumer,tenant_id,event_id)\`, \`(tenant_id,device_id,seq)\`, \`(tenant_id,warehouse_id,item_id,effective_at)\`, \`(tenant_id,legal_entity_id,business_date)\`.
- Invoice/sale, credit/refund, warehouse transfer double posting => append-only ja kompenseeriv dokument. Ärge tehke DELETE/UPDATE juba kinnitatud tehingutele.
- Retseptil \`recipe_version\`, brutokulu, kadu, tootmisrežiim MAKE_TO_ORDER / PREPRODUCED / STOCKED_GOOD; preproduction ei vähenda koostisosi sama müügi ajal uuesti.
- Inventuuri null != "loendamata": \`counted_qty=NULL\` = loendamata, \`0\` = loetud null. Ajaliselt paralleelne tellimus/müük ei tohi kaduda.
- \`business_date\` = müügikoha timezone + cutoff (UTC audit eraldi); kontor ei otsusta ühe juhusliku serveri süsteemiaja järgi; kontrolli DST vahetus.
- Retention: äriseaduse ja GDPR nõuetele vastav raamatupidaja/juristi kinnitatud poliitika; sündmusi ei saa kustutada üksnes seetõttu, et queue on ACKitud. Kriitiline on hinnata DB kasv, partition/tablespaces alles kui mõõdetud; arhiveeri sõltuvuse ja auditiga, mitte juhusliku cron DELETE-ga.

## 4. SQLite edge seadistusprofiil — kavand, mitte kohe pimesi kasutatav SQL

Ainult kohaliku failipõhise edge SQLite puhul; arenduse in-memory SQLite peab jääma oma profiili. Testi, kas SQLCipher integreerub valitud Python SQLAlchemy driver'iga ja kuidas võtmed turvaliselt salvestatakse.

\`\`\`text
PRAGMA journal_mode=WAL;          # FAIL if returned mode != wal
PRAGMA synchronous=FULL;          # journalled money durability, measure latency
PRAGMA foreign_keys=ON;          # every connection
PRAGMA busy_timeout=10000;       # planned: tune after concurrency tests
PRAGMA wal_autocheckpoint=1000;   # verify checkpoint/backup traffic
PRAGMA integrity_check;          # startup/drill, not every request
\`\`\`

- \`journal_mode=WAL\` lülitus on andmebaasi tasandi püsiv režiim, mitte lihtne võlusõna igas connect-hook'is. Tee kontrollitud ühekordne provisioning/warm-up ja read-back. \`synchronous=FULL\` on iga ühenduse poliitika; testis mõõda journal fsync ja p95.
- SQLCipher/testitud ketta krüpto + seadme turvaline credential vault; kui puudub, **ärge märgistage edge pilot-ready**. Krüptovõtme kaotus = andmete kaotus, seega key recovery ja eskalatsioon peavad olema juhendatud ning õigustega.
- Backup SQLite **online Backup API** või tested consistent snapshot; kunagi ära kopeeri ainult \`.db\` faili töötava WAL-i kõrvalt ega kustuta \`-wal\` ja \`-shm\` käsitsi.
- Kassa funktsionaalne online/offline olek: WAN/LAN/KDS/printer/terminal eraldi; internetita ei tohi sularahamüüki ega kviitungi järjekorda välja mõeldud rohelise staatusega kinnitada.
- \`synchronous=NORMAL\` WAL on kiirem, kuid rahalise commit'i puhul ei ole *power cut durability* sama. Valik peab põhinema päris seadme benchmark'il ja usaldatavusnõudel; SQLite dokumenteerib kadumisriski.
- Eralda seadme *local command log* ja *sync outbox*, sama ACID commit. Online ACK tuleneb serveri idempotentsest journal ingest'ist, mitte browseri optimistic animation'ist.

## 5. PostgreSQL töökindlus ja juurutus

- Kasuta **toetatud konkreetset PostgreSQL major versiooni** (praegu CI on PG16), mitte valimatult uusimat. Versiooniuuendus on eraldi migratsioon/restore test; testida sama versiooni dump/restore tööriistad.
- Tootmises eraldi DB **owner** migratsioonideks ja **app role** ühendusteks. Migratsioonid \`expand → backfill → dual-read/dual-write ainult kontrollitult → contract\` et vana kassa offline snapshot ei puruneks.
- PostgreSQL RLS töötab vaid õige DB rolli ja tenant kontekstiga; owner võib mööda minna, seega auditeeri privileges, SECURITY DEFINER funktsioone ja view'te invoker/owner konteksti.
- Seadista \(kindlate arvude määramine enne load testi on eksitav\): \`max_connections\`, app worker pool, \`statement_timeout\`, \`lock_timeout\`, \`idle_in_transaction_session_timeout\`, autoscale piirid, per-query trace. Otse API-lt 1000 ühendust PG-le ei ole strateegia.
- Managed PG backup: pidev WAL archiving/PITR, offsite encrypted backup, least-privilege restore credential, soovitavalt teine AZ. Replication ei päästa kasutaja juhusliku DELETE või rikutud koodi eest; vaja PITR mineviku hetke.
- Dev/stage/prod andmebaasid füüsiliselt ja õigustelt eraldi; testile mitte kunagi päris klienditokenid ega HR andmed.
- Tootmis-JWT/secrets/DB credentials keskses secret manageris, rotatsioon, logiredaktsioon. Edge lokaalsete krüptovõtmete varundus ilma neid POS browserisse loetamatult lekkimata.

## 6. Olekumasinad ja kohustuslik kaitse

| Äriteema | Minimaalne state machine | Valideerimine |
| --- | --- | --- |
| Makse | INIT → SENT → APPROVED / DECLINED / UNKNOWN → CAPTURED → SETTLED / REFUNDED / DISPUTED | UNKNOWN puhul päring pakkuja poolele sama transaktsiooni ID-ga; mitte uus charge |
| Müük | CART → QUOTED → ACCEPTED → RECORDED → CLOSED → CORRECTED | immutable snapshot, one receipt, one outbox |
| Köök | NOT_SENT → QUEUED → ACKED → PREPARING → READY → HANDED_OVER | print job ACK != cooked ticket; retry duplicate prevents double cooking |
| Laodokument | DRAFT → REVIEWED → POSTED või CANCELLED | POSTED only once, reversal new document |
| Menüü | DRAFT → APPROVED → PUBLISHED → ACKNOWLEDGED per target | late devices have last signed valid version |
| Raamatupidamine | QUEUED → SENT → PROVIDER_ACK → BOOKED (only if confirmed) | retry same invoice, no double posting |
| Offline sync | LOCAL_COMMITTED → PENDING → SENT → ACKED / QUARANTINED | contiguous seq, hashes, no silent dismiss |
| Cash drawer | OPEN → COUNTING → CLOSED / REVIEW | physical IN/OUT distinct from gross sales |

## 7. Võrdlusallikad

- PostgreSQL row policies: https://www.postgresql.org/docs/18/ddl-rowsecurity.html
- PostgreSQL continuous archiving/PITR: https://www.postgresql.org/docs/18/continuous-archiving.html
- SQLite WAL: https://www.sqlite.org/wal.html
- SQLite PRAGMA, synchronous durability: https://www.sqlite.org/pragma.html
- DB crash risk network file: https://www.sqlite.org/useovernet.html
- NATS JetStream: https://docs.nats.io/concepts/jetstream
- OWASP API Top 10: https://owasp.org/projects/api-security-project
- OpenTelemetry metrics and logs: https://opentelemetry.io/docs/concepts/signals/
