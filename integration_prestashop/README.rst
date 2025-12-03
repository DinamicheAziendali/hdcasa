PrestaShop Integration
======================

Feedback
########
|

- In case of any issues, please contact us at support@ventor.tech
- Don't forget to share your experience after you go live :)

  | (only person who made a purchase, can leave ratings)

|

Change Log
##########

|

* 1.15.1 (2024-01-05)
    - NEW! On odoo.sh when the backup is restored on the staging branch, disable automatic all sales integrations, disable on integrations critical functions (export of products, order statuses, product inventory) and delete webhooks.
    - Refactored logic of mapping products.
    - Improved orders processing: imported orders data will be marked as "require update" to make sure that the latest updates will be downloaded during Sales Order creation in Odoo.
    - Fixed an issue with stock synchronization for products with zero stock.
    - Fixed for order cancellation: orders cancelled in external e-commerce system will be automatically cancelled if they were imported to Odoo.
    - Other small fixes and improvements.

* 1.15.0 (2023-11-05)
    - NEW! Improved price calculation through configurable rounding for price export.
    - NEW! Added the possibility to export the Delivery Time field. `(watch video) <https://youtu.be/yIWK6ReBngA>`__
    - Improved logic of states auto-mapping.
    - Improved connectors' UI/UX.
    - Improved image naming logic for products with lengthy names or with special symbols in product names.
    - Improved calculation of discount on prices with includes taxes.
    - Added support of discounts for delivery lines in Odoo.
    - Improved detection of changes in product attributes, including images, to trigger product export.
    - Added integration settings export/import wizard.
    - Fixed issue with products serialization for export to e-commerce system when 'en_US' language is inactive in Odoo.
    - Fixed export of translatable fields with empty values.
    - Fixed issue with export of images and stock during the first-time export.
    - Fixed issue with mapping product attributes / features values.
    - Added check for group permissions in Quick Configuration Wizard.
    - Removed deprecated functionality for importing payments.
    - Other small fixes and improvements.

* 1.14.1 (2023-09-29)
    - Fixed issue with auto-workflow not executing all tasks

* 1.14.0 (2023-09-19)
    - NEW! Added the ability to exclude specific products from Stock Synchronization with the use of special checkbox in the E-commerce tab on the product form. `(watch video) <https://www.youtube.com/watch?v=l9Mu3eCPBds>`__
    - Fixed issue with updating translatable fields when default ERP language different to PrestaShop  language.
    - Fixed issue with missed orders.
    - Fixed issue with exporting tracking number for pickings with product kits.
    - Fixed sending tax-excluded sale price (B2B) from Odoo to the E-commerce system.
    - Added unit tests for testing field mapping logic within the integration module.
    - Other small improvements and fixes.

* 1.13.0 (2023-08-14)
    - NEW! Add setting for export prices via price list from Odoo to PrestaShop. Configurable based on integration. `(watch video) <https://www.youtube.com/watch?v=Q9Hh1okL3bw&ab_channel=VentorTech>`__
    - NEW! Upgrade export images for product template and product variants. For product variants which have image set in Odoo after exporting in PrestaShop this image will be placed as main on product variant form.
    - NEW! Set forcibly discount to zero to avoid affection of the price list with policy "Show public price & discount to the customer".
    - NEW! Improve automatic mapping of country states to Odoo country states.

