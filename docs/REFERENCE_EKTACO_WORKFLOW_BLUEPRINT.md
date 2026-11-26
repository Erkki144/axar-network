# AXAR ONE — Ektaco CompuCash 5 tööloogika võrdlusuuring ja teostatav protsessimudel

**Koostatud:** 2026-11-26. **Seos:** #60 (tervikintegratsioon), #61 (tootekataloog), #40 (Menu Studio), #14 (ladu), #38 (retseptid), #48 (tühistamine), #25 (seadmed), #27 (hierarhia).
**Staatus:** võrdlusuuring + insenertehniline lähteülesanne; mitte juba valminud funktsioonide väide. Selles dokumendis ei muudeta tootmiskoodi.
**Võrdlusalus:** Ektaco avalikud CompuCash 5 klienditoe kasutusjuhendid ning juhendites avaldatud kuvatõmmised; ei ole ligipääsu Ektaco lähtekoodile, andmebaasile, päris API-le ega testkeskkonnale. Seetõttu eristame avalikult **näidatud käitumist** ja AXARile **soovitatavat teostust**. Ärge kopeerige Ektaco kasutajaliidese kujundust, tekste, pilte ega ärilist suletud koodi.

## 0. Kuidas seda dokumenti kasutada

- **[E]** = Ektaco avalikus juhendis või pildil näidatud käitumine.
- **[X]** = AXAR ONE olemasoleva koodi olukord vastavalt PR #63 code-backed inventory'le, kontrollitav sealsete viidete ja testidega; PR #63 on eraldi PR, mitte automaatselt main.
- **[A]** = AXAR ONE jaoks pakutav lahendus/ärireegel; ei ole väide Ektaco sisemise arhitektuuri kohta.
- **[T]** = nõutav automatiseeritud vastuvõtutest.
- Iga allolev töövoog tuleb tõendada: UI tegevus → API command → andmebaasi muutus → outbox/event → järgmise mooduli reaktsioon → aruanne → tõrke korral taastumine.
- Määrake igal olekuüleminekul idempotency key, õigused, audit trail, event version ja konfliktireegel.

## 1. Kasutajavaated vs andmestruktuur: üks tervik, mitte sõltumatud ekraanid

[E] CompuCash 5 kontoris on tooterühmade puu ja tooteregister; tootekaart koondab üldinfo, hinnad, soodustused, lao, tarnija, müügipunktid ning retsepti. Kassa paneelid on peapaneel, tegevustepaneel ja alampaneel. Müügirühma juurest saab määrata ka köögiprinteri ja müügipunkti.

[A] AXAR ei tohiks segi ajada alljärgnevaid mõisteid:
1. **LegalEntity** (juriidiline isik, maksud, arvestus) → **Site** (objekt/loomaaed) → **Outlet** (müügipunkt) → **Register/Device** (kassa/seade); laod võivad teenindada mitut müügipunkti, kuid iga liikumine kannab füüsilise lao identifikaatorit.
2. **Product** (püsiv ID, müügiartikkel) ≠ **InventoryItem** (füüsiline kaup) ≠ **SupplierProduct** (tarnija kood ja pakend) ≠ **RecipeVersion** (tooraine kooslus) ≠ **ProductionBatch** (ette tehtud valmistoode) ≠ **MenuEntry** (toote nähtavus kindlas kanalis ja menüüs) ≠ **Button** (kassa paigutus).
3. **Order** (kliendi tellimus) ≠ **KitchenTicket** (valmistamistöö) ≠ **Invoice/Sale** (müügidokument) ≠ **PaymentAttempt/Tender** (maksekorraldus) ≠ **StockMovement** (füüsilise kauba kanne).
4. **Catalog snapshot** ja **historic sale line snapshot** on eraldi. Uus hind, retsept või tootenimi ei tohi muuta kinnitatud müügidokumenti.
5. **Stock document**: DRAFT → REVIEWED → POSTED või CANCELLED; POSTED kannete parandamiseks REVERSAL + uus dokument, mitte ajaloo ülekirjutamine.

