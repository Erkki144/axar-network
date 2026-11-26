# Discovery — CATALOG: tooted, hinnad, retseptid, kassapaigutus

Etapp 2 (`products/axar-one/architecture/13-delivery-journey.md`). Valdkond → tegevuskava sammud **P1-1**, **P1-5**.
Allikad:
- turul oleva süsteemi kontor (ainult vaatamine, 26.11.2026): tooted *Croissant lõhega*, *2 palli jäätis*, *lõunakomplekt*,
  hinnakirjad, kassapaneel, süsteemitooted, üldseaded;
- AXAR ONE kood: `products/axar-pos/axar_pos/models.py` (`Product`, `Item`, `Recipe`, `RecipeLine`, `TaxRate`, `SaleLine`, `StockMovement`).

**Vaatluse piirang.** Valikuloendite sisu (tootetüübid, ühikud) jäi osaliselt nägemata. Muutmisvormis muutis üks klõps
salvestamata väärtust, vajutasin „Katkesta“ ja kontrollisin, et midagi ei salvestunud. Muutmisvorme ma enam ei ava. Lahtised küsimused on §6.

---

## 1. Kaks erinevat mudelit — põhiotsus

| | Turu süsteem | AXAR ONE täna | Otsus |
|---|---|---|---|
| Mis on „toode“ | **üks kirje** on korraga müügitoode, laokaup ja tooraine; lipud „laotoode“ ja „retsept“ | **kaks registrit**: `Product` = müügipakkumine (menüüs, kassas), `Item` = laokaup (tooraine, valmistoode, edasimüük, pakend) | **jätame AXAR-i mudeli.** Üks laokaup (piim) võib olla kümne müügitoote koostises, ja üks müügitoode (kohv) ei pea olema laos. Kontoris näitame neid ühel kaardil, kui `stock_mode = item`, et kasutaja ei peaks topelt sisestama. |
| Käibemaks | **number tootel** (24) | **maksukategooria** tootel + versioonitud määrad (`TaxRate.valid_from`) | **jätame AXAR-i mudeli.** Määra muutus (nt 22 → 24 %) ei nõua iga toote muutmist ja vanad müügid jäävad õigeks. |
| Hind | hind **iga hinnakirja kohta**, tühi = selles hinnakirjas ei müüda | **üks hind** tootel (`price_cents`) | **ehitame hinnakirjad + hinnaversioonid** (ADR-0015). |
| Retsept | komponent võib olla toode või alamretsept; kadu %; ladu; tellimuse tüüp; tootlikkus; „kanna maha tootena“ | versioonitud, read = laokaup + kogus; tootlikkus; partii-retsept (`output_item_id`) | **laiendame**: kadu %, tellimuse tüüp, ühik, alamretsept (kaudselt läbi pooltoote). |
| Rühm | puu, rühma kaupa seaded | tekstiväli `category` „ainult kassa jaoks“ | **ehitame rühmade puu.** |
| Kogus müügireal | komakohaga (kaalukaup) | **täisarv** (`SaleLine.quantity`) | **muudame** mikroühikuteks nagu laos (`qty_micro`). Vajalik kaalukauba ja grammide jaoks. |

---

## 2. Müügitoode (`Product`) — väli väljalt

