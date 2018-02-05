from datetime import datetime
from .BaseEvent import BaseEvent
from .. import Unknown
from ..Utilities.MonUtils import (
    get_pokemon_cp_range, is_weather_boosted, get_base_types, get_type_emoji
)
from ..Utilities.GenUtils import (
    get_gmaps_link, get_applemaps_link, get_time_as_str, get_seconds_remaining,
    get_weather_emoji
)


class RaidEvent(BaseEvent):

    def __init__(self, data):
        super(RaidEvent, self).__init__('raid')
        check_for_none = BaseEvent.check_for_none
        self.gym_id = data.get('gym_id')
        self.raid_end = datetime.utcfromtimestamp(
            data.get('end') or data.get('raid_end'))
        self.time_left = get_seconds_remaining(self.raid_end)
        self.lat = float(data['latitude'])
        self.lng = float(data['longitude'])
        self.raid_lvl = int(data['level'])
        self.mon_id = int(data['pokemon_id'])
        self.cp = int(data['cp'])
        self.types = get_base_types(self.mon_id)
        self.boss_level = 20
        self.weather_id = check_for_none(
            int, data.get('weather'), Unknown.TINY)
        self.boosted_weather_id = (
            0 if Unknown.is_not(self.weather_id) else Unknown.TINY
        )
        if is_weather_boosted(self.mon_id, self.weather_id):
            self.boosted_weather_id = self.weather_id
            self.boss_level = 25
        self.quick_id = check_for_none(int, data.get('move_1'), Unknown.TINY)
        self.charge_id = check_for_none(int, data.get('move_2'), Unknown.TINY)
        self.gym_name = check_for_none(
            str, data.get('name'), Unknown.REGULAR).strip()
        self.gym_image = check_for_none(str, data.get('url'), Unknown.REGULAR)
        self.gym_sponsor = check_for_none(
            int, data.get('sponsor'), Unknown.SMALL)
        self.gym_park = check_for_none(str, data.get('park'), Unknown.REGULAR)
        self.current_team_id = check_for_none(
            int, data.get('team'), Unknown.TINY)
        self.name = self.gym_id
        self.geofence = Unknown.REGULAR
        self.custom_dts = {}

    def generate_dts(self, locale):
        raid_end_time = get_time_as_str(self.raid_end, self.lat, self.lng)
        dts = self.custom_dts.copy()
        boosted_weather_name = locale.get_weather_name(boosted_weather_id)
        weather_name = locale.get_weather_name(self.weather_id)
        type1 = locale.get_type_name(self.types[0])
        type2 = locale.get_type_name(self.types[1])
        cp_range = get_pokemon_cp_range(self.mon_id, self.boss_level)
        dts.update({
            'gym_id': self.gym_id,
            'raid_time_left': raid_end_time[0],
            '12h_raid_end': raid_end_time[1],
            '24h_raid_end': raid_end_time[2],
            'type1': type1,
            'type1_or_empty': Unknown.or_empty(type1),
            'type1_emoji': Unknown.or_empty(get_type_emoji(self.types[0])),
            'type2': type2,
            'type2_or_empty': Unknown.or_empty(type2),
            'type2_emoji': Unknown.or_empty(get_type_emoji(self.types[1])),
            'types': (
                "{}/{}".format(type1, type2)
                if Unknown.is_not(type2) else type1),
            'types_emoji': (
                "{}{}".format(
                    get_type_emoji(self.types[0]),
                    get_type_emoji(self.types[1]))
                if Unknown.is_not(type2) else get_type_emoji(self.types[0])),
            'lat': self.lat,
            'lng': self.lng,
            'lat_5': "{:.5f}".format(self.lat),
            'lng_5': "{:.5f}".format(self.lng),
            'gmaps': get_gmaps_link(self.lat, self.lng),
            'applemaps': get_applemaps_link(self.lat, self.lng),
            'geofence': self.geofence,
            'weather_id': self.weather_id,
            'weather': weather_name,
            'weather_or_empty': Unknown.or_empty(weather_name),
            'weather_emoji': get_weather_emoji(self.weather_id),
            'boosted_weather_id': self.boosted_weather_id,
            'boosted_weather': boosted_weather_name,
            'boosted_weather_or_empty': (
                '' if self.boosted_weather_id == 0
                else Unknown.or_empty(boosted_weather_name)),
            'boosted_weather_emoji': get_weather_emoji(
                self.boosted_weather_id),
            'boosted_or_empty':
                locale.get_boosted_text() if self.boss_level == 25 else '',
            'raid_lvl': self.raid_lvl,
            'mon_name': locale.get_pokemon_name(self.mon_id),
            'mon_id': self.mon_id,
            'mon_id_3': "{:03}".format(self.mon_id),
            'quick_move': locale.get_move_name(self.quick_id),
            'quick_id': self.quick_id,
            'charge_move': locale.get_move_name(self.charge_id),
            'charge_id': self.charge_id,
            'cp': self.cp,
            'min_cp': cp_range[0],
            'max_cp': cp_range[1],
            'gym_name': self.gym_name,
            'gym_image': self.gym_image,
            'gym_sponsor': self.gym_sponsor,
            'gym_park': self.gym_park,
            'team_id': self.current_team_id,
            'team_name': locale.get_team_name(self.current_team_id),
            'team_leader': locale.get_leader_name(self.current_team_id)
        })
        return dts