# AXAR ONE — NERVE piloodi valmisoleku- ja vastupidavuskontroll

**Seis:** projekteerimis- ja testiplaan 2026-11-26. Ei tõenda tootmiskõlblikkust. Omanik ei pea süsteemi ise ehitama: inseneri/Claude vastutus on teha automaatne install, seadistus, testid, taastamine, tõendite kogumine ja arendus-PR-id. Omaniku eraldi otsused: majutus, seadmetootja, makseteenus, kulud, ametlik live juurutus.

## 1. Soovitatav riistvaraline ja võrgu topoloogia 10 loomaaia müügikohale

- **Iga kassa:** lukustatud Windowsi/Androidi POS seade, püsiv lokaalne salvestus, krüptovõti seadmepõhises credential store'is, automaatkäivitus, PIN/muud volitused, kohalik log/sync agent. Füüsiline tšekiprinter võib olla USB või lokaalses LAN-is; printeri toe/USB loadimise test.
- **Võrguosa:** kahe tsooniga LAN/VLAN (POS/till/terminal ja külaliste Wi-Fi eraldi), TLS ja outbound firewall. Kassadevaheline LAN toimib ka WAN-rikke korral; mobiilandmeside varuühendus ainult kui kulud/levi õigustavad, mitte default garantiina. Router/switch/AP UPS vähemalt piloodi kriitilistes müügikohtades; toite ajaks mõõta katkestuse simulaatoriga.
- **Köök:** KDS-ekraan / printeri bridge samas hallatud sisevõrgus, station routing (baar/köök/jäätis). Eraldi tööjärjekord ja ACK, ajatempli ning order_ticket_id järgi dubleerimise kontroll.
- **Terminal:** ainult ametlikult sertifitseeritud pakkuja seade; füüsiline provider reference, refund/reversal ja kassaseadme sidumine. Võrk vs provider approval eraldi olek.
- **TV/QDS:** kiosk-brauseri read-only ekraan. Teleri lülitamine/MDM oleneb konkreetsest mudelist; häire korral kuvab viimast kinnitatud menüüversiooni või tellimuse numbri, mitte suvalist uut hinda.
- **Keskus:** 1 hallatav PostgreSQL primary + PITR; üks FastAPI teenus (võib horisontaalselt skaleeruda) ja eraldi event- ning sync-worker. 1 status server ei asenda redundantset salvestuskihti. Redis/NATS ainult põhjendatud benchmark'iga.
- **Võrgudomeen:** lokaalset arveldamist ilma keskserverita tohib teha üksnes volitatud režiimis. Jagatud lauaarvete offline koordineerimist kassade vahel ei ole täna tõendatud; kas block või eraldi kohalik site authority, mida tuleb failover-testida.

## 2. Seadistusregister (iga ettevõte / objekt / müügipunkt)

| Konfiguratsioon | Kellel on viimane sõna | Avaldamine / piirangu näide |
| --- | --- | --- |
| Tenant ja legal entity, KM, dokumendi number | raamatupidaja + õigusvastutaja; autoriteet server | periodiseeritud policy, mitte kassa kliendi kell |
| Site, outlet, kassa, ladu, source rule | admin kinnitatud | per-register config ACK; väär stock source = block |
| Menu/pricebook/retseptiversioon | menu/chef approved | signed version + effective_at, historical snapshots immutable |
| Staff and device roles | IAM, least privilege | per-company + per-outlet + per-register; manager approval token |
| Cash rounding, drawer safe-drop, pimekassa | finance + kassahaldur | server-side redaction, cash movement not revenue |
| Terminal device binding and status | provider + technical admin | test/sandbox/live modes visibly distinct; unknown not charged twice |
| Kitchen & printer routing | outlet manager + technical admin | ACK/retry/alert; delivered != prepared |
| Offline max hours and last contact | organization policy | 1–72h currently in code; legal and security review |
| Printer/teller template | approved per hardware | signed version + fallback, text width and copy mark |
| Kassa uuendus / rollback | rollout controller | canary 1 → 2 → 10, version reports and undo |
| Alert thresholds, SLO, recovery objectives | service owner/engineer | measured target, auditable proof |

## 3. NERVE Health dashboard: soovitatud ühe ekraani tulevik

**Üks juhtimisvaade, mitte 15 eri administraatorilehte.** Omaniku vaade näitab ettevõtete/müügikohtade staatusi, rahavoogude ja laoseisu erinevusi ning andmeid ootavaid ühendusi. Tehniline drill-down kuvab iga sündmuse teekonna.

