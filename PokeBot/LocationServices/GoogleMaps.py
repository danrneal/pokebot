import logging
import googlemaps
from random import randint

log = logging.getLogger('LocService')


class GoogleMaps(object):

    def __init__(self, api_key, locale):
        self.__api_key = api_key
        self.__locale = locale
        self.__reverse_location = False
        self.__reverse_location_history = {}

    def add_optional_arguments(self, dest, data):
        if self.__reverse_location:
            data.update(**self.__get_reverse_location(dest))

    def enable_reverse_location(self):
        if not self.__reverse_location:
            self.__reverse_location = True
            log.info("Reverse Location DTS detected - API has been enabled!")

    def __get_reverse_location(self, location):
        key = "{:.5f},{:.5f}".format(location[0], location[1])
        if key in self.__reverse_location_history:
            return self.__reverse_location_history[key]
        details = {
            'street_num': '???',
            'street': 'unknown',
            'address': 'unknown',
            'postal': 'unknown',
            'neighborhood': 'unknown',
            'sublocality': 'unknown',
            'city': 'unknown',
            'county': 'unknown',
            'state': 'unknown',
            'country': 'country'
        }
        try:
            gmaps_key = self.__api_key[randint(0, len(self.__api_key) - 1)]
            client = googlemaps.Client(
                key=gmaps_key, timeout=3, retry_timeout=5)
            result = client.reverse_geocode(
                location, language=self.__locale)[0]
            loc = {}
            for item in result['address_components']:
                for category in item['types']:
                    loc[category] = item['short_name']
            details['street_num'] = loc.get('street_number', '')
            details['street'] = loc.get('route', '')
            details['address'] = "{} {}".format(
                details['street_num'], details['street'])
            details['address_eu'] = "{} {}".format(
                details['street'], details['street_num'])
            details['postal'] = loc.get('postal_code', 'unknown')
            details['neighborhood'] = loc.get(
                'neighborhood', details['street'])
            details['sublocality'] = loc.get('sublocality', "unknown")
            details['city'] = loc.get('locality', loc.get(
                'postal_town', 'unknown'))
            details['county'] = loc.get(
                'administrative_area_level_2', 'unknown')
            details['state'] = loc.get(
                'administrative_area_level_1', 'unknown')
            details['country'] = loc.get('country', 'unknown')
            self.__reverse_location_history[key] = details
        except Exception as e:
            log.error((
                "Encountered error while getting reverse location data ({}: " +
                "{})"
            ).format(type(e).__name__, e))
        return details