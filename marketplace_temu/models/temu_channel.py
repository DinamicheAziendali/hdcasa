# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Estensione di centrivo.channel per Temu (campi + bottoni).

Vive nel modulo Temu, non in integrations_core: il core non si tocca.
"""
import logging

from odoo import api, fields, models

from odoo.addons.integrations_core.connectors.base import MarketplaceConnector

_logger = logging.getLogger(__name__)


class CentrivoChannel(models.Model):
    _inherit = "centrivo.channel"

    temu_app_key = fields.Char(
        string="App key Temu",
        help="Chiave dell'applicazione registrata sulla Partner Platform Temu.")
    temu_app_secret = fields.Char(
        string="App secret Temu",
        help="Segreto dell'applicazione: serve SOLO a firmare le chiamate, non "
             "viene mai inviato né scritto nei log.")
    temu_access_token = fields.Char(
        string="Access token Temu",
        help="Token mostrato dal Seller Center dopo l'autorizzazione manuale "
             "dell'app (Sistema → Gestione autorizzazioni): si copia e si "
             "incolla qui.")
    temu_token_expiry = fields.Date(
        string="Scadenza token",
        help="Data di scadenza dell'access token. Si può digitare a mano "
             "copiandola dal Seller Center, ma il valore buono lo scrive qui "
             "il bottone «Verifica token e permessi», che la chiede al token "
             "stesso: quella è la scadenza vera, e sovrascrive quanto "
             "digitato. Quando la data si avvicina o passa, va rifatta "
             "l'autorizzazione dal Seller Center per ottenere un token nuovo "
             "e va incollato qui.")
    # ⚠️ QUI STAVANO QUATTRO CAMPI, TOLTI IL 2026-08-27: `temu_region_id`,
    # `temu_max_sale_delay`, `temu_shipment_limit_day`, `temu_cost_template_id`.
    # Erano l'impronta della PUBBLICAZIONE DELLE SCHEDE, che e' fuori
    # perimetro: le schede le crea l'interfaccia Temu da file, questo modulo le
    # aggancia e tiene allineati prezzo e quantita'. Nessuna riga di codice li
    # leggeva: chi li compilava credeva di aver configurato qualcosa. Se un
    # giorno la pubblicazione rientra nel perimetro, si rimettono INSIEME al
    # codice che li usa, mai prima.
    temu_currency = fields.Char(
        string="Valuta", default="EUR",
        help="Valuta dei prezzi inviati a Temu.")
    temu_order_status = fields.Selection(
        selection=[("1", "In attesa"), ("2", "Da spedire"), ("4", "Spedito"),
                   ("5", "Consegnato"), ("0", "Tutti")],
        string="Stato ordini da scaricare", default="2",
        help="Da quale stato si scaricano gli ordini. Il valore giusto e' "
             "'Da spedire': finche' l'ordine e' 'In attesa' il cliente puo' "
             "ancora cambiare indirizzo e quantita', e Temu stessa raccomanda "
             "di non lavorarlo. 'Tutti' serve solo per ispezionare, non per "
             "importare.")
    temu_orders_from = fields.Date(
        string="Importa ordini dal",
        help="Gli ordini creati prima di questa data vengono ignorati. Serve a "
             "non tirare dentro lo storico gia' fatturato altrove: il negozio "
             "ha centinaia di ordini vecchi che in Odoo sarebbero solo rumore. "
             "Vuoto = nessun limite.")
    temu_import_on_pull = fields.Boolean(
        string="Importa subito dopo lo scarico", default=True,
        help="Se attivo, ogni ordine scaricato viene subito tradotto in ordine "
             "Odoo. Se spento, gli ordini restano in elenco come 'In attesa' e "
             "si importano a mano.")
    temu_shipping_product_id = fields.Many2one(
        "product.product", string="Articolo spese di spedizione",
        help="Articolo usato per portare in Odoo le spese di spedizione "
             "incassate da Temu. Se vuoto, le spese non finiscono nell'ordine "
             "e il totale Odoo sara' piu' basso di quello Temu.")

    temu_allow_zero_stock = fields.Boolean(
        string="Consenti azzeramento di massa", default=False,
        help="Normalmente il connettore si RIFIUTA di azzerare quasi tutte le "
             "giacenze in un colpo solo: quando succede, quasi sempre e' Odoo "
             "che non conosce le giacenze (prodotti senza stock, magazzino o "
             "azienda sbagliati), non il magazzino che si e' svuotato. "
             "Attivalo solo se l'azzeramento e' davvero quello che vuoi.")

    temu_pricelist_id = fields.Many2one(
        "product.pricelist", string="Listino prezzi base Temu",
        help="Listino da cui si prende il prezzo da comunicare a Temu. "
             "ATTENZIONE: il prezzo di Temu NON e' quello al pubblico, e' "
             "l'importo che incassi tu — Temu ci aggiunge il proprio margine "
             "e le proprie promozioni. Per questo serve un listino dedicato e "
             "non si puo' riusare quello degli altri marketplace. Se vuoto, i "
             "prezzi NON vengono inviati.")

    temu_warehouse_id = fields.Char(
        string="Magazzino di partenza (Temu)", readonly=True,
        help="Identificativo del magazzino da cui partono le spedizioni, letto "
             "da Temu alla prima comunicazione e poi tenuto: non cambia, e "
             "richiederlo ogni volta sarebbe una chiamata sprecata.")

    temu_simulate = fields.Boolean(
        string="Simula (non invia)", default=True,
        help="Se attivo, le operazioni di scrittura costruiscono il messaggio "
             "e lo scrivono nel Log operazioni SENZA inviarlo a Temu. Si "
             "spegne solo quando il contenuto è stato verificato.")
    temu_token_days_left = fields.Integer(
        string="Giorni alla scadenza del token",
        compute="_compute_temu_token_days_left",
        help="Quanto manca alla scadenza dell'access token. Quando scade, il "
             "connettore si ferma del tutto: nessun ordine, nessuna giacenza, "
             "nessun prezzo. Il token si rinnova rifacendo l'autorizzazione "
             "dell'app nel Seller Center e incollando qui quello nuovo.")

    temu_last_recon = fields.Datetime(
        string="Ultima ricognizione", readonly=True)
    temu_listing_count = fields.Integer(
        string="Schede Temu", compute="_compute_temu_listing_count")

    @api.depends("temu_token_expiry")
    def _compute_temu_token_days_left(self):
        oggi = fields.Date.context_today(self)
        for channel in self:
            if channel.temu_token_expiry:
                channel.temu_token_days_left = (
                    channel.temu_token_expiry - oggi).days
            else:
                channel.temu_token_days_left = 0

    @api.model
    def cron_temu_check_token(self):
        """Avvisa quando il token Temu sta per scadere.

        Esiste perche' la scadenza, finora, era solo una data scritta a mano su
        un campo che nessuno guarda. Un token scaduto non da' un errore
        comprensibile: da' un rifiuto su OGNI chiamata, e si perde tempo a
        cercare il guasto altrove. Meglio un avviso trenta giorni prima.

        Cron SPENTO di default, come tutti gli altri del modulo: si accende a
        mano quando si e' pronti.
        """
        soglia = 30
        for channel in self.search([("connector_code", "=", "temu"),
                                    ("active", "=", True)]):
            if not channel.temu_token_expiry:
                continue
            mancano = channel.temu_token_days_left
            if mancano > soglia:
                continue
            if mancano < 0:
                messaggio = ("Il token Temu del canale '%s' e' SCADUTO il %s: "
                             "il connettore non puo' piu' fare nulla finche' "
                             "non viene rinnovato."
                             % (channel.name, channel.temu_token_expiry))
            else:
                messaggio = ("Il token Temu del canale '%s' scade fra %s giorni "
                             "(%s). Va rifatta l'autorizzazione dell'app nel "
                             "Seller Center e incollato qui il token nuovo."
                             % (channel.name, mancano, channel.temu_token_expiry))
            self.env["centrivo.job.log"].sudo().create({
                "channel_id": channel.id,
                "operation": "temu_token_check",
                "result": "error" if mancano < 0 else "skip",
                "message": messaggio,
                "company_id": channel.company_id.id,
            })
            # ⚠️ QUI C'ERA UN AVVISO CHE NON POTEVA PARTIRE. Fino al
            # 2026-08-27 questo punto chiamava
            # `channel.activity_schedule(...)` sull'utente configurato in
            # `error_activity_user_id`. Ma `centrivo.channel`
            # (integrations_core) NON eredita `mail.thread` ne'
            # `mail.activity.mixin`: quel metodo sul canale non esiste, e la
            # chiamata sollevava `AttributeError` — cioe' il controllo della
            # scadenza si rompeva proprio nel giro in cui doveva servire, e
            # per giunta dopo aver scritto la riga di registro, lasciando il
            # cron rotto a meta'.
            #
            # ⚠️ Una chiamata che non puo' funzionare e' PEGGIO di nessuna
            # chiamata: da' l'illusione dell'avviso a chi legge il codice e
            # a chi valorizza il campo sul canale, e nessuno dei due scopre
            # niente finche' il token non scade davvero. Qui non si e' tolta
            # una funzionalita' che funzionava: si e' tolta una che non e'
            # mai potuta funzionare, e la si e' resa VISIBILE, con una riga
            # di registro che dice a chiare lettere che l'avviso non parte.
            #
            # ⚠️ IL DEBITO RESTA APERTO, e la decisione non e' di questo
            # modulo: aggiungere i due mixin a `centrivo.channel` tocca
            # `integrations_core`, che e' IN PRODUZIONE sotto ManoMano e
            # BricoBravo. Il precedente di casa e' Cdiscount, che lo stesso
            # problema l'ha risolto SENZA toccare il nucleo: i mixin stanno
            # sul modello foglia `cdiscount.pacchetto`
            # (`marketplace_cdiscount/models/cdiscount_pacchetto.py`), e
            # l'attivita' si programma li'. La stessa strada, qui, vuole un
            # modello foglia Temu che oggi non c'e'.
            if channel.error_activity_user_id:
                self.env["centrivo.job.log"].sudo().create({
                    "channel_id": channel.id,
                    "operation": "temu_token_check",
                    "result": "skip",
                    "message": (
                        "Andrebbe avvisato %s, l'utente configurato su questo "
                        "canale per gli errori: l'avviso NON puo' partire "
                        "perche' centrivo.channel non ha i meccanismi delle "
                        "attivita' (mail.thread / mail.activity.mixin). "
                        "Finche' resta cosi', questa riga di registro e' "
                        "l'unico avviso: va guardata a mano."
                        % channel.error_activity_user_id.name),
                    "company_id": channel.company_id.id,
                })
        return True

    def _compute_temu_listing_count(self):
        Listing = self.env["centrivo.temu.listing"]
        for channel in self:
            channel.temu_listing_count = Listing.search_count(
                [("channel_id", "=", channel.id)])

    def action_temu_open_listings(self):
        """Apre il registro schede filtrato su questo canale."""
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "marketplace_temu.action_temu_listing")
        action["domain"] = [("channel_id", "=", self.id)]
        action["context"] = {"default_channel_id": self.id}
        return action

    def action_temu_verifica_collegamento(self):
        """Chiede al token cosa sa di se': scadenza vera e chiamate scoperte.

        ⚠️ E' la chiamata con cui e' stato diagnosticato il blocco degli
        importi. Prima di questo bottone, sapere quali chiamate fossero
        autorizzate voleva dire mezza giornata di prove a mano.
        """
        self.ensure_one()
        connector = MarketplaceConnector.for_channel(self)
        r = connector.verifica_collegamento()
        # ⚠️ VERDE SOLO SE L'ESITO E' DAVVERO POSITIVO, come dice la docstring
        # di `_temu_notification` due schermate piu' sotto. Il confronto e' con
        # la LISTA VUOTA, e non e' pignoleria: `scoperte` vale None su tutte e
        # tre le uscite d'errore — 502, rete caduta, token rifiutato — e anche
        # quando la risposta non porta l'elenco dei permessi. None e' falsy,
        # quindi `if r.get("scoperte")` avrebbe dipinto di verde proprio i
        # quattro casi in cui non si e' saputo niente. E il popup e' la sola
        # cosa che Angelo vede: la riga di registro sta in un'altra schermata.
        buono = r.get("scoperte") == [] and r.get("scadenza_scritta")
        return self._temu_notification(
            r.get("motivo") or "Verifica eseguita.",
            kind="success" if buono else "warning")

    def action_temu_recon(self):
        """Ricognizione del negozio Temu: legge e basta, non scrive su Temu."""
        self.ensure_one()
        connector = MarketplaceConnector.for_channel(self)
        riepilogo = connector.recon_catalog()
        if riepilogo.get("errore"):
            # ⚠️ E la data NON si scrive: prima si scriveva PRIMA di guardare
            # l'esito, cosi' «Ultima ricognizione» diceva oggi anche su un
            # giro morto a pagina 1. Un campo che dice «fatto oggi» e' proprio
            # cio' che impedisce a qualcuno di rifare il giro.
            return self._temu_notification(
                "Ricognizione non completata: %s. Dettagli nel Log operazioni."
                % riepilogo["errore"], kind="danger")
        # Da qui in poi la ricognizione e' arrivata in fondo: anche un negozio
        # letto e trovato vuoto e' una ricognizione fatta, e la data e' vera.
        self.temu_last_recon = fields.Datetime.now()
        if riepilogo.get("prima_pagina_vuota"):
            return self._temu_notification(
                "Nessuna SKU letta da Temu. Se il negozio non è vuoto, apri "
                "il Log operazioni: il corpo grezzo della risposta dice se i "
                "nomi dei campi sono cambiati.", kind="warning")
        messaggio = ("%(letti)s SKU lette, %(agganciati)s agganciate ai "
                     "prodotti Odoo, %(orfani)s orfane." % riepilogo)
        # Da quali filtri arrivano: Temu non ha un elenco "tutto" e le SKU
        # stanno in piu' contenitori. Mostrarne la ripartizione evita di
        # scambiare per completo un catalogo letto a meta'.
        contenitori = riepilogo.get("per_contenitore") or {}
        if len(contenitori) > 1:
            messaggio += " Provenienza: %s." % ", ".join(
                "filtro %s: %s" % (t, n) for t, n in sorted(contenitori.items()))
        if riepilogo.get("prezzo_rifiutato"):
            messaggio += (" %(prezzo_rifiutato)s con prezzo rifiutato o "
                          "delistate per prezzo." % riepilogo)
        return self._temu_notification(
            messaggio, kind="warning" if riepilogo["orfani"] else "success")

    def action_temu_push_stock(self):
        """Allinea le giacenze di Temu a quelle di Odoo."""
        self.ensure_one()
        connector = MarketplaceConnector.for_channel(self)
        r = connector.push_stock()
        if r.get("errore"):
            return self._temu_notification(r["errore"], kind="danger")
        messaggio = ("%(aggiornate)s giacenze aggiornate, %(invariate)s gia' "
                     "allineate, %(senza_prodotto)s schede senza prodotto Odoo."
                     % r)
        if self.temu_simulate:
            messaggio = "SIMULAZIONE (nulla e' stato inviato). " + messaggio
        if r.get("errori"):
            messaggio += " %s errori: vedi il Log operazioni." % r["errori"]
        return self._temu_notification(
            messaggio, kind="warning" if r.get("errori") else "success")

    def action_temu_push_prices(self):
        """Propone a Temu i prezzi base dal listino dedicato."""
        self.ensure_one()
        connector = MarketplaceConnector.for_channel(self)
        r = connector.push_prices()
        if r.get("errore"):
            return self._temu_notification(r["errore"], kind="danger")
        messaggio = ("%(proposte)s prezzi proposti, %(invariate)s gia' "
                     "allineati, %(senza_prezzo)s senza prezzo a listino." % r)
        if self.temu_simulate:
            messaggio = "SIMULAZIONE (nulla e' stato inviato). " + messaggio
        elif r.get("pratiche"):
            messaggio += (" I prezzi NON cambiano subito: Temu ha aperto %s "
                          "pratiche di revisione." % len(r["pratiche"]))
        return self._temu_notification(
            messaggio, kind="warning" if r.get("errori") else "success")

    def action_temu_check_stock(self):
        """Confronta le giacenze di Temu con quelle di Odoo. Non scrive nulla."""
        self.ensure_one()
        connector = MarketplaceConnector.for_channel(self)
        r = connector.check_stock()
        messaggio = ("Confronto giacenze: %(allineate)s allineate, "
                     "%(diverse)s diverse, %(illeggibili)s non leggibili. "
                     "Dettaglio nel Log operazioni." % r)
        # ⚠️ VERDE SOLO SE SI E' DAVVERO LETTO TUTTO. Con tutte le letture
        # fallite il conto e' 0 allineate, 0 diverse, N illeggibili: un verde
        # qui direbbe «siamo allineati» su ZERO letture. Ed e' il semaforo che
        # autorizza ad accendere l'invio automatico su un catalogo pubblico,
        # quindi la riga non letta pesa piu' della riga diversa: rosso.
        if r.get("illeggibili"):
            messaggio += (" ⚠️ Le righe non lette NON sono allineate: sono "
                          "ignote. Questo confronto non autorizza a niente "
                          "finche' restano.")
            colore = "danger"
        elif r.get("diverse"):
            colore = "warning"
        else:
            colore = "success"
        return self._temu_notification(messaggio, kind=colore)

    def action_temu_check_price_orders(self):
        """Rilegge l'esito delle pratiche di revisione prezzo aperte.

        ⚠️ Serve perche' oggi si apre una pratica, il numero finisce sulla
        scheda, e nessuno rilegge mai se Temu l'ha accettata.
        """
        self.ensure_one()
        connector = MarketplaceConnector.for_channel(self)
        r = connector.check_price_orders()
        # ⚠️ «Pratiche prezzo aperte: 0» su una lettura FALLITA e' la stessa
        # bugia del verde: zero pratiche aperte si legge come «tutto a posto»,
        # mentre non si e' saputo niente. L'elenco vuoto e' vuoto per forza
        # quando la chiamata non e' arrivata: il numero da solo non distingue.
        if not r.get("letto"):
            return self._temu_notification(
                "Pratiche prezzo NON lette: %s. Non si sa quante siano "
                "aperte. Il motivo e' nel Log operazioni."
                % (r.get("motivo") or "motivo non dato"), kind="danger")
        return self._temu_notification(
            "Pratiche prezzo aperte: %s. Dettaglio nel Log operazioni."
            % len(r.get("pratiche") or []), kind="info")

    # ------------------------------------------------------------------
    # Il cron di prezzi e giacenze
    # ------------------------------------------------------------------
    def _temu_turno_libero(self):
        """Vero se su questo canale non sta gia' girando qualcos'altro.

        ⚠️ Perche' una domanda e non un'eccezione da intercettare:
        `push_stock` e `push_prices` prendono il turno del canale
        (`_prendi_il_turno`, un `FOR UPDATE NOWAIT`). Se un operatore sta
        facendo un giro a mano quando scatta il cron, il turno e' occupato e
        quei metodi sollevano una `UserError` — ma quella `UserError` e'
        indistinguibile, per tipo, da «manca il listino Temu», che e'
        configurazione sbagliata e va detta come errore. Guardare il turno
        PRIMA e' l'unico modo onesto di separare le due cose senza leggere il
        testo di un messaggio tradotto.

        ⚠️ E il lock che si prende qui NON si rilascia subito: resta preso
        fino alla fine della transazione del cron, quindi il `FOR UPDATE` che
        `push_stock` rifa' tra un istante e' gia' nostro e non puo' fallire.
        La finestra fra la domanda e la risposta e' chiusa: senza, fra il
        controllo e la chiamata ci starebbe comodo il clic di un operatore.

        ⚠️ Il savepoint e' necessario, non decorativo: un'istruzione SQL che
        fallisce lascia la transazione ABORTITA, e da li' in poi anche
        scrivere la riga di registro esploderebbe.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                # Il nome della tabella viene dal modello, non scritto a mano.
                self.env.cr.execute(  # noqa: S608 - `_table` e' interno
                    "SELECT id FROM %s WHERE id = %%s FOR UPDATE NOWAIT"
                    % self._table, (self.id,))
        except Exception as errore:  # noqa: BLE001
            # ⚠️ Solo `lock_not_available` e' «c'e' un altro giro». Odoo apre
            # i cursori in REPEATABLE READ: un errore di SERIALIZZAZIONE e'
            # un'altra cosa, e chiamarla turno occupato direbbe il falso
            # sopprimendo il ritentativo che Odoo fa da solo.
            occupato = MarketplaceConnector.LOCK_OCCUPATO
            if getattr(errore, "pgcode", None) != occupato:
                raise
            return False
        return True

    @api.model
    def cron_temu_push_offers(self):
        """Manda a Temu giacenze e prezzi di tutti i canali Temu attivi.

        Cron SPENTO di default, come tutti quelli del modulo.

        ⚠️ Un canale che esplode non deve fermare gli altri: l'errore si
        registra e il giro continua. Un guasto sul negozio italiano non e' una
        buona ragione per lasciare l'altro al prezzo di ieri.

        ⚠️ E UN TURNO OCCUPATO NON E' UN ERRORE: e' il lock che funziona.
        Se ogni ora il registro scrivesse «errore» perche' un operatore stava
        lavorando a mano, si imparerebbe a non leggerlo piu' — e
        l'assuefazione e' cio' che nasconde i guasti veri. Si registra come
        `skip`, con il suo perche'.

        ⚠️ Si chiama il connettore, non i bottoni `action_temu_push_*`:
        quelli restituiscono una notifica a video, che dentro un cron non la
        vede nessuno.
        """
        canali = self.search([("connector_code", "=", "temu"),
                              ("active", "=", True)])
        # ⚠️ I nomi si leggono TUTTI ORA, mentre la transazione e' certamente
        # sana. `display_name` e' una lettura SQL, e su una transazione gia'
        # abortita da un guasto del database solleva `InFailedSqlTransaction`:
        # letto dentro un gestore d'errore ucciderebbe il cron proprio nel
        # caso per cui il gestore e' stato scritto.
        nomi = {canale.id: canale.display_name for canale in canali}
        for canale in canali:
            nome = nomi[canale.id]
            try:
                # ⚠️ IL SAVEPOINT, e perche' intercettare l'eccezione NON
                # basta: se il giro fa arrivare un errore DAL DATABASE la
                # transazione resta ABORTITA, da li' in poi ogni canale
                # successivo fallisce, e il commit finale del cron diventa un
                # ROLLBACK SILENZIOSO che si porta via anche il lavoro dei
                # canali gia' fatti.
                with self.env.cr.savepoint():
                    if not canale._temu_turno_libero():
                        _logger.info(
                            "Giro Temu saltato su %s: un altro giro e' gia' "
                            "in corso.", nome)
                        self._temu_registra(canale, nome, "skip", (
                            "Saltato: un altro giro e' gia' in corso su "
                            "questo canale (di solito un giro a mano). Non e' "
                            "un guasto: il prossimo passaggio del cron "
                            "riprovera'."))
                        continue
                    connettore = MarketplaceConnector.for_channel(canale)
                    connettore.push_stock()
                    connettore.push_prices()
            except Exception as errore:  # noqa: BLE001
                _logger.exception("Giro Temu fallito su %s", nome)
                self._temu_registra(
                    canale, nome, "error",
                    "Il giro si e' interrotto: %s" % errore)
        return True

    def _temu_registra(self, canale, nome, esito, messaggio):
        """Una riga nel registro delle operazioni, che non porti via il giro.

        ⚠️ Scrivere il registro e' l'ultima cosa che deve poter far cadere un
        cron: se la riga non si scrive (una transazione gia' abortita da un
        guasto vero del database, per dire), resta comunque il log di sistema
        — e gli altri canali vanno lavorati lo stesso.

        ⚠️ Per questo il nome arriva come PAROLA, gia' letta da chi chiama
        quando la transazione era sana: leggere `canale.display_name` qui
        dentro sarebbe una lettura SQL nel gestore d'errore, cioe' la stessa
        eccezione che si sta cercando di sopravvivere. Per la stessa ragione
        l'azienda si legge da `canale.company_id.id` **dentro** il `try`.

        ⚠️ E IL SAVEPOINT NON E' DECORATIVO, benche' l'eccezione sia gia'
        catturata qui sotto: catturare in Python non salva la transazione.
        """
        try:
            with self.env.cr.savepoint():
                self.env["centrivo.job.log"].sudo().create({
                    "channel_id": canale.id,
                    "operation": "temu_push_offers",
                    "result": esito,
                    "message": messaggio,
                    "company_id": canale.company_id.id,
                })
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Temu: non si e' potuta scrivere la riga di registro (%s) "
                "del canale %s", esito, nome)

    def _temu_notification(self, message, kind="info", title="Temu"):
        """Popup non bloccante. Verde SOLO se l'esito è davvero positivo."""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title, "message": message, "type": kind,
                       "sticky": False},
        }
