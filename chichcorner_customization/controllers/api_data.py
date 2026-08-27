from odoo import http, api
from odoo.http import request
import json
from odoo.exceptions import AccessDenied
import werkzeug.exceptions
import requests
import werkzeug
from datetime import datetime, timedelta
import logging
import re
import base64

def validate_api_key(api_key):
    """Validate the API key and return the associated user if valid"""
    if not api_key:
        return None
    api_key_record = request.env['api.key'].sudo().search([
        ('key', '=', api_key),
        ('active', '=', True)
    ], limit=1)
    return api_key_record.user_id if api_key_record else None

#product_list_api -- id existe - deployé
class DimensionProduitAPI(http.Controller):

    @http.route("/api/dimension_product", auth='none', type='http', methods=['GET'], csrf=False)
    def get_dimension_produit(self, id_pdt_start=None, id_pdt_end=None, **kwargs):
        try:
            api_key = request.httprequest.headers.get('Authorization')

            user = validate_api_key(api_key)
            if not user:
                return http.Response(
                    json.dumps({"error": "Invalid or missing API key"}),
                    status=401,
                    content_type="application/json"
                )

            if not user.has_group('base.group_system'):
                return http.Response(
                    json.dumps({"error": "Access Denied", "details": "This API requires admin access"}),
                    status=403,
                    content_type="application/json"
                )

            request.update_env(user=user)

            domain = []
            if id_pdt_start and id_pdt_end:
                domain.append(('id', '>=', int(id_pdt_start)))
                domain.append(('id', '<=', int(id_pdt_end)))

            products = request.env['product.template'].sudo().search(domain)

            produit_data = []

            for product in products:
                pos_categories = [category.name for category in product.pos_categ_ids] if product.pos_categ_ids else None

                taxes = [tax.name for tax in product.taxes_id] if product.taxes_id else None

                supplier_info = [
                    {
                        "Nom du fournisseur": supplier.display_name,
                        "Prix": supplier.price,
                        "Devise": supplier.currency_id.name if supplier.currency_id else None
                    }
                    for supplier in product.seller_ids
                ]

                pricelist_item = request.env['product.pricelist.item'].sudo().search([
                    ('product_tmpl_id', '=', product.id),
                    ('pricelist_id.active', '=', True)
                ], limit=1)
                product_price = pricelist_item.fixed_price if pricelist_item else None

                stock_quantities = {}
                stock_records = request.env['stock.quant'].sudo().search([
                    ('product_id', '=', product.product_variant_id.id)
                ])
                for stock in stock_records:
                    location_name = stock.location_id.complete_name
                    stock_quantities[location_name] = stock.quantity

                category_path = []
                current_category = product.categ_id
                while current_category:
                    category_path.insert(0, current_category.name)
                    current_category = current_category.parent_id

                def get_cat_level(level):
                    return category_path[level - 1] if len(category_path) >= level else None

                id_produit_with_prefix = f"70001{product.id}" if product.id else "70001"
                produit_data.append({
                    "Id du produit": id_produit_with_prefix,
                    "Nom du Produit": product.name,
                    "Code barre": product.barcode,
                    "default code": product.default_code,
                    "Item ID": product.x_studio_item_id,
                    "Coût": product.standard_price,
                    "Prix de vente": product_price,
                    "HS Code": product.x_studio_hs_code,
                    "Pays Origine": product.x_studio_origine_pays,
                    "Magasin": get_cat_level(1),
                    "Gender": get_cat_level(2),
                    "Type d'article": get_cat_level(3),
                    "Sous-Marque": get_cat_level(4),
                    "Collection": get_cat_level(5),
                    "Catégorie d'article": get_cat_level(6),
                    "Composition": product.x_studio_composition,
                    "Type de produit": product.detailed_type,
                    "Politique de fabrication": product.invoice_policy,
                    "Stock selon l'emplacement": stock_quantities,
                    "Catégorie de produit": product.categ_id.name,
                    "Marque du produit": pos_categories,
                    "Disponible en POS": product.available_in_pos,
                    "Taxes": taxes,
                    "Informations fournisseur": supplier_info,
                })

            return request.make_json_response(produit_data, status=200)

        except werkzeug.exceptions.Unauthorized as e:
            return http.Response(
                json.dumps({"error": "Authentication Required", "details": str(e)}),
                status=401,
                content_type="application/json"
            )
        except werkzeug.exceptions.Forbidden as e:
            return http.Response(
                json.dumps({"error": "Access Denied", "details": str(e)}),
                status=403,
                content_type="application/json"
            )
        except Exception as e:
            error_message = f"Error fetching Dimension_produit: {str(e)}"
            request.env.cr.rollback()
            return http.Response(
                json.dumps({"error": "Internal Server Error", "details": error_message}),
                status=500,
                content_type="application/json"
            )
