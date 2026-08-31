# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Un canale, piu' mercati — e ogni mercato conta per conto suo.

Deciso da Angelo il 2026-08-29: le chiavi API di Kaufland sono dell'account, non
del mercato, e il mercato viaggia come parametro su ogni chiamata
(`?storefront=it`). Cinque mercati non devono voler dire cinque canali con lo
stesso segreto copiato cinque volte.

⚠️ **Ma la ragione vera di questo lavoro non e' la comodita'.** Il cancello del
riaggancio — cio' che autorizza a CREARE offerte — oggi e' per canale: un valore
solo. Mettere cinque mercati in un canale senza toccarlo significherebbe che
**una lettura parziale su Kaufland.de apre o chiude il cancello anche per
l'Italia**, cioe' perdere la protezione che vale piu' di tutte (vedi
`test_cancello_riaggancio.py`).

Il mercato non e' un'etichetta: **e' l'unita' su cui si conta.**

Piano: docs/superpowers/plans/2026-08-29-kaufland-multi-mercato.md
"""
import json

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

TRASPORTO = ("odoo.addons.marketplace_kaufland.connectors.kaufland_client."
             "TrasportoRequests.chiama")


def corpo(righe):
    return 200, json.dumps({"data": righe,
                            "pagination": {"total": len(righe)}}), {}


@tagged("post_install", "-at_install", "centrivo_kaufland")
class TestMercati(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["product.product"].create([
            {"name": "Box doccia IT-1", "default_code": "MKT-1"},
            {"name": "Box doccia IT-2", "default_code": "MKT-2"},
            {"name": "Box doccia IT-3", "default_code": "MKT-3"},
        ])
        # ⚠️ UN canale, e le credenziali scritte UNA VOLTA SOLA: e' il punto
        # di partenza di Angelo. Oggi lo stesso segreto andrebbe copiato su
        # cinque canali, e chi ne dimentica uno rompe quel mercato in silenzio.
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Kaufland (prova multi-mercato)",
            "connector_code": "kaufland",
            "company_id": cls.env.company.id,
            "kaufland_client_key": "chiave-finta",
            "kaufland_secret_key": "segreto-finto",
        })
        Mercato = cls.env["centrivo.kaufland.market"]
        cls.italia = Mercato.create({
            "channel_id": cls.canale.id,
            "storefront": "it",
            "shipping_group_id": "GRUPPO-IT",
            "offerte_attese": 3,
            "riagganciato": True,
        })
        cls.germania = Mercato.create({
            "channel_id": cls.canale.id,
            "storefront": "de",
            # ⚠️ Gruppo DIVERSO, e non e' un dettaglio: lo dice gia' il campo
            # di oggi — «quello di Kaufland.de non vale per Kaufland.it».
            "shipping_group_id": "GRUPPO-DE",
            "offerte_attese": 3,
            "riagganciato": True,
        })

    # ------------------------------------------------------------------
    def _riaggancia(self, per_mercato):
        """Finto Kaufland che risponde in modo DIVERSO per ogni mercato.

        Il mercato si legge dall'indirizzo (`?storefront=de`), che e' come
        Kaufland lo riceve davvero.
        """
        viste = {}

        def finto(self_trasporto, metodo, uri, teste, corpo_richiesta):
            mercato = "de" if "storefront=de" in uri else "it"
            viste[mercato] = viste.get(mercato, 0) + 1
            righe = per_mercato.get(mercato, [])
            # Prima pagina le righe, poi vuoto: come fa Kaufland a fine elenco.
            return corpo(righe if viste[mercato] == 1 else [])

        with patch(TRASPORTO, finto):
            # ⚠️ `per_mercato`, non `riaggancia()`: e' il giro che il bottone
            # fa davvero, ed e' l'unico che mette il connettore su un mercato.
            self.canale._get_connector().per_mercato("riaggancia")
        return viste

    @staticmethod
    def _riga(codice, id_unit):
        return {"id_offer": codice, "id_unit": id_unit}

    # ------------------------------------------------------------------
    def test_01_i_mercati_stanno_su_UN_canale_solo(self):
        """Le credenziali si scrivono una volta, i mercati sono righe.

        Se questo controllo fallisce, siamo tornati a «un canale per mercato» e
        tutto il resto del disegno non ha piu' senso.
        """
        self.assertEqual(len(self.canale.kaufland_market_ids), 2)
        self.assertEqual(
            set(self.canale.kaufland_market_ids.mapped("storefront")),
            {"it", "de"})
        self.assertEqual(
            self.italia.shipping_group_id, "GRUPPO-IT",
            "Ogni mercato porta il SUO gruppo di spedizione.")
        self.assertEqual(self.germania.shipping_group_id, "GRUPPO-DE")

    def test_02_il_cancello_e_di_OGNI_MERCATO_non_del_canale(self):
        """⚠️ IL CONTROLLO PER CUI ESISTE QUESTO LAVORO.

        L'Italia risponde bene (3 offerte su 3 attese): il suo cancello resta
        aperto. La Germania risponde PARZIALE (1 su 3 attese): il suo si
        chiude.

        Col disegno di oggi — un cancello solo per tutto il canale — la
        Germania si porterebbe dietro l'Italia, e su un mercato che funziona si
        smetterebbe di poter creare offerte per colpa di un altro. Oppure, nel
        verso peggiore, l'Italia terrebbe aperto un cancello che la Germania ha
        appena dimostrato di non meritare.
        """
        self._riaggancia({
            "it": [self._riga("MKT-1", "11"), self._riga("MKT-2", "12"),
                   self._riga("MKT-3", "13")],
            "de": [self._riga("MKT-1", "21")],
        })
        self.assertTrue(
            self.italia.riagganciato,
            "L'Italia ha letto tutto quello che si aspettava: il suo cancello "
            "resta aperto.")
        self.assertFalse(
            self.germania.riagganciato,
            "La Germania ha letto 1 offerta su 3 attese: il SUO cancello si "
            "chiude — e solo il suo.")

    def test_03_le_offerte_appartengono_al_MERCATO(self):
        """Lo stesso prodotto puo' avere un'offerta su ogni mercato.

        ⚠️ Con la chiave di oggi — unica per (canale, prodotto) — il secondo
        mercato non potrebbe nemmeno esistere: la riga andrebbe in conflitto
        con quella del primo. E' il vincolo che va spostato sul mercato.
        """
        self._riaggancia({
            "it": [self._riga("MKT-1", "11")],
            "de": [self._riga("MKT-1", "21")],
        })
        Offerta = self.env["kaufland.offer"].sudo()
        righe = Offerta.search([("market_id", "in",
                                 self.canale.kaufland_market_ids.ids)])
        self.assertEqual(
            len(righe), 2,
            "Lo stesso prodotto ha un'offerta per mercato: due righe, non una.")
        self.assertEqual(
            set(righe.mapped("market_id.storefront")), {"it", "de"})

    def test_04_un_mercato_che_ROMPE_mostra_il_suo_messaggio(self):
        """⚠️ Il difetto vero del 2026-08-29, al primo uso su un canale vero.

        Il canale non aveva l'etichetta prodotto. Il connettore lo diceva
        benissimo — «non e' indicata alcuna etichetta prodotto: non so quali
        prodotti portare su Kaufland» — ma chi aveva premuto il bottone vedeva
        `KeyError: 'guardati'`: i contatori non esistevano e venivano letti lo
        stesso.

        **Un guasto che nasconde il proprio messaggio costa piu' del guasto**:
        manda a cercare un difetto del modulo dove c'era una casella da
        compilare.
        """
        # Nessuna etichetta prodotto sul canale: e' la configurazione vera che
        # ha prodotto l'errore.
        self.canale.export_product_tag_ids = [(5, 0, 0)]
        azione = self.canale.action_kaufland_ricognizione()
        parametri = azione.get("params") or {}
        self.assertEqual(
            parametri.get("type"), "danger",
            "Un mercato che rompe deve dare una notifica di guasto, non "
            "un'eccezione.")
        self.assertIn(
            "etichetta prodotto", parametri.get("message") or "",
            "E il messaggio dev'essere QUELLO del connettore: e' l'unico che "
            "dice cosa fare.")

    def test_05_il_messaggio_dice_QUALE_mercato_e_rimasto_indietro(self):
        """⚠️ Il difetto di comunicazione del 2026-08-31.

        Con l'Italia aperta e la Germania alla prima misura, la notifica
        diceva «la guardia RESTA CHIUSA» — al singolare, senza nominare
        nessuno. Il registro distingueva i due mercati, il messaggio a video
        no: e chi legge va a cercare il guasto sul mercato sbagliato, mentre
        l'altro e' aperto e funzionante.
        """
        riunito = self.canale._kaufland_riunisci({
            "it": {"completo": True, "lette": 166, "agganciate": 166},
            "de": {"completo": False, "prima_misura": True, "lette": 166,
                   "agganciate": 166},
        })
        self.assertFalse(
            riunito["completo"],
            "Un mercato non completo rende non completo il giro.")
        self.assertEqual(
            riunito.get("mercati_da_finire"), "de",
            "E il giro deve dire QUALE mercato e' rimasto indietro.")
        self.assertEqual(
            riunito["lette"], 332,
            "I contatori invece si sommano: 166 + 166.")
