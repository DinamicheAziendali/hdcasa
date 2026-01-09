from odoo import fields, models


class SelectedPlacementOption(models.Model):
    _name = 'amz.selected.placement.option.ept'
    _description = 'Selected Placement Option'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    shipment_discount = fields.Float(string="Discount", help='Placement option discount.')
    shipment_fees = fields.Float(string="Fees", help="Placement option fees.")
    shipment_ids = fields.Text(string="Shipment Ids", help="Placement option shipment ids")
    discount_currency_id = fields.Many2one('res.currency', string='Discount Currency')
    fees_currency_id = fields.Many2one('res.currency', string='Fees Currency')
    placement_option_id = fields.Char(string='Placement Option Id',
                                      help="Shipment placement option id")
    shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Inbound Plan',
                                       help="Inbound shipment plan id")
    status = fields.Selection([('OFFERED', 'OFFERED'), ('ACCEPTED', 'ACCEPTED'),
                               ('EXPIRED', 'EXPIRED')], string='Operation Type')
    shipment_count = fields.Integer(string='Shipment Count', help="This Field relocates the shipment count.")
    packing_group_ids = fields.Text(string="Group Ids", help="Packing Group ids")
    packing_option_id = fields.Char(string='Packing Option Id', help="Packing option id")
    package_count = fields.Integer(string='Package Count', help="This Field relocates the package count.")
    packing_discount = fields.Float(string="Discount", help='Packing option discount.')
    packing_fees = fields.Float(string="Fees", help="Packing option fees.")
    carrier_code = fields.Char(string="Carrier Code", help="Transportation Carrier Code")
    carrier_name = fields.Char(string="Carrier Name", help="Transportation Carrier Name")
    preconditions = fields.Text(string="Preconditions", help="Transportation preconditions")
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
    availability_type = fields.Selection([('AVAILABLE', 'AVAILABLE'),
                                          ('CONGESTED', 'CONGESTED')], string='Availability Type')
    window_start_date = fields.Datetime(string="Start Date", help="Shipment window start date.")
    window_end_date = fields.Datetime(string="End Date", help="Shipment window end date.")
    window_valid_until_date = fields.Datetime(string="Valid Until Date", help="Shipment window valid until date.")
    delivery_window_option_id = fields.Char(string='Delivery Window Option Id', help="Delivery window option id")
    placement_status = fields.Selection([('shipment_placement_option', 'Shipment Placement Option'),
                                         ('packing_placement_option', 'Packing Placement Option'),
                                         ('transportation_option', 'Transportation Option'),
                                         ('delivery_window_option', 'Delivery Window Option')], string='Placement Status')
