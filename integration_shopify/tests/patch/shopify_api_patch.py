# See LICENSE file for full copyright and licensing details.

import json

from .storage import STORAGE_STR
from ...shopify_api import ShopifyAPIClient
from ...shopify.shopify_client import Client
from ...shopify.graphql_client import ShopifyGraphQL


SITE_NAME = 'shopifytestsite'
STORAGE = json.loads(STORAGE_STR)


class ApiVersionPatchTest:

    def __init__(self):
        self.name = '2024-07'


class SessionPatchTest:

    def __init__(self):
        self.url = f'{SITE_NAME}.myshopify.com'
        self.site = f'https://{SITE_NAME}.myshopify.com/admin'
        self.token = 'shpat_blablablablablablabla'
        self.api_version = ApiVersionPatchTest()


class ShopPatch:

    def __init__(self) -> None:
        self.name = SITE_NAME
        self.weight_unit = 'kg'
        self.primary_location_id = 10000000001


class ShopifyClientPatchTest(Client):

    def __init__(self, settings):
        self._session = SessionPatchTest()
        self.shop = ShopPatch()

        self.country = 'PL'
        self.primary_locale = 'en'

    def _save(self, record):
        """TODO"""
        return record

    def _apply(self, name, *args):
        """TODO"""
        shopify_cls = self._model(name)
        return shopify_cls

    def _destroy(self, record):
        """TODO"""
        return record

    def _refresh(self, record):
        """TODO"""
        return record

    def _fetch_one(self, name, record_id, fields):
        shopify_cls = self._model_init(name)

        record_dict = STORAGE[name][str(record_id)]
        shopify_cls._update(record_dict)

        return shopify_cls

    def _fetch_multi(self, name, params, fields, quantity):
        """TODO"""
        if 'order_id' in params:
            return [self._fetch_one(name, params['order_id'], fields)]

        return [self._model(name)]

    def _get_admin_url(self):
        return f'https://{SITE_NAME}.myshopify.com/admin'

    def _get_access_scope(self):
        return [
            'write_fulfillments',
            'read_fulfillments',
            'write_inventory',
            'read_inventory',
            'read_orders',
            'write_products',
            'read_products',
            'write_orders',
            'write_merchant_managed_fulfillment_orders',
            'read_merchant_managed_fulfillment_orders',
            'read_customers',
            'write_locations',
            'read_locations',
            'read_shipping',
            'write_shipping',
            'read_publications',
            'read_all_orders',
            'unauthenticated_write_customers',
            'unauthenticated_read_customers',
            'unauthenticated_read_customer_tags',
        ]


class ShopifyGraphQLPatchTest(ShopifyGraphQL):

    def execute(self, *args, **kw):
        """TODO"""
        return {}

    def get_orders_ids_query(self, order_ids: list):
        return [
            {
                'node': {
                    'id': f'gid://shopify/Order/{order_id}',
                    'publication': {'id': 'gid://shopify/Publication/100500'},
                },
            }
            for order_id in order_ids
        ]


class ShopifyAPIClientPatchTest(ShopifyAPIClient):

    def __init__(self, settings):
        self._integration_id = None
        self._integration_name = None
        self._settings = settings

        self._client = ShopifyClientPatchTest(settings)
        self._graphql = ShopifyGraphQLPatchTest(
            site=self._client._session.site.rsplit('/', maxsplit=1)[0] + '/'
            + settings['fields']['graphql_version']['value'],
            token=self._client._session.token,
        )
        self.country = self._client.country
        self.lang = self._client.primary_locale

        self.location_id = self._client._get_location_id()
        self.access_scopes = self._client._get_access_scope()
        self.admin_url = self._client._get_admin_url()
        self._weight_uom = self._client._get_weight_uom()

    def get_payment_methods(self):
        return [
            {'id': 'shopify-payment-bogus', 'name': 'bogus'},
            {'id': 'shopify-payment-manual_in_shopify_test', 'name': 'manual_in_shopify_test'},
            {'id': 'shopify-payment-gift_card', 'name': 'gift_card'},
            {'id': 'shopify-payment-Not_Defined', 'name': 'shopify-payment-Not_Defined'},
        ]
