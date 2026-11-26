# AXAR ONE — Ektaco CC5 süvauuring IV: kassasahtli tehinguvälised liikumised, andmete taastamine, variatsioonid, erisoovid, terminal, õigused ja käivitusvalmidus

**Lisatud:** 2026-11-26. **Staatus:** täiendav allikapõhine analüüs ja programmeeritavad vastuvõtukriteeriumid, mitte valmis AXARi kood.
**Seotud:** #60 (master-integration), PR #63 (evidence inventory), #61 (product CRUD), #14 (inventory), #40 (menu), #25 (terminal/printing), #29 (offline), #34 (security), #48 (refunds), #32 (control).
**Tõenduslegend:** EK = Ektaco avalik CC5/CompuCash juhend ja kontrollitud pildid; CODE = AXAR main-haru lähtekood avatud ja kontrollitud; AX = AXARi jaoks soovitatav projekteerimisreegel; TEST = mõõdetav E2E või API test.
**Versioonipiir:** CompuCash CC5 kassa, CC4000 ja CC Retail on eri tooted/versioonid. Näiteks Bolti juhendi all toodud kassategevused on selgelt pealkirjastatud CC4000; CC Retail tagastusjuhend ei tõenda CC5 kassas identset UI-d. Ühegi suletud süsteemi sisearhitektuuri kohta ei tehta väiteid.

## F01 — Sularaha sisse/välja väljaspool müüki: kodeeritud puudujääk

EK: CC5 kassa Tegevused → Vahetus näitab eraldi „Sularaha sisse“ ja „Sularaha välja“, mis liigutavad raha sahtlisse või välja ilma müügita. See on päris CC5, mitte CC4000 dokument.
Pilditõend: https://ektaco.ee/wp-content/uploads/2021/02/260202-16-44-26-dnplayer-860x460.png
Juhend: https://ektaco.ee/helpcenter/kassa/cc5-kassa/kassaga-seotud-uldised-tegevused-cc5/

CODE: products/axar-pos/axar_pos/services.py, funktsioon _expected_cash_expr() tagastab avamise algsularaha + cash_sales_cents - cash_refunds_cents. open_drawer ja close_drawer eksisteerivad. Puudub eraldi summat muutvate sularaha sissemaksete/väljavõtmiste arvestus selles arvutuses. Selle väite ulatus on main-i kontrollitud services.py ja sellele vastavad drawer mudelid; mitte kogu hoidla puudumise universaalne tõend.
AX: DrawerCashMovement (id, tenant, drawer_session, register, direction IN|OUT, amount, currency, reason, actor, approver, timestamp, cash_custody_receiver, idempotency_key) append-only. Võimalikud põhjused: algraha täiendamine, seifi väljavõte, pangakullerile üleandmine, raha tagastamine seifist, väike kassakulu (vajab raamatupidaja reeglit). Põhjused ja õigused konfiguratsioonina, suured summad astmelise kinnitusega. Ära lisa summasid müügitulusse. Daily expected cash = opening float + cash sale tenders - cash refunds + non-sale cash IN - non-sale cash OUT. Iga liikumine kuulub ühele sahtlile ja kassale; seifi liikumine võib olla eraldi custody transfer koos teise poole ACK-iga.
TEST: opening 100€, cash sales 600€, safe drop 400€, no refunds → drawer expected 300€. Duplicate safe drop retry does not subtract 400€ twice. Closing concurrently with safe drop either commits both in deterministic order or one is rejected. Every amount enters control audit and reconciliation rule; sales revenue remains 600€.

## F02 — CC5 seadme taastamine: „Lähtesta arved“ on kriitiline tegevus

EK: CC5 Seaded → Andmed eristab Värskenda andmed, Lähtesta arved (tühistab pooleliolevad arved; kasutada ainult kasutajatoega kooskõlastatult) ning Andmete alglaadimine.
Pilditõend: https://ektaco.ee/wp-content/uploads/2021/02/260202-16-57-48-dnplayer-860x464.png
Juhend: https://ektaco.ee/helpcenter/kassa/cc5-kassa/kassaga-seotud-uldised-tegevused-cc5/

