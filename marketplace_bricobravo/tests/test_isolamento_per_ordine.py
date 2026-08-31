# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Un ordine che rompe non deve portarsi via lo scarico degli altri.

⚠️ **E' qui che il difetto morde in produzione**: `centrivo.order.map` ha **168
ordini mappati**, e BricoBravo e' un canale che scarica ordini veri ogni giorno.
L'isolamento fra CANALI protegge il giro; questo protegge il singolo giro
dall'ordine storto che ci finisce dentro.

Due cicli, e non sono lo stesso:

1. **lo scarico** (`pull_orders`): un ordine che rompe la banca dati non deve
   annullare quelli gia' importati ne' fermare quelli dopo;
2. **la ripresa degli acquisiti** (`retry_pending_acquired`): prima non aveva
   nemmeno un gestore d'errore — **un ordine rimasto indietro ieri fermava lo
   scarico di oggi**, perche' la ripresa e' la prima cosa che il pull chiama.

Il guasto e' VERO (`SELECT 1/0` eseguito da PostgreSQL): un'eccezione Python
lascerebbe la transazione sana, e la prova passerebbe anche su un codice rotto.
"""
from unittest.mock import patch

from odoo.addons.integrations_core.connectors.transport import TransportResponse
from odoo.tests.common import TransactionCase, tagged


def _pagina(ordini):
    """Una pagina di ordini come la restituisce BricoBravo."""
    return TransportResponse(200, {"data": ordini, "pagination": {"pages": 1}})


class BaseOrdini(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "BricoBravo (prova isolamento ordini)",
            "connector_code": "bricobravo",
            "company_id": cls.env.company.id,
            "api_key": "chiave-finta",
        })

    def _spacca(self):
        """Un guasto che viene da PostgreSQL, non un RuntimeError."""
        self.env.cr.execute("SELECT 1 / 0")

    def _mappa(self, esterno):
        return self.env["centrivo.order.map"].sudo().search([
            ("channel_id", "=", self.canale.id),
            ("external_id", "=", esterno),
        ])

    def _esiste_nel_database(self, esterno):
        """⚠️ Letto in SQL: la cache dell'ORM direbbe di si' anche per una
        riga che non ha mai raggiunto il database."""
        self.env.cr.execute(
            "SELECT COUNT(*) FROM centrivo_order_map "
            "WHERE channel_id = %s AND external_id = %s",
            (self.canale.id, esterno))
        return self.env.cr.fetchone()[0] > 0

    def _crea_mappa(self, esterno, stato="imported", acquisito=False):
        riga = self.env["centrivo.order.map"].sudo().create({
            "channel_id": self.canale.id,
            "external_id": esterno,
            "state": stato,
            "acquired_done": acquisito,
            "company_id": self.canale.company_id.id,
        })
        self.env.flush_all()
        return riga


# ===========================================================================
# 1. LO SCARICO — un ordine storto in mezzo a tre
# ===========================================================================
@tagged("post_install", "-at_install", "centrivo_bricobravo")
class TestIsolamentoScarico(BaseOrdini):

    ORDINI = [{"id_order": "A1"}, {"id_order": "B2"}, {"id_order": "C3"}]

    def _gira(self):
        prova = self
        connettore = self.canale._get_connector()

        def finto_import(self_conn, ordine):
            esterno = ordine.get("id_order")
            if esterno == "B2":
                prova._spacca()          # ⚠️ il guasto sta IN MEZZO
                return True
            prova._crea_mappa(esterno)
            return True

        with patch.object(type(connettore.transport), "request",
                          lambda *a, **k: _pagina(self.ORDINI)), \
             patch.object(type(connettore), "import_order", finto_import):
            return connettore.pull_orders()

    def test_01_lo_scarico_arriva_in_fondo(self):
        self._gira()

    def test_02_l_ordine_DOPO_quello_guasto_viene_importato(self):
        """Prima, l'errore usciva dal ciclo: gli ordini dopo non esistevano."""
        self._gira()
        self.assertTrue(
            self._esiste_nel_database("C3"),
            "L'ordine dopo quello guasto dev'essere stato importato.")

    def test_03_l_ordine_PRIMA_non_viene_annullato(self):
        """⚠️ Prima il commit della richiesta diventava un ROLLBACK silenzioso
        che si portava via gli ordini gia' importati — mentre i contatori
        dicevano «importati N»."""
        self._gira()
        self.assertTrue(
            self._esiste_nel_database("A1"),
            "L'ordine importato prima del guasto dev'essere ancora nel "
            "database.")

    def test_04_la_transazione_resta_utilizzabile(self):
        self._gira()
        self._crea_mappa("DOPO-IL-GUASTO")

    def test_05_l_ordine_guasto_lascia_una_traccia(self):
        """Un ordine perso in silenzio e' peggio di un ordine rifiutato."""
        self._gira()
        riga = self._mappa("B2")
        registro = self.env["centrivo.job.log"].sudo().search([
            ("channel_id", "=", self.canale.id),
            ("external_id", "=", "B2"),
        ])
        self.assertTrue(
            riga or registro,
            "L'ordine guasto deve lasciare traccia su order.map o nel "
            "registro: senza, sparisce e nessuno lo cerca piu'.")


# ===========================================================================
# 2. LA RIPRESA DEGLI ACQUISITI — «ieri» non deve fermare «oggi»
# ===========================================================================
@tagged("post_install", "-at_install", "centrivo_bricobravo")
class TestRipresaAcquisiti(BaseOrdini):

    def _gira(self):
        prova = self
        connettore = self.canale._get_connector()

        def finto_acquired(self_conn, esterno, order_map=None):
            if esterno == "B2":
                prova._spacca()
                return True
            if order_map:
                order_map.sudo().write({"acquired_done": True})
                order_map.env.flush_all()
            return True

        with patch.object(type(connettore), "mark_acquired", finto_acquired):
            return connettore.retry_pending_acquired()

    def _acquisito_nel_database(self, esterno):
        self.env.cr.execute(
            "SELECT acquired_done FROM centrivo_order_map "
            "WHERE channel_id = %s AND external_id = %s",
            (self.canale.id, esterno))
        riga = self.env.cr.fetchone()
        return bool(riga and riga[0])

    def setUp(self):
        super().setUp()
        for esterno in ("A1", "B2", "C3"):
            self._crea_mappa(esterno)

    def test_01_la_ripresa_arriva_in_fondo(self):
        """⚠️ Prima non c'era nemmeno un gestore: un ordine rimasto indietro
        IERI fermava lo scarico di OGGI, perche' la ripresa e' la prima cosa
        che il pull chiama."""
        self._gira()

    def test_02_l_ordine_DOPO_quello_guasto_viene_recuperato(self):
        self._gira()
        self.assertTrue(
            self._acquisito_nel_database("C3"),
            "L'ordine dopo quello guasto dev'essere stato acquisito.")

    def test_03_l_ordine_PRIMA_resta_acquisito(self):
        self._gira()
        self.assertTrue(
            self._acquisito_nel_database("A1"),
            "L'acquisizione riuscita prima del guasto dev'essere ancora nel "
            "database.")

    def test_04_il_conteggio_non_conta_il_guasto(self):
        """⚠️ Il contatore sta FUORI dal savepoint apposta: non si conta cio'
        che il rollback si e' portato via."""
        self.assertEqual(
            self._gira(), 2,
            "Recuperati devono essere 2, non 3: l'ordine guasto non si conta.")

    def test_05_la_transazione_resta_utilizzabile(self):
        self._gira()
        self._crea_mappa("DOPO-LA-RIPRESA")
