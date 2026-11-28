"""
Part BI-01 — recording ConversationProductInterest rows.

Kept in its own module (not in chat/views.py) so the two call sites
(ConversationStartView and MessageSendView) share one implementation and
so it can be unit-tested without going through HTTP.
"""

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from chat.models import ConversationParticipant, ConversationProductInterest
from products.models import Product


def record_product_interest(*, conversation, customer, product, source, touch=True):
    """
    Create the (conversation, product) interest row if it does not exist.

    touch=True  -> an existing row gets last_opened_at/updated_at moved to
                   now (used when the customer opens the conversation again
                   from the product screen).
    touch=False -> an existing row is left untouched (used for "shared a
                   product into an existing conversation": the spec says
                   record it only if it is not recorded yet).

    Never creates a duplicate: UniqueConstraint + get_or_create (which
    itself handles the concurrent-insert IntegrityError by re-reading).
    Returns (interest, created).
    """
    now = timezone.now()
    with transaction.atomic():
        interest, created = ConversationProductInterest.objects.get_or_create(
            conversation=conversation,
            product=product,
            defaults={
                "business_id": product.business_id,
                "customer": customer,
                "source": source,
                "first_opened_at": now,
                "last_opened_at": now,
            },
        )
        if not created and touch:
            ConversationProductInterest.objects.filter(pk=interest.pk).update(
                last_opened_at=now, updated_at=now
            )
            interest.last_opened_at = now
            interest.updated_at = now
    return interest, created


def record_shared_product_interest(*, message, sender):
    """
    Called after a Message is saved. If the message shares a Product and
    the sender is the CUSTOMER side of that product (i.e. the owner of the
    product's business is the OTHER participant of the conversation),
    record the interest — only if it is not recorded yet.

    Returns the interest row, or None when nothing applies:
      - the message shares nothing / shares a Post or Reel,
      - the product is soft-deleted or its business is soft-deleted,
      - the sender is the business owner sharing their own product,
      - the product belongs to a business that is not in this conversation.
    """
    if message.shared_content_type_id is None or message.shared_object_id is None:
        return None
    product_ct = ContentType.objects.get_for_model(Product)
    if message.shared_content_type_id != product_ct.id:
        return None

    product = (
        Product.objects.select_related("business")
        .filter(pk=message.shared_object_id)
        .first()
    )
    if product is None or product.business.is_deleted:
        return None

    other_user_id = (
        ConversationParticipant.objects.filter(conversation_id=message.conversation_id)
        .exclude(user_id=sender.id)
        .values_list("user_id", flat=True)
        .first()
    )
    if other_user_id is None or product.business.user_id != other_user_id:
        return None

    interest, _created = record_product_interest(
        conversation=message.conversation,
        customer=sender,
        product=product,
        source=ConversationProductInterest.Source.SHARED_PRODUCT,
        touch=False,
    )
    return interest