class PosSalesExportAPI(http.Controller):

    API_KEY = "5c8737045ad4abc7ed519a3932d1e5ce65c8ffd9"

    @http.route(
        "/api/pos_sales",
        auth="none",
        type="http",
        methods=["GET"],
        csrf=False
    )
    def get_pos_sales(self, startDate=None, endDate=None, **kwargs):

        try:
            api_key = request.httprequest.headers.get("Authorization")

            if api_key != self.API_KEY:
                return http.Response(
                    json.dumps({
                        "error": "Invalid or missing API key"
                    }),
                    status=401,
                    content_type="application/json"
                )

            sales_data = self._get_pos_sales(
                startDate,
                endDate
            )

            return request.make_json_response(
                sales_data,
                status=200
            )

        except Exception as e:

            error_message = (
                f"Error fetching POS sales: {str(e)}"
            )

            request.env.cr.rollback()

            return http.Response(
                json.dumps({
                    "error": "Internal Server Error",
                    "details": error_message
                }),
                status=500,
                content_type="application/json"
            )

    # =========================================================
    # EXTRACT PROMOTION PERCENTAGE
    # =========================================================

    def _extract_promotion_percentage(self, promotion_line):

        product_name = (
            promotion_line.product_id.display_name
            or promotion_line.name
            or ""
        )

        match = re.search(
            r'(\d+(?:[.,]\d+)?)\s*%',
            product_name
        )

        if not match:
            return None

        percentage = match.group(1)

        percentage = percentage.replace(",", ".")

        return float(percentage)

    # =========================================================
    # FIND PRODUCTS TO WHICH THE PROMOTION APPLIES
    # =========================================================

    def _find_discounted_lines(
        self,
        product_lines,
        promotion_percentage,
        promotion_amount
    ):

        """
        Find the combination of products for which:

            sum(product subtotal * promotion %) = promotion amount

        Example:

            Product 1 subtotal = 1600
            Product 2 subtotal = 2000
            Product 3 subtotal = 2450

            Promotion = 10%
            Promotion amount = 360

            1600 * 10% = 160
            2000 * 10% = 200

            160 + 200 = 360

        Therefore Product 1 + Product 2 receive the promotion.
        """

        if not product_lines:
            return []

        target = abs(promotion_amount)

        # -----------------------------------------------------
        # Calculate expected discount for each product
        # using price_subtotal_incl.
        # -----------------------------------------------------

        candidates = []

        for line in product_lines:

            subtotal = abs(line.price_subtotal_incl)

            expected_discount = (
                subtotal
                * promotion_percentage
                / 100
            )

            candidates.append({
                "line": line,
                "subtotal": subtotal,
                "discount": expected_discount,
            })

        # -----------------------------------------------------
        # Find a combination whose discount equals the
        # promotion amount.
        #
        # Example:
        #
        # 160 + 200 + 245 = ...
        #
        # We search for the combination that gives 360.
        # -----------------------------------------------------

        def find_combination(
            index,
            current_total,
            selected_lines
        ):

            # Small tolerance for decimal calculations
            if abs(current_total - target) < 0.01:
                return selected_lines

            if current_total > target + 0.01:
                return None

            if index >= len(candidates):
                return None

            # -------------------------------------------------
            # Try including current product
            # -------------------------------------------------

            candidate = candidates[index]

            result = find_combination(
                index + 1,
                current_total + candidate["discount"],
                selected_lines + [candidate["line"]]
            )

            if result is not None:
                return result

            # -------------------------------------------------
            # Try without current product
            # -------------------------------------------------

            result = find_combination(
                index + 1,
                current_total,
                selected_lines
            )

            return result

        result = find_combination(
            0,
            0,
            []
        )

        return result or []

    # =========================================================
    # GET POS SALES
    # =========================================================

    def _get_pos_sales(self, start_date, end_date):

        domain = []

        if start_date:
            domain.append(
                ("date_order", ">=", start_date)
            )

        if end_date:
            domain.append(
                ("date_order", "<=", end_date)
            )

        pos_orders = request.env[
            "pos.order"
        ].sudo().search(
            domain,
            order="date_order asc, id asc"
        )

        sales_data = []

        for order in pos_orders:

            store_code = (
                order.config_id.name
                if order.config_id
                else ""
            )

            currency = (
                order.currency_id.name
                if order.currency_id
                else ""
            )

            # =================================================
            # SEPARATE PRODUCT AND PROMOTION LINES
            # =================================================

            product_lines = []
            promotion_lines = []

            for line in order.lines:

                # Negative price = promotion line
                if line.price_unit < 0:
                    promotion_lines.append(line)

                else:
                    product_lines.append(line)

            # =================================================
            # FIND DISCOUNTED PRODUCTS
            # =================================================

            discounted_lines = {}

            # We process each promotion line
            for promotion_line in promotion_lines:

                promotion_percentage = (
                    self._extract_promotion_percentage(
                        promotion_line
                    )
                )

                if promotion_percentage is None:
                    continue

                promotion_amount = abs(
                    promotion_line.price_subtotal_incl
                    if promotion_line.price_subtotal_incl
                    else promotion_line.price_unit
                )

                # -------------------------------------------------
                # Find products whose subtotal percentages match
                # the promotion amount.
                # -------------------------------------------------

                matching_lines = self._find_discounted_lines(
                    product_lines,
                    promotion_percentage,
                    promotion_amount
                )

                for line in matching_lines:

                    discounted_lines[line.id] = (
                        promotion_percentage
                    )

            # =================================================
            # EXPORT PRODUCT LINES
            # =================================================

            item_no = 0

            for line in product_lines:

                item_no += 1

                tax_rate = 0

                if line.tax_ids:

                    tax_rate = int(
                        round(
                            line.tax_ids[0].amount
                        )
                    )

                qty = line.qty

                # =================================================
                # ORIGINAL SUBTOTAL INCLUDING QUANTITY
                # =================================================

                sales_amount = abs(
                    line.price_subtotal_incl
                )

                # =================================================
                # APPLY PROMOTION
                # =================================================

                if line.id in discounted_lines:

                    promotion_percentage = (
                        discounted_lines[line.id]
                    )

                    # IMPORTANT:
                    #
                    # We calculate the discount from
                    # price_subtotal_incl, NOT price_unit.
                    #
                    # Example:
                    #
                    # qty = 2
                    # subtotal = 1600
                    # promo = 10%
                    #
                    # discount = 1600 * 10% = 160
                    #
                    # final = 1600 - 160 = 1440

                    discount_value = (
                        sales_amount
                        * promotion_percentage
                        / 100
                    )

                    sales_amount = (
                        sales_amount
                        - discount_value
                    )

                    if sales_amount < 0:
                        sales_amount = 0

                # =================================================
                # TRANSACTION TYPE
                # =================================================

                transaction_type = (
                    "Refund"
                    if qty < 0
                    else "Sale"
                )

                # =================================================
                # ADD RESULT
                # =================================================

                sales_data.append({

                    "StoreCode": store_code,

                    "CreatedDate": (
                        order.date_order.strftime(
                            "%Y-%m-%dT%H:%M:%S"
                        )
                        if order.date_order
                        else None
                    ),

                    "LastUpdatedDate": (
                        order.write_date.strftime(
                            "%Y-%m-%dT%H:%M:%S"
                        )
                        if order.write_date
                        else None
                    ),

                    "InvoiceNo": (
                        order.pos_reference
                        or order.name
                    ),

                    "InvoiceItemNo": item_no,

                    "TransactionType": transaction_type,

                    "Barcode": (
                        line.product_id.barcode
                        or ""
                    ),

                    "SalesAmount": sales_amount,

                    "Currency": currency,

                    "SalesQuantity": abs(qty),

                    "TaxRate": tax_rate,

                    "InitialSalePrice": line.price_unit,
                })

            # =================================================
            # PROMOTION LINES ARE NOT EXPORTED
            # =================================================

        return sales_data

