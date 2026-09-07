# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il RIAGGANCIO, provato dentro un Odoo vero con un trasporto finto.

⚠️ **I CODICI DI PROVA COMINCIANO PER `ZZRIAG-`, E I GTIN PER `000000`.** Lo stage e'
una copia della produzione: `HDC00001` e' un prodotto VERO — il box doccia
Luna 70x70 — e `search(default_code=..., limit=1)` pescava quello invece del
prodotto appena creato dalla prova. Tre prove fallivano per questo, con
messaggi che parlavano d'altro (`product.product(10842,) != (3288,)`).

La stessa cosa e' successa una seconda volta con i GTIN, appena il riaggancio
ha imparato ad accoppiare per codice a barre: `8057680141877` e' il GTIN VERO
del Luna 70x70, e il ripiego lo agganciava — giustamente. Da qui i GTIN che
cominciano per `000000`, che nessun prodotto puo' avere.

**Un dato di prova che puo' esistere davvero non prova niente.**

⚠️ Perché qui e non solo in `tools/`: il banco fuori da Odoo
(`tools/test_cdiscount_prodotti.py`) prova la FORMA — il cursore, i secchi,
come si legge una riga. Qui si prova ciò che quel banco non può vedere:
che l'accoppiamento per `default_code` trovi i prodotti veri, che il
dominio azienda tenga, che le schede nascano con lo stato giusto e che le
offerte poi nascano da loro.

