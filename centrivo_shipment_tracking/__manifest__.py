# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Modulo BASE della suite di tracking spedizioni (post-spedizione).
# Fornisce il modello a due livelli (spedizione/collo), gli eventi di tracking,
# la normalizzazione degli stati via TABELLA dati, lo stato reale sul picking,
# il polling adattivo (bottone manuale + cron disattivo) e il contratto astratto
# dei TrackingConnector. Gli adattatori concreti (GLS/BRT/Poste) sono moduli
# separati. Vedi Progetto_tracking_corrieri.md (Fase 1).
{
    "name": "Centrivo Shipment Tracking",
    "version": "18.0.2.1.0",
    "license": "OPL-1",
    "category": "Inventory/Delivery",
    "summary": "Tracking spedizioni post-creazione: stato reale del collo/spedizione "
               "dai corrieri, normalizzazione stati, polling adattivo, stato sul "
               "picking. Base per gli adattatori corriere (GLS/BRT/Poste).",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    # Suite AUTOSUFFICIENTE (TASK_73): il trasporto HTTP è interno
    # (connectors/transport.py: RestTransport), il log è interno
    # (centrivo.shipment.log) → NESSUNA dipendenza da integrations_core. Restano solo
    # moduli STANDARD di Odoo: stock/stock_delivery per il picking nativo e
    # carrier_id/carrier_tracking_ref; sale_stock per sale_id; mail per le attività
    # native (mail.activity) degli alert SLA (Fase 2). NESSUNA dipendenza da
    # centrivo_carrier_base.
    "depends": [
        "stock",
        "stock_delivery",
        "sale_stock",
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/centrivo_shipment_security.xml",
        "data/centrivo_shipment_status_data.xml",
        "data/centrivo_tracking_config_data.xml",
        "data/centrivo_shipment_server_actions.xml",
        "data/ir_cron.xml",
        "views/centrivo_shipment_status_views.xml",
        "views/centrivo_shipment_status_map_views.xml",
        "views/centrivo_tracking_config_views.xml",
        "views/centrivo_tracking_carrier_map_views.xml",
        "views/centrivo_tracking_account_views.xml",
        "views/centrivo_shipment_log_views.xml",
        "views/centrivo_sla_zone_views.xml",
        "views/centrivo_sla_rule_views.xml",
        "views/centrivo_shipment_alert_views.xml",
        "views/centrivo_shipment_views.xml",
        "views/centrivo_shipment_report_views.xml",
        "views/centrivo_carrier_performance_views.xml",
        "views/centrivo_shipment_dashboard_views.xml",
        "views/stock_picking_views.xml",
        "views/centrivo_shipment_menus.xml",
    ],
    # Registrazione best-effort dell'istanza al Centrivo License Server alla prima
    # installazione e a ogni cambio-versione (FASE 1: nessun gating). Mai bloccante:
    # timeout corto + try/except totale. Vedi __init__.post_init_hook e
    # models/centrivo_license.py.
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
}
