# Copyright (C) 2022-Today:
# Dinamiche Aziendali Srl (<http://www.dinamicheaziendali.it/>)
# @author: Gianmarco Conte <gconte@dinamicheaziendali.it>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import models


class StockPickingInherit(models.Model):
    _inherit = "stock.picking"

    def get_shipping_dest(self):
        for picking in self:
            partner = False
            if picking.picking_type_id.dropshipping:
                partner = picking.sale_id.partner_shipping_id
            else:
                partner = picking.partner_id
            if partner.parent_id:
                if not partner.parent_id.print_child_in_label:
                    partner = partner.parent_id
            return partner
