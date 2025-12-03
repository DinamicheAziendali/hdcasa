/** @odoo-module **/

import { registry } from '@web/core/registry';
import { listView } from '@web/views/list/list_view';

registry.category('views').add('sale_integration_list_view', {
  ...listView,
  buttonTemplate: 'integration.SaleIntegrationListView.Buttons',
});
