import uuid
import re
from django.db import models


def parse_numeric(value, is_price=False):
    if not value:
        return None
    value = str(value).strip()

    if is_price:
        # Handle price ranges like "$12,000-$15,000" - take average
        ranges = re.findall(r'[\d,]+', value.replace(',', ''))
        if ranges:
            nums = [float(r) for r in ranges]
            return sum(nums) / len(nums) if len(nums) > 1 else nums[0]
        return None

    # Remove all non-numeric characters except decimal point and minus
    cleaned = re.sub(r'[^\d.\-]', '', value)
    if cleaned:
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


class Car(models.Model):
    company = models.CharField(max_length=100, db_column="company_names")
    name = models.CharField(max_length=100, db_column="cars_names")
    engine = models.CharField(max_length=100, blank=True)
    engine_capacity = models.FloatField(null=True, blank=True, help_text="CC")
    horsepower = models.FloatField(null=True, blank=True, help_text="HP")
    total_speed = models.FloatField(null=True, blank=True, help_text="km/h")
    acceleration = models.FloatField(null=True, blank=True, help_text="0-100 km/h seconds")
    price = models.FloatField(null=True, blank=True, help_text="Price in $")
    fuel_type = models.CharField(max_length=50, blank=True)
    seats = models.IntegerField(null=True, blank=True)
    torque = models.FloatField(null=True, blank=True, help_text="Nm")

    class Meta:
        verbose_name_plural = "cars"
        db_table = "api_car"

    def __str__(self):
        return f"{self.company} {self.name}"


class Chat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class Message(models.Model):
    class Sender(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    class MessageType(models.TextChoices):
        USER_INPUT = "user_input", "User Input"
        AGENT_QUESTION = "agent_question", "Agent Question"
        COMPARISON = "comparison", "Comparison Table"
        PLAIN_TEXT = "plain_text", "Plain Text"

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField(max_length=20, choices=Sender.choices)
    message_type = models.CharField(max_length=20, choices=MessageType.choices)
    content = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender} - {self.message_type}"
