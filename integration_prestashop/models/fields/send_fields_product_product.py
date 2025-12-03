# See LICENSE file for full copyright and licensing details.

from .send_fields import SendFieldsPresta
from ...presta.base_model import BaseModel
from odoo.addons.integration.models.fields import ProductProductSendMixin


class SendFieldsProductProductPresta(SendFieldsPresta, ProductProductSendMixin):

    def _get_value_for_template_in_presta(self, field_name):
        external_record = self.odoo_obj.product_tmpl_id.try_to_external_record(self.integration)
        product_code = external_record and external_record.code

        if not product_code:
            return 0

        value = self.adapter._client.model('product').search_read(
            filters={'id': '[%s]' % product_code}, fields=[field_name])

        return value and float(value[0][field_name]) or 0

    def send_product_cost_variant(self, field_name):
        if self._check_combinations_not_exist(self.odoo_obj.product_tmpl_id):
            return {field_name: 0}
        return {
            field_name: round(
                self.odoo_obj.standard_price, BaseModel.PRESTASHOP_PRECISION),
        }

    def send_weight(self, field_name):
        if self._check_combinations_not_exist(self.odoo_obj.product_tmpl_id):
            return {field_name: 0}
        else:
            weight_uom = self.adapter.get_weight_uom_for_converter()

            tmpl_weight = self._get_value_for_template_in_presta('weight')
            weight = self.odoo_obj.weight - tmpl_weight

            weight = self.convert_weight_uom_from_odoo(weight, weight_uom)

            return {
                field_name: round(weight, BaseModel.PRESTASHOP_PRECISION),
            }

    def send_price(self, field_name):
        return {
            field_name: round(
                self.get_price_by_send_tax_incl(self.odoo_obj.price_extra),
                BaseModel.PRESTASHOP_PRECISION,
            )
        }
