# ADR-0015 — AXAR ONE töökindel andmetuum ja sündmuste närvivõrk (NERVE)

**Staatus:** ETTEPANEK / research draft, 2026-11-26. **Kooskõlas:** ADR-0002 (modulaarne monoliit + PostgreSQL), ADR-0004 (outbox/inbox at-least-once), #42 NERVE, #60 tervikaudit, #34 turve, #29 offline, #27 hierarhia, #69 laoallikas, #68 kassasahtel, #62 server. **Muudatus:** arhitektuuri täpsustus, MITTE automaatne olemasoleva koodi ümberkirjutamine. Ektaco avalikud juhendid on äriprotsesside võrdlus, mitte kolmanda osapoole sisekoodi alus.

## Otsus lühidalt

**AXAR ONE baas on üks PostgreSQL-i tehinguline autoriteet, modulaarne FastAPI monoliit koos töötajaprotsessidega, iga kassa kohalik püsiv SQLite-päevik, versioonitud sündmused transactional outbox/inbox kaudu ja eraldatud füüsiliste seadmete adapterid.** Tõenduseta Kafka, NATS, Redis, Kubernetes ja mikroteenuste lisamine on **edasi lükatud**; vajadusel lisada vastavalt mõõdetud piirangule, ilma finants-/laodomeeni tõeallikat muutmata.

### Kaardistus: kes on mis?

