# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Congela i canali ESISTENTI sulla risoluzione immagini di prima (1920 px).

Questa versione introduce `feed_image_resolution` con default 1024 px, per
alleggerire lo scaricamento delle immagini da parte dei marketplace. Il default
di un campo nuovo verrebbe applicato anche ai canali già configurati: BricoBravo
è VIVO in produzione con l'originale a 1920 px, e non si cambia un canale che
vende senza una decisione esplicita.

Qui quindi si scrive 1920 su tutto ciò che esisteva PRIMA di questo
aggiornamento; i canali creati d'ora in poi nascono a 1024. Chi vuole
alleggerire un canale esistente lo fa dalla scheda del canale, con un clic.

Gira in PRE-migrate: la colonna si crea e si riempie prima che l'ORM applichi il
default, così non c'è una finestra in cui i canali esistenti risultino a 1024.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # La colonna non esiste ancora (l'ORM la creerà subito dopo): la creiamo noi
    # e la riempiamo, così il default 1024 non tocca i canali già configurati.
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'centrivo_channel'
           AND column_name = 'feed_image_resolution'
    """)
    if not cr.fetchone():
        cr.execute("ALTER TABLE centrivo_channel "
                   "ADD COLUMN feed_image_resolution VARCHAR")

    cr.execute("""
        UPDATE centrivo_channel
           SET feed_image_resolution = '1920'
         WHERE feed_image_resolution IS NULL
    """)
    _logger.info(
        "integrations_core: %s canali esistenti congelati a 1920 px "
        "(i nuovi nascono a 1024).", cr.rowcount)
