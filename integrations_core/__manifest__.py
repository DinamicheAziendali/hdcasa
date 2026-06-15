# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Manifest del modulo "Integrations Core".
# Layer condiviso (Famiglia A) sotto cui vivono i connettori marketplace
# "semplici" (BricoBravo, ManoMano). Definisce i contratti astratti dei
# connettori, i trasporti (REST/CSV), i registri per idempotenza e mapping SKU,
# e il log operativo. Nessun segreto è contenuto qui: le credenziali sono campi
# di configurazione sul record "centrivo.channel", valorizzati in ambiente.
{
    "name": "Integrations Core",
    "version": "18.0.2.0.0",
    "license": "OPL-1",
    "category": "Connector",
    "summary": "Layer condiviso per i connettori marketplace: contratti astratti, trasporti REST/CSV, mapping SKU e corrieri, registro ordini e log operazioni.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    # Dipendenze: vendite (sale.order), magazzino (stock), gestione vendite.
    # delivery → modello delivery.carrier (mapping corrieri); stock_delivery →
    # campi nativi carrier_id/carrier_tracking_ref su stock.picking (Strato 3b).
    "depends": ["base", "sale_management", "stock", "delivery", "stock_delivery"],
    "data": [
        "security/ir.model.access.csv",
        "security/integration_security.xml",
        "data/ir_cron.xml",
        "views/integration_channel_views.xml",
        "views/integration_order_map_views.xml",
        "views/integration_sku_map_views.xml",
        "views/integration_carrier_map_views.xml",
        "views/integration_job_log_views.xml",
        "views/integration_menus.xml",
    ],
    "installable": True,
    "application": False,
}
