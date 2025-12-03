# pylint: disable=C0114
# -*- coding: utf-8 -*-pack
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    # App information
    'name': 'Amazon Settlement Ext Ept',
    'version': '',
    'category': 'Sales',
    'license': 'OPL-1',
    'summary': 'This Module will create the settlement report record from the operations wizard and attach the uploaded file in the record'
               'connector.',
    # Author
    'author': 'Emipro Technologies Pvt. Ltd.',
    'website': 'http://www.emiprotechnologies.com/',
    'maintainer': 'Emipro Technologies Pvt. Ltd.',
    # Dependencies
    'depends': ['amazon_ept'],
    # Views
    'data': [
        'wizard_views/amazon_process_import_export.xml'
    ],

    # Technical
    'installable': True,
    'auto_install': False,
    'application': True,
}
