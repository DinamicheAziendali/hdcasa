# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La memoria di cosa e' stato mandato a ManoMano.

Senza questa tabella il ritiro non e' scrivibile: `push_offers` ricostruisce
l'elenco dal tag a ogni giro, quindi un prodotto che perde il tag **sparisce**,
e non c'e' nessun posto da cui pescarlo per mandargli lo zero.
**Non si puo' azzerare cio' che non ci si ricorda di aver mandato.**

Progetto: docs/superpowers/specs/2026-08-29-manomano-ritiro-offerte-design.md
"""
from odoo import fields, models

STATI = [
    ("attiva", "Attiva — il prodotto ha il tag"),
    ("da_ritirare", "Da ritirare — il tag non c'e' piu'"),
    ("ritirata", "Ritirata — lo zero e' arrivato"),
]


class CentrivoManoManoOffer(models.Model):
    """Un'offerta mandata a ManoMano, per (canale, SKU, contract).

    ⚠️ **IN SOLA LETTURA PER GLI UTENTI NORMALI**, e non e' prudenza generica:
    e' la stessa difesa di `kaufland.offer`. Lo stato di queste righe decide
    **cosa viene rimesso in vendita su un marketplace vero**. Chi «pulisse» a
    mano una riga `ritirata` la rimetterebbe in gioco, e al giro dopo l'offerta
    tornerebbe comprabile su merce che non c'e' — cioe' esattamente il difetto
    che questa tabella esiste per chiudere.

    ⚠️ Il connettore scrive qui **sempre in `sudo()`**: chiudere i permessi non
    rompe nessun giro. Chi aggiungesse una scrittura senza `sudo()` la vedrebbe
    fallire con un `AccessError` su un utente normale — che e' il verso giusto
    in cui accorgersene.
    """

    _name = "centrivo.manomano.offer"
    _description = "ManoMano — offerta mandata (memoria per il ritiro)"
    _order = "channel_id, sku, contract"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)

    # ⚠️ `ondelete="set null"`: un prodotto cancellato in Odoo NON deve portarsi
    # via la memoria di cio' che sta su ManoMano. L'offerta la' resta viva, e
    # senza la riga nessuno la ritirerebbe piu'.
    product_id = fields.Many2one(
        "product.product", string="Prodotto", ondelete="set null", index=True)

    # L'identita' vera dell'offerta su ManoMano: l'EAN non entra nel payload.
    # Si conserva a parte dal prodotto perche' deve sopravvivergli.
    sku = fields.Char(string="SKU", required=True, index=True)

    # ⚠️ Il contract fa parte della chiave. ManoMano e' multi-contract e la
    # stessa SKU viaggia verso OGNI contract configurato: senza questo campo si
    # ritirerebbe su un mercato e non sull'altro, e le due righe si
    # sovrascriverebbero a vicenda.
    contract = fields.Char(string="Contract", required=True, index=True)

    ultimo_prezzo = fields.Float(
        string="Ultimo prezzo mandato", digits="Product Price",
        help="Serve al ritiro: ManoMano pretende un prezzo anche quando la "
             "quantita' e' zero, e non se ne inventa uno nuovo.")
    ultima_quantita = fields.Integer(string="Ultima quantita' mandata")

    # ⚠️ Peso e tempi si conservano perche' il RITIRO deve poter ricostruire la
    # stessa identica riga con la sola quantita' a zero — e nel momento del
    # ritiro il prodotto in Odoo puo' non esistere piu' (o non avere piu' il
    # peso). ManoMano rifiuta un'offerta con peso zero: senza questi campi il
    # ritiro fallirebbe proprio sulle righe che piu' contano.
    ultimo_peso = fields.Float(string="Ultimo peso mandato", digits="Stock Weight")
    ultimo_tempo_min = fields.Integer(string="Ultimo shipping_time_min")
    ultimo_tempo_max = fields.Integer(string="Ultimo shipping_time_max")

    stato = fields.Selection(
        STATI, string="Stato", default="attiva", required=True, index=True)

    mandata_il = fields.Datetime(string="Mandata il", readonly=True)
    ritirata_il = fields.Datetime(string="Ritirata il", readonly=True)

    ultimo_esito = fields.Selection(
        [("ok", "Riuscito"), ("errore", "Rifiutato"), ("incerto", "Non lo so")],
        string="Ultimo esito")
    ultimo_messaggio = fields.Text(string="Ultimo messaggio")

    company_id = fields.Many2one(
        "res.company", string="Azienda", related="channel_id.company_id",
        store=True, index=True)

    _sql_constraints = [
        ("uniq_canale_sku_contract",
         "unique(channel_id, sku, contract)",
         "Su un canale ManoMano la stessa SKU puo' avere una sola riga per "
         "contract."),
    ]
