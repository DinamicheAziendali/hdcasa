# See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import itertools
import logging
import re
import requests

from time import sleep
from copy import deepcopy
from collections import defaultdict
from dateutil import parser
from typing import List, Dict

from odoo import _
from odoo.addons.integration.api.abstract_apiclient import AbsApiClient
from odoo.addons.integration.models.fields.common_fields import GENERAL_GROUP
from odoo.addons.integration.tools import not_implemented, add_dynamic_kwargs, TemplateHub, ExternalImage, ProductType
from odoo.exceptions import UserError, ValidationError

from .shopify import Client, ShopifyGraphQL, check_scope
from .shopify.tools import merge_orders_data, parse_graphql_id, ExtractNode
from .shopify.exceptions import ShopifyApiException
from .shopify.shopify_client import (
    ORDER,
    TEMPLATE,
    VARIANT,
    IMAGE,
    COUNTRY,
    FULFILLMENT,
    FULFILLMENT_ORDER,
    COLLECT,
    CATEGORY,
    INVENT_LEVEL,
    WEBHOOK,
    CUSTOMER,
    TRANSACTION,
    SHOPIFY_FETCH_LIMIT,
)
from .shopify.shopify_helpers import ShopifyOrderStatus, ShopifyTxnStatus as Txn
from .shopify.shopify_client import METAFIELD, LOCATION
from .shopify.shopify_order import (
    ShopifyOrder,
    format_delivery_code,
    format_attr_code,
    format_attr_value_code,
    format_payment_code,
    serialize_fulfillment,
    serialize_transaction,
)


SHOPIFY = 'shopify'
ATTR_DEFAULT_TITLE = 'Title'  # Default product attribute name according to the Shopify API
ATTR_DEFAULT_VALUE = 'Default Title'  # Default product attribute value according to the Shopify API
METAFIELDS_NAME = 'metafields'
TRANSLATABLE_FIELDS = ['title', 'body_html']

_logger = logging.getLogger(__name__)


