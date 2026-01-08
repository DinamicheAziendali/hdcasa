# See LICENSE file for full copyright and licensing details.

from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    integrations = env['sale.integration'].search([
        ('type_api', '=', 'shopify'),
    ])

    for integation in integrations:
        pricelist_sale_price = env.ref('integration_shopify.shopify_ecommerce_field_variant_pricelist_sale_price')

        env['product.ecommerce.field.mapping'].create({
            'active': False,
            'integration_id': integation.id,
            'ecommerce_field_id': pricelist_sale_price.id,
        })
