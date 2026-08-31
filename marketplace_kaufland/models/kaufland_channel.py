# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# ⚠️ Il codice d'errore del lock si prende da dove vive, non si riscrive: e'
# lo stesso `FOR UPDATE NOWAIT` del connettore, e due copie dello stesso
# numero magico prima o poi divergono.
from ..connectors.kaufland import LOCK_OCCUPATO

_logger = logging.getLogger(__name__)

# ⚠️ Un canale per MERCATO, non uno per Kaufland. Kaufland e' sei mercati
# sotto un solo account venditore, ma `centrivo.sku.map` ha il vincolo
# unique(channel_id, external_code): un canale unico farebbe collidere le sei
# offerte dello stesso prodotto. E listino, magazzino, IVA e gruppo di
# spedizione sono gia' campi del canale, e cambiano da mercato a mercato.
MERCATI = [("at", "Austria"), ("de", "Germania"), ("es", "Spagna"),
           ("fr", "Francia"), ("it", "Italia"), ("nl", "Paesi Bassi")]

# ⚠️ Le chiavi di un esito che NON sono un guasto. Tutto il resto — saltate,
# fallite, senza identificativo, di esito ignoto, ancora da fare — rende il
# giro parziale, e un giro parziale non esce verde. Vedi `_kaufland_notifica`
# per il perche' si elencano le buone e non le guaste.
CHIAVI_BUONE = ("create", "aggiornate", "invariate", "mandate")

# ⚠️ I CAMPI SU CUI IL CANCELLO SI REGGE. `kaufland_riagganciato` non
# significa «ho letto le offerte vive»: significa «ho letto le offerte vive
# DI QUESTO MERCATO, CON QUESTE CREDENZIALI, SU QUESTO INDIRIZZO». Cambiato
# uno di questi quattro, la frase resta scritta ma non parla piu' di niente.
# Il caso vero: un canale «Italia» riagganciato pulito a cui si cambia il
# mercato in `de` per provare — le righe restano «pronte» riferite all'Italia,
# il cancello e' alzato, e «Crea le offerte mancanti» metterebbe in vendita su
# Kaufland.de offerte mai riconosciute la'.
CAMPI_DEL_CANCELLO = ("kaufland_storefront", "kaufland_client_key",
                      "kaufland_secret_key", "base_url")


