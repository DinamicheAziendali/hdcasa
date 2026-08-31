# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Allineamento di giacenze e prezzi da Odoo verso Temu.

⚠️ IL PREZZO DI TEMU NON E' IL PREZZO AL PUBBLICO. La Guida ai prezzi lo dice
chiaramente: il **base price** e' *"l'importo che ricevi da ogni vendita in
condizioni standard"*. Il prezzo che il cliente vede lo decide Temu, che ci
aggiunge il proprio margine e le proprie promozioni.

Conseguenza pratica, e Angelo l'aveva intuita prima di leggere la
documentazione: **non si puo' mandare a Temu lo stesso listino degli altri
marketplace**. Per questo il canale ha un listino DEDICATO: se non e'
configurato, i prezzi non partono affatto. Meglio non allineare che allineare
il numero sbagliato — su Temu un prezzo troppo basso non e' uno sconto, e' un
incasso mancato su ogni pezzo venduto.

Due altre cose imparate leggendo, che cambiano le aspettative:
  - un cambio prezzo NON e' immediato: crea un "price order" che Temu esamina,
    e la risposta ci restituisce il numero della pratica;
  - le giacenze si scrivono per ARTICOLO (`goodsId`), non per SKU: una sola
    chiamata porta tutte le varianti di quella scheda.
