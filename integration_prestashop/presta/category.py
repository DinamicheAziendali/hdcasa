#  See LICENSE file for full copyright and licensing details.

from .base_model import BaseModel, PRESTASHOP
from odoo.addons.integration.tools import IS_TRUE

import re


class Category(BaseModel):
    """Prestashop product-category (product.public.category)"""

    def create(self, vals):
        categories = self._client.get('categories', options={'schema': 'blank'})

        categories['category']['active'] = IS_TRUE
        BaseModel._fill_translated_field(vals['name'], categories['category']['name'])

        BaseModel._fill_translated_field(
            vals['name'],
            categories['category']['link_rewrite'],
            convert=self._name_to_link_rewrite
        )

        if vals['parent_id']:
            categories['category']['id_parent'] = vals['parent_id']

        result = self._client.add('categories', categories)
        category_id = result[PRESTASHOP]['category']['id']

        return self.get(category_id)

    def _name_to_link_rewrite(self, name):
        name = name.lower()
        name = re.sub(r'\s+', '-', name)
        name = re.sub(r'[^a-z0-9-_]', '', name)
        return name
