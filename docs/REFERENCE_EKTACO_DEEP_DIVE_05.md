# AXAR ONE — Ektaco CC5 süvauuring V: kogu avaliku indeksi kontroll, QR-kanalid, kinkekaardi elutsükkel, hinnapoliitika, aruandlus ja läbiv testimiskohustus

**Kuupäev:** 2026-11-26. **Uuringu ulatus:** Ektaco ametliku CompuCash 5 indeksi 64 linki + CC5 kassa indeksi 7 linki avati. Masinkontrollitav nimekiri: docs/ektaco/CC5_SOURCE_COVERAGE.json. Kõigi indeksi linkide avamine **ei tõenda**, et iga GIF-kaader, video, suletud API või päris makse-/riistvaraliides oleks läbi testitud. Iga alljärgnev fakt on seotud algallika lingiga.

Legend: **[E]** Ektaco avalik dokument; **[A]** AXAR ONE kavandatav tööreegel; **[C]** eelnevates dokumentides sõltumatult kontrollitud AXAR main-haru kood; **[T]** täpne automaattest. CC4000, CC Retail ja CC5 on eri teostused — sama kontori kasutamine ei tõenda ühesugust kassakäitumist.

## 1. Kahel „Veebimenüüd“ lehel on eri äriprotsessid

[E] URL /tootehaldus/veebimenuud/ käsitleb Bolt/Wolt pakkumisi, menüü importi, toodete järjekorda, lisandeid, kanalihindu, integratsioonide sihtmüügikohti ja saadavuse automaatset värskendamist. Kui menüüd hallatakse CompuCashist, võivad väliste keskkondade iseseisvad menüümuudatused olla lukus.
[E] URL /tootehaldus/veebimuuk/ (kuigi pealkiri samuti „Veebimenüüd“) käsitleb Tellikohe.ee veebist ettetellimist, QR-koodiga lauast müüki ja ainult menüü kuvamist; „Müügi tüüp“ peab eristama Veebimüük / Lauast müük; tellimustel müügipunkt, keskmine valmistusaeg ning võime **peatada QR-tellimused ülekoormuse korral**.

[A] Ära sega kolme erinevat mudelit:
1. `ChannelMenuPublication` koos hinna/keele/pildi ja sihtkanali versiooniga; välise kanali ACK ja content ownership.
2. `GuestOrderIngress` (QR Lauast / Ette tellitud / delivery marketplace) koos teenindusviisi, müügikoha, makse saaja ja laua või pickup aja näitajaga.
3. `OrderIntakeCapacity`: OPEN | PAUSED_MANUALLY | PAUSED_AT_CAPACITY | CLOSED_SCHEDULED; paus peatab **uued** tellimused, mitte juba vastu võetud köögitöö ega raha tagastamise võimalus. Vali täpne service mode ja selgitus ning kanali avaldamis-ACK.
[T] QR peatus tipptunnil: 25 already-accepted orders persist; 26th receives clear unavailable/try-later; no charge without accepted order; restart reconnect keeps paused state; Wolt is independently configured and does not accidentally pause. Two websites with same external SKU do not share order IDs unless explicitly mapped.
Allikad: https://ektaco.ee/helpcenter/compucash5/tootehaldus/veebimenuud/ ; https://ektaco.ee/helpcenter/compucash5/tootehaldus/veebimuuk/

## 2. Kinkekaardi elutsükkel on rahaline kohustus, mitte kupong