class StockDataAPI(http.Controller):

    API_KEY = "5c8737045ad4abc7ed519a3932d1e5ce65c8ffd9"

    @http.route(
        "/api/stock_data",
        auth="none",
        type="http",
        methods=["GET"],
        csrf=False
    )
    def get_stock_data(
        self,
        startDate=None,
        endDate=None,
        **kwargs
    ):

        try:

            # =====================================================
            # AUTHENTICATION
            # =====================================================

            api_key = request.httprequest.headers.get(
                "Authorization"
            )

            if api_key != self.API_KEY:

                return http.Response(
                    json.dumps({
                        "error": "Invalid or missing API key"
                    }),
                    status=401,
                    content_type="application/json"
                )

            # =====================================================
            # VALIDATE DATES
            # =====================================================

            if not startDate or not endDate:

                return http.Response(
                    json.dumps({
                        "error": "startDate and endDate are required"
                    }),
                    status=400,
                    content_type="application/json"
                )

            try:

                start_date = datetime.strptime(
                    startDate,
                    "%Y-%m-%d"
                ).date()

                end_date = datetime.strptime(
                    endDate,
                    "%Y-%m-%d"
                ).date()

            except ValueError:

                return http.Response(
                    json.dumps({
                        "error": (
                            "Invalid date format. "
                            "Use YYYY-MM-DD"
                        )
                    }),
                    status=400,
                    content_type="application/json"
                )

            if start_date > end_date:

                return http.Response(
                    json.dumps({
                        "error": (
                            "startDate cannot be greater "
                            "than endDate"
                        )
                    }),
                    status=400,
                    content_type="application/json"
                )

            # =====================================================
            # GET STOCK
            # =====================================================

            stock_data = self._get_stock_data(
                start_date,
                end_date
            )

            return request.make_json_response(
                stock_data,
                status=200
            )

        except Exception as e:

            request.env.cr.rollback()

            return http.Response(
                json.dumps({
                    "error": "Internal Server Error",
                    "details": str(e)
                }),
                status=500,
                content_type="application/json"
            )

    # =============================================================
    # GET STOCK DATA (single pass over the whole date range)
    # =============================================================
    #
    # Instead of recomputing "current quants" and re-scanning
    # "all future moves" once PER DAY (which is what made this
    # timeout with 9571+ products), we now:
    #
    #   1. Load quants ONCE.
    #   2. Load all relevant move lines ONCE, sorted newest first.
    #   3. Walk backward day by day, only ever touching each move
    #      line exactly once, applying its reversal as we cross
    #      its date going backward in time.
    #   4. Batch-fetch product/location display data ONCE instead
    #      of browsing one record at a time in a loop.
    #
    # This turns the cost from O(days x (quants + moves)) into
    # O(quants + moves), regardless of how many days are requested.
    # =============================================================

    def _get_stock_data(
        self,
        start_date,
        end_date
    ):

        StockQuant = request.env["stock.quant"].sudo()
        StockMoveLine = request.env["stock.move.line"].sudo()
        Product = request.env["product.product"].sudo()
        Location = request.env["stock.location"].sudo()

        # =====================================================
        # 1) CURRENT STOCK — fetched ONCE
        # =====================================================

        quants = StockQuant.search([
            ("location_id.usage", "=", "internal")
        ])

        stock_by_key = {}

        for quant in quants:

            key = (
                quant.product_id.id,
                quant.location_id.id
            )

            stock_by_key[key] = (
                stock_by_key.get(key, 0)
                + quant.quantity
            )

        # =====================================================
        # 2) ALL RELEVANT MOVE LINES — fetched ONCE,
        #    newest first, so we can consume them
        #    incrementally while walking backward.
        # =====================================================

        earliest_cutoff = datetime.combine(
            start_date,
            datetime.max.time()
        )

        future_moves = StockMoveLine.search([
            ("state", "=", "done"),
            ("date", ">", earliest_cutoff.strftime(
                "%Y-%m-%d %H:%M:%S"
            ))
        ], order="date desc")

        moves_list = list(future_moves)

        # =====================================================
        # 3) BATCH-PREFETCH product / location display data
        #    (avoids one-by-one browse() calls in a loop)
        # =====================================================

        product_ids = set(quants.mapped("product_id").ids)
        location_ids = set(quants.mapped("location_id").ids)

        for move_line in moves_list:
            product_ids.add(move_line.product_id.id)
            location_ids.add(move_line.location_id.id)
            location_ids.add(move_line.location_dest_id.id)

        products = Product.browse(list(product_ids))
        locations = Location.browse(list(location_ids))

        products_data = {
            p.id: (p.barcode or "", p.display_name)
            for p in products
        }

        locations_name = {
            l.id: (l.complete_name or l.name)
            for l in locations
        }

        locations_usage = {
            l.id: l.usage
            for l in locations
        }

        # =====================================================
        # 4) WALK BACKWARD DAY BY DAY, applying only the
        #    slice of moves that falls after each day's
        #    cutoff, consuming moves_list exactly once total.
        # =====================================================

        result = []

        current_date = end_date
        move_idx = 0
        total_moves = len(moves_list)

        while current_date >= start_date:

            date_end = datetime.combine(
                current_date,
                datetime.max.time()
            )

            while (
                move_idx < total_moves
                and moves_list[move_idx].date > date_end
            ):

                move_line = moves_list[move_idx]

                product_id = move_line.product_id.id
                quantity = move_line.quantity
                source_id = move_line.location_id.id
                dest_id = move_line.location_dest_id.id

                # SOURCE: a future movement decreased it,
                # so we ADD it back when going backward.
                if locations_usage.get(source_id) == "internal":

                    key = (product_id, source_id)

                    stock_by_key[key] = (
                        stock_by_key.get(key, 0)
                        + quantity
                    )

                # DESTINATION: a future movement increased it,
                # so we REMOVE it when going backward.
                if locations_usage.get(dest_id) == "internal":

                    key = (product_id, dest_id)

                    stock_by_key[key] = (
                        stock_by_key.get(key, 0)
                        - quantity
                    )

                move_idx += 1

            # ---------------------------------------------------
            # Snapshot for this day
            # ---------------------------------------------------

            date_str = current_date.strftime("%Y-%m-%d")

            for (product_id, location_id), quantity in stock_by_key.items():

                if abs(quantity) < 0.0001:
                    continue

                barcode, product_name = products_data.get(
                    product_id, ("", "")
                )

                result.append({
                    "Date": date_str,
                    "StoreCode": locations_name.get(location_id, ""),
                    "Barcode": barcode,
                    "ProductID": product_id,
                    "ProductName": product_name,
                    "Quantity": quantity,
                })

            current_date -= timedelta(days=1)

        return result
