# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment.parcel — il singolo collo di una spedizione.

Il modello a due livelli (spedizione/collo) serve a gestire multicollo e consegna
parziale: ogni collo ha il proprio numero di tracking e il proprio stato; lo stato
della spedizione è l'AGGREGATO dei colli (vedi centrivo.shipment). Per i corrieri
che NON espongono i colli singoli (es. nodo pubblico GLS nazionale) la spedizione
ha un unico collo di default allineato al tracking di spedizione.
"""
from odoo import fields, models


class CentrivoShipmentParcel(models.Model):
    _name = "centrivo.shipment.parcel"
    _description = "Collo di una spedizione tracciata"
    _order = "id"

    shipment_id = fields.Many2one(
        "centrivo.shipment", string="Spedizione", required=True,
        ondelete="cascade", index=True)
    tracking_number = fields.Char(string="Numero tracking collo")
    status_id = fields.Many2one(
        "centrivo.shipment.status", string="Stato collo", index=True)
    status_date = fields.Datetime(string="Stato aggiornato il", copy=False)
    delivered_date = fields.Datetime(string="Consegnato il", copy=False)
    last_poll_date = fields.Datetime(string="Ultimo aggiornamento", copy=False)

    company_id = fields.Many2one(
        "res.company", string="Azienda",
        related="shipment_id.company_id", store=True, index=True)

    def _set_status(self, status):
        """Imposta lo stato del collo e aggiorna le date conseguenti."""
        self.ensure_one()
        vals = {"last_poll_date": fields.Datetime.now()}
        if status and self.status_id != status:
            vals["status_id"] = status.id
            vals["status_date"] = fields.Datetime.now()
        if status and status.code == "delivered" and not self.delivered_date:
            vals["delivered_date"] = fields.Datetime.now()
        self.write(vals)
        return self
