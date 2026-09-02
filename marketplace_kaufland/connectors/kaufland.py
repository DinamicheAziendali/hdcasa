# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il connettore Kaufland: la parte che tocca Odoo.

La logica pura sta nei file accanto (firma, offerte, risposta, catalogo) e si
prova senza Odoo con i test di tools/.
"""
import logging
import time
from collections import OrderedDict
from urllib.parse import quote

from odoo import _, fields
from odoo.exceptions import UserError

from odoo.addons.integrations_core.connectors.base import (
    MarketplaceConnector,
    register_connector,
)
from odoo.addons.integrations_core.connectors.carrier_resolver import NON_TRADOTTO

from .kaufland_catalogo import stato_scheda
from .kaufland_client import (
    ATTESA,
    KAUFLAND_URL,
    KauflandClient,
    LetturaInterrotta,
    TrasportoRequests,
)
from .kaufland_offerte import (
    GRUPPO,
    _pezzi as pezzi_valide,
    corpi_aggiornamento,
    corpo_offerta,
    in_centesimi,
)
from .kaufland_risposta import id_unit as leggi_id_unit
from .kaufland_risposta import messaggio as leggi_messaggio

_logger = logging.getLogger(__name__)

API_UNITA = "/units/"
# ⚠️ L'aggiornamento in blocco: 150 unita' per chiamata (vedi `GRUPPO`), e
# Kaufland rifiuta il GRUPPO INTERO se una sola riga non gli piace.
API_UNITA_BLOCCO = "/units/bulk"
API_PRODOTTI_EAN = "/products/ean/"

# ⚠️ Gli ORDINI: sono DUE indirizzi e serve il secondo. `/orders/` porta solo
# la testata (nessun cliente, nessuna riga); `/order-units/` porta tutto.
# Misurato sul vero il 2026-08-31 sui primi due ordini tedeschi.
API_ORDINI = "/orders/"
API_ORDER_UNITS = "/order-units/"
# ⚠️ La comunicazione della spedizione e' PER RIGA: l'indirizzo porta
# `id_order_unit`, non `id_order`. Un ordine da tre articoli sono TRE chiamate.
# Letto sulla guida, non misurato: `docs/kaufland-spedizione-guida.md`.
API_RIGA_SPEDITA = "/order-units/%s/send"

# ⚠️ Lo STATO da chiedere, e non e' un dettaglio di efficienza.
# Una riga nasce in stato `open` e per 15 minuti Kaufland NASCONDE gli
# indirizzi, apposta, per impedire di spedire troppo presto. Senza questo
# filtro un ordine pescato nei suoi primi minuti entrerebbe in Odoo senza via,
# senza citta' e senza CAP — e sovrascriverebbe con dei vuoti l'indirizzo buono
# di un cliente gia' in anagrafica. A mano non capita quasi mai; con il cron
# ogni 15 minuti capita. Il filtro e' quello che consiglia la loro guida.
STATO_DA_SPEDIRE = "need_to_be_sent"

# I corrieri ammessi da Kaufland: il campo `carrier_code` vuole il NOME PER
# ESTESO, non una sigla. Lista letta sulla guida il 2026-08-31.
# ⚠️ Due nomi sono scritti in modo strano e NON sono refusi nostri:
# «Post Italiane» (senza la «e») e «Fedex» (con la «e» minuscola). Sono
# esattamente i punti su cui una lettura puo' sbagliare — per questo esiste
# l'eccezione di canale, che scavalca questa tabella senza un rilascio.
KAUFLAND_CARRIERS = [(nome, nome) for nome in (
    "Other", "Other Hauler", "AIT Home Delivery", "Allekurier",
    "Amazon Logistics DE (Swiship)", "Amazon Shipping (IT)", "Asendia",
    "Asendia Germany", "Austrian Post", "Ambro Express", "Bejot Logistics",
    "BRT Bartolini", "Bursped", "Cainiao", "Cargoline", "Cargo International",
    "China Post", "Chronopost", "Chukou1 Logistics", "Colissimo", "Colis Prive",
    "CNE Express", "Correos", "Cubyn", "Czech Post", "Dachser", "Deutsche Post",
    "DHL", "DHL 2 MH", "DHL Express", "DHL Ecommerce", "DHL Freight",
    "DHL Hong Kong", "DHL Poland Domestic", "DPD", "DPD France", "DPD Hungary",
    "DPD Netherlands", "DPD Romania", "DPD Czech Republic", "DPD Slovakia",
    "DPD Austria", "DPD UK", "DPD Poland", "dtl", "DSV", "ECE", "Emons", "Evri",
    "Fedex", "FedEx Poland Domestic", "Flyt Express", "4PX",
    "Gebruder Weiss Germany", "Gebruder Weiss", "Geis", "Geis Poland", "GEL",
    "Geodis", "GLS", "GLS Czech Republic", "GLS Italy", "GLS Poland",
    "Go Express and Logistics", "GOFO", "Hellmann", "Hermes", "Hermes 2 MH",
    "Hong Kong Post", "Hua Han Logistics", "IDS Logistik", "Iloxx",
    "Iloxx Spedition", "InPost", "Jersey Post", "Kuehne & Nagel", "La Poste",
    "Liccardi", "Maersk", "Mondial Relay", "Nexive", "Nova Post",
    "Orlen Paczka", "Overseas Territory FR EMS", "Packeta", "Post Italiane",
    "Post Haste", "PostNL", "PostNL 3S", "Pressio", "PPL", "Poland Post",
    "Raben Group", "Redur Spain", "Rhenus", "Royal Mail", "Royal Shipments",
    "Sailpost", "Schenker", "Seur", "SFC Service", "Slovakia Post",
    "Slovak Parcel Service", "Spring GDS", "SGT Corriere Espresso",
    "SPT Furniture Logistic", "SDA", "Spedition Guettler", "Siodemka", "Suus",
    "Sunyou", "TNT", "TNT Click", "TNT France", "TNT Italy", "Trans FM",
    "trans-o-flex", "TopTrans", "UBI Smart Parcel", "UPS", "Wanb Express",
    "WeDo Logistics", "Winit", "WnDirect", "XL Courier", "Yanwen", "YDH",
    "Yun Express", "Zufall")]
# Quale secchio della ricognizione conta quale stato della scheda. Le quattro
# voci sono TUTTE quelle che `kaufland_catalogo` puo' produrre (la quarta e' il
# suo `None`): un quinto stato deve rompere qui e non finire in un secchio
# sbagliato in silenzio.
SECCHI = {"pronta": "pronte", "guscio": "gusci", "assente": "assenti",
          "sconosciuto": "sconosciuti"}
# Quanto payload si conserva nel registro: un corpo enorme rende la
# schermata delle operazioni illeggibile.
MAX_PAYLOAD = 2000

# ⚠️ QUANTE OFFERTE SI CREANO IN UN GIRO SOLO, e perche' c'e' un tetto.
# Ogni offerta e' una chiamata di rete a se': non esiste una creazione in
# blocco. `config/odoo.conf` dichiara `limit_time_real = 120`, cioe' una
# richiesta web che dura piu' di due minuti viene UCCISA dal worker — e in
# Odoo un worker ucciso annulla l'INTERA transazione. Le offerte gia' create
# su Kaufland resterebbero vive mentre in Odoo sparirebbe ogni traccia del
# loro identificativo: e' esattamente il modo in cui sono nate le 165 orfane
# da cui parte questa consegna. Il tetto non e' prudenza, e' l'unica cosa che
# tiene il giro dentro il tempo che gli e' concesso. Quel che avanza si fa al
# giro dopo, e il messaggio finale dice quanto avanza.
#
# ⚠️ Il tetto conta le CHIAMATE FATTE, non le righe esaminate. Contare le
# righe produrrebbe un livelock: «saltato» resta candidato per scelta, quindi
# 50 righe senza prezzo ordinate prima delle buone brucerebbero i 50 posti a
# ogni giro, per sempre, senza mai chiamare Kaufland — mentre il messaggio
# invita a ripetere «finche' non ne resta nessuna».
MAX_PER_GIRO = 50
# Il `limit_time_real` del worker, da `config/odoo.conf`.
LIMITE_WORKER = 120
# Quel che si lascia alla chiusura del giro (i conti, il registro, il
# cancello) dopo l'ultima chiamata.
MARGINE_CHIUSURA = 15
# ⚠️ Il tetto vero e' il TEMPO, non il numero, e si CALCOLA: il controllo
# avviene PRIMA di una chiamata, e quella chiamata puo' durare fino ad
# `ATTESA` secondi. Un valore scritto a mano si disallineerebbe in silenzio
# il giorno in cui `ATTESA` cambia, e il peggior caso sfonderebbe
# `limit_time_real` producendo proprio le orfane che il tetto esiste per
# evitare.
SECONDI_PER_GIRO = LIMITE_WORKER - ATTESA - MARGINE_CHIUSURA
# ⚠️ IL TETTO DELLA RICOGNIZIONE, e perche' era il giro che ne aveva piu'
# bisogno. La ricognizione fa UNA CHIAMATA PER PRODOTTO — 549 misurate sul
# vero il 2026-08-22 — dentro una sola richiesta web, e non scrive niente
# fino alla fine. Con `limit_time_real = 120` bastano 0,22 secondi a chiamata
# perche' il worker la uccida: torna indietro tutto, nessuna riga letta, e
# nemmeno la riga di registro. Senza tetto, sul catalogo vero la ricognizione
# rischiava di non riuscire MAI, e di peggiorare man mano che il catalogo
# cresce.
#
# ⚠️ Il tetto vero e' `SECONDI_PER_GIRO`, lo stesso della creazione e
# dell'allineamento: e' il tempo che uccide, non il numero. Il numero e' la
# cintura per il caso opposto — un collegamento cosi' veloce che 75 secondi
# non bastano a fermarlo — e tiene comunque limitato quanto una sola
# transazione ha in pancia. A 549 prodotti servono due giri: e' il prezzo
# onesto di non farsi uccidere a meta'.
MAX_GUARDATI_PER_GIRO = 400
# ⚠️ Quanti rifiuti IDENTICI di fila bastano a dire che non e' il prodotto,
# e' la configurazione. Senza questa frenata un rifiuto sistemico marcherebbe
# in errore 50 righe per giro — e una riga in errore esce dalle candidate e
# nessuno strumento la rimette dentro.
RIFIUTI_UGUALI_DI_FILA = 5
# ⚠️ QUANTO VALE IL CANCELLO DEL RIAGGANCIO, e perche' SCADE.
# `kaufland_riagganciato` non dichiara un fatto su Odoo: dichiara un fatto sul
# MONDO FUORI — «so quali offerte esistono su Kaufland» — e quel mondo cambia
# senza chiedere permesso. `centrivo-hub` e' ancora acceso su Kaufland e puo'
# creare offerte, e una persona puo' aprirne o chiuderne dal portale. Finche'
# la data non si confrontava con niente, un riaggancio di tre mesi fa
# autorizzava una creazione di oggi.
#
# ⚠️ Ventiquattro ore, e la finestra e' scelta, non presa a caso. Non meno:
# una campagna vera si fa a giri da `MAX_PER_GIRO` offerte con in mezzo la
# ricognizione, e cominciare la mattina per finire la sera deve restare
# possibile senza rifare il riaggancio a meta'. Non di piu': rifarlo costa
# due chiamate in SOLA LETTURA, quindi un cancello scaduto e' una seccatura
# da un minuto, mentre uno che non scade mai e' un'autorizzazione firmata in
# bianco su un marketplace vero.
VALIDITA_CANCELLO_ORE = 24
# ⚠️ Il codice PostgreSQL di «lock gia' preso, e avevo detto NOWAIT» (55P03).
# Qualunque ALTRO codice — 40001 serializzazione, 40P01 deadlock — NON e' «un
# altro giro in corso»: tradurlo cosi' direbbe una cosa falsa e sopprimerebbe
# il ritentativo che Odoo fa da solo.
LOCK_OCCUPATO = "55P03"
# ⚠️ Il piu' grande intero che un campo `Integer` di Odoo regge: e' un `int4`
# di Postgres. Oltre, l'errore arriva dal DATABASE e si porta via l'intera
# transazione.
MASSIMO_INT4 = 2 ** 31 - 1

# ⚠️ IL TETTO DELL'ALLINEAMENTO, e perche' e' TARATO SUI GRUPPI.
# Qui non c'e' una chiamata per offerta: ce n'e' una ogni 150 (`GRUPPO`), e su
# 166 offerte sono due chiamate in tutto. Il rischio di farsi uccidere dal
# `limit_time_real` e' quindi molto piu' basso che nella creazione — ma non e'
# zero: a migliaia di offerte i gruppi diventano decine, e ogni chiamata puo'
# durare fino ad `ATTESA` secondi. Venti gruppi sono 3.000 offerte per giro,
# che e' abbondante per qualunque catalogo di casa e resta dentro il tempo
# anche con chiamate lente.
#
# ⚠️ E il tetto NON produce il livelock che nella creazione ha costretto a
# contare le chiamate invece delle righe: qui si mandano solo le offerte
# CAMBIATE, e un cambio non mandato resta un cambio al giro dopo. Il giro
# successivo riparte esattamente da li', perche' le prime 3.000 nel frattempo
# sono diventate invariate.
MAX_GRUPPI_PER_GIRO = 20
MAX_CAMBI_PER_GIRO = MAX_GRUPPI_PER_GIRO * GRUPPO
# ⚠️ Quanti GRUPPI interi rifiutati di fila con lo STESSO motivo bastano a
# dire che non e' il dato di una riga, e' la configurazione. Due, non cinque
# come nella creazione: la' un rifiuto costava un'offerta, qui ne costa 150 —
# due gruppi sono gia' 300 offerte rifiutate per la stessa ragione.
GRUPPI_RIFIUTATI_DI_FILA = 2


@register_connector("kaufland", "Kaufland")
class KauflandConnector(MarketplaceConnector):

    # ⚠️ Cosa Kaufland NON usa della scheda del canale. Le sue credenziali
    # stanno nel suo tab, non nella «API Key» generica di BricoBravo; i feed
    # CSV non li fa (parla via API); la mappatura del catalogo non la legge.
    #
    # ⚠️ E soprattutto NON HA UN AMBIENTE DI PROVA. Il campo diceva
    # «Sandbox (test)» su un canale il cui indirizzo era
    # `sellerapi.kaufland.com` — cioe' il Kaufland VERO. Una schermata che
    # dice «sei in prova» mentre scrivi su un marketplace vero e' peggio di
    # una schermata muta.
    usa_api_key = False
    usa_ambienti = False
    usa_feed_csv = False
    usa_immagini_feed = False
    usa_mappa_catalogo = False
    usa_presa_in_carico = False   # non esiste, su questo marketplace

    default_base_url = KAUFLAND_URL

    # I corrieri ammessi da Kaufland (vedi KAUFLAND_CARRIERS). Serve al
    # Selection dell'ECCEZIONE di canale: senza questa lista l'eccezione non
    # potrebbe essere compilata per Kaufland, e la via di fuga non esisterebbe.
    carrier_codes = KAUFLAND_CARRIERS

    # Traduzione corriere dell'anagrafica -> nome atteso da Kaufland.
    #
    # ⚠️ `dpd` resta VOLUTAMENTE non tradotto, come su Temu: in Italia quel
    # servizio e' BRT, e la lista di Kaufland ha un «DPD» generico piu' otto
    # varianti nazionali. Sceglierne una a caso manderebbe i pacchi sotto il
    # corriere sbagliato. La Copertura corrieri lo mostrera' come «Manca», e
    # sara' una persona a decidere: e' il comportamento giusto.
    carrier_brand_codes = {
        "brt": "BRT Bartolini",
        "gls": "GLS Italy",
        "poste": "Post Italiane",   # ⚠️ senza la «e»: e' come lo scrivono loro
        "sda": "SDA",
        "dhl": "DHL",
        "ups": "UPS",
        "tnt": "TNT Italy",
        "fedex": "Fedex",           # ⚠️ «e» minuscola: idem
    }

    # ------------------------------------------------------------------
    # Attrezzi
    # ------------------------------------------------------------------
    def _client(self):
        """Il client firmato per QUESTO canale.

        ⚠️ Le credenziali si leggono in sudo: i campi hanno
        `groups="base.group_system"`, quindi un utente normale che schiaccia
        il bottone le leggerebbe vuote — e l'errore sembrerebbe «credenziali
        sbagliate» invece che «non hai il permesso di vederle».
        """
        canale = self.channel.sudo()
        chiave = (canale.kaufland_client_key or "").strip()
        segreto = (canale.kaufland_secret_key or "").strip()
        if not chiave or not segreto:
            raise UserError(_(
                "Sul canale «%s» mancano la chiave o il segreto Kaufland.")
                % self.channel.display_name)
        return KauflandClient(chiave, segreto, TrasportoRequests(),
                              base_url=canale.base_url or KAUFLAND_URL)

    # ------------------------------------------------------------------
    # I MERCATI
    # ⚠️ Un canale serve piu' mercati (decisione di Angelo, 2026-08-29): le
    # chiavi API sono dell'account venditore, non del mercato, e il mercato
    # viaggia come `?storefront=` su ogni chiamata. Ogni operazione gira UNA
    # VOLTA PER MERCATO, e il mercato corrente vive in `self._mercato_riga`.
    # ------------------------------------------------------------------
    _mercato_riga = None

    def _mercati(self):
        """Le righe mercato attive del canale."""
        mercati = self.channel.sudo().kaufland_market_ids.filtered("active")
        if not mercati:
            raise UserError(_(
                "Sul canale «%s» non c'è nessun mercato Kaufland attivo. "
                "Aggiungine almeno uno nella scheda Kaufland: le credenziali "
                "si scrivono una volta sola, i mercati sono righe.")
                % self.channel.display_name)
        return mercati

    def _riga_mercato(self):
        """La riga del mercato su cui si sta lavorando adesso.

        ⚠️ Se qui manca, e' un errore di programmazione, non di
        configurazione: vuol dire che un'operazione e' stata chiamata senza
        passare da `per_mercato`, e lavorerebbe su un mercato indefinito —
        cioe' scriverebbe su Kaufland senza sapere dove.
        """
        if not self._mercato_riga:
            raise UserError(_(
                "Operazione Kaufland avviata senza un mercato. È un difetto "
                "del modulo, non della configurazione: va segnalato."))
        return self._mercato_riga

    def per_mercato(self, nome_operazione, *args, **kwargs):
        """Esegue l'operazione una volta per ogni mercato attivo del canale.

        Restituisce {codice mercato: esito}. ⚠️ Un mercato che solleva NON
        ferma gli altri: e' la stessa disciplina dell'isolamento fra canali
        (2026-08-27). Il guasto di un mercato non deve spegnere gli altri, e
        l'errore si legge nell'esito di quel mercato.
        """
        esiti = {}
        for riga in self._mercati():
            self._mercato_riga = riga
            try:
                with self.env.cr.savepoint():
                    esiti[riga.storefront] = getattr(
                        self, nome_operazione)(*args, **kwargs)
            except Exception as errore:  # noqa: BLE001
                esiti[riga.storefront] = {"errore": str(errore)}
                _logger.warning("Kaufland %s su %s: %s", nome_operazione,
                                riga.storefront, errore)
            finally:
                self._mercato_riga = None
        return esiti

    def _mercato(self):
        """Il codice del mercato corrente, come lo vuole Kaufland."""
        return self._riga_mercato().storefront

    def _listino_del_mercato(self):
        """Il listino dei prezzi per il mercato corrente.

        ⚠️ **Il listino del MERCATO vince**, e quello del canale e' il ripiego.
        Il prezzo e' per Paese come l'IVA: un box doccia non costa lo stesso in
        Germania e in Italia. Un listino solo sul canale sbaglia gia' al
        secondo mercato, e sbaglia in SILENZIO — le offerte partono, nessun
        errore compare, e il prezzo e' quello di un altro Paese.

        Il ripiego sul canale esiste perche' chi ha un mercato solo non deve
        compilare niente di nuovo per il fatto che abbiamo aggiunto un campo.
        """
        mercato = self._riga_mercato()
        listino = mercato.sudo().pricelist_id or self.channel.sudo().pricelist_selling_id
        if not listino:
            # ⚠️ Il messaggio nomina IL MERCATO, non solo il canale: ora che il
            # listino puo' stare in due posti, «manca sul canale» manderebbe a
            # cercare nel posto sbagliato.
            raise UserError(_(
                "Manca il listino dei prezzi per il mercato «%(mercato)s» del "
                "canale «%(canale)s»: senza, nessun prodotto avrebbe un prezzo "
                "e non si manderebbe nessuna offerta. Compila il listino sulla "
                "riga del mercato, oppure il «Listino prezzo pieno» del canale "
                "se vale per tutti i mercati.")
                % {"mercato": mercato.storefront or "?",
                   "canale": self.channel.display_name})
        return listino

    def _giorni_lavorazione(self, prodotto):
        """I giorni che si dichiarano a Kaufland per QUESTO prodotto.

        ⚠️ Vince il tempo di risposta al cliente del prodotto; il campo del
        canale e' il ripiego per chi non ce l'ha. Deciso da Angelo il
        2026-08-29, e allinea Kaufland a ManoMano, che fa gia' cosi'.

        Prima si mandava sempre il valore del canale, e il tempo del prodotto
        non veniva letto **nemmeno quando c'era**: lo stesso articolo poteva
        essere promesso in 3 giorni su Kaufland e in 10 su ManoMano. Su un
        articolo che dal fornitore arriva in due settimane, quei 3 giorni
        diventano un ritardo di consegna — e le metriche di puntualita' su
        Kaufland sono per account, non per offerta.
        """
        giorni = int(getattr(prodotto, "sale_delay", 0) or 0)
        if giorni > 0:
            return giorni
        return int(self.channel.sudo().kaufland_handling_time or 0)

    @staticmethod
    def _testo_id(valore):
        """L'identificativo di un'offerta come testo, e senza decimali.

        ⚠️ `str(1234.0)` da' "1234.0", che non tornera' mai piu': non
        corrisponde a niente su Kaufland e non si ritrova nemmeno
        nell'`external_code` gia' in mappa. Il JSON non garantisce il tipo,
        quindi lo si normalizza qui, in un punto solo.
        """
        if valore is None or isinstance(valore, bool):
            return ""
        if isinstance(valore, float):
            return str(int(valore)) if valore.is_integer() else str(valore)
        if isinstance(valore, int):
            return str(valore)
        return str(valore).strip()

    @staticmethod
    def _eta_cancello(mercato):
        """Da quante ORE è aperta la guardia del riaggancio, o None.

        ⚠️ Prende la RIGA DEL MERCATO, non il canale: dal 2026-08-29 il
        cancello e' di ogni mercato, e chiedere l'eta' al canale vorrebbe dire
        misurare quella di un cancello che non esiste piu'.

        None significa «non si sa dire», e si tratta come scaduta: un
        cancello aperto senza data e' un cancello di cui nessuno puo' dire
        quanto vale, ed e' esattamente lo stato che questa guardia esiste per
        rifiutare.

        ⚠️ `to_datetime` e non un confronto diretto: un campo Datetime letto
        dall'ORM e' un `datetime`, ma lo stesso valore appena scritto puo'
        essere ancora la stringa che ci e' stata passata, e sottrarre una
        stringa da un `datetime` esplode dentro una guardia di sicurezza —
        cioe' nel punto peggiore.
        """
        quando = mercato.riagganciato_il
        if not quando:
            return None
        quando = fields.Datetime.to_datetime(quando)
        adesso = fields.Datetime.to_datetime(fields.Datetime.now())
        return (adesso - quando).total_seconds() / 3600.0

    @staticmethod
    def _intero(valore):
        """Un intero SCRIVIBILE, oppure None.

        ⚠️ Serve per i valori che Kaufland restituisce: il JSON non
        garantisce il tipo, e un prezzo arrivato come `"1990"` deve valere
        1990, mentre un valore illeggibile deve semplicemente NON essere
        scritto — non diventare uno zero che poi si spaccia per una base
        vera.

        ⚠️ Un numero A VIRGOLA non e' leggibile: `int(19.9)` darebbe 19,
        cioe' TRONCHEREBBE IN SILENZIO proprio nel campo che deve dire
        «quanto sa Kaufland». Un float senza decimali (`1990.0`, forma
        normalissima nel JSON) invece e' lo stesso numero, e si accetta.

        ⚠️ E un numero FUORI DALL'INTERVALLO non si scrive: i due campi sono
        `Integer`, cioe' `int4` in Postgres, e un valore assurdo farebbe
        arrivare l'errore DAL DATABASE — che in Odoo annulla l'INTERA
        transazione, qui quella del riaggancio, con dentro centinaia di
        righe gia' agganciate. E' la stessa lezione gia' pagata con
        `unique(channel_id, id_unit)`: i vincoli del database non si toccano
        a mani nude.
        """
        if valore is None or isinstance(valore, bool):
            return None
        if isinstance(valore, float):
            if not valore.is_integer():
                return None
            valore = int(valore)
        try:
            numero = int(valore)
        except (TypeError, ValueError):
            return None
        if not -MASSIMO_INT4 - 1 <= numero <= MASSIMO_INT4:
            return None
        return numero

    @classmethod
    def _base_da_kaufland(cls, unita):
        """Prezzo e quantita' che Kaufland dichiara per un'offerta viva.

        Si scrive solo cio' che c'e' davvero: se Kaufland non li restituisce,
        la base non si inventa (resterebbe zero, ma almeno non si dichiara
        vera una cosa falsa — al primo allineamento ci pensa `allineato_il`,
        che vuoto forza l'invio di tutti e due i valori).
        """
        valori = {}
        prezzo = cls._intero(unita.get("listing_price"))
        if prezzo is not None:
            valori["ultimo_prezzo"] = prezzo
        quantita = cls._intero(unita.get("amount"))
        if quantita is not None:
            valori["ultima_quantita"] = quantita
        return valori

    def _registra(self, operazione, esito, messaggio, payload=None,
                  external_id=None):
        """Una riga nel registro delle operazioni.

        ⚠️ Il messaggio arriva già con il dettaglio dentro (vedi
        kaufland_risposta.messaggio): non accorciarlo qui, è l'unica cosa
        che dice QUALE campo Kaufland ha rifiutato e perché.
        """
        return self.env["centrivo.job.log"].sudo().create({
            "channel_id": self.channel.id,
            "operation": operazione,
            "external_id": external_id,
            "result": esito,
            "message": messaggio,
            "payload": (payload or "")[:MAX_PAYLOAD] or False,
            "company_id": self.channel.company_id.id,
        })

    # ------------------------------------------------------------------
    # Gli ordini: non in questa consegna
    # ------------------------------------------------------------------
    def pull_orders(self):
        """Scarica gli ordini del mercato corrente e li importa in Odoo.

        ⚠️ Due chiamate esistono, e serve la SECONDA: `/orders/` porta solo la
        testata (nessun cliente, nessuna riga), `/order-units/` porta tutto.
        Misurato sul vero il 2026-08-31.

        ⚠️ E Kaufland ragiona per RIGA: le righe si raggruppano per `id_order`,
        e un ordine da tre articoli diventa UN `sale.order` con tre righe.
        """
        mercato = self._riga_mercato()
        client = self._client()
        esito = {"lette": 0, "ordini": 0, "importati": 0, "gia_importati": 0,
                 "in_errore": 0, "interrotto": 0}

        # ⚠️ `pagine()` solleva `LetturaInterrotta` se l'elenco si ferma a
        # meta': un elenco parziale dato per completo farebbe risultare
        # «nessun ordine nuovo» mentre ne mancano — e nessuno lo cercherebbe.
        per_ordine = OrderedDict()
        try:
            # ⚠️ `status=need_to_be_sent` NON e' un'ottimizzazione: senza,
            # entrerebbero anche le righe in stato `open`, che per i primi 15
            # minuti arrivano SENZA INDIRIZZO (Kaufland lo nasconde apposta).
            # Vedi STATO_DA_SPEDIRE.
            for pagina in client.pagine("%s?storefront=%s&status=%s"
                                        % (API_ORDER_UNITS, mercato.storefront,
                                           STATO_DA_SPEDIRE)):
                for riga in pagina:
                    esito["lette"] += 1
                    id_order = str(riga.get("id_order") or "").strip()
                    if not id_order:
                        continue
                    per_ordine.setdefault(id_order, []).append(riga)
        except LetturaInterrotta as errore:
            esito["interrotto"] = 1
            self._registra("pull_orders", "error", str(errore))
            return esito

        esito["ordini"] = len(per_ordine)
        for id_order, righe in per_ordine.items():
            # ⚠️ IL SAVEPOINT PER ORDINE: un ordine che rompe il database non
            # deve annullare quelli gia' importati ne' fermare quelli dopo.
            # Provato dentro Odoo su BricoBravo il 2026-08-30.
            try:
                with self.env.cr.savepoint():
                    self._importa_ordine(mercato, id_order, righe, esito)
            except Exception as errore:  # noqa: BLE001
                esito["in_errore"] += 1
                _logger.exception("Kaufland: ordine %s non importato", id_order)
                self._al_riparo(self._segna_ordine_in_errore, mercato,
                                id_order, str(errore)[:2000])
        return esito

    def _importa_ordine(self, mercato, id_order, righe, esito):
        """Un ordine Kaufland diventa un `sale.order`. Idempotente."""
        Mappa = self.env["centrivo.order.map"].sudo()
        mappa = Mappa.search([("channel_id", "=", self.channel.id),
                              ("external_id", "=", id_order)], limit=1)
        if mappa and mappa.state == "imported":
            # ⚠️ L'idempotenza: lo scarico si ripete, l'ordine no.
            esito["gia_importati"] += 1
            return

        # --- I prodotti: `id_offer` E' il nostro codice articolo -----------
        Prodotto = self.env["product.product"].sudo()
        mancanti, prodotti = [], {}
        for riga in righe:
            codice = str(riga.get("id_offer") or "").strip()
            prodotto = Prodotto.search(
                [("default_code", "=", codice),
                 ("company_id", "in", [False, self.channel.company_id.id])],
                limit=1)
            if not prodotto:
                mancanti.append(codice)
            else:
                prodotti[riga.get("id_order_unit")] = prodotto

        if mancanti:
            # ⚠️ Decisione di Angelo (2026-08-31): il prodotto NON si crea mai
            # da un ordine. L'ordine va in errore, e dev'essere VISIBILE — un
            # ordine perso in silenzio e' peggio di uno rifiutato.
            #
            # ⚠️ Da NON confondere col prodotto senza giacenza: quello entra
            # lo stesso, e la mancanza si guarda in magazzino. Qui manca il
            # DATO, la' manca la MERCE.
            self._segna_ordine_in_errore(
                mercato, id_order,
                _("Nessun prodotto in Odoo con codice %s: l'ordine non e' "
                  "stato importato. Il prodotto non si crea da un ordine — "
                  "va creato o corretto il codice, poi si ripete lo scarico.")
                % ", ".join(sorted(set(mancanti))))
            esito["in_errore"] += 1
            return

        cliente = self._cliente_da_riga(righe[0])
        ordine = self.env["sale.order"].sudo().create({
            "partner_id": cliente.id,
            "company_id": self.channel.company_id.id,
            "team_id": self.channel.team_id.id or False,
            # ⚠️ Dal MERCATO, non dal canale: l'IVA e' per Paese.
            "fiscal_position_id": mercato.fiscal_position_id.id or False,
            "client_order_ref": id_order,
            "order_line": [(0, 0, {
                "product_id": prodotti[riga.get("id_order_unit")].id,
                "product_uom_qty": 1,
                "price_unit": self._prezzo_riga(riga),
            }) for riga in righe],
        })
        # Decisione di Angelo: nasce gia' confermato.
        ordine.action_confirm()
        self._controlla_totale(ordine, id_order, righe)

        valori = {"state": "imported", "sale_order_id": ordine.id,
                  "error_message": False}
        if mappa:
            mappa.write(valori)
        else:
            mappa = Mappa.create(dict(
                valori, channel_id=self.channel.id, external_id=id_order,
                company_id=self.channel.company_id.id))

        self._registra_righe(mercato, mappa, ordine, righe, prodotti)
        esito["importati"] += 1

    def _registra_righe(self, mercato, mappa, ordine, righe, prodotti):
        """Una riga `centrivo.kaufland.order.unit` per ogni riga Kaufland."""
        Unita = self.env["centrivo.kaufland.order.unit"].sudo()
        per_prodotto = {r.product_id.id: r for r in ordine.order_line}
        for riga in righe:
            id_unit = str(riga.get("id_order_unit") or "").strip()
            if not id_unit:
                continue
            prodotto = prodotti.get(riga.get("id_order_unit"))
            Unita.create({
                "market_id": mercato.id,
                "order_map_id": mappa.id,
                "id_order_unit": id_unit,
                "id_order": str(riga.get("id_order") or ""),
                "id_offer": str(riga.get("id_offer") or ""),
                "sale_line_id": (per_prodotto.get(prodotto.id).id
                                 if prodotto and per_prodotto.get(prodotto.id)
                                 else False),
                "stato_kaufland": riga.get("status") or "",
                "prezzo": (riga.get("price") or 0) / 100.0,
                "ricavo_netto": (riga.get("revenue_net") or 0) / 100.0,
                "scade_il": self._data_iso(
                    riga.get("delivery_time_expires_iso")),
            })

    @staticmethod
    def _prezzo_riga(riga):
        """Il prezzo da mettere in `price_unit`: il LORDO, IVA inclusa.

        ⚠️ **In HD casa si lavora a prezzi IVA inclusa**: le aliquote sono
        configurate come «IVA inclusa» (`price_include`), e per la Germania e'
        «19% IVA inclusa». Con un'imposta cosi', `price_unit` porta il prezzo
        pagato dal cliente ed e' **Odoo a scorporare** l'IVA. Precisato da
        Angelo il 2026-08-31.

        ⚠️ **E qui mi ero sbagliato prima.** Sullo stage l'ordine da 230,00 €
        nasceva da 264,50, e ne avevo dedotto che il prezzo andasse scorporato
        a mano. La misura era giusta, la conclusione no: quel 264,50 veniva da
        un'imposta di prova al 15% **che non e' «IVA inclusa»** e quindi si
        somma. Il difetto non era il prezzo: era l'imposta.

        Quindi qui si scrive il lordo, tale e quale, e **se l'imposta e'
        configurata male il totale non torna e la guardia lo dice** — che e' il
        verso giusto: si aggiusta l'imposta, non si piega il prezzo.

        `price` arriva in CENTESIMI: 23000 sono 230,00 €.
        """
        return (riga.get("price") or 0) / 100.0

    def _controlla_totale(self, ordine, id_order, righe):
        """Il totale dell'ordine deve tornare con quello che dice Kaufland.

        ⚠️ Serve perche' l'imponibile da solo non basta: se la posizione
        fiscale del mercato non applica l'aliquota che Kaufland ha usato, il
        totale non torna lo stesso — e non lo dice nessuno. Un ordine che in
        Odoo vale meno (o piu') di quello che il cliente ha pagato e' un errore
        che si scopre in contabilita', mesi dopo.

        L'ordine resta IMPORTATO: esiste, il cliente ha comprato. Ma la
        differenza finisce nel registro delle operazioni, dove si guarda.
        """
        atteso = sum((r.get("price") or 0) for r in righe) / 100.0
        trovato = ordine.amount_total
        if abs(atteso - trovato) <= 0.01:
            return True
        self._registra(
            "pull_orders", "error",
            _("Ordine %(ordine)s importato, ma il totale NON torna: in Odoo "
              "%(trovato).2f, su Kaufland %(atteso).2f.\n"
              "⚠️ Il prezzo scritto e' quello pagato dal cliente, IVA "
              "INCLUSA: se il totale e' PIU' ALTO, l'imposta applicata non e' "
              "configurata come «IVA inclusa» e si sta sommando a un prezzo "
              "che ce l'ha gia' dentro. Se e' piu' basso o diverso, l'aliquota "
              "non e' quella usata da Kaufland (%(aliquota)s%%).\n"
              "L'ordine e' corretto nei prodotti e nelle quantita': e' "
              "l'imposta a non combaciare, e va sistemata prima di "
              "fatturare.") % {
                  "ordine": id_order, "trovato": trovato, "atteso": atteso,
                  "aliquota": righe[0].get("vat") if righe else "?"},
            external_id=id_order)
        return False

    @staticmethod
    def _data_iso(testo):
        """La data ISO di Kaufland come Datetime di Odoo, o False."""
        if not testo:
            return False
        try:
            return fields.Datetime.to_datetime(
                str(testo).replace("Z", "").replace("T", " "))
        except Exception:  # noqa: BLE001 - una data storta non ferma un ordine
            return False

    def _cliente_da_riga(self, riga):
        """Il `res.partner` del compratore, creato se non c'e'.

        ⚠️ `street` e `house_number` arrivano SEPARATI: tenuti separati, il
        numero civico si perde e il pacco non arriva.

        ⚠️ E l'email non e' quella vera: Kaufland ne da' una di INOLTRO
        anonimo (`…@kaufland-marktplatz.de`). Va bene come chiave e per
        scrivere al cliente, ma non e' un recapito personale.
        """
        Partner = self.env["res.partner"].sudo()
        indirizzo = riga.get("shipping_address") or {}
        compratore = riga.get("buyer") or {}
        email = (compratore.get("email") or "").strip()

        nome = " ".join(x for x in (indirizzo.get("first_name"),
                                    indirizzo.get("last_name")) if x).strip()
        nome = nome or (indirizzo.get("company_name") or "").strip()             or email or _("Cliente Kaufland")

        esistente = Partner.search([("email", "=", email)], limit=1)             if email else Partner.browse()
        via = " ".join(x for x in (indirizzo.get("street"),
                                   indirizzo.get("house_number")) if x).strip()
        paese = self.env["res.country"].sudo().search(
            [("code", "=", (indirizzo.get("country") or "").upper())], limit=1)
        valori = {
            "name": nome,
            "street": via or False,
            "street2": indirizzo.get("additional_field") or False,
            "zip": indirizzo.get("postcode") or False,
            "city": indirizzo.get("city") or False,
            "country_id": paese.id if paese else False,
            "phone": indirizzo.get("phone") or False,
            "email": email or False,
        }
        if esistente:
            # ⚠️ Su un cliente che c'e' gia' si scrivono SOLO i campi che
            # abbiamo davvero. Scrivere anche i vuoti CANCELLEREBBE il suo
            # indirizzo buono, e una riga senza indirizzo non e' un caso di
            # scuola: per i primi 15 minuti Kaufland li nasconde apposta (vedi
            # STATO_DA_SPEDIRE). Il filtro di stato dovrebbe gia' impedirlo;
            # questa e' la seconda cintura, perche' il prezzo di sbagliarsi e'
            # un pacco che non arriva.
            pieni = {chiave: valore for chiave, valore in valori.items()
                     if valore}
            if pieni:
                esistente.write(pieni)
            return esistente
        return Partner.create(valori)

    def _segna_ordine_in_errore(self, mercato, id_order, messaggio):
        """La riga di mappa in errore: e' il posto dove si va a guardare."""
        Mappa = self.env["centrivo.order.map"].sudo()
        mappa = Mappa.search([("channel_id", "=", self.channel.id),
                              ("external_id", "=", id_order)], limit=1)
        valori = {"state": "error", "error_message": messaggio}
        if mappa:
            mappa.write(valori)
        else:
            Mappa.create(dict(valori, channel_id=self.channel.id,
                              external_id=id_order,
                              company_id=self.channel.company_id.id))
        self._registra("pull_orders", "error",
                           "Ordine %s: %s" % (id_order, messaggio))
        return True

    # ------------------------------------------------------------------
    # LA SPEDIZIONE — Consegna 2, Compito 5. Trigger MANUALE.
    # ------------------------------------------------------------------
    def push_shipment(self, order_map):
        """Comunica a Kaufland che l'ordine e' partito. UNA CHIAMATA PER RIGA.

        ⚠️ **Nessuna riga di questo metodo e' stata provata contro Kaufland
        vero.** La forma viene dalla loro guida (`docs/kaufland-spedizione-
        guida.md`), non da una misura: Kaufland non ha un ambiente di prova, e
        questa chiamata SCRIVE su ordini di clienti veri. Il primo giro si fa a
        mano, su un ordine, con qualcuno che guarda il portale.

        Da dove vengono i dati — tutti da campi PUBBLICI di Odoo:

        - il `sale.order` della mappa, e i suoi trasferimenti in stato `done`
          con `carrier_tracking_ref` valorizzato (campo NATIVO: chi l'abbia
          scritto, ShipTracker o una persona, non ci riguarda);
        - il codice corriere dall'impianto del tronco (vettore → corriere →
          codice del canale), con l'eccezione di canale che vince sempre.

        ⚠️ **L'esito incerto non e' un successo, e non e' nemmeno un
        fallimento.** Se la risposta e' un 5xx o non arriva, la richiesta puo'
        essere arrivata lo stesso: la riga NON si segna comunicata (si
        perderebbe la spedizione) ma non si ritenta da soli (si duplicherebbe).
        Resta li', e lo decide una persona. Un numero di tracciamento riusato
        e' un rifiuto, gia' misurato su Temu.
        """
        esterno = order_map.external_id

        # ⚠️ IL CANCELLO. Dal cron non si parte finche' una persona non ha
        # visto sul portale che il primo invio e' andato: queste chiamate non
        # sono mai state provate contro Kaufland vero, e scrivono su ordini di
        # clienti veri. A mano si passa: il primo invio E' quel gesto.
        if not (self.channel.sudo().kaufland_spedizione_provata
                or self.env.context.get("kaufland_spedizione_a_mano")):
            return self._spedizione_ferma(
                esterno, _("il primo invio a Kaufland si fa A MANO, dal "
                           "pulsante sull'ordine. Quando sul portale l'ordine "
                           "risultera' spedito, apri «Spedizione verificata "
                           "sul portale» sulla scheda del canale, e da li' in "
                           "poi ci pensera' anche l'automatismo"))

        righe = self.env["centrivo.kaufland.order.unit"].sudo().search(
            [("order_map_id", "=", order_map.id)])

        # --- Precondizioni: meglio non chiamare che chiamare al buio -------
        if order_map.state != "imported" or not order_map.sale_order_id:
            return self._spedizione_ferma(
                esterno, _("l'ordine non risulta importato, o non ha un ordine "
                           "di vendita collegato"))
        if not righe:
            return self._spedizione_ferma(
                esterno, _("non ci sono righe Kaufland registrate per questo "
                           "ordine: senza di quelle non si sa cosa comunicare"))

        da_fare = righe.filtered(lambda r: not r.spedizione_comunicata)
        if not da_fare:
            self._registra("push_shipment", "skip",
                           _("Ordine %s: spedizione gia' comunicata per tutte "
                             "le righe.") % esterno, external_id=esterno)
            self._chiudi_spedizione(order_map, righe)
            return True

        trasferimenti = order_map.sale_order_id.picking_ids.filtered(
            lambda p: p.state == "done" and (p.carrier_tracking_ref or "").strip())
        if not trasferimenti:
            return self._spedizione_ferma(
                esterno, _("nessuna spedizione pronta: serve un trasferimento "
                           "in stato «Fatto» con il numero di tracciamento"))

        codice = self._codice_corriere(trasferimenti, esterno)
        if not codice:
            return False

        # ⚠️ I numeri di tracciamento si uniscono con la VIRGOLA: e' cosi' che
        # Kaufland vuole il multi-collo, e `tracking_numbers` e' una STRINGA
        # malgrado il plurale.
        # ⚠️ Il limite di oggi, detto perche' si sappia: i numeri vanno TUTTI
        # su OGNI riga. Non proviamo a indovinare quale collo porti quale
        # articolo — un'attribuzione sbagliata darebbe al cliente il
        # tracciamento del pacco di un altro articolo, e non c'e' modo di
        # provarla finche' non passa un multi-collo vero.
        numeri = ",".join(dict.fromkeys(
            (t.carrier_tracking_ref or "").strip() for t in trasferimenti
            if (t.carrier_tracking_ref or "").strip()))

        client = self._client()
        corpo = {"carrier_code": codice, "tracking_numbers": numeri}
        for riga in da_fare:
            # ⚠️ Ogni riga per conto suo: una che salta in aria non deve
            # portarsi via quelle gia' accettate da Kaufland. Se la richiesta
            # intera venisse annullata, al giro dopo le rimanderemmo — cioe'
            # esattamente il doppione che stiamo evitando.
            try:
                self._comunica_riga(client, order_map, riga, corpo)
            except Exception as errore:  # noqa: BLE001
                _logger.exception("Kaufland: spedizione della riga %s",
                                  riga.id_order_unit)
                # ⚠️ Posizionali: `_al_riparo` di questo connettore prende
                # solo *argomenti, e il payload sta in mezzo (None qui).
                self._al_riparo(
                    self._registra, "push_shipment", "error",
                    _("Riga %s: %s") % (riga.id_order_unit, str(errore)[:2000]),
                    None, esterno)
        self._chiudi_spedizione(order_map, righe)
        return bool(order_map.shipment_pushed)

    def _comunica_riga(self, client, order_map, riga, corpo):
        """Una riga, una chiamata. Segna la riga SOLO su un verdetto certo."""
        esterno = order_map.external_id
        risposta = client.chiama(
            "PATCH", API_RIGA_SPEDITA % quote(str(riga.id_order_unit), safe=""),
            corpo)

        incerta = self._esito_incerto(risposta)
        if incerta:
            # ⚠️ Ne' fatta ne' fallita: la riga resta da comunicare, e a
            # deciderlo sara' una persona che guarda il portale.
            self._registra(
                "push_shipment", "error",
                _("Riga %s: esito INCERTO, %s. Non e' stata segnata come "
                  "comunicata: prima di ripremere, controlla sul portale "
                  "Kaufland se la spedizione risulta gia' registrata.")
                % (riga.id_order_unit, incerta),
                external_id=esterno)
            return False

        if not risposta.ok:
            self._registra(
                "push_shipment", "error",
                # ⚠️ `_motivo_http` e non `risposta.messaggio`: il secondo da'
                # solo il testo di Kaufland, senza lo stato. Un rifiuto a corpo
                # vuoto lascerebbe nel registro una riga che non dice niente, e
                # un 400 (colpa del dato) diventerebbe indistinguibile da tutto
                # il resto proprio mentre si cerca di capire cosa correggere.
                _("Riga %s rifiutata da Kaufland: %s")
                % (riga.id_order_unit, self._motivo_http(risposta)),
                external_id=esterno)
            return False

        riga.sudo().spedizione_comunicata = True
        self._registra("push_shipment", "success",
                       _("Riga %s: spedizione comunicata (%s, %s).")
                       % (riga.id_order_unit, corpo["carrier_code"],
                          corpo["tracking_numbers"]),
                       external_id=esterno)
        return True

    def _esito_incerto(self, risposta):
        """Il motivo per cui il verdetto NON e' certo, oppure None se lo e'.

        ⚠️ **Non e' un doppione di `_causa_incerta`, e questa nota esiste
        perche' ci sono cascato.** Quella, in questo connettore, non risponde
        MAI None: e' scritta per un chiamante che ha gia' stabilito che la
        risposta e' un rifiuto, e si limita a spiegare perche' l'esito e'
        ignoto. Chiamata su un 200 restituisce comunque un motivo — e la
        spedizione appena accettata da Kaufland risulterebbe «incerta»,
        cioe' da rifare. Il primo giro dentro Odoo ha fatto cadere quattro
        banchi esattamente su questo.

        Qui la regola sta scritta per esteso, una volta sola:

        - risposta a posto        → verdetto CERTO, e positivo;
        - stato 0 oppure 5xx      → non si sa se sia arrivata;
        - qualunque altro rifiuto → verdetto CERTO, e negativo (un 400 e'
          colpa del dato, e ridirlo «incerto» inviterebbe a ritentare).
        """
        if risposta.ok:
            return None
        if risposta.stato == 0 or risposta.stato >= 500:
            return self._causa_incerta(risposta)
        return None

    def _codice_corriere(self, trasferimenti, esterno):
        """Il nome del corriere atteso da Kaufland, o None dopo aver detto perche'.

        ⚠️ I trasferimenti devono puntare TUTTI allo stesso corriere: il corpo
        ne porta uno solo. Due corrieri diversi sullo stesso ordine non si
        possono esprimere, e sceglierne uno darebbe al cliente il tracciamento
        sotto il vettore sbagliato.
        """
        codici = {}
        for trasferimento in trasferimenti:
            modello, id_sorgente, nome = (
                self.channel._picking_carrier_source(trasferimento))
            if not id_sorgente:
                self._spedizione_ferma(
                    esterno, _("il trasferimento %s non ha un vettore da cui "
                               "ricavare il corriere") % trasferimento.name)
                return None
            esito = self.env["centrivo.carrier.map"].resolve_external_code(
                self.channel, modello, id_sorgente, self.channel.company_id)
            if not esito:
                if esito.failure_reason == NON_TRADOTTO:
                    self._spedizione_ferma(
                        esterno,
                        _("il corriere «%s» non ha un nome per Kaufland. "
                          "Guarda in Corrieri → Copertura corrieri, oppure "
                          "aggiungi un'eccezione di canale.")
                        % (esito.brand_name or "?"))
                else:
                    self._spedizione_ferma(
                        esterno,
                        _("il vettore «%s» non e' collegato a nessun corriere. "
                          "Aggiungi la riga in Corrieri → Vettori.") % nome)
                return None
            codici[esito.external_code] = True
        if len(codici) > 1:
            self._spedizione_ferma(
                esterno, _("i colli di questo ordine viaggiano con corrieri "
                           "diversi (%s), e Kaufland ne accetta uno solo per "
                           "riga") % ", ".join(sorted(codici)))
            return None
        return next(iter(codici))

    def _chiudi_spedizione(self, order_map, righe):
        """L'ordine e' «spedito» solo quando NON manca piu' nessuna riga."""
        tutte = all(riga.spedizione_comunicata for riga in righe)
        if tutte and not order_map.shipment_pushed:
            order_map.sudo().shipment_pushed = True
        return tutte

    def _spedizione_ferma(self, esterno, motivo):
        """Scrive perche' non si e' comunicato niente, e si ferma. Sempre False."""
        self._registra("push_shipment", "error",
                       _("Spedizione dell'ordine %s non comunicata: %s.")
                       % (esterno, motivo), external_id=esterno)
        return False

    # ------------------------------------------------------------------
    # Riaggancio
    # ------------------------------------------------------------------
    def riaggancia(self):
        """Legge le offerte già vive su Kaufland e popola la mappa SKU.

        ⚠️ NON scrive niente su Kaufland. Esiste perché su Kaufland ci sono
        già 166 offerte italiane, 165 delle quali nate senza che noi ne
        registrassimo l'identificativo. Creare offerte prima di sapere quali
        esistono significa duplicarle su un marketplace vero.

        L'accoppiamento è: `id_offer` di Kaufland (che è il nostro
        riferimento interno) → `product.product.default_code`.
        """
        mercato = self._mercato()
        client = self._client()
        Offerta = self.env["kaufland.offer"].sudo()
        Prodotto = self.env["product.product"].sudo()
        azienda = self.channel.company_id

        # ⚠️ Ogni riga letta deve finire in UNO di questi secchi, e in fondo
        # si controlla che sommino a `lette`. Una riga che non finisce da
        # nessuna parte è una riga di cui non sappiamo dire niente, e non si
        # apre la guardia della creazione su un «non lo so».
        lette = agganciate = senza_prodotto = contese = scartate = 0
        contese_dette = []
        try:
            # ⚠️ Il savepoint rende LOCALE l'intenzione «meglio niente che
            # metà». Oggi le righe parziali spariscono lo stesso, ma solo
            # perché la UserError risale fino al dispatch RPC del bottone: il
            # giorno in cui il riaggancio finisse su un cron con la forma
            # della casa (`except Exception` e si prosegue, vedi
            # integration_channel.py:363-379) resterebbero in banca dati in
            # silenzio, e mezzo riaggancio è peggio di nessuno.
            with self.env.cr.savepoint():
                for pagina in client.pagine("%s?storefront=%s"
                                            % (API_UNITA, mercato)):
                    for unita in pagina:
                        lette += 1
                        id_unit = self._testo_id(
                            unita.get("id_unit") or unita.get("unit_id"))
                        # ⚠️ `str()` prima di `.strip()`: `id_offer` arriva
                        # dal JSON di Kaufland e non è garantito testo. Un
                        # numero farebbe esplodere il giro con AttributeError
                        # a metà elenco, e senza LetturaInterrotta nessuno
                        # saprebbe che le righe in mano sono parziali.
                        codice = str(unita.get("id_offer") or "").strip()
                        if not id_unit or not codice:
                            # ⚠️ Il secchio delle scartate esiste perché una
                            # riga saltata in silenzio si confonde con una
                            # riga agganciata bene: se Kaufland cambiasse il
                            # nome di `id_offer`, cadrebbero TUTTE qui e il
                            # riaggancio si dichiarerebbe riuscito con zero
                            # agganci.
                            scartate += 1
                            continue
                        # ⚠️ Il dominio azienda: in `sudo()` la ricerca vede
                        # anche i prodotti delle altre aziende, e un
                        # `default_code` omonimo finirebbe agganciato a questo
                        # canale.
                        #
                        # ⚠️ Questo giro accoppia per `default_code`, la
                        # ricognizione accoppia per etichetta + EAN: NON sono
                        # due domini piu' o meno larghi sullo stesso insieme,
                        # sono due insiemi che si intersecano e basta. Un
                        # `default_code` cambiato dopo la creazione
                        # dell'offerta basta a far cadere l'offerta qui in
                        # `senza_prodotto` mentre di la' il prodotto risulta
                        # con la scheda pronta. Per questo `senza_prodotto`
                        # CHIUDE il cancello (vedi `_chiudi`) e l'offerta
                        # orfana si scrive come riga interrogabile (vedi
                        # `_segna_offerta_orfana`): non ci si affida al
                        # combaciare dei domini, che non combaciano.
                        prodotto = Prodotto.search([
                            ("default_code", "=", codice),
                            ("company_id", "in", [False, azienda.id]),
                        ], limit=1)
                        if not prodotto:
                            # ⚠️ Un'offerta viva su Kaufland che in Odoo non
                            # ha un prodotto e' una notizia, non un
                            # dettaglio: e' roba in vendita che non sappiamo
                            # di vendere. E finche' non si sa di chi e',
                            # nessun prodotto con quel codice a barre puo'
                            # ricevere una seconda offerta: la riga qui sotto
                            # e' cio' che rende quel fatto INTERROGABILE per
                            # EAN, che e' la chiave con cui la ricognizione
                            # guarda il mondo.
                            senza_prodotto += 1
                            self._segna_offerta_orfana(id_unit, unita, codice)
                            continue

                        # ⚠️ La contesa: `kaufland.offer` ha
                        # unique(channel_id, id_unit) e unique(channel_id,
                        # product_id). Scrivere alla cieca farebbe arrivare
                        # l'errore dal DATABASE, che in Odoo annulla l'INTERA
                        # transazione: si perderebbero anche le centinaia di
                        # righe già riagganciate bene, e il messaggio
                        # parlerebbe di un vincolo, non di due prodotti che si
                        # contendono un'offerta.
                        riga_unit = Offerta.search([
                            ("market_id", "=", self._riga_mercato().id),
                            ("id_unit", "=", id_unit)], limit=1)
                        riga_prodotto = Offerta.search([
                            ("market_id", "=", self._riga_mercato().id),
                            ("product_id", "=", prodotto.id)], limit=1)
                        conteso = self._contesa(id_unit, codice, prodotto,
                                                riga_unit, riga_prodotto)
                        if conteso:
                            contese += 1
                            if len(contese_dette) < 10:
                                contese_dette.append(conteso)
                            _logger.warning("Kaufland riaggancio: %s", conteso)
                            self._segna_contesa(prodotto, riga_prodotto,
                                                conteso)
                            continue

                        # ⚠️ QUI SI SCRIVEVA IN `centrivo.sku.map`, e non si
                        # fa piu' (2026-08-31, segnalato da Angelo guardando
                        # le 332 righe comparse la' dentro).
                        #
                        # Tre ragioni, e la terza da sola basta:
                        #  1. NESSUNO le rileggeva: ne' questo modulo ne' il
                        #     tronco. Erano scritture a vuoto.
                        #  2. Duplicavano cio' che sta gia' in
                        #     `kaufland.offer`, che tiene prodotto e id_unit
                        #     con i vincoli giusti.
                        #  3. ⚠️ La CHIAVE era quella sbagliata per lo scopo:
                        #     si scriveva `external_code = id_unit`
                        #     (l'identificativo dell'offerta, 392842971623),
                        #     mentre negli ORDINI arriva `id_offer` — il
                        #     nostro codice articolo (HDC00041). Anche se
                        #     qualcuno le avesse lette, non avrebbero mai
                        #     fatto combaciare niente.
                        #
                        # E il danno vero era sulla leggibilita': la mappa SKU
                        # e' la tabella delle ECCEZIONI messe a mano, quella
                        # che si apre per capire perche' un ordine non aggancia.
                        # Con 332 righe automatiche dentro, non si legge piu'.

                        # `riga_unit` senza prodotto e' una riga rimasta
                        # orfana (product_id e' `ondelete="set null"`): si
                        # riadotta, non se ne crea una seconda per la stessa
                        # offerta viva.
                        riga = riga_prodotto or riga_unit
                        valori = {"id_unit": id_unit,
                                  "ean": unita.get("ean") or prodotto.barcode,
                                  "ultimo_esito": "successo",
                                  "ultimo_messaggio": False,
                                  # ⚠️ Si ripulisce anche la FIRMA
                                  # dell'errore. Chi riporta una riga a
                                  # «successo» deve togliere il marchio di
                                  # chi aveva scritto l'errore: lasciarlo
                                  # farebbe credere all'allineamento che un
                                  # errore scritto piu' tardi da un ALTRO
                                  # giro (una contesa della ricognizione)
                                  # sia suo, e glielo farebbe cancellare —
                                  # cioe' proprio la cosa che `errore_da`
                                  # esiste per impedire.
                                  "errore_da": False,
                                  "controllato_il": fields.Datetime.now()}
                        # ⚠️ LA BASE DEL CONFRONTO DELL'ALLINEAMENTO.
                        # `ultimo_prezzo` e `ultima_quantita` sono Integer:
                        # valgono 0 finche' qualcuno non ci scrive, e fino a
                        # ieri ci scriveva solo la creazione. Sulle 165
                        # offerte riagganciate la base restava 0, e per un
                        # prodotto a giacenza 0 in Odoo l'allineamento
                        # confrontava `0 != 0` e non mandava niente: su
                        # Kaufland restava l'`amount` con cui l'offerta era
                        # nata, e l'offerta continuava a vendere merce che
                        # non c'e'. Questi due valori Kaufland li restituisce
                        # gia': si prendono, e la base diventa vera.
                        valori.update(self._base_da_kaufland(unita))
                        if riga:
                            if riga.product_id != prodotto:
                                valori["product_id"] = prodotto.id
                            riga.write(valori)
                        else:
                            Offerta.create(dict(
                                valori, market_id=self._riga_mercato().id,
                                product_id=prodotto.id))
                        agganciate += 1
        except LetturaInterrotta as errore:
            # ⚠️ Mezzo elenco NON e' un elenco. Se ci fermiamo a meta', la
            # guardia resta chiusa: meglio non creare niente che creare
            # doppioni.
            #
            # ⚠️ La UserError qui sotto annulla la transazione, e con essa
            # anche la riga di registro appena scritta. L'annullamento delle
            # scritture e' voluto, la perdita della traccia no: la si mette
            # anche nel log di sistema, che il rollback non tocca.
            _logger.error(
                "Kaufland riaggancio: lettura interrotta dopo %s offerte "
                "sul canale %s — %s", lette, self.channel.display_name,
                errore)
            self._registra(
                "kaufland_riaggancio", "error",
                _("Lettura interrotta dopo %s offerte: %s") % (lette, errore))
            # ⚠️ E QUI IL CANCELLO NON SI PUO' CHIUDERE, e va DETTO. La
            # UserError qui sotto annulla la transazione, quindi qualunque
            # `write` che richiudesse la guardia se ne andrebbe con lei: su un
            # canale gia' autorizzato la guardia resta APERTA mentre l'ultima
            # cosa che sappiamo e' che l'elenco era a meta'. Non e'
            # correggibile con una scrittura — si corregge dicendolo, e
            # dicendolo forte.
            if self._riga_mercato().riagganciato:
                avviso = _(
                    "\n\n⛔ La guardia del riaggancio era già aperta ed è "
                    "RIMASTA APERTA: questo giro non ha potuto richiuderla, "
                    "perché l'errore annulla anche le scritture. NON premere "
                    "«Crea le offerte mancanti» finché un riaggancio non "
                    "arriva in fondo: quello che si sa delle offerte vive è "
                    "fermo a un elenco letto a metà.")
            else:
                avviso = _("\n\nLa creazione delle offerte resta bloccata.")
            raise UserError(_(
                "La lettura delle offerte si è fermata dopo %s righe. "
                "Il riaggancio NON è completo.\n\n%s%s")
                % (lette, errore, avviso))

        return self._chiudi(lette, agganciate, senza_prodotto, contese,
                            scartate, contese_dette)

    # ------------------------------------------------------------------
    # I conti, e la guardia
    # ------------------------------------------------------------------
    def _chiudi(self, lette, agganciate, senza_prodotto, contese, scartate,
                contese_dette):
        """Tira le somme, decide se aprire la guardia, e lascia una riga.

        ⚠️ Il difetto che questo metodo chiudeva: prima bastava che `pagine()`
        non sollevasse per dichiarare il riaggancio riuscito. Un elenco vuoto
        — storefront sbagliato, permesso mancante, account non ancora
        abilitato a quel mercato — usciva verde con `lette=0` e la guardia
        aperta, e il passo successivo avrebbe creato 166 offerte doppie su un
        marketplace vero. Stessa cosa se Kaufland cambiasse il nome di
        `id_offer`: 166 lette, zero agganciate, verde.

        ⚠️ E il difetto che restava: i conti che tornano NON bastano. Un
        elenco coerentemente piu' corto quadra benissimo — vedi la soglia
        delle offerte attese, qui sotto.
        """
        canale = self.channel.sudo()
        somma = agganciate + senza_prodotto + contese + scartate
        quadra = somma == lette
        # ⚠️ LA SOGLIA DELLE OFFERTE ATTESE, e perche' i conti che tornano non
        # bastano. `pagine()` difende dalle letture TRONCATE (confronta con
        # `pagination.total`), NON da un elenco COERENTEMENTE piu' corto: se
        # `GET /units/` filtrasse per impostazione predefinita — solo unita'
        # attive, un permesso piu' stretto sulle credenziali, un cambio della
        # loro API — Kaufland risponderebbe 200 con 136 righe e `total: 136`.
        # I conti quadrerebbero, il cancello si aprirebbe, le 30 mancanti non
        # avrebbero nessuna riga qui (quindi nemmeno il secondo strato di
        # difesa le vedrebbe), la ricognizione le direbbe «pronte» — e avrebbe
        # ragione, la scheda esiste PERCHE' l'offerta ci vive sopra — e la
        # creazione metterebbe in vendita 30 doppioni veri.
        #
        # ⚠️ La soglia frena SOLO verso il basso. Le offerte che crescono sono
        # la vita normale del canale e non devono chiedere il permesso a
        # nessuno: si aprono il cancello e ALZANO la soglia. Solo un elenco
        # piu' corto di quello dell'ultimo riaggancio riuscito e' una notizia
        # che va capita prima di scrivere su Kaufland.
        attese = self._riga_mercato().offerte_attese or 0
        abbastanza = lette >= attese
        # La guardia si apre solo se la lettura ha prodotto qualcosa, se ogni
        # riga si sa dove è finita, e se NESSUNA riga è finita in un secchio
        # diverso da «agganciata».
        #
        # ⚠️ Qui c'era un'asimmetria — `senza_prodotto` non chiudeva la
        # guardia — e si reggeva su un ragionamento SBAGLIATO: «un `id_offer`
        # che nessun prodotto porta non può essere ricreato, perché manca il
        # prodotto da cui nascerebbe». Presupponeva che l'unica strada verso
        # la creazione passasse dal `default_code`. Non è così: la
        # ricognizione trova i prodotti per ETICHETTA e interroga Kaufland per
        # EAN. Basta un `default_code` cambiato dopo la creazione
        # dell'offerta — «Lavabo 60», codice `ABC123`, offerta viva `ABC-123`
        # sullo stesso EAN — perché il riaggancio non accoppi, la ricognizione
        # dica «pronta» (e abbia ragione: la scheda esiste, l'offerta è viva)
        # e la creazione metta in vendita una SECONDA offerta sullo stesso
        # codice a barre. Il numero da cui parte questa consegna è «165
        # offerte nate senza che ne registrassimo l'identificativo»: i
        # `default_code` sballati non sono un'ipotesi di scuola.
        sano = (bool(lette) and quadra and not contese
                and not scartate and not senza_prodotto)
        # ⚠️ LA SOGLIA SOPRAVVIVEVA AL PROPRIO RIMEDIO NEL GIRO CHE CONTA.
        # `abbastanza` e' «lette >= attese», e su un canale nuovo `attese`
        # vale 0: e' vero SEMPRE. Cioe' il primo giro — l'unico che questo
        # canale non ha ancora fatto, e l'unico che nessuna soglia ha mai
        # protetto — passava comunque. Con 136 offerte lette su 166 vere i
        # conti quadrano, il cancello si apriva, la soglia NASCEVA a 136, e
        # le 30 che non compaiono non hanno nessuna riga qui: la
        # ricognizione le dichiara «pronte» (e ha ragione: la scheda esiste
        # perche' l'offerta ci vive sopra) e la creazione ne farebbe 30
        # doppioni VERI su un marketplace vero. Era anche il solo momento in
        # cui una difesa contro i doppioni riposava INTERAMENTE sull'occhio
        # di una persona — con una notifica verde, non appiccicata, che il
        # numero non lo nominava nemmeno.
        #
        # Quindi il primo giro MISURA e basta: scrive il numero, lascia la
        # guardia chiusa e dice cosa fare. Costa a una persona un numero da
        # confermare, una volta, su un canale solo. Chi la soglia se la
        # scrive a mano PRIMA — la via sicura — non paga nemmeno quello: al
        # primo giro il cancello si apre.
        prima_misura = sano and not attese
        completo = sano and abbastanza and not prima_misura

        messaggio = _(
            "Lette %(lette)s offerte su Kaufland: %(agganciate)s "
            "riagganciate, %(senza)s senza prodotto in Odoo, %(contese)s "
            "contese, %(scartate)s scartate (senza identificativo o senza "
            "codice)."
        ) % {"lette": lette, "agganciate": agganciate,
             "senza": senza_prodotto, "contese": contese,
             "scartate": scartate}
        if not lette:
            messaggio += _(
                "\n⚠️ Kaufland non ha restituito NESSUNA offerta. Non "
                "significa che non ce ne siano: le cause tipiche sono il "
                "mercato sbagliato sul canale, un permesso mancante sulle "
                "credenziali, o l'account non ancora abilitato a quel "
                "mercato.")
        if not quadra:
            messaggio += _(
                "\n⚠️ I conti non tornano: %s righe lette non sono finite in "
                "nessun secchio. Il riaggancio non è affidabile.") % (
                lette - somma)
        if scartate:
            messaggio += _(
                "\n⚠️ %s righe sono arrivate senza identificativo o senza "
                "codice: sono offerte vive di cui non sappiamo dire niente. "
                "Se sono TUTTE le righe lette, Kaufland ha probabilmente "
                "cambiato il nome di `id_offer` o di `id_unit`.") % scartate
        if senza_prodotto:
            messaggio += _(
                "\n⚠️ %s offerte vive non hanno un prodotto in Odoo: sono in "
                "vendita e non lo sappiamo. Ognuna ha ora una riga con il suo "
                "identificativo e il suo codice a barre: va capito di quale "
                "prodotto è (di solito il codice interno è cambiato dopo la "
                "creazione dell'offerta), e finché non si sa nessun prodotto "
                "con quel codice a barre può ricevere una seconda offerta."
            ) % senza_prodotto
        if contese:
            messaggio += _("\n⚠️ %s offerte contese, NON agganciate:\n%s") % (
                contese, "\n".join(contese_dette))
            if contese > len(contese_dette):
                messaggio += _("\n… e altre %s.") % (
                    contese - len(contese_dette))
        if not abbastanza:
            messaggio += _(
                "\n⚠️ Kaufland ha restituito %(lette)s offerte, ma su "
                "questo canale ne sono attese %(attese)s: ne mancano "
                "%(mancanti)s. Un elenco più corto NON è la prova che le "
                "offerte siano diminuite: può essere un filtro predefinito "
                "della loro API, un permesso più stretto sulle credenziali o "
                "un cambio del loro lato — e in quel caso le offerte che non "
                "compaiono non hanno una riga qui, la ricognizione le "
                "dichiara pronte, e la creazione ne farebbe altrettanti "
                "doppioni VERI su un marketplace vero. Se le offerte sono "
                "DAVVERO diminuite (ritirate o chiuse dal portale), si "
                "corregge a mano «Offerte attese su Kaufland» sul canale e si "
                "ripete il riaggancio.") % {
                    "lette": lette, "attese": attese,
                    "mancanti": attese - lette}
        if completo:
            valori = {
                "riagganciato": True,
                "riagganciato_il": fields.Datetime.now(),
            }
            if lette > attese:
                valori["offerte_attese"] = lette
                messaggio += _(
                    "\nLe offerte vive sono cresciute da %(attese)s a "
                    "%(lette)s: la soglia si alza da sola.") % {
                        "attese": attese, "lette": lette}
            self._riga_mercato().write(valori)
        else:
            # ⚠️ Il cancello si scrive in TUTTI E DUE I VERSI. Prima si
            # scriveva solo a `True`: al primo giro «non lo apre» equivale a
            # «lo chiude», ma dal secondo in poi no — un canale gia'
            # autorizzato restava autorizzato anche quando il riaggancio
            # scopriva un'offerta orfana. Un giro che trova un problema deve
            # TOGLIERE l'autorizzazione, non lasciarla dov'era. La data se ne
            # va con lui: una data accanto a un cancello chiuso si legge come
            # se valesse ancora.
            era_aperta = bool(self._riga_mercato().riagganciato)
            valori = {}
            if era_aperta or self._riga_mercato().riagganciato_il:
                valori = {"riagganciato": False,
                          "riagganciato_il": False}
            if prima_misura:
                # ⚠️ LA PRIMA VOLTA NESSUNO DEVE INDOVINARE UN NUMERO: lo
                # misura questo giro, e lo scrive. Ma NON si autorizza da
                # solo: un numero misurato e un numero verificato non sono la
                # stessa cosa, e questo e' l'unico punto del modulo in cui la
                # difesa contro i doppioni non ha altro su cui appoggiarsi.
                #
                # ⚠️ E si scrive solo se il resto del giro e' SANO (`sano`):
                # una soglia che nasce da una lettura di cui non ci si fida
                # sarebbe peggio di nessuna soglia.
                valori["offerte_attese"] = lette
            if valori:
                self._riga_mercato().write(valori)
            if prima_misura:
                messaggio += _(
                    "\nPrima misura: questo giro ha trovato %(lette)s "
                    "offerte vive, e il numero è ora scritto sul canale come "
                    "«Offerte attese su Kaufland».\n⚠️ La guardia RESTA "
                    "CHIUSA APPOSTA, e non c'è niente da riparare: la prima "
                    "misura è l'unica che nessuna soglia ha protetto. Se "
                    "Kaufland ne avesse restituite meno di quante ce ne sono "
                    "davvero (un filtro predefinito della loro API, un "
                    "permesso più stretto sulle credenziali), quel numero "
                    "sbagliato nascerebbe qui come soglia giusta, e le "
                    "offerte che non compaiono resterebbero creabili una "
                    "seconda volta.\nControlla che %(lette)s sia il numero "
                    "di offerte che vedi sul portale Kaufland per questo "
                    "mercato: se corrisponde, conferma il numero sul canale e "
                    "ripeti il riaggancio — al secondo giro la guardia si "
                    "apre. Se sul portale ne vedi di più, correggi tu il "
                    "numero PRIMA di ripetere.") % {"lette": lette}
            else:
                messaggio += (_(
                    "\n⚠️ La guardia del riaggancio è stata RICHIUSA: era "
                    "aperta, e questo giro ha trovato qualcosa che non "
                    "torna.")
                    if era_aperta else _(
                    "\n⚠️ La guardia del riaggancio RESTA CHIUSA.")) + _(
                    " La creazione delle offerte non partirà finché non si "
                    "risolve quanto sopra e non si ripete il riaggancio. Le "
                    "righe già agganciate restano.")
            _logger.warning(
                "Kaufland riaggancio sul canale %s: guardia NON aperta. %s",
                self.channel.display_name, messaggio)
        # ⚠️ Niente verde su un lavoro parziale. `completo` comprende gia'
        # `senza_prodotto`, che ora chiude la guardia come tutto il resto.
        self._registra(
            "kaufland_riaggancio", "success" if completo else "error",
            messaggio)
        return {"lette": lette, "agganciate": agganciate,
                "senza_prodotto": senza_prodotto, "contese": contese,
                "scartate": scartate, "completo": completo,
                "attese": attese, "prima_misura": prima_misura}

    # ------------------------------------------------------------------
    # Le offerte vive che non sappiamo di chi sono
    # ------------------------------------------------------------------
    def _segna_offerta_orfana(self, id_unit, unita, codice):
        """Registra l'offerta viva che in Odoo non ha un prodotto.

        ⚠️ Senza questa riga l'informazione resta solo nel registro delle
        operazioni, che non si interroga per codice a barre — e la
        ricognizione, che il mondo lo guarda per EAN, non ha nessun modo di
        sapere che quell'EAN e' gia' in vendita. E' il ponte fra le due
        chiavi: il riaggancio accoppia per `default_code`, la ricognizione per
        EAN, e questa riga porta tutti e due.

        Il prodotto resta VUOTO: attribuire l'offerta al prodotto che ha lo
        stesso EAN sarebbe cambiare il criterio di accoppiamento su un
        marketplace vero, ed e' una decisione di prodotto, non una
        correzione.
        """
        Offerta = self.env["kaufland.offer"].sudo()
        ean = str(unita.get("ean") or "").strip()
        motivo = _(
            "offerta viva su Kaufland (identificativo %s, codice interno %s) "
            "che in Odoo non corrisponde a nessun prodotto: finché non si sa "
            "di chi è, nessun prodotto con questo codice a barre può ricevere "
            "una seconda offerta.") % (id_unit, codice)
        valori = {"ean": ean or False,
                  "ultimo_esito": "errore",
                  "ultimo_messaggio": motivo,
                  # ⚠️ Chi SCRIVE un errore lo firma, anche quando la firma
                  # e' «di nessuno»: se la riga portava gia' la firma
                  # dell'allineamento da un giro fallito, questo motivo la
                  # EREDITEREBBE, e il primo allineamento riuscito lo
                  # cancellerebbe credendosi autorizzato a chiudere un
                  # errore suo. Sparirebbe la notizia, non l'errore.
                  "errore_da": False,
                  "controllato_il": fields.Datetime.now()}
        # ⚠️ Si cerca per identificativo prima di creare: `kaufland.offer` ha
        # unique(channel_id, id_unit), e un secondo giro di riaggancio
        # riporterebbe la stessa offerta orfana. L'errore arriverebbe dal
        # DATABASE, che in Odoo annulla l'INTERA transazione.
        esistente = Offerta.search([("market_id", "=", self._riga_mercato().id),
                                    ("id_unit", "=", id_unit)], limit=1)
        if esistente:
            # ⚠️ `product_id` non si tocca: se una riga porta gia' quella
            # offerta intestata a un prodotto, quel legame vale piu' di questo
            # giro — qui sappiamo solo che il `default_code` non ha ritrovato
            # nessuno, non che l'intestazione precedente fosse sbagliata.
            return esistente.write(valori)
        return Offerta.create(dict(valori, market_id=self._riga_mercato().id,
                                   id_unit=id_unit))

    # ------------------------------------------------------------------
    # Le contese
    # ------------------------------------------------------------------
    def _segna_contesa(self, prodotto, riga_prodotto, motivo):
        """Lascia della contesa una traccia INTERROGABILE, sul prodotto.

        ⚠️ Senza, il prodotto che perde la contesa resta senza nessuna riga
        `kaufland.offer`, cioè indistinguibile da uno mai agganciato: la
        ricognizione lo dichiarerebbe «pronta» e la creazione gli farebbe un
        doppione. Il motivo sta nel registro, ma il registro non si interroga
        per prodotto.

        ⚠️ `id_unit` NON si tocca: è proprio quello che non sappiamo, ed è il
        campo su cui batte il vincolo del database.
        """
        valori = {"ultimo_esito": "errore",
                  "ultimo_messaggio": motivo,
                  # ⚠️ Come in `_segna_offerta_orfana`: la contesa non deve
                  # ereditare la firma di chi aveva scritto l'errore prima,
                  # o l'allineamento se la porta via al primo giro riuscito
                  # e la riga torna verde mentre due offerte vive si
                  # contendono lo stesso codice a barre.
                  "errore_da": False,
                  "controllato_il": fields.Datetime.now()}
        if riga_prodotto:
            return riga_prodotto.write(valori)
        # Nel caso che perde, il prodotto non ha una riga sua: crearla non
        # incontra nessuno dei due vincoli (id_unit resta vuoto, e Postgres
        # considera i NULL distinti fra loro).
        return self.env["kaufland.offer"].sudo().create(dict(
            valori, market_id=self._riga_mercato().id, product_id=prodotto.id))

    @staticmethod
    def _contesa(id_unit, codice, prodotto, riga_unit, riga_prodotto):
        """Il motivo per cui questa unità NON si può agganciare, o None.

        Due casi, entrambi finirebbero contro un vincolo del database:
        l'offerta è già intestata a un altro prodotto, oppure il prodotto è
        già intestato a un'altra offerta viva (cioè su Kaufland il doppione
        esiste già, ed è proprio quello che si voleva sapere).
        """
        if riga_unit and riga_unit != riga_prodotto:
            # ⚠️ L'unica eccezione benigna: la riga che porta quell'offerta
            # non ha piu' un prodotto (`ondelete="set null"`) e il prodotto
            # ritrovato non ha una riga sua. Li' non c'e' contesa: c'e' una
            # riadozione, e la fa il chiamante.
            if riga_unit.product_id or riga_prodotto:
                return _(
                    "l'offerta %s (codice %s) risulta già di «%s», mentre il "
                    "codice punta a «%s»") % (
                    id_unit, codice,
                    riga_unit.product_id.display_name if riga_unit.product_id
                    else _("una riga senza prodotto"),
                    prodotto.display_name)
        if riga_prodotto and riga_prodotto.id_unit \
                and riga_prodotto.id_unit != id_unit:
            return _(
                "il prodotto «%s» (codice %s) è già agganciato all'offerta "
                "%s: su Kaufland ne risulta viva anche la %s") % (
                prodotto.display_name, codice, riga_prodotto.id_unit, id_unit)
        return None

    # ------------------------------------------------------------------
    # La ricognizione
    # ------------------------------------------------------------------
    def _dominio_prodotti(self):
        """Il dominio dei prodotti di QUESTO canale, prima del codice a barre.

        ⚠️ QUESTO DOMINIO DEVE RESTARE ALLINEATO A QUELLO CON CUI
        `riaggancia()` cerca il prodotto: stesso `sudo()`, stesso dominio
        azienda, e in entrambi i casi gli archiviati restano fuori perche'
        nessuno dei due passa `active_test=False`. Chi tocca uno dei due deve
        toccare anche l'altro.

        ⚠️ Ma l'allineamento dei domini NON basta, e non ci si appoggia: i due
        giri accoppiano su CHIAVI DIVERSE — il riaggancio per `default_code`,
        la ricognizione per etichetta + EAN — quindi non sono due domini piu'
        o meno larghi sullo stesso insieme, sono due insiemi che si
        intersecano e basta. Cio' che rende sicura la consegna e' altrove:
        `senza_prodotto` chiude il cancello, l'offerta viva senza prodotto
        diventa una riga interrogabile per EAN, e la ricognizione rifiuta di
        dichiarare creabile un prodotto il cui codice a barre e' gia' di
        un'offerta viva.
        """
        etichette = self.channel.export_product_tag_ids
        if not etichette:
            raise UserError(_(
                "Sul canale «%s» non è indicata alcuna etichetta prodotto: "
                "non so quali prodotti portare su Kaufland.")
                % self.channel.display_name)
        return [("product_tmpl_id.product_tag_ids", "in", etichette.ids),
                ("company_id", "in", [False, self.channel.company_id.id])]

    def _guardati_quando(self):
        """Quando ogni prodotto di questo canale e' stato guardato l'ultima
        volta. Restituisce {id del prodotto: data}, e un prodotto che non
        compare non e' MAI stato guardato.

        ⚠️ La data si normalizza con `fields.Datetime.to_datetime`: in Odoo
        il campo torna gia' come `datetime`, ma confrontare valori arrivati
        per strade diverse (una stringa scritta a mano, un `False`) mette in
        fila un `str` e un `datetime` e il confronto esplode. Qui l'ordine
        di tutta la ricognizione dipende da questi confronti.
        """
        quando = {}
        for riga in self.env["kaufland.offer"].sudo().search([
                ("market_id", "=", self._riga_mercato().id),
                ("product_id", "!=", False),
                ("controllato_il", "!=", False)]):
            data = fields.Datetime.to_datetime(riga.controllato_il)
            # ⚠️ La cintura: il dominio esclude gia' i vuoti, ma un `None`
            # che arrivasse fin qui finirebbe nella chiave di ordinamento e
            # farebbe esplodere il confronto con un `datetime` — cioe' la
            # ricognizione intera, per un campo vuoto.
            if data:
                quando[riga.product_id.id] = data
        return quando

    def _prodotti_del_canale(self, guardati_quando=None):
        """I prodotti da portare su questo mercato: quelli etichettati,
        NELL'ORDINE IN CUI VANNO GUARDATI.

        Stesso campo degli altri connettori della casa (ManoMano, BricoBravo),
        cosi' la regola e' una sola per tutti i marketplace.

        Il codice a barre e' l'UNICO restringimento rispetto al dominio del
        riaggancio, e restringere non e' pericoloso: su Kaufland la scheda si
        cerca per EAN, quindi un prodotto senza codice non si puo' guardare —
        non ne nasce nessuna riga, e quindi nessun candidato alla creazione.
        Restringere non inventa candidati; allargare sì. Quanti restano fuori
        lo dice comunque la ricognizione: un prodotto etichettato che non
        andra' mai in vendita e' una notizia, non un dettaglio.

        ⚠️ Il filtro toglie solo i codici ASSENTI: quelli fatti di soli spazi
        passano di qui e li ferma il ciclo, perche' un dominio Odoo non sa
        fare `strip` e un codice di soli spazi e' un codice che non c'e'.

        ⚠️ L'ORDINE E' IL RIMEDIO, e non e' un abbellimento. Il ciclo della
        ricognizione ha un tetto (`MAX_GUARDATI_PER_GIRO`), e scorreva questo
        elenco DALL'INIZIO a ogni chiamata: due giri di fila guardavano gli
        stessi prodotti. Sul catalogo vero — 549 prodotti misurati, tetto 400
        — quelli oltre il tetto non sarebbero stati guardati MAI, per quante
        volte si premesse il bottone: nessuna riga `kaufland.offer`, quindi
        mai candidati, quindi mai in vendita, in silenzio. Qui i mai
        guardati vanno per primi e gli altri dal piu' vecchio, cosi' il giro
        RIPRENDE da dove si era fermato.

        ⚠️ E quando sono stati guardati TUTTI si ricomincia dai piu' vecchi,
        invece di fermarsi credendo di aver finito: una ricognizione
        periodica e' utile — le schede su Kaufland cambiano senza avvisarci —
        e a fermarsi qui il bottone diventerebbe inerte per sempre.

        ⚠️ Si ordina in Python e non con `order=`: la data non sta su
        `product.product`, sta sulla riga `kaufland.offer` del canale, e un
        `order` sul prodotto non la puo' vedere. La chiave e' un `bool` prima
        della data, cosi' i mai guardati non hanno bisogno di una data finta
        con cui confrontarsi, e l'`id` chiude i pari merito perche' un ordine
        instabile riguarderebbe due volte gli stessi.
        """
        if guardati_quando is None:
            guardati_quando = self._guardati_quando()
        prodotti = self.env["product.product"].sudo().search(
            self._dominio_prodotti() + [("barcode", "!=", False)])
        return sorted(prodotti, key=lambda p: (
            (True, guardati_quando[p.id], p.id) if p.id in guardati_quando
            else (False, p.id)))

    def _quanti_senza_barcode(self):
        """Quanti prodotti etichettati non hanno proprio il codice a barre.

        Quelli col codice fatto di soli spazi li conta il ciclo, che e'
        l'unico posto dove il codice si puo' ripulire: un dominio Odoo non sa
        fare `strip`.
        """
        return self.env["product.product"].sudo().search_count(
            self._dominio_prodotti() + [("barcode", "=", False)])

    def _offerte_vive_per_ean(self):
        """Le offerte vive gia' note su questo canale, per codice a barre.

        Restituisce {ean: [(id_unit, id del prodotto o False), ...]}.

        ⚠️ Sono le righe con un `id_unit`: quelle riagganciate e quelle
        orfane scritte da `_segna_offerta_orfana`. Le righe nate dalla
        ricognizione non hanno identificativo e non entrano — altrimenti ogni
        prodotto si vedrebbe rifiutato dalla propria riga del giro prima.
        """
        mappa = {}
        for riga in self.env["kaufland.offer"].sudo().search([
                ("market_id", "=", self._riga_mercato().id),
                ("id_unit", "!=", False),
                ("ean", "!=", False)]):
            mappa.setdefault((riga.ean or "").strip(), []).append(
                (riga.id_unit, riga.product_id.id))
        return mappa

    @staticmethod
    def _codici_condivisi(prodotti):
        """I prodotti che si contendono lo stesso codice a barre, fra loro.

        Restituisce {ean: [prodotto, ...]} solo per i codici portati da PIU'
        di un prodotto dell'insieme.

        ⚠️ E' la stessa famiglia del rifiuto sulle offerte vive, ma il gemello
        sta dentro Odoo: due prodotti con lo stesso codice a barre e nessuna
        offerta ancora viva passerebbero tutti e due come «pronti», e la
        creazione ne farebbe due sullo stesso EAN. Il rifiuto costruito sulle
        offerte non li vede, perche' di offerte non ce n'e' nemmeno una.
        """
        per_codice = {}
        for prodotto in prodotti:
            codice = (prodotto.barcode or "").strip()
            if codice:
                per_codice.setdefault(codice, []).append(prodotto)
        return {c: elenco for c, elenco in per_codice.items()
                if len(elenco) > 1}

    @staticmethod
    def _elenco_nomi(prodotti, quanti=5):
        """I nomi di qualche prodotto, senza allagare il messaggio."""
        nomi = ["«%s»" % p.display_name for p in prodotti[:quanti]]
        if len(prodotti) > quanti:
            nomi.append(_("e altri %s") % (len(prodotti) - quanti))
        return ", ".join(nomi)

    def _stato_letto(self, risposta):
        """Lo stato della scheda letto da una risposta, con una difesa in piu'.

        ⚠️ `kaufland_catalogo.stato_scheda` legge come «guscio» qualunque
        risposta 2xx i cui dati non dicano titolo e validita', ed e' la
        lettura giusta quando i dati ci sono e sono vuoti (misurato il
        2026-08-22: 65 prodotti su 549). Ma una 2xx che NON porta affatto
        l'oggetto `data` non e' un verdetto su quel prodotto: e' la pagina
        d'errore di un proxy che risponde HTML con stato 200, o un corpo che
        non si e' capito — la stessa cosa contro cui `pagine()` si difende.
        Contarla come guscio direbbe «serve la scheda» per TUTTI i prodotti,
        cioe' farebbe scrivere centinaia di schede che non servivano:
        esattamente il danno che il quarto stato esiste per evitare. Qui vale
        «non lo so», e col dettaglio della risposta, non con un generico.
        """
        if risposta.ok and not isinstance(risposta.dati, dict):
            return {"stato": None, "id_product": None, "titolo": None,
                    "id_categoria": None,
                    "errore": _("HTTP %s senza i dati del prodotto: %s")
                    % (risposta.stato, risposta.messaggio)}
        return stato_scheda(risposta.stato, risposta.dati, risposta.messaggio)

    def ricognizione(self):
        """Per ogni prodotto etichettato, in che stato è la scheda Kaufland.

        ⚠️ NON scrive niente su Kaufland: e' una lettura. In Odoo scrive solo
        lo stato letto sulla riga `kaufland.offer` del prodotto.

        ⚠️ Gli stati sono TRE più «non lo so». Il «guscio» — Kaufland
        risponde 200 ma la pagina non esiste — va contato come «serve la
        scheda»: un'offerta agganciata lì non vende nulla e non dà alcun
        errore. E «non lo so» NON è un verdetto: trattarlo come «serve la
        scheda» farebbe scrivere centinaia di schede inutili, e per questo
        chiude il verde sull'intera ricognizione invece di sparire nei conti.

        ⚠️ Niente `savepoint` qui, al contrario del riaggancio: la' meta'
        lavoro apriva un cancello, qui ogni riga scritta e' un fatto vero e
        indipendente dalle altre, e una ricognizione interrotta non autorizza
        proprio niente.
        """
        mercato = self._mercato()
        client = self._client()
        Offerta = self.env["kaufland.offer"].sudo()
        adesso = fields.Datetime.now()

        # ⚠️ Le date di controllo si leggono UNA volta sola: servono sia a
        # ordinare l'elenco (il giro riprende da dove si era fermato) sia a
        # sapere chi non e' MAI stato guardato, che e' cio' che il messaggio
        # finale conta come «restano».
        visti_prima = self._guardati_quando()
        prodotti = self._prodotti_del_canale(visti_prima)
        totale = len(prodotti)
        # ⚠️ CHI NON E' MAI STATO GUARDATO, e perche' `da_guardare` non puo'
        # piu' essere `totale - fatti`. Con un catalogo piu' grande del tetto
        # quel conto non arriva MAI a zero — 549 prodotti e tetto 400 fanno
        # 149 per sempre — e siccome `da_guardare` chiude il verde, la
        # ricognizione sarebbe rossa per costruzione: il semaforo su cui si
        # decide di creare diventa inutilizzabile, e ci si abitua al rosso.
        # Quel che va davvero atteso e' che ogni prodotto sia stato guardato
        # ALMENO UNA VOLTA; da li' in poi ripetere il comando e' una
        # ricognizione periodica, non un lavoro lasciato a meta'.
        mai_guardati = {p.id for p in prodotti if p.id not in visti_prima}
        raggiunti = set()
        # ⚠️ Le offerte gia' vive, prese UNA volta sola: una ricerca per
        # prodotto sarebbe una seconda interrogazione su 549 giri.
        vive = self._offerte_vive_per_ean()
        condivisi = self._codici_condivisi(prodotti)
        conteggi = {"guardati": 0, "pronte": 0, "gusci": 0, "assenti": 0,
                    "sconosciuti": 0, "gia_vive": 0, "gemelli": 0,
                    "doppioni": 0, "da_guardare": 0,
                    "senza_barcode": self._quanti_senza_barcode()}
        fatti = 0
        chiamate = 0
        fermata = None
        partenza = time.monotonic()
        try:
            for prodotto in prodotti:
                # ⚠️ IL TETTO, e qui serviva più che altrove: una chiamata
                # per prodotto, tutte dentro la stessa richiesta web, e
                # nessuna scrittura prima della fine. Sfondare
                # `limit_time_real` non lascia mezzo lavoro: non lascia
                # NIENTE, nemmeno la riga di registro. Vedi
                # `MAX_GUARDATI_PER_GIRO`.
                if chiamate >= MAX_GUARDATI_PER_GIRO:
                    fermata = _(
                        "Il giro si è fermato al tetto di %s prodotti "
                        "guardati per volta, per non farsi uccidere dal "
                        "limite di tempo del worker (che butterebbe via "
                        "l'intera ricognizione, righe comprese). Ripetere "
                        "per continuare.") % MAX_GUARDATI_PER_GIRO
                    break
                # ⚠️ Il tempo si guarda solo dopo la prima chiamata:
                # altrimenti un canale lento non guarderebbe mai niente.
                if chiamate and time.monotonic() - partenza > SECONDI_PER_GIRO:
                    fermata = _(
                        "Il giro si è fermato da solo dopo %s secondi per "
                        "non farsi uccidere dal limite di tempo del worker "
                        "(che butterebbe via l'intera ricognizione, righe "
                        "comprese). Ripetere per continuare."
                    ) % SECONDI_PER_GIRO
                    break
                fatti += 1
                # ⚠️ «Raggiunto» e non «guardato»: un codice di soli spazi
                # esce dal ciclo qui sotto senza nessuna chiamata e senza
                # riga, ma lo si e' comunque esaminato in questo giro — e lo
                # conta gia' `senza_barcode`, che chiude il verde per conto
                # suo. Lasciarlo fra i «da guardare» terrebbe il numero
                # sopra lo zero per sempre, cioe' il difetto di prima con un
                # altro nome.
                raggiunti.add(prodotto.id)
                # ⚠️ `("barcode", "!=", False)` lascia passare "   ": in URL
                # diventerebbe `/products/ean/?storefront=it`, cioe' una
                # domanda su nessun codice, e un 404 la farebbe uscire
                # «assente» — candidata a farsi scrivere una scheda con l'EAN
                # vuoto. Un codice di soli spazi e' un codice che non c'e'.
                codice = (prodotto.barcode or "").strip()
                if not codice:
                    conteggi["senza_barcode"] += 1
                    continue
                conteggi["guardati"] += 1
                chiamate += 1
                # ⚠️ Il codice a barre finisce dentro un URL, e la firma si
                # calcola su quell'URL: un carattere da sfuggire (spazio, `&`,
                # `#` — il campo di Odoo e' testo libero) produrrebbe un
                # indirizzo diverso da quello voluto, o peggio un secondo
                # parametro di ricerca appiccicato a `storefront`.
                risposta = client.chiama(
                    "GET", "%s%s?storefront=%s" % (
                        API_PRODOTTI_EAN, quote(codice, safe=""), mercato))
                letto = self._stato_letto(risposta)
                stato = letto["stato"] or "sconosciuto"
                conteggi[SECCHI[stato]] += 1

                riga = Offerta.search(
                    [("market_id", "=", self._riga_mercato().id),
                     ("product_id", "=", prodotto.id)], limit=1)
                valori = {"ean": codice, "stato_scheda": stato,
                          "controllato_il": adesso}
                motivo = letto["errore"]

                # ⚠️ IL RIFIUTO, e A CHI SI APPLICA. Una riga che porta
                # gia' il suo `id_unit` NON e' a rischio di doppione: la
                # creazione cerca le righe SENZA identificativo, e non la
                # guardera' mai. Marcarla in errore direbbe «rotto» di un
                # prodotto che vende regolarmente — e se l'allineamento
                # saltasse le righe in errore, smetterebbe di aggiornarne
                # prezzo e giacenza in silenzio. Se c'e' qualcosa di storto,
                # va nel log, non sulla riga.
                if riga and riga.id_unit:
                    if [u for u, pid in vive.get(codice, ())
                            if pid != prodotto.id]:
                        # ⚠️ IL DOPPIONE CHE C'E' GIA'. La riga non si tocca
                        # (vende regolarmente, e marcarla direbbe «rotto» di
                        # un prodotto sano), ma il fatto SI CONTA: finche'
                        # finiva in un solo `_logger.warning`, la
                        # ricognizione usciva VERDE sapendo che su Kaufland
                        # due offerte vive portano lo stesso codice a barre.
                        # E il verde della ricognizione e' il semaforo su cui
                        # si decide di creare: un giro verde con un doppione
                        # noto dentro insegna a fidarsi del verde.
                        conteggi["doppioni"] += 1
                        _logger.warning(
                            "Kaufland ricognizione: «%s» è agganciato "
                            "all'offerta %s, ma sul canale %s il codice a "
                            "barre %s risulta anche di altre offerte vive.",
                            prodotto.display_name, riga.id_unit,
                            self.channel.display_name, codice)
                else:
                    impedimenti = []
                    # a) l'offerta viva di qualcun altro sullo stesso codice:
                    #    la scheda sara' anche «pronta» — ed e' vero, la
                    #    pagina esiste proprio perche' l'offerta ci vive sopra
                    #    — ma creargliene una seconda metterebbe in vendita
                    #    due volte lo stesso EAN su un marketplace vero.
                    altrui = [u for u, pid in vive.get(codice, ())
                              if pid != prodotto.id]
                    if altrui:
                        conteggi["gia_vive"] += 1
                        impedimenti.append(_(
                            "il codice a barre %s è già dell'offerta viva %s "
                            "su Kaufland: non se ne crea una seconda sullo "
                            "stesso codice finché non si sa di chi è quella."
                        ) % (codice, ", ".join(altrui)))
                    # b) il gemello dentro Odoo.
                    gemelli = [p for p in condivisi.get(codice, ())
                               if p.id != prodotto.id]
                    if gemelli:
                        conteggi["gemelli"] += 1
                        impedimenti.append(_(
                            "il codice a barre %s è condiviso con %s: due "
                            "prodotti Odoo con lo stesso codice non possono "
                            "avere due offerte su Kaufland, e quale dei due "
                            "vende lo decide una persona, non noi."
                        ) % (codice, self._elenco_nomi(gemelli)))
                    if impedimenti:
                        valori["ultimo_esito"] = "errore"
                        # ⚠️ Il dettaglio della risposta non si butta: se
                        # Kaufland ha anche sbagliato, quel testo e' l'unica
                        # cosa che dice cosa.
                        motivo = " ".join(impedimenti + ([motivo] if motivo
                                                         else []))

                # ⚠️ Il messaggio NON si sovrascrive se la riga porta gia' un
                # errore: su una riga contesa (`_segna_contesa`) o su
                # un'offerta orfana quel testo e' l'UNICA cosa interrogabile
                # per prodotto che dice perche' quel prodotto non e'
                # agganciato, e una rete caduta lo cancellerebbe proprio nel
                # momento peggiore. L'errore nuovo non si perde: va nel log di
                # sistema, e lo stato letto e' comunque sulla riga.
                if motivo and not (riga and riga.ultimo_esito == "errore"):
                    valori["ultimo_messaggio"] = motivo
                elif motivo:
                    _logger.warning(
                        "Kaufland ricognizione: «%s» porta già un errore, il "
                        "nuovo NON lo sostituisce — %s",
                        prodotto.display_name, motivo)

                if riga:
                    riga.write(valori)
                else:
                    Offerta.create(dict(valori, market_id=self._riga_mercato().id,
                                        product_id=prodotto.id))
        except Exception as errore:
            # ⚠️ 549 prodotti sono 549 chiamate in fila dentro una sola
            # richiesta RPC: si sta sopra il `limit_time_real` di un worker
            # Odoo. Se il worker viene ucciso o qualcosa esplode a meta', la
            # transazione torna indietro per intero — nessuna riga, e nemmeno
            # la riga di registro, che si scrive solo alla fine. Il log di
            # sistema e' l'unica cosa che il rollback non tocca, ed e' la
            # stessa cura che il riaggancio ha sulla lettura interrotta.
            _logger.error(
                "Kaufland ricognizione sul canale %s: fermata al prodotto %s "
                "di %s — %s: %s", self.channel.display_name, fatti, totale,
                type(errore).__name__, errore)
            raise

        # Quel che non si è MAI guardato, nemmeno nei giri precedenti: il
        # tetto ha fermato questo giro prima di arrivarci.
        conteggi["da_guardare"] = len(mai_guardati - raggiunti)
        return self._chiudi_ricognizione(conteggi, fermata)

    def _chiudi_ricognizione(self, conteggi, fermata=None):
        """Tira le somme della ricognizione e lascia una riga nel registro.

        ⚠️ Niente verde su un lavoro parziale, e qui i modi di essere parziali
        sono sette: nessun prodotto guardato, prodotti per cui Kaufland non
        ha risposto, prodotti etichettati che non si sono nemmeno potuti
        guardare, prodotti il cui codice a barre e' gia' di un'offerta viva,
        prodotti che il codice a barre se lo contendono fra loro, doppioni
        gia' vivi su Kaufland, e prodotti che non sono MAI stati guardati.
        Tutti lasciano il quadro incompleto, e un quadro incompleto letto come
        completo porta a scrivere schede che non servivano, a non scriverne di
        necessarie, o a mettere in vendita due volte lo stesso codice.

        ⚠️ E la regola sta QUI, in un posto solo: `conteggi["completo"]` e'
        cio' che il bottone legge per decidere il colore della notifica.
        """
        # ⚠️ I quattro stati sono i secchi, e devono sommare a `guardati`: la
        # lezione del riaggancio (un prodotto che non finisce in nessun secchio
        # e' un prodotto di cui non sappiamo dire niente). `gia_vive` NON e' un
        # quinto secchio: e' trasversale — un prodotto gia' in vendita ha
        # comunque il suo stato di scheda — e per questo si conta a parte.
        somma = (conteggi["pronte"] + conteggi["gusci"] + conteggi["assenti"]
                 + conteggi["sconosciuti"])
        quadra = somma == conteggi["guardati"]

        messaggio = _(
            "Guardati %(guardati)s prodotti: %(pronte)s con la scheda pronta, "
            "%(gusci)s gusci, %(assenti)s assenti, %(sconosciuti)s senza "
            "risposta.") % conteggi
        if not conteggi["guardati"]:
            # ⚠️ Lo stesso difetto che il riaggancio ha gia' chiuso: zero
            # righe guardate NON significa «tutto a posto, niente da fare».
            messaggio += _(
                "\n⚠️ Nessun prodotto guardato. Le cause tipiche sono "
                "l'etichetta indicata sul canale che non è quella messa sui "
                "prodotti, i prodotti di un'altra azienda, o l'assenza del "
                "codice a barre.")
        if not quadra:
            messaggio += _(
                "\n⚠️ I conti non tornano: %s prodotti guardati non sono "
                "finiti in nessuno stato. La ricognizione non è affidabile."
            ) % (conteggi["guardati"] - somma)
        if conteggi["sconosciuti"]:
            messaggio += _(
                "\n⚠️ Per %s prodotti Kaufland non ha risposto: di quelli NON "
                "sappiamo se la scheda ci sia. «Non lo so» non è «serve la "
                "scheda»: il quadro è incompleto e la ricognizione va "
                "ripetuta. Il motivo di ciascuno è sulla riga del prodotto."
            ) % conteggi["sconosciuti"]
        if conteggi["gia_vive"]:
            messaggio += _(
                "\n⚠️ %s prodotti hanno il codice a barre di un'offerta già "
                "viva su Kaufland che non risulta loro: NON sono creabili, "
                "sono segnati in errore, e vanno risolti a mano (di solito il "
                "codice interno del prodotto è cambiato dopo la creazione "
                "dell'offerta). Crearne una seconda metterebbe in vendita due "
                "volte lo stesso codice.") % conteggi["gia_vive"]
        if conteggi["gemelli"]:
            messaggio += _(
                "\n⚠️ %s prodotti hanno un codice a barre condiviso con un "
                "altro prodotto Odoo: NON sono creabili, sono segnati in "
                "errore, e va deciso a mano quale dei due va in vendita su "
                "Kaufland. Crearli entrambi metterebbe in vendita due volte "
                "lo stesso codice.") % conteggi["gemelli"]
        if conteggi["senza_barcode"]:
            messaggio += _(
                "\n⚠️ %s prodotti etichettati non hanno il codice a barre: su "
                "Kaufland la scheda si cerca per EAN, quindi non sono stati "
                "guardati e non andranno mai in vendita finché il codice "
                "manca.") % conteggi["senza_barcode"]
        if conteggi["doppioni"]:
            messaggio += _(
                "\n⚠️ %s prodotti già agganciati alla loro offerta hanno il "
                "codice a barre di ALTRE offerte vive su Kaufland: il "
                "doppione esiste già là fuori e va sciolto sul portale. Le "
                "loro righe non sono state toccate — quelle offerte vendono "
                "regolarmente — ma finché il doppione c'è, il quadro non è "
                "pulito e questa ricognizione non può dirsi riuscita."
            ) % conteggi["doppioni"]
        if conteggi["da_guardare"]:
            messaggio += _(
                "\nRestano %s prodotti mai guardati nemmeno una volta: il "
                "giro NON ha finito. Si ripete il comando finché non ne "
                "resta nessuno — ogni giro riprende da dove si era fermato, "
                "quindi il numero scende a ogni passaggio."
            ) % conteggi["da_guardare"]
        if fermata:
            messaggio += "\n⚠️ " + fermata
        completo = (bool(conteggi["guardati"]) and quadra
                    and not conteggi["sconosciuti"]
                    and not conteggi["gia_vive"]
                    and not conteggi["gemelli"]
                    and not conteggi["senza_barcode"]
                    and not conteggi["doppioni"]
                    and not conteggi["da_guardare"])
        # ⚠️ LA REGOLA DEL VERDE E' QUESTA, e sta scritta UNA VOLTA SOLA. Il
        # bottone a video la leggeva riscrivendola a mano, e le due copie
        # erano gia' divergenti alla nascita: la notifica poteva uscire verde
        # mentre il registro diceva «errore». Chi la legge la legge da qui.
        conteggi["completo"] = completo
        if not completo:
            _logger.warning("Kaufland ricognizione sul canale %s: %s",
                            self.channel.display_name, messaggio)
        self._registra("kaufland_ricognizione",
                       "success" if completo else "error", messaggio)
        return conteggi

    # ------------------------------------------------------------------
    # La creazione delle offerte — l'unica cosa che SCRIVE su Kaufland
    # ------------------------------------------------------------------
    def _candidate(self):
        """Il dominio delle righe che si possono mettere in vendita.

        ⚠️ Le quattro condizioni sono TUTTE necessarie, e la quarta e' quella
        che si dimentica: `ultimo_esito != "errore"`.

        La ricognizione e il riaggancio marcano in errore le righe che NON
        vanno create, e sono tre famiglie, tutte e tre gia' incontrate sul
        vero: le contese (due offerte vive si rivendicano lo stesso
        prodotto), i prodotti il cui codice a barre e' gia' di un'offerta
        viva che non e' loro, e i gemelli (due prodotti Odoo con lo stesso
        codice a barre). Senza questa riga quel marchio non ferma nessuno:
        resta solo il cancello del riaggancio, che dice «il quadro e'
        completo», non «questa riga si puo' creare». Le due difese sono
        meta' ciascuna.

        ⚠️ In Odoo `!=` comprende anche i valori nulli, quindi le righe
        appena nate (`ultimo_esito` vuoto) restano candidate: e' voluto. E
        «saltato» resta candidato: un prezzo mancante e' un dato da
        sistemare, non un divieto.
        """
        return [("market_id", "=", self._riga_mercato().id),
                ("stato_scheda", "=", "pronta"),
                ("id_unit", "=", False),
                ("ultimo_esito", "!=", "errore")]


    def _prendi_il_turno(self, giro=None, conseguenza=None):
        """Un solo giro per volta su questo canale.

        ⚠️ I due parametri servono soltanto a DIRE LA VERITA' nel messaggio:
        il turno e' unico per canale e vale per la creazione come per
        l'allineamento (che legge proprio gli `id_unit` che la creazione
        scrive). Il valore predefinito e' quello della creazione: il
        COMPORTAMENTO gia' provato non cambia. Il testo a video si', di poco
        («Un altro giro (creazione delle offerte) e' gia' in corso...»), e
        con esso la stringa da tradurre.

        ⚠️ Il cancello si legge all'inizio e si riscrive alla fine, e fra i
        due momenti passa piu' di un minuto in cui a video non succede
        niente. Un secondo clic da un'altra scheda del browser — o dopo un
        ricaricamento — e' il comportamento naturale di chi non vede
        succedere niente: due giri sovrapposti leggono le STESSE candidate e
        fanno due `POST /units/` per lo stesso prodotto. Doppioni veri, su un
        marketplace vero.

        Il `FOR UPDATE NOWAIT` sulla riga del canale serializza le
        invocazioni. `NOWAIT` e non un'attesa: una richiesta web che aspetta
        il turno finirebbe uccisa dal `limit_time_real` senza spiegazioni.

        ⚠️ IL SAVEPOINT E' NECESSARIO, non decorativo: un'istruzione SQL che
        fallisce lascia la transazione ABORTITA, e da li' in poi anche
        leggere `display_name` per comporre il messaggio d'errore
        esploderebbe con un `InFailedSqlTransaction` grezzo al posto della
        UserError chiara.

        ⚠️ E NON OGNI GUASTO E' «UN ALTRO GIRO IN CORSO». Odoo apre i cursori
        in REPEATABLE READ: un `FOR UPDATE` su una riga canale aggiornata da
        un'altra transazione gia' committata (il cron del feed catalogo, per
        dirne una) da' un errore di SERIALIZZAZIONE, non un lock occupato.
        Tradurlo in «un altro giro e' in corso» direbbe una cosa falsa e
        sopprimerebbe il ritentativo che Odoo fa da solo: si lascia risalire
        tutto cio' che non e' esattamente `lock_not_available`.
        """
        try:
            with self.env.cr.savepoint():
                # Il nome della tabella viene dal modello, non scritto a mano.
                self.env.cr.execute(  # noqa: S608 - `_table` e' interno
                    "SELECT id FROM %s WHERE id = %%s FOR UPDATE NOWAIT"
                    % self.channel._table, (self.channel.id,))
        except Exception as errore:  # noqa: BLE001
            if getattr(errore, "pgcode", None) != LOCK_OCCUPATO:
                raise
            _logger.warning(
                "Kaufland %s sul canale %s: turno non disponibile — %s",
                giro or _("creazione"), self.channel.display_name, errore)
            raise UserError(_(
                "Un altro giro (%(giro)s) è già in corso su questo canale. "
                "Non se ne avvia un secondo: %(conseguenza)s. Attendere che "
                "il primo finisca.") % {
                    "giro": giro or _("creazione delle offerte"),
                    "conseguenza": conseguenza or _(
                        "due giri sovrapposti creerebbero le stesse offerte "
                        "due volte su Kaufland")})

    @staticmethod
    def _motivo_stato(stato, dettaglio):
        """`HTTP <stato>: <dettaglio>`, la forma di TUTTI i rifiuti.

        Sta a parte perche' l'allineamento riceve gli stati riga per riga
        dentro la risposta di gruppo, dove non c'e' nessun oggetto risposta
        da cui leggerli: la forma del motivo deve restare una sola, o due
        giri diversi raggrupperebbero in due modi lo stesso guasto.
        """
        pulito = (dettaglio or "").strip()
        return _("HTTP %s: %s") % (stato, pulito or _("nessun dettaglio"))

    @classmethod
    def _motivo_http(cls, risposta):
        """Il motivo di un rifiuto, con lo stato HTTP davanti.

        ⚠️ Senza il prefisso, un rifiuto a corpo vuoto lascia un motivo
        VUOTO: nel registro si leggerebbe «Motivi dei rifiuti: 1 × » e lo
        stato non comparirebbe da nessuna parte. E' informazione che abbiamo
        gia' in mano, e serve anche a distinguere un 400 (colpa del dato) da
        un 502 (colpa del trasporto, ed esito ignoto).
        """
        return cls._motivo_stato(risposta.stato, risposta.messaggio)

    @staticmethod
    def _nome(prodotto):
        """Il nome del prodotto, anche quando leggerlo non si puo'.

        Serve dentro i gestori d'errore: li' si sta gia' rimediando a un
        guasto, e una seconda eccezione mentre si compone il messaggio
        porterebbe via tutto.
        """
        try:
            return prodotto.display_name
        except Exception:  # noqa: BLE001
            return _("(prodotto non leggibile)")

    def _al_riparo(self, azione, *argomenti):
        """Esegue una scrittura dentro un savepoint, e non solleva mai.

        ⚠️ Un `try/except` NON basta. In PostgreSQL un errore vero — un
        `IntegrityError` da uno dei due `unique` — mette la transazione in
        stato ABORTITO: da li' in poi ogni istruzione fallisce e il commit
        finale della richiesta diventa un ROLLBACK silenzioso. Intercettare
        l'eccezione non salva gli `id_unit` gia' scritti: si perdono lo
        stesso, e in piu' a video comparirebbe una notifica gialla che dice
        «create: 3» mentre in banca dati non e' rimasto niente. Solo il
        savepoint riporta la transazione a uno stato utilizzabile.
        """
        try:
            with self.env.cr.savepoint():
                azione(*argomenti)
            return True
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Kaufland sul canale %s: una scrittura di servizio è "
                "fallita ed è stata annullata.", self.channel.display_name)
            return False

    @staticmethod
    def _se_non_era_nata():
        """Cosa fare quando il riaggancio NON ritrova l'offerta incerta.

        ⚠️ «Va RILETTA con Riaggancia» risolve meta' dei casi e tace
        sull'altra meta'. Se l'offerta ERA nata, il riaggancio la trova, le
        scrive l'identificativo e la riga torna a posto. Se NON era nata, il
        riaggancio non trova niente: la riga resta in errore PER SEMPRE, esce
        dalle candidate PER SEMPRE, e quel prodotto non andra' mai in vendita
        senza che nessuno lo dica — mentre l'operatore che ha seguito
        l'istruzione crede di aver chiuso la faccenda. Il silenzio e' il
        difetto: la riga esiste, e' verde di nessun colore, e non protesta.
        """
        return _(
            " ⚠️ E se il riaggancio NON la trova, vuol dire che l'offerta "
            "non era nata: allora la riga resta segnata in errore ed esce "
            "dalle candidate per sempre. Per rimetterla in gioco va aperta "
            "la sua riga in «Offerte Kaufland» e svuotato «Ultimo esito» "
            "(serve un amministratore); al giro dopo la creazione la "
            "riprende.")

    def _marca_incerta(self, riga, motivo, esterno=None):
        """Marca la riga e lascia una traccia, senza MAI sollevare.

        ⚠️ Il marchio d'errore sulla riga NON e' cosmetico: e' cio' che la
        toglie dal dominio delle candidate. Senza, il giro dopo riproverebbe
        alla cieca la stessa creazione, e se l'offerta era nata ne farebbe
        una seconda sullo stesso codice a barre.

        ⚠️ E siccome il marchio e' PER SEMPRE, il messaggio deve dire anche
        come si toglie: vedi `_se_non_era_nata`. Sta qui, e non nei due
        chiamanti, perche' tutti e due lasciano una riga incerta e tutti e
        due hanno lo stesso caso negativo.
        """
        motivo += self._se_non_era_nata()
        _logger.error("Kaufland creazione sul canale %s: %s",
                      self.channel.display_name, motivo)
        self._al_riparo(riga.write, {"ultimo_esito": "errore",
                                     "ultimo_messaggio": motivo})
        self._al_riparo(self._registra, "kaufland_crea_offerta", "error",
                        motivo, None, esterno or False)
        return motivo

    def _interrompi(self, riga, prodotto, causa, esterno=None):
        """L'esito di questa creazione e' IGNOTO: si marca e ci si ferma."""
        return self._marca_incerta(riga, _(
            "Esito IGNOTO: %(causa)s. L'offerta di «%(prodotto)s» POTREBBE "
            "essere già stata creata su Kaufland: non si riprova, perché un "
            "secondo tentativo la duplicherebbe. Va RILETTA con «Riaggancia "
            "le offerte esistenti», che è lo strumento che rilegge quel che "
            "c'è davvero su Kaufland.")
            % {"causa": causa, "prodotto": self._nome(prodotto)}, esterno)

    def _causa_incerta(self, risposta):
        """Perche' di questa risposta non si puo' dire se l'offerta sia nata.

        ⚠️ Due casi, e il secondo e' quello che si dimentica:

        - stato `0`: `TrasportoRequests` non ha ricevuto risposta (rete
          caduta, tempo scaduto). La richiesta puo' essere arrivata lo
          stesso.
        - **stato 5xx**: 500, 502, 503, 504. Vuol dire ESATTAMENTE la stessa
          cosa — la richiesta puo' essere arrivata a Kaufland e l'offerta
          puo' essere nata. Trattarlo come un rifiuto certo farebbe
          proseguire il giro col cancello aperto, e la riga si leggerebbe
          «rifiutata da Kaufland», cioe' un invito a correggere il dato e
          RIPROVARE. Qui il trasporto sta dietro il proxy Traefik di Coolify:
          i 502 e i 504 non sono un'ipotesi di scuola.
        """
        if risposta.stato == 0:
            return _("la risposta si è persa per strada (%s)") % (
                (risposta.messaggio or "").strip() or _("nessun dettaglio"))
        return _("Kaufland ha risposto %s, cioè un guasto del suo lato o del "
                 "proxy che gli sta davanti") % self._motivo_http(risposta)

    def _interrompi_odoo(self, riga, prodotto, errore, esterno=None):
        """Kaufland ha risposto, ma Odoo non e' riuscito a registrare l'esito.

        ⚠️ Il pre-controllo dei due vincoli unique chiude i due casi
        PREVISTI, non la classe. Qualunque altro errore dopo la risposta
        annullerebbe l'intera transazione e con essa TUTTI gli `id_unit` del
        giro: e' letteralmente il meccanismo con cui sono nate le 165 orfane.
        Il savepoint per riga ha gia' rimesso in piedi la transazione; qui si
        marca, ci si ferma e si richiude il cancello.
        """
        _logger.exception(
            "Kaufland creazione sul canale %s: scrittura Odoo fallita dopo "
            "la risposta di Kaufland", self.channel.display_name)
        return self._marca_incerta(riga, _(
            "Kaufland ha risposto, ma Odoo non è riuscito a registrare "
            "l'esito di «%(prodotto)s» (%(tipo)s: %(errore)s). L'offerta "
            "POTREBBE essere viva su Kaufland e senza identificativo qui: va "
            "RILETTA con «Riaggancia le offerte esistenti», non ritentata.")
            % {"prodotto": self._nome(prodotto),
               "tipo": type(errore).__name__, "errore": errore}, esterno)

    def crea_offerte(self, limite=None):
        """Crea su Kaufland le offerte che mancano.

        Si creano SOLO i prodotti che hanno tutte queste cose: la scheda
        `pronta`, nessun `id_unit`, nessun marchio d'errore addosso, un
        codice a barre coerente con la ricognizione, un riferimento interno e
        un prezzo positivo.

        `limite` serve alla prima prova sul vero: si parte da UN prodotto, e
        conta le CHIAMATE, non le righe esaminate.

        ⚠️ Da qui in poi si scrive su un marketplace vero, e non si annulla.
        Quattro regole governano tutto il metodo:

        1. **Un solo giro per volta** (`_prendi_il_turno`).
        2. **Ogni riga sta dentro il suo savepoint.** Non basta intercettare:
           un errore vero del database lascia la transazione ABORTITA, e da
           li' in poi si perde tutto quel che era stato scritto prima — cioe'
           gli `id_unit` delle offerte gia' create, che restano vive su
           Kaufland e irraggiungibili da Odoo. Il savepoint e' l'unica cosa
           che le salva, ed e' lo stesso strumento che usa il riaggancio.
        3. **Ogni offerta che POTREBBE essere nata lascia una traccia** e
           richiude il cancello del riaggancio.
        4. **Non si riprova alla cieca** su nulla di incerto.
        """
        # ⚠️ Guardia 0 — il turno. Prima di tutto: se un altro giro e' in
        # corso, tutto il resto sarebbe una lettura di dati che cambiano.
        self._prendi_il_turno()

        # ⚠️ Guardia 1 — il riaggancio. Su Kaufland ci sono gia' offerte vive:
        # creare senza sapere quali esistono le duplica.
        if not self._riga_mercato().riagganciato:
            raise UserError(_(
                "Prima di creare offerte va eseguito «Riaggancia le offerte "
                "esistenti» su questo canale: su Kaufland ci sono già offerte "
                "vive, e crearne di nuove senza saperlo le duplicherebbe."))

        # ⚠️ Guardia 1-bis — E LA FOTOGRAFIA DEVE ESSERE DI OGGI. Il cancello
        # dice «so quali offerte esistono su Kaufland»: e' un fatto sul mondo
        # fuori da Odoo, e invecchia da solo. Vedi `VALIDITA_CANCELLO_ORE`.
        ore = self._eta_cancello(self._riga_mercato())
        if ore is None or ore > VALIDITA_CANCELLO_ORE:
            raise UserError(_(
                "Il riaggancio di questo canale %(quando)s: la fotografia "
                "delle offerte vive su Kaufland vale %(validita)s ore, e più "
                "in là non autorizza più a creare — là fuori le offerte "
                "possono essere cambiate senza che Odoo lo sappia. Eseguire "
                "di nuovo «Riaggancia le offerte esistenti»: è una lettura, "
                "non scrive niente su Kaufland.") % {
                    "quando": _("non ha una data")
                    if ore is None else _("è di %s ore fa") % int(ore),
                    "validita": VALIDITA_CANCELLO_ORE})

        mercato = self._mercato()
        canale = self.channel.sudo()
        gruppo = (self._riga_mercato().shipping_group_id or "").strip()
        # ⚠️ Guardia 2 — Kaufland rifiuta le offerte senza gruppo di
        # spedizione, e il gruppo e' PER MERCATO.
        if not gruppo:
            raise UserError(_(
                "Sul canale «%s» manca il gruppo di spedizione: Kaufland "
                "rifiuterebbe ogni offerta.") % self.channel.display_name)

        client = self._client()
        Offerta = self.env["kaufland.offer"].sudo()
        # ⚠️ Guardia 3 — senza listino ogni prodotto risulterebbe «senza
        # prezzo»: N righe marcate «saltato» con un messaggio che incolpa i
        # prodotti invece della configurazione. E' configurazione mancante, e
        # si dice prima di sporcare qualcosa. Il listino e' PER MERCATO.
        listino = self._listino_del_mercato()

        dominio = self._candidate()
        # ⚠️ Nessun `limit` sulla ricerca: il tetto conta le chiamate, e
        # tagliare qui le righe rimetterebbe in piedi il livelock delle
        # saltate (vedi MAX_PER_GIRO).
        candidate = Offerta.search(dominio)
        tetto = min(limite, MAX_PER_GIRO) if limite else MAX_PER_GIRO

        esito = {"create": 0, "saltate": 0, "fallite": 0,
                 "senza_identificativo": 0, "interrotte": 0}
        motivi = {}

        def conta(chiave):
            """Incrementa un contatore e dice QUALE, per poterlo disfare.

            ⚠️ Serve perche' i rami che finiscono con `continue` contano
            DENTRO il savepoint: se la scrittura che li giustifica viene
            annullata all'uscita (in Odoo il flush avviene li', non alla riga
            che ha chiamato `write`), il gestore conterebbe una seconda volta
            la stessa riga — «saltate: 2» dove le righe sono una. Dove il
            `continue` non c'e' (la creazione riuscita) il contatore sta gia'
            FUORI dal savepoint, che e' la forma migliore.
            """
            esito[chiave] += 1
            return chiave

        fermata = None
        chiamate = 0
        ultimo_rifiuto = None
        rifiuti_di_fila = 0
        rifiutata_prima = False
        partenza = time.monotonic()

        for riga in candidate:
            # ⚠️ La striscia dei rifiuti identici si AZZERA appena una riga
            # va diversamente. Senza, la sequenza «rifiuto A, successo,
            # rifiuto A, …» arriverebbe a cinque e fermerebbe il giro
            # dichiarando «è la configurazione del canale» mentre quattro
            # offerte sono nate benissimo.
            if not rifiutata_prima:
                ultimo_rifiuto, rifiuti_di_fila = None, 0
            rifiutata_prima = False

            if chiamate >= tetto:
                fermata = _(
                    "Il giro si è fermato al tetto di %s offerte create per "
                    "volta, per non farsi uccidere dal limite di tempo del "
                    "worker (che annullerebbe anche le offerte già create). "
                    "Ripetere per continuare.") % tetto
                break
            # ⚠️ Il tempo si guarda solo dopo la prima chiamata: altrimenti
            # un canale lento non creerebbe mai niente.
            if chiamate and time.monotonic() - partenza > SECONDI_PER_GIRO:
                fermata = _(
                    "Il giro si è fermato da solo dopo %s secondi per non "
                    "farsi uccidere dal limite di tempo del worker (che "
                    "annullerebbe anche le offerte già create). Ripetere per "
                    "continuare.") % SECONDI_PER_GIRO
                break

            prodotto = None
            riferimento = None
            partita = False
            ricevuta = False
            creata_ora = False
            conteggio = None
            motivo_contato = None
            try:
                # ⚠️ IL SAVEPOINT COPRE TUTTA LA RIGA, prima e dopo la
                # chiamata. Anche le vie «saltato» scrivono sulla riga, e una
                # scrittura che rompe il database la' annullerebbe il giro
                # esattamente come una che rompe dopo. Gli inneschi sono
                # ordinari: un listino in valuta senza cambio del giorno fa
                # sollevare `_pricelist_price`, un prodotto cancellato in
                # concorrenza da' MissingError.
                with self.env.cr.savepoint():
                    prodotto = riga.product_id
                    if not prodotto:
                        # Una riga senza prodotto non ha niente da mettere in
                        # vendita. Non dovrebbe arrivare fin qui, ma qui si
                        # sta per scrivere su un marketplace vero.
                        riga.write({"ultimo_esito": "saltato",
                                    "ultimo_messaggio": _(
                                        "Riga senza prodotto in Odoo: non c'è "
                                        "niente da mettere in vendita.")})
                        conteggio = conta("saltate")
                        continue

                    # ⚠️ In Odoo un campo `Char` non valorizzato si legge
                    # `False`, non `None` e non stringa vuota:
                    # `prodotto.barcode` e `prodotto.default_code` finirebbero
                    # TALI E QUALI nel corpo JSON (`"ean": false`), e Kaufland
                    # rifiuterebbe senza dire di quale prodotto sta parlando.
                    codice_riga = (riga.ean or "").strip()
                    codice_prodotto = (prodotto.barcode or "").strip()
                    riferimento = (prodotto.default_code or "").strip()

                    # ⚠️ L'EAN sulla riga e' quello che la RICOGNIZIONE ha
                    # guardato; quello sul prodotto e' quello di ADESSO. Se
                    # non coincidono, il codice a barre e' cambiato dopo
                    # l'ultima ricognizione e nessuno dei due e' affidabile:
                    # col vecchio ci si aggancerebbe alla scheda Kaufland di
                    # un ALTRO articolo, col nuovo si userebbe un verdetto
                    # («pronta») che riguardava un altro codice.
                    #
                    # ⚠️ «Non coincidono» comprende il caso in cui il codice
                    # sia stato TOLTO: la ricognizione scrive sempre `ean`
                    # insieme a `stato_scheda`, quindi «riga con EAN +
                    # prodotto senza codice» significa SEMPRE codice tolto
                    # dopo, e creare l'offerta la aggancerebbe a un codice
                    # che il prodotto non dichiara piu'.
                    if codice_riga and codice_riga != codice_prodotto:
                        riga.write({"ultimo_esito": "saltato",
                                    "ultimo_messaggio": _(
                                        "Il codice a barre di «%(prodotto)s» "
                                        "ora è %(nuovo)s, ma la ricognizione "
                                        "aveva guardato %(vecchio)s: è "
                                        "cambiato dopo, e il verdetto «scheda "
                                        "pronta» non riguarda più questo "
                                        "codice. Rifare «Guarda le schede su "
                                        "Kaufland» prima di creare.")
                                    % {"prodotto": prodotto.display_name,
                                       "nuovo": codice_prodotto
                                       or _("assente"),
                                       "vecchio": codice_riga}})
                        conteggio = conta("saltate")
                        continue

                    ean = codice_riga or codice_prodotto
                    if not ean or not riferimento:
                        manca = _("il codice a barre") if not ean else ""
                        if not riferimento:
                            manca = (manca + _(" e ") if manca else "") + _(
                                "il riferimento interno")
                        riga.write({"ultimo_esito": "saltato",
                                    "ultimo_messaggio": _(
                                        "A «%(prodotto)s» manca %(manca)s: "
                                        "Kaufland rifiuterebbe l'offerta, e "
                                        "senza il riferimento interno il "
                                        "riaggancio non saprebbe mai "
                                        "ritrovarla.")
                                    % {"prodotto": prodotto.display_name,
                                       "manca": manca}})
                        conteggio = conta("saltate")
                        continue

                    prezzo = self._pricelist_price(listino, prodotto)
                    if not prezzo or float(prezzo) <= 0:
                        # ⚠️ Guardia 4 — un prezzo a zero e' un dato
                        # mancante, non un'offerta gratis.
                        riga.write({"ultimo_esito": "saltato",
                                    "ultimo_messaggio": _(
                                        "Senza prezzo nel listino di "
                                        "vendita.")})
                        conteggio = conta("saltate")
                        continue

                    try:
                        # ⚠️ `in_centesimi` rende un INTERO e arrotonda:
                        # `corpo_offerta` rifiuta i numeri a virgola apposta,
                        # perche' `prezzo * 100` perde un centesimo. E
                        # `storefront` NON entra qui dentro: va nella stringa
                        # di ricerca dell'indirizzo, e nel corpo Kaufland
                        # rifiuta con due errori che sembrano contraddirsi
                        # (misurato su 166 offerte rifiutate su 166).
                        corpo = corpo_offerta(
                            ean=ean,
                            prezzo_centesimi=in_centesimi(prezzo),
                            quantita=self._available_quantity(prodotto,
                                                              canale),
                            id_offer=riferimento,
                            id_gruppo_spedizione=gruppo,
                            giorni_consegna=self._giorni_lavorazione(prodotto),
                            id_magazzino=self._riga_mercato().warehouse_id,
                        )
                    except ValueError as errore:
                        # ⚠️ Per PRODOTTO: un prezzo che arrotonda a zero
                        # (0,004 €) non deve far cadere il giro.
                        riga.write({"ultimo_esito": "saltato",
                                    "ultimo_messaggio": str(errore)})
                        conteggio = conta("saltate")
                        continue

                    chiamate += 1
                    partita = True
                    risposta = client.chiama(
                        "POST", "%s?storefront=%s" % (API_UNITA, mercato),
                        corpo)
                    ricevuta = True

                    if not risposta.ok:
                        # ⚠️ QUI SI DECIDE SE SI PUO' RIPROVARE, ed e' il
                        # punto su cui si creano i doppioni.
                        #
                        #  - stato `0` o **5xx**: non si sa se l'offerta sia
                        #    nata. Ci si FERMA e si dice che va RILETTA.
                        #  - 401/403: l'offerta NON e' nata, e non e' colpa
                        #    della riga: si segna «saltato» (non «errore»,
                        #    che la escluderebbe per sempre). NON si ferma il
                        #    giro sulla prima: un 403 di categoria o marchio
                        #    riguarda UNA offerta, e fermarsi la' terrebbe il
                        #    canale in ostaggio per sempre — quella riga
                        #    resta candidata e prima in ordine. Se invece e'
                        #    sistemico lo dice la striscia, qui sotto.
                        #  - ogni altro 4xx: Kaufland ha detto di no sul
                        #    dato. L'offerta NON e' nata: si va avanti.
                        if risposta.stato == 0 or risposta.stato >= 500:
                            conteggio = conta("interrotte")
                            fermata = self._interrompi(
                                riga, prodotto, self._causa_incerta(risposta),
                                riferimento)
                            break

                        motivo = self._motivo_http(risposta)
                        if risposta.stato in (401, 403):
                            riga.write({"ultimo_esito": "saltato",
                                        "ultimo_messaggio": _(
                                            "%(motivo)s — Kaufland ha "
                                            "rifiutato le credenziali o il "
                                            "permesso per «%(prodotto)s». "
                                            "Può essere la configurazione del "
                                            "canale, oppure la categoria o il "
                                            "marchio non autorizzati per "
                                            "QUESTO prodotto.")
                                        % {"motivo": motivo,
                                           "prodotto": prodotto.display_name}})
                            conteggio = conta("saltate")
                        else:
                            riga.write({"ultimo_esito": "errore",
                                        "ultimo_messaggio": motivo})
                            conteggio = conta("fallite")
                            motivi[motivo] = motivi.get(motivo, 0) + 1
                            motivo_contato = motivo
                        self._registra("kaufland_crea_offerta", "error",
                                       motivo, payload=str(corpo),
                                       external_id=riferimento)

                        if risposta.troppe_chiamate:
                            # Il tetto e' 111 chiamate al secondo per
                            # venditore: insistere peggiora la situazione.
                            fermata = _(
                                "Fermato dal limite di chiamate di Kaufland: "
                                "insistere peggiora. Riprovare fra qualche "
                                "minuto.")
                            break
                        # ⚠️ La striscia: se il motivo e' sempre lo stesso
                        # non e' il prodotto, e' la configurazione — e ogni
                        # giro in piu' marca righe che nessuno strumento
                        # rimette dentro.
                        rifiuti_di_fila = (rifiuti_di_fila + 1
                                           if motivo == ultimo_rifiuto else 1)
                        ultimo_rifiuto = motivo
                        rifiutata_prima = True
                        if rifiuti_di_fila >= RIFIUTI_UGUALI_DI_FILA:
                            fermata = _(
                                "%(quante)s offerte di fila rifiutate con lo "
                                "STESSO motivo (%(motivo)s): non è un "
                                "problema dei singoli prodotti, è la "
                                "configurazione del canale.%(extra)s Ci si "
                                "ferma prima di segnare tutte le altre."
                            ) % {"quante": rifiuti_di_fila, "motivo": motivo,
                                 "extra": _(" Sono le credenziali o i "
                                            "permessi del canale.")
                                 if risposta.stato in (401, 403) else ""}
                            break
                        continue

                    # ⚠️ Kaufland puo' accettare rispondendo SENZA
                    # l'intestazione `Location`: e' successo il 2026-08-25 su
                    # 165 offerte, rimaste vive e non aggiornabili. Si guarda
                    # anche nel corpo, in tutte e due le forme: dentro
                    # l'involucro `data` e al primo livello — la seconda
                    # chiamata passa intestazioni vuote perche' `Location` e'
                    # gia' stata provata.
                    identificativo = self._testo_id(
                        leggi_id_unit(risposta.teste, risposta.dati)
                        or leggi_id_unit({}, risposta.corpo))
                    if not identificativo:
                        # ⚠️ L'offerta E' NATA su Kaufland, ma non ne
                        # conosciamo l'identificativo: e' viva e non
                        # aggiornabile. Non si conta fra le create, e non si
                        # scrive un id_unit finto.
                        riga.write({"ultimo_esito": "errore",
                                    "ultimo_messaggio": _(
                                        "Offerta creata su Kaufland ma senza "
                                        "identificativo: né in Location né "
                                        "nel corpo. Rieseguire «Riaggancia "
                                        "le offerte esistenti».")})
                        conteggio = conta("senza_identificativo")
                        self._registra("kaufland_crea_offerta", "error",
                                       _("Creata senza identificativo"),
                                       payload=str(risposta.teste),
                                       external_id=riferimento)
                        continue

                    # ⚠️ I due vincoli unique arrivano dal DATABASE. Si
                    # guarda prima, come fa gia' il riaggancio; e cio' che il
                    # pre-controllo non prevede lo raccoglie il savepoint.
                    altra = Offerta.search([
                        ("market_id", "=", self._riga_mercato().id),
                        ("id_unit", "=", identificativo)], limit=1)
                    if altra and altra.id != riga.id:
                        motivo = _(
                            "Offerta creata su Kaufland con l'identificativo "
                            "%s, che però risulta già di un'altra riga di "
                            "questo canale: l'identificativo non si è potuto "
                            "registrare qui. Rieseguire «Riaggancia le "
                            "offerte esistenti».") % identificativo
                        riga.write({"ultimo_esito": "errore",
                                    "ultimo_messaggio": motivo})
                        conteggio = conta("senza_identificativo")
                        self._registra("kaufland_crea_offerta", "error",
                                       motivo, external_id=riferimento)
                        continue

                    # ⚠️ Anche qui si scriveva in `centrivo.sku.map`, e vale
                    # la stessa ragione del riaggancio: nessuno rilegge quelle
                    # righe, duplicano `kaufland.offer`, e la chiave e' quella
                    # dell'OFFERTA — non quella che arriva negli ordini.

                    riga.write({"id_unit": identificativo,
                                "ean": ean,
                                "ultimo_prezzo": corpo["listing_price"],
                                "ultima_quantita": corpo["amount"],
                                "ultimo_esito": "successo",
                                "ultimo_messaggio": False,
                                # ⚠️ Come nel riaggancio: chi riporta una
                                # riga a «successo» toglie anche la firma
                                # dell'errore, o il marchio diventa stantio
                                # e fa cancellare l'errore di un altro giro.
                                "errore_da": False,
                                "controllato_il": fields.Datetime.now()})
                    creata_ora = True
                # ⚠️ IL CONTATORE STA FUORI DAL SAVEPOINT, e non e' pignoleria:
                # in Odoo la scrittura viene mandata al database all'USCITA dal
                # context manager, non alla riga sopra. Se fallisce li', il
                # savepoint la annulla e il gestore conta `interrotte` — ma un
                # `create` gia' incrementato resterebbe, ed e' la stessa bugia
                # di «create: 3» con la banca dati vuota. Si conta solo cio'
                # che e' al sicuro.
                if creata_ora:
                    esito["create"] += 1
            except Exception as errore:  # noqa: BLE001
                # ⚠️ Il savepoint ha gia' riportato indietro le scritture di
                # QUESTA riga e rimesso in piedi la transazione: tutto quel
                # che era stato scritto per le righe precedenti resta.
                #
                # ⚠️ E si disfa il conteggio: la scrittura che lo giustificava
                # non c'e' piu', e senza questo la stessa riga comparirebbe
                # due volte nei conti — una dentro il savepoint annullato e
                # una qui sotto.
                if conteggio:
                    esito[conteggio] -= 1
                    if motivo_contato and motivi.get(motivo_contato):
                        motivi[motivo_contato] -= 1
                        if not motivi[motivo_contato]:
                            del motivi[motivo_contato]
                if not partita:
                    # Nessuna chiamata e' partita: l'offerta NON e' nata, e
                    # si puo' semplicemente saltare questa riga.
                    esito["saltate"] += 1
                    self._al_riparo(riga.write, {
                        "ultimo_esito": "saltato",
                        "ultimo_messaggio": _(
                            "Odoo non è riuscito a preparare l'offerta di "
                            "questa riga (%(tipo)s: %(errore)s). Nessuna "
                            "chiamata è partita verso Kaufland.")
                        % {"tipo": type(errore).__name__, "errore": errore}})
                    _logger.exception(
                        "Kaufland creazione sul canale %s: riga saltata per "
                        "un guasto prima della chiamata",
                        self.channel.display_name)
                    continue
                esito["interrotte"] += 1
                if not ricevuta:
                    # Il trasporto ha sollevato: non si sa se la richiesta
                    # sia partita, quindi vale l'incertezza piena.
                    fermata = self._interrompi(
                        riga, prodotto,
                        _("il trasporto ha sollevato %s: %s")
                        % (type(errore).__name__, errore), riferimento)
                else:
                    fermata = self._interrompi_odoo(riga, prodotto, errore,
                                                    riferimento)
                break

        # ⚠️ IL CANCELLO SI RICHIUDE QUI, PRIMA della chiusura del giro e nel
        # SUO savepoint. Stava dentro `_chiudi_creazione`, insieme alla riga
        # di registro: un guasto del registro faceva un ROLLBACK TO SAVEPOINT
        # che si portava via ANCHE la chiusura del cancello — che restava
        # aperto mentre esisteva un'offerta di esito ignoto, cioe' cadeva
        # proprio l'invariante che il cancello difende («so quali offerte
        # esistono su Kaufland»). Fra le due scritture, quella che deve
        # sopravvivere e' quella che protegge: va per prima, e da sola.
        cancello_chiuso = True
        if esito["interrotte"] or esito["senza_identificativo"]:
            # ⚠️ IL RITORNO DI `_al_riparo` SI LEGGE. Inghiotte il guasto
            # apposta, ma ignorarlo farebbe scrivere «La guardia è stata
            # RICHIUSA» su una guardia rimasta APERTA — e chi lo legge
            # rilancia la creazione il giorno dopo, che è esattamente lo
            # scenario di duplicazione che il cancello esiste per impedire.
            cancello_chiuso = self._al_riparo(
                self._riga_mercato().write, {"riagganciato": False,
                               "riagganciato_il": False})
            if not cancello_chiuso:
                esito["cancello_non_chiuso"] = 1
        try:
            with self.env.cr.savepoint():
                return self._chiudi_creazione(dominio, esito, motivi, fermata,
                                              cancello_chiuso)
        except Exception:  # noqa: BLE001
            # ⚠️ Nemmeno la chiusura puo' portarsi via il giro: se i conti o
            # il registro esplodono, le offerte create restano create e i
            # loro identificativi restano scritti. L'esito grezzo va nel log
            # di sistema, che il rollback non tocca.
            _logger.exception(
                "Kaufland creazione sul canale %s: la chiusura del giro è "
                "fallita. Esito grezzo: %s", self.channel.display_name, esito)
            esito.setdefault("rimaste", 0)
            esito["chiusura_fallita"] = 1
            return esito

    def _chiudi_creazione(self, dominio, esito, motivi, fermata,
                          cancello_chiuso=True):
        """Tira le somme della creazione e lascia una riga nel registro.

        ⚠️ Qui NON si scrive piu' il cancello: lo fa `crea_offerte` prima di
        chiamare questo metodo, nel proprio savepoint. Se stesse qui, un
        guasto sulla riga di registro se lo porterebbe via col rollback.
        """
        rimaste = self.env["kaufland.offer"].sudo().search_count(dominio)
        messaggio = _(
            "Create %(create)s offerte, %(saltate)s saltate, %(fallite)s "
            "rifiutate da Kaufland, %(senza_identificativo)s create senza "
            "identificativo, %(interrotte)s di esito ignoto.") % esito

        if esito["interrotte"]:
            messaggio += _(
                "\n⚠️ %s offerte hanno esito IGNOTO: Kaufland non ha "
                "risposto, o ha risposto con un guasto suo, dopo che la "
                "richiesta era già partita — quindi potrebbero essere nate. "
                "NON vanno ritentate: un secondo tentativo le duplicherebbe. "
                "Vanno RILETTE con «Riaggancia le offerte esistenti», e se il "
                "riaggancio non le trova vuol dire che non erano nate: il "
                "messaggio sulla loro riga dice come rimetterle in gioco, "
                "perché da sole non ci tornano più."
            ) % esito["interrotte"]
        if esito["senza_identificativo"]:
            messaggio += _(
                "\n⚠️ %s offerte sono nate su Kaufland senza che se ne "
                "conosca l'identificativo: sono vive e NON aggiornabili "
                "finché non si rifà il riaggancio."
            ) % esito["senza_identificativo"]
        if motivi:
            # ⚠️ I motivi DISTINTI, col loro conteggio: davanti a 150 righe
            # tutte identiche l'unica cosa che si puo' fare e' indovinare.
            messaggio += _("\nMotivi dei rifiuti: ") + "; ".join(
                "%s × %s" % (quante, motivo)
                for motivo, quante in sorted(motivi.items(),
                                             key=lambda c: -c[1]))
        if fermata:
            messaggio += "\n⚠️ " + fermata
        if rimaste:
            messaggio += _(
                "\nRestano %s righe candidate alla creazione: il giro NON ha "
                "finito. Si ripete il comando finché non ne resta nessuna."
            ) % rimaste

        # ⚠️ Il cancello e' gia' stato richiuso da `crea_offerte`: qui se ne
        # da' solo notizia. Un'offerta nata di cui non si conosce
        # l'identificativo — o di cui non si sa nemmeno se sia nata — rende
        # FALSO cio' che il cancello dichiara: «so quali offerte esistono su
        # Kaufland». Stessa scelta di `_chiudi`: un giro che trova un
        # problema TOGLIE l'autorizzazione, non la lascia dov'era.
        if esito["interrotte"] or esito["senza_identificativo"]:
            messaggio += _(
                "\n⚠️ La guardia del riaggancio è stata RICHIUSA: ci sono "
                "offerte su Kaufland di cui Odoo non conosce "
                "l'identificativo, quindi il quadro non è più affidabile. La "
                "creazione non ripartirà finché non si rifà il riaggancio."
            ) if cancello_chiuso else _(
                "\n⛔ NON si è riusciti a RICHIUDERE la guardia del "
                "riaggancio, che resta APERTA mentre ci sono offerte su "
                "Kaufland di cui Odoo non conosce l'identificativo. NON "
                "rilanciare la creazione: duplicherebbe. Eseguire subito "
                "«Riaggancia le offerte esistenti».")

        # ⚠️ Niente verde su un lavoro parziale, e «restano candidate» E'
        # lavoro parziale: il bottone di prova su una sola offerta esce
        # giallo apposta, perche' e' esattamente cio' che e'.
        fallito = bool(esito["saltate"] or esito["fallite"]
                       or esito["senza_identificativo"]
                       or esito["interrotte"] or fermata or rimaste)
        if fallito:
            _logger.warning("Kaufland creazione sul canale %s: %s",
                            self.channel.display_name, messaggio)
        self._registra("kaufland_crea_offerte",
                       "error" if fallito else "success", messaggio)
        esito["rimaste"] = rimaste
        return esito

    # ------------------------------------------------------------------
    # L'allineamento di prezzi e giacenze
    # ------------------------------------------------------------------
    @staticmethod
    def _numero_unita(grezzo):
        """L'identificativo dell'offerta come numero, o None.

        ⚠️ Una sola riga sporca fa cadere TUTTO l'allineamento se non la si
        toglie di mezzo prima: `corpi_aggiornamento` costruisce i gruppi in
        blocco e solleva `ValueError` sul primo identificativo che non e' un
        numero — cioe' un `id_unit` scritto a mano o arrivato storto
        impedirebbe di aggiornare il prezzo di tutte le altre offerte del
        canale. Qui si guarda una per una, e quella si salta.
        """
        try:
            return int(str(grezzo).strip())
        except (TypeError, ValueError):
            return None

    def _marca(self, riga, valori):
        """Scrive sulla riga senza cancellare l'errore DI UN ALTRO GIRO.

        ⚠️ Due regole, e la seconda è quella che si dimentica:

        1. Su una riga contesa o su un'offerta orfana — errori scritti dalla
           ricognizione o dal riaggancio — quel messaggio è l'UNICA cosa
           interrogabile per prodotto che dice perché quella riga non torna,
           e un allineamento riuscito lo cancellerebbe proprio mentre il
           problema è ancora lì. Quello NON si tocca. Prezzo, quantità e
           date si scrivono comunque: sono fatti, e non contraddicono
           l'errore.
        2. **Un errore che ha scritto l'allineamento è suo, e transitorio.**
           Se non potesse sostituirlo, la riga mostrerebbe per sempre il
           motivo del primo rifiuto: direbbe «Fallito» mentre si allinea
           benissimo, e quando venisse DAVVERO rifiutata mostrerebbe il
           motivo dell'altro ieri. Nulla, in tutto il modulo, ripulisce quel
           marchio. Perciò un esito dell'allineamento sostituisce un errore
           dell'allineamento, e un successo lo CHIUDE.

        `errore_da` è ciò che distingue i due casi: lo scrive solo questo
        giro, quindi vuoto significa «di un altro».
        """
        if riga.ultimo_esito == "errore" and riga.errore_da != "allineamento":
            nuovo = valori.get("ultimo_messaggio")
            if nuovo:
                _logger.warning(
                    "Kaufland allineamento sul canale %s: la riga porta già "
                    "un errore di un altro giro, il nuovo NON lo sostituisce "
                    "— %s", self.channel.display_name, nuovo)
            valori = {c: v for c, v in valori.items()
                      if c not in ("ultimo_esito", "ultimo_messaggio",
                                   "errore_da")}
        if not valori:
            return True
        return self._al_riparo(riga.write, valori)

    def _raccogli_cambi(self, righe, listino, canale, esito, motivi_salto,
                        tetto=None):
        """Cosa è cambiato, riga per riga, e cosa non si può nemmeno guardare.

        Rende `(cambi, per_id, esaminate)`. Ogni riga finisce in UNO di
        questi esiti: un cambio da mandare, «invariata», oppure «saltata» col
        suo motivo — la stessa regola dei secchi del riaggancio, perché una
        riga che non finisce da nessuna parte è una riga di cui non sappiamo
        dire niente.
        """
        cambi = []
        per_id = {}
        esaminate = 0
        for riga in righe:
            # ⚠️ Il tetto si conta sui CAMBI, non sulle righe esaminate: le
            # righe invariate non costano nulla a Kaufland, e fermarsi su
            # quelle lascerebbe le ultime offerte del canale senza
            # allineamento per sempre. Un cambio non mandato invece resta un
            # cambio al giro dopo, quindi il tetto non perde niente.
            if len(cambi) >= (tetto or MAX_CAMBI_PER_GIRO):
                break
            esaminate += 1
            prodotto = riga.product_id
            if not prodotto:
                # ⚠️ Un'offerta viva senza prodotto in Odoo (le orfane del
                # riaggancio): non ha prezzo né giacenza da mandare, e
                # `_available_quantity` esploderebbe su un recordset vuoto
                # portandosi via l'intero giro.
                self._salta(riga, esito, motivi_salto, _(
                    "offerta viva senza prodotto in Odoo: non c'è nessun "
                    "prezzo né nessuna giacenza da mandare finché non si sa "
                    "di chi è."))
                continue
            identificativo = self._numero_unita(riga.id_unit)
            if identificativo is None:
                self._salta(riga, esito, motivi_salto, _(
                    "l'identificativo dell'offerta (%r) non è un numero: "
                    "Kaufland rifiuterebbe l'intera richiesta, non la sola "
                    "riga. Va riletto con «Riaggancia le offerte "
                    "esistenti».") % riga.id_unit)
                continue
            if identificativo in per_id:
                # ⚠️ Il vincolo del database è su `id_unit` come TESTO:
                # «0123» e «123» sono due righe distinte per Postgres e lo
                # stesso numero per Kaufland. `corpi_aggiornamento` le
                # fonderebbe in una voce sola, e una delle due resterebbe
                # senza aggiornamento e senza che nessuno lo dica.
                self._salta(riga, esito, motivi_salto, _(
                    "l'identificativo %(id)s è già di un'altra riga di questo "
                    "canale scritta in modo diverso (%(testo)r): finché sono "
                    "due righe per la stessa offerta, non si manda né l'una "
                    "né l'altra.") % {"id": identificativo,
                                      "testo": riga.id_unit})
                continue
            try:
                prezzo = self._pricelist_price(listino, prodotto)
                centesimi = in_centesimi(prezzo) if prezzo else 0
                # ⚠️ La quantità si normalizza QUI con la stessa funzione che
                # la normalizzerà nel corpo: senza, una giacenza negativa
                # (ordini oltre la disponibilità: cosa ordinaria) verrebbe
                # confrontata come -3 contro lo 0 già mandato, risulterebbe
                # «cambiata» a ogni giro, e manderebbe per sempre lo stesso
                # zero che Kaufland ha già.
                quantita = pezzi_valide(self._available_quantity(prodotto,
                                                                 canale))
            except Exception as errore:  # noqa: BLE001
                # Un listino in valuta senza cambio del giorno, un prodotto
                # cancellato in concorrenza: una riga sola non deve portarsi
                # via l'allineamento di tutte le altre.
                _logger.exception(
                    "Kaufland allineamento sul canale %s: prezzo o giacenza "
                    "non leggibili", self.channel.display_name)
                self._salta(riga, esito, motivi_salto, _(
                    "prezzo o giacenza non leggibili (%(tipo)s: %(errore)s).")
                    % {"tipo": type(errore).__name__, "errore": errore})
                continue

            if centesimi <= 0:
                # ⚠️ Non è un salto: la giacenza si manda lo stesso, ed è
                # anzi il caso in cui serve di più. Ma è una notizia — su
                # Kaufland resta in vendita il prezzo di prima — e per questo
                # si conta e si dice, invece di sparire nei «invariate».
                esito["senza_prezzo"] += 1

            # ⚠️ IL PRIMO INVIO SI FORZA, e non è prudenza: `ultimo_prezzo`
            # e `ultima_quantita` sono Integer, valgono 0 finché qualcuno non
            # ci scrive, quindi «mai mandata» e «mandata zero» sono lo stesso
            # valore. Su un prodotto a giacenza 0 il confronto `0 != 0` non
            # manderebbe niente, su Kaufland resterebbe l'`amount` con cui
            # l'offerta è nata, e l'offerta continuerebbe a vendere merce che
            # non c'è — per sempre, perché un prodotto stabilmente a zero non
            # tornerà mai «cambiato». Il riaggancio ora scrive la base vera,
            # ma questa guardia copre anche le righe che esistono già e il
            # caso in cui Kaufland non restituisse quei campi.
            mai_mandata = not riga.allineato_il
            cambio = {"id_unit": identificativo}
            if centesimi > 0 and (mai_mandata
                                  or centesimi != riga.ultimo_prezzo):
                cambio["prezzo_centesimi"] = centesimi
            if mai_mandata or quantita != riga.ultima_quantita:
                cambio["quantita"] = quantita
            if len(cambio) == 1:
                # ⚠️ Un aggiornamento che non cambia niente è una chiamata
                # sprecata, e su migliaia di offerte si sente.
                esito["invariate"] += 1
                continue
            cambi.append(cambio)
            per_id[identificativo] = riga
        return cambi, per_id, esaminate

    def _salta(self, riga, esito, motivi_salto, motivo):
        """Una riga che non si manda, contata e detta col suo perché."""
        esito["saltate"] += 1
        motivi_salto[motivo] = motivi_salto.get(motivo, 0) + 1
        _logger.warning("Kaufland allineamento sul canale %s: %s",
                        self.channel.display_name, motivo)
        self._marca(riga, {"ultimo_esito": "errore",
                           "ultimo_messaggio": motivo,
                           "errore_da": "allineamento"})

    def allinea(self, limite=None):
        """Manda a Kaufland i prezzi e le giacenze cambiati.

        `limite` serve alla prova in piccolo, e qui serve più che nella
        creazione: se il canale ha il listino sbagliato il giro **riesce**, e
        mette in vendita fino a 3.000 prezzi sbagliati in una manciata di
        chiamate. La frenata protegge dai rifiuti, non dai successi
        sbagliati; solo una prova su una offerta lo fa.

        Si mandano SOLO le offerte che hanno un `id_unit` (le altre non sono
        aggiornabili) e SOLO i valori che sono cambiati davvero: su migliaia
        di offerte, rimandare l'uguale si sente.

        ⚠️ NON si filtra per `ultimo_esito`, ed è il contrario esatto della
        creazione. Qui si lavora sulle offerte VIVE: una riga può portare
        `ultimo_esito="errore"` per un impedimento della ricognizione — un
        codice a barre conteso, per dirne uno — e vendere regolarmente su
        Kaufland. Saltarla vorrebbe dire smettere di allinearle prezzo e
        giacenza in silenzio, che è il modo più costoso di sbagliare: si
        vende al prezzo di ieri senza che nessuno se ne accorga.

        ⚠️ E Kaufland rifiuta il GRUPPO INTERO, non la singola riga: l'esito
        va letto riga per riga dalla risposta, e ciò che è partito e non
        torna NON è «riuscito».
        """
        # ⚠️ Guardia 0 — il turno, lo stesso della creazione e per una
        # ragione precisa: l'allineamento legge gli `id_unit` che la
        # creazione scrive, e i due giri non devono sovrapporsi.
        self._prendi_il_turno(
            _("allineamento di prezzi e giacenze"),
            _("l'allineamento legge gli identificativi che la creazione "
              "scrive, e due giri sovrapposti li leggerebbero a metà"))

        mercato = self._mercato()
        canale = self.channel.sudo()
        client = self._client()
        Offerta = self.env["kaufland.offer"].sudo()
        # ⚠️ Senza listino ogni riga risulterebbe «senza prezzo» e si
        # manderebbero solo le giacenze, in silenzio. È configurazione
        # mancante, e si dice PRIMA di chiamare Kaufland — qui si può ancora
        # sollevare, perché non è partita nessuna richiesta. PER MERCATO.
        listino = self._listino_del_mercato()

        # ⚠️ Le righe con un `id_unit`, e basta: le altre non esistono su
        # Kaufland e non c'è niente da aggiornare.
        righe = Offerta.search([("market_id", "=", self._riga_mercato().id),
                                ("id_unit", "!=", False)])
        esito = {"mandate": 0, "aggiornate": 0, "fallite": 0, "invariate": 0,
                 "fermato_per_limite": False, "incerte": 0, "saltate": 0,
                 "non_registrate": 0, "estranee": 0, "senza_prezzo": 0,
                 "da_fare": 0}
        motivi = {}
        motivi_salto = {}

        tetto_cambi = (min(limite, MAX_CAMBI_PER_GIRO) if limite
                       else MAX_CAMBI_PER_GIRO)
        cambi, per_id, esaminate = self._raccogli_cambi(
            righe, listino, canale, esito, motivi_salto, tetto_cambi)

        try:
            gruppi = corpi_aggiornamento(cambi)
        except ValueError as errore:
            # `_raccogli_cambi` ha già tolto di mezzo tutto ciò che
            # `corpi_aggiornamento` sa rifiutare: se solleva lo stesso, è un
            # caso che non avevamo previsto e non si manda niente a nessuno.
            #
            # ⚠️ La UserError qui sotto annulla la transazione, e con essa
            # anche la riga di registro appena scritta: la stessa cura del
            # riaggancio, il log di sistema, che il rollback non tocca.
            _logger.error(
                "Kaufland allineamento sul canale %s: i gruppi non si sono "
                "potuti costruire — %s", self.channel.display_name, errore)
            self._registra("kaufland_allinea", "error", str(errore))
            raise UserError(_(
                "L'allineamento non è partito: %s") % errore)

        fermata = None
        ultimo_rifiuto = None
        rifiuti_di_fila = 0
        mandati = 0
        partenza = time.monotonic()

        for gruppo in gruppi:
            if mandati >= MAX_GRUPPI_PER_GIRO:
                fermata = _(
                    "Il giro si è fermato al tetto di %(gruppi)s gruppi da "
                    "%(quanti)s offerte per volta, per non farsi uccidere dal "
                    "limite di tempo del worker. Ripetere per continuare."
                ) % {"gruppi": MAX_GRUPPI_PER_GIRO, "quanti": GRUPPO}
                break
            # ⚠️ Il tempo si guarda solo dopo la prima chiamata: altrimenti
            # un canale lento non allineerebbe mai niente.
            if mandati and time.monotonic() - partenza > SECONDI_PER_GIRO:
                fermata = _(
                    "Il giro si è fermato da solo dopo %s secondi per non "
                    "farsi uccidere dal limite di tempo del worker. Ripetere "
                    "per continuare.") % SECONDI_PER_GIRO
                break

            mandati += 1
            esito["mandate"] += len(gruppo)
            try:
                # ⚠️ `storefront` nell'indirizzo anche qui: senza, Kaufland
                # rifiuta l'INTERO gruppo con «'storefront' is required».
                risposta = client.chiama(
                    "POST", "%s?storefront=%s" % (API_UNITA_BLOCCO, mercato),
                    gruppo)
            except Exception as errore:  # noqa: BLE001
                # `TrasportoRequests` promette di non sollevare, ma qui una
                # promessa non basta: non si sa se la richiesta sia partita.
                _logger.exception(
                    "Kaufland allineamento sul canale %s: il trasporto ha "
                    "sollevato", self.channel.display_name)
                esito["incerte"] += len(gruppo)
                fermata = _(
                    "Ci si è fermati: %s. Nessuna riga è stata aggiornata in "
                    "Odoo, quindi il giro dopo rimanda tutto da solo."
                ) % self._conta_motivo(motivi, _(
                    "il trasporto ha sollevato %(tipo)s: %(errore)s")
                    % {"tipo": type(errore).__name__, "errore": errore},
                    len(gruppo))
                break

            if risposta.troppe_chiamate:
                # Il tetto è 111 chiamate al secondo: insistere peggiora.
                # ⚠️ Le unità di questo gruppo si contano fra le fallite: è
                # un rifiuto certo (niente è cambiato su Kaufland), e senza
                # contarle i conti del giro non tornerebbero.
                esito["fermato_per_limite"] = True
                esito["fallite"] += len(gruppo)
                self._conta_motivo(motivi, self._motivo_http(risposta),
                                   len(gruppo))
                break

            if not risposta.ok:
                # ⚠️ Stato 0 o 5xx: non si sa se l'aggiornamento sia
                # arrivato. Qui il danno di un dubbio è molto minore che
                # nella creazione — rimandare lo stesso prezzo non duplica
                # niente — ma il giro NON si può dichiarare riuscito, e non
                # si scrive sulle righe quel che forse non è arrivato: così
                # il giro dopo lo rimanda da solo.
                if risposta.stato == 0 or risposta.stato >= 500:
                    esito["incerte"] += len(gruppo)
                    self._conta_motivo(motivi, self._causa_incerta(risposta),
                                       len(gruppo))
                    fermata = _(
                        "Ci si è fermati su una risposta di esito IGNOTO: "
                        "insistere sugli altri gruppi mentre il guasto è in "
                        "corso spreca chiamate. Nessuna riga è stata "
                        "aggiornata in Odoo, quindi il giro dopo rimanda "
                        "tutto da solo.")
                    break
                motivo = self._motivo_http(risposta)
            elif not isinstance(risposta.dati, list):
                # ⚠️ Una 2xx che non porta l'elenco degli esiti NON è un
                # successo: è la pagina di un proxy, o un corpo che non si è
                # capito. Trattarla come «tutto bene» scriverebbe su 150
                # righe dei valori che nessuno ha confermato, e da lì in poi
                # quelle righe risulterebbero «invariate» per sempre — cioè
                # il prezzo vecchio resterebbe su Kaufland in silenzio.
                motivo = self._motivo_stato(risposta.stato, _(
                    "risposta senza l'elenco degli esiti — %s")
                    % risposta.messaggio)
            else:
                motivo = None

            if motivo is not None:
                # ⚠️ Kaufland rifiuta il GRUPPO INTERO, non la singola riga.
                # Le righe NON si marcano una per una: il rifiuto non è un
                # verdetto su nessuna di loro (149 sono cadute per colpa
                # della centocinquantesima), e marcarle in errore
                # attribuirebbe a ciascuna una colpa che non ha. Il motivo,
                # col suo conteggio, sta nel registro.
                esito["fallite"] += len(gruppo)
                self._conta_motivo(motivi, motivo, len(gruppo))
                # ⚠️ Davanti a «150 × HTTP 400» una persona non sa da dove
                # cominciare: quali offerte fossero non risulta da nessuna
                # parte, perché le righe non si marcano. Costa un log.
                _logger.warning(
                    "Kaufland allineamento sul canale %s: gruppo di %s unità "
                    "rifiutato INTERO (%s). Le unità erano: %s",
                    self.channel.display_name, len(gruppo), motivo,
                    ", ".join(str(v["id_unit"]) for v in gruppo))
                rifiuti_di_fila = (rifiuti_di_fila + 1
                                   if motivo == ultimo_rifiuto else 1)
                ultimo_rifiuto = motivo
                if rifiuti_di_fila >= GRUPPI_RIFIUTATI_DI_FILA:
                    fermata = _(
                        "%(quanti)s gruppi interi di fila rifiutati con lo "
                        "STESSO motivo (%(motivo)s): non è il dato di una "
                        "riga, è la configurazione del canale. Ci si ferma "
                        "prima di sprecare le altre chiamate."
                    ) % {"quanti": rifiuti_di_fila, "motivo": motivo}
                    break
                continue

            ultimo_rifiuto, rifiuti_di_fila = None, 0
            self._leggi_esiti(gruppo, risposta, per_id, esito, motivi)

        # Quel che non è partito: i cambi rimasti fuori dal tetto più le
        # righe che non si sono nemmeno esaminate.
        esito["da_fare"] = (len(cambi) - esito["mandate"]
                            + max(0, len(righe) - esaminate))
        try:
            with self.env.cr.savepoint():
                return self._chiudi_allineamento(esito, motivi, motivi_salto,
                                                 fermata, len(righe))
        except Exception:  # noqa: BLE001
            # ⚠️ Nemmeno la chiusura può portarsi via il giro. Qui non ci sono
            # identificativi da perdere come nella creazione, ma un guasto sul
            # registro lascerebbe la transazione ABORTITA: il commit finale
            # diventerebbe un ROLLBACK silenzioso e sparirebbero anche i
            # prezzi appena registrati, mentre a video comparirebbe
            # «aggiornate: 150». L'esito grezzo va nel log di sistema, che il
            # rollback non tocca.
            _logger.exception(
                "Kaufland allineamento sul canale %s: la chiusura del giro è "
                "fallita. Esito grezzo: %s", self.channel.display_name, esito)
            esito["chiusura_fallita"] = 1
            return esito

    @staticmethod
    def _conta_motivo(motivi, motivo, quante=1):
        """Un motivo nel riepilogo, col suo conteggio. Rende il motivo."""
        motivi[motivo] = motivi.get(motivo, 0) + quante
        return motivo

    def _leggi_esiti(self, gruppo, risposta, per_id, esito, motivi):
        """Legge l'esito RIGA PER RIGA dentro la risposta di un gruppo.

        ⚠️ È qui che questo giro può mentire. Kaufland risponde con un
        elenco, e niente garantisce che ci sia una voce per ogni unità
        mandata: se se ne perdono cinquanta e ci si limita a contare le voci
        buone, il giro esce verde con «aggiornate: 100» mentre cinquanta
        offerte hanno ancora il prezzo di ieri. La riconciliazione è
        sull'INSIEME degli identificativi mandati: si segna quali sono
        tornati, e tutto ciò che non è tornato si conta fra le fallite —
        senza scrivere niente sulle sue righe, così il giro dopo le rimanda.
        """
        adesso = fields.Datetime.now()
        per_gruppo = {voce["id_unit"]: voce["unit_data"] for voce in gruppo}
        visti = set()
        for voce in (risposta.dati or []):
            if not isinstance(voce, dict):
                esito["estranee"] += 1
                continue
            # ⚠️ `unit_id` come `id_unit`: Kaufland usa l'uno in un punto
            # della documentazione e l'altro altrove, ed è la stessa ragione
            # per cui `kaufland_risposta.id_unit()` le prova tutte e due. Se
            # `/units/bulk` rispondesse con `unit_id`, ogni voce sarebbe
            # estranea, ogni unità mancante, e l'allineamento resterebbe
            # inerte al 100%. Si provano IN SEQUENZA, ciascuna col suo
            # controllo: con un solo `.get` e il valore predefinito, un
            # `id_unit` esplicitamente nullo coprirebbe un `unit_id` buono.
            identificativo = None
            for chiave in ("id_unit", "unit_id"):
                if voce.get(chiave) not in (None, ""):
                    identificativo = self._numero_unita(voce.get(chiave))
                    if identificativo is not None:
                        break
            # ⚠️ Una voce che non corrisponde a nessuna unità mandata NON si
            # conta come letta: contarla coprirebbe una mancante, e il gruppo
            # dimezzato tornerebbe a leggersi verde. Vale anche per la stessa
            # unità nominata due volte.
            if identificativo is None or identificativo not in per_gruppo \
                    or identificativo in visti:
                esito["estranee"] += 1
                continue
            visti.add(identificativo)
            riga = per_id.get(identificativo)
            stato = self._numero_unita(voce.get("status_code"))
            if stato is not None and 200 <= stato < 300:
                dati_cambio = per_gruppo[identificativo]
                valori = {"ultimo_esito": "successo",
                          "ultimo_messaggio": False,
                          # ⚠️ Un successo CHIUDE l'errore transitorio che
                          # l'allineamento aveva scritto: senza, la riga
                          # direbbe «Fallito» per sempre. L'errore di un
                          # altro giro invece resta, e `_marca` lo protegge.
                          "errore_da": False,
                          "controllato_il": adesso,
                          # ⚠️ La data che distingue «mai mandata» da
                          # «mandata zero». Si scrive solo qui: quando
                          # Kaufland ha confermato QUESTO invio.
                          "allineato_il": adesso}
                # ⚠️ Si scrive quel che è stato MANDATO, non quel che si è
                # calcolato: è il valore che ora vive su Kaufland, ed è il
                # termine di paragone del prossimo «è cambiato?».
                if "listing_price" in dati_cambio:
                    valori["ultimo_prezzo"] = dati_cambio["listing_price"]
                if "amount" in dati_cambio:
                    valori["ultima_quantita"] = dati_cambio["amount"]
                if riga is None:
                    # Non dovrebbe accadere (l'unità è nostra), ma senza riga
                    # non c'è niente da registrare e il conto lo deve dire.
                    esito["non_registrate"] += 1
                    continue
                # ⚠️ Il contatore si incrementa DOPO che la scrittura è al
                # sicuro: `_al_riparo` esegue dentro un savepoint e rende
                # False se il database l'ha annullata. Contare prima
                # produrrebbe «aggiornate: 150» sopra una banca dati che non
                # ha registrato niente.
                if self._marca(riga, valori):
                    esito["aggiornate"] += 1
                else:
                    esito["non_registrate"] += 1
                continue

            dettaglio = leggi_messaggio(stato or 0, {
                c: v for c, v in voce.items()
                if c not in ("id_unit", "status_code")})
            motivo = self._motivo_stato(
                stato if stato is not None else _("(assente)"), dettaglio)
            esito["fallite"] += 1
            self._conta_motivo(motivi, motivo)
            if riga is not None:
                # ⚠️ QUI il rifiuto è un verdetto sulla singola riga (a
                # differenza del gruppo intero rifiutato), e la riga è
                # l'unico posto dove si può leggere per prodotto.
                self._marca(riga, {"ultimo_esito": "errore",
                                   "ultimo_messaggio": motivo,
                                   "errore_da": "allineamento"})

        mancanti = len(gruppo) - len(visti)
        if mancanti > 0:
            # ⚠️ La riconciliazione: quel che è partito e non torna nella
            # risposta non è «riuscito». Senza questo conto, un gruppo
            # dimezzato leggerebbe verde. Le loro righe NON si toccano: non
            # avendo scritto il valore mandato, il prossimo giro le vede
            # ancora cambiate e le rimanda.
            esito["fallite"] += mancanti
            self._conta_motivo(motivi, _(
                "unità mandate che non compaiono nella risposta: Kaufland "
                "non ha detto cosa ne ha fatto, e in Odoo non si è scritto "
                "niente — il prossimo giro le rimanda"), mancanti)

    def _chiudi_allineamento(self, esito, motivi, motivi_salto, fermata,
                             vive=0):
        """Tira le somme dell'allineamento e lascia una riga nel registro.

        ⚠️ Niente verde su un lavoro parziale, e qui i modi di essere
        parziale sono sette: righe rifiutate, righe di esito ignoto, righe
        saltate, righe aggiornate su Kaufland ma non registrate in Odoo,
        voci estranee nella risposta, offerte vive senza prezzo di listino, e
        cambi che non sono nemmeno partiti.
        """
        # ⚠️ I conti devono tornare come nel riaggancio: ogni unità mandata
        # deve essere finita in UNO dei quattro secchi. Se non tornano, il
        # riepilogo lo dice invece di lasciarlo indovinare.
        somma = (esito["aggiornate"] + esito["fallite"] + esito["incerte"]
                 + esito["non_registrate"])
        quadra = somma == esito["mandate"]

        messaggio = _(
            "Mandate %(mandate)s, aggiornate %(aggiornate)s, fallite "
            "%(fallite)s, invariate %(invariate)s.") % esito
        if not vive:
            # ⚠️ Zero righe da guardare NON è «tutto a posto, niente da
            # fare»: è la stessa trappola già chiusa nel riaggancio e nella
            # ricognizione. Su un canale con offerte vive vuol dire che gli
            # identificativi non sono mai stati registrati, e allora prezzi e
            # giacenze là fuori sono fermi a quando l'offerta è nata.
            messaggio += _(
                "\n⚠️ Nessuna offerta con un identificativo su questo "
                "canale: non c'è niente da allineare. Se su Kaufland ci sono "
                "offerte vive, vuol dire che i loro identificativi non sono "
                "mai stati registrati qui — si rilegge con «Riaggancia le "
                "offerte esistenti».")
        if esito["saltate"]:
            messaggio += _(
                "\n⚠️ %s offerte NON si sono potute nemmeno guardare."
            ) % esito["saltate"]
        if esito["incerte"]:
            messaggio += _(
                "\n⚠️ %s offerte hanno esito IGNOTO: Kaufland non ha "
                "risposto, o ha risposto con un guasto suo. In Odoo non si è "
                "scritto niente su di loro, quindi il prossimo giro le "
                "rimanda da solo — un aggiornamento ripetuto non duplica "
                "nulla.") % esito["incerte"]
        if esito["non_registrate"]:
            messaggio += _(
                "\n⚠️ %s offerte sono state aggiornate su Kaufland ma NON si "
                "è riusciti a registrarlo in Odoo: il valore là fuori è "
                "quello nuovo, qui risulta ancora il vecchio, e il prossimo "
                "giro lo rimanderà.") % esito["non_registrate"]
        if esito["estranee"]:
            messaggio += _(
                "\n⚠️ Nella risposta di Kaufland ci sono %s voci che non "
                "corrispondono a nessuna unità mandata (o che ripetono la "
                "stessa): non sono state contate come esiti, e le unità che "
                "mancano risultano fra le fallite.") % esito["estranee"]
        if esito["senza_prezzo"]:
            messaggio += _(
                "\n⚠️ %s offerte vive non hanno un prezzo nel listino di "
                "vendita: su Kaufland resta in vendita il prezzo di prima, e "
                "di loro si allinea solo la giacenza."
            ) % esito["senza_prezzo"]
        if not quadra:
            messaggio += _(
                "\n⚠️ I conti non tornano: di %s unità mandate non si sa "
                "dire com'è finita. L'allineamento non è affidabile."
            ) % (esito["mandate"] - somma)
        if motivi_salto:
            messaggio += _("\nMotivi dei salti: ") + "; ".join(
                "%s × %s" % (quante, motivo)
                for motivo, quante in sorted(motivi_salto.items(),
                                             key=lambda c: -c[1]))
        if motivi:
            # ⚠️ I motivi DISTINTI, col loro conteggio: davanti a 150 righe
            # tutte identiche l'unica cosa che si può fare è indovinare.
            messaggio += _("\nMotivi: ") + "; ".join(
                "%s × %s" % (quante, motivo)
                for motivo, quante in sorted(motivi.items(),
                                             key=lambda c: -c[1]))
        if esito["fermato_per_limite"]:
            messaggio += _(
                "\n⚠️ Fermato dal limite di chiamate di Kaufland: il giro NON "
                "è completo.")
        if fermata:
            messaggio += "\n⚠️ " + fermata
        if esito["da_fare"]:
            messaggio += _(
                "\nRestano %s fra cambi non mandati e righe non esaminate: "
                "il giro NON ha finito. Si ripete il comando finché non ne "
                "resta nessuno.") % esito["da_fare"]

        fallito = bool(not vive or esito["fallite"] or esito["incerte"]
                       or esito["saltate"] or esito["non_registrate"]
                       or esito["estranee"] or esito["senza_prezzo"]
                       or esito["da_fare"] or esito["fermato_per_limite"]
                       or fermata or not quadra)
        if fallito:
            _logger.warning("Kaufland allineamento sul canale %s: %s",
                            self.channel.display_name, messaggio)
        self._registra("kaufland_allinea",
                       "error" if fallito else "success", messaggio)
        return esito
