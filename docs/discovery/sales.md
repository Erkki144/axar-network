# Discovery — SALES: arved, maksed, soodustused, aruanded

Etapp 2. Allikad: `README.md`. Tegevuskava: **P1-5** (soodustused, makseviisid, tellimuse tüübid), **P1-6** (kontori müügivaated), **P1-2** (tühistus ja kreedit, #48).
AXAR täna: müük, sahtel, sularahatagastus, lauaarve jagamine, simuleeritud kaart, ülekandearve koos panga sidumisega (C01–C07).

## 1. Arved

### 1.1 Turul
| Liik | Kust tuleb | Olekud | Kohustuslikud väljad | Märkused |
|---|---|---|---|---|
| **Kassaarve** | kassa | suletud; tühistatud | — | suletakse makse hetkel |
| **Hulgiarve** (B2B) | kontor | mustand → aktsepteeritud | ostja (äriklient), kuupäev, müügipunkt | read: toode, kogus, hind, soodustus %; ühine soodustus kõigile ridadele |
| **Kreeditarve** | kontor („Krediteeri“) | mustand → aktsepteeritud | — | negatiivsete summadega **koopia**; kirjeldatud ainult hulgiarvele |
| **Koondarve** | kontor | mustand → aktsepteeritud | ostja, kuupäev, müügipunkt | koondab kliendi perioodi arved; **tühistamine ei tühista koondatud arveid** |
| **Müügitellimus** | kontor / kassa | salvestatud → aktsepteeritud = hulgiarve | klient, kuupäev, hinnakiri, müügipunkt | nõutav ettemaks %; ettemaksu sisestamine loob taustal ettemaksuarve |
| **Ettemaksuarve** | automaatne | — | — | kustutamine tühistab selle |

- **Arvete loend:** filtrid periood, vahetus, summa, müügipunkt, teenindaja, toode, makseviis ja klient. Arve detailis on üldinfo, read koos laost mahakandmise märke ja soodustusega ning maksed.
- **Maksete muutmine tagantjärele:** makseviisi, summa ja makse lisamine või eemaldamine. **Maksete summa peab võrduma arve summaga.** Muudatus kajastub aruannetes.
- **Tühistamine:** kasutaja valib, millised read lähevad lattu tagasi. Valmistatud toitu tagasi ei kanta, see tuleb maha kanda.
- **E-post, PDF, trükk.** E-arve riigi e-arveldaja kaudu, iga arve eraldi.
- **Puudub:**
  - olekuväli;
  - numeratsiooni kirjeldus;
  - käibemaksu jaotus;
  - tühistuse põhjus;
  - kas arve võib kuuluda kahte koondarvesse;
  - laoreserveering tellimusel.

### 1.2 AXAR-i otsused
| Teema | Otsus | Samm |
|---|---|---|
| Arve olekud | selgesõnaline olekumasin: `draft → issued → (partially_paid) → paid`; `issued → credited` ainult kreeditarvega; `cancelled` ainult mustandile | P1-6, P1-2 |
| Kassaarve parandus | **ei muudeta**: makseviisi muutus = makse tühistus + uus makse (mõlemad sündmustena); tühistus/kreedit = vastupidine dokument (J7) | P1-2 |
| Ridade laokäsitlus tühistusel | rea kaupa: tagasi lattu (ainult pakendatud) / mahakandmine põhjusega / liikumist pole — turu „vali read“ on sama mõte, aga meil kohustuslik ja auditeeritud | P1-2 (#48) |
| Tühistuse põhjus | kohustuslik valik + vaba tekst; aruandes | P1-2 |
| Koondarve | arve saab kuuluda **ainult ühte** koondarvesse; koondatud arvel on viide; koondarve tühistus vabastab arved | P1-6 |
| Müügitellimus + ettemaks | ettemaks on **kohustus** kuni arveni; tellimus võib **reserveerida** laoseisu (valikuline) | P2 |
| Numeratsioon | seeria juriidilise isiku + dokumendiliigi kaupa, lünkadeta (meil juba kassa kaupa, C05) | P1-6 |
| E-arve | automaatne, kui kliendil on e-arve kanal; olek arvel | P1-3 |

## 2. Makseviisid

**Turul:**
- Süsteemsed makseviisid on muutmatud.
- Kasutaja saab lisada **vabu makseviise**. Väljad: nimi, „maksev“ (kui ei ole maksev, on arve maksmata, nt ülekanne), grupi tähis aruannete jaoks, lisainfo nõue ja kiirnupp kassas.
- Makseviisi saab kassa kaupa välja lülitada või keelata ainult osamakse.
- Vaba makseviisi kasutatakse ka siis, kui kaart maksti kassaga ühendamata terminalis (käsitsi kanne).

**AXAR (P1-5):**

| Väli | Tähendus |
|---|---|
| tüüp | `cash` / `card_integrated` / `card_manual` / `transfer` (nõuab klienti, tekitab nõude) / `voucher` / `gift_card` / `room_charge` / `courier_prepaid` / `other` |
| grupp aruandes | vaba silt |
| lubatud kassad ja müügipunktid | — |
| osamakse lubatud | — |
| jootraha lubatud | — |
| lisainfo nõue | nt viimased 4 numbrit |
| raamatupidamise konto | P1-3 |
| arhiveeritud | kasutatud makseviisi ei kustutata |

- **Reegel R3 jääb kehtima:** maksed katavad arve täpselt.
- **Käsitsi kaardimakse on eraldi tüüp**, et kooskõlakontroll teaks, et terminali tehingut pole oodata.

## 3. Soodustused

### 3.1 Turul (täpne loogika)
- **Rakendumine:**
  - **käsitsi**: teenindaja lisab, või rakendub kliendikaardi registreerimisel;
  - **automaatne**: ajaline (päevad, kuupäevad, kellaaeg), summapõhine (lingitud toodete summa üle piiri) või kogusepõhine (lingitud toodete arv).
- **Arvutamise alus:**
  - protsent;
  - hind teisest hinnakirjast;
  - summa rea kohta;
  - uus hind rea kohta;
  - kindel summa arvelt;
  - „protsent kuni“: teenindaja sisestab ise, kuni lubatud maksimumini.
- **Tingimused:**
  - kehtib koos teistega (kui ei kehti, valitakse **kliendile soodsaim**);
  - nähtav kassas;
  - ainult kliendikaardiga;
  - sünnipäev ± X päeva;
  - kasutusviis: annab soodustust või **kogub boonust**.
- **Ulatus:**
  - kogu arve, kõik tooted kõigis või valitud müügipunktides;
  - valitud tooted, sealhulgas **automaatne lisamine uutele toodetele**;
  - rea tasandil.
- **Kliendid:** seo automaatselt uute era- või äriklientidega. Kliendil on soodustuste **prioriteet** (lohistatav järjekord). Kui esimene ei sobi, proovitakse järgmist.
- **Õiguste grupi piirang:** nt suur soodustus ainult vahetuse juhile.
- **Järjekord kassas:** soodustus enne vahearvet ja enne arve jagamist. Pärast jagamist on read lukus.
- **Sooduskoodid:**
  - käsitsi koodid: kood, kehtivus, soodustus, maksimaalne lunastuste arv;
  - automaatsed koodid tšekil: tingimus on min summa või kogus; võib anda koodi iga summa kordse kohta; ainult püsiklientidele;
  - kehtivus päevades või kuupäevadega;
  - soodustus peab olema märgitud „kehtib ainult koodiga“, muidu rakendub see kõigile.
- **Ebaselge:** näites „2 = 3“ annab 5 toote korral soodustuse 3 tootele. Ümardus ja käibemaksu jaotus pole kirjeldatud.

### 3.2 AXAR-i disain (P1-5)
| Register | Väljad |
|---|---|
| `discount_rule` (versioonitud) | nimi; rakendumine `manual` / `auto`; käivitaja `time` / `amount` / `quantity` / `code` / `customer` / `birthday`; arvutus `percent` / `amount_per_line` / `amount_per_bill` / `fixed_price` / `price_list` / `percent_up_to`; väärtus; kehtivus (päevad, kellaaeg, periood); ulatus (müügipunktid, rühmad, tooted, sh / v.a); kombineeritav; nähtav kassas; ainult kaardiga; lubatud õiguste grupid; prioriteet; tulemus `discount` / `bonus` |
| `promo_code` | kood (unikaalne), reegel, kehtivus, max lunastusi, lunastatud; automaatkoodi reegel eraldi |

**Arvutusreeglid** (kirjutame lahti, turul on need lahtised):
1. Rea soodustused arvutatakse enne arve soodustusi. Arve soodustus jaotatakse ridadele proportsionaalselt, sest käibemaks peab olema rea kaupa õige.
2. Kui soodustus pole kombineeritav, valitakse kliendile soodsaim. Võrdse tulemuse korral otsustab prioriteet.
3. Kogusepõhise soodustuse „N + 1“ korral saab soodustuse odavaim toode igas N + 1 komplektis. Valem on dokumenteeritud ja testitud.
4. Ümardus on rea tasandil sendini. Arve kogusumma jääk läheb viimasele reale.
5. Müügireal hoitakse koopiana soodustuse reegli versiooni, summat ja kinnitajat (kui soodustus nõudis kõrgemat õigust).
6. Uus kooskõlareegel R15: rea neto = hind × kogus − soodustused. Arve soodustus = ridade soodustuste summa.

## 4. Aruanded

| Aruanne turul | Sisu | AXAR |
|---|---|---|
| Töölauad | 12 vidinatüüpi (müük, külastajad, keskmine arve, TOP-tooted), filtrid müügipunkti ja müüja järgi; looja muudab, kõik näevad | P3 (#32 CONTROL) |
| Läbimüük | periood + kellaaeg, **võrdlusperiood**, grupeerimine, filtrid, salvestatud vaated, eksport | P1-6 |
| Makseviisid | makseviisi järgi; grupeeri müügipunkti, müüja ja kliendi järgi; filtrid: ainult hulgiarved, ainult mitme makseviisiga arved, **ainult summavahega arved** | P1-6. Summavahega arveid AXAR-is olla ei saa (R3). |
| Müügi koond | KM-ga ja KM-ta müük, KM, tühistatud, krediteeritud, soodustused; tellimuse tüübid; makseviisid grupi järgi; rühmad | P1-6; **täpsustame**, kas tühistus ja kreedit on summast maha arvatud (turul pole öeldud) |
| Vahetused | müüja vahetus ja kassavahetus; filtrid; peab olema vs loetud | P1-6 (C02 on olemas) |
| Soodusmüük | nagu läbimüük | P1-6 |
| Statistika | graafik, mõõdikud, lõiked | P3 |
| Menüü optimeerimine | populaarsus × kate, Kasavana-Smith | P3-2 |

**AXAR-i aruannete põhimõtted:**
- iga aruande number on kooskõlas teistega: koond = läbimüük = makseviisid = vahetused samal perioodil;
- äripäeva algus on asukoha seade;
- aruanne ütleb, millise andmeseisu järgi see tehti;
- iga mõõdiku valem on aruande juures lahti kirjutatud.
