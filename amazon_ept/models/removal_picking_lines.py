# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import models, fields

PRODUCT_UOS = "Product UoS"


class RemovalPickingLines(models.Model):
    _name = "removal.picking.lines.ept"
    _description = "removal.picking.lines.ept"

    amazon_product_id = fields.Many2one('amazon.product.ept', string='Product',
                                        domain=[('fulfillment_by', '=', 'FBA')])
    seller_sku = fields.Char(size=120, string='Seller SKU', related="amazon_product_id.seller_sku",
                             readonly=True)
    sellable_quantity = fields.Float(digits=PRODUCT_UOS)
    unsellable_quantity = fields.Float(digits=PRODUCT_UOS)
    sellable_stock = fields.Float(digits=PRODUCT_UOS,
                                  compute="_compute_sellable_unsellable_stock", readonly=True)
    unsellable_stock = fields.Float(digits=PRODUCT_UOS,
                                    compute="_compute_sellable_unsellable_stock", readonly=True)
    removal_transfer_id = fields.Many2one("amazon.removal.transfer.ept", string="Removal Transfer")
    removal_disposition = fields.Selection([('Return', 'Return'), ('Disposal', 'Disposal'),
                                            ('Liquidations', 'Liquidations')])
    carrier_id = fields.Many2one('delivery.carrier', string='Carrier')
    tracking_ref = fields.Char(string='Tracking Reference', copy=False)
    carrier_name_in_file = fields.Char()

    def _compute_sellable_unsellable_stock(self):
        """
        This Method relocates get stock using line of amazon product id with context passed location
        of removal order of warehouse
        """
        for line in self:
            lot_stock_id = line.removal_transfer_id.instance_id.warehouse_id.lot_stock_id.id
            unsellable_location_id = line.removal_transfer_id.instance_id.fba_warehouse_id.unsellable_location_id.id
            line.sellable_stock = line.amazon_product_id.product_id.with_context( \
                **{'location': lot_stock_id}).qty_available
            line.unsellable_stock = line.amazon_product_id.product_id.with_context( \
                **{'location': unsellable_location_id}).qty_available