* 1.12.0 (2023-07-19)
    - NEW! Added the possibility of automatic import of pricelists from Prestashop to Odoo. `(watch video) <https://youtu.be/6WxW-_vzOTM>`__
    - NEW! Added setting to automatically create products on SO Import in case products doesn’t exist yet in Odoo. Configurable based on integration. `(watch video) <https://www.youtube.com/watch?v=b0aBh9XCNCI&ab_channel=VentorTech>`__
    - NEW! During initial import, the connector will generate only product variants that exist in Prestashop. This behavior is configurable on the “Product Defaults“ tab on sales integration with the checkbox “Import Attributes as Dynamic“. It is switched off by default. `(watch video) <https://youtu.be/esONyR7kZ7A>`__
    - NEW! Add new behavior on empty tax “Take from the Product“. When selected, if the downloaded sales order line will not have defined taxes, it will insert on the sales order line customer tax defined on the product. `(watch video) <https://youtu.be/bShKi6TZbtc>`__
    - NEW! Allow excluding specific product attributes to synchronize from Odoo to Prestashop. Can be configured in “Sales - Configuration - Attributes“. `(watch video) <https://youtu.be/LZvrutgifuU>`__
    - NEW! Discount for individual products is added as a separate line on Odoo Sales Order for proper financial records. `(watch video) <https://youtu.be/OvymmCkTsi0>`__
    - NEW! Allow switching on and off validation of missing barcodes on product variants. When “Validate missing barcodes for variants“ is enabled then the connector will validate that either all variants should have barcodes, or neither of the variants should have barcodes (the mix is not allowed). Available only in Debug mode on the “Product Defaults“ tab. `(watch video) <https://youtu.be/sL4ZOO7swpg>`__
    - In case it is configured not to download the barcode field from Prestashop to Odoo (in Product Fields Mapping there is no barcode field defined) connector will not analyze external products for duplicated barcodes.
    - Download orders by batches to avoid timeout of “Receive Orders” job.
    - Do not send inactive product variants when exporting products to Prestashop.
    - Added to sales integration list of global fields that are monitored for changes. So when the product is updated and these fields are changed, then we also trigger the export of the product.
    - Product attributes are synchronized according to their sequence to preserve the same order as in Odoo.
    - FIX: Cost price on Products with variants during initial import from Prestashop were not saved.
    - Other small improvements and fixes.

* 1.11.3 (2023-05-19)
    - Fix issue with receiving orders for different time zones.

* 1.11.2 (2023-04-04)
    - Fix issue with duplicated product price for products with variants on initial product import.

* 1.11.1 (2023-03-23)
    - Fix issue with impossibility to cancel sales order (in some cases) or register payment.

* 1.11.0 (2023-03-13)
    - NEW! Allow importing inactive product categories during the product's initial import. Added configuration “Import Inactive Categories“ on the “Product Defaults“ tab (visible in Debug mode). `(watch video) <https://youtu.be/B-qFUhsTCUY>`__
    - NEW! Added “Exclude from Synchronisation” settings on the product to exclude specific products and all their variants totally from sync and all related logic (validation, auto-mapping). `(watch video) <https://youtu.be/7zO2y0Q6aS8>`__
    - NEW! Contacts that were created by the connector will have a special Tag with the name of the sales integration it was created from. That allows us to easier find all contacts created from specific integration. `(watch video) <https://youtu.be/0a0r-RDeNag>`__
    - Copy “e-Commerce payment method” from Sales Order to the related Customer Invoice.
    - Sales Orders with a non-valid EU VAT number will be created. But a warning message will be added in Internal Note for the created Sales Order informing the user about this problem.
    - Convert weight on import/export of products in case UoM in Odoo is different from UoM in Prestashop (kgs vs lbs).
    - When sending the Shipped flag for the order also send Delivery Date equal to the date when Order was shipped.
    - Other small fixes and improvements.

* 1.10.0 (2023-02-17)
    - NEW! Reworked product import and export mechanism. Now for simple fields, no coding is required to synchronize them from/to Odoo. Fields mapping working both for initial import (Prestashop -> Odoo) and for export (Odoo -> Prestashop). `(watch video) <https://youtu.be/VPsw1F51aYE>`__
    - NEW! Trigger products export only if fields that are marked with the “Send field for updating“ checkbox are updated. That leads to a smaller number of export product jobs. `(watch video) <https://youtu.be/ye-z8xtqKro>`__
    - NEW! Now all integration logs are available in a separate menu "Job Logs". It is possible to see everything that happened to a specific Product or Sales Order in a quick way. `(watch video) <https://youtu.be/06b1kPVFYno>`__
    - NEW! Add the possibility to define the "Orders Cut-off" date. Only orders created after this date will be synchronized. `(watch video) <https://youtu.be/AyqOlhyiFuc>`__
    - NEW! Link Odoo Pricelists to existing Customer Groups in Prestashop. Send pricelist items from Odoo to Prestashop. `(watch video) <https://youtu.be/RMCS-Fsw4q4>`__
    - NEW! Synchronize product brand from Odoo to PrestaShop and from PrestaShop to Odoo (in case this field was added with a custom module like OCA “product_brand“). `(watch video) <https://youtu.be/GpV11bcO2UM>`__
    - NEW! Added the possibility to synchronize “Availability preferences“ from Odoo to Prestashop and from Prestashop to Odoo. Should be manually switched on. `(watch video) <https://youtu.be/y5X07wnNapA>`__
    - Make ZIP code a non-required field for contact creation during sales order import as some countries do not require it.
    - PERFORMANCE! Overall performance improvements for the requests to Prestashop.
    - Other small fixes and improvements.

