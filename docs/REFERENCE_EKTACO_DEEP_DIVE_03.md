# AXAR ONE: Ektaco CC5 süvauuring III — laoallikas, ohutu import, maksud, e-arved, hooajakassad ja aruanded

Koostatud 2026-11-26. Seotud #60, PR #63, #14, #27, #40, #61, #4, #32, #34. See on uurimis- ja testispetsifikatsioon, mitte valminud AXARi funktsioon. [EK] = Ektaco avalikus juhendis nähtud käitumine; [KOOD] = AXAR main lähtekoodi kontroll; [AX] = kavandatav lahendus; [TEST] = kontrollitav nõue. CC4000 Windowsi ja CC5 veebikontori erinevused on olulised: kõiki varasema kassamudeli võimalusi ei saa automaatselt omistada CC5 kassale.

## 1. Toote/müügipunkti laoallika tegelik tähendus

[EK] Ektaco Ladu–Toode: iga müügipunkti jaoks saab toodetele määrata eraldi lao; eriseos on ülimuslik müügipunkti vaikimisi lao suhtes. Baaritooted võivad minna baarilaost, köögitooted köögilaost. Hulgitoimingud lisavad, asendavad või eemaldavad neid eriseoseid.
[KOOD] AXAR main failis products/axar-pos/axar_pos/inventory.py, funktsioonis consume_for_sale, otsitakse Warehouse, mille location_id = reg.location_id ja is_default = True. Kogu müügiridade plaani tarbimine kasutab sama wh.id. Toote/komponendi lao override pole selles funktsioonis implementeeritud. Kontrollitud vastuolu Ektaco võimekuse ja AXAR praeguse teostuse vahel.
[AX] Lisada StockSourceRule(legal_entity,site,outlet,product_or_ingredient,warehouse,priority,effective_from,to,approved_by). Allikavaliku erand: müügirea kinnitamisel fikseeritakse tegelik warehouse_id igal laokandel. Puuduva seadistuse korral tõrge/selge läbivaatus, mitte vaikne vale ladu. Retsept võib kasutada mitut ladu; tootereegli ja koostisosareegli prioriteet peab olema täpne. Samaaegsete tehingute lukud kindlas järjestuses. Menüü nähtavus ja füüsiline ladu ei ole sama asi.
[TEST] Ühe kassa sama ostukorv: jook baarilaost, burger köögilaost, teenus ilma laota; COGS ja StockMovement warehouse_id võrduvad ootusega. Hilinenud uuestisaatmine ei tekita topeltkulu. Samal ostukorvil kaks retseptikomponenti kahest laost. Konfiguratsiooni muutus ei kirjuta ümber vana müüki.
Allikas: https://ektaco.ee/helpcenter/compucash5/laohaldus/ladu-toode/

## 2. Massimport on andmete muutmise käsk, mitte süütu faililaadimine

[EK] Toodete import: CSV identifikaatorina vöötkood või tootenumber, kohustuslik tootenimi. Eraldi valikud uute aktiveerimiseks, olemasolevate uuendamiseks, hinna muutmiseks, deaktiveeritud aktiveerimiseks ja täidetud väärtuste asendamiseks tühjadega. CSV saab lisada tooterühma ja tarnija seose; impordiprofiil salvestub.
[EK] CRM import: eri jao era-/äriklientide jaoks; valitav identifikaator, soodustused ja ettevõtte töötajaseos; olemasoleva andmestiku ülekirjutamine ja deaktiveeritud kliendi aktiveerimine võivad olla lubatud. Kliendihaldus eristab erakliendi boonuseid ja krediiti ärikliendi maksetähtaegadest.
[AX] ImportJob: UPLOADED → PARSED → VALIDATED → DIFF_READY → APPROVED → APPLYING → COMPLETED/PARTIAL/FAILED. Reeglid: puuduv tulp != tühi lahter != NULL != kustutuskäsk. Vaikimisi ei kustuta andmeid, ei aktiveeri, ei muuda müügihindu. Eelvaates: muudetavate toodete arv, esialgne/vana hind, sihtmüügikohad, kliendi isikuandmed, ebaselged seosed. Seadistuse salvestatud profiil peab olema versioonitud. Audit: faili räsi, isik, kinnitaja, muudatuse diff, lõplikud tulemused.
[AX] Isikukood võib olla välise andmeallika otsinguväli, kuid AXARi sisemine ID olgu oma genereeritud võtmega. Isikuandmete nõusolekut, turundusluba ega ligipääsu ei tohi CSV abil automaatselt luua või taasaktiveerida.
[TEST] Sama fail 3x → sama lõpptulemus, üks kontrollitud import. Puuduv hinna tulp säilitab hinna, tühi hinnaväli annab kontrolli; 1000 kirjega CSV 30 vigase reaga tagastab täpse aruande. Deaktiveeritud kliendi või toote taasaktiveerimine nõuab eraldi volitust. Ükski toote import ei avalda muudatust POS-i enne Menu Studio publish'i.
Allikad: https://ektaco.ee/helpcenter/compucash5/tootehaldus/toodete-import/ ; https://ektaco.ee/helpcenter/compucash5/kliendihaldus/crm-import/ ; https://ektaco.ee/helpcenter/compucash5/kliendihaldus/kliendid/

