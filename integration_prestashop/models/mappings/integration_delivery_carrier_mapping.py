# See LICENSE file for full copyright and licensing details.

from odoo import models


class IntegrationDeliveryCarrierMapping(models.Model):
    _inherit = 'integration.delivery.carrier.mapping'

    def _fix_unmapped_by_search(self):
        if self.integration_id.is_prestashop():
            self._fix_shipping_from_parent()

        return super(IntegrationDeliveryCarrierMapping, self)._fix_unmapped_by_search()

    def _fix_shipping_from_parent(self):
        self.ensure_one()

        integration = self.integration_id
        adapter = integration._build_adapter()
        prestashop_data = adapter.get_parent_delivery_methods(
            self.external_carrier_id.code,
        )
        id_list = [data['id'] for data in prestashop_data]

        mapped_method = self.search(
            [
                ('carrier_id', '!=', False),
                ('integration_id', '=', integration.id),
                ('external_carrier_id.code', 'in', id_list),
            ], order='id desc', limit=1)

        if mapped_method:
            self.carrier_id = mapped_method.carrier_id.id

        return mapped_method.carrier_id
