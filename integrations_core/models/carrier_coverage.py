# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.carrier.coverage — cosa succederebbe se spedissi adesso.

Una riga per corriere e canale attivo, con lo stato della traduzione. Serve a
scoprire i buchi PRIMA di spedire: un corriere senza traduzione si manifesta
altrimenti solo al momento del push, davanti a un ordine del cliente.
Non scrive nulla e non chiama nessun marketplace.
"""
from odoo import api, fields, models

from ..connectors.base import MarketplaceConnector
from ..connectors.carrier_resolver import code_for_brand


class CentrivoCarrierCoverage(models.TransientModel):
    _name = "centrivo.carrier.coverage"
    _description = "Copertura corrieri sui marketplace"
    _order = "brand_name, channel_name"

    brand_id = fields.Many2one("centrivo.carrier.brand", string="Corriere")
    brand_name = fields.Char(string="Corriere", readonly=True)
    channel_id = fields.Many2one("centrivo.channel", string="Canale")
    channel_name = fields.Char(string="Canale", readonly=True)
    state = fields.Selection(
        [("auto", "Automatico"), ("override", "Eccezione"), ("missing", "Manca")],
        string="Stato", readonly=True)
    external_code = fields.Char(string="Codice inviato", readonly=True)
    source_names = fields.Char(string="Vettori collegati", readonly=True)

    @api.model
    def action_rebuild(self):
        """Ricostruisce la fotografia e apre l'elenco."""
        Brand = self.env["centrivo.carrier.brand"]
        Channel = self.env["centrivo.channel"]
        Source = self.env["centrivo.carrier.source"]
        Override = self.env["centrivo.carrier.override"]

        righe = []
        canali = Channel.search([])
        for brand in Brand.search([]):
            vettori = Source.search([("brand_id", "=", brand.id)])
            nomi = ", ".join(sorted(v.source_display or "?" for v in vettori))
            for channel in canali:
                override = Override.resolve_code(
                    channel, brand, channel.company_id)
                codice = code_for_brand(
                    brand.code,
                    MarketplaceConnector.get_brand_codes_for(
                        channel.connector_code),
                    override_code=override)
                righe.append({
                    "brand_id": brand.id,
                    "brand_name": brand.name,
                    "channel_id": channel.id,
                    "channel_name": channel.name,
                    "state": ("override" if override
                              else "auto" if codice else "missing"),
                    "external_code": codice or "",
                    "source_names": nomi,
                })
        records = self.create(righe) if righe else self.browse()
        return {
            "type": "ir.actions.act_window",
            "name": "Copertura corrieri",
            "res_model": "centrivo.carrier.coverage",
            "view_mode": "list",
            "domain": [("id", "in", records.ids)],
            "target": "current",
        }
