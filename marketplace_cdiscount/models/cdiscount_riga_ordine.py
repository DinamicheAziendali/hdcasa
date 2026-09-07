# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La RIGA di un ordine Cdiscount, come l'ha mandata Octopia.

Una per `orderLineId`. E' la memoria di cio' che Cdiscount ha detto della
riga — prezzo pagato, spedizione, **commissione** (il numero del margine
reale, quello che Centrivo-app usera'), date promesse — e il posto in cui si
segna che la spedizione e' stata comunicata.

⚠️ Su Cdiscount la spedizione si dichiara per ORDINE (un collo per l'intero
ordine, LETTO): `spedizione_comunicata` si accende su tutte le righe insieme.
Resta per riga perche' e' cosi' che il tronco e Kaufland ragionano, e il
giorno in cui Octopia aprisse la spedizione per riga non ci sarebbe niente da
migrare.
"""
from odoo import fields, models


class CdiscountRigaOrdine(models.Model):
    _name = "cdiscount.riga.ordine"
    _description = "Cdiscount — riga di un ordine"
    _order = "ordine, riga"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)
    order_map_id = fields.Many2one(
        "centrivo.order.map", string="Ordine", required=True,
        ondelete="cascade", index=True)
    riga = fields.Char(
        string="ID riga Cdiscount", required=True, index=True,
        help="L'`orderLineId` di Octopia.")
    ordine = fields.Char(
        string="ID ordine Cdiscount", required=True, index=True,
        help="L'`orderId` di Octopia: e' con questo che si dichiara la "
             "spedizione.")
    codice = fields.Char(
        string="Codice articolo", index=True,
        help="Il `sellerProductId`: e' il NOSTRO codice, lo stesso "
             "dell'offerta. L'aggancio al prodotto e' diretto.")
    gtin = fields.Char(string="GTIN")
    sale_line_id = fields.Many2one(
        "sale.order.line", string="Riga d'ordine Odoo", ondelete="set null")
    stato_cdiscount = fields.Char(
        string="Stato su Cdiscount",
        help="Come arriva dall'API (es. `InPreparation`).")
    quantita = fields.Integer(string="Quantita'")
    prezzo = fields.Float(
        string="Prezzo unitario", digits="Product Price",
        help="Il `sellingPrice.unitSalesPrice`: quello pagato dal cliente, "
             "IVA inclusa.")
    spedizione = fields.Float(
        string="Spedizione", digits="Product Price",
        help="Il costo di spedizione della riga pagato dal cliente.")
    commissione_con_iva = fields.Float(
        string="Commissione (IVA inclusa)", digits="Product Price")
    commissione_senza_iva = fields.Float(
        string="Commissione (senza IVA)", digits="Product Price",
        help="⚠️ E' questo il numero del margine reale.")
    tasso_commissione = fields.Float(string="Tasso commissione (%)")
    promesso_entro = fields.Datetime(
        string="Consegna promessa entro",
        help="`delivery.promisedAtMax`.")
    spedire_entro = fields.Datetime(
        string="Spedire entro",
        help="`delivery.shippedAtMax`: oltre, la spedizione e' in ritardo.")
    spedizione_comunicata = fields.Boolean(
        string="Spedizione comunicata", default=False, copy=False,
        groups="base.group_system")
    company_id = fields.Many2one(
        "res.company", string="Azienda", related="channel_id.company_id",
        store=True, index=True)

    # ⚠️ L'unicita' e' per ORDINE e riga, non per sola riga: che
    # `orderLineId` sia unico in tutto Octopia e' plausibile ma non misurato,
    # e un vincolo piu' stretto del vero manderebbe in errore ordini buoni.
    _sql_constraints = [
        ("uniq_canale_ordine_riga", "unique(channel_id, ordine, riga)",
         "Questa riga d'ordine Cdiscount è già registrata su questo canale."),
    ]
