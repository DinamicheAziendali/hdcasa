# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.job.log — log degli esiti delle operazioni dei connettori.

Visibilità operativa: ogni pull/import/acquired/shipment lascia una riga con
esito, timestamp, payload sintetico (troncato) e messaggio. Utile per capire
cosa è successo senza leggere i log di sistema.
"""
from odoo import fields, models


class IntegrationJobLog(models.Model):
    _name = "centrivo.job.log"
    _description = "Log operazioni dei connettori di integrazione"
    _order = "create_date desc"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", ondelete="cascade", index=True)

    # Tipo di operazione: pull_orders, import_order, mark_acquired, push_shipment.
    operation = fields.Char(string="Operazione", required=True, index=True)

    # Identificativo ordine esterno coinvolto, se applicabile.
    external_id = fields.Char(string="ID esterno")

    result = fields.Selection(
        selection=[("success", "Successo"), ("error", "Errore"), ("skip", "Saltato")],
        string="Esito", required=True, index=True)

    message = fields.Text(string="Messaggio")

    # Payload sintetico/troncato per diagnosi (mai dati sensibili completi).
    payload = fields.Text(string="Payload (troncato)")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company)
