# Discovery — TILL ja LIIDESED: kassa töövoog, raamatupidamine, kanalid

Etapp 2. Allikad: `README.md`. Tegevuskava: **P2-1** (paigaldatav kassa), **P1-3** (raamatupidamine), **P1-4** (võrguühenduseta arved), P3 (kullerid, e-pood).
AXAR täna: veebikassa, seadme registreerimine, PIN, 72 h võrguühenduseta töö koos sünkroonimisega (C18–C20), lauaarved, simuleeritud terminal, ekspordijärjekord.

## 1. Kassa töövoog

| Teema | Turul | AXAR |
|---|---|---|
| Aktiveerimine | rakendus poest; kontoris „genereeri aktiveerimiskood“ kassa kohta; kood sisestatakse või skannitakse. Teine juhend räägib e-postiga saadetud kasutajatunnustest. | olemas: ühekordne registreerimiskood → seadme token (C10). Lisame QR-koodi ja koodi aegumise. |
| Sisselogimine | vali kasutaja + parool; nupud, milleks õigust pole, on peidetud | PIN / kaart; õiguseta tegevus = **peidetud või „vajab kinnitust“** vastavalt õiguse tasemele |
| Võrguühenduseta | punane riba; müük töötab, aga: **ei trüki tellimust ega tšekki**, kliendikaarti ei saa lisada, terminal ei tööta, teiste kassade arveid ei näe, vahetust ei saa sulgeda, veebitellimused ei tule; **piiri ega sünkroonimise kirjeldust pole** | 72 h piir ja selle hoiatus kassas; kohalik tšeki ja köögipileti trükk (P2-3); terminali otseühendus kassaarvutist; räsiahelaga päevik; sünkroonimine täpselt üks kord; konfliktid läbivaatusele (J9) |
| Vahetused | **teenindaja vahetus** (algsumma, lõpp: loetud summa vs peab olema) ja **kassavahetus** (kollektiivne vastutus); võib kasutada mõlemat | olemas: sahtlisessioon. Lisame teenindaja vahetuse ja valiku „teenindaja / kassa / mõlemad“ asukoha seadena; vahe nõuab põhjust ja kinnitust (olemas) |
| Uus arve / taust | tühi arve takistab uut; täis arve läheb tausta | sama loogika; arve on tellimus olekuga (C06) |
| Arvete jagamine kasutajate vahel | ainult omad / kõik / jagatud; **avamine annab arve üle** | omanik + nähtavusreegel; üleandmine on sündmus ja nõuab õigust |
| Vahearve | trükk enne lõppmakset; **soodustus enne vahearvet**; pärast vahearvet muutmine nõuab õigust | õigus „muuda pärast vahearvet“ (kinnitusega); vahearve on sündmus |
| Arve tunnus / laud / käik | eelseadistatud või vaba tekst; köögipiletil | tunnus = tellimuse tüüp + laud + vaba silt; käik on tellimuse real |
| Jagamine | külastaja või rea järgi, mitu korraga; **pärast jagamist on read lukus** | olemas osaliselt (jagatud makse); lisame jagamise eraldi arveteks koos lukustusreegliga |
| Tühista arve / tühista makse | arve kuvatakse registris „tühistatud“; köögitellimus tühistatakse | arve tühistus = vastupidine dokument (J7); köök saab tühistuse sündmuse |
| Maksed | sularaha (vahetusraha), kliendikaart, integreeritud või käsitsi kaart, vabad makseviisid (Wolt, Bolt), ülekanne (nõuab klienti) | `sales.md` §2 |
| Pärast müüki | arve e-postiga | e-tšekk + e-arve P1-6 |

## 2. Raamatupidamise liides (näide: Merit Aktiva)

**Turul:**
- API ID ja võti, ühenduse test. Mitu juriidilist isikut = asukohad eksporditakse eraldi.
- **Käibemaksu vastavus:** 0 % ja maksuvaba müüki ei saa eristada.
- **Müük:**
  - ekspordi müük;
  - **päeva koondarve makstud arvetest** või iga arve eraldi;
  - numeratsioon ja prefiks;
  - tundmatu klient → „Eraklient“;
  - hotellitoa maksed.
