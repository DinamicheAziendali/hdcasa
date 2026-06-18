# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Connettore BricoBravo (Famiglia A — sotto integrations_core).
# Ossatura del connettore ordini via API REST. La logica di chiamata reale verrà
# rifinita in un task successivo testando sulla sandbox BricoBravo (che resetta
# gli ordini ogni ora). Nessuna chiave API è contenuta nel codice.
#
# FASI FUTURE (NON in questo modulo):
#   - v2: export GIACENZE verso BricoBravo via CSV (CsvTransport). EXPORT da
#         Odoo (fonte di verità) verso il marketplace.
#         Colonne feed giacenze (riferimento):
#         Sku EAN/GTIN; Selling Price (Price to GPP); Discounted Price;
#         Available Quantity; Processing Time; Product_Code.
#   - v3: export CATALOGO completo verso BricoBravo via CSV (CsvTransport).
#         Colonne feed completo (riferimento):
#         Sku EAN/GTIN; Category; Brand; ProductName; Url; Weight;
#         Product Description; Image URL 1..10; Selling Price (Price to GPP);
#         Discounted Price; Vat; Available Quantity; Processing Time; Product_Code.
{
    "name": "Marketplace - BricoBravo",
    "version": "18.0.2.4.0",
    "license": "OPL-1",
    "category": "Connector",
    "summary": "Connettore BricoBravo (API REST) su integrations_core: import ordini, conferma, acquisizione e comunicazione spedizione.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    "depends": ["integrations_core"],
    "data": [
        # Server action richiamabile a mano per creare prodotti di TEST su stage.
        "data/test_products.xml",
    ],
    "installable": True,
    "application": False,
}