#sales_api -- id existe - deployé
class PosOrderAPI(http.Controller):

    @http.route("/api/pos_ventes", auth='none', type='http', methods=['GET'], csrf=False)
    def get_pos_orders(self,id_produit=None, id_magasin=None, id_client=None, id_debut=None, id_fin=None, **kwargs):
        try:
            api_key = request.httprequest.headers.get('Authorization')
            user = validate_api_key(api_key)
            if not user:
                return http.Response(
                    json.dumps({"error": "Invalid or missing API key"}),
                    status=401,
                    content_type="application/json"
                )

            request.update_env(user=user)

            domain = []

            if id_magasin:
                domain.append(('config_id', '=', int(id_magasin)))

            if id_client:
                domain.append(('partner_id', '=', int(id_client)))

            if id_debut and id_fin:
                domain.append(('date_order', '>=', id_debut))
                domain.append(('date_order', '<=', id_fin))

            pos_orders = request.env['pos.order'].sudo().search(domain)

            pos_data = []
            for order in pos_orders:
                order_lines = []
                for line in order.lines:
                    if id_produit and line.product_id.id != int(id_produit):
                        continue
                    id_produit_with_prefix = f"70001{line.product_id.id}"
                    order_lines.append({
                        "id_produit": id_produit_with_prefix,
                        "Nom": line.product_id.display_name,
                        "Quantité": line.qty,
                        "Note du client": line.customer_note or "",
                        "discount": line.discount,
                        "Prix": line.price_subtotal_incl,
                    })

                if not order_lines:
                    continue
                id_ticket_with_prefix = f"70001{order.id}"
                ticket_caisse_with_prefix = f"70001 {order.pos_reference}" if order.pos_reference else "70001"
                id_magasin_with_prefix = 5 + order.config_id.id
                pos_data.append({
                    "Ref": order.name,
                    "Session": order.session_id.id if order.session_id else "None",
                    "Date de commande": order.date_order,
                    "Id Magasin": id_magasin_with_prefix,
                    "Nom du Magasin": order.config_id.name if order.config_id else "None",# ID du magasin
                    "Id Ticket": id_ticket_with_prefix,
                    "Ticket de caisse": ticket_caisse_with_prefix,
                    "Id Client": order.partner_id.id if order.partner_id else None,  # ID du client
                    "Nom du client": order.partner_id.name if order.partner_id else "",
                    "Caissier": order.employee_id.name if order.employee_id else "",
                    "Nom du vendeur": order.suggestion if order.suggestion else "",
                    #"amount_total": order.amount_total,
                    "Produits achetés": order_lines,
                })

            return request.make_json_response(pos_data, status=200)

        except Exception as e:
            error_message = f"Error fetching POS orders: {str(e)}"
            request.env.cr.rollback()
            return http.Response(
                json.dumps({"error": "Internal Server Error", "details": error_message}),
                status=500,
                content_type="application/json"
            )

