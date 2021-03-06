import logging
import asyncio
import sys
import json
import discord
from datetime import datetime, timedelta
from collections import OrderedDict, namedtuple
from .Geofence import load_geofence_file
from .Alarms import alarm_factory
from .LocationServices.GMaps import GMaps
from .Locale import Locale
from .Cache import cache_factory
from .Events import MonEvent, EggEvent, RaidEvent
from .Filters.MonFilter import MonFilter
from .Filters.EggFilter import EggFilter
from .Filters.RaidFilter import RaidFilter
from .Utilities.GenUtils import get_path, update_filters
from .commands import (
    status, commands, dex, set_raids, set_eggs, delete_raids, delete_eggs,
    set_, delete, reset, pause, resume, activate, deactivate, alerts_eggs,
    alerts_raids, alerts, areas
)

log = logging.getLogger('BotManager')

Rule = namedtuple('Rule', ['filter_names', 'alarm_names'])


class BotManager(discord.Client):

    def __init__(self, name, bot_number, google_key, locale, cache_type,
                 filter_file, geofence_file, alarm_file, command_channels,
                 alert_role, muted_role, all_areas, ex_parks, number_of_bots):
        super(BotManager, self).__init__()
        self.__name = str(name).lower()
        self.__bot_number = bot_number
        log.info("----------- Bot Manager '{}' is being created.".format(
            self.__name))
        self._google_key = None
        self._gmaps_service = None
        if len(google_key) > 0:
            self._google_key = google_key
            self._gmaps_service = GMaps(google_key)
        self._language = locale
        self.__locale = Locale(locale)
        self.__cache = cache_factory(cache_type, self.__name)
        self.__mons_enabled, self.__mon_filters = {}, OrderedDict()
        self.__eggs_enabled, self.__egg_filters = {}, OrderedDict()
        self.__raids_enabled, self.__raid_filters = {}, OrderedDict()
        self.load_filter_file(get_path(filter_file))
        self.__filter_file = filter_file
        self.geofences = None
        if str(geofence_file).lower() != 'none':
            self.geofences = load_geofence_file(get_path(geofence_file))
        self.__alarms = {}
        self.load_alarms_file(get_path(alarm_file))
        self.__command_channels = command_channels
        self.__alert_role = alert_role
        self.__muted_role = muted_role
        self.__all_areas = all_areas
        self.__ex_parks = ex_parks
        self.__number_of_bots = number_of_bots
        self.__queue = asyncio.Queue()
        log.info("----------- Manager '{}' successfully created.".format(
            self.__name))

    async def update(self, obj):
        await self.__queue.put(obj)

    def get_name(self):
        return self.__name

    def get_bot_number(self):
        return self.__bot_number

    def get_alarm(self):
        return self.__alarms['user_alarm']

    def enable_gmaps_reverse_geocoding(self):
        if not self._gmaps_service:
            raise ValueError(
                "Unable to enable Google Maps Reverse Geocoding.  No GMaps " +
                "API key has been set."
            )
        self._gmaps_reverse_geocode = True

    @staticmethod
    def load_filter_section(section, sect_name, filter_type):
        defaults = section.pop('defaults', {})
        default_dts = defaults.pop('custom_dts', {})
        filter_set = OrderedDict()
        for name, settings in section.pop('filters', {}).items():
            settings = dict(list(defaults.items()) + list(settings.items()))
            try:
                local_dts = dict(
                    list(default_dts.items()) +
                    list(settings.pop('custom_dts', {}).items())
                )
                if len(local_dts) > 0:
                    settings['custom_dts'] = local_dts
                filter_set[name] = filter_type(name, settings)
            except Exception as e:
                log.error("Encountered error inside filter named '{}'.".format(
                    name))
                raise e
        for key in section:
            raise ValueError((
                "'{}' is not a recognized parameter for the '{}' section."
            ).format(key, sect_name))
        return filter_set

    def load_filter_file(self, file_path):
        try:
            log.info("Loading Filters from file at {}".format(file_path))
            with open(file_path, encoding="utf-8") as f:
                user_filters = json.load(f, object_pairs_hook=OrderedDict)
            if type(user_filters) is not OrderedDict:
                log.critical(
                    "User filters files must be a JSON object: { " +
                    "\"user_id\": {\"monsters\":{...},... } }"
                )
                raise ValueError("Filter file did not contain a dict.")
        except ValueError as e:
            log.error("Encountered error while loading Filters: {}: {}".format(
                type(e).__name__, e))
            log.error(
                "PokeBot has encountered a 'ValueError' while loading the "
                "Filters file. This typically means the file isn't in the "
                "correct json format. Try loading the file contents into a "
                "json validator."
            )
            sys.exit(1)
        except IOError as e:
            log.error("Encountered error while loading Filters: {}: {}".format(
                type(e).__name__, e))
            log.error((
                "PokeBot was unable to find a filters file at {}. Please " +
                "check that this file exists and that PA has read permissions."
            ).format(file_path))
            sys.exit(1)
        log.info("Parsing user filters file")
        for user_id in user_filters:
            try:
                section = user_filters[user_id].pop('monsters', {})
                self.__mons_enabled[user_id] = bool(section.pop(
                    'enabled', False))
                self.__mon_filters[user_id] = self.load_filter_section(
                    section, 'monsters', MonFilter)
                section = user_filters[user_id].pop('eggs', {})
                self.__eggs_enabled[user_id] = bool(section.pop(
                    'enabled', False))
                self.__egg_filters[user_id] = self.load_filter_section(
                    section, 'eggs', EggFilter)
                section = user_filters[user_id].pop('raids', {})
                self.__raids_enabled[user_id] = bool(section.pop(
                    'enabled', False))
                self.__raid_filters[user_id] = self.load_filter_section(
                    section, 'raids', RaidFilter)
            except Exception as e:
                log.error(
                    "Encountered error while parsing Filters. This is " +
                    "because of a mistake in your Filters file."
                )
                log.error("{}: {}".format(type(e).__name__, e))
                sys.exit(1)
        return

    def load_alarms_file(self, file_path):
        log.info("Loading Alarms from the file at {}".format(file_path))
        try:
            with open(file_path, 'r') as f:
                alarm_settings = json.load(f)
            if type(alarm_settings) is not dict:
                log.critical(
                    "Alarms file must be an object of Alarms objects - { " +
                    "'alarm1': {...}, ... 'alarm5': {...} }"
                )
                sys.exit(1)
            self.__alarms = {}
            self.__alarms['user_alarm'] = alarm_factory(
                alarm_settings, 1, self._google_key, 'user', self)
            log.info("{} active alarms found.".format(len(self.__alarms)))
            return
        except ValueError as e:
            log.error((
                "Encountered error while loading Alarms file: {}: {}"
            ).format(type(e).__name__, e))
            log.error(
                "PokeBot has encountered a 'ValueError' while loading the " +
                "Alarms file. This typically means your file isn't in the " +
                "correct json format. Try loading your file contents into a " +
                "json validator."
            )
        except IOError as e:
            log.error((
                "Encountered error while loading Alarms: {}: {}"
            ).format(type(e).__name__, e))
            log.error((
                "PokeBot was unable to find a filters file  at {}. Please " +
                "check that this file exists and PA has read permissions."
            ).format(file_path))
        except Exception as e:
            log.error((
                "Encountered error while loading Alarms: {}: {}"
            ).format(type(e).__name__, e))
        sys.exit(1)

    async def run(self):
        last_clean = datetime.utcnow()
        while True:
            if datetime.utcnow() - last_clean > timedelta(minutes=5):
                self.__cache.clean_and_save()
                last_clean = datetime.utcnow()
            try:
                event = await self.__queue.get()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0)
                continue
            try:
                kind = type(event)
                if kind == MonEvent:
                    await self.process_monster(event)
                elif kind == EggEvent:
                    await self.process_egg(event)
                elif kind == RaidEvent:
                    await self.process_raid(event)
                else:
                    pass
            except Exception as e:
                log.error((
                    "Encountered error during processing: {}: {}"
                ).format(type(e).__name__, e))
        self.__cache.clean_and_save()

    async def process_monster(self, mon):
        if self.__cache.monster_expiration(mon.enc_id) is not None:
            return
        self.__cache.monster_expiration(mon.enc_id, mon.disappear_time)
        for user_id in self.__mons_enabled:
            if int(user_id) % self.__number_of_bots != self.__bot_number:
                continue
            if self.__mons_enabled[user_id] is False:
                continue
            mon.name = self.__locale.get_pokemon_name(mon.monster_id)
            rules = {
                "default": Rule(
                    self.__mon_filters[user_id].keys(), self.__alarms.keys())
            }
            for r_name, rule in rules.items():
                for f_name in rule.filter_names:
                    f = self.__mon_filters[user_id].get(f_name)
                    passed = (
                        f.check_event(mon) and self.check_geofences(f, mon)
                    )
                    if not passed:
                        continue
                    mon.custom_dts = f.custom_dts
                    dest = discord.utils.get(
                        self.get_all_members(),
                        id=int(user_id)
                    )
                    log.info((
                        "{} monster notification has been triggered for " +
                        "user '{}'!"
                    ).format(mon.name, dest.display_name))
                    await self._trigger_mon(mon, rule.alarm_names, dest)
                    break

    async def _trigger_mon(self, mon, alarms, dest):
        dts = mon.generate_dts(self.__locale)
        if self._gmaps_reverse_geocode:
            dts.update(self._gmaps_service.reverse_geocode(
                (mon.lat, mon.lng), self._language))
        for name in alarms:
            alarm = self.__alarms.get(name)
            if alarm:
                await alarm.pokemon_alert(dts, dest)
            else:
                log.critical("Alarm '{}' not found!".format(name))

    async def process_egg(self, egg):
        if self.__cache.egg_expiration(egg.gym_id) is not None:
            return
        self.__cache.egg_expiration(egg.gym_id, egg.hatch_time)
        for user_id in self.__eggs_enabled:
            if int(user_id) % self.__number_of_bots != self.__bot_number:
                continue
            if self.__eggs_enabled[user_id] is False:
                continue
            rules = {
                "default": Rule(
                    self.__egg_filters[user_id].keys(), self.__alarms.keys())
            }
            for r_name, rule in rules.items():
                for f_name in rule.filter_names:
                    f = self.__egg_filters[user_id].get(f_name)
                    passed = (
                        f.check_event(egg) and self.check_geofences(f, egg)
                    )
                    if not passed:
                        continue
                    egg.custom_dts = f.custom_dts
                    dest = discord.utils.get(
                        self.get_all_members(),
                        id=int(user_id)
                    )
                    log.info((
                        "{} egg notification has been triggered for user '{}'!"
                    ).format(egg.name, dest.display_name))
                    await self._trigger_egg(egg, rule.alarm_names, dest)
                    break

    async def _trigger_egg(self, egg, alarms, dest):
        dts = egg.generate_dts(self.__locale)
        if self._gmaps_reverse_geocode:
            dts.update(self._gmaps_service.reverse_geocode(
                (egg.lat, egg.lng), self._language))
        for name in alarms:
            alarm = self.__alarms.get(name)
            if alarm:
                await alarm.raid_egg_alert(dts, dest)
            else:
                log.critical("Alarm '{}' not found!".format(name))

    async def process_raid(self, raid):
        if self.__cache.raid_expiration(raid.gym_id) is not None:
            return
        self.__cache.raid_expiration(raid.gym_id, raid.raid_end)
        for user_id in self.__raids_enabled:
            if int(user_id) % self.__number_of_bots != self.__bot_number:
                continue
            if self.__raids_enabled[user_id] is False:
                continue
            rules = {
                "default": Rule(
                    self.__raid_filters[user_id].keys(), self.__alarms.keys())
            }
            for r_name, rule in rules.items():
                for f_name in rule.filter_names:
                    f = self.__raid_filters[user_id].get(f_name)
                    passed = (
                        f.check_event(raid) and self.check_geofences(f, raid)
                    )
                    if not passed:
                        continue
                    raid.custom_dts = f.custom_dts
                    dest = discord.utils.get(
                        self.get_all_members(),
                        id=int(user_id)
                    )
                    log.info((
                        "{} raid notification has been triggered for user " +
                        "'{}'!"
                    ).format(raid.name, dest.display_name))
                    await self._trigger_raid(raid, rule.alarm_names, dest)
                    break

    async def _trigger_raid(self, raid, alarms, dest):
        dts = raid.generate_dts(self.__locale)
        if self._gmaps_reverse_geocode:
            dts.update(self._gmaps_service.reverse_geocode(
                (raid.lat, raid.lng), self._language))
        for name in alarms:
            alarm = self.__alarms.get(name)
            if alarm:
                await alarm.raid_alert(dts, dest)
            else:
                log.critical("Alarm '{}' not found!".format(name))

    def check_geofences(self, f, e):
        if self.geofences is None or f.geofences is None:
            return True
        targets = f.geofences
        if len(targets) == 1 and "all" in targets:
            targets = self.geofences.keys()
        for name in targets:
            gf = self.geofences.get(name)
            if not gf:
                log.error("Cannot check geofence %s: does not exist!", name)
            elif gf.contains(e.lat, e.lng):
                e.geofence = name
                return True
        return False

    async def on_ready(self):
        log.info("----------- Bot Manager '{}' is starting up.".format(
            self.__name))
        if self.__bot_number != 0:
            await self.change_presence(status=discord.Status.invisible)
        self.__roles = {}
        for guild in self.guilds:
            if guild.id not in self.__roles:
                self.__roles[guild.id] = {}
            for role in guild.roles:
                self.__roles[guild.id][role.name.lower()] = role
        users = []
        muted = []
        for member in self.get_all_members():
            if (member.id % self.__number_of_bots == self.__bot_number and
                member.top_role >= self.__roles[member.guild.id][
                    self.__alert_role]):
                if str(member.id) not in users:
                    users.append(str(member.id))
                if (str(member.id) not in muted and
                    self.__muted_role is not None and
                    self.__roles[member.guild.id][
                        self.__muted_role] in member.roles):
                    muted.append(str(member.id))
        reload = False
        with open(self.__filter_file, 'r+', encoding="utf-8") as f:
            user_filters = json.load(f, object_pairs_hook=OrderedDict)
            old_users = []
            old_geofences = []
            for user_id in user_filters:
                if int(user_id) % self.__number_of_bots == self.__bot_number:
                    filters = user_filters[user_id]
                    if user_id not in users and user_id not in old_users:
                        old_users.append(user_id)
                    else:
                        if (user_id in muted and
                            (filters['monsters']['enabled'] is True or
                             filters['eggs']['enabled'] is True or
                             filters['raids']['enabled'] is True)):
                            filters['monsters']['enabled'] = False
                            filters['eggs']['enabled'] = False
                            filters['raids']['enabled'] = False
                            reload = True
                            member = discord.utils.get(
                                self.get_all_members(),
                                id=int(user_id)
                            )
                            embeds = discord.Embed(
                                description=((
                                    "{} Your alerts have been paused due to " +
                                    "being muted, please contact an admin."
                                ).format(member.mention)),
                                color=int('0xee281f', 16)
                            )
                            await self.__alarms['user_alarm'].update(1, {
                                'destination': member,
                                'embeds': embeds
                            })
                            log.info('Paused muted user {}.'.format(
                                member.display_name))
                        defaults = filters['monsters']['defaults']
                        for geofence in defaults['geofences']:
                            if (geofence not in list(self.geofences.keys()) and
                                    geofence != "all"):
                                defaults['geofences'].remove(geofence)
                                reload = True
                                if geofence not in old_geofences:
                                    old_geofences.append(geofence)
                                    log.info((
                                        "Removed old geofence {} from user " +
                                        "filters."
                                    ).format(geofence))
                        defaults = filters['eggs']['defaults']
                        for geofence in defaults['geofences']:
                            if (geofence not in list(self.geofences.keys()) and
                                    geofence != "all"):
                                defaults['geofences'].remove(geofence)
                                reload = True
                                if geofence not in old_geofences:
                                    old_geofences.append(geofence)
                                    log.info((
                                        "Removed old geofence {} from user " +
                                        "filters."
                                    ).format(geofence))
                        defaults = filters['raids']['defaults']
                        for geofence in defaults['geofences']:
                            if (geofence not in list(self.geofences.keys()) and
                                    geofence != "all"):
                                defaults['geofences'].remove(geofence)
                                reload = True
                                if geofence not in old_geofences:
                                    old_geofences.append(geofence)
                                    log.info((
                                        "Removed old geofence {} from user " +
                                        "filters."
                                    ).format(geofence))
            if (len(list(user_filters.keys())) > 0 and
                    len(old_users) / len(list(user_filters.keys())) < 0.1):
                for user in old_users:
                    user_filters.pop(user)
                    reload = True
                    log.info("Removed old user {} from user filters.".format(
                        user))
            else:
                log.warning(
                    "More than 10% of users not found, Discord may be " +
                    "having issues, PokeBot will not purge old users at " +
                    "this time."
                )
            if reload:
                update_filters(user_filters, self.__filter_file, f)
        if reload:
            self.load_filter_file(get_path(self.__filter_file))
        log.info("----------- Bot Manager '{}' is connected.".format(
            self.__name))

    async def on_member_update(self, before, after):
        if (after.id % self.__number_of_bots == self.__bot_number and
                before.roles != after.roles):
            reload = False
            with open(self.__filter_file, 'r+', encoding="utf-8") as f:
                user_filters = json.load(f, object_pairs_hook=OrderedDict)
                if str(after.id) in list(user_filters.keys()):
                    filters = user_filters[str(after.id)]
                    if after.top_role < self.__roles[after.guild.id][
                            self.__alert_role]:
                        user_filters.pop(str(after.id))
                        reload = True
                        log.info('Removed user {} from user filters'.format(
                            after.display_name))
                    elif (self.__muted_role is not None and
                          self.__roles[after.guild.id][
                              self.__muted_role] in after.roles and
                          (filters['monsters']['enabled'] is True or
                           filters['eggs']['enabled'] is True or
                           filters['raids']['enabled'] is True)):
                        filters['monsters']['enabled'] = False
                        filters['eggs']['enabled'] = False
                        filters['raids']['enabled'] = False
                        reload = True
                        embeds = discord.Embed(
                            description=((
                                "{} Your alerts have been paused due to " +
                                "being muted, please contact an admin."
                            ).format(after.mention)),
                            color=int('0xee281f', 16)
                        )
                        await self.__alarms['user_alarm'].update(1, {
                            'destination': after,
                            'embeds': embeds
                        })
                        log.info('Paused {} on mute.'.format(
                            after.display_name))
                if reload:
                    update_filters(user_filters, self.__filter_file, f)
            if reload:
                self.load_filter_file(get_path(self.__filter_file))

    async def on_member_remove(self, member):
        if (member.id % self.__number_of_bots == self.__bot_number and
                member not in self.get_all_members()):
            reload = False
            with open(self.__filter_file, 'r+', encoding="utf-8") as f:
                user_filters = json.load(f, object_pairs_hook=OrderedDict)
                if str(member.id) in list(user_filters.keys()):
                    user_filters.pop(str(member.id))
                    reload = True
                    log.info('Removed user {} from user filters'.format(
                        member.display_name))
                if reload:
                    update_filters(user_filters, self.__filter_file, f)
            if reload:
                self.load_filter_file(get_path(self.__filter_file))

    async def on_message(self, message):
        message.content = message.content.replace('’', "'")
        if (message.channel.id in self.__command_channels and
            message.author.top_role >= self.__roles[message.author.guild.id][
                self.__alert_role]):
            if message.content.lower() == '!status':
                await status(
                    self, message, self.__bot_number, self.__number_of_bots)
            elif (
                message.author.id % self.__number_of_bots == self.__bot_number
            ):
                if message.content.lower() in ['!commands', '!help']:
                    await commands(self, message)
                elif message.content.lower().startswith('!dex '):
                    await dex(self, message)
                elif message.content.lower().startswith('!set raid'):
                    await set_raids(
                        self, message, self.geofences, self.__all_areas,
                        self.__ex_parks, self.__filter_file
                    )
                elif message.content.lower().startswith('!set egg'):
                    await set_eggs(
                        self, message, self.geofences, self.__all_areas,
                        self.__ex_parks, self.__filter_file
                    )
                elif message.content.lower().startswith(
                        ('!delete raid', '!remove raid')):
                    await delete_raids(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file, self.__locale
                    )
                elif message.content.lower().startswith(
                        ('!delete egg', '!remove egg')):
                    await delete_eggs(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file, self.__locale
                    )
                elif message.content.lower().startswith('!set '):
                    await set_(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file, self.__locale
                    )
                elif message.content.lower().startswith(
                        ('!delete ', '!remove ')):
                    await delete(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file, self.__locale
                    )
                elif message.content.lower().startswith('!reset '):
                    await reset(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file, self.__locale
                    )
                elif (message.content.lower().startswith('!pause') or
                      message.content.lower().startswith('!p')):
                    await pause(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file
                    )
                elif (message.content.lower().startswith('!resume') or
                      message.content.lower().startswith('!r')):
                    await resume(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file
                    )
                elif message.content.lower().startswith('!activate '):
                    await activate(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file
                    )
                elif message.content.lower().startswith('!deactivate '):
                    await deactivate(
                        self, message, self.geofences, self.__all_areas,
                        self.__filter_file
                    )
                elif message.content.lower().startswith('!alerts egg'):
                    await alerts_eggs(
                        self, message, self.__bot_number, self.geofences,
                        self.__all_areas, self.__filter_file, self.__locale
                    )
                elif message.content.lower().startswith('!alerts raid'):
                    await alerts_raids(
                        self, message, self.__bot_number, self.geofences,
                        self.__all_areas, self.__filter_file, self.__locale
                    )
                elif message.content.lower() in ['!alerts', '!alerts pokemon']:
                    await alerts(
                        self, message, self.__bot_number, self.geofences,
                        self.__all_areas, self.__filter_file, self.__locale
                    )
                elif message.content.lower() == '!areas':
                    await areas(
                        self, message, self.geofences, self.__filter_file)
                elif message.content.lower().startswith('!'):
                    embeds = discord.Embed(
                        description=((
                            "{} Unrecognized command, type `!help` for " +
                            "assistance."
                        ).format(message.author.mention)),
                        color=int('0xee281f', 16)
                    )
                    await self.__alarms['user_alarm'].update(1, {
                        'destination': message.channel,
                        'embeds': embeds
                    })
                    log.info('{} sent unrecognized command.'.format(
                        message.author.display_name))
        elif message.channel.id in self.__command_channels:
            embeds = discord.Embed(
                description=((
                    "{} You don't have the proper role to create an alert."
                ).format(message.author.mention)),
                color=int('0xee281f', 16)
            )
            await self.__alarms['user_alarm'].update(1, {
                'destination': message.channel,
                'embeds': embeds
            })
            log.info('{} sent unauthorized command.'.format(
                message.author.display_name))