## 3. Seismajäänud laokaup ja saadavuse-põhine müügikiirus

[EK] Ektaco seismajäänud kauba aruanne: filtrid müügianalüüsi algus, laovaru päevades, „Saadaval olnud päevi rohkem kui“, ladu, rühm, kogus; kuvab päevamüüki, seismajääva varu ja väärtuse. Kauba saab massiliselt märgistada ja märgise alusel soodustusega siduda.
[AX] Kaks eraldi suurust: observed sales velocity = müüdud ühikud / varuga päevi (mitte kogu kalendriaeg) ja stock cover = jääk / eeldatav päevamüük. Eraldi signaalid DeadStockRisk ja ExpiryRisk. Soodustus või mahakandmine ei teki riskimärgisest automaatselt. AI võib teha põhjendatud ettepaneku koos hinnapõranda ja müügiprognoosiga.
[TEST] 30 päeva, toode saadaval 10 päeva, müüdi 100 ühikut → rate 10 tk/saadavuspäev, mitte 3,33 tk/kalendripäev; märgis ei muuda ladu ega hinda enne heakskiitu.
Allikas: https://ektaco.ee/helpcenter/compucash5/laohaldus/seismajaanud-toodete-laoseisuaruanne/

## 4. Tooteajalugu ning paralleelsed lao- ja finantsvaated

[EK] Ektaco Tooteajalugu näitab konkreetse artikli iga liikumist koos kuupäeva, dokumendinumbri, programmi kasutaja, järgse laoseisu ja laohinnaga; filtreeritakse lao ja toimingu järgi. Lao koondaruannet saab võtta koguses või väärtuses.
[AX] Üks append-only StockLedger ning eraldi read-modelid StockCard, QuantityMovementReport, ValueMovementReport. Näita nii tehingu tegelikku aega kui tagantjärele sisestamise aega. Ajaloolist hinda korrigeeritakse nähtava kompensatsioonikandega.
[TEST] 10 sisse, 3 müüki, 2 ülekanne välja ja 1 inventuuri vahe → 4 lõppjääk; väärtusliku korrigeerimisega kogus ei muutu.
Allikad: https://ektaco.ee/helpcenter/compucash5/laohaldus/tooteajalugu/ ; https://ektaco.ee/helpcenter/compucash5/laohaldus/lao-koondaruanne/

## 5. Käibemaksu muutus hõlmab POSi, kontorit ja raamatupidamist samaaegselt

[EK] Ektaco 2025 juhend näitab standardmäära 22%→24% muutmist hinnakirja vaates ning eraldi toodete käibemaksumäära muutmist. Merit Aktivas tuleb vastavad maksuartiklid luua iga müügipunkti müügigrupi ning iga lao ostugrupi jaoks; Directo/E-Invoice kasutavad eraldi ekspordi liikide ja kontode seoseid. Need on ajaloolised näited, mitte tänase maksumäära allikas.
[AX] TaxChangePlan: effective_at, TaxCategoryVersion, PriceBookVersion, outlet/warehouse mapping, POS/TV/offline rollout, AccountingTaxMapping, EInvoiceTaxCode. Kõik nõutud sihtsüsteemide seosed peavad olema kinnitatud enne jõustumist; puuduv mapping näidatakse veana, mitte ei muudeta vaikimisi maksumäära. Bruto müügihinna säilitamine või muutmine on eraldi äriline otsus. Varasema müügi tagastamisel kasutatakse õiget algset maksukäsitlust ja raamatupidaja kinnitatud reegleid.
[TEST] Maksumäära kehtivusaja vahetus 00:00, võrguühenduseta kassa hiljem sünkroonib vanad ostud, vana müügi tagastus, puuduv ühe lao ekspordikood: historics unchanged, puuduv mapping blocks/flags.
Allikad:
- https://ektaco.ee/helpcenter/compucash5/kaibemaksu-muudatus/kaibemaksu-standardmaara-muutmine-compucashis/
- https://ektaco.ee/helpcenter/compucash5/kaibemaksu-muudatus/toote-kaibemaksu-muutmine-compucashis/
- https://ektaco.ee/helpcenter/compucash5/kaibemaksu-muudatus/kaibemaksu-muutmine-merit-aktiva-liideses/
- https://ektaco.ee/helpcenter/compucash5/kaibemaksu-muudatus/kaibemaksu-muutmine-rmp-liideses-directo-e-invoice/

