# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Le OFFERTE Cdiscount (Consegna 2), provate dentro un Odoo vero con un
trasporto finto: nessuna chiamata esce.

⚠️ Perché qui e non in `tools/`: i banchi fuori da Odoo provano la FORMA
(`tools/test_cdiscount_offerte.py`); qui si prova che il connettore la usa
davvero contro il listino, le giacenze e i modelli veri — cioè il pezzo che
«verde in tools» non copre (lezione del 2026-08-28: quindici moduli fermati
dal primo Odoo vero).

Il ciclo LETTO sulla documentazione (`docs/cdiscount-offerte-contratto.md`):
`GET /sellers/delivery-modes` (misurato) → `POST /offer-packages` (201, numero
nel `Content-Location`) → `POST …/offer-requests` a lotti da 100 →
`PATCH …` `{"state": "Ready"}` → poi il raccoglitore: `GET /offer-packages/{id}`
e `GET …/offer-requests-results`.
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
    RIFIUTATO, RIUSCITO)
from odoo.addons.marketplace_cdiscount.models.cdiscount_offerta import (
    DA_MANDARE, RITIRATA)
from odoo.addons.marketplace_cdiscount.models.cdiscount_pacchetto import (
    APERTO, RACCOLTO, TIPO_OFFERTE)
from odoo.addons.marketplace_cdiscount.models.cdiscount_scheda import (
    IN_ATTESA, SCONOSCIUTO_SCHEDA)

MODI = {"count": 2, "items": [
    {"code": "TRK", "name": "Envoi Suivi", "delivery_delay": 0,
     "is_express_delivery": False, "more_than30_kg_product": False},
    {"code": "REG", "name": "Recommandé", "delivery_delay": 0,
     "is_express_delivery": False, "more_than30_kg_product": False}]}


class Trasporto(object):
    """Il trasporto finto: risposte per (metodo, pezzo di percorso), in coda.

    Registra ogni chiamata, gettone escluso. Una chiamata senza risposta
    preparata fa fallire la prova: e' una chiamata che il connettore non
    doveva fare.
    """

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


def ok_modi():
    return ("GET", r"^/sellers/delivery-modes$", (200, MODI, {}))


def pacchetto_nato(numero="PKG001"):
    return ("POST", r"^/offer-packages$",
            (201, None, {"Content-Location": "/offer-packages/%s" % numero}))


def lotto_preso(numero="PKG001"):
    return ("POST", r"^/offer-packages/%s/offer-requests$" % numero,
            (200, {"accepted": True}, {}))


def pronto(numero="PKG001", stato=200):
    return ("PATCH", r"^/offer-packages/%s$" % numero,
            (stato, None if stato == 204 else {"state": "Ready"}, {}))


def stato_pacchetto(numero, stato):
    return ("GET", r"^/offer-packages/%s$" % numero,
            (200, {"packageId": numero, "state": stato}, {}))


def esiti(numero, righe, link=""):
    teste = {"Link": link} if link else {}
    return ("GET", r"^/offer-packages/%s/offer-requests-results" % numero,
            (200, {"itemsPerPage": len(righe), "items": righe}, teste))


def riga_esito(codice, stato="Integrated", messaggio="Offer created"):
    return {"sellerExternalReference": codice, "integrationStatus": stato,
            "results": [{"resultCode": "8000", "message": messaggio}]}


