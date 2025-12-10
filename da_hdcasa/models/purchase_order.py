# Copyright (C) 2022-Today:
# Dinamiche Aziendali Srl (<http://www.dinamicheaziendali.it/>)
# @author: Gianmarco Conte <gconte@dinamicheaziendali.it>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models


class PurchaseOrderInherit(models.Model):
    _inherit = "purchase.order"

    dest_country_id = fields.Many2one(
        "res.country",
        string="Nazione",
        related="dest_address_id.country_id",
        store=True,
    )
