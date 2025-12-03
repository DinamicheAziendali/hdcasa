# See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ResPartner(models.Model):
    _inherit = 'res.partner'

    subscribed_to_newsletter_presta = fields.Boolean(
        string='Subscribed to newsletter (Prestashop)',
        help='Check this box if the customer is subscribed to the newsletter.',
    )
    newsletter_registration_date_presta = fields.Datetime(
        string='Newsletter registration date (Prestashop)',
        help='Date when the customer registered to the newsletter.',
    )
    customer_registration_date_presta = fields.Datetime(
        string='Customer registration date (Prestashop)',
        help='Date when the customer registered to the website.',
    )