[X] PR #63 inventuuris on müük, laod, retseptid, tellimus/köök, seadme offline-journal, maksekatse simulaator ja R1–R13 kooskõlakontroll. Puuduvad/pooleli on toote loomise kontoriekraan, menüü avaldamine, inventuur, mahakandmine, ladudevahelised liikumised, makseterminali reaalne ühendus, printer, raamatupidamise tegelik saatmine ja tervikhierarhia. Vaata #63 detailseid tõendeid.

**Üldine invariant:** iga rahaline/laoline sündmus peab viitama ühele allikdokumendile, täpsetele ridadele, juriidilisele isikule, müügikohale/ladu(de)le, ajale, kasutajale ja sündmuse versioonile. Ühtegi posti ei tohi töödelda kaks korda.

## 2. Tooted, ühikud, retseptid ja valmistootmine

[E] Toode võib olla kassast peidetud komponent. Ektaco juhend eristab sisseostu ühikut (kg, l, pakk, karp) retseptis kasutatavast ühikust (g, ml, tk). Retseptis on kogus, kaoprotsent ja netokogus. Ektaco Caesari salati kuvatõmmis näitab näiteks salatipõhjal 171,42 g brutot, 30% kadu ja 120 g netot; kanafileel 187,5 g brutot, 20% kadu ja 150 g netot. Sellest järeldub kalkulatsioon: bruto = netto / (1 - kao osakaal). Näiteks 120 g / 0,70 = 171,43 g ümardamisel.

[E] Ektaco eristab retseptitoote müüki, mille korral kuluvad komponendid, ja ette valmistatud valmiskauba tootmist: 200 kg kartulisalatit valmistatakse kesklaos, komponendid vähenevad seal ning müügikoha lattu lisandub 200 kg valmis salatit. Müügil peab vähenema **valmistoode**, mitte uuesti salati koostisosad. Juhend mainib, et Ektacos võib see vajada eriseadistust.

[A] Kaks üksteist välistavat laorežiimi iga order-line jaoks:
- **MAKE_TO_ORDER:** POSTED SALE → retseptiversiooni ja koguse snapshot → kulu komponentidena konkreetsest laost; sama müügirea kohta maksimaalselt üks consumption batch.
- **PREPRODUCED:** POST PRODUCTION → toorained maha lähtelaost, valmis partii üles sihtlattu; SALE → üksnes valmistoote partii maha. Keelata topeltmahaarvamine.
- **STOCKED_GOOD:** pudel või jäätis → müügil otse füüsiline kaup maha.
- **NON_STOCK:** teenus/pilet → laokannet ei tehta.
- Partiid, kõlblikkusaeg, allergeenid, retseptiversiooni effective_from/to, saagise ja ühikute täpne rational/decimal teisendus.
- Omahinna arvutus peab kasutama tehingu kuupäevaks kehtivat kulumudelit ning säilitama arvutuse sisendid. Retsepti muutus ei kirjuta ümber vana müügi kulu.
- Kui sama füüsilist toodet kasutatakse ka pakendina, siduda pakendi komponent konkreetsesse retsepti või artiklisse.
[T] Müü üks salat mõlemas režiimis; MAKE_TO_ORDER kulutab koostisosad täpselt korra, PREPRODUCED kulutab koostisosad ainult tootmisel ning valmisvaru ainult müügil. Kaoprotsendi ühikutest sõltuv ümardus testida.

## 3. Tarnija, ostuarve import ja kauba vastuvõtt

[E] Sisseostu töövoog: hankija peab olema tuvastatud; dokumendil tarnija, number, kuupäev, ladu, tähtajad, lisakulud ja read; faili importimisel tuvastatakse tarnija ning arve veerud; võrreldakse impordituid andmeid originaalarvega; tarnija kaubakood tuleb esmasel korral siduda laoartikliga või luua artikkel; seejärel dokument kinnitatakse/aktsepteeritakse. Korduvate tarnijate seosed jäävad meelde. Juhend näitab AI-toega impordi võimalust ja automaatse järgmise importimise valikut.