[E] Kinkekaardil võib olla number, kehtivuse algus/lõpp, kasutuskordade arv, vaikimisi väärtus, osaline lunastamine ja korduskasutus. Generaatoriga loodud ning trükikojale saadetud EAN-koodid **ei ole aktiveeritud**; aktiveerimine järgneb müügile. CC5 saab kaarti kontrollida/lunastada, kontorist kontrollitakse väljastatud kaartide kasutust ja aegumist. Digitaalse kaardi puhul sama kaart on Apple/Google Walletis ning saldot värskendatakse; saatmine võib olla kohene või ajastatud.
[E] „Lihtne“ reegliteta kinkekaarditoode võib juhendi järgi toimida ilma numbrita ja saldo-/kasutusreeglita. See on kasutuskogemuslik erinevus, mida AXAR EI PEA turvariski tõttu üle võtma.
[A] `GiftCardToken` vs `GiftCardAccount` vs `GiftCardLiabilityLedger` vs `GiftCardWalletProjection`. Token states PREPRINTED → ISSUED_UNFUNDED → ACTIVATED_FUNDED → PARTLY_REDEEMED → EXHAUSTED/EXPIRED/SUSPENDED. A token code is NOT an asset without funding. 'Reusable' card means same token can accept a **new authorized load**; exhausted card cannot silently regenerate purchasing power.
[A] Kontrollitav rahaline saldo = successful funds loaded - confirmed redemptions - authorized refunds/adjustments. Redeem reserve/commit is atomic across all tills. Expiration/unused liability and tax accounting handled with accountant-approved policy; never treat issuing face value and later redeeming it as two ordinary product sales.
[T] 500 preprinted codes → 0 spendable until sold. One card loaded 50€, redeemed 20€, remaining 30€; same 40€ offline command from two tills cannot create negative balance. Wallet email delivery failure never invalidates funded card. Card renew after spent requires new load, not arbitrary balance reset. CompuCash provider rate limits/mall quotas not generalized as AXAR requirements.
Allikad: https://ektaco.ee/helpcenter/compucash5/tootehaldus/kinkekaardid/ ; https://ektaco.ee/helpcenter/compucash5/tootehaldus/digitaalne-kinkekaart/

## 3. Soodustuste konfliktid ja „kehtib kõigile“ risk on süsteemsed

[E] Ektaco automaatseid soodustusi saab piirata ajaga, ostusummaga, ostukogusega, müügipunktiga, tootega ja kliendikaardiga. Kui „Kehtib koos teiste soodustustega“ pole märgitud, kirjeldab juhend kliendile soodsaima soodustuse valimist arve tasemel; valitud üherea soodustus võib käituda teisiti. Soodustust saab määrata uutele tulevastele toodetele ja uutele klientidele automaatselt. Õiguste rühma tühjaks jätmine võib tähendada piirangute puudumist.
[E] Sooduskoodiga automaatne allahindlus vajab „Kehtib ainult koos sooduskoodiga“ sätet; vastasel korral võib see hakata kõikidele arvetele rakenduma.
[A] `PromotionScope` peab sisaldama eksplitsiitselt enum ALL_PRODUCTS / SELECTED_PRODUCTS / ALL_NEW_PRODUCTS, ja ALL_CUSTOMERS/SELECTED_CUSTOMERS, authorized_roles, outlets, validity. Tühi valik ei ole autoriseering.
[A] Versioned deterministic `PromotionEvaluator` koos discount budget, VAT allocation, compounding precedence, same line one-time apply, cashback/points separate. Eelvaade enne publish'i: affected SKUs, outlets, existing/new customers, maximum financial impact; admin kinnitab suure ulatuse.
[T] Loo kupong -2€ ainult koodiga; ilma koodita arve jääb tavahinnaga, ühes kassas 2x redeem ei suurenda soodustust; uus toode ei saa soodustust ilma explicit ALL_NEW valikuta; „stacking=false“ valib ühe korrektselt soodsama arvepõhise tulemuse.
Allikad: https://ektaco.ee/helpcenter/compucash5/muuk/soodustused/ ; https://ektaco.ee/helpcenter/compucash5/muuk/sooduskoodid/

## 4. Hinnakirja kopeerimine/taastamine ei tohi muuta kinnitatud müüki

