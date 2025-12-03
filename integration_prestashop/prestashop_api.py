# See LICENSE file for full copyright and licensing details.

import itertools
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime

from prestapyt import PrestaShopWebServiceError

from odoo import _
from odoo.tools import frozendict
from odoo.exceptions import UserError
from odoo.addons.integration.tools import IS_FALSE, IS_TRUE
from odoo.addons.integration.tools import add_dynamic_kwargs, make_list_if_not
from odoo.addons.integration.api.abstract_apiclient import AbsApiClient
from odoo.addons.integration.models.sale_integration import DATETIME_FORMAT, IMPORT_EXTERNAL_BLOCK

from .presta import Client, PRESTASHOP  # noqa
from ..integration.exceptions import ApiImportError


_logger = logging.getLogger(__name__)


PRODUCT_TYPE_MAP = {
    'simple': 'product',
    'pack': 'product',
    'virtual': 'service',
}
ROOT_CMS_PAGE_CATEGORY_ID = '1'
NULL_DATETIME = '0000-00-00 00:00:00'

# We store human readable values for Delivery Time field, so we need to map them to
# PrestaShop values
PRESTASHOP_DELIVERY_TIME_MAPPING = {
    'none': '0',
    'default': '1',
    'specific': '2',
}


# TODO: all reading through pagination
class PrestaShopApiClient(AbsApiClient):

    default_receive_orders_filter = (
        '{"filter[current_state]": "[<put state id here>]"}'
    )

    settings_fields = (
        ('url', 'Shop URL', ''),
        ('admin_url', 'Admin URL', ''),
        ('key', 'Webservice Key', ''),
        ('language_id', 'Shop Language Code', ''),
        (
            'receive_orders_filter',
            'Receive Orders Filter',
            default_receive_orders_filter,
            True,
        ),
        ('import_products_filter', 'Import Products Filter', '{"active": "1"}'),
        ('id_group_shop', 'Shop Group where export products', ''),
        ('shop_ids', 'Shop ids in id_group_shop separated by comma', ''),
        (
            'PS_TIMEZONE',
            (
                'PrestaShop timezone value.'
                ' Will be automatically populated when integration is active'
            ),
            '',
        ),
        (
            'weight_uom',
            (
                'PrestaShop weight unit.'
                ' Will be automatically populated when integration is active'
            ),
            '',
        ),
        ('adapter_version', 'Version number of the api client', '0'),
        ('decimal_precision', 'Number of decimal places in the price of the exported product', '6'),
    )

    def __init__(self, settings):
        super().__init__(settings)

        api_url = '/'.join([self.get_settings_value('url').strip('/'), 'api'])
        api_key = self.get_settings_value('key')

        admin_url = self.get_settings_value('admin_url').strip('/')
        if not admin_url.endswith('index.php'):
            admin_url += '/index.php'
        self.admin_url = admin_url

        self._client = Client(
            api_url,
            api_key,
        )

        self._language_id = self.get_settings_value('language_id')
        self._weight_uom = self.get_settings_value('weight_uom')

        id_group_shop = self.get_settings_value('id_group_shop')

        shop_ids_str = self.get_settings_value('shop_ids')
        if shop_ids_str:
            shop_ids = shop_ids_str.split(',')
        else:
            shop_ids = []

        self._client.default_language_id = self._language_id
        self._client.id_group_shop = id_group_shop
        self._client.shop_ids = shop_ids
        self._client.data_block_size = self._settings['data_block_size']

        self._default_shop_id = None

    @property
    def lang(self):
        return self._language_id

    def check_connection(self):
        resources = self._client.get('')
        connection_ok = bool(resources)
        return connection_ok

    def get_api_resources(self):
        return self._client.get('')

    def get_delivery_methods(self):
        delivery_methods = self._client.model('carrier').search_read(
            filters={
                'deleted': IS_FALSE,
                'id': '![0]',  # Exclude bad carriers
            },
            fields=['id', 'name'],
        )
        return delivery_methods

    def get_single_carrier(self, external_id):
        carrier = self._client.model('carrier').search_read(
            filters={'id': external_id},
            fields=['id', 'name'],
        )
        return carrier and carrier[0] or dict()

    def get_parent_delivery_methods(self, not_mapped_id):
        id_reference = self._client.model('carrier').search_read(
            filters={'id': not_mapped_id},
            fields=['id_reference'],
        )
        reference = id_reference and id_reference[0]
        if not reference:
            return False
        family_delivery_methods = self._client.model('carrier').search_read(
            filters={'id_reference': reference['id_reference']},
            fields=['id', 'name'],
        )
        return family_delivery_methods

    @property
    def default_shop_id(self):
        if not self._default_shop_id:
            self._default_shop_id = self.get_configuration('PS_SHOP_DEFAULT')

        return self._default_shop_id

    def get_weight_uom_for_converter(self):
        if not self._weight_uom:
            raise UserError(_(
                'Sale Integration setting "PrestaShop weight unit" is not specified. '
                'Please, deactivate and then activate Sale Integration to populate it'))

        return self._weight_uom

    def get_weight_uoms(self):
        if self._weight_uom:
            return [self._weight_uom]
        return []

    def get_configuration(self, name):
        value = self._client.model('configuration').search_read(
            filters={'name': f'[{name}]'},
            fields=['value'],
            with_translation=True,
        )

        return value and value[0] if isinstance(value, list) else value

    def get_shops(self):
        shops = self._client.model('shop').search_read(
            fields=['id', 'name'],
        )
        return [(x['id'], x['name']) for x in shops]

    def get_shop_groups(self):
        shop_groups = self._client.model('shop_group').search_read(
            fields=['id', 'name'],
        )
        return [(x['id'], x['name']) for x in shop_groups]

    def get_single_tax(self, tax_id):
        taxes = self.get_taxes()
        tax_list = [x for x in taxes if x['id'] == tax_id]
        return tax_list and tax_list[0] or dict()

    def get_taxes(self):
        """Will return the following list:
            [
                {'id': '1',
                 'rate': '5.000',
                 'name': 'CA 5%',
                 'tax_groups': [{'id': '6', 'name': 'CA Standard Rate'}]
                 },
                 {'id': '3',
                  'rate': '0.000',
                  'name': 'CA-MB 8%',
                  'tax_groups': [{'id': '6', 'name': 'CA Standard Rate'}]
                 },
                 ...
            ]
        """
        # first retrieve all taxes
        taxes = self._client.model('tax').search_read(
            filters={'deleted': IS_FALSE},
            fields=['id', 'name', 'rate'],
        )

        # retrieve tax groups and convert to dictionary for fast searching
        tax_groups = self._client.model('tax_rule_group').search_read(
            filters={'deleted': IS_FALSE},
            fields=['id', 'name'],
        )

        tax_groups_dict = dict((p['id'], p['name']) for p in tax_groups)

        # retrieve all tax rules
        tax_rules = self._client.model('tax_rule').search_read(
            fields=['id_tax_rules_group', 'id_tax'],
        )

        # create dictionary of tax groups per tax
        tax_to_tax_rule_dict = defaultdict(set)
        for tax_rule in tax_rules:
            tax_id = tax_rule['id_tax']
            tax_group_id = tax_rule['id_tax_rules_group']
            tax_group_name = tax_groups_dict.get(tax_group_id)
            if tax_group_name:
                tax_group_dict = frozendict(
                    {
                        'id': tax_group_id,
                        'name': tax_group_name,
                    }
                )
                tax_to_tax_rule_dict[tax_id].add(tax_group_dict)

        for tax in taxes:
            tax['tax_groups'] = list(tax_to_tax_rule_dict.get(tax['id'], []))

        return taxes

    @staticmethod
    def create_error_message(duplicate_dict, object_name):
        """
        Format of the return message: <name> (<normalized_code>) - found in Prestashop in records:
        [<iso_code_in_presta>] <name> (id=id_in_presta), ..., ...

        :param duplicate_dict: Dictionary with duplicates
        :param object_name: name of the object for which duplicates were searched for
        """
        duplicate_message = []

        for key, vals in duplicate_dict.items():
            name, code = key[1], key[0]

            dupl_list = [f'[{d.get("iso_code")}] {d.get("name")} (id={d.get("id")})' for d in vals]

            text_error = f'{name} ({code}) - ' + \
                         _('found in Prestashop in records: ') + f'{", ".join(dupl_list)}'

            duplicate_message.append(text_error)

        if duplicate_message:
            return _('Found duplicated active %s in Prestashop, '
                     'please, fix duplicates:\n') \
                % object_name + ', \n'.join(duplicate_message)

    def _find_duplicated_objects(self, converted_and_orig_list):
        """
        Search for duplicates in a tuple list (converted_record, original_record)

        :param converted_and_orig_list: list of tuples
        :return: dictionary with duplicates
        :return format: {(converted_code, converted_name): [{'id', 'iso_code', 'name'}, [dict],...]}
        """

        transformed_unique_key_list = []
        group_unique_key_dict = defaultdict(list)

        code_name_mapping = {}
        for converted, original in converted_and_orig_list:
            converted_reference = converted['external_reference']
            converted_name = code_name_mapping.get(converted_reference)
            if not converted_name:
                converted_name = converted['name']
                code_name_mapping[converted_reference] = converted_name
            converted_key = (converted_reference, converted_name)
            transformed_unique_key_list.append((converted_key, original))

        [
            group_unique_key_dict[key].extend(list(i[1] for i in group))
            for key, group in itertools.groupby(
                transformed_unique_key_list,
                key=lambda tuple_: tuple_[0])
        ]

        return {key: val for key, val in group_unique_key_dict.items() if len(val) >= 2}

    def get_countries(self):
        countries = self._client.model('country').search_read(
            filters={'active': IS_TRUE},
            fields=['id', 'name', 'iso_code'],
        )

        converted_countries = []
        for external_country in countries:
            converted_countries.append({
                'id': external_country.get('id'),
                'name': external_country.get('name'),
                'external_reference': external_country.get('iso_code'),
            })

        converted_and_orig_list = [
            (obj_conv, obj_orig)for obj_conv, obj_orig in zip(converted_countries, countries)
        ]
        duplicates_dict = self._find_duplicated_objects(converted_and_orig_list)
        error_duplicate_message = self.create_error_message(duplicates_dict, 'countries')
        if error_duplicate_message:
            raise ApiImportError(error_duplicate_message)

        return converted_countries

    def get_states(self):
        countries = self._client.model('country').search_read(
            filters={'active': IS_TRUE},
            fields=['id', 'name', 'iso_code'],
        )

        original_state, converted_states = [], []
        state_endpoint = self._client.model('state')

        for external_country in countries:
            states = state_endpoint.search_read(
                filters={'id_country': external_country['id'], 'active': IS_TRUE},
                fields=['id', 'name', 'iso_code'],
            )

            original_state.extend(states)

            for external_state in states:
                state_code = external_state.get('iso_code')
                if '-' in state_code:
                    # In prestashop some states are defined in a strange way like (NL-DE)
                    # We need to have only state code
                    state_code = state_code.split('-')[1]
                state_reference = '{}_{}'.format(
                    external_country.get('iso_code'),
                    state_code
                )

                converted_states.append({
                    'id': external_state.get('id'),
                    'name': external_state.get('name'),
                    'external_reference': state_reference,
                })

        converted_and_orig_list = [
            (obj_conv, obj_orig) for obj_conv, obj_orig in zip(converted_states, original_state)
        ]
        duplicates_dict = self._find_duplicated_objects(converted_and_orig_list)
        error_duplicate_message = self.create_error_message(duplicates_dict, 'states')
        if error_duplicate_message:
            raise ApiImportError(error_duplicate_message)

        return converted_states

    def get_payment_methods(self):
        orders = self._client.model('order').search_read(
            fields=['id', 'payment'],
            sort='[id_DESC]',
            limit=1000,
        )
        return [{'id': x['payment']} for x in orders]

    def get_languages(self):  # TODO: not optimal, better to use id_shop: all
        # TODO: if there is only one records presta returns it as simple {} rather than
        #  list of dicts [{}]
        added_language_ids = []
        languages = []

        ext_languages = self._client.model('language').search_read(
            filters={'active': IS_TRUE},
            fields=['id', 'name', 'language_code'],
            with_translation=True,
        )

        for ext_language in ext_languages:
            if ext_language['id'] not in added_language_ids:
                language_code = ext_language.get('language_code', '').replace('-', '_')
                languages.append({
                    'id': ext_language.get('id'),
                    'name': ext_language.get('name'),
                    # Converting language code to Odoo Format
                    'external_reference': language_code,
                })
                added_language_ids.append(ext_language['id'])

        return languages

    def _exclude_broken_values(self, resource, group_field, values):
        exist_ids = []
        group_ids = list(set([x[group_field] for x in values]))
        Endpoint = self._client.model(resource)

        while group_ids:
            ids = '|'.join(group_ids[:IMPORT_EXTERNAL_BLOCK])
            records = Endpoint.search_read(
                filters={'id': f'[{ids}]'},
                fields=['id'],
            )
            group_ids = group_ids[IMPORT_EXTERNAL_BLOCK:]
            exist_ids.extend([x['id'] for x in records])

        result = filter(lambda x: x[group_field] in exist_ids, values)
        return list(result)

    def get_attributes(self):
        attributes = self._client.model('product_option').search_read(
            fields=['id', 'name'],
            with_translation=True,
        )
        return attributes

    def get_attribute_values(self):
        attribute_values = self._client.model('product_option_value').search_read(
            filters={'id_attribute_group': f'![{IS_FALSE}]'},
            fields=['id', 'name', 'id_attribute_group'],
            with_translation=True,
        )
        attribute_values_filter = self._exclude_broken_values(
            'product_option',
            'id_attribute_group',
            attribute_values,
        )
        for attr in attribute_values_filter:
            value = attr.pop('id_attribute_group')
            attr['id_group'] = value

        return attribute_values_filter

    def get_features(self):
        features = self._client.model('product_feature').search_read(
            fields=['id', 'name'],
            with_translation=True,
        )
        return features

    def get_feature_values(self):
        feature_values = self._client.model('product_feature_value').search_read(
            filters={'id_feature': f'![{IS_FALSE}]'},
            fields=['id', 'value', 'id_feature'],
            with_translation=True,
        )
        feature_values_filtered = self._exclude_broken_values(
            'product_feature',
            'id_feature',
            feature_values,
        )
        for feature in feature_values_filtered:
            value = feature.pop('id_feature')
            feature['id_group'] = value
            feature['name'] = feature['value']

        return feature_values_filtered

    def get_locations(self):
        _logger.info('Prestashop: get_locations(). Not Implemented.')
        return []

    def get_pricelists(self):
        _logger.info('Prestashop: get_pricelists()')
        groups = self._client.model('group').search_read(
            fields=['id', 'name'],
            with_translation=True,
        )
        return [{'id': IS_FALSE, 'name': _('All Groups')}, *groups]

    @add_dynamic_kwargs
    def get_categories(self, **kw):
        # Filter out category with ID = 1. See below code form Presta
        # Core/Domain/CmsPageCategory/ValueObject/CmsPageCategoryId.php#L39
        # public const ROOT_CMS_PAGE_CATEGORY_ID = 1;
        filters = {
            'id': '![%s]' % ROOT_CMS_PAGE_CATEGORY_ID,
        }

        integration = self._get_integration(kw)
        if not integration.import_inactive_categories:
            filters['active'] = IS_TRUE

        categories = self._client.model('category').search_read(
            filters=filters,
            fields=['id', 'name', 'id_parent', 'is_root_category'],
            with_translation=True,
        )

        result_categories = []
        for cat in categories:
            if cat['id_parent'] == ROOT_CMS_PAGE_CATEGORY_ID:
                cat['id_parent'] = IS_FALSE
            result_categories.append(cat)

        return result_categories

    def get_sale_order_statuses(self):
        external_order_states = self._client.model('order_state').search_read(
            filters={'deleted': IS_FALSE},
            fields=['id', 'name', 'template'],
            with_translation=True,
        )

        order_states = []
        for ext_order_state in external_order_states:
            order_states.append({
                'id': ext_order_state.get('id'),
                'name': ext_order_state.get('name'),
                # We cannot add any unique internal reference as in Prestashop it doesn't exist
                'external_reference': False,
            })
        return order_states

    def get_product_template_ids(self):
        template_ids = self._client.model('product').search_read_by_blocks(
            filters=self._get_product_filter_hook(),
            fields=self._get_product_fields_hook(['id']),
        )
        template_ids = self._filter_templates_hook(template_ids)
        return template_ids and [x['id'] for x in template_ids] or []

    @add_dynamic_kwargs
    def get_product_templates(self, template_ids, **kw):
        integration = self._get_integration(kw)
        ref_field = integration._get_product_reference_name()
        barcode_field = integration._get_product_barcode_name()

        ecommerce_ref_tmpl = integration._template_field_name_to_ecommerce_name(ref_field)
        ecommerce_barcode_tmpl = integration._template_field_name_to_ecommerce_name(barcode_field)
        ecommerce_ref_variant = integration._variant_field_name_to_ecommerce_name(ref_field)
        ecommerce_barcode_variant = integration._variant_field_name_to_ecommerce_name(barcode_field)

        external_templates = self._client.model('product').search_read(
            filters={'id': '[%s]' % '|'.join(template_ids)},
            fields=['id', 'name', ecommerce_ref_tmpl, ecommerce_barcode_tmpl],
        )

        result = {
            str(x['id']): {
                'id': str(x['id']),
                'name': x['name'],
                'barcode': x.get(ecommerce_barcode_tmpl) or None,
                'external_reference': x.get(ecommerce_ref_tmpl) or None,
                'variants': [],
            }
            for x in external_templates
        }

        product_variants = self._client.model('combination').search_read(
            filters={'id_product': '[%s]' % '|'.join(template_ids)},
        )
        for variant in product_variants:
            template_id = str(variant['id_product'])

            if template_id in result:
                result[template_id]['variants'].append({
                    'id': self._build_product_external_code(template_id, variant['id']),
                    'name': result[template_id]['name'],
                    'external_reference': variant.get(ecommerce_ref_variant) or None,
                    'barcode': variant.get(ecommerce_barcode_variant) or None,
                    'ext_product_template_id': template_id,
                    'attribute_value_ids': self._parse_attribute_value_ids(variant),
                })

        return result

    def _parse_attribute_value_ids(self, variant):
        value_list = variant \
            .get('associations', {}) \
            .get('product_option_values', {}) \
            .get('product_option_value', [])

        return [x['id'] for x in make_list_if_not(value_list)]

    def get_customer_ids(self, date_since=None):
        customer_ids = self._client.model('customer').search_read_by_blocks(
            filters=date_since and {'date_upd': '>[%s]' % date_since},
            fields=['id'],
            date='1',
        )

        return customer_ids and [x['id'] for x in customer_ids] or []

    def get_customer_and_addresses(self, customer_id):
        customer = self._client.model('customer').read_one(customer_id)
        addresses = self._client.model('address').search_read(
            filters={'id_customer': customer_id},
        )

        parsed_customer = self._parse_customer(customer)
        parsed_addresses = [self._parse_address(customer, x) for x in addresses]

        return parsed_customer, parsed_addresses

    def create_webhooks_from_routes(self, routes_dict):
        result = dict()

        for name_tuple, route in routes_dict.items():
            webhook = self._client.model('webhook')

            webhook.url = route
            webhook.hook = name_tuple[-1]  # --> technical_name
            webhook.real_time = IS_TRUE
            webhook.active = IS_TRUE
            webhook.retries = 0

            webhook.save()
            result[name_tuple] = str(webhook.id)

        return result

    def unlink_existing_webhooks(self, external_ids=None):
        if not external_ids:
            return False

        webhooks = self._client.model('webhook').search({
            'id': '[%s]' % '|'.join(external_ids)
        })
        result = webhooks.delete()
        return result

    def export_category(self, category):
        presta_category = self._client.model('category').create(category)
        return presta_category.id

    @add_dynamic_kwargs
    def find_existing_template(self, template, **kw):
        # we try to search existing product template ONLY if there is no external_id for it
        # If there is external ID then we already mapped products and we do not need to search
        if template['external_id']:
            return False

        products = template['products']
        integration = self._get_integration(kw)

        ref_field = integration._get_product_reference_name()
        ecommerce_ref_variant = integration._variant_field_name_to_ecommerce_name(ref_field)

        product_refs = [str(x['fields'].get(ecommerce_ref_variant)) for x in products]

        # Let's validate if all found products belong to the same product template
        ids_set = self._find_product_by_references(product_refs)(**kw)
        # If nothing found, then just return False
        if not len(ids_set):
            return False

        # If more than one product id found - then we found references,
        # but they all belong to different products and we need to inform user about it
        # So he can fix on Prestashop side
        # Because in Odoo it is single product template, and in Prestashop - separate
        # product templates. That should not be allowed. Note that after previous check on
        # duplicates most likely it will not be possible, this check is just to be 100% sure
        if len(ids_set) > 1:
            error_message = _(
                'Product %s(s) "%s" were found in multiple Prestashop Products: %s. '
                'This is not allowed as in Odoo same %ss already belong to '
                'single product template and its variants. Structure of Odoo products '
                'and Prestashop Products should be the same!'
            ) % (
                ecommerce_ref_variant,
                ', '.join(product_refs),
                ', '.join(list(ids_set)),
                ecommerce_ref_variant,
            )
            raise UserError(error_message)

        presta_product_id = list(ids_set)[0]
        # Check if products in Odoo has the same amount of variants as in Prestashop
        product_combinations = self._client.model('combination').search_read(
            filters={'id_product': f'[{presta_product_id}]'},
        )
        # counting expected variants excluding "virtual" variant
        template_variants_count = len([x for x in products if x['attribute_values']])
        if template_variants_count != len(product_combinations):
            error_message = _(
                'Amount of combinations in Prestashop is %s. While amount in Odoo is %s. '
                'Please, check the product with id %s in Prestashop and make sure '
                'it has the same amount of combinations as variants in Odoo '
                '(with enabled integration "%s").'
            ) % (
                len(product_combinations),
                template_variants_count,
                presta_product_id,
                self._integration_name,
            )
            raise UserError(error_message)

        for variant in product_combinations:
            # Make sure that reference is set on the combination
            if variant['id'] != IS_FALSE and not variant[ecommerce_ref_variant]:
                error_message = _(
                    'Product with id "%s" do not have references on all combinations. '
                    'Please, add them and relaunch product export') % presta_product_id
                raise UserError(error_message)

            attribute_values_from_presta = self._parse_attribute_value_ids(variant)
            attribute_values_from_odoo = list(filter(
                lambda x: x['fields'].get(ecommerce_ref_variant) == variant[ecommerce_ref_variant],
                products,
            ))
            if not len(attribute_values_from_odoo):
                error_message = _(
                    'There is no variant in Odoo with %s "%s" that corresponds to '
                    'Prestashop product %s.'
                ) % (ecommerce_ref_variant, variant[ecommerce_ref_variant], presta_product_id)
                raise UserError(error_message)

            attribute_values_from_odoo = [
                x['external_id'] for x in attribute_values_from_odoo[0]['attribute_values']
            ]
            if Counter(attribute_values_from_odoo) != Counter(attribute_values_from_presta):
                error_message = _(
                    'Prestashop Variant with %s "%s" has variant values "%s". While same '
                    'Odoo Variant has attribute values "%s". Products in Prestashop and Odoo '
                    'with the same %s should have the same combination of attributes.'
                ) % (
                    ecommerce_ref_variant,
                    variant[ecommerce_ref_variant],
                    attribute_values_from_presta,
                    attribute_values_from_odoo,
                    ecommerce_ref_variant,
                )
                raise UserError(error_message)

        return presta_product_id

    @add_dynamic_kwargs
    def _find_product_by_references(self, product_refs, **kw):
        if product_refs and not isinstance(product_refs, list):
            product_refs = [str(product_refs)]

        integration = self._get_integration(kw)
        ref_field = integration._get_product_reference_name()

        ecommerce_ref_tmpl = integration._template_field_name_to_ecommerce_name(ref_field)
        ecommerce_ref_variant = integration._variant_field_name_to_ecommerce_name(ref_field)

        templates, variants = self._get_products_and_variants(
            ['id', 'name', ecommerce_ref_tmpl],
            ['id', 'id_product', ecommerce_ref_variant],
            {ecommerce_ref_tmpl: '[%s]' % '|'.join(product_refs)},
        )
        ids_set = {str(x['id']) for x in templates}
        ids_set.update([str(x['id_product']) for x in variants])
        return ids_set

    def validate_template(self, template):
        _logger.info('Prestashop: validate_template().')
        mappings_to_delete = []

        # (1) if template with such external id exists?
        presta_product_id = template['external_id']
        if presta_product_id:
            presta_product = self._client.model('product').search_read(
                filters={'id': presta_product_id},
                fields=['id']
            )
            if len(presta_product) == 0:
                mappings_to_delete.append({
                    'model': 'product.template',
                    'external_id': str(presta_product_id),
                })

        # (2) if part of the variants has no external_id?
        mappings_to_update = self.parse_mappings_to_update(template['products'])

        # (3) if variant with such external id exists?
        for variant in template['products']:
            variant_ext_id = variant['external_id']
            if not variant_ext_id:
                continue

            __, presta_combination_id = self._parse_product_external_code(variant_ext_id)

            if presta_combination_id:
                presta_combination = self._client.model('combination').search_read(
                    filters={'id': presta_combination_id},
                    fields=['id']
                )
                if len(presta_combination) == 0:
                    mappings_to_delete.append({
                        'model': 'product.product',
                        'external_id': str(variant_ext_id),
                    })
        return mappings_to_delete, mappings_to_update

    @add_dynamic_kwargs
    def export_template(self, template, **kw):
        _logger.info('Presta: export_template(%s)', template['external_id'])
        integration = self._get_integration(kw)
        ref_field = integration._get_product_reference_name()
        ecommerce_ref_variant = integration._variant_field_name_to_ecommerce_name(ref_field)

        product = self._client.model('product').get(template['external_id'])
        self._fill_product(product, template)

        #  Removes inactive internal variant-associated external variants from a template.
        variants = template['products']
        variant_reference_list = [
            rec['fields'].get(ecommerce_ref_variant, False) for rec in variants
        ]
        product_combinations = product.get_combinations()
        for combination in product_combinations:
            if getattr(combination, ecommerce_ref_variant) not in variant_reference_list:
                combination.delete()

        # we save product type here, before save, because it
        # got overridden with incorrect type after save
        presta_product_type = product.type
        product.save()
        product_id = str(product.id)

        mappings = [{
            'model': 'product.template',
            'id': template['id'],
            'external_id': product_id,
        }]

        if presta_product_type == 'pack':
            stock = self._client.model('stock_available').search({
                'id_product': product_id,
            })
            stock.save()

        for variant in variants:
            if len(variants) == 1:
                combination_id = False
            else:
                combination_id = self._export_product(product_id, variant)

            mappings.append({
                'model': 'product.product',
                'id': variant['id'],
                'external_id': self._build_product_external_code(product_id, combination_id)
            })

        return mappings

    def _fill_product(self, product, vals):
        # We do not update the product type during subsequent exports, as the product type
        # typically remains unchanged after the initial creation. This code is primarily
        # intended for setting initial product properties.
        if not product.type:
            product.type = 'simple'
            product.state = IS_TRUE
            product.is_virtual = IS_FALSE

            if vals['type'] == 'service':
                product.type = 'virtual'
                product.is_virtual = IS_TRUE

        # We do not support "Minimum quantity for sale" as a feature
        # This field will have None value during first export
        if product.minimal_quantity is None or product.minimal_quantity == '0':
            product.minimal_quantity = '1'

        for field, value in vals['fields'].items():
            if self._is_translated_field(value):
                self._fill_translated_field(product, field, value)
            else:
                setattr(product, field, value)

        if vals['kits'] and len(vals['products']) <= 1:
            product.type = 'pack'
            kit = vals['kits'][0]
            bundle_products = []
            for component in kit['components']:
                bundle_products.append({
                    'id': component['product_id'],
                    'quantity': component['qty'],
                })

            product.product_bundle = bundle_products

    @staticmethod
    def _is_translated_field(value):
        return isinstance(value, dict) and value.get('language')

    def _fill_translated_field(self, model, field, value):
        for lang_id, translation in value['language'].items():
            setattr(model.lang(lang_id), field, translation or '')

    def _export_product(self, presta_product_id, product):
        if product['external_id']:
            product_id, combination_id = self._parse_product_external_code(product['external_id'])
        else:
            product_id, combination_id = presta_product_id, None

        if combination_id:
            combination = self._client.model('combination').get(combination_id)
            self._fill_combination(combination, product, product_id)
            combination.save()
        else:
            combination = self._client.model('combination')
            prestashop_product = self._client.model('product').get(product_id)
            if product['attribute_values']:
                self._fill_combination(combination, product, product_id)
                combination = prestashop_product.add_combination(combination)
            else:
                combination.id = IS_FALSE  # update template with some values

        return combination.id

    def _fill_combination(self, combination, vals, product_id):
        combination.id_product = product_id

        for field, value in vals['fields'].items():
            if self._is_translated_field(value):
                self._fill_translated_field(combination, field, value)
            else:
                setattr(combination, field, value)

        # We do not support "Minimum quantity for sale" as a feature
        # We do not support "Minimum quantity for sale" as a feature
        # This field will have None value during first export
        if combination.minimal_quantity is None or combination.minimal_quantity == '0':
            combination.minimal_quantity = '1'

        if vals['attribute_values']:
            attribute_value_ids = [x['external_id'] for x in vals['attribute_values']]
            product_option_values = self._client.model('product_option_value').get(
                attribute_value_ids,
            )
            combination.product_option_values = product_option_values

    def get_special_prices(self, external_codes, **params):
        shops = self.get_shops()
        setting_shop_ids = self.get_settings_value('shop_ids')

        if setting_shop_ids:
            join_shop_ids = '|'.join(map(lambda x: x.strip(), setting_shop_ids.split(',')))
            params['id_shop'] = f'[{join_shop_ids}]'
        elif len(shops) == 1:
            params['id_shop'] = f'[{IS_FALSE}|{shops[0][0]}]'

        Endpoint = self._client.model('specific_price')

        # 1. Fetch records for specific `id_group`
        kwargs = {
            'id_shop': IS_FALSE,
            'id_shop_group': IS_FALSE,
            **params,
        }
        record_list = Endpoint.search_read_by_blocks(filters=kwargs)

        # 2. Serialize data
        data = defaultdict(list)
        for record in record_list:
            id_product = record['id_product']
            if external_codes and id_product not in external_codes:
                continue

            vals = self._parse_prices_vals(record)
            key = (id_product, record['id_product_attribute'])
            data[key].append(vals)

        return data

    def _parse_prices_vals(self, record):
        price = record['price']
        if price.startswith('-'):
            price = False

        vals = {
            'id': int(record['id']),
            'fixed_price': float(price),
            'reduction': float(record['reduction']),
            'reduction_type': record['reduction_type'],
            'min_quantity': float(record['from_quantity']),
            'date_end': self._parse_price_rule_date(record['to']),
            'date_start': self._parse_price_rule_date(record['from']),
            'reduction_tax_included': record['reduction_tax'] == IS_TRUE,
        }
        return vals

    def export_pricelists(self, data, updating=False):
        tmpl_data, var_data_list = data

        # 1. Export `template` prices
        self._export_specific_price_multi(tmpl_data, updating)

        # 2. Export `variant` prices
        for variant_data in var_data_list:
            self._export_specific_price_multi(variant_data, updating)

        return tmpl_data, var_data_list

    def _export_specific_price_multi(self, data_class, updating):
        force_unlink = data_class.force_sync_pricelist or not updating
        tmpl_id, comb_id = data_class._parse_template_and_combination()
        external_group_ids = data_class.join_external_groups()

        existing_price_ids = self._client.model('specific_price').search({
            'id_product': f'[{tmpl_id}]',
            'id_product_attribute': f'[{comb_id}]',
            'id_group': f'[{external_group_ids}]',
        })
        if existing_price_ids:
            if force_unlink:
                res = existing_price_ids.delete()
            else:
                to_unlink_prices = self._find_to_unlink_prices(
                    data_class,
                    existing_price_ids,
                )
                to_unlink_prices_ids = to_unlink_prices.ids
                res = to_unlink_prices.delete()

                data_class.update_unlinked(to_unlink_prices_ids)

            if not res:
                data_class.update_unlinked([])
                return data_class

        for price_rule in data_class.prices:
            res = self._export_specific_price_one(tmpl_id, comb_id, price_rule)
            data_class.update_result((price_rule['_item_id'], res))

        return data_class

    def _export_specific_price_one(self, tmpl_id, comb_id, price_rule):
        key_value = self._prepare_specific_price_vals(tmpl_id, comb_id, price_rule)
        specific_price_id = self._client.model('specific_price')

        for key, value in key_value.items():
            setattr(specific_price_id, key, value)

        res = specific_price_id.save()
        return res and res.get('id') and int(res.get('id'))

    def _prepare_specific_price_vals(self, tmpl_id, comb_id, rule):
        vals = self._get_default_price_vals()
        extra_vals = {
            'id_product': tmpl_id,
            'id_product_attribute': comb_id,
            'price': str(rule['price'] or -1),  # Negative number is important for non `fixed-price`
            'reduction': str(rule['reduction']),
            'reduction_type': rule['reduction_type'],
            'id_group': rule['external_group_id'],
            'from_quantity': str(rule['min_quantity']),
            'to': self._format_price_rule_date(rule['date_end']),
            'from': self._format_price_rule_date(rule['date_start']),
            'reduction_tax': [IS_FALSE, IS_TRUE][rule['reduction_tax_included']],
        }
        vals.update(extra_vals)
        return vals

    def _find_to_unlink_prices(self, data_class, existing_item_ids):
        # 1. Filter previously added
        outdated_external_ids = data_class.parsed_external_items()
        ids_to_unlink = [
            x.id for x in existing_item_ids if x.id in outdated_external_ids
        ]
        # 2. Filter records with the same period of time
        twin_list = [
            (
                self._format_price_rule_date(x['date_start']),
                self._format_price_rule_date(x['date_end']),
            )
            for x in data_class.prices
        ]

        for rec in existing_item_ids:
            key = (getattr(rec, 'from'), getattr(rec, 'to'))
            if key in twin_list:
                ids_to_unlink.append(rec.id)

        ids_to_unlink.sort()
        existing_item_ids._ids = list(set(ids_to_unlink))
        return existing_item_ids

    def _format_price_rule_date(self, value):
        if not value:
            return NULL_DATETIME
        return datetime.strftime(value, DATETIME_FORMAT)

    def _parse_price_rule_date(self, value):
        if value == NULL_DATETIME:
            return False
        return datetime.strptime(value, DATETIME_FORMAT)

    @staticmethod
    def _get_default_price_vals():
        return {
            'id_shop': IS_FALSE,
            'id_cart': IS_FALSE,
            'id_country': IS_FALSE,
            'id_currency': IS_FALSE,
            'id_customer': IS_FALSE,
            'id_shop_group': IS_FALSE,
            'id_specific_price_rule': IS_FALSE,
        }

    def export_images(self, images_data):
        result = list()
        external_template_id = images_data['template']['id']
        external_template = self._client.model('product').get(external_template_id)

        # 1. Drop old images
        external_images = external_template.get_images()
        for image in external_images:
            image.delete()

        # 2. Save template images (default + extra)
        default_image_dict = images_data['template']['default']
        if default_image_dict:
            image_bin = default_image_dict['data']
            img_id = external_template.add_image(image_bin)
            result.append(img_id)

        for extra_image_dict in images_data['template']['extra']:
            image_bin = extra_image_dict['data']
            img_id = external_template.add_image(image_bin)
            result.append(img_id)

        # 3. Save variants images (default + extra)
        for variant_image_data in images_data['products']:
            default_image_dict = variant_image_data['default']

            if default_image_dict:
                image_bin = default_image_dict['data']
                img_id = external_template.add_image(image_bin)

                __, external_variant_id = self._parse_product_external_code(
                    variant_image_data['id'],
                )
                if external_variant_id:
                    external_variant = external_template.get_combination(external_variant_id)
                    if external_variant:
                        external_variant.assign_image(img_id)
                    result.append(img_id)

            for extra_image_dict in variant_image_data['extra']:
                image_bin = extra_image_dict['data']
                img_id = external_template.add_image(image_bin)
                result.append(img_id)

        return result

    def export_attribute(self, attribute):
        product_option = self._client.model('product_option')

        self._fill_translated_field(
            product_option,
            'name',
            attribute['name'],
        )

        self._fill_translated_field(
            product_option,
            'public_name',
            attribute['name'],
        )

        product_option.group_type = 'select'
        product_option.save()

        return product_option.id

    def export_attribute_value(self, attribute_value):
        product_option_value = self._client.model('product_option_value')

        self._fill_translated_field(
            product_option_value,
            'name',
            attribute_value['name'],
        )

        product_option_value.id_attribute_group = attribute_value['attribute']
        product_option_value.save()

        return product_option_value.id

    def export_feature(self, feature):
        product_feature = self._client.model('product_feature')

        self._fill_translated_field(
            product_feature,
            'name',
            feature['name'],
        )

        product_feature.save()

        return product_feature.id

    def export_feature_value(self, feature_value):
        product_feature_value = self._client.model('product_feature_value')

        self._fill_translated_field(
            product_feature_value,
            'value',
            feature_value['name'],
        )

        product_feature_value.id_feature = feature_value['feature_id']
        product_feature_value.save()

        return product_feature_value.id

    @add_dynamic_kwargs
    def order_fetch_kwargs(self, **kw):
        limit = self.order_limit_value()
        integration = self._get_integration(kw)
        receive_from = integration.last_receive_orders_datetime_str
        cut_off_datetime = integration.orders_cut_off_datetime_str

        options = {
            'date': IS_TRUE,
            'display': 'full',
            'sort': '[date_upd_asc]',
            'limit': limit,
            'filter[date_upd]': f'>[{receive_from}]',
        }

        if cut_off_datetime:
            options['filter[date_add]'] = f'>[{cut_off_datetime}]'

        # Update values from stored settings
        settings_filter = self.get_settings_value('receive_orders_filter')
        options.update(settings_filter)

        return {
            'options': options,
        }

    @add_dynamic_kwargs
    def receive_orders(self, **kw):
        _logger.info('Prestashop: receive_orders()')

        # For using the `search_read` instead of the `get` method need to migrate params:
        # filter[current_state] --> current_state, etc
        kwargs = self.order_fetch_kwargs()(**kw)
        response = self._client.get('orders', **kwargs)['orders']
        orders = response['order'] if response else list()

        result = list()
        for order in make_list_if_not(orders):
            data = self._get_input_file_data(order)
            vals = dict(
                id=order['id'],
                data=data,
                updated_at=order['date_upd'],
                created_at=order['date_add'],
            )
            result.append(vals)

        return result

    def receive_order(self, order_id):
        order = self._client.model('order').read_one(order_id)
        if not order:
            return order

        order_data = self._get_input_file_data(order)
        return {
            'id': order_id,
            'data': order_data,
        }

    def _get_messages_list(self, order_id):
        messages = self._client.model('message').search_read(
            filters={'id_order': order_id},
            with_translation=True,
        )
        return messages

    def _get_input_file_data(self, order):
        customer = self._client.model('customer').read_one(order['id_customer'])

        address_endpoint = self._client.model('address')
        delivery_address = address_endpoint.read_one(order['id_address_delivery'])
        invoice_address = address_endpoint.read_one(order['id_address_invoice'])

        input_file_data = {
            'order': order,
            'customer': customer,
            'delivery_address': delivery_address,
            'invoice_address': invoice_address,
            'messages': self._get_messages_list(order['id']),
            'payment_transactions': self._get_payment_transactions(order['reference'])
        }
        return input_file_data

    def _get_carrier_tax_ids(self, carrier_id, country_id, state_id, postcode):
        if not carrier_id or carrier_id == IS_FALSE:
            return list(), IS_FALSE

        tax_rule_group = self._client.model('carrier').search_read(
            filters={'id': carrier_id},
            fields=['id_tax_rules_group'],
        )

        tax_rule_group_id = tax_rule_group and tax_rule_group[0]['id_tax_rules_group']['value']

        return self._get_taxes_by_tax_rule(tax_rule_group_id, country_id, state_id, postcode)

    def _get_taxes_by_tax_rule(self, tax_rule_group_id, country_id, state_id, postcode):
        # Based on https://github.com/
        #     PrestaShop/PrestaShop/blob/develop/classes/tax/TaxRulesTaxManager.php#L74

        # behavior:
        #     '0' - this tax only
        #     '1' - combine
        #     '2' - one after another
        first_row = True
        behavior = IS_FALSE
        tax_ids = list()

        if not tax_rule_group_id or not country_id \
                or tax_rule_group_id == IS_FALSE or country_id == IS_FALSE:
            return tax_ids, behavior

        tax_rules = self._client.get(
            'tax_rules',
            options={
                'filter[id_country]': f'[{country_id}]',
                'filter[id_state]': f'[0|{state_id}]',
                'filter[id_tax_rules_group]': f'[{tax_rule_group_id}]',
                'display': '[id,id_state,zipcode_from,zipcode_to,id_tax,behavior]',
                'sort': '[zipcode_from_DESC,zipcode_to_DESC,id_state_DESC]',
            }
        )

        tax_rules = tax_rules['tax_rules'] and tax_rules['tax_rules']['tax_rule']

        if not tax_rules:
            return tax_ids, behavior

        if isinstance(tax_rules, dict):
            tax_rules = [tax_rules]

        if not postcode:
            postcode = ''

        tax_rules = list(
            filter(
                lambda x: (x['zipcode_from'] <= postcode <= x['zipcode_to'])
                or (x['zipcode_to'] == IS_FALSE and x['zipcode_from'] in [IS_FALSE, postcode]),
                tax_rules,
            )
        )

        for rule in tax_rules:
            tax_ids.append(rule['id_tax'])

            if first_row:
                behavior = rule['behavior']
                first_row = False

            if rule['behavior'] == IS_FALSE:
                break

        return tax_ids, behavior

    def _get_payment_transactions(self, order_ref):
        payments = self._client.model('order_payment').search_read(
            filters={'order_reference': order_ref},
            fields=['id', 'amount', 'transaction_id', 'date_add', 'id_currency', 'payment_method'],
        )

        payment_transactions = []

        for payment in payments:
            if payment['transaction_id']:
                presta_currency = self._client.model('currency').search_read(
                    filters={
                        'id': payment['id_currency'],
                    },
                    fields=['iso_code'],
                )
                currency = presta_currency and presta_currency[0] or dict()
                vals = {
                    'id': payment['id'],
                    'order_id': order_ref,
                    'gateway': payment['payment_method'],
                    'transaction_id': '%s (%s): %s' % (order_ref,
                                                       payment['payment_method'],
                                                       payment['transaction_id']),
                    'transaction_date': payment['date_add'],
                    'amount': float(payment['amount']),
                    'currency': currency.get('iso_code', ''),
                }
                payment_transactions.append(vals)
        return payment_transactions

    @add_dynamic_kwargs
    def parse_order(self, input_file, **kw):
        _logger.info('WooCommerce: parse_order() from input file.')
        integration = self._get_integration(kw)

        order = input_file['order']
        customer = input_file['customer']
        delivery_address = input_file['delivery_address']
        invoice_address = input_file['invoice_address']
        messages = input_file['messages']
        payment_transactions = input_file['payment_transactions']

        is_cancelled = False
        status_code = order['current_state']
        sub_status_id = kw['_env']['sale.order.sub.status']\
            .from_external(integration, status_code)
        if integration.sub_status_cancel_id and integration.sub_status_cancel_id == sub_status_id:
            is_cancelled = True

        delivery_notes = ''
        if messages:
            delivery_notes_list = [
                msg.get('message') for msg in messages if msg.get('private') == IS_FALSE
            ]
            delivery_notes = '\n'.join(delivery_notes_list)

        order_rows = make_list_if_not(order['associations']['order_rows']['order_row'])

        presta_currency = self._client.model('currency').search_read(
            filters={
                'id': order['id_currency'],
            },
            fields=['iso_code'],
        )
        currency = presta_currency and presta_currency[0] or dict()

        carrier_tax_ids, carrier_tax_behavior = self._get_carrier_tax_ids(
            order['id_carrier'],
            delivery_address.get('id_country'),
            delivery_address.get('id_state'),
            delivery_address.get('postcode'),
        )
        wrapping_tax_ids, wrapping_tax_behavior = self._get_taxes_by_tax_rule(
            self.get_configuration('PS_GIFT_WRAPPING_TAX_RULES_GROUP'),
            delivery_address.get('id_country'),
            delivery_address.get('id_state'),
            delivery_address.get('postcode'),
        )

        parsed_order = {
            'id': order['id'],
            'ref': order['reference'],
            'current_order_state': order['current_state'],
            'date_order': order['date_add'],
            'integration_workflow_states': [order['current_state']],
            'currency': currency.get('iso_code', ''),
            'lines': [self._parse_order_row(order['id'], x) for x in order_rows],
            'payment_method': order['payment'],
            'payment_transactions': payment_transactions,
            'amount_total': (
                float(order['total_products_wt']) + float(order['total_shipping_tax_incl'])
                + float(order['total_wrapping_tax_incl']) - float(order['total_discounts_tax_incl'])
            ),
            'delivery_data': {
                'delivery_notes': delivery_notes,
                'shipping_cost_tax_excl': float(order['total_shipping_tax_excl']),
                'carrier': {
                    'id': order['id_carrier'] if order['id_carrier'] != IS_FALSE else '',
                    'name': '',
                },
                'shipping_cost': float(order['total_shipping']),
                'carrier_tax_rate': float(order['carrier_tax_rate']),
                'taxes': carrier_tax_ids,
                'carrier_tax_behavior': carrier_tax_behavior,  # TODO: what we have to do with that
            },
            'discount_data': {
                'total_discounts_tax_incl': float(order['total_discounts_tax_incl']),
                'total_discounts_tax_excl': float(order['total_discounts_tax_excl']),
            },
            'gift_data': {
                'do_gift_wrapping': order['gift'] == '1',
                'gift_message': order['gift_message'],
                'wrapping_tax_ids': wrapping_tax_ids,
                'total_wrapping_tax_incl': float(order['total_wrapping_tax_incl']),
                'total_wrapping_tax_excl': float(order['total_wrapping_tax_excl']),
                'wrapping_tax_behavior': wrapping_tax_behavior,  # TODO: what we have to do with ?
                'recycled_packaging': order['recyclable'] == '1',  # TODO: handle in future
            },
            'order_risks': list(),
            'order_transactions': list(),
            'external_tags': list(),
            'is_cancelled': is_cancelled,
        }

        if customer:
            parsed_order['customer'] = self._parse_customer(customer)

        if delivery_address:
            parsed_order['shipping'] = self._parse_address(customer, delivery_address)

        if invoice_address:
            parsed_order['billing'] = self._parse_address(customer, invoice_address)

        return parsed_order

    @staticmethod
    def _parse_customer(customer):
        return {
            'id': customer['id'],
            'pricelist_id': customer['id_default_group'],
            'person_name': ' '.join([customer['firstname'], customer['lastname']]),
            'email': customer['email'],
            'language': customer['id_lang'],
            'newsletter': customer['newsletter'],
            'newsletter_date_add': customer['newsletter_date_add'],
            'customer_date_add': customer['date_add'],
        }

    @staticmethod
    def _parse_address(customer, presta_address):
        """
        we add customer id to address id to distinguish them from each other
        """

        if not presta_address:
            return {}

        address = {
            'id': '',
            'person_name': ' '.join([presta_address['firstname'], presta_address['lastname']]),
            'email': customer.get('email', ''),
            'person_id_number': presta_address['dni'],
            'company_name': presta_address['company'],
            'company_reg_number': presta_address['vat_number'],
            'street': presta_address['address1'],
            'street2': presta_address['address2'],
            'city': presta_address['city'],
            'country': presta_address['id_country'],
            'state': presta_address['id_state'] if presta_address['id_state'] != IS_FALSE else '',
            'phone': presta_address['phone'],
            'mobile': presta_address['phone_mobile'],
            'other': presta_address['other'],
        }

        if presta_address.get('postcode'):
            address['zip'] = presta_address['postcode']

        if customer.get('id_lang'):
            address['language'] = customer.get('id_lang')

        return address

    def _parse_order_row(self, order_id, row):
        details = self._client.model('order_detail').read_one(row['id'])
        assert details

        tax = details['associations']['taxes'].get('tax')
        if not tax:
            taxes = []
        else:
            taxes = [x['id'] for x in make_list_if_not(tax) if x['id'] != IS_FALSE]

        product_id = self._build_product_external_code(
            row['product_id'],
            row['product_attribute_id'],
        )
        return {
            'id': row['id'],
            'product_id': product_id,
            'product_uom_qty': int(row['product_quantity']),
            'price_unit': float(row['unit_price_tax_excl']),
            'price_unit_tax_incl': float(row['unit_price_tax_incl']),
            'taxes': taxes,
        }

    def export_inventory(self, inventory):
        _logger.info('Prestashop: export_inventory()')
        results = list()
        for product_combination_id, inventory_item_list in inventory.items():
            product_id, combination_id = product_combination_id.split('-')

            # find stock for combination
            stock = self._client.model('stock_available').search({
                'id_product': product_id,
                'id_product_attribute': combination_id,
            })

            if stock:
                result = []
                # There can be multiple stocks if customer has multiple shops
                # We push the same quantity to all stocks
                for stock_item in stock:
                    inventory_item = inventory_item_list[0]
                    stock_item.quantity = int(inventory_item['qty'])

                    message = ''
                    result.append(stock_item.save())
            else:
                result, message = False, 'Stock resource not found'

            results.append((product_combination_id, result, message))
        return results

    def export_tracking(self, sale_order_id, tracking_data_list):
        tracking = ', '.join(set([x['tracking'] for x in tracking_data_list]))

        order_carrier = self._client.model('order_carrier').search({
            'id_order': sale_order_id,
        })

        # TODO: check with integrational test

        order_carrier.tracking_number = tracking
        result = order_carrier.save()
        return result

    def export_sale_order_status(self, vals):
        status = vals['status']
        order_id = vals['order_id']

        order = self._client.model('order').get(order_id)
        order.current_state = status

        if vals.get('delivery_date'):
            order.delivery_date = vals.get('delivery_date', NULL_DATETIME)

        return order.save()

    def get_product_for_import(self, product_code, import_images=False, id_shop=False):
        shop_filter = {'id_shop': id_shop} if id_shop else {}
        # Get product
        presta_template = self._client.model('product').search_read(
            filters={'id': product_code},
            with_translation=True,
            **shop_filter,
        )

        if not presta_template:
            raise UserError(
                _('Product with id "%s" does not exist in PrestaShop') % product_code
            )

        if isinstance(presta_template, list):
            presta_template = presta_template[0]

        # Get combinations
        presta_variants = self._client.model('combination').search_read(
            filters={'id_product': product_code},
            **shop_filter,
        )

        images_hub = {
            'images': dict(),  # 'images': {'image_id': bin-data,}
            'variants': dict(),  # variants: {'variant_id': [image-ids],}
        }

        # Handle a variant marked as default firstly.
        presta_variants = sorted(presta_variants, key=lambda x: x['default_on'], reverse=True)
        for presta_variant in presta_variants:
            presta_variant['template'] = presta_template

        for variant in presta_variants:
            variant_id = self._build_product_external_code(
                variant['id_product'],
                variant['id'],
            )
            image_list = make_list_if_not(variant['associations']['images'].get('image', []))

            if image_list:
                images_hub['variants'][variant_id] = [
                    image['id'] for image in image_list if image['id'] != IS_FALSE
                ]

        bom_components = []
        external_components = presta_template['associations']['product_bundle'].get('product', [])

        for component in make_list_if_not(external_components):
            bundle_product_id = component['id']

            if 'id_product_attribute' not in component:
                raise ApiImportError(_(
                    'You are using Prestashop 1.6 version that has bugs related to product packs. '
                    'Please, ask your Prestashop developer to contact support@ventor.tech for '
                    'details on what should be fixed in Prestashop: product=%s, product_bundle=%s'
                ) % (product_code, bundle_product_id))

            complex_code = self._build_product_external_code(
                bundle_product_id,
                component['id_product_attribute'],
            )
            bom_components.append({
                'product_id': complex_code,
                'quantity': component['quantity'],
            })

        if import_images:
            image_list_tmpl = presta_template['associations']['images'].get('image', [])

            image_ids = [image['id'] for image in make_list_if_not(image_list_tmpl)]

            for variant_imag_ids in images_hub['variants'].values():
                image_ids.extend(variant_imag_ids)

            image_ids = list(set(image_ids))

            bearer_url = f'{self._client._api_url}images/products/{product_code}'

            for image_id in image_ids:
                if not image_id or image_id == IS_FALSE:
                    continue

                try:
                    response = self._client._execute(f'{bearer_url}/{image_id}', 'GET')
                except PrestaShopWebServiceError:
                    pass
                else:
                    if response.status_code == 200:
                        images_hub['images'][image_id] = response.content

        return presta_template, presta_variants, bom_components, images_hub

    def _get_product_fields_hook(self, fields):
        # This method exists to extend amount of fields that are retrieved
        # from Prestashop API. So additional logic can be added
        assert isinstance(fields, list), 'Expected a list of the field names'
        if 'id' not in fields:
            fields.append('id')
        return fields

    def _get_product_filter_hook(self, search_filter=None):
        # This method exists to extend filter criteria for products
        # for Prestashop API. So additional logic can be added
        kwargs = search_filter or dict()
        import_products_filter = json.loads(self.get_settings_value('import_products_filter'))
        product_filter_dict = {
            **import_products_filter,
            **kwargs,
        }
        return product_filter_dict

    def _get_combination_fields_hook(self, fields):
        # This method exists to extend amount of fields that are retrieved
        # from Prestashop API. So additional logic can be added
        fields_list = fields
        if 'id' not in fields_list:
            fields_list.append('id')
        if 'id_product' not in fields_list:
            fields_list.append('id_product')
        return fields_list

    def _get_combination_filter_hook(self, search_filter):
        # This method exists to extend filter criteria for combinations
        # for Prestashop API. So additional logic can be added
        if not isinstance(search_filter, dict):
            search_filter = {}
        return search_filter

    def _filter_templates_hook(self, templates):
        # This method is a hooks that allows to additionally filter products
        # by some non-standard logic. Designed for extension in sub-classes
        return templates

    def _get_products_and_variants(self, product_fields, combination_fields, product_filter):

        template_ids = self._client.model('product').search_read_by_blocks(
            filters=self._get_product_filter_hook(product_filter),
            fields=self._get_product_fields_hook(product_fields),
        )

        variant_ids = self._client.model('combination').search_read_by_blocks(
            filters=self._get_combination_filter_hook(product_filter),
            fields=self._get_combination_fields_hook(combination_fields),
        )

        if variant_ids and not template_ids:
            product_ids = set([x['id_product'] for x in variant_ids])
            template_ids = self._client.model('product').search_read_by_blocks(
                filters={'id': '[%s]' % '|'.join(product_ids)},
                fields=self._get_product_fields_hook(product_fields),
            )

        template_ids = self._filter_templates_hook(template_ids)

        active_template_ids = [x['id'] for x in template_ids]

        # If we were searching by some criteria we have to double check now if found combinations
        # correspond to product template search criteria (usually it is {'active': 1})
        if product_filter and variant_ids:
            tmpl_ids_filter = {'id': '[%s]' % '|'.join([x['id_product'] for x in variant_ids])}
            active_templates = self._client.model('product').search_read(
                filters=self._get_product_filter_hook(tmpl_ids_filter),
                fields=self._get_product_fields_hook(['id']),
            )
            active_templates = self._filter_templates_hook(active_templates)
            # create list of ids
            active_template_ids = [x['id'] for x in active_templates]

        variant_ids = [x for x in variant_ids if x['id_product'] in active_template_ids]

        return template_ids, variant_ids

    @add_dynamic_kwargs
    def get_templates_and_products_for_validation_test(self, product_refs=None, **kw):
        """Presta allows different references for template and its single variant."""
        _logger.info('Prestashop: get_templates_and_products_for_validation_test()')
        if product_refs and not isinstance(product_refs, list):
            product_refs = [str(product_refs)]

        integration = self._get_integration(kw)
        ref_field = integration._get_product_reference_name()
        barcode_field = integration._get_product_barcode_name()

        ecommerce_ref_tmpl = integration._template_field_name_to_ecommerce_name(ref_field)
        ecommerce_barcode_tmpl = integration._template_field_name_to_ecommerce_name(barcode_field)
        ecommerce_ref_variant = integration._variant_field_name_to_ecommerce_name(ref_field)
        ecommerce_barcode_variant = integration._variant_field_name_to_ecommerce_name(barcode_field)

        def serialize_variant(v):
            return {
                'id': v['id'],
                'name': v['name'],
                'barcode': v.get(ecommerce_barcode_variant) or '',
                'ref': v.get(ecommerce_ref_variant) or '',
                'parent_id': v['id_product'],
                'skip_ref': False,
                'joint_namespace': False,
            }

        def serialize_template(t):
            return {
                'id': t['id'],
                'name': t['name'],
                'barcode': t.get(ecommerce_barcode_tmpl) or '',
                'ref': t.get(ecommerce_ref_tmpl) or '',
                'parent_id': '',
                'skip_ref': False,
                'joint_namespace': False,
            }

        reference_filter_mixin = dict()
        if product_refs:
            reference_filter_mixin[ecommerce_ref_tmpl] = '[%s]' % '|'.join(product_refs)

        template_ids, variant_ids = self._get_products_and_variants(
            ['id', 'name', ecommerce_ref_tmpl, ecommerce_barcode_tmpl],
            ['id', 'id_product', ecommerce_ref_variant, ecommerce_barcode_variant],
            reference_filter_mixin,
        )

        products_data = defaultdict(list)
        for tmpl in template_ids:
            products_data[tmpl['id']].append(
                serialize_template(tmpl)
            )

        for variant in variant_ids:
            tmpl_id = variant['id_product']
            variant['name'] = products_data[tmpl_id][0]['name']
            products_data[tmpl_id].append(
                serialize_variant(variant)
            )

        # If there is at least one variant, template reference is not essential.
        for product_list in products_data.values():
            if len(product_list) > 1:
                for tmpl_dict in filter(lambda x: not x['parent_id'], product_list):
                    tmpl_dict['skip_ref'] = True

        TH_Class = integration.get_template_hub_class()
        return TH_Class(list(itertools.chain.from_iterable(products_data.values())))

    @add_dynamic_kwargs
    def get_products_for_accessories(self, **kw):
        _logger.info('Prestashop: get_products_for_accessories()')

        external_ids = set()
        external_data = list()
        template_router = defaultdict(set)
        integration = self._get_integration(kw)

        ref_field = integration._get_product_reference_name()
        ecommerce_ref_tmpl = integration._template_field_name_to_ecommerce_name(ref_field)

        templates = self._client.model('product').search_read_by_blocks(
            filters=self._get_product_filter_hook(),
        )

        for template in templates:
            accessories = self._parse_accessory_ids(template)

            if accessories:
                template_id = template['id']

                external_ids.add(template_id)
                external_ids.update(accessories)
                template_router[template_id].update(accessories)

        for template in templates:
            template_id = template['id']

            if template_id in external_ids:
                product_data = {
                    'id': template_id,
                    'name': template['name'],
                    'external_reference': template[ecommerce_ref_tmpl] or None,
                }
                external_data.append(product_data)
                external_ids.remove(template_id)

            if not external_ids:
                break

        return external_data, template_router

    def get_stock_levels(self, *args, **kw):
        stock_available = self._client.model('stock_available').search_read_by_blocks(
            filters=None,
            fields=['id_product', 'id_product_attribute', 'quantity'],
        )

        stock_available = {
            x['id_product'] + '-' + x['id_product_attribute']: x['quantity']
            for x in stock_available
        }

        return stock_available

    def _parse_accessory_ids(self, template):
        accessories = template.get('associations', {}).get('accessories', {}).get('product', [])

        if isinstance(accessories, dict):
            accessories = [accessories]

        return [x['id'] for x in accessories]

    def _get_url_pattern(self, wrap_li=True):
        pattern = f'<a href="{self.admin_url}/sell/catalog/products/%s" target="_blank">%s</a>'
        if wrap_li:
            return f'<li>{pattern}</li>'
        return pattern

    def _prepare_url_args(self, record):
        return ((record.parent_id or record.id), record.format_name)

    def _convert_to_html(self, id_list):
        pattern = self._get_url_pattern()
        arg_list = [self._prepare_url_args(x) for x in id_list]
        return ''.join([pattern % args for args in arg_list])

    def export_out_of_stock_option(self, data):
        """Export out of stock option to Prestashop."""
        _logger.info('Prestashop: export_out_of_stock_option()')
        stock_available_ids = self._client.model('stock_available').search({
            'id_product': data['external_id'],
        })
        out_of_stock = data['out_of_stock']
        for stock_available in stock_available_ids:
            stock_available.out_of_stock = out_of_stock
            stock_available.save()
        return True

    def import_out_of_stock_option(self, product_id):
        """Import out of stock option from Prestashop."""
        _logger.info('Prestashop: import_out_of_stock_option()')
        stock_available = self._client.model('stock_available').search_read(
            filters={'id_product': product_id},
            fields=['out_of_stock'],
        )
        out_of_stock = [x['out_of_stock'] for x in stock_available]
        return int(out_of_stock[0]) if out_of_stock else IS_FALSE

    @staticmethod
    def _parse_product_external_code(code):
        """
        Redefined method for Presta flow.

        The external code may be formatted as:
            - (1) "100" (just template)
            - (2) "100-0" (template with the single variant)
            - (3) "100-99" (template with the one of its variants)
        """
        # A: handle the (1) case
        if '-' not in code:
            return code, None

        template_id, variant_id = code.rsplit('-', maxsplit=1)

        # B: handle the (2) case
        if variant_id == IS_FALSE:
            return template_id, None

        # C: handle the (3) case
        return template_id, variant_id
