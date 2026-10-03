from rest_framework.routers import DefaultRouter
from .views import DepositViewSet,PaymentMethodViewSet,WithdrawalViewSet,AdminBonusRequestViewSet,UserBonusRequestViewSet

router = DefaultRouter()
router.register(r'deposits', DepositViewSet, basename='deposit')
router.register(r'payments', PaymentMethodViewSet, basename='deposit')
router.register(r'withdrawals', WithdrawalViewSet, basename='withdrawal')
router.register(r'bonuses', UserBonusRequestViewSet, basename='bonus')
router.register(r'admin-bonuses', AdminBonusRequestViewSet, basename='admin-bonus')

urlpatterns = router.urls
