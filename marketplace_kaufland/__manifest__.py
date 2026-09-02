# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Connettore Kaufland (Famiglia A — sotto integrations_core).
# CONSEGNA 1: la meta' offerte — riaggancio, ricognizione, creazione,
# allineamento. Gli ordini sono la Consegna 2.
{
    "name": "Marketplace - Kaufland",
    "version": "18.0.7.2.0",
    "license": "OPL-1",
    "category": "Connector",
    "summary": "Connettore Kaufland: riaggancio delle offerte esistenti, "
               "ricognizione delle schede, creazione e allineamento offerte.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    "depends": ["integrations_core", "account"],
    "data": [
        "security/ir.model.access.csv",
        "views/kaufland_menu_root.xml",
        "data/ir_cron.xml",
        "views/kaufland_offer_views.xml",
        "views/kaufland_channel_views.xml",
        "views/kaufland_order_map_views.xml",
    ],
    "installable": True,
    "application": False,
}
