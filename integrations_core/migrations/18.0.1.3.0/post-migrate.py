# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Backfill export_token sui canali esistenti (TASK_23).

Il nuovo campo `export_token` protegge gli URL pubblici del feed. I record di
canale preesistenti potrebbero avere il token vuoto: qui ne assegniamo uno
robusto e UNICO per ciascun canale. Idempotente: tocca solo i canali con token
NULL/vuoto, quindi NON ruota i token già assegnati (non rompe gli URL già
distribuiti) se rieseguito.
"""
import logging
import secrets

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        "SELECT id FROM integration_channel "
        "WHERE export_token IS NULL OR export_token = ''")
    channel_ids = [row[0] for row in cr.fetchall()]
    for channel_id in channel_ids:
        cr.execute(
            "UPDATE integration_channel SET export_token = %s WHERE id = %s",
            (secrets.token_urlsafe(32), channel_id))
    if channel_ids:
        _logger.info("Backfill export_token: %s canali aggiornati.",
                     len(channel_ids))
