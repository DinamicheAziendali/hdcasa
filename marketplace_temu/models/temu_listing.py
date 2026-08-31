# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Registro delle schede Temu: la fotografia del negozio dentro Odoo.

Una riga = una SKU Temu. Serve a tre cose: sapere cosa esiste davvero sul
marketplace, legarlo al prodotto Odoo, e ricordare gli identificativi (scheda e
SKU) che servono in ogni chiamata di aggiornamento futura.
"""
from odoo import api, fields, models

from ..connectors.temu_catalog_parser import DETAIL_LABELS, STATUS_LABELS


class CentrivoTemuListing(models.Model):
    _name = "centrivo.temu.listing"
    _description = "Registro schede Temu"
    _order = "channel_id, out_sku_sn, sku_id"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)
    product_id = fields.Many2one(
        "product.product", string="Prodotto Odoo", ondelete="set null",
        index=True)
    goods_id = fields.Char(string="ID scheda Temu", required=True, index=True)
    sku_id = fields.Char(string="ID SKU Temu", required=True, index=True)
    out_sku_sn = fields.Char(string="Codice venditore", index=True)
    goods_name = fields.Char(string="Nome su Temu")
    sku_status = fields.Char(string="Stato")
    sku_sub_status = fields.Char(string="Sottostato")
    sku_detail_status = fields.Char(
        string="Dettaglio operativo",
        help="Il campo `subStatus4VO` di Temu, su una scala da 1 a 14 diversa "
             "dal sottostato. Dice PERCHE' una scheda si trova nel suo stato: "
             "sanzione in corso, sanzione finita, revisione supplementare.")
    status_label = fields.Char(
        string="Stato leggibile", compute="_compute_status_label", store=True)
    detail_label = fields.Char(
        string="Dettaglio leggibile", compute="_compute_detail_label",
        store=True)
    orphan = fields.Boolean(
        string="Orfana", default=False, index=True,
        help="Vero se il codice venditore della SKU non corrisponde ad alcun "
             "riferimento interno in Odoo. Nessun prodotto viene creato: la "
             "riga resta qui in evidenza per essere sistemata a mano.")
    stock_sent = fields.Integer(
        string="Giacenza inviata", readonly=True,
        help="Ultima giacenza comunicata a Temu e accettata. Serve a non "
             "riscrivere lo stesso numero a ogni giro: le chiamate hanno un "
             "limite di frequenza.")
    stock_sent_on = fields.Datetime(string="Giacenza inviata il", readonly=True)
    price_sent = fields.Float(
        string="Prezzo base proposto", readonly=True, digits=(16, 2),
        help="Ultimo prezzo base proposto a Temu. NON e' il prezzo al "
             "pubblico: e' l'importo che incassiamo noi.")
    price_sent_on = fields.Datetime(string="Prezzo proposto il", readonly=True)
    price_order_sn = fields.Char(
        string="Pratica prezzo Temu", readonly=True,
        help="Numero della pratica di revisione aperta da Temu sul cambio "
             "prezzo. Un prezzo proposto NON e' un prezzo applicato.")

    last_error = fields.Text(string="Ultimo errore")
    last_seen = fields.Datetime(string="Vista l'ultima volta", readonly=True)
    company_id = fields.Many2one(
        "res.company", string="Azienda", related="channel_id.company_id",
        store=True, index=True)

    _sql_constraints = [
        ("temu_listing_uniq", "unique(channel_id, goods_id, sku_id)",
         "Esiste già una riga per questa SKU su questo canale."),
    ]

    @api.depends("sku_detail_status")
    def _compute_detail_label(self):
        for record in self:
            record.detail_label = DETAIL_LABELS.get(
                record.sku_detail_status or "", record.sku_detail_status or "")

    @api.depends("sku_sub_status")
    def _compute_status_label(self):
        for record in self:
            record.status_label = STATUS_LABELS.get(
                record.sku_sub_status or "", record.sku_sub_status or "")

    @api.model
    def upsert_rows(self, channel, rows):
        """Crea o aggiorna le righe della ricognizione. Non cancella nulla.

        Ritorna la coppia (creati, aggiornati). Le righe che non compaiono più
        su Temu restano in Odoo con la loro data di ultima visita: sparire da un
        elenco non è una prova sufficiente per buttare via un dato.
        """
        creati = aggiornati = 0
        adesso = fields.Datetime.now()
        for row in rows:
            dominio = [("channel_id", "=", channel.id),
                       ("goods_id", "=", row["goods_id"]),
                       ("sku_id", "=", row["sku_id"])]
            valori = {
                "channel_id": channel.id,
                "goods_id": row["goods_id"],
                "sku_id": row["sku_id"],
                "out_sku_sn": row.get("out_sku_sn") or False,
                "goods_name": row.get("goods_name") or False,
                "sku_status": row.get("sku_status") or False,
                "sku_sub_status": row.get("sku_sub_status") or False,
                "sku_detail_status": row.get("sku_detail_status") or False,
                "product_id": row.get("product_id") or False,
                "orphan": not row.get("product_id"),
                "last_seen": adesso,
            }
            esistente = self.search(dominio, limit=1)
            if esistente:
                esistente.write(valori)
                aggiornati += 1
            else:
                self.create(valori)
                creati += 1
        return creati, aggiornati
