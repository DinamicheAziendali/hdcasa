# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

"""
Added class to store the amazon carton details and added fields
"""
from odoo import models, fields


class CartonContentInfo(models.Model):
    """
    Added class to store the amazon carton details (amazon product, pkg information, seller, and
    quantity)
    """
    _name = "amazon.carton.content.info.ept"
    _description = 'amazon.carton.content.info.ept'
    _rec_name = "packing_group_id"

    package_id = fields.Many2one("stock.quant.package", string="Package")
    amazon_product_id = fields.Many2one("amazon.product.ept", string="Amazon Product")
    seller_sku = fields.Char(size=120, related="amazon_product_id.seller_sku", readonly=True)
    quantity = fields.Float("Carton Qty", digits=(16, 2))
    inbound_shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', "Inbound Shipment Plan")
    packing_group_id = fields.Char('Packing Group Id')
    package_info_ids = fields.One2many("stock.quant.package", "amz_carton_info_id",
                                       string="Package Info")
