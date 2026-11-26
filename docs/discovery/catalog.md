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

## 7. Mis läheb P1-1 alla (ettepanek)

1. Tooterühmade puu ja tootekaart (§2: nimi, lühend, rühm, tüüp `standard` / `system`, number, vöötkoodid, maksukategooria, `stock_mode`, aktiivne, peidetud, kanalid, kirjeldus, allergeenid).
2. Üks hinnakiri + hinnaversioonid `valid_from`-iga, mõju eelvaade, sündmus `pricing.price.published`, reegel R14.
3. Retsepti laiendus: sisestusühik, kadu, hoiatused, avaldamise sündmus.
4. Kassa saab muudatused ilma taaskäivituseta. Võrguühenduseta kassa saab need järgmisel ühendusel (J2).
5. Komakohaga kogus müügireal, kui omanik vastab küsimusele 3 „jah“.
6. Brauseritest: kontor loob toote „Seljanka 6.50“ ja retsepti → kassa näitab seda → müük kannab retsepti järgi maha → vana müük jääb vana hinnaga (DELIVERY_ROADMAP P1-1).
