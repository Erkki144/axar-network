# AXAR ONE #68 — teenindaja KOGU tööaken ja tegevuste spetsifikatsioon

**Seis:** 2026-11-26, UX/ärianalüüsi ja API-de võrdlus; ei ole väide, et kõik ekraaninupud on main-harus töötavad.
**Tõendus:** [E] Ektaco ametlik CC5 kassa juhend ja avalik originaalpilt; [X] AXAR main kood (tegelikult kontrollitud); [A] AXAR ONE soovitatud teostus/ekraan; [T] nõutav test. Ektaco CC4000, CC Retail ja CC5 **ei ole identsed**.
**Masinloetav täielik nuppude register:** [CASHIER_ACTION_CATALOG.json](CASHIER_ACTION_CATALOG.json) — 80 tegevust 7 grupis, klassifitseeritud olemasoleva API ja puuduva integratsiooni järgi.

## 0. TÕELINE CC5 ekraan — kus mis asub

[E1] Avatud arvete pildil **vasak sinine külgriba**: Arved, Veebitellimused, KDS, teenindaja ja kassa number, Tegevused, Logi välja; põhialas päis Avatud arved, järjestus, arve kaart (number/aeg/summa/teenindaja), valikute kolme-punkti menüü ja Uus arve.
Originaal: https://ektaco.ee/wp-content/uploads/2021/02/260202-11-53-41-dnplayer-860x484.png

[E2] Avatud arve menüü pildil: Uus arve, Vahearve, Arve tunnus, Saada (võib olla disabled), Soodustused, Jaga arvet, Tühista makse, Käigud kööki, Kinkekaart, Sooduskoodid, Muuda laud, Tühista arve.
Originaal: https://ektaco.ee/wp-content/uploads/2021/02/260202-12-09-04-860x854.png
Märkus: pildil leiduv „Tühista arve“ ei õigusta välja antud finantsdokumendi destruktiivset kustutamist AXARis.

[E3] Arverea detaili pildil: nimetus, kogus -/+, külaline, käik, Jaga rida, Lisa soodustus, Uus ühiku hind, Kustuta rida, Muuda lisandeid, Muuda erisoove. Arverea all võib olla valmistamis- või lisandirida.
Originaal: https://ektaco.ee/wp-content/uploads/2021/02/260130-11-35-32-dnplayer-860x484.png

[E4] Maksmise pildil: vasakul arve read ja maksueristus, keskel makseviisid, paremal tasutava summa sisestamine, sularaha tagasiraha, numbriklahvid, summade otseteed, Kinnita.
Originaal: https://ektaco.ee/wp-content/uploads/2021/02/260202-14-17-16-dnplayer-860x484.png

[E5] Vahetuse pildil: Vahetus / Arvete ajalugu / Makseterminal / Aruanded / Seaded; tegevuskaardid Sularaha sisse, Sularaha välja, Lõpeta teenindaja vahetus ja Lõpeta kassa vahetus.
Originaal: https://ektaco.ee/wp-content/uploads/2021/02/260202-16-44-26-dnplayer-860x460.png

[E6] Arvete ajaloo pildil: lõpetatud tšekk, kuupäev, summa, Prindi tšekk, Tagasta ja ridade detailid.
Originaal: https://ektaco.ee/wp-content/uploads/2021/02/260202-16-45-19-dnplayer-860x461.png

[E7] Terminali pildil: Tühista, Korduskviitung, Saada tehingud, Eelnevad tehingud, Kontrolli ühendust, Seadista parameetrid.
Originaal: https://ektaco.ee/wp-content/uploads/2021/02/260202-16-46-24-dnplayer-860x462.png
[E8] Seadete pildil: Andmed / Rakendus / Riistvara, Värskenda andmeid, Lähtesta arved (risk), Andmete alglaadimine, viimase värskenduse aeg.
Originaal: https://ektaco.ee/wp-content/uploads/2021/02/260202-16-57-48-dnplayer-860x464.png

[E9] CC5 kassapaneelide kontorijuhend: 3 paneelitüüpi — mitu peapaneeli, üks tegevustepaneel, alampaneelid. Nupu tüübid: toode, rühm, tühi paigutuskoht, alampaneel. Nupul võib eraldi olla kuvatav nimi, värv ja pilt. Need on administreeeritavad kontoris, mitte fikseeritud ainult programmis.
Allikas: https://ektaco.ee/helpcenter/compucash5/tootehaldus/kassa-nupud/

