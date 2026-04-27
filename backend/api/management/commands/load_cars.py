import csv
import re
from django.core.management.base import BaseCommand
from api.models import Car


def parse_numeric(value, is_price=False):
    if not value:
        return None
    value = str(value).strip()

    if is_price:
        ranges = re.findall(r'[\d,]+', value.replace(',', ''))
        if ranges:
            nums = [float(r) for r in ranges]
            return sum(nums) / len(nums) if len(nums) > 1 else nums[0]
        return None

    cleaned = re.sub(r'[^\d.\-]', '', value)
    if cleaned:
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def parse_engine_capacity(value):
    if not value:
        return None
    # Take first number found
    cleaned = re.sub(r'[^\d.]', '', str(value))
    match = re.search(r'[\d.]+', cleaned)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


class Command(BaseCommand):
    help = 'Load cars data from CSV file'

    def add_arguments(self, parser):
        parser.add_argument('file_path', nargs='?', default='./cars_data.csv')

    def handle(self, *args, **options):
        if Car.objects.exists():
            self.stdout.write(self.style.WARNING('Cars already loaded, skipping...'))
            return

        file_path = options['file_path']
        count = 0

        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for row in reader:
                Car.objects.create(
                    company=row.get('Company Names', '').strip(),
                    name=row.get('Cars Names', '').strip(),
                    engine=row.get('Engines', '').strip(),
                    engine_capacity=parse_engine_capacity(row.get('CC/Battery Capacity', '')),
                    horsepower=parse_numeric(row.get('HorsePower', '')),
                    total_speed=parse_numeric(row.get('Total Speed', '')),
                    acceleration=parse_numeric(row.get('Performance(0 - 100 )KM/H', '')),
                    price=parse_numeric(row.get('Cars Prices', ''), is_price=True),
                    fuel_type=row.get('Fuel Types', '').strip(),
                    seats=int(row.get('Seats', '').strip()) if row.get('Seats', '').strip().isdigit() else None,
                    torque=parse_numeric(row.get('Torque', '')),
                )
                count += 1

        self.stdout.write(self.style.SUCCESS(f'Loaded {count} cars'))
