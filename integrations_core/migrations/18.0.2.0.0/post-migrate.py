# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Rename namespace integration.* → centrivo.* — fix dei cron su update in-place (TASK_30).

Il rename dei _name fa creare a Odoo le nuove tabelle `centrivo_*` (le vecchie
`integration_*` restano orfane: re-init voluto, i dati di test si abbandonano).

Problema da risolvere SOLO sull'update in-place (es. stage): i 4 cron sono in un
data file `noupdate="1"`, quindi l'update NON ne riapplica il `model_id`, e il
vecchio modello `integration.channel` (con il suo external id `model_integration_channel`)
viene rimosso → i cron resterebbero appesi a un model_id inesistente (o cascade-
cancellati). Qui, una volta sola su questo step di versione, GARANTIAMO che i 4
cron esistano e puntino a `centrivo.channel`: repoint se presenti, ricreazione
(con relativo external id) se mancanti. Su un'installazione PULITA (Odoo.sh) questa
migrazione non gira: lì l'XML crea già i cron corretti.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# xmlid -> specifica canonica del cron (coerente con data/ir_cron.xml).
CRON_SPECS = {
    "cron_pull_all_channels": {
        "name": "BricoBravo: pull ordini in entrata",
        "code": "model.cron_pull_all_channels()",
        "interval_number": 1, "interval_type": "hours",
    },
    "cron_push_shipments": {
        "name": "BricoBravo: push spedizioni",
        "code": "model.cron_push_shipments()",
        "interval_number": 6, "interval_type": "hours",
    },
    "cron_generate_stock_feeds": {
        "name": "BricoBravo: genera feed prezzi/giacenze",
        "code": "model.cron_generate_stock_feeds()",
        "interval_number": 30, "interval_type": "minutes",
    },
    "cron_generate_catalog_feeds": {
        "name": "BricoBravo: genera feed catalogo",
        "code": "model.cron_generate_catalog_feeds()",
        "interval_number": 12, "interval_type": "hours",
    },
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    model = env["ir.model"].search([("model", "=", "centrivo.channel")], limit=1)
    if not model:
        _logger.warning("centrivo.channel non trovato: skip fix cron (TASK_30).")
        return
    root = env.ref("base.user_root", raise_if_not_found=False)

    for xmlid, spec in CRON_SPECS.items():
        cron = env.ref("integrations_core.%s" % xmlid, raise_if_not_found=False)
        if cron and cron.exists():
            # Repoint al modello rinominato (tutti i cron girano su centrivo.channel).
            cron.write({"model_id": model.id})
            continue
        # Cron mancante (cascade-rimosso con il vecchio modello): lo ricreiamo
        # con il suo external id, così resta tracciato e disattivo.
        vals = {
            "name": spec["name"],
            "model_id": model.id,
            "state": "code",
            "code": spec["code"],
            "interval_number": spec["interval_number"],
            "interval_type": spec["interval_type"],
            "active": False,
        }
        if root:
            vals["user_id"] = root.id
        cron = env["ir.cron"].create(vals)
        env["ir.model.data"].create({
            "name": xmlid,
            "module": "integrations_core",
            "model": "ir.cron",
            "res_id": cron.id,
            "noupdate": True,
        })
        _logger.info("Cron %s ricreato e collegato a centrivo.channel.", xmlid)
