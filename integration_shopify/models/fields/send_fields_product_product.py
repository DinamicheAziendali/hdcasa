# See LICENSE file for full copyright and licensing details.

from odoo.addons.integration.models.fields import ProductProductSendMixin

from .send_fields import SendFieldsShopify
from ...shopify_api import VARIANT


class SendFieldsProductProductShopify(SendFieldsShopify, ProductProductSendMixin):

    def send_integration_cost_price(self, field_name):
        return {
            field_name: self.odoo_obj.get_integration_cost_price(self.integration),
        }

    def send_weight(self, field_name):
        shopify_uom = None

        if self.external_id:
            shopify_variant_id = self.adapter._parse_variant_id(self.external_id)
            # TODO: get rid of that request in converter
            variant = self.adapter.fetch_one(VARIANT, shopify_variant_id, fields=['weight_unit'])
            if not variant.is_new():
                shopify_uom = variant.weight_unit

        if not shopify_uom:
            shopify_uom = self.adapter.get_weight_uom_for_converter()

        weight = self.convert_weight_uom_from_odoo(self.odoo_obj.weight, shopify_uom)
        return {
            field_name: weight,
        }

    def send_taxable_flag(self, field_name):
        return {
            field_name: bool(self.odoo_obj.product_tmpl_id.taxes_id),
        }

    def send_lst_price(self, field_name):
        """
        Override to send the list price as a float and add Compare At Price when needed.
        """
        res = super().send_lst_price(field_name)

        price = float(res[field_name])

        # We use sale pricelist to detect if there any sale price for the product.
        # If there is, we use list price as compare at price and sale price as list price.
        if self.integration.integration_sale_pricelist_id:
            compare_at_price = None
            sale_price, pricelist_rule_id = self.integration.integration_sale_pricelist_id._get_product_price_rule(
                self.odoo_obj, 0)

            if not pricelist_rule_id:
                # No specific rule for this product, no sale price
                return {
                    field_name: price,
                    'compare_at_price': compare_at_price,
                }

            sale_price = self.get_price_by_send_tax_incl(sale_price)

            if sale_price != price:
                compare_at_price = price
                price = sale_price

            return {
                field_name: price,
                'compare_at_price': compare_at_price,
            }

        return {
            field_name: price,
            'compare_at_price': None,  # Reset compare at price if no sale pricelist
        }