[E] CC5 hinnakirjal on müügipunkt ja ajavahemik; hinnad saab massiliselt arvutada olemasolevate laokulude/kasumimarginaali järgi, kopeerida teise hinnakirja, kustutada filtriga ja taastada eelmisest tegevusest. Kopeerimise reegel võib kaasa võtta ka **tühjad hinnalahtrid** ja seega sihthinnad kustutada. Juhendi järgi on arvestuse alus eri ladude keskmine omahind.
[A] `PriceBulkJob` on (SOURCE_VERSION, TARGET, SCOPE_PREVIEW, MAPPING, APPROVAL, EFFECTIVE_DATE, APPLY, UNDO_AS_NEW_VERSION). Null/empty/missing säilitavad eraldi tähendused. Mass-importi, hinnakirja kopeerimist ja taastamist tuleb auditeerida ühe üldise bulk-operation engine'iga. Viimati avaldatud hinnad säilivad offline-seadme snapshotis, kuni uus kontrollitud versioon on ACK-itud.
[A] Hinnakirja 'Taasta' on vastupidine uus hindade versioon, **mitte eilsete sale_line snapshot'ide muutmine**. Ettevõtte erineva lao COGS võib olla muu kui kaupade globaalne keskmine – marginaalid olgu ladu/outlet täpsed.
[T] 500 kauba hulgihinna muutus, 2 kauba nulliks muutmise risk; preview näitab kõiki, cashier müüb keset avaldamist → üks terviklik kehtiv price version, varasema tehingu hind muutumatu. Rollback loob auditiga uue event'i.
Allikas: https://ektaco.ee/helpcenter/compucash5/muuk/hinnakirjad/

## 5. AI ostuarve automaatne salvestus ei ole automaatne vastuvõtukanne

[E] Ostuarvete failist importimisel võib kasutaja märkida „Ära näita seda akent enam ja loo ostuarve automaatselt selle tarnija puhul“ – see **jätab ära järgmiste arvete inimese andmete-kontrolli akna**. Ka siis vajab arve esialgsel tarnija toote kaardistamisel laoartiklite sidumist ja ostuarve aktsepteerimist. Paki teisendus 0,5 kg / pakend on juhendis selge näide.
[A] `SupplierImportProfile`: automaatne PARSE ja DRAFT ainult kontrollitud tarnijate ning kokkulepitud e-arve/XML vormingute puhul. **Kunagi automaatselt POST laokandeid ainult OCR-confidence järgi.** Võrdle originaalsummasid, käibemaksu, müügihinda, ühikuid, tarnija SKU ja vastuvõetud koguseid. Human approval if total mismatch, unexpected supplier SKU or unit ratio.
[T] Tarnija auto-save opt-in + tarnija muudab 500g paki 400g peale: sama vananenud mapping ei tohi laoseisu üle arvestada, review required; dokument draft ilma stock/liability mutation'ita.
Allikas: https://ektaco.ee/helpcenter/compucash5/laohaldus/ostuarvete-import-failist/

## 6. Inventuur vs üherealine korrigeerimine vs ajalooline laoseis

[E] Ektaco toote üksikkorrigeerimine loob inventuuri **praeguse laoseisu ajahetkega**. Avalik juhend hoiatab, et kui suurem inventuur on juba füüsiliselt loetud, kuid veel sisestamata, võib uus mini-inventuur takistada vanema inventuuri aktsepteerimist. Laoseisus saab vaadata ajaloolist kuupäeva/kellaaega ning positiivset, nullilist või negatiivset laoseisu.
[E] Laost nulljäägiga toote eemaldamise tegevus ei tähenda, et toote peakataloogi identiteet kaob; järgmine sisseost võib selle lattu tagasi tuua.
[A] `InventoryCountCutoff`: ettevõte/ladu/count_scope/start/read_at/posted_at; stale count is either explicitly rebased, rejected or reviewed, never silently overwrites new transactions. One-item and whole-warehouse counts run through the SAME invariants and lock range.
[A] `StockItemVisibleInWarehouse` is a projection not product identity; hiding zero balance may not delete audit and previous source document.
[T] Full stock count at 10:00 100 items; 10:15 single-item correction; 11:00 full count posted. No duplicate adjustments or retroactive lost 10:15 movement; user sees exact conflict, correct cutoff, final stock ledger matches.
Allikad: https://ektaco.ee/helpcenter/compucash5/tootehaldus/tooted/ ; https://ektaco.ee/helpcenter/compucash5/laohaldus/laoseis/ ; https://ektaco.ee/helpcenter/compucash5/laohaldus/inventuur/