AX: erista ConfigurationRefresh, CatalogResync, RecoveryRebuild ja EmergencyOpenOrderResolution. Ükski „värskenda“ või „taasta“ nupp ei tohi kustutada device journal'i, outbox'i, maksetulemuse tõendeid, kassasahtli olekut või avatud tellimusi.
- RecoveryGuard peavad nägema unresolved_payment, unsynced_sales/refunds, open_bills, unacknowledged_kitchen, local last_backup, receipt counter, config version.
- Preflight ja kõrge riskiga operatsiooni kaks kinnitust; read-only export/snapshot enne seanssi; taastamine idempotentne ja sammude kaupa jätkatav; katastroofi taastamise harjutus (checksum + Restore Report).
- OpenOrderReset = iga avatud arve kohta eraldi kompenseeriv sündmus koos originaalse külastaja tellimuse, köögitöö, makse ja lao dispositioniga. „Tühista kõik“ tohib esineda vaid selge auditiga kontrollitud hädaolukorra toiminguna, mitte hooldusnupuna.
TEST: offline tills with 3 unsynced sales + 1 partial card unknown + 2 pending kitchen tickets; perform refresh/recovery. All original transaction IDs and hash chain survive; zero duplicated funds and stock. Emergency reset cannot run without authorized confirmation and conflict resolution.

## F03 — Kolm maksetõde: kassa, terminal, pank

EK: CC5 kassa Makseterminal alammenüüs Tühista, Korduskviitung, Saada tehingud, Eelnevad tehingud, Kontrolli ühendust. Liidestus peab olema aktiveeritud. Arvejuhendis: kui terminal ei ole integreeritud, registreeritakse kassas kaardimakse **alles pärast** käsitsi tehtud ja kontrollitud terminalitehingut. Terminali tühistuse asemel soovitatakse arve tühistamisel pigem tagastust. CC5 eristab ka „Vabamaksed“ (näiteks Wolt/Bolt/GSM makse) ja pangaülekande kliendi.
Allikad:
https://ektaco.ee/helpcenter/kassa/cc5-kassa/kassaga-seotud-uldised-tegevused-cc5/
https://ektaco.ee/helpcenter/kassa/cc5-kassa/muuk-ja-arvega-seotud-tegevused-cc5/

AX: TerminalPaymentAttempt (requested, pending, approved, declined, unknown, reversed, refunded), ProviderBatch (open, submitted, accepted, settled, disputed), CashierRecordedTender ja BankSettlement on eraldi; provider batch/send käitumine peab vastama valitud pakkuja tegelikule API-le, mitte Ektaco kasutajaliidesele.
- Manual external terminal tender on UNCERTIFIED until matched against provider statement (or trustworthy evidence). Do not show as confirmed captured solely because cashier clicked „card“.
- Wolt/Bolt custom pay represents marketplace collected funds and receivable from the platform, not local card payment or cash.
- Merchant duplicate print generates COPY, never a second charge.
- Terminal failure modes: transport down, declined, unknown timeout, late provider approval, reconciliation after midnight, batch settlement partial, reversal not permitted after cut-off.
TEST: 100€ approved card shown in POS but settlement 97.30€ after 2.70€ fee; total sales stay 100€, provider expense 2.70€, bank cash 97.30€. Late chargeback changes receivable/liability with separate document, original sale unchanged.

## F04 — ProductVariant ≠ Modifier ≠ SpecialInstruction

