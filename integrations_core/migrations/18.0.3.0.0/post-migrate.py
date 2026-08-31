# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Conversione della mappa corrieri v1 nei modelli v2.

Obiettivo vincolante: dopo l'aggiornamento ogni spedizione deve produrre lo
STESSO codice e lo STESSO URL di prima. Per questo il corriere si deduce dal
codice già configurato (un fatto confermato dall'utente) e non dal nome del
vettore (un'interpretazione), e ogni scostamento dal comportamento predefinito
diventa un avviso esplicito.

Regola non negoziabile: una conversione non deve MAI impedire l'installazione
del modulo. Ogni riga gira dentro un punto di ripristino E un blocco
try/except (il punto di ripristino da solo rilancia l'eccezione dopo il
rollback: serve il try/except per fermarla davvero); gli errori si
registrano e l'aggiornamento prosegue.
"""
import importlib
import logging

from odoo import SUPERUSER_ID, api
from odoo.addons.integrations_core.connectors.base import MarketplaceConnector
from odoo.addons.integrations_core.connectors.carrier_resolver import brand_for_code

_logger = logging.getLogger(__name__)


def _avviso(env, channel, messaggio, contatori=None):
    """Avviso visibile nel Log operazioni, oltre che nei log del server.

    La create() è avvolta in un punto di ripristino annidato: se fallisse a
    livello database (es. vincolo violato), il fallimento non deve avvelenare
    la transazione (o il punto di ripristino della riga) in cui ci troviamo.
    """
    _logger.warning("Migrazione mappa corrieri: %s", messaggio)
    if contatori is not None:
        contatori["avvisi"] += 1
    valori = {
        "channel_id": channel.id if channel else False,
        "operation": "carrier_migration",
        "result": "error",
        "message": messaggio,
    }
    # company_id su centrivo.job.log è OBBLIGATORIO e ha già il proprio valore
    # predefinito (l'azienda dell'utente corrente). Passare la chiave con
    # valore falso sovrascriverebbe quel predefinito, violerebbe il NOT NULL e
    # farebbe rollare indietro il punto di ripristino: l'avviso finirebbe solo
    # nei log del server, proprio nel caso in cui l'utente DEVE vederlo (es.
    # "la conversione non è stata eseguita", che arriva senza canale). Quindi
    # la chiave si aggiunge SOLO quando c'è un'azienda vera da scrivere; se
    # manca, si lascia agire il predefinito del campo, che è la soluzione più
    # robusta perché resta corretta anche se in futuro quel predefinito
    # cambia.
    if channel and channel.company_id:
        valori["company_id"] = channel.company_id.id
    try:
        with env.cr.savepoint():
            env["centrivo.job.log"].sudo().create(valori)
    except Exception:  # noqa: BLE001 - un avviso non deve mai bloccare
        _logger.warning("Avviso non registrabile nel Log operazioni.")


def _carica_connettori_marketplace(cr):
    """Importa i moduli marketplace installati, prima di leggere il registro.

    Questo post-migrate gira durante l'aggiornamento di integrations_core.
    Ma `carrier_brand_codes` vive nei moduli marketplace_* (marketplace_
    bricobravo, marketplace_manomano, ...), che DIPENDONO da integrations_core
    e che Odoo importa DOPO di esso nel ciclo di aggiornamento. Senza questo
    passo esplicito, CONNECTOR_REGISTRY sarebbe ancora vuoto quando gira
    `migrate()`: ogni riga finirebbe nel ramo "connettore non registrato" e
    l'anagrafica risulterebbe vuota, con nessuna spedizione più in grado di
    risolvere il corriere. Importiamo quindi qui, a runtime, solo i moduli
    marketplace risultanti installati sul database; un modulo che non si
    riesce a importare non deve bloccare la migrazione, quindi si logga e si
    prosegue con gli altri.

    Anche la scoperta in sé (interrogazione di ir_module_module) è protetta:
    il `try/except` Python cattura l'errore per non bloccare la migrazione,
    ma da solo non basta. Se la SELECT fallisse A LIVELLO DATABASE (lock,
    timeout, connessione), PostgreSQL metterebbe la transazione in stato
    abortito: il `try/except` assorbirebbe comunque l'eccezione, ma la
    prima istruzione SQL successiva (la ricerca delle righe da convertire,
    fuori da qualunque protezione) fallirebbe a sua volta con "current
    transaction is aborted", bloccando l'aggiornamento lo stesso. Per questo
    la query gira dentro un punto di ripristino: un suo eventuale fallimento
    resta confinato lì, la transazione principale resta pulita, il registro
    resta semplicemente vuoto e ogni riga finirà nel ramo "connettore non
    registrato" (già gestito e loggato più avanti).

    Lo stato da cercare NON è solo 'installed'. Quando l'aggiornamento parte
    da riga di comando (`odoo-bin -u integrations_core`), Odoo marca i moduli
    che dipendono da integrations_core come 'to upgrade' PRIMA di eseguire le
    migrazioni: filtrando il solo 'installed', marketplace_manomano e
    marketplace_bricobravo non verrebbero trovati né importati, il registro
    resterebbe vuoto e OGNI riga finirebbe nel ramo "connettore non
    registrato" — zero collegamenti creati, zero URL copiati, nessuna
    spedizione più in grado di risolvere il corriere. Aggiornando
    dall'interfaccia Apps il difetto non si vedrebbe (là il Python dei moduli
    è già importato in memoria): si manifesterebbe solo in consegna. Si
    accetta anche 'to install' per il caso in cui il connettore venga
    installato nello stesso comando.
    """
    try:
        with cr.savepoint():
            cr.execute(
                "SELECT name FROM ir_module_module "
                "WHERE state IN ('installed', 'to upgrade', 'to install') "
                "AND name LIKE 'marketplace_%'")
            moduli = cr.fetchall()
        for (nome,) in moduli:
            try:
                importlib.import_module("odoo.addons." + nome)
            except Exception:  # noqa: BLE001 - un connettore non importabile non blocca
                _logger.warning(
                    "Migrazione mappa corrieri: impossibile importare il modulo "
                    "'%s', le sue traduzioni corriere non saranno disponibili.",
                    nome)
    except Exception:  # noqa: BLE001 - la scoperta dei moduli non deve mai bloccare
        _logger.warning(
            "Migrazione mappa corrieri: impossibile leggere l'elenco dei moduli "
            "marketplace installati; il registro dei connettori resterà vuoto "
            "e le righe finiranno nel ramo 'connettore non registrato'.")


def _chiavi_vettore(riga):
    """(modello, id) del vettore di una riga v1: chiavi durevoli o corriere nativo.

    Le righe più vecchie hanno solo `carrier_id`: per loro il vettore è il
    corriere nativo di Odoo con quell'id.
    """
    source_model = riga.source_model
    source_res_id = riga.source_res_id
    if not (source_model and source_res_id) and riga.carrier_id:
        source_model = "delivery.carrier"
        source_res_id = riga.carrier_id.id
    return source_model, source_res_id


def _riprova_righe_non_invertibili(env, da_riprovare, contatori):
    """Seconda passata: le righe col codice non riconducibile a un corriere.

    Perché serve una seconda passata. Se il codice configurato su una riga non
    si può invertire (il connettore non lo dichiara più, o lo dichiarano due
    corrieri diversi), il collegamento vettore → corriere non nasce da quella
    riga. Ma lo STESSO vettore può avere un codice invertibile su un ALTRO
    canale: in quel caso il collegamento nasce comunque, e dal momento
    dell'aggiornamento anche il canale "saltato" risolverebbe il corriere e
    invierebbe il codice PREDEFINITO del connettore al posto del suo vecchio
    codice — cioè esattamente ciò che la promessa vincolante vieta (stesso
    codice e stesso URL di prima). La cura è un'eccezione in
    centrivo.carrier.override per quel canale, quel corriere e quell'azienda,
    col vecchio codice: le eccezioni vincono su tutto, quindi il
    comportamento resta identico.

    Perché DOPO il ciclo principale e non dentro: l'ordine delle righe non
    garantisce nulla, e il collegamento che rende necessaria l'eccezione può
    nascere da una riga successiva. Qui tutti i collegamenti esistono già.

    Se invece il vettore non risulta collegato a nessun corriere, non c'è
    nulla da preservare: il comportamento resta quello di prima (riga saltata)
    e l'avviso dice la verità, cioè che quel codice non verrà più inviato.

    L'eccezione NON si crea nemmeno quando cambierebbe il codice a un altro
    vettore: vale per tutto il canale e per tutti i vettori di quel corriere,
    quindi scriverla alla cieca sposterebbe il problema invece di risolverlo.
    Anche in quel caso si avvisa e non si scrive niente.

    Come tutto il resto, ogni riga gira in un punto di ripristino annidato
    dentro un try/except: nemmeno questa passata può impedire l'installazione
    del modulo. In particolare la create dell'eccezione può legittimamente
    fallire (il vecchio codice potrebbe non essere più nella lista chiusa
    dichiarata dal connettore): il fallimento diventa un avviso, non un
    blocco.
    """
    if not da_riprovare:
        return
    Map = env["centrivo.carrier.map"]
    Source = env["centrivo.carrier.source"]
    Override = env["centrivo.carrier.override"]
    for riga, source_model, source_res_id in da_riprovare:
        channel = False
        try:
            with env.cr.savepoint():
                channel = riga.channel_id
                company = riga.company_id
                collegamento = Source.resolve_brand(
                    source_model, source_res_id, company)
                if not collegamento:
                    _avviso(env, channel,
                            "Il codice '%s' configurato per il vettore '%s' non "
                            "è più riconducibile a un corriere, e quel vettore "
                            "non risulta collegato ad alcun corriere: da adesso "
                            "quel codice NON verrà più inviato per questo "
                            "vettore. Apri Integrations → Copertura corrieri e "
                            "collega il vettore al corriere giusto."
                            % (riga.external_code, riga.source_display or "?"),
                            contatori)
                    continue
                brand = collegamento.brand_id
                esistente = Override.search([
                    ("channel_id", "=", channel.id),
                    ("brand_id", "=", brand.id),
                    ("company_id", "=", company.id),
                ], limit=1)
                if esistente:
                    if esistente.external_code != riga.external_code:
                        _avviso(env, channel,
                                "Esiste già un'eccezione con codice '%s' per il "
                                "corriere %s su questo canale: il codice '%s' "
                                "del vettore '%s' non verrà inviato. Verifica "
                                "Integrations → Copertura corrieri."
                                % (esistente.external_code, brand.name,
                                   riga.external_code,
                                   riga.source_display or "?"), contatori)
                    continue

                # Ultimo controllo prima di scrivere, ed è quello che evita di
                # rimediare a una violazione creandone un'altra: l'eccezione
                # vale per TUTTO il canale e per TUTTI i vettori che ricadono
                # su quel corriere, non per il singolo vettore. Se sullo stesso
                # canale un altro vettore ricade sul medesimo corriere con un
                # codice DIVERSO, scrivere l'eccezione cambierebbe il codice
                # anche a quello. In quel caso non si scrive nulla e si avvisa:
                # il codice di questa riga si perde comunque, ma almeno non si
                # trascina dietro una riga che oggi funziona.
                altri_codici = set()
                for altra in Map.search([
                        ("id", "!=", riga.id),
                        ("channel_id", "=", channel.id),
                        ("company_id", "=", company.id)]):
                    altro_collegamento = Source.resolve_brand(
                        *_chiavi_vettore(altra), company)
                    if (altro_collegamento
                            and altro_collegamento.brand_id == brand
                            and altra.external_code != riga.external_code):
                        altri_codici.add(altra.external_code)
                if altri_codici:
                    _avviso(env, channel,
                            "Il codice '%s' del vettore '%s' non è più "
                            "riconducibile a un corriere e non è stato possibile "
                            "conservarlo: sullo stesso canale il corriere %s è "
                            "già usato con il codice '%s' da un altro vettore, e "
                            "un'eccezione lo cambierebbe anche a quello. Da "
                            "adesso per questo vettore verrà inviato il codice "
                            "previsto per %s. Verifica Integrations → Copertura "
                            "corrieri."
                            % (riga.external_code, riga.source_display or "?",
                               brand.name, ", ".join(sorted(altri_codici)),
                               brand.name), contatori)
                    continue

                Override.create({
                    "channel_id": channel.id,
                    "brand_id": brand.id,
                    "external_code": riga.external_code,
                    "company_id": company.id,
                })
                contatori["eccezioni"] += 1
                _logger.info(
                    "Migrazione mappa corrieri: creata l'eccezione '%s' per il "
                    "corriere %s sul canale %s, così il codice inviato resta "
                    "quello configurato per il vettore '%s'.",
                    riga.external_code, brand.name, channel.name,
                    riga.source_display or "?")
        except Exception as esc:  # noqa: BLE001 - una riga non deve mai bloccare
            _avviso(env, channel,
                    "Il codice '%s' del vettore '%s' non è più riconducibile a "
                    "un corriere e non è stato possibile conservarlo con "
                    "un'eccezione (%s): da adesso quel codice NON verrà più "
                    "inviato per questo vettore. Verifica Integrations → "
                    "Copertura corrieri."
                    % (riga.external_code, riga.source_display or "?", esc),
                    contatori)


def migrate(cr, version):
    if not version:
        return
    _carica_connettori_marketplace(cr)
    env = api.Environment(cr, SUPERUSER_ID, {})
    # active_test=False: un corriere archiviato deve comunque essere trovato,
    # altrimenti si otterrebbe il messaggio fuorviante "assente in anagrafica".
    Brand = env["centrivo.carrier.brand"].with_context(active_test=False)
    Source = env["centrivo.carrier.source"]

    # Anche la sola LETTURA dell'elenco da convertire è protetta: è
    # un'istruzione sul database come le altre e, se fallisse (lock, timeout,
    # tabella non leggibile), l'eccezione risalirebbe fuori da migrate() e
    # farebbe fallire l'aggiornamento del modulo. Il punto di ripristino serve
    # perché il try/except da solo non ripulisce la transazione; il return
    # subito dopo perché senza l'elenco non c'è nulla da convertire.
    try:
        with cr.savepoint():
            righe = env["centrivo.carrier.map"].search([])
    except Exception as esc:  # noqa: BLE001 - nemmeno la lettura deve bloccare
        _avviso(env, False,
                "Impossibile leggere l'elenco delle righe da convertire (%s). "
                "LA CONVERSIONE DELLA MAPPA CORRIERI NON E' STATA ESEGUITA: "
                "nessun corriere è stato convertito e la conversione va "
                "rifatta (aggiornare di nuovo il modulo integrations_core, "
                "una volta risolto il problema). L'aggiornamento del modulo "
                "prosegue comunque." % esc)
        return
    _logger.info("Migrazione mappa corrieri: %s righe da convertire.", len(righe))

    contatori = {"collegamenti": 0, "eccezioni": 0, "avvisi": 0}
    # Righe col codice non invertibile: si riesaminano in coda, quando tutti i
    # collegamenti esistono. Vedi _riprova_righe_non_invertibili.
    da_riprovare = []

    for riga in righe:
        # Valori di riserva per il messaggio d'errore: se anche la lettura di
        # channel_id/company_id (dentro il blocco protetto qui sotto)
        # sollevasse un'eccezione imprevista, _avviso riceverebbe comunque un
        # valore valido invece di un nome non definito.
        channel = False
        company = False
        try:
            with cr.savepoint():
                channel = riga.channel_id
                company = riga.company_id
                traduzioni = MarketplaceConnector.get_brand_codes_for(
                    channel.connector_code)
                if not traduzioni:
                    _avviso(env, channel,
                            "Connettore '%s' non registrato o senza traduzioni: "
                            "riga del vettore '%s' non convertita."
                            % (channel.connector_code, riga.source_display or "?"),
                            contatori)
                    continue

                source_model, source_res_id = _chiavi_vettore(riga)
                if not (source_model and source_res_id):
                    _avviso(env, channel,
                            "Riga senza vettore identificabile (id %s): saltata."
                            % riga.id, contatori)
                    continue

                brand_code = brand_for_code(riga.external_code, traduzioni)
                if not brand_code:
                    # Da questa riga non nasce nessun collegamento: il corriere
                    # non è deducibile. Ma il vecchio codice va comunque
                    # conservato, perché lo stesso vettore può essere collegato
                    # da un'altra riga e quel collegamento cambierebbe il codice
                    # inviato su QUESTO canale. La riga si mette da parte e si
                    # riesamina dopo il ciclo, quando tutti i collegamenti
                    # esistono. Vedi _riprova_righe_non_invertibili.
                    da_riprovare.append((riga, source_model, source_res_id))
                    continue
                brand = Brand.search([("code", "=", brand_code)], limit=1)
                if not brand:
                    _avviso(env, channel,
                            "Corriere '%s' assente in anagrafica: riga del vettore "
                            "'%s' non convertita."
                            % (brand_code, riga.source_display or "?"), contatori)
                    continue

                # 1) collegamento vettore -> corriere
                esistente = Source.search([
                    ("source_model", "=", source_model),
                    ("source_res_id", "=", source_res_id),
                    ("company_id", "=", company.id),
                ], limit=1)
                if not esistente:
                    Source.create({
                        "source_model": source_model,
                        "source_res_id": source_res_id,
                        "source_display": riga.source_display
                        or (riga.carrier_id.display_name if riga.carrier_id else ""),
                        "brand_id": brand.id,
                        "company_id": company.id,
                    })
                    contatori["collegamenti"] += 1
                elif esistente.brand_id != brand:
                    # Non si indovina: si tiene il collegamento già presente
                    # e ci si ferma qui, altrimenti il passo successivo
                    # scriverebbe l'URL di tracciamento sul corriere appena
                    # dedotto (quello scartato), che spedizioni di un altro
                    # vettore legittimamente collegato non usano.
                    _avviso(env, channel,
                            "Il vettore '%s' risulta collegato a corrieri diversi "
                            "su canali diversi (%s e %s): tenuto il primo."
                            % (riga.source_display or "?",
                               esistente.brand_id.name, brand.name), contatori)
                    continue

                # 2) URL di tracciamento sul corriere, senza sovrascrivere
                if riga.tracking_url_template and not brand.tracking_url_template:
                    brand.tracking_url_template = riga.tracking_url_template
                elif (riga.tracking_url_template
                      and brand.tracking_url_template != riga.tracking_url_template):
                    _avviso(env, channel,
                            "URL di tracciamento diversi per il corriere %s: "
                            "tenuto '%s', scartato '%s'."
                            % (brand.name, brand.tracking_url_template,
                               riga.tracking_url_template), contatori)

                # 3) nessuna eccezione da creare PER QUESTA RIGA: il corriere è
                # stato dedotto invertendo la stessa tabella del connettore
                # (brand_for_code), quindi il codice che il connettore
                # userebbe di default per quel corriere coincide, per
                # costruzione, con riga.external_code. Il round-trip da solo
                # garantisce che il codice inviato resti quello di sempre.
                # Le righe con un codice NON invertibile (nessun corriere
                # trovato, o più di uno sullo stesso codice) sono un caso
                # diverso e sono messe da parte sopra: per quelle l'eccezione
                # serve, e la crea la seconda passata qui sotto.
        except Exception as esc:  # noqa: BLE001 - una riga non deve mai bloccare
            _avviso(env, channel,
                    "Errore imprevisto durante la conversione della riga %s "
                    "(vettore '%s'): %s"
                    % (riga.id, riga.source_display or "?", esc), contatori)

    _riprova_righe_non_invertibili(env, da_riprovare, contatori)

    esito = "success" if contatori["avvisi"] == 0 else "skip"
    riepilogo = ("Migrazione mappa corrieri completata: %s righe esaminate, "
                 "%s collegamenti creati, %s eccezioni create, %s avvisi."
                 % (len(righe), contatori["collegamenti"],
                    contatori["eccezioni"], contatori["avvisi"]))
    _logger.info(riepilogo)
    try:
        with cr.savepoint():
            env["centrivo.job.log"].sudo().create({
                "operation": "carrier_migration",
                "result": esito,
                "message": riepilogo,
            })
    except Exception:  # noqa: BLE001 - il riepilogo non deve mai bloccare
        _logger.warning("Riepilogo della migrazione non registrabile nel Log "
                         "operazioni.")