#payment_methedo_api -- id existe - deployé
class PosPaymentAPI(http.Controller):

    @http.route("/api/pos_payments", auth='none', type='http', methods=['GET'], csrf=False)
    def get_pos_payments(self,id_order_start=None,id_order_end=None, id_magasin=None, id_client=None, id_produit=None, id_debut=None, id_fin=None, **kwargs):
        try:
            api_key = request.httprequest.headers.get('Authorization')
            user = validate_api_key(api_key)
            if not user:
                return http.Response(
                    json.dumps({"error": "Invalid or missing API key"}),
                    status=401,
                    content_type="application/json"
                )

            request.update_env(user=user)

            domain = []

            if id_order_start and id_order_end:
                domain.append(('id', '>=', id_order_start))
                domain.append(('id', '<=', id_order_end))

            if id_magasin:
                domain.append(('session_id.config_id', '=', int(id_magasin)))

            if id_client:
                domain.append(('partner_id', '=', int(id_client)))

            if id_debut and id_fin:
                domain.append(('date_order', '>=', id_debut))
                domain.append(('date_order', '<=', id_fin))

            if id_produit:
                domain.append(('lines.product_id', '=', int(id_produit)))

            pos_orders = request.env['pos.order'].sudo().search(domain)
            payment_data = []
            for order in pos_orders:
                payment_methods = {}

                for payment in order.payment_ids:
                    method_name = payment.payment_method_id.name
                    # Add to existing amount if method already exists, otherwise create new entry
                    if method_name in payment_methods:
                        payment_methods[method_name] += payment.amount
                    else:
                        payment_methods[method_name] = payment.amount

                id_ticket_with_prefix = f"70001{order.id}" if order.id else "70001"
                id_ticket_caisse_with_prefix = f"70001 {order.pos_reference}" if order.pos_reference else "70001"
                payment_data.append({
                    "Nom du Magasin": order.config_id.name if order.config_id else "None",
                    "Session": order.session_id.name if order.session_id else "None",
                    "Id Ticket": id_ticket_with_prefix,
                    "Ticket de caisse": id_ticket_caisse_with_prefix,
                    "Caissier": order.employee_id.name if order.employee_id else "None",
                    "Date de commande": order.date_order,
                    "Vendeur": order.suggestion if order.suggestion else "",
                    #"id_client": order.partner_id.id if order.partner_id else None,
                    "Nom du Client": order.partner_id.name if order.partner_id else "None",
                    "Méthodes de paiement": payment_methods
                })

            return request.make_json_response(payment_data, status=200)

        except Exception as e:
            error_message = f"Error fetching POS payments: {str(e)}"
            request.env.cr.rollback()
            return http.Response(
                json.dumps({"error": "Internal Server Error", "details": error_message}),
                status=500,
                content_type="application/json"
            )

