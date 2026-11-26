# Discovery — INVENTORY: laodokumendid, inventuur, laoseis, tarnijad

Etapp 2. Allikad: `README.md`. Tegevuskava: **P1-7** (inventuur, mahakandmine, liikumine), **P2-7** (taastellimine).
AXAR täna (C13, C14): laokaup, ladu, tarnija, saateleht / ostuarve (mustand → postitatud), tagastus tarnijale, liikuv keskmine omahind (ADR-0011),
versioonitud retsept, tootmispartii, müügi mahakandmine sündmuse kaudu, kaubakulu, kooskõlareeglid R9–R10.

## 1. Dokumendid — ühine muster turul
Kõigil laodokumentidel on sama elutsükkel: **mustand** (muudetav, kustutatav) → **„Aktsepteeri“** (laoseis muutub, dokument lukustub).
Parandus: krediteeri (negatiivne koopia, mis tuleb ka aktsepteerida) või kopeeri dokument ja aktsepteeri koopia.
Kui number jäetakse tühjaks, genereeritakse see seadetes oleva valemi järgi.

| Dokument | Päis | Read | Erireeglid |
|---|---|---|---|
| **Sisseost** (saateleht / ostuarve / ostutellimus / kreeditarve) | number, kuupäev (tagantjärele lubatud), tarnija, sihtladu, maksepäev (info), **transport** (jaotatakse ridadele võrdselt ja lisatakse hinnale), **soodustus** (jaotatakse võrdselt ja lahutatakse), info, „ära ekspordi raamatupidamisse“, manus | kogus, ühiku hind KM-ta, summa (sisesta üks, teine arvutatakse) | aktsepteeritud dokumenti ei saa muuta |
| **Liikumine** | number, kuupäev, lähteladu, sihtladu, info, manus | kogus | maksumus pole kirjeldatud |
| **Mahakandmine** | number, kuupäev, ladu, info (põhjus) | kogus, **„lahka retsept“** (kantakse maha koostisosad, mitte toode) | kulu läheb materjalikuluks |
| **Tootmine** | number, kuupäev, lähteladu (komponendid), sihtladu (valmistoode); võivad olla samad | retseptitoode, kogus | müügil valmistoote mahakandmine nõuab **tootja eriseadistust** |
| **Inventuur** | ladu, **seisu aeg: praegu või kindel minevikuhetk** | loetud kogus, erinevus, kommentaar; lisa toode / lisa retsept; „täida nullidega“ | tühi rida = lugemata (seis ei muutu); hoiatus aktsepteerimata saatelehtede kohta |
| **Hinnainventuur** | ladu | uus laohind või % muutus (ainult tühjad / kõik) | — |
| **Üksiku toote korrektsioon** | laoseisu vaatest või tootekaardilt | uus kogus või hind + kommentaar | **teeb automaatse inventuuri praeguse hetkega**, mille järel ei saa enam tagantjärele inventuuri teha |

### Impordid
- **CSV saateleht:** kohustuslikud vöötkood või number, kogus ja ostuhind. Lisaks ladu ja tarnija. Sobitamine võib olla range või lõtv.
  Valikud: luua tarnija või toode, uued tooted mitteaktiivseks, katkesta vea korral. Profiilid.
  **Ilma katkestamise valikuta imporditakse vaikselt osaliselt.**
- **PDF- või pildiarve tehisaruga:** tarnija tuvastatakse, veerud seadistatakse esimesel korral, kontrollvaade näitab summade erinevust.
  Seejärel ridade sidumine tootega koos **suhtega** (500 g pakk → 0,5 kg). Aktsepteerida saab alles siis, kui kõik read on seotud.
  Ülevaatuse saab tarnija kaupa välja lülitada. **Andmetöötluse asukoht on teadmata.**
- **EDI (Telema):** tarnija ekspordikood ja lao liidesekood (tõstutundlik). Sidumata read on punased ja blokeerivad aktsepteerimise. Sama suhte loogika.

## 2. Vaated ja aruanded turul
- **Laoseis:** filtrid rühm, kogus (> 0, = 0, < 0), ladu, atribuudid, seisma jäänud. **Ajalooline seis** valitud hetkel.
  „Kustuta nullread“ (tulevad liikumisel tagasi). Rea tegevused: seisma jäänud märge, tooteajalugu, korrektsioon.
  **Negatiivne laoseis on lubatud.** Juhendi järgi on selle tavaline põhjus müük valest laost.
