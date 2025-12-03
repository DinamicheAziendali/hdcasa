#  See LICENSE file for full copyright and licensing details.

import logging

from odoo.http import Controller, route, request
from odoo.addons.integration.controllers.integration_webhook import IntegrationWebhook
from odoo.addons.integration.controllers.utils import build_environment, validate_integration

from ..prestashop_api import PRESTASHOP


_logger = logging.getLogger(__name__)


class PrestashopWebhook(Controller, IntegrationWebhook):

    _kwargs = {
        'type': 'json',
        'auth': 'none',
        'methods': ['POST'],
        'csrf': False,
    }

    """
    headers = {
        X-Forwarded-Host: ventor-dev-integration-webhooks-test-15.odoo.com
        X-Forwarded-For: 141.95.36.76
        X-Forwarded-Proto: https
        X-Real-Ip: 141.95.36.76
        Connection: close
        Content-Length: 11369
        User-Agent: Httpful/0.2.20 (cURL/7.64.0 PHP/7.3.27-9+0~20210227.82+debian10~1.gbpa4a3d6
                    (Linux) Apache/2.4.38 (Debian) Mozilla/5.0 (X11; Linux x86_64)
                    AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36)
        Content-Type: application/json
        Accept: */*; q=0.5, text/plain; q=0.8, text/html;level=3;
        X-Secure-Key: GHDGBKC15DIDXMXFXZHYUWXBPAEOGEAT
        X-Hook: actionProductUpdate
    }
    """

    SHOP_NAME = 'X-Forwarded-Host'  # This header skipped in redefined method `get_shop_domain`
    TOPIC_NAME = 'X-Hook'
    NEW_ORDER_METHOD_NAME = 'actionValidateOrder'

    @property
    def integration_type(self):
        return PRESTASHOP

    @route(f'/<string:dbname>/integration/{PRESTASHOP}/<int:integration_id>/orders', **_kwargs)
    @build_environment
    @validate_integration
    def prestashop_receive_orders(self, *args, **kw):
        """
        Expected methods:
            actionOrderHistoryAddAfter (Order Status Updated)
            actionValidateOrder (Order Created)
        """
        _logger.info('Call prestashop webhook controller: prestashop_receive_orders()')
        integration = request.env['sale.integration'].browse(kw['integration_id'])
        return self._receive_order_generic(integration)

    def actionValidateOrder(self, integration, external_order_id):
        """Order Created"""
        _logger.info('Call prestashop webhook controller: actionValidateOrder()')
        return integration.integration_receive_orders_cron(cron_operation=False)  # TODO: by ID

    def actionOrderHistoryAddAfter(self, input_file):
        """Order Status Updated"""
        _logger.info('Call prestashop webhook controller actionOrderHistoryAddAfter()')

        integration = input_file.si_id
        status_code = self._get_value_from_post_data('current_state')
        sub_status_id, __ = self._get_order_sub_status_tuple(integration, status_code)

        if not sub_status_id:
            _logger.info('Prestashop webhook: external sub-status not recognized.')
            return False

        data = self._prepare_pipeline_data()
        if sub_status_id == integration.sub_status_cancel_id:  # TODO: unnecessary `if`
            return input_file._run_cancel_order(data)

        return input_file._build_and_run_order_pipeline(data)

    def get_shop_domain(self, integration):
        # TODO: now it's just a `stub`.
        # Method returns what the `webhook validator` expects for.
        # We need a header kind of the shopify `X-Shopify-Shop-Domain`
        return integration.get_settings_value('url')

    def _get_post_data(self):
        res = super(PrestashopWebhook, self)._get_post_data()
        return res.get('order', dict())

    def _prepare_pipeline_data(self):
        post_data = self._get_post_data()
        vals = {
            'payment_method': post_data['payment'],
            'integration_workflow_states': [post_data['current_state']],
        }
        return vals

    def _check_webhook_digital_sign(self, integration):
        return True  # TODO

    def _get_essential_headers(self):
        return [
            self.TOPIC_NAME,
            'X-Secure-Key',
        ]