## 6. E-arve saatmine on olekumasin, mitte üks „Saada“ nupp

[EK] Ektaco RIK liidese kasutuselevõtt nõuab nende poolelt aktiveerimist ja lepingut läbi Ektaco veebikontori. Arvet saab valida ja saata e-arvena ning näidata „E-arve saadetud“ veergu. Ebaõnnestumisel kontrollitakse kliendi registrikoodi ja e-arvete vastuvõtmise võimalust. Juhend ei avalda sisemise edastussüsteemi ACK-semantikat.
[AX] TransmissionState: READY → QUEUED → SUBMITTED → ACCEPTED → DELIVERED (ainult kui pakkuja kinnitab) | FAILED_RETRY | REJECTED. Erista API vastus, pakkuja aktsepteering ja saaja kohaletoimetamine. Vead nähtavad juhile. Duplikaatkaitse invoice UUID ja provider transaction ID alusel.
[TEST] Saatmine 3 korda pärast timeout'i: üks originaalarve; vale reg kood ja kättesaamatu saaja tuleb selgelt välja.
Allikas: https://ektaco.ee/helpcenter/compucash5/liidesed/rik-e-arveldaja/

## 7. Hooajaliselt avatavad kassad, et offline raha ei kaoks

[EK] Ektaco veebikontoris saab kassalitsentse aktiveerida/deaktiveerida hooajaliselt; see mõjutab sisselogimise võimalust ja pakkuja arveldamist. Pildil on seadmeliik, olek, viimane ühendus. Hinnastuse ärimudelit ei ole vaja AXARile kopeerida.
[AX] RegisterLifecycle: CREATED → PREPARED → ACTIVE → SUSPENDED → OFFBOARDED. Suspend ei ole DELETE. Enne sulgemist kontrolli lahendamata device journal'i, sünkroonimist, viimast vahetust, kassa rahajääki ja receipt counter'it. Varastatud seadmel tühista võtmed, säilita tõendid ja offline konfliktikäsitlus.
[TEST] Kümme müügipunkti ja kolm hooajal suletavat kassat, ühes üks sünkroonimata müük: süsteem peab blokeerima või selgelt eskaleerima sulgemise, tehing ei kao. Järgmisel hooajal tšekinumbrite kordumist ei teki.
Allikas: https://ektaco.ee/helpcenter/compucash5/seaded/kassa-litsentside-haldus/

## 8. Aruanded = ühiste mõõdikute erinevad vaated

[EK] Makseviiside aruandes saab vaadata mitme makseviisiga arveid ja maksesummade erinevusi. Vahetuste aruanne eristab kassavahetust ja kasutaja vahetust ning kirjeldab ühise kassa rahasahtli vastutust; müügi on teinud konkreetsed kasutajad. Soodusmüügi aruandes on filtreerimine ja ajalõik; statistika raportis mõõdikud, Lõikes-grupeerimine ja eksport. Laokoondis kogus ja väärtus.
[AX] MetricDictionary: net_sales, gross_sales, VAT, discount, void, refund, settled_tender, receivable, prepayment, COGS. Dimensions: legal entity, business date, outlet, register, employee, warehouse, channel, tax category, promotion. Split tender ei dubleeri käivet; employee shift != drawer custody.
[TEST] 30€ arve: 10 sularaha, 10 kaart, 10 pangaülekande nõue → üks 30€ müük, 20€ kohe laekunud (card only if approved), 10€ avatud nõue; personali ja kassasahtli koondid on eri mõõtmed.
Allikad:
- https://ektaco.ee/helpcenter/compucash5/muuk/makseviiside-aruanne/
- https://ektaco.ee/helpcenter/compucash5/muuk/vahetuste-aruanne/
- https://ektaco.ee/helpcenter/compucash5/muuk/soodusmuugi-aruanne/
- https://ektaco.ee/helpcenter/compucash5/muuk/statistika-raport/

## 9. Kliendikaardi ja kliendiandmete turvapiirid

