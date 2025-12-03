# See LICENSE file for full copyright and licensing details.

from odoo.addons.integration.tools import make_list_if_not
from odoo.addons.integration.models.fields import ProductTemplateSendMixin

from .send_fields import SendFieldsPresta
from ...prestashop_api import IS_TRUE, IS_FALSE, PRESTASHOP_DELIVERY_TIME_MAPPING
from ...presta.base_model import BaseModel


class SendFieldsProductTemplatePresta(SendFieldsPresta, ProductTemplateSendMixin):

    def convert_to_external(self):
        result = super(SendFieldsProductTemplatePresta, self).convert_to_external()

        if 'active' in result['fields'] and \
                self.integration.send_inactive_product and not self.external_id:
            result['fields']['active'] = IS_FALSE

        if 'manufacturer_name' in result['fields']:
            manufacturer_name = result['fields'].pop('manufacturer_name')
            manufacturer_id = '0'

            if manufacturer_name:
                manufacturer = self.adapter._client.model('manufacturer').search_read(
                    filters={'name': manufacturer_name},
                    fields=['id'],
                )

                if manufacturer:
                    manufacturer_id = manufacturer[0]['id']
                else:
                    presta_manufacturer = self.adapter._client.model('manufacturer')
                    presta_manufacturer.name = manufacturer_name
                    presta_manufacturer.active = '1'
                    presta_manufacturer.save()
                    manufacturer_id = presta_manufacturer.id

            result['fields']['id_manufacturer'] = manufacturer_id

        if result['fields'].get('id_category_default') and 'categories' not in result['fields']:
            default_category_id = result['fields']['id_category_default']
            category_ids = [default_category_id]

            if self.external_id:
                category_ids = self._get_external_template_categories()

                if default_category_id not in category_ids:
                    category_ids.append(default_category_id)

            result['fields']['categories'] = self.adapter._client.model('category').get(
                category_ids)

        return result

    def _get_external_template_categories(self):
        if not self.external_id:
            return []

        presta_product = self.adapter._client.model('product').search_read(
            filters={'id': '[%s]' % self.external_id},
        )

        if not presta_product:
            return []

        ext_categories = presta_product[0].get(
            'associations', {}).get('categories', {}).get('category', [])
        ext_categories = make_list_if_not(ext_categories)

        ext_category_ids = [x['id'] for x in ext_categories if x != IS_FALSE]
        return ext_category_ids

    def send_integration_name(self, field_name):
        return {
            field_name: self.odoo_obj.get_integration_name(self.integration),
        }

    def send_price(self, field_name):
        res = super().send_price(field_name)
        return {
            field_name: round(
                float(res['price']),
                BaseModel.PRESTASHOP_PRECISION,
            ),
            'show_price': IS_TRUE,
        }

    def send_default_category(self, field_name):
        return {
            field_name: self.odoo_obj.get_default_category(self.integration),
        }

    def send_categories(self, field_name):
        categories_list = self.odoo_obj.get_categories(self.integration)
        default_category = self.odoo_obj.get_default_category(self.integration)

        if default_category:
            categories_list.append(default_category)

        category_ids = [x for x in set(categories_list) if x != IS_FALSE]

        return {
            field_name: self.adapter._client.model('category').get(category_ids),
        }

    def send_taxes(self, field_name):
        taxes = self.odoo_obj.get_taxes(self.integration) or [{'tax_group_id': IS_FALSE}]

        return {
            field_name: taxes[0]['tax_group_id'],
        }

    def send_additional_delivery_time(self, field_name):
        return {
            field_name: PRESTASHOP_DELIVERY_TIME_MAPPING[self.integration.delivery_time],
        }

    def send_in_stock_delivery_message(self, field_name):
        return {
            field_name: self.odoo_obj.get_in_stock_delivery_message(self.integration),
        }

    def send_out_of_stock_delivery_message(self, field_name):
        return {
            field_name: self.odoo_obj.get_out_of_stock_delivery_message(self.integration),
        }

    def send_product_features(self, field_name):
        return {
            field_name: self.odoo_obj.get_product_features(self.integration),
        }

    def send_product_cost_template(self, field_name):
        # We send cost for template only if it has not combinations
        if self._check_combinations_not_exist(self.odoo_obj):
            return {field_name: round(
                self.odoo_obj.standard_price, BaseModel.PRESTASHOP_PRECISION)}
        else:
            return {}

    def send_related_products(self, field_name):
        return {
            field_name: [{'id': x} for x in self.odoo_obj.get_related_products(self.integration)],
        }

    def send_weight(self, field_name):
        # We send weight for template only if it has not combinations
        if self._check_combinations_not_exist(self.odoo_obj):
            weight_uom = self.adapter.get_weight_uom_for_converter()
            weight = self.convert_weight_uom_from_odoo(self.odoo_obj.weight, weight_uom)
            return {field_name: round(weight, BaseModel.PRESTASHOP_PRECISION)}
        else:
            return {}

    def send_default_availability_option(self, field_name):
        if 'allow_out_of_stock_order' in self.odoo_obj:
            out_of_stock_option = int(self.odoo_obj.allow_out_of_stock_order)
            data = {
                'external_id': self.external_id,
                field_name: out_of_stock_option,
            }
            self.adapter.export_out_of_stock_option(data)
        return {}