EK: Ektaco Toote variatsioonid: omadused (nt suurus ja värv) määratakse enne; varieeruv peatoode loob väärtuskombinatsioonide alusel eraldi toodeteks seotud variatsioonid, alghind/number tulevad peatoote mudelist; variatsioonid saab eraldi eemaldada, märkida puuduvaks või lähtestada vaikeseadetele.
EK: Lisandid (erisoovid): seotud tootel võib automaatselt avaneda mitme lisandi menüü, tasulise/tasuta lisandi hinnaklass, min/max valik, müügipunkti piirang, muudatava järjestuse; erisoovid on eraldi eeldefineeritud või lubatud vaba tekstiga köögijuhised.
**Ohtlik erand:** Ektaco juhendis öeldakse selgelt, et lisandi/erisoovi grupp, millel pole ühtegi tooteseost, kehtib **kõigile** toodetele. AXAR ei tohi seda vaikimisi korrata, kui rida näeb välja nagu „seostamata“: nõua explicit applies_to_all=true ja kontrollitud mõju eelvaadet.
Allikad:
https://ektaco.ee/helpcenter/compucash5/tootehaldus/toote-variatsioonid/
https://ektaco.ee/helpcenter/compucash5/tootehaldus/lisandid-erisoovid/

AX:
- VariantSKU = eraldi identity, barcode, stock item, tax/price, optional parent ProductFamily. Variant combinations must enforce unique keys, not overwrite existing child prices on parent reset without explicit approved diff.
- ModifierGroup = optional/required choice attached to offer. Each choice has price_delta, ingredient_delta, substitution logic and inventory source; minimum/maximum cardinality checked at server too. Example „pitsa ilma juustuta“ may need negative ingredient substitution; „topelt juust“ adds ingredient and price.
- SpecialInstruction = free text or controlled value for kitchen (rare/medium/well done); never automatically charge or deduct stock if not configured as a measured ingredient change. Disallowed free text must be blocked (sanitized, limited length), never interpreted as authoritative allergen guarantee. Kitchen changes require reprepare/reprice compensation when already sent.
- Realtime menu publication must take effect per configured outlet only, with acknowledgment and audited user identity.
TEST: 3 sizes × 2 types yields 6 unique variants without identifier collisions; existing child custom price survives parent change unless expressly re-approved. Empty modifier membership does NOT publish to all products without an explicit flag. „Without cheese“ changes recipe use once, never stock two times. Unauthorized cashier cannot change modifier costs.

## F05 — Õiguste grupid on kolmetasemelised ning neid ei kustutata kasutajate alt

EK: CC5 õiguste grupil on kassapool ja kontoripool eraldi; kontoris on BLOCKED, READ-ONLY ja ALLOWED. Grupi saab luua olemasolevast mallist. Sidestatud kasutajaga gruppi ei saa kustutada enne, kui seosed on eemaldatud.
Pildi/teksti allikas: https://ektaco.ee/helpcenter/compucash5/personal/oiguste-grupid/

AX: Policy model Subject(role, company/site/outlet/warehouse) × Resource × Capability(view, edit, approve, publish, reverse, export) + step-up approval. RBAC with least privilege; server check every action. Role cloning must snapshot policy version to avoid surprising drift; group deactivation and assignments should not silently disable staff mid-shift without safe process. Group delete BLOCK if assigned, but permit archival with historical permission audit. Read-only must redact secrets/PII beyond permission; viewing a report is not automatically viewing all employees.
TEST: cloned manager template can view stock but not post transfer; group delete while 8 members assigned rejects with exact impact; changes to policy invalidate sessions after propagation; stale offline token permission cannot escalate by hidden UI call.

## F06 — CC5 seisukohalt on toote/menüü/seadme käivitamisel sõltuvusjärjekord

EK: 6 sammu alustamiseks näeb ette tooterühmad → müügitooted ja komponendid/retseptid → sisseost ja omahind → hinnakirjad → kassanupud → õiguste grupid ja kasutajad. Lisaks tuleb seadistada seadmete võimekus ja füüsiline ühendus.
Allikas: https://ektaco.ee/helpcenter/compucash5/uldine-compucash-5/6-sammu-alustamiseks-cc5/

