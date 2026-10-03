from rest_framework.viewsets import ViewSet
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import action

from .models import Deposit,PaymentMethod,Withdrawal,BonusRequest
from .serializers import DepositSerializer,PaymentMethodSerializer,WithdrawalSerializer,BonusRequestSerializer
from core.permissions import IsAdminOrReadCreateOnlyForRegularUsers, IsSiteAdmin
from shared.mixins import StandardResponseMixin
from shared.helpers import create_user_notification,create_admin_notification,create_admin_log
from shared.cache_utils import cache_result


class DepositViewSet(StandardResponseMixin, ViewSet):
    """
    ViewSet for managing deposits.
    - Admin users have full access to all deposits.
    - Regular users can only create deposits and view their own deposits.
    """
    permission_classes = [IsAuthenticated, IsAdminOrReadCreateOnlyForRegularUsers]
    parser_classes = [MultiPartParser, FormParser]

    def list(self, request):
        """
        List deposits made by the authenticated user.
        """
        if getattr(self, 'swagger_fake_view', False): 
            return Response([], status=status.HTTP_200_OK)
        
        user = request.user
        deposits = Deposit.objects.filter(user=user).order_by('-date_time')  # Regular user: Their deposits

        serializer = DepositSerializer(deposits, many=True)
        return self.standard_response(
            success=True,
            message="Deposits retrieved successfully.", 
            data=serializer.data,
            status_code=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description="Create a deposit for the authenticated user.",
        request_body=DepositSerializer,  # Use the DepositSerializer for the request body
        responses={
            201: openapi.Response(
                description="Deposit created successfully.",
                examples={
                    "application/json": {
                        "success": True,
                        "message": "Deposit created successfully.",
                        "data": {
                            "id": 1,
                            "amount": "100.00",
                            "user": "username",
                            "status": "Pending",
                            "created_at": "2024-11-24T12:00:00Z",
                        },
                    }
                },
            ),
            400: openapi.Response(
                description="Validation error.",
                examples={
                    "application/json": {
                        "success": False,
                        "message": "Validation failed.",
                        "data": {
                            "amount": ["This field is required."],
                        },
                    }
                },
            ),
        },
    )

    def create(self, request):
        """
        Create a deposit for the authenticated user.
        """
        serializer = DepositSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user, status="Pending")
        message = f"You made a deposit of {serializer.validated_data['amount']} USD"
        create_user_notification(
            user=request.user,
            title="Deposit",
            message=message
            )
        message = f"A new deposit of {serializer.validated_data['amount']} USD was initialized by {request.user.username}"
        create_admin_notification(
            "Deposit Created",
            message,
        )
        return self.standard_response(
            success=True,
            message="Deposit created successfully.",
            data=serializer.data,
            status_code=status.HTTP_201_CREATED,
        )



