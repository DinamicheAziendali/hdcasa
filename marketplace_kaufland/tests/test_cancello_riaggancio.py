# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il cancello del riaggancio: quando si puo' creare offerte su Kaufland.

E' il pezzo piu' pericoloso del modulo, perche' l'errore non si vede da Odoo:
si vede sul marketplace, sotto forma di **offerte doppie vere**.

Il numero da cui nasce tutto: su Kaufland ci sono **166 offerte italiane, 165
delle quali nate senza che noi ne registrassimo l'identificativo**. Creare
offerte prima di sapere quali esistono significa duplicarle.

⚠️ La revisione del 2026-08-26 aveva fermato proprio qui un difetto vero: *«se
Kaufland avesse risposto con 136 offerte invece di 166 — coerentemente, col
totale che torna — i conti quadravano e la creazione avrebbe messo in vendita
30 doppioni veri»*. Quel difetto era passato **sotto test verdi**, perche' i
banchi in `tools/` non caricano i modelli e non fanno girare il giro vero.

Queste prove girano **dentro Odoo**, con un finto Kaufland che risponde come
risponderebbe lui. E' lo stesso trattamento che il 2026-08-29 su ManoMano ha
trovato due difetti veri che 37 banchi verdi non vedevano.
"""
import json

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

TRASPORTO = ("odoo.addons.marketplace_kaufland.connectors.kaufland_client."
             "TrasportoRequests.chiama")


def risposta(righe, totale=None):
    """Il corpo che Kaufland restituisce per l'elenco delle unita'.

    ⚠️ `total` e' quello che il client usa per capire se l'elenco e' completo:
    un finto che lo omettesse renderebbe la prova piu' facile del vero, ed e'
    esattamente il modo in cui su Kaufland tre difetti erano gia' passati
    sotto test verdi.
    """
    corpo = {"data": righe,
             "pagination": {"total": totale if totale is not None else len(righe)}}
    return 200, json.dumps(corpo), {}


@tagged("post_install", "-at_install", "centrivo_kaufland")
class TestCancelloRiaggancio(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        azienda = cls.env.company
        cls.prodotti = cls.env["product.product"].create([
            {"name": "Box doccia A", "default_code": "KFL-A",
             "barcode": "8000000000101"},
            {"name": "Box doccia B", "default_code": "KFL-B",
             "barcode": "8000000000102"},
            {"name": "Box doccia C", "default_code": "KFL-C",
             "barcode": "8000000000103"},
        ])
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Kaufland Italia (prova)",
            "connector_code": "kaufland",
            "company_id": azienda.id,
            # Valori finti: servono solo a superare i controlli di
            # configurazione. Nessuna chiamata esce davvero.
            "kaufland_client_key": "chiave-finta",
            "kaufland_secret_key": "segreto-finto",
        })
        # ⚠️ Dal 2026-08-29 il mercato e' una RIGA, e il cancello sta li'.
        cls.mercato = cls.env["centrivo.kaufland.market"].create({
            "channel_id": cls.canale.id,
            "storefront": "it",
            "shipping_group_id": "GRUPPO-IT",
        })

    # ------------------------------------------------------------------
    def _riaggancia_con(self, righe, totale=None):
        """Fa girare il riaggancio contro un finto Kaufland.

        Il finto risponde alla prima pagina con le righe, e a ogni pagina
        successiva con l'elenco vuoto — come fa Kaufland quando l'elenco e'
        finito.
        """
        chiamate = {"n": 0}

        def finto(self_trasporto, metodo, uri, teste, corpo):
            chiamate["n"] += 1
            if chiamate["n"] == 1:
                return risposta(righe, totale)
            return risposta([], totale if totale is not None else len(righe))

        with patch(TRASPORTO, finto):
            self.canale._get_connector().per_mercato("riaggancia")

    @staticmethod
    def _riga(codice, id_unit):
        return {"id_offer": codice, "id_unit": id_unit}

    def _stato_cancello(self):
        mercato = self.mercato.sudo()
        return (mercato.riagganciato, mercato.offerte_attese)

    # ------------------------------------------------------------------
    def test_01_la_prima_misura_non_apre_il_cancello(self):
        """La prima volta si MISURA e basta.

        ⚠️ E' l'unico giro che nessuna soglia protegge: se Kaufland ne
        restituisse meno di quante ce ne sono davvero, quel numero sbagliato
        nascerebbe qui come soglia giusta. Perche' il cancello si apra serve
        che una persona confermi il numero.
        """
        self._riaggancia_con([self._riga("KFL-A", "1"),
                              self._riga("KFL-B", "2")])
        aperto, attese = self._stato_cancello()
        self.assertFalse(
            aperto,
            "La prima misura NON deve autorizzare la creazione: un numero "
            "misurato e un numero verificato non sono la stessa cosa.")
        self.assertEqual(
            attese, 2,
            "Ma il numero misurato si scrive sul canale, cosi' il giro "
            "successivo ha una soglia da difendere.")

    def test_02_una_lettura_PARZIALE_non_apre_il_cancello(self):
        """⚠️ IL CONTROLLO CHE VALE PIU' DI TUTTI.

        Il canale sa che le offerte vive sono 5. Kaufland ne restituisce 3, e
        lo fa in modo **coerente**: il totale dichiarato torna, le pagine
        tornano, nessuna riga finisce in un secchio strano. Dal di dentro
        sembra tutto a posto.

        Se il cancello si aprisse, la creazione metterebbe in vendita le 2
        offerte «mancanti» — che su Kaufland esistono gia'. **Doppioni veri su
        un marketplace vero**, e nessun errore da nessuna parte.
        """
        self.mercato.sudo().write({"offerte_attese": 5})
        self._riaggancia_con([self._riga("KFL-A", "1"),
                              self._riga("KFL-B", "2"),
                              self._riga("KFL-C", "3")])
        aperto, attese = self._stato_cancello()
        self.assertFalse(
            aperto,
            "Un elenco piu' corto di quello atteso NON e' la prova che le "
            "offerte siano diminuite: puo' essere un filtro della loro API o "
            "un permesso piu' stretto. Il cancello resta chiuso.")
        self.assertEqual(
            attese, 5,
            "⚠️ E la soglia NON si abbassa da sola: abbassarla renderebbe la "
            "lettura parziale la nuova verita', e al giro dopo il cancello si "
            "aprirebbe sul numero sbagliato.")

    def test_03_le_offerte_che_crescono_aprono_il_cancello(self):
        """Il verso opposto, e deve funzionare senza chiedere permesso.

        Le offerte che crescono sono la vita normale del canale: la soglia si
        alza da sola e il cancello si apre. Senza questa prova, un banco che
        controlla solo i casi negativi lascerebbe passare un cancello che non
        si apre MAI — cioe' un modulo che non serve a niente.
        """
        self.mercato.sudo().write({"offerte_attese": 2})
        self._riaggancia_con([self._riga("KFL-A", "1"),
                              self._riga("KFL-B", "2"),
                              self._riga("KFL-C", "3")])
        aperto, attese = self._stato_cancello()
        self.assertTrue(
            aperto, "Con tutte le righe agganciate e le offerte cresciute, il "
                    "cancello si apre.")
        self.assertEqual(attese, 3, "E la soglia si alza da sola.")

    def test_04_una_riga_senza_prodotto_CHIUDE_il_cancello(self):
        """⚠️ L'asimmetria che la revisione ha corretto.

        Prima, un `id_offer` che nessun prodotto Odoo portava non chiudeva il
        cancello, e il ragionamento era: «non puo' essere ricreato, manca il
        prodotto da cui nascerebbe». **Non e' vero**: il riaggancio accoppia
        per `default_code`, la ricognizione accoppia per etichetta + codice a
        barre. Basta un `default_code` cambiato dopo la creazione dell'offerta
        perche' il riaggancio non accoppi, la ricognizione dica «pronta», e la
        creazione metta in vendita una SECONDA offerta sullo stesso codice a
        barre.
        """
        self.mercato.sudo().write({"offerte_attese": 2})
        self._riaggancia_con([self._riga("KFL-A", "1"),
                              self._riga("KFL-SCONOSCIUTO", "9")])
        aperto, _attese = self._stato_cancello()
        self.assertFalse(
            aperto,
            "Un'offerta viva che qui non trova il suo prodotto e' proprio il "
            "caso che puo' diventare un doppione: il cancello si chiude.")

    def test_05_il_cancello_gia_aperto_si_RICHIUDE_se_il_giro_trova_un_guaio(self):
        """Un giro che trova un problema TOGLIE l'autorizzazione.

        ⚠️ Prima il cancello si scriveva in un verso solo: un canale gia'
        autorizzato restava autorizzato anche quando il riaggancio scopriva
        un'offerta orfana. «Non lo apre» e «lo chiude» sono la stessa cosa
        solo al primo giro.
        """
        self.mercato.sudo().write({
            "offerte_attese": 2,
            "riagganciato": True,
        })
        self._riaggancia_con([self._riga("KFL-A", "1"),
                              self._riga("KFL-SCONOSCIUTO", "9")])
        aperto, _attese = self._stato_cancello()
        self.assertFalse(
            aperto,
            "Il cancello va richiuso, non lasciato dov'era: un'autorizzazione "
            "vecchia accanto a un guaio nuovo si legge come se valesse "
            "ancora.")
