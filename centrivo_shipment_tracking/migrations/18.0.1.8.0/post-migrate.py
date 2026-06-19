# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Migrazione dati Fase 2 SLA (18.0.1.8.0).

I seed dei tre adattatori e degli stati di sistema sono noupdate="1": un update del
modulo NON li ri-importa, quindi i nuovi flag introdotti in questa versione vanno
applicati alle righe ESISTENTI con una migrazione dati (eseguita una sola volta).

  - is_pre_advice sui codici grezzi che sono SOLO trasmissione dati / pre-avviso:
      BRT 700, GLS PREADVICE (Poste: nessuno → restano False, default colonna).
  - alert_on_entry sugli stati problematici che generano alert all'ingresso
    (giacenza, anomalia, reso) e alert_suggests_claim su anomalia/reso.

Le colonne sono già state create da Odoo (init dei campi) prima della post-migrate.
Operazione idempotente: imposta il valore semantico corretto dei flag di seed.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # is_pre_advice: keyed per (tracker_code, raw_code), robusto rispetto agli
    # external id degli adattatori (i raw_code sono normalizzati in maiuscolo).
    cr.execute("""
        UPDATE centrivo_shipment_status_map
           SET is_pre_advice = TRUE
         WHERE (lower(tracker_code) = 'brt' AND upper(raw_code) = '700')
            OR (lower(tracker_code) = 'gls' AND upper(raw_code) = 'PREADVICE')
    """)
    _logger.info("SLA migration: is_pre_advice impostato su %s righe.", cr.rowcount)

    # Trigger su stato: giacenza/anomalia/reso generano alert all'ingresso;
    # anomalia/reso suggeriscono reclamo.
    cr.execute("""
        UPDATE centrivo_shipment_status
           SET alert_on_entry = TRUE
         WHERE code IN ('held', 'exception', 'returned')
    """)
    cr.execute("""
        UPDATE centrivo_shipment_status
           SET alert_suggests_claim = TRUE
         WHERE code IN ('exception', 'returned')
    """)
    _logger.info("SLA migration: trigger di stato seminati su giacenza/anomalia/reso.")
