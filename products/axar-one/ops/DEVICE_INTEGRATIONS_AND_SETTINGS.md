# AXAR ONE — seadmete, perifeeria ja lülituste praktiline võrdlus

**Staatus:** 2026-11-26, avalike tootjajuhendite uuring. Ühtegi riistvara või makseliidesest tehingut **ei ole** selle dokumendiga testitud. Eesmärk on vähendada paigalduse, printeri, kassa ning seadmehalduse läbikukkumisi (#25, #29, #42, #62, #72).

| Valdkond | Tehniline variant | Tõestatud avalik võimekus | AXAR ONE jaoks ohutu lähtevalik ja vaja tõendada |
| --- | --- | --- | --- |
| Windows POS lukustatud töökoht | Microsoft Assigned Access (Edge/UWP), Shell Launcher (desktop) | Ametlikult dokumenteeritud ettevõtte kioskirežiim | Windows edge-kassa puhul alustada ühe kinnitatud POS äpiga; automaatselt käivituv Windows Service (edge API + SQLite), brauser kioskirežiimis, sõlmitud driver install. Kontrollida reboot/update/print/USB, et kasutaja ei pääseks OS-i teistesse kohtadesse. |
| Android POS lukustatud töökoht | Android Enterprise *Lock Task Mode* + Device Policy Controller | Ametlikult toetatud dedicated-device kiosk | Tõendada valitud terminali OS-iga. MDM/device-owner provision ja signed app; app ei tohi töö ajal sleep/reboot tõttu journal'it kaotada. Mitte võtta Android WebView localStorage rahaliseks allikaks. |
| Kohalik tšekiprinter | Epson ePOS SDK | Epsonil avalik JS/API printeri juhtimiseks teatud ühilduvatel TM mudelitel | Üks konkreetne testmudel, driver või bridge. R1 receipt rendering + print job ID. Tegevuse õnnestumine ≠ printeri füüsiline töö; spool ja paper-out/ribbon status. |
| Pilve-/võrguprinter | Star Micronics CloudPRNT | HTTP/JSON printeri poll, olek, print-jobs; MQTT-põhine versioon sõnumipõhise äratusega | Hea kandidaat kaugemate köögiprinterite jaoks, kuid WAN-katkestuse korral kohaliku LAN bridge võimalus tuleb päriselt testida. Kontrollida tootja mudelite täpset tuge ja ACK semantics. |
| Makseterminal / SmartPOS | Acquirer/terminal provider official ECR/SDK | Protokoll, sertifikaat, offline/makse tühistus sõltub konkreetsest partnerist | Valida provider alles ärilise lepinguga; sandbox+physical HIL; identifikaator, idempotency, refund, reversal, unknown, settlement, PCI andmed mitte AXARisse. |
| KDS / QDS / TV | browser + signed client access + SSE | Brauseri lahendus ei vaja kinnise Ektaco protokolli | Status read-only, KDS station ACK ja QDS avalik minimaalne tellimuse ID. TV ei juhi raha/stock'i; pikk WAN downtime näitab viimast kinnitatud versiooni. |
| Kaal / barcode | sertifitseeritud kaal + tootja SDK/serial protocol | Kaalu protokoll on mudelikohane, müügil võivad olla õiguslikud nõuded | Nõuda mudelit, täpsust ja ühikureegleid, numbrite ümardamist, batch/GTIN seost. |

## Seadme olekute sõnastik

\`UNPROVISIONED → ENROLLED → READY → ACTIVE → DEGRADED → OFFLINE → SUSPENDED/RETIRED\`.

Ei piisa ühest rohelisest interneti-ikoonist:
- server connection, local storage ready, journal fsync, outbound sync, printer physical status, kitchen KDS, terminal acquiring, security credential valid, menu version valid, device time trusted.
- igal adapteril oma \`capabilities\`, \`last_seen\`, \`last_ack\`, \`unsupported\`, \`manual_intervention\`, \`retries\`, \`firmware\`, \`serial\` ja \`outlet_binding\`.
- tootmiskassas peab „Kassa valmis” olema **arvutatud sellest võimekuste maatriksist**, mitte kõigi seadmete oletatavast olemasolust.
- offline kaardimakse nupp eemaldada/kinnitada ainult provider-verified approved offline mode korral, mitte automaatselt.
- non-sale cash OUT jaoks turvaline kassa PIN ja seifi üleandmise kinnitus; kaardimaksega selle tegevuse sidumine on vale.

## Uuendused ja juurutuse käigud

1. Tootja mudelid ja support lifetime kirja; installeri SHA256, signed releases, rollback; secret provisioning ja device identity.
2. Üks outlet canary → üks nädal reaalse töö stress → kaks outletit → 10; menüü/terminali muudatustes üksik samm ja rollback plan.
3. Arveldusjärjepidevuse test: app/driver crash, printer paper-out, kassa reboot, 1h võrgukatkestus, tasumise unknown; tehingu ACK alles pärast päris allikat.
4. Seadme retire: kontrolli \`unsynced_count=0\`, receipts ja kassavahetuse close, võta journal'i audit varukoopia, revoke ja wipe õiguspäraselt.
5. Enne printeri või makseterminali ostu tehnilise inseneri 1-leheküljeline provider matrix: mudel, liides, sandbox, offline, driver, garantii, loodud teste tõend, hind ja risk; omanik valib.

## Ametlikud avalikud allikad

- Windows kiosk valikud: https://learn.microsoft.com/en-us/windows/configuration/kiosk-methods
- Microsoft IoT Shell Launcher / Assigned Access: https://learn.microsoft.com/windows/iot/iot-enterprise/customize/kiosk-mode
- Android dedicated devices Lock Task Mode: https://developer.android.com/work/dpc/dedicated-devices/lock-task-mode
- Epson ePOS JS SDK: https://download4.epson.biz/sec_pubs/pos/reference_en/technology/epson_epos_sdk.html
- Star CloudPRNT HTTP poll/status: https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/protocol-reference/http-method-reference/server-polling-post/index.html
- Star MQTT/HTTP comparison: https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/protocol-guide.html
