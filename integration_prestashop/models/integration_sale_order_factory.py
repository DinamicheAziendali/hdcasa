# See LICENSE file for full copyright and licensing details.

from datetime import datetime

from odoo.addons.integration.models.sale_integration import DATETIME_FORMAT
from odoo.addons.integration.tools import IS_TRUE
from odoo import models, api


class IntegrationSaleOrderFactory(models.AbstractModel):
    _inherit = 'integration.sale.order.factory'

    @api.model
    def _fetch_odoo_partner(self, integration, partner_data, address_type=None, parent=False):
        partner = super(IntegrationSaleOrderFactory, self)._fetch_odoo_partner(
            integration, partner_data, address_type, parent,
        )

        if integration.is_prestashop():
            vals = dict()
            integration_su = integration.sudo()
            if 'newsletter' in partner_data:
                subscribed_to_newsletter_field = integration_su.subscribed_to_newsletter_id
                if subscribed_to_newsletter_field:
                    newsletter_subscription_status = partner_data.get('newsletter') == IS_TRUE
                    vals[subscribed_to_newsletter_field.name] = newsletter_subscription_status

            if 'newsletter_date_add' in partner_data:
                newsletter_registration_date_field = integration_su.newsletter_registration_date_id
                if newsletter_registration_date_field:
                    try:
                        newsletter_registration_date = datetime.strptime(
                            partner_data['newsletter_date_add'], DATETIME_FORMAT)
                    except ValueError:
                        newsletter_registration_date = False
                    vals[newsletter_registration_date_field.name] = newsletter_registration_date

            if 'customer_date_add' in partner_data:
                customer_registration_date_field = integration_su.customer_registration_date_id
                if customer_registration_date_field:
                    try:
                        customer_registration_date = datetime.strptime(
                            partner_data['customer_date_add'], DATETIME_FORMAT)
                    except ValueError:
                        customer_registration_date = False
                    vals[customer_registration_date_field.name] = customer_registration_date

            other_value = partner_data.get('other')
            if other_value:
                additional_address_information_field = integration_su.additional_address_information
                if additional_address_information_field:
                    vals[additional_address_information_field.name] = other_value

            if vals:
                partner.write(vals)

        return partner
