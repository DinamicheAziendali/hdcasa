#  See LICENSE file for full copyright and licensing details.

from prestapyt import PrestaShopWebServiceError

from .base_model import BaseModel
from .image import Image


class Product(BaseModel):
    """Prestashop product (product.template)"""

    _name = 'product'
    _required_fields = [
        'name',
    ]

    _skip_if_absent_in_schema = [
        'state',
    ]

    def _save(self, vals):
        is_update = bool(self.id)
        result = super()._save(vals)
        if is_update:
            self._thirtybees_forcibly_update_price_per_shop(vals)
        return result

    def _thirtybees_forcibly_update_price_per_shop(self, vals):
        # Some bug on Thirtybees doesn't allow us update price when id_group_shop
        # is set. It just doesn't save price at all. But it works ok with PrestaShop.
        # So we update price per shop as workaround
        for shop_id in self._shop_ids:
            self._client.edit('products', vals, options={'id_shop': shop_id})

    def _prepare_save_vals(self):
        categories_to_update = self._to_update.pop('categories', None)
        product_bundle_to_update = self._to_update.pop('product_bundle', None)
        product_features_to_update = self._to_update.pop('product_features', None)
        product_relations_to_update = self._to_update.pop('accessories', None)

        product_schema = super()._prepare_save_vals()

        if categories_to_update is not None:
            categories = product_schema['product']['associations']['categories']
            categories['category'] = [{'id': str(x)} for x in categories_to_update._ids]

            # otherwise error Undefined index # TODO: common logic
            if 'value' in categories:
                del categories['value']

        if product_bundle_to_update is not None:
            product_bundle = product_schema['product']['associations']['product_bundle']
            product_bundle['product'] = product_bundle_to_update

            # otherwise error Undefined index # TODO: common logic
            if 'value' in product_bundle:
                del product_bundle['value']

        if product_features_to_update is not None:
            feature_lines = product_schema['product']['associations']['product_features']
            feature_lines['product_feature'] = product_features_to_update

            # otherwise error Undefined index # TODO: common logic
            if 'value' in feature_lines:
                del feature_lines['value']

        if product_relations_to_update is not None:
            product_relation = product_schema['product']['associations']['accessories']
            product_relation['product'] = product_relations_to_update

            # otherwise error Undefined index # TODO: common logic
            if 'value' in product_relation:
                del product_relation['value']

        self._remove_fields(product_schema)
        self._remove_service_not_compatible_fields(product_schema)

        return product_schema

    def get_combination(self, external_id):
        external_id = int(external_id)
        combinations = self.get_combinations()

        if external_id not in combinations.ids:
            return self._client.model('combination')
        return list(filter(lambda x: x.id == external_id, combinations))[0]

    def get_combinations(self):
        combinations = self._client.model('combination').search({
            'id_product': self.id,
        })
        return combinations

    def add_combination(self, combination):
        # TODO: product_id!!!
        combination._product_id = self.id  # TODO: refactor
        combination.save()
        return combination

    def _remove_fields(self, product):
        fields = [
            'manufacturer_name',
            'quantity',
            'position_in_category',
        ]
        for field in fields:
            try:
                del product['product'][field]
            except KeyError:
                pass

    def _remove_service_not_compatible_fields(self, product):
        if self._get_value(product, 'type') == 'virtual':
            try:
                del product['product']['associations']['product_bundle']
            except KeyError:
                pass

    def _get_value(self, product, name):
        value = product['product'][name]
        if isinstance(value, dict):
            value = value['value']

        return value

    def get_images(self):  # todo: refactor
        product_images_full_url = (
            self._client._api_url
            + 'images/products/'
            + str(self.id)
        )

        if self._id_group_shop:
            product_images_full_url += '?id_group_shop=%s' % self._id_group_shop

        try:
            response = self._client.get_with_url(product_images_full_url)
            declination_list = response['image']['declination']
            if not isinstance(declination_list, list):
                declination_list = [declination_list]
            declination_ids = [x['attrs']['id'] for x in declination_list]
        except PrestaShopWebServiceError:
            declination_ids = []

        result = []
        for declination_id in declination_ids:
            image = Image(
                self._client, self._id_group_shop, self._shop_ids
            ).get(declination_id)
            image._product_id = self.id
            result.append(image)

        return result

    def add_image(self, data):
        new_image = Image(self._client, self._id_group_shop, self._shop_ids)
        new_image._product_id = self.id  # todo: bad
        return new_image.create('image.jpg', data)  # todo: image name
