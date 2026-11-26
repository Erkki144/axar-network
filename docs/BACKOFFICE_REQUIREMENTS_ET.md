# AXAR ONE – kontorimooduli nõuded (funktsionaalne kaardistus)

Allikas: ühe turul oleva POS-kontorisüsteemi kõigi vaadete vaatlus ainult lugemiseks (26.11.2026).
Dokument kirjeldab **funktsioone, andmevälju ja äriloogikat**, mitte välimust ega tekste. AXAR ONE kasutajaliides,
nimetused ja kood tehakse iseseisvalt selle kirjelduse põhjal. Isikunimesid ega müüginumbreid siin ei ole.

Prioriteedid: **P1** = esimesse versiooni, **P2** = varsti pärast, **P3** = hiljem / lisamoodul.

---

## 1. Ülesehitus

**Ettevõte → Müügipunktid → Kassad** ja eraldi **Laod**. Müügipunktil on vaikeladu ja hinnakiri.
Mitu müügipunkti võivad jagada üht ladu. Kasutaja valib aktiivse lao päisest.

| Moodul | Sisu |
|---|---|
| Avaleht | Viimati kasutatud vaated, kiirlingid, automaatsed trendikaardid |
| Töölauad | Kasutaja loodud KPI-kaartide lehed (vahelehtedena) |
| Müük | Arved, avatud arved, aruanded, hinnakirjad, soodustused, makseviisid, valuuta |
| Tooted | Tooteregister, kinkekaardid, variatsioonid, kassapaneeli nupud, süsteemitooted |
| Ladu | Laodokumendid, laoaruanded, laod, tarnijad, taastellimine |
| Kliendid | Kliendiregister ja lojaalsus, import, aruanne |
| Personal | Kasutajad, õiguste grupid, logid, meililistid |
| Liidesed | Raamatupidamine, kullerid, e-pood, EDI, hotellid, muu |
| Seaded | Litsentsid/moodulid, ettevõte, kassad, üldseaded, prindipõhjad, KDS, kliendiekraan |
| AI | Trendide analüüs ja assistent |

**Läbivad UI-mustrid (P1):** igas loendis kuupäevavahemik, filtripaneel koos aktiivsete filtrite arvuga,
filtri lähtestamine, otsing, valitavad veerud, **salvestatud vaated**, printimine, lehitsemine (25/50 rida) ja hulgivalik.

---

## 2. Tooted

### 2.1 Tooteloend (P1)
- Vasakul pesastatud **tooterühmade puu** (otsing, lisamine, rühmamenüü); paremal tabel.
- Põhiveerud: nimi, põhiühik, retsept jah/ei, laotoode jah/ei, rühm, müügihind.
- Filtrid: müügitooted / laotooted / kõik, aktiivsus.

### 2.2 Tootekaart
**Üldine (P1):** ID, nimi (mitmekeelne), lühend, rühm, tüüp, tootenumber, käibemaks %; põhiühik,
teisendussuhe, ühik retseptides, kaoprotsent; lipud (aktiivne, laotoode, peidetud kassast, veebimüük,
lisatakse automaatselt kaasavõtutellimusele); kaalumistaara, kogus hulgipakendis, infoväljad,
ekspordikood, kehtivuse lõpp, sidumiskood, tagastamise ajapiirang; kohandatavad omadused.
Tegevused: vöötkoodid, etiketid, pilt.

**Hinnad (P1):** omahind; **iga hinnakirja kohta** hind KM-ga, KM %, hind KM-ta, kate, juurdehindlus %
ja juurdehindlus summana. Ühe välja muutmisel arvutatakse ülejäänud ümber.

**Soodustused (P2):** tootele kehtivad soodustused.

**Ladu (P1):** laoseis lao kaupa (laohind, kogus, taastellimise piir ja kogus, 7 päeva müük, seisma jäänud
toote märge); müügipunkti ja lao seos; tarnijad (kood, tarnija tootenimi, ühiku koefitsient, tarnija hind,
viimane ostuhind ja -aeg, vaiketarnija, aktiivne).

**Müügipunktid ja tingimused (P2):** müügipunkti kaupa müügiluba, ajapiirang, vanusepiirang, turvaelement;
kassasõnumid (tekst, millal näidata, summatingimus).

**Retsept (P1 toitlustusele):** komponendid (toode või alamretsept), kogus, kadu, netokogus, ühiku omahind,
koguse omahind, millisest laost, tellimuse tüüp. Omahind arvutatakse keskmisena või müügipunkti kaupa.

