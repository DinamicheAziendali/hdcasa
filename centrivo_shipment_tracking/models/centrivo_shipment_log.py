# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment.log — log PROPRIO del modulo tracking (autosufficienza).

Il tracking ha il suo log dedicato (non condivide il log dei connettori
marketplace): la suite è autosufficiente. Registra gli esiti delle operazioni di
tracking (poll, normalizzazione stato, risoluzione corriere) con un estratto
TRONCATO della risposta grezza per la diagnosi. NB: il nodo GLS è pubblico e non ha
credenziali; per gli adattatori che ne avranno (BRT/Poste) il raw_excerpt NON deve
mai contenere segreti — chi scrive il log è responsabile di non passarli.
"""
from odoo import fields, models


class CentrivoShipmentLog(models.Model):
    _name = "centrivo.shipment.log"
    _description = "Log operazioni di tracking"
    _order = "event_datetime desc, id desc"

    shipment_id = fields.Many2one(
        "centrivo.shipment", string="Spedizione", ondelete="cascade", index=True)
    parcel_id = fields.Many2one(
        "centrivo.shipment.parcel", string="Collo", ondelete="cascade", index=True)
    tracker_code = fields.Char(string="Corriere (tracker)", index=True)

    operation = fields.Char(
        string="Operazione", required=True, index=True,
        help="Es. poll_tracking, normalize_status, resolve_carrier.")
    result = fields.Selection(
        selection=[("ok", "OK"), ("skip", "Saltato"), ("error", "Errore")],
        string="Esito", required=True, index=True)
    message = fields.Text(string="Messaggio")
    raw_excerpt = fields.Text(
        string="Estratto risposta (troncato)",
        help="Estratto della risposta grezza del corriere, troncato. MAI "
             "credenziali/segreti.")

    event_datetime = fields.Datetime(
        string="Data/ora", default=fields.Datetime.now, index=True)

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True, index=True,
        default=lambda self: self.env.company)
