# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Registro delle righe d'ordine gia' comunicate come spedite a Temu.

Serve a una cosa sola, ma decisiva: **un numero di tracking su Temu si puo'
usare una volta sola**, e una riga d'ordine gia' comunicata non va ricomunicata.
Quando un ordine viene spedito in piu' picking, la comunicazione avviene in piu'
riprese: senza una traccia di cosa e' gia' partito, il secondo giro rimanderebbe
anche le righe del primo e Temu rifiuterebbe tutto.
"""
from odoo import fields, models


class CentrivoTemuShipment(models.Model):
    _name = "centrivo.temu.shipment"
    _description = "Riga d'ordine comunicata come spedita a Temu"
    _order = "create_date desc"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)
    parent_order_sn = fields.Char(
        string="Ordine Temu", required=True, index=True)
    order_sn = fields.Char(
        string="Riga ordine Temu", required=True, index=True)
    quantity = fields.Integer(string="Quantità comunicata")
    carrier_code = fields.Char(string="Corriere (codice Temu)")
    tracking_number = fields.Char(string="Numero di spedizione")
    picking_id = fields.Many2one(
        "stock.picking", string="Trasferimento", ondelete="set null")
    company_id = fields.Many2one(
        "res.company", string="Azienda", related="channel_id.company_id",
        store=True, index=True)

    _sql_constraints = [
        ("temu_shipment_uniq", "unique(channel_id, order_sn)",
         "Questa riga d'ordine è già stata comunicata come spedita a Temu."),
    ]
