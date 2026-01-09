# See LICENSE file for full copyright and licensing details.

from odoo.tests import tagged
from odoo.tools import mute_logger
from odoo.exceptions import UserError
from odoo.addons.integration.tools import Adapter

from .init_integration_shopify import IntegrationShopifyBase
from .patch.shopify_api_patch import ShopifyAPIClientPatchTest, ShopifyClientPatchTest, ShopifyGraphQLPatchTest


ORDER_ID = '100500'
ORDER_ID_FP = '100510'


@tagged('post_install', '-at_install', 'test_integration_shopify')
class TestIntegrationShopify(IntegrationShopifyBase):

    def setUp(self):
        super(TestIntegrationShopify, self).setUp()

    def test_get_product_accounts(self):
        # Product 1
        res = self.product1.product_tmpl_id.with_company(self.company).get_product_accounts()
        self.assertEqual(
            res['income'].id,
            self.env.ref('integration_shopify.integration_shopify_account_a_income').id,
        )
        self.assertEqual(
            res['expense'].id,
            self.env.ref('integration_shopify.integration_shopify_account_a_expense').id,
        )

        # Product 2
        res = self.product2.product_tmpl_id.with_company(self.company).get_product_accounts()
        self.assertEqual(
            res['income'].id,
            self.env.ref('integration_shopify.integration_shopify_account_a_income').id,
        )
        self.assertEqual(
            res['expense'].id,
            self.env.ref('integration_shopify.integration_shopify_account_a_expense').id,
        )

    def test_get_wh(self):
        self.assertEqual(self.integration._get_wh_from_external_location('73153839000'), self.wh)

    def test_integration_settings_patch(self):
        self.assertTrue(isinstance(self.adapter, Adapter))
        self.assertTrue(isinstance(self.adapter._Adapter__cache_core, ShopifyAPIClientPatchTest))
        self.assertTrue(isinstance(self.adapter._client, ShopifyClientPatchTest))
        self.assertTrue(isinstance(self.adapter._graphql, ShopifyGraphQLPatchTest))

        self.assertEqual(self.adapter._integration_id, self.integration.id)
        self.assertEqual(self.adapter._integration_name, self.integration.name)

    @mute_logger('odoo.addons.integration_shopify.shopify.tools')
    def test_fetch_order(self):
        order_dict = self.integration.adapter.receive_order(ORDER_ID)
        data = order_dict['data']

        self.assertEqual(order_dict['id'], int(ORDER_ID))
        self.assertEqual(data['name'], '#1166X01')
        self.assertEqual(data['customer_locale'], 'pl')
        self.assertEqual(data['customer']['email'], 'przecietny-kowalski@mail.pl')
        self.assertEqual(data['customer']['first_name'], 'Przeciętny')
        self.assertEqual(data['customer']['last_name'], 'Kowalski')
        self.assertEqual(data['customer']['default_address']['first_name'], 'Przeciętny')
        self.assertEqual(data['customer']['default_address']['last_name'], 'Kowalski')
        self.assertEqual(data['currency'], 'PLN')
        self.assertEqual(data['billing_address']['country_code'], 'PL')
        self.assertEqual(data['shipping_address']['zip'], '01-123')
        self.assertEqual(data['customer']['id'], 6670178025000)
        self.assertEqual(data['customer']['currency'], 'PLN')
        self.assertEqual(data['tags'], 'ttag1, ttag2')
        self.assertEqual(data['note'], 'Just note from customer')
        self.assertEqual(data['location_id'], 73153839000)
        self.assertEqual(data['payment_terms'], None)
        self.assertEqual(data['refunds'], [])
        self.assertEqual(data['shipping_lines'], [])
        self.assertEqual(data['channel_id'], '100500')
        self.assertEqual(data['created_at'], '2024-10-08T14:03:31+02:00')
        self.assertEqual(len(data['fulfillments']), 2)
        self.assertEqual(len(data['line_items']), 2)

    @mute_logger('odoo.addons.integration_shopify.shopify.tools')
    def test_create_order_from_input(self):
        # 1. Create input file
        self.integration.test_method_parameter = ORDER_ID
        input_file = self.integration.integrationApiReceiveOrder()

        self.assertEqual(input_file.si_id.id, self.integration.id)
        self.assertEqual(input_file.name, ORDER_ID)
        self.assertFalse(input_file.order_id)
        self.assertTrue(input_file.update_required)

        # 2. Create order from input file
        input_file.update_required = False
        input_file = self._set_permissions(input_file)

        parsed_data = input_file.parse()
        self.assertEqual(parsed_data['id'], ORDER_ID)
        self.assertEqual(parsed_data['external_location_id'], '73153839000')

        order = input_file.process_no_job()

        # 2.1 Check order
        self.assertEqual(order.name, '#1166X01')
        self.assertEqual(len(order.order_line), 2)
        self.assertEqual(order.company_id, self.company)
        self.assertEqual(order.currency_id, self.env.ref('base.PLN'))

        self.assertEqual(order.partner_id.type, 'contact')
        self.assertEqual(order.partner_id.company_type, 'person')
        self.assertEqual(order.partner_id.name, 'Przeciętny Kowalski')
        self.assertEqual(order.partner_id.city, 'Warszawa')
        # self.assertEqual(order.partner_id.phone, '+48177774456')
        self.assertEqual(order.partner_id.email, 'przecietny-kowalski@mail.pl')
        self.assertEqual(order.partner_id.country_id, self.env.ref('base.pl'))
        self.assertEqual(order.partner_id.zip, '01-123')
        self.assertEqual(order.partner_id.street, 'Górna 22')
        self.assertFalse(order.partner_id.company_name)
        self.assertFalse(order.partner_id.is_company)
        self.assertEqual(order.partner_id.lang, self.env.ref('base.lang_pl').code)
        self.assertEqual(order.partner_id.category_id.name, self.integration.name)

        self.assertEqual(order.pricelist_id.company_id, self.company)
        self.assertEqual(order.pricelist_id.currency_id, self.env.ref('base.PLN'))

        self.assertEqual(round(order.amount_untaxed, 1), 1746.0)
        self.assertEqual(round(order.amount_tax, 2), 401.58)
        self.assertEqual(round(order.amount_total, 2), 2147.58)

        # First line
        line = order.order_line[0]
        self.assertEqual(line.warehouse_id, self.wh)
        self.assertEqual(line.product_id.default_code, 'gtp3-ref-1-shopify-test')
        self.assertEqual(round(line.product_qty, 2), 2.0)
        self.assertEqual(round(line.qty_to_deliver, 2), 2.0)

        self.assertEqual(round(line.price_unit, 1), 123.0)
        self.assertEqual(round(line.price_subtotal, 1), 246.0)
        self.assertEqual(round(line.price_tax, 2), 56.58)
        self.assertEqual(round(line.price_total, 2), 302.58)

        # Second line
        line = order.order_line[1]
        self.assertEqual(line.warehouse_id, self.wh)
        self.assertEqual(line.product_id.default_code, 'guit1-sku-bl-shopify-test')
        self.assertEqual(round(line.product_qty, 1), 3.0)
        self.assertEqual(round(line.qty_to_deliver, 1), 3.0)

        self.assertEqual(round(line.price_unit, 1), 500.0)
        self.assertEqual(round(line.price_subtotal, 1), 1500.0)
        self.assertEqual(round(line.price_tax, 1), 345.0)
        self.assertEqual(round(line.price_total, 0), 1845)

        # 2.2 Integration fields
        self.assertEqual(order.integration_id, self.integration)

        self.assertEqual(order.sub_status_id.name, 'Paid')
        self.assertEqual(order.shopify_fulfilment_status.name, 'Shipped')

        self.assertEqual(order.payment_method_id.integration_id, self.integration)
        self.assertEqual(order.payment_method_id.name, 'manual_in_shopify_test')
        self.assertEqual(order.external_sales_order_ref, '#1166X01')

        self.assertEqual(len(order.external_tag_ids), 2)
        self.assertEqual(order.external_tag_ids.integration_id, self.integration)
        self.assertEqual(set(order.external_tag_ids.mapped('name')), {'ttag1', 'ttag2'})

        self.assertEqual(len(order.external_fulfillment_ids), 2)
        self.assertEqual(order.external_fulfillment_ids.integration_id, self.integration)
        self.assertEqual(set(order.external_fulfillment_ids.mapped('name')), {'#1166X01.1', '#1166X01.2'})
        self.assertEqual(set(order.external_fulfillment_ids.mapped('external_status')), {'success'})
        self.assertEqual(set(order.external_fulfillment_ids.mapped('internal_status')), {'draft'})

        self.assertEqual(len(order.external_payment_ids), 1)
        self.assertEqual(order.external_payment_ids.integration_id, self.integration)
        self.assertEqual(order.external_payment_ids.external_str_id, '10679267000000')
        self.assertEqual(order.external_payment_ids.amount, '147.58')
        self.assertEqual(order.external_payment_ids.currency, 'PLN')

        self.assertEqual(len(order.order_risk_ids), 0)

        # 2.3 Check pipeline
        pipeline = order.integration_pipeline
        pipeline.ensure_one()
        self.assertTrue(all(pipeline.pipeline_task_ids.mapped(lambda x: x.state == 'skip')))
        self.assertEqual(set(pipeline.sub_state_external_ids.mapped('code')), {'paid', 'fulfilled'})
        self.assertFalse(pipeline.skip_dispatch)
        self.assertFalse(pipeline.invoice_journal_id)
        self.assertFalse(pipeline.payment_journal_id)
        self.assertFalse(pipeline.current_info)
        self.assertEqual(pipeline.input_file_id, input_file)

        # 3. Run pipeline

        # Activate tasks
        pipeline.pipeline_task_ids.write({'state': 'todo'})
        self.integration.apply_external_fulfillments = True  # Create two pickings automatically (Shopify feature)
        self.integration.apply_external_payments = True  # Create payments automatically (Shopify feature)

        self.env['stock.quant'].create({
            'product_id': self.product1.id,
            'location_id': self.wh.lot_stock_id.id,
            'quantity': 100,
        })
        self.env['stock.quant'].create({
            'product_id': self.product2.id,
            'location_id': self.wh.lot_stock_id.id,
            'quantity': 100,
        })

        # 3.1 validate_order
        task1 = pipeline.pipeline_task_ids.filtered(lambda x: x.current_step_method == 'validate_order')

        self.assertEqual(order.state, 'draft')
        self.assertEqual(len(order.picking_ids), 0)

        task1.run()

        self.assertEqual(task1.state, 'done')
        self.assertEqual(set(order.external_fulfillment_ids.mapped('internal_status')), {'done'})

        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.delivery_status, 'full')
        self.assertEqual(order.invoice_status, 'to invoice')
        self.assertEqual(len(order.picking_ids), 2)
        self.assertEqual(order.picking_ids[0].state, 'done')
        self.assertEqual(order.picking_ids[1].state, 'done')

        # 3.2 validate_picking
        task2 = pipeline.pipeline_task_ids.filtered(lambda x: x.current_step_method == 'validate_picking')
        self.assertEqual(task2.state, 'todo')

        task2.run()

        self.assertEqual(task2.state, 'done')
        self.assertEqual(len(order.picking_ids), 2)

        # 3.3 create_invoice
        task3 = pipeline.pipeline_task_ids.filtered(lambda x: x.current_step_method == 'create_invoice')
        self.assertEqual(task3.state, 'todo')

        with self.assertRaises(UserError) as ex:
            task3.run()

        self.assertIn('No Invoice Journal defined', str(ex.exception))

        pipeline.sub_state_external_ids\
            .filtered(lambda x: x.code == 'paid') \
            .invoice_journal_id = self.invoice_journal.id

        self.assertEqual(pipeline.invoice_journal_id, self.invoice_journal)

        # Define partner's accounts
        partner = order.partner_id.with_company(self.company)
        partner.property_account_receivable_id = self.env.ref('integration_shopify.integration_shopify_account_a_recv')
        partner.property_account_payable_id = self.env.ref('integration_shopify.integration_shopify_account_a_pay')

        task3.run()

        self.assertEqual(task3.state, 'done')

        self.assertEqual(order.invoice_status, 'invoiced')

        invoice = order.actual_invoice_ids
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.state, 'draft')
        self.assertEqual(invoice.invoice_origin, order.name)
        self.assertEqual(invoice.move_type, 'out_invoice')
        self.assertEqual(invoice.country_code, 'PL')
        self.assertEqual(invoice.currency_id, self.env.ref('base.PLN'))
        self.assertEqual(invoice.payment_state, 'not_paid')
        self.assertEqual(invoice.integration_id, self.integration)
        self.assertEqual(round(invoice.amount_untaxed, 1), 1746.0)
        self.assertEqual(round(invoice.amount_tax, 2), 401.58)
        self.assertEqual(round(invoice.amount_total, 2), 2147.58)
        self.assertEqual(round(invoice.amount_residual, 2), 2147.58)

        # 3.4 validate_invoice
        task4 = pipeline.pipeline_task_ids.filtered(lambda x: x.current_step_method == 'validate_invoice')

        self.assertEqual(task4.state, 'todo')

        # first attempt
        with self.assertRaises(UserError) as ex:
            task4.run()  # Assert applying external payments

        self.assertTrue(
            f'No Payment Journal defined for Payment Method "{order.payment_method_id.name}"' in str(ex.exception)
        )

        journal = self.env.ref('integration_shopify.integration_shopify_account_cash_journal')
        pipeline.payment_method_external_id.payment_journal_id = journal.id

        def _get_outstanding_account_patch(self, payment_type):
            if payment_type == 'inbound':
                return self.env.ref('integration_shopify.integration_shopify_account_debit')
            return self.env['account.account']

        self.patch(type(self.env['account.payment']), '_get_outstanding_account', _get_outstanding_account_patch)

        # second attempt
        task4.run()

        self.assertEqual(task4.state, 'done')
        self.assertEqual(set(order.external_payment_ids.mapped('internal_status')), {'done'})

        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(round(invoice.amount_residual, 1), 2000.0)

        self.assertEqual(invoice.payment_state, 'partial')

        payment = self.env['account.payment'].search([('invoice_ids', 'in', invoice.id)])
        payment.ensure_one()
        self.assertEqual(payment.state, 'paid')
        self.assertEqual(round(payment.amount, 2), 147.58)

        # 3.5 send invoice
        task5 = pipeline.pipeline_task_ids.filtered(lambda x: x.current_step_method == 'send_invoice')
        self.assertEqual(task5.state, 'todo')

        # Patch wizard action to avoid actually sending email
        def _mock_action_send_and_print(self):
            invoice.write({'is_move_sent': True})
            return {'type': 'ir.actions.act_window_close'}  # Simulate success

        self.patch(
            type(self.env['account.move.send.wizard']),
            'action_send_and_print',
            _mock_action_send_and_print,
        )

        self.assertFalse(invoice.is_move_sent)

        task5.run()

        self.assertEqual(task5.state, 'done')
        self.assertTrue(invoice.is_move_sent)

        # 3.6 register_payment
        task6 = pipeline.pipeline_task_ids.filtered(lambda x: x.current_step_method == 'register_payment')
        self.assertEqual(task6.state, 'todo')

        def _get_available_payment_method_lines_patch(self, payment_type):
            if not self:
                return self.env['account.payment.method.line']

            self.ensure_one()
            if payment_type == 'inbound':
                return self.env.ref('integration_shopify.integration_shopify_line_check_in')

            return journal.outbound_payment_method_line_ids

        self.patch(type(journal), '_get_available_payment_method_lines', _get_available_payment_method_lines_patch)

        task6.run()

        self.assertEqual(task6.state, 'done')

        self.assertEqual(round(invoice.amount_residual, 1), 0.0)
        self.assertEqual(invoice.payment_state, 'paid')

        payments = self.env['account.payment'].search([('invoice_ids', 'in', invoice.id)])
        self.assertEqual(len(payments), 2)
        self.assertEqual(payments[0].state, 'paid')
        self.assertEqual(payments[1].state, 'paid')
        self.assertEqual(round(sum(payments.mapped('amount')), 2), 2147.58)

    @mute_logger('odoo.addons.integration_shopify.shopify.tools')
    def test_order_fiscal_position(self):
        # 1. Create input file
        self.integration.test_method_parameter = ORDER_ID_FP
        input_file = self.integration.integrationApiReceiveOrder()

        self.assertEqual(input_file.si_id.id, self.integration.id)
        self.assertEqual(input_file.name, ORDER_ID_FP)
        self.assertFalse(input_file.order_id)
        self.assertTrue(input_file.update_required)

        # 2. Parse order from input file
        input_file.update_required = False
        input_file = self._set_permissions(input_file)

        parsed_data = input_file.parse()
        self.assertEqual(parsed_data['id'], ORDER_ID_FP)
        self.assertEqual(parsed_data['customer']['email'], 'j.hatf.shopify.test@myshopify.test.com')
        self.assertEqual(parsed_data['customer']['person_name'], 'James Hatf')

        self.assertEqual(parsed_data['billing']['email'], 'j.hatf.shopify.test@myshopify.test.com')
        self.assertEqual(parsed_data['billing']['person_name'], 'James Hatf')
        self.assertEqual(parsed_data['billing']['phone'], '+48534612001')
        self.assertEqual(parsed_data['billing']['company_name'], 'J. Hat T-Co')
        self.assertEqual(parsed_data['billing']['street'], 'Trojanowska 71')
        self.assertEqual(parsed_data['billing']['city'], 'Sochaczew')
        self.assertEqual(parsed_data['billing']['zip'], '96-500')
        self.assertEqual(parsed_data['billing']['country_code'], 'PL')

        self.assertEqual(parsed_data['shipping']['email'], 'j.hatf.shopify.test@myshopify.test.com')
        self.assertEqual(parsed_data['shipping']['person_name'], 'James Hatf')
        self.assertEqual(parsed_data['shipping']['phone'], '+48534612001')
        self.assertEqual(parsed_data['shipping']['company_name'], 'J. Hat T-Co')
        self.assertEqual(parsed_data['shipping']['street'], 'Trojanowska 71')
        self.assertEqual(parsed_data['shipping']['city'], 'Sochaczew')
        self.assertEqual(parsed_data['shipping']['zip'], '96-500')
        self.assertEqual(parsed_data['shipping']['country_code'], 'PL')

        # 2. Create customer from parsed data
        PartnerFactory = self.env['integration.res.partner.factory'].create_factory(
            self.integration.id,
            customer_data=parsed_data['customer'],
            billing_data=parsed_data['billing'],
            shipping_data=parsed_data['shipping'],
        )

        partner, addresses = PartnerFactory.get_partner_and_addresses()

        # 2.1 Partner
        self.assertEqual(partner.name, 'James Hatf')
        self.assertFalse(partner.company_name)
        self.assertFalse(partner.external_company_name)
        self.assertEqual(partner.email, 'j.hatf.shopify.test@myshopify.test.com')
        # self.assertFalse(partner.phone)
        # self.assertFalse(partner.mobile)
        self.assertEqual(partner.lang, self.env.ref('base.lang_en').code)
        self.assertEqual(partner.category_id.name, self.integration.name)

        self.assertEqual(partner.commercial_partner_id.type, 'contact')
        self.assertEqual(partner.commercial_partner_id.name, 'J. Hat T-Co')
        self.assertEqual(partner.commercial_partner_id.company_type, 'company')

        # 2.2 Partner parent
        self.assertTrue(partner.parent_id)
        self.assertTrue(partner.parent_id.name, 'J. Hat T-Co')
        self.assertTrue(partner.parent_id.type, 'contact')
        self.assertTrue(partner.parent_id.company_type, 'company')

        # 2.3 Billing
        billing = addresses['billing']

        # TODO: Fix this test
        # self.assertEqual(billing.type, 'other')
        self.assertEqual(billing.company_type, 'person')
        # self.assertEqual(billing.external_company_name, 'J. Hat T-Co')
        self.assertEqual(billing.name, 'James Hatf')
        self.assertEqual(billing.city, 'Sochaczew')
        self.assertEqual(billing.street, 'Trojanowska 71')
        self.assertFalse(billing.street2)
        # self.assertEqual(billing.phone, '+48534612001')
        # self.assertFalse(billing.mobile)
        self.assertEqual(billing.email, 'j.hatf.shopify.test@myshopify.test.com')
        self.assertEqual(billing.country_id, self.env.ref('base.pl'))
        self.assertEqual(billing.zip, '96-500')
        self.assertFalse(billing.company_name)
        self.assertFalse(billing.is_company)
        self.assertEqual(billing.lang, self.env.ref('base.lang_en').code)
        self.assertEqual(billing.category_id.name, self.integration.name)
        self.assertEqual(billing.commercial_partner_id.id, partner.commercial_partner_id.id)

        # 2.4 Shipping
        shipping = addresses['shipping']

        # TODO: Fix this test
        # self.assertEqual(shipping.type, 'other')
        self.assertEqual(shipping.company_type, 'person')
        # self.assertEqual(shipping.external_company_name, 'J. Hat T-Co')
        self.assertEqual(shipping.name, 'James Hatf')
        self.assertEqual(shipping.city, 'Sochaczew')
        self.assertEqual(shipping.street, 'Trojanowska 71')
        self.assertFalse(shipping.street2)
        # self.assertEqual(shipping.phone, '+48534612001')
        # self.assertFalse(shipping.mobile)
        self.assertEqual(shipping.email, 'j.hatf.shopify.test@myshopify.test.com')
        self.assertEqual(shipping.country_id, self.env.ref('base.pl'))
        self.assertEqual(shipping.zip, '96-500')
        self.assertFalse(shipping.company_name)
        self.assertFalse(shipping.is_company)
        self.assertEqual(shipping.lang, self.env.ref('base.lang_en').code)
        self.assertEqual(shipping.category_id.name, self.integration.name)
        self.assertEqual(shipping.commercial_partner_id.id, partner.commercial_partner_id.id)

        tax_23 = self.env.ref('integration_shopify.integration_shopify_account_tax_23')
        tax_21 = self.env.ref('integration_shopify.integration_shopify_account_tax_21')

        # 3. Process input file without customer's fiscal position
        order = input_file.process_no_job()

        self.assertEqual(order.partner_id.id, partner.id)
        self.assertEqual(order.partner_invoice_id.id, billing.id)
        self.assertEqual(order.partner_shipping_id.id, shipping.id)
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_id.default_code, 'guitar-cl-shopify-test-1')
        self.assertEqual(order.order_line.tax_id.id, tax_23.id)
        self.assertEqual(order.partner_shipping_id.id, shipping.id)

        self.assertFalse(order.fiscal_position_id)
        self.assertEqual(round(order.amount_untaxed, 1), 1723.0)
        self.assertEqual(round(order.amount_tax, 2), 396.29)
        self.assertEqual(round(order.amount_total, 2), 2119.29)

        order.unlink()

        # 3. Process input file with customer's fiscal position and the update_fiscal_positionf flag as True
        self.integration.update_fiscal_position = True

        fiscal_position = self.env['account.fiscal.position'].create({
            'name': 'Fiscal 23 -> 21 Shopify Test',
            'company_id': self.company.id,
            'country_id': self.env.ref('base.pl').id,
            'tax_ids': [(0, 0, {'tax_src_id': tax_23.id, 'tax_dest_id': tax_21.id})],
        })

        # Assign FP to the !!!Parent
        partner.parent_id.with_company(self.company).property_account_position_id = fiscal_position.id

        self.assertEqual(
            partner.property_account_position_id.id,
            False,
        )
        self.assertEqual(
            partner.with_company(self.company).property_account_position_id.id,
            fiscal_position.id,
        )

        order = input_file.process_no_job()

        self.assertEqual(order.partner_id.id, partner.id)
        self.assertEqual(order.partner_invoice_id.id, billing.id)
        self.assertEqual(order.partner_shipping_id.id, shipping.id)
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_id.default_code, 'guitar-cl-shopify-test-1')

        self.assertEqual(order.order_line.tax_id.id, tax_21.id)
        self.assertEqual(order.fiscal_position_id.id, fiscal_position.id)
        self.assertEqual(order.show_update_fpos, False)

        self.assertEqual(round(order.amount_untaxed, 1), 1723.0)
        self.assertEqual(round(order.amount_tax, 2), 361.83)
        self.assertEqual(round(order.amount_total, 2), 2084.83)

        # 4. Process input file with customer's fiscal position and the update_fiscal_positionf flag as False
        self.integration.update_fiscal_position = False
        order.unlink()

        order = input_file.process_no_job()

        self.assertEqual(order.partner_id.id, partner.id)
        self.assertEqual(order.partner_invoice_id.id, billing.id)
        self.assertEqual(order.partner_shipping_id.id, shipping.id)
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_id.default_code, 'guitar-cl-shopify-test-1')

        self.assertEqual(order.order_line.tax_id.id, tax_23.id)
        self.assertEqual(order.fiscal_position_id.id, fiscal_position.id)
        self.assertEqual(order.show_update_fpos, True)

        self.assertEqual(round(order.amount_untaxed, 1), 1723.0)
        self.assertEqual(round(order.amount_tax, 2), 396.29)
        self.assertEqual(round(order.amount_total, 2), 2119.29)
