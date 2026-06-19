# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
from . import connectors
from . import models


def post_init_hook(env):
    """Registrazione best-effort dell'istanza al Centrivo License Server.

    FASE 1: solo "ping di registrazione" all'installazione/aggiornamento del modulo.
    Idempotente (un ping a ogni cambio-versione, non a ogni restart) e MAI bloccante
    (timeout corto + try/except totale dentro centrivo.license). Vedi
    models/centrivo_license.py.
    """
    env["centrivo.license"]._register_if_needed()