* 1.9.2 (2023-01-24)
    - Fix Customer VAT (Registration) number import.

* 1.9.1 (2023-01-06)
    - Fix issue when en_US language is deactivated.
    - Add Sale Integration in product on Import Product From External.

* 1.9.0 (2022-12-28)
    - NEW! Add a setting to send products from Odoo on initial export in “inactive“ status, so products can be reviewed later and published manually. `(watch video) <https://youtu.be/UkHizPacfsw>`__
    - NEW! Allow defining payment terms that will be used instead of the standard on Order synchronization depending on the payment method of the sales order. `(watch video) <https://youtu.be/gDSbEe1GEGQ>`__
    - NEW! Trigger new products export only if a product has non-empty fields that are mandatory for product export. The list of fields is defined on the integration level and by default, it is “Internal Reference“ only. `(watch video) <https://youtu.be/-6ruWO7qVHE>`__
    - NEW! Send the "Paid" status to Prestashop after the order is fully paid in Odoo. `(watch video) <https://youtu.be/tpH-5M-ZGgM>`__
    - NEW! Added global config to allow sending tax included OR tax excluded sales price. `(watch video) <https://youtu.be/0VbrJceXibw>`__
    - NEW! Allow defining special ZERO tax that will be used in case there are no taxes defined on the imported sales order line. `(watch video) <https://youtu.be/4Pyw_HETjaM>`__
    - NEW! Allow saving information from Prestashop “other“ field on customer address to any text field in Odoo partner. `(watch video) <https://youtu.be/DSBWmrLOIzI>`__
    - Export tracking number in case it is added after Picking is moved to the "Done" state (when using some third-party connectors).
    - Improve connector to allow exporting more than 10K products.
    - Added a new field on the customer to have “Company Name” as a separate field. This field is also used when displaying customer addresses on Odoo forms and on printed PDF forms (e.g. Invoices, Pickings and etc.)
    - Implement proper application of discounts from Prestashop orders to Odoo orders.
    - Set the order date in Odoo to be the same as in the Prestashop order. Previously it was changed by Odoo standard mechanism during order confirmation.
    - Fix auto-workflow action “Validate Picking“ not validating pickings in case of multi-step delivery.
    - “Force Export to External“ action on products is now sending products to Prestashop even if automatic products export from Odoo is disabled in integration settings.
    - Skip importing inactive categories for products during initial product import.
    - Other small fixes and improvements.

* 1.8.6 (2022-12-16)
    - Fixed bug when importing with value assignment in different languages.

* 1.8.5 (2022-12-14)
    - Fixed creation of mappings during the initial product import.

* 1.8.4 (2022-11-25)
    - Fixed import or products when there are duplicate product attributes.

* 1.8.3 (2022-11-07)
    - Added compatibility with partner_firstname module from OCA.
    - Fixed import of gift line.

* 1.8.2 (2022-10-28)
    - Fixed Feature Value creation.
    - Fixed “Import External Records“ running for Product Variants from Jobs.
    - Fixed calculation of discount in Odoo if there are several taxes in sales order.

* 1.8.1 (2022-10-18)
    - Import customers functionality was not working with all queue_job module versions.