### 2.3 Muu
- **Kinkekaardid (P2):** nimi, müügihind, nummerdatud kaartide reeglid, laos jah/ei; filtrid hinnakirja ja rühma järgi.
- **Variatsioonid (P3):** omadused (nt suurus) ja nende väärtused.
- **Süsteemitooted (P1):** tehnilised seosed: kinkekaardi jääk, ettemaks, varutoode tundmatutele
  imporditud ridadele (EDI/kullerid). Igaühel olek „seadistatud / seadistamata“.
- **Kassapaneeli nupud (P1):** kassa → paneelid (tavalised ja iseteeninduse paneelid, üks vaikimisi) →
  ruudustik. Nupu tüüp võib olla toode, makseviis, soodustus või funktsioon (uus arve, kogus, maksa,
  arve kööki jne). Nupul on värv ja tekst.
  *UX-õppetund: nende redaktoris avab üks klõps kustutamise kinnituse. AXAR ONE-s peab klõps nuppu muutma
  ja kustutamine olema eraldi tegevus.*

---

## 3. Müük

### 3.1 Arved (P1)
Loend: arve number, suletud, tühistatud, summa, teenindaja. Filtrid: aeg (kas arve avamise või sulgemise
järgi), vahetus, summa vahemik, arve tüüp, müügipunkt, kassa, töötaja, kauba väljastaja, toode, makseviis,
äri- või eraklient, tellimuse tüüp, tunnus. Uus **hulgiarve** (B2B arve kontorist). **Avatud arved**: lauad ja tellimused.

### 3.2 Aruanded
- **Koondaruanne (P1):** summa KM-ta, KM, summa KM-ga, kohalik maks, ümardus, tühistused, kreedit,
  soodustus, arvete arv, jootraha, teenustasu. Jaotused: tellimuse tüüp (külastajad, keskmine ost,
  keskmine aeg, mille arve on avatud), makseviisid, soodustused, tooterühmad, tühistatud ja krediteeritud arved.
- **Vahetuste aruanne / Z (P1):** avatud, suletud, kestus, arved, tühistused, **sularaha peaks olema /
  müüja sisestatud / vahe**, sularaha sisse ja välja, kaardilaadimised, läbimüük.
- **Läbimüük (P1), makseviisid (P1), soodustused (P2), statistika (P2).**
- **Menüü optimeerimine (P2):** müüdud kogus, populaarsus %, läbimüük, omahind, kasum, ühiku kasum,
  toidu omahinna %, **Kasavana-Smithi klass** (star / plowhorse / puzzle / dog).

### 3.3 Seaded
- **Hinnakirjad (P1):** tabel, kus read on tooted ja veerud hinnakirjad. Hinnakirja saab kopeerida ja kustutada
  ning hindu taastada. Filtrid: rühm, hind KM-ta, ainult seisma jäänud tooted.
- **Soodustused (P1):** nimi; kas rakendub automaatselt või lisatakse käsitsi; arvutamise alus (protsent või summa);
  väärtus; kehtivus (kogu päev või kellaaeg, nädalapäevad, periood); tingimused: kehtib koos teiste
  soodustustega, nähtav kassas, ainult kliendikaardiga, sünnipäevasoodustus, kas annab soodustust või
  kogub boonust, kas rakendub kogu arvele või reale, müügipunktid, tooterühmad (kõik / valitud / välja arvatud),
  ainult seisma jäänud tooted; seotakse automaatselt uute era- või äriklientidega; piirang õiguste gruppide
  järgi; aktiivne. Paremal reaalajas **kokkuvõte**.
- **Arvete ja müügitellimuste seaded (P2):** komakohtade arv (2–4); arve tunnused müügipunkti kaupa
  (nt laud, kaasa); allkirjaõiguslik isik; pealkirjad (aktsepteerimata, aktsepteeritud, kreedit); numeratsioon;
  lubatud kuupäevaerinevus ja hoiatuse piir; müügipunkti rekvisiidid; ridade liitmine; **jaluse tekstid**
  (nt säilivusaeg). Müügitellimused: staatused (uus → kinnitamisel → arveks tehtud), ettemaksutoode,
  ettemaksu %, nõutud täielik ettemaks, arve automaatne aktsepteerimine.
- **Makseviisid (P1):** ID, nimi, makseviisi tüüp (sularaha, kaart, ülekanne, kupong, hotelli arve, veebimakse …),
  grupi tähis, lisainfo, kas jootraha on lubatud, arhiveeritud.
- **Valuuta (P3):** valuuta, kurss, ümardus, kas kassas on lubatud.

---

## 4. Ladu

### 4.1 Dokumendid (P1)
Sisseost (saateleht/ostuarve, ostutellimus, kreeditarve), väljaminek, ladudevaheline liikumine
(ja ülekandetellimus), mahakandmine, tootmine, **inventuur ja hinnainventuur**, taastellimine (P2).
- Loendi veerud: tüüp, number, kuupäev, olek (mustand või aktsepteeritud), tarnija, lähte- ja sihtladu,
  summa KM-ta ja KM-ga; sildid.
