from odoo import fields, models, api, _


class InboundShipmentPlanNewEpt(models.Model):
    _name = 'new.inbound.shipment.plan.check.status.ept'
    _description = 'Inbound Shipment Plan Check Status'
    _inherit = ['mail.thread']
    _order = 'id desc'

    operation = fields.Selection([('create_shipment_plan', 'Create Shipment Plan'),
                                  ('generate_packing_options', 'Generate Packing Options'),
                                  ('confirm_packing_option', 'Confirm Packing Option'),
                                  ('set_packing_information', 'Set Packing Information'),
                                  ('generate_placement_options', 'Generate Placement Options'),
                                  ('confirm_placement_options', 'Confirm Placement Options'),
                                  ('generate_transportation_options', 'Generate Transportation Options'),
                                  ('generate_delivery_window_options', 'Generate Delivery Window Options'),
                                  ('confirm_transportation_options', 'Confirm Transportation Options'),
                                  ('confirm_delivery_window_options', 'Confirm Delivery Window Options'),
                                  ('cancel_shipment_plan', 'Cancel Shipment Plan')],
                                 string='Operation Type')
    operation_id = fields.Char(string='Operation Type ID')
    inbound_shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Inbound Shipment Plan')