"""
import json
import logging

_logger = logging.getLogger(__name__)

API_STOCK_EDIT = "bg.local.goods.stock.edit"
API_STOCK_QUERY = "temu.local.goods.sku.stock.query"
API_PRICE_CHANGE = "bg.local.goods.priceorder.change.sku.price"
API_PRICE_ORDERS = "bg.local.goods.priceorder.query"

# Limiti dichiarati dalla documentazione (errore 150013002).
STOCK_MIN = 0
STOCK_MAX = 1000000

# Giacenza ordinaria: 0. L'altro valore (1) e' il pre-ordine, che non usiamo.
STOCK_TYPE_ORDINARIO = 0

# Sotto questo numero di righe l'azzeramento di massa non scatta: su pochi
# articoli mettere tutto a zero e' un'operazione normale (fine serie, ritiro).
SOGLIA_AZZERAMENTO = 20


class TemuPricingMixin(object):
    """Giacenze e prezzi verso Temu."""

    # ------------------------------------------------------------------
    # GIACENZE
    # ------------------------------------------------------------------
    def push_stock(self, listings=None):
        """Allinea le giacenze di Temu a quelle di Odoo.

        Manda solo cio' che e' CAMBIATO rispetto all'ultimo invio riuscito: le
        chiamate hanno un limite di frequenza e riscrivere lo stesso numero e'
        lavoro sprecato. Ritorna il riepilogo: i guasti di Temu diventano voci
        del riepilogo, non eccezioni.

        ⚠️ UNICA ECCEZIONE che esce di qui: la `UserError` del turno occupato
        qui sotto. E' voluta, e il bottone del canale la mostra da sola.
        """
        # ⚠️ Un giro per volta su questo canale. Senza, due clic — o un cron
        # che si sovrappone a un clic — fanno partire due giri che mandano le
        # stesse cose. Sulle giacenze c'e' la chiave di unicita' che protegge
        # lato Temu; sui prezzi e sulle spedizioni no.
        self._prendi_il_turno("invio delle giacenze")
        channel = self.channel
        riepilogo = {"esaminate": 0, "da_aggiornare": 0, "aggiornate": 0,
                     "invariate": 0, "senza_prodotto": 0, "errori": 0,
                     "errore": None}

        schede = listings or self.env["centrivo.temu.listing"].search([
            ("channel_id", "=", channel.id),
        ])
        per_articolo = {}
        for scheda in schede:
            riepilogo["esaminate"] += 1
            if not scheda.product_id:
                riepilogo["senza_prodotto"] += 1
                continue
            quantita = self._temu_quantita(scheda.product_id)
            quantita = max(STOCK_MIN, min(STOCK_MAX, int(quantita)))
            if scheda.stock_sent == quantita:
                riepilogo["invariate"] += 1
                continue
            riepilogo["da_aggiornare"] += 1
            per_articolo.setdefault(scheda.goods_id, []).append(
                (scheda, quantita))

        # ⚠️ PROTEZIONE CONTRO L'AZZERAMENTO DI MASSA. Se quasi tutto quello che
        # stiamo per mandare e' zero, quasi certamente non e' vero che il
        # magazzino e' vuoto: e' Odoo che non sa le giacenze (prodotti importati
        # come gusci, magazzino sbagliato nelle impostazioni, azienda sbagliata).
        # Mandarlo comunque toglierebbe dalla vendita l'intero negozio con una
        # sola chiamata. Meglio fermarsi e farlo decidere a una persona.
        da_mandare = [q for voci in per_articolo.values() for _s, q in voci]
        zeri = sum(1 for q in da_mandare if q == 0)
        if (len(da_mandare) >= SOGLIA_AZZERAMENTO
                and zeri >= len(da_mandare) * 0.9
                and not channel.temu_allow_zero_stock):
            riepilogo["errore"] = (
                "Fermato: %s giacenze su %s sarebbero azzerate. Se il magazzino "
                "fosse davvero vuoto sarebbe un caso; molto piu' probabile e' "
                "che Odoo non conosca le giacenze (prodotti senza stock, "
                "magazzino o azienda sbagliati nelle impostazioni del canale). "
                "Controlla, e se l'azzeramento e' voluto attiva 'Consenti "
                "azzeramento di massa' sul canale." % (zeri, len(da_mandare)))
            self._log("push_stock", "error", riepilogo["errore"])
            return riepilogo

        for goods_id, voci in per_articolo.items():
            corpo = {
                "goodsId": int(goods_id) if str(goods_id).isdigit() else goods_id,
                "stockType": STOCK_TYPE_ORDINARIO,
                # Chiave di unicita': se la stessa richiesta partisse due volte
                # (rete incerta, ritentativo), Temu la scarta invece di
                # applicarla di nuovo.
                "requestUniqueKey": "odoo-%s-%s-%s" % (
                    channel.id, goods_id,
                    "-".join("%s:%s" % (s.sku_id, q) for s, q in voci))[:120],
                "skuStockTargetList": [
                    {"skuId": int(s.sku_id) if s.sku_id.isdigit() else s.sku_id,
                     "stockTarget": q}
                    for s, q in voci
                ],
            }
            if channel.temu_simulate:
                self._log("push_stock", "skip",
                          "SIMULAZIONE: giacenze pronte ma NON inviate per "
                          "l'articolo %s (%s varianti)." % (goods_id, len(voci)),
                          payload=json.dumps(corpo, ensure_ascii=False)[:8000],
                          external_id=goods_id)
                continue

            esito = self.client.call(API_STOCK_EDIT, corpo)

            # ⚠️ Stesso principio della spedizione: un 502 non e' un rifiuto,
            # la richiesta puo' essere arrivata lo stesso. Qui il danno e'
            # minore — la `requestUniqueKey` qui sopra protegge lato Temu dal
            # doppio invio — ma segnare come «inviata» una giacenza il cui
            # esito non si conosce e' comunque una bugia: al giro dopo
            # risulterebbe «invariata» e non ripartirebbe mai piu'.
            incerto = self._causa_incerta(esito)
            if incerto:
                riepilogo["errori"] += len(voci)
                self._log("push_stock", "error",
                          "Esito IGNOTO sulle giacenze dell'articolo %s (%s "
                          "varianti): %s. Non si segna niente come inviato: al "
                          "prossimo giro si rilegge e si rimanda."
                          % (goods_id, len(voci), incerto),
                          payload=json.dumps(esito.raw, ensure_ascii=False)[:8000],
                          external_id=goods_id)
                continue

            if not esito.ok:
                # Il riepilogo conta VARIANTI, non articoli: "esaminate",
                # "aggiornate" e "invariate" sono tutte schede, e anche gli
                # errori per singolo SKU qui sotto contano uno a testa. Un
                # rifiuto sull'articolo ferma tutte le sue varianti, quindi
                # sono quelle che vanno contate — o il totale finale mescola
                # due unita' di misura e non torna.
                riepilogo["errori"] += len(voci)
                self._log("push_stock", "error",
                          "Giacenze rifiutate per l'articolo %s: %s %s"
                          % (goods_id, esito.error_code or "",
                             esito.error_msg or ""),
                          payload=json.dumps(esito.raw, ensure_ascii=False)[:8000],
                          external_id=goods_id)
                continue

            # La risposta dice l'esito SKU per SKU: si segna come inviata solo
            # quella che Temu ha davvero accettato.
            per_sku = {}
            for voce in ((esito.data or {}).get("skuStockEditStatusInfoList") or []):
                if isinstance(voce, dict):
                    per_sku[str(voce.get("skuId"))] = voce
            for scheda, quantita in voci:
                stato = per_sku.get(str(scheda.sku_id))
                if stato is None or stato.get("stockEditStatus"):
                    # ⚠️ Savepoint. Senza, una write rifiutata dal database
                    # abortisce la transazione e si porta via anche le schede
                    # gia' segnate come inviate in questo giro: al giro dopo
                    # risulterebbero «da aggiornare» e si riscriverebbe su
                    # Temu roba gia' scritta.
                    # ⚠️ E il contatore resta DOPO la write: non si conta come
                    # aggiornata una riga che non e' stata scritta.
                    if not self._al_riparo(
                            scheda.write,
                            {"stock_sent": quantita,
                             "stock_sent_on": self._temu_adesso()}):
                        riepilogo["errori"] += 1
                        continue
                    riepilogo["aggiornate"] += 1
                else:
                    # Qui il contatore sta PRIMA di proposito: conta il rifiuto
                    # di Temu, che c'e' stato comunque. La write e' solo la
                    # nota locale che lo spiega.
                    riepilogo["errori"] += 1
                    if not self._al_riparo(
                            scheda.write,
                            {"last_error": "Giacenza rifiutata: %s %s"
                             % (stato.get("errorCode"),
                                stato.get("errorMsg"))}):
                        _logger.error(
                            "Temu: non si e' potuta annotare la giacenza "
                            "rifiutata sulla scheda %s", scheda.id)

        esito_log = "error" if riepilogo["errori"] else "success"
        self._log("push_stock", esito_log,
                  "Giacenze: %(esaminate)s schede esaminate, %(aggiornate)s "
                  "aggiornate, %(invariate)s gia' allineate, "
                  "%(senza_prodotto)s senza prodotto Odoo, %(errori)s errori."
                  % riepilogo)
        return riepilogo

    # ------------------------------------------------------------------
    # PREZZI
    # ------------------------------------------------------------------
    def push_prices(self, listings=None, reason=None):
        """Propone a Temu i nuovi prezzi base. NON e' un aggiornamento immediato.

        Ogni cambio apre una pratica ("price order") che Temu esamina: la
        risposta ci da' il numero della pratica, e l'esito arriva dopo. Va detto,
        altrimenti si guarda il negozio e ci si chiede perche' il prezzo e'
        ancora quello vecchio.

        Ritorna il riepilogo: i guasti di Temu diventano voci del riepilogo,
        non eccezioni.

        ⚠️ UNICA ECCEZIONE che esce di qui: la `UserError` del turno occupato
        qui sotto. E' voluta, e il bottone del canale la mostra da sola. Qui
        il turno pesa piu' che altrove: il cambio prezzo non ha chiave di
        unicita', e due giri sovrapposti aprono due pratiche identiche.
        """
        # ⚠️ Un giro per volta su questo canale, come per le giacenze — e qui
        # serve di piu': il cambio prezzo NON ha una chiave di unicita', quindi
        # due giri sovrapposti aprono due pratiche identiche su Temu.
        self._prendi_il_turno("invio dei prezzi")
        channel = self.channel
        riepilogo = {"esaminate": 0, "da_aggiornare": 0, "proposte": 0,
                     "invariate": 0, "senza_prodotto": 0, "senza_prezzo": 0,
                     "errori": 0, "errore": None, "pratiche": []}

        if not channel.temu_pricelist_id:
            riepilogo["errore"] = (
                "Nessun listino Temu configurato sul canale. Il prezzo che Temu "
                "vuole NON e' quello al pubblico: e' l'importo che incassi tu, "
                "e va tenuto su un listino dedicato. Senza, i prezzi non "
                "partono — di proposito.")
            self._log("push_prices", "error", riepilogo["errore"])
            return riepilogo

        schede = listings or self.env["centrivo.temu.listing"].search([
            ("channel_id", "=", channel.id),
        ])
        per_articolo = {}
        for scheda in schede:
            riepilogo["esaminate"] += 1
            if not scheda.product_id:
                riepilogo["senza_prodotto"] += 1
                continue
            prezzo = self._temu_prezzo_base(scheda.product_id)
            if not prezzo:
                riepilogo["senza_prezzo"] += 1
                continue
            if scheda.price_sent and abs(scheda.price_sent - prezzo) < 0.005:
                riepilogo["invariate"] += 1
                continue
            riepilogo["da_aggiornare"] += 1
            per_articolo.setdefault(scheda.goods_id, []).append((scheda, prezzo))

        valuta = channel.temu_currency or "EUR"
        motivo = reason or "Allineamento dal gestionale Odoo"

        for goods_id, voci in per_articolo.items():
            corpo = {
                "goodsId": int(goods_id) if str(goods_id).isdigit() else goods_id,
                "changeSkuPriceDTOList": [{
                    "reason": motivo,
                    "skuChangePriceBaseDTOList": [
                        {"skuId": int(s.sku_id) if s.sku_id.isdigit() else s.sku_id,
                         # "supplier price" nel linguaggio di Temu = il base
                         # price, cioe' quanto incassiamo noi.
                         "newSupplierPrice": {"amount": "%.2f" % p,
                                              "currency": valuta}}
                        for s, p in voci
                    ],
                }],
            }
            if channel.temu_simulate:
                self._log("push_prices", "skip",
                          "SIMULAZIONE: prezzi pronti ma NON inviati per "
                          "l'articolo %s (%s varianti)." % (goods_id, len(voci)),
                          payload=json.dumps(corpo, ensure_ascii=False)[:8000],
                          external_id=goods_id)
                continue

            esito = self.client.call(API_PRICE_CHANGE, corpo)

            # ⚠️ Come sopra, ma qui la rete di protezione non c'e': il cambio
            # prezzo non ha una chiave di unicita', quindi se la richiesta era
            # passata il prossimo giro aprira' una SECONDA pratica identica.
            # Meglio saperlo dal log che scoprirlo in Seller Center.
            incerto = self._causa_incerta(esito)
            if incerto:
                riepilogo["errori"] += len(voci)
                self._log("push_prices", "error",
                          "Esito IGNOTO sui prezzi dell'articolo %s (%s "
                          "varianti): %s. Non si segna niente come proposto: "
                          "al prossimo giro si rilegge e si riprova. Se la "
                          "pratica era passata, quel giro ne aprira' una "
                          "seconda uguale — si guardano le pratiche prezzo "
                          "aperte prima di preoccuparsi."
                          % (goods_id, len(voci), incerto),
                          payload=json.dumps(esito.raw, ensure_ascii=False)[:8000],
                          external_id=goods_id)
                continue

            if not esito.ok:
                # Come per le giacenze: si contano le varianti fermate, che e'
                # l'unita' di tutto il resto del riepilogo.
                riepilogo["errori"] += len(voci)
                self._log("push_prices", "error",
                          "Prezzi rifiutati per l'articolo %s: %s %s"
                          % (goods_id, esito.error_code or "",
                             esito.error_msg or ""),
                          payload=json.dumps(esito.raw, ensure_ascii=False)[:8000],
                          external_id=goods_id)
                continue

            dati = esito.data or {}
            accettate = {str(x) for x in (dati.get("successSkuList") or [])}
            motivi = dati.get("failedSkuReasonMap") or {}
            pratiche = {}
            for pratica in (dati.get("successPriceOrderList") or []):
                if not isinstance(pratica, dict):
                    continue
                numero = pratica.get("priceOrderSn")
                riepilogo["pratiche"].append(numero)
                for sku in (pratica.get("skuIdList") or []):
                    pratiche[str(sku)] = numero

            for scheda, prezzo in voci:
                if accettate and str(scheda.sku_id) not in accettate:
                    # Contatore prima: il rifiuto di Temu c'e' stato comunque,
                    # la write e' solo la nota locale che lo spiega.
                    riepilogo["errori"] += 1
                    if not self._al_riparo(
                            scheda.write,
                            {"last_error": "Prezzo rifiutato: %s"
                             % motivi.get(str(scheda.sku_id),
                                          "motivo non dato")}):
                        _logger.error(
                            "Temu: non si e' potuto annotare il prezzo "
                            "rifiutato sulla scheda %s", scheda.id)
                    continue
                # ⚠️ Savepoint, e qui il danno sarebbe il peggiore del modulo:
                # una write rifiutata abortisce la transazione, si porta via
                # anche le schede gia' segnate come proposte, e al giro dopo
                # si aprirebbe una SECONDA pratica prezzo identica su Temu —
                # che qui, senza chiave di unicita', niente ferma.
                # ⚠️ Contatore DOPO la write: non si conta come proposta una
                # riga che non e' stata scritta.
                if not self._al_riparo(scheda.write, {
                        "price_sent": prezzo,
                        "price_sent_on": self._temu_adesso(),
                        "price_order_sn": pratiche.get(str(scheda.sku_id))
                        or False,
                }):
                    riepilogo["errori"] += 1
                    continue
                riepilogo["proposte"] += 1

        esito_log = "error" if riepilogo["errori"] else "success"
        messaggio = ("Prezzi: %(esaminate)s schede esaminate, %(proposte)s "
                     "proposte a Temu, %(invariate)s gia' allineate, "
                     "%(senza_prodotto)s senza prodotto, %(senza_prezzo)s senza "
                     "prezzo a listino, %(errori)s errori." % riepilogo)
        if riepilogo["pratiche"]:
            messaggio += (" ⚠️ I prezzi NON cambiano subito: Temu ha aperto %s "
                          "pratiche di revisione (%s)."
                          % (len(riepilogo["pratiche"]),
                             ", ".join(str(p) for p in riepilogo["pratiche"][:5])))
        self._log("push_prices", esito_log, messaggio)
        return riepilogo

    def check_price_orders(self):
        """Rilegge le pratiche di revisione prezzo aperte. Sola lettura.

        ⚠️ Rende un DIZIONARIO, non l'elenco: prima rendeva solo la lista e
        buttava via l'esito, cosi' una lettura fallita — dove l'elenco e'
        vuoto per forza — arrivava al bottone indistinguibile da «nessuna
        pratica aperta», cioe' da «tutto a posto». Il registro lo diceva,
        il popup no, e il popup e' la sola cosa che si guarda.
        """
        risposta = self.client.call(API_PRICE_ORDERS,
                                    {"pageNumber": 1, "pageSize": 100})
        esito = "success" if risposta.ok else "error"
        elenco = (risposta.data or {}).get("priceAuditList") or []
        motivo = "" if risposta.ok else ("%s %s" % (risposta.error_code or "",
                                                    risposta.error_msg or ""))
        self._log("check_price_orders", esito,
                  "Pratiche prezzo aperte: %s." % len(elenco) if risposta.ok
                  else "Pratiche prezzo non leggibili: %s" % motivo,
                  payload=json.dumps(risposta.raw, ensure_ascii=False)[:20000])
        return {"letto": bool(risposta.ok), "pratiche": elenco,
                "motivo": motivo}

    def check_stock(self, listings=None):
        """Rilegge da Temu le giacenze e le confronta con Odoo. Sola lettura.

        Serve a rispondere alla domanda "siamo allineati?" senza scrivere
        niente: e' il controllo da fare prima di accendere l'invio automatico.
        """
        schede = listings or self.env["centrivo.temu.listing"].search([
            ("channel_id", "=", self.channel.id), ("product_id", "!=", False),
        ])
        per_articolo = {}
        for scheda in schede:
            per_articolo.setdefault(scheda.goods_id, []).append(scheda)

        allineate = diverse = illeggibili = 0
        scostamenti = []
        for goods_id, voci in per_articolo.items():
            risposta = self.client.call(
                API_STOCK_QUERY,
                {"goodsId": int(goods_id) if str(goods_id).isdigit() else goods_id})
            if not risposta.ok:
                illeggibili += len(voci)
                continue
            su_temu = {}
            for articolo in ((risposta.data or {}).get("stockList") or []):
                for info in (articolo.get("skuStockInfoList") or []):
                    ordinaria = (info.get("selfOrdinaryStock") or {})
                    su_temu[str(info.get("skuId"))] = ordinaria.get("stock")
            for scheda in voci:
                valore = su_temu.get(str(scheda.sku_id))
                nostro = int(self._temu_quantita(scheda.product_id))
                if valore is None:
                    illeggibili += 1
                elif int(valore) == nostro:
                    allineate += 1
                else:
                    diverse += 1
                    if len(scostamenti) < 20:
                        scostamenti.append("%s: Temu %s / Odoo %s"
                                           % (scheda.out_sku_sn or scheda.sku_id,
                                              valore, nostro))
        messaggio = ("Confronto giacenze: %s allineate, %s diverse, %s non "
                     "leggibili." % (allineate, diverse, illeggibili))
        if scostamenti:
            messaggio += " Primi scostamenti: %s." % "; ".join(scostamenti)
        # ⚠️ `illeggibili` NON e' una sfumatura, ed e' il verde bugiardo di
        # questo metodo: con tutte le letture fallite il conto e' 0 allineate,
        # 0 diverse, N illeggibili — e prima usciva "success". Questo e' «il
        # controllo da fare prima di accendere l'invio automatico», cioe' il
        # semaforo che autorizza a scrivere su un catalogo pubblico: verde su
        # zero letture e' la risposta peggiore che possa dare.
        if illeggibili:
            messaggio += (" ⚠️ Con %s righe non lette questo confronto NON "
                          "dice che siamo allineati: dice che non si sa."
                          % illeggibili)
        # Il log del nucleo ammette solo successo/errore/saltato: uno
        # scostamento non e' un errore del connettore (la lettura e' riuscita),
        # ma nemmeno un successo pieno. Si usa "saltato", che nel resto del
        # modulo significa gia' "fatto, ma con una riserva da leggere". Una
        # riga NON LETTA invece e' un guasto: la lettura non c'e' stata.
        self._log("check_stock",
                  "error" if illeggibili else ("skip" if diverse else "success"),
                  messaggio)
        return {"allineate": allineate, "diverse": diverse,
                "illeggibili": illeggibili, "scostamenti": scostamenti}

    # ------------------------------------------------------------------
    def _temu_quantita(self, product):
        """Quantita' secondo le impostazioni del canale.

        Copiata dalla stessa funzione degli altri connettori invece di essere
        condivisa: e' quattro righe, e legarla fra moduli di marketplace diversi
        creerebbe una dipendenza fra cose che devono poter cambiare da sole.
        """
        channel = self.channel
        campo = channel.stock_quantity_type or "free_qty"
        if channel.stock_scope == "warehouses" and channel.warehouse_ids:
            totale = 0.0
            for magazzino in channel.warehouse_ids:
                totale += getattr(
                    product.with_context(warehouse=magazzino.id), campo)
            return totale
        return getattr(product, campo)

    def _temu_prezzo_base(self, product):
        """Prezzo base per Temu, preso dal listino DEDICATO del canale.

        Non si ripiega mai sul prezzo di vendita del prodotto: se il listino non
        copre quell'articolo, il prezzo non parte. Un ripiego silenzioso qui
        manderebbe a Temu il prezzo al pubblico come se fosse l'incasso, e ogni
        vendita ci costerebbe la differenza.
        """
        listino = self.channel.temu_pricelist_id
        if not listino:
            return 0.0
        # ⚠️ IL RIPIEGO STA FUORI DALL'`except`, che si limita ad alzare una
        # bandiera. Dentro un `except` non si tocca nessun record: se il guasto
        # veniva dal database la transazione e' ABORTITA, e li' perfino leggere
        # esplode. Qui il danno oggi sarebbe nullo — questo `except` cattura
        # una differenza di FIRMA fra versioni di Odoo, non un guasto di
        # database — ma la regola vale su tutti e tre i file del modulo, e
        # «tanto qui non serve» e' esattamente il modo in cui una regola si
        # perde: il giorno in cui questo `except` cattura anche altro, la
        # lettura scoperta sarebbe gia' li'. E rimandare invece di anticipare
        # tiene il ripiego PIGRO: sul percorso normale `with_context` non si
        # chiama nemmeno.
        firma_rifiutata = False
        prezzo = 0.0
        try:
            prezzo = listino._get_product_price(product, 1.0)
        except Exception:  # noqa: BLE001 - API di listino diversa fra versioni
            firma_rifiutata = True
        if firma_rifiutata:
            prezzo = listino.with_context(quantity=1.0)._get_product_price(
                product, 1.0)
        return float(prezzo or 0.0)

    @staticmethod
    def _temu_adesso():
        from odoo import fields as odoo_fields
        return odoo_fields.Datetime.now()