class CentrivoChannel(models.Model):
    _inherit = "centrivo.channel"

    kaufland_client_key = fields.Char(
        string="Chiave cliente Kaufland", groups="base.group_system")
    kaufland_secret_key = fields.Char(
        string="Segreto Kaufland", groups="base.group_system",
        help="La stringa esatta fornita da Kaufland. ⚠️ Somiglia a un numero "
             "esadecimale ma va usata come testo: interpretarla come numero "
             "produce una firma sbagliata.")
    # ⚠️ MERCATO, GRUPPO DI SPEDIZIONE e MAGAZZINO se ne sono andati di qui il
    # 2026-08-29: stanno sulle righe di `kaufland_market_ids`. Le chiavi API
    # sono dell'account venditore, non del mercato, e tenerli qui obbligava a
    # un canale per mercato — cioe' lo stesso segreto copiato cinque volte.
    kaufland_market_ids = fields.One2many(
        "centrivo.kaufland.market", "channel_id", string="Mercati Kaufland",
        help="Un mercato per riga, ognuno col suo gruppo di spedizione e il "
             "suo cancello. Le credenziali si scrivono una volta sola.")
    # ⚠️ Serve alle SCHERMATE, non alla sicurezza: i bottoni che scrivono su
    # Kaufland restano chiusi dall'AccessError nei metodi e dal cancello di
    # OGNI mercato dentro il connettore. Questo dice solo «c'e' almeno un
    # mercato pronto», cosi' non si mostra un bottone che non farebbe niente.
    kaufland_cancello_aperto = fields.Boolean(
        string="Almeno un mercato riagganciato",
        compute="_compute_kaufland_cancello_aperto",
        groups="base.group_system")

    kaufland_handling_time = fields.Integer(
        string="Giorni di lavorazione (ripiego)", default=3,
        help="Usato SOLO quando il prodotto non dichiara il suo tempo di "
             "risposta al cliente. Se il prodotto ce l'ha, vince il suo: un "
             "articolo lento dev'essere promesso lento, o il ritardo diventa "
             "una metrica che peggiora.")

    # ⚠️ La guardia del riaggancio (Compito 7). Finche' e' falso, la
    # creazione delle offerte si rifiuta di partire: su Kaufland ci sono gia'
    # offerte vive, e crearne di nuove senza sapere quali esistono le
    # duplicherebbe su un marketplace vero.
    #
    # ⚠️ `groups=` NON e' un doppione di `readonly=True`: in Odoo
    # `readonly` e' un'indicazione per l'interfaccia, non un vincolo dell'ORM.
    # `centrivo.channel` e' in scrittura a `base.group_user`, quindi senza
    # `groups=` un qualunque utente interno alzerebbe il cancello con una sola
    # `write` RPC, aggirando del tutto l'`AccessError` messo sul bottone qui
    # sotto. E' la seconda porta verso la stessa stanza, e si chiude come sono
    # gia' chiuse le due credenziali qui sopra.
    # ⚠️ IL CANCELLO NON E' PIU' QUI: vive sulla riga del mercato
    # (`centrivo.kaufland.market`). Un valore solo per tutto il canale voleva
    # dire che un mercato che legge male si portava dietro gli altri — si
    # smetteva di poter creare offerte su un mercato sano per colpa di un
    # altro, o, nel verso peggiore, si teneva aperto un cancello che nessuno
    # aveva meritato.

    # ------------------------------------------------------------------
    # Ciò su cui il cancello si regge non si cambia sotto al cancello
    # ------------------------------------------------------------------
    def write(self, valori):
        """Cambiare mercato, credenziali o indirizzo RICHIUDE la guardia.

        ⚠️ Il cancello dichiara «so quali offerte esistono su Kaufland»: è
        una frase su UN mercato, letta con QUELLE credenziali. Cambiato uno
        dei due, la frase resta scritta e non parla più di niente — e il
        bottone «Crea le offerte mancanti» resta acceso su un quadro che
        riguarda un altro mercato.

        ⚠️ Si guarda se il valore CAMBIA DAVVERO, non se compare fra quelli
        scritti: un salvataggio che rimanda lo stesso mercato non deve
        costare un riaggancio a nessuno. E si confronta normalizzando, perché
        in Odoo un Char vuoto vale `False` mentre dal client arriva `""`, e
        una differenza inventata è comunque una seccatura.

        ⚠️ Tutto in `sudo()`, e non è una comodità: i tre campi Kaufland hanno
        `groups="base.group_system"`, quindi a un utente normale si
        leggerebbero vuoti — cioè «cambiati» sempre — e la scrittura che
        richiude la guardia gli morirebbe in mano con un `AccessError` mentre
        stava solo salvando un indirizzo. `centrivo.channel` è in scrittura a
        `base.group_user`: qui ci passa gente che non è amministratrice.
        """
        campi = [c for c in CAMPI_DEL_CANCELLO if c in valori]
        da_richiudere = []
        da_azzerare = []
        if campi:
            for canale in self.sudo():
                if canale.connector_code != "kaufland":
                    continue
                cambiati = [c for c in campi
                            if str(canale[c] or "") != str(valori[c] or "")]
                if not cambiati:
                    continue
                # ⚠️ IL MERCATO CHE CAMBIA PORTA VIA ANCHE LA SOGLIA, e solo
                # lui. «Offerte attese su Kaufland» è un numero DI UN
                # MERCATO: le 166 dell'Italia appiccicate a un canale ormai
                # tedesco facevano dire al riaggancio «ne sono attese 166» di
                # un mercato che non le ha mai avute. Il verso era sicuro (il
                # cancello restava chiuso) ma il testo mentiva. Azzerata, la
                # soglia torna a «prima misura»: si misura, si conferma, si
                # riparte — che su un mercato nuovo è esattamente giusto.
                #
                # ⚠️ Le CREDENZIALI e l'indirizzo NO, ed è il verso stretto:
                # il mercato è lo stesso, e una chiave nuova con un permesso
                # più stretto che restituisce meno offerte è precisamente ciò
                # che la soglia esiste per fermare. Azzerarla la
                # disarmerebbe proprio nel caso che conta.
                if ("kaufland_storefront" in cambiati
                        and canale.kaufland_offerte_attese):
                    da_azzerare.append(canale.id)
                if (canale.kaufland_riagganciato
                        or canale.kaufland_riagganciato_il):
                    da_richiudere.append(canale.id)
        esito = super().write(valori)
        # ⚠️ DOPO la scrittura vera, e in `write` che NON toccano i campi del
        # cancello: nessuna ricorsione.
        if da_richiudere:
            richiusi = self.sudo().browse(da_richiudere)
            richiusi.write({"kaufland_riagganciato": False,
                            "kaufland_riagganciato_il": False})
            for canale in richiusi:
                _logger.warning(
                    "Kaufland: sul canale %s è cambiato %s, la guardia del "
                    "riaggancio è stata RICHIUSA. Va rifatto «Riaggancia le "
                    "offerte esistenti» prima di creare.",
                    canale.display_name, ", ".join(campi))
        if da_azzerare:
            azzerati = self.sudo().browse(da_azzerare)
            azzerati.write({"kaufland_offerte_attese": 0})
            for canale in azzerati:
                _logger.warning(
                    "Kaufland: sul canale %s è cambiato il mercato, e "
                    "«Offerte attese su Kaufland» è stato azzerato: quel "
                    "numero parlava dell'altro mercato. Il prossimo "
                    "riaggancio lo rimisura e chiede di confermarlo.",
                    canale.display_name)
        return esito

    # ------------------------------------------------------------------
    # Il bottone del riaggancio
    # ------------------------------------------------------------------
    @api.depends("kaufland_market_ids.riagganciato")
    def _compute_kaufland_cancello_aperto(self):
        for canale in self:
            canale.kaufland_cancello_aperto = any(
                canale.kaufland_market_ids.filtered("active").mapped(
                    "riagganciato"))

    def action_kaufland_riaggancia(self):
        """Legge le offerte già vive su Kaufland e popola la mappa.

        ⚠️ Non scrive nulla su Kaufland: e' l'unica cosa che si puo'
        eseguire sul vero senza rischio, ed e' il presupposto di tutto il
        resto.
        """
        self.ensure_one()
        # ⚠️ `centrivo.channel` è in scrittura a `base.group_user`, e il
        # `groups=` sulla vista NASCONDE il bottone, non lo impedisce: la
        # chiamata RPC resta a portata di qualunque utente interno. E qui non
        # è una formalità: `riaggancia()` legge le credenziali dell'azienda in
        # `sudo()` e apre la guardia che più avanti autorizzerà a scrivere su
        # un marketplace vero.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_(
                "Il riaggancio Kaufland è riservato agli amministratori: usa "
                "le credenziali dell'azienda e apre la guardia che autorizza "
                "la creazione delle offerte."))
        esito = self._kaufland_riunisci(
            self._get_connector().per_mercato("riaggancia"))
        if esito.get("errori"):
            return self._kaufland_guasto(
                _("Riaggancio Kaufland"), esito)
        # ⚠️ Niente verde su un lavoro parziale. Verde SOLO se la guardia si è
        # aperta e non ci sono offerte vive senza prodotto in Odoo.
        parziale = not esito.get("completo", 0)
        messaggio = "Lette %s offerte: %s riagganciate, %s senza prodotto, " \
                    "%s contese, %s scartate." % (
                        esito.get("lette", 0), esito.get("agganciate", 0),
                        esito.get("senza_prodotto", 0), esito.get("contese", 0),
                        esito.get("scartate", 0))
        if esito.get("completo", 0):
            messaggio += " La creazione delle offerte è ora sbloccata."
        elif esito.get("prima_misura", 0):
            # ⚠️ La prima misura NON è un guasto, ed è l'unico momento in cui
            # una difesa contro i doppioni dipende dall'occhio di una
            # persona: la notifica deve dire il numero e cosa farne, non
            # mandare a cercarlo nel registro. Resta gialla e appiccicata —
            # una finestra che si chiude da sola su questo passaggio è
            # esattamente il modo di non farlo.
            messaggio += " ⚠️ Prima misura: la guardia RESTA CHIUSA " \
                         "apposta, e non c'è niente da riparare. Controlla " \
                         "che %s sia il numero di offerte che vedi sul " \
                         "portale Kaufland per questo mercato, conferma " \
                         "«Offerte attese su Kaufland» sul canale e ripeti " \
                         "il riaggancio." % esito.get("lette", 0)
        else:
            messaggio += " ⚠️ La guardia RESTA CHIUSA: la creazione delle " \
                         "offerte non partirà. Vedi il registro delle " \
                         "operazioni."
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Riaggancio Kaufland",
                "message": messaggio,
                "type": "warning" if parziale else "success",
                "sticky": bool(parziale),
            },
        }

    # ------------------------------------------------------------------
    # Il bottone della ricognizione
    # ------------------------------------------------------------------
    def action_kaufland_ricognizione(self):
        """Guarda su Kaufland in che stato è la scheda di ogni prodotto.

        ⚠️ Sola lettura verso Kaufland: non crea e non modifica nessuna
        offerta. In Odoo scrive solo lo stato letto sulle righe
        `kaufland.offer`, e non tocca il cancello del riaggancio.
        """
        self.ensure_one()
        # ⚠️ Stessa simmetria del riaggancio: il `groups=` sulla vista
        # NASCONDE il bottone, non lo impedisce, e la chiamata RPC resta a
        # portata di qualunque utente interno. Qui non è una formalità: un
        # solo clic spara centinaia di chiamate firmate con le credenziali
        # dell'azienda, che si leggono in `sudo()`.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_(
                "La ricognizione Kaufland è riservata agli amministratori: "
                "usa le credenziali dell'azienda e interroga Kaufland una "
                "volta per ogni prodotto etichettato."))
        esito = self._kaufland_riunisci(
            self._get_connector().per_mercato("ricognizione"))
        # ⚠️ Se un mercato ha sollevato, i contatori possono non esserci
        # affatto: si mostra il guasto e si esce, invece di leggerli e
        # seppellire il messaggio vero sotto un KeyError.
        if esito.get("errori"):
            return self._kaufland_guasto(
                _("Ricognizione Kaufland"), esito)
        # ⚠️ LA REGOLA DEL VERDE E' UNA SOLA, e sta nel connettore
        # (`_chiudi_ricognizione`). Qui ce n'era una SECONDA copia, scritta a
        # mano, e le due erano gia' divergenti alla nascita: cinque
        # condizioni qui, sei là. Il risultato è la peggiore delle bugie
        # possibili — la notifica a video VERDE mentre il registro dice
        # «errore» — e chi guarda il video decide di creare. Chi aggiunge un
        # modo di essere parziali lo aggiunge in un posto solo.
        parziale = not esito.get("completo", 0)
        if not esito.get("guardati", 0):
            messaggio = "Nessun prodotto guardato: controlla le etichette " \
                        "prodotto del canale e che i prodotti abbiano il " \
                        "codice a barre."
        else:
            messaggio = "%s pronte, %s gusci, %s assenti, %s senza risposta." \
                        % (esito.get("pronte", 0), esito.get("gusci", 0), esito.get("assenti", 0),
                           esito.get("sconosciuti", 0))
            if esito.get("gia_vive", 0):
                messaggio += " ⚠️ %s hanno il codice a barre di un'offerta " \
                             "già viva e NON sono creabili." \
                             % esito.get("gia_vive", 0)
            if esito.get("gemelli", 0):
                messaggio += " ⚠️ %s hanno un codice a barre condiviso con " \
                             "un altro prodotto e NON sono creabili." \
                             % esito.get("gemelli", 0)
            if esito.get("senza_barcode", 0):
                messaggio += " %s prodotti etichettati sono senza codice a " \
                             "barre e non sono stati guardati." \
                             % esito.get("senza_barcode", 0)
            if esito.get("doppioni", 0):
                messaggio += " ⚠️ %s hanno il codice a barre di ALTRE " \
                             "offerte già vive su Kaufland: il doppione " \
                             "esiste già là fuori." % esito.get("doppioni", 0)
            if esito.get("da_guardare", 0):
                # ⚠️ «Mai guardati», non «rimasti fuori da questo giro»: il
                # secondo numero, su un catalogo più grande del tetto, non
                # scenderebbe mai a zero e direbbe il falso invitando a
                # ripetere un comando che non finisce.
                messaggio += " Restano %s prodotti mai guardati: ripetere " \
                             "il comando, il giro riprende da dove si era " \
                             "fermato." % esito.get("da_guardare", 0)
            if parziale:
                messaggio += " ⚠️ Il quadro è INCOMPLETO: vedi il registro " \
                             "delle operazioni."
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Ricognizione Kaufland",
                "message": messaggio,
                "type": "warning" if parziale else "success",
                "sticky": parziale,
            },
        }

    # ------------------------------------------------------------------
    # I bottoni della creazione — gli unici che SCRIVONO su Kaufland
    # ------------------------------------------------------------------
    def _kaufland_solo_amministratori(self):
        """La stessa simmetria del riaggancio e della ricognizione.

        ⚠️ Il `groups=` sulla vista NASCONDE il bottone, non lo impedisce: la
        chiamata RPC resta a portata di qualunque utente interno, e
        `centrivo.channel` è in scrittura a `base.group_user`. Qui è meno
        formale che altrove: questo bottone METTE IN VENDITA prodotti su un
        marketplace vero, con le credenziali dell'azienda lette in `sudo()`,
        e non si annulla.
        """
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_(
                "La creazione delle offerte Kaufland è riservata agli "
                "amministratori: mette in vendita prodotti su un marketplace "
                "vero, con le credenziali dell'azienda, e non si annulla."))

    def action_kaufland_crea_una_offerta(self):
        """Crea UNA sola offerta: la prima prova sul vero si fa così."""
        self.ensure_one()
        self._kaufland_solo_amministratori()
        esito = self._kaufland_riunisci(
            self._get_connector().per_mercato("crea_offerte", limite=1))
        return self._kaufland_notifica("Prova su una sola offerta", esito)

    def action_kaufland_crea_offerte(self):
        """Crea su Kaufland le offerte che mancano."""
        self.ensure_one()
        self._kaufland_solo_amministratori()
        esito = self._kaufland_riunisci(
            self._get_connector().per_mercato("crea_offerte"))
        return self._kaufland_notifica("Creazione offerte Kaufland", esito)

    # ------------------------------------------------------------------
    # Il bottone dell'allineamento — scrive su Kaufland, ma non crea niente
    # ------------------------------------------------------------------
    def action_kaufland_allinea(self):
        """Manda a Kaufland i prezzi e le giacenze cambiati.

        ⚠️ Scrive su un marketplace vero, ma NON crea niente: aggiorna
        offerte che esistono già. Per questo un aggiornamento ripetuto non
        duplica nulla — ed è il motivo per cui, al contrario della creazione,
        su un esito incerto il giro dopo può semplicemente rimandare.
        """
        self.ensure_one()
        # ⚠️ Stessa simmetria dei bottoni della creazione: il `groups=` sulla
        # vista NASCONDE il bottone, non lo impedisce, e `centrivo.channel` è
        # in scrittura a `base.group_user`. Qui si cambiano prezzi di vendita
        # su un marketplace vero con le credenziali dell'azienda.
        self._kaufland_solo_amministratori()
        return self._kaufland_notifica(
            "Allineamento Kaufland",
            self._kaufland_riunisci(self._get_connector().per_mercato("allinea")))

    def action_kaufland_allinea_una(self):
        """Allinea UNA sola offerta: la prima prova sul vero si fa così.

        ⚠️ Serve più che nella creazione. Là un errore di configurazione si
        traduce in un rifiuto, e la frenata ferma il giro; qui, se il canale
        ha il listino sbagliato, il giro RIESCE e mette in vendita fino a
        3.000 prezzi sbagliati in una manciata di chiamate. Dai successi
        sbagliati protegge solo una prova in piccolo.
        """
        self.ensure_one()
        self._kaufland_solo_amministratori()
        return self._kaufland_notifica(
            "Prova su una sola offerta",
            self._kaufland_riunisci(
                self._get_connector().per_mercato("allinea", limite=1)))

    @staticmethod
    def _kaufland_riunisci(esiti_per_mercato):
        """Un esito solo a partire da quelli dei singoli mercati.

        ⚠️ I contatori si SOMMANO, ma i mercati restano nominati: «create: 12»
        non dice se sono tutte dell'Italia e la Germania e' ferma. E un
        mercato che ha sollevato porta la sua voce `errore`, che non e' fra le
        CHIAVI_BUONE e quindi tinge la notifica di giallo — che e' il verso
        giusto: meglio un giallo di troppo che un verde su un mercato guasto.
        """
        riunito = {}
        guasti = []
        for mercato, esito in (esiti_per_mercato or {}).items():
            if not isinstance(esito, dict) or "errore" in esito:
                # ⚠️ Un mercato che ha sollevato NON puo' far passare il giro
                # per completo: si annota, e i «si/no» qui sotto lo tengono a
                # falso. Il testo dell'errore va nell'esito, non nel registro
                # e basta: chi ha premuto il bottone deve leggerlo.
                guasti.append("%s: %s" % (
                    mercato,
                    esito.get("errore") if isinstance(esito, dict) else esito))
                continue
            for chiave, valore in esito.items():
                if isinstance(valore, bool):
                    # ⚠️ I «si/no» si uniscono con la E, non si rinominano per
                    # mercato: `completo` dev'essere vero solo se lo e' per
                    # TUTTI. Rinominandoli, chi legge `esito.get("completo", 0)`
                    # trovava la chiave sparita — e su un giro parziale la
                    # notifica sarebbe uscita verde.
                    riunito[chiave] = bool(riunito.get(chiave, True)) and valore
                elif isinstance(valore, int):
                    riunito[chiave] = riunito.get(chiave, 0) + valore
                else:
                    riunito["%s: %s" % (mercato, chiave)] = valore
        if guasti:
            riunito["completo"] = False
            riunito["mercati_in_errore"] = len(guasti)
            riunito["errori"] = " | ".join(guasti)
        # ⚠️ Le chiavi che i chiamanti leggono per nome devono ESSERCI anche
        # quando nessun mercato le ha prodotte (tutti in errore): altrimenti
        # il KeyError seppellisce l'errore vero sotto una traccia di stack.
        riunito.setdefault("completo", False)
        return riunito

    def _kaufland_guasto(self, titolo, esito):
        """La notifica di un giro in cui almeno un mercato ha sollevato.

        ⚠️ Esiste per un difetto vero del 2026-08-29, al primo uso su un canale
        vero: un mercato falliva per una configurazione mancante — «non e'
        indicata alcuna etichetta prodotto», un messaggio chiarissimo — e chi
        aveva premuto il bottone vedeva invece un `KeyError: 'guardati'`,
        perche' i contatori non esistevano e venivano letti lo stesso.

        **Il messaggio del guasto vale piu' dei contatori**: si mostra quello,
        e non si legge nulla che potrebbe non esserci.
        """
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": titolo,
                "message": esito.get("errori") or _("Guasto non descritto."),
                "type": "danger",
                "sticky": True,
            },
        }

    def _kaufland_notifica(self, titolo, esito):
        """La notifica a video di un giro, gialla se il lavoro è parziale.

        ⚠️ Si dichiarano le chiavi BUONE, non quelle guaste, e il verso non è
        indifferente: chi aggiungerà un contatore e si dimenticherà di
        questa riga lo vedrà contato come un guasto — cioè giallo di troppo,
        che si nota e si corregge. Nell'altro verso un guasto nuovo
        passerebbe per verde, e un verde sbagliato su un marketplace vero non
        si nota affatto.
        """
        guasti = sum(valore for chiave, valore in esito.items()
                     if chiave not in CHIAVI_BUONE
                     and isinstance(valore, (int, bool)))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": titolo,
                "message": "; ".join("%s: %s" % (chiave, valore)
                                     for chiave, valore in esito.items()),
                "type": "warning" if guasti else "success",
                "sticky": bool(guasti),
            },
        }

    # ------------------------------------------------------------------
    # Il giro automatico
    # ------------------------------------------------------------------
    def _kaufland_turno_libero(self):
        """Vero se su questo canale non sta già girando qualcos'altro.

        ⚠️ Perché una domanda e non un'eccezione da intercettare: `allinea()`
        prende il turno del canale (`_prendi_il_turno`, un `FOR UPDATE
        NOWAIT`), lo stesso turno della creazione delle offerte. Se un
        operatore sta facendo un giro a mano quando scatta il cron, il turno è
        occupato e `allinea()` solleva una `UserError` — ma quella `UserError`
        è indistinguibile, per tipo, da «manca il listino di vendita», che è
        configurazione sbagliata e va detta come errore. Guardare il turno
        PRIMA è l'unico modo onesto di separare le due cose senza leggere il
        testo di un messaggio tradotto.

        ⚠️ E il lock che si prende qui NON si rilascia subito: resta preso
        fino alla fine della transazione del cron, quindi il `FOR UPDATE` che
        `allinea()` rifà tra un istante è già nostro e non può fallire. La
        finestra fra la domanda e la risposta è chiusa: senza, fra il
        controllo e la chiamata ci starebbe comodo il clic di un operatore.

        ⚠️ Il savepoint è necessario, non decorativo: un'istruzione SQL che
        fallisce lascia la transazione ABORTITA, e da lì in poi anche scrivere
        la riga di registro esploderebbe.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                # Il nome della tabella viene dal modello, non scritto a mano.
                self.env.cr.execute(  # noqa: S608 - `_table` e' interno
                    "SELECT id FROM %s WHERE id = %%s FOR UPDATE NOWAIT"
                    % self._table, (self.id,))
        except Exception as errore:  # noqa: BLE001
            # ⚠️ Solo `lock_not_available` è «c'è un altro giro». Odoo apre i
            # cursori in REPEATABLE READ: un errore di SERIALIZZAZIONE è
            # un'altra cosa, e chiamarla turno occupato direbbe il falso.
            if getattr(errore, "pgcode", None) != LOCK_OCCUPATO:
                raise
            return False
        return True

    @api.model
    def cron_kaufland_allinea_tutti(self):
        """Manda a Kaufland i prezzi e le giacenze di tutti i canali attivi.

        ⚠️ Un canale che esplode non deve fermare gli altri: l'errore si
        registra e il giro continua. Un guasto su Kaufland Italia non è una
        buona ragione per lasciare Kaufland Germania al prezzo di ieri.

        ⚠️ E un TURNO OCCUPATO non è un errore: è il lock che funziona. Se
        ogni ora il registro scrivesse «errore» perché un operatore stava
        lavorando a mano, si imparerebbe a non leggerlo più — e
        l'assuefazione è ciò che nasconde i guasti veri. Si registra come
        `skip`, con il suo perché.

        ⚠️ Si chiama il connettore, non `action_kaufland_allinea`: i bottoni
        hanno la guardia `base.group_system`, che qui non serve (il cron gira
        come `base.user_root`) e che legherebbe un automatismo a un controllo
        pensato per chi clicca.
        """
        canali = self.search([("connector_code", "=", "kaufland"),
                              ("active", "=", True)])
        # ⚠️ I nomi si leggono TUTTI ORA, mentre la transazione è certamente
        # sana. `display_name` è una lettura SQL, e su una transazione già
        # abortita da un guasto del database solleva `InFailedSqlTransaction`:
        # letto dentro un gestore d'errore ucciderebbe il cron proprio nel
        # caso per cui il gestore è stato scritto. Nei gestori si usa la
        # copia, che non tocca il database.
        nomi = {canale.id: canale.display_name for canale in canali}
        for canale in canali:
            nome = nomi[canale.id]
            try:
                # ⚠️ IL SAVEPOINT, e perché intercettare l'eccezione NON
                # basta. È la lezione già pagata tre volte un piano più in
                # basso, riapparsa qui: se `allinea()` fa arrivare un errore
                # DAL DATABASE, la transazione resta ABORTITA. Da lì in poi
                # ogni canale successivo fallisce, e il commit finale del
                # cron diventa un ROLLBACK SILENZIOSO che si porta via anche
                # l'allineamento dei canali già fatti — mentre il registro
                # dice «aggiornate: N». La docstring qui sopra promette che
                # un canale che esplode non ferma gli altri: senza savepoint
                # la promessa regge per gli errori Python e non per quelli
                # del database, che sono proprio i peggiori.
                #
                # ⚠️ E il lock del turno non ci rimette niente: se questo
                # savepoint torna indietro, il canale è comunque saltato, e
                # un lock rilasciato insieme al suo lavoro è giusto così.
                with self.env.cr.savepoint():
                    if not canale._kaufland_turno_libero():
                        _logger.info(
                            "Allineamento Kaufland saltato su %s: un altro "
                            "giro è già in corso.", nome)
                        self._kaufland_registra(canale, nome, "skip", _(
                            "Saltato: un altro giro è già in corso su questo "
                            "canale (di solito un giro a mano). Non è un "
                            "guasto: il prossimo passaggio del cron "
                            "riproverà."))
                        continue
                    canale._get_connector().per_mercato("allinea")
            except Exception as errore:  # noqa: BLE001
                _logger.exception("Allineamento Kaufland fallito su %s", nome)
                self._kaufland_registra(
                    canale, nome, "error",
                    _("Il giro si è interrotto: %s") % errore)
        return True

    def _kaufland_registra(self, canale, nome, esito, messaggio):
        """Una riga nel registro delle operazioni, che non porti via il giro.

        ⚠️ Scrivere il registro è l'ultima cosa che deve poter far cadere un
        cron: se la riga non si scrive (una transazione già abortita da un
        guasto vero del database, per dire), resta comunque il log di sistema
        — e gli altri canali vanno allineati lo stesso.

        ⚠️ Per questo il nome arriva come PAROLA, già letta da chi chiama
        quando la transazione era sana: leggere `canale.display_name` qui
        dentro sarebbe una lettura SQL nel gestore d'errore, cioè la stessa
        eccezione che si sta cercando di sopravvivere. Per la stessa ragione
        l'azienda si legge da `canale.company_id.id` **dentro** il `try`.

        ⚠️ E IL SAVEPOINT NON È DECORATIVO, benché l'eccezione sia già
        catturata qui sotto. Catturare in Python non salva la transazione: se
        questa `create` fallisce con un errore del DATABASE, PostgreSQL la
        lascia ABORTITA, e il canale successivo esplode già nel flush
        d'entrata del suo savepoint — cioè il cron perderebbe tutti i canali
        che vengono dopo, proprio mentre sta scrivendo che uno solo è andato
        male. È la stessa cura del gemello `cdiscount._cdiscount_registra`.
        """
        try:
            with self.env.cr.savepoint():
                self.env["centrivo.job.log"].sudo().create({
                    "channel_id": canale.id,
                    "operation": "kaufland_allinea",
                    "result": esito,
                    "message": messaggio,
                    "company_id": canale.company_id.id,
                })
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Kaufland: non si è potuta scrivere la riga di registro "
                "(%s) del canale %s", esito, nome)
