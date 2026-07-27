# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Riaggancia le mappature esistenti ai nuovi record dei campi ManoMano.

Prima di questa versione la colonna era un testo (`mm_column`, ora campo rimosso
→ colonna DB orfana). Al momento dell'aggiornamento la taxonomy NON è ancora
stata scaricata: creiamo quindi un record «segnaposto» per ogni colonna in uso,
che il primo scarico adotterà per nome riempiendone i metadati. Nessuna
configurazione va persa e nessuno deve rifare la mappatura a mano.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'centrivo_manomano_feed_map'
           AND column_name = 'mm_column'
    """)
    if not cr.fetchone():
        return  # nessuna installazione precedente da convertire

    cr.execute("""
        SELECT id, mm_column FROM centrivo_manomano_feed_map
         WHERE mm_column IS NOT NULL AND mm_column <> ''
    """)
    rows = cr.fetchall()
    if not rows:
        return

    riagganciate = 0
    for map_id, column in rows:
        cr.execute(
            "SELECT id FROM centrivo_manomano_feed_field WHERE name = %s",
            (column,))
        found = cr.fetchone()
        if found:
            field_id = found[0]
        else:
            cr.execute("""
                INSERT INTO centrivo_manomano_feed_field
                       (name, label, active, sequence, mandatory, is_variant,
                        has_values, create_uid, create_date, write_uid, write_date)
                VALUES (%s, %s, true, 10, false, false, false,
                        1, now(), 1, now())
                RETURNING id
            """, (column, column))
            field_id = cr.fetchone()[0]
        cr.execute(
            "UPDATE centrivo_manomano_feed_map SET mm_field_id = %s WHERE id = %s",
            (field_id, map_id))
        riagganciate += 1

    _logger.info("ManoMano: %s righe di mappatura riagganciate ai campi feed.",
                 riagganciate)
