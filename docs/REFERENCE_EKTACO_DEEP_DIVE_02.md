# AXAR ONE — CompuCash 5 süvauuring II: kassaarved, ettemaksud, maksekohustused, kanalid, QDS, audit ja tõrked

**Kuupäev:** 2026-11-26. **Staatus:** avalike juhendite ja ekraanipiltide põhjal koostatud võrdlusuuring; AXARi nõuded on soovitused, mitte koodi valmimise väited.
**Seotud:** uuringu põhidokument samas PR-is #67, süsteemiaudit PR #63 ja issue #60; #61, #14, #27, #40, #48, #25, #32 ja #33.
**Tõendamisreegel:** [EK] = Ektaco avalikust juhendist või nähtavast kuvatõmmisest tõendatud; [AX] = projekteerimisettepanek; [TEST] = testimise nõue. Ektaco suletud süsteemi sisemist koodi/andmebaasi me ei näe. Avalikud kasutusjuhendid ei tõenda päris riistvara jõudlust ega õiguslikku vastavust.

## A. Uued põhjapanevad leiud (mis esimesest uuringust välja jäid)

| ID | Ektaco käitumine / täpne juhend | AXARi domeenipiir, mida tuleb lisaks modelleerida | Testi oluline piirjuht |
| --- | --- | --- | --- |
| A01 | **Müügitellimus** võib tekkida enne kaupade üleandmist; nõutud ettemaksu %; makstud ettemaks teeb eraldi ettemaksuarve; hiljem tellimusest aktsepteeritud hulgiarvele kantakse tellimuse tooted ja ettemaks. [EK1] | SalesOrder + PrepaymentLiability/TaxDocument + FinalInvoice + Allocation. Tellimus ≠ tulu; ettemaks ≠ teine müük. | 40 € tellimus, 20 € ettemaks, hilisem lõpparve: kliendi jääk 20 €, finants- ja KM arvestus korrektne; sama kinnitust 3x = 1 arve |
| A02 | **Koondarve** seob ühe kliendi varasemad arved; aktsepteerimine lukustab koondi; koondarve tühistamine EI tühista seotud algarveid. [EK2] | ConsolidatedStatement references existing Invoices; no double revenue and no blanket cancel of children. | 3 originaalarvet, 1 koond; koondi tühistamine jätab kõik 3 originaali kehtima |
| A03 | **Arve kuuluvus**: enda arved, kolleegide omad või jagatud; teise teenindaja avamine võib arve omandi üle võtta. [EK3] | OwnerUser vs AccessibleUsers/SharedStatus vs LockHolder + optimistic version; transfer ownership audit. | Kaks teenindajat avavad sama arve korraga; puudub line overwrite ja topeltmakse |
| A04 | **Arverida** võib liikuda külastaja/käigu vahel, olla kahe külastaja vahel jagatud, saada erihinna ja märkuse; pärast arve jagamist read külmutatakse tavaseadistuses. [EK4] | Immutable posted bill, mutable open order lines with versioned edits; SplitBill and GuestAllocation are first-class entities, not destructive sale rewriting. | Pitsa jagatakse kaheks, maksja A tasub 1/2, maksja B ülejäänu, köök saab siiski ühe valmistamise |
| A05 | **Vahearve ja köögi saatmine**: vahearve trükk võib piirata hilisemat soodustust; arve taustale saatmine või maksmine võib automaatselt köögile sõnumi saata. [EK3] | Separate PREVIEW_RECEIPT from FISCAL_RECEIPT; kitchen send explicit idempotent action, never automatically double-send via both blur and payment. | Double click + lost ACK + tab close → sama ticket |
| A06 | **Makseviis võib olla „mittemaksev”**: ülekandega arve registreerimine ei võrdu laekumisega; makseviisidel on aruandlusgrupi tähis. [EK5] | TenderClass + SettlementState separated: CASH_SETTLED, CARD_PENDING/SETTLED, RECEIVABLE_OPEN etc; report grouping separate from payment provider identity. | Ülekanne on open receivable, mitte kassas laekunud sularaha |
| A07 | **Manuaalne kaarditerminal**: klient tasub eraldi terminalil ja alles õnnestumise järel registreeritakse kaardimakse kassas. [EK3] | EXTERNAL_UNVERIFIED payment method is not provable terminal capture; manager-approved reference and reconciliation; do not present as provider-verified. | Manuaalselt märgitud kaart, kuid panga failis puudub: alert, mitte roheline reconciled |
| A08 | **Kaks vahetuse mõõdet**: teenindaja vahetus ja kassa/seadme vahetus võivad eksisteerida paralleelselt; viimane summeerib mitme töötaja tegevused. [EK6] | DrawerSession(register, money pool) ≠ EmployeeShift(staff, worktime). Separate cash custody from employment hours. | Kaks teenindajat kasutavad üht sahtlit; ainult üks füüsiline cash variance |
| A09 | **QDS ei ole KDS**: QDS on kliendile nähtav valmisoleku järjekord kahe veeruga, tellimuse numbritega; müügikoht seob seadet; sama seade teises brauseris katkestab vana; valmis tellimused aeguvad (vaikimisi 15 min). [EK7] | KitchenTicket state → PickupProjection (public, privacy-minimised) → DisplayDeviceLease with reconnect/TTL/pagination. | Kahe ekraani sama seadme konflikt, pikk järjekord ja uuestiühendus; ainult tellimuse number, mitte kliendi PII |
| A10 | **WooCommerce**: üksik/massi-/täisavaldamine, eri väljade osaline sync; esmakordselt olemasoleva toote otsing SKU järgi; valitud ladude kombineeritud laoseis; inbound tellimus võib muutuda müügitellimuseks, kontoriarveks või kassas kinnitatavaks veebitellimuseks. [EK8] | ChannelCatalogMapping + per-field ownership + SyncJob/ACK + stock aggregation with reservation and safety; external-order import mode by channel. | Sama SKU erinevates kanalites; inbound webhook kordub; üks müük; kombineeritud varu ei dubleeru |
| A11 | **Telema e-arve**: tarnija- ja laokoodid seotakse; sobitamata tooterida punane ning dokumenti ei saa aktsepteerida; 240g pakk → 0,240 kg. [EK9] | SupplierSKU crosswalk + UnitConversion immutable and reviewed; unmatched line blocks POST. | 1 pk=0.240 kg, 100 pk=24 kg. Vale SKU = quarantined, mitte juhuslik laokanne |
| A12 | **Digitaalne kinkekaart** on sama nummerdatud kaart Walletis, jääk/kehtivus kuvatakse; müük ja lunastamine nõuab online saldokontrolli; pärast lunastust Wallet uueneb. [EK10] | StoredValueLedger / GiftCardLiability, Authorization/Redemption, WalletProjection. Ei käsitleta kinkekaardi laadimist tavalise tootetuluna. | Kaks kassat lunastavad viimast 5 € samaaegselt → ainult üks õnnestub; offline nõuab teenusepakkuja kinnitatud turvalist lahendust või blokeeritakse |
| A13 | **Kliendikaart**: Apple/Google Wallet QR, boonuspunktid, rahaline boonus, asukohateavitus; võimalik kaardi jagamine, kuid isiklike boonuste korral juhend hoiatab selle eest. [EK11] | CustomerIdentity + LoyaltyEarn/Burn + wallet pass + consent/preferences + identity/scope. Eralda points ledger, stored credit ledger ja discount. | Kliendi kaardi jagamine ei tohi võimaldada piiramatu saldo kulutamist |
| A14 | **Sooduskood**: käsitsi või reeglipõhine automaatne genereerimine; kehtivus, lunastuste max arv; ainult koodiga töötava soodustuse nõue. [EK12] | CouponLedger / PromotionRule; eligibility, issuance, redemption and reversal separately. | Ühekordne kupong kahes kassas samal ajal → üks redemption |
| A15 | **Aruandlus**: juhi töölaudade ja filtreeritud vidinate valik, mitu müügikohta, kuupäev/kellaaeg, salvestatud aruandevaated, perioodide võrdlus, eksport XLSX/CSV/PDF; maksegrupid ja tühistatud müügid eraldi. [EK13] | Scope-aware analytics semantic layer + saved views; all report measures defined once (revenue, tax, void, credit, tender, receivable, discounts, margin, gift card liability). | Sama tehingu neto müük kõigis raportites võrdne, eri vaate filtritel ainult lubatud müügikohad |
| A16 | **Audit**: kassa ja kontori tegevuste eraldi kronoloogilised vaated, otsing arve järgi, filtrid kasutaja/toote järgi; Ektaco kontorijuhend ütleb ligikaudu 3 kuu logid. [EK14] | Central tamper-evident AuditLedger with retention policy and legal/privacy review; AXAR ei tohiks vaikimisi eeldada 3 kuud on kõigile piisav, ega lõpmatu PII hoidmine õiguslikult lubatud. | Tagasimakse ↔ manager approval ↔ kassakasutaja ↔ väljavõte: jälgitav ka pärast töövahetust |
| A17 | **Offline kassas** toimib kohalik müük, kuid puuduvad peakassaga ühenduses printerid, terminal, kliendikaart, jagatud avatud arved, vahetuse lõpetamine, veebitellimused. [EK15] | Feature-capability state per device, not just ONLINE/OFFLINE boolean; safe subset until all dependencies ack. | Internet down + local receipt printer + local kitchen printer: erista keskserveri, LAN-i ja riistvara eraldi olekud |
| A18 | **Kassaseaded ja tšekimallid**: müügikohad → kassad; kviitungiplokid tingimuslikud, eri printerite laius 42/48 märki; muudatus nõuab andmete värskendust; kassapõhiselt saab piirata makseviise, küsida lauda, aktiveerida käike. [EK16] | ConfigurationVersion per outlet/register + controlled publish/ack/rollback; template engine with conditional blocks and validated device profiles. | Halb paigutus ei halvenda kassa müüki; new config visible only when acknowledged |
| A19 | **Saaliplaan**: müügikohas võib olla mitu korruse/terrassi plaani, laua kuju/koht/kohtade arv muudetav. [EK17] | FloorPlanVersion + Table identity + separate live occupancy state. | Saaliplaani ümberkujundus ei kaota avatud arvet ega laua identiteeti |
| A20 | **Käibemaksu risk**: Ektaco WooCommerce juhend näitab ajaloolist 22% standardmäära näidet. EMTA järgi on 2026 Eesti standard 24%. [EK8] [EMTA] | Date-effective TaxPolicy source-of-truth; UI/helpcenter näited ei ole õigusallikad. VAT decimal/tax point tests for prepayments, cancellations and historic sales. | 2025-06 vs 2025-07 vs 2026 müük ja ettemaks, korrektne KM ilma ajaloo ümberarvutuseta |

