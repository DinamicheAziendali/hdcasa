#  See LICENSE file for full copyright and licensing details.

from copy import deepcopy
import logging


_logger = logging.getLogger(__name__)


PRESTASHOP = 'prestashop'


class BaseModel:
    """
    The BaseModel class for the convenient handling prestashop API objects.
    In other words it's proxy between the `prestapyt` library object
    and the integration `adapter` class.
    """
    _name = None
    _required_fields = []
    _skip_if_absent_in_schema = []

    _data = {}

    PRESTASHOP_PRECISION = 6

    def __init__(
        self,
        client,
        id_group_shop=None,
        shop_ids=None,
        default_language_id=None,
        data_block_size=None,
    ):
        self._ids = []
        self._to_update = {}
        self._default_language_id = default_language_id
        self._data_block_size = data_block_size
        self._lang_fields = []
        self._lang_id = []

        self._client = client
        self._id_group_shop = id_group_shop
        self._shop_ids = shop_ids

        self._id_group_shop_options = {}
        if self._id_group_shop:
            self._id_group_shop_options['id_group_shop'] = str(self._id_group_shop)

    def __str__(self):
        return str(self.data)

    def __setattr__(self, key, value):
        if key == 'id' or key.startswith('_'):
            return super().__setattr__(key, value)

        if self._lang_id:
            self._lang_fields.append(key)
            self._to_update.setdefault(key, {})[self._lang_id] = value
        else:
            self._to_update[key] = value

    def __getattr__(self, item):
        if not self._data:
            if self.id:
                self.refresh()
            else:
                self._data = self._to_update
        value = self._data.get(item)

        # unwrap `{'attrs': {'notFilterable': 'true'}, 'value': 'simple'}`
        if isinstance(value, dict) and 'value' in value:
            value = value['value']

        return value

    def __getitem__(self, item):
        item_id = self._ids[item]
        item = self.get(item_id)
        return item

    def __iter__(self):
        for item_id in self._ids:
            yield self.get(item_id)

    def get(self, ids=None):
        if ids is None:
            ids = []

        if not isinstance(ids, list):
            ids = [ids]

        records = self.__class__(self._client, self._id_group_shop, self._shop_ids)
        records._name = self._name
        for _id in ids:
            if not isinstance(_id, int):
                _id = int(_id)

            record = self.__class__(self._client, self._id_group_shop, self._shop_ids)
            record.id = _id
            record._name = self._name

            records += record

        return records

    def save(self):
        vals = self._prepare_save_vals()
        result = self._save(vals)
        return result

    def _save(self, vals):
        self._check_required_fields(vals)

        if self.id:
            result = self._client.edit(
                self._plural_name, vals, options=self._id_group_shop_options
            )
        else:
            result = self._client.add(
                self._plural_name,
                vals,
                options=self._id_group_shop_options,
            )[PRESTASHOP][self._name]

            new_id = result['id']
            self.id = new_id

        self._to_update = {}
        self._data = result
        return result

    def search(self, filters=None):
        if not filters:
            filters = {}

        filters = {
            'filter[{}]'.format(key): value for key, value in filters.items()
        }

        ids = self._client.search(
            self._plural_name,
            options=filters,
        )

        records = self.get(ids)
        return records

    def _read(self):
        result = self._client.get(
            self._plural_name,
            self.id
        )[self._name]

        return result

    def search_read(self, filters=None, fields=None, with_translation=False, **kwargs):
        if not filters:
            filters = {}

        options = {
            'filter[{}]'.format(key): value for key, value in filters.items()
        }

        if fields:
            join_fields = ','.join(str(x) for x in fields)
            options['display'] = f'[{join_fields}]'
        else:
            options['display'] = 'full'

        if kwargs:
            options.update(kwargs)

        data = self._client.get(
            self._plural_name,
            options=options,
        )

        data = self._unwrap(data)

        if not with_translation:
            for record in data:
                for key, value in record.items():
                    if self._is_multi_lang_value(value):
                        if self._default_language_id:
                            record[key] = self._get_translation(
                                value,
                                self._default_language_id,
                            )
                        elif self._is_multi_lang_value_with_single_translation(value):
                            record[key] = value['language']['value']

        return data

    def search_read_by_blocks(self, filters, fields=None, with_translation=False, **kwargs):
        response = []
        last = 0
        step = self._data_block_size
        while True:
            res = self.search_read(
                filters=filters,
                fields=fields,
                with_translation=with_translation,
                limit='%d,%d' % (last, step),
                **kwargs,
            )
            last += step
            response += res

            _logger.info('PrestaShop: model "%s" method "search_read_by_blocks" '
                         'records received: %d' % (self._name, len(response)))

            if not res:
                break

        return response

    def read_one(self, item_id):
        record_list = self.search_read(
            filters={'id': f'[{item_id}]'},
            with_translation=True,
        )
        return record_list[0] if record_list else dict()

    def refresh(self):
        data = self._read()
        self._data = data

    def _is_multi_lang_value(self, value):
        result = isinstance(value, dict) and 'language' in value
        return result

    def _is_multi_lang_value_with_single_translation(self, value):
        if not self._is_multi_lang_value(value):
            return False

        result = isinstance(value['language'], dict)
        return result

    def _unwrap(self, result):
        """
        Remove plural and single model name layers

        ```
        {
            'taxes': {
                'tax': {
                    'active': '1',
                    'name': {'language': {'attrs': {'id': '1'}, 'value': 'VAT'}}
                }
            }
        }
        ```
        to
        ```
        [{'active': '1', 'name': {'language': {'attrs': {'id': '1'}, 'value': 'VAT'}}}]
        ```

        and

        `{'taxes': ''}` to `[]`
        """
        without_plural_name_layer = result[self._plural_name]
        if not without_plural_name_layer:
            return []

        result = without_plural_name_layer[self._name]
        if not isinstance(result, list):
            result = [result]

        return result

    def synopsis(self):
        result = self._client.get(
            self._plural_name,
            options={
                'schema': 'synopsis',
            }
        )[self._name]

        return result

    def blank(self):
        result = self._client.get(
            self._plural_name,
            options={
                'schema': 'blank',
                'display': 'full',
            }
        )[self._name]

        return result

    def delete(self):
        if not self._ids:
            return True

        self._client.delete(
            self._plural_name,
            self._ids,
        )
        return True

    @property
    def _plural_name(self):
        # TODO: myparcel - broken dependency order
        irregular = {
            'myparcel_delivery_option': 'myparcel_delivery_option',
        }

        if self._name in irregular:
            return irregular[self._name]
        elif self._name.endswith('ss') or self._name.endswith('x'):
            return self._name + 'es'
        elif self._name.endswith('y'):
            return self._name[:-1] + 'ies'
        else:
            return self._name + 's'

    def _prepare_save_vals(self):
        if self.id:
            schema = self._client.get(self._plural_name, self.id)
        else:
            schema = self._client.get(
                self._plural_name, options={'schema': 'blank', 'display': 'full'}
            )

        vals = self._fill_schema(schema)

        return vals

    def _fill_schema(self, schema):
        vals = deepcopy(schema)

        for key, value in self._to_update.items():
            object_data = vals[self._name]

            if key not in object_data and key in self._skip_if_absent_in_schema:
                # for example `state` is absent in 1.6 whereas is present in 1.7
                continue

            if isinstance(value, BaseModel):
                associations = object_data['associations']
                association_schema = associations[value._plural_name]

                if len(value) > 1:
                    association_ids = [{'id': str(x.id)} for x in value]
                    association_vals = {
                        value._name: association_ids,
                    }
                else:
                    association_vals = value._fill_schema(association_schema)
                    if 'value' in association_schema:
                        del association_schema['value']

                associations[value._plural_name] = association_vals
            elif key in self._lang_fields:
                self._fill_translated_field(value, object_data[key])
            elif self._is_multi_lang_value(object_data[key]):
                language_field = object_data[key]['language']
                single_translation = self._is_multi_lang_value_with_single_translation(
                    object_data[key],
                )
                if not single_translation:
                    raise ValueError(
                        'You are trying to fill multi language field'
                        ' without `with_lang`'
                    )
                language_field['value'] = value
            else:
                object_data[key] = value

        return vals

    @property
    def ids(self):
        return self._ids

    @property
    def id(self):
        assert len(self._ids) <= 1
        return self._ids[0] if self._ids else None

    @id.setter
    def id(self, value):
        assert len(self._ids) <= 1
        if value:
            self._ids = [value]

    def lang(self, lang_id):
        with_lang = self.__class__(self._client, self._id_group_shop, self._shop_ids)
        with_lang._to_update = self._to_update
        with_lang._lang_fields = self._lang_fields
        with_lang._lang_id = lang_id
        return with_lang

    @staticmethod
    def _fill_translated_field(src, dst, convert=None):
        languages = dst['language']

        if isinstance(src, dict):
            src = src.get('language', src)

        if not isinstance(languages, list):
            languages = [languages]

        for language in languages:
            external_language_code = language['attrs']['id']
            try:
                translated_value = src[external_language_code]
                if convert:
                    translated_value = convert(translated_value)
            except KeyError as e:
                msg = (
                    "Translated value is not found for the language with code %s."
                    " Check language mapping!"
                ) % external_language_code
                raise Exception(msg) from e
            language['value'] = translated_value

    def __add__(self, other):
        self._ids.extend(other._ids)
        return self

    def __repr__(self):
        return f'{self.__class__.__name__}({str(self._ids)})'

    def __bool__(self):
        return bool(self._ids)

    def __len__(self):
        return len(self._ids)

    def _check_required_fields(self, vals):
        vals = vals[self._name]

        for field_name in self._required_fields:
            field_value = vals[field_name]

            if isinstance(field_value, dict):
                self._check_required_translatable_field(
                    field_name,
                    field_value,
                )
            else:
                self._check_required_plain_field(
                    field_name,
                    field_value,
                )

    def _check_required_plain_field(self, field_name, field_value):
        if not field_value:
            raise Exception(
                '{} field is required!'.format(field_name),
            )

    def _check_required_translatable_field(self, field_name, field_value):
        translations = field_value['language']
        if not isinstance(translations, list):
            translations = [translations]

        for translation in translations:
            if not translation['value']:
                msg_tmpl = "{} field is required! Check your language mapping"
                raise Exception(
                    msg_tmpl.format(field_name),
                )

    @staticmethod
    def _get_translation(field, language_id):
        languages = field['language']

        if not isinstance(languages, list):
            languages = [languages]

        for translation in languages:
            if translation['attrs']['id'] == str(language_id):
                return translation['value']

        raise Exception('value for language not found')