| Väli (turul) | Tähendus | Kohustuslik / vaikimisi | Valideerimine | Kus kasutatakse | Seosed | AXAR-i otsus |
|---|---|---|---|---|---|---|
| Toote ID | süsteemi tunnus | automaatne | — | kõik viited | — | olemas (`id`). Viited **ainult ID järgi**. |
| Toote nimi | nimi kassas, tšekil, aruandes | **kohustuslik** | mitmekeelne | kassa, tšekk, KDS, aruanne, eksport | tšekk salvestab nime koopia | olemas (`name`). **Lisame tõlked.** Ümbernimetamine ei muuda vanu tšekke. |
| Lühend | lühinimi kassanupule / köögile | valikuline | pikkuse piir | kassanupp, KDS | — | lisame (`short_name`). |
| Rühm | koht puus, aruannete jaotus, vaikeväärtuste allikas | **kohustuslik** | olemasolev rühm | kassa navigatsioon, aruanne, soodustuse ulatus, köögiprinter | rühm annab vaikimisi maksukategooria / printeri | `category` tekst → **viide rühmale** (`group_id`). |
| Toote tüüp | käitumine: tava, teenuskaart (kasutuskorrad, kehtivus, külastus), kinkekaart, süsteemitoode … | **kohustuslik**, „tava“ | valik | kassa käitumine, eksport | tüüp **muudab vormi välju** (teenuskaart lisab kasutuskordade, kehtivuse ja ajapiirangu väljad) | lisame `kind`: `standard`, `gift_card`, `service_card`, `deposit`, `system`. Esimeses etapis ainult `standard` + `system`, ülejäänud P3. |
| Toote number | välise süsteemi / raamatupidamise kood | valikuline; võib genereerida ID-st või numeratsioonist | unikaalne | eksport, import | — | olemas (`sku`, unikaalne). Genereerimisreegel seadetes. |
| Vöötkood(id) | skanneriga müük | valikuline, mitu | EAN-kontrollsumma | kassa | kaalukauba kood sisaldab kaalu | **lisame** eraldi tabelina (mitu koodi tootel). Kaaluga koodid P2. |
| Käibemaks | maksumäär | **kohustuslik** | — | hind KM-ta, tšekk, eksport | — | **maksukategooria** (`tax_category`), mitte number. |
| Põhiühik, suhe, ühik retseptides | mõõtühik ja teisendus | **kohustuslik** | — | ladu, retsept, ostud | tarnija ühik × koefitsient | müügitootel ühik = „tk“ või „kg“ (kaalukaup). **Teisendused kuuluvad laokaubale** (`Item.uom`), mitte müügitootele. |
| Kaoprotsent | keskmine kadu valmistamisel | 0 | 0–100 | omahind | retsepti kadu | kuulub **retseptireale**, mitte tootele. |
| Aktiivne | kas müüakse | jah | — | kassa, hinnakiri | ajalugu jääb | olemas (`active`). Deaktiveerimine sündmusega (J4). |
| Laotoode | kas müük kannab laost maha | — | — | ladu | ühtib „retseptiga“ | olemas: `stock_mode` = `none` / `item` / `recipe`. Selgem kui kaks lippu. |
| Retsept (lipp) | kas tootel on koostis | **arvutatakse** (pole muudetav) | — | — | tuleneb retseptist | ei salvesta eraldi; tuleneb `stock_mode = recipe`. |
| Peida kassast | müüakse, aga nuppu ei näidata (nt ainult skanneriga) | ei | — | kassa | — | lisame (`hidden_at_till`). |
| Veebimüük | nähtav e-poes / QR-tellimuses | ei | — | külalise kanal (#18) | — | lisame (`channels`: kassa, veeb, kuller). |
| Lisa automaatselt kaasavõtule | pakend lisatakse kaasa-tellimusele | ei | — | iseteenindus | tellimuse tüüp | **P1-5 reegel** tellimuse tüübil („kaasa → lisa pakend X“), mitte lipp tootel. |
| Kaalumise taara | kaalukauba pakendi kaal | — | — | kaal | — | P3. |
| Kogus hulgipakendis | ostu-/müügipakend | 1, **kohustuslik** | > 0 | ost, taastellimine | — | kuulub **laokauba ja tarnija** juurde. |
| Info, Info 1, Info 2 (tõlgitavad) | kirjeldus menüüs / koostis / allergeenid | valikuline | — | menüü, QR, tšekk | — | asendame selgete väljadega: `description`, **`allergens`** (EL 14 allergeeni), `ingredients_text`. |
| Ekspordi kood / objekt | raamatupidamise konto / kulukoht | valikuline | — | eksport | rühma vaikimisi | P1-3 kontoplaani seos, rühma tasemel + erand tootel. |
| Kehtivusaja lõpp | säilivus etiketil | valikuline | — | etikett | — | P3 (partii ja säilivus #14). |
| Artikli sidumiskood | seos välise süsteemiga (e-pood, kuller) | valikuline | — | integratsioon | — | lisame `external_refs` (allikas + kood) — mitu süsteemi korraga. |
| Tagastamise ajaline piirang | kui kaua tohib tagastada | valikuline | — | tagastus | — | P1-2 (#48) reegel. |
| Täiendavad omadused | vabad atribuudid | — | — | filtrid, aruanded | — | P3. |
| Pilt | kassa / menüü / QR | valikuline | — | — | — | P2 (#40). |

## 3. Hinnad ja hinnakirjad

**Vaadeldud:**
- Iga toote juures on rida **iga hinnakirja kohta**. Hinnakiri vastab müügipunktile (nt eraldi hind igas söögikohas).
- Tühi hind tähendab, et selles hinnakirjas toodet ei müüda.
- Ühe välja muutmisel arvutatakse teised kohe ümber:
  - hind KM-ga;
  - KM %;
  - hind KM-ta;
  - **kate** = hind KM-ta − omahind;
  - **juurdehindlus %** = kate ÷ hind KM-ta;
  - **juurde %** = kate ÷ omahind.

  Näide: hind 4,50 → KM-ta 3,63 → omahind 0,45 → kate 3,18 → 87,6 % → 706,7 %.
- Hinnakirjade lehel saab hinnakirja kopeerida ja hindu taastada.
- Hinnakirja saab sättida ka tellimuse tüübi järgi (kaasa / kohapeal).

**AXAR-i disain (P1-1 → P1-5):**

| Register | Väljad | Reeglid |
|---|---|---|
| `price_list` | nimi, ulatus (müügipunkt(id), tellimuse tüüp), prioriteet, aktiivne | igal müügipunktil on vaikimisi hinnakiri |
| `price_version` | toode, hinnakiri, hind KM-ga sentides, `valid_from`, `valid_to`, autor, põhjus | **ei kirjutata üle**; uus hind = uus versioon; avaldamine → `pricing.price.published` |
| arvutatud vaade | KM-ta hind, kate, kate %, juurdehindlus % | **ei salvestata**; omahind tuleb retseptist / laost hetke seisuga |

**Hinna leidmise järjekord kassas:**
1. tellimuse tüübi hinnakiri müügipunktis;
2. müügipunkti hinnakiri;
3. vaikehinnakiri;
4. kui hinda pole → toodet ei müüda. Juht võib hinna käsitsi sisestada õigusega „hinna sisestamine“; müügirida märgitakse „käsihind“.

**Uus kooskõlareegel R14:** iga müügirea hind võrdub müügi hetkel kehtinud avaldatud versiooniga või on märgitud ja kinnitatud käsihinnaks.

## 4. Retsept

**Vaadeldud:**

| Väli | Tähendus | Näide |
|---|---|---|
| Retsepti tootlikkus | mitu ühikut üks retsept annab | 1 tk |
| „Kanna maha tootena, mitte koostisosadena, teistes retseptides“ | **pooltoode**: kui see on teise retsepti komponent, võetakse laost pooltoode, mitte selle toorained | — |
| Valmistamisjuhend | tekst köögile | — |
| Komponent | toode või alamretsept | — |
| Kogus + ühik | ühik võib erineda laoühikust | 1 g kanašnitslit, laohind 7,75 €/kg |
| Kadu % | koguse kadu | 0 % |
| Neto kogus | kogus pärast kadu | — |
| Ühiku omahind, koguse omahind | — | — |
| Laost | millisest laost võetakse | — |
| Tellimuse tüüp | komponenti kasutatakse ainult kohapeal / kaasa / tarne | kaasa → lisa karp |
| Omahind | keskmine üle müügipunktide või müügipunkti kaupa | — |

**Tähelepanek:** näites on kanašnitsli kogus 1 g, kui mõeldud oli ilmselt 1 tk või 150 g. Tulemuseks on omahind 0,01 €. Turu süsteem seda ei märka.
**AXAR peab hoiatama:** ühikusobimatus või ebarealistlik omahind (näiteks alla 5 % müügihinnast) → hoiatus enne avaldamist.

**AXAR-i disain (laiendame olemasolevat `Recipe` / `RecipeLine`):**

| Väli | Täna | Lisame |
|---|---|---|
| versioon, `valid_from`, tootlikkus | ✅ | avaldamise sündmus `recipe.version.published` |
| komponent = laokaup | ✅ (`item_id`) | pooltoode = laokaup, mida toodetakse partii-retseptiga (`output_item_id`). Nii saame alamretsepti ilma tsüklite ohuta. |
| kogus | ✅ `qty_micro` laoühikus | **sisestusühik** + teisendus (g → kg) |
| kadu % | ☐ | `loss_bp` → bruto = neto ÷ (1 − kadu) |
| tellimuse tüüp | ☐ | `order_types` (tühi = kõik) |
| ladu | vaikimisi ladu müügipunkti järgi | ülekirjutus reale (P2) |
| valmistamisjuhend, allergeenid | ☐ | allergeenid **arvutatakse** komponentidest ja näidatakse tootel |
| hoiatused | ☐ | ühikusobimatus, omahind 0, toidu omahinna % üle piiri |

## 5. Kassapaigutus ja süsteemitooted

**Vaadeldud kassapaigutus:**
- Ülesehitus: kassa → paneelid (tavalised ja iseteeninduse paneelid, üks neist vaikimisi) → ruudustik.
- Nupp võib olla:
  - toode;
  - makseviis (sularaha, pangakaart);
  - soodustus (−10 %);
  - funktsioon (uus arve, kogus, maksa, arve kööki, kupong).
- Nupul on tekst ja värv.

**AXAR-i disain:**
- **P1-1:** automaatne paigutus rühmade puu järgi. Kassapidaja näeb rühmi ja tooteid, käsitsi paigutust pole vaja.
- **P2-5 (#40):** `pos_layout` versioon: kassa → paneelid → nupud (tüüp + viide ID järgi + värv), avaldamine → `catalog.layout.published`.
- Redaktoris **muudab klõps nuppu ja kustutamine on eraldi tegevus**. Turu süsteemis avas üks klõps kustutamise kinnituse.

**Süsteemitooted (vaadeldud):** kinkekaardi jääk, ettemaks ja varutoode tundmatutele imporditud ridadele. Igaühel on olek „seadistamata / seadistatud“.

**AXAR-i disain:** süsteemitooted luuakse **automaatselt koos tenandiga** ja neid ei saa kustutada. „Seadistamata“ olekut pole.

## 6. Lahtised küsimused omanikule

1. **Hinnakirjad:** kas igal söögikohal on oma hinnad? Turu süsteemis on 9 hinnakirja, aga vaadeldud toodetel oli hind ainult ühes. Kui hinnad on kõikjal samad, piisab esimeses etapis ühest hinnakirjast + tellimuse tüübi erandist.
2. **Tootetüübid:** kas kasutate teenuskaarte (nt 10 korra kaart, külastused) või kinkekaarte? Kui ei, jäävad need P3-sse.
3. **Kaalukaup:** kas müüte midagi kaalu järgi (salatibaar)? Siis on komakohaga kogus vajalik juba P1-1-s.
4. **Allergeenid:** kas need peavad olema menüüs ja tšekil kohe piloodis?
5. **Hinnamuudatuste kinnitamine:** kas hinnamuudatus vajab teise inimese kinnitust või piisab juhi õigusest?

### 6.1 Omaniku vastused (26.11.2026) ja nende tagajärg

| # | Vastus | Tagajärg disainile |
|---|---|---|
| 1 | Igal söögikohal võivad olla oma hinnad. Toode võib olla sama ja hind erinev, või võivad tooted olla erinevad. | **Mitu hinnakirja on vaja juba P1-1-s**, mitte alles P1-5-s. Hinnakiri on seotud müügipunktiga. Toode on ettevõtte ühine. Toode, millel müügipunkti hinnakirjas hinda pole, selles müügipunktis ei müüda. Seega saab ka „erinevad tooted eri kohtades“ lahendada ilma eraldi tooteta. |
| 2 | Kinkekaart võib tulla. | Kinkekaart → **P2** (uus samm P2-9), mitte P3. Tootetüüp `gift_card` on andmemudelis kohe olemas, aga müük ja lunastus tulevad P2-s. |
| 3 | Kaalukaupa müüakse. | **Komakohaga kogus müügi- ja tellimusreal on P1-1-s** (`qty_micro`, ühik kg). Kaaluga vöötkood ja kaaluühendus tulevad P2-s. |
| 4 | Allergeenid ei pea piloodis olema. | Allergeenide väli on valikuline ja selle kuvamine menüüs ja tšekil on **P2**. |
| 5 | Hinnamuudatus vajab teise inimese kinnitust. | **Neli silma:** hinna mustandi loob üks inimene, avaldab teine (õigus „kinnita hinnamuudatus“). Looja ei saa enda mustandit kinnitada. Mõlemad on hinnaversioonil kirjas. Erand: omanik, kui ta on ettevõttes ainuke kinnitaja, ja see märgitakse versioonile. |

## 7. Lisandused tootja juhendist (26.11.2026)

### 7.1 Tooterühm on rohkem kui kaust
- **Rühma seaded:** nimi, alamrühmad, **köögi- ja baariprinterid** (tellimuse suunamine käib rühma järgi), **müügipunktid, kus rühma tooteid müüakse**,
  automaatne kassanupp (ei lisa / lisab rühma nupu / lisab rühma iga uue toote valitud paneelidele).
- Rühma ei saa kustutada, kui selles on tooteid. Enne tuleb tooted ümber tõsta.
- **AXAR:** rühm annab vaikeväärtused (maksukategooria, köögijaam, müügipunktid, kassanupu reegel, raamatupidamise konto). Toode võib neid üle kirjutada.
  Köögijaama saab määrata ka **tootele** (turul ainult rühmale: üks erand nõuab uut rühma).

### 7.2 Komponendid ja ühikud
- Turul on komponent tavaline toode, millel on „peida kassast“ märge. Iga komponent vajab ostuühikut ja retsepti mõõtühikut koos suhtega. Neli tüüpilist juhtu:
  - ostetakse kg, retseptis g;
  - ostetakse l, retseptis ml;
  - ostetakse tükk, retseptis g (purk 300 g);
  - ostetakse pakk, retseptis tk (munad karbis).
- Alkoholi soovitus: laos liitrites, retseptis sentiliitrites. Siis ei sõltu arvestus pudeli suurusest.
- **AXAR:** see on meil juba **laokaup** (`Item`) põhiühikuga. Lisame **ühikuteisendused** laokaubale (ostuühik → põhiühik → retsepti ühik) ja
  **tarnija pakendi suhte** tarnija seosesse (500 g pakk → 0,5 kg). Komponenti ei pea tegema müügitooteks.

### 7.3 Lisandid ja erisoovid (puuduvad AXAR-is)
- Kaks grupi liiki:
  - **lisandid** (nt garneering), millel võib olla hind;
  - **erisoovid** (nt küpsetusaste), kus on valikud või vaba tekst.
- Grupi seaded:
  - aken avaneb toote lisamisel automaatselt;
  - mitu valikut ja maksimumarv;
  - piirang müügipunktide kaupa;
  - hind hinnakirjast (kui hind puudub, on lisand tasuta);
  - vaba teksti lubamine.
- Valikute järjekord on sama, mis kassas.
- **Oht:** grupp, mis pole ühegi tootega seotud, kehtib **kõigile toodetele**.
- Veebimenüüd vajavad lisandigrupis vaikimisi valikut.
- **AXAR (P1-5 / P2):** `modifier_group` (liik, min / max valikuid, vaikimisi valik, vaba tekst, müügipunktid) → `modifier` (nimi, hinnamuutus, laokulu retseptina).
  **Seos peab alati olema selgesõnaline**, „kehtib kõigile“ vaikimisi ei ole. Valitud lisand on tellimuse- ja müügireal alamreana koos hinna ja laokuluga.
  Köök näeb lisandeid ja erisoove piletil.

### 7.4 Variatsioonid
- Omadused (värv, suurus) ja nende väärtused. Variatsioonidega tootel on tüüp „varieeruv toode“. Salvestamisel luuakse kõik kombinatsioonid eraldi toodetena,
  mis pärivad põhitoote numbri ja hinna. Hulgitegevused: lähtesta põhitoote järgi, nimi valemist, vöötkoodid, hind, deaktiveerimine.
- **Oht:** lähtestamine kirjutab üle variandi käsitsi muudetud hinna.
- **AXAR (P3, jaekaubandus):** sama mudel. Lähtestamise eelvaade näitab, mida see üle kirjutab.

### 7.5 Kinkekaardid
- Kinkekaart on toode tüübiga „kinkekaart“ ja reegel:
  - kasutuskordade arv (0 = piiramatu);
  - vaikeväärtus (tühi = müügihind);
  - kehtivus päevades või kindel kuupäev;
  - osaline kasutus;
  - korduvkasutus;
  - number genereeritakse või loetakse müügil;
  - trükk kassas.
- Numbreid saab ette genereerida trükikojale. Number aktiveerub alles müügil.
- Lunastamine käib makseviisina: kaart ilmub arvele negatiivse reana. Aruanne näitab väljastatud, kasutatud ja aegunud kaarte.
- Digitaalne kinkekaart (Apple / Google Wallet) näitab jääki reaalajas. Kaarte saab ka ajastatult klientidele saata (0 € arvega).
- **AXAR (P3):**
  - kinkekaardi müük **ei ole tulu**, vaid kohustus (ettemaks);
  - lunastamine on makseviis;
  - müük läheb raamatupidamisse ettemaksu kontole;
  - turu juhend ei kirjelda raamatupidamise poolt üldse.

### 7.6 Hinnakirjade tegevused (täpsustab §3)
- **Hinnakirja väljad:** nimi, **ümardus**, käibemaks % (vt L1), müügipunktid koos **kehtivusvahemikuga**.
- **„Arvuta“:** uued hinnad = keskmine laohind × (1 + kate %), kus keskmine laohind on keskmine üle ladude. Ulatus: kõik tooted / ainult hinnaga tooted / ainult hinnata tooted.
  Hind muudetakse ainult siis, kui muutus ületab künnise.
- **„Kopeeri“:** teisest hinnakirjast protsendi või koefitsiendiga.
- **„Kustuta“:** kustutab filtri ulatuses.
- **Taastamine:** logis on kuupäev, hinnakiri, kasutaja ja tegevus. Hinnad saab taastada tegevuse eelsesse seisu.
- **Vastuseta jääb:** mis juhtub, kui kaks hinnakirja kattuvad samas müügipunktis.
- **AXAR:**
  - hulgitegevused (arvuta, kopeeri, % muutus, ümardus) loovad **ühe mustandi**, millele järgneb eelvaade ja avaldamine kehtivuskuupäevaga;
  - „taastamine“ on uus versioon, mitte ajaloo ülekirjutus;
  - hinnakirjade kattumise reegel on selgesõnaline: prioriteet + tellimuse tüüp, kattumine on keelatud.

### 7.7 Toodete import
- CSV-faili veergude järjekord on vaba. Kohustuslikud on vöötkood või tootenumber ning nimi. Soovituslikud on hind KM-ga ja rühm (rühm luuakse, kui seda pole).
- Valikud:
  - identifikaator;
  - hinnakiri;
  - kas importida uusi tooteid;
  - kas uued tooted on aktiivsed;
  - kas uuendada olemasolevaid;
  - **kas kirjutada tühjaga üle**;
  - kas uuendada hindu;
  - kas aktiveerida deaktiveeritud tooted;
  - kas nõuda hinda;
  - tarnija info;
  - kas jätta päis vahele.
- Seadistuse saab salvestada profiilina (privaatne või vaikimisi). Vigased read jäetakse vahele.
- **AXAR (P2-8):** sama paindlikkus, aga:
  - proovikäivitus ja erinevuste aruanne;
  - tühi lahter ei kustuta;
  - kogu import on üks tehing, mida saab tagasi võtta;
  - impordiprofiilid on olemas ka teiste kassasüsteemide formaatide jaoks.

### 7.8 Veebimenüüd ja kanalid
- **Kullerimenüü (Bolt / Wolt):**
  - eraldi menüü oma rühmadega (pole seotud tooterühmadega);
  - hind kullerihinnakirjast;
  - ajaline saadavus;
  - saadavus platvormi kaupa;
  - paus (nt kui toode on otsas);
  - **automaatne paus, kui laos otsas**.
- Kui menüüd hallatakse kassasüsteemist, on platvormi menüü lukus.
- **QR- ja veebitellimus:** nähtavad rühmad ja tooted, müügi tüüp (eeltellimus / lauast), keskmine valmistamisaeg, QR-tellimise paus kuni kellaajani.
- **AXAR (#40, #18):** üks menüü-register, kus **kanal** on omadus (kassa, QR, veeb, kuller X). Saadavus ja paus on reaalajas sündmused
  (`catalog.availability.changed`), mitte iga kanali eraldi seadistus.

## 8. P1-1 ulatus — LUKUS (omaniku vastuste järel, 26.11.2026)

P1-1 jaguneb kolmeks väikeseks PR-iks (reegel: üks väike PR sammu kohta).

**P1-1a — tooted, rühmad, hinnakirjad, neli silma**
1. Tooterühmade puu (rühma vaikeväärtused: maksukategooria, müügipunktid).
2. Tootekaart (§2): nimi, lühend, rühm, tüüp (`standard`, `system`, `gift_card` mudelis), number, vöötkoodid, maksukategooria, `stock_mode`, aktiivne, peidetud kassas, kirjeldus.
3. **Hinnakirjad müügipunktide kaupa** + hinnaversioonid (`valid_from`). Hinda pole = selles müügipunktis ei müüda.
4. **Mustand → eelvaade → kinnitus teise inimese poolt → avaldamine**. Sündmus `pricing.price.published`, kooskõlareegel R14.
5. Toote deaktiveerimine ei muuda laoseisu (README L2). Eelvaade näitab avatud tellimusi.
6. Kassa saab muudatused ilma taaskäivituseta. Võrguühenduseta kassa saab need järgmisel ühendusel (J2).

**P1-1b — kaalukaup**
1. Kogus müügi-, tellimus- ja tagastusreal on komakohaga (`qty_micro`). Ühik on tootel (tk / kg).
2. Kassas saab sisestada kaalu käsitsi. Summa ümardatakse sendini rea tasandil.
3. Migratsioon ja vanade ridade teisendus (täisarv → mikroühik), kooskõlareeglid R1–R13 jäävad roheliseks.

**P1-1c — retsepti laiendus**
1. Sisestusühik ja teisendus (g → kg), kadu %, tellimuse tüüp reale.
2. Hoiatused: ühikud ei sobi, omahind 0, toidu omahinna % üle piiri.
3. Sündmus `recipe.version.published`.

**Brauseritest (kogu P1-1):**
1. Kontor loob toote „Seljanka“, hinnaga 6,50 müügipunktis A ja 6,90 müügipunktis B.
2. Teine juht kinnitab hinna.
3. Mõlema müügipunkti kassad näitavad õiget hinda.
4. Salatibaarist müüakse 0,352 kg.
5. Müük kannab retsepti järgi laost maha.
6. Vana müük jääb vana hinnaga.
7. Looja ei saa enda hinnamustandit kinnitada.

**P1-1-st välja:** allergeenid (P2), kinkekaardi müük ja lunastus (P2-9), kaaluga vöötkood ja kaalu ühendus (P2), lisandid ja erisoovid (P1-5 / P2), kassanuppude redaktor (P2-5).

### 8.1 P1-1a valmis — mis tehti teisiti kui ülal kirjas (26.11.2026)

| Teema | Ülal kirjas | Tehtud | Miks |
|---|---|---|---|
| Hind puudub müügipunkti hinnakirjas | „hinda pole = ei müüda“ | **kehtib põhihind**; „ei müüda“ on hinnakirjas eraldi märge (hind tühi) | Muidu peaks iga hinnakirja täitma iga tootega. Nüüd sisestatakse ainult erandid. |
| Neli silma | ainukese omaniku erand | **erandit pole**: enda ettepanekut ei saa kinnitada keegi, ka omanik mitte | Erand on turvarisk. Kui ettevõttes on üks juht, tuleb see lahendada P1-8 õiguste grupis selgesõnaliselt. |
| Hinna kinnitamine | — | Kinnitab ainult **kõigi müügipunktide** juht või omanik (kontori sessioon). Ühe müügipunkti juht ei saa muuta hindu, mis kehtivad ka teistes müügipunktides. | Põhihind ja hinnakirjad mõjutavad mitut müügipunkti. |
| Tagantjärele hind | — | Kinnitamine ei saa hinda tagasi dateerida: kui kehtivuse algus on minevikus, kehtib hind kinnitamise hetkest. | Juba tehtud müüke ei tohi ümber hinnata. |
| Vanad tooted | — | P1-1a eelne hind kirjutatakse esimesel kinnitamisel ajalukku kirjeks „hind enne hinnaversioone“ | R14 peab tõestama ka vanade müükide hinnad. |
| **Võrguühenduseta kassa** | „saab muudatused järgmisel ühendusel“ | **EI OLE TEHTUD.** Keskserveriga ühendatud kassad saavad muudatuse kohe (sündmus → kassa laeb hinnad uuesti). Võrguühenduseta kassa saab praegu ühendudes ainult ajapiirangu. Kui ta müüb vana hinnaga, satub müük juhi läbivaatusele („price_mismatch“) ega lähe kaotsi. | Hindade tõmbamine võrguühenduseta kassasse on sünkroonimise transpordi osa. Lisatud tegevuskavasse sammuna **P1-1d**. |