@tagged("post_install", "-at_install", "centrivo_cdiscount")
class TestOfferte(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.listino = cls.env["product.pricelist"].create({
            "name": "Listino Francia (prova)", "currency_id":
            cls.env.company.currency_id.id})
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Cdiscount (prova offerte)",
            "connector_code": "cdiscount",
            "company_id": cls.env.company.id,
            "cdiscount_client_id": "id", "cdiscount_client_secret": "segreto",
            "cdiscount_seller_id": "424639",
            "cdiscount_canale_vendita": "CDISFR",
            "cdiscount_categoria": "0H0805",
            "pricelist_selling_id": cls.listino.id,
            "cdiscount_modo_consegna": "TRK",
            "cdiscount_spedizione_costo": 4.9,
            "cdiscount_spedizione_costo_aggiuntivo": 1.0,
            "cdiscount_iva": 20.0,
            "processing_time_default": 3,
            "stock_quantity_type": "qty_available",
            "stock_scope": "company",
        })
        cls.Scheda = cls.env["cdiscount.scheda"]
        cls.Offerta = cls.env["cdiscount.offerta"]
        cls.Pacchetto = cls.env["cdiscount.pacchetto"]

    # ------------------------------------------------------------------
    _contatore = 0

    def _prodotto(self, codice, prezzo, ecotax=0.35, peso=10.0, gtin=None):
        # ⚠️ Il codice a barre dev'essere di SOLE CIFRE, unico per prova: un
        # GTIN storto fa saltare l'offerta prima di qualunque altra cosa.
        type(self)._contatore += 1
        tmpl = self.env["product.template"].create({
            "name": "Prodotto %s" % codice, "default_code": codice,
            # ⚠️ Odoo 18: un prodotto ha giacenza solo se `is_storable`;
            # senza, i quant non si creano («Quants cannot be created for
            # consumables»).
            "list_price": prezzo, "type": "consu", "is_storable": True,
            "weight": peso,
            "cdiscount_ecotax": ecotax,
            "barcode": gtin or ("8057680%06d" % type(self)._contatore),
        })
        return tmpl.product_variant_id

    def _scheda_riuscita(self, codice, prezzo=49.99, **extra):
        prodotto = self._prodotto(codice, prezzo, **extra)
        scheda = self.Scheda.create({
            "channel_id": self.canale.id, "codice": codice,
            "product_id": prodotto.id, "stato": RIUSCITO,
            "titolo": "t", "descrizione": "d", "immagini": "https://a/b.jpg"})
        return prodotto, scheda

    def _giro(self, risposte, limite=None):
        trasporto = Trasporto(risposte)
        with patch.object(CD, "TrasportoRequests", lambda: trasporto):
            esito = self.canale._get_connector().allinea_offerte(limite=limite)
        return esito, trasporto

    def _raccolta(self, risposte):
        trasporto = Trasporto(risposte)
        with patch.object(CD, "TrasportoRequests", lambda: trasporto):
            esito = self.canale._get_connector().raccogli()
        return esito, trasporto

    def _offerte(self):
        return {o.codice: o for o in self.Offerta.search(
            [("channel_id", "=", self.canale.id)])}

    # ------------------------------------------------------------------
    def test_01_le_guardie_prima_di_scrivere(self):
        self._scheda_riuscita("OFF-G1")
        self.canale.pricelist_selling_id = False
        with self.assertRaises(UserError, msg="senza listino non si parte"):
            self._giro([ok_modi()])
        self.canale.pricelist_selling_id = self.listino.id
        self.canale.cdiscount_modo_consegna = False
        with self.assertRaises(UserError):
            self._giro([ok_modi()])
        # ⚠️ Un modo che l'account non ha (i codici della documentazione,
        # THD/EHD/SHD, NON sono nostri: misurato) ferma tutto PRIMA di scrivere.
        self.canale.cdiscount_modo_consegna = "THD"
        with self.assertRaises(UserError) as cm:
            _e, tr = self._giro([ok_modi(), pacchetto_nato()])
        self.assertIn("THD", str(cm.exception))
        self.canale.cdiscount_modo_consegna = "TRK"
        # La lettura dei modi che non arriva: si ferma, «riprova».
        with self.assertRaises(UserError) as cm:
            self._giro([("GET", r"^/sellers/delivery-modes$",
                         (502, "<html>Bad Gateway</html>", {}))])
        self.assertIn("non si e' potuto", str(cm.exception).replace("è", "e'"))

    def test_02_il_primo_invio_e_la_forma_di_cio_che_parte(self):
        p1, s1 = self._scheda_riuscita("OFF-A1", prezzo=49.99, ecotax=0.35)
        p2, s2 = self._scheda_riuscita("OFF-A2", prezzo=10.0, ecotax=0.0)
        # giacenza: p1 ne ha 12
        self.env["stock.quant"]._update_available_quantity(
            p1, self.env.ref("stock.warehouse0").lot_stock_id, 12)
        esito, tr = self._giro([ok_modi(), pacchetto_nato(), lotto_preso(),
                                pronto()])
        self.assertEqual(tr.sequenza(), [
            ("GET", "/sellers/delivery-modes"),
            ("POST", "/offer-packages"),
            ("POST", "/offer-packages/PKG001/offer-requests"),
            ("PATCH", "/offer-packages/PKG001")])
        self.assertEqual(tr.corpo("POST", r"^/offer-packages$"),
                         {"packageType": "Upsert"})
        self.assertEqual(tr.corpo("PATCH", r"PKG001$"), {"state": "Ready"})
        lotto = tr.corpo("POST", r"offer-requests$")
        self.assertEqual(len(lotto), 1, "OFF-A2 ha ecotax zero: si salta")
        offerta = lotto[0]
        self.assertEqual(offerta["sellerExternalReference"], "OFF-A1")
        self.assertEqual(offerta["product"], {"gtin": p1.barcode})
        self.assertEqual(offerta["condition"], "New")
        self.assertEqual(offerta["price"]["price"], 49.99)
        self.assertEqual(offerta["price"]["taxes"],
                         [{"code": "VAT", "value": 20},
                          {"code": "Ecotax", "value": 0.35}])
        self.assertEqual(offerta["deliveryModes"],
                         [{"code": "TRK", "cost": 4.9, "additionalCost": 1.0}])
        self.assertEqual(offerta["preparationTime"], 3)
        self.assertEqual(offerta["quantity"], 12)
        # I conti
        self.assertEqual((esito["mandate"], esito["pacchetti"],
                          esito["saltate"], esito["ritiri"]), (1, 1, 1, 0))
        self.assertEqual(esito["modo"], "TRK")
        # Le righe
        off = self._offerte()
        self.assertEqual(off["OFF-A1"].stato, IN_ATTESA)
        self.assertEqual((off["OFF-A1"].ultimo_prezzo,
                          off["OFF-A1"].ultima_quantita,
                          off["OFF-A1"].ultima_ecotax), (49.99, 12, 0.35))
        self.assertTrue(off["OFF-A1"].mandata_il)
        self.assertEqual(off["OFF-A1"].scheda_id, s1)
        self.assertEqual(off["OFF-A2"].stato, DA_MANDARE)
        self.assertIn("éco-participation", off["OFF-A2"].motivo)
        # Il pacchetto
        pacchetto = off["OFF-A1"].pacchetto_id
        self.assertEqual((pacchetto.numero, pacchetto.tipo, pacchetto.stato,
                          pacchetto.pronto), ("PKG001", TIPO_OFFERTE, APERTO,
                                              True))
        # ⚠️ Il registro non e' verde: una saltata e' una notizia.
        riga = self.env["centrivo.job.log"].search(
            [("channel_id", "=", self.canale.id),
             ("operation", "=", "cdiscount_allinea_offerte")], limit=1,
            order="id desc")
        self.assertEqual(riga.result, "error")
        self.assertIn("OFF-A2", riga.message)

    def test_03_ecotax_a_zero_va_bene_se_il_canale_non_la_pretende(self):
        self._scheda_riuscita("OFF-Z1", prezzo=10.0, ecotax=0.0)
        self.canale.cdiscount_ecotax_obbligatoria = False
        esito, tr = self._giro([ok_modi(), pacchetto_nato(), lotto_preso(),
                                pronto()])
        self.assertEqual(esito["saltate"], 0)
        self.assertEqual(tr.corpo("POST", r"offer-requests$")[0]["price"]
                         ["taxes"][1], {"code": "Ecotax", "value": 0})

    def test_04_invariata_non_si_rimanda_e_cambiata_si(self):
        p, s = self._scheda_riuscita("OFF-B1", prezzo=20.0)
        self._giro([ok_modi(), pacchetto_nato(), lotto_preso(), pronto()])
        off = self._offerte()["OFF-B1"]
        off.write({"stato": RIUSCITO})   # come dopo una raccolta positiva
        esito, tr = self._giro([ok_modi()])
        self.assertEqual((esito["invariate"], esito["mandate"]), (1, 0))
        self.assertEqual(tr.sequenza(), [("GET", "/sellers/delivery-modes")],
                         "niente da mandare: nessun pacchetto nasce")
        # Cambia il prezzo: riparte.
        p.product_tmpl_id.list_price = 25.0
        esito, tr = self._giro([ok_modi(), pacchetto_nato("PKG002"),
                                lotto_preso("PKG002"), pronto("PKG002")])
        self.assertEqual(esito["mandate"], 1)
        self.assertEqual(tr.corpo("POST", r"offer-requests$")[0]["price"]
                         ["price"], 25.0)
        self.assertEqual(self._offerte()["OFF-B1"].ultimo_prezzo, 25.0)

    def test_05_una_riga_in_volo_non_si_tocca(self):
        self._scheda_riuscita("OFF-C1", prezzo=20.0)
        self._giro([ok_modi(), pacchetto_nato(), lotto_preso(), pronto()])
        esito, tr = self._giro([ok_modi()])
        self.assertEqual(esito["mandate"], 0)
        self.assertEqual(self._offerte()["OFF-C1"].stato, IN_ATTESA)

    def test_06_il_ritiro_manda_quantita_zero_una_volta(self):
        p, s = self._scheda_riuscita("OFF-R1", prezzo=20.0)
        self._giro([ok_modi(), pacchetto_nato(), lotto_preso(), pronto()])
        off = self._offerte()["OFF-R1"]
        off.write({"stato": RIUSCITO})
        # La scheda non e' piu' riuscita: l'offerta va ritirata.
        s.write({"stato": RIFIUTATO})
        esito, tr = self._giro([ok_modi(), pacchetto_nato("PKG002"),
                                lotto_preso("PKG002"), pronto("PKG002")])
        self.assertEqual((esito["mandate"], esito["ritiri"]), (1, 1))
        corpo = tr.corpo("POST", r"offer-requests$")[0]
        self.assertEqual(corpo["quantity"], 0)
        self.assertEqual(corpo["sellerExternalReference"], "OFF-R1")
        off = self._offerte()["OFF-R1"]
        self.assertTrue(off.ritiro)
        self.assertEqual(off.stato, IN_ATTESA)
        # Il raccoglitore: integrata → «ritirata», non «viva».
        self._raccolta([stato_pacchetto("PKG002", "Integrated"),
                        esiti("PKG002", [riga_esito("OFF-R1")])])
        off = self._offerte()["OFF-R1"]
        self.assertEqual(off.stato, RITIRATA)
        # E non si ritira due volte.
        esito, tr = self._giro([ok_modi()])
        self.assertEqual(esito["mandate"], 0)

    def test_07_oltre_30_kg_su_un_modo_che_non_li_regge_si_salta(self):
        self._scheda_riuscita("OFF-P1", prezzo=300.0, peso=45.0)
        esito, tr = self._giro([ok_modi()])
        self.assertEqual(esito["saltate"], 1)
        self.assertIn("30", self._offerte()["OFF-P1"].motivo)

    def test_08_senza_gtin_o_senza_prezzo_si_salta_e_lo_dice(self):
        p1, _s = self._scheda_riuscita("OFF-S1", prezzo=0.0)
        p2, _s = self._scheda_riuscita("OFF-S2", prezzo=10.0)
        p2.product_tmpl_id.barcode = False
        esito, tr = self._giro([ok_modi()])
        self.assertEqual(esito["saltate"], 2)
        off = self._offerte()
        self.assertIn("prezzo", off["OFF-S1"].motivo)
        self.assertIn("GTIN", off["OFF-S2"].motivo)

    def test_09_il_limite_conta_le_offerte(self):
        for n in range(3):
            self._scheda_riuscita("OFF-L%d" % n, prezzo=10.0 + n)
        esito, tr = self._giro([ok_modi(), pacchetto_nato(), lotto_preso(),
                                pronto()], limite=1)
        self.assertEqual(esito["mandate"], 1)
        self.assertEqual(len(tr.corpo("POST", r"offer-requests$")), 1)
        self.assertEqual(esito["rimaste"], 2)

    def test_10_il_patch_che_fallisce_lascia_il_pacchetto_da_mandare(self):
        self._scheda_riuscita("OFF-T1", prezzo=10.0)
        esito, tr = self._giro([ok_modi(), pacchetto_nato(), lotto_preso(),
                                ("PATCH", r"PKG001$",
                                 (502, "<html>Bad Gateway</html>", {}))])
        pacchetto = self.Pacchetto.search([("channel_id", "=", self.canale.id),
                                           ("numero", "=", "PKG001")])
        self.assertEqual((pacchetto.stato, pacchetto.pronto), (APERTO, False))
        self.assertEqual(self._offerte()["OFF-T1"].stato, IN_ATTESA)
        self.assertTrue(esito.get("fermata"))
        # Il raccoglitore ritenta il PATCH prima di chiedere l'esito.
        esito_r, tr = self._raccolta([pronto(),
                                      stato_pacchetto("PKG001",
                                                      "IntegrationPending")])
        self.assertEqual(tr.sequenza()[0], ("PATCH", "/offer-packages/PKG001"))
        self.assertTrue(pacchetto.pronto)
        self.assertEqual(esito_r["in_lavorazione"], 1)

    def test_11_la_raccolta_scrive_i_verdetti_e_chiude(self):
        self._scheda_riuscita("OFF-E1", prezzo=10.0)
        self._scheda_riuscita("OFF-E2", prezzo=11.0)
        self._giro([ok_modi(), pacchetto_nato(), lotto_preso(), pronto()])
        # Ancora in lavorazione: niente scritto.
        esito, tr = self._raccolta([stato_pacchetto("PKG001",
                                                    "IntegrationPending")])
        self.assertEqual(esito["in_lavorazione"], 1)
        self.assertEqual(self._offerte()["OFF-E1"].stato, IN_ATTESA)
        # Integrato: un esito per riga.
        esito, tr = self._raccolta([
            stato_pacchetto("PKG001", "Integrated"),
            esiti("PKG001", [riga_esito("OFF-E1"),
                             riga_esito("OFF-E2", "Rejected",
                                        "Price below minimum")])])
        off = self._offerte()
        self.assertEqual(off["OFF-E1"].stato, RIUSCITO)
        self.assertEqual(off["OFF-E2"].stato, RIFIUTATO)
        self.assertIn("Price below minimum", off["OFF-E2"].motivo)
        self.assertEqual(off["OFF-E1"].pacchetto_id.stato, RACCOLTO)
        self.assertEqual((esito["confermate"], esito["rifiutate"],
                          esito["chiusi"]), (1, 1, 1))

    def test_12_gli_esiti_a_pagine_si_leggono_tutti(self):
        for n in range(3):
            self._scheda_riuscita("OFF-Q%d" % n, prezzo=10.0 + n)
        self._giro([ok_modi(), pacchetto_nato(), lotto_preso(), pronto()])
        esito, tr = self._raccolta([
            stato_pacchetto("PKG001", "Integrated"),
            esiti("PKG001", [riga_esito("OFF-Q0"), riga_esito("OFF-Q1")],
                  link='</offer-packages/PKG001/offer-requests-results'
                       '?cursor=abc&limit=100>; rel="next"'),
            ("GET", r"offer-requests-results\?.*cursor=abc",
             (200, {"itemsPerPage": 1, "items": [riga_esito("OFF-Q2")]}, {})),
        ])
        self.assertEqual(esito["confermate"], 3)
        self.assertEqual([m for m, _p in tr.sequenza()].count("GET"), 3)

    def test_13_un_pacchetto_rifiutato_in_blocco(self):
        self._scheda_riuscita("OFF-K1", prezzo=10.0)
        self._giro([ok_modi(), pacchetto_nato(), lotto_preso(), pronto()])
        esito, tr = self._raccolta([
            ("GET", r"^/offer-packages/PKG001$",
             (200, {"packageId": "PKG001", "state": "Rejected",
                    "result": {"resultCode": "2394",
                               "message": "Package rejected"}}, {}))])
        off = self._offerte()["OFF-K1"]
        self.assertEqual(off.stato, RIFIUTATO)
        self.assertIn("Package rejected", off.motivo)
        self.assertEqual(off.pacchetto_id.stato, RACCOLTO)

    def test_14_il_lotto_rifiutato_non_ferma_gli_altri(self):
        for n in range(150):
            self._scheda_riuscita("OFF-M%03d" % n, prezzo=10.0 + n,
                                  gtin="4006381%06d" % n)
        esito, tr = self._giro([
            ok_modi(), pacchetto_nato(),
            ("POST", r"offer-requests$",
             (400, {"title": "Validation Failed",
                    "errors": {"price": ["troppo basso"]}}, {})),
            lotto_preso(), pronto()])
        self.assertEqual((esito["mandate"], esito["rifiutate"]), (50, 100))
        stati = [o.stato for o in self.Offerta.search(
            [("channel_id", "=", self.canale.id)])]
        self.assertEqual(stati.count(RIFIUTATO), 100)
        self.assertEqual(stati.count(IN_ATTESA), 50)

    def test_15_un_pacchetto_scaduto_lascia_le_offerte_senza_verdetto(self):
        self._scheda_riuscita("OFF-X1", prezzo=10.0)
        self._giro([ok_modi(), pacchetto_nato(), lotto_preso(), pronto()])
        pacchetto = self._offerte()["OFF-X1"].pacchetto_id
        pacchetto.write({"scade_il": "2020-01-01 00:00:00"})
        esito, tr = self._raccolta([])
        self.assertEqual(esito["scaduti"], 1)
        self.assertEqual(self._offerte()["OFF-X1"].stato, SCONOSCIUTO_SCHEDA)

    def test_16_il_cron_nasce_spento(self):
        cron = self.env.ref("marketplace_cdiscount.cron_cdiscount_allinea_offerte")
        self.assertFalse(cron.active)
