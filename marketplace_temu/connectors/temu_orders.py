# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Ciclo ordini Temu: scarico ed import in Odoo.

DECISIONI PRESE CON ANGELO (2026-08-20), da non rimettere in discussione:
  - si importa SOLO dallo stato "Da spedire": in "In attesa" il cliente puo'
    ancora cambiare indirizzo e quantita', e la Order Guide raccomanda di non
    lavorare l'ordine (docs/temu-ciclo-ordini-studio.md §1);
  - lo storico NON si importa: si parte dalla data impostata sul canale;
  - il prezzo di riga e' quello PAGATO DAL CLIENTE, perche' quello e' il totale
    dell'ordine per noi;
  - nessuna accettazione ordine: su Temu non esiste, gli ordini avanzano da soli;
  - cancellazioni e resi si gestiscono a mano nel Seller Center.

⚠️ IMPORTI: arrivano da una chiamata a parte (`bg.order.amount.query`). Al
2026-08-27 quella chiamata NON e' autorizzata sul nostro token: risponde
3000032. Non e' un sospetto ed e' inutile riprovare a caso: sono state provate
tutte le versioni, la v1 non esiste (3000003) e le altre tre esistono ma sono
negate. La causa non e' un permesso revocato: quell'interfaccia non e' mai
stata dichiarata quando l'app e' stata registrata, e dal pannello non si
aggiunge a un'app gia' approvata. Le misure, il ticket e il testo gia' pronto
da mandare stanno in centrivo-hub/docs/richiesta-a-temu-permesso-importi.md.
Finche' non e' autorizzata l'import si FERMA con un errore parlante invece di
creare ordini con prezzi inventati.
"""
import json
import logging

from .temu_orders_parser import (
    ORDER_STATUS_LABELS, STATUS_DA_SPEDIRE, parse_order_page, righe_da_spedire)

_logger = logging.getLogger(__name__)

# ⚠️ Il valore che `import_order` rende quando l'ordine NON e' entrato.
# (Si chiama cosi' e non "FALLITO" per una ragione pratica: la batteria dei
# banchi cerca la parola "FALLITO" per contare i controlli caduti, e un nome
# che compare nelle etichette dei controlli la farebbe suonare a vuoto.)
# Prima i tre punti d'uscita rendevano `self._temu_errore_ordine(...)`, che
# rende questo valore PER COSTRUZIONE. Da quando la scrittura di servizio
# passa da un savepoint quel valore va scritto, e scriverlo a mano in quattro
# posti diverse volte invita a sbagliarne uno: `pull_orders` conta su di lui
# (`if self.import_order(...)`), e un valore vero farebbe risultare
# «importato» un ordine che non e' mai entrato in banca dati.
NON_IMPORTATO = False

API_ORDER_LIST = "bg.order.list.v2.get"
API_ORDER_DETAIL = "bg.order.detail.v2.get"
API_SHIPPING_DECRYPT = "bg.order.decryptshippinginfo.get"

# Importi dell'ordine.
#
# ⚠️ ATTENZIONE ALLA VERSIONE. La guida "Order Amount V1/V2/V3" elenca la v2
# fra le chiamate di GIAPPONE e COREA. Il nostro sito e' l'Italia: la chiamata
# e' quella senza versione. Il commento precedente diceva il contrario e ha
# retto per una settimana, perche' il rifiuto che si riceve (3000032, permesso
# mancante) e' lo stesso che si riceverebbe con la chiamata giusta.
API_ORDER_AMOUNT = "bg.order.amount.query"

# La variante Giappone/Corea. Non si usa e non si cancella: senza questa riga
# il prossimo che legge la guida rifa' lo stesso ragionamento da capo.
API_ORDER_AMOUNT_JP_KR = "temu.order.amount.v2.query"

# ⚠️ Le interfacce che il modulo chiama e che NON compaiono nell'elenco
# dichiarato al momento della registrazione dell'app
# (docs/temu-registrazione-app.md). Il permesso sugli importi non e' stato
# tolto: non e' mai stato chiesto. Vanno chieste TUTTE INSIEME, perche' la
# domanda si fa una volta sola.
INTERFACCE_DA_CHIEDERE = (
    API_ORDER_AMOUNT,
    "bg.logistics.shipment.v2.confirm",
    "bg.logistics.shipment.v2.get",
    "bg.logistics.warehouse.list.get",
    "bg.local.goods.priceorder.query",
    "temu.local.goods.sku.stock.query",
    "temu.pay.tax.get.galerie.signature",
    "temu.pay.tax.merchant.upload.invoice",
)

# Dove sta la pratica: le prove fatte, la risposta di Temu al ticket e il testo
# gia' pronto da rimandare. E' in un altro repo perche' la diagnosi l'ha fatta
# l'hub, ma riguarda questo modulo.
DOC_PRATICA = "centrivo-hub/docs/richiesta-a-temu-permesso-importi.md"

# ⚠️ Il parametro di pagina qui si chiama `pageNumber`, NON `pageNo` come
# nell'elenco SKU. Stessa piattaforma, nome diverso: un parametro di pagina
# sbagliato non da' errore, fa rileggere la prima pagina all'infinito.
PAGE_PARAM = "pageNumber"
PAGE_SIZE = 100          # massimo dichiarato dalla documentazione
MAX_PAGES = 200          # tetto di sicurezza: 20.000 ordini


class TemuOrdersMixin(object):
    """Metodi del ciclo ordini del connettore Temu."""

    # ------------------------------------------------------------------
    # SCARICO
    # ------------------------------------------------------------------
    def _temu_registra(self, esito, messaggio, payload=None):
        """Una riga di registro del giro ordini, che non puo' far esplodere il giro.

        ⚠️ `_log` fa un `create`, cioe' una SCRITTURA: su una transazione
        abortita esplode verso l'alto, e da `pull_orders` un'eccezione non
        deve uscire (vedi la sua docstring). Passa quindi da un savepoint, e il
        valore reso si legge — un `False` e' l'unica traccia che la riga non
        e' finita nel registro. Il messaggio arriva gia' composto: nominarlo
        nel ramo d'errore e' sicuro anche quando il database non risponde piu'.

        ⚠️ Esiste perche' le righe del giro sono SEI, e sei blocchi copiati
        sono sei occasioni di dimenticarne uno: e' esattamente cosi' che le
        cinque `_log` nude sono sopravvissute al primo giro di questa cura.
        """
        if not self._al_riparo(self._log, "pull_orders", esito, messaggio,
                               payload=payload):
            _logger.error("Temu: riga di registro del giro ordini non "
                          "scritta [%s]. %s", esito, messaggio)

    def pull_orders(self, start_datetime=None, end_datetime=None, status=None):
        """Scarica gli ordini da Temu e ne registra la traccia. NON importa.

        Ritorna il riepilogo. Come per la ricognizione catalogo, tiene un
        insieme degli ordini gia' visti: se la paginazione non avanzasse, il
        giro si ferma e lo dichiara invece di contare piu' volte le stesse
        righe.

        ⚠️ SOLLEVA UNA COSA SOLA, e prima di toccare qualunque cosa: la
        `UserError` del turno, quando un altro giro e' gia' in corso su questo
        canale. Da li' in poi non solleva piu', e non e' pignoleria: ogni
        scrittura del giro sta dentro un savepoint perche' un'eccezione che
        uscisse di qui ucciderebbe la passata di cron di TUTTI i canali di
        TUTTI i marketplace — il gestore del cron nel nucleo scrive a sua volta
        sulla stessa transazione, e su una transazione abortita quella
        scrittura esplode a sua volta. La `UserError` del turno e' l'unica
        eccezione che quel gestore sa gia' isolare per canale.
        """
        # ⚠️ IL TURNO PER PRIMO, come negli altri tre giri — era l'unico dei
        # quattro a non prenderlo. Il cron e il bottone possono partire
        # insieme: entrambi farebbero la `search` a vuoto sullo stesso ordine e
        # poi COLLIDEREBBERO sull'INSERT, perche' `centrivo.order.map` ha un
        # `unique(channel_id, external_id)`. E una violazione di vincolo non e'
        # un errore che si cattura e via: abortisce la transazione.
        self._prendi_il_turno("scarico degli ordini")
        channel = self.channel
        # L'azienda si legge UNA volta sola e fuori dal ciclo. Non e' solo
        # risparmio: piu' sotto finisce fra gli argomenti di `_al_riparo`, e
        # gli argomenti si valutano PRIMA che il savepoint esista.
        azienda_id = channel.company_id.id
        riepilogo = {"letti": 0, "nuovi": 0, "gia_visti": 0, "pagine": 0,
                     "errore": None, "importati": 0, "falliti": 0,
                     "non_registrati": 0, "per_stato": {}}

        if not (channel.temu_app_key and channel.temu_app_secret
                and channel.temu_access_token):
            riepilogo["errore"] = ("Credenziali Temu incomplete sul canale: "
                                   "servono app key, app secret e access token.")
            self._temu_registra("error", riepilogo["errore"])
            return riepilogo

        stato = status or channel.temu_order_status or STATUS_DA_SPEDIRE
        OrderMap = self.env["centrivo.order.map"]
        visti = set()
        pagina = 1

        while pagina <= MAX_PAGES:
            params = {
                PAGE_PARAM: pagina,
                "pageSize": PAGE_SIZE,
                "parentOrderStatus": int(stato) if str(stato).isdigit() else 0,
            }
            # La finestra temporale va SEMPRE a coppie: Temu rifiuta un estremo
            # solo. Si usa la data di aggiornamento, non quella di creazione:
            # e' quella che intercetta anche gli ordini vecchi che cambiano
            # stato adesso.
            inizio = self._temu_epoch(start_datetime)
            fine = self._temu_epoch(end_datetime)
            if inizio and fine:
                params["updateAtStart"] = inizio
                params["updateAtEnd"] = fine

            result = self.client.call(API_ORDER_LIST, params)
            if not result.ok:
                riepilogo["errore"] = "%s %s" % (result.error_code or "",
                                                 result.error_msg or "")
                self._temu_registra(
                    "error",
                    "Scarico interrotto alla pagina %s: %s"
                    % (pagina, riepilogo["errore"]),
                    payload=json.dumps(result.raw, ensure_ascii=False))
                return riepilogo

            ordini, _totale, grezzi = parse_order_page(result.data)
            if grezzi == 0:
                if pagina == 1:
                    self._temu_registra(
                        "skip",
                        "Nessun ordine nello stato '%s'. Se non e' quello "
                        "che ti aspetti, il corpo grezzo nel payload dice "
                        "se i nomi dei campi sono cambiati."
                        % ORDER_STATUS_LABELS.get(str(stato), stato),
                        payload=json.dumps(result.raw,
                                           ensure_ascii=False)[:20000])
                break

            nuovi_sn = {o["parent_order_sn"] for o in ordini} - visti
            if ordini and not nuovi_sn:
                self._temu_registra(
                    "error",
                    "La paginazione non avanza: la pagina %s contiene "
                    "solo ordini gia' letti." % pagina)
                riepilogo["errore"] = "la paginazione non avanza"
                break
            visti |= nuovi_sn

            for ordine in ordini:
                if ordine["parent_order_sn"] not in nuovi_sn:
                    continue
                if self._temu_ordine_troppo_vecchio(ordine):
                    continue
                riepilogo["letti"] += 1
                stato_o = ordine.get("status") or ""
                riepilogo["per_stato"][stato_o] = (
                    riepilogo["per_stato"].get(stato_o, 0) + 1)
                esistente = OrderMap.search([
                    ("channel_id", "=", channel.id),
                    ("external_id", "=", ordine["parent_order_sn"]),
                ], limit=1)
                if esistente:
                    riepilogo["gia_visti"] += 1
                # ⚠️ ANCHE QUESTA `create` PASSA DA UN SAVEPOINT, e non e'
                # prudenza generica: `centrivo.order.map` ha un
                # `unique(channel_id, external_id)`, e una violazione di
                # vincolo ABORTISCE la transazione. Nuda, moriva qui tutto il
                # giro — riga di chiusura mai scritta, eccezione verso l'alto,
                # e dietro la passata di cron di tutti i marketplace.
                elif not self._al_riparo(OrderMap.create, {
                        "channel_id": channel.id,
                        "external_id": ordine["parent_order_sn"],
                        "state": "pending",
                        "company_id": azienda_id,
                }):
                    # Il contatore sta QUI e non e' `falliti`: quello conta gli
                    # ordini che non sono entrati in Odoo, questo gli ordini di
                    # cui non e' rimasta nemmeno la traccia. Al giro dopo si
                    # rileggono da Temu.
                    riepilogo["non_registrati"] += 1
                    _logger.error(
                        "Temu: la traccia dell'ordine %s non si e' potuta "
                        "scrivere; si riprova al giro dopo.",
                        ordine["parent_order_sn"])
                else:
                    riepilogo["nuovi"] += 1

            riepilogo["pagine"] = pagina
            if grezzi < PAGE_SIZE:
                break
            pagina += 1
        else:
            self._temu_registra(
                "error",
                "Raggiunto il tetto di %s pagine: l'elenco NON e' "
                "completo." % MAX_PAGES)
            riepilogo["errore"] = "elenco troncato al tetto di pagine"

        # IMPORT degli ordini nuovi, uno per uno e ISOLATI DA UN SAVEPOINT: un
        # ordine che fallisce non deve impedire agli altri di entrare.
        #
        # ⚠️ QUESTA FRASE E' RIMASTA FALSA A LUNGO, e vale la pena dire
        # perche'. Il solo `_al_riparo` sulla scrittura dell'errore, qui sotto,
        # NON bastava: `import_order` scrive (cliente, ordine, righe) e l'ORM
        # tiene quelle scritture IN CANNA, non le manda subito. Il
        # `cr.savepoint()` di Odoo fa un flush ENTRANDO, cioe' PRIMA che il
        # savepoint esista: la roba in canna partiva quindi all'ingresso del
        # savepoint di `_al_riparo`, FUORI da qualunque protezione. Due danni:
        #   1. un ordine scritto a META' restava in banca dati — mezzo cliente,
        #      un ordine senza righe — mentre il registro lo contava fra i
        #      falliti;
        #   2. se una di quelle scritture rompeva, la transazione restava
        #      ABORTITA: da li' ogni ordine successivo falliva, e la riga di
        #      chiusura esplodeva verso l'alto. Il gestore del cron nel nucleo
        #      scrive a sua volta sulla stessa transazione, quindi la passata
        #      moriva per TUTTI i canali di TUTTI i marketplace, non solo per
        #      Temu.
        # Il savepoint per ordine chiude tutte e due: o l'ordine entra tutto,
        # o non e' successo niente. E' lo schema di `raccogli()` in
        # `marketplace_cdiscount`, contatori compresi.
        if channel.temu_import_on_pull and not riepilogo["errore"]:
            pendenti = OrderMap.search([
                ("channel_id", "=", channel.id),
                ("state", "in", ("pending", "error")),
            ])
            for order_map in pendenti:
                # ⚠️ Il codice esterno si legge QUI, mentre la transazione e'
                # certamente sana. Dentro l'`except` potrebbe essere ABORTITA,
                # e li' anche solo LEGGERE un campo esplode — e la lettura
                # avverrebbe fuori da qualunque savepoint, perche' gli
                # argomenti di `_al_riparo` si valutano PRIMA che il savepoint
                # esista. Un controllo del banco pretende che dentro un
                # `except` di questo file non si legga nessun campo di record.
                esterno = order_map.external_id
                # ⚠️ Il verdetto sta in una variabile PYTHON, non in un
                # contatore: le variabili il rollback non le tocca, i contatori
                # invece sopravviverebbero al rollback dicendo di aver
                # importato cio' che e' tornato indietro.
                entrato = False
                try:
                    # ⚠️ IL SAVEPOINT PER ORDINE. Senza, un errore del database
                    # dentro `import_order` lascia la transazione ABORTITA e
                    # catturarlo in Python non la salva: PostgreSQL rifiuta
                    # ogni istruzione successiva, e il commit finale della
                    # richiesta diventa un ROLLBACK silenzioso.
                    with self.env.cr.savepoint():
                        entrato = bool(self.import_order(esterno))
                except Exception as exc:  # noqa: BLE001 - isolamento per ordine
                    riepilogo["falliti"] += 1
                    _logger.exception("Import Temu fallito per %s", esterno)
                    # ⚠️ La traccia dell'errore passa comunque da
                    # `_al_riparo`, e non e' ridondante: il savepoint qui
                    # sopra ha rimesso in piedi la transazione, ma il flush
                    # d'ingresso del savepoint PUO' rompersi a sua volta, e li'
                    # la transazione e' abortita davvero. Un `False` e' l'unica
                    # traccia che la scrittura di servizio non c'e' stata.
                    scritto = self._al_riparo(
                        self._temu_errore_ordine, esterno,
                        "Errore inatteso: %s" % exc)
                    if not scritto:
                        # L'ordine resta "pending" e nessuno saprebbe perche'.
                        # (Il guasto col suo stack l'ha gia' scritto
                        # `_al_riparo`.)
                        _logger.error(
                            "Temu: non si e' potuto registrare l'errore "
                            "dell'ordine %s", esterno)
                    continue
                # ⚠️ SOLO ADESSO i contatori, e FUORI dal savepoint: e' la
                # «regola 3» di `marketplace_cdiscount`. Non si conta cio' che
                # il rollback si porta via.
                if entrato:
                    riepilogo["importati"] += 1
                else:
                    riepilogo["falliti"] += 1

        # ⚠️ Un ordine NON REGISTRATO tinge di rosso il giro, un ordine
        # fallito no — e la differenza e' voluta. Ogni fallito ha gia' la sua
        # riga rossa scritta da `_temu_errore_ordine`; un non registrato non ha
        # nessun'altra traccia, e senza questo resterebbe un verde che nasconde
        # ordini che nessuno sa nemmeno di aver visto.
        esito = ("error" if (riepilogo["errore"] or riepilogo["non_registrati"])
                 else "success")
        messaggio = ("Scarico ordini: %(letti)s letti in %(pagine)s pagine, "
                     "%(nuovi)s nuovi, %(gia_visti)s gia' presenti, "
                     "%(importati)s importati, %(falliti)s falliti."
                     % riepilogo)
        stati = ", ".join(
            "%s: %s" % (ORDER_STATUS_LABELS.get(k, "stato " + (k or "?")), v)
            for k, v in sorted(riepilogo["per_stato"].items(),
                               key=lambda kv: -kv[1]))
        if stati:
            messaggio += " Per stato: %s." % stati
        if riepilogo["non_registrati"]:
            messaggio += (" ⚠️ %s ordini NON registrati: la traccia non si e' "
                          "potuta scrivere e si rileggono al giro dopo."
                          % riepilogo["non_registrati"])
        self._temu_registra(esito, messaggio)
        return riepilogo

    # ------------------------------------------------------------------
    # IMPORT
    # ------------------------------------------------------------------
    def import_order(self, parent_order_sn):
        """Traduce un ordine Temu in un sale.order Odoo. Idempotente.

        Sequenza: dettaglio → prodotti (uno mancante = nessun ordine, mai
        ordini a meta') → importi → cliente → sale.order. Odoo resta la fonte
        di verita': i prodotti non si creano dall'ordine.
        """
        env = self.env
        channel = self.channel
        company = channel.company_id
        OrderMap = env["centrivo.order.map"]

        order_map = OrderMap.search([
            ("channel_id", "=", channel.id),
            ("external_id", "=", parent_order_sn),
        ], limit=1)
        if order_map and order_map.state == "imported":
            self._log("import_order", "skip",
                      "Ordine gia' importato, saltato (idempotenza).",
                      external_id=parent_order_sn)
            return order_map.sale_order_id

        # 1) DETTAGLIO
        risposta = self.client.call(API_ORDER_DETAIL,
                                    {"parentOrderSn": parent_order_sn})
        if not risposta.ok:
            return self._temu_errore_ordine(
                parent_order_sn, "Dettaglio non leggibile: %s %s"
                % (risposta.error_code or "", risposta.error_msg or ""))
        ordini, _t, _g = parse_order_page({"pageItems": [risposta.data]})
        if not ordini:
            from .temu_orders_parser import parse_order
            ordine = parse_order(risposta.data)
        else:
            ordine = ordini[0]
        if not ordine:
            return self._temu_errore_ordine(
                parent_order_sn, "Dettaglio in una forma non riconosciuta.")

        if ordine["status"] != (channel.temu_order_status or STATUS_DA_SPEDIRE):
            return self._temu_errore_ordine(
                parent_order_sn,
                "Stato '%s': si importano solo gli ordini nello stato '%s'. "
                "In attesa il cliente puo' ancora cambiare indirizzo e "
                "quantita'." % (ordine["status_label"] or ordine["status"],
                                ORDER_STATUS_LABELS.get(
                                    channel.temu_order_status or
                                    STATUS_DA_SPEDIRE, "?")))

        righe = righe_da_spedire(ordine)
        if not righe:
            return self._temu_errore_ordine(
                parent_order_sn, "Ordine senza righe da spedire.")

        # 2) PRODOTTI — una riga non risolta ferma tutto
        mancanti = []
        risolte = []
        for riga in righe:
            prodotto, diagnosi = self._temu_trova_prodotto(riga)
            if not prodotto:
                mancanti.append(diagnosi)
            else:
                risolte.append((riga, prodotto))
        if mancanti:
            return self._temu_errore_ordine(
                parent_order_sn,
                "Prodotto non trovato — %s" % " | ".join(mancanti))

        # 3) IMPORTI — senza prezzi non si crea niente
        importi, errore = self._temu_importi(parent_order_sn)
        if errore:
            return self._temu_errore_ordine(parent_order_sn, errore)

        # 4) CLIENTE
        try:
            partner = self._temu_partner(parent_order_sn, ordine)
        except Exception as exc:  # noqa: BLE001
            # ⚠️ Savepoint: il guasto puo' venire dal database (una
            # `res.partner` rifiutata da un vincolo), e allora la transazione
            # e' ABORTITA. Segnare l'errore senza rete fallirebbe a sua volta
            # e il commit finale diventerebbe un rollback silenzioso che si
            # porta via anche gli ordini gia' importati in questo giro.
            if not self._al_riparo(self._temu_errore_ordine, parent_order_sn,
                                   "Errore sul cliente: %s" % exc):
                _logger.error(
                    "Temu: non si e' potuto registrare l'errore sul cliente "
                    "dell'ordine %s", parent_order_sn)
            # ⚠️ `False` e non `return self._temu_errore_ordine(...)` come
            # prima: quella forma rendeva il valore PER COSTRUZIONE, questa lo
            # scrive a mano. Un `True` qui farebbe contare a `pull_orders` come
            # importato un ordine che non e' entrato. Due controlli del banco
            # lo tengono fermo: che `_temu_errore_ordine` renda sempre `False`,
            # e che ogni `return` dentro un `except` di questo metodo sia
            # quello stesso `False`.
            return NON_IMPORTATO

        # 5) SALE ORDER
        order_lines = []
        for riga, prodotto in risolte:
            order_lines.append((0, 0, {
                "product_id": prodotto.id,
                "product_uom_qty": riga["qty"],
                "price_unit": importi["per_riga"].get(riga["order_sn"], 0.0),
                "name": riga["goods_name"] or prodotto.display_name,
            }))
        spedizione = importi.get("spedizione") or 0.0
        if spedizione and channel.temu_shipping_product_id:
            order_lines.append((0, 0, {
                "product_id": channel.temu_shipping_product_id.id,
                "product_uom_qty": 1,
                "price_unit": spedizione,
                "name": "Spese di spedizione Temu",
            }))

        sale_order = order_map.sale_order_id if (
            order_map and order_map.sale_order_id) else False
        if not sale_order:
            valori = {
                "partner_id": partner.id,
                "company_id": company.id,
                "client_order_ref": parent_order_sn,
                "origin": parent_order_sn,
                "note": "Stato Temu: %s" % (ordine["status_label"] or ""),
                "order_line": order_lines,
            }
            if channel.team_id:
                valori["team_id"] = channel.team_id.id
            try:
                sale_order = env["sale.order"].with_company(company).create(valori)
            except Exception as exc:  # noqa: BLE001
                # ⚠️ Qui il guasto viene quasi sempre dal database (vincoli
                # sul sale.order o sulle sue righe): transazione ABORTITA, e
                # senza savepoint anche la sola nota d'errore la finirebbe di
                # rovinare, trasformando il commit in un rollback silenzioso.
                if not self._al_riparo(
                        self._temu_errore_ordine, parent_order_sn,
                        "Errore creazione ordine: %s" % exc):
                    _logger.error(
                        "Temu: non si e' potuto registrare l'errore di "
                        "creazione dell'ordine %s", parent_order_sn)
                return NON_IMPORTATO

        if channel.confirm_on_import and sale_order.state in ("draft", "sent"):
            try:
                sale_order.action_confirm()
            except Exception as exc:  # noqa: BLE001
                # ⚠️ Stessa ragione: `action_confirm` prenota il magazzino e
                # crea i movimenti, quindi il guasto e' spesso di database.
                # ⚠️ E `sale_order.name` si legge DENTRO il savepoint, non
                # prima: su una transazione gia' abortita perfino leggere un
                # campo esplode, e fuori di qui esploderebbe verso l'alto
                # invece di diventare un `False` che si puo' raccontare.

                def segna_errore_conferma():
                    self._temu_errore_ordine(
                        parent_order_sn, "Errore conferma ordine %s: %s"
                        % (sale_order.name, exc))

                if not self._al_riparo(segna_errore_conferma):
                    _logger.error(
                        "Temu: non si e' potuto registrare l'errore di "
                        "conferma dell'ordine %s", parent_order_sn)
                return NON_IMPORTATO

        valori_map = {
            "channel_id": channel.id,
            "external_id": parent_order_sn,
            "sale_order_id": sale_order.id,
            "state": "imported",
            "error_message": False,
            "company_id": company.id,
        }
        if order_map:
            order_map.write(valori_map)
        else:
            OrderMap.create(valori_map)
        self._log("import_order", "success",
                  "Ordine %s importato → %s (%s righe, totale %s)"
                  % (parent_order_sn, sale_order.name, len(order_lines),
                     importi.get("totale_cliente")),
                  external_id=parent_order_sn)
        return sale_order

    # ------------------------------------------------------------------
    # SUPPORTO
    # ------------------------------------------------------------------
    def _temu_trova_prodotto(self, riga):
        """Trova la variante Odoo. NON crea mai prodotti.

        Cascata, dalla chiave piu' solida alla piu' fragile:
          a) registro Schede Temu per `skuId` — identificativo numerico stabile,
             gia' agganciato dalla ricognizione;
          b) mappa SKU manuale, per le eccezioni;
          c) riferimento interno uguale al codice venditore;
          d) niente → diagnostica leggibile.
        """
        env = self.env
        diagnosi = []
        listing = env["centrivo.temu.listing"].search([
            ("channel_id", "=", self.channel.id),
            ("sku_id", "=", riga["sku_id"]),
        ], limit=1)
        diagnosi.append("scheda(skuId %s): %s"
                        % (riga["sku_id"], "trovata" if listing else "no"))
        if listing and listing.product_id:
            return listing.product_id, ""

        codice = riga.get("ext_code") or ""
        if codice:
            mappato = env["centrivo.sku.map"].search([
                ("channel_id", "=", self.channel.id),
                ("external_code", "=", codice),
            ], limit=1)
            diagnosi.append("sku.map(%s): %s"
                            % (codice, "trovato" if mappato else "no"))
            if mappato:
                return mappato.product_id, ""
            prodotto = env["product.product"].search(
                [("default_code", "=ilike", codice)], limit=1)
            diagnosi.append("riferimento interno(%s): %s"
                            % (codice, "trovato" if prodotto else "no"))
            if prodotto:
                return prodotto, ""
        else:
            diagnosi.append("codice venditore assente sulla riga")
        return False, "riga %s [%s]" % (riga["order_sn"], "; ".join(diagnosi))

    def _temu_importi(self, parent_order_sn):
        """Importi dell'ordine. Ritorna (dizionario, errore).

        Il prezzo che finisce sulla riga e' quello PAGATO DAL CLIENTE (decisione
        di Angelo): e' quello il totale dell'ordine per noi. Temu espone anche
        il `basePrice`, cioe' quanto incassiamo, ma i due divergono perche'
        esistono sconti a carico della piattaforma.
        """
        risposta = self.client.call(API_ORDER_AMOUNT,
                                    {"parentOrderSn": parent_order_sn})
        if not risposta.ok:
            if str(risposta.error_code) == "3000032":
                self._temu_spiega_importi()
                return {}, (
                    "Importi non leggibili (3000032): al token manca il "
                    "permesso su %s. Il perche' e cosa fare sono scritti una "
                    "sola volta nel Log operazioni di questo giro, e in %s. "
                    "L'ordine NON viene creato, per non inventare prezzi."
                    % (API_ORDER_AMOUNT, DOC_PRATICA))
            return {}, ("Importi non leggibili: %s %s"
                        % (risposta.error_code or "", risposta.error_msg or ""))

        dati = risposta.data or {}
        per_riga = {}
        for voce in (dati.get("orderAmountList") or dati.get("orderList") or []):
            if not isinstance(voce, dict):
                continue
            order_sn = str(voce.get("orderSn") or "").strip()
            if not order_sn:
                continue
            per_riga[order_sn] = self._temu_prezzo_cliente(voce)
        return {
            "per_riga": per_riga,
            "spedizione": self._temu_decimale(
                dati.get("shippingAmountTotal") or dati.get("shippingTotal")),
            "totale_cliente": self._temu_decimale(
                dati.get("amountPaidByCustomer") or dati.get("orderAmountTotal")),
            "grezzo": dati,
        }, None

    def _temu_spiega_importi(self):
        """Scrive nel Log operazioni, UNA volta per giro, perche' gli importi
        non si leggono e cosa si puo' davvero fare.

        ⚠️ Questa spiegazione e' lunga una pagina e sull'ordine non ci va: il
        messaggio d'errore finisce in `error_message` E nel Log operazioni per
        ogni ordine fermo, e con cinquanta ordini fermi sarebbero cinquanta
        copie della stessa pagina. Sull'ordine va una riga corta che rimanda.

        ⚠️ La guardia sta sull'ISTANZA, non sulla classe: il connettore si
        costruisce a ogni giro, quindi "una volta per istanza" e' "una volta
        per giro". Sulla classe sarebbe "una volta per processo", e dal
        secondo giro Angelo non leggerebbe piu' niente.
        """
        if getattr(self, "_temu_importi_gia_spiegato", False):
            return
        self._temu_importi_gia_spiegato = True
        self._log("import_order", "error", (
            "Gli importi non sono leggibili: al token manca il permesso su "
            "%s (3000032).\n\n"
            "⚠️ Rifare l'autorizzazione dell'app NON serve: e' gia' stato "
            "provato, e il token torna con gli stessi 146 permessi. "
            "L'interfaccia degli importi non e' mai stata dichiarata al "
            "momento della registrazione dell'app, quindi non e' stata "
            "tolta: non c'e' mai stata.\n\n"
            "⚠️ E non si sblocca da qui. Dal Centro Partner si cambiano tre "
            "sole cose — indirizzo IP, nuovi negozi, Paesi autorizzati — e "
            "nessuna riguarda le interfacce; e una domanda nuova non si puo' "
            "fare, perche' ogni account ha diritto a una sola app "
            "self-developed. Deve essere Temu a modificare la registrazione "
            "dell'app.\n\n"
            "La richiesta e' GIA' APERTA presso l'assistenza Temu, che ha "
            "risposto di riflesso senza guardare il problema: va sollecitata "
            "sul ticket esistente, che si segue dal Seller Center → "
            "Assistenza. ⚠️ Si chiede una volta sola, quindi vanno "
            "chieste tutte insieme le interfacce non dichiarate al momento "
            "della registrazione: %s.\n\n"
            "Stato della pratica e testo gia' pronto da mandare: %s.\n\n"
            "Senza importi gli ordini NON vengono creati, per non inventare "
            "prezzi."
            % (API_ORDER_AMOUNT, ", ".join(INTERFACCE_DA_CHIEDERE),
               DOC_PRATICA)))

    def _temu_prezzo_cliente(self, voce):
        """Prezzo unitario pagato dal cliente per una riga.

        Si prova prima il prezzo unitario al dettaglio; se manca, si ricava dal
        totale diviso la quantita'. I nomi dei campi vanno confermati alla prima
        risposta reale: oggi la chiamata non e' autorizzata, quindi questa
        lettura NON e' ancora stata verificata sul campo.
        """
        for chiave in ("unitRetailPrice", "unitRetailPriceVatIncl",
                       "unitRetailPriceVatExcl", "retailPrice"):
            valore = self._temu_decimale(voce.get(chiave))
            if valore:
                return valore
        totale = self._temu_decimale(voce.get("retailPriceTotal")
                                     or voce.get("orderAmount"))
        quantita = voce.get("quantity") or 1
        try:
            return totale / float(quantita or 1)
        except (TypeError, ZeroDivisionError):
            return totale

    @staticmethod
    def _temu_decimale(valore):
        """Numero da un campo che puo' arrivare come stringa, dizionario o None.

        Temu esprime alcuni importi come oggetto {amount, currency}: si legge
        l'importo. I centesimi non si arrotondano qui.
        """
        if valore is None:
            return 0.0
        if isinstance(valore, dict):
            valore = valore.get("amount")
        try:
            return float(str(valore).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    def _temu_partner(self, parent_order_sn, ordine):
        """Cliente dell'ordine, letto dai dati di consegna. Non li logga MAI.

        I dati arrivano cifrati e vanno chiesti in chiaro con una seconda
        chiamata, che funziona solo finche' l'ordine e' aperto. Sono dati
        personali: il connettore li usa per creare il cliente e basta.
        """
        env = self.env
        Partner = env["res.partner"]
        risposta = self.client.call(API_SHIPPING_DECRYPT,
                                    {"parentOrderSn": parent_order_sn})
        if not risposta.ok:
            raise ValueError(
                "indirizzo non leggibile (%s %s)"
                % (risposta.error_code or "", risposta.error_msg or ""))
        d = risposta.data or {}

        nome = (d.get("receiptName") or "").strip()
        if not nome:
            extra = d.get("addressExtra") or {}
            nome = " ".join(x for x in (extra.get("firstName"),
                                        extra.get("lastName")) if x).strip()
        if not nome:
            nome = "Cliente Temu %s" % parent_order_sn

        # Si cerca un cliente gia' creato per lo stesso ordine, cosi' un
        # secondo tentativo non produce un doppione in anagrafica.
        esistente = Partner.search([("ref", "=", parent_order_sn)], limit=1)
        if esistente:
            return esistente

        strada = " ".join(x for x in (d.get("addressLine1"),
                                      d.get("addressLine2"),
                                      d.get("addressLine3")) if x).strip()
        paese = env["res.country"].search(
            [("code", "=", (d.get("nationalAddress") or "IT")[:2].upper())],
            limit=1)
        return Partner.create({
            "name": nome,
            "ref": parent_order_sn,
            "street": strada or False,
            "city": (d.get("regionName3") or d.get("regionName2") or "") or False,
            "zip": d.get("postCode") or False,
            "phone": d.get("mobile") or False,
            "email": d.get("mail") or False,
            "country_id": paese.id if paese else False,
            "company_id": False,
        })

    def _temu_ordine_troppo_vecchio(self, ordine):
        """Vero se l'ordine e' anteriore alla data di partenza del canale.

        Serve a NON importare lo storico: il negozio ha centinaia di ordini
        vecchi, chiusi e gia' fatturati altrove, che in Odoo sarebbero solo
        rumore (decisione di Angelo).
        """
        limite = self.channel.temu_orders_from
        if not limite:
            return False
        istante = ordine.get("order_time") or 0
        if not istante:
            return False
        from datetime import datetime, timezone
        data = datetime.fromtimestamp(istante, tz=timezone.utc).date()
        return data < limite

    @staticmethod
    def _temu_epoch(valore):
        """Data/ora Odoo in secondi epoch, o None."""
        if not valore:
            return None
        from datetime import timezone
        try:
            return int(valore.replace(tzinfo=timezone.utc).timestamp())
        except Exception:  # noqa: BLE001
            return None

    def _temu_errore_ordine(self, parent_order_sn, messaggio):
        """Segna l'ordine in errore e lo scrive nel log. Rende sempre NON_IMPORTATO.

        ⚠️ Il valore reso e' la costante `NON_IMPORTATO` del modulo, la stessa che
        rendono i tre `return` dentro un `except` di `import_order`: un solo
        posto da guardare, invece di quattro letterali da tenere allineati.
        """
        OrderMap = self.env["centrivo.order.map"]
        order_map = OrderMap.search([
            ("channel_id", "=", self.channel.id),
            ("external_id", "=", parent_order_sn),
        ], limit=1)
        valori = {"state": "error", "error_message": messaggio}
        if order_map:
            order_map.write(valori)
        else:
            valori.update({
                "channel_id": self.channel.id,
                "external_id": parent_order_sn,
                "company_id": self.channel.company_id.id,
            })
            OrderMap.create(valori)
        self._log("import_order", "error", messaggio,
                  external_id=parent_order_sn)
        return NON_IMPORTATO
