# Copyright (C) 2022-Today:
# Dinamiche Aziendali Srl (<http://www.dinamicheaziendali.it/>)
# @author: Giuseppe Borruso <gborruso@dinamicheaziendali.it>
# @author: Gianmarco Conte <gconte@dinamicheaziendali.it>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "HDCasa - Customizations ",
    "version": "16.0.1.0.0",
    "development_status": "Beta",
    "category": "Customizations/HDCasa",
    "author": "Dinamiche Aziendali srl",
    "website": "https://www.dinamicheaziendali.it",
    "license": "AGPL-3",
    "depends": [
        "base",
        "product",
        "purchase",
        "delivery_multi_destination",
        "transport_carrier_base",
        "stock",
        "sale",
        "account",
        "l10n_it_fiscalcode",
    ],
    "data": [
        'views/delivery_carrier_view.xml',
        'views/invoice_template.xml',
        'views/purchase_order_template.xml',
        'views/purchase_quotation_template.xml',
        'views/stock_picking_operations_template.xml',
        'views/purchase_order_view.xml',
    ],
    "installable": True,
}
