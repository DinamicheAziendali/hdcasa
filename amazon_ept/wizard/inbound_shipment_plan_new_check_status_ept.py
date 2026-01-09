from odoo import fields, models, _
from odoo.addons.iap.tools import iap_tools
from odoo.exceptions import UserError
from ..endpoint import DEFAULT_ENDPOINT

class InboundShipmentPlanNewCheckStatus(models.TransientModel):
    _name = 'inbound.shipment.plan.new.check.status.ept'
    _description = 'Inbound Shipment Plan New Check Status'

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

    def amz_check_status_wizard_view(self):
        """
        will return the check status wizard view for checking operation status for inbound plan.
        """
        view = self.env.ref('amazon_ept.view_inbound_shipment_plan_new_check_status_wizard')
        return {
            'name': 'Check Status',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'inbound.shipment.plan.new.check.status.ept',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': self.env.context
        }
    def amz_inbound_operation_check_status(self):
        """
        This method Make request on Seller Central on Amazon to obtain the current status of the specified operation.
        """
        inbound_plan_check_status_obj = self.env['new.inbound.shipment.plan.check.status.ept']
        inbound_plan_id = self.env['inbound.shipment.plan.new.ept'].browse(self._context.get('inbound_shipment_plan_id'))
        kwargs = self.env['inbound.shipment.plan.new.ept'].prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            inbound_plan_id.instance_id, 'check_operation_status_sp_api_v2024')
        operation_id = inbound_plan_check_status_obj.search([('operation', '=', self.operation),
                                                             ('inbound_shipment_plan_id', '=', inbound_plan_id.id)],
                                                            limit=1).operation_id
        if operation_id:
            kwargs.update({'operationId': operation_id})
        else:
            raise UserError(f'Warning: Please ensure the Operation is already performed'
                            f' before checking status for {self.operation} !')
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(response.get('error', {}))
        status = response.get('result', {}).get('operationStatus', '')
        operation_problems = response.get('result', {}).get('operationProblems', [{}])
        sticky = False
        if status == 'SUCCESS':
            client_message = '%s Operation request has Successfully Finished!'% self.operation
            message_type = 'success'
        elif status == 'IN_PROGRESS':
            client_message = '%s Operation request is still in Progress!'% self.operation
            message_type = 'info'
        elif status == 'FAILED':
            client_message = f"{operation_problems[0].get('message', '')}\n{operation_problems[0].get('details', '')}"
            message_type = 'danger'
            sticky = True
        else:
            client_message = 'Unknown status'
            message_type = 'warning'
        if client_message:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Inbound Shipment Plan Status'),
                    'type': message_type,
                    'target': 'new',
                    "message": _(client_message),
                    'next': {'type': 'ir.actions.act_window_close'},
                    'sticky': sticky
                }
        }
