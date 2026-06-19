# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Estensione centrivo.tracking.account per Poste — campo `poste_scope` (OAuth2).

L'auth OAuth2 di Poste richiede uno `scope`. È un default TECNICO di produzione (non
un segreto), quindi lo teniamo come default del campo; resta configurabile sull'account
nel caso Poste lo cambi. Usato solo dal connettore Poste.
"""
from odoo import fields, models

from ..connectors.poste import POSTE_DEFAULT_SCOPE


class CentrivoTrackingAccount(models.Model):
    _inherit = "centrivo.tracking.account"

    poste_scope = fields.Char(
        string="Poste scope (OAuth2)", default=POSTE_DEFAULT_SCOPE,
        help="Scope OAuth2 per l'autenticazione Poste (client_credentials). "
             "Default tecnico di produzione; usato solo dal connettore Poste. "
             "Lascia il default salvo indicazioni di Poste.")
