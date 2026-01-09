from odoo import fields, models, _, api
from odoo.exceptions import UserError
from odoo.addons.iap.tools import iap_tools
from ..endpoint import DEFAULT_ENDPOINT
import ast


class PackingOptionDetails(models.TransientModel):
    _name = "inbound.shipment.packing.option.details"
    _description = 'Inbound shipment packing details'

    inbound_shipment_list_packing_option_ids = fields.One2many('inbound.shipment.list.packing.option.ept',
                                                                 'inbound_shipment_packing_details_wizard_id',
                                                               string="Inbound Shipment Packing List")

    @api.model
    def default_get(self, fields):
        """
        Define this method for list the placement options.
        :param fields: []
        :return: update result dict {}
        """
        res = super(PackingOptionDetails, self).default_get(fields)
        packing_options = self._context.get('packing_options', [])
        shipment_plan_id = self._context.get('shipment_plan_id', False)
        ship_plan_rec = self.env['inbound.shipment.plan.new.ept'].browse(shipment_plan_id)
        result = []
        for packing_option in packing_options:
            discount_amount = packing_option.get('discounts', []) and packing_option.get('discounts', [{}])[0].get(
                'value', {}).get('amount', 0.0) or 0.0
            discount_code = packing_option.get('discounts', []) and packing_option.get('discounts', [{}])[0].get(
                'value', {}).get('code', '') or ''
            fees_amount = packing_option.get('fees', []) and packing_option.get('fees', [{}])[0].get(
                'value', {}).get('amount', 0.0) or 0.0
            fees_code = packing_option.get('fees', []) and packing_option.get('fees', [{}])[0].get(
                'value', {}).get('code', '') or ''
            packing_option_id = packing_option.get('packingOptionId', '')
            packing_group_ids = packing_option.get('packingGroups', '')
            status = packing_option.get('status', [])
            discount_currency = self.find_currency_ept(discount_code)
            fees_currency = self.find_currency_ept(fees_code)
            fees_description = packing_option.get('fees', []) and packing_option.get('fees', [{}])[0].get(
                'description', '') or ''
            placement_data = {
                'packing_discount': discount_amount,
                'discount_currency_id': discount_currency.id if discount_currency else False,
                'packing_fees': fees_amount,
                'fees_currency_id': fees_currency.id if fees_currency else False,
                'packing_option_id': packing_option_id,
                'status': status,
                'packing_group_ids': packing_group_ids,
                'shipment_plan_id': ship_plan_rec.id,
                'placement_description': fees_description
            }
            result.append((0, 0, placement_data))
        res.update({'inbound_shipment_list_packing_option_ids': result})
        return res

    def confirm_packing_option_sp_api_v2024(self):
        """
        Confirm packing option for inbound shipment plan.
        """
        inbound_shipment_plan_obj = self.env['inbound.shipment.plan.new.ept']
        if not self.inbound_shipment_list_packing_option_ids.filtered(lambda l: l.is_selected_packing):
            raise UserError(_("Please select the packing option."))
        if len(self.inbound_shipment_list_packing_option_ids.filtered(lambda l: l.is_selected_packing)) > 1:
            raise UserError(_("You can select only one option from the list."))
        packing_option = self.inbound_shipment_list_packing_option_ids.filtered(
            lambda l: l.is_selected_packing)
        kwargs = inbound_shipment_plan_obj.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            packing_option.shipment_plan_id.instance_id, 'confirm_packing_option_plan_sp_api_v2024')
        kwargs.update({'inboundPlanId': packing_option.shipment_plan_id.inbound_plan_id,
                       'packingOptionId':packing_option.packing_option_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        packing_option.shipment_plan_id.amz_create_inbound_shipment_check_status(
            packing_option.shipment_plan_id.id, 'confirm_packing_option',
            response.get('result', {}).get('operationId', ''))
        packing_option.shipment_plan_id.write({'state': 'confirm_packing_option'})
        self.amz_create_packing_group_details_record(packing_option)
        self.amz_create_selected_packing_option_record(packing_option)
        return True

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

    def amz_create_selected_packing_option_record(self, packing_option):
        """
        Define this method for create selected placement option record.
        :param: packing_option: inbound.shipment.list.packing.option.ept()
        :return
        """
        self.env['amz.selected.placement.option.ept'].create({
            'packing_discount': packing_option.packing_discount,
            'packing_fees': packing_option.packing_fees,
            'packing_group_ids': packing_option.packing_group_ids,
            'discount_currency_id': packing_option.discount_currency_id.id if packing_option.discount_currency_id else False,
            'fees_currency_id': packing_option.fees_currency_id.id if packing_option.fees_currency_id else False,
            'packing_option_id': packing_option.packing_option_id,
            'shipment_plan_id': packing_option.shipment_plan_id.id,
            'status': packing_option.status,
            'package_count': packing_option.package_count,
            'placement_status': 'packing_placement_option'
        })

    def amz_create_packing_group_details_record(self, packing_option):
        """
        Define this method for create packing group details for selected group id.
        :param: packing_option: inbound.shipment.list.packing.option.ept()
        :return: True
        """
        amz_cart_cont_info_obj = self.env['amazon.carton.content.info.ept']
        amazon_product_obj = self.env['amazon.product.ept']
        kwargs = packing_option.shipment_plan_id.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            packing_option.shipment_plan_id.instance_id, 'list_packing_group_items_sp_api_v2024')
        kwargs.update({'inboundPlanId': packing_option.shipment_plan_id.inbound_plan_id})
        for packing_group_id in ast.literal_eval(packing_option.packing_group_ids):
            kwargs.update({'packingGroupId': packing_group_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            group_items = response.get('result', {}).get('items', {})
            for item in group_items:
                amazon_product = amazon_product_obj.search_amazon_product(
                    packing_option.shipment_plan_id.instance_id.id, item.get('msku', ''), 'FBA')
                if not amazon_product:
                    amazon_product = amazon_product_obj.search([
                        ('product_asin', '=', item.get('fnsku', '')),
                        ('instance_id', '=', packing_option.shipment_plan_id.instance_id.id)], limit=1)
                if not amazon_product:
                    continue
                amz_cart_cont_info_obj.create({
                    'amazon_product_id': amazon_product.id,
                    'seller_sku': item.get('msku', '') if item.get('msku', '') else item.get('fnsku', ''),
                    'quantity': item.get('quantity', 0),
                    'packing_group_id': packing_group_id,
                    'inbound_shipment_plan_id': packing_option.shipment_plan_id.id
                })


class ListPackingOptions(models.TransientModel):
    _name = 'inbound.shipment.list.packing.option.ept'
    _description = 'Inbound Shipment List Packing Option'

    packing_discount = fields.Float(string="Discount", help='Packing option discount.')
    packing_fees = fields.Float(string="Fees", help="Packing option fees.")
    packing_group_ids = fields.Text(string="Group Ids", help="Packing Group ids")
    discount_currency_id = fields.Many2one('res.currency', string='Discount Currency')
    fees_currency_id = fields.Many2one('res.currency', string='Fees Currency')
    packing_option_id = fields.Char(string='Packing Option Id', help="Packing option id")
    shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Inbound Plan',
                                       help="Inbound shipment plan id")
    is_selected_packing = fields.Boolean("Is Selected Packing?", default=False)
    status = fields.Selection([('OFFERED', 'OFFERED'), ('ACCEPTED', 'ACCEPTED'),
                               ('EXPIRED', 'EXPIRED')], string='Operation Type')
    inbound_shipment_packing_details_wizard_id = fields.Many2one("inbound.shipment.packing.option.details",
                                                                 string="Inbound Shipment Packing Details Wizard")
    placement_description = fields.Char(string="Placement Description")
    package_count = fields.Integer(compute="_compute_package_count",
                                   help="This Field relocates the package count.")

    def _compute_package_count(self):
        """
        Define this method for compute package count.
        :return:
        """
        for record in self:
            record.package_count = len(ast.literal_eval(record.packing_group_ids)) if record.packing_group_ids else 0
