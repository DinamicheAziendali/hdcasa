from odoo import fields, models, _, api
from odoo.exceptions import UserError
from odoo.addons.iap.tools import iap_tools
from ..endpoint import DEFAULT_ENDPOINT
from datetime import datetime
import ast


class DeliveryWindowOptionDetails(models.TransientModel):
    _name = "inbound.shipment.delivery.window.option.details"
    _description = 'Inbound shipment delivery window options details'

    inbound_shipment_list_delivery_window_option_ids = fields.One2many('inbound.shipment.list.delivery.window.option.ept',
                                                                      'inbound_shipment_delivery_window_details_wizard_id',
                                                                      string="Inbound Shipment Delivery Window List")
    shipment_ids = fields.Text(string="Shipment Ids", help="Shipment ids for delivery window options")
    is_multiple_shipment = fields.Boolean(string="Is Multiple Shipment?", default=False)
    shipment_count = fields.Integer(string='Shipment Count', help="This Field relocates the shipment count.")
    is_confirm_placement_option = fields.Boolean(string="Is Imported Shipment?", default=False)

    @api.model
    def default_get(self, fields):
        """
        Define this method for list the placement options.
        :param fields: []
        :return: update result dict {}
        """
        res = super(DeliveryWindowOptionDetails, self).default_get(fields)
        delivery_window_options = self._context.get('delivery_window_options', [])
        shipment_plan_id = self._context.get('shipment_plan_id', False)
        ship_plan_rec = self.env['inbound.shipment.plan.new.ept'].browse(shipment_plan_id)
        placement_option_id = ship_plan_rec.amz_get_selected_placement_option_ept(ship_plan_rec.id)
        shipment_ids = ast.literal_eval(placement_option_id.shipment_ids) if placement_option_id.shipment_ids else ''
        if not shipment_ids:
            raise UserError(_("Selected placement option shipment id not found in the ERP "
                              "for this shipment plan."))
        if len(shipment_ids) > 1:
            res.update({'shipment_ids': shipment_ids, 'is_multiple_shipment': True,
                        'shipment_count': placement_option_id.shipment_count})
        result = []
        for shipment_id, delivery_window_option in delivery_window_options.items():
            # delivery_window_option = delivery_window_option[0]
            availability_type = delivery_window_option[0].get('availabilityType', '')
            end_date = datetime.strptime(delivery_window_option[0].get('endDate', ''), '%Y-%m-%dT%H:%MZ')
            start_date = datetime.strptime(delivery_window_option[0].get('startDate', ''), '%Y-%m-%dT%H:%MZ')
            valid_until_date = datetime.strptime(delivery_window_option[0].get('validUntil', ''), '%Y-%m-%dT%H:%MZ')
            delivery_window_option_id = delivery_window_option[0].get('deliveryWindowOptionId', '')
            placement_data = {
                'availability_type': availability_type,
                'window_start_date': start_date,
                'window_end_date': end_date,
                'window_valid_until_date': valid_until_date,
                'delivery_window_option_id': delivery_window_option_id,
                'shipment_plan_id': ship_plan_rec.id,
                'shipment_id': shipment_id,
            }
            result.append((0, 0, placement_data))
        res.update({'inbound_shipment_list_delivery_window_option_ids': result,
                    'is_confirm_placement_option': ship_plan_rec.is_confirm_placement_option})
        return res

    def confirm_delivery_window_option_sp_api_v2024(self):
        """
        Define this method for confirm transportation options.
        :return: True
        """
        if not self.inbound_shipment_list_delivery_window_option_ids.filtered(lambda l: l.is_selected_delivery_window):
            raise UserError(_("Please select option from the list.."))
        if not self.is_multiple_shipment and len(self.inbound_shipment_list_delivery_window_option_ids.filtered(
                lambda l: l.is_selected_delivery_window)) > 1:
            raise UserError(_("You can select only one option from the list."))
        if self.is_multiple_shipment and len(self.inbound_shipment_list_delivery_window_option_ids.filtered(
                lambda l: l.is_selected_delivery_window)) != self.shipment_count:
            raise UserError(_(f"You need to select exact {self.shipment_count} delivery window option from the list."))
        if self.is_multiple_shipment:
            selected_options = self.inbound_shipment_list_delivery_window_option_ids.filtered(
                lambda l: l.is_selected_delivery_window)
            for shipment_id in ast.literal_eval(self.shipment_ids):
                if shipment_id not in selected_options.mapped('shipment_id'):
                    raise UserError(_(f"You have not selected {shipment_id} shipment id option from the list."))
                if shipment_id in selected_options.mapped('shipment_id') and len(selected_options.filtered(
                        lambda l: l.shipment_id == shipment_id)) > 1:
                    raise UserError(_(f"Select only once option from the list for {shipment_id} shipment id."))
        selected_delivery_window_options = self.inbound_shipment_list_delivery_window_option_ids.filtered(
            lambda l: l.is_selected_delivery_window)
        for delivery_window_option in selected_delivery_window_options:
            kwargs = delivery_window_option.shipment_plan_id.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
                delivery_window_option.shipment_plan_id.instance_id, 'confirm_delivery_window_options_sp_api_v2024')
            kwargs.update({'inboundPlanId': delivery_window_option.shipment_plan_id.inbound_plan_id,
                           'shipmentId': delivery_window_option.shipment_id,
                           'deliveryWindowOptionId': delivery_window_option.delivery_window_option_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            delivery_window_option.shipment_plan_id.amz_create_inbound_shipment_check_status(
                delivery_window_option.shipment_plan_id.id, 'confirm_delivery_window_options',
                response.get('result', {}).get('operationId', ''))
            if not delivery_window_option.shipment_plan_id.is_confirm_delivery_window_options:
                delivery_window_option.shipment_plan_id.write({'is_confirm_delivery_window_options': True})
            self.amz_create_selected_delivery_window_option_record(delivery_window_option)
        return True

    def amz_create_selected_delivery_window_option_record(self, delivery_window_option):
        """
        Define this method for create selected delivery window option record.
        :param: delivery_window_option: inbound.shipment.list.delivery.window.option.ept()
        :return
        """
        self.env['amz.selected.placement.option.ept'].create({
            'availability_type': delivery_window_option.availability_type,
            'window_start_date': delivery_window_option.window_start_date,
            'window_end_date': delivery_window_option.window_end_date,
            'window_valid_until_date': delivery_window_option.window_valid_until_date,
            'delivery_window_option_id': delivery_window_option.delivery_window_option_id,
            'placement_status': 'delivery_window_option',
            'shipment_ids': delivery_window_option.shipment_id,
            'shipment_plan_id': delivery_window_option.shipment_plan_id.id
        })


class ListDeliveryWindowOptions(models.TransientModel):
    _name = 'inbound.shipment.list.delivery.window.option.ept'
    _description = 'Inbound Shipment List Delivery Window Option'

    availability_type = fields.Selection([('AVAILABLE', 'AVAILABLE'),
                                          ('CONGESTED', 'CONGESTED')], string='Availability Type')
    window_start_date = fields.Datetime(string="Start Date", help="Shipment window start date.")
    window_end_date = fields.Datetime(string="End Date", help="Shipment window end date.")
    window_valid_until_date = fields.Datetime(string="Valid Until Date", help="Shipment window valid until date.")
    delivery_window_option_id = fields.Char(string='Delivery Window Option Id', help="Delivery window option id")
    shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Inbound Plan',
                                       help="Inbound shipment plan id")
    shipment_id = fields.Char(string="Shipment Id", help="Shipment id")
    is_selected_delivery_window = fields.Boolean("Is Selected Window?", default=False)
    inbound_shipment_delivery_window_details_wizard_id = fields.Many2one("inbound.shipment.delivery.window.option.details",
                                                                         string="Inbound Shipment Delivery Window Details Wizard")
