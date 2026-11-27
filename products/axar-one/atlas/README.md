# AXAR ATLAS v0.1 — süsteemi digitaalne kaksik

**Staatus:** klikitav ja testitav **staatiline** arhitektuurikaart. See **EI OLE** ühendatud live-serveriga ega näita päris raha-/laoandmeid. Ei sisalda API paroole, token'eid ega makseadapterit. AXAR NERVE (#42) ja süsteemiauditi (#60) jaoks ehitatud eraldi inspektorirakendus, mis ei muuda kassat ega kontorit. POS/Office eraldi tööakende nõue #76 jääb jõusse.

## Ava arvutis

See v0.1 ei vaja Node'i, Reacti, andmebaasi ega internetti.

- Ava products/axar-one/atlas/index.html Chrome'i või Edge'iga. Kõik staatilised skriptid laaditakse lokaalselt.
- Või repo juurkaustast käivita: python -m http.server 8090 --directory products/axar-one/atlas ; siis ava http://127.0.0.1:8090/ .

**Navigatsioon:** Arhitektuuris klõpsa sõlmele, paremal näed vastutajat, lähtekoodi ja issue'sid; otsing, ühenduse tüübi filter, suurendamine/lohistamine, Tab+Enter. Andmete teekonnad näitavad äriprotsessi kaasatud sõlmi. Andmeregistrid näitavad tabelit. Valvurid kirjeldavad senise tõenduse piire.

**Sisu:** 20 käsitsi kaardistatud sõlme ja 28 ühendust, 4 protsessiteekonda, 8 kontrollväravat. Koodis olemas / osaliselt / kavandatud / prototüüp tähistab koodi või planeeringu olemasolu, **mitte käituse tervist**. graph-data.js on versioonitud hetkepilt ja **ei uuene GitHubi muudatustega automaatselt**. Avatud PR-ide funktsioone ei loeta main'is valminuks.

## Testid

POS projektikaustas:

    python -m pytest tests/test_atlas_model.py
    python -m pytest tests_e2e/test_atlas_interactive.py

Playwright Chromium peab olema paigaldatud, vt requirements-e2e.txt. Olemasolev .github/workflows/axar-pos.yml käivitab mõlemad töövood, sest atlas asub products/axar-one/ ja testid products/axar-pos/ all. CI roheline kinnitab konkreetseid teste, mitte tootmiskõlblikkust.

Mudelivalvur kontrollib sõlmede unikaalsust, viiteid ja olemasolevaid lähtefaile. Brauseritestid kontrollivad 20 sõlme, 28 ühendust, otsingut, detailivaadet, suurendust, filtreid, töövoogu ja mobiili.

## Phase 2: automaatselt genereeritud graaf ja päris NERVE seire

**See pole v0.1-s valmis.** Claude + Codex teevad järgmisena eraldi piiritletud PR-id:

1. Source graph generator: loe FastAPI tegelik route register (method/path/realm/owner), SQLAlchemy Base.metadata tabelid ning RLS/migratsioonid, versioonitud sündmused/skeemid, tarbijate register ja seadmete lepingud. Ära arva omaniku nime klassi järgi: igale moodulile peab olema eraldi versioneeritud omaniku manifest. Tõendamata uut route/table/event lubav valvur peab FAIL-ima.
2. Build snapshot: iga kaardi juures build SHA, migration head, environment, snapshot timestamp ja source test evidence. Tootmiskeskkonna pilt tuleb ainult reaalsest release'ist; avatud PR overlay on selgelt eraldi.
3. Eraldi turvatud admin ATLAS: oma URL ja OFFICE/admin MFA, per-tenant/per-entity õigused, RLS, ilma POS PIN ligipääsuta. Avalik static prototüüp ei tohi saada päris klientide andmeid. DEMO kasutab vaid sünteetilisi testandmeid.
4. Live OpenTelemetry/Grafana: command_id → sale_id → event_id → consumer → delivery job → provider ACK. Mõõda queue age, latest unacked, dead letter, card UNKNOWN, printer ACK, R1–R14 erinevused, device last_seen, edge disk, backup PITR age. PIN-i, isikuandmeid ega maksetokenit ei pane trace payload'i.
5. SYSTEM GOVERNOR: üks release gate, mis kontrollib API sessioonide lahusust (#76), route ownership, RLS, event contract, table ownership, migration/ADR open-PR collison, E2E ledger ja crash testid, hardware HIL, restore ja security. Kui GitHub API ei vasta, pole release green.
6. Reaalse oleku allikas: iga mooduli live värv peab tulenema konkreetsest mõõdikust + timestampist; UNKNOWN/STALE on omaette olek. Rikke puhul issue/runbook/owner; graafilt ei tehta makset ega laokannet ilma volitatud käsuta.

## Vastutajad

| Osa | Teostaja |
| --- | --- |
| Kassa ja kontori äriloogika, moodulite integratsioon | Claude'i aktiivsed PR-id |
| ATLAS v0.1 graaf + lähteandmed + interaktsioon + testid | AION/Codex |
| Source generator ja API/DB/event omanikumanifest | Claude koos sõltumatu koodireview'ga |
| Päris seire, observability ja System Governor | Claude + AION/Codex + sõltumatu SRE/QA |
| Ärilised ja tootmisjuurutuse otsused | Omanik |

## Lähteviited

- products/axar-one/architecture/03-data-ownership.md
- products/axar-one/architecture/04-event-catalog.md
- products/axar-one/architecture/adr/ADR-0002-modular-monolith-postgresql.md
- products/axar-pos/axar_pos: api.py, models.py, events.py, consumers.py, worker.py, sync.py, orders.py, payments.py, inventory.py, auth.py
- #42 NERVE; #60 süsteemiaudit; #76 POS/Office eraldus; #77 privaatne demo
- Avatud mustand-PR #73, #74 ja #75

Ei lisa uusi mikroteenuseid ega sõnumivahendajat v0.1 jaoks. Ei merge'i ega deploy tootmisesse ilma omaniku nõusolekuta.