- Seosed:
  - **makseviis ↔ makseviis**: seoseta makseviisiga arve eksporditakse eraldi;
  - müügipunkt ↔ osakond;
  - **müügipunkt + KM määr ↔ artikkel**.
- **Ost:** ekspordi, manused ja „kinnitamise ootel“. Ladu ↔ osakond, artiklikoodid peavad täpselt ühtima.
- **Ajastus:** automaatselt valitud päevadel ja kellaajal või käsitsi perioodi kaupa.
- Puuduv klient luuakse registrikoodi põhjal.
- **Vead on ainult kontori logis.** Uuesti saatmist ei ole kirjeldatud.

**AXAR-i disain (P1-3):**
1. Ekspordijärjekord on olemas (`books.export`). Lisame **edastaja** koos kviitungiga. Rida loetakse edastatuks ainult kviitungi olemasolul (R8).
2. **Kontoplaani seos** on andmetes ja ühes kohas: makseviis → konto, maksukategooria → KM kood (**0 %, maksuvaba ja maksustamata on eraldi**), rühm / toode → tulukonto, müügipunkt / ladu → osakond.
   **Seoseta rida ei lähe vaikselt eraldi**, vaid jääb järjekorda veaga, mis on nähtav kontoris.
3. **Koond või üksik:** valikuline, aga koond on alati lahti võetav arveteks (jälg).
4. **Kanne on tasakaalus** (deebet = kreedit). Kontroll enne saatmist.
5. Uuesti saatmine ei tekita kunagi topeltkannet (idempotentne võti).
6. **Perioodi sulgemine** (J10).

## 3. Kanalid ja muud liidesed

| Liides | Turul | AXAR |
|---|---|---|
| **E-arve (riigi e-arveldaja)** | aktiveerimine + leping kontoris; saadetakse käsitsi, arve kaupa; nõuab kliendi registrikoodi | automaatne, kui klient võtab e-arveid vastu; olek arvel; P1-6 / P1-3 |
| **Wolt / Bolt** | toote number = platvormi POS ID või SKU; tellimus tuleb kassasse („veebitellimused“) → valmimisaeg → kinnitus (köögipilet) → valmis → arve; seade „sulge makstud arve valmimisel“ | kanal tellimuse tüübina; **toote seos `external_refs`** (`catalog.md` §2); tellimus = order kanaliga, makse = `courier_prepaid`; saadavus ja paus sündmustena; võrguühenduseta kassa korral ootab tellimus serveris. P3 |
| **WooCommerce** | tooted, hind, laoseis iga 5 min; tellimus impordi liigina (müügitellimus / kontori arve / veebitellimus kassas); tarnetoode ja tundmatu toode; **üks pood müügipunkti kohta** | P3, sama kanalimudel |
| **EDI ostuarved (Telema)** | `inventory.md` §1 | P2-8 |
| **Ooteekraan (QDS)** | „valmimas“ ja „valmis“; valmis tellimus aegub 1–60 min; lehekülgede vahetus; heli; uuesti ühendamine; serveri seaded kirjutavad kohalikud üle | lisaekraan (`11-surfaces-and-realms.md`); andmed tulevad `kitchen.ticket.*` sündmustest (olemas). P2 |

## 4. Kokkuvõte: mida AXAR-i kassa peab tegema paremini
1. **Võrguühenduseta töö on dokumenteeritud lubadus:**
   - piir;
   - mis töötab (müük, tšekk, köök kohalikult, sularaha);
   - mis ei tööta (integreeritud kaart sõltub terminalist; kuller ja veeb ootavad);
   - kuidas andmed tagasi jõuavad.
2. **Seadete muutus ei vaja taaskäivitust ega tootja tuge.**
3. **Raamatupidamise viga ei peida arvet ega kao logisse.** See on nähtav järjekord koos parandusvõimalusega.
4. **Igal kassategevusel on õiguse tase** (lubatud, vajab kinnitust või keelatud) ja jälg auditilogis.
