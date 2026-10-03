from django.core.management.base import BaseCommand

from import_products_complete import import_products


class Command(BaseCommand):
    help = "Run the complete product import script (source API -> destination API)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Starting product import..."))
        import_products()
        self.stdout.write(self.style.SUCCESS("Product import command finished."))
