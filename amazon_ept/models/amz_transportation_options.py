from odoo import fields, models


class AmzTransportationOptions(models.Model):
    _name = 'amz.transportation.option.ept'
    _description = 'Amazon Transportation Options'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = "carrier_name"

    carrier_code = fields.Char(string="Carrier Code", help="Transportation Carrier Code")
    carrier_name = fields.Char(string="Carrier Name", help="Transportation Carrier Name")
    preconditions = fields.Text(string="Preconditions", help="Transportation preconditions")
    shipment_id = fields.Char(string="Shipment Id", help="Shipment id")
    shipping_mode = fields.Selection([('GROUND_SMALL_PARCEL', 'GROUND SMALL PARCEL'), ('FREIGHT_LTL', 'FREIGHT LTL'),
                                      ('FREIGHT_FTL_PALLET', 'FREIGHT FTL PALLET'),
                                      ('FREIGHT_FTL_NONPALLET', 'FREIGHT FTL NONPALLET'),
                                      ('OCEAN_LCL', 'OCEAN LCL'), ('OCEAN_FCL', 'OCEAN FCL'),
                                      ('AIR_SMALL_PARCEL', 'AIR SMALL PARCEL'),
                                      ('AIR_SMALL_PARCEL_EXPRESS', 'AIR SMALL PARCEL EXPRESS')],
                                     string='Shipping Mode')
    shipping_solution = fields.Selection([('AMAZON_PARTNERED_CARRIER', 'AMAZON PARTNERED CARRIER'),
                                          ('USE_YOUR_OWN_CARRIER', 'USE YOUR OWN CARRIER')], string='Shipping Solution')
    transportation_option_id = fields.Char(string='Transportation Option Id', help="Transportation option id")
    shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Inbound Plan',
                                       help="Inbound shipment plan id")