class ShopifyAPIClient(AbsApiClient):

    settings_fields = (
        ('url', 'Shop URL', ''),
        ('version', 'API Version', ''),
        ('key', 'Admin API access token', '', False, True),
        ('secret_key', 'API Secret Key', '', False, True),
        ('graphql_version', 'GraphQl Version', '2025-10'),
        ('language_id', 'Shop Language Code', ''),
        ('import_products_filter', 'Import Products Filter', '{"status": "active"}', True),
        (
            'receive_order_statuses',
            'Order statuses separated by comma',
            ShopifyOrderStatus.STATUS_OPEN,
        ),
        (
            'receive_order_financial_statuses',
            'Order financial statuses separated by comma',
            ShopifyOrderStatus.SPECIAL_STATUS_ANY,
        ),
        (
            'receive_order_fulfillment_statuses',
            'Order fulfillment statuses separated by comma',
            ShopifyOrderStatus.SPECIAL_STATUS_ANY,
        ),
        ('weight_uom', 'Shopify weight unit. '
                       'Will be automatically populated when integration is active', '',),
        ('decimal_precision', 'Number of decimal places in the price of the exported product', '2'),
        ('batch_size', 'Number of orders processed in one batch', '1000'),
        *AbsApiClient.settings_fields,
    )

    def __init__(self, settings):
        super().__init__(settings)

        self._client = Client(settings)
        self._graphql = ShopifyGraphQL(
            site=self._client._session.site.rsplit('/', maxsplit=1)[0] + '/'
            + settings['fields']['graphql_version']['value'],
            token=self._client._session.token,
        )
        self.country = self._client.shop.country
        self.lang = self._client.shop.primary_locale
        self.location_id = self._client._get_location_id()
        self.access_scopes = self._client._get_access_scope()
        self.admin_url = self._client._get_admin_url()
        self._weight_uom = self.get_settings_value('weight_uom')

    def deactivate_adapter(self):
        self._client.deactivate_session()

    def activate_adapter(self):
        self._client.activate_session()

    def check_connection(self):
        """TODO"""
        return True

    def get_api_resources(self):
        return

    def save(self, record):
        return self._client._save(record)

    def apply(self, name, *args):
        # Currently it used for the wrapping InventoryLevel `set` method
        return self._client._apply(name, *args)

    def destroy(self, record):
        return self._client._destroy(record)

    def refresh(self, record):
        return self._client._refresh(record)

    def model(self, name):
        return self._client._model(name)

    def model_init(self, name, **kw):
        return self._client._model_init(name, **kw)

    def fetch_one(self, name, record_id, fields=None):
        if not record_id:
            return self.model_init(name)

        result = self._client._fetch_one(name, record_id, fields)
        if not result:
            return self.model_init(name)

        return result

    def fetch_multi(self, name, params=None, fields=None, quantity=None):
        return self._client._fetch_multi(name, params, fields, quantity)

    def count(self, name):
        return self._client._model(name).count()

    def validate_template(self, template):
        _logger.info('Shopify "%s": validate_template()', self._integration_name)
        mappings_to_delete = []

        # (1) if template with such external id exists?
        shopify_product_id = template['external_id']
        if shopify_product_id:
            shopify_product = self.fetch_one(TEMPLATE, shopify_product_id)

            if shopify_product.is_new():
                mappings_to_delete.append({
                    'model': 'product.template',
                    'external_id': shopify_product_id,
                })

        # (2) if part of the variants has no external_id?
        mappings_to_update = self.parse_mappings_to_update(template['products'])

        # (3) if variant with such external id exists?
        for variant in template['products']:
            shopify_variant_id = self._parse_variant_id(variant['external_id'])
            if shopify_variant_id:
                shopify_variant = self.fetch_one(VARIANT, shopify_variant_id)

                if shopify_variant.is_new():
                    mappings_to_delete.append({
                        'model': 'product.product',
                        'external_id': variant['external_id'],
                    })

        return mappings_to_delete, mappings_to_update

    @add_dynamic_kwargs
    def find_existing_template(self, template, **kw):
        _logger.info('Shopify "%s": find_existing_template()', self._integration_name)

        # Skip search if the external ID is already mapped
        if template['external_id']:
            return False

        variants = template['products']
        integration = self._get_integration(kw)
        variant_reference = integration.variant_reference_api_name
        product_refs = [x['fields'].get(variant_reference) for x in variants]

        # Validate product references and ensure they belong to the same Shopify product
        ids_set = self._find_product_by_references(product_refs)(**kw)
        ids_set_product = [x[0] for x in ids_set if x[0]]

        if not ids_set_product:
            return False

        if len(set(ids_set_product)) > 1:
            error_message = _(
                'Product reference(s) "%s" were found in multiple Shopify Products: %s. '
                'This is not allowed as in Odoo, these references already belong to a single '
                'product template and its variants. Ensure the structure of products in Shopify '
                'matches the Odoo product template structure.'
            ) % (', '.join(product_refs), ', '.join(ids_set_product))
            raise UserError(error_message)

        shopify_product_id = ids_set_product[0]

        # Validate the number of variants between Odoo and Shopify
        product = self.fetch_one(TEMPLATE, shopify_product_id)
        product_combination_ids = product.variants
        if len(product_refs) != len(product_combination_ids):
            raise UserError(_(
                'The number of combinations in Shopify (%d) does not match Odoo (%d). '
                'Check the product with ID %s in Shopify and ensure the combination count matches '
                'the variant count in Odoo (Integration: "%s").'
            ) % (
                len(product_combination_ids),
                len(product_refs),
                shopify_product_id,
                self._integration_name,
            ))

        attribute_value_tmpl_ids = self._attribute_value_from_template(product)

        for combination in product_combination_ids:
            # Ensure reference is set for each combination
            reference = getattr(combination, variant_reference)

            if not reference:
                raise UserError(_(
                    'Product with ID "%s" lacks references for all combinations. '
                    'Add the missing references and retry the export process.'
                ) % shopify_product_id)

            # Compare attribute values between Shopify and Odoo
            attribute_values_from_shopify = self._attribute_value_from_variant(
                combination,
                attribute_value_tmpl_ids,
            )
            current_odoo_variant = list(
                filter(lambda x: x['fields'].get(variant_reference) == reference, variants)
            )

            if not current_odoo_variant:
                raise UserError(_(
                    'No Odoo variant found with reference "%s" matching Shopify product ID %s.'
                ) % (reference, shopify_product_id))

            attribute_values_from_odoo = [
                format_attr_value_code(values['key'], values['value'])
                for values in current_odoo_variant[0]['attribute_values']
            ]

            if set(attribute_values_from_odoo) != set(attribute_values_from_shopify):
                raise UserError(_(
                    'Mismatch in attribute values for Shopify variant with reference "%s": '
                    'Shopify values: %s; Odoo values: %s. Products with the same reference '
                    'must have identical attribute combinations in Shopify and Odoo.'
                ) % (
                    reference,
                    attribute_values_from_shopify,
                    attribute_values_from_odoo,
                ))

        return shopify_product_id

    def create_webhooks_from_routes(self, routes_dict):
        result = dict()

        for name_tuple, route in routes_dict.items():
            webhook = self.model_init(WEBHOOK)

            webhook.address = route
            webhook.topic = name_tuple[-1]  # --> technical_name

            self.save(webhook)
            result[name_tuple] = str(webhook.id)

        return result

    def unlink_existing_webhooks(self, external_ids=None):
        if not external_ids:
            return False

        existing_webhooks = self.fetch_multi(WEBHOOK)

        for record in existing_webhooks:
            if str(record.id) in external_ids:
                self.destroy(record)

        return True

    @check_scope('write_products')
    def export_template(self, tmpl_data):
        _logger.info('Shopify "%s": export_template()', self._integration_name)

        tmpl_data['product_type'] = tmpl_data.pop('type')
        first_time_export = not bool(tmpl_data['external_id'])

        # Take metafields from tmpl_data
        meta_template_key = f'product.template.{str(tmpl_data["id"])}'
        meta_fields_vals = {meta_template_key: tmpl_data['fields'].pop(METAFIELDS_NAME, [])}

        for variant_data in tmpl_data['products']:
            meta_variant_key = f'product.product.{str(variant_data["id"])}'
            meta_fields_vals[meta_variant_key] = variant_data['fields'].pop(METAFIELDS_NAME, [])

        # Create or update product
        product = self.fetch_one(TEMPLATE, tmpl_data['external_id'])

        if first_time_export:
            self._attach_variants(product, tmpl_data['products'])
        else:
            self._update_variants(product, tmpl_data['products'])

        self._set_base_values(product, tmpl_data['fields'])
        # product.errors.full_messages()
        self.save(product)

        # Manage Collections
        if 'collections' in tmpl_data['fields']:
            collection_ids = [
                int(x) for x in tmpl_data['fields']['collections']
            ]
            collects = self.fetch_multi(
                COLLECT,
                params={
                    'product_id': product.id,
                },
            )
            for collection_id in [x.collection_id for x in collects]:
                if collection_id not in collection_ids:
                    collection = self.fetch_one(CATEGORY, collection_id)
                    collection.remove_product(product)

            for collection_id in collection_ids:
                collection = self.fetch_one(CATEGORY, collection_id)
                collection.add_product(product)

        mappings = self._serialize_mappings(product, tmpl_data)

        self._update_metafields(meta_fields_vals, mappings)

        # Prepare translations for export
        translations_to_update = tmpl_data.get('translations_to_update', [])
        translations_to_remove = tmpl_data.get('translations_to_remove', [])

        # Proceed only if translations is provided
        if translations_to_update or translations_to_remove:
            self._export_translations(product, translations_to_update, translations_to_remove)

        return mappings

    def _export_translations(self, product, translations_to_update, translations_to_remove):
        """
        Export translations for a product using Shopify's GraphQL API.
        :param product: The product object to export translations for.
        :param translations_to_update: List of translations to update.
        :param translations_to_remove: List of translations to remove.
        :raises ValidationError: If there are errors in the response from Shopify.
        """
        _logger.info('Shopify "%s": exporting translations for product ID %s',
                     self._integration_name, product.id)

        resource_id = f'gid://shopify/Product/{product.id}'

        if translations_to_update:
            self._send_translation_updates(resource_id, translations_to_update, product)

        if translations_to_remove:
            self._send_translation_removals(resource_id, translations_to_remove, product)

    def _send_translation_updates(self, resource_id, translations, product):
        """
        Send translation update requests to Shopify.

        :param resource_id: The Shopify resource ID for the product.
        :param translations: Dictionary with fields and locales to update translations for.
        :param product: The product object to update translations for.
        """
        translations_to_register = []

        # Calculate hash for updated primary language values
        # We have to do it here because Shopify may apply some modifications to the primary
        # language values after exporting product information
        for field, field_translations in translations.items():
            if not field_translations:
                # No translations for this field, skip it
                continue

            if field.startswith(f'{METAFIELDS_NAME}.'):
                __, namespace, key = field.split('.')

                # Only global metafields (meta_title and meta_description) are supported to export
                # using translations for product
                if key not in ['title_tag', 'description_tag']:
                    continue

                # Get primary language value for metafield
                meta_fields = product.metafields()

                meta_field = list(filter(lambda x: x.key == key and x.namespace == namespace, meta_fields))
                primary_value = meta_field and meta_field[0].value or None

                # We need only the last part of the metafield keys + we need to replace field names
                # due to strange Shopify API implementation (sic!)
                field = 'meta_title' if key == 'title_tag' else 'meta_description'
            else:
                # Regular product field
                primary_value = getattr(product, field, '')

            digest = hashlib.sha256(primary_value.encode('utf-8')).hexdigest()

            for field_translation in field_translations:
                # Prepare translation data
                translation_data = {
                    'key': field,
                    'locale': field_translation['locale'],
                    'value': field_translation['value'],
                    'translatableContentDigest': digest,
                }
                translations_to_register.append(translation_data)

        response = self._graphql.translations_register(resource_id, translations_to_register)
        user_errors = response.get('userErrors', [])

        if user_errors:
            messages = '\n'.join(f'- {error.get("message")}' for error in user_errors)
            raise ValidationError(_(
                'Failed to export product translations to Shopify.\n\n'
                'Errors:\n%s'
            ) % messages)

        _logger.info(
            'Shopify "%s": Translation updates completed for product ID %s',
            self._integration_name, product.id
        )

    def _send_translation_removals(self, resource_id, translations, product):
        """
        Send translation removal requests to Shopify.
        Sends separate requests for each unique set of locales.

        :param resource_id: The Shopify resource ID for the product.
        :param translations: Dict like {'field_name': [locales], ...}. Empty list = all locales.
        :param product: The product object (for logging).
        """
        # Group fields by their locale sets
        groups = {}
        for field, locales in translations.items():
            for locale_key in locales:
                if locale_key not in groups:
                    groups[locale_key] = []
                groups[locale_key].append(field)

        # Send separate request for each group
        for locale_key, fields in groups.items():
            payload_keys = []
            for field in fields:
                if field.startswith(f'{METAFIELDS_NAME}.'):
                    __, namespace, key = field.split('.', 2)
                    if key == 'title_tag':
                        payload_keys.append('meta_title')
                    elif key == 'description_tag':
                        payload_keys.append('meta_description')
                    # Skip other metafields
                else:
                    payload_keys.append(field)

            if not payload_keys:
                continue

            response = self._graphql.translations_remove(resource_id, {
                'keys': payload_keys,
                'locales': [locale_key],
            })
            user_errors = response.get('userErrors', [])

            if user_errors:
                messages = '\n'.join(f'- {error.get("message")}' for error in user_errors)
                raise ValidationError(_(
                    'Failed to remove product translations to Shopify.\n\n'
                    'Fields: %s\nLocale: %s\n\nErrors:\n%s'
                ) % (', '.join(fields), locale_key, messages))

            _logger.info(
                'Shopify "%s": Translation removals completed (product ID %s)',
                self._integration_name, product.id
            )

    def _update_metafields(self, meta_fields_vals, mappings):
        for mapping in mappings:
            if mapping['model'] == 'product.template':
                tmpl_params = {
                    'resource': 'products',
                    'resource_id': int(mapping['external_id']),
                }
            else:
                tmpl_params = {
                    'resource': 'variants',
                    'resource_id': self._parse_variant_id(mapping['external_id']),
                }

            meta_template_key = f'{mapping["model"]}.{str(mapping["id"])}'
            meta_vals = meta_fields_vals.get(meta_template_key)

            if not meta_vals:
                continue

            meta_fields = self.fetch_multi(METAFIELD, params=tmpl_params)

            for vals in meta_vals:
                meta_field = list(filter(lambda x: x.key == vals['key'], meta_fields))
                if meta_field:
                    meta_field = meta_field[0]

                    if not vals['value']:
                        self.destroy(meta_field)
                else:
                    meta_field = self.model_init(METAFIELD, prefix_options=tmpl_params)
                    meta_field.key = vals['key']
                    meta_field.namespace = vals['namespace']
                    meta_field.type = vals['type']

                if not vals['value']:
                    continue

                meta_field.value = vals['value']

                if not self.save(meta_field):
                    raise ShopifyApiException(_(
                        'Failed to export metafield "%s" for resource "%s". '
                        'Please ensure the following:\n'
                        '1. The "Technical Name" is correctly defined.\n'
                        '2. The "Metafield Type" is properly configured.\n\n'
                        'You can verify and update these settings in the menu:\n'
                        '"E-Commerce Integrations → Product Fields → All Product Fields".'
                    ) % (vals['key'], tmpl_params['resource']))

        return True

    @check_scope('write_products', 'write_files')
    def export_template_images(
        self,
        external_template_id: str,
        datacls_list: List[ExternalImage],
        **kw,
    ) -> List[ExternalImage]:
        """
        1. All the product images stores on the template level.
        2. Template cover has property: posotion = 1.
        3. Product variant just have a FK to the image from parent template (variant support only one image).
        """
        _logger.info('Shopify "%s": export_images()', self._integration_name)

        template = self._graphql.read_template_media(external_template_id)

        # 1. Drop old images
        current_ids = [x.code for x in datacls_list if x.code]
        if not current_ids:  # It means a) the first time export; b) all mappings were dropped
            to_delete_ids = [x['id'] for x in template['media']]
        else:
            to_delete_ids = [x['id'] for x in template['media'] if x['id'] not in current_ids]

        if to_delete_ids:
            self._graphql.drop_product_images(to_delete_ids)

            # 1.1 Refresh template
            template = self._graphql.read_template_media(external_template_id)

        # 2. Prepare images to create
        variants_append_media = []
        to_create_images = sorted([x for x in datacls_list if x.to_create], key=lambda x: x.checksum)

        # 2.1 Grouping new images by checksum to avoid duplicates
        for checksum, group in itertools.groupby(to_create_images, key=lambda x: x.checksum):
            group = list(group)

            image_gid = self._graphql.create_media_image(
                filename=group[0]._get_unique_filename(),
                mimetype=group[0].mimetype,
                binary_data=base64.b64decode(group[0].b64_bytes),
            )

            # Before attach image add sleep to avoid an error:
            # {'code': 'NON_READY_STATE', 'field': ['files'], 'message': 'Non-ready files cannot be updated.'}
            sleep(2.5)

            image_data = self._graphql.attach_image(template['id'], image_gid)

            if image_data['userErrors']:
                raise ShopifyApiException(str(image_data['userErrors']))
            else:
                image = image_data['files'][0]

            for datacls in filter(lambda x: x.is_variant_cover, group):
                variants_append_media.append({
                    'mediaIds': [image_gid],
                    'variantId': f'gid://shopify/ProductVariant/{datacls.variant_code}',
                })

            if any(x.is_template_cover for x in group):
                self._graphql.set_image_as_cover(template['id'], image_gid)
                # Update template with new featured media manually to avoid API request
                template['featuredMedia'] = {'id': image_gid}

            # 2.3 Update datacls with new image data to update Odoo mappings
            datacls_to_update = [x for x in datacls_list if (x.to_assign and x.checksum == checksum)]
            datacls_to_update.extend(group)

            src = image['preview']['image']['url']  # TODO: Make it safer
            for datacls in datacls_to_update:
                datacls.update(
                    code=image_gid,
                    name=src.split('/')[-1],
                    src=src,
                )

        # 3. Reassign images covers
        featured_media_id = template['featuredMedia'] and template['featuredMedia']['id']
        for datacls in [x for x in datacls_list if x.to_assign]:
            if datacls.is_template_cover:
                for image_data in template['media']:
                    image_gid = image_data['id']
                    variant_media_gid = f'gid://shopify/MediaImage/{datacls.code_int}'

                    if (image_gid == variant_media_gid) and (image_gid != featured_media_id):
                        self._graphql.set_image_as_cover(template['id'], image_gid)

            if not datacls.variant_code:
                continue

            variants_append_media.append({
                'mediaIds': datacls.code,
                'variantId': f'gid://shopify/ProductVariant/{datacls.variant_code}',
            })

        if variants_append_media:
            data_ = {
                x['id']: [y['id'] for y in (x.get('media') or [])] for x in template['variants']
            }

            data_to_detach = []
            for append_data in variants_append_media:
                variant_id = append_data['variantId']

                if variant_id in data_ and data_[variant_id]:
                    data_to_detach.append({
                        'variantId': variant_id,
                        'mediaIds': data_[variant_id],
                    })

            if data_to_detach:
                self._graphql.detach_images_from_variants(template['id'], data_to_detach)

            self._graphql.append_images_to_variants(template['id'], variants_append_media)

        return datacls_list

    @not_implemented
    def export_attribute(self, attribute):
        """
        There is no Shopify REST API endpoint for `Attributes`.
        Moreover, the is no way to reuse attribute ID because for the each productsthe same
        attributes will create the brand new attribute ID (id + product_id have to be unique).
        See the `_handle_mapping_data` method in integration class.

        :Template options:

            "options": [
                {
                    "id": 10578321309988,
                    "product_id": 8335897788708,
                    "name": "Size",
                    "position": 1,
                    "values": [
                        "UK 1",
                        "UK 2",
                    ]
                },
            ]

        """
        pass

    @not_implemented
    def export_attribute_value(self, attribute_value):
        """
        There is no Shopify REST API endpoint for `Attribute-Values`
        and there is no ID for shopify value, only name.
        See the `_handle_mapping_data` method in integration.
        """
        pass

    @not_implemented
    def export_feature(self, feature):
        pass

    def export_feature_value(self, feature_value):
        _logger.info('Shopify "%s": export_feature_value().', self._integration_name)
        return feature_value['name']

    @check_scope('write_products')
    def export_category(self, category):
        _logger.info('Shopify "%s": export_category()', self._integration_name)

        shopify_category = self.model_init(CATEGORY)
        shopify_category.title = category['name']
        self.save(shopify_category)
        return str(shopify_category.id)

    @check_scope('write_products', 'write_inventory')
    def export_inventory(self, inventory):
        _logger.info('Shopify "%s": export_inventory()', self._integration_name)

        results = list()
        default_location_id = self.location_id

        for external_id, inventory_item_list in inventory.items():
            variant_id = self._parse_variant_id(external_id)
            shopify_variant = self.fetch_one(VARIANT, variant_id)

            if shopify_variant.is_new():
                message = _('External product "%s" does not exist') % variant_id
                results.append((variant_id, None, message))
                continue

            if getattr(shopify_variant, 'inventory_management', '') != SHOPIFY:
                shopify_variant.inventory_management = SHOPIFY  # TODO: need to think
                res = self.save(shopify_variant)
                if not res:
                    message = _('Inventory management for product "%s" was not saved') % variant_id
                    results.append((variant_id, res, message))
                    continue

            item_result = list()
            for inventory_item in inventory_item_list:
                location_id = inventory_item['external_location_id'] or default_location_id

                args = (
                    int(location_id),
                    shopify_variant.inventory_item_id,
                    int(inventory_item['qty']),
                )
                res = self.apply(INVENT_LEVEL, *args)

                if not res:
                    # Most likely it is due to the one of the following reasons:
                    # 1. Location does not exist
                    # 2. Location is deactivated
                    raise UserError(_(
                        "Failed to update the inventory level for product variant '%s'. "
                        "The specified Shopify location (ID: %s) may not exist or may be deactivated. "
                        "Please ensure this location is valid and active in your Shopify Admin."
                    ) % (variant_id, location_id))

                res_data = dict(
                    inventory_item_id=res.inventory_item_id,
                    location_id=res.location_id,
                    available=res.available,
                )
                item_result.append(res_data)

            results.append((external_id, item_result, ''))

        return results

    @check_scope(
        'write_fulfillments',
        'write_merchant_managed_fulfillment_orders',
    )
    def export_tracking(self, sale_order_id: str, tracking_data_list: List[Dict], force_done=False) -> list:
        tracking_data_list_ = sorted(
            tracking_data_list,
            key=lambda x: int(x['external_location_id'] or 0),
            reverse=True,
        )

        result_list, end = [], len(tracking_data_list_)

        for idx, picking_data in enumerate(tracking_data_list_, start=1):
            result = self.send_picking(sale_order_id, picking_data, force_done=(force_done and idx == end))
            result_list.extend(result)

        return [x for x in result_list if x]

    @check_scope(
        'write_fulfillments',
        'write_merchant_managed_fulfillment_orders',
    )
    def send_picking(self, sale_order_id: str, picking_data: dict, force_done : bool = False) -> list:
        location_id = int(picking_data.get('external_location_id') or 0)
        lines = [{'id': int(x['id']), 'qty': int(x['qty'])} for x in picking_data['lines']]

        picking_data.update(
            lines=lines,
            external_location_id=location_id,
        )

        # Make a copy of original picking data
        picking_data_orig = deepcopy(picking_data)

        fulfill_orders = self.fetch_fulfillment_orders(sale_order_id)
        fulfill_orders = [x for x in fulfill_orders if x.status in ('open', 'in_progress') and x.line_items]

        if location_id:
            # Check if there are any moves between fulfill orders must be done before fulfilling
            is_anything_was_moved = self._prepare_fulfillment_orders(fulfill_orders, picking_data)

            if is_anything_was_moved:
                # Refresh the fulfill orders and fulfill them (using original picking data)
                fulfill_orders = self.fetch_fulfillment_orders(sale_order_id)
                fulfill_orders = [x for x in fulfill_orders if x.status in ('open', 'in_progress') and x.line_items]

            fulfill_orders.sort(key=lambda x: x.assigned_location_id == location_id, reverse=True)

        fulfillments = []

        # Start for fulfilling orders (lines from picking data must fit existing fulfill orders)
        for fulfill_order in fulfill_orders:
            if force_done:
                fulfillment = self._force_fulfill_order(fulfill_order, picking_data_orig)
            else:
                fulfillment = self._fulfill_order(fulfill_order, picking_data_orig)

            if fulfillment:
                fulfillments.append(fulfillment)

            if not force_done:
                if not any(x['qty'] for x in picking_data_orig['lines']):
                    break

        return self._process_fulfillments(fulfillments)

    def _prepare_fulfillment_orders(self, fulfill_orders, picking_data):
        # If there is location ID, we need to find out which items must be moved
        # before fulfilling
        location_id = picking_data['external_location_id']

        fulfill_orders_with_same_location = [x for x in fulfill_orders if x.assigned_location_id == location_id]
        fulfill_orders_with_different_location = [x for x in fulfill_orders if x.assigned_location_id != location_id]

        # Step 1. Check orders with the same location
        for fulfill_order in fulfill_orders_with_same_location:
            for line in picking_data['lines']:
                data = fulfill_order._prepare_pending_line(line['id'], line['qty'])

                if data:
                    line['qty'] -= data['quantity']

        # Step 2. Filter lines with zero quantity in picking data
        picking_data['lines'] = [x for x in picking_data['lines'] if x['qty']]

        # Exit if nothing to fulfill
        if not picking_data['lines']:
            return False

        # Step 3. Check orders with different location
        line_ids_to_fullfill = [x['id'] for x in picking_data['lines']]

        def _sort_fulfillment_orders(o):
            # Sort by number of matching line items
            line_item_ids = [x.line_item_id for x in o.line_items if x.fulfillable_quantity]
            return len(set(line_item_ids) & set(line_ids_to_fullfill))

        # Leave only fulfill_orders that have different location
        fulfill_orders_with_different_location.sort(key=_sort_fulfillment_orders, reverse=True)

        is_anything_was_moved = False

        for fulfill_order in fulfill_orders_with_different_location:
            lines_to_move = []

            for line in picking_data['lines']:
                data = fulfill_order._prepare_pending_line(line['id'], line['qty'])

                if data:
                    line['qty'] -= data['quantity']

                    # Save data for moving
                    lines_to_move.append(data)

            if lines_to_move:
                fulfill_order.move(location_id, lines_to_move)

                is_anything_was_moved = True

            # If there is no more lines to fulfill, break the loop
            if not any(x['qty'] for x in picking_data['lines']):
                break

        return is_anything_was_moved

    def _fulfill_order(self, fulfill_order, picking_data: dict):
        fulfillment = self.model_init(FULFILLMENT)

        line_items = []

        for line in picking_data['lines']:
            data = fulfill_order._prepare_pending_line(line['id'], line['qty'])

            if data:
                line_items.append(data)
                line['qty'] -= data['quantity']

        if not line_items:
            return False

        tracking_info = {
            'number': picking_data['tracking'],
            'company': picking_data['carrier_code'],
        }

        if picking_data['carrier_tracking_url']:
            tracking_info['url'] = picking_data['carrier_tracking_url']

        fulfillment.tracking_info = tracking_info

        fulfillment.line_items_by_fulfillment_order = [{
            'fulfillment_order_id': fulfill_order.id,
            'fulfillment_order_line_items': line_items,
        }]
        fulfillment.notify_customer = True

        return fulfillment

    def _force_fulfill_order(self, fulfill_order, picking_data: dict):
        fulfillment = self.model_init(FULFILLMENT)

        tracking_info = {
            'number': picking_data['tracking'],
            'company': picking_data['carrier'],
        }

        if picking_data['carrier_tracking_url']:
            tracking_info['url'] = picking_data['carrier_tracking_url']

        fulfillment.tracking_info = tracking_info

        fulfillment.line_items_by_fulfillment_order = [{
            'fulfillment_order_id': fulfill_order.id,
            'fulfillment_order_line_items': fulfill_order._prepare_pending_lines(),
        }]
        fulfillment.notify_customer = True

        return fulfillment

    def _process_fulfillments(self, fulfillments: list):
        result = []

        if not fulfillments:
            return result

        for fulfillment in fulfillments:
            res = self.save(fulfillment)

            if not res:
                raise ShopifyApiException(f'Fulfillment was not saved: {fulfillment.errors.full_messages()}')

            result.append(serialize_fulfillment(fulfillment.to_dict()))

        return result

    @check_scope('write_orders')
    def export_sale_order_status(self, vals):
        method_name = f'_export_sub_status_{vals["status"]}'

        if hasattr(self, method_name):
            return getattr(self, method_name)(vals)

        raise NotImplementedError(_(
            'The Shopify export method "%s" is not yet implemented. Please contact VentorTech '
            'support at support@ventor.tech to report this issue. When contacting support, '
            'provide the following:\n'
            '1. The exact status value: "%s".\n'
            '2. The Shopify instance URL (e.g., "xxx.myshopify.com").\n\n'
            'For secure sharing of sensitive information, use https://share.ventor.tech.'
            ) % (method_name, vals["status"])
        )

    def _export_sub_status_paid(self, vals):
        amount = vals['amount']
        currency = vals['currency']
        order_id = vals['order_id']

        order = self.fetch_one(ORDER, order_id)
        if not order.id or order.financial_status == ShopifyOrderStatus.STATUS_PAID:
            return dict()

        # Handle partially paid orders (not yet supported)
        if order.financial_status == ShopifyOrderStatus.STATUS_PARTIALLY_PAID:  # TODO
            raise ValidationError(_(
                'Marking "Partially Paid" orders as fully paid is not supported. Please resolve this '
                'issue in Shopify directly and then reattempt the operation.'
            ))

        if order.financial_status == ShopifyOrderStatus.STATUS_PARTIALLY_REFUNDED:  # TODO
            raise ValidationError(
                _('We do not support yet marking as paid for "Partially Refunded" orders')
            )

        # Fetch existing transactions
        params = dict(order_id=order_id)
        txn_list = self.fetch_multi(TRANSACTION, params=params)

        # Exclude voided transactions
        except_ids = [
            x.parent_id for x in txn_list if x.kind == Txn.VOID and x.status == Txn.STATUS_SUCCESS
        ]
        txn_list = [
            x for x in txn_list if x.kind in (Txn.AUTH, Txn.SALE)
            and x.status in (Txn.STATUS_PENDING, Txn.STATUS_SUCCESS)
            and x.id not in except_ids
        ]

        # Prepare the new transaction
        parent = txn_list[-1] if txn_list else False
        txn = self.model_init(TRANSACTION, prefix_options=params)

        if not parent:
            txn.kind = Txn.SALE
            txn.source = Txn.SOURCE_EXTERNAL
            txn.amount = amount
            txn.currency = currency

        elif parent.kind == Txn.SALE:
            # Handle existing sale transactions
            if parent.status == Txn.STATUS_PENDING:
                txn.kind = Txn.CAPTURE  # TODO: make sure that `parent.amount == amount`
                txn.parent_id = parent.id
            else:
                txn.kind = Txn.SALE
                txn.source = Txn.SOURCE_EXTERNAL
                txn.amount = amount
                txn.currency = currency

        else:
            # Handle other transaction kinds
            if parent.status == Txn.STATUS_PENDING:  # TODO: do the math how to perform
                raise ValidationError(_(             # pending parent transaction without raising
                    'Cannot proceed with transaction. Awaiting resolution for the pending parent '
                    'transaction: %s' % parent.to_dict()
                ))

            txn.kind = Txn.CAPTURE
            txn.parent_id = parent.id
            txn.amount = amount
            txn.currency = currency

        result = self.save(txn)

        if not result:
            return dict()

        return serialize_transaction(txn.to_dict())

    @add_dynamic_kwargs
    def order_fetch_kwargs(self, **kw):
        integration = self._get_integration(kw)
        receive_from = integration.last_receive_orders_datetime_str
        cut_off_datetime = integration.orders_cut_off_datetime_str

        params = self._default_order_domain()
        params['updated_at_min'] = receive_from
        params['order'] = 'updated_at ASC'

        if cut_off_datetime:
            params['created_at_min'] = cut_off_datetime

        return {
            'params': params,
            'quantity': self.order_limit_value(),
        }

    def receive_orders_using_graphql(self, order_ids):
        """
        Fetch orders using GraphQL API.
        """
        order_graphql_ids = self._graphql.get_orders_ids_query(order_ids)

        # Process GraphQL data
        graphql_orders = []
        for order in order_graphql_ids:
            order_id = ExtractNode.extract_raw(order, 'node.id', str)
            channel_id = ExtractNode.extract_raw(order, 'node.publication.id', str)
            channel_name = ExtractNode.extract_raw(order, 'node.publication.name', str)
            source_name = ExtractNode.extract_raw(order, 'node.sourceName', str)

            if order_id:
                graphql_orders.append({
                    'id': parse_graphql_id(order_id),
                    'channel_id': parse_graphql_id(channel_id) if channel_id else None,
                    'channel_name': channel_name,
                    'order_source_name': source_name,
                })

        return graphql_orders

    @add_dynamic_kwargs
    @check_scope('read_orders')
    def receive_orders(self, **kw):
        _logger.info('Shopify "%s": receive_orders()', self._integration_name)

        # Fetch orders using REST API
        kwargs = self.order_fetch_kwargs()(**kw)
        orders = self.fetch_multi(ORDER, **kwargs)

        # Extract order IDs
        new_order_ids = [str(order.id) for order in orders]

        # If no orders found, return empty list
        if not new_order_ids:
            return []

        # Fetch additional order information using GraphQL API
        graphql_orders_data = self.receive_orders_using_graphql(new_order_ids)

        # Merge GraphQL data into orders.
        merge_orders_data(orders, graphql_orders_data, ['channel_id', 'channel_name', 'order_source_name'])

        result = [
            {
                'id': str(order.id),
                'data': order.to_dict(),
                'updated_at': order.updated_at,
                'created_at': order.created_at,
            }
            for order in orders
        ]

        return result

    @check_scope('read_orders')
    def receive_order(self, order_id):
        """
        Receive and process a single order from Shopify.
        """
        # Fetch order from REST API
        order = self.fetch_one(ORDER, order_id)
        if order.is_new():
            return {}

        # Fetch order data from GraphQL API
        graphql_order_data = self._graphql.get_orders_ids_query(order_id)
        channel_id = parse_graphql_id(
            ExtractNode.extract_raw(graphql_order_data, '0.node.publication.id', str)
        )
        channel_name = ExtractNode.extract_raw(graphql_order_data, '0.node.publication.name', str)
        source_name = ExtractNode.extract_raw(graphql_order_data, '0.node.sourceName', str)

        order.channel_id = channel_id
        order.channel_name = channel_name
        order.order_source_name = source_name

        # Prepare the final output
        return {
            'id': order.id,
            'data': order.to_dict()
        }

    def get_order_class_parser(self):
        """Hook for external module extensions"""
        return ShopifyOrder

    @add_dynamic_kwargs
    def parse_order(self, input_file: dict, **kw) -> dict:
        _logger.info('Shopify "%s": parse_order() from input file.', self._integration_name)

        fulfillment_orders = self.fetch_fulfillment_orders(input_file['id'])
        order_risks = self.fetch_order_risks(input_file['id'])
        payment_transactions = self.fetch_order_payments(input_file['id'])

        ClassParser = self.get_order_class_parser()

        shopify_order = ClassParser(
            self._get_integration(kw),
            input_file,
            fulfillment_orders=[x.to_dict() for x in fulfillment_orders],
            order_risks=order_risks,
            payment_transactions=payment_transactions,
        )

        return shopify_order.parse()

    @check_scope('read_orders')
    def fetch_order_risks(self, external_order_id: str, risklevel : str = 'HIGH'):
        """
        Fetch order risks from Shopify for a specific order.
        """
        risk_data = self._graphql.get_order_risks_from_order_query(external_order_id)
        if not risk_data:
            return []

        risks = []
        assessments = risk_data.get('assessments') or list()
        recommendation = risk_data.get('recommendation') or ''

        for record in assessments:
            if record.get('riskLevel') == risklevel:

                for fact in record.get('facts', []):
                    risks.append({
                        **fact,
                        'order_id': external_order_id,
                        'recommendation': recommendation.lower(),
                    })

        return risks

    @check_scope('read_orders')
    def fetch_order_payments(self, external_order_id):
        records = self.fetch_multi(TRANSACTION, params={'order_id': external_order_id})
        return [x.to_dict() for x in records]

    def fetch_order_transactions(self, external_order_id):
        payments = self.fetch_order_payments(external_order_id)
        return [serialize_transaction(x) for x in payments]

    def fetch_order_fulfillments(self, external_order_id):
        order_data = self.receive_order(external_order_id)
        if not order_data:
            return []

        fulfillments = order_data['data'].get('fulfillments') or list()
        return [serialize_fulfillment(x) for x in fulfillments]

    def get_delivery_methods(self):
        _logger.info('Shopify "%s": get_delivery_methods()', self._integration_name)

        batch_size = int(self.get_settings_value('batch_size'))
        delivery_set = set()
        cursor = None

        while True:
            order_edges, cursor = self._graphql.get_delivery_methods_from_orders_query(batch_size, cursor)

            control_set = set()
            for data in order_edges:
                control_set |= self._parse_delivery_methods(data.get('node'))

            # Exit the loop if no new delivery methods are found
            if not control_set.difference(delivery_set):
                break

            delivery_set.update(control_set)

            # Exit the loop if no cursor
            if not cursor:
                break

        return [dict(x) for x in delivery_set]

    def _parse_delivery_methods(self, order):
        shipping_methods = []
        for line in order.get('shippingLines', {}).get('nodes', []):
            title = line.get('title')
            code = line.get('code')
            ext_code = format_delivery_code(title, code)

            shipping_methods.append(
                (('id', ext_code), ('name', (title or code)))
            )

        return set(shipping_methods)

    def get_single_tax(self, tax_id):
        _logger.info('Shopify "%s": get_single_tax(). No implemented', self._integration_name)
        return dict()

    @check_scope('read_orders')
    def get_taxes(self, **kw):
        _logger.info('Shopify "%s": get_taxes()', self._integration_name)

        batch_size = int(self.get_settings_value('batch_size'))
        tax_set = set()
        cursor = None

        while True:
            order_edges, cursor = self._graphql.get_taxes_from_orders_query(batch_size, cursor)

            control_set = set()
            for data in order_edges:
                control_set |= self._parse_taxes(data.get('node'))

            # Exit the loop if no new taxes are found
            if not control_set.difference(tax_set):
                break

            tax_set.update(control_set)

            # Exit the loop if no cursor
            if not cursor:
                break

        return [self._format_external_tax(x) for x in tax_set]

    def _parse_taxes(self, order):
        tax_included = order.get('taxesIncluded')

        # Extract taxes from order tax lines
        order_tax_list = [
            self._format_tax(tax, tax_included)
            for tax in order.get('taxLines', [])
            if tax
        ]

        # Extract taxes from line item tax lines
        line_tax_list = [
            self._format_tax(tax, tax_included)
            for line in order.get('lineItems', {}).get('edges', [])
            for tax in line.get('node', {}).get('taxLines', [])
            if tax
        ]

        # Extract taxes from shipping line tax lines
        shipping_tax_list = [
            self._format_tax(tax, tax_included)
            for line in order.get('shippingLines', {}).get('edges', [])
            for tax in line.get('node', {}).get('taxLines', [])
            if tax
        ]
        return set(order_tax_list + line_tax_list + shipping_tax_list)

    @check_scope('read_orders')
    def get_payment_methods(self, **kw):
        _logger.info('Shopify "%s": get_payment_methods()', self._integration_name)

        batch_size = int(self.get_settings_value('batch_size'))
        payment_set = set()
        cursor = None

        while True:
            order_edges, cursor = self._graphql.get_payment_methods_from_orders_query(batch_size, cursor)

            empty_code = format_payment_code(None)
            if not payment_set:
                payment_set = {(('id', empty_code), ('name', empty_code))}

            control_set = set()
            for data in order_edges:
                control_set |= self._parse_payment_methods(data.get('node'))

            # Exit the loop if no new payment methods are found
            if not control_set.difference(payment_set):
                break

            payment_set.update(control_set)

            # Exit the loop if no cursor
            if not cursor:
                break

        return [dict(x) for x in payment_set]

    def _parse_payment_methods(self, order):
        payment_methods = []
        for name in order.get('paymentGatewayNames', []):
            if not name:
                continue

            ext_code = format_payment_code(name)
            payment_methods.append(
                (('id', ext_code), ('name', name))
            )

        return set(payment_methods)

    @check_scope('read_locales')
    def get_languages(self):
        _logger.info('Shopify "%s": get_languages()', self._integration_name)
        locales = self._graphql.get_locales()
        if not locales:
            return []

        return [
            {
                'id': x['locale'],
                'code': x['locale'],
                'external_reference': f"{x['locale']}_{self.country}",
                'primary': x['primary'],
            } for x in locales
        ]

    @check_scope('read_products')
    def get_attributes(self, parse_values=False):
        _logger.info('Shopify "%s": get_attributes()', self._integration_name)

        products = self.fetch_multi(TEMPLATE, fields=['options'])

        result = set()
        for product in products:
            res = self._parse_attributes(product, parse_values=parse_values)
            result.update(res)

        return [dict(x) for x in result]

    def get_attribute_values(self):
        _logger.info('Shopify "%s": get_attribute_values()', self._integration_name)
        return self.get_attributes(parse_values=True)

    def get_features(self):
        _logger.info('Shopify "%s": get_features()', self._integration_name)
        return [{
            'id': GENERAL_GROUP,
            'name': 'General group',
        }]

    def get_feature_values(self):
        _logger.info('Shopify "%s": get_feature_values()', self._integration_name)
        tags = self._graphql.get_feature_values()

        return [
            {
                'id': x['node'],
                'name': x['node'],
                'id_group': GENERAL_GROUP,
            } for x in tags
        ]

    @check_scope('read_publications')
    def get_sale_channels(self):
        _logger.info('Shopify "%s": get_sale_channels()', self._integration_name)

        sale_channels = self._graphql.get_sale_channels()
        return [x['node'] for x in sale_channels]

    def get_pricelists(self):
        _logger.info('Shopify "%s": get_pricelists(). Not implemented.', self._integration_name)
        return []

    @check_scope('read_locations')
    def get_locations(self):
        _logger.info('Shopify "%s": get_locations().', self._integration_name)

        result = list()
        location_list = self.fetch_multi(LOCATION)

        for rec in location_list:
            vals = dict(
                id=str(rec.id),
                name=rec.name,
            )
            result.append(vals)

        return result

    def get_countries(self):
        _logger.info('Shopify "%s": get_countries()', self._integration_name)

        external_countries = list()
        countries = self.fetch_multi(COUNTRY, fields=['name', 'code'])

        for country in countries:
            external_country = {
                'id': str(country.id),
                'name': country.name,
                'external_reference': country.code,
            }
            external_countries.append(external_country)

        return external_countries

    def get_states(self):
        _logger.info('Shopify "%s": get_states()', self._integration_name)

        external_states = list()
        countries = self.fetch_multi(COUNTRY, fields=['name', 'code', 'provinces'])

        for country in countries:
            for state in country.provinces:
                external_state = {
                    'id': str(state.id),
                    'name': state.name,
                    'external_reference': f'{country.code}_{state.code}',
                }
                external_states.append(external_state)

        return external_states

    @check_scope('read_products')
    def get_categories(self):
        _logger.info('Shopify "%s": get_categories()', self._integration_name)

        external_collections = list()
        collections = self.fetch_multi(CATEGORY, fields=['title'])

        for collection in collections:
            external_state = {
                'id': str(collection.id),
                'name': collection.title,
            }
            external_collections.append(external_state)

        return external_collections

    def get_sale_order_statuses(self):
        _logger.info('Shopify "%s": get_sale_order_statuses()', self._integration_name)
        order_states = list()

        statuses = self._get_shopify_statuses()
        for state, values in statuses.items():
            order_states.append({
                'id': state,
                'name': values[0],
                'external_reference': False,
            })

        return order_states

    def get_product_template_ids(self):
        _logger.info('Shopify "%s": get_product_template_ids()', self._integration_name)

        params = self._default_product_domain()
        template_records = self.fetch_multi(
            TEMPLATE,
            params=params,
            fields=['id'],
        )
        return [x.id for x in template_records]

    @add_dynamic_kwargs
    @check_scope('read_products')
    def get_product_templates(self, template_ids, **kw):
        _logger.info('Shopify "%s": get_product_templates()', self._integration_name)

        if not template_ids:
            return dict()

        integration = self._get_integration(kw)
        variant_reference = integration.variant_reference_api_name
        variant_barcode = integration.variant_barcode_api_name

        def parse_variant(template, variant):
            attribute_value_tmpl_ids = self._attribute_value_from_template(template)
            attribute_var_ids = self._attribute_value_from_variant(
                variant,
                attribute_value_tmpl_ids,
            )

            return {
                'id': self._build_product_external_code(template.id, variant.id),
                'name': template.title,
                'external_reference': getattr(variant, variant_reference) or None,
                'barcode': getattr(variant, variant_barcode) or None,
                'ext_product_template_id': str(template.id),
                'attribute_value_ids': attribute_var_ids,
            }

        result_list = list()
        templates = self.fetch_multi(
            TEMPLATE,
            params={
                'ids': ','.join(template_ids),
            },
            fields=['title', 'options', 'variants'],
        )

        for template in templates:
            external_ref = barcode = None
            variants = template.variants

            if len(variants) == 1:
                barcode = getattr(variants[0], variant_barcode) or None
                external_ref = getattr(variants[0], variant_reference) or None

            result_list.append({
                'id': str(template.id),
                'name': template.title,
                'barcode': barcode,
                'external_reference': external_ref,
                'variants': [parse_variant(template, x) for x in variants],
            })

        return {x['id']: x for x in result_list}

    @check_scope('read_customers')
    def get_customer_ids(self, date_since=None):
        _logger.info('Shopify "%s": get_customer_ids()', self._integration_name)

        # TODO: After migrating to GraphQL API, include the "locale" attribute when fetching customers
        customers = self.fetch_multi(CUSTOMER, fields=['id', 'updated_at'])

        if date_since:
            customers = [
                x for x in customers if
                parser.isoparse(x.updated_at).replace(tzinfo=None) > date_since
            ]
        return [x.id for x in customers]

    @check_scope('read_customers')
    def get_customer_and_addresses(self, customer_id):
        _logger.info('Shopify "%s": get_customer_and_addresses()', self._integration_name)
        parsed_customer, parsed_addreses = dict(), list()

        # TODO: After migrating to GraphQL API, include the "locale" attribute when fetching customers
        customer = self.fetch_one(CUSTOMER, customer_id)
        if customer.is_new():
            return parsed_customer, parsed_addreses

        customer = customer.to_dict()
        parsed_customer = self._parse_customer(customer)
        parsed_addreses = [
            self._parse_address(customer, x) for x in customer['addresses']
        ]
        return parsed_customer, parsed_addreses

    @add_dynamic_kwargs
    @check_scope('read_products', 'read_inventory')
    def get_product_for_import(self, external_template_id: str, **kw):
        _logger.info('Shopify "%s": get_product_for_import()', self._integration_name)

        product = self.fetch_one(TEMPLATE, external_template_id)
        if product.is_new():
            raise UserError(_(
                'Product with id "%s" does not exist in Shopify. Please verify the product ID '
                'and ensure it is available in your Shopify store.'
                ) % external_template_id
            )

        primary_locale, locales = self._process_locales()

        if not primary_locale:
            primary_locale = self.lang

        # If no specific locales are available (e.g., 'read_locales' scope not granted or no locales
        # configured in Shopify), default to using the store's primary language for translations.
        if not locales:
            locales = [primary_locale]

        translations_product = self._graphql.get_product_translations(external_template_id, locales)
        if any(translations_product.values()):
            # If translations are not empty, assemble them
            product = self._assemble_translations(primary_locale, product, translations_product)

        # Parse template images
        external_images = []
        for image in product.images:
            if image.id == product.image.id or not image.variant_ids:
                external_images.append(
                    ExternalImage(
                        code=image.admin_graphql_api_id,
                        name=image.src.rsplit('/', 1)[-1],
                        ttype=ProductType.PRODUCT_TEMPLATE,
                        template_code=external_template_id,
                        src=image.src,
                        is_cover=(image.id == product.image.id),
                        integration_id=self._integration_id,
                    ),
                )

        # Parse variants
        variants = []
        for variant in product.variants:

            translations_variant = self._graphql.get_variant_translations(variant.id, locales)
            if any(translations_variant.values()):
                # If translations are not empty, assemble them
                variant = self._assemble_translations(primary_locale, variant, translations_variant)

            variants.append((product, variant))

            # PArse variant images
            for image in product.images:
                if image.id == variant.image_id:
                    external_images.append(
                        ExternalImage(
                            code=image.admin_graphql_api_id,
                            name=image.src.rsplit('/', 1)[-1],
                            ttype=ProductType.PRODUCT_PRODUCT,
                            template_code=external_template_id,
                            variant_code=str(variant.id),
                            src=image.src,
                            is_cover=True,  # The Shopify variant has only one image (relates to the template image)
                            integration_id=self._integration_id,
                        )
                    )

        return product, variants, [], external_images  # TODO: convert product / variants to dict

    @check_scope('read_locales')
    def _get_locales(self):
        """
        Get available locales from Shopify.
        """
        return self._graphql.get_locales()

    def _process_locales(self):
        """
        Process locales from Shopify and return primary locale and list of locales.
        """
        primary_locale = None
        locales = []

        # Check if 'read_locales' access scope is granted.
        # If not, the old logic (without locale-specific data) is used.
        # If granted, we can fetch and utilize Shopify's locale information.
        if 'read_locales' in self.access_scopes:
            shop_locales = self._get_locales()

            for lang in shop_locales:
                if lang.get('primary'):
                    primary_locale = lang.get('locale')
                    break

            locales = [x['locale'] for x in shop_locales]

        return primary_locale, locales

    def _assemble_translations(self, primary_locale, product, translations):
        """
        Assemble translations for product or variant.
        {
            "title": {"language": [{"attrs": {"id": "en"}, "value": "Title"}, ...]},
            ...
        }
        """
        result = deepcopy(product)
        primary_keys = set()

        for alias_key, translation_list in translations.items():
            locale = alias_key.split('_', 1)[-1]  # translations_en -> en
            for item in translation_list:
                field_name = item['key']
                value = item['value']

                if not isinstance(result.attributes.get(field_name), dict):
                    result.attributes[field_name] = {'language': []}

                result.attributes[field_name]['language'].append({
                    'attrs': {'id': locale},
                    'value': value
                })

                # Add primary locale translation only once per field
                primary_value = product.attributes.get(field_name)
                if field_name not in primary_keys:
                    result.attributes[field_name]['language'].append({
                        'attrs': {'id': primary_locale},
                        'value': primary_value
                    })
                    primary_keys.add(field_name)

        return result

    def get_image_data(self, src):
        response = requests.get(src)

        if response.ok:
            return base64.b64encode(response.content)

        raise ShopifyApiException(response.text)

    def _attribute_value_from_template(self, template):
        attribute_value_tmpl_ids = list()

        for option in template.options:
            # If the attribute name is default and there is only one default value - skip it
            if (
                option.name == ATTR_DEFAULT_TITLE
                and len(option.values) == 1
                and option.values[0] == ATTR_DEFAULT_VALUE
            ):
                continue

            for value in option.values:
                attribute_value_tmpl_ids.append((option.name, value))

        return attribute_value_tmpl_ids

    def _attribute_value_from_variant(self, variant, attribute_value_tmpl_ids):
        attribute_var_ids = list()
        keys = self._get_option_keys()

        for variant_value in filter(None, [getattr(variant, key) for key in keys]):
            for (option_name, option_value) in attribute_value_tmpl_ids:
                if variant_value == option_value:
                    attribute_var_ids.append(
                        format_attr_value_code(option_name, option_value)
                    )

        return attribute_var_ids

    @not_implemented
    def get_products_for_accessories(self):
        return [], {}

    @check_scope('read_products', 'read_inventory')
    def get_stock_levels(self, external_location_id):
        _logger.info('Shopify "%s": get_stock_levels(%s)', self._integration_name, external_location_id)

        stock_levels = self.fetch_multi(
            INVENT_LEVEL,
            params={
                'location_ids': external_location_id or self.location_id,
            },
            fields=['inventory_item_id', 'available'],
        )
        inventory_data = {x.inventory_item_id: x.available for x in stock_levels}

        result = dict()
        products = self.fetch_multi(TEMPLATE, fields=['id', 'variants'])
        for product in products:
            for variant in product.variants:
                item_id = variant.inventory_item_id

                if item_id in inventory_data:
                    code = self._build_product_external_code(product.id, variant.id)
                    result[code] = inventory_data[item_id]

        return result

    @add_dynamic_kwargs
    @check_scope('read_products')
    def get_templates_and_products_for_validation_test(self, product_refs=None, **kw):
        """Shopify product has no reference (sku) and barcode, only its variant."""
        _logger.info('Shopify "%s": get_templates_and_products_for_validation_test()', self._integration_name)

        integration = self._get_integration(kw)
        variant_reference = integration.variant_reference_api_name
        variant_barcode = integration.variant_barcode_api_name

        def serialize_template(t):

            def serialize_variant(v):
                return {
                    'id': str(v['id']),
                    'name': v['title'],
                    'barcode': v.get(variant_barcode) or '',
                    'ref': v.get(variant_reference) or '',
                    'parent_id': str(t['id']),
                    'skip_ref': False,
                    'joint_namespace': False,
                }

            return [
                {
                    'id': str(t['id']),
                    'name': t['title'],
                    'barcode': '',
                    'ref': '',
                    'parent_id': '',
                    'skip_ref': True,
                    'joint_namespace': False,
                },
                *[serialize_variant(var) for var in t['variants']],
            ]

        params = self._default_product_domain()
        template_ids = self.fetch_multi(
            TEMPLATE,
            params=params,
            fields=['title', 'variants'],
        )
        products_data = dict()
        for tmpl in (template.to_dict() for template in template_ids):
            products_data[str(tmpl['id'])] = serialize_template(tmpl)

        return TemplateHub(list(itertools.chain.from_iterable(products_data.values())))

    @check_scope(
        'read_merchant_managed_fulfillment_orders',
        # 'read_assigned_fulfillment_orders',  # TODO
        # 'read_third_party_fulfillment_orders',  # TODO
    )
    def fetch_fulfillment_orders(self, external_order_id):
        return self.fetch_multi(
            FULFILLMENT_ORDER,
            params={
                'order_id': external_order_id,
            },
        )

    @check_scope('write_orders')
    def cancel_order(self, external_id: str, params: dict):
        return self._graphql.cancel_order(external_id, params)

    @check_scope('write_merchant_managed_fulfillment_orders')
    def cancel_fulfillment(self, external_id: str):
        return self._graphql.cancel_fulfillment(external_id)

    def _set_base_values(self, instance, data):
        for field, value in data.items():
            setattr(instance, field, value)

    def _init_image(self, data, product_id: str, variant_ids=False):
        image = self.model_init(IMAGE, prefix_options={'product_id': product_id})
        image.variant_ids = variant_ids or []
        image.attach_image(base64.b64decode(data))
        return image

    def _update_variants(self, product, variant_list):
        # Drop variants if necessary
        external_ids = [
            self._parse_variant_id(x['external_id']) for x in variant_list
        ]
        for variant in product.variants:
            if variant.id not in external_ids:
                self.destroy(variant)

        # Update variants
        self.refresh(product)
        self._attach_variants(product, variant_list)

    def _set_values(self, instance, data):
        self._set_base_values(instance, data['fields'])
        instance.inventory_management = SHOPIFY  # TODO: need to think

        keys = self._get_option_keys()
        values = [attr['value'] for attr in data['attribute_values']]

        for key, value in zip(keys, values):
            setattr(instance, key, value)

        return instance

    def _attach_variants(self, product, variant_list):
        product_options = defaultdict(list)
        existing_variants = getattr(product, 'variants', list())
        product.variants = list()

        for data in variant_list:
            variant_id = self._parse_variant_id(data['external_id'])
            variants = list(filter(lambda x: x.id == variant_id, existing_variants))
            variant = variants[0] if variants else self.fetch_one(VARIANT, variant_id)

            self._set_values(variant, data)
            product.variants.append(variant)

            for attr in data['attribute_values']:
                product_options[attr['key']].append(attr['value'])

        if product_options:  # avoid 'could not update options to []' shopify api error
            product.options = [
                {'name': k, 'values': v} for k, v in product_options.items()
            ]

    def _serialize_mappings(self, product, tmpl_data):
        mappings = [{
            'model': 'product.template',
            'id': tmpl_data['id'],
            'external_id': str(product.id),
            'attribite_values': {
                'external_data': [dict(x) for x in self._parse_attributes(product, True)],
                'existing_ids': [
                    y['external_id'] for x in tmpl_data['products'] for y in x['attribute_values']
                ],
            },
        }]

        for variant in product.variants:
            for data in tmpl_data['products']:
                if getattr(variant, data['reference_api_field']) == data['reference']:

                    mappings.append({
                        'model': 'product.product',
                        'id': data['id'],
                        'external_id': self._build_product_external_code(
                            product.id,
                            variant.id,
                        ),
                    })

        return mappings

    @add_dynamic_kwargs
    def _find_product_by_references(self, product_refs, **kw):
        products = list()
        integration = self._get_integration(kw)
        variant_reference = integration.variant_reference_api_name

        for ref in product_refs:
            result = self._fetch_product_by_ref(variant_reference, ref)
            products.append(result)

        return products

    def _fetch_product_by_ref(self, ecommerce_ref, ref):

        def truncate(item):
            if not item or not isinstance(item, str):
                return False
            return item.rsplit('/', maxsplit=1)[-1]

        node = self._graphql.get_product_id_by_reference(ecommerce_ref, ref)

        variant_id = node.get('id')
        product_id = node.get('product', {}).get('id')

        return truncate(product_id), truncate(variant_id)

    def _parse_attributes(self, product, parse_values=False):
        container = defaultdict(set)

        for option in product.options:
            # If the attribute name is default and there is only one default value - skip it
            if (
                option.name == ATTR_DEFAULT_TITLE
                and len(option.values) == 1
                and option.values[0] == ATTR_DEFAULT_VALUE
            ):
                continue

            container[option.name].update(set(option.values))

        if parse_values:
            value_set = set()
            for k, vals in container.items():
                for val in vals:
                    attribute_data = (
                        ('id', format_attr_value_code(k, val)),
                        ('id_group', format_attr_code(k)),
                        ('id_group_name', k),
                        ('name', val),
                    )
                    value_set.add(attribute_data)
            return value_set

        return set([(('id', format_attr_code(x)), ('name', x)) for x in container.keys()])

    def _get_url_pattern(self, wrap_li=True):
        pattern = f'<a href="{self.admin_url}/products/%s/variants/%s" target="_blank">%s</a>'
        if wrap_li:
            return f'<li>{pattern}</li>'
        return pattern

    def _prepare_url_args(self, record):
        if record.parent_id:
            return (record.parent_id, record.id, record.format_name)
        return (record.id, record.id, record.format_name)

    def _convert_to_html(self, id_list):
        pattern = self._get_url_pattern()
        arg_list = [self._prepare_url_args(x) for x in id_list]
        return ''.join([pattern % args for args in arg_list])

    @staticmethod
    def _parse_variant_id(external_id):
        if not external_id or not isinstance(external_id, str):
            return False
        return int(external_id.split('-')[-1])

    @staticmethod
    def _get_option_keys():
        return 'option1', 'option2', 'option3'

    @staticmethod
    def _parse_base_contact(data):
        """
        Parse common contact fields (name, email, phone, locale) for customers or addresses.
        """
        first_name = (data.get('first_name') or '').strip()
        last_name = (data.get('last_name') or '').strip()
        person_name = ' '.join(filter(None, [first_name, last_name]))
        email = (data.get('email') or '').strip()

        vals = {
            'email': email,
            'phone': data.get('phone') or '',
            'person_name': person_name or email,
            'customer_locale': (data.get('customer_locale') or '').strip(),
        }

        return vals

    def _parse_customer(self, customer_data):
        """
        Parse Shopify customer data for Odoo partner creation.
        """
        vals = self._parse_base_contact(customer_data)
        vals['id'] = str(customer_data.get('id', ''))
        return vals

    def _parse_address(self, customer_data, address_data):
        if not address_data:
            return {}

        vals = self._parse_base_contact(address_data)

        vals.update({
            'phone': (address_data.get('phone') or '').strip(),
            'company_name': (address_data.get('company') or '').strip(),
            'street': (address_data.get('address1') or '').strip(),
            'street2': (address_data.get('address2') or '').strip(),
            'city': (address_data.get('city') or '').strip(),
            'country_code': (address_data.get('country_code') or '').strip(),
            'state_code': (address_data.get('province_code') or '').strip(),
            'zip': (address_data.get('zip') or '').strip(),
            'person_name': (address_data.get('name') or '').strip(),
        })

        # Fallback to customer name if name is missing
        if not vals['person_name']:
            vals['person_name'] = customer_data.get('person_name')

        # Fallback to customer email if email is missing
        if not vals['email']:
            vals['email'] = customer_data.get('email', '')

        # Duplicate language from customer to address if available
        if customer_data.get('customer_locale'):
            vals['customer_locale'] = customer_data['customer_locale']

        # Determine the address type as “invoice”.
        # Since in Shopify one address can be set as both billing and shipping addresses.
        # We only need the billing address
        if address_data.get('default'):
            vals['type'] = 'invoice'

        return vals

    @staticmethod
    def _format_tax(tax, is_tax_included):
        # Format tax like a 'Sales Tax (LX799/XL) 20.3% [excluded]'
        rate = str(round(tax['rate'] * 100, 2))
        tax_option = ('excluded', 'included')[is_tax_included]

        return f'{tax["title"]} {rate}% [{tax_option}]'

    @staticmethod
    def _format_external_tax(tax_id):
        # Expected tax_id formatted as 'Sales Tax (LX799/XL) 20.3% [excluded]'
        tax_rate = re.findall(r'-?\d+\.?\d*', tax_id)[-1]  # parse `20.3`
        tax_option = re.findall(r'\[(\w+)\]', tax_id)[-1]  # parse `excluded` - removed extra space

        return {
            'id': tax_id,
            'name': tax_id,
            'rate': tax_rate,
            'price_include': {'excluded': False, 'included': True}[tax_option],
        }

    def get_weight_uom_for_converter(self):
        if not self._weight_uom:
            raise UserError(_(
                'The "Shopify Weight Unit" setting is not configured in the Sale Integration. '
                'To resolve this, deactivate and then reactivate the Sale Integration. This will '
                'populate the required weight unit setting automatically.'
            ))

        return self._weight_uom

    def get_weight_uoms(self):
        if self._weight_uom:
            return [self._weight_uom]
        return []

    def _default_product_domain(self):
        return self.get_settings_value('import_products_filter') or dict()

    def _default_order_domain(self):
        domain = dict()
        status = self.get_settings_value('receive_order_statuses')
        if status:
            domain['status'] = status

        financial_status = self.get_settings_value('receive_order_financial_statuses')
        if financial_status:
            domain['financial_status'] = financial_status

        fulfillment_status = self.get_settings_value('receive_order_fulfillment_statuses')
        if fulfillment_status:
            status_list = fulfillment_status.split(',')
            fulfilled = ShopifyOrderStatus.STATUS_FULFILLED

            if fulfilled in status_list:  # Change the `fulfilled` value to the 'shipped' value
                status_list.remove(fulfilled)
                status_list.append(ShopifyOrderStatus.SPECIAL_STATUS_SHIPPED)
                fulfillment_status = ','.join(status_list)

            domain['fulfillment_status'] = fulfillment_status

        return domain

    def _get_shopify_statuses(self):
        return ShopifyOrderStatus.all_statuses()

    def order_limit_value(self):
        return SHOPIFY_FETCH_LIMIT

    def get_customer_metafields_by_id(self, customer_id):
        metafield_data = self._graphql.get_customer_metafields_by_id(customer_id)
        return [x['node'] for x in metafield_data]

    def get_order_metafields_by_id(self, order_id):
        metafield_data = self._graphql.get_order_metafields_by_id(order_id)
        return [x['node'] for x in metafield_data]

    def get_metafields(self, entity_name):
        metafields = self._graphql.get_metafields(entity_name)

        def _serialize_node(node):
            return {
                'metafield_code': node['id'],
                'metafield_name': node['name'],
                'metafield_key': node['key'],
                'metafield_namespace': node['namespace'],
                'metafield_type': node['type']['name'],
            }

        return [_serialize_node(x['node']) for x in metafields]

    def get_order_url(self, external_order_id):
        """
        Get the order URL for the given external order ID.
        """
        return f'{self.admin_url}/admin/orders/{external_order_id}'

    def get_product_url(self, external_product_code):
        """
        Get the product URL for the given external product code.
        """
        return f'{self.admin_url}/products/{external_product_code}'
