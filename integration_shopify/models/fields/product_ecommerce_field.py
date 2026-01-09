#  See LICENSE file for full copyright and licensing details.

from ...shopify_api import SHOPIFY, METAFIELDS_NAME
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class ProductEcommerceField(models.Model):
    _inherit = 'product.ecommerce.field'

    type_api = fields.Selection(
        selection_add=[(SHOPIFY, 'Shopify')],
        ondelete={
            SHOPIFY: 'cascade',
        },
    )

    shopify_metafield_type = fields.Selection(
        string=' Metafield Type',
        selection=[
            ('boolean', 'Boolean'),
            ('date', 'Date'),
            ('date_time', 'DateTime'),
            ('multi_line_text_field', 'Multi Line Text Field'),
            ('number_decimal', 'Number Decimal'),
            ('number_integer', 'Number Integer'),
            ('single_line_text_field', 'Single Line Text Field'),
        ]
    )

    @api.constrains('shopify_metafield_type', 'technical_name')
    def check_metafields(self):
        """
        Validates Shopify metafields to ensure they meet the required format and have a defined type.
        """
        for record in self:
            if record.type_api == SHOPIFY:
                api_name = record.technical_name
                # Skip validation if the metafield does not contain a dot
                if '.' not in api_name:
                    continue

                # Validate the metafield format
                parts = api_name.split('.')
                if len(parts) != 3 or not api_name.startswith(f'{METAFIELDS_NAME}.'):
                    raise ValidationError(_(
                        'Invalid Shopify Metafield format. The metafield must follow the format '
                        '"%s.<Namespace>.<Key>". Provided: "%s". Please ensure the technical name '
                        'matches this format and try again.'
                    ) % (METAFIELDS_NAME, api_name))

                # Ensure the metafield type is selected
                if not record.shopify_metafield_type:
                    raise ValidationError(_(
                        'Shopify Metafield "%s" is missing a type. Please select a Metafield Type '
                        'before proceeding.'
                    ) % api_name)