[A] **Kriitiline eristus:** OCR/AI extraction ≠ finantsiline/liikumise POSTED kanne.
1. RECEIVE_FILE → EXTRACTED (PDF/XML/e-arve tekst + confidence ja algfaili räsi).
2. SUPPLIER_MATCHED → ITEM_MAPPING_REVIEW (supplier_sku, GTIN, pakend, ühik, KM määr, hind, kogus).
3. TOTALS_VALIDATED: sum(net + tax = gross), rea ühikud, koondsumma, duplikaatarve, tolerantsid.
4. DRAFT_DOCUMENT salvestatakse; tarne ja füüsiline kaubavastuvõtt võivad erineda arve mahust (received_qty, damaged_qty, backorder).
5. Manager POST → täpselt üks laokanne rea kohta ja vajadusel ostukohustuse kirje; dokument lukustub.
6. Vale dokument → selgesõnaline REVERSAL, kuhu lisatakse põhjus ja algdokumendi link.
- Hinnatõus ja tarnekoguse lahknevus peavad olema nähtavad enne aktsepteerimist.
- Uue tarnija artikli seos tehakse kontrollitult; AI ei tohi suvalist toodet automaatselt ühendada ega lattu postitada.
- Transport, pakendisoodustus ja lisakulud tuleb jaotada läbipaistva poliitika järgi; ühiku omahind ei tohi põhineda varjatud ümardusel.
[T] Impordi sama PDF kolm korda: üks tarnija arve, üks POST; muuda ühe rea ühikut ja veendu, et kontroll annab vea; halb GTIN ei seo valesti.

## 4. Lao vahekanne, mahakandmine ja inventuur

[E] Ektaco liikumise dokument kannab lähteladu, sihtladu, kuupäeva ja ridu; kinnitamisel toimub ladudevaheline liikumine ning dokument lukustub. Mahakandmisel on ladu, põhjus ja read; retseptitoote puhul saab valida retsepti lahkamise komponentideks. Inventuuri saab teha jooksva või ajaloolise seisuga. Avatud kinnitamata saatelehtedest hoiatatakse. **Tühi loetud kogus ei tähenda nulli**: tühja korral rida ei muudeta; 0 ainult siis, kui artiklit tegelikult ei leitud. Inventuuri võib lisada laos oleva valmistoote retsepti kujul. Kinnitatud inventuur teeb korrektsioonikande.

[A] Ühine StockLedger, tüübid RECEIVE, TRANSFER_OUT, TRANSFER_IN, PRODUCTION_CONSUME, PRODUCTION_OUTPUT, SALE_CONSUME, WASTE, COUNT_ADJUSTMENT, RETURN_RESTOCK, REVERSAL; iga kande source_type/source_id/line_id, effective_at, recorded_at, warehouse, product, qty_base_unit, cost, batch.
- Transfer peab olema aatomiline või kahes osas IN_TRANSIT: ei tohi ladu A vähendada ilma tarne olekut jälgimata; vastuvõtvas laos peab olema kinnitamise hetk.
- INVENTORY_COUNT: draft koos lõikeajaga; nähtav 'loetud', 'arvestuslik', 'erinevus'; NULL=loendamata, 0=reaalne null. Ühel ajal avatud inventuurid ja paralleelsed müügid vajavad deterministlikku lõikeaja reeglit ja lukustust/uuesti arvutust.
- Põhjustega WASTE: riknenud, valmistatud kuid ära visatud, personalitoit, proov, purunemine jne; raamatupidamislik käsitlus konfiguratsiooniga, mitte oletus.
- Lao varu ≡ algjääk + posted movements; eri dokumendid ei tohi liikuda 'tagantjärele' ilma ajatempli ja kooskõlakontrollita.
[T] 10 rida: 4 sisestatud, 1=0, 5 NULL → muudetakse ainult 5 rida; katkesta pärast salvestamist ja proovi uuesti; paralleelne müük ei kao.

## 5. Taastellimine ja ostusoovitus