- Inventuur: number, olek, **erinevus koguses ja rahas**, loodud, aktsepteeritud, kommentaar, looja.

### 4.2 Aruanded
- **Laoseis (P1):** valitud kuupäeva ja kellaaja seisuga; laohind, kokku, müügihind, müügihindade summa;
  peida nullkogused; filtrid: rühm, hinnakiri, ladu, kohandatud atribuudid.
- **Koondaruanne, liikumised, tooteajalugu (P1).**
- **Seisma jäänud tooted (P2):** müügi analüüs alates kuupäevast; laovaru päevades, saadaval olnud päevad,
  seisma jäänud kogus ja summa, müük päevas, viimane müük.

### 4.3 Seaded
- **Laod (P1):** nimi, maja, tarneaeg, liidese tüüp, aadress, telefon, hinnakirjad, selgitus.
- **Tarnijad (P1):** nimi, registrikood, kontakt, telefon, e-post, KM-ta, maksetähtaeg, IBAN, viitenumber, aadress.
- **Taastellimise piirid (P2):** toode, vöötkood, laos, piir, tellitav kogus, kogus pakendis, 7 päeva müük.
- **Ladu ja toode (P2):** toote seotus ladudega.
- **Ladustamisalad (P3):** lao sees asukohad.
- **Dokumentide seaded (P1):** numeratsioon dokumendi tüübi kaupa (prefiks + number); pealkirjad;
  vaikehinnaklass; kas mitteaktiivse toote võib lattu võtta; retsepti lahtivõtmine vaikimisi;
  ostudokumendil kas võtta viimane ostuhind või tarnija hind; müügihinna kiirsisestus; pandipakendi
  automaatne lisamine; komakohad; ülekandetellimuse kinnitamine ja automaatne aktsepteerimine;
  kuupäevaerinevuse piirid; toode tundmatute imporditud ridade jaoks.

---

## 5. Kliendid
- **Register (P1):** kaardi number, ees- ja perenimi, e-post, telefon, kehtivuse algus ja lõpp, viimane müük.
  Rohked filtrid: ettevõte, maksed, soodustus, sünnipäev, vanus, sugu, olek, viimase müügi vahemik, aegumine,
  keel, müük, loodud, nõusolekud (tingimused, turundus), linn, kliendi tüüp, mobiilse kaardi olek.
- **Import (P1):** CSV, eraldaja, eelvaade, imporditavate väljade **profiil**.
- **Seaded (P2):** äriklientide vaikimisi maksetähtaeg, viivis ja tarnetähtaeg; kas keelata müük, kui
  kliendihind puudub; kas kaardiga saab maksta; kliendi tüübid; kaardi kehtivus (päevad, kellaaeg, kuud);
  sünnipäevasoodustuse aken (päevad enne ja pärast); **soodustuse tasemete** automaatne määramine;
  kaardinumbri genereerimine; tervitusboonus (summa ja periood).
- **Kliendi tegevuse aruanne (P2).**

## 6. Personal
- **Kasutajad (P1):** ees- ja perenimi, õiguste grupp, keel, e-post, info, müügipunktid, kaart (sisselogimiseks), laod.
- **Õiguste grupid (P1)** (nt iseteenindus, kontor, teenindaja). Õigused on kahel vahelehel:
  - **Kontor:** müük ja aruanded, vahetuste ja tööaja aruanded, tootehaldus, kontorimüük, müügiaruanded,
    teenuste kalender, majutus, ERP eksport, EDI, müügihinnad ja soodustused, arve tühistamine, maksete muutmine,
    seaded, sündmuste logi, kasutajad, programmi uuendamine, CRM, kliendikaardi laadimine ja saldo nullimine,
    mobiilse kaardi teavitused, laohaldus, **ostuarve aktsepteerimine**, **inventuuri sulgemine**, **omahinna nägemine**.
  - **Kassa:** sisselogimine, superkasutaja, riistvara muutmine; sularaha sisse ja välja, kassa ja teenindaja
    vahetuse lõpetamine, rahasahtli avamine; arve üleandmine, hinnakirja muutmine, arve jagamine, liitmine ja
    ülevõtmine, ridade muutmine pärast vahearvet või kööki saatmist, kviitungi koopia, maksete muutmine,
    tühistamine, kreedit, piiranguteta tagastus; ridade kustutamine ja koguse vähendamine (kõik, viimane, kööki
    saadetud); hinna sisestamine, omahinna nägemine, **miinusmüük**; aruanded (teiste kasutajate omad, ainult
    avatud vahetus); kliendid ja soodustused (otsing, lisamine, käsitsi soodustus, kaardi laadimine);
    lao koondaruanne, inventuuri sisestamine.
