# See LICENSE file for full copyright and licensing details.

from decimal import Decimal

from odoo import _
from odoo.exceptions import UserError
from odoo.addons.integration.tools import make_list_if_not
from odoo.addons.integration.models.fields import ProductProductReceiveMixin

from .receive_fields import ReceiveFieldsPresta
from ...prestashop_api import IS_FALSE


class ReceiveFieldsProductProductPresta(ReceiveFieldsPresta, ProductProductReceiveMixin):

    def __init__(self, integration, odoo_obj=False, external_obj=False):
        super().__init__(integration, odoo_obj, external_obj)

        if not self.odoo_obj:
            self.odoo_obj = self.env['product.product']

    def get_ext_attr(self, ext_attr_name):
        if ext_attr_name == 'variant_id':
            return f"{self.external_obj['id_product']}-{self.external_obj['id']}"
        if ext_attr_name == 'attribute_value_ids':
            attribute_value_ids = self.external_obj.get('associations', {})\
                .get('product_option_values', {}).get('product_option_value', [])
            return [x['id'] for x in make_list_if_not(attribute_value_ids)]

        raise UserError(_('Import attribute "%s" do not exist') % ext_attr_name)

    def receive_product_cost_variant(self, field_name):
        price = Decimal(self.external_obj['wholesale_price'])
        if not price:
            price = Decimal(self.external_obj['template']['wholesale_price'])
        return {
            field_name: float(price),
        }

    def receive_weight(self, field_name):
        weight = float(
            Decimal(self.external_obj['template']['weight']) +
            Decimal(self.external_obj['weight'])
        )
        weight_uom = self.adapter.get_weight_uom_for_converter()
        weight = self.convert_weight_uom_to_odoo(weight, weight_uom)

        return {
            field_name: weight,
        }

    def receive_price(self, field_name):
        extra_price = float(Decimal(self.external_obj['price']))

        if self.integration.price_including_taxes:
            id_tax_rules_group = self.external_obj['template']['id_tax_rules_group']

            if id_tax_rules_group and id_tax_rules_group != IS_FALSE:
                erp_tax = self._get_odoo_tax_from_external(id_tax_rules_group)
                extra_price = extra_price * (1 + erp_tax.amount / 100)

        return {
            field_name: extra_price,
        }
