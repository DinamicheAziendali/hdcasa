# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La RIGA di un ordine Kaufland.

⚠️ **Kaufland ragiona per riga, non per ordine**, e questo modello esiste per
quello. Lo `status` sta sulla riga (`id_order_unit`), non sulla testata: un
ordine da tre articoli puo' averne uno spedito, uno da spedire e uno annullato.

BricoBravo e ManoMano ragionano per ordine, e `centrivo.order.map` e' nata
cosi'. Il tronco non si piega: la mappa resta per ORDINE e diventa il
`sale.order`; qui sta la riga.

⚠️ **E serve soprattutto per dopo.** La comunicazione della spedizione si fa
**per riga**: senza un posto dove segnare quale riga e' gia' stata comunicata,
il secondo giro la ricomunicherebbe — e un numero di tracciamento riusato e' un
rifiuto, come gia' misurato su Temu.

Misure: `docs/kaufland-ordini-misure.md`
Piano: `docs/superpowers/plans/2026-08-31-kaufland-consegna-2-ordini.md`
"""
from odoo import fields, models


class CentrivoKauflandOrderUnit(models.Model):
    """Una riga d'ordine Kaufland, agganciata alla mappa dell'ordine.

    ⚠️ In sola lettura per gli utenti normali, come gli altri modelli del
    modulo: `spedizione_comunicata` e' cio' che impedisce di rimandare una
    spedizione gia' comunicata, e chi lo azzera a mano fa rifiutare la riga da
    Kaufland senza capire perche'.
    """

    _name = "centrivo.kaufland.order.unit"
    _description = "Kaufland — riga di un ordine"
    _order = "id_order, id_order_unit"

    market_id = fields.Many2one(
        "centrivo.kaufland.market", string="Mercato", required=True,
        ondelete="cascade", index=True)
    order_map_id = fields.Many2one(
        "centrivo.order.map", string="Ordine", required=True,
        ondelete="cascade", index=True)

    id_order_unit = fields.Char(
        string="ID riga Kaufland", required=True, index=True,
        help="L'identificativo della RIGA su Kaufland. E' con questo che si "
             "comunica la spedizione, non con quello dell'ordine.")
    id_order = fields.Char(string="ID ordine Kaufland", required=True, index=True)
    id_offer = fields.Char(
        string="Codice articolo", index=True,
        help="⚠️ E' il NOSTRO codice articolo: Kaufland lo restituisce tale e "
             "quale, quindi l'aggancio al prodotto e' diretto.")

    sale_line_id = fields.Many2one(
        "sale.order.line", string="Riga d'ordine Odoo", ondelete="set null")

    stato_kaufland = fields.Char(
        string="Stato su Kaufland",
        help="Come arriva dall'API (es. `need_to_be_sent`). ⚠️ Il cruscotto "
             "Kaufland ne mostra altri: la corrispondenza non e' ovvia e non "
             "va data per scontata.")

    prezzo = fields.Float(
        string="Prezzo", digits="Product Price",
        help="Quello pagato dal cliente, IVA inclusa. Dall'API arriva in "
             "centesimi e qui e' gia' in euro.")
    ricavo_netto = fields.Float(
        string="Ricavo netto", digits="Product Price",
        help="Quello che resta al netto di IVA e commissione Kaufland "
             "(`revenue_net`). ⚠️ E' questo il numero del margine, non il "
             "prezzo.")

    scade_il = fields.Datetime(
        string="Spedire entro",
        help="⚠️ Oltre questa data la spedizione e' in ritardo, e le metriche "
             "di puntualita' di Kaufland sono per ACCOUNT, non per offerta.")

    spedizione_comunicata = fields.Boolean(
        string="Spedizione comunicata", default=False, copy=False,
        groups="base.group_system")

    company_id = fields.Many2one(
        "res.company", string="Azienda", related="market_id.company_id",
        store=True, index=True)

    _sql_constraints = [
        # ⚠️ Per MERCATO: la stessa riga non si registra due volte, ed e' cio'
        # che rende lo scarico ripetibile senza doppioni.
        ("uniq_mercato_riga", "unique(market_id, id_order_unit)",
         "Questa riga d'ordine Kaufland e' gia' registrata su questo mercato."),
    ]
