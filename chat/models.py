from django.conf import settings
from django.db import models

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

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message #{self.pk} in Conversation #{self.conversation_id}"
