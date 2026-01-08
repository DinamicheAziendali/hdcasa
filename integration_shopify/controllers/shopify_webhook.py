#  See LICENSE file for full copyright and licensing details.

import json
import hmac
import base64
from hashlib import sha256
import logging
from werkzeug.wrappers import Response

from odoo.http import Controller, route, request
from odoo.addons.integration.controllers.integration_webhook import IntegrationWebhook
from odoo.addons.integration.controllers.utils import build_environment, validate_integration, with_webhook_context

from ..shopify_api import SHOPIFY
from ..shopify.shopify_order import ShopifyOrder, serialize_fulfillment


_logger = logging.getLogger(__name__)


class ShopifyWebhook(Controller, IntegrationWebhook):

    _kwargs = {
        'type': 'json',
        'auth': 'none',
        'methods': ['POST'],
        'csrf': False,
    }

    """
    headers = {
        X-Forwarded-Host: ventor-dev-integration-webhooks-test-15-main-5524588.dev.odoo.com
        X-Forwarded-For: 34.133.113.228
        X-Forwarded-Proto: https
        X-Real-Ip: 34.133.113.228
        Connection: close
        Content-Length: 1757
        User-Agent: Shopify-Captain-Hook
        Accept: */*
        Accept-Encoding: gzip;q=1.0,deflate;q=0.6,identity;q=0.3
        Content-Type: application/json
        X-Shopify-Api-Version: 2022-04
        X-Shopify-Hmac-Sha256: jgX3NMnUpTwfDuFXr0ufE//LiH1K+IGwd26+hy0wVik=
        X-Shopify-Product-Id: 8060197470448
        X-Shopify-Shop-Domain: vendevstore.myshopify.com
        X-Shopify-Topic: orders/paid
        X-Shopify-Webhook-Id: e62018d6-92e0-43a3-a96c-f74404f5bd15
    }
    """

    SHOP_NAME = 'X-Shopify-Shop-Domain'
    TOPIC_NAME = 'X-Shopify-Topic'

    @property
    def integration_type(self):
        return SHOPIFY

    def _check_webhook_digital_sign(self, integration):
        # https://shopify.dev/apps/webhooks/configuration/https#verify-a-webhook
        headers = self._get_headers()
        hmac_header = headers.get('X-Shopify-Hmac-Sha256')

        post_data = self._get_post_data()
        api_secret_key = integration.get_settings_value('secret_key')
        data = json.dumps(post_data).encode('utf-8')

        digest = hmac.new(api_secret_key.encode('utf-8'), data, digestmod=sha256).digest()
        computed_hmac = base64.b64encode(digest)

        result = hmac.compare_digest(computed_hmac, hmac_header.encode('utf-8'))  # TODO
        _logger.info('Shopify webhook digital sign: %s', result)
        return True

    def _get_hook_name_method(self):
        headers = self._get_headers()
        topic = headers[self.TOPIC_NAME]
        return '_'.join(topic.split('/'))

    def _get_essential_headers(self):
        return [
            self.SHOP_NAME,
            self.TOPIC_NAME,
            'X-Shopify-Hmac-Sha256',
        ]

    def _get_events_mapping(self):
        return {
            'orders/create': '_process_create_order',
            'orders/paid': '_process_pay_order',
            'orders/partially_fulfilled': '_process_partially_fulfill_order',
            'orders/fulfilled': '_process_fulfill_order',
            'orders/cancelled': '_process_cancel_order',
            'products/create': '_process_create_product',
            'products/update': '_process_update_product',
            'products/delete': '_process_delete_product',
        }

    # Handle orders
    @route(f'/<string:dbname>/integration/{SHOPIFY}/<int:integration_id>/orders', **_kwargs)
    @build_environment
    @validate_integration
    def shopify_receive_orders(self, *args, **kw):
        """
        Expected methods:
            orders/create
            orders/paid
            orders/cancelled
            orders/fulfilled
            orders/partially_fulfilled
        """
        _logger.info('Call shopify webhook controller method: shopify_receive_orders()')
        integration = request.env['sale.integration'].browse(kw['integration_id'])
        external_order_id = self._get_value_from_post_data('id')
        return self._process_event(integration, external_order_id)

    def _prepare_pipeline_data(self):
        post_data = self._get_post_data()
        vals = {
            'external_tags': ShopifyOrder._parse_tags(post_data),
            'payment_method': ShopifyOrder._parse_payment_code(post_data),
            'integration_workflow_states': ShopifyOrder._parse_workflow_states(post_data),
            'order_fulfillments': [serialize_fulfillment(x) for x in post_data['fulfillments']],
            'date_order': post_data.get('created_at'),
        }
        return vals

    @with_webhook_context
    def _process_cancel_order(self, integration, external_order_id):
        _logger.info(f'Call {integration.name} webhook controller: _process_cancel_order')
        data = self._prepare_pipeline_data()

        # Handle order existence check
        should_import, response = self._handle_missing_order(
            integration, external_order_id, data
        )
        if should_import is not None:
            return response

        # Order exists, proceed with cancel logic
        integration.cancel_order_by_id_with_delay(external_order_id, data)
        return Response(f'Job created for order with code={external_order_id}. Action: cancel order')

    @with_webhook_context
    def _process_pay_order(self, integration, external_order_id):
        _logger.info(f'Call {integration.name} webhook controller method: _process_pay_order')
        data = self._prepare_pipeline_data()

        # Handle order existence check
        should_import, response = self._handle_missing_order(
            integration, external_order_id, data
        )
        if should_import is not None:
            return response

        # Order exists, proceed with pay order processing
        integration.process_pipeline_by_id_with_delay(external_order_id, data, build_and_run=True)
        return Response(f'Job created for order with code={external_order_id}. Action: process pay order')

    @with_webhook_context
    def _process_fulfill_order(self, integration, external_order_id):
        _logger.info(f'Call {integration.name} webhook controller method: _process_fulfill_order')
        data = self._prepare_pipeline_data()

        # Handle order existence check
        should_import, response = self._handle_missing_order(
            integration, external_order_id, data
        )
        if should_import is not None:
            return response

        # Order exists, proceed with fulfill order processing
        integration.process_pipeline_by_id_with_delay(external_order_id, data, build_and_run=True)
        return Response(f'Job created for order with code={external_order_id}. Action: process fulfill order')

    @with_webhook_context
    def _process_partially_fulfill_order(self, integration, external_order_id):
        _logger.info(f'Call {integration.name} webhook controller method: _process_partially_fulfill_order')
        data = self._prepare_pipeline_data()

        # Handle order existence check
        should_import, response = self._handle_missing_order(
            integration, external_order_id, data
        )
        if should_import is not None:
            return response

        # Order exists, proceed with partially fulfill order processing
        integration.process_pipeline_by_id_with_delay(external_order_id, data, build_and_run=True)
        return Response(f'Job created for order with code={external_order_id}. Action: process partially fulfill order')

    # Handle products
    @route(f'/<string:dbname>/integration/{SHOPIFY}/<int:integration_id>/products', **_kwargs)
    @build_environment
    @validate_integration
    def shopify_receive_products(self, *args, **kw):
        """
        Expected methods:
            products/create
            products/update
            products/delete
        """
        _logger.info('Call shopify webhook controller method: shopify_receive_products()')
        integration = request.env['sale.integration'].browse(kw['integration_id'])
        external_product_id = self._get_value_from_post_data('id')
        return self._process_event(integration, external_product_id)

    def _get_product_name(self, integration):
        return self._get_value_from_post_data('title')
