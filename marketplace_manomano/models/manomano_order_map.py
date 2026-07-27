# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Estensione di centrivo.order.map per ManoMano: rifiuto ordine.

Il campo e l'azione stanno nel modulo del marketplace, non nel core: è la
regola del progetto per tutto ciò che è specifico di un canale.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError


class CentrivoOrderMap(models.Model):
    _inherit = "centrivo.order.map"

    manomano_refused = fields.Boolean(
        string="Rifiutato su ManoMano", readonly=True, copy=False,
        help="L'ordine è stato rifiutato su ManoMano. Azione irreversibile.")

    manomano_is_manomano = fields.Boolean(
        string="Canale ManoMano", compute="_compute_manomano_is_manomano")

    @api.depends("channel_id.connector_code")
    def _compute_manomano_is_manomano(self):
        for record in self:
            record.manomano_is_manomano = (
                record.channel_id.connector_code == "manomano")

    def action_manomano_refuse(self):
        """Bottone: rifiuta l'ordine su ManoMano. Irreversibile.

        Solo sugli ordini dei canali ManoMano. L'esito finisce nel Log
        operazioni; l'eventuale fallimento non lascia il record in uno stato
        incoerente (manomano_refused resta False e si può ritentare).
        """
        self.ensure_one()
        if self.channel_id.connector_code != "manomano":
            raise UserError(
                "Questo ordine non arriva da ManoMano: non si può rifiutare da qui.")
        if self.manomano_refused:
            raise UserError("Questo ordine risulta già rifiutato su ManoMano.")
        ok = self.channel_id._get_connector().refuse_order(
            self.external_id, order_map=self)
        if ok:
            message = "Ordine rifiutato correttamente su ManoMano."
            kind = "success"
        else:
            message = (
                "Il rifiuto NON è stato registrato su ManoMano: leggi il "
                "dettaglio nel Log operazioni prima di ritentare.")
            kind = "danger"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Ordine ManoMano",
                "message": message,
                "type": kind,
                "sticky": False,
            },
        }
