# See LICENSE file for full copyright and licensing details.

from .product_abstract import ProductAbstractReceive


class ProductTemplateReceiveMixin(ProductAbstractReceive):
    """Specific behavior only for `product.template` Odoo class during receiving external."""

    def receive_pricelist_sale_price(self, *args, **kwargs):
        return {}

    def _parse_langs(self, vals, attr, variations, custom=True):
        """Currently it uses only for `Magento 2` integration"""
        lang = self.integration.get_adapter_lang_code()

        def _parse_simple(vals, attr):
            return vals.get(attr)

        value = _parse_simple(vals, attr)
        if not value:
            return value

        dict_values = {
            k: _parse_simple(self.adapter._parse_custom_attributes(v) if custom else v, attr)
            for k, v in variations.items()
        }
        dict_values = {
            k: v for k, v in dict_values.items() if v
        }
        if dict_values:
            dict_values[lang] = value
            value = {
                'language': [
                    {'attrs': {'id': k}, 'value': v} for k, v in dict_values.items()
                ],
            }

        return value