[E] Ektaco taastellimisel on ladu, minimaalne piir, soovituslik tellimiskogus ja tarnija; kui seos tarnijaga puudub, tuleb see luua. Ostuvalikute juures tuleb kasutajal kontrollida koguseid ja suunata artiklid tellimustele.
[A] ReorderService ei tohi vaadata vaid 'praegune varu alla piiri'. Tarvis: available = on_hand - reserved - safety_hold + confirmed_inbound; demand_rate, cover_days, lead_time, supplier MOQ/pack multiple, shelf_life, upcoming menu/events, expected zoo visitor flow, spoilage risk. AI toodab **selgitatava ostusoovituse**, aga ostutellimust ei saada ega ostu tehta ilma lubatud äriprotsessita.
[T] Madal laoseis + kinnitatud saabuv tarnetellimus väldib teist ostu; pakendikoguse ümardus on korrektne; aeguv kaup vähendab soovitust.

## 6. Hinnakirjad, soodustused, variatsioonid ja menüü avaldamine

[E] Ektaco hinnakiri on seotud müügipunkti ja soovi korral ajavahemikuga. Soodustustel võivad olla kuupäeva/kellaaja, tootekomplekti, kliendi, koguse ja prioriteedi tingimused. Lisandid võivad avaneda toote valikul automaatselt, olla tasulised või hinnas sisalduvad ja min/max arvuga. Variatsioonidel on omadused ja väärtused. Veebimenüüd Boltile/Woltile on eraldi avaldamiskanalid: toote- ja rühmajärjestus, kanalipõhine hind, tõlked, saadavus, lisandid, toodete peatamine, automaatsed muudatused. Ektaco juhend hoiatab, et keskse halduse korral võivad teenusepakkuja enda menüümuudatused olla lukus.

[A] MENU STUDIO on versioonitud publish süsteem:
- Product master → PriceBook(valid_from, outlet/channel/customer segment) → ModifierGroup(min/max/default/price_effect) → MenuVersion(entries, sort order, photos, translations) → TargetMapping(outlet, till, TV, QR, Wolt, Bolt).
- Looge **DRAFT → VALIDATED → APPROVED → PUBLISHED → ACKNOWLEDGED/RETRY**. Avaldamine ei ole pelgalt DB update: kuni sihtseade pole muudatust ACK-inud, näidatakse kontoris viimast kinnitatud versiooni ja tarne järjekorda.
- Kassa peab säilitama viimase kehtiva allkirjastatud menüü offline-kasutuseks. Hind kassas fikseeritakse reana quote/checkout hetkel; avatud arve vahepealse hinnamuutuse puhul läbipaistev reeglistik.
- Toote peitmine ühest kanalist ≠ arhiveerimine globaalses tooteregistris ≠ laojäägi nullimine. Ladu muutub ainult posted laodokumendi tulemusel.
- Promotions engine määrab konfliktid (stackable, best-deal, caps, cashier override, VAT allocation), salvestab applied_rule_version ja breakdown'i.
- TV, POS ja Wolt võivad näidata eri kujundusi, aga kasutavad sama tooteidentiteeti ja hindade reeglit.
[T] Loo kontoris toode 'Seljanka 6.50' → määra retsept → avalda müügikohta A → kassanupp ilmub ilma restartita → TV/kanal saab õige versiooni → müük salvestab 6.50; muuda hind 7.00 → eelmine müük jääb 6.50; B müügikoht ei muutu.

## 7. Kassapaneelid, arve, köök ja teenindus

[E] CC5 paneelid: peapaneel, kiirtegevused, alampaneel. Nupu tüüp võib olla toode, rühm, tühi ruum või alampaneel. Nupul võib olla kasutajale erinev tekst, värv ja pilt. Tootegrupiga seotud köögiseadmete suunamise juures saab määrata mitu printerit, saatev kassa ja printiv kassa, samuti KDS-ekraani numbri. Saaliplaanid on müügikohapõhised ja laudade asukohad muudetavad.

