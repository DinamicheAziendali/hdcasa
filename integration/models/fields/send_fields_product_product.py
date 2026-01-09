# See LICENSE file for full copyright and licensing details.

from odoo import _
from odoo.exceptions import UserError

from .product_abstract import ProductAbstractSend


class ProductProductSendMixin(ProductAbstractSend):
    """Specific behavior only for `product.product` Odoo class during sending to external."""

    def convert_to_external(self):
        self.ensure_odoo_record()

        attribute_values = []
        for attribute_value in self.odoo_obj.product_template_attribute_value_ids:
            attr_value = attribute_value.product_attribute_value_id
            if attr_value.exclude_from_synchronization:
                continue
            value = attr_value.to_export_format_or_export(self.integration)

            attribute_values.append(value)

        result = {
            'id': self.odoo_obj.id,
            'external_id': self.external_id,
            'attribute_values': attribute_values,
            'fields': self.calculate_send_fields(self.external_id),
            'reference': getattr(self.odoo_obj, self.integration.product_reference_name),
            'reference_api_field': self.integration.variant_reference_api_name,
        }
        return result

    def send_lst_price(self, field_name):
        if self.integration.integration_pricelist_id:
            price = self.integration.integration_pricelist_id._get_product_price(self.odoo_obj, 0)
        else:
            price = self.odoo_obj.lst_price
        return {
            field_name: str(self.get_price_by_send_tax_incl(price)),
        }

    def send_pricelist_sale_price(self, field_name):
        if self.integration.integration_sale_pricelist_id:
            price, pricelist_rule_id = self.integration.integration_sale_pricelist_id._get_product_price_rule(
                self.odoo_obj, 0)

            if not pricelist_rule_id:
                # No specific rule for this product, no sale price
                return {
                    field_name: None,
                }
        else:
            raise UserError(_(
                'The product cannot be exported because the "Sale Pricelist for Product Export" is missing for '
                'the "%s" integration.\n'
                'Please review the following options to resolve the issue:\n'
                '1. Set the "Sale Pricelist for Product Export" in the settings '
                '(E-Commerce Integrations → Stores → %s → Products → Sale Pricelist for Product Export), OR\n'
                '2. Deactivate the field mapping for "Product Template Pricelist Sale Price".'
            ) % (self.integration.name, self.integration.name))

        return {
            field_name: self.get_price_by_send_tax_incl(price),
        }
