# Copyright (C) 2026-Today:
# Dinamiche Aziendali Srl (<http://www.dinamicheaziendali.it/>)
# @author: Giuseppe Borruso <gborruso@dinamicheaziendali.it>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models
from odoo.tools.float_utils import float_compare


class SaleOrderInherit(models.Model):
    _inherit = "sale.order"

    def _check_qty_stock_vs_dropship(self):
        self.ensure_one()

        dropship_route = self.env.ref(
            "stock_dropshipping.route_drop_shipping",
            raise_if_not_found=False
        )
        if not dropship_route:
            return

        stock_route = False
        if "delivery_route_id" in self.warehouse_id._fields:
            stock_route = self.warehouse_id.delivery_route_id

        consumed_by_product = {}

        lines = self.order_line.filtered(
            lambda ol: ol.product_id
            and ol.product_id.route_ids.filtered(lambda r: r.id == dropship_route.id)
        )
        for line in lines:
            product = line.product_id

            free_qty_product_uom = product.with_context(
                warehouse_id=self.warehouse_id.id
            ).free_qty

            already_consumed = consumed_by_product.get(product.id, 0.0)
            usable_free_qty = max(free_qty_product_uom - already_consumed, 0.0)

            usable_free_qty_line_uom = product.uom_id._compute_quantity(
                usable_free_qty, line.product_uom
            )

            if float_compare(
                usable_free_qty_line_uom,
                line.product_uom_qty,
                precision_rounding=line.product_uom.rounding,
            ) >= 0:
                line.route_id = stock_route.id if stock_route else False
                consumed_by_product[product.id] = (
                    already_consumed + line.product_uom_qty
                )
            else:
                line.route_id = dropship_route.id

    def action_confirm(self):
        for order in self:
            order._check_qty_stock_vs_dropship()
        return super().action_confirm()
