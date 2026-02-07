"""
Pickup Cost Calculator

Calculates the fuel cost to pick up an item based on:
- Distance to seller (× 2 for round trip)
- Vehicle fuel efficiency (MPG)
- Current gas prices in the area
"""
import re
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class PickupCost:
    """Calculated pickup cost breakdown"""
    distance_miles: float  # One-way distance
    round_trip_miles: float
    gas_price_per_gallon: float
    vehicle_mpg: float
    gallons_needed: float
    fuel_cost: float
    gas_price_source: str  # "api", "override", "default"
    
    def __str__(self):
        return f"${self.fuel_cost:.2f} ({self.round_trip_miles:.1f}mi @ ${self.gas_price_per_gallon:.2f}/gal)"


class PickupCostCalculator:
    """
    Calculates the cost to drive and pick up an item.
    
    Uses GasBuddy or AAA for gas prices, falls back to national average.
    """
    
    # National average as fallback (updated periodically)
    DEFAULT_GAS_PRICE = 3.25
    
    def __init__(
        self,
        vehicle_mpg: float = 25.0,
        gas_price_override: float = 0.0,
        zip_code: str = ""
    ):
        self.vehicle_mpg = vehicle_mpg
        self.gas_price_override = gas_price_override
        self.zip_code = zip_code
        self._cached_gas_price: Optional[float] = None
        self._gas_price_source = "default"
    
    async def get_gas_price(self, zip_code: str = None) -> tuple[float, str]:
        """
        Get current gas price for zip code.
        
        Returns: (price_per_gallon, source)
        """
        # Use override if set
        if self.gas_price_override > 0:
            return self.gas_price_override, "override"
        
        # Use cached price if available
        if self._cached_gas_price:
            return self._cached_gas_price, self._gas_price_source
        
        zip_code = zip_code or self.zip_code
        
        # Try to fetch from API
        try:
            price = await self._fetch_gas_price_api(zip_code)
            if price:
                self._cached_gas_price = price
                self._gas_price_source = "api"
                return price, "api"
        except Exception as e:
            print(f"      ⚠️ Gas price lookup failed: {e}")
        
        # Fall back to default
        return self.DEFAULT_GAS_PRICE, "default"
    
    async def _fetch_gas_price_api(self, zip_code: str) -> Optional[float]:
        """
        Fetch gas price from API.
        
        Uses collectapi.com gasoline prices (free tier) or scrapes GasBuddy.
        """
        if not zip_code:
            return None
        
        # Try scraping GasBuddy (no API key needed)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # GasBuddy ZIP search
                url = f"https://www.gasbuddy.com/home?search={zip_code}&fuel=1"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                response = await client.get(url, headers=headers, follow_redirects=True)
                
                if response.status_code == 200:
                    # Look for average price in response
                    # GasBuddy shows prices like "$3.459"
                    text = response.text
                    
                    # Try to find price pattern
                    price_match = re.search(r'\$(\d+\.\d{2,3})', text)
                    if price_match:
                        price = float(price_match.group(1))
                        if 1.50 < price < 8.00:  # Sanity check
                            return price
        except Exception:
            pass
        
        # Try AAA average (national)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # AAA gas prices API
                url = "https://gasprices.aaa.com/wp-json/aaa-gas-prices/v1/averages"
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    # Look for national average
                    if 'national' in data:
                        return float(data['national'].get('regular', self.DEFAULT_GAS_PRICE))
        except Exception:
            pass
        
        return None
    
    def parse_distance_from_location(self, location: str) -> Optional[float]:
        """
        Parse distance in miles from FB location string.
        
        Examples:
        - "5 miles away" → 5.0
        - "10 mi" → 10.0
        - "Listed 2 days ago in Pittsburgh, PA" → None (no distance)
        - "Pittsburgh, PA · 12 miles away" → 12.0
        """
        if not location:
            return None
        
        location = location.lower()
        
        # Pattern: number followed by "mile(s)" or "mi"
        patterns = [
            r'(\d+(?:\.\d+)?)\s*miles?\s*away',
            r'(\d+(?:\.\d+)?)\s*mi\b',
            r'·\s*(\d+(?:\.\d+)?)\s*miles?',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, location)
            if match:
                return float(match.group(1))
        
        return None
    
    async def calculate(
        self,
        distance_miles: float = None,
        location_string: str = None,
        zip_code: str = None
    ) -> Optional[PickupCost]:
        """
        Calculate the fuel cost for a round-trip pickup.
        
        Args:
            distance_miles: Direct distance in miles (one-way)
            location_string: FB location string like "5 miles away"
            zip_code: ZIP code for gas price lookup
            
        Returns:
            PickupCost with breakdown, or None if distance unknown
        """
        # Get distance
        if distance_miles is None and location_string:
            distance_miles = self.parse_distance_from_location(location_string)
        
        if distance_miles is None:
            return None
        
        # Get gas price
        gas_price, source = await self.get_gas_price(zip_code)
        
        # Calculate
        round_trip = distance_miles * 2
        gallons_needed = round_trip / self.vehicle_mpg
        fuel_cost = gallons_needed * gas_price
        
        return PickupCost(
            distance_miles=distance_miles,
            round_trip_miles=round_trip,
            gas_price_per_gallon=gas_price,
            vehicle_mpg=self.vehicle_mpg,
            gallons_needed=gallons_needed,
            fuel_cost=fuel_cost,
            gas_price_source=source
        )


# Quick test
if __name__ == "__main__":
    import asyncio
    
    async def test():
        calc = PickupCostCalculator(vehicle_mpg=25.0, zip_code="15213")
        
        # Test distance parsing
        test_locations = [
            "5 miles away",
            "Pittsburgh, PA · 12 miles away",
            "10 mi",
            "Listed in Pittsburgh",  # No distance
        ]
        
        print("Distance parsing:")
        for loc in test_locations:
            dist = calc.parse_distance_from_location(loc)
            print(f"  '{loc}' → {dist} miles")
        
        # Test cost calculation
        print("\nPickup cost calculation:")
        cost = await calc.calculate(distance_miles=15)
        if cost:
            print(f"  15 miles one-way: {cost}")
            print(f"  Breakdown:")
            print(f"    Round trip: {cost.round_trip_miles} miles")
            print(f"    Gas price: ${cost.gas_price_per_gallon:.2f}/gal ({cost.gas_price_source})")
            print(f"    Gallons needed: {cost.gallons_needed:.2f}")
            print(f"    Fuel cost: ${cost.fuel_cost:.2f}")
    
    asyncio.run(test())
