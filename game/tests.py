import time
from decimal import Decimal
from itertools import combinations

from django.test import TestCase
from django.contrib.auth import get_user_model

from game.models import Product
from game.serializers import AdminNegativeUserSerializer
from packs.models import Pack
from wallet.models import Wallet, OnHoldPay


User = get_user_model()


class NegativeUserSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="tester",
            email="tester@example.com",
            password="pass1234",
            phone_number="1234567890",
            transactional_password="1234",
        )
        self.pack = Pack.objects.create(
            name="Starter",
            usd_value=Decimal("0.00"),
            daily_missions=1,
            daily_withdrawals=1,
            icon="test-pack.jpg",
            created_by=self.user,
            profit_percentage=Decimal("1.00"),
            special_product_percentage=Decimal("2.00"),
            payment_bonus=Decimal("0.00"),
            payment_limit_to_trigger_bonus=Decimal("0.00"),
            minimum_balance_for_submissions=Decimal("0.00"),
            number_of_set=1,
            short_description="short",
            description="long",
            is_active=True,
        )
        Wallet.objects.create(user=self.user, balance=Decimal("0.00"), package=self.pack)
        self.on_hold = OnHoldPay.objects.create(
            min_amount=Decimal("0.00"),
            max_amount=Decimal("1000.00"),
            is_active=True,
        )

    def test_number_of_negative_product_min_is_1(self):
        serializer = AdminNegativeUserSerializer.Create(
            data={
                "user": self.user.id,
                "on_hold": self.on_hold.id,
                "number_of_negative_product": 0,
                "rank_appearance": 1,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("number_of_negative_product", serializer.errors)


class SelectProductsPerformanceTests(TestCase):
    def test_select_products_with_1000_products(self):
        products = []
        for i in range(1, 1001):
            products.append(
                Product(
                    name=f"Product {i}",
                    price=Decimal(i),
                    description="desc",
                    image="test-product.jpg",
                    rating_no=f"{i:011d}",
                )
            )
        Product.objects.bulk_create(products)

        serializer = AdminNegativeUserSerializer.Create()
        min_amount = Decimal("1000.00")
        max_amount = Decimal("1000.00")
        max_products = 3

        start = time.perf_counter()
        result = serializer.select_products_within_range(min_amount, max_amount, max_products)
        duration = time.perf_counter() - start

        self.assertEqual(len(result), 3)
        # This print helps you see timing when running tests locally.
        print(f"select_products_within_range duration: {duration:.4f}s for 1000 products")


class SelectProductsExactnessTests(TestCase):
    def setUp(self):
        self.products = []
        prices = [
            "10.00", "12.50", "15.25", "20.00", "25.75",
            "30.00", "33.33", "40.00", "50.00", "60.50",
        ]
        for i, price in enumerate(prices, start=1):
            self.products.append(
                Product(
                    name=f"Base {i}",
                    price=Decimal(price),
                    description="desc",
                    image="test-product.jpg",
                    rating_no=f"{i:011d}",
                )
            )
        Product.objects.bulk_create(self.products)
        self.serializer = AdminNegativeUserSerializer.Create()

    def _sum_prices(self, products):
        return sum(Decimal(p.price) for p in products)

    def test_max_products_1(self):
        result = self.serializer.select_products_within_range(
            Decimal("19.00"), Decimal("21.00"), 1
        )
        self.assertEqual(len(result), 1)
        total = self._sum_prices(result)
        self.assertTrue(Decimal("19.00") <= total <= Decimal("21.00"))

    def test_max_products_2(self):
        result = self.serializer.select_products_within_range(
            Decimal("45.00"), Decimal("46.00"), 2
        )
        self.assertEqual(len(result), 2)
        total = self._sum_prices(result)
        self.assertTrue(Decimal("45.00") <= total <= Decimal("46.00"))

    def test_max_products_3(self):
        result = self.serializer.select_products_within_range(
            Decimal("57.75"), Decimal("58.25"), 3
        )
        self.assertEqual(len(result), 3)
        total = self._sum_prices(result)
        self.assertTrue(Decimal("57.75") <= total <= Decimal("58.25"))

    def test_no_valid_combo_returns_empty(self):
        result = self.serializer.select_products_within_range(
            Decimal("999.00"), Decimal("1000.00"), 3
        )
        self.assertEqual(result, [])

    def test_max_products_greater_than_3_returns_empty(self):
        result = self.serializer.select_products_within_range(
            Decimal("50.00"), Decimal("60.00"), 4
        )
        self.assertEqual(result, [])

    def test_handles_more_products(self):
        more_products = []
        base = 1000
        for i in range(1, 501):
            more_products.append(
                Product(
                    name=f"Extra {i}",
                    price=Decimal(i + base),
                    description="desc",
                    image="test-product.jpg",
                    rating_no=f"{i + base:011d}",
                )
            )
        Product.objects.bulk_create(more_products)
        result = self.serializer.select_products_within_range(
            Decimal("1050.00"), Decimal("1055.00"), 2
        )
        self.assertEqual(len(result), 2)
        total = self._sum_prices(result)
        self.assertTrue(Decimal("1050.00") <= total <= Decimal("1055.00"))


def _old_select_products_within_range(products, min_amount, max_amount, max_products):
    """
    Previous brute-force approach using combinations.
    WARNING: This can be extremely slow for larger inputs.
    """
    for combination in combinations(products, max_products):
        total_price = sum(Decimal(product.price) for product in combination)
        if min_amount <= total_price <= max_amount:
            return list(combination)
    return []


class SelectProductsComparisonTests(TestCase):
    def test_compare_old_and_new_with_high_prices(self):
        """
        Compare timing of the old brute-force method vs the new deterministic method.
        Uses high prices and a bounded product count to avoid very long runtimes.
        """
        products = []
        for i in range(1, 201):
            products.append(
                Product(
                    name=f"High {i}",
                    price=Decimal(100000 + i),
                    description="desc",
                    image="test-product.jpg",
                    rating_no=f"{90000000000 + i}",
                )
            )
        Product.objects.bulk_create(products)

        serializer = AdminNegativeUserSerializer.Create()
        # Use a guaranteed valid target: sum of the three highest prices
        target_sum = Decimal(100000 + 198) + Decimal(100000 + 199) + Decimal(100000 + 200)
        min_amount = target_sum
        max_amount = target_sum
        max_products = 3

        start_new = time.perf_counter()
        new_result = serializer.select_products_within_range(min_amount, max_amount, max_products)
        new_duration = time.perf_counter() - start_new

        start_old = time.perf_counter()
        old_result = _old_select_products_within_range(products, min_amount, max_amount, max_products)
        old_duration = time.perf_counter() - start_old

        self.assertEqual(len(new_result), 3)
        self.assertEqual(len(old_result), 3)
        print(
            "comparison high prices: "
            f"new={new_duration:.4f}s, old={old_duration:.4f}s for 200 products"
        )


def _old_select_with_limits(products, min_amount, max_amount, max_products, max_checks=200000, time_limit_s=2.0):
    """
    Old brute-force with safety limits to avoid very long runtimes.
    Returns (result, checked, timed_out)
    """
    start = time.perf_counter()
    checked = 0
    for combination in combinations(products, max_products):
        checked += 1
        if checked >= max_checks or (time.perf_counter() - start) >= time_limit_s:
            return None, checked, True
        total_price = sum(Decimal(product.price) for product in combination)
        if min_amount <= total_price <= max_amount:
            return list(combination), checked, False
    return [], checked, False


class SelectProductsComparison2000Tests(TestCase):
    def test_compare_old_and_new_with_2000_products(self):
        products = []
        for i in range(1, 2001):
            products.append(
                Product(
                    name=f"High {i}",
                    price=Decimal(100000 + i),
                    description="desc",
                    image="test-product.jpg",
                    rating_no=f"{80000000000 + i}",
                )
            )
        Product.objects.bulk_create(products)

        serializer = AdminNegativeUserSerializer.Create()
        # Target the highest three prices to force old method to scan very deep.
        max_products = 3
        target_sum = Decimal(100000 + 1998) + Decimal(100000 + 1999) + Decimal(100000 + 2000)
        min_amount = target_sum
        max_amount = target_sum

        start_new = time.perf_counter()
        new_result = serializer.select_products_within_range(min_amount, max_amount, max_products)
        new_duration = time.perf_counter() - start_new

        start_old = time.perf_counter()
        old_result, checked, timed_out = _old_select_with_limits(
            products, min_amount, max_amount, max_products, max_checks=200000, time_limit_s=2.0
        )
        old_duration = time.perf_counter() - start_old

        self.assertEqual(len(new_result), 3)
        # Old method is expected to time out at this scale.
        if timed_out:
            print(
                "comparison 2000 products: "
                f"new={new_duration:.4f}s, old={old_duration:.4f}s (timed out after {checked} checks)"
            )
        else:
            print(
                "comparison 2000 products: "
                f"new={new_duration:.4f}s, old={old_duration:.4f}s (finished after {checked} checks)"
            )
