# See LICENSE file for full copyright and licensing details.

from odoo import _

from odoo.addons.integration.exceptions import ApiExportError
from odoo.addons.integration.models.fields import SendFields

from ...shopify_api import METAFIELDS_NAME


class SendFieldsShopify(SendFields):

    def convert_translated_field_to_integration_format_all_languages(self, odoo_field_name, ecommerce_field_name):
        """
        Convert a translatable field to the integration format for all languages.
        This method retrieves the primary value in the primary locale and then
        iterates through all language mappings to create a list of translations
        to update and a list of translations to remove.
        :param odoo_field_name: The name of the Odoo field to convert.
        :param ecommerce_field_name: The name of the field in the e-commerce platform.
        :return: A tuple containing two lists:
            - translations_to_update: Dictionary of translations to update
            - translations_to_remove: Dictionary of locales to remove translations for
        """
        translations_to_update = []
        translations_to_remove = []

        primary_language_code = self.integration.get_settings_value('language_id')
        # If the primary locale is not available (e.g., due to an older connector version or missing config),
        # return an empty list as translations cannot be properly formatted without it.
        if not primary_language_code:
            return []

        primary_language_external = self.env['integration.res.lang.external'].search([
            ('code', '=', primary_language_code),
            ('integration_id', '=', self.integration.id),
        ], limit=1)

        if not primary_language_external:
            # If no primary language external mapping is found, return empty translations
            return []

        odoo_primary_language_code = primary_language_external.odoo_record.code

        if not odoo_primary_language_code:
            # If the primary language code is not set, return empty translationsAdd commentMore actions
            return []

        # Get field value in primary locale
        primary_value = getattr(
            self.odoo_obj.with_context(lang=odoo_primary_language_code),
            odoo_field_name
        ) or ''

        # Loop through language mappings and build translations
        language_mappings = self.env['integration.res.lang.mapping'].search([
            ('language_id', '!=', False),
            ('integration_id', '=', self.integration.id),
        ])

        for mapping in language_mappings:
            external_code = mapping.external_language_id.code
            odoo_code = mapping.language_id.code

            # Skip primary locale
            if external_code == primary_language_code:
                continue

            if not primary_value.strip():
                # If the primary value is empty, need remove translations for this field in shopify
                translations_to_remove.append(external_code)
                continue

            translated_value = getattr(
                self.odoo_obj.with_context(lang=odoo_code),
                odoo_field_name
            ) or ''

            if primary_value == translated_value:
                # If the translated value is the same as the primary value,
                # need remove translation for this locale in shopify
                translations_to_remove.append(external_code)
                continue

            translations_to_update.append({
                'locale': external_code,
                'value': translated_value,
            })

        return (
            {ecommerce_field_name: translations_to_update},
            {ecommerce_field_name: translations_to_remove}
        )

    def convert_translated_field_to_integration_format(self, field_name):
        primary_language_code = self.adapter.lang
        language = self.env['res.lang'].from_external(self.integration, primary_language_code)

        return getattr(self.odoo_obj.with_context(lang=language.code), field_name)

    def _get_simple_value(self, ecommerce_field):
        result = super(SendFieldsShopify, self)._get_simple_value(ecommerce_field)

        field_name = result and list(result.keys())[0] or ''

        # Handle Shopify metafields
        if field_name.startswith(f'{METAFIELDS_NAME}.'):
            if not ecommerce_field.shopify_metafield_type:
                raise ApiExportError(_(
                    'To export the metafield "%s", the "namespace" and "type" must be specified. '
                    'Please, go to "E-Commerce Integrations → Product Fields → All Product Fields" '
                    'and ensure these fields are filled in. Refer to Shopify '
                    'Settings → Custom Data → Products for guidance.'
                ) % field_name)

            # Parse the metafield components
            try:
                __, namespace, key = field_name.split('.')
            except ValueError:
                raise ApiExportError(_(
                    'The metafield "%s" has an invalid format. It must follow the structure '
                    '"%s.<Namespace>.<Key>".' % (field_name, METAFIELDS_NAME)
                ))

            # Construct the metafield value
            meta_value = {
                'key': key,
                'value': result[field_name],
                'namespace': namespace,
                'type': ecommerce_field.shopify_metafield_type,
            }
            result[field_name] = meta_value

        return result

    def _update_calculated_fields(self, vals, field_values):
        for field_name, field_value in field_values.items():
            if field_name.startswith(f'{METAFIELDS_NAME}.'):
                field_name = METAFIELDS_NAME
                field_value = vals.get(METAFIELDS_NAME, []) + [field_value]

            vals[field_name] = field_value

        return vals

    def _prepare_simple_value(self, ecommerce_field, odoo_value):
        field_name = ecommerce_field.technical_name

        if not field_name.startswith(f'{METAFIELDS_NAME}.'):
            return super()._prepare_simple_value(ecommerce_field, odoo_value)

        metafield_type = ecommerce_field.shopify_metafield_type
        odoo_field_type = ecommerce_field.odoo_field_id.ttype

        # Process metafields with Date and Datetime types. If corresponding Odoo fields have
        # Date or Datetime types, we need to convert the value to the string format.
        if metafield_type == 'date' and odoo_field_type in ('date', 'datetime'):
            return odoo_value and odoo_value.strftime('%Y-%m-%d')

        if metafield_type == 'date_time' and odoo_field_type in ('date', 'datetime'):
            return odoo_value and odoo_value.strftime('%Y-%m-%dT%H:%M:%SZ')

        return odoo_value
