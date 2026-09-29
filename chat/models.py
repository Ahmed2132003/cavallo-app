from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

from core.models import TimestampedModel


class Conversation(TimestampedModel):
    """
    A conversation thread between exactly two accounts (any account_type
    combination is valid — no restriction here per confirmed decision).
    No direct participant FKs on this model; participants live in
    ConversationParticipant so the schema doesn't paint us into a
    1:1-only corner if group chat is ever needed later.
    """

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Conversation #{self.pk}"


class ConversationParticipant(TimestampedModel):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_participations",
    )

    class Meta:
        unique_together = ("conversation", "user")

    def __str__(self):
        return f"{self.user} in Conversation #{self.conversation_id}"


class Message(TimestampedModel):
    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"

    class MediaType(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_messages",
    )
    text = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SENT,
    )
    # Part P-076. Plain FileField (not ImageField): same P-013/P-032/P-041
    # convention as Post.image / Reel.video / Story.media (no Pillow).
    # Real content validation happens in MessageSerializer.validate_media()
    # via core.media.validate_upload(). Optional: a text-only message
    # leaves it null; a media-only message leaves `text` blank.
    media = models.FileField(upload_to="chat/media/", null=True, blank=True)
    # "image" / "video" / "" (no media). Set by the serializer from the
    # SNIFFED content type (never from the client), so the Flutter client
    # can render the right bubble without guessing from a filename.
    media_type = models.CharField(
        max_length=10,
        choices=MediaType.choices,
        blank=True,
        default="",
    )

    # Part P-077 — optional reference to a piece of platform content
    # (Post / Reel / Product) shared into the conversation. Same generic-FK
    # pattern as Like/Save/Share (social/models.py). Both columns are
    # nullable and are ALWAYS set together or left null together (enforced
    # by the CheckConstraint below). Allowed target types are whitelisted
    # in chat/serializers.py (SHARE_TO_CHAT_CONTENT_TYPES), never derived
    # from the ContentType table. PROTECT (not SET_NULL) so deleting a
    # ContentType row can never leave a half-null reference behind.
    shared_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    shared_object_id = models.PositiveIntegerField(null=True, blank=True)
    shared_content = GenericForeignKey("shared_content_type", "shared_object_id")

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(shared_content_type__isnull=True, shared_object_id__isnull=True)
                    | Q(
                        shared_content_type__isnull=False,
                        shared_object_id__isnull=False,
                    )
                ),
                name="message_shared_ref_both_or_neither",
            ),
        ]

    def __str__(self):
        return f"Message #{self.pk} in Conversation #{self.conversation_id}"
