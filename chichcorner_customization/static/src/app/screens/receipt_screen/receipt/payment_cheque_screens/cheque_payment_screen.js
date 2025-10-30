      /** @odoo-module **/
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ChequePaymentFormPopup } from "./cheque_payment";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup();
        this.popup = useService("popup");
        this.notification = useService("notification");
    },
    async addNewPaymentLine(paymentMethod) {
    const result = await super.addNewPaymentLine(paymentMethod);
    if (result && paymentMethod.name === "Chèque") {
        const popupResult = await this.popup.add(ChequePaymentFormPopup, {
            title: _t("Please Enter Cheque Payment Details!"),
        });
        if (popupResult && popupResult.confirmed !== undefined) {
            const { confirmed, payload } = popupResult;
            if (!confirmed) {
                const paymentLineCid = this.currentOrder.selected_paymentline.cid;
                this.deletePaymentLine(paymentLineCid);
                this.notification.add(_t("Cheque payment line removed!"), {
                    type: "danger",
                });
            }
        } else {
            console.error("Popup result is invalid or missing 'confirmed' property");
        }
    }
    return result;
}
});
