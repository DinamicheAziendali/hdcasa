# See LICENSE file for full copyright and licensing details.

from typing import Dict

from odoo import api, models


class IntegrationSaleOrderFactory(models.AbstractModel):
    _inherit = 'integration.sale.order.factory'

    @api.model
    def _prepare_order_vals(self, integration, order_data):
        res = super(IntegrationSaleOrderFactory, self) \
            ._prepare_order_vals(integration, order_data)

        if integration.is_shopify():
            external_location_id = order_data.get('external_location_id')

            if external_location_id:
                warehouse = integration._get_wh_from_external_location(external_location_id)
                if warehouse:
                    res['warehouse_id'] = warehouse.id

            channel_id = order_data.get('channel_id')
            if channel_id:
                SaleChannel = self.env['external.sale.channel']

                # Shopify GraphQL API doesn't return some custom channels for unknown reason.
                # To avoid possible order import errors we don't want to raise errors if
                # sale channel was not found.
                sale_channel = SaleChannel.get_record(
                    integration.id,
                    channel_id,
                    raise_error=False,
                )

                if not sale_channel:
                    channel_name = order_data.get('channel_name') or f'Sales Channel {channel_id}'
                    sale_channel = SaleChannel.create({
                        'integration_id': integration.id,
                        'external_id': channel_id,
                        'name': channel_name,
                    })

                res['integration_sale_channel_id'] = sale_channel.id

            source_name = order_data.get('order_source_name')
            if source_name:
                OrderSourceName = self.env['external.order.source.name']

                order_source_name = OrderSourceName.get_or_create(integration.id, source_name)

                res['integration_order_source_name_id'] = order_source_name.id

        return res

    def _prepare_order_line_vals(self, integration, line):
        res = super(IntegrationSaleOrderFactory, self)._prepare_order_line_vals(integration, line)

        if integration.is_shopify():
            external_location_id = line.get('external_location_id')

            if external_location_id:
                warehouse = integration._get_wh_from_external_location(external_location_id)
                if warehouse:
                    res['warehouse_id'] = warehouse.id

        return res

    @api.model
    def _create_order(self, integration, order_data):
        """
        Override to create a sale order.
        """
        order = super(IntegrationSaleOrderFactory, self)._create_order(integration, order_data)

        if integration.is_shopify():
            payment_methods = self.env['sale.order.payment.method']
            for payment_method_data in order_data['payment_methods']:
                payment_methods |= self._get_payment_method(integration, payment_method_data)

            if payment_methods:
                order.write({'payment_method_ids': [(6, 0, payment_methods.ids)]})

        return order

    def _post_create_order(self, integration: models.Model, order: models.Model, order_data: Dict):
        """
        Update order fields based on meta field mappings from the integration.
        """
        super(IntegrationSaleOrderFactory, self)._post_create_order(integration, order, order_data)

        if not integration.is_shopify():
            return order

        metafield_mappings = integration.order_metafield_mapping_ids

        if not metafield_mappings:
            return order

        # Retrieve meta fields associated with the order
        order_metafields = integration.get_object_metafields('order', order_data['id'])

        if not order_metafields:
            return order

        vals = {}
        for mapping in metafield_mappings:

            for order_metafield in order_metafields:
                if order_metafield.get('key') == mapping.metafield_key:
                    metafield_value = order_metafield.get('value')

                    if mapping.metafield_type == 'boolean':
                        metafield_value = True if metafield_value == 'true' else False

                    vals[mapping.odoo_field_id.name] = metafield_value
                    break

        if vals:
            order.write(vals)

        return order
