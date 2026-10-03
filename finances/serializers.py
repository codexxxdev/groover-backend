from rest_framework import serializers
from .models import Deposit,PaymentMethod,Withdrawal,BonusRequest
from django.contrib.auth  import get_user_model
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from shared.helpers import create_user_notification, create_admin_notification
from shared.cache_utils import invalidate_bonus_cache
from wallet.models import Wallet

User = get_user_model()

class UserPartialSerilzer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "last_name",
            "first_name",
            "is_active"
        ]


class DepositSerializer(serializers.ModelSerializer):
    # from users.serializers import UserPartialSerilzer
    # user = UserPartialSerilzer(read_only=True)  # Display the username instead of the ID

    class Meta:
        model = Deposit
        fields = [
            "id", "user", "amount", "date_time", "status", "screenshot", 
            "created_at", "updated_at"
        ]
        read_only_fields = ["user", "created_at", "updated_at",'status']

class PaymentMethodSerializer(serializers.ModelSerializer):
    """
    Serializer for PaymentMethod with user details mapping.
    """
    name = serializers.CharField(read_only=True) 
    phone_number = serializers.CharField(read_only=True) 
    email_address = serializers.CharField(read_only=True) 

    class Meta:
        model = PaymentMethod
        fields = "__all__"
        read_only_fields = ["name", "phone_number", "email_address",'user'] 

    def save(self, **kwargs):
        """
        Save method to map user details into the PaymentMethod instance.
        """
        user = kwargs.get("user")  # Get the user from the context or kwargs
        instance = super().save(**kwargs)

        # Map user details into the PaymentMethod fields
        if user:
            instance.name = f"{user.first_name if user.first_name else ''} {user.last_name if user.last_name else ''}".strip() or user.username
            instance.phone_number = getattr(user, "phone_number", None)
            instance.email_address = user.email
            instance.save()

        return instance


class WithdrawalSerializer:

    class MakeWithdrawal(serializers.ModelSerializer):
        amount = serializers.DecimalField(max_digits=10, decimal_places=2)
        password = serializers.CharField(write_only=True)  # Password should not be exposed in the response

        class Meta:
            model = Withdrawal
            fields = ["amount", "password"]

        def validate(self, data):
            data = super().validate(data)  # Ensure base validation logic is executed
            user = self.context['request'].user  # Access request.user from context
            
            if not user.is_authenticated:
                raise serializers.ValidationError("User is not authenticated.")

            # Call the can_withdraw method on the Withdrawal model
            can_withdraw, error = Withdrawal.can_withdraw(user, data["amount"], data["password"])
            
            if not can_withdraw:
                raise serializers.ValidationError({"error":error})
            
            # user_wallet = user.wallet
            # if user_wallet:
            #     user_wallet.debit(data['amount'])

            data.pop("password","")
            return data
        
    class ListWithdrawals(serializers.ModelSerializer):
        payment_method = PaymentMethodSerializer(read_only=True)
        class Meta:
            model = Withdrawal
            fields = "__all__"


