# PR #67 (Ektaco uuring I–IV) → AXAR ONE tegelik seis ja tööjärjekord

Koostatud 26.11.2026. Allikas: PR #67 dokumendid `REFERENCE_EKTACO_WORKFLOW_BLUEPRINT.md` ja `REFERENCE_EKTACO_DEEP_DIVE_02/03/04.md`,
võrreldud AXAR ONE koodiga harus `claude/catalog-pricing-p1-1a` (sisaldab #60 P0-1…P0-3 ja P1-1a).
Reegel: lisame tegevuskavasse **ainult päris puudused**. Kõik, mis juba on mõne sammu all, seotakse selle sammuga.

Olek: **TEHTUD** (koodis ja testitud) · **OSALINE** · **PLAANIS** (tegevuskava samm olemas) · **UUS** (lisatud selle kaardiga).

## 1. Kontrollitud koodiväited (PR #67 → kood)

| PR #67 väide | Kontroll | Tulemus |
|---|---|---|
| E19: müük kannab alati maha müügipunkti **vaikelaost**; toote / koostisosa lao reeglit pole | `inventory.consume_for_sale` kasutab `Warehouse.is_default` | **tõene** → UUS samm P1-7b |
| F01 / E27: sahtli oodatav sularaha = algraha + sularahamüük − tagastused; **müügiväliseid sisse- ja väljamakseid pole** | `services._expected_cash_expr` | **tõene** → UUS samm P1-9 |
| A20: käibemaks peab olema kuupäevaga | `TaxRate.valid_from/valid_to`, 22 % → 24 % alates 01.07.2025 | **juba tehtud** |

## 2. Blueprint (§1–§15) ja kuldsed teekonnad

| Teema | AXAR seis | Samm |
|---|---|---|
| §1 mõisted lahus (Product ≠ InventoryItem ≠ SupplierProduct ≠ RecipeVersion ≠ Batch ≠ MenuEntry ≠ Button) | Product ↔ Item lahus ✅; retseptiversioon ✅; partii ✅; MenuEntry / Button ☐ | P2-5 (#40) |
| §1 müügirea snapshot ≠ kataloog | ✅ (P1-1a: hinnaversioon, R14) | TEHTUD |
| §2 laorežiimid MAKE_TO_ORDER / PREPRODUCED / STOCKED / NON_STOCK | `stock_mode` = recipe / item / none + tootmispartii ✅; topeltmahakandmise keeld R9 ✅ | TEHTUD (nimed erinevad) |
| §2 bruto = neto ÷ (1 − kadu), ühikuteisendus | ☐ | P1-1c |
| §3 ostuarve import: EXTRACTED → MAPPING → TOTALS → DRAFT → POST, duplikaadikaitse | sisestus käsitsi ✅, import ☐ | P2-8 (laiendatud, vt §5) |
| §4 laoliikumised, NULL ≠ 0 inventuuris, põhjusega mahakandmine | ☐ | P1-7 |
| §5 taastellimine koos saabuva kauba, tarneaja ja pakendiga | ☐ | P2-7 |
| §6 hinnakiri müügipunkti ja ajaga; soodustused; lisandid; menüü avaldamine koos ACK-iga | **hinnakiri + aeg + neli silma ✅ (P1-1a)**; soodustused ja lisandid ☐; avaldamise ACK ☐ | P1-5, P1-1d, P2-5 |
| §7 köögipiletite idempotentsus, jaamad, KDS | idempotentne saatmine ✅ (C06); jaamad / printer ☐ | P2-3 |
| §8 parandus ≠ tagastus ≠ kreedit, rea dispositsioon | ☐ | P1-2 (#48) |
| §9 Wolt / Bolt kanalid | ☐ | P3 |
| §10 õigused + ulatus + astmeline kinnitus | rollid + asukoha ulatus + juhi kinnitus ✅; grupid andmetena ☐ | P1-8 |
| §11 raamatupidamise ACK, kontoplaan, perioodi sulgemine | järjekord ✅, edastus ☐ | P1-3 |
| §12 offline: päevik ✅, kvarantiin ✅, 72 h ✅; menüü allkirjastatud hetktõmmis ☐ | | P1-1d |
| Kuldne teekond **A** (tellimusena valmistatav) | ahela keskosa ✅ (kassast kaubakulu ja ekspordini); algus (PDF) ja lõpp (ACK) ☐ | P2-8, P1-3 |
| Kuldne teekond **B** (eeltoodetud) | tootmine ✅; ülekanne ja inventuur ☐ | P1-7 |
| Kuldne teekond **C** (erand: maksetulemus teadmata → tagastus → kreedit) | maksetulemus teadmata ✅ (C07); ülejäänu ☐ | P1-2, P1-3 |
| Kuldne teekond **D** (offline + kuller) | offline ✅; kuller ☐ | P3 |
| Kuldne teekond **E** (avaldamine) | **hind müügipunktis A / B ✅ (P1-1a E2E)**; kanalid ja ACK ☐ | P1-1d, P2-5 |

## 3. Süvauuring II (A01–A20)

| ID | Sisu | Seis | Samm |
|---|---|---|---|
| A01 | müügitellimus + ettemaks + lõpparve | ☐ | **UUS P2-11** |
| A02 | koondarve ei tühista algarveid | ☐ | P1-6 |
| A03 | arve omanik / samaaegne avamine | versiooni-CAS ✅, omanik ja üleandmine ☐ | P1-4 |
| A04 | rea jagamine külastajate vahel, käigud | jagatud makse ✅, rea fraktsioonid ☐ | P1-4 |
| A05 | vahearve ≠ fiskaaltšekk; köök ei saadeta topelt | köögi idempotentsus ✅; vahearve ☐ | P1-4 |
| A06 | makseviisi klass ja arvelduse olek (ülekanne = nõue) | ülekanne → nõue ✅ (C07); makseviiside register ☐ | P1-5 |
| A07 | käsitsi kaarditerminal = kinnitamata | ☐ | P1-5 (tüüp `card_manual`) |
| A08 | töötaja vahetus ≠ sahtlisessioon | sahtel ✅, töötaja vahetus ☐ | **UUS P1-9** (koos sularaha liikumistega) |
| A09 | ooteekraan (QDS) ≠ köögiekraan | ☐ | P2-3 |
| A10 | WooCommerce | ☐ | P3 |
| A11 | Telema / EDI sidumine, suhe 0,240 kg | ☐ | P2-8 |
| A12 | kinkekaart kui kohustuste register | ☐ | P2-9 |
| A13 | lojaalsus (punktid ≠ krediit ≠ soodustus) | ☐ | P2-6 |
| A14 | sooduskoodid, lunastuse piir | ☐ | P1-5 |
| A15 | aruannete mõõdikute sõnastik | ☐ | P1-6 |
| A16 | auditi säilitus (turul ~3 kuud) | auditilogi ✅, säilituspoliitika ☐ | P1-8 |
| A17 | seadme võimekuse olek, mitte ainult online / offline | ☐ | P2-1 |
| A18 | konfiguratsiooni versioon + ACK + rollback | ☐ | P1-8 / P2-3 |
| A19 | saaliplaani versioon | lauad ✅, plaan ☐ | P2 |
| A20 | KM kuupäevaga | ✅ | TEHTUD |

## 4. Süvauuringud III–IV (E19–E34)

| ID | Serv | Seis | Samm |
|---|---|---|---|
| E19 | toote / koostisosa lao reegel → laomahakandmine | ☐ (kontrollitud) | **UUS P1-7b** |
| E20 | import: puuduv ≠ tühi ≠ NULL, eelvaade + kinnitus | ☐ | P2-8 (lisatud tingimused) |
| E21 | müügikiirus saadavuspäevade järgi | ☐ | P2-7 (lisatud valem) |
| E22 | maksumuutus kogu ahelas (hind, kassa, eksport) | määrad ✅; ekspordi vastendus ☐ | P1-3 |
| E23 | e-arve olekumasin | ☐ | P1-3 |
| E24 | hooajakassa peatamine ilma kirjeid kaotamata | ☐ | **UUS P2-10** |
| E25 | aruannete ühised mõõdikud | ☐ | P1-6 |
| E26 | CRM massmuudatused ja nõusolekud | ☐ | P2-6 |
| E27 | sahtli müügivälised sularaha liikumised | ☐ (kontrollitud) | **UUS P1-9** |
| E28 | taastamine ei kustuta päevikut ega avatud arveid | AXAR-is pole „lähtesta arved“ nuppu; edge-päevikut ei kustutata ✅; RecoveryGuard ☐ | P2-1 (vastuvõtutingimus) |
| E29 | terminal → pakkuja partii → pank (tasud) | ☐ | P2-4 + P1-3 |
| E30 | variant ≠ lisand ≠ erisoov; „kehtib kõigile“ ainult selgesõnaliselt | ☐ | P1-5 (lisandid), P3 (variandid) |
| E31 | õiguste mallid, kasutusel grupi kustutamise keeld | ☐ | P1-8 |
| E32 | müügipunkti valmisoleku kontroll enne esimest müüki | ☐ | **UUS P1-10** |
| E33 | tšeki e-kiri: korduskoopia, mitte uus müük | ☐ | P1-6 |
| E34 | kanalite eraldus (Bolt ≠ Wolt) | ☐ | P3 |

## 5. Mida see kaart tegevuskavas muudab
- **Uued sammud:** P1-7b (lao allikareegel), P1-9 (sularaha sisse ja välja + töötaja vahetus), P1-10 (müügipunkti valmisolek), P2-10 (kassa elutsükkel ja hooajaline peatamine), P2-11 (müügitellimus ja ettemaks).
- **Täiendatud sammud:**
  - P2-8 import: puuduv ≠ tühi ≠ NULL, faili räsi, duplikaadikaitse;
  - P2-7: müügikiirus saadavuspäevade järgi;
  - P2-1: RecoveryGuard;
  - P1-5: makseviisi tüüp `card_manual` on kinnitamata, kuni see sobitatakse.
- **Muutmata:** P1-1a on valmis ja katab blueprint §6 hinnaosa ning kuldse teekonna E hinnaosa.

---

## 6. PR #67 uuendus (26.11 õhtu): 52 servaga register `docs/ektaco/AXAR_GAP_REGISTER.json`, süvauuringud V–VI, issue'd #68–#70

Register ise ütleb, et see on vastuvõtu kontrollnimekiri, mitte teostuse tõend. Allpool on iga serva seis **koodis**.
Arvestuse alus on haru `claude/catalog-pricing-p1-1a`, mis sisaldab #60 P0-1…P0-3 ja P1-1a.

| Serv | Nõue (lühidalt) | AXAR seis | Tõend / samm |
|---|---|---|---|
| E1 | kontoris loodud toode jõuab kassasse | **TEHTUD (P1-1a)**, nupuredaktorita | `tests_e2e/test_catalog_ui.py` |
| E2 | kehtiv hind → muutumatu müügirida | **TEHTUD (P1-1a)** + R14 | `tests/test_catalog_pricing.py` |
| E3 | retsept → laokulu, üks kord | TEHTUD (C14, R9) | `test_inventory.py` |
| E4 | menüü avaldamine POS / TV koos ACK-iga | PLAANIS | P2-5 (#40), P1-1d |
| E5 | sularahamüük, idempotentne | TEHTUD (C01) | `test_sales.py` |
| E6 | kadunud ACK ei tekita teist köögipiletit | TEHTUD (C06) | `test_orders.py` |
| E7 | jagatud makse → üks müük | TEHTUD, simuleeritud (C07) | `test_payments.py` |
| E8 | päris terminal + teadmata olek | OSALINE: adapter ✅ (P0-3), pakkuja ☐ | P2-4 |
| E9 | ülekanne → nõue → pank | OSALINE: API ✅, kasutajaliides ☐ | P1-6 |
| E10 | sularahatagastus ≤ makstud | TEHTUD (C03) | `test_refunds.py` |
| E11 | tagastus → lattu / mahakandmine | PLAANIS | P1-2 (#48) |
| E12 | müük / tagastus → raamatupidamise järjekord | TEHTUD (R8) | `test_events.py` |
| E13 | raamatupidamise ACK | PLAANIS | P1-3 |
| E14 | sahtli oodatav raha = tegelikud liikumised | ☐ (kontrollitud) | **P1-9 = #68, prioriteet P0** |
| E15 | aruanded klapivad äripäeva ja KM-iga | OSALINE (päevaaruanne) | P1-6 |
| E16 | offline → keskserver, ei kao ega dubleeru | TEHTUD (C18, R13) | `test_offline_sync.py` |
| E17 | ekraan jätkab sündmuste kursorist | TEHTUD (SSE, `?after=`) | `test_cross_device_orders.py` |
| E18 | tarnija arve ei postitu kontrollimata sidumisega | OSALINE: käsitsi mustand → postitus ✅; impordi sidumine ☐ | P2-8 |
| E19 | lao allikas toote kaupa | ☐ (kontrollitud) | **P1-7b = #69, prioriteet P0** |
| E20 | massimport: eelvaade ja kinnitus | PLAANIS | P2-8 |
| E21 | seisma jäänud kaup saadavuspäevade järgi | PLAANIS | P2-7 |
| E22 | maksumuutus kogu ahelas | OSALINE (kuupäevaga määrad ✅) | P1-3 |
| E23 | e-arve: saadetud ≠ kohale jõudnud | PLAANIS | P1-3 |
| E24 | hooajakassa peatamine | PLAANIS | P2-10 |
| E25 | jagatud makse ei dubleeri käivet aruannetes | OSALINE (R3 ✅) | P1-6 |
| E26 | CRM massmuudatus ei anna turundusnõusolekut | PLAANIS | P2-6 |
| E27 | sularaha sisse / välja ja seifi viimine | ☐ | **P1-9 = #68, P0** |
| E28 | taastamine ei kaota avatud arvet ega päevikut | OSALINE: lähtestamisnuppu pole, päevik on räsiahelaga ✅ | P2-1 (RecoveryGuard) |
| E29 | terminal: kinnitatud ≠ arveldatud ≠ tagasinõue, tasud | PLAANIS | P2-4 + P1-3 |
| E30 | variandid, lisandid, märkused → retsept | PLAANIS | P1-5, P3 |
| E31 | õiguste mall → API jõustab ainult lugemise õiguse | OSALINE: rollid API-s ✅ | P1-8 |
| E32 | müügipunkti valmisolek enne esimest müüki | PLAANIS | P1-10 |
| E33 | tšeki uuesti saatmine = koopia | PLAANIS | P1-6 |
| E34 | Wolti ID ei muuda Bolti tellimust | PLAANIS | P3 |
| E35 | QR-tellimuste paus | PLAANIS | P3 (#18) |
| E36 | kinkekaart: aktiveerimata kaarti ei saa kasutada, kohustus | PLAANIS | **P2-9 = #70** |
| E37 | soodustusreegli versioon müügireal | PLAANIS | P1-5 |
| E38 | hindade hulgimuudatus ja tagasivõtt uue versioonina | OSALINE: versioonid + neli silma ✅ (P1-1a); hulgitöö ☐ | **UUS P1-1e** |
| E39 | tarnija pakendi muutus nõuab ülevaatust | PLAANIS | P2-8 |
| E40 | inventuuri lõikeaeg ja paralleelne loendus | PLAANIS | P1-7 |
| E41 | pimekassa: API ei näita summasid | ☐ | P1-8 |
| E42 | seadme sidumine ühekordse koodiga | **TEHTUD** (C10: kood ühekordne, 15 min) | `test_auth.py::test_enrollment_code_is_single_use_and_expires` |
| E43 | retsepti komponendi hulgivahetus → uued versioonid | OSALINE: versioonid ✅, hulgivahetus ☐ | P1-1c |
| E44 | QR: otsas ≠ paus ≠ suletud | PLAANIS | P3 (#18) |
| E45 | iseteeninduskiosk | PLAANIS | P3 (#18) |
| E46 | pilet → läbipääs (valikuline) | PLAANIS (valikuline) | P3 (#31) |
| E47 | jootraha eraldi arvestus | OSALINE: jootraha väli ✅ (C07); tasu ja vastutus ☐ | P1-5 / P1-3 |
| E48 | pandipakend (valikuline) | PLAANIS (valikuline) | P3, raamatupidaja |
| E49 | kaaluga vöötkood | PLAANIS | P1-1b + P2 |
| E50 | SmartPOS seade | PLAANIS | P2-4 |
| E51 | külastajate prognoos → ostusoovitus | PLAANIS | P2-7 / P3 |
| E52 | mobiilne juhi toiming | PLAANIS | P3 (#32) |

**Kokku:** 11 serva on TEHTUD (E1, E2, E3, E5, E6, E7, E10, E12, E16, E17, E42; E7 simuleeritud makseteenusega), 11 on OSALINE (E8, E9, E15, E18, E22, E25, E28, E31, E38, E43, E47), ülejäänud on PLAANIS ja seotud tegevuskava sammuga.
**Uus samm:** **P1-1e**, hindade hulgimuudatus (E38). Uued issue'd #68, #69 ja #70 on seotud olemasolevate sammudega ja #68–#69 prioriteet tõsteti P0-ks, nagu issue'des.
