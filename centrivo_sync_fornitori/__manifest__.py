# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# centrivo_sync_fornitori — caricamento prodotti dropship (Flusso A) + (in futuro)
# aggiornamento giacenze (Flusso B) da fornitori dropship.
#
# QUESTO MODULO (Flusso A v2.x — wizard a mappatura dinamica):
#   - estensione product.template con i campi dropship;
#   - centrivo.sync.channel (config per-fornitore: mappatura salvata;
#     parametri SFTP predisposti per il solo Flusso B);
#   - riferimento interno (default_code): COLONNA del file mappabile (v2.2),
#     compilata a mano; il modulo NON genera più il codice HDC;
#   - centrivo.sync.column.map (mappatura colonna→destinazione, salvata per fornitore);
#   - centrivo.sync.import.wizard (+ line): upload CSV/XLSX a mano, lettura
#     intestazioni reali, mappatura dinamica mista, applicazione selettiva per giro;
#   - centrivo.sync.import.job: import ASINCRONO in background via cron nativo
#     (a chunk, con cursore/ripresa) — evita il troncamento da timeout web sui
#     file grandi; include la FASE 2 immagini (User-Agent browser, resiliente);
#   - centrivo.sync.supply (TRACCIA prodotto↔fornitore↔SKU; NON è più la giacenza);
#   - parser file (xlsx openpyxl + csv con rilevamento separatore);
#   - import a 2 fasi (anagrafica batch + immagini resilienti);
#   - Flusso B: aggiornamento ricorrente giacenze (SFTP → stock Odoo nel
#     magazzino dedicato del fornitore; anti-fantasma, prezzo ignorato,
#     azzeramento; cron esecutore + bottone "Aggiorna giacenze ora").
#
# FUORI da questo modulo (Fase B):
#   - route/PO/ricevimento, galleria immagini, schede tecniche;
#   - MAI scrittura di list_price.
#
# Vedi docs/sync-fornitori/01-specifica-import-csv.md per la specifica completa.
{
    "name": "Centrivo - Sync Fornitori (Dropship)",
    "version": "18.0.7.0.0",
    "license": "OPL-1",
    "category": "Inventory/Purchase",
    "summary": "Caricamento prodotti dropship via wizard a mappatura dinamica "
               "(upload CSV/XLSX) + aggiornamento giacenze da SFTP nello stock del "
               "magazzino dedicato del fornitore.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    # integrations_core: trasporto (SftpTransport) + log operazioni (centrivo.job.log)
    # + catena prodotto/vendite/magazzino (product, sale, stock).
    "depends": ["integrations_core", "stock"],
    # Dipendenze Python esterne dichiarate:
    #   - openpyxl: lettura catalogo .xlsx (già nell'immagine Odoo base);
    #   - paramiko: download SFTP (SftpTransport). Reso disponibile su entrambi
    #     gli ambienti via requirements.txt in root (Dockerfile su Hetzner;
    #     lettura nativa su Odoo.sh). NB: SftpTransport importa paramiko in modo
    #     locale, quindi il modulo resta installabile anche dove paramiko manca;
    #     questa dichiarazione documenta/forza la dipendenza dove i requirements
    #     vengono applicati.
    "external_dependencies": {"python": ["openpyxl", "paramiko"]},
    "data": [
        "security/ir.model.access.csv",
        "security/sync_security.xml",
        "data/ir_cron.xml",
        "views/sync_channel_views.xml",
        "views/sync_import_wizard_views.xml",
        "views/sync_import_job_views.xml",
        "views/sync_supply_views.xml",
        "views/product_template_views.xml",
        "views/sync_menus.xml",
    ],
    "installable": True,
    "application": False,
}
