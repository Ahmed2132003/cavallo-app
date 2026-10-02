from django.contrib import admin

from payments.models import Invoice, Subscription, Transaction


class _ReadOnlyPaymentAdmin(admin.ModelAdmin):
    """
    Payment records are written only by the system (initiation service,
    P-090 webhook, P-091 reconciliation). Staff can inspect them but can
    not create, edit or delete them by hand, so a payment status can never
    be forged from Admin.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(_ReadOnlyPaymentAdmin):
    list_display = ("id", "business", "plan", "status", "gateway_reference")
    list_filter = ("status",)
    search_fields = ("gateway_reference", "business__business_name")
    raw_id_fields = ("business", "plan")


@admin.register(Transaction)
class TransactionAdmin(_ReadOnlyPaymentAdmin):
    list_display = (
        "id",
        "subscription",
        "transaction_id",
        "amount",
        "currency",
        "status",
    )
    list_filter = ("status", "currency")
    search_fields = ("transaction_id",)
    raw_id_fields = ("subscription",)


@admin.register(Invoice)
class InvoiceAdmin(_ReadOnlyPaymentAdmin):
    list_display = ("id", "subscription", "transaction", "issued_at", "pdf_url")
    raw_id_fields = ("subscription", "transaction")