[A] Müügivoog:
1. CUSTOMER/TABLE/TAKEAWAY → OPEN_ORDER, assign salesperson, outlet, table, menu_version.
2. Add line → select variation, mandatory modifier(s), allergies or notes, price snapshot, recipe_version; invalid min/max blocks confirm.
3. SEND_TO_KITCHEN → immutable ticket sections (hot/cold/pizza/bar) generated by routing rules, with one guaranteed ticket ID and retry. Routing may fan out to several devices without causing duplicate preparation.
4. PREPARING → READY → SERVED/PICKED_UP; UI learns via SSE/WebSocket with recovery after gap. If printer unavailable, job stays queued and KDS/manual fallback is visible.
5. ACCEPT_PAYMENT → payment(s) → invoice close → outbox sale.completed → inventory consume → accounting export queue; define exact business event boundary so stock never posts twice.
6. Cancellation before cooking vs after preparation use different stock disposition rules; compensation actions, no destructive rewrite.
- Design quick cashier UX: large targets, 1–2 tap frequent products, explicit pending/failed status, visible order progress; office supports accessibility and bulk editing.
[T] Lost response while sending kitchen ticket: retry yields same ticket; two KDS devices configured produce their own delivery copies but one kitchen preparation; wrong modifier cardinality blocked.

## 8. Payment, invoices, refunds, reversals

[E] Ektaco arveregistris saab avada arveread ja nende laomõju, makseviisid, arve tehnilised detailid. Maksetüübi korrigeerimisel peab summade kogusumma võrduma arvega. Arve tühistamise kuvatõmmisel on reas valik 'Tagasi lattu'. Valmistatud/ära visatud toitu ei tohiks tagasi saada toorainevaruks. Hulgiarve saab välja anda ilma kassata, kinnitada ning krediteerida negatiivse dokumendiga.
[A] Lahutada kolm toimingut:
- **Correction of recorded tender**: parandab valesti märgitud sularaha/kaart, kuid ei algata päris rahaliikumist; vaja originaalset payment transaction receipt'i.
- **Refund**: teeb päris raha tagastuse maksevahendi kaudu (sandboxis vaid simulaator), idempotentne provider reference, unknown-state reconciliation.
- **Credit / void**: muudab müügi- ja maksuarvestust uue parandusdokumendi kaudu; algset müüki ei kustutata. Võib olla osaline ja rea kaupa.
- Iga rea disposition: RESTOCK_SEALED / WRITE_OFF_PREPARED / NO_STOCK_MOVEMENT, vajab põhjendust ja nõutud juhi heakskiitu. PREPRODUCED valmistoodet võib tagastada vaid tegelikult müügikõlbliku partii tingimustel; äravisatud salatit ei tohi maagiliselt tagasi muuta kartuliks.
- 'Unknown' terminali tulemusega ei või teist kaarditehingut automaatselt teha enne pakkujalt staatuse kindlakstegemist.
- Kardimakse ja pangakonto peab kasutama päris pakkuja heakskiidetud integratsiooni, testsandbox ei ole live.
[T] 3 arverida; tagasta 2: avamata vesi RESTOCK ja valmistatud burger WRITE_OFF; arve, laoraamat, kassapäevik, KM ja accounting export peavad kooskõlla jääma. Sama refund-käsku 3 korda → üks refund.

## 9. Veebitellimused, Wolt/Bolt, staatuste omavaheline side

[E] Wolt POS ID seob kanali toote kassas oleva tootenumbriga. Ektaco Wolt juhend näitab: tellimus saabub ja eristub, teenindaja määrab valmistamisaja, kinnitab, mis saadab aja kanalile ning köögikviitungi; tellimus märgitakse valmis ja info jõuab kanalisse; arve suletakse. Kahepoolne menüühaldus toetab saadavuse suunamist.

[A] ChannelOrder.status: RECEIVED → ACCEPTED(preparation_eta) → KITCHEN_SENT → READY → HANDED_OVER → SETTLED; alternatiiv DECLINED/CANCELLED/FAILED/REVIEW. ExternalOrderId + channel + entity unique. Payment ownership (channel collected vs local collected) explicit, otherwise tekib topeltlaekumine. Channel SKU mapping on versioned, report platform fees and net settlements separately. Price/availability publish ja inbound order peavad olema eraldi ühendused ning mõlemal jälgitav ACK/retry.
[T] Sama Wolt webhook 4 korda → üks order, üks kitchen ticket, üks stock consumption; price mismatch and unknown item → REVIEW, mitte müük vale tootega.

