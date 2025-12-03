# -*- coding: utf-8 -*-
# Copyright (C) 2022-Today:
#     Dinamiche Aziendali srl (<http://www.dinamicheaziendali.it/>)
# @author: Gianmarco Conte (gconte@dinamicheaziendali.it)
# License GPL-3.0 or later (http://www.gnu.org/licenses/gpl.html).

{
    'name': "Report picking Hdcasa in Excel per Pinto",
    'summary': "",
    'description': """Report picking Hdcasa in Excel per Pinto
    . Dinamiche Aziendali srl""",
    'author': 'Gianmarco Conte <gconte@dinamicheaziendali.it> ',
    'license': 'OPL-1',
    'website': 'www.dinamicheaziendali.it',
    'category': '',
    'version': '16.0.1.0',
    'depends': [
        'base',
        'stock',
        'delivery',
        'report_xlsx',
        'da_hdcasa',
    ],
    'data': [
        'views/stock_picking.xml',
        'reports/report_xlsx_pinto.xml',
    ],
    'demo': [],
    'active': False,
    'installable': True
}