Il giro è LETTO sulla documentazione Octopia («Retrieve seller Products»):
`GET /products?limit=…&cursor=…`, risposta `{"items": [...], "cursor": …}`.
"""
import json
import re
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.marketplace_cdiscount.connectors import cdiscount as CD
from odoo.addons.marketplace_cdiscount.connectors.cdiscount_client import (
    TOKEN_URL)
from odoo.addons.marketplace_cdiscount.connectors.cdiscount_rapporto import (
    RIUSCITO)


class Trasporto(object):
    """Risposte in coda per (metodo, pezzo di percorso). Gettone a parte."""

    def __init__(self, risposte):
        self.risposte = list(risposte)
        self.chiamate = []

    def chiama(self, metodo, uri, teste, corpo):
        if uri == TOKEN_URL:
            return (200, json.dumps({"access_token": "TOK",
                                     "expires_in": 7200}), {})
        percorso = uri.split("/v2", 1)[-1]
        self.chiamate.append((metodo, percorso))
        for indice, (m, pezzo, risposta) in enumerate(self.risposte):
            if m == metodo and re.search(pezzo, percorso):
                del self.risposte[indice]
                stato, corpo_r, teste_r = risposta
                if not isinstance(corpo_r, str):
                    corpo_r = "" if corpo_r is None else json.dumps(corpo_r)
                return stato, corpo_r, teste_r
        raise AssertionError("chiamata non prevista: %s %s" % (metodo, percorso))


def pagina(righe, cursore=None):
    return ("GET", r"^/products\?", (200, {"items": righe,
                                           "cursor": cursore}, {}))


# ⚠️ LA FORMA VERA di una riga di `GET /products` (schema ufficiale, e il
# rapporto del primo riaggancio vero il 2026-09-03): il NOSTRO codice sta
# dentro `sellers[]`, non al primo livello, e il riferimento Octopia si
# chiama `reference`. Il primo tentativo leggeva `sellerProductReference` e
# ha scartato 70 righe su 70.
SELLER = "424639"


def prodotto_cdiscount(codice, riferimento="AUC1", vendibile=True,
                       gtin="0000000000001", venditore=SELLER):
    riga = {"reference": riferimento,
            "label": "Cabine de douche",
            "gtin": gtin,
            "isMarketable": vendibile,
            "permissions": {"edit": True, "enrich": True}}
    if codice:
        riga["sellers"] = [{"reference": venditore,
                            "productReference": codice,
                            "isCreator": True}]
    return riga


@tagged("post_install", "-at_install", "centrivo_cdiscount")
class TestRiaggancio(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.listino = cls.env["product.pricelist"].create({
            "name": "Listino Francia (riaggancio)",
            "currency_id": cls.env.company.currency_id.id})
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Cdiscount (prova riaggancio)",
            "connector_code": "cdiscount",
            "company_id": cls.env.company.id,
            "cdiscount_client_id": "id", "cdiscount_client_secret": "segreto",
            "cdiscount_seller_id": "424639",
            "cdiscount_canale_vendita": "CDISFR",
            "pricelist_selling_id": cls.listino.id,
            "cdiscount_modo_consegna": "TRK",
            "cdiscount_iva": 20.0,
            "processing_time_default": 3,
            "stock_quantity_type": "qty_available",
            "stock_scope": "company",
        })
        cls.Scheda = cls.env["cdiscount.scheda"]

    _contatore = 0

    def _prodotto(self, codice):
        type(self)._contatore += 1
        tmpl = self.env["product.template"].create({
            "name": "Prodotto %s" % codice, "default_code": codice,
            "list_price": 49.9, "type": "consu", "is_storable": True,
            "barcode": "000000%07d" % type(self)._contatore,
        })
        return tmpl.product_variant_id

    def _riaggancia(self, risposte):
        trasporto = Trasporto(risposte)
        with patch.object(CD, "TrasportoRequests", lambda: trasporto):
            esito = self.canale._get_connector().riaggancia()
        return esito, trasporto

    def _schede(self):
        return {s.codice: s for s in self.Scheda.search(
            [("channel_id", "=", self.canale.id)])}

    # ------------------------------------------------------------------
    def test_aggancia_per_riferimento_interno(self):
        """Il caso normale: il prodotto c'è in Odoo e su Cdiscount."""
        self._prodotto("ZZRIAG-001")
        esito, _t = self._riaggancia([
            pagina([prodotto_cdiscount("ZZRIAG-001", "AUC0000000000001")])])
        self.assertEqual(1, esito["agganciate"])
        scheda = self._schede()["ZZRIAG-001"]
        self.assertEqual("AUC0000000000001", scheda.riferimento_octopia)
        self.assertTrue(scheda.vendibile)
        self.assertEqual(RIUSCITO, scheda.stato)
        self.assertTrue(scheda.agganciata_il)

    def test_non_scrive_niente_su_cdiscount(self):
        """⚠️ La promessa del riaggancio: legge e basta."""
        self._prodotto("ZZRIAG-001")
        _e, trasporto = self._riaggancia([
            pagina([prodotto_cdiscount("ZZRIAG-001")])])
        metodi = {m for m, _p in trasporto.chiamate}
        self.assertEqual({"GET"}, metodi)

    def test_segue_il_cursore_fino_in_fondo(self):
        self._prodotto("ZZRIAG-001")
        self._prodotto("ZZRIAG-002")
        esito, trasporto = self._riaggancia([
            pagina([prodotto_cdiscount("ZZRIAG-001")], cursore="AAA"),
            pagina([prodotto_cdiscount("ZZRIAG-002")]),
        ])
        self.assertEqual(2, esito["agganciate"])
        self.assertEqual(2, len(trasporto.chiamate))
        self.assertIn("cursor=AAA", trasporto.chiamate[1][1])

    def test_prodotto_che_in_odoo_non_esiste(self):
        """Roba in vendita su Cdiscount che non sappiamo di vendere."""
        esito, _t = self._riaggancia([
            pagina([prodotto_cdiscount("MAI-VISTO")])])
        self.assertEqual(0, esito["agganciate"], "esito intero: %r" % esito)
        self.assertEqual(1, esito["senza_prodotto"], "esito intero: %r" % esito)
        self.assertIn("MAI-VISTO", esito["senza"])
        self.assertFalse(self._schede())

    def test_prodotto_non_vendibile_non_si_aggancia(self):
        """Una scheda che esiste ma non è nostra non deve avere un'offerta."""
        self._prodotto("ZZRIAG-001")
        esito, _t = self._riaggancia([
            pagina([prodotto_cdiscount("ZZRIAG-001", vendibile=False)])])
        self.assertEqual(0, esito["agganciate"])
        self.assertEqual(1, esito["non_vendibili"])
        self.assertFalse(self._schede())

    def test_riga_senza_riferimento_venditore_finisce_negli_scarti(self):
        """⚠️ Se Octopia rinominasse il campo, cadrebbero TUTTE qui."""
        esito, _t = self._riaggancia([
            pagina([{"reference": "AUC9", "isMarketable": True}])])
        self.assertEqual(1, esito["scartate"])
        self.assertEqual(0, esito["agganciate"])

    def test_i_secchi_sommano_alle_righe_lette(self):
        self._prodotto("ZZRIAG-001")
        esito, _t = self._riaggancia([pagina([
            prodotto_cdiscount("ZZRIAG-001"),
            prodotto_cdiscount("MAI-VISTO"),
            prodotto_cdiscount("ZZRIAG-099", vendibile=False),
            {"reference": "AUC9"},
        ])])
        somma = (esito["agganciate"] + esito["senza_prodotto"]
                 + esito["non_vendibili"] + esito["contese"]
                 + esito["scartate"])
        self.assertEqual(esito["lette"], somma)
        self.assertEqual(4, esito["lette"])

    def test_ripetuto_non_duplica(self):
        """Due riagganci di fila lasciano una scheda sola."""
        self._prodotto("ZZRIAG-001")
        for _volta in range(2):
            self._riaggancia([pagina([prodotto_cdiscount("ZZRIAG-001")])])
        self.assertEqual(1, len(self._schede()))

    def test_due_codici_sullo_stesso_prodotto_sono_una_contesa(self):
        """⚠️ Si dice, non si risolve a caso: è un dato sbagliato altrove.

        E soprattutto NON si lascia arrivare il vincolo unico dal database,
        che in Odoo annulla l'intera transazione: si perderebbero anche le
        righe già agganciate bene.
        """
        prodotto = self._prodotto("ZZRIAG-001")
        self.Scheda.create({"channel_id": self.canale.id,
                            "codice": "VECCHIO", "product_id": prodotto.id,
                            "stato": RIUSCITO})
        esito, _t = self._riaggancia([
            pagina([prodotto_cdiscount("ZZRIAG-001")])])
        self.assertEqual(1, esito["contese"], "esito intero: %r" % esito)
        self.assertEqual(0, esito["agganciate"])

    def test_il_prodotto_di_un_altra_azienda_non_si_aggancia(self):
        """⚠️ In sudo() la ricerca vede tutte le aziende: un default_code
        omonimo finirebbe agganciato al canale sbagliato."""
        altra = self.env["res.company"].create({"name": "Altra azienda"})
        tmpl = self.env["product.template"].create({
            "name": "Omonimo", "default_code": "ZZRIAG-001",
            "company_id": altra.id, "type": "consu"})
        self.assertTrue(tmpl.product_variant_id)
        esito, _t = self._riaggancia([
            pagina([prodotto_cdiscount("ZZRIAG-001")])])
        self.assertEqual(0, esito["agganciate"])
        self.assertEqual(1, esito["senza_prodotto"])

    def test_una_lettura_interrotta_non_aggancia_niente(self):
        """⚠️ Meglio niente che metà: le offerte nascerebbero da una
        fotografia parziale del catalogo."""
        self._prodotto("ZZRIAG-001")
        with self.assertRaises(Exception):
            self._riaggancia([
                pagina([prodotto_cdiscount("ZZRIAG-001")], cursore="AAA"),
                ("GET", r"^/products\?", (500, {"error": "boom"}, {})),
            ])
        self.assertFalse(self._schede())

    def test_le_offerte_nascono_dalle_schede_agganciate(self):
        """Il punto di tutto: dopo il riaggancio l'offerta ha da cosa nascere."""
        prodotto = self._prodotto("ZZRIAG-001")
        self._riaggancia([pagina([prodotto_cdiscount("ZZRIAG-001")])])
        connettore = self.canale._get_connector()
        Offerta = self.env["cdiscount.offerta"].sudo()
        connettore._assicura_offerte(Offerta, self.env["cdiscount.scheda"].sudo())
        schede = self.env["cdiscount.scheda"].search(
            [("channel_id", "=", self.canale.id)])
        righe = Offerta.search([("channel_id", "=", self.canale.id)])
        self.assertEqual(
            1, len(righe),
            "schede: %r" % [(s.codice, s.stato, s.product_id.id, s.vendibile)
                            for s in schede])
        self.assertEqual("ZZRIAG-001", righe.codice)
        self.assertEqual(prodotto, righe.product_id)

    def test_il_bottone_e_sulla_vista_del_canale(self):
        arch = self.env["centrivo.channel"].get_views(
            [(False, "form")])["views"]["form"]["arch"]
        self.assertIn("action_cdiscount_riaggancia", arch)

    def test_prodotto_di_un_altro_venditore_si_aggancia_per_gtin(self):
        """⚠️ IL CASO PIÙ FREQUENTE, e senza di lui il riaggancio è inutile.

        Per i prodotti creati da ALTRI venditori la documentazione avverte
        che «some fields will not be displayed»: `sellers[]` può mancare del
        tutto, quindi il nostro codice non c'è. Resta il GTIN, che in Odoo è
        il codice a barre. Al primo caricamento erano 76 su 176 — la
        maggioranza.
        """
        prodotto = self._prodotto("ZZRIAG-050")
        prodotto.product_tmpl_id.barcode = "0000009990001"
        esito, _t = self._riaggancia([pagina([
            {"reference": "AUC-ALTRUI", "gtin": "0000009990001",
             "isMarketable": True}])])
        self.assertEqual(1, esito["agganciate"], "esito intero: %r" % esito)
        scheda = self._schede()["ZZRIAG-050"]
        self.assertEqual(prodotto, scheda.product_id)
        self.assertEqual("AUC-ALTRUI", scheda.riferimento_octopia)

    def test_il_codice_di_un_altro_venditore_non_si_prende(self):
        """⚠️ `sellers[]` porta anche i concorrenti: si prende il NOSTRO.

        Prendere la prima riga vorrebbe dire agganciare il codice interno di
        un concorrente a un nostro prodotto.
        """
        self._prodotto("ZZRIAG-060")
        esito, _t = self._riaggancia([pagina([{
            "reference": "AUC-X", "gtin": "0000009990002",
            "isMarketable": True,
            "sellers": [{"reference": "999999",
                         "productReference": "ZZRIAG-060"}]}])])
        self.assertEqual(0, esito["agganciate"], "esito intero: %r" % esito)
