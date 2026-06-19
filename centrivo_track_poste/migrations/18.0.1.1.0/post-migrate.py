# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Migrazione Poste 18.0.1.1.0 (TASK_68): pulizia righe-fantasma di normalizzazione.

Il vecchio seed (noupdate=1) mappava NOMI-STATO IPOTIZZATI che l'API Poste non manda
MAI (GIACENZA, RIFIUTO, MANCATA CONSEGNA, INDIRIZZO ERRATO, FERMO DEPOSITO, SVINCOLO,
ANOMALIA, CENTRO INTERNAZIONALE, IN ATTESA DI SDOGANAMENTO, RITIRATA IN DIGITALE,
CONSEGNATO). Essendo noupdate, un semplice update del modulo NON le rimuove: vanno
cancellate qui (idempotente). Il nuovo seed introduce la normalizzazione a due livelli
(override su `status` 003/009/RRR + fallback sui 5 `phase` reali).

Si conservano i 5 phase reali (PRESA IN CARICO, IN TRANSITO, IN CONSEGNA, CONSEGNATA,
RESO AL MITTENTE). Le righe-fantasma non sono referenziate da alcun evento (gli eventi
puntano a centrivo.shipment.status, non alla mappa), quindi la cancellazione è sicura.
Si rimuovono anche gli external id orfani per evitare riferimenti pendenti.
"""
import logging

_logger = logging.getLogger(__name__)

# raw_code (MAIUSCOLO) delle righe-fantasma Poste da rimuovere.
PHANTOM_RAW_CODES = (
    "GIACENZA", "RIFIUTO", "MANCATA CONSEGNA", "INDIRIZZO ERRATO", "FERMO DEPOSITO",
    "SVINCOLO", "ANOMALIA", "CENTRO INTERNAZIONALE", "IN ATTESA DI SDOGANAMENTO",
    "RITIRATA IN DIGITALE", "CONSEGNATO",
)
# External id (module centrivo_track_poste) delle vecchie righe-fantasma.
PHANTOM_XMLIDS = (
    "map_poste_giacenza", "map_poste_rifiuto", "map_poste_mancata_consegna",
    "map_poste_indirizzo_errato", "map_poste_fermo_deposito", "map_poste_svincolo",
    "map_poste_anomalia", "map_poste_centro_internazionale", "map_poste_sdoganamento",
    "map_poste_ritirata_digitale", "map_poste_consegnato",
)


def migrate(cr, version):
    # Cancella le righe di mappatura fantasma (tracker poste, raw_code nei phantom).
    cr.execute(
        """
        DELETE FROM centrivo_shipment_status_map
         WHERE lower(tracker_code) = 'poste'
           AND upper(raw_code) IN %s
        """,
        (PHANTOM_RAW_CODES,),
    )
    _logger.info("Poste TASK_68: rimosse %s righe-fantasma di normalizzazione.",
                 cr.rowcount)

    # Rimuove gli external id orfani delle righe cancellate (evita ref pendenti).
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'centrivo_track_poste'
           AND name IN %s
        """,
        (PHANTOM_XMLIDS,),
    )
    _logger.info("Poste TASK_68: rimossi %s external id orfani.", cr.rowcount)
