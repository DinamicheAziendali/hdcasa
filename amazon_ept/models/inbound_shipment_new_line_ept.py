from odoo import fields, models, api


class InboundShipmentLineNew(models.Model):
    _name = 'inbound.shipment.line.new.ept'
    _description = 'Inbound Shipment Line New'

    amazon_product_id = fields.Many2one('amazon.product.ept', string='Amazon Product',
                                        domain=[('fulfillment_by', '=', 'FBA')])
    odoo_product_id = fields.Many2one('product.product', related="amazon_product_id.product_id", string="Odoo Product")
    asin = fields.Char(string="Asin")
    expiration = fields.Date(string='Expiration Date')
    fn_sku = fields.Char(size=120, string='Fulfillment SKU', related="amazon_product_id.seller_sku", readonly=True)
    label_owner = fields.Selection([('AMAZON', 'AMAZON'), ('SELLER', 'SELLER'), ('NONE', 'NONE')], string='Label Owner')
    manufacturing_lot_code = fields.Char(string='Manufacturing Lot Code')
    seller_sku = fields.Char(size=120, string='Seller SKU', related="amazon_product_id.seller_sku", readonly=True)
    prep_owner = fields.Selection([('AMAZON', 'AMAZON'), ('SELLER', 'SELLER'), ('NONE', 'NONE')], string='Prep Owner')
    shipment_new_id = fields.Many2one('inbound.shipment.new.ept', string='Inbound Shipment')
    quantity = fields.Float(string="Quantity")
    received_qty = fields.Float(string="Received Quantity", default=0.0, copy=False, help="Received Quantity")
    difference_qty = fields.Float(string="Difference Quantity", compute="_compute_total_difference_qty",
                                  help="Difference Quantity")
    is_extra_line = fields.Boolean(string="Extra Line ?", default=False, help="Extra Line ?")

    def _compute_total_difference_qty(self):
        for shipment_line in self:
            shipment_line.difference_qty = shipment_line.quantity - shipment_line.received_qty

