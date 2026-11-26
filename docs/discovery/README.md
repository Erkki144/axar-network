# Discovery — turu süsteemi süvaanalüüs (etapp 2)

Protsess: `products/axar-one/architecture/13-delivery-journey.md`. Iga valdkond on eraldi failis.
Iga valdkonna fail kirjeldab sama asja kolmes osas:
1. **kuidas turul tehakse** (väljad, tähendus, reeglid, töövoog);
2. **mis seal valutab** (puudused, ohud);
3. **mida AXAR teeb** (võtab üle / teeb teisiti / jätab välja) ja millise tegevuskava sammu alla see läheb.

## Allikad (26.11.2026)
- **Tootja avalik kasutajajuhend**: 64 kontori ja 7 kassa artiklit, lisaks 7 liidese artiklit. Loetud täielikult ja ümber sõnastatud. Teksti pole kopeeritud.
- **Omaniku konto turu süsteemi kontoris**: ainult vaatamine. Vaadeldud olid päristooted, retseptid, hinnad, soodustus, õiguste grupid, kassapaneel ja aruanded.
  Muutmisvorme ei avata (üks juhuslik muudatus tühistati „Katkesta“ nupuga ja kontrolliti üle).
- **Brauseri päringute aadressid** (ainult URL-id; vastuseid ja sisselogimise tokenit ei loetud).
- **AXAR ONE kood**: `products/axar-pos/axar_pos/models.py` ja selle haru teised moodulid.

## Valdkonnad
| Fail | Valdkond | Tegevuskava |
|---|---|---|
| [catalog.md](catalog.md) | tooted, rühmad, hinnad, retseptid, lisandid, variatsioonid, kinkekaardid, kassanupud, import | P1-1, P1-5, P2-5 |
| [sales.md](sales.md) | arved, hulgi- ja koondarved, müügitellimused, makseviisid, soodustused, sooduskoodid, aruanded | P1-5, P1-6, P1-2 |
| [inventory.md](inventory.md) | sisseost, liikumised, mahakandmine, tootmine, inventuur, laoseis, taastellimine, tarnijad | P1-7, P2-7 |
| [people-customers-settings.md](people-customers-settings.md) | kasutajad, õigused, logid, kliendid ja lojaalsus, kassa seaded, KDS, saaliplaan, e-post, litsentsid | P1-8, P2-6, P2-3 |
| [till-and-integrations.md](till-and-integrations.md) | kassa aktiveerimine, sisselogimine, võrguühenduseta töö, vahetused, arve tegevused, raamatupidamine, e-arve, kullerid, e-pood, EDI, ooteekraan | P2-1, P1-3, P3 |

## Kuidas turu süsteem andmeid hoiab (päringute aadresside põhjal)
- Kontori API (`CC5OfficeAPI2`) on uus kiht **vanema kassasüsteemi andmebaasi** peal. Seadistuste rajad algavad vana toote nimega,
  näiteks `CC4000 && Modules && CCInventory`.
- **Seaded on üks üldine võtme-väärtuse puu** (`CcConfiguration`, loetakse rajade kaupa). Moodulite, laoarvestuse, valuuta ja muu seadistus on kõik samas puus.
  See seletab, miks paljud seaded on sisestatud käsitsi ja miks juhendites on mitu kohta sama asja jaoks (käibemaks tootel, hinnakirjal ja üldseadetes).
- Eraldi ressursid: `ProductRegister` (+ `Barcodes`, `Attributes`, `ProductImage`), `RecipeComponents` ja **pöördvaade** `ComponentInRecipes`
  (mis retseptides see komponent on), `Stores`, `SalePoints`, `Keyboards` (kassanupud), `Campaigns` (soodustused),
  `CcUserGroup` + `Cc5OfficeRights` + `Cc5PosRights`, `DebtManagement`, `TrendFeed/Insights`.
- **Vormide väljade nähtavus on andmetes** (`ComponentVisibility/Forms`): klient saab peita välju, mida ta ei kasuta.
- Kasutaja valib sessioonis ettevõtte (`SetUserCompany`): ühe sisselogimisega saab olla mitu ettevõtet.

## Läbivad õppetunnid (kehtivad kõigile valdkondadele)

| # | Mida turul nägime | AXAR-i põhimõte |
|---|---|---|
| L1 | Käibemaksu saab määrata kolmes kohas ja juhend ei ütle, milline kehtib | **Üks allikas:** maksukategooria tootel + kuupäevaga määrad. Hinnakiri ei sisalda käibemaksu. |
| L2 | Toodet ei saa kustutada; deaktiveerimine **loob vaikimisi inventuuri, mis nullib laoseisu** | Deaktiveerimine ei muuda ladu. Kui laoseis pole null, näidatakse eelvaates hoiatust. |
| L3 | Ühe toote laokorrektsioon **lukustab inventuuri kuupäeva** ja tagantjärele inventuur muutub võimatuks | Korrektsioonid on dokumendid oma kuupäevaga. Ajalugu arvutatakse liikumistest. Lukustusi ei ole peale perioodi sulgemise. |
| L4 | Impordi seade „kirjuta tühjaga üle“ võib andmed kustutada, hoiatust ei ole | Iga import teeb kõigepealt **proovikäivituse** ja näitab erinevusi. Tühi lahter ei tähenda vaikimisi kustutamist. Importi saab tagasi võtta. |
| L5 | Kontori tegevuste logi hoitakse **umbes 3 kuud** | Auditilogi kirjeid ainult lisatakse ja neid hoitakse vähemalt 7 aastat (raamatupidamise seadus). |
| L6 | Kassa seadistuse ja seadmete ühendamiseks on vaja tootja tuge | Kõik seadistatakse kontoris ise. Platvormi tugi aitab, aga ei ole kohustuslik samm. |
| L7 | Uus müügipunkt või ladu **blokeerib vaikselt** kasutajad, kuni need käsitsi lisatakse | Õigused antakse rollide ja **asukohtade ulatusega** („kõik praegused ja tulevased“ või „valitud“). Uus asukoht järgib reeglit. |
| L8 | Pimekassa nõuab nii kassa seadet kui ka õiguste seadet, ja ühe unustamine lekitab summad | Üks seade, üks koht. Õigus „näeb vahetuse summasid“ on ainult õiguste grupis. |
| L9 | Mitu juhendit on tekstiga pildis ja mõni on kopeeritud teisest (Bolt = Wolt tekst) | AXAR-i juhend genereeritakse samast allikast mis kasutajaliides (väljade nimed ja selgitused). Testid kontrollivad, et need ühtivad. |
| L10 | Ajalooline laoseis on hetkevõte, mis ei arvesta tagantjärele sisestatud liikumisi | Laoseis igal ajahetkel = liikumiste summa. Tagantjärele sisestus muudab ajalugu õigesti ja see on auditilogis. |
| L11 | Võrguühenduseta kassas **kaovad köögitellimused ja veebitellimused**, piiri pole kirjas | AXAR: köögipilet trükitakse kohalikult või läheb järjekorda. Veebitellimus ootab serveris. Piirang (72 h) on dokumenteeritud ja testitud. |
| L12 | Raamatupidamise ekspordis ei saa eristada 0 % ja maksuvaba müüki | Maksukategooriad eristavad 0 %, maksuvaba ja maksustamata müüki. |
