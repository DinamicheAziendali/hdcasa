/** @odoo-module **/

import { registry } from '@web/core/registry';
import { kanbanView } from '@web/views/kanban/kanban_view';

registry.category('views').add('sale_integration_kanban_view', {
  ...kanbanView,
  buttonTemplate: 'integration.SaleIntegrationKanbanView.Buttons',
});
