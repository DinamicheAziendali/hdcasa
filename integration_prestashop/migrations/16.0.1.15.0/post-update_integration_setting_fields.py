# See LICENSE file for full copyright and licensing details.

from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    integrations = env['sale.integration'].search([
        ('type_api', '=', 'prestashop'),
    ])

    for rec in integrations:
        api_fields = rec.field_ids.filtered(lambda x: x.name == 'decimal_precision')

        for fld in api_fields:
            fld.value = '6'