\`\`\`text
AXAR NERVE HEALTH — näidis, mitte reaalajas mõõdetud andmed

ASUKOHT         KASSAD     MÜÜGID      SYNC JÄRJEKORD   MAKSE UNKNOWN   KÖÖK    BOOKS ACK
Loomaaed        10 / 10    OKEI        2 ootel         0              OK      1 ootel
Kohvik Fahle      1 / 1    OKEI        0               1 REVIEW       OK      OK

Hoiatus: PRINTER 3 offline 2 min | OUTBOX books.export peatunud | 1 STOCK mismatch

Kliki: SALE#... → UI käsk → DB commit → event recorded_seq → stock.consume /
  books.export / KDS ticket → provider ACK → päeva reconciliation.
\`\`\`

**Sündmuste nähtavus:**
- OpenTelemetry trace_id/correlation_id/causation_id igas API -> outbox -> consumer -> provider job teekonnas; logi ei sisalda PIN-i, makseandmeid ega liigseid isikuandmeid.
- Mõõdikud: per-outlet p95/p99 sale latency, journal fsync, age oldest unacked, outbox lag, inbox/dead letter, device last_seen, seq gap, WAL/PITR archive lag, PITR drill age, restore duration, KDS/unprinted ticket, card UNKNOWN pending, stock mismatches, manual refunds.
- Ärihäired: „Müük kinnitatud, laokanne puudub”, „Kaardimakse teadmata olek”, „Kassa ei saada offline-kirjeid”, „Arve eksporditud, kuid saaja ACK puudu”, „Inventuur null vs NULL”.
- Oluline: tehnilise tervise roheline ei tähenda, et raha on tegelikult pangas laekunud. Iga lüli peab näitama oma tõeallikat ja kellaaega.
- Alert'idel on vastutaja, prioriteet, eskalatsiooniaeg ja sulgemise põhjendus, mitte lihtsalt e-kiri.

## 4. Kohustuslikud kontrollülesanded: millal võib panna pilooti?

| GATE | Täpne tõestus | Piloodi piir |
| --- | --- | --- |
| G-00 arhitektuur | ADR-0015, data owners, event/protocol diagram, service dependencies reviewed | ei käivita töötajale live nuppe enne vastutuse selgust |
| G-01 migratsioon | PostgreSQL andmeskeem, tenant RLS, app non-owner grants; migrations up/down/up; checked newest PR heads | migration conflict või privilege leak -> BLOCK |
| G-02 raha | päris idempotency, split tender/unknown, refund, cash move; 1 sale=1 receipt=1 money effect, 0 double charges | failing core invariant -> BLOCK |
| G-03 laod | barcode/recipe/preproduced, warehouse routing #69, count cutoff & post once, refund restock vs writeoff | wrong warehouse -> BLOCK |
| G-04 NERVE | outbox/inbox, poison vs retry, aggregate ordering, queue monitor; 13+ reconciliation checks including #68/#69 | missing event/ACK without visible exception -> BLOCK |
| G-05 edge | encrypted SQLite WAL+FULL on local SSD, hash-chain restore, 72h offline policy, power-off/no network/disk full tests | lost confirmed local sale or untested encryption -> BLOCK |
| G-06 printer/KDS | printer model HIL: queue + ACK, retry outcome, customer receipt, KDS duplicate prevention | untested hardware -> DISABLE that capability |
| G-07 terminal | provider sandbox + real terminal HIL, approved/declined/unknown/reconcile, actual merchant settlement contract | *mock not accepted*; live card button OFF |
| G-08 BOOKS & BANK | correct VAT/tender/fees mapping, send/receive ACK, bank reconciliation of provider settlement | no accountant approved mapping -> don't claim settled |
| G-09 backups | continuous WAL/PITR encrypted offsite, successful restore on isolated host with real-sized data, RPO/RTO measured | missing drill or expired WAL -> BLOCK |
| G-10 AUTH | MFA privileged, scoped staff/device roles, revocation, privacy, 403 negative tests, audit | cross tenant leak or demo headers -> BLOCK |
| G-11 speed/load | 10 tills real-latency peak: orders, KDS, menu publish, SSE, events, DB pool; 1/10/100 fake connections | measured p95 targets/limits documented; fail if critical queue unbounded |
| G-12 installation | signed installer, upgrade/rollback, recovery if update interrupted, config version ACK, secrets not in executable | canary must be reversible |
| G-13 operations | incident response runbook, device lost, offline manual workflow, on-call owner, support/specific provider contacts | unmanned live outage not accepted |
| G-14 shadow-pilot | 14-day, 1 outlet parallel accounting reconciliation and actual device tests; no unexplained money/stock differences | begin multi-outlet expansion only after signoff |

**No gate should be marked PASS because GitHub issue is closed or CI is green alone.** HIL/restore/reconciliation/actual funding must be independently evidenced.

## 5. Kaosetestide register

1. **Kassa toide** katkestub pärast tehingu commit'i, enne UI vastust → sama receipt ID ja one sale.
2. **Internet katkeb** 4 h, LAN jääb tööle: cash sale, local KDS/printer as supported, sync pending; card restricted.
3. **WAN+LAN katkeb**: distinct capabilities; no false „KDS online” ja tellimuse valmistamine ei dubleeru.
4. **Disk full** kohalikus kassas → ei kinnita „Müük OK” kui püsiv commit ei õnnestu.
5. **Saatmine 3x** / sama event, teine payload → üks mõju või 409, konflikt karantiini.
6. **Outbox worker hang** 30 min → event queue age alarm, sales not lost; after restart exactly one effect.
7. **Poison event** blokeerib raha tarbija, admin sees reason, replay after repair same event.
8. **Päris terminal** timeout enne/peale provider ACK → UNKNOWN ja reconcile, no duplicate charge.
9. **Printer kaob** pärast enqueue'd, enne ACK → visible pending; copy vs original print semantics.
10. **Midnight/DST** Europe/Tallinn + asukohapõhine cutoff → correct VAT/business_date, no wrong day revenue.
11. **Kaks ettevõtet** jagavad füüsilist printerit, mitte andmebaasi → no cross-company receipt/info.
12. **PITR restore** enne lõunat → edge journal replay unique, accounting/export dedup, device config not stale.
13. **10 müügikohta korraga** 2h test, 10+ concurrent terminal/KDS interactions → no lost ledger, queue bounded.
14. **Menüü publish** keset müüki → one atomic menu version and sale snapshot, per-device ACK.
15. **Kaotatud/seadmelt varastatud PIN või device key** → revoke, audit, theft runbook with device recovery.
16. **Kaks kassapidajat sama arve juures** → version CAS and no double kitchen or charge.
17. **Retsept/tooraine mitu ladu** → one stock movement per ingredient in correct warehouse.
18. **Seifi väljavõte vs vahetuse sulgemine** → 100+600−400=300; safe drop and close serialize.

## 6. Milliseid seadistusotsuseid ei tohi „100% töökindluse” nimel suvaliselt lukustada?

- PostgreSQL max_connections/pool-size/WAL archive interval: määrata koormustesti ja piloodi hosti järgi; algsed numbrid on hüpoteesid.
- Sünkroonne replica vs üks primary: erinev raha ja keerukuse kompromiss. Üks primary + managed PITR on piloodi aluseks; HA ja failover test enne 10-le kohale live laiendamist.
- SQLCipher vs OS disk encryption: sõltub kassaseadme OS-ist, key vault'ist ja sertifitseeritud kaitsemudelist. Kumbki pole efektiivne ilma võtmehalduse, taastamise ja varastatud seadme revoketa.
- NATS/Redis: mitte lisada enne kui outbox DB poll, SSE ja fan-out koormus näitab konkreetset kitsaskohta.
- Edge gateway: ei saa eeldada, et ühe seadme SQLite haldab teise kassa avatud arvet WAN-rikke ajal. Pilot-only cash sale võib jätkuda eraldi; mitme kassa offline ühiskasutus vajab uut tugevat autoriteeti.

## 7. Päris tehnoloogia allikad

- PostgreSQL PITR: https://www.postgresql.org/docs/18/continuous-archiving.html
- SQLite WAL durability: https://www.sqlite.org/wal.html
- NATS JetStream: https://docs.nats.io/concepts/jetstream
- OpenTelemetry distributed traces: https://opentelemetry.io/docs/concepts/signals/traces/
- AWS RPO/RTO: https://docs.aws.amazon.com/wellarchitected/latest/framework/rel13-bp01.html
- OWASP API auth scopes: https://owasp.org/projects/api-security-project

**Kasutus:** Claude peab lisama gate'i juurde automaattesti/skripti/hardware report'i, testversiooni ja kuupäeva. Iga puuduv gate → planeeritav väike DRAFT PR, mitte loominguline „valmis”. Main merge, prod deploy, live charges ja paid vendor contracts jäävad omaniku kinnituse taha.
