# Discovery — IAM, CRM, SETTINGS: kasutajad, õigused, kliendid, kassa seaded

Etapp 2. Allikad: `README.md`. Tegevuskava: **P1-8** (kasutajad, õigused), **P2-6** (kliendid), **P2-3** (printerid, KDS), seaded läbivalt.
AXAR täna (C10, C11): seadme registreerimine, PIN, juhi parool + TOTP, 5 fikseeritud rolli asukoha ulatusega, juhi kinnitus, auditisündmused, RLS.

## 1. Kasutajad ja õigused

**Turul:**
- **Kassakasutaja:** ees- ja perenimi (kohustuslik), kassaparool, **üks** õiguste grupp, müügipunktid (uuel kasutajal vaikimisi kõik), laod, aktiivne.
- **Kontorikasutaja** on eraldi tasuline litsents: kassakasutaja + e-post.
  Juhi vahetus käib vana kasutaja kaudu. Uuel kasutajal peab olema ADMIN-õigus, muidu üleanne ebaõnnestub.
- **Õiguste grupp (amet):**
  - nimi, kontori ja kassa õigused eraldi;
  - kontoris kolm taset: puudub / lugemine / lubatud;
  - grupi saab luua teise grupi koopiana;
  - gruppi ei saa kustutada, kui sellel on kasutajaid.

  Kassa õiguste nimekiri (vaadeldud kontoris):
  - vahetused, rahasahtel;
  - arve üleandmine, jagamine, liitmine;
  - ridade kustutamine ja kogus;
  - kustutamine pärast vahearvet või kööki saatmist;
  - tühistus, kreedit, tagastus;
  - hind, omahind, miinusmüük;
  - aruanded;
  - kliendid ja soodustused;
  - inventuur.
- **Seosed:** kasutaja näeb ainult seotud müügipunktide ja ladude andmeid. **Uus müügipunkt või ladu ei ole kellelegi nähtav, kuni see käsitsi lisatakse.**
- **Logid:**
  - kassa tegevused (filtrid kassa, müüja, toode ja tegevus; otsing arve järgi);
  - kontori tegevused (tegevuse tüüp, toode, kasutaja), **hoitakse umbes 3 kuud**;
  - kassa tegevuste koondaruanne pettuste märkamiseks (vaadeldud kontoris).

**AXAR-i disain (P1-8):**

| Teema | Otsus |
|---|---|
| Inimene vs kasutajakonto | `staff` (inimene) ja sisselogimisviisid: PIN / kaart kassas, e-post + parool + TOTP kontoris. **Üks inimene, üks kirje.** Kontorikasutus on õigus, mitte eraldi kasutaja. |
| Õiguste grupp | andmetena: nimi, **iga õiguse kohta tase** (kontoris: puudub / loe / muuda / kinnita; kassas: lubatud / vajab kinnitust / keelatud). Vaikegrupid on praegused 5 rolli. Grupi saab kopeerida. |
| Mitu gruppi | lubatud (nt „kassapidaja“ + „laohoidja“). Tulemus = õiguste ühend. Konflikti korral kehtib kõige rangem „vajab kinnitust“. |
| Ulatus | grupi määramisel valitakse asukohad: **kõik (ka tulevased)** või valitud. Sellega kaob turu „uus asukoht blokeerib kõik“ probleem. |
| „Vajab kinnitust“ | olemas (juhi PIN samas kassas). Laiendame igale kassaõigusele. |
| Omaniku üleanne | omaniku rolli saab anda ainult omanik (kood olemas: `auth.py`); **lisame** reegli „viimast omanikku ei saa eemaldada“; taastamine platvormi toe kaudu koos auditiga |
| Logid | kõik kassa- ja kontoritegevused ühes auditilogis, kuhu kirjeid ainult lisatakse, **≥ 7 a**; vaated kassa / kontor / kasutaja / dokument |
| Pettuste koondaruanne | teenindaja kaupa: kustutatud read (sh pärast vahearvet ja hiljem kui 5 min), koguse vähendamised, tühistused, kreedit, käsitsi soodustused, sahtli avamised; **lävendid ja teavitus** (turul puudub) |

## 2. Kliendid ja lojaalsus

**Turul:**
- **Eraklient:**
  - kohustuslikud ees- ja perenimi;
  - kaardi number (kohustuslik, aga füüsilise kaardi puudumisel genereeritakse);
  - kontaktid, isikukood, sugu, sünnipäev;
  - kehtivus;
  - **kaardi kasutusviis**: sooduskaart / soodus-maksekaart / boonuskaart / krediitkaart;
  - teade kassas;
  - seos ettevõttega;
  - nõusolekud (andmetöötlus, e-posti ja sõnumite turundus).
- **Äriklient:**
  - nimi (kohustuslik), registrikood, KMKR, kontaktid;
  - **nähtav kassas** (ainult siis saab kassas arvele panna);
  - isiklik soodustus;
  - arve ilma käibemaksuta;
  - **hinnaklass**, maksetähtaeg, viivis, saldo.
- **Soodustused kliendil** on prioriteedi järjekorras. Kui esimene ei sobi, rakendub järgmine. Saab massiliselt lisada või eemaldada.
- **Vormi väljade haldus:** väljade järjekord, peitmine ja nähtavus kassas.
- **CRM-import:**
  - kohustuslikud identifikaator (isikukood / kaart / registrikood) ja nimi;
  - valikud: aktiveeri deaktiveeritud, uuenda olemasolevaid, lisa soodustus kõigile, seo ettevõttega, **kirjuta tühjaga üle**, päis, kuupäeva formaat;
  - profiilid.