## 10. Personal, õigused ja töövahetus

[E] Kasutaja sidumine müügipunkti ja lao ning õiguste grupiga mõjutab seda, milliseid tegevusi ja aruandeid saab teha. Kasutaja deaktiveerimisel kaob ligipääs. Kassa ja kontori õigused on lahutatud ning müügikohtade lisandumisel tuleb kasutaja ligipääs üle vaadata.

[A] Role capability + scope (legal entity/site/outlet/warehouse/register) + step-up (refund, stock adjustment, purchase POST, price publish). Server kontrollib, mitte üksnes peidetud nupp UI-s. Töötaja lahkumisel revoke offline device token following secure revoke protocol; secure till operation interrupted by expired permits. Audit event captures before/after, reason and approver (minimal personal data).
[T] Müügikoha A töötaja ei näe B andmeid. Inventuuride kinnitamise õigus on eraldi loendamise õigusest. Deaktiveeritud töötaja ei pääse serverisse; offline revoke-policy testitav.

## 11. Raamatupidamine ja registrite sobitamine

[E] Merit Aktiva juhend kasutab API ühendust, müügi- ja ostuarvete eksporti, KM määrade, makseviiside, müügipunkti/osakonna ja artiklikoodide vastendamist, koond- või üksikarve eksporti, logi ja korduskatset. Juhendis on selgesõnalised eri juriidiliste kehade seadistused.

[A] AXAR CONNECT:
- Company-specific ChartOfAccounts mapping by tender, tax, outlet, expense category, customer, supplier, channel fee.
- DeliveryLedger: export intent → transmission → receiver ACK with external_document_id or typed failure. Backoff + idempotency + dead-letter + manual reconcile.
- Avoid 'API call succeeded' = 'booked' assumption. Must validate receiver reference and daily totals; number series and legal VAT reviewed with an accountant.
- Tenders ≠ net bank settlement: card provider fees and platform commissions require distinct entries.
- Period close uses snapshot and a documented correction period; later corrections need reversals, not silent changes to prior reports.
[T] 1000 sales, 25 refunds, 2 offline replay batches, 2 warehouse transfers: reported revenues, payment balances, stock balances and exported accounting totals reconcile with explained rounding, no duplicate books entry.

## 12. Offline, devices, failure and recoverability

[X] PR #63 lists an existing hash-chained device journal, sync outbox/inbox, per-register receipt numbers, strict sequence checks, quarantine and 72-hour cap; hardware and real transport are not yet proven. PR #64 and #65 (office screens and terminal adapter wiring) were opened after that snapshot and must be reviewed independently.

[A] For every edge, define source of truth and degraded mode:
- Till cash sale offline: local durable journal before UI says 'saved'; receipt number reserved per till; replay to central with same identity; backend dedup by tenant/device/seq/hash.
- Kitchen ticket offline local station: distinguish device dispatch from message acceptance; no auto-resend resulting in duplicate cooking.
- Card terminal network down: do not claim an approved charge; show unknown/pending and block second attempt until reconciliation.
- Price/menu distribution: last valid signed snapshot + effective date, central ACK visibility, no partial mixed version.
- Sync conflict: quarantine with actionable resolution reason; never discard funds or stock movements without audit.
- Printer offline: queued print job, resend only after explicit outcome check; duplicate detection where printer protocol permits.
- R1–R13 reconciliation in #63 plus explicit rules for production consumption, count cutoff, transfer pairs, vendor invoice mapping, menu version ACK, external channel import, accounting ACK.
[T] Abrupt power loss at every transition; reconnect after 72h; duplicate webhook; unknown payment; document posted twice; menu update during sale. All monetary/stock invariants must survive restart.

## 13. Kuvatõmmiste kontroll: mida neist tegelikult õpime

Reference images are links to Ektaco-published screenshots, not copied assets. Use them for workflow study, not pixel-level cloning.

