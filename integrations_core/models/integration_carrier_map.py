# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.carrier.map — mapping corriere Odoo <-> codice corriere del marketplace.

Generico e per-canale: lo stesso delivery.carrier Odoo può corrispondere a codici
diversi su marketplace diversi, quindi il mapping è specifico del canale.

Il codice esterno (`external_code`) NON è testo libero: è un Selection le cui
opzioni sono dichiarate DAL CONNETTORE del canale (vedi
MarketplaceConnector.carrier_codes). Selezionando un canale BricoBravo si scelgono
solo i codici BricoBravo; quando arriveranno altri connettori (ManoMano, ecc.)
ciascuno dichiarerà la propria lista senza toccare questo modello.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..connectors.base import MarketplaceConnector
from ..connectors.carrier_resolver import NON_COLLEGATO, NON_TRADOTTO, code_for_brand


class CarrierResolution(object):
    """Esito della risoluzione del corriere: si comporta come la vecchia riga.

    I connettori usano soltanto `external_code`, `tracking_url_template` e la
    verità/falsità del risultato: questo oggetto offre esattamente quelli, più
    la causa del fallimento per scegliere il messaggio giusto.
    """

    def __init__(self, external_code="", tracking_url_template="",
                 reason="", brand_name=""):
        self.external_code = external_code
        self.tracking_url_template = tracking_url_template
        self.failure_reason = reason
        self.brand_name = brand_name

    def __bool__(self):
        return bool(self.external_code)


