# Copyright (C) 2022-Today:
#     Dinamiche Aziendali srl (<http://www.dinamicheaziendali.it/>)
# @author: Gianmarco Conte (gconte@dinamicheaziendali.it)
# License GPL-3.0 or later (http://www.gnu.org/licenses/gpl.html).

{
    "name": "Report picking Hdcasa in Excel per Pinto",
    "summary": "",
    "author": "Gianmarco Conte <gconte@dinamicheaziendali.it>, Dinamiche Aziendali srl",
    "license": "AGPL-3",
    "website": "https://www.dinamicheaziendali.it/",
    "category": "",
    "version": "18.0.1.0.0",
    "depends": [
        "base",
        "stock",
        "delivery",
        "report_xlsx",
        "da_hdcasa",
    ],
    "data": [
        "views/stock_picking.xml",
        "reports/report_xlsx_pinto.xml",
    ],
    "images": ["static/description/icon.png"],
    "installable": True,
}