AX: NewOutletReadiness wizard does not demand 6 manual IT tasks from owner; it derives dependencies and proposes or automates safe work. Missing prerequisites BLOCK activation:
- Entity tax mapping, outlet and warehouse rules, products/ingredients, approved recipe, tax-inclusive selling prices;
- payment/till method permissions and terminal device binding, receipt series, printer/KDS routing;
- data scope / staff role and device credential;
- offline signed menu and on-device save/restore test.
Show a checklist for each outlet with traffic light statuses, proof link and exact missing setup; clone from approved outlet template while preserving unique device IDs and sequences. Self-service export/print onboarding summary.
TEST: create 10 zoo outlets, inherit shared menu but each has distinct stock source, device and some unique prices; one outlet deliberately has no kitchen routing → cannot pass readiness gate; never accidentally copy permissions from company A to B.

## F07 — Viimase minuti ohukohad: meilikviitung ja tehingute parandamine

EK: CC5 saab lõpetatud arve saata kliendi meiliaadressile; kontoris seatakse SMTP server/port ja sisselogimine ning saab testida ühendust.
EK: CC Retail tehingute vaates saab lõpetatud arve järgi otsida, tagastada üksikridu ja koguseid, põhjendust nõuda, luua kreeditarve, tühistada müügid ning korrigeerida valesti märgitud makseviisi. See on **CC Retail**, mitte tõend, et CC5 töötab identse tagastusvaatega.
Allikad:
https://ektaco.ee/helpcenter/compucash5/seaded/e-maili-seadistamine/
https://ektaco.ee/helpcenter/kassa/cc5-kassa/muuk-ja-arvega-seotud-tegevused-cc5/
https://ektaco.ee/helpcenter/kassa/cc-retail-kassa/tehingud-cc-retail/

AX: ReceiptDeliveryJob (immutable sale snapshot, artifact hash, recipient, delivery status, operator, consent/purpose, retry/backoff, privacy retention). SMTP/API secrets in external secrets store, NEVER in repository or logs; test message goes to sandbox and public response masks recipient details. Sending again must send a COPY of the same receipt, not create new sale or tax document. Per-line return needs a required reason + tax/legal credit doc and physical stock disposition already in #48; correcting recorded tender must NOT trigger second real provider charge.
TEST: email timeout causes pending delivery retry while payment/sale remain final; user asks for 2 copies to same email, track delivery jobs but one invoice. Invalid recipient email never deletes sale.

## F08 — Bolti juhendi sees on Wolti nimetus: dokumentatsiooni kontrolli piir

EK: Ektaco Bolt liidese tekstis kirjeldatakse Bolt SKU ↔ CompuCash tootenumber seost, ent CC4000 kassas protsessi järgmistes sammudes (valmistamisaja ja valmisoleku teavitus) kasutatakse osaliselt nimetust „Wolt“.
Allikas: https://ektaco.ee/helpcenter/compucash5/liidesed/bolt/

AX: Dokumentatsiooni kopeerimisviga või terminoloogiline ebatäpsus pole tõend toimiva või mittetoimiva liidese kohta. Arendaja ei tohiks kanalite webhook/status/API lepinguid tuletada tekstijuhendi suvalisest lausest. Provider contract: channel unique, external order ID and event IDs unique, webhook signature validation, status mapping per provider, parent receipt and kitchen ticket immutable, reconciliation with channel settlement. Live sandbox tests and provider docs are separate gate.
TEST: Bolt webhook can't cause Wolt status update even if their external_order_id equals; intentionally mixed channel IDs → quarantine.

## F09 — Ektaco ekraanipiltide järgi tehtud täpsed vaatlused