class BonusRequestSerializer:
    class List(serializers.ModelSerializer):
        user = UserPartialSerilzer(read_only=True)
        created_by = UserPartialSerilzer(read_only=True)

        class Meta:
            model = BonusRequest
            fields = "__all__"

    class Create(serializers.ModelSerializer):
        user = serializers.PrimaryKeyRelatedField(queryset=User.objects.users())
        admin_password = serializers.CharField(write_only=True, required=True)

        class Meta:
            model = BonusRequest
            fields = ["user", "amount", "reason", "admin_password"]

        def validate_amount(self, value):
            if value <= 0:
                raise serializers.ValidationError("Bonus amount must be greater than 0.")
            return value

        def validate_admin_password(self, value):
            request = self.context.get("request")
            if not request or not request.user.is_authenticated or not request.user.is_staff:
                raise serializers.ValidationError("User does not have permission to perform this action.")
            if not request.user.check_transactional_password(value):
                raise serializers.ValidationError("Incorrect admin password.")
            return value

        def create(self, validated_data):
            validated_data.pop("admin_password", None)
            request = self.context.get("request")
            bonus = BonusRequest.objects.create(
                created_by=request.user if request else None,
                **validated_data,
            )
            invalidate_bonus_cache(bonus.user_id)
            create_user_notification(
                bonus.user,
                "Bonus Assigned",
                f"The system has assigned a bonus of {bonus.amount} USD to you. Please accept or reject it.",
            )
            create_admin_notification(
                "Bonus Created",
                f"A bonus of {bonus.amount} USD was assigned to {bonus.user.username}.",
            )
            return bonus

    class Update(serializers.ModelSerializer):
        admin_password = serializers.CharField(write_only=True, required=True)

        class Meta:
            model = BonusRequest
            fields = ["amount", "reason", "admin_password"]

        def validate_amount(self, value):
            if value <= 0:
                raise serializers.ValidationError("Bonus amount must be greater than 0.")
            return value

        def validate_admin_password(self, value):
            request = self.context.get("request")
            if not request or not request.user.is_authenticated or not request.user.is_staff:
                raise serializers.ValidationError("User does not have permission to perform this action.")
            if not request.user.check_transactional_password(value):
                raise serializers.ValidationError("Incorrect admin password.")
            return value

        def update(self, instance, validated_data):
            if instance.status != BonusRequest.STATUS_PENDING:
                raise ValidationError({"error": "Only pending bonuses can be edited."})
            validated_data.pop("admin_password", None)
            bonus = super().update(instance, validated_data)
            invalidate_bonus_cache(bonus.user_id)
            create_user_notification(
                bonus.user,
                "Bonus Updated",
                f"Your pending bonus has been updated to {bonus.amount} USD.",
            )
            return bonus

    class Revoke(serializers.Serializer):
        admin_password = serializers.CharField(write_only=True, required=True)
        reason = serializers.CharField(required=False, allow_blank=True)

        def validate_admin_password(self, value):
            request = self.context.get("request")
            if not request or not request.user.is_authenticated or not request.user.is_staff:
                raise serializers.ValidationError("User does not have permission to perform this action.")
            if not request.user.check_transactional_password(value):
                raise serializers.ValidationError("Incorrect admin password.")
            return value

        def save(self, **kwargs):
            bonus = self.context["bonus"]
            if bonus.status == BonusRequest.STATUS_REVOKED:
                raise ValidationError({"error": "This bonus has already been revoked."})

            if bonus.status == BonusRequest.STATUS_ACCEPTED and bonus.credited_amount > 0:
                wallet, _ = Wallet.objects.get_or_create(user=bonus.user)
                wallet.balance -= bonus.credited_amount
                wallet.save()

            bonus.status = BonusRequest.STATUS_REVOKED
            bonus.revoked_at = timezone.now()
            bonus.save(update_fields=["status", "revoked_at", "updated_at"])
            invalidate_bonus_cache(bonus.user_id)
            create_user_notification(
                bonus.user,
                "Bonus Revoked",
                f"Your bonus of {bonus.amount} USD has been revoked.",
            )
            return bonus

    class Pending(serializers.ModelSerializer):
        class Meta:
            model = BonusRequest
            fields = ["id", "amount", "reason", "status", "created_at", "updated_at"]

    class Respond(serializers.Serializer):
        action = serializers.ChoiceField(choices=["accept", "reject"])

        def save(self, **kwargs):
            bonus = self.context["bonus"]
            request = self.context["request"]

            if bonus.user_id != request.user.id:
                raise ValidationError({"error": "This bonus does not belong to the authenticated user."})
            if bonus.status != BonusRequest.STATUS_PENDING:
                raise ValidationError({"error": "This bonus is no longer pending."})

            action = self.validated_data["action"]
            if action == "reject":
                bonus.status = BonusRequest.STATUS_REJECTED
                bonus.rejected_at = timezone.now()
                bonus.save(update_fields=["status", "rejected_at", "updated_at"])
                invalidate_bonus_cache(bonus.user_id)
                create_user_notification(
                    bonus.user,
                    "Bonus Rejected",
                    f"You rejected the {bonus.amount} USD bonus.",
                )
                create_admin_notification(
                    "Bonus Rejected",
                    f"{bonus.user.username} rejected a bonus of {bonus.amount} USD.",
                )
                return bonus

            wallet, _ = Wallet.objects.get_or_create(user=bonus.user)
            wallet.credit(bonus.amount)
            bonus.status = BonusRequest.STATUS_ACCEPTED
            bonus.accepted_at = timezone.now()
            bonus.credited_amount = bonus.amount
            bonus.save(update_fields=["status", "accepted_at", "credited_amount", "updated_at"])
            invalidate_bonus_cache(bonus.user_id)
            create_user_notification(
                bonus.user,
                "Bonus Accepted",
                f"Your bonus of {bonus.amount} USD has been added to your wallet. New balance is {wallet.balance} USD.",
            )
            create_admin_notification(
                "Bonus Accepted",
                f"{bonus.user.username} accepted a bonus of {bonus.amount} USD.",
            )
            return bonus