## 1. AXAR ONE enda teenindajaliides — terviklik ruumiline paigutus [A]

Põhimõte: **üks püsiv tööruum**, mitte pidev eri vaadete vahel liikumine. Kassapidaja 10 kõige tavalisemat tegevust peavad mahtuma kiiresti kätte. AXARi olemasolev 3-veeruline \`static/one/index.html\` on lähtealus, kuid vajab täiendavaid töörežiime ja olekuid.

Töölaua skeem (1440×900 või suurem; adapteerub 1024/768 ja telefonile):

\`\`\`
┌────────────────────────────────────────────────────────────────────────────────────┐
│ [01] AXAR ◈ | Ettevõte › Zoo › Müügikoht 3 › Kassa K3 | Teenindaja | Kell / päev │
│ [02] SERVER ✓ | SYNC 0 | KÖÖK ✓ | PRINTER ✓ | TERMINAL ? | KASSA AVATUD [!]      │
├─────────────┬───────────────────────────────────────────┬──────────────────────────┤
│ [03] Arved  │ [04] Režiim: KIIRMÜÜK / LAUAD / KAASA     │ [09] ARVE #/LAUD/KÜLALINE │
│ Avatud      │ [05] OTSI / SKÄNNI / LEMMIKUD             │ [10] Rida +/−, lisandid   │
│ Veebitell.  │ [06] Kategooriad / põhimenüü / alammenüü  │ käik, erisoov, soodustus  │
│ Lauad       │ [07] SUURED TOOTENUPUD                    │ [11] KOKKU, KM, ALLAH.    │
│ Köök/KDS    │    Joogid / Söök / Jäätis / Pitsa ...     │ [12] SAADA KÖÖKI          │
│ QDS         │                                           │ [13] MAKSA / JAGA ARVE    │
│ Ajalugu     │ [08] Kiirtoimingud / väljamüüdud kaup     │ [14] Tagasiraha ja maksed │
│ Tegevused   │                                           │                          │
│ Juht/Ladu   │                                           │                          │
│ Lukusta     │                                           │                          │
├─────────────┴───────────────────────────────────────────┴──────────────────────────┤
│ [15] Häire- ja teavitusriba (andmete viimane ACK, ootel arved, printer, offline aeg) │
└────────────────────────────────────────────────────────────────────────────────────┘
\`\`\`

### Täpne tähendus ja käitumine

| Ala | Mida näeb | Kasutaja tegevus | Erand/keeld |
| --- | --- | --- | --- |
| 01 | Ettevõte/objekt/müügikoht/kassa, kasutajanimi, kohalik aeg | kassa valik ainult õigustega, vahetuse/teenindaja vahetus | valesti sidumata seadet ei tohi valida teise firma kassaks |
| 02 | sõltumatud tervised: AXAR server, edge sync, KDS, printer, terminal, kassa | avab probleemide täpse nimekirja | roheline „online“ EI tähenda, et printer või terminal on online |
| 03 | Arved, veebitellimused, lauad, köök, QDS, ajalugu, tegevused, juht, ladu | vahetab töörežiimi | õigusteta funktsioon on põhjendusega peidetud/keelatud, API keeld kinnitab |
| 04 | Kiirmüük / Saal / Kaasa / Ettetellimus / QR | valib töö tüübi | teenuseviis on osa order'i olekust ja võib mõjutada KDS, hindu, makseviise |
| 05 | otsing: nimetus, SKU, triipkood, lemmikud, hiljutised | leiab toote <=3 sekundiga | server price authoritative; skanner ei tohi anda toote ID-d vale tenant'i alt |
| 06 | põhimenüü, kategooria, alampaneel, kõik/tooterühmad | navigeerib ühe puudutusega | kontori menüü publitseerimisversioonilt, mitte demo seed'ist |
| 07 | kujundatavad tootenupud (nimi/pilt/hind/aktiivne/saadavus) | lisab arverea; vajalik modifier avaneb kohe | „välja müüdud“ vs „kanalis peatatud“ vs „suletud“ on eri põhjus |
| 08 | kiirtoimingud: arve tunnus, laua vahetus, vahearve, kliendikaart, voucher | avab alamnupu | ainult õiguste ja aktiivse arve korral |
| 09 | avatud arve number, laua tunnus, teenindaja, shared/locked status, külalised/käigud | uus, jätka, võta üle, anna üle | kaks teenindajat ei saa sama arvet ilma version CAS'ita üle kirjutada |
| 10 | arveread: kogus, nimi, hind, lisandid, erisoovid, käik, külaline, valmistamise olek | +/−, rida jaga, rida tõsta, soodustus, juht erihind | pärast kööki saatmist on muutmine eraldi kompenseeriv töö |
| 11 | brutohind, soodustus, eraldi KM, tip, tagatis, summa ja maksmata jääk | avab hinnaselgituse | server arvutab; varasem tehing snapshot jääb muutumatuks |
| 12 | Kööki saadetud vs saatmata ridade selge arv; KDS ja printer | saada vajalikud read | retry = sama ticket ID; võrgukatkestusel ei tohi näidata „saadetud“ kuni ACK |
| 13 | Maksa, osaliselt maksa, jaga arve, pane taustale | maksekanali valik | makset ei saa uuesti saata kui terminali olek unknown |
| 14 | antud sularaha, tagasiantav, maksete loetelu, maksete olek, ülekanne/kinkekaart | kinnita/otsi probleemne tehing | „kaart makstud“ ainult tegeliku pakkuja vastusega, praegu mock |
| 15 | ootel sündmused, kviitungi print, serveri ja terminali side, tegevusvihjed | avab probleemi üksikasjad | ei tohi lasta kasutajal klõpsata katastroofilisele „lähtesta arved“ käsule ilma ülemkontrollita |

### Eraldi töövaated

**„Arved”**: vasakul filtrid (minu / jagatud / kõik lubatud, lauaga / lauata, võetud üle, avatud/osaliselt tasutud), keskel kaardid (nr, laud, tunnus, summa, serveri versioon, teenindaja, aeg, köögis), paremal arve avamise eelvaade. „Üle võtta“ ja „Jaga“ on päris õiguste+versiooniga käsud, mitte lihtsalt muutus UI olekus.

**„Toote detail”**: ühiku hind, kogus ja -/+, külaline, käik, variant, modifikaator (koguse piirid), erisoov, kulu mõju, soodustus, ühekordne erihind juhataja kinnitusega; kustutamine ainult kinnitamata arverea puhul, muul juhul tühistus/praak.

**„Maksmine”**: arve read, maksta/ostuhind/KM; valik Sularaha/Kaart/Ülekanne/Wolt/Bolt/Kinkekaart/Kliendikrediit (kui tegelikult ühendatud); summa klahvistik, 5/10/20/50 € otseteed, tagasiantav raha, osalise makse reegel, pakkuja olek ja viitenumber, kinnita. Kaardi offline keeld kui terminal seda ei sertifitseeri.

**„Tegevused → Vahetus”**: Sularaha sisse, Sularaha välja/seifi, Läbiv loendus, Lõpeta teenindaja vahetus, Lõpeta kassa vahetus, Loo vahetuse aruanne. Rahaliikumise detailis: suund, summa, põhjus, raha üleandja ja vastuvõtja, praegune kassaoote summa, originaalne tehinguviide, vastutaja kinnitused.

**„Tegevused → Arvete ajalugu”**: tšeki number, aeg, kassa, teenindaja, makseviisid, tooted, KM, printeri/kviitungi PDF/TXT koopia, tagastuse põhjus, rea põhine restock/writeoff, audit.

**„Tegevused → Terminal”**: seadme ID, ühenduse ja provider'i olek, eelmised tehingud, terminali kviitungi kordustrükk, tühistus/tagastus (pakkuja reeglitega), vajadusel batch/settlement. **Päris terminali kuni sandbox/HIL testini mitte kuvada töövalmis.**

**„Tegevused → Seaded/taastamine”**: seadme nimi, menüüversioon, viimane sync, printer, KDS, terminal, offline päeviku olek, turvaline andmete värskendus. Hävitav restart/reset ainult juhi erikinnituse, tehingupäeviku varunduse ja konfliktide lahendamisega.

## 2. Teenindaja rollid ja õigused [A]

| Õigus | Teenindaja | Vahetuse juht | Omanik/admin |
| --- | --- | --- | --- |
| Korv, tavaline müük, enda arved | jah | jah | jah |
| Jagatud arve võtmine | ainult määratud õigusega | jah | jah |
| Kööki saatmine | jah, kui õigus | jah | jah |
| Muuda ühekordset hinda / erisoodustus | juhataja kinnitusega | poliitika järgi | jah |
| Sularaha sisse/välja | algatus koos põhjusega | kinnitab vastavalt limiidile | jah |
| Sulge kassavahetus | jah, kui loendatud ja variance = 0 | kõrvalekaldega | jah |
| Arve tagastus / kreeditarve | algatus | kinnitab sõltuvalt summast | jah |
| Terminali seaded, bulk reset, menüü avaldamine | ei | piiratud | jah |
| Pimekassa puhul oma kassa rahaline summa | ei (serverilt redigeeritud) | jah | jah |
| Juriidilise isiku teiste müügikohtade aruanded | ei | enda määratud piir | õigusega |

Kõik piirangud on API-s jõustatud ja auditiga; nupu peitmine UI-s üksi ei ole turve. Session'i ja offline token'i õigused peavad kehtima õigesti ka pärast töötaja lahkumist.

## 3. 80 nupu täielik register

Tegevuste nimekiri koos UI grupi, praeguse AXAR API, serveriõiguse, offline lubatavuse, teostuse astmega ja riskimärkusega on masinloetavas failis:
\`docs/issue68/CASHIER_ACTION_CATALOG.json\`.

**Klassifikatsiooni võti:** MAIN_UI = AXAR main teenindajaliideses olemas (ei võrdu live hardware); MAIN_API = backend endpoint olemas, kuid UI võib puududa; PARTIAL_MAIN = alamfunktsioon olemas, terviklik reegel veel mitte; MISSING_MAIN = main-is selle voo täielikku teostust ei tõendatud; PROVIDER_DEPENDENT = füüsilise/vendor-liidese kinnitust vaja.

**Läbiv UX reegel:** ühegi töövoo „Kinnita“ ei tee kõrvaltoimet, kui kohaliku seadme ajakava, autoriseeritud server, idempotentsuse võti või eelnev äriseis ei luba. Kui tulemus on teadmata, kuvatakse „Kontrollin tehingu tulemust“ koos püsiva viitega, mitte kohe teine tehing.

## 4. Ekraani olekute ja märgete täielik sõnastik

- **Kassa:** SULETUD / AVATUD / SULGEMINE / VARIANCE_REVIEW.
- **Arve:** OPEN / SHARED / LOCKED_BY_OTHER / KITCHEN_SENT / PART_PAID / PAID / CANCELLED / REVIEW.
- **Köök:** NOT_SENT / QUEUED / ACKED / PREPARING / READY / HANDED_OVER; printerile väljastamine on eraldi delivery attempt.
- **Makse:** UNSTARTED / PENDING / APPROVED / DECLINED / UNKNOWN / CAPTURED / SETTLED / REFUNDED / CHARGEBACK. In UI näita inimesele „Ootel“, „Kinnitatud“, „Tulemus teadmata“, „Laekunud“; ära nimeta APPROVED = SETTLED.
- **Toode:** ACTIVE / SOLD_OUT / PAUSED_CHANNEL / SCHEDULED_OFF / ARCHIVED; müügi ajal snapshot.
- **Offline:** CENTRAL_LOST / LOCAL_LAN_WORKS / DEVICE_JOURNAL_DURABLE / SYNC_PENDING / QUARANTINED / OFFLINE_LIMIT_REACHED; näita tegevusspetsiifilisi piiranguid.
- **Turva:** ROLE_DENIED / NEEDS_MANAGER_APPROVAL / TENANT_SCOPE_DENIED / STALE_MENU / STALE_ORDER_VERSION.

Iga olek toob UI-sse selge edasise sammu. Näiteks „Tulemuseks teadmata“ → kontrolli sama viitenumbri järgi, **mitte** „Proovi uuesti maksta“.

## 5. Seotud koodi audit [X] (main @ 1b7a3af, 2026-11-26)

- \`products/axar-pos/axar_pos/static/one/index.html\`: kolmest piirkonnast koosnev kest, nupud Kiirmüük/Lauad/Köök/Juht/Ladu, ostukorv, kassavalik, võrguoleku märge.
- \`static/one/app.mjs\`: \`renderProductGrid\`, \`renderTables\`, \`renderKitchen\`, \`renderManager\`, \`openDrawerDialog\`, \`closeDrawerDialog\`, \`openPayment\`, \`openBillPayment\`, \`openRefundDialog\`.
- \`api.py\`: \`/drawer/open\`, \`/drawer/{id}/close\`, \`/sales/cash\`, \`/sales/{id}/refunds\`, \`/orders/{id}/payments\`, \`/orders/{id}/kitchen\`, \`/kitchen/tickets\`, \`/events/stream\`, \`/sync/status\`; müügi/raamatupidamise põhiliidesed olemas, kuid ei tõenda kõiki ülalnimetatud nuppe.
- \`services.py::_expected_cash_expr()\`: opening + cash_sales - cash_refunds; raha IN/OUT + seif puudub.
- \`models.py::DrawerSession\`: opening, cash_sales, cash_refunds, counted, expected, variance, opened_by, closed_by; ei sisalda eraldi cash in/out append-only päevaraamatut.
- PR #63–#66 töö on eraldi harudes ja tuleb enne „MISSING“ lõplikku kinnitamist võrdlusesse võtta.

## 6. UX testid ja vastuvõtukriteeriumid [T]

- 1366×768, 1024×768, 768×1024, 390×844: ei teki varjatud „Maksa“, tähtsad sihid vähemalt 44×44px, nähtavad veateated ja klaviatuuriga navigeerimine.
- Füüsilises 10-kassa koormuses tavatoote lisamine siht < 2 puudutust, makse alustamine < 2 lisapuudutust. Mõõta p95 ühes realistlikus riistvaras; **mitte väita mõõdetuks enne testi**.
- Kõigil 80 tegevusel kontrollitav õigus ja kontekst: denied API call ei tohi muuta andmeid, success muudab ainult oma režiimi andmeid.
- Kaotatud vastus (retry) ei tekita topeltköögipiletit, topeltmaksu, topeltlaokannet ega topeltseifi väljavõtet.
- Pimekassa: kasutajale ei saadeta kaudselt suletud kassavahetuse saldot HTML-is ega JSON-is.
- Offline: „sularahamüük jätkub“ ainult püsiva lokaalse journal'i korral, kaardimakse unknown ei proovita topelt.
- Ajalugu: enne/peale toimingu saab numbri järgi tagasi algdokumendi ja auditijälje.
- Igal tõrkel üks selge eestikeelne soovitus (nt „Printeri ühendus puudub. Tellimus ootab saatmist.”).

## 7. Avalikud CC5 juhendid (originaalid)

- https://ektaco.ee/helpcenter/kassa/cc5-kassa/
- https://ektaco.ee/helpcenter/kassa/cc5-kassa/muuk-ja-arvega-seotud-tegevused-cc5/
- https://ektaco.ee/helpcenter/kassa/cc5-kassa/arveridadega-tegevused-cc5/
- https://ektaco.ee/helpcenter/kassa/cc5-kassa/kassaga-seotud-uldised-tegevused-cc5/
- https://ektaco.ee/helpcenter/kassa/cc5-kassa/vahetused-cc5/
- https://ektaco.ee/helpcenter/kassa/cc5-kassa/offline-too-cc5/
- https://ektaco.ee/helpcenter/kassa/cc5-kassa/cc5-kassa-aktiveerimine/
- https://ektaco.ee/helpcenter/compucash5/seaded/kassa-seadistused/
- https://ektaco.ee/helpcenter/compucash5/tootehaldus/kassa-nupud/

**Piirid:** avalik pilt näitab nähtavat nuppu ja töövoo selgitust. Ektaco lähtekood, era-API ja makse-/seadme sertifikaat on suletud. AXAR loome originaalse koodina, mitte kopeerides Ektaco kujundust või koodi.
