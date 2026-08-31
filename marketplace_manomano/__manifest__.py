# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Connettore ManoMano (Famiglia A — sotto integrations_core).
# STRATO 1: export offerte (prezzo + giacenza) via Partners API `update_offers`.
# Il connettore è multi-contract (multi-mercato) fin da subito: oggi è attivo il
# solo mercato IT, ma il campo contract sul canale accetta più codici.
# Nessuna chiave API nel codice: si legge da channel.api_key.
#
# STRATI FUTURI (NON in questo modulo):
#   - Strato 2: creazione offerte nuove (match EAN sul catalogo ManoMano).
#   - Strato 3: schede prodotto (categorie/attributi/immagini/moderazione).
{
    "name": "Marketplace - ManoMano",
    "version": "18.0.1.15.0",
    "license": "OPL-1",
    "category": "Connector",
    "summary": "Connettore ManoMano (Partners API): export offerte prezzo/giacenza "
               "via update_offers, multi-contract.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    "depends": ["integrations_core", "account"],
    "data": [
        "security/ir.model.access.csv",
        "views/manomano_feed_field_views.xml",
        "views/manomano_channel_views.xml",
        "views/manomano_offer_views.xml",
        "views/manomano_order_map_views.xml",
        "views/manomano_invoice_views.xml",
        "data/ir_cron.xml",
    ],
    "installable": True,
    "application": False,
}
