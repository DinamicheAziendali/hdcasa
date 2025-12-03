#  See LICENSE file for full copyright and licensing details.

from .fields.send_fields import SendFieldsPresta
from .fields.send_fields_product_product import SendFieldsProductProductPresta
from .fields.send_fields_product_template import SendFieldsProductTemplatePresta
from .fields.receive_fields import ReceiveFieldsPresta
from .fields.receive_fields_product_product import ReceiveFieldsProductProductPresta
from .fields.receive_fields_product_template import ReceiveFieldsProductTemplatePresta

from ..prestashop_api import PrestaShopApiClient, PRESTASHOP
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.addons.integration.models.sale_integration import DATETIME_FORMAT

import pytz
from datetime import datetime


class SaleIntegration(models.Model):
    _inherit = 'sale.integration'

    type_api = fields.Selection(
        selection_add=[(PRESTASHOP, 'PrestaShop')],
        ondelete={
            PRESTASHOP: 'cascade',
        },
    )

    delivery_time = fields.Selection(
        string='Delivery Time',
        selection=[
            ('none', 'None'),
            ('default', 'Default delivery time'),
            ('specific', 'Specific delivery time to this product')
        ],
        required=True,
        default='none',
        help=(
            'Configure the "Delivery Time" for products to sync with PrestaShop. '
            'Ensure you specify the "In-stock Delivery Days" and "Out-of-stock Delivery Days" '
            'if you opt for a "Specific delivery time for this product".'
        ),
    )

    product_delivery_in_stock = fields.Many2one(
        string='In-stock Delivery Duration Field',
        comodel_name='ir.model.fields',
        help=(
            'Specify the field from the Product template (accepting only float or integer values) '
            'to determine the "In-stock Delivery Days" for syncing with '
            'PrestaShop\'s corresponding setting.'
        ),
        domain='[("model_id.model", "=", "product.template"),'
               ' ("ttype", "in", ("float", "integer")) ]',
    )

    message_template_in_stock = fields.Char(
        string='In-stock Delivery Message Template',
        translate=True,
        default='Delivery in {} days',
        help=(
            'Define a message template for the "Delivery time of in-stock products" '
            'to be displayed in PrestaShop for products available in stock.'
        ),
    )

    product_delivery_out_of_stock = fields.Many2one(
        string='Out-of-stock Delivery Duration Field',
        comodel_name='ir.model.fields',
        help=(
            'Specify the Product template field (accepting only float or integer values) '
            'that holds the "Out-of-stock Delivery Days" for syncing with '
            'PrestaShop\'s equivalent attribute.'
        ),
        domain='[("model_id.model", "=", "product.template"),'
               ' ("ttype", "in", ("float", "integer")) ]',
    )

    message_template_out_of_stock = fields.Char(
        string='Out-of-stock Delivery Message Template',
        translate=True,
        default='Delivery in {} days',
        help=(
            'Define a message template for the "Delivery time of out-of-stock products with '
            'allowed orders" to communicate delivery expectations on PrestaShop for products that '
            'are out of stock.'
        ),
    )

    subscribed_to_newsletter_id = fields.Many2one(
        string='Newsletter Subscribed Field',
        comodel_name='ir.model.fields',
        help=(
            'Specify the boolean field on the Customer record where the subscription status '
            'for newsletters will be reflected. This field corresponds to the "Newsletter '
            'Subscribed" status from PrestaShop.'
        ),
        domain='[("model_id.model", "=", "res.partner"), ("ttype", "=", "boolean")]',
        default=lambda self: self._get_field_for_set_default('subscribed_to_newsletter_presta'),
    )

    newsletter_registration_date_id = fields.Many2one(
        string='Newsletter Registration Field',
        comodel_name='ir.model.fields',
        help=(
            'Specify the datetime field on the Customer record to log the date and time when '
            'the customer subscribed to the newsletter. This syncs with the '
            '"Newsletter Registration Date" from PrestaShop.'
        ),
        domain='[("model_id.model", "=", "res.partner"), ("ttype", "=", "datetime")]',
        default=lambda self: self._get_field_for_set_default('newsletter_registration_date_presta'),
    )

    customer_registration_date_id = fields.Many2one(
        string='Registration Date Field',
        comodel_name='ir.model.fields',
        help=(
            'Select the datetime field on the Customer record to record the original registration '
            'date of the customer, aligning with the "Registration Date" field from PrestaShop.'
        ),
        domain='[("model_id.model", "=", "res.partner"), ("ttype", "=", "datetime")]',
        default=lambda self: self._get_field_for_set_default('customer_registration_date_presta'),
    )

    additional_address_information = fields.Many2one(
        string='Additional Address Info Field',
        comodel_name='ir.model.fields',
        help=(
            'Choose the character or text field on the Customer record for storing any '
            'supplementary address details. This field will contain additional information '
            'from the corresponding e-commerce system field.'
        ),
        domain='[("model_id.model", "=", "res.partner"), ("ttype", "in", ("char", "text"))]',
    )

    import_inactive_categories = fields.Boolean(
        string='Sync Inactive Categories',
        help=(
            'Select this option to include inactive categories from e-commerce system '
            'in the import process, allowing them to be managed within Odoo.'
        ),
    )

    @api.onchange('type_api')
    def _onchange_type_api(self):
        for integration in self:
            if integration.is_prestashop():
                integration.set_settings_value('decimal_precision', '6')
                continue

            super(SaleIntegration, integration)._onchange_type_api()

    def is_carrier_tracking_required(self):
        if self.is_prestashop():
            return True
        return super(SaleIntegration, self).is_carrier_tracking_required()

    def _get_field_for_set_default(self, field_name):
        return self.env['ir.model.fields'].sudo().search([
            ('model_id.model', '=', 'res.partner'),
            ('name', '=', field_name),
        ], limit=1)

    @api.depends('last_receive_orders_datetime')
    def _compute_last_receive_orders_datetime_str(self):
        for integration in self:
            if integration.is_prestashop():
                settings_timezone = integration.get_settings_value('PS_TIMEZONE', '')
                if settings_timezone:
                    timezone = pytz.timezone(settings_timezone)
                    value = integration.last_receive_orders_datetime\
                        .astimezone(timezone).strftime(DATETIME_FORMAT)

                    integration.last_receive_orders_datetime_str = value
                    continue

            super(SaleIntegration, integration)._compute_last_receive_orders_datetime_str()

    @api.depends('orders_cut_off_datetime')
    def _compute_orders_cut_off_datetime_str(self):
        for integration in self:
            if integration.is_prestashop():
                settings_timezone = integration.get_settings_value('PS_TIMEZONE', '')
                if settings_timezone:
                    timezone = pytz.timezone(settings_timezone)
                    value = integration.orders_cut_off_datetime \
                        .astimezone(timezone).strftime(DATETIME_FORMAT)

                    integration.orders_cut_off_datetime_str = value
                    continue

            super(SaleIntegration, integration)._compute_orders_cut_off_datetime_str()

    def is_prestashop(self):
        self.ensure_one()
        return self.type_api == PRESTASHOP

    def get_class(self):
        self.ensure_one()
        if self.is_prestashop():
            return PrestaShopApiClient

        return super().get_class()

    def action_active(self):
        self.ensure_one()
        result = super().action_active()

        if self.is_prestashop():
            adapter = self._build_adapter()
            ps_timezone = adapter.get_configuration('PS_TIMEZONE')
            ps_weight_unit = adapter.get_configuration('PS_WEIGHT_UNIT')

            self.set_settings_value('PS_TIMEZONE', ps_timezone)
            self.set_settings_value('weight_uom', ps_weight_unit)

        return result

    def _retrieve_webhook_routes(self):
        if self.is_prestashop():
            routes = {
                'orders': [
                    ('Order Created', 'actionValidateOrder'),
                    ('Order Status Updated', 'actionOrderHistoryAddAfter'),
                ],
            }
            return routes
        return super(SaleIntegration, self)._retrieve_webhook_routes()

    def convert_external_tax_to_odoo(self, tax_id):
        if not self.is_prestashop():
            return super(SaleIntegration, self).convert_external_tax_to_odoo(tax_id)

        assert_message = 'Prestashop integration expected product `taxes_id` as a string.'
        assert isinstance(tax_id, str) is True, assert_message

        external_tax_group = self.env['integration.account.tax.group.external'].search([
            ('integration_id', '=', self.id),
            ('code', '=', tax_id),
        ], limit=1)

        external_tax = external_tax_group.default_external_tax_id

        if not external_tax:
            tax_name = external_tax_group.name or tax_id

            raise ValidationError(_(
                'It is not possible to import product into Odoo, because you haven\'t defined '
                'default External Tax for Tax Group "%s". Please,  click "Quick Configuration" '
                'button on your integration "%s" to define that mapping.'
            ) % (tax_name, self.name))

        odoo_tax = self.env['account.tax'].from_external(
            self,
            external_tax.code,
        )

        return odoo_tax

    def get_parent_delivery_methods(self, not_mapped_id):
        adapter = self._build_adapter()
        data = adapter.get_parent_delivery_methods(not_mapped_id)

        return data

    def _fetch_external_carrier(self, carrier_data):
        if self.is_prestashop():
            adapter = self._build_adapter()
            carrier = adapter.get_single_carrier(carrier_data['id'])
            return carrier

        return super(SaleIntegration, self)._fetch_external_carrier(carrier_data)

    def _set_zero_time_zone(self, external_date, to_string=False):
        if self.is_prestashop():
            settings_timezone = self.get_settings_value('PS_TIMEZONE', '')

            if settings_timezone:
                res = super(SaleIntegration, self)._set_zero_time_zone(external_date)
                timezone = pytz.timezone(settings_timezone)
                utc_data = timezone.localize(res).astimezone(pytz.utc)

                if to_string:
                    return datetime.strftime(utc_data, DATETIME_FORMAT)
                return utc_data.replace(tzinfo=None)

        return super(SaleIntegration, self)._set_zero_time_zone(external_date, to_string)

    def _get_error_webhook_message(self, error):
        if not self.is_prestashop():
            return super(SaleIntegration, self)._get_error_webhook_message(error)

        if error.args[0] in ['Method Not Allowed', 'Unauthorized']:
            url = 'https://addons.prestashop.com/en/third-party-data-integrations-crm-erp/' \
                  '48921-webhooks-integration.html'
            href_to_module = f'<a href="{url}" target="_blank">' + _('Webhook Integration') + '<a/>'
            message = _(
                'By default, Prestashop does not have webhooks functionality. '
                'Webhooks can be added only via 3rd party modules. We at VentorTech '
                'investigated available solutions on the Prestashop addons market '
                'and found that this %s module is suitable for '
                'this. Also, we communicated with the developers of this module and '
                'asked them to include in their plugin a few changes that are needed '
                'for our connector. So in order to use webhooks functionality with '
                'Prestashop, you need: '
                '<br/> (1) to purchase and install specified module '
                '<br/> (2) In Prestashop admin in the menu '
                '"Advanced Parameters → Webservice" for your API Key add '
                'permissions for "webhooks" resource.'
            ) % href_to_module
            return message

        return _('Prestashop Webhook Error: %s') % error.args[0]

    def init_send_field_converter(self, odoo_obj=False):
        if not self.is_prestashop():
            return super(SaleIntegration, self).init_send_field_converter(odoo_obj)

        if getattr(odoo_obj, '_name', '') == 'product.template':
            return SendFieldsProductTemplatePresta(self, odoo_obj)
        if getattr(odoo_obj, '_name', '') == 'product.product':
            return SendFieldsProductProductPresta(self, odoo_obj)
        return SendFieldsPresta(self, odoo_obj)

    def init_receive_field_converter(self, odoo_obj=False, external_obj=False):
        if not self.is_prestashop():
            return super(SaleIntegration, self).init_receive_field_converter(odoo_obj, external_obj)

        if getattr(odoo_obj, '_name', '') == 'product.template':
            return ReceiveFieldsProductTemplatePresta(self, odoo_obj, external_obj)
        if getattr(odoo_obj, '_name', '') == 'product.product':
            return ReceiveFieldsProductProductPresta(self, odoo_obj, external_obj)
        return ReceiveFieldsPresta(self, odoo_obj, external_obj)

    def _get_weight_integration_fields(self):
        if not self.is_prestashop():
            return super(SaleIntegration, self)._get_weight_integration_fields()

        return [
            'integration_prestashop.prestashop_ecommerce_field_template_weight',
            'integration_prestashop.prestashop_ecommerce_field_variant_weight',
        ]
