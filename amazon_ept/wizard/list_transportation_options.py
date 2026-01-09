from odoo import fields, models, _, api
from odoo.exceptions import UserError
from odoo.addons.iap.tools import iap_tools
from ..endpoint import DEFAULT_ENDPOINT
import ast


class TransportationOptionDetails(models.TransientModel):
    _name = "inbound.shipment.transportation.option.details"
    _description = 'Inbound shipment transportation details'

    inbound_shipment_list_transportation_option_ids = fields.One2many('inbound.shipment.list.transportation.option.ept',
                                                                      'inbound_shipment_transportation_details_wizard_id',
                                                                      string="Inbound Shipment Transportation List")
    shipment_ids = fields.Text(string="Shipment Ids", help="Shipment ids for transportation options")
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
        res = super(TransportationOptionDetails, self).default_get(fields)
        transportation_options = self._context.get('transportation_options', [])
        shipment_plan_id = self._context.get('shipment_plan_id', False)
        ship_plan_rec = self.env['inbound.shipment.plan.new.ept'].browse(shipment_plan_id)
        amz_transportation_option_obj = self.env['amz.transportation.option.ept']
        # below method provided selected placement option for the shipment plan
        placement_option_id = ship_plan_rec.amz_get_selected_placement_option_ept(ship_plan_rec.id)
        shipment_ids = ast.literal_eval(placement_option_id.shipment_ids) if placement_option_id.shipment_ids else ''
        if not shipment_ids:
            raise UserError(_("Selected placement option shipment id not found in the ERP "
                              "for this shipment plan."))
        if len(shipment_ids) > 1:
            res.update({'shipment_ids': shipment_ids, 'is_multiple_shipment': True,
                        'shipment_count': placement_option_id.shipment_count})
        result = []
        list_of_shipment_ids = []
        for transportation_option_data in transportation_options:
            for transportation_option in transportation_option_data:
                search_domain = []
                amz_transportation = amz_transportation_option_obj
                carrier_name = transportation_option.get('carrier', {}).get('name', '')
                carrier_code = transportation_option.get('carrier', {}).get('alphaCode', '')
                preconditions = transportation_option.get('preconditions', []) and transportation_option.get(
                    'preconditions', []) or ''
                shipment_id = transportation_option.get('shipmentId', '')
                shipping_mode = transportation_option.get('shippingMode', '')
                transportation_option_id = transportation_option.get('transportationOptionId', '')
                shipping_solution = transportation_option.get('shippingSolution', '')
                placement_data = {
                    'carrier_code': carrier_code,
                    'carrier_name': carrier_name,
                    'preconditions': preconditions,
                    'shipment_id': shipment_id,
                    'shipping_mode': shipping_mode,
                    'shipping_solution': shipping_solution,
                    'transportation_option_id': transportation_option_id,
                    'shipment_plan_id': ship_plan_rec.id
                }
                carrier_code and search_domain.append(('carrier_code', '=', carrier_code))
                carrier_name and search_domain.append(('carrier_name', '=', carrier_name))
                shipment_id and search_domain.append(('shipment_id', '=', shipment_id))
                shipping_mode and search_domain.append(('shipping_mode', '=', shipping_mode))
                shipping_solution and search_domain.append(('shipping_solution', '=', shipping_solution))
                transportation_option_id and search_domain.append(('transportation_option_id', '=', transportation_option_id))
                ship_plan_rec and search_domain.append(('shipment_plan_id', '=', ship_plan_rec.id))
                # if found duplicate record then do not create same record
                if search_domain:
                    amz_transportation = amz_transportation_option_obj.search(search_domain)
                if not amz_transportation:
                    amz_transportation_option_obj.create(placement_data)
                # create uniq shipment id records in the wizard for available transportation
                if shipment_id not in list_of_shipment_ids:
                    result.append((0, 0, {'shipment_id': shipment_id, 'shipment_plan_id': ship_plan_rec.id}))
                    list_of_shipment_ids.append(shipment_id)
        res.update({'inbound_shipment_list_transportation_option_ids': result,
                    'is_confirm_placement_option': ship_plan_rec.is_confirm_placement_option})
        return res

    def confirm_transportation_option_sp_api_v2024(self):
        """
        Define this method for confirm transportation options.
        :return: True
        """
        if not self.inbound_shipment_list_transportation_option_ids.filtered(lambda l: l.amz_transportation_id):
            raise UserError(_("Please select transportation option for the shipment Id."))
        if self.is_multiple_shipment and len(self.inbound_shipment_list_transportation_option_ids.filtered(
                lambda l: l.amz_transportation_id)) != self.shipment_count:
            raise UserError(_("You need to select transportation option for all the shipment Id."))
        selected_transportation_options = self.inbound_shipment_list_transportation_option_ids.filtered(
            lambda l: l.amz_transportation_id)
        if not selected_transportation_options[0].shipment_plan_id.is_confirm_delivery_window_options:
            raise UserError(_("Please confirm delivery window option first for this shipment plan."))
        confirm_transportation_datas = []
        for transportation_option in selected_transportation_options:
            confirm_transportation_data = self.prepare_data_for_confirm_transportation_options_sp_api_v2024(
                transportation_option)
            confirm_transportation_datas.append(confirm_transportation_data)
        if not confirm_transportation_datas:
            return True
        kwargs = selected_transportation_options[
            0].shipment_plan_id.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            selected_transportation_options[0].shipment_plan_id.instance_id,
            'confirm_transportation_options_sp_api_v2024')
        kwargs.update({'body': {'confirmTransportationOptions':{"transportationSelections": confirm_transportation_datas}}})
        kwargs.update({'inboundPlanId': selected_transportation_options[0].shipment_plan_id.inbound_plan_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        selected_transportation_options[0].shipment_plan_id.amz_create_inbound_shipment_check_status(
            selected_transportation_options[0].shipment_plan_id.id, 'confirm_transportation_options',
            response.get('result', {}).get('operationId', ''))
        if not selected_transportation_options[0].shipment_plan_id.is_confirm_transportation_options:
            selected_transportation_options[0].shipment_plan_id.write({'is_confirm_transportation_options': True})
        for selected_transportation_option in selected_transportation_options:
            self.amz_create_selected_transportation_option_record(selected_transportation_option)
        selected_transportation_options[0].shipment_plan_id.write({'state': 'plan_approved'})
        self.amz_delete_available_transportation_options(
            selected_transportation_options[0].shipment_plan_id.id,
            selected_transportation_options.mapped('shipment_id'))
        return True

    def amz_create_selected_transportation_option_record(self, transportation_option):
        """
        Define this method for create selected transportation option record.
        :param: transportation_option: inbound.shipment.list.transportation.option.ept()
        :return
        """
        self.env['amz.selected.placement.option.ept'].create({
            'carrier_code': transportation_option.amz_transportation_id.carrier_code,
            'carrier_name': transportation_option.amz_transportation_id.carrier_name,
            'preconditions': transportation_option.amz_transportation_id.preconditions,
            'shipping_mode': transportation_option.amz_transportation_id.shipping_mode,
            'shipping_solution': transportation_option.amz_transportation_id.shipping_solution,
            'transportation_option_id': transportation_option.amz_transportation_id.transportation_option_id,
            'placement_status': 'transportation_option',
            'shipment_ids': transportation_option.shipment_id,
            'shipment_plan_id': transportation_option.shipment_plan_id.id
        })

    @staticmethod
    def prepare_data_for_confirm_transportation_options_sp_api_v2024(transportation_option):
        """
        Define this method for prepare confirm transportation options data.
        :param: transportation_option: inbound.shipment.list.transportation.option.ept()
        :return: dict {}
        """
        return {
            'shipmentId': transportation_option.shipment_id,
            'transportationOptionId': transportation_option.amz_transportation_id.transportation_option_id
        }

    def amz_delete_available_transportation_options(self, shipment_plan_id, shipment_ids):
        """
        Define this method for delete available transportation options from the database,
        once user selected and confirm the transportation option for the shipment plan.
        Purpose: Because we do not required this listed data after confirmation of the transportation option.
        :param: shipment_plan_id: inbound.shipment.plan.new.ept()
        :param: shipment_ids: list of shipment ids
        :return: True
        """
        self.env['amz.transportation.option.ept'].search([('shipment_plan_id', '=', shipment_plan_id),
                                                          ('shipment_id', 'in', shipment_ids)]).unlink()
        return True


class ListTransportationOptions(models.TransientModel):
    _name = 'inbound.shipment.list.transportation.option.ept'
    _description = 'Inbound Shipment List Transportation Option'

    shipment_id = fields.Char(string="Shipment Id", help="Shipment id")
    shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Inbound Plan',
                                       help="Inbound shipment plan id")
    inbound_shipment_transportation_details_wizard_id = fields.Many2one("inbound.shipment.transportation.option.details",
                                                                        string="Inbound Shipment Transportation Details Wizard")
    amz_transportation_id = fields.Many2one('amz.transportation.option.ept', string='Transportation Option',
                                            help="Inbound shipment plan id")
