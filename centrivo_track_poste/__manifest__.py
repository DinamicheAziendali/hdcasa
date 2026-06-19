# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Adattatore di tracking Poste Italiane per la suite centrivo_shipment_tracking.
# Registra il TrackingConnector "poste" (API REST con OAuth2, sola lettura), estende
# l'account con lo scope OAuth2 e semina la mappatura delle phase Poste verso gli
# stati Centrivo. Dipende solo dal modulo base. Vedi
# docs/studio-corrieri/REPORT_TASK_51.md (sezione Poste).
{
    "name": "Centrivo Tracking - Poste",
    "version": "18.0.1.2.1",
    "license": "OPL-1",
    "category": "Inventory/Delivery",
    "summary": "Adattatore di tracking Poste Italiane (OAuth2) per "
               "centrivo_shipment_tracking: stato reale, eventi, normalizzazione.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    "depends": ["centrivo_shipment_tracking"],
    "data": [
        "views/tracking_account_views.xml",
        "data/status_map_poste.xml",
    ],
    "installable": True,
    "application": False,
}
