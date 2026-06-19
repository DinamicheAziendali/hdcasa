# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Estensione centrivo.tracking.account per GLS — credenziali infoweb (TASK_69).

La fonte infoweb GLS usa due parametri (nessun user/password):
  - gls_sender_code   : sigla sede GLS (locpartenza, es. SA);
  - gls_contract_code : Codice CONTRATTO GLS (CodCli, es. 4665) — NON il Codice Cliente.

Il vecchio `caller` (nodo pubblico gls-group.com, TASK_44) NON serve più con la nuova
fonte: resta a schema per retrocompatibilità (niente drop di colonna/dato) ma è
deprecato e NON viene più mostrato in UI.
"""
from odoo import fields, models


class CentrivoTrackingAccount(models.Model):
    _inherit = "centrivo.tracking.account"

    gls_sender_code = fields.Char(
        string="Sigla sede GLS",
        help="Sigla della sede GLS di partenza (parametro 'locpartenza', es. SA). "
             "È anche il prefisso del numero spedizione, che viene tolto per comporre "
             "il NumSped. Usato solo dal connettore GLS.")
    gls_contract_code = fields.Char(
        string="Codice contratto GLS",
        help="Codice CONTRATTO GLS (parametro 'CodCli', es. 4665). NON è il Codice "
             "Cliente. Usato solo dal connettore GLS.")

    # Deprecato (vecchio nodo pubblico): mantenuto a schema per retrocompatibilità,
    # non mostrato in UI. Non più usato dal connettore infoweb.
    caller = fields.Char(
        string="GLS caller (deprecato)", default="witt002",
        help="DEPRECATO: parametro del vecchio nodo pubblico GLS (non più usato dalla "
             "fonte infoweb). Mantenuto solo per retrocompatibilità.")
