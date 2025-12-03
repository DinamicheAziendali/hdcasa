# -*- coding: utf-8 -*-pack
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    # App information
    "name": "Amazon Settlement Extended EPT",
    'version': '16.0.1',
    'category': 'Accounting',
    'license': 'OPL-1',
    "summary": """
            Amazon settlement extended ept is used to process an large number of settlement data via queues and 
            scheduler,
    """,
    # Author
    'author': 'Emipro Technologies Pvt. Ltd.',
    'website': 'http://www.emiprotechnologies.com/',
    'maintainer': 'Emipro Technologies Pvt. Ltd.',

    # Dependencies
    'depends': ['amazon_ept'],

    # data files
    "data": ['security/ir.model.access.csv',
             'data/ir_sequence.xml',
             'data/ir_cron.xml',
             'views/settlement_process_queue.xml',
             'views/settlement_report.xml'],

    # Technical
    "installable": True,
    'auto_install': False,
}
