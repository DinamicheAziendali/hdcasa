import json
import ast
from odoo import fields, models, api, _
from odoo.addons.iap.tools import iap_tools
from odoo.exceptions import UserError
from ..endpoint import DEFAULT_ENDPOINT


class InboundShipmentPlanNewEpt(models.Model):
    _name = 'inbound.shipment.plan.new.ept'
    _description = 'Inbound Shipment Plan New Ept'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(size=120, readonly=True, required=False, index=True)
    state = fields.Selection([('draft', 'Draft'), ('inprogress', 'In Progress'),
                              ('generate_placement', 'Generate Placement'),
                              ('plan_approved', 'Shipment Plan Approved'),
                              ('list_placement', 'List Placement'),
                              ('get_shipment', 'Get Shipment'),
                              ('generate_packing_option', 'Generate Packing Option'),
                              ('confirm_packing_option', 'Confirm Packing Option'),
                              ('set_packing_information', 'Set Packing Information'),
                              ('generate_transportation', 'Generate Transportation'),
                              ('generate_delivery_window', 'Generate Delivery Window'),
                              ('confirm_transportation_option', 'Confirm Transportation Option'),
                              ('confirm_delivery_window_option', 'Confirm Delivery Window Option'),
                              ('cancel', 'Cancel')], default='draft', string='State')
    instance_id = fields.Many2one('amazon.instance.ept', string='Marketplace', required=True)
    warehouse_id = fields.Many2one("stock.warehouse", string='Warehouse')
    company_id = fields.Many2one('res.company', string='Company', compute="_compute_company", store=True)
    source_address_id = fields.Many2one("res.partner", string='Source Address', required=True)
    plan_name = fields.Char(string='Plane Name')
    label_owner = fields.Selection([('AMAZON', 'AMAZON'), ('SELLER', 'SELLER'), ('NONE', 'NONE')], default='NONE',
                                   string='Label Owner')
    prep_owner = fields.Selection([('AMAZON', 'AMAZON'), ('SELLER', 'SELLER'), ('NONE', 'NONE')], default='NONE',
                                  string='Prep Owner')
    new_shipment_line_ids = fields.One2many('inbound.shipment.plan.line.new', 'shipment_new_plan_id',
                                            string='Inbound Shipment Plan Items', readonly=True,
                                            help="SKU and quantity information for the items in an inbound shipment.")
    log_ids = fields.One2many('common.log.lines.ept', compute='_compute_error_logs')
    plan_operation_id = fields.Char(string='Plan Operation')
    inbound_plan_id = fields.Char(string='Inbound Plan')
    package_operation_id = fields.Char(string='Package Operation')
    packing_option_id = fields.Char('Packing Option Id')
    packing_group_id = fields.Char('Packing Group Id')
    shipment_id = fields.Char('Shipment Id')
    package_group_details_ids = fields.One2many('stock.quant.package', 'inbound_shipment_plan_id',
                                                string='Package Groupings Detail')
    is_box_information_known = fields.Boolean(string="Box Information Known?", default=False)
    plan_current_status = fields.Selection([('generate_placement', 'Generate Placement'),
                                            ('list_placement', 'List Placement'), ('get_shipment', 'Get Shipment')],
                                           string='Plan Status')
    odoo_shipment_ids = fields.One2many('inbound.shipment.new.ept', 'shipment_plan_id',
                                        string='Amazon Shipments')
    count_odoo_shipment = fields.Integer('Count Odoo Shipment', compute='_compute_odoo_shipment')
    ready_to_ship_window_date = fields.Datetime(string="Ready To Ship Date", help="Shipment ready to ship date.")
    is_generated_transportation_options = fields.Boolean(string="Is Generated Transportation Options?", default=False)
    is_generated_delivery_window_options = fields.Boolean(string="Is Generated Delivery Window Options?", default=False)
    is_confirm_transportation_options = fields.Boolean(string="Is Confirm Transportation Options?", default=False)
    is_confirm_delivery_window_options = fields.Boolean(string="Is Confirm Delivery Window Options?", default=False)
    is_imported_shipment = fields.Boolean(string="Is Imported Shipment?", default=False)
    is_confirm_placement_option = fields.Boolean(string="Confirm Placement Option", default=False)
    shipment_tracking_type = fields.Selection([('ltl', 'Less-Than-Truckload (LTL)'),
                                               ('spd', 'Small Parcel Delivery (SPD)')],
                                              string='Shipment Type')
    is_ltl_shipment = fields.Boolean(string="Is LTL Shipment?", default=False)
    is_ltl_get_shipment_info = fields.Boolean(string="Is LTL Get Shipment Info?", default=False)

    def _compute_odoo_shipment(self):
        """
        This method is used to compute total numbers of inbound shipments
        :return: N/A
        """
        for rec in self:
            rec.count_odoo_shipment = len(rec.odoo_shipment_ids.ids)

    def _compute_error_logs(self):
        """
        This method will compute total logs crated from the current record.
        :return:
        """
        log_line_obj = self.env['common.log.lines.ept']
        log_lines = log_line_obj.amz_find_mismatch_details_log_lines(self.id, 'inbound.shipment.plan.new.ept')
        self.log_ids = log_lines.ids if log_lines else False

    @api.depends('instance_id')
    def _compute_company(self):
        """
        Find Company id on change of instance
        """
        for record in self:
            company_id = record.instance_id.company_id.id if record.instance_id else False
            if not company_id:
                company_id = self.env.company.id
            record.company_id = company_id

    def action_view_inbound_shipment(self):
        """
        This method creates and return an action for opening the view of amazon inbound shipment
        :return: action
        """
        action = {
            'name': 'Inbound Shipment',
            'res_model': 'inbound.shipment.new.ept',
            'type': 'ir.actions.act_window'
        }
        if self.count_odoo_shipment != 1:
            action.update({'domain': [('id', 'in', self.odoo_shipment_ids.ids)],
                           'view_mode': 'list,form'})
        else:
            action.update({'res_id': self.odoo_shipment_ids.id,
                           'view_mode': 'form'})
        return action

    @api.onchange('instance_id')
    def onchange_instance_id(self):
        """Set the warehouse as per instance warehouse"""
        if self.instance_id:
            self.warehouse_id = self.instance_id.fba_warehouse_id.id
            self.is_box_information_known = self.instance_id.is_inbound_box_information_known

    def import_product_for_inbound_new_shipment(self):
        """
        Open wizard to import product through xlsx file.
        :return: import.product.inbound.shipment()
        """
        import_obj = self.env['import.product.inbound.shipment'].create({'new_shipment_plan_id': self.id})
        ctx = self.env.context.copy()
        ctx.update({'new_shipment_plan_id': self.id, 'update_existing': False, 'new_inbound_plan': True})
        return import_obj.with_context(ctx).wizard_view()

    def prepare_kwargs_for_create_inbound_plan_sp_api_v2024(self, instance, emipro_api):
        """
        Prepare General Arguments for Prepare General Amazon Request dictionary.
        :param instance: amazon.instance.ept()
        :param: emipro_api: str
        :return: kwargs {}
        """
        account = self.env['iap.account'].search([('service_name', '=', 'amazon_ept')])
        dbuuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        amz_marketplace_code = instance.seller_id.country_id.amazon_marketplace_code or instance.seller_id.country_id.code
        if amz_marketplace_code.upper() == 'GB':
            amz_marketplace_code = 'UK'
        return {'merchant_id': instance.merchant_id and str(instance.merchant_id) or False,
                  'emipro_api': emipro_api,
                  'app_name': 'amazon_ept_spapi', 'account_token': account.account_token,
                  'dbuuid': dbuuid, 'marketplace_id': instance.market_place_id,
                'amazon_marketplace_code': amz_marketplace_code}

    def prepare_plan_create_data_sp_api_v2024(self):
        """
        This method is used to prepare the data for the Create an Inbound Plan.
        """
        if not self.new_shipment_line_ids:
            raise UserError(_('Warning : Add the Products in the Shipment lines Before Creating the Inbound Plan.'))
        items = []
        for item in self.new_shipment_line_ids:
            items.append({**({'expiration': item.expiration.strftime('%Y-%m-%d')} if item.expiration else {}), 'labelOwner': item.label_owner,
                          **({'manufacturingLotCode': item.manufacturing_lot_code} if item.manufacturing_lot_code else {}),
                          'msku': item.seller_sku, 'prepOwner': item.prep_owner, 'quantity': item.quantity})
        address = self.source_address_id
        if not all([address.street, address.city, address.country_code, address.name, address.phone, address.zip]):
            partner_address = {'street': address.street, 'city': address.city, 'country': address.country_code,
                               'name': address.name, 'phone': address.phone,'zip': address.zip}
            missing_fields = [field for field, value in partner_address.items() if not value]
            message = ', '.join(missing_fields)
            raise UserError(_('Warning: Please set the details for {} in the source address.'.format(message)))
        source_address = {'addressLine1': address.street if address.street else '',
                          'city': address.city if address.city else '',
                          'countryCode': address.country_code if address.country_code else '',
                          'name': address.name if address.name else '',
                          'phoneNumber': address.phone if address.phone else '',
                          'postalCode': address.zip if address.zip else '',
                          'stateOrProvinceCode': address.state_id.code if address.state_id.code else ''
                          }
        plan_createdata = {'destinationMarketplaces': [self.instance_id.market_place_id], 'items': items,
                           'name': self.name, 'sourceAddress': source_address}
        return plan_createdata

    def create_inbound_plan_sp_api_v2024(self):
        """
        This method Create an Inbound Plan and request to api for Creating the Inbound Plan in Amazon.
        :return: boolean()
        """
        plan_create_data = self.prepare_plan_create_data_sp_api_v2024()
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'create_inbound_plan_sp_api_v2024')
        create_inbound_plan = {"createInboundPlan": plan_create_data}
        kwargs.update({'body': create_inbound_plan})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        self.amz_create_inbound_shipment_check_status(self.id, 'create_shipment_plan',
                                                      response.get('result', {}).get('operationId', ''))
        self.write({'state': 'inprogress', 'inbound_plan_id': response.get('result', {}).get('inboundPlanId', '')})
        return True

    def check_operation_status_sp_api_v2024(self):
        """
        This method open the new wizard for performing the check status operation for the already performed operation.
        :return : inbound.shipment.plan.new.check.status.ept()
        """
        import_obj = self.env['inbound.shipment.plan.new.check.status.ept']
        ctx = self.env.context.copy()
        ctx.update({'inbound_shipment_plan_id': self.id})
        return import_obj.with_context(ctx).amz_check_status_wizard_view()

    def generate_packing_options_sp_api_v2024(self):
        """
        Generate the packing options for inbound shipment plan.
        :return: True
        """
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id,'generate_packing_operations_plan_sp_api_v2024')
        if self.inbound_plan_id:
            kwargs.update({'inboundPlanId': self.inbound_plan_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        self.amz_create_inbound_shipment_check_status(self.id, 'generate_packing_options',
                                                      response.get('result', {}).get('operationId', ''))
        self.write({'state': 'generate_packing_option'})
        return True

    def list_packing_information_sp_api_v2024(self):
        """
        List the Packing Option Operation and Each PackingOption includes a set of PackingGroups, each of which
        contains a set of SKUs that can be packed together.
        :return : True
        """
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'list_packing_option_sp_api_v2024')
        if self.inbound_plan_id:
            kwargs.update({'inboundPlanId': self.inbound_plan_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        packing_options = []
        result = response.get('result', {})
        if not isinstance(result.get('packingOptions', []), list):
            packing_options.append(result.get('packingOptions', {}))
        else:
            packing_options = result.get('packingOptions', [])
        return self.display_list_packing_options(packing_options)

    def prepare_data_for_set_packing_sp_api_v2024(self):
        """
        Method prepare the packing information data for Inbound plan based on
        package available in carton content information.
        return : {}
        """
        self.amz_check_package_required_details()
        self.amz_check_package_qty()
        dimension_units = {'centimeters': 'CM', 'inches': 'IN'}
        weight_units = {'pounds': 'LB', 'kilograms': 'KG'}
        packaging_information_data = []
        for package in self.package_group_details_ids:
            items = []
            for info in package.carton_info_ids:
                line = self.new_shipment_line_ids.search([('amazon_product_id', '=', info.amazon_product_id.id),
                                                          ('shipment_new_plan_id', '=', self.id)], limit=1)
                manufact_lot_code = line.manufacturing_lot_code
                if package.box_content_information_source == 'BOX_CONTENT_PROVIDED':
                    items.append({'msku': info.seller_sku, 'quantity': info.quantity,
                                  **({'expiration': line.expiration.strftime('%Y-%m-%d')} if line.expiration else {}),
                                  'prepOwner': line.prep_owner, 'labelOwner': line.label_owner,
                                  **({'manufacturingLotCode': manufact_lot_code} if manufact_lot_code else {})
                                  })
            dimensions = {'unitOfMeasurement': dimension_units.get(package.ul_id.dimension_unit),
                          'length': str(package.ul_id.length), 'width': str(package.ul_id.width),
                          'height': str(package.ul_id.height)}
            box_data = {'weight': {'unit': weight_units.get(package.weight_unit), 'value': str(package.weight_value)},
                        'dimensions': dimensions, 'quantity': str(1), 'items': items,
                        'contentInformationSource': package.box_content_information_source}
            is_updated_data = False
            for packaging_information in packaging_information_data:
                if packaging_information.get('packingGroupId', '') == package.amz_carton_info_id.packing_group_id:
                    packaging_information.get('boxes', []).append(box_data)
                    is_updated_data = True
                    break
            if not is_updated_data:
                packaging_information_data.append({'packingGroupId': package.amz_carton_info_id.packing_group_id,
                                                   'boxes': [box_data]})
        return packaging_information_data

    def amz_check_package_qty(self):
        """
        Define this method for check package quantity and shipment plan quantity and
        found mismatch then raise warning.
        :return: True
        """
        amz_product_obj = self.env['amazon.product.ept']
        prd_wise_plan_qty = {}
        prd_wise_package_qty = {}
        for line in self.new_shipment_line_ids:
            if line.amazon_product_id.id in prd_wise_plan_qty:
                qty = prd_wise_plan_qty.get(line.amazon_product_id.id, {})  + line.quantity
                prd_wise_plan_qty.update({line.amazon_product_id.id: qty})
            else:
                prd_wise_plan_qty.update({line.amazon_product_id.id: line.quantity})
        for package in self.package_group_details_ids:
            for info in package.carton_info_ids:
                if info.amazon_product_id.id in prd_wise_package_qty:
                    qty = prd_wise_package_qty.get(info.amazon_product_id.id, {}) + info.quantity
                    prd_wise_package_qty.update({info.amazon_product_id.id: qty})
                else:
                    prd_wise_package_qty.update({info.amazon_product_id.id: info.quantity})
        mismatched_quantity_products = [
            line_prd for line_prd, line_qty in prd_wise_plan_qty.items()
            if line_prd not in prd_wise_package_qty or prd_wise_package_qty[line_prd] != line_qty
        ]
        if mismatched_quantity_products:
            raise UserError(_("There is mismatch in the shipment plan quantity and package quantity "
                              "in the following products: %s" % (amz_product_obj.browse(
                mismatched_quantity_products).mapped('seller_sku'))))
        return True

    def amz_check_package_required_details(self):
        """
        Define this method for check the package required details.
        :return:
        """
        if not self.package_group_details_ids:
            raise UserError(_('Add the Products in the Package Group and Details '
                              'Before Set Packing Information.'))
        for package in self.package_group_details_ids:
            if not package.ul_id:
                raise UserError(_('Please select package in the product package group details.'))
            elif not package.box_content_information_source:
                raise UserError(_('Please select content information source in the product package group details.'))
            elif not package.weight_unit or not package.weight_value:
                raise UserError(_('Either package weight or unit is not set in the product package group '
                                  'details, please select it.'))
            elif not all([package.ul_id.dimension_unit, package.ul_id.length, package.ul_id.width, package.ul_id.height]):
                raise UserError(_('Package dimension not found for any one of them Dimension Unit, '
                                  'Length, Width, and Height in the product package group.'))


    def set_packing_information_sp_api_v2024(self):
        """
        Set the packing information related to the items that are packed into each box,
        including items, item quantities, dimensions, weight, and quantity of boxes and request it.
        """
        packaging_information_data = self.prepare_data_for_set_packing_sp_api_v2024()
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'set_packing_option_plan_sp_api_v2024')
        if self.inbound_plan_id and packaging_information_data:
            kwargs.update({'inboundPlanId': self.inbound_plan_id, 'packageGroupings': packaging_information_data})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        self.amz_create_inbound_shipment_check_status(
            self.id, 'set_packing_information', response.get('result', {}).get('operationId', ''))
        self.write({'state': 'set_packing_information'})
        return True

    def amz_prepare_generate_placement_options_request_data(self):
        """
        Method prepare generate placement option request data from shipment line items.
        return : {}
        """
        items = []
        for item in self.new_shipment_line_ids:
            items.append({**({'expiration': item.expiration.strftime('%Y-%m-%d')} if item.expiration else {}),
                          **({'manufacturingLotCode': item.manufacturing_lot_code} if item.manufacturing_lot_code else {}),
                          'msku': item.seller_sku, 'prepOwner': item.prep_owner, 'quantity': item.quantity})
        body = {'customPlacement': [{'items': items, 'warehouseId': self.warehouse_id.code}]}
        return body

    def generate_placement_options_sp_api_v2024(self):
        """
        This method Generate placement options (shipment splits) that are available for the inbound plan and
        describes the destination FCs and shipping options for each item in the inbound plan.
        :return:
        """
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'generate_placement_options_sp_api_v2024')
        request_body = self.amz_prepare_generate_placement_options_request_data()
        if self.inbound_plan_id and request_body:
            kwargs.update({'inboundPlanId': self.inbound_plan_id, 'body': request_body})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        self.amz_create_inbound_shipment_check_status(self.id, 'generate_placement_options',
                                                      response.get('result', {}).get('operationId', ''))
        self.write({'state': 'generate_placement'})
        return True

    def list_placement_options_sp_api_v2024(self):
        """
        This method Provides a list of all placement options for an inbound plan, which includes a placement option ID,
        status (offered or accepted),any fees and discounts, the expiration date, and the shipment IDs
        associated with each option.
        """
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'list_placement_options_sp_api_v2024')
        if self.inbound_plan_id:
            kwargs.update({'inboundPlanId': self.inbound_plan_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        shipments = []
        result = response.get('result', {})
        if not isinstance(result.get('placementOptions', []), list):
            shipments.append(result.get('placementOptions', {}))
        else:
            shipments = result.get('placementOptions', [])
        return self.display_list_placement_options(shipments)

    def generate_transportation_option_sp_api_v2024(self):
        """
        Define this method for generate transportation options for the shipment plan.
        :return: True
        """
        if self.is_ltl_shipment and not self.is_imported_shipment:
            raise UserError(_("Warning: This is an LTL shipment. First get the shipment details from "
                              "GET SHIPMENT Button, and then proceed to generate the transportation options."))
        if self.is_ltl_shipment and self.is_imported_shipment:
            shipment_ids = self.amz_get_placement_shipment_id()
            new_shipments = self.env['inbound.shipment.new.ept'].search([('shipment_id', 'in', shipment_ids)])
            for ship_id in new_shipments:
                req_data = [ship_id.partnered_ltl_id.id, ship_id.seller_freight_class,
                            ship_id.seller_declared_value, ship_id.declared_value_currency_id.id]
                req_data.extend(ship_id.new_partnered_ltl_ids.ids or [])
                if not all(req_data):
                    raise UserError(_(f"Warning: LTL Shipment requires transport information. Please set the required "
                                      f"Transport information in the {ship_id.name} Shipment of this Inbound Plan."))
        if not self.ready_to_ship_window_date:
            raise UserError(_("Please select the ready to ship date for the shipment."))
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'generate_transportation_option_sp_api_v2024')
        generate_transportation_data = self.prepare_data_for_generate_transportation_options_sp_api_v2024()
        kwargs.update({'body': {"generateTransportationOptions": [generate_transportation_data]},
                           'inboundPlanId': self.inbound_plan_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        self.amz_create_inbound_shipment_check_status(self.id, 'generate_transportation_options',
                                                      response.get('result', {}).get('operationId', ''))
        self.write({'is_generated_transportation_options': True})
        return True

    def prepare_data_for_generate_transportation_options_sp_api_v2024(self):
        """
        Define this method for prepare generate transportation options data.
        :return: dict {}
        """
        new_inbound_shipment_obj = self.env['inbound.shipment.new.ept']
        placement_option_id = self.amz_get_placement_option_id()
        shipment_ids = self.amz_get_placement_shipment_id()
        transportation_data = {'placementOptionId': placement_option_id}
        shipment_data = []
        dimension_unit = {'inches': 'IN', 'centimeters': 'CM'}
        dict_weight_unit = {'pounds': 'LB', 'kilograms': 'KG'}
        for shipment_id in shipment_ids:
            if self.is_ltl_shipment:
                pallets_data = []
                new_shipment_id = new_inbound_shipment_obj.search([('shipment_id', '=', shipment_id)])
                for pallet in new_shipment_id.new_partnered_ltl_ids:
                    unit = dimension_unit.get(pallet.ul_id.dimension_unit, '')
                    pallets_data.append({'weight': {'unit': dict_weight_unit.get(pallet.weight_unit),
                                                    'value': pallet.weight_value},
                                         'dimensions': {'unitOfMeasurement': unit, 'length': pallet.ul_id.length,
                                                        'width': pallet.ul_id.width, 'height': pallet.ul_id.height},
                                         'quantity': 1, 'stackability': pallet.stack_ability})
                shipment_data.append({'shipmentId': new_shipment_id.shipment_id,
                                      'readyToShipWindow': {'start': self.ready_to_ship_window_date.isoformat() + 'Z'},
                                      'contactInformation': {"phoneNumber": new_shipment_id.partnered_ltl_id.phone,
                                                             "email": new_shipment_id.partnered_ltl_id.email or '',
                                                             "name": new_shipment_id.partnered_ltl_id.name},
                                      'pallets': pallets_data,
                                      'freightInformation': {
                                          'freightClass': str(new_shipment_id.seller_freight_class),
                                          'declaredValue': {
                                              'code': new_shipment_id.declared_value_currency_id.name,
                                              'amount': new_shipment_id.seller_declared_value
                                          }
                                      }
                                      })
            else:
                shipment_data.append({'readyToShipWindow': {'start': self.ready_to_ship_window_date.isoformat() + 'Z'},
                                      'shipmentId': shipment_id})
        transportation_data.update({'shipmentTransportationConfigurations': shipment_data})
        return transportation_data

    def list_transportation_options_sp_api_v2024(self):
        """
        Define this method for list transportation options for the shipment plan.
        :return: True
        """
        list_transportation_options = []
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'list_transportation_options_sp_api_v2024')
        kwargs.update({'inboundPlanId': self.inbound_plan_id})
        shipment_ids = self.amz_get_placement_shipment_id()
        for shipment_id in shipment_ids:
            kwargs.update({'shipmentId': shipment_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            result = response.get('result', {})
            list_transportation_options.append(result.get('transportationOptions', {}))
        return self.display_list_transportation_options(list_transportation_options)

    def amz_get_placement_option_id(self):
        """
        Define this method for get placement option id form the selected placement option by the
        seller when listed the placement options.
        :return: placement option id - str
        """
        selected_placement_option = self.amz_get_selected_placement_option_ept(self.id)
        placement_option_id = selected_placement_option.placement_option_id if selected_placement_option.placement_option_id else False
        if not placement_option_id:
            raise UserError(_("Placement option not found in the ERP for generate transportation "
                              "options for this shipment plan."))
        return placement_option_id

    def generate_delivery_window_option_sp_api_v2024(self):
        """
        Define this method for generate delivery window options.
        :return: True
        """
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'generate_delivery_window_option_sp_api_v2024')
        shipment_ids = self.amz_get_placement_shipment_id()
        kwargs.update({'inboundPlanId': self.inbound_plan_id})
        for shipment_id in shipment_ids:
            kwargs.update({'shipmentId': shipment_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            self.amz_create_inbound_shipment_check_status(self.id, 'generate_delivery_window_options',
                                                          response.get('result', {}).get('operationId', ''))
        self.write({'is_generated_delivery_window_options': True})
        return True

    def amz_get_placement_shipment_id(self):
        """
        Define this method for get selected placement option ids for the shipment plan.
        :return: list of shipment ids
        """
        selected_placement_option = self.amz_get_selected_placement_option_ept(self.id)
        shipment_ids = selected_placement_option.shipment_ids if selected_placement_option.shipment_ids else ''
        if not shipment_ids:
            raise UserError(_("Selected placement option shipment id not found in the ERP for "
                              "generate transportation options for this shipment plan."))
        return ast.literal_eval(shipment_ids)

    def amz_get_selected_placement_option_ept(self, plan_id):
        """
        Define this method for get seller selected placement option for shipment plan.
        :param: plan_id: inbound.shipment.plan.new.ept() - id
        :return: amz.selected.placement.option.ept()
        """
        amz_selected_placement_opt_obj = self.env['amz.selected.placement.option.ept']
        return amz_selected_placement_opt_obj.search([
            ('shipment_plan_id', '=', plan_id), ('placement_status', '=', 'shipment_placement_option')], limit=1)

    def list_delivery_window_options_sp_api_v2024(self):
        """
        Define this method for list delivery window options for the shipment plan.
        :return: response
        """
        list_delivery_window_options = {}
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'list_delivery_window_options_sp_api_v2024')
        shipment_ids = self.amz_get_placement_shipment_id()
        kwargs.update({'inboundPlanId': self.inbound_plan_id})
        for shipment_id in shipment_ids:
            kwargs.update({'shipmentId': shipment_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            result = response.get('result', {})
            list_delivery_window_options.update({shipment_id: result.get('deliveryWindowOptions', [])})
        if not list_delivery_window_options:
            return True
        return self.display_list_delivery_window_options(list_delivery_window_options)


    def display_list_placement_options(self, shipments):
        """
        Define this method for display placement options.
        :param: shipments: []
        :return: ir.actions.act_window()
        """
        view = self.env.ref('amazon_ept.view_inbound_shipment_placement_options_details')
        context = dict(self._context)
        context.update({'shipments': shipments, 'shipment_plan_id': self.id})
        return {
            'name': _('Placement Options'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'inbound.shipment.placement.option.details',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': context
        }

    def display_list_packing_options(self, packing_options):
        """
        Define this method for display placement options.
        :param: shipments: []
        :return: ir.actions.act_window()
        """
        view = self.env.ref('amazon_ept.view_inbound_shipment_packing_options_details')
        context = dict(self._context)
        context.update({'packing_options': packing_options, 'shipment_plan_id': self.id})
        return {
            'name': _('Placement Options'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'inbound.shipment.packing.option.details',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': context
        }

    def display_list_transportation_options(self, list_transportation_options):
        """
        Define this method for display transportation options.
        :param: list_transportation_options: []
        :return: ir.actions.act_window()
        """
        view = self.env.ref('amazon_ept.view_inbound_shipment_transportation_options_details')
        context = dict(self._context)
        context.update({'transportation_options': list_transportation_options, 'shipment_plan_id': self.id})
        return {
            'name': _('Transportation Options'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'inbound.shipment.transportation.option.details',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': context
        }

    def display_list_delivery_window_options(self, list_delivery_window_options):
        """
        Define this method for display delivery window options.
        :param: list_delivery_window_options: []
        :return: ir.actions.act_window()
        """
        view = self.env.ref('amazon_ept.view_inbound_shipment_delivery_window_options_details')
        context = dict(self._context)
        context.update({'delivery_window_options': list_delivery_window_options, 'shipment_plan_id': self.id})
        return {
            'name': _('Delivery Window Options'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'inbound.shipment.delivery.window.option.details',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': context
        }

    def amz_create_inbound_shipment_check_status(self, inbound_plan_id, operation_type, operation_id):
        """
        This method creates record for the inbound shipment check status for operation.
        :param: inbound_plan_id: inbound.shipment.plan.new.ept()
        :param: operation_type: inbound plan operation char()
        :param: operation_id: char()
        :return: boolean
        """
        inbound_check_status_obj = self.env['new.inbound.shipment.plan.check.status.ept']
        vals = {'inbound_shipment_plan_id': inbound_plan_id, 'operation': operation_type, 'operation_id': operation_id}
        if not inbound_check_status_obj.search([('operation', '=', operation_type),
                                                ('inbound_shipment_plan_id', '=', inbound_plan_id),
                                                ('operation_id', '=', operation_id)]):
            inbound_check_status_obj.create(vals)
        return True

    def get_shipment_sp_api_v2024(self):
        """
        Define this method to get and create inbound shipment in the odoo.
        :return:
        """
        shipment_response = []
        inbound_shipment_obj = self.env['inbound.shipment.new.ept']
        inbound_shipment_placement_option_obj = self.env['inbound.shipment.placement.option.details']
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'get_shipment_data_sp_api_v2024')
        kwargs.update({'inboundPlanId': self.inbound_plan_id})
        shipment_ids = self.amz_get_placement_shipment_id()
        for shipment_id in shipment_ids:
            kwargs.update({'shipmentId': shipment_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            shipment_response.append(response.get('result', {}))
        for shipment_data in shipment_response:
            shipment_vals = inbound_shipment_placement_option_obj.amz_prepare_shipment_values(shipment_data, self)
            odoo_shipment = inbound_shipment_obj.create(shipment_vals)
            inbound_shipment_placement_option_obj.amz_get_and_create_shipment_lines(odoo_shipment)
        self.write({'is_imported_shipment': True, 'is_ltl_get_shipment_info': False})
        return True

    def view_packing_details_sp_api_v2024(self):
        """
        Define this method for view packing group details.
        :return:
        """
        amz_carton_content_info_obj = self.env['amazon.carton.content.info.ept']
        amz_selected_placement_opt_obj = self.env['amz.selected.placement.option.ept']
        packing_placement_option =  amz_selected_placement_opt_obj.search([
            ('shipment_plan_id', '=', self.id), ('placement_status', '=', 'packing_placement_option')], limit=1)
        if not packing_placement_option:
            raise UserError(_("Please confirm the packing placement option."))
        packing_group_ids = ast.literal_eval(packing_placement_option.packing_group_ids)
        records = amz_carton_content_info_obj.search([('packing_group_id', 'in', packing_group_ids),
                                                      ('inbound_shipment_plan_id', '=', self.id)])
        context = self._context.copy() or {}
        context.update({'search_default_amz_packing_group_id': 1})
        action = {
            'domain': "[('id', 'in', " + str(records.ids) + " )]",
            'name': 'Content Information',
            'view_mode': 'list,form',
            'res_model': 'amazon.carton.content.info.ept',
            'type': 'ir.actions.act_window',
            'context': context,
        }
        return action

    def confirmation_placement_option_sp_api_v2024(self):
        """
        Define this method for confirm placement option in the Amazon.
        :return: True
        """
        inbound_shipment_new_obj = self.env['inbound.shipment.new.ept']
        odoo_shipments = inbound_shipment_new_obj.search([('shipment_plan_id', '=', self.id),
                                                          ('instance_id_ept', '=', self.instance_id.id)])
        if not odoo_shipments:
            raise UserError(_("Not found any imported shipments in the odoo for this shipment plan."))
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'confirmation_placement_option_sp_api_v2024')
        kwargs.update({'inboundPlanId': self.inbound_plan_id,
                       'placementOptionId': odoo_shipments[0].placement_option_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        self.amz_create_inbound_shipment_check_status(self.id, 'confirm_placement_options',
                                                      response.get('result', {}).get('operationId', ''))
        self.write({'is_confirm_placement_option': True})
        self.amz_get_shipment_for_shipment_confirmation_id(odoo_shipments)
        return True

    def amz_get_shipment_for_shipment_confirmation_id(self, odoo_shipments):
        """
        Define this method for get shipment details to update shipment confirmation id in the
        shipment to perform check status process.
        :return:
        """
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'get_shipment_data_sp_api_v2024')
        kwargs.update({'inboundPlanId': self.inbound_plan_id})
        for odoo_shipment in odoo_shipments:
            kwargs.update({'shipmentId': odoo_shipment.shipment_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            shipment_confirm_id = response.get('result', {}).get('shipmentConfirmationId', '')
            if not shipment_confirm_id:
                raise UserError(_("Shipment was not confirmed in the Amazon seller central, "
                                  "please check the shipment status in the seller central."))
            odoo_shipment.write({'shipment_confirmation_id': shipment_confirm_id})
            odoo_shipment.create_procurements()
        return True

    def cancel_shipment_plan_sp_api_v2024(self):
        """
        Define this method for cancel shipment plan in the Amazon.
        :return:
        """
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'cancel_inbound_plan_sp_api_v2024')
        kwargs.update({'inboundPlanId': self.inbound_plan_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        self.amz_create_inbound_shipment_check_status(self.id, 'cancel_shipment_plan',
                                                      response.get('result', {}).get('operationId', ''))
        self.write({'state': 'cancel'})
        return True

    def amz_get_shipment_link_confirmation_id(self):
        """
        Define this method for get shipment details to update shipment confirmation id in the
        shipments from inbound shipment plan.
        :return: True
        """
        inbound_shipment_new_obj = self.env['inbound.shipment.new.ept']
        odoo_shipments = inbound_shipment_new_obj.search([('shipment_plan_id', '=', self.id),
                                                          ('instance_id_ept', '=', self.instance_id.id)])
        if not odoo_shipments:
            raise UserError(_("Not found any imported shipments in the odoo for this shipment plan."))
        kwargs = self.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id, 'get_shipment_data_sp_api_v2024')
        kwargs.update({'inboundPlanId': self.inbound_plan_id})
        for odoo_shipment in odoo_shipments:
            kwargs.update({'shipmentId': odoo_shipment.shipment_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            shipment_confirm_id = response.get('result', {}).get('shipmentConfirmationId', '')
            if not shipment_confirm_id:
                raise UserError(_("Shipment was not confirmed in the Amazon seller central, "
                                  "please check the shipment status in the seller central."))
            odoo_shipment.write({'shipment_confirmation_id': shipment_confirm_id,
                                 'state': response.get('result', {}).get('status', '')})
            odoo_shipment.create_procurements()
        if all(odoo_shipments.mapped('shipment_confirmation_id')):
            self.write({'is_confirm_placement_option': True})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        """
        The below method sets name of a particular record as per the sequence.
        :param: vals_list: list of values []
        :return: inbound.shipment.plan.new.ept()
        """
        for vals in vals_list:
            sequence = self.env.ref('amazon_ept.seq_inbound_shipment_plan_new', raise_if_not_found=False)
            name = sequence.next_by_id() if sequence else '/'
            vals.update({'name': name})
        return super(InboundShipmentPlanNewEpt, self).create(vals_list)