[EK] Kliendihalduses on era- ja ärikliendid eri väljade ja reeglitega: erakliendi soodustuste prioriteedid ja kaardirežiimid, ärikliendi hinnaklass/maksetähtaeg ning kassas nähtavus; massiline soodustuste määramine ja kassas nähtavad väljad.
[AX] Person, BusinessCustomer, CustomerAccount, Loyalty, DiscountAssignment, MarketingConsent on eri registrid. Andmete peitmine kassas ei asenda API õiguste kontrolli. Massmuudatusel eelvaade, kinnitaja ja audit.
[TEST] Varasem soodustus aegub ja järgmine prioriteet rakendub. Kassapidaja ei saa API kaudu privaatset isikuvälja isegi siis, kui tal on kliendiotsingu õigus.
Allikas: https://ektaco.ee/helpcenter/compucash5/kliendihaldus/kliendid/

## 10. Nähtud originaalpildid ja allikate piirid

- https://ektaco.ee/wp-content/uploads/2023/09/chrome-mmwwbaaqrp-522x860.png — Ladu–Toode filter, „Müügipunkt“ ja „Vaikimisi ladu“.
- https://ektaco.ee/wp-content/uploads/2024/09/kihqej5nc2-516x860.png — seismajäänud kauba filter (müügianalüüs, varu päevades, saadavuspäevad).
- https://ektaco.ee/wp-content/uploads/2024/09/chrome-oahjjg90di-860x134.png — hulgivalikut toetav riskikauba tabel.
- https://ektaco.ee/wp-content/uploads/2026/03/260309-10-34-49-860x369.png — hooajalise kassa aktiveerimise olek, tüüp, viimane ühendus; ekraanil olev hind pole tänase pakkumise tõend.
- https://ektaco.ee/wp-content/uploads/2025/07/image-1-13.png — Merit KM müügipunkt, KM protsent ja artiklikood.
Mitmed importi ja hulgitoimingut kujutavad GIF-id ei avanenud veebivaatlejas; nende kaadrite kohta eraldi pildipõhist väidet ei tehta.

## 11. Uued #60 lõimumisservad (projekt, mitte valmis teostus)

| ID | Edge | Invariant | Väärtus / prioriteet |
| --- | --- | --- | --- |
| E19 | Product / outlet / ingredient → warehouse source → stock.consume | 0 müüki valest laost, 0 topeltkulu | P1, #14/#27 |
| E20 | CSV / CRM import preview → manager approval → product/client update | absent != empty != null, 0 vaikimisi ohtlikku ülekirjutust | P1, #61 |
| E21 | sell-through velocity → deadstock risk → proposed markdown | tõendatud saadavuspäevad, 0 kinnitamata soodustust | P2, #39/#32 |
| E22 | TaxPolicy → Pricebook + register + accounting export | ajaloolised dokumendid muutumatud, mapping required | P1, #4 |
| E23 | E-invoice → provider → recipient confirmation | one invoice, differentiated send/ack/receipt statuses | P2, #4/#8 |
| E24 | Seasonal till suspend → sync journal + drawer + device credentials | 0 kaotatud offline tehingut | P1, #29/#34 |
| E25 | Register / employee / tender / inventory → analytics | sama müük loetakse ühe korra, õiged õigused | P1, #32 |
| E26 | CRM bulk changes → loyalty/discount/privacy scopes | 0 vaikimisi uuesti aktiveeritud klienti/turundusluba | P2, #33 |

## 12. Täpsed testid, mille Claude peab ajakavasse lisama

1. WAREHOUSE_SOURCE_ROUTING – eri tooted samal tšekil erinevast laost, retsept, replay, ajalooline source.
2. IMPORT_PREVIEW_AND_REPLAY – CSV puuduv/null/tühi, import idempotency, hinnad ja nõusolekud.
3. TAX_ROLLOVER_EXPORT_ACK – maksu jõustumisaeg, outlet/warehouse mapping, offline and accounting.
4. OFFBOARD_WITH_UNSENT_JOURNAL – hooajaline kassa peatamine ilma sündmusi kaotamata.
5. DEADSTOCK_AVAILABILITY_RATE – müügi päevad vs saadavuspäevad ja soodustuse heakskiit.
6. DRAWER_VS_EMPLOYEE_SHIFT – ühine kassa, kaks teenindajat, mitte topeltkäive.
7. EINVOICE_ACK_DEDUP – RIK provider / recipient progress and retry without duplicate legal invoice.

Claude peab võrdlema neid #60 / PR #63 tegeliku koodimaatriksiga, mitte täitma pimesi dubleerivaid funktsioone. Eraldi väikesed draft PR-id ja testid; main/production/real payments require owner's permission.
