# See LICENSE file for full copyright and licensing details.

from odoo import api, SUPERUSER_ID

NEW_FIELDS = [
    ('prestashop_ecommerce_field_template_weight', 'prestashop_ecommerce_field_variant_weight'),
    ('prestashop_ecommerce_field_template_reference', 'prestashop_ecommerce_field_reference'),
    ('prestashop_ecommerce_field_template_barcode', 'prestashop_ecommerce_field_barcode'),
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['product.ecommerce.field.mapping'].add_mapping_using_another_field('prestashop', NEW_FIELDS)
