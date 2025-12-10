# Copyright (C) 2018-Today:
# Dinamiche Aziendali Srl (<http://www.dinamicheaziendali.it/>)
# @author: Giuseppe Borruso <gborruso@dinamicheaziendali.it>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models


class ProductProductInherit(models.Model):
    _inherit = "product.product"

    def _get_vendor_code(self):
        seller_id = self.seller_ids
        if seller_id:
            return seller_id[0].product_code
        else:
            return ""
