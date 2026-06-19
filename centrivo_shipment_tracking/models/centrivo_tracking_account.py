# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.tracking.account — credenziali/config di tracking per corriere e azienda.

Dice al modulo COME interrogare un corriere (endpoint, ambiente, eventuali
credenziali) e PER QUALI metodi di consegna nativi (delivery_carrier_ids) vale.
Il GLS nodo pubblico può non richiedere credenziali. I segreti NON vengono mai
loggati (sono usati solo dal connettore per la chiamata HTTP).

La risoluzione "quale account/connettore traccia questo picking" avviene cercando
l'account la cui M2m delivery_carrier_ids contiene il corriere del picking.
"""
from odoo import api, fields, models

from ..connectors.base import TrackingConnector


class CentrivoTrackingAccount(models.Model):
    _name = "centrivo.tracking.account"
    _description = "Account di tracking corriere"
    _order = "name"

    name = fields.Char(string="Nome", required=True)

    # Selection dinamico dei connettori di tracking registrati (gemello del
    # pattern marketplace). Si popola con i moduli centrivo_track_* installati.
    tracker_code = fields.Selection(
        selection="_selection_tracker_code", string="Corriere (tracker)",
        required=True,
        help="Connettore di tracking da usare. Le opzioni dipendono dai moduli "
             "centrivo_track_* installati (es. GLS).")

    active = fields.Boolean(string="Attivo", default=True)

    environment = fields.Selection(
        selection=[("production", "Produzione"), ("test", "Collaudo")],
        string="Ambiente", default="production", required=True)

    endpoint = fields.Char(
        string="Endpoint",
        help="URL base del nodo/API di tracking. Se vuoto, il connettore usa il "
             "proprio endpoint di default (es. il nodo pubblico GLS).")

    # Credenziali generiche (gli adattatori che ne hanno bisogno le usano; GLS
    # nodo pubblico può lasciarle vuote). MAI loggate. Si nascondono in UI quando
    # il connettore selezionato non le richiede (requires_credentials).
    api_user = fields.Char(string="Utente API")
    api_password = fields.Char(string="Password/Token API")

    requires_credentials = fields.Boolean(
        string="Richiede credenziali", compute="_compute_requires_credentials",
        help="Dipende dal connettore: i nodi pubblici (GLS) non le richiedono, le "
             "API private (BRT/Poste) sì. Pilota la visibilità dei campi credenziali.")

    delivery_carrier_ids = fields.Many2many(
        "delivery.carrier", "centrivo_tracking_account_carrier_rel",
        "account_id", "carrier_id", string="Metodi di consegna",
        help="Metodi di consegna nativi (delivery.carrier) tracciati da questo "
             "account: una spedizione il cui picking usa uno di questi corrieri "
             "viene tracciata con questo connettore.")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True, index=True,
        default=lambda self: self.env.company)

    @api.model
    def _selection_tracker_code(self):
        """Opzioni del Selection tracker_code: i connettori di tracking registrati."""
        options = TrackingConnector.get_selection()
        return options or [("none", "Nessun adattatore installato")]

    @api.depends("tracker_code")
    def _compute_requires_credentials(self):
        """True se il connettore selezionato richiede credenziali (dal registro)."""
        for account in self:
            account.requires_credentials = (
                TrackingConnector.get_requires_credentials(account.tracker_code)
                if account.tracker_code else True)

    @api.onchange("tracker_code")
    def _onchange_tracker_code(self):
        """Precompila l'endpoint col default del connettore se vuoto."""
        if self.tracker_code and not self.endpoint:
            default_endpoint = TrackingConnector.get_default_endpoint(self.tracker_code)
            if default_endpoint:
                self.endpoint = default_endpoint