#achat -- vente delete order_id - deployé
class PurchaseOrderAPI(http.Controller):
    @http.route("/api/purchase_orders", auth='none', type='http', methods=['GET'], csrf=False)
    def get_purchase_orders(self,id_fournisseur=None, id_user=None, id_debut=None, id_fin=None, **kwargs):
        try:
            api_key = request.httprequest.headers.get('Authorization')
            user = validate_api_key(api_key)
            if not user:
                return http.Response(
                    json.dumps({"error": "Invalid or missing API key"}),
                    status=401,
                    content_type="application/json"
                )

            request.update_env(user=user)

            domain = []

            if id_fournisseur:
                domain.append(('partner_id', '=', int(id_fournisseur)))

            if id_user:
                domain.append(('user_id', '=', int(id_user)))

            if id_debut and id_fin:
                domain.append(('date_approve', '>=', id_debut))
                domain.append(('date_approve', '<=', id_fin))

            domain.append(('state', 'not in', ['cancel', 'done']))
            purchase_orders = request.env['purchase.order'].sudo().search(domain)

            purchase_data = []
            for po in purchase_orders:
                total_qty = sum(po.order_line.mapped('product_qty'))
                total_received = sum(po.order_line.mapped('qty_received'))
                warehouse = po.picking_type_id.warehouse_id if po.picking_type_id else None
                currency = po.currency_id.name
                supplier_name = po.partner_id.name if po.partner_id else ""

                # Add "MA - " only if it's EUR
                if currency == "MAD" and not supplier_name.startswith("MA - "):
                    supplier_name = "MA - " + supplier_name

                # Manual exchange rate conversion
                if po.currency_id.name == 'MAD':
                    converted_total = po.amount_total
                elif po.currency_id.name == 'EUR':
                    converted_total = po.amount_total * 11.200000000
                elif po.currency_id.name == 'USD':
                    converted_total = po.amount_total * 9.7500000000
                else:
                    converted_total = po.amount_total  # fallback if unknown currency

                id_magasin_with_prefix = 5 + warehouse.id
                purchase_data.append({
                    "Bon De Commande": po.name,
                    "Date de confirmation": po.date_approve,
                    "Fournisseur": supplier_name,
                    "Livrer à" : po.picking_type_id.name if po.picking_type_id else "",
                    "Id du Magasin": id_magasin_with_prefix,
                    "Nom du Magasin": warehouse.name if warehouse else "",
                    #"partner_name": po.partner_id.name if po.partner_id else None,
                    #"user_id": po.user_id.id if po.user_id else None,
                    #"Utilisateur": po.user_id.name if po.user_id else None,
                    "Document D'origine": po.origin if po.origin else "",
                    "DD Impot": po.x_studio_dd_impot if po.x_studio_dd_impot else "",
                    "Num Fact Fournisseur": po.x_studio_num_fact_frs if po.x_studio_num_fact_frs else "",
                    "Total": converted_total,
                    "Quantité commandée": total_qty,
                    "Quantité reçue": total_received
                })

            return request.make_json_response(purchase_data, status=200)

        except Exception as e:
            error_message = f"Error fetching purchase orders: {str(e)}"
            request.env.cr.rollback()
            return http.Response(
                json.dumps({"error": "Internal Server Error", "details": error_message}),
                status=500,
                content_type="application/json"
            )

