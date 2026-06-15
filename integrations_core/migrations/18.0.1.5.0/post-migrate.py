# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Rimuove la colonna orfana map_image_url_field (TASK_25).

Il campo `map_image_url_field` (introdotto in TASK_24) è stato rimosso: le
immagini del feed catalogo ora sono URL costruiti dai blob nativi Odoo, senza
dipendere da un campo URL esterno. Alla rimozione del field Odoo lascia la
colonna orfana nel DB: qui la eliminiamo per pulizia. Idempotente.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'integration_channel' "
        "AND column_name = 'map_image_url_field'")
    if cr.fetchone():
        cr.execute(
            "ALTER TABLE integration_channel DROP COLUMN map_image_url_field")
        _logger.info("Colonna orfana map_image_url_field rimossa.")
