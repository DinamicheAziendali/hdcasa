# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment.event — checkpoint di tracking (storico, append-only).

Ogni interrogazione del corriere accoda i NUOVI checkpoint (deduplicati). È la
materia prima della timeline e, in futuro, dell'analitica (tempi tra checkpoint,
filiale che ha gestito il collo). Lo `status_id` è lo stato Centrivo normalizzato
all'epoca dell'evento (quando il checkpoint porta un codice mappabile).
"""
from odoo import fields, models


class CentrivoShipmentEvent(models.Model):
    _name = "centrivo.shipment.event"
    _description = "Evento (checkpoint) di tracking"
    _order = "event_datetime desc, id desc"

    shipment_id = fields.Many2one(
        "centrivo.shipment", string="Spedizione", required=True,
        ondelete="cascade", index=True)
    parcel_id = fields.Many2one(
        "centrivo.shipment.parcel", string="Collo", ondelete="set null", index=True)

    event_datetime = fields.Datetime(string="Data/ora evento", index=True)
    raw_code = fields.Char(string="Codice grezzo")
    raw_description = fields.Char(string="Descrizione corriere")
    status_id = fields.Many2one(
        "centrivo.shipment.status", string="Stato (normalizzato)")
    location = fields.Char(string="Località")
    branch_raw = fields.Char(
        string="Filiale (grezza)",
        help="Nome del deposito/filiale così come restituito dal corriere "
             "(stringa grezza, normalizzata in una fase successiva).")

    company_id = fields.Many2one(
        "res.company", string="Azienda",
        related="shipment_id.company_id", store=True, index=True)