# stock : - deployé
class InventoryAPI(http.Controller):

    @http.route("/api/inventory", auth='none', type='http', methods=['GET'], csrf=False)
    def get_inventory(self,id_debut=None, id_fin=None, **kwargs):
        try:
            api_key = request.httprequest.headers.get('Authorization')
            user = validate_api_key(api_key)
            if not user:
                return http.Response(
                    json.dumps({"error": "Invalid or missing API key"}),
                    status=401,
                    content_type="application/json"
                )

            request.update_env(user=user)

            domain = []
            if id_debut and id_fin:
                domain.append(('in_date', '>=', id_debut))
                domain.append(('in_date', '<=', id_fin))

            inventory_lines = request.env['stock.quant'].sudo().search(domain)

            inventory_data = []
            for line in inventory_lines:
                product = line.product_id
                category_path = []
                category = product.categ_id
                while category:
                    category_path.insert(0, category.name)  # Ajouter au début pour avoir le bon ordre
                    category = category.parent_id

                pricelist_item = request.env['product.pricelist.item'].sudo().search([
                    ('product_tmpl_id', '=', product.product_tmpl_id.id),
                    ('pricelist_id.active', '=', True)
                ], limit=1)

                product_price = pricelist_item.fixed_price if pricelist_item else None
                pos_categories = [category.name for category in product.pos_categ_ids] if product.pos_categ_ids else []
                id_produit_with_prefix = f"70001{product.id}" if product.id else "70001"
                id_magasin_with_prefix = 5 + line.warehouse_id.id if line.warehouse_id.id else "Aucun"

                inventory_data.append({
                    "L'emplacement": line.location_id.complete_name if line.location_id else "",
                    "ID du Produit": id_produit_with_prefix,
                    "Nom du produit": product.display_name if product else "",
                    "Id Magasin": id_magasin_with_prefix,
                    "Nom du Magasin": line.warehouse_id.name if line.warehouse_id.name else "Aucun",
                    "Categorie POS": ", ".join(pos_categories) if pos_categories else "Aucune",
                    "Categorie": " / ".join(category_path) if category_path else "Non classé",
                    "Quantité en stock": line.inventory_quantity_auto_apply,
                    "Valeur en MAD": line.value,
                    "Item ID": product.x_studio_item_id if hasattr(product, 'x_studio_item_id') else None,
                    "Prix de vente": product_price
                })

            return request.make_json_response(inventory_data, status=200)

        except Exception as e:
            error_message = f"Error fetching inventory data: {str(e)}"
            request.env.cr.rollback()
            return http.Response(
                json.dumps({"error": "Internal Server Error", "details": error_message}),
                status=500,
                content_type="application/json"
            )