* 1.8.0 (2022-10-10)
    - NEW! Allow exporting of product quantities both in real-time and by cron. Make it configurable on the “Inventory“ tab on sales integration. `(watch video) <https://youtu.be/qpNzJk2G3Lk>`__
    - NEW! Allow defining which field should be synchronized when sending the stock to the e-Commerce system. Allowing 3 options: “Free To Use Quantity“, “On Hand Quantity” and  “Forecasted Quantity”. `(watch video) <https://youtu.be/8c7yw2QT5fY>`__
    - NEW! Implemented wizard allowing to import customers based on the last update date. `(watch video) <https://youtu.be/f__ZMptKj7A>`__
    - NEW! Implementing Gift Wrap synchronization from Prestashop to Odoo as a separate line in sales orders. `(watch video) <https://youtu.be/mLA4yu729Z4>`__
    - NEW! Added setting to allow automatic creation of Delivery Carrier and Taxes in Odoo if the existing mapping is not found (during initial import and during Sales Order Import). `(watch video) <https://youtu.be/FmKa8gu4PpM>`__
    - Fix issue with auto-workflow failing in some cases when SO status is changing on webhook.
    - When an order is created with an existing partner make sure to also emulate the selection of partner on the Odoo interface so needed fields from the partner will be filled in (Payment Terms, Fiscal Positions and etc.).
    - TECHNICAL! Improve the retry mechanism for importing products and executing workflow actions to workaround concurrent update errors in some cases (e.g. sales order was not auto-confirmed and remained in draft state).
    - Do not create webhooks automatically in case integration is activated. Users need to do it manually by clicking the “Create Webhooks“ button on “Webhooks“ tab inside integration.
    - Set the proper fiscal position on automatic order import according to Fiscal Position settings.
    - Improved manual mapping of product variants and product templates in case template has only 1 variant.

* 1.7.1 (2022-09-08)
    - Added possibility to specify additional field where Sales Order reference from Prestashop will be added (for example "Client Reference" field on SO). `(watch video) <https://youtu.be/Fmx80pKh4Vc>`__
    - Fix synchronization of newsletter subscription status.
    - Sales Order date is now set equal to Order creation date from the Prestashop.
    - Improve functionality for partners creation (first search partner by full address, before creating a new one).

* 1.7.0 (2022-09-05)
    - **NEW!** Major feature. Introduced auto workflow that allows based on sales order status: to validate sales order, create and validate invoice for it and register payment on created invoice. Configuration is flexible and can be done individually for every SO status. `(watch video) <https://youtu.be/DEskoCQ-4Ek>`__
    - **NEW!** Added automatic creation of Webhooks to track Order Status change on the Prestashop side. Requires paid third-party module from Prestashop addons webshop “Webhooks integration Module“ Link to module https://addons.prestashop.com/en/third-party-data-integrations-crm-erp/48921-webhooks-integration.html `(watch video) <https://youtu.be/cqXjQ6_4I24>`__.
    - **NEW!** Auto-cancel Sales Order on Odoo side when Order is Cancelled on Prestashop side. Requires paid third-party module from Prestashop addons webshop “Webhooks integration Module“ (see link above). `(watch video) <https://youtu.be/uIJc7pzoFzs>`__
    - **NEW!** Change Sales Order sub-status to "Shipped" when all transfers related to it are "Done" or "Cancelled". `(watch video) <https://youtu.be/-j5pdsHS9z4>`__
    - **NEW!** Save to Odoo newsletter subscription status for the customer (is subscribed?,  date of subscription, date of user Registration). Only set during first customer creation. `(watch video) <https://youtu.be/WfdN3FhFYaE>`__
    - **NEW!** Separate functionality of products mapping (trying to map with existing Odoo Product) from products import (trying to map and if not found create product in Odoo). `(watch video) <https://youtu.be/hNqCVyv5fcY>`__
    - Allow to disable export of product images from Odoo to Prestashop (checkbox on Integration form -> "Product Defaults" tab).
    - When carrier details are changed on Prestashop side, no need to add mapping of delivery carrier again in Odoo.
    - During creation of sales order if mapping for product was not found trying to auto-map by reference OR barcode with existing Odoo Product before failing creation of sales order.
    - Send tracking numbers only when sales order is fully shipped (all related pickings are either "done" or "cancelled" and there are at least some delivered items).
    - Import from Prestashop to Odoo only Feature Values that are connected to some Feature.
    - Fix issue with synchronizing records (features, attributes and etc) with special symbols in their name ("%", "_" , etc.).
    - Fix issue with impossibility to import orders with deleted customer (set "Default Customer" on Sale Integration -> "Sale Order Details" tab).
    - TECHNICAL: Added possibility to easier extend product search criteria (for importing and validating products).
    - TECHNICAL: Updated prestapyt library to new version 0.11.1 to remove deprecated warnings for Python 3 (See requirements.txt file in the module).
    - TECHNICAL Improved Performance to allow importing of 150 000+ products from Prestashop.