- **Kassa tegevuste logi (P1):** aeg, kassa, arve, kogus, summa, vahetus, tegevus, toode.
- **Kassa tegevuste koondaruanne (P2, pettuste ennetus):** lisatud read; kustutatud read (kõik, hiljem kui
  5 minutit, pärast vahearvet); koguse vähendamised; tühistatud ja krediteeritud arved; käsitsi soodustusega
  arved; rahasahtli avamised. Igaüks arvu ja %-na **teenindaja kaupa**, järjestatav protsendi järgi.
- **Kontori tegevuste logi (P1)**, **meililistid** aruannete saatmiseks (P3).

## 7. Seaded
- **Litsentsid ja moodulid (P2):** iseteenindusena moodulite sisse- ja väljalülitamine, kuutasu muutuse
  eelvaade, kasutajate ja kassade arv, ajutine sulgemine.
- **Ettevõtte andmed (P1):** nimi, registrikood, KMKR, aadress, kontaktid, pangakontod (IBAN, BIC).
- **Müügipunktid ja kassad (P1):** puu, kus müügipunkti all on kassad; kassa lisamine.
- **Üldseaded (P1):** vaikimisi KM, valuuta, **päeva algus** (öise vahetuse jaoks), vaikeühik,
  iseteeninduse kasutaja, komakohad, tootenumbri ja vöötkoodi genereerimine, kaaluga vöötkoodid,
  tagatisraha toode, kinkekaardi jäägi toode; e-posti saatmine (SMTP, MS 365, Gmail, ühenduse test).
- **Prindipõhjad (P2):** müügidokumendid, laodokumendid, etiketid.
- **Tellimuse tüübid (P1):** nimi (kohapeal, kaasa, lauast, tarne), hinnakiri, müügipunktid, baastüüp,
  maksukood, kas teenustasu arvestatakse.
- **Kehtivusperioodid (P2):** nimelised ajavahemikud (algus, lõpp, nädalapäevad), mida kasutavad soodustused ja piirangud.
- **Kohandatav statistika (P3):** kaks vabalt määratavat statistikadimensiooni ja nende väärtused.
- **Kaalumise taarad (P3):** nimi ja kaal kg.
- **KDS ja köögiprinterid (P2):** kassad (nimi, kood, müügipunkt); köögiprinterid (nimi, saatev kassa,
  printiv kassa, printer, KDS-ekraan); veebitšekiprinter.
- **Kliendiekraan (P3):** reklaammeedia teisele ekraanile.

## 8. AI ja trendid (P3, eristumise võimalus)
Automaatsed kaardid, kus on võrdlus eelmise perioodiga, % muutus ja seletus lihtsas keeles (nt tipptund,
soodustuse mõju keskmisele ostukorvile, ostukorvide langus vahetuse kaupa) ning link aruandesse.
Saadaval on ka AI-assistent ja ostuarvete lugemine PDF-ist või pildist.

## 9. Liidesed (turu ootus)
Raamatupidamine (Merit, Directo, QuickBooks), EDI (Telema, Edisoft), riigi e-arve, kullerid (Wolt, Bolt,
Deliverect jt), e-pood (WooCommerce), hotellid (PMS), makseterminalid (EMV, Adyen), ID-kaart, Stebby,
kaubanduskeskuste statistika eksport.

## 10. Hinnastus turul (viide)
Konkurent müüb mudelis, kus on baaspakett ja lisamoodulid. Ligikaudu: kassa ~40 €/kuu ja aktiveerimistasu,
kontorikasutaja ~10 €/kuu, makseterminali liides ~5 €/kassa, lisamoodulid 10–40 €/kuu ja ühekordne
aktiveerimistasu 58–116 €.

---

## Soovitused AXAR ONE-le
1. **Import konkurendi ekspordist** (tooted, retseptid, kliendid, laoseis, tarnijad): ühe klõpsuga üleminek.
2. **Lihtsam hinnastus:** vähem eraldi tasulisi mooduleid ja aktiveerimistasusid.
3. **Ohutu redaktor:** klõps muudab, kustutamine on eraldi ja kinnitusega. Igal muudatusel on tagasivõtmine.
4. **Miinuslaoseisu poliitika** müügipunkti kaupa (luba / hoiata / keela).
5. **Kate reaalajas:** hoiatus, kui retsepti omahind tõstab toidu omahinna % üle piiri.
6. Pettuste ennetuse koondaruanne koos teavitustega (nt e-kiri, kui teenindaja kustutab ridu pärast vahearvet üle X%).
7. AI-trendid kohe põhipaketis.
