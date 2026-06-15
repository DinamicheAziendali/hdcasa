# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.sku.map — mapping SKU esterno <-> variante prodotto Odoo.

OPZIONALE / fallback. Per la v1 BricoBravo il match prodotto è diretto:
  - barcode = EAN dell'item, con fallback su default_code = product_code.
Questo modello serve SOLO per i casi in cui i codici NON coincidano: permette di
mappare a mano un codice esterno alla variante prodotto Odoo corretta. Odoo
resta la fonte di verità: qui si mappa, non si creano prodotti.
"""
from odoo import fields, models


class IntegrationSkuMap(models.Model):
    _name = "centrivo.sku.map"
    _description = "Mapping SKU esterno verso variante prodotto Odoo"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)

    # Codice prodotto usato dal marketplace (EAN o product_code lato canale).
    external_code = fields.Char(string="Codice esterno", required=True, index=True)

    # Variante prodotto Odoo a cui corrisponde.
    product_id = fields.Many2one(
        "product.product", string="Variante prodotto Odoo", required=True,
        ondelete="cascade")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("uniq_channel_external_code",
         "unique(channel_id, external_code)",
         "Esiste già un mapping per questo codice esterno su questo canale."),
    ]
