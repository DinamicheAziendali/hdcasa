#  See LICENSE file for full copyright and licensing details.

import logging

from prestapyt import PrestaShopWebServiceDict
from requests import Session

from .base_model import BaseModel
from .category import Category
from .product import Product
from .combination import Combination
from .image import Image


_logger = logging.getLogger(__name__)


class Client(PrestaShopWebServiceDict):

    default_language_id = None
    id_group_shop = None
    data_block_size = None
    shop_ids = []

    classes = {
        'category': Category,
        'product': Product,
        'combination': Combination,
        'image': Image,
    }

    def __init__(self, api_url, api_key):
        session = Session()
        add_headers = self._get_add_headers()
        session.headers.update(add_headers)
        super(Client, self).__init__(api_url=api_url, api_key=api_key, session=session)

    def _get_add_headers(self):
        headers = {
            'User-Agent': 'VentorTech-Odoo-Prestashop-Connector-PRO/1.X',
        }
        return headers

    def add(self, resource, content=None, files=None, options=None):
        _logger.debug(
            'add() resource=%s, content=%s, files=%s, options=%s',
            resource,
            content,
            files,
            options,
        )
        return super(Client, self).add(resource, content, files, options)

    def edit(self, resource, content, options=None):
        _logger.debug(
            'edit() resource=%s, content=%s, options=%s',
            resource,
            content,
            options,
        )
        return super(Client, self).edit(resource, content, options)

    def model(self, name):
        cls = self.classes.get(name)
        if not cls:
            cls = BaseModel

        instance = cls(
            self,
            id_group_shop=self.id_group_shop,
            shop_ids=self.shop_ids,
            default_language_id=self.default_language_id,
            data_block_size=self.data_block_size,
        )
        instance._name = name  # TODO: bad

        return instance
