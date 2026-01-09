/** @odoo-module **/

import { loadJS } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { getColor, hexToRGBA } from "@web/core/colors/colors";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { EmiproDashboardGraph } from "@common_connector_library/views/fields/graph_widget_ept/graph_widget_ept";

import { Component, onWillStart, useEffect, useRef } from "@odoo/owl";
import { cookie } from "@web/core/browser/cookie";
import { useService } from "@web/core/utils/hooks";
import { actionService } from "@web/webclient/actions/action_service";

export class EmiproDashboardGraphAmzEpt extends EmiproDashboardGraph {
    static template = "amazon_ept.EmiproDashboardGraphAmzEpt";
    static props = {
        ...standardFieldProps,
        graphType: String,
    };

    constructor() {
        super(...arguments);
        this.selectedDrpOption = "";
    }

    setup() {
        super.setup(...arguments);
        this.orm = useService("orm");
        this.action = useService('action');

        onWillStart(async () => {
            await loadJS("/web/static/lib/jquery/jquery.js");
        });
    }
    /* Update required amazon data */
    getUpdatedData(currentTarget){
        var self = this;
        if(self.match_key == 'amazon_order_data'){
         if(self.graph_data.fulfillment_by == 'FBA'){
            if(self.graph_data.total_sales && self.graph_data.fba_order_data && self.graph_data.fba_order_data.order_count){
               if(self.graph_data.fba_order_data.order_count != 0){
                    $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + Math.round(self.graph_data.total_sales / self.graph_data.fba_order_data.order_count))
               } else {
                    $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + 0)
               }
            } else {
                $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + 0)
            }
         } else if (self.graph_data.fulfillment_by == 'FBM'){
            if(self.graph_data.total_sales && self.graph_data.fbm_order_data && self.graph_data.fbm_order_data.order_count){
               if(self.graph_data.fbm_order_data.order_count != 0){
                    $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + Math.round(self.graph_data.total_sales / self.graph_data.fbm_order_data.order_count))
               } else {
                    $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + 0)
               }
            } else {
                $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + 0)
            }

         } else if (self.graph_data.fulfillment_by == 'Both'){
            if(self.graph_data.total_sales && self.graph_data.fbm_order_data && (self.graph_data.fbm_order_data.order_count || self.graph_data.fba_order_data.order_count)){
                if(self.graph_data.fbm_order_data.order_count != 0 || self.graph_data.fba_order_data.order_count != 0){
                    $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + Math.round(self.graph_data.total_sales / (self.graph_data.fba_order_data.order_count + self.graph_data.fbm_order_data.order_count)))
                } else {
                    $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + 0)
                }
            } else {
                $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + 0)
            }
         }
       } else {
            if(self.graph_data.total_sales && self.graph_data.order_data && self.graph_data.order_data.order_count){
                if(self.graph_data.order_data.order_count != 0){
                    $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + Math.round(self.graph_data.total_sales / self.graph_data.order_data.order_count))
                } else {
                    $(currentTarget).parent('div').find('.ep_graph_details .col-5 b').html(self.graph_data.currency_symbol + 0)
                }
            }
       }

       if(self.match_key == 'amazon_order_data'){
            if(self.graph_data.fbm_order_data){
                $(currentTarget).parent('div').find('#instance_fbm_order > p:first-child').html(self.graph_data.fbm_order_data && self.graph_data.fbm_order_data.order_count ? self.graph_data.fbm_order_data.order_count: 0);
            }
            if(self.graph_data.fba_order_data){
                $(currentTarget).parent('div').find('#instance_fba_order > p:first-child').html(self.graph_data.fba_order_data && self.graph_data.fba_order_data.order_count ? self.graph_data.fba_order_data.order_count : 0);
            }
       }
    }
    /* Override method */
    onchangeSortOrderData(e) {
        var self = this;
        var context = {...this.context}
        context.sort = e.currentTarget.value
        context.fulfillment_by = $(e.currentTarget).siblings('#sort_order_data_amazone').val()
        var currentTarget = e.currentTarget
        return this.orm.call(
            this.props.record.resModel,
            "read",
            [this.props.record.resId], {context: context}
        ).then(function (result) {
            if(result.length) {
                self.updateGraphData(JSON.parse(result[0][self.match_key]), currentTarget);
                self.getUpdatedData(currentTarget);
            }
        })

    }

    _sortAmazonOrders(e) {
       var self = this;
       var context = {...this.context}
       context.fulfillment_by = e.currentTarget.value
       context.sort = $(e.currentTarget).siblings('#sort_order_data').val()
       var currentTarget = e.currentTarget
       return this.orm.call(
           this.props.record.resModel,
           "read",
           [this.props.record.resId], {context: context}
       ).then(function (result) {
           if(result.length) {
               self.updateGraphData(JSON.parse(result[0][self.match_key]), currentTarget);
               self.getUpdatedData(currentTarget);
           }
       })
    }

     /* Render action for  Products */
    _getProducts() {
        return this.action.doAction(this.graph_data.product_date.product_action);
    }

    /*Render action for  Sales Order */
    _getFbmOrders() {
        return this.action.doAction(this.graph_data.fbm_order_data.order_action);
    }

    /*Render action for  Sales Order */
    _getFbaOrders() {
        return this.action.doAction(this.graph_data.fba_order_data.order_action);
    }
}

export const emiproDashboardGraphAmzEpt = {
    component: EmiproDashboardGraphAmzEpt,
    supportedTypes: ["text"],
    extractProps: ({ attrs }) => ({
        graphType: attrs.graph_type,
    }),
};

registry.category("fields").add("dashboard_graph_amz_ept", emiproDashboardGraphAmzEpt);