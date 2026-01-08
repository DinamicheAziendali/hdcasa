# See LICENSE file for full copyright and licensing details.
from datetime import datetime, date

from odoo import fields
from odoo.exceptions import ValidationError

from odoo.addons.integration.models.fields import ReceiveFields

from ...shopify_api import METAFIELDS_NAME


class ReceiveFieldsShopify(ReceiveFields):

    def _get_value(self, field_name):
        if not field_name.startswith(f'{METAFIELDS_NAME}.'):
            return getattr(self.external_obj, field_name, None)

        meta_fields = self.external_obj.metafields()
        __, namespace, key = field_name.split('.')

        meta_field = list(filter(
            lambda x: x.key == key and x.namespace == namespace, meta_fields))
        value = meta_field and meta_field[0].value or None
        return value

    def _prepare_simple_value(self, ecommerce_field, ext_value):
        field_name = ecommerce_field.technical_name

        if not field_name.startswith(f'{METAFIELDS_NAME}.'):
            return super()._prepare_simple_value(ecommerce_field, ext_value)

        metafield_type = ecommerce_field.shopify_metafield_type
        odoo_field_type = ecommerce_field.odoo_field_id.ttype

        if not ext_value:
            return None

        # Process metafields with Date and Datetime types. If corresponding Odoo fields have
        # Date or Datetime types, we need to convert the value to the string format.
        if metafield_type == 'date':
            # For metafields of type "date", the expected format is "YYYY-MM-DD".
            try:
                # Using date.fromisoformat here is fine because it validates the "YYYY-MM-DD" format.
                parsed_date = date.fromisoformat(ext_value)
            except ValueError:
                raise ValidationError(
                    f'Date metafield "{field_name}" has incorrect date format: "{ext_value}"'
                    ' (expected format: "YYYY-MM-DD")'
                )

            if odoo_field_type == 'date':
                return fields.Date.to_date(parsed_date)

            if odoo_field_type == 'datetime':
                # Odoo datetime field: combine the date with midnight (00:00:00) to form a datetime.
                datetime_value = datetime.combine(parsed_date, datetime.min.time())
                return fields.Datetime.to_datetime(datetime_value)

        elif metafield_type == 'date_time':
            # For metafields of type "date_time", the expected format is "YYYY-MM-DDTHH:MM:SSZ".
            try:
                # We use datetime.strptime instead of datetime.fromisoformat because fromisoformat does not
                # support the "Z" suffix, which indicates UTC
                parsed_datetime = datetime.strptime(ext_value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                raise ValidationError(
                    f'Datetime metafield "{field_name}" has incorrect datetime format: "{ext_value}"'
                    ' (expected format: "YYYY-MM-DDTHH:MM:SSZ")'
                )

            if odoo_field_type == 'datetime':
                return fields.Datetime.to_datetime(parsed_datetime)

            if odoo_field_type == 'date':
                return fields.Date.to_date(parsed_datetime.date())

        return super()._prepare_simple_value(ecommerce_field, ext_value)