class IntegrationCarrierMap(models.Model):
    _name = "centrivo.carrier.map"
    _description = "Mapping corriere verso codice corriere del marketplace"
    _sql_constraints = [
        ("channel_carrier_company_uniq",
         "unique(channel_id, carrier_id, company_id)",
         "Esiste già un mapping per questo corriere su questo canale e "
         "azienda."),
    ]
    # Il vincolo SQL sopra copre solo carrier_id (i NULL delle righe a chiavi
    # durevoli non collidono mai in PostgreSQL, quindi non blocca quel caso):
    # resta comunque l'unica protezione a livello di database contro
    # concorrenza e import, e continua a valere anche per le righe native
    # perché l'inverse riallinea carrier_id ogni volta che la sorgente è
    # delivery.carrier. Il controllo applicativo _check_unique_mapping
    # copre in aggiunta le righe a chiavi durevoli (vettori di terzi).

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True,
        help="Il mapping è specifico del canale: lo stesso corriere Odoo può "
             "mappare a codici diversi su marketplace diversi.")

    carrier_id = fields.Many2one(
        "delivery.carrier", string="Corriere Odoo",
        ondelete="cascade",
        help="Resta per compatibilità e per il caso nativo, cioè quando il "
             "vettore da mappare è un metodo di consegna Odoo. Per un "
             "vettore di terzi (es. Dinamiche Aziendali) usare invece "
             "'Seleziona vettore' qui sotto.")

    # --- Chiavi DUREVOLI del vettore -------------------------------------
    # Sopravvivono alla disinstallazione del modulo di terzi che fornisce il
    # vettore: restano leggibili (modello, id, nome per esteso) anche quando il
    # record sorgente non è più raggiungibile. Non sono `required` a livello di
    # campo perché vengono riempite in create/write PRIMA del vincolo, e perché
    # un NOT NULL bloccherebbe la migrazione sulle righe esistenti.
    source_model = fields.Char(
        string="Modello sorgente", index=True,
        help="Modello tecnico del record vettore (es. delivery.carrier).")
    source_res_id = fields.Integer(
        string="ID sorgente", index=True,
        help="Id del record vettore nel modello sorgente.")
    source_display = fields.Char(
        string="Vettore",
        help="Nome del vettore per esteso, conservato per restare leggibile "
             "anche se il modulo che lo forniva viene disinstallato.")
    source_record_key = fields.Selection(
        selection="_selection_source_records", string="Seleziona vettore",
        store=False, compute="_compute_source_record_key",
        inverse="_inverse_source_record_key",
        help="Vettore da mappare. La tendina elenca i record del modello "
             "puntato dal campo configurato in Configurazione integrazioni.")

    @api.model
    def _get_source_model(self):
        """Modello puntato dal campo sorgente configurato.

        Lettura DINAMICA: se il campo non esiste (modulo di terzi non
        installato) o non è risolvibile, si ripiega sul corriere nativo
        'delivery.carrier'.
        """
        name = self.env["centrivo.integration.config"].get_carrier_source_field_name()
        field = self.env["stock.picking"]._fields.get(name)
        if field is not None and field.type == "many2one":
            return field.comodel_name
        return "delivery.carrier"

    @api.model
    def _selection_source_records(self):
        """Opzioni della tendina: i record del modello sorgente.

        Ritorna coppie (str(id), nome). Se il modello non è disponibile o non ha
        record, lista vuota: nessuna eccezione.
        """
        model = self._get_source_model()
        if model not in self.env:
            return []
        records = self.env[model].sudo().search([], limit=1000)
        return sorted(
            [(str(rec.id), rec.display_name or ("#%s" % rec.id)) for rec in records],
            key=lambda opzione: opzione[1])

    @api.depends("source_res_id", "source_model")
    def _compute_source_record_key(self):
        """Valorizza la tendina SOLO se il modello sorgente combacia con quello attuale.

        Scenario reale, ed è esattamente il motivo per cui questo controllo
        esiste: dopo la migrazione, tutte le righe di produzione hanno
        source_model = 'delivery.carrier'. Se l'utente cambia il campo
        sorgente configurato (es. su transport_carrier_id di un modulo di
        terzi), `_selection_source_records` ripopola la tendina coi record
        di transport.carrier — e un id nudo come "7" individua un vettore
        completamente diverso a seconda del modello a cui appartiene. Senza
        confrontare source_model col modello sorgente ATTUALE, la tendina
        selezionerebbe in automatico "il transport.carrier #7" al posto del
        delivery.carrier #7 mappato in precedenza: un ripuntamento silenzioso
        su un vettore diverso, che al salvataggio l'inverse trascriverebbe
        nelle chiavi durevoli, corrompendo il mapping (e quindi il codice e
        l'URL di tracking inviati al marketplace). Per questo, se il modello
        non combacia, si ritorna False: la tendina resta vuota e l'utente è
        costretto a ri-selezionare esplicitamente il vettore giusto.
        """
        current_model = self._get_source_model()
        for record in self:
            if record.source_res_id and record.source_model == current_model:
                record.source_record_key = str(record.source_res_id)
            else:
                record.source_record_key = False

    def _inverse_source_record_key(self):
        """Dalla scelta in tendina alle tre chiavi durevoli.

        Scrive SOLO se il valore scelto differisce da quello che il compute
        avrebbe già mostrato per la riga (stesso id, stesso modello sorgente
        attuale): così l'apertura e il salvataggio di una form senza toccare
        il campo non riscrive mai le chiavi durevoli, e non può alterare per
        errore una riga il cui vettore era già corretto.
        """
        model = self._get_source_model()
        for record in self:
            if not record.source_record_key:
                continue
            if (record.source_res_id and record.source_model == model
                    and str(record.source_res_id) == record.source_record_key):
                continue
            res_id = int(record.source_record_key)
            source = self.env[model].sudo().browse(res_id).exists()
            if not source:
                # Il record scelto in tendina è stato cancellato nel
                # frattempo: non si scrive nulla, meglio lasciare la riga
                # come stava che salvare un vettore inesistente.
                continue
            record.source_model = model
            record.source_res_id = res_id
            record.source_display = source.display_name or ("#%s" % res_id)
            # Compatibilità: se la sorgente è il corriere nativo, si tiene
            # allineato anche carrier_id, così le installazioni che non hanno
            # moduli di terzi continuano a vedere il campo di sempre.
            if model == "delivery.carrier":
                record.carrier_id = res_id

    @api.model_create_multi
    def create(self, vals_list):
        """Crea le righe e forza subito il controllo di integrità sul vettore.

        `@api.constrains` viene valutato da Odoo solo per i campi presenti
        nei vals passati al create: un create che non porta né carrier_id né
        le chiavi durevoli (import, file di dati, altro modulo) non fa
        scattare `_check_source` da solo, e una riga senza alcun vettore
        entrerebbe in tabella. Qui il controllo si invoca esplicitamente sui
        record appena creati, indipendentemente da quali campi fossero nei
        vals.
        """
        records = super().create(vals_list)
        records._check_source()
        return records

    @api.constrains("source_model", "source_res_id", "carrier_id")
    def _check_source(self):
        """Una riga deve identificare un vettore: chiavi durevoli o corriere nativo."""
        for record in self:
            if record.source_model and record.source_res_id:
                continue
            if record.carrier_id:
                continue
            raise ValidationError(
                "Indica il vettore da mappare: seleziona un vettore oppure un "
                "corriere Odoo.")

    @api.constrains("channel_id", "company_id", "carrier_id", "source_model",
                     "source_res_id")
    def _check_unique_mapping(self):
        """Un solo mapping per vettore, per canale e azienda.

        Sostituisce il vecchio vincolo SQL unique(channel_id, carrier_id,
        company_id): con carrier_id diventato opzionale quel vincolo non
        intercetterebbe più i duplicati sulle chiavi durevoli (due righe con
        carrier_id vuoto ma stesso source_model/source_res_id non lo
        violerebbero, perché in SQL i NULL sono sempre distinti tra loro).
        La verifica applicativa copre entrambi i casi.
        """
        for record in self:
            domain = [
                ("id", "!=", record.id),
                ("channel_id", "=", record.channel_id.id),
                ("company_id", "=", record.company_id.id),
            ]
            if record.source_model and record.source_res_id:
                domain += [
                    ("source_model", "=", record.source_model),
                    ("source_res_id", "=", record.source_res_id),
                ]
            elif record.carrier_id:
                domain.append(("carrier_id", "=", record.carrier_id.id))
            else:
                continue
            if self.search_count(domain):
                raise ValidationError(
                    "Esiste già un mapping per questo vettore su questo "
                    "canale e azienda.")

    @api.model
    def resolve_external_code(self, channel, source_model, source_res_id, company):
        """Codice corriere e URL di tracciamento per quel vettore su quel canale.

        Legge i modelli NUOVI (anagrafica corrieri, collegamento vettore →
        corriere, eccezioni); le righe di questo modello non vengono più
        consultate. La firma resta quella di prima e il risultato espone gli
        stessi due attributi usati dai connettori, così i loro file non
        cambiano.

        Ritorna un CarrierResolution: falso se la risoluzione non riesce, con
        `failure_reason` che distingue le due cause (vettore non collegato /
        corriere non tradotto), perché sono problemi diversi e vanno detti in
        modo diverso.
        """
        link = self.env["centrivo.carrier.source"].resolve_brand(
            source_model, source_res_id, company)
        if not link:
            return CarrierResolution(reason=NON_COLLEGATO)
        brand = link.brand_id
        override = self.env["centrivo.carrier.override"].resolve_code(
            channel, brand, company)
        codice = code_for_brand(
            brand.code,
            MarketplaceConnector.get_brand_codes_for(channel.connector_code),
            override_code=override)
        if not codice:
            return CarrierResolution(reason=NON_TRADOTTO, brand_name=brand.name)
        return CarrierResolution(
            external_code=codice,
            tracking_url_template=brand.tracking_url_template or "",
            brand_name=brand.name)

    # Selection DINAMICO: le opzioni sono i codici dichiarati dai connettori.
    # Vedi nota in _selection_external_code sul perché si ritorna l'UNIONE.
    external_code = fields.Selection(
        selection="_selection_external_code",
        string="Codice corriere marketplace", required=True,
        help="Codice del corriere come atteso dal marketplace. Le opzioni "
             "dipendono dal connettore del canale selezionato (lista chiusa).")

    tracking_url_template = fields.Char(
        string="Template URL tracking", required=True,
        help="URL di tracciamento con il segnaposto {tracking}, che verrà "
             "sostituito dal numero di tracking. "
             "Esempio: https://vivi.brt.it/?tracking={tracking}")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company)

    @api.model
    def _selection_external_code(self):
        """Opzioni del Selection `external_code`: UNIONE dei codici di tutti i connettori.

        Un campo Selection deve dichiarare TUTTI i valori memorizzabili (su
        qualunque canale), altrimenti Odoo marca invalidi i valori non in lista.
        La restrizione "su un canale BricoBravo si scelgono SOLO i codici
        BricoBravo" è applicata dall'onchange (_onchange_channel_id) e dal vincolo
        (_check_external_code). Oggi il solo connettore registrato è BricoBravo,
        quindi qui compaiono esattamente i suoi 16 codici.
        """
        codes = MarketplaceConnector.get_all_carrier_codes()
        return codes or [("other", "Altro")]

    def _codes_for_channel(self, channel):
        """Dict {codice: etichetta} dei codici validi per il connettore del canale."""
        if not channel:
            return {}
        return dict(MarketplaceConnector.get_carrier_codes_for(channel.connector_code))

    @api.onchange("channel_id")
    def _onchange_channel_id(self):
        """Quando cambia il canale, azzera un external_code non più valido.

        Così, cambiando canale, non resta selezionato un codice appartenente a
        un altro connettore. (Il web client mostra l'unione dei codici; il
        vincolo garantisce comunque la coerenza al salvataggio.)
        """
        if self.external_code and self.channel_id:
            if self.external_code not in self._codes_for_channel(self.channel_id):
                self.external_code = False

    @api.constrains("external_code", "channel_id")
    def _check_external_code(self):
        """Garantisce che external_code appartenga al connettore del canale."""
        for rec in self:
            valid = rec._codes_for_channel(rec.channel_id)
            if rec.external_code and rec.external_code not in valid:
                raise ValidationError(
                    "Il codice corriere '%s' non è valido per il connettore "
                    "del canale '%s'. Codici ammessi: %s."
                    % (rec.external_code, rec.channel_id.name,
                       ", ".join(sorted(valid)) or "nessuno"))