## 7. Tšekimall, pimekassa, kassapõhised maksepiirangud

[E] Kassa seadistused eristavad makseviisi täielikku keelamist ja ainult osalise makse keelamist iga kassa puhul. Kviitungimalli plokid ja printeripõhine laiuse valik, kordustrükk, samuti „pimekassa“ (teenindaja ei näe sulgemise summasid) ja automaatne arvete jagamine. Selliste muudatuste järel tuleb CC5 kassas seadistust värskendada.
[A] `RegisterPolicy` immutable version: allowed_tenders, partial_tender_rules, blind_cashier_flag, receipt_template_version, receipt device capabilities, order sharing, table_required, shift preferences, publish ACK. Peidetud summa peab olema peidetud ka API payloadis, mida kassapidaja tohib näha, mitte ainult UI CSS-iga.
[T] Kassa A keelab 10€ osalise Wolt-pseudo tender'i, B lubab; cashier cannot fetch blind drawer totals via API; manager can; printer 42/48 glyph wrapping; new config remains pending until device ACK.
Allikas: https://ektaco.ee/helpcenter/compucash5/seaded/kassa-seadistused/

## 8. Esmaseadistuse aktiveerimiskood ja kliendiekraanide identiteet

[E] CC5 nutiseadme aktiveerimine käib CompuCash POS äpi ja kontoris müügipunkti/kassa jaoks genereeritud koodi kaudu. QDS kliendiekraan seotakse müügipunkti QDS seadmega; ühe seadme kasutamine teises brauseris katkestab esimese ühenduse; browser-local re-pairing võib kaduda pärast andmete kustutamist.
[A] `DeviceEnrollmentChallenge` ühekordne, aeguv, company/outlet/register-bound, revoke+audit; pairing must not expose administrator secrets or shared bearer tokens. QDS display lease ja guest-visible projection on eraldi POS tilli offline journal'ist. Kassa asendamisel tuleb vana token tühistada ja viimased tehingud keskregistriga sobitada.
[T] Võõra müügikoha aktiveerimiskood B ei saa paaritada A kassat; 2× pair same code fails; QDS eelmise brauseri lease expires, new screen has last agreed display state.
Allikad: https://ektaco.ee/helpcenter/kassa/cc5-kassa/cc5-kassa-aktiveerimine/ ; https://ektaco.ee/helpcenter/compucash5/liidesed/qds/

## 9. Kriitilised omadused mis on juba AXAR main-is ainult osaliselt

[C] Eraldi kontrollitud eelnevates uuringutes:
- `inventory.consume_for_sale`: üks register default warehouse kogu müügile, puudub toote/ingredient-specific source routing.
- `services._expected_cash_expr`: opening_float + cash_sales - cash_refunds; puudub mittemüügilise sularaha IN/OUT käsitlemine.
- `models.Product`: SKU, name, price, tax category, category, active, stock mode; kontori CRUD API ja pub/sub / menüüversioon pole main-is kõigi kasutusjuhtude jaoks tõendatud.
- Ektaco sarnaste külalise QR, telerimenüü, kinkekaardi liability ledger, soodustuste policy engine, QDS, e-arve ACK, 10-outlet riistvaraproof ja offline portable install läbiv testimine on ##60 PR #63 inventuuris puudu või poolik.
**Täpsus:** PR #63–#66 on vahepeal avatud ning võivad mõnda main-i lünka täita oma harus. Ükski nimetatud uuring ei asenda nende CI/API-kontrolli.

## 10. Uued integratsiooniservad E35–E44

