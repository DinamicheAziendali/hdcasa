# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Migrazione 18.0.1.10.0 (TASK_68): garanzia alert_on_entry su held/exception/returned.

I tre stati problematici (giacenza, anomalia, reso) devono generare un alert
all'ingresso (Scenario D), senza attendere una soglia temporale. Questi flag sono già
nel seed (noupdate) e nella migrazione 18.0.1.8.0; questa è una ri-asserzione DIFENSIVA
e IDEMPOTENTE per garantire che i tre flag risultino True dopo l'update anche su DB che
non fossero allineati. Imposta SOLO da False→True (non sovrascrive nient'altro), quindi
non tocca eventuali altre configurazioni utente.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE centrivo_shipment_status
           SET alert_on_entry = TRUE
         WHERE code IN ('held', 'exception', 'returned')
           AND alert_on_entry IS NOT TRUE
    """)
    if cr.rowcount:
        _logger.info(
            "TASK_68: alert_on_entry ri-asserito su %s stati (held/exception/returned).",
            cr.rowcount)
