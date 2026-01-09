# See LICENSE file for full copyright and licensing details.

from odoo import _
from odoo.exceptions import UserError

from .common_fields import CommonFields
from ...exceptions import NotMappedToExternal
from ...models.fields.common_fields import TEXT_FIELDS, MANY2ONE_FIELDS
from ...tools import round_float


class SendFields(CommonFields):

    def __init__(self, integration, odoo_obj, external_obj=False):
        super().__init__(integration, odoo_obj, external_obj)
        self.external_id = self._update_external_id()
        self._sub_converter = None

    def replace_record(self, odoo_obj):
        self.odoo_obj = odoo_obj
        self._add_context_lang()

        self.external_id = self._update_external_id()
        self._sub_converter = None

    @property
    def first_time_export(self):
        return not self.external_id

    def ensure_mapped(self):
        return all([self.odoo_obj, self.external_id])

    def ensure_odoo_record(self):
        if not self.odoo_obj:
            raise UserError(_(
                'Odoo record is not defined for the current converter: %s.\n'
                'This is a technical issue. Please ensure that the Odoo instance is properly configured or '
                'contact support team for assistance.'
            ) % self)

    def ensure_external_code(self):
        self.ensure_odoo_record()

        if not self.external_id:
            raise NotMappedToExternal(_(
                'No corresponding external record found for the Odoo value: %s.\n'
                'This is a technical issue. Please ensure that the Odoo instance is properly configured or '
                'contact support team for assistance.'
            ) % self, model_name=self.odoo_obj._name, obj_id=self.odoo_obj.id, integration=self.integration)

    def convert_translated_field_to_integration_format(self, field_name):
        return getattr(self.odoo_obj, field_name)

    def _update_external_id(self):
        if not self.odoo_obj:
            return None
        external_record_method = getattr(self.odoo_obj, 'try_to_external_record', lambda x: False)
        external_record = external_record_method(self.integration)
        return external_record and external_record.code

    def _get_simple_value(self, ecommerce_field):
        field_name = ecommerce_field.odoo_field_name
        odoo_value = getattr(self.odoo_obj, field_name)

        api_value = self._prepare_simple_value(ecommerce_field, odoo_value)

        return {ecommerce_field.technical_name: api_value}

    def _prepare_simple_value(self, ecommerce_field, odoo_value):
        field_type = ecommerce_field.odoo_field_id.ttype

        if not odoo_value and field_type in TEXT_FIELDS:
            return ''

        if field_type in MANY2ONE_FIELDS:
            if odoo_value:
                return odoo_value.display_name
            else:
                return ''

        return odoo_value

    def _get_translatable_field_value(self, ecommerce_field):
        odoo_name = ecommerce_field.odoo_field_name
        api_value = self.convert_translated_field_to_integration_format(odoo_name)
        return {ecommerce_field.technical_name: api_value or ''}

    def _get_python_method_value(self, ecommerce_field):
        result = self._compute_field_value_using_python_method(
            ecommerce_field.send_method, ecommerce_field.technical_name)
        return result

    def calculate_send_fields(self, external_code):
        domain_ext = [] if self.first_time_export else [('send_on_update', '=', True)]
        return self.calculate_fields(domain_ext)

    def get_price_by_send_tax_incl(self, price):
        # Get the decimal precision value from the integration settings
        decimal_precision = self.integration.get_settings_value('decimal_precision')
        try:
            decimal_precision = int(decimal_precision)
        except ValueError:
            raise UserError(_(
                'The "decimal_precision" value is not a valid integer.\n'
                'To resolve this issue, please follow these steps:\n'
                '1. Go to "E-Commerce Integrations → Stores → %s → General tab → Parameters table".\n'
                '2. Locate the parameter named "decimal_precision".\n'
                '3. Enter a valid integer value for the "decimal_precision" parameter.'
            ) % self.integration.name)

        if self.integration.select_send_sale_price == 'no_changes':
            return round_float(price, decimal_precision)

        # Calculate the price rounding based on the decimal precision
        precision_rounding = 10 ** (-decimal_precision)

        # In some cases, it is necessary to force/prevent the rounding of the tax and the total
        # amounts. For example, in SO/PO line, we don't want to round the price unit at the
        # precision of the currency.
        # The context key 'round' allows to force the standard behaviour.
        # We also pass the context variable 'precision_rounding' to indicate the rounding precision.
        ctx = dict(round=False)
        if self.env.company.currency_id.rounding > precision_rounding:
            # If precision_rounding is greater than the currency's rounding precision,
            # update the context to use the custom precision for rounding.
            ctx.update(precision_rounding=precision_rounding)

        res = self.odoo_obj.taxes_id\
            .filtered(lambda x: x.company_id == self.integration.company_id) \
            .with_context(**ctx) \
            .compute_all(price, product=self.odoo_obj, partner=self.env['res.partner'])

        if self.integration.select_send_sale_price == 'tax_included':
            return round_float(res['total_included'], decimal_precision)

        return round_float(res['total_excluded'], decimal_precision)

    def convert_weight_uom_from_odoo(self, weight, uom_name):
        weight = self._convert_weight_uom(weight, uom_name, False)
        return weight
