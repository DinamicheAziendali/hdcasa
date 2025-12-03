# See LICENSE file for full copyright and licensing details.

from odoo import _
from odoo.exceptions import UserError
from odoo.addons.integration.tools import make_list_if_not

from .receive_fields import ReceiveFieldsPresta
from ...prestashop_api import IS_FALSE, IS_TRUE, PRODUCT_TYPE_MAP


DENY_ORDER = 0
ALLOW_ORDER = 1


class ReceiveFieldsProductTemplatePresta(ReceiveFieldsPresta):

    def __init__(self, integration, odoo_obj=False, external_obj=False):
        super().__init__(integration, odoo_obj, external_obj)

        if not self.odoo_obj:
            self.odoo_obj = self.env['product.template']

    def get_ext_attr(self, ext_attr_name):
        if ext_attr_name == 'attr_values_ids_by_attr_id':
            ext_attribute_value_ids = self.external_obj.get('associations', {}).get(
                'product_option_values', {}).get('product_option_value', [])
            ext_attribute_value_ids = make_list_if_not(ext_attribute_value_ids)
            return self.find_attributes_in_odoo([x['id'] for x in ext_attribute_value_ids])

        raise UserError(_('Import attribute "%s" do not exist') % ext_attr_name)

    def convert_from_external(self):
        result = self.calculate_receive_fields()

        # 'type' field should be set only during the initial import
        # (to avoid issues with 'type' field changes on products synchronisation -
        # Odoo doesn't allow to change existing products types)
        if not self.odoo_obj:
            result['type'] = PRODUCT_TYPE_MAP[self.external_obj['type']['value']]

        return result

    def receive_integration_name(self, field_name):
        name_value = self.convert_translated_field_to_odoo_format(self.external_obj.get('name'))
        odoo_field = self.odoo_obj.get_integration_name_field()
        return {
            odoo_field: name_value,
        }

    def receive_price(self, field_name):
        price = float(self.external_obj['price'])

        if self.integration.price_including_taxes:  # TODO price_including_taxes
            id_tax_rules_group = self.external_obj['id_tax_rules_group']

            if id_tax_rules_group and id_tax_rules_group != IS_FALSE:
                erp_tax = self._get_odoo_tax_from_external(id_tax_rules_group)
                price = price * (1 + erp_tax.amount / 100)

        return {
            field_name: price,
        }

    def _check_active_categories(self, external_category_ids):
        if external_category_ids:
            external_categories = self.adapter._client.model('category').search_read(
                filters={'id': '[%s]' % '|'.join(external_category_ids),
                         'active': IS_TRUE},
                fields=['id'],
            )

            external_category_ids = [x['id'] for x in external_categories if x != IS_FALSE]

        return external_category_ids

    def receive_default_category(self, field_name):
        presta_category_id = self.external_obj['id_category_default']
        odoo_category_id = False

        # Check if inactive categories should be imported
        import_inactive_categories = self.integration.import_inactive_categories

        if presta_category_id and \
                (import_inactive_categories or self._check_active_categories([presta_category_id])):
            odoo_categories = self.find_categories_in_odoo([presta_category_id])
            if odoo_categories:
                odoo_category_id = odoo_categories[0]

        return {
            field_name: odoo_category_id,
        }

    def receive_categories(self, field_name):
        external_categories = make_list_if_not(self.external_obj.get(
            'associations', {}).get('categories', {}).get('category', []))

        if self.integration.import_inactive_categories:
            ext_category_ids = [x['id'] for x in external_categories if x != IS_FALSE]
        else:
            ext_category_ids = self._check_active_categories([x['id'] for x in external_categories])

        return {
            field_name: [(6, 0, self.find_categories_in_odoo(ext_category_ids))],
        }

    def receive_taxes(self, field_name):
        id_tax_rules_group = self.external_obj['id_tax_rules_group']

        if id_tax_rules_group and id_tax_rules_group != IS_FALSE:
            odoo_tax = self._get_odoo_tax_from_external(id_tax_rules_group)
            tax_ids = [(6, 0, odoo_tax.ids)]
        else:
            tax_ids = [(5,)]

        return {
            field_name: tax_ids,
        }

    def receive_additional_delivery_time(self, field_name):
        # We set Delivery Time on the integration level, so we don't need to import it
        return {}

    def receive_in_stock_delivery_message(self, field_name):
        return {}

    def receive_out_of_stock_delivery_message(self, field_name):
        return {}

    def receive_product_features(self, field_name):
        ProductFeature = self.env['product.feature']
        ProductFeatureValue = self.env['product.feature.value']
        odoo_features = [(5, 0)]

        external_features = self.external_obj.get(
            'associations', {}).get('product_features', {}).get('product_feature', [])

        external_features = [{
            'feature_id': x['id'],
            'feature_value_id': x['id_feature_value']
        } for x in make_list_if_not(external_features)]

        for feature_line in external_features:
            feature = ProductFeature
            feature_value = ProductFeatureValue

            if feature_line['feature_id'] != IS_FALSE:
                feature = ProductFeature.from_external(self.integration, feature_line['feature_id'])

            if feature_line['feature_value_id'] != IS_FALSE:
                feature_value = ProductFeatureValue.from_external(
                    self.integration, feature_line['feature_value_id'])

            if feature and feature_value:
                odoo_features.append((0, 0, {
                    'feature_id': feature.id,
                    'feature_value_id': feature_value.id,
                }))

        return {
            field_name: odoo_features,
        }

    def receive_product_cost_template(self, field_name):
        return {
            field_name: float(self.external_obj['wholesale_price']),
        }

    def receive_related_products(self, field_name):
        ProductTemplate = self.env['product.template']
        accessories = self.external_obj.get(
            'associations', {}).get('accessories', {}).get('product', [])

        external_template_ids = [x['id'] for x in make_list_if_not(accessories)]
        optional_product = ProductTemplate

        if external_template_ids:
            for external_template_id in external_template_ids:
                if external_template_id and external_template_id != IS_FALSE:
                    optional_product |= ProductTemplate.from_external(
                        self.integration,
                        external_template_id,
                        False,
                    )

        return {
            field_name: [(6, 0, optional_product.ids)],
        }

    def receive_weight(self, field_name):
        weight_uom = self.adapter.get_weight_uom_for_converter()
        weight = self.convert_weight_uom_to_odoo(float(self.external_obj['weight']), weight_uom)

        return {
            field_name: weight,
        }

    def receive_default_availability_option(self, field_name):
        if 'allow_out_of_stock_order' in self.odoo_obj:
            out_of_stock = self.adapter.import_out_of_stock_option(self.external_obj['id'])
            if out_of_stock in [DENY_ORDER, ALLOW_ORDER]:
                return {
                    'allow_out_of_stock_order': bool(out_of_stock),
                }
        return {}
