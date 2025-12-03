# See LICENSE file for full copyright and licensing details.

from odoo.addons.integration.models.fields.common_fields import BOOLEAN_FIELDS, FLOAT_FIELDS
from odoo.addons.integration.models.fields import SendFields
from ...prestashop_api import IS_TRUE, IS_FALSE
from ...presta.base_model import BaseModel


class SendFieldsPresta(SendFields):

    def convert_translated_field_to_integration_format(self, field_name):
        language_mappings = self.env['integration.res.lang.mapping'].search([
            ('integration_id', '=', self.integration.id)
        ])

        translations = {}
        for language_mapping in language_mappings:
            external_code = language_mapping.external_language_id.code
            odoo_code = language_mapping.language_id.code
            translations[external_code] = getattr(
                self.odoo_obj.with_context(lang=odoo_code), field_name)

        return {'language': translations}

    def _prepare_simple_value(self, field_name, field_type, odoo_value):
        if field_type in BOOLEAN_FIELDS:
            return IS_TRUE if odoo_value else IS_FALSE
        if field_type in FLOAT_FIELDS:
            return round(odoo_value, BaseModel.PRESTASHOP_PRECISION)

        return super()._prepare_simple_value(field_name, field_type, odoo_value)
