# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Adattatore di tracking GLS per la suite centrivo_shipment_tracking.
# Registra il TrackingConnector "gls" (fonte infoweb GLS Italia XML, sola lettura) e
# semina la mappatura dei codici stato GLS verso gli stati Centrivo. Dipende solo
# dal modulo base. Vedi Progetto_tracking_corrieri.md (Fase 1, adattatore GLS).
{
    "name": "Centrivo Tracking - GLS",
    "version": "18.0.2.0.1",
    "license": "OPL-1",
    "category": "Inventory/Delivery",
    "summary": "Adattatore di tracking GLS (infoweb XML) per "
               "centrivo_shipment_tracking: stato reale, eventi, normalizzazione.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    "depends": ["centrivo_shipment_tracking"],
    "data": [
        "data/status_map_gls.xml",
        "views/tracking_account_views.xml",
    ],
    "installable": True,
    "application": False,
}