\`\`\`mermaid
flowchart TB
    subgraph Z["Loomaaed: lokaalne töö ka internetikatkestusel"]
      POS1["Kassa K1: UI + edge API + lokaalne SQLite/journal"]
      POS2["Kassa K2...K10: igaühel oma püsiv päevik"]
      LAN["Kohalik võrgu/print bridge: KDS ja tšekiprinter"]
      POS1 --> LAN
      POS2 --> LAN
    end
    subgraph C["Keskne AXAR: üks autoriteet"]
      GATE["API/Auth: tenant + legaalüksus + objekt + müügipunkt"]
      DB[("PostgreSQL / ACID<br>müük, raha, ladu, retsept, dokumendid")]
      OUT[("Transactional outbox/inbox<br>järjekorrad + karantiin")]
      WORK["Taaskäivitatavad worker'id<br>stock/BOOKS/KDS/menu"]
      PROJ["Arvestust mitte muutvad projektsioonid<br>juht, TV, AI, statistika"]
      GATE --> DB
      DB --> OUT --> WORK --> PROJ
    end
    POS1 <-->|"HTTPS + idempotentne sync / ACK"| GATE
    POS2 <-->|"HTTPS + idempotentne sync / ACK"| GATE
    WORK --> AD["Ametlikud adapterid<br>terminal, RIK/Merit, Wolt/Bolt, printer"]
    DB --> BK["Krüptitud WAL/PITR varundus + testitud taastamine"]
    PROJ -.-> POS1
    PROJ -.-> POS2
\`\`\`

Märkus: **makseterminal ei kuulu pilve maksevisuaali** — kaarditehing peab kasutama sertifitseeritud pakkuja adapterit ning kinnitatud tulemust; internetikatkestuse ajal ei mõelda maksetulemust välja. Kassa kohalikuks müügiks vajalik püsiv päevik ei tohi asuda võrgukettal. Kui ühes objektis on vaja offline-režiimis laua jagatud arveid, KDS-i või ladu koordineerida kassade vahel, kavandada eraldi kohalik *site authority* ja selle tõrkeolek; ära nimeta kassade iseseisvaid SQLite-faile üheks jagatud autoriteediks.

## Võrdlus: mida valida ja mida alguses mitte teha?

| Valik | Otsus praegu | Miks / millal uuesti kaaluda |
| --- | --- | --- |
| PostgreSQL kui keskne ACID DB | JAH | Tugevad rea-/tehingulukud, RLS, üks tõeallikas. Raha ja ladu peavad sisaldama atomaarseid kandeid. |
| Lokaalne SQLite igas kassas | JAH, after hardening | Ühe seadme üks kirjutaja; kohalik SSD; kontrollitud WAL + synchronous FULL + storage encryption; power-cut test. |
| Modulaarne monoliit, eraldi workers | JAH | Vähem võrguvõimalusi pooleldi tehtud finantstehinguteks. Moodulite vahel ainult oma API/command ja sündmused. |
| PostgreSQL outbox/inbox | JAH, olemas | Koodis juba kontrollitud; commit atomic, retries, dead letters, fencing. Ärge eemaldage. |
| NATS JetStream | **TINGIMUSLIK** | Lisada ainult siis, kui mõõdetud DB poll/SSE fan-out, eri asukohtade reaalajas liiklus või rohkem väliseid tarbijaid nõuab. Kasuta bridge'i olemasolevast outbox'ist, mitte uut tõeallikat. |
| Kafka / Debezium | **MITTE ALGUSES** | Liigne ops-koormus 10 müügikohale; vajadus võib tekkida suurel andmevoogude/CDC ökosüsteemil. Debezium Outbox Router on tehniline valik hiljem. |
| Redis | **AINULT VAHEMÄLU** | Mitte ainus kehtiv raha-, menüü- ega seadmejääk; restart peab taastuma PostgreSQL-ist. |
| Temporal | **VÕIMALIK HILJEM** | Pikkade väliste protsesside (leping, hanked, e-arve, e-kiri) jaoks, kui oma workflow/queue muutub raskesti hooldatavaks. |
| Kubernetes / multi-region aktiiv-aktiiv | **EI** piloodiks | Suurem kompleksus, raskem DB consistency; esmalt managed PG, üks API ja eraldi worker, restore/drill. |
| pgvector + AI | **ERALDI READ/RECOMMEND** | AI ei tohi juhtida makse, ladu ega kehtivat hinnakirja otse; ainult ettepanek koos kinnitatava command'iga. |

## Ärireeglite autoriteedid (üks kirjutaja ja tõe allikas)

- \`catalog_product\` identiteet; \`recipe_version\` kehtivuses ja valmistamise režiimis; \`menu_version\` + per-register ACK (kassa ei arvuta hinda iseseisvalt serveri asemel).
- \`sale\` ja \`sale_line_snapshot\`: ühekordne immutable müük, koos mängu ajal kehtinud hinna/KM/retsepti/tarnija/müügikoha versiooniga; hilisem hinnamuutus ei muuda eelmist arvet.
- \`payment_intent\` → \`provider_attempt\` → \`tender\` → \`settlement\`: provider \`UNKNOWN\` ei ole \`DECLINED\` ega \`SUCCESS\`; raha ei küsi automaatselt kaks korda.
- \`stock_movement\`: lao allikas iga tegeliku kulurea juures, sealhulgas toote/retsepti koostisosapõhine ladu. Ühe kassa vaikimisi lao kasutamine kõigi ridade puhul on kontrollitud lünk (#69).
- \`drawer_cash_movement\`: kassasahtli mittemüügilised IN/OUT, seif, põhjus, idempotency; kontrollitud lünk main-is ja mustand #71 (#68).
- \`document_ledger\`: ostu, inventuuri, paranduse, kreediti, tagastuse ja pädevuse heakskiidu olekud. POSTED kannet ei muudeta tagantjärele.
- \`accounting_export\`: teadmata, ootel, pakkuja kinnitatud, saajale toimetatud eri staatused; võimalus reaalse teenuseosutaja alusel sobitada.
- \`identity\`: tenant != legal entity != location != outlet != register != physical warehouse; iga objekti eraldi õigused ja kindel source owner (#27).

## NERVE sündmuse leping

\`\`\`json
{
  "event_id": "uuid",
  "event_type": "pos.sale.completed",
  "event_version": 2,
  "tenant_id": "tenant-id",
  "legal_entity_id": "planned-not-yet-main",
  "aggregate_type": "sale",
  "aggregate_id": "stable-id",
  "aggregate_version": 1,
  "recorded_seq": 123,
  "correlation_id": "request-trace",
  "causation_id": "command-id",
  "occurred_at": "ISO8601 UTC",
  "payload_hash": "sha256",
  "payload": {"sale_id": "stable-id"}
}
\`\`\`

See on TARGET-kujund, **mitte väide, et kõik väljad on main-skeemis**: näiteks \`legal_entity_id\` vajab #27 migratsiooni. Olemasolev \`events.emit\` kirjutab sündmuse sama tehingu sees ning järjenumbri per tenant; versioon + actor + correlation + aggregate väljad on koodis olemas. Tarbijal on inbox-idempotentsus, lease/fencing ning nähtav poison/dead-letter. Tulevikus võib väliseks brokeriks lisada JetStream'i, aga sündmuse tulemus on äritehingus *at-least-once delivery, exactly-once business effect via unique constraints*, mitte universaalne „exactly once network“.

**Ära tee cross-domain SQL update'i otse**, kui mõne teise mooduli omaniku funktsiooni või versioonitud event'i kaudu on sama võimalik. Sündmuse tarbija tohib muuta ainult talle kuuluvaid tabelid/registreid. DB-s väikesed ACID-ärireeglid on lubatud; välised liidesed lähevad asünkroonsesse delivery-outbox'i.

## Konkreetne teekond

1. Teenindaja vajutab tootenupule. UI reageerib lokaalselt; toote, hinna, retsepti ja menüü versioon on allkirjastatud/cached; avatud korv ei ole veel finantstehing.
2. Kassa salvestab volitatud müügikäsu ning edge SQLite päeviku **enne** „müük edukas“ kuvamist (serveris juba ACKetud müügi korral allikas on keskne PG). Kõik numbri- ja makseväljad base units / integer cents.
3. Kui keskus on saadaval, backend atomaarne kassamüügi snapshot + outbox, vastuses sale-ID ja kviitungi number; kaotatud HTTP vastusele tuleb sama \`Idempotency-Key\` ja payload-hash, mitte teine müük.
4. Stock worker tarbib \`pos.sale.completed\` ja teeb õige lao ning retseptiversiooni alusel ühe kande; ajaloolise müügi snapshot säilib.
5. Books export queue teeb mapping'u ja ootab **vastuvõtja ACK**; S3 eksport või HTTP 200 ilma kinnituseta ei ole automaatselt raamatupidamisse kantud.
6. Kitchen KDS/printer teeb **delivery-job** koos seadme ACK-ga ja käsitleb üht ticket-ID-d; kui printeri tulemus teadmata, inimkontroll/KOOPIA märgistusega vastavalt seadme protokollile.
7. TV/QDS/AI saab read-only projektsiooni ja ei muuda ärilist algregistrit.
8. Offline sünkroniseerib monotonse seq + hash-chain; conflict läheb quarantinesse, ei kao. 72 tunni limiit on poliitika, mitte iseenesest tõestatud õiguslik või tehniline SLA.

## Kõige olulisemad tõrkerežiimid ja käitumine

| Tõrge | Ohutu käitumine |
| --- | --- |
| Keskserver offline | Kassa jätkab ainult volitatud kohalikke müüke; näitab offline vanust ja sync järjekorda. Päris kaart ainult provider'i sertifitseeritud režiimis. |
| Kassa elekter katkeb commit'i ajal | SQLite rollback / atomic commit; pärast restart'i verify journal + contiguous seq; kuvatakse ainult kindlalt taastatud müük. |
| Vastus kaob pärast commit'i | Sama võtmega replay, serveri unikaalsusconstraint; uut makset ei alustata teadmata staatusega. |
| Köögiprinter offline | Printeri tellimus püsib järjekorras; UI näitab mitteedastatust. Köök ei tohiks kaotada valmistamise ülesannet. |
| Worker lag / poison | Sõnum jääb blokeeritud queue'sse; alarms koos juurpõhjuse ja owner'iga, mitte vaikselt kadumine. |
| Laos ei jagu / vale laoseos | Ära võta vaikselt teisest laost; näita BLOCK/REVIEW ja kompensatsioonireeglit. |
| Kaks töötajat jagavad arvet | Serveri CAS version/lock; konflikt näidatakse, mitte ei kirjutata üle. |
| Tühistus pärast valmistamist | Finantsrefund/kreedit ja füüsiline stock disposition eraldi; valmistatud toit ei taastu tooraineks. |
| Seade varastatakse | Võtmed revoke, ligipääs suletud, alles olev journal kriitilise kompromissi osas review; ei kirjuta minevikku ümber. |
| Server taastatakse vanemast PITR punktist | Võrdle edge seq/receipt/inbox kättesaadavate tehingute ja taastatud registriga; reconcile + kontrollitud replay, vältida topeltmüüki/numbrite konflikti. |

## Mõõtmine enne lõplikke taristuprognoose

Mõõdikud ja *sihttasemed, mitte juba täidetud SLA*:
- p95 kassatoote nupu tagasiside <100ms *lokaalsel seadmel*; sale journal p95 <250ms on toetatud SSD-l.
- p95 KDS kohaliku võrgu dispatch <500ms; connected TV publish ACK p95 <2s, kui võrk toimib.
- 10 POS-i, 10 tunniga võimalikult realistlik müügikoormus, 72h offline-poliitika kontroll, vähemalt 24h fault-injection soaks; kokkulepitud numbrid alles pärast eeltööd.
- Kriitilised nulltolerantsused: duplicate sale/payment/stock, kadunud confirmed cash event, cross-tenant leak, arve varjatud muutmine.
- RPO keskne piloodieesmärk <=5min ja RTO <=60min *ainult siis*, kui varunduse/PITR infrastruktuur on käivitatud ja taastamisproov ajaliselt tõestatud; lokaalselt ACKetud müügitehingu kadumine pole aktsepteeritav (toetamata kettal pole 0-loss garantiid).
- Monitor: queue depth, oldest unacked age, device seq gap, dead letters, reconciliation discrepancies, provider unknown, register health, restore drill age, p95/p99 latencies, DB WAL archive lag, edge disk free, clock skew, KDS/terminal ACK.

## Viited: ametlik dokumentatsioon, mitte konkurendi lähtekood

- PostgreSQL PITR/WAL: https://www.postgresql.org/docs/18/continuous-archiving.html
- PostgreSQL RLS: https://www.postgresql.org/docs/18/ddl-rowsecurity.html
- SQLite WAL: https://www.sqlite.org/wal.html
- SQLite suitable uses: https://www.sqlite.org/whentouse.html
- NATS JetStream durable delivery: https://docs.nats.io/concepts/jetstream
- Debezium Outbox SMT (hilisem CDC): https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html
- OpenTelemetry signals: https://opentelemetry.io/docs/concepts/signals/
- OWASP API security: https://owasp.org/projects/api-security-project
- RPO/RTO: https://docs.aws.amazon.com/wellarchitected/latest/framework/rel-availability-and-recovery-objectives.html

**Inseneri kohustus:** võrrelda seda ADR-iga 0002/0004 ja uute PR-idega #63–#71, märgistada iga serv DONE/PARTIAL/MISSING, ehitada väikeste ohutute PR-idega; CI + E2E + HIL + restore ning omaniku eraldi heakskiit enne main/production/live payments/purchases.