| Edge | Ärisündmus ja otsustav invariant | Prioriteet |
| --- | --- | --- |
| E35 | Channel capacity state → QR incoming orders; 0 order after pause unless already accepted; no stranded paid order | P1 #18,#40 |
| E36 | Preprinted gift token → paid activation → liability ledger → redemption → wallet projection; 0 spend before activation | P1 #18,#32 |
| E37 | Promotion rule/version → checkout price and discount → sale snapshot; no accidental all-products auto-discount | P1 #40,#61 |
| E38 | PriceBulkJob → pricebook version → menu publish/ACK → cashier price snapshot; undo only as new version | P1 #40,#61 |
| E39 | Supplier import auto-draft → SKU/units review → posted receipt; stale 500g→400g mapping blocked | P1 #14 |
| E40 | StockCountCutoff + mini-count → stock ledger + historical projection; no overlapping count loss | P1 #14 |
| E41 | RegisterPolicy blind cashier + per-terminal tender permissions → API authorization; no unauthorized financial total disclosure | P1 #34,#27 |
| E42 | Device enrollment challenge → outlet/device auth + revoke + display lease; one-time proof and no cross-tenant misuse | P1 #25,#34 |
| E43 | Product master/global retsept component replace → versioned recipes + future sales; no retroactive edit | P1 #38,#14 |
| E44 | QR/service-mode product availability → offered menu; pause vs out-of-stock vs scheduled closed are distinct state | P1 #18,#40 |

## 11. Üheselt mõõdetav VALMIMISDEFINITSIOON

1. Allika register: every CC5 public index link accounted for, distinct channel/manual versions, critical screen descriptions linked. 64+7 links, 71 unique.
2. Äritegevuse olekumudel: every business action lists authority, tenancy/actor, preconditions, write transaction and immutable document snapshot, derived events, idempotency and failure.
3. Contract/e2e: each E1–E44 mapped to code module or MISSING; every INTEGRATED edge backed by API/browser or hardware test, and ledger recon invariant where financial/stock impact exists.
4. Zoo 10-outlet pilot: one location cash safe drops, one batch-produced item transferred, distinct physical bar/food warehouses, QR pause at rush, 3 concurrent gift card redemptions, duplicate marketplace webhook, sale reversal/credit/refund with correct VAT, 72h offline recovery, 100% finance/stock reconciliation.
5. Real-world gates: printer/terminal provider and actual device test, secure TLS hosting, off-site backups and restore drill, accountant-approved VAT/receipt policy, user-centered cashier usability and accessibility validation, no data loss/duplicate charges.
6. Human approvals: GitHub draft PR may propose changes; main merge, deploy, purchase, live bank transaction and production data change require owner's explicit separate approval. Do not claim all changes implemented when only requirements, documents or mocked tests exist.

## 12. Peamised ülevaadatud ametlikud viited

- https://ektaco.ee/helpcenter/compucash5/ (64 kontori jaotise linki)
- https://ektaco.ee/helpcenter/kassa/cc5-kassa/ (7 CC5 kassa jaotise linki)
- https://ektaco.ee/helpcenter/compucash5/tootehaldus/veebimenuud/
- https://ektaco.ee/helpcenter/compucash5/tootehaldus/veebimuuk/
- https://ektaco.ee/helpcenter/compucash5/tootehaldus/kinkekaardid/
- https://ektaco.ee/helpcenter/compucash5/tootehaldus/digitaalne-kinkekaart/
- https://ektaco.ee/helpcenter/compucash5/muuk/soodustused/
- https://ektaco.ee/helpcenter/compucash5/muuk/sooduskoodid/
- https://ektaco.ee/helpcenter/compucash5/muuk/hinnakirjad/
- https://ektaco.ee/helpcenter/compucash5/laohaldus/ostuarvete-import-failist/
- https://ektaco.ee/helpcenter/compucash5/laohaldus/laoseis/
- https://ektaco.ee/helpcenter/compucash5/tootehaldus/tooted/
- https://ektaco.ee/helpcenter/compucash5/seaded/kassa-seadistused/
- https://ektaco.ee/helpcenter/kassa/cc5-kassa/cc5-kassa-aktiveerimine/
- https://ektaco.ee/helpcenter/compucash5/liidesed/qds/

**Täielik linkide kataloog ja masinvalideerimise skript:** docs/ektaco/CC5_SOURCE_COVERAGE.json ja scripts/validate_ektaco_coverage.py.
