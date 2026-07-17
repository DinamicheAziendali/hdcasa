# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Migrazione 18.0.2.2.0: snooze a tempo per gli alert gestiti a mano.

Prima di questa versione il tasto "Risolvi" NON sopprimeva la riapertura (l'alert
tornava al polling/valutazione SLA successiva se la condizione era ancora attiva),
mentre "Ignora" sopprimeva in modo INDEFINITO (finché la condizione non decadeva).
Ora entrambi mettono l'alert in SNOOZE a tempo (campo snooze_until).

Per non cambiare comportamento in modo brusco all'aggiornamento, gli alert
'ignored' ancora attivi (che prima erano soppressi a tempo indeterminato)
ricevono uno snooze pari alla durata di default, così NON riemergono in massa
subito dopo l'update. Idempotente: tocca solo i record senza snooze già impostato.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # 48h di calendario in migrazione (approssimazione ragionevole del default
    # lavorativo): serve solo a evitare un'ondata di riaperture all'update.
    cr.execute("""
        UPDATE centrivo_shipment_alert
           SET snooze_until = (now() AT TIME ZONE 'UTC') + interval '48 hours'
         WHERE state = 'ignored'
           AND snooze_until IS NULL
    """)
    if cr.rowcount:
        _logger.info(
            "Snooze migrazione: %s alert 'ignored' silenziati per la durata di "
            "default (evita riaperture immediate post-update).", cr.rowcount)
