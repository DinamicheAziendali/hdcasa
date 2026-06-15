# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Allinea i cron BricoBravo ai nuovi default (TASK_29).

I cron sono passati a noupdate="1" (le modifiche UI non vengono più sovrascritte
dagli update). Conseguenza: su un DB dove i cron ESISTONO già (es. stage, dai
TASK_23/24) l'update NON applicherebbe i nuovi nomi/frequenze definiti in XML.
Qui li forziamo UNA volta ai default canonici di questa versione (rename a
"BricoBravo: ..." e catalogo 6h → 12h). Eseguito una sola volta (migrazione di
versione); gli update successivi rispettano le scelte UI grazie a noupdate.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# xmlid -> valori canonici da applicare ai cron già esistenti.
CRON_DEFAULTS = {
    "cron_pull_all_channels": {
        "name": "BricoBravo: pull ordini in entrata",
        "interval_number": 1, "interval_type": "hours",
    },
    "cron_generate_stock_feeds": {
        "name": "BricoBravo: genera feed prezzi/giacenze",
        "interval_number": 30, "interval_type": "minutes",
    },
    "cron_generate_catalog_feeds": {
        "name": "BricoBravo: genera feed catalogo",
        "interval_number": 12, "interval_type": "hours",
    },
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid, vals in CRON_DEFAULTS.items():
        cron = env.ref("integrations_core.%s" % xmlid, raise_if_not_found=False)
        if cron:
            cron.write(vals)
            _logger.info("Cron %s allineato ai default TASK_29.", xmlid)
