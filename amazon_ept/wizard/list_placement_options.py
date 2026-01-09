from odoo import fields, models, _, api
from odoo.exceptions import UserError
from odoo.addons.iap.tools import iap_tools
from ..endpoint import DEFAULT_ENDPOINT
import ast


class PlacementOptionDetails(models.TransientModel):
    _name = "inbound.shipment.placement.option.details"
    _description = 'Inbound shipment placement details'

    inbound_shipment_list_placement_option_ids = fields.One2many('inbound.shipment.list.placement.option.ept',
                                                                 'inbound_shipment_placement_details_wizard_id',
                                                                 string="Inbound Shipment Placement List")
    is_box_information_known = fields.Boolean(string="Box Information Known?", default=False)


    @api.model
    def default_get(self, fields):
        """
        Define this method for list the placement options.
        :param fields: []
        :return: update result dict {}
        """
        res = super(PlacementOptionDetails, self).default_get(fields)
        shipments = self._context.get('shipments', [])
        shipment_plan_id = self._context.get('shipment_plan_id', False)
        ship_plan_rec = self.env['inbound.shipment.plan.new.ept'].browse(shipment_plan_id)
        result = []
        for shipment in shipments:
            discount_amount = shipment.get('discounts', []) and shipment.get('discounts', [{}])[0].get(
                'value', {}).get('amount', 0.0) or 0.0
            discount_code = shipment.get('discounts', []) and shipment.get('discounts', [{}])[0].get(
                'value', {}).get('code', '') or ''
            fees_amount = shipment.get('fees', []) and shipment.get('fees', [{}])[0].get(
                'value', {}).get('amount', 0.0) or 0.0
            fees_code = shipment.get('fees', []) and shipment.get('fees', [{}])[0].get(
                'value', {}).get('code', '') or ''
            fees_description = shipment.get('fees', []) and shipment.get('fees', [{}])[0].get(
                'description', '') or ''
            placement_option_id = shipment.get('placementOptionId', '')
            shipment_ids = shipment.get('shipmentIds', '')
            status = shipment.get('status', [])
            discount_currency = self.find_currency_ept(discount_code)
            fees_currency = self.find_currency_ept(fees_code)
            placement_data = {
                'shipment_discount': discount_amount,
                'discount_currency_id': discount_currency.id if discount_currency else False,
                'shipment_fees': fees_amount,
                'fees_currency_id': fees_currency.id if fees_currency else False,
                'placement_option_id': placement_option_id,
                'status': status,
                'shipment_ids': shipment_ids,
                'shipment_plan_id': ship_plan_rec.id,
                'placement_description': fees_description
            }
            result.append((0, 0, placement_data))
        res.update({'inbound_shipment_list_placement_option_ids': result,
                    'is_box_information_known': ship_plan_rec.is_box_information_known})
        return res

    def find_currency_ept(self, currency_code):
        """
        Define this method for find the odoo currency.
        :param: currency_code: str
        :return: res.currency()
        """
        currency_obj = self.env['res.currency']
        currency = currency_obj.search([('name', '=', currency_code)], limit=1)
        if not currency and currency_code:
            currency = currency.search([('name', '=', currency_code), ('active', '=', False)], limit=1)
            currency.write({'active': True})
        return currency

    def get_shipment_sp_api_v2024(self):
        """
        Define this method for get shipment based on the selected option by the seller.
        :return:
        """
        inbound_shipment_obj = self.env['inbound.shipment.new.ept']
        inbound_shipment_plan_obj = self.env['inbound.shipment.plan.new.ept']
        shipment_response = []
        odoo_shipment_ids = []
        if not self.inbound_shipment_list_placement_option_ids.filtered(lambda l: l.is_selected_shipment):
            raise UserError(_("Please select the shipment."))
        if len(self.inbound_shipment_list_placement_option_ids.filtered(lambda l: l.is_selected_shipment)) > 1:
            raise UserError(_("You can select only one option from the list."))
        shipment_placement_option = self.inbound_shipment_list_placement_option_ids.filtered(
            lambda l: l.is_selected_shipment)
        kwargs = inbound_shipment_plan_obj.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            shipment_placement_option.shipment_plan_id.instance_id, 'get_shipment_data_sp_api_v2024')
        kwargs.update({'inboundPlanId': shipment_placement_option.shipment_plan_id.inbound_plan_id})
        for shipment_id in ast.literal_eval(shipment_placement_option.shipment_ids):
            kwargs.update({'shipmentId': shipment_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            shipment_response.append(response.get('result', {}))
        for shipment_data in shipment_response:
            shipment_vals = self.amz_prepare_shipment_values(shipment_data, shipment_placement_option.shipment_plan_id)
            odoo_shipment = inbound_shipment_obj.create(shipment_vals)
            self.amz_get_and_create_shipment_lines(odoo_shipment)
            odoo_shipment_ids.append(odoo_shipment.id)
        shipment_placement_option.shipment_plan_id.write({'state': 'list_placement',
                                                          'is_imported_shipment': True,
                                                          'is_ltl_get_shipment_info': False})
        self.amz_create_selected_placement_option_record(shipment_placement_option)
        return True

    def amz_get_and_create_shipment_lines(self, odoo_shipment):
        """
        Define this method for get and create shipment lines.
        :param: odoo_shipment: inbound.shipment.new.ept()
        :return: True
        """
        not_exist_seller_skus = []
        amazon_product_obj = self.env['amazon.product.ept']
        amazon_inbound_shipment_line_obj = self.env['inbound.shipment.line.new.ept']
        inbound_shipment_plan_obj = self.env['inbound.shipment.plan.new.ept']
        kwargs = inbound_shipment_plan_obj.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            odoo_shipment.instance_id_ept, 'list_shipment_items_sp_api_v2024')
        kwargs.update({'inboundPlanId': odoo_shipment.shipment_plan_id.inbound_plan_id,
                       'shipmentId': odoo_shipment.shipment_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        items = response.get('result', {}).get('items', {})
        for item in items:
            seller_sku = item.get('msku', '')
            fn_sku = item.get('fnsku', '')
            quantity = float(item.get('quantity', 0.0))
            asin = item.get('asin', '')
            prep_instruction = item.get('prepInstructions')[0] if item.get('prepInstructions') else []
            prep_owner = prep_instruction and prep_instruction.get('prepOwner', '') or ''
            label_owner = item.get('labelOwner', '')
            amazon_product = amazon_product_obj.search_amazon_product(
                odoo_shipment.instance_id_ept.id, seller_sku, 'FBA')
            if not amazon_product:
                amazon_product = amazon_product_obj.search([
                    ('product_asin', '=', fn_sku),
                    ('instance_id', '=', odoo_shipment.instance_id_ept.id)], limit=1)
            if not amazon_product:
                not_exist_seller_skus.append(seller_sku)
                continue
            amazon_inbound_shipment_line_obj.create({'amazon_product_id': amazon_product.id,
                                                     'seller_sku': seller_sku, 'quantity': quantity,
                                                     'fn_sku': fn_sku, 'asin': asin,
                                                     'label_owner': label_owner, 'prep_owner': prep_owner,
                                                     'shipment_new_id': odoo_shipment.id})
        if not_exist_seller_skus:
            user_message = ("You will be required to map products before proceeding. Please map the following "
                            "Amazon SKUs with Odoo products and try again!\n%s" % not_exist_seller_skus)
            raise UserError(_(user_message))
        return True

    def select_placement_option_sp_api_v2024(self):
        """
        Define this method select and store the shipment placement option.
        :return: True
        """
        if not self.inbound_shipment_list_placement_option_ids.filtered(lambda l: l.is_selected_shipment):
            raise UserError(_("Please select the shipment."))
        if len(self.inbound_shipment_list_placement_option_ids.filtered(lambda l: l.is_selected_shipment)) > 1:
            raise UserError(_("You can select only one option from the list."))
        shipment_placement_option = self.inbound_shipment_list_placement_option_ids.filtered(
            lambda l: l.is_selected_shipment)
        shipment_placement_option.shipment_plan_id.write(
            {'state': 'list_placement',
             **({
                    'is_ltl_get_shipment_info': True} if shipment_placement_option.shipment_plan_id.is_ltl_shipment else {})})
        self.amz_create_selected_placement_option_record(shipment_placement_option)
        return True

    def amz_prepare_shipment_values(self, shipment_data, shipment_plan_id):
        """
        Define this method for prepare inbound shipment values.
        :param: shipment_data: shipment response
        :param: shipment_plan_id: inbound.shipment.plan.new.ept()
        :return: dict {}
        """
        destination_address_id = False
        ship_to_address_id = False
        fulfillment_center_id = shipment_data.get('destination', {}).get('warehouseId', False)
        if shipment_data.get('destination', {}).get('address', {}):
            destination_address_id = self.create_or_update_shipment_address(
                shipment_data.get('destination', {}).get('address', {}))
        if shipment_data.get('source', {}).get('address', {}):
            ship_to_address_id = self.create_or_update_shipment_address(
                shipment_data.get('source', {}).get('address', {}))
        sequence = self.env.ref('amazon_ept.new_seq_inbound_shipments', raise_if_not_found=False)
        name = sequence.next_by_id() if sequence else '/'
        return {'name': name, 'amazon_reference_id': shipment_data.get('amazonReferenceId', ''),
                'placement_option_id': shipment_data.get('placementOptionId', ''),
                'shipment_confirmation_id': shipment_data.get('shipmentConfirmationId', ''),
                'shipment_id': shipment_data.get('shipmentId', ''),
                'state': 'WORKING', 'destination_address_id': destination_address_id,
                'ship_from_address_id': ship_to_address_id, 'shipment_plan_id': shipment_plan_id.id,
                'amz_inbound_create_date': fields.datetime.now().date() if shipment_plan_id else False,
                'instance_id_ept': shipment_plan_id.instance_id.id, 'fulfill_center_id': fulfillment_center_id,
                'is_ltl_shipment': shipment_plan_id.is_ltl_shipment}

    def create_or_update_shipment_address(self, address):
        """
        This method will prepare partner values based on the address details and
        return the created partner.
        """
        domain = []
        partner_obj = self.env['res.partner']
        country_obj = self.env['res.country']
        state_obj = self.env['res.country.state']
        state_id = False
        country = country_obj.search([('code', '=', address.get('countryCode', ''))])
        state = address.get('stateOrProvinceCode', '')
        name = address.get('name', '')
        street = address.get('addressLine1', '')
        street2 = address.get('addressLine2', '')
        postal_code = address.get('postalCode', '')
        city = address.get('city', '')
        email = address.get('email', '')
        if state:
            result_state = state_obj.search([('code', '=ilike', state), ('country_id', '=', country.id)])
            if not result_state:
                state = partner_obj.create_or_update_state_ept(country.code, state, postal_code, country)
                state_id = state.id
            else:
                state_id = result_state[0].id
        name and domain.append(('name', '=', name))
        street and domain.append(('street', '=', street))
        street2 and domain.append(('street2', '=', street2))
        city and domain.append(('city', '=', city))
        postal_code and domain.append(('zip', '=', postal_code))
        state_id and domain.append(('state_id', '=', state_id))
        country and domain.append(('country_id', '=', country.id))
        email and domain.append(('email', '=', email))
        partner = partner_obj.with_context(is_amazon_partner=True).search(domain)
        if not partner:
            partner_vals = {
                'name': name, 'is_company': False,
                'street': street, 'street2': street2,
                'city': city, 'country_id': country.id, 'zip': postal_code, 'state_id': state_id,
                'is_amz_customer': True, 'email': email
            }
            partner = partner_obj.create(partner_vals)
        return partner.id

    def amz_create_selected_placement_option_record(self, shipment_placement_option):
        """
        Define this method for create selected placement option record.
        :param: shipment_placement_option: inbound.shipment.list.placement.option.ept()
        :return
        """
        self.env['amz.selected.placement.option.ept'].create({
            'shipment_discount': shipment_placement_option.shipment_discount,
            'shipment_fees': shipment_placement_option.shipment_fees,
            'shipment_ids': shipment_placement_option.shipment_ids,
            'discount_currency_id': shipment_placement_option.discount_currency_id.id if shipment_placement_option.discount_currency_id else False,
            'fees_currency_id': shipment_placement_option.fees_currency_id.id if shipment_placement_option.fees_currency_id else False,
            'placement_option_id': shipment_placement_option.placement_option_id,
            'shipment_plan_id': shipment_placement_option.shipment_plan_id.id,
            'status': shipment_placement_option.status,
            'shipment_count': shipment_placement_option.shipment_count,
            'placement_status': 'shipment_placement_option'
        })


class ListPlacementOptions(models.TransientModel):
    _name = 'inbound.shipment.list.placement.option.ept'
    _description = 'Inbound Shipment List Placement Option'

    shipment_discount = fields.Float(string="Discount", help='Placement option discount.')
    shipment_fees = fields.Float(string="Fees", help="Placement option fees.")
    shipment_ids = fields.Text(string="Shipment Ids", help="Placement option shipment ids")
    discount_currency_id = fields.Many2one('res.currency', string='Discount Currency')
    fees_currency_id = fields.Many2one('res.currency', string='Fees Currency')
    placement_option_id = fields.Char(string='Placement Option Id',
                                      help="Shipment placement option id")
    shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Inbound Plan',
                                       help="Inbound shipment plan id")
    is_selected_shipment = fields.Boolean("Is Selected Shipment?", default=False)
    status = fields.Selection([('OFFERED', 'OFFERED'), ('ACCEPTED', 'ACCEPTED'),
                               ('EXPIRED', 'EXPIRED')], string='Operation Type')
    inbound_shipment_placement_details_wizard_id = fields.Many2one("inbound.shipment.placement.option.details",
                                                                   string="Inbound Shipment Placement Details Wizard")
    shipment_count = fields.Integer(compute="_compute_shipment_count",
                                    help="This Field relocates the shipment count.")
    placement_description = fields.Char(string="Placement Description")

    def _compute_shipment_count(self):
        """
        Define this method for compute shipment count.
        :return:
        """
        for record in self:
            record.shipment_count = len(ast.literal_eval(record.shipment_ids)) if record.shipment_ids else 0
