from odoo import fields, models, api


class InboundShipmentPlanLineNew(models.Model):
    _name = 'inbound.shipment.plan.line.new'
    _description = 'Inbound Shipment Plan Line New'

    amazon_product_id = fields.Many2one('amazon.product.ept', string='Amazon Product',
                                        domain=[('fulfillment_by', '=', 'FBA')])
    odoo_product_id = fields.Many2one('product.product', related="amazon_product_id.product_id", string="Odoo Product")
    quantity = fields.Float(string="Quantity")
    seller_sku = fields.Char(size=120, string='Seller SKU', related="amazon_product_id.seller_sku", readonly=True)
    shipment_new_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Inbound Shipment Plan')
    label_owner = fields.Selection([('AMAZON', 'AMAZON'), ('SELLER', 'SELLER'), ('NONE', 'NONE')], string='Label Owner')
    prep_owner = fields.Selection([('AMAZON', 'AMAZON'), ('SELLER', 'SELLER'), ('NONE', 'NONE')], string='Prep Owner')
    expiration = fields.Date(string='Expiration Date')
    manufacturing_lot_code = fields.Char(string='Manufacturing Lot Code')

    _sql_constraints = [('check_quantity', "CHECK(quantity > 0.0)", 'Quantity must be greater than zero!')]

    @api.onchange('shipment_new_plan_id')
    def onchange_shipment_new_plan_id(self):
        if self.shipment_new_plan_id:
            self.label_owner = self.shipment_new_plan_id.label_owner
            self.prep_owner = self.shipment_new_plan_id.prep_owner
