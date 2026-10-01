import re
import logging
from datetime import datetime

from odoo import models, api, fields

_logger = logging.getLogger(__name__)


class StockExportCron(models.AbstractModel):
    _name = "stock.export.cron"
    _description = "Export quotidien du stock vers x_api_stock"

    # =========================================================
    # EXTRACT STORE CODE
    # =========================================================

    def _get_store_code(self, location):
        if not location:
            return ""

        location_name = location.complete_name or location.name or ""
        match = re.match(r"^\s*(\d+)", location_name)
        if match:
            return match.group(1)

        return ""

    # =========================================================
    # VALIDATE BARCODE
    # =========================================================

    def _get_valid_barcode(self, product):
        if not product:
            return None

        barcode = (product.barcode or "").strip()

        if not re.fullmatch(r"86\d{11}", barcode):
            return None

        return barcode

    # =========================================================
    # CRON ENTRY POINT — traite le stock du jour (exécuté à 23h)
    # =========================================================

    @api.model
    def run_daily_export(self):
        now = fields.Datetime.now()
        stock_date = now.date()

        _logger.info(
            "[stock_export] Cron déclenché à %s — reconstruction du stock pour le %s",
            now, stock_date
        )

        self._export_stock_for_date(stock_date)

    def _export_stock_for_date(self, stock_date):
        StockQuant = self.env["stock.quant"].sudo()
        StockMoveLine = self.env["stock.move.line"].sudo()
        Product = self.env["product.product"].sudo()
        ApiStock = self.env["x_api_stock"].sudo()

        date_end = datetime.combine(stock_date, datetime.max.time())

        # =====================================================
        # CURRENT STOCK
        # =====================================================

        quants = StockQuant.search([
            ("location_id.usage", "=", "internal")
        ])

        stock_by_product_store = {}

        for quant in quants:
            product = quant.product_id
            location = quant.location_id

            store_code = self._get_store_code(location)
            if not store_code:
                continue

            barcode = self._get_valid_barcode(product)
            if not barcode:
                continue

            key = (product.id, store_code)
            stock_by_product_store[key] = (
                stock_by_product_store.get(key, 0) + quant.quantity
            )

        # =====================================================
        # REVERSE STOCK MOVEMENTS AFTER REQUESTED DATE
        # =====================================================

        future_moves = StockMoveLine.search([
            ("state", "=", "done"),
            ("date", ">", date_end.strftime("%Y-%m-%d %H:%M:%S")),
        ])

        for move_line in future_moves:
            product = move_line.product_id

            barcode = self._get_valid_barcode(product)
            if not barcode:
                continue

            quantity = move_line.quantity

            source_location = move_line.location_id
            destination_location = move_line.location_dest_id

            if source_location.usage == "internal":
                store_code = self._get_store_code(source_location)
                if store_code:
                    key = (product.id, store_code)
                    stock_by_product_store[key] = (
                        stock_by_product_store.get(key, 0) + quantity
                    )

            if destination_location.usage == "internal":
                store_code = self._get_store_code(destination_location)
                if store_code:
                    key = (product.id, store_code)
                    stock_by_product_store[key] = (
                        stock_by_product_store.get(key, 0) - quantity
                    )

        # =====================================================
        # WRITE TO x_api_stock
        # =====================================================

        date_str = stock_date.strftime("%Y-%m-%d")

        created_count = 0
        skipped_duplicate = 0
        skipped_zero = 0
        skipped_barcode = 0

        for (product_id, store_code), quantity in stock_by_product_store.items():

            if quantity <= 0:
                skipped_zero += 1
                continue

            product = Product.browse(product_id)
            if not product.exists():
                continue

            barcode = self._get_valid_barcode(product)
            if not barcode:
                skipped_barcode += 1
                continue

            if float(quantity).is_integer():
                quantity = int(quantity)
            else:
                quantity = float(quantity)

            existing = ApiStock.search([
                ("x_studio_date", "=", date_str),
                ("x_studio_storecode", "=", store_code),
                ("x_studio_barcode", "=", barcode),
            ], limit=1)

            if existing:
                skipped_duplicate += 1
                continue

            ApiStock.create({
                "x_name": f"{date_str}-{store_code}-{barcode}",
                "x_studio_date": date_str,
                "x_studio_storecode": store_code,
                "x_studio_warehousetype": "Internal",
                "x_studio_barcode": barcode,
                "x_studio_quantity": quantity,
            })
            created_count += 1

        _logger.info(
            "[stock_export] Terminé pour le %s — %s ligne(s) créée(s), "
            "%s doublon(s) ignoré(s), %s ligne(s) stock<=0 ignorée(s), "
            "%s ligne(s) sans barcode valide",
            date_str, created_count, skipped_duplicate, skipped_zero, skipped_barcode
        )