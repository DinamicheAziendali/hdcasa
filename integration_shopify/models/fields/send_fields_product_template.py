# See LICENSE file for full copyright and licensing details.

from odoo import _

from odoo.addons.integration.exceptions import ApiExportError
from odoo.addons.integration.models.fields import ProductTemplateSendMixin

from .send_fields import SendFieldsShopify
from ...shopify_api import METAFIELDS_NAME


class SendFieldsProductTemplateShopify(SendFieldsShopify, ProductTemplateSendMixin):

    def __init__(self, *args, **kwargs):
        super(SendFieldsProductTemplateShopify, self).__init__(*args, **kwargs)
        self.translations_to_update = {}
        self.translations_to_remove = {}

    def convert_to_external(self):
        result = super(SendFieldsProductTemplateShopify, self).convert_to_external()

        if self.integration.disable_translations_sync:
            return result

        # Always initialize translation containers
        result['translations_to_update'] = dict(self.translations_to_update)
        result['translations_to_remove'] = dict(self.translations_to_remove)

        # Get all translatable fields for the product template
        EcommerceFields = self.env['product.ecommerce.field.mapping'].with_context(integration_id=self.integration.id)
        odoo_name_fields = EcommerceFields.get_translatable_template_odoo_names()
        ecommerce_name_fields = EcommerceFields.get_translatable_template_api_names()

        # Get the template field name for the integration
        template_field_name = self.odoo_obj.get_integration_name_field()
        if template_field_name:
            odoo_name_fields.append(template_field_name)
            ecommerce_name_fields.append('title')

        # Convert each translatable field to the integration format
        for odoo_field_name, ecommerce_field_name in zip(odoo_name_fields, ecommerce_name_fields):
            updates, removals = self.convert_translated_field_to_integration_format_all_languages(
                odoo_field_name, ecommerce_field_name,
            )
            result['translations_to_update'].update(updates)
            result['translations_to_remove'].update(removals)

        return result

    def convert_pricelists(self, *args, **kw):
        raise NotImplementedError

    def send_product_status_spf(self, field_name):
        if not self.odoo_obj.active:
            return {field_name: 'archived'}
        send_inactive_product = not self.external_id and self.integration.send_inactive_product
        if send_inactive_product or not self.odoo_obj.sale_ok:
            return {field_name: 'draft'}
        return {field_name: 'active'}

    def send_categories(self, field_name):
        return {
            field_name: self.odoo_obj.get_categories(self.integration),
        }

    def send_price(self, field_name):
        return {}

    def send_product_tags(self, field_name):
        features = self.odoo_obj.get_product_features(self.integration)
        tags = ','.join([x['id_feature_value'] for x in features])
        return {
            field_name: tags,
        }

    def _get_kits(self):
        if self.integration.is_shopify():
            return []

        return super(SendFieldsProductTemplateShopify, self)._get_kits()

    def send_product_meta_title(self, field_name):
        return self._prepare_shopify_metafield_with_translations(
            field_name,
            odoo_field_name='website_seo_metatitle',
            shopify_key='meta_title',
            metafield_type='string',
        )

    def send_product_meta_description(self, field_name):
        return self._prepare_shopify_metafield_with_translations(
            field_name,
            odoo_field_name='website_seo_description',
            shopify_key='meta_description',
            metafield_type='multi_line_text_field',
        )

    def _prepare_shopify_metafield_with_translations(self, field_name, odoo_field_name, shopify_key, metafield_type):
        """
        Prepare Shopify metafield with optional translations.

        :param field_name: full metafield name (e.g., 'metafields.global.title_tag')
        :param odoo_field_name: Odoo field to extract the value from (e.g., 'website_seo_metatitle')
        :param shopify_key: Shopify metafield key (e.g., 'meta_title')
        :param metafield_type: Shopify metafield type, default to 'multi_line_text_field'
        :return: dict for metafield export
        """
        # Validate field structure
        if not field_name.startswith(f'{METAFIELDS_NAME}.'):
            raise ApiExportError(_(
                'To export the metafield "%s", the "namespace" must be specified. '
                'Please, go to "e-Commerce Integration → Product Fields → All Product Fields" '
                'and ensure these fields are filled in. Refer to Shopify '
                'Settings → Custom Data → Products for guidance.'
            ) % field_name)

        try:
            __, namespace, key = field_name.split('.')
        except ValueError:
            raise ApiExportError(_(
                'The metafield "%s" has an invalid format. It must follow the structure '
                '"%s.<Namespace>.<Key>".' % (field_name, METAFIELDS_NAME)
            ))

        # Primary value
        value = self.convert_translated_field_to_integration_format(odoo_field_name)

        # Collect translations for additional locales
        if not self.integration.disable_translations_sync:
            updates, removals = self.convert_translated_field_to_integration_format_all_languages(
                odoo_field_name, field_name)

            self.translations_to_update.update(updates)
            self.translations_to_remove.update(removals)

        return {
            field_name: {
                'key': key,
                'value': value,
                'namespace': namespace,
                'type': metafield_type,
            }
        }
