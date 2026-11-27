window.AXAR_ATLAS_DATA = {
  "meta": {
    "name": "AXAR ATLAS",
    "version": "0.1.0",
    "snapshot": "2026-11-27",
    "source": "AXAR main + avatud PR-ide ja issue'de eristatud kaardistus",
    "runtimeConnected": false,
    "disclaimer": "Arhitektuuri lähtekoodipõhine hetkepilt. Seadmete/teenuste reaalajaolek pole ühendatud. Mustand-PR ei tähenda main'is valminud funktsiooni."
  },
  "statuses": {
    "code": {
      "label": "Koodis olemas",
      "tone": "cyan"
    },
    "partial": {
      "label": "Osaliselt",
      "tone": "amber"
    },
    "planned": {
      "label": "Kavandatud",
      "tone": "slate"
    },
    "prototype": {
      "label": "Prototüüp",
      "tone": "purple"
    }
  },
  "nodes": [
    {
      "id": "pos",
      "title": "AXAR POS",
      "subtitle": "Kassapidaja tööaken",
      "x": 70,
      "y": 125,
      "kind": "interface",
      "status": "partial",
      "owner": "POS / teenindus",
      "description": "Töötav /one/ liides olemas; kontori täielik eraldamine on veel töö #76. Müük ja ostukorv on koodis.",
      "sources": [
        "products/axar-pos/axar_pos/static/one/app.mjs"
      ],
      "issues": [
        68,
        76
      ]
    },
    {
      "id": "office",
      "title": "AXAR OFFICE",
      "subtitle": "Eraldi halduskeskkond",
      "x": 70,
      "y": 270,
      "kind": "interface",
      "status": "planned",
      "owner": "OFFICE / juhtimine",
      "description": "Praeguses mustandis kontori kasutajaliides on POS-i külge ühendatud. Eraldi URL, autentimine ja täielik lahusus on #76.",
      "sources": [],
      "issues": [
        76,
        61,
        40
      ]
    },
    {
      "id": "edge",
      "title": "POS EDGE",
      "subtitle": "Offline-journal ja sync",
      "x": 70,
      "y": 415,
      "kind": "device",
      "status": "partial",
      "owner": "EDGE / seadmed",
      "description": "Journal, sync ja offline piirang on koodis; päris riistvara, SQLCipher ja voolukatkestuste testid puuduvad.",
      "sources": [
        "products/axar-pos/axar_pos/journal.py",
        "products/axar-pos/axar_pos/sync.py",
        "products/axar-pos/axar_pos/offline.py"
      ],
      "issues": [
        29,
        72
      ]
    },
    {
      "id": "atlas",
      "title": "AXAR ATLAS",
      "subtitle": "Visuaalne süsteemikaart",
      "x": 70,
      "y": 660,
      "kind": "governance",
      "status": "prototype",
      "owner": "Arhitekt / AION",
      "description": "See interaktiivne kaart. Praegu staatiline tõenditega hetkpilt, mitte serveri või maksete reaalajas juhtpult.",
      "sources": [
        "products/axar-one/atlas/index.html"
      ],
      "issues": [
        42
      ]
    },
    {
      "id": "auth",
      "title": "IAM / AUTH",
      "subtitle": "Rollid ja sessioonid",
      "x": 360,
      "y": 120,
      "kind": "core",
      "status": "partial",
      "owner": "Turve / IAM",
      "description": "PIN, seadme sidumine ning parool+TOTP on koodis. Kind=till/admin API taseme range eraldus vajab #76 parandust.",
      "sources": [
        "products/axar-pos/axar_pos/auth.py"
      ],
      "issues": [
        34,
        76
      ]
    },
    {
      "id": "api",
      "title": "FASTAPI",
      "subtitle": "Serveri API ja käsud",
      "x": 360,
      "y": 285,
      "kind": "core",
      "status": "code",
      "owner": "API / moodulite omanikud",
      "description": "Üks modulaarsel monoliidil põhinev server; endpointid, volitused ja kassatehingute teenused.",
      "sources": [
        "products/axar-pos/axar_pos/api.py",
        "products/axar-pos/axar_pos/services.py"
      ],
      "issues": [
        60
      ]
    },
    {
      "id": "device",
      "title": "DEVICE LINK",
      "subtitle": "Kassaseade ja printerid",
      "x": 360,
      "y": 500,
      "kind": "device",
      "status": "partial",
      "owner": "EDGE / adapterid",
      "description": "Seadmetokenid ja lokaalne sidumine on olemas. Päris printerite, terminalide ja staatuseadapterite testimine ootab.",
      "sources": [
        "products/axar-pos/axar_pos/auth.py",
        "products/axar-pos/axar_pos/terminals"
      ],
      "issues": [
        25,
        42
      ]
    },
    {
      "id": "db",
      "title": "POSTGRESQL",
      "subtitle": "Keskne tõeallikas",
      "x": 655,
      "y": 230,
      "kind": "core",
      "status": "code",
      "owner": "DATA / finantsarhitekt",
      "description": "SQLAlchemy mudelid, Alembic migratsioonid, tenant-RLS, ühes tehingus kirjed; elava hostingu PITR on puudu.",
      "sources": [
        "products/axar-pos/axar_pos/models.py",
        "products/axar-pos/axar_pos/db.py"
      ],
      "issues": [
        34,
        62
      ]
    },
    {
      "id": "nerve",
      "title": "NERVE EVENTS",
      "subtitle": "Outbox · Inbox · Worker",
      "x": 655,
      "y": 420,
      "kind": "core",
      "status": "code",
      "owner": "NERVE / integraator",
      "description": "Tehinguline outbox, cursor, inbox, retry, poison/dead-letter ja worker lease/fencing on koodis.",
      "sources": [
        "products/axar-pos/axar_pos/events.py",
        "products/axar-pos/axar_pos/consumers.py",
        "products/axar-pos/axar_pos/worker.py"
      ],
      "issues": [
        42
      ]
    },
    {
      "id": "governor",
      "title": "SYSTEM GUARD",
      "subtitle": "Lepingud ja kooskõla",
      "x": 655,
      "y": 670,
      "kind": "governance",
      "status": "partial",
      "owner": "QA / integratsiooniarhitekt",
      "description": "Sündmuste lepingute ja registrite kontrollid on olemas; avatud PR-ide kokkupõrkevalvur PR #74 mustandis. Ühendatud release gate vajab tõendit.",
      "sources": [
        "products/axar-pos/tests/test_event_contracts.py"
      ],
      "issues": [
        60,
        42
      ]
    },
    {
      "id": "catalog",
      "title": "CATALOG",
      "subtitle": "Tooted ja hinnad",
      "x": 950,
      "y": 70,
      "kind": "domain",
      "status": "partial",
      "owner": "MENU / tooteregister",
      "description": "Põhitooted on main'is; täielik kontori tootekaart, hinnaversioonid, hinnakirjad ja R14 on mustand-PR #74.",
      "sources": [
        "products/axar-pos/axar_pos/models.py"
      ],
      "issues": [
        61,
        40
      ]
    },
    {
      "id": "orders",
      "title": "ORDERS",
      "subtitle": "Lauad ja arved",
      "x": 950,
      "y": 185,
      "kind": "domain",
      "status": "code",
      "owner": "ORDERS / teenindus",
      "description": "Tellimuste ja versioonitud arveliinide töö on olemas; keerukamate offline laua-arvete lahendus pole valmis.",
      "sources": [
        "products/axar-pos/axar_pos/orders.py"
      ],
      "issues": [
        29
      ]
    },
    {
      "id": "pay",
      "title": "PAY",
      "subtitle": "Makseolek ja tagastus",
      "x": 950,
      "y": 300,
      "kind": "domain",
      "status": "partial",
      "owner": "PAY / maksesüsteem",
      "description": "Sularaha ja simuleeritud kaardimaksete loogika olemas, kuid päris sertifitseeritud makseterminali ühendust pole.",
      "sources": [
        "products/axar-pos/axar_pos/payments.py"
      ],
      "issues": [
        25
      ]
    },
    {
      "id": "stock",
      "title": "STOCK",
      "subtitle": "Ladude ja retseptide kanded",
      "x": 950,
      "y": 415,
      "kind": "domain",
      "status": "partial",
      "owner": "STOCK / inventuur",
      "description": "Lao- ja retseptikanded olemas; mitme lao õige allika valimise reeglid vajavad täiendust.",
      "sources": [
        "products/axar-pos/axar_pos/inventory.py"
      ],
      "issues": [
        69,
        14
      ]
    },
    {
      "id": "kds",
      "title": "KDS / KITCHEN",
      "subtitle": "Köögitellimused",
      "x": 950,
      "y": 530,
      "kind": "domain",
      "status": "partial",
      "owner": "KDS / köök",
      "description": "Köögitellimuse salvestus ja olekud koodis; füüsilise printeri ja kohaliku offline edastuse ühendus puudub.",
      "sources": [
        "products/axar-pos/axar_pos/orders.py"
      ],
      "issues": [
        25,
        38
      ]
    },
    {
      "id": "books",
      "title": "BOOKS",
      "subtitle": "Raamatupidamise eksport",
      "x": 950,
      "y": 645,
      "kind": "domain",
      "status": "partial",
      "owner": "BOOKS / raamatupidaja",
      "description": "Outboxist luuakse export_queue kirjeid. Päris raamatupidamistarkvara edastus ja ACK on puudu.",
      "sources": [
        "products/axar-pos/axar_pos/consumers.py"
      ],
      "issues": [
        4,
        8
      ]
    },
    {
      "id": "control",
      "title": "CONTROL",
      "subtitle": "Aruanded ja kontrollid",
      "x": 950,
      "y": 760,
      "kind": "governance",
      "status": "partial",
      "owner": "CONTROL / juhtimine",
      "description": "Müügiaruanded olemas; terviklik NERVE Health ja grafiku reaalaja valvurid on kavandatud.",
      "sources": [
        "products/axar-pos/axar_pos/services.py"
      ],
      "issues": [
        42,
        32
      ]
    },
    {
      "id": "terminal",
      "title": "TERMINAL",
      "subtitle": "Päris kaarditerminal",
      "x": 1240,
      "y": 300,
      "kind": "device",
      "status": "planned",
      "owner": "PAY / seadme pakkuja",
      "description": "Pakkujaneutraalse adapteri leping ja simulaator olemas, kuid füüsilist makse HIL-testi ei ole.",
      "sources": [
        "products/axar-pos/axar_pos/terminals"
      ],
      "issues": [
        25
      ]
    },
    {
      "id": "printer",
      "title": "PRINTER",
      "subtitle": "Tšekk ja köök",
      "x": 1240,
      "y": 530,
      "kind": "device",
      "status": "planned",
      "owner": "KDS / seadme pakkuja",
      "description": "Tšeki ja köögi füüsilise printeri ACK, järjekord ja HIL test on plaanis.",
      "sources": [],
      "issues": [
        25
      ]
    },
    {
      "id": "otel",
      "title": "OBSERVABILITY",
      "subtitle": "Traces ja mõõdikud",
      "x": 1240,
      "y": 680,
      "kind": "governance",
      "status": "planned",
      "owner": "SRE / NERVE",
      "description": "OpenTelemetry/Grafana abil on kavandatud reaalaja jälg; see ATLAS versioon pole live mõõdikutele ühendatud.",
      "sources": [],
      "issues": [
        42,
        62
      ]
    }
  ],
  "edges": [
    {
      "from": "pos",
      "to": "api",
      "type": "api",
      "status": "code",
      "label": "HTTPS / quote ja sale"
    },
    {
      "from": "office",
      "to": "api",
      "type": "api",
      "status": "planned",
      "label": "Eraldi admin session #76"
    },
    {
      "from": "pos",
      "to": "edge",
      "type": "sync",
      "status": "partial",
      "label": "Kohalik journal"
    },
    {
      "from": "edge",
      "to": "api",
      "type": "sync",
      "status": "partial",
      "label": "ID + seq + ACK"
    },
    {
      "from": "api",
      "to": "auth",
      "type": "auth",
      "status": "code",
      "label": "Kind + tenant + scope"
    },
    {
      "from": "api",
      "to": "db",
      "type": "data",
      "status": "code",
      "label": "ACID transaction"
    },
    {
      "from": "db",
      "to": "nerve",
      "type": "event",
      "status": "code",
      "label": "Transactional outbox"
    },
    {
      "from": "office",
      "to": "catalog",
      "type": "api",
      "status": "planned",
      "label": "Tooted / hinnad / publish"
    },
    {
      "from": "catalog",
      "to": "db",
      "type": "data",
      "status": "partial",
      "label": "Versioned price"
    },
    {
      "from": "pos",
      "to": "orders",
      "type": "api",
      "status": "code",
      "label": "Order / sale commands"
    },
    {
      "from": "orders",
      "to": "db",
      "type": "data",
      "status": "code",
      "label": "Order snapshots"
    },
    {
      "from": "api",
      "to": "pay",
      "type": "api",
      "status": "partial",
      "label": "Tenders, split, refund"
    },
    {
      "from": "pay",
      "to": "db",
      "type": "data",
      "status": "code",
      "label": "Payment records"
    },
    {
      "from": "pay",
      "to": "terminal",
      "type": "device",
      "status": "planned",
      "label": "Provider ACK"
    },
    {
      "from": "nerve",
      "to": "stock",
      "type": "event",
      "status": "code",
      "label": "stock.consume"
    },
    {
      "from": "stock",
      "to": "db",
      "type": "data",
      "status": "code",
      "label": "Stock ledger"
    },
    {
      "from": "orders",
      "to": "kds",
      "type": "event",
      "status": "partial",
      "label": "Kitchen tickets"
    },
    {
      "from": "kds",
      "to": "printer",
      "type": "device",
      "status": "planned",
      "label": "Print job ACK"
    },
    {
      "from": "nerve",
      "to": "books",
      "type": "event",
      "status": "code",
      "label": "books.export"
    },
    {
      "from": "books",
      "to": "db",
      "type": "data",
      "status": "code",
      "label": "Export queue"
    },
    {
      "from": "db",
      "to": "control",
      "type": "data",
      "status": "code",
      "label": "Reports"
    },
    {
      "from": "device",
      "to": "edge",
      "type": "device",
      "status": "partial",
      "label": "Enrolled till"
    },
    {
      "from": "device",
      "to": "terminal",
      "type": "device",
      "status": "planned",
      "label": "Vendor adapter"
    },
    {
      "from": "governor",
      "to": "api",
      "type": "quality",
      "status": "partial",
      "label": "Contract tests"
    },
    {
      "from": "governor",
      "to": "db",
      "type": "quality",
      "status": "partial",
      "label": "Reconcile"
    },
    {
      "from": "nerve",
      "to": "otel",
      "type": "telemetry",
      "status": "planned",
      "label": "Distributed trace"
    },
    {
      "from": "atlas",
      "to": "governor",
      "type": "quality",
      "status": "partial",
      "label": "Static audit data"
    },
    {
      "from": "atlas",
      "to": "otel",
      "type": "telemetry",
      "status": "planned",
      "label": "Future telemetry"
    }
  ],
  "flows": [
    {
      "id": "sale",
      "label": "Sularahamüük",
      "subtitle": "Kassa → tehing → registrid",
      "nodes": [
        "pos",
        "api",
        "auth",
        "db",
        "nerve",
        "stock",
        "books",
        "control"
      ],
      "note": "Kirjeldab koodi teekonda; ei ole jälitatud päris müüki."
    },
    {
      "id": "table",
      "label": "Restorani arve",
      "subtitle": "Laud → köök → makse",
      "nodes": [
        "pos",
        "orders",
        "kds",
        "api",
        "pay",
        "db",
        "nerve"
      ],
      "note": "Köögitellimuse salvestus olemas; füüsiline printer pole ühendatud."
    },
    {
      "id": "offline",
      "label": "Internetikatkestus",
      "subtitle": "Kassa → edge → sünkroniseerimine",
      "nodes": [
        "pos",
        "edge",
        "api",
        "db",
        "nerve"
      ],
      "note": "Offline loogika koodis; päris voolukatkestuse riistvarakatse puudub."
    },
    {
      "id": "admin",
      "label": "Hinnakirja avaldamine",
      "subtitle": "Kontor → tooteregister → kassa",
      "nodes": [
        "office",
        "catalog",
        "db",
        "nerve",
        "pos"
      ],
      "note": "Täielik avaldamise ahel veel kavandatud; hinnakirja töö PR #74 mustandis."
    }
  ]
};