class PaymentMethodViewSet(StandardResponseMixin, ViewSet):
    """
    ViewSet for managing user payment methods.
    - Users can create, view, update, and delete their payment methods.
    """
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """
        List or create the authenticated user's payment method.
        """
        user = request.user

        # Use get_or_create to ensure a payment method exists
        payment_method, created = PaymentMethod.objects.get_or_create(
            user=user,
            defaults={
                "name": (
                    f"{user.first_name if user.first_name else ''} {user.last_name if user.last_name else ''}".strip()
                ) or user.username,
                "phone_number": getattr(user, "phone_number", ""),
                "email_address": user.email,
                "wallet": "",
                "exchange": ""
            }
        )

        # Ensure `name` is properly updated after creation
        if created:
            payment_method.name = (
                f"{user.first_name if user.first_name else ''} {user.last_name if user.last_name else ''}".strip()
            ) or user.username
            payment_method.save()

        serializer = PaymentMethodSerializer(payment_method)

        message = (
            "Payment method created successfully." if created else "Payment method retrieved successfully."
        )

        return self.standard_response(
            success=True,
            message=message,
            data=serializer.data,
            status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
    @swagger_auto_schema(
        operation_summary="Create or Update Payment Method",
        operation_description="Create or update the authenticated user's payment method.",
        request_body=PaymentMethodSerializer,
        responses={
            201: openapi.Response(
                description="Payment method created successfully.",
                schema=PaymentMethodSerializer
            ),
            200: openapi.Response(
                description="Payment method updated successfully.",
                schema=PaymentMethodSerializer
            ),
            400: "Validation error occurred.",
        }
    )
    def create(self, request):
        """
        Create or update the authenticated user's payment method.
        """
        # Use `get_or_create` to ensure a single payment method per user
        payment_method, created = PaymentMethod.objects.get_or_create(user=request.user)
        serializer = PaymentMethodSerializer(payment_method, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        create_user_notification(request.user,"Payment Method",f"Your Payment details has been updated successfully")
        create_admin_notification("Payment Method",f"{request.user.username} has updated is payment method")

        message = "Payment method created successfully." if created else "Payment method updated successfully."
        return self.standard_response(
            success=True,
            message=message,
            data=serializer.data,
            status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
    
class WithdrawalViewSet(StandardResponseMixin, ViewSet):

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Create a withdrawal request for a user.",
        request_body=WithdrawalSerializer.MakeWithdrawal,
        responses={
            201: openapi.Response(
                description="Withdrawal request successfully created.",
                examples={
                    "application/json": {
                        "success": True,
                        "message": "Withdrawal Request Made Successfully.",
                        "data": {
                            "amount": "100.00",
                        },
                    }
                },
            ),
            400: openapi.Response(
                description="Validation failed.",
                examples={
                    "application/json": {
                        "success": False,
                        "message": "Validation failed.",
                        "data": {
                            "amount": ["This field is required."],
                            "password": ["This field is required."]
                        },
                    }
                },
            ),
        },
    )
    @action(detail=False, methods=['post'])
    def make_withdrawal(self, request):
        """
        Handle withdrawal requests.
        """
        serializer = WithdrawalSerializer.MakeWithdrawal(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        # Process the withdrawal logic here
        amount = serializer.validated_data['amount']
        
        # Assuming the user has a `payment_method` attribute
        payment_method = request.user.payment_method

        # Create the withdrawal record
        Withdrawal.objects.create(user=request.user, amount=amount, payment_method=payment_method)
        message = f"You made a withdrawal request of  {serializer.validated_data['amount']} USD"
        create_user_notification(
            user=request.user,
            title="Withdrawal",
            message=message
            )
        message = f"A new withdrawal of {serializer.validated_data['amount']} USD was initialized by {request.user.username}"
        create_admin_notification("Withdrawal",message)
        # Custom standard response
        return self.standard_response(
            success=True,
            message="Withdrawal Request Made Successfully.",
            data=serializer.validated_data,
            status_code=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['get'])
    def withdrawal_history(self,request):
        """
        Retrieve the withdrawal history for the authenticated user.
        """
        withdrawals = Withdrawal.objects.filter(user=request.user)
        serializer = WithdrawalSerializer.ListWithdrawals(withdrawals, many=True)
        return self.standard_response(
            success=True,
            message="Withdrawal History Fetched succesfully.",
            data=serializer.data,
            status_code=status.HTTP_201_CREATED,
        )


class AdminBonusRequestViewSet(StandardResponseMixin, ViewSet):
    permission_classes = [IsSiteAdmin]

    def get_serializer_class(self):
        action_to_serializer = {
            "list": BonusRequestSerializer.List,
            "create": BonusRequestSerializer.Create,
            "partial_update": BonusRequestSerializer.Update,
            "revoke": BonusRequestSerializer.Revoke,
        }
        return action_to_serializer.get(self.action, BonusRequestSerializer.List)

    def list(self, request):
        bonuses = BonusRequest.objects.select_related("user", "created_by").all()
        status_filter = request.query_params.get("status")
        if status_filter:
            bonuses = bonuses.filter(status=status_filter)
        serializer = BonusRequestSerializer.List(bonuses, many=True)
        return self.standard_response(
            success=True,
            message="Bonus requests retrieved successfully.",
            data=serializer.data,
            status_code=status.HTTP_200_OK,
        )

    def create(self, request):
        serializer = self.get_serializer_class()(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        bonus = serializer.save()
        create_admin_log(
            request,
            f"Created bonus request #{bonus.id} for {bonus.user.username}. Amount: {bonus.amount} USD",
        )
        return self.standard_response(
            success=True,
            message="Bonus request created successfully.",
            data=BonusRequestSerializer.List(bonus).data,
            status_code=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, pk=None):
        try:
            bonus = BonusRequest.objects.get(pk=pk)
        except BonusRequest.DoesNotExist:
            return self.standard_response(
                success=False,
                message="Bonus request not found.",
                data=None,
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = self.get_serializer_class()(bonus, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        bonus = serializer.save()
        create_admin_log(
            request,
            f"Updated bonus request #{bonus.id} for {bonus.user.username}. Amount: {bonus.amount} USD",
        )
        return self.standard_response(
            success=True,
            message="Bonus request updated successfully.",
            data=BonusRequestSerializer.List(bonus).data,
            status_code=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        try:
            bonus = BonusRequest.objects.get(pk=pk)
        except BonusRequest.DoesNotExist:
            return self.standard_response(
                success=False,
                message="Bonus request not found.",
                data=None,
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = self.get_serializer_class()(data=request.data, context={"request": request, "bonus": bonus})
        serializer.is_valid(raise_exception=True)
        bonus = serializer.save()
        create_admin_log(
            request,
            f"Revoked bonus request #{bonus.id} for {bonus.user.username}. Amount: {bonus.amount} USD",
            reason=serializer.validated_data.get("reason"),
        )
        return self.standard_response(
            success=True,
            message="Bonus request revoked successfully.",
            data=BonusRequestSerializer.List(bonus).data,
            status_code=status.HTTP_200_OK,
        )


class UserBonusRequestViewSet(StandardResponseMixin, ViewSet):
    permission_classes = [IsAuthenticated]

    @cache_result("BONUSES", "user.id", ttl=30)
    @action(detail=False, methods=["get"], url_path="pending")
    def pending(self, request):
        bonus = (
            BonusRequest.objects.filter(user=request.user, status=BonusRequest.STATUS_PENDING)
            .order_by("created_at")
            .first()
        )
        data = BonusRequestSerializer.Pending(bonus).data if bonus else None
        return self.standard_response(
            success=True,
            message="Pending bonus fetched successfully.",
            data=data,
            status_code=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="respond")
    def respond(self, request, pk=None):
        try:
            bonus = BonusRequest.objects.get(pk=pk, user=request.user)
        except BonusRequest.DoesNotExist:
            return self.standard_response(
                success=False,
                message="Bonus request not found.",
                data=None,
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = BonusRequestSerializer.Respond(data=request.data, context={"request": request, "bonus": bonus})
        serializer.is_valid(raise_exception=True)
        bonus = serializer.save()
        return self.standard_response(
            success=True,
            message="Bonus accepted successfully." if bonus.status == BonusRequest.STATUS_ACCEPTED else "Bonus rejected successfully.",
            data=BonusRequestSerializer.Pending(bonus).data,
            status_code=status.HTTP_200_OK,
        )
