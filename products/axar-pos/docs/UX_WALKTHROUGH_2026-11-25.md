# Usability walkthrough — real server, real browser (25 Nov)

**Setup**
- Server: server-auth mode (enrolled devices, PIN, owner created with the CLI).
- Browser: Chromium via Playwright at 390×844 (phone) and 1024×768 (tablet).
- Flow: two devices, the real API, no mocks.
- Measured: taps and wall time per task (local machine).
- Not tested yet: this is not a study with real staff (#10 needs 20 operators).

## Measured (after fixes)

| Task | Taps | Time (local) |
| --- | --- | --- |
| Enrol device (one-off) | 2 | 0.6 s |
| PIN login (name + 4 digits + OK) | 6 | 0.4 s |
| Open drawer | 3 | 0.1 s |
| 3-item cash sale, quick tender 20 €, receipt, next customer | 7 | 0.8 s |
| Table: choose table, 2 items, send to kitchen | 5 | 0.6 s |
| Switch to kitchen / manager view | 1 | 0.1 / 0.6 s |
| Kitchen ticket reaching another device (stream, no polling) | 0 | < 3 s asserted |

- Same tap counts at both sizes.
- No horizontal scrolling.
- No console errors after the fixes.

## Problems found and fixed

| # | Problem (seen on screenshots) | Fix | Test |
| --- | --- | --- | --- |
| U1 | **Phone:** the basket, total and pay button were ~1,300 px below the menu. The cashier could not see what was in the basket while adding products. | Sticky bar above the bottom navigation: "3 toodet · 9,25 € · Maksa →". Tapping the left side scrolls to the basket. | `test_phone_mini_cart_keeps_total_and_pay_in_view` |
| U2 | A large marketing headline ("Kiire teenindus. Vähem samme.") took ~200 px on every view. | Hidden at ≤1350 px. Work starts at the top. | screenshots |
| U3 | **Tablet:** only 2.5 basket lines were visible; +/− sat on a separate row with 30 px buttons. | One line per item; 40 px buttons; icon hidden on narrow screens. | screenshots |
| U4 | Roles were shown in English ("cashier, waiter"). | Estonian ("kassapidaja, teenindaja"). | `test_phone_mini_cart…` |
| U5 | The kitchen view said "uuenevad iga 4 sekundi järel", which has been wrong since the realtime stream. Developer banners were also shown to staff in server mode. | Banners only in demo mode. | `test_table_bill_survives…` |
| U6 | A cashier saw "Märgi valmis" in the kitchen, and the server would refuse it with 403. | Button hidden without `kitchen.update`; the card says "Valmis märgib köök". The server still enforces the permission. | `test_kitchen_view_respects_permissions` |
| U7 | The kitchen did not show how long a ticket had waited. | Age per ticket ("just nüüd", "7 min"), red from 15 min. | screenshots |
| U8 | The manager view asked for the report even for a cashier (403 in the console). | Not requested without the permission; clear text instead. | `test_kitchen_view_respects_permissions` |
| U9 | **Phone:** no way to lock the till (the lock button sits in the hidden sidebar). | 🔒 in the phone top bar. | screenshots |
| U10 | The PIN field had no label; there was no keypad on a shared touch till. | "PIN" placeholder + large 3×4 keypad (56 px keys); a keyboard still works. | `test_phone_mini_cart…` |

## Not done yet (next)

- Back-office screens for staff, devices and tables. The owner uses the API/CLI today.
- Split bill, move table, void with reason (ADR-0003).
- Kitchen stations and bump bar; sound when a new ticket arrives.
- Estonian/English/Russian switch (#10).
- Real-operator study (#10) and an axe accessibility audit.