#stock valorisation - deployé
class StockValuationAPI(http.Controller):

    @http.route("/api/stock_valuation", auth='none', type='http', methods=['GET'], csrf=False)
    def get_stock_valuation(self,id_val_start=None, id_debut=None, id_fin=None, id_val_end=None, **kwargs):
        try:
            api_key = request.httprequest.headers.get('Authorization')
            user = validate_api_key(api_key)
            if not user:
                return http.Response(
                    json.dumps({"error": "Invalid or missing API key"}),
                    status=401,
                    content_type="application/json"
                )

            request.update_env(user=user)

            domain = []

            if id_debut and id_fin:
                domain.append(('create_date', '>=', id_debut))
                domain.append(('create_date', '<=', id_fin))

            if id_val_start and id_val_end:
                domain.append(('id', '>=', id_val_start))
                domain.append(('id', '<=', id_val_end))

            valuation_records = request.env['stock.valuation.layer'].sudo().search(domain)

            valuation_data = []
            total_value = 0

            for record in valuation_records:
                product = record.product_id
                pos_category_names = [cat.name for cat in product.pos_categ_ids] if product.pos_categ_ids else [
                    "Aucune", ]

                id_produit_with_prefix = f"70001{record.product_id.id}" if record.product_id.id else "70001"
                valuation_data.append({
                    "Date de création": record.create_date,
                    "Référence": record.reference,
                    "ID Du Produit": id_produit_with_prefix,
                    "Nom Produit": record.product_id.display_name if record.product_id else "",
                    "Magasin": " / ".join(pos_category_names),
                    "Quantité": record.quantity,
                    "Quantité restante": record.remaining_qty,
                    "Valeur en MAD": record.value,
                    "Valeur restante en MAD": record.remaining_value,
                })
                total_value += record.value


            valuation_data.append({
                "Total Valeur en MAD": total_value
            })

            return request.make_json_response(valuation_data, status=200)

        except Exception as e:
            error_message = f"Error fetching stock valuation data: {str(e)}"
            request.env.cr.rollback()
            return http.Response(
                json.dumps({"error": "Internal Server Error", "details": error_message}),
                status=500,
                content_type="application/json"
            )