- **Mobiilne kliendikaart (Wallet):**
  - punktid ja boonuse saldo kaardil;
  - jagamine (keelata, kui boonus on isiklik);
  - sünnipäevateavitus;
  - asukohateavitus (juhend ise hoiatab, et see võib mõjuda jälgimisena);
  - registreerimisleht valitavate väljadega;
  - kutse e-postiga.

**AXAR-i disain (P2-6):**

| Teema | Otsus |
|---|---|
| Klient | üks `customer` register, tüüp: era- või äriklient; ettevõtte töötaja = seos (äriklient → isikud) |
| Identifikaatorid | mitu: kaardi number(id), telefon, e-post, isikukood (valikuline, **minimaalne andmekogumine**), registrikood |
| Rahalised suhted | eraldi kontod: **boonus** (punktid / €), **ettemaks / deposiit**, **krediit** (äriklient, limiit, tähtaeg) — kõik kontoliikumistena, mitte saldo väljana |
| Kaardi kasutusviis | ei ole ühe välja valik. Kliendil võivad olla korraga soodustus, boonus ja krediit. |
| Soodustused | läbi P1-5 reeglite (`customer` / `tier` / `birthday` käivitaja), prioriteet reeglil |
| Nõusolekud | eraldi kirjed: mis, millal, millise kanali kaudu, tagasivõtmine; turundusnimekiri ainult kehtiva nõusolekuga |
| GDPR | eksport, kustutamine / anonüümimine (müügid jäävad, isik mitte), säilitusaeg |
| Import | proovikäivitus, tühi ei kustuta (README L4) |
| Wallet | P3; asukohateavitust **ei tee** |

## 3. Kassa seaded, köök, saaliplaan, e-post, litsentsid

**Turul:**
- **Tšeki kujundus:** printeri tüüp (48 / 42 märki, terminali printer) ja **plokkidest koosnev mall**. Tingimuslikud plokid: klient, vahearve, kasutatud makseviisid.
  Elemendid: tekst, stiil, joondus, vahe ja andmeväljad. Pärast muutmist tuleb kassa taaskäivitada.
- **Konfiguratsioon** vahelehtedel makseviisid, printimine, vahetused, muu ja statistika. Näiteid:
  - makseviisi keelamine kassas;
  - tšeki trükk: alati / küsi / ainult korduskviitungiga;
  - vahetuse alg- ja lõppsumma küsimine;
  - **pimekassa** (vajab ka õiguste muudatust);
  - arvete automaatne jagamine;
  - laua numbri küsimine;
  - roogade järjekord (käigud);
  - arve tunnuse küsimine.
- **KDS ja köögiprinterid:**
  - printeri seaded: nimi, saatev kassa, printiv kassa ja KDS-ekraani number;
  - tellimused suunatakse **ainult tooterühma järgi**;
  - iga printeri ja kassa paari tuleb seadistada eraldi.
- **Saaliplaan:**
  - plaan kuulub müügipunktile; plaane võib olla mitu ja nende järjekord on sama, mis kassas;
  - laua väljad: nimi, värv, kuju, mõõdud, toolid igal küljel;
  - lauda saab kopeerida.
- **E-post:** üks SMTP-postkast kogu süsteemile, ühenduse test. Seda kasutatakse aruannete, arvete ja Walleti kutsete saatmiseks.
- **Litsentsid:**
  - kassa aktiveerimiseks tuleb tõsta litsentside arvu **ja** märkida kassa aktiivseks;
  - kui litsentside arvu ei vähendata, maksad edasi;
  - lepingu ajutine peatamine.
- **Alustamise järjekord** (tootja soovitus):
  1. rühmade puu;
  2. tooted ja komponendid;
  3. sisseostud (laoseis ja omahind);
  4. hinnakirjad;
  5. kassapaneelid;
  6. ametid ja kasutajad;
  7. kassa seadistus koos tootja toega.

**AXAR-i disain:**

| Teema | Otsus | Samm |
|---|---|---|
| Seadete mudel | iga seade kuulub ühele moodulile ja **tasandile** (ettevõte → asukoht → kassa), madalam kirjutab üle; muudatus = sündmus, kassa saab järgmisel ühendusel; **ei ole vaja taaskäivitada** | P1-8 |
| Tšeki mall | malli versioonid, eelvaade, seaduse nõutud väljad lukus (ettevõtte andmed, KMKR, KM jaotus, number) | P2-3 |
| Köögi suunamine | jaam (köök, baar, külm, pitsa) → printer ja/või KDS; **reegel: toode > rühm > vaikimisi**; kassa ja printeri paarid tuletatakse asukohast, käsitsi kombinatsioone pole | P2-3 |
| Pimekassa | üks õigus „näeb vahetuse summasid“; tšekk ja aruanne järgivad seda automaatselt | P1-8 |
| Saaliplaan | asukoha plaanid + lauad (olemas: `dining_tables`), lisame kuju, mõõdud ja koha plaanil; laudade hulgiloome | P2 |
| E-post | saatja ettevõtte kaupa; saatmise järjekord korduskatsetega (outbox); ebaõnnestumine on nähtav | P1-6 |
| Litsentsid ja moodulid | platvormi kihis (`11-surfaces-and-realms.md`); kassa aktiivsus ja arveldus on **üks** toiming | platvorm |
| Alustamise viisard | AXAR-i kontoris on samm-sammuline seadistus samas järjekorras, koos kontrollidega: „kassas pole ühtegi hinnaga toodet“, „lao omahind puudub“ jne | P1-8 / piloot |