* 1.6.0 (2022-07-21)
    - **NEW!** Automatically Cancel order on Prestashop when it is marked as Cancelled on Odoo side.
    - **NEW!** Product Features: Synchronize from Prestashop to Odoo during initial import. `(watch video) <https://www.youtube.com/watch?v=6ucwcLhhOlw>`__
    - **NEW!** Product Features: Sync from Odoo to Prestashop (when changing/creating on Odoo side). `(watch video) <https://www.youtube.com/watch?v=6ucwcLhhOlw>`__
    - **NEW!** Synchronise Optional Products from Odoo to Prestashop (requires to add Optional Products field to fields mapping). `(watch video) <https://www.youtube.com/watch?v=6ucwcLhhOlw>`__
    - **NEW!** Add possibility to synchronize optional products from Odoo to Prestashop. `(watch video) <https://www.youtube.com/watch?v=6ucwcLhhOlw>`__
    - Search only for active combinations when validating Prestashop products for duplicates.
    - When creating sales order from Prestashop, also set current sales order status as it is in Presta.
    - Fix issue with product validation results when Prestashop admin URL cannot be opened (if contains uppercase letters).
    - Add compatibility for older Prestashop versions where on order row there is no id_customization.
    - Added the ability to work both with the Manufacturing module and without it.
    - Added the ability to work both with the eCommerce module and without it.
    - Add possibility to Synchronize Products Cost Price from Odoo to Prestashop.
    - Improve categories synchronisation (automatically sync parent categories together with child, remove Root category from initial synchronisation as it is useless). `(watch video) <https://www.youtube.com/watch?v=XNNHPlNPoLk>`__
    - TECHNICAL: Added possibility to easily extend module for adding custom fields. `(watch video) <https://www.youtube.com/watch?v=sBXCKvOdQ9w>`__
    - Validate Countries and States for duplicates and if any found, then show error message with list of all problematic countries/states.

* 1.5.5 (2022-06-16)
    - Do not delete redundant combinations on Prestashop side in case we unset checkbox for specific integration on the Product.
    - Fix issue with initial creation of Product with variants when checkbox for integration is set.
    - Automatically cleanup non-existing external product and product variants records (in case not found in Prestashop).
    - Before exporting products from Odoo to Prestashop double check that same product already exists in Presta. If exists then map it automatically by internal reference.
    - Fix issue with not downloading of products with customizations.

* 1.5.4 (2022-06-12)
    - Download tax rules at the same time as downloading taxes.
    - Associate automatically tax rules with taxes.

* 1.5.3 (2022-06-02)
    - Allow definition of the mapping between taxes and tax rules using Quick Configuration Wizard.
    - Improve product taxes import and export between Odoo and Prestashop (using taxes/tax rules mapping).
    - Fix shipping taxes calculations (now possible to have more then one tax on shipping line).
    - Added functionality to import payment transactions (containing transaction_id) to Odoo. It is using OCA module sale_advance_payment.

* 1.5.2 (2022-05-16)
    - Solve issue with multi-company setup and automatic sales order download.
    - Synchronize all countries from Prestashop (not only active).
    - Set proper currency on Sales Order if it is different from company standard.
    - Multi-step delivery: Send tracking number ONLY for outgoing picking.

* 1.5.1 (2022-05-09)
    - Retrieve only active states from Prestashop.

* 1.5.0 (2022-05-01)
    - Added Quick Configuration Wizard.
    - Added taxes and tax groups quick manual import.
    - Version of prestapyt library changed to 0.10.1
    - Fixed initial payment methods import.
    - Fixed import BOMs with no product variant components.
    - Fixed incorrect tax rate applied to order shipping line.
    - When integration is deleted, also delete related Sales Order download Scheduled Action.
    - When importing sales order, payment method is also created if it doesn't exist.

