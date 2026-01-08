# See LICENSE file for full copyright and licensing details.

from odoo import _
from odoo.exceptions import UserError
from odoo.addons.integration.models.fields.common_fields import GENERAL_GROUP
from odoo.addons.integration.models.fields import ProductTemplateReceiveMixin

from .receive_fields import ReceiveFieldsShopify
from ...shopify.shopify_client import COLLECT
from ...shopify.shopify_order import format_attr_value_code


class ReceiveFieldsProductTemplateShopify(ReceiveFieldsShopify, ProductTemplateReceiveMixin):

    def __init__(self, integration, odoo_obj=False, external_obj=False):
        super().__init__(integration, odoo_obj, external_obj)

        if not self.odoo_obj:
            self.odoo_obj = self.env['product.template']

    def get_ext_attr(self, ext_attr_name):
        if ext_attr_name == 'attr_values_ids_by_attr_id':
            attributes = self.adapter._attribute_value_from_template(self.external_obj)

            return self.find_attributes_in_odoo(
                format_attr_value_code(name, value) for (name, value) in attributes
            )

        raise UserError(_(
            'The attribute "%s" does not exist or is unsupported for import. '
            'Please verify the attribute name and try again.'
        ) % ext_attr_name)

    def convert_from_external(self):
        result = self.calculate_receive_fields()

        # 'type' field should be set only during the initial import
        # (to avoid issues with 'type' field changes on products synchronisation -
        # Odoo doesn't allow to change existing products types)
        if not self.odoo_obj:
            result['type'] = 'product'

        return result

    def receive_integration_name(self, field_name):
        name_value = self.convert_translated_field_to_odoo_format(self.external_obj.title)
        odoo_field = self.odoo_obj.get_integration_name_field()
        return {
            odoo_field: name_value,
        }

    def receive_product_status_spf(self, field_name):
        return {
            'sale_ok': self.external_obj.status == 'active',
            'active': self.external_obj.status != 'archived',
        }

    def receive_categories(self, field_name):
        collects = self.adapter.fetch_multi(
            COLLECT,
            params={
                'product_id': self.external_obj.id,
            },
            fields=['collection_id'],
        )

        ext_category_ids = [x.collection_id for x in collects]

        return {
            field_name: [(6, 0, self.find_categories_in_odoo(ext_category_ids))],
        }

    def receive_list_price(self, field_name):
        # TODO price_including_taxes
        # The API only returns prices for variants, so the method returns an empty dictionary.
        if len(self.external_obj.variants) != 1:
            return {}

        # Price shouldn't be imported if pricelist for export was set (excluding the first time import)
        if self.integration.integration_pricelist_id and not self.first_time_import:
            return {}

        variant = self.external_obj.variants[0]
        price = float(variant.price)

        if self.integration.integration_sale_pricelist_id:
            compare_at_price = float(variant.compare_at_price) if variant.compare_at_price else None

            # If there is a compare at price, we use it as the list price.
            if compare_at_price:
                price = compare_at_price

        return {field_name: price}

    def receive_product_tags(self, field_name):
        ProductFeatureValue = self.env['product.feature.value']
        FeatureValueExternal = self.env['integration.product.feature.value.external']
        odoo_features = [(5, 0)]

        feature = self.env['product.feature'].from_external(self.integration, GENERAL_GROUP)
        tags = self.external_obj.tags
        tags = tags.split(',')

        for tag in tags:
            tag = tag.strip()

            if not tag:
                continue

            feature_value = ProductFeatureValue.from_external(
                self.integration, tag, raise_error=False)

            if not feature_value:
                feature_value = ProductFeatureValue.create({
                    'feature_id': feature.id,
                    'name': tag,
                })

                external_feature = feature.to_external_record(self.integration)

                feature_value_external = FeatureValueExternal.create_or_update({
                    'integration_id': self.integration.id,
                    'code': tag,
                    'name': tag,
                    'external_feature_id': external_feature.id,
                })

                feature_value_external.create_or_update_mapping(odoo_id=feature_value.id)

            odoo_features.append((0, 0, {
                'feature_id': feature.id,
                'feature_value_id': feature_value.id,
            }))

        return {
            field_name: odoo_features,
        }

    def _receive_product_meta_field(self, field_name, namespace, key, translation_key):
        """
        Helper method to receive a generic product meta field and its translations.

        :param field_name: The name of the field to return in the result.
        :param namespace: The namespace of the metafield.
        :param key: The key of the metafield.
        :param translation_key: The key used for translations.
        :return: A dictionary containing the field name and its translations.
        """
        meta_field_translations = {}

        # Step 1: Get primary locale and supported locales
        primary_locale, locales = self.adapter._process_locales()
        if not primary_locale:
            primary_locale = self.adapter.lang

        # Step 2: Get all language mappings (external code -> odoo ID)
        language_mappings = self.env['integration.res.lang.mapping'].search([
            ('integration_id', '=', self.integration.id),
        ])
        language_codes = {x.external_language_id.code: x.language_id.id for x in language_mappings}

        # Step 3: Find primary metafield value
        primary_value = None
        for meta_field in self.external_obj.metafields():
            if meta_field.key == key and meta_field.namespace == namespace:
                primary_value = meta_field.value
                break

        # Step 4: Set primary locale translation
        if primary_locale in language_codes:
            meta_field_translations[language_codes[primary_locale]] = primary_value

        # Step 5: Fetch Shopify translations (if enabled)
        if not self.integration.disable_translations_sync:
            external_template_id = self.external_obj.id
            translations_product = self.adapter._graphql.get_product_translations(external_template_id, locales)

            for alias_key, translation_list in translations_product.items():
                locale = alias_key.split('_', 1)[-1]
                if locale == primary_locale:
                    continue

                for item in translation_list:
                    if item.get('key') == translation_key and locale in language_codes:
                        meta_field_translations[language_codes[locale]] = item.get('value')
                        break

        # Step 6: Ensure all mapped languages are included, fallback to primary_value
        for locale_code, lang_id in language_codes.items():
            if lang_id not in meta_field_translations:
                meta_field_translations[lang_id] = primary_value

        return {
            field_name: {'language': meta_field_translations}
        }

    def receive_product_meta_title(self, field_name):
        """
        Receive the product meta title from the external object and its translations.
        """
        return self._receive_product_meta_field(field_name, 'global', 'title_tag', 'meta_title')

    def receive_product_meta_description(self, field_name):
        """
        Receive the product meta description from the external object.
        """
        return self._receive_product_meta_field(field_name, 'global', 'description_tag', 'meta_description')
