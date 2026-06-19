# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Migrazione GLS 18.0.2.0.0 (TASK_69): nuova fonte infoweb.

Gli account GLS esistenti puntano l'endpoint al vecchio nodo pubblico
(gls-group.com/.../rstt030). Si ripunta l'endpoint alla nuova fonte infoweb SOLO se
ancora sul vecchio nodo (idempotente, non sovrascrive un endpoint già personalizzato).

I codici cliente GLS (gls_sender_code / gls_contract_code) NON vengono precompilati:
sono dati del cliente e li inserisce l'utente da UI (così si valida anche la UI nel
test di accettazione). Le colonne nuove sono create da Odoo prima della post-migrate.
"""
import logging

_logger = logging.getLogger(__name__)

OLD_ENDPOINT_MARKER = "gls-group.com"
NEW_ENDPOINT = "https://infoweb.gls-italy.com/XML/get_xml_track.php"


def migrate(cr, version):
    cr.execute(
        """
        UPDATE centrivo_tracking_account
           SET endpoint = %s
         WHERE lower(tracker_code) = 'gls'
           AND (endpoint IS NULL OR endpoint LIKE %s)
        """,
        (NEW_ENDPOINT, "%" + OLD_ENDPOINT_MARKER + "%"),
    )
    if cr.rowcount:
        _logger.info(
            "GLS TASK_69: endpoint infoweb impostato su %s account GLS.", cr.rowcount)
