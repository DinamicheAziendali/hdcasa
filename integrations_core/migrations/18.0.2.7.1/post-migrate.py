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


def _tipo_colonna(cr, tabella, colonna):
    """Tipo SQL di una colonna, o None se non esiste."""
    cr.execute("""
        SELECT data_type FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
           AND table_schema = current_schema()
    """, (tabella, colonna))
    riga = cr.fetchone()
    return riga[0] if riga else None


def _sql_rinomina(tabella, colonna_tipo, condizione_id):
    """UPDATE che scrive l'etichetta rispettando il tipo reale della colonna.

    In Odoo 18 il nome dell'azione server è un campo TRADOTTO, quindi in
    database è `jsonb`: scriverci una stringa semplice fa fallire la query con
    "invalid input syntax for type json". Quando la colonna è jsonb si
    riscrivono tutte le lingue già presenti col nuovo testo (e se non ce n'è
    nessuna si crea `en_US`), così non si perdono chiavi per strada.
    """
    if colonna_tipo == "jsonb":
        valore = ("""COALESCE(
                     (SELECT jsonb_object_agg(k, %s)
                        FROM jsonb_each_text(COALESCE(name, '{}'::jsonb)) AS t(k, v)),
                     jsonb_build_object('en_US', %s))""")
    else:
        valore = "%s"
    return "UPDATE {tabella} SET name = {valore} WHERE {condizione}".format(
        tabella=tabella, valore=valore, condizione=condizione_id)


def _rinomina_cron(cr):
    """Aggiorna l'etichetta dei cron generici. NON PUÒ far fallire l'update.

    È un'operazione cosmetica: se qualcosa va storto si logga e si tira dritto,
    perché nessun cliente deve vedersi bloccare l'installazione di un modulo per
    il nome di un cron. Il SAVEPOINT serve proprio a questo: in PostgreSQL una
    query fallita invalida l'intera transazione, quindi un try/except da solo
    non basterebbe a proseguire.

    Dalla 16 `ir.cron` delega il nome a `ir.actions.server`: si guarda lo schema
    invece di darlo per scontato, e si rispetta il TIPO della colonna (in Odoo 18
    è jsonb, perché il nome è tradotto).
    """
    cr.execute("SAVEPOINT rinomina_cron")
    try:
        if _tipo_colonna(cr, "ir_cron", "name"):
            tabella = "ir_cron"
            condizione = "id = %s"
            tipo = _tipo_colonna(cr, "ir_cron", "name")
        else:
            tabella = "ir_act_server"
            condizione = ("id = (SELECT ir_actions_server_id FROM ir_cron "
                          "WHERE id = %s)")
            tipo = _tipo_colonna(cr, "ir_act_server", "name")
        sql = _sql_rinomina(tabella, tipo, condizione)

        rinominati = 0
        for xmlid, nuovo_nome in NUOVI_NOMI_CRON.items():
            cr.execute("""
                SELECT res_id FROM ir_model_data
                 WHERE module = 'integrations_core' AND name = %s
                   AND model = 'ir.cron'
            """, (xmlid,))
            riga = cr.fetchone()
            if not riga:
                continue
            parametri = ((nuovo_nome, nuovo_nome, riga[0]) if tipo == "jsonb"
                         else (nuovo_nome, riga[0]))
            cr.execute(sql, parametri)
            rinominati += cr.rowcount
    except Exception as exc:  # noqa: BLE001 - cosmetico: mai bloccare l'update
        cr.execute("ROLLBACK TO SAVEPOINT rinomina_cron")
        _logger.warning(
            "Rinomina dei cron generici non riuscita (%s): i cron restano col "
            "nome precedente. Nessun impatto sul funzionamento.", exc)
    else:
        cr.execute("RELEASE SAVEPOINT rinomina_cron")
        _logger.info("Cron generici rinominati: %s.", rinominati)
