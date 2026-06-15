# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Backfill company_id sui modelli del connettore (TASK_18).

Contesto: in questa versione vengono attivate le record rules multi-azienda
(ir.rule globali con domain su company_id) su integration.channel,
integration.order.map, integration.sku.map e integration.job.log.

Senza backfill, eventuali record preesistenti con company_id NULL sparirebbero
dalla vista una volta attive le rules. Qui li allineiamo TUTTI all'azienda
principale (la company con id più basso). È idempotente: aggiorna solo le righe
con company_id IS NULL, quindi rieseguirlo non ha effetti collaterali.

Si opera via SQL diretto SOLO perché siamo in una migrazione di schema su tabelle
del modulo stesso (non sul core Odoo) e i campi sono semplici scalari: nessun
side-effect di business da innescare.
"""
import logging

_logger = logging.getLogger(__name__)

TABLES = (
    "integration_channel",
    "integration_order_map",
    "integration_sku_map",
    "integration_job_log",
)


def migrate(cr, version):
    # Azienda principale = quella con id più basso (la company "di default"
    # creata all'inizializzazione del database).
    cr.execute("SELECT id FROM res_company ORDER BY id ASC LIMIT 1")
    row = cr.fetchone()
    if not row:
        _logger.warning("Backfill company_id saltato: nessuna res.company trovata.")
        return
    company_id = row[0]

    for table in TABLES:
        # Tabella potrebbe non esistere se il modello è nuovo: guardia difensiva.
        cr.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,))
        if not cr.fetchone():
            continue
        cr.execute(
            "UPDATE %s SET company_id = %%s WHERE company_id IS NULL" % table,
            (company_id,))
        if cr.rowcount:
            _logger.info(
                "Backfill company_id=%s su %s: %s record aggiornati.",
                company_id, table, cr.rowcount)
