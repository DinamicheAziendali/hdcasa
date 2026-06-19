# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Adattatore di tracking BRT (Bartolini) per la suite centrivo_shipment_tracking.
# Registra il TrackingConnector "brt" (API REST ufficiale, sola lettura) e semina la
# mappatura dei codici stato BRT verso gli stati Centrivo. Dipende solo dal modulo
# base. Vedi docs/studio-corrieri/REPORT_TASK_51.md (sezione BRT).
{
    "name": "Centrivo Tracking - BRT",
    "version": "18.0.1.5.1",
    "license": "OPL-1",
    "category": "Inventory/Delivery",
    "summary": "Adattatore di tracking BRT (Bartolini) per "
               "centrivo_shipment_tracking: stato reale, eventi, normalizzazione.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    "depends": ["centrivo_shipment_tracking"],
    "data": [
        "data/status_map_brt.xml",
    ],
    "installable": True,
    "application": False,
}