- **Tooteajalugu:** iga toimingu kuupäev, dokument, kasutaja ning seis ja laohind pärast toimingut.
- **Lao koondaruanne:** koguses või laohindades, ainult liikunud tooted, mitteaktiivsed tooted, lõiked, eksport.
- **Seisma jäänud tooted:**
  - laovaru päevades = seis ÷ müük päevas;
  - saadaval olnud päevad;
  - seisma jääv kogus ja summa;
  - massiline märge ja etiketid;
  - seos soodustustega.
- **Taastellimine:**
  - piir ja tellitav kogus toote ja lao kaupa;
  - nimekirjas on tooted, mille seis on alla piiri;
  - tarnija valitakse rea kaupa, saab määrata vaiketarnija;
  - kogus avatud dokumentidel;
  - müük päevas;
  - tulemuseks ostutellimused.

  **Puudub:** tarneaeg, varu ja prognoos.
- **Ladu ja toode:** millisest laost müük maha kantakse, **müügipunkti kaupa**, ja vaikeladu. Hulgitegevused.

## 3. Mis valutab turul
1. **Omahinna meetod pole kirjas** (keskmine? FIFO?). Transpordi jaotus ridadele on võrdne, mitte väärtuse järgi.
2. **Negatiivne laoseis** on lubatud ilma reeglita.
3. Üksiku toote korrektsioon lukustab inventuuri kuupäeva (README L3).
4. Ajalooline seis on hetkevõte (README L10).
5. Tootmisel valmistoote mahakandmine müügil nõuab tootja tuge.
6. Osaline import ilma hoiatuseta.
7. Inventuuris tühjendab „täida nullidega“ kergesti kõik lugemata read.

## 4. AXAR-i disain

| Teema | Otsus | Samm |
|---|---|---|
| Dokumendi elutsükkel | `draft → posted`; postitatud dokumenti ei muudeta; parandus = vastupidine dokument viitega (nagu turul, aga **ühtne kõigile dokumentidele** ja sündmusega `stock.movement.posted`) | P1-7 |
| Omahind | liikuv keskmine (ADR-0011, olemas); **transport ja soodustus jaotatakse väärtuse järgi**, mitte võrdselt; eelvaade näitab mõju omahinnale | P1-7 |
| Negatiivne laoseis | poliitika **müügipunkti ja lao kaupa**: luba / hoiata / keela; negatiivne liikumine on märgitud (`negative_stock`, olemas) ja aruandes; järgmine sisseost korrigeerib omahinna kaubakuluga (olemas) | P1-7 |
| Inventuur | seisu aeg on dokumendi omadus; ajalugu arvutatakse liikumistest, **lukustusi ei ole**; lugemata ja nulliks loetud read on selgelt eristatud (null vajab kinnitust); sulgemine eraldi õigusega; erinevus koguses ja rahas, kulu konto raamatupidamisse | P1-7 |
| Üksiku toote korrektsioon | = ühe reaga inventuur oma kuupäevaga, mitte eriline tee | P1-7 |
| Mahakandmine | põhjus kohustuslik (rikneb, praak, personal, proov…); retseptitoote korral kantakse maha koostisosad versiooni järgi | P1-7 |
| Liikumine | maksumus liigub keskmise omahinnaga; ülekandetellimus (taotlus → kinnitus → liikumine) P2 | P1-7 |
| Tootmine | olemas (partii); valmistoote mahakandmine müügil on **toote seade** (`stock_mode = item` pooltootele), mitte tootja tugi | olemas + P1-1 |
| Tarnija seos | tarnija kood, tarnija tootenimi, **pakendi suhe**, viimane hind ja aeg, vaiketarnija; sama seos teenindab CSV-, PDF- ja EDI-importi | P1-7 / P2-8 |
| Ostuarve import | ühine sobitamise samm kõigile allikatele: sidumata read blokeerivad postitamise; summade kontroll; tehisaru kasutus ainult omaniku nõusolekul ja ELi andmetöötlusega | P2-8 |
| Taastellimine | piir, kogus, pakend + **tarneaeg ja müügikiirus** → soovitus; tarnija kaupa ostutellimuse mustandid | P2-7 |
| Seisma jäänud | arvutatud vaade (mitte käsitsi märge), soodustusega seos | P2-7 |
| Laoseis igal hetkel | liikumiste summa (R10 on olemas) | P1-7 |