* 1.4.4 (2022-04-20)
    - Added filter by active countries and states in initial import.
    - Fixed order import when line has several taxes.
    - Fixed product import.

* 1.4.3 (2022-03-31)
    - Added import of payment method before creating an order if it does not exists.
    - Added integration info in Queue Job for errors with mapping.
    - Added possibility to import product categories by action “Import Categories“ in menus “External → Categories“ and “Mappings → Categories“.
    - Added button "Import Product" on unmapped products in menu “Mapping → Products“.
    - Fixed issue with export new products.
    - Fixed product and product variant mapping in initial import.
    - Fixed empty external names after export products and import orders.

* 1.4.2 (2022-03-11)
    - Sale order line description for discount and price difference is assigned from product

* 1.4.1 (2022-03-01)
    - Fix issue with difference per cent of the total order amount.

* 1.4.0 (2022-02-17)
    - Added possibility to import product attributes and values by action “Import Products Attributes“ in menus “External → Product Attributes“ and “Mappings → Product Attributes“.
    - Added creation of Order Discount from e-Commerce System as a separate product line in a sell order.
    - Fix issue with trying to send stock to Prestashop for products that has disabled integration.
    - Fix bug of mapping modification for users without role Job Queue Manager.

* 1.3.8 (2022-01-05)
    - Added export of "Delivery time of in-stock products" and "Delivery time of out-of-stock products with allowed orders" fields.

* 1.3.7 (2021-12-31)
    - Added button "Import Stock Levels" to “Initial Import“ tab that tries to download stock levels for storable products.
    - Fixed bug of delivery line tax calculation.
    - Fixed multiple timezone bug in Prestashop.

* 1.3.6 (2021-12-24)
    - Added “Initial Import“ tab with two separate buttons into “Sale Integration“:
        - “Import Master Data“ - download and try to map common data.
        - “Import products“ - try to import products from e-Commerce System to Odoo (with pre-validation step).
    - Added possibility to import products by action Import Products in menu “External → Products“.
    - Import of products is run in jobs separately for each product.

* 1.3.5 (2021-11-22)
    - Downloaded sales order now is moved from file to JSON format and can be edited/viewed in menu “e-Commerce Integration → Sales Raw Data“.

* 1.3.4 (2021-10-27)
    - Synchronize tracking only after it is added to the stock picking. Some carrier connectors.

* 1.3.3 (2021-10-21)
    - Fix issue with Combinations not exporting properly attribute values.

* 1.3.2 (2021-10-19)
    - Fix issues with incorrect categories syncing.

* 1.3.1 (2021-10-18)
    - Added synchronization of partner language and partner email (to delivery and shipping address).

* 1.3 (2021-10-02)
    - Automapping of the Countries, Country States, Languages, Payment Methods.
    - Added Default Sales Team to Sales Order created via e-Commerce Integration.
    - Added synchronization of VAT and Personal Identification Number field.
    - In case purchase is done form the company, create Company and Contact inside Odoo.

* 1.2.1 (2021-09-21)
    - Fixed regression issue with initial creation of the product with combination not working properly.

* 1.2 (2021-09-20)
    - Added possibility to define field mappings and specify if field should be updatable or not.
    - Avoid creation of duplicated products under some conditions.

* 1.1 (2021-06-28)
    - Add field for Delivery Notes on Sales Order.
    - Added configuration to define on Sales Integration which fields should be used on SO and Delivery Order for Delivery Notes.
    - Allow to specify which product should be exported to which channel.
    - Add separate field that allows to specify Product Name to be sent to e-Commerce site instead of standard name.
    - Do not change Minimal Order Quantity on existing Combinations.

* 1.0.4 (2021-06-01)
    - Fix variants import if no variants exists.

* 1.0.3 (2021-05-28)
    - Replaced client request to new format (fixing payment and delivery methods retrieving).
    - Fixed warnings on Odoo.sh with empty description on new models.

* 1.0.2 (2021-04-21)
    - Fixed errors during import external models.
    - Fixed images export.

* 1.0.1 (2021-04-13)
    - Added PS_TIMEZONE settings field to correctly handle case when PrestaShop is in different timezone.
    - Added Check Connection support.

* 1.0 (2021-03-23)
    - Odoo integration with PrestaShop.

|