1. CC5 vahetuse menüü (https://ektaco.ee/wp-content/uploads/2021/02/260202-16-44-26-dnplayer-860x460.png): „Sularaha sisse“, „Sularaha välja“, „Lõpeta teenindaja vahetus“, „Lõpeta kassa vahetus“ on eraldi, suured tegevusnupud.
2. CC5 andmesätete vaade (https://ektaco.ee/wp-content/uploads/2021/02/260202-16-57-48-dnplayer-860x464.png): kolm eraldi nuppu „Värskenda andmeid“, „Lähtesta arved“, „Andmete alglaadimine“ ning viimati värskendamise kellaaeg. Nupu juures on kasutajatoega kooskõlastamise hoiatus; seega taastamisoperatsioonid on eri riskiga.
3. CC Retail valikuline toote tagastus (https://ektaco.ee/wp-content/uploads/2025/07/CCRetail_loo_tagastus1.png): korraga on näha algse arve number, iga rea valik, koguse + nupp ja põhjenduse kommentaar. Seda pilti ei tohi nimetada CC5 kuvatõmmiseks.
4. Õiguste gruppide UI (https://ektaco.ee/wp-content/uploads/2021/02/chrome-couhz9xsyy.png): pildil on kontori/kassa eraldavad sakid; lugemisõiguse kolm olekut on kinnitatud sama lehe tekstis, mitte selle tillukese pildi põhjal.
5. Tootevariantide juhendi animatsioon on GIF, mida veebipildi vaates ei saanud visuaalselt tõendada; selle sisu kohta kasutame ainult juhendi otsesõnalist teksti.

## F10 — Uued #60 integratsiooniservad ja prioriteedid

| ID | Source → target | Invariant / fail-safe | P / seotud |
| --- | --- | --- | --- |
| E27 | DrawerCashMovement → cash expected/closing/reconcile | 0 mitte-müügi raha loetud müügikäibeks; koos müükidega üks sahtlisaldo | **P1** / #60,#32 |
| E28 | RecoveryRefresh → device journal + open bills + payments | 0 nähtamatult kaotatud pooleliolevat arvet või offline tehingut | **P0** / #29,#34 |
| E29 | Terminal payment → provider batch → bank settlement | captured != settled; charges/refunds and fees match provider | **P1** / #25,#4 |
| E30 | ProductFamily → VariantSKU → modifier/special instruction → recipe/price | no duplicate SKU, valid modifier cardinality, no default global leaking | **P1** / #61,#40,#14 |
| E31 | RoleTemplate → assignments → effective permissions | no delete of assigned policy; view != edit != approve | **P1** / #34,#33 |
| E32 | Outlet setup → verified prereq → active till | all business prerequisites validated before first sale | **P1** / #27,#61 |
| E33 | Sale snapshot → receipt rendering → delivery queue → email | send failure never cancels sale; no duplicate invoices | P2 / #25 |
| E34 | Channel webhook → exact platform status flow + settlement | no Wolt↔Bolt state confusion; idempotency per channel | P2 / #18,#40 |

## F11 — Cross-domain acceptance scenarios, with no fake hardware claims

- **Cash safe drop:** 100€ initial + 600€ cash sale - 400€ safe drop = 300€ in till; repeated safe drop request and parallel drawer close do not duplicate or lose cash movement.
- **Device crash + dangerous reset:** two open bills, one pending kitchen send, three offline sales, one unknown card payment; try recovery. All intact or explicitly quarantined with trace; none silently deleted.
- **Variant and modifiers:** pizza M/L × vegan/meat variants, extra cheese priced, no cheese consumes correct stock, custom "without nuts" note reaches kitchen but does not make unsupported allergy guarantee.
- **Role template:** cashier read-only stock cannot POST stocktake or edit price by direct HTTP; manager delete role with assigned staff denied.
- **Outlet activation:** 10 zoo outlets; only 9 pass readiness because one has no stock source mapping; broken one cannot sell stock-impacting product.
- **Terminal reconciliation:** approved card 100€, fee 2.70€, net 97.30€, later chargeback. No double sale and bank reconciliation shows all three lines separately.
- **Channel separation:** Bolt and Wolt contain same external order id, two separate genuine orders; duplicate Bolt webhook once only; wrong channel status update impossible.
- **Receipt copy:** original finalized receipt emailed twice, provider timeout/retry, immutable sale/tax unaffected.

**Delivery:** Claude should compare E27–E34 against #60 / PR #63 actual code and add only genuine gaps as small, tested draft PRs. This research branch carries documentation only and must not be merged or deployed without owner's approval.