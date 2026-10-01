import re
import logging
from datetime import timedelta

from odoo import models, api, fields,_

_logger = logging.getLogger(__name__)


class PosSalesExportCron(models.AbstractModel):
    _name = "pos.sales.export.cron"
    _description = "Export quotidien des ventes POS vers x_api_sales"

    def _get_store_code(self, config):
        if not config:
            return ""

        config_name = config.name or ""
        match = re.match(r"^\s*(\d+)", config_name)
        if match:
            return match.group(1)

        return ""

    def _get_valid_barcode(self, product):
        if not product:
            return None

        barcode = (product.barcode or "").strip()

        if not re.fullmatch(r"86\d{11}", barcode):
            return None

        return barcode

    @api.model
    def run_daily_export(self):
        now = fields.Datetime.now()
        start_dt = now - timedelta(days=1)
        end_dt = now

        _logger.info(
            "[pos_sales_export] Cron déclenché à %s — récupération des commandes entre %s et %s",
            now,
            start_dt,
            end_dt
        )

        self._export_pos_sales(start_dt, end_dt)

    def _export_pos_sales(self, start_dt, end_dt):
        domain = [
            ("date_order", ">=", start_dt),
            ("date_order", "<", end_dt),
        ]

        pos_orders = self.env["pos.order"].sudo().search(
            domain,
            order="date_order asc, id asc"
        )

        _logger.info(
            "[pos_sales_export] %s commande(s) trouvée(s) dans la fenêtre %s -> %s",
            len(pos_orders),
            start_dt,
            end_dt
        )

        ApiSales = self.env["x_api_sales"].sudo()

        created_count = 0
        skipped_duplicate = 0
        skipped_barcode = 0
        skipped_promo = 0

        for order in pos_orders:

            store_code = self._get_store_code(order.config_id)
            currency = order.currency_id.name if order.currency_id else ""

            item_no = 0

            for line in order.lines:

                # Ignorer les lignes promotionnelles
                if line.price_unit < 0:
                    skipped_promo += 1
                    continue

                # Vérifier le barcode
                barcode = self._get_valid_barcode(line.product_id)

                if not barcode:
                    skipped_barcode += 1
                    continue

                item_no += 1

                invoice_no = order.pos_reference or order.name

                # Vérifier si la ligne existe déjà
                existing = ApiSales.search([
                    ("x_studio_invoiceno", "=", invoice_no),
                    ("x_studio_invoiceitemno", "=", item_no),
                ], limit=1)

                if existing:
                    skipped_duplicate += 1
                    continue

                # Taux de taxe
                tax_rate = 0

                if line.tax_ids:
                    tax_rate = int(round(line.tax_ids[0].amount))

                qty = line.qty
                price_unit = line.price_unit

                # Calcul du prix HT
                if line.tax_ids:
                    taxes_result = line.tax_ids.compute_all(
                        price_unit,
                        currency=order.currency_id,
                        quantity=1,
                        product=line.product_id,
                        partner=order.partner_id,
                    )

                    price_unit_excl_tax = taxes_result["total_excluded"]
                else:
                    price_unit_excl_tax = price_unit

                # Montant total
                sales_amount = price_unit_excl_tax * abs(qty)

                # Prix initial total
                initial_sale_price = price_unit_excl_tax * abs(qty)

                # Type de transaction
                transaction_type = "Refund" if qty < 0 else "Sale"

                # Datetime fields
                # x_studio_createddate_1 et x_studio_lastupdateddate_1
                # sont maintenant des champs Datetime
                created_date = order.date_order
                updated_date = order.write_date

                ApiSales.create({
                    "x_name": f"{invoice_no}-{item_no}",

                    "x_studio_storecode": store_code,

                    "x_studio_createddate_1": created_date,
                    "x_studio_lastupdateddate_1": updated_date,

                    "x_studio_invoiceno": invoice_no,
                    "x_studio_invoiceitemno": item_no,

                    "x_studio_transactiontype": transaction_type,
                    "x_studio_barcode": barcode,

                    "x_studio_salesamount": sales_amount,
                    "x_studio_currency": currency,
                    "x_studio_salesquantity": abs(qty),
                    "x_studio_taxrate": tax_rate,
                    "x_studio_initialsaleprice": initial_sale_price,
                })

                created_count += 1

        _logger.info(
            "[pos_sales_export] Terminé — %s ligne(s) créée(s), "
            "%s doublon(s) ignoré(s), "
            "%s ligne(s) sans barcode valide, "
            "%s ligne(s) promo ignorée(s)",
            created_count,
            skipped_duplicate,
            skipped_barcode,
            skipped_promo
        )
