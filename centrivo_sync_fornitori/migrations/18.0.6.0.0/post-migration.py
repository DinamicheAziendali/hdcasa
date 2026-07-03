# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Backfill: rendi STOCCABILI i prodotti dropship già creati.

Fino alla 18.0.5.0.0 i prodotti creati dal Flusso A restavano "consumabili"
(is_storable=False): il Flusso B non poteva scriverne la giacenza come stock
nativo, con errore "Quants cannot be created for consumables or services". Qui li
portiamo tutti a stoccabili, così le giacenze si applicano. Via ORM (disciplina:
niente SQL sulle quantità), idempotente (agisce solo dove non è già True).
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Template = env["product.template"]
    if "is_storable" not in Template._fields:
        return
    products = Template.with_context(active_test=False).search([
        ("is_dropship", "=", True),
        ("is_storable", "!=", True),
    ])
    if products:
        products.write({"is_storable": True})
