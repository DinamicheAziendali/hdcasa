# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Cio' che un canale Cdiscount deve sapere per parlare con Octopia.

Le cose configurate a mano (le due credenziali OAuth2, il codice venditore,
il canale di vendita, il listino e i dati delle offerte) e due che si scrive
da solo (il gettone e la sua scadenza).

⚠️ **Questo modulo NON crea schede su Cdiscount, e non e' una mancanza: e'
una scelta** (2026-09-02). Il catalogo Octopia e' condiviso — una scheda per
GTIN — e le schede si creano dal portale, dove la categoria sta su ogni riga
e si vede cosa esiste gia'. Il modulo **aggancia** cio' che c'e'
(«Riaggancia le schede») e gestisce quel che e' davvero nostro: le offerte e
gli ordini. Un modulo che non puo' creare inserzioni non puo' crearne di
sbagliate.

E i bottoni. ⚠️ Il lavoro vero non e' qui: sta nel connettore
(`connectors/cdiscount.py`). Qui c'e' solo cio' che un bottone deve avere e
che un connettore non puo' avere — il permesso e la notifica.
"""
import logging
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# ⚠️ La classe base dei connettori, e serve per UNA costante:
# `LOCK_OCCUPATO`. Si importa invece di riscrivere «55P03» qui, perche' e' lo
# stesso codice su cui `_prendi_il_turno` decide: due copie che divergono
# farebbero chiamare «turno occupato» un deadlock, sopprimendo il ritentativo
# che Odoo fa da solo.
from odoo.addons.integrations_core.connectors.base import MarketplaceConnector

# ⚠️ Il codice del canale di vendita si prende da dove vive gia' — lo stesso
# `CANALE_FRANCIA` che il connettore mette nelle intestazioni di ogni
# chiamata. Due copie della stessa stringa prima o poi divergono, e una
# divergenza qui vorrebbe dire schede spedite a un canale di vendita che non
# e' quello per cui sono state composte.
from ..connectors.cdiscount_client import CANALE_FRANCIA

# ⚠️ Oggi l'elenco ha UNA voce sola, e non e' un errore: la ricognizione del
# 2026-08-25 su `GET /sellers/subscriptions` ha trovato il solo `CDISFR`. E'
# una Selection e non un Char perche' il valore finisce in un'intestazione di
# ogni chiamata: un refuso scritto a mano ("CDISFr", uno spazio in coda) si
# scoprirebbe solo dal rifiuto di un pacchetto asincrono, cioe' tre giorni
# dopo. Quando Cdiscount ne aprira' altri, si aggiunge qui una riga.
CANALI_VENDITA = [(CANALE_FRANCIA, "Cdiscount Francia (CDISFR)")]

_logger = logging.getLogger(__name__)


def _epoch_in_data(secondi):
    """I secondi epoch come data UTC naive, che e' cio' che Odoo tiene.

    ⚠️ Vuoto rende `False`, non «1 gennaio 1970»: un gettone che non c'e' non
    e' un gettone scaduto cinquant'anni fa, e mostrare una data sarebbe
    peggio che non mostrare niente.

    ⚠️ E un valore FUORI SCALA non deve poter rompere la schermata. Il campo
    e' un Float scritto dal connettore con cio' che il server ha risposto: una
    durata assurda ci arriva, e `utcfromtimestamp` su un numero fuori
    dall'intervallo delle date solleva. Un `compute` che solleva rende
    illeggibile TUTTA la scheda del canale — compresi i campi che servono a
    capire cosa non va — cioe' fa il danno peggiore proprio nel momento in cui
    si sta guardando perche' qualcosa non funziona.
    """
    if not secondi:
        return False
    try:
        return datetime.utcfromtimestamp(secondi)
    except (OverflowError, OSError, ValueError):
        return False


class CentrivoChannel(models.Model):
    _inherit = "centrivo.channel"

    # ------------------------------------------------------------------
    # Le credenziali. ⚠️ `groups="base.group_system"` NON e' un doppione di
    # `readonly`: in Odoo `readonly` e' un'indicazione per l'interfaccia, non
    # un vincolo dell'ORM, e `centrivo.channel` e' in scrittura a
    # `base.group_user`. Senza `groups=` un qualunque utente interno
    # leggerebbe il segreto con una `read` RPC. Stessa scelta gia' fatta su
    # Kaufland (marketplace_kaufland/models/kaufland_channel.py).
    # ------------------------------------------------------------------
    cdiscount_client_id = fields.Char(
        string="Client ID Cdiscount", groups="base.group_system",
        help="L'identificativo OAuth2 `client_credentials` del realm "
             "Keycloak `maas`.")
    cdiscount_client_secret = fields.Char(
        string="Segreto Cdiscount", groups="base.group_system",
        help="Il segreto OAuth2. Va copiato esattamente com'e': gli spazi in "
             "coda non si vedono e producono un 401 che sembra un permesso "
             "mancante.")
    cdiscount_seller_id = fields.Char(
        string="SellerId Cdiscount", groups="base.group_system",
        help="Il codice venditore Octopia. Viaggia in un'intestazione di "
             "ogni chiamata: senza, l'API non sa per conto di chi si parla.")

    # ------------------------------------------------------------------
    # La configurazione. Nessun `groups=`: e' della stessa famiglia del
    # gruppo di spedizione di Kaufland — configurazione operativa che chi
    # gestisce il canale deve poter leggere e correggere senza essere
    # amministratore di sistema.
    # ------------------------------------------------------------------
    cdiscount_canale_vendita = fields.Selection(
        CANALI_VENDITA, string="Canale di vendita",
        help="Il marketplace Octopia su cui finiscono le schede di questo "
             "canale. Oggi Cdiscount ce ne apre uno solo.")

    # ⚠️ QUESTO CAMPO DEVE RESTARE NELLA SCHERMATA (vedi
    # `views/cdiscount_channel_views.xml`). Sulla piattaforma esterna la
    # colonna esisteva, la migrazione l'aveva creata e la guardia dell'invio
    # la controllava — e NESSUNA schermata la scriveva. Risultato: il bottone
    # d'invio si rifiutava di partire e l'unico modo di sbloccarlo era una
    # scrittura a mano sul database. E' un difetto gia' pagato: chi togliesse
    # questo campo dalla vista rimetterebbe il modulo esattamente in quello
    # stato, e non se ne accorgerebbe finche' non prova a mandare.
    #
    # ⚠️ Dev'essere di LIVELLO 3, e da qui non si puo' verificare: una
    # categoria di primo livello ha la stessa forma (vedi
    # `connectors/cdiscount_schede.corpo_scheda`). L'unico controllo possibile
    # e' che qualcuno la legga dal portale Cdiscount e la copi qui.
    cdiscount_categoria = fields.Char(
        string="Categoria Cdiscount (livello 3)",
        help="Il codice della categoria in cui nascono le schede di questo "
             "canale. ⚠️ DEVE essere di LIVELLO 3: una categoria di primo o "
             "secondo livello ha la stessa forma, viene accettata dal "
             "campo e fa rifiutare il pacchetto tre giorni dopo. Si legge "
             "dal portale Cdiscount, ramo per ramo, fino alla foglia.")

    # ------------------------------------------------------------------
    # Il gettone. Se lo scrive il connettore, non una persona.
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # CONSEGNA 2 — le offerte. I due dati che bloccavano (éco-participation e
    # costo di spedizione) diventano campi; finche' mancano, le righe si
    # saltano e lo dicono. Nessun `groups=`: e' configurazione operativa.
    # ------------------------------------------------------------------
    cdiscount_iva = fields.Float(
        string="IVA delle offerte (%)", default=20.0, digits=(5, 2),
        help="L'aliquota dichiarata in ogni offerta (`taxes[].VAT`). La "
             "Francia e' al 20%. Octopia la vuole dichiarata da noi.")
    cdiscount_ecotax_obbligatoria = fields.Boolean(
        string="Pretendi l'éco-participation", default=True,
        help="Acceso: un prodotto con éco-participation a zero NON parte, "
             "la riga viene saltata e lo dice. Si spegne solo quando il "
             "consulente conferma che per quei prodotti lo zero e' giusto.")
    cdiscount_modo_consegna = fields.Char(
        string="Modo di consegna", default="TRK",
        help="Il codice del modo di consegna dell'account (misurato il "
             "2026-09-02: TRK = Envoi Suivi, REG = Recommandé). Si verifica "
             "su Cdiscount prima di ogni invio: un codice che l'account non "
             "ha ferma tutto. ⚠️ Nessuno dei due regge oltre i 30 kg: quei "
             "prodotti si saltano finche' non si attiva un modo «Big parcel».")
    cdiscount_spedizione_costo = fields.Float(
        string="Costo di spedizione (€)", digits=(16, 2), default=0.0,
        help="Quanto paga il cliente francese per la spedizione, per "
             "offerta (`deliveryModes[].cost`). Zero = spedizione gratuita. "
             "E' una decisione commerciale, non un campo tecnico.")
    cdiscount_spedizione_costo_aggiuntivo = fields.Float(
        string="Costo per pezzo aggiuntivo (€)", digits=(16, 2), default=0.0,
        help="Il costo di spedizione per ogni pezzo oltre il primo "
             "(`deliveryModes[].additionalCost`).")

    # ------------------------------------------------------------------
    # CONSEGNA 3 — gli ordini. La posizione fiscale (Francia, IVA inclusa) e
    # il prodotto delle spese di spedizione sono configurazione; il cancello
    # della spedizione e' la stessa disciplina di Kaufland.
    # ------------------------------------------------------------------
    cdiscount_posizione_fiscale = fields.Many2one(
        "account.fiscal.position", string="Posizione fiscale (Francia)",
        help="Applicata a ogni ordine importato. I prezzi arrivano IVA "
             "inclusa: l'aliquota francese dev'essere configurata «IVA "
             "inclusa», o il totale non torna e il registro lo dice.")
    cdiscount_prodotto_spedizione = fields.Many2one(
        "product.product", string="Prodotto spese di spedizione",
        domain="[('type', '=', 'service')]",
        help="La riga con cui le spese di spedizione pagate dal cliente "
             "entrano nell'ordine. Senza, le spese restano fuori e il "
             "registro lo dice.")
    cdiscount_spedizione_provata = fields.Boolean(
        string="Spedizione verificata sul portale", default=False, copy=False,
        groups="base.group_system",
        help="Finche' e' spento, la spedizione Cdiscount parte SOLO a mano, "
             "dal pulsante sull'ordine. Si accende dopo aver visto sul "
             "portale che il primo invio e' risultato spedito.")
    cdiscount_spedizione_provata_il = fields.Datetime(
        string="Verificata il", copy=False, groups="base.group_system")

    cdiscount_gettone = fields.Char(
        string="Gettone Cdiscount", readonly=True, copy=False,
        groups="base.group_system",
        help="L'ultimo token OAuth2 ottenuto. Dura due ore e si rinnova da "
             "solo: non si scrive a mano.")
    # ⚠️ E' un Float e porta SECONDI EPOCH, non una data. Non e' una stranezza:
    # `connectors/cdiscount_token` lavora in secondi (`scadenza` somma una
    # durata a un istante, `serve_rinnovo` sottrae due istanti), e quel file
    # non importa Odoo apposta, per potersi provare senza. Convertire avanti e
    # indietro in `Datetime` aggiungerebbe due passaggi di fuso orario fra il
    # calcolo e il confronto — e un errore di fuso qui vale un'ora di 401 che
    # sembrano credenziali sbagliate.
    #
    # ⚠️ Vuoto si legge `False`, e `serve_rinnovo` lo sa: qualunque valore
    # falso significa «non ho una scadenza», cioe' RINNOVA, non «vale per
    # sempre».
    cdiscount_gettone_scade = fields.Float(
        string="Il gettone scade (epoch)", readonly=True, copy=False,
        groups="base.group_system",
        help="Quando scade il gettone, in secondi epoch UTC. Vuoto significa "
             "«non ne ho»: il prossimo giro ne chiede uno nuovo.")

    # ⚠️ LO STESSO ISTANTE, SCRITTO IN MODO CHE UNA PERSONA LO POSSA LEGGERE.
    # Il campo qui sopra sta in schermata APPOSTA — «quando qualcosa non va e'
    # la prima cosa da guardare», perche' un gettone scaduto e un segreto
    # sbagliato danno lo stesso 401 — ma in schermata si mostra come
    # «1.787.563.200,00», che a chi non e' programmatore non dice niente: non
    # si sa nemmeno se sia passato o no. Un campo che serve a rispondere a una
    # domanda e che nessuno sa leggere e' un campo che non c'e'.
    #
    # ⚠️ Il Float NON si tocca, e non e' pigrizia: `cdiscount_token` lavora in
    # secondi epoch (`scadenza` somma una durata a un istante, `serve_rinnovo`
    # sottrae due istanti) e non importa Odoo apposta, per potersi provare
    # senza. Questo campo e' una LETTURA di quello, calcolata e non
    # memorizzata: due copie in banca dati divergerebbero, una copia calcolata
    # no.
    #
    # ⚠️ Stesso `groups=` dell'originale. Senza, un utente interno qualunque
    # leggerebbe con una `read` RPC un dato che sul Float non puo' vedere: un
    # calcolato che non eredita i permessi del campo da cui nasce e' un
    # buco aperto da una comodita'.
    #
    # ⚠️ Odoo tiene i Datetime in UTC e li mostra nel fuso di chi guarda:
    # `utcfromtimestamp` rende esattamente cio' che l'ORM si aspetta, e la
    # conversione la fa la schermata.
    cdiscount_gettone_scade_il = fields.Datetime(
        string="Il gettone scade il", readonly=True,
        compute="_compute_cdiscount_gettone_scade_il",
        groups="base.group_system",
        help="Lo stesso istante del campo qui sopra, in data e ora. Vuoto "
             "significa «non ho un gettone»: il prossimo giro ne chiede uno.")

    @api.depends("cdiscount_gettone_scade")
    def _compute_cdiscount_gettone_scade_il(self):
        for canale in self:
            canale.cdiscount_gettone_scade_il = _epoch_in_data(
                canale.cdiscount_gettone_scade)

    # ------------------------------------------------------------------
    # I TRE BOTTONI, e due di loro scrivono su un catalogo pubblico
    #
    # ⚠️ `groups="base.group_system"` sulla vista NASCONDE il bottone, non lo
    # impedisce: `centrivo.channel` e' in scrittura a `base.group_user`, e la
    # chiamata RPC di un metodo di modello resta a portata di qualunque
    # utente interno che sappia come si chiama. Il permesso vero e' il
    # controllo qui dentro, e i due vanno insieme — il primo perche' non si
    # veda, il secondo perche' non si possa.
    # ------------------------------------------------------------------
    def _cdiscount_solo_amministratori(self, cosa):
        """Il permesso dei tre bottoni, in un punto solo.

        ⚠️ Una copia per bottone diverge alla prima correzione: e' gia'
        successo su Kaufland con la regola del verde, scritta due volte e
        gia' divergente alla nascita (cinque condizioni di qua, sei di la').
        """
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_(
                "«%s» è riservato agli amministratori: usa le credenziali "
                "dell'azienda e scrive su un catalogo pubblico. Una scheda "
                "nata storta là fuori resta là fuori, e l'esito di un "
                "pacchetto scade in tre giorni.") % cosa)

    def _cdiscount_notifica(self, titolo, esito):
        """La notifica dei due bottoni d'invio: stessi numeri, stessa regola.

        ⚠️ **Niente verde su un lavoro parziale, e la regola sta in un punto
        solo.** Una scheda scartata, un pacchetto rifiutato o un esito ignoto
        non sono un successo: chi legge il verde non apre il registro, e
        l'esito ignoto e' precisamente la cosa che va guardata entro tre
        giorni.

        ⚠️ La CATEGORIA si nomina sempre. E' l'unica protezione contro una
        categoria cambiata per sbaglio che una persona possa esercitare: il
        campo non ha `groups=` (dev'essere scrivibile dalla schermata) e
        qualunque utente interno puo' toccarlo, quindi la si mette sotto gli
        occhi a ogni invio invece di scoprirla tre giorni dopo dal rifiuto di
        un pacchetto.
        """
        parziale = bool(esito.get("incerte") or esito.get("rifiutate")
                        or esito.get("scartate") or esito.get("non_partite")
                        or esito.get("fermata")
                        or esito.get("chiusura_fallita"))
        messaggio = (
            "Mandate %(mandate)s schede in %(pacchetti)s pacchetti, nella "
            "categoria %(categoria)s. Scartate prima di partire: "
            "%(scartate)s. In pacchetti rifiutati: %(rifiutate)s. Di esito "
            "ignoto: %(incerte)s. Non partite (guasto prima della rete): "
            "%(non_partite)s. Restano da mandare: %(rimaste)s."
        ) % {"mandate": esito.get("mandate", 0),
             "pacchetti": esito.get("pacchetti", 0),
             "categoria": esito.get("categoria") or "?",
             "scartate": esito.get("scartate", 0),
             "rifiutate": esito.get("rifiutate", 0),
             "incerte": esito.get("incerte", 0),
             "non_partite": esito.get("non_partite", 0),
             "rimaste": esito.get("rimaste", 0)}
        if esito.get("fermata"):
            messaggio += " ⚠️ %s" % esito["fermata"]
        if esito.get("chiusura_fallita"):
            messaggio += (" ⚠️ La chiusura del giro è fallita: i pacchetti "
                          "partiti restano partiti, ma i conti qui sopra non "
                          "sono finiti nel registro. Guarda il registro di "
                          "sistema.")
        if esito.get("incerte"):
            messaggio += (" ⚠️ NON rimandare niente prima di aver guardato "
                          "cosa c'è davvero su Cdiscount.")
        # ⚠️ E questa dice l'OPPOSTO, apposta: «non partite» è il guasto che
        # succede prima di toccare la rete (il gettone che non arriva), e lì
        # non c'è niente su Cdiscount da andare a guardare. Le due frasi non
        # si contraddicono perché parlano di due numeri diversi — ma sono
        # nate dallo stesso stato 0, ed è per questo che vanno dette
        # entrambe.
        if esito.get("non_partite"):
            messaggio += (" ⚠️ Le schede «non partite» non sono mai arrivate "
                          "a Cdiscount: non c'è niente da guardare là fuori "
                          "e non si creano doppioni. Ripartono da sole al "
                          "prossimo invio, una volta tolta la causa.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": titolo,
                "message": messaggio,
                "type": "warning" if parziale else "success",
                # ⚠️ Appiccicata quando qualcosa non è andato: una notifica
                # che sparisce da sola è il modo di non far leggere proprio
                # la riga che andava letta.
                "sticky": parziale,
            },
        }



    def _cdiscount_notifica_raccolta(self, titolo, esito):
        """La notifica del bottone «Raccogli gli esiti adesso».

        ⚠️ La regola del verde e' la STESSA di `_chiudi_raccolta` nel
        connettore, che decide il colore della riga di registro: due regole
        diverse per lo stesso giro farebbero leggere una notifica verde
        accanto a una riga rossa, e chi guarda crederebbe alla prima.

        ⚠️ E `scaduti` entra nel non-verde: un pacchetto scaduto e' l'esito di
        fino a 10.000 schede perso per sempre, ed e' LA cosa che non deve mai
        passare per un giro andato bene.
        """
        parziale = bool(esito.get("mancanti") or esito.get("estranee")
                        or esito.get("rifiutate") or esito.get("incerti")
                        or esito.get("illeggibili") or esito.get("guasti")
                        or esito.get("scaduti")
                        or esito.get("avvisi_falliti")
                        or esito.get("fermata")
                        or esito.get("chiusura_fallita"))
        messaggio = (
            "Pacchetti guardati: %(guardati)s, chiusi %(chiusi)s, ancora "
            "aperti %(rimasti)s. Schede confermate: %(confermate)s, "
            "rifiutate da Cdiscount: %(rifiutate)s, partite e mai nominate "
            "dal rapporto: %(mancanti)s. Ancora in lavorazione: "
            "%(attesa)s, non raggiungibili %(incerti)s, illeggibili "
            "%(illeggibili)s. ⚠️ Pacchetti SCADUTI e chiusi in questo giro: "
            "%(scaduti)s, e le schede rimaste senza verdetto sono "
            "%(senza)s."
        ) % {"guardati": esito.get("pacchetti", 0),
             "chiusi": esito.get("chiusi", 0),
             "rimasti": esito.get("rimasti", 0),
             "confermate": esito.get("confermate", 0),
             "rifiutate": esito.get("rifiutate", 0),
             "mancanti": esito.get("mancanti", 0),
             "attesa": esito.get("in_lavorazione", 0),
             "incerti": esito.get("incerti", 0),
             "illeggibili": esito.get("illeggibili", 0),
             "scaduti": esito.get("scaduti", 0),
             "senza": esito.get("senza_verdetto", 0)}
        if esito.get("muti"):
            messaggio += (" ⚠️ %s rapporti non nominano NESSUNA delle schede "
                          "partite: guarda il registro, e l'attività aperta "
                          "sul pacchetto." % esito["muti"])
        if esito.get("fermata"):
            messaggio += " ⚠️ %s" % esito["fermata"]
        if esito.get("chiusura_fallita"):
            messaggio += (" ⚠️ La chiusura del giro è fallita: i verdetti "
                          "raccolti restano scritti, ma i conti qui sopra non "
                          "sono finiti nel registro.")
        messaggio += (" Il dettaglio, pacchetto per pacchetto, è nel registro "
                      "delle operazioni.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": titolo,
                "message": messaggio,
                "type": "warning" if parziale else "success",
                "sticky": parziale,
            },
        }

    def action_cdiscount_riaggancia(self):
        """Legge da Cdiscount i prodotti gia' vendibili e li aggancia ai nostri.

        ⚠️ Non scrive niente su Cdiscount: e' il gemello di «Riaggancia» di
        Kaufland. Per questo non ha `confirm=` sulla vista — i confirm stanno
        sui gesti che toccano il marketplace, e metterli anche qui insegna a
        cliccarli via.

        ⚠️ **Va premuto PRIMA di qualunque offerta**, e ogni volta che sul
        portale nascono prodotti nuovi: le offerte nascono dalle schede
        agganciate, e una scheda che il riaggancio non ha visto non esiste
        per il modulo.
        """
        self.ensure_one()
        self._cdiscount_solo_amministratori(_("Riaggancia le schede Cdiscount"))
        esito = self._get_connector().riaggancia()
        return self._cdiscount_notifica_riaggancio(
            _("Cdiscount — riaggancio delle schede"), esito)

    def _cdiscount_notifica_riaggancio(self, titolo, esito):
        """La notifica del riaggancio.

        ⚠️ **Zero agganciate NON e' verde**, nemmeno con zero errori: e' il
        modo in cui questo giro puo' mentire — se Octopia rinominasse
        `sellerProductReference`, ogni riga cadrebbe negli scarti e il conto
        tornerebbe lo stesso. Un riaggancio che non aggancia niente e' una
        notizia, e va guardato.
        """
        agganciate = esito.get("agganciate", 0)
        parziale = bool(not agganciate or esito.get("senza_prodotto")
                        or esito.get("contese") or esito.get("scartate"))
        messaggio = (
            "Righe lette da Cdiscount: %(lette)s. Agganciate ai nostri "
            "prodotti: %(agganciate)s. Senza un prodotto in Odoo: "
            "%(senza)s. Non vendibili da noi: %(nonvend)s. Contese fra due "
            "codici: %(contese)s. Scartate perché senza riferimento "
            "venditore: %(scartate)s."
        ) % {"lette": esito.get("lette", 0), "agganciate": agganciate,
             "senza": esito.get("senza_prodotto", 0),
             "nonvend": esito.get("non_vendibili", 0),
             "contese": esito.get("contese", 0),
             "scartate": esito.get("scartate", 0)}
        if esito.get("senza"):
            messaggio += (" ⚠️ In vendita su Cdiscount ma senza prodotto in "
                          "Odoo: %s." % ", ".join(esito["senza"]))
        if esito.get("contese_dette"):
            messaggio += (" ⚠️ Codici che si contendono lo stesso prodotto: "
                          "%s." % ", ".join(esito["contese_dette"]))
        if not agganciate and esito.get("lette"):
            messaggio += (" ⚠️ Ha letto righe ma non ne ha agganciata "
                          "NESSUNA: controlla che i riferimenti venditore su "
                          "Cdiscount corrispondano ai riferimenti interni dei "
                          "prodotti in Odoo.")
        messaggio += " Il dettaglio è nel registro delle operazioni."
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": titolo,
                "message": messaggio,
                "type": "warning" if parziale else "success",
                "sticky": parziale,
            },
        }

    def _cdiscount_notifica_offerte(self, titolo, esito):
        """La notifica dei due bottoni delle offerte: niente verde su un
        lavoro parziale, e la regola sta in un punto solo."""
        parziale = bool(esito.get("incerte") or esito.get("rifiutate")
                        or esito.get("saltate") or esito.get("non_partite")
                        or esito.get("fermata")
                        or esito.get("chiusura_fallita"))
        messaggio = (
            "Mandate %(mandate)s offerte (di cui %(ritiri)s ritiri) in "
            "%(pacchetti)s pacchetti, col modo di consegna %(modo)s. "
            "Invariate: %(invariate)s. Saltate: %(saltate)s. In pacchetti "
            "rifiutati: %(rifiutate)s. Di esito ignoto: %(incerte)s. Non "
            "partite: %(non_partite)s."
        ) % {"mandate": esito.get("mandate", 0),
             "ritiri": esito.get("ritiri", 0),
             "pacchetti": esito.get("pacchetti", 0),
             "modo": esito.get("modo") or "?",
             "invariate": esito.get("invariate", 0),
             "saltate": esito.get("saltate", 0),
             "rifiutate": esito.get("rifiutate", 0),
             "incerte": esito.get("incerte", 0),
             "non_partite": esito.get("non_partite", 0)}
        if esito.get("fermata"):
            messaggio += " ⚠️ %s" % esito["fermata"]
        if esito.get("chiusura_fallita"):
            messaggio += (" ⚠️ La chiusura del giro è fallita: i pacchetti "
                          "partiti restano partiti, ma i conti non sono nel "
                          "registro. Guarda il registro di sistema.")
        if esito.get("incerte"):
            messaggio += (" ⚠️ Le offerte di esito ignoto restano «in "
                          "attesa»: il raccoglitore leggerà l'esito del "
                          "pacchetto. NON rimandare niente prima.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": titolo,
                "message": messaggio,
                "type": "warning" if parziale else "success",
                "sticky": parziale,
            },
        }

    def action_cdiscount_apri_cancello_spedizione(self):
        """Apre il cancello: da qui la spedizione Cdiscount rientra
        nell'automatismo. Si preme DOPO aver visto sul portale che il primo
        invio risulta spedito."""
        for canale in self.filtered(lambda c: c.connector_code == "cdiscount"):
            canale.sudo().write({
                "cdiscount_spedizione_provata": True,
                "cdiscount_spedizione_provata_il": fields.Datetime.now(),
            })
        return True

    def action_cdiscount_chiudi_cancello_spedizione(self):
        for canale in self.filtered(lambda c: c.connector_code == "cdiscount"):
            canale.sudo().cdiscount_spedizione_provata = False
        return True

    def action_cdiscount_allinea_una(self):
        """Manda UNA offerta. È la prima prova di prezzo sul marketplace vero.

        ⚠️ Il corpo di un'offerta è LETTO sulla documentazione, non misurato
        (non c'è un sandbox): la prima va guardata sul portale — prezzo, IVA,
        éco-participation, spese — prima di mandare il resto.
        """
        self.ensure_one()
        self._cdiscount_solo_amministratori(_("Allinea UNA offerta"))
        esito = self._get_connector().allinea_offerte(limite=1)
        return self._cdiscount_notifica_offerte(
            _("Cdiscount — una offerta"), esito)

    def action_cdiscount_allinea_offerte(self):
        """Manda a Cdiscount tutte le offerte cambiate, a pacchetti."""
        self.ensure_one()
        self._cdiscount_solo_amministratori(_("Allinea le offerte"))
        esito = self._get_connector().allinea_offerte()
        return self._cdiscount_notifica_offerte(
            _("Cdiscount — allineamento delle offerte"), esito)

    @api.model
    def cron_cdiscount_allinea_offerte_tutti(self):
        """L'allineamento delle offerte su tutti i canali Cdiscount attivi.

        ⚠️ Nasce spento (`data/ir_cron.xml`). Stesse regole della raccolta:
        un canale che esplode non ferma gli altri, e un turno occupato e' uno
        `skip`, non un errore.
        """
        canali = self.search([("connector_code", "=", "cdiscount"),
                              ("active", "=", True)])
        nomi = {canale.id: canale.display_name for canale in canali}
        for canale in canali:
            nome = nomi[canale.id]
            try:
                with self.env.cr.savepoint():
                    if not canale._cdiscount_turno_libero():
                        self._cdiscount_registra(canale, nome, "skip", _(
                            "Allineamento offerte saltato: un altro giro è "
                            "già in corso su questo canale."))
                        continue
                    canale._get_connector().allinea_offerte()
            except Exception as errore:  # noqa: BLE001
                _logger.exception(
                    "Allineamento offerte Cdiscount fallito su %s", nome)
                self._cdiscount_registra(
                    canale, nome, "error",
                    _("L'allineamento delle offerte si è interrotto: %s")
                    % errore)
        return True

    def action_cdiscount_raccogli_adesso(self):
        """Va a riprendere gli esiti ADESSO, senza aspettare il cron.

        ⚠️ **E' il gemello di «Manda UNA scheda», e senza di lui il giro e'
        chiuso.** Tutta la meta' raccoglitore (`raccogli`,
        `sorveglia_scadenze`) e' raggiungibile solo dal cron; il cron nasce
        SPENTO, e la lista di controllo dice di accenderlo «solo quando una
        raccolta vera e' andata pulita». Per accendere il cron serviva una
        raccolta pulita, e per avere una raccolta pulita serviva il cron:
        l'unica via d'uscita era Impostazioni → Tecnico → Azioni pianificate →
        Esegui manualmente, che non e' roba per chi non e' programmatore. E la
        prima raccolta vera e' esattamente quella che va guardata, come la
        prima spedizione.

        ⚠️ Nessun `confirm=` sulla vista, ed e' una scelta: questo bottone non
        scrive niente su Cdiscount — legge, e basta. Un `confirm` su un gesto
        senza conseguenze la' fuori insegna a cliccare via i confirm, e i due
        che contano stanno sui bottoni che creano schede su un catalogo
        pubblico.

        ⚠️ Se un altro giro sta gia' girando su questo canale, `raccogli()`
        solleva una `UserError` col suo perche' (`_prendi_il_turno`): a un
        bottone e' la risposta giusta — chi ha cliccato deve sapere che non e'
        successo niente e perche'. Il cron, che non ha nessuno davanti, la
        domanda se la fa PRIMA (`_cdiscount_turno_libero`) e registra `skip`.
        """
        self.ensure_one()
        self._cdiscount_solo_amministratori(
            _("Raccogli gli esiti Cdiscount adesso"))
        esito = self._get_connector().raccogli()
        return self._cdiscount_notifica_raccolta(
            _("Cdiscount — raccolta degli esiti"), esito)

    # ------------------------------------------------------------------
    # IL GIRO AUTOMATICO — la raccolta degli esiti
    #
    # ⚠️ Qui c'e' solo il METODO che il cron chiamera'. Il record `ir.cron` —
    # spento alla nascita, con `noupdate="1"` e mezz'ora di intervallo — e' del
    # Compito 12, insieme alla pagina.
    # ------------------------------------------------------------------
    def _cdiscount_turno_libero(self):
        """Vero se su questo canale non sta gia' girando qualcos'altro.

        ⚠️ Perche' una domanda e non un'eccezione da intercettare: `raccogli()`
        prende il turno del canale (`_prendi_il_turno`, un `FOR UPDATE
        NOWAIT`), lo stesso turno dell'invio. Se un amministratore sta
        mandando schede a mano quando scatta il cron, il turno e' occupato e
        `raccogli()` solleva una `UserError` — ma quella `UserError` e'
        indistinguibile, per TIPO, da «manca il SellerId», che e'
        configurazione sbagliata e va detta come errore. Guardare il turno
        PRIMA e' l'unico modo onesto di separare le due cose senza leggere il
        testo di un messaggio tradotto.

        ⚠️ E il lock che si prende qui NON si rilascia subito: resta preso
        fino alla fine della transazione del cron, quindi il `FOR UPDATE` che
        `raccogli()` rifa' tra un istante e' gia' nostro e non puo' fallire.
        La finestra fra la domanda e la risposta e' chiusa: senza, fra il
        controllo e la chiamata ci starebbe comodo il clic di un
        amministratore.

        ⚠️ Il savepoint e' necessario, non decorativo: un'istruzione SQL che
        fallisce lascia la transazione ABORTITA, e da li' in poi anche
        scrivere la riga di registro esploderebbe.

        ⚠️ Il codice del lock si prende dalla CLASSE BASE dei connettori e non
        si ricopia qui: e' la stessa costante che `_prendi_il_turno` usa per
        decidere, e due copie che divergono farebbero chiamare «turno
        occupato» un deadlock — sopprimendo il ritentativo che Odoo fa da
        solo.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                # Il nome della tabella viene dal modello, non scritto a mano.
                self.env.cr.execute(  # noqa: S608 - `_table` e' interno
                    "SELECT id FROM %s WHERE id = %%s FOR UPDATE NOWAIT"
                    % self._table, (self.id,))
        except Exception as errore:  # noqa: BLE001
            # ⚠️ Solo `lock_not_available` e' «c'e' un altro giro». Odoo apre i
            # cursori in REPEATABLE READ: un errore di SERIALIZZAZIONE e'
            # un'altra cosa, e chiamarla turno occupato direbbe il falso.
            if getattr(errore,
                       "pgcode", None) != MarketplaceConnector.LOCK_OCCUPATO:
                raise
            return False
        return True

    @api.model
    def cron_cdiscount_raccogli_tutti(self):
        """Va a riprendere l'esito dei pacchetti aperti di tutti i canali.

        ⚠️ Un canale che esplode non deve fermare gli altri: l'errore si
        registra e il giro continua. Un guasto su un canale non e' una buona
        ragione per lasciar scadere l'esito di un altro — e un esito scaduto
        non torna piu'.

        ⚠️ E un TURNO OCCUPATO non e' un errore: e' il lock che funziona. Se
        ogni mezz'ora il registro scrivesse «errore» perche' qualcuno stava
        mandando schede a mano, si imparerebbe a non leggerlo piu' — e
        l'assuefazione e' cio' che nasconde i guasti veri. Si registra come
        `skip`, con il suo perche'.

        ⚠️⚠️ E LA SORVEGLIANZA DELLE SCADENZE STA IN UN SAVEPOINT SUO, prima
        della raccolta. Sono due lavori con due rischi diversi: la prima
        chiude cio' che e' perduto e non tocca la rete, la seconda parla con
        Cdiscount e puo' fallire per mille ragioni. In un savepoint solo, il
        rollback della seconda annullerebbe la prima — anche i savepoint
        interni gia' rilasciati — e con un gettone rotto per tre giorni non
        si chiuderebbe mai niente, in silenzio. Vedi
        `CdiscountConnector.sorveglia_scadenze`.

        ⚠️ Si chiama il connettore, non un bottone: i bottoni hanno la guardia
        `base.group_system`, che qui non serve (il cron gira come
        `base.user_root`) e che legherebbe un automatismo a un controllo
        pensato per chi clicca.
        """
        canali = self.search([("connector_code", "=", "cdiscount"),
                              ("active", "=", True)])
        # ⚠️ I nomi si leggono TUTTI ORA, mentre la transazione e' certamente
        # sana. `display_name` e' una lettura SQL, e su una transazione gia'
        # abortita da un guasto del database solleva `InFailedSqlTransaction`:
        # letto dentro un gestore d'errore ucciderebbe il cron proprio nel
        # caso per cui il gestore e' stato scritto. Nei gestori si usa la
        # copia, che non tocca il database.
        nomi = {canale.id: canale.display_name for canale in canali}
        for canale in canali:
            nome = nomi[canale.id]
            # ------------------------------------------------------------
            # ⚠️⚠️ DUE SAVEPOINT, E NON E' PIGNOLERIA: E' LA RAGIONE PER CUI
            # QUESTO MODULO ESISTE.
            #
            # La sorveglianza delle scadenze chiude i pacchetti perduti e
            # manda le attivita'. La raccolta interroga Cdiscount e puo'
            # fallire — basta un gettone che non arriva. In UN SOLO
            # savepoint, il rollback della raccolta si porterebbe via anche
            # le chiusure e gli avvisi **gia' rilasciati**: catturare
            # l'eccezione in Python non salva niente, e un rollback esterno
            # annulla anche i savepoint interni gia' chiusi. Con credenziali
            # rotte per tre giorni non si chiuderebbe mai piu' niente e
            # nessuna attivita' comparirebbe — in silenzio.
            #
            # Divisa in due, cio' che la sorveglianza ha chiuso resta chiuso
            # anche quando la raccolta esplode un istante dopo.
            # ------------------------------------------------------------
            try:
                # ⚠️ IL SAVEPOINT, e perche' intercettare l'eccezione NON
                # basta. Se il connettore fa arrivare un errore DAL DATABASE,
                # la transazione resta ABORTITA: da li' in poi ogni canale
                # successivo fallisce, e il commit finale del cron diventa un
                # ROLLBACK SILENZIOSO che si porta via anche gli esiti gia'
                # raccolti — mentre il registro dice «raccolti: N».
                with self.env.cr.savepoint():
                    if not canale._cdiscount_turno_libero():
                        _logger.info(
                            "Raccolta Cdiscount saltata su %s: un altro giro "
                            "e' gia' in corso.", nome)
                        self._cdiscount_registra(canale, nome, "skip", _(
                            "Saltato: un altro giro è già in corso su questo "
                            "canale (di solito un invio a mano). Non è un "
                            "guasto: il prossimo passaggio del cron "
                            "riproverà."))
                        continue
                    # ⚠️ Il turno preso qui NON si rilascia all'uscita di
                    # questo savepoint: il `FOR UPDATE` vale fino alla fine
                    # della transazione del cron, quindi il secondo blocco e
                    # il `_prendi_il_turno` di `raccogli()` trovano il lock
                    # gia' nostro.
                    canale._get_connector().sorveglia_scadenze()
            except Exception as errore:  # noqa: BLE001
                _logger.exception(
                    "Sorveglianza delle scadenze Cdiscount fallita su %s",
                    nome)
                self._cdiscount_registra(
                    canale, nome, "error",
                    _("La sorveglianza delle scadenze si è interrotta: %s. "
                      "⚠️ Un pacchetto che scade senza essere chiuso resta in "
                      "giro per sempre: guarda il filtro «Oltre la scadenza, "
                      "ancora aperti».") % errore)
                # ⚠️ Non si prosegue con la raccolta: `raccogli()` rifa' la
                # sorveglianza per conto suo e fallirebbe allo stesso modo,
                # lasciando una seconda riga rossa che dice la stessa cosa.
                continue
            try:
                # ⚠️ Il SECONDO savepoint, e il terzo livello: `raccogli()` ne
                # ha gia' uno per pacchetto. Questo protegge cio' che sta
                # fuori da quelli — la ricerca dei pacchetti, il gettone, la
                # chiusura del giro — e soprattutto NON comprende le chiusure
                # per scadenza, che sono gia' state rilasciate sopra.
                with self.env.cr.savepoint():
                    canale._get_connector().raccogli()
            except Exception as errore:  # noqa: BLE001
                _logger.exception("Raccolta Cdiscount fallita su %s", nome)
                self._cdiscount_registra(
                    canale, nome, "error",
                    _("La raccolta si è interrotta: %s") % errore)
        return True

    def _cdiscount_registra(self, canale, nome, esito, messaggio):
        """Una riga nel registro delle operazioni, che non porti via il giro.

        ⚠️ Scrivere il registro e' l'ultima cosa che deve poter far cadere un
        cron: se la riga non si scrive (una transazione gia' abortita da un
        guasto vero del database, per dire), resta comunque il log di sistema
        — e gli altri canali vanno raccolti lo stesso.

        ⚠️ Per questo il nome arriva come PAROLA, gia' letta da chi chiama
        quando la transazione era sana: leggere `canale.display_name` qui
        dentro sarebbe una lettura SQL nel gestore d'errore, cioe' la stessa
        eccezione che si sta cercando di sopravvivere. Per la stessa ragione
        l'azienda si legge da `canale.company_id.id` **dentro** il `try`.

        ⚠️ E IL SAVEPOINT NON E' DECORATIVO, benche' l'eccezione sia gia'
        catturata qui sotto. Catturare in Python non salva la transazione: se
        questa `create` fallisce con un errore del DATABASE, PostgreSQL la
        lascia ABORTITA, e il canale successivo esplode gia' nel flush
        d'entrata del suo savepoint — cioe' il cron perderebbe tutti i canali
        che vengono dopo, proprio mentre sta scrivendo che uno solo e'
        andato male. Il gemello di casa (`kaufland._kaufland_registra`) ha
        ricevuto la stessa cura il 2026-08-27.
        """
        try:
            with self.env.cr.savepoint():
                self.env["centrivo.job.log"].sudo().create({
                    "channel_id": canale.id,
                    "operation": "cdiscount_raccogli",
                    "result": esito,
                    "message": messaggio,
                    "company_id": canale.company_id.id,
                })
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Cdiscount: non si e' potuta scrivere la riga di registro "
                "(%s) del canale %s", esito, nome)
