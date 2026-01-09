from odoo import fields, models, api, _


class InboundShipmentReportDataEPT(models.Model):
    _name = 'inbound.shipment.report.data.ept'
    _description = 'Inbound Shipment Report Data'

    inbound_shipment_id = fields.Many2one(comodel_name='inbound.shipment.new.ept', string='Inbound Shipment')
    amz_old_shipment_id = fields.Many2one(comodel_name='amazon.inbound.shipment.ept', string='Amazon Old Inbound Shipment')
    inbound_shipment_ref = fields.Char(string='Inbound Shipment Ref')
    seller_sku = fields.Char(string='Seller Sku')
    shipped_qty = fields.Float(string='Shipped Quantity')
    received_qty = fields.Float(string='Received Quantity')
    current_status = fields.Char(string='Current Status')
    company_id = fields.Many2one('res.company', string='Inbound Shipment Report Data Company')
    is_display_record = fields.Boolean(string='Display Inbound Shipment Data', compute="_compute_is_display_record",
                                       search="_search_is_display_record", store=False)

    def _search_is_display_record(self, operator, operand):
        """ Filter record having mismatch between shipped and received quantity."""
        ids = [rec.id for rec in self.search([]) if rec.shipped_qty != rec.received_qty]
        return [('id', 'in', ids)]

    def _compute_is_display_record(self):
        """
        Compute the record to displayed or not based shipped and received quantity.
        """
        for rec in self:
            rec.is_display_record = True if rec.shipped_qty != rec.received_qty else False

    def create_shipment_report_data(self, inbound_ship_report_data, seller_id, shipment_model_type='new'):
        """
        Creates or updates the inbound shipment report data based on the available shipped and received quantities.
        :param inbound_ship_report_data : list()
        :param seller_id : amazon.seller.ept()
        :param shipment_model_type : str, 'new' or 'old' for the type of shipment model.
        """
        instance_ids = self.env['amazon.seller.ept'].browse(seller_id).instance_ids.ids
        if shipment_model_type == 'new':
            shipment_model = self.env['inbound.shipment.new.ept']
            shipment_id_field = 'shipment_confirmation_id'
        else:
            shipment_model = self.env['amazon.inbound.shipment.ept']
            shipment_id_field = 'shipment_id'

        for data in inbound_ship_report_data:
            shipment_id = data.get('shipment_id')
            status = data.get('status')
            shipment = shipment_model.search([(shipment_id_field, '=', shipment_id),
                                              ('instance_id_ept', 'in', instance_ids)])
            if shipment:
                if not self.search([('amz_old_shipment_id' if shipment_model_type == 'old' else 'inbound_shipment_id', '=',
                                     shipment.id)]):
                    for item in data.get('items'):
                        self.create({
                            'amz_old_shipment_id' if shipment_model_type == 'old' else 'inbound_shipment_id': shipment.id,
                            'inbound_shipment_ref': getattr(shipment, shipment_id_field),
                            'seller_sku': item.get('seller_sku'), 'shipped_qty': item.get('qty_shipped'),
                            'received_qty': item.get('qty_received'), 'current_status': status,
                            'company_id': shipment.company_id.id
                        })
                else:
                    for item in data.get('items'):
                        ship_rpt_rec = self.search([
                            ('amz_old_shipment_id' if shipment_model_type == 'old' else 'inbound_shipment_id', '=',
                             shipment.id), ('seller_sku', '=', item.get('seller_sku'))
                        ])
                        ship_rpt_rec.write({'shipped_qty': item.get('qty_shipped'), 'received_qty': item.get('qty_received'),
                            'current_status': status
                        })
        return True
