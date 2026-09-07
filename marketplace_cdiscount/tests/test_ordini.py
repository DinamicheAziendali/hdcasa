# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Gli ORDINI Cdiscount (Consegna 3), provati dentro un Odoo vero con un
trasporto finto: nessuna chiamata esce.

Il finto Cdiscount risponde con la forma LETTA sulla documentazione
(`docs/cdiscount-ordini-contratto.md`) dentro l'involucro MISURATO
(`{"itemsPerPage", "items"}`, paginazione a indice, conteggio intero nudo).
Sull'account vero non c'era ancora nessun ordine: il primo confermera' la
forma, e queste prove dicono cosa il connettore fa di quella forma.

Le decisioni che presidiano (2026-09-02):
- si scaricano SOLO gli `InPreparation`, e prima si conta chi aspetta
  un'accettazione;
- l'ordine nasce gia' confermato, col prezzo pagato dal cliente IVA inclusa;
- prodotto NON mappato → ordine in errore, e si vede;
- la spedizione e' UN collo per ordine, col cancello chiuso finche' non e'
  stata vista sul portale; in Francia solo BRT e GLS.
"""
import json
import re
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.marketplace_cdiscount.connectors import cdiscount as CD
from odoo.addons.marketplace_cdiscount.connectors.cdiscount_client import (
    TOKEN_URL)

INDIRIZZO = {"civility": "Mme", "firstName": "Camille", "lastName": "Durand",
             "companyName": None, "companyVatNumber": None,
             "addressLine1": "12 rue des Lilas", "addressLine2": "Bât. B",
             "addressLine3": None, "postalCode": "69003", "city": "Lyon",
             "stateOrRegion": None, "countryCode": "FR"}


def riga(id_riga, codice, quantita=1, prezzo=100.0, spedizione=5.0,
         stato="InPreparation", supply="Seller", consegna=None):
    return {
        "orderLineId": id_riga, "status": stato, "quantity": quantita,
        "totalPrice": {"offerPrice": prezzo * quantita,
                       "sellingPrice": prezzo * quantita},
        "offerPrice": {"unitSalesPrice": prezzo, "shippingCost": spedizione,
                       "commission": {"amountWithVat": 12.0,
                                      "amountWithoutVat": 10.0, "rate": 12},
                       "taxes": []},
        "sellingPrice": {"unitSalesPrice": prezzo, "shippingCost": spedizione,
                         "taxes": []},
        "offer": {"id": "OF1", "sellerProductId": codice, "supplyMode": supply,
                  "productId": "AUC1", "productTitle": "Cabine %s" % codice,
                  "condition": "New", "productGtin": None},
        "delivery": {"mode": "TrackedHomeDelivery",
                     "promisedAtMin": "2026-09-05T00:00:00+00:00",
                     "promisedAtMax": "2026-09-08T00:00:00+00:00",
                     "shippedAtMax": "2026-09-04T00:00:00+00:00"},
        "shippingAddress": dict(consegna or INDIRIZZO),
    }


def ordine(numero, righe, totale=None, fatturazione=None):
    if totale is None:
        totale = round(sum(r["sellingPrice"]["unitSalesPrice"] * r["quantity"]
                           + r["sellingPrice"]["shippingCost"]
                           for r in righe
                           if r["status"] == "InPreparation"), 2)
    return {"orderId": numero, "reference": None, "businessOrder": False,
            "salesChannel": {"id": "CDISFR", "name": "Cdiscount"},
            "seller": {"id": "424639"},
            "customer": {"reference": "CUST-%s" % numero},
            "purchasedAt": "2026-09-02T09:15:00+00:00",
            "createdAt": "2026-09-02T09:15:00+00:00",
            "updatedAt": "2026-09-02T09:20:00+00:00",
            "shippedAtMax": "2026-09-04T00:00:00+00:00",
            "status": "InPreparation", "payment": {"method": "Card"},
            "currencyCode": "EUR",
            "billingAddress": dict(fatturazione or INDIRIZZO),
            "totalPrice": {"offerPrice": totale, "sellingPrice": totale},
            "serviceFees": [], "statusEvents": [], "lines": righe}


class Trasporto(object):
    """Risposte per (metodo, pezzo di percorso), in coda; ogni chiamata si
    registra. Una chiamata senza risposta preparata fa fallire la prova."""

    def __init__(self, risposte):
        self.risposte = list(risposte)
        self.chiamate = []

    def chiama(self, metodo, uri, teste, corpo):
        if uri == TOKEN_URL:
            return (200, json.dumps({"access_token": "TOK",
                                     "expires_in": 7200}), {})
        percorso = uri.split("/v2", 1)[-1]
        self.chiamate.append((metodo, percorso,
                              json.loads(corpo.decode("utf-8")) if corpo
                              else None))
        for indice, (m, pezzo, risposta) in enumerate(self.risposte):
            if m == metodo and re.search(pezzo, percorso):
                del self.risposte[indice]
                stato, corpo_r, teste_r = risposta
                if not isinstance(corpo_r, str):
                    corpo_r = "" if corpo_r is None else json.dumps(corpo_r)
                return stato, corpo_r, teste_r
        raise AssertionError("chiamata non prevista: %s %s" % (metodo, percorso))

    def sequenza(self):
        return [(m, p) for m, p, _c in self.chiamate]

    def corpo(self, metodo, pezzo):
        for m, p, c in self.chiamate:
            if m == metodo and re.search(pezzo, p):
                return c
        return None


def in_attesa(quanti=0):
    return ("GET", r"^/orders/count\?status=WaitingAcceptance$", (200, quanti, {}))


def pagina(ordini, indice=1):
    return ("GET", r"^/orders\?.*pageIndex=%d(&|$)" % indice,
            (200, {"itemsPerPage": len(ordini), "items": ordini}, {}))


def spedito(numero, stato=201):
    return ("POST", r"^/orders/%s/shipments$" % numero,
            (stato, None, {}))


@tagged("post_install", "-at_install", "centrivo_cdiscount")
class TestOrdini(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        azienda = cls.env.company
        cls.posizione = cls.env["account.fiscal.position"].create({
            "name": "Francia (prova Cdiscount)", "company_id": azienda.id})
        # ⚠️ SENZA imposte: i prezzi arrivano IVA inclusa e il controllo del
        # totale confronta al centesimo. Sullo stage il prodotto nascerebbe
        # con l'IVA predefinita dell'azienda, non «inclusa», e il totale
        # salirebbe del 22% — che e' proprio il caso che il controllo deve
        # segnalare in produzione, non qui.
        cls.spedizione = cls.env["product.product"].create({
            "name": "Spese di spedizione Cdiscount (prova)", "type": "service",
            "list_price": 0.0, "taxes_id": [(6, 0, [])]})
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Cdiscount (prova ordini)",
            "connector_code": "cdiscount",
            "company_id": azienda.id,
            "cdiscount_client_id": "id", "cdiscount_client_secret": "segreto",
            "cdiscount_seller_id": "424639",
            "cdiscount_canale_vendita": "CDISFR",
            "cdiscount_categoria": "0H0805",
            "cdiscount_posizione_fiscale": cls.posizione.id,
            "cdiscount_prodotto_spedizione": cls.spedizione.id,
        })
        cls.p1 = cls.env["product.product"].create({
            "name": "Cabine (prova ordini) 1", "default_code": "ORD-CD-1",
            "list_price": 100.0, "taxes_id": [(6, 0, [])]})
        cls.p2 = cls.env["product.product"].create({
            "name": "Cabine (prova ordini) 2", "default_code": "ORD-CD-2",
            "list_price": 50.0, "taxes_id": [(6, 0, [])]})
        # L'impianto corrieri del tronco, montato per davvero.
        cls.marca_brt = cls.env.ref("integrations_core.carrier_brand_brt")
        cls.marca_brt.tracking_url_template = "https://vivi.brt.it/?tracking={tracking}"
        cls.vettore = cls.env["delivery.carrier"].create({
            "name": "BRT (prova Cdiscount)",
            "product_id": cls.env["product.product"].create({
                "name": "Trasporto BRT (prova Cdiscount)",
                "type": "service"}).id})
        cls.env["centrivo.carrier.source"].create({
            "source_model": "delivery.carrier",
            "source_res_id": cls.vettore.id,
            "source_display": cls.vettore.display_name,
            "brand_id": cls.marca_brt.id,
            "company_id": azienda.id})
        cls.Mappa = cls.env["centrivo.order.map"]
        cls.Riga = cls.env["cdiscount.riga.ordine"]

    # ------------------------------------------------------------------
    def _scarico(self, risposte):
        trasporto = Trasporto(risposte)
        with patch.object(CD, "TrasportoRequests", lambda: trasporto):
            esito = self.canale._get_connector().pull_orders()
        return esito, trasporto

    def _mappa(self, numero):
        return self.Mappa.search([("channel_id", "=", self.canale.id),
                                  ("external_id", "=", numero)], limit=1)

    def _spedisci_in_odoo(self, mappa, tracking="BRT-0001", vettore=None):
        picking = mappa.sale_order_id.picking_ids[:1]
        if not picking:
            return picking
        picking.write({"carrier_id": (vettore or self.vettore).id,
                       "carrier_tracking_ref": tracking})
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.picking_type_id.create_backorder = "never"
        picking.button_validate()
        return picking

    def _push(self, mappa, risposte, a_mano=True):
        trasporto = Trasporto(risposte)
        conn = self.canale._get_connector()
        if a_mano:
            conn = self.canale.with_context(
                cdiscount_spedizione_a_mano=True)._get_connector()
        with patch.object(CD, "TrasportoRequests", lambda: trasporto):
            esito = conn.push_shipment(mappa)
        return esito, trasporto

    def _registro(self, operazione):
        return self.env["centrivo.job.log"].search(
            [("channel_id", "=", self.canale.id),
             ("operation", "=", operazione)], order="id desc")

    # ------------------------------------------------------------------
    def test_01_un_ordine_entra_e_nasce_confermato(self):
        o = ordine("CD-1", [riga("L1", "ORD-CD-1", quantita=2, prezzo=100.0,
                                 spedizione=5.0),
                            riga("L2", "ORD-CD-2", prezzo=50.0, spedizione=0)])
        esito, tr = self._scarico([in_attesa(0), pagina([o])])
        self.assertEqual(tr.sequenza(), [
            ("GET", "/orders/count?status=WaitingAcceptance"),
            ("GET", "/orders?status=InPreparation&salesChannelId=CDISFR"
                    "&pageSize=100&pageIndex=1")])
        self.assertEqual((esito["lette"], esito["importati"],
                          esito["in_errore"]), (1, 1, 0))
        mappa = self._mappa("CD-1")
        self.assertEqual(mappa.state, "imported")
        so = mappa.sale_order_id
        self.assertEqual(so.state, "sale", "l'ordine nasce gia' confermato")
        self.assertEqual(so.client_order_ref, "CD-1")
        self.assertEqual(so.fiscal_position_id, self.posizione)
        # Le righe: due prodotti + una riga di spedizione
        prodotti = {l.product_id: l for l in so.order_line}
        self.assertEqual(prodotti[self.p1].product_uom_qty, 2)
        self.assertEqual(prodotti[self.p1].price_unit, 100.0)
        self.assertEqual(prodotti[self.p2].price_unit, 50.0)
        self.assertEqual(prodotti[self.spedizione].price_unit, 5.0)
        # Il cliente: anonimo, agganciato per riferimento
        self.assertEqual(so.partner_id.name, "Camille Durand")
        self.assertEqual(so.partner_id.street, "12 rue des Lilas")
        self.assertEqual(so.partner_id.street2, "Bât. B")
        self.assertEqual((so.partner_id.zip, so.partner_id.city,
                          so.partner_id.country_id.code),
                         ("69003", "Lyon", "FR"))
        self.assertEqual(so.partner_id.ref, "CDISCOUNT:CUST-CD-1")
        # Le righe Cdiscount, con la commissione
        righe = self.Riga.search([("order_map_id", "=", mappa.id)])
        self.assertEqual(len(righe), 2)
        r1 = righe.filtered(lambda r: r.riga == "L1")
        self.assertEqual((r1.codice, r1.quantita, r1.prezzo, r1.spedizione,
                          r1.commissione_senza_iva, r1.tasso_commissione),
                         ("ORD-CD-1", 2, 100.0, 5.0, 10.0, 12.0))
        self.assertTrue(r1.spedire_entro)
        self.assertEqual(r1.sale_line_id, prodotti[self.p1])
        # Il registro e' verde
        self.assertEqual(self._registro("pull_orders")[0].result, "success")

    def test_02_lo_stesso_ordine_non_entra_due_volte(self):
        o = ordine("CD-2", [riga("L1", "ORD-CD-1")])
        self._scarico([in_attesa(0), pagina([o])])
        esito, tr = self._scarico([in_attesa(0), pagina([o])])
        self.assertEqual((esito["importati"], esito["gia_importati"]), (0, 1))
        self.assertEqual(self.env["sale.order"].search_count(
            [("client_order_ref", "=", "CD-2")]), 1)

    def test_03_prodotto_non_mappato_ordine_in_errore(self):
        o = ordine("CD-3", [riga("L1", "NON-ESISTE")])
        esito, tr = self._scarico([in_attesa(0), pagina([o])])
        self.assertEqual(esito["in_errore"], 1)
        mappa = self._mappa("CD-3")
        self.assertEqual(mappa.state, "error")
        self.assertIn("NON-ESISTE", mappa.error_message)
        self.assertFalse(mappa.sale_order_id)
        self.assertEqual(self.env["sale.order"].search_count(
            [("client_order_ref", "=", "CD-3")]), 0)

    def test_04_le_righe_annullate_si_saltano(self):
        o = ordine("CD-4", [riga("L1", "ORD-CD-1"),
                            riga("L2", "ORD-CD-2", stato="Cancelled")])
        esito, tr = self._scarico([in_attesa(0), pagina([o])])
        so = self._mappa("CD-4").sale_order_id
        self.assertEqual(so.order_line.mapped("product_id"),
                         self.p1 | self.spedizione)
        # Tutto annullato: l'ordine non nasce, e si conta come saltato.
        o2 = ordine("CD-4b", [riga("L1", "ORD-CD-1", stato="Cancelled")])
        esito, tr = self._scarico([in_attesa(0), pagina([o2])])
        self.assertEqual(esito["saltati"], 1)
        self.assertFalse(self._mappa("CD-4b").sale_order_id)

    def test_05_il_totale_che_non_torna_si_dice_ma_l_ordine_resta(self):
        o = ordine("CD-5", [riga("L1", "ORD-CD-1", prezzo=100.0, spedizione=0)],
                   totale=123.45)
        esito, tr = self._scarico([in_attesa(0), pagina([o])])
        self.assertEqual(esito["importati"], 1)
        self.assertTrue(self._mappa("CD-5").sale_order_id)
        registro = self._registro("pull_orders")
        self.assertEqual(registro[0].result, "error")
        self.assertTrue(any("123.45" in r.message for r in registro))

    def test_06_gli_ordini_in_attesa_di_accettazione_si_dicono(self):
        esito, tr = self._scarico([in_attesa(3), pagina([])])
        self.assertEqual(esito["in_attesa_accettazione"], 3)
        registro = self._registro("pull_orders")
        self.assertEqual(registro[0].result, "error")
        self.assertIn("3", registro[0].message)
        self.assertIn("accettazione", registro[0].message.lower())

    def test_07_senza_posizione_fiscale_l_ordine_entra_e_lo_si_dice(self):
        self.canale.cdiscount_posizione_fiscale = False
        o = ordine("CD-7", [riga("L1", "ORD-CD-1")])
        esito, tr = self._scarico([in_attesa(0), pagina([o])])
        self.assertEqual(esito["importati"], 1)
        self.assertIn("posizione fiscale",
                      self._registro("pull_orders")[0].message.lower())
        self.canale.cdiscount_posizione_fiscale = self.posizione.id

    def test_08_senza_prodotto_spedizione_le_spese_restano_fuori_e_si_dice(self):
        self.canale.cdiscount_prodotto_spedizione = False
        o = ordine("CD-8", [riga("L1", "ORD-CD-1", spedizione=7.0)])
        esito, tr = self._scarico([in_attesa(0), pagina([o])])
        so = self._mappa("CD-8").sale_order_id
        self.assertEqual(len(so.order_line), 1)
        self.assertIn("spedizione",
                      self._registro("pull_orders")[0].message.lower())
        self.canale.cdiscount_prodotto_spedizione = self.spedizione.id

    def test_09_la_consegna_diversa_dalla_fatturazione(self):
        consegna = dict(INDIRIZZO, addressLine1="5 av. Foch", city="Paris",
                        postalCode="75016", addressLine2=None)
        o = ordine("CD-9", [riga("L1", "ORD-CD-1", consegna=consegna)])
        self._scarico([in_attesa(0), pagina([o])])
        so = self._mappa("CD-9").sale_order_id
        self.assertNotEqual(so.partner_shipping_id, so.partner_id)
        self.assertEqual(so.partner_shipping_id.city, "Paris")
        self.assertEqual(so.partner_shipping_id.parent_id, so.partner_id)

    def test_10_due_pagine_si_leggono_tutte(self):
        o1 = ordine("CD-10a", [riga("L1", "ORD-CD-1")])
        o2 = ordine("CD-10b", [riga("L1", "ORD-CD-2")])
        with patch.object(CD, "ORDINI_PER_PAGINA", 1):
            esito, tr = self._scarico([in_attesa(0), pagina([o1], 1),
                                       pagina([o2], 2), pagina([], 3)])
        self.assertEqual(esito["importati"], 2)
        self.assertEqual([m for m, _p in tr.sequenza()].count("GET"), 4)

    def test_11_una_pagina_che_non_arriva_interrompe_e_lo_dice(self):
        esito, tr = self._scarico([in_attesa(0),
                                   ("GET", r"^/orders\?", (502, "<html>", {}))])
        self.assertEqual(esito["interrotto"], 1)
        self.assertEqual(self._registro("pull_orders")[0].result, "error")

    # ------------------------------------------------------------------
    def _ordine_spedibile(self, numero="CD-S1"):
        o = ordine(numero, [riga("L1", "ORD-CD-1"), riga("L2", "ORD-CD-2")])
        self._scarico([in_attesa(0), pagina([o])])
        return self._mappa(numero)

    def test_20_il_cancello_chiuso_ferma_l_automatismo(self):
        mappa = self._ordine_spedibile("CD-S20")
        self._spedisci_in_odoo(mappa)
        esito, tr = self._push(mappa, [], a_mano=False)
        self.assertFalse(esito)
        self.assertEqual(tr.sequenza(), [])
        self.assertIn("a mano", self._registro("push_shipment")[0].message.lower())
        self.assertFalse(mappa.shipment_pushed)

    def test_21_a_mano_parte_UN_collo_per_l_intero_ordine(self):
        mappa = self._ordine_spedibile("CD-S21")
        self._spedisci_in_odoo(mappa, tracking="BRT-0021")
        esito, tr = self._push(mappa, [spedito("CD-S21")])
        self.assertTrue(esito)
        self.assertEqual(tr.sequenza(), [("POST", "/orders/CD-S21/shipments")])
        self.assertEqual(tr.corpo("POST", "shipments"), [{
            "parcelNumber": "BRT-0021", "carrierName": "BRT",
            "trackingUrl": "https://vivi.brt.it/?tracking=BRT-0021"}])
        self.assertTrue(mappa.shipment_pushed)
        righe = self.Riga.search([("order_map_id", "=", mappa.id)])
        self.assertTrue(all(righe.mapped("spedizione_comunicata")))
        self.assertEqual(self._registro("push_shipment")[0].result, "success")

    def test_22_col_cancello_aperto_parte_anche_dall_automatismo(self):
        self.canale.sudo().write({"cdiscount_spedizione_provata": True})
        mappa = self._ordine_spedibile("CD-S22")
        self._spedisci_in_odoo(mappa, tracking="BRT-0022")
        esito, tr = self._push(mappa, [spedito("CD-S22")], a_mano=False)
        self.assertTrue(esito)
        self.canale.sudo().write({"cdiscount_spedizione_provata": False})

    def test_23_premere_due_volte_non_richiama_cdiscount(self):
        mappa = self._ordine_spedibile("CD-S23")
        self._spedisci_in_odoo(mappa, tracking="BRT-0023")
        self._push(mappa, [spedito("CD-S23")])
        esito, tr = self._push(mappa, [])
        self.assertTrue(esito)
        self.assertEqual(tr.sequenza(), [])
        self.assertEqual(self._registro("push_shipment")[0].result, "skip")

    def test_24_un_502_non_segna_niente_e_non_ritenta(self):
        mappa = self._ordine_spedibile("CD-S24")
        self._spedisci_in_odoo(mappa, tracking="BRT-0024")
        esito, tr = self._push(mappa, [spedito("CD-S24", stato=502)])
        self.assertFalse(esito)
        self.assertFalse(mappa.shipment_pushed)
        righe = self.Riga.search([("order_map_id", "=", mappa.id)])
        self.assertFalse(any(righe.mapped("spedizione_comunicata")))
        self.assertIn("ignoto", self._registro("push_shipment")[0].message.lower())

    def test_25_un_rifiuto_certo_lascia_da_fare_e_lo_scrive(self):
        mappa = self._ordine_spedibile("CD-S25")
        self._spedisci_in_odoo(mappa, tracking="BRT-0025")
        esito, tr = self._push(mappa, [("POST", r"shipments$", (400, {
            "title": "Validation Failed",
            "errors": {"carrierName": ["unknown carrier"]}}, {}))])
        self.assertFalse(esito)
        self.assertFalse(mappa.shipment_pushed)
        self.assertIn("unknown carrier",
                      self._registro("push_shipment")[0].message)

    def test_26_senza_trasferimento_concluso_non_si_chiama_nessuno(self):
        mappa = self._ordine_spedibile("CD-S26")
        esito, tr = self._push(mappa, [])
        self.assertFalse(esito)
        self.assertEqual(tr.sequenza(), [])

    def test_27_un_vettore_senza_traduzione_ferma_e_dice_dove_andare(self):
        marca = self.env["centrivo.carrier.brand"].create({
            "name": "Poste Italiane (prova)", "code": "poste-prova"})
        altro = self.env["delivery.carrier"].create({
            "name": "Poste (prova Cdiscount)",
            "product_id": self.env["product.product"].create({
                "name": "Trasporto Poste (prova)", "type": "service"}).id})
        self.env["centrivo.carrier.source"].create({
            "source_model": "delivery.carrier", "source_res_id": altro.id,
            "source_display": altro.display_name, "brand_id": marca.id,
            "company_id": self.env.company.id})
        mappa = self._ordine_spedibile("CD-S27")
        self._spedisci_in_odoo(mappa, tracking="POSTE-1", vettore=altro)
        esito, tr = self._push(mappa, [])
        self.assertFalse(esito)
        self.assertEqual(tr.sequenza(), [])
        self.assertIn("Cdiscount", self._registro("push_shipment")[0].message)

    def test_28_i_corrieri_dichiarati_sono_quelli_misurati(self):
        codici = dict(CD.CdiscountConnector.get_carrier_codes())
        self.assertEqual(len(codici), 66)
        self.assertEqual((codici["brt"], codici["gls"]), ("BRT", "GLS"))
        marchi = CD.CdiscountConnector.carrier_brand_codes
        self.assertEqual((marchi["brt"], marchi["gls"]), ("brt", "gls"))
        self.assertNotIn("poste", marchi)