## B. Olekumasinate täpsed piirid AXARi jaoks

### B1. Müügitellimus ja ettemaks (lahku lõpparvest)

[AX] \`SalesOrder: DRAFT → CONFIRMED → PARTLY_PREPAID | FULLY_PREPAID → FULFILLED → INVOICED → CLOSED\`.
Erandid: CANCELLED, EXPIRED, PARTIALLY_FULFILLED, DISPUTED. Ettemaksu raamatupidamislik/maksustamise käsitlus peab kasutama maksujuristi/raamatupidaja kinnitatud reegleid, mitte automaatset müügitulu topeltkannet.
- \`PrepaymentReceipt\` ja \`FinalInvoice\` on kaks dokumenti; \`PrepaymentAllocation\` sisaldab amount, tax code/effective date ja invoice-line links.
- Lubatud \`amountAllocated <= amountReceived\`; koostatud koondarve on originaalide vaade/koond, mitte uus müük.
- Kui ettemaks tagastatakse enne tellimuse täitmist: rahaline tagastus + sobiv parandusdokument; laokulu ainult füüsilise kauba liikumisel.

### B2. Avatud arve, kahe kassapidaja konflikt

[AX] \`OrderBill: OPEN(version) → KITCHEN_SENT → PART_PAID → PAID → CLOSED\`. Split creates child PaymentObligations over disjoint line fractions, but one cooked product remains one kitchen task and one stock consumption. Staff transfer uses lock lease + version CAS. \`applyDiscount\` after preview receipt is governed by configurable explicit business rule, not a hidden surprise.

### B3. Makseviis ja raha tõe allikas

[AX] \`PaymentIntent → Attempt → ProviderStatus(unknown/declined/approved) → TenderRecorded → SettlementMatched\`.
- Cash settlement: real physical drawer money, subject to shift count.
- Card integrated: provider txn ID; capture status required.
- Card manually entered: external reference/evidence, flagged for reconciliation.
- Transfer: receivable; open until bank match.
- Gift card: redeem liability, distinct balance.
- Coupon: reduces sale price (discount); it is NOT a gift-card tender.
- Marketplace: cash collection by platform; later platform net settlement minus commission. The receipt, provider fee and revenue must not be counted twice.

### B4. Köögi ja kliendi järjekord

[AX] \`KitchenTicket: WAITING → IN_PREP → READY → HANDED_OVER\`. QDS is a separate, minimally identifying projection, not authoritative order status. Device subscription binding: one active lease per display-config ID, automatic reconnect with stream cursor and full snapshot fallback. Expiration only removes the item from **screen**, never deletes the order.

### B5. Veebipoe/toote sync

[AX] \`MenuPublishJob: DRAFT → VALIDATED → QUEUED → SENT → ACKED | FAILED_RETRY → RECONCILIATION_REQUIRED\`.
An external SKU match must not silently merge two unrelated products. An external order can enter one of three explicit business modes (sales order, office invoice, till approval); the selection changes how/when tax, stock and receivable documents are created.

## C. Ektaco ekraanipiltide tegelik visuaalne vaatlus

Allpool toodud lingid on kontrollitud kujutised, mis avati ja vaadati 2026-11-26 uurimise käigus; UX järeldused on [AX], mitte peidetud Ektaco skeemi faktid.

1. [CC5 kassa avatud arvete ekraan](https://ektaco.ee/wp-content/uploads/2021/02/260202-11-53-41-dnplayer-860x484.png). Nähtav: vasakul Arved, Veebitellimused, KDS, teenindaja/kassa ja Tegevused; paremal avatud arved nummerdatult ja „Uus arve“. **[AX]** Kassa tööruumi peamised funktsioonid peavad olema ühe pilguga leitavad. „Veebitellimused“ ja KDS on eri funktsioonid; ära sega staatusi.
2. [CC5 makse ekraan](https://ektaco.ee/wp-content/uploads/2021/02/260202-14-17-16-dnplayer-860x484.png). Nähtav: vasakul arve read ja KM eristus, keskel makseviisid, paremal summa, sisestatud sularaha ja tagasiantav raha ning suured klahvid. **[AX]** Eristada makseviisi valik, tegelik makseolek, antud sularaha ja tagasiraha.
3. [CC5 arverea detail](https://ektaco.ee/wp-content/uploads/2021/02/260130-11-35-32-dnplayer-860x484.png). Nähtav: ühe roa kogus, külalise ja käigu valik, rea jagamine, soodustus, erihind, kustutamine, lisandid ja erisoov. **[AX]** Üks rida ei ole lihtsalt product_id; order_line sisaldab guest allocation, course, modifier snapshot ja preparation note.
4. [Müügitellimuse toodete ja ettemaksu plokk](https://ektaco.ee/wp-content/uploads/2025/07/HELP_CC5OFFICE_tellimuse_tooted.png). Nähtav: nõutud ettemaksu 50%, summa 20, kahe ühiku 20-eurone kaubarida ja rea soodustused. **[AX]** Ühe tellimuse ettemaksu nõue, laekumine ja hinnaalused on eri andmed.
5. [QDS kahe veeru avalik olekuekraan](https://ektaco.ee/wp-content/uploads/2026/11/chrome-pjemap0rn6-860x288.png). Nähtav: kell ja kaks pealkirja „TEGEMISEL“ ja „VALMIS“; pilt ei tõenda tegelikku tellimuste arvu või latency't. **[AX]** QDS tuleb eristada kokatöö KDS-ist.
6. [WooCommerce valitud ladude samm](https://ektaco.ee/wp-content/uploads/2025/07/image-7-17.png). Nähtav: konfiguratsiooni mitmeastmeline viisard, lao valik ja seotud varuarvestus. **[AX]** Mitme lao kombineeritud varu vajab reserveeringute ning füüsilise asukoha reegleid.
7. [Ostuarve import: originaal kõrvuti tõlgendusega](https://ektaco.ee/wp-content/uploads/2021/11/pilt-860x499.png). Nähtav: imporditud tarnija/arve read vasakul, originaalarve paremal, „Võta andmed uuesti“, „Loobu“, „Salvesta ostuarvena“. **[AX]** OCR ei ole POST; eelvaade koos originaaliga peab olema enne kinnitamist.
8. [Müügi koondaruande ülevaade](https://ektaco.ee/wp-content/uploads/2022/09/chrome-54rh7m95bj-860x284.png). Nähtav: müük KM-ta, KM, KM-ga; tühistatud müük, krediit, soodustus, arved, jootraha ja teenustasu. **[AX]** Kõigil nendel mõõdikutel peab olema eraldi kindel definitsioon.

**Varem kirjeldatud, kuid selle täienduse pildikontrollis mitte taasesitatud üksikuid URL-e ei käsitle me siin pildiga kinnitatud tõendina.**

## D. AXAR ONE-i kontrollnimekiri — uued lõimumisservad, mitte lihtsalt nupud

| Serv | Source of truth | API/Event contract [AX] | Reconciler (new proposed check) |
| --- | --- | --- | --- |
| SalesOrder → PrepaymentInvoice → FinalInvoice | orders, tax documents | \`order.deposit.received.v1\`, \`invoice.finalized.v1\`, \`deposit.allocated.v1\` | Advance received - allocated - refunded = outstanding advance liability |
| Original invoices → consolidated bill | original invoices | \`invoice.consolidated.v1\` | Every child original counted in sales exactly once |
| Bill split → multiple tenders | bill lines, payment obligations | \`bill.split.v1\`, \`tender.recorded.v2\` | Sum allocated fractions = original line quantity/value, no duplicate sale/stock |
| Employee shift ↔ drawer session | drawer + staff event | \`shift.open/close\`, \`drawer.open/close\` | Each physical cash movement counted against exactly one drawer |
| Gift card issue / reload / redeem | stored value ledger | \`giftcard.issued/loaded/redeemed\` | Opening balance + loads - redemptions - refunds = closing gift card liability |
| Loyalty earn / burn | loyalty points ledger | \`loyalty.earned/redeemed\` | Unique source sale, reverse points on refund once |
| Kitchen Ready → QDS display | kitchen ticket (source), display projection | \`kitchen.ticket.ready.v2\`, \`pickup.display.updated.v1\` | Projection only; display expiration never cancels ticket |
| Woo products/stock ↔ order import | catalog/stock + external order inbox | \`channel.publish.ack.v1\`, \`channel.order.received.v1\` | External order ID and channel exactly once; channel stock mismatch flagged |
| Telema supplier line → receiving | approved vendor doc | \`vendor.line.mapped.v1\`, \`goods.received.v1\` | One external supplier invoice posted once, unresolved SKU=0 posted |
| Promo code issue/redeem | coupon ledger | \`coupon.redeemed.v1\` | Redeemed count <= cap and per-client limit |
| Configuration rollout → till/printer | config service | \`device.config.applied.v1\` | Target last acknowledged version matches planned after update window |
| POS partial disconnect → recovery | edge journal | \`device.capability.changed.v1\` | No recorded real card charges without provider reference |

## E. End-to-end acceptance scenarios (minimum extra cases)

**Scenario E1 – Catering advance**: 10 meals preordered for next week; 50% advance → one deposit receipt and tax treatment by approved policy; delivery day final invoice with deposit allocation; refund 2 meals only; stock consumed for 8 prepared items; accountant export no duplicate revenue.

**Scenario E2 – Zoo 10 till + shared inventory**: ten outlets, four warehouses, one central prep kitchen. One production batch converted from raw stock, moved to three outlets, item sold at correct outlet-specific price; 2 simultaneous tills; QDS shows ready; daily closing matches outlet and organization totals.

**Scenario E3 – Wallet gift card, offline tills**: 20 € stored value sold at till A, same code scanned at tills B/C, concurrent redemptions 12 € and 15 €; at most 20 € can be consumed. With network down, block if no cryptographically sound authorized offline redemption protocol.

**Scenario E4 – Mixed payments / receivable**: bill 30 €, 10 € cash, 10 € card, 10 € transfer receivable. Only cash and provider-captured card count paid; transfer remains due. Manual terminal evidence mismatched with bank → investigation.

**Scenario E5 – External multi-channel**: Product price changed in AXAR; Wolt price A, Woo price B; Woo store aggregates 2 warehouse stocks; duplicated webhook and unknown SKU; order goes review, no silent sell of wrong stock; publish ACK and retry tracked.

**Scenario E6 – Kitchen and queue display**: Separate hot and cold stations; same order generates intended station jobs without duplicate preparation; guest screen only displays status with anonymity; browser reconnect refreshes and does not revive already collected order.

**Scenario E7 – Stock count under live sales**: physical inventory count 0 vs uncounted NULL during concurrent POS sale. Correction posted with cutoff and no lost sale; reorder receives updated available stock.

**Scenario E8 – Discount / receipt / tax**: one customer coupon max 1 redemption, preview receipt after discount applied, 2025 and 2026 sales with time-effective VAT, refund once across outlets.

**Scenario E9 – Shared register, two employees**: same register drawer held by two waiters with separate attendance shifts; physical opening/closing cash vs each person's sales; reports can reconcile without double cash.

**Scenario E10 – Evidence retention**: cancellation 4 months after original sale; complete signed payment, receipt and refund trace exists per approved retention policy; reject unauthorized access, maintain GDPR-compliant minimization and deletion controls.

## F. Accurate sources inspected in this second pass

[EK1] https://ektaco.ee/helpcenter/compucash5/muuk/muugitellimused/
[EK2] https://ektaco.ee/helpcenter/compucash5/muuk/koondarved-2/
[EK3] https://ektaco.ee/helpcenter/kassa/cc5-kassa/muuk-ja-arvega-seotud-tegevused-cc5/
[EK4] https://ektaco.ee/helpcenter/kassa/cc5-kassa/arveridadega-tegevused-cc5/
[EK5] https://ektaco.ee/helpcenter/compucash5/muuk/makseviisid/
[EK6] https://ektaco.ee/helpcenter/kassa/cc5-kassa/vahetused-cc5/
[EK7] https://ektaco.ee/helpcenter/compucash5/liidesed/qds/
[EK8] https://ektaco.ee/helpcenter/compucash5/liidesed/woocommerce-veebipood/
[EK9] https://ektaco.ee/helpcenter/compucash5/liidesed/telema/
[EK10] https://ektaco.ee/helpcenter/compucash5/tootehaldus/digitaalne-kinkekaart/
[EK11] https://ektaco.ee/helpcenter/compucash5/kliendihaldus/compucash-walleti-seadistused/
[EK12] https://ektaco.ee/helpcenter/compucash5/muuk/sooduskoodid/
[EK13] https://ektaco.ee/helpcenter/compucash5/muuk/toolauad/ ; https://ektaco.ee/helpcenter/compucash5/muuk/labimuugi-aruanne/ ; https://ektaco.ee/helpcenter/compucash5/muuk/muugi-koondaruanne/
[EK14] https://ektaco.ee/helpcenter/compucash5/personal/kassa-tegevused/ ; https://ektaco.ee/helpcenter/compucash5/personal/kontori-tegevused/
[EK15] https://ektaco.ee/helpcenter/kassa/cc5-kassa/offline-too-cc5/
[EK16] https://ektaco.ee/helpcenter/compucash5/seaded/kassa-seadistused/
[EK17] https://ektaco.ee/helpcenter/compucash5/seaded/saaliplaanid/
[EMTA] https://www.emta.ee/uudised/maksumuudatused-2026
[EXTRA] https://ektaco.ee/helpcenter/compucash5/kliendihaldus/kliendid/ ; https://ektaco.ee/helpcenter/compucash5/tootehaldus/kinkekaardid/ ; https://ektaco.ee/helpcenter/kassa/cc5-kassa/kassaga-seotud-uldised-tegevused-cc5/ ; https://ektaco.ee/helpcenter/compucash5/personal/oiguste-grupid/ ; https://ektaco.ee/helpcenter/compucash5/uldine-compucash-5/6-sammu-alustamiseks-cc5/

**Next engineering step:** Claude reviews both documents against #60/PR #63 actual code and updates integration matrix and roadmap by adding only truly missing capability IDs. Small PRs implement and test each slice. Do not merge this research PR without owner approval. Do not purchase, deploy or charge live accounts.