| Public screenshot | What is visibly verified | UX/logic question for AXAR |
| --- | --- | --- |
| [Tooteregister: left tree + configurable right list](https://ektaco.ee/wp-content/uploads/2025/07/image-21-1.png) | category navigation, filters, quick-add, column configuration | How to make a product findable in <3 seconds and still show stock, pricing and channel state? |
| [Recipe screenshot: Caesar salad](https://ektaco.ee/wp-content/uploads/2025/07/image-35-1.png) | line ingredient picker, gross amount, loss %, net, save | Show exact unit conversions and recipe version; warn when gross/net inconsistent |
| [Kassapaneel editing view](https://ektaco.ee/wp-content/uploads/2021/02/chrome-mgxdceo9wf-860x277.png) | mixed category/product buttons, empty slots, direct add | One-click preview for each till model and reversible publish |
| [Supplier invoice: original vs interpreted](https://ektaco.ee/wp-content/uploads/2021/11/pilt-860x499.png) | side-by-side comparison and review/redo/save actions | Never post to stock before verified mapping and totals |
| [Inventory count table](https://ektaco.ee/wp-content/uploads/2021/11/chrome-pouixaxfbm-860x224.png) | counted quantity, discrepancy, book stock, value, note | Distinguish blank count from physically counted zero |
| [Cancel invoice per-line restock checkbox](https://ektaco.ee/wp-content/uploads/2021/02/chrome-i5jiunsqnn.png) | visible per-line 'Tagasi lattu' choice | Add explicit disposition, reason and financial consequences |
| [Modifier maximum selection](https://ektaco.ee/wp-content/uploads/2021/02/chrome-b7hra4thni.png) | min/max values for multi-select modifier | Validate both cashier UI and backend |
| [Price-selection screenshot for modifiers](https://ektaco.ee/wp-content/uploads/2021/02/chrome-8ambedfcwj.png) | linked price list for modifier charging | Avoid charging twice when included in meal price |

**What we cannot infer:** database schema, actual terminal settlement semantics, private APIs, precise accounting journals, concurrency guarantees, actual UX latency, and compliance of Ektaco. No statements of those as confirmed facts.

## 14. Directed dependency map and acceptance gates

Data owners (AXAR proposal):
- CatalogService owns Product and PriceBook; RecipeService owns RecipeVersion and production; MenuPublisher owns MenuVersion and destination ACK; StockService owns InventoryLedger and count/transfers; SalesService owns orders and immutable sale snapshots; PaymentService owns tender attempts; AccountingAdapter owns export receipts; IdentityService owns permissions.
- Changes reach other domains via **versioned, transactional outbox** with idempotent consumers; cross-domain direct SQL writes forbidden.

**Golden path A (made to order):** supplier PDF → item mapping → posted receiving → recipe publish → price + modifier publish → POS snapshot → order → kitchen → simulated then later live payment → sale → recipe consumption → margin → accounting queue + ACK → daily reconciliation.
**Golden path B (preproduction):** central warehouse inventory → signed production order → ingredients consumed → finished batch → outlet transfer → sale finished batch → daily stock count → margin.
**Golden path C (exception):** sale → payment unknown → explicit reconcile → authorized partial refund → per-line restock/write-off → credit note → accounting reversal → same day and previous day reports reconcile.
**Golden path D (offline and delivery):** no network → offline sale → local journal → reconnect → central exactly once → platform order webhook duplicate → only one kitchen ticket → no duplicate sales/stock/finance.
**Golden path E (publisher):** product created → price A/B and promotional date → send to 10 outlets + TV + Wolt/Bolt → target ACK tracking → product sold out locally → only applicable channel pauses → historical bill unchanged.

All five must pass PostgreSQL/RLS + browser E2E + seeded realistic restaurant/zoo scenario. Add property-based and concurrency tests for inventory and money math. Real hardware and provider sandbox remain independent gates (do not label simulated tests live-ready).

## 15. Integration backlog: gap / owner / linked issues

| Priority | Specific gap or design task | Existing issue | Proof gate |
| --- | --- | --- | --- |
| P0 | Align this design with code-backed inventory; update E1–E18 and R1–R13 if new edge | #60, PR #63 | evidence matrix rows for all five golden paths |
| P1 | Product CRUD, price validity, stock mode, activation, office UI | #61 | product-to-sale test from office without DB seeding |
| P1 | Dynamic modifiers, price rule precedence, timed availability | #40, #61 | unit test discount graph + browser modifier validation |
| P1 | Recipe gross/net loss, versioning, stock mode distinction, batch production | #38, #14 | dual-mode sale E2E with quantities + COGS |
| P1 | Vendor bill import, supplier SKU crosswalk, match review, duplicate guard | #14 | original/inferred preview and POST once test |
| P1 | Physical stock count at timestamp, NULL vs zero, adjustments, transfer, waste | #14 | 3 distinct docs + concurrent stock test |
| P1 | Cancellation/restock/waste/credit/refund full state machine | #48 | sealed bottle + cooked dish + split tender test |
| P1 | Accounting mapping and actual acknowledged delivery | #4, #8 | exact ACK & duplicate prevention + daily matching |
| P2 | Menus per outlet/channel, TV publish with target ACK | #40 | one menu version in POS/TV/QR and channel targets |
| P2 | Kitchen station routing, KDS/printer delivery and recovery | #25, #42 | two stations, one ticket, unplug/replug printer |
| P2 | Wolt/Bolt inbound/outbound order and menu sync | #18, #40 | duplicate webhooks + mismatch quarantine |
| P2 | Multi-legal-entity boundaries, employees scoped to warehouses and sites | #27, #33 | RLS isolation and separated accountant exports |
| P2 | Hardware/offline/chaos + 10-outlet load | #29, #42, #44 | real-device reports and stress tests |

**Order instruction:** do not start duplicate parallel implementations of the same model while Claude is working on #60/#61; open focused small PRs and link this document. Business owner approves live terminal provider, hosting, production deployment and financial/legal settings; technical design/test/migration remains engineering responsibility.

## 16. Primary sources (exact pages examined)

1. https://ektaco.ee/klienditugi/
2. https://ektaco.ee/helpcenter/compucash5/
3. https://ektaco.ee/helpcenter/compucash5/tootehaldus/tooted/
4. https://ektaco.ee/helpcenter/compucash5/tootehaldus/kassa-nupud/
5. https://ektaco.ee/helpcenter/compucash5/tootehaldus/lisandid-erisoovid/
6. https://ektaco.ee/helpcenter/compucash5/tootehaldus/veebimenuud/
7. https://ektaco.ee/helpcenter/compucash5/tootehaldus/toote-variatsioonid/
8. https://ektaco.ee/helpcenter/compucash5/laohaldus/sisseost/
9. https://ektaco.ee/helpcenter/compucash5/laohaldus/tootmine/
10. https://ektaco.ee/helpcenter/compucash5/laohaldus/inventuur/
11. https://ektaco.ee/helpcenter/compucash5/laohaldus/taastellimine-ja-taastellimise-piirid/
12. https://ektaco.ee/helpcenter/compucash5/laohaldus/ladude-vahelised-liikumised/
13. https://ektaco.ee/helpcenter/compucash5/laohaldus/mahakandmine/
14. https://ektaco.ee/helpcenter/compucash5/muuk/arved/
15. https://ektaco.ee/helpcenter/compucash5/muuk/hinnakirjad/
16. https://ektaco.ee/helpcenter/compucash5/muuk/soodustused/
17. https://ektaco.ee/helpcenter/compucash5/seaded/kds-ja-koogiprinterid/
18. https://ektaco.ee/helpcenter/compucash5/seaded/kassa-seadistused/
19. https://ektaco.ee/helpcenter/compucash5/seaded/saaliplaanid/
20. https://ektaco.ee/helpcenter/compucash5/personal/kasutajad/
21. https://ektaco.ee/helpcenter/compucash5/liidesed/wolt/
22. https://ektaco.ee/helpcenter/compucash5/liidesed/merit-aktiva/

**Repo evidence:** https://github.com/Erkki144/axar-network/pull/63 and https://github.com/Erkki144/axar-network/issues/60 ; newer P0-2/P0-3 PRs #64/#65 require separate review.
