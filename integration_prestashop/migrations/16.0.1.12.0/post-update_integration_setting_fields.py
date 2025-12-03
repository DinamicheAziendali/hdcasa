# See LICENSE file for full copyright and licensing details.

from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    integrations = env['sale.integration'].search([
        ('type_api', '=', 'prestashop'),
    ])

    for rec in integrations:
        order_fields = rec.field_ids.filtered(lambda x: x.name == 'receive_orders_filter')

        for fld in order_fields:
            value = fld.value
            fld.value = value.replace(
                'presta_last_receive_orders_datetime',
                'last_receive_orders_datetime_str',
            )
