# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Riempie le chiavi durevoli sulle righe di mapping corrieri esistenti.

Prima di questa versione una riga identificava il vettore col solo carrier_id
(metodo di consegna nativo). Le tre chiavi durevoli si ricavano da lì, così il
push continua a risolvere il vettore esattamente come prima.

PRUDENZA sul nome del vettore: `delivery_carrier.name` potrebbe essere una
colonna di testo semplice oppure, se il campo è tradotto, una colonna jsonb
(chiave = codice lingua). Non possiamo assumerlo: una UPDATE che sceglie
l'operatore SQL sbagliato per il tipo reale bloccherebbe l'aggiornamento del
modulo in produzione. Qui il tipo si VERIFICA a runtime via
information_schema prima di costruire la query, e in caso di dubbio (colonna
non trovata, tipo inatteso) si usa la forma più prudente: un'etichetta
segnaposto "Corriere #<id>" che si aggiorna da sola al primo salvataggio della
riga dalla UI (il vettore intanto continua a risolvere correttamente tramite
carrier_id, indipendentemente da questa etichetta).
"""
import logging

_logger = logging.getLogger(__name__)

FALLBACK_DISPLAY_SQL = "'Corriere #' || m.carrier_id"


def _display_expr(cr):
    """Espressione SQL per l'etichetta del corriere, in base al tipo reale
    della colonna delivery_carrier.name su QUESTO database."""
    cr.execute("""
        SELECT data_type FROM information_schema.columns
        WHERE table_name = 'delivery_carrier' AND column_name = 'name'
          AND table_schema = current_schema()
    """)
    row = cr.fetchone()
    data_type = row[0] if row else None

    if data_type == "jsonb":
        # Campo tradotto: prova le lingue più comuni, altrimenti segnaposto.
        return ("COALESCE(c.name ->> 'it_IT', c.name ->> 'en_US', %s)"
                % FALLBACK_DISPLAY_SQL)
    if data_type in ("character varying", "text", "character"):
        # Campo semplice: usa il valore diretto, altrimenti segnaposto.
        return "COALESCE(c.name, %s)" % FALLBACK_DISPLAY_SQL
    # Tipo non riconosciuto (o colonna non trovata): niente estrazione,
    # solo il segnaposto. Non si rischia mai un operatore incompatibile.
    _logger.warning(
        "Mapping corrieri: tipo colonna delivery_carrier.name non "
        "riconosciuto (%s), uso etichetta segnaposto.", data_type)
    return FALLBACK_DISPLAY_SQL


def migrate(cr, version):
    if not version:
        return

    display_expr = _display_expr(cr)
    cr.execute("""
        UPDATE centrivo_carrier_map AS m
           SET source_model = 'delivery.carrier',
               source_res_id = m.carrier_id,
               source_display = {display_expr}
          FROM delivery_carrier AS c
         WHERE c.id = m.carrier_id
           AND m.carrier_id IS NOT NULL
           AND (m.source_res_id IS NULL OR m.source_res_id = 0)
    """.format(display_expr=display_expr))
    _logger.info("Mapping corrieri: %s righe convertite alle chiavi durevoli.",
                 cr.rowcount)

    _rinomina_cron(cr)


# ---------------------------------------------------------------------------
# Rinomina dei cron generici del ciclo ordini/feed.
#
# Si chiamavano "BricoBravo: ..." da quando BricoBravo era l'unico connettore,
# ma il codice non filtra per canale: scorrono TUTTI i canali attivi, ManoMano
# compresa. Il nome era una trappola operativa — si rischiava di accendere
# "BricoBravo: pull ordini" credendo di toccare solo quello e ritrovarsi a
# importare e accettare in automatico anche gli ordini di un altro marketplace.
#
# I record cron sono `noupdate="1"` (le modifiche fatte a mano non vanno perse
# negli aggiornamenti), quindi il nuovo nome nel file dati vale solo per le
# installazioni nuove: qui si rinominano quelle esistenti. Si tocca SOLO
# l'etichetta — frequenza, stato attivo/disattivo e codice restano intatti.
# ---------------------------------------------------------------------------

NUOVI_NOMI_CRON = {
    "cron_pull_all_channels": "Integrations: scarica ordini (tutti i marketplace)",
    "cron_push_shipments": "Integrations: comunica spedizioni (tutti i marketplace)",
    "cron_generate_stock_feeds": "Integrations: genera feed prezzi/giacenze (CSV)",
    "cron_generate_catalog_feeds": "Integrations: genera feed catalogo (CSV)",
}


def _rinomina_cron(cr):
    """Aggiorna l'etichetta dei cron generici, se ancora quella vecchia.

    Dalla 16 `ir.cron` delega a `ir.actions.server`, quindi la colonna `name`
    può stare sull'una o sull'altra tabella a seconda della versione: si guarda
    lo schema invece di darlo per scontato, così la migrazione non solleva.
    """
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'ir_cron' AND column_name = 'name'
           AND table_schema = current_schema()
    """)
    nome_su_ir_cron = bool(cr.fetchone())

    rinominati = 0
    for xmlid, nuovo_nome in NUOVI_NOMI_CRON.items():
        cr.execute("""
            SELECT res_id FROM ir_model_data
             WHERE module = 'integrations_core' AND name = %s AND model = 'ir.cron'
        """, (xmlid,))
        riga = cr.fetchone()
        if not riga:
            continue
        cron_id = riga[0]
        if nome_su_ir_cron:
            cr.execute("UPDATE ir_cron SET name = %s WHERE id = %s",
                       (nuovo_nome, cron_id))
        else:
            cr.execute("""
                UPDATE ir_act_server SET name = %s
                 WHERE id = (SELECT ir_actions_server_id FROM ir_cron WHERE id = %s)
            """, (nuovo_nome, cron_id))
        rinominati += cr.rowcount
    _logger.info("Cron generici rinominati: %s.", rinominati)
